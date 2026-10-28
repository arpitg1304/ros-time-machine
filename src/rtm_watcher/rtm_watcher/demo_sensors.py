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

"""Fake robot sensors for the demos: a camera watching a cup and a swinging arm.

scenario:=drop (default) drops the cup every period_s seconds, which a real VLM can
detect; scenario:=static shows one still frame. With truth_csv set, each drop is
appended as `object_dropped,<time_s>` for `review eval`.
"""

import math
import os

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import CompressedImage, JointState

from .demo_scene import DropLoop, StaticScene


class DemoSensors(Node):

    def __init__(self):
        super().__init__('rtm_demo_sensors')
        self.declare_parameter('camera_hz', 15.0)
        self.declare_parameter('joint_hz', 50.0)
        self.declare_parameter('scenario', 'drop')
        self.declare_parameter('period_s', 20.0)
        self.declare_parameter('truth_csv', '')
        scenario = self.get_parameter('scenario').value
        self.scene = DropLoop(float(self.get_parameter('period_s').value)) \
            if scenario == 'drop' else StaticScene()
        self.truth_csv = self.get_parameter('truth_csv').value
        self.gripper = 0.0
        self.camera_pub = self.create_publisher(
            CompressedImage, '/camera/front/image_raw/compressed', 10)
        self.joint_pub = self.create_publisher(JointState, '/joint_states', 10)
        self.create_timer(1.0 / self.get_parameter('camera_hz').value, self._camera)
        self.create_timer(1.0 / self.get_parameter('joint_hz').value, self._joints)
        self.get_logger().info(f'Publishing demo camera ({scenario}) and joint states')

    def _camera(self):
        now = self.get_clock().now()
        state = self.scene.at(now.nanoseconds / 1e9)
        self.gripper = state.gripper
        if state.drop_started:
            t = self.scene.drop_time(state.loop)
            self.get_logger().info(f'Demo: the cup drops now (t={t:.1f})')
            if self.truth_csv:
                os.makedirs(os.path.dirname(self.truth_csv) or '.', exist_ok=True)
                new = not os.path.exists(self.truth_csv)
                with open(self.truth_csv, 'a', encoding='utf-8') as f:
                    f.write(('label,time_s\n' if new else '') + f'object_dropped,{t:.3f}\n')
        msg = CompressedImage()
        msg.header.stamp = now.to_msg()
        msg.header.frame_id = 'camera_front'
        msg.format = 'jpeg'
        msg.data = state.frame
        self.camera_pub.publish(msg)

    def _joints(self):
        t = self.get_clock().now().nanoseconds / 1e9
        msg = JointState()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.name = ['shoulder_pan', 'shoulder_lift', 'elbow_flex', 'gripper']
        msg.position = [0.5 * math.sin(t), 0.3 * math.sin(0.5 * t), 0.8, self.gripper]
        self.joint_pub.publish(msg)


def main(args=None):
    rclpy.init(args=args)
    node = DemoSensors()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
