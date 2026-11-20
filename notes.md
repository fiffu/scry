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
