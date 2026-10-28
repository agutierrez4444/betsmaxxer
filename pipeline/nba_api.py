"""NBA data from ESPN's public site API (free, no key):
  * scoreboard/<date>  - every game that day: quarter scores, team box, odds
  * summary/<event>    - player box scores and the pre-game line
  * injuries           - current league-wide injury report

Finished days and games are cached forever in .cache/nba/ (slimmed down);
anything that can still change is re-downloaded on each build.
"""
import json
import os
import time
import urllib.request
from urllib.error import HTTPError, URLError

from .nhl_api import ROOT

CACHE_DIR = os.path.join(ROOT, ".cache", "nba")
BASE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/"
THROTTLE = 0.15
_last = [0.0]


def _get(url, retries=3):
    for attempt in range(retries):
        wait = THROTTLE - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "betsmaxxer/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, ValueError) as e:
            if attempt == retries - 1:
                raise RuntimeError("NBA download failed: %s (%s)" % (url, e))
            time.sleep(2 * (attempt + 1))


def _cache_path(kind, key):
    d = os.path.join(CACHE_DIR, kind)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "%s.json" % key)


def _read(path):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return None


def _write(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"))


def _stat(stats, name):
    for s in stats or []:
        if s.get("name") == name:
            try:
                return float(s.get("displayValue"))
            except (TypeError, ValueError):
                return None
    return None


def _odds(o):
    """DraftKings line from a scoreboard/pickcenter odds entry, home perspective."""
    if not o:
        return None
    home, away = o.get("homeTeamOdds") or {}, o.get("awayTeamOdds") or {}
    spread = o.get("spread")
    if spread is not None:
        spread = -abs(spread) if home.get("favorite") else abs(spread)  # home spread
    ml = o.get("moneyline") or {}

    def ml_of(side, d):
        if d.get("moneyLine") is not None:
            return d["moneyLine"]
        x = ((ml.get(side) or {}).get("close") or {}).get("odds")
        try:
            return int(str(x).replace("+", ""))
        except (TypeError, ValueError):
            return None
    return {"spread": spread, "total": o.get("overUnder"), "homeML": ml_of("home", home), "awayML": ml_of("away", away)}


def scoreboard(day_str, today_str):
    """Slim list of the day's games. Past days are cached once every game is final."""
    path = _cache_path("days", day_str)
    cached = _read(path)
    if cached is not None:
        return cached
    data = _get(BASE + "scoreboard?dates=" + day_str.replace("-", ""))
    games = []
    for ev in data.get("events", []):
        comp = ev["competitions"][0]
        st = comp.get("status", {}).get("type", {})
        teams = {}
        for c in comp["competitors"]:
            t = c["team"]
            teams[c["homeAway"]] = {
                "abbr": t.get("abbreviation"), "name": t.get("displayName"), "short": t.get("shortDisplayName") or t.get("name"),
                "id": t.get("id"), "score": int(float(c.get("score") or 0)),
                "periods": [int(l.get("value", 0)) for l in c.get("linescores", [])],
                "record": next((r.get("summary") for r in c.get("records", []) if r.get("type") == "total"), None),
                "fga": _stat(c.get("statistics"), "fieldGoalsAttempted"), "fgm": _stat(c.get("statistics"), "fieldGoalsMade"),
                "fg3a": _stat(c.get("statistics"), "threePointFieldGoalsAttempted"), "fg3m": _stat(c.get("statistics"), "threePointFieldGoalsMade"),
                "fta": _stat(c.get("statistics"), "freeThrowsAttempted"), "reb": _stat(c.get("statistics"), "rebounds"),
                "ast": _stat(c.get("statistics"), "assists"),
            }
        games.append({
            "id": ev["id"], "date": day_str, "start": ev.get("date"), "season": ev.get("season", {}).get("year"),
            "type": ev.get("season", {}).get("type"), "state": st.get("state"), "final": bool(st.get("completed")),
            "venue": (comp.get("venue") or {}).get("fullName"), "neutral": comp.get("neutralSite", False),
            "home": teams.get("home"), "away": teams.get("away"),
            "lines": _odds((comp.get("odds") or [None])[0]),
        })
    if day_str < today_str and all(g["final"] or g["state"] == "post" for g in games):
        _write(path, games)
    return games


def summary(event_id):
    """Player box score + pre-game line for one game (cached once final)."""
    path = _cache_path("games", event_id)
    cached = _read(path)
    if cached is not None:
        return cached
    d = _get(BASE + "summary?event=%s" % event_id)
    comp = (d.get("header") or {}).get("competitions", [{}])[0]
    final = bool(comp.get("status", {}).get("type", {}).get("completed"))
    players = []
    for team in (d.get("boxscore") or {}).get("players", []):
        abbr = team["team"]["abbreviation"]
        for block in team.get("statistics", []):
            keys = [k.upper() for k in (block.get("names") or block.get("keys") or [])]
            for a in block.get("athletes", []):
                if a.get("didNotPlay") or not a.get("stats"):
                    continue
                row = dict(zip(keys, a["stats"]))
                ath = a.get("athlete", {})

                def num(k):
                    try:
                        return int(str(row.get(k, "0")).split("-")[0].replace("+", ""))
                    except ValueError:
                        return 0
                players.append({
                    "id": ath.get("id"), "name": ath.get("displayName"), "team": abbr,
                    "pos": ((ath.get("position") or {}).get("abbreviation") or "F")[:1],
                    "headshot": (ath.get("headshot") or {}).get("href"), "starter": bool(a.get("starter")),
                    "min": num("MIN"), "pts": num("PTS"), "reb": num("REB"), "ast": num("AST"),
                    "fg3m": num("3PT"), "stl": num("STL"), "blk": num("BLK"), "tov": num("TO"),
                })
    out = {"id": event_id, "final": final, "lines": _odds((d.get("pickcenter") or [None])[0]), "players": players}
    if final:
        _write(path, out)
    return out


def injuries():
    """{team_abbr: [{"name", "status", "comment", "date"}]} — today's report."""
    try:
        d = _get(BASE + "injuries")
    except RuntimeError as e:
        print("  ! injuries: %s" % e)
        return {}
    out = {}
    for team in d.get("injuries", []):
        for x in team.get("injuries", []):
            ath = x.get("athlete") or {}
            abbr = ((ath.get("team") or {}).get("abbreviation"))
            if not abbr:
                continue
            out.setdefault(abbr, []).append({
                "name": ath.get("displayName"), "pos": (ath.get("position") or {}).get("abbreviation"),
                "status": x.get("status"), "comment": x.get("shortComment"), "date": (x.get("date") or "")[:10],
            })
    return out
