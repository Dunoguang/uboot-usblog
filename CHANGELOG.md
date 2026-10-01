# Changelog

All notable changes to the DW99 / vp19 u-boot USB-log image.

Versioning was restarted on 2026-09-28.  Everything that had been tried before
(v1..v25, v27..v29) was discarded, and the only image that had been verified on
real hardware - v26 - was promoted to be the baseline release, 0.0.1.  The
history of how it was found is in [`docs/campaign-log.md`](docs/campaign-log.md).

## [0.0.10] - 2026-10-01

**The live USB log is complete.**  `images/uboot-0.0.10-chunked.img`,
484260 bytes, md5 `aa780e3b519d59aa65979def9b83eb44`, built by
`patch/make_log_images_010.py`.

    240 bytes -> 2608 bytes, ending at
                 `init_log_partition_hdr(): init log partition header sucess!`

- **Fixed: any console line longer than 64 bytes killed the log permanently.**
  The injected log fn passed the whole line to `reply_to_pctool`, which ends in a
  single pump call waiting for a single IN-endpoint completion - i.e. one max
  packet.  Past 64 bytes the completion count and the pump count stop matching,
  the ring never drains again, and because the pump is bounded the boot carries
  on printing into a ring nothing will ever read.  The log fn now walks the
  string and calls `reply_to_pctool` once per **<= 63-byte piece**
  (`0x1BCA8`: `bl LOG` -> `bl LOG2` at `0x1BD80`, 39 instructions).
- Measured, not inferred: with 0.0.5 the host received exactly 240 bytes and the
  COM port stayed enumerated for a further 4.8 s (so nothing was torn down - the
  device simply stopped handing data over).  The nine lines that arrived are all
  <= 46 bytes; the tenth is 70.  A throwaway data-only image that shortened
  *only* that one format string to a 16-byte line made it arrive, and the stream
  then stopped again at the next long line.
- **Verified against the device**: that boot's own `uboot_log` slot contains
  `lcd start init time:2281ms`; from `USB SERIAL PORT OPENED` to the end it is
  **67 lines, byte-for-byte identical to the 67 lines the host received**.
  Raw capture: `reference/usblog-0.0.10-full.bin`.
- New `tools/read_com_log.py`: Windows reader for the U2S port over the
  `sprdvcom` COM interface - 1-byte blocking reads (a read URB is always armed),
  per-line timestamps, and a COM-port-presence poll.  No libusb, no UsbDk.
- New `tools/verify_capture.py`: compares a live capture against the device's
  own `uboot_log` slot.
- Full write-up: [`docs/root-cause-64-byte.md`](docs/root-cause-64-byte.md).

Also landed while getting here (kept, all folded into 0.0.10):

- **0.0.2** - correct gate address (`0xA34`, the VA's low 12 bits - the 0.0.1
  block used the file offset's `0xC34` and therefore the wrong byte); bounded
  log pump (the 0.0.1 stall); no pctool command wait.
- **0.0.3 / 0.0.4** - longer calibrate wait windows.  Inert, and measurably so:
  in a no-host boot both wait loops leave through their flag, not their timeout.
- **0.0.5** - `usb_gate_wait` at the trigger: polls the gser "port opened" flag
  so u-boot holds the channel open until the host has opened the port, instead
  of racing a 2 s budget.
- **0.0.6 / 0.0.7 / 0.0.8** - trace markers.  0.0.6's ring markers never
  appeared (the ring was already dead - itself a clue).  0.0.9 is the data-only
  probe described above and is kept as `images/uboot-0.0.9-lentest-probe.img`.
  0.0.8 called `printf` from inside the pump and hung the watch; never call
  `printf` from the console path.

## [Unreleased]

### Documentation
- Diagnosis of the 0.0.1 stall corrected.  An IDA/Hex-Rays review
  ([`docs/te-stall-analysis.md`](docs/te-stall-analysis.md)) shows the boot is
  parked in the console send path the patch introduces
  (`reply_to_pctool` -> unbounded wait for the IN-endpoint completion flag),
  not in the co5300 panel read ID - that path retries at most 4 times and every
  DSI wait is bounded, and the unpatched baseline boots to the system on the
  same hardware.  The last line the host received is simply the last write that
  completed; the next one (the read-ID line, ~165 ms later) blocks.
- Section 4 of that document specifies the exact behaviour: with **no host** the
  forced branch is never reached and the watch boots like the stock image
  (no log); plugged into a PC **without a reader on EP 0x85** the boot freezes
  *before* the LCD init; no key press is involved anywhere.
- **Host handshake bug found**: `gser_setup` (file `0x2B668`) opens the port
  only for `SET_CONTROL_LINE_STATE` with `wValue == 1`; any other value -
  including `3` (DTR|RTS), which `tools/usb_reader.py` used to send - closes it.
  The reader now sends `1`, so unmodified u-boot can bring the channel up by
  itself and the `0x1A720` force is optional.
- Section 6 of that document lists the fix direction for 0.0.2 (bounded /
  best-effort log pump, or queue-only logging) and section 7 the experiments
  that would confirm it on hardware (E0 tests the handshake on the stock image).

## [0.0.1] - 2026-09-28

The first kept version: the former `uboot-v26-forceport.img`, unchanged byte for
byte.

    images/uboot-0.0.1.img   484260 bytes   md5 9a1d5975d299363045fbc702aa3e8fcd
    rebuilt by patch/make_log_images.py from images/uboot.img
              (md5 a03efc263613a61680e892261df90954), asserts + md5 self-check

### Added
- **Live u-boot console over USB** (bulk EP 0x85, vendor interface
  `1782:4d00`/`bcdDevice 24.16`) - 258 bytes captured on hardware with
  `tools/usb_reader.py`:
  `USB SERIAL PORT OPENED`, `usb read timeout`,
  `** File not found /recovery/last_memory **`, `lcd start init time:12148ms`,
  `sprd backlight power brightness=0`, `phy status0/1/2 1f00/1f1a`,
  `sprdfb: mipi_dispc_init_config not support TE`.
- **Console hook** - file `0xE798` (puts entry) branches to injected code in the
  never-called fastboot unlock handler body (file `0x1BC9C`); gate byte at
  `0x1BC34`, trigger stub at `0x1BC38` on the "USB SERIAL PORT OPENED" printf
  (file `0x1A768`), log fn at `0x1BC4C` (`strlen` + `reply_to_pctool`).
- **Forced 8 KB gserial channel allocation** - file `0x1A720`
  (`cbnz w0,+0x38` -> `b +0x38`).  Without it u-boot takes the "calibrate port
  open timeout" path and never allocates the ring, so no byte can reach the
  host at all.

### Known issues
- The boot stops right after `sprdfb: mipi_dispc_init_config not support TE`:
  the co5300 panel read ID never completes and the screen stays dark.  The stall
  is localised between file `0x3314C` (that print) and file `0x3445C` (the
  panel read-ID print).
- The log path is blocking: `reply_to_pctool` (file `0x1A8BC`) calls the event
  pump at file `0x2D2F0`, whose wait
  (`while ([0x9F1CC118] == 0) usb_gadget_handle_interrupts();`, file `0x2D314`)
  has no timeout, so a host that stops reading can freeze u-boot mid-boot.
- The injected code addresses the gate byte 0x200 too high: it really lives at
  file `0x1BE34` (in the *next* dead function, on an `add` instruction), not at
  the `0x1BC34` the patch layout names.  Benign today - both functions are
  unreachable - but it must be fixed before the injected block is moved or
  extended.
- `[0x30]` must stay `0x75EF0` and the image must stay 484260 bytes (vboot
  sechdr base - changing either reset-loops the watch).

### Discarded in this release
- v1..v25 (no channel allocated, or images that reset-looped by changing
  `[0x30]`), v27 and v28 (timeout-path variants; v28 also zeroed a shared
  timeout global used by the tool-read wait), v29 (diagnostic, never tested).
- All pre-2026-09-26 images (`uboot-usblog.img`, `uboot-nocal-usblog*.img`,
  `uboot-patched.img`, `uboot-usb-log.img`).
