#!/usr/bin/env bash
set -euo pipefail

PORT=6053
NAME="%h"
SERVICE=/etc/avahi/services/esphome-lva.service

# Wait up to ~40s for any global IPv4 (cosmetic: to include ipv4_* TXT)
deadline=$((SECONDS+40))
while [ $SECONDS -lt $deadline ]; do
  ip -o -4 addr show up scope global | grep -q . && break || true
  sleep 1
done

# Collect all UP, non-loopback ifaces
mapfile -t IFACES < <(ip -o link show up | awk -F': ' '{print $2}' | grep -v '^lo$' | sort -u)

# Detect platform/board from the running system
PLATFORM="LINUX"
ARCH_RAW="$(uname -m)"
case "$ARCH_RAW" in
  aarch64|arm64) BOARD="aarch64" ;;
  x86_64|amd64)  BOARD="x86_64"  ;;
  *)             BOARD="$ARCH_RAW" ;;
esac

TXT="    <txt-record>platform=${PLATFORM}</txt-record>
"
TXT+="    <txt-record>board=${BOARD}</txt-record>
"

LIST=""
for i in "${IFACES[@]}"; do
  [ -e "/sys/class/net/$i/address" ] || continue
  mac=$(cat "/sys/class/net/$i/address")
  mac_nc=${mac//:/}   # no colons (ESPHome style)
  if [ -d "/sys/class/net/$i/wireless" ] || [[ "$i" == wl* ]]; then net=wifi; else net=ethernet; fi

  TXT+="    <txt-record>network=${net}</txt-record>
"
  TXT+="    <txt-record>mac=${mac_nc}</txt-record>
"
  LIST+="${i},"
done
LIST=${LIST%,}

cat >"$SERVICE" <<XML
<?xml version="1.0" standalone='no'?><!--*-nxml-*-->
<!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<service-group>
  <name replace-wildcards="yes">${NAME}</name>
  <service protocol="ipv4">
    <type>_esphomelib._tcp</type>
    <port>${PORT}</port>
    <txt-record>version=2025.9.0</txt-record>
${TXT}  </service>
</service-group>
XML

# Avahi will (re)publish on all active ifaces automatically
systemctl restart avahi-daemon || true
