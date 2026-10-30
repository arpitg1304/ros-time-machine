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

"""Review saved clips and measure trigger precision per label.

    ros2 run rtm_watcher review list  /tmp/ros_time_machine/clips
    ros2 run rtm_watcher review mark  /tmp/ros_time_machine/clips/object_dropped_17... good
    ros2 run rtm_watcher review stats /tmp/ros_time_machine/clips
    ros2 run rtm_watcher review eval  /tmp/ros_time_machine/clips --truth labels.csv

Verdicts are written into each clip's event.json ("review"), so they travel
with the clip. Needs no ROS: `python3 -m rtm_watcher.review` works anywhere.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import getpass
import json
import os
import statistics
import sys
from typing import Dict, Iterable, List, Optional, Tuple

SIDECAR = 'event.json'
VERDICTS = ('good', 'bad')


@dataclass
class Clip:
    path: str        # Bag directory
    event: dict      # Parsed event.json

    @property
    def name(self) -> str:
        return os.path.basename(self.path.rstrip(os.sep))

    @property
    def trigger(self) -> dict:
        return self.event.get('trigger') or {}

    @property
    def label(self) -> str:
        return self.trigger.get('label', '?')

    @property
    def source(self) -> str:
        return self.trigger.get('source', '?')

    @property
    def confidence(self) -> Optional[float]:
        value = self.trigger.get('confidence')
        return float(value) if isinstance(value, (int, float)) else None

    @property
    def lag(self) -> Optional[float]:
        value = self.trigger.get('detection_lag_s')
        return float(value) if isinstance(value, (int, float)) else None

    @property
    def verdict(self) -> Optional[str]:
        review = self.event.get('review')
        return review.get('verdict') if isinstance(review, dict) else None

    @property
    def pre_event_complete(self) -> Optional[bool]:
        return (self.event.get('coverage') or {}).get('pre_event_complete')


def find_clips(root: str) -> List[Clip]:
    """Every clip under root (or root itself if it is a clip), oldest event first."""
    clips = []
    if os.path.isfile(os.path.join(root, SIDECAR)):
        candidates: Iterable[str] = [root]
    else:
        candidates = (dirpath for dirpath, _, files in os.walk(root) if SIDECAR in files)
    for path in candidates:
        try:
            with open(os.path.join(path, SIDECAR), 'r', encoding='utf-8') as f:
                clips.append(Clip(path, json.load(f)))
        except (OSError, ValueError) as e:
            print(f'warning: skipping {path}: {e}', file=sys.stderr)
    return sorted(clips, key=lambda c: (c.event.get('event_stamp_ns', 0), c.name))


def mark(clip_path: str, verdict: str, note: str = '', by: str = '') -> dict:
    """Record a verdict in the clip's event.json (atomically)."""
    if verdict not in VERDICTS:
        raise ValueError(f'verdict must be one of {VERDICTS}')
    path = os.path.join(clip_path, SIDECAR)
    with open(path, 'r', encoding='utf-8') as f:
        event = json.load(f)
    event['review'] = {
        'verdict': verdict,
        'note': note,
        'by': by or _whoami(),
        'at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z',
    }
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(event, f, indent=2)
        f.write('\n')
    os.replace(tmp, path)
    return event


def _whoami() -> str:
    try:
        return getpass.getuser()
    except (KeyError, OSError):
        return 'unknown'


@dataclass
class GroupStats:
    clips: int = 0
    reviewed: int = 0
    good: int = 0
    incomplete_pre_event: int = 0
    confidences: Tuple[float, ...] = ()
    lags: Tuple[float, ...] = ()

    @property
    def precision(self) -> Optional[float]:
        return self.good / self.reviewed if self.reviewed else None


def group_stats(clips: List[Clip], key) -> Dict[str, GroupStats]:
    groups: Dict[str, GroupStats] = {}
    for clip in clips:
        g = groups.setdefault(key(clip), GroupStats())
        g.clips += 1
        if clip.verdict in VERDICTS:
            g.reviewed += 1
            g.good += clip.verdict == 'good'
        if clip.pre_event_complete is False:
            g.incomplete_pre_event += 1
        if clip.confidence is not None:
            g.confidences += (clip.confidence,)
        if clip.lag is not None:
            g.lags += (clip.lag,)
    return groups


def suggest_threshold(clips: List[Clip], target_precision: float,
                      min_reviewed: int = 5) -> Optional[float]:
    """Lowest confidence cutoff whose reviewed clips reach the target precision."""
    reviewed = sorted(((c.confidence, c.verdict == 'good') for c in clips
                       if c.verdict in VERDICTS and c.confidence is not None), reverse=True)
    if len(reviewed) < min_reviewed:
        return None
    best = None
    good = 0
    for i, (confidence, is_good) in enumerate(reviewed, start=1):
        good += is_good
        # Only cut between distinct confidences
        next_conf = reviewed[i][0] if i < len(reviewed) else None
        if confidence == next_conf:
            continue
        if i >= min_reviewed and good / i >= target_precision:
            best = confidence
    return best


@dataclass
class TruthEvent:
    label: str
    stamp_ns: int


def load_truth(path: str) -> List[TruthEvent]:
    """CSV with columns label,time_s (robot time in seconds, e.g. bag time)."""
    events = []
    with open(path, newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            events.append(TruthEvent(row['label'].strip(), int(float(row['time_s']) * 1e9)))
    return events


def evaluate(clips: List[Clip], truth: List[TruthEvent], match_label: bool = True,
             near: Tuple[float, float] = (3.0, 8.0)) -> dict:
    """The plan's metrics: recall, precision, detection lag, pre-event coverage.

    A labelled event is recalled if some clip's window contains it (with the same
    label unless match_label is False). A clip is precise if its window contains
    a labelled event. Lag runs from the labelled time to the trigger's arrival.

    Long windows can contain an event the trigger had nothing to do with, so the
    `*_near` metrics look at the frame each trigger pointed at instead: an event
    counts when a trigger's event_stamp lies from near[0] s before to near[1] s
    after it. `noticed` accepts a trigger of any label there.
    """
    def window(clip):
        w = clip.event.get('window') or {}
        return w.get('start_ns', 0), w.get('end_ns', 0)

    def contains(clip, event):
        start, end = window(clip)
        labels = {e.get('label') for e in clip.event.get('events') or [clip.trigger]}
        return start <= event.stamp_ns <= end and (not match_label or event.label in labels)

    recalled, lags = 0, []
    for event in truth:
        hits = [c for c in clips if contains(c, event)]
        if hits:
            recalled += 1
            arrivals = [e.get('event_stamp_ns', 0) + int(e['detection_lag_s'] * 1e9)
                        for c in hits for e in (c.event.get('events') or [c.trigger])
                        if isinstance(e.get('detection_lag_s'), (int, float))]
            if arrivals:
                lags.append((min(arrivals) - event.stamp_ns) / 1e9)
    precise = sum(1 for c in clips if any(contains(c, e) for e in truth))
    complete = [c.pre_event_complete for c in clips if c.pre_event_complete is not None]

    before_ns, after_ns = int(near[0] * 1e9), int(near[1] * 1e9)
    triggers = [(c, e.get('label'), e.get('event_stamp_ns', 0))
                for c in clips for e in (c.event.get('events') or [c.trigger])]

    def close(stamp_ns, event):
        return -before_ns <= stamp_ns - event.stamp_ns <= after_ns

    noticed = sum(1 for ev in truth if any(close(st, ev) for _, _, st in triggers))
    recalled_near = sum(1 for ev in truth if any(lab == ev.label and close(st, ev) for _, lab, st in triggers))
    precise_near = sum(1 for c in clips if any(
        cc is c and close(st, ev) and lab == ev.label for cc, lab, st in triggers for ev in truth))

    def pct(values, q):
        values = sorted(values)
        return values[min(len(values) - 1, int(q * len(values)))] if values else None

    return {
        'truth_events': len(truth),
        'clips': len(clips),
        'recall': recalled / len(truth) if truth else None,
        'precision': precise / len(clips) if clips else None,
        'lag_p50_s': pct(lags, 0.5),
        'lag_p99_s': pct(lags, 0.99),
        'pre_event_coverage': sum(complete) / len(complete) if complete else None,
        'noticed': noticed / len(truth) if truth else None,
        'recall_near': recalled_near / len(truth) if truth else None,
        'precision_near': precise_near / len(clips) if clips else None,
    }


def _fmt(value, spec='.2f', missing='-'):
    return missing if value is None else format(value, spec)


def _print_table(rows: List[List[str]], header: List[str]):
    widths = [max(len(str(x)) for x in col) for col in zip(header, *rows)]
    for row in [header] + rows:
        print('  '.join(str(x).ljust(w) for x, w in zip(row, widths)).rstrip())


def cmd_list(args) -> int:
    clips = find_clips(args.root)
    if args.label:
        clips = [c for c in clips if c.label == args.label]
    if args.unreviewed:
        clips = [c for c in clips if c.verdict is None]
    rows = [[c.name, c.label, c.source, _fmt(c.confidence), _fmt(c.lag, '.1f'),
             {True: 'yes', False: 'NO', None: '-'}[c.pre_event_complete], c.verdict or '-',
             (c.trigger.get('description') or '')[:60]] for c in clips]
    _print_table(rows, ['clip', 'label', 'source', 'conf', 'lag_s', 'pre_ok', 'review',
                        'description'])
    print(f'\n{len(clips)} clip(s)')
    return 0


def cmd_mark(args) -> int:
    for path in args.clips:
        mark(path, args.verdict, args.note, args.by)
        print(f'{os.path.basename(path.rstrip(os.sep))}: {args.verdict}')
    return 0


def cmd_stats(args) -> int:
    clips = find_clips(args.root)
    by_label = group_stats(clips, lambda c: c.label)
    by_source = group_stats(clips, lambda c: c.source)
    suggestions = {label: suggest_threshold([c for c in clips if c.label == label],
                                            args.target_precision) for label in by_label}

    if args.json:
        def dump(groups):
            return {k: {'clips': g.clips, 'reviewed': g.reviewed, 'good': g.good,
                        'precision': g.precision,
                        'incomplete_pre_event': g.incomplete_pre_event,
                        'median_lag_s': statistics.median(g.lags) if g.lags else None}
                    for k, g in groups.items()}
        print(json.dumps({'labels': dump(by_label), 'sources': dump(by_source),
                          'suggested_min_confidence': suggestions}, indent=2))
        return 0

    def table(groups, title, with_suggestion):
        rows = []
        for key, g in sorted(groups.items()):
            row = [key, g.clips, g.reviewed, _fmt(g.precision),
                   _fmt(statistics.median(g.lags) if g.lags else None, '.1f'),
                   _fmt(max(g.lags) if g.lags else None, '.1f'), g.incomplete_pre_event]
            if with_suggestion:
                row.append(_fmt(suggestions.get(key)))
            rows.append(row)
        header = [title, 'clips', 'reviewed', 'precision', 'lag_p50', 'lag_max', 'pre_short']
        if with_suggestion:
            header.append(f'min_conf@{args.target_precision:g}')
        _print_table(rows, header)

    table(by_label, 'label', True)
    print()
    table(by_source, 'source', False)
    return 0


def cmd_eval(args) -> int:
    result = evaluate(find_clips(args.root), load_truth(args.truth), not args.any_label, tuple(args.near))
    if args.json:
        print(json.dumps(result, indent=2))
        return 0
    rows = [[k, _fmt(v, '.3f') if isinstance(v, float) else _fmt(v, 'd')]
            for k, v in result.items()]
    _print_table(rows, ['metric', 'value'])
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog='review', description='Review Time Machine clips and trigger precision.')
    sub = parser.add_subparsers(dest='command', required=True)

    p = sub.add_parser('list', help='list clips and their triggers')
    p.add_argument('root')
    p.add_argument('--label')
    p.add_argument('--unreviewed', action='store_true')
    p.set_defaults(func=cmd_list)

    p = sub.add_parser('mark', help='record a good/bad verdict in event.json')
    p.add_argument('clips', nargs='+', metavar='clip')
    p.add_argument('verdict', choices=VERDICTS)
    p.add_argument('--note', default='')
    p.add_argument('--by', default='')
    p.set_defaults(func=cmd_mark)

    p = sub.add_parser('stats', help='precision and lag per label and source')
    p.add_argument('root')
    p.add_argument('--target-precision', type=float, default=0.8)
    p.add_argument('--json', action='store_true')
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser('eval', help='recall, precision and lag against labelled events')
    p.add_argument('root')
    p.add_argument('--truth', required=True, help='CSV with columns label,time_s')
    p.add_argument('--any-label', action='store_true',
                   help='count a clip as a hit whatever label it fired with')
    p.add_argument('--near', nargs=2, type=float, default=[3.0, 8.0], metavar=('BEFORE', 'AFTER'),
                   help='*_near metrics: trigger frame this many s before/after the event (3 8)')
    p.add_argument('--json', action='store_true')
    p.set_defaults(func=cmd_eval)

    args, _ = parser.parse_known_args(argv)  # Tolerate --ros-args from ros2 run
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
