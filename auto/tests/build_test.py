#!/usr/bin/env python3
"""Full pipeline: finder -> builder on all four baselines; compare md5 to the
released/historical products."""
import hashlib, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib import finder, builder

IMGS = {
 'vp19':  ('/root/github/uboot-usblog/images/uboot.img', 0x75EF0,
           'aa780e3b519d59aa65979def9b83eb44'),
 'ai3':   ('/root/usblog-patch-transplant-ai3/uboot.bin', 0x75E90,
           '41edd1b4020fa166f6cf775e49b76b50'),
 'dw99':  ('/root/usblog-patch-transplant-dw99/uboot-unlock-bootloader.img', 0x76180,
           '3eefc377c52a5af9ed6188602378e3a7'),
 'dw100': ('/root/usblog-patch-transplant-dw100/uboot-unlock-bootloader.img', 0x75A90,
           'e5badc44d778ce613e91a09628f0c0c3'),
}

fails = 0
for n, (p, d30, gold) in IMGS.items():
    d = open(p, 'rb').read()
    print('==== %s' % n)
    a = finder.locate(d)
    out = builder.build(d, a, d30)
    md5 = hashlib.md5(out).hexdigest()
    ok = (md5 == gold)
    print('  %-6s -> md5 %s  %s' % (n, md5, 'OK' if ok else 'MISMATCH (want %s)' % gold))
    if not ok:
        fails += 1
print('FAILS: %d / %d' % (fails, len(IMGS)))
