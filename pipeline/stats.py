"""Turn raw daily score files into per-team game logs and aggregates."""
from collections import defaultdict
from datetime import date, timedelta

from . import nhl_api

ROUND = 3


def parse_date(s):
    y, m, d = s.split("-")
    return date(int(y), int(m), int(d))


def collect_games(as_of, today_str, current_season, need_per_team, teams, prior_per_team=0, max_days=450):
    """Walk backwards from the day before `as_of` until we have the whole
    current season, enough games to give every team `need_per_team`, and
    at least `prior_per_team` of each team's final games from last season.
    Returns completed regular-season games."""
    games = []
    counts = defaultdict(int)
    prior = defaultdict(int)
    seen_prior = False
    last_season = current_season - 10001  # 20262027 -> 20252026
    d = (parse_date(as_of) - timedelta(days=1)).isoformat()
    for _ in range(max_days):
        day = nhl_api.score_day(d, today_str)
        reached_older = False
        for g in day["games"]:
            if g["gameType"] != 2 or g["state"] not in nhl_api.FINAL_STATES:
                continue
            if g["season"] < last_season:
                reached_older = True
                continue
            games.append(g)
            for side in ("home", "away"):
                counts[g[side]["abbrev"]] += 1
                if g["season"] < current_season:
                    prior[g[side]["abbrev"]] += 1
            if g["season"] < current_season:
                seen_prior = True
        if reached_older:
            break
        if (seen_prior and all(counts[t] >= need_per_team for t in teams)
                and all(prior[t] >= prior_per_team for t in teams)):
            break
        prev = day.get("prevDate")
        d = prev if prev and prev < d else (parse_date(d) - timedelta(days=1)).isoformat()
    return games


def team_logs(games):
    """{team: [game record, ...]} newest first, from each team's point of view."""
    logs = defaultdict(list)
    for g in games:
        for side, other in (("home", "away"), ("away", "home")):
            team = g[side]["abbrev"]
            opp = g[other]["abbrev"]
            periods = [[0, 0] for _ in range(3)]
            reg_gf = reg_ga = 0
            scorers = []
            for goal in g["goals"]:
                if goal["type"] == "SO":
                    continue
                mine = goal["team"] == team
                if goal["type"] == "REG" and 1 <= goal["period"] <= 3:
                    periods[goal["period"] - 1][0 if mine else 1] += 1
                    if mine:
                        reg_gf += 1
                    else:
                        reg_ga += 1
                if mine:
                    scorers.append({"id": goal["playerId"], "name": goal["name"], "period": goal["period"]})
            gf, ga = g[side]["score"] or 0, g[other]["score"] or 0
            first = next((x["team"] for x in g["goals"] if x["type"] != "SO"), None)
            logs[team].append({
                "gameId": g["id"],
                "date": g["date"],
                "season": g["season"],
                "opp": opp,
                "home": side == "home",
                "gf": gf,
                "ga": ga,
                "regGf": reg_gf,
                "regGa": reg_ga,
                "periods": periods,
                "end": g["lastPeriodType"] or "REG",
                "win": gf > ga,
                "regWin": reg_gf > reg_ga,
                "sogF": g[side]["sog"],
                "sogA": g[other]["sog"],
                "scorers": scorers,
                "scoredFirst": None if first is None else first == team,
            })
    for team in logs:
        logs[team].sort(key=lambda r: r["date"], reverse=True)
    return logs


def build_sample(log, current_season, min_sample):
    """Current season, topped up with last season's latest games if short."""
    cur = [r for r in log if r["season"] == current_season]
    prior = [r for r in log if r["season"] < current_season]
    filler = prior[: max(0, min_sample - len(cur))]
    return cur + filler, len(cur), len(filler)


def _avg(vals):
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), ROUND) if vals else None


def _pct(flags):
    flags = list(flags)
    return round(sum(1 for f in flags if f) / len(flags), ROUND) if flags else None


def aggregate(games, legs):
    """Averages and rates over a list of team game records."""
    if not games:
        return {"gp": 0}
    total_line = legs["regulation_total_line"]
    fp_line = legs["first_period_team_line"]
    out = {
        "gp": len(games),
        "gfPg": _avg(r["gf"] for r in games),
        "gaPg": _avg(r["ga"] for r in games),
        "regTotalPg": _avg(r["regGf"] + r["regGa"] for r in games),
        "sogFPg": _avg(r["sogF"] for r in games),
        "sogAPg": _avg(r["sogA"] for r in games),
        "winPct": _pct(r["win"] for r in games),
        "regWinPct": _pct(r["regWin"] for r in games),
        "otPct": _pct(r["end"] != "REG" for r in games),
        "scored1pPct": _pct(r["periods"][0][0] > fp_line for r in games),
        "conceded1pPct": _pct(r["periods"][0][1] > fp_line for r in games),
        "overPct": _pct(r["regGf"] + r["regGa"] > total_line for r in games),
        "byPeriod": [
            {"gf": _avg(r["periods"][i][0] for r in games), "ga": _avg(r["periods"][i][1] for r in games)}
            for i in range(3)
        ],
    }
    out["gdPg"] = round(out["gfPg"] - out["gaPg"], ROUND)
    home = [r for r in games if r["home"]]
    away = [r for r in games if not r["home"]]
    out["home"] = {"gp": len(home), "gfPg": _avg(r["gf"] for r in home), "gaPg": _avg(r["ga"] for r in home)}
    out["away"] = {"gp": len(away), "gfPg": _avg(r["gf"] for r in away), "gaPg": _avg(r["ga"] for r in away)}
    return out


def leg_rates(games, legs, as_favorite):
    """How often the 3 legs hit in these games.
    as_favorite=True  -> team won / over / team scored in 1st
    as_favorite=False -> team lost / over / team conceded in 1st
    """
    total_line = legs["regulation_total_line"]
    fp_line = legs["first_period_team_line"]
    rows = []
    for r in games:
        ml = r["win"] if as_favorite else not r["win"]
        over = r["regGf"] + r["regGa"] > total_line
        fp = (r["periods"][0][0] if as_favorite else r["periods"][0][1]) > fp_line
        rows.append((ml, over, fp, ml and over and fp))
    if not rows:
        return None
    return {
        "gp": len(rows),
        "ml": _pct(x[0] for x in rows),
        "over": _pct(x[1] for x in rows),
        "firstPeriod": _pct(x[2] for x in rows),
        "all3": _pct(x[3] for x in rows),
    }


def blend(sample_val, recent_val, w):
    if sample_val is None:
        return recent_val
    if recent_val is None:
        return sample_val
    return round((1 - w) * sample_val + w * recent_val, ROUND)
