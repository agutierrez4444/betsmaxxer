"""Build the NFL dashboard data, one file per week.

    py -m pipeline.nfl_build                 # last week (results) + this week
    py -m pipeline.nfl_build --weeks-back 4  # also rebuild 4 earlier weeks
    py -m pipeline.nfl_build --week 6

Writes site/data/nfl/<season>-wNN.json and site/data/nfl/index.json.
Stats for a week only use games from earlier weeks.
"""
import argparse
import json
import os
from collections import defaultdict
from datetime import date, datetime, timedelta

import yaml

from . import nfl_api, nfl_scoring, scoring, stats, trends
from .nhl_api import ROOT

OUT_DIR = os.path.join(ROOT, "site", "data", "nfl")
QUARTER = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th"}
SCOPES = [
    {"key": "game", "label": "Full game (incl. OT)"}, {"key": "reg", "label": "Regulation"},
    {"key": "h1", "label": "1st half"}, {"key": "h2", "label": "2nd half"},
    {"key": "p1", "label": "1st quarter"}, {"key": "p2", "label": "2nd quarter"},
    {"key": "p3", "label": "3rd quarter"}, {"key": "p4", "label": "4th quarter"},
]


def load_config():
    with open(os.path.join(ROOT, "config.yaml"), encoding="utf-8") as f:
        return yaml.safe_load(f)["nfl"]


def _f(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _i(x, default=0):
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return default


def _dec(ml):
    ml = _f(ml, None)
    if ml is None or ml == 0:
        return None
    return round(1 + ml / 100.0, 2) if ml > 0 else round(1 + 100.0 / -ml, 2)


def _eastern_to_utc(day, hhmm):
    """nflverse times are US Eastern. DST: 2nd Sunday of March to 1st Sunday of November."""
    y, m, d = [int(x) for x in day.split("-")]
    hh, mm = [int(x) for x in (hhmm or "13:00").split(":")]
    local = datetime(y, m, d, hh, mm)

    def nth_sunday(month, n):
        first = date(y, month, 1)
        return first + timedelta(days=(6 - first.weekday()) % 7 + 7 * (n - 1))
    dst = nth_sunday(3, 2) <= local.date() < nth_sunday(11, 1)
    return (local + timedelta(hours=4 if dst else 5)).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------- game logs ----------

def team_logs(game_rows, team_stats, quarters):
    """{team: [records newest first]} with the same shape the NHL code uses
    (gf/ga/periods/end/win...) plus NFL extras (yards, turnovers, lines)."""
    logs = defaultdict(list)
    for g in game_rows:
        if g["home_score"] == "" or g["away_score"] == "":
            continue
        q = quarters.get(g["espn"])
        for side, other in (("home", "away"), ("away", "home")):
            team, opp = g[side + "_team"], g[other + "_team"]
            gf, ga = _i(g[side + "_score"]), _i(g[other + "_score"])
            periods = None
            if q and q.get(side) and q.get(other):
                periods = [[q[side][i] if i < len(q[side]) else 0, q[other][i] if i < len(q[other]) else 0] for i in range(4)]
            ts = team_stats.get((g["season"], g["week"], team), {})
            os_ = team_stats.get((g["season"], g["week"], opp), {})
            spread_home = _f(g["spread_line"], None)  # expected home margin
            spread = None if spread_home is None else (-spread_home if side == "home" else spread_home)
            logs[team].append({
                "gameId": g["game_id"], "espn": g["espn"], "date": g["gameday"], "season": int(g["season"]),
                "week": int(g["week"]), "tag": "W%s" % g["week"], "opp": opp, "home": side == "home",
                "gf": gf, "ga": ga, "win": gf > ga, "tie": gf == ga,
                "end": "OT" if g["overtime"] == "1" else "REG",
                "periods": periods,
                "regGf": sum(p[0] for p in periods) if periods else gf,
                "regGa": sum(p[1] for p in periods) if periods else ga,
                "spread": spread, "totalLine": _f(g["total_line"], None),
                "yds": _yards(ts), "ydsA": _yards(os_),
                "pass": _f(ts.get("passing_yards")), "passA": _f(os_.get("passing_yards")),
                "rush": _f(ts.get("rushing_yards")), "rushA": _f(os_.get("rushing_yards")),
                "to": _turnovers(ts), "toA": _turnovers(os_),
                "tds": _i(ts.get("passing_tds")) + _i(ts.get("rushing_tds")) + _i(ts.get("special_teams_tds")) + _i(ts.get("def_tds")),
                "fgA": _i(os_.get("fg_made")),  # field goals the opponent kicked against this team
                "scoredFirst": None, "scorers": [],
            })
    for t in logs:
        logs[t].sort(key=lambda r: (r["season"], r["week"]), reverse=True)
    return logs


def _yards(ts):
    return _f(ts.get("passing_yards")) + _f(ts.get("rushing_yards")) - _f(ts.get("sack_yards_lost"))


def _turnovers(ts):
    return _i(ts.get("passing_interceptions")) + _i(ts.get("fumbles_lost_total") or ts.get("rushing_fumbles_lost"))


def _avg(vals):
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / float(len(vals)), 3) if vals else None


def _pct(flags):
    flags = [f for f in flags if f is not None]
    return round(sum(1 for f in flags if f) / float(len(flags)), 3) if flags else None


def aggregate(games, legs):
    if not games:
        return {"gp": 0}
    with_q = [r for r in games if r["periods"]]
    fp = legs["first_quarter_team_line"]
    out = {
        "gp": len(games),
        "gfPg": _avg(r["gf"] for r in games), "gaPg": _avg(r["ga"] for r in games),
        "regTotalPg": _avg(r["gf"] + r["ga"] for r in games),
        "ydsFPg": _avg(r["yds"] for r in games), "ydsAPg": _avg(r["ydsA"] for r in games),
        "passFPg": _avg(r["pass"] for r in games), "passAPg": _avg(r["passA"] for r in games),
        "rushFPg": _avg(r["rush"] for r in games), "rushAPg": _avg(r["rushA"] for r in games),
        "toPg": _avg(r["to"] for r in games), "toAPg": _avg(r["toA"] for r in games),
        "fgAPg": _avg(r["fgA"] for r in games),
        "winPct": _pct(r["win"] for r in games),
        "atsPct": _pct(None if r["spread"] is None or r["gf"] - r["ga"] + r["spread"] == 0 else r["gf"] - r["ga"] + r["spread"] > 0 for r in games),
        "overPct": _pct(None if r["totalLine"] is None or r["gf"] + r["ga"] == r["totalLine"] else r["gf"] + r["ga"] > r["totalLine"] for r in games),
        "scored1pPct": _pct(r["periods"][0][0] > fp for r in with_q),
        "conceded1pPct": _pct(r["periods"][0][1] > fp for r in with_q),
        "byPeriod": [{"gf": _avg(r["periods"][i][0] for r in with_q), "ga": _avg(r["periods"][i][1] for r in with_q)} for i in range(4)],
    }
    out["gdPg"] = round(out["gfPg"] - out["gaPg"], 3)
    pts = sum(r["gf"] for r in games)
    out["tdShare"] = round(min(0.9, max(0.5, 7.0 * sum(r["tds"] for r in games) / pts)), 3) if pts else 0.75
    home = [r for r in games if r["home"]]
    away = [r for r in games if not r["home"]]
    out["home"] = {"gp": len(home), "gfPg": _avg(r["gf"] for r in home), "gaPg": _avg(r["ga"] for r in home)}
    out["away"] = {"gp": len(away), "gfPg": _avg(r["gf"] for r in away), "gaPg": _avg(r["ga"] for r in away)}
    return out


def leg_rates(games, legs, as_favorite):
    rows = []
    for r in games:
        if not r["periods"]:
            continue
        ml = r["win"] if as_favorite else (not r["win"] and not r["tie"])
        line = r["totalLine"] or legs["total_line_fallback"]
        over = r["gf"] + r["ga"] > line
        fp = (r["periods"][0][0] if as_favorite else r["periods"][0][1]) > legs["first_quarter_team_line"]
        rows.append((ml, over, fp, ml and over and fp))
    if not rows:
        return None
    return {"gp": len(rows), "ml": _pct(x[0] for x in rows), "over": _pct(x[1] for x in rows),
            "firstPeriod": _pct(x[2] for x in rows), "all3": _pct(x[3] for x in rows)}


# ---------- players ----------

POS = {"QB": "QB", "RB": "RB", "FB": "RB", "WR": "WR", "TE": "TE", "K": "K"}


def player_rows(rows, season, before_week=None):
    out = []
    for r in rows:
        if r.get("season_type") != "REG":
            continue
        pos = POS.get(r.get("position"))
        if not pos:
            continue
        wk = int(r["week"])
        if before_week is not None and wk >= before_week:
            continue
        rush_td, rec_td = _i(r.get("rushing_tds")), _i(r.get("receiving_tds"))
        out.append({
            "id": r["player_id"], "name": r["player_display_name"], "team": r["team"], "pos": pos,
            "headshot": r.get("headshot_url"), "week": wk, "season": season, "gameId": r["game_id"],
            "date": "%d-%02d" % (season, wk),
            "cmp": _i(r.get("completions")), "att": _i(r.get("attempts")),
            "pass_yds": _i(r.get("passing_yards")), "pass_tds": _i(r.get("passing_tds")), "ints": _i(r.get("passing_interceptions")),
            "carries": _i(r.get("carries")), "rush_yds": _i(r.get("rushing_yards")), "rush_tds": rush_td,
            "rec": _i(r.get("receptions")), "tgt": _i(r.get("targets")), "rec_yds": _i(r.get("receiving_yards")), "rec_tds": rec_td,
            "any_td": rush_td + rec_td,
            "pass_rush_yds": _i(r.get("passing_yards")) + _i(r.get("rushing_yards")),
            "rush_rec_yds": _i(r.get("rushing_yards")) + _i(r.get("receiving_yards")),
            "opp": r.get("opponent_team"), "tag": "W%d" % wk, "home": r["game_id"].split("_")[-1] == r["team"],
            "fg_made": _i(r.get("fg_made")), "fg_att": _i(r.get("fg_att")), "pat_made": _i(r.get("pat_made")),
            "kick_pts": 3 * _i(r.get("fg_made")) + _i(r.get("pat_made")),
        })
    return out


def _qb(r): return r["att"] >= 15
def _rb(r): return r["carries"] >= 8
def _wr(r): return r["tgt"] >= 3
def _k(r): return r["fg_att"] + r["pat_made"] >= 1
def _v(k): return lambda r: r.get(k, 0)


PLAYER_PROPS = [
    {"stat": "pass_yds", "label": "passing yards", "lines": [199.5, 224.5, 249.5, 274.5], "sides": ["over", "under"], "pos": {"QB"}, "usage": _qb, "value": _v("pass_yds")},
    {"stat": "pass_tds", "label": "passing TDs", "lines": [0.5, 1.5, 2.5], "sides": ["over"], "pos": {"QB"}, "usage": _qb, "value": _v("pass_tds")},
    {"stat": "cmp", "label": "completions", "lines": [17.5, 20.5, 23.5], "sides": ["over", "under"], "pos": {"QB"}, "usage": _qb, "value": _v("cmp")},
    {"stat": "rush_yds", "label": "rushing yards", "lines": [19.5, 29.5], "sides": ["over"], "pos": {"QB"}, "usage": _qb, "value": _v("rush_yds")},
    {"stat": "ints", "label": "interceptions", "lines": [0.5], "sides": ["under", "over"], "pos": {"QB"}, "usage": _qb, "value": _v("ints")},
    {"stat": "pass_rush_yds", "label": "pass + rush yards", "lines": [224.5, 249.5, 274.5], "sides": ["over", "under"], "pos": {"QB"}, "usage": _qb, "value": _v("pass_rush_yds")},
    {"stat": "rush_rec_yds", "label": "rush + rec yards", "lines": [59.5, 79.5, 99.5], "sides": ["over", "under"], "pos": {"RB"}, "usage": _rb, "value": _v("rush_rec_yds")},
    {"stat": "rush_yds", "label": "rushing yards", "lines": [39.5, 59.5, 79.5, 99.5], "sides": ["over", "under"], "pos": {"RB"}, "usage": _rb, "value": _v("rush_yds")},
    {"stat": "carries", "label": "rush attempts", "lines": [12.5, 15.5, 18.5], "sides": ["over"], "pos": {"RB"}, "usage": _rb, "value": _v("carries")},
    {"stat": "rec", "label": "receptions", "lines": [1.5, 2.5, 3.5], "sides": ["over"], "pos": {"RB"}, "usage": _rb, "value": _v("rec")},
    {"stat": "any_td", "label": "touchdowns (anytime)", "lines": [0.5], "sides": ["over"], "pos": {"RB"}, "usage": _rb, "value": _v("any_td")},
    {"stat": "rec_yds", "label": "receiving yards", "lines": [29.5, 49.5, 69.5, 89.5], "sides": ["over", "under"], "pos": {"WR", "TE"}, "usage": _wr, "value": _v("rec_yds")},
    {"stat": "rec", "label": "receptions", "lines": [2.5, 3.5, 4.5, 5.5, 6.5], "sides": ["over"], "pos": {"WR", "TE"}, "usage": _wr, "value": _v("rec")},
    {"stat": "any_td", "label": "touchdowns (anytime)", "lines": [0.5], "sides": ["over"], "pos": {"WR", "TE"}, "usage": _wr, "value": _v("any_td")},
    {"stat": "fg_made", "label": "field goals made", "lines": [0.5, 1.5, 2.5], "sides": ["over", "under"], "pos": {"K"}, "usage": _k, "value": _v("fg_made")},
    {"stat": "kick_pts", "label": "kicking points", "lines": [5.5, 7.5, 9.5], "sides": ["over"], "pos": {"K"}, "usage": _k, "value": _v("kick_pts")},
]


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
    for line, txt in ((-6.5, "7+"), (-9.5, "10+"), (-13.5, "14+")):
        add("by%s" % txt, "{T} wins by %s" % txt, "winby", -line, {"m": "spread", "s": "game", "t": "home", "line": line})
    add("q1", "{T} scores in the 1st quarter", "q1score", 0, {"m": "team", "s": "p1", "t": "home", "side": "over", "line": 0.5})
    add("q1a", "{T} allows points in the 1st quarter", "q1allow", 0, {"m": "team", "s": "p1", "t": "away", "side": "over", "line": 0.5})
    add("q1w", "{T} wins the 1st quarter", "q1win", 0, {"m": "win", "s": "p1", "t": "home"})
    add("h1w", "{T} leads at halftime", "h1win", 0, {"m": "win", "s": "h1", "t": "home"})
    add("h2w", "{T} wins the 2nd half", "h2win", 0, {"m": "win", "s": "h2", "t": "home"})
    for line in (20.5, 23.5, 27.5, 30.5):
        add("tt_o%s" % line, "{T} scores %d+ points" % (line + 0.5), "tt_over", line, {"m": "team", "s": "game", "t": "home", "side": "over", "line": line})
        add("ta_o%s" % line, "{T} allows %d+ points" % (line + 0.5), "ta_over", line, {"m": "team", "s": "game", "t": "away", "side": "over", "line": line})
    for line in (20.5, 17.5, 14.5):
        add("tt_u%s" % line, "{T} scores under %s points" % line, "tt_under", -line, {"m": "team", "s": "game", "t": "home", "side": "under", "line": line})
        add("ta_u%s" % line, "{T} allows under %s points" % line, "ta_under", -line, {"m": "team", "s": "game", "t": "away", "side": "under", "line": line})
    for line in (10.5, 13.5, 16.5):
        add("h1_o%s" % line, "{T} scores %d+ in the 1st half" % (line + 0.5), "h1_over", line, {"m": "team", "s": "h1", "t": "home", "side": "over", "line": line})
    for line in (40.5, 44.5, 48.5, 52.5):
        add("g_o%s" % line, "{T} games: over %s points" % line, "g_over", line, {"m": "total", "s": "game", "side": "over", "line": line})
    for line in (44.5, 40.5, 36.5):
        add("g_u%s" % line, "{T} games: under %s points" % line, "g_under", -line, {"m": "total", "s": "game", "side": "under", "line": line})
    for line in (20.5, 23.5):
        add("h1t_o%s" % line, "{T} games: 1st half over %s" % line, "h1t_over", line, {"m": "total", "s": "h1", "side": "over", "line": line})
    return P


TEAM_PROPS = _team_props()
PROP_STATS = ("cmp", "att", "pass_yds", "pass_tds", "carries", "rush_yds", "rec", "tgt", "rec_yds", "any_td", "fg_made", "kick_pts",
              "ints", "pass_rush_yds", "rush_rec_yds")

# Stats the bet builder offers as player props (key, label, positions).
BUILDER_PROPS = [
    {"key": "pass_yds", "label": "Passing yards", "pos": "QB"},
    {"key": "pass_tds", "label": "Passing TDs", "pos": "QB"},
    {"key": "cmp", "label": "Completions", "pos": "QB"},
    {"key": "ints", "label": "Interceptions", "pos": "QB"},
    {"key": "pass_rush_yds", "label": "Pass + rush yards", "pos": "QB"},
    {"key": "rush_yds", "label": "Rushing yards", "pos": "QB RB WR"},
    {"key": "carries", "label": "Rush attempts", "pos": "QB RB"},
    {"key": "rush_rec_yds", "label": "Rush + rec yards", "pos": "RB WR TE"},
    {"key": "rec", "label": "Receptions", "pos": "RB WR TE"},
    {"key": "rec_yds", "label": "Receiving yards", "pos": "RB WR TE"},
    {"key": "any_td", "label": "Touchdowns (anytime)", "pos": "QB RB WR TE"},
    {"key": "fg_made", "label": "Field goals made", "pos": "K"},
    {"key": "kick_pts", "label": "Kicking points", "pos": "K"},
]
USAGE = {"QB": _qb, "RB": _rb, "WR": _wr, "TE": _wr, "K": _k}


def export_players(by_team, team):
    out = []
    for pid, rows in by_team.get(team, {}).items():
        pos = rows[0]["pos"]
        used = [r for r in rows if USAGE[pos](r)]
        if len(used) < 2:
            continue
        out.append({"id": pid, "name": rows[0]["name"], "pos": pos,
                    "rows": [[r.get(s["key"], 0) for s in BUILDER_PROPS] for r in used]})
    order = {"QB": 0, "RB": 1, "WR": 2, "TE": 3, "K": 4}
    out.sort(key=lambda p: (order[p["pos"]], -len(p["rows"])))
    return out


def _prop_label(name, prop):
    stat_label = next((p["label"] for p in PLAYER_PROPS if p["stat"] == prop["stat"]), prop["stat"])
    line = prop["line"]
    if prop["side"] == "over" and line % 1 == 0.5:
        return "%s %d+ %s" % (name, int(line + 0.5), stat_label)
    return "%s %s %s %s" % (name, prop["side"], line, stat_label)


def key_players(rows, team):
    """Season per-game averages for the team's main skill players and kicker."""
    agg = defaultdict(lambda: {"gp": 0})
    for r in rows:
        if r["team"] != team:
            continue
        a = agg[r["id"]]
        a.update({"id": r["id"], "name": r["name"], "pos": r["pos"], "headshot": r["headshot"]})
        a["gp"] += 1
        for k in PROP_STATS + ("rush_tds", "rec_tds", "ints", "fg_att", "pat_made"):
            a[k] = a.get(k, 0) + r.get(k, 0)
    players = list(agg.values())

    def top(pos, key, n):
        c = [p for p in players if p["pos"] in pos]
        c.sort(key=lambda p: p.get(key, 0), reverse=True)
        return c[:n]
    picked = top({"QB"}, "att", 1) + top({"RB"}, "carries", 2) + top({"WR"}, "tgt", 3) + top({"TE"}, "tgt", 1) + top({"K"}, "fg_att", 1)
    for p in picked:
        gp = float(p["gp"]) or 1
        for k in PROP_STATS + ("rush_tds", "rec_tds", "ints", "fg_att", "pat_made"):
            p[k + "Pg"] = round(p.get(k, 0) / gp, 1)
    return picked


# ---------- week build ----------

def build_week(season, week, cfg, src):
    slate = [g for g in src["games"] if g["season"] == str(season) and g["game_type"] == "REG" and g["week"] == str(week)]
    if not slate:
        return None
    legs = cfg["legs"]
    w = cfg["recent_weight"]
    teams = sorted({g["home_team"] for g in src["games"] if g["season"] == str(season)})

    prior_games = [g for g in src["games"] if g["season"] == str(season - 1) and g["game_type"] == "REG"]
    cur_games = [g for g in src["games"] if g["season"] == str(season) and g["game_type"] == "REG" and int(g["week"]) < week]
    quarters = {}
    for s, wk in sorted({(int(g["season"]), int(g["week"])) for g in prior_games + cur_games + slate}):
        for eid, ev in src["espn"](s, wk).items():
            quarters[eid] = {"home": ev["teams"]["home"]["quarters"], "away": ev["teams"]["away"]["quarters"]}
    espn_now = src["espn"](season, week)

    logs = team_logs(prior_games + cur_games, src["team_stats"], quarters)
    profiles = {}
    for team in teams:
        log = logs.get(team, [])
        sample, n_cur, n_prior = stats.build_sample(log, season, cfg["min_sample_games"])
        recent = sample[: cfg["recent_games"]]
        agg_s, agg_r = aggregate(sample, legs), aggregate(recent, legs)
        prof = {"team": team, "sample": agg_s, "recent": agg_r, "nCurrent": n_cur, "nPrior": n_prior,
                "_sampleGames": sample, "_recentGames": recent, "_log": log}
        for k in ("gfPg", "gaPg", "gdPg", "ydsFPg", "ydsAPg", "toPg", "toAPg", "scored1pPct", "conceded1pPct", "regTotalPg"):
            prof[k] = stats.blend(agg_s.get(k), agg_r.get(k), w)
        profiles[team] = prof

    def rank(key, reverse):
        order = sorted(teams, key=lambda t: profiles[t]["sample"].get(key) if profiles[t]["sample"].get(key) is not None else (-1e9 if reverse else 1e9), reverse=reverse)
        return {t: i + 1 for i, t in enumerate(order)}
    gf_rank = sorted(teams, key=lambda t: profiles[t]["gfPg"] or -1, reverse=True)
    ga_rank = sorted(teams, key=lambda t: profiles[t]["gaPg"] if profiles[t]["gaPg"] is not None else 99)
    for i, t in enumerate(gf_rank):
        profiles[t]["gfRank"] = i + 1
    for i, t in enumerate(ga_rank):
        profiles[t]["gaRank"] = i + 1
    ranks = {"passA": rank("passAPg", True), "rushA": rank("rushAPg", True), "gaR": rank("gaPg", True), "fgA": rank("fgAPg", True)}

    # players (this season, earlier weeks) for trends + key players
    prows = player_rows(src["player_rows"](season), season, before_week=week)
    by_team = defaultdict(lambda: defaultdict(list))
    for r in prows:
        by_team[r["team"]][r["id"]].append(r)
    for t in by_team:
        for pid in by_team[t]:
            by_team[t][pid].sort(key=lambda r: r["week"], reverse=True)
    logs_cur = {t: [r for r in logs.get(t, []) if r["season"] == season and r["periods"]] for t in teams}
    league_out = trends.league_outcomes(logs_cur, extras=("spread", "totalLine"))
    base_cache = {}
    dists = {}
    tcfg = cfg["trends"]

    def ctx(stat, opp):
        p = profiles[opp]["sample"]
        if stat in ("pass_yds", "cmp", "pass_tds", "rec_yds", "rec"):
            return "%s allows %.0f pass yds/gm (#%d most)" % (opp, p.get("passAPg") or 0, ranks["passA"][opp])
        if stat in ("rush_yds", "carries"):
            return "%s allows %.0f rush yds/gm (#%d most)" % (opp, p.get("rushAPg") or 0, ranks["rushA"][opp])
        if stat == "any_td":
            return "%s allows %.1f pts/gm (#%d most)" % (opp, p.get("gaPg") or 0, ranks["gaR"][opp])
        if stat in ("fg_made", "kick_pts"):
            return "%s allows %.1f FGs/gm (#%d most)" % (opp, p.get("fgAPg") or 0, ranks["fgA"][opp])
        return None

    final_rows = {}
    for r in player_rows(src["player_rows"](season), season):
        final_rows.setdefault(r["gameId"], {})[r["id"]] = {k: r[k] for k in PROP_STATS}

    out_games = []
    for g in slate:
        home, away = g["home_team"], g["away_team"]
        ev = espn_now.get(g["espn"], {})
        side = {}
        for abbrev, slot, is_home in ((home, "home", True), (away, "away", False)):
            prof = dict(profiles[abbrev])
            prof["isHome"] = is_home
            spread_home = _f(g["spread_line"], None)
            prof["spread"] = None if spread_home is None else (-spread_home if is_home else spread_home)
            prof["rest"] = _i(g[slot + "_rest"], None)
            side[abbrev] = prof
        s_home, d_home = scoring.score_side(side[home], side[away], cfg["conditions"], nfl_scoring.CONDITIONS)
        s_away, d_away = scoring.score_side(side[away], side[home], cfg["conditions"], nfl_scoring.CONDITIONS)
        fav, dog = (home, away) if s_home >= s_away else (away, home)
        score, details = (s_home, d_home) if fav == home else (s_away, d_away)

        game = {
            "id": g["game_id"], "startTimeUTC": _eastern_to_utc(g["gameday"], g["gametime"]),
            "venue": ev.get("venue") or g.get("stadium"), "home": home, "away": away,
            "lines": {"spread": _f(g["spread_line"], None), "total": _f(g["total_line"], None),
                      "homeML": g["home_moneyline"], "awayML": g["away_moneyline"]},
            "stomp": {"score": score, "fav": fav, "dog": dog, "conditions": details, "flag": None,
                      "otherSideScore": s_away if fav == home else s_home},
            "teams": {}, "h2h": [], "result": None,
        }
        for abbrev, slot in ((home, "home"), (away, "away")):
            p = side[abbrev]
            info = (ev.get("teams") or {}).get(slot, {})
            espn_abbr = (info.get("abbr") or abbrev).lower()
            logo_url = "https://a.espncdn.com/i/teamlogos/nfl/500/%s.png" % espn_abbr
            is_fav = abbrev == fav
            game["teams"][abbrev] = {
                "abbrev": abbrev, "name": info.get("name") or abbrev, "short": info.get("short") or abbrev,
                "logo": logo_url, "darkLogo": "https://a.espncdn.com/i/teamlogos/nfl/500-dark/%s.png" % espn_abbr,
                "odds": _dec(g[slot + "_moneyline"]), "spread": p["spread"], "rest": p["rest"],
                "record": info.get("record") or _record(p["_log"], season), "streak": _streak(p["_log"], season),
                "b2b": False, "nCurrent": p["nCurrent"], "nPrior": p["nPrior"],
                "gfRank": p["gfRank"], "gaRank": p["gaRank"],
                "blended": {k: p[k] for k in ("gfPg", "gaPg", "gdPg", "scored1pPct", "conceded1pPct", "regTotalPg")},
                "sample": p["sample"], "recent": p["recent"],
                "legs": {"role": "favorite" if is_fav else "underdog",
                         "sample": leg_rates(p["_sampleGames"], legs, is_fav),
                         "recent": leg_rates(p["_recentGames"], legs, is_fav)},
                "lastGames": [_game_row(r) for r in p["_log"][:10]],
                "log": [{"h": r["home"], "gf": r["gf"], "ga": r["ga"], "p": r["periods"], "e": r["end"],
                         "ls": 1 if r["season"] < season else 0}
                        for r in ([x for x in p["_log"] if x["season"] == season and x["periods"]] +
                                  [x for x in p["_log"] if x["season"] < season and x["periods"]][:cfg["last_season_games"]])],
                "keyPlayers": key_players(prows, abbrev),
                "players": export_players(by_team, abbrev),
                "goalies": [], "skaters": [],
            }
        game["h2h"] = [_game_row(r) for r in side[home]["_log"] if r["opp"] == away][:5]

        items = []
        for team, opp, slot in ((home, away, "home"), (away, home, "away")):
            items += trends.team_trends(team, opp, slot, logs_cur, league_out, TEAM_PROPS, tcfg,
                                        extras=("spread", "totalLine"), base_cache=base_cache, dists=dists)
            pt = trends.player_trends(team, opp, by_team.get(team, {}), prows, PLAYER_PROPS, tcfg, ctx_fn=ctx, dists=dists,
                                      info=lambda pid, team=team: {"headshot": by_team[team][pid][0]["headshot"]})
            for t in pt:
                t["label"] = _prop_label(t["name"], t["prop"])
            items += pt
        game["trends"] = trends.finalize(items, tcfg)

        if g["home_score"] != "" and g["away_score"] != "":
            hs, as_ = _i(g["home_score"]), _i(g["away_score"])
            q = quarters.get(g["espn"])
            periods = [[q["home"][i], q["away"][i]] for i in range(4)] if q and len(q["home"]) >= 4 and len(q["away"]) >= 4 else None
            fs, ds = (hs, as_) if fav == home else (as_, hs)
            fq = None if not periods else (periods[0][0] if fav == home else periods[0][1])
            line = game["lines"]["total"] or legs["total_line_fallback"]
            ml, over, fp = fs > ds, hs + as_ > line, fq is not None and fq > legs["first_quarter_team_line"]
            game["result"] = {"homeScore": hs, "awayScore": as_, "end": "OT" if g["overtime"] == "1" else "REG",
                              "periods": periods, "regTotal": hs + as_,
                              "legs": {"ml": ml, "over": over, "firstPeriod": fp, "all3": ml and over and fp},
                              "box": final_rows.get(g["game_id"])}
        out_games.append(game)

    scoring.assign_flags(out_games, cfg)
    out_games.sort(key=lambda x: (x["stomp"]["flag"] is None, -x["stomp"]["score"], x["startTimeUTC"]))
    league = [_avg(profiles[t]["sample"]["byPeriod"][i]["gf"] for t in teams if profiles[t]["sample"].get("gp")) for i in range(4)]
    days = sorted({g["gameday"] for g in slate})
    return {
        "date": "%d-w%02d" % (season, week), "sport": "nfl", "season": season, "week": week,
        "label": "Week %d" % week, "start": days[0], "end": days[-1],
        "generatedAt": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "settings": {
            "recentGames": cfg["recent_games"], "minSampleGames": cfg["min_sample_games"], "recentWeight": w,
            "legs": legs, "tiers": cfg["flag_tiers"], "simulation": cfg.get("simulation", {}),
            "builder": cfg.get("builder", {}), "trends": tcfg, "leaguePeriodGf": league,
            "propStats": BUILDER_PROPS,
            "periods": 4, "scopes": SCOPES,
            "markets": ["win", "total", "team", "spread"],
            "lineDefaults": {"total": legs["total_line_fallback"], "team": 20.5, "teamScope": "game", "spread": -3.5,
                             "periodTotal": 9.5},
        },
        "trendDist": dists,
        "games": out_games,
    }


def _record(log, season):
    cur = [r for r in log if r["season"] == season]
    w = sum(1 for r in cur if r["win"])
    t = sum(1 for r in cur if r["tie"])
    return "%d-%d%s" % (w, len(cur) - w - t, "-%d" % t if t else "")


def _streak(log, season):
    cur = [r for r in log if r["season"] == season]
    if not cur:
        return ""
    code = "W" if cur[0]["win"] else ("T" if cur[0]["tie"] else "L")
    n = 0
    for r in cur:
        c = "W" if r["win"] else ("T" if r["tie"] else "L")
        if c != code:
            break
        n += 1
    return "%s%d" % (code, n)


def _game_row(r):
    return {"date": r["date"], "opp": r["opp"], "home": r["home"], "gf": r["gf"], "ga": r["ga"], "end": r["end"],
            "win": r["win"], "periods": r["periods"] or [[0, 0]] * 4, "regTotal": r["gf"] + r["ga"],
            "season": r["season"], "week": r["week"], "totalLine": r["totalLine"], "spread": r["spread"]}


def current_week(game_rows, season):
    reg = [g for g in game_rows if g["season"] == str(season) and g["game_type"] == "REG"]
    unplayed = [int(g["week"]) for g in reg if g["home_score"] == ""]
    return min(unplayed) if unplayed else max(int(g["week"]) for g in reg)


def write_index():
    keys = sorted(f[:-5] for f in os.listdir(OUT_DIR) if f.endswith(".json") and f != "index.json")
    days = []
    for k in keys:
        with open(os.path.join(OUT_DIR, k + ".json"), encoding="utf-8") as f:
            d = json.load(f)
        ff = [g["result"]["legs"] for g in d["games"] if g["stomp"]["flag"] and g.get("result")]
        days.append({"date": k, "label": d["label"], "start": d["start"], "end": d["end"], "games": len(d["games"]),
                     "flagged": sum(1 for g in d["games"] if g["stomp"]["flag"]), "flaggedFinal": len(ff),
                     "hits": {x: sum(1 for l in ff if l[x]) for x in ("ml", "over", "firstPeriod", "all3")}})
    with open(os.path.join(OUT_DIR, "index.json"), "w", encoding="utf-8") as f:
        json.dump({"days": days, "updatedAt": datetime.utcnow().replace(microsecond=0).isoformat() + "Z"}, f)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--week", type=int, help="build only this week")
    ap.add_argument("--weeks-back", type=int, default=1, help="how many past weeks to rebuild")
    args = ap.parse_args()

    cfg = load_config()
    print("NFL: downloading schedules and stats...")
    game_rows = nfl_api.games()
    season = max(int(g["season"]) for g in game_rows if g["game_type"] == "REG")
    team_stats = {}
    for s in (season - 1, season):
        for r in nfl_api.team_weeks(s, season):
            if r.get("season_type", "REG") == "REG":
                team_stats[(r["season"], r["week"], r["team"])] = r
    pcache = {}

    def prow(s):
        if s not in pcache:
            pcache[s] = nfl_api.player_weeks(s, season)
        return pcache[s]
    src = {"games": game_rows, "team_stats": team_stats, "espn": nfl_api.espn_week, "player_rows": prow}

    cur = current_week(game_rows, season)
    weeks = [args.week] if args.week else [w for w in range(cur - args.weeks_back, cur + 1) if w >= 1]
    os.makedirs(OUT_DIR, exist_ok=True)
    for wk in weeks:
        print("Building NFL %d week %d..." % (season, wk))
        data = build_week(season, wk, cfg, src)
        if data is None:
            continue
        with open(os.path.join(OUT_DIR, data["date"] + ".json"), "w", encoding="utf-8") as f:
            json.dump(data, f, separators=(",", ":"))
        flagged = [g for g in data["games"] if g["stomp"]["flag"]]
        print("  %d games, %d flagged: %s" % (len(data["games"]), len(flagged), ", ".join(
            "%s vs %s (%d, %s)" % (g["stomp"]["fav"], g["stomp"]["dog"], g["stomp"]["score"], g["stomp"]["flag"]) for g in flagged)))
    write_index()


if __name__ == "__main__":
    main()
