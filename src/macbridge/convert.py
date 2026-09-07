"""Aspect-preserving conversion to a packed 1-bit monochrome frame."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np

from macbridge.config import ConvertSettings


@dataclass(frozen=True)
class MonoFrame:
    """A monochrome frame with both unpacked pixels and packed scanlines.

    ``pixels`` contains one byte per pixel with values 0 (black) or 1 (white).
    ``packed`` packs each scanline most-significant-bit first, matching the
    conventional byte order for a one-bit raster and the later DPI backend.
    """

    pixels: np.ndarray

    def __post_init__(self) -> None:
        pixels = np.asarray(self.pixels)
        if pixels.ndim != 2:
            raise ValueError("MonoFrame pixels must be a two-dimensional array")
        if pixels.dtype != np.uint8:
            raise ValueError("MonoFrame pixels must have dtype uint8")
        if not np.all((pixels == 0) | (pixels == 1)):
            raise ValueError("MonoFrame pixels must contain only 0 and 1")

    @property
    def height(self) -> int:
        """Return the frame height in pixels."""

        return int(self.pixels.shape[0])

    @property
    def width(self) -> int:
        """Return the frame width in pixels."""

        return int(self.pixels.shape[1])

    @property
    def stride(self) -> int:
        """Return the packed bytes per scanline, including pad bits."""

        return (self.width + 7) // 8

    @property
    def packed(self) -> bytes:
        """Return scanlines packed MSB-first with zero-valued pad bits."""

        packed = np.packbits(self.pixels, axis=1, bitorder="big")
        return packed.tobytes()

    def preview_image(self) -> np.ndarray:
        """Return an 8-bit black-and-white image for preview encoding."""

        return self.pixels * np.uint8(255)


def _as_grayscale(frame: np.ndarray) -> np.ndarray:
    """Convert a captured BGR, BGRA, or grayscale frame to uint8 grayscale."""

    image = np.asarray(frame)
    if image.size == 0:
        raise ValueError("Cannot convert an empty frame")
    if image.dtype != np.uint8:
        image = np.clip(image, 0, 255).astype(np.uint8)
    if image.ndim == 2:
        return image
    if image.ndim == 3 and image.shape[2] == 3:
        return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    if image.ndim == 3 and image.shape[2] == 4:
        return cv2.cvtColor(image, cv2.COLOR_BGRA2GRAY)
    raise ValueError(
        "Expected a grayscale, BGR, or BGRA frame; "
        f"received shape {image.shape}"
    )


def _fit_image(
    gray: np.ndarray,
    width: int,
    height: int,
    fit: str,
) -> np.ndarray:
    """Resize grayscale input to the target geometry using the selected fit."""

    source_height, source_width = gray.shape
    if fit == "stretch":
        return cv2.resize(gray, (width, height), interpolation=cv2.INTER_AREA)
    if fit != "contain":
        raise ValueError("fit must be contain or stretch")

    scale = min(width / source_width, height / source_height)
    fitted_width = max(1, round(source_width * scale))
    fitted_height = max(1, round(source_height * scale))
    resized = cv2.resize(
        gray,
        (fitted_width, fitted_height),
        interpolation=cv2.INTER_AREA,
    )
    canvas = np.zeros((height, width), dtype=np.uint8)
    offset_x = (width - fitted_width) // 2
    offset_y = (height - fitted_height) // 2
    canvas[
        offset_y : offset_y + fitted_height,
        offset_x : offset_x + fitted_width,
    ] = resized
    return canvas


def _threshold(gray: np.ndarray, threshold: int) -> np.ndarray:
    """Convert grayscale pixels to binary pixels with a fixed threshold."""

    return (gray >= threshold).astype(np.uint8)


def _floyd_steinberg(gray: np.ndarray) -> np.ndarray:
    """Convert grayscale pixels with serpentine-free Floyd–Steinberg dither."""

    working = gray.astype(np.float32, copy=True)
    result = np.zeros(gray.shape, dtype=np.uint8)
    height, width = gray.shape

    for y in range(height):
        for x in range(width):
            old_value = working[y, x]
            new_value = 255.0 if old_value >= 128.0 else 0.0
            result[y, x] = 1 if new_value else 0
            error = old_value - new_value

            if x + 1 < width:
                working[y, x + 1] += error * (7.0 / 16.0)
            if y + 1 < height:
                if x > 0:
                    working[y + 1, x - 1] += error * (3.0 / 16.0)
                working[y + 1, x] += error * (5.0 / 16.0)
                if x + 1 < width:
                    working[y + 1, x + 1] += error * (1.0 / 16.0)

    return result


def convert_frame(
    frame: np.ndarray,
    settings: Optional[ConvertSettings] = None,
) -> MonoFrame:
    """Convert a captured frame to the configured 1-bit raster.

    Input may be a BGR/BGRA OpenCV frame or an 8-bit grayscale array. The
    output is aspect-fitted or stretched to the target dimensions, then
    thresholded or Floyd–Steinberg dithered. ``invert`` flips the final pixel
    polarity so the same raster can support either CRT convention.
    """

    settings = settings or ConvertSettings()
    if settings.width <= 0 or settings.height <= 0:
        raise ValueError("Output dimensions must be positive")
    if not 0 <= settings.threshold <= 255:
        raise ValueError("Threshold must be between 0 and 255")

    gray = _as_grayscale(frame)
    fitted = _fit_image(
        gray,
        settings.width,
        settings.height,
        settings.fit,
    )
    if settings.mode == "threshold":
        pixels = _threshold(fitted, settings.threshold)
    elif settings.mode == "floyd_steinberg":
        pixels = _floyd_steinberg(fitted)
    else:
        raise ValueError(
            "Conversion mode must be threshold or floyd_steinberg"
        )

    if settings.invert:
        pixels = np.uint8(1) - pixels
    return MonoFrame(pixels=pixels)
