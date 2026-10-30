"""Contact sheets for labelling episodes by eye: one row per episode, a frame every N s."""

from __future__ import annotations

import glob
import subprocess
import tempfile
from typing import List, Optional, Sequence, Tuple

from .dataset import LeRobotDataset
from .video import require_ffmpeg


def contact_sheets(dataset: LeRobotDataset, camera: str, out_prefix: str, every_s: float = 2.0,
                   crop: Optional[Tuple[int, int, int, int]] = None, tile: Tuple[int, int] = (200, 150),
                   per_sheet: int = 15, episodes: Optional[Sequence[int]] = None) -> List[str]:
    try:
        from PIL import Image, ImageDraw
    except ImportError as e:
        raise SystemExit('contact sheets need Pillow: pip install "lerobot-rewind[sheets]"') from e
    require_ffmpeg()
    if camera not in dataset.cameras:
        raise SystemExit(f'no camera {camera!r}; the dataset has {dataset.cameras}')
    vf = (f'crop={crop[2]}:{crop[3]}:{crop[0]}:{crop[1]},' if crop else '') + f'scale={tile[0]}:{tile[1]}'
    rows = []
    for i in (episodes if episodes is not None else dataset.episode_indices):
        ep = dataset.episode(i)
        path, start, end = ep.videos[camera]
        times = [start + k * every_s for k in range(int((end - start) / every_s) + 1)] + [max(start, end - 0.05)]
        tiles = []
        with tempfile.TemporaryDirectory() as tmp:
            for k, tt in enumerate(times):
                out = f'{tmp}/{k:03d}.png'
                subprocess.run(['ffmpeg', '-v', 'error', '-ss', f'{tt:.3f}', '-i', path, '-frames:v', '1',
                                '-vf', vf, out], check=True)
                found = glob.glob(out)
                tiles.append((tt - start, Image.open(found[0]).copy() if found else None))
        rows.append((i, tiles))
    paths = []
    for s in range(0, len(rows), per_sheet):
        chunk = rows[s:s + per_sheet]
        cols = max(len(r[1]) for r in chunk)
        sheet = Image.new('RGB', (48 + cols * tile[0], len(chunk) * tile[1]), (20, 20, 20))
        d = ImageDraw.Draw(sheet)
        for r, (i, tiles) in enumerate(chunk):
            y = r * tile[1]
            d.text((6, y + tile[1] // 2 - 6), f'ep{i}', fill=(255, 255, 0))
            for c, (tt, im) in enumerate(tiles):
                x = 48 + c * tile[0]
                if im is not None:
                    sheet.paste(im, (x, y))
                d.rectangle([x, y, x + 44, y + 12], fill=(0, 0, 0))
                d.text((x + 2, y), f'{tt:.1f}s' if c < len(tiles) - 1 else 'end', fill=(255, 255, 0))
        path = f'{out_prefix}_{s // per_sheet}.png'
        sheet.save(path)
        paths.append(path)
    return paths
