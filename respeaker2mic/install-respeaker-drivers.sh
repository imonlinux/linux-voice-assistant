#!/usr/bin/env bash

# Installs audio support for the ReSpeaker 2-Mics Pi HAT on
# Raspberry Pi OS and other distros on Raspberry Pi hardware.
#
# Auto-detects the HAT revision from the i2c bus and installs the matching
# mainline-only overlay:
#   - v1 (WM8960 codec,  i2c address 0x1a): snd-soc-wm8960
#   - v2 (TLV320AIC3104, i2c address 0x18): snd-soc-tlv320aic3x
#
# Kernel-version independent: uses ONLY mainline kernel drivers
# (snd-soc-simple-card + the codec driver above) via a device tree overlay.
# No kernel headers, no DKMS, no per-kernel driver branches. Works on any
# kernel >= 5.4 that ships those modules (stock Raspberry Pi OS, Debian,
# Kali/re4son and Fedora kernels all do).
#
# This replaces the old DKMS installer that downloaded per-kernel branches
# from HinTak/seeed-voicecard (those branches stopped at kernel 6.14, so
# kernels >= 6.15 needed a kernel downgrade). The ALSA card ID
# ("seeed2micvoicec") is unchanged on both HAT revisions, so existing LVA
# configs keep working. Running this script on a device with the legacy
# DKMS install upgrades it in place and removes the old module
# automatically.
#
# Note: the ReSpeaker 4-Mic/8-Mic HATs use the AC108 codec, which has no
# mainline driver. They still need Seeed's DKMS installer and are NOT
# handled by this script.
#
# Must be run with sudo.
# Requires: alsa-utils, i2c-tools, device-tree-compiler (installed below).

set -eo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Please run with sudo."
  exit 1
fi

# POSIX-safe: $0 works under bash AND sh (dash); ${BASH_SOURCE[0]} does not,
# and under sh its failure silently degrades script_dir to the CWD.
script_dir="$(cd "$(dirname "$0")" && pwd)"

# Locate the boot partition (bookworm moved config/overlays to /boot/firmware)
if [ -d /boot/firmware/overlays ]; then
  boot_dir=/boot/firmware
else
  boot_dir=/boot
fi
config="${boot_dir}/config.txt"
overlays_dir="${boot_dir}/overlays"

# --- sanity checks -----------------------------------------------------------

overlay_v1_dts="${script_dir}/seeed-2mic-voicecard-overlay.dts"
overlay_v2_dts="${script_dir}/seeed-2mic-v2-voicecard-overlay.dts"
if [ ! -f "${overlay_v1_dts}" ]; then
  echo "ERROR: seeed-2mic-voicecard-overlay.dts not found next to this script"
  echo "       (looked in: ${script_dir})."
  exit 1
fi
if [ ! -f "${overlay_v2_dts}" ]; then
  echo "ERROR: seeed-2mic-v2-voicecard-overlay.dts not found next to this script"
  echo "       (looked in: ${script_dir})."
  exit 1
fi

kernel_major_minor="$(uname -r | cut -f1,2 -d.)"
# version-aware floor check (naive numeric comparison would reject 5.15 < 5.4)
if [ "$(printf '%s\n' 5.4 "${kernel_major_minor}" | sort -V | head -1)" != "5.4" ]; then
  echo "ERROR: kernel ${kernel_major_minor} is too old; this installer needs >= 5.4."
  exit 1
fi

echo "Checking for the HAT on i2c bus 1..."
modprobe i2c-dev 2>/dev/null || true
apt-get install --no-install-recommends --yes i2c-tools
v1_found=0
v2_found=0
if i2cdetect -y -r 1 0x1a 0x1a 2>/dev/null | grep -qE "(^|[[:space:]])(1a|UU)"; then
  v1_found=1
fi
if i2cdetect -y -r 1 0x18 0x18 2>/dev/null | grep -qE "(^|[[:space:]])(18|UU)"; then
  v2_found=1
fi

if [ "${v1_found}" -eq 1 ] && [ "${v2_found}" -eq 1 ]; then
  echo "ERROR: devices found at BOTH 0x1a (v1/WM8960) and 0x18 (v2/AIC3104)."
  echo "       That combination is ambiguous; aborting. If something else"
  echo "       occupies one of these addresses, detach it and re-run."
  exit 1
fi

if [ "${v1_found}" -eq 1 ]; then
  hat_version="v1"
  hat_codec="WM8960"
  overlay_dts="${overlay_v1_dts}"
  overlay_name="seeed-2mic-voicecard"
  other_overlay_name="seeed-2mic-v2-voicecard"
  codec_module="snd-soc-wm8960"
elif [ "${v2_found}" -eq 1 ]; then
  hat_version="v2"
  hat_codec="TLV320AIC3104"
  overlay_dts="${overlay_v2_dts}"
  overlay_name="seeed-2mic-v2-voicecard"
  other_overlay_name="seeed-2mic-voicecard"
  codec_module="snd-soc-tlv320aic3x"
else
  echo "ERROR: no ReSpeaker 2-Mics Pi HAT found (v1 at 0x1a, v2 at 0x18)."
  echo "       Is the HAT seated correctly on the 40-pin header?"
  exit 1
fi
echo "HAT detected: ReSpeaker 2-Mics Pi HAT ${hat_version} (${hat_codec})."

echo "Checking kernel modules..."
missing=""
for mod in "${codec_module}" snd-soc-simple-card; do
  if ! modinfo -n "${mod}" >/dev/null 2>&1; then
    grep -q "${mod}" "/lib/modules/$(uname -r)/modules.builtin" 2>/dev/null || missing="${missing} ${mod}"
  fi
done
if [ -n "${missing}" ]; then
  echo "ERROR: this kernel does not provide:${missing}"
  echo "       The mainline ${hat_codec}/simple-card drivers are required."
  exit 1
fi

apt-get update
apt-get install --no-install-recommends --yes alsa-utils device-tree-compiler

# --- remove the legacy DKMS-based driver, if present -------------------------

if dkms status 2>/dev/null | grep -q "^seeed-voicecard/"; then
  echo "Removing legacy seeed-voicecard DKMS module..."
  modprobe -r seeed-voicecard 2>/dev/null || true
  dkms remove -m seeed-voicecard -v 0.3 --all || true
  rm -rf /usr/src/seeed-voicecard-0.3 /var/lib/dkms/seeed-voicecard
fi
if [ -f /lib/systemd/system/seeed-voicecard.service ]; then
  systemctl disable --now seeed-voicecard.service 2>/dev/null || true
  rm -f /lib/systemd/system/seeed-voicecard.service
fi
rm -f /usr/bin/seeed-voicecard
# snd-soc-ac108 was only for the 4-mic HAT; not needed here.
sed -i '/^snd-soc-ac108$/d' /etc/modules 2>/dev/null || true
systemctl daemon-reload 2>/dev/null || true

# --- build and install the overlay --------------------------------------------

echo "Compiling device tree overlay..."
temp_dir="$(mktemp -d)"
trap 'rm -rf "${temp_dir}"' EXIT

dtc -@ -I dts -O dtb \
  -o "${temp_dir}/${overlay_name}.dtbo" \
  "${overlay_dts}"

mkdir -p "${overlays_dir}" /etc/voicecard
cp "${temp_dir}/${overlay_name}.dtbo" "${overlays_dir}/"

# --- boot configuration --------------------------------------------------------

echo "Updating ${config}..."
touch "${config}"
grep -q "^dtparam=i2c_arm=on" "${config}" || echo "dtparam=i2c_arm=on" >> "${config}"
grep -q "^dtparam=i2s=on" "${config}" || echo "dtparam=i2s=on" >> "${config}"
grep -q "^dtparam=spi=on" "${config}" || echo "dtparam=spi=on" >> "${config}"
grep -q "^dtoverlay=i2s-mmap" "${config}" || echo "dtoverlay=i2s-mmap" >> "${config}"
# drop a stale entry for the other HAT revision (HAT was swapped), then add ours
sed -i "/^dtoverlay=${other_overlay_name}\$/d" "${config}"
grep -q "^dtoverlay=${overlay_name}" "${config}" || echo "dtoverlay=${overlay_name}" >> "${config}"

# --- ALSA configuration --------------------------------------------------------

echo "Installing ALSA configuration..."
cp "${script_dir}/asound_2mic.conf" /etc/voicecard/asound_2mic.conf

ln -sfn /etc/voicecard/asound_2mic.conf /etc/asound.conf

if [ "${hat_version}" = "v1" ]; then
  # wm8960 mixer state: restored by alsa-state.service on every boot.
  cp "${script_dir}/wm8960_asound.state" /etc/voicecard/wm8960_asound.state
  if [ -f /var/lib/alsa/asound.state ] && [ ! -L /var/lib/alsa/asound.state ]; then
    mv /var/lib/alsa/asound.state "/var/lib/alsa/asound.state.bak.$(date +%s)"
  fi
  ln -sfn /etc/voicecard/wm8960_asound.state /var/lib/alsa/asound.state
else
  # v2: wm8960_asound.state describes wm8960 mixer controls and does not
  # apply to the AIC3104. Drop a v1-era symlink so alsa-state.service
  # falls back to its default file, which the first `alsactl store` fills.
  if [ -L /var/lib/alsa/asound.state ] && \
     readlink /var/lib/alsa/asound.state | grep -q wm8960_asound.state; then
    rm -f /var/lib/alsa/asound.state
  fi
fi

# --- try to bring the card up without a reboot ---------------------------------

if aplay -l 2>/dev/null | grep -q seeed2micvoicec; then
  echo "Card already registered; restoring mixer state."
  alsactl restore 2>/dev/null || true
elif command -v dtoverlay >/dev/null 2>&1 && dtoverlay -d "${overlays_dir}" "${overlay_name}" 2>/dev/null; then
  sleep 2
  if aplay -l 2>/dev/null | grep -q seeed2micvoicec; then
    echo "Card registered live; no reboot needed. Restoring mixer state."
    alsactl restore 2>/dev/null || true
  fi
fi

if aplay -l 2>/dev/null | grep -q seeed2micvoicec; then
  echo
  echo "Done. The HAT is available as card 'seeed2micvoicec'."
  echo "Verify with:"
  echo "  arecord -D plughw:CARD=seeed2micvoicec -f cd -d 5 /tmp/test.wav && aplay /tmp/test.wav"
else
  echo
  echo 'Done. Please reboot the system to activate the HAT.'
  echo "After reboot, verify with:"
  echo "  aplay -l | grep seeed2micvoicec"
  echo "  arecord -D plughw:CARD=seeed2micvoicec -f cd -d 5 /tmp/test.wav && aplay /tmp/test.wav"
fi
