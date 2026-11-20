#!/usr/bin/env python3
"""
scry.py: browse a folder of StarCraft II replays as a searchable library.

Usage:
    python scry.py [--root DIR] [--host 127.0.0.1] [--port 0] [--scan-only]

Needs: pip install sc2reader
Run it in the folder that holds your replays (or give --root). The script scans that
folder and its subfolders, stores what it finds in '.scry/scry.sqlite', and serves a
page. The browser loads the database and runs all searches itself (SQLite in WASM).

Times in the database are seconds on the in-game clock (frame / 22.4).
sc2reader's event.second is frame / 16 and is wrong for this: never use it.
"""

import argparse
import bisect
import hashlib
import json
import os
import re
import sqlite3
import statistics
import sys
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from math import hypot
from urllib.parse import parse_qs, urlparse

PARSER_VERSION = 2  # bump when parsing or tagging changes: the database is rebuilt
FPS = 22.4
EXT = '.sc2replay'

# ----------------------------------------------------------------------------
# Tuning knobs. Every threshold used by tags and build rules lives here.
# All values are guesses made from 6 replays of one build: tune them.
# ----------------------------------------------------------------------------
CFG = dict(
    long_game_s=20 * 60,
    short_game_s=10 * 60,
    stomp_ratio=3.0, stomp_min_loss=3000,
    slugfest_fights=8,
    one_big_min=4000, one_big_share=0.5,
    big_fight=5000,
    flip_min_fights=3, flip_fight_value=1500, flip_ratio=1.25,
    base_trade_window_s=90,
    float_bank=5000, float_ticks=8,
    fast_third_s=200,
    second_rax_s=150,
    leaver_gap_s=60,
    high_apm=200, low_apm=40,
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
}
NOISE_PREFIX = ('Build', 'Train', 'Morph', 'Research', 'Upgrade', 'Evolve', 'Cancel', 'Lift', 'Land',
                'WarpIn', 'Rally', 'Select', 'Load', 'Unload')

# Hand-made groups so a viewer can search "detection" without knowing ability names.
SKILL_GROUPS = {
    'macro': {'CalldownMULE', 'ChronoBoostEnergyCost', 'SpawnLarva', 'ExtraSupplies', 'EnergyRecharge'},
    'creep': {'CreepTumor', 'GenerateCreep'},
    'detection': {'ScannerSweep', 'OracleRevelation', 'SpawnChangeling'},
    'nuke': {'TacticalNukeStrike'},
    'stim': {'UseStimpack', 'StimpackRedirect'},
    'spell': {'GravitonBeam', 'ForceField', 'PsionicStorm', 'InfestorNeuralParasite', 'FungalGrowth',
              'QueenTransfusion', 'EMPRound', 'PrismaticAlignment', 'HallucinatePhoenix', 'KD8Charge',
              'AmorphousArmorcloud', 'SpawnLocustsTargeted', 'OracleWeapon'},
    'mode': {'SiegeMode', 'TankMode', 'PhasingMode', 'TransportMode', 'BurrowWidowMine', 'UnburrowWidowMine',
             'BurrowInfestor', 'UnburrowInfestor', 'CloakGhost', 'OracleWeaponOff',
             'OverseerMorphtoOverseerSiegeMode'},
    'boost': {'MedivacSpeedBoost', 'HydraliskFrenzy'},
}
GROUP_OF = {a: g for g, names in SKILL_GROUPS.items() for a in names}

JUNK_UNITS = {
    'Larva', 'Egg', 'Broodling', 'BroodlingEscort', 'Locust', 'LocustMP', 'LocustMPFlying', 'Interceptor',
    'AutoTurret', 'CreepTumor', 'CreepTumorBurrowed', 'CreepTumorQueen', 'ParasiticBombDummy', 'MULE',
    'Changeling', 'ChangelingMarine', 'ChangelingZealot', 'ChangelingZergling', 'ForceField',
}
WORKERS = {'SCV', 'Probe', 'Drone'}
TOWNHALLS = {'Nexus', 'CommandCenter', 'Hatchery'}
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
RACE_LETTER = {'Terran': 'T', 'Protoss': 'P', 'Zerg': 'Z'}


def is_skill(name):
    return bool(name) and name not in NOISE_EXACT and not name.startswith(NOISE_PREFIX)


def is_cosmetic(name):
    return name.startswith(('Reward', 'Spray')) or name.endswith('Spray')


# ----------------------------------------------------------------------------
# Parsing: replay file -> plain dict of rows
# ----------------------------------------------------------------------------

def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def player_key(p):
    """Stable id of a player. Names can change, the toon id cannot."""
    toon = getattr(p, 'toon_id', 0)
    if not toon:
        return 'name:' + p.name
    return '%s-%s-%s' % (getattr(p, 'region', '?'), getattr(p, 'subregion', '?'), toon)


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
    race_of = {p.pid: p.play_race for p in players}
    keys = {p.pid: player_key(p) for p in players}

    stats = defaultdict(list)
    deaths, hall_deaths, structs, born, upgrades, morphs = [], [], defaultdict(list), defaultdict(list), [], []
    starts = {}
    for e in r.tracker_events:
        n = type(e).__name__
        if n == 'PlayerStatsEvent':
            stats[e.pid].append(e)
        elif n == 'UnitBornEvent':
            u = e.unit
            if e.frame == 0 and e.unit_type_name in TOWNHALLS:
                starts[e.control_pid] = (e.x, e.y)
            elif u and u.is_army and u.name not in JUNK_UNITS and (u.minerals or u.vespene):
                born[e.control_pid].append(dict(t=e.frame / FPS, name=e.unit_type_name))
        elif n == 'UnitInitEvent':
            u = e.unit
            if u and u.name not in JUNK_UNITS and not u.name.startswith('CreepTumor'):
                structs[e.control_pid].append(dict(t=e.frame / FPS, name=e.unit_type_name, x=e.x, y=e.y))
        elif n == 'UnitTypeChangeEvent':
            u = e.unit
            if u and u.owner and e.unit_type_name in TECH_MORPHS:
                morphs.append((u.owner.pid, e.frame / FPS, e.unit_type_name))
        elif n == 'UpgradeCompleteEvent':
            if not is_cosmetic(e.upgrade_type_name):
                upgrades.append((e.pid, e.frame / FPS, e.upgrade_type_name))
        elif n == 'UnitDiedEvent':
            u = e.unit
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
                    cmds[e.player.pid].append((t, ab))
                elif ab.startswith(('Research', 'Evolve', 'Upgrade')):
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
                            p.play_race, p.result or '', mmr, m.get('APM'), round(apm_est[p.pid], 1)))
        buckets = Counter(int(f / FPS // 30) for f in actions[p.pid])
        apm_rows += [(p.pid, b, c) for b, c in sorted(buckets.items())]

    # ---- build steps and timings -------------------------------------------
    step_rows, timing_rows, builds = [], [], {}
    for p in players:
        pid, race = p.pid, p.play_race
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
    comp = {tn: ''.join(sorted(RACE_LETTER.get(race_of[q], '?') for q in race_of if team_of[q] == tn)) for tn in teams}
    tg.add(None, 'map', r.map_name, 'exact', r.map_name)
    tg.add(None, 'mode', r.type, 'exact', r.type)
    tg.add(None, 'matchup', 'v'.join(sorted(comp.values())), 'exact', 'v'.join(sorted(comp.values())))
    if dur >= CFG['long_game_s']:
        tg.add(None, 'shape', 'long', 'inferred', 'long game', 'lasted %s' % mmss(dur))
    if dur < CFG['short_game_s']:
        tg.add(None, 'shape', 'short', 'inferred', 'short game', 'lasted %s' % mmss(dur))

    team_loss = {tn: sum(d['value'] for d in deaths if d['team'] == tn) for tn in teams}
    if len(teams) >= 2:
        ranked = sorted(team_loss.items(), key=lambda kv: kv[1])
        lo, hi = ranked[0], ranked[-1]
        if hi[1] >= CFG['stomp_min_loss'] and hi[1] >= CFG['stomp_ratio'] * max(lo[1], 1):
            tg.add(None, 'shape', 'stomp', 'inferred', 'stomp',
                   'team %d lost %d, team %d lost %d' % (hi[0], hi[1], lo[0], lo[1]))
    fvals = []
    for i, f in enumerate(fights):
        fvals.append((i, sum(d['value'] for d in f)))
    total_fight = sum(v for _, v in fvals)
    if len(fights) >= CFG['slugfest_fights']:
        tg.add(None, 'shape', 'slugfest', 'inferred', 'slugfest', '%d fights' % len(fights),
               [(f[0]['t'], f[-1]['t']) for f in fights])
    if fvals:
        bi, bv = max(fvals, key=lambda x: x[1])
        if bv >= CFG['one_big_min'] and bv >= CFG['one_big_share'] * total_fight:
            tg.add(None, 'shape', 'one-big-battle', 'inferred', 'one big battle',
                   'one fight holds %d of %d lost value (%s)' % (bv, total_fight, mmss(fights[bi][0]['t'])),
                   [(fights[bi][0]['t'], fights[bi][-1]['t'])])
        bigs = [(f[0]['t'], f[-1]['t']) for f, (_, v) in zip(fights, fvals) if v >= CFG['big_fight']]
        if bigs:
            tg.add(None, 'spectacle', 'big-fight', 'inferred', 'big fight',
                   '%d fight(s) of %d+ lost value' % (len(bigs), CFG['big_fight']), bigs)
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
               'the losing side changed %d times over %d fights' % (flips, len(losers)))

    kills = sorted(hall_deaths, key=lambda h: h['t'])
    if kills:
        tg.add(None, 'spectacle', 'base-kill', 'inferred', 'base kill', '%d town hall(s) destroyed' % len(kills),
               [(k['t'], k['t']) for k in kills])
        for a in kills:
            if any(b['team'] != a['team'] and abs(b['t'] - a['t']) <= CFG['base_trade_window_s'] for b in kills):
                tg.add(None, 'spectacle', 'base-trade', 'inferred', 'base trade',
                       'both teams lost a town hall within %ds (around %s)' % (CFG['base_trade_window_s'], mmss(a['t'])),
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
        for t, ab in cmds[pid]:
            counts[ab] += 1
            tg.add(pid, 'skill', ab, 'exact', ab, '', [(t, t)])
            g = GROUP_OF.get(ab)
            if g:
                tg.add(pid, 'group', g, 'exact', g, '', [(t, t)])
        for u in {u['name'] for u in born[pid]}:
            tg.add(pid, 'unit', u, 'exact', u, '', [(min(x['t'] for x in born[pid] if x['name'] == u),) * 2])
        for q, t, n in upgrades:
            if q == pid:
                tg.add(pid, 'upgrade', n, 'exact', n, '', [(t, t)])
        cap = [e.frame / FPS for e in st if e.food_used >= 200]
        if cap:
            tg.add(pid, 'econ', 'maxed-out', 'inferred', 'maxed out', '200 supply from %s' % mmss(cap[0]),
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
                   'minerals above %d for %s' % (CFG['float_bank'], mmss(sum(b - a for a, b in spans))), spans)
        third = next((v for q, k, v in timing_rows if q == pid and k == 'third_base'), None)
        if third is not None and third < CFG['fast_third_s']:
            tg.add(pid, 'econ', 'fast-third', 'inferred', 'fast third', 'third base started at %s' % mmss(third),
                   [(third, third)])
        apm = meta.get(pid, {}).get('APM') or apm_est[pid]
        if apm >= CFG['high_apm']:
            tg.add(pid, 'people', 'high-apm', 'inferred', 'high APM', 'APM %d' % apm)
        if apm < CFG['low_apm']:
            tg.add(pid, 'people', 'low-apm', 'inferred', 'low APM', 'APM %d' % apm)
        lv = leaves.get(pid)
        if lv is not None and dur - lv >= CFG['leaver_gap_s']:
            tg.add(pid, 'people', 'leaver', 'inferred', 'left early',
                   'left at %s, game ended %s' % (mmss(lv), mmss(dur)), [(lv, lv)])

    chat_rows = []
    for m in r.messages:
        text = getattr(m, 'text', None)
        if not text:
            continue
        pid = m.player.pid if getattr(m, 'player', None) else None
        chat_rows.append((pid, m.frame / FPS, text, 1 if getattr(m, 'to_all', True) else 0))
    late = [c for c in chat_rows if c[1] >= CFG['chat_after_s']]
    if late:
        tg.add(None, 'people', 'chat', 'inferred', 'has chat', '%d message(s) after %s' % (len(late), mmss(CFG['chat_after_s'])),
               [(c[1], c[1]) for c in late])

    winner = r.winner.number if r.winner else None
    cast_rows = [(p.pid, ab, GROUP_OF.get(ab, ''), t) for p in players for t, ab in cmds[p.pid]]
    return dict(
        replay=dict(fingerprint=hashlib.sha1(('%s|%s' % (r.start_time.isoformat(), sorted(keys.values()))).encode()).hexdigest()[:16],
                    map=r.map_name, mode=r.type, category=r.category, build=r.build,
                    played_at=r.start_time.isoformat(), duration_s=round(dur, 1), winner_team=winner),
        players=player_rows, stats=stat_rows, casts=cast_rows, steps=step_rows, timings=timing_rows,
        fights=fight_rows, losses=loss_rows, apm=apm_rows, chat=chat_rows, tags=tg.rows, spans=tg.spans)


def parse_job(args):
    """Runs in a worker process. Returns (rel, sha, size, mtime, record_or_None, error_or_None)."""
    rel, path, sha, size, mtime = args
    try:
        return rel, sha, size, mtime, parse_replay(path), None
    except Exception as ex:  # a broken replay must never stop the scan
        return rel, sha, size, mtime, None, '%s: %s' % (type(ex).__name__, ex)


# ----------------------------------------------------------------------------
# Database
# ----------------------------------------------------------------------------

SCHEMA = '''
create table meta(key text primary key, value text);
create table replays(rid integer primary key, id text unique, fingerprint text, path text unique, size int, mtime real,
    map text, mode text, category text, build int, played_at text, duration_s real, winner_team int, error text);
create table players(rid int, pid int, toon text, name text, clan text, team int, race text, result text,
    mmr int, apm_blizzard real, apm_est real, primary key(rid, pid));
create table tags(tid integer primary key, rid int, pid int, family text, tag text, kind text, label text, evidence text);
create table tag_spans(tid int, t0 real, t1 real);
create table casts(rid int, pid int, ability text, grp text, t real);
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
CHILD_TABLES = ('players', 'tags', 'casts', 'build_steps', 'timings', 'fights', 'fight_losses', 'stats', 'apm', 'chat')


def open_db(path):
    fresh = not os.path.exists(path)
    con = sqlite3.connect(path, check_same_thread=False)
    if not fresh:
        try:
            ver = con.execute("select value from meta where key='parser_version'").fetchone()
        except sqlite3.Error:
            ver = None
        if not ver or ver[0] != str(PARSER_VERSION):
            con.close()
            os.remove(path)
            fresh = True
            con = sqlite3.connect(path, check_same_thread=False)
    if fresh:
        con.executescript(SCHEMA)
        con.execute("insert into meta values('parser_version', ?)", (str(PARSER_VERSION),))
        con.commit()
    return con


def delete_replay(con, sha):
    row = con.execute('select rid from replays where id=?', (sha,)).fetchone()
    if not row:
        return
    rid = row[0]
    con.execute('delete from tag_spans where tid in (select tid from tags where rid=?)', (rid,))
    for t in CHILD_TABLES:
        con.execute('delete from %s where rid=?' % t, (rid,))
    con.execute('delete from replays where rid=?', (rid,))


def store(con, sha, rel, size, mtime, rec, error):
    delete_replay(con, sha)
    if rec is None:
        iso = time.strftime('%Y-%m-%dT%H:%M:%S+00:00', time.gmtime(mtime))
        con.execute('insert into replays(id,path,size,mtime,played_at,error) values(?,?,?,?,?,?)',
                    (sha, rel, size, mtime, iso, error))
        return
    R = rec['replay']
    rid = con.execute('insert into replays(id,fingerprint,path,size,mtime,map,mode,category,build,played_at,'
                      'duration_s,winner_team) values(?,?,?,?,?,?,?,?,?,?,?,?)',
                      (sha, R['fingerprint'], rel, size, mtime, R['map'], R['mode'], R['category'], R['build'],
                       R['played_at'], R['duration_s'], R['winner_team'])).lastrowid

    def many(table, rows, width):
        if rows:
            con.executemany('insert into %s values(?%s)' % (table, ',?' * width), [(rid,) + tuple(r) for r in rows])
    many('players', rec['players'], 10)
    many('stats', rec['stats'], 12)
    many('casts', rec['casts'], 4)
    many('build_steps', rec['steps'], 7)
    many('timings', rec['timings'], 3)
    many('fights', rec['fights'], 5)
    many('fight_losses', rec['losses'], 4)
    many('apm', rec['apm'], 3)
    many('chat', rec['chat'], 4)
    tid = con.execute('select coalesce(max(tid), 0) from tags').fetchone()[0]
    tid_of, tag_rows = {}, []
    for pid, family, tag, kind, label, evidence in rec['tags']:
        tid += 1
        tid_of[(pid, tag)] = tid
        tag_rows.append((tid, rid, pid, family, tag, kind, label, evidence))
    con.executemany('insert into tags values(?,?,?,?,?,?,?,?)', tag_rows)
    con.executemany('insert into tag_spans values(?,?,?)',
                    [(tid_of[(pid, tag)], t0, t1) for pid, tag, t0, t1 in rec['spans']])


class State:
    def __init__(self, root, db_path):
        self.root, self.db_path = root, db_path
        self.lock = threading.Lock()   # held while writing and while the file is read for download
        self.version = 1               # bumps on every commit: the browser uses it to know when to reload
        self.scanning, self.done, self.total = False, 0, 0
        self.errors = []
        self.message = ''


def find_replays(root):
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith('.'))
        for fn in sorted(filenames):
            if fn.lower().endswith(EXT) and not fn.startswith('.'):
                out.append(os.path.join(dirpath, fn))
    return out


def rel_path(root, full):
    return os.path.relpath(full, root).replace(os.sep, '/')


def scan(state, workers=None):
    """Bring the database in line with the folder. Parses only new or changed files."""
    state.scanning, state.done, state.total, state.errors = True, 0, 0, []
    try:
        con = open_db(state.db_path)
        known = {row[0]: row[1:] for row in con.execute('select path,id,size,mtime from replays')}
        files = find_replays(state.root)
        on_disk = {rel_path(state.root, f): f for f in files}
        with state.lock:
            for rel, (rid, _, _) in list(known.items()):
                if rel not in on_disk:
                    delete_replay(con, rid)
                    del known[rel]
            con.commit()
        by_id = {v[0]: k for k, v in known.items()}
        todo = []
        for rel, full in on_disk.items():
            st = os.stat(full)
            k = known.get(rel)
            if k and k[1] == st.st_size and abs(k[2] - st.st_mtime) < 1e-3:
                continue
            sha = sha256_file(full)
            if sha in by_id and by_id[sha] != rel and by_id[sha] not in on_disk:
                with state.lock:  # file was moved or renamed: keep the parsed data
                    con.execute('update replays set path=?, size=?, mtime=? where id=?', (rel, st.st_size, st.st_mtime, sha))
                    con.commit()
                    state.version += 1
                continue
            if sha in by_id and by_id[sha] != rel:
                state.errors.append('duplicate of %s: %s' % (by_id[sha], rel))
                continue
            todo.append((rel, full, sha, st.st_size, st.st_mtime))
        state.total = len(todo)
        state.message = 'parsing %d replay(s)' % len(todo)

        def finish(res):
            rel, sha, size, mtime, rec, err = res
            with state.lock:
                store(con, sha, rel, size, mtime, rec, err)
                con.commit()
                state.version += 1
            state.done += 1
            if err:
                state.errors.append('%s: %s' % (rel, err))

        pool = None
        if len(todo) > 2:
            try:
                pool = ProcessPoolExecutor(max_workers=workers or max(1, min(6, (os.cpu_count() or 2) - 1)))
            except Exception:
                pool = None
        if pool:
            with pool:
                futs = [pool.submit(parse_job, t) for t in todo]
                for fut in as_completed(futs):
                    finish(fut.result())
        else:
            for t in todo:
                finish(parse_job(t))
        con.close()
        state.message = 'ready'
    except Exception as ex:
        state.message = 'scan failed: %s' % ex
        state.errors.append(state.message)
    finally:
        state.scanning = False
        state.version += 1


# ---- server and page are added below ----

def main():
    ap = argparse.ArgumentParser(description='Browse StarCraft II replays as a searchable library')
    ap.add_argument('--root', default=os.getcwd(), help='folder to scan (default: current folder)')
    ap.add_argument('--scan-only', action='store_true', help='update the database and exit')
    args = ap.parse_args()
    root = os.path.realpath(args.root)
    os.makedirs(os.path.join(root, '.scry'), exist_ok=True)
    state = State(root, os.path.join(root, '.scry', 'scry.sqlite'))
    t = time.time()
    scan(state)
    print('scanned %d replay(s) in %.1fs: %s' % (state.done, time.time() - t, state.message))
    for e in state.errors:
        print('  ', e)


if __name__ == '__main__':
    main()
