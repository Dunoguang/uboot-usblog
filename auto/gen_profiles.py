#!/usr/bin/env python3
"""Extract per-device profiles from the legacy generators into profiles/*.json."""
import importlib.util
import json
import os
import re

GENS = {
 'vp19':  dict(path='/root/github/uboot-usblog/patch/make_log_images.py',
               image='/root/github/uboot-usblog/images/uboot.img',
               content_end=0x76344, product='uboot-0.0.1.img',
               product_md5='aa780e3b519d59aa65979def9b83eb44'),
 'ai3':   dict(path='/root/usblog-patch-transplant-ai3/make_log_images_new.py',
               image='/root/usblog-patch-transplant-ai3/uboot.bin',
               content_end=0x76344, product='uboot-usblog.bin',
               product_md5='41edd1b4020fa166f6cf775e49b76b50'),
 'dw99':  dict(path='/root/usblog-patch-transplant-dw99/make_log_images_dw99.py',
               image='/root/usblog-patch-transplant-dw99/uboot-unlock-bootloader.img',
               content_end=0x76634, product='uboot-unlock-bootloader-usblog.img',
               product_md5='3eefc377c52a5af9ed6188602378e3a7'),
 'dw100': dict(path='/root/usblog-patch-transplant-dw100/make_log_images_dw100.py',
               image='/root/usblog-patch-transplant-dw100/uboot-unlock-bootloader.img',
               content_end=0x760A4, product='uboot-unlock-bootloader-usblog.img',
               product_md5='e5badc44d778ce613e91a09628f0c0c3'),
}

ANCHOR_KEYS = ['PUTS', 'PUTS_BODY', 'TRIG', 'FORCE_PORT', 'RET1', 'PUMP_CALL',
               'HOOK_BL', 'REPLY', 'PRINTF', 'GETTIMER', 'IRQ', 'STOCK_PUMP',
               'FLAG_TX', 'PORTFLAG', 'FLAG', 'S1', 'LOG', 'HOOK', 'PUMP',
               'GATE_WAIT', 'S2', 'LOG2', 'DEAD_END']

def anchor_value(m, src, key):
    if hasattr(m, key):
        return getattr(m, key)
    mm = re.search(r'PUMP_CALL\),\s*(0x[0-9A-Fa-f]+)', src)
    if key == 'STOCK_PUMP' and mm:
        return int(mm.group(1), 16)
    raise SystemExit('no attribute %s' % key)


for name, g in GENS.items():
    spec = importlib.util.spec_from_file_location('gen_' + name, g['path'])
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    src = open(g['path']).read()
    m30 = re.search(r'must stay (0x[0-9A-Fa-f]+)', src)
    d = open(g['image'], 'rb').read()
    real_end = max(i for i, b in enumerate(d) if b) + 1
    note = ''
    if abs(real_end - g['content_end']) > 0x100:
        note = ' NOTE real_end=%#x' % real_end
    prof = {
        'name': name,
        'image': g['image'],
        'image_len': len(d),
        'image_md5': m.BASE_MD5,
        'dhtb30': m30.group(1) if m30 else None,
        'anchors': {k: hex(anchor_value(m, src, k)) for k in ANCHOR_KEYS},
        'content_end': hex(real_end),
        'product_name': g['product'],
        'product_md5': g['product_md5'],
    }
    consts = {c: getattr(m, c) for c in ('CHUNK', 'BUDGET', 'WAIT_MS') if hasattr(m, c)}
    if consts:
        prof['constants'] = consts
    json.dump(prof, open('profiles/%s.json' % name, 'w'), indent=1)
    print('%-6s %3d anchors  [0x30]=%s  len=%d  real_end=%#x%s'
          % (name, len(prof['anchors']), prof['dhtb30'], len(d), real_end, note))
