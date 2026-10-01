#!/usr/bin/env python3
"""Build uboot 0.0.8 - 0.0.7 with the pump reporting itself through printf.

What 0.0.7 taught us (measured): the hook's string edit DOES work - the USB
capture shows `|SB SERIAL PORT OPENED` and `|prdfb: ... not support TE` - but
the uboot_log partition shows the text untouched (zero '|' bytes in 4 MB).
So the log partition taps the console *above* u-boot's puts, i.e. at the
printf/vprintf level, and cannot observe anything the puts hook does.

It can, however, observe a printf the hook itself makes.  The pump runs inside
reply_to_pctool, at which point the console gate is 0, so a printf from there
goes through the hook and the log fn returns early - no recursion.

  success  -> emit('S')     the TX completion arrived
  timeout  -> emit('P')     the budget ran out without a completion

Both go to the console and therefore into the uboot_log partition of the same
boot, giving an observation channel that does not depend on USB:

  'S'/'P' keep appearing after the TE line -> the hook is alive, USB TX is dead
  'S'/'P' stop together with the TE line   -> the hook is no longer called

Usage:  python3 make_log_images_008.py [baseline] [outdir]
"""
import hashlib, os, struct, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from make_log_images import va, b_, bl_, adrp, add_imm
from make_log_images_002 import movz_w, ldr_w_imm, str_w_imm, subs_w_imm, cbnz_w, b_ne
from make_log_images_007 import build_007

PUMP    = 0x1BCC0
EMIT_P  = 0x1BDF0
EMIT_S  = 0x1BE10
DEAD_END = 0x1BED8

TXFLAG  = 0x9F1CC118
IRQ     = 0x9F030CA0
PRINTF  = 0x9F00E5D8
BUDGET  = 10000

CALL    = 0x1A8F0        # reply_to_pctool -> bl <pump>


def emit_code(addr, ch):
    """printf("<ch>") using a fresh 2-byte stack buffer, so the 0.0.7 string
    marker can never mutate it."""
    return [
        0xD10043FF,                                # 0 sub  sp,sp,#16
        movz_w(3, ch),                             # 1 movz w3,#ch
        0x390003E3,                                # 2 strb w3,[sp]
        0x390007FF,                                # 3 strb wzr,[sp,#1]
        0x910003E0,                                # 4 mov  x0,sp
        bl_(va(addr + 5 * 4), PRINTF),             # 5 bl   printf
        0x910043FF,                                # 6 add  sp,sp,#16
        0xD65F03C0,                                # 7 ret
    ]


def pump_code():
    LOOP, SUCCESS, EPI = 6, 13, 14
    return [
        0xA9BE7BFD,                                       # 0  stp x29,x30,[sp,#-0x20]!
        0x910003FD,                                       # 1  mov x29,sp
        0xA90153F3,                                       # 2  stp x19,x20,[sp,#0x10]
        adrp(19, va(PUMP + 3 * 4), TXFLAG & ~0xFFF),      # 3  adrp x19,page
        add_imm(19, 19, TXFLAG & 0xFFF),                  # 4  add  x19,x19,#0x118
        movz_w(20, BUDGET),                               # 5  mov  w20,#BUDGET
        ldr_w_imm(0, 19, 0),                              # 6  loop: ldr w0,[x19]
        cbnz_w(0, SUCCESS - 7),                           # 7  cbnz w0,success
        bl_(va(PUMP + 8 * 4), IRQ),                       # 8  bl   usb_gadget_handle_interrupts
        subs_w_imm(20, 20, 1),                            # 9  subs w20,w20,#1
        b_ne(LOOP - 10),                                  # 10 b.ne loop
        bl_(va(PUMP + 11 * 4), va(EMIT_P)),               # 11 bl   emit('P')
        b_(va(PUMP + 12 * 4), va(PUMP) + EPI * 4),        # 12 b    epilogue
        bl_(va(PUMP + 13 * 4), va(EMIT_S)),               # 13 success: bl emit('S')
        str_w_imm(31, 19, 0),                             # 14 epilogue: str wzr,[x19]
        0xA94153F3,                                       # 15 ldp x19,x20,[sp,#0x10]
        0xA8C27BFD,                                       # 16 ldp x29,x30,[sp],#0x20
        0xD65F03C0,                                       # 17 ret
    ]


def build_008(base):
    d = bytearray(build_007(base))

    assert struct.unpack_from('<I', d, CALL)[0] == bl_(va(CALL), va(PUMP)), 'pump call'
    p = pump_code()
    assert PUMP + len(p) * 4 <= 0x1BD20, 'pump now collides with usb_gate_wait'
    for i, w in enumerate(p):
        struct.pack_into('<I', d, PUMP + i * 4, w)

    for addr, ch in ((EMIT_P, 0x50), (EMIT_S, 0x53)):
        c = emit_code(addr, ch)
        assert addr + len(c) * 4 <= DEAD_END, 'emit stub overruns the dead function'
        for i, w in enumerate(c):
            struct.pack_into('<I', d, addr + i * 4, w)

    assert struct.unpack_from('<I', d, 0x1BC34)[0] == 0, 'gate byte must stay zero'
    assert struct.unpack_from('<I', d, 0x30)[0] == 0x75EF0
    assert len(d) == 484260
    return bytes(d)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    base = sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, '..', 'images', 'uboot.img')
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(here, '..', 'images')
    data = build_008(base)
    p = os.path.join(out, 'uboot-0.0.8-pumpmark.img')
    open(p, 'wb').write(data)
    print('uboot-0.0.8-pumpmark.img  %d bytes  md5 %s' % (len(data), hashlib.md5(data).hexdigest()))
    return 0


if __name__ == '__main__':
    sys.exit(main())
