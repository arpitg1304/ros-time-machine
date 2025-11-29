# ROS Time Machine - System Flow Explained

## TL;DR - How It Works

The ROS Time Machine is like a "DVR for robots" - it continuously buffers sensor data and saves snapshots when interesting events occur.

## The Flow in Simple Terms

### 1. **Startup Phase**
```
Config Manager loads YAML → Validates settings → Initializes all components
```

**What happens:**
- Config Manager reads `example_snapshots.yaml`
- Validates all snapshot definitions (topics, durations, triggers)
- If valid, creates Buffer Manager, Trigger Monitor, and Recorder

### 2. **Normal Operation (Continuous Buffering)**
```
ROS Topics → Buffer Manager → Circular Buffers (always running)
```

**What happens:**
- Buffer Manager subscribes to ALL topics mentioned in ANY snapshot's recording list
- Example: `/camera/image_raw`, `/lidar/points`, `/odom`, `/imu/data`
- Messages continuously fill circular buffers (like a loop - oldest data gets overwritten)
- Buffer size determined by `max(pre_event_duration)` across all snapshots

**Example:**
- If snapshot A needs 5s pre-event and snapshot B needs 3s pre-event
- Buffer keeps at least 5 seconds of data for each topic

### 3. **Event Detection**
```
ROS Topics → Trigger Monitor → Evaluate Conditions → Check Rate Limit
```

**What happens:**
- Trigger Monitor subscribes to trigger topics (e.g., `/emergency_stop`, `/imu/data`)
- For each message, evaluates the condition:
  - Emergency stop: `data == true`
  - High acceleration: `linear_acceleration.x > 5.0`
- If condition matches, checks rate limiting
- If within rate limit, sends trigger to Recorder

### 4. **Recording Process**

#### Step 1: Trigger Received
```
Trigger Monitor → Recorder → Assign Worker Thread
```

#### Step 2: Dump Pre-Event Buffer
```
Worker Thread → Buffer Manager: "Give me last 5s of [/camera, /lidar, /odom]"
Buffer Manager → Worker Thread: Returns messages from (t-5s to t0)
```

**This is the magic** - we already have the data BEFORE the event!

#### Step 3: Write Pre-Event Data
```
Worker Thread → Create rosbag2 file → Write buffered messages
```

**Output:** `/tmp/ros_time_machine/emergency_stop_20241122_143000.mcap`

#### Step 4: Record Post-Event Data
```
Worker Thread → Subscribe to topics → Record for 10s → Write to same rosbag
```

**What happens:**
- Temporarily subscribes to the same topics
- Records live data for `post_event_duration`
- Writes to the same rosbag file

#### Step 5: Finalize
```
Worker Thread → Unsubscribe → Close rosbag → Publish status → Done
```

**What happens:**
- Unsubscribes from topics (no longer needed)
- Closes rosbag file
- Publishes `RecordingStatus` message (COMPLETED)
- Releases worker thread back to pool

### 5. **Simultaneous Recordings**

**Scenario:** Two events trigger at almost the same time
```
Trigger 1 → Worker Thread 1 (recording emergency_stop)
Trigger 2 → Worker Thread 2 (recording high_acceleration)
Both can run simultaneously using thread pool!
```

## Component Responsibilities Summary

| Component | Job | Analogy |
|-----------|-----|---------|
| **Config Manager** | Parse and validate YAML config | Blueprint reader |
| **Buffer Manager** | Maintain circular buffers for all topics | DVR's constant recording |
| **Trigger Monitor** | Watch for events and evaluate conditions | Motion detector |
| **Recorder** | Orchestrate the actual recording | Recording controller |
| **Worker Threads** | Execute individual recording jobs | Assembly line workers |
| **Service Interface** | Allow manual triggers and status queries | Remote control |

## Data Flow Example

### Example: Emergency Stop Event

**Configuration:**
```yaml
- name: "emergency_stop"
  trigger:
    topic: "/emergency_stop"
    condition: data == true
  recording:
    topics: ["/camera/image_raw", "/lidar/points", "/odom"]
    pre_event_duration: 5.0
    post_event_duration: 10.0
```

**Timeline:**
```
t-10s: Camera, lidar, odom messages flowing → stored in circular buffers
t-5s:  Buffers contain 5s of data (ready to capture pre-event)
t-3s:  More data flowing...
t-1s:  More data flowing...
t=0s:  ⚡ /emergency_stop = true
       → Trigger Monitor detects condition
       → Sends trigger to Recorder
       → Worker thread assigned
       → Dumps buffer (t-5s to t=0s) ← Already have this data!
       → Writes to rosbag: emergency_stop_timestamp.mcap
       → Subscribes to /camera, /lidar, /odom
t+1s:  Recording live data...
t+5s:  Recording live data...
t+10s: Post-event duration complete
       → Unsubscribe from topics
       → Close rosbag
       → Status: COMPLETED
```

**Result:**
- Single rosbag file with 15 seconds of data (5s before + 10s after event)
- Filename: `/tmp/ros_time_machine/snapshots/emergency_stop_20241122_143045.mcap`

## Key Features

### 1. Pre-Event Buffering
**Problem:** By the time you realize something interesting happened, the data is gone.

**Solution:** Always keep a buffer of recent data. When event triggers, we already have it!

### 2. Selective Recording
**Problem:** Recording everything wastes storage.

**Solution:** Only record when specific conditions are met.

### 3. Multiple Simultaneous Snapshots
**Problem:** Two events happen at once - miss one?

**Solution:** Thread pool handles multiple recordings in parallel.

### 4. Rate Limiting
**Problem:** Noisy sensor triggers thousands of events.

**Solution:** Configurable rate limiting per snapshot (e.g., max 5 triggers/minute).

### 5. Topic Deduplication
**Problem:** Multiple snapshots record the same topic - waste memory?

**Solution:** Single buffer per topic, shared across all snapshots.

## Manual Triggering

Users can also manually trigger recordings via service:

```bash
ros2 service call /time_machine/trigger_snapshot \
  ros_time_machine_msgs/srv/TriggerSnapshot \
  "{snapshot_name: 'manual_capture'}"
```

This immediately starts a recording using the "manual_capture" snapshot configuration.

## Monitoring

### Buffer Statistics
Published periodically on `/time_machine/buffer_stats`:
- Memory usage per topic
- Buffer utilization (%)
- Dropped messages count
- Oldest/newest message timestamps

### Recording Status
Published on events to `/time_machine/recording_status`:
- TRIGGERED: Recording started
- RECORDING: In progress
- COMPLETED: Successfully saved
- FAILED: Error occurred

## Next Implementation Steps

Current status: ✅ Config Manager complete

Remaining components:
1. **Buffer Manager** - Circular buffer implementation
2. **Trigger Monitor** - Event detection and condition evaluation
3. **Recorder** - Recording orchestration with rosbag2
4. **Service Interface** - ROS services for manual control

Each component builds on the previous, following the flow described above.
