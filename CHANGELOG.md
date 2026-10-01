# Changelog

All notable changes to the DW99 / vp19 u-boot USB-log image.

## [0.0.1] - 2026-10-01

The first release, and the only image this repository ships.

    images/uboot-0.0.1.img   484260 bytes   md5 aa780e3b519d59aa65979def9b83eb44
    built by patch/make_log_images.py from images/uboot.img
             (the unlocked baseline, md5 a03efc263613a61680e892261df90954)
    checked by patch/verify_image.py

### Added

- **A complete real-time u-boot console over USB.**  Bulk EP 0x85 on the U2S
  "calibrate" port (`1782:4d00`, `bcdDevice 0x2416`), captured on hardware as
  **2608 bytes / 67 lines**, ending at
  `init_log_partition_hdr(): init log partition header sucess!` - the last line
  u-boot prints before the kernel.
- **Verified against the device.**  The `uboot_log` slot written by that same
  boot gives 67 lines from `USB SERIAL PORT OPENED` to the end,
  **byte-for-byte identical** to what the host received
  (`tools/verify_capture.py`; raw capture in
  `reference/usblog-0.0.1-full.bin`).
- **Best-effort, bounded log path.**  Every wait in the patch is bounded, and no
  send is ever retried, so the console can neither block nor delay the boot -
  with or without a host attached.
- `tools/read_com_log.py` - Windows reader over the `sprdvcom` COM port: 1-byte
  blocking reads (a read URB is always armed), per-line timestamps, and a
  COM-port-presence poll.  No libusb and no UsbDk needed.
- `tools/concap.py` / `tools/scc_console.py` - run `scc.exe` in its own hidden
  console and scrape it.  Without a real console its partition write aborts with
  a console `IOException`, which is what made BROM flashing fail repeatedly.
- `tools/verify_capture.py` - reconcile a live capture with the device's own
  `uboot_log` slot.
- `patch/verify_image.py` - disassemble a built image and resolve every branch
  target before flashing it.
- `docs/internals.md`, `docs/host-side.md`.

### Fixed

- **The log stopped after 240 bytes.**  The injected log fn handed whole console
  lines to `reply_to_pctool`, which ends in a single pump call waiting for a
  single IN-endpoint completion - i.e. one 64-byte max packet.  Past that, the
  completion count and the pump count stop matching, the ring never drains again
  and the log goes silent permanently, while the boot itself is unaffected.  The
  log fn now calls `reply_to_pctool` once per **<= 63-byte piece**.
  Measured, not inferred: the nine lines that used to arrive are all `<= 46`
  bytes and the tenth is 70; a data-only image that shortened *only* that one
  format string made it arrive, and the stream then stopped again at the next
  long line.  See `docs/internals.md` section 4.
- **The boot could be parked by the log.**  `reply_to_pctool`'s pump waited for
  the completion flag with no timeout, so a host that stopped reading froze
  u-boot inside whatever `printf` came next.  The pump is now bounded.
- **The gate byte was addressed 0x200 too high.**  The injected `adrp`/`add`
  pairs used the low 12 bits of the *file* offset (`0xC34`) instead of the *VA*
  (`0xA34`), so the byte the code really read and wrote was not the one the
  trigger stub set.  Latent in one build and fatal in another - with a fresh
  block it means **not one byte of output**.  All four pairs are now correct and
  `patch/verify_image.py` checks them.
- **u-boot's 2 s budget for "host opened the port" was too short** for a Windows
  `sprdvcom` handle (~3.9 s to open), so the gserial ring was frequently never
  allocated and nothing could reach the host at all.  The trigger stub now polls
  the gser "port opened" flag - servicing the gadget - and exits the instant the
  host opens the port, so an attached reader costs nothing and a missing reader
  costs at most `WAIT_MS` (20 s).

### Discarded

Every intermediate experiment was removed from the tree; the repository ships one
image and one generator.  What each of them tried is recorded here only so the
paths are not walked again:

- longer calibrate wait windows - **inert**, and measurably so: in a no-host boot
  both wait loops leave through their flag, not through their timeout;
- ring-buffer trace markers - never appeared, because the ring was already dead
  by then, which was itself a clue;
- a marker string written into the console text - proved that the on-flash
  `uboot_log` taps the console *above* `puts`, so it cannot observe hook activity;
- `printf` from inside the console path - needs ~0x520 bytes of stack under
  `puts -> hook -> log fn -> reply_to_pctool -> pump` and hung the watch with a
  dark screen.  Never do this.

History note: versioning restarted here.  All earlier images (`v1..v29`, and the
pre-2026-09-26 experiments) were superseded; several of them changed `[0x30]` and
reset-looped the watch, and most never allocated the gserial ring, so they could
not have produced a single byte.  This release is a clean rewrite of the recipe,
rebuilt from the baseline and re-verified on hardware.
