# docs

- [`internals.md`](internals.md) - device side.  Addressing and the `+0x200`
  trap, the hard constraints (`[0x30]`, image length), how the console comes up,
  the console TX path, **the one rule: one max packet per `reply_to_pctool`
  call**, the full patch layout, where patch code may live, and the traps.
- [`host-side.md`](host-side.md) - host side.  Reading the log on Windows (COM)
  and Linux (libusb), checking a capture against the device's own record,
  flushing an image over adb or BROM, and the `scc.exe`-needs-a-real-console
  trap that makes scripted flashing fail.

Start with the [README](../README.md); it has the verified result, the quick
start and a troubleshooting table.
