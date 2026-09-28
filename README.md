# uboot-usblog - DW99 / vp19 (Spreadtrum SL8541E) uboot USB log capture

Patch a Spreadtrum u-boot so that its boot log streams out over USB in real
time, and read it from a host with libusb.  No device disassembly, no UART.
Built for debugging DW99 / vp19 watches (e.g. the 4.4 / 4.4.302 kernel line
that "does not boot and prints nothing").

> **Current version: 0.0.1** - `images/uboot-0.0.1.img`
> (484260 bytes, md5 `9a1d5975d299363045fbc702aa3e8fcd`).
> The live USB log is verified on real hardware with this image; see
> [`CHANGELOG.md`](CHANGELOG.md) for what it contains and what is still broken.
>
> On 2026-09-28 every other experiment (v1..v25, v27..v29) was discarded and the
> verified v26 was promoted to be the baseline, version 0.0.1.  The record of how
> it was found is kept in [`docs/campaign-log.md`](docs/campaign-log.md) and
> [`docs/v28-review.md`](docs/v28-review.md) (why 0.0.1 still stops at the panel
> read ID, and why its log path can block).
>
> Full notes: [`docs/`](docs/README.md) - internals, campaign log, host side.
>
> (This README was rewritten in English because the authoring environment
> silently corrupts non-ASCII input.  A Chinese version is welcome as a PR.)

## 1. Working recipe (verified 2026-09-26, both parts required)

### 1.1 Console hook
- `file 0xE798` (puts entry) -> `b` to `0x1BC9C`, inside a never-called
  function body in the dead-code area (file `0x1BC34..0x1BEDC`).
- Hook: redo the prologue -> call the log fn -> jump back to `0xE79C`.
- Log fn at `0x1BC4C`: read the gate byte (`0x1BC34`, value 0 in the image)
  -> if 0, return immediately; otherwise clear the gate first (re-entrancy
  guard) -> strlen -> `bl 0x1A8BC` (reply_to_pctool) -> restore the gate.
- The gate is set at `file 0x1A768`, the printf call site of
  "USB SERIAL PORT OPENED" (trigger stub at `0x1BC38`).

### 1.2 Force the channel allocation (the critical part)
- Without a tool handshake u-boot prints
  `usb calibrate port open timeout3871,1870,2000` and **skips the 8 KB
  gserial channel allocation** (the `bl 0x9F02CDFC` at file `0x1A758` sits on
  the "port opened" branch).
- Patching `file 0x1A720` from `cbnz w0,+0x38` to `b +0x38` makes u-boot
  allocate the channel, which is what lets any byte reach the host.

## 2. Images and tools

| image | md5 | what |
|---|---|---|
| `images/uboot-0.0.1.img` | `9a1d5975...` | **version 0.0.1**: console hook + forced channel allocation; 258 bytes captured on hardware |
| `images/uboot.img` | `a03efc26...` | the unlock baseline every patch is built from - not a release |

- `tools/usb_reader.py` - host reader (libusb via ctypes, no deps); fsyncs
  every received chunk to disk.
- `tools/dump_uboot_log.sh` - dump + parse the uboot_log partition (section 5).
- `patch/make_log_images.py` - rebuild `images/uboot-0.0.1.img` from
  `images/uboot.img`, with asserts on the baseline and an md5 self-check.

## 3. Known issues of 0.0.1

- The boot stops right after `sprdfb: mipi_dispc_init_config not support TE`
  (the co5300 panel read ID never completes, so the screen stays dark).  The
  stall is localised between that print (file `0x3314C`) and the panel read-ID
  print (file `0x3445C`); see `docs/v28-review.md` section 3.
- The log path is blocking: `reply_to_pctool` (file `0x1A8BC`) ends in an
  unbounded `while ([0x9F1CC118] == 0) usb_gadget_handle_interrupts();`
  (file `0x2D114`), so a host that stops reading can freeze u-boot.
- The gate byte is addressed 0x200 too high by the injected code: it really
  lives at file `0x1BE34`, not `0x1BC34`.  Benign (that byte is 0 and inside the
  same dead function), but do not move the injected block without fixing it.
- Taking the "port opened" branch also runs the tool handshake waits
  (`usb read timeout` shows up in the log), which adds about 8 s
  (`lcd start init time` goes 4004 ms -> 12148 ms).

## 4. [0x30] must not be changed (mechanism)

vboot derives the tail security header location from it:

    sechdr_offset = [0x30] + 0x200 = 0x760F0     (cert at 0x76150)

Changing [0x30] makes vboot read the wrong location -> **reset loop**.  This
is the "screen lights up, then reboots after about 3.3 s" symptom (the LCD
init runs before the vboot step, so the screen comes up first).  [0x30] must
stay `0x75EF0` and the file length must not change (484260).

## 5. uboot_log partition (only written for *successful* boots)

- `/dev/block/mmcblk0p11`, 4 MB = header + N x 256 KB slots; each slot is
  the complete pre-kernel u-boot console log of one boot.
- **Verified: a slot is only written when the boot completes successfully.**
  A boot that stalls or resets at any step (including before the kernel
  jump) leaves **no record at all**, so this partition cannot be used to
  debug unbootable / stuck devices.  For those cases the live USB log is the
  only channel (UART does not work on the DW99: 1.8 V vs 3.3 V levels).
- Slots from successful boots contain lines like `rst_mode 40/0`,
  `is_7s_reset`, `USB SERIAL CONFIGED`, `port open timeout`,
  `battery unconnected shutdown charge`.
- Dump it from recovery (adb root) with `tools/dump_uboot_log.sh`.

## 6. Host-side notes and pitfalls

- Device: `1782:4d00`, `bcdDevice 24.16` ("Gadget Serial", vendor class
  0xff, EP5-IN = 0x85, 64-byte bulk).
- After claiming the interface, send `SET_CONTROL_LINE_STATE` (DTR/RTS).
- Address conversion: `VA = file + 0x9EFFFE00`; converting back,
  `file = (VA - 0x9F000000) + 0x200`.  Forgetting the 0x200 is a classic
  trap (it cost most of a day here).
- If no data arrives, check the device side for `port open timeout`: that
  means the gserial channel was never allocated.

## 7. Versioning and history

- Versions are plain `MAJOR.MINOR.PATCH` from `CHANGELOG.md`; `0.0.1` is the
  first kept version and is the former `v26` image, byte for byte.
- v1..v25 and v27..v29 were **discarded** on 2026-09-28; their images are gone
  from `images/`, and only the code that produces 0.0.1 is kept in `patch/`.
- v1's claim of "verified, 3.7 KB of logs" cannot be reproduced: without the
  channel-allocation patch there is no channel, and no host-side reader can
  receive anything (see `FINDINGS-2026-09-26.md`).
- Every image that touched `[0x30]` (v4/v5/v7/v12/v13) reset-looped - section 4.
- History, traps and the analysis of the remaining defects:
  `docs/campaign-log.md`, `docs/v28-review.md`, `FINDINGS-2026-09-26.md`.

## 8. Layout

- `images/` - the images; `patch/` - patch generators; `tools/` - host tools;
- `analysis/` - the corrected IDA listing (true runtime base,
  `VA = file + 0x9EFFFE00`); `analysis/README.md` records what the old export got
  wrong and how to re-export it if ever needed;
- `reference/` - reference material (UART boot log of a sibling watch).
