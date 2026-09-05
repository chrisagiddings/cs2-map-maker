import numpy as np
import pytest
from PIL import Image

from src import spec
from src.export import (
    ExportError, write_heightmap, verify_heightmap, check_worldmap_center,
    downsample_playable_to_world, worldmap_center_slice, write_resource_mask,
)

N = spec.HEIGHTMAP_SIZE


@pytest.fixture(scope="module")
def ramp():
    # Diagonal ramp spanning the full uint16 range, plus some texture so the
    # unique-value count is meaningful.
    y, x = np.mgrid[0:N, 0:N]
    a = (x + y) / (2 * (N - 1)) * 65535.0
    a += np.sin(x / 37.0) * 200.0
    a = np.clip(np.rint(a), 0, 65535).astype(np.uint16)
    a[0, 0], a[-1, -1] = 0, 65535   # pin the extremes so range assertions are exact
    return a


def test_spec_constants():
    assert spec.PLAYABLE_M_PER_PX == 3.5
    assert spec.WORLD_M_PER_PX == 14.0
    assert spec.WORLD_CENTER_PX == 1024


def test_roundtrip_full_size(tmp_path, ramp):
    p = tmp_path / "hm.png"
    stats = write_heightmap(p, ramp)
    assert stats.shape == (N, N)
    assert stats.dtype == "uint16"
    assert stats.min_px == 0 and stats.max_px == 65535
    assert not (tmp_path / "hm.png.aux.xml").exists()
    # Belt and braces: a third reader (imageio) must agree too.
    import imageio.v3 as iio
    back = iio.imread(p)
    assert back.dtype == np.uint16 and back.shape == (N, N)
    assert np.array_equal(back, ramp)


def test_rejects_wrong_shape():
    with pytest.raises(ExportError, match="shape"):
        write_heightmap("nope.png", np.zeros((1024, 1024), np.uint16))


def test_rejects_wrong_dtype():
    with pytest.raises(ExportError, match="dtype"):
        write_heightmap("nope.png", np.zeros((N, N), np.uint8))


def test_rejects_multichannel():
    with pytest.raises(ExportError, match="2-D"):
        write_heightmap("nope.png", np.zeros((N, N, 3), np.uint16))


def test_verify_catches_8bit_file(tmp_path):
    # The classic failure: something wrote an 8-bit PNG at the right size.
    p = tmp_path / "eightbit.png"
    Image.fromarray((np.arange(N * N) % 256).astype(np.uint8).reshape(N, N)).save(p)
    with pytest.raises(ExportError, match="uint16"):
        verify_heightmap(p)


def test_verify_catches_rgb_file(tmp_path):
    p = tmp_path / "rgb.png"
    Image.fromarray(np.zeros((N, N, 3), np.uint8)).save(p)
    with pytest.raises(ExportError, match="single-channel"):
        verify_heightmap(p)


def test_verify_catches_flat_file(tmp_path):
    p = tmp_path / "flat.png"
    write_heightmap(p, np.full((N, N), 12345, np.uint16), verify=False)
    with pytest.raises(ExportError, match="spans"):
        verify_heightmap(p)


def test_worldmap_center_consistency(ramp):
    world = np.zeros((N, N), np.uint16)
    rs, cs = worldmap_center_slice()
    assert rs == slice(1536, 2560) and cs == slice(1536, 2560)
    world[rs, cs] = downsample_playable_to_world(ramp)
    assert check_worldmap_center(world, ramp) <= 1
    world[rs, cs] += 50
    with pytest.raises(ExportError, match="world map centre"):
        check_worldmap_center(world, ramp)


def test_downsample_is_block_mean():
    a = np.zeros((N, N), np.uint16)
    a[3, 3] = 1600
    d = downsample_playable_to_world(a)
    assert d.shape == (1024, 1024) and d[0, 0] == 100


def test_resource_mask(tmp_path):
    m = np.zeros((256, 256), np.uint8)
    m[100:150, 100:150] = 255
    p = tmp_path / "ore.png"
    write_resource_mask(p, m)
    assert np.array_equal(np.array(Image.open(p)), m)
    with pytest.raises(ExportError, match="shape"):
        write_resource_mask(p, np.zeros((128, 128), np.uint8))
