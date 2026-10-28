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

"""Turn ROS image messages into small JPEGs for the VLM.

OpenCV (what cv_bridge already pulls in) or Pillow is used when available. With
neither, CompressedImage JPEGs pass through unchanged and raw images are skipped.
"""

from __future__ import annotations

import io
from typing import Optional

try:  # pragma: no cover - availability depends on the robot
    import cv2
    import numpy as np
except ImportError:  # pragma: no cover
    cv2 = None
    np = None

try:  # pragma: no cover
    from PIL import Image as PILImage
except ImportError:  # pragma: no cover
    PILImage = None

JPEG_QUALITY = 85

# sensor_msgs/Image encodings we can convert: name -> (channels, PIL mode, is_bgr)
_RAW_ENCODINGS = {
    'rgb8': (3, 'RGB', False),
    'bgr8': (3, 'RGB', True),
    'rgba8': (4, 'RGBA', False),
    'bgra8': (4, 'RGBA', True),
    'mono8': (1, 'L', False),
}


def have_encoder() -> bool:
    return cv2 is not None or PILImage is not None


def _is_jpeg(data: bytes) -> bool:
    return data[:3] == b'\xff\xd8\xff'


def compressed_to_jpeg(data: bytes, max_px: int) -> Optional[bytes]:
    """Downscale a CompressedImage payload to fit max_px, re-encoding as JPEG."""
    data = bytes(data)
    if cv2 is not None:
        image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            return None
        return _cv2_encode(image, max_px)
    if PILImage is not None:
        try:
            image = PILImage.open(io.BytesIO(data))
            return _pil_encode(image, max_px)
        except OSError:
            return None
    return data if _is_jpeg(data) else None


def raw_to_jpeg(encoding: str, width: int, height: int, step: int, data: bytes,
                max_px: int) -> Optional[bytes]:
    """Encode a sensor_msgs/Image of a common 8-bit encoding as a downscaled JPEG."""
    spec = _RAW_ENCODINGS.get(encoding.lower())
    if spec is None or width <= 0 or height <= 0:
        return None
    channels, mode, is_bgr = spec
    row = width * channels
    buf = bytes(data)
    if step != row:  # Strip row padding
        buf = b''.join(buf[r * step:r * step + row] for r in range(height))
    if len(buf) < row * height:
        return None

    if cv2 is not None:
        image = np.frombuffer(buf, np.uint8).reshape(height, width, channels) \
            if channels > 1 else np.frombuffer(buf, np.uint8).reshape(height, width)
        if channels == 3 and not is_bgr:
            image = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
        elif channels == 4:
            image = cv2.cvtColor(image, cv2.COLOR_BGRA2BGR if is_bgr else cv2.COLOR_RGBA2BGR)
        return _cv2_encode(image, max_px)
    if PILImage is not None:
        image = PILImage.frombytes(mode, (width, height), buf)
        if is_bgr:
            channels_split = image.split()
            image = PILImage.merge(mode, (channels_split[2], channels_split[1],
                                          channels_split[0]) + tuple(channels_split[3:]))
        return _pil_encode(image, max_px)
    return None


def _cv2_encode(image, max_px: int) -> Optional[bytes]:
    h, w = image.shape[:2]
    scale = max_px / max(h, w)
    if scale < 1.0:
        image = cv2.resize(image, (max(1, int(w * scale)), max(1, int(h * scale))),
                           interpolation=cv2.INTER_AREA)
    ok, encoded = cv2.imencode('.jpg', image, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
    return encoded.tobytes() if ok else None


def _pil_encode(image, max_px: int) -> bytes:
    image = image.convert('RGB')
    image.thumbnail((max_px, max_px))
    out = io.BytesIO()
    image.save(out, 'JPEG', quality=JPEG_QUALITY)
    return out.getvalue()
