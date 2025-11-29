# Testing ROS Time Machine with Public Datasets

This guide explains how to test ROS Time Machine using publicly available ROS2 rosbag datasets, including the NVIDIA Isaac R2B dataset with multi-sensor data.

## Quick Start

```bash
# 1. Download a dataset (see options below)
# 2. Create a config file for your dataset
# 3. Run Time Machine with the config
ros2 launch ros_time_machine time_machine.launch.py \
  config_file:=/path/to/your_config.yaml

# 4. In another terminal, play the rosbag
ros2 bag play /path/to/dataset --clock

# 5. In a third terminal, trigger a snapshot
ros2 topic pub -1 /time_machine/manual_trigger std_msgs/msg/Bool "{data: true}"

# 6. Check your recordings
ls /tmp/ros_time_machine/snapshots/
```

## Available Public Datasets

### 1. NVIDIA Isaac R2B 2023 (Recommended)

Multi-sensor dataset with cameras, LiDAR, and IMU from NVIDIA's robotics research.

| Property | Value |
|----------|-------|
| **Size** | 15.5 GB total |
| **Sensors** | D455 RGB/Depth, HAWK stereo, Pandar XT32 LiDAR, IMU |
| **Scenes** | 8 (cafe, datacenter, hallway, hideaway, hope, lounge, mezzanine, storage) |
| **Format** | MCAP rosbag2 |

**Download:**
```bash
# Create datasets directory
mkdir -p datasets/raw && cd datasets/raw

# Download from NVIDIA NGC (requires free account)
# https://catalog.ngc.nvidia.com/orgs/nvidia/teams/isaac/resources/r2bdataset2023

# Or use wget if you have direct links:
wget https://api.ngc.nvidia.com/v2/resources/nvidia/isaac/r2bdataset2023/versions/3/files/r2b_cafe.zip
wget https://api.ngc.nvidia.com/v2/resources/nvidia/isaac/r2bdataset2023/versions/3/files/r2b_datacenter.zip
# ... (other scenes)

# Extract
unzip r2b_cafe.zip
```

**Topics:**
```
/d455_1_rgb_image         - RealSense D455 RGB camera
/d455_1_depth_image       - RealSense D455 depth camera
/d455_1_left_ir_image     - RealSense D455 left IR
/d455_1_right_ir_image    - RealSense D455 right IR
/hawk_0_left_rgb_image    - HAWK stereo left camera
/hawk_0_right_rgb_image   - HAWK stereo right camera
/pandar_xt_32_0_lidar     - Pandar XT32 LiDAR point cloud
/d455_1_imu               - IMU data
```

### 2. AutoCore Ouster OS1-64 LiDAR

High-quality LiDAR dataset from AutoCore.ai.

| Property | Value |
|----------|-------|
| **Size** | ~1 GB |
| **Sensors** | Ouster OS1-64 LiDAR |
| **Scenes** | 2 (trucks, pedestrians/vehicles) |
| **Format** | MCAP rosbag2 |

**Download:**
```bash
mkdir -p datasets/raw && cd datasets/raw

# Available from various ROS2 bag repositories
# Search for "ouster os1-64 rosbag2" or check rosbag.org
```

**Topics:**
```
/sensing/lidar/top/rectified/pointcloud
```

### 3. Intel RealSense D435 Camera

RGB-D camera dataset for indoor testing.

| Property | Value |
|----------|-------|
| **Size** | ~1.8 GB |
| **Sensors** | RealSense D435 RGB-D |
| **Format** | MCAP rosbag2 |

**Topics:**
```
/camera/color/image_raw
/camera/depth/image_rect_raw
/camera/infra1/image_rect_raw
/camera/infra2/image_rect_raw
```

## Configuration Files

### Multi-Sensor Configuration (NVIDIA R2B)

Create `configs/r2b_multi_sensor.yaml`:

```yaml
# Time Machine Configuration for NVIDIA Isaac R2B Multi-Sensor Dataset
# Captures all sensors: 4 cameras, LiDAR, and IMU

snapshots:
  - name: "r2b_multi_sensor_snapshot"
    description: "Multi-sensor snapshot from NVIDIA Isaac R2B dataset"

    trigger:
      topic: "/time_machine/manual_trigger"
      msg_type: "std_msgs/msg/Bool"
      condition:
        field: "data"
        operator: "=="
        value: true

    recording:
      topics:
        # RealSense D455 Camera
        - "/d455_1_rgb_image"
        - "/d455_1_depth_image"
        - "/d455_1_left_ir_image"
        - "/d455_1_right_ir_image"

        # HAWK Stereo Camera
        - "/hawk_0_left_rgb_image"
        - "/hawk_0_right_rgb_image"

        # LiDAR
        - "/pandar_xt_32_0_lidar"

        # IMU
        - "/d455_1_imu"

      pre_event_duration: 2.0   # 2 seconds before trigger
      post_event_duration: 3.0  # 3 seconds after trigger
      max_buffer_size: 1000     # MB

    rate_limit:
      max_triggers_per_minute: 10

    output:
      path: "/tmp/ros_time_machine/snapshots"
      name_pattern: "r2b_{timestamp}"
      format: "mcap"

global:
  max_total_buffer_memory: 2000
  recording_threads: 4
  log_level: "INFO"
```

### LiDAR-Only Configuration

Create `configs/lidar_test.yaml`:

```yaml
# Time Machine Configuration for LiDAR Dataset Testing

snapshots:
  - name: "lidar_snapshot"
    description: "LiDAR point cloud snapshot"

    trigger:
      topic: "/time_machine/manual_trigger"
      msg_type: "std_msgs/msg/Bool"
      condition:
        field: "data"
        operator: "=="
        value: true

    recording:
      topics:
        - "/sensing/lidar/top/rectified/pointcloud"

      pre_event_duration: 3.0
      post_event_duration: 5.0
      max_buffer_size: 500

    rate_limit:
      max_triggers_per_minute: 5

    output:
      path: "/tmp/ros_time_machine/snapshots"
      name_pattern: "lidar_{timestamp}"
      format: "mcap"

global:
  max_total_buffer_memory: 1000
  recording_threads: 2
  log_level: "INFO"
```

### Camera-Only Configuration

Create `configs/camera_test.yaml`:

```yaml
# Time Machine Configuration for Camera Dataset Testing

snapshots:
  - name: "camera_snapshot"
    description: "RGB-D camera snapshot"

    trigger:
      topic: "/time_machine/manual_trigger"
      msg_type: "std_msgs/msg/Bool"
      condition:
        field: "data"
        operator: "=="
        value: true

    recording:
      topics:
        - "/camera/color/image_raw"
        - "/camera/depth/image_rect_raw"

      pre_event_duration: 2.0
      post_event_duration: 3.0
      max_buffer_size: 500

    rate_limit:
      max_triggers_per_minute: 10

    output:
      path: "/tmp/ros_time_machine/snapshots"
      name_pattern: "camera_{timestamp}"
      format: "mcap"

global:
  max_total_buffer_memory: 1000
  recording_threads: 2
  log_level: "INFO"
```

## Step-by-Step Testing

### Test 1: Multi-Sensor Recording (NVIDIA R2B)

```bash
# Terminal 1: Start Time Machine
source install/setup.bash
ros2 launch ros_time_machine time_machine.launch.py \
  config_file:=$(pwd)/configs/r2b_multi_sensor.yaml

# Terminal 2: Play the rosbag with clock (for time sync)
ros2 bag play datasets/raw/r2b_cafe --clock

# Terminal 3: Wait 3-5 seconds for buffer to fill, then trigger
ros2 topic pub -1 /time_machine/manual_trigger std_msgs/msg/Bool "{data: true}"

# Check results
ros2 bag info /tmp/ros_time_machine/snapshots/r2b_*
```

### Test 2: LiDAR Recording

```bash
# Terminal 1: Start Time Machine
ros2 launch ros_time_machine time_machine.launch.py \
  config_file:=$(pwd)/configs/lidar_test.yaml

# Terminal 2: Play LiDAR dataset
ros2 bag play datasets/raw/rosbag2_2022_04_14-ped_vehicle --clock

# Terminal 3: Trigger after buffer fills
ros2 topic pub -1 /time_machine/manual_trigger std_msgs/msg/Bool "{data: true}"
```

### Test 3: Automated Trigger (IMU-based)

Create a config that triggers on high acceleration:

```yaml
snapshots:
  - name: "high_acceleration_event"
    description: "Triggered by high IMU acceleration"

    trigger:
      topic: "/d455_1_imu"
      msg_type: "sensor_msgs/msg/Imu"
      condition:
        field: "linear_acceleration.x"
        operator: ">"
        value: 2.0  # Trigger when accel > 2 m/s²

    recording:
      topics:
        - "/d455_1_rgb_image"
        - "/d455_1_imu"
        - "/pandar_xt_32_0_lidar"

      pre_event_duration: 2.0
      post_event_duration: 5.0

    rate_limit:
      max_triggers_per_minute: 5

    output:
      path: "/tmp/ros_time_machine/snapshots"
      name_pattern: "accel_event_{timestamp}"
```

## Verifying Recordings

### Check Recording Info

```bash
ros2 bag info /tmp/ros_time_machine/snapshots/<snapshot_name>
```

Expected output:
```
Files:             <snapshot_name>_0.mcap
Bag size:          XX.X MiB
Storage id:        mcap
Duration:          5.XXXs
Start:             <timestamp>
End:               <timestamp>
Messages:          XXX
Topic information:
  /d455_1_rgb_image | sensor_msgs/msg/Image | XX messages
  /pandar_xt_32_0_lidar | sensor_msgs/msg/PointCloud2 | XX messages
  ...
```

### Playback Recording

```bash
# Simple playback
ros2 bag play /tmp/ros_time_machine/snapshots/<snapshot_name>

# With RViz visualization
ros2 bag play /tmp/ros_time_machine/snapshots/<snapshot_name> &
rviz2  # Add displays for your topics
```

### Verify Timeline

The recording should contain:
- **Pre-event data**: Messages from `pre_event_duration` seconds before trigger
- **Post-event data**: Messages from `post_event_duration` seconds after trigger

Check the timestamps match your configuration.

## Troubleshooting

### "Topic not available" warnings

This is normal when starting Time Machine before playing the rosbag. The dynamic topic discovery will subscribe once the bag starts playing.

### No messages recorded

1. **Buffer not filled**: Wait longer before triggering (wait for `pre_event_duration` + a few seconds)
2. **Topic name mismatch**: Check exact topic names with `ros2 topic list` while bag is playing
3. **QoS mismatch**: Try adding `--qos-profile-overrides-path` when playing the bag

### Recording too small

- Check that the bag is actually playing: `ros2 topic hz /your_topic`
- Verify topic names match exactly (case-sensitive, including leading `/`)

### High memory usage

- Reduce `pre_event_duration` and `post_event_duration`
- Reduce `max_total_buffer_memory` in global settings
- Record fewer topics

## Tips for Best Results

1. **Always use `--clock`** when playing rosbags to ensure proper time synchronization

2. **Wait for buffer warm-up**: After starting playback, wait at least `pre_event_duration` seconds before triggering

3. **Monitor buffer status**: Enable debug logging to see buffer fill status:
   ```bash
   ros2 launch ros_time_machine time_machine.launch.py \
     config_file:=... log_level:=debug
   ```

4. **Check topic types**: Before creating a config, verify message types:
   ```bash
   ros2 bag info /path/to/dataset
   ```

5. **Start simple**: Test with a single topic first, then add more sensors

## Additional Resources

- [NVIDIA Isaac R2B Dataset](https://catalog.ngc.nvidia.com/orgs/nvidia/teams/isaac/resources/r2bdataset2023)
- [ROS2 Rosbag Documentation](https://docs.ros.org/en/jazzy/Tutorials/Beginner-CLI-Tools/Recording-And-Playing-Back-Data/Recording-And-Playing-Back-Data.html)
- [MCAP Format](https://mcap.dev/)
