#!/usr/bin/env python3
"""
Launch file for ROS Time Machine REST API Bridge

This launch file starts the REST API bridge node that provides HTTP endpoints
for triggering Time Machine snapshots without requiring ROS tools.
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    """Generate launch description for REST API bridge"""

    # Declare launch arguments
    host_arg = DeclareLaunchArgument(
        'host',
        default_value='0.0.0.0',
        description='Host address for REST API server'
    )

    port_arg = DeclareLaunchArgument(
        'port',
        default_value='8080',
        description='Port for REST API server'
    )

    trigger_topic_arg = DeclareLaunchArgument(
        'trigger_topic',
        default_value='/time_machine/manual_trigger',
        description='Topic to publish trigger messages to'
    )

    snapshot_dir_arg = DeclareLaunchArgument(
        'snapshot_dir',
        default_value='/tmp/ros_time_machine/snapshots',
        description='Directory where snapshots are stored'
    )

    # REST Bridge Node
    rest_bridge_node = Node(
        package='ros_time_machine_rest_bridge',
        executable='rest_bridge_node',
        name='rest_bridge_node',
        output='screen',
        parameters=[{
            'host': LaunchConfiguration('host'),
            'port': LaunchConfiguration('port'),
            'trigger_topic': LaunchConfiguration('trigger_topic'),
            'snapshot_dir': LaunchConfiguration('snapshot_dir'),
        }]
    )

    return LaunchDescription([
        host_arg,
        port_arg,
        trigger_topic_arg,
        snapshot_dir_arg,
        rest_bridge_node,
    ])
