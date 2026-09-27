#!/bin/bash
# keep /dev/bus/usb nodes for the watch visible inside this container
while :; do
  for d in /sys/bus/usb/devices/*/; do
    [ -f "$d/idVendor" ] || continue
    [ "$(cat $d/idVendor 2>/dev/null)" = "1782" ] || continue
    IFS=: read maj min < "$d/dev" || continue
    bn=$(cat $d/busnum 2>/dev/null); dn=$(cat $d/devnum 2>/dev/null)
    p=$(printf '/dev/bus/usb/%03d/%03d' "$bn" "$dn")
    if [ ! -e "$p" ]; then
      mkdir -p "$(dirname "$p")"
      mknod "$p" c "$maj" "$min" && chmod 666 "$p" && echo "$(date +%H:%M:%S) created $p ($maj:$min)"
    fi
  done
  sleep 1
done
