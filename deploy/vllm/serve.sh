#!/usr/bin/env bash
# Start, stop or inspect the vLLM server for ROS Time Machine semantic triggers.
#
#   ./serve.sh                       default profile (qwen3-vl-4b-fp8) in Docker
#   ./serve.sh qwen3-vl-8b-fp8       another profile (./serve.sh --list)
#   ./serve.sh --native [profile]    run `vllm serve` from the current Python env instead
#   ./serve.sh --dry-run [profile]   print what would run, change nothing
#   ./serve.sh --stop | --logs | --list
#
# Local settings (port, API key, HF token, image tag) go in .env next to this
# script; start from .env.example.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROFILE="qwen3-vl-4b-fp8"
MODE="docker"
ACTION="up"
WAIT_S="${WAIT_S:-1800}"   # First start downloads the model

usage() { sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; }
die() { echo "error: $*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --native) MODE="native" ;;
    --dry-run) ACTION="dry-run" ;;
    --stop) ACTION="stop" ;;
    --logs) ACTION="logs" ;;
    --list) ACTION="list" ;;
    -h|--help) usage; exit 0 ;;
    -*) die "unknown option $1 (see --help)" ;;
    *) PROFILE="$1" ;;
  esac
  shift
done

compose() { docker compose -f "$HERE/compose.yaml" "$@"; }

case "$ACTION" in
  list)
    for f in "$HERE"/profiles/*.env; do
      printf '%-20s %s\n' "$(basename "$f" .env)" "$(sed -n 's/^# //p' "$f" | head -n 1)"
    done
    exit 0 ;;
  stop) compose down; exit 0 ;;
  logs) compose logs -f --tail 100; exit 0 ;;
esac

PROFILE_FILE="$HERE/profiles/$PROFILE.env"
[ -f "$PROFILE_FILE" ] || die "no profile '$PROFILE' (./serve.sh --list)"

# The profile, then local overrides from .env
set -a
# shellcheck disable=SC1090
. "$PROFILE_FILE"
if [ -f "$HERE/.env" ]; then
  # shellcheck disable=SC1091
  . "$HERE/.env"
fi
VLLM_BIND="${VLLM_BIND:-0.0.0.0}"
VLLM_PORT="${VLLM_PORT:-8000}"
VLLM_IMAGE="${VLLM_IMAGE:-vllm/vllm-openai:v0.31.0}"
HF_CACHE="${HF_CACHE:-$HOME/.cache/huggingface}"
MAX_MODEL_LEN="${MAX_MODEL_LEN:-8192}"
GPU_MEM_UTIL="${GPU_MEM_UTIL:-0.85}"
MAX_NUM_SEQS="${MAX_NUM_SEQS:-4}"
MAX_IMAGES="${MAX_IMAGES:-8}"
set +a

# The same flags compose.yaml passes to the container
native_args() {
  printf '%s\n' \
    --served-model-name "$SERVED_NAME" \
    --max-model-len "$MAX_MODEL_LEN" \
    --gpu-memory-utilization "$GPU_MEM_UTIL" \
    --max-num-seqs "$MAX_NUM_SEQS" \
    --limit-mm-per-prompt "{\"image\": $MAX_IMAGES, \"video\": 0}"
}

lan_ip() {
  hostname -I 2>/dev/null | awk '{print $1}' || true
}

print_endpoints() {
  local ip
  ip="$(lan_ip)"
  echo "  This machine:   endpoint http://127.0.0.1:$VLLM_PORT/v1"
  if [ "$VLLM_BIND" = "0.0.0.0" ]; then
    echo "  Other machines: endpoint http://${ip:-<this-machine-ip>}:$VLLM_PORT/v1"
  fi
  echo "  Model name:     $SERVED_NAME"
  echo "  Check it:       $HERE/check.sh http://127.0.0.1:$VLLM_PORT $SERVED_NAME"
}

if [ "$ACTION" = "dry-run" ]; then
  echo "profile: $PROFILE ($MODEL)"
  if [ "$MODE" = "native" ]; then
    printf 'vllm serve %s --host %s --port %s' "$MODEL" "$VLLM_BIND" "$VLLM_PORT"
    native_args | while IFS= read -r arg; do
      case "$arg" in *' '*) printf " '%s'" "$arg" ;; *) printf ' %s' "$arg" ;; esac
    done
    echo
  else
    echo "image: $VLLM_IMAGE  listen: $VLLM_BIND:$VLLM_PORT  cache: $HF_CACHE"
    echo "docker compose -f $HERE/compose.yaml up -d"
  fi
  [ -n "${VLLM_API_KEY:-}" ] && echo "api key: set (sent as VLLM_API_KEY, never on the command line)"
  exit 0
fi

if [ "$VLLM_BIND" = "0.0.0.0" ] && [ -z "${VLLM_API_KEY:-}" ]; then
  echo "warning: listening on all interfaces without VLLM_API_KEY; anyone on the network" \
       "can use this GPU. Set one in $HERE/.env" >&2
fi

if [ "$MODE" = "native" ]; then
  command -v vllm >/dev/null || die "vllm not found; pip install vllm (or use Docker: drop --native)"
  ARGS=()
  while IFS= read -r arg; do ARGS+=("$arg"); done < <(native_args)
  echo "Starting $MODEL with vLLM (Ctrl+C to stop)"
  print_endpoints
  exec vllm serve "$MODEL" --host "$VLLM_BIND" --port "$VLLM_PORT" "${ARGS[@]}"
fi

# Docker
command -v docker >/dev/null || die "docker not found; see README.md (Docker + NVIDIA Container Toolkit)"
docker compose version >/dev/null 2>&1 || die "docker compose plugin not found (apt install docker-compose-plugin)"
if ! docker info --format '{{json .Runtimes}}' 2>/dev/null | grep -q nvidia; then
  echo "warning: Docker has no nvidia runtime; install the NVIDIA Container Toolkit (README.md)" >&2
fi

echo "Starting $PROFILE ($MODEL) in Docker..."
compose up -d

echo "Waiting for the model to load (the first start downloads it; follow with ./serve.sh --logs)"
start=$(date +%s)
while :; do
  if curl -fs "http://127.0.0.1:$VLLM_PORT/health" >/dev/null 2>&1; then
    break
  fi
  if [ "$(docker inspect -f '{{.State.Running}}' rtm-vllm 2>/dev/null)" != "true" ]; then
    echo "error: the vLLM container stopped. Last log lines:" >&2
    compose logs --tail 30 >&2 || true
    exit 1
  fi
  elapsed=$(( $(date +%s) - start ))
  if [ "$elapsed" -ge "$WAIT_S" ]; then
    die "not ready after ${WAIT_S}s; ./serve.sh --logs"
  fi
  if [ $(( elapsed % 30 )) -lt 5 ]; then
    echo "  ${elapsed}s: $(compose logs --tail 1 2>/dev/null | tail -n 1 | cut -c1-120)"
  fi
  sleep 5
done

echo "Ready."
print_endpoints
