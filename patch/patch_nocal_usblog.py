#!/usr/bin/env python3
# uboot 校准砍除 + USB 日志补丁 v3（基线=uboot-usblog.img）
# v3: 所有追加区指令程序化编码(v2 手算 ADD/STRB 寄存器错位)
import struct

SRC = "/root/github/firmware/uboot-usblog.img"
DST = "/root/github/vp19-analysis/uboot-nocal-usblog.img"
data = bytearray(open(SRC,'rb').read())

def bl(src,dst): return 0x94000000 | (((dst-src)//4) & 0x3FFFFFF)
def b(src,dst):  return 0x14000000 | (((dst-src)//4) & 0x3FFFFFF)
def adrp(rd, pc, target):
    off = (target & ~0xFFF) - (pc & ~0xFFF)
    imm = off >> 12
    immlo, immhi = imm & 3, (imm >> 2) & 0x7FFFF
    return 0x90000000 | (immlo<<29) | (immhi<<5) | rd
def add_imm(rd,rn,imm): return 0x91000000 | ((imm&0xFFF)<<10) | (rn<<5) | rd
def movz_w(rd,imm):     return 0x52800000 | ((imm&0xFFFF)<<5) | rd
def strb_imm(rt,rn,imm=0): return 0x39000000 | ((imm&0xFFF)<<10) | (rn<<5) | rt
NOP, RET = 0xD503201F, 0xD65F03C0

FLAG = 0x763A4
def put(off, *insns):
    for i,v in enumerate(insns):
        struct.pack_into('<I', data, off+i*4, v)

# --- sub_1AA40 (normal 插线路径) ---
put(0x1AA6C, 0x340003E0)   # 恢复 CBZ: AON 0xA0 未插线 → 退出
put(0x1AA80, 0x34000340)   # 恢复 CBZ: fdt fixup 返回值检查
put(0x1AAF0, bl(0x1AAF0, 0x763A8))  # BL usb_init_and_flag stub
put(0x1AAF4, b(0x1AAF4, 0x1AAE8))   # B exit (砍校准循环)
put(0x1A318, b(0x1A318, 0x1AAE8))   # B exit (砍 AON bit3 校准分支)

# --- 教室电脑线: 砍 pctool/NV ---
put(0x17FDC, movz_w(0,1), RET)      # MOV W0,#1; RET
put(0x17B0C, movz_w(0,1), RET)

# --- 追加区 stub @0x763A8: SMC init + flag=1 ---
put(0x763A8, bl(0x763A8, 0x1B328),           # BL sub_1B328 (SMC USB init)
            adrp(1, 0x763AC, FLAG),           # ADRP X1, flag页
            add_imm(1, 1, FLAG & 0xFFF),      # ADD X1,X1,#0x3A4
            movz_w(2, 1),                     # MOV W2,#1
            strb_imm(2, 1),                   # STRB W2,[X1] -> flag=1
            RET,                              # -> 0x1AAF4
            NOP)                              # 0x763C0
# 0x763C4 usb_log_puts / 0x76408 puts_hook / 0x2C98 / DHTB 沿用 usblog

open(DST,'wb').write(bytes(data))
d = open(DST,'rb').read()
chk = {
 0x1AA6C:(0x340003E0,'CBZ AON'), 0x1AA80:(0x34000340,'CBZ fdt'),
 0x1AAF0:(bl(0x1AAF0,0x763A8),'BL stub'), 0x1AAF4:(b(0x1AAF4,0x1AAE8),'B exit'),
 0x1A318:(b(0x1A318,0x1AAE8),'B exit'),
 0x17FDC:(movz_w(0,1),'MOV W0,1'), 0x17FE0:(RET,'RET'),
 0x17B0C:(movz_w(0,1),'MOV W0,1'), 0x17B10:(RET,'RET'),
 0x2C98:(0xD2802280,'原样'), 0x2C9C:(0xF2A805C0,'原样'), 0x30:(0x00076024,'DHTB'),
 0xE798:(0x14019F1C,'B puts_hook'),
 0x763A8:(bl(0x763A8,0x1B328),'BL 1B328'),
 0x763AC:(adrp(1,0x763AC,FLAG),'ADRP flag'),
 0x763B0:(add_imm(1,1,0x3A4),'ADD flag'),
 0x763B4:(movz_w(2,1),'MOV W2,1'),
 0x763B8:(strb_imm(2,1),'STRB flag'),
 0x763BC:(RET,'RET'), 0x763C0:(NOP,'NOP'),
 0x763C4:(0xA9BD7BFD,'log_puts帧'), 0x76408:(0xA9BF7BFD,'hook STP'),
}
ok=True
for off,(exp,desc) in sorted(chk.items()):
    got=struct.unpack_from('<I',d,off)[0]
    s='OK ' if got==exp else 'FAIL'
    if got!=exp: ok=False
    print('%08X %08X %s %s'%(off,got,s,desc))
print('===> ALL PASS' if ok else '===> HAS FAIL','size=%d'%len(d))
