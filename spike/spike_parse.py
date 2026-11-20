#!/usr/bin/env python3
"""SPIKE: parse StarCraft II replays and print inferences a DJ-style timeline viewer could show.

Run isolated:  python3 -I spike_parse.py --libs <dir with sc2reader+mpyq> [files or dirs] [--skill NAME]
Exploration only. Replays are read-only untrusted input.
"""
import argparse, glob, json, os, statistics, sys
from collections import Counter, defaultdict
from math import hypot

FPS = 22.4  # in-game clock at Faster speed. event.second (frame/16) is WRONG for this.
BIN = 30  # seconds per activity-strip character
BLOCKS = "▁▂▃▄▅▆▇█"

CMD_EVENTS = {"BasicCommandEvent", "TargetPointCommandEvent", "TargetUnitCommandEvent", "DataCommandEvent"}
# Camera events are over half of all events, so they are excluded from APM.
APM_EVENTS = CMD_EVENTS | {"SelectionEvent", "GetControlGroupEvent", "SetControlGroupEvent", "AddToControlGroupEvent"}

NOISE_EXACT = {"RightClick", "Attack", "Stop", "HoldPosition", "ReturnCargo", "SetWorkerRally", "Move",
               "Patrol", "Scan", "SmartCommand", "CreepTumor", "RaiseSupplyDepot", "LowerSupplyDepot",
               "TankMode", "HoldFireGhost", "BurrowWidowMine"}
# Production, construction and research commands are macro actions, not "skills".
NOISE_PREFIX = ("Build", "Train", "Morph", "Research", "Upgrade", "Evolve", "Cancel", "Lift", "Land",
                "WarpIn", "Rally", "Select", "Load", "Unload")
JUNK_UNITS = {"Larva", "Egg", "Broodling", "BroodlingEscort", "Locust", "LocustMP", "LocustMPFlying", "Interceptor",
              "AutoTurret", "CreepTumor", "CreepTumorBurrowed", "CreepTumorQueen", "ParasiticBombDummy",
              "MULE", "Changeling", "ChangelingMarine", "ChangelingZealot", "ChangelingZergling", "ForceField"}
TOWNHALLS = {"Nexus", "CommandCenter", "Hatchery"}
TECH_MORPHS = {"OrbitalCommand", "PlanetaryFortress", "Lair", "Hive", "GreaterSpire", "WarpGate"}
# Empirical: adding these two to APM_EVENTS fits Blizzard's APM to within ~9% on these replays.
FIT_EVENTS = {"CommandManagerStateEvent", "UpdateTargetUnitCommandEvent"}
COSMETIC_PREFIX = ("Reward", "Spray")


def mmss(sec):
    sec = int(sec)
    return "%d:%02d" % (sec // 60, sec % 60)


def is_skill(name):
    return bool(name) and name not in NOISE_EXACT and not name.startswith(NOISE_PREFIX)


def is_cosmetic(name):
    return name.startswith(COSMETIC_PREFIX) or name.endswith("Spray")


def spark(values, vmax):
    """One block char per value. A middle dot means exactly zero."""
    out = []
    for v in values:
        out.append("·" if v <= 0 or vmax <= 0 else BLOCKS[min(7, int(v / vmax * 7.999))])
    return "".join(out)


def ruler(nbins):
    """Label every 5 minutes (10 bins)."""
    return "".join(("%dm" % (k * 5)).ljust(10) for k in range(nbins // 10 + 1))[:nbins]


class Replay:
    """Everything the report functions need, extracted once per replay."""

    def __init__(self, path, sc2reader, mpyq):
        self.path = path
        self.name = os.path.basename(path)
        r = self.r = sc2reader.load_replay(path, load_level=4)
        self.dur = max(r.frames, max((e.frame for e in r.events), default=0)) / FPS
        self.meta = {}
        try:
            a = mpyq.MPQArchive(path)
            raw = a.read_file("replay.gamemetadata.json")
            self.meta = {p["PlayerID"]: p for p in json.loads(raw).get("Players", [])}
        except Exception as ex:  # metadata is optional in some builds
            print("  (no gamemetadata.json: %s)" % ex)
        self.players = list(r.players)
        self.team_of = {p.pid: p.team.number for p in self.players}
        self.stats = defaultdict(list)  # pid -> PlayerStatsEvent list
        self.deaths = []  # dicts: t, pid, team, value, name, x, y
        self.born = defaultdict(list)  # pid -> (t, unit type) for units
        self.builds = defaultdict(list)  # pid -> (t, label) structures, morphs, upgrades
        self.actions = defaultdict(list)  # pid -> frames of APM-relevant actions
        self.cmds = defaultdict(list)  # pid -> (frame, ability_name)
        self.extra = Counter()  # pid -> count of events that empirically close the gap to Blizzard APM
        for e in r.tracker_events:
            self._tracker(e, type(e).__name__)
        for e in r.game_events:
            n = type(e).__name__
            if n in FIT_EVENTS and e.player is not None:
                self.extra[e.player.pid] += 1
            if n in APM_EVENTS and e.player is not None:
                self.actions[e.player.pid].append(e.frame)
                if n in CMD_EVENTS and getattr(e, "ability_name", None):
                    self.cmds[e.player.pid].append((e.frame, e.ability_name))

    def _tracker(self, e, n):
        if n == "PlayerStatsEvent":
            self.stats[e.pid].append(e)
        elif n == "UnitDiedEvent":
            u = e.unit
            if not u or not u.owner or u.name in JUNK_UNITS:
                return
            # Blizzard's resources_lost ignores suicides and drones that morph into buildings
            # (Drone death with unknown killer). Skipping them made our totals match within ~5%.
            if e.killer_pid == u.owner.pid or (u.name == "Drone" and not e.killer_pid):
                return
            value = (u.minerals or 0) + (u.vespene or 0)
            if value > 0:
                self.deaths.append(dict(t=e.frame / FPS, pid=u.owner.pid, team=self.team_of.get(u.owner.pid),
                                        value=value, name=u.name, x=e.x, y=e.y))
        elif n == "UnitBornEvent":
            u = e.unit
            if u and u.is_army and u.name not in JUNK_UNITS and (u.minerals or u.vespene):
                self.born[e.control_pid].append((e.frame / FPS, e.unit_type_name))
        elif n == "UnitInitEvent":
            u = e.unit
            if u and u.name not in JUNK_UNITS and not u.name.startswith("CreepTumor"):
                self.builds[e.control_pid].append((e.frame / FPS, e.unit_type_name, "struct"))
        elif n == "UnitTypeChangeEvent":
            u = e.unit
            if u and u.owner and e.unit_type_name in TECH_MORPHS:
                self.builds[u.owner.pid].append((e.frame / FPS, e.unit_type_name, "morph"))
        elif n == "UpgradeCompleteEvent":
            if not is_cosmetic(e.upgrade_type_name):
                self.builds[e.pid].append((e.frame / FPS, e.upgrade_type_name, "upgrade"))

    def team_label(self, num):
        return "Team %d (%s)" % (num, ", ".join(p.name for p in self.players if p.team.number == num))

    def my_apm(self, pid, fit=False):
        return (len(self.actions[pid]) + (self.extra[pid] if fit else 0)) / (self.dur / 60)


def print_header(R):
    r = R.r
    win = R.team_label(r.winner.number) if r.winner else "unknown"
    print("=" * 100)
    print("FILE  %s" % R.name)
    print("MAP   %s | %s (%s) | build %s | %s" % (r.map_name, r.type, r.category, r.build, r.start_time.date()))
    print("TIME  %s (frames/22.4; Blizzard 'Duration' is frames/16 so it is %s)" % (mmss(R.dur), mmss(R.dur / 1.4)))
    print("WIN   %s" % win)


def print_players(R):
    print("\n-- Players --")
    print("  %-14s %-5s %-8s %-5s %-5s %9s %9s %9s" % ("name", "team", "race", "res", "MMR", "APM(blz)", "APM(mine)", "APM(fit)"))
    for p in R.players:
        m = R.meta.get(p.pid, {})
        # -36400 is a "no rating" sentinel in both sources
        mmr = next((v for v in (p.init_data.get("scaled_rating"), m.get("MMR")) if v and v > 0), None)
        print("  %-14s %-5s %-8s %-5s %-5s %9s %9.0f %9.0f" % (p.name, p.team.number, p.play_race, p.result,
                                                          mmr or "-", m.get("APM", "-"), R.my_apm(p.pid), R.my_apm(p.pid, True)))


def print_builds(R):
    print("\n-- Build order (first notable items) and first-seen army units --")
    for p in R.players:
        items, seen, counts = [], set(), Counter()
        for t, name, kind in sorted(R.builds[p.pid]):
            counts[name] += 1
            if kind == "struct":
                if name in ("SupplyDepot", "Pylon") and counts[name] > 3:
                    continue
                if name in ("Refinery", "Extractor", "Assimilator") and counts[name] > 2:
                    continue
                if name in seen and name not in TOWNHALLS:
                    continue
            elif name in seen:
                continue
            seen.add(name)
            # the starting base is a UnitBornEvent, so the first built townhall is base #2
            label = name + (" #%d" % (counts[name] + 1) if name in TOWNHALLS else "")
            items.append("%s %s" % (mmss(t), label))
        print("  %s (%s)" % (p.name, p.play_race))
        print("    build: " + ", ".join(items[:14]))
        first = {}
        for t, name in sorted(R.born[p.pid]):
            first.setdefault(name, t)
        print("    army : " + (", ".join("%s %s" % (n, mmss(t)) for n, t in list(first.items())[:12]) or "-"))


def bin_series(R, pid):
    nb = int(R.dur // BIN) + 1
    apm, sup, fgt, dth = [0.0] * nb, [0.0] * nb, [0.0] * nb, [0.0] * nb
    for f in R.actions[pid]:
        apm[min(nb - 1, int(f / FPS // BIN))] += 1 / (BIN / 60)
    ticks = defaultdict(list)
    prev = None
    for e in R.stats[pid]:
        b = min(nb - 1, int(e.frame / FPS // BIN))
        ticks[b].append(e.food_used)
        if prev:  # delta of cumulative killed + lost since the previous tick
            fgt[b] += (e.resources_killed - prev.resources_killed) + (e.resources_lost - prev.resources_lost)
        prev = e
    for b, v in ticks.items():
        sup[b] = sum(v) / len(v)
    for d in R.deaths:
        if d["pid"] == pid:
            dth[min(nb - 1, int(d["t"] // BIN))] += d["value"]
    return apm, sup, fgt, dth


def print_strips(R):
    nb = int(R.dur // BIN) + 1
    series = {p.pid: bin_series(R, p.pid) for p in R.players}
    fmax = max([max(s[2]) for s in series.values()] + [1])
    dmax = max([max(s[3]) for s in series.values()] + [1])
    print("\n-- Activity strip: 1 char = %ds. APM/row-max; SUP=food_used/200; FGT=killed+lost delta; DTH=own death value --" % BIN)
    print("  %-22s %s" % ("", ruler(nb)))
    for p in R.players:
        apm, sup, fgt, dth = series[p.pid]
        tag = "%s/%s" % (p.name[:9], p.play_race[0])
        print("  %-12s APM %4.0f pk %s" % (tag, max(apm), spark(apm, max(apm))))
        print("  %-12s SUP %4d max %s" % ("", max(sup), spark(sup, 200)))
        print("  %-12s FGT %4d pk %s" % ("", max(fgt), spark(fgt, fmax)))
        print("  %-12s DTH %4d pk %s" % ("", max(dth), spark(dth, dmax)))


def wmedian(pairs):
    pairs = sorted(pairs)
    half, acc = sum(w for _, w in pairs) / 2, 0
    for v, w in pairs:
        acc += w
        if acc >= half:
            return v


def cum_stat(R, pid, t, key, after):
    """Cumulative stat at the last tick before t, or at the first tick at or after t."""
    ticks = R.stats[pid]
    if after:
        return next((getattr(e, key) for e in ticks if e.frame / FPS >= t), getattr(ticks[-1], key))
    return next((getattr(e, key) for e in reversed(ticks) if e.frame / FPS < t), 0)


def detect_fights(deaths, dur, step=5, win=4, thresh=600, merge_gap=2, min_total=800):
    """Sliding window of win*step = 20 s over death value. Merge nearby active bins."""
    nb = int(dur // step) + 1
    val = [0] * nb
    for d in deaths:
        val[int(d["t"] // step)] += d["value"]
    active = [False] * nb
    for i in range(nb):
        if sum(val[i:i + win]) >= thresh:
            for j in range(i, min(nb, i + win)):
                active[j] = active[j] or val[j] > 0
    spans, cur = [], None
    for i in range(nb):
        if active[i]:
            if cur and i - cur[1] <= merge_gap:
                cur[1] = i
            else:
                cur = [i, i]
                spans.append(cur)
    fights = [[d for d in deaths if a <= int(d["t"] // step) <= b] for a, b in spans]
    return [f for f in fights if sum(d["value"] for d in f) >= min_total]


def print_fights(R):
    teams = sorted({p.team.number for p in R.players})
    print("\n-- Fights (death value, 20 s window >= 600, total >= 800) --")
    for tn in teams:  # sanity: death-value total vs Blizzard's own resources_lost counter
        pids = [p.pid for p in R.players if p.team.number == tn]
        mine = sum(d["value"] for d in R.deaths if d["team"] == tn)
        theirs = sum(R.stats[q][-1].resources_lost for q in pids if R.stats[q])
        print("  check Team %d: death-value lost %d vs stats resources_lost %d" % (tn, mine, theirs))
    for f in detect_fights(R.deaths, R.dur):
        t0, t1 = f[0]["t"], f[-1]["t"]
        lost = {tn: sum(d["value"] for d in f if d["team"] == tn) for tn in teams}
        slost = {tn: sum(cum_stat(R, p.pid, t1, "resources_lost", True) - cum_stat(R, p.pid, t0, "resources_lost", False)
                         for p in R.players if p.team.number == tn and R.stats[p.pid]) for tn in teams}
        mx = wmedian([(d["x"], d["value"]) for d in f])
        my = wmedian([(d["y"], d["value"]) for d in f])
        spread = statistics.median(hypot(d["x"] - mx, d["y"] - my) for d in f)
        hi, lo = sorted(lost.values())[-1], sorted(lost.values())[0]
        label = "even trade" if hi < 1.25 * lo else "Team %d lost more" % max(lost, key=lost.get)
        top = Counter()
        for d in f:
            top[d["name"]] += d["value"]
        print("  %s-%s  %s  @(%d,%d) spread %d  stat-lost %s  [%s]" % (
            mmss(t0), mmss(t1), "  ".join("T%d lost %5d" % (k, v) for k, v in lost.items()), mx, my, spread,
            "/".join(str(v) for v in slost.values()), label))
        print("      main losses: " + ", ".join("%s %d" % kv for kv in top.most_common(4)))


def print_skills(R):
    print("\n-- Skills and macro habits --")
    for p in R.players:
        cmds = R.cmds[p.pid]
        uses, first = Counter(), {}
        for f, name in cmds:
            if is_skill(name):
                uses[name] += 1
                first.setdefault(name, f / FPS)
        print("  %s (%s)" % (p.name, p.play_race))
        print("    top skills: " + (", ".join("%s x%d (first %s)" % (n, c, mmss(first[n])) for n, c in uses.most_common(7)) or "-"))
        habits = []
        for label, ability in (("injects", "SpawnLarva"), ("MULEs", "CalldownMULE"),
                               ("chronos", "ChronoBoostEnergyCost"), ("scans", "ScannerSweep")):
            ts = [f / FPS for f, n in cmds if n == ability]
            if ts:
                gap = statistics.median(b - a for a, b in zip(ts, ts[1:])) if len(ts) > 1 else 0
                habits.append("%d %s%s" % (len(ts), label, " (median gap %.0fs)" % gap if ability == "SpawnLarva" else ""))
        st = R.stats[p.pid]
        if st:
            bank = max(st, key=lambda e: e.minerals_current + e.vespene_current)
            wk = max(st, key=lambda e: e.workers_active_count)
            cap = [e for e in st if e.food_used >= 200]
            habits.append("peak bank %d at %s" % (bank.minerals_current + bank.vespene_current, mmss(bank.frame / FPS)))
            habits.append("peak workers %d at %s" % (wk.workers_active_count, mmss(wk.frame / FPS)))
            habits.append("200 supply: %s" % ("%d ticks (~%ds), from %s" % (len(cap), len(cap) * 160 / FPS, mmss(cap[0].frame / FPS)) if cap else "never"))
        print("    habits: " + "; ".join(habits))


def print_chat(R):
    print("\n-- Chat (in-game clock from frame; message.second is frame/16) --")
    msgs = [m for m in R.r.messages if getattr(m, "text", None)]
    for m in msgs:
        print("  %s  %-14s %s%s" % (mmss(m.frame / FPS), m.player.name if m.player else m.name, "" if m.to_all else "[allies] ", m.text))
    if not msgs:
        print("  (none)")


def skill_search(replays, needle):
    needle = needle.lower()
    for R in replays:
        for p in R.players:
            by = defaultdict(list)
            for f, name in R.cmds[p.pid]:
                if needle in name.lower():
                    by[name].append(f / FPS)
            for name, ts in sorted(by.items()):
                shown = " ".join(mmss(t) for t in ts[:40]) + (" ... +%d more" % (len(ts) - 40) if len(ts) > 40 else "")
                print("%s | %s (%s) | %s x%d | %s" % (R.name, p.name, p.play_race, name, len(ts), shown))


def collect_paths(inputs):
    out = []
    for i in inputs:
        out += sorted(glob.glob(os.path.join(i, "*.SC2Replay"))) if os.path.isdir(i) else [i]
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("inputs", nargs="*", default=["/home/fif/git/scry"], help="replay files or folders")
    ap.add_argument("--libs", required=True, help="folder with sc2reader and mpyq (pip --target)")
    ap.add_argument("--skill", help="case-insensitive ability substring: search all replays and exit")
    args = ap.parse_args()
    sys.path.insert(0, args.libs)  # needed because the script runs with python3 -I
    import sc2reader, mpyq

    replays = []
    for path in collect_paths(args.inputs):
        try:
            replays.append(Replay(path, sc2reader, mpyq))
        except Exception as ex:
            print("FAILED to parse %s: %r" % (path, ex))
    if args.skill:
        skill_search(replays, args.skill)
        return
    for R in replays:
        for fn in (print_header, print_players, print_builds, print_strips, print_fights, print_skills, print_chat):
            try:
                fn(R)
            except Exception as ex:  # keep going: a spike should show which section breaks
                print("  !! %s failed: %r" % (fn.__name__, ex))


if __name__ == "__main__":
    main()
