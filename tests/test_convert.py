"""Tests for geometry, monochrome conversion, and bit packing."""

import numpy as np

from macbridge.config import ConvertSettings
from macbridge.convert import MonoFrame, convert_frame


def test_convert_produces_target_geometry_and_binary_pixels() -> None:
    source = np.full((20, 30), 255, dtype=np.uint8)
    settings = ConvertSettings(
        width=512,
        height=342,
        mode="threshold",
        threshold=128,
        fit="contain",
    )

    result = convert_frame(source, settings)

    assert result.pixels.shape == (342, 512)
    assert result.pixels.dtype == np.uint8
    assert set(np.unique(result.pixels)) <= {0, 1}


def test_threshold_and_invert_control_polarity() -> None:
    source = np.array([[0, 64, 127, 128, 129, 200, 255, 10]], dtype=np.uint8)
    settings = ConvertSettings(
        width=8,
        height=1,
        mode="threshold",
        threshold=128,
        fit="stretch",
    )

    result = convert_frame(source, settings)
    inverted = convert_frame(source, ConvertSettings(
        width=8,
        height=1,
        mode="threshold",
        threshold=128,
        fit="stretch",
        invert=True,
    ))

    np.testing.assert_array_equal(
        result.pixels,
        [[0, 0, 0, 1, 1, 1, 1, 0]],
    )
    np.testing.assert_array_equal(inverted.pixels, 1 - result.pixels)


def test_contain_preserves_aspect_ratio_with_black_borders() -> None:
    source = np.full((4, 4), 255, dtype=np.uint8)
    settings = ConvertSettings(
        width=8,
        height=4,
        mode="threshold",
        threshold=128,
        fit="contain",
    )

    result = convert_frame(source, settings)

    np.testing.assert_array_equal(result.pixels[:, :2], 0)
    np.testing.assert_array_equal(result.pixels[:, 2:6], 1)
    np.testing.assert_array_equal(result.pixels[:, 6:], 0)


def test_dither_retains_midtones_as_a_binary_pattern() -> None:
    source = np.full((16, 16), 127, dtype=np.uint8)
    threshold = convert_frame(
        source,
        ConvertSettings(
            width=16,
            height=16,
            mode="threshold",
            threshold=128,
            fit="stretch",
        ),
    )
    dither = convert_frame(
        source,
        ConvertSettings(
            width=16,
            height=16,
            mode="floyd_steinberg",
            fit="stretch",
        ),
    )

    assert np.count_nonzero(threshold.pixels) == 0
    assert 0 < np.count_nonzero(dither.pixels) < dither.pixels.size


def test_packed_scanlines_are_msb_first() -> None:
    pixels = np.array(
        [
            [1, 0, 1, 0, 1, 0, 1, 0],
            [0, 1, 0, 1, 0, 1, 0, 1],
        ],
        dtype=np.uint8,
    )

    frame = MonoFrame(pixels)

    assert frame.width == 8
    assert frame.height == 2
    assert frame.stride == 1
    assert frame.packed == bytes([0xAA, 0x55])
