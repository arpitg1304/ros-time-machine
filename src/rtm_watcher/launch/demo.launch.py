"""End-to-end demo: fake sensors (a cup that drops every 20 s), Time Machine and the watcher.

    # No GPU: a scripted VLM "sees" object_dropped every few calls
    ros2 launch rtm_watcher demo.launch.py

    # A real VLM, e.g. vLLM on this machine or a GPU box on the LAN (deploy/vllm)
    ros2 launch rtm_watcher demo.launch.py vlm:=external \\
        endpoint:=http://127.0.0.1:8000/v1 model:=qwen3-vl-4b-instruct

Clips appear in /tmp/ros_time_machine/demo/ with an event.json each. Every drop is also
logged to /tmp/ros_time_machine/demo/truth.csv for `review eval`.
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import LaunchConfigurationEquals
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():
    args = [
        DeclareLaunchArgument('vlm', default_value='fake', choices=['fake', 'external'],
                              description='fake: scripted VLM; external: endpoint/model below'),
        DeclareLaunchArgument('endpoint', default_value='http://127.0.0.1:8000/v1',
                              description='OpenAI-compatible endpoint (vlm:=external)'),
        DeclareLaunchArgument('model', default_value='qwen3-vl-4b-instruct',
                              description='Served model name (vlm:=external)'),
        DeclareLaunchArgument('api_key_env', default_value='',
                              description='Env var holding the server API key, if it has one'),
        DeclareLaunchArgument('scenario', default_value='drop', choices=['drop', 'static']),
        DeclareLaunchArgument('period_s', default_value='20.0',
                              description='Seconds between drops'),
        DeclareLaunchArgument('truth_csv',
                              default_value='/tmp/ros_time_machine/demo/truth.csv'),
        DeclareLaunchArgument('every', default_value='5',
                              description='Fake VLM reports an event every N calls'),
        DeclareLaunchArgument('delay', default_value='1.5',
                              description='Fake inference latency (s)'),
    ]
    watch_list = PathJoinSubstitution(
        [FindPackageShare('rtm_watcher'), 'config', 'demo_watch_list.yaml'])

    fake_vlm = Node(
        package='rtm_watcher', executable='fake_vlm', name='fake_vlm', output='screen',
        condition=LaunchConfigurationEquals('vlm', 'fake'),
        arguments=['--port', '8765', '--fire', 'object_dropped',
                   '--every', LaunchConfiguration('every'),
                   '--delay', LaunchConfiguration('delay')])

    watcher_fake = Node(
        package='rtm_watcher', executable='watcher_node', name='rtm_watcher', output='screen',
        condition=LaunchConfigurationEquals('vlm', 'fake'),
        parameters=[{'watch_list': watch_list}])

    watcher_external = Node(
        package='rtm_watcher', executable='watcher_node', name='rtm_watcher', output='screen',
        condition=LaunchConfigurationEquals('vlm', 'external'),
        parameters=[{
            'watch_list': watch_list,
            'endpoint': LaunchConfiguration('endpoint'),
            'model': LaunchConfiguration('model'),
            'api_key_env': LaunchConfiguration('api_key_env'),
        }])

    sensors = Node(
        package='rtm_watcher', executable='demo_sensors', name='rtm_demo_sensors',
        parameters=[{
            'scenario': LaunchConfiguration('scenario'),
            'period_s': LaunchConfiguration('period_s'),
            'truth_csv': LaunchConfiguration('truth_csv'),
        }])

    time_machine = Node(
        package='ros_time_machine', executable='time_machine_node', name='time_machine',
        output='screen',
        parameters=[{'config_file': PathJoinSubstitution(
            [FindPackageShare('ros_time_machine'), 'config', 'demo_semantic.yaml'])}])

    return LaunchDescription(
        args + [fake_vlm, sensors, time_machine, watcher_fake, watcher_external])
