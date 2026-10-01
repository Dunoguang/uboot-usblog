#!/usr/bin/env python3
"""Build uboot 0.0.6 - 0.0.5 (gate wait) plus two 1-byte self-reporting markers.

Question this image answers: the live log always stops on the line
`sprdfb: mipi_dispc_init_config not support TE` (measured 240 bytes, three
separate runs, identical byte count) and then the device sends nothing for the
~4.9 s it stays enumerated.  Why?

Three candidates:

  A  the puts hook is no longer called for later output
  B  the console gate is stuck at 0, so LOG returns immediately
  C  the ring write / TX is dead (the gserial tty handle is zero, so
     reply_to_pctool queues nothing and the pump waits for a completion that
     can never arrive)

Both markers are written by calling the ring write directly
(sub_9F02CFD0, file 0x2D1D0), bypassing the gate, so the marker can only appear
if the write path itself still works:

  '!'  written by bounded_pump when it exhausts its budget without ever seeing
       [0x9F1CC118].  Seeing '!' proves hook -> LOG -> reply_to_pctool -> pump is
       alive AND that a byte still travels device -> host.
  'g'  written by LOG on the gate==0 early-return path.  Seeing 'g' proves the
       hook is still being called but the gate is stuck closed.

Reading the capture:
  text... then nothing           -> A (hook dead for the later output)
  text... then 'g' only          -> B (gate stuck)
  text... then '!' only          -> the text path broke while the chain is alive
  text... then '!' and/or 'g'    -> the chain and TX are fine, so the loss is in
                                    the normal write, not in USB

Layout inside the never-called fastboot handler body (file 0x1BC34..0x1BED8):

  0x1BC34  gate byte (0.0.2 F1)          0x1BC38  S1 trigger stub (unused now)
  0x1BC4C  LOG fn (20)                   0x1BC9C  HOOK (7)
  0x1BCC0  bounded_pump, now 21 instrs   0x1BD20  usb_gate_wait (20)
  0x1BD74  S2 trigger stub (11)          0x1BDA4  gate-closed marker stub (5)
  0x1BEC0  '!'   0x1BEC1  'g'

Usage:  python3 make_log_images_006.py [baseline] [outdir]
"""
import hashlib, os, struct, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from make_log_images import va, b_, bl_, adrp, add_imm, cbz_w
from make_log_images_002 import build_002, movz_w, ldr_w_imm, str_w_imm, \
    subs_w_imm, cbnz_w, b_ne

GATE_WAIT = 0x1BD20
S2        = 0x1BD74
STUB_G    = 0x1BDA4
MARK_BANG = 0x1BEC0
MARK_G    = 0x1BEC1
DEAD_END  = 0x1BED8

PUMP      = 0x1BCC0
CALL      = 0x1A8F0          # reply_to_pctool -> bl 0x9F02D0F0 in 0.0.1/0.0.2
BUDGET    = 10000

LOG       = 0x1BC4C
LOG_EPI   = LOG + 18 * 4     # 0x1BC94, ldp x29,x30,[sp],#0x30
GATE_CBZ  = 0x1BC64          # cbz w1, +12 inside LOG (index 6)

FLAG      = 0x1BC34
TRIG      = 0x1A768
S1        = 0x1BC38

TXFLAG    = 0x9F1CC118
PORTFLAG  = 0x9F1CC190
IRQ       = 0x9F030CA0
RING      = 0x9F02CFD0       # ring write (file 0x2D1D0)
GETTIMER  = 0x9F00184C
PRINTF    = 0x9F00E5D8

WAIT_MS   = 20000


def orr_x(rd, rn, rm):        return 0xAA000000 | (rm << 16) | (rn << 5) | rd
def sub_x(rd, rn, rm):        return 0xCB000000 | (rm << 16) | (rn << 5) | rd
def cmp_x(rn, rm):            return 0xEB000000 | (rm << 16) | (rn << 5) | 31
def b_cond(delta, cond):      return 0x54000000 | ((delta & 0x7FFFF) << 5) | cond
def strb_w_imm(rt, rn, imm12): return 0x39000000 | ((imm12 & 0xFFF) << 10) | (rn << 5) | rt


def marker_instrs(addr, mark_va):
    """adrp x0,page(mark) ; add x0,x0,#low ; mov w1,#1 ; bl ring_write"""
    return [adrp(0, va(addr), mark_va & ~0xFFF),
            add_imm(0, 0, mark_va & 0xFFF),
            movz_w(1, 1),
            bl_(va(addr + 3 * 4), RING)]


def pump_code():
    """bounded_pump + '!' marker on the give-up path."""
    LOOP, MARKS, SUCCESS = 6, 11, 17
    code = [
        0xA9BE7BFD,                                    # 0  stp x29,x30,[sp,#-0x20]!
        0x910003FD,                                    # 1  mov x29,sp
        0xA90153F3,                                    # 2  stp x19,x20,[sp,#0x10]
        adrp(19, va(PUMP + 3 * 4), TXFLAG & ~0xFFF),   # 3  adrp x19,page
        add_imm(19, 19, TXFLAG & 0xFFF),               # 4  add x19,x19,#0x118
        movz_w(20, BUDGET),                            # 5  mov w20,#BUDGET
        ldr_w_imm(0, 19, 0),                           # 6  loop: ldr w0,[x19]
        cbnz_w(0, SUCCESS - 7),                        # 7  cbnz w0,success
        bl_(va(PUMP + 8 * 4), IRQ),                    # 8  bl usb_gadget_handle_interrupts
        subs_w_imm(20, 20, 1),                         # 9  subs w20,w20,#1
        b_ne(LOOP - 10),                               # 10 b.ne loop
    ]
    code += marker_instrs(PUMP + MARKS * 4, va(MARK_BANG))     # 11..14
    code += [
        b_(va(PUMP + 15 * 4), va(PUMP) + SUCCESS * 4),  # 15 b success
        0xD503201F,                                     # 16 nop (pad)
        str_w_imm(31, 19, 0),                           # 17 success: str wzr,[x19]
        0xA94153F3,                                     # 18 ldp x19,x20,[sp,#0x10]
        0xA8C27BFD,                                     # 19 ldp x29,x30,[sp],#0x20
        0xD65F03C0,                                     # 20 ret
    ]
    return code


def gate_wait_code():
    LO, DONE = 9, 16
    return [
        0xA9BD7BFD,                                    # 0
        0x910003FD,                                    # 1
        0xA90153F3,                                    # 2
        0xF90013F5,                                    # 3  str x21,[sp,#0x20]
        adrp(20, va(GATE_WAIT + 4 * 4), PORTFLAG & ~0xFFF),   # 4
        add_imm(20, 20, PORTFLAG & 0xFFF),             # 5
        movz_w(21, WAIT_MS),                           # 6
        bl_(va(GATE_WAIT + 7 * 4), GETTIMER),          # 7
        orr_x(19, 31, 0),                              # 8  mov x19,x0
        ldr_w_imm(0, 20, 0),                           # 9  loop
        cbnz_w(0, DONE - 10),                          # 10
        bl_(va(GATE_WAIT + 11 * 4), IRQ),              # 11
        bl_(va(GATE_WAIT + 12 * 4), GETTIMER),         # 12
        sub_x(0, 0, 19),                               # 13
        cmp_x(0, 21),                                  # 14
        b_cond(LO - 15, 3),                            # 15 b.lo loop
        0xA94153F3,                                    # 16 done
        0xF94013F5,                                    # 17
        0xA8C37BFD,                                    # 18
        0xD65F03C0,                                    # 19
    ]


def s2_code():
    return [
        0xA9BE7BFD,                                    # 0
        0x910003FD,                                    # 1
        0xF9000BE0,                                    # 2
        bl_(va(S2 + 3 * 4), va(GATE_WAIT)),            # 3
        0xF9400BE0,                                    # 4
        0xA8C27BFD,                                    # 5
        adrp(1, va(S2 + 6 * 4), va(FLAG) & ~0xFFF),    # 6
        add_imm(1, 1, va(FLAG) & 0xFFF),               # 7   low 12 bits of the VA
        0xD2800022,                                    # 8   mov w2,#1
        strb_w_imm(2, 1, 0),                           # 9   strb w2,[x1]
        b_(va(S2 + 10 * 4), PRINTF),                   # 10
    ]


def stub_g_code():
    return marker_instrs(STUB_G, va(MARK_G)) + \
           [b_(va(STUB_G + 4 * 4), va(LOG_EPI))]


def place(d, addr, words, limit):
    assert addr + len(words) * 4 <= limit, 'code at %#x overruns %#x' % (addr, limit)
    for i, w in enumerate(words):
        struct.pack_into('<I', d, addr + i * 4, w)
    return addr + len(words) * 4


def build_006(base):
    d = bytearray(build_002(base))

    # 1. bounded_pump: rebuilt at the same address, now with the '!' marker.
    #    build_002 already redirected this call site at 0x1BCC0, so the pump
    #    keeps its address and only its body changes.
    assert struct.unpack_from('<I', d, CALL)[0] == bl_(va(CALL), va(PUMP)), 'pump call'
    struct.pack_into('<I', d, CALL, bl_(va(CALL), va(PUMP)))
    end = place(d, PUMP, pump_code(), 0x1BD20)

    # 2. gate wait + new trigger stub
    end = place(d, GATE_WAIT, gate_wait_code(), S2)
    end = place(d, S2, s2_code(), STUB_G)
    struct.pack_into('<I', d, TRIG, bl_(va(TRIG), va(S2)))

    # 3. gate-closed marker stub, reached from LOG's `cbz w1,+12`
    place(d, STUB_G, stub_g_code(), DEAD_END)
    delta = (STUB_G - GATE_CBZ) // 4
    assert struct.unpack_from('<I', d, GATE_CBZ)[0] == cbz_w(1, 12), 'LOG gate cbz'
    struct.pack_into('<I', d, GATE_CBZ, cbz_w(1, delta))

    # 4. marker data bytes
    d[MARK_BANG] = 0x21        # '!'
    d[MARK_G] = 0x67           # 'g'

    assert struct.unpack_from('<I', d, FLAG)[0] == 0, 'gate byte must stay zero'
    assert struct.unpack_from('<I', d, 0x30)[0] == 0x75EF0
    assert len(d) == 484260
    return bytes(d)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    base = sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, '..', 'images', 'uboot.img')
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(here, '..', 'images')
    data = build_006(base)
    p = os.path.join(out, 'uboot-0.0.6-trace.img')
    open(p, 'wb').write(data)
    print('uboot-0.0.6-trace.img  %d bytes  md5 %s' % (len(data), hashlib.md5(data).hexdigest()))
    return 0


if __name__ == '__main__':
    sys.exit(main())
