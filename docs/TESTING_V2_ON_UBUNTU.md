# Testing v2 on an Ubuntu GPU machine

A step-by-step check of the semantic-triggers work on one Ubuntu machine with an NVIDIA
GPU. Each step depends on the previous one, so stop at the first failure and send the
output listed under [If something fails](#if-something-fails).

| Step | Checks | Needs the GPU |
|---|---|---|
| 1–2 | Dependencies and the build (the first compile of the ROS C++) | No |
| 3 | Unit tests, plus launch tests for triggers, merging, pins and `event.json` | No |
| 4 | The full pipeline with a scripted VLM | No |
| 5–6 | A real VLM on vLLM, end to end, scored against ground truth | Yes |

## 1. Prerequisites

- Ubuntu 24.04 with [ROS 2 Jazzy](https://docs.ros.org/en/jazzy/Installation/Ubuntu-Install-Debs.html),
  or 22.04 with [ROS 2 Humble](https://docs.ros.org/en/humble/Installation/Ubuntu-Install-Debs.html)
- `sudo apt install ros-dev-tools python3-pil python3-yaml python3-pytest`
- For steps 5–6: Docker and the NVIDIA Container Toolkit
  ([deploy/vllm/README.md](../deploy/vllm/README.md#requirements))

Get the `feat/v2-semantic-triggers` branch onto the machine, then:

```bash
cd ~/ros-time-machine
source /opt/ros/$ROS_DISTRO/setup.bash
sudo rosdep init 2>/dev/null; rosdep update
rosdep install --from-paths src --ignore-src -y     # mcap storage plugin, opencv, ...
```

If pyenv, conda or uv puts another Python first on `PATH`, ROS Jazzy (built for the system
Python 3.12) breaks: `import rclpy` fails, or CMake picks the wrong interpreter. Use the
system one in this workspace (`pyenv local system`, `conda deactivate`) and check that
`python3 -c "import rclpy"` works.

## 2. Build

```bash
colcon build --symlink-install --cmake-args -DPython3_EXECUTABLE=/usr/bin/python3 \
    2>&1 | tee /tmp/rtm_build.log
source install/setup.bash
```

All five packages should finish: `ros_time_machine_msgs`, `ros_time_machine`,
`ros_time_machine_rest_bridge`, `rtm_watcher`, `rtm_vlm`. This is the first time the v2 C++
compiles on ROS; if it fails, `/tmp/rtm_build.log` is what to send.

## 3. Tests

```bash
# C++ unit tests (buffer + pins, trigger queue, sidecar, config) and the two launch tests
colcon test --packages-select ros_time_machine \
    --ctest-args -R "test_(circular_buffer|pin_manager|trigger_queue|event_sidecar|config_manager|integration|event_trigger_integration)"
# Python: watcher, review CLI, rtm_vlm, deploy kit
colcon test --packages-select rtm_watcher rtm_vlm
python3 -m pytest -q deploy/vllm/tests
colcon test-result --verbose | tail -40
```

`test_event_trigger_integration` is the important one. It starts the node, sends a trigger
for a moment 1 s in the past, checks the clip's window and `event.json`, then checks that
overlapping triggers merge and that a pin kept through `~/release_pin` becomes a clip. The
`-R` filter skips the ament linters (`uncrustify`, `cpplint`); their style complaints
predate v2.

## 4. Full pipeline, no GPU

```bash
rm -rf /tmp/ros_time_machine/demo
ros2 launch rtm_watcher demo.launch.py
```

Watch for these lines, in order (2 and 3 come from different processes, so they may swap):

1. `Subscribed to camera /camera/front/image_raw/compressed`
2. `EventTrigger object_dropped (0.90) -> manipulation ... [detected N.Ns after it happened]`
3. `Event 'object_dropped' from vlm:fake-vlm -> 'manipulation' clip 1 queued`
4. `Recording completed successfully: /tmp/ros_time_machine/demo/object_dropped_...`

After about a minute, press Ctrl+C, then:

```bash
ros2 run rtm_watcher review list /tmp/ros_time_machine/demo
cat /tmp/ros_time_machine/demo/object_dropped_*/event.json | head -40
ros2 bag info /tmp/ros_time_machine/demo/object_dropped_*  | head -20
```

Each clip should hold about 8 s of `/camera/front/image_raw/compressed` and
`/joint_states`, with `"pre_event_complete": true`. The fake VLM fires on a schedule, not
on the drops, so don't score this run.

While it runs, these show the moving parts:

```bash
ros2 topic echo /time_machine/recording_status
ros2 topic echo /rtm_watcher/status --field data
ros2 service call /time_machine/get_status ros_time_machine_msgs/srv/GetStatus
```

## 5. Start a real VLM

```bash
cd deploy/vllm
cp .env.example .env
./serve.sh                    # qwen3-vl-4b-fp8; first start downloads the model
./check.sh                    # note the suggested max_trigger_latency
```

If port 8000 is taken (`ss -ltn 'sport = :8000'`), set `VLLM_PORT` in `.env` and use that
port in step 6's `endpoint`. Set `VLLM_BIND=127.0.0.1` when nothing else needs to connect.

On a 16 GB RTX 40-series card, the default profile leaves plenty of headroom. Try
`./serve.sh qwen3-vl-8b-fp8` afterwards to compare.

## 6. Real VLM, end to end

```bash
rm -rf /tmp/ros_time_machine/demo
ros2 launch rtm_watcher demo.launch.py vlm:=external \
    endpoint:=http://127.0.0.1:8000/v1 model:=qwen3-vl-4b-instruct
```

Every 20 s the demo camera drops the cup (`Demo: the cup drops now`). Within a few
seconds the watcher should log `EventTrigger object_dropped (...)` with a caption from the
model and the detection lag. Let it run for 5 minutes (about 15 drops), stop it, then:

```bash
ros2 run rtm_watcher review list /tmp/ros_time_machine/demo
ros2 run rtm_watcher review eval /tmp/ros_time_machine/demo \
    --truth /tmp/ros_time_machine/demo/truth.csv
ros2 topic echo --once /rtm_watcher/status --field data    # before stopping: call stats
```

What good looks like. Recall is the objective; extra clips are an accepted cost. Measured
here: `qwen3-vl-4b-fp8` on an RTX 4070 Ti SUPER, 15 drops.

| Metric | Expect | Measured | If not |
|---|---|---|---|
| recall | 1.0 | 1.000 | Missed drops: try `qwen3-vl-8b-fp8`, lower `min_confidence`, or check the watcher log for errors |
| precision | ≈ 0.5 | 0.484 | The demo's `cooldown_s: 5` saves each drop twice: once as it falls, once ~8 s later while the cup still lies on the floor. Much lower: look for `person_in_cell` hits |
| lag_p50_s | ≈ 1–3 s | 1.37 | Compare with `./check.sh`; set `max_trigger_latency` above `lag_p99_s` |
| pre_event_coverage | ≥ 0.9 | 0.968 | A clip in the first seconds after launch is short because the buffer hasn't filled yet. Other short clips: raise `max_trigger_latency` in `demo_semantic.yaml` |

With `cooldown_s: 10` the same run gave recall 0.933 and precision 0.875: a false alarm at
startup started the cooldown and hid the first real drop.

The same eval over a replayed real-robot bag (`ros2 bag play --clock` with
`use_sim_time:=true`) and hand-labelled `truth.csv` is how to compare models and settings
on your own data.

## If something fails

Send:

- Build: `/tmp/rtm_build.log`.
- Tests: the output of `colcon test-result --verbose`, plus `log/latest_test/<package>/stdout_stderr.log`.
- Runtime: the terminal output of the launch, `ros2 run rtm_vlm vlm doctor`, and
  `./serve.sh --logs` (the last 50 lines) for the vLLM side.
