# Documentation index

| file | content |
|---|---|
| [uboot-internals.md](uboot-internals.md) | image layout, [0x30]/vboot, USB serial console flow, dead-code area, the gate-byte defect, VA<->file addressing |
| [campaign-log.md](campaign-log.md) | v1..v29 history: every change, every result, and the traps |
| [host-side.md](host-side.md) | USB descriptors, libusb reading, uboot_log partition, reference logs (UART + sfd_tool), sysdump lead |

Release notes: [`../CHANGELOG.md`](../CHANGELOG.md).

Working recipe: `../README.md`.  One-page summary: `../FINDINGS-2026-09-26.md`.
Raw material: [`../reference/`](../reference) (`uart_readable.log`,
`disavb_tos_8541e.log`) and [`../analysis/uboot.asm`](../analysis/uboot.asm).
