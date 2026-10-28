# vLLM server for semantic triggers

Runs a Qwen3-VL (or Cosmos-Reason2) model behind an OpenAI-compatible API on an NVIDIA
GPU machine, for the ROS Time Machine watcher to call. It's the same whether the watcher
runs on the same machine or on a robot elsewhere on the LAN.

```bash
cp .env.example .env          # set VLLM_API_KEY if other machines will connect
./serve.sh                    # Qwen3-VL-4B FP8 in Docker; waits until it answers
./check.sh                    # health, models, and a latency check with real watcher requests
```

This kit is plain Docker and shell; the GPU machine doesn't need ROS. Contents:
`serve.sh` (start, stop, logs), `check.sh` (verify from any machine), `compose.yaml`,
`profiles/` (one file per model), `.env.example` (your local settings).

- [Where things run](#where-things-run)
- [Requirements](#requirements)
- [Pick a model](#pick-a-model)
- [Start, stop, logs](#start-stop-logs)
- [Check it](#check-it)
- [Point the watcher at it](#point-the-watcher-at-it)
- [Test the ROS stack on the same machine](#test-the-ros-stack-on-the-same-machine)
- [Troubleshooting](#troubleshooting)

## Where things run

**A. Everything on the GPU machine.** This is the setup for development and testing. The
demo's fake sensors (or a bag replay), Time Machine, the watcher and vLLM all run on one
Ubuntu box, and the watcher calls `http://127.0.0.1:8000/v1`.

**B. Robot plus a GPU box on the LAN.** This is the setup for real robots.

```
┌────────────── robot ───────────────┐              ┌──── GPU box ────┐
│ cameras ─► time_machine_node       │              │                 │
│        └─► rtm_watcher ────────────┼── HTTP ─────►│ vLLM (this kit) │
│            (samples 1 fps, JPEG)   │◄── JSON ─────│                 │
└────────────────────────────────────┘              └─────────────────┘
```

The cameras and full-rate buffers stay on the robot. Only the watcher's sampled frames
cross the network: about 4 × 40 KB JPEGs per request, roughly 1–2 Mbit/s at 1 Hz. The
GPU box needs no ROS, and timestamps never leave the robot, so the two clocks don't need
to agree.

**Avoid running the watcher away from the cameras.** Full-rate camera topics over DDS
saturate Wi-Fi, and the watcher and Time Machine would then stamp frames with different
machines' clocks, which needs `chrony` time sync to stay correct.

## Requirements

Ubuntu 22.04 or 24.04, an NVIDIA GPU with a recent driver (`nvidia-smi` should list it),
and one of:

- **Docker (recommended):** [Docker Engine](https://docs.docker.com/engine/install/ubuntu/)
  with the compose plugin, plus the
  [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html).
  Check that containers see the GPU:

  ```bash
  docker run --rm --gpus all ubuntu nvidia-smi
  ```

- **Native:** a Python virtual environment with `pip install vllm==0.31.0`, then
  `./serve.sh --native`.

The model weights download from Hugging Face on first start, a few GB per model, into
`~/.cache/huggingface`. Later starts reuse them.

## Pick a model

```bash
./serve.sh --list
```

| Profile | Model | VRAM | Use it when |
|---|---|---|---|
| `qwen3-vl-4b-fp8` (default) | Qwen3-VL-4B-Instruct-FP8 | ~6 GB | RTX 40-series or newer. Best latency per quality |
| `qwen3-vl-8b-fp8` | Qwen3-VL-8B-Instruct-FP8 | ~11 GB | 16 GB RTX 40-series card; subtler events. Bench it: slower |
| `qwen3-vl-2b` | Qwen3-VL-2B-Instruct (BF16) | ~5 GB | Lowest latency; 8 GB cards |
| `qwen3-vl-4b` | Qwen3-VL-4B-Instruct (BF16) | ~10 GB | GPUs without FP8 support (RTX 30-series, A-series) |
| `cosmos-reason2-2b` | nvidia/Cosmos-Reason2-2B (BF16) | ~5 GB | Physical-reasoning tuning. Gated: accept the license on Hugging Face and set `HF_TOKEN` |

FP8 needs compute capability 8.9 or newer: RTX 40-series (including the 4070 Ti Super),
Ada, Hopper, Blackwell. Both 4B profiles serve as `qwen3-vl-4b-instruct`, so watch lists
don't change when you switch between them.

vLLM reserves `GPU_MEM_UTIL` × total VRAM at startup (0.85 by default) and fills what's
left after the weights with KV cache. If the same GPU drives your desktop or runs other
models (Ollama, games), stop them or lower `GPU_MEM_UTIL` in `.env`.

## Start, stop, logs

```bash
cp .env.example .env
openssl rand -hex 16          # paste as VLLM_API_KEY in .env if other machines connect
./serve.sh                    # or: ./serve.sh qwen3-vl-8b-fp8
```

```
Starting qwen3-vl-4b-fp8 (Qwen/Qwen3-VL-4B-Instruct-FP8) in Docker...
Waiting for the model to load (the first start downloads it; follow with ./serve.sh --logs)
Ready.
  This machine:   endpoint http://127.0.0.1:8000/v1
  Other machines: endpoint http://192.168.1.50:8000/v1
  Model name:     qwen3-vl-4b-instruct
```

| Command | Does |
|---|---|
| `./serve.sh --logs` | Follow the server log |
| `./serve.sh --stop` | Stop and remove the container |
| `./serve.sh --dry-run` | Show what would run |
| `./serve.sh --native` | Run `vllm serve` in the foreground from your Python env |

The container restarts automatically after reboots and crashes until you `--stop` it. To
switch models, run `./serve.sh <other-profile>`; Compose replaces the running container.

Settings in `.env` override the profile: `VLLM_PORT`, `VLLM_BIND` (`127.0.0.1` to refuse
other machines), `VLLM_API_KEY`, `HF_TOKEN`, `HF_CACHE`, `VLLM_IMAGE` (the pinned vLLM
version), and any profile value such as `GPU_MEM_UTIL` or `MAX_MODEL_LEN`.

## Check it

```bash
./check.sh                                    # on the GPU machine
./check.sh http://192.168.1.50:8000           # from the robot (any machine with this repo)
VLLM_API_KEY=... ./check.sh http://gpubox:8000
```

`check.sh` confirms `/health`, lists the served models, then runs `vlm bench`: six
requests shaped exactly like the watcher's (4 frames at 640 px, sliding window). Run it
from the machine the watcher will run on, so network time is included. The end of its
output is what to configure:

```
Suggested settings
  watch list:   sample_hz: 1.0   request_timeout_s: 10
  time machine: global.max_trigger_latency: 4
```

Put `max_trigger_latency` into Time Machine's config (`global:`); it sizes the buffers
so late answers still find their data. Install `python3-yaml` and `python3-pil` for the
benchmark (`sudo apt install python3-yaml python3-pil`).

## Point the watcher at it

In the watch list:

```yaml
watcher:
  endpoint: http://192.168.1.50:8000/v1    # http://127.0.0.1:8000/v1 on the same machine
  model: qwen3-vl-4b-instruct              # the profile's served name
  api_key_env: VLLM_API_KEY                # if the server has a key: export it on the robot
```

Or keep the watch list as is and override at launch:

```bash
export VLLM_API_KEY=...                    # same value as the server's .env
ros2 launch rtm_watcher watcher.launch.py watch_list:=/path/watch_list.yaml \
    endpoint:=http://192.168.1.50:8000/v1 model:=qwen3-vl-4b-instruct api_key_env:=VLLM_API_KEY
```

Endpoints the watcher accepts without `allow_remote: true`:
- `localhost` and `127.0.0.1`;
- private LAN IPs (`10.x`, `172.16–31.x`, `192.168.x`);
- `.local` names (mDNS);
- dotless hostnames such as `gpubox`, which only resolve on your network.

A public domain name counts as remote, on purpose.

If the robot can't reach the server, open the port only to your LAN on the GPU machine:

```bash
sudo ufw allow from 192.168.1.0/24 to any port 8000 proto tcp
```

## Test the ROS stack on the same machine

The full walkthrough is in [docs/TESTING_V2_ON_UBUNTU.md](../../docs/TESTING_V2_ON_UBUNTU.md):
build, unit and integration tests, the no-GPU demo, then the demo against this server:

```bash
ros2 launch rtm_watcher demo.launch.py vlm:=external \
    endpoint:=http://127.0.0.1:8000/v1 model:=qwen3-vl-4b-instruct
```

The demo camera drops a cup every 20 s. Clips with the VLM's caption land in
`/tmp/ros_time_machine/demo/`, and each drop's true time goes to `truth.csv` there, so
`review eval` can score recall, precision and detection lag.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Startup fails with CUDA out of memory, or "No available memory for the cache blocks" | Something else holds VRAM (`nvidia-smi`): stop it, use a smaller profile, or adjust `GPU_MEM_UTIL` / `MAX_MODEL_LEN` in `.env` |
| FP8 profile fails on an RTX 30-series card | That GPU has no FP8: use `qwen3-vl-4b` or `qwen3-vl-2b` |
| `401` / `403` downloading `cosmos-reason2-2b` | Accept the model's license on Hugging Face, put a token in `HF_TOKEN` |
| `check.sh` says 401 on `/v1/models` | The server has a key: `export VLLM_API_KEY=...` |
| Robot gets "connection refused" | `VLLM_BIND=0.0.0.0` in `.env`, port open in `ufw`, and the IP from `./serve.sh` output |
| Watcher refuses the endpoint ("frames would leave the site") | Use the LAN IP or a dotless/`.local` hostname, or set `allow_remote: true` deliberately |
| `docker: Error response from daemon: could not select device driver "nvidia"` | Install the NVIDIA Container Toolkit, then `sudo systemctl restart docker` |
| First request after start takes seconds | Normal warm-up; `request_timeout_s: 30` in the watch list covers it |
| Invalid JSON or slow answers | `check.sh` prints hints. For thinking-capable models set `extra_body: {reasoning_effort: none}` |
| Port 8000 already in use | Another server (llama.cpp, a previous vLLM): stop it or set `VLLM_PORT` |

To upgrade vLLM, set `VLLM_IMAGE` in `.env` to a newer tag, run `./serve.sh` again, then
`./check.sh`.
