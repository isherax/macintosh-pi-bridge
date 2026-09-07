# Macintosh Pi Bridge

This project turns an HDMI signal from an external Linux computer into a
512×342, 1-bit monochrome video stream suitable for a later Macintosh Plus
CRT output stage. A Raspberry Pi handles capture and conversion; it is not the
computer running the desktop.

The current stage uses a USB UVC HDMI capture dongle and a network preview.
The CRT electrical interface and Raspberry Pi DPI output are deliberately
separate and are not enabled by this repository yet.

## Design goals

- Inspect the capture hardware before selecting a pixel format or resolution.
- Keep the live path low-latency by dropping stale frames.
- Preserve the input aspect ratio while producing exactly 512×342 pixels.
- Optimize the final 1-bit image for readable text and UI elements.
- Keep capture and conversion separate from preview so a later DPI output can
  replace it.
- Use Linux V4L2/KMS/DPI hardware timing later; never bit-bang GPIO in Python.
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
│   └── inspect-capture.sh
├── src/
│   └── macbridge/
│       ├── __init__.py
│       ├── __main__.py
│       ├── cli.py
│       ├── config.py
│       ├── capture.py
│       ├── convert.py
│       ├── patterns.py
│       ├── pipeline.py
│       └── outputs/
│           ├── __init__.py
│           └── preview.py
└── tests/
    └── test_convert.py
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

The Pi user must be able to read the capture node. Check the group membership
and add the user to `video` if necessary:

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

Start a synthetic 512×342 test card:

```bash
PYTHONPATH=src .venv/bin/python -m macbridge pattern
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

## Run the live pipeline

With the source computer sending a supported HDMI mode:

```bash
PYTHONPATH=src python3 -m macbridge run --device /dev/video0
```

The command loads `config/local.yaml` if it exists, otherwise the example
defaults. It opens the selected capture device, converts each newest frame to
512×342 1-bit, and publishes an HTTP MJPEG preview.

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

The service account must be able to read the capture node, normally through
the `video` group.

## Future DPI output

`config/dpi-config.txt.example` records an experimental timing model only:

- 512 active horizontal pixels;
- approximately 704 total horizontal clocks;
- 342 active vertical lines;
- approximately 370 total vertical lines;
- approximately 15.67 MHz pixel clock;
- approximately 60.15 Hz refresh.

Do not apply that file blindly. Validate the timing and GPIO mapping against
the current Raspberry Pi OS KMS/DPI documentation and the actual analog-board
interface. The future output must use hardware-generated DPI/KMS timing and a
proper level buffer/interposer. The original logic board and analog-board power
loading are hardware concerns outside this software repository.

## Tests

Conversion tests do not need a Raspberry Pi or capture dongle:

```bash
.venv/bin/python -m pytest
```

## Safety and scope

This repository does not drive a CRT, change boot configuration, manipulate
GPIO timing, or connect to an analog board. Test the software pipeline with a
preview first, and treat the eventual Macintosh electrical interface as a
separate hardware project.
