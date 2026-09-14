"""Command-line entry points for inspection, KMS output, preview, and bridging."""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Optional, Sequence

import cv2

from macbridge.capture import CaptureError, V4L2Capture
from macbridge.config import AppConfig, OutputSettings, load_config
from macbridge.convert import MonoFrame, convert_frame
from macbridge.outputs import FrameOutput, build_output
from macbridge.patterns import make_test_pattern
from macbridge.pipeline import run_pipeline


def _add_output_argument(parser: argparse.ArgumentParser) -> None:
    """Add the shared output-backend override argument."""

    parser.add_argument(
        "--output",
        choices=("kms", "preview", "both"),
        default=None,
        help="override output backends (default: config output.kms/preview)",
    )


def _apply_output_backend(
    settings: OutputSettings,
    backend: Optional[str],
) -> OutputSettings:
    """Return output settings with an optional CLI backend override applied."""

    if backend is None:
        return settings
    if backend == "preview":
        return replace(settings, kms=False, preview=True)
    if backend == "kms":
        return replace(settings, kms=True, preview=False)
    if backend == "both":
        return replace(settings, kms=True, preview=True)
    raise ValueError(f"Unknown output backend: {backend}")


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
    _add_output_argument(run_parser)
    run_parser.add_argument(
        "--device",
        help="override capture.device without editing local config",
    )

    pattern_parser = subparsers.add_parser(
        "pattern",
        help="publish a synthetic conversion test card",
    )
    _add_config_argument(pattern_parser)
    _add_output_argument(pattern_parser)
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

    kms_parser = subparsers.add_parser(
        "kms-test",
        help="paint a 512x342 test card on the DPI connector and hold it",
    )
    _add_config_argument(kms_parser)
    kms_parser.add_argument(
        "--connector",
        help="override output.connector (for example DPI-1)",
    )
    kms_parser.add_argument(
        "--device",
        help="override output.device DRM card such as /dev/dri/card1",
    )

    return parser


def _load_command_config(config_path: Optional[Path]) -> AppConfig:
    """Load and return the selected configuration."""

    return load_config(config_path)


def _with_output(
    config: AppConfig,
    backend: Optional[str] = None,
    bind: Optional[str] = None,
    port: Optional[int] = None,
    connector: Optional[str] = None,
    drm_device: Optional[str] = None,
) -> AppConfig:
    """Return config with CLI output overrides applied."""

    output = _apply_output_backend(config.output, backend)
    return replace(
        config,
        output=replace(
            output,
            bind=bind if bind is not None else output.bind,
            port=port if port is not None else output.port,
            connector=connector if connector is not None else output.connector,
            device=drm_device if drm_device is not None else output.device,
        ),
    )


def _announce_output(output: FrameOutput) -> None:
    """Print the HTTP preview URL when a preview backend is present."""

    url = getattr(output, "url", None)
    if url:
        print(f"Preview: {url}", flush=True)


def _converted_test_card(config: AppConfig) -> MonoFrame:
    """Build the 1-bit test card using conversion settings, including invert."""

    return convert_frame(
        make_test_pattern(config.convert.width, config.convert.height),
        config.convert,
    )


def _publish_pattern(
    output: FrameOutput,
    converted: MonoFrame,
    fps: float,
    frame_count: Optional[int],
) -> int:
    """Send a test card until interrupted, a frame limit, or shutdown."""

    output.start()
    _announce_output(output)
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


def _run(
    config_path: Optional[Path],
    device: Optional[str],
    output_backend: Optional[str],
) -> int:
    """Run the live pipeline command."""

    config = _with_output(_load_command_config(config_path), backend=output_backend)
    if device:
        config = replace(
            config,
            capture=replace(config.capture, device=device),
        )
    capture = V4L2Capture(config.capture)
    output = build_output(config.output)
    _announce_output(output)
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
    output_backend: Optional[str],
) -> int:
    """Publish a synthetic test card through the configured outputs."""

    if fps <= 0:
        raise ValueError("--fps must be positive")
    if frame_count is not None and frame_count <= 0:
        raise ValueError("--frames must be positive")

    config = _with_output(
        _load_command_config(config_path),
        backend=output_backend,
        bind=bind,
        port=port,
    )
    output = build_output(config.output)
    return _publish_pattern(output, _converted_test_card(config), fps, frame_count)


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


def _run_kms_test(
    config_path: Optional[Path],
    connector: Optional[str],
    drm_device: Optional[str],
) -> int:
    """Paint the conversion test card on the DPI connector until interrupted."""

    config = _with_output(
        _load_command_config(config_path),
        backend="kms",
        connector=connector,
        drm_device=drm_device,
    )
    output = build_output(config.output)
    try:
        output.start()
        output.send(_converted_test_card(config))
        print(
            "Painting the Macintosh Plus test card on DPI. "
            "Leave this process running; Ctrl+C blanks the connector.",
            flush=True,
        )
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        output.stop()
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Parse arguments, execute one command, and return a shell exit code."""

    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            return _run(args.config, args.device, args.output)
        if args.command == "pattern":
            return _run_pattern(
                args.config,
                args.frames,
                args.fps,
                args.bind,
                args.port,
                args.output,
            )
        if args.command == "capture-test":
            return _run_capture_test(
                args.config,
                args.device,
                args.frames,
                args.save,
            )
        if args.command == "kms-test":
            return _run_kms_test(
                args.config,
                args.connector,
                args.device,
            )
    except (CaptureError, FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
        print(f"macbridge: {exc}", file=sys.stderr)
        return 2
    return 2
