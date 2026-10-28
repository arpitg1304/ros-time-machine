"""Semantic triggers end to end: a local VLM, Time Machine and the watcher.

    ros2 launch rtm_vlm semantic_stack.launch.py
    ros2 launch rtm_vlm semantic_stack.launch.py prefer:=latency
    ros2 launch rtm_vlm semantic_stack.launch.py vlm:=external watch_list:=/path/hosted.yaml

vlm:=local (default) starts the model `vlm recommend` picks for this device under the
name "local-vlm" (first start downloads it). vlm:=external skips it, for a VLM on a LAN
box or a hosted provider named in your watch list.
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import LaunchConfigurationEquals
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():
    args = [
        DeclareLaunchArgument('vlm', default_value='local', choices=['local', 'external']),
        DeclareLaunchArgument('profile', default_value='auto',
                              description='auto, qwen3-vl-2b, qwen3-vl-4b, qwen3-vl-8b, ...'),
        DeclareLaunchArgument('prefer', default_value='balanced',
                              choices=['latency', 'balanced', 'quality']),
        DeclareLaunchArgument('backend', default_value='llamacpp',
                              choices=['llamacpp', 'vllm', 'ollama']),
        DeclareLaunchArgument('port', default_value='8000'),
        DeclareLaunchArgument('watch_list', default_value=PathJoinSubstitution(
            [FindPackageShare('rtm_vlm'), 'config', 'local_watch_list.yaml'])),
        DeclareLaunchArgument('config_file', default_value=PathJoinSubstitution(
            [FindPackageShare('ros_time_machine'), 'config', 'semantic_triggers.yaml'])),
        DeclareLaunchArgument('use_sim_time', default_value='false'),
    ]

    vlm_server = Node(
        package='rtm_vlm', executable='vlm', name='local_vlm', output='screen',
        condition=LaunchConfigurationEquals('vlm', 'local'),
        arguments=['serve', '--profile', LaunchConfiguration('profile'),
                   '--prefer', LaunchConfiguration('prefer'),
                   '--backend', LaunchConfiguration('backend'),
                   '--port', LaunchConfiguration('port'), '--alias', 'local-vlm'])

    time_machine = Node(
        package='ros_time_machine', executable='time_machine_node', name='time_machine',
        output='screen', parameters=[{
            'config_file': LaunchConfiguration('config_file'),
            'use_sim_time': LaunchConfiguration('use_sim_time'),
        }])

    watcher = Node(
        package='rtm_watcher', executable='watcher_node', name='rtm_watcher', output='screen',
        parameters=[{
            'watch_list': LaunchConfiguration('watch_list'),
            'use_sim_time': LaunchConfiguration('use_sim_time'),
        }])

    return LaunchDescription(args + [vlm_server, time_machine, watcher])
