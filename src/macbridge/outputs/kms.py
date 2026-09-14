"""KMS/DPI output that paints converted 1-bit frames onto GPIO19 VIDEO."""

from __future__ import annotations

from typing import Optional, Protocol

import numpy as np

from macbridge.config import OutputSettings
from macbridge.convert import MonoFrame
from macbridge.dpi import pack_video_rgb565
from macbridge.drm import KmsError, open_dpi_display


class RasterDisplay(Protocol):
    """Mapped RGB565 target that can accept packed Macintosh frames."""

    width: int
    height: int
    description: str

    def blit(self, rgb565: np.ndarray) -> None:
        """Copy one RGB565 frame into the display buffer."""

    def close(self) -> None:
        """Release display resources."""


class KmsDpiOutput:
    """Publish each monochrome frame to the Raspberry Pi DPI connector.

    White pixels are written as RGB565 0xFFFF and black as zero so the red
    most-significant bit on GPIO19 follows the 1-bit raster. Horizontal and
    vertical sync remain hardware DPI signals; this class does not bit-bang
    GPIO.
    """

    def __init__(
        self,
        settings: OutputSettings,
        display: Optional[RasterDisplay] = None,
    ) -> None:
        self.settings = settings
        self._display = display
        self._owns_display = display is None
        self._started = False

    @property
    def description(self) -> str:
        """Return the attached DRM connector and mode, if started."""

        if self._display is None:
            return ""
        return self._display.description

    def start(self) -> None:
        """Open the DPI connector and set the 512x342 RGB565 mode."""

        if self._started:
            return
        if self._display is None:
            self._display = open_dpi_display(
                device=self.settings.device,
                connector=self.settings.connector,
            )
            self._owns_display = True
        self._started = True
        if self.description:
            print(f"KMS: {self.description}", flush=True)

    def send(self, frame: MonoFrame) -> None:
        """Pack one 1-bit frame to RGB565 and blit it to the DPI FB."""

        if not self._started or self._display is None:
            raise KmsError("KMS output has not been started")
        if (
            frame.width != self._display.width
            or frame.height != self._display.height
        ):
            raise KmsError(
                f"Frame {frame.width}x{frame.height} does not match "
                f"DPI mode {self._display.width}x{self._display.height}"
            )
        self._display.blit(pack_video_rgb565(frame.pixels))

    def stop(self) -> None:
        """Release the DRM framebuffer and close the card if present."""

        display = self._display
        self._started = False
        if display is None:
            return
        display.close()
        if self._owns_display:
            self._display = None
