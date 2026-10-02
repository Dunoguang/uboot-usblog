"""Unified usblog patch builder.

One parameterized build() replaces the four per-device generator copies.
The injected blocks are the exact 0.0.1 / bounded-pump logic; every address
comes from the anchor table.  Address convention matches the legacy
generators: PUTS/TRIG/... are file offsets, PRINTF/GETTIMER/IRQ/STOCK_PUMP/
FLAG_TX/PORTFLAG are VAs.
"""
import struct

from . import aarch64 as A

BASE = 0x9EFFFE00
CHUNK = 63        # <= 64-byte bulk max packet, with margin
BUDGET = 10000    # bounded-pump iterations of the gadget handler
WAIT_MS = 20000   # how long u-boot may wait for the host to open the port


class BuildError(Exception):
    pass


def va(f):
    return BASE + f


def s1_code(a):
    """S1 trigger stub (vestigial): set the gate, tail-call printf."""
    return [A.adrp(1, va(a['S1']), va(a['FLAG'])),
            A.add_imm(1, 1, va(a['FLAG']) & 0xFFF),
            0xD2800022, 0x39000022, A.b_(va(a['S1'] + 0x10), a['PRINTF'])]


def log_code(a):
    """LOG (vestigial): gate, inline strlen, one reply_to_pctool call."""
    LOG = a['LOG']
    return [0xA9BD7BFD, 0x910003FD, 0xF9000BE0,
            A.adrp(1, va(LOG + 0x0C), va(a['FLAG'])),
            A.add_imm(1, 1, va(a['FLAG']) & 0xFFF),
            0x39400021, A.cbz_w(1, 12),
            0x3900003F, 0xD2800001,
            0x38616802, A.cbz_w(2, 3),
            0x91000421, A.b_(va(LOG + 0x30), va(LOG + 0x24)),
            A.bl_(va(LOG + 0x34), va(a['REPLY'])),
            A.adrp(1, va(LOG + 0x3C), va(a['FLAG'])),
            A.add_imm(1, 1, va(a['FLAG']) & 0xFFF),
            0xD2800022, 0x39000022, 0xA8C37BFD, 0xD65F03C0]


def hook_code(a):
    """puts entry hook: recreate the prologue, call the log fn, return
    to the real body at PUTS_BODY (which expects the stack set up)."""
    return [0xA9BF7BFD, 0xA9BE7BFD, 0xF9000BE0,
            A.bl_(va(a['HOOK'] + 0x0C), va(a['LOG'])),
            0xF9400BE0, 0xA8C27BFD, A.b_(va(a['HOOK'] + 0x18), va(a['PUTS_BODY']))]


def pump_code(a):
    """bounded_pump: poll the TX flag, give up after BUDGET iterations."""
    LOOP, DONE = 6, 11
    PUMP = a['PUMP']
    return [
        0xA9BE7BFD,                                   #  0 stp x29,x30,[sp,#-0x20]!
        0x910003FD,                                   #  1 mov x29,sp
        0xA90153F3,                                   #  2 stp x19,x20,[sp,#0x10]
        A.adrp(19, va(PUMP + 3 * 4), a['FLAG_TX'] & ~0xFFF),
        A.add_imm(19, 19, a['FLAG_TX'] & 0xFFF),
        A.movz_w(20, BUDGET),
        A.ldr_w_imm(0, 19, 0),                        #  6 loop: ldr w0,[x19]
        A.cbnz_w(0, DONE - 7),                        #  7 cbnz w0,done
        A.bl_(va(PUMP + 8 * 4), a['IRQ']),            #  8 bl usb_gadget_handle_interrupts
        A.subs_w_imm(20, 20, 1),                      #  9 subs w20,w20,#1
        A.b_ne(LOOP - 10),                            # 10 b.ne loop
        A.str_w_imm(31, 19, 0),                       # 11 done: str wzr,[x19]
        0xA94153F3,                                   # 12 ldp x19,x20,[sp,#0x10]
        0xA8C27BFD,                                   # 13 ldp x29,x30,[sp],#0x20
        0xD65F03C0,                                   # 14 ret
    ]


def gate_wait_code(a):
    """usb_gate_wait: wait (bounded) for the host to open the port."""
    LO, DONE = 9, 16
    GATE_WAIT = a['GATE_WAIT']
    return [
        0xA9BD7BFD,                                   #  0 stp x29,x30,[sp,#-0x30]!
        0x910003FD,                                   #  1 mov x29,sp
        0xA90153F3,                                   #  2 stp x19,x20,[sp,#0x10]
        0xF90013F5,                                   #  3 str x21,[sp,#0x20]
        A.adrp(20, va(GATE_WAIT + 4 * 4), a['PORTFLAG'] & ~0xFFF),
        A.add_imm(20, 20, a['PORTFLAG'] & 0xFFF),
        A.movz_x(21, WAIT_MS),
        A.bl_(va(GATE_WAIT + 7 * 4), a['GETTIMER']),
        A.mov_x(19, 0),                               #  8 mov x19,x0 (start)
        A.ldr_w_imm(0, 20, 0),                        #  9 loop: ldr w0,[x20]
        A.cbnz_w(0, DONE - 10),                       # 10 cbnz w0,done
        A.bl_(va(GATE_WAIT + 11 * 4), a['IRQ']),      # 11 bl irq
        A.bl_(va(GATE_WAIT + 12 * 4), a['GETTIMER']), # 12 bl get_timer
        A.sub_x(0, 0, 19),                            # 13 sub x0,x0,x19
        A.cmp_x(0, 21),                               # 14 cmp x0,x21
        A.b_cond(LO - 15, 3),                         # 15 b.lo loop
        0xA94153F3,                                   # 16 done: ldp x19,x20
        0xF94013F5,                                   # 17 ldr x21,[sp,#0x20]
        0xA8C37BFD,                                   # 18 ldp x29,x30,[sp],#0x30
        0xD65F03C0,                                   # 19 ret
    ]


def s2_code(a):
    """S2 trigger stub (shipped): save the format string, wait for the
    host, set gate = 1, tail-call printf."""
    S2 = a['S2']
    return [
        0xA9BE7BFD,                                   #  0 stp x29,x30,[sp,#-0x20]!
        0x910003FD,                                   #  1 mov x29,sp
        0xF9000BE0,                                   #  2 str x0,[sp,#0x10]
        A.bl_(va(S2 + 3 * 4), va(a['GATE_WAIT'])),    #  3 bl usb_gate_wait
        0xF9400BE0,                                   #  4 ldr x0,[sp,#0x10]
        0xA8C27BFD,                                   #  5 ldp x29,x30,[sp],#0x20
        A.adrp(1, va(S2 + 6 * 4), va(a['FLAG']) & ~0xFFF),
        A.add_imm(1, 1, va(a['FLAG']) & 0xFFF),
        0xD2800022,                                   #  8 mov w2,#1
        A.strb_imm(2, 1, 0),                          #  9 strb w2,[x1]
        A.b_(va(S2 + 10 * 4), a['PRINTF']),           # 10 b printf
    ]


def log2_code(a):
    """LOG2 (shipped): gate check, chunked send, one reply per <=63 bytes."""
    EPI, STRLEN, STRLEN_DONE, SEND, SEND_DONE = 34, 14, 19, 20, 32
    USE_REM, HAVE = 25, 26
    LOG2 = a['LOG2']
    V = lambda k: va(LOG2 + k * 4)
    return [
        0xA9BC7BFD,                                   #  0 stp x29,x30,[sp,#-0x40]!
        0x910003FD,                                   #  1 mov x29,sp
        0xA90153F3,                                   #  2 stp x19,x20,[sp,#0x10]
        0xA9025BF5,                                   #  3 stp x21,x22,[sp,#0x20]
        A.str_x_imm(23, 31, 0x30 // 8),               #  4 str x23,[sp,#0x30]
        A.adrp(1, V(5), va(a['FLAG'])),               #  5 adrp x1,gate
        A.add_imm(1, 1, va(a['FLAG']) & 0xFFF),       #  6 add x1,x1,#gate
        A.ldrb_imm(2, 1, 0),                          #  7 ldrb w2,[x1]
        A.cbz_w(2, EPI - 8),                          #  8 cbz w2,epilogue
        A.strb_imm(31, 1, 0),                         #  9 strb wzr,[x1]
        A.mov_x(23, 1),                               # 10 mov x23,x1
        A.mov_x(22, 0),                               # 11 mov x22,x0
        A.mov_x(19, 0),                               # 12 mov x19,x0
        0xD2800014,                                   # 13 mov x20,#0
        A.ldrb_imm(2, 19, 0),                         # 14 strlen: ldrb w2,[x19]
        A.cbz_w(2, STRLEN_DONE - 15),                 # 15 cbz w2,strlen_done
        A.add_imm(19, 19, 1),                         # 16 add x19,x19,#1
        A.add_imm(20, 20, 1),                         # 17 add x20,x20,#1
        A.b_(V(18), V(STRLEN)),                       # 18 b strlen
        A.mov_x(19, 22),                              # 19 strlen_done: x19 = buf
        A.cbz_x(20, SEND_DONE - 20),                  # 20 send: cbz x20,done
        A.movz_x(21, CHUNK),                          # 21 mov x21,#CHUNK
        A.subs_imm_x(31, 20, CHUNK),                  # 22 cmp x20,#CHUNK
        A.b_cond(USE_REM - 23, 9),                    # 23 b.ls use_rem
        A.b_(V(24), V(HAVE)),                         # 24 b have_len
        A.mov_x(21, 20),                              # 25 use_rem: mov x21,x20
        A.mov_x(0, 19),                               # 26 have_len: mov x0,x19
        A.mov_x(1, 21),                               # 27 mov x1,x21
        A.bl_(V(28), va(a['REPLY'])),                 # 28 bl reply_to_pctool
        A.add_x(19, 19, 21),                          # 29 add x19,x19,x21
        A.sub_x(20, 20, 21),                          # 30 sub x20,x20,x21
        A.b_(V(31), V(SEND)),                         # 31 b send
        0xD2800022,                                   # 32 send_done: mov w2,#1
        A.strb_imm(2, 23, 0),                         # 33 strb w2,[x23] restore gate
        A.ldr_x_imm(23, 31, 0x30 // 8),               # 34 epilogue: ldr x23,[sp,#0x30]
        0xA9425BF5,                                   # 35 ldp x21,x22,[sp,#0x20]
        0xA94153F3,                                   # 36 ldp x19,x20,[sp,#0x10]
        0xA8C47BFD,                                   # 37 ldp x29,x30,[sp],#0x40
        0xD65F03C0,                                   # 38 ret
    ]


def put(d, off, words, what, log):
    for i, w in enumerate(words):
        struct.pack_into('<I', d, off + i * 4, w)
    log('  %-34s file %#08x  %2d instrs' % (what, off, len(words)))


def build(base_bytes, a, dhtb30, log=print):
    """apply the usblog patch; returns the patched image bytes"""
    d = bytearray(base_bytes)

    if struct.unpack_from('<I', d, 0x30)[0] != dhtb30:
        raise BuildError('[0x30] mismatch: want %#x' % dhtb30)
    if A.w(d, a['PUTS']) != 0xA9BF7BFD:
        raise BuildError('puts prologue @%#x' % a['PUTS'])
    if A.w(d, a['TRIG']) != A.bl_(va(a['TRIG']), a['PRINTF']):
        raise BuildError('TRIG @%#x is not bl PRINTF' % a['TRIG'])
    if A.w(d, a['PUMP_CALL']) != A.bl_(va(a['PUMP_CALL']), a['STOCK_PUMP']):
        raise BuildError('PUMP_CALL @%#x is not bl STOCK_PUMP' % a['PUMP_CALL'])
    if A.w(d, a['RET1']) != 0x52800033:
        raise BuildError('RET1 @%#x is not mov w19,#1' % a['RET1'])
    if A.w(d, a['FORCE_PORT']) != 0x350001C0:
        raise BuildError('FORCE_PORT @%#x is not cbnz w0,+0x38' % a['FORCE_PORT'])

    log('patching %d bytes:' % len(d))

    # --- the console hook -------------------------------------------------
    struct.pack_into('<I', d, a['FLAG'], 0)
    put(d, a['S1'], s1_code(a), 'trigger stub S1 (vestigial)', log)
    put(d, a['LOG'], log_code(a), 'log fn LOG (vestigial)', log)
    put(d, a['HOOK'], hook_code(a), 'puts hook', log)
    struct.pack_into('<I', d, a['PUTS'], A.b_(va(a['PUTS']), va(a['HOOK'])))
    struct.pack_into('<I', d, a['TRIG'], A.bl_(va(a['TRIG']), va(a['S1'])))
    log('  %-34s file %#08x  -> hook' % ('puts entry', a['PUTS']))

    # --- never block, never wait for a tool -------------------------------
    put(d, a['PUMP'], pump_code(a), 'bounded pump', log)
    struct.pack_into('<I', d, a['PUMP_CALL'], A.bl_(va(a['PUMP_CALL']), va(a['PUMP'])))
    struct.pack_into('<I', d, a['RET1'], 0x52800013)      # mov w19,#0
    struct.pack_into('<I', d, a['FORCE_PORT'], 0x1400000E)  # b +0x38
    log('  %-34s file %#08x' % ('force channel allocation', a['FORCE_PORT']))
    log('  %-34s file %#08x' % ('no pctool command wait', a['RET1']))

    # --- hold the channel open until the host has opened the port ---------
    put(d, a['GATE_WAIT'], gate_wait_code(a), 'usb_gate_wait', log)
    put(d, a['S2'], s2_code(a), 'trigger stub S2', log)
    struct.pack_into('<I', d, a['TRIG'], A.bl_(va(a['TRIG']), va(a['S2'])))

    # --- the actual fix: one max packet per send --------------------------
    if d[a['FLAG']] != 0:
        raise BuildError('gate byte is not zero')
    if A.w(d, a['HOOK_BL']) != A.bl_(va(a['HOOK_BL']), va(a['LOG'])):
        raise BuildError('HOOK_BL @%#x is not bl LOG' % a['HOOK_BL'])
    put(d, a['LOG2'], log2_code(a), 'log fn LOG2 (CHUNK=%d)' % CHUNK, log)
    struct.pack_into('<I', d, a['HOOK_BL'], A.bl_(va(a['HOOK_BL']), va(a['LOG2'])))
    if a['LOG2'] + len(log2_code(a)) * 4 > a['DEAD_END']:
        raise BuildError('injected code overruns the dead function')
    return bytes(d)


def diff_ranges(x, y):
    n = min(len(x), len(y))
    rngs, i = [], 0
    while i < n:
        if x[i] != y[i]:
            j = i
            while j < n and x[j] != y[j]:
                j += 1
            rngs.append((i, j))
            i = j
        else:
            i += 1
    return rngs
