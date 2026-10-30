# lerobot-rewind

Replay a [LeRobot](https://github.com/huggingface/lerobot) dataset as if the robot were
running. `lerobot-rewind` lays the episodes on one robot timeline and writes a ROS 2 MCAP
session: every camera at its recorded rate, joint states and actions, with the robot idling
between episodes. `ros2 bag play --clock` then plays it to anything that listens on ROS 2,
with no robot attached.

It's how ROS Time Machine's semantic triggers were tested on 45 real SO-101 policy rollouts
([results](../../docs/RESULTS_SO101.md)), but nothing in it depends on Time Machine.

- Reads LeRobot **v2.1 and v3.0** datasets, from a local folder or the Hugging Face Hub.
- **Needs no ROS install** to write the MCAP: message definitions are embedded, so it runs
  on a laptop. Playing the result needs ROS 2 (Humble or newer).
- Helps you **label ground truth**: gripper-release times from the joint data, and contact
  sheets to judge each episode by eye.

## Install

```bash
pip install ./tools/lerobot-rewind                 # from this repo
pip install "./tools/lerobot-rewind[hub,sheets]"   # + Hub downloads and contact sheets
```

It also needs `ffmpeg` on the `PATH` to decode the videos (`apt install ffmpeg`,
`brew install ffmpeg`).

## Use

```bash
lerobot-rewind info arpitg1304/eval_smolvla_stack_lego          # cameras, joints, episodes
lerobot-rewind convert arpitg1304/eval_smolvla_stack_lego session.mcap --episodes 0-3
ros2 bag play session.mcap --clock                        # the robot, rewound
```

`convert` prints where each episode sits on the session clock and writes `session.json`
next to the MCAP with the same offsets.

| Topic | Type | What |
|---|---|---|
| `/camera/<camera>/image_raw/compressed` | `sensor_msgs/CompressedImage` | Each camera in the dataset (`top`, `wrist`, `front`, ...) as JPEG, at the recorded rate |
| `/joint_states` | `sensor_msgs/JointState` | `observation.state`, in LeRobot's units (SO-100/101: degrees, gripper 0-100) |
| `/lerobot/action` | `sensor_msgs/JointState` | `action`: the commanded positions |
| `/lerobot/episode` | `std_msgs/String` | `start <n>` and `end <n>` at each episode's boundaries |

Header stamps equal the bag's log times, so nodes running with `use_sim_time:=true` see
the robot's own clock.

| Option | Default | What |
|---|---|---|
| `--episodes` | all | `0-44`, or a list such as `3,10,15-20` |
| `--lead-in` | 30 | Seconds of idle robot before the first episode, so buffers can fill |
| `--gap` | 3 | Seconds between episodes. Cameras keep publishing the last frame, as an idle robot does |
| `--start` | 1800000000 | Robot time of the session start, in Unix seconds |
| `--quality` | 4 | JPEG quality as ffmpeg `-q:v`: 2 (best) to 31 |
| `--camera-topic` | `/camera/{camera}/image_raw/compressed` | Topic pattern for cameras |
| `--state-topic`, `--action-topic` | `/joint_states`, `/lerobot/action` | |

A 15-minute session of two 640×480 cameras at 30 fps takes about 30 s to write and is
about 650 MB.

## Label ground truth

To score a detector on the replay, you need to know when the real events happened.

```bash
# 1. When does the gripper let go? One row per episode, at its last release
lerobot-rewind events arpitg1304/eval_smolvla_stack_lego --joint gripper --below 15 --template labels.csv

# 2. What happened at each release? Look, then replace LABEL_ME in labels.csv
lerobot-rewind sheets arpitg1304/eval_smolvla_stack_lego --camera front --out sheets/ep --every 2

# 3. Convert with the labels: truth on the session clock, for scoring
lerobot-rewind convert arpitg1304/eval_smolvla_stack_lego session.mcap --labels labels.csv
```

`labels.csv` has one row per event: `episode,label,t_rel_s`, with time in seconds from the
episode start. `convert --labels` writes `session_truth.csv` (`label,time_s` on the session
clock), the format `ros2 run rtm_watcher review eval --truth` scores against.

## With ROS Time Machine

```bash
ros2 launch rtm_vlm semantic_stack.launch.py vlm:=external use_sim_time:=true \
    config_file:=time_machine.yaml watch_list:=watch_list.yaml
ros2 bag play session.mcap --clock
ros2 run rtm_watcher review eval <clips dir> --truth session_truth.csv
```

Or, with no configs to write: `ros2 run rtm_watcher quickstart --use-sim-time --vlm fake`
while the bag plays.

## Caveats

- Between episodes the replay holds the last frame, then cuts to the next episode's first
  frame. A real robot resets in view. A detector looking at a few frames at once can react
  to the cut; ignore triggers in the first seconds of each episode when scoring.
- Joint values keep LeRobot's units. `JointState` normally means radians; convert them
  before using the topic for kinematics.
