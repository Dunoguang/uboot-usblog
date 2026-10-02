# uboot-usblog - real-time u-boot console over USB for the DW99 / vp19 watch

Patch a Spreadtrum u-boot so its boot log streams out over USB **live**, and read
it from a host with no device disassembly, no UART and no working kernel.  Built
for watches that "do not boot and print nothing" - where the on-flash log
partition is empty precisely because the boot failed.

- Target: DW99 / vp19, UNISOC/Spreadtrum **SL8541E** (`sl8541e_1h10`, model
  `S10_Max`), u-boot `2015.07 (Oct 28 2024 - 18:20:07)`, sharklE.
- Release: **`images/uboot-0.0.1.img`** - 484260 bytes,
  md5 `aa780e3b519d59aa65979def9b83eb44`.
- Channel: the U2S **"calibrate" port** (vendor interface `1782:4d00`,
  `bcdDevice 0x2416`, bulk EP 0x85), repurposed as a log drain.
- Guarantee: the log is **best-effort and bounded**.  It can never block or
  delay the boot, whether or not a host is listening.

---

## 1. Verified result

```
USB SERIAL PORT OPENED
** File not found /recovery/last_memory **
lcd start init time:2281ms
sprd backlight power brightness=0
phy status0 1f00 / phy status 1f00 / phy status1 1f00 / phy status2 1f1a
sprdfb: mipi_dispc_init_config not support TE
co5300_readid read id value is 0x33,0x11,0x0,0x0!  and return counter:0
uboot co5300_mipi_init
sprd backlight power brightness=25
uboot consume time:2706ms, lcd init consume:920ms, backlight on time:3201ms
sprd_get_vboot_key(): load_buf is 0x9efffe00.
sprd_get_vboot_key(): sechdr_offset is 0x760f0.
sprd_get_vboot_key(): sechdr_addr is 0x9f075ef0.
sprd_get_vboot_key(): sechdr_addr: 0x9f075ef0. cert_addr: 0x9f075f50. ...
cert_key / dumpHex:32 bytes / ...
read successed / sprd_get_imgversion: rpmb read blk 16380 successful / ...
pass_chip_uid_to_tos()... sizeof(blocks)=8
blks:0x00744806 0x0c208b23, result: 0x0
uboot_set_rpmb_size: rpmb size 4194304
is_wr_rpmb_key rpmb key has been written
init_log_partition_hdr(): init log partition header sucess!
```

**2608 bytes, 67 lines**, ending at the last line u-boot prints before the
kernel.  The port then disappears (u-boot hands over), about 4.8 s after the boot
starts.

This is not a partial capture:

- the endpoint is `init_log_partition_hdr(): init log partition header sucess!`,
  which is the exact last line of every `uboot_log` slot on the device;
- the boot's own `uboot_log` slot was dumped and compared -
  **67 of 67 lines identical**.

Both sides of that comparison ship with the repository, so the headline claim can
be checked without a device at all:

    python tools/verify_capture.py reference/uboot-log-p11-0.0.1.bin \
                                    reference/usblog-0.0.1-full.bin
    # -> host lines 67, device lines 67, mismatches 0

The same 2608-byte result is reproduced by both readers: the Windows COM one and
`tools/usb_reader.py` on Linux (libusb, run from Arch hosted on an Android
phone).  `usb_reader.py` logs the capture with its own timestamps, which is how
the 64-byte defect and the control-request stalls were localised.

Earlier images stopped after 240 bytes.  Why, and how that was found and fixed:
section 4 and `docs/internals.md` section 4.

---

## 2. Quick start

### Read the log

Plug the watch in, then start the reader **before** powering it on (the port only
exists while u-boot runs):

    python tools/read_com_log.py AUTO 120 capture.bin     # Windows
    sudo python3 tools/usb_reader.py -t 120 -o capture.bin  # Linux

On Linux, `usb_reader.py --list` shows what USB devices are visible (including
whether the watch is sitting in download mode instead of u-boot), and
`usb_reader.py --check` performs the whole setup - detach, claim, control
requests - and reports, without reading anything.

Then power-cycle the watch.  You should see `USB SERIAL PORT OPENED` within a
second or two.

### Flash the image

With Android or recovery available (fastest):

    adb push images/uboot-0.0.1.img /tmp/ub.img
    adb shell "dd if=/tmp/ub.img of=/dev/block/mmcblk0p9 bs=4096; sync"
    adb shell "dd if=/dev/block/mmcblk0p9 bs=4096 count=119 | head -c 484260 | md5sum"
    # the md5 printed must be aa780e3b519d59aa65979def9b83eb44

When the watch will not boot at all, use BROM download mode - force it off (hold
the side button ~20 s), then hold the side button while plugging USB:

    scc.exe fdl 1 0x5000 fdl 2 0x9EFFFE00 w uboot images\uboot-0.0.1.img rst

`scc.exe` **must run in a real console** or its partition write aborts; see
`docs/host-side.md` section 5, and `tools/concap.py` /
`tools/scc_console.py` for the wrapper that gives it one and still logs it.

---

## 3. How it works, in one page

```
boot -> sub_9F01A49C brings up the U2S calibrate port
          wait #1: host enumerated usb?          (3000 ms budget, [0x9F1CC11C])
          wait #2: host opened the port?         (2000 ms budget, [0x9F1CC190])
          allocate the 8 KB gserial ring
          printf("USB SERIAL PORT OPENED")  <-- the trigger site
      -> ... LCD init, vboot, RPMB, kernel load ...

console path:   puts -> hook -> log fn -> reply_to_pctool(buf, len)
                              -> write into the ring
                              -> pump: wait for the IN-endpoint completion
```

The patch adds five things, all of them in the never-called fastboot unlock/lock
handler body (`file 0x1BC34..0x1BED8`):

1. **A `puts` hook** (`file 0xE798` -> `0x1BC9C`) that forwards every console
   line to the calibrate port's ring.  The gate byte at `file 0x1BC34` is opened
   by a stub on the `USB SERIAL PORT OPENED` printf, so logging starts exactly
   when the channel exists.
2. **A bounded pump**, so a host that stops reading can never park the boot.
3. **No pctool command wait** - we never want u-boot to wait for input from us.
4. **`usb_gate_wait`**: u-boot's own 2 s budget for "host opened the port" is
   short - a Windows `sprdvcom` handle takes ~3.9 s to open - so the trigger stub
   first polls the gser "port opened" flag (servicing the gadget) and exits the
   instant the host opens the port.  Without a reader the boot pays at most
   `WAIT_MS` (20 s); with one it pays nothing.
5. **Chunked sends** - the fix described next.

Full address-level detail, pseudocode for every injected block and the exact
patch table: `docs/internals.md`.

---

## 4. The one rule: one max packet per `reply_to_pctool` call

`reply_to_pctool` (file `0x1A8BC`) ends with **a single pump call waiting for a
single IN-endpoint completion** - and one completion is **one 64-byte packet**.

Hand it a longer buffer and the completion count and the pump count stop
matching, the ring never drains again, and - because the pump is bounded - u-boot
happily carries on booting while printing into a ring that nothing will ever
read.  The silence is permanent, the device's own console output is unaffected,
and the only symptom is "the log stops".

So `uboot-0.0.1.img`'s log fn walks the string and calls `reply_to_pctool` once
per **<= 63-byte piece**.

### How it was found

An earlier build delivered exactly 240 bytes and then nothing, every run.  Two
measurements broke it open:

**1. The link was fine.**  The reader watches COM-port presence while it reads.
The port stayed enumerated for **4.8 s past the last byte** - that is u-boot
finishing and jumping to the kernel - so nothing was torn down and no USB reset
happened.  The device simply stopped handing data over.

**2. The limit is per line, not cumulative.**  The nine lines that arrived are
all `<= 46` bytes; the tenth is 70:

| # | line | bytes |
|---|---|---|
| 1 | `USB SERIAL PORT OPENED` | 23 |
| 2 | `** File not found /recovery/last_memory **` | 43 |
| 3 | `lcd start init time:2110ms` | 27 |
| 4 | `sprd backlight power brightness=0` | 34 |
| 5-8 | `phy status0/1/2 ...` | 16-17 |
| 9 | `sprdfb: mipi_dispc_init_config not support TE` | 46 |
| 10 | `co5300_readid read id value is 0x33,...,counter:0` | **70** |

A throwaway data-only image changed **only** the read-id format string
(file `0x65259`) so line 10 printed 16 bytes instead of 70 - nothing else, no
code, no layout.  Line 10 arrived, so did the two short lines after it, and the
stream stopped again at the next long line (~70 bytes).  That is the proof.

---

## 5. Build it yourself

    python3 patch/make_log_images.py                  # -> images/uboot-0.0.1.img
    python3 patch/verify_image.py images/uboot-0.0.1.img

`make_log_images.py` is self-contained: it starts from
`images/uboot.img` (the **unlocked baseline**, md5 `a03efc26...`), asserts every
original word it overwrites, and asserts the result against the released md5.  It
either reproduces the verified image byte for byte or fails loudly.

`verify_image.py` disassembles the injected blocks and resolves every branch
target.  **Run it before flashing anything.**  Two mistakes in this patch are
completely silent:

- `str`/`ldr` unsigned-immediate forms take **bytes/8** in imm12, so
  `str x23,[sp,#0x30]` written as `imm12 = 0x30` assembles to `[sp,#0x180]`;
- the gate address constant must be `0xA34`, the **VA's** low 12 bits, not the
  file offset's `0xC34`.  Get it wrong and the log fn never sees an open gate -
  **not one byte of output**.

### Any other build in this family?  Use `auto/`

`auto/` is the fully automatic successor of the hand-ported per-device
generators.  Given any baseline image of this u-boot family it *locates every
anchor from byte-level evidence* - no IDA, no symbols, pure python - then
builds, verifies and audits the patched image:

    cd auto && python3 usblog.py patch <baseline.img>

It reproduces the 0.0.1 image byte for byte from the same baseline, and has
been cross-checked on unseen builds: a third-party-unlocked ai3 image comes out
identical to its hand-made product, and the vp19/ai3/dw99/dw100 regression runs
`0 fails`.  See `auto/README.md` for the anchor chain and the profile format.

### Hard constraints

- `[0x30]` must stay `0x75EF0`.  vboot derives the tail security header location
  from it (`sechdr_offset = [0x30] + 0x200 = 0x760F0`); changing it makes the
  watch reset-loop ~3.3 s after power-on - and since LCD init runs first, the
  screen lights up, which makes it look like a display fault.
- The file length must stay **484260**.
- Injected code may only live in a function that normal boot never calls.
  Free-looking zero regions are not safe: `0x5904C` looks unused and has no
  static references, yet using it killed the device.

---

## 6. Why the on-flash log cannot replace this

`/dev/block/mmcblk0p11` (`uboot_log`, 4 MB) keeps one 256 KB slot per recorded
boot, each containing the complete pre-kernel u-boot console log.  It is real and
it is complete - for boots that **succeed**.

**A boot that stalls or resets at any step leaves no record at all.**  That is
exactly the case you need logs for, and it is why this project exists.  UART is
not an option either: the DW99 UART is electrically unreachable (1.8 V vs 3.3 V
levels).

`uboot_log` is still valuable as ground truth for a boot that did complete, and
`tools/verify_capture.py` automates exactly that comparison.

---

## 7. Repository layout

```
images/
  uboot.img                 the unlocked baseline everything is built from (a03efc26...)
  uboot-0.0.1.img           the release: live USB log            (aa780e3b...)
patch/
  make_log_images.py        single self-contained generator -> images/uboot-0.0.1.img
  verify_image.py           disassemble + check a built image before flashing
auto/
  usblog.py                 automatic anchor location + patch injection:
                            find / patch / verify / audit / regress
  lib/                      byte-level finder, unified builder, verifier, diff audit
  profiles/                 anchor tables for vp19 / ai3 / dw99 / dw100 + templates
  tests/                    four-device regression + unseen-image cross-check
tools/
  read_com_log.py           Windows reader: U2S COM port, blocking reads, presence poll
  usb_reader.py             Linux reader: libusb via ctypes, EP 0x85; creates the
                            /dev/bus/usb node itself when the dev tree lacks it
  verify_capture.py         compare a live capture against the device's uboot_log slot
  concap.py                 run a console program in its own hidden console and scrape it
  scc_console.py            one-shot wrapper around concap.py for scc.exe
  parse_uboot_log.py        uboot_log slot parser (magic 0xABCD, 256 KB slots)
  dump_uboot_log.sh         pull + parse uboot_log over adb
docs/
  internals.md              addresses, patch layout, pseudocode, the 64-byte rule, traps
  host-side.md              reading (Windows/Linux), flashing, BROM recovery, pitfalls
analysis/
  uboot.asm                 corrected IDA listing at the true runtime base
  README.md                 what the old export got wrong and how to re-export
reference/
  usblog-0.0.1-full.bin     the verified 2608-byte live capture
  uboot-log-p11-0.0.1.bin   the device's own uboot_log partition, dumped over adb: the
                            independent side of the 67/67 comparison above
  uart_readable.log         115200 UART boot log of a sibling watch (same u-boot family)
  disavb_tos_8541e.log      BROM session that produced the unlocked baseline; also the
                            source of the 38-partition map (uboot = 9, uboot_bak = 10,
                            uboot_log = 11)
```

---

## 8. Troubleshooting

| symptom | cause |
|---|---|
| no COM port at all | the port only exists while u-boot runs; unplug/replug and power-cycle, and start the reader first |
| port appears but no bytes | the ring was never allocated - look for `usb calibrate port open timeout` in the device's own log; make sure the reader asserts **DTR only** (`SET_CONTROL_LINE_STATE wValue == 1`; `3` actively *closes* the port) |
| log stops part-way, device keeps booting | a send exceeded one max packet (64 bytes) - see section 4 |
| boot freezes / dark screen with a host attached | an unbounded wait somewhere in the log path; this image's pump is bounded on purpose |
| `bcdDevice=2.02` | that is SPL / download / charge mode, not u-boot; you need `24.16` |
| `scc.exe` prints `鍙戠敓閿欒: 鍙ユ焺鏃犳晥銆俙 right after `寮€濮嬪啓鍏boot鍒嗗尯` | it was started without a real console - see `docs/host-side.md` section 5 |

---

## 9. License

MIT, Copyright (c) 2026 Dunoguang - see [`LICENSE`](LICENSE).

