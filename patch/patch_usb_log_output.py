#!/usr/bin/env python3
"""
USB 日志输出补丁 - 第二阶段
在已清空的 sub_17FDC 区域添加 USB 日志输出代码
"""

import shutil
import struct

SRC = "/root/vp19-analysis/uboot-patched.img"
DST = "/root/vp19-analysis/uboot-usb-log.img"

# ARM64 指令编码
NOP = 0xD503201F
RET = 0xD65F03C0
MOV_W0_1 = 0x52800020

# 预计算的指令
STRB_W0_X1      = 0x39000020    # STRB W0, [X1]
ADRP_X2_0x18000 = 0xB0000002    # ADRP X2, 0x18000 (from 0x17FE8)
LDR_W3_X2_20    = 0xB9402043    # LDR W3, [X2, #0x20]  (head, imm12=8=0x20/4)
LDR_W4_X2_24    = 0xB9402444    # LDR W4, [X2, #0x24]  (tail, imm12=9=0x24/4)
ADD_W5_W3_1     = 0x11000465    # ADD W5, W3, #1
AND_W5_0x1F     = 0x53001065    # UBFX W5, W3, #0, #5 (≡ W5 = W3 & 0x1F)
CMP_W5_W4       = 0x6B0400BF    # CMP W5, W4
B_EQ_0x1800C    = 0x54000060    # B.EQ 0x1800C (imm19=3)
STRB_W0_X2_X3   = 0x38236840    # STRB W0, [X2, X3]
STR_W5_X2_20    = 0xB9002045    # STR W5, [X2, #0x20]  (imm12=8=0x20/4)
B_0x2B2F8        = 0x14004CBB    # B 0x2B2F8

# 修改 sub_2B2D8: STRB W0, [X1] → B usb_log_putc
# B 从 0x2B2F4 到 0x17FE4
B_TO_USB_LOG    = 0x17FFB33C    # B 0x17FE4 (from 0x2B2F4)

def patch_bytes(data, offset, values):
    for i, val in enumerate(values):
        struct.pack_into('<I', data, offset + i * 4, val)

# 备份
print(f"备份 {SRC} → {SRC}.bak2")
shutil.copy2(SRC, SRC + ".bak2")

# 读取
with open(SRC, 'rb') as f:
    data = bytearray(f.read())

print(f"文件大小: {len(data)} bytes")

# ===== 写入 usb_log_putc @ 0x17FE4 =====
print(f"\n[写入] usb_log_putc @ 0x17FE4")

usb_log_putc_code = [
    STRB_W0_X1,       # 0x17FE4: STRB W0, [X1]           (原始操作)
    ADRP_X2_0x18000,  # 0x17FE8: ADRP X2, 0x18000
    LDR_W3_X2_20,     # 0x17FEC: LDR W3, [X2, #0x20]     (head)
    LDR_W4_X2_24,     # 0x17FF0: LDR W4, [X2, #0x24]     (tail)
    ADD_W5_W3_1,      # 0x17FF4: ADD W5, W3, #1
    AND_W5_0x1F,      # 0x17FF8: UBFX W5, W3, #0, #5  ((head+1)%32)
    CMP_W5_W4,        # 0x17FFC: CMP W5, W4              (检查是否满)
    B_EQ_0x1800C,     # 0x18000: B.EQ 0x1800C            (满则跳过写入)
    STRB_W0_X2_X3,    # 0x18004: STRB W0, [X2, X3]       (buf[head]=char)
    STR_W5_X2_20,     # 0x18008: STR W5, [X2, #0x20]     (更新head)
    B_0x2B2F8,        # 0x1800C: B 0x2B2F8                (返回)
]

patch_bytes(data, 0x17FE4, usb_log_putc_code)
print(f"  写入 {len(usb_log_putc_code)} 条指令 ({len(usb_log_putc_code)*4} bytes)")

# 剩余填 NOP (0x18010 - 0x1825B)
nop_area_start = 0x17FE4 + len(usb_log_putc_code) * 4
nop_area_end = 0x1825C
nop_count = (nop_area_end - nop_area_start) // 4
print(f"  NOP 填充: 0x{nop_area_start:06X} - 0x{nop_area_end:06X} ({nop_count} NOPs)")
for i in range(nop_count):
    struct.pack_into('<I', data, nop_area_start + i * 4, NOP)

# ===== 环形缓冲区初始化 @ 0x18020 =====
# head 和 tail 初始化为 0 (已经在二进制中是 0, 因为原先是 NOP)
print(f"\n[环形缓冲区] buf @ 0x18000 (32 bytes), head @ 0x18020, tail @ 0x18024")
# 确保 head/tail 为 0
struct.pack_into('<I', data, 0x18020, 0)  # head = 0
struct.pack_into('<I', data, 0x18024, 0)  # tail = 0

# ===== 修改 sub_2B2D8: STRB → B usb_log_putc =====
print(f"\n[修改] sub_2B2D8 @ 0x2B2F4: STRB W0, [X1] → B usb_log_putc")
original = struct.unpack_from('<I', data, 0x2B2F4)[0]
print(f"  原始: 0x{original:08X} (STRB W0, [X1])")
struct.pack_into('<I', data, 0x2B2F4, B_TO_USB_LOG)
new = struct.unpack_from('<I', data, 0x2B2F4)[0]
print(f"  新值: 0x{new:08X} (B 0x17FE4)")

# ===== 验证 =====
print(f"\n=== 验证 ===")

# 验证 usb_log_putc
print(f"usb_log_putc @ 0x17FE4:")
for i in range(len(usb_log_putc_code)):
    insn = struct.unpack_from('<I', data, 0x17FE4 + i*4)[0]
    print(f"  0x{0x17FE4+i*4:06X}: {insn:08X}")

# 验证 sub_2B2D8 修改
print(f"\nsub_2B2D8 关键指令:")
for i in range(0x2B2D8, 0x2B308, 4):
    insn = struct.unpack_from('<I', data, i)[0]
    marker = " ← 已修改" if i == 0x2B2F4 else ""
    print(f"  0x{i:06X}: {insn:08X}{marker}")

# 验证缓冲区
print(f"\n环形缓冲区:")
head = struct.unpack_from('<I', data, 0x18020)[0]
tail = struct.unpack_from('<I', data, 0x18024)[0]
print(f"  head @ 0x18020: {head}")
print(f"  tail @ 0x18024: {tail}")

# 写入
with open(DST, 'wb') as f:
    f.write(data)

print(f"\n✓ 补丁完成: {DST}")
print(f"  原始文件: {SRC}")
print(f"  补丁文件: {DST}")
