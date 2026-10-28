<p align="center">
  <img src="docs/images/banner.svg" alt="ROS Time Machine" width="100%">
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-Apache_2.0-3b82f6.svg" alt="License: Apache 2.0"></a>
  <img src="https://img.shields.io/badge/ROS_2-Jazzy-22a7f0.svg" alt="ROS 2 Jazzy">
  <img src="https://img.shields.io/badge/C%2B%2B-17-a78bfa.svg" alt="C++17">
  <img src="https://img.shields.io/badge/storage-MCAP-10b981.svg" alt="MCAP">
  <img src="https://img.shields.io/badge/REST_API-optional-fb7185.svg" alt="REST API">
</p>

<p align="center">
  <b>A dashcam for your robot.</b> Continuously buffer recent sensor data, and when something
  important happens, automatically save a rosbag with the moments <i>before</i> and <i>after</i> the event.
</p>

---

## Why?

When a robot does something unexpected — a collision, an emergency stop, a weird sensor reading — the
data you *really* want is the few seconds **leading up to it**. But you can't record everything forever.

ROS Time Machine keeps a rolling, in-memory buffer of the last *N* seconds for each topic. The instant a
trigger fires, it stitches that **pre-event** buffer together with **live post-event** recording into a
single MCAP rosbag — capturing the full story of the moment, automatically.

## How it works

<p align="center">
  <img src="docs/images/how-it-works.svg" alt="How ROS Time Machine works: pre-event buffer + trigger + post-event recording" width="100%">
</p>

> **Key idea:** one circular buffer per topic is *always* recording. On a trigger, only the relevant
> slice is extracted and combined with live recording — so you pay memory cost, not disk cost, until it matters.

## Features

| | |
|---|---|
| ⏪ **Pre-event buffering** | Circular buffers continuously hold recent data — capture what happened *before* the trigger |
| ⏺️ **Post-event recording** | Keep recording for a configurable duration *after* the trigger |
| ⚡ **Flexible triggers** | Watch any topic; fire on conditions (`==`, `!=`, `>`, `<`, `>=`, `<=`) over nested message fields |
| 🔍 **Dynamic topic discovery** | Auto-detects and subscribes to topics as they appear — no message-type codegen needed |
| 🚦 **Rate limiting** | Cap recordings per minute/second to avoid runaway disk usage |
| 💾 **MCAP format** | Modern, efficient rosbag2 storage with compression |
| 🧵 **Multi-threaded** | Worker pool handles multiple simultaneous recordings |
| 🌐 **REST API (optional)** | Trigger & list snapshots over HTTP from web apps, mobile, or non-ROS systems |

## New in v2: semantic triggers

Describe the moments you care about in plain English, and a vision-language model watching a
low-rate copy of the cameras fires **time-addressed** triggers. Time Machine saves the window
around the frame where the event was *seen*, not around the moment the model answered, so
detector latency costs nothing.

```yaml
# watch_list.yaml
watcher:
  endpoint: http://localhost:8000/v1        # vLLM, llama.cpp, Ollama, ...
  model: qwen3-vl-4b-instruct
  cameras: [/camera/front/image_raw/compressed]
  events:
    - label: object_dropped
      describe: "An object falls out of the gripper or off the table."
      profile: manipulation
```

| | |
|---|---|
| 🕰️ **Time-addressed triggers** | `EventTrigger` names a moment in robot time; any detector (VLM, scorer, human, REST) can point at the past |
| 👁️ **VLM watcher** | `rtm_watcher` prompts any OpenAI-compatible VLM, validates its JSON, applies confidence, cooldown and sampling |
| 📌 **Pin, then judge** | Cheap scorers pin a window instantly (conformal-calibrated thresholds); the VLM keeps or releases it |
| 🧾 **Self-explaining clips** | Every clip gets an `event.json`: label, caption, confidence, model + prompt version, detection lag, coverage |
| 🧮 **Merging & budgets** | Overlapping triggers become one clip; per-profile rate limits and a clips-per-hour budget |
| ✅ **Review loop** | `review` CLI: mark clips good/bad, precision per label, suggested confidence thresholds |

Try the whole pipeline without a GPU: `ros2 launch rtm_watcher demo.launch.py`.
Run it for real with a local Qwen3-VL sized to your device (Jetson, NVIDIA GPU, Apple Silicon):
`ros2 launch rtm_vlm semantic_stack.launch.py`.

Guides: **[Wiring it into your robot](docs/INTEGRATION_GUIDE.md)** · **[Semantic triggers](docs/SEMANTIC_TRIGGERS.md)** · **[Choosing and running a VLM](docs/VLM_BACKENDS.md)** ·
**[vLLM server on a GPU box](deploy/vllm/README.md)** · **[Testing v2 on Ubuntu](docs/TESTING_V2_ON_UBUNTU.md)**

## Quick start

> **Prerequisites:** ROS 2 Jazzy (Ubuntu 24.04) · a C++17 compiler · rosbag2 with MCAP support

```bash
# 1. Clone & build
git clone https://github.com/arpitg1304/ros-time-machine.git
cd ros-time-machine
colcon build --packages-up-to ros_time_machine
source install/setup.bash

# 2. Launch (uses the bundled example config)
ros2 launch ros_time_machine time_machine.launch.py

# 3. Trigger a snapshot
ros2 topic pub -1 /time_machine/manual_trigger std_msgs/msg/Bool "{data: true}"

# 4. Find your recording
ls /tmp/ros_time_machine/snapshots/
```

To use your own setup, copy and edit the example config:

```bash
cp src/ros_time_machine/config/example_snapshots.yaml my_config.yaml
ros2 launch ros_time_machine time_machine.launch.py config_file:=/path/to/my_config.yaml
```

## Configuration

A snapshot ties together a **trigger**, the **topics** to record, and how much to keep **before/after**:

```yaml
snapshots:
  - name: "emergency_stop"
    trigger:
      topic: "/emergency_stop"
      msg_type: "std_msgs/msg/Bool"
      condition: { field: "data", operator: "==", value: true }
    recording:
      topics: ["/camera/image_raw", "/lidar/points", "/odom"]
      pre_event_duration: 5.0     # seconds kept from the buffer (before)
      post_event_duration: 10.0   # seconds recorded live (after)
    rate_limit:
      max_triggers_per_minute: 3
    output:
      path: "/tmp/ros_time_machine/snapshots"
      name_pattern: "{snapshot_name}_{timestamp}"
      format: "mcap"
```

<details>
<summary><b>📖 Full configuration reference</b></summary>

#### Snapshot
- **name** — unique identifier for this snapshot type
- **description** — human-readable description (optional)

#### `trigger`
- **topic** — ROS topic to monitor
- **msg_type** — full message type, e.g. `std_msgs/msg/Bool`
- **condition** *(optional)* — fire only when this is true
  - **field** — field path in the message (e.g. `data`, `linear_acceleration.x`)
  - **operator** — one of `==`, `!=`, `>`, `<`, `>=`, `<=`
  - **value** — value to compare against

#### `recording`
- **topics** — list of topics to record
- **pre_event_duration** — seconds of buffered data to capture *before* the trigger
- **post_event_duration** — seconds to keep recording *after* the trigger

#### `rate_limit`
- **max_triggers_per_minute** — max triggers allowed per minute
- **max_triggers_per_second** — *(alternative)* max triggers per second
- **window_size** — time window in seconds for rate limiting

#### `output`
- **path** — directory to save recordings
- **name_pattern** — filename pattern (supports `{snapshot_name}` and `{timestamp}`)
- **format** — `mcap` (recommended) or `sqlite3`

</details>

<details>
<summary><b>📋 More example snapshots</b> (high-acceleration detection, multi-camera)</summary>

**High-acceleration detection** — fire when IMU acceleration exceeds a threshold:

```yaml
snapshots:
  - name: "high_accel"
    trigger:
      topic: "/imu/data"
      msg_type: "sensor_msgs/msg/Imu"
      condition: { field: "linear_acceleration.x", operator: ">", value: 5.0 }
    recording:
      topics: ["/camera/image", "/imu/data"]
      pre_event_duration: 3.0
      post_event_duration: 5.0
    rate_limit:
      max_triggers_per_minute: 10
```

**Multi-camera capture** — grab all cameras on a manual trigger:

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

</details>

## Use cases

- 🚗 **Collision detection** — sensor data from 5s before impact to 10s after
- 📈 **Anomaly detection** — record when unusual sensor readings occur
- 🖱️ **Manual capture** — trigger on demand via topic or REST API
- 🛑 **Emergency events** — auto-save when safety systems activate
- 📱 **Remote monitoring** — trigger snapshots from a dashboard or mobile app via REST

## Architecture

A single ROS 2 node runs four cooperating components, all driven by one YAML config:

<p align="center">
  <img src="docs/images/architecture.svg" alt="ROS Time Machine architecture" width="100%">
</p>

For detailed diagrams, sequence flows, and component specs, see **[ARCHITECTURE.md](ARCHITECTURE.md)**.

## REST API bridge (optional)

Trigger and query snapshots over HTTP — ideal for web dashboards, mobile apps, and non-ROS integrations.

<details>
<summary><b>🌐 Setup & endpoints</b></summary>

```bash
# Install deps & build
pip3 install --break-system-packages fastapi uvicorn pydantic
colcon build --packages-up-to ros_time_machine_rest_bridge
source install/setup.bash

# Run (default port 8080)
ros2 launch ros_time_machine_rest_bridge rest_bridge.launch.py        # or: port:=9000
```

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET`  | `/health` | Health check |
| `POST` | `/api/snapshots/trigger` | Trigger a snapshot (optional `reason`, `metadata`) |
| `POST` | `/api/events` | Time-addressed trigger: `label`, `seconds_ago`, optional `profile`, `pre_event`, `post_event` |
| `GET`  | `/api/snapshots` | List all snapshots |
| `GET`  | `/api/snapshots/{id}` | Snapshot details |
| `GET`  | `/docs` | Interactive Swagger UI |

```bash
# Trigger with metadata
curl -X POST http://localhost:8080/api/snapshots/trigger \
  -H "Content-Type: application/json" \
  -d '{"reason": "collision_detected", "metadata": {"severity": "high"}}'
```

A `test_rest_api.sh` script is included to verify the API end-to-end.

</details>

## Working with recordings & sim time

<details>
<summary><b>▶️ Playback, sim time, and viewing snapshots</b></summary>

**Replaying a bag for testing** (use simulated time so buffer timestamps line up):

```bash
# Terminal 1 — play the bag, publishing /clock
ros2 bag play your_bag.mcap --clock

# Terminal 2 — run with sim time
ros2 launch ros_time_machine time_machine.launch.py \
  config_file:=my_config.yaml use_sim_time:=true
```

**Inspecting a snapshot:**

```bash
ros2 bag info /tmp/ros_time_machine/snapshots/emergency_stop_1234567890   # metadata
ros2 bag play /tmp/ros_time_machine/snapshots/emergency_stop_1234567890   # replay
rqt_bag   /tmp/ros_time_machine/snapshots/emergency_stop_1234567890       # GUI
```

</details>

<details>
<summary><b>⚙️ Launch parameters & running without the launch file</b></summary>

```bash
ros2 launch ros_time_machine time_machine.launch.py \
  config_file:=/path/to/config.yaml \
  log_level:=debug \
  use_sim_time:=true
```

| Parameter | Description | Default |
|-----------|-------------|---------|
| `config_file` | Path to YAML configuration | `example_snapshots.yaml` |
| `log_level` | `debug`, `info`, `warn`, `error`, `fatal` | `info` |
| `use_sim_time` | Use simulated time from `/clock` | `false` |

Direct run (no launch file):

```bash
ros2 run ros_time_machine time_machine_node \
  --ros-args -p config_file:=/path/to/config.yaml -p use_sim_time:=true --log-level debug
```

</details>

## Testing with public datasets

Try ROS Time Machine on real sensor data — NVIDIA Isaac R2B, AutoCore Ouster OS1-64, Intel RealSense D435.
See **[docs/TESTING_WITH_DATASETS.md](docs/TESTING_WITH_DATASETS.md)** for download links, sample configs, and step-by-step instructions.

## Troubleshooting

<details>
<summary><b>🔧 Common issues</b></summary>

**No messages being recorded**
1. Check topics are publishing: `ros2 topic list`
2. Verify topic names in config match exactly (case-sensitive)
3. Enable `log_level:=debug` to see buffer status
4. Allow enough `pre_event_duration` for buffer warm-up

**Gaps in recorded data**
- Increase `pre_event_duration` to extend buffer capacity
- Ensure `use_sim_time` is set correctly during bag playback
- Play bags with the `--clock` flag

**High memory usage**
- Reduce `pre_event_duration` / `post_event_duration`
- Record fewer topics
- Use rate limiting to prevent excessive triggers

</details>

## Contributing

Contributions welcome! Please open an [issue](https://github.com/arpitg1304/ros-time-machine/issues) or
[discussion](https://github.com/arpitg1304/ros-time-machine/discussions), or send a pull request.

## License

[Apache 2.0](LICENSE)
