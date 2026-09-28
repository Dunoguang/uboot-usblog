# Where the boot really stops (IDA review of the 0.0.1 patch)

Analysis date: 2026-09-28.  Tool: IDA Pro 9.4 / idalib (Hex-Rays), database built
from `images/uboot.img` wrapped as ELF at its true VAs
(`VA = file + 0x9EFFFE00`), so every address below is a *file* offset unless it
is written as `0x9F...` (a VA).  No byte-level scanning was used: functions,
cross-references, callers and loops all come from the IDA database.

## 0. Summary

`0.0.1` does not stop because of the panel or because the co5300 read ID never
completes.  **It stops inside its own console send path**: every `printf` is
routed through `reply_to_pctool()`, whose last step is an *unbounded* wait for
the USB IN-endpoint completion flag.  The panel read-ID path is bounded at every
level, and the unpatched baseline boots to the system on the same hardware, so
the panel is not the problem.

The last line the host received (`... not support TE`) is simply the last write
that completed.  The next console write on that code path - the co5300 read-ID
line, ~165 ms later - parks u-boot in the wait loop, with the display
half-initialised (backlight off, panel not yet read), which is exactly the
"screen stays dark, no further log" symptom.

## 1. Boot order: what prints what, and where the silence starts

Reconstructed from the strings and their call sites (all verified in IDA):

| order | code | what it prints | site |
|---|---|---|---|
| 1 | `sub_9F0182B8` | `lcd start init time:%dms` | call at `0x9F0182F4` |
| 2 | `sub_9F0182B8` -> `sub_9F00C070` -> `sub_9F0323E0` (`lcd_ctrl_init`) | - | - |
| 3 | `sub_9F0323E0` -> `sub_9F002EA8(0)` (backlight) | `sprd backlight power brightness=%d` | call at `0x9F032410`, print at `0x9F002EC0` |
| 4 | `sub_9F0323E0` -> `sub_9F032808` (**panel probe/adapt**) | - | call at `0x9F032438` |
| 5 | probe -> `sub_9F0326A4` (`panel_init`) -> `(*(ctrl+24))()` = `sub_9F032E88` (**mipi dispc init_config**) | `phy status0/1/2` (from `sub_9F033468`), then `sprdfb: mipi_dispc_init_config not support TE` | call at `0x9F032890`, TE print at `0x9F032EC4` |
| 6 | probe -> `*(*(panel+32)+16)` = `sub_9F0340B4` (**panel reset**) | - (GPIO 33/50 + 114 ms of delays) | indirect `BLR` at `0x9F032A34` |
| 7 | probe -> `*(*(panel+32)+96)` = `sub_9F034188` (**co5300 readid**) | `co5300_readid read id value is 0x%x,...` | indirect `BLR` at `0x9F0328B4`, print at `0x9F03425C` |

So after the TE line the **very next console write is the read-ID line** in
step 7.  The host received nothing of it, which means step 7 either never
reached its `printf`, or the `printf` never left the device.  Section 3 shows
why it cannot be the former.

The probe itself (`sub_9F032808`) walks the panel candidate table
`qword_9F055D68`: `0x05300206 -> descriptor 0x9F06BCE0` (co5300) and
`0x0000FFFF -> descriptor 0x9F06B780` (dummy/fallback).  The co5300
descriptor's control block at `0x9F06BC78` holds:

    +0   sub_9F0342AC   "uboot co5300_mipi_init"
    +16  sub_9F0340B4   panel reset (GPIO + delays)
    +96  sub_9F034188   readid

## 2. The panel read-ID path cannot hang (it is bounded everywhere)

`sub_9F034188` (readid), decompiled:

```c
v2 = panel->ctrl;                     // 0x9F06BC78
v3 = *(v2 + 64);  v4 = *(v2 + 72);    // DSI write / read callbacks
(*(v2))();                            // DSI context init (sub_9F03316C)
while (1) {
    v3(55, qword_9F06BC20, 16777218, 1);   // send the read-id command
    v6 = 51; while (--v6) delay_us(1000);  // 50 ms
    v4(4, 4, &id);                         // read 4 bytes
    printf("co5300_readid read id value is 0x%x,...", ...);
    if (byte0 == 0x33 && byte1 == 0x11) break;   // co5300 id
    if (--v5 == 0) return 0;               // v5 = 4 attempts, then give up
}
return 0x5300206;                          // matches the table entry 0x05300206
```

and below it every wait is bounded too:

- `sub_9F0348E4` (DSI write): all retries are `500`-iteration loops, returning
  `15` on timeout.
- `sub_9F034B44` (DSI read): two `500`-iteration loops, printing
  `tx rd command timed out` / `rx buffer empty` / `rx command timed out` and
  returning `0`.
- The register helpers (`sub_9F035B60`, `sub_9F035BA8`, `sub_9F035B0C`,
  `sub_9F035B28`) are single status-register reads (offset `0x98`/`0x70`), no
  loops of their own.
- `sub_9F0340B4` (panel reset) is `sub_9F025AE4`/`sub_9F025C20` GPIO writes plus
  21/21/21/51 ms delays; `sub_9F032614` (the fallback reset used by the dummy
  panel) is 21/21/121 ms delays.

A loop audit of the whole image (IDA instruction walk over 5234 backward
branches) plus a call-graph walk from the panel probe (18 functions, depth 8)
shows that every wait in that subtree is counter-bounded - the only loops that
call a delay are the candidate/delay loops of the probe itself - and that **no
function in the subtree references the USB gadget state page
`0x9F1CC000..0x9F1CD000`**: the display code neither disturbs nor services
USB.

## 3. What the patch adds: an unbounded wait on every console write

0.0.1 wires the console into the gserial endpoint:

    puts 0x9F00E598  ->  hook 0x9F01BA9C  ->  log fn 0x9F01BA4C
        ->  reply_to_pctool 0x9F01A6BC
              -> sub_9F02CFD0 (write into the 8 KB ring; queues TX only if the
                               port's tty handle is non-zero)
              -> sub_9F02D0F0 (pump) with w0 = 1

The pump is the problem, and it is called from exactly one place in the whole
image (`sub_9F01A6BC` = `reply_to_pctool`):

```c
// sub_9F02D0F0, file 0x2D2F0
x19 = 0x9F1CC118;                    // TX-complete flag (w0 = 1 selects this one)
for (;;) {
    if (*(u32 *)x19 != 0) { *(u32 *)x19 = 0; return; }   // seen a completion
    usb_gadget_handle_interrupts();                       // sub_9F030CA0
}
```

`0x9F1CC118` is written by exactly one function - `sub_9F02C464`
(`gs_write_complete`), i.e. the IN-endpoint completion callback - and that
callback only runs when the USB controller reports a completed transfer, which
requires the **host** to keep polling EP 0x85:

```
sub_9F02CDFC / sub_9F02D4D4  ->  sub_9F02CD18  (enable endpoints, queue RX+TX)
                             ->  sub_9F02C464  (gs_write_complete: [0x9F1CC118] = 1)
```

There is no timeout, no re-check of the "cable/tool present" state, and no check
of the send error global `0x9F1CC0D0`.  Consequences:

- If the host stops draining the endpoint, the **boot freezes** in whatever
  `printf` is next, not just the log.
- If the port's tty handle is zero when the ring write runs, `reply_to_pctool`
  queues nothing and then still waits for a completion that can never come -
  the same permanent hang, without any host involvement at all.
- The `+8 s` handshake delay that the forced "port opened" branch adds
  (section 5) is not the cause, but it changes *when* the console becomes
  host-dependent.

This is why the documented "the co5300 read ID never completes" is not
supported: the read-ID code prints and retries; the send of its line is what
stops.

## 4. Behaviour: the exact conditions that gate the boot

The stock USB-serial bring-up is `sub_9F01A49C`, and it contains **two timed
waits** before the patch site:

```c
MEMORY[0x9F1BE008] = 3000;                          // timeout for wait #1
start = get_timer();
while (1) {                                         // wait #1: USB configured
    if (sub_9F02D1B4()) break;                      // [0x9F1CC11C] != 0
    if (elapsed() > 3000) { printf("usb calibrate configuration timeout,..."); return 0; }
}
printf("USB SERIAL CONFIGED\n");
MEMORY[0x9F1BE008] = 2000;                          // timeout for wait #2
start = get_timer();
while (1) {                                         // wait #2: port opened
    if (sub_9F02D1EC()) break;                      // [0x9F1CC190] != 0
    if (elapsed() > 2000) { printf("usb calibrate port open timeout,..."); return 0; }
}
sub_9F02CDFC();                                     // <- 0x1A720 forces this line
printf("USB SERIAL PORT OPENED\n");                 // <- 0x1A768: trigger sets the gate
return 1;
```

Both flags are host-driven, and the writers are known:

- `[0x9F1CC11C]` (wait #1) is set by the gserial **bind/config callback**
  `sub_9F02D6D8` -> `sub_9F02D4D4`: it needs the host to enumerate the device and
  send `SET_CONFIGURATION`.  A plain PC does this; a charger does not.
- `[0x9F1CC190]` (wait #2) is set only by the class-request handler
  `sub_9F02B668` (`gser_setup`):

      if ((bmRequestType & 0x60) != 0x20 || bRequest != 0x22) return -EOPNOTSUPP;
      if (wValue_low == 1) [0x9F1CC190] = 1;   // DTR asserted, RTS clear -> "port open"
      else                 [0x9F1CC190] = 0;   // anything else -> "port closed"

  i.e. the port opens on `SET_CONTROL_LINE_STATE (0x22)` with **wValue exactly
  1**, and any other value - including `wValue = 3` (DTR|RTS) - *closes* it.

`tools/usb_reader.py` sends `wValue = 3`, so it closes the port on every attach;
that is why wait #2 always times out and why the `0x1A720` force was needed.

### 4.1 Behaviour table (0.0.1 image)

| host situation | what the device does | why |
|---|---|---|
| no host at all (battery, dumb charger, PC powered off) | wait #1 times out after 3 s, `sub_9F01A49C` returns early, the forced branch is **never reached**, the gate never opens: **the watch boots exactly like the stock image** (screen comes up, no log).  It does *not* hang | wait #1 needs `SET_CONFIGURATION` |
| PC attached, nothing reading EP 0x85 (no reader, or reader not started) | wait #1 passes, wait #2 times out, the patch forces the allocation, `USB SERIAL PORT OPENED` opens the gate - and then the **first log line blocks in the pump**, because the completion needs the host to read EP 0x85.  Result: **freeze before the LCD init**: dark screen, device dead, no further output.  Starting the reader later unblocks it (the already-queued IN request completes) | pump flag = IN completion |
| PC attached, reader running with today's `wValue = 3` | boot proceeds line by line at the reader's pace; **the moment the reader stops, the boot freezes at the next line** - the observed stop after the TE line | pump has no timeout |
| PC attached, reader asserting DTR only (`wValue = 1`) | wait #2 succeeds in milliseconds, the *stock* code allocates the channel itself and prints `USB SERIAL PORT OPENED` - the `0x1A720` patch becomes a no-op.  The blocking pump remains the only defect | `gser_setup` |
| holding/pressing any key or button | no effect on this path: there is **no live key wait**.  `Press volume up/down ...` belongs to the dead fastboot unlock handler, and u-boot's `Hit any key to stop autoboot` is a bounded countdown that runs before the gate opens | xrefs of the key strings |

Direction matters: **host -> device transfers never gate the boot** (every input
wait has a 3000/2000 ms timeout and prints a timeout line).  Only
**device -> host** can block, because only that direction lacks a timeout.

### 4.2 Host-side fix that comes out of this

The `wValue == 1` rule above means an unmodified u-boot (or a hook-only image)
*can* bring the console channel up by itself - v1..v25 never tried it, because
`usb_reader.py` always sent `wValue = 3` and therefore closed the port it was
trying to open.  Consequences for 0.0.2:

- send `SET_CONTROL_LINE_STATE` with `wValue = 1` (DTR only) in the reader;
- the `cbnz -> b` force at `0x1A720` is then unnecessary (kept only if the
  handshake cannot be relied on);
- the +8 s of handshake timeouts shrink (wait #2 becomes immediate), though the
  pctool input waits that follow `USB SERIAL PORT OPENED` still time out;
- the blocking pump must still be fixed (section 6) - the handshake does not
  touch that.

## 5. Patch review: each of the four 0.0.1 changes

| # | change | verdict |
|---|---|---|
| 1 | `0xE798` puts entry -> hook at `0x1BC9C` | sound; recreates the prologue and returns to `0xE79C`, and v22's `b .` control test proves it is the print path |
| 2 | `0x1A768` -> trigger stub `0x1BC38` (sets the gate, tail-calls the original printf) | sound; sets the gate on the live "USB SERIAL PORT OPENED" site.  Note a second, unreferenced copy of that print exists at file `0x1C60C` (dead function `0x1C598`) - a boot that took it would never set the gate |
| 3 | `0x1A720` `cbnz` -> `b` (force the ring allocation) | works - but it is now known to be **unnecessary**: with `SET_CONTROL_LINE_STATE wValue = 1` wait #2 succeeds and the stock code allocates the channel by itself (section 4).  Keeping it costs the 2 s wait #2 timeout and keeps the console bring-up host-dependent |
| 4 | injected log fn calling `reply_to_pctool` | **the defect**: a blocking send on a boot path.  The gate byte it uses is at file `0x1BE34` (0x200 off, inside the *next* dead function, on an `add` instruction) - latent, not the cause, but fix it before moving the block |

## 6. Fix direction for 0.0.2 (make the log best-effort)

The log must never be able to stop the boot.  Options, cheapest first:

1. **Bounded pump.**  Point `reply_to_pctool`'s call at file `0x1A6F0`
   (`bl 0x9F02D0F0`) at an injected `bounded_pump` that polls the same flag but
   gives up after N iterations (e.g. 10000) and returns.  Data still goes out
   whenever the host is reading; when it is not, the line is dropped and the
   boot continues.  This is a small, local change and keeps the verified
   behaviour of 0.0.1 while removing the deadlock.
2. **Skip the wait.**  Make the log fn call only the ring write
   (`sub_9F02CFD0`, file `0x2CFD0`): the transfer is queued and u-boot's other
   USB polling sites (`sub_9F02D1B4`, `sub_9F02D1EC`, `sub_9F02E388/478/4FC`)
   will complete it.  Cheapest, but the "USB SERIAL PORT OPENED" ordering
   guarantee becomes weaker.
3. **Take the channel without the handshake.**  Allocate the ring and enable the
   endpoints directly (call `sub_9F02CDFC`/`sub_9F02D4D4` from a place that does
   not run the 2 s + poll handshake) so the `+8 s` and the `usb read timeout`
   lines disappear.
4. Fix the gate constant (`0xC34` -> `0xA34`) while the block is being touched,
   and move the gate into the injected block itself so a future move is safe.

## 7. Experiments that would settle the remaining question

Static analysis cannot decide *why* the completion stopped arriving at that
particular line (host-side stall vs. device-side USB state); these images would:

- **E0 (new, cheapest of all)**: keep the *stock* `images/uboot.img`, run the
  reader with `SET_CONTROL_LINE_STATE wValue = 1`, and watch for
  `usb calibrate port open timeout` in the uboot_log partition afterwards: if
  the line is *absent*, wait #2 succeeded and the channel was allocated without
  any patch - which is the missing handshake behind v1..v25's silence.
- **E1 (decisive)**: 0.0.1 + bounded pump (fix 1).  If the boot then passes the
  TE line, reaches the co5300 read-ID line and the screen comes up, the stall is
  confirmed to be the blocking log path.
- **E2**: baseline + forced ring allocation only, no hook/trigger (the never
  tested "port-only" idea).  Proves change 3 alone does not disturb the boot.
- **E3**: 0.0.1 with the host reader *not* running at all, but the watch plugged
  into a PC.  Expect the freeze *before* the LCD init (section 4.1, row 2);
  unplugging will not help, starting the reader will.
- **E3b**: 0.0.1 on battery only - expect a normal boot with no log at all
  (wait #1 times out and the forced branch is never reached).
- **E4**: 0.0.1 with the reader running and host-side `dmesg -w` / `lsusb -t`
  watched: distinguishes a host USB reset/suspend at the stall point from a
  device-side stop.
- **E5**: 0.0.1 + reader logging timestamps of every zero-length timeout, to see
  whether the endpoint keeps NAKing (device alive, nothing to send) or stops
  answering altogether.

## 8. Reproducing this analysis

    mkdir -p /root/idawork && cd /root/idawork
    python3 tools/mk_ida_elf.py images/uboot.img uboot-base.elf     # throwaway wrapper
    PYTHONPATH=/root/ida/idalib/python python3 build_db.py uboot-base.elf
    PYTHONPATH=/root/ida/idalib/python python3 q.py uboot-base.elf.i64 decomp 0x9F02D0F0
    PYTHONPATH=/root/ida/idalib/python python3 q.py uboot-base.elf.i64 xrefs 0x9F1CC118

`build_db.py`, `q.py`, `loops.py` and `reach.py` are the throwaway helpers used
here (loop audit, call-graph walk, gadget-state check); they live outside the
repo on purpose - the shipped `analysis/uboot.asm` plus `objdump` reproduce the
same facts without them.
