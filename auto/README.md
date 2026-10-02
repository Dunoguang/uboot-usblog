# usblog-auto

Automatic anchor-location + **usblog** patch injection for the
SL8541e / SC9832e u-boot (DHTB) firmware family.

One tool replaces the four hand-ported per-device generators:

    python3 usblog.py patch <uboot.img>

(finds every anchor, builds the patch, verifies the image, audits the diff)

## What the patch is

The injection is the shipped 0.0.1 design: a `puts` entry hook gates console
output through a chunked bulk sender (<=63 bytes per transfer, one reply per
chunk), a bounded pump replaces the stock TX wait, and a trigger stub waits
(bounded) for the host to open the USB serial port.  All injected code lives
in a never-called fastboot subcommand handler body (verified dead:

zero external branches and zero pointers into it).

## Anchor location (byte-level evidence only)

No IDA / capstone / symbols.  Each step cross-checks the next:

| anchor(s) | method |
|---|---|
| `TRIG` | `"USB SERIAL PORT OPENED"` string -> `adrp+add` refs -> nearby `bl`, filtered by the atomic pair `[TRIG-4] == mov w19,#1` and `[TRIG-0x48] == cbnz w0,+0x38` (kills the decoy ref site) |
| `RET1` `FORCE_PORT` `PUMP_CALL` `REPLY` | rigid deltas from `TRIG` (-4, -0x48, +0x188, +0x154; stable across all known builds), re-checked by asserts |
| `PRINTF` / `STOCK_PUMP` | decoded `bl` targets of `TRIG` / `PUMP_CALL` |
| `FLAG_TX` / `PORTFLAG` | the two `adrp+add` data refs inside the stock pump are 0x80 apart; the lower one is `FLAG_TX`, `PORTFLAG = FLAG_TX + 0x78` |
| `PUTS` `GETTIMER` `IRQ` `REPLY` | masked word templates (dw99 reference): wildcard every layout-dependent word (`b/bl/adrp/cbz/...`), seed-search the stable runs, vote on the delta, score the full block |
| dead zone `FLAG`..`DEAD_END` | masked template + zero-external-ref scan + canonical tail `ret`; the in-zone layout is a fixed table (`S1 +4`, `LOG +0x18`, `HOOK +0x68`, `PUMP +0x8C`, `GATE_WAIT +0xCC`, `S2 +0x11C`, `LOG2 +0x14C`, `DEAD_END +0x2A4`) |

Addressing: `VA = file + 0x9EFFFE00` (`file = VA - 0x9F000000 + 0x200`).
Anchor outputs keep the legacy convention: `PUTS/TRIG/...` are file offsets,
`PRINTF/GETTIMER/IRQ/STOCK_PUMP/FLAG_TX/PORTFLAG` are VAs.

## Usage

    usblog.py find   <image> [--save anchors.json]        # locate only
    usblog.py patch  <image> [--out out.img]              # the full pipeline
    usblog.py verify <image> [--md5 HEX] [--len N]
    usblog.py audit  <base> <prod> [--content-end HEX]
    usblog.py regress                                     # four-device regression

`patch`/`verify`/`audit` accept `--anchors file.json` or `--profile <name>`
to pin a known table; everything is auto-located otherwise.  When a pinned
table is supplied it is cross-checked against the auto-located one.

## Known profiles

| profile | baseline md5 | [0x30] | product md5 |
|---|---|---|---|
| vp19  | a03efc263613a61680e892261df90954 | 0x75EF0 | aa780e3b519d59aa65979def9b83eb44 |
| ai3   | 9527c46bda13e468864f3c463f34ee37 | 0x75E90 | 41edd1b4020fa166f6cf775e49b76b50 |
| dw99  | 70caeb39d9cd69b44e42b58cb1e08373 | 0x76180 | 3eefc377c52a5af9ed6188602378e3a7 |
| dw100 | 29bf2c0adcc5f9a3e735836370769d5a | 0x75A90 | e5badc44d778ce613e91a09628f0c0c3 |

`python3 tests/regress.py` runs finder -> builder -> verify -> audit on all
four and byte-compares the products: **0 fails** means the unified tool is
fully equivalent to the four legacy generators.

## Files

    usblog.py             CLI entry
    lib/aarch64.py        encoders + decoders (pure python)
    lib/finder.py         automatic anchor location
    lib/builder.py        unified injected-code builder
    lib/verify.py         pre-flash verifier (branch-decode walk)
    lib/audit.py          diff-containment audit
    profiles/*.json       per-device anchor tables + dw99 template blobs
    gen_profiles.py       re-extract profiles from the legacy generators
    dump_templates.py     re-extract the reference templates
    probe_bytes.py / probe2.py / probe3.py   method-feasibility probes
    tests/                find_test.py, build_test.py, regress.py

## Provenance

Built on the uboot-usblog project (0.0.1 release) and the four
hand-ported device generators; the regression pins every output to the
historical byte-exact products, so this tool can replace them 1:1.
