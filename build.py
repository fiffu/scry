#!/usr/bin/env python3
"""
build.py: write the static site that GitHub Pages serves.

    python build.py [OUT]        OUT defaults to ./site

Needs sc2reader (pip install -r requirements.txt). It downloads Pyodide and sqlite-wasm from the npm
registry once, checks their pinned sha512 hashes, and writes the page, the worker, the parser (scry.py),
libs.zip (the source of sc2reader and mpyq) and manifest.json into OUT. See DESIGN.md.
"""
import hashlib
import json
import os
import sys

import scry

# Browser SQLite (sqlite-wasm), fetched once and checked against a pinned hash
VENDOR = dict(
    url='https://registry.npmjs.org/@sqlite.org/sqlite-wasm/-/sqlite-wasm-3.54.1-build1.tgz',
    integrity='sha512-foW427VBLJ3G87bsnvQTZCc9zYe0Fq1wH0pz1I6BlKLKXcArIAyTkHtq0iT2llmhcMQt9T/p6l5PpQQh+6O8uQ==',
    files={'index.mjs': 'package/dist/index.mjs', 'sqlite3.wasm': 'package/dist/sqlite3.wasm'},
)
# Python in the browser.
PYODIDE = dict(
    version='314.0.8',
    url='https://registry.npmjs.org/pyodide/-/pyodide-314.0.8.tgz',
    integrity='sha512-74Wq1kXOC+CCEzAfxuRAO701nAcCMLjf8NrwvWpu8S41dVMXwXmLQ05dYB+F4e46AdnGo0oq7YHSVUpHsoarLQ==',
    files={n: 'package/' + n for n in ('pyodide.mjs', 'pyodide.asm.mjs', 'pyodide.asm.wasm',
                                      'python_stdlib.zip', 'pyodide-lock.json')},
)


def fetch_pkg(spec, dest, what):
    """Download an npm tarball once, check its pinned sha512, and extract the listed files into dest."""
    if all(os.path.isfile(os.path.join(dest, n)) for n in spec['files']):
        return True
    import base64
    import io
    import tarfile
    import urllib.request
    print('Downloading %s (one time)...' % what)
    try:
        req = urllib.request.Request(spec['url'], headers={'User-Agent': 'scry'})
        data = urllib.request.urlopen(req, timeout=120).read()
    except Exception as ex:
        print('Download failed: %s' % ex)
        print('Fetch %s and place these files in %s: %s' % (spec['url'], dest, ', '.join(spec['files'])))
        return False
    digest = 'sha512-' + base64.b64encode(hashlib.sha512(data).digest()).decode()
    if digest != spec['integrity']:
        print('Downloaded file does not match the pinned hash. Not using it.')
        return False
    os.makedirs(dest, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as tf:
        for name, member in spec['files'].items():
            with open(os.path.join(dest, name), 'wb') as f:
                f.write(tf.extractfile(member).read())
    return True


def ensure_vendor(vdir):
    """sqlite-wasm for the page. Returns True when both files are present."""
    return fetch_pkg(VENDOR, vdir, 'sqlite-wasm, about 1 MB')


def build_site(out):
    """Write the static site: the page, the worker, the parser, and everything the browser needs to run them."""
    import zipfile
    here = os.path.dirname(os.path.realpath(__file__))
    for need in ('page.html', 'worker.mjs'):
        if not os.path.isfile(os.path.join(here, need)):
            sys.exit('build.py needs %s next to it' % need)
    try:
        import sc2reader
        import mpyq
    except ImportError:
        sys.exit('build.py needs sc2reader installed (pip install -r requirements.txt): its source goes into libs.zip')
    out = os.path.abspath(out)
    os.makedirs(os.path.join(out, 'vendor', 'pyodide'), exist_ok=True)
    if not (ensure_vendor(os.path.join(out, 'vendor')) and
            fetch_pkg(PYODIDE, os.path.join(out, 'vendor', 'pyodide'), 'Pyodide %s, about 6 MB' % PYODIDE['version'])):
        sys.exit(1)
    with zipfile.ZipFile(os.path.join(out, 'libs.zip'), 'w', zipfile.ZIP_DEFLATED) as z:
        pkg = os.path.dirname(sc2reader.__file__)
        for dirpath, dirnames, filenames in os.walk(pkg):
            dirnames[:] = [d for d in dirnames if d != '__pycache__']
            for fn in filenames:
                if not fn.endswith('.pyc'):
                    full = os.path.join(dirpath, fn)
                    z.write(full, os.path.join('sc2reader', os.path.relpath(full, pkg)))
        z.write(mpyq.__file__, 'mpyq.py')
    for src, dst in (('page.html', 'index.html'), ('worker.mjs', 'worker.mjs'), ('scry.py', 'scry.py')):
        with open(os.path.join(here, src), 'rb') as f, open(os.path.join(out, dst), 'wb') as g:
            g.write(f.read())
    with open(os.path.join(out, 'manifest.json'), 'w', encoding='utf8') as f:
        json.dump(dict(parser_version=scry.PARSER_VERSION, schema=scry.SCHEMA, rec_tables=scry.REC_TABLES,
                       replay_fields=scry.REPLAY_FIELDS, pyodide=PYODIDE['version'],
                       sc2reader=getattr(sc2reader, '__version__', '')), f)
    print('Wrote static site to %s' % out)
    print('Serve it over HTTP, for example: python3 -m http.server --bind 127.0.0.1 -d %s' % out)


if __name__ == '__main__':
    build_site(sys.argv[1] if len(sys.argv) > 1 else 'site')
