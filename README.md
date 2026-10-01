# uboot-usblog - DW99 / vp19 (Spreadtrum SL8541E) uboot USB log capture

Patch a Spreadtrum u-boot so that its boot log streams out over USB in real
time, and read it from a host with libusb.  No device disassembly, no UART.
Built for debugging DW99 / vp19 watches (e.g. the 4.4 / 4.4.302 kernel line
that "does not boot and prints nothing").

> **Current version: 0.0.10** - `images/uboot-0.0.10-chunked.img`
> (484260 bytes, md5 `aa780e3b519d59aa65979def9b83eb44`).
> The live USB log is verified on real hardware and is now **complete**: 2608
> bytes, ending at `init_log_partition_hdr(): init log partition header sucess!`
> - the last line u-boot prints before the kernel - and byte-for-byte identical
> to that boot's own `uboot_log` slot.  See [`CHANGELOG.md`](CHANGELOG.md) and
> [`docs/root-cause-64-byte.md`](docs/root-cause-64-byte.md).
>
> 0.0.10 fixes the defect that made 0.0.1..0.0.8 stop after 240 bytes: the
> injected log fn handed whole console lines to `reply_to_pctool`, and any line
> longer than the 64-byte USB max packet size killed the stream permanently.
> 0.0.10 splits every line into <= 63-byte pieces.
>
> On 2026-09-28 every other experiment (v1..v25, v27..v29) was discarded and the
> verified v26 was promoted to be the baseline, version 0.0.1.  The record of how
> it was found is kept in [`docs/campaign-log.md`](docs/campaign-log.md); the
> defects that are still open are listed in section 3 below.
>
> Full notes: [`docs/`](docs/README.md) - internals, campaign log, host side.
>
> (This README was rewritten in English because the authoring environment
> silently corrupts non-ASCII input.  A Chinese version is welcome as a PR.)

## 1. Working recipe

### 1.0 Log fn must not exceed one max packet (0.0.10, the fix for 0.0.1..0.0.8)
- `reply_to_pctool` ends in a single pump call that waits for a single IN-endpoint
  completion, i.e. one 64-byte packet.  Hand it a longer buffer and the ring
  never drains again - silently, forever, while u-boot keeps booting.
- 0.0.10 therefore walks the string and calls `reply_to_pctool` once per
  **<= 63-byte piece**.  Evidence and the throwaway probe image that proved it:
  [`docs/root-cause-64-byte.md`](docs/root-cause-64-byte.md).

### 1.1 Console hook
- `file 0xE798` (puts entry) -> `b` to `0x1BC9C`, inside a never-called
  function body in the dead-code area (the injected block spans file
  `0x1BC34..0x1BCB7`, inside the dead fastboot handler `0x1BC34..0x1BED8`).
- Hook: redo the prologue -> call the log fn -> jump back to `0xE79C`.
- Log fn at `0x1BC4C`: read the gate byte -> if 0, return immediately;
  otherwise clear the gate first (re-entrancy guard) -> inline strlen loop ->
  `bl 0x1A8BC` (reply_to_pctool) -> restore the gate -> ret.
- The gate is set at `file 0x1A768`, the printf call site of
  "USB SERIAL PORT OPENED" (trigger stub at `0x1BC38`).
- The gate byte the code actually reads and writes is at file **`0x1BE34`**,
  not `0x1BC34` - the stub and the log fn agree on that address, which is why
  0.0.1 works.  See section 3 before touching the block.

### 1.2 Force the channel allocation (the critical part)
- Without a tool handshake u-boot prints
  `usb calibrate port open timeout3871,1870,2000` and **skips the 8 KB
  gserial channel allocation** (the `bl 0x9F02CDFC` at file `0x1A758` sits on
  the "port opened" branch).
- Patching `file 0x1A720` from `cbnz w0,+0x38` to `b +0x38` makes u-boot
  allocate the channel, which is what lets any byte reach the host.  It is
  **optional since 2026-09-28**: the host must assert DTR only
  (`SET_CONTROL_LINE_STATE` with `wValue = 1`), then u-boot's own port-open wait
  succeeds and allocates the channel without any patch here - see
  [`docs/te-stall-analysis.md`](docs/te-stall-analysis.md) section 4.

## 2. Images and tools

| image | md5 | what |
|---|---|---|
| `images/uboot-0.0.10-chunked.img` | `aa780e3b...` | **current**: 0.0.5 + the log fn chunked to <= 63 bytes; **2608-byte complete live log, verified against the device's own uboot_log slot** |
| `images/uboot-0.0.5-gatewait.img` | `38192ec8...` | hook + bounded pump + `usb_gate_wait`; still stops at 240 bytes (the 64-byte defect) |
| `images/uboot-0.0.2.img` | `fb0186e8...` | 0.0.1 + correct gate address, bounded pump, no pctool wait |
| `images/uboot-0.0.1.img` | `9a1d5975...` | the original verified release: console hook + forced channel allocation; 258 bytes captured |
| `images/uboot.img` | `a03efc26...` | the unlock baseline every patch is built from - not a release |

- `patch/make_log_images_010.py` - rebuild `images/uboot-0.0.10-chunked.img` from
  the baseline (imports the 0.0.1/0.0.2/0.0.5 generators, so the whole stack is
  reproducible from one command).
- `tools/read_com_log.py` - **Windows** reader for the SPRD U2S port over the
  `sprdvcom` COM interface: 1-byte blocking reads so a URB is always armed,
  microsecond timestamps per line, and a once-a-second COM-port-presence poll
  (that poll is what proved the gadget is not torn down when the log stops).

- `tools/usb_reader.py` - host reader (libusb via ctypes, no deps); accepts
  only `1782:4d00` with `bcdDevice 0x2416`, sends DTR/RTS after claiming the
  interface, and fsyncs every received chunk to disk.
- `tools/dump_uboot_log.sh` - dump the uboot_log partition over adb and parse
  it with `tools/parse_uboot_log.py` (section 5).
- `tools/parse_uboot_log.py` - slot parser: header magic `0xABCD`, one 256 KB
  slot per recorded boot.
- `tools/mk_usb_node.sh` - keep `/dev/bus/usb` nodes alive inside a container;
  without it the reader prints only `listen ...s` while `lsusb` already shows
  the watch (see `docs/host-side.md` section 6).
- `patch/make_log_images.py` - rebuild `images/uboot-0.0.1.img` from
  `images/uboot.img`, with asserts on the baseline and an md5 self-check.
  Verified 2026-09-28: it reproduces the released image byte for byte.

## 3. Known issues of 0.0.1 (all four fixed by 0.0.10)

> **Resolved 2026-10-01.**  The 240-byte stop was *not* only the blocking pump.
> With the pump bounded (0.0.2) the boot completed normally and the screen came
> up, yet the host still received exactly 240 bytes and then nothing, while the
> port stayed enumerated for another 4.8 s.  The real limit is the transfer
> size: `reply_to_pctool` must never be handed more than one 64-byte max packet.
> Measured, proved with a data-only probe image and fixed by chunking -
> [`docs/root-cause-64-byte.md`](docs/root-cause-64-byte.md).  The four items
> below are kept as history.

- The boot stops right after `sprdfb: mipi_dispc_init_config not support TE`
  and the screen stays dark.  The stall is between that print (file `0x3314C`,
  the printf call; the string is loaded at `0x33140`) and the panel read-ID
  printf call (file `0x3445C`; the format string
  `co5300_readid read id value is 0x%x,...` is at file `0x65259`).
  The cause is **not** the panel: the read-ID path retries at most 4 times and
  every DSI wait is bounded, while the console send path this patch adds has no
  timeout at all - the next console write after the TE line parks u-boot in the
  USB wait.  Full IDA review:
  [`docs/te-stall-analysis.md`](docs/te-stall-analysis.md).
- The log path is blocking: `reply_to_pctool` (file `0x1A8BC`) calls the event
  pump at file `0x2D2F0`, whose wait
  `while ([0x9F1CC118] == 0) usb_gadget_handle_interrupts();`
  (file `0x2D314`, VA `0x9F02D114`) has no timeout; the flag it waits for is set
  only by the IN-endpoint completion callback (`gs_write_complete`, file
  `0x2C464`), so u-boot blocks whenever the host stops draining EP 0x85.  This
  is the defect behind the stall above - see `docs/te-stall-analysis.md`
  sections 3 and 6.
- The gate byte is addressed 0x200 too high by the injected code: the gate it
  really uses is file `0x1BE34`, not `0x1BC34`.  That byte is the first byte of
  `add x0,x0,#0x7b7` in the *next* dead function (the fastboot "unlock
  bootloader" confirm handler, file `0x1BEDC..0x1BF87`), so a set gate turns
  that instruction into `add x1,x0,#0x7b7`.  Benign today - both functions are
  unreachable (no branch target, no pointer reference in the image) - but the
  byte is not where the layout table in `docs/uboot-internals.md` places it,
  and it must be fixed before the injected block is moved or extended.
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
- After claiming the interface, send `SET_CONTROL_LINE_STATE` with **`wValue = 1`**
  (DTR asserted, RTS clear).  The vendor `gser_setup` handler treats exactly `1`
  as "port open" and **every other value - including `3` (DTR|RTS) - as "port
  closed"**, so `wValue = 3` actively prevents u-boot from allocating the
  channel (`docs/te-stall-analysis.md` section 4).  `tools/usb_reader.py` sends
  `1` since 2026-09-28.
- Address conversion: `VA = file + 0x9EFFFE00`; converting back,
  `file = (VA - 0x9F000000) + 0x200`.  Forgetting the 0x200 is a classic
  trap (it cost most of a day here).
- If no data arrives, check the device side for `port open timeout`: that
  means the gserial channel was never allocated.
- Container gotcha: `/dev/bus/usb` does not survive a container restart, and a
  device number changes on every re-enumeration.  Keep
  `tools/mk_usb_node.sh` running in the background; details and the symptom in
  `docs/host-side.md` section 6.

## 7. Versioning and history

- Versions are plain `MAJOR.MINOR.PATCH` from `CHANGELOG.md`; `0.0.1` is the
  first kept version and is the former `v26` image, byte for byte.
- v1..v25 and v27..v29 were **discarded** on 2026-09-28; their images are gone
  from `images/`, and only the code that produces 0.0.1 is kept in `patch/`.
- v1's claim of "verified, 3.7 KB of logs" cannot be reproduced: without the
  channel-allocation patch there is no channel, and no host-side reader can
  receive anything (see `FINDINGS-2026-09-26.md`).
- Every image that touched `[0x30]` (v4/v5/v7/v12/v13) reset-looped - section 4.
- History and traps: `docs/campaign-log.md`, `docs/uboot-internals.md`,
  `FINDINGS-2026-09-26.md`.

## 8. Layout

- `images/` - the images; `patch/` - the generator for 0.0.1; `tools/` - host
  tools;
- `analysis/` - the corrected IDA listing (true runtime base,
  `VA = file + 0x9EFFFE00`); `analysis/README.md` records what the old export got
  wrong and how to re-export it if ever needed;
- `docs/` - internals, campaign log, host side (index in `docs/README.md`);
- `reference/` - two logs: `uart_readable.log` (115200 UART boot log of a
  sibling watch of the same u-boot family) and `disavb_tos_8541e.log` (an
  sfd_tool session: BROM -> FDL1/FDL2, the full 38-partition table - which
  independently confirms `uboot_log` as partition 11 / 4 MB - and the
  "disable AVB by patching trustos" run that produced the unlocked baseline).

## 9. License

MIT, Copyright (c) 2026 Dunoguang - see [`LICENSE`](LICENSE).
