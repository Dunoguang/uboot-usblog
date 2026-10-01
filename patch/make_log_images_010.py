#!/usr/bin/env python3
"""0.0.10 - chunked console log: the >64-byte line fix.

Root cause, measured on hardware
--------------------------------
0.0.5 stops after exactly 240 bytes:

    USB SERIAL PORT OPENED                                     23 B
    ** File not found /recovery/last_memory **                 43 B
    lcd start init time:2110ms                                 27 B
    sprd backlight power brightness=0                          34 B
    phy status0 1f00                                           17 B
    phy status 1f00                                            16 B
    phy status1 1f00                                           17 B
    phy status2 1f1a                                           17 B
    sprdfb: mipi_dispc_init_config not support TE              46 B   <- last one in
    co5300_readid read id value is 0x33,...counter:0           70 B   <- first >64, never arrives

Every line that arrives is <= 46 bytes; the first line longer than the 64-byte
USB bulk max packet size kills the stream permanently.  Confirmed by a
data-only image (0.0.9): shortening *only* that one format string to a 16-byte
line made it arrive, along with the next two short lines (22 B, 34 B), and the
stream then stopped again at the next long line (`uboot consume time:...`,
~70 B).  u-boot itself kept printing all of it - the uboot_log partition slot
written by that same boot contains every line.

So `reply_to_pctool(buf, len)` cannot be handed more than one maxpacket: the
ring advances one packet per completion, and our injected log fn only pumps
once per call, so the ring never drains again.

Fix
---
The log fn walks the string and calls `reply_to_pctool` once per <= CHUNK-byte
piece, so every call is a single packet and matches its single pump.

    0x1BCA8  bl LOG(0x1BC4C)  ->  bl LOG2(0x1BD80)      (the puts hook's call)
    0x1BD80  the new log fn, 39 instructions, ends 0x1BE1C
             (dead fastboot unlock/lock handler body runs to 0x1BED8)

Everything else stays 0.0.5, i.e. 0.0.1's hook/trigger plus:
  0.0.2 F1 real gate address (0xA34), F2 bounded pump, F4 no pctool wait
  0.0.5 S2 trigger + usb_gate_wait (WAIT_MS) so the host can open the port first

[0x30] stays 0x75EF0, length stays 484260.

Usage:  python3 make_log_images_010.py [baseline] [outdir]
"""
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from make_log_images import va, b_, bl_, adrp, add_imm, cbz_w          # noqa: E402
from make_log_images_002 import movz_w, ldr_w_imm, str_w_imm           # noqa: E402
from make_log_images_005 import (movz_x, strb_w_imm, orr_x, sub_x,     # noqa: E402
                                 cmp_x, b_cond, build_005)

# ---------------------------------------------------------------------------
CHUNK = 63                # <= 64-byte bulk max packet, with margin
LOG2  = 0x1BD80           # new log fn (free: 0.0.5 ends at 0x1BD7C)
DEAD_END = 0x1BED8        # end of the never-called fastboot handler body
HOOK_BL  = 0x1BCA8        # `bl LOG` inside the puts hook (HOOK 0x1BC9C + 0x0C)
OLD_LOG  = 0x1BC4C
REPLY    = 0x1A8BC        # reply_to_pctool
FLAG     = 0x1BC34        # gate byte, VA 0x9F01BA34


def cbz_x(rt, delta):          return 0xB4000000 | ((delta & 0x7FFFF) << 5) | rt
def add_x(rd, rn, rm):         return 0x8B000000 | (rm << 16) | (rn << 5) | rd
def subs_imm_x(rd, rn, imm12): return 0xF1000000 | ((imm12 & 0xFFF) << 10) | (rn << 5) | rd
def ldrb_imm(rt, rn, imm12):   return 0x39400000 | ((imm12 & 0xFFF) << 10) | (rn << 5) | rt
def strb_imm(rt, rn, imm12):   return 0x39000000 | ((imm12 & 0xFFF) << 10) | (rn << 5) | rt
def ldr_x_imm(rt, rn, imm12):  return 0xF9400000 | ((imm12 & 0xFFF) << 10) | (rn << 5) | rt
def str_x_imm(rt, rn, imm12):  return 0xF9000000 | ((imm12 & 0xFFF) << 10) | (rn << 5) | rt


EPI, STRLEN, STRLEN_DONE, SEND, SEND_DONE = 34, 14, 19, 20, 32
USE_REM, HAVE = 25, 26


def log2_code():
    return [
        0xA9BC7BFD,                                        #  0 stp x29,x30,[sp,#-0x40]!
        0x910003FD,                                        #  1 mov x29,sp
        0xA90153F3,                                        #  2 stp x19,x20,[sp,#0x10]
        0xA9025BF5,                                        #  3 stp x21,x22,[sp,#0x20]
        str_x_imm(23, 31, 0x30 // 8),                      #  4 str x23,[sp,#0x30]   (imm12 = bytes/8)
        adrp(1, va(LOG2 + 5 * 4), va(FLAG)),               #  5 adrp x1,gate page
        add_imm(1, 1, va(FLAG) & 0xFFF),                    #  6 add x1,x1,#0xA34   (VA low 12 bits!)
        ldrb_imm(2, 1, 0),                                 #  7 ldrb w2,[x1]
        cbz_w(2, EPI - 8),                                 #  8 cbz w2,epilogue
        strb_imm(31, 1, 0),                                #  9 strb wzr,[x1]  clear gate
        orr_x(23, 31, 1),                                  # 10 mov x23,x1     (gate addr)
        orr_x(22, 31, 0),                                  # 11 mov x22,x0     (buf)
        orr_x(19, 31, 0),                                  # 12 mov x19,x0     cursor
        0xD2800014,                                        # 13 mov x20,#0     len
        ldrb_imm(2, 19, 0),                                # 14 strlen: ldrb w2,[x19]
        cbz_w(2, STRLEN_DONE - 15),                        # 15 cbz w2,strlen_done
        add_imm(19, 19, 1),                                # 16 add x19,x19,#1
        add_imm(20, 20, 1),                                # 17 add x20,x20,#1
        b_(va(LOG2 + 18 * 4), va(LOG2 + STRLEN * 4)),      # 18 b strlen
        orr_x(19, 31, 22),                                 # 19 strlen_done: x19 = buf
        cbz_x(20, SEND_DONE - 20),                         # 20 send: cbz x20,done
        movz_x(21, CHUNK),                                 # 21 mov x21,#CHUNK
        subs_imm_x(31, 20, CHUNK),                         # 22 cmp x20,#CHUNK
        b_cond(USE_REM - 23, 9),                           # 23 b.ls use_rem
        b_(va(LOG2 + 24 * 4), va(LOG2 + HAVE * 4)),        # 24 b have_len
        orr_x(21, 31, 20),                                 # 25 use_rem: mov x21,x20
        orr_x(0, 31, 19),                                  # 26 have_len: mov x0,x19
        orr_x(1, 31, 21),                                  # 27 mov x1,x21
        bl_(va(LOG2 + 28 * 4), va(REPLY)),                 # 28 bl reply_to_pctool
        add_x(19, 19, 21),                                 # 29 add x19,x19,x21
        sub_x(20, 20, 21),                                 # 30 sub x20,x20,x21
        b_(va(LOG2 + 31 * 4), va(LOG2 + SEND * 4)),        # 31 b send
        0xD2800022,                                        # 32 send_done: mov w2,#1
        strb_imm(2, 23, 0),                                # 33 strb w2,[x23] restore gate
        ldr_x_imm(23, 31, 0x30 // 8),                      # 34 epilogue: ldr x23,[sp,#0x30]
        0xA9425BF5,                                        # 35 ldp x21,x22,[sp,#0x20]
        0xA94153F3,                                        # 36 ldp x19,x20,[sp,#0x10]
        0xA8C47BFD,                                        # 37 ldp x29,x30,[sp],#0x40
        0xD65F03C0,                                        # 38 ret
    ]


def build_010(base):
    d = bytearray(build_005(base))

    assert d[FLAG] == 0, 'gate byte is not zero'
    import struct
    old = struct.unpack_from('<I', d, HOOK_BL)[0]
    assert old == bl_(va(HOOK_BL), va(OLD_LOG)), 'hook does not call the old log fn'

    code = log2_code()
    for i, w in enumerate(code):
        struct.pack_into('<I', d, LOG2 + i * 4, w)
    assert LOG2 + len(code) * 4 <= DEAD_END, 'log2 overruns the dead function'

    struct.pack_into('<I', d, HOOK_BL, bl_(va(HOOK_BL), va(LOG2)))

    assert struct.unpack_from('<I', d, 0x30)[0] == 0x75EF0
    assert len(d) == 484260
    return bytes(d)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    base = sys.argv[1] if len(sys.argv) > 1 else \
        r'\\wsl.localhost\archlinux\root\github\uboot-usblog\images\uboot.img'
    out = sys.argv[2] if len(sys.argv) > 2 else here
    data = build_010(base)
    p = os.path.join(out, 'uboot-0.0.10-chunked.img')
    open(p, 'wb').write(data)
    print('uboot-0.0.10-chunked.img  %d bytes  md5 %s  (CHUNK=%d, log2 ends %#x)'
          % (len(data), hashlib.md5(data).hexdigest(), CHUNK, LOG2 + len(log2_code()) * 4))
    return 0


if __name__ == '__main__':
    sys.exit(main())

