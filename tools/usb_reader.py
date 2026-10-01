#!/usr/bin/env python3
"""usb_reader.py - read the live u-boot console from the DW99 / vp19 watch.

Linux host side, libusb-1.0 through ctypes, no third-party dependencies.

    sudo python3 usb_reader.py                     # 10 min, -> usblog.bin
    sudo python3 usb_reader.py -t 120 -o log.bin
    sudo python3 usb_reader.py --list              # what USB devices are there?
    sudo python3 usb_reader.py --check             # set up and report, read nothing

Requires `images/uboot-0.0.1.img` (or any image built from it) on the watch.
The U2S port only exists while u-boot runs, so start this first and then
power-cycle the watch; the reader waits for the device to appear.

Why this file looks the way it does
-----------------------------------
Everything below is here because something went wrong without it.

1.  `SET_CONTROL_LINE_STATE` must carry **wValue = 1**.
    u-boot's `gser_setup` treats exactly 1 (DTR asserted, RTS clear) as "port
    open" and *every other value - including 3 (DTR|RTS) - as "port closed"*.
    A reader that sends 3 therefore closes the port it is trying to open, the
    port-open wait times out, and the 8 KB gserial ring may never be allocated:
    zero bytes, no matter what else is right.

2.  The interface must really be claimed.
    The device is `class 0xff` vendor, so normally nothing binds to it - but if
    a `usbserial`/`cdc_acm`-style driver did grab it, `claim_interface` returns
    `LIBUSB_ERROR_BUSY` and every later transfer fails.  We auto-detach, then
    detach explicitly, then claim, and we *check* the result instead of
    printing it and carrying on.

3.  A read must be pending essentially all the time.
    u-boot's console send waits for an IN-endpoint completion, which only
    happens when the host actually reads EP 0x85.  The gap between two
    `libusb_bulk_transfer` calls is microseconds, but a short timeout makes the
    library cancel and re-arm the URB on every expiry, and the device pays for
    each one.  A generous timeout keeps the endpoint armed for the whole window.

4.  `bcdDevice` tells u-boot and the download agent apart - they share VID:PID.
    0x2416 is u-boot ("Gadget Serial"); 0x0202 is SPL / download / charge mode,
    which will never produce a log.  Say so instead of spinning silently.

5.  The device disappears when u-boot hands over to the kernel, and comes back
    differently later, so re-enumerate rather than exiting.  Bus number and
    device address change on every re-enumeration, so never cache them.

6.  In a container `/dev/bus/usb` may simply not exist; `--list` says so.
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import os
import signal
import sys
import time

VID = 0x1782
PID = 0x4D00
BCD_UBOOT = 0x2416          # "Gadget Serial" - the one we want
BCD_DOWNLOAD = 0x0202       # SPL / download / charge mode - never logs
EP_IN = 0x85                # bulk IN, 64-byte packets
IFACE = 0
TIMEOUT_MS = 500            # per bulk read; the URB stays armed for all of it
KEEPALIVE_S = 2.0           # re-assert DTR at least this often
POLL_DEVICE_S = 0.25        # how often to re-enumerate while waiting

ERRORS = {
    0: 'SUCCESS', -1: 'IO', -2: 'INVALID_PARAM', -3: 'ACCESS', -4: 'NO_DEVICE',
    -5: 'NOT_FOUND', -6: 'BUSY', -7: 'TIMEOUT', -8: 'OVERFLOW', -9: 'PIPE',
    -10: 'INTERRUPTED', -11: 'NO_MEM', -12: 'NOT_SUPPORTED', -99: 'OTHER',
}


class DeviceDescriptor(ctypes.Structure):
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


def load_libusb():
    name = ctypes.util.find_library('usb-1.0') or 'libusb-1.0.so.0'
    try:
        L = ctypes.CDLL(name)
    except OSError as e:
        sys.exit('cannot load libusb-1.0 (%s).\n'
                 '  Debian/Ubuntu: apt install libusb-1.0-0\n'
                 '  Arch:          pacman -S libusb' % e)

    L.libusb_init.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
    L.libusb_init.restype = ctypes.c_int
    L.libusb_exit.argtypes = [ctypes.c_void_p]
    L.libusb_get_device_list.argtypes = [ctypes.c_void_p,
                                         ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))]
    L.libusb_get_device_list.restype = ctypes.c_ssize_t
    L.libusb_free_device_list.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_int]
    L.libusb_get_device_descriptor.argtypes = [ctypes.c_void_p,
                                               ctypes.POINTER(DeviceDescriptor)]
    L.libusb_get_device_descriptor.restype = ctypes.c_int
    L.libusb_get_bus_number.argtypes = [ctypes.c_void_p]
    L.libusb_get_bus_number.restype = ctypes.c_uint8
    L.libusb_get_device_address.argtypes = [ctypes.c_void_p]
    L.libusb_get_device_address.restype = ctypes.c_uint8
    L.libusb_open.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    L.libusb_open.restype = ctypes.c_int
    L.libusb_close.argtypes = [ctypes.c_void_p]
    L.libusb_get_string_descriptor_ascii.argtypes = [
        ctypes.c_void_p, ctypes.c_uint8, ctypes.POINTER(ctypes.c_ubyte), ctypes.c_int]
    L.libusb_get_string_descriptor_ascii.restype = ctypes.c_int
    L.libusb_kernel_driver_active.argtypes = [ctypes.c_void_p, ctypes.c_int]
    L.libusb_kernel_driver_active.restype = ctypes.c_int
    L.libusb_detach_kernel_driver.argtypes = [ctypes.c_void_p, ctypes.c_int]
    L.libusb_detach_kernel_driver.restype = ctypes.c_int
    L.libusb_set_auto_detach_kernel_driver.argtypes = [ctypes.c_void_p, ctypes.c_int]
    L.libusb_set_auto_detach_kernel_driver.restype = ctypes.c_int
    L.libusb_claim_interface.argtypes = [ctypes.c_void_p, ctypes.c_int]
    L.libusb_claim_interface.restype = ctypes.c_int
    L.libusb_release_interface.argtypes = [ctypes.c_void_p, ctypes.c_int]
    L.libusb_release_interface.restype = ctypes.c_int
    L.libusb_clear_halt.argtypes = [ctypes.c_void_p, ctypes.c_ubyte]
    L.libusb_clear_halt.restype = ctypes.c_int
    L.libusb_control_transfer.argtypes = [
        ctypes.c_void_p, ctypes.c_uint8, ctypes.c_uint8, ctypes.c_uint16,
        ctypes.c_uint16, ctypes.POINTER(ctypes.c_ubyte), ctypes.c_uint16, ctypes.c_uint]
    L.libusb_control_transfer.restype = ctypes.c_int
    L.libusb_bulk_transfer.argtypes = [
        ctypes.c_void_p, ctypes.c_ubyte, ctypes.c_void_p, ctypes.c_int,
        ctypes.POINTER(ctypes.c_int), ctypes.c_uint]
    L.libusb_bulk_transfer.restype = ctypes.c_int
    return L


L = load_libusb()


def errname(rc):
    return ERRORS.get(rc, 'rc=%d' % rc)


class Reader:
    def __init__(self, args):
        self.a = args
        self.ctx = ctypes.c_void_p()
        rc = L.libusb_init(ctypes.byref(self.ctx))
        if rc != 0:
            sys.exit('libusb_init failed: %s' % errname(rc))
        self.handle = None
        self.claimed = False
        self.dd = DeviceDescriptor()
        self.buf = ctypes.create_string_buffer(4096)
        self.got = ctypes.c_int(0)
        self.total = 0
        self.line = bytearray()
        self.last_dtr = 0.0
        self.t0 = time.time()
        self.out = None

    # -- reporting ---------------------------------------------------------
    def t(self):
        return time.time() - self.t0

    def say(self, msg):
        print('[%7.3f] %s' % (self.t(), msg), flush=True)

    # -- enumeration -------------------------------------------------------
    def find(self):
        """Return (dev, dd, bus, addr) for 1782:4d00, or (None, ...) plus a note."""
        lst = ctypes.POINTER(ctypes.c_void_p)()
        n = L.libusb_get_device_list(self.ctx, ctypes.byref(lst))
        if n < 0:
            self.say('get_device_list failed: %s' % errname(n))
            return None, None, 0, 0
        found = None
        seen = []
        try:
            for i in range(n):
                dev = lst[i]
                dd = DeviceDescriptor()
                if L.libusb_get_device_descriptor(dev, ctypes.byref(dd)) != 0:
                    continue
                bus, addr = L.libusb_get_bus_number(dev), L.libusb_get_device_address(dev)
                seen.append((bus, addr, dd))
                if dd.idVendor == VID and dd.idProduct == PID:
                    found = (dev, dd, bus, addr)
                    break
        finally:
            L.libusb_free_device_list(lst, 1)
        return found if found else (None, None, 0, 0), seen

    def describe(self, bus, addr, dd):
        return 'bus %03d addr %03d  %04x:%04x  bcdDevice %04x  class %02x' % (
            bus, addr, dd.idVendor, dd.idProduct, dd.bcdDevice, dd.bDeviceClass)

    # -- device setup ------------------------------------------------------
    def open(self, dev, dd, bus, addr):
        h = ctypes.c_void_p()
        rc = L.libusb_open(dev, ctypes.byref(h))
        if rc != 0:
            self.say('open failed: %s  (root? udev rules?)' % errname(rc))
            return False
        self.handle = h
        self.dd = dd

        def s(idx):
            if not idx:
                return ''
            b = (ctypes.c_ubyte * 128)()
            r = L.libusb_get_string_descriptor_ascii(h, idx, b, 128)
            return b.value.decode('utf-8', 'replace') if r > 0 else '?'

        self.say('device  %s' % self.describe(bus, addr, dd))
        self.say('        %s / %s' % (s(dd.iManufacturer), s(dd.iProduct)))

        # detach anything that grabbed the interface (point 2 in the header)
        act = L.libusb_kernel_driver_active(h, IFACE)
        if act == 1:
            self.say('kernel driver is attached to interface %d' % IFACE)
        r = L.libusb_set_auto_detach_kernel_driver(h, 1)
        if r != 0 and act == 1:
            self.say('auto-detach unavailable (%s), detaching explicitly' % errname(r))
            L.libusb_detach_kernel_driver(h, IFACE)

        rc = L.libusb_claim_interface(h, IFACE)
        if rc == -6:                                   # BUSY
            self.say('claim BUSY - detaching kernel driver and retrying')
            L.libusb_detach_kernel_driver(h, IFACE)
            rc = L.libusb_claim_interface(h, IFACE)
        if rc != 0:
            self.say('claim interface %d FAILED: %s' % (IFACE, errname(rc)))
            self.say('  -3 ACCESS    : run as root, or install a udev rule')
            self.say('  -6 BUSY      : another driver/process holds the device')
            self.close()
            return False
        self.claimed = True
        self.say('claimed interface %d' % IFACE)

        # point 1: wValue MUST be 1.  Anything else CLOSES the port.
        rc = L.libusb_control_transfer(h, 0x21, 0x22, 1, IFACE, None, 0, 1000)
        self.say('SET_CONTROL_LINE_STATE wValue=1 -> %s' % errname(rc)
                 + ('' if rc == 0 else '   (the port will NOT open)'))
        # 115200 8N1, the console's line coding
        code = (ctypes.c_ubyte * 7)(0x00, 0xC2, 0x01, 0x00, 0x00, 0x00, 0x08)
        rc = L.libusb_control_transfer(h, 0x21, 0x20, 0, IFACE, code, 7, 1000)
        self.say('SET_LINE_CODING 115200 8N1 -> %s' % errname(rc))
        self.last_dtr = time.time()

        ch = L.libusb_clear_halt(h, EP_IN)
        if ch != 0:
            self.say('clear_halt(0x%02x) -> %s (continuing)' % (EP_IN, errname(ch)))

        if self.out is None:
            self.out = open(self.a.out, 'wb', buffering=0)
        self.say('reading EP 0x%02x -> %s   (Ctrl-C to stop)' % (EP_IN, self.a.out))
        return True

    def close(self):
        if self.handle:
            if self.claimed:
                L.libusb_release_interface(self.handle, IFACE)
                self.claimed = False
            L.libusb_close(self.handle)
            self.handle = None
        if self.line:
            self.emit(bytes(self.line))
            self.line.clear()

    # -- output ------------------------------------------------------------
    def emit(self, raw):
        self.total += len(raw)
        try:
            self.out.write(raw)
        except OSError:
            pass
        printable = ''.join(chr(b) if 32 <= b < 127 else
                            ('\\r' if b == 13 else '\\n' if b == 10 else '.')
                            for b in raw)
        self.say('+%-6d %s' % (self.total, printable))

    # -- read loop ---------------------------------------------------------
    def read_until(self, deadline):
        keepalive = self.last_dtr + KEEPALIVE_S
        while time.time() < deadline:
            if time.time() >= keepalive:
                # re-assert; gser_setup's flag is idempotent for wValue == 1
                L.libusb_control_transfer(self.handle, 0x21, 0x22, 1, IFACE,
                                          None, 0, 500)
                keepalive = time.time() + KEEPALIVE_S
            rc = L.libusb_bulk_transfer(self.handle, EP_IN,
                                        ctypes.cast(self.buf, ctypes.c_void_p),
                                        len(self.buf), ctypes.byref(self.got),
                                        TIMEOUT_MS)
            if rc == 0 and self.got.value > 0:
                data = self.buf.raw[:self.got.value]
                self.line += data
                while b'\n' in self.line:
                    i = self.line.index(b'\n')
                    self.emit(bytes(self.line[:i + 1]))
                    del self.line[:i + 1]
                continue
            if rc == -7:                       # timeout: nothing to read, fine
                continue
            if rc in (-4, -1, -9):             # gone / io error / stall
                self.say('device stopped answering (%s) - u-boot has probably '
                         'handed over to the kernel' % errname(rc))
                return False
            self.say('bulk read: %s (clearing halt)' % errname(rc))
            L.libusb_clear_halt(self.handle, EP_IN)
            time.sleep(0.05)
        return True

    # -- top level ---------------------------------------------------------
    def run(self):
        deadline = self.t0 + self.a.seconds
        announced = {}
        while time.time() < deadline:
            found, seen = self.find()
            dev, dd, bus, addr = found
            if dev is None:
                for b, a, d in seen:
                    if d.idVendor == VID and d.idProduct == PID:
                        break
                else:
                    key = 'none'
                    if key not in announced:
                        announced[key] = True
                        self.say('no %04x:%04x device - waiting for the watch '
                                 '(started before power-on?)' % (VID, PID))
                        if not seen:
                            self.say('  no USB devices visible at all: in a '
                                     'container, is /dev/bus/usb mounted?')
                time.sleep(POLL_DEVICE_S)
                continue

            if dd.bcdDevice == BCD_DOWNLOAD:
                if 'dl' not in announced:
                    announced['dl'] = True
                    self.say('found %04x:%04x but bcdDevice=%04x: that is SPL / '
                             'download / charge mode, not u-boot (%04x).'
                             % (VID, PID, dd.bcdDevice, BCD_UBOOT))
                    self.say('  it will not log; power-cycle the watch normally.')
                time.sleep(POLL_DEVICE_S)
                continue

            if dd.bcdDevice != BCD_UBOOT:
                if 'unk' not in announced:
                    announced['unk'] = True
                    self.say('found %s - unexpected bcdDevice, skipping'
                             % self.describe(bus, addr, dd))
                time.sleep(POLL_DEVICE_S)
                continue

            announced.clear()
            if not self.open(dev, dd, bus, addr):
                time.sleep(0.5)
                continue
            if self.a.check:
                self.say('--check: setup is good, not reading')
                self.close()
                return 0
            ok = self.read_until(deadline)
            self.close()
            if not ok:
                time.sleep(POLL_DEVICE_S)
        self.say('done, %d bytes -> %s' % (self.total, self.a.out))
        return 0


def do_list():
    ctx = ctypes.c_void_p()
    if L.libusb_init(ctypes.byref(ctx)) != 0:
        sys.exit('libusb_init failed')
    lst = ctypes.POINTER(ctypes.c_void_p)()
    n = L.libusb_get_device_list(ctx, ctypes.byref(lst))
    if n < 0:
        sys.exit('get_device_list failed: %s' % errname(n))
    print('%d USB device(s):' % n)
    for i in range(n):
        dd = DeviceDescriptor()
        dev = lst[i]
        if L.libusb_get_device_descriptor(dev, ctypes.byref(dd)) != 0:
            continue
        mark = ''
        if dd.idVendor == VID and dd.idProduct == PID:
            mark = ('   <-- u-boot, use this one' if dd.bcdDevice == BCD_UBOOT
                    else '   <-- download/charge mode, not u-boot'
                    if dd.bcdDevice == BCD_DOWNLOAD else '   <-- 1782:4d00')
        print('  bus %03d addr %03d  %04x:%04x  bcd %04x  class %02x%s'
              % (L.libusb_get_bus_number(dev), L.libusb_get_device_address(dev),
                 dd.idVendor, dd.idProduct, dd.bcdDevice, dd.bDeviceClass, mark))
    L.libusb_free_device_list(lst, 1)
    L.libusb_exit(ctx)
    return 0


def main():
    ap = argparse.ArgumentParser(
        description='read the live u-boot console over USB (DW99 / vp19)',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('-t', '--seconds', type=float, default=600.0,
                    help='how long to keep reading (default 600)')
    ap.add_argument('-o', '--out', default='usblog.bin',
                    help='raw capture file (default usblog.bin)')
    ap.add_argument('--check', action='store_true',
                    help='open, claim and send the control requests, then exit')
    ap.add_argument('--list', action='store_true',
                    help='list USB devices and exit')
    a = ap.parse_args()

    if a.list:
        return do_list()

    r = Reader(a)
    stop = {'n': False}

    def on_int(_sig, _frm):
        stop['n'] = True
        r.a.seconds = 0
        raise KeyboardInterrupt
    signal.signal(signal.SIGINT, on_int)
    try:
        return r.run()
    except KeyboardInterrupt:
        r.close()
        print('\ninterrupted, %d bytes -> %s' % (r.total, a.out))
        return 0


if __name__ == '__main__':
    sys.exit(main())
