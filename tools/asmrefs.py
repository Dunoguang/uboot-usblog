#!/usr/bin/env python3
"""asmrefs.py - resolve IDA-listing string references against the real image.

The IDA export `analysis/uboot.asm` names strings (aXxx DCB "...") and refers
to them as `#(aXxx+off)@PAGE` / `@PAGEOFF`.  The label itself carries no
address, and the listing's address arithmetic is easy to misread, so this
tool rebuilds the truth from the binary:

  1. parse every `aXxx DCB "text"` label of the listing
  2. locate that text inside the image -> the label's true file offset
  3. resolve every `#(label+off)@PAGE` reference to a file offset and print
     the ACTUAL bytes found there

Usage:
    asmrefs.py uboot.asm uboot.img [grep]      # list resolved references
    asmrefs.py uboot.asm uboot.img --labels    # list label -> file offset
"""
import re
import sys


def parse_labels(asm_path):
    labels = {}
    line_re = re.compile(r'^([A-Za-z_]\w*)\s+DCB\s+(.*)$')
    str_re = re.compile(r'^"((?:[^"\\]|\\.)*)"')
    with open(asm_path, encoding='utf-8', errors='replace') as fh:
        for line in fh:
            m = line_re.match(line)
            if not m:
                continue
            q = str_re.match(m.group(2))
            if not q:
                continue
            s = q.group(1)
            s = (s.replace('\\n', '\n').replace('\\r', '\r')
                  .replace('\\t', '\t').replace('\\"', '"').replace('\\\\', '\\'))
            labels[m.group(1)] = s
    return labels


def build_index(img, labels):
    """label -> file offset.  Each key is scanned once, duplicates in order."""
    cache = {}
    used = set()
    out = {}
    for name, s in labels.items():
        if len(s) < 6:
            continue
        key = s[:16].encode('utf-8', 'replace')
        if key not in cache:
            occ = []
            start = 0
            while len(occ) < 64:
                i = img.find(key, start)
                if i < 0:
                    break
                occ.append(i)
                start = i + 1
            cache[key] = occ
        for i in cache[key]:
            if i not in used:
                used.add(i)
                out[name] = i
                break
    return out


def cstring(img, off, cap=120):
    end = img.find(b'\x00', off)
    if end < 0 or end - off > cap:
        end = min(off + cap, len(img))
    return ''.join(chr(b) if 32 <= b < 127 else '.' for b in img[off:end])


def parse_refs(asm_path):
    """[(lineno, label, off)] for symbolic ADRP/ADD pairs (register aware)."""
    refs = []
    pending = {}
    pat_adrp = re.compile(r'^\s*ADRP\s+([XW]\d+),\s*#\(([A-Za-z_]\w*)(?:\+([0-9A-Fa-fx]+))?\)@PAGE')
    pat_add = re.compile(r'^\s*ADD\s+([XW]\d+),\s*([XW]\d+),\s*#\(([A-Za-z_]\w*)(?:\+([0-9A-Fa-fx]+))?\)@PAGEOFF')
    pat_adrl = re.compile(r'^\s*ADRL\s+([XW]\d+),\s*\(([A-Za-z_]\w*)(?:\+([0-9A-Fa-fx]+))?\)')

    def num(x):
        return int(x, 16 if x.startswith('0x') else 10) if x else 0

    with open(asm_path, encoding='utf-8', errors='replace') as fh:
        for n, line in enumerate(fh, 1):
            m = pat_adrp.match(line)
            if m:
                pending[m.group(1)] = (n, m.group(2), num(m.group(3)))
                continue
            m = pat_add.match(line)
            if m and m.group(1) == m.group(2):
                got = pending.get(m.group(1))
                if got and got[1] == m.group(3):
                    refs.append((got[0], got[1], got[2]))
                    del pending[m.group(1)]
                continue
            m = pat_adrl.match(line)
            if m:
                refs.append((n, m.group(2), num(m.group(3))))
    return refs


def scan_pairs(img):
    """ADRP+ADD pairs decoded straight from the image -> (site, target)."""
    out = []
    pending = {}
    base = 0x9EFFFE00
    for off in range(0, len(img) - 8, 4):
        w = int.from_bytes(img[off:off+4], 'little')
        if (w & 0x9F000000) == 0x90000000:                 # ADRP
            rd = w & 0x1F
            imm = (((w >> 5) & 0x7FFFF) << 2) | ((w >> 29) & 3)
            if imm & (1 << 20):
                imm -= 1 << 21
            pc = off + base
            pending[rd] = ((pc & ~0xFFF) + (imm << 12)) & 0xFFFFFFFFFFFF
        elif (w & 0xFF800000) == 0x91000000:               # ADD Xd, Xn, #imm
            rd = w & 0x1F
            rn = (w >> 5) & 0x1F
            if rd == rn and rn in pending:
                tgt = pending.pop(rn) + ((w >> 10) & 0xFFF)
                out.append((off, tgt - base))
    return out


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return
    asm_path, img_path = sys.argv[1], sys.argv[2]
    arg = sys.argv[3] if len(sys.argv) > 3 else ''
    img = open(img_path, 'rb').read()
    if arg == '--scan':
        pairs = scan_pairs(img)
        print('ADRP+ADD pairs decoded from image: %d' % len(pairs))
        filt = sys.argv[4] if len(sys.argv) > 4 else ''
        hits = 0
        for site, tgt in pairs:
            if not (0 <= tgt < len(img)):
                continue
            s = cstring(img, tgt)
            if filt and filt not in s:
                continue
            hits += 1
            print('site 0x%06X -> 0x%06X  %r' % (site, tgt, s[:80]))
        print('shown: %d' % hits)
        return
    labels = parse_labels(asm_path)
    where = build_index(img, labels)
    print('labels parsed: %d, located in image: %d, image size: 0x%X'
          % (len(labels), len(where), len(img)))
    if arg == '--labels':
        for name in sorted(where, key=lambda k: where[k]):
            print('  0x%06X  %-30s %r' % (where[name], name, labels[name][:55]))
        return
    refs = parse_refs(asm_path)
    print('symbolic ADRP/ADD refs: %d' % len(refs))
    hits = 0
    for lineno, label, off in refs:
        if label not in where:
            continue
        tgt = where[label] + off
        s = cstring(img, tgt) if tgt < len(img) else '<oob>'
        text = '%s+%#x' % (label, off) if off else label
        if arg and arg not in s and arg not in label:
            continue
        hits += 1
        print('asm:%-7d %-32s -> 0x%06X  %r' % (lineno, text, tgt, s[:70]))
    print('shown: %d' % hits)


if __name__ == '__main__':
    main()
