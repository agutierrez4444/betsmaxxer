"""Build the soccer dashboard data: one folder per league, one file per match day.

    py -m pipeline.soccer_build                    # every league in config.yaml
    py -m pipeline.soccer_build --league eng.1     # just one
    py -m pipeline.soccer_build --days-back 7

Writes site/data/soccer/<league>/<date>.json, an index.json per league and
site/data/soccer/leagues.json for the league picker.
Stats for a day only use matches played before that day.
"""
import argparse
import json
import os
from collections import defaultdict
from datetime import date, datetime, timedelta

import yaml

from . import scoring, soccer_api, soccer_scoring, stats, trends
from .build import _rest
from .nhl_api import ROOT

OUT_DIR = os.path.join(ROOT, "site", "data", "soccer")
SCOPES = [{"key": "game", "label": "Full match"}, {"key": "h1", "label": "1st half"}, {"key": "h2", "label": "2nd half"}]


def load_config():
    with open(os.path.join(ROOT, "config.yaml"), encoding="utf-8") as f:
        return yaml.safe_load(f)["soccer"]


def _dec(ml):
    if ml in (None, 0):
        return None
    return round(1 + ml / 100.0, 2) if ml > 0 else round(1 + 100.0 / -ml, 2)


def _avg(vals):
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / float(len(vals)), 3) if vals else None


def _pct(flags):
    flags = [f for f in flags if f is not None]
    return round(sum(1 for f in flags if f) / float(len(flags)), 3) if flags else None


# ---------- matches ----------

class Source(object):
    """Scoreboards and summaries for one league, memoized for the run."""

    def __init__(self, league, today_str):
        self.league, self.today = league, today_str
        self.days, self.sums = {}, {}

    def day(self, d):
        if d not in self.days:
            try:
                self.days[d] = soccer_api.scoreboard(self.league, d, self.today)
            except RuntimeError as e:
                print("  ! scoreboard %s %s: %s" % (self.league, d, e))
                self.days[d] = {"games": [], "calendar": [], "season": {}, "league": {}}
        return self.days[d]

    def summary(self, gid):
        if gid not in self.sums:
            try:
                self.sums[gid] = soccer_api.summary(self.league, gid)
            except RuntimeError as e:
                print("  ! summary %s: %s" % (gid, e))
                self.sums[gid] = None
        return self.sums[gid]

    def calendar(self, d):
        """Match days of the season `d` falls in. Cups like the Champions League
        don't list dates, so their Tuesdays and Wednesdays are scanned instead."""
        b = self.day(d)
        if b["calendar"]:
            return b["calendar"], b["season"]
        s = b["season"]
        if not s.get("start"):
            return [], s
        start, end = stats.parse_date(s["start"]), min(stats.parse_date(s["end"]), stats.parse_date(self.today) + timedelta(days=14))
        days = []
        while start <= end:
            if start.weekday() in (1, 2):
                days.append(start.isoformat())
            start += timedelta(days=1)
        return days, s


def collect_games(src, day_str, prior_per_team):
    """Finished matches before `day_str`: all of this season, plus last season's
    latest `prior_per_team` for every team in this season."""
    cal, season = src.calendar(day_str)
    out = []
    for d in sorted((x for x in cal if x < day_str), reverse=True):
        out += [g for g in src.day(d)["games"] if g["final"]]
    teams = {t for g in out for t in (g["home"]["abbr"], g["away"]["abbr"])}
    teams |= {t for g in src.day(day_str)["games"] for t in (g["home"]["abbr"], g["away"]["abbr"])}
    if season.get("start"):
        prev = (stats.parse_date(season["start"]) - timedelta(days=1)).isoformat()
        pcal, _ = src.calendar(prev)
        count = defaultdict(int)
        for d in sorted(pcal, reverse=True):
            if all(count[t] >= prior_per_team for t in teams):
                break
            for g in src.day(d)["games"]:
                if not g["final"]:
                    continue
                h, a = g["home"]["abbr"], g["away"]["abbr"]
                if (h in teams and count[h] < prior_per_team) or (a in teams and count[a] < prior_per_team):
                    count[h] += 1
                    count[a] += 1
                    out.append(g)
    return out, season


def _halves(g, side):
    """Goals per half [[for, against], [for, against]] from goal minutes (extra time excluded)."""
    h = [[0, 0], [0, 0]]
    for x in g["goals"]:
        if x["half"] > 2:
            continue
        h[x["half"] - 1][0 if x["side"] == side else 1] += 1
    return h


def team_logs(games, lines, lg=None, tag=None):
    """{team: [records newest first]} in the shape the shared code expects.
    `lg`/`tag` mark matches from another competition (for all-competition trends)."""
    logs = defaultdict(list)
    for g in games:
        ln = g.get("lines") or lines.get(g["id"]) or {}
        first = g["goals"][0]["side"] if g["goals"] else None
        cards = {s: sum(2 if c["red"] else 1 for c in g["cards"] if c["side"] == s) for s in ("home", "away")}
        for side, other in (("home", "away"), ("away", "home")):
            me, op = g[side], g[other]
            halves = _halves(g, side)
            et = any(x["half"] > 2 for x in g["goals"])
            logs[me["abbr"]].append({
                "gameId": g["id"], "date": g["date"], "start": g.get("start") or "", "season": g["season"],
                "tid": me.get("id"), "ab": me["abbr"], "lg": lg, "tag": ("%s %s" % (g["date"][5:], tag)) if tag else None,
                "opp": op["abbr"], "home": side == "home",
                "gf": me["score"], "ga": op["score"], "win": me["score"] > op["score"], "tie": me["score"] == op["score"],
                "end": "ET" if et else "REG", "periods": halves,
                "regGf": sum(x[0] for x in halves), "regGa": sum(x[1] for x in halves),
                "scoredFirst": None if first is None else first == side,
                "totalLine": ln.get("total"),
                "st": {"cornersF": me.get("corners"), "cornersA": op.get("corners"),
                       "cardsF": cards[side], "cardsA": cards[other],
                       "sotF": me.get("sot"), "sotA": op.get("sot"), "shotsF": me.get("shots"), "shotsA": op.get("shots")},
                "poss": me.get("poss"), "scorers": [],
            })
    for t in logs:
        logs[t].sort(key=lambda r: (r["date"], r["start"]), reverse=True)
    return logs


def aggregate(games, legs):
    if not games:
        return {"gp": 0}
    st = lambda k: _avg(r["st"].get(k) for r in games)
    out = {
        "gp": len(games),
        "gfPg": _avg(r["gf"] for r in games), "gaPg": _avg(r["ga"] for r in games),
        "regTotalPg": _avg(r["gf"] + r["ga"] for r in games),
        "winPct": _pct(r["win"] for r in games), "drawPct": _pct(r["tie"] for r in games),
        "ppg": _avg(3 if r["win"] else 1 if r["tie"] else 0 for r in games),
        "csPct": _pct(r["ga"] == 0 for r in games), "bttsPct": _pct(r["gf"] > 0 and r["ga"] > 0 for r in games),
        "over25Pct": _pct(r["gf"] + r["ga"] > 2.5 for r in games),
        "overPct": _pct(r["gf"] + r["ga"] > legs["total_line"] for r in games),
        "scored1pPct": _pct(r["periods"][0][0] > 0 for r in games),
        "conceded1pPct": _pct(r["periods"][0][1] > 0 for r in games),
        "firstPct": _pct(r["scoredFirst"] for r in games),
        "shotsFPg": st("shotsF"), "shotsAPg": st("shotsA"), "sotFPg": st("sotF"), "sotAPg": st("sotA"),
        "cornersFPg": st("cornersF"), "cornersAPg": st("cornersA"), "cardsPg": st("cardsF"),
        "possPct": _avg((r["poss"] or 0) / 100.0 if r["poss"] is not None else None for r in games),
        "byPeriod": [{"gf": _avg(r["periods"][i][0] for r in games), "ga": _avg(r["periods"][i][1] for r in games)} for i in range(2)],
    }
    out["gdPg"] = round(out["gfPg"] - out["gaPg"], 3)
    home = [r for r in games if r["home"]]
    away = [r for r in games if not r["home"]]
    out["home"] = {"gp": len(home), "gfPg": _avg(r["gf"] for r in home), "gaPg": _avg(r["ga"] for r in home)}
    out["away"] = {"gp": len(away), "gfPg": _avg(r["gf"] for r in away), "gaPg": _avg(r["ga"] for r in away)}
    return out


def leg_rates(games, legs, as_favorite):
    """Default combo: favorite wins + match over the total line + favorite scores in the 1st half."""
    rows = []
    for r in games:
        ml = r["win"] if as_favorite else (not r["win"] and not r["tie"])
        over = r["gf"] + r["ga"] > legs["total_line"]
        fh = (r["periods"][0][0] if as_favorite else r["periods"][0][1]) > legs["first_half_team_line"]
        rows.append((ml, over, fh, ml and over and fh))
    if not rows:
        return None
    return {"gp": len(rows), "ml": _pct(x[0] for x in rows), "over": _pct(x[1] for x in rows),
            "firstPeriod": _pct(x[2] for x in rows), "all3": _pct(x[3] for x in rows)}


# ---------- players ----------

def player_rows(summary, logs_cur):
    """summary(gameId, league) -> slim summary. Rows are grouped under the team's
    key in `logs_cur`, even when its abbreviation differs in another competition."""
    side_of = {(r["gameId"], r["ab"]): (t, r) for t in logs_cur for r in logs_cur[t]}
    lg_of = {r["gameId"]: r["lg"] for t in logs_cur for r in logs_cur[t]}
    by_team = defaultdict(lambda: defaultdict(list))
    flat = []
    for gid in sorted(lg_of):
        s = summary(gid, lg_of[gid])
        if not s:
            continue
        for p in s["players"]:
            hit = side_of.get((gid, p["team"]))
            if not hit or not p["starter"]:
                continue
            team, log = hit
            row = dict(p)
            row.update({"date": log["date"], "opp": log["opp"], "home": log["home"], "tag": log.get("tag")})
            by_team[team][p["id"]].append(row)
            flat.append(row)
    for t in by_team:
        for pid in by_team[t]:
            rows = by_team[t][pid]
            rows.sort(key=lambda r: r["date"], reverse=True)
            # a player's usual position = where they started most
            pos = max(("G", "D", "M", "F"), key=lambda x: sum(1 for r in rows if r["pos"] == x))
            for r in rows:
                r["pos"] = pos
    return by_team, flat


def _started(r):
    return r["starter"]


def _v(k):
    return lambda r: r.get(k, 0)


OUTFIELD = {"D", "M", "F"}
PLAYER_PROPS = [
    {"stat": "g", "label": "goals", "lines": [0.5, 1.5], "sides": ["over"], "pos": OUTFIELD, "usage": _started, "value": _v("g")},
    {"stat": "a", "label": "assists", "lines": [0.5], "sides": ["over"], "pos": OUTFIELD, "usage": _started, "value": _v("a")},
    {"stat": "shots", "label": "shots", "lines": [0.5, 1.5, 2.5, 3.5], "sides": ["over", "under"], "pos": OUTFIELD, "usage": _started, "value": _v("shots")},
    {"stat": "sot", "label": "shots on target", "lines": [0.5, 1.5, 2.5], "sides": ["over", "under"], "pos": OUTFIELD, "usage": _started, "value": _v("sot")},
    {"stat": "yc", "label": "yellow cards", "lines": [0.5], "sides": ["over"], "pos": OUTFIELD, "usage": _started, "value": _v("yc")},
    {"stat": "saves", "label": "saves", "lines": [1.5, 2.5, 3.5, 4.5], "sides": ["over", "under"], "pos": {"G"}, "usage": _started, "value": _v("saves")},
]
BUILDER_PROPS = [
    {"key": "g", "label": "Goals", "pos": "DMF"},
    {"key": "a", "label": "Assists", "pos": "DMF"},
    {"key": "shots", "label": "Shots", "pos": "DMF"},
    {"key": "sot", "label": "Shots on target", "pos": "DMF"},
    {"key": "yc", "label": "Yellow cards", "pos": "DMF"},
    {"key": "saves", "label": "Saves", "pos": "G"},
]
RESULT_KEYS = [p["key"] for p in BUILDER_PROPS]


def _team_props():
    P = []

    def add(key, label, group, rank=0, leg=None, pred=None):
        p = {"key": key, "label": label, "group": group, "rank": rank}
        if leg:
            p["leg"] = leg
        if pred:
            p["pred"] = pred
        P.append(p)

    def over_posted(o):
        if o.get("totalLine") is None or o["fh"] + o["fa"] == o["totalLine"]:
            return None
        return o["fh"] + o["fa"] > o["totalLine"]

    add("ml", "{T} wins", "win", 0, {"m": "win", "s": "game", "t": "home"})
    add("dnb", "{T} doesn't lose", "dnl", pred=lambda o: o["fh"] >= o["fa"])
    add("draw", "{T} games end in a draw", "draw", 0, {"m": "win", "s": "game", "t": "draw"})
    add("first", "{T} scores first", "first", pred=lambda o: None if o["first"] is None else o["first"] == "home")
    add("h1w", "{T} leads at halftime", "h1win", 0, {"m": "win", "s": "h1", "t": "home"})
    add("h1s", "{T} scores in the 1st half", "h1score", 0, {"m": "team", "s": "h1", "t": "home", "side": "over", "line": 0.5})
    add("h2s", "{T} scores in the 2nd half", "h2score", 0, {"m": "team", "s": "h2", "t": "home", "side": "over", "line": 0.5})
    add("h1a", "{T} concedes in the 1st half", "h1allow", 0, {"m": "team", "s": "h1", "t": "away", "side": "over", "line": 0.5})
    add("both_h", "{T} scores in both halves", "bothh", pred=lambda o: o["p"][0][0] > 0 and o["p"][1][0] > 0)
    add("cs", "{T} keeps a clean sheet", "cs", 0, {"m": "team", "s": "game", "t": "away", "side": "under", "line": 0.5})
    add("fts", "{T} fails to score", "fts", 0, {"m": "team", "s": "game", "t": "home", "side": "under", "line": 0.5})
    for line in (1.5, 2.5):
        add("tt_o%s" % line, "{T} scores %d+ goals" % (line + 0.5), "tt_over", line, {"m": "team", "s": "game", "t": "home", "side": "over", "line": line})
        add("ta_o%s" % line, "{T} concedes %d+ goals" % (line + 0.5), "ta_over", line, {"m": "team", "s": "game", "t": "away", "side": "over", "line": line})
    add("by2", "{T} wins by 2+", "winby", 1.5, {"m": "spread", "s": "game", "t": "home", "line": -1.5})
    for line in (1.5, 2.5, 3.5):
        add("g_o%s" % line, "{T} matches: over %s goals" % line, "g_over", line, {"m": "total", "s": "game", "side": "over", "line": line})
    for line in (3.5, 2.5, 1.5):
        add("g_u%s" % line, "{T} matches: under %s goals" % line, "g_under", -line, {"m": "total", "s": "game", "side": "under", "line": line})
    add("h1t_o", "{T} matches: goal in the 1st half", "h1t_over", 0.5, {"m": "total", "s": "h1", "side": "over", "line": 0.5})
    add("h1t_o15", "{T} matches: 2+ goals in the 1st half", "h1t_over", 1.5, {"m": "total", "s": "h1", "side": "over", "line": 1.5})
    add("btts", "{T} matches: both teams score", "btts", 0, {"m": "btts", "s": "game", "side": "yes"})
    add("btts_no", "{T} matches: not both teams score", "btts_no", 0, {"m": "btts", "s": "game", "side": "no"})
    add("ou_o", "{T} matches go over the posted total", "ou_o", pred=over_posted)
    add("ou_u", "{T} matches go under the posted total", "ou_u", pred=lambda o: None if over_posted(o) is None else not over_posted(o))
    for line in (3.5, 4.5, 5.5, 6.5):
        add("c_o%s" % line, "{T} wins %d+ corners" % (line + 0.5), "c_over", line, {"m": "count", "k": "corners", "s": "game", "t": "home", "side": "over", "line": line})
        add("ca_o%s" % line, "{T} opponents win %d+ corners" % (line + 0.5), "ca_over", line, {"m": "count", "k": "corners", "s": "game", "t": "away", "side": "over", "line": line})
    for line in (7.5, 8.5, 9.5, 10.5, 11.5):
        add("ct_o%s" % line, "{T} matches: %d+ corners" % (line + 0.5), "ct_over", line, {"m": "count", "k": "corners", "s": "game", "t": "both", "side": "over", "line": line})
    for line in (10.5, 9.5, 8.5):
        add("ct_u%s" % line, "{T} matches: under %s corners" % line, "ct_under", -line, {"m": "count", "k": "corners", "s": "game", "t": "both", "side": "under", "line": line})
    for line in (1.5, 2.5):
        add("k_o%s" % line, "{T} gets %d+ cards" % (line + 0.5), "k_over", line, {"m": "count", "k": "cards", "s": "game", "t": "home", "side": "over", "line": line})
    for line in (3.5, 4.5, 5.5):
        add("kt_o%s" % line, "{T} matches: %d+ cards" % (line + 0.5), "kt_over", line, {"m": "count", "k": "cards", "s": "game", "t": "both", "side": "over", "line": line})
    return P


TEAM_PROPS = _team_props()


def export_players(by_team, team):
    out = []
    for pid, rows in by_team.get(team, {}).items():
        if len(rows) < 2:
            continue
        out.append({"id": pid, "name": rows[0]["name"], "pos": rows[0]["pos"],
                    "rows": [[r.get(k, 0) for k in RESULT_KEYS] for r in rows]})
    order = {"F": 0, "M": 1, "D": 2, "G": 3}
    out.sort(key=lambda p: (order.get(p["pos"], 4), -sum(r[2] for r in p["rows"]) / len(p["rows"])))
    return out


def key_players(by_team, team):
    ps = []
    for pid, rows in by_team.get(team, {}).items():
        gp = float(len(rows))
        p = {"id": pid, "name": rows[0]["name"], "pos": rows[0]["pos"], "headshot": rows[0]["headshot"], "gp": len(rows),
             "goals": sum(r["g"] for r in rows), "assists": sum(r["a"] for r in rows)}
        for k in ("shots", "sot", "saves"):
            p[k + "Pg"] = round(sum(r[k] for r in rows) / gp, 1)
        ps.append(p)
    gk = sorted([p for p in ps if p["pos"] == "G"], key=lambda p: -p["gp"])[:1]
    out = sorted([p for p in ps if p["pos"] != "G"], key=lambda p: (-(p["goals"] + 0.5 * p["assists"]), -p["shotsPg"]))[:7]
    return out + gk


# ---------- day build ----------

def build_day(day_str, today_str, cfg, lg_cfg, src, others=None, summary=None):
    """others: [(league cfg, [finished matches this season])] from the other
    configured competitions, for the "all competitions" trends."""
    board = src.day(day_str)
    slate = board["games"]
    if not slate:
        return None
    legs = cfg["legs"]
    w = cfg["recent_weight"]
    games, season = collect_games(src, day_str, cfg["last_season_games"])
    year = season.get("year") or slate[0]["season"]
    lines = {}
    for g in games:  # posted totals for this season's matches (over-the-line trends)
        if g["season"] == year and not g.get("lines"):
            s = src.summary(g["id"])
            if s and s.get("lines"):
                lines[g["id"]] = s["lines"]
    logs = team_logs(games, lines)
    teams = sorted(logs)
    n_teams = max(len({t for g in games if g["season"] == year for t in (g["home"]["abbr"], g["away"]["abbr"])}), 2)

    profiles = {}
    for team in teams:
        log = logs[team]
        sample, n_cur, n_prior = stats.build_sample(log, year, cfg["min_sample_games"])
        recent = sample[: cfg["recent_games"]]
        agg_s, agg_r = aggregate(sample, legs), aggregate(recent, legs)
        prof = {"team": team, "sample": agg_s, "recent": agg_r, "nCurrent": n_cur, "nPrior": n_prior, "nTeams": n_teams,
                "_sampleGames": sample, "_recentGames": recent, "_log": log}
        for k in ("gfPg", "gaPg", "gdPg", "scored1pPct", "conceded1pPct", "regTotalPg", "ppg", "sotFPg", "sotAPg"):
            prof[k] = stats.blend(agg_s.get(k), agg_r.get(k), w)
        profiles[team] = prof

    def rank(get, reverse):
        order = sorted(teams, key=lambda t: get(t) if get(t) is not None else (-1e9 if reverse else 1e9), reverse=reverse)
        return {t: i + 1 for i, t in enumerate(order)}
    gf, ga = rank(lambda t: profiles[t]["gfPg"], True), rank(lambda t: profiles[t]["gaPg"], False)
    for t in teams:
        profiles[t]["gfRank"], profiles[t]["gaRank"] = gf[t], ga[t]
    ranks = {"gaR": rank(lambda t: profiles[t]["sample"].get("gaPg"), True),
             "sotA": rank(lambda t: profiles[t]["sample"].get("sotAPg"), True)}

    logs_cur = {t: [r for r in logs[t] if r["season"] == year] for t in teams}
    summary = summary or (lambda gid, lg: src.summary(gid))
    by_team, flat = player_rows(summary, logs_cur)
    league_out = trends.league_outcomes(logs_cur, extras=("totalLine", "st"))
    base_cache, dists = {}, {}
    tcfg = cfg["trends"]

    # The same teams' matches in the other configured competitions (matched by ESPN team id).
    logs_all, by_team_all = logs_cur, by_team
    if others:
        key_of = {r["tid"]: t for t in logs_cur for r in logs_cur[t][:1]}
        for g in slate:
            for side in ("home", "away"):
                key_of.setdefault(g[side]["id"], g[side]["abbr"])
        extra = defaultdict(list)
        for olg, ogames in others:
            mine = [g for g in ogames if g["date"] < day_str and (g["home"]["id"] in key_of or g["away"]["id"] in key_of)]
            for recs in team_logs(mine, {}, lg=olg["key"], tag=olg.get("short") or olg["key"]).values():
                for r in recs:
                    if r["tid"] in key_of:
                        extra[key_of[r["tid"]]].append(r)
        if extra:
            logs_all = {t: sorted(logs_cur.get(t, []) + extra.get(t, []), key=lambda r: (r["date"], r["start"]), reverse=True)
                        for t in set(logs_cur) | set(extra)}
            by_team_all, _ = player_rows(summary, logs_all)

    def ctx(stat, opp):
        p = profiles[opp]["sample"]
        if stat in ("g", "a"):
            return "%s concedes %.2f goals/gm (#%d most)" % (opp, p.get("gaPg") or 0, ranks["gaR"][opp])
        if stat in ("shots", "sot", "saves"):
            return "%s allows %.1f shots on target/gm (#%d most)" % (opp, p.get("sotAPg") or 0, ranks["sotA"][opp])
        return None

    out_games = []
    for g in slate:
        home, away = g["home"]["abbr"], g["away"]["abbr"]
        for t in (home, away):  # a team with no history yet gets an empty profile
            if t not in profiles:
                profiles[t] = {"team": t, "sample": {"gp": 0}, "recent": {"gp": 0}, "nCurrent": 0, "nPrior": 0, "nTeams": n_teams,
                               "_sampleGames": [], "_recentGames": [], "_log": [], "gfPg": None, "gaPg": None, "gdPg": 0,
                               "scored1pPct": None, "conceded1pPct": None, "regTotalPg": None, "ppg": None,
                               "sotFPg": None, "sotAPg": None, "gfRank": n_teams, "gaRank": n_teams}
        ln = g.get("lines")
        if (not ln or ln.get("homeML") is None) and g["final"]:
            ln = (src.summary(g["id"]) or {}).get("lines") or ln
        ln = ln or {}
        side = {}
        for abbrev, slot, is_home in ((home, "home", True), (away, "away", False)):
            prof = dict(profiles[abbrev])
            prof["isHome"] = is_home
            prof["odds"] = _dec(ln.get(slot + "ML"))
            # cups only see their own matches, not the league games in between, so rest is unknown
            prof.update({"daysOff": None, "gamesLast7": None} if lg_cfg.get("cup") else _rest(profiles[abbrev]["_log"], day_str))
            side[abbrev] = prof
        s_home, d_home = scoring.score_side(side[home], side[away], cfg["conditions"], soccer_scoring.CONDITIONS)
        s_away, d_away = scoring.score_side(side[away], side[home], cfg["conditions"], soccer_scoring.CONDITIONS)
        fav, dog = (home, away) if s_home >= s_away else (away, home)
        score, details = (s_home, d_home) if fav == home else (s_away, d_away)

        game = {
            "id": g["id"], "startTimeUTC": g["start"], "venue": g.get("venue"), "home": home, "away": away,
            "lines": {"total": ln.get("total"), "homeML": ln.get("homeML"), "awayML": ln.get("awayML"), "drawML": ln.get("drawML"),
                      "draw": _dec(ln.get("drawML"))},
            "stage": g.get("stage"),
            "stomp": {"score": score, "fav": fav, "dog": dog, "conditions": details, "flag": None,
                      "otherSideScore": s_away if fav == home else s_home},
            "teams": {}, "h2h": [], "result": None,
        }
        if g["state"] == "in":
            game["state"] = "LIVE"
        for abbrev, slot in ((home, "home"), (away, "away")):
            p = side[abbrev]
            info = g[slot]
            logo = info.get("logo") or "https://a.espncdn.com/i/teamlogos/soccer/500/%s.png" % info.get("id")
            is_fav = abbrev == fav
            game["teams"][abbrev] = {
                "abbrev": abbrev, "name": info.get("name") or abbrev, "short": info.get("short") or abbrev,
                "logo": logo, "darkLogo": logo, "odds": p["odds"],
                "record": info.get("record") or _record(p["_log"], year), "streak": _form(p["_log"], year),
                "nCurrent": p["nCurrent"], "nPrior": p["nPrior"], "gfRank": p["gfRank"], "gaRank": p["gaRank"],
                "blended": {k: p[k] for k in ("gfPg", "gaPg", "gdPg", "scored1pPct", "conceded1pPct", "regTotalPg")},
                "sample": p["sample"], "recent": p["recent"],
                "legs": {"role": "favorite" if is_fav else "underdog",
                         "sample": leg_rates(p["_sampleGames"], legs, is_fav),
                         "recent": leg_rates(p["_recentGames"], legs, is_fav)},
                "lastGames": [_game_row(r) for r in p["_log"][:10]],
                "log": [{"h": r["home"], "gf": r["gf"], "ga": r["ga"], "p": r["periods"], "e": r["end"],
                         "ls": 1 if r["season"] < year else 0}
                        for r in ([x for x in p["_log"] if x["season"] == year] +
                                  [x for x in p["_log"] if x["season"] < year][:cfg["last_season_games"]])],
                "keyPlayers": key_players(by_team, abbrev),
                "players": export_players(by_team, abbrev),
                "goalies": [], "skaters": [],
            }
            if not lg_cfg.get("cup"):
                game["teams"][abbrev].update({"daysOff": p["daysOff"], "gamesLast7": p["gamesLast7"]})
        game["h2h"] = [_game_row(r) for r in profiles[home]["_log"] if r["opp"] == away][:5]

        items = []
        for team, opp, slot in ((home, away, "home"), (away, home, "away")):
            if not logs_cur.get(team):
                continue
            items += trends.team_trends(team, opp, slot, logs_cur, league_out, TEAM_PROPS, tcfg,
                                        extras=("totalLine", "st"), base_cache=base_cache, dists=dists)
            items += trends.player_trends(team, opp, by_team.get(team, {}), flat, PLAYER_PROPS, tcfg, ctx_fn=ctx, dists=dists,
                                          info=lambda pid, team=team: {"headshot": by_team[team][pid][0]["headshot"]})
        for t in items:
            if t["kind"] == "player":
                t["label"] = _prop_label(t["name"], t["prop"])
        game["trends"] = trends.finalize(items, tcfg)
        if logs_all is not logs_cur and any(len(logs_all.get(t, [])) > len(logs_cur.get(t, [])) for t in (home, away)):
            items = []
            for team, opp, slot in ((home, away, "home"), (away, home, "away")):
                if not logs_all.get(team):
                    continue
                items += trends.team_trends(team, opp, slot, logs_all, league_out, TEAM_PROPS, tcfg,
                                            extras=("totalLine", "st"), base_cache=base_cache, dists=dists)
                items += trends.player_trends(team, opp, by_team_all.get(team, {}), flat, PLAYER_PROPS, tcfg, ctx_fn=ctx,
                                              dists=dists, info=lambda pid, team=team: {"headshot": by_team_all[team][pid][0]["headshot"]})
            for t in items:
                if t["kind"] == "player":
                    t["label"] = _prop_label(t["name"], t["prop"])
            game["trendsAll"] = trends.finalize(items, tcfg)

        if g["final"]:
            hs, as_ = g["home"]["score"], g["away"]["score"]
            log = team_logs([g], {})[home][0]
            fs, ds = (hs, as_) if fav == home else (as_, hs)
            fh = log["periods"][0][0] if fav == home else log["periods"][0][1]
            ml, over, h1 = fs > ds, hs + as_ > legs["total_line"], fh > legs["first_half_team_line"]
            s = src.summary(g["id"])
            game["result"] = {
                "homeScore": hs, "awayScore": as_, "end": log["end"], "periods": log["periods"], "regTotal": hs + as_,
                "legs": {"ml": ml, "over": over, "firstPeriod": h1, "all3": ml and over and h1},
                "stats": log["st"],  # home perspective: F = home, A = away
                "box": {p["id"]: {k: p[k] for k in RESULT_KEYS} for p in (s or {}).get("players", [])} or None,
            }
        out_games.append(game)

    scoring.assign_flags(out_games, cfg)
    out_games.sort(key=lambda x: (x["stomp"]["flag"] is None, -x["stomp"]["score"], x["startTimeUTC"] or ""))
    sample_logs = [r for t in teams for r in profiles[t]["_sampleGames"]]
    league = [_avg(r["periods"][i][0] for r in sample_logs) or 0.65 for i in range(2)]
    key = lg_cfg["key"]
    return {
        "date": day_str, "sport": "soccer/" + key, "league": key, "leagueName": lg_cfg["name"],
        "leagueLogo": board["league"].get("logo"), "season": year,
        "generatedAt": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "settings": {
            "sport": "soccer", "recentGames": cfg["recent_games"], "minSampleGames": cfg["min_sample_games"], "recentWeight": w,
            "legs": legs, "tiers": cfg["flag_tiers"], "simulation": cfg.get("simulation", {}),
            "builder": cfg.get("builder", {}), "trends": tcfg, "leaguePeriodGf": league,
            "propStats": BUILDER_PROPS, "periods": 2, "scopes": SCOPES,
            "markets": ["win", "total", "team", "spread", "btts"],
            "lineDefaults": {"total": 2.5, "totalScope": "game", "team": 0.5, "teamScope": "h1", "spread": -1.5},
        },
        "trendDist": dists,
        "games": out_games,
    }


def _prop_label(name, prop):
    stat_label = next((p["label"] for p in PLAYER_PROPS if p["stat"] == prop["stat"]), prop["stat"])
    line = prop["line"]
    if prop["side"] == "over" and line % 1 == 0.5:
        if prop["stat"] == "g" and line == 0.5:
            return "%s scores anytime" % name
        return "%s %d+ %s" % (name, int(line + 0.5), stat_label)
    return "%s %s %s %s" % (name, prop["side"], line, stat_label)


def _record(log, year):
    cur = [r for r in log if r["season"] == year]
    w = sum(1 for r in cur if r["win"])
    d = sum(1 for r in cur if r["tie"])
    return "%d-%d-%d" % (w, d, len(cur) - w - d)


def _form(log, year):
    cur = [r for r in log if r["season"] == year][:5]
    return "".join("W" if r["win"] else "D" if r["tie"] else "L" for r in cur)


def _game_row(r):
    return {"date": r["date"], "opp": r["opp"], "home": r["home"], "gf": r["gf"], "ga": r["ga"], "end": r["end"],
            "win": r["win"], "periods": r["periods"], "regTotal": r["gf"] + r["ga"],
            "season": r["season"], "totalLine": r["totalLine"]}


def write_index(league_dir):
    days = sorted(f[:-5] for f in os.listdir(league_dir) if f.endswith(".json") and f != "index.json")
    out = []
    for d in days:
        with open(os.path.join(league_dir, d + ".json"), encoding="utf-8") as f:
            data = json.load(f)
        ff = [g["result"]["legs"] for g in data["games"] if g["stomp"]["flag"] and g.get("result")]
        out.append({"date": d, "games": len(data["games"]), "flagged": sum(1 for g in data["games"] if g["stomp"]["flag"]),
                    "flaggedFinal": len(ff), "hits": {k: sum(1 for l in ff if l[k]) for k in ("ml", "over", "firstPeriod", "all3")}})
    with open(os.path.join(league_dir, "index.json"), "w", encoding="utf-8") as f:
        json.dump({"days": out, "updatedAt": datetime.utcnow().replace(microsecond=0).isoformat() + "Z"}, f)
    return out


def write_leagues(cfg, today_str):
    """The league picker: each league's next match day and flag count."""
    out = []
    for lg in cfg["leagues"]:
        idx = os.path.join(OUT_DIR, lg["key"], "index.json")
        if not os.path.exists(idx):
            continue
        with open(idx, encoding="utf-8") as f:
            days = json.load(f)["days"]
        nxt = next((d for d in days if d["date"] >= today_str), None)
        logo = None
        if days:
            with open(os.path.join(OUT_DIR, lg["key"], days[-1]["date"] + ".json"), encoding="utf-8") as f:
                logo = json.load(f).get("leagueLogo")
        out.append({"key": lg["key"], "name": lg["name"], "country": lg.get("country"), "logo": logo,
                    "next": nxt["date"] if nxt else None, "games": nxt["games"] if nxt else 0,
                    "flagged": nxt["flagged"] if nxt else 0})
    with open(os.path.join(OUT_DIR, "leagues.json"), "w", encoding="utf-8") as f:
        json.dump({"leagues": out, "updatedAt": datetime.utcnow().replace(microsecond=0).isoformat() + "Z"}, f)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--league", help="league key, e.g. eng.1 (default: all in config.yaml)")
    ap.add_argument("--date", help="YYYY-MM-DD (default: recent and upcoming match days)")
    ap.add_argument("--days-back", type=int, default=3, help="how many past days to rebuild")
    args = ap.parse_args()

    cfg = load_config()
    today = date.today()
    today_str = today.isoformat()
    leagues = [lg for lg in cfg["leagues"] if not args.league or lg["key"] == args.league]
    sources = {lg["key"]: Source(lg["key"], today_str) for lg in cfg["leagues"]}
    season_cache = {}

    def season_games(lg):
        """Finished matches of `lg` this season (for other leagues' all-competition trends)."""
        if lg["key"] not in season_cache:
            s = sources[lg["key"]]
            cal, _ = s.calendar(today_str)
            season_cache[lg["key"]] = [g for d in cal if d <= today_str for g in s.day(d)["games"] if g["final"]]
        return season_cache[lg["key"]]

    def summary(gid, lg):
        return sources[lg].summary(gid) if lg in sources else None

    for lg in leagues:
        src = sources[lg["key"]]
        others = [(o, season_games(o)) for o in cfg["leagues"] if o["key"] != lg["key"]]
        if args.date:
            days = [args.date]
        else:
            lo = (today - timedelta(days=args.days_back)).isoformat()
            hi = (today + timedelta(days=cfg.get("days_ahead", 7))).isoformat()
            cal, _ = src.calendar(today_str)
            days = [d for d in cal if lo <= d <= hi]
        league_dir = os.path.join(OUT_DIR, lg["key"])
        os.makedirs(league_dir, exist_ok=True)
        print("%s: %d match days to build" % (lg["name"], len(days)))
        for d in days:
            data = build_day(d, today_str, cfg, lg, src, others,
                             summary=lambda gid, l, k=lg["key"]: summary(gid, l or k))
            if data is None:
                continue
            with open(os.path.join(league_dir, d + ".json"), "w", encoding="utf-8") as f:
                json.dump(data, f, separators=(",", ":"))
            flagged = [g for g in data["games"] if g["stomp"]["flag"]]
            print("  %s: %d matches, %d flagged: %s" % (d, len(data["games"]), len(flagged), ", ".join(
                "%s vs %s (%d)" % (g["stomp"]["fav"], g["stomp"]["dog"], g["stomp"]["score"]) for g in flagged)))
        write_index(league_dir)
    write_leagues(cfg, today_str)


if __name__ == "__main__":
    main()
