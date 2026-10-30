"""Decode a stretch of a LeRobot video (AV1, H.264, ...) into JPEG frames with ffmpeg."""

from __future__ import annotations

import shutil
import subprocess
from typing import List, Optional


def require_ffmpeg() -> None:
    if not shutil.which('ffmpeg'):
        raise SystemExit('ffmpeg not found: install it (apt install ffmpeg, brew install ffmpeg)')


def split_jpegs(stream: bytes) -> List[bytes]:
    """Split an MJPEG byte stream into frames. JPEG stuffs 0xFF in entropy-coded data, so the
    start (FFD8) and end (FFD9) markers only occur at frame boundaries."""
    frames, start = [], 0
    while True:
        soi = stream.find(b'\xff\xd8', start)
        if soi < 0:
            return frames
        eoi = stream.find(b'\xff\xd9', soi + 2)
        if eoi < 0:
            return frames
        frames.append(stream[soi:eoi + 2])
        start = eoi + 2


def jpeg_frames(path: str, start_s: float, end_s: float, quality: int = 4,
                expected: Optional[int] = None) -> List[bytes]:
    """JPEG bytes for each frame in [start_s, end_s). With `expected`, trimmed or padded with
    the last frame to exactly that many, so frames line up with the dataset's rows."""
    cmd = ['ffmpeg', '-v', 'error', '-ss', f'{start_s:.6f}', '-i', path, '-t', f'{end_s - start_s:.6f}',
           '-f', 'image2pipe', '-c:v', 'mjpeg', '-q:v', str(quality), '-']
    frames = split_jpegs(subprocess.run(cmd, capture_output=True, check=True).stdout)
    if expected is not None and frames:
        frames = (frames + [frames[-1]] * expected)[:expected]
    return frames
