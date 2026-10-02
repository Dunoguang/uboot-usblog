#!/usr/bin/env python3
"""Extract reference anchor blobs (from the dw99 baseline) into profiles/templates_dw99.json."""
import hashlib, json, struct, sys

BASE = 0x9EFFFE00
REF = sys.argv[1] if len(sys.argv) > 1 else \
    '/root/usblog-patch-transplant-dw99/uboot-unlock-bootloader.img'

BLOCKS = {
    'puts':     (0xE770, 32, 0),
    'gettimer': (0x1A4C, 16, 0),
    'irq':      (0x30E94, 32, 0),
    'reply':    (0x1A880, 32, 0x14),
    'dead':     (0x1BC28, 169, 0),
}

d = open(REF, 'rb').read()
out = {
    'ref_name': 'dw99',
    'ref_md5': hashlib.md5(d).hexdigest(),
    'base': hex(BASE),
    'string_anchor': 'USB SERIAL PORT OPENED',
    'blocks': {},
}
for name, (off, n, arel) in BLOCKS.items():
    words = [struct.unpack_from('<I', d, off + 4 * k)[0] for k in range(n)]
    out['blocks'][name] = {
        'off': hex(off), 'n': n, 'anchor_rel': arel,
        'hex': ' '.join('%08x' % x for x in words),
    }
json.dump(out, open('profiles/templates_dw99.json', 'w'), indent=1)
print('wrote profiles/templates_dw99.json  ref md5 %s' % out['ref_md5'])
