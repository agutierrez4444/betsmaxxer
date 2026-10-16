"""NHL trend catalog: which team and player bets to check, at the lines
sportsbooks usually offer."""
from collections import defaultdict

from . import nhl_api, trends

PERIOD = {1: "1st", 2: "2nd", 3: "3rd"}


def _team_props():
    P = []

    def add(key, label, group, rank=0, leg=None, pred=None):
        p = {"key": key, "label": label, "group": group, "rank": rank}
        if leg:
            p["leg"] = leg
        if pred:
            p["pred"] = pred
        P.append(p)

    add("first", "{T} scores first", "first", pred=lambda o: None if o["first"] is None else o["first"] == "home")
    add("ml", "{T} wins (incl. OT)", "win", 0, {"m": "win", "s": "game", "t": "home"})
    add("reg_win", "{T} wins in regulation", "win", 1, {"m": "win", "s": "reg", "t": "home"})
    add("p1_win", "{T} wins the 1st period", "p1win", 0, {"m": "win", "s": "p1", "t": "home"})
    for i in (1, 2, 3):
        add("p%d_score" % i, "{T} scores in the %s period" % PERIOD[i], "p%dscore" % i, 0,
            {"m": "team", "s": "p%d" % i, "t": "home", "side": "over", "line": 0.5})
        add("p%d_allow" % i, "{T} allows a goal in the %s period" % PERIOD[i], "p%dallow" % i, 0,
            {"m": "team", "s": "p%d" % i, "t": "away", "side": "over", "line": 0.5})
    for line in (2.5, 3.5, 4.5):
        add("tt_o%s" % line, "{T} scores %d+ goals" % (line + 0.5), "tt_over", line,
            {"m": "team", "s": "game", "t": "home", "side": "over", "line": line})
        add("ta_o%s" % line, "{T} allows %d+ goals" % (line + 0.5), "ta_over", line,
            {"m": "team", "s": "game", "t": "away", "side": "over", "line": line})
    for line in (1.5, 2.5):
        add("tt_u%s" % line, "{T} scores under %s goals" % line, "tt_under", -line,
            {"m": "team", "s": "game", "t": "home", "side": "under", "line": line})
        add("ta_u%s" % line, "{T} allows under %s goals" % line, "ta_under", -line,
            {"m": "team", "s": "game", "t": "away", "side": "under", "line": line})
    for line in (5.5, 6.5):
        add("reg_o%s" % line, "{T} games: over %s goals (regulation)" % line, "reg_over", line,
            {"m": "total", "s": "reg", "side": "over", "line": line})
    for line in (5.5, 4.5):
        add("reg_u%s" % line, "{T} games: under %s goals (regulation)" % line, "reg_under", -line,
            {"m": "total", "s": "reg", "side": "under", "line": line})
    add("p1_o15", "{T} games: 1st period over 1.5", "p1_over", 1.5, {"m": "total", "s": "p1", "side": "over", "line": 1.5})
    add("p1_u15", "{T} games: 1st period under 1.5", "p1_under", -1.5, {"m": "total", "s": "p1", "side": "under", "line": 1.5})
    add("btts", "{T} games: both teams score", "btts", 0, {"m": "btts", "s": "game", "side": "yes"})
    add("ot", "{T} games go to overtime", "ot", pred=lambda o: o["end"] != "REG")
    add("all_periods", "{T} scores in all 3 periods", "allp", pred=lambda o: all(q[0] > 0 for q in o["p"]))
    add("any_period", "{T} wins at least one period", "anyp", pred=lambda o: any(q[0] > q[1] for q in o["p"]))
    return P


TEAM_PROPS = _team_props()


def _skater(r):
    return r["toi"] >= 600  # 10+ minutes: a regular shift


def _starter(r):
    return r.get("starter", False)


def _v(k):
    return lambda r: r.get(k, 0)


PLAYER_PROPS = [
    {"stat": "g", "label": "goals", "lines": [0.5, 1.5], "sides": ["over"], "pos": {"F", "D"}, "usage": _skater, "value": _v("g")},
    {"stat": "a", "label": "assists", "lines": [0.5, 1.5], "sides": ["over"], "pos": {"F", "D"}, "usage": _skater, "value": _v("a")},
    {"stat": "pts", "label": "points", "lines": [0.5, 1.5, 2.5], "sides": ["over"], "pos": {"F", "D"}, "usage": _skater, "value": _v("pts")},
    {"stat": "sog", "label": "shots on goal", "lines": [1.5, 2.5, 3.5, 4.5], "sides": ["over", "under"], "pos": {"F", "D"}, "usage": _skater, "value": _v("sog")},
    {"stat": "saves", "label": "saves", "lines": [22.5, 25.5, 28.5, 31.5], "sides": ["over", "under"], "pos": {"G"}, "usage": _starter, "value": _v("saves")},
]

# Stats the bet builder offers as player props (key, label, positions).
PROP_STATS = [
    {"key": "g", "label": "Goals", "pos": "FD"},
    {"key": "a", "label": "Assists", "pos": "FD"},
    {"key": "pts", "label": "Points", "pos": "FD"},
    {"key": "sog", "label": "Shots on goal", "pos": "FD"},
    {"key": "saves", "label": "Saves", "pos": "G"},
]


def export_players(by_team, team, names, season):
    """This season's per-game stats for the team's regulars, for builder props.
    Rows are newest first, values in PROP_STATS order."""
    out = []
    for pid, rows in by_team.get(team, {}).items():
        pos = rows[0]["pos"]
        used = [r for r in rows if (_starter(r) if pos == "G" else _skater(r))]
        if len(used) < 3:
            continue
        full = names.get(pid, {})
        out.append({
            "id": pid, "name": full.get("name") or rows[0]["name"], "pos": pos,
            "rows": [[r.get(s["key"], 0) for s in PROP_STATS] for r in used],
        })
    out.sort(key=lambda p: (p["pos"] == "G", -sum(r[2] for r in p["rows"]) / len(p["rows"])))
    return out


def load_boxes(game_ids):
    boxes = {}
    for gid in game_ids:
        try:
            b = nhl_api.boxscore(gid)
        except RuntimeError as e:
            print("  ! boxscore %s: %s" % (gid, e))
            b = None
        if b:
            boxes[gid] = b
    return boxes


def player_rows(boxes, logs_cur):
    """{team: {pid: [rows newest first]}} and a flat list of all rows."""
    date_of = {r["gameId"]: r["date"] for t in logs_cur for r in logs_cur[t]}
    by_team = defaultdict(lambda: defaultdict(list))
    flat = []
    for gid, b in boxes.items():
        for p in b["players"]:
            row = dict(p)
            row["date"] = date_of.get(gid, b.get("date"))
            by_team[p["team"]][p["id"]].append(row)
            flat.append(row)
    for t in by_team:
        for pid in by_team[t]:
            by_team[t][pid].sort(key=lambda r: r["date"], reverse=True)
    return by_team, flat


def result_box(game_id):
    """Per-player stats for a finished game, for settling saved prop bets."""
    try:
        b = nhl_api.boxscore(game_id)
    except RuntimeError:
        return None
    if not b:
        return None
    keys = ("g", "a", "pts", "sog", "saves")
    return {str(p["id"]): {k: p[k] for k in keys if k in p} for p in b["players"]}


def build(game, slots, logs_cur, profiles, by_team, flat, names, cfg, ranks, season, base_cache, league_out):
    """All trends for one game. slots = {abbrev: "home"/"away"};
    by_team/flat from player_rows(); names = {pid: {"name", "headshot"}}."""
    home, away = game["home"], game["away"]

    def ctx(stat, opp):
        p = profiles[opp]
        s = p["sample"]
        if stat == "sog":
            return "%s allows %.1f shots/gm (#%d most)" % (opp, s.get("sogAPg") or 0, ranks["sogA"][opp])
        if stat in ("g", "a", "pts"):
            return "%s allows %.2f goals/gm (#%d most)" % (opp, p["gaPg"] or 0, 33 - p["gaRank"])
        if stat == "saves":
            return "%s takes %.1f shots/gm (#%d most)" % (opp, s.get("sogFPg") or 0, ranks["sogF"][opp])
        if stat == "ga":
            return "%s scores %.2f goals/gm (#%d most)" % (opp, p["gfPg"] or 0, p["gfRank"])
        return None

    def info(pid):
        team = next((t for t in (home, away) if pid in by_team.get(t, {})), home)
        full = names.get(pid, {})
        return {
            "name": full.get("name") or by_team[team][pid][0]["name"],
            "headshot": full.get("headshot") or "https://assets.nhle.com/mugs/nhl/%s/%s/%s.png" % (season, team, pid),
        }

    tcfg = cfg["trends"]
    items = []
    for team, opp in ((home, away), (away, home)):
        items += trends.team_trends(team, opp, slots[team], logs_cur, league_out, TEAM_PROPS, tcfg,
                                    base_cache=base_cache)
        items += trends.player_trends(team, opp, by_team.get(team, {}), flat, PLAYER_PROPS, tcfg,
                                      ctx_fn=ctx, info=info)
    # Re-label players with full names now that info() has run.
    for t in items:
        if t["kind"] == "player":
            t["label"] = prop_label(t["name"], t["prop"], PLAYER_PROPS)
    return trends.finalize(items, tcfg)


def prop_label(name, prop, catalog):
    stat_label = next((p["label"] for p in catalog if p["stat"] == prop["stat"]), prop["stat"])
    line = prop["line"]
    if prop["side"] == "over" and line % 1 == 0.5:
        return "%s %d+ %s" % (name, int(line + 0.5), stat_label)
    return "%s %s %s %s" % (name, prop["side"], line, stat_label)
