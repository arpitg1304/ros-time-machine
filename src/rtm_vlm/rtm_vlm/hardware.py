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

"""Figure out what kind of machine we are on and how much memory a model may use."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
import platform
import shutil
import subprocess
from typing import Dict, List, Optional

GB = 1024 ** 3

# Share of unified memory a model can realistically use
APPLE_GPU_SHARE = 0.65    # macOS caps GPU-wired memory below total RAM
JETSON_GPU_SHARE = 0.75   # CPU and GPU share the same LPDDR on Jetson
CPU_SHARE = 0.5


@dataclass
class DeviceInfo:
    kind: str                 # jetson | nvidia | apple | cpu
    name: str
    memory_gb: float          # What a model may use: VRAM, or a share of unified memory
    system_ram_gb: float
    details: Dict[str, str] = field(default_factory=dict)

    def describe(self) -> str:
        return (f'{self.name} ({self.kind}): ~{self.memory_gb:.1f} GB usable for the model, '
                f'{self.system_ram_gb:.0f} GB RAM')


class SystemProbe:
    """Everything detect() reads from the machine. Tests substitute a fake."""

    def system(self) -> str:
        return platform.system()

    def machine(self) -> str:
        return platform.machine()

    def read(self, path: str) -> Optional[str]:
        try:
            with open(path, 'rb') as f:
                return f.read().decode('utf-8', 'replace').replace('\x00', '').strip()
        except OSError:
            return None

    def run(self, cmd: List[str]) -> Optional[str]:
        if not shutil.which(cmd[0]):
            return None
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False)
        except (OSError, subprocess.SubprocessError):
            return None
        return out.stdout.strip() if out.returncode == 0 else None

    def ram_gb(self) -> float:
        if self.system() == 'Darwin':
            value = self.run(['sysctl', '-n', 'hw.memsize'])
            return int(value) / GB if value and value.isdigit() else 0.0
        try:
            return os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES') / GB
        except (ValueError, OSError, AttributeError):
            return 0.0


def detect(probe: Optional[SystemProbe] = None) -> DeviceInfo:
    probe = probe or SystemProbe()
    ram = probe.ram_gb()

    model = probe.read('/proc/device-tree/model') or ''
    if 'jetson' in model.lower() or probe.read('/etc/nv_tegra_release'):
        name = model or 'NVIDIA Jetson'
        details = {}
        release = probe.read('/etc/nv_tegra_release')
        if release:
            details['l4t'] = release.splitlines()[0][:80]
        return DeviceInfo('jetson', name, ram * JETSON_GPU_SHARE, ram, details)

    smi = probe.run(['nvidia-smi', '--query-gpu=name,memory.total',
                     '--format=csv,noheader,nounits'])
    if smi:
        gpus = []
        for line in smi.splitlines():
            parts = [p.strip() for p in line.split(',')]
            if len(parts) == 2 and parts[1].replace('.', '').isdigit():
                gpus.append((parts[0], float(parts[1]) / 1024))
        if gpus:
            best = max(gpus, key=lambda g: g[1])
            details = {'gpus': '; '.join(f'{n} {m:.0f} GB' for n, m in gpus)}
            return DeviceInfo('nvidia', best[0], best[1], ram, details)

    if probe.system() == 'Darwin' and probe.machine() == 'arm64':
        name = probe.run(['sysctl', '-n', 'machdep.cpu.brand_string']) or 'Apple Silicon'
        return DeviceInfo('apple', name, ram * APPLE_GPU_SHARE, ram)

    name = probe.machine() or 'CPU'
    return DeviceInfo('cpu', f'{probe.system()} {name} (no GPU found)', ram * CPU_SHARE, ram)
