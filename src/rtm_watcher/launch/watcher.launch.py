"""Launch the VLM watcher with a watch list.

    ros2 launch rtm_watcher watcher.launch.py watch_list:=/path/watch_list.yaml
    # Point the same watch list at a vLLM box on the LAN (deploy/vllm):
    ros2 launch rtm_watcher watcher.launch.py watch_list:=/path/watch_list.yaml \\
        endpoint:=http://192.168.1.50:8000/v1 model:=qwen3-vl-4b-instruct \\
        api_key_env:=VLLM_API_KEY

endpoint, model and api_key_env override the watch list when set; empty means "use
the file". Overrides get the same validation as the file, including the LAN check.
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():
    args = [
        DeclareLaunchArgument(
            'watch_list',
            default_value=PathJoinSubstitution(
                [FindPackageShare('rtm_watcher'), 'config', 'watch_list.example.yaml']),
            description='Watch list YAML (plain-English events to detect)'),
        DeclareLaunchArgument('endpoint', default_value='',
                              description='Override the watch list endpoint'),
        DeclareLaunchArgument('model', default_value='',
                              description='Override the watch list model'),
        DeclareLaunchArgument('api_key_env', default_value='',
                              description='Env var holding the server API key'),
        DeclareLaunchArgument('use_sim_time', default_value='false'),
    ]

    return LaunchDescription(args + [
        Node(
            package='rtm_watcher',
            executable='watcher_node',
            name='rtm_watcher',
            output='screen',
            parameters=[{
                'watch_list': LaunchConfiguration('watch_list'),
                'endpoint': LaunchConfiguration('endpoint'),
                'model': LaunchConfiguration('model'),
                'api_key_env': LaunchConfiguration('api_key_env'),
                'use_sim_time': LaunchConfiguration('use_sim_time'),
            }],
        ),
    ])
