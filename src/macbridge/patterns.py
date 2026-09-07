"""Synthetic input frames for testing conversion and preview."""

from __future__ import annotations

import cv2
import numpy as np


def make_test_pattern(width: int = 512, height: int = 342) -> np.ndarray:
    """Create a grayscale test card containing text, geometry, and a ramp."""

    if width <= 0 or height <= 0:
        raise ValueError("Pattern dimensions must be positive")

    image = np.zeros((height, width), dtype=np.uint8)
    border = max(1, min(width, height) // 64)
    cv2.rectangle(
        image,
        (border, border),
        (width - border - 1, height - border - 1),
        255,
        border,
    )

    grid_step = max(8, width // 16)
    for x in range(grid_step, width, grid_step):
        cv2.line(image, (x, 0), (x, height - 1), 64, 1)
    for y in range(grid_step, height, grid_step):
        cv2.line(image, (0, y), (width - 1, y), 64, 1)

    ramp_left = max(8, width // 16)
    ramp_right = width // 2
    ramp_top = height // 2
    ramp_bottom = min(height - 8, ramp_top + max(16, height // 5))
    ramp_width = max(1, ramp_right - ramp_left)
    ramp = np.tile(
        np.linspace(0, 255, ramp_width, dtype=np.uint8),
        (ramp_bottom - ramp_top, 1),
    )
    image[ramp_top:ramp_bottom, ramp_left:ramp_right] = ramp
    cv2.rectangle(
        image,
        (ramp_left, ramp_top),
        (ramp_right - 1, ramp_bottom - 1),
        255,
        1,
    )

    font_scale = max(0.35, min(width / 420, height / 120))
    cv2.putText(
        image,
        "MACBRIDGE",
        (max(8, width // 16), max(24, height // 4)),
        cv2.FONT_HERSHEY_SIMPLEX,
        font_scale,
        255,
        max(1, round(font_scale)),
        cv2.LINE_AA,
    )
    cv2.putText(
        image,
        "512 x 342  1-BIT TEST",
        (max(8, width // 16), max(40, height // 3)),
        cv2.FONT_HERSHEY_SIMPLEX,
        max(0.3, font_scale * 0.55),
        220,
        1,
        cv2.LINE_AA,
    )

    checker_size = max(2, min(width, height) // 32)
    checker_left = width * 3 // 5
    checker_top = height // 2
    checker_width = min(width - checker_left - 8, checker_size * 8)
    checker_height = min(height - checker_top - 8, checker_size * 4)
    for row in range(checker_height // checker_size):
        for column in range(checker_width // checker_size):
            if (row + column) % 2:
                x0 = checker_left + column * checker_size
                y0 = checker_top + row * checker_size
                image[y0 : y0 + checker_size, x0 : x0 + checker_size] = 255

    return image
