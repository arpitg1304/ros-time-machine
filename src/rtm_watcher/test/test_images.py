import io

import pytest

from rtm_watcher import images

PIL = pytest.importorskip('PIL.Image')


def jpeg(w, h, color=(200, 40, 40)):
    out = io.BytesIO()
    PIL.new('RGB', (w, h), color).save(out, 'JPEG')
    return out.getvalue()


def size(data):
    return PIL.open(io.BytesIO(data)).size


def test_compressed_downscaled_to_max_px():
    out = images.compressed_to_jpeg(jpeg(1280, 720), 640)
    assert size(out) == (640, 360)


def test_compressed_small_image_kept_small():
    assert size(images.compressed_to_jpeg(jpeg(320, 240), 640)) == (320, 240)


def test_compressed_garbage_rejected():
    assert images.compressed_to_jpeg(b'not an image', 640) is None


@pytest.mark.parametrize('encoding,channels', [('rgb8', 3), ('bgr8', 3), ('mono8', 1),
                                               ('rgba8', 4)])
def test_raw_encodings(encoding, channels):
    w, h = 8, 4
    data = bytes([10, 20, 30, 255][:channels] * (w * h))
    out = images.raw_to_jpeg(encoding, w, h, w * channels, data, 640)
    assert size(out) == (w, h)


def test_raw_bgr_channel_order():
    w, h = 16, 16
    out = images.raw_to_jpeg('bgr8', w, h, w * 3, bytes([0, 0, 255] * (w * h)), 640)
    r, g, b = PIL.open(io.BytesIO(out)).convert('RGB').getpixel((8, 8))
    assert r > 200 and b < 50  # Blue-first bytes became a red pixel


def test_raw_row_padding_stripped():
    w, h, step = 3, 2, 12  # 9 bytes of pixels + 3 padding per row
    row = bytes([50] * 9) + b'\x00\x00\x00'
    assert size(images.raw_to_jpeg('rgb8', w, h, step, row * h, 640)) == (3, 2)


def test_raw_unsupported_or_short():
    assert images.raw_to_jpeg('16UC1', 4, 4, 8, bytes(32), 640) is None
    assert images.raw_to_jpeg('rgb8', 4, 4, 12, bytes(10), 640) is None
