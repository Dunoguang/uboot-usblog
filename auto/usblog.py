#!/usr/bin/env python3
"""usblog-auto CLI - automatic anchor location + usblog patch injection.

    usblog.py find    <image> [--save anchors.json] [--quiet]
    usblog.py patch   <image> [--out out.img] [--anchors f|--profile n] [--quiet]
    usblog.py verify  <image> [--anchors f|--profile n] [--md5 HEX] [--len N] [--quiet]
    usblog.py audit   <base> <prod> [--anchors f|--profile n] [--md5 HEX]
                      [--content-end HEX] [--quiet]
    usblog.py regress

For patch/verify/audit, if no anchor source is given the finder locates the
anchor table automatically from byte-level evidence.
"""
import argparse
import hashlib
import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from lib import audit as audit_mod
from lib import builder, finder, verify as verify_mod

QUIET = lambda *x: None


def load_anchors(args):
    if getattr(args, 'anchors', None):
        a = json.load(open(args.anchors))
        return {k: (int(v, 16) if isinstance(v, str) else v) for k, v in a.items()}
    if getattr(args, 'profile', None):
        p = json.load(open(os.path.join(HERE, 'profiles', args.profile + '.json')))
        return {k: int(v, 16) for k, v in p['anchors'].items()}
    return None


def d30_of(d):
    return struct.unpack_from('<I', d, 0x30)[0]


def cmd_find(args):
    d = open(args.image, 'rb').read()
    a = finder.locate(d, log=QUIET if args.quiet else print)
    if args.save:
        json.dump({k: hex(v) for k, v in a.items()}, open(args.save, 'w'), indent=1)
        print('wrote %s (%d anchors)' % (args.save, len(a)))
    return 0


def cmd_patch(args):
    d = open(args.image, 'rb').read()
    log = QUIET if args.quiet else print
    a = load_anchors(args)
    if a is None:
        a = finder.locate(d, log=log)
    else:
        got = finder.locate(d, log=QUIET)
        bad = [k for k in got if a.get(k) != got[k]]
        if bad:
            print('ERROR: provided anchors disagree with the auto-located table:')
            for k in bad:
                print('  %-10s given %s auto %#x' % (k, hex(a.get(k, -1)), got[k]))
            return 2
        log('provided anchor table matches the auto-located table (%d anchors)' % len(got))
    prod = builder.build(d, a, d30_of(d), log=log)
    bad = verify_mod.verify(prod, a, d30_of(d), log=log)
    bad += audit_mod.audit(d, prod, a, d30_of(d), log=log)
    out = args.out or (os.path.splitext(args.image)[0] + '-usblog.img')
    open(out, 'wb').write(prod)
    print('%s  %d bytes  md5 %s  %s' %
          (out, len(prod), hashlib.md5(prod).hexdigest(),
           'ALL CHECKS PASSED' if bad == 0 else '%d FAILURES' % bad))
    return 1 if bad else 0


def cmd_verify(args):
    d = open(args.image, 'rb').read()
    a = load_anchors(args)
    if a is None:
        a = finder.locate(d, log=QUIET)
    bad = verify_mod.verify(
        d, a, int(args.dhtb30, 16) if args.dhtb30 else d30_of(d),
        expected_len=args.len, expected_md5=args.md5,
        log=QUIET if args.quiet else print)
    return 1 if bad else 0


def cmd_audit(args):
    base = open(args.base, 'rb').read()
    prod = open(args.prod, 'rb').read()
    a = load_anchors(args)
    if a is None:
        a = finder.locate(base, log=QUIET)
    bad = audit_mod.audit(
        base, prod, a, int(args.dhtb30, 16) if args.dhtb30 else d30_of(base),
        expected_md5=args.md5,
        content_end=int(args.content_end, 16) if args.content_end else None,
        log=QUIET if args.quiet else print)
    return 1 if bad else 0


def cmd_regress(args):
    import subprocess
    return subprocess.call([sys.executable, os.path.join(HERE, 'tests', 'regress.py')])


def main():
    ap = argparse.ArgumentParser(prog='usblog.py', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd')
    sub.required = True

    p = sub.add_parser('find', help='locate the anchor table in an image')
    p.add_argument('image')
    p.add_argument('--save', help='write anchors as JSON')
    p.add_argument('--quiet', action='store_true')
    p.set_defaults(fn=cmd_find)

    p = sub.add_parser('patch', help='find + build + verify + audit')
    p.add_argument('image')
    p.add_argument('--out')
    p.add_argument('--anchors')
    p.add_argument('--profile', help='use profiles/<name>.json anchors')
    p.add_argument('--quiet', action='store_true')
    p.set_defaults(fn=cmd_patch)

    p = sub.add_parser('verify', help='verify a built image')
    p.add_argument('image')
    p.add_argument('--anchors')
    p.add_argument('--profile')
    p.add_argument('--md5')
    p.add_argument('--len', type=int)
    p.add_argument('--dhtb30')
    p.add_argument('--quiet', action='store_true')
    p.set_defaults(fn=cmd_verify)

    p = sub.add_parser('audit', help='diff-containment audit')
    p.add_argument('base')
    p.add_argument('prod')
    p.add_argument('--anchors')
    p.add_argument('--profile')
    p.add_argument('--md5')
    p.add_argument('--content-end')
    p.add_argument('--dhtb30')
    p.add_argument('--quiet', action='store_true')
    p.set_defaults(fn=cmd_audit)

    p = sub.add_parser('regress', help='run the four-device regression')
    p.set_defaults(fn=cmd_regress)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == '__main__':
    sys.exit(main())
