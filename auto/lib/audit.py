"""Independent diff-containment audit of a built image (parameterized).

The allowed change zones are derived from the anchor table itself:
  5 single-word rewrites + the injected region [FLAG, LOG2+0x9C).
Everything else must be byte-identical to the baseline.
"""
import hashlib
import struct

LOG2_LEN_WORDS = 39


def audit(base, prod, a, dhtb30, expected_md5=None, content_end=None, log=print):
    bad = 0

    allow = [
        (a['PUTS'], a['PUTS'] + 4),
        (a['FORCE_PORT'], a['FORCE_PORT'] + 4),
        (a['RET1'], a['RET1'] + 4),
        (a['TRIG'], a['TRIG'] + 4),
        (a['PUMP_CALL'], a['PUMP_CALL'] + 4),
        (a['FLAG'], a['LOG2'] + LOG2_LEN_WORDS * 4),
    ]

    def in_allow(off):
        return any(x <= off < y for x, y in allow)

    if len(base) != len(prod):
        log('FAIL length differs: %d vs %d' % (len(base), len(prod)))
        return 1

    changed = [i for i in range(len(base)) if base[i] != prod[i]]
    outside = [o for o in changed if not in_allow(o)]
    log('changed bytes total: %d' % len(changed))
    log('changed bytes outside allowed zones: %d' % len(outside))
    for o in outside[:20]:
        log('   %#x' % o)
    bad += 1 if outside else 0
    for x, y in allow:
        n = sum(1 for o in changed if x <= o < y)
        log('   zone %#x..%#x changed: %d' % (x, y, n))

    md5 = hashlib.md5(prod).hexdigest()
    checks = [
        ('[0x30]', struct.unpack_from('<I', prod, 0x30)[0] == dhtb30),
        ('md5', (md5 == expected_md5) if expected_md5 else True),
        ('gate byte 0', prod[a['FLAG']:a['FLAG'] + 4] == b'\x00\x00\x00\x00'),
        ('header same', base[0x200:a['PUTS']] == prod[0x200:a['PUTS']]),
        ('ByYC kept', prod.count(b'ByYC') == base.count(b'ByYC')),
    ]
    if content_end is not None:
        checks.append(('tail zeros', all(x == 0 for x in prod[content_end:])))
    for name, ok in checks:
        log('%-14s %s' % (name, 'ok' if ok else 'FAIL'))
        bad += 0 if ok else 1
    log('AUDIT FAILURES: %d' % bad if bad else 'AUDIT OK')
    return bad
