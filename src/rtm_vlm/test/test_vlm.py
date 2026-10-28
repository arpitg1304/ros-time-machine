import json
import os
import socket
import sys

import pytest

from rtm_vlm import backends, cli
from rtm_vlm.bench import bench_config, frames_from_dir, run_bench, suggest, synthetic_frames
from rtm_vlm.hardware import detect, SystemProbe
from rtm_vlm.profiles import PROFILES
from rtm_vlm.recommend import recommend
from rtm_watcher.fake_vlm import FakeVlmServer, ScriptedResponder
from rtm_watcher.vlm_client import OpenAICompatibleClient

GB = 1024 ** 3


class FakeProbe(SystemProbe):

    def __init__(self, system='Linux', machine='x86_64', ram_gb=16, files=None, commands=None):
        self._system, self._machine, self._ram = system, machine, ram_gb
        self.files, self.commands = files or {}, commands or {}

    def system(self):
        return self._system

    def machine(self):
        return self._machine

    def read(self, path):
        return self.files.get(path)

    def run(self, cmd):
        return self.commands.get(cmd[0])

    def ram_gb(self):
        return self._ram


ORIN_NANO = FakeProbe('Linux', 'aarch64', 7.4, files={
    '/proc/device-tree/model': 'NVIDIA Jetson Orin Nano Engineering Reference Developer Kit Super',
    '/etc/nv_tegra_release': '# R36 (release), REVISION: 4.3'})
AGX_ORIN = FakeProbe('Linux', 'aarch64', 61.4, files={
    '/proc/device-tree/model': 'NVIDIA Jetson AGX Orin Developer Kit'})
RTX_4060 = FakeProbe(commands={'nvidia-smi': 'NVIDIA GeForce RTX 4060, 8188'})
RTX_3050 = FakeProbe(commands={'nvidia-smi': 'NVIDIA GeForce RTX 3050 Laptop GPU, 4096'})
MAC_16 = FakeProbe('Darwin', 'arm64', 16, commands={'sysctl': 'Apple M4'})
MAC_64 = FakeProbe('Darwin', 'arm64', 64, commands={'sysctl': 'Apple M5 Pro'})
CPU_ONLY = FakeProbe('Linux', 'x86_64', 32)


# Hardware detection -----------------------------------------------------------

def test_detects_jetson_by_device_tree():
    device = detect(ORIN_NANO)
    assert device.kind == 'jetson' and 'Orin Nano' in device.name
    assert device.memory_gb == pytest.approx(7.4 * 0.75)
    assert device.details['l4t'].startswith('# R36')


def test_detects_nvidia_dgpu_vram():
    device = detect(FakeProbe(commands={'nvidia-smi': 'RTX A2000, 6138\nRTX 4090, 24564'}))
    assert device.kind == 'nvidia' and device.name == 'RTX 4090'
    assert device.memory_gb == pytest.approx(24564 / 1024)


def test_detects_apple_and_cpu():
    assert detect(MAC_64).kind == 'apple' and detect(MAC_64).name == 'Apple M5 Pro'
    cpu = detect(CPU_ONLY)
    assert cpu.kind == 'cpu' and cpu.memory_gb == 16


def test_real_machine_detection_does_not_crash():
    assert detect().kind in ('jetson', 'nvidia', 'apple', 'cpu')


# Recommendations --------------------------------------------------------------

@pytest.mark.parametrize('probe,prefer,expected', [
    (ORIN_NANO, 'balanced', 'qwen3-vl-2b'),   # 4B does not fit in ~5.5 GB
    (AGX_ORIN, 'balanced', 'qwen3-vl-4b'),
    (AGX_ORIN, 'quality', 'qwen3-vl-8b'),
    (RTX_4060, 'balanced', 'qwen3-vl-4b'),
    (RTX_4060, 'quality', 'qwen3-vl-4b'),     # 8B does not fit in 8 GB with headroom
    (RTX_3050, 'balanced', 'qwen3-vl-2b'),
    (MAC_16, 'balanced', 'qwen3-vl-4b'),
    (MAC_64, 'latency', 'qwen3-vl-2b'),
    (CPU_ONLY, 'quality', 'qwen3-vl-2b'),     # Bigger is too slow without a GPU
])
def test_recommendations(probe, prefer, expected):
    assert recommend(detect(probe), prefer).profile.key == expected


def test_orin_nano_gets_lighter_watcher_and_power_hint():
    rec = recommend(detect(ORIN_NANO))
    assert rec.watcher['context_frames'] == 3 and rec.watcher['max_image_px'] == 512
    assert any('power mode' in w for w in rec.warnings)
    assert any('Cosmos-Reason2' in w for w in rec.warnings)


def test_cpu_slows_sampling_and_tiny_device_warns():
    assert recommend(detect(CPU_ONLY)).watcher['sample_hz'] == 0.5
    tiny = recommend(detect(FakeProbe('Linux', 'aarch64', 4, files={
        '/proc/device-tree/model': 'NVIDIA Jetson Orin Nano 4GB'})))
    assert tiny.profile.key == 'qwen3-vl-2b' and any('tight' in w for w in tiny.warnings)


def test_bad_preference():
    with pytest.raises(ValueError):
        recommend(detect(MAC_16), 'fastest')


# Backends -------------------------------------------------------------------

def test_llamacpp_plan_and_flag_detection():
    profile = PROFILES['qwen3-vl-2b']
    launch = backends.plan(profile, 'llamacpp', port=8123, flags={'--jinja', '--alias'})
    assert launch.server[:3] == ['llama-server', '-hf', 'Qwen/Qwen3-VL-2B-Instruct-GGUF:Q4_K_M']
    assert '--jinja' in launch.server and launch.endpoint == 'http://127.0.0.1:8123/v1'
    assert launch.model == 'qwen3-vl-2b-instruct'
    old = backends.plan(profile, 'llamacpp', flags=set())        # Build without --jinja
    assert '--jinja' not in old.server
    cpu = backends.plan(profile, 'llamacpp', gpu=False, alias='local-vlm', extra_args=['-t', '8'])
    assert cpu.server[cpu.server.index('-ngl') + 1] == '0'
    assert cpu.model == 'local-vlm' and cpu.server[-2:] == ['-t', '8']


def test_vllm_and_ollama_plans():
    vllm = backends.plan(PROFILES['cosmos-reason2-2b'], 'vllm')
    assert vllm.server[:3] == ['vllm', 'serve', 'nvidia/Cosmos-Reason2-2B']
    assert json.loads(vllm.server[vllm.server.index('--limit-mm-per-prompt') + 1]) == \
        {'image': 8, 'video': 0}
    assert vllm.server[vllm.server.index('--gpu-memory-utilization') + 1] == '0.85'
    fp8 = backends.plan(PROFILES['qwen3-vl-4b-fp8'], 'vllm', host='0.0.0.0')
    assert fp8.server[2] == 'Qwen/Qwen3-VL-4B-Instruct-FP8'
    assert fp8.model == 'qwen3-vl-4b-instruct'     # Same name as BF16: watch lists unchanged
    assert any('license' in n.lower() for n in vllm.notes)
    ollama = backends.plan(PROFILES['qwen3-vl-4b'], 'ollama')
    assert ollama.server == ['ollama', 'serve'] and ollama.model == 'qwen3-vl:4b'
    assert ollama.after_ready == [['ollama', 'pull', 'qwen3-vl:4b']]
    assert ollama.endpoint == 'http://127.0.0.1:11434/v1'
    assert 'OLLAMA_HOST=127.0.0.1:11434 ollama serve' in ollama.shell()
    aliased = backends.plan(PROFILES['qwen3-vl-4b'], 'ollama', alias='local-vlm')
    assert aliased.model == 'local-vlm'
    assert aliased.after_ready[-1] == ['ollama', 'cp', 'qwen3-vl:4b', 'local-vlm']


def test_plan_errors():
    with pytest.raises(backends.BackendError):
        backends.plan(PROFILES['cosmos-reason2-2b'], 'llamacpp')   # No GGUF
    with pytest.raises(backends.BackendError):
        backends.plan(PROFILES['cosmos-reason2-2b'], 'ollama')
    with pytest.raises(backends.BackendError):
        backends.plan(PROFILES['qwen3-vl-4b'], 'tensorrt')


def test_supported_flags_parses_help():
    flags = backends.supported_flags(sys.executable)   # python --help lists e.g. --version
    assert '--version' in flags
    assert backends.supported_flags('/nonexistent/binary') == set()


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def test_managed_server_starts_waits_and_stops(tmp_path):
    port = free_port()
    here = os.path.dirname(__file__)
    # Keep the caller's PYTHONPATH: under ROS it is how installed packages are found
    env_path = os.pathsep.join(p for p in [os.path.join(here, '..', '..', 'rtm_watcher'),
                                           os.environ.get('PYTHONPATH', '')] if p)
    launch = backends.LaunchPlan(
        'test', [sys.executable, '-m', 'rtm_watcher.fake_vlm', '--port', str(port)],
        f'http://127.0.0.1:{port}/v1', 'fake-vlm', env={'PYTHONPATH': env_path})
    log = tmp_path / 'server.log'
    with backends.ManagedServer(launch, str(log)) as server:
        assert not server.reused
        assert server.wait_ready(timeout_s=20, poll_s=0.1)
        assert backends.endpoint_ready(launch.endpoint)
        with backends.ManagedServer(launch) as second:   # Same endpoint: reused, not started
            assert second.reused and second.process is None
    assert server.process.poll() is not None
    assert not backends.endpoint_ready(launch.endpoint)


def test_managed_server_reports_crash():
    launch = backends.LaunchPlan('test', [sys.executable, '-c', 'raise SystemExit(3)'],
                                 f'http://127.0.0.1:{free_port()}/v1', 'x')
    with backends.ManagedServer(launch) as server:
        assert not server.wait_ready(timeout_s=10, poll_s=0.05)


# Benchmark --------------------------------------------------------------------

def test_synthetic_frames_are_realistic_jpegs():
    pytest.importorskip('PIL')
    frames = synthetic_frames(3, 640)
    assert len(frames) == 3 and all(f[:3] == b'\xff\xd8\xff' for f in frames)
    assert 15_000 < len(frames[0]) < 200_000   # Close to a real 640px camera JPEG


def test_frames_from_dir_cycles(tmp_path):
    (tmp_path / 'a.jpg').write_bytes(b'\xff\xd8\xffA')
    (tmp_path / 'b.jpg').write_bytes(b'\xff\xd8\xffB')
    assert frames_from_dir(str(tmp_path), 3) == [b'\xff\xd8\xffA', b'\xff\xd8\xffB',
                                                 b'\xff\xd8\xffA']
    with pytest.raises(ValueError):
        frames_from_dir(str(tmp_path / 'missing'), 1)


def test_bench_measures_latency_validity_and_false_alarms():
    responder = ScriptedResponder('object_dropped', every=4, confidence=0.9)
    with FakeVlmServer(responder, delay_s=0.05) as server:
        config = bench_config('fake', server.endpoint, frames=4, image_px=640)
        client = OpenAICompatibleClient(server.endpoint, 'fake', timeout_s=5)
        seen = []
        result = run_bench(config, client, [b'\xff\xd8\xffx'] * 4, requests=8,
                           progress=lambda i, s: seen.append(i))
    assert seen == list(range(9))              # Cold + 8 warm
    assert result.cold_s is not None and result.requests == 8
    assert len(result.latencies) == 8 and result.p50 >= 0.05
    assert result.valid_rate == 1.0
    assert result.detections == 2              # Calls 4 and 8 "saw" an event in a static scene
    body = server.requests[-1]
    assert sum(p['type'] == 'image_url' for p in body['messages'][1]['content']) == 4


def test_bench_slides_window_one_new_frame_per_request():
    with FakeVlmServer(ScriptedResponder()) as server:
        config = bench_config('fake', server.endpoint, frames=3, image_px=320)
        images = [bytes([0xff, 0xd8, 0xff, i]) for i in range(5)]   # 3 + 2 requests
        run_bench(config, OpenAICompatibleClient(server.endpoint, 'fake'), images, requests=2)

    def shown(body):
        return [p['image_url']['url'] for p in body['messages'][1]['content']
                if p['type'] == 'image_url']
    windows = [shown(body) for body in server.requests]
    assert len(windows) == 3 and all(len(w) == 3 for w in windows)
    assert windows[1][:2] == windows[0][1:] and windows[1][2] not in windows[0]
    assert windows[2][:2] == windows[1][1:]


def test_bench_images_has_enough_distinct_frames():
    pytest.importorskip('PIL')
    from rtm_vlm.bench import bench_images
    config = bench_config('m', 'http://127.0.0.1:8000/v1', 4, 320)
    images = bench_images(config, requests=6)
    assert len(images) == 10 and len(set(images)) == 10


def test_bench_counts_invalid_json_and_stops_on_unreachable():
    with FakeVlmServer(lambda messages: 'I see a robot.') as server:
        config = bench_config('fake', server.endpoint, 2, 320)
        result = run_bench(config, OpenAICompatibleClient(server.endpoint, 'fake'),
                           [b'x'] * 2, requests=3)
    assert result.invalid_json == 3 and result.valid_rate == 0.0
    down = bench_config('fake', 'http://127.0.0.1:9/v1', 1, 320)
    result = run_bench(down, OpenAICompatibleClient(down.endpoint, 'fake', timeout_s=0.5),
                       [b'x'], requests=5)
    assert result.requests == 0 and result.errors and not result.latencies


def test_suggest_from_measurements():
    from rtm_vlm.bench import BenchResult
    fast = BenchResult('e', 'm', 4, 640, True, latencies=[0.4, 0.5, 0.6, 0.7])
    advice = suggest(fast, 1.0)
    assert advice['keeps_up'] and advice['sample_hz'] == 1.0
    assert advice['max_trigger_latency'] == 4    # ceil(1 + 2*0.7 + 1)
    slow = BenchResult('e', 'm', 4, 640, True, latencies=[2.0, 2.5, 3.0])
    advice = suggest(slow, 1.0)
    assert not advice['keeps_up'] and advice['sample_hz'] == 0.4
    assert advice['request_timeout_s'] == 10 and suggest(BenchResult('e', 'm', 1, 1, True)) == {}
    assert any('reasoning_effort' in h for h in advice['hints'])
    assert any('--frames 1 fits' in h for h in advice['hints'])     # 0.8 s / 0.625 s per frame
    tuned = suggest(slow, 1.0, {'reasoning_effort': 'none'})
    assert not any('reasoning_effort' in h for h in tuned['hints'])
    assert suggest(fast, 1.0)['hints'] == []
    bad = BenchResult('e', 'm', 4, 640, True, latencies=[0.5], requests=4, invalid_json=1,
                      detections=1)
    hints = ' '.join(suggest(bad, 1.0)['hints'])
    assert 'Invalid JSON' in hints and 'nothing happens' in hints


def test_bench_uses_user_watch_list(tmp_path):
    from rtm_watcher.watchlist import parse_watch_list
    user = parse_watch_list({'cameras': ['/cam'], 'robot_context': 'A forklift.',
                             'events': [{'label': 'pallet_fell', 'describe': 'A pallet falls.'}]})
    config = bench_config('m', 'http://127.0.0.1:8000/v1', 2, 512, user, image_detail='low')
    assert config.labels == ['pallet_fell'] and config.max_image_px == 512
    assert config.image_detail == 'low' and config.robot_context == 'A forklift.'


# CLI ---------------------------------------------------------------------------

def test_cli_recommend_writes_valid_watch_list(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(cli, 'detect', lambda: detect(ORIN_NANO))
    out = tmp_path / 'wl.yaml'
    assert cli.main(['recommend', '--port', '8100', '--write-watch-list', str(out)]) == 0
    text = capsys.readouterr().out
    assert 'qwen3-vl-2b' in text and 'jetson-containers' in text
    from rtm_watcher.watchlist import load_watch_list
    wl = load_watch_list(str(out))
    assert wl.endpoint == 'http://127.0.0.1:8100/v1' and wl.model == 'qwen3-vl-2b-instruct'
    assert wl.context_frames == 3 and wl.max_image_px == 512


def test_cli_serve_dry_run_and_missing_binary(capsys, monkeypatch):
    monkeypatch.setattr(cli, 'detect', lambda: detect(RTX_4060))
    monkeypatch.setattr(backends, 'find_binary', lambda backend: None)
    assert cli.main(['serve', '--dry-run', '--', '--threads', '4', '--ros-args', '-r', 'x']) == 0
    line = capsys.readouterr().out.strip()
    assert line.startswith('llama-server -hf Qwen/Qwen3-VL-4B') and line.endswith('--threads 4')
    assert cli.main(['serve', '--port', str(free_port())]) == 2
    assert 'not found' in capsys.readouterr().err


def test_cli_bench_against_fake_server(capsys):
    with FakeVlmServer(ScriptedResponder(), delay_s=0.02) as server:
        assert cli.main(['bench', '--endpoint', server.endpoint, '--model', 'fake',
                         '--requests', '3', '--frames', '2', '--image-px', '320']) == 0
    out = capsys.readouterr().out
    assert 'Warm     p50' in out and '100% valid' in out and 'max_trigger_latency' in out
    assert 'Keeps up' in out


def test_cli_bench_errors(capsys, monkeypatch):
    assert cli.main(['bench', '--model', 'x']) == 2
    assert cli.main(['bench', '--provider', 'llamacpp']) == 2
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    assert cli.main(['bench', '--provider', 'openai', '--model', 'gpt-x']) == 2
    assert 'OPENAI_API_KEY' in capsys.readouterr().err
