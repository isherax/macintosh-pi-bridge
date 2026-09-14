"""Pipeline outputs: KMS/DPI writer, HTTP MJPEG preview, and fan-out."""

from __future__ import annotations

from typing import Optional, Protocol, Sequence

from macbridge.config import OutputSettings
from macbridge.convert import MonoFrame
from macbridge.outputs.kms import KmsDpiOutput
from macbridge.outputs.preview import MjpegPreviewOutput


class FrameOutput(Protocol):
    """Start/stop output that consumes converted 1-bit frames."""

    def start(self) -> None:
        """Acquire output resources."""

    def send(self, frame: MonoFrame) -> None:
        """Publish one converted frame."""

    def stop(self) -> None:
        """Release output resources."""


class CompositeOutput:
    """Send each frame to every configured backend in order."""

    def __init__(self, outputs: Sequence[FrameOutput]) -> None:
        if not outputs:
            raise ValueError("CompositeOutput requires at least one backend")
        self._outputs = list(outputs)

    @property
    def url(self) -> Optional[str]:
        """Return the first nested HTTP preview URL, if any."""

        for output in self._outputs:
            url = getattr(output, "url", None)
            if url:
                return str(url)
        return None

    @property
    def description(self) -> str:
        """Return the first nested KMS description, if any."""

        for output in self._outputs:
            description = getattr(output, "description", "")
            if description:
                return str(description)
        return ""

    def start(self) -> None:
        """Start every backend, rolling back already-started ones on failure."""

        started = []
        try:
            for output in self._outputs:
                output.start()
                started.append(output)
        except Exception:
            for output in reversed(started):
                output.stop()
            raise

    def send(self, frame: MonoFrame) -> None:
        """Publish one frame to every backend."""

        for output in self._outputs:
            output.send(frame)

    def stop(self) -> None:
        """Stop every backend, later backends first."""

        for output in reversed(self._outputs):
            output.stop()


def build_output(settings: OutputSettings) -> FrameOutput:
    """Construct the KMS writer, HTTP preview, or both from settings."""

    outputs: list[FrameOutput] = []
    if settings.kms:
        outputs.append(KmsDpiOutput(settings))
    if settings.preview:
        outputs.append(MjpegPreviewOutput(settings.preview_settings))
    if not outputs:
        raise ValueError("output.kms and output.preview cannot both be false")
    if len(outputs) == 1:
        return outputs[0]
    return CompositeOutput(outputs)
