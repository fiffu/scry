// Parses replays with Python (Pyodide) inside a Web Worker.
// Messages in:  { bytes: ArrayBuffer }           parse one replay
// Messages out: { type: 'ready' } once, then { type: 'done', json } per replay,
//               or { type: 'fatal', error } if startup fails.
// `json` is '{"rec": ...}' or '{"error": "..."}'. A broken replay never throws out of the worker.
import { loadPyodide } from './vendor/pyodide/pyodide.mjs';

const TMP = '/tmp/job.SC2Replay';
let parseFile = null;

async function init() {
  const py = await loadPyodide({ indexURL: new URL('./vendor/pyodide/', import.meta.url).href });
  // sc2reader and mpyq are plain Python source (libs.zip). scry.py is the parser.
  const libs = await (await fetch(new URL('./libs.zip', import.meta.url))).arrayBuffer();
  py.unpackArchive(libs, 'zip', { extractDir: '/libs' });
  const src = await (await fetch(new URL('./scry.py', import.meta.url))).text();
  py.FS.mkdirTree('/app');
  py.FS.writeFile('/app/scry.py', src);
  await py.runPythonAsync(`
import sys, json
sys.path.insert(0, '/libs'); sys.path.insert(0, '/app')
import scry

def parse_file(path):
    try:
        return json.dumps({'rec': scry.parse_replay(path)})
    except Exception as ex:
        return json.dumps({'error': '%s: %s' % (type(ex).__name__, ex)})
`);
  const fn = py.globals.get('parse_file');
  parseFile = bytes => {
    py.FS.writeFile(TMP, new Uint8Array(bytes));
    try {
      return fn(TMP);
    } finally {
      py.FS.unlink(TMP);  // keep the in-memory file system small
    }
  };
  postMessage({ type: 'ready' });
}

const ready = init().catch(err => {
  postMessage({ type: 'fatal', error: String(err && err.message || err).slice(0, 800) });
  throw err;
});

onmessage = async e => {
  await ready;
  postMessage({ type: 'done', json: parseFile(e.data.bytes) });
};
