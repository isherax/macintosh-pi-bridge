#!/usr/bin/env bash
#
# Collect the information needed to choose a V4L2 capture mode.
# Run this on the Raspberry Pi with the HDMI dongle connected.

set -u

report_path="${1:-inspect-report.txt}"
report_dir="$(dirname "$report_path")"
mkdir -p "$report_dir"

exec > >(tee "$report_path") 2>&1

section() {
    printf '\n===== %s =====\n' "$1"
}

run_optional() {
    if command -v "$1" >/dev/null 2>&1; then
        "$@"
    else
        printf '%s: command not found\n' "$1"
    fi
}

section "kernel"
uname -a

section "os-release"
if [[ -r /etc/os-release ]]; then
    cat /etc/os-release
else
    printf '/etc/os-release is not readable\n'
fi

section "usb"
run_optional lsusb

section "v4l2 devices"
if command -v v4l2-ctl >/dev/null 2>&1; then
    v4l2-ctl --list-devices
else
    printf 'v4l2-ctl: command not found\n'
fi

section "video nodes"
video_node_found=0
for device in /dev/video*; do
    if [[ -e "$device" ]]; then
        video_node_found=1
        ls -l "$device"
    fi
done
if [[ "$video_node_found" -eq 0 ]]; then
    printf 'No /dev/video* nodes found\n'
fi

section "v4l2 formats"
if command -v v4l2-ctl >/dev/null 2>&1; then
    for device in /dev/video*; do
        if [[ -e "$device" ]]; then
            printf '\n--- %s ---\n' "$device"
            v4l2-ctl -d "$device" --list-formats-ext
        fi
    done
else
    printf 'v4l2-ctl: command not found\n'
fi

section "python"
run_optional python3 --version

section "opencv"
if command -v python3 >/dev/null 2>&1; then
    python3 - <<'PY'
try:
    import cv2
except ImportError as exc:
    print(f"OpenCV unavailable: {exc}")
else:
    print(f"OpenCV {cv2.__version__}")
PY
else
    printf 'python3: command not found\n'
fi

section "ffmpeg"
if command -v ffmpeg >/dev/null 2>&1; then
    ffmpeg -version | awk 'NR <= 3'
else
    printf 'ffmpeg: command not found\n'
fi

printf '\nInspection report written to %s\n' "$report_path"
