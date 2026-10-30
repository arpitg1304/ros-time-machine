"""lerobot-rewind: replay LeRobot datasets as a running ROS 2 robot.

    lerobot-rewind info    <dataset>
    lerobot-rewind convert <dataset> session.mcap [--episodes 0-44] [--labels labels.csv]
    lerobot-rewind events  <dataset> --joint gripper --below 15 [--template labels.csv]
    lerobot-rewind sheets  <dataset> --camera top --out sheets/ep [--every 2] [--crop x,y,w,h]

<dataset> is a local LeRobot dataset folder or a Hugging Face repo id (with the [hub] extra).
Play the result with `ros2 bag play session.mcap --clock`.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from typing import List, Optional

from .dataset import LeRobotDataset, resolve
from .session import Options, Topics, labels_to_truth, write_info, write_session


def parse_episodes(spec: str, available: List[int]) -> Optional[List[int]]:
    """'0-44', '3,10,15-20' or '' (all) to a list of episode indices."""
    if not spec:
        return None
    wanted = set()
    for part in spec.split(','):
        lo, _, hi = part.strip().partition('-')
        wanted.update(range(int(lo), int(hi or lo) + 1))
    return [e for e in available if e in wanted]


def cmd_info(a) -> int:
    ds = LeRobotDataset(resolve(a.dataset))
    lengths = [ds.episode(i).length for i in ds.episode_indices[:1]]
    print(f'{ds.root}\n  format {ds.version}, {len(ds.episode_indices)} episodes at {ds.fps:g} fps\n'
          f'  cameras: {", ".join(ds.cameras) or "none"}\n  joints: {", ".join(ds.state_names)}\n'
          f'  first episode: {lengths[0] / ds.fps:.1f} s')
    return 0


def cmd_convert(a) -> int:
    ds = LeRobotDataset(resolve(a.dataset))
    opts = Options(episodes=parse_episodes(a.episodes, ds.episode_indices), gap_s=a.gap, lead_in_s=a.lead_in,
                   start_s=a.start, quality=a.quality,
                   topics=Topics(camera=a.camera_topic, state=a.state_topic, action=a.action_topic))
    session = write_session(ds, a.out, opts, progress=lambda s: print(s, flush=True))
    info = os.path.splitext(a.out)[0] + '.json'
    write_info(info, ds, session, opts)
    duration = (max(session.episode_end_ns.values()) - session.start_ns) / 1e9
    print(f'wrote {a.out}: {session.messages} messages, {duration:.0f} s of robot time; episode offsets in {info}')
    if a.labels:
        truth = a.truth or os.path.splitext(a.out)[0] + '_truth.csv'
        print(f'wrote {truth}: {labels_to_truth(a.labels, truth, session)} labelled events on the session clock')
    print(f'play it: ros2 bag play {a.out} --clock')
    return 0


def cmd_events(a) -> int:
    from .events import crossings
    ds = LeRobotDataset(resolve(a.dataset))
    rows = list(crossings(ds, a.joint, a.below))
    w = csv.writer(sys.stdout, lineterminator='\n')
    w.writerow(['episode', 'event', 't_s'])
    for ep, kind, t in rows:
        w.writerow([ep, kind, f'{t:.2f}'])
    if a.template:
        last_open = {}
        for ep, kind, t in rows:
            if kind == 'open':
                last_open[ep] = t
        with open(a.template, 'w', newline='') as f:
            out = csv.writer(f, lineterminator='\n')
            out.writerow(['episode', 'label', 't_rel_s', 'source'])
            for ep in ds.episode_indices:
                if ep in last_open:
                    out.writerow([ep, 'LABEL_ME', f'{last_open[ep]:.2f}', f'last {a.joint} release'])
        print(f'wrote {a.template}: replace LABEL_ME with what happened at each release', file=sys.stderr)
    return 0


def cmd_sheets(a) -> int:
    from .sheets import contact_sheets
    ds = LeRobotDataset(resolve(a.dataset))
    crop = tuple(int(v) for v in a.crop.split(',')) if a.crop else None
    os.makedirs(os.path.dirname(a.out) or '.', exist_ok=True)
    episodes = parse_episodes(a.episodes, ds.episode_indices)
    for p in contact_sheets(ds, a.camera, a.out, a.every, crop, episodes=episodes):
        print(p)
    return 0


def main(argv=None) -> int:
    summary, usage = __doc__.split('\n\n', 1)
    p = argparse.ArgumentParser(prog='lerobot-rewind', description=summary, epilog=usage,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='cmd', required=True)

    s = sub.add_parser('info', help='what is in a dataset')
    s.add_argument('dataset')
    s.set_defaults(func=cmd_info)

    s = sub.add_parser('convert', help='write episodes as one ROS 2 MCAP session')
    s.add_argument('dataset')
    s.add_argument('out', help='output .mcap file')
    s.add_argument('--episodes', default='', help="e.g. '0-44' or '3,10,15-20' (default: all)")
    s.add_argument('--gap', type=float, default=3.0, help='seconds between episodes (3)')
    s.add_argument('--lead-in', type=float, default=30.0, help='idle seconds before the first episode (30)')
    s.add_argument('--start', type=int, default=1_800_000_000, help='session start, Unix seconds')
    s.add_argument('--quality', type=int, default=4, help='JPEG quality, ffmpeg -q:v 2..31 (4)')
    s.add_argument('--camera-topic', default=Topics.camera, help='{camera} is replaced by the camera name')
    s.add_argument('--state-topic', default=Topics.state)
    s.add_argument('--action-topic', default=Topics.action)
    s.add_argument('--labels', help='episode,label,t_rel_s CSV to turn into session-clock ground truth')
    s.add_argument('--truth', help='where to write it (default: <out>_truth.csv)')
    s.set_defaults(func=cmd_convert)

    s = sub.add_parser('events', help='threshold crossings of a joint, e.g. gripper releases')
    s.add_argument('dataset')
    s.add_argument('--joint', default='gripper')
    s.add_argument('--below', type=float, default=15.0, help='closed below this value')
    s.add_argument('--template', help='write a labels.csv to fill in, one row per episode at its last release')
    s.set_defaults(func=cmd_events)

    s = sub.add_parser('sheets', help='contact sheets for labelling by eye')
    s.add_argument('dataset')
    s.add_argument('--camera', required=True)
    s.add_argument('--out', required=True, help='output prefix, e.g. sheets/ep')
    s.add_argument('--every', type=float, default=2.0)
    s.add_argument('--crop', help='x,y,w,h in source pixels')
    s.add_argument('--episodes', default='')
    s.set_defaults(func=cmd_sheets)

    a = p.parse_args(argv)
    return a.func(a)


if __name__ == '__main__':
    sys.exit(main())
