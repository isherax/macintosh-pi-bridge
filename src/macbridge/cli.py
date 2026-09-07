"""Command-line entry points for inspection, preview, and live bridging."""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Optional, Sequence

import cv2

from macbridge.capture import CaptureError, V4L2Capture
from macbridge.config import AppConfig, load_config
from macbridge.convert import convert_frame
from macbridge.outputs.preview import MjpegPreviewOutput
from macbridge.patterns import make_test_pattern
from macbridge.pipeline import run_pipeline


def _add_config_argument(parser: argparse.ArgumentParser) -> None:
    """Add the shared optional configuration path argument."""

    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="YAML config path (default: config/local.yaml or example)",
    )


def _build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""

    parser = argparse.ArgumentParser(
        prog="macbridge",
        description="Capture HDMI and convert it to a 512x342 1-bit raster.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser(
        "run",
        help="run the live capture, conversion, and output pipeline",
    )
    _add_config_argument(run_parser)
    run_parser.add_argument(
        "--device",
        help="override capture.device without editing local config",
    )

    pattern_parser = subparsers.add_parser(
        "pattern",
        help="publish a synthetic conversion test card",
    )
    _add_config_argument(pattern_parser)
    pattern_parser.add_argument(
        "--frames",
        type=int,
        default=None,
        help="stop after this many frames instead of running continuously",
    )
    pattern_parser.add_argument(
        "--fps",
        type=float,
        default=30.0,
        help="test-card frame rate (default: 30)",
    )
    pattern_parser.add_argument(
        "--bind",
        help="override preview bind address for this test",
    )
    pattern_parser.add_argument(
        "--port",
        type=int,
        help="override preview port for this test (0 selects an ephemeral port)",
    )

    capture_parser = subparsers.add_parser(
        "capture-test",
        help="open V4L2 and print the selected mode while grabbing frames",
    )
    _add_config_argument(capture_parser)
    capture_parser.add_argument(
        "--device",
        help="override capture.device without editing local config",
    )
    capture_parser.add_argument(
        "--frames",
        type=int,
        default=30,
        help="number of frames to grab (default: 30)",
    )
    capture_parser.add_argument(
        "--save",
        type=Path,
        help="save the last captured frame to this path",
    )

    return parser


def _load_command_config(config_path: Optional[Path]) -> AppConfig:
    """Load and return the selected configuration."""

    return load_config(config_path)


def _run(config_path: Optional[Path], device: Optional[str]) -> int:
    """Run the live pipeline command."""

    config = _load_command_config(config_path)
    if device:
        config = replace(
            config,
            capture=replace(config.capture, device=device),
        )
    capture = V4L2Capture(config.capture)
    output = MjpegPreviewOutput(config.preview)
    preview_url = getattr(output, "url", None)
    if preview_url:
        print(f"Preview: {preview_url}", flush=True)
    stats = run_pipeline(capture, config.convert, output)
    print(
        f"Stopped: captured={stats.captured} emitted={stats.emitted}",
        flush=True,
    )
    return 0


def _run_pattern(
    config_path: Optional[Path],
    frame_count: Optional[int],
    fps: float,
    bind: Optional[str],
    port: Optional[int],
) -> int:
    """Publish a synthetic test card through the configured preview."""

    if fps <= 0:
        raise ValueError("--fps must be positive")
    if frame_count is not None and frame_count <= 0:
        raise ValueError("--frames must be positive")

    config = _load_command_config(config_path)
    if bind is not None or port is not None:
        preview = config.preview
        config = replace(
            config,
            preview=replace(
                preview,
                bind=bind if bind is not None else preview.bind,
                port=port if port is not None else preview.port,
            ),
        )
    output = MjpegPreviewOutput(config.preview)
    output.start()
    print(f"Preview: {getattr(output, 'url', 'started')}", flush=True)
    converted = convert_frame(
        make_test_pattern(config.convert.width, config.convert.height),
        config.convert,
    )
    interval = 1.0 / fps
    sent = 0
    try:
        while frame_count is None or sent < frame_count:
            started = time.monotonic()
            output.send(converted)
            sent += 1
            time.sleep(max(0.0, interval - (time.monotonic() - started)))
    except KeyboardInterrupt:
        pass
    finally:
        output.stop()
    return 0


def _run_capture_test(
    config_path: Optional[Path],
    device: Optional[str],
    frame_count: int,
    save_path: Optional[Path],
) -> int:
    """Grab a finite number of frames and report the actual capture mode."""

    if frame_count <= 0:
        raise ValueError("--frames must be positive")
    config = _load_command_config(config_path)
    if device:
        config = replace(
            config,
            capture=replace(config.capture, device=device),
        )

    capture = V4L2Capture(config.capture)
    last_frame = None
    with capture:
        print(f"Device: {capture.device}")
        print(f"Selected format: {capture.selected_format}")
        print(f"Actual format: {capture.actual_format()}")
        for index in range(frame_count):
            last_frame = capture.read()
            if index == 0:
                print(f"First frame shape: {last_frame.shape}")

    if save_path is not None and last_frame is not None:
        if not cv2.imwrite(str(save_path), last_frame):
            raise RuntimeError(f"Could not write frame to {save_path}")
        print(f"Saved: {save_path}")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Parse arguments, execute one command, and return a shell exit code."""

    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            return _run(args.config, args.device)
        if args.command == "pattern":
            return _run_pattern(
                args.config,
                args.frames,
                args.fps,
                args.bind,
                args.port,
            )
        if args.command == "capture-test":
            return _run_capture_test(
                args.config,
                args.device,
                args.frames,
                args.save,
            )
    except (CaptureError, FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
        print(f"macbridge: {exc}", file=sys.stderr)
        return 2
    return 2
