"""Low-latency HTTP MJPEG preview output."""

from __future__ import annotations

import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional, Tuple
from urllib.parse import urlsplit

import cv2

from macbridge.config import PreviewSettings
from macbridge.convert import MonoFrame


class _PreviewState:
    """Store one encoded frame and wake clients when it changes."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._sequence = 0
        self._jpeg: Optional[bytes] = None
        self._stopping = False

    def publish(self, jpeg: bytes) -> None:
        """Replace the current frame and wake waiting HTTP clients."""

        with self._condition:
            self._sequence += 1
            self._jpeg = jpeg
            self._condition.notify_all()

    def wait_for_new(
        self,
        previous_sequence: int,
        timeout: float,
    ) -> Optional[Tuple[int, bytes]]:
        """Wait for a frame newer than ``previous_sequence``."""

        deadline = time.monotonic() + timeout
        with self._condition:
            while (
                self._sequence <= previous_sequence
                and not self._stopping
            ):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(remaining)

            if self._sequence <= previous_sequence or self._jpeg is None:
                return None
            return self._sequence, self._jpeg

    def stop(self) -> None:
        """Wake clients and mark the state as stopping."""

        with self._condition:
            self._stopping = True
            self._condition.notify_all()

    @property
    def stopping(self) -> bool:
        """Return whether the output is shutting down."""

        with self._condition:
            return self._stopping


def _handler_for(output: "MjpegPreviewOutput"):
    """Create a request handler bound to one preview output instance."""

    class PreviewHandler(BaseHTTPRequestHandler):
        """Serve a small status page and an MJPEG stream."""

        server_version = "MacBridgePreview/0.1"

        def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
            path = urlsplit(self.path).path
            if path in {"/", ""}:
                self._send_index()
            elif path in {"/stream", "/stream.mjpg"}:
                output._serve_stream(self)
            else:
                self.send_error(HTTPStatus.NOT_FOUND)

        def _send_index(self) -> None:
            body = (
                "<!doctype html><meta charset=utf-8>"
                "<title>Macintosh Pi Bridge</title>"
                "<style>body{background:#222;color:#eee;font-family:sans-serif}"
                "img{image-rendering:pixelated;max-width:100%;height:auto}"
                "</style><h1>Macintosh Pi Bridge</h1>"
                '<img src="/stream.mjpg" alt="1-bit preview">'
            ).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format_string: str, *args: object) -> None:
            """Keep routine stream requests out of the service journal."""

    return PreviewHandler


class MjpegPreviewOutput:
    """Publish the newest monochrome frame as an HTTP MJPEG stream."""

    def __init__(self, settings: PreviewSettings) -> None:
        self.settings = settings
        self._state = _PreviewState()
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    @property
    def url(self) -> str:
        """Return the local URL for the preview stream."""

        if self._server is None:
            return (
                f"http://{self.settings.bind}:{self.settings.port}"
                "/stream.mjpg"
            )
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/stream.mjpg"

    def start(self) -> None:
        """Start the threaded HTTP server."""

        if self._server is not None:
            return
        self._state = _PreviewState()
        handler = _handler_for(self)
        self._server = ThreadingHTTPServer(
            (self.settings.bind, self.settings.port),
            handler,
        )
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="mjpeg-preview",
            daemon=True,
        )
        self._thread.start()

    def send(self, frame: MonoFrame) -> None:
        """JPEG-encode and publish one converted frame."""

        if self._server is None:
            raise RuntimeError("Preview output has not been started")
        success, encoded = cv2.imencode(
            ".jpg",
            frame.preview_image(),
            [cv2.IMWRITE_JPEG_QUALITY, self.settings.jpeg_quality],
        )
        if not success:
            raise RuntimeError("OpenCV could not encode the preview frame")
        self._state.publish(encoded.tobytes())

    def _serve_stream(self, handler: BaseHTTPRequestHandler) -> None:
        """Write multipart JPEG frames to one connected HTTP client."""

        handler.send_response(HTTPStatus.OK)
        handler.send_header(
            "Content-Type",
            "multipart/x-mixed-replace; boundary=frame",
        )
        handler.send_header("Cache-Control", "no-cache, no-store")
        handler.send_header("Pragma", "no-cache")
        handler.send_header("Connection", "close")
        handler.end_headers()

        sequence = 0
        try:
            while not self._state.stopping:
                packet = self._state.wait_for_new(sequence, timeout=1.0)
                if packet is None:
                    continue
                sequence, jpeg = packet
                header = (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    + f"Content-Length: {len(jpeg)}\r\n\r\n".encode("ascii")
                )
                handler.wfile.write(header + jpeg + b"\r\n")
                handler.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            # The browser or ffplay disconnected; the next client can connect.
            return

    def stop(self) -> None:
        """Stop the HTTP server and release its thread."""

        self._state.stop()
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._server = None
        self._thread = None
