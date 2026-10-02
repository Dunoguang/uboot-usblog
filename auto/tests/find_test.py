#!/usr/bin/env python3
"""Run the automatic finder against all four known baselines; compare to known tables."""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib import finder

IMGS = {
 'vp19':  ('/root/github/uboot-usblog/images/uboot.img', dict(
    PUTS=0xE798, PUTS_BODY=0xE79C, TRIG=0x1A768, FORCE_PORT=0x1A720, RET1=0x1A764,
    PUMP_CALL=0x1A8F0, REPLY=0x1A8BC, PRINTF=0x9F00E5D8, GETTIMER=0x9F00184C,
    IRQ=0x9F030CA0, STOCK_PUMP=0x9F02D0F0, FLAG_TX=0x9F1CC118, PORTFLAG=0x9F1CC190,
    FLAG=0x1BC34, S1=0x1BC38, LOG=0x1BC4C, HOOK=0x1BC9C, PUMP=0x1BCC0,
    GATE_WAIT=0x1BD00, S2=0x1BD50, LOG2=0x1BD80, DEAD_END=0x1BED8, HOOK_BL=0x1BCA8)),
 'ai3':   ('/root/usblog-patch-transplant-ai3/uboot.bin', dict(
    PUTS=0xE790, PUTS_BODY=0xE794, TRIG=0x1A770, FORCE_PORT=0x1A728, RET1=0x1A76C,
    PUMP_CALL=0x1A8F8, REPLY=0x1A8C4, PRINTF=0x9F00E5D0, GETTIMER=0x9F00184C,
    IRQ=0x9F030CF4, STOCK_PUMP=0x9F02D144, FLAG_TX=0x9F1CC098, PORTFLAG=0x9F1CC110,
    FLAG=0x1BC58, S1=0x1BC5C, LOG=0x1BC70, HOOK=0x1BCC0, PUMP=0x1BCE4,
    GATE_WAIT=0x1BD24, S2=0x1BD74, LOG2=0x1BDA4, DEAD_END=0x1BEFC, HOOK_BL=0x1BCCC)),
 'dw99':  ('/root/usblog-patch-transplant-dw99/uboot-unlock-bootloader.img', dict(
    PUTS=0xE770, PUTS_BODY=0xE774, TRIG=0x1A740, FORCE_PORT=0x1A6F8, RET1=0x1A73C,
    PUMP_CALL=0x1A8C8, REPLY=0x1A894, PRINTF=0x9F00E5B0, GETTIMER=0x9F00184C,
    IRQ=0x9F030C94, STOCK_PUMP=0x9F02D0E4, FLAG_TX=0x9F1CC098, PORTFLAG=0x9F1CC110,
    FLAG=0x1BC28, S1=0x1BC2C, LOG=0x1BC40, HOOK=0x1BC90, PUMP=0x1BCB4,
    GATE_WAIT=0x1BCF4, S2=0x1BD44, LOG2=0x1BD74, DEAD_END=0x1BECC, HOOK_BL=0x1BC9C)),
 'dw100': ('/root/usblog-patch-transplant-dw100/uboot-unlock-bootloader.img', dict(
    PUTS=0xE770, PUTS_BODY=0xE774, TRIG=0x1A740, FORCE_PORT=0x1A6F8, RET1=0x1A73C,
    PUMP_CALL=0x1A8C8, REPLY=0x1A894, PRINTF=0x9F00E5B0, GETTIMER=0x9F00184C,
    IRQ=0x9F030C94, STOCK_PUMP=0x9F02D0E4, FLAG_TX=0x9F1CC098, PORTFLAG=0x9F1CC110,
    FLAG=0x1BC28, S1=0x1BC2C, LOG=0x1BC40, HOOK=0x1BC90, PUMP=0x1BCB4,
    GATE_WAIT=0x1BCF4, S2=0x1BD44, LOG2=0x1BD74, DEAD_END=0x1BECC, HOOK_BL=0x1BC9C)),
}

fails = 0
for n, (p, known) in IMGS.items():
    d = open(p, 'rb').read()
    print('==== %s (%d bytes)' % (n, len(d)))
    t0 = time.time()
    try:
        res = finder.locate(d)
    except finder.FindError as e:
        print('  FIND ERROR: %s' % e)
        fails += 1
        continue
    dt = time.time() - t0
    bad = [k for k in known if res.get(k) != known[k]]
    missing = [k for k in known if k not in res]
    print('  -> %d anchors in %.1fs;  mismatches: %s; missing: %s'
          % (len(res), dt, bad or 'NONE', missing or 'NONE'))
    for k in bad:
        print('     %s: got %#x want %#x' % (k, res.get(k, -1), known[k]))
    if bad or missing:
        fails += 1
print('FAILS: %d / %d' % (fails, len(IMGS)))
