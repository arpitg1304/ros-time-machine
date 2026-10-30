"""When a joint crosses a threshold: gripper closes and releases, for timing labels."""

from __future__ import annotations

from typing import Iterator, Tuple

import numpy as np

from .dataset import LeRobotDataset


def crossings(dataset: LeRobotDataset, joint: str, below: float) -> Iterator[Tuple[int, str, float]]:
    """(episode, 'close' | 'open', t_s) each time `joint` goes below / back above `below`."""
    if joint not in dataset.state_names:
        raise SystemExit(f'no joint {joint!r}; observation.state has {dataset.state_names}')
    j = dataset.state_names.index(joint)
    for i in dataset.episode_indices:
        ep = dataset.episode(i)
        values = np.stack(ep.data['observation.state'].values)[:, j]
        ts = ep.data['timestamp'].values.astype(float)
        closed = values < below
        for k in range(1, len(values)):
            if closed[k] != closed[k - 1]:
                yield i, 'close' if closed[k] else 'open', float(ts[k])
