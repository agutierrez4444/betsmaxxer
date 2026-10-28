"""Build the NBA dashboard data, one file per day (like the NHL).

    py -m pipeline.nba_build                 # yesterday (results), today, tomorrow
    py -m pipeline.nba_build --days-back 5   # also rebuild 5 earlier days
    py -m pipeline.nba_build --date 2026-11-02

Writes site/data/nba/<date>.json and site/data/nba/index.json.
Stats for a day only use games played before that day.
"""
import argparse
import json
import math
import os
from collections import defaultdict
from datetime import date, datetime, timedelta

import yaml

from . import nba_api, nba_scoring, scoring, stats, trends
from .build import _rest
from .nhl_api import ROOT

OUT_DIR = os.path.join(ROOT, "site", "data", "nba")
SCOPES = [
    {"key": "game", "label": "Full game (incl. OT)"}, {"key": "reg", "label": "Regulation"},
    {"key": "h1", "label": "1st half"}, {"key": "h2", "label": "2nd half"},
    {"key": "p1", "label": "1st quarter"}, {"key": "p2", "label": "2nd quarter"},
    {"key": "p3", "label": "3rd quarter"}, {"key": "p4", "label": "4th quarter"},
]
OUT_STATUSES = ("Out", "Doubtful")


def load_config():
    with open(os.path.join(ROOT, "config.yaml"), encoding="utf-8") as f:
        return yaml.safe_load(f)["nba"]


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


# ---------- games ----------

class Source(object):
    """Scoreboards and summaries, memoized for the whole run."""

    def __init__(self, today_str):
        self.today = today_str
        self.days = {}
        self.sums = {}

    def day(self, d):
        if d not in self.days:
            try:
                self.days[d] = nba_api.scoreboard(d, self.today)
            except RuntimeError as e:
                print("  ! scoreboard %s: %s" % (d, e))
                self.days[d] = []
        return self.days[d]

    def summary(self, gid):
        if gid not in self.sums:
            try:
                self.sums[gid] = nba_api.summary(gid)
            except RuntimeError as e:
                print("  ! summary %s: %s" % (gid, e))
                self.sums[gid] = None
        return self.sums[gid]


def collect_games(src, day_str, season, prior_per_team, max_days=420):
    """Regular-season finals before `day_str`: all of this season, plus last
    season's latest `prior_per_team` games for every team."""
    day = stats.parse_date(day_str)
    out, prior = [], defaultdict(int)
    for i in range(1, max_days):
        d = (day - timedelta(days=i)).isoformat()
        for g in src.day(d):
            if g["type"] != 2 or not g["final"] or not g["home"] or not g["away"]:
                continue
            if g["season"] == season:
                out.append(g)
            elif g["season"] == season - 1:
                if prior[g["home"]["abbr"]] >= prior_per_team and prior[g["away"]["abbr"]] >= prior_per_team:
                    continue
                prior[g["home"]["abbr"]] += 1
                prior[g["away"]["abbr"]] += 1
                out.append(g)
        if len(prior) >= 30 and min(prior.values()) >= prior_per_team:
            break
    return out


def team_logs(games, lines):
    """{team: [records newest first]} in the shape the shared code expects."""
    logs = defaultdict(list)
    for g in games:
        ln = g.get("lines") or lines.get(g["id"]) or {}
        for side, other in (("home", "away"), ("away", "home")):
            me, op = g[side], g[other]
            qs = [[me["periods"][i] if i < len(me["periods"]) else 0, op["periods"][i] if i < len(op["periods"]) else 0] for i in range(4)]
            n_ot = max(0, len(me["periods"]) - 4)
            spread_home = ln.get("spread")
            logs[me["abbr"]].append({
                "gameId": g["id"], "date": g["date"], "start": g.get("start") or "", "season": g["season"],
                "opp": op["abbr"], "home": side == "home",
                "gf": me["score"], "ga": op["score"], "win": me["score"] > op["score"], "tie": False,
                "end": "REG" if not n_ot else ("OT" if n_ot == 1 else "%dOT" % n_ot),
                "periods": qs, "regGf": sum(q[0] for q in qs), "regGa": sum(q[1] for q in qs),
                "spread": None if spread_home is None else (spread_home if side == "home" else -spread_home),
                "totalLine": ln.get("total"),
                "fgm": me.get("fgm"), "fga": me.get("fga"), "fg3m": me.get("fg3m"), "fg3a": me.get("fg3a"),
                "reb": me.get("reb"), "ast": me.get("ast"), "fta": me.get("fta"),
                "fg3mA": op.get("fg3m"), "rebA": op.get("reb"), "astA": op.get("ast"),
                "scoredFirst": None, "scorers": [],
            })
    for t in logs:
        logs[t].sort(key=lambda r: (r["date"], r["start"]), reverse=True)
    return logs


def _h1(r):
    return sum(q[0] for q in r["periods"][:2]), sum(q[1] for q in r["periods"][:2])


def aggregate(games, legs):
    if not games:
        return {"gp": 0}
    fb = legs["total_line_fallback"]
    out = {
        "gp": len(games),
        "gfPg": _avg(r["gf"] for r in games), "gaPg": _avg(r["ga"] for r in games),
        "regTotalPg": _avg(r["gf"] + r["ga"] for r in games),
        "winPct": _pct(r["win"] for r in games),
        "atsPct": _pct(None if r["spread"] is None or r["gf"] - r["ga"] + r["spread"] == 0 else r["gf"] - r["ga"] + r["spread"] > 0 for r in games),
        "overPct": _pct(None if r["totalLine"] is None or r["gf"] + r["ga"] == r["totalLine"] else r["gf"] + r["ga"] > r["totalLine"] for r in games),
        "h1WinPct": _pct(_h1(r)[0] > _h1(r)[1] for r in games),
        "q1WinPct": _pct(r["periods"][0][0] > r["periods"][0][1] for r in games),
        "fgPct": _pct_of(games, "fgm", "fga"), "fg3Pct": _pct_of(games, "fg3m", "fg3a"),
        "fg3mPg": _avg(r["fg3m"] for r in games), "fg3mAPg": _avg(r["fg3mA"] for r in games),
        "rebPg": _avg(r["reb"] for r in games), "rebAPg": _avg(r["rebA"] for r in games),
        "astPg": _avg(r["ast"] for r in games), "astAPg": _avg(r["astA"] for r in games),
        "byPeriod": [{"gf": _avg(r["periods"][i][0] for r in games), "ga": _avg(r["periods"][i][1] for r in games)} for i in range(4)],
        "overFallbackPct": _pct(r["gf"] + r["ga"] > fb for r in games),
    }
    out["gdPg"] = round(out["gfPg"] - out["gaPg"], 3)
    # the legs panel / blended fields share names with the other sports
    out["scored1pPct"] = out["h1WinPct"]
    out["conceded1pPct"] = _pct(_h1(r)[1] > _h1(r)[0] for r in games)
    home = [r for r in games if r["home"]]
    away = [r for r in games if not r["home"]]
    out["home"] = {"gp": len(home), "gfPg": _avg(r["gf"] for r in home), "gaPg": _avg(r["ga"] for r in home)}
    out["away"] = {"gp": len(away), "gfPg": _avg(r["gf"] for r in away), "gaPg": _avg(r["ga"] for r in away)}
    return out


def _pct_of(games, made, att):
    m = sum(r[made] or 0 for r in games)
    a = sum(r[att] or 0 for r in games)
    return round(m / float(a), 3) if a else None


def leg_rates(games, legs, as_favorite):
    """Default combo: moneyline + over the posted total + wins the 1st half."""
    rows = []
    for r in games:
        ml = r["win"] if as_favorite else not r["win"]
        over = r["gf"] + r["ga"] > (r["totalLine"] or legs["total_line_fallback"])
        hf, ha = _h1(r)
        h1 = hf > ha if as_favorite else ha > hf
        rows.append((ml, over, h1, ml and over and h1))
    if not rows:
        return None
    return {"gp": len(rows), "ml": _pct(x[0] for x in rows), "over": _pct(x[1] for x in rows),
            "firstPeriod": _pct(x[2] for x in rows), "all3": _pct(x[3] for x in rows)}


# ---------- players ----------

def player_rows(src, logs_cur):
    """{team: {pid: [rows newest first]}}, flat list, and final box scores by game."""
    side_of = {(r["gameId"], t): r for t in logs_cur for r in logs_cur[t]}
    by_team = defaultdict(lambda: defaultdict(list))
    flat, boxes = [], {}
    for gid in sorted({r["gameId"] for t in logs_cur for r in logs_cur[t]}):
        s = src.summary(gid)
        if not s:
            continue
        boxes[gid] = s
        for p in s["players"]:
            log = side_of.get((gid, p["team"]))
            if not log:
                continue
            row = dict(p)
            row.update({"date": log["date"], "opp": log["opp"], "home": log["home"]})
            for k, parts in COMBOS.items():
                row[k] = sum(p[x] for x in parts)
            by_team[p["team"]][p["id"]].append(row)
            flat.append(row)
    for t in by_team:
        for pid in by_team[t]:
            by_team[t][pid].sort(key=lambda r: r["date"], reverse=True)
    return by_team, flat, boxes


COMBOS = {"pra": ("pts", "reb", "ast"), "pr": ("pts", "reb"), "pa": ("pts", "ast"), "ra": ("reb", "ast"), "stocks": ("stl", "blk")}


def _played(r):
    return r["min"] >= 20  # a real rotation role that night


def _v(k):
    return lambda r: r.get(k, 0)


ALL = {"G", "F", "C"}
PLAYER_PROPS = [
    {"stat": "pts", "label": "points", "lines": [9.5, 14.5, 19.5, 24.5, 29.5, 34.5], "sides": ["over", "under"], "pos": ALL, "usage": _played, "value": _v("pts")},
    {"stat": "reb", "label": "rebounds", "lines": [3.5, 5.5, 7.5, 9.5, 11.5], "sides": ["over", "under"], "pos": ALL, "usage": _played, "value": _v("reb")},
    {"stat": "ast", "label": "assists", "lines": [2.5, 4.5, 6.5, 8.5], "sides": ["over", "under"], "pos": ALL, "usage": _played, "value": _v("ast")},
    {"stat": "fg3m", "label": "threes made", "lines": [0.5, 1.5, 2.5, 3.5, 4.5], "sides": ["over", "under"], "pos": ALL, "usage": _played, "value": _v("fg3m")},
    {"stat": "pra", "label": "pts + reb + ast", "lines": [19.5, 24.5, 29.5, 34.5, 39.5, 44.5], "sides": ["over", "under"], "pos": ALL, "usage": _played, "value": _v("pra")},
    {"stat": "pr", "label": "pts + reb", "lines": [14.5, 19.5, 24.5, 29.5, 34.5], "sides": ["over"], "pos": ALL, "usage": _played, "value": _v("pr")},
    {"stat": "pa", "label": "pts + ast", "lines": [14.5, 19.5, 24.5, 29.5, 34.5], "sides": ["over"], "pos": ALL, "usage": _played, "value": _v("pa")},
    {"stat": "ra", "label": "reb + ast", "lines": [6.5, 9.5, 12.5, 15.5], "sides": ["over"], "pos": ALL, "usage": _played, "value": _v("ra")},
    {"stat": "stocks", "label": "steals + blocks", "lines": [0.5, 1.5, 2.5, 3.5], "sides": ["over"], "pos": ALL, "usage": _played, "value": _v("stocks")},
]

BUILDER_PROPS = [
    {"key": "pts", "label": "Points", "pos": "GFC"},
    {"key": "reb", "label": "Rebounds", "pos": "GFC"},
    {"key": "ast", "label": "Assists", "pos": "GFC"},
    {"key": "fg3m", "label": "Threes made", "pos": "GFC"},
    {"key": "pra", "label": "Pts + reb + ast", "pos": "GFC"},
    {"key": "pr", "label": "Pts + reb", "pos": "GFC"},
    {"key": "pa", "label": "Pts + ast", "pos": "GFC"},
    {"key": "ra", "label": "Reb + ast", "pos": "GFC"},
    {"key": "stl", "label": "Steals", "pos": "GFC"},
    {"key": "blk", "label": "Blocks", "pos": "GFC"},
    {"key": "stocks", "label": "Steals + blocks", "pos": "GFC"},
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

    def ats(o):
        if o.get("spread") is None:
            return None
        m = o["fh"] - o["fa"] + o["spread"]
        return None if m == 0 else m > 0

    def over_posted(o):
        if o.get("totalLine") is None or o["fh"] + o["fa"] == o["totalLine"]:
            return None
        return o["fh"] + o["fa"] > o["totalLine"]

    add("ml", "{T} wins", "win", 0, {"m": "win", "s": "game", "t": "home"})
    add("ats", "{T} covers the spread", "ats", pred=ats)
    add("ats_no", "{T} fails to cover the spread", "ats_no", pred=lambda o: None if ats(o) is None else not ats(o))
    add("ou_o", "{T} games go over the posted total", "ou_o", pred=over_posted)
    add("ou_u", "{T} games go under the posted total", "ou_u", pred=lambda o: None if over_posted(o) is None else not over_posted(o))
    for line in (-5.5, -9.5, -14.5):
        add("by%s" % -line, "{T} wins by %d+" % (-line + 0.5), "winby", -line, {"m": "spread", "s": "game", "t": "home", "line": line})
    add("q1w", "{T} wins the 1st quarter", "q1win", 0, {"m": "win", "s": "p1", "t": "home"})
    add("h1w", "{T} leads at halftime", "h1win", 0, {"m": "win", "s": "h1", "t": "home"})
    add("h2w", "{T} wins the 2nd half", "h2win", 0, {"m": "win", "s": "h2", "t": "home"})
    for line in (104.5, 109.5, 114.5, 119.5, 124.5):
        add("tt_o%s" % line, "{T} scores %d+ points" % (line + 0.5), "tt_over", line, {"m": "team", "s": "game", "t": "home", "side": "over", "line": line})
        add("ta_o%s" % line, "{T} allows %d+ points" % (line + 0.5), "ta_over", line, {"m": "team", "s": "game", "t": "away", "side": "over", "line": line})
    for line in (114.5, 109.5, 104.5):
        add("tt_u%s" % line, "{T} scores under %s points" % line, "tt_under", -line, {"m": "team", "s": "game", "t": "home", "side": "under", "line": line})
        add("ta_u%s" % line, "{T} allows under %s points" % line, "ta_under", -line, {"m": "team", "s": "game", "t": "away", "side": "under", "line": line})
    for line in (52.5, 56.5, 60.5):
        add("h1_o%s" % line, "{T} scores %d+ in the 1st half" % (line + 0.5), "h1_over", line, {"m": "team", "s": "h1", "t": "home", "side": "over", "line": line})
    for line in (25.5, 28.5, 31.5):
        add("q1_o%s" % line, "{T} scores %d+ in the 1st quarter" % (line + 0.5), "q1_over", line, {"m": "team", "s": "p1", "t": "home", "side": "over", "line": line})
    for line in (215.5, 220.5, 225.5, 230.5, 235.5, 240.5):
        add("g_o%s" % line, "{T} games: over %s points" % line, "g_over", line, {"m": "total", "s": "game", "side": "over", "line": line})
    for line in (230.5, 225.5, 220.5, 215.5):
        add("g_u%s" % line, "{T} games: under %s points" % line, "g_under", -line, {"m": "total", "s": "game", "side": "under", "line": line})
    for line in (110.5, 114.5, 118.5):
        add("h1t_o%s" % line, "{T} games: 1st half over %s" % line, "h1t_over", line, {"m": "total", "s": "h1", "side": "over", "line": line})
    add("ot", "{T} games go to overtime", "ot", pred=lambda o: o["end"] != "REG")
    return P


TEAM_PROPS = _team_props()


def export_players(by_team, team):
    out = []
    for pid, rows in by_team.get(team, {}).items():
        used = [r for r in rows if r["min"] >= 15]
        if len(used) < 2:
            continue
        out.append({"id": pid, "name": rows[0]["name"], "pos": rows[0]["pos"],
                    "rows": [[r.get(k, 0) for k in RESULT_KEYS] for r in used]})
    out.sort(key=lambda p: -sum(r[0] for r in p["rows"]) / len(p["rows"]))
    return out


def key_players(by_team, team, injured):
    """Per-game averages for the team's top scorers this season, with injury status."""
    ps = []
    for pid, rows in by_team.get(team, {}).items():
        gp = float(len(rows))
        p = {"id": pid, "name": rows[0]["name"], "pos": rows[0]["pos"], "headshot": rows[0]["headshot"], "gp": len(rows)}
        for k in ("min", "pts", "reb", "ast", "fg3m", "stl", "blk"):
            p[k + "Pg"] = round(sum(r[k] for r in rows) / gp, 1)
        inj = injured.get(rows[0]["name"])
        if inj:
            p["injury"] = inj["status"]
        ps.append(p)
    ps.sort(key=lambda p: -p["ptsPg"])
    return ps[:8]


# ---------- day build ----------

def build_day(day_str, today_str, cfg, src, injuries):
    slate = [g for g in src.day(day_str) if g["type"] in (2, 3) and g["home"] and g["away"]]
    if not slate:
        return None
    season = slate[0]["season"]
    legs = cfg["legs"]
    w = cfg["recent_weight"]

    games = collect_games(src, day_str, season, cfg["last_season_games"])
    cur_ids = [g["id"] for g in games if g["season"] == season]
    lines = {}
    for gid in cur_ids:  # posted lines for this season's games (for ATS / over trends)
        s = src.summary(gid)
        if s and s.get("lines"):
            lines[gid] = s["lines"]
    logs = team_logs(games, lines)
    teams = sorted(logs)

    profiles = {}
    for team in teams:
        log = logs[team]
        sample, n_cur, n_prior = stats.build_sample(log, season, cfg["min_sample_games"])
        recent = sample[: cfg["recent_games"]]
        agg_s, agg_r = aggregate(sample, legs), aggregate(recent, legs)
        prof = {"team": team, "sample": agg_s, "recent": agg_r, "nCurrent": n_cur, "nPrior": n_prior,
                "_sampleGames": sample, "_recentGames": recent, "_log": log}
        for k in ("gfPg", "gaPg", "gdPg", "scored1pPct", "conceded1pPct", "regTotalPg", "winPct"):
            prof[k] = stats.blend(agg_s.get(k), agg_r.get(k), w)
        profiles[team] = prof

    def rank(get, reverse):
        order = sorted(teams, key=lambda t: get(t) if get(t) is not None else (-1e9 if reverse else 1e9), reverse=reverse)
        return {t: i + 1 for i, t in enumerate(order)}

    def sample(key):
        return lambda t: profiles[t]["sample"].get(key)
    gf_rank, ga_rank = rank(lambda t: profiles[t]["gfPg"], True), rank(lambda t: profiles[t]["gaPg"], False)
    for t in teams:
        profiles[t]["gfRank"], profiles[t]["gaRank"] = gf_rank[t], ga_rank[t]
    ranks = {"gaR": rank(sample("gaPg"), True), "fg3A": rank(sample("fg3mAPg"), True),
             "rebA": rank(sample("rebAPg"), True), "astA": rank(sample("astAPg"), True)}

    # ---- this season's players (box scores) ----
    logs_cur = {t: [r for r in logs[t] if r["season"] == season] for t in teams}
    by_team, flat, _ = player_rows(src, logs_cur)
    league_out = trends.league_outcomes(logs_cur, extras=("spread", "totalLine"))
    base_cache, dists = {}, {}
    tcfg = cfg["trends"]

    def ctx(stat, opp):
        p = profiles[opp]["sample"]
        if stat in ("pts", "pr", "pa", "pra"):
            return "%s allows %.1f pts/gm (#%d most)" % (opp, p.get("gaPg") or 0, ranks["gaR"][opp])
        if stat == "fg3m":
            return "%s allows %.1f threes/gm (#%d most)" % (opp, p.get("fg3mAPg") or 0, ranks["fg3A"][opp])
        if stat in ("reb", "ra"):
            return "%s allows %.1f reb/gm (#%d most)" % (opp, p.get("rebAPg") or 0, ranks["rebA"][opp])
        if stat == "ast":
            return "%s allows %.1f ast/gm (#%d most)" % (opp, p.get("astAPg") or 0, ranks["astA"][opp])
        return None

    live = day_str >= today_str  # the injury report only describes today
    star_cfg = cfg["conditions"].get("dog_star_out", {})
    top_n = star_cfg.get("top_n", 2)

    def stars_out(team):
        if not live:
            return [], {}
        inj = {x["name"]: x for x in injuries.get(team, [])}
        tops = key_players(by_team, team, {})[:top_n]
        return [p["name"] for p in tops if p["gp"] >= 2 and inj.get(p["name"], {}).get("status") in OUT_STATUSES], inj

    out_games = []
    for g in slate:
        home, away = g["home"]["abbr"], g["away"]["abbr"]
        if home not in profiles or away not in profiles:
            print("  ! no history for %s @ %s, skipping" % (away, home))
            continue
        ln = g.get("lines")
        if not ln or ln.get("total") is None:
            s = src.summary(g["id"]) if g["final"] else None
            ln = (s or {}).get("lines") or ln or {}
        side = {}
        for abbrev, slot, is_home in ((home, "home", True), (away, "away", False)):
            prof = dict(profiles[abbrev])
            prof["isHome"] = is_home
            sp = ln.get("spread")
            prof["spread"] = None if sp is None else (sp if is_home else -sp)
            prof.update(_rest(logs[abbrev], day_str))
            prof["starsOut"], prof["_inj"] = stars_out(abbrev)
            side[abbrev] = prof
        s_home, d_home = scoring.score_side(side[home], side[away], cfg["conditions"], nba_scoring.CONDITIONS)
        s_away, d_away = scoring.score_side(side[away], side[home], cfg["conditions"], nba_scoring.CONDITIONS)
        fav, dog = (home, away) if s_home >= s_away else (away, home)
        score, details = (s_home, d_home) if fav == home else (s_away, d_away)

        game = {
            "id": g["id"], "startTimeUTC": g["start"], "venue": g.get("venue"), "home": home, "away": away,
            "lines": {"spread": ln.get("spread"), "total": ln.get("total"), "homeML": ln.get("homeML"), "awayML": ln.get("awayML")},
            "stomp": {"score": score, "fav": fav, "dog": dog, "conditions": details, "flag": None,
                      "otherSideScore": s_away if fav == home else s_home},
            "teams": {}, "h2h": [], "result": None,
        }
        if g["state"] == "in":
            game["state"] = "LIVE"
        for abbrev, slot in ((home, "home"), (away, "away")):
            p = side[abbrev]
            info = g[slot]
            ab = abbrev.lower()
            is_fav = abbrev == fav
            game["teams"][abbrev] = {
                "abbrev": abbrev, "name": info.get("name") or abbrev, "short": info.get("short") or abbrev,
                "logo": "https://a.espncdn.com/i/teamlogos/nba/500/%s.png" % ab,
                "darkLogo": "https://a.espncdn.com/i/teamlogos/nba/500-dark/%s.png" % ab,
                "odds": _dec(ln.get(slot + "ML")), "spread": p["spread"],
                "daysOff": p["daysOff"], "gamesLast7": p["gamesLast7"],
                "record": info.get("record") or _record(p["_log"], season), "streak": _streak(p["_log"], season),
                "nCurrent": p["nCurrent"], "nPrior": p["nPrior"], "gfRank": p["gfRank"], "gaRank": p["gaRank"],
                "blended": {k: p[k] for k in ("gfPg", "gaPg", "gdPg", "scored1pPct", "conceded1pPct", "regTotalPg")},
                "sample": p["sample"], "recent": p["recent"],
                "legs": {"role": "favorite" if is_fav else "underdog",
                         "sample": leg_rates(p["_sampleGames"], legs, is_fav),
                         "recent": leg_rates(p["_recentGames"], legs, is_fav)},
                "lastGames": [_game_row(r) for r in p["_log"][:10]],
                "log": [{"h": r["home"], "gf": r["gf"], "ga": r["ga"], "p": r["periods"], "e": r["end"],
                         "ls": 1 if r["season"] < season else 0}
                        for r in ([x for x in p["_log"] if x["season"] == season] +
                                  [x for x in p["_log"] if x["season"] < season][:cfg["last_season_games"]])],
                "keyPlayers": key_players(by_team, abbrev, p["_inj"]),
                "injuries": injuries.get(abbrev, []) if live else None,
                "players": export_players(by_team, abbrev),
                "goalies": [], "skaters": [],
            }
        game["h2h"] = [_game_row(r) for r in side[home]["_log"] if r["opp"] == away][:5]

        items = []
        for team, opp, slot in ((home, away, "home"), (away, home, "away")):
            items += trends.team_trends(team, opp, slot, logs_cur, league_out, TEAM_PROPS, tcfg,
                                        extras=("spread", "totalLine"), base_cache=base_cache, dists=dists)
            items += trends.player_trends(team, opp, by_team.get(team, {}), flat, PLAYER_PROPS, tcfg, ctx_fn=ctx, dists=dists,
                                          info=lambda pid, team=team: {"headshot": by_team[team][pid][0]["headshot"]})
        for t in items:
            if t["kind"] == "player":
                t["label"] = _prop_label(t["name"], t["prop"])
        game["trends"] = trends.finalize(items, tcfg)

        if g["final"]:
            hs, as_ = g["home"]["score"], g["away"]["score"]
            ph, pa = g["home"]["periods"], g["away"]["periods"]
            periods = [[ph[i], pa[i]] for i in range(4)] if len(ph) >= 4 and len(pa) >= 4 else None
            n_ot = max(0, len(ph) - 4)
            fs, ds = (hs, as_) if fav == home else (as_, hs)
            line = game["lines"]["total"] or legs["total_line_fallback"]
            h1 = None
            if periods:
                h1h, h1a = periods[0][0] + periods[1][0], periods[0][1] + periods[1][1]
                h1 = (h1h > h1a) if fav == home else (h1a > h1h)
            ml, over = fs > ds, hs + as_ > line
            s = src.summary(g["id"])
            game["result"] = {
                "homeScore": hs, "awayScore": as_, "end": "REG" if not n_ot else ("OT" if n_ot == 1 else "%dOT" % n_ot),
                "periods": periods, "regTotal": hs + as_,
                "legs": {"ml": ml, "over": over, "firstPeriod": bool(h1), "all3": bool(ml and over and h1)},
                "box": {p["id"]: dict({k: p[k] for k in ("pts", "reb", "ast", "fg3m", "stl", "blk")},
                                      **{k: sum(p[x] for x in parts) for k, parts in COMBOS.items()})
                        for p in (s or {}).get("players", [])} or None,
            }
        out_games.append(game)

    scoring.assign_flags(out_games, cfg)
    out_games.sort(key=lambda x: (x["stomp"]["flag"] is None, -x["stomp"]["score"], x["startTimeUTC"] or ""))

    sample_logs = [r for t in teams for r in profiles[t]["_sampleGames"]]
    league = [_avg(r["periods"][i][0] for r in sample_logs) for i in range(4)]
    qvals = [r["periods"][i][0] for r in sample_logs for i in range(4)]
    qmean = sum(qvals) / float(len(qvals)) if qvals else 28.0
    qsd = math.sqrt(sum((v - qmean) ** 2 for v in qvals) / max(1, len(qvals) - 1)) if qvals else 6.5
    sim = dict(cfg.get("simulation", {}))
    sim["quarter_sd"] = round(qsd, 2)
    return {
        "date": day_str, "sport": "nba", "season": season,
        "generatedAt": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "settings": {
            "sport": "nba", "recentGames": cfg["recent_games"], "minSampleGames": cfg["min_sample_games"], "recentWeight": w,
            "legs": legs, "tiers": cfg["flag_tiers"], "simulation": sim,
            "builder": cfg.get("builder", {}), "trends": tcfg, "leaguePeriodGf": league,
            "propStats": BUILDER_PROPS, "periods": 4, "scopes": SCOPES,
            "markets": ["win", "total", "team", "spread"],
            "lineDefaults": {"total": legs["total_line_fallback"], "team": 112.5, "teamScope": "game", "spread": -5.5,
                             "periodTotal": 56.5},
        },
        "trendDist": dists,
        "games": out_games,
    }


def _prop_label(name, prop):
    stat_label = next((p["label"] for p in PLAYER_PROPS if p["stat"] == prop["stat"]), prop["stat"])
    line = prop["line"]
    if prop["side"] == "over" and line % 1 == 0.5:
        return "%s %d+ %s" % (name, int(line + 0.5), stat_label)
    return "%s %s %s %s" % (name, prop["side"], line, stat_label)


def _record(log, season):
    cur = [r for r in log if r["season"] == season]
    w = sum(1 for r in cur if r["win"])
    return "%d-%d" % (w, len(cur) - w)


def _streak(log, season):
    cur = [r for r in log if r["season"] == season]
    if not cur:
        return ""
    code = "W" if cur[0]["win"] else "L"
    n = 0
    for r in cur:
        if ("W" if r["win"] else "L") != code:
            break
        n += 1
    return "%s%d" % (code, n)


def _game_row(r):
    return {"date": r["date"], "opp": r["opp"], "home": r["home"], "gf": r["gf"], "ga": r["ga"], "end": r["end"],
            "win": r["win"], "periods": r["periods"], "regTotal": r["gf"] + r["ga"],
            "season": r["season"], "totalLine": r["totalLine"], "spread": r["spread"]}


def write_index():
    days = sorted(f[:-5] for f in os.listdir(OUT_DIR) if f.endswith(".json") and f != "index.json")
    out = []
    for d in days:
        with open(os.path.join(OUT_DIR, d + ".json"), encoding="utf-8") as f:
            data = json.load(f)
        ff = [g["result"]["legs"] for g in data["games"] if g["stomp"]["flag"] and g.get("result")]
        out.append({"date": d, "games": len(data["games"]), "flagged": sum(1 for g in data["games"] if g["stomp"]["flag"]),
                    "flaggedFinal": len(ff), "hits": {k: sum(1 for l in ff if l[k]) for k in ("ml", "over", "firstPeriod", "all3")}})
    with open(os.path.join(OUT_DIR, "index.json"), "w", encoding="utf-8") as f:
        json.dump({"days": out, "updatedAt": datetime.utcnow().replace(microsecond=0).isoformat() + "Z"}, f)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYY-MM-DD (default: yesterday, today and tomorrow)")
    ap.add_argument("--days-back", type=int, default=1, help="how many past days to rebuild")
    args = ap.parse_args()

    cfg = load_config()
    today = date.today()
    today_str = today.isoformat()
    if args.date:
        days = [args.date]
    else:
        days = [(today - timedelta(days=i)).isoformat() for i in range(args.days_back, 0, -1)]
        days += [today_str, (today + timedelta(days=1)).isoformat()]

    os.makedirs(OUT_DIR, exist_ok=True)
    src = Source(today_str)
    print("NBA: loading injury report...")
    injuries = nba_api.injuries()
    for d in days:
        print("Building NBA %s..." % d)
        data = build_day(d, today_str, cfg, src, injuries)
        if data is None:
            print("  no games")
            continue
        with open(os.path.join(OUT_DIR, d + ".json"), "w", encoding="utf-8") as f:
            json.dump(data, f, separators=(",", ":"))
        flagged = [g for g in data["games"] if g["stomp"]["flag"]]
        print("  %d games, %d flagged: %s" % (len(data["games"]), len(flagged), ", ".join(
            "%s vs %s (%d, %s)" % (g["stomp"]["fav"], g["stomp"]["dog"], g["stomp"]["score"], g["stomp"]["flag"]) for g in flagged)))
    write_index()


if __name__ == "__main__":
    main()
