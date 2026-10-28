#!/usr/bin/env bash
# Check a vLLM (or any OpenAI-compatible) server from any machine: health, served
# models, then a short benchmark with the watcher's real requests.
#
#   ./check.sh                                   http://127.0.0.1:8000, first served model
#   ./check.sh http://192.168.1.50:8000          a server on the LAN
#   ./check.sh http://gpubox:8000 qwen3-vl-4b-instruct
#
# If the server has an API key, export VLLM_API_KEY first. The benchmark needs
# python3 with PyYAML; Pillow (python3-pil) makes its test frames realistic.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
URL="${1:-http://127.0.0.1:8000}"
URL="${URL%/}"
URL="${URL%/v1}"
MODEL="${2:-}"
REQUESTS="${REQUESTS:-6}"

get() {
  if [ -n "${VLLM_API_KEY:-}" ]; then
    curl -fsS --max-time 10 -H "Authorization: Bearer $VLLM_API_KEY" "$1"
  else
    curl -fsS --max-time 10 "$1"
  fi
}

printf 'Health   '
if curl -fs --max-time 5 "$URL/health" >/dev/null; then
  echo "ok ($URL)"
else
  echo "no answer from $URL/health"
  echo "  - Is the server up? On the GPU machine: ./serve.sh --logs"
  echo "  - From another machine: is VLLM_BIND=0.0.0.0, and is port ${URL##*:} open (sudo ufw status)?"
  exit 1
fi

if ! models_json="$(get "$URL/v1/models" 2>&1)"; then
  echo "Models   request failed: $models_json"
  echo "  - 401 means the server has an API key: export VLLM_API_KEY=..."
  exit 1
fi
models="$(printf '%s' "$models_json" | python3 -c \
  'import json, sys; print(" ".join(m["id"] for m in json.load(sys.stdin)["data"]))')"
echo "Models   $models"
if [ -z "$MODEL" ]; then
  MODEL="${models%% *}"
fi

if ! python3 -c 'import yaml' 2>/dev/null; then
  echo "Bench    skipped: python3 needs PyYAML (sudo apt install python3-yaml)"
  exit 0
fi
if ! python3 -c 'import PIL' 2>/dev/null; then
  echo "Note     Pillow not installed (sudo apt install python3-pil): the benchmark falls back"
  echo "         to one tiny frame and will look faster than real cameras."
fi

echo "Bench    $REQUESTS watcher-shaped requests to $MODEL"
key_args=()
if [ -n "${VLLM_API_KEY:-}" ]; then
  key_args=(--api-key-env VLLM_API_KEY)
fi
PYTHONPATH="$REPO/src/rtm_vlm:$REPO/src/rtm_watcher${PYTHONPATH:+:$PYTHONPATH}" \
  python3 -m rtm_vlm bench --endpoint "$URL/v1" --model "$MODEL" \
  --requests "$REQUESTS" ${key_args[@]+"${key_args[@]}"}
