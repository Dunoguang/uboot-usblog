#!/usr/bin/env python3
"""Part C: masked word-template search (dw99 ref vs others). Part D: FLAG_TX via pump refs."""
import struct
from probe_bytes import IMGS, T, w, dec_pair, BASE

def is_wild(v):
    if (v & 0xFC000000) in (0x14000000, 0x94000000): return True   # b / bl
    if (v & 0x7E000000) in (0x34000000, 0x35000000): return True   # cbz / cbnz
    if (v & 0xFF000010) == 0x54000000: return True                 # b.cond
    if (v & 0x7E000000) in (0x36000000, 0x37000000): return True   # tbz / tbnz
    if (v & 0x9F000000) in (0x10000000, 0x90000000): return True   # adr / adrp
    if (v & 0x3B000000) == 0x18000000: return True                 # ldr literal
    return False

def find_block(dst, ref, off, nwords, name, topn=4):
    pat = [w(ref, off + 4 * k) for k in range(nwords)]
    runs, i = [], 0
    while i < nwords:
        if not is_wild(pat[i]):
            j = i
            while j < nwords and not is_wild(pat[j]): j += 1
            if j - i >= 3: runs.append((i, j))
            i = j
        else:
            i += 1
    votes = {}
    for (i, j) in runs:
        seg = struct.pack('<%dI' % (j - i), *pat[i:j])
        start = 0
        while True:
            k = dst.find(seg, start)
            if k < 0: break
            if k % 4 == 0:
                delta = k - off - 4 * i
                votes[delta] = votes.get(delta, 0) + (j - i)
            start = k + 4
    cand = sorted(votes.items(), key=lambda x: -x[1])[:topn]
    print('== %s  ref %#x  %d words  seeds=%s' % (name, off, nwords,
          ['%d-%d' % r for r in runs]))
    for delta, sup in cand:
        s0 = off + delta
        if s0 < 0 or s0 + 4 * nwords > len(dst): continue
        sc = tot = 0; mism = []
        for k2 in range(nwords):
            if is_wild(pat[k2]): continue
            tot += 1
            if w(dst, s0 + 4 * k2) == pat[k2]: sc += 1
            else: mism.append(k2)
        print('   delta %+#08x -> %#08x  support %2d  score %d/%d  mism %s' % (
            delta, s0, sup, sc, tot, ','.join(str(m) for m in mism[:10])))

def partC():
    ref = open(IMGS['dw99'], 'rb').read()
    for n in ('dw100', 'ai3', 'vp19'):
        dst = open(IMGS[n], 'rb').read()
        print('---- ref dw99 -> dst %s ----' % n)
        find_block(dst, ref, 0xE770, 32, 'puts')
        find_block(dst, ref, 0x1A674, 64, 'calibrate')
        find_block(dst, ref, 0x2D2E4, 16, 'pump')
        find_block(dst, ref, 0x1A4C, 16, 'gettimer')
        find_block(dst, ref, 0x30E94, 32, 'irq')
        find_block(dst, ref, 0x1BC28, 40, 'dead-part')

def partD():
    print('== D. FLAG_TX via adrp+add refs inside the stock pump ==')
    PUMPS = {'vp19': 0x2D2F0, 'ai3': 0x2D344, 'dw99': 0x2D2E4, 'dw100': 0x2D2E4}
    for n, p in IMGS.items():
        d = open(p, 'rb').read()
        hits = []
        off = PUMPS[n]
        for k in range(0, 0x60, 4):
            t = dec_pair(d, off + k)
            if t is not None: hits.append('%x->%x' % (off + k, t))
        print('  %-6s pump@%#x  refs %s' % (n, off, hits))

if __name__ == '__main__':
    partC()
    partD()
