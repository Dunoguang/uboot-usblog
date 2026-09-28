# analysis/ - disassembly listing

| file | md5 | what |
|---|---|---|
| `uboot.asm` | `d19970aa3435785dbbb2a083954e570d` | IDA Pro 9.4 listing, **true runtime base** (the corrected export) |

Baseline image: `../images/uboot.img`, md5 `a03efc263613a61680e892261df90954`
(484260 bytes, "U-Boot 2015.07 (Oct 28 2024 18:20:07)", sharklE).
Current release built from it: `../images/uboot-0.0.1.img` (see `../CHANGELOG.md`).

## Addressing

The blob has a 0x200-byte header in front of the payload:

    VA   = file + 0x9EFFFE00
    file = VA   - 0x9EFFFE00        (= (VA - 0x9F000000) + 0x200)

So the payload is linked at `0x9F000000` and the header sits at `0x9EFFFE00`
(that is also what vboot reports: `load_buf is 0x9efffe00`).  Code label names in
the listing embed the VA, e.g. `sub_9F01A49C` = file `0x1A69C` = the USB
"calibrate" state machine, `loc_9F01A558` = file `0x1A758` = the 8 KB gserial
ring allocation, `sub_9F00E598` = file `0xE798` = `puts`.

## Why the previous export was wrong

`analysis/uboot.asm` used to be exported with the raw blob loaded at base 0, i.e.
its addresses were file offsets.  That is fine for code addresses, but it made
every *data* reference land 0x200 too low, so the string labels were wrong:

| site | old export | corrected listing |
|---|---|---|
| file `0x1A768`, the "USB SERIAL PORT OPENED" printf | `ADRL X0, aLoglevel7 ; "loglevel=7"` | `ADRL X0, aUsbSerialPortO ; "USB SERIAL PORT OPENED\n"` |
| file `0x1A750`, the port-open timeout printf | `ADRL X0, (aLoglevel+1) ; "oglevel"` | `ADRP/ADD X0, aUsbCalibratePo ; "usb calibrate port open timeout%lu,%lu,%lu"` |
| file `0x3314C`, the display TE warning | `ADRP X0, #(aSprdfbMipiDisp+0x2C) ; "E\n"` | `ADRL X0, aSprdfbMipiDisp ; "sprdfb: mipi_dispc_init_config not support TE"` |
| file `0x343BC`, the co5300 read-ID result | `ADRP X0, #(aPhyStatus2X) ; "phy status2 %x"` | `ADRP/ADD X26, aCo5300ReadidRe ; "co5300 readid read id value is 0x%x,..."` |

The old file (md5 `c25cf8dba00e26bd3b501c0f84e736b6`, 134921 lines, file-offset
base) is still in git history; it is superseded and must not be used to look up
data references.

## How the listing was produced (and how to reproduce it)

The image was wrapped in a minimal ELF64/EM_AARCH64 container with two PT_LOAD
segments (`0x9EFFFE00` for the 0x200 header, `0x9F000000` for the payload) so
that IDA loads it at its true VAs and resolves every reference correctly.  The
wrapper is a throwaway - it must never be flashed:

    python3 - <<'EOF'
    import struct
    img = open('images/uboot.img','rb').read()
    H, T, N = 0x9EFFFE00, 0x9F000000, 0x200
    eh = b'\x7fELF' + bytes([2,1,1,0]) + b'\x00'*8 + struct.pack(
        '<HHIQQQIHHHHHH', 2, 183, 1, T, 64, 0, 0, 64, 56, 2, 64, 0, 0)
    p1 = struct.pack('<IIQQQQQQ', 1, 4, 0, H, H, N, N, 0x1000)
    p2 = struct.pack('<IIQQQQQQ', 1, 5, N, T, T, len(img)-N, len(img)-N, 0x1000)
    open('/tmp/uboot.elf','wb').write(eh + p1 + p2 + img[0xB0:])
    EOF

    # headless IDA 9.4 (idalib; the `idat` CLI wants a GUI/licence on this box)
    PYTHONPATH=/root/ida/idalib/python python3 - <<'EOF'
    import idapro, ida_auto, idc, ida_idaapi
    idapro.open_database('/tmp/uboot.elf', run_auto_analysis=True)
    ida_auto.auto_wait()
    print(idc.gen_file(idc.OFILE_ASM, 'analysis/uboot.asm', 0, ida_idaapi.BADADDR, 0))
    idapro.close_database()
    EOF

IDA leaves `*.i64`/`*.id0`/`*.id1`/`*.nam` files next to the input; delete them,
they are regenerable and not needed to use the listing.

## Cross-checking a reference

The listing has no per-line address column (IDA's assembler output only names
labels), so for anything that must be exact take the second opinion straight
from the image:

    aarch64-linux-gnu-objdump -D -b binary -m aarch64 \
        --adjust-vma=0x9EFFFE00 images/uboot-0.0.1.img | less
    # VA = file + 0x9EFFFE00 ; file = VA - 0x9EFFFE00
