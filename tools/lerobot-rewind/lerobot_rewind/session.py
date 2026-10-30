"""Lay LeRobot episodes on one robot timeline and write them as a ROS 2 MCAP session."""

from __future__ import annotations

from dataclasses import dataclass, field
import csv
import json
from typing import Callable, Dict, List, Optional, Sequence

import numpy as np
from mcap_ros2.writer import Writer

from .dataset import Episode, LeRobotDataset
from .rosmsg import MSGDEFS, NS, compressed_image, joint_state
from .video import jpeg_frames, require_ffmpeg


@dataclass
class Topics:
    camera: str = '/camera/{camera}/image_raw/compressed'   # {camera}: top, wrist, front, ...
    state: str = '/joint_states'
    action: str = '/lerobot/action'
    episode: str = '/lerobot/episode'                         # "start <n>" / "end <n>"


@dataclass
class Options:
    episodes: Optional[Sequence[int]] = None   # default: all
    gap_s: float = 3.0                         # reset time between episodes
    lead_in_s: float = 30.0                    # idle robot before the first episode
    start_s: int = 1_800_000_000               # robot time of the session start (Unix seconds)
    quality: int = 4                           # ffmpeg -q:v for the JPEGs: 2 (best) .. 31
    topics: Topics = field(default_factory=Topics)


@dataclass
class Session:
    start_ns: int
    episode_start_ns: Dict[int, int]
    episode_end_ns: Dict[int, int]
    messages: int


def _frames(dataset: LeRobotDataset, ep: Episode, quality: int) -> Dict[str, List[bytes]]:
    out = {}
    for cam, (path, start, end) in ep.videos.items():
        out[cam] = jpeg_frames(path, start, end, quality, expected=ep.length)
    return out


def write_session(dataset: LeRobotDataset, out_path: str, opts: Options = Options(),
                  progress: Callable[[str], None] = lambda s: None) -> Session:
    """Write the episodes back to back, `gap_s` apart, after `lead_in_s` of idle robot.

    Cameras and joints keep publishing at the dataset's rate during the lead-in and the gaps,
    holding the nearest recorded frame and joint state, as a robot would between rollouts.
    Header stamps equal log times, so `ros2 bag play --clock` reproduces the robot's clock.
    """
    require_ffmpeg()
    fps = dataset.fps
    period = round(NS / fps)
    episodes = list(opts.episodes) if opts.episodes is not None else dataset.episode_indices
    t = opts.topics
    with open(out_path, 'wb') as f:
        w = Writer(f)
        schemas = {name: w.register_msgdef(name, text) for name, text in MSGDEFS.items()}
        image_s = schemas['sensor_msgs/msg/CompressedImage']
        joint_s = schemas['sensor_msgs/msg/JointState']
        string_s = schemas['std_msgs/msg/String']
        count = 0

        def emit(msgs):
            nonlocal count
            for ns, topic, schema, msg in sorted(msgs, key=lambda m: m[0]):
                w.write_message(topic, schema, msg, log_time=ns, publish_time=ns)
            count += len(msgs)

        def hold(start_ns, end_ns, frames, pos, act, msgs):
            for ns in range(start_ns, end_ns, period):
                for cam, jpg in frames.items():
                    msgs.append((ns, t.camera.format(camera=cam), image_s, compressed_image(ns, jpg, f'{cam}_camera')))
                msgs.append((ns, t.state, joint_s, joint_state(ns, dataset.state_names, pos)))
                if act is not None:
                    msgs.append((ns, t.action, joint_s, joint_state(ns, dataset.action_names, act)))

        session_start = opts.start_s * NS
        t0 = session_start + round(opts.lead_in_s * NS)
        idle_from, last = session_start, None
        starts, ends = {}, {}
        for i in episodes:
            ep = dataset.episode(i)
            frames = _frames(dataset, ep, opts.quality)
            state = np.stack(ep.data['observation.state'].values)
            action = np.stack(ep.data['action'].values) if 'action' in ep.data else None
            ts = ep.data['timestamp'].values.astype(float)
            msgs = []
            for cam, jpgs in frames.items():
                topic = t.camera.format(camera=cam)
                for k, jpg in enumerate(jpgs):
                    ns = t0 + round(k * NS / fps)
                    msgs.append((ns, topic, image_s, compressed_image(ns, jpg, f'{cam}_camera')))
            for k, (tt, pos) in enumerate(zip(ts, state)):
                ns = t0 + round(float(tt) * NS)
                msgs.append((ns, t.state, joint_s, joint_state(ns, dataset.state_names, pos)))
                if action is not None:
                    msgs.append((ns, t.action, joint_s, joint_state(ns, dataset.action_names, action[k])))
            end = max(m[0] for m in msgs)
            # The still scene before this episode: the previous one's end, or this one's start
            held = last or ({c: f[0] for c, f in frames.items()}, state[0],
                            action[0] if action is not None else None)
            hold(idle_from, t0, *held, msgs)
            msgs.append((t0, t.episode, string_s, {'data': f'start {i}'}))
            msgs.append((end, t.episode, string_s, {'data': f'end {i}'}))
            emit(msgs)
            starts[i], ends[i] = t0, end
            progress(f'episode {i}: {ep.length} frames, {(end - t0) / NS:.1f} s at +{(t0 - session_start) / NS:.1f} s')
            last = ({c: f[-1] for c, f in frames.items()}, state[-1], action[-1] if action is not None else None)
            idle_from = end + period
            t0 = end + round(opts.gap_s * NS)
        w.finish()
    return Session(session_start, starts, ends, count)


def write_info(path: str, dataset: LeRobotDataset, session: Session, opts: Options) -> None:
    """Sidecar JSON: where each episode sits on the session clock."""
    json.dump({'dataset': dataset.root, 'fps': dataset.fps, 'cameras': dataset.cameras,
               'joint_names': dataset.state_names, 'start_s': opts.start_s,
               'episode_start_ns': {str(k): v for k, v in session.episode_start_ns.items()},
               'episode_end_ns': {str(k): v for k, v in session.episode_end_ns.items()},
               'topics': {'camera': opts.topics.camera, 'state': opts.topics.state,
                          'action': opts.topics.action, 'episode': opts.topics.episode}},
              open(path, 'w'), indent=1)


def labels_to_truth(labels_csv: str, truth_csv: str, session: Session) -> int:
    """Turn per-episode labels (episode,label,t_rel_s[,...]) into session-clock ground truth
    (label,time_s), the format `ros2 run rtm_watcher review eval --truth` reads."""
    rows = 0
    with open(labels_csv) as src, open(truth_csv, 'w', newline='') as dst:
        out = csv.writer(dst, lineterminator='\n')
        out.writerow(['label', 'time_s'])
        for r in csv.DictReader(src):
            ep = int(r['episode'])
            if ep in session.episode_start_ns:
                out.writerow([r['label'], f"{session.episode_start_ns[ep] / NS + float(r['t_rel_s']):.3f}"])
                rows += 1
    return rows
