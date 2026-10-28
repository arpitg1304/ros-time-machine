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

"""Pick a model, a runtime and watcher settings that fit the device."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from .hardware import DeviceInfo
from .profiles import ModelProfile, PROFILES

PREFERENCES = ('latency', 'balanced', 'quality')

# Preferred model per preference, best first; the first one that fits wins
_LADDER = {
    'latency': ['qwen3-vl-2b'],
    'balanced': ['qwen3-vl-4b', 'qwen3-vl-2b'],
    'quality': ['qwen3-vl-8b', 'qwen3-vl-4b', 'qwen3-vl-2b'],
}
HEADROOM = 1.25  # Leave room for the KV cache growing and other processes


@dataclass
class Recommendation:
    device: DeviceInfo
    profile: ModelProfile
    backend: str                          # llamacpp | vllm | ollama
    watcher: Dict[str, float] = field(default_factory=dict)   # Watch list overrides
    reasons: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def fits(profile: ModelProfile, device: DeviceInfo) -> bool:
    return profile.memory_gb * HEADROOM <= device.memory_gb


def recommend(device: DeviceInfo, prefer: str = 'balanced') -> Recommendation:
    if prefer not in PREFERENCES:
        raise ValueError(f'prefer must be one of {PREFERENCES}')

    ladder = list(_LADDER[prefer])
    if device.kind == 'cpu':
        ladder = ['qwen3-vl-2b']  # Anything bigger takes many seconds per frame on a CPU
    choice = next((PROFILES[k] for k in ladder if fits(PROFILES[k], device)), None)
    rec = Recommendation(device, choice or PROFILES['qwen3-vl-2b'], 'llamacpp')
    if choice is None:
        rec.warnings.append(
            f'Even {rec.profile.key} (~{rec.profile.memory_gb:.0f} GB) is tight for '
            f'~{device.memory_gb:.1f} GB usable memory; expect swapping or an OOM. Use a LAN '
            'box or a hosted provider instead.')
    rec.reasons.append(
        f'{rec.profile.key} needs ~{rec.profile.memory_gb:g} GB; '
        f'{device.memory_gb:.1f} GB usable ({prefer}).')

    # Watcher settings that keep each call well under the sampling period
    rec.watcher = {'sample_hz': 1.0, 'context_frames': 4, 'max_image_px': 640}
    if device.kind == 'jetson':
        rec.reasons.append('llama.cpp with CUDA is the simplest Jetson runtime '
                           '(jetson-containers ships builds).')
        if device.memory_gb < 12:
            rec.watcher.update(context_frames=3, max_image_px=512)
            rec.warnings.append('On Orin Nano, switch to the 25 W or MAXN SUPER power mode for '
                                'usable latency (sudo nvpmodel -q lists the modes).')
        rec.warnings.append('Alternative: NVIDIA documents Cosmos-Reason2-2B with vLLM on '
                            'Jetson (profile cosmos-reason2-2b, backend vllm).')
    elif device.kind == 'nvidia':
        if device.memory_gb >= 16:
            rec.reasons.append('16 GB+ VRAM: vLLM gives higher throughput if several robots '
                               'share this GPU (backend vllm).')
    elif device.kind == 'apple':
        rec.reasons.append('llama.cpp runs on Metal out of the box (brew install llama.cpp).')
    else:
        rec.watcher.update(sample_hz=0.5, context_frames=2, max_image_px=448)
        rec.warnings.append('No GPU: expect several seconds per call. Fine for trying the '
                            'pipeline; run the VLM on a GPU machine for real use.')
    return rec
