#!/usr/bin/env python3
"""Run scc.exe (SPRDClientExample) with a real console, and print what it said.

    python scc_console.py r uboot C:\\out\\readback.img
    python scc_console.py fdl 1 0x5000 fdl 2 0x9EFFFE00 w uboot image.img rst

Why this exists
---------------
scc.exe draws its partition-write progress display with System.Console
cursor/window APIs.  Started with stdout or stderr redirected - which is what any
scripted harness does - those APIs throw `IOException: The handle is invalid.`
(zh-CN `句柄无效。`) the moment the write starts, so the write aborts:

    ... 已执行fdl2并握手
    开始写入uboot分区
    发生错误: 句柄无效。

The handshake and both FDL stages succeed; only the write dies, which makes it
look like a device problem.  It is not.

This wrapper gives scc.exe its own (hidden) console via CREATE_NEW_CONSOLE and
then scrapes that console with AttachConsole + ReadConsoleOutputCharacter, so the
write works and the output is still captured.

Environment:
    SCC_PATH   directory or full path of scc.exe (default: look next to this file)
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from concap import spawn_with_console, ConsoleCapture  # noqa: E402

SCC = os.environ.get('SCC_PATH') or os.path.join(HERE, 'scc.exe')
if os.path.isdir(SCC):
    SCC = os.path.join(SCC, 'scc.exe')

DONE_MARKERS = ('完毕', '成功', '发生错误', '无效', 'denied', '失败')


def run(args, timeout=240.0, quiet_after=6.0, exe=None, cwd=None, echo=True):
    """Run a console program in its own hidden console; return (text, error)."""
    exe = exe or SCC
    cwd = cwd or os.path.dirname(exe)
    p = spawn_with_console([exe] + list(args), cwd=cwd)

    cap = ConsoleCapture(p.pid)
    attached = False
    for _ in range(25):                 # the console is not always ready instantly
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
        return None, 'console attach failed err=%s' % cap.err

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


if __name__ == '__main__':
    if not os.path.exists(SCC):
        print('scc.exe not found (set SCC_PATH): %s' % SCC, file=sys.stderr)
        sys.exit(2)
    txt, err = run(sys.argv[1:])
    if err:
        print('ERROR: %s' % err, file=sys.stderr)
        sys.exit(2)
    print('=' * 60)
    print(txt)
    if '发生错误' in txt or 'error' in txt.lower():
        sys.exit(1)
