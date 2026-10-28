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

"""The demo camera's story: a gripper holds a red cup, then drops it, on a loop.

Gives a real VLM something to find, and records when each drop happened so the
resulting clips can be scored with `review eval`.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Dict, Optional

from .demo_image import DEMO_JPEG

FRAMES_DIR = os.path.join(os.path.dirname(__file__), 'demo_frames')
DROP_AT_S = 12.0      # Seconds into each loop when the cup starts falling
FALL_S = 0.5          # How long the falling frame is shown
GRIPPER_CLOSED = 0.0
GRIPPER_OPEN = 0.04


def load_frames() -> Dict[str, bytes]:
    frames = {}
    for name in ('hold', 'fall', 'floor'):
        with open(os.path.join(FRAMES_DIR, f'{name}.jpg'), 'rb') as f:
            frames[name] = f.read()
    return frames


@dataclass
class SceneState:
    frame: bytes
    gripper: float
    loop: int                  # Which repetition we are in
    drop_started: bool         # True on the first update after a drop begins


class DropLoop:
    """Hold for DROP_AT_S seconds, drop, lie on the floor, repeat every period_s."""

    def __init__(self, period_s: float = 20.0, frames: Optional[Dict[str, bytes]] = None):
        if period_s <= DROP_AT_S + FALL_S:
            raise ValueError(f'period_s must be longer than {DROP_AT_S + FALL_S} s')
        self.period_s = period_s
        self.frames = frames or load_frames()
        self._dropped_loop = -1

    def at(self, t_s: float) -> SceneState:
        loop = int(t_s // self.period_s)
        phase = t_s - loop * self.period_s
        if phase < DROP_AT_S:
            return SceneState(self.frames['hold'], GRIPPER_CLOSED, loop, False)
        started = loop != self._dropped_loop
        self._dropped_loop = loop
        name = 'fall' if phase < DROP_AT_S + FALL_S else 'floor'
        return SceneState(self.frames[name], GRIPPER_OPEN, loop, started)

    def drop_time(self, loop: int) -> float:
        return loop * self.period_s + DROP_AT_S


class StaticScene:
    """The original demo: one still frame, nothing ever happens."""

    def at(self, t_s: float) -> SceneState:
        return SceneState(DEMO_JPEG, GRIPPER_CLOSED, 0, False)
