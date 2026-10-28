#!/usr/bin/env python3
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

"""End-to-end test of time-addressed triggers, pins and event.json sidecars."""

import json
import os
import tempfile
import time
import unittest

from builtin_interfaces.msg import Time as TimeMsg
import launch
import launch_ros.actions
import launch_testing.actions
import pytest
import rclpy
from rclpy.node import Node
from ros_time_machine_msgs.msg import EventTrigger, RecordingStatus
from ros_time_machine_msgs.srv import PinRange, ReleasePin, TriggerSnapshot
from std_msgs.msg import Int32

OUTPUT_DIR = tempfile.mkdtemp(prefix='rtm_v2_')

CONFIG = f"""
snapshots:
  - name: "profile_a"
    description: "EventTrigger-only profile"
    recording:
      topics: ["/rtm_test/data"]
      pre_event_duration: 2.0
      post_event_duration: 1.0
      max_buffer_size: 50
    rate_limit:
      max_triggers_per_minute: 60
    output:
      path: "{OUTPUT_DIR}"
      name_pattern: "{{label}}_{{timestamp}}"

global:
  recording_threads: 1
  log_level: "INFO"
  publish_stats: false
  max_trigger_latency: 3.0
  write_settle_time: 0.1
  robot:
    id: "test-bot"
"""


@pytest.mark.launch_test
def generate_test_description():
    config_file = os.path.join(OUTPUT_DIR, 'config.yaml')
    with open(config_file, 'w') as f:
        f.write(CONFIG)
    node = launch_ros.actions.Node(
        package='ros_time_machine', executable='time_machine_node', name='time_machine',
        parameters=[{'config_file': config_file}], output='screen')
    return launch.LaunchDescription([node, launch_testing.actions.ReadyToTest()]), {}


def to_msg(ns):
    return TimeMsg(sec=ns // 1_000_000_000, nanosec=ns % 1_000_000_000)


class TestEventTriggers(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        rclpy.init()
        cls.node = Node('rtm_v2_test')
        cls.data_pub = cls.node.create_publisher(Int32, '/rtm_test/data', 10)
        cls.event_pub = cls.node.create_publisher(
            EventTrigger, '/time_machine/event_trigger', 10)
        cls.statuses = []
        cls.node.create_subscription(
            RecordingStatus, '/time_machine/recording_status', cls.statuses.append, 10)
        cls.counter = 0

    @classmethod
    def tearDownClass(cls):
        cls.node.destroy_node()
        rclpy.shutdown()

    def spin_publishing(self, seconds, hz=50.0):
        """Publish /rtm_test/data while spinning, so the buffer fills."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.counter += 1
            self.data_pub.publish(Int32(data=self.counter))
            rclpy.spin_once(self.node, timeout_sec=1.0 / hz)

    def wait_for_status(self, count, timeout=15.0):
        end = time.monotonic() + timeout
        while len(self.statuses) < count and time.monotonic() < end:
            self.spin_publishing(0.1)
        self.assertGreaterEqual(len(self.statuses), count, 'no clip was reported')
        return self.statuses[count - 1]

    def read_sidecar(self, status):
        self.assertEqual(status.status, RecordingStatus.STATUS_COMPLETED, status.error_message)
        with open(os.path.join(status.output_path, 'event.json')) as f:
            return json.load(f)

    def wait_for_subscriber(self, publisher, timeout=10.0):
        end = time.monotonic() + timeout
        while publisher.get_subscription_count() == 0 and time.monotonic() < end:
            self.spin_publishing(0.1)
        self.assertGreater(publisher.get_subscription_count(), 0)

    def test_1_event_in_the_past_gets_its_window(self):
        self.wait_for_subscriber(self.data_pub)
        self.wait_for_subscriber(self.event_pub)
        self.spin_publishing(4.0)

        # The event happened 1 s ago; the window is [event - 2 s, event + 1 s]
        event_ns = self.node.get_clock().now().nanoseconds - 1_000_000_000
        msg = EventTrigger(
            event_stamp=to_msg(event_ns), profile='profile_a', label='object_dropped',
            description='test drop', confidence=0.8, source='test', tags=['it'])
        self.event_pub.publish(msg)

        sidecar = self.read_sidecar(self.wait_for_status(1))
        self.assertEqual(sidecar['schema'], 'rtm.event/1')
        self.assertEqual(sidecar['trigger']['label'], 'object_dropped')
        self.assertEqual(sidecar['trigger']['source'], 'test')
        self.assertEqual(sidecar['robot']['id'], 'test-bot')
        self.assertEqual(sidecar['window']['start_ns'], event_ns - 2_000_000_000)
        self.assertEqual(sidecar['window']['end_ns'], event_ns + 1_000_000_000)
        self.assertTrue(sidecar['coverage']['pre_event_complete'])
        self.assertGreater(sidecar['trigger']['detection_lag_s'], 0.5)
        self.assertGreater(sidecar['topics'][0]['messages'], 100)  # ~3 s at 50 Hz
        self.assertTrue(os.path.basename(sidecar['clip']).startswith('object_dropped_'))

    def test_2_overlapping_events_merge(self):
        before = len(self.statuses)
        now_ns = self.node.get_clock().now().nanoseconds
        for offset, conf in ((0.5, 0.6), (0.2, 0.95)):
            self.event_pub.publish(EventTrigger(
                event_stamp=to_msg(now_ns - int(offset * 1e9)), profile='profile_a',
                label=f'merge_{conf}', confidence=conf, source='test'))
        sidecar = self.read_sidecar(self.wait_for_status(before + 1))
        self.assertEqual(sidecar['trigger']['merged_from'], 2)
        self.assertEqual(sidecar['trigger']['label'], 'merge_0.95')
        self.spin_publishing(1.0)
        self.assertEqual(len(self.statuses), before + 1)

    def test_3_service_trigger_and_pin_keep(self):
        trigger = self.node.create_client(TriggerSnapshot, '/time_machine/trigger_snapshot')
        self.assertTrue(trigger.wait_for_service(timeout_sec=5.0))
        before = len(self.statuses)
        future = trigger.call_async(TriggerSnapshot.Request(snapshot_name='profile_a'))
        rclpy.spin_until_future_complete(self.node, future, timeout_sec=5.0)
        self.assertTrue(future.result().success, future.result().message)
        self.assertEqual(self.read_sidecar(self.wait_for_status(before + 1))['trigger']['source'],
                         'service')

        pin = self.node.create_client(PinRange, '/time_machine/pin_range')
        release = self.node.create_client(ReleasePin, '/time_machine/release_pin')
        self.assertTrue(pin.wait_for_service(timeout_sec=5.0))
        now_ns = self.node.get_clock().now().nanoseconds
        future = pin.call_async(PinRange.Request(
            start=to_msg(now_ns - 1_500_000_000), end=to_msg(now_ns), profile='profile_a',
            ttl_seconds=30.0, reason='scorer:test'))
        rclpy.spin_until_future_complete(self.node, future, timeout_sec=5.0)
        self.assertTrue(future.result().success)
        pin_id = future.result().pin_id

        self.spin_publishing(1.0)
        future = release.call_async(ReleasePin.Request(
            pin_id=pin_id, keep=True, label='grasp_slip', confidence=0.9, source='judge'))
        rclpy.spin_until_future_complete(self.node, future, timeout_sec=5.0)
        self.assertTrue(future.result().success, future.result().message)

        before = len(self.statuses)
        sidecar = self.read_sidecar(self.wait_for_status(before + 1))
        self.assertEqual(sidecar['trigger']['label'], 'grasp_slip')
        self.assertEqual(sidecar['window']['start_ns'], now_ns - 1_500_000_000)

        # A resolved pin cannot be released twice
        future = release.call_async(ReleasePin.Request(pin_id=pin_id, keep=False))
        rclpy.spin_until_future_complete(self.node, future, timeout_sec=5.0)
        self.assertFalse(future.result().success)
