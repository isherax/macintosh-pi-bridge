"""Low-latency capture, conversion, and output orchestration."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np

from macbridge.capture import V4L2Capture
from macbridge.config import ConvertSettings
from macbridge.convert import convert_frame
from macbridge.outputs import FrameOutput


class PipelineError(RuntimeError):
    """Raised when the capture worker or conversion loop fails."""


@dataclass
class PipelineStats:
    """Counters useful for command-line diagnostics."""

    captured: int = 0
    emitted: int = 0
    dropped: int = 0


class LatestFrameBuffer:
    """Thread-safe one-slot buffer that intentionally discards old frames."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._frame: Optional[np.ndarray] = None
        self._sequence = 0
        self._pending = False
        self._closed = False
        self._error: Optional[BaseException] = None

    def publish(self, frame: np.ndarray) -> bool:
        """Replace the buffered frame and report whether one was pending."""

        with self._condition:
            if self._closed:
                return False
            dropped = self._pending
            self._frame = frame
            self._sequence += 1
            self._pending = True
            self._condition.notify()
            return dropped

    def wait_for_new(
        self,
        previous_sequence: int,
        timeout: float = 0.5,
    ) -> Optional[Tuple[int, np.ndarray]]:
        """Wait for and return the newest frame after a sequence number."""

        with self._condition:
            self._condition.wait_for(
                lambda: self._sequence > previous_sequence or self._closed,
                timeout=timeout,
            )
            if self._sequence > previous_sequence and self._frame is not None:
                self._pending = False
                return self._sequence, self._frame
            if self._error is not None:
                raise PipelineError("Capture worker stopped") from self._error
            return None

    def close(self, error: Optional[BaseException] = None) -> None:
        """Close the buffer and optionally expose a worker failure."""

        with self._condition:
            self._error = error
            self._closed = True
            self._condition.notify_all()


def _capture_worker(
    capture: V4L2Capture,
    buffer: LatestFrameBuffer,
    stop_event: threading.Event,
    stats: PipelineStats,
) -> None:
    """Continuously read frames into the one-slot latest-frame buffer."""

    try:
        while not stop_event.is_set():
            frame = capture.read()
            stats.captured += 1
            if buffer.publish(frame):
                stats.dropped += 1
    except Exception as exc:
        if not stop_event.is_set():
            buffer.close(exc)


def run_pipeline(
    capture: V4L2Capture,
    convert_settings: ConvertSettings,
    output: FrameOutput,
) -> PipelineStats:
    """Run capture → conversion → output until interrupted or a failure occurs.

    Capture occurs on a worker thread and feeds a one-slot buffer. If
    conversion or output is slower than the source, intermediate frames are
    overwritten instead of accumulating latency. ``output`` may be the KMS
    writer, the HTTP preview, or both.
    """

    stats = PipelineStats()
    stop_event = threading.Event()
    buffer = LatestFrameBuffer()
    worker: Optional[threading.Thread] = None
    output_started = False

    try:
        output.start()
        output_started = True
        capture.open()
        worker = threading.Thread(
            target=_capture_worker,
            args=(capture, buffer, stop_event, stats),
            name="v4l2-capture",
            daemon=True,
        )
        worker.start()

        sequence = 0
        while not stop_event.is_set():
            packet = buffer.wait_for_new(sequence)
            if packet is None:
                continue
            sequence, frame = packet
            converted = convert_frame(frame, convert_settings)
            output.send(converted)
            stats.emitted += 1
    except KeyboardInterrupt:
        return stats
    finally:
        stop_event.set()
        buffer.close()
        capture.close()
        if worker is not None:
            worker.join(timeout=2)
        if output_started:
            output.stop()

    return stats
