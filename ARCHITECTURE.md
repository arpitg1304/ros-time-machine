# Architecture

> Internals of ROS Time Machine — components, control flow, and the design decisions behind them.
> For usage, see the [README](README.md).

## Overview

ROS Time Machine is a single ROS 2 node composed of four cooperating components, all driven by one YAML
config. Sensor messages stream continuously into in-memory **circular buffers**; a **trigger monitor**
watches for events; and on a match, the **recorder** fuses the buffered *pre-event* slice with live
*post-event* recording into a single MCAP rosbag.

<p align="center">
  <img src="docs/images/architecture.svg" alt="Component architecture" width="100%">
</p>

| Component | Responsibility |
|-----------|----------------|
| **Config Manager** | Parse & validate the YAML config; expose snapshot definitions at runtime |
| **Buffer Manager** | Maintain a thread-safe circular buffer per topic; serve time-ranged slices on demand |
| **Trigger Monitor** | Subscribe to trigger topics, evaluate conditions, enforce rate limits, fire the recorder |
| **Recorder** | Coordinate recording on a worker pool: dump buffer → record live → write rosbag |

## Recording lifecycle

Each trigger drives one recording job through a small state machine. Rate-limited triggers are dropped
early; I/O errors fail the job cleanly and return the system to idle for the next event.

<p align="center">
  <img src="docs/images/lifecycle.svg" alt="Recording lifecycle state machine" width="100%">
</p>

## Component interaction

The sequence below traces a single snapshot end-to-end — from always-on buffering, through trigger
detection, to a finalized rosbag. Note that recording runs on a **worker thread**, so the main node
stays responsive to new triggers and concurrent recordings.

<p align="center">
  <img src="docs/images/sequence.svg" alt="Component interaction sequence" width="100%">
</p>

## Buffer strategy

The buffer is keyed by **topic**, not by snapshot. Buffers are allocated once and shared across every
snapshot that references the topic, so a camera feed used by three snapshots is still buffered only once.
On a trigger, the recorder dumps only the topics that the *firing* snapshot needs.

<p align="center">
  <img src="docs/images/buffer-strategy.svg" alt="Buffer sharing strategy" width="100%">
</p>

**Design decisions:**
- **One buffer per topic** (not per snapshot) — avoids duplicating high-bandwidth data across snapshots.
- **Lock-based thread safety** — per-topic mutex guards concurrent producer (subscription) and consumer (dump).
- **Drop-oldest on overflow** — the buffer is a fixed-size window; oldest messages are evicted first.

## Core components

### 1 · Config Manager

Parses and validates YAML configuration files and exposes snapshot definitions at runtime.

```yaml
snapshots:
  - name: "collision_event"
    trigger:
      topic: "/emergency_stop"
      msg_type: "std_msgs/msg/Bool"
      condition: { field: "data", operator: "==", value: 1.0 }
    recording:
      topics: ["/camera/image_raw", "/lidar/points", "/odom"]
      pre_event_duration: 5.0     # seconds
      post_event_duration: 10.0   # seconds
      max_buffer_size: 1000       # MB
    rate_limit:
      max_triggers_per_minute: 5
    output:
      path: "/data/snapshots"
      name_pattern: "{snapshot_name}_{timestamp}"
      format: "mcap"
```

### 2 · Buffer Manager

Maintains a circular buffer per configured topic.

- Thread-safe circular buffer per topic
- Dynamic topic discovery via generic (serialized) subscriptions — no per-type codegen
- Buffer capacity derived from the configured pre-event duration
- Buffer statistics tracking (message counts, drops)

### 3 · Trigger Monitor

Subscribes to trigger topics and decides when to record.

- Condition evaluation engine: `==`, `!=`, `>`, `<`, `>=`, `<=`
- Support for nested message fields (e.g. `linear_acceleration.x`)
- Per-snapshot rate limiting
- Fires the recorder on a match

### 4 · Recorder

Coordinates recording on trigger events, on a worker thread pool.

- Multi-threaded recording with a worker pool (multiple simultaneous recordings)
- Buffer-dump coordination for pre-event data
- Live post-event recording via temporary subscriptions
- MCAP rosbag2 output

Per recording job:
1. Dump the pre-event buffer slice to the rosbag
2. Subscribe to topics and record live for the post-event duration
3. Close & finalize the rosbag
4. Clean up and publish status

### 5 · Service & topic interface

Runtime control and monitoring.

- **Services:** manual snapshot triggering, status queries, snapshot listing
- **Topics:** recording-status events, buffer statistics

## Technology stack

**ROS 2**
- **Distribution:** Jazzy Jalisco (Ubuntu 24.04)
- **Build system:** colcon with `ament_cmake`
- **Language:** C++17 (core) · Python (REST bridge)
- **Key dependencies:** `rclcpp`, `rosbag2_cpp`, `std_msgs`, `sensor_msgs`, `yaml-cpp`

**Testing**
- GTest for C++ unit tests
- `launch_testing` for ROS 2 integration tests

## Performance considerations

**Memory**
- Buffer size is a function of topic bandwidth × pre-event duration.
  *Example:* a 10 Hz image topic at ~1 MB/msg with a 5 s buffer ≈ 50 MB.
- Configurable memory limits with drop-oldest degradation.

**Threading**
- Buffer updates guarded by per-topic mutex locks.
- Recording handled by a configurable worker pool (default: 4) — trigger handling stays non-blocking.
- Async I/O for disk writes.

**I/O**
- Worker pool sized to available I/O capacity.
- Buffered writes minimize syscalls; MCAP format for efficient storage.
- Queue management prevents memory overflow under sustained triggering.
