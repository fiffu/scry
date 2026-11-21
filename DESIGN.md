# Scry: design

Scry reads StarCraft II replays and helps you find the games worth watching, for example to cut a highlights reel.
It runs entirely in the browser and is hosted on GitHub Pages (`fiffu.github.io/scry`).
Nothing is uploaded. Replays are read from a folder on your computer and stay there.

This file describes the tool as it is now. `notes.md` is the dated log of how it got here.
`plan.md` is the original plan and is out of date where it disagrees with this file.

## Contents

1. Goals and limits
2. Architecture
3. Files and build
4. Parsing a replay
5. Data model
6. Tags
7. Build orders
8. The library page
9. The game page
10. The minimap
11. Notes, history and links
12. Phone layout
13. Testing
14. Known limits

## 1. Goals and limits

Goals:
- Show a library of games with the facts that matter for a highlights reel: who played, the matchup, the opening, how the fights went, which skills were used.
- Let the user filter fast: by tag, unit, upgrade, skill, map, player, matchup, build timing, and by text in the chat.
- Show one game in detail: a timeline, a replay on a minimap, build orders, skills, fights, chat and the user's own notes.
- Make every claim traceable. A tag has an evidence line. A number on a screen is a number from the replay, or it is marked as an estimate.

Limits, chosen on purpose:
- No server. Hosting is static files on GitHub Pages.
- No upload. Parsing and searching happen in the browser.
- No account. Notes live in the browser (`localStorage`) and can be exported to a file.
- One page, no framework, no build step for the page. `page.html` is one file of HTML, CSS and a module script.
- Dark theme only.

## 2. Architecture

```
replays on disk
   |  showDirectoryPicker() handle (saved in IndexedDB), or <input webkitdirectory>
   v
page.html (main thread)
   |  sha256 of each file; skip files already in the database
   |  up to 4 Web Workers
   v
worker.mjs  (Pyodide = Python in WebAssembly)
   |  imports scry.py + sc2reader + mpyq (libs.zip), calls parse_replay(path)
   |  returns one record per replay, as JSON
   v
page.html: storeRec() writes the record into SQLite
   |  sqlite-wasm, in memory, plus temp FTS5 tables for search
   |  the database is exported and saved in IndexedDB after each scan
   v
Every filter, count, histogram and card is an SQL query.
```

Choices and reasons:
- **Parsing in Python, in the browser.** sc2reader is a Python library and there is no equivalent in JavaScript. Pyodide runs it unchanged. A parse takes about 1.5 times as long as native Python (about 0.2 s per replay). Pyodide output was checked to be identical to native output on all replays.
- **Web Workers.** Parsing must not freeze the page. Each worker loads its own Pyodide, so the page starts at most 4. They are stopped after each scan to free memory.
- **SQLite in the browser.** Filters combine many conditions (families, NOT, player scope, build timing, histogram ranges). SQL expresses these directly. sqlite-wasm has FTS5 with the `trigram` tokenizer, so a search for "stim" finds `UseStimpack`. The FTS tables are created in the `temp` schema, so they are not saved with the database.
- **IndexedDB for the database.** The exported database is saved after a scan. The next visit loads it, and only new or changed files are parsed. A different `PARSER_VERSION` throws the saved database away and re-parses everything.
- **Integer row keys.** Child tables use an integer `rid`, not the 64-character file hash. The hash in every row made the database three times bigger.
- **HTTPS or localhost is required.** `crypto.subtle` (the hash) and the File System Access API need a secure context.

Browser support: Chrome and Edge remember the folder (the handle is saved). Other browsers use a file input and must pick the folder again on each visit. Firefox and Safari were not tested.

## 3. Files and build

```
page.html        the page: library, game page, minimap, notes (HTML + CSS + one module script)
worker.mjs       Web Worker: starts Pyodide and calls parse_replay()
scry.py          the parser: parse_replay(), tag and build rules, SCHEMA, REC_TABLES
build.py         writes the static site (python build.py [OUT])
requirements.txt sc2reader (pinned)
Dockerfile       builds the site, then serves it with nginx
nginx.conf       serves .mjs files as JavaScript (nginx does not know the type)
Makefile         help (default), deps, site, dev, image, serve
.github/workflows/pages.yml   builds the site with Docker and deploys it to GitHub Pages
notes.md         dated log of decisions and findings
```

`build.py` writes:

```
site/index.html            = page.html
site/worker.mjs, scry.py   copied
site/libs.zip              source of sc2reader and mpyq (Pyodide runs them)
site/manifest.json         parser_version, SCHEMA, REC_TABLES, REPLAY_FIELDS, versions
site/vendor/index.mjs, sqlite3.wasm        sqlite-wasm 3.54.1-build1
site/vendor/pyodide/...                    Pyodide 314.0.8 (5 files)
```

- The vendor packages come from the npm registry and are checked against a pinned sha512 (`VENDOR` and `PYODIDE` in `build.py`). A download that does not match is refused.
- The site is about 15 MB.
- Deploy: a push to `main` runs the workflow. It builds with `docker build --target site --output site .`, checks that the key files exist, and publishes `site/`.
- Local work: `make dev` builds `site/` and serves it on `127.0.0.1:8000`. It needs sc2reader installed (`make deps`). `make site` and `make serve` use Docker instead.
- Python versions differ (Docker image 3.12, Pyodide 3.14). The parser output was identical on 3.10, 3.12 and 3.14.

## 4. Parsing a replay

`parse_replay(path)` in `scry.py` takes one file and returns a dict of rows. It raises on an unreadable file. The worker turns that into an error row, and the library shows it as a card with the error text. A broken replay never stops a scan.

Facts about the replay format that the code depends on (found by testing, so keep them):

1. **The clock.** `event.second` in sc2reader is `frame / 16`, which is wrong. The in-game clock is `frame / 22.4` (`FPS`). All times in the database are seconds on this clock, computed from the frame. The metadata `Duration` has the same error.
2. **Player stats** (`PlayerStatsEvent`) arrive about every 7.1 s: supply, workers, income, bank, army value, and the running totals of resources killed and lost.
3. **Deaths need filtering** before they count as losses. Skip units that cost nothing (larva, creep tumors, broodlings, locusts, interceptors, auto-turrets), suicides, and drones that die with no killer (a drone that becomes a building). With the filters, fight totals are within about 5% of the stat totals.
4. **APM.** Counting commands, selections and control groups gives 40 to 70% of Blizzard's number. Adding `CommandManagerStateEvent` and `UpdateTargetUnitCommandEvent` gets within about 9%. This fit is empirical. The Blizzard number (from `replay.gamemetadata.json`) is stored too.
5. **MMR** of `-36400` or any value at or below 0 means "no rating". It is stored as missing.
6. **Race is localised.** `play_race` is text in the language of the recording client (`Зерги`). `canonical_race()` resolves the race in this order: the metadata `AssignedRace` (`Terr`, `Prot`, `Zerg`), the type of the starting base born at frame 0, `pick_race`, and `play_race` last. Stored races are always English.
7. **Unit names.** `unit.name` is the unit's last type. An overlord that became an overseer reports `Overseer`. Use the type of the birth event (`unit_type_name`) when you need the original.
8. **Warp-ins and buildings.** Warp-ins fire `UnitInitEvent` like buildings. Filter with `unit.is_building`.
9. **Player identity** is `region-subregion-toonid`. Names can change. AI players have no toon id and use `name:<name>`.
10. **Player colour** is the slot colour the replay stores (`player.color`): Red, Blue, Teal, Purple and so on. It is stored as `#rrggbb`.
11. **No map image.** The replay holds only the map name, hash and size. The minimap draws coordinates on a plain background.
12. **Skills** are command events with an ability name. Generic orders are not skills. `NOISE_EXACT`, `NOISE_PREFIX` and `NOISE_SUFFIX` in `scry.py` list them (`Gather`, `ScanMove`, `SetUnitRally`, anything starting `Build`, `Train`, `Morph`, `Transform`, `Root`, anything ending `Attack`, and so on). The list is hand-kept. A strange skill in the list means a generic order is missing from the filters.
13. **Unit positions are sparse.** `UnitPositionsEvent` logs a unit only when it moved, about every 10.7 s (240 frames). A unit has about 3 known positions. Structures have exact placement and death.
14. **Only some commands have a place.** A skill aimed at a point or a unit has x,y (1,178 of 1,479 casts in the 14 sample replays). Self-cast skills (siege, stim) have none.

The thresholds that tags and rules use are all in `CFG` at the top of `scry.py`.

## 5. Data model

One SQLite database per browser. `SCHEMA` in `scry.py` creates it. `REC_TABLES` says how a parsed record maps onto tables: `(table, key in the record, number of columns after rid)`. The page reads both from `manifest.json`, so the Python and the JavaScript agree on the layout.

| Table | Content |
|---|---|
| `meta` | `parser_version` |
| `replays` | `rid`, `id` (sha256 of the file), `fingerprint`, `path`, `size`, `mtime`, map, mode, category, build, `played_at`, `duration_s`, `winner_team`, `error` |
| `players` | `rid`, `pid`, `toon`, name, clan, team, race, result, `mmr`, `apm_blizzard`, `apm_est`, `color` |
| `tags` | `tid`, `rid`, `pid` (null for the whole game), `family`, `tag`, `kind`, `label`, `evidence` |
| `tag_spans` | `tid`, `t0`, `t1`: where in the game the tag happened |
| `casts` | `rid`, `pid`, `ability`, `t`, `x`, `y` (skills only) |
| `build_steps` | `rid`, `pid`, `idx`, `t`, `supply`, `kind`, `name`, `nth` |
| `timings` | `rid`, `pid`, `key`, `t`: natural, third base, first gas, first production, first tech, first army unit |
| `fights`, `fight_losses` | fight windows with a place, and the value lost per team with its main units |
| `stats` | the player stats samples |
| `apm` | actions per 30 s bucket |
| `chat` | messages with time and sender |
| `map_units`, `map_pts`, `map_structs` | what the minimap draws (see section 10) |

Identity and caching:
- `replays.id` is the sha256 of the file. A moved or renamed file keeps its data.
- `fingerprint` is the start time plus the sorted toon ids. Two copies of one game share it.
- A file is parsed again only if its hash is new or `PARSER_VERSION` changed. Bump `PARSER_VERSION` in `scry.py` after any change to parsing or tagging.
- An identical file in another folder is a duplicate. The first path in sorted order wins.
- The saved database is about 60 to 80 KB per replay (14 replays: 868 KB, 120 replays: 9.7 MB).

## 6. Tags

A tag has a family, a name, a label, a kind (`exact`, `rule` or `inferred`), an optional player, optional time spans, and an evidence line that says why it fired.

| Family | Examples | How it is found |
|---|---|---|
| `skill` | `UseStimpack`, `TacticalNukeStrike` | command events, after the noise filter |
| `unit`, `upgrade` | `Ghost`, `Carrier` | tracker events (cosmetic upgrades are dropped) |
| `build` | `12-pool`, `gate-expand`, `cannon-rush`, `unnamed` | rules over the order of structures (section 7) |
| `shape` | `stomp`, `long`, `short`, `slugfest`, `back-and-forth` | game length and the fights |
| `spectacle` | `base-kill`, `base-trade` | town hall deaths |
| `econ` | `maxed-out`, `floating-bank`, `fast-third` | player stats and timings |
| `social` | `leaver`, `chat`, `cyka`, `blyat` | leave events and chat. `CHAT_WORDS` lists the watched words |
| `map` | the map name | header |
| `mine` | your own tags | notes in the browser |

Rules in plain words (thresholds are in `CFG`):
- `stomp`: one team lost at least 3 times the value of the other, and at least 3,000 in total.
- `slugfest`: 8 or more fights. `back-and-forth`: the side that lost the big fights changed at least twice.
- `base-trade`: both teams lost a base within 90 s of each other.
- `floating-bank`: 5,000 or more minerals unspent for about a minute. `fast-third`: a third base before 3:20.
- A fight is a run of 5 s buckets whose losses pass a threshold, merged when close. Its place is the value-weighted median of its deaths.

## 7. Build orders

A build order is a list of steps `(time, supply, kind, name)`. The kinds are structure start, first unit of a type, upgrade and morph. The opening window ends at 5:00 or the first fight, whichever is first. Supply at a step comes from the nearest earlier stats sample, so it can be off by 2 or 3. Time is exact, so filters prefer time.

Key timings stored per player: natural expansion, third base, first gas, first production building, first tech building, first army unit.

`classify_build()` names the opener from the order of structures. Examples: Zerg `12-pool`, `hatch-first`, `gas-first`, `3-hatch-before-pool`. Terran `reaper-expand`, `cc-first`, `1-1-1`, `2-rax-no-expand`. Protoss `gate-expand`, `nexus-first`, `forge-expand`, `2-gate`, `cannon-rush`, `stargate-opener`, `robo-opener`. A cannon rush needs the cannons to be within `rush_dist` of the enemy start. If no rule matches, the tag is `build:unnamed` and the evidence holds the structure order.

All thresholds are guesses from a small set of replays. Tune them with more games.

## 8. The library page

- **Cards**, newest first (sort can be flipped). A card shows the map, mode, length, the teams with race badges and build tags, the highlight tags, the file name and the date. A click opens the game. Names are not coloured here, because in a 1v1 everybody is Red or Blue.
- **Search box** (FTS5, trigram) over tag labels, hand-written synonyms (`SYN`, for example "nuke" and "nuclear") and chat text. It offers suggestions as you type.
- **Active filters** are listed at the top with a clear button.
- **Filters**: players (by toon id, with an option to count a tag only if that player did it), matchup (a row of race buttons per side), maps, build timing conditions (for example "Barracks, 1st, before 0:45"), histograms for key timings and game length (drag the handles), and tag families: builds, units, upgrades, skills, highlights (shape and spectacle), econ, social and your own tags. Families start collapsed.
- **Combining**: tags in a family narrow the result (AND). The counts next to every option update to match the current result.
- **NOT**: hold Shift and every tag chip shows "NOT". A click adds "NOT tag", which means games without it. A plain click on a NOT tag flips it to include, and a second one removes it. An excluded tag stays listed with a count of 0 so it can be undone. On a touch screen a NOT button does the same for one tap.
- **Matchup, players and tags** can also be set by clicking pills on a card or on the game page. On the game page that returns you to the library.

## 9. The game page

Header, then:
- **Teams** with race, MMR, result and build tags.
- **Highlights**: the tags of the game with the evidence.
- **Timeline** (an SVG). For each player: APM (squares joined by a line, one per 30 s), supply, and losses per stats sample. Fights are flat dark red bands across all lanes. A ruler gives the time. Skills you switch on in the Skills panel get a row of diamonds, one per use, in the player's colour. Hover shows the numbers at that time. Your notes are purple bands with their text above the ruler.
- **Minimap** next to the timeline (section 10).
- **Skills**, grouped by race. A click shows or hides a skill on the timeline and the map.
- **Fights** table: when, where, value lost per team, and the main units lost.
- **Build orders**, one column per player and one row per second, so things that happened together line up. Key timings are labels on the step. Only the first 10 minutes are shown.
- **Chat**.
- **Notes** (section 11).

Player colours on this page are the replay's slot colours. Dark ones (the Blue and the Purple) are made lighter (lightness at least 60%, same hue) so they can be read on a dark page.

## 10. The minimap

It replays the game on a plain coordinate map (no map image). Controls: play and pause, a seek bar, a speed list (1x to 32x), and keys (space, arrows, shift-arrows).

What is drawn, and how:
- **Structures** are squares from placement to destruction. A destroyed structure shows its square with an X inside, and fades in 12 s.
- **Units** (army units, not workers or overlords) are dots. Known positions are joined by dashed lines, which fade in 12 s after the unit arrives. When two known positions are one tick (10.7 s) apart, the unit glides between them. When they are further apart, it waits and then glides during the last tick. This is a straight-line estimate and not the path the unit took.
- **Icons** have a dark outline. Colours are the players' slot colours.
- **Skills with a place** show as diamonds, only if you switched the skill on. They fade in 30 s.
- **Fights** are a soft dark red stain, drawn under everything, that grows in from the first death, is bigger for more value lost, and fades 12 s after the last death.
- **Effects for a few skills** (`EPIC` in `page.html`), only when the skill is switched on: a nuke shows a crosshair across the whole map in the player's colour while it counts down (14 s), then a shockwave, a flash and a dim blast disc. Scan, fungal, storm, EMP, blinding cloud, force field, temporal field and parasitic bomb show a dim disc for as long as they last. A base that dies sends out a shockwave. The radii and durations come from the game's rules and are not in the replay, so they only change how things look.
- **Labels** pop up for new bases, the first building of each type, and the skills you switched on (at most 5 at once). A destroyed base's label is struck through.
- **Scoreboard** under the map: supply and cap with a bar, army value and worker count per player, from the latest stats sample. The cells have fixed widths, so nothing moves as the numbers change.
- **Captions**: your note covers the current moment, and at the end the winners' names.

Everything on it is a record of what the replay says, or is marked as an estimate. A canvas cannot read CSS variables, so `makeMap` reads the few colours it needs once with `getComputedStyle`.

## 11. Notes, history and links

- **Notes** are stored in `localStorage` (key `scry.notes.v1`) as `{ replayId: { fingerprint, text, tags, marks: [{t0, t1, text}] } }`. They are found by the file hash, and by the fingerprint if the hash is missing. They are also searchable. Export and Import in the header save and load them as a file. Clearing the browser data deletes them.
- **Adding a note**: on a computer, drag anywhere on the timeline (a drag shorter than 2 s is ignored) and type the text. Enter saves. On a touch screen, tap Add note, tap the start, tap the end. You can scroll between the two taps.
- **History**: opening a game pushes `#game=<id>` (the file hash). The browser Back button returns to the library, and Forward returns to the game. A `#game=<id>` link opens that game after the first load. If a link was opened fresh, the Back button in the page shows the library and stays on the site.
- **Copy**: a click on the file name chip copies the file name to the clipboard.

## 12. Phone layout

Rules apply below 900 px wide, and by `hover:none` for touch-only parts.
- The filters sit behind a Filters button, with the number of active filters.
- The header wraps and its buttons have short labels. The folder box is hidden.
- The game bar is not sticky. Teams stack. The map is under the timeline at full width.
- The timeline puts the names above each player's lanes, so the plot gets the whole width. A long game is wider than the screen and scrolls sideways. The names stay at the left edge while it scrolls.
- The build-order table and the fights table scroll sideways inside their panels.
- NOT and Add note buttons appear, because there is no Shift key and no mouse drag.

## 13. Testing

There are no automated tests in the repository. Everything was checked with throwaway scripts that are not in git:
- headless Chromium driven by `puppeteer-core`, with a fake `showDirectoryPicker`, against the built site;
- pixel measurements on the minimap canvas (fades, X marks, stains);
- SQL counts compared with the filters (matchup, NOT, histograms, build timing);
- parser output compared between Pyodide and native Python, and before and after changes.

To check by hand: `make dev`, open `http://127.0.0.1:8000`, choose a folder of replays.

## 14. Known limits

- Unit positions are snapshots about 10.7 s apart. The dashed lines and the glide are estimates. Workers and overlords are not drawn. A building that lifts off and lands elsewhere stays where it was placed.
- Self-cast skills have no place on the map.
- Effect sizes and durations on the minimap are from the game rules, not the replay.
- The skill filter is a hand-kept list of generic orders. The tag and build thresholds are guesses from a small set of replays (14 replays, builds 98310 to 98745).
- Map names may be localised in other replays. Not checked.
- A replay with no result (a game that did not finish) shows no winner.
- Not tested: Firefox and Safari, observer and custom games, replays from other game versions, a real phone in landscape, restoring the saved folder handle in a later session (only checked with a fake handle).
- A remembered folder is not scanned when the page loads, because Chrome asks for permission on a click. Use Load… to look for new replays.
