# Changelog

All notable changes to the DW99 / vp19 u-boot USB-log image.

Versioning was restarted on 2026-09-28.  Everything that had been tried before
(v1..v25, v27..v29) was discarded, and the only image that had been verified on
real hardware - v26 - was promoted to be the baseline release, 0.0.1.  The
history of how it was found is in [`docs/campaign-log.md`](docs/campaign-log.md).

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
  panel read-ID print); analysis in [`docs/v28-review.md`](docs/v28-review.md).
- The log path is blocking: `reply_to_pctool` (file `0x1A8BC`) ends in an
  unbounded `while ([0x9F1CC118] == 0) usb_gadget_handle_interrupts();`
  (file `0x2D114`), so a host that stops reading can freeze u-boot mid-boot.
- The injected code addresses the gate byte 0x200 too high (it really lives at
  file `0x1BE34`); benign today, but it must be fixed before the injected block
  is moved or extended.
- `[0x30]` must stay `0x75EF0` and the image must stay 484260 bytes (vboot
  sechdr base - changing either reset-loops the watch).

### Discarded in this release
- v1..v25 (no channel allocated, or images that reset-looped by changing
  `[0x30]`), v27 and v28 (timeout-path variants; v28 also zeroed a shared
  timeout global used by the tool-read wait), v29 (diagnostic, never tested).
- All pre-2026-09-26 images (`uboot-usblog.img`, `uboot-nocal-usblog*.img`,
  `uboot-patched.img`, `uboot-usb-log.img`).
