# Macintosh Pi Bridge

This project turns an HDMI signal from an external Linux computer into a
512×342, 1-bit monochrome video stream suitable for a later Macintosh Plus
CRT output stage. A Raspberry Pi handles capture and conversion; it is not the
computer running the desktop.

The current stage captures HDMI, converts it to a 512×342 1-bit raster, and
paints that raster onto the Pi 4 DPI connector through KMS. An HTTP MJPEG
preview remains available over the network. The Pi 4 KMS/DPI pin map is locked
so the analog-board interposer can be wired once. This repository still does
not write boot configuration or attach to an analog board.

## Design goals

- Inspect the capture hardware before selecting a pixel format or resolution.
- Keep the live path low-latency by dropping stale frames.
- Preserve the input aspect ratio while producing exactly 512×342 pixels.
- Optimize the final 1-bit image for readable text and UI elements.
- Keep capture and conversion separate from output so KMS/DPI and the HTTP
  preview can run together or independently.
- Use Linux V4L2/KMS/DPI hardware timing; never bit-bang GPIO in Python.
- Keep hostnames, usernames, IP addresses, and local device details out of git.

## Current pipeline

```text
External Linux HDMI
        │
        ▼
USB UVC capture dongle
        │ V4L2
        ▼
macbridge.capture
        │ latest frame only
        ▼
macbridge.convert
  aspect fit → grayscale → threshold/dither → 512×342 1-bit
        │
        ├── KMS/DPI RGB565 (GPIO19 VIDEO, GPIO2/3 sync)
        └── HTTP MJPEG preview
```

The USB dongle is appropriate for bring-up and conversion testing. It may add
roughly 80–150 ms by itself, depending on the device and mode. That is often
acceptable for reading or demonstrating the CRT, but may feel slow for
interactive mouse and keyboard use. The software keeps only the newest frame
so processing cannot add an unbounded queue of additional latency.

If the dongle is too slow for the eventual display, an HDMI-to-CSI-2 capture
device is the intended future input swap. The conversion and output interfaces
should not need to change.

## Repository layout

```text
macintosh-pi-bridge/
├── README.md
├── LICENSE
├── requirements.txt
├── .gitignore
├── config/
│   ├── config.example.yaml
│   └── dpi-config.txt.example
├── systemd/
│   └── macbridge.service
├── scripts/
│   ├── inspect-capture.sh
│   └── validate-dpi.sh
├── src/
│   └── macbridge/
│       ├── __init__.py
│       ├── __main__.py
│       ├── cli.py
│       ├── config.py
│       ├── capture.py
│       ├── convert.py
│       ├── dpi.py
│       ├── drm.py
│       ├── patterns.py
│       ├── pipeline.py
│       └── outputs/
│           ├── __init__.py
│           ├── kms.py
│           └── preview.py
└── tests/
    ├── test_convert.py
    ├── test_dpi.py
    └── test_kms.py
```

`config/local.yaml` is intentionally absent from the repository. Copy the
example to make local changes:

```bash
cp config/config.example.yaml config/local.yaml
```

Do not commit that local file if it contains a device path, network setting,
or other machine-specific value.

The committed example is a tested profile for the original Macintosh Plus
M0001A display: 512×342 monochrome output from MJPEG 640×480 at 60 Hz on
`/dev/video0`, with threshold conversion tuned for readable terminal and icon
strokes. Run `scripts/inspect-capture.sh` and override `config/local.yaml` if
your capture device uses different values.

## Install on Raspberry Pi OS Lite

Install the system tools and libraries first:

```bash
sudo apt update
sudo apt install -y \
  ffmpeg \
  git \
  python3-numpy \
  python3-opencv \
  python3-pip \
  python3-pytest \
  python3-setuptools \
  python3-venv \
  python3-wheel \
  python3-yaml \
  v4l-utils
```

This project uses the `v4l2-ctl` command and OpenCV directly; no Python V4L2
binding is required.

For the first Pi hardware test, use the distribution packages directly with
`PYTHONPATH=src`; this avoids downloading a second OpenCV build. A virtual
environment is optional.

For an isolated Python environment:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

## First bring-up over SSH

For an uncommitted working tree, `rsync` is a convenient way to transfer
updates to a Pi. It avoids copying a host-specific virtual environment or
local configuration:

```bash
rsync -av \
  --exclude '.git/' \
  --exclude '.venv/' \
  --exclude '.pytest_cache/' \
  --exclude '__pycache__/' \
  --exclude 'config/local.yaml' \
  --exclude '*.egg-info/' \
  ./ user@raspberrypi.local:~/macintosh-pi-bridge/
```

The Pi user must be able to read the capture node and `/dev/dri`. Check the
group membership and add the user to `video` if necessary:

```bash
id -nG
sudo usermod -aG video "$USER"
```

Log out and back in after changing group membership. The repository contains
no SSH host, user, key, or password; replace the placeholders in these
commands locally.

## Inspect the Pi and capture dongle

Run this on the Pi after connecting the HDMI capture dongle:

```bash
./scripts/inspect-capture.sh
```

The script reports:

- kernel and Raspberry Pi OS version;
- USB devices;
- available V4L2 device nodes;
- every format, size, and interval reported by each `/dev/video*` node;
- installed `ffmpeg`, Python, and OpenCV versions.

The default report is written to `inspect-report.txt`, which is ignored by
git. If asking for help, share the relevant device and format sections after
removing any hardware serial numbers.

The source Linux computer should be configured to a mode the dongle advertises.
Do not assume that `/dev/video0`, MJPEG, 1080p, or 60 Hz is available until the
inspection output confirms it.

### Activate and configure the HDMI source

Some UVC dongles do not assert HDMI hotplug/EDID until the Pi has opened the
video node. Start the capture test or live pipeline on the Pi before checking
the source computer's HDMI connector.

For the validated Macintosh Plus profile, the source computer and dongle use
`640x480` at `60 Hz`. On an X11 source session, the output can be selected
with:

```bash
xrandr --query
xrandr --output HDMI-A-0 --mode 640x480 --rate 60
```

Connector names differ by GPU and driver. If the command is being run over
SSH, set the graphical session environment first; an SSH shell normally has
no `DISPLAY`:

```bash
loginctl list-sessions
loginctl show-session <session-id> -p Type -p Display
DISPLAY=:0 XAUTHORITY=/home/<graphical-user>/.Xauthority xrandr --query
```

If the HDMI connector remains disconnected, re-seat or power-cycle the HDMI
cable and dongle while the Pi capture is active. A powered HDMI splitter or
EDID emulator may be required if the dongle does not provide a stable
hotplug signal.

For a small source display, temporarily prevent X11 screen blanking while
testing:

```bash
DISPLAY=:0 XAUTHORITY=/home/<graphical-user>/.Xauthority xset s off
DISPLAY=:0 XAUTHORITY=/home/<graphical-user>/.Xauthority xset -dpms
DISPLAY=:0 XAUTHORITY=/home/<graphical-user>/.Xauthority xset s noblank
DISPLAY=:0 XAUTHORITY=/home/<graphical-user>/.Xauthority xset dpms force on
```

These settings are only for the test session; consider the security and power
implications before making them permanent.

## Test capture without starting the pipeline

After inspection, the quickest hardware test is to use the distribution
Python packages directly:

```bash
PYTHONPATH=src python3 -m macbridge capture-test \
  --device /dev/video0 --frames 60
```

If a virtual environment was installed:

```bash
PYTHONPATH=src .venv/bin/python -m macbridge capture-test --frames 30
```

This uses the configured V4L2 device and mode and prints the actual mode
returned by OpenCV. Run `scripts/inspect-capture.sh` before adapting the
capture fields for a different dongle. To test a known node without
committing a configuration change:

```bash
PYTHONPATH=src .venv/bin/python -m macbridge capture-test \
  --device /dev/video0 --frames 30
```

The selected pixel format can be overridden in `config/local.yaml` after
inspection, for example with `MJPG` or `YUYV`.

## Test conversion and preview without a dongle

Start a synthetic 512×342 test card. On a machine without the DPI overlay,
keep the HTTP preview only:

```bash
PYTHONPATH=src .venv/bin/python -m macbridge pattern --output preview
```

The preview server listens on `http://127.0.0.1:5000/` by default. View it
with a browser or with FFmpeg:

```bash
ffplay -fflags nobuffer -flags low_delay \
  http://127.0.0.1:5000/stream.mjpg
```

For a headless Pi, forward the port from another machine. Replace the
placeholder account and host with your own values; neither belongs in the repo:

```bash
ssh -N -L 15000:127.0.0.1:5000 user@raspberrypi.local
ffplay -fflags nobuffer -flags low_delay \
  http://127.0.0.1:15000/stream.mjpg
```

## Paint the Macintosh CRT

After the analog-board interposer is wired, the DPI overlay is appended to
`/boot/firmware/config.txt`, and the Pi has rebooted, confirm pinmux and the
512×342 mode:

```bash
./scripts/validate-dpi.sh
```

Then paint a held test card on the DPI connector. Leave the process running;
exiting the DRM client blanks VIDEO:

```bash
PYTHONPATH=src python3 -m macbridge kms-test
```

The test card uses the same conversion settings as the live pipeline, including
`convert.invert`. If the CRT shows a negative image, set `invert: true` in
`config/local.yaml` and run `kms-test` again.

The service account or SSH user must be in the `video` group for `/dev/dri`.
Stop a desktop compositor, plymouth, or `kmstest` if the card reports busy.

With HDMI capture running, paint live frames and keep the network preview:

```bash
PYTHONPATH=src python3 -m macbridge run --device /dev/video0
```

`run` defaults to both KMS and HTTP preview. Override for a single backend:

```bash
PYTHONPATH=src python3 -m macbridge run --output kms
PYTHONPATH=src python3 -m macbridge pattern --output preview
```

## Run the live pipeline

With the source computer sending a supported HDMI mode:

```bash
PYTHONPATH=src python3 -m macbridge run --device /dev/video0
```

The command loads `config/local.yaml` if it exists, otherwise the example
defaults. It opens the selected capture device, converts each newest frame to
512×342 1-bit, paints RGB565 on the DPI connector, and publishes an HTTP MJPEG
preview. Use `--output preview` if the DPI overlay is not installed yet.

To leave the pipeline running after ending the SSH shell, use a process
supervisor or, for a temporary test:

```bash
nohup env PYTHONPATH=src python3 -m macbridge run \
  --device /dev/video0 >/tmp/macbridge.log 2>&1 </dev/null &
```

Conversion modes are configured in `config/local.yaml`:

```yaml
convert:
  mode: threshold        # threshold or floyd_steinberg
  threshold: 96
  fit: stretch
  invert: false
```

`stretch` fills the Macintosh Plus raster from the 4:3 capture input. Use
`contain` instead if preserving source geometry is more important than filling
the raster; it adds black borders when needed. Floyd–Steinberg dithering can
preserve thin strokes and gradients, while thresholding usually produces
steadier text. The final CRT output will use the same `MonoFrame` data, not the
JPEG preview.

## Troubleshooting the validated setup

- **`xrandr: Can't open display` over SSH:** the SSH shell does not inherit
  the graphical session. Set `DISPLAY` and `XAUTHORITY`, or run the command
  from a terminal in the graphical session.
- **HDMI reports disconnected:** start the Pi capture first, then re-seat or
  power-cycle the HDMI cable and dongle. A powered splitter or EDID emulator
  may be needed for unreliable hotplug detection.
- **The preview is uniformly dark:** wake the source desktop and temporarily
  disable X11 screen blanking with the commands above. Check the source mode
  is `640x480@60`.
- **Terminal or icons are hard to read:** increase the source desktop's font
  and icon sizes. A 512×342 1-bit display cannot recover detail that was
  rendered smaller than a few source pixels.
- **`No DPI connector found`:** append the `dtoverlay`/`dtparam` lines from
  `config/dpi-config.txt.example` to `/boot/firmware/config.txt`, reboot, and
  run `scripts/validate-dpi.sh`. Use `--output preview` until that succeeds.
- **DRM device is busy:** stop the desktop compositor, plymouth, `kmstest`,
  or another `macbridge` process. Only one DRM master can own the card.
- **CRT is blank while `kms-test` is running:** the analog board often will
  not lock to a short generic HSYNC. Confirm `validate-dpi.sh` reports
  `512x342` and a 704-wide mode, then make sure `/boot/firmware/config.txt`
  uses the 12/178/2 and 1/4/23 porches from
  `config/dpi-config.txt.example`. Do not use `hbp=0` or `vfp=0`; VC4 can
  stall. Power the Mac off before editing boot config, reboot the Pi, then
  start `kms-test` before powering the Mac on again.
- **CRT is blank after `kms-test` exits:** the DRM client must keep running.
  Leave `kms-test` or `run` in the foreground, `nohup`, or systemd.
- **CRT image is inverted:** set `convert.invert: true` in
  `config/local.yaml`. Video polarity is not a GPIO change.

## systemd

The example unit assumes the project is installed at
`/opt/macintosh-pi-bridge` and a service account named `macbridge`. Edit those
generic placeholders for the target Pi, then install it:

```bash
sudo install -m 0644 systemd/macbridge.service \
  /etc/systemd/system/macbridge.service
sudo systemctl daemon-reload
sudo systemctl enable --now macbridge.service
sudo systemctl status macbridge.service
```

The service account must be able to read the capture node and the DRM device,
normally through the `video` group.

## Pi 4 KMS/DPI pin lock

Target platform: Raspberry Pi 4, Raspberry Pi OS Bookworm, KMS. The BCM2711
DPI block fixes VSYNC on GPIO2 and HSYNC on GPIO3. Monochrome VIDEO uses
RGB565 mode 2 and taps the red most-significant bit on GPIO19. Wire these
three signals through a 3.3 V level buffer; do not connect Pi GPIOs directly
to 5 V Macintosh analog-board TTL.

| Signal | BCM GPIO | Header pin | DPI function |
| --- | --- | --- | --- |
| VSYNC | 2 | 3 | LCD_VSYNC |
| HSYNC | 3 | 5 | LCD_HSYNC |
| VIDEO | 19 | 35 | DPI_D15 (RGB565 R7) |
| GND | — | 6, 9, 14, 20, 25, 30, 34, or 39 | ground |

`macbridge.dpi` is the software source of truth for that map. GPIO8 (B7) and
GPIO14 (G7) would also toggle if every RGB channel is 0 or 255; do not wire
them. A later custom overlay can mux only GPIO2/3/19 without moving the
wires.

`config/dpi-config.txt.example` is a Bookworm `vc4-kms-dpi-generic` fragment
for 512×342 at 15.6672 MHz (704×370 total, about 60.15 Hz) with compact-Mac
HSYNC at 178 clocks. It is not applied automatically. Legacy
`dtoverlay=dpi24` / `dpi_timings` entries are obsolete on Bookworm. After
reviewing pinmux conflicts in that file, append the `dtparam`/`dtoverlay`
lines to `/boot/firmware/config.txt` and reboot:

```bash
# Review the fragment, then append only the dtoverlay/dtparam lines.
sudo nano /boot/firmware/config.txt
sudo reboot
```

Bookworm already loads `dtoverlay=vc4-kms-v3d`; do not duplicate it. Porch
widths, sync pulse lengths, and sync polarity are overlay parameters and can
be tuned later without resoldering. Video polarity stays `convert.invert`.

On the Pi, confirm ALT2 pinmux and a 512×342 DPI mode:

```bash
./scripts/validate-dpi.sh
```

Pinmux success does not put pixels on GPIO19. VIDEO is valid only while a DRM
client holds the DPI connector. This repository's writer does that:

```bash
PYTHONPATH=src python3 -m macbridge kms-test
```

Optional probe after `sudo apt install kms++-utils`:

```bash
kmstest
```

`kmstest` is a diagnostic, not the live pipeline. Stop it before starting
`kms-test` or `run`; only one DRM master can own the card.

The live pipeline paints `MonoFrame` as RGB565 on the DPI connector and can
still publish HTTP MJPEG. The original logic board and analog-board power
loading remain hardware concerns outside this repository.

## Tests

Conversion, DPI pin-map, RGB565 packing, and KMS selection tests do not need a
Raspberry Pi or capture dongle:

```bash
.venv/bin/python -m pytest
```

## Safety and scope

This repository does not write `/boot/firmware/config.txt`, bit-bang GPIO, or
connect to an analog board. The KMS writer paints the DPI connector; level
translation and analog-board power remain a separate hardware project. Test
the software pipeline with `kms-test` before sending live HDMI.
