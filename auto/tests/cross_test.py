#!/usr/bin/env python3
"""Cross-validation on images the system has never been ported to:
  1) a third-party-modified (YC-unlocked) ai3 image must reproduce the
     hand-made 3-in-1 product byte-exactly;
  2) a lock-carrying variant runs through the full pipe (self-consistency).
"""
import hashlib, os, struct, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib import finder, builder, verify, audit

quiet = lambda *x: None
fails = 0

def d30_of(d):
    return struct.unpack_from('<I', d, 0x30)[0]

base_p = '/root/github/8541e_unlock_work/ai3-watch/patched/uboot-unlock-bootloader.img'
gold_p = '/root/github/8541e_unlock_work/ai3-watch/patched/uboot-unlock-bootloader-usblog.img'
if os.path.exists(base_p) and os.path.exists(gold_p):
    base = open(base_p, 'rb').read()
    a = finder.locate(base, log=quiet)
    prod = builder.build(base, a, d30_of(base), log=quiet)
    md5 = hashlib.md5(prod).hexdigest()
    gold = hashlib.md5(open(gold_p, 'rb').read()).hexdigest()
    ok = md5 == gold
    print('1) YC-unlocked ai3 -> %s %s' % (md5, 'OK (== hand-made 3-in-1)' if ok else 'MISMATCH'))
    fails += 0 if ok else 1
    fails += 1 if verify.verify(prod, a, d30_of(base), log=quiet) else 0
    fails += 1 if audit.audit(base, prod, a, d30_of(base), log=quiet) else 0
else:
    print('1) skipped (files not present)')

lock_p = '/root/github/8541e_unlock_work/uboot_has_bootloader_lock.bin'
if os.path.exists(lock_p):
    base = open(lock_p, 'rb').read()
    a = finder.locate(base, log=quiet)
    print('2) hyx lock variant: %d anchors located' % len(a))
    prod = builder.build(base, a, d30_of(base), log=quiet)
    vb = verify.verify(prod, a, d30_of(base), log=quiet)
    ab = audit.audit(base, prod, a, d30_of(base), log=quiet)
    ok = (vb == 0 and ab == 0)
    print('   build+verify+audit: %s (md5 %s)' % ('OK' if ok else 'FAILED', hashlib.md5(prod).hexdigest()))
    fails += 0 if ok else 1
else:
    print('2) skipped (file not present)')

print('CROSS FAILS: %d' % fails)
sys.exit(1 if fails else 0)
