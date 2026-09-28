# DW99 / vp19 u-boot internals

Source: `images/uboot.img` (484260 bytes, md5 `a03efc263613a61680e892261df90954`,
"U-Boot 2015.07 (Oct 28 2024 18:20:07)", sharklE).  All numbers are file
offsets in that image unless written as VA.

## 1. Addressing

The image is linked at 0x9F000000, but the first 0x200 bytes are a header:

    VA   = file + 0x9EFFFE00
    file = (VA - 0x9F000000) + 0x200

Example: the printf call site of "USB SERIAL PORT OPENED" is VA 0x9F01A568,
so the patch goes to file 0x1A768.  Writing it at 0x1A568 (forgetting the
+0x200) is exactly why v21..v24 never sent a byte.  Always re-verify a
patched word against the disassembly.

## 2. Image tail, [0x30], vboot (do not touch)

- u32 at file 0x30 = 0x75EF0.
- vboot computes `sechdr_offset = [0x30] + 0x200 = 0x760F0` and
  `cert_offset = sechdr + 0x60 = 0x76150` (both printed in the boot log).
- The tail from 0x76100 is a descriptor/signature table (its first field is
  also 0x75EF0), not padding.
- Changing [0x30] (v4/v5/v7/v12/v13) makes vboot read the wrong location ->
  reset loop about 3.3 s after power-on.  LCD init runs before vboot, so
  the screen turns on first ("screen lights up, then reboots forever").
- Control experiment: v20 changes one banner byte (0x57DF0 'U'->'V') and
  boots fine -> no image integrity check; failures are structural.
- The file length must stay 484260.

## 3. USB serial console flow (stock)

Without a host pctool:

    "USB SERIAL CONFIGED"
    bl 0x9F02D1EC            ; wait for "port open" (timeout in x1 = 2000 ms)
    cbnz w0, 0x9F01A558      ; opened -> allocation branch
    bl 0x9F01A400 + cbz ...  ; poll loop
    "usb calibrate port open timeout3871,1870,2000"
    b 0x9F01A56C             ; SKIPS the 8 KB channel allocation

Allocation branch (file 0x1A758, VA 0x9F01A558):

    bl 0x9F02CDFC            ; allocate the gserial ring buffer (8 KB)
    "USB SERIAL PORT OPENED" ; printf at file 0x1A768

So without a tool the 8 KB channel is never allocated and nothing can ever
be transmitted - the root cause of zero bytes on every reader for v1..v25.

The stock console TX path:

    reply_to_pctool(buf, len)  at file 0x1A8BC
      -> bl 0x2D1D0  write into the ring buffer
      -> if global 0x9F1CC0D0 set: printf "%s line %d usb trans with
         error %d\n"  (name "reply_to_pctool", line 381)
      -> w0 = 1; bl 0x2D2F0  (event pump, VA 0x9F02D0F0)

The pump waits with no timeout: file 0x2D314 (VA 0x9F02D114) is

    ldr w0, [x19]            ; x19 = 0x9F1CC118
    cbnz w0, done
    bl 0x9F030CA0            ; usb_gadget_handle_interrupts()
    b 0x9F02D114             ; loop, no timeout

so it only returns once the host has drained the ring.  That is why a reader
which stops reading freezes u-boot mid-boot (0.0.1 known issue).  Note the
file offset: 0x2D314, not 0x2D114 - the latter is the VA minus 0x9F000000
(the +0x200 trap again).

## 4. Where patch code may live: dead functions only

Free-looking zero regions are NOT safe: 0x5904C looks unused and has no
static references, yet using it (v19) killed the device - the region is
cleared or used at runtime.  Static emptiness does not imply safety.

The rule that works: put code in a function that normal boot never calls.
Scan for prologues (stp at 4-byte aligned addresses) that are (a) not a
bl/b target, (b) not present as an 8-byte pointer in the image, and
(c) preceded by a terminator (ret/b/fill).  35 candidates survived.

Chosen: the fastboot unlock/lock subcommand handler at **file 0x1BC34**
(680 bytes; strings "subcmd is null.", "unlock", "Not implemet.", "lock").
It only runs when a host sends that fastboot command.

Patch layout there:

| file | size | what |
|---|---|---|
| 0x1BC34 | 4 B | zeroed by the patch; **not** the byte the code gates on (see below) |
| 0x1BC38 | 20 B | trigger stub: set gate=1, tail-jump to the original printf |
| 0x1BC4C | 80 B | log fn: gate==0 -> return; clear gate; inline strlen; bl reply_to_pctool; set gate; ret |
| 0x1BC9C | 28 B | puts hook: recreate prologue, bl log fn, restore, jump to 0xE79C |
| 0xE798 | 4 B | puts entry -> b hook |

The hook recreates the prologue instructions it overwrites, because the
original code continues at 0xE79C with the stack already set up.

Proof that 0xE798 is the print path (v22): replacing it with `b .` hangs
the device before the logo appears.

### The gate byte is 0x200 off

The gate the injected code really reads, clears and restores is at **file
0x1BE34**, not 0x1BC34: the stub emits `adrp x1, 0x9F01B000` + `add x1, x1,
#0xC34` (= VA 0x9F01BC34 = file 0x1BE34), while the generator intended the low
12 bits of the file offset (0xA34).  Consequences:

- It is **not** in the same dead function.  File 0x1BE34 is inside the next
  dead function (the fastboot "unlock bootloader" volume-button confirm handler,
  file 0x1BEDC..0x1BF87, which prints "Press volume down button to confirm
  that." / "Press volume up button to cancel." / "Unlock bootloader fail.").
- That byte is the low byte of `add x0, x0, #0x7b7`, so a gate value of 1 turns
  the instruction into `add x1, x0, #0x7b7`.  It never runs: like the patched
  handler, that function has no branch target and no pointer reference in the
  image, so the corruption is latent, not live.
- It works because the trigger stub and the log fn compute the same (wrong)
  address, and because that byte happens to be 0 in the image.  Fix the
  constant (`0xA34`) before moving or extending the injected block; the address
  must stay consistent between the stub, the log fn and the trigger site.

Placement note: "USB SERIAL PORT OPENED" is printed from **two** places in the
image - file 0x1A768 (the live path, where the trigger stub sits) and file
0x1C60C, inside another unreferenced function (file 0x1C598) that also prints
"USB SERIAL CONFIGED" and "Waiting For USB SERIAL PORT OPEN ... ...".  Only the
first site is patched; a boot that reached the other one would never set the
gate and 0.0.1 would stay silent.

## 5. Patch recipe of version 0.0.1 (produced by patch/make_log_images.py)

    0xE798: A9BF7BFD -> 14003541   puts entry -> hook (file 0x1BC9C)
    0x1A768: 97FFD01C -> 94000534  "USB SERIAL PORT OPENED" printf -> trigger
                                   stub (file 0x1BC38)
    0x1A720: 350001C0 -> 1400000E  cbnz w0,+0x38 -> b +0x38 (force the 8 KB
                                   gserial channel allocation)

The image is rebuilt from the baseline with `assert`s on every original word
and an md5 self-check (`images/uboot-0.0.1.img`, md5 `9a1d5975...`).

Re-verified 2026-09-28: `python3 patch/make_log_images.py images/uboot.img <dir>`
reproduces the released image byte for byte, and a whole-image diff of the
release against `images/uboot.img` contains exactly four changed sites -
`0xE798`, `0x1A720`, `0x1A768` and the injected block `0x1BC34..0x1BCB7`
(132 B) - so the recipe above is the complete description of 0.0.1.

Earlier variants (v25 hook-only, v27 timeout-path allocation, v28 port-open
wait 0 ms, v29 allocation without logging) were discarded on 2026-09-28; what
they changed is recorded in `campaign-log.md`.
