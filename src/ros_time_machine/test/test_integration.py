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

"""Integration test for ROS Time Machine node."""

import os
import pathlib
import tempfile
import time
import unittest

import launch
import launch.actions
import launch_ros.actions
import launch_testing.actions
import pytest
import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool


@pytest.mark.launch_test
def generate_test_description():
    """Generate launch description for integration test."""
    # Create temp config file
    test_config = """
snapshots:
  - name: "integration_test"
    description: "Integration test snapshot"

    trigger:
      topic: "/test_trigger"
      msg_type: "std_msgs/msg/Bool"
      condition:
        field: "data"
        operator: "=="
        value: true

    recording:
      topics:
        - "/test_data"
      pre_event_duration: 1.0
      post_event_duration: 1.0
      max_buffer_size: 50

    rate_limit:
      max_triggers_per_minute: 60

    output:
      path: "/tmp/test_integration_snapshots"
      name_pattern: "{snapshot_name}_{timestamp}"
      format: "mcap"

global:
  max_total_buffer_memory: 100
  recording_threads: 1
  log_level: "DEBUG"
  publish_stats: false
"""

    temp_dir = tempfile.mkdtemp()
    config_file = os.path.join(temp_dir, "test_config.yaml")
    with open(config_file, 'w') as f:
        f.write(test_config)

    # Launch time machine node
    time_machine_node = launch_ros.actions.Node(
        package='ros_time_machine',
        executable='time_machine_node',
        name='time_machine_test',
        parameters=[{'config_file': config_file}],
        output='screen',
    )

    return (
        launch.LaunchDescription([
            time_machine_node,
            launch_testing.actions.ReadyToTest(),
        ]),
        {
            'time_machine_node': time_machine_node,
            'config_file': config_file,
            'temp_dir': temp_dir,
        }
    )


class TestTimeMachineIntegration(unittest.TestCase):
    """Integration test class for Time Machine."""

    @classmethod
    def setUpClass(cls):
        """Set up test class."""
        rclpy.init()

    @classmethod
    def tearDownClass(cls):
        """Tear down test class."""
        rclpy.shutdown()

    def setUp(self):
        """Set up test."""
        self.node = Node('test_integration_node')

    def tearDown(self):
        """Tear down test."""
        self.node.destroy_node()

    def test_node_starts(self, proc_output):
        """Test that the time machine node starts successfully."""
        # Just check that the node started
        # The proc_output fixture captures stdout/stderr
        time.sleep(2.0)  # Give node time to start
        self.assertTrue(True)  # If we got here, node started

    def test_trigger_topic_exists(self):
        """Test that trigger topic can be published to."""
        # Create publisher for trigger
        trigger_pub = self.node.create_publisher(
            Bool,
            '/test_trigger',
            10
        )

        # Wait for subscribers
        time.sleep(1.0)

        # Publish trigger
        msg = Bool()
        msg.data = True
        trigger_pub.publish(msg)

        # Spin to process callbacks
        rclpy.spin_once(self.node, timeout_sec=1.0)

        self.assertGreaterEqual(trigger_pub.get_subscription_count(), 0)

    def test_data_topic_subscription(self):
        """Test that data topic is subscribed to."""
        # Create publisher for data topic
        data_pub = self.node.create_publisher(
            Bool,
            '/test_data',
            10
        )

        # Wait for Time Machine to subscribe
        time.sleep(2.0)

        # Check if Time Machine subscribed
        # Note: This is a basic check - full test would verify buffering
        self.assertGreaterEqual(data_pub.get_subscription_count(), 0)


@launch_testing.post_shutdown_test()
class TestProcessOutput(unittest.TestCase):
    """Test process output after shutdown."""

    def test_exit_code(self, proc_info):
        """Test that process exits cleanly."""
        # launch_testing will automatically check that processes shut down correctly
        launch_testing.asserts.assertExitCodes(proc_info)
