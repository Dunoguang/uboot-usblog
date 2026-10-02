#!/usr/bin/env python3
"""Full regression: for every known profile run finder -> builder ->
verify -> audit and compare against the historical products."""
import hashlib, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib import finder, builder, verify, audit

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
quiet = lambda *x: None

fails = 0
for name in ('vp19', 'ai3', 'dw99', 'dw100'):
    prof = json.load(open(os.path.join(HERE, 'profiles', name + '.json')))
    img = prof['image']
    if not os.path.exists(img):
        cand = os.path.join(HERE, os.pardir, 'images', os.path.basename(img))
        if os.path.exists(cand):
            img = cand
    if not os.path.exists(img):
        print('==== %s  SKIPPED (baseline not present: %s)' % (name, prof['image']))
        continue
    base = open(img, 'rb').read()
    if hashlib.md5(base).hexdigest() != prof['image_md5']:
        print('!! %s baseline md5 mismatch' % name)
        fails += 1
        continue
    print('==== %s  %d bytes' % (name, len(base)))
    a = finder.locate(base, log=quiet)
    bad = [k for k, v in prof['anchors'].items() if a.get(k) != int(v, 16)]
    if bad:
        print('  anchors MISMATCH: %s' % bad)
        fails += 1
        continue
    print('  anchors: %d/%d OK' % (len(prof['anchors']), len(prof['anchors'])))
    d30 = int(prof['dhtb30'], 16)
    prod = builder.build(base, a, d30, log=quiet)
    md5 = hashlib.md5(prod).hexdigest()
    ok = md5 == prof['product_md5']
    print('  product md5 %s %s' % (md5, 'OK' if ok else 'MISMATCH'))
    fails += 0 if ok else 1
    vb = verify.verify(prod, a, d30, expected_len=prof['image_len'],
                       expected_md5=prof['product_md5'], log=quiet)
    print('  verify: %s' % ('OK' if vb == 0 else '%d FAILURES' % vb))
    fails += 1 if vb else 0
    ab = audit.audit(base, prod, a, d30, expected_md5=prof['product_md5'],
                     content_end=int(prof['content_end'], 16), log=quiet)
    print('  audit: %s' % ('OK' if ab == 0 else '%d FAILURES' % ab))
    fails += 1 if ab else 0

print('REGRESSION FAILS: %d' % fails)
sys.exit(1 if fails else 0)
