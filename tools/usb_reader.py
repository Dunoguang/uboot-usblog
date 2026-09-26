#!/usr/bin/env python3
"""uboot-usblog host-side reader (libusb-1.0 via ctypes, no deps).

Usage:  python3 usb_reader.py [seconds] [out.bin]
Default: 3600 seconds, out = ./usblog.bin

Only accepts 1782:4d00 with bcdDevice==0x2416 (u-boot "Gadget Serial").
After claiming the interface it sends SET_CONTROL_LINE_STATE(DTR/RTS)
and SET_LINE_CODING, then polls bulk EP5-IN (0x85) and fsyncs every
received chunk to the output file.

IMPORTANT (measured 2026-09-26):
  The device side MUST use an image that forces the 8KB gserial
  channel allocation (images/uboot-v26-forceport.img or later).
  Otherwise u-boot takes the "usb calibrate port open timeout" path,
  the channel buffer is never allocated, and no host-side reader can
  get any byte (this is why v1..v25 never produced data).
"""
import ctypes, ctypes.util, time, sys, os

VID, PID, EP_IN, BCD_U = 0x1782, 0x4D00, 0x85, 0x2416

class DD(ctypes.Structure):
    _fields_ = [('bLength', ctypes.c_uint8),
                ('bDescriptorType', ctypes.c_uint8),
                ('bcdUSB', ctypes.c_uint16),
                ('bDeviceClass', ctypes.c_uint8),
                ('bDeviceSubClass', ctypes.c_uint8),
                ('bDeviceProtocol', ctypes.c_uint8),
                ('bMaxPacketSize0', ctypes.c_uint8),
                ('idVendor', ctypes.c_uint16),
                ('idProduct', ctypes.c_uint16),
                ('bcdDevice', ctypes.c_uint16),
                ('iManufacturer', ctypes.c_uint8),
                ('iProduct', ctypes.c_uint8),
                ('iSerialNumber', ctypes.c_uint8),
                ('bNumConfigurations', ctypes.c_uint8)]

L = ctypes.CDLL(ctypes.util.find_library('usb-1.0') or 'libusb-1.0.so.0')
L.libusb_init.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
L.libusb_open_device_with_vid_pid.argtypes = [ctypes.c_void_p,
    ctypes.c_uint16, ctypes.c_uint16]
L.libusb_open_device_with_vid_pid.restype = ctypes.c_void_p
L.libusb_get_device.argtypes = [ctypes.c_void_p]
L.libusb_get_device.restype = ctypes.c_void_p
L.libusb_get_device_descriptor.argtypes = [ctypes.c_void_p,
    ctypes.POINTER(DD)]
L.libusb_set_auto_detach_kernel_driver.argtypes = [ctypes.c_void_p,
    ctypes.c_int]
L.libusb_claim_interface.argtypes = [ctypes.c_void_p, ctypes.c_int]
L.libusb_claim_interface.restype = ctypes.c_int
L.libusb_release_interface.argtypes = [ctypes.c_void_p, ctypes.c_int]
L.libusb_clear_halt.argtypes = [ctypes.c_void_p, ctypes.c_ubyte]
L.libusb_clear_halt.restype = ctypes.c_int
L.libusb_control_transfer.argtypes = [ctypes.c_void_p, ctypes.c_uint8,
    ctypes.c_uint8, ctypes.c_uint16, ctypes.c_uint16,
    ctypes.POINTER(ctypes.c_ubyte), ctypes.c_uint16, ctypes.c_uint]
L.libusb_control_transfer.restype = ctypes.c_int
L.libusb_bulk_transfer.argtypes = [ctypes.c_void_p, ctypes.c_ubyte,
    ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_int),
    ctypes.c_uint]
L.libusb_bulk_transfer.restype = ctypes.c_int
L.libusb_close.argtypes = [ctypes.c_void_p]

def main():
    dur = float(sys.argv[1]) if len(sys.argv) > 1 else 3600.0
    outp = sys.argv[2] if len(sys.argv) > 2 else 'usblog.bin'
    ctx = ctypes.c_void_p()
    L.libusb_init(ctypes.byref(ctx))
    buf = ctypes.create_string_buffer(4096)
    got = ctypes.c_int(0)
    dd = DD()
    out = open(outp, 'ab', buffering=0)
    print('listen %.0fs -> %s' % (dur, outp))
    t0 = time.time()
    tot = 0
    dev = None
    errs = 0
    while time.time() - t0 < dur:
        now = time.time() - t0
        if dev is None:
            dev = L.libusb_open_device_with_vid_pid(ctx, VID, PID)
            if not dev:
                time.sleep(0.01)
                continue
            L.libusb_get_device_descriptor(L.libusb_get_device(dev),
                                           ctypes.byref(dd))
            if dd.bcdDevice != BCD_U:
                L.libusb_close(dev)
                dev = None
                time.sleep(0.2)
                continue
            print('[%.2f] device bcd=%04x found' % (now, dd.bcdDevice))
            L.libusb_set_auto_detach_kernel_driver(dev, 1)
            print('[%.2f] claim=%d' %
                  (now, L.libusb_claim_interface(dev, 0)))
            r1 = L.libusb_control_transfer(dev, 0x21, 0x22, 3, 0,
                                           None, 0, 1000)
            d7 = (ctypes.c_ubyte * 7)(0x00, 0xc2, 0x01, 0, 0, 0, 8)
            r2 = L.libusb_control_transfer(dev, 0x21, 0x20, 0, 0,
                                           d7, 7, 1000)
            print('[%.2f] ctl DTR=%d CODE=%d' % (now, r1, r2))
            sys.stdout.flush()
            continue
        rc = L.libusb_bulk_transfer(dev, EP_IN,
            ctypes.cast(buf, ctypes.c_void_p), len(buf),
            ctypes.byref(got), 100)
        if rc == 0 and got.value > 0:
            raw = buf.raw[:got.value]
            out.write(raw)
            out.flush()
            os.fsync(out.fileno())
            tot += got.value
            print('[%.2f] RX %dB tot=%d' % (now, got.value, tot))
            print(''.join(chr(b) if 32 <= b < 127 or b in (9, 10, 13)
                          else '.' for b in raw))
            sys.stdout.flush()
        elif rc == -7:
            continue
        else:
            errs += 1
            if errs == 1:
                print('[%.2f] rc=%d clear_halt=%d' %
                      (now, rc, L.libusb_clear_halt(dev, EP_IN)))
            if rc == -4 or errs > 40:
                L.libusb_close(dev)
                dev = None
                errs = 0
                time.sleep(0.2)
    print('TOTAL=%d -> %s' % (tot, outp))

main()
