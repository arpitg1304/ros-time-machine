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

"""Measure a VLM endpoint with exactly the requests the watcher will send."""

from __future__ import annotations

from dataclasses import dataclass, field
import glob
import io
import math
import os
import random
import time
from typing import Callable, List, Optional

from rtm_watcher.decision import parse_response
from rtm_watcher.demo_image import DEMO_JPEG
from rtm_watcher.frames import Frame, NS_PER_S
from rtm_watcher.prompt import build_messages
from rtm_watcher.vlm_client import VlmError
from rtm_watcher.watchlist import parse_watch_list, WatcherConfig

try:  # pragma: no cover - optional
    from PIL import Image, ImageDraw
except ImportError:  # pragma: no cover
    Image = None

HAVE_PIL = Image is not None

DEFAULT_EVENTS = [
    {'label': 'object_dropped',
     'describe': 'An object falls out of the gripper or off the table.'},
    {'label': 'person_in_cell', 'describe': "A person enters the robot's work area."},
]


@dataclass
class BenchResult:
    endpoint: str
    model: str
    frames: int
    image_px: int
    synthetic: bool                         # Static scene: every detection is a false alarm
    cold_s: Optional[float] = None          # First request (model warm-up, lazy compiles)
    latencies: List[float] = field(default_factory=list)   # Warm requests that succeeded
    errors: List[str] = field(default_factory=list)
    invalid_json: int = 0
    detections: int = 0
    requests: int = 0
    image_kb: float = 0.0

    def _q(self, q: float) -> Optional[float]:
        values = sorted(self.latencies)
        if not values:
            return None
        return values[min(len(values) - 1, math.ceil(q * len(values)) - 1)]

    @property
    def p50(self) -> Optional[float]:
        return self._q(0.5)

    @property
    def p95(self) -> Optional[float]:
        return self._q(0.95)

    @property
    def max(self) -> Optional[float]:
        return max(self.latencies) if self.latencies else None

    @property
    def answered(self) -> int:
        return self.requests - len(self.errors)

    @property
    def valid_rate(self) -> Optional[float]:
        return (self.answered - self.invalid_json) / self.answered if self.answered else None


def bench_config(model: str, endpoint: str, frames: int, image_px: int,
                 watch_list: Optional[WatcherConfig] = None, **overrides) -> WatcherConfig:
    """The watch list to benchmark with: the user's (if given) with endpoint and model swapped."""
    if watch_list is not None:
        config = watch_list
        config.endpoint, config.model = endpoint, model
        config.context_frames, config.max_image_px = frames, image_px
        for key, value in overrides.items():
            setattr(config, key, value)
        return config
    raw = {'endpoint': endpoint, 'model': model, 'cameras': ['/camera/front'],
           'context_frames': frames, 'max_image_px': image_px, 'events': DEFAULT_EVENTS,
           'allow_remote': True}
    raw.update(overrides)
    return parse_watch_list(raw)


def synthetic_frames(count: int, px: int, seed: int = 0) -> List[bytes]:
    """A static tabletop with a red cup and sensor noise: nothing happens in it.

    Noise keeps JPEG sizes close to real camera frames, which matters for upload
    time and image tokens. Falls back to the tiny bundled frame without Pillow.
    """
    if Image is None:
        return [DEMO_JPEG] * count
    rng = random.Random(seed)
    w, h = px, int(px * 3 / 4)
    frames = []
    for i in range(count):
        img = Image.new('RGB', (w, h), (60, 64, 72))
        draw = ImageDraw.Draw(img)
        draw.rectangle([0, int(h * 0.6), w, h], fill=(140, 120, 95))        # Table
        cx, cy = int(w * 0.55) + i, int(h * 0.48)                           # Cup, barely moving
        draw.rectangle([cx - w // 24, cy - h // 10, cx + w // 24, cy + h // 8], fill=(190, 35, 35))
        draw.rectangle([int(w * 0.15), int(h * 0.2), int(w * 0.3), int(h * 0.6)],
                       fill=(90, 90, 100))                                   # Robot arm
        noise = bytes(rng.randrange(256) for _ in range((w // 4) * (h // 4)))
        grain = Image.frombytes('L', (w // 4, h // 4), noise).resize((w, h))
        img = Image.blend(img, Image.merge('RGB', (grain, grain, grain)), 0.12)
        out = io.BytesIO()
        img.save(out, 'JPEG', quality=85)
        frames.append(out.getvalue())
    return frames


def frames_from_dir(path: str, count: int) -> List[bytes]:
    files = sorted(f for ext in ('jpg', 'jpeg', 'JPG', 'JPEG')
                   for f in glob.glob(os.path.join(path, f'*.{ext}')))
    if not files:
        raise ValueError(f'no .jpg images in {path}')
    data = []
    for f in files[:count]:
        with open(f, 'rb') as fh:
            data.append(fh.read())
    while len(data) < count:
        data.append(data[len(data) % len(files)])
    return data


def run_bench(config: WatcherConfig, client, images: List[bytes], requests: int = 10,
              synthetic: bool = True, progress: Optional[Callable[[int, str], None]] = None,
              monotonic: Callable[[], float] = time.monotonic) -> BenchResult:
    """One cold request, then `requests` warm ones, sliding the window like the watcher.

    Request i shows frames i .. i + context_frames - 1, so every request has exactly
    one frame the server has not seen. Re-sending identical frames would let prompt
    and image caches make the endpoint look several times faster than it is.
    `images` should hold context_frames + requests frames (it is cycled if shorter).
    """
    n = config.context_frames
    stream = [Frame('/camera/front', (i + 1) * NS_PER_S, images[i % len(images)])
              for i in range(n + requests)]
    result = BenchResult(config.endpoint, config.model, n, config.max_image_px,
                         synthetic, image_kb=sum(len(d) for d in images) / len(images) / 1024)
    for i in range(requests + 1):
        cold = i == 0
        frames = stream[i:i + n]
        messages = build_messages(config, frames)
        started = monotonic()
        try:
            text = client.complete(messages)
        except VlmError as e:
            if progress:
                progress(i, f'error: {str(e)[:120]}')
            if cold and 'cannot reach' in str(e):
                result.errors.append(str(e))
                return result  # Endpoint down: no point continuing
            if not cold:
                result.requests += 1
                result.errors.append(str(e))
            continue
        elapsed = monotonic() - started
        parsed = parse_response(text, config.labels, len(frames))
        if cold:
            result.cold_s = elapsed
        else:
            result.requests += 1
            result.latencies.append(elapsed)
            result.invalid_json += not parsed.valid_json
            result.detections += sum(
                d.confidence >= config.min_confidence for d in parsed.detections)
        if progress:
            status = 'invalid JSON' if not parsed.valid_json else \
                f'{len(parsed.detections)} event(s)'
            progress(i, f'{elapsed:.2f}s {status}')
    return result


def bench_images(config: WatcherConfig, requests: int, images_dir: Optional[str] = None
                 ) -> List[bytes]:
    """Enough distinct frames for a sliding window over every request."""
    count = config.context_frames + requests
    if images_dir:
        return frames_from_dir(images_dir, count)
    return synthetic_frames(count, config.max_image_px)


def suggest(result: BenchResult, sample_hz: float = 1.0,
            extra_body: Optional[dict] = None) -> dict:
    """Watch list and Time Machine settings that match what was measured, plus hints."""
    if result.p95 is None:
        return {}
    period = 1.0 / sample_hz
    # Worst case: the event lands just after a sample, a call is already in flight,
    # and the call that sees it takes p95
    lag = period + 2 * result.p95 + 1.0
    keeps_up = result.p50 <= period
    hints = []
    if not keeps_up:
        if 'reasoning_effort' not in (extra_body or {}):
            hints.append('If this is a thinking model, turn reasoning off: '
                         '--extra-body \'{"reasoning_effort": "none"}\' (Ollama, OpenAI-style '
                         'APIs). Hidden reasoning was 2x the latency in our tests.')
        per_frame = result.p50 / max(result.frames, 1)
        fit = int(period * 0.8 / per_frame)
        hints.append(f'Every frame is re-encoded on every call (~{per_frame:.2f} s each here); '
                     + (f'--frames {fit} fits {sample_hz:g} Hz.' if fit >= 1 else
                        'smaller --image-px or a smaller model is needed for this rate.'))
        hints.append('Too slow to watch continuously can still be fast enough to judge pinned '
                     'windows (the cascade), which are rare.')
    if result.valid_rate is not None and result.valid_rate < 0.95:
        hints.append('Invalid JSON: thinking models can run out of max_tokens before answering '
                     '(turn reasoning off or raise max_tokens); with llama.cpp also try '
                     '--extra-body \'{"cache_prompt": false}\'.')
    if result.synthetic and result.detections:
        hints.append('The model reported events in a scene where nothing happens: raise '
                     'min_confidence or use a larger model.')
    return {
        'sample_hz': sample_hz if keeps_up else round(min(sample_hz, 1.0 / result.p50), 2),
        'request_timeout_s': max(10, math.ceil(3 * result.max)),
        'max_trigger_latency': math.ceil(lag),
        'keeps_up': keeps_up,
        'hints': hints,
    }
