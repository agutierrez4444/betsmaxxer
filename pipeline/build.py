"""Build the NHL dashboard data.

    py -m pipeline.build                  # yesterday, today, tomorrow
    py -m pipeline.build --date 2026-10-15
    py -m pipeline.build --days-back 7    # also rebuild the last 7 days

Writes site/data/nhl/<date>.json and site/data/nhl/index.json.
Stats for a date only use games played *before* that date, so past
days show what the dashboard would have said that morning, plus the
actual result once the game is final.
"""
import argparse
import json
import os
from datetime import date, datetime, timedelta

import yaml

from . import nhl_api, nhl_trends, scoring, stats, trends

ROOT = nhl_api.ROOT
OUT_DIR = os.path.join(ROOT, "site", "data", "nhl")


def load_config():
    with open(os.path.join(ROOT, "config.yaml"), encoding="utf-8") as f:
        return yaml.safe_load(f)["nhl"]


def _odds(team, provider_id):
    if provider_id is None:
        return None
    for o in team.get("odds", []) or []:
        if o.get("providerId") == provider_id:
            try:
                return float(o["value"])
            except (TypeError, ValueError):
                return None
    return None


def _likely_goalie(club):
    goalies = sorted(club.get("goalies", []), key=lambda g: (g.get("gamesStarted") or 0), reverse=True)
    out = []
    for g in goalies:
        out.append({
            "id": g["playerId"],
            "name": "%s %s" % (g["firstName"]["default"], g["lastName"]["default"]),
            "headshot": g.get("headshot"),
            "starts": g.get("gamesStarted") or 0,
            "gp": g.get("gamesPlayed") or 0,
            "record": "%d-%d-%d" % (g.get("wins") or 0, g.get("losses") or 0, g.get("overtimeLosses") or 0),
            "savePct": round(g["savePercentage"], 3) if g.get("savePercentage") is not None else None,
            "gaa": round(g["goalsAgainstAverage"], 2) if g.get("goalsAgainstAverage") is not None else None,
            "shutouts": g.get("shutouts") or 0,
        })
    return out


def _skaters(club, recent_games):
    """Top skaters by points, with goals in the team's recent games."""
    recent_goals = {}
    recent_1p = {}
    for r in recent_games:
        for s in r["scorers"]:
            recent_goals[s["id"]] = recent_goals.get(s["id"], 0) + 1
            if s["period"] == 1:
                recent_1p[s["id"]] = recent_1p.get(s["id"], 0) + 1
    rows = []
    for p in club.get("skaters", []):
        gp = p.get("gamesPlayed") or 0
        rows.append({
            "id": p["playerId"],
            "name": "%s %s" % (p["firstName"]["default"], p["lastName"]["default"]),
            "pos": p.get("positionCode"),
            "headshot": p.get("headshot"),
            "gp": gp,
            "g": p.get("goals") or 0,
            "a": p.get("assists") or 0,
            "pts": p.get("points") or 0,
            "gpg": round((p.get("goals") or 0) / gp, 2) if gp else 0,
            "shots": p.get("shots") or 0,
            "ppg": p.get("powerPlayGoals") or 0,
            "recentG": recent_goals.get(p["playerId"], 0),
            "recent1pG": recent_1p.get(p["playerId"], 0),
        })
    rows.sort(key=lambda r: (r["pts"], r["g"], r["recentG"]), reverse=True)
    return rows[:8]


def _game_row(r):
    return {
        "date": r["date"], "opp": r["opp"], "home": r["home"], "gf": r["gf"], "ga": r["ga"],
        "end": r["end"], "win": r["win"], "periods": r["periods"],
        "regTotal": r["regGf"] + r["regGa"], "season": r["season"],
    }


def _rest(log, day_str):
    """Days off since the team's last game (0 = back-to-back) and games in the
    previous 7 days. daysOff is None for a team's first game after the summer."""
    day = stats.parse_date(day_str)
    if not log:
        return {"daysOff": None, "gamesLast7": 0}
    gap = (day - stats.parse_date(log[0]["date"])).days
    last7 = sum(1 for r in log if 0 < (day - stats.parse_date(r["date"])).days <= 7)
    return {"daysOff": gap - 1 if gap <= 30 else None, "gamesLast7": last7}


def build_day(day_str, today_str, cfg, shared):
    slate = nhl_api.schedule_day(day_str)
    slate = [g for g in slate if g.get("gameType") == 2]
    if not slate:
        print("  %s: no regular-season games" % day_str)
        return None

    current_season = slate[0]["season"]
    teams = shared["teams"]
    need = max(cfg["min_sample_games"], cfg["recent_games"])
    last_n = cfg.get("last_season_games", 41)
    games = stats.collect_games(day_str, today_str, current_season, need, teams, prior_per_team=last_n)
    logs = stats.team_logs(games)
    legs = cfg["legs"]
    w = cfg["recent_weight"]
    yesterday = (stats.parse_date(day_str) - timedelta(days=1)).isoformat()

    # ---- per-team profiles (all 32, so ranks are league-wide) ----
    profiles = {}
    for team in teams:
        log = logs.get(team, [])
        sample, n_cur, n_prior = stats.build_sample(log, current_season, cfg["min_sample_games"])
        recent = sample[: cfg["recent_games"]]
        agg_s = stats.aggregate(sample, legs)
        agg_r = stats.aggregate(recent, legs)
        prof = {"team": team, "sample": agg_s, "recent": agg_r,
                "nCurrent": n_cur, "nPrior": n_prior,
                "b2b": bool(log) and log[0]["date"] == yesterday,
                **_rest(log, day_str),
                "_sampleGames": sample, "_recentGames": recent, "_log": log}
        for k in ("gfPg", "gaPg", "gdPg", "scored1pPct", "conceded1pPct", "regTotalPg"):
            prof[k] = stats.blend(agg_s.get(k), agg_r.get(k), w)
        profiles[team] = prof

    by_gf = sorted(teams, key=lambda t: profiles[t]["gfPg"] if profiles[t]["gfPg"] is not None else -1, reverse=True)
    by_ga = sorted(teams, key=lambda t: profiles[t]["gaPg"] if profiles[t]["gaPg"] is not None else 99)
    for i, t in enumerate(by_gf):
        profiles[t]["gfRank"] = i + 1
    for i, t in enumerate(by_ga):
        profiles[t]["gaRank"] = i + 1
    ranks = {"sogA": {}, "sogF": {}}
    for key, field in (("sogA", "sogAPg"), ("sogF", "sogFPg")):
        order = sorted(teams, key=lambda t: profiles[t]["sample"].get(field) or 0, reverse=True)
        for i, t in enumerate(order):
            ranks[key][t] = i + 1

    # ---- trends: this season only, with per-player box scores ----
    logs_cur = {t: [r for r in logs.get(t, []) if r["season"] == current_season] for t in teams}
    slate_teams = {g["homeTeam"]["abbrev"] for g in slate} | {g["awayTeam"]["abbrev"] for g in slate}
    box_ids = sorted({r["gameId"] for t in slate_teams for r in logs_cur[t]})
    boxes = nhl_trends.load_boxes(box_ids)
    p_by_team, p_flat = nhl_trends.player_rows(boxes, logs_cur)
    league_out = trends.league_outcomes(logs_cur)
    base_cache = {}
    dists = {}

    # ---- each game on the slate ----
    out_games = []
    results = {}
    try:
        for g in nhl_api.score_day(day_str, today_str)["games"]:
            results[g["id"]] = g
    except RuntimeError:
        pass

    for g in slate:
        home, away = g["homeTeam"]["abbrev"], g["awayTeam"]["abbrev"]
        side = {}
        for abbrev, tm, is_home in ((home, g["homeTeam"], True), (away, g["awayTeam"], False)):
            prof = dict(profiles[abbrev])
            prof["isHome"] = is_home
            club = shared["club"].get(abbrev)
            if club is None:
                club = nhl_api.club_stats(abbrev)
                shared["club"][abbrev] = club
            goalies = _likely_goalie(club)
            prof["goalie"] = goalies[0] if goalies else None
            prof["_goalies"] = goalies
            prof["_skaters"] = _skaters(club, prof["_recentGames"])
            prof["_odds"] = _odds(tm, cfg.get("odds_provider_id"))
            prof["_meta"] = tm
            side[abbrev] = prof
            for pl in club.get("skaters", []) + club.get("goalies", []):
                shared["names"][pl["playerId"]] = {
                    "name": "%s %s" % (pl["firstName"]["default"], pl["lastName"]["default"]),
                    "headshot": pl.get("headshot"),
                }

        s_home, d_home = scoring.score_side(side[home], side[away], cfg["conditions"])
        s_away, d_away = scoring.score_side(side[away], side[home], cfg["conditions"])
        fav, dog = (home, away) if s_home >= s_away else (away, home)
        score, details = (s_home, d_home) if fav == home else (s_away, d_away)

        game = {
            "id": g["id"],
            "startTimeUTC": g["startTimeUTC"],
            "venue": (g.get("venue") or {}).get("default"),
            "home": home,
            "away": away,
            "stomp": {"score": score, "fav": fav, "dog": dog, "conditions": details, "flag": None,
                      "otherSideScore": s_away if fav == home else s_home},
            "teams": {},
            "h2h": [],
            "result": None,
        }
        for abbrev in (home, away):
            p = side[abbrev]
            meta = p["_meta"]
            is_fav = abbrev == fav
            st = shared["standings"].get(abbrev, {})
            summ = shared["summary"].get(abbrev, {})
            game["teams"][abbrev] = {
                "abbrev": abbrev,
                "name": "%s %s" % (meta["placeName"]["default"], meta["commonName"]["default"]),
                "short": meta["commonName"]["default"],
                "logo": meta.get("logo"),
                "darkLogo": meta.get("darkLogo"),
                "odds": p["_odds"],
                "record": st.get("record"),
                "streak": st.get("streak"),
                "points": st.get("points"),
                "ppPct": summ.get("ppPct"),
                "pkPct": summ.get("pkPct"),
                "b2b": p["b2b"],
                "daysOff": p["daysOff"],
                "gamesLast7": p["gamesLast7"],
                "nCurrent": p["nCurrent"],
                "nPrior": p["nPrior"],
                "gfRank": p["gfRank"],
                "gaRank": p["gaRank"],
                "blended": {k: p[k] for k in ("gfPg", "gaPg", "gdPg", "scored1pPct", "conceded1pPct", "regTotalPg")},
                "sample": p["sample"],
                "recent": p["recent"],
                "legs": {
                    "role": "favorite" if is_fav else "underdog",
                    "sample": stats.leg_rates(p["_sampleGames"], legs, is_fav),
                    "recent": stats.leg_rates(p["_recentGames"], legs, is_fav),
                },
                "lastGames": [_game_row(r) for r in p["_log"][:10]],
                # compact games for the bet builder, newest first: all of this season,
                # then last season's final `last_season_games` (marked ls=1)
                "log": [{"h": r["home"], "gf": r["gf"], "ga": r["ga"], "p": r["periods"], "e": r["end"],
                         "ls": 1 if r["season"] < current_season else 0}
                        for r in ([x for x in p["_log"] if x["season"] == current_season] +
                                  [x for x in p["_log"] if x["season"] < current_season][:last_n])],
                "skaters": p["_skaters"],
                "players": nhl_trends.export_players(p_by_team, abbrev, shared["names"], current_season),
                "goalies": p["_goalies"][:3],
            }
        game["trends"] = nhl_trends.build(
            game, {home: "home", away: "away"}, logs_cur, profiles, p_by_team, p_flat, shared["names"],
            cfg, ranks, current_season, base_cache, league_out, dists)
        h2h = [r for r in side[home]["_log"] if r["opp"] == away][:5]
        game["h2h"] = [_game_row(r) for r in h2h]

        res = results.get(g["id"])
        if res and res["state"] in nhl_api.FINAL_STATES:
            game["result"] = _result_for(res, fav, legs)
            game["result"]["box"] = nhl_trends.result_box(g["id"])
        elif res:
            game["state"] = res["state"]
        out_games.append(game)

    scoring.assign_flags(out_games, cfg)
    out_games.sort(key=lambda x: (x["stomp"]["flag"] is None, -x["stomp"]["score"], x["startTimeUTC"]))

    # League-average goals per team per period, used to steady small samples in the simulation.
    league = [stats._avg(profiles[t]["sample"]["byPeriod"][i]["gf"] for t in teams if profiles[t]["sample"].get("gp"))
              for i in range(3)]
    return {
        "date": day_str,
        "sport": "nhl",
        "season": current_season,
        "generatedAt": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "settings": {
            "recentGames": cfg["recent_games"],
            "minSampleGames": cfg["min_sample_games"],
            "recentWeight": w,
            "legs": legs,
            "tiers": cfg["flag_tiers"],
            "simulation": cfg.get("simulation", {}),
            "builder": cfg.get("builder", {}),
            "leaguePeriodGf": league,
            "trends": cfg.get("trends", {}),
            "propStats": nhl_trends.PROP_STATS,
        },
        "trendDist": dists,
        "games": out_games,
    }


def _result_for(res, fav, legs):
    log = stats.team_logs([res])[fav][0]
    ml = log["win"]
    over = log["regGf"] + log["regGa"] > legs["regulation_total_line"]
    fp = log["periods"][0][0] > legs["first_period_team_line"]
    home_log = stats.team_logs([res])[res["home"]["abbrev"]][0]
    return {
        "homeScore": res["home"]["score"], "awayScore": res["away"]["score"],
        "end": res["lastPeriodType"] or "REG",
        "periods": home_log["periods"],  # [[home, away], ...] for periods 1-3
        "legs": {"ml": ml, "over": over, "firstPeriod": fp, "all3": ml and over and fp},
        "regTotal": log["regGf"] + log["regGa"],
    }


def load_shared():
    print("Fetching standings, team summary...")
    standings = nhl_api.standings_now()
    teams = sorted(s["teamAbbrev"]["default"] for s in standings)
    st = {}
    season_id = None
    for s in standings:
        a = s["teamAbbrev"]["default"]
        season_id = s.get("seasonId", season_id)
        st[a] = {
            "record": "%d-%d-%d" % (s["wins"], s["losses"], s["otLosses"]),
            "points": s["points"],
            "streak": "%s%s" % (s.get("streakCode", ""), s.get("streakCount", "")),
        }
    summary = {}
    if season_id:
        for row in nhl_api.team_summary(season_id):
            summary[row["teamFullName"]] = row
    by_abbrev = {}
    for s in standings:
        full = "%s %s" % (s["placeName"]["default"], s["teamCommonName"]["default"])
        name = s.get("teamName", {}).get("default", full)
        row = summary.get(name) or summary.get(full)
        if row:
            by_abbrev[s["teamAbbrev"]["default"]] = {
                "ppPct": row.get("powerPlayPct"), "pkPct": row.get("penaltyKillPct"),
            }
    return {"teams": teams, "standings": st, "summary": by_abbrev, "club": {}, "names": {}}


def write_index():
    days = sorted(f[:-5] for f in os.listdir(OUT_DIR) if f.endswith(".json") and f != "index.json")
    summary = []
    for d in days:
        with open(os.path.join(OUT_DIR, d + ".json"), encoding="utf-8") as f:
            data = json.load(f)
        flagged_final = [g["result"]["legs"] for g in data["games"] if g["stomp"]["flag"] and g.get("result")]
        summary.append({
            "date": d,
            "games": len(data["games"]),
            "flagged": sum(1 for g in data["games"] if g["stomp"]["flag"]),
            # how the flagged games actually did (only once they're final)
            "flaggedFinal": len(flagged_final),
            "hits": {k: sum(1 for l in flagged_final if l[k]) for k in ("ml", "over", "firstPeriod", "all3")},
        })
    with open(os.path.join(OUT_DIR, "index.json"), "w", encoding="utf-8") as f:
        json.dump({"days": summary, "updatedAt": datetime.utcnow().replace(microsecond=0).isoformat() + "Z"}, f)


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
    shared = load_shared()
    for d in days:
        print("Building %s..." % d)
        data = build_day(d, today_str, cfg, shared)
        if data is None:
            continue
        with open(os.path.join(OUT_DIR, d + ".json"), "w", encoding="utf-8") as f:
            json.dump(data, f, separators=(",", ":"))
        flagged = [g for g in data["games"] if g["stomp"]["flag"]]
        print("  %d games, %d flagged: %s" % (
            len(data["games"]), len(flagged),
            ", ".join("%s vs %s (%d, %s)" % (g["stomp"]["fav"], g["stomp"]["dog"], g["stomp"]["score"], g["stomp"]["flag"])
                      for g in flagged)))
    write_index()
    print("Done. Open the dashboard with:  py -m http.server 8000 -d site")


if __name__ == "__main__":
    main()
