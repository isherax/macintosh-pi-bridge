"""Tests for the locked Pi 4 KMS/DPI pin map and Macintosh Plus raster."""

import numpy as np

from macbridge.dpi import (
    BUS_FORMAT,
    GROUND_HEADER_PINS,
    HSYNC_PIN,
    OVERLAY,
    RGB565_BLACK,
    RGB565_MSB_GPIO,
    RGB565_R7_MASK,
    RGB565_WHITE,
    TIMING,
    VIDEO_BIT,
    VIDEO_PIN,
    VSYNC_PIN,
    pack_video_rgb565,
)


def test_sync_and_video_gpios_match_official_dpi_alt2() -> None:
    assert VSYNC_PIN.gpio == 2
    assert VSYNC_PIN.header_pin == 3
    assert VSYNC_PIN.function == "LCD_VSYNC"
    assert HSYNC_PIN.gpio == 3
    assert HSYNC_PIN.header_pin == 5
    assert HSYNC_PIN.function == "LCD_HSYNC"
    assert VIDEO_PIN.gpio == 19
    assert VIDEO_PIN.header_pin == 35
    assert VIDEO_PIN.function == "DPI_D15"


def test_rgb565_mode2_red_msb_is_the_video_tap() -> None:
    assert BUS_FORMAT == "rgb565"
    assert OVERLAY == "vc4-kms-dpi-generic"
    assert VIDEO_BIT == "R7"
    assert RGB565_MSB_GPIO["R7"] == 19
    assert RGB565_MSB_GPIO["G7"] == 14
    assert RGB565_MSB_GPIO["B7"] == 8
    assert VIDEO_PIN.gpio == RGB565_MSB_GPIO[VIDEO_BIT]


def test_timing_is_kms_legal_macintosh_plus_raster() -> None:
    assert TIMING.pixel_clock_hz == 15_667_200
    assert TIMING.hactive == 512
    assert TIMING.hfp == 12
    assert TIMING.hsync == 178
    assert TIMING.hbp == 2
    assert TIMING.vactive == 342
    assert TIMING.vfp == 1
    assert TIMING.vsync == 4
    assert TIMING.vbp == 23
    assert TIMING.htotal == 704
    assert TIMING.vtotal == 370
    assert TIMING.hsync_active_low
    assert TIMING.vsync_active_low
    assert TIMING.refresh_hz == TIMING.pixel_clock_hz / (704 * 370)
    assert abs(TIMING.refresh_hz - 60.15) < 0.01


def test_ground_header_pins_are_the_40pin_grounds() -> None:
    assert GROUND_HEADER_PINS == (6, 9, 14, 20, 25, 30, 34, 39)


def test_rgb565_packing_sets_red_msb_for_white_pixels() -> None:
    pixels = np.array([[0, 1], [1, 0]], dtype=np.uint8)

    packed = pack_video_rgb565(pixels)

    assert packed.dtype == np.uint16
    np.testing.assert_array_equal(
        packed,
        [[RGB565_BLACK, RGB565_WHITE], [RGB565_WHITE, RGB565_BLACK]],
    )
    assert packed[0, 1] & RGB565_R7_MASK
    assert packed[1, 0] & RGB565_R7_MASK
    assert packed[0, 0] & RGB565_R7_MASK == 0
