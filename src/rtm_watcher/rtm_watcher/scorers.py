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

"""ROS-free scoring functions behind the built-in scorer nodes."""

from __future__ import annotations

from typing import Dict, Sequence


class EffortSpikeScore:
    """max over joints of |effort - EMA(effort)| / (EMA(|deviation|) + eps)."""

    def __init__(self, alpha: float = 0.05, eps: float = 1e-3):
        self.alpha = alpha
        self.eps = eps
        self._mean: Dict[str, float] = {}
        self._spread: Dict[str, float] = {}

    def update(self, names: Sequence[str], efforts: Sequence[float]) -> float:
        score = 0.0
        for name, effort in zip(names, efforts):
            mean = self._mean.get(name)
            if mean is None:
                self._mean[name] = effort
                self._spread[name] = 0.0
                continue
            deviation = abs(effort - mean)
            score = max(score, deviation / (self._spread[name] + self.eps))
            self._mean[name] = mean + self.alpha * (effort - mean)
            self._spread[name] += self.alpha * (deviation - self._spread[name])
        return score
