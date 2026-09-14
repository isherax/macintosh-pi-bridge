"""Linux DRM/KMS client for the Raspberry Pi DPI connector.

This module talks to ``/dev/dri/card*`` with the kernel ioctl ABI. It does
not bit-bang GPIO. Timing comes from the ``vc4-kms-dpi-generic`` overlay;
software only selects that 512x342 mode and paints RGB565 frames so the
red most-significant bit on GPIO19 follows the 1-bit raster.
"""

from __future__ import annotations

import ctypes
import errno
import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import numpy as np

from macbridge.dpi import TIMING

_IOC_NRBITS = 8
_IOC_TYPEBITS = 8
_IOC_SIZEBITS = 14
_IOC_NONE = 0
_IOC_WRITE = 1
_IOC_READ = 2
_IOC_NRSHIFT = 0
_IOC_TYPESHIFT = _IOC_NRSHIFT + _IOC_NRBITS
_IOC_SIZESHIFT = _IOC_TYPESHIFT + _IOC_TYPEBITS
_IOC_DIRSHIFT = _IOC_SIZESHIFT + _IOC_SIZEBITS

DRM_IOCTL_BASE = ord("d")
DRM_DISPLAY_MODE_LEN = 32
DRM_CLIENT_CAP_UNIVERSAL_PLANES = 2
DRM_MODE_CONNECTOR_DPI = 17
DRM_FORMAT_RGB565 = (
    ord("R") | (ord("G") << 8) | (ord("1") << 16) | (ord("6") << 24)
)

CONNECTOR_TYPE_NAMES = {
    0: "Unknown",
    1: "VGA",
    2: "DVI-I",
    3: "DVI-D",
    4: "DVI-A",
    5: "Composite",
    6: "SVIDEO",
    7: "LVDS",
    8: "Component",
    9: "DIN",
    10: "DP",
    11: "HDMI-A",
    12: "HDMI-B",
    13: "TV",
    14: "eDP",
    15: "Virtual",
    16: "DSI",
    17: "DPI",
    18: "Writeback",
    19: "SPI",
    20: "USB",
}


class KmsError(RuntimeError):
    """Raised when the DPI connector cannot be opened or painted."""


class NoDpiConnector(KmsError):
    """Raised when a DRM card has no matching DPI connector."""


def _ioc(direction: int, number: int, size: int) -> int:
    """Encode a Linux ioctl request for the DRM type letter.

    Python's ``fcntl.ioctl`` takes a signed 32-bit request, so values with
    the high bit set are returned as negative integers.
    """

    request = (
        (direction << _IOC_DIRSHIFT)
        | (size << _IOC_SIZESHIFT)
        | (DRM_IOCTL_BASE << _IOC_TYPESHIFT)
        | (number << _IOC_NRSHIFT)
    ) & 0xFFFFFFFF
    if request >= 0x80000000:
        return request - 0x100000000
    return request


def _io(number: int) -> int:
    """Encode a DRM ioctl with no payload."""

    return _ioc(_IOC_NONE, number, 0)


def _iow(number: int, struct_type: type) -> int:
    """Encode a write-only DRM ioctl."""

    return _ioc(_IOC_WRITE, number, ctypes.sizeof(struct_type))


def _iowr(number: int, struct_type: type) -> int:
    """Encode a read/write DRM ioctl."""

    return _ioc(_IOC_READ | _IOC_WRITE, number, ctypes.sizeof(struct_type))


class _ModeInfo(ctypes.Structure):
    """``struct drm_mode_modeinfo``."""

    _fields_ = [
        ("clock", ctypes.c_uint32),
        ("hdisplay", ctypes.c_uint16),
        ("hsync_start", ctypes.c_uint16),
        ("hsync_end", ctypes.c_uint16),
        ("htotal", ctypes.c_uint16),
        ("hskew", ctypes.c_uint16),
        ("vdisplay", ctypes.c_uint16),
        ("vsync_start", ctypes.c_uint16),
        ("vsync_end", ctypes.c_uint16),
        ("vtotal", ctypes.c_uint16),
        ("vscan", ctypes.c_uint16),
        ("vrefresh", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("type", ctypes.c_uint32),
        ("name", ctypes.c_char * DRM_DISPLAY_MODE_LEN),
    ]


class _CardRes(ctypes.Structure):
    """``struct drm_mode_card_res``."""

    _fields_ = [
        ("fb_id_ptr", ctypes.c_uint64),
        ("crtc_id_ptr", ctypes.c_uint64),
        ("connector_id_ptr", ctypes.c_uint64),
        ("encoder_id_ptr", ctypes.c_uint64),
        ("count_fbs", ctypes.c_uint32),
        ("count_crtcs", ctypes.c_uint32),
        ("count_connectors", ctypes.c_uint32),
        ("count_encoders", ctypes.c_uint32),
        ("min_width", ctypes.c_uint32),
        ("max_width", ctypes.c_uint32),
        ("min_height", ctypes.c_uint32),
        ("max_height", ctypes.c_uint32),
    ]


class _GetConnector(ctypes.Structure):
    """``struct drm_mode_get_connector``."""

    _fields_ = [
        ("encoders_ptr", ctypes.c_uint64),
        ("modes_ptr", ctypes.c_uint64),
        ("props_ptr", ctypes.c_uint64),
        ("prop_values_ptr", ctypes.c_uint64),
        ("count_modes", ctypes.c_uint32),
        ("count_props", ctypes.c_uint32),
        ("count_encoders", ctypes.c_uint32),
        ("encoder_id", ctypes.c_uint32),
        ("connector_id", ctypes.c_uint32),
        ("connector_type", ctypes.c_uint32),
        ("connector_type_id", ctypes.c_uint32),
        ("connection", ctypes.c_uint32),
        ("mm_width", ctypes.c_uint32),
        ("mm_height", ctypes.c_uint32),
        ("subpixel", ctypes.c_uint32),
        ("pad", ctypes.c_uint32),
    ]


class _GetEncoder(ctypes.Structure):
    """``struct drm_mode_get_encoder``."""

    _fields_ = [
        ("encoder_id", ctypes.c_uint32),
        ("encoder_type", ctypes.c_uint32),
        ("crtc_id", ctypes.c_uint32),
        ("possible_crtcs", ctypes.c_uint32),
        ("possible_clones", ctypes.c_uint32),
    ]


class _ModeCrtc(ctypes.Structure):
    """``struct drm_mode_crtc``."""

    _fields_ = [
        ("set_connectors_ptr", ctypes.c_uint64),
        ("count_connectors", ctypes.c_uint32),
        ("crtc_id", ctypes.c_uint32),
        ("fb_id", ctypes.c_uint32),
        ("x", ctypes.c_uint32),
        ("y", ctypes.c_uint32),
        ("gamma_size", ctypes.c_uint32),
        ("mode_valid", ctypes.c_uint32),
        ("mode", _ModeInfo),
    ]


class _CreateDumb(ctypes.Structure):
    """``struct drm_mode_create_dumb``."""

    _fields_ = [
        ("height", ctypes.c_uint32),
        ("width", ctypes.c_uint32),
        ("bpp", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("handle", ctypes.c_uint32),
        ("pitch", ctypes.c_uint32),
        ("size", ctypes.c_uint64),
    ]


class _MapDumb(ctypes.Structure):
    """``struct drm_mode_map_dumb``."""

    _fields_ = [
        ("handle", ctypes.c_uint32),
        ("pad", ctypes.c_uint32),
        ("offset", ctypes.c_uint64),
    ]


class _DestroyDumb(ctypes.Structure):
    """``struct drm_mode_destroy_dumb``."""

    _fields_ = [
        ("handle", ctypes.c_uint32),
    ]


class _FbCmd2(ctypes.Structure):
    """``struct drm_mode_fb_cmd2``."""

    _fields_ = [
        ("fb_id", ctypes.c_uint32),
        ("width", ctypes.c_uint32),
        ("height", ctypes.c_uint32),
        ("pixel_format", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("handles", ctypes.c_uint32 * 4),
        ("pitches", ctypes.c_uint32 * 4),
        ("offsets", ctypes.c_uint32 * 4),
        ("modifier", ctypes.c_uint64 * 4),
    ]


class _SetClientCap(ctypes.Structure):
    """``struct drm_set_client_cap``."""

    _fields_ = [
        ("cap", ctypes.c_uint64),
        ("value", ctypes.c_uint64),
    ]


DRM_IOCTL_SET_CLIENT_CAP = _iow(0x0D, _SetClientCap)
DRM_IOCTL_SET_MASTER = _io(0x1E)
DRM_IOCTL_DROP_MASTER = _io(0x1F)
DRM_IOCTL_MODE_GETRESOURCES = _iowr(0xA0, _CardRes)
DRM_IOCTL_MODE_SETCRTC = _iowr(0xA2, _ModeCrtc)
DRM_IOCTL_MODE_GETENCODER = _iowr(0xA6, _GetEncoder)
DRM_IOCTL_MODE_GETCONNECTOR = _iowr(0xA7, _GetConnector)
DRM_IOCTL_MODE_RMFB = _iowr(0xAF, ctypes.c_uint32)
DRM_IOCTL_MODE_CREATE_DUMB = _iowr(0xB2, _CreateDumb)
DRM_IOCTL_MODE_MAP_DUMB = _iowr(0xB3, _MapDumb)
DRM_IOCTL_MODE_DESTROY_DUMB = _iowr(0xB4, _DestroyDumb)
DRM_IOCTL_MODE_ADDFB2 = _iowr(0xB8, _FbCmd2)


@dataclass(frozen=True)
class DisplayMode:
    """One DRM display mode advertised by a connector."""

    clock_khz: int
    hdisplay: int
    hsync_start: int
    hsync_end: int
    htotal: int
    vdisplay: int
    vsync_start: int
    vsync_end: int
    vtotal: int
    flags: int = 0
    name: str = ""
    hskew: int = 0
    vscan: int = 0
    vrefresh: int = 0
    type: int = 0

    @property
    def refresh_hz(self) -> float:
        """Return the vertical refresh rate implied by the mode clocks."""

        total = self.htotal * self.vtotal
        if total == 0:
            return 0.0
        return (self.clock_khz * 1000.0) / total


@dataclass(frozen=True)
class ConnectorInfo:
    """One DRM connector and the modes it currently advertises."""

    connector_id: int
    name: str
    connector_type: int
    connection: int
    encoder_id: int
    encoder_ids: Tuple[int, ...]
    modes: Tuple[DisplayMode, ...]


def connector_name(connector_type: int, connector_type_id: int) -> str:
    """Return the sysfs-style connector name, such as ``DPI-1``."""

    type_name = CONNECTOR_TYPE_NAMES.get(connector_type, f"Type{connector_type}")
    return f"{type_name}-{connector_type_id}"


def select_dpi_connector(
    connectors: Sequence[ConnectorInfo],
    preferred: str = "",
) -> ConnectorInfo:
    """Return the DPI connector to paint, or a caller-selected name."""

    if not connectors:
        raise NoDpiConnector(
            "No DRM connectors were found. Append "
            "config/dpi-config.txt.example to /boot/firmware/config.txt "
            "and reboot, then run scripts/validate-dpi.sh."
        )
    if preferred:
        needle = preferred.strip()
        for connector in connectors:
            if connector.name == needle or needle in connector.name:
                return connector
        available = ", ".join(item.name for item in connectors)
        raise NoDpiConnector(
            f"Connector {needle!r} was not found (available: {available})."
        )

    dpi = [
        connector
        for connector in connectors
        if connector.connector_type == DRM_MODE_CONNECTOR_DPI
        or connector.name.startswith("DPI")
    ]
    if not dpi:
        available = ", ".join(item.name for item in connectors)
        raise NoDpiConnector(
            "No DPI connector found. Append config/dpi-config.txt.example "
            "to /boot/firmware/config.txt and reboot, then run "
            f"scripts/validate-dpi.sh. Available connectors: {available}."
        )
    for connector in dpi:
        try:
            select_macintosh_mode(connector.modes)
        except KmsError:
            continue
        return connector
    return dpi[0]


def select_macintosh_mode(modes: Sequence[DisplayMode]) -> DisplayMode:
    """Return the 512x342 mode closest to the locked Macintosh Plus raster."""

    candidates = [
        mode
        for mode in modes
        if mode.hdisplay == TIMING.hactive and mode.vdisplay == TIMING.vactive
    ]
    if not candidates:
        available = ", ".join(
            f"{mode.hdisplay}x{mode.vdisplay}" for mode in modes
        ) or "none"
        raise KmsError(
            "DPI connector does not advertise 512x342. Apply "
            "config/dpi-config.txt.example and reboot. "
            f"Available modes: {available}."
        )

    target_clock = TIMING.pixel_clock_hz

    def _distance(mode: DisplayMode) -> Tuple[int, int, int]:
        return (
            abs(mode.htotal - TIMING.htotal),
            abs(mode.vtotal - TIMING.vtotal),
            abs(mode.clock_khz * 1000 - target_clock),
        )

    return min(candidates, key=_distance)


def _mode_from_info(info: _ModeInfo) -> DisplayMode:
    """Convert a kernel modeinfo structure into ``DisplayMode``."""

    name = bytes(info.name).split(b"\x00", 1)[0].decode("ascii", "replace")
    return DisplayMode(
        clock_khz=int(info.clock),
        hdisplay=int(info.hdisplay),
        hsync_start=int(info.hsync_start),
        hsync_end=int(info.hsync_end),
        htotal=int(info.htotal),
        vdisplay=int(info.vdisplay),
        vsync_start=int(info.vsync_start),
        vsync_end=int(info.vsync_end),
        vtotal=int(info.vtotal),
        flags=int(info.flags),
        name=name,
        hskew=int(info.hskew),
        vscan=int(info.vscan),
        vrefresh=int(info.vrefresh),
        type=int(info.type),
    )


def _mode_to_info(mode: DisplayMode) -> _ModeInfo:
    """Convert ``DisplayMode`` back into a kernel modeinfo structure."""

    info = _ModeInfo()
    info.clock = mode.clock_khz
    info.hdisplay = mode.hdisplay
    info.hsync_start = mode.hsync_start
    info.hsync_end = mode.hsync_end
    info.htotal = mode.htotal
    info.hskew = mode.hskew
    info.vdisplay = mode.vdisplay
    info.vsync_start = mode.vsync_start
    info.vsync_end = mode.vsync_end
    info.vtotal = mode.vtotal
    info.vscan = mode.vscan
    info.vrefresh = mode.vrefresh
    info.flags = mode.flags
    info.type = mode.type
    encoded = mode.name.encode("ascii", "replace")[: DRM_DISPLAY_MODE_LEN - 1]
    info.name = encoded
    return info


def _pointer(array: ctypes.Array) -> int:
    """Return a u64 address for a ctypes array, or 0 for an empty buffer."""

    if len(array) == 0:
        return 0
    return ctypes.addressof(array)


def _ioctl(fd: int, request: int, buf: object = None) -> None:
    """Issue a DRM ioctl and translate common errno values."""

    import fcntl

    try:
        if buf is None:
            fcntl.ioctl(fd, request)
        else:
            fcntl.ioctl(fd, request, buf, True)
    except OSError as exc:
        raise _kms_os_error(exc) from exc


def _kms_os_error(exc: OSError, path: Optional[Path] = None) -> KmsError:
    """Wrap a DRM ``OSError`` with an operator-facing message."""

    where = f" ({path})" if path is not None else ""
    if exc.errno in {errno.EACCES, errno.EPERM}:
        return KmsError(
            f"Permission denied opening the DRM device{where}. Add the "
            "user to the video group and re-login."
        )
    if exc.errno == errno.EBUSY:
        return KmsError(
            f"The DRM device is busy{where}. Stop the desktop compositor, "
            "plymouth, kmstest, or another DRM client and retry."
        )
    return KmsError(f"DRM ioctl failed{where}: {exc}")


class LinuxDrmDisplay:
    """Mapped RGB565 framebuffer on one DPI connector."""

    def __init__(self, device: Path, connector: str = "") -> None:
        self.device = Path(device)
        self.connector_filter = connector
        self.width = 0
        self.height = 0
        self.pitch = 0
        self.description = ""
        self._fd = -1
        self._fb_id = 0
        self._handle = 0
        self._crtc_id = 0
        self._connector_id = 0
        self._map = None
        self._mode: Optional[DisplayMode] = None

    def open(self) -> None:
        """Open the card, attach the DPI connector, and map an RGB565 FB."""

        if self._fd >= 0:
            return
        try:
            self._fd = os.open(self.device, os.O_RDWR | os.O_CLOEXEC)
        except OSError as exc:
            raise _kms_os_error(exc, self.device) from exc

        try:
            self._set_universal_planes()
            try:
                _ioctl(self._fd, DRM_IOCTL_SET_MASTER)
            except KmsError as exc:
                cause = exc.__cause__
                if isinstance(cause, OSError) and cause.errno == errno.EBUSY:
                    raise
            crtc_ids, connectors = self._probe_connectors()
            chosen = select_dpi_connector(connectors, self.connector_filter)
            mode = select_macintosh_mode(chosen.modes)
            self._create_framebuffer(mode.hdisplay, mode.vdisplay)
            crtc_id = self._set_crtc_with_fallback(
                chosen,
                crtc_ids,
                mode,
            )
            self.width = mode.hdisplay
            self.height = mode.vdisplay
            self._mode = mode
            self._crtc_id = crtc_id
            self._connector_id = chosen.connector_id
            self.description = (
                f"{self.device} {chosen.name} "
                f"{mode.hdisplay}x{mode.vdisplay} RGB565 "
                f"{mode.refresh_hz:.2f} Hz"
            )
        except Exception:
            self.close()
            raise

    def blit(self, rgb565: np.ndarray) -> None:
        """Copy one RGB565 frame into the mapped dumb buffer."""

        if self._map is None:
            raise KmsError("KMS display has not been started")
        frame = np.asarray(rgb565)
        if frame.shape != (self.height, self.width):
            raise KmsError(
                f"Frame {frame.shape[1]}x{frame.shape[0]} does not match "
                f"DPI mode {self.width}x{self.height}"
            )
        if frame.dtype != np.uint16:
            raise KmsError("DPI framebuffer writes require RGB565 uint16 data")
        framebuffer = np.ndarray(
            (self.height, self.width),
            dtype=np.uint16,
            buffer=self._map,
            strides=(self.pitch, 2),
        )
        framebuffer[:, :] = frame

    def close(self) -> None:
        """Disable the CRTC, unmap the framebuffer, and close the card."""

        if self._fd < 0:
            return
        try:
            self._disable_crtc()
        except KmsError:
            pass
        if self._map is not None:
            self._map.close()
            self._map = None
        if self._fb_id:
            try:
                fb_id = ctypes.c_uint32(self._fb_id)
                _ioctl(self._fd, DRM_IOCTL_MODE_RMFB, fb_id)
            except KmsError:
                pass
            self._fb_id = 0
        if self._handle:
            try:
                destroy = _DestroyDumb()
                destroy.handle = self._handle
                _ioctl(self._fd, DRM_IOCTL_MODE_DESTROY_DUMB, destroy)
            except KmsError:
                pass
            self._handle = 0
        try:
            _ioctl(self._fd, DRM_IOCTL_DROP_MASTER)
        except KmsError:
            pass
        os.close(self._fd)
        self._fd = -1
        self.description = ""

    def _set_universal_planes(self) -> None:
        """Request universal planes; ignore cards that do not implement it."""

        request = _SetClientCap()
        request.cap = DRM_CLIENT_CAP_UNIVERSAL_PLANES
        request.value = 1
        try:
            _ioctl(self._fd, DRM_IOCTL_SET_CLIENT_CAP, request)
        except KmsError:
            return

    def _probe_connectors(self) -> Tuple[Tuple[int, ...], List[ConnectorInfo]]:
        """Return CRTC ids and connectors for this modesetting card."""

        resources = _CardRes()
        try:
            _ioctl(self._fd, DRM_IOCTL_MODE_GETRESOURCES, resources)
        except KmsError as exc:
            raise NoDpiConnector(
                f"{self.device} is not a KMS modesetting node"
            ) from exc
        for _ in range(3):
            n_crtcs = resources.count_crtcs
            n_connectors = resources.count_connectors
            n_encoders = resources.count_encoders
            n_fbs = resources.count_fbs
            crtcs = (ctypes.c_uint32 * max(n_crtcs, 1))()
            connectors = (ctypes.c_uint32 * max(n_connectors, 1))()
            encoders = (ctypes.c_uint32 * max(n_encoders, 1))()
            fbs = (ctypes.c_uint32 * max(n_fbs, 1))()
            resources.crtc_id_ptr = _pointer(crtcs) if n_crtcs else 0
            resources.connector_id_ptr = (
                _pointer(connectors) if n_connectors else 0
            )
            resources.encoder_id_ptr = _pointer(encoders) if n_encoders else 0
            resources.fb_id_ptr = _pointer(fbs) if n_fbs else 0
            resources.count_crtcs = n_crtcs
            resources.count_connectors = n_connectors
            resources.count_encoders = n_encoders
            resources.count_fbs = n_fbs
            _ioctl(self._fd, DRM_IOCTL_MODE_GETRESOURCES, resources)
            if (
                resources.count_crtcs <= n_crtcs
                and resources.count_connectors <= n_connectors
                and resources.count_encoders <= n_encoders
                and resources.count_fbs <= n_fbs
            ):
                break
        if resources.count_connectors == 0:
            raise NoDpiConnector(f"{self.device} has no DRM connectors")
        crtc_ids = tuple(int(crtcs[index]) for index in range(resources.count_crtcs))
        infos = [
            self._get_connector(int(connectors[index]))
            for index in range(resources.count_connectors)
        ]
        return crtc_ids, infos

    def _get_connector(self, connector_id: int) -> ConnectorInfo:
        """Fetch one connector, including its current mode list."""

        buf = _GetConnector()
        buf.connector_id = connector_id
        _ioctl(self._fd, DRM_IOCTL_MODE_GETCONNECTOR, buf)
        modes_array: Optional[ctypes.Array] = None
        encoders_array: Optional[ctypes.Array] = None
        for _ in range(3):
            n_modes = buf.count_modes
            n_props = buf.count_props
            n_encoders = buf.count_encoders
            modes_array = (_ModeInfo * max(n_modes, 1))()
            props = (ctypes.c_uint32 * max(n_props, 1))()
            prop_values = (ctypes.c_uint64 * max(n_props, 1))()
            encoders_array = (ctypes.c_uint32 * max(n_encoders, 1))()
            buf.modes_ptr = _pointer(modes_array) if n_modes else 0
            buf.props_ptr = _pointer(props) if n_props else 0
            buf.prop_values_ptr = _pointer(prop_values) if n_props else 0
            buf.encoders_ptr = _pointer(encoders_array) if n_encoders else 0
            buf.count_modes = n_modes
            buf.count_props = n_props
            buf.count_encoders = n_encoders
            _ioctl(self._fd, DRM_IOCTL_MODE_GETCONNECTOR, buf)
            if (
                buf.count_modes <= n_modes
                and buf.count_props <= n_props
                and buf.count_encoders <= n_encoders
            ):
                break
        assert modes_array is not None
        assert encoders_array is not None
        modes = tuple(
            _mode_from_info(modes_array[index])
            for index in range(buf.count_modes)
        )
        encoder_ids = tuple(
            int(encoders_array[index]) for index in range(buf.count_encoders)
        )
        return ConnectorInfo(
            connector_id=int(buf.connector_id),
            name=connector_name(buf.connector_type, buf.connector_type_id),
            connector_type=int(buf.connector_type),
            connection=int(buf.connection),
            encoder_id=int(buf.encoder_id),
            encoder_ids=encoder_ids,
            modes=modes,
        )

    def _crtc_candidates(
        self,
        connector: ConnectorInfo,
        crtc_ids: Sequence[int],
    ) -> List[int]:
        """Return CRTC ids that may be able to drive the connector."""

        encoder_ids = list(connector.encoder_ids)
        if connector.encoder_id:
            encoder_ids.insert(0, connector.encoder_id)
        possible: List[int] = []
        seen = set()
        for encoder_id in encoder_ids:
            if encoder_id in seen or encoder_id == 0:
                continue
            seen.add(encoder_id)
            encoder = _GetEncoder()
            encoder.encoder_id = encoder_id
            try:
                _ioctl(self._fd, DRM_IOCTL_MODE_GETENCODER, encoder)
            except KmsError:
                continue
            if encoder.crtc_id and encoder.crtc_id in crtc_ids:
                possible.insert(0, int(encoder.crtc_id))
            for index, crtc_id in enumerate(crtc_ids):
                if encoder.possible_crtcs & (1 << index):
                    possible.append(int(crtc_id))
        ordered: List[int] = []
        for crtc_id in possible + list(crtc_ids):
            if crtc_id not in ordered:
                ordered.append(crtc_id)
        if not ordered:
            raise KmsError(
                f"No CRTC can drive {connector.name} on {self.device}"
            )
        return ordered

    def _set_crtc_with_fallback(
        self,
        connector: ConnectorInfo,
        crtc_ids: Sequence[int],
        mode: DisplayMode,
    ) -> int:
        """Modeset using each candidate CRTC until one accepts the DPI."""

        last_error: Optional[KmsError] = None
        for crtc_id in self._crtc_candidates(connector, crtc_ids):
            try:
                self._set_crtc(crtc_id, connector.connector_id, mode)
                return crtc_id
            except KmsError as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise KmsError(f"Could not attach a CRTC to {connector.name}")

    def _create_framebuffer(self, width: int, height: int) -> None:
        """Allocate, export, and mmap a dumb RGB565 buffer."""

        import mmap

        create = _CreateDumb()
        create.width = width
        create.height = height
        create.bpp = 16
        _ioctl(self._fd, DRM_IOCTL_MODE_CREATE_DUMB, create)
        self._handle = int(create.handle)
        self.pitch = int(create.pitch)
        fb = _FbCmd2()
        fb.width = width
        fb.height = height
        fb.pixel_format = DRM_FORMAT_RGB565
        fb.handles[0] = self._handle
        fb.pitches[0] = self.pitch
        _ioctl(self._fd, DRM_IOCTL_MODE_ADDFB2, fb)
        self._fb_id = int(fb.fb_id)
        mapped = _MapDumb()
        mapped.handle = self._handle
        _ioctl(self._fd, DRM_IOCTL_MODE_MAP_DUMB, mapped)
        self._map = mmap.mmap(
            self._fd,
            int(create.size),
            mmap.MAP_SHARED,
            mmap.PROT_READ | mmap.PROT_WRITE,
            offset=int(mapped.offset),
        )
        self.width = width
        self.height = height
        black = np.zeros((height, width), dtype=np.uint16)
        self.blit(black)

    def _set_crtc(
        self,
        crtc_id: int,
        connector_id: int,
        mode: DisplayMode,
    ) -> None:
        """Modeset the CRTC onto the DPI connector with the mapped FB."""

        connector_ids = (ctypes.c_uint32 * 1)(connector_id)
        request = _ModeCrtc()
        request.set_connectors_ptr = ctypes.addressof(connector_ids)
        request.count_connectors = 1
        request.crtc_id = crtc_id
        request.fb_id = self._fb_id
        request.mode_valid = 1
        request.mode = _mode_to_info(mode)
        _ioctl(self._fd, DRM_IOCTL_MODE_SETCRTC, request)

    def _disable_crtc(self) -> None:
        """Blank the CRTC so the framebuffer can be released."""

        if self._crtc_id == 0 or self._fd < 0:
            return
        request = _ModeCrtc()
        request.crtc_id = self._crtc_id
        request.fb_id = 0
        request.mode_valid = 0
        _ioctl(self._fd, DRM_IOCTL_MODE_SETCRTC, request)


def list_dri_cards() -> List[Path]:
    """Return primary DRM card nodes, ignoring render-only nodes."""

    directory = Path("/dev/dri")
    if not directory.is_dir():
        return []
    return sorted(directory.glob("card*"))


def open_dpi_display(
    device: str = "",
    connector: str = "",
) -> LinuxDrmDisplay:
    """Open the first DRM card that can paint the Macintosh DPI raster.

    ``device`` selects a specific ``/dev/dri/cardN`` node. ``connector``
    selects a connector name such as ``DPI-1``. Empty values auto-detect
    a DPI connector that advertises 512x342.
    """

    if device:
        display = LinuxDrmDisplay(Path(device), connector)
        display.open()
        return display

    cards = list_dri_cards()
    if not cards:
        raise KmsError(
            "No /dev/dri/card* nodes found. This command must run on the "
            "Raspberry Pi after the KMS/DPI overlay is applied."
        )

    errors: List[str] = []
    for card in cards:
        display = LinuxDrmDisplay(card, connector)
        try:
            display.open()
            return display
        except NoDpiConnector as exc:
            errors.append(f"{card}: {exc}")
        except KmsError as exc:
            display.close()
            raise KmsError(f"{card}: {exc}") from exc
    raise NoDpiConnector(" ".join(errors) if errors else "No DPI connector found")
