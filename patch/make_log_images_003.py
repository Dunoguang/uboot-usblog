#!/usr/bin/env python3
"""Build uboot 0.0.3 - 0.0.2 plus a long enough port-open wait.

0.0.2 removed every wait but that made the USB log uncapturable: measured with
probe3, the host driver's first CreateFile on COM11 starts 16 ms after the port
appears and takes 3.9 s to fail with ERROR_GEN_FAILURE, while u-boot stops
servicing the endpoint after wait #2's 2000 ms timeout.  So the driver starves.

0.0.3 therefore extends wait #2 only:

  F5  file 0x1A710  mov x1,#0x7d0 (2000 ms)  ->  mov x1,#0x1770 (6000 ms)

Why this is not a "wait": wait #2 polls the port-open flag
[0x9F1CC190], which gser_setup sets on SET_CONTROL_LINE_STATE with wValue == 1
- exactly what read-com-*.ps1 sends the moment its Open succeeds.  So with a
reader attached the wait ends the instant the driver finishes opening the port;
the longer timeout only buys the driver the endpoint servicing it needs.  The
extra 4 s are paid only when no host reader is present at all (and the 0x1A720
force patch still allocates the channel in that case).

Usage:  python3 make_log_images_003.py [baseline] [outdir]
"""
import hashlib, os, struct, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from make_log_images_002 import build_002, MD5_001

MD5_002 = 'fb0186e881ebedf4bcc7cbb26fa32683'

PORTOPEN_TIMEOUT = 0x1A710       # `mov x1,#0x7d0` in sub_9F01A49C (wait #2)
OLD_W, NEW_W = 0xD280FA01, 0xD282EE01     # 2000 ms -> 6000 ms (movz x1,#0x1770)


def build_003(base):
    d = bytearray(build_002(base))
    assert hashlib.md5(d).hexdigest() == MD5_002, 'not the 0.0.2 image'
    assert struct.unpack_from('<I', d, PORTOPEN_TIMEOUT)[0] == OLD_W, 'wait #2 constant'
    struct.pack_into('<I', d, PORTOPEN_TIMEOUT, NEW_W)
    assert struct.unpack_from('<I', d, 0x30)[0] == 0x75EF0
    assert len(d) == 484260
    return bytes(d)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    base = sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, '..', 'images', 'uboot.img')
    out  = sys.argv[2] if len(sys.argv) > 2 else os.path.join(here, '..', 'images')
    data = build_003(base)
    md5  = hashlib.md5(data).hexdigest()
    p = os.path.join(out, 'uboot-0.0.3.img')
    open(p, 'wb').write(data)
    print('uboot-0.0.3.img  %d bytes  md5 %s' % (len(data), md5))
    print('asserted: baseline %s, 0.0.1 %s, 0.0.2 %s' % ('a03efc26...', MD5_001, MD5_002))
    return 0


if __name__ == '__main__':
    sys.exit(main())
