"""Configured V4L2 capture through OpenCV."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np

from macbridge.config import CaptureSettings


class CaptureError(RuntimeError):
    """Raised when a configured V4L2 device cannot be opened or read."""


@dataclass(frozen=True)
class CaptureFormat:
    """One capture format and its negotiated geometry."""

    fourcc: str
    width: int
    height: int
    fps: Optional[float] = None


class V4L2Capture:
    """Open one inspected V4L2 node and return decoded BGR frames."""

    def __init__(self, settings: CaptureSettings) -> None:
        self.settings = settings
        self.device = settings.device
        self.selected_format = CaptureFormat(
            fourcc=settings.pixel_format,
            width=settings.width,
            height=settings.height,
            fps=settings.fps,
        )
        self._capture: Optional[cv2.VideoCapture] = None

    @property
    def is_open(self) -> bool:
        """Return whether the underlying OpenCV capture is open."""

        return self._capture is not None and self._capture.isOpened()

    def open(self) -> None:
        """Open and configure the inspected capture device."""

        if self.is_open:
            return

        backend = getattr(cv2, "CAP_V4L2", cv2.CAP_ANY)
        capture = cv2.VideoCapture(self.device, backend)
        if not capture.isOpened():
            capture.release()
            raise CaptureError(
                f"Could not open {self.device}; run "
                "scripts/inspect-capture.sh and check video permissions."
            )

        capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        capture.set(
            cv2.CAP_PROP_FOURCC,
            cv2.VideoWriter_fourcc(*self.settings.pixel_format),
        )
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.settings.width)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.settings.height)
        capture.set(cv2.CAP_PROP_FPS, self.settings.fps)
        self._capture = capture

    def read(self) -> np.ndarray:
        """Read and return one decoded BGR frame."""

        if not self.is_open:
            raise CaptureError("Capture device is not open")
        assert self._capture is not None
        success, frame = self._capture.read()
        if not success or frame is None:
            raise CaptureError(f"Could not read a frame from {self.device}")
        return frame

    def actual_format(self) -> CaptureFormat:
        """Return the dimensions and fourcc reported by OpenCV."""

        if not self.is_open:
            raise CaptureError("Capture device is not open")
        assert self._capture is not None
        fourcc_value = int(self._capture.get(cv2.CAP_PROP_FOURCC))
        fourcc = "".join(
            chr((fourcc_value >> (8 * index)) & 0xFF)
            for index in range(4)
        ).strip("\x00")
        return CaptureFormat(
            fourcc=fourcc or "unknown",
            width=round(self._capture.get(cv2.CAP_PROP_FRAME_WIDTH)),
            height=round(self._capture.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            fps=self._capture.get(cv2.CAP_PROP_FPS) or None,
        )

    def close(self) -> None:
        """Release the underlying V4L2 device."""

        if self._capture is not None:
            self._capture.release()
            self._capture = None

    def __enter__(self) -> "V4L2Capture":
        self.open()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
