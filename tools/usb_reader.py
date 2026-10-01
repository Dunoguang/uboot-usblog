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

6.  `/dev/bus/usb` may not exist at all.
    On a normal PC udev creates the nodes.  Inside a container, a chroot, or an
    Arch install hosted on an Android phone - all of which this has been run in -
    `/dev` is a minimal snapshot with **no `/dev/bus/usb` directory**, and then
    libusb still *enumerates* the watch (it reads sysfs) but `libusb_open` fails:
    the reader looks broken while `lsusb` happily lists the device.  So the
    reader creates the node itself from sysfs before opening - usb_device is
    major 189, and the minor is `(bus - 1) * 128 + devnum - 1`.  Needs root or
    CAP_MKNOD; `--no-mknod` turns it off.

7.  Send exactly ONE control request, and send it last.
    `gser_setup` handles `bRequest == 0x22` and returns `-EOPNOTSUPP` for
    everything else, so `SET_LINE_CODING` (0x20) and `clear_halt`'s
    `CLEAR_FEATURE` both stall and burn a full timeout each.  Measured on the
    watch: 1.0 s and 2.9 s - and u-boot only spends about 4 s between the port
    opening and handing over to the kernel, so that wasted the entire boot.
    So: one `SET_CONTROL_LINE_STATE wValue = 1` with a 200 ms timeout, then read
    immediately.  `SET_LINE_CODING` is behind `--line-coding`; an endpoint halt
    is cleared lazily, only if a transfer actually reports `LIBUSB_ERROR_PIPE`.
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import os
import signal
import stat
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
SYSFS_USB = '/sys/bus/usb/devices'
USB_DEV_MAJOR = 189         # usb_device; the node the kernel exposes per device

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
        self.warned_mknod = False
        self.made_nodes = set()
        self.cleared = False
        self.said_gone = False

    # -- reporting ---------------------------------------------------------
    def t(self):
        return time.time() - self.t0

    def say(self, msg):
        print('[%7.3f] %s' % (self.t(), msg), flush=True)

    # -- /dev/bus/usb nodes ------------------------------------------------
    def make_nodes(self):
        """Create /dev/bus/usb/BBB/DDD for the watch if the dev tree lacks them.

        libusb enumerates from sysfs, so it *sees* the watch even when /dev has
        no bus nodes - it is libusb_open that then fails.  usb_device is char
        major 189, minor `(bus - 1) * 128 + devnum - 1`.

        Runs every enumeration pass and costs a few stat() calls; a node is only
        created when it is actually missing, so a normal PC (udev) does nothing
        here.
        """
        if self.a.no_mknod or not hasattr(os, 'mknod') or not os.path.isdir(SYSFS_USB):
            return
        try:
            entries = os.listdir(SYSFS_USB)
        except OSError:
            return
        for e in entries:
            d = os.path.join(SYSFS_USB, e)
            try:
                if open(os.path.join(d, 'idVendor')).read().strip().lower() != '%04x' % VID:
                    continue
                if open(os.path.join(d, 'idProduct')).read().strip().lower() != '%04x' % PID:
                    continue
                maj, mi = (int(x) for x in open(os.path.join(d, 'dev')).read().strip().split(':'))
                bus = int(open(os.path.join(d, 'busnum')).read().strip())
                dn = int(open(os.path.join(d, 'devnum')).read().strip())
            except (OSError, ValueError):
                continue
            path = '/dev/bus/usb/%03d/%03d' % (bus, dn)
            if os.path.exists(path) or path in self.made_nodes:
                continue
            if USB_DEV_MAJOR != 0 and maj != USB_DEV_MAJOR:
                self.say('note: %s reports major %d, expected %d'
                         % (e, maj, USB_DEV_MAJOR))
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                os.mknod(path, stat.S_IFCHR | 0o666, os.makedev(maj, mi))
                os.chmod(path, 0o666)
                self.made_nodes.add(path)
                self.say('created %s (%d:%d) - /dev/bus/usb was missing'
                         % (path, maj, mi))
            except PermissionError:
                self.made_nodes.add(path)
                if not self.warned_mknod:
                    self.warned_mknod = True
                    self.say('cannot create %s (%d:%d): permission denied.'
                             % (path, maj, mi))
                    self.say('  run as root (sudo), or fix /dev another way.')
            except OSError as ex:
                self.made_nodes.add(path)
                self.say('mknod %s failed: %s' % (path, ex))

    # -- enumeration -------------------------------------------------------
    def find(self):
        """Return (dev, dd, bus, addr) for 1782:4d00, or (None, ...) plus a note."""
        self.make_nodes()
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
        self.cleared = False
        self.said_gone = False

        def s(idx):
            """String descriptor as text.  Never let a weird descriptor kill
            the reader: a c_ubyte array has no .value (that was a real bug)."""
            if not idx:
                return ''
            try:
                b = (ctypes.c_ubyte * 128)()
                r = L.libusb_get_string_descriptor_ascii(h, idx, b, 128)
                if r <= 0:
                    return '?'
                return bytes(b[:r]).decode('utf-8', 'replace')
            except Exception as e:                       # noqa: BLE001
                return '? (%s)' % e

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
        # Short timeout: u-boot answers a handled request in microseconds, so a
        # slow return means it is not handling it at all - and every millisecond
        # spent here is a millisecond of console output we are not draining.
        rc = L.libusb_control_transfer(h, 0x21, 0x22, 1, IFACE, None, 0, 200)
        self.say('SET_CONTROL_LINE_STATE wValue=1 -> %s' % errname(rc)
                 + ('' if rc == 0 else '   (the port will NOT open)'))
        self.last_dtr = time.time()

        # Deliberately NOT done here, each for a measured reason:
        #
        #   SET_LINE_CODING (0x20)
        #       u-boot's gser_setup handles exactly one class request, 0x22, and
        #       returns -EOPNOTSUPP for everything else, so this stalls and costs
        #       a full timeout - measured 1.0 s of the ~4 s u-boot spends between
        #       the port opening and handing over to the kernel.  Enable with
        #       --line-coding only if some other gadget needs it.
        #
        #   clear_halt(0x85)
        #       Also an unhandled control request: measured 2.9 s.  A halt is
        #       cleared lazily instead, the first time a transfer actually
        #       reports a stall (LIBUSB_ERROR_PIPE).
        if self.a.line_coding:
            code = (ctypes.c_ubyte * 7)(0x00, 0xC2, 0x01, 0x00, 0x00, 0x00, 0x08)
            rc = L.libusb_control_transfer(h, 0x21, 0x20, 0, IFACE, code, 7, 200)
            self.say('SET_LINE_CODING 115200 8N1 -> %s' % errname(rc))

        if self.out is None:
            self.out = open(self.a.out, 'wb', buffering=0)
        self.say('reading EP 0x%02x immediately -> %s   (Ctrl-C to stop)'
                 % (EP_IN, self.a.out))
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
        if self.out is not None:
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
                # re-assert; gser_setup's flag is idempotent for wValue == 1.
                # Short timeout - a slow answer here would cost log, not gain it.
                L.libusb_control_transfer(self.handle, 0x21, 0x22, 1, IFACE,
                                          None, 0, 200)
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
            if rc == -9:                       # stall: clear it, then carry on
                if not self.cleared:
                    self.cleared = True
                    self.say('endpoint stalled, clearing halt')
                L.libusb_clear_halt(self.handle, EP_IN)
                continue
            if rc in (-4, -1):
                # u-boot tore the gadget down and jumped to the kernel, or the
                # watch was unplugged.  Say it once, not once per poll.
                if not self.said_gone:
                    self.said_gone = True
                    self.say('device stopped answering (%s) - u-boot has handed '
                             'over to the kernel (%d bytes captured)'
                             % (errname(rc), self.total))
                return False
            self.say('bulk read: %s' % errname(rc))
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
            try:
                ok = self.open(dev, dd, bus, addr)
            except Exception as e:                       # noqa: BLE001
                self.say('setup raised %s: %s' % (type(e).__name__, e))
                self.close()
                time.sleep(0.5)
                continue
            if not ok:
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
    ap.add_argument('--no-mknod', action='store_true',
                    help='do not create missing /dev/bus/usb nodes')
    ap.add_argument('--line-coding', action='store_true',
                    help='also send SET_LINE_CODING (u-boot stalls it, costs ~1 s)')
    a = ap.parse_args()

    r = Reader(a)
    if a.list:
        r.make_nodes()          # so --list reflects what is actually usable
        return do_list()
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
