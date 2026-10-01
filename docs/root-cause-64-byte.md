# Root cause of the 240-byte stop: one max packet per `reply_to_pctool` call

Analysis date: 2026-10-01.  Hardware: the DW99 watch, image
`images/uboot-0.0.5-gatewait.img` and then `images/uboot-0.0.10-chunked.img`.

This supersedes the "blocking pump" explanation as the *complete* story.  The
unbounded pump of 0.0.1 was real and 0.0.2 fixed it, but a boot with the bounded
pump still delivered exactly 240 bytes and then went silent forever.  The
remaining defect is a **transfer-size limit**, not a wait.

## 1. The measurement

`tools/read_com_log.py` (new) opens the SPRD U2S port with 1-byte blocking
reads - so a read URB is always armed - and, in the same run, polls
`HKLM\HARDWARE\DEVICEMAP\SERIALCOMM` once a second.  0.0.5:

     t=7.768  COM11 OPEN
     t=7.769  +23   USB SERIAL PORT OPENED
     t=7.897  +66   ** File not found /recovery/last_memory **
     t=7.900  +93   lcd start init time:2110ms
     t=8.021  +127  sprd backlight power brightness=0
     t=8.025  +144  phy status0 1f00
     t=8.026  +160  phy status 1f00
     t=8.028  +177  phy status1 1f00
     t=8.029  +194  phy status2 1f1a
     t=8.032  +240  sprdfb: mipi_dispc_init_config not support TE
             <-- 4.8 s of silence, then:
     t=12.852 ReadFile FAILED err=995 (ERROR_OPERATION_ABORTED)
     t=13.853 PORT SET CHANGED: {'COM11': '\Device\sprd_usbcomm_serial1'} -> {}

Two facts fall out of this immediately:

- the port stays present for another 4.8 s, i.e. the gadget is **not** torn down
  and the link is **not** reset - the device simply stops handing data over;
- 4.8 s later the port disappears, which is u-boot finishing and jumping to the
  kernel.  The boot therefore ran to completion.

## 2. Line lengths, not byte counts

| # | line | bytes |
|---|---|---|
| 1 | `USB SERIAL PORT OPENED` | 23 |
| 2 | `** File not found /recovery/last_memory **` | 43 |
| 3 | `lcd start init time:2110ms` | 27 |
| 4 | `sprd backlight power brightness=0` | 34 |
| 5 | `phy status0 1f00` | 17 |
| 6 | `phy status 1f00` | 16 |
| 7 | `phy status1 1f00` | 17 |
| 8 | `phy status2 1f1a` | 17 |
| 9 | `sprdfb: mipi_dispc_init_config not support TE` | 46 |
| 10 | `co5300_readid read id value is 0x33,0x11,0x0,0x0!  and return counter:0` | **70** |

Nine lines arrive, all <= 46 bytes.  Line 10 is the **first line longer than the
64-byte USB bulk max packet size** and never arrives.

## 3. Proof: a data-only image

`images/uboot-0.0.9` is not a release; it was a throwaway probe, byte-identical
to 0.0.5 except for 74 bytes: the co5300 read-id format string at file `0x65259`
was replaced by `RDID %x.%x.%x.%x c%d`, so line 10 prints 16 bytes instead of 70.
Nothing else changed - no code, no layout, `[0x30]` untouched.

Result:

     t=8.031  +240  sprdfb: mipi_dispc_init_config not support TE
     t=8.196  +280  RDID 33.11.0.0 c0uboot co5300_mipi_init        <-- now arrives
     t=8.809  +315  sprd backlight power brightness=25             <-- and so does this
             <-- stops again; the next line is
                 `uboot consume time:2706ms, lcd init consume:920ms, ...` (~70 B)

The stream advanced past line 10 exactly when line 10 became short, and stopped
again at the next long line.  The limit is per call, not cumulative.

## 4. Why

`reply_to_pctool` (file `0x1A8BC`) ends with a single pump call and waits for a
single IN-endpoint completion.  One completion corresponds to one packet.  When
the ring is handed more than one maxpacket, the completion count and the pump
count no longer correspond, the ring never drains again, and because the pump is
bounded (0.0.2) the boot carries on printing into a ring that nothing will ever
read.  Silently: u-boot's own console output is unaffected.

## 5. Fix (0.0.10)

The injected log fn no longer hands the whole string to `reply_to_pctool`.  It
walks the string and calls `reply_to_pctool` once per <= 63-byte piece, so every
call is one packet and matches its one pump.

    0x1BCA8   bl LOG (0x1BC4C)  ->  bl LOG2 (0x1BD80)     the puts hook's call
    0x1BD80   the new log fn, 39 instructions, ends 0x1BE1C
              (the dead fastboot unlock/lock handler body runs to 0x1BED8)

Everything else is 0.0.5 unchanged: the 0.0.1 hook/trigger, 0.0.2's real gate
address (`0xA34` - the VA's low 12 bits, *not* the file offset's), bounded pump
and no-pctool-wait, and 0.0.5's `usb_gate_wait`.

## 6. Result

`images/uboot-0.0.10-chunked.img`, md5 `aa780e3b519d59aa65979def9b83eb44`.

    240 bytes  ->  2608 bytes, ending at
                   `init_log_partition_hdr(): init log partition header sucess!`

which is the last line u-boot prints before the kernel - the same last line every
`uboot_log` slot ends with.

**Verified against the device**: that boot's own `uboot_log` slot (p11, slot 1)
contains `lcd start init time:2281ms`; taking everything from
`USB SERIAL PORT OPENED` to the end gives **67 lines, byte-for-byte identical to
the 67 lines the host received**.  The live USB log is now complete, not partial.

The raw capture is kept as `reference/usblog-0.0.10-full.bin`.

## 7. Reproducing

    python3 patch/make_log_images_010.py            # -> uboot-0.0.10-chunked.img
    python3 tools/read_com_log.py AUTO 120 out.bin  # Windows, waits for the port

On Windows the reader must be started *before* u-boot enumerates the port; it
polls for up to 45 s.  Start it, then power-cycle the watch.

## 8. Two traps hit while building this

- `str`/`ldr` unsigned-immediate forms take **bytes/8** in imm12, not bytes.
  `str x23,[sp,#0x30]` is `imm12 = 6`; passing `0x30` silently emits
  `[sp,#0x180]`.
- The gate address is `va(FLAG) & 0xFFF` = `0xA34`.  Using the file offset's
  low bits (`0xC34`) lands `0x200` past the byte the trigger stub sets, and the
  log fn then never sees the gate open - i.e. **not one byte of output**.  This
  is the same 0.0.1 defect that `docs/uboot-internals.md` section 4 describes;
  it is easy to reintroduce with every new block.

Both were caught by disassembling the built image before flashing.  Always do
that.
