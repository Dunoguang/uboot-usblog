# uboot-usblog - DW99 / vp19 (Spreadtrum SL8541E) uboot USB log capture

Patch a Spreadtrum u-boot so that its boot log streams out over USB in real
time, and read it from a host with libusb.  No device disassembly, no UART.
Built for debugging DW99 / vp19 watches (e.g. the 4.4 / 4.4.302 kernel line
that "does not boot and prints nothing").

> **Status 2026-09-26**: the live USB log has been verified on real hardware
> (recipe below), and the root cause why v1..v25 never received a single byte
> has been found.  Details in `FINDINGS-2026-09-26.md`.
>
> Full notes: [`docs/`](docs/README.md) - internals, campaign log, host side.
>
> (This README was rewritten in English because the authoring environment
> silently corrupts non-ASCII input.  A Chinese version is welcome as a PR.)

## 1. Working recipe (verified 2026-09-26, both parts required)

### 1.1 Console hook
- `file 0xE798` (puts entry) -> `b` to `0x1BC34`, inside a never-called
  function body in the dead-code area.
- Hook: redo the prologue -> call the log fn -> jump back to `0xE79C`.
- Log fn at `0x1BC4C`: read the gate byte (`0x1BC34`, value 0 in the image)
  -> if 0, return immediately; otherwise clear the gate first (re-entrancy
  guard) -> strlen -> `bl 0x1A8BC` (reply_to_pctool) -> restore the gate.
- The gate is set at `file 0x1A768`, the printf call site of
  "USB SERIAL PORT OPENED".

### 1.2 Force the channel allocation (the critical part)
- Without a tool handshake u-boot prints
  `usb calibrate port open timeout3871,1870,2000` and **skips the 8 KB
  gserial channel allocation** (the `bl 0x9F02CDFC` sits on the
  "port opened" branch).
- Patching `file 0x1A720` from `cbnz w0,+0x38` to `b +0x38` (= v26) makes
  u-boot allocate the channel, which is what lets any byte reach the host.
- Gentler variants (not tested yet): `file 0x1A754` `b +0x18` -> `b +0x04`
  (= v27), and `file 0x1A710` timeout 2000 ms -> 0 (= v28).

## 2. Images and tools

| image | md5 | status |
|---|---|---|
| `images/uboot-v25-log.img` | `57a103f5...` | hook only; boots fine, no data |
| `images/uboot-v26-forceport.img` | `9a1d5975...` | **verified**: 258 bytes captured |
| `images/uboot-v27-alloc.img` | `b19f00a8...` | untested |
| `images/uboot-v28-alloc-fast.img` | `0bfe247b...` | untested |

- `tools/usb_reader.py` - host reader (libusb via ctypes, no deps); fsyncs
  every received chunk to disk.
- `tools/dump_uboot_log.sh` - dump + parse the uboot_log partition (section 5).
- `patch/make_log_images.py` - rebuild all four images from
  `images/uboot.img`, with md5 self-check.

## 3. Known issue of v26

Taking the "port opened" branch also runs the tool handshake waits
(`usb read timeout` shows up in the log), which adds about 8 s
(`lcd start init time` goes 4004 ms -> 12148 ms), and the boot then
**stalls at the panel read ID step**.  v27 / v28 target exactly this.

## 4. [0x30] must not be changed (mechanism)

vboot derives the tail security header location from it:

    sechdr_offset = [0x30] + 0x200 = 0x760F0     (cert at 0x76150)

Changing [0x30] makes vboot read the wrong location -> **reset loop**.  This
is the "screen lights up, then reboots after about 3.3 s" symptom (the LCD
init runs before the vboot step, so the screen comes up first).  [0x30] must
stay `0x75EF0` and the file length must not change.

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

## 7. Earlier versions

- v1's claim of "verified, 3.7 KB of logs" cannot be reproduced: without
  the channel-allocation patch there is no channel, and no host-side reader
  can receive anything (see `FINDINGS-2026-09-26.md`).
- Every image that touched `[0x30]` (v4/v5/v7/v12/v13) loops - section 4.
- Treat all pre-2026-09-26 images as non-functional experiments.

## 8. Layout

- `images/` - the images; `patch/` - patch generators; `tools/` - host tools;
- `analysis/` - disassembly export; `reference/` - reference material.
