#!/usr/bin/env python3
"""展锐 uboot_log 分区解析：dump 每次开机的日志 slot"""
import struct, sys, re, os

fn = sys.argv[1] if len(sys.argv)>1 else '/root/uboot_log.bin'
d = open(fn,'rb').read()
magic, data_off, ver, n_ent = struct.unpack_from('<IIII', d, 0)
print(f'magic=0x{magic:04X} data_off=0x{data_off:X} ver={ver} entries={n_ent} size={len(d)/1024/1024:.1f}MB')
if magic != 0xABCD:
    print('!! magic 不匹配 (分区可能是空的/未初始化)')
out = os.path.dirname(os.path.abspath(fn)) or '.'
for i in range(n_ent):
    off, size, flag = struct.unpack_from('<III', d, 0x18 + i*12)
    if not off or not size or off+size > len(d): 
        print(f'[{i}] skip (off={off:#x} size={size:#x})'); continue
    raw = d[off:off+size]
    txt = re.sub(rb'[^\x20-\x7e\n]', b'.', raw).decode()
    p = os.path.join(out, f'slot{i}_@{off:#x}.log')
    open(p,'w').write(txt)
    banner = next((l for l in txt.split('\n') if 'U-Boot 2015' in l), '?')
    last  = [l for l in txt.strip().split('\n') if l.strip()][-1][:60]
    print(f'[{i}] off=0x{off:07X} size=0x{size:04X} ({size}B) flag=0x{flag:08X}')
    print(f'     banner: {banner.strip()}')
    print(f'     tail  : {last}')
    print(f'     -> {p}')
