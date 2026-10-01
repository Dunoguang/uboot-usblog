#!/usr/bin/env python3
"""Build uboot 0.0.4 - 0.0.2 with a *long* wait #2 window (diagnostic only).

Why: the u-boot gadget is only enumerated for about 5.5 s with 0.0.2, because
the calibrate state machine gives the host 2000 ms to open the port and then
moves on to panel init, where nothing polls usb_gadget_handle_interrupts().

That is enough for a libusb reader (it opens in milliseconds) but far too short
to operate a GUI driver installer by hand, or to let a slow vendor serial
driver (sprdvcom.sys, whose CreateFile sequence spans ~3.9 s) finish.

  0.0.4 bumps wait #2 to 60000 ms.  It is NOT a shipping image: with no host
  attached every boot pays the full 60 s.  Use it to install/verify the host
  side, then go back to 0.0.2.

  file 0x1A710   D280FA01  mov x1,#0x7d0   (2000 ms)
              -> D29D4C01  mov x1,#0xEA60  (60000 ms)
  (movz x1,#imm16 -- 0xEA60<<5 = 0x1D4C00, | 0xD2800001 | 1)

Everything else is 0.0.2, asserted through build_002().

Usage:  python3 make_log_images_004.py [baseline] [outdir]
"""
import hashlib, os, struct, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from make_log_images_002 import build_002

WAIT2 = 0x1A710
OLD, NEW = 0xD280FA01, 0xD29D4C01


def build_004(base):
    d = bytearray(build_002(base))

    assert struct.unpack_from('<I', d, WAIT2)[0] == OLD, 'wait #2 mov x1,#0x7d0'
    struct.pack_into('<I', d, WAIT2, NEW)

    assert struct.unpack_from('<I', d, 0x30)[0] == 0x75EF0
    assert len(d) == 484260
    return bytes(d)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    base = sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, '..', 'images', 'uboot.img')
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(here, '..', 'images')
    data = build_004(base)
    p = os.path.join(out, 'uboot-0.0.4-longwin.img')
    open(p, 'wb').write(data)
    print('uboot-0.0.4-longwin.img  %d bytes  md5 %s' % (len(data), hashlib.md5(data).hexdigest()))
    return 0


if __name__ == '__main__':
    sys.exit(main())
