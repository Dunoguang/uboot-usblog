"""Automatic anchor location for the usblog patch (byte-level evidence only).

Chain (every step is cross-checked by atomic asserts):
  TRIG        <- "USB SERIAL PORT OPENED" string -> adrp+add refs -> nearby bl,
                 filtered by [TRIG-4]==mov w19,#1 and [TRIG-0x48]==cbnz w0,+0x38
  RET1/FORCE_PORT/PUMP_CALL/REPLY  <- rigid deltas from TRIG
  PRINTF      <- bl(TRIG);   STOCK_PUMP <- bl(PUMP_CALL)
  FLAG_TX     <- lower of the two refs inside STOCK_PUMP (they are 0x80 apart)
  PUTS/GETTIMER/IRQ  <- masked word templates (dw99 reference blobs)
  dead zone   <- masked template + zero-external-ref scan + canonical tail
"""
import json
import os
import struct

from . import aarch64 as A

BASE = 0x9EFFFE00
DEAD_SIZE = 0x2A4
DEAD_LAYOUT = [
    ('FLAG', 0), ('S1', 4), ('LOG', 0x18), ('HOOK', 0x68), ('PUMP', 0x8C),
    ('GATE_WAIT', 0xCC), ('S2', 0x11C), ('LOG2', 0x14C), ('DEAD_END', DEAD_SIZE),
]
RIGID = [('RET1', -4), ('FORCE_PORT', -0x48), ('PUMP_CALL', 0x188), ('REPLY', 0x154)]


class FindError(Exception):
    pass


def load_templates(path=None):
    here = os.path.dirname(os.path.abspath(__file__))
    if path is None:
        path = os.path.join(here, os.pardir, 'profiles', 'templates_dw99.json')
    with open(path) as f:
        return json.load(f)


def template_candidates(d, blk, topn=8):
    """masked word-template search; returns candidate dicts sorted by support"""
    off0 = int(blk['off'], 16)
    pat = [int(x, 16) for x in blk['hex'].split()]
    n = len(pat)
    runs, i = [], 0
    while i < n:
        if not A.is_wild(pat[i]):
            j = i
            while j < n and not A.is_wild(pat[j]):
                j += 1
            if j - i >= 3:
                runs.append((i, j))
            i = j
        else:
            i += 1
    votes = {}
    for (i0, j0) in runs:
        seg = struct.pack('<%dI' % (j0 - i0), *pat[i0:j0])
        start = 0
        while True:
            k = d.find(seg, start)
            if k < 0:
                break
            if k % 4 == 0:
                delta = k - off0 - 4 * i0
                votes[delta] = votes.get(delta, 0) + (j0 - i0)
            start = k + 4
    out = []
    for delta, sup in sorted(votes.items(), key=lambda kv: -kv[1])[:topn]:
        s0 = off0 + delta
        if s0 < 0 or s0 + 4 * n > len(d):
            continue
        sc = tot = 0
        for k2 in range(n):
            if A.is_wild(pat[k2]):
                continue
            tot += 1
            if A.w(d, s0 + 4 * k2) == pat[k2]:
                sc += 1
        out.append(dict(off=s0, delta=delta, support=sup, score=sc, tot=tot,
                        frac=(float(sc) / tot if tot else 0.0)))
    return out


def scan_refs(d):
    """one pass over the image: all branch (src,target) pairs and qword VA values"""
    brs = []
    for o in range(0x200, len(d) - 4, 4):
        t = A.branch_target(d, o, BASE)
        if t is not None:
            brs.append((o, t))
    ptrs = set()
    for o in range(0x200, len(d) - 8):
        v = struct.unpack_from('<Q', d, o)[0]
        if 0x9E000000 <= v < 0xA0000000:
            ptrs.add(v)
    return brs, ptrs


def dead_zone_clean(brs, ptrs, lo_off, size=DEAD_SIZE + 4):
    lo, hi = lo_off + BASE, lo_off + BASE + size
    for o, t in brs:
        if lo <= t < hi and not (lo <= o + BASE < hi):
            return False, 'branch %#x -> %#x' % (o, t)
    for v in ptrs:
        if lo <= v < hi:
            return False, 'ptr %#x' % v
    return True, ''


def locate(d, log=print):
    """locate every anchor; returns dict name -> file offset (raises FindError)"""
    t = load_templates()
    res = {}

    # ---- 1. TRIG via the console string ---------------------------------
    sstr = t['string_anchor'].encode('ascii')
    s = d.find(sstr)
    if s < 0:
        raise FindError('anchor string %r not found' % t['string_anchor'])
    sva = s + BASE
    sites = [o for o in range(0x200, len(d) - 8, 4)
             if A.dec_pair(d, o, BASE) == sva]
    cands = []
    for o in sites:
        for k in range(4, 0x40, 4):
            if (A.w(d, o + k) >> 26) == 0x25:
                cands.append(o + k)
                break
    good = []
    for c in cands:
        ok1 = c >= 4 and A.w(d, c - 4) == 0x52800033
        ok2 = c >= 0x48 and A.w(d, c - 0x48) == 0x350001C0
        good.append((c, ok1, ok2))
    sel = [c for (c, k1, k2) in good if k1 and k2]
    if len(sel) != 1:
        raise FindError('TRIG not unique (%d/%d pass): %s' % (
            len(sel), len(good),
            ', '.join('%#x%s%s' % (c, '[1]' if k1 else '', '[2]' if k2 else '')
                      for c, k1, k2 in good)))
    TRIG = sel[0]
    res['TRIG'] = TRIG
    log('  TRIG        %#08x   (string @ %#x, %d ref sites, %d bl cands)' %
        (TRIG, s, len(sites), len(cands)))

    # ---- 2. rigid deltas -------------------------------------------------
    for name, dv in RIGID:
        res[name] = TRIG + dv
    if A.w(d, res['RET1']) != 0x52800033:
        raise FindError('RET1 at %#x is not mov w19,#1' % res['RET1'])
    if A.w(d, res['FORCE_PORT']) != 0x350001C0:
        raise FindError('FORCE_PORT at %#x is not cbnz w0,+0x38' % res['FORCE_PORT'])
    if (A.w(d, res['PUMP_CALL']) >> 26) != 0x25:
        raise FindError('PUMP_CALL at %#x is not a bl' % res['PUMP_CALL'])
    log('  rigid       RET1 %#08x  FORCE_PORT %#08x  PUMP_CALL %#08x  REPLY %#08x' %
        (res['RET1'], res['FORCE_PORT'], res['PUMP_CALL'], res['REPLY']))

    # ---- 3. bl-target chain ----------------------------------------------
    res['PRINTF'] = A.bl_target(d, TRIG, BASE)
    res['STOCK_PUMP'] = A.bl_target(d, res['PUMP_CALL'], BASE)
    if res['PRINTF'] is None or res['STOCK_PUMP'] is None:
        raise FindError('bl decode failed at TRIG/PUMP_CALL')
    log('  bl chain    PRINTF %#x  STOCK_PUMP %#x' %
        (res['PRINTF'], res['STOCK_PUMP']))

    # ---- 4. FLAG_TX / PORTFLAG -------------------------------------------
    sp_file = res['STOCK_PUMP'] - BASE
    refs = []
    for k in range(0, 0x80, 4):
        v = A.dec_pair(d, sp_file + k, BASE)
        if v is not None:
            refs.append(v)
    refs = sorted(set(refs))
    pairs = [(refs[i], refs[i + 1]) for i in range(len(refs) - 1)
             if refs[i + 1] - refs[i] == 0x80]
    if len(pairs) != 1:
        raise FindError('pump ref pattern not unique (%d pairs) refs=%s' %
                        (len(pairs), ['%#x' % r for r in refs]))
    res['FLAG_TX'] = pairs[0][0]
    res['PORTFLAG'] = pairs[0][0] + 0x78
    log('  data        FLAG_TX %#x  PORTFLAG %#x' %
        (res['FLAG_TX'], res['PORTFLAG']))

    # ---- 5. code templates ------------------------------------------------
    def pick(name, frac_min, extra=None):
        cs = template_candidates(d, t['blocks'][name])
        for c in cs:
            if c['frac'] >= frac_min and (extra is None or extra(c['off'])):
                return c
        raise FindError('%s: no candidate passed (frac>=%.2f): %s' % (
            name, frac_min,
            ', '.join('%#x %.0f%%' % (c['off'], 100 * c['frac']) for c in cs[:6])))

    puts = pick('puts', 0.85, lambda o: A.w(d, o) == 0xA9BF7BFD)
    res['PUTS'] = puts['off']
    res['PUTS_BODY'] = puts['off'] + 4
    log('  puts        %#08x  (frac %.0f%%, delta %+#x)' %
        (puts['off'], 100 * puts['frac'], puts['delta']))

    gt = pick('gettimer', 0.9)
    res['GETTIMER'] = gt['off'] + BASE
    log('  gettimer    %#08x  (frac %.0f%%, delta %+#x)' %
        (gt['off'], 100 * gt['frac'], gt['delta']))

    irq = pick('irq', 0.8)
    res['IRQ'] = irq['off'] + BASE
    log('  irq         %#08x  (frac %.0f%%, delta %+#x)' %
        (irq['off'], 100 * irq['frac'], irq['delta']))

    rpcs = template_candidates(d, t['blocks']['reply'])
    rp_rel = t['blocks']['reply'].get('anchor_rel', 0x14)
    rp_hit = [c for c in rpcs if c['off'] + rp_rel == res['REPLY']]
    if not rp_hit:
        raise FindError('reply template does not confirm REPLY=%#x; top: %s' %
                        (res['REPLY'],
                         ', '.join('%#x %.0f%%' % (c['off'], 100 * c['frac'])
                                   for c in rpcs[:6])))
    log('  reply       %#08x  confirmed (frac %.0f%%)' %
        (res['REPLY'], 100 * rp_hit[0]['frac']))

    # ---- 6. dead zone ------------------------------------------------------
    brs, ptrs = scan_refs(d)
    dead = None
    tried = []
    for c in template_candidates(d, t['blocks']['dead'], topn=12):
        o = c['off']
        if o + DEAD_SIZE + 4 > len(d):
            continue
        ret_ok = A.w(d, o + DEAD_SIZE) == 0xD65F03C0
        clean, why = dead_zone_clean(brs, ptrs, o)
        tried.append('%#x[%s%s]' % (o, '.' if clean else 'X', '.' if ret_ok else 'x'))
        if clean and ret_ok:
            dead = c
            break
    if dead is None:
        raise FindError('dead zone not found; tried: %s' % ' '.join(tried[:8]))
    FLAG = dead['off']
    for name, dv in DEAD_LAYOUT:
        res[name] = FLAG + dv
    res['HOOK_BL'] = res['HOOK'] + 0x0C
    log('  dead zone   %#08x..%#08x  (zero ext refs, tail ret ok, delta %+#x)' %
        (FLAG, res['DEAD_END'], dead['delta']))
    return res
