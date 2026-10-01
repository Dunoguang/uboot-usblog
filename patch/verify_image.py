#!/usr/bin/env python3
"""Check a built image before you flash it.

    python3 patch/verify_image.py images/uboot-0.0.1.img

Disassembles every injected block, resolves each branch target back to a file
offset, and checks the single-word patches against their expected originals.
Needs `capstone` (`pip install capstone`); without it the structural checks
still run.

This exists because two mistakes in this patch are completely silent:

  * `str`/`ldr` unsigned-immediate forms take BYTES/8 in imm12.  Writing
    `str x23,[sp,#0x30]` as imm12 = 0x30 emits `[sp,#0x180]` and corrupts the
    stack frame.
  * the gate address is `va(FLAG) & 0xFFF` = 0xA34, the VA's low 12 bits.
    Using the file offset's low bits (0xC34) lands 0x200 past the byte the
    trigger stub sets; the log fn then never sees an open gate and the image
    emits no output at all.
"""
import hashlib
import os
import struct
import sys

BASE = 0x9EFFFE00
FLAG, S1, LOG, HOOK = 0x1BC34, 0x1BC38, 0x1BC4C, 0x1BC9C
PUMP, GATE_WAIT, S2, LOG2, DEAD_END = 0x1BCC0, 0x1BD00, 0x1BD50, 0x1BD80, 0x1BED8
TRIG, FORCE_PORT, RET1, PUMP_CALL, HOOK_BL, PUTS, PUTS_BODY = \
    0x1A768, 0x1A720, 0x1A764, 0x1A8F0, 0x1BCA8, 0xE798, 0xE79C
OUT_MD5 = 'aa780e3b519d59aa65979def9b83eb44'
PRINTF, GETTIMER, IRQ, REPLY = 0x9F00E5D8, 0x9F00184C, 0x9F030CA0, 0x1A8BC

BLOCKS = [('bounded pump', PUMP, 15), ('usb_gate_wait', GATE_WAIT, 20),
          ('trigger stub S2', S2, 11), ('log fn LOG2', LOG2, 39),
          ('puts hook', HOOK, 7)]


def va(f):
    return BASE + f


def v2f(v):
    return (v - 0x9F000000) + 0x200


def main():
    p = sys.argv[1] if len(sys.argv) > 1 else \
        os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'images', 'uboot-0.0.1.img')
    d = open(p, 'rb').read()
    md5 = hashlib.md5(d).hexdigest()
    bad = 0

    print('%s\n  %d bytes   md5 %s' % (p, len(d), md5))
    if len(d) != 484260:
        print('  FAIL length must be 484260'); bad += 1
    if struct.unpack_from('<I', d, 0x30)[0] != 0x75EF0:
        print('  FAIL [0x30] must stay 0x75EF0 (vboot sechdr base)'); bad += 1
    if md5 != OUT_MD5:
        print('  note md5 differs from the released image %s' % OUT_MD5)

    print('\nsingle-word patches')
    checks = [
        (PUTS,       0x14000000, 'puts entry -> b hook (any b)'),
        (TRIG,       0x94000000, 'printf call site -> bl <stub> (any bl)'),
        (FORCE_PORT, 0x1400000E, 'cbnz w0,+0x38 -> b +0x38'),
        (RET1,       0x52800013, 'mov w19,#1 -> mov w19,#0'),
        (PUMP_CALL,  0x94000000, 'reply_to_pctool pump call -> bl <bounded> (any bl)'),
        (HOOK_BL,    0x94000000, 'hook call -> bl <log fn> (any bl)'),
    ]
    for off, mask, what in checks:
        w = struct.unpack_from('<I', d, off)[0]
        ok = (w & 0xFC000000) == mask if mask in (0x14000000, 0x94000000) else w == mask
        print('  %s  file %#08x  %#010x  %s' % ('ok  ' if ok else 'FAIL', off, w, what))
        bad += 0 if ok else 1

    print('\ngate address in every adrp/add pair must be %#x (VA low 12 bits)'
          % (va(FLAG) & 0xFFF))
    want = (va(FLAG) & 0xFFF)
    for off in (S1 + 4, LOG + 0x10, S2 + 0x1C, LOG2 + 0x18):
        w = struct.unpack_from('<I', d, off)[0]
        imm = (w >> 10) & 0xFFF
        ok = (w & 0xFFC00000) == 0x91000000 and imm == want
        print('  %s  file %#08x  add ...#%#x' % ('ok  ' if ok else 'FAIL', off, imm))
        bad += 0 if ok else 1
    if d[FLAG] != 0:
        print('  FAIL gate byte is not zero'); bad += 1

    try:
        from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
        from capstone.arm64 import ARM64_OP_IMM
    except ImportError:
        print('\ncapstone not installed - skipping the disassembly walk')
        print('\n%s' % ('FAILURES: %d' % bad if bad else 'all structural checks passed'))
        return 1 if bad else 0

    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    md.detail = True
    print('\ndisassembly')
    for name, off, n in BLOCKS:
        end = off + n * 4
        if end > DEAD_END:
            print('  FAIL %s overruns the dead function' % name); bad += 1; continue
        print('  --- %s @ file %#08x ---' % (name, off))
        for ins in md.disasm(d[off:end], va(off)):
            tgt = ''
            if ins.operands and ins.operands[0].type == ARM64_OP_IMM and \
                    ins.mnemonic.split('.')[0] in ('b', 'bl', 'cbz', 'cbnz'):
                t = ins.operands[0].imm
                # every legitimate target: inside the injected block, or one of
                # the five u-boot functions the patch is allowed to call
                inside = (va(FLAG) <= t < va(DEAD_END)) or t in (
                    PRINTF, GETTIMER, IRQ, va(PUTS_BODY),
                ) or v2f(t) == REPLY
                tgt = '  -> file %#x%s' % (v2f(t), '' if inside else '   <-- OUTSIDE the expected targets')
                if not inside:
                    bad += 1
            print('    %#08x  %-8s %s%s' % (ins.address, ins.mnemonic, ins.op_str, tgt))

    print('\n%s' % ('FAILURES: %d' % bad if bad else 'OK - image looks correct'))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())

