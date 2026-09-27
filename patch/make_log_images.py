#!/usr/bin/env python3
"""Build the USB-log uboot images from the unlock baseline.

Usage: python3 make_log_images.py [baseline] [outdir]
Default baseline: ../images/uboot.img  (md5 a03efc..., sha256 b8ec59...)

Produced images (all 484260 bytes, [0x30]=0x75EF0 untouched):
  uboot-v25-log.img         console hook + gate byte + trigger stub
  uboot-v26-forceport.img   v25, and always take the 8KB channel alloc
  uboot-v27-alloc.img       v25, and alloc the channel on the timeout path
  uboot-v28-alloc-fast.img  v27, and port-open wait timeout = 0

Addresses are FILE offsets.  VA = file + 0x9EFFFE00, so when converting a
VA back to a file offset remember: file = (VA - 0x9F000000) + 0x200.

Layout inside the never-called fastboot unlock/lock subcommand handler
(file 0x1BC34.., 680 bytes of dead code):
  0x1BC34  gate byte (image value 0 == logging off)
  0x1BC38  trigger stub: set gate=1, tail-call the original printf
  0x1BC4C  log fn: if gate==0 return; clear gate (re-entrancy guard);
           strlen + reply_to_pctool(0x1A8BC); restore gate; ret
  0x1BC9C  hook: redo puts() prologue, call log fn, jump back to 0xE79C
Patched call sites:
  0xE798  puts entry                -> b hook
  0x1A768 "USB SERIAL PORT OPENED"  -> bl trigger stub
  0x1A720 cbnz w0,+0x38 (port ok?)  -> b +0x38        (v26 only)
  0x1A754 b +0x18 (skip alloc)      -> b +0x04        (v27/v28)
  0x1A710 mov x1,#0x7d0 (2000 ms)   -> mov x1,#0        (v28 only)
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

FLAG = 0x1BC34
S1   = 0x1BC38
LOG  = 0x1BC4C
HOOK = 0x1BC9C
TRIG = 0x1A768

MD5 = {
    'uboot-v25-log.img':        '57a103f55e6357876be00aef63fda55f',
    'uboot-v26-forceport.img':  '9a1d5975d299363045fbc702aa3e8fcd',
    'uboot-v27-alloc.img':      'b19f00a8f123177207167e256c6a1264',
    'uboot-v28-alloc-fast.img': '0bfe247bf20b9e95911be0cdc1b0b14a',
    'uboot-v29-portonly.img':   '057265da68e8e9d2833d9a9b2276a4f6',
}

def make(base, force_port=False, alloc_timeout=False, timeout0=False, trigger=True):
    d = bytearray(open(base, 'rb').read())
    assert len(d) == 484260
    assert struct.unpack_from('<I', d, 0x30)[0] == 0x75EF0
    assert struct.unpack_from('<I', d, 0xE798)[0] == 0xA9BF7BFD
    assert struct.unpack_from('<I', d, TRIG)[0] == bl_(va(TRIG), 0x9F00E5D8)
    struct.pack_into('<I', d, FLAG, 0)
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
    for i, w in enumerate(s1):  struct.pack_into('<I', d, S1 + i * 4, w)
    for i, w in enumerate(log): struct.pack_into('<I', d, LOG + i * 4, w)
    for i, w in enumerate(hook):struct.pack_into('<I', d, HOOK + i * 4, w)
    struct.pack_into('<I', d, 0xE798, b_(va(0xE798), va(HOOK)))
    if trigger:
        struct.pack_into('<I', d, TRIG, bl_(va(TRIG), va(S1)))
    if force_port:
        assert struct.unpack_from('<I', d, 0x1A720)[0] == 0x350001C0
        struct.pack_into('<I', d, 0x1A720, 0x1400000E)
    if alloc_timeout:
        assert struct.unpack_from('<I', d, 0x1A754)[0] == 0x14000006
        struct.pack_into('<I', d, 0x1A754, 0x14000001)
    if timeout0:
        assert struct.unpack_from('<I', d, 0x1A710)[0] == 0xD280FA01
        struct.pack_into('<I', d, 0x1A710, 0xD2800001)
    return bytes(d)

def main():
    base = sys.argv[1] if len(sys.argv) > 1 else '../images/uboot.img'
    out  = sys.argv[2] if len(sys.argv) > 2 else '.'
    plans = [
        ('uboot-v25-log.img',        dict()),
        ('uboot-v26-forceport.img',  dict(force_port=True)),
        ('uboot-v27-alloc.img',      dict(alloc_timeout=True)),
        ('uboot-v28-alloc-fast.img', dict(alloc_timeout=True, timeout0=True)),
        ('uboot-v29-portonly.img',   dict(force_port=True, trigger=False)),
    ]
    for name, kw in plans:
        data = make(base, **kw)
        md5 = hashlib.md5(data).hexdigest()
        ok = ('OK' if md5 == MD5[name] else 'MD5 MISMATCH!') if name in MD5 else '(new)'
        p = os.path.join(out, name)
        open(p, 'wb').write(data)
        print('%-26s %s  %s' % (name, md5, ok))

main()
