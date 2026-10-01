#!/usr/bin/env python3
"""Locate strings and all BL call sites for a set of target file offsets."""
import struct
import sys

BASE = 0x9EFFFE00
path = sys.argv[1]
d = open(path, 'rb').read()

print('=== string search ===')
for s in (b'USB SERIAL PORT OPENED', b'USB SERIAL CONFIGED',
          b'usb calibrate configuration timeout', b'loglevel=7'):
    off = d.find(s)
    print('  %-38s file=%s  VA(if +0x9EFFFE00)=%s  VA(if +0x9EFFFE00-0x200)=%s'
          % (s.decode(), ('%#x' % off) if off >= 0 else 'NOT FOUND',
             ('%#x' % (off + BASE)) if off >= 0 else '-',
             ('%#x' % (off + BASE - 0x200)) if off >= 0 else '-'))

targets = {}
for a in sys.argv[2:]:
    targets[int(a, 0)] = int(a, 0)
if not targets:
    targets = {0x1A400: 0x1A400, 0x2CDFC: 0x2CDFC, 0x2CFD0: 0x2CFD0,
               0x2D0F0: 0x2D0F0, 0x30CA0: 0x30CA0, 0x1A0C0: 0x1A0C0}

print()
print('=== BL call sites targeting %s ===' %
      ', '.join('%#x' % t for t in targets))
for off in range(0, len(d) - 4, 4):
    w = struct.unpack_from('<I', d, off)[0]
    if (w & 0xFC000000) != 0x94000000:
        continue
    imm = w & 0x03FFFFFF
    if imm & 0x02000000:
        imm -= 0x04000000
    tf = (off + BASE) + imm * 4 - BASE
    if tf in targets:
        print('  file %#08x  bl %#010x' % (off, tf + BASE))
