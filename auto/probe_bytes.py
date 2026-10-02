#!/usr/bin/env python3
"""Byte-level anchor-location probe across the four known DW images."""
import struct

BASE = 0x9EFFFE00

IMGS = {
 'vp19':  '/root/github/uboot-usblog/images/uboot.img',
 'ai3':   '/root/usblog-patch-transplant-ai3/uboot.bin',
 'dw99':  '/root/usblog-patch-transplant-dw99/uboot-unlock-bootloader.img',
 'dw100': '/root/usblog-patch-transplant-dw100/uboot-unlock-bootloader.img',
}

T = {
 'vp19':  dict(PUTS=0xE798, TRIG=0x1A768, FORCE_PORT=0x1A720, RET1=0x1A764,
               PUMP_CALL=0x1A8F0, REPLY=0x1A8BC, FLAG=0x1BC34, DEAD_END=0x1BED8),
 'ai3':   dict(PUTS=0xE790, TRIG=0x1A770, FORCE_PORT=0x1A728, RET1=0x1A76C,
               PUMP_CALL=0x1A8F8, REPLY=0x1A8C4, FLAG=0x1BC58, DEAD_END=0x1BEFC),
 'dw99':  dict(PUTS=0xE770, TRIG=0x1A740, FORCE_PORT=0x1A6F8, RET1=0x1A73C,
               PUMP_CALL=0x1A8C8, REPLY=0x1A894, FLAG=0x1BC28, DEAD_END=0x1BECC),
 'dw100': dict(PUTS=0xE770, TRIG=0x1A740, FORCE_PORT=0x1A6F8, RET1=0x1A73C,
               PUMP_CALL=0x1A8C8, REPLY=0x1A894, FLAG=0x1BC28, DEAD_END=0x1BECC),
}

def w(d, off):
    return struct.unpack_from('<I', d, off)[0]

def dec_pair(d, off):
    w1 = w(d, off)
    if (w1 & 0x9F000000) != 0x90000000: return None
    w2 = w(d, off + 4)
    if (w2 & 0xFF800000) != 0x91000000: return None
    if (w1 & 0x1F) != ((w2 >> 5) & 0x1F): return None
    immlo = (w1 >> 29) & 3
    immhi = (w1 >> 5) & 0x7FFFF
    imm = (immhi << 2) | immlo
    if imm & (1 << 20): imm -= (1 << 21)
    pc = off + BASE
    return ((pc & ~0xFFF) + (imm << 12)) + ((w2 >> 10) & 0xFFF)

def partA():
    print('== A. anchors across builds (file offsets) ==')
    for k in ('PUTS','TRIG','FORCE_PORT','RET1','PUMP_CALL','REPLY','FLAG','DEAD_END'):
        print('  %-10s' % k + ''.join(' %-8x' % T[n][k] for n in IMGS))
    print('  deltas vs TRIG:')
    for k in ('RET1','FORCE_PORT','PUMP_CALL','REPLY','FLAG','DEAD_END'):
        print('  %-10s' % k + ''.join(' %+#7x' % (T[n][k]-T[n]['TRIG']) for n in IMGS))

def partB():
    print('== B. TRIG via the "USB SERIAL PORT OPENED" string ==')
    for n, p in IMGS.items():
        d = open(p, 'rb').read()
        s = d.find(b'USB SERIAL PORT OPENED')
        if s < 0:
            print('  %-6s string NOT FOUND' % n); continue
        sva = s + BASE
        sites = [off for off in range(0x200, len(d) - 8, 4)
                 if dec_pair(d, off) == sva]
        trigs = []
        for off in sites:
            for k in range(8, 0x40, 4):
                v = w(d, off + k)
                if (v >> 26) == 0x25:
                    trigs.append(off + k); break
        print('  %-6s str@%#x refsites=%s bl-after=%s known=%#x' % (
            n, s, ['%x' % x for x in sites[:6]],
            ['%x' % x for x in trigs[:6]], T[n]['TRIG']))

if __name__ == '__main__':
    partA()
    partB()
