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
  `port open timeout`: the channel was never allocated (use version 0.0.1, `images/uboot-0.0.1.img`).

## 3. uboot_log partition (offline, with a big caveat)

- `/dev/block/mmcblk0p11`, 4 MB = header (magic 0xABCD, data offset 0x200,
  entries N) + N x 256 KB slots; one slot per boot.
- Cross-checked against the device partition table captured in
  `reference/disavb_tos_8541e.log`: index 11 is `uboot_log`, 4 MB, and the
  whole table has 38 entries (uboot = 9, uboot_bak = 10).
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

## 4. Reference material (UART log, sfd_tool session)

`reference/uart_readable.log` - a UART boot log captured on a second watch
(same u-boot family, panel icna3311, kernel 4.4.83).  It is useful because the
DW99 UART is electrically unreachable (1.8 V vs 3.3 V levels):

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

`reference/disavb_tos_8541e.log` - a download-mode (BROM) session with the
vendor `sfd_tool` (v2.6.18).  It is the record of how the unlocked baseline was
produced, and it is also where the partition map in these docs comes from:

- FDL1 is sent to `0x5000`, FDL2 to `0x9efffe00` - the second address is exactly
  the image base the VA/file rules in `uboot-internals.md` use;
- the GPT is read and all 38 partitions are listed (`uboot` 1 MB at index 9,
  `uboot_bak` at 10, `uboot_log` 4 MB at 11, `system` 3800 MB, ...);
- `trustos` + `sml` are dumped, `tos-noavb.bin` is built ("Disable AVB by
  patching trustos") and written to `trustos_bak` and `w_force`, with the
  original kept as `trustos-orig.bin`; the log notes "Device is not using VAB";
- raw data mode is supported but disabled by default for stability.

So `images/uboot.img` is a u-boot image for a device whose AVB has been
disabled this way - a patched u-boot on a device that still enforces AVB is a
different situation.

## 5. Environment pitfalls (cost real time)

- The DW99 `uboot_log` only helps for successful boots (above); for failed
  boots the live USB console is the only channel.
- Vendor images may print `is_7s_reset 0x1000` / `sysdump_flag` - these are
  normal flags, not error markers.
- When capturing, filter on bcdDevice 0x24/0x16, otherwise the probe will
  hammer the 2.02 charge-mode device and print endless rc=-1.

## 6. Container gotcha: /dev/bus/usb must be rebuilt after a container restart

Inside the LXC/container the /dev tree is a minimal snapshot.  After a
container restart there is NO /dev/bus/usb at all; `libusb_open*` then
returns NULL, and the reader loops forever printing only `listen ...s`.
Device numbers also change on every re-enumeration of the watch.

Fix: create the node from sysfs and keep it fresh - sysfs
`/sys/bus/usb/devices/1-1/dev` gives e.g. `189:18`, the node path is
`/dev/bus/usb/<bus>/<devnum>`:

    mknod /dev/bus/usb/001/019 c 189 18 && chmod 666 /dev/bus/usb/001/019

`tools/mk_usb_node.sh` does this in a loop for 1782:4d00 (run it with
nohup before the reader).  Symptom to remember: the reader prints only
`listen ...s` while `lsusb` already shows the device.

## 7. Windows: reading over the vendor COM port instead of libusb

On Windows the watch is claimed by `sprdvcom.sys`, which exposes the same
interface as a COM port (Ports class, `Service=sprdvcom` - **not** the USB
class, which matters if you are tempted to filter it).  `libusb_open` returns
`LIBUSB_ERROR_NOT_SUPPORTED` because the driver owns the device.

You do not need libusb, and you do not need UsbDk.  Open the COM port instead -
`tools/read_com_log.py` - but two details are not optional:

- **Keep a read URB armed at all times.**  Polling `BytesToRead` or using
  `ReadTimeout = 0` lets the driver's queue run dry and the log stalls early.
  The tool issues 1-byte `ReadFile` calls with all `COMMTIMEOUTS` zeroed, i.e.
  blocking until a byte arrives; `SetDTR` after opening is what makes `gser_setup`
  see `SET_CONTROL_LINE_STATE wValue == 1`.
- **Poll COM-port presence while you read.**  This is what distinguishes "the
  device stopped sending" from "the link went away", and it is how the 64-byte
  defect was found: the port stayed alive 4.8 s past the last byte, which ruled
  out a reset or a torn-down gadget.

The port only exists while u-boot runs - it is gone in Android and in recovery -
so start the reader *before* rebooting the watch; the tool waits up to 45 s for
the port to appear.

### 7.1 Flashing on Windows: `scc.exe` needs a real console

`scc.exe` (SPRDClientExample) draws its partition-write progress display with
`System.Console` cursor/window APIs.  Run it with stdout/stderr redirected to a
file or a pipe - which is what any scripted harness does - and those APIs throw
`IOException: The handle is invalid.` (zh-CN `句柄无效。`) **the moment the write
starts**, so every write aborts with `发生错误: 句柄无效。` right after
`开始写入uboot分区`.  Handshake, fdl1 and fdl2 all succeed; only the write dies.

Fix: give it its own console and scrape that console from outside.

    con = spawn_with_console([scc, 'fdl','1','0x5000', ...])   # CREATE_NEW_CONSOLE
    ConsoleCapture(con.pid).open()                             # AttachConsole + CONOUT$

`tools/concap.py` does exactly this (and hides the window with
`STARTF_WINDOW`/`SW_HIDE`, so the user sees nothing).  Note that
`ReadConsoleOutputCharacter` must be called **one row at a time**: a single
block read returns a short character count that is not a multiple of the row
width and cannot be sliced back into lines.

Same trap on the PowerShell side: a `.ps1` containing non-ASCII must be saved
with a BOM, because Windows PowerShell 5.1 reads BOM-less scripts as ANSI and
the Chinese string literals break the parser.
