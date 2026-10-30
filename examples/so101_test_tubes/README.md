# SO-101 test-tube bench

Configs for semantic triggers on a LeRobot SO-101 that moves a red-capped test tube from the
leftmost slot of a rack to the rightmost. Tested on 45 replayed Diffusion Policy rollouts:
see [the results](../../docs/RESULTS_SO101.md).

| File | What |
|---|---|
| [`watch_list.yaml`](watch_list.yaml) | Four triggers that don't overlap (tube dropped, wrong slot, person in cell, tube placed), watching the overhead and wrist cameras |
| [`time_machine.yaml`](time_machine.yaml) | Profiles: `manipulation` (6 s before, 3 s after), `safety` (10 s / 5 s, plus an `/emergency_stop` rule), `episodes` (25 s before a success) |
| [`labels_rollout_diffpo_jun8.csv`](labels_rollout_diffpo_jun8.csv) | Ground truth for the 45 rollouts, per episode: `episode,label,t_rel_s` |

The topics are the ones [`lerobot-rewind`](../../tools/lerobot-rewind/) publishes from a
LeRobot dataset: `/camera/top/...`, `/camera/wrist/...`, `/joint_states`, `/lerobot/action`.

**On a replay** of any SO-101 dataset with `top` and `wrist` cameras:

```bash
lerobot-rewind convert <dataset> session.mcap
ros2 launch ros_time_machine time_machine.launch.py use_sim_time:=true \
    config_file:=examples/so101_test_tubes/time_machine.yaml
ros2 launch rtm_watcher watcher.launch.py use_sim_time:=true \
    watch_list:=examples/so101_test_tubes/watch_list.yaml
ros2 bag play session.mcap --clock
```

**On the live robot**, LeRobot holds the cameras and the serial bus, so a bridge in the LeRobot
process has to publish the same topics. That bridge isn't written yet.

For a different task, keep the shape and change the words: describe what the camera sees,
make sure no two triggers fit the same event, and leave out events that can't happen.
