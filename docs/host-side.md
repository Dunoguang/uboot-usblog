# Host side: reading the log, flushing images, recovering a dead watch

## 1. What shows up on USB

While u-boot runs, the watch enumerates as

    idVendor=1782  idProduct=4d00  bcdDevice=24.16
    Product: Gadget Serial
    Manufacturer: spreadtrum with musb-hdrc
    full-speed (12 Mbps)
    interface class 0xff (vendor), EP 0x85 = bulk IN, 64-byte packets

`bcdDevice=2.02` is the **SPL / download / charge mode**, not u-boot - same
VID:PID, different device.  Verify `24.16` before capturing.

On Windows the vendor driver `sprdvcom.sys` claims it and exposes it as a COM
port.  It lands in the **Ports** class (`{4d36e978-...}`,
`Service=sprdvcom`) - *not* the USB class - which matters if you are tempted to
filter or redirect it.

The port exists **only while u-boot runs**: it is gone in Android and in
recovery.  Start the reader first, then power-cycle the watch.

## 2. Windows: read the COM port (recommended here)

    python tools/read_com_log.py AUTO 120 capture.bin

No libusb, no UsbDk, no driver changes.  `libusb_open` on this device returns
`LIBUSB_ERROR_NOT_SUPPORTED` because `sprdvcom` owns it.  Two details are not
optional:

- **Keep a read URB armed at all times.**  Polling `BytesToRead`, or using
  `ReadTimeout = 0`, lets the driver's queue run dry and the log stalls early.
  The tool issues 1-byte `ReadFile` calls with all `COMMTIMEOUTS` zeroed, i.e.
  blocking until a byte arrives, and calls `SetDTR` after opening - which is what
  makes `gser_setup` see `SET_CONTROL_LINE_STATE wValue == 1`.
- **Poll COM-port presence while you read.**  This is what separates "the device
  stopped sending" from "the link went away".  It is how the 64-byte defect was
  found: the port stayed alive 4.8 s past the last byte, which ruled out a reset
  or a torn-down gadget.

`AUTO` selects whatever COM port appears; it waits up to 45 s for one.  Every
line is printed with a timestamp and the running byte count, and the raw stream
is written to the output file.

## 3. Linux: read the endpoint directly

    sudo python3 tools/usb_reader.py -t 120 -o capture.bin
    sudo python3 tools/usb_reader.py --list      # what is on the bus right now
    sudo python3 tools/usb_reader.py --check     # set up and report, read nothing

libusb-1.0 through ctypes, no third-party dependencies.  It re-enumerates on
every pass rather than caching bus/address (both change on re-enumeration), so it
survives the device disappearing when u-boot hands over to the kernel, and it can
be left running across power cycles.

Four things in it are load-bearing; each is there because something failed
without it.

- **`SET_CONTROL_LINE_STATE` carries `wValue = 1`** (DTR asserted, RTS clear).
  `gser_setup` reads exactly `1` as "port open" and *every other value, including
  `3`, as "port closed"* - a reader that sends `3` closes the port it is trying
  to open, the port-open wait times out, and the gserial ring may never be
  allocated.  `usb_reader.py` re-asserts it every 2 s while reading.  If you
  write your own, this is the first thing to get right.
- **The interface is really claimed.**  Auto-detach, then an explicit
  `detach_kernel_driver` on `LIBUSB_ERROR_BUSY`, and the claim result is checked
  instead of printed and ignored.  A silent failure here produces exactly the
  "no bytes at all" symptom.
- **A read is pending essentially all the time.**  The console send waits for an
  IN-endpoint completion, which only happens while the host has a transfer
  outstanding; a short per-read timeout makes libusb cancel and re-arm the URB
  constantly and the device pays for each one.
- **`bcdDevice` is checked and reported.**  u-boot and the download agent share
  VID:PID; `0x2416` is u-boot, `0x0202` is SPL / download / charge mode and will
  never produce a log.  The reader says which one it found instead of spinning
  silently.

- **`/dev/bus/usb` nodes are created if they are missing.**  On a normal PC udev
  does this and the reader does nothing.  Inside a container, a chroot, or an
  Arch install hosted on an Android phone - where `/dev` is a minimal snapshot
  with *no* `/dev/bus/usb` at all - it builds `/dev/bus/usb/BBB/DDD` from sysfs
  before opening (usb_device is char major 189, minor
  `(bus - 1) * 128 + devnum - 1`).  Without it the symptom is nasty: `lsusb`
  lists the watch, the reader's own enumeration finds it too, and `libusb_open`
  fails anyway.  Needs root or `CAP_MKNOD`; `--no-mknod` turns it off.

## 4. Checking a capture without trusting it

    python tools/verify_capture.py <p11-dump.bin> <capture.bin>

Compares everything from `USB SERIAL PORT OPENED` to the end against the device's
own `uboot_log` slot for that same boot.  The released image matches 67 of 67
lines.  Dump p11 first:

    adb shell "dd if=/dev/block/mmcblk0p11 bs=4096" > p11.bin     # from recovery, as root
    # or, in the container / on Linux:
    tools/dump_uboot_log.sh

`tools/parse_uboot_log.py` prints the slots; each one is the complete pre-kernel
console log of one **successful** boot (see `docs/internals.md` section 8).

## 5. Flushing an image

The image must go into the `uboot` partition - `mmcblk0p9`, 1 MB.  `uboot_bak`
(`mmcblk0p10`) holds a copy; writing both is the safe habit.  The file is 484260
bytes and the first 484260 bytes of the partition must match it exactly.

**With adb (fastest, needs Android or recovery):**

    adb push uboot-0.0.1.img /tmp/ub.img
    adb shell "dd if=/tmp/ub.img of=/dev/block/mmcblk0p9 bs=4096; sync"
    adb shell "dd if=/dev/block/mmcblk0p9 bs=4096 count=119 | head -c 484260 | md5sum"

The md5 printed must equal the image's md5.  Verify; do not assume.

**Over BROM (needed when the watch will not boot at all):** put the watch into
download mode - force it off (hold the side button ~20 s), then hold the side
button while plugging USB - and use the vendor tool:

    scc.exe fdl 1 0x5000 fdl 2 0x9EFFFE00 w uboot <img> w uboot_bak <img> rst

`scc.exe` (SPRDClientExample) draws its partition-write progress display with
`System.Console` cursor/window APIs.  Run it with stdout or stderr redirected -
which is what any scripted harness does - and those APIs throw
`IOException: The handle is invalid.` (zh-CN `句柄无效。`) **the moment the write
starts**, so every write aborts right after `开始写入uboot分区`.  The handshake,
fdl1 and fdl2 all succeed; only the write dies, which makes it look like a device
problem.  It is not.

Give it a real console and scrape that console from outside:

```python
from concap import spawn_with_console, ConsoleCapture
p = spawn_with_console([scc, 'fdl', '1', '0x5000', ...])   # CREATE_NEW_CONSOLE, hidden
cap = ConsoleCapture(p.pid); cap.open()                    # AttachConsole + CONOUT$
...
```

`tools/concap.py` does exactly this (window hidden via
`STARTF_USESHOWWINDOW`/`SW_HIDE`, so nothing pops up on screen), and
`tools/scc_console.py` wraps it for one-shot commands such as

    python tools/scc_console.py r uboot readback.img

Note that `ReadConsoleOutputCharacter` must be called **one row at a time**: a
single block read returns a short character count that is not a multiple of the
row width and cannot be sliced back into lines.  And a PowerShell `.ps1`
containing non-ASCII must be saved **with a BOM** - Windows PowerShell 5.1 reads
BOM-less scripts as ANSI, and Chinese string literals break the parser.

## 6. Environment pitfalls that cost real time

- The `uboot_log` partition only helps for successful boots.  For a failed boot
  the live USB log is the only channel - see `docs/internals.md` section 8.
- `is_7s_reset 0x1000`, `sysdump_flag`, `invalid sprd imgversion magic` and the
  repeated `rpmb read blk ... successful` lines are **normal**, not errors.
- When capturing, filter on `bcdDevice 0x24/0x16`, otherwise the probe hammers
  the 2.02 charge-mode device and prints endless errors.
- If no data arrives at all, look for `usb calibrate port open timeout` in the
  device's own log: the gserial ring was never allocated.
- Device-side patches are easy to get silently wrong.  Disassemble the built
  image before flashing: `python patch/verify_image.py images/uboot-0.0.1.img`.
