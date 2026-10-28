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

"""Validate VLM output. Small models wrap JSON in prose or fences; never trust, never crash."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
from typing import Any, Iterable, List, Optional


@dataclass
class Detection:
    """One event the VLM claims to have seen."""

    label: str
    frame_index: int
    confidence: float
    description: str = ''


@dataclass
class ParseResult:
    detections: List[Detection] = field(default_factory=list)
    problems: List[str] = field(default_factory=list)
    valid_json: bool = True


def _find_json_object(text: str) -> Optional[Any]:
    """Return the first JSON object or array in text, tolerating fences and chatter."""
    text = text.strip()
    try:
        return json.loads(text)
    except ValueError:
        pass
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch in '{[':
            try:
                value, _ = decoder.raw_decode(text, i)
                return value
            except ValueError:
                continue
    return None


def parse_response(text: str, labels: Iterable[str], num_frames: int) -> ParseResult:
    """Turn raw model text into validated detections.

    Unknown labels, bad indexes and malformed entries are dropped and reported in
    ``problems``; a missing frame_index defaults to the newest frame.
    """
    result = ParseResult()
    allowed = set(labels)
    data = _find_json_object(text or '')
    if data is None:
        result.valid_json = False
        result.problems.append('no JSON object in response')
        return result

    if isinstance(data, dict) and 'events' in data:
        events = data['events']
    elif isinstance(data, list):
        events = data
    elif isinstance(data, dict) and 'label' in data:
        events = [data]  # A single bare event
    else:
        result.valid_json = False
        result.problems.append("JSON has no 'events' list")
        return result

    if not isinstance(events, list):
        result.valid_json = False
        result.problems.append("'events' is not a list")
        return result

    for entry in events:
        if not isinstance(entry, dict):
            result.problems.append(f'event is not an object: {entry!r}')
            continue
        label = entry.get('label')
        if not isinstance(label, str) or label.strip() not in allowed:
            result.problems.append(f'unknown label: {label!r}')
            continue
        label = label.strip()

        index = entry.get('frame_index', num_frames - 1)
        try:
            index = int(index)
        except (TypeError, ValueError):
            result.problems.append(f'{label}: bad frame_index {index!r}')
            continue
        if index < 0:
            index += num_frames  # Allow -1 = newest
        if not 0 <= index < num_frames:
            result.problems.append(f'{label}: frame_index {index} out of range')
            continue

        confidence = entry.get('confidence', 0.0)
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            result.problems.append(f'{label}: bad confidence {confidence!r}')
            continue
        if math.isnan(confidence):
            result.problems.append(f'{label}: confidence is NaN')
            continue
        if confidence > 1.0 and confidence <= 100.0:
            confidence /= 100.0  # Percent
        confidence = min(max(confidence, 0.0), 1.0)

        description = entry.get('description', '')
        if not isinstance(description, str):
            description = str(description)
        result.detections.append(Detection(label, index, confidence, description.strip()[:500]))
    return result
