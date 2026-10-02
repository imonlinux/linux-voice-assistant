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
# Uninstall: run with --uninstall to return the system to the state of a
# fresh Raspberry Pi OS install (same packages). Every config change this
# installer makes is reverted: the dtparam/i2s-mmap lines it uncommented or
# added in config.txt, the i2c-dev entry in /etc/modules, both HAT overlays,
# /etc/voicecard with the ALSA config, the mixer-state symlink (pre-install
# backup restored), and any legacy DKMS driver. Installed packages are left
# in place.
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

usage() {
  cat <<EOF
Usage: sudo ${0##*/} [--uninstall]

  (no args)    Detect the ReSpeaker 2-Mic HAT revision and install the
               matching mainline driver overlay (v1 WM8960, v2 AIC3104).
  --uninstall  Return the system to the state of a fresh Raspberry Pi OS
               install (installed packages are kept): remove the device tree
               overlays (both revisions) and every config.txt line this
               installer added or uncommented (dtparam=i2c_arm=on / i2s=on /
               spi=on, dtoverlay=i2s-mmap, the HAT overlay entries), the
               i2c-dev entry in /etc/modules, /etc/voicecard with the
               asound.conf symlink, and the wm8960 mixer-state symlink
               (pre-install backup restored). Any legacy seeed-voicecard
               DKMS driver is removed too.
EOF
}

mode="install"
case "${1:-}" in
  "") ;;
  --uninstall) mode="uninstall" ;;
  -h|--help) usage; exit 0 ;;
  *) echo "Unknown option: $1" >&2; usage >&2; exit 1 ;;
esac

# Locate the boot partition (bookworm moved config/overlays to /boot/firmware)
if [ -d /boot/firmware/overlays ]; then
  boot_dir=/boot/firmware
else
  boot_dir=/boot
fi
config="${boot_dir}/config.txt"
overlays_dir="${boot_dir}/overlays"

# --- uninstall ----------------------------------------------------------------

# Reverses this installer as narrowly as possible. Everything below mirrors
# an install step; nothing else is touched.
do_uninstall() {
  removed=0

  # legacy DKMS driver from the old Seeed installer (same cleanup as install)
  if command -v dkms >/dev/null 2>&1 && dkms status 2>/dev/null | grep -q "^seeed-voicecard/"; then
    echo "Removing legacy seeed-voicecard DKMS module..."
    modprobe -r seeed-voicecard 2>/dev/null || true
    dkms remove -m seeed-voicecard -v 0.3 --all || true
    rm -rf /usr/src/seeed-voicecard-0.3 /var/lib/dkms/seeed-voicecard
    removed=1
  fi
  if [ -f /lib/systemd/system/seeed-voicecard.service ]; then
    systemctl disable --now seeed-voicecard.service 2>/dev/null || true
    rm -f /lib/systemd/system/seeed-voicecard.service
    systemctl daemon-reload 2>/dev/null || true
    removed=1
  fi
  rm -f /usr/bin/seeed-voicecard
  # i2c-dev: added by this installer; snd-soc-ac108: legacy DKMS leftover
  sed -i -e '/^i2c-dev$/d' -e '/^snd-soc-ac108$/d' /etc/modules 2>/dev/null || true

  # config.txt: revert to the stock state. Stock Raspberry Pi OS ships the
  # three interface dtparams COMMENTED OUT, so removing the uncommented lines
  # restores exactly what a fresh image has (commented forms are untouched).
  # End anchor omitted from the seeed patterns so entries with extra
  # parameters are caught too.
  if [ -f "${config}" ]; then
    sed -i \
      -e '/^dtparam=i2c_arm=on$/d' \
      -e '/^dtparam=i2s=on$/d' \
      -e '/^dtparam=spi=on$/d' \
      -e '/^dtoverlay=seeed-2mic-voicecard/d' \
      -e '/^dtoverlay=seeed-2mic-v2-voicecard/d' \
      -e '/^dtoverlay=i2s-mmap/d' \
      "${config}"
  fi

  # compiled overlays from the boot partition
  for overlay_file in \
    "${overlays_dir}/seeed-2mic-voicecard.dtbo" \
    "${overlays_dir}/seeed-2mic-v2-voicecard.dtbo"
  do
    if [ -f "${overlay_file}" ]; then
      rm -f "${overlay_file}"
      echo "Removed ${overlay_file}"
      removed=1
    fi
  done

  # remove the overlays from the live device tree (best effort); any residual
  # runtime state (loaded modules) clears on reboot
  if command -v dtoverlay >/dev/null 2>&1; then
    dtoverlay -r seeed-2mic-voicecard 2>/dev/null || true
    dtoverlay -r seeed-2mic-v2-voicecard 2>/dev/null || true
    dtoverlay -r i2s-mmap 2>/dev/null || true
  fi

  # ALSA configuration
  if [ -L /etc/asound.conf ] && \
     [ "$(readlink /etc/asound.conf)" = "/etc/voicecard/asound_2mic.conf" ]; then
    rm -f /etc/asound.conf
    echo "Removed /etc/asound.conf symlink."
    removed=1
  fi
  if [ -d /etc/voicecard ]; then
    rm -rf /etc/voicecard
    echo "Removed /etc/voicecard."
    removed=1
  fi

  # mixer state: drop our wm8960 symlink; restore the pre-install backup this
  # installer left, if one exists and no real state file has replaced it
  if [ -L /var/lib/alsa/asound.state ] && \
     readlink /var/lib/alsa/asound.state | grep -q wm8960_asound.state; then
    rm -f /var/lib/alsa/asound.state
    echo "Removed /var/lib/alsa/asound.state symlink."
    removed=1
    if [ ! -e /var/lib/alsa/asound.state ]; then
      backup="$(find /var/lib/alsa -maxdepth 1 -name 'asound.state.bak.*' 2>/dev/null | sort | tail -1)"
      if [ -n "${backup}" ]; then
        mv "${backup}" /var/lib/alsa/asound.state
        echo "Restored pre-install mixer state from ${backup}."
      fi
    fi
  fi

  echo
  if [ "${removed}" -eq 0 ]; then
    echo "Nothing to uninstall: no ReSpeaker 2-Mic driver state found."
  else
    if command -v aplay >/dev/null 2>&1 && aplay -l 2>/dev/null | grep -q seeed2micvoicec; then
      echo "Done. The card is still registered; reboot to finish removal."
    else
      echo "Done. ReSpeaker 2-Mic driver support removed."
    fi
    echo
    echo "Config files are back to their fresh-install state. The packages"
    echo "installed alongside the driver (alsa-utils, i2c-tools,"
    echo "device-tree-compiler) were left in place."
  fi
}

if [ "${mode}" = "uninstall" ]; then
  do_uninstall
  exit 0
fi

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
apt-get install --no-install-recommends --yes i2c-tools

# I2C is off by default on a fresh image. Enable it BEFORE probing: config.txt
# entry for the next boot, live dtparam for this session, i2c-dev for the
# /dev/i2c-* character devices (raspi-config does the same three things).
touch "${config}"
grep -q "^dtparam=i2c_arm=on" "${config}" || echo "dtparam=i2c_arm=on" >> "${config}"
grep -qx "i2c-dev" /etc/modules 2>/dev/null || echo "i2c-dev" >> /etc/modules
modprobe i2c-dev 2>/dev/null || true
if command -v dtparam >/dev/null 2>&1; then
  [ -e /dev/i2c-1 ] || dtparam i2c_arm=on 2>/dev/null || true
  dtparam i2s=on 2>/dev/null || true
fi
if [ ! -e /dev/i2c-1 ]; then
  echo "ERROR: no I2C bus on this system (/dev/i2c-1 is missing)."
  echo "       dtparam=i2c_arm=on was added to ${config}; reboot and re-run"
  echo "       this script, and check that the HAT is seated."
  exit 1
fi
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
