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

"""Prompt construction for an OpenAI-compatible vision-language model."""

from __future__ import annotations

import base64
import hashlib
from typing import Any, Dict, List

from .frames import Frame, NS_PER_S
from .watchlist import WatcherConfig

# Bump when the wording changes: it is part of every clip's source_version
PROMPT_TEMPLATE_VERSION = 1

SYSTEM_PROMPT = """You watch a robot's cameras and report events from a fixed list.

Rules:
- Report an event only if you can see it happen in the frames. When unsure, report nothing.
- Use only labels from the list. Never invent labels.
- frame_index is the number of the first frame where the event is visible.
- confidence is your probability (0 to 1) that the event really happened.
- description is one short sentence about what you saw.
- Respond with JSON only, no prose, in exactly this shape:
{"events": [{"label": "<label>", "frame_index": <int>, "confidence": <float>, "description": "<sentence>"}]}
- If none of the listed events happened, respond with {"events": []}."""


def _events_text(config: WatcherConfig) -> str:
    return '\n'.join(f'- {event.label}: {event.describe}' for event in config.events)


def prompt_version(config: WatcherConfig) -> str:
    """Short hash of everything that shapes the VLM's answer, e.g. 'prompt:3f9a1c'."""
    digest = hashlib.sha1()
    digest.update(str(PROMPT_TEMPLATE_VERSION).encode())
    digest.update(SYSTEM_PROMPT.encode())
    digest.update(_events_text(config).encode())
    digest.update(config.robot_context.encode())
    digest.update(str(config.context_frames).encode())
    return 'prompt:' + digest.hexdigest()[:6]


JUDGE_NOTE = ('A sensor on the robot flagged something unusual in this window. Look '
              'carefully and report any listed event you can see.')


def build_messages(
    config: WatcherConfig, frames: List[Frame], state_text: str = '', note: str = ''
) -> List[Dict[str, Any]]:
    """Chat messages: system rules, then the event list, robot state and numbered frames."""
    if not frames:
        raise ValueError('build_messages needs at least one frame')
    newest_ns = frames[-1].stamp_ns

    intro = []
    if config.robot_context:
        intro.append(f'Context: {config.robot_context}')
    if note:
        intro.append(note)
    intro.append('Events to watch for:\n' + _events_text(config))
    if state_text:
        intro.append('Robot state (latest):\n' + state_text)
    intro.append(
        f'{len(frames)} frames follow, oldest first. Times are seconds relative to the '
        'newest frame.')

    content: List[Dict[str, Any]] = [{'type': 'text', 'text': '\n\n'.join(intro)}]
    for i, frame in enumerate(frames):
        dt = (frame.stamp_ns - newest_ns) / NS_PER_S
        content.append({'type': 'text', 'text': f'Frame {i} ({frame.camera}, t={dt:+.1f}s):'})
        image = {'url': 'data:image/jpeg;base64,' + base64.b64encode(frame.jpeg).decode('ascii')}
        if config.image_detail:
            image['detail'] = config.image_detail
        content.append({'type': 'image_url', 'image_url': image})

    return [
        {'role': 'system', 'content': SYSTEM_PROMPT},
        {'role': 'user', 'content': content},
    ]
