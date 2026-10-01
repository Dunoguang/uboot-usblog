#!/usr/bin/env python3
"""Disassemble the USB gadget wait helpers and find who calls them.

VA = file + 0x9EFFFE00   (so file 0x2D3B4 <-> VA 0x9F02D1B4)

usage: dis_wait.py <image> [start_file] [end_file]
"""
import struct
import sys

from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM

BASE = 0x9EFFFE00
path = sys.argv[1]
start = int(sys.argv[2], 0) if len(sys.argv) > 2 else 0x2D000
end = int(sys.argv[3], 0) if len(sys.argv) > 3 else 0x2D600

d = open(path, 'rb').read()
md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
md.detail = False

WATCH = {0x2D2F0: 'pump  sub_9F02D0F0',
         0x2D344: 'sub_9F02D144',
         0x2D3B4: 'wait  sub_9F02D1B4',
         0x2D3EC: 'wait  sub_9F02D1EC',
         0x30EA0: 'usb_gadget_handle_interrupts'}

print('=== disassembly  file %#x..%#x  (VA %#x..%#x) ===' %
      (start, end, start + BASE, end + BASE))
for ins in md.disasm(d[start:end], start + BASE):
    f = ins.address - BASE
    tag = ''
    if ins.mnemonic == 'bl':
        try:
            tgt = int(ins.op_str, 16)
            tag = '   --> %s' % WATCH.get(tgt - BASE, 'file %#x' % (tgt - BASE))
        except ValueError:
            pass
    print('%08x  %08x  %-8s %s%s' %
          (f, struct.unpack_from('<I', d, f)[0], ins.mnemonic, ins.op_str, tag))

print()
print('=== all BL call sites in the image that target the watch list ===')
want = set(WATCH)
for off in range(0, len(d) - 4, 4):
    w = struct.unpack_from('<I', d, off)[0]
    if (w & 0xFC000000) != 0x94000000:
        continue
    imm = w & 0x03FFFFFF
    if imm & 0x02000000:
        imm -= 0x04000000
    tgt = (off + BASE) + imm * 4
    tf = tgt - BASE
    if tf in want:
        print('  file %#08x  VA %#010x  bl %#010x   (%s)' % (off, off + BASE, tgt, WATCH[tf]))
