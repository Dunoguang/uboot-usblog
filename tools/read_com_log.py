"""read_com_log.py - timestamped raw reader for the SPRD U2S "calibrate/log" port.

Keeps a read URB permanently armed (1-byte blocking reads, no timeouts), records
byte arrival times, and watches whether the COM port itself disappears - which is
what tells us the difference between:
    the device stopped sending        (port stays present, no bytes)
    the USB link/port went away       (port disappears from Windows)
"""
import ctypes
import ctypes.wintypes as wt
import os
import sys
import time

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
advapi = ctypes.WinDLL("advapi32", use_last_error=True)

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
SETDTR = 5
CLRDTR = 6


class COMMTIMEOUTS(ctypes.Structure):
    _fields_ = [("ReadIntervalTimeout", wt.DWORD),
                ("ReadTotalTimeoutMultiplier", wt.DWORD),
                ("ReadTotalTimeoutConstant", wt.DWORD),
                ("WriteTotalTimeoutMultiplier", wt.DWORD),
                ("WriteTotalTimeoutConstant", wt.DWORD)]


k32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p,
                            wt.DWORD, wt.DWORD, ctypes.c_void_p]
k32.CreateFileW.restype = wt.HANDLE
k32.SetCommTimeouts.argtypes = [wt.HANDLE, ctypes.POINTER(COMMTIMEOUTS)]
k32.ReadFile.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD,
                         ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
k32.EscapeCommFunction.argtypes = [wt.HANDLE, wt.DWORD]


def present_ports():
    import winreg
    out = {}
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"HARDWARE\DEVICEMAP\SERIALCOMM") as k:
            i = 0
            while True:
                try:
                    name, val, _ = winreg.EnumValue(k, i)
                except OSError:
                    break
                out[val] = name
                i += 1
    except OSError:
        pass
    return out


def main():
    port = sys.argv[1] if len(sys.argv) > 1 else "COM11"
    secs = float(sys.argv[2]) if len(sys.argv) > 2 else 90.0
    outbin = sys.argv[3] if len(sys.argv) > 3 else "usblog.bin"

    t0 = time.time()

    def stamp():
        return "%8.3f" % (time.time() - t0)

    print("[%s] ports now: %s" % (stamp(), present_ports()), flush=True)

    h = INVALID_HANDLE_VALUE
    deadline = t0 + 45.0                     # wait for the port to enumerate
    while time.time() < deadline:
        cands = [port] if port.upper() != "AUTO" else []
        cands += [p for p in present_ports() if p not in cands]
        for c in cands:
            h = k32.CreateFileW("\\\\.\\" + c, GENERIC_READ | GENERIC_WRITE, 0,
                                None, OPEN_EXISTING, 0, None)
            if h != INVALID_HANDLE_VALUE:
                port = c
                break
            h = INVALID_HANDLE_VALUE
        if h != INVALID_HANDLE_VALUE:
            break
        time.sleep(0.25)
    if h == INVALID_HANDLE_VALUE:
        print("[%s] open %s FAILED err=%d" % (stamp(), port, ctypes.get_last_error()),
              flush=True)
        return 2
    t = COMMTIMEOUTS(0, 0, 0, 0, 0)          # no timeouts -> block until 1 byte
    k32.SetCommTimeouts(h, ctypes.byref(t))
    k32.EscapeCommFunction(h, SETDTR)        # same as .NET DtrEnable = true
    print("[%s] %s OPEN, DTR set, blocking 1-byte reads" % (stamp(), port), flush=True)

    f = open(outbin, "wb")
    n = wt.DWORD(0)
    buf = ctypes.create_string_buffer(1)
    total = 0
    line = bytearray()
    last_poll = 0.0
    ports = present_ports()
    while time.time() - t0 < secs:
        now = time.time()
        if now - last_poll > 1.0:
            last_poll = now
            p = present_ports()
            if p != ports:
                print("[%s] *** PORT SET CHANGED: %s -> %s" % (stamp(), ports, p),
                      flush=True)
                ports = p
        ok = k32.ReadFile(h, buf, 1, ctypes.byref(n), None)
        if not ok:
            err = ctypes.get_last_error()
            print("[%s] ReadFile FAILED err=%d (device removed?)" % (stamp(), err),
                  flush=True)
            if h not in (None, INVALID_HANDLE_VALUE):
                try: k32.CloseHandle(ctypes.c_void_p(h))
                except Exception: pass
            time.sleep(0.5)
            h = k32.CreateFileW("\\\\.\\" + port, GENERIC_READ | GENERIC_WRITE, 0,
                                None, OPEN_EXISTING, 0, None)
            if h == INVALID_HANDLE_VALUE:
                print("[%s] reopen failed err=%d; keep watching presence"
                      % (stamp(), ctypes.get_last_error()), flush=True)
                time.sleep(0.5)
                continue
            k32.SetCommTimeouts(h, ctypes.byref(t))
            k32.EscapeCommFunction(h, SETDTR)
            print("[%s] reopened %s" % (stamp(), port), flush=True)
            continue
        if n.value:
            b = buf.raw[:n.value]
            f.write(b)
            f.flush()
            total += len(b)
            line += b
            while b"\n" in line:
                idx = line.index(b"\n")
                seg, line = bytes(line[:idx + 1]), line[idx + 1:]
                print("[%s] +%d  %s" % (stamp(), total, repr(seg)), flush=True)
    f.close()
    if h not in (None, INVALID_HANDLE_VALUE):
        try:
            k32.CloseHandle(ctypes.c_void_p(h))
        except Exception:
            pass
    print("[%s] done, %d bytes total -> %s" % (stamp(), total, outbin), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

