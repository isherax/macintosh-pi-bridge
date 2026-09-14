"""Tests for the locked Pi 4 KMS/DPI pin map and Macintosh Plus raster."""

from macbridge.dpi import (
    BUS_FORMAT,
    GROUND_HEADER_PINS,
    HSYNC_PIN,
    OVERLAY,
    RGB565_MSB_GPIO,
    TIMING,
    VIDEO_BIT,
    VIDEO_PIN,
    VSYNC_PIN,
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
    assert TIMING.vactive == 342
    assert TIMING.htotal == 704
    assert TIMING.vtotal == 370
    assert TIMING.hsync_active_low
    assert TIMING.vsync_active_low
    assert TIMING.refresh_hz == TIMING.pixel_clock_hz / (704 * 370)
    assert abs(TIMING.refresh_hz - 60.15) < 0.01


def test_ground_header_pins_are_the_40pin_grounds() -> None:
    assert GROUND_HEADER_PINS == (6, 9, 14, 20, 25, 30, 34, 39)
