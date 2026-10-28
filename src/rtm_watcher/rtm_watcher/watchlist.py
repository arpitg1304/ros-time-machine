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

"""Watch list: the plain-English events the VLM watcher looks for."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
import re
from typing import Any, Dict, List, Optional

import yaml

_LABEL_RE = re.compile(r'^[a-z][a-z0-9_]*$')


class WatchListError(ValueError):
    """Raised when a watch list is invalid."""


@dataclass
class WatchEvent:
    """One event type the watcher should report."""

    label: str
    describe: str
    profile: str = ''               # Time Machine snapshot profile; '' = default
    sample_rate: float = 1.0        # Keep this fraction of detections (e.g. 0.2 for successes)
    min_confidence: Optional[float] = None   # Overrides the watcher-wide threshold
    cooldown_s: Optional[float] = None       # Overrides the watcher-wide cooldown
    pre_event: Optional[float] = None        # Seconds before the event; None = profile default
    post_event: Optional[float] = None       # Seconds after the event; None = profile default
    tags: List[str] = field(default_factory=list)


@dataclass
class ScorerConfig:
    """A cheap always-on signal that pins a window for the VLM to judge (the cascade)."""

    topic: str                      # std_msgs Float32/Float64 score topic
    profile: str = ''               # Time Machine profile for the pin
    threshold: Optional[float] = None   # Fixed threshold; None = calibrate
    calibration_s: float = 60.0     # Seconds of normal operation used to calibrate
    alpha: float = 0.01             # Target false-alarm rate per score sample
    pre_event: float = 5.0          # Pin this long before the spike...
    post_event: float = 3.0         # ...and after it
    ttl_s: float = 30.0             # Pin falls back to the profile's timeout policy after this
    judge: bool = True              # Ask the VLM to keep/release; false = TTL policy decides


@dataclass
class WatcherConfig:
    """Everything the watcher needs to run."""

    events: List[WatchEvent]
    endpoint: str = 'http://localhost:8000/v1'
    model: str = 'qwen3-vl-4b-instruct'
    api_key_env: str = ''           # Name of an env var holding the API key (never the key)
    cameras: List[str] = field(default_factory=list)
    sample_hz: float = 1.0
    context_frames: int = 4
    state_topics: List[str] = field(default_factory=list)
    min_confidence: float = 0.6
    cooldown_s: float = 10.0
    request_timeout_s: float = 10.0
    max_image_px: int = 640         # Longest side sent to the VLM
    max_tokens: int = 300
    temperature: Optional[float] = None     # None = provider default (some models reject others)
    image_detail: str = ''          # '' | low | high | auto (OpenAI-style; cuts image tokens)
    extra_body: Dict[str, Any] = field(default_factory=dict)  # Provider-specific request fields
    event_trigger_topic: str = '/time_machine/event_trigger'
    allow_remote: bool = False      # Camera frames may leave this machine/LAN only if true
    robot_context: str = ''         # Free text: what the robot is doing, scene description
    scorers: List[ScorerConfig] = field(default_factory=list)
    history_s: float = 30.0         # Sampled frames kept for judging pinned windows
    judge_frames: int = 8           # Most frames sent when judging a pin
    time_machine_ns: str = '/time_machine'   # Where the pin services live

    def event(self, label: str) -> Optional[WatchEvent]:
        for event in self.events:
            if event.label == label:
                return event
        return None

    @property
    def labels(self) -> List[str]:
        return [event.label for event in self.events]

    def api_key(self) -> str:
        return os.environ.get(self.api_key_env, '') if self.api_key_env else ''


def _number(raw: Dict[str, Any], key: str, default, minimum=None, maximum=None, where=''):
    value = raw.get(key, default)
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise WatchListError(f'{where}{key} must be a number, got {raw.get(key)!r}')
    if minimum is not None and value < minimum:
        raise WatchListError(f'{where}{key} must be >= {minimum}, got {value}')
    if maximum is not None and value > maximum:
        raise WatchListError(f'{where}{key} must be <= {maximum}, got {value}')
    return value


def _string_list(raw: Dict[str, Any], key: str, where='') -> List[str]:
    value = raw.get(key, [])
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise WatchListError(f'{where}{key} must be a list of strings')
    return list(value)


def _is_local_endpoint(endpoint: str) -> bool:
    host = re.sub(r'^[a-z]+://', '', endpoint).split('/')[0].split(':')[0].lower()
    if host in ('localhost', '127.0.0.1', '::1', '0.0.0.0') or host.endswith('.local'):
        return True
    # A dotless name ("gpubox") only resolves through LAN DNS or /etc/hosts
    if host and '.' not in host and not host.startswith('['):
        return True
    # Private LAN ranges
    return bool(re.match(r'^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)', host))


def parse_watch_list(data: Dict[str, Any]) -> WatcherConfig:
    """Validate a parsed YAML document (with or without a top-level 'watcher' key)."""
    if not isinstance(data, dict):
        raise WatchListError('watch list must be a mapping')
    raw = data.get('watcher', data)
    if not isinstance(raw, dict):
        raise WatchListError("'watcher' must be a mapping")

    raw_events = raw.get('events')
    if not isinstance(raw_events, list) or not raw_events:
        raise WatchListError('watcher.events must be a non-empty list')

    events: List[WatchEvent] = []
    seen = set()
    for i, raw_event in enumerate(raw_events):
        where = f'events[{i}].'
        if not isinstance(raw_event, dict):
            raise WatchListError(f'events[{i}] must be a mapping')
        label = raw_event.get('label')
        if not isinstance(label, str) or not _LABEL_RE.match(label):
            raise WatchListError(
                f'{where}label must be snake_case (e.g. object_dropped), got {label!r}')
        if label in seen:
            raise WatchListError(f'duplicate event label: {label}')
        seen.add(label)
        describe = raw_event.get('describe')
        if not isinstance(describe, str) or not describe.strip():
            raise WatchListError(f'{where}describe must be a non-empty sentence')
        events.append(WatchEvent(
            label=label,
            describe=describe.strip(),
            profile=str(raw_event.get('profile', '') or ''),
            sample_rate=_number(raw_event, 'sample_rate', 1.0, 0.0, 1.0, where),
            min_confidence=_number(raw_event, 'min_confidence', None, 0.0, 1.0, where),
            cooldown_s=_number(raw_event, 'cooldown_s', None, 0.0, None, where),
            pre_event=_number(raw_event, 'pre_event', None, 0.0, None, where),
            post_event=_number(raw_event, 'post_event', None, 0.0, None, where),
            tags=_string_list(raw_event, 'tags', where),
        ))

    config = WatcherConfig(
        events=events,
        endpoint=str(raw.get('endpoint', WatcherConfig.endpoint)).rstrip('/'),
        model=str(raw.get('model', WatcherConfig.model)),
        api_key_env=str(raw.get('api_key_env', '') or ''),
        cameras=_string_list(raw, 'cameras'),
        sample_hz=_number(raw, 'sample_hz', 1.0, 0.01, 30.0),
        context_frames=int(_number(raw, 'context_frames', 4, 1, 32)),
        state_topics=_string_list(raw, 'state_topics'),
        min_confidence=_number(raw, 'min_confidence', 0.6, 0.0, 1.0),
        cooldown_s=_number(raw, 'cooldown_s', 10.0, 0.0),
        request_timeout_s=_number(raw, 'request_timeout_s', 10.0, 0.1),
        max_image_px=int(_number(raw, 'max_image_px', 640, 64, 4096)),
        max_tokens=int(_number(raw, 'max_tokens', 300, 16, 8192)),
        temperature=_number(raw, 'temperature', None, 0.0, 2.0),
        image_detail=str(raw.get('image_detail', '') or ''),
        extra_body=raw.get('extra_body') or {},
        event_trigger_topic=str(
            raw.get('event_trigger_topic', WatcherConfig.event_trigger_topic)),
        allow_remote=bool(raw.get('allow_remote', False)),
        robot_context=str(raw.get('robot_context', '') or '').strip(),
    )

    if config.image_detail not in ('', 'low', 'high', 'auto'):
        raise WatchListError(
            f'image_detail must be low, high or auto, got {config.image_detail!r}')
    if not isinstance(config.extra_body, dict):
        raise WatchListError('extra_body must be a mapping of extra request fields')
    reserved = {'model', 'messages'} & set(config.extra_body)
    if reserved:
        raise WatchListError(f'extra_body cannot override {sorted(reserved)}')

    raw_scorers = raw.get('scorers', []) or []
    if not isinstance(raw_scorers, list):
        raise WatchListError('watcher.scorers must be a list')
    for i, raw_scorer in enumerate(raw_scorers):
        where = f'scorers[{i}].'
        if not isinstance(raw_scorer, dict) or not isinstance(raw_scorer.get('topic'), str):
            raise WatchListError(f'{where}topic is required')
        scorer = ScorerConfig(
            topic=raw_scorer['topic'],
            profile=str(raw_scorer.get('profile', '') or ''),
            threshold=_number(raw_scorer, 'threshold', None, where=where),
            calibration_s=_number(raw_scorer, 'calibration_s', 60.0, 1.0, where=where),
            alpha=_number(raw_scorer, 'alpha', 0.01, 1e-6, 0.5, where),
            pre_event=_number(raw_scorer, 'pre_event', 5.0, 0.0, where=where),
            post_event=_number(raw_scorer, 'post_event', 3.0, 0.0, where=where),
            ttl_s=_number(raw_scorer, 'ttl_s', 30.0, 1.0, where=where),
            judge=bool(raw_scorer.get('judge', True)),
        )
        config.scorers.append(scorer)
    config.history_s = _number(raw, 'history_s', 30.0, 1.0, 600.0)
    config.judge_frames = int(_number(raw, 'judge_frames', 8, 1, 32))
    config.time_machine_ns = str(raw.get('time_machine_ns', '/time_machine')).rstrip('/')
    longest_pin = max([s.pre_event + s.post_event for s in config.scorers], default=0.0)
    if longest_pin > config.history_s:
        raise WatchListError(
            f'history_s ({config.history_s}) must cover the longest scorer pin '
            f'({longest_pin}s) or the judge cannot see the whole window')

    if not config.cameras:
        raise WatchListError('watcher.cameras must list at least one image topic')
    if not config.endpoint.startswith(('http://', 'https://')):
        raise WatchListError(f'watcher.endpoint must be an http(s) URL, got {config.endpoint}')
    if not config.allow_remote and not _is_local_endpoint(config.endpoint):
        raise WatchListError(
            f'endpoint {config.endpoint} is not on this machine or a private LAN; camera '
            'frames would leave the site. Set allow_remote: true to opt in.')
    return config


def load_watch_list(path: str, overrides: Optional[Dict[str, Any]] = None) -> WatcherConfig:
    """Load and validate a watch list YAML file.

    ``overrides`` replace top-level watcher keys (e.g. endpoint, model from launch
    arguments) before validation, so they get the same checks as the file.
    """
    with open(path, 'r', encoding='utf-8') as f:
        try:
            data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise WatchListError(f'invalid YAML in {path}: {e}') from e
    if overrides:
        if not isinstance(data, dict):
            raise WatchListError('watch list must be a mapping')
        watcher = data.get('watcher', data)
        if not isinstance(watcher, dict):
            raise WatchListError("'watcher' must be a mapping")
        watcher.update({k: v for k, v in overrides.items() if v not in (None, '')})
    return parse_watch_list(data)
