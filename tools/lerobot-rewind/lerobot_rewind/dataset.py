"""Read LeRobot datasets (v2.1 and v3.0) from a local folder or the Hugging Face Hub."""

from __future__ import annotations

from dataclasses import dataclass
import glob
import json
import os
from typing import Dict, List, Optional, Tuple

import pandas as pd

IMAGE_PREFIX = 'observation.images.'


@dataclass
class Episode:
    index: int
    length: int
    tasks: List[str]
    data: pd.DataFrame                              # rows of this episode, in frame order
    videos: Dict[str, Tuple[str, float, float]]     # camera -> (mp4 path, from_s, to_s)


def resolve(dataset: str) -> str:
    """A local dataset folder, the LeRobot cache entry for a repo id, or a fresh Hub download."""
    if os.path.isfile(os.path.join(dataset, 'meta', 'info.json')):
        return dataset
    cache = os.path.join(os.environ.get('HF_LEROBOT_HOME', os.path.expanduser('~/.cache/huggingface/lerobot')),
                         dataset)
    if os.path.isfile(os.path.join(cache, 'meta', 'info.json')):
        return cache
    try:
        from huggingface_hub import snapshot_download
    except ImportError as e:
        raise SystemExit(f'{dataset} is not a local LeRobot dataset; to download it from the Hub, '
                         'pip install "lerobot-rewind[hub]"') from e
    return snapshot_download(repo_id=dataset, repo_type='dataset')


class LeRobotDataset:

    def __init__(self, root: str):
        self.root = root
        self.info = json.load(open(os.path.join(root, 'meta', 'info.json')))
        self.version = self.info.get('codebase_version', '')
        self.fps = float(self.info['fps'])
        features = self.info['features']
        self.video_keys = [k for k, v in features.items() if v.get('dtype') == 'video']
        self.cameras = [k[len(IMAGE_PREFIX):] if k.startswith(IMAGE_PREFIX) else k for k in self.video_keys]
        self.state_names = self._names(features.get('observation.state'))
        self.action_names = self._names(features.get('action'))
        self._episodes = self._read_episodes()
        self._data_cache: Dict[str, pd.DataFrame] = {}

    @staticmethod
    def _names(feature: Optional[dict]) -> List[str]:
        if not feature:
            return []
        names = feature.get('names') or []
        if isinstance(names, dict):  # some datasets: {"motors": [...]}
            names = next(iter(names.values()), [])
        n = feature.get('shape', [len(names)])[0]
        names = [str(x) for x in names] or [f'joint_{i}' for i in range(n)]
        return [x[:-4] if x.endswith('.pos') else x for x in names]

    def _read_episodes(self) -> pd.DataFrame:
        if self.version.startswith('v3'):
            files = sorted(glob.glob(os.path.join(self.root, 'meta', 'episodes', '*', '*.parquet')))
            return pd.concat(pd.read_parquet(f) for f in files).sort_values('episode_index').reset_index(drop=True)
        rows = [json.loads(line) for line in open(os.path.join(self.root, 'meta', 'episodes.jsonl')) if line.strip()]
        return pd.DataFrame(rows).sort_values('episode_index').reset_index(drop=True)

    @property
    def episode_indices(self) -> List[int]:
        return [int(i) for i in self._episodes['episode_index']]

    def _parquet(self, path: str) -> pd.DataFrame:
        if path not in self._data_cache:
            self._data_cache = {path: pd.read_parquet(path)}  # one file at a time: they can be large
        return self._data_cache[path]

    def episode(self, index: int) -> Episode:
        row = self._episodes[self._episodes.episode_index == index].iloc[0]
        length = int(row['length'])
        tasks = list(row['tasks']) if 'tasks' in row and row['tasks'] is not None else []
        videos = {}
        if self.version.startswith('v3'):
            data_path = os.path.join(self.root, self.info['data_path'].format(
                chunk_index=int(row['data/chunk_index']), file_index=int(row['data/file_index'])))
            for key, cam in zip(self.video_keys, self.cameras):
                path = os.path.join(self.root, self.info['video_path'].format(
                    video_key=key, chunk_index=int(row[f'videos/{key}/chunk_index']),
                    file_index=int(row[f'videos/{key}/file_index'])))
                videos[cam] = (path, float(row[f'videos/{key}/from_timestamp']),
                               float(row[f'videos/{key}/to_timestamp']))
        else:
            chunk = index // int(self.info.get('chunks_size', 1000))
            data_path = os.path.join(self.root, self.info['data_path'].format(
                episode_chunk=chunk, episode_index=index))
            for key, cam in zip(self.video_keys, self.cameras):
                path = os.path.join(self.root, self.info['video_path'].format(
                    episode_chunk=chunk, video_key=key, episode_index=index))
                videos[cam] = (path, 0.0, length / self.fps)
        data = self._parquet(data_path)
        data = data[data.episode_index == index].sort_values('frame_index')
        return Episode(index, length, tasks, data, videos)
