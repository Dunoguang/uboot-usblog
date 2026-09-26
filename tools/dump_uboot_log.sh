#!/bin/bash
# Dump the uboot_log partition from the watch (recovery, adb root) and
# parse one u-boot console log per boot slot.
# Usage: ./dump_uboot_log.sh [adb-serial] [outdir]
# uboot_log = /dev/block/mmcblk0p11 (4MB = header + N x 256KB slots).
# A slot holds the full pre-kernel u-boot console log of one boot, but it
# is only written when the boot completes successfully: a boot that stalls
# at any step (including before the kernel jump) leaves no record at all,
# so this partition cannot be used to debug a stuck/unbootable device.
set -e
W=${1:-}
DIR=${2:-.}
B=/dev/block/platform/soc/soc:ap-ahb/20600000.sdio/by-name
A="adb ${W:+-s $W}"
$A shell dd if=$B/uboot_log of=/tmp/uboot_log.bin bs=4096
$A pull /tmp/uboot_log.bin "$DIR/uboot_log.bin"
python3 "$(dirname "$0")/parse_uboot_log.py" "$DIR/uboot_log.bin"
