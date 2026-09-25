#!/usr/bin/env python3
"""
U-Boot USB 日志输出补丁
校准模式替换为跳过，为 USB 日志输出腾出空间

修改内容:
1. sub_17FDC (0x17FDC, 640 bytes) → MOV W0, #1; RET; NOP×158
2. sub_17B0C (0x17B0C, 104 bytes) → MOV W0, #1; RET; NOP×24
3. sub_2C98  (0x2C98,  8 bytes)  → MOV X0, #0; RET
"""

import shutil
import struct

SRC = "/root/vp19-analysis/uboot.img"
DST = "/root/vp19-analysis/uboot-patched.img"

# ARM64 指令编码
MOV_W0_1  = 0x52800020  # MOV W0, #1
MOV_X0_0  = 0xD2800000  # MOV X0, #0
RET       = 0xD65F03C0  # RET
NOP       = 0xD503201F  # NOP

def patch_function(data, offset, size, insns):
    """用指令列表替换指定区域，剩余填 NOP"""
    expected_size = size // 4
    if len(insns) > expected_size:
        raise ValueError(f"Instructions ({len(insns)}) exceed size ({expected_size})")
    
    nop_count = expected_size - len(insns)
    all_insns = insns + [NOP] * nop_count
    
    for i, insn in enumerate(all_insns):
        struct.pack_into('<I', data, offset + i * 4, insn)
    
    return nop_count

def verify_patch(data, offset, size, expected_first):
    """验证补丁是否正确写入"""
    for i in range(min(len(expected_first), size // 4)):
        insn = struct.unpack_from('<I', data, offset + i * 4)[0]
        if insn != expected_first[i]:
            return False, i, insn, expected_first[i]
    return True, 0, 0, 0

# 备份原文件
print(f"备份 {SRC} → {SRC}.bak")
shutil.copy2(SRC, SRC + ".bak")

# 读取
with open(SRC, 'rb') as f:
    data = bytearray(f.read())

print(f"文件大小: {len(data)} bytes (0x{len(data):X})")

# ===== 补丁 1: sub_17FDC (0x17FDC, 640 bytes) =====
print(f"\n[补丁 1] sub_17FDC @ 0x17FDC (640 bytes)")
nop1 = patch_function(data, 0x17FDC, 640, [MOV_W0_1, RET])
print(f"  MOV W0, #1 + RET + NOP×{nop1}")

ok, idx, got, exp = verify_patch(data, 0x17FDC, 640, [MOV_W0_1, RET])
if ok:
    print(f"  ✓ 验证通过")
else:
    print(f"  ✗ 验证失败: 位置 {idx}, 得到 0x{got:08X}, 期望 0x{exp:08X}")

# ===== 补丁 2: sub_17B0C (0x17B0C, 104 bytes) =====
print(f"\n[补丁 2] sub_17B0C @ 0x17B0C (104 bytes)")
nop2 = patch_function(data, 0x17B0C, 104, [MOV_W0_1, RET])
print(f"  MOV W0, #1 + RET + NOP×{nop2}")

ok, idx, got, exp = verify_patch(data, 0x17B0C, 104, [MOV_W0_1, RET])
if ok:
    print(f"  ✓ 验证通过")
else:
    print(f"  ✗ 验证失败: 位置 {idx}, 得到 0x{got:08X}, 期望 0x{exp:08X}")

# ===== 补丁 3: sub_2C98 (0x2C98, 8 bytes) =====
print(f"\n[补丁 3] sub_2C98 @ 0x2C98 (8 bytes)")
nop3 = patch_function(data, 0x2C98, 8, [MOV_X0_0, RET])
print(f"  MOV X0, #0 + RET")

ok, idx, got, exp = verify_patch(data, 0x2C98, 8, [MOV_X0_0, RET])
if ok:
    print(f"  ✓ 验证通过")
else:
    print(f"  ✗ 验证失败: 位置 {idx}, 得到 0x{got:08X}, 期望 0x{exp:08X}")

# 写入
with open(DST, 'wb') as f:
    f.write(data)

print(f"\n✓ 补丁完成: {DST}")
print(f"  原始文件: {SRC}.bak")
print(f"  补丁文件: {DST}")
