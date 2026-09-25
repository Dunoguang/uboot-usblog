#!/usr/bin/env python3
"""
DW99/vp19 uboot USB 日志读取器（libusb-1.0 via ctypes，无第三方依赖）
设备侧: puts hook -> gserial 环形缓冲(8KB) -> 异步提交 -> EP 0x85 (厂商类 0xff)
用法:
    sudo python3 uboot_usb_log_reader.py                 # 读日志到 stdout + 文件
    sudo python3 uboot_usb_log_reader.py -o my.log       # 指定输出文件
    sudo python3 uboot_usb_log_reader.py --raw           # 同时保存原始字节流
要点:
    必须在手表开机【之前】启动本脚本（uboot 阶段 USB 才存在，跳内核即断开）
    设备断开会自动等待重新枚举（可反复开机抓多次）
"""
import ctypes, sys, time, argparse, os, signal

VID, PID = 0x1782, 0x4D00
EP_IN    = 0x85          # EP5 IN（bulk）
IFACE    = 0             # 厂商类接口
TIMEOUT  = 100           # ms per bulk_transfer (短超时以便检测断连/退出)

lib = ctypes.CDLL("libusb-1.0.so.0")

# ---- libusb 原型 ----
lib.libusb_init.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
lib.libusb_init.restype  = ctypes.c_int
lib.libusb_exit.argtypes = [ctypes.c_void_p]
lib.libusb_open_device_with_vid_pid.argtypes = [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_uint16]
lib.libusb_open_device_with_vid_pid.restype  = ctypes.c_void_p
lib.libusb_set_auto_detach_kernel_driver.argtypes = [ctypes.c_void_p, ctypes.c_int]
lib.libusb_claim_interface.argtypes = [ctypes.c_void_p, ctypes.c_int]
lib.libusb_claim_interface.restype  = ctypes.c_int
lib.libusb_release_interface.argtypes = [ctypes.c_void_p, ctypes.c_int]
lib.libusb_bulk_transfer.argtypes = [ctypes.c_void_p, ctypes.c_ubyte,
                                     ctypes.c_void_p, ctypes.c_int,
                                     ctypes.POINTER(ctypes.c_int), ctypes.c_uint]
lib.libusb_bulk_transfer.restype  = ctypes.c_int
lib.libusb_close.argtypes = [ctypes.c_void_p]

LIBUSB_ERROR_TIMEOUT    = -7
LIBUSB_ERROR_NO_DEVICE  = -4
LIBUSB_ERROR_IO         = -1
LIBUSB_ERROR_PIPE       = -9

def err_name(r):
    return {0:"OK",-1:"IO",-2:"INVALID_PARAM",-3:"ACCESS",-4:"NO_DEVICE",
            -5:"NOT_FOUND",-6:"BUSY",-7:"TIMEOUT",-9:"PIPE",-11:"INTERRUPTED"}.get(r,f"ERR{r}")

running = True
def on_sigint(*_):
    global running; running = False
signal.signal(signal.SIGINT, on_sigint)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-o","--output", default=None, help="日志输出文件 (默认 uboot_usb_log_<ts>.log)")
    ap.add_argument("--raw", action="store_true", help="同时保存原始字节流")
    ap.add_argument("--once", action="store_true", help="抓到一个设备会话即退出（默认循环等待）")
    args = ap.parse_args()

    out_path = args.output or f"uboot_usb_log_{time.strftime('%m%d_%H%M%S')}.log"
    raw_path = out_path + ".raw" if args.raw else None
    fout = open(out_path, "wb", buffering=0)
    fraw = open(raw_path, "wb", buffering=0) if raw_path else None

    ctx = ctypes.c_void_p()
    if lib.libusb_init(ctypes.byref(ctx)) != 0:
        print("libusb_init failed"); sys.exit(1)

    total = 0; sessions = 0
    print(f"[reader] 等待设备 {VID:04x}:{PID:04x} @ EP 0x{EP_IN:02x} ... (输出 -> {out_path})")
    print(f"[reader] 现在给手表上电/开机；ubooot 阶段会被抓到，跳内核时自动断开\n")

    try:
        while running:
            h = lib.libusb_open_device_with_vid_pid(ctx, VID, PID)
            if not h:
                time.sleep(0.15); continue
            sessions += 1
            # 内核驱动可能占着 vendor 接口，自动 detach
            lib.libusb_set_auto_detach_kernel_driver(h, 1)
            r = lib.libusb_claim_interface(h, IFACE)
            if r != 0:
                print(f"[reader] claim interface {IFACE} 失败: {err_name(r)}")
                lib.libusb_close(h); time.sleep(0.5); continue
            print(f"[reader] 设备已连接 (session #{sessions})，开始读取 ...")
            got = 0
            buf = ctypes.create_string_buffer(4096)
            while running:
                xfer = ctypes.c_int(0)
                r = lib.libusb_bulk_transfer(h, EP_IN, buf, 4096, ctypes.byref(xfer), TIMEOUT)
                if r == 0 and xfer.value > 0:
                    data = buf.raw[:xfer.value]
                    fout.write(data); fraw and fraw.write(data)
                    got += xfer.value; total += xfer.value
                    sys.stdout.buffer.write(data); sys.stdout.buffer.flush()
                elif r == LIBUSB_ERROR_TIMEOUT:
                    continue
                elif r in (LIBUSB_ERROR_NO_DEVICE, LIBUSB_ERROR_IO, LIBUSB_ERROR_PIPE):
                    print(f"\n[reader] 设备断开 ({err_name(r)})，本次收到 {got} 字节，总 {total} 字节")
                    break
                else:
                    print(f"[reader] bulk_transfer: {err_name(r)}")
                    break
            lib.libusb_release_interface(h, IFACE)
            lib.libusb_close(h)
            if args.once and got > 0:
                break
    finally:
        fout.close(); fraw and fraw.close()
        lib.libusb_exit(ctx)
        print(f"\n[reader] 退出。共 {sessions} 个会话，{total} 字节 -> {out_path}")
        if raw_path: print(f"[reader] raw -> {raw_path}")

if __name__ == "__main__":
    main()
