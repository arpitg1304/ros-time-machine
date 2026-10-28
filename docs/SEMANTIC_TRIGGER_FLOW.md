# How a semantic trigger becomes a clip

This page follows one event from the line you write in a watch list to the clip on disk,
through the actual code path. The examples are the demo's real files, and the numbers come
from a 5-minute run of the demo with Qwen3-VL-4B FP8 on vLLM (RTX 4070 Ti SUPER, 15 cup
drops).

For the design and the full reference, see [Semantic triggers](SEMANTIC_TRIGGERS.md). To set
it up on your own robot, see [Wiring it into your robot](INTEGRATION_GUIDE.md).

<p align="center">
  <img src="images/semantic-flow.svg" alt="Two YAML files configure two nodes. Sensors feed the watcher at 1 Hz and Time Machine's buffers at full rate. The watcher sends frames and a prompt to the VLM, gets JSON back, and publishes an EventTrigger that Time Machine queues, protects in its buffers and writes out as MCAP plus event.json." width="100%">
</p>

The two YAML files configure two separate nodes on the robot. The only link between them is
one ROS topic, `/time_machine/event_trigger`. Buffers record every message all the time; the
VLM only ever sees a 1 Hz sample.

- [What you write](#what-you-write)
- [Step by step](#step-by-step)
- [One drop, to scale](#one-drop-to-scale)
- [How many detections became clips](#how-many-detections-became-clips)

## What you write

The comment on each key gives the step below where it takes effect.

[`demo_watch_list.yaml`](../src/rtm_watcher/config/demo_watch_list.yaml), trimmed:

```yaml
watcher:
  endpoint: http://127.0.0.1:8765/v1   # 4  the fake VLM; vlm:=external endpoint:=... overrides it
  model: fake-vlm                      # 4  e.g. model:=qwen3-vl-4b-instruct
  cameras: [/camera/front/image_raw/compressed]  # 2
  state_topics: [/joint_states]        # 2, 3  summarized as text
  sample_hz: 1.0                       # 2  per camera, in robot time
  context_frames: 4                    # 2, 3  frames per prompt
  robot_context: "A robot arm with a two-finger gripper holds a red cup above a table."  # 3
  min_confidence: 0.6                  # 6  (the default)
  cooldown_s: 5                        # 6  per label
  events:
    - label: object_dropped            # 3, 5, 7
      describe: "An object falls out of the gripper or off the table."  # 3
      profile: manipulation            # 7, 8  which profile saves it
      # optional per event: min_confidence, cooldown_s, sample_rate (6),
      #   pre_event / post_event (6, 8), tags (7)
```

[`demo_semantic.yaml`](../src/ros_time_machine/config/demo_semantic.yaml), trimmed:

```yaml
snapshots:
  - name: "manipulation"               # 8  matched by events[].profile
    # no trigger: block, so only EventTrigger, pins or the service can fire it
    recording:
      topics: ["/camera/front/image_raw/compressed", "/joint_states"]  # buffered at full rate
      pre_event_duration: 5.0          # 8  start = event - 5 s
      post_event_duration: 3.0         # 8  end   = event + 3 s
    rate_limit:
      max_triggers_per_minute: 20      # 9
    output:
      path: "/tmp/ros_time_machine/demo"   # 10
      name_pattern: "{label}_{timestamp}"  # 10
global:
  max_trigger_latency: 7.0             # buffer per topic = 5 + 3 + 7 = 15 s
  # defaults used here: write_settle_time 0.2 (10),
  #   budget.merge_gap, max_clip_duration, max_clips_per_hour (9)
```

## Step by step

Steps 1 to 7 run in the watcher, once per sampled frame. Step 4 is the only one that leaves
the robot. Steps 8 to 10 run in Time Machine, once per trigger.

### In the watcher

Code: [`watchlist.py`](../src/rtm_watcher/rtm_watcher/watchlist.py),
[`engine.py`](../src/rtm_watcher/rtm_watcher/engine.py),
[`prompt.py`](../src/rtm_watcher/rtm_watcher/prompt.py).

**1. Load and check the watch list.** At startup the YAML is parsed into one config object, and
the node subscribes to every camera and state topic. An endpoint outside this machine or the
private LAN is refused unless `allow_remote: true`, so frames can't leave the site by accident.
An API key, if any, is read from the environment variable named in `api_key_env`, never from
the file.

**2. Sample frames in robot time.** Each camera is sampled at `sample_hz`, using the ROS clock
time at which each message was received. Only the frames that are kept get encoded to JPEG,
downscaled to `max_image_px`. They go into a sliding window of the last `context_frames` per
camera. State topics are rendered as text at most 5 times a second. Robot time is the same
clock Time Machine stamps its buffers with, so the watcher and the buffers agree on when a
frame happened. Under bag replay with `--clock`, both use bag time.

**3. Build the prompt.** A fixed system prompt sets the rules: report only listed labels, only
what is visible, `frame_index` is the first frame where the event shows, answer in JSON. The
user message is built from your YAML. This is what the model received in the demo run (the
joint values are illustrative):

```text
Context: A robot arm with a two-finger gripper holds a red cup above a table.

Events to watch for:
- object_dropped: An object falls out of the gripper or off the table.
- person_in_cell: A person enters the robot's work area.

Robot state (latest):
/joint_states: shoulder_pan=0.12 (effort 1.10), shoulder_lift=-0.85 ...

4 frames follow, oldest first. Times are seconds relative to the newest frame.
Frame 0 (/camera/front/image_raw/compressed, t=-3.0s): [JPEG]
Frame 1 (/camera/front/image_raw/compressed, t=-2.0s): [JPEG]
Frame 2 (/camera/front/image_raw/compressed, t=-1.0s): [JPEG]
Frame 3 (/camera/front/image_raw/compressed, t=+0.0s): [JPEG]
```

A hash of the rules, events, context and frame count becomes `source_version`
(`prompt:d049f7` here). It is written into every clip, so each clip can be traced to the
wording that produced it.

### At the VLM

**4. Ask the model.** Whenever the window holds a frame the model hasn't seen, the watcher sends
one `POST /v1/chat/completions` and waits for it, up to `request_timeout_s`. Each answer is a
list of events, empty when nothing happened:

```json
{"events": [{"label": "object_dropped", "frame_index": 3, "confidence": 0.95,
             "description": "The red cup falls from the gripper and lands on the table."}]}
```

The caption and confidence are from a real clip; the `frame_index` is an example. On the
RTX 4070 Ti SUPER a call took 0.32 s on average across 288 calls.

### Back in the watcher

Code: [`decision.py`](../src/rtm_watcher/rtm_watcher/decision.py),
[`engine.py`](../src/rtm_watcher/rtm_watcher/engine.py).

**5. Validate the answer.** JSON wrapped in prose or code fences is tolerated. Entries with
unknown labels or out-of-range frame indexes are dropped and counted in `dropped_entries`;
unparseable answers count as `invalid_json`. A bad answer never crashes the watcher.

**6. Decide, one detection at a time.** Three filters, in this order:

1. **Confidence.** Drop it if it's below the event's `min_confidence` (or the global one).
2. **Cooldown.** Drop it if this label already fired within `cooldown_s` of this frame's time.
   Consecutive windows overlap, so the model sees one drop in several windows; this stops it
   firing every time.
3. **Sampling.** Keep only a `sample_rate` fraction. A detection dropped here still starts the
   cooldown, so the next window can't re-roll it.

A detection that passes gets `event_stamp` = the time of the frame at `frame_index`: the moment
the event was seen, not the moment the model answered. That is why the model's latency doesn't
cost any data.

**7. Publish an EventTrigger** on `/time_machine/event_trigger`. Anything else can publish the
same message: a scorer, an operator, or the REST bridge's `POST /api/events`.

```yaml
event_stamp:    frame time where the event was seen
profile:        manipulation          # from events[].profile
label:          object_dropped
confidence:     0.95
description:    "The red cup falls from the gripper and lands on the table."
source:         vlm:qwen3-vl-4b-instruct
source_version: prompt:d049f7;model:qwen3-vl-4b-instruct
start, end:     0, meaning the profile's window   # or set by pre_event / post_event
tags:           []
```

### In Time Machine

Code: [`recorder.cpp`](../src/ros_time_machine/src/recorder.cpp),
[`trigger_queue.cpp`](../src/ros_time_machine/src/trigger_queue.cpp).

**8. Resolve the window.** The `profile` name picks the snapshot; an unknown name is rejected,
and an empty one means the first profile. A zero `start` becomes
`event_stamp − pre_event_duration`, and a zero `end` becomes `event_stamp + post_event_duration`.
Here that gives 5 s before and 3 s after the frame.

**9. Queue it.**

- **Reject** a window that ends before it starts or is longer than `max_clip_duration`.
- **Merge** into a pending clip of the same profile if the windows overlap or are within
  `merge_gap`. The clip grows to cover both, and every event is listed in its `event.json`. A
  merge doesn't count against rate limits.
- **Otherwise** open a new clip, if the profile's `max_triggers_per_minute` and the global
  `max_clips_per_hour` allow it.

While a clip is pending, its window is protected: messages the ring buffer would evict move to
a side store instead, so a slow window can't lose its start.

**10. Write the clip once its window has closed.** Every 50 ms the writer checks for clips whose
`end + write_settle_time` has passed in robot time. It reads every buffered message in
`[start, end]`, writes an MCAP bag named from `name_pattern`, writes `event.json` next to it,
and announces the clip on `/time_machine/recording_status`. The real `event.json` for the drop
below, trimmed:

```json
"window":   { "start": "2026-10-28T03:40:47.726Z", "end": "2026-10-28T03:40:55.726Z" },
"coverage": { "pre_event_complete": true },
"trigger": {
  "label": "object_dropped",
  "confidence": 0.95,
  "description": "The red cup falls from the gripper and lands on the table.",
  "source_version": "prompt:d049f7;model:qwen3-vl-4b-instruct",
  "event_stamp": "2026-10-28T03:40:52.726Z",
  "received_at": "2026-10-28T03:40:53.366Z",
  "detection_lag_s": 0.64,
  "merged_from": 1
}
```

## One drop, to scale

The cup dropped at 03:40:52.000 UTC (from `truth.csv`); T is that moment. Every mark comes from
the run's logs and the clip's `event.json`.

<p align="center">
  <img src="images/semantic-timeline.svg" alt="Timeline of one drop: the cup drops at T, the frame that shows it is stamped T+0.73 s, the trigger arrives at T+1.37 s, the saved window runs from T−4.27 s to T+3.73 s, and the clip is written at T+3.96 s, when the buffer reaches back to about T−11 s." width="100%">
</p>

The window is cut around the frame, not around the answer, so it already includes the 4.27 s
before the drop when the trigger arrives. The buffer only has to reach back far enough when the
clip is written; `max_trigger_latency` is that margin. If it's too small for a slow model, the
clip is still written, with `pre_event_complete: false`.

## How many detections became clips

Counters from `/rtm_watcher/status` five seconds before the end of the 5-minute run, then Time
Machine's side from its log. The run kept going for those last five seconds, which is why there
is one more clip than triggers.

| Stage | Step | Count |
|---|---|---:|
| Frames offered by the camera | 2 | 4,485 |
| Frames sampled at 1 Hz | 2 | 289 |
| VLM calls (0 errors, 0 invalid JSON) | 4–5 | 288 |
| Detections reported by the model | 5 | 81 |
| Dropped: below `min_confidence` | 6 | 0 |
| Dropped: inside `cooldown_s` | 6 | 51 |
| Dropped: `sample_rate` | 6 | 0 |
| **EventTriggers published** | 7 | **30** |
| Merged or rejected by the queue | 9 | 0 |
| **Clips written by the end of the run** | 10 | **31** |

The model saw each drop in about five consecutive windows (81 detections for 15 drops), and the
cooldown removed most of the repeats. With `cooldown_s: 5`, one repeat per drop still gets
through about 8 s later, while the cup lies on the floor. That is why there are about two clips
per drop, and why recall stayed at 1.0 even after a false alarm at startup. With
`cooldown_s: 10`, that startup false alarm hid the first real drop (recall 0.93).
