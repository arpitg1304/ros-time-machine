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

"""Your first clip from your own robot, with no YAML to write.

    ros2 run rtm_watcher quickstart                       # press Enter to save the last 10 s
    ros2 run rtm_watcher quickstart --vlm fake            # plus the watcher, with a scripted VLM
    ros2 run rtm_watcher quickstart --vlm http://gpubox:8000/v1 --model qwen3-vl-4b-instruct

Finds the robot's camera and joint-state topics, writes a Time Machine config and a
watch list into ./rtm_quickstart/, and runs Time Machine (and, with --vlm, the watcher)
on them. Files you have edited are never overwritten, so the next step is to edit
watch_list.yaml and run again.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
import os
import signal
import subprocess
import sys
import threading
import time
from typing import Dict, List, Optional, Sequence, Tuple

COMPRESSED = 'sensor_msgs/msg/CompressedImage'
RAW = 'sensor_msgs/msg/Image'
JOINTS = 'sensor_msgs/msg/JointState'
# Topics that are ROS or Time Machine plumbing, never worth recording
IGNORED_PREFIXES = ('/rosout', '/parameter_events', '/time_machine/', '/rtm_watcher/',
                    '/rtm_quickstart')
PROFILE = 'quickstart'
FAKE_ENDPOINT = 'http://127.0.0.1:8765/v1'
MAX_WATCHED_CAMERAS = 2  # Every camera adds images to each VLM request

STARTER_EVENTS = [
    ('object_dropped', 'An object falls out of the gripper or off the table.'),
    ('collision', 'The robot hits an object, a person or its surroundings.'),
    ('person_in_cell', "A person enters the robot's work area."),
]


@dataclass
class Selection:
    cameras: List[str] = field(default_factory=list)      # watched by the VLM
    state: List[str] = field(default_factory=list)        # summarized as text for the VLM
    record: List[str] = field(default_factory=list)       # buffered and saved in each clip
    raw_cameras: bool = False                              # any uncompressed image topic


def choose_topics(topics: Sequence[Tuple[str, Sequence[str]]],
                  wanted: Optional[Sequence[str]] = None) -> Selection:
    """Pick cameras and joint states from (name, [types]) pairs, as rclpy reports them.

    A raw image topic with a compressed sibling (image_transport's ``.../compressed``)
    is replaced by the sibling: it is far cheaper to buffer and to send to a VLM.
    ``wanted`` restricts the choice to those topics (all of them are recorded).
    """
    types: Dict[str, str] = {}
    for name, names_of_types in topics:
        if name.startswith(IGNORED_PREFIXES) or not names_of_types:
            continue
        types[name] = names_of_types[0]

    candidates = list(wanted) if wanted else sorted(types)
    selection = Selection()
    for name in candidates:
        kind = types.get(name, '')
        if kind == RAW and types.get(name.rstrip('/') + '/compressed') == COMPRESSED:
            continue
        if kind in (COMPRESSED, RAW):
            selection.cameras.append(name)
            selection.raw_cameras |= kind == RAW
            selection.record.append(name)
        elif kind == JOINTS:
            selection.state.append(name)
            selection.record.append(name)
        elif wanted:
            selection.record.append(name)
    selection.cameras = selection.cameras[:MAX_WATCHED_CAMERAS]
    return selection


def _yaml_list(items: Sequence[str]) -> str:
    # JSON strings are valid YAML scalars, so topic names need no escaping rules of their own
    return '[' + ', '.join(json.dumps(item) for item in items) + ']'


def time_machine_yaml(selection: Selection, clips_dir: str, pre_s: float, post_s: float) -> str:
    buffer_mb = 1000 if selection.raw_cameras else 300
    return f'''# Time Machine config written by `ros2 run rtm_watcher quickstart`. Edit freely:
# quickstart never overwrites this file (delete it, or pass --fresh, to regenerate).
# Reference: src/ros_time_machine/config/semantic_triggers.yaml
snapshots:
  - name: "{PROFILE}"
    recording:
      topics: {_yaml_list(selection.record)}
      pre_event_duration: {pre_s}       # seconds saved before the moment you flag
      post_event_duration: {post_s}     # seconds saved after it
      max_buffer_size: {buffer_mb}       # MB per topic
    rate_limit:
      max_triggers_per_minute: 30
    output:
      path: {json.dumps(clips_dir)}
      name_pattern: "{{label}}_{{timestamp}}"
      format: "mcap"

global:
  max_trigger_latency: 5.0       # extra buffer for VLM detection lag (deploy/vllm/check.sh)
  publish_stats: true
'''


def watch_list_yaml(selection: Selection) -> str:
    events = '\n'.join(
        f'    - label: {label}\n      describe: {json.dumps(describe)}\n'
        f'      profile: {PROFILE}'
        for label, describe in STARTER_EVENTS)
    return f'''# Watch list written by `ros2 run rtm_watcher quickstart`. Edit freely:
# quickstart never overwrites this file (delete it, or pass --fresh, to regenerate).
# Write events as what the camera sees. Reference: docs/INTEGRATION_GUIDE.md, stage 5.
watcher:
  endpoint: {FAKE_ENDPOINT}   # quickstart --vlm overrides this and model
  model: fake-vlm
  request_timeout_s: 30
  cameras: {_yaml_list(selection.cameras)}
  state_topics: {_yaml_list(selection.state)}
  sample_hz: 1.0
  context_frames: 4
  robot_context: "A robot at work. Replace this with one sentence about your robot and task."
  min_confidence: 0.5            # low on purpose: missing an event costs more than an extra clip
  cooldown_s: 5
  events:
{events}
'''


def write_if_missing(path: str, text: str, fresh: bool) -> bool:
    """Write ``text`` unless the file exists (and ``fresh`` is false). True if written."""
    if os.path.exists(path) and not fresh:
        return False
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)
    return True


def clip_summary(clip_dir: str) -> str:
    """'8.0 s, 2 topics, 520 messages' from a clip's event.json, or '' if unreadable."""
    try:
        with open(os.path.join(clip_dir, 'event.json'), encoding='utf-8') as f:
            event = json.load(f)
        window = event['window']
        seconds = (window['end_ns'] - window['start_ns']) / 1e9
        topics = event.get('topics', [])
        messages = sum(t.get('messages', 0) for t in topics)
        short = '' if event.get('coverage', {}).get('pre_event_complete', True) else \
            ' (buffer was still filling: the start is short)'
        return f'{seconds:.1f} s, {len(topics)} topics, {messages:,} messages{short}'
    except (OSError, ValueError, KeyError, TypeError):
        return ''


# ---------------------------------------------------------------- the running part (ROS)

class Children:
    """The nodes quickstart starts, each in its own session so only we see Ctrl+C."""

    def __init__(self, log_dir: str):
        self.log_dir = log_dir
        self.procs: List[Tuple[str, subprocess.Popen, str]] = []

    def start(self, name: str, package: str, executable: str, args: List[str]) -> None:
        # Run the executable itself: `ros2 run` does not pass SIGINT on to its child
        from ament_index_python.packages import get_package_prefix
        cmd = [os.path.join(get_package_prefix(package), 'lib', package, executable), *args]
        log_path = os.path.join(self.log_dir, f'{name}.log')
        log = open(log_path, 'w', encoding='utf-8')
        proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, start_new_session=True)
        self.procs.append((name, proc, log_path))

    def dead(self) -> Optional[Tuple[str, str]]:
        for name, proc, log_path in self.procs:
            if proc.poll() is not None:
                return name, log_path
        return None

    def stop(self, timeout_s: float = 20.0) -> None:
        # Time Machine writes pending clips on SIGINT, so give it time before escalating
        for _, proc, _ in self.procs:
            self._signal(proc, signal.SIGINT)
        deadline = time.monotonic() + timeout_s
        for _, proc, _ in self.procs:
            try:
                proc.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                self._signal(proc, signal.SIGTERM)
                proc.wait(timeout=5.0)

    @staticmethod
    def _signal(proc: subprocess.Popen, sig: int) -> None:
        # Each child leads its own process group, so this also reaches anything it started
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, sig)
            except ProcessLookupError:
                pass


def _tail(path: str, lines: int = 15) -> str:
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            return ''.join(f.readlines()[-lines:])
    except OSError:
        return ''


def discover(node, wait_s: float, wanted: Optional[Sequence[str]]) -> Selection:
    """Poll the ROS graph until a camera (or the wanted topics) shows up, up to wait_s."""
    import rclpy
    deadline = time.monotonic() + wait_s
    while True:
        rclpy.spin_once(node, timeout_sec=0.25)
        selection = choose_topics(node.get_topic_names_and_types(), wanted)
        if (selection.cameras or (wanted and selection.record)) or time.monotonic() > deadline:
            return selection


def run(args) -> int:
    import rclpy
    from rclpy.executors import ExternalShutdownException
    from rclpy.node import Node
    from rclpy.parameter import Parameter
    from rclpy.qos import QoSProfile, ReliabilityPolicy
    from ros_time_machine_msgs.msg import EventTrigger, RecordingStatus

    out_dir = os.path.abspath(args.out)
    clips_dir = os.path.join(out_dir, 'clips')
    log_dir = os.path.join(out_dir, 'logs')
    os.makedirs(clips_dir, exist_ok=True)
    os.makedirs(log_dir, exist_ok=True)
    tm_path = os.path.join(out_dir, 'time_machine.yaml')
    wl_path = os.path.join(out_dir, 'watch_list.yaml')
    sim = 'true' if args.use_sim_time else 'false'

    rclpy.init()
    node = Node('rtm_quickstart', parameter_overrides=[
        Parameter('use_sim_time', value=args.use_sim_time)])
    children = Children(log_dir)
    try:
        need_tm = args.fresh or not os.path.exists(tm_path)
        need_wl = args.fresh or not os.path.exists(wl_path)
        for path, needed in ((tm_path, need_tm), (wl_path, need_wl)):
            if not needed:
                print(f'Using your {path} (delete it, or pass --fresh, to regenerate)')
        if need_tm or need_wl:
            print('Looking for camera and joint-state topics...')
            selection = discover(node, args.wait, args.topics)
            if not selection.record:
                print('No camera or joint-state topics found. Is the robot (or a bag:\n'
                      '`ros2 bag play <bag> --loop`) running in this ROS_DOMAIN_ID? '
                      'Or name topics with --topics.', file=sys.stderr)
                return 1
            print('  recording: ' + ', '.join(selection.record))
            if selection.cameras:
                print('  watching:  ' + ', '.join(selection.cameras))
            if selection.raw_cameras:
                print('  note: uncompressed images use a lot of memory; publish the '
                      '.../compressed topic if you can')
            if need_tm:
                write_if_missing(tm_path, time_machine_yaml(
                    selection, clips_dir, args.pre, args.post), True)
                print(f'Wrote {tm_path}')
            if need_wl and selection.cameras:
                write_if_missing(wl_path, watch_list_yaml(selection), True)
                print(f'Wrote {wl_path}')
        if args.vlm and not os.path.exists(wl_path):
            print('No camera topic, so no VLM watcher: saving on Enter only.')
            args.vlm = ''

        ros_args = ['--ros-args', '-p', f'use_sim_time:={sim}']
        # Same node names as the launch files, so topics and services are /time_machine/...
        children.start('time_machine', 'ros_time_machine', 'time_machine_node', [
            *ros_args, '-r', '__node:=time_machine', '-p', f'config_file:={tm_path}'])
        if args.vlm:
            endpoint, model = args.vlm, args.model
            if args.vlm == 'fake':
                endpoint, model = FAKE_ENDPOINT, 'fake-vlm'
                children.start('fake_vlm', 'rtm_watcher', 'fake_vlm', [
                    '--port', '8765', '--fire', 'object_dropped', '--every', '5',
                    '--delay', '1.0'])
            elif not model:
                print('--vlm needs --model (the served model name)', file=sys.stderr)
                return 2
            watcher_args = [*ros_args, '-r', '__node:=rtm_watcher', '-p', f'watch_list:={wl_path}',
                            '-p', f'endpoint:={endpoint}', '-p', f'model:={model}',
                            '-p', f'allow_remote:={"true" if args.allow_remote else "false"}']
            if args.api_key_env:  # rcl rejects an empty `-p name:=`
                watcher_args += ['-p', f'api_key_env:={args.api_key_env}']
            children.start('watcher', 'rtm_watcher', 'watcher_node', watcher_args)

        reliable = QoSProfile(depth=50, reliability=ReliabilityPolicy.RELIABLE)
        trigger_pub = node.create_publisher(EventTrigger, '/time_machine/event_trigger', reliable)
        saved = []

        def on_status(msg):
            if msg.status == RecordingStatus.STATUS_COMPLETED and msg.output_path:
                summary = clip_summary(msg.output_path)
                print(f'Saved {msg.output_path}' + (f'  ({summary})' if summary else ''))
                saved.append(msg.output_path)
            elif msg.status == RecordingStatus.STATUS_FAILED:
                print(f'Clip failed: {msg.error_message}', file=sys.stderr)

        def on_trigger(msg):
            if msg.source and msg.source != 'quickstart':
                print(f'{msg.source}: {msg.label} ({msg.confidence:.2f}) "{msg.description}"')

        node.create_subscription(RecordingStatus, '/time_machine/recording_status',
                                 on_status, reliable)
        node.create_subscription(EventTrigger, '/time_machine/event_trigger', on_trigger,
                                 reliable)

        # Wait for Time Machine to listen before offering to save
        deadline = time.monotonic() + 30.0
        while trigger_pub.get_subscription_count() == 0:
            rclpy.spin_once(node, timeout_sec=0.2)
            failed = children.dead()
            if failed or time.monotonic() > deadline:
                name, log_path = failed or ('time_machine', os.path.join(log_dir,
                                                                         'time_machine.log'))
                print(f'{name} did not start. Last lines of {log_path}:\n{_tail(log_path)}',
                      file=sys.stderr)
                return 1

        requests: List[float] = []
        give_up = float('inf')
        if args.save_in is not None:
            requests.append(time.monotonic() + args.save_in)
            give_up = time.monotonic() + args.save_in + args.post + 30.0
            print(f'Saving the last {args.pre:g} s in {args.save_in:g} s...')
        else:
            print(f'\nRecording. Press Enter to save the last {args.pre:g} s '
                  f'(and the next {args.post:g} s). Ctrl+C to stop.')

            def read_keys():
                for _ in sys.stdin:
                    requests.append(time.monotonic())
            threading.Thread(target=read_keys, daemon=True).start()

        if args.vlm:
            print(f'Watching for: {", ".join(label for label, _ in STARTER_EVENTS)} '
                  f'(edit {wl_path})')

        while True:
            rclpy.spin_once(node, timeout_sec=0.1)
            now = time.monotonic()
            while requests and requests[0] <= now:
                requests.pop(0)
                trigger_pub.publish(EventTrigger(profile=PROFILE, label='manual',
                                                 source='quickstart', confidence=1.0))
            failed = children.dead()
            if failed:
                print(f'{failed[0]} stopped. Last lines of {failed[1]}:\n{_tail(failed[1])}',
                      file=sys.stderr)
                return 1
            if args.save_in is not None and saved:
                break
            if now > give_up:
                print('No clip was written; see ' + log_dir, file=sys.stderr)
                return 1
        return 0
    except (KeyboardInterrupt, ExternalShutdownException):
        return 0
    finally:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        if children.procs:
            print('\nStopping (clips still pending are written first)...')
        children.stop()
        node.destroy_node()
        rclpy.try_shutdown()
        if os.path.exists(wl_path):
            print(f'\nNext: describe your own events in {wl_path}, then\n'
                  '  ros2 run rtm_watcher quickstart --vlm http://<gpu-box>:8000/v1 '
                  '--model qwen3-vl-4b-instruct\n'
                  f'Clips and their event.json are in {clips_dir}; '
                  f'`ros2 run rtm_watcher review list {clips_dir}` lists them.')


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog='quickstart',
        description='Save your first clip from your own robot, with no YAML to write.')
    parser.add_argument('--vlm', default='', metavar='ENDPOINT',
                        help="also run the watcher: 'fake' for a scripted VLM, or an "
                             'OpenAI-compatible URL such as http://gpubox:8000/v1')
    parser.add_argument('--model', default='', help='served model name, with --vlm URL')
    parser.add_argument('--api-key-env', default='', help='env var holding the API key')
    parser.add_argument('--allow-remote', action='store_true',
                        help='allow a hosted endpoint (camera frames leave the site)')
    parser.add_argument('--topics', nargs='+', metavar='TOPIC',
                        help='record these topics instead of the cameras and joint states found')
    parser.add_argument('--out', default='rtm_quickstart',
                        help='folder for the configs, clips and logs (./rtm_quickstart)')
    parser.add_argument('--pre', type=float, default=10.0, help='seconds saved before (10)')
    parser.add_argument('--post', type=float, default=2.0, help='seconds saved after (2)')
    parser.add_argument('--wait', type=float, default=5.0,
                        help='seconds to wait for topics to appear (5)')
    parser.add_argument('--fresh', action='store_true', help='regenerate both config files')
    parser.add_argument('--save-in', type=float, metavar='SECONDS',
                        help='flag a moment after this many seconds and exit once a clip is saved')
    parser.add_argument('--use-sim-time', action='store_true',
                        help='follow /clock, for `ros2 bag play --clock`')
    args, _ = parser.parse_known_args(argv)  # ros2 run may append --ros-args
    sys.stdout.reconfigure(line_buffering=True)  # progress shows up in piped logs and CI
    return run(args)


if __name__ == '__main__':
    sys.exit(main())
