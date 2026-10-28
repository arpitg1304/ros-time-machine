# Copyright 2024 ROS Time Machine Contributors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""The watcher's decision loop, free of ROS so it can be tested and replayed offline."""

from __future__ import annotations

from dataclasses import dataclass, field
import random
import threading
import time
from typing import Callable, Dict, List, Optional, Protocol, Union

from .decision import Detection, parse_response
from .frames import Frame, FrameHistory, FrameSampler, FrameWindow, NS_PER_S, StateBoard
from .prompt import build_messages, JUDGE_NOTE, prompt_version
from .vlm_client import VlmError
from .watchlist import WatcherConfig

JpegSource = Union[bytes, Callable[[], Optional[bytes]]]


class ChatClient(Protocol):
    def complete(self, messages: List[dict]) -> str:
        ...


@dataclass
class TriggerRequest:
    """What the watcher asks Time Machine to save (maps 1:1 onto EventTrigger.msg)."""

    label: str
    profile: str
    event_stamp_ns: int
    start_ns: int          # 0 = profile default
    end_ns: int            # 0 = profile default
    confidence: float
    description: str
    source: str
    source_version: str
    tags: List[str] = field(default_factory=list)


@dataclass
class StepResult:
    triggers: List[TriggerRequest] = field(default_factory=list)
    detections: List[Detection] = field(default_factory=list)
    problems: List[str] = field(default_factory=list)
    raw_text: str = ''
    error: str = ''
    latency_s: float = 0.0
    frames: int = 0


@dataclass
class JudgeResult:
    """The VLM's verdict on a pinned window."""

    keep: bool = False
    detection: Optional[Detection] = None
    event_stamp_ns: int = 0
    frames: int = 0
    error: str = ''
    latency_s: float = 0.0


@dataclass
class WatcherStats:
    frames_offered: int = 0
    frames_sampled: int = 0
    calls: int = 0
    errors: int = 0
    invalid_json: int = 0
    dropped_entries: int = 0
    detections: int = 0
    triggers: int = 0
    below_confidence: int = 0
    in_cooldown: int = 0
    sampled_out: int = 0
    last_latency_s: float = 0.0
    total_latency_s: float = 0.0
    judged: int = 0
    judge_kept: int = 0

    def as_dict(self) -> Dict[str, float]:
        data = dict(self.__dict__)
        data['mean_latency_s'] = self.total_latency_s / self.calls if self.calls else 0.0
        return data


class WatcherEngine:
    """Samples frames, asks the VLM, and turns credible answers into time-addressed triggers.

    Thread-safe: frames and state can be offered from subscription callbacks while
    step() runs on a worker thread.
    """

    def __init__(
        self,
        config: WatcherConfig,
        client: ChatClient,
        rng: Callable[[], float] = random.random,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self.config = config
        self.client = client
        self.stats = WatcherStats()
        self.source = f'vlm:{config.model}'
        self.source_version = f'{prompt_version(config)};model:{config.model}'
        self._rng = rng
        self._monotonic = monotonic
        self._sampler = FrameSampler(config.sample_hz)
        self._window = FrameWindow(config.context_frames)
        self._history = FrameHistory(config.history_s)
        self._state = StateBoard()
        self._last_fired_ns: Dict[str, int] = {}
        self._lock = threading.Lock()

    def offer_frame(self, camera: str, stamp_ns: int, jpeg: JpegSource) -> bool:
        """Offer a camera frame; returns True if it was sampled.

        ``jpeg`` may be a callable so frames that are not sampled are never encoded.
        """
        with self._lock:
            self.stats.frames_offered += 1
            if not self._sampler.accept(camera, stamp_ns):
                return False
        data = jpeg() if callable(jpeg) else jpeg
        if not data:
            return False
        frame = Frame(camera, stamp_ns, data)
        self._window.add(frame)
        self._history.add(frame)
        with self._lock:
            self.stats.frames_sampled += 1
        return True

    def update_state(self, topic: str, stamp_ns: int, text: str) -> None:
        self._state.update(topic, stamp_ns, text)

    def ready(self) -> bool:
        """True when there are frames the VLM has not looked at yet."""
        return self._window.has_unseen()

    def step(self) -> StepResult:
        """Ask the VLM about the current window. Never raises for model or network faults."""
        result = StepResult()
        frames = self._window.snapshot()
        result.frames = len(frames)
        if not frames:
            return result

        messages = build_messages(
            self.config, frames, self._state.summary(now_ns=frames[-1].stamp_ns))
        result.raw_text, result.error, result.latency_s = self._call(messages)
        if result.error:
            return result

        parsed = parse_response(result.raw_text, self.config.labels, len(frames))
        result.detections = parsed.detections
        result.problems = parsed.problems
        with self._lock:
            if not parsed.valid_json:
                self.stats.invalid_json += 1
            self.stats.dropped_entries += len(parsed.problems) if parsed.valid_json else 0
            self.stats.detections += len(parsed.detections)
            for detection in parsed.detections:
                trigger = self._decide(detection, frames)
                if trigger:
                    result.triggers.append(trigger)
            self.stats.triggers += len(result.triggers)
        return result

    def judge(self, start_ns: int, end_ns: int) -> JudgeResult:
        """Ask the VLM whether a pinned window contains a listed event.

        Only confidence thresholds apply: a scorer already decided this window is
        unusual, so cooldown and sampling would only hide real events. A kept
        verdict still starts the label's cooldown so periodic sampling does not
        report the same event again.
        """
        result = JudgeResult()
        frames = self._history.between(start_ns, end_ns, self.config.judge_frames)
        result.frames = len(frames)
        if not frames:
            result.error = 'no frames in pinned window'
            return result

        messages = build_messages(
            self.config, frames, self._state.summary(now_ns=frames[-1].stamp_ns), JUDGE_NOTE)
        text, result.error, result.latency_s = self._call(messages)
        if result.error:
            return result

        parsed = parse_response(text, self.config.labels, len(frames))
        with self._lock:
            self.stats.judged += 1
            if not parsed.valid_json:
                self.stats.invalid_json += 1
            credible = [d for d in parsed.detections if d.confidence >= self._threshold(d.label)]
            if credible:
                best = max(credible, key=lambda d: d.confidence)
                result.keep = True
                result.detection = best
                result.event_stamp_ns = frames[best.frame_index].stamp_ns
                self._last_fired_ns[best.label] = result.event_stamp_ns
                self.stats.judge_kept += 1
        return result

    def _call(self, messages):
        """Run one completion; returns (text, error, latency) and updates call stats."""
        text, error = '', ''
        started = self._monotonic()
        try:
            text = self.client.complete(messages)
        except VlmError as e:
            error = str(e)
        except Exception as e:  # noqa: B902 - a bad client must not kill the watcher
            error = f'{type(e).__name__}: {e}'
        latency = self._monotonic() - started
        with self._lock:
            self.stats.calls += 1
            self.stats.last_latency_s = latency
            self.stats.total_latency_s += latency
            if error:
                self.stats.errors += 1
        return text, error, latency

    def _threshold(self, label: str) -> float:
        event = self.config.event(label)
        return event.min_confidence if event.min_confidence is not None \
            else self.config.min_confidence

    def _decide(self, detection: Detection, frames: List[Frame]) -> Optional[TriggerRequest]:
        """Apply confidence, cooldown and sampling. Caller holds the lock."""
        event = self.config.event(detection.label)
        if detection.confidence < self._threshold(detection.label):
            self.stats.below_confidence += 1
            return None

        stamp_ns = frames[detection.frame_index].stamp_ns
        cooldown = event.cooldown_s if event.cooldown_s is not None else self.config.cooldown_s
        last = self._last_fired_ns.get(detection.label)
        # Consecutive windows overlap, so the same event is usually reported several times
        if last is not None and abs(stamp_ns - last) < cooldown * NS_PER_S:
            self.stats.in_cooldown += 1
            return None
        self._last_fired_ns[detection.label] = stamp_ns

        # Sampled-out events still start a cooldown so the next window cannot re-roll them
        if event.sample_rate < 1.0 and self._rng() >= event.sample_rate:
            self.stats.sampled_out += 1
            return None

        start_ns = stamp_ns - int(event.pre_event * NS_PER_S) if event.pre_event is not None else 0
        end_ns = stamp_ns + int(event.post_event * NS_PER_S) \
            if event.post_event is not None else 0
        return TriggerRequest(
            label=detection.label,
            profile=event.profile,
            event_stamp_ns=stamp_ns,
            start_ns=start_ns,
            end_ns=end_ns,
            confidence=detection.confidence,
            description=detection.description,
            source=self.source,
            source_version=self.source_version,
            tags=list(event.tags),
        )
