"""Tests for KMS connector selection, RGB565 output, and config wiring."""

from pathlib import Path

import numpy as np
import pytest

from macbridge.config import OutputSettings, load_config
from macbridge.convert import MonoFrame
from macbridge.dpi import RGB565_BLACK, RGB565_WHITE, TIMING
from macbridge.drm import (
    DRM_MODE_CONNECTOR_DPI,
    ConnectorInfo,
    DisplayMode,
    KmsError,
    NoDpiConnector,
    select_dpi_connector,
    select_macintosh_mode,
)
from macbridge.outputs import CompositeOutput, build_output
from macbridge.outputs.kms import KmsDpiOutput
from macbridge.outputs.preview import MjpegPreviewOutput


def _mode(
    hdisplay: int = TIMING.hactive,
    vdisplay: int = TIMING.vactive,
    htotal: int = TIMING.htotal,
    vtotal: int = TIMING.vtotal,
    clock_khz: int = TIMING.pixel_clock_hz // 1000,
    name: str = "512x342",
) -> DisplayMode:
    return DisplayMode(
        clock_khz=clock_khz,
        hdisplay=hdisplay,
        hsync_start=hdisplay + TIMING.hfp,
        hsync_end=hdisplay + TIMING.hfp + TIMING.hsync,
        htotal=htotal,
        vdisplay=vdisplay,
        vsync_start=vdisplay + TIMING.vfp,
        vsync_end=vdisplay + TIMING.vfp + TIMING.vsync,
        vtotal=vtotal,
        name=name,
    )


def _connector(
    name: str,
    connector_type: int,
    modes: tuple[DisplayMode, ...],
    connector_id: int = 41,
) -> ConnectorInfo:
    return ConnectorInfo(
        connector_id=connector_id,
        name=name,
        connector_type=connector_type,
        connection=1,
        encoder_id=0,
        encoder_ids=(),
        modes=modes,
    )


class FakeDisplay:
    """In-memory RGB565 target used in place of a DRM card."""

    def __init__(self, width: int = 8, height: int = 2) -> None:
        self.width = width
        self.height = height
        self.description = f"fake DPI-1 {width}x{height} RGB565"
        self.frames: list[np.ndarray] = []
        self.closed = False

    def blit(self, rgb565: np.ndarray) -> None:
        self.frames.append(np.array(rgb565, copy=True))

    def close(self) -> None:
        self.closed = True


class FakeOutput:
    """Minimal FrameOutput used to test composite fan-out and rollback."""

    def __init__(self, name: str, fail_start: bool = False) -> None:
        self.name = name
        self.fail_start = fail_start
        self.started = False
        self.stopped = False
        self.frames: list[MonoFrame] = []

    def start(self) -> None:
        if self.fail_start:
            raise RuntimeError(f"{self.name} failed to start")
        self.started = True

    def send(self, frame: MonoFrame) -> None:
        self.frames.append(frame)

    def stop(self) -> None:
        self.stopped = True


def test_select_dpi_connector_prefers_dpi_with_macintosh_mode() -> None:
    hdmi = _connector("HDMI-A-1", 11, (_mode(hdisplay=640, vdisplay=480),))
    dpi = _connector("DPI-1", DRM_MODE_CONNECTOR_DPI, (_mode(),))

    chosen = select_dpi_connector([hdmi, dpi])

    assert chosen.name == "DPI-1"


def test_select_dpi_connector_honors_preferred_name() -> None:
    first = _connector("DPI-1", DRM_MODE_CONNECTOR_DPI, (_mode(),), connector_id=1)
    second = _connector("DPI-2", DRM_MODE_CONNECTOR_DPI, (_mode(),), connector_id=2)

    chosen = select_dpi_connector([first, second], preferred="DPI-2")

    assert chosen.name == "DPI-2"


def test_select_dpi_connector_errors_without_dpi() -> None:
    hdmi = _connector("HDMI-A-1", 11, (_mode(hdisplay=1920, vdisplay=1080),))

    with pytest.raises(NoDpiConnector, match="No DPI connector"):
        select_dpi_connector([hdmi])


def test_select_macintosh_mode_picks_locked_raster() -> None:
    wrong_blanking = _mode(htotal=800, vtotal=500, clock_khz=20_000)
    locked = _mode()

    chosen = select_macintosh_mode((wrong_blanking, locked))

    assert chosen.htotal == TIMING.htotal
    assert chosen.vtotal == TIMING.vtotal
    assert abs(chosen.refresh_hz - TIMING.refresh_hz) < 0.02


def test_select_macintosh_mode_rejects_missing_512x342() -> None:
    with pytest.raises(KmsError, match="512x342"):
        select_macintosh_mode((_mode(hdisplay=640, vdisplay=480),))


def test_kms_output_blits_packed_rgb565() -> None:
    display = FakeDisplay()
    output = KmsDpiOutput(OutputSettings(kms=True, preview=False), display=display)
    frame = MonoFrame(np.array([[0, 1, 0, 1, 1, 0, 1, 0], [1, 1, 0, 0, 1, 1, 0, 0]], dtype=np.uint8))

    output.start()
    output.send(frame)
    output.stop()

    assert len(display.frames) == 1
    np.testing.assert_array_equal(
        display.frames[0],
        [
            [
                RGB565_BLACK,
                RGB565_WHITE,
                RGB565_BLACK,
                RGB565_WHITE,
                RGB565_WHITE,
                RGB565_BLACK,
                RGB565_WHITE,
                RGB565_BLACK,
            ],
            [
                RGB565_WHITE,
                RGB565_WHITE,
                RGB565_BLACK,
                RGB565_BLACK,
                RGB565_WHITE,
                RGB565_WHITE,
                RGB565_BLACK,
                RGB565_BLACK,
            ],
        ],
    )
    assert display.closed


def test_kms_output_rejects_mismatched_geometry() -> None:
    display = FakeDisplay(width=8, height=2)
    output = KmsDpiOutput(OutputSettings(kms=True, preview=False), display=display)
    output.start()

    with pytest.raises(KmsError, match="does not match"):
        output.send(MonoFrame(np.zeros((342, 512), dtype=np.uint8)))


def test_composite_output_fans_out_and_rolls_back_start() -> None:
    first = FakeOutput("first")
    second = FakeOutput("second", fail_start=True)
    composite = CompositeOutput([first, second])

    with pytest.raises(RuntimeError, match="second failed"):
        composite.start()

    assert first.started
    assert first.stopped
    assert not second.started

    ready = FakeOutput("ready")
    other = FakeOutput("other")
    composite = CompositeOutput([ready, other])
    frame = MonoFrame(np.ones((2, 2), dtype=np.uint8))
    composite.start()
    composite.send(frame)
    composite.stop()
    assert ready.frames[0] is frame
    assert other.frames[0] is frame
    assert ready.stopped
    assert other.stopped


def test_build_output_preview_only() -> None:
    output = build_output(OutputSettings(kms=False, preview=True, port=0))
    assert isinstance(output, MjpegPreviewOutput)


def test_build_output_kms_only_uses_kms_writer() -> None:
    output = build_output(OutputSettings(kms=True, preview=False))
    assert isinstance(output, KmsDpiOutput)


def test_load_config_parses_kms_flags(tmp_path: Path) -> None:
    path = tmp_path / "local.yaml"
    path.write_text(
        "output:\n  kms: true\n  preview: false\n  connector: DPI-1\n",
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.output.kms
    assert not config.output.preview
    assert config.output.connector == "DPI-1"


def test_load_config_rejects_no_outputs(tmp_path: Path) -> None:
    path = tmp_path / "local.yaml"
    path.write_text("output:\n  kms: false\n  preview: false\n", encoding="utf-8")

    with pytest.raises(ValueError, match="cannot both be false"):
        load_config(path)


def test_example_config_enables_kms_and_preview() -> None:
    config = load_config(Path("config/config.example.yaml"))

    assert config.output.kms
    assert config.output.preview
    assert config.convert.width == 512
    assert config.convert.height == 342
