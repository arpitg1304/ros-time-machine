"""Static checks for the vLLM deploy kit; runs anywhere (no Docker or GPU needed)."""

import functools
import os
import re
import shutil
import subprocess
import sys
import tempfile

import pytest
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
KIT = os.path.dirname(HERE)
REPO = os.path.dirname(os.path.dirname(KIT))
sys.path[:0] = [os.path.join(REPO, 'src', 'rtm_vlm'), os.path.join(REPO, 'src', 'rtm_watcher')]

from rtm_vlm.profiles import PROFILES  # noqa: E402

PROFILE_FILES = sorted(f[:-4] for f in os.listdir(os.path.join(KIT, 'profiles'))
                       if f.endswith('.env'))


def read_env(name):
    values = {}
    with open(os.path.join(KIT, 'profiles', f'{name}.env')) as f:
        lines = f.read().splitlines()
    for line in lines:
        if line and not line.startswith('#'):
            key, value = line.split('=', 1)
            values[key] = value
    return lines[0], values


@functools.lru_cache(maxsize=None)
def clean_kit():
    """A copy of the kit without the developer's own .env, which serve.sh would load."""
    path = tempfile.mkdtemp(prefix='rtm-vllm-kit-')
    for name in ('serve.sh', 'compose.yaml'):
        shutil.copy2(os.path.join(KIT, name), path)
    shutil.copytree(os.path.join(KIT, 'profiles'), os.path.join(path, 'profiles'))
    return path


def run(*args, env=None):
    full_env = dict(os.environ, HOME='/home/robot', **(env or {}))
    for key in ('VLLM_API_KEY', 'VLLM_PORT', 'VLLM_BIND'):
        if not env or key not in env:
            full_env.pop(key, None)
    kit = clean_kit()
    return subprocess.run(['bash', os.path.join(kit, 'serve.sh'), *args],
                          capture_output=True, text=True, env=full_env, cwd=kit)


@pytest.mark.parametrize('name', PROFILE_FILES)
def test_profile_files_match_rtm_vlm(name):
    description, env = read_env(name)
    assert description.startswith('# ') and len(description) < 110
    for key in ('MODEL', 'SERVED_NAME', 'MAX_MODEL_LEN', 'GPU_MEM_UTIL', 'MAX_IMAGES',
                'MAX_NUM_SEQS'):
        assert key in env, key
    # Same model and served name as `vlm serve --backend vllm --profile <name>`
    profile = PROFILES[name]
    assert env['MODEL'] == profile.hf_model
    assert env['SERVED_NAME'] == profile.served_name
    assert 0.5 <= float(env['GPU_MEM_UTIL']) <= 0.95


def test_default_profile_exists():
    assert 'qwen3-vl-4b-fp8' in PROFILE_FILES


def test_compose_mirrors_native_args():
    with open(os.path.join(KIT, 'compose.yaml')) as f:
        compose = yaml.safe_load(f)
    service = compose['services']['vllm']
    flags = {item.split('=', 1)[0] for item in service['command']}
    assert flags == {'--model', '--served-model-name', '--max-model-len',
                     '--gpu-memory-utilization', '--max-num-seqs', '--limit-mm-per-prompt'}
    native = run('--dry-run', '--native').stdout
    for flag in flags - {'--model'}:
        assert flag in native, flag
    assert service['ipc'] == 'host'
    assert service['deploy']['resources']['reservations']['devices'][0]['driver'] == 'nvidia'
    assert service['environment'] == {'HF_TOKEN': '${HF_TOKEN:-}',
                                      'VLLM_API_KEY': '${VLLM_API_KEY:-}'}


def test_dry_run_native_command():
    result = run('--dry-run', '--native', 'qwen3-vl-8b-fp8')
    assert result.returncode == 0, result.stderr
    line = [ln for ln in result.stdout.splitlines() if ln.startswith('vllm serve')][0]
    assert line.startswith('vllm serve Qwen/Qwen3-VL-8B-Instruct-FP8 --host 0.0.0.0 --port 8000')
    assert '--served-model-name qwen3-vl-8b-instruct' in line
    assert '--gpu-memory-utilization 0.90' in line
    assert """--limit-mm-per-prompt '{"image": 8, "video": 0}'""" in line


def test_dry_run_docker_and_overrides():
    result = run('--dry-run', env={'VLLM_PORT': '9001', 'VLLM_API_KEY': 's3cret'})
    assert result.returncode == 0, result.stderr
    assert 'profile: qwen3-vl-4b-fp8 (Qwen/Qwen3-VL-4B-Instruct-FP8)' in result.stdout
    assert 'listen: 0.0.0.0:9001' in result.stdout
    assert 'cache: /home/robot/.cache/huggingface' in result.stdout
    assert 'api key: set' in result.stdout and 's3cret' not in result.stdout
    assert 'docker compose -f' in result.stdout


def test_unknown_profile_and_option():
    assert run('--dry-run', 'qwen9-vl').returncode != 0
    bad = run('--frobnicate')
    assert bad.returncode != 0 and 'unknown option' in bad.stderr


def test_list_shows_every_profile():
    out = run('--list').stdout
    for name in PROFILE_FILES:
        assert re.search(rf'^{re.escape(name)}\s+\S', out, re.M), name


@pytest.mark.parametrize('script', ['serve.sh', 'check.sh'])
def test_scripts_parse_and_are_executable(script):
    path = os.path.join(KIT, script)
    assert subprocess.run(['bash', '-n', path]).returncode == 0
    assert os.access(path, os.X_OK)
    if shutil.which('shellcheck'):
        assert subprocess.run(['shellcheck', path]).returncode == 0


def test_check_reports_unreachable_server():
    result = subprocess.run(['bash', os.path.join(KIT, 'check.sh'), 'http://127.0.0.1:9'],
                            capture_output=True, text=True)
    assert result.returncode == 1 and 'no answer' in result.stdout


def test_env_example_is_safe_by_default():
    with open(os.path.join(KIT, '.env.example')) as f:
        text = f.read()
    assert re.search(r'^VLLM_API_KEY=$', text, re.M)
    assert re.search(r'^HF_TOKEN=$', text, re.M)
    assert 'vllm/vllm-openai:v' in text
