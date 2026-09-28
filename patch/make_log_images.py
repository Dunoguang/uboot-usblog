#!/usr/bin/env python3
"""Build uboot 0.0.1 - the USB-log image (v26 promoted to the baseline version).

Usage:  python3 make_log_images.py [baseline] [outdir]
Default baseline: ../images/uboot.img        (md5 a03efc263613a61680e892261df90954)
Default output:   ../images/uboot-0.0.1.img  (md5 9a1d5975d299363045fbc702aa3e8fcd)

Version history: 0.0.1 is the former `uboot-v26-forceport.img`.  On 2026-09-28
every other experiment (v1..v25, v27..v29) was discarded and v26 became the one
supported image, version 0.0.1 - see ../CHANGELOG.md.  The generator below is
the v26 recipe unchanged, so it reproduces the verified image byte for byte.

0.0.1 contains two parts, both required for the live USB log:

  a) console hook - file 0xE798 (puts entry) -> hook in the never-called
     fastboot unlock handler body (file 0x1BC34..0x1BEDC):
       0x1BC34  gate byte (0 in the image; the trigger sets it, the log fn
                clears it while sending so a re-entrant printf cannot recurse)
       0x1BC38  trigger stub: set gate=1, tail-call the original printf
       0x1BC4C  log fn: gate==0 -> return; clear gate; strlen;
                bl reply_to_pctool (file 0x1A8BC); set gate; ret
       0x1BC9C  hook: recreate the puts prologue, bl log fn, jump back to
                0x9F00E59C
     The gate is set at file 0x1A768, the printf call site of
     "USB SERIAL PORT OPENED".

  b) force the 8 KB gserial channel allocation - file 0x1A720
     (`cbnz w0,+0x38` -> `b +0x38`).  Without a tool handshake u-boot prints
     `usb calibrate port open timeout3871,1870,2000` and skips the
     `bl 0x9F02CDFC` that allocates the ring; with no ring, no byte can ever
     reach the host.

Known limitation of 0.0.1 (why it is a 0.0.x): the boot stops right after
`sprdfb: mipi_dispc_init_config not support TE`, and the log path can block
u-boot (reply_to_pctool ends in an unbounded `while (!flag)
usb_gadget_handle_interrupts();`).  See the "Known issues of 0.0.1" section of
../README.md and ../CHANGELOG.md.

Addresses are FILE offsets.  VA = file + 0x9EFFFE00, so when converting a VA
back to a file offset remember: file = (VA - 0x9F000000) + 0x200.
"""
import struct, hashlib, sys, os

BASE = 0x9EFFFE00
def va(f): return BASE + f
def b_(src, dst): return 0x14000000 | (((dst - src) // 4) & 0x3FFFFFF)
def bl_(src, dst): return 0x94000000 | (((dst - src) // 4) & 0x3FFFFFF)
def adrp(rd, pc, target):
    off = (target & ~0xFFF) - (pc & ~0xFFF)
    imm = (off >> 12) & 0x1FFFFF
    return 0x90000000 | ((imm & 3) << 29) | ((imm >> 2) << 5) | rd
def add_imm(rd, rn, imm): return 0x91000000 | ((imm & 0xFFF) << 10) | (rn << 5) | rd
def cbz_w(rt, delta): return 0x34000000 | ((delta & 0x7FFFF) << 5) | rt

FLAG = 0x1BC34          # gate byte
S1   = 0x1BC38          # trigger stub
LOG  = 0x1BC4C          # log fn
HOOK = 0x1BC9C          # puts hook
TRIG = 0x1A768          # "USB SERIAL PORT OPENED" printf
FORCE_PORT = 0x1A720    # cbnz w0,+0x38 (port opened?)

BASE_MD5 = 'a03efc263613a61680e892261df90954'
OUT_MD5  = '9a1d5975d299363045fbc702aa3e8fcd'
OUT_NAME = 'uboot-0.0.1.img'


def build(base):
    d = bytearray(open(base, 'rb').read())
    assert len(d) == 484260, 'unexpected image length %d' % len(d)
    assert hashlib.md5(d).hexdigest() == BASE_MD5, 'unexpected baseline image'
    assert struct.unpack_from('<I', d, 0x30)[0] == 0x75EF0, '[0x30] must stay 0x75EF0'
    assert struct.unpack_from('<I', d, 0xE798)[0] == 0xA9BF7BFD
    assert struct.unpack_from('<I', d, TRIG)[0] == bl_(va(TRIG), 0x9F00E5D8)
    assert struct.unpack_from('<I', d, FORCE_PORT)[0] == 0x350001C0

    struct.pack_into('<I', d, FLAG, 0)                      # gate = 0
    s1 = [adrp(1, va(S1), va(FLAG)), add_imm(1, 1, FLAG & 0xFFF),
          0xD2800022, 0x39000022, b_(va(S1 + 0x10), 0x9F00E5D8)]
    log = [0xA9BD7BFD, 0x910003FD, 0xF9000BE0,
           adrp(1, va(LOG + 0x0C), va(FLAG)), add_imm(1, 1, FLAG & 0xFFF),
           0x39400021, cbz_w(1, 12),
           0x3900003F, 0xD2800001,
           0x38616802, cbz_w(2, 3),
           0x91000421, b_(va(LOG + 0x30), va(LOG + 0x24)),
           bl_(va(LOG + 0x34), va(0x1A8BC)),
           adrp(1, va(LOG + 0x3C), va(FLAG)), add_imm(1, 1, FLAG & 0xFFF),
           0xD2800022, 0x39000022, 0xA8C37BFD, 0xD65F03C0]
    hook = [0xA9BF7BFD, 0xA9BE7BFD, 0xF9000BE0,
            bl_(va(HOOK + 0x0C), va(LOG)),
            0xF9400BE0, 0xA8C27BFD, b_(va(HOOK + 0x18), va(0xE79C))]
    assert (len(s1), len(log), len(hook)) == (5, 20, 7)
    for i, w in enumerate(s1):   struct.pack_into('<I', d, S1 + i * 4, w)
    for i, w in enumerate(log):  struct.pack_into('<I', d, LOG + i * 4, w)
    for i, w in enumerate(hook): struct.pack_into('<I', d, HOOK + i * 4, w)

    struct.pack_into('<I', d, 0xE798, b_(va(0xE798), va(HOOK)))      # puts -> hook
    struct.pack_into('<I', d, TRIG,   bl_(va(TRIG), va(S1)))         # trigger
    struct.pack_into('<I', d, FORCE_PORT, 0x1400000E)                # force alloc
    assert struct.unpack_from('<I', d, 0x30)[0] == 0x75EF0
    return bytes(d)


def main():
    base = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), '..', 'images', 'uboot.img')
    out  = sys.argv[2] if len(sys.argv) > 2 else os.path.join(os.path.dirname(__file__), '..', 'images')
    data = build(base)
    md5 = hashlib.md5(data).hexdigest()
    p = os.path.join(out, OUT_NAME)
    open(p, 'wb').write(data)
    print('%s  %s  %s' % (OUT_NAME, md5, 'OK' if md5 == OUT_MD5 else 'MD5 MISMATCH (expected %s)' % OUT_MD5))
    return 0 if md5 == OUT_MD5 else 1


if __name__ == '__main__':
    sys.exit(main())
