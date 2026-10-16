"""Thin client for the free NHL APIs, with an on-disk cache.

Completed days never change, so they are cached forever in .cache/.
Anything that can still change (today, future days, live stats) is
fetched fresh on every run.
"""
import json
import os
import time
import urllib.request
from urllib.error import HTTPError, URLError

WEB = "https://api-web.nhle.com/v1"
STATS = "https://api.nhle.com/stats/rest/en"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(ROOT, ".cache", "nhl")

FINAL_STATES = {"OFF", "FINAL"}
_last_request = [0.0]


def _get(url, retries=3):
    # Be polite: at most ~6 requests per second.
    wait = 0.15 - (time.time() - _last_request[0])
    if wait > 0:
        time.sleep(wait)
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "betsmaxxer/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                _last_request[0] = time.time()
                return json.loads(resp.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError) as e:
            if attempt == retries - 1:
                raise RuntimeError("NHL API request failed: %s (%s)" % (url, e))
            time.sleep(1.5 * (attempt + 1))


def _cache_path(name):
    return os.path.join(CACHE_DIR, name + ".json")


def _read_cache(name):
    path = _cache_path(name)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return None


def _write_cache(name, data):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(_cache_path(name), "w", encoding="utf-8") as f:
        json.dump(data, f)


def score_day(date_str, today_str):
    """All games on a date, with every goal and its period."""
    name = "score_" + date_str
    if date_str < today_str:
        cached = _read_cache(name)
        if cached is not None:
            return cached
    data = _get("%s/score/%s" % (WEB, date_str))
    slim = {
        "date": date_str,
        "prevDate": data.get("prevDate"),
        "games": [_slim_game(g) for g in data.get("games", [])],
    }
    all_final = all(g["state"] in FINAL_STATES for g in slim["games"])
    if date_str < today_str and all_final:
        _write_cache(name, slim)
    return slim


def _slim_game(g):
    goals = []
    for goal in g.get("goals", []) or []:
        pd = goal.get("periodDescriptor", {})
        goals.append({
            "period": pd.get("number", goal.get("period")),
            "type": pd.get("periodType", "REG"),
            "team": goal.get("teamAbbrev"),
            "playerId": goal.get("playerId"),
            "name": (goal.get("name") or {}).get("default"),
            "en": goal.get("goalModifier") == "empty-net",
            "strength": goal.get("strength"),
        })
    return {
        "id": g["id"],
        "season": g.get("season"),
        "gameType": g.get("gameType"),
        "date": g.get("gameDate"),
        "state": g.get("gameState"),
        "lastPeriodType": (g.get("gameOutcome") or {}).get("lastPeriodType"),
        "away": {"abbrev": g["awayTeam"]["abbrev"], "score": g["awayTeam"].get("score"),
                 "sog": g["awayTeam"].get("sog")},
        "home": {"abbrev": g["homeTeam"]["abbrev"], "score": g["homeTeam"].get("score"),
                 "sog": g["homeTeam"].get("sog")},
        "goals": goals,
    }


def _toi_seconds(s):
    try:
        m, sec = (s or "0:0").split(":")
        return int(m) * 60 + int(sec)
    except ValueError:
        return 0


def boxscore(game_id):
    """Per-player stats for one finished game (cached forever)."""
    name = "box_%s" % game_id
    cached = _read_cache(name)
    if cached is not None:
        return cached
    data = _get("%s/gamecenter/%s/boxscore" % (WEB, game_id))
    if data.get("gameState") not in FINAL_STATES:
        return None
    players = []
    for side in ("homeTeam", "awayTeam"):
        team = data[side]["abbrev"]
        pbg = data.get("playerByGameStats", {}).get(side, {})
        for grp in ("forwards", "defense"):
            for p in pbg.get(grp, []):
                players.append({
                    "id": p["playerId"], "name": (p.get("name") or {}).get("default"), "team": team,
                    "pos": "D" if grp == "defense" else "F",
                    "g": p.get("goals", 0), "a": p.get("assists", 0), "pts": p.get("points", 0),
                    "sog": p.get("sog", 0), "blk": p.get("blockedShots", 0), "hits": p.get("hits", 0),
                    "toi": _toi_seconds(p.get("toi")),
                })
        for p in pbg.get("goalies", []):
            players.append({
                "id": p["playerId"], "name": (p.get("name") or {}).get("default"), "team": team, "pos": "G",
                "starter": bool(p.get("starter")), "saves": p.get("saves", 0),
                "sa": p.get("shotsAgainst", 0), "ga": p.get("goalsAgainst", 0),
                "toi": _toi_seconds(p.get("toi")),
            })
    slim = {"id": game_id, "season": data.get("season"), "date": data.get("gameDate"), "players": players}
    _write_cache(name, slim)
    return slim


def schedule_day(date_str):
    """The slate for a date (includes free moneyline odds)."""
    data = _get("%s/schedule/%s" % (WEB, date_str))
    for day in data.get("gameWeek", []):
        if day.get("date") == date_str:
            return day.get("games", [])
    return []


def standings_now():
    return _get("%s/standings/now" % WEB).get("standings", [])


def team_summary(season_id):
    """PP%, PK%, shots per game, etc. for every team."""
    url = "%s/team/summary?cayenneExp=seasonId=%s%%20and%%20gameTypeId=2" % (STATS, season_id)
    return _get(url).get("data", [])


def club_stats(team_abbrev):
    """Season skater and goalie stats for one team."""
    return _get("%s/club-stats/%s/now" % (WEB, team_abbrev))
