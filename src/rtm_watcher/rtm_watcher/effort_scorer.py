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

"""Built-in scorer: sudden joint effort changes (contacts, collisions, slips).

Publishes std_msgs/Float32 on ~/score at the JointState rate. The score has no
units; the watcher calibrates a threshold from normal operation.
"""

from __future__ import annotations

import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import JointState
from std_msgs.msg import Float32

from .scorers import EffortSpikeScore


class EffortScorerNode(Node):

    def __init__(self):
        super().__init__('effort_scorer')
        self.declare_parameter('joint_states', '/joint_states')
        self.declare_parameter('alpha', 0.05)
        self.score = EffortSpikeScore(alpha=float(self.get_parameter('alpha').value))
        self.pub = self.create_publisher(Float32, '~/score', 50)
        self.create_subscription(
            JointState, self.get_parameter('joint_states').value, self._on_joints,
            qos_profile_sensor_data)

    def _on_joints(self, msg: JointState):
        if msg.effort:
            self.pub.publish(Float32(data=float(self.score.update(msg.name, msg.effort))))


def main(args=None):
    rclpy.init(args=args)
    node = EffortScorerNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
