"""concap.py - capture another process's Windows console screen buffer.

Why: scc.exe (SPRDClientExample) draws its write-progress display with System.Console
cursor/window APIs.  When its stdout is a file or pipe those APIs throw
IOException "The handle is invalid." (zh-CN: 句柄无效。) and the write aborts.
So scc.exe must own a real console - and we still want its text.

Usage:
    from concap import spawn_with_console, ConsoleCapture
"""
import ctypes
import ctypes.wintypes as wt
import subprocess
import time

k32 = ctypes.WinDLL("kernel32", use_last_error=True)

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

CREATE_NEW_CONSOLE = 0x00000010
CREATE_NEW_PROCESS_GROUP = 0x00000200
STARTF_USESHOWWINDOW = 0x00000001
SW_HIDE = 0


class COORD(ctypes.Structure):
    _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]


class SMALL_RECT(ctypes.Structure):
    _fields_ = [("Left", ctypes.c_short), ("Top", ctypes.c_short),
                ("Right", ctypes.c_short), ("Bottom", ctypes.c_short)]


class CONSOLE_SCREEN_BUFFER_INFO(ctypes.Structure):
    _fields_ = [("dwSize", COORD), ("dwCursorPosition", COORD),
                ("wAttributes", wt.WORD), ("srWindow", SMALL_RECT),
                ("dwMaximumWindowSize", COORD)]


k32.FreeConsole.restype = wt.BOOL
k32.AttachConsole.argtypes = [wt.DWORD]
k32.AttachConsole.restype = wt.BOOL
k32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p,
                            wt.DWORD, wt.DWORD, ctypes.c_void_p]
k32.CreateFileW.restype = wt.HANDLE
k32.GetConsoleScreenBufferInfo.argtypes = [wt.HANDLE,
                                           ctypes.POINTER(CONSOLE_SCREEN_BUFFER_INFO)]
k32.GetConsoleScreenBufferInfo.restype = wt.BOOL
k32.ReadConsoleOutputCharacterW.argtypes = [wt.HANDLE, ctypes.c_wchar_p, wt.DWORD,
                                            COORD, ctypes.POINTER(wt.DWORD)]
k32.ReadConsoleOutputCharacterW.restype = wt.BOOL
k32.CloseHandle.argtypes = [wt.HANDLE]


def spawn_with_console(cmd, cwd=None):
    """Start cmd in a brand-new (hidden) console so Console.* APIs work."""
    si = subprocess.STARTUPINFO()
    si.dwFlags |= STARTF_USESHOWWINDOW
    si.wShowWindow = SW_HIDE
    return subprocess.Popen(cmd, cwd=cwd, startupinfo=si, close_fds=True,
                            creationflags=CREATE_NEW_CONSOLE | CREATE_NEW_PROCESS_GROUP)


class ConsoleCapture:
    """Attach to a process's console and mirror its screen buffer to text."""

    def __init__(self, pid, max_rows=4000):
        self.pid = int(pid)
        self.max_rows = max_rows
        self.h = None
        self.attached = False
        self.err = None

    def open(self):
        k32.FreeConsole()          # no-op if we have no console
        if not k32.AttachConsole(self.pid):
            self.err = ctypes.get_last_error()
            return False
        self.attached = True
        self.h = k32.CreateFileW("CONOUT$", GENERIC_READ | GENERIC_WRITE,
                                 FILE_SHARE_READ | FILE_SHARE_WRITE, None,
                                 OPEN_EXISTING, 0, None)
        if not self.h or self.h == INVALID_HANDLE_VALUE:
            self.err = ctypes.get_last_error()
            return False
        return True

    def read(self):
        """Per-row reads.

        A single block read is NOT usable: ReadConsoleOutputCharacterW returns a
        short character count that is not a multiple of the row width, so the
        result cannot be sliced back into rows (verified empirically).
        """
        info = CONSOLE_SCREEN_BUFFER_INFO()
        if not k32.GetConsoleScreenBufferInfo(self.h, ctypes.byref(info)):
            return None
        w = info.dwSize.X
        rows = min(info.dwSize.Y, self.max_rows)
        if w <= 0 or rows <= 0:
            return ""

        cursor = info.dwCursorPosition.Y
        self.high = max(getattr(self, "high", 0), cursor)
        last = min(rows - 1, self.high + 2)

        buf = ctypes.create_unicode_buffer(w)
        got = wt.DWORD(0)
        lines = []
        for r in range(last + 1):
            got.value = 0
            if k32.ReadConsoleOutputCharacterW(self.h, buf, w, COORD(0, r), ctypes.byref(got)):
                n = got.value
                if n > w:
                    n = w
                lines.append(buf[:n].replace("\x00", "").rstrip())
            else:
                lines.append("")
        while lines and not lines[-1]:
            lines.pop()
        return "\n".join(lines)

    def close(self):
        if self.h:
            k32.CloseHandle(self.h)
            self.h = None


def capture_until(pid, stop, out_path, interval=0.35, timeout=600.0, append=True):
    """Poll the console of `pid` until stop() is true, writing text to out_path."""
    cap = ConsoleCapture(pid)
    t0 = time.time()
    if not cap.open():
        with open(out_path, "a", encoding="utf-8") as f:
            f.write(f"\n[capture] attach failed err={cap.err}\n")
        return None
    last = None
    try:
        while time.time() - t0 < timeout:
            txt = cap.read()
            if txt is not None and txt != last:
                last = txt
                mode = "a" if append else "w"
                try:
                    with open(out_path, mode, encoding="utf-8") as f:
                        f.write(("\n" if append else "") + txt + "\n")
                except OSError:
                    pass
            if stop():
                break
            time.sleep(interval)
        txt = cap.read()
        if txt is not None:
            last = txt
    finally:
        cap.close()
    return last
