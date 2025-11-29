# ROS Time Machine

**Event-triggered recording system for ROS 2 that captures sensor data snapshots with pre-event buffering.**

Capture the moments that matter by recording data *before* and *after* specific events occur. Perfect for debugging, data collection, and critical event analysis in robotics applications.

## Features

- **Pre-Event Buffering**: Circular buffers continuously store recent data, capturing what happened *before* a trigger
- **Post-Event Recording**: Continue recording for a specified duration after the trigger
- **Dynamic Topic Discovery**: Automatically detects and subscribes to topics as they become available
- **Flexible Triggers**: Monitor any ROS topic and trigger on custom conditions
- **Rate Limiting**: Prevent excessive recordings with configurable trigger limits
- **MCAP Format**: Modern, efficient rosbag2 storage with compression support
- **Multi-threaded**: Handle multiple simultaneous recordings efficiently
- **REST API Bridge**: Optional HTTP API for triggering snapshots from web apps, mobile devices, or non-ROS systems

## Use Cases

- **Collision Detection**: Capture sensor data from 5 seconds before impact to 10 seconds after
- **Anomaly Detection**: Record when unusual sensor readings occur
- **Manual Capture**: Trigger recordings on demand via topic or REST API
- **Emergency Events**: Automatically save data when safety systems activate
- **Remote Monitoring**: Trigger snapshots from a web dashboard or mobile app using the REST API

## Quick Start

### Prerequisites

- **ROS 2 Jazzy** (Ubuntu 24.04)
- **C++17** compatible compiler
- **rosbag2** with MCAP support

### Installation

```bash
# Clone the repository
git clone https://github.com/arpitg1304/ros_time_machine.git
cd ros_time_machine

# Build the package
colcon build --packages-select ros_time_machine

# Source the workspace
source install/setup.bash
```

### Basic Usage

1. **Create a configuration file** (or use the example):

```bash
# Copy the example config
cp src/ros_time_machine/config/example_snapshots.yaml my_config.yaml

# Edit to match your topics and trigger conditions
nano my_config.yaml
```

2. **Launch the Time Machine node**:

```bash
# With default config
ros2 launch ros_time_machine time_machine.launch.py

# With custom config
ros2 launch ros_time_machine time_machine.launch.py \
  config_file:=/path/to/my_config.yaml

# With debug logging
ros2 launch ros_time_machine time_machine.launch.py \
  config_file:=/path/to/my_config.yaml \
  log_level:=debug
```

3. **Trigger a recording**:

```bash
# Manual trigger (requires manual_capture snapshot in config)
ros2 topic pub -1 /time_machine/manual_trigger std_msgs/msg/Bool "{data: true}"

# Or let automatic triggers activate based on your conditions
```

4. **Find your recordings**:

```bash
# Default location
ls /tmp/ros_time_machine/snapshots/

# Or your configured output path
```

## Configuration Guide

The Time Machine uses YAML configuration files to define snapshots, triggers, and recording parameters.

### Basic Configuration Structure

```yaml
snapshots:
  - name: "my_snapshot"
    description: "Description of what this captures"

    trigger:
      topic: "/trigger/topic"
      msg_type: "std_msgs/msg/Bool"
      condition:
        field: "data"
        operator: "=="
        value: true

    recording:
      topics:
        - "/camera/image_raw"
        - "/lidar/points"
      pre_event_duration: 5.0   # seconds before trigger
      post_event_duration: 10.0  # seconds after trigger

    rate_limit:
      max_triggers_per_minute: 3

    output:
      path: "/tmp/ros_time_machine/snapshots"
      name_pattern: "{snapshot_name}_{timestamp}"
      format: "mcap"
```

### Configuration Fields

#### Snapshot Definition

- **name**: Unique identifier for this snapshot type
- **description**: Human-readable description

#### Trigger Configuration

- **topic**: ROS topic to monitor
- **msg_type**: Full message type (e.g., "std_msgs/msg/Bool")
- **condition**: (Optional) Condition to evaluate
  - **field**: Field path in message (e.g., "data" or "linear_acceleration.x")
  - **operator**: Comparison operator (`==`, `!=`, `>`, `<`, `>=`, `<=`)
  - **value**: Value to compare against

#### Recording Configuration

- **topics**: List of topics to record
- **pre_event_duration**: Seconds of data to capture before trigger
- **post_event_duration**: Seconds to continue recording after trigger

#### Rate Limiting

- **max_triggers_per_minute**: Maximum number of triggers allowed per minute
- **max_triggers_per_second**: (Alternative) Maximum triggers per second
- **window_size**: Time window in seconds for rate limiting

#### Output Configuration

- **path**: Directory to save recordings
- **name_pattern**: Filename pattern (supports `{snapshot_name}` and `{timestamp}`)
- **format**: Storage format ("mcap" recommended, or "sqlite3")

### Example Configurations

#### Manual Trigger

```yaml
snapshots:
  - name: "manual_capture"
    trigger:
      topic: "/time_machine/manual_trigger"
      msg_type: "std_msgs/msg/Bool"
      condition:
        field: "data"
        operator: "=="
        value: true

    recording:
      topics:
        - "/camera/image"
        - "/odom"
      pre_event_duration: 2.0
      post_event_duration: 3.0

    output:
      path: "/tmp/snapshots"
```

#### High Acceleration Detection

```yaml
snapshots:
  - name: "high_accel"
    trigger:
      topic: "/imu/data"
      msg_type: "sensor_msgs/msg/Imu"
      condition:
        field: "linear_acceleration.x"
        operator: ">"
        value: 5.0

    recording:
      topics:
        - "/camera/image"
        - "/imu/data"
      pre_event_duration: 3.0
      post_event_duration: 5.0

    rate_limit:
      max_triggers_per_minute: 10
```

#### Multi-Camera Recording

```yaml
snapshots:
  - name: "multi_camera"
    trigger:
      topic: "/capture_trigger"
      msg_type: "std_msgs/msg/Bool"

    recording:
      topics:
        - "/camera/front/image"
        - "/camera/rear/image"
        - "/camera/left/image"
        - "/camera/right/image"
      pre_event_duration: 1.0
      post_event_duration: 2.0

    output:
      path: "/data/recordings"
      format: "mcap"
```

## Working with Simulated Time

When playing back rosbag files for testing, use simulated time:

```bash
# Terminal 1: Play bag with clock
ros2 bag play your_bag.mcap --clock

# Terminal 2: Launch Time Machine with simulated time
ros2 launch ros_time_machine time_machine.launch.py \
  config_file:=my_config.yaml \
  use_sim_time:=true
```

## Viewing Recorded Snapshots

```bash
# Get info about a recording
ros2 bag info /tmp/ros_time_machine/snapshots/manual_1234567890

# Play back a recording
ros2 bag play /tmp/ros_time_machine/snapshots/manual_1234567890

# View with rqt_bag (GUI)
rqt_bag /tmp/ros_time_machine/snapshots/manual_1234567890
```

## REST API Bridge (Optional)

The REST API bridge allows you to trigger snapshots via HTTP requests without requiring ROS tools. This is perfect for:
- Web dashboards and mobile apps
- Remote operation and monitoring
- Integration with non-ROS systems
- Cloud-based control interfaces

### Installation

The REST bridge requires FastAPI dependencies:

```bash
# Install FastAPI dependencies
pip3 install --break-system-packages fastapi uvicorn pydantic

# Build the REST bridge package
colcon build --packages-select ros_time_machine_rest_bridge
source install/setup.bash
```

### Usage

#### Start the REST API Bridge

```bash
# Using launch file (recommended)
ros2 launch ros_time_machine_rest_bridge rest_bridge.launch.py

# With custom port
ros2 launch ros_time_machine_rest_bridge rest_bridge.launch.py port:=9000

# Direct run
ros2 run ros_time_machine_rest_bridge rest_bridge_node
```

The API will be available at `http://localhost:8080` by default.

#### API Endpoints

**Health Check**
```bash
curl http://localhost:8080/health
```

**Trigger a Snapshot**
```bash
# Simple trigger
curl -X POST http://localhost:8080/api/snapshots/trigger \
  -H "Content-Type: application/json" \
  -d '{"reason": "manual_api_trigger"}'

# With metadata
curl -X POST http://localhost:8080/api/snapshots/trigger \
  -H "Content-Type: application/json" \
  -d '{"reason": "collision_detected", "metadata": {"severity": "high", "location": "intersection_5"}}'
```

**List All Snapshots**
```bash
curl http://localhost:8080/api/snapshots
```

**Get Snapshot Details**
```bash
curl http://localhost:8080/api/snapshots/{snapshot_id}
```

**Interactive API Documentation**

Visit `http://localhost:8080/docs` in your browser for interactive Swagger UI documentation.

### Testing the REST API

A test script is provided to verify the REST API functionality:

```bash
bash test_rest_api.sh
```

## Advanced Usage

### Launch File Parameters

```python
ros2 launch ros_time_machine time_machine.launch.py \
  config_file:=/path/to/config.yaml \
  log_level:=debug \
  use_sim_time:=true
```

Available parameters:
- `config_file`: Path to YAML configuration (default: example_snapshots.yaml)
- `log_level`: Logging level (debug, info, warn, error, fatal)
- `use_sim_time`: Use simulated time from /clock topic (default: false)

### Running Without Launch File

```bash
ros2 run ros_time_machine time_machine_node \
  --ros-args \
  -p config_file:=/path/to/config.yaml \
  -p use_sim_time:=true \
  --log-level debug
```

## Troubleshooting

### No messages being recorded

1. Check that topics are publishing: `ros2 topic list`
2. Verify topic names in config match exactly (case-sensitive)
3. Enable debug logging to see buffer status
4. Ensure sufficient pre-event duration for buffer warm-up

### Gaps in recorded data

- Increase buffer capacity by extending `pre_event_duration`
- Check that `use_sim_time` is set correctly when using bag playback
- Verify rosbag is played with `--clock` flag

### High memory usage

- Reduce `pre_event_duration` and `post_event_duration`
- Limit number of topics being recorded
- Use rate limiting to prevent excessive triggers

## Architecture

```
┌─────────────────┐
│  Config Manager │  Loads and validates YAML config
└────────┬────────┘
         │
┌────────▼────────┐
│ Buffer Manager  │  Circular buffers for each topic
│                 │  • Dynamic topic discovery
│                 │  • Time-based retrieval
└────────┬────────┘
         │
┌────────▼────────┐
│ Trigger Monitor │  Monitors topics for conditions
│                 │  • Condition evaluation
│                 │  • Rate limiting
└────────┬────────┘
         │
┌────────▼────────┐
│    Recorder     │  Writes snapshots to disk
│                 │  • Pre + post event data
│                 │  • MCAP format
│                 │  • Multi-threaded
└─────────────────┘
```

For detailed architecture diagrams and component specifications, see [ARCHITECTURE.md](ARCHITECTURE.md).

## License

Apache 2.0

## Contributing

Contributions welcome! Please open an issue or pull request.

## Support

- **Issues**: https://github.com/arpitg1304/ros_time_machine/issues
- **Discussions**: https://github.com/arpitg1304/ros_time_machine/discussions
