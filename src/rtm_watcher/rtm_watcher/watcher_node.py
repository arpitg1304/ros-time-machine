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

"""ROS 2 node: watch cameras with a VLM and publish time-addressed EventTriggers."""

from __future__ import annotations

from dataclasses import dataclass
import json
import threading
from typing import Dict, List

from builtin_interfaces.msg import Time as TimeMsg
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, qos_profile_sensor_data, ReliabilityPolicy
from ros_time_machine_msgs.msg import EventTrigger
from ros_time_machine_msgs.srv import PinRange, ReleasePin
from rosidl_runtime_py.utilities import get_message
from std_msgs.msg import String

from .calibration import ConformalThreshold
from .engine import TriggerRequest, WatcherEngine
from .frames import NS_PER_S, summarize_joint_state
from .images import compressed_to_jpeg, have_encoder, raw_to_jpeg
from .vlm_client import OpenAICompatibleClient
from .watchlist import load_watch_list, ScorerConfig, WatchListError

IMAGE_TYPES = ('sensor_msgs/msg/CompressedImage', 'sensor_msgs/msg/Image')
STATE_HZ = 5.0  # State topics are summarized at most this often
JUDGE_SETTLE_NS = NS_PER_S // 2  # Wait for the window's last frames before judging


@dataclass
class ScorerState:
    config: ScorerConfig
    gate: ConformalThreshold
    calibration_start_ns: int = 0
    refractory_until_ns: int = 0


@dataclass
class PendingJudge:
    pin_id: int
    start_ns: int
    end_ns: int
    scorer: str


def to_time_msg(ns: int) -> TimeMsg:
    ns = max(int(ns), 0)
    return TimeMsg(sec=ns // NS_PER_S, nanosec=ns % NS_PER_S)


class WatcherNode(Node):

    def __init__(self):
        super().__init__('rtm_watcher')
        self.declare_parameter('watch_list', '')
        self.declare_parameter('use_header_stamp', False)
        # Point an existing watch list at another server without editing it
        self.declare_parameter('endpoint', '')
        self.declare_parameter('model', '')
        self.declare_parameter('api_key_env', '')
        path = self.get_parameter('watch_list').value
        if not path:
            raise WatchListError('set the watch_list parameter to a watch list YAML file')
        self.config = load_watch_list(path, {
            name: self.get_parameter(name).value
            for name in ('endpoint', 'model', 'api_key_env')})
        self.use_header_stamp = bool(self.get_parameter('use_header_stamp').value)

        client = OpenAICompatibleClient(
            self.config.endpoint, self.config.model, api_key=self.config.api_key(),
            timeout_s=self.config.request_timeout_s, max_tokens=self.config.max_tokens,
            temperature=self.config.temperature, extra_body=self.config.extra_body)
        self.engine = WatcherEngine(self.config, client)

        reliable = QoSProfile(depth=50, reliability=ReliabilityPolicy.RELIABLE)
        self.trigger_pub = self.create_publisher(
            EventTrigger, self.config.event_trigger_topic, reliable)
        self.status_pub = self.create_publisher(String, '~/status', 10)

        self._subs: Dict[str, object] = {}
        self._last_state_ns: Dict[str, int] = {}
        self._warned: set = set()

        # Cascade: local scorers pin, the VLM judges
        self._scorers: Dict[str, ScorerState] = {}
        for scorer in self.config.scorers:
            gate = ConformalThreshold.fixed(scorer.threshold) if scorer.threshold is not None \
                else ConformalThreshold(alpha=scorer.alpha)
            self._scorers[scorer.topic] = ScorerState(scorer, gate)
        self._judges: List[PendingJudge] = []
        self._judges_lock = threading.Lock()
        ns = self.config.time_machine_ns
        self.pin_client = self.create_client(PinRange, f'{ns}/pin_range')
        self.release_client = self.create_client(ReleasePin, f'{ns}/release_pin')
        self.create_timer(1.0, self._discover)
        self.create_timer(5.0, self._publish_status)
        self._discover()

        self._stop = threading.Event()
        self._worker = threading.Thread(target=self._loop, name='vlm-watcher', daemon=True)
        self._worker.start()

        self.get_logger().info(
            f'Watching {len(self.config.cameras)} camera(s) for '
            f'{", ".join(self.config.labels)} with {self.config.model} at '
            f'{self.config.endpoint} ({self.engine.source_version})')
        if not have_encoder():
            self.get_logger().warn(
                'Neither OpenCV nor Pillow is installed: JPEG CompressedImage topics pass '
                'through at full size and raw Image topics are ignored.')

    # Subscriptions -------------------------------------------------------

    def _discover(self):
        """Subscribe to configured topics once their types are known."""
        types = dict(self.get_topic_names_and_types())
        for topic in self.config.cameras + self.config.state_topics + list(self._scorers):
            if topic in self._subs or topic not in types or not types[topic]:
                continue
            type_name = types[topic][0]
            is_camera = topic in self.config.cameras
            if topic in self._scorers:
                self._subscribe_scorer(topic, type_name)
                continue
            if is_camera and type_name not in IMAGE_TYPES:
                self._warn_once(topic, f'{topic} is {type_name}, not an image type; ignoring')
                continue
            try:
                msg_class = get_message(type_name)
            except (AttributeError, ModuleNotFoundError, ValueError) as e:
                self._warn_once(topic, f'cannot import {type_name} for {topic}: {e}')
                continue
            callback = (lambda msg, t=topic, n=type_name: self._on_image(t, n, msg)) \
                if is_camera else (lambda msg, t=topic, n=type_name: self._on_state(t, n, msg))
            self._subs[topic] = self.create_subscription(
                msg_class, topic, callback, qos_profile_sensor_data)
            self.get_logger().info(
                f'Subscribed to {"camera" if is_camera else "state"} {topic} ({type_name})')

    def _subscribe_scorer(self, topic: str, type_name: str):
        try:
            msg_class = get_message(type_name)
        except (AttributeError, ModuleNotFoundError, ValueError) as e:
            self._warn_once(topic, f'cannot import {type_name} for scorer {topic}: {e}')
            return
        if not hasattr(msg_class(), 'data'):
            self._warn_once(topic, f'scorer {topic} is {type_name}; expected std_msgs Float32/64')
            return
        self._subs[topic] = self.create_subscription(
            msg_class, topic, lambda msg, t=topic: self._on_score(t, float(msg.data)),
            QoSProfile(depth=100))
        gate = self._scorers[topic].gate
        how = f'threshold {gate.threshold:g}' if gate.calibrated else \
            f'calibrating for {self._scorers[topic].config.calibration_s:g}s'
        self.get_logger().info(f'Subscribed to scorer {topic} ({how})')

    def _warn_once(self, key: str, text: str):
        if key not in self._warned:
            self._warned.add(key)
            self.get_logger().warn(text)

    def _stamp_ns(self, msg) -> int:
        # Receipt time on the node's ROS clock matches how Time Machine stamps its buffers
        if self.use_header_stamp and hasattr(msg, 'header'):
            stamp = msg.header.stamp
            ns = stamp.sec * NS_PER_S + stamp.nanosec
            if ns > 0:
                return ns
        return self.get_clock().now().nanoseconds

    def _on_image(self, topic: str, type_name: str, msg):
        max_px = self.config.max_image_px
        if type_name.endswith('CompressedImage'):
            def encode():
                return compressed_to_jpeg(msg.data, max_px)
        else:
            def encode():
                return raw_to_jpeg(msg.encoding, msg.width, msg.height, msg.step, msg.data,
                                   max_px)
        self.engine.offer_frame(topic, self._stamp_ns(msg), encode)

    def _on_state(self, topic: str, type_name: str, msg):
        stamp_ns = self._stamp_ns(msg)
        last = self._last_state_ns.get(topic)
        if last is not None and 0 <= stamp_ns - last < NS_PER_S / STATE_HZ:
            return
        self._last_state_ns[topic] = stamp_ns
        if type_name == 'sensor_msgs/msg/JointState':
            text = summarize_joint_state(msg.name, msg.position, msg.velocity, msg.effort)
        else:
            text = self._generic_summary(msg)
        self.engine.update_state(topic, stamp_ns, text)

    @staticmethod
    def _generic_summary(msg) -> str:
        try:
            from rosidl_runtime_py import message_to_ordereddict
            data = message_to_ordereddict(msg)
            data.pop('header', None)
            return json.dumps(data, default=str, separators=(',', ':'))
        except Exception:  # noqa: B902 - any message must summarize somehow
            return str(msg)

    def _on_score(self, topic: str, score: float):
        state = self._scorers[topic]
        now_ns = self.get_clock().now().nanoseconds
        if not state.gate.calibrated:
            if state.calibration_start_ns == 0 or now_ns < state.calibration_start_ns:
                state.calibration_start_ns = now_ns
            state.gate.observe(score)
            if now_ns - state.calibration_start_ns >= state.config.calibration_s * NS_PER_S:
                if state.gate.finalize():
                    self.get_logger().info(
                        f'Scorer {topic} calibrated: threshold {state.gate.threshold:.4g} '
                        f'(alpha {state.config.alpha:g})')
                else:
                    self._warn_once(
                        f'cal:{topic}',
                        f'Scorer {topic}: {state.gate.samples} samples so far, need '
                        f'{state.gate.required_samples()} for alpha {state.config.alpha:g}')
            return
        if now_ns < state.refractory_until_ns or not state.gate.exceeds(score):
            return
        cfg = state.config
        state.refractory_until_ns = now_ns + int(cfg.post_event * NS_PER_S)
        self._request_pin(topic, now_ns, score, state)

    def _request_pin(self, topic: str, now_ns: int, score: float, state: ScorerState):
        cfg = state.config
        if not self.pin_client.service_is_ready():
            self._warn_once('pin_srv', f'{self.pin_client.srv_name} unavailable; '
                            'scorer spikes are not being pinned')
            return
        start_ns = now_ns - int(cfg.pre_event * NS_PER_S)
        end_ns = now_ns + int(cfg.post_event * NS_PER_S)
        request = PinRange.Request()
        request.start = to_time_msg(start_ns)
        request.end = to_time_msg(end_ns)
        request.profile = cfg.profile
        request.ttl_seconds = float(cfg.ttl_s)
        request.reason = f'scorer:{topic} {score:.4g}>{state.gate.threshold:.4g}'
        future = self.pin_client.call_async(request)

        def done(fut):
            response = fut.result()
            if response is None or not response.success:
                self.get_logger().warn(
                    f'Pin for {topic} refused: {getattr(response, "message", "no response")}')
                return
            self.get_logger().info(f'Pinned {request.reason} as pin {response.pin_id}')
            if cfg.judge:
                with self._judges_lock:
                    self._judges.append(
                        PendingJudge(response.pin_id, start_ns, end_ns, topic))
        future.add_done_callback(done)

    def _next_judge(self):
        """Oldest pin whose window has closed, if any."""
        now_ns = self.get_clock().now().nanoseconds
        with self._judges_lock:
            for i, judge in enumerate(self._judges):
                if judge.end_ns + JUDGE_SETTLE_NS <= now_ns:
                    return self._judges.pop(i)
        return None

    def _judge(self, pending: PendingJudge):
        verdict = self.engine.judge(pending.start_ns, pending.end_ns)
        if verdict.error:
            # No decision: the pin resolves by its profile's timeout policy
            self.get_logger().warn(f'Judge for pin {pending.pin_id} failed: {verdict.error}')
            return
        request = ReleasePin.Request()
        request.pin_id = pending.pin_id
        request.keep = verdict.keep
        request.source = self.engine.source
        if verdict.detection:
            request.label = verdict.detection.label
            request.description = verdict.detection.description
            request.confidence = float(verdict.detection.confidence)
        self.release_client.call_async(request)
        self.get_logger().info(
            f'Pin {pending.pin_id} ({pending.scorer}): '
            + (f'keep as {request.label} ({request.confidence:.2f})' if verdict.keep
               else 'release, nothing listed was seen'))

    # Inference loop ------------------------------------------------------

    def _loop(self):
        while not self._stop.is_set():
            # Pinned windows first: a scorer already said something is happening
            pending = self._next_judge()
            if pending is not None:
                self._judge(pending)
                continue
            if not self.engine.ready():
                self._stop.wait(0.05)
                continue
            result = self.engine.step()
            if result.error:
                self.get_logger().warn(f'VLM call failed: {result.error}',
                                       throttle_duration_sec=10.0)
                self._stop.wait(1.0)  # Back off; frames keep flowing into the window
                continue
            for problem in result.problems:
                self.get_logger().debug(f'VLM output problem: {problem}')
            for trigger in result.triggers:
                self._publish_trigger(trigger)

    def _publish_trigger(self, trigger: TriggerRequest):
        msg = EventTrigger()
        msg.event_stamp = to_time_msg(trigger.event_stamp_ns)
        if trigger.start_ns:
            msg.start = to_time_msg(trigger.start_ns)
        if trigger.end_ns:
            msg.end = to_time_msg(trigger.end_ns)
        msg.profile = trigger.profile
        msg.label = trigger.label
        msg.description = trigger.description
        msg.confidence = float(trigger.confidence)
        msg.source = trigger.source
        msg.source_version = trigger.source_version
        msg.tags = list(trigger.tags)
        self.trigger_pub.publish(msg)
        lag = (self.get_clock().now().nanoseconds - trigger.event_stamp_ns) / NS_PER_S
        self.get_logger().info(
            f'EventTrigger {trigger.label} ({trigger.confidence:.2f}) -> '
            f'{trigger.profile or "default"}: "{trigger.description}" [detected {lag:.1f}s '
            'after it happened]')

    def _publish_status(self):
        stats = self.engine.stats.as_dict()
        stats['model'] = self.config.model
        stats['subscribed'] = sorted(self._subs)
        self.status_pub.publish(String(data=json.dumps(stats)))

    def destroy_node(self):
        self._stop.set()
        self._worker.join(timeout=self.config.request_timeout_s + 1.0)
        return super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    try:
        node = WatcherNode()
    except WatchListError as e:
        rclpy.logging.get_logger('rtm_watcher').error(str(e))
        rclpy.try_shutdown()
        raise SystemExit(1)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
