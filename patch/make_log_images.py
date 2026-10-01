#!/usr/bin/env python3
"""Build images/uboot-0.0.1.img - the live USB log image for the DW99 / vp19
watch (Spreadtrum SL8541E, u-boot 2015.07).

This is the one supported image.  It streams u-boot's console over the U2S
"calibrate" port in real time, and it can never block the boot: every send is
best-effort and bounded.

    python3 patch/make_log_images.py                       # uses ../images/uboot.img
    python3 patch/make_log_images.py <baseline> <outdir>

The build is asserted at every step against the expected original words, and the
result is checked against the released md5, so it either reproduces
images/uboot-0.0.1.img byte for byte or fails loudly.

Run patch/verify_image.py afterwards: it disassembles the injected code and
resolves every branch target.  Two operand mistakes are very easy to make here
and both are silent - see docs/internals.md section 5.
"""
import hashlib
import os
import struct
import sys

# ---------------------------------------------------------------------------
# addressing:  VA = file + 0x9EFFFE00   ->   file = (VA - 0x9F000000) + 0x200
# ---------------------------------------------------------------------------
BASE = 0x9EFFFE00


def va(f):
    return BASE + f


def b_(src, dst):
    return 0x14000000 | (((dst - src) // 4) & 0x3FFFFFF)


def bl_(src, dst):
    return 0x94000000 | (((dst - src) // 4) & 0x3FFFFFF)


def adrp(rd, pc, target):
    off = (target & ~0xFFF) - (pc & ~0xFFF)
    imm = (off >> 12) & 0x1FFFFF
    return 0x90000000 | ((imm & 3) << 29) | ((imm >> 2) << 5) | rd


def add_imm(rd, rn, imm):          return 0x91000000 | ((imm & 0xFFF) << 10) | (rn << 5) | rd
def add_x(rd, rn, rm):             return 0x8B000000 | (rm << 16) | (rn << 5) | rd
def sub_x(rd, rn, rm):             return 0xCB000000 | (rm << 16) | (rn << 5) | rd
def cmp_x(rn, rm):                 return 0xEB000000 | (rm << 16) | (rn << 5) | 31
def subs_w_imm(rd, rn, imm):       return 0x71000000 | ((imm & 0xFFF) << 10) | (rn << 5) | rd
def subs_imm_x(rd, rn, imm):       return 0xF1000000 | ((imm & 0xFFF) << 10) | (rn << 5) | rd
def orr_x(rd, rn, rm):             return 0xAA000000 | (rm << 16) | (rn << 5) | rd
def movz_w(rd, imm16):             return 0x52800000 | ((imm16 & 0xFFFF) << 5) | rd
def movz_x(rd, imm16):             return 0xD2800000 | ((imm16 & 0xFFFF) << 5) | rd
def ldr_w_imm(rt, rn, imm12):      return 0xB9400000 | ((imm12 & 0xFFF) << 10) | (rn << 5) | rt
def ldr_x_imm(rt, rn, imm12):      return 0xF9400000 | ((imm12 & 0xFFF) << 10) | (rn << 5) | rt
def str_w_imm(rt, rn, imm12):      return 0xB9000000 | ((imm12 & 0xFFF) << 10) | (rn << 5) | rt
def str_x_imm(rt, rn, imm12):      return 0xF9000000 | ((imm12 & 0xFFF) << 10) | (rn << 5) | rt
def ldrb_imm(rt, rn, imm12):       return 0x39400000 | ((imm12 & 0xFFF) << 10) | (rn << 5) | rt
def strb_imm(rt, rn, imm12):       return 0x39000000 | ((imm12 & 0xFFF) << 10) | (rn << 5) | rt
def cbz_w(rt, delta):              return 0x34000000 | ((delta & 0x7FFFF) << 5) | rt
def cbz_x(rt, delta):              return 0xB4000000 | ((delta & 0x7FFFF) << 5) | rt
def cbnz_w(rt, delta):             return 0x35000000 | ((delta & 0x7FFFF) << 5) | rt
def b_ne(delta):                   return 0x54000000 | ((delta & 0x7FFFF) << 5) | 0x1
def b_cond(delta, cond):           return 0x54000000 | ((delta & 0x7FFFF) << 5) | cond


def mov_x(rd, rm):                 # mov Xd, Xm  ==  orr Xd, XZR, Xm
    return orr_x(rd, 31, rm)


# ---------------------------------------------------------------------------
# addresses (all FILE offsets unless written as 0x9F... which is a VA)
# ---------------------------------------------------------------------------
FLAG      = 0x1BC34        # console gate byte; VA 0x9F01BA34 -> low 12 bits 0xA34
S1        = 0x1BC38        # 0.0.1 trigger stub - superseded by S2, kept as-is
LOG       = 0x1BC4C        # 0.0.1 log fn     - superseded by LOG2, kept as-is
HOOK      = 0x1BC9C        # puts entry hook (0xE798 branches here)
PUMP      = 0x1BCC0        # bounded pump
GATE_WAIT = 0x1BD00        # wait for the host to open the port
S2        = 0x1BD50        # trigger stub: gate_wait, then set gate, tail-call printf
LOG2      = 0x1BD80        # the chunked log fn (39 instrs, ends 0x1BE1C)
DEAD_END  = 0x1BED8        # end of the never-called fastboot unlock/lock handler

TRIG      = 0x1A768        # printf call site of "USB SERIAL PORT OPENED"
FORCE_PORT= 0x1A720        # cbnz w0,+0x38  (did the port open?)  -> force alloc
RET1      = 0x1A764        # mov w19,#1     ("tool present")     -> return 0
PUMP_CALL = 0x1A8F0        # `bl 0x9F02D0F0` inside reply_to_pctool
HOOK_BL   = 0x1BCA8        # `bl LOG` inside the hook (HOOK + 0x0C)
PUTS      = 0xE798         # puts entry
PUTS_BODY = 0xE79C         # puts body, right after the prologue we recreate
REPLY     = 0x1A8BC        # reply_to_pctool(buf, len) -> VA 0x9F01A6BC
PRINTF    = 0x9F00E5D8
GETTIMER  = 0x9F00184C
IRQ       = 0x9F030CA0      # usb_gadget_handle_interrupts
FLAG_TX   = 0x9F1CC118      # TX-complete flag
PORTFLAG  = 0x9F1CC190      # gser "port opened", set by gser_setup for wValue == 1

GATEADDS  = (0x1BC3C, 0x1BC5C, 0x1BC88)   # the three `add x1,x1,#0xC34`
OLD_ADD,  NEW_ADD = 0x9130D021, 0x9128D021

CHUNK     = 63              # <= 64-byte bulk max packet, with margin
BUDGET    = 10000           # bounded-pump iterations of the gadget handler
WAIT_MS   = 20000           # how long u-boot may wait for the host to open the port

BASE_MD5 = 'a03efc263613a61680e892261df90954'      # images/uboot.img
OUT_MD5  = 'aa780e3b519d59aa65979def9b83eb44'      # images/uboot-0.0.1.img
OUT_NAME = 'uboot-0.0.1.img'


# ---------------------------------------------------------------------------
# the injected blocks
# ---------------------------------------------------------------------------
def s1_code():
    """0.0.1's trigger stub: set the gate, tail-call the original printf."""
    return [adrp(1, va(S1), va(FLAG)), add_imm(1, 1, FLAG & 0xFFF),
            0xD2800022, 0x39000022, b_(va(S1 + 0x10), PRINTF)]


def log_code():
    """0.0.1's log fn, kept verbatim.  It gates, measures with an inline strlen
    and sends the whole line in one reply_to_pctool call - the defect that
    LOG2 below fixes.  Unused by the shipped hook."""
    return [0xA9BD7BFD, 0x910003FD, 0xF9000BE0,
            adrp(1, va(LOG + 0x0C), va(FLAG)), add_imm(1, 1, FLAG & 0xFFF),
            0x39400021, cbz_w(1, 12),
            0x3900003F, 0xD2800001,
            0x38616802, cbz_w(2, 3),
            0x91000421, b_(va(LOG + 0x30), va(LOG + 0x24)),
            bl_(va(LOG + 0x34), va(REPLY)),
            adrp(1, va(LOG + 0x3C), va(FLAG)), add_imm(1, 1, FLAG & 0xFFF),
            0xD2800022, 0x39000022, 0xA8C37BFD, 0xD65F03C0]


def hook_code():
    """puts entry: recreate the prologue we overwrite, call the log fn, return
    to the real body at PUTS_BODY (which expects the stack already set up)."""
    return [0xA9BF7BFD, 0xA9BE7BFD, 0xF9000BE0,
            bl_(va(HOOK + 0x0C), va(LOG)),
            0xF9400BE0, 0xA8C27BFD, b_(va(HOOK + 0x18), va(PUTS_BODY))]


def pump_code():
    """bounded_pump: poll the TX-complete flag, but give up after BUDGET
    iterations so a host that has stopped reading can never park the boot.

        loop:  ldr w0,[x19] ; cbnz w0,done
               bl IRQ ; subs w20,w20,#1 ; b.ne loop
        done:  str wzr,[x19] ; ... ; ret
    """
    LOOP, DONE = 6, 11
    return [
        0xA9BE7BFD,                                    #  0 stp x29,x30,[sp,#-0x20]!
        0x910003FD,                                    #  1 mov x29,sp
        0xA90153F3,                                    #  2 stp x19,x20,[sp,#0x10]
        adrp(19, va(PUMP + 3 * 4), FLAG_TX & ~0xFFF),  #  3 adrp x19,page
        add_imm(19, 19, FLAG_TX & 0xFFF),              #  4 add  x19,x19,#0x118
        movz_w(20, BUDGET),                            #  5 mov  w20,#BUDGET
        ldr_w_imm(0, 19, 0),                           #  6 loop: ldr w0,[x19]
        cbnz_w(0, DONE - 7),                           #  7 cbnz w0,done
        bl_(va(PUMP + 8 * 4), IRQ),                    #  8 bl   usb_gadget_handle_interrupts
        subs_w_imm(20, 20, 1),                         #  9 subs w20,w20,#1
        b_ne(LOOP - 10),                               # 10 b.ne loop
        str_w_imm(31, 19, 0),                          # 11 done: str wzr,[x19]
        0xA94153F3,                                    # 12 ldp x19,x20,[sp,#0x10]
        0xA8C27BFD,                                    # 13 ldp x29,x30,[sp],#0x20
        0xD65F03C0,                                    # 14 ret
    ]


def gate_wait_code():
    """usb_gate_wait: wait (bounded) for the host to open the port.

    u-boot's own port-open wait has a 2 s budget and a PC-side driver needs
    ~3.9 s to get a handle open, so without this the channel is often never
    allocated.  Polling the gser "port opened" flag and servicing the gadget
    lets u-boot hold the window open instead of racing it.  Exits the instant
    the host opens the port, so an attached reader costs nothing.
    """
    LO, DONE = 9, 16
    return [
        0xA9BD7BFD,                                   #  0 stp x29,x30,[sp,#-0x30]!
        0x910003FD,                                   #  1 mov x29,sp
        0xA90153F3,                                   #  2 stp x19,x20,[sp,#0x10]
        0xF90013F5,                                   #  3 str x21,[sp,#0x20]
        adrp(20, va(GATE_WAIT + 4 * 4), PORTFLAG & ~0xFFF),   # 4 adrp x20,page
        add_imm(20, 20, PORTFLAG & 0xFFF),            #  5 add x20,x20,#0x190
        movz_x(21, WAIT_MS),                          #  6 mov x21,#WAIT_MS
        bl_(va(GATE_WAIT + 7 * 4), GETTIMER),         #  7 bl get_timer
        mov_x(19, 0),                                 #  8 mov x19,x0  (start)
        ldr_w_imm(0, 20, 0),                          #  9 loop: ldr w0,[x20]
        cbnz_w(0, DONE - 10),                         # 10 cbnz w0,done
        bl_(va(GATE_WAIT + 11 * 4), IRQ),             # 11 bl usb_gadget_handle_interrupts
        bl_(va(GATE_WAIT + 12 * 4), GETTIMER),        # 12 bl get_timer
        sub_x(0, 0, 19),                              # 13 sub x0,x0,x19
        cmp_x(0, 21),                                 # 14 cmp x0,x21
        b_cond(LO - 15, 3),                           # 15 b.lo loop
        0xA94153F3,                                   # 16 done: ldp x19,x20,[sp,#0x10]
        0xF94013F5,                                   # 17 ldr x21,[sp,#0x20]
        0xA8C37BFD,                                   # 18 ldp x29,x30,[sp],#0x30
        0xD65F03C0,                                   # 19 ret
    ]


def s2_code():
    """The trigger stub actually used: save the format string, wait for the
    host, set gate = 1, tail-call printf.

    The `add` below must use the VA's low 12 bits (0xA34).  Using the file
    offset's (0xC34) lands 0x200 past the byte this stub sets, so the log fn
    never sees an open gate and the image emits nothing at all.
    """
    return [
        0xA9BE7BFD,                                   #  0 stp x29,x30,[sp,#-0x20]!
        0x910003FD,                                   #  1 mov x29,sp
        0xF9000BE0,                                   #  2 str x0,[sp,#0x10]
        bl_(va(S2 + 3 * 4), va(GATE_WAIT)),           #  3 bl usb_gate_wait
        0xF9400BE0,                                   #  4 ldr x0,[sp,#0x10]
        0xA8C27BFD,                                   #  5 ldp x29,x30,[sp],#0x20
        adrp(1, va(S2 + 6 * 4), va(FLAG) & ~0xFFF),   #  6 adrp x1,page
        add_imm(1, 1, va(FLAG) & 0xFFF),              #  7 add x1,x1,#0xA34
        0xD2800022,                                   #  8 mov w2,#1
        strb_imm(2, 1, 0),                            #  9 strb w2,[x1]
        b_(va(S2 + 10 * 4), PRINTF),                  # 10 b printf
    ]


EPI, STRLEN, STRLEN_DONE, SEND, SEND_DONE = 34, 14, 19, 20, 32
USE_REM, HAVE = 25, 26


def log2_code():
    """The log fn that ships.

        if (gate == 0) return;            // the gate is opened by S2
        gate = 0;                         // re-entrancy guard
        len = strlen(buf);
        for (off = 0; off < len; off += CHUNK)
            reply_to_pctool(buf + off, min(CHUNK, len - off));
        gate = 1;                         // restore

    One reply_to_pctool call per <= 63-byte piece is the whole point: that
    function ends in a single pump waiting for a single IN-endpoint completion,
    i.e. one 64-byte packet.  Hand it more and the ring never drains again -
    silently, forever, while u-boot happily keeps booting.
    """
    return [
        0xA9BC7BFD,                                   #  0 stp x29,x30,[sp,#-0x40]!
        0x910003FD,                                   #  1 mov x29,sp
        0xA90153F3,                                   #  2 stp x19,x20,[sp,#0x10]
        0xA9025BF5,                                   #  3 stp x21,x22,[sp,#0x20]
        str_x_imm(23, 31, 0x30 // 8),                 #  4 str x23,[sp,#0x30]  (imm12 = bytes/8)
        adrp(1, va(LOG2 + 5 * 4), va(FLAG)),          #  5 adrp x1,gate page
        add_imm(1, 1, va(FLAG) & 0xFFF),              #  6 add x1,x1,#0xA34   (VA low 12 bits)
        ldrb_imm(2, 1, 0),                            #  7 ldrb w2,[x1]
        cbz_w(2, EPI - 8),                            #  8 cbz w2,epilogue
        strb_imm(31, 1, 0),                           #  9 strb wzr,[x1]      clear gate
        mov_x(23, 1),                                 # 10 mov x23,x1         (gate addr)
        mov_x(22, 0),                                 # 11 mov x22,x0         (buf)
        mov_x(19, 0),                                 # 12 mov x19,x0         cursor
        0xD2800014,                                   # 13 mov x20,#0         len
        ldrb_imm(2, 19, 0),                           # 14 strlen: ldrb w2,[x19]
        cbz_w(2, STRLEN_DONE - 15),                   # 15 cbz w2,strlen_done
        add_imm(19, 19, 1),                           # 16 add x19,x19,#1
        add_imm(20, 20, 1),                           # 17 add x20,x20,#1
        b_(va(LOG2 + 18 * 4), va(LOG2 + STRLEN * 4)), # 18 b strlen
        mov_x(19, 22),                                # 19 strlen_done: x19 = buf
        cbz_x(20, SEND_DONE - 20),                    # 20 send: cbz x20,done
        movz_x(21, CHUNK),                            # 21 mov x21,#CHUNK
        subs_imm_x(31, 20, CHUNK),                    # 22 cmp x20,#CHUNK
        b_cond(USE_REM - 23, 9),                      # 23 b.ls use_rem
        b_(va(LOG2 + 24 * 4), va(LOG2 + HAVE * 4)),   # 24 b have_len
        mov_x(21, 20),                                # 25 use_rem: mov x21,x20
        mov_x(0, 19),                                 # 26 have_len: mov x0,x19
        mov_x(1, 21),                                 # 27 mov x1,x21
        bl_(va(LOG2 + 28 * 4), va(REPLY)),            # 28 bl reply_to_pctool
        add_x(19, 19, 21),                            # 29 add x19,x19,x21
        sub_x(20, 20, 21),                            # 30 sub x20,x20,x21
        b_(va(LOG2 + 31 * 4), va(LOG2 + SEND * 4)),   # 31 b send
        0xD2800022,                                   # 32 send_done: mov w2,#1
        strb_imm(2, 23, 0),                           # 33 strb w2,[x23]  restore gate
        ldr_x_imm(23, 31, 0x30 // 8),                 # 34 epilogue: ldr x23,[sp,#0x30]
        0xA9425BF5,                                   # 35 ldp x21,x22,[sp,#0x20]
        0xA94153F3,                                   # 36 ldp x19,x20,[sp,#0x10]
        0xA8C47BFD,                                   # 37 ldp x29,x30,[sp],#0x40
        0xD65F03C0,                                   # 38 ret
    ]


def put(d, off, words, what):
    for i, w in enumerate(words):
        struct.pack_into('<I', d, off + i * 4, w)
    print('  %-34s file %#08x  %2d instrs' % (what, off, len(words)))


def build(base):
    d = bytearray(open(base, 'rb').read())
    assert len(d) == 484260, 'unexpected image length %d' % len(d)
    assert hashlib.md5(d).hexdigest() == BASE_MD5, 'unexpected baseline image'
    assert struct.unpack_from('<I', d, 0x30)[0] == 0x75EF0, '[0x30] must stay 0x75EF0'

    print('patching %s:' % os.path.basename(base))

    # --- the console hook -------------------------------------------------
    assert struct.unpack_from('<I', d, PUTS)[0] == 0xA9BF7BFD, 'puts prologue'
    assert struct.unpack_from('<I', d, TRIG)[0] == bl_(va(TRIG), PRINTF), 'TRIG'
    struct.pack_into('<I', d, FLAG, 0)
    put(d, S1, s1_code(), 'trigger stub S1 (vestigial)')
    put(d, LOG, log_code(), 'log fn LOG (vestigial)')
    put(d, HOOK, hook_code(), 'puts hook')
    struct.pack_into('<I', d, PUTS, b_(va(PUTS), va(HOOK)))
    struct.pack_into('<I', d, TRIG, bl_(va(TRIG), va(S1)))
    print('  %-34s file %#08x  -> hook' % ('puts entry', PUTS))

    # --- never block, never wait for a tool ------------------------------
    assert struct.unpack_from('<I', d, PUMP_CALL)[0] == bl_(va(PUMP_CALL), 0x9F02D0F0)
    assert struct.unpack_from('<I', d, RET1)[0] == 0x52800033, 'mov w19,#1'
    assert struct.unpack_from('<I', d, FORCE_PORT)[0] == 0x350001C0, 'cbnz w0,+0x38'
    put(d, PUMP, pump_code(), 'bounded pump')
    struct.pack_into('<I', d, PUMP_CALL, bl_(va(PUMP_CALL), va(PUMP)))
    struct.pack_into('<I', d, RET1, 0x52800013)           # mov w19,#0
    struct.pack_into('<I', d, FORCE_PORT, 0x1400000E)     # b +0x38
    print('  %-34s file %#08x' % ('force channel allocation', FORCE_PORT))
    print('  %-34s file %#08x' % ('no pctool command wait', RET1))

    # --- the gate address (0xA34, not 0xC34) -----------------------------
    for off in GATEADDS:
        assert struct.unpack_from('<I', d, off)[0] == OLD_ADD, 'gate add @%#x' % off
        struct.pack_into('<I', d, off, NEW_ADD)

    # --- hold the channel open until the host has opened the port ---------
    put(d, GATE_WAIT, gate_wait_code(), 'usb_gate_wait (WAIT_MS=%d)' % WAIT_MS)
    put(d, S2, s2_code(), 'trigger stub S2')
    struct.pack_into('<I', d, TRIG, bl_(va(TRIG), va(S2)))

    # --- and the actual fix: one max packet per send ----------------------
    assert d[FLAG] == 0, 'gate byte is not zero'
    assert struct.unpack_from('<I', d, HOOK_BL)[0] == bl_(va(HOOK_BL), va(LOG))
    put(d, LOG2, log2_code(), 'log fn LOG2 (CHUNK=%d)' % CHUNK)
    struct.pack_into('<I', d, HOOK_BL, bl_(va(HOOK_BL), va(LOG2)))
    assert LOG2 + len(log2_code()) * 4 <= DEAD_END, 'injected code overruns the dead function'

    assert struct.unpack_from('<I', d, 0x30)[0] == 0x75EF0
    assert len(d) == 484260
    return bytes(d)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    base = sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, '..', 'images', 'uboot.img')
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(here, '..', 'images')
    data = build(base)
    md5 = hashlib.md5(data).hexdigest()
    p = os.path.join(out, OUT_NAME)
    open(p, 'wb').write(data)
    ok = md5 == OUT_MD5
    print('\n%s  %d bytes  md5 %s  %s'
          % (OUT_NAME, len(data), md5, 'OK (matches the released image)' if ok
             else 'MISMATCH - expected %s' % OUT_MD5))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
