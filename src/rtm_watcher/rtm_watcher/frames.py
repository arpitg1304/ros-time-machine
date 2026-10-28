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

"""Low-rate copies of the robot's sensors: sampled keyframes and a state summary."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import threading
from typing import Deque, Dict, List, Optional

NS_PER_S = 1_000_000_000


@dataclass(frozen=True)
class Frame:
    """A sampled camera frame, stamped in robot ROS time."""

    camera: str
    stamp_ns: int
    jpeg: bytes


class FrameSampler:
    """Per-camera rate gate driven by robot time, so bag replay and sim time behave."""

    def __init__(self, sample_hz: float):
        self._period_ns = int(NS_PER_S / sample_hz)
        self._last: Dict[str, int] = {}

    def accept(self, camera: str, stamp_ns: int) -> bool:
        last = self._last.get(camera)
        # Accept the first frame, a frame one period later, or any frame after time
        # jumped backwards (replay looped)
        if last is None or stamp_ns - last >= self._period_ns or stamp_ns < last:
            self._last[camera] = stamp_ns
            return True
        return False


class FrameWindow:
    """The last N sampled frames per camera: the context the VLM looks at."""

    def __init__(self, context_frames: int):
        self._context = context_frames
        self._frames: Dict[str, Deque[Frame]] = {}
        self._lock = threading.Lock()
        self._unseen = False

    def add(self, frame: Frame) -> None:
        with self._lock:
            frames = self._frames.setdefault(frame.camera, deque(maxlen=self._context))
            if frames and frame.stamp_ns < frames[-1].stamp_ns:
                # Time went backwards (replay looped): all context is from another timeline
                for camera_frames in self._frames.values():
                    camera_frames.clear()
            frames.append(frame)
            self._unseen = True

    def snapshot(self) -> List[Frame]:
        """Frames ordered by time (camera name breaks ties) and marked as seen."""
        with self._lock:
            frames = [f for cam in self._frames.values() for f in cam]
            self._unseen = False
        return sorted(frames, key=lambda f: (f.stamp_ns, f.camera))

    def has_unseen(self) -> bool:
        with self._lock:
            return self._unseen

    def __len__(self) -> int:
        with self._lock:
            return sum(len(cam) for cam in self._frames.values())


class FrameHistory:
    """Every sampled frame from the last few seconds, for judging pinned windows."""

    def __init__(self, seconds: float):
        self._span_ns = int(seconds * NS_PER_S)
        self._frames: Deque[Frame] = deque()
        self._lock = threading.Lock()

    def add(self, frame: Frame) -> None:
        with self._lock:
            if self._frames and frame.stamp_ns < self._frames[-1].stamp_ns - NS_PER_S:
                self._frames.clear()  # Clock reset
            self._frames.append(frame)
            cutoff = max(f.stamp_ns for f in self._frames) - self._span_ns
            while self._frames and self._frames[0].stamp_ns < cutoff:
                self._frames.popleft()

    def between(self, start_ns: int, end_ns: int, max_frames: int) -> List[Frame]:
        """Frames in [start, end], evenly thinned to at most max_frames, oldest first."""
        with self._lock:
            frames = sorted((f for f in self._frames if start_ns <= f.stamp_ns <= end_ns),
                            key=lambda f: (f.stamp_ns, f.camera))
        if len(frames) <= max_frames:
            return frames
        if max_frames == 1:
            return [frames[-1]]
        step = (len(frames) - 1) / (max_frames - 1)
        return [frames[round(i * step)] for i in range(max_frames)]

    def __len__(self) -> int:
        with self._lock:
            return len(self._frames)


class StateBoard:
    """Latest compact text for each state topic (joints, gripper, ...)."""

    def __init__(self, max_chars_per_topic: int = 400):
        self._max_chars = max_chars_per_topic
        self._latest: Dict[str, tuple] = {}
        self._lock = threading.Lock()

    def update(self, topic: str, stamp_ns: int, text: str) -> None:
        text = ' '.join(text.split())
        if len(text) > self._max_chars:
            text = text[:self._max_chars - 3] + '...'
        with self._lock:
            self._latest[topic] = (stamp_ns, text)

    def summary(self, now_ns: Optional[int] = None, max_age_s: float = 5.0) -> str:
        with self._lock:
            items = sorted(self._latest.items())
        lines = []
        for topic, (stamp_ns, text) in items:
            if now_ns is not None and now_ns - stamp_ns > max_age_s * NS_PER_S:
                lines.append(f'{topic}: (stale) {text}')
            else:
                lines.append(f'{topic}: {text}')
        return '\n'.join(lines)


def summarize_joint_state(names, positions, velocities=(), efforts=(), digits: int = 2) -> str:
    """Render a JointState as 'name=pos' pairs (with effort when present)."""
    parts = []
    for i, name in enumerate(names):
        pos = f'{positions[i]:.{digits}f}' if i < len(positions) else '?'
        part = f'{name}={pos}'
        if i < len(efforts):
            part += f' (effort {efforts[i]:.{digits}f})'
        parts.append(part)
    return ', '.join(parts)
