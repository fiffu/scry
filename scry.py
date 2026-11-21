"""
scry.py: turn one StarCraft II replay into plain rows. This file runs in the browser (Pyodide, in a Web Worker).

    parse_replay(path) -> dict of rows, or raises. The page stores the rows in SQLite (sqlite-wasm).
    SCHEMA, REC_TABLES and REPLAY_FIELDS describe that database. build.py puts them in manifest.json.

Needs sc2reader and mpyq. Times are seconds on the in-game clock (frame / 22.4).
sc2reader's event.second is frame / 16 and is wrong for this: never use it. See DESIGN.md.
"""

import bisect
import hashlib
import json
from collections import Counter, defaultdict
from math import hypot

PARSER_VERSION = 14  # bump when parsing or tagging changes: every browser re-parses its replays
FPS = 22.4

# ----------------------------------------------------------------------------
# Tuning knobs. Every threshold used by tags and build rules lives here.
# All values are guesses made from 6 replays of one build: tune them.
# ----------------------------------------------------------------------------
CFG = dict(
    long_game_s=20 * 60,
    short_game_s=10 * 60,
    stomp_ratio=3.0, stomp_min_loss=3000,
    slugfest_fights=8,
    flip_min_fights=3, flip_fight_value=1500, flip_ratio=1.25,
    base_trade_window_s=90,
    float_bank=5000, float_ticks=8,
    fast_third_s=200,
    second_rax_s=150,
    leaver_gap_s=60,
    chat_after_s=60,
    fight_step=5, fight_win=4, fight_thresh=600, fight_merge=2, fight_min_total=800,
    opener_window_s=300,
    rush_dist=40,
)

CMD_EVENTS = {'BasicCommandEvent', 'TargetPointCommandEvent', 'TargetUnitCommandEvent', 'DataCommandEvent'}
APM_EVENTS = CMD_EVENTS | {'SelectionEvent', 'GetControlGroupEvent', 'SetControlGroupEvent', 'AddToControlGroupEvent'}
# Empirical: counting these two as well brings our APM within ~9% of Blizzard's number.
FIT_EVENTS = {'CommandManagerStateEvent', 'UpdateTargetUnitCommandEvent'}

NOISE_EXACT = {
    'RightClick', 'Attack', 'Stop', 'HoldPosition', 'ReturnCargo', 'SetWorkerRally', 'Move', 'Patrol', 'Scan',
    'SmartCommand', 'RaiseSupplyDepot', 'LowerSupplyDepot', 'SetRallyPoint', 'SCVRepair', 'MULERepair',
    'HoldFireGhost', 'SalvageEffect', 'DecloakGhost',
    'Gather', 'ScanMove', 'SetUnitRally',   # Gather: one player sent 20,000 of them in a single game
}
NOISE_PREFIX = ('Build', 'Train', 'Morph', 'Research', 'Upgrade', 'Evolve', 'Cancel', 'Lift', 'Land',
                'WarpIn', 'Rally', 'Select', 'Load', 'Unload', 'Transform', 'Root', 'Uproot')

# Words that get their own social tag when a player types them in chat (case does not matter).
CHAT_WORDS = ('cyka', 'blyat')

JUNK_UNITS = {
    'Larva', 'Egg', 'Broodling', 'BroodlingEscort', 'Locust', 'LocustMP', 'LocustMPFlying', 'Interceptor',
    'AutoTurret', 'CreepTumor', 'CreepTumorBurrowed', 'CreepTumorQueen', 'ParasiticBombDummy', 'MULE',
    'Changeling', 'ChangelingMarine', 'ChangelingZealot', 'ChangelingZergling', 'ForceField',
}
WORKERS = {'SCV', 'Probe', 'Drone'}
NOT_ARMY = {'Overlord'}  # sc2reader calls it army. It is supply, so it must not count as the first army unit.
TOWNHALLS = {'Nexus', 'CommandCenter', 'Hatchery'}
RACES = ('Terran', 'Protoss', 'Zerg')
START_RACE = {'Hatchery': 'Zerg', 'Nexus': 'Protoss', 'CommandCenter': 'Terran'}
META_RACE = {'Terr': 'Terran', 'Prot': 'Protoss', 'Zerg': 'Zerg'}
TOWNHALL_FORMS = TOWNHALLS | {'OrbitalCommand', 'PlanetaryFortress', 'Lair', 'Hive'}
GAS = {'Refinery', 'Extractor', 'Assimilator'}
PRODUCTION = {'Terran': 'Barracks', 'Protoss': 'Gateway', 'Zerg': 'SpawningPool'}
TECH = {
    'Terran': {'Factory', 'Starport'},
    'Protoss': {'CyberneticsCore', 'Stargate', 'RoboticsFacility', 'TwilightCouncil'},
    'Zerg': {'RoachWarren', 'BanelingNest', 'HydraliskDen', 'Spire', 'InfestationPit'},
}
TECH_MORPHS = {'OrbitalCommand', 'PlanetaryFortress', 'Lair', 'Hive', 'GreaterSpire'}
SHORT = {'SupplyDepot': 'Depot', 'Barracks': 'Rax', 'CommandCenter': 'CC', 'Refinery': 'Gas', 'Extractor': 'Gas',
         'Assimilator': 'Gas', 'Gateway': 'Gate', 'CyberneticsCore': 'Core', 'SpawningPool': 'Pool',
         'Hatchery': 'Hatch', 'EngineeringBay': 'Ebay', 'RoboticsFacility': 'Robo', 'PhotonCannon': 'Cannon',
         'SporeCrawler': 'Spore', 'SpineCrawler': 'Spine', 'TwilightCouncil': 'Twilight'}


NOISE_SUFFIX = ('Attack',)   # 'Attack', 'BattlecruiserAttack': an attack order, whatever unit gets it


def is_skill(name):
    return bool(name) and name not in NOISE_EXACT and not name.startswith(NOISE_PREFIX) and not name.endswith(NOISE_SUFFIX)


def is_cosmetic(name):
    return name.startswith(('Reward', 'Spray')) or name.endswith('Spray')


# ----------------------------------------------------------------------------
# Parsing: replay file -> plain dict of rows
# ----------------------------------------------------------------------------

def player_key(p):
    """Stable id of a player. Names can change, the toon id cannot."""
    toon = getattr(p, 'toon_id', 0)
    if not toon:
        return 'name:' + p.name
    return '%s-%s-%s' % (getattr(p, 'region', '?'), getattr(p, 'subregion', '?'), toon)


def slot_colour(p):
    """The colour the replay stores for the player's slot (Red, Blue, Teal, ...), as '#rrggbb'. Empty if there is none."""
    c = getattr(p, 'color', None)
    return '#%02x%02x%02x' % (c.r, c.g, c.b) if c is not None else ''


def detect_fights(deaths, dur):
    step, win = CFG['fight_step'], CFG['fight_win']
    nb = int(dur // step) + 1
    val = [0] * nb
    for d in deaths:
        val[min(nb - 1, int(d['t'] // step))] += d['value']
    active = [False] * nb
    for i in range(nb):
        if sum(val[i:i + win]) >= CFG['fight_thresh']:
            for j in range(i, min(nb, i + win)):
                active[j] = active[j] or val[j] > 0
    spans, cur = [], None
    for i in range(nb):
        if active[i]:
            if cur and i - cur[1] <= CFG['fight_merge']:
                cur[1] = i
            else:
                cur = [i, i]
                spans.append(cur)
    fights = [[d for d in deaths if a <= int(d['t'] // step) <= b] for a, b in spans]
    return [f for f in fights if sum(d['value'] for d in f) >= CFG['fight_min_total']]


def wmedian(pairs):
    pairs = sorted(pairs)
    half, acc = sum(w for _, w in pairs) / 2, 0
    for v, w in pairs:
        acc += w
        if acc >= half:
            return v


class Tags:
    def __init__(self):
        self.rows = []    # (pid, family, tag, kind, label, evidence)
        self.spans = []   # (pid, tag, t0, t1)
        self._seen = set()

    def add(self, pid, family, name, kind, label=None, evidence='', spans=()):
        tag = '%s:%s' % (family, name)
        if (pid, tag) not in self._seen:
            self._seen.add((pid, tag))
            self.rows.append((pid, family, tag, kind, label or str(name), evidence))
        for s in spans:
            self.spans.append((pid, tag, s[0], s[1]))


def plural(n, word):
    return '%d %s' % (n, word if n == 1 else word + 's')


def times_text(ts, limit=5):
    shown = ', '.join(mmss(t) for t in ts[:limit])
    return shown + (' +%d more' % (len(ts) - limit) if len(ts) > limit else '')


def mmss(sec):
    sec = int(sec)
    return '%d:%02d' % (sec // 60, sec % 60)


def classify_build(race, structs, units, own_start, enemy_starts):
    """Name an opening from the order of structures. Returns (label, extras, notation).

    structs: dicts (t, name, supply, x, y) sorted by time, opening window only.
    units: dicts (t, name) of first army units.
    """
    def first(name):
        return next((s for s in structs if s['name'] == name), None)

    def all_of(name):
        return [s for s in structs if s['name'] == name]

    halls = [s for s in structs if s['name'] in TOWNHALLS]
    gas = next((s for s in structs if s['name'] in GAS), None)
    extras, label = [], None
    notation = ', '.join('%d %s' % (s['supply'], SHORT.get(s['name'], s['name'])) for s in structs[:8])

    if race == 'Zerg':
        pool = first('SpawningPool')
        hatch = halls[0] if halls else None
        if pool and pool['supply'] <= 13 and (not hatch or pool['t'] < hatch['t']):
            label = '12-pool'
        elif pool and hatch and pool['t'] < hatch['t']:
            label = 'pool-first'
        elif gas and (not hatch or gas['t'] < hatch['t']) and (not pool or gas['t'] < pool['t']):
            label = 'gas-first'
        elif len(halls) >= 2 and pool and halls[1]['t'] < pool['t']:
            label = '3-hatch-before-pool'
        elif hatch:
            label = 'hatch-first'
    elif race == 'Terran':
        rax = all_of('Barracks')
        cc = halls[0] if halls else None
        reaper = next((u for u in units if u['name'] == 'Reaper'), None)
        fact, star = first('Factory'), first('Starport')
        if cc and (not rax or cc['t'] < rax[0]['t']):
            label = 'cc-first'
        elif len(rax) >= 2 and rax[1]['t'] < CFG['second_rax_s'] and (not cc or cc['t'] > 180):
            label = '2-rax-no-expand'
        elif cc and reaper:
            label = 'reaper-expand'
        elif fact and star and len(halls) < 2:
            label = '1-1-1'
        elif cc:
            label = 'rax-expand'
    elif race == 'Protoss':
        nex = halls[0] if halls else None
        gates = all_of('Gateway')
        forge = first('Forge')
        core = first('CyberneticsCore')
        cannons = [s for s in all_of('PhotonCannon') if s['t'] < 210]
        near = [c for c in cannons if enemy_starts and own_start
                and min(hypot(c['x'] - e[0], c['y'] - e[1]) for e in enemy_starts) < CFG['rush_dist']
                and min(hypot(c['x'] - e[0], c['y'] - e[1]) for e in enemy_starts)
                < hypot(c['x'] - own_start[0], c['y'] - own_start[1])]
        if near:
            label = 'cannon-rush'
        elif nex and (not gates or nex['t'] < gates[0]['t']):
            label = 'nexus-first'
        elif forge and (not gates or forge['t'] < gates[0]['t']):
            label = 'forge-expand' if nex else 'forge-first'
        elif len(gates) >= 2 and (not core or core['t'] > gates[1]['t']) and (not nex or nex['t'] > 150):
            label = '2-gate'
        elif nex and nex['t'] < 200:
            label = 'gate-expand'
        star = first('Stargate')
        if star and star['t'] < 210:
            extras.append('stargate-opener')
        if first('RoboticsFacility') and first('RoboticsFacility')['t'] < 240:
            extras.append('robo-opener')
    if not label:
        label = 'unnamed'
        notation = '-'.join(SHORT.get(s['name'], s['name']) for s in structs[:5]) + ' | ' + notation
    return label, extras, notation


def canonical_race(p, meta_player, start_type):
    """The race as an English name.

    sc2reader's play_race is written in the language of the player's game client: a Russian replay says
    'Зерги' and 'Терраны'. So it is the last resort. The metadata's AssignedRace and the player's starting
    base are the same in every language. pick_race is English too, but says 'Random' for random players.
    """
    for guess in (META_RACE.get((meta_player or {}).get('AssignedRace')), START_RACE.get(start_type),
                  getattr(p, 'pick_race', None), p.play_race):
        if guess in RACES:
            return guess
    return p.play_race


def cmd_xy(e):
    """Where a command was aimed, or (None, None). Self-cast commands have no location."""
    loc = getattr(e, 'location', None)
    if loc and len(loc) >= 2 and (loc[0] or loc[1]):
        return round(float(loc[0]), 1), round(float(loc[1]), 1)
    return None, None


def parse_replay(path):
    """Parse one replay into a dict of rows. Raises on unreadable files."""
    import sc2reader
    import mpyq

    r = sc2reader.load_replay(path, load_level=4)
    last_frame = max([r.frames] + [e.frame for e in r.events]) if r.events else r.frames
    dur = last_frame / FPS
    meta = {}
    try:
        raw = mpyq.MPQArchive(path).read_file('replay.gamemetadata.json')
        meta = {p['PlayerID']: p for p in json.loads(raw).get('Players', [])}
    except Exception:
        pass  # optional in some builds

    players = list(r.players)
    team_of = {p.pid: p.team.number for p in players}
    keys = {p.pid: player_key(p) for p in players}

    stats = defaultdict(list)
    deaths, hall_deaths, structs, born, upgrades, morphs = [], [], defaultdict(list), defaultdict(list), [], []
    starts, start_type = {}, {}
    map_structs, map_units, map_pts = {}, {}, []   # minimap: structure squares, unit dots, and where each unit was seen
    for e in r.tracker_events:
        n = type(e).__name__
        if n == 'PlayerStatsEvent':
            stats[e.pid].append(e)
        elif n == 'UnitBornEvent':
            u = e.unit
            if e.frame == 0 and e.unit_type_name in TOWNHALLS:
                starts[e.control_pid] = (e.x, e.y)
                start_type[e.control_pid] = e.unit_type_name
                if u:
                    map_structs[u.id] = [e.control_pid, e.unit_type_name, e.x, e.y, 0.0, None]
            elif u and u.is_army and u.name not in JUNK_UNITS and e.unit_type_name not in NOT_ARMY and (u.minerals or u.vespene):  # u.name is the LAST type: an overlord that became an overseer reports 'Overseer'
                born[e.control_pid].append(dict(t=e.frame / FPS, name=e.unit_type_name))
                map_units[u.id] = [e.control_pid, e.unit_type_name, round(e.frame / FPS, 1), None]
                map_pts.append((u.id, round(e.frame / FPS, 1), e.x, e.y))
        elif n == 'UnitPositionsEvent':
            # The replay lists where some units are, about every 10 s. It is the only record of units moving.
            for unit, (x, y) in e.units.items():
                if unit.id in map_units:
                    map_pts.append((unit.id, round(e.frame / FPS, 1), x, y))
        elif n == 'UnitInitEvent':
            u = e.unit
            # Warp-ins fire UnitInit too, so keep only real buildings.
            if u and u.is_building and u.name not in JUNK_UNITS and not u.name.startswith('CreepTumor'):
                structs[e.control_pid].append(dict(t=e.frame / FPS, name=e.unit_type_name, x=e.x, y=e.y))
                map_structs[u.id] = [e.control_pid, e.unit_type_name, e.x, e.y, round(e.frame / FPS, 1), None]
        elif n == 'UnitTypeChangeEvent':
            u = e.unit
            if u and u.owner and e.unit_type_name in TECH_MORPHS:
                morphs.append((u.owner.pid, e.frame / FPS, e.unit_type_name))
        elif n == 'UpgradeCompleteEvent':
            if not is_cosmetic(e.upgrade_type_name):
                upgrades.append((e.pid, e.frame / FPS, e.upgrade_type_name))
        elif n == 'UnitDiedEvent':
            u = e.unit
            if u and u.id in map_structs and map_structs[u.id][5] is None:
                map_structs[u.id][5] = round(e.frame / FPS, 1)
            if u and u.id in map_units and map_units[u.id][3] is None:
                map_units[u.id][3] = round(e.frame / FPS, 1)
                map_pts.append((u.id, round(e.frame / FPS, 1), e.x, e.y))
            if not u or not u.owner:
                continue
            # Blizzard's resources_lost ignores suicides and drones that become buildings.
            if e.killer_pid == u.owner.pid:
                continue
            if u.name in TOWNHALL_FORMS and e.killer_pid and team_of.get(e.killer_pid) != team_of.get(u.owner.pid):
                hall_deaths.append(dict(t=e.frame / FPS, team=team_of.get(u.owner.pid), name=u.name,
                                        pid=u.owner.pid, x=e.x, y=e.y))
            if u.name in JUNK_UNITS or (u.name == 'Drone' and not e.killer_pid):
                continue
            value = (u.minerals or 0) + (u.vespene or 0)
            if value > 0:
                deaths.append(dict(t=e.frame / FPS, pid=u.owner.pid, team=team_of.get(u.owner.pid),
                                   value=value, name=u.name, x=e.x, y=e.y))

    race_of = {p.pid: canonical_race(p, meta.get(p.pid), start_type.get(p.pid)) for p in players}

    actions = defaultdict(list)
    cmds = defaultdict(list)
    research = defaultdict(list)
    for e in r.game_events:
        n = type(e).__name__
        if e.player is None:
            continue
        if n in APM_EVENTS or n in FIT_EVENTS:
            actions[e.player.pid].append(e.frame)
        if n in CMD_EVENTS:
            ab = getattr(e, 'ability_name', None)
            if ab:
                t = e.frame / FPS
                if is_skill(ab):
                    x, y = cmd_xy(e)
                    cmds[e.player.pid].append((t, ab, x, y))
                elif ab.startswith(('Research', 'Evolve', 'Upgrade')) and not ab.startswith('UpgradeTo'):
                    research[e.player.pid].append((t, ab))

    leaves = {}
    for e in r.events:
        if type(e).__name__ == 'PlayerLeaveEvent' and getattr(e, 'player', None):
            leaves[e.player.pid] = e.frame / FPS

    # ---- fights -------------------------------------------------------------
    teams = sorted({p.team.number for p in players})
    fights, fight_rows, loss_rows = detect_fights(deaths, dur), [], []
    for i, f in enumerate(fights):
        mx = wmedian([(d['x'], d['value']) for d in f])
        my = wmedian([(d['y'], d['value']) for d in f])
        fight_rows.append((i, f[0]['t'], f[-1]['t'], int(mx), int(my)))
        for tn in teams:
            top = Counter()
            for d in f:
                if d['team'] == tn:
                    top[d['name']] += d['value']
            loss_rows.append((i, tn, sum(top.values()), ', '.join('%s %d' % kv for kv in top.most_common(4))))

    # ---- stats, supply lookup ----------------------------------------------
    stat_rows, frames_of = [], {}
    for p in players:
        st = stats[p.pid]
        frames_of[p.pid] = [e.frame for e in st]
        for e in st:
            stat_rows.append((p.pid, e.frame / FPS, int(e.food_used), int(e.food_made), e.workers_active_count,
                              e.minerals_collection_rate, e.vespene_collection_rate, e.minerals_current,
                              e.vespene_current, e.minerals_used_active_forces + e.vespene_used_active_forces,
                              e.resources_killed, e.resources_lost))

    def supply_at(pid, t):
        fr = frames_of.get(pid) or []
        if not fr:
            return 0
        i = max(0, bisect.bisect_right(fr, t * FPS) - 1)
        return int(stats[pid][i].food_used)

    # ---- players ------------------------------------------------------------
    player_rows, apm_rows = [], []
    apm_est = {}
    for p in players:
        m = meta.get(p.pid, {})
        mmr = next((v for v in (getattr(p, 'init_data', {}).get('scaled_rating'), m.get('MMR')) if v and v > 0), None)
        apm_est[p.pid] = len(actions[p.pid]) / (dur / 60) if dur else 0
        player_rows.append((p.pid, keys[p.pid], p.name, getattr(p, 'clan_tag', '') or '', p.team.number,
                            race_of[p.pid], p.result or '', mmr, m.get('APM'), round(apm_est[p.pid], 1), slot_colour(p)))
        buckets = Counter(int(f / FPS // 30) for f in actions[p.pid])
        apm_rows += [(p.pid, b, c) for b, c in sorted(buckets.items())]

    # ---- build steps and timings -------------------------------------------
    step_rows, timing_rows, builds = [], [], {}
    for p in players:
        pid, race = p.pid, race_of[p.pid]
        s_list = sorted(structs[pid], key=lambda s: s['t'])
        for s in s_list:
            s['supply'] = supply_at(pid, s['t'])
        firsts, seen = [], set()
        for u in sorted(born[pid], key=lambda u: u['t']):
            if u['name'] not in seen and u['name'] not in WORKERS and u['t'] > 0:
                seen.add(u['name'])
                firsts.append(u)
        steps = [(s['t'], 'struct', s['name']) for s in s_list]
        steps += [(u['t'], 'unit', u['name']) for u in firsts]
        steps += [(t, 'research', n) for t, n in research[pid]]
        steps += [(t, 'morph', n) for q, t, n in morphs if q == pid]
        steps.sort()
        nth = Counter()
        for idx, (t, kind, name) in enumerate(steps):
            nth[name] += 1
            step_rows.append((pid, idx, t, supply_at(pid, t), kind, name, nth[name]))

        def tm(key, t):
            if t is not None:
                timing_rows.append((pid, key, t))
        halls = [s for s in s_list if s['name'] in TOWNHALLS]
        tm('natural', halls[0]['t'] if halls else None)
        tm('third_base', halls[1]['t'] if len(halls) > 1 else None)
        tm('first_gas', next((s['t'] for s in s_list if s['name'] in GAS), None))
        tm('first_production', next((s['t'] for s in s_list if s['name'] == PRODUCTION.get(race)), None))
        tm('first_tech', next((s['t'] for s in s_list if s['name'] in TECH.get(race, ())), None))
        tm('first_army', min((u['t'] for u in born[pid] if u['t'] > 0), default=None))
        tm('max_supply', next((e.frame / FPS for e in stats[pid] if e.food_used >= 200), None))

        opening, seen_n = [], Counter()
        for s in s_list:
            if s['t'] > CFG['opener_window_s']:
                break
            seen_n[s['name']] += 1
            if s['name'] in ('SupplyDepot', 'Pylon') and seen_n[s['name']] > 1:
                continue  # only the first supply building is part of the named opener
            opening.append(s)
        enemies = [loc for q, loc in starts.items() if team_of.get(q) != team_of.get(pid)]
        builds[pid] = classify_build(race, opening, [u for u in born[pid]], starts.get(pid), enemies)

    # ---- tags ---------------------------------------------------------------
    tg = Tags()
    tg.add(None, 'map', r.map_name, 'exact', r.map_name)
    if dur >= CFG['long_game_s']:
        tg.add(None, 'shape', 'long', 'inferred', 'long game', 'Lasted %s' % mmss(dur))
    if dur < CFG['short_game_s']:
        tg.add(None, 'shape', 'short', 'inferred', 'short game', 'Lasted %s' % mmss(dur))

    team_loss = {tn: sum(d['value'] for d in deaths if d['team'] == tn) for tn in teams}
    if len(teams) >= 2:
        ranked = sorted(team_loss.items(), key=lambda kv: kv[1])
        lo, hi = ranked[0], ranked[-1]
        if hi[1] >= CFG['stomp_min_loss'] and hi[1] >= CFG['stomp_ratio'] * max(lo[1], 1):
            # The team that lost less is the one that destroyed more: its kills are the other team's losses.
            if lo[1] == 0:
                why = 'Team %d destroyed %s value, team %d destroyed none' % (lo[0], format(hi[1], ','), hi[0])
            else:
                why = 'Team %d destroyed %dx more value than team %d' % (lo[0], round(hi[1] / lo[1]), hi[0])
            tg.add(None, 'shape', 'stomp', 'inferred', 'stomp', why)
    if len(fights) >= CFG['slugfest_fights']:
        tg.add(None, 'shape', 'slugfest', 'inferred', 'slugfest', plural(len(fights), 'fight'),
               [(f[0]['t'], f[-1]['t']) for f in fights])
    # lead flips: which team lost more in each sizable fight
    losers = []
    for i, f in enumerate(fights):
        by = {tn: sum(d['value'] for d in f if d['team'] == tn) for tn in teams}
        if sum(by.values()) >= CFG['flip_fight_value'] and len(by) >= 2:
            s = sorted(by.items(), key=lambda kv: kv[1])
            if s[-1][1] >= CFG['flip_ratio'] * max(s[0][1], 1):
                losers.append((s[-1][0], f[0]['t']))
    flips = sum(1 for a, b in zip(losers, losers[1:]) if a[0] != b[0])
    if flips >= CFG['flip_min_fights'] - 1 and len(losers) >= CFG['flip_min_fights']:
        tg.add(None, 'shape', 'back-and-forth', 'inferred', 'back and forth',
               'Losing side changed %s over %s' % (plural(flips, 'time'), plural(len(losers), 'fight')))

    kills = sorted(hall_deaths, key=lambda h: h['t'])
    if kills:
        tg.add(None, 'spectacle', 'base-kill', 'inferred', 'base kill', '%s destroyed at %s' % (plural(len(kills), 'base'), times_text([k['t'] for k in kills])),
               [(k['t'], k['t']) for k in kills])
        for a in kills:
            if any(b['team'] != a['team'] and abs(b['t'] - a['t']) <= CFG['base_trade_window_s'] for b in kills):
                tg.add(None, 'spectacle', 'base-trade', 'inferred', 'base trade',
                       'Both teams lost a base around %s' % mmss(a['t']),
                       [(a['t'], a['t'])])
                break

    for p in players:
        pid = p.pid
        st = stats[pid]
        label, extras, notation = builds[pid]
        tg.add(pid, 'build', label, 'rule', label, notation)
        for x in extras:
            tg.add(pid, 'build', x, 'rule', x.replace('-', ' '), notation)
        counts = Counter()
        for t, ab, _x, _y in cmds[pid]:
            counts[ab] += 1
            tg.add(pid, 'skill', ab, 'exact', ab, '', [(t, t)])
        for u in sorted({u['name'] for u in born[pid]}):
            tg.add(pid, 'unit', u, 'exact', u, '', [(min(x['t'] for x in born[pid] if x['name'] == u),) * 2])
        for q, t, n in upgrades:
            if q == pid:
                tg.add(pid, 'upgrade', n, 'exact', n, '', [(t, t)])
        cap = [e.frame / FPS for e in st if e.food_used >= 200]
        if cap:
            tg.add(pid, 'econ', 'maxed-out', 'inferred', 'maxed out', 'Reached 200 supply at %s' % mmss(cap[0]),
                   [(cap[0], cap[-1])])
        run, spans = [], []
        for e in st + [None]:
            if e is not None and e.minerals_current >= CFG['float_bank']:
                run.append(e)
            else:
                if len(run) >= CFG['float_ticks']:
                    spans.append((run[0].frame / FPS, run[-1].frame / FPS))
                run = []
        if spans:
            tg.add(pid, 'econ', 'floating-bank', 'inferred', 'floating bank',
                   'Over %s minerals banked for %s' % (format(CFG['float_bank'], ','), mmss(sum(b - a for a, b in spans))), spans)
        third = next((v for q, k, v in timing_rows if q == pid and k == 'third_base'), None)
        if third is not None and third < CFG['fast_third_s']:
            tg.add(pid, 'econ', 'fast-third', 'inferred', 'fast third', 'Third base at %s' % mmss(third),
                   [(third, third)])
        lv = leaves.get(pid)
        if lv is not None and dur - lv >= CFG['leaver_gap_s']:
            tg.add(pid, 'social', 'leaver', 'inferred', 'left early',
                   'Left at %s, game ended %s' % (mmss(lv), mmss(dur)), [(lv, lv)])

    chat_rows = []
    for m in r.messages:
        text = getattr(m, 'text', None)
        if not text:
            continue
        pid = m.player.pid if getattr(m, 'player', None) else None
        chat_rows.append((pid, m.frame / FPS, text, 1 if getattr(m, 'to_all', True) else 0))
    late = [c for c in chat_rows if c[1] >= CFG['chat_after_s']]
    if late:
        tg.add(None, 'social', 'chat', 'inferred', 'has chat', '%s after %s' % (plural(len(late), 'message'), mmss(CFG['chat_after_s'])),
               [(c[1], c[1]) for c in late])
    add_chat_word_tags(tg, chat_rows)

    winner = r.winner.number if r.winner else None
    cast_rows = [(p.pid, ab, t, x, y) for p in players for t, ab, x, y in cmds[p.pid]]
    return dict(
        replay=dict(fingerprint=hashlib.sha1(('%s|%s' % (r.start_time.isoformat(), sorted(keys.values()))).encode()).hexdigest()[:16],
                    map=r.map_name, mode=r.type, category=r.category, build=r.build,
                    played_at=r.start_time.isoformat(), duration_s=round(dur, 1), winner_team=winner),
        players=player_rows, stats=stat_rows, casts=cast_rows, steps=step_rows, timings=timing_rows,
        fights=fight_rows, losses=loss_rows, apm=apm_rows, chat=chat_rows, tags=tg.rows, spans=tg.spans,
        munits=[(uid, v[0], v[1], v[2], v[3]) for uid, v in map_units.items()],
        mpts=sorted(map_pts, key=lambda q: (q[0], q[1])),
        mstructs=[tuple(v) for v in map_structs.values()])


def add_chat_word_tags(tg, chat_rows):
    """One tag per watched word, on the player who typed it. The spans mark each message."""
    for word in CHAT_WORDS:
        for pid, t, text, _ in chat_rows:
            if word in text.lower():
                tg.add(pid, 'social', word, 'inferred', word, text, [(t, t)])


# ----------------------------------------------------------------------------
# Database layout
# ----------------------------------------------------------------------------

SCHEMA = '''
create table meta(key text primary key, value text);
create table replays(rid integer primary key, id text unique, fingerprint text, path text unique, size int, mtime real,
    map text, mode text, category text, build int, played_at text, duration_s real, winner_team int, error text);
create table players(rid int, pid int, toon text, name text, clan text, team int, race text, result text,
    mmr int, apm_blizzard real, apm_est real, color text, primary key(rid, pid));
create table tags(tid integer primary key, rid int, pid int, family text, tag text, kind text, label text, evidence text);
create table tag_spans(tid int, t0 real, t1 real);
create table casts(rid int, pid int, ability text, t real, x real, y real);
create table map_units(rid int, uid int, pid int, name text, t0 real, t1 real);
create table map_pts(rid int, uid int, t real, x real, y real);
create table map_structs(rid int, pid int, name text, x real, y real, t0 real, t1 real);
create table build_steps(rid int, pid int, idx int, t real, supply int, kind text, name text, nth int);
create table timings(rid int, pid int, key text, t real);
create table fights(rid int, idx int, t0 real, t1 real, x int, y int);
create table fight_losses(rid int, fight_idx int, team int, value int, top_units text);
create table stats(rid int, pid int, t real, food_used int, food_made int, workers int, min_rate int,
    gas_rate int, min_bank int, gas_bank int, army_value int, killed int, lost int);
create table apm(rid int, pid int, bucket int, n int);
create table chat(rid int, pid int, t real, text text, to_all int);
create index i_tags on tags(tag);
create index i_tags_r on tags(rid);
create index i_spans on tag_spans(tid);
create index i_casts on casts(rid, ability);
create index i_steps on build_steps(name, nth, t);
create index i_timings on timings(key, t);
create index i_players on players(toon);
'''
# How a parsed record maps onto tables: (table, key in the record, columns after rid).
# The page reads this from manifest.json when it stores a record.
REC_TABLES = (('players', 'players', 11), ('stats', 'stats', 12), ('casts', 'casts', 5),
              ('build_steps', 'steps', 7), ('timings', 'timings', 3), ('fights', 'fights', 5),
              ('fight_losses', 'losses', 4), ('apm', 'apm', 3), ('chat', 'chat', 4),
              ('map_units', 'munits', 5), ('map_pts', 'mpts', 4), ('map_structs', 'mstructs', 6))
REPLAY_FIELDS = ('map', 'mode', 'category', 'build', 'played_at', 'duration_s', 'winner_team')
