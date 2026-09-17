#!/usr/bin/env bash

# Installs audio support for the ReSpeaker 2-Mics Pi HAT (WM8960) on
# Raspberry Pi OS and other distros on Raspberry Pi hardware.
#
# Kernel-version independent: uses ONLY mainline kernel drivers
# (snd-soc-simple-card + snd-soc-wm8960) via a device tree overlay.
# No kernel headers, no DKMS, no per-kernel driver branches. Works on any
# kernel >= 5.4 that ships those modules (stock Raspberry Pi OS, Debian,
# Kali/re4son and Fedora kernels all do).
#
# This replaces the old DKMS installer that downloaded per-kernel branches
# from HinTak/seeed-voicecard (those branches stopped at kernel 6.14, so
# kernels >= 6.15 needed a kernel downgrade). The ALSA card ID
# ("seeed2micvoicec") is unchanged, so existing LVA configs keep working.
# Running this script on a device with the legacy DKMS install upgrades it
# in place and removes the old module automatically.
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

if [ ! -f "${script_dir}/seeed-2mic-voicecard-overlay.dts" ]; then
  echo "ERROR: seeed-2mic-voicecard-overlay.dts not found next to this script"
  echo "       (looked in: ${script_dir})."
  exit 1
fi

kernel_major_minor="$(uname -r | cut -f1,2 -d.)"
# version-aware floor check (naive numeric comparison would reject 5.15 < 5.4)
if [ "$(printf '%s\n' 5.4 "${kernel_major_minor}" | sort -V | head -1)" != "5.4" ]; then
  echo "ERROR: kernel ${kernel_major_minor} is too old; this installer needs >= 5.4."
  exit 1
fi

echo "Checking for the HAT on i2c bus 1 (address 0x1a)..."
modprobe i2c-dev 2>/dev/null || true
apt-get install --no-install-recommends --yes i2c-tools
if ! i2cdetect -y -r 1 0x1a 0x1a 2>/dev/null | grep -qE "(^|[[:space:]])(1a|UU)"; then
  echo "ERROR: no device found at i2c address 0x1a."
  echo "       Is the ReSpeaker 2-Mics Pi HAT seated correctly?"
  exit 1
fi
echo "HAT detected."

echo "Checking kernel modules..."
missing=""
for mod in snd-soc-wm8960 snd-soc-simple-card; do
  if ! modinfo -n "${mod}" >/dev/null 2>&1; then
    grep -q "${mod}" "/lib/modules/$(uname -r)/modules.builtin" 2>/dev/null || missing="${missing} ${mod}"
  fi
done
if [ -n "${missing}" ]; then
  echo "ERROR: this kernel does not provide:${missing}"
  echo "       The mainline wm8960/simple-card drivers are required."
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
  -o "${temp_dir}/seeed-2mic-voicecard.dtbo" \
  "${script_dir}/seeed-2mic-voicecard-overlay.dts"

mkdir -p "${overlays_dir}" /etc/voicecard
cp "${temp_dir}/seeed-2mic-voicecard.dtbo" "${overlays_dir}/"

# --- boot configuration --------------------------------------------------------

echo "Updating ${config}..."
touch "${config}"
grep -q "^dtparam=i2c_arm=on" "${config}" || echo "dtparam=i2c_arm=on" >> "${config}"
grep -q "^dtparam=i2s=on" "${config}" || echo "dtparam=i2s=on" >> "${config}"
grep -q "^dtparam=spi=on" "${config}" || echo "dtparam=spi=on" >> "${config}"
grep -q "^dtoverlay=i2s-mmap" "${config}" || echo "dtoverlay=i2s-mmap" >> "${config}"
grep -q "^dtoverlay=seeed-2mic-voicecard" "${config}" || echo "dtoverlay=seeed-2mic-voicecard" >> "${config}"

# --- ALSA configuration --------------------------------------------------------

echo "Installing ALSA configuration..."
cp "${script_dir}/asound_2mic.conf" /etc/voicecard/asound_2mic.conf
cp "${script_dir}/wm8960_asound.state" /etc/voicecard/wm8960_asound.state

ln -sfn /etc/voicecard/asound_2mic.conf /etc/asound.conf

# alsa-state.service restores /var/lib/alsa/asound.state on every boot;
# point it at our card's state file (same approach the seeed installer used).
if [ -f /var/lib/alsa/asound.state ] && [ ! -L /var/lib/alsa/asound.state ]; then
  mv /var/lib/alsa/asound.state "/var/lib/alsa/asound.state.bak.$(date +%s)"
fi
ln -sfn /etc/voicecard/wm8960_asound.state /var/lib/alsa/asound.state

# --- try to bring the card up without a reboot ---------------------------------

if aplay -l 2>/dev/null | grep -q seeed2micvoicec; then
  echo "Card already registered; restoring mixer state."
  alsactl restore 2>/dev/null || true
elif command -v dtoverlay >/dev/null 2>&1 && dtoverlay -d "${overlays_dir}" seeed-2mic-voicecard 2>/dev/null; then
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
