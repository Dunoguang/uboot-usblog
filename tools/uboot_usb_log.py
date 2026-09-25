#!/usr/bin/env python3
"""
DW99 / vp19 (SL8541E) uboot USB log collector.
Requires only libusb-1.0 (via ctypes, no pip packages needed).

Device side:
    uboot puts() hooked -> usb_log_puts() -> gserial TX ring buffer
    -> bulk EP5-IN (0x85), vendor interface, VID:PID = 1782:4d00

Usage:
    sudo python3 uboot_usb_log.py                 # timestamped .log
    sudo python3 uboot_usb_log.py -o uart.txt     # custom output file
    sudo python3 uboot_usb_log.py --raw           # also save raw byte stream
    sudo python3 uboot_usb_log.py --no-ts         # no host timestamps
    sudo python3 uboot_usb_log.py --once          # exit after one session

IMPORTANT: start this script BEFORE powering the watch (USB exists only
           during uboot). Disconnect when jumping to kernel is normal;
           the script waits for the next enumeration. Stop with Ctrl-C.
"""
import argparse
import ctypes
import ctypes.util
import signal
import sys
import time

VID, PID = 0x1782, 0x4D00
EP_IN = 0x85
IFACE = 0
XFER_TIMEOUT_MS = 200
POLL_INTERVAL = 0.5

_running = True


def _sigint(*_):
    global _running
    _running = False


def load_libusb():
    names = ["libusb-1.0.so.0", "libusb-1.0.so",
             "/usr/lib/libusb-1.0.so.0", "/lib/libusb-1.0.so.0",
             "libusb-1.0.dylib"]
    found = ctypes.util.find_library("usb-1.0")
    if found:
        names.insert(0, found)
    err = None
    for n in names:
        try:
            return ctypes.CDLL(n)
        except OSError as e:
            err = e
    sys.exit("[-] cannot load libusb-1.0: %s\n    install: apt install libusb-1.0-0" % err)


lib = load_libusb()
lib.libusb_init.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
lib.libusb_init.restype = ctypes.c_int
lib.libusb_exit.argtypes = [ctypes.c_void_p]
lib.libusb_open_device_with_vid_pid.argtypes = [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_uint16]
lib.libusb_open_device_with_vid_pid.restype = ctypes.c_void_p
lib.libusb_set_auto_detach_kernel_driver.argtypes = [ctypes.c_void_p, ctypes.c_int]
lib.libusb_claim_interface.argtypes = [ctypes.c_void_p, ctypes.c_int]
lib.libusb_claim_interface.restype = ctypes.c_int
lib.libusb_release_interface.argtypes = [ctypes.c_void_p, ctypes.c_int]
lib.libusb_close.argtypes = [ctypes.c_void_p]
lib.libusb_bulk_transfer.argtypes = [ctypes.c_void_p, ctypes.c_ubyte,
                                     ctypes.c_void_p, ctypes.c_int,
                                     ctypes.POINTER(ctypes.c_int), ctypes.c_uint]
lib.libusb_bulk_transfer.restype = ctypes.c_int


class Collector(object):
    """Writes decoded lines (with optional host timestamps) + optional raw stream."""

    def __init__(self, out_path, raw_path=None, timestamp=True):
        self.out_path = out_path
        self.raw_path = raw_path
        self.timestamp = timestamp
        self.fout = open(out_path, "ab", buffering=0)
        self.fraw = open(raw_path, "ab", buffering=0) if raw_path else None
        self.total_bytes = 0
        self.total_xfers = 0
        self.sessions = 0
        self.partial = b""
        self.t0 = time.time()

    def feed(self, data):
        self.total_bytes += len(data)
        if self.fraw:
            self.fraw.write(data)
        buf = self.partial + data
        lines = buf.split(b"\n")
        self.partial = lines.pop()
        if not lines:
            return
        ts = time.strftime("[%H:%M:%S] ").encode()
        for ln in lines:
            ln = ln.rstrip(b"\r")
            out = (ts + ln + b"\n") if self.timestamp else (ln + b"\n")
            self.fout.write(out)
            try:
                sys.stdout.buffer.write(out)
            except Exception:
                pass
        sys.stdout.buffer.flush()

    def flush_partial(self):
        if self.partial:
            ts = time.strftime("[%H:%M:%S] ").encode() if self.timestamp else b""
            self.fout.write(ts + self.partial + b"\n")
            sys.stdout.buffer.write(b"<partial> " + self.partial + b"\n")
            sys.stdout.buffer.flush()
            self.partial = b""

    def stats(self):
        dt = time.time() - self.t0
        s = ("bytes=%d xfers=%d sessions=%d elapsed=%.0fs out=%s"
             % (self.total_bytes, self.total_xfers, self.sessions, dt, self.out_path))
        if self.raw_path:
            s += " raw=%s" % self.raw_path
        return s


def wait_for_device(ctx, verbose=True):
    """Poll until the device appears; returns handle or None if interrupted."""
    announced = False
    while _running:
        h = lib.libusb_open_device_with_vid_pid(ctx, VID, PID)
        if h:
            return h
        if verbose and not announced:
            print("[*] waiting for device %04x:%04x ... (power on the watch)" % (VID, PID))
            announced = True
        time.sleep(POLL_INTERVAL)
    return None


def session(ctx, out, verbose=True):
    """One device session: open -> claim -> drain until disconnect."""
    h = wait_for_device(ctx, verbose)
    if not h:
        return False
    lib.libusb_set_auto_detach_kernel_driver(h, 1)
    rc = lib.libusb_claim_interface(h, IFACE)
    if rc < 0 and verbose:
        print("[!] claim interface rc=%d (trying to read anyway)" % rc)
    print("[+] device connected, reading EP 0x%02X" % EP_IN)
    out.sessions += 1
    buf = ctypes.create_string_buffer(64 * 1024)
    got = ctypes.c_int(0)
    idle = 0
    while _running:
        rc = lib.libusb_bulk_transfer(h, EP_IN, ctypes.cast(buf, ctypes.c_void_p),
                                      len(buf), ctypes.byref(got), XFER_TIMEOUT_MS)
        if rc == 0 and got.value > 0:
            out.total_xfers += 1
            out.feed(buf.raw[:got.value])
            idle = 0
        elif rc == -7:      # LIBUSB_ERROR_TIMEOUT
            idle += 1
            if verbose and idle % 25 == 0:
                print("[.] idle ... (%d bytes so far)" % out.total_bytes)
        else:
            if verbose:
                print("[-] transfer end rc=%d (device gone / jumped to kernel)" % rc)
            break
    try:
        lib.libusb_release_interface(h, IFACE)
    except Exception:
        pass
    lib.libusb_close(h)
    out.flush_partial()
    print("[*] session ended (total %d bytes)" % out.total_bytes)
    return True


def main():
    ap = argparse.ArgumentParser(description="DW99/vp19 uboot USB log collector")
    ap.add_argument("-o", "--output", default=None,
                    help="output file (default uboot_usb_<timestamp>.log)")
    ap.add_argument("--raw", action="store_true", help="also save raw byte stream (.raw)")
    ap.add_argument("--no-ts", action="store_true", help="do not prepend host timestamps")
    ap.add_argument("--once", action="store_true", help="exit after one device session")
    ap.add_argument("-q", "--quiet", action="store_true", help="less verbose output")
    args = ap.parse_args()

    signal.signal(signal.SIGINT, _sigint)

    out_path = args.output or ("uboot_usb_%s.log" % time.strftime("%m%d_%H%M%S"))
    raw_path = out_path + ".raw" if args.raw else None
    out = Collector(out_path, raw_path, timestamp=not args.no_ts)

    ctx = ctypes.c_void_p()
    if lib.libusb_init(ctypes.byref(ctx)) != 0:
        sys.exit("[-] libusb_init failed (permission? try sudo)")

    print("=" * 66)
    print(" DW99 uboot USB log collector   dev %04x:%04x  EP 0x%02X" % (VID, PID, EP_IN))
    print(" output: %s" % out_path)
    print(" NOTE: start this script BEFORE powering the watch!")
    print("=" * 66)
    try:
        while _running:
            if not session(ctx, out, verbose=not args.quiet):
                break
            if args.once:
                break
            print("[*] waiting for next enumeration ...\n")
    finally:
        print("\n[*] " + out.stats())
        try:
            out.fout.close()
            if out.fraw:
                out.fraw.close()
        except Exception:
            pass
        lib.libusb_exit(ctx)


if __name__ == "__main__":
    main()
