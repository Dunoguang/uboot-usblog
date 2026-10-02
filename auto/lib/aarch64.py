"""AArch64 helpers: encoders for the injected code + lightweight decoders.

Encoders mirror the legacy per-device generators bit-for-bit.
Decoders are pure-python (no capstone/IDA needed).
"""
import struct

def w(d, off):
    return struct.unpack_from('<I', d, off)[0]

# ------------------------------------------------------------------ encoders
def b_(src, dst):  return 0x14000000 | (((dst - src) // 4) & 0x3FFFFFF)
def bl_(src, dst): return 0x94000000 | (((dst - src) // 4) & 0x3FFFFFF)

def adrp(rd, pc, target):
    off = (target & ~0xFFF) - (pc & ~0xFFF)
    imm = (off >> 12) & 0x1FFFFF
    return 0x90000000 | ((imm & 3) << 29) | ((imm >> 2) << 5) | rd

def add_imm(rd, rn, imm):    return 0x91000000 | ((imm & 0xFFF) << 10) | (rn << 5) | rd
def add_x(rd, rn, rm):       return 0x8B000000 | (rm << 16) | (rn << 5) | rd
def sub_x(rd, rn, rm):       return 0xCB000000 | (rm << 16) | (rn << 5) | rd
def cmp_x(rn, rm):           return 0xEB000000 | (rm << 16) | (rn << 5) | 31
def subs_w_imm(rd, rn, imm): return 0x71000000 | ((imm & 0xFFF) << 10) | (rn << 5) | rd
def subs_imm_x(rd, rn, imm): return 0xF1000000 | ((imm & 0xFFF) << 10) | (rn << 5) | rd
def orr_x(rd, rn, rm):       return 0xAA000000 | (rm << 16) | (rn << 5) | rd
def movz_w(rd, imm16):       return 0x52800000 | ((imm16 & 0xFFFF) << 5) | rd
def movz_x(rd, imm16):       return 0xD2800000 | ((imm16 & 0xFFFF) << 5) | rd
def ldr_w_imm(rt, rn, i):    return 0xB9400000 | ((i & 0xFFF) << 10) | (rn << 5) | rt
def ldr_x_imm(rt, rn, i):    return 0xF9400000 | ((i & 0xFFF) << 10) | (rn << 5) | rt
def str_w_imm(rt, rn, i):    return 0xB9000000 | ((i & 0xFFF) << 10) | (rn << 5) | rt
def str_x_imm(rt, rn, i):    return 0xF9000000 | ((i & 0xFFF) << 10) | (rn << 5) | rt
def ldrb_imm(rt, rn, i):     return 0x39400000 | ((i & 0xFFF) << 10) | (rn << 5) | rt
def strb_imm(rt, rn, i):     return 0x39000000 | ((i & 0xFFF) << 10) | (rn << 5) | rt
def cbz_w(rt, delta):        return 0x34000000 | ((delta & 0x7FFFF) << 5) | rt
def cbz_x(rt, delta):        return 0xB4000000 | ((delta & 0x7FFFF) << 5) | rt
def cbnz_w(rt, delta):       return 0x35000000 | ((delta & 0x7FFFF) << 5) | rt
def b_ne(delta):             return 0x54000000 | ((delta & 0x7FFFF) << 5) | 0x1
def b_cond(delta, cond):     return 0x54000000 | ((delta & 0x7FFFF) << 5) | cond
def mov_x(rd, rm):           return orr_x(rd, 31, rm)

# ------------------------------------------------------------------ decoders
def sign_ext(v, bits):
    if v & (1 << (bits - 1)):
        return v - (1 << bits)
    return v

def bl_target(d, off, base):
    """target VA of a bl at file offset off, or None"""
    v = w(d, off)
    if (v >> 26) != 0x25:
        return None
    return base + off + sign_ext(v & 0x3FFFFFF, 26) * 4

def dec_pair(d, off, base):
    """decode an adrp+add pair at off; return target VA or None"""
    w1 = w(d, off)
    if (w1 & 0x9F000000) != 0x90000000:
        return None
    w2 = w(d, off + 4)
    if (w2 & 0xFF800000) != 0x91000000:
        return None
    if (w1 & 0x1F) != ((w2 >> 5) & 0x1F):
        return None
    sh = (w2 >> 22) & 3
    if sh not in (0, 1):
        return None
    immlo = (w1 >> 29) & 3
    immhi = (w1 >> 5) & 0x7FFFF
    imm = sign_ext((immhi << 2) | immlo, 21)
    pc = off + base
    addimm = (w2 >> 10) & 0xFFF
    if sh == 1:
        addimm <<= 12
    return ((pc & ~0xFFF) + (imm << 12)) + addimm

def branch_target(d, off, base):
    """target VA of b/bl/cbz/cbnz/b.cond/tbz/tbnz at off, or None"""
    v = w(d, off)
    if (v & 0xFC000000) in (0x14000000, 0x94000000):          # b / bl
        return base + off + sign_ext(v & 0x3FFFFFF, 26) * 4
    if (v & 0x7E000000) in (0x34000000, 0x35000000):          # cbz / cbnz
        return base + off + sign_ext((v >> 5) & 0x7FFFF, 19) * 4
    if (v & 0xFF000010) == 0x54000000:                        # b.cond
        return base + off + sign_ext((v >> 5) & 0x7FFFF, 19) * 4
    if (v & 0x7E000000) in (0x36000000, 0x37000000):          # tbz / tbnz
        return base + off + sign_ext((v >> 5) & 0x3FFF, 14) * 4
    return None

def is_wild(v):
    """True if the word's immediate depends on build layout (wildcard it)"""
    if (v & 0xFC000000) in (0x14000000, 0x94000000):  return True   # b / bl
    if (v & 0x7E000000) in (0x34000000, 0x35000000):  return True   # cbz / cbnz
    if (v & 0xFF000010) == 0x54000000:                return True   # b.cond
    if (v & 0x7E000000) in (0x36000000, 0x37000000):  return True   # tbz / tbnz
    if (v & 0x9F000000) in (0x10000000, 0x90000000):  return True   # adr / adrp
    if (v & 0x3B000000) == 0x18000000:                return True   # ldr literal
    return False
