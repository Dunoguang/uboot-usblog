#!/usr/bin/env python3
"""Build uboot 0.0.7 - 0.0.6 plus a console-visible "the hook ran" mark.

Why: 0.0.6's USB-side markers ('!' on pump give-up, 'g' on the gate-closed
path) both come out empty, which leaves two indistinguishable explanations,
because USB is the only observation channel:

  A  the puts hook stops being called for the output after the TE line
  C  the hook still runs but the ring/TX path is dead, so a marker cannot leave

0.0.7 adds an observation channel that does not use USB at all.  The u-boot
console log is mirrored into the `uboot_log` partition (eMMC, mmcblk0p11) and
can be pulled with plain adb afterwards.  The hook therefore *edits the text
u-boot is about to print*: every 8th call it overwrites the first character of
the string with '|'.  Whatever happens on USB, that edit lands in uboot_log.

  marker '|' keeps appearing after the TE line  -> C (hook alive, TX dead)
  marker '|' stops together with the TE line    -> A (hook no longer called)

The mark is emitted by a stub spliced in front of the log fn, so it runs on
every puts call whether or not the console gate is open.

Counter byte: file 0x1BEC2 (writable RAM inside the never-called function body,
same region the gate byte at 0x1BC34 already uses).

Usage:  python3 make_log_images_007.py [baseline] [outdir]
"""
import hashlib, os, struct, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from make_log_images import va, b_, bl_, adrp, add_imm
from make_log_images_002 import movz_w
from make_log_images_006 import build_006

MARK2   = 0x1BDC0        # mark + branch to the log fn
CTR     = 0x1BEC2        # counter byte
MARKCH  = 0x7C           # '|'
DEAD_END = 0x1BED8

HOOK_BL_LOG = 0x1BCA8    # `bl LOG` inside the puts hook
LOG         = 0x1BC4C


def strb_w_imm(rt, rn, imm12): return 0x39000000 | ((imm12 & 0xFFF) << 10) | (rn << 5) | rt
def ldrb_w_imm(rt, rn, imm12): return 0x39400000 | ((imm12 & 0xFFF) << 10) | (rn << 5) | rt
def add_w_imm(rd, rn, imm12):  return 0x11000000 | ((imm12 & 0xFFF) << 10) | (rn << 5) | rd
def cmp_w_imm(rn, imm12):      return 0x71000000 | ((imm12 & 0xFFF) << 10) | (rn << 5) | 31
def b_cond(delta, cond):       return 0x54000000 | ((delta & 0x7FFFF) << 5) | cond


def mark2_code():
    """Called in place of `bl LOG` from the puts hook.  x0 = the string."""
    DONE = 10
    return [
        adrp(2, va(MARK2 + 0 * 4), va(CTR) & ~0xFFF),  # 0  adrp x2,page
        add_imm(2, 2, va(CTR) & 0xFFF),                # 1  add  x2,x2,#0xEC2
        ldrb_w_imm(3, 2, 0),                           # 2  ldrb w3,[x2]
        add_w_imm(3, 3, 1),                            # 3  add  w3,w3,#1
        strb_w_imm(3, 2, 0),                           # 4  strb w3,[x2]
        cmp_w_imm(3, 8),                               # 5  cmp  w3,#8
        b_cond(DONE - 6, 1),                           # 6  b.ne done
        strb_w_imm(31, 2, 0),                          # 7  strb wzr,[x2]  (reset)
        movz_w(3, MARKCH),                             # 8  mov  w3,#'|'
        strb_w_imm(3, 0, 0),                           # 9  strb w3,[x0]   (mark!)
        b_(va(MARK2 + DONE * 4), va(LOG)),             # 10 done: b LOG
    ]


def build_007(base):
    d = bytearray(build_006(base))

    assert struct.unpack_from('<I', d, HOOK_BL_LOG)[0] == bl_(va(HOOK_BL_LOG), va(LOG)), \
        'hook does not call the log fn'
    code = mark2_code()
    for i, w in enumerate(code):
        struct.pack_into('<I', d, MARK2 + i * 4, w)
    assert MARK2 + len(code) * 4 <= DEAD_END, 'mark stub overruns the dead function'
    struct.pack_into('<I', d, HOOK_BL_LOG, bl_(va(HOOK_BL_LOG), va(MARK2)))

    d[CTR] = 0
    assert struct.unpack_from('<I', d, 0x1BC34)[0] == 0, 'gate byte must stay zero'
    assert struct.unpack_from('<I', d, 0x30)[0] == 0x75EF0
    assert len(d) == 484260
    return bytes(d)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    base = sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, '..', 'images', 'uboot.img')
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(here, '..', 'images')
    data = build_007(base)
    p = os.path.join(out, 'uboot-0.0.7-consolemark.img')
    open(p, 'wb').write(data)
    print('uboot-0.0.7-consolemark.img  %d bytes  md5 %s' % (len(data), hashlib.md5(data).hexdigest()))
    return 0


if __name__ == '__main__':
    sys.exit(main())
