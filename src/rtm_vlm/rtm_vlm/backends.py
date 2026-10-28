# Copyright 2024 ROS Time Machine Contributors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Start an OpenAI-compatible VLM server (llama.cpp, vLLM or Ollama) and wait until it is up."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
import re
import shutil
import signal
import subprocess
import time
from typing import Dict, List, Optional, Set
import urllib.error
import urllib.request

from .profiles import ModelProfile

BACKENDS = ('llamacpp', 'vllm', 'ollama')
OLLAMA_PORT = 11434
CONTEXT_TOKENS = 8192   # A 4-8 frame prompt plus a short JSON answer fits easily
MAX_IMAGES = 8          # Per request (judge_frames defaults to 8)
VLLM_GPU_SHARE = 0.85   # Leave room for a desktop session on the same GPU
VLLM_MAX_SEQS = 4       # A few robots, not a public service: keeps activation memory low


class BackendError(RuntimeError):
    pass


@dataclass
class LaunchPlan:
    """What to run, and where the watcher should point afterwards."""

    backend: str
    server: List[str]                  # The long-lived server process
    endpoint: str
    model: str                         # Name to put in the watch list
    after_ready: List[List[str]] = field(default_factory=list)  # Run once the server answers
    env: Dict[str, str] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)

    def shell(self) -> str:
        env = ''.join(f'{k}={v} ' for k, v in self.env.items())
        lines = [env + ' '.join(_quote(a) for a in self.server)]
        lines += [env + ' '.join(_quote(a) for a in cmd) for cmd in self.after_ready]
        return '\n'.join(lines)


def _quote(arg: str) -> str:
    return arg if re.match(r'^[\w@%+=:,./-]+$', arg) else "'" + arg.replace("'", "'\\''") + "'"


def supported_flags(binary: str) -> Set[str]:
    """Long options a binary advertises in --help (empty if it cannot be run)."""
    try:
        out = subprocess.run([binary, '--help'], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return set()
    return set(re.findall(r'(?<![\w-])(--[a-z0-9][a-z0-9-]*)', out.stdout + out.stderr))


def find_binary(backend: str) -> Optional[str]:
    return shutil.which({'llamacpp': 'llama-server', 'vllm': 'vllm', 'ollama': 'ollama'}[backend])


def plan(profile: ModelProfile, backend: str = 'llamacpp', host: str = '127.0.0.1',
         port: int = 8000, alias: str = '', gpu: bool = True,
         flags: Optional[Set[str]] = None, extra_args: Optional[List[str]] = None) -> LaunchPlan:
    """Build the commands for a backend. `flags` (from supported_flags) gates optional flags."""
    if backend not in BACKENDS:
        raise BackendError(f'backend must be one of {BACKENDS}')
    extra = list(extra_args or [])

    if backend == 'llamacpp':
        if not profile.gguf_repo:
            raise BackendError(f'{profile.key} has no GGUF build; use --backend vllm')
        name = alias or profile.served_name
        cmd = ['llama-server', '-hf', profile.gguf_ref, '--host', host, '--port', str(port),
               '--alias', name, '-c', str(CONTEXT_TOKENS), '-np', '1',
               '-ngl', '99' if gpu else '0']
        # Optional flags only when this build has them (older builds lack some)
        for flag in ('--jinja',):
            if flags is None or flag in flags:
                cmd.append(flag)
        notes = ['First start downloads the model and its vision projector from Hugging Face '
                 '(set LLAMA_CACHE to choose where).']
        return LaunchPlan('llamacpp', cmd + extra, f'http://{host}:{port}/v1', name,
                          notes=notes)

    if backend == 'vllm':
        name = alias or profile.served_name
        # Same flags as deploy/vllm; video off frees the memory reserved for long videos
        cmd = ['vllm', 'serve', profile.hf_model, '--host', host, '--port', str(port),
               '--served-model-name', name, '--max-model-len', str(CONTEXT_TOKENS),
               '--gpu-memory-utilization', str(VLLM_GPU_SHARE),
               '--max-num-seqs', str(VLLM_MAX_SEQS),
               '--limit-mm-per-prompt', json.dumps({'image': MAX_IMAGES, 'video': 0})]
        notes = ['vLLM needs a CUDA GPU (on Jetson, use the jetson-containers vLLM image). '
                 'For a Docker setup with profiles and LAN access, see deploy/vllm.']
        if 'cosmos' in profile.key:
            notes.append('Cosmos-Reason2 is gated: accept the NVIDIA Open Model License on '
                         'Hugging Face and log in (huggingface-cli login) first.')
        return LaunchPlan('vllm', cmd + extra, f'http://{host}:{port}/v1', name, notes=notes)

    # Ollama serves on its own port and names models by tag
    if not profile.ollama_tag:
        raise BackendError(f'{profile.key} is not in the Ollama library; use another backend')
    env = {'OLLAMA_HOST': f'{host}:{OLLAMA_PORT}'}
    steps = [['ollama', 'pull', profile.ollama_tag]]
    if alias and alias != profile.ollama_tag:
        steps.append(['ollama', 'cp', profile.ollama_tag, alias])  # Ollama's way to alias
    return LaunchPlan(
        'ollama', ['ollama', 'serve'] + extra, f'http://{host}:{OLLAMA_PORT}/v1',
        alias or profile.ollama_tag, after_ready=steps, env=env,
        notes=['An Ollama service that is already running is reused.',
               'Ollama always listens on port 11434; point the watch list there.'])


def endpoint_ready(endpoint: str, api_key: str = '', timeout_s: float = 3.0) -> bool:
    """True once GET {endpoint}/models answers 200 (llama.cpp returns 503 while loading)."""
    request = urllib.request.Request(endpoint.rstrip('/') + '/models')
    if api_key:
        request.add_header('Authorization', f'Bearer {api_key}')
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


class ManagedServer:
    """Runs a LaunchPlan's server in the background, or reuses one already answering."""

    def __init__(self, launch: LaunchPlan, log_path: Optional[str] = None):
        self.launch = launch
        self.log_path = log_path
        self.process: Optional[subprocess.Popen] = None
        self.reused = False
        self._log = None
        self._env = dict(os.environ, **launch.env)

    def start(self) -> None:
        if endpoint_ready(self.launch.endpoint):
            self.reused = True  # Something already serves this endpoint; leave it alone
            return
        self._log = open(self.log_path, 'ab') if self.log_path else None
        try:
            self.process = subprocess.Popen(
                self.launch.server, env=self._env, stdout=self._log,
                stderr=subprocess.STDOUT if self._log else None, start_new_session=True)
        except OSError as e:
            raise BackendError(f'cannot start {self.launch.server[0]}: {e}') from e

    def wait_ready(self, timeout_s: float = 600.0, poll_s: float = 1.0) -> bool:
        """Wait for the endpoint, then run after_ready steps.

        False on timeout, if the server exits, or if a step fails.
        """
        deadline = time.monotonic() + timeout_s
        while not endpoint_ready(self.launch.endpoint):
            if self.process is not None and self.process.poll() is not None:
                return False
            if time.monotonic() >= deadline:
                return False
            time.sleep(poll_s)
        for cmd in self.launch.after_ready:
            if subprocess.run(cmd, env=self._env, check=False).returncode != 0:
                return False
        return True

    def stop(self, timeout_s: float = 10.0) -> None:
        if self.process is not None and self.process.poll() is None:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
                self.process.wait(timeout=timeout_s)
            except (OSError, subprocess.TimeoutExpired):
                self.process.kill()
                self.process.wait()
        if self._log:
            self._log.close()
            self._log = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()
