#!/usr/bin/env python3
"""Build uboot 0.0.5 = 0.0.2 + a bounded "wait for the host to open the port" loop
placed at the console gate.

Why
---
The gadget is only enumerated for as long as u-boot takes to boot (~2.5 s, window
measured at 2.0..5.5 s depending on the run).  Nothing in the stock calibrate
state machine holds it open: patching its 2000 ms budget (file 0x1A710) changes
nothing, because in a no-host boot those loops leave through their flag, not
through their timeout - measured: every uboot_log slot shows
`lcd start init time` between 2065 and 2363 ms with the 60000 ms 0.0.4 image.

That window is enough for a libusb reader (opens in milliseconds) but not for
sprdvcom.sys, whose CreateFile sequence spans ~3.9 s.  So 0.0.5 inserts the
servicing window where we control it: at the gate trigger (file 0x1A768), just
before "USB SERIAL PORT OPENED" is printed.

    usb_gate_wait   poll [0x9F1CC190] (the gser "port opened" flag, set by
                    gser_setup on SET_CONTROL_LINE_STATE wValue==1)
                    while not set and elapsed < WAIT_MS:
                        usb_gadget_handle_interrupts()
                    -- the only function that reads the MUSB registers, so it is
                    the only thing that can answer the host's control requests.

    S2              the new trigger stub: save x0 (the format string), call
                    usb_gate_wait, restore x0, set gate = 1, tail-call printf.
                    Replaces S1 at TRIG; S1 is left in place but unused.

The loop exits the instant the host opens the port, so an attached reader costs
nothing.  Without a reader the boot pays at most WAIT_MS.

WAIT_MS is the single tunable.

Usage:  python3 make_log_images_005.py [baseline] [outdir]
"""
import hashlib, os, struct, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from make_log_images import va, b_, bl_, adrp, add_imm, cbz_w
from make_log_images_002 import build_002

# ---------------------------------------------------------------------------
WAIT_MS = 20000

GATE_WAIT = 0x1BD00      # usb_gate_wait, 20 instrs
S2        = 0x1BD50      # new trigger stub, 11 instrs
DEAD_END  = 0x1BED8      # end of the never-called fastboot handler body

TRIG      = 0x1A768      # `bl S1` -> `bl S2`
S1        = 0x1BC38
FLAG      = 0x1BC34
PORTFLAG  = 0x9F1CC190    # gser "port opened"
GETTIMER  = 0x9F00184C
IRQ       = 0x9F030CA0    # usb_gadget_handle_interrupts
PRINTF    = 0x9F00E5D8


def movz_x(rd, imm16):        return 0xD2800000 | ((imm16 & 0xFFFF) << 5) | rd
def ldr_w_imm(rt, rn, imm12): return 0xB9400000 | ((imm12 & 0xFFF) << 10) | (rn << 5) | rt
def strb_w_imm(rt, rn, imm12):return 0x39000000 | ((imm12 & 0xFFF) << 10) | (rn << 5) | rt
def orr_x(rd, rn, rm):        return 0xAA000000 | (rm << 16) | (rn << 5) | rd
def sub_x(rd, rn, rm):        return 0xCB000000 | (rm << 16) | (rn << 5) | rd
def cmp_x(rn, rm):            return 0xEB000000 | (rm << 16) | (rn << 5) | 31
def b_cond(delta, cond):      return 0x54000000 | ((delta & 0x7FFFF) << 5) | cond
def cbnz_w(rt, delta):        return 0x35000000 | ((delta & 0x7FFFF) << 5) | rt

LO, DONE = 9, 16          # instruction indices inside usb_gate_wait


def gate_wait_code():
    return [
        0xA9BD7BFD,                                   # 0  stp x29,x30,[sp,#-0x30]!
        0x910003FD,                                   # 1  mov x29,sp
        0xA90153F3,                                   # 2  stp x19,x20,[sp,#0x10]
        0xF90013F5,                                   # 3  str x21,[sp,#0x20]
        adrp(20, va(GATE_WAIT + 4 * 4), PORTFLAG & ~0xFFF),   # 4 adrp x20,page
        add_imm(20, 20, PORTFLAG & 0xFFF),            # 5  add x20,x20,#0x190
        movz_x(21, WAIT_MS),                          # 6  mov x21,#WAIT_MS
        bl_(va(GATE_WAIT + 7 * 4), GETTIMER),         # 7  bl get_timer
        orr_x(19, 31, 0),                             # 8  mov x19,x0   (start)
        ldr_w_imm(0, 20, 0),                          # 9  loop: ldr w0,[x20]
        cbnz_w(0, DONE - 10),                         # 10   cbnz w0,done
        bl_(va(GATE_WAIT + 11 * 4), IRQ),             # 11   bl usb_gadget_handle_interrupts
        bl_(va(GATE_WAIT + 12 * 4), GETTIMER),        # 12   bl get_timer
        sub_x(0, 0, 19),                              # 13   sub x0,x0,x19
        cmp_x(0, 21),                                 # 14   cmp x0,x21
        b_cond(LO - 15, 3),                           # 15   b.lo loop
        0xA94153F3,                                   # 16 done: ldp x19,x20,[sp,#0x10]
        0xF94013F5,                                   # 17   ldr x21,[sp,#0x20]
        0xA8C37BFD,                                   # 18   ldp x29,x30,[sp],#0x30
        0xD65F03C0,                                   # 19   ret
    ]


def s2_code():
    return [
        0xA9BE7BFD,                                   # 0  stp x29,x30,[sp,#-0x20]!
        0x910003FD,                                   # 1  mov x29,sp
        0xF9000BE0,                                   # 2  str x0,[sp,#0x10]   (fmt string)
        bl_(va(S2 + 3 * 4), va(GATE_WAIT)),           # 3  bl usb_gate_wait
        0xF9400BE0,                                   # 4  ldr x0,[sp,#0x10]
        0xA8C27BFD,                                   # 5  ldp x29,x30,[sp],#0x20
        adrp(1, va(S2 + 6 * 4), va(FLAG) & ~0xFFF),   # 6  adrp x1,page
        # low 12 bits of the *VA*, not of the file offset: 0xA34, not 0xC34.
        # Using the file offset here lands 0x200 past the gate byte - the exact
        # defect 0.0.1 shipped with and 0.0.2's F1 fixed.
        add_imm(1, 1, va(FLAG) & 0xFFF),              # 7  add x1,x1,#0xA34
        0xD2800022,                                   # 8  mov w2,#1
        strb_w_imm(2, 1, 0),                          # 9  strb w2,[x1]
        b_(va(S2 + 10 * 4), PRINTF),                  # 10 b printf
    ]


def build_005(base):
    d = bytearray(build_002(base))

    assert struct.unpack_from('<I', d, TRIG)[0] == bl_(va(TRIG), va(S1)), 'TRIG not bl S1'

    gw = gate_wait_code()
    for i, w in enumerate(gw):
        struct.pack_into('<I', d, GATE_WAIT + i * 4, w)

    s2 = s2_code()
    for i, w in enumerate(s2):
        struct.pack_into('<I', d, S2 + i * 4, w)

    assert S2 + len(s2) * 4 <= DEAD_END, 'injected code overruns the dead function'
    struct.pack_into('<I', d, TRIG, bl_(va(TRIG), va(S2)))

    # the gate byte must still be the zeroed word the injected code gates on
    assert struct.unpack_from('<I', d, FLAG)[0] == 0
    assert struct.unpack_from('<I', d, 0x30)[0] == 0x75EF0
    assert len(d) == 484260
    return bytes(d)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    base = sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, '..', 'images', 'uboot.img')
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(here, '..', 'images')
    data = build_005(base)
    p = os.path.join(out, 'uboot-0.0.5-gatewait.img')
    open(p, 'wb').write(data)
    print('uboot-0.0.5-gatewait.img  %d bytes  md5 %s  (WAIT_MS=%d, code ends %#x)'
          % (len(data), hashlib.md5(data).hexdigest(), WAIT_MS, S2 + len(s2_code()) * 4))
    return 0


if __name__ == '__main__':
    sys.exit(main())
