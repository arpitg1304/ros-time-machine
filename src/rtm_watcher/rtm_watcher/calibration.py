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

"""Turn a raw anomaly score into a pin decision with a known false-alarm rate."""

from __future__ import annotations

import math
from typing import List, Optional


class ConformalThreshold:
    """Split-conformal threshold calibrated on scores from normal operation.

    With n calibration scores, the threshold is the ceil((n + 1)(1 - alpha))-th
    smallest. A new score from the same (normal) distribution exceeds it with
    probability at most alpha, whatever the scorer's scale or shape.
    """

    def __init__(self, alpha: float = 0.01, min_samples: int = 50, max_samples: int = 200_000):
        if not 0.0 < alpha < 1.0:
            raise ValueError('alpha must be in (0, 1)')
        self.alpha = alpha
        self.min_samples = min_samples
        self.max_samples = max_samples
        self._scores: List[float] = []
        self.threshold: Optional[float] = None

    @classmethod
    def fixed(cls, threshold: float) -> 'ConformalThreshold':
        gate = cls()
        gate.threshold = float(threshold)
        return gate

    @property
    def calibrated(self) -> bool:
        return self.threshold is not None

    @property
    def samples(self) -> int:
        return len(self._scores)

    def required_samples(self) -> int:
        """Fewest scores for which alpha is achievable (k <= n)."""
        return max(self.min_samples, math.ceil(1.0 / self.alpha) - 1)

    def observe(self, score: float) -> None:
        if self.calibrated or not math.isfinite(score):
            return
        if len(self._scores) < self.max_samples:
            self._scores.append(float(score))

    def finalize(self) -> bool:
        """Fix the threshold from the scores seen so far. False if there were too few."""
        n = len(self._scores)
        if n < self.required_samples():
            return False
        k = math.ceil((n + 1) * (1.0 - self.alpha))
        self.threshold = sorted(self._scores)[min(k, n) - 1]
        self._scores = []
        return True

    def exceeds(self, score: float) -> bool:
        return self.calibrated and math.isfinite(score) and score > self.threshold
