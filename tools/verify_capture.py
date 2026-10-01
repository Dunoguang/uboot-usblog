#!/usr/bin/env python3
"""Reconcile a live USB capture with the device's own uboot_log record.

    python3 tools/verify_capture.py p11.bin capture.bin

Dump p11 first (from recovery, as root):

    adb shell "dd if=/dev/block/mmcblk0p11 bs=4096" > p11.bin

The uboot_log partition is `header (magic 0xABCD, data offset 0x200, N entries)
+ N x 256 KB slots`, one slot per recorded boot, and a slot is only written when
the boot **completed**.  So for a successful boot this gives an independent,
device-side copy of the same console output, which is exactly what you want to
check a capture against.

The tool finds the slot belonging to the boot that produced the capture (by the
`lcd start init time:<n>ms` line, which is unique per boot), then compares from
`USB SERIAL PORT OPENED` to the end.
"""
import os
import struct
import sys

MAGIC = 0xABCD
HDR = 0x200
START_MARK = 'USB SERIAL PORT OPENED'
BOOT_MARK = 'lcd start init time:'


def load_slots(path):
    b = open(path, 'rb').read()
    if len(b) < HDR or struct.unpack_from('<I', b, 0)[0] != MAGIC:
        raise SystemExit('%s: not a uboot_log partition (magic %#x expected)'
                         % (path, MAGIC))
    n = struct.unpack_from('<I', b, 0x0C)[0]
    out = []
    for i in range(n):
        ln = struct.unpack_from('<I', b, 0x1C + i * 12 + 0)[0]
        sz = struct.unpack_from('<I', b, 0x1C + i * 12 + 4)[0]
        off = HDR + i * sz
        if off + ln > len(b):
            continue
        out.append((i, off, ln, b[off:off + ln].decode('utf-8', 'replace')))
    return out


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    p11, cap_path = sys.argv[1], sys.argv[2]

    cap = open(cap_path, 'rb').read().decode('utf-8', 'replace')
    host = cap.replace('\r\n', '\n').split('\n')
    while host and not host[-1].strip():
        host.pop()

    boot = next((l.strip() for l in host if BOOT_MARK in l), None)
    if boot is None:
        print('capture does not contain %r - cannot identify the boot' % BOOT_MARK)
        return 2
    print('capture : %s (%d bytes, %d lines)' % (cap_path, len(cap), len(host)))
    print('boot id : %s' % boot)

    slots = load_slots(p11)
    hits = [s for s in slots if boot in s[3]]
    if not hits:
        print('\nNo uboot_log slot contains that boot.')
        print('Either the boot did not complete (a slot is only written for a')
        print('successful boot), or this p11 dump predates it.')
        return 1

    i, off, ln, txt = hits[0]
    print('slot    : %d (offset %#x, %d bytes)\n' % (i, off, ln))

    dev_lines = txt.splitlines()
    try:
        start = next(k for k, l in enumerate(dev_lines) if START_MARK in l)
    except StopIteration:
        print('slot %d has no %r line' % (i, START_MARK))
        return 1
    dev = dev_lines[start:]

    n = max(len(host), len(dev))
    bad = 0
    for k in range(n):
        a = host[k].rstrip() if k < len(host) else '<missing>'
        b = dev[k].rstrip() if k < len(dev) else '<missing>'
        if a != b:
            bad += 1
            if bad <= 12:
                print('  line %3d DIFF\n     host   : %r\n     device : %r' % (k, a, b))

    print('host lines %d, device lines %d, mismatches %d' % (len(host), len(dev), bad))
    if bad == 0:
        print('\nIDENTICAL - the live USB log is complete for this boot.')
        return 0
    print('\nDIFFERENT - see the lines above.')
    return 1


if __name__ == '__main__':
    sys.exit(main())
