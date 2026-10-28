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

"""Small VLMs known to work as semantic-trigger watchers, and where to get them."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict


@dataclass(frozen=True)
class ModelProfile:
    key: str
    served_name: str          # Model name the watcher sends (llama.cpp --alias)
    params_b: float
    memory_gb: float          # Rough peak at Q4 with an 8K context, incl. vision projector
    gguf_repo: str            # llama.cpp: -hf <repo>:<quant> (downloads the mmproj too)
    gguf_quant: str
    hf_model: str             # vLLM: full-precision weights
    ollama_tag: str           # Ollama library tag ('' = not in the library)
    license: str
    notes: str

    @property
    def gguf_ref(self) -> str:
        return f'{self.gguf_repo}:{self.gguf_quant}' if self.gguf_repo else ''


PROFILES: Dict[str, ModelProfile] = {p.key: p for p in [
    ModelProfile(
        key='qwen3-vl-2b',
        served_name='qwen3-vl-2b-instruct',
        params_b=2.0,
        memory_gb=3.0,
        gguf_repo='Qwen/Qwen3-VL-2B-Instruct-GGUF',
        gguf_quant='Q4_K_M',
        hf_model='Qwen/Qwen3-VL-2B-Instruct',
        ollama_tag='qwen3-vl:2b',
        license='Apache-2.0',
        notes='Fits an 8 GB Jetson Orin Nano; the latency pick. Weakest at subtle events.',
    ),
    ModelProfile(
        key='qwen3-vl-4b',
        served_name='qwen3-vl-4b-instruct',
        params_b=4.0,
        memory_gb=5.0,
        gguf_repo='Qwen/Qwen3-VL-4B-Instruct-GGUF',
        gguf_quant='Q4_K_M',
        hf_model='Qwen/Qwen3-VL-4B-Instruct',
        ollama_tag='qwen3-vl:4b',
        license='Apache-2.0',
        notes='The reference watcher: the best quality per second on 6 GB+ GPUs and Apple '
              'Silicon.',
    ),
    ModelProfile(
        key='qwen3-vl-8b',
        served_name='qwen3-vl-8b-instruct',
        params_b=8.0,
        memory_gb=8.5,
        gguf_repo='Qwen/Qwen3-VL-8B-Instruct-GGUF',
        gguf_quant='Q4_K_M',
        hf_model='Qwen/Qwen3-VL-8B-Instruct',
        ollama_tag='qwen3-vl:8b',
        license='Apache-2.0',
        notes='Quality pick for 12 GB+ GPUs or a LAN box; roughly 2x the latency of 4B.',
    ),
    ModelProfile(
        key='qwen3-vl-4b-fp8',
        served_name='qwen3-vl-4b-instruct',
        params_b=4.0,
        memory_gb=6.0,
        gguf_repo='',
        gguf_quant='',
        hf_model='Qwen/Qwen3-VL-4B-Instruct-FP8',
        ollama_tag='',
        license='Apache-2.0',
        notes='Official FP8 weights for vLLM. Needs an RTX 40-series or newer GPU (compute '
              'capability 8.9+). The vLLM default on 12-16 GB cards.',
    ),
    ModelProfile(
        key='qwen3-vl-8b-fp8',
        served_name='qwen3-vl-8b-instruct',
        params_b=8.0,
        memory_gb=11.0,
        gguf_repo='',
        gguf_quant='',
        hf_model='Qwen/Qwen3-VL-8B-Instruct-FP8',
        ollama_tag='',
        license='Apache-2.0',
        notes='Official FP8 weights for vLLM; the quality pick on a 16 GB RTX 40-series card.',
    ),
    ModelProfile(
        key='cosmos-reason2-2b',
        served_name='cosmos-reason2-2b',
        params_b=2.0,
        memory_gb=7.0,
        gguf_repo='',
        gguf_quant='',
        hf_model='nvidia/Cosmos-Reason2-2B',
        ollama_tag='',
        license='NVIDIA Open Model License',
        notes='Qwen3-VL-2B post-trained by NVIDIA for physical reasoning. NVIDIA documents it '
              'on Jetson with vLLM (jetson-ai-lab.com). Accept the license before use.',
    ),
]}

DEFAULT_PROFILE = 'qwen3-vl-4b'
