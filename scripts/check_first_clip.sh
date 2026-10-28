#!/usr/bin/env bash
# Time the two paths a new user takes to a first clip, and fail if either breaks:
#   1. the no-GPU demo (`ros2 launch rtm_watcher demo.launch.py`)
#   2. `ros2 run rtm_watcher quickstart` against a running "robot" (the demo's fake sensors)
#
# Run from a built and sourced workspace:  scripts/check_first_clip.sh
# CI runs it on every pull request; the timings go to the job summary.
set -uo pipefail
# Job control: without it, background jobs of a non-interactive shell ignore SIGINT, and
# ROS nodes only shut down cleanly (writing pending clips) on SIGINT
set -m

DEMO_TIMEOUT_S=${DEMO_TIMEOUT_S:-60}
WORK=$(mktemp -d)
DEMO_DIR=/tmp/ros_time_machine/demo
PIDS=()

cleanup() {
  for pid in "${PIDS[@]}"; do kill -INT "$pid" 2>/dev/null; done
  sleep 2
  for pid in "${PIDS[@]}"; do kill "$pid" 2>/dev/null; done
}
trap cleanup EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }
now() { date +%s.%N; }
since() { awk -v a="$1" -v b="$(now)" 'BEGIN { printf "%.1f", b - a }'; }

# 1. Demo: launch to the first clip on disk
rm -rf "$DEMO_DIR"
start=$(now)
ros2 launch rtm_watcher demo.launch.py > "$WORK/demo.log" 2>&1 &
DEMO_PID=$!
PIDS+=("$DEMO_PID")
until compgen -G "$DEMO_DIR/*/event.json" > /dev/null; do
  if ! kill -0 "$DEMO_PID" 2>/dev/null || [ "$(since "$start" | cut -d. -f1)" -ge "$DEMO_TIMEOUT_S" ]; then
    tail -40 "$WORK/demo.log" >&2
    fail "the demo saved no clip within ${DEMO_TIMEOUT_S} s"
  fi
  sleep 0.5
done
demo_s=$(since "$start")
kill -INT "$DEMO_PID"; wait "$DEMO_PID" 2>/dev/null
if grep -qE "Traceback|ImportError" "$WORK/demo.log"; then
  grep -B2 -A8 -E "Traceback|ImportError" "$WORK/demo.log" | head -40 >&2
  fail "the demo logged a Python error"
fi
echo "demo: first clip ${demo_s} s after launch"

# 2. quickstart: an empty folder to a clip of the robot's own topics
SENSORS="$(ros2 pkg prefix rtm_watcher)/lib/rtm_watcher/demo_sensors"
"$SENSORS" --ros-args -p truth_csv:="$WORK/truth.csv" > "$WORK/sensors.log" 2>&1 &
PIDS+=("$!")
cd "$WORK" || exit 1
start=$(now)
timeout -s INT 120 ros2 run rtm_watcher quickstart --save-in 12 --wait 15 \
  > "$WORK/quickstart.log" 2>&1
rc=$?
quick_s=$(since "$start")
clips=$(compgen -G "$WORK/rtm_quickstart/clips/*/event.json" | wc -l)
if [ "$rc" -ne 0 ] || [ "$clips" -lt 1 ]; then
  cat "$WORK/quickstart.log" >&2
  tail -30 "$WORK"/rtm_quickstart/logs/*.log >&2 2>/dev/null
  fail "quickstart exited $rc with $clips clip(s)"
fi
echo "quickstart: own clip ${quick_s} s after start (12 s of it is --save-in)"

if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
  {
    echo "### Time to first clip"
    echo "| Path | Seconds |"
    echo "|---|---|"
    echo "| \`demo.launch.py\`, launch to first clip | ${demo_s} |"
    echo "| \`quickstart --save-in 12\`, start to own clip | ${quick_s} |"
  } >> "$GITHUB_STEP_SUMMARY"
fi
