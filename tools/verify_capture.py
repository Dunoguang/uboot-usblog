"""Verify the live USB capture against the device's own uboot_log slot.

The boot under test printed `lcd start init time:2281ms`; find the slot that
contains it (that is this boot), take everything from `USB SERIAL PORT OPENED`
to the end, and compare line by line with the 2608 bytes the host received.
"""
import struct

F = r'C:\Users\Administrator\Documents\deepseek-harness\default-workspace'
p11 = open(F + r'\p11-after-010.bin', 'rb').read()
cap = open(F + r'\usblog-010.bin', 'rb').read().decode('utf-8', 'replace')

n = struct.unpack_from('<I', p11, 0x0C)[0]
slots = []
for i in range(n):
    ln = struct.unpack_from('<I', p11, 0x1C + i * 12 + 0)[0]
    sz = struct.unpack_from('<I', p11, 0x1C + i * 12 + 4)[0]
    off = 0x200 + i * sz
    slots.append((i, off, p11[off:off + ln].decode('utf-8', 'replace')))

MARK = 'lcd start init time:2281ms'
hit = [s for s in slots if MARK in s[2]]
print("slots containing %r: %s" % (MARK, [s[0] for s in hit]))
if not hit:
    print("!! this boot wrote no uboot_log slot")
    raise SystemExit(1)

i, off, txt = hit[0]
print("using slot %d (off 0x%06X, %d bytes)" % (i, off, len(txt)))

# device side: from the port-opened line to the end
dev_lines = txt.splitlines()
start = next(k for k, l in enumerate(dev_lines) if 'USB SERIAL PORT OPENED' in l)
dev = dev_lines[start:]

host_lines = cap.replace('\r\n', '\n').split('\n')
while host_lines and not host_lines[-1].strip():
    host_lines.pop()

print("\nhost lines: %d   device lines (from port-open): %d\n" % (len(host_lines), len(dev)))

mismatch = 0
for k in range(max(len(host_lines), len(dev))):
    h = host_lines[k].rstrip() if k < len(host_lines) else '<missing>'
    d = dev[k].rstrip() if k < len(dev) else '<missing>'
    if h != d:
        mismatch += 1
        if mismatch <= 12:
            print("  line %3d DIFF\n     host: %r\n     dev : %r" % (k, h, d))

print("\n%d mismatched lines out of %d" % (mismatch, max(len(host_lines), len(dev))))
print("VERDICT:", "IDENTICAL - the live USB log is complete"
      if mismatch == 0 else "differences above")
