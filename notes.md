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
