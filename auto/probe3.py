#!/usr/bin/env python3
"""Part E: TRIG candidate disambiguation. Part F: bl-target chains + reply template."""
import struct
from probe_bytes import IMGS, T, w, dec_pair, BASE
from probe2 import find_block

def bl_target(d, off):
    v = w(d, off)
    if (v >> 26) != 0x25: return None
    imm = v & 0x3FFFFFF
    if imm & (1 << 25): imm -= (1 << 26)
    return off + BASE + imm * 4

def partE():
    print('== E. TRIG candidates from the string ref, filtered ==')
    for n, p in IMGS.items():
        d = open(p, 'rb').read()
        s = d.find(b'USB SERIAL PORT OPENED'); sva = s + BASE
        sites = [off for off in range(0x200, len(d) - 8, 4) if dec_pair(d, off) == sva]
        for off in sites:
            for k in range(8, 0x40, 4):
                if (w(d, off + k) >> 26) == 0x25:
                    t = off + k
                    wm4 = w(d, t - 4); wm48 = w(d, t - 0x48)
                    print('  %-6s cand %#x  -4=%08x %s  -0x48=%08x  isTRIG=%s' % (
                        n, t, wm4, 'mov w19,#1 OK' if wm4 == 0x52800033 else '          ',
                        wm48, t == T[n]['TRIG']))
                    break

def partF():
    print('== F. bl-target chains ==')
    PUMPS = {'vp19': 0x2D2F0, 'ai3': 0x2D344, 'dw99': 0x2D2E4, 'dw100': 0x2D2E4}
    for n, p in IMGS.items():
        d = open(p, 'rb').read()
        t = bl_target(d, T[n]['PUMP_CALL'])
        t2 = bl_target(d, T[n]['TRIG'])
        print('  %-6s PUMP_CALL bl-> %#x (stock pump %#x)   TRIG bl-> %#x' % (
            n, t, PUMPS[n] + BASE, t2))
    print('== G. replyfn template (ref dw99 0x1A880/32w) ==')
    ref = open(IMGS['dw99'], 'rb').read()
    for n in ('dw100', 'ai3', 'vp19'):
        dst = open(IMGS[n], 'rb').read()
        find_block(dst, ref, 0x1A880, 32, 'replyfn')

if __name__ == '__main__':
    partE()
    partF()
