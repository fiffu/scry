# Scry: plan

> Historical: this was the first plan. The tool is built and runs on GitHub Pages only. [DESIGN.md](DESIGN.md) says how it works now, and the server, `scan` and one-file build described below no longer exist.

Scry reads StarCraft II replays from a folder. It builds a searchable library and shows a timeline for each game.
The goal is to help a viewer find games worth cutting into a highlights reel.
The tool specializes in filtering. Each viewer has their own idea of a highlight.

## Decisions

| Topic | Decision |
|---|---|
| Language | Python 3. Parser dependency: `sc2reader` (pip). The server uses only the standard library, in the style of `_uimerge.py`. |
| Front end | One HTML page that talks to a Python file server. |
| Database | SQLite. Python builds the file. The browser queries it through the official `sqlite-wasm` build. |
| Replay id | sha256 of the file bytes. A header fingerprint is stored as a second field. |
| Notes | Browser `localStorage`, joined into the database at page load. |
| Game modes | Team games first. 1v1 comes from the same code but is not tested yet. |
| Players | Every player looks the same. The index can filter by player. |
| Search | Tags, free text, exact skill names, and build timing conditions. |
| Replay source | The folder is on another machine (a Windows computer). The user copies replays in. |
| Static site | Same page, no server: Pyodide parses in Web Workers, sqlite-wasm queries, IndexedDB keeps the database. Built with `scry.py --site`. See notes.md. |

## Research notes

Sources: 6 sample replays, all 2v2 ladder games, build 98310, 11 to 22 minutes long.
Probe code lives in `spike_parse.py`. It runs on all 6 replays in about 2.3 s.

### What a replay holds

| Part | Content |
|---|---|
| `replay.gamemetadata.json` | Map, result, MMR, Blizzard APM per player |
| `replay.details` and `initData` | Names, toon ids, clan tags, colors, teams, map size, start time |
| Tracker events | `PlayerStats` samples, unit born/init/done/died with x and y, upgrades |
| Game events | Commands with ability ids, camera, selection, control groups, pings, resource trade |
| Message events | Chat with timestamps |
| Other files | `smartcam`, `sync`, `battlelobby`, `resumable`, `load.info`. Not used so far. |

Timing: a full parse takes 0.38 s per replay. A header-only parse takes 0.01 s.

### Traps found

1. **Clock.** `event.second` in sc2reader is `frame / 16`. The in-game clock is `frame / 22.4`. Always compute time from the frame. The metadata `Duration` field has the same error. Chat `second` too.
2. **Stat sampling.** `PlayerStats` arrives every 160 frames (about 7.1 s). Fields include supply, workers, income, bank, army value, and cumulative `resources_killed` and `resources_lost`.
3. **Supply is weak late in a game.** A player can sit at 200/200 for 10 minutes. Use killed/lost deltas and death bursts for activity.
4. **Deaths need filtering.** Skip units that cost nothing (larva, creep tumors, broodlings, locusts, interceptors, auto-turrets). Skip suicides (the killer is the unit's own player). Skip drone deaths with no killer (drones become buildings). With these filters, fight loss totals match `resources_lost` within about 5%. Without them, totals are off by up to 2x. The killer is known for only about 65% of deaths.
5. **APM.** Only the Blizzard total is stored. Counting commands, selections and control groups gives 40 to 70% of the Blizzard number. Adding `CommandManagerStateEvent` and `UpdateTargetUnitCommandEvent` gets within about 9%. This fit is empirical. Exclude camera events.
6. **No rating.** MMR `-36400` means "no rating". Treat values at or below 0 as missing.
7. **Cosmetic upgrades.** About 100 `Reward...` entries appear at second 0. Filter them.
8. **No minimap image.** The replay holds only the filename `Minimap.tga`, the map hash and the map size. Unit coordinates exist, so a plain coordinate map is possible.
9. **Names come from tables, not the replay.** The replay stores ids. sc2reader maps ids to ability and unit names. All 149 abilities in the samples resolved. Only one build was tested.
10. **Start base is not a build event.** The first extra town hall is the natural expansion.
11. **Supply at a build step is approximate.** It comes from the nearest earlier stat sample, so it can be off by 2 or 3. Time is exact. Prefer time in filters.
12. **Player identity.** Names can change. The toon id is stable. The same two players appear in all 6 samples, so the player filter must use toon id.

### Signals checked in the samples

- Reliable: clock, build order, first-seen units, upgrades, casts, MULE/chrono/scan counts, bank and worker peaks, chat times, town hall kills, leave events, fight loss totals.
- Unproven: comeback detection. The army-value probe broke on samples taken after players left. Fix this before shipping `shape:comeback`.
- Noisy: long battles merge into one fight window. Fights in two places at once are not split. Inject gaps mean little with several queens. Peak bank is inflated at the end.
- Untested: 1v1 games, games against AI, custom games, observers, other builds.

### Browser SQLite check

| | sql.js 1.17.0 | sqlite-wasm 3.54.1-build1 |
|---|---|---|
| FTS5 | not found in the binary | found |
| FTS3/FTS4 | found | not found |
| `trigram` tokenizer | not found | found |
| WASM size | 688 KB | 879 KB |
| Single-file build | yes (981 KB JS with WASM inside) | no |

Choice: `sqlite-wasm`. The `trigram` tokenizer matches parts of words, so "stim" finds `UseStimpack`.
These checks read the binaries only. Nothing ran in a browser yet. Milestone 0 covers this.

## Architecture

```
replays/*.SC2Replay
      |  scry.py scan  (sc2reader, sha256, tags, build steps)
      v
scry.sqlite  (plain tables, no FTS tables)
      |  GET /db  (ETag)
      v
Browser: sqlite-wasm loads the file in memory
         creates FTS5 tables from plain tables
         creates `notes` from localStorage
         runs every search, facet count and filter as SQL
```

- Python builds the database with its built-in `sqlite3`. Python needs no FTS5 support.
- The browser builds the FTS5 tables at load time. The data is small, so this is fast.
- The server has three jobs: parse new or changed replays, serve static files, serve the database.
- WASM does not load from `file://`. A small HTTP server stays necessary.

### Files

```
scry/
  scry.py            # CLI (serve, scan, bundle), parser, tags, build rules, server
  page.html          # the single page: library view and game view
  requirements.txt   # sc2reader (pinned)
  Makefile           # help (default), deps, run, scan, bundle
  dist/scry.py       # one-file build (make bundle), page embedded
  .scry/             # created at run time: scry.sqlite and vendor/ (sqlite-wasm, fetched once)
  plan.md, notes.md
```

Decided: all Python stays in one file. `page.html` is embedded by `make bundle`, so the shipped tool is one file.
The sqlite-wasm files are not shipped. The first run downloads them and checks a pinned sha512.

### CLI

- `scry.py serve --replays DIR [--db scry.sqlite] [--port 0]` scans DIR recursively, updates the database, then serves.
- `scry.py scan --replays DIR` updates the database only.
- A file is re-parsed when its sha256 is new or `parser_version` changed.
- A parse failure writes `replays.error` and never stops the scan.
- Unknown ability or unit ids get the fallback name `ability:<id>` or `unit:<id>`.

### Setup note

The laptop has no `python3-venv`. Use `pip install --user -r requirements.txt` or `pip install --target`.
On Windows: `pip install sc2reader`.

## Data model (sketch)

```sql
replays(id TEXT PRIMARY KEY, fingerprint TEXT, path TEXT, size INT, mtime REAL,
        parser_version INT, map TEXT, mode TEXT, build INT, played_at TEXT,
        duration_s REAL, winner_team INT, error TEXT)
players(replay_id, pid, toon_id, name, team, race, result, mmr INT,
        apm_blizzard REAL, apm_est REAL, PRIMARY KEY (replay_id, pid))
tags(replay_id, pid, family, tag, kind, label, weight REAL, evidence TEXT)
tag_spans(replay_id, pid, tag, t0 REAL, t1 REAL)
casts(replay_id, pid, ability, grp, t REAL)          -- skill-like abilities only
build_steps(replay_id, pid, idx, t REAL, supply INT, kind, name, nth INT)
timings(replay_id, pid, key, t REAL)                 -- natural, first_gas, third_base ...
fights(replay_id, idx, t0 REAL, t1 REAL, x INT, y INT)
fight_losses(replay_id, fight_idx, team, value INT, top_units TEXT)
stats(replay_id, pid, t REAL, food_used, food_made, workers, min_rate, gas_rate,
      min_bank, gas_bank, army_value, killed, lost)
apm(replay_id, pid, bucket INT, n INT)               -- 30 s buckets
chat(replay_id, pid, t REAL, text TEXT)
```

Times are seconds on the in-game clock (`frame / 22.4`).
Generic commands (RightClick, Attack, Stop, HoldPosition, ReturnCargo, SetWorkerRally, Move) stay out of `casts` to keep the file small.
Database size is not measured yet.

### Replay ids

- `replays.id` is the sha256 of the file. A renamed or moved file keeps its id.
- `fingerprint` is start time plus sorted toon ids. Two copies of the same game should share it. This is untested because no such pair exists yet.
- The parse cache key is `(sha256, parser_version)`.
- Notes store both the id and the fingerprint. If the id is missing, the fingerprint finds the notes.

## Tags

Each tag has: id, label, family, kind (`exact`, `rule` or `inferred`), weight, optional player, optional time spans, and an evidence line.
The evidence line explains why a tag fired. Example: "base-trade: both teams lost a town hall within 60 s".
All thresholds live in one table at the top of the code.

| Family | Examples | Source | Kind |
|---|---|---|---|
| `skill:` | `skill:UseStimpack`, `skill:TacticalNukeStrike` | command events | exact |
| `group:` | detection, nuke, spellcaster, macro | hand-made groups over ability names | exact |
| `unit:` / `upgrade:` | `unit:Ghost`, `unit:Carrier` | tracker events | exact |
| `build:` | `build:12-pool`, `build:gate-expand` | build-order rules | rule |
| `who:` | player by toon id | header | exact |
| `map:` `mode:` `matchup:` | Reclamation, 2v2, ZvT | header | exact |
| `shape:` | stomp, back-and-forth, one-big-battle, slugfest, long, short | fights and loss curve | inferred |
| `spectacle:` | base-trade, base-kill, big-fight | town hall deaths, fight value | inferred |
| `econ:` | maxed-out, floating-bank, fast-third | `PlayerStats` | inferred |
| `people:` | leaver, high-APM, low-APM, has-chat | leave events, APM, chat | inferred |
| `mine:` | user tags | notes in localStorage | manual |

Rules:
- Tag spans let the timeline mark where the tag happened.
- Rarity score: a tag that appears in few games ranks higher. The index card shows each game's most distinctive tags.
- `shape:comeback` waits until the signal is validated.

## Build orders

A build order is a list of steps `(time, supply, kind, name)`.
The kinds are: structure start, first unit of a type, upgrade, morph.
The opening window ends at 5:00 or at the first fight, whichever is earlier.

Notation (community style): `14 Depot, 16 Rax, 16 Gas, 20 CC`.

Key timings stored per player:
- natural expansion
- first gas
- first production building
- first tech building
- first army unit
- third base
- first fight

Opener names come from rules over the order of structures. If no rule matches, the label is the structure order itself, for example "Depot, Rax, CC, Factory". Every game gets a readable label.

Seed rules (guesses to tune against more replays):

| Race | Name | Pattern |
|---|---|---|
| Zerg | 12-pool | pool at 13 supply or lower, before any hatchery |
| Zerg | hatch-first | hatchery before pool |
| Zerg | gas-first | extractor before hatchery and pool |
| Zerg | 3-hatch-before-pool | third hatchery before pool |
| Terran | reaper-expand | Barracks, Refinery, Reaper, then CC |
| Terran | cc-first | CC before Barracks |
| Terran | 1-1-1 | Barracks, Factory, Starport before a second CC |
| Terran | 2-rax-all-in | two Barracks and no CC before about 3:00 |
| Protoss | gate-expand | Gateway, then Nexus |
| Protoss | nexus-first | Nexus before Gateway |
| Protoss | cannon-rush | Forge, then Cannons near the enemy start |
| Protoss | stargate-opener | Stargate before about 3:00 |

Cannon-rush detection needs a distance check between the cannons and the enemy start. Building coordinates exist.

Examples from the samples:
- Zerg: pool at 12 supply, hatchery at 19 supply. Rule: 12-pool.
- Zerg: hatchery 16, pool 18, gas 19. Rule: hatch-first.
- Terran: depot 14, rax 16, gas 16, CC 20, Reaper at 2:05. Rule: reaper-expand.
- Protoss: Gateway 15, Nexus 19, two Stargates. Rule: gate-expand into double Stargate.

## Search and filters

Entry points:

1. **Search box.** FTS5 with the `trigram` tokenizer over tag labels, hand-written synonyms ("nuke", "nuclear") and chat text. Autocomplete from the same tables.
2. **Facet sidebar.** Exact ability, unit and tag names with counts. Counts update as filters apply. Tags in one family combine with OR. Different families combine with AND. A NOT toggle excludes a tag.
3. **Player filter.** By toon id. Shows every game that includes that player.
4. **Build timing builder.** Rows of conditions. Each row has: player scope (any player, same player, named player), a thing (structure, unit, upgrade, ability), first or nth, before/after/between, and a time or supply value.
   Examples: "Barracks, 1st, before 0:45". "Natural between 1:40 and 2:00". "First gas before 0:30".
   Time is preferred over supply because supply is approximate.
5. **Histogram sliders.** One histogram per key timing across the library: natural, first gas, first army unit, first fight, game length, APM. Dragging the handles filters the list live.
6. **Optional shorthand.** `rax<0:45 natural 1:40-2:00`.

Time conditions on skills (for example "nuke after 10:00") join on `casts`.

## Views

### Index

- A game card shows: map, mode, duration, players with race and result, build labels, top distinctive tags.
- Sort: date, duration, rarity score, biggest fight, APM.

### Timeline (per game)

Text-mode prototype exists in `spike_parse.py`. The page shows:
- Header with map, mode, duration, result, players.
- One lane per player: APM, supply, fight intensity (killed/lost delta), own death value. One cell is 30 s.
- Fight segments with location and loss per team.
- Build order per player with key timings.
- Tag spans, skill uses and chat lines as markers.
- A notes box and a marks list (start, end, text).

When a filter or tag is active, the timeline annotates its uses.

## Notes

- Stored in `localStorage` under one key, as JSON: `{ replayId: { fingerprint, text, tags, marks: [{t0, t1, text}] } }`.
- At page load the browser writes the notes into a `notes` table, so `mine:` tags join like any other tag.
- The page has export and import buttons for the notes JSON. This is the safety net against cleared site data.
- Later option: a toggle that POSTs notes to the backend, which writes a file to disk.

## Milestones

0. **Smoke test.** A page loads `sqlite-wasm` from `vendor/`, opens a small database, and runs one FTS5 trigram query. This is a go/no-go check.
1. **Parser module.** Replay to records, from the spike code. Checks on the 6 samples: fight totals within 5% of `resources_lost`, APM fit within 10% of Blizzard.
2. **Database build.** Schema, incremental scan, error column, parser version.
3. **Server.** Static files and `/db` with ETag. CLI flags. Makefile with `help`, `vendor`, `scan`, `run`.
4. **Index page.** List, search box, facets, player filter.
5. **Timeline page.** Lanes, fights, build info, markers.
6. **Build timing filter.** Builder first, then histogram sliders.
7. **Notes.** localStorage, export/import, `notes` join.

## Risks and unknowns

- `sqlite-wasm` was not run in a browser yet. Milestone 0 settles it.
- Database size is not measured. If it is large, store fewer cast rows or use the browser cache.
- Python's built-in SQLite on the friend's Windows machine is not checked. The plan needs only plain tables, so this risk is small.
- sc2reader tables may lag new game patches. The fallback name keeps parsing alive.
- All thresholds (fights, tags, openers) are guesses. Six replays from one build is a small test set.
- Notes in `localStorage` can vanish. Export and import reduce the harm.

## Follow-ups (out of scope for the first version)

- Test and fix 1v1, AI, custom and observer games.
- Validate `shape:comeback`.
- Compute exact supply from unit events.
- "Find builds like this": rank games by step order and timing distance.
- Split fights that happen in different places at the same time.
- Detect cannon rush, proxy, harass and drops.
- Draw a coordinate map of fights and bases.
- A POST toggle that saves notes to disk.
- A zip hand-off so the friend can run it without Python (needs an inline build or a small static server).

## Open questions

- One Python file, or a few modules?
- How should the index rank games by default?
- Should the tool read replays from more than one folder?
