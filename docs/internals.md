# u-boot internals: addresses, patch layout, and the one rule that matters

Everything here is measured against `images/uboot.img`
(484260 bytes, md5 `a03efc263613a61680e892261df90954`,
`U-Boot 2015.07 (Oct 28 2024 - 18:20:07 +0800)`, sharklE, SL8541E).

All numbers are **file** offsets unless written as `0x9F...`, which is a VA.

## 1. Addressing

The image is linked at `0x9F000000` but the first `0x200` bytes are a header:

    VA   = file + 0x9EFFFE00
    file = (VA - 0x9F000000) + 0x200

Forgetting the `+0x200` is *the* classic trap in this project, and it appears in
two different disguises:

- writing a patch at the wrong offset (the patch silently does nothing);
- building an `adrp`/`add` pair for an address in the injected code.  The low 12
  bits must come from the **VA**, so the gate byte at file `0x1BC34` is VA
  `0x9F01BA34` and the constant is `0xA34`, **not** the file offset's `0xC34`.
  Getting this wrong is silent and total: `images/uboot-0.0.1.img`'s predecessor
  shipped with `0xC34` and every read of the gate landed 0x200 too far.
  `patch/verify_image.py` checks all four `adrp`/`add` pairs for this.

## 2. Hard constraints - do not touch

- `u32 at file 0x30` must stay **`0x75EF0`**.  vboot derives the tail security
  header from it: `sechdr_offset = [0x30] + 0x200 = 0x760F0`, cert at `0x76150`
  (both are printed in the boot log as `sprd_get_vboot_key(): ...`).  Change it
  and the watch reset-loops about 3.3 s after power-on - and because LCD init
  runs *before* the vboot step, the screen lights up first, which makes it look
  like a display problem.
- The **file length must stay 484260**.
- The tail from `0x76100` is a descriptor/signature table (its first field is
  also `0x75EF0`), not padding.
- There is no image integrity check: changing one banner byte boots fine.

## 3. Where the console comes from

The DW99 has no usable UART (1.8 V vs 3.3 V levels), so this project borrows the
**U2S "calibrate" port** - the vendor channel a PC tool uses during factory LCD
calibration - and points u-boot's console at it.

`sub_9F01A49C` brings it up, with **two host-driven timed waits**:

```c
MEMORY[0x9F1BE008] = 3000;                  // wait #1 budget
start = get_timer();
while (1) {                                 // wait #1: USB configured
    if (sub_9F02D1B4()) break;              // [0x9F1CC11C] != 0
    if (elapsed() > 3000) { printf("usb calibrate configuration timeout,..."); return 0; }
}
printf("USB SERIAL CONFIGED\n");
MEMORY[0x9F1BE008] = 2000;                  // wait #2 budget
start = get_timer();
while (1) {                                 // wait #2: port opened
    if (sub_9F02D1EC()) break;              // [0x9F1CC190] != 0
    if (elapsed() > 2000) { printf("usb calibrate port open timeout..."); return 0; }
}
sub_9F02CDFC();                             // allocate the 8 KB gserial ring
printf("USB SERIAL PORT OPENED\n");         // <- the trigger site
return 1;
```

- `[0x9F1CC11C]` is set by the gserial bind/config callback, i.e. it needs the
  host to enumerate the device and send `SET_CONFIGURATION`.  A charger does not.
- `[0x9F1CC190]` is set **only** by `gser_setup` (file `0x2B668`), on a vendor
  class request `bmRequestType & 0x60 == 0x20`, `bRequest == 0x22`
  (`SET_CONTROL_LINE_STATE`), and only for **`wValue == 1`** (DTR asserted, RTS
  clear).  *Any other value, including `3` (DTR|RTS), clears the flag* - so a
  reader that asserts both lines actively prevents the channel from opening.
  Use `wValue = 1`.

If wait #2 times out, the stock code prints
`usb calibrate port open timeout<elapsed>,<start>,2000` and **skips the ring
allocation** - with no ring, no byte can ever reach the host.

## 4. The console TX path, and the one rule

     puts -> hook -> log fn -> reply_to_pctool(buf, len)   (file 0x1A8BC)
                                  -> sub_9F02CFD0  write into the 8 KB ring
                                  -> mov w0,#1 ; bl pump (file 0x2D2F0)

The stock pump:

```c
x19 = 0x9F1CC118;                       // TX-complete flag
for (;;) {
    if (*(u32 *)x19 != 0) { *(u32 *)x19 = 0; return; }
    usb_gadget_handle_interrupts();     // file 0x30EA0
}
```

`0x9F1CC118` is written by exactly one function - `gs_write_complete` (file
`0x2C464`), the IN-endpoint completion callback.  One completion corresponds to
**one 64-byte packet** (the bulk max packet size of EP 0x85).

> **The rule: never hand `reply_to_pctool` more than one max packet.**
>
> It ends in a single pump that waits for a single completion.  Past 64 bytes the
> completion count and the pump count stop matching, the ring never drains again,
> and - because the pump is bounded (see below) - u-boot carries on booting while
> printing into a ring nothing will ever read.  The silence is **permanent** and
> the console output on the device is completely unaffected, so the only symptom
> is "the log stops".

`images/uboot-0.0.1.img` therefore walks each string and calls
`reply_to_pctool` once per `<= 63`-byte piece.

### Evidence

With an earlier build the host received exactly 240 bytes and then nothing, while
the COM port stayed enumerated for another 4.8 s (u-boot finishing and jumping to
the kernel - so nothing was torn down and the link never reset):

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

Nine lines, all `<= 46` bytes; the tenth is the first one over 64 and it never
arrives.  Confirmed by a throwaway data-only image that shortened *only* that
format string (file `0x65259`) to a 16-byte line: it arrived, so did the two
short lines after it, and the stream stopped again at the next long line
(`uboot consume time:...`, ~70 bytes).

The result is not merely "more output" - it is the complete pre-kernel console
log, and it has been reconciled line by line against the device's own record:
see `README.md` section 3.

## 5. Patch layout of `images/uboot-0.0.1.img`

All injected code lives in the **never-called fastboot unlock/lock subcommand
handler body**, file `0x1BC34..0x1BED8`.

| file | what |
|---|---|
| `0xE798` | `puts` entry -> `b` to the hook.  The prologue word `A9BF7BFD` becomes a branch. |
| `0x1BC34` | the console gate byte; zeroed by the patch |
| `0x1BC38` | trigger stub `S1` (superseded by `S2`, kept as-is) |
| `0x1BC4C` | log fn `LOG` (superseded by `LOG2`, kept as-is) |
| `0x1BC9C` | the `puts` hook: recreate the prologue, `bl` the log fn, jump back to `0xE79C` |
| `0x1BCA8` | the hook's call -> `bl LOG2` |
| `0x1BCC0` | `bounded_pump` |
| `0x1BD00` | `usb_gate_wait` |
| `0x1BD50` | trigger stub `S2`: `usb_gate_wait`, set gate = 1, tail-call `printf` |
| `0x1BD80` | log fn `LOG2`: gate check, chunked send (39 instructions, ends `0x1BE1C`) |
| `0x1A720` | `cbnz w0,+0x38` -> `b +0x38`: force the 8 KB ring allocation even when wait #2 timed out |
| `0x1A764` | `mov w19,#1` -> `mov w19,#0`: do not advertise "tool present", which is what starts the 2 s pctool command read |
| `0x1A768` | the `printf` of `USB SERIAL PORT OPENED` -> `bl S2` (the trigger) |
| `0x1A8F0` | `reply_to_pctool`'s `bl 0x9F02D0F0` -> `bl bounded_pump` |

`LOG2` in pseudocode:

```c
if (gate == 0) return;            // S2 opens the gate
gate = 0;                         // re-entrancy guard
len = strlen(buf);
for (off = 0; off < len; off += 63)
    reply_to_pctool(buf + off, min(63, len - off));
gate = 1;
```

`bounded_pump` is why the boot can never be parked by the log:

```c
x19 = 0x9F1CC118;
w20 = 10000;
for (;;) {
    if (*(u32 *)x19 != 0) break;
    usb_gadget_handle_interrupts();
    if (--w20 == 0) break;
}
*(u32 *)x19 = 0;
```

`usb_gate_wait` (called by `S2` before the gate opens) is the other half:

```c
x20 = 0x9F1CC190;                 // gser "port opened"
x21 = 20000;                      // ms
start = get_timer();
while (*(u32 *)x20 == 0 && get_timer() - start < x21)
    usb_gadget_handle_interrupts();
```

u-boot's own budget for that wait is 2 s, and a Windows `sprdvcom` handle takes
about 3.9 s to open, so without this the channel is frequently never allocated
at all.  It exits the instant the host opens the port, so an attached reader
costs nothing, and without a reader the boot pays at most `WAIT_MS`.

## 6. Where patch code may live

Free-looking zero regions are **not** safe - `0x5904C` looks unused, has no
static references, and using it killed the device (the region is cleared or used
at runtime).  Static emptiness does not imply safety.

The rule that works: put code in a function that normal boot never calls.  Scan
for prologues (`stp` at 4-byte aligned addresses) that are (a) not a `bl`/`b`
target, (b) not present as an 8-byte pointer in the image, and (c) preceded by a
terminator (`ret`, `b`, fill).  35 candidates survive; the fastboot unlock/lock
subcommand handler is the one used here.  It only runs if a host sends that
fastboot command.

## 7. Traps worth writing down

- **`str`/`ldr` unsigned-immediate forms take bytes/8 in imm12.**  Writing
  `str x23,[sp,#0x30]` as `imm12 = 0x30` silently assembles to `[sp,#0x180]`
  and corrupts the frame.  `patch/verify_image.py` disassembles the block, so
  this is caught before flashing.
- **The gate constant is the VA's low 12 bits** - section 1.
- **Never call `printf` from inside the console path.**  A build that printed a
  marker from the pump needed ~0x520 bytes of stack under `puts -> hook -> log fn
  -> reply_to_pctool -> pump` and hung the watch with a dark screen.  Markers for
  device-side diagnosis must go out through the ring, not through `printf`.
- **`reply_to_pctool` also prints**, through the error path guarded by the global
  `0x9F1CC0D0` (`printf "%s line %d usb trans with error %d\n"`).  The gate is
  cleared around the call precisely so that re-entry stops there.
- The direction that can block is **device -> host only**.  Every host -> device
  wait has a 3000/2000 ms budget and prints a timeout line.

## 8. The `uboot_log` partition cannot debug a failed boot

`/dev/block/mmcblk0p11`, 4 MB, is `header (magic 0xABCD, data offset 0x200, N
entries) + N x 256 KB slots`; one slot per recorded boot.  Each slot is the
complete pre-kernel u-boot console log, and every slot ends with
`init_log_partition_hdr(): init log partition header sucess!` - the last line
u-boot prints before the kernel.

**A slot is only written when the boot completes successfully.**  A boot that
stalls or resets at any step, including before the kernel jump, leaves no record
at all.  That is the whole reason this project exists: for an unbootable watch,
the live USB log is the only channel.

It is still useful as ground truth for a boot that *did* complete - the sections
above were verified against it, and `tools/verify_capture.py` automates the
comparison.
