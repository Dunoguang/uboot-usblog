"""scc_console.py - run scc.exe (or any console tool) in its own hidden console
and print the captured screen text.

    python scc_console.py r uboot C:\\path\\out.img

Needed because scc.exe uses System.Console cursor APIs; with redirected stdio
they throw IOException "The handle is invalid." (zh-CN: 句柄无效。).
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from concap import spawn_with_console, ConsoleCapture  # noqa: E402

TOOLDIR = r"C:\Users\Administrator\Downloads\win-x86"
SCC = os.path.join(TOOLDIR, "scc.exe")

DONE_MARKERS = ("完毕", "成功", "发生错误", "无效", "denied", "失败")


def run(args, timeout=240.0, quiet_after=6.0, exe=SCC, cwd=TOOLDIR, echo=True):
    p = spawn_with_console([exe] + list(args), cwd=cwd)
    cap = ConsoleCapture(p.pid)
    attached = False
    for _ in range(25):
        if cap.open():
            attached = True
            break
        if p.poll() is not None:
            break
        time.sleep(0.2)
    if not attached:
        try:
            p.kill()
        except OSError:
            pass
        return None, "console attach failed err=%s" % cap.err

    text = ""
    t0 = time.time()
    last_change = time.time()
    try:
        while time.time() - t0 < timeout:
            t = cap.read()
            if t is not None and t != text:
                text = t
                last_change = time.time()
                if echo:
                    print(t, flush=True)
            if any(m in text for m in DONE_MARKERS) and time.time() - last_change > quiet_after:
                break
            if p.poll() is not None:
                break
            time.sleep(0.4)
        t = cap.read()
        if t:
            text = t
    finally:
        cap.close()
        try:
            p.kill()
        except OSError:
            pass
    return text, None


if __name__ == "__main__":
    txt, err = run(sys.argv[1:])
    if err:
        print("ERROR:", err, file=sys.stderr)
        sys.exit(2)
    print("=" * 60)
    print(txt)
