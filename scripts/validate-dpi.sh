#!/usr/bin/env bash
#
# Confirm the Pi 4 KMS/DPI pinmux and 512x342 mode after applying
# config/dpi-config.txt.example. Run this on the Raspberry Pi after reboot.
#
# Pinmux assignment does not put pixels on GPIO19. VIDEO is valid only after
# a DRM client paints white/black on the DPI connector. Optional probe:
#   sudo apt install kms++-utils
#   kmstest

set -u

failures=0

fail() {
    printf 'FAIL: %s\n' "$1"
    failures=$((failures + 1))
}

pass() {
    printf 'OK: %s\n' "$1"
}

warn() {
    printf 'WARN: %s\n' "$1"
}

section() {
    printf '\n===== %s =====\n' "$1"
}

require_raspberry_pi() {
    local model_path="/proc/device-tree/model"
    local model

    if [[ ! -r "$model_path" ]]; then
        printf 'This script must run on a Raspberry Pi.\n' >&2
        exit 1
    fi
    model="$(tr -d '\0' < "$model_path")"
    if [[ "$model" != Raspberry\ Pi* ]]; then
        printf 'This script must run on a Raspberry Pi (found: %s).\n' \
            "$model" >&2
        exit 1
    fi
    printf 'Model: %s\n' "$model"
    if [[ "$model" != *"Raspberry Pi 4"* && "$model" != *"Compute Module 4"* ]]; then
        warn "locked pin map is documented for Pi 4; this model may differ"
    fi
}

pinctrl_function() {
    local gpio="$1"
    pinctrl get "$gpio" 2>/dev/null | tr -s ' ' | tr '[:upper:]' '[:lower:]'
}

check_pinctrl_pin() {
    local gpio="$1"
    local expected_name="$2"
    local output alt

    if ! command -v pinctrl >/dev/null 2>&1; then
        fail "pinctrl is not installed (package raspi-utils)"
        return
    fi

    output="$(pinctrl_function "$gpio")"
    if [[ -z "$output" ]]; then
        fail "GPIO${gpio}: pinctrl returned no output"
        return
    fi
    printf 'GPIO%s: %s\n' "$gpio" "$(pinctrl get "$gpio")"

    alt="$(printf '%s\n' "$output" | awk '{print $2}')"
    if [[ "$alt" != "a2" ]]; then
        fail "GPIO${gpio} is '${alt}', expected ALT2 (a2) for DPI"
        return
    fi
    if [[ "$output" != *"$expected_name"* ]]; then
        fail "GPIO${gpio} ALT2 label does not include ${expected_name}"
        return
    fi
    pass "GPIO${gpio} is ALT2 ${expected_name}"
}

check_dpi_mode() {
    local found_connector=0
    local found_mode=0
    local drm_path modes_path status_path connector

    if command -v kmsprint >/dev/null 2>&1; then
        printf '%s\n' "$(kmsprint)"
        if kmsprint | grep -qi 'DPI'; then
            found_connector=1
        fi
    else
        warn "kmsprint not found; install kms++-utils or use sysfs fallback"
    fi

    for drm_path in /sys/class/drm/card*-DPI-*; do
        if [[ ! -e "$drm_path" ]]; then
            continue
        fi
        found_connector=1
        connector="$(basename "$drm_path")"
        status_path="${drm_path}/status"
        modes_path="${drm_path}/modes"
        if [[ -r "$status_path" ]]; then
            printf '%s status: %s' "$connector" "$(tr -d '\n' < "$status_path")"
            printf '\n'
        fi
        if [[ -r "$modes_path" ]] && grep -q '512x342' "$modes_path"; then
            found_mode=1
            printf '%s modes:\n' "$connector"
            cat "$modes_path"
        fi
    done

    if [[ "$found_connector" -eq 0 ]]; then
        fail "no DPI DRM connector found"
        return
    fi
    pass "DPI DRM connector is present"

    if [[ "$found_mode" -eq 0 ]]; then
        fail "DPI connector does not advertise 512x342"
        return
    fi
    pass "DPI connector advertises 512x342"
}

require_raspberry_pi

section "pinctrl"
if command -v pinctrl >/dev/null 2>&1; then
    check_pinctrl_pin 2 "vsync"
    check_pinctrl_pin 3 "hsync"
    check_pinctrl_pin 19 "dpi_d15"
else
    fail "pinctrl is not installed (package raspi-utils)"
fi

section "drm"
check_dpi_mode

section "video probe"
printf 'GPIO19 carries VIDEO only while a DRM client paints the DPI\n'
printf 'connector. Pinmux success does not mean the data pin is toggling.\n'
printf 'Optional: sudo apt install kms++-utils && kmstest\n'

if [[ "$failures" -ne 0 ]]; then
    printf '\nDPI validation failed with %s error(s).\n' "$failures"
    exit 1
fi

printf '\nDPI pinmux and 512x342 mode look correct.\n'
exit 0
