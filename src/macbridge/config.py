"""Configuration loader for capture, conversion, KMS, and preview."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Union

import yaml


PathLike = Union[str, Path]

DEFAULT_CONFIG: Dict[str, Any] = {
    "capture": {
        "device": "/dev/video0",
        "pixel_format": "MJPG",
        "width": 640,
        "height": 480,
        "fps": 60,
    },
    "convert": {
        "width": 512,
        "height": 342,
        "mode": "threshold",
        "threshold": 96,
        "fit": "stretch",
        "invert": False,
    },
    "output": {
        "kms": True,
        "preview": True,
        "connector": "",
        "device": "",
        "bind": "0.0.0.0",
        "port": 5000,
        "jpeg_quality": 80,
    },
}


@dataclass(frozen=True)
class CaptureSettings:
    """V4L2 device and format selected after hardware inspection."""

    device: str = "/dev/video0"
    pixel_format: str = "MJPG"
    width: int = 640
    height: int = 480
    fps: float = 60.0


@dataclass(frozen=True)
class ConvertSettings:
    """Output geometry and monochrome conversion settings."""

    width: int = 512
    height: int = 342
    mode: str = "threshold"
    threshold: int = 96
    fit: str = "stretch"
    invert: bool = False


@dataclass(frozen=True)
class PreviewSettings:
    """Network preview server settings."""

    bind: str = "0.0.0.0"
    port: int = 5000
    jpeg_quality: int = 80


@dataclass(frozen=True)
class OutputSettings:
    """KMS/DPI writer and optional HTTP preview settings."""

    kms: bool = True
    preview: bool = True
    connector: str = ""
    device: str = ""
    bind: str = "0.0.0.0"
    port: int = 5000
    jpeg_quality: int = 80

    @property
    def preview_settings(self) -> "PreviewSettings":
        """Return the HTTP preview fields as a dedicated settings object."""

        return PreviewSettings(
            bind=self.bind,
            port=self.port,
            jpeg_quality=self.jpeg_quality,
        )


@dataclass(frozen=True)
class AppConfig:
    """Validated configuration for capture, conversion, and outputs."""

    capture: CaptureSettings
    convert: ConvertSettings
    output: OutputSettings

    @property
    def preview(self) -> PreviewSettings:
        """Return HTTP preview settings for callers that only need preview."""

        return self.output.preview_settings


def _deep_merge(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    """Merge nested mappings without mutating either input."""

    merged = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _read_yaml(path: Path) -> Dict[str, Any]:
    """Read a YAML mapping from ``path``."""

    with path.open("r", encoding="utf-8") as config_file:
        values = yaml.safe_load(config_file) or {}
    if not isinstance(values, dict):
        raise ValueError(f"Configuration root must be a mapping: {path}")
    return values


def _section(values: Dict[str, Any], name: str) -> Dict[str, Any]:
    """Return one configuration section and validate its type."""

    section = values.get(name, {})
    if not isinstance(section, dict):
        raise ValueError(f"Configuration section '{name}' must be a mapping")
    return section


def _build_config(values: Dict[str, Any]) -> AppConfig:
    """Build and validate the three settings groups from YAML values."""

    capture_values = _section(values, "capture")
    convert_values = _section(values, "convert")
    output_values = _section(values, "output")

    capture = CaptureSettings(
        device=str(capture_values.get("device", "/dev/video0")),
        pixel_format=str(capture_values.get("pixel_format", "MJPG")).upper(),
        width=int(capture_values.get("width", 640)),
        height=int(capture_values.get("height", 480)),
        fps=float(capture_values.get("fps", 60)),
    )
    if len(capture.pixel_format) != 4:
        raise ValueError("capture.pixel_format must be a four-character code")
    if (
        capture.width <= 0
        or capture.height <= 0
        or capture.fps <= 0
    ):
        raise ValueError("capture dimensions and fps must be positive")

    convert = ConvertSettings(
        width=int(convert_values.get("width", 512)),
        height=int(convert_values.get("height", 342)),
        mode=str(convert_values.get("mode", "threshold")).lower(),
        threshold=int(convert_values.get("threshold", 96)),
        fit=str(convert_values.get("fit", "stretch")).lower(),
        invert=bool(convert_values.get("invert", False)),
    )
    if convert.width <= 0 or convert.height <= 0:
        raise ValueError("convert dimensions must be positive")
    if convert.mode not in {"threshold", "floyd_steinberg"}:
        raise ValueError(
            "convert.mode must be threshold or floyd_steinberg"
        )
    if not 0 <= convert.threshold <= 255:
        raise ValueError("convert.threshold must be between 0 and 255")
    if convert.fit not in {"contain", "stretch"}:
        raise ValueError("convert.fit must be contain or stretch")

    preview = PreviewSettings(
        bind=str(output_values.get("bind", "0.0.0.0")),
        port=int(output_values.get("port", 5000)),
        jpeg_quality=int(output_values.get("jpeg_quality", 80)),
    )
    if not 1 <= preview.port <= 65535:
        raise ValueError("output.port must be between 1 and 65535")
    if not 1 <= preview.jpeg_quality <= 100:
        raise ValueError("output.jpeg_quality must be between 1 and 100")

    kms_enabled = bool(output_values.get("kms", True))
    preview_enabled = bool(output_values.get("preview", True))
    if not kms_enabled and not preview_enabled:
        raise ValueError("output.kms and output.preview cannot both be false")

    output = OutputSettings(
        kms=kms_enabled,
        preview=preview_enabled,
        connector=str(output_values.get("connector", "") or ""),
        device=str(output_values.get("device", "") or ""),
        bind=preview.bind,
        port=preview.port,
        jpeg_quality=preview.jpeg_quality,
    )

    return AppConfig(capture=capture, convert=convert, output=output)


def load_config(path: Optional[PathLike] = None) -> AppConfig:
    """Load an explicit config or the example plus ignored local overlay."""

    values = dict(DEFAULT_CONFIG)
    if path is not None:
        config_path = Path(path)
        if not config_path.is_file():
            raise FileNotFoundError(f"Configuration file not found: {config_path}")
        return _build_config(_deep_merge(values, _read_yaml(config_path)))

    example_path = Path("config/config.example.yaml")
    local_path = Path("config/local.yaml")
    if example_path.is_file():
        values = _deep_merge(values, _read_yaml(example_path))
    if local_path.is_file():
        values = _deep_merge(values, _read_yaml(local_path))
    return _build_config(values)
