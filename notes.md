# Notes

Findings and decisions made while building. Newest entries go at the bottom.

## Decisions from the user (after plan.md)

- Ship as ONE file for easy distribution. Modular source is fine if it helps.
- The index sorts by timestamp. Scry is a content manager, not a ranking tool. Rarity score is dropped as a sort. Tags may still show as chips.
- Reuse the folder picker from `_uimerge.py`: the user picks any folder under the root, and the tree shows only folders that hold replays.
- Commit early and often. Keep notes here.

## Build approach

- Source files: `scry.py` (server, parser, database) and `page.html` (the page).
- `make bundle` inlines `page.html` into `dist/scry.py`, so the shipped artifact is one file.
- When `scry.py` runs from source, it reads `page.html` from disk. This keeps editing easy.
- `_uimerge.py` stays untracked. It is the user's reference file from another project.
- Replays are not committed (`*.SC2Replay` is in `.gitignore`).

## M0: sqlite-wasm smoke test (Node, 2026-11-20)

- `@sqlite.org/sqlite-wasm` 3.54.1-build1 loads a database file built by Python's `sqlite3`.
- FTS5 with `tokenize='trigram'` works. Queries `stim`, `nuc`, `neural` found the right rows. A trigram query needs at least 3 characters.
- `sqlite3_deserialize` needs a plain `Uint8Array`. A Node `Buffer` fails with "Invalid heapForSize() size".
- `selectObjects()` returns objects with a null prototype.
- Files needed in the browser: `dist/index.mjs` (657 KB) and `dist/sqlite3.wasm` (879 KB). The module finds the `.wasm` next to itself.
- npm integrity for the package tarball: `sha512-foW427VBLJ3G87bsnvQTZCc9zYe0Fq1wH0pz1I6BlKLKXcArIAyTkHtq0iT2llmhcMQt9T/p6l5PpQQh+6O8uQ==`
- Still to check: the same load in a real browser (Chromium), once the page exists.

## Vendor files for a one-file tool

- A one-file tool cannot ship 1.5 MB of WASM and JS as separate files.
- Chosen approach: on first run the script downloads the pinned npm tarball, checks the sha512 integrity above, and extracts the two files into `.scry/vendor/`. After that it works offline.
- Fallback: the user can place `index.mjs` and `sqlite3.wasm` in `.scry/vendor/` by hand.

## Parser and database (first cut)

- `scry.py` scans the root folder, parses new or changed replays in a process pool, and writes `.scry/scry.sqlite`. 6 sample replays take 0.8 s in total.
- Child tables use an integer `rid`, not the 64-character sha256. Using the sha in every row made the file 3x bigger (1.77 MB vs 0.61 MB for 6 replays). Tag spans point at a tag id `tid` for the same reason.
- Size now: about 100 KB per replay. 200 replays would be about 20 MB. The server should gzip the download.
- `parser_version` in the `meta` table triggers a full rebuild when it differs from `PARSER_VERSION` in the code. Bump it whenever parsing or tagging changes.
- A moved or renamed file keeps its parsed data (matched by sha256). An identical copy in another folder is skipped and listed as a duplicate.
- A replay that fails to parse still gets a row with `error` set. The scan continues.
- Player identity is `region-subregion-toonid`. AI players fall back to `name:<name>`.

### Tag tuning after the first run

- `econ:floating-bank` (5000+ minerals for 8 samples) and `econ:fast-third` (before 3:20) still fire for about half the players in the samples. Many of these games are lopsided team games, so this may be correct. Re-check with more replays.
- The 2-rax rule needs the second Barracks before 2:30. A looser rule caught normal one-base play.
- Openers that match no rule get the tag `build:unnamed`. The evidence holds the structure order and the notation. This keeps the tag list short.
- Sample openers recognised: 12-pool, hatch-first, gas-first, reaper-expand, rax-expand, 2-rax-no-expand, gate-expand, forge-expand, 2-gate, plus `stargate-opener`.
- No cannon-rush appeared in the samples. The forge game (3 cannons at 2:00) was classed `forge-expand` because its cannons were near its own base. The rush distance check is untested on a real rush.

## Page, server and one-file build (built and browser-tested)

Tested in headless Chromium (snap) driven by puppeteer-core. Checks that passed:
- sqlite-wasm loads the Python-built file. FTS5 `trigram` works in the browser (M0 done): "nuke" finds `group:nuke` and `skill:TacticalNukeStrike` through synonyms.
- Build timing filter: "first Barracks before 0:45" gave 2 games, the same as a direct SQL count. Histogram slider natural 1:40–2:00 gave 4 games, the same as SQL.
- Notes, marks, user tags and note-text search work. Notes are keyed by file sha256 and also store the header fingerprint.
- Folder picker (copied from `_uimerge.py`): the tree lists only folders that hold replays. Picking a folder scopes the list and the histograms to it.
- Rescan adds new files and drops removed ones. Broken files show as error cards. Identical files are reported as duplicates, and the first path in sorted order wins.
- `python scry.py --bundle dist/scry.py` writes the one-file build (about 100 KB). It was run from an empty folder: it downloaded sqlite-wasm, scanned and served the embedded page.

Things learned:
- Warp-ins (Stalker, Zealot) fire `UnitInitEvent` like buildings. Filter with `unit.is_building`, or the build order fills with warp-ins.
- `UpgradeTo...` commands duplicate the morph events. They are dropped from build steps.
- The gzip response is about 3x smaller than the file: 610 KB became 199 KB for 6 replays.
- Child tables must use an integer key. See the parser notes above.
- `sqlite3_deserialize` needs a plain `Uint8Array` in Node. The browser accepted the same call.
- `pkill -f "scry.py ..."` kills the calling shell when the same command line contains the pattern. Use `pgrep -f "[s]cry.py"` in a separate command.
- Snap Chromium cannot write screenshots to `/tmp`. Use puppeteer, which writes the file from Node.
- Typing in the notes box scrolls the game view. A test that drags on the ruler must scroll back to the top first.

Design choices made without asking (change them if they feel wrong):
- The folder picker's root is the current folder, or `--root`. `.scry/` inside the root holds the database and the vendor files.
- The first run needs internet once for sqlite-wasm. Offline machines can copy the two files into `.scry/vendor/`.
- Search covers tag labels, synonyms, chat text and note text. Tags in one filter all must match (AND). The sidebar has no OR across tags yet.
- Clicking a tag cycles include, exclude, clear.
- With a player selected, tag filters only count tags that belong to that player (a checkbox turns this off).
- Game cards show each player's opener and a few game-level tags. Economy tags (floating bank, fast third, maxed out) show only on the game page.

Known gaps and follow-ups:
- 1v1, AI, custom and observer games are untested.
- The `errors` list from the scan (duplicates and parse failures) is only visible in the browser console and as error cards.
- The database download is the whole file on every change. Fine at 20 MB, slow beyond that.
- Scanning hundreds of replays: about 0.15 s each with 6 workers on this machine. Not measured at scale.
- `shape:comeback` is still not implemented.
- Timeline hover shows the nearest earlier stat sample, so values lag by up to 7 s.
- Marks can only be added by dragging on the ruler. No edit of existing marks.
- Cannon-rush detection was not exercised by any sample replay.

## Pyodide feasibility (Node only, 2026-11-21)

Question: could the whole tool run in the browser (Pyodide for parsing, sqlite-wasm for queries, no Python server)?

Measured with `pyodide` 314.0.8 (Python 3.14.2) in Node, running the unchanged `scry.parse_replay()`:
- Pyodide starts in about 1.5 s. `zlib`, `bz2`, `lzma` and `sqlite3` import without extra steps.
- `sc2reader` 1.9.0 and `mpyq` import and run as plain source copied into the Pyodide file system. Pillow is not needed.
- All 6 replays: 2.8 s in Pyodide against 2.0 s native (about 1.4x slower).
- Output is identical to native Python 3.10 for all 6 replays, after sorting each row list.
- Sizes: Pyodide core is 13 MB uncompressed (9.6 MB wasm, 2.5 MB stdlib zip, 1.2 MB loader). `sc2reader` + `mpyq` zip is 333 KB.

Not verified: Pyodide inside a Web Worker in a real browser, loading a replay chosen in the browser, memory use over hundreds of replays. Two attempts to start a local static server for that test were refused by an automatic permission check, so the browser test was not run.

Bug found by the comparison: unit tags came out in set order, so the output differed between runs (hash seed). Fixed with `sorted()`. The database content was never wrong, only the row order.
