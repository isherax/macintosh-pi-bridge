"""Locked Raspberry Pi 4 KMS/DPI pin map and Macintosh Plus raster timing.

The BCM2711 Display Parallel Interface uses GPIO alternate function 2.
VSYNC is fixed on GPIO2 (LCD_VSYNC) and HSYNC is fixed on GPIO3
(LCD_HSYNC). Monochrome VIDEO is a single RGB565 data pin: GPIO19, which
carries the red most-significant bit (R7 / DPI_D15) in official mode 2.

White pixels must be written as full-on RGB and black pixels as zero so
that R7 follows the 1-bit raster. GPIO8 (B7) and GPIO14 (G7) would also
toggle under that convention; they are not the wiring tap.

Porch widths, sync pulse lengths, and sync polarity are overlay parameters.
They can change without moving the three CRT wires. Video polarity is
handled by conversion, not by choosing a different GPIO.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DpiPin:
    """One BCM GPIO used by the analog-board interposer."""

    gpio: int
    header_pin: int
    function: str


@dataclass(frozen=True)
class DpiTiming:
    """KMS-legal 512x342 raster at Macintosh Plus line and frame rates."""

    pixel_clock_hz: int
    hactive: int
    hfp: int
    hsync: int
    hbp: int
    vactive: int
    vfp: int
    vsync: int
    vbp: int
    hsync_active_low: bool
    vsync_active_low: bool

    @property
    def htotal(self) -> int:
        """Return horizontal clocks per line, including blanking."""

        return self.hactive + self.hfp + self.hsync + self.hbp

    @property
    def vtotal(self) -> int:
        """Return vertical lines per frame, including blanking."""

        return self.vactive + self.vfp + self.vsync + self.vbp

    @property
    def refresh_hz(self) -> float:
        """Return the vertical refresh rate implied by the timing."""

        return self.pixel_clock_hz / (self.htotal * self.vtotal)


# Official RGB565 mode 2 (output_format 2) most-significant bits.
# R[7:3] occupy GPIO19-15, G[7:2] occupy GPIO14-9, B[7:3] occupy GPIO8-4.
RGB565_MSB_GPIO = {
    "R7": 19,
    "G7": 14,
    "B7": 8,
}

BUS_FORMAT = "rgb565"
VIDEO_BIT = "R7"
OVERLAY = "vc4-kms-dpi-generic"

VSYNC_PIN = DpiPin(gpio=2, header_pin=3, function="LCD_VSYNC")
HSYNC_PIN = DpiPin(gpio=3, header_pin=5, function="LCD_HSYNC")
VIDEO_PIN = DpiPin(
    gpio=RGB565_MSB_GPIO[VIDEO_BIT],
    header_pin=35,
    function="DPI_D15",
)

GROUND_HEADER_PINS = (6, 9, 14, 20, 25, 30, 34, 39)

TIMING = DpiTiming(
    pixel_clock_hz=15_667_200,
    hactive=512,
    hfp=32,
    hsync=128,
    hbp=32,
    vactive=342,
    vfp=11,
    vsync=3,
    vbp=14,
    hsync_active_low=True,
    vsync_active_low=True,
)
