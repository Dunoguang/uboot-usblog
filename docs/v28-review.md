# v28 review: why the USB log stops at "not support TE" and the screen stays dark

> **Status 2026-09-28**: this is the analysis of the discarded **v28** image.
> v25..v29 were dropped and v26 was promoted to version **0.0.1**
> (`images/uboot-0.0.1.img`) - see `../CHANGELOG.md`.  The analysis below still
> applies to 0.0.1: v28 and v26 share the injected console code and the forced
> channel allocation, so the stall at the TE line and the blocking log pump
> described here are properties of 0.0.1 too.  The experiments that ask for the
> v29 image can no longer be run as written.

Date: 2026-09-28.  Method: byte-level audit of every image plus an independent
disassembly (`aarch64-linux-gnu-objdump -D -b binary -m aarch64
--adjust-vma=0x9EFFFE00 images/<img>`), cross-checked against the IDA export in
`analysis/uboot.asm` and the good-boot reference in `reference/uart_readable.log`.
No access to the device was needed for anything below.

    VA   = file + 0x9EFFFE00
    file = VA   - 0x9EFFFE00        (= (VA - 0x9F000000) + 0x200)

## 0. Conclusions

1. **v28's bytes are exactly what the generator says** (md5s match, length
   484260, `[0x30]` still 0x75EF0).  All four patched call sites and the
   132-byte injected block are instruction-correct; the hook's stack discipline
   is correct too (see 4.5).
2. **The v28-only change cannot be the cause of the stall.**  It only turns the
   port-open wait from 2000 ms into ~0 ms.  v26 does not contain it and stops at
   exactly the same line, and both v26/v27/v28 end up in the same place
   (`file 0x1A758`: allocate the 8 KB ring, print "USB SERIAL PORT OPENED",
   open the log gate).
3. **The device does not die at the TE print.**  The TE line is printed by
   `mipi_dispc_init_config` (printf at file `0x3314C`).  In a known-good boot the
   *next* output is the panel read-ID line, then the panel mipi-init line, then
   `sprd backlight power brightness=25` - which is the moment the screen would
   light up.  Our log stops inside/at the beginning of that **co5300 panel
   read-ID sequence**, and the backlight print happens *before* the backlight
   hardware write (file `0x30CC` vs `0x30D0`), so a stop there always means a
   dark screen.
4. Two mechanisms can freeze the boot there, and **both were introduced by the
   patch as a whole, not by the v28 delta**:
   * **A - the console log path is a blocking USB round trip.**  Every logged
     line ends in an *unbounded* wait for a USB completion event
     (`while ([0x9F1CC118] == 0) usb_gadget_handle_interrupts();`, loop at file
     `0x2D114`/`0x2D124`).  The flag is only set when a USB request completes
     (file `0x2C6E0`).  If the host stops consuming - reader exited/slow, the
     reader re-opened the device, a USB reset dropped the pending request - u-boot
     spins there **forever**: no more output, screen dark.
   * **B - the forced "port opened" path changes the boot flow.**  The caller
     (file `0x1AA40`) now takes the "PC tool present" branch (reads a tool
     command -> "usb read timeout"), and the gadget keeps a live IN endpoint.
     The only measured DW99 datapoint (the v26 capture) is
     `lcd start init time:12148ms`, against 4383 ms on the sibling watch's stock
     UART boot - i.e. the patched boot also runs several seconds slower before
     the display bring-up.  Timing-sensitive panel power-up/read-ID can then
     fail: if the co5300 never answers, the DSI read spins and the boot never
     reaches `backlight=25`.
5. Decisive experiments are in section 5; the cheapest one was the "allocate but
   do not log" variant (`v29`), which is **no longer available** - like every
   other pre-0.0.1 image it was discarded on 2026-09-28 (rebuilding it would mean
   re-adding the `force_port=True, trigger=False` case to the generator).

## 1. What v28 actually is (byte audit)

Diff against the baseline `images/uboot.img` (md5 `a03efc26...`), verified on
the images themselves, not with the generator:

| file | baseline word | v25 | v26 | v27 | v28 | v29 | what it is |
|---|---|---|---|---|---|---|---|
| `0xE798` | `A9BF7BFD` `stp x29,x30,[sp,#-16]!` | `14003541` `b 0x9F01BA9C` | same | same | same | same | `puts()` entry -> hook |
| `0x1A768` | `97FFD01C` `bl printf` | `94000534` `bl 0x9F01BA38` | same | same | same | - | the "USB SERIAL PORT OPENED" printf -> gate trigger |
| `0x1A720` | `350001C0` `cbnz w0,+0x38` | - | `1400000E` `b +0x38` | - | - | `1400000E` | force the "port opened" branch |
| `0x1A754` | `14000006` `b +0x18` | - | - | `14000001` `b +0x04` | same | - | timeout path falls into the alloc |
| `0x1A710` | `D280FA01` `mov x1,#0x7d0` | - | - | - | `D2800001` `mov x1,#0` | - | port-open wait 2000 ms -> 0 ms |
| `0x1BC34..0x1BCB7` | (dead function body) | injected | injected | injected | injected | injected | gate slot / trigger / log fn / hook |

Note that **v28 does not contain v26's `0x1A720` patch**: it reaches the alloc
through the patched timeout fall-through at `0x1A754` instead.  Both routes land
on the same instruction (`file 0x1A758`, `bl 0x9F02CDFC` = the 8 KB ring
`memalign(64, 8192)`) and on the same printf that opens the log gate.

Injected block as it exists in the images (file offsets):

    0x1BC34  00000000                zeroed word (the slot the author *thinks* is the gate)
    0x1BC38  adrp/add x1 = 0x9F01BC34 ; mov w2,#1 ; strb w2,[x1] ; b 0x9F00E5D8 (printf)
    0x1BC4C  stp x29,x30,[sp,#-48]! ; mov x29,sp ; str x0,[sp,#16]
             adrp/add x1 = 0x9F01BC34 ; ldrb w1,[x1] ; cbz w1,+0x30 (-> epilogue)
             strb wzr,[x1] ; mov x1,#0 ; strlen loop ; bl 0x9F01A6BC (reply_to_pctool)
             adrp/add x1 = 0x9F01BC34 ; mov w2,#1 ; strb w2,[x1] ; ldp x29,x30,[sp],#48 ; ret
    0x1BC9C  stp x29,x30,[sp,#-16]! ; stp x29,x30,[sp,#-32]! ; str x0,[sp,#16]
             bl 0x9F01BA4C (log fn) ; ldr x0,[sp,#16] ; ldp x29,x30,[sp],#32 ; b 0x9F00E59C

`0x9F01BC34` is **file 0x1BE34**, not 0x1BC34 - see 4.1.

## 2. The USB console state machine (annotated, all addresses verified)

`file 0x1A69C` (`sub_1A69C`, called from `file 0x1A884` in `sub_1AA40`), the
"usb serial calibrate" step.  It uses two globals: timeout `0x9F1BE008`
(file `0x1BE208`) and start time `0x9F1BE068` (file `0x1BE268`).

    entry:            timeout = 3000 ms ; start = now
    loop1 (0x1A6CC):  poll1 = file 0x2D3B4   (flag 0x9F1CC11C, "configured")
                      elapsed > timeout ? -> printf "usb calibrate configuration
                      timeout,%lu,%lu,%lu" (string file 0x60462)
                      success -> printf "USB SERIAL CONFIGED" (string file 0x603CE)
    after loop1:      start = now ; timeout = 2000 ms        <- file 0x1A710 (v28: 0)
    loop2 (0x1A718):  poll2 = file 0x2D3EC   (flag 0x9F1CC190, "port open")
                      w0 != 0 -> 0x1A758
                      elapsed > timeout -> printf "usb calibrate port open
                      timeout%lu,%lu,%lu" (string file 0x60493) at file 0x1A750
                                         -> file 0x1A754 (v27/v28: falls to 0x1A758)
    0x1A758:          bl 0x9F02CDFC (memalign ring) ; printf "USB SERIAL PORT
                      OPENED" (string file 0x604BF, call at file 0x1A768)
                      w19 = 1 (file 0x1A764) ; return 1
    0x1A76C:          return w19

`file 0x1AA40` (`sub_1AA40`) - the caller.  Return 1 -> "tool present":
it allocates a buffer and calls the tool reader `file 0x1A780` ("read up to 10
bytes"), which prints `usb read timeout` (string file 0x604D7) and whose elapsed
check (`sub_1A600`, file 0x1A600) **re-reads the same timeout global**.  Both
outcomes (tool read fails, or return 0) then take the same `mov w0,#-1` path
(file 0x1AAE8 -> shared epilogue file 0x1AC8C), so v26/v28 follow the same boot
flow as the stock image *after* the USB step.

`file 0x1A8BC` (`reply_to_pctool`) - what the hook calls for every line:

    bl 0x9F02CFD0      ring write (file 0x2D1D0): copy into the 8 KB ring
                       (clamped to free space), then kick + store status
                       in 0x9F1CC0D0
    [guarded printf "func: %s line %d usb trans with error %d"]
    bl 0x9F02D0F0      event pump (file 0x2D2F0)  <-- can block forever

`file 0x2D2F0` - the pump (w0 = 1 selects the 0x9F1CC118 flag):

    0x2D114:  ldr w0,[x19] ; cbnz w0 -> clear flag and return
              bl 0x9F030CA0   (usb_gadget_handle_interrupts, file 0x30EA0)
              b  0x2D114      <-- unbounded, no timeout, no bound on iterations

The flag is set to 1 at file `0x2C6E0`, in the request-completion handler
(it dequeues a request at file `0x2C6xx` and invokes the gadget callback at file
`0x2C610`).  So a logged line only completes when the host actually takes the
data; there is no timeout and no fallback.

## 3. Where the boot stops (evidence)

`reference/uart_readable.log` (sibling watch, good boot, UART - different panel
IC, so only the *order* of the steps is comparable, not the timings), lines
71-80:

    lcd start init time:4383ms
    sprd backlight power brightness=0
    phy status0 1f00 / phy status 1f00 / phy status1 1f00 / phy status2 1f1a
    sprdfb: mipi_dispc_init_config not support TE
    icna3311_readid read id value is 0x33,0x11,0x0,0x0!  and return counter:0
    uboot icna3311_mipi_init
    sprd backlight power brightness=25          <-- screen on
    uboot consume time:4694ms, lcd init consume:917ms, backlight on time:5300ms

DW99 equivalents (strings in `images/uboot.img`):

| message | string | printed at |
|---|---|---|
| `sprdfb: mipi_dispc_init_config not support TE` | file `0x64F39` | file `0x3314C` |
| `co5300_readid read id value is 0x%x,... counter:%d` | file `0x65259` | file `0x3445C` |
| `uboot co5300_mipi_init` | file `0x652A5` | file `0x344DC` |
| `sprd backlight power brightness=%d` | file `0x594BB` | file `0x30CC` (HW write at `0x30D0`) |

The captured patched log ends exactly at the TE line, i.e. between the TE print
and the read-ID print: the co5300 read-ID wrapper at file `0x34388` never gets
to its printf at file `0x3445C`.  Before that printf it calls three panel/DSI
callbacks (`blr x0` file `0x343D8`, `blr x23` file `0x343F0`, `blr x24` file
`0x34440`, from the table at file `0x6BE18`, entry `+0xD8` -> file `0x34388`)
and waits 51 x 1 ms; the stall is in one of those, or in the console print that
precedes them (mechanism A).

Cross-check on completeness: the documented 258 captured bytes are *exactly* the
sum of those ten lines (23+17+43+28+34+17+16+17+17+46 = 258), so nothing was
truncated and the reader was healthy right up to the TE line - the freeze starts
after it.  Useful diagnostic: on the v28 run, note the total byte count and the
10th line.  A v28 capture that also stops at the TE line with a *different*
`lcd start init time` value (~258 +/- 1 bytes) rules out a byte-count/packet
boundary effect and points at the panel bring-up (mechanism B); an identical
258-byte stop every time is equally consistent with a lost USB completion event
stalling the log pump at that line (mechanism A).

## 4. Defects found

### 4.1 The gate byte is at file `0x1BE34`, not `0x1BC34` (0x200 bug)

The injected code computes the gate address with `adrp` + `add ..., #0xC34`,
i.e. the *file* offset's low 12 bits.  The real VA is `0x9F01BA34` (low 12 bits
`0xA34`), so the ADRP/ADD pair yields `0x9F01BC34` = **file 0x1BE34**.

Consequences: it works today only by luck - file `0x1BE34` is inside the same
never-called function body and its low byte is 0 in the baseline, and all three
users (trigger, log fn x2) are consistently wrong, so the gate behaves as
intended.  But the documented "gate at file 0x1BC34" is not where the gate is,
`struct.pack_into('<I', d, FLAG, 0)` zeroes a word that nothing reads, and any
future re-layout of the injected block can collide with the real gate (or with
the `strb` that silently patches one byte of dead code at `0x1BE34`).

Fix: use the VA's low bits - `add_imm(1, 1, va(FLAG) & 0xFFF)` - which puts the
gate back at file `0x1BC34` (already zeroed by the generator).

### 4.2 v28's `mov x1,#0` writes a *shared* timeout global

`file 0x1A710` stores into `0x9F1BE008`.  That global is read by `sub_1A600`
(file `0x1A600`), which is also the elapsed check of the *tool read*
(`file 0x1A780`).  v28 therefore does not only skip the port-open wait: it also
disables the following tool-read wait (that read returns immediately, which is
visible as an immediate `usb read timeout`).  Harmless in this campaign (there
is no pctool to answer) but it is the wrong layer to patch - prefer v26's branch
forcing, or the `0x1A754` fall-through with the timeout left at 2000 ms.

### 4.3 The log path can block the whole boot (mechanism A)

See section 2.  This is the only unbounded loop in the patch, it runs once per
printed line, and everything downstream (display bring-up, kernel hand-off)
stops with it.  Recommended shape: write the ring (file `0x2D1D0`) and then call
`usb_gadget_handle_interrupts` (file `0x30EA0`) a *fixed* small number of times
instead of `reply_to_pctool`'s wait-for-flag loop, so a slow/absent host can at
worst lose log bytes, never stall u-boot.

### 4.4 Documentation bugs (they cost debugging time)

* `FINDINGS-2026-09-26.md` and `docs/uboot-internals.md` place the allocation
  `bl` at "file 0x1A558".  The instruction is at **file 0x1A758**; `0x9F01A558`
  is its VA.  This is the project's own 0x200 trap, this time in the notes.
* Same documents say the gate byte is at file `0x1BC34` (see 4.1).
* The IDA export `analysis/uboot.asm` names every referenced string 0x200 too
  low (it labels the "USB SERIAL PORT OPENED" call site `aLoglevel7`, the
  "usb calibrate port open timeout" site `aLoglevel+1`, the co5300 read-ID
  format `aPhyStatus2X`, ...).  Its *code* addresses are correct file offsets,
  its *data* names are not - cross-check patch sites with `objdump`, never with
  the string labels of the export.

### 4.5 Things that are right (verified, so they can be left alone)

* The hook's frame is correct: `stp x29,x30,[sp,#-16]!` re-creates the original
  prologue, the temporary `stp x29,x30,[sp,#-32]!` frame holds `x0` at `sp+16`
  and is fully popped by `ldp x29,x30,[sp],#32`, so the original 16-byte frame
  (and the caller's `x29`) is intact when the hook branches back to
  `0x9F00E59C`.  No frame-pointer corruption.
* The log fn is ABI-clean (it only touches x0/x1/x2, frame `stp/ldp` balanced);
  the gate early-return `cbz w1,+12` and the strlen `cbz w2,+3` offsets are in
  instructions and jump exactly to the epilogue / to the `reply_to_pctool` call.
* The trigger stub clobbers x1/x2, which is harmless at its only call site
  ("USB SERIAL PORT OPENED" has no format arguments).
* `[0x30]` and the file length are untouched in every image => no vboot reset
  loop; the "screen lights up then reboots" failure mode is not in play here.

## 5. Recommended next steps (cheapest first)

1. **Let the reader run continuously and watch the device, not just the log.**
   With the log reader attached (fresh `/dev/bus/usb` node, see
   `tools/mk_usb_node.sh`), record host timestamps and check during the stall:
   * `lsusb -d 1782:4d00 -v | grep bcdDevice` -> `0x2416` = u-boot still alive and
     spinning; the device disappearing = reset/power-off; `0x0202` = SPL/download
     mode, i.e. the watch rebooted.
   * whether the reader keeps returning `rc=-7` (endpoint alive, idle) or
     `rc=-1/-4` (device gone).
   * wait several minutes: a bounded retry loop would eventually print more.
2. **(historical, not runnable)** flash a "forced allocation but no logging"
   build - this was `v29`, discarded on 2026-09-28.  It separated the two
   mechanisms: if it still stopped at the TE line the console log path was not
   the cause (the forced tool-mode/timing was), and if it booted on to the
   kernel the logging path was confirmed as mechanism A.  Rebuilding it means
   adding `force_port=True, trigger=False` back to `patch/make_log_images.py`.
3. **A/B the host side to characterise mechanism A**: run once with the reader
   attached the whole time, once killing the reader a few seconds after
   "USB SERIAL PORT OPENED".  If killing it freezes the boot (log stops at the
   next line), the unbounded pump is proven, and "pull the log" is inherently
   able to hang the boot.
4. Only then rebuild with fixes (all are generator-level changes):
   * F1 - gate addressing (`va(FLAG) & 0xFFF`);
   * F2 - bounded log path (ring write + N x `usb_gadget_handle_interrupts`);
   * F3 - stop patching the shared timeout value (keep 0x1A754 / 0x1A720
     branch forcing, leave 2000 ms);
   * F4 - optionally keep the stock boot flow by forcing the alloc path to
     return 0 (`file 0x1A764`: `mov w19,#1` `0x52800033` -> `mov w19,#0`
     `0x52800013`), so the caller does not enter the "tool present" branch -
     the gate is already open by then, so logging still works.

## 6. Reproducing the analysis

    # disassemble an image with correct VAs (0.0.1 = the kept version)
    aarch64-linux-gnu-objdump -D -b binary -m aarch64 \
        --adjust-vma=0x9EFFFE00 images/uboot-0.0.1.img > /tmp/001.asm

    # byte-level audit against the baseline (md5 + every differing word)
    python3 - <<'EOF'
    import hashlib
    base = open('images/uboot.img','rb').read()
    d    = open('images/uboot-0.0.1.img','rb').read()
    diffs = [i for i in range(len(base)) if d[i] != base[i]]
    print(len(d), hashlib.md5(d).hexdigest(),
          ['0x%X' % (x & ~3) for x in diffs])
    EOF

The discarded v25/v27/v28/v29 images can be rebuilt from git history
(`git show <rev>:images/uboot-v28-alloc-fast.img`), but nothing depends on them.

Quick reference for the addresses used above (file offsets):
`0xE798` puts entry, `0x1A69C` calibrate state machine, `0x1A710/0x1A720/0x1A754/
0x1A758/0x1A768` its patch sites, `0x1A780` tool read, `0x1A8BC` reply_to_pctool,
`0x1AA40` caller, `0x2CFFC` ring alloc, `0x2D1D0` ring write, `0x2D2F0` pump,
`0x2D3B4`/`0x2D3EC` polls, `0x30EA0` usb_gadget_handle_interrupts,
`0x3314C` TE printf, `0x34388` co5300 read-ID wrapper, `0x3445C` read-ID printf,
`0x30CC` backlight printf, `0x1BC34/0x1BC38/0x1BC4C/0x1BC9C` injected block,
`0x1BE34` where the gate really is.
