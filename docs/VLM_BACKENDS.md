# Choosing and running a VLM for semantic triggers

The watcher talks to any OpenAI-compatible chat endpoint. This page covers which model to
run where, how to stand one up on your own hardware with `rtm_vlm`, and how to measure any
endpoint, local or hosted, before trusting it with your robot.

- [Start here](#start-here)
- [Run it on your own hardware](#run-it-on-your-own-hardware)
- [Measure before you trust it: `vlm bench`](#measure-before-you-trust-it-vlm-bench)
- [What we measured](#what-we-measured)
- [Hosted providers](#hosted-providers)
- [Latency levers, in order of impact](#latency-levers-in-order-of-impact)

## Start here

| Your situation | Use | Why |
|---|---|---|
| Robot with a Jetson Orin Nano (8 GB) | **Qwen3-VL-2B** on llama.cpp, on the robot | Frames never leave the robot; fits in memory |
| Jetson AGX Orin / Orin NX, a 6 GB+ NVIDIA GPU, or Apple Silicon | **Qwen3-VL-4B** on llama.cpp | The reference watcher: best quality per second |
| A GPU box on the robot's LAN (16 GB+) | Qwen3-VL-8B, or vLLM if several robots share it | More headroom for subtle events |
| You want physical-reasoning tuning | **Cosmos-Reason2-2B** on vLLM | NVIDIA's Qwen3-VL-2B post-train, documented on Jetson |
| An Ubuntu desktop with an RTX 40-series card, same machine or on the LAN | **Qwen3-VL-4B FP8** on vLLM: [deploy/vllm](../deploy/vllm/README.md) | Docker kit with profiles, an API key, and a LAN setup guide |
| No GPU anywhere, data may leave the site | A hosted Flash-Lite / mini-class model | See [Hosted providers](#hosted-providers) |

One command tells you what fits the machine you're on:

```bash
ros2 run rtm_vlm vlm recommend          # or: python3 -m rtm_vlm recommend
```

```
Device   Apple M5 Pro (apple): ~41.6 GB usable for the model, 64 GB RAM
Model    qwen3-vl-4b (Qwen/Qwen3-VL-4B-Instruct-GGUF:Q4_K_M, ~5 GB, Apache-2.0)
Runtime  llamacpp: not installed -> brew install llama.cpp
...
```

It detects Jetson (device tree), NVIDIA GPUs (`nvidia-smi`), Apple Silicon and CPU-only
machines, then picks the largest curated model that fits with headroom. `--prefer
latency|balanced|quality` moves it along the 2B, 4B, 8B ladder. On an Orin Nano it also
lightens the watcher (3 frames at 512 px) and reminds you about the power mode.

## Run it on your own hardware

For an NVIDIA GPU machine serving one or more robots over the LAN, use the Docker kit in
[deploy/vllm](../deploy/vllm/README.md) instead: `./serve.sh`, then `./check.sh` from the
robot.

`rtm_vlm` is optional. It manages the server, but the runtime itself you install once:

| Device | Install the runtime |
|---|---|
| Apple Silicon | `brew install llama.cpp` (Metal), or Ollama |
| Jetson | `jetson-containers run $(autotag llama_cpp)` ([jetson-containers](https://github.com/dusty-nv/jetson-containers)); `autotag vllm` for vLLM |
| Linux + NVIDIA GPU | llama.cpp built with CUDA, the `ghcr.io/ggml-org/llama.cpp:server-cuda` container, `pip install vllm`, or Ollama |
| CPU only | llama.cpp release build; expect seconds per call |

Then:

```bash
ros2 run rtm_vlm vlm doctor                    # hardware + which runtimes are installed
ros2 run rtm_vlm vlm serve                     # start the recommended model, wait until ready
ros2 run rtm_vlm vlm serve --dry-run           # just print the command
ros2 run rtm_vlm vlm serve --profile qwen3-vl-2b --backend ollama
ros2 run rtm_vlm vlm recommend --write-watch-list watch_list.yaml
```

`serve` runs `llama-server -hf Qwen/Qwen3-VL-4B-Instruct-GGUF:Q4_K_M ...`, which downloads
the model and its vision projector on first start. It waits until `/v1/models` answers,
then keeps the server in the foreground until you stop it. If something already serves
that endpoint, it's reused rather than started twice. Optional llama.cpp flags are
passed only if the installed build lists them in `--help`.

**The whole stack in one launch.** A local VLM, Time Machine and the watcher:

```bash
ros2 launch rtm_vlm semantic_stack.launch.py                 # model picked for this device
ros2 launch rtm_vlm semantic_stack.launch.py prefer:=latency
ros2 launch rtm_vlm semantic_stack.launch.py vlm:=external watch_list:=/path/hosted.yaml
```

The launched model is served as `local-vlm`, whichever one was picked, so the bundled
[`local_watch_list.yaml`](../src/rtm_vlm/config/local_watch_list.yaml) works unchanged. Copy
it and edit cameras and events. With `backend:=ollama`, point the watch list at port 11434.

## Measure before you trust it: `vlm bench`

Latency claims for VLMs vary wildly with image size, number of images, hidden reasoning
and caching, so measure the exact request your watcher will send:

```bash
ros2 run rtm_vlm vlm bench --provider llamacpp --model qwen3-vl-4b-instruct
ros2 run rtm_vlm vlm bench --provider ollama --model qwen3-vl:4b --frames 2 --image-px 448
ros2 run rtm_vlm vlm bench --provider gemini --model <model id>       # needs GEMINI_API_KEY
ros2 run rtm_vlm vlm bench --endpoint http://10.0.0.5:8000/v1 --model local-vlm \
    --watch-list my_watch_list.yaml --images ~/frames_from_my_robot/
```

What it does:

- **Sends watcher-shaped requests.** It uses the watcher's prompt builder, your watch list
  if given, `--frames` images at `--image-px`, and one cold request followed by `--requests`
  warm ones.
- **Slides the window the way the watcher does.** Each request has one new frame. Re-sending
  identical frames lets prompt and image caches make an endpoint look several times faster
  than it is; we measured a 12× difference.
- **Uses a static synthetic scene by default.** It's a tabletop with a red cup and sensor
  noise, at realistic JPEG sizes. Any reported event in it is a false alarm. `--images DIR`
  uses your own frames instead.
- **Reports the numbers that matter.** Cold and warm latency (p50/p95/max), JSON validity,
  and false alarms.
- **Translates them into settings.** The `sample_hz` the model can keep up with, a
  `request_timeout_s`, and Time Machine's `max_trigger_latency`. The worst-case lag is one
  sampling period plus two p95 calls (one already in flight, one that sees the event) plus a
  margin.
- **Gives specific hints.** It suggests turning reasoning off, a frame count that fits the
  rate, `cache_prompt` for llama.cpp JSON trouble, and using a slow model as a cascade judge
  instead of a continuous watcher.

## What we measured

**Measured on our machine:** Apple M5 Pro (64 GB), Ollama 0.24, `gemma4:26b-a4b-it-q8_0`
(a mixture-of-experts model with ~4B active parameters), synthetic 640×480 scenes, 8 warm
requests each:

| Request | Reasoning | p50 | p95 | JSON valid | False alarms |
|---|---|---|---|---|---|
| 4 frames × 640 px | on (default) | 8.74 s | 10.66 s | 88 % | 0 |
| 4 frames × 640 px | `reasoning_effort: none` | 4.56 s | 4.58 s | 100 % | 0 |
| 2 frames × 448 px | `reasoning_effort: none` | 2.39 s | 2.40 s | 100 % | 0 |

**Semantic check:** four drawn frames of a red cup falling out of a gripper, run through the
real `WatcherEngine` (reasoning off). The model answered `object_dropped` at `frame_index: 2`,
the first frame where the cup is falling, with confidence 0.90 and the caption "The red cup
falls from the gripper and lands on the floor." A static control scene returned
`{"events": []}`.

What this tells you:

- **Hidden reasoning doubled latency and broke 1 in 8 answers**, likely by using up
  `max_tokens` before the JSON. Turn it off for watchers (`extra_body` below).
- **Every frame is re-encoded on every call**, about 1.1 s per 640 px image for this model,
  because the sliding window changes the prompt from the first image on. Frames × resolution
  is the main cost.
- **A 26B model is too slow to watch at 1 Hz on a laptop, but fine as a cascade judge**,
  which runs only on rare pinned windows. Watchers want the 2B–4B models above.

**From published sources** (not measured by us; verify with `vlm bench`):

- **Jetson Orin Nano Super, Cosmos-Reason2-2B (a Qwen3-VL-2B post-train):** about 114 ms
  image time-to-first-token and 38 tok/s with llama.cpp Q4_K_M; 75 ms and 56 tok/s with vLLM
  W4A16, at 6.9 GB peak ([Hokwang Choi](https://hokwangchoi.com/blog/vlm-benchmarks/)). The
  first image request after start is 0.5–3 s.
- **Jetson AGX Orin 32 GB, Qwen3-VL-8B Q4_K_M on llama.cpp:** first answer after 2.75 s for one
  720p image ([OpenZeka VLM benchmark explorer](https://whitepapers.openzeka.com/vlm-inference-benchmarks/)).
- **Qwen3-VL GGUF builds** with vision projectors are published by Qwen
  ([2B](https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct-GGUF),
  [8B](https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct-GGUF)); `llama-server` serves them
  with an OpenAI-compatible API that accepts images.
- **Cosmos-Reason2-2B on Jetson:** NVIDIA documents it with vLLM
  ([Jetson AI Lab](https://www.jetson-ai-lab.com/tutorials/cosmos-reason2-vlm)) under the
  NVIDIA Open Model License.

## Hosted providers

For teams that can send frames off-site: set `allow_remote: true` in the watch list and the
key's variable name in `api_key_env`. The watcher refuses non-LAN endpoints otherwise.

| Provider | Try | Why | Caveats |
|---|---|---|---|
| Google Gemini (`--provider gemini`) | Gemini 3.5 / 3.1 Flash-Lite | Google positions Flash-Lite for low-latency, high-volume use ([changelog](https://ai.google.dev/gemini-api/docs/changelog)); one gateway reports ~0.9 s median TTFT for 3.1 Flash-Lite ([Requesty](https://www.requesty.ai/model/google/gemini-3.1-flash-lite)) | That figure is from a gateway, not Google |
| OpenAI (`--provider openai`) | GPT-5.4 mini / nano | OpenAI says 5.4 mini is over 2× faster than GPT-5 mini, tuned for multimodal ([Decrypt](https://decrypt.co/361434)) | Turn reasoning down via `extra_body`; the client adapts to `max_completion_tokens` and rejected `temperature` automatically |
| DeepInfra (`--provider deepinfra`) | Qwen3-VL-30B-A3B ($0.15 / $0.60 per M tokens), 235B-A22B | Same family as the local default; a priority tier buys faster TTFT at 1.5× ([pricing](https://deepinfra.com/pricing)) | |
| OpenRouter (`--provider openrouter`) | Any of the above | One key, many providers | Latency depends on the route; 5.5 % fee on credit purchases |
| Fireworks (`--provider fireworks`) | Qwen3-VL | Hosted open weights | |
| Anthropic | Claude Haiku 5.5 (`claude-haiku-5-5`, $0.10 / $0.50 per M tokens) | Cheap and fast | Not wired into the watcher yet: low latency needs effort set to `low` through the native Anthropic API |
| Groq | (none for vision) | Very fast for text | Current vision offerings unclear; benchmark first |

Treat every latency claim in this table as a starting point. Run `vlm bench --provider ...`
with your frames before deciding; vision APIs are bursty under load, so look at p95, not the
average.

## Latency levers, in order of impact

1. **Turn hidden reasoning off.** This was 2× in our test, and it also fixed truncated JSON.

   ```yaml
   extra_body: {reasoning_effort: none}   # Ollama, OpenAI-style APIs
   ```

2. **Fewer, smaller frames.** Cost scales with frames × resolution because the whole window
   is re-encoded each call: `context_frames: 2`, `max_image_px: 448`.
3. **Low-detail images on hosted APIs.** `image_detail: low` (OpenAI-style), which sends far
   fewer image tokens.
4. **A smaller model.** 2B over 4B over 8B. Bench the next size down before buying hardware.
5. **Run it closer.** On the robot or the LAN instead of the cloud: no upload, no queueing
   behind other customers.
6. **Sample less often.** `sample_hz: 0.5` halves the load. Let the cascade's scorers catch
   fast events.
