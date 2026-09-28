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

Allocation branch (file 0x1A558):

    bl 0x9F02CDFC            ; allocate the gserial ring buffer (8 KB)
    "USB SERIAL PORT OPENED" ; printf at file 0x1A768

So without a tool the 8 KB channel is never allocated and nothing can ever
be transmitted - the root cause of zero bytes on every reader for v1..v25.

The stock console TX path:

    reply_to_pctool(buf, len)  at file 0x1A8BC
      -> bl 0x2D1D0  write into the ring buffer
      -> if global 0x9F1CC0D0 set: printf "%s line %d usb trans with
         error %d\n"  (name "reply_to_pctool", line 381)
      -> w0 = 1; bl 0x2D2F0  (event pump)

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
| 0x1BC34 | 4 B | gate byte (0 in the image) |
| 0x1BC38 | 20 B | trigger stub: set gate=1, tail-jump to the original printf |
| 0x1BC4C | 80 B | log fn: gate==0 -> return; clear gate; strlen; bl reply_to_pctool; set gate; ret |
| 0x1BC9C | 28 B | puts hook: recreate prologue, bl log fn, restore, jump to 0xE79C |
| 0xE798 | 4 B | puts entry -> b hook |

The hook recreates the prologue instructions it overwrites, because the
original code continues at 0xE79C with the stack already set up.

Proof that 0xE798 is the print path (v22): replacing it with `b .` hangs
the device before the logo appears.

## 5. Patch recipe of version 0.0.1 (produced by patch/make_log_images.py)

    0xE798: A9BF7BFD -> 14003541   puts entry -> hook (file 0x1BC9C)
    0x1A768: 97FFD01C -> 94000534  "USB SERIAL PORT OPENED" printf -> trigger
                                   stub (file 0x1BC38)
    0x1A720: 350001C0 -> 1400000E  cbnz w0,+0x38 -> b +0x38 (force the 8 KB
                                   gserial channel allocation)

The image is rebuilt from the baseline with `assert`s on every original word
and an md5 self-check (`images/uboot-0.0.1.img`, md5 `9a1d5975...`).

Earlier variants (v25 hook-only, v27 timeout-path allocation, v28 port-open
wait 0 ms, v29 allocation without logging) were discarded on 2026-09-28; the
analysis of why they did not help is in `v28-review.md`.
