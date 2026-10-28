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

"""OpenAI-compatible endpoints worth benchmarking. Pick the model with --model."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict


@dataclass(frozen=True)
class Provider:
    name: str
    endpoint: str
    api_key_env: str      # '' = no key
    remote: bool          # Frames leave this machine / LAN
    notes: str


PROVIDERS: Dict[str, Provider] = {p.name: p for p in [
    Provider('llamacpp', 'http://127.0.0.1:8000/v1', '', False,
             'llama-server started by `vlm serve` (default port 8000).'),
    Provider('vllm', 'http://127.0.0.1:8000/v1', '', False, 'vllm serve on port 8000.'),
    Provider('ollama', 'http://127.0.0.1:11434/v1', '', False,
             'Ollama; the model is the tag, e.g. qwen3-vl:4b.'),
    Provider('openai', 'https://api.openai.com/v1', 'OPENAI_API_KEY', True,
             'Small GPT-5.x models; set extra_body reasoning_effort low for latency.'),
    Provider('gemini', 'https://generativelanguage.googleapis.com/v1beta/openai',
             'GEMINI_API_KEY', True, "Gemini Flash-Lite through Google's OpenAI-compatible API."),
    Provider('deepinfra', 'https://api.deepinfra.com/v1/openai', 'DEEPINFRA_API_KEY', True,
             'Hosted open weights (Qwen3-VL); a priority tier trades price for TTFT.'),
    Provider('openrouter', 'https://openrouter.ai/api/v1', 'OPENROUTER_API_KEY', True,
             'One key, many providers; latency depends on the route it picks.'),
    Provider('fireworks', 'https://api.fireworks.ai/inference/v1', 'FIREWORKS_API_KEY', True,
             'Hosted open weights.'),
]}
