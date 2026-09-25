#!/usr/bin/env python3
# v4: 在 v3 基础上补 USB 事件 poll（修复 in-flight 泄漏导致日志卡死）
# 依据: sub_1A8BC = BL 0x2D1D0(写+kick) + BL 0x2D2F0(poll)  —— 我们漏了 poll
import struct
SRC = "/root/github/vp19-analysis/uboot-nocal-usblog.img"
DST = "/root/github/vp19-analysis/uboot-nocal-usblog-v4.img"
data = bytearray(open(SRC,'rb').read())
def bl(src,dst): return 0x94000000 | (((dst-src)//4) & 0x3FFFFFF)
def put(off,*ins):
    for i,v in enumerate(ins): struct.pack_into('<I',data,off+i*4,v)

# 1) stub: SMC init -> flag=1 -> poll -> RET   (原: flag=1 -> RET -> NOP)
put(0x763BC, bl(0x763BC, 0x30EA0), 0xD65F03C0)
# 2) usb_log_puts 尾部: [发送] [poll] [返回]
put(0x763F8, bl(0x763F8, 0x2D1D0),   # BL 0x2D1D0 (X0=buf,W1=len 仍然有效)
              bl(0x763FC, 0x30EA0))  # BL 0x30EA0 (poll, 未就绪返回-22)

open(DST,'wb').write(bytes(data))
d = open(DST,'rb').read()
chk = {
 0x1AAF0:(bl(0x1AAF0,0x763A8),'BL stub'),
 0x1AAF4:(0x17FFFFFD,'B exit'),
 0x17FDC:(0x52800020,'MOV W0,1'), 0x17B0C:(0x52800020,'MOV W0,1'),
 0x1A318:(0x140001F4,'B exit'),
 0x763A8:(bl(0x763A8,0x1B328),'SMC init'),
 0x763BC:(bl(0x763BC,0x30EA0),'stub poll'),
 0x763C0:(0xD65F03C0,'RET'),
 0x763F8:(bl(0x763F8,0x2D1D0),'send'),
 0x763FC:(bl(0x763FC,0x30EA0),'poll'),
 0x76400:(0xA8C37BFD,'LDP'),
 0x76404:(0xD65F03C0,'RET'),
 0xE798:(0x14019F1C,'B puts_hook'),
}
ok=True
for off,(exp,desc) in sorted(chk.items()):
    got=struct.unpack_from('<I',d,off)[0]
    s='OK ' if got==exp else 'FAIL'
    if got!=exp: ok=False
    print('%08X %08X %s %s'%(off,got,s,desc))
print('===> ALL PASS' if ok else '===> HAS FAIL','size=%d'%len(d))
