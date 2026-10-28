# Wiring ROS Time Machine into your robot

This guide takes you from a robot with cameras to saved, labelled clips of the moments you
care about, measured against ground truth on your own data. It is written for a robotics
team adding semantic triggers to an existing ROS 2 robot; no prior knowledge of this repo
is assumed.

Work through it in order. Every stage ends with a check, so a problem shows up in the stage
that caused it.

| Stage | You end up with | GPU needed |
|---|---|---|
| [1. Choose where things run](#1-choose-where-things-run) | A topology for your site | No |
| [2. Install on the robot](#2-install-on-the-robot) | A working build | No |
| [3. Inventory your topics](#3-inventory-your-topics) | The topics to buffer and to watch | No |
| [4. Configure Time Machine](#4-configure-time-machine) | Profiles: what to save, how far around the event | No |
| [5. Write the watch list](#5-write-the-watch-list) | Your events, in plain English | No |
| [6. Test the plumbing with the fake VLM](#6-test-the-plumbing-with-the-fake-vlm) | Clips from your own topics | No |
| [7. Stand up a real VLM](#7-stand-up-a-real-vlm) | An endpoint, and the latency it adds | Yes |
| [8. Go live](#8-go-live) | Real detections on the robot | Yes |
| [9. Measure on your own data](#9-measure-on-your-own-data) | Recall, lag and coverage on labelled bags | Yes |
| [10. Tune for recall](#10-tune-for-recall) | Settings that miss as little as possible | Yes |
| [11. Run it day to day](#11-run-it-day-to-day) | Autostart, monitoring, review, clips off the robot | Yes |

**How it works, in two sentences.** Time Machine keeps a rolling in-memory buffer of your
topics at full rate. A watcher shows a vision-language model (VLM) one frame per second
from your cameras; when the model reports one of your events, the watcher sends Time
Machine the time the event *happened*, and Time Machine saves the buffered seconds around
it as an MCAP bag plus an `event.json` explaining why. For the design, see
[Semantic triggers](SEMANTIC_TRIGGERS.md).

## 1. Choose where things run

There are three processes: **Time Machine** (`time_machine_node`), the **watcher**
(`rtm_watcher`), and the **VLM server**. Time Machine and the watcher always run on the
robot, next to the cameras. Only the VLM server can move.

| Setup | VLM runs on | Use it when |
|---|---|---|
| **A. Robot + GPU box on the LAN** (recommended) | An NVIDIA GPU machine (16 GB+) on the robot's network, via the [vLLM kit](../deploy/vllm/README.md) | Real robots; one GPU box can serve several robots |
| **B. All on the robot** | The robot's own GPU: Jetson Orin, an onboard NVIDIA GPU, Apple Silicon, via `rtm_vlm` | Frames must never leave the robot, or there's no reliable network |
| **C. All on one dev machine** | The same Ubuntu GPU machine that replays bags | Development, and measuring on recorded data (stage 9) |
| **D. Hosted API** | Gemini, OpenAI, DeepInfra, ... | No GPU anywhere, and camera frames may leave the site |

```
┌────────────── robot ───────────────┐              ┌──── GPU box ────┐
│ cameras ─► time_machine_node       │              │                 │
│        └─► rtm_watcher ────────────┼── HTTP ─────►│ vLLM            │
│            (1 fps, ~4 × 40 KB JPEG)│◄── JSON ─────│ (no ROS needed) │
└────────────────────────────────────┘              └─────────────────┘
```

In setup A, only the sampled frames cross the network, about 1–2 Mbit/s at 1 Hz. The GPU
box doesn't need ROS. Timestamps never leave the robot, so the two clocks don't have to
agree.

Don't run the watcher on a different machine from the cameras. Full-rate camera topics over
DDS saturate Wi-Fi, and the two nodes would stamp frames with different clocks.

[Choosing and running a VLM](VLM_BACKENDS.md) has the model to use for each device.

## 2. Install on the robot

**Requirements:** Ubuntu 24.04 with ROS 2 Jazzy (22.04 with Humble also works), and
rosbag2 with the MCAP plugin.

```bash
mkdir -p ~/rtm_ws/src && cd ~/rtm_ws/src
git clone https://github.com/arpitg1304/ros-time-machine.git
cd ~/rtm_ws
source /opt/ros/$ROS_DISTRO/setup.bash
sudo apt install ros-dev-tools python3-pil python3-yaml python3-pytest
sudo rosdep init 2>/dev/null; rosdep update
rosdep install --from-paths src --ignore-src -y
colcon build --symlink-install --cmake-args -DPython3_EXECUTABLE=/usr/bin/python3
source install/setup.bash
```

**If you use pyenv, conda or uv,** make sure ROS sees the system Python. ROS Jazzy is built
for the system's Python 3.12. If another interpreter comes first on `PATH`, `rclpy` fails
to import, or CMake picks a different interpreter and the message build fails with
`No module named 'em'`. The `-DPython3_EXECUTABLE` flag above fixes the build. For running
nodes and tests:

```bash
pyenv local system               # pyenv: in the workspace directory
conda deactivate                 # conda
python3 -c "import rclpy"        # must succeed with ROS sourced
```

**Check:** all five packages build: `ros_time_machine_msgs`, `ros_time_machine`,
`ros_time_machine_rest_bridge`, `rtm_watcher`, `rtm_vlm`. The full test suite is in
[Testing v2 on Ubuntu](TESTING_V2_ON_UBUNTU.md#3-tests).

## 3. Inventory your topics

With the robot running:

```bash
ros2 topic list -t
ros2 topic hz /your/camera/image_raw/compressed
ros2 topic bw /your/camera/image_raw/compressed
```

Make three lists:

1. **Topics to save in a clip.** Cameras, joint states, gripper and force/torque, the
   policy's commands: whatever you'd want when looking back at a failure.
2. **Cameras to watch.** One or two views that show the events. A wide scene camera usually
   beats a wrist camera for "something fell"; a wrist camera is better for grasp quality.
   `sensor_msgs/CompressedImage` and `sensor_msgs/Image` both work.
3. **State to describe to the model.** A few small topics summarized as text, such as
   `/joint_states` or a gripper state. Any message type works.

**Buffer compressed camera topics wherever you can.** 15 s of 640×480 RGB at 30 fps is
about 420 MB raw, versus 15–30 MB as JPEG. If your driver only publishes raw images, add
`image_transport republish` or enable the driver's compressed output.

## 4. Configure Time Machine

A **profile** says which topics to save, how many seconds before and after the event, and
where to write. Start from this template. The full annotated example is
[`semantic_triggers.yaml`](../src/ros_time_machine/config/semantic_triggers.yaml).

```yaml
# ~/rtm_config/time_machine.yaml
snapshots:
  - name: "manipulation"                    # no trigger block: fired by the watcher
    recording:
      topics:
        - "/your/scene_camera/image_raw/compressed"
        - "/your/wrist_camera/image_raw/compressed"
        - "/joint_states"
      pre_event_duration: 5.0               # seconds saved before the event
      post_event_duration: 3.0              # seconds saved after it
      max_buffer_size: 500                  # MB per topic
    rate_limit:
      max_triggers_per_minute: 6
    output:
      path: "/var/lib/rtm/clips"            # persistent disk, not /tmp
      name_pattern: "{label}_{timestamp}"
      format: "mcap"

  # Optional: keep your existing rule triggers; they share the same queue
  - name: "safety"
    trigger:
      topic: "/emergency_stop"
      msg_type: "std_msgs/msg/Bool"
      condition: { field: "data", operator: "==", value: true }
    recording:
      topics: ["/your/scene_camera/image_raw/compressed", "/joint_states"]
      pre_event_duration: 10.0
      post_event_duration: 5.0
    output:
      path: "/var/lib/rtm/clips"
      name_pattern: "{label}_{timestamp}"

global:
  max_total_buffer_memory: 4000             # MB across all buffers
  event_trigger_topic: "/time_machine/event_trigger"
  max_trigger_latency: 7.0                  # set from check.sh in stage 7
  budget:
    max_clips_per_hour: 120
    merge_gap: 1.0                          # one clip for events closer than this (s)
  robot:                                    # copied into every event.json
    id: "robot-01"
    policy_version: "your-policy-v1"
```

**Sizing.** Each topic keeps `pre_event_duration + post_event_duration +
max_trigger_latency` seconds, using the largest value from any profile that records it.
`max_trigger_latency` absorbs the model's detection lag, so the seconds before an event are
still in the buffer when a late answer arrives. Check memory with
`ros2 topic echo /time_machine/buffer_stats` once it's running.

**Check:** `ros2 launch ros_time_machine time_machine.launch.py
config_file:=$HOME/rtm_config/time_machine.yaml` prints every profile and `Listening for
EventTrigger on /time_machine/event_trigger`, with no "not currently available" warnings
for your topics once the robot is publishing.

## 5. Write the watch list

The watch list describes your events in plain English and says where the model is. Start
with two to four events. The full annotated example is
[`watch_list.example.yaml`](../src/rtm_watcher/config/watch_list.example.yaml).

```yaml
# ~/rtm_config/watch_list.yaml
watcher:
  endpoint: http://192.168.1.50:8000/v1     # set in stage 7
  model: qwen3-vl-4b-instruct
  # api_key_env: VLLM_API_KEY               # name of the env var holding the key
  request_timeout_s: 30                     # the first call after a model starts is slow
  cameras: [/your/scene_camera/image_raw/compressed]
  sample_hz: 1.0
  context_frames: 4                         # per camera
  state_topics: [/joint_states]
  robot_context: "A UR5 arm with a suction gripper picks boxes from a tote onto a conveyor."
  min_confidence: 0.5                       # low: recall first (stage 10)
  cooldown_s: 5                             # short: a false alarm mustn't hide a real event
  events:
    - label: object_dropped
      describe: "A box falls off the suction cup or off the conveyor."
      profile: manipulation
    - label: missed_pick
      describe: "The gripper rises from the tote without a box attached."
      profile: manipulation
```

**Writing good events:**

- **Describe what the camera sees**, not what the robot intended. Write "A box falls off the
  suction cup", not "Grasp failure".
- **Make each event specific.** Name the object, the place and the motion. Vague events
  ("something goes wrong") fire constantly and mean little.
- **Set the scene once in `robot_context`.** One sentence about the robot, the gripper and the
  task gives the model what it needs to read every frame.
- **Use stable `label`s** in `snake_case`. They become clip names, and the review tools group
  by them.
- **Point each event at a profile.** Each profile sets its own window; per-event `pre_event` /
  `post_event` override it, for example 20 s before a `task_success` to capture the whole
  episode.

## 6. Test the plumbing with the fake VLM

Before involving a model, check that your topics flow end to end. The fake VLM is a local
OpenAI-compatible server that reports your label on every Nth request. Run each command in
its own terminal, with the workspace sourced:

```bash
# 1. Time Machine with your config
ros2 launch ros_time_machine time_machine.launch.py \
    config_file:=$HOME/rtm_config/time_machine.yaml

# 2. Fake VLM: reports object_dropped on every 5th request
ros2 run rtm_watcher fake_vlm --port 8765 --fire object_dropped --every 5 --delay 1.5

# 3. The watcher, with your watch list pointed at the fake VLM
ros2 launch rtm_watcher watcher.launch.py watch_list:=$HOME/rtm_config/watch_list.yaml \
    endpoint:=http://127.0.0.1:8765/v1 model:=fake-vlm
```

Within about 15 s, the watcher logs `EventTrigger object_dropped (0.90) -> manipulation ...`
and Time Machine logs `Recording completed successfully: /var/lib/rtm/clips/object_dropped_...`.
Stop everything with Ctrl+C, then:

```bash
ros2 run rtm_watcher review list /var/lib/rtm/clips
ros2 bag info /var/lib/rtm/clips/object_dropped_*  | head -20
grep -A3 '"coverage"' /var/lib/rtm/clips/object_dropped_*/event.json
```

**Check:** every topic from your profile appears in `ros2 bag info` with a sensible message
count, the duration is about `pre + post` seconds, and `"pre_event_complete": true`. The
fake VLM fires on a schedule, not on real events, so don't judge the clips' content yet.

To test from a non-ROS system or by hand, flag "4 seconds ago" over a topic or HTTP (the
[REST bridge](../README.md#rest-api-bridge-optional) must be running for HTTP):

```bash
ros2 topic pub --once /time_machine/event_trigger ros_time_machine_msgs/msg/EventTrigger \
  "{event_stamp: {sec: $(($(date +%s) - 4))}, profile: manipulation, label: near_miss,
    source: human, confidence: 1.0}"
curl -X POST http://localhost:8080/api/events -H "Content-Type: application/json" \
     -d '{"label": "near_miss", "seconds_ago": 4, "profile": "manipulation"}'
```

## 7. Stand up a real VLM

**Setup A: GPU box on the LAN.** On the GPU box (Docker plus the NVIDIA Container Toolkit;
no ROS needed), from a copy of this repo:

```bash
cd deploy/vllm
cp .env.example .env
openssl rand -hex 16          # paste as VLLM_API_KEY in .env
./serve.sh                    # Qwen3-VL-4B FP8; the first start downloads the model
sudo ufw allow from 192.168.1.0/24 to any port 8000 proto tcp   # your LAN only
```

Profiles, VRAM, ports and troubleshooting are in the [vLLM kit README](../deploy/vllm/README.md).

**Setup B: on the robot.** `ros2 run rtm_vlm vlm recommend` picks a model that fits the
device, and `ros2 run rtm_vlm vlm serve` starts it. See
[Run it on your own hardware](VLM_BACKENDS.md#run-it-on-your-own-hardware).

**Setup D: hosted.** Set `allow_remote: true` and `api_key_env` in the watch list. The
watcher refuses non-LAN endpoints without `allow_remote: true`, so frames can't leave the
site by accident. See [Hosted providers](VLM_BACKENDS.md#hosted-providers).

**Then, from the robot,** measure the endpoint with requests shaped exactly like the
watcher's:

```bash
export VLLM_API_KEY=...                                   # if the server has one
deploy/vllm/check.sh http://192.168.1.50:8000 qwen3-vl-4b-instruct
```

```
Warm     p50 0.23 s  p95 0.25 s  max 0.25 s  (6 requests, 0 errors)
JSON     100% valid
Alarms   0 detection(s) on a scene where nothing happens
Suggested settings
  watch list:   sample_hz: 1.0   request_timeout_s: 10
  time machine: global.max_trigger_latency: 3
```

Copy the suggested `max_trigger_latency` into the Time Machine config, and the endpoint,
model and `api_key_env` into the watch list. `ros2 run rtm_vlm vlm bench ... --images DIR`
runs the same benchmark on frames from your own robot.

## 8. Go live

One launch runs Time Machine and the watcher against the endpoint in your watch list:

```bash
export VLLM_API_KEY=...
ros2 launch rtm_vlm semantic_stack.launch.py vlm:=external \
    config_file:=$HOME/rtm_config/time_machine.yaml \
    watch_list:=$HOME/rtm_config/watch_list.yaml
```

Make your events happen in front of the camera. For each one you should see:

```
EventTrigger object_dropped (0.95) -> manipulation: "The box falls off the suction cup." [detected 0.6s after it happened]
Recording completed successfully: /var/lib/rtm/clips/object_dropped_... (520 messages, 'object_dropped' from vlm:qwen3-vl-4b-instruct)
```

While it runs:

```bash
ros2 topic echo /rtm_watcher/status --field data           # calls, errors, invalid JSON, cooldowns, latency
ros2 topic echo /time_machine/recording_status             # one message per clip written
ros2 service call /time_machine/get_status ros_time_machine_msgs/srv/GetStatus
```

**Check:** `errors` and `invalid_json` in the watcher status stay at 0, and each clip's
caption describes what actually happened.

## 9. Measure on your own data

Watching live tells you it works. A labelled recording tells you how well, and lets you
compare models and settings on exactly the same events.

**Record** a session with the topics from your profile, ideally 15–30 minutes with a few
dozen real events:

```bash
ros2 bag record -s mcap -o ~/rtm_eval/session1 \
    /your/scene_camera/image_raw/compressed /your/wrist_camera/image_raw/compressed /joint_states
```

**Label** the start of every event in `truth.csv`, in bag time (Unix seconds). Read the bag's
`Start` from `ros2 bag info`, then add the offset at which you see each event in a viewer such
as Foxglove or `rqt_bag`:

```csv
label,time_s
object_dropped,1793158172.0
missed_pick,1793158245.5
```

**Replay** through the full stack on simulated time, writing to a directory per run:

```bash
# Terminal 1: the stack, on /clock. Set output.path in a copy of your config, e.g. /tmp/rtm_eval/run_4b
ros2 launch rtm_vlm semantic_stack.launch.py vlm:=external use_sim_time:=true \
    config_file:=$HOME/rtm_config/time_machine_eval.yaml \
    watch_list:=$HOME/rtm_config/watch_list.yaml
# Terminal 2: once it's up
ros2 bag play ~/rtm_eval/session1 --clock
```

**Score** the run against the labels:

```bash
ros2 run rtm_watcher review eval /tmp/rtm_eval/run_4b --truth ~/rtm_eval/truth.csv
```

| Metric | Means | Aim for |
|---|---|---|
| `recall` | Labelled events that ended up inside a saved clip | As close to 1.0 as you can get: the main objective |
| `pre_event_coverage` | Clips whose buffers still reached back to the window start | 1.0 |
| `lag_p50_s`, `lag_p99_s` | Time from the event to the trigger arriving | Below `max_trigger_latency` |
| `precision` | Clips that contain a labelled event | Secondary: extra clips cost disk and review time, not data |

Add `--any-label` to count a hit whatever label the clip fired with. Replay the same bag
with another model, prompt or setting into a new directory and compare.

## 10. Tune for recall

The goal is to miss as few real events as possible. A few false alarms are an acceptable
price: they cost disk space and a little review time, and the review loop filters them out
afterwards.

| Symptom | Lever |
|---|---|
| Events missed while the watcher was quiet | Lower `min_confidence` (per event or globally); reword `describe` to say what the camera sees; add a camera view that shows the event |
| Events missed soon after another trigger | Shorten `cooldown_s`. Any trigger, false or real, suppresses that label for this long |
| Fast events missed (contacts, slips under a second) | Raise `sample_hz` if `check.sh` says the model keeps up, or add a scorer to pin those windows ([the cascade](SEMANTIC_TRIGGERS.md#pin-then-judge-the-cascade)) |
| Subtle events missed | A larger model (`./serve.sh qwen3-vl-8b-fp8`), more `context_frames`, or a larger `max_image_px` |
| `pre_event_coverage` below 1.0 | Raise `max_trigger_latency` above `lag_p99_s`. A clip within the first few seconds after startup can also be short: the buffer hadn't filled yet |
| Too many clips | Accept them, or set `max_clips_per_hour` and `sample_rate` per event. Lower recall only as a last resort |

**Why the cooldown matters for recall.** The model sees each event in several consecutive
windows, and the cooldown stops it from triggering every time. But a false alarm also starts
the cooldown, and a real event inside it is lost. In our demo with a 10 s cooldown, one
startup false alarm hid the first real drop (recall 0.93). At 5 s, recall was 1.0 and each
drop was saved about twice, because the repeat detection became an extra clip. Repeats that
arrive before the first clip's window has closed are merged into it.

After a few days of real clips, `review stats` suggests a per-label `min_confidence` from your
own verdicts (see [Reviewing clips](SEMANTIC_TRIGGERS.md#reviewing-clips)).

## 11. Run it day to day

**Start on boot.** An example systemd unit. Adjust the paths, user and key file:

```ini
# /etc/systemd/system/rtm.service
[Unit]
Description=ROS Time Machine with semantic triggers
After=network-online.target
Wants=network-online.target

[Service]
User=robot
EnvironmentFile=/etc/rtm/env          # VLLM_API_KEY=..., plus ROS_DOMAIN_ID if you use one
ExecStart=/bin/bash -c 'source /opt/ros/jazzy/setup.bash && \
    source /home/robot/rtm_ws/install/setup.bash && \
    exec ros2 launch rtm_vlm semantic_stack.launch.py vlm:=external \
        config_file:=/home/robot/rtm_config/time_machine.yaml \
        watch_list:=/home/robot/rtm_config/watch_list.yaml'
KillSignal=SIGINT                     # lets pending clips be written before exit
TimeoutStopSec=30
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now rtm
journalctl -u rtm -f
```

On the GPU box, the vLLM container restarts on its own after reboots until you run
`./serve.sh --stop`.

**Watch its health.** `/rtm_watcher/status` (every 5 s: calls, errors, invalid JSON, mean
latency), `/time_machine/buffer_stats` (per-topic memory and pins), and
`/time_machine/recording_status` (each clip as it's written). If the VLM goes down, the
watcher keeps sampling and retries; rule triggers and manual triggers keep working.

**Review clips.** Mark clips good or bad, and the verdict is stored in each clip's
`event.json`:

```bash
ros2 run rtm_watcher review list  /var/lib/rtm/clips --unreviewed
ros2 run rtm_watcher review mark  /var/lib/rtm/clips/object_dropped_1793158192_673457614 good
ros2 run rtm_watcher review stats /var/lib/rtm/clips
```

The review CLI has no ROS dependency (`python3 -m rtm_watcher.review`), so it also runs
wherever the clips end up.

**Get clips off the robot.** Each clip is a self-contained directory (MCAP plus
`event.json`), so any file sync works:

```bash
rsync -a robot-01:/var/lib/rtm/clips/ /data/rtm/robot-01/
```

Delete clips on the robot only after they've been copied, and leave the newest few
minutes alone, because a clip may still be being written.

**Trigger from your own systems.** Fleet dashboards, operator tablets and test harnesses can
flag a moment with `POST /api/events` on the REST bridge (`label`, `seconds_ago`, `profile`).
Any ROS node can publish `EventTrigger` with an `event_stamp` in the past.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `import rclpy` fails, or the build fails with `No module named 'em'` | Another Python is shadowing the system one; see [stage 2](#2-install-on-the-robot) |
| Clips are empty or cut short during bag replay | Run with `use_sim_time:=true` and play with `--clock` |
| `Topic ... not currently available` | Topic names must match exactly; Time Machine starts buffering as soon as they appear |
| Watcher refuses the endpoint ("frames would leave the site") | Use a LAN IP, a dotless or `.local` hostname, or set `allow_remote: true` deliberately |
| `401` from the VLM | Export the key named in `api_key_env` in the shell or systemd unit that runs the watcher |
| Port 8000 already in use on the GPU box | Set `VLLM_PORT` in `deploy/vllm/.env` and use that port in the endpoint |
| `invalid_json` climbing in `/rtm_watcher/status` | For thinking-capable models set `extra_body: {reasoning_effort: none}` |
| `pre_event_complete: false` | Raise `max_trigger_latency`; see [stage 10](#10-tune-for-recall) |

More: [vLLM kit troubleshooting](../deploy/vllm/README.md#troubleshooting) ·
[Failure behavior](SEMANTIC_TRIGGERS.md#failure-behavior) ·
[README troubleshooting](../README.md#troubleshooting)
