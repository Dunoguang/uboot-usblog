# Campaign log: how the USB log was made to work (and what cost time)

> **Status 2026-09-28**: this is the historical record.  v26 was promoted to
> version **0.0.1** (`images/uboot-0.0.1.img`, md5 `9a1d5975d299363045fbc702aa3e8fcd`)
> and all other experiments (v1..v25, v27..v29) were discarded - see
> `../CHANGELOG.md`.  Image files named below no longer exist.

Device: DW99 / vp19 (Spreadtrum SL8541E), u-boot 2015.07.
Baseline image: the "unlock baseline" `images/uboot.img`
(md5 `a03efc263613a61680e892261df90954`, 484260 bytes) - the only known-good
image, boots normally, no integrity check.

## v1..v13: patching without understanding the mechanism

- v1 claimed "verified, 3.7 KB of log captured".  NOT REPRODUCIBLE: without
  the channel-allocation patch the gserial channel is never created, so no
  host-side reader can receive anything; the image also did not boot.
  Treat every pre-2026-09-26 "verified" claim as folklore.
- v4 / v5 / v7 / v12 appended code and raised [0x30] so the append area
  would be loaded.  All reset-looped.
- v13 is the decisive experiment: it differs from the baseline by exactly
  ONE 4-byte field ([0x30]: 0x75EF0 -> 0x75FA4), the other 484256 bytes are
  identical.  It loops -> [0x30] must never change (mechanism:
  uboot-internals.md section 2).

## v18..v24: the hook, and the +0x200 trap

- v18: hook + gate byte at file 0x1AABC.  Black screen.
- v19: hook only, code placed at file 0x5904C ("empty" zero region).
  Device dead -> that region IS used at runtime.  Static emptiness proves
  nothing; only "never-called function" bodies are safe.
- v20 (control): one banner byte changed (file 0x57DF0 'U' -> 'V').
  Boots fine -> no integrity check exists; the failures are structural.
- v21: hook + code inside the dead fastboot handler (file 0x1BC34), gate
  still at 0x1AABC.  Boots normally, receives 0 bytes.
- v22 (binary test): 0xE798 replaced by `b .` (infinite loop).  The device
  hangs before the logo -> 0xE798 is really the early print path, and the
  hook placement concept is valid.
- v24: trigger written at file 0x1A568 and gate at 0x1AABC.  Still 0 bytes.
- Root cause of v21..v24 silence: VA/file conversion.  The correct trigger
  is file 0x1A768 (= VA 0x9F01A568 minus 0x9F000000 plus 0x200).  The
  0x1AABC site was never the gate site either.

## v25..v26: the channel is the missing piece

- v25: hook + log fn + trigger at file 0x1A768 (gate set right at the
  "USB SERIAL PORT OPENED" printf).  Boots, still 0 bytes - and now the
  only remaining explanation is on the device side.
- Decoding the calibrate / port-open code (file 0x1A500..0x1A770) shows:
  without a tool handshake u-boot times out
  (`usb calibrate port open timeout3871,1870,2000`) and SKIPS the
  `bl 0x9F02CDFC` that allocates the 8 KB gserial ring buffer.
- v26: + `0x1A720: cbnz w0,+0x38` -> `b +0x38` (take the allocation branch
  manually).  **258 bytes of real u-boot console arrive on EP 0x85**:

      USB SERIAL PORT OPENED
      usb read timeout
      ** File not found /recovery/last_memory **
      lcd start init time:12148ms
      sprd backlight power brightness=0
      phy status0 1f00 / phy status 1f00 / phy status1 1f00 / phy status2 1f1a
      sprdfb: mipi_dispc_init_config not support TE

  It then stalls at the panel read-ID step.
- Side effect: the forced branch also runs the tool handshake waits, making
  boot ~8 s slower (`lcd start init time` 4004 ms -> 12148 ms).  v27/v28
  (built by an earlier revision of `patch/make_log_images.py`, which today
  builds only 0.0.1) targeted exactly this, but were never tested and have been
  discarded (2026-09-28).  v26 is the kept version, 0.0.1.

## Traps worth remembering

1. `[0x30]` is vboot's sechdr base; one changed field = reset loop.
2. Zero regions are not free memory; use never-called functions.
3. VA -> file needs +0x200 (cost: v21..v24).
4. "Hook returns early" designs (like old v1) skip the real allocation.
5. "Image is dead" and "patch is wrong" look identical on screen; only
   byte-level diffs plus a control image (v20) separate them.
6. The live u-boot console is the ONLY channel for failed boots: the
   uboot_log partition records successful boots only, and UART is
   electrically unreachable on this watch (1.8 V vs 3.3 V).
7. A patch constant copied from a disassembly keeps its own page base: the
   injected code's gate lands at file `0x1BE34` (0x200 above the intended
   `0x1BC34`) because `adrp 0x9F01B000` was paired with `#0xC34` instead of
   `#0xA34`.  It still works only because the stub and the log fn repeat the
   same mistake - see `uboot-internals.md` section 4.
