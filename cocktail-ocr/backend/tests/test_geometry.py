import numpy as np
import pytest

from app import imaging
from app.ocr.base import sub_quad


@pytest.mark.parametrize("which", ["cw90", "ccw90", "r180"])
def test_rotation_inverse_maps_pixels_back(which):
    img = np.zeros((40, 70, 3), np.uint8)
    img[5, 60] = (255, 0, 0)  # marker at x=60, y=5
    v = imaging.rotate(img, which)
    ys, xs = np.nonzero(v.image[:, :, 0])
    back = v.to_orig(np.array([[xs[0], ys[0]]]))
    assert back[0].tolist() == [60, 5]


def test_tile_and_scale_inverse():
    img = np.zeros((2000, 2000, 3), np.uint8)
    ts = imaging.tiles(img, tile=900, overlap=180, factor=2.0)
    assert len(ts) >= 4
    t = next(t for t in ts if t.name.startswith("tile_720_720"))
    assert t.to_orig(np.array([[200.0, 100.0]]))[0].tolist() == [820.0, 770.0]
    up = imaging.upscale(img[:100, :100], 3.0)
    assert up.to_orig(np.array([[30.0, 60.0]]))[0].tolist() == [10.0, 20.0]


def test_tiles_cover_whole_image():
    img = np.zeros((1900, 2500, 3), np.uint8)
    covered = np.zeros(img.shape[:2], bool)
    for t in imaging.tiles(img):
        x0, y0 = t.to_orig(np.array([[0.0, 0.0]]))[0]
        h, w = t.image.shape[:2]
        covered[int(y0):int(y0 + h / 2), int(x0):int(x0 + w / 2)] = True
    assert covered.all()


def test_sub_quad_fraction():
    q = sub_quad([[0, 0], [100, 0], [100, 10], [0, 10]], 0.5, 1.0)
    assert q == [[50, 0], [100, 0], [100, 10], [50, 10]]
