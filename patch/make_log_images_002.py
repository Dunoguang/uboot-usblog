#!/usr/bin/env python3
"""Build uboot 0.0.2 - the *non-blocking* USB log image.

0.0.2 = 0.0.1 + three generator-level fixes.  All three remove a place where the
console patch made u-boot wait for the host.  The U2S calibration port is being
used purely as a log drain; nothing on the device may ever block on it.

  F1  gate addressing (correctness, prerequisite for extending the block).
      0.0.1's injected adrp/add pairs use #0xC34 - the low 12 bits of the *file*
      offset - so the gate byte the code really reads and writes is file 0x1BE34,
      inside the NEXT dead function (the low byte of an `add x0,x0,#0x7b7`).
      It only works because the trigger stub and the log fn repeat the same
      mistake.  0.0.2 uses the VA's low 12 bits (#0xA34), so the gate is file
      0x1BC34, the zeroed word the layout always intended.

  F2  bounded log pump (the stall fix).
      reply_to_pctool (file 0x1A8BC) ends with
          0x1A8EC  mov  w0, #1
          0x1A8F0  bl   0x9F02D0F0        <-- the pump
      and that pump (file 0x2D2F0) spins on
          0x2D314  ldr w0,[x19] ; cbnz w0,done ; bl usb_gadget_handle_interrupts ; b 0x2D314
      with no timeout.  A host that stops draining EP 0x85 therefore parks
      u-boot inside whatever printf comes next - in practice at the line after
      `sprdfb: mipi_dispc_init_config not support TE`.
      0.0.2 redirects that single call site to an injected `bounded_pump` that
      polls the same completion flag but gives up after BUDGET iterations of the
      gadget interrupt handler.  Log lines become best-effort: they go out
      whenever the host is draining, and are dropped (never retried, never
      waited on) when it is not.

  F4  no tool-command wait.
      sub_9F01A49C ends by returning 1 ("tool present", file 0x1A764
      `mov w19,#1` = 0x52800033).  Its caller sub_9F01A840 therefore calls the
      10-byte pctool command reader sub_9F01A580, which waits out the full
      2000 ms timeout and prints `usb read timeout`.
      Returning 0 instead makes the caller take the shared `mov w0,#-1` path -
      byte-for-byte the flow the stock image takes when no host is attached.
      The console gate is already open at that point (it is set by the trigger on
      the "USB SERIAL PORT OPENED" printf at file 0x1A768, which runs *before*
      the return), so logging is unaffected.  We never send pctool commands.

Not changed: the console hook, the trigger stub, the log fn, the forced 8 KB
gserial channel allocation at file 0x1A720, [0x30] and the 484260-byte length.

Usage:  python3 make_log_images_002.py [baseline] [outdir]
"""
import hashlib, os, struct, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from make_log_images import (build as build_001, va, b_, bl_, adrp, add_imm,
                             BASE_MD5, OUT_MD5 as MD5_001)

# ---------------------------------------------------------------------------
# helpers the 0.0.1 generator does not have
# ---------------------------------------------------------------------------
def movz_w(rd, imm16):         return 0x52800000 | ((imm16 & 0xFFFF) << 5) | rd
def ldr_w_imm(rt, rn, imm12):  return 0xB9400000 | ((imm12 & 0xFFF) << 10) | (rn << 5) | rt
def str_w_imm(rt, rn, imm12):  return 0xB9000000 | ((imm12 & 0xFFF) << 10) | (rn << 5) | rt
def subs_w_imm(rd, rn, imm12): return 0x71000000 | ((imm12 & 0xFFF) << 10) | (rn << 5) | rd
def cbnz_w(rt, delta):         return 0x35000000 | ((delta & 0x7FFFF) << 5) | rt
def b_ne(delta):               return 0x54000000 | ((delta & 0x7FFFF) << 5) | 0x1

# ---------------------------------------------------------------------------
# F2: injected bounded pump, placed after the 0.0.1 block in the same
# never-called fastboot unlock/lock subcommand handler body
# (the dead function spans file 0x1BC34..0x1BED8; 0.0.1's block ends at 0x1BCB7)
# ---------------------------------------------------------------------------
PUMP   = 0x1BCC0
BUDGET = 10000                  # iterations of usb_gadget_handle_interrupts
FLAG_TX = 0x9F1CC118            # the TX-complete flag (w0 == 1 selects it)
IRQ    = 0x9F030CA0             # usb_gadget_handle_interrupts (file 0x30EA0)
CALL   = 0x1A8F0                # the `bl 0x9F02D0F0` inside reply_to_pctool
RET1   = 0x1A764                # `mov w19,#1` in sub_9F01A49C
GATEADDS = (0x1BC3C, 0x1BC5C, 0x1BC88)   # the three `add x1,x1,#0xC34`
OLD_ADD, NEW_ADD = 0x9130D021, 0x9128D021


def pump_code():
    """bounded_pump:

           stp x29,x30,[sp,#-0x20]! ; mov x29,sp ; stp x19,x20,[sp,#0x10]
           adrp x19, FLAG_TX & ~0xFFF ; add x19,x19,#(FLAG_TX & 0xFFF)
           mov w20,#BUDGET
        loop:   ldr w0,[x19] ; cbnz w0,done
                bl IRQ ; subs w20,w20,#1 ; b.ne loop
        done:   str wzr,[x19] ; ldp x19,x20,[sp,#0x10]
                ldp x29,x30,[sp],#0x20 ; ret

    reply_to_pctool only ever passes w0 == 1, so the 0.0.1 pump's second flag
    (0x9F1CC198) is not reproduced."""
    LOOP, DONE = 6, 11
    return [
        0xA9BE7BFD,                                       # 0  stp x29,x30,[sp,#-0x20]!
        0x910003FD,                                       # 1  mov x29,sp
        0xA90153F3,                                       # 2  stp x19,x20,[sp,#0x10]
        adrp(19, va(PUMP + 3 * 4), FLAG_TX & ~0xFFF),     # 3  adrp x19, page
        add_imm(19, 19, FLAG_TX & 0xFFF),                 # 4  add  x19,x19,#0x118
        movz_w(20, BUDGET),                               # 5  mov  w20,#BUDGET
        ldr_w_imm(0, 19, 0),                              # 6  loop: ldr w0,[x19]
        cbnz_w(0, DONE - 7),                              # 7  cbnz w0,done
        bl_(va(PUMP + 8 * 4), IRQ),                       # 8  bl   usb_gadget_handle_interrupts
        subs_w_imm(20, 20, 1),                            # 9  subs w20,w20,#1
        b_ne(LOOP - 10),                                  # 10 b.ne loop
        str_w_imm(31, 19, 0),                             # 11 done: str wzr,[x19]
        0xA94153F3,                                       # 12 ldp x19,x20,[sp,#0x10]
        0xA8C27BFD,                                       # 13 ldp x29,x30,[sp],#0x20
        0xD65F03C0,                                       # 14 ret
    ]


def build_002(base):
    d = bytearray(build_001(base))
    assert hashlib.md5(d).hexdigest() == MD5_001, 'not the 0.0.1 image'

    # F1 - point the three gate adrp/add pairs at the real gate (file 0x1BC34)
    for off in GATEADDS:
        assert struct.unpack_from('<I', d, off)[0] == OLD_ADD, 'gate add @%#x' % off
        struct.pack_into('<I', d, off, NEW_ADD)
    assert struct.unpack_from('<I', d, 0x1BC34)[0] == 0

    # F4 - stop advertising "tool present", which is what triggers the 2 s
    #      10-byte pctool command read
    assert struct.unpack_from('<I', d, RET1)[0] == 0x52800033, 'mov w19,#1'
    struct.pack_into('<I', d, RET1, 0x52800013)               # mov w19,#0

    # F2 - bounded pump
    assert struct.unpack_from('<I', d, CALL)[0] == bl_(va(CALL), 0x9F02D0F0), 'pump call'
    struct.pack_into('<I', d, CALL, bl_(va(CALL), va(PUMP)))
    code = pump_code()
    for i, w in enumerate(code):
        struct.pack_into('<I', d, PUMP + i * 4, w)
    assert PUMP + len(code) * 4 <= 0x1BED8, 'pump overruns the dead function'

    assert struct.unpack_from('<I', d, 0x30)[0] == 0x75EF0
    assert len(d) == 484260
    return bytes(d)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    base = sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, '..', 'images', 'uboot.img')
    out  = sys.argv[2] if len(sys.argv) > 2 else os.path.join(here, '..', 'images')
    data = build_002(base)
    md5  = hashlib.md5(data).hexdigest()
    p = os.path.join(out, 'uboot-0.0.2.img')
    open(p, 'wb').write(data)
    print('uboot-0.0.2.img  %d bytes  md5 %s' % (len(data), md5))
    print('baseline %s and 0.0.1 %s both asserted' % (BASE_MD5, MD5_001))
    return 0


if __name__ == '__main__':
    sys.exit(main())
