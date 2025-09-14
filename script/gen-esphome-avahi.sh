#!/usr/bin/env bash
set -euo pipefail
PORT=6053
NAME="%h (Linux Voice Assistant)"

mapfile -t IFACES < <(ip -o link show up | awk -F': ' '{print $2}' | grep -v '^lo$')

TXT="    <txt-record>platform=linux</txt-record>
"
LIST=""
for i in "${IFACES[@]}"; do
  mac=$(cat "/sys/class/net/$i/address")
  if [ -d "/sys/class/net/$i/wireless" ] || [[ $i == wl* ]]; then net=wifi; else net=ethernet; fi
  TXT+="    <txt-record>mac_${i}=${mac}</txt-record>
"
  TXT+="    <txt-record>net_${i}=${net}</txt-record>
"
  LIST+="${i},"
done
LIST=${LIST%,}

cat >/etc/avahi/services/esphome-lva.service <<XML
<?xml version="1.0" standalone='no'?><!--*-nxml-*-->
<!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<service-group>
  <name replace-wildcards="yes">${NAME}</name>
  <service>
    <type>_esphomelib._tcp</type>
    <port>${PORT}</port>
    <txt-record>ifaces=${LIST}</txt-record>
${TXT}  </service>
</service-group>
XML
