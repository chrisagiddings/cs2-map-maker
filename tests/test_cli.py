import pytest

from make_map import parse_args


def test_center_and_bbox_are_exclusive():
    with pytest.raises(SystemExit):
        parse_args(["--center", "1,2", "--bbox", "0,0,1,1", "--name", "x"])
    with pytest.raises(SystemExit):
        parse_args(["--name", "x"])


def test_defaults():
    a = parse_args(["--center", "35.0456,-85.3097", "--name", "chatt"])
    assert a.exaggeration == 1.0 and a.sea_level is None and a.water_surface is None
    assert a.oversample == "auto" and a.deterrace == "auto" and not a.no_burn
    assert a.min_order == 2 and a.depth_scale == 1.0 and a.epsg is None and a.out == "out"


def test_brief_examples_parse():
    a = parse_args(["--center", "35.0456,-85.3097", "--name", "chattanooga", "--exaggeration", "1.0"])
    assert a.center == "35.0456,-85.3097"
    b = parse_args(["--bbox", "-85.4,35.0,-85.2,35.1", "--name", "foo", "--sea-level", "200"])
    assert b.bbox == "-85.4,35.0,-85.2,35.1" and b.sea_level == 200.0


def test_negative_leading_coordinate():
    # western hemisphere: value starts with '-' and must not be read as an option
    a = parse_args(["--center", "-33.9,151.2", "--name", "syd"])
    assert a.center == "-33.9,151.2"
    b = parse_args(["--bbox", "-122.5,37.7,-122.3,37.8", "--name", "sf"])
    assert b.bbox == "-122.5,37.7,-122.3,37.8"
