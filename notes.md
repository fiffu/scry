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

Browser test (Chromium, Pyodide in a module Web Worker, files served as static files):
- The worker was ready in 2.0 s. That covers loading Pyodide, unpacking the `sc2reader` zip and importing `scry.py`.
- Parse time per replay: 0.26 to 0.86 s. All 6 took 2.6 s in total, about the same as native.
- Row counts match the native database (3262 stats rows across the 6 replays).
- The one console error was a 404 for the favicon.

Still not verified: replays chosen through a file picker or directory handle, memory use over hundreds of replays, several workers in parallel.

Environment note: a local static server started with the default bind address (all network interfaces) was refused three times by the automatic permission check, with the reason "Undermining Oversight". The same server bound to 127.0.0.1 was allowed at once. The cause is probably the network exposure, but this is a guess. Bind local test servers to 127.0.0.1.

Bug found by the comparison: unit tags came out in set order, so the output differed between runs (hash seed). Fixed with `sorted()`. The database content was never wrong, only the row order.

## Static site: Python in the browser (built and browser-tested)

`python scry.py --site site` (or `make site`) writes a folder that needs no Python server: `index.html` (this same `page.html`), `worker.mjs`, `scry.py`, `libs.zip` (sc2reader and mpyq source), `manifest.json`, and `vendor/` with sqlite-wasm and Pyodide. It is 15 MB. Serve it with any static server (`make serve-site`) or a static host.

How it works:
- One page, two backends. At startup the page asks `/api/status`. A server answers, and the page runs in server mode as before. Anything else (a static host answers 404) switches to browser mode.
- Browser mode: the user picks a folder. Chromium's `showDirectoryPicker()` gives a handle that is saved in IndexedDB. Other browsers use `<input webkitdirectory>` and must pick the folder again each visit.
- Parsing runs in up to 4 Web Workers. Each loads Pyodide, unpacks `libs.zip`, imports `scry.py` and calls the unchanged `parse_replay()`. The worker returns the record as JSON. Workers are stopped after each scan to free memory.
- The page writes the rows into the in-memory sqlite-wasm database. After a scan it saves the exported database in IndexedDB. The next visit loads it, and only new or changed files are parsed.
- `REC_TABLES` and `REPLAY_FIELDS` in `scry.py` describe how a record maps to tables. The Python `store()` and the JavaScript `storeRec()` both read them (the page gets them from `manifest.json`), so the two writers share one layout.
- FTS5 tables moved to the temp schema (`temp.fts_tags`, `temp.fts_text`). This keeps them out of the exported database.

Test results (headless Chromium, fake `showDirectoryPicker`, 6 sample replays plus broken files):
- Database content built in the browser is identical to the one built by the Python server: 6 replays, 12 table groups each, 0 differences (compared by file hash, with row ids removed).
- First scan: 8 files (6 replays, 2 broken) in 3.5 to 4.0 s, including worker start.
- Rescan with no change: 0.6 s, nothing parsed. A removed file disappears. A renamed file keeps its data without a re-parse. Hidden folders and non-replay files are ignored. The duplicate is reported.
- Reload: the database comes back from IndexedDB. Search, filters and the game page work on it.
- Scale: 120 distinct files (the 6 replays plus unique trailing bytes) parsed in 20.7 s with 3 workers, 0.17 s per replay. The saved database was 9.7 MB. 60 files gave 0 error rows. The main-thread heap was 93 MB.
- Fallback path (`<input webkitdirectory>`): works when files are given through a `DataTransfer`. Real `webkitRelativePath` handling is untested.
- Server mode still passes the earlier browser tests (histogram, notes, marks, same-player scope).

Not verified:
- The real `showDirectoryPicker()` dialog, permission prompts, and restoring the saved handle in the next session. A fake handle cannot be stored in IndexedDB, so this needs a manual check in Chrome or Edge.
- Firefox and Safari, including module workers.
- Memory of the workers themselves (each runs its own Pyodide). Only the main-thread heap was measured.
- Hosting on a real static host. The page needs HTTPS or localhost for `crypto.subtle`.
- A phone browser.

Things learned:
- `python3 -m http.server` binds to all interfaces by default. See the permission note above. Use `--bind 127.0.0.1`.
- Pyodide needs only 5 files from the npm package (`pyodide.mjs`, `pyodide.asm.mjs`, `pyodide.asm.wasm`, `python_stdlib.zip`, `pyodide-lock.json`). No separate packages: zlib, bz2, lzma and sqlite3 are included.
- The 404 for `/api/status` in the console on a static host is how the page detects that no server exists.
- `store()` in Python had a bug: a changed file at an existing path hit the unique path constraint. Fixed in both writers: the old row at that path is removed first.
- The `--bundle` one-file build is the server version. The static site needs the source folder (`page.html`, `worker.mjs`), so `--site` refuses to run from the one-file build.
- Pyodide's Python is 3.14 and the laptop's is 3.10. The parser output was identical.

## Docker build

- `Dockerfile` has three stages. `build` (python:3.12-slim) installs `requirements.txt` and runs `scry.py --site /site`. `site` is a `scratch` stage that only holds the output. `serve` (nginx:1.27-alpine) is the default and serves the site on port 80.
- `make image` builds the image, `make serve` runs it on 127.0.0.1:8000, and `make site` exports the static folder to `site/` with `docker build --target site --output site .`. None of these need Python on the laptop.
- nginx 1.27's `mime.types` maps `.wasm` but not `.mjs`. A module script served with the wrong type is refused, so `nginx.conf` sets `text/javascript` for `.mjs`. It also turns on gzip for wasm, js and json.
- `try_files $uri =404` returns 404 for `/`. It must be `try_files $uri $uri/ =404`.
- Tested: the container run on 127.0.0.1 passes the same end-to-end test as the local site (same results, same saved database size of 704512 bytes). `/api/status` returns 404, which is how the page picks browser mode.
- The exported `site/` equals the local build. Only `libs.zip` differs in bytes (zip timestamps). All 93 files inside it are identical.
- Python in the Docker build image is 3.12, the laptop's is 3.10, Pyodide's is 3.14. Parser output was identical on 3.10 and 3.14, and the site built in the 3.12 image worked.
- `.dockerignore` keeps replays, `.scry`, `site`, `dist`, `spike` and `_uimerge.py` out of the build context.

## What still needs Python

- At run time in the browser there is no Python install, but Python source still runs: `scry.py` and the `sc2reader` source (in `libs.zip`) are executed by Pyodide. So `scry.py` cannot be deleted.
- At build time something must fetch the pinned packages, zip the `sc2reader` source and write `manifest.json`. `scry.py --site` does this inside Docker.
- Only the server mode could be removed: `State`, `scan()`, `store()`, `build_tree()`, `make_handler()`, `bundle()`, `main()` serve and scan paths in `scry.py`, and the server branch in `page.html`.

## UI feedback round (user testing, 2026-11-21)

Changes:
- Welcome: no browser names. The line about not remembering the folder says "may not remember ... you may have to pick it again". Progress text never says Python: "Loading…" with a busy bar, "Loading N / N" with a filling bar, then "Loaded N new replay(s)".
- Removed the high-APM and low-APM tags. Removed the APM text from cards. Parser version is now 4, so saved databases rebuild.
- Cards: one column per team (team 1 left), players sorted by name without regard to case, a "won" or "lost" label per team. Each player has one colour everywhere (hue from a hash of the player id, so it never changes). Builds are pills next to the player. All pills apply their tag as a filter, and the click does not open the game.
- Facets: "Shape of the game" and "Spectacle" are merged into "Game highlights" (the tag families in the database are unchanged). On hover the count turns into `+`, then `−` for an included tag and `×` for an excluded tag. Tooltips describe the tag. The descriptions live in `TAG_INFO` and `SKILL_INFO` in `page.html`. Their numbers repeat the thresholds in `CFG` in `scry.py`, so update both together.
- Mode and matchup tags are gone. A matchup widget replaced them: each side has one row of P, T, Z buttons per player slot. The number of rows is the largest team size in the library. One side set means some team has those races. Both sides set means one team has the left races and a different team has the right races. Checked against independent SQL counts (5, 2 and 4 games).
- The "tags must belong to the selected player" checkbox is renamed "Only count what the selected player did", has a tooltip, and shows only while a player is selected.
- Timing ranges: names use the same style as the tag headings, the value range sits below the name, and a filled line joins the two handles.

Bug found while testing: an excluded tag vanished from the facet list, because the counts only cover games that still match. Selected tags now stay in the list with a count of 0, so they can be cleared there.

Limits and open points:
- Mixed team sizes: the matchup rows follow the largest team size, so a library with 1v1 and 2v2 games shows 2 rows. There is no way to filter by team size alone. A 1v1 game can only match row 1 selections.
- The matchup "one side set" case matches either team. It does not know which team is "left".
- Card layout: "stacked horizontally" in the feedback was read as teams side by side, with players listed top to bottom inside a team.
- Hover states were checked through computed styles of the pseudo-element. Appearance was checked only in light mode.

## UI feedback round 2 (2026-11-21)

Changes:
- Players: a collapsed section, closed by default. The list covers the whole folder, not the current result, so it does not shift while filtering. A selected player shows as an active filter chip.
- The NOT modifier is gone. Clicking a tag chip turns it on or off. Hover shows `+` to add and `−` to remove.
- Map is its own section directly under Matchup, in alphabetical order.
- Dates read `31 Jan 2026` on cards and on the game page. The full local date and time are in the tooltip.
- Tag order: builds, units, upgrades, skills, skill groups, game highlights, economy, social (mine last).
- Skill group labels are plural: macro abilities, creep tumors, detection abilities, nukes, stims, spells, mode switches, speed boosts. The labels live in `GROUP_LABEL` in `scry.py`.
- The `people` tags are now `social`. New tags `social:cyka` and `social:blyat` fire when a player types the word in chat (case does not matter, parts of words count). They belong to the speaker, with a span at each message, so the player scope option applies. `CHAT_WORDS` in `scry.py` lists them. None of the 6 sample replays contains either word, so only a unit test of `add_chat_word_tags()` covers this.
- Pills keep their size on hover. The count stays in place with transparent text and the `+` is drawn over it.
- Matchup buttons have a dim tint of the race colour when not selected.
- Parser version is now 5.

Checks: players closed by default and unchanged by a filter, no `neg` class anywhere, sidebar order, facet order, plural labels, date format, pill size equal before and after hover (135.6 x 26), tinted button colours. Server-mode regression and the static site test pass.

Skill groups as a quick selector (not built, needs a decision):
- Group membership is a hand-written table (`SKILL_GROUPS`). It does not need many replays, but it only covers abilities seen so far. A new ability from a new replay has no group until someone adds it. The replay data has no ability categories, so grouping cannot be learned from replays.
- Today a group tag already means "any ability in the group", computed when the replay is parsed.
- A true multi-select needs a rule for several skills at once. Tag filters currently AND. A quick selector wants "any of these". Choosing OR inside the Skills list would also change what two clicked skills mean.
- Other facet lists (builds, skills and so on) still reorder by count while filtering. Only the player list was made stable.

## UI feedback round 3: the game page (2026-11-21)

Skill groups are gone, because a hand-made table is a poor fit. Removed `SKILL_GROUPS`, `GROUP_OF`, `GROUP_LABEL`, the `group:` tags and the `grp` column of `casts` (the schema changed, so the parser version is now 6). The "quick selector" idea is dropped with them, so the OR-or-AND question is moot.

Game page changes:
- The back button is primary-coloured and sticky. It lives in a bar that is a direct child of the scrolling container (`.backbar`). Sticky only works there, not inside a small wrapper. `top:-14px` cancels the container's top padding. Without it, scrolled content shows above the bar.
- Players: same team columns as the library cards (name colour, race badge, clan, MMR, build pills). The APM column is gone. Build pills apply the filter and return to the library.
- "What stands out" is now "Highlights", a table of pill and description. The description is the tag's `evidence` text, now written by the parser in short form with real singular and plural (`plural()`, `times_text()` in `scry.py`). Example: "72% of value destroyed at 11:34", "2 town halls destroyed at 9:51, 12:17", "1 fight of 5,000+ value at 11:34".
- Timeline: APM is a line with a point per 30 s, joined by straight lines. A 30 s bucket with no actions is a real 0 point, not skipped. The "fights" lane label is now "losses".
- Skills: the panel is grouped by race. A click puts a row at the bottom of the timeline, one pip per use, coloured by player. Hovering a pip shows the skill name and player, and dims all other players' pips in every row. Skills used as library filters start switched on. Other active filters keep the thin "filters" lane under each player.
- Build orders: one table, one column per player, one row per second. Steps from different players at the same second share a row. The `#n` counter is gone. The supply number stays in front of each step. Matching conditions still highlight the cell. Only the first 10 minutes are shown.
- Chat: narrow time column, bold coloured names (the same colour as everywhere else), no row lines.
- A file name chip (`fileChip()`) sits left of the date on library cards and below the heading on the game page. The folder path is in the tooltip.

Bug found by testing: the invisible crosshair line followed the pointer and sat on top of the pip under it, so the pip never received hover (the tooltip showed, the dimming did not). The crosshair and the drag box now have `pointer-events="none"`.

Checked: 4 APM lines with 44 points each (zero buckets kept), no "fights" label, 49 pips for two skills in two player colours, others dim to 0.12 on hover and return to 1, build table cells equal the 195 steps in the database, chat time column 51 px wide, name colour the same in the players block and in chat, a pill on the game page returns to the library with the filter set, the sticky button stays at the same screen position after scrolling to the end. The static site test passes too.

Open points:
- Hardcoded lists remain: the ability noise filter (`NOISE_EXACT`, `NOISE_PREFIX`) decides what counts as a skill, `SKILL_INFO` and `TAG_INFO` hold tooltip text, and `SYN` holds search synonyms. A missing entry only loses a nicety, except the noise filter, which decides what counts as a skill.
- Marks are bookmarks on the timeline: a time range with a note, saved with the game in the browser. They may need a clearer name.
- The build table shows only the first 10 minutes, as before. There is no "whole game" switch yet.

## Build order table, second pass (2026-11-21)

- Times appear only in the left column. A player's cell holds the supply and the step. The subtitle reads "first 10 minutes only".
- The "key" row of timing chips is gone. A key timing is now a small label on the step that happened at that moment (natural, third base, first gas, first production, first tech, first army). Every timing except `max_supply` has a build step at exactly the same time and of the matching kind (checked on all 6 replays: 0 misses), so the match is exact. `max_supply` has no step, so it gets its own line, "Reached 200 supply", in that player's column (only if it happens within 10 minutes). All 24 labels in one game landed on the right step and column.
- Parser bug found by this: an Overlord counted as "army", so a Zerg player's first army unit was an Overlord at about 0:30 and `unit:Overlord` was a tag. Fixed with `NOT_ARMY`. The check must use the type at birth (`e.unit_type_name`). sc2reader's `u.name` is the unit's LAST type, so an overlord that later became an overseer reports `Overseer` and slipped through the first version of the fix. Parser version is now 7. The "first army unit" histogram and filter are now correct for Zerg.

## UI feedback round 4 (2026-11-21)

Timeline:
- The ruler is 36 px tall (was 18). `RH` in `drawTimeline()` holds the height, and the lanes, the fight bands and the crosshair all derive from it.
- A mark can start anywhere in the plot area, not only on the ruler. A drag shorter than 2 s is ignored, so a plain click opens nothing. Checked: a click does nothing, a drag across the middle of a lane opens the form and saves the mark.
- The "Skills" heading overlapped the first skill name, because the first row started at the heading's own height. The heading now gets its own 22 px line (measured: heading bottom 493, label top 505).

Library:
- The matchup hover uses `opacity` only. Before, the hover rules overrode the background, so hovering a selected button turned it back to its dim tint. Checked: a selected button keeps its solid colour at opacity 0.78 on hover.
- Units are listed fewest games first. Other categories still list the most games first.
- All tag categories start collapsed.
- The `+` and `−` on a pill are drawn with two gradient bars, not typed as text, so font metrics cannot push them off centre. Measured on a 4x screenshot: the cross is 9 x 9 px and within 0.4 px of the chip's vertical centre.

Game page:
- The stomp text now says who destroyed more: "Team 1 destroyed 4x more value than team 2". The team that lost less destroyed more, because its kills are the other team's losses. With a zero loss it reads "Team 1 destroyed 25,300 value, team 2 destroyed none". Parser version is now 8.
- The heading moved into the sticky bar (`.gamebar`). Row one is the back button, row two is the heading with the file chip and date on the right. The bar has a bottom line, since it is taller now. Checked: heading above-left, meta at the right edge on the same row, both still visible after scrolling 2500 px.

## UI feedback round 5 (2026-11-21)

- A mark is saved with the Enter key in the text box, the same as the "Add mark" button. Enter on empty text saves a mark with no text.
- The Skills and Build orders panels use one subtitle style (`.subt`, 12 px, muted): "click to show on the timeline" and "first 10 minutes only".
- Build order rows are more compact: no vertical cell padding, 12 px text, smaller key labels. Measured on a 154-row game: average row 26.5 px down to 18.9 px (single-step rows 16 px), table height 4103 px down to 2940 px. The taller rows are cells where one player did several things in the same second.
- Idea, not built: the player names at the top of the build table scroll under the sticky game bar. A sticky header row under the bar would keep them visible.

## UI feedback round 6 (2026-11-21)

- Timeline notes: the text of each note is drawn in a strip above the ruler (20 px, only when some note has text). A text may run on until the next note starts and is then cut with an ellipsis (about 6.4 px per character). A purple bar under the text shows the note's time range. The tooltip on the note band is gone. Tested with two notes: "the big push into t…" (cut, the next note starts soon) and "defense holds". Everything else in the SVG sits in a `<g transform>` below the strip, so the ruler and lanes did not need new coordinates.
- Wording: "mark" is now "note" everywhere on screen: legend "Your notes", hint "Drag anywhere on the timeline to add a note", button "Add note", list "Timeline notes", empty text "No timeline notes yet". The panel title is now "Notes (saved in this browser)". Saved data keeps its old field name `marks`, so notes saved earlier still load. The CSS class `.mark` and the element id `markform` also keep their names.
- `shape:one-big-battle` and `spectacle:big-fight` are removed (parser, tooltips). They often appeared together and added little. `CFG` lost `one_big_min`, `one_big_share` and `big_fight`. Fight detection and the shaded fight bands on the timeline are unchanged. Parser version is now 9.
- "town hall" is now "base" in all visible text: "2 bases destroyed at 9:51, 12:17", "Both teams lost a base around 7:23" and the two tooltips. Code names (`TOWNHALLS`) are unchanged. The two build rules that name the command center are specific on purpose.
- The game bar is one row: back button, heading, and the file chip and date at the right (`margin-left:auto`). It is 55 px tall (about 100 px before) and stays pinned. On a 900 px screen it wraps to two lines.
- Fixed while testing: on narrow screens the build table made the whole page scroll sideways by 11 px. It now scrolls inside its own panel.

## New replays: language, 1v1 and spam (2026-11-21)

Eight new replays arrived (1v1, 2v2, builds 98310, 98370 and 98745). The first report: "no skills detected" and race `?` on `2026-11-18 - (Z)Arenhaet VS (T)zar`.

Cause: that replay was recorded by a Russian-language game client. sc2reader's `play_race` is text in the client's language (`Зерги`, `Терраны`), so every comparison with `Zerg`, `Terran` or `Protoss` failed. The skills were found (the ability names are English: `SpawnLarva`, `SiegeMode`), but the Skills panel groups by race, found none, and showed nothing. The race badges showed `?`, the build rules and the first-production and first-tech timings did not run, and the matchup filter could not match the game.

Fix: `canonical_race()` in `scry.py` resolves each player's race once, in this order: the metadata `AssignedRace` (`Terr`, `Prot`, `Zerg`, the same in every language), the type of the starting base born at frame 0, `pick_race` (English, but `Random` for random players), and `play_race` last. A unit test covers six cases, including random players and missing metadata. All stored races are now English. The browser matchup filter matches independent SQL counts on the 14-replay library (11, 6, 7 and 4 games).

Related finds in the same replays:
- The map name was English in both Russian-client replays. Map names could still be localised in other replays. Not verified.
- The 2v2 `MEDOED` game had 20,456 `Gather` casts from three players. `Gather` (mining orders) is a generic order, not a skill. The skill filter now also drops `ScanMove`, `SetUnitRally`, anything ending in `Attack` (`BattlecruiserAttack` is an attack order) and anything starting with `Transform`, `Root` or `Uproot` (warp gate and crawler relocation). Casts went from 22,007 to 1,479 across the 14 games, and the database is 868 KB.
- The skill filter is a hand-kept list. Every new patch or unit can add another generic order. Treat an odd skill in the list as a sign to extend `NOISE_EXACT`, `NOISE_PREFIX` or `NOISE_SUFFIX`.
- The `MEDOED` game has no result at all (`winner` is `None` for every player), so it was not a clean finish. Cards and the game bar show no won or lost label for it. This is handled without errors.
- A 198 s 2v2 on build 98370 triggered `cannon-rush` for one player. Checked by hand: a proxy pylon at 63 s, 173 units from their own base, a forge at 94 s, and cannons at 139 to 170 s that are 28 to 53 units from the enemy base. So the rule works on a real rush (first time it has seen one).
- Newer builds (98370, 98745) parse without trouble. Pyodide and native Python give identical output on all 14 replays (Pyodide takes about 1.5x as long). The static site parsed the Russian-client replay and the `Gather` replay correctly.
- 1v1 games: cards, game page, builds and the matchup widget all work. The matchup widget still shows two rows per side while the library holds 2v2 games.

## Rescan button removed (2026-11-21)

- The "Rescan" button and its handler are gone, because it did nothing visible. In the browser version, "Choose folder…" is the way to look for new, moved or removed replays: it runs the same incremental scan. Checked with a fake folder: one file removed, one moved and one broken file deleted, then "Choose folder…" gave the right 6 games. The old button started synchronously. "Choose folder…" waits for the picker first, so the text from the previous scan ("Up to date") stays for a moment.
- The Python server version (`make run`) now has no way to rescan while running. It scans on start, so new replays need a restart. The `/api/rescan` route is still in `scry.py` but nothing calls it.
- The "No replays found" message now says what works: choose the folder again (browser) or restart the server.
- A remembered folder is not scanned again when the page loads, because Chrome asks for permission on a user click. Scanning on load when permission is already granted would be possible. Not built.
- No unit tests for now. Everything tested so far was checked with throwaway scripts that are not in git.

## Minimap (2026-11-21)

What the replay records, and so what the map can show (checked on `Reclamation LE (2113)`):
- Structures: every placement has x and y, and every destruction has a time. This part is complete.
- Units: a spawn point, a death point, and "position snapshots" the game logs about every 10 s. In that game 183 of 312 army units appeared in at least one snapshot, about 3 each. So a dashed line joins two known points and is NOT the path the unit took. The legend and the canvas tooltip say so.
- Skills: commands aimed at a point or a unit carry x and y (1,178 of 1,479 casts in the 14-replay library: nukes, scans, injects, MULEs, blinks). Self-cast skills (siege, stim, burrow) have none and never appear on the map.
- Workers, overlords, larva and other junk are left out, so the map shows army units only. There is no terrain image: the replay holds none, only the map size.

Data (parser version 13): new tables `map_units` (uid, pid, name, born, died), `map_pts` (uid, t, x, y: birth, snapshots, death) and `map_structs` (pid, name, x, y, placed, destroyed), plus `x` and `y` on `casts`. The database grew from 868 KB to 1,452 KB for 14 replays, about 42 KB per replay. Pyodide and native Python still give identical output on all 14 replays.

On the page:
- The map sits beside the timeline (stacked below it on a narrow screen, sticky while you scroll). The bounds come from the data, with one scale so the shape is not stretched, and the game's y axis (up) is flipped.
- Playback: play and pause, a seek bar and a clock. I added a speed choice (1x to 32x, default 8x), because a 20-minute game at real time is not watchable. A moving line on the timeline (the playhead) follows the same time.
- Symbols: squares for buildings (bigger for a base), dots for units at their last known place, dashed lines from the previous known place, diamonds for skills. All use the owner's colour. A canvas cannot read CSS variables, so the colours are computed from the same hash with `--pl` read once.
- Fade: a symbol is brightest when new and loses brightness over 90 s of replay time (`FADE_S`). Floors: 0.18 for buildings, 0.15 for dots and 0.2 for skill diamonds, so the layout stays faintly visible. Dashed lines fade to nothing. Measured on a structure's own pixel: colour distance from the background 365, 247, 124, 68, 68 at 1, 30, 60, 100 and 300 s after placement.
- Skills show on the map only when switched on in the Skills panel. Toggling one redraws the page, but the replay keeps its time and keeps playing. Leaving the game stops it.
- The timeline skill pips are diamonds now (an SVG `path` with the same `pip` class, so the hover dimming is unchanged).

Checked in Chromium: layout, painted pixels grow over time (398, 895, 3,481, 7,863 at 10, 120, 400 and 900 s), 8x playback advances 12 s in 1.5 s, pause holds, the playhead moves, selecting a skill changes the map and deselecting restores it exactly, 24 diamonds and no circles on the timeline, the replay survives a redraw, and a fresh game starts paused. The map also renders from a database parsed in the browser by the static site.

Not built or not verified:
- No marker where a unit dies, so fights show only as dots disappearing.
- No toggles for workers or layers.
- The long straight dashed lines look odd when a unit's two snapshots are far apart. They are the data, not a drawing error.
- Dark mode, and the map on a phone-sized screen.
- Database size at 200 replays (about 8 MB more than before, by extrapolation).

## Minimap, second pass (2026-11-21)

Behaviour you asked for:
- Buildings and units that stand stay at full strength. When one is destroyed it becomes an X in the owner's colour, and the X fades out over 90 s of replay time (`FADE_S`). Movement lines and skill diamonds fade with their age (to nothing). Measured: a standing building keeps the same colour; a destroyed turret reads 421, 292, 147, 0 at 3, 30, 60 and 100 s after; a skill diamond reads 96, 80, 42, 0 at 1, 30, 60 and 95 s after the cast.
- The legend uses the symbols: ■ building, ● unit, ◆ skill, ✕ destroyed, ╌╌ movement, ◯ fight.
- Units looked still because the first version made each dot jump to its new position at the snapshot time. Facts from the data: the game logs a unit only when it has moved (0 of 563 and 0 of 315 consecutive known positions were the same place), about every 10.7 s (`T_TICK`), and about a quarter of the pairs are exactly one tick apart (continuous motion). So a dot now glides along its dashed line. If two known places are one tick apart it glides over the whole gap. If the gap is longer, the unit waits and glides during the last tick. This glide is a straight-line estimate, not the path taken, and the legend tooltip says so. Checked with pixels on an isolated unit: the dot sits on each estimated spot at the right time and not on the others (ink gains 27, 33, 37).

Flourishes (my choices; all can be dropped):
- Pop-up labels: every base going up or down, the first building of each type per player (a build order you can watch), and the skills switched on. At most 5 at once, stacked so they do not overlap, and lasting about 2.4 s of REAL time at any speed (`2.4 * speed` replay seconds, at least 6).
- Fight rings: a pulsing ring where a fight happens, sized by the value lost, fading for 12 s after the last death. The positions come from the fights table.
- Scoreboard under the map: per player a supply bar, supply, workers and army value, from the stats the game logs about every 7 s.
- Note captions: while the playhead is inside one of your timeline notes, its text shows across the top of the map. At the end of the replay the winner is shown.
- Keys: space plays and pauses, the arrow keys seek 5 s (shift: 30 s). Typing in a text box and the buttons keep their own key handling.

Size: the map is 480 px on its longest side (was 340). `K = 1.3` scales the symbols with it. The timeline keeps 940 px at a 1500 px window and drops under the map below about 1134 px.

Checked in Chromium: label, ring, caption and banner appear at the right times and are gone at other times (ink counted around the position), the scoreboard changes with time (BYD 200/218 supply and 8.5k army at 15:00), keys behave, and the static site renders the map from a database the browser parsed.

Not done or not verified: a legend entry for the labels, dark mode, a phone-sized screen, `roundRect` in older browsers (a plain rectangle is used there, untested), and the cost of the larger map on a slow machine (about 12,000 painted pixels at 15:00 on a 20-minute game, no frame problems seen in headless Chromium).

## Minimap, third pass (2026-11-21)

- Every icon has an outline (the theme's text colour at 70%: dark on the light theme, light on the dark theme), so icons stay readable when they pile up. Checked: the darkest pixel of a standing building is 54 against a fill of 88.
- A destroyed icon keeps its own shape and gets an X INSIDE it: a square with an X for a building, a dot with an X for a unit. So you can tell which one died. The X is drawn in the background colour, and the whole icon fades over 90 s. Checked: the centre of a destroyed building goes from brightness 88 to 175 (the X), the total colour in its window stays high for a few seconds (32,352 against 31,965 before) and then falls to 1,049 at +100 s. A destroyed unit's centre window holds 5 background-coloured pixels (the X) and fades to 0.
- Buildings are bigger (8 px, bases 13 px) so there is room for the X, and unit dots are 3.9 px.
- The play and pause button keeps a fixed size (44 x 34 px). The two symbols have different widths, so it used to change size.
- A unit's old dashed path no longer vanishes when the unit sets off again. The first version drew only the current move. Now every move that ended in the last 90 s is drawn and fades by its own age. Checked: with a unit travelling on to its next stop, the first path still shows at its midpoint (ink 4, against 0 once fully faded).

## Epic indicators (2026-11-21)

Effects on the minimap, drawn for a few skills once they are switched on in the Skills panel (same rule as the diamonds):
- Nuke (`TacticalNukeStrike`): while it counts down (14 s), a pulsing dashed red ring with a crosshair at the target. On landing: an expanding shockwave ring, a brief red flash across the map (20% at most, about 0.6 s of real time), a "nuke lands" label, and a dim blast disc (outer radius 8 game units, a stronger core at half that) that fades over 90 s like the other symbols.
- Scan (`ScannerSweep`): a dim blue disc (radius 13) for the 12.3 s a scan lasts, then it fades over 6 s.
- A base going down sends a shockwave ring in its owner's colour. This needs no skill switched on.

Honesty about the numbers: the replay records when a nuke was ORDERED, not when it lands. The 14 s countdown, the radii and the scan duration come from the game's rules, not from the replay. They sit in a small hand-kept table (`EPIC` in `page.html`) and only change how it looks. Add a skill by adding an entry.

Checked with pixels (colour added around the spot, effect on minus effect off): warning ring at +5 s (169,062), dim blast disc at +45 s after landing (71,778), gone at +100 s (557); scan disc while scanning (95,893) and gone 40 s later (1,785); a destroyed base's shockwave (421,245 at +1 s against 360,785 at +14 s); the red flash shows (corner pixel 705 to 636). One of my own probes is invalid on the landing frame: it measures distance from the canvas corner pixel, and the flash tints that corner too, so the number comes out negative. The landing frame was judged by eye on a screenshot instead.

A first flash in white did nothing on the light map, because the background is already near white. It is now the skill's own colour.

Ideas not built: more area skills (psionic storm, fungal, EMP: their radii are only 1.5 to 2.25 units, so about 3 px on this map), a mothership recall circle, making nukes show without switching them on, and a sound.

## Dark mode only, struck-through labels (2026-11-21)

- Light mode is gone. The palette is the dark one, set on `:root` with `color-scheme: dark`, and the `prefers-color-scheme` block is removed. Checked: a browser that asks for light still gets the dark page (background 26,30,34).
- Borders had poor contrast: `--line` was #394046 on a #23282d card (about 1.4:1). It is now #6b7681 (3.2:1 against a card, 3.6:1 against the page). Quiet dividers (table rows, the build-order separator, the team divider, note rows) use a new softer `--rule` (#394046), so the page does not turn into a grid of heavy lines. `--mut` was raised a little for the same reason.
- The pop-up label for a destroyed base is struck through and no longer starts with "✕". Checked: 42 label-coloured pixels in the row through the text while it shows, 0 before the base falls.
- The canvas fallbacks for `--pl`, `--bg`, `--fg` and `--win` were light-theme values. They are dark values now.
- My icon test assumed the X is brighter than the fill (true on light). On dark the X is darker: the centre of a destroyed building goes from 166 to 85. The probe reads "false", the picture is right.
- Not checked: the phone layout, and the static site build with the dark theme.

- Map icon outlines are a fixed dark colour (#0d1013, 90%) now. They were the pale text colour, which looked washed out on dark. A dark edge on a pale fill is crisp and keeps overlapping icons apart. Checked: darkest pixel of a standing building 55 against a fill of 166.

- The search suggestion box sits 6 px below the input (was touching it) and has 6 px of padding inside, so the first option is 13 px below the input instead of about 5.

- The incoming-nuke marker has a dotted crosshair now (round dots, reaching 2.2 ring radii past the target, with a centre dot) on top of the pulsing dashed ring. It shows until the nuke lands. Checked in a 3x zoom screenshot.

- More area skills get a dim disc on the minimap (once switched on): FungalGrowth (radius 2.25, 3 s), PsionicStorm (1.5, 2.9 s), EMPRound (1.5, 1 s), BlindingCloud (2, 11 s), ForceField (1.7, 11 s), TemporalField (3.5, 4 s), ParasiticBomb (3, 7 s). Radii and durations are from the game's rules as I remember them, NOT from the replay, so they may be slightly off. They only change how it looks. Only the nuke has the warning ring, the dotted crosshair and the shockwave.
- A disc is held for at least 1.5 s of real time, so a 3 s fungal is still visible at 8x speed. Checked on a fungal in a real game: the ring pixels are present at +1 s to +14 s and mostly gone by +20 s. A small residue (about 440 colour units) stays at the probe spot. I did not chase it.
- Casts per skill that have a map position, over the 14 replays: ChronoBoostEnergyCost 223, SpawnLarva 404, CreepTumor 91, CalldownMULE 85, ScannerSweep 61, TacticalNukeStrike 50, FungalGrowth 7, PsionicStorm 2, BlindingCloud 15, ParasiticBomb 13, ForceField 7. Fungal and storm are rare in this sample.

- A fight is a soft dark red stain on the map background now (a radial gradient, no ring and no pulse), drawn under every icon, bigger for more value lost, and fading over 12 s after the last death. It replaces the orange bobbing circle, which looked like an orbital scan. The legend shows a red block for it. Checked: ink around a fight is 2,434 during it against 1,115 forty seconds after, and the screenshot shows the stain under the icons.
- Orbital scans are not plotted: a scan is a different skill (ScannerSweep) and has its own blue disc once switched on. The orbital command's own scan cast is the same ScannerSweep, so it is already covered.

- The timeline's fight bands are the same dark red as the map stain now, with a gradient that is stronger in the middle and soft at the ends (it was a flat orange at 12%). The loss bars stay orange, so they still stand out against the red. Checked in a screenshot: all 14 fights in the test game are red bands.

- No gradient on the timeline: fight bands are a flat dark red (#8c1419 at 50%). The minimap stain is a flat dark red disc with a short fade at its rim (flat to 82% of the radius, then it fades out). Checked in screenshots.
- The APM line, its dots and its legend swatch are white now (they were teal).

- Movement lines fade in 25 s now (`LINE_FADE_S`), counted from when the unit arrives. They took 90 s. Icons that were destroyed and skill diamonds still take 90 s. Checked: the path of a finished move still shows 17 s after arrival, while the unit moves on (ink 4), and is gone at 40 s (ink 0).

- The nuke's dotted crosshair is in the colour of the player who ordered it, and its lines run across the whole minimap (edge to edge). The pulsing ring around the target stays red. Checked in a screenshot (a yellow player's nuke).

- The nuke crosshair is a solid dim line (1 px, 35% opacity) in the player's colour, not dotted. The centre dot stays brighter (90%).

- The nuke's dashed warning ring is in the player's colour too. The dim blast disc, the shockwave and the flash after landing stay red.

- Removed the "nuke lands" label and the dashed targeting ring. The nuke now shows: the player-coloured crosshair while it counts down, then the shockwave, flash and dim blast disc on landing. The cast label (the skill's name) still shows when the nuke is ordered. Checked: the effect is still present at impact (150,652) and gone by +100 s (95).

- The minimap legend no longer lists the fight stain; it reads "■ building ● unit ◆ skill ✕ destroyed ╌╌ movement".

- Destroyed buildings and units fade in 25 s now (`QUICK_FADE_S`), the same as movement lines. They took 90 s. Skill diamonds and the nuke's blast disc still take 90 s (`FADE_S`). Checked: a destroyed turret's icon window goes 13,295 (+3 s), 9,588 (+12 s), 412 (+30 s). A destroyed unit goes 9,216 (+2 s), 7,685 (+12 s), 0 (+30 s).

- The quick fade (movement lines and destroyed icons) is 12 s now, down from 25 s (`QUICK_FADE_S`). Checked: a destroyed turret goes 13,143 (+3 s), 9,755 (+6 s), 1,386 (+14 s). A destroyed unit goes 9,200, 7,453, 0. A finished move's path shows 6 s after arrival (ink 4) and is gone at 20 s.

- Skill diamonds and the nuke's blast disc fade in 30 s now (`FADE_S`), down from 90 s. The nuke's lingering blast disc has no border any more (the other area discs, such as scan and fungal, keep theirs). Checked: the nuke's effect is strong 10 s after landing (215,529) and gone at 40 s (0). The shockwave ring at the moment of landing is separate and stays.
