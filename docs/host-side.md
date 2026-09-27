# Host side: reading the log, the uboot_log partition, UART notes

## 1. What shows up on USB

With a patched u-boot running the console, the watch enumerates as:

    idVendor=1782 idProduct=4d00 bcdDevice=24.16
    Product: Gadget Serial
    Manufacturer: spreadtrum with musb-hdrc
    full-speed (12 Mbps)
    interface class 0xff (vendor), EP 0x85 = bulk IN, 64-byte packets

If you see bcdDevice=2.02 instead, that is the SPL / download / charge mode,
not u-boot (do not sleep on it - verify 24.16 before capturing).

## 2. Reading it (tools/usb_reader.py)

- libusb via ctypes, no third-party dependencies.
- claim interface 0, then send SET_CONTROL_LINE_STATE (DTR/RTS) once.
- read EP 0x85 in 64-byte chunks; write + flush + fsync every chunk so a
  hang never loses data.
- rc=-7 means the endpoint is alive but idle (no data yet);
  rc=-1 / submit -2 for a few ms around a disconnect is normal.
- If reads time out forever, look at the device side for
  `port open timeout`: the channel was never allocated (use v26 or newer).

## 3. uboot_log partition (offline, with a big caveat)

- `/dev/block/mmcblk0p11`, 4 MB = header (magic 0xABCD, data offset 0x200,
  entries N) + N x 256 KB slots; one slot per boot.
- **A slot is only written when the boot completes successfully.**  A boot
  that stalls or resets at any step (including before the kernel jump)
  leaves NO record.  Verified on hardware - so this partition MUST NOT be
  used to debug unbootable / stuck devices.
- Slots from successful boots contain lines such as:
  `rst_mode 40/0` (40 = reset by the PC flashing tool, 0 = normal),
  `is_7s_reset`, `USB SERIAL CONFIGED`,
  `usb calibrate port open timeout`, `battery unconnected shutdown charge`.
- Dump from recovery (adb root) with `tools/dump_uboot_log.sh`; parse with
  `tools/parse_uboot_log.py`.

## 4. UART reference (captured on a sibling watch)

A second watch (same u-boot family, panel icna3311, kernel 4.4.83) provides
a UART comparison - useful because the DW99 UART is electrically
unreachable (1.8 V vs 3.3 V levels):

- Same calibrate line: `usb calibrate port open timeout4080,2079,2000`.
- Same +0x200 rule: that image has [0x30] = 0x75C50 and prints
  `sprd_get_vboot_key(): sechdr_offset is 0x75e50`.
- Its u-boot console ends with `init_log_partition_hdr(): init log
  partition header sucess!` and the kernel follows immediately:
  `[0.000000] c0 Booting Linux on physical CPU 0x0`.
- Kernel early console: `earlycon: Early serial console at MMIO 0x70100000
  (options '115200n8')`.
- **sysdump**: the kernel prints
  `sysdump: [sysdump_magic_setup]SYSDUMP paddr from uboot: 0x85500000`
  and later `sprd_sysdump_write: start!!! / disable user version sysdump!!!
  / sprd_set_reboot_mode: disable sysdump! / set_sysdump_enable: get rst
  mode value is = f0 / sprd_sysdump_write: end!!!`.
  This is a crash-dump channel the kernel receives from u-boot, and a
  possible route to kernel-side logs on the DW99 (candidate partitions:
  `pm_sys`, `miscdata`).  **UNVERIFIED** - check whether it also only
  records successful boots (unlikely, it is a crash path) before relying
  on it.

## 5. Environment pitfalls (cost real time)

- The DW99 `uboot_log` only helps for successful boots (above); for failed
  boots the live USB console is the only channel.
- Vendor images may print `is_7s_reset 0x1000` / `sysdump_flag` - these are
  normal flags, not error markers.
- When capturing, filter on bcdDevice 0x24/0x16, otherwise the probe will
  hammer the 2.02 charge-mode device and print endless rc=-1.
