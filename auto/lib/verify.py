"""Verify a built usblog image before flashing (anchor-table parameterized).

Every injected block is disassembled with a built-in AArch64 branch
decoder and each branch target is resolved back to a file offset.

The two classic silent mistakes this checks:
  * str/ldr unsigned-immediate forms take BYTES/8 in imm12.
  * the gate address is va(FLAG) & 0xFFF, the VA's low 12 bits
    (using the file offset's low bits lands 0x200 past the gate byte).
"""
import hashlib
import struct

from . import aarch64 as A
from .builder import CHUNK, BUDGET, WAIT_MS

BASE = 0x9EFFFE00

BLOCK_SPEC = [('bounded pump', 'PUMP', 15), ('usb_gate_wait', 'GATE_WAIT', 20),
              ('trigger stub S2', 'S2', 11), ('log fn LOG2', 'LOG2', 39),
              ('puts hook', 'HOOK', 7)]
GATE_ADDS = [('S1', 4), ('LOG', 0x10), ('LOG', 0x3C), ('S2', 0x1C), ('LOG2', 0x18)]


def va(f):
    return BASE + f


def v2f(v):
    return (v - 0x9F000000) + 0x200


def verify(d, a, dhtb30, expected_len=None, expected_md5=None, log=print):
    bad = 0

    def check(ok, msg):
        nonlocal bad
        log('  %s %s' % ('ok  ' if ok else 'FAIL', msg))
        if not ok:
            bad += 1

    md5 = hashlib.md5(d).hexdigest()
    log('%d bytes   md5 %s' % (len(d), md5))
    check(struct.unpack_from('<I', d, 0x30)[0] == dhtb30, '[0x30] == %#x' % dhtb30)
    if expected_len is not None:
        check(len(d) == expected_len, 'length == %d' % expected_len)
    if expected_md5 is not None:
        check(md5 == expected_md5, 'md5 == %s' % expected_md5)

    check(A.w(d, a['PUTS']) == A.b_(va(a['PUTS']), va(a['HOOK'])),
          'puts entry -> hook')
    check(A.w(d, a['TRIG']) == A.bl_(va(a['TRIG']), va(a['S2'])), 'TRIG -> S2')
    check(A.w(d, a['PUMP_CALL']) == A.bl_(va(a['PUMP_CALL']), va(a['PUMP'])),
          'PUMP_CALL -> bounded pump')
    check(A.w(d, a['RET1']) == 0x52800013, 'RET1 = mov w19,#0')
    check(A.w(d, a['FORCE_PORT']) == 0x1400000E, 'FORCE_PORT = b +0x38')
    check(A.w(d, a['HOOK_BL']) == A.bl_(va(a['HOOK_BL']), va(a['LOG2'])),
          'HOOK_BL -> LOG2')

    want = va(a['FLAG']) & 0xFFF
    log('  gate address in every adrp/add pair must be %#x (VA low 12 bits)' % want)
    for bname, rel in GATE_ADDS:
        off = a[bname] + rel
        w = A.w(d, off)
        imm = (w >> 10) & 0xFFF
        check((w & 0xFFC00000) == 0x91000000 and imm == want,
              '%s+%#x add ...#%#x' % (bname, rel, imm))
    check(d[a['FLAG']] == 0, 'gate byte is zero')

    log('  disassembly walk')
    for name, key, n in BLOCK_SPEC:
        off = a[key]
        end = off + n * 4
        if end > a['DEAD_END']:
            check(False, '%s overruns the dead function' % name)
            continue
        log('  --- %s @ file %#08x ---' % (name, off))
        k = 0
        while k < n:
            ioff = off + k * 4
            w = A.w(d, ioff)
            t = A.branch_target(d, ioff, BASE)
            if t is None and k + 1 < n:
                pt = A.dec_pair(d, ioff, BASE)
                if pt is not None:
                    check(pt in (va(a['FLAG']), a['FLAG_TX'], a['PORTFLAG']),
                          '%#08x adrp/add -> %#x' % (va(ioff), pt))
                    k += 2
                    continue
            if t is not None:
                inside = (va(a['FLAG']) <= t < va(a['DEAD_END']) + 4) or \
                    t in (a['PRINTF'], a['GETTIMER'], a['IRQ'], va(a['PUTS_BODY'])) or \
                    v2f(t) == a['REPLY']
                check(inside, '%#08x branch -> file %#x' % (va(ioff), v2f(t)))
            k += 1

    log('FAILURES: %d' % bad if bad else 'OK - image looks correct')
    return bad
