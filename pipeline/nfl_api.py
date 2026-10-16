"""NFL data: nflverse (schedules with betting lines, weekly player/team stats)
and ESPN (quarter-by-quarter scores, logos). All free, no keys.

Files that can still change are re-downloaded when older than REFRESH_HOURS;
completed seasons and finished weeks are cached forever in .cache/nfl/.
"""
import csv
import io
import json
import os
import time
import urllib.request
from urllib.error import HTTPError, URLError

from .nhl_api import ROOT

CACHE_DIR = os.path.join(ROOT, ".cache", "nfl")
REFRESH_HOURS = 3

GAMES_URL = "https://github.com/nflverse/nfldata/raw/master/data/games.csv"
PLAYER_URL = "https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_%d.csv"
TEAM_URL = "https://github.com/nflverse/nflverse-data/releases/download/stats_team/stats_team_week_%d.csv"
ESPN_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?seasontype=2&week=%d&dates=%d"


def _download(url, retries=3):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "betsmaxxer/1.0"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read()
        except (HTTPError, URLError, TimeoutError) as e:
            if attempt == retries - 1:
                raise RuntimeError("NFL download failed: %s (%s)" % (url, e))
            time.sleep(2 * (attempt + 1))


def _cached_file(name, url, forever=False):
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, name)
    fresh = os.path.exists(path) and (forever or time.time() - os.path.getmtime(path) < REFRESH_HOURS * 3600)
    if not fresh:
        try:
            data = _download(url)
            with open(path, "wb") as f:
                f.write(data)
        except RuntimeError:
            if not os.path.exists(path):
                raise
            print("  ! using cached %s (download failed)" % name)
    with open(path, "rb") as f:
        return f.read()


def _csv(raw):
    return list(csv.DictReader(io.StringIO(raw.decode("utf-8"))))


def games():
    """Every NFL game since 1999 with scores and closing betting lines."""
    return _csv(_cached_file("games.csv", GAMES_URL))


def player_weeks(season, current):
    return _csv(_cached_file("players_%d.csv" % season, PLAYER_URL % season, forever=season < current))


def team_weeks(season, current):
    return _csv(_cached_file("teams_%d.csv" % season, TEAM_URL % season, forever=season < current))


def espn_week(season, week):
    """{espn_event_id: {"quarters": {abbr: [q1..q4]}, "teams": {...}, "final": bool}}"""
    name = os.path.join(CACHE_DIR, "espn_%d_%02d.json" % (season, week))
    if os.path.exists(name):
        with open(name, encoding="utf-8") as f:
            return json.load(f)
    data = json.loads(_download(ESPN_URL % (week, season)).decode("utf-8"))
    out = {}
    all_final = True
    for ev in data.get("events", []):
        comp = ev["competitions"][0]
        final = comp.get("status", {}).get("type", {}).get("completed", False)
        all_final = all_final and final
        teams = {}
        for c in comp["competitors"]:
            t = c["team"]
            teams[c["homeAway"]] = {
                "abbr": t.get("abbreviation"), "name": t.get("displayName"), "short": t.get("shortDisplayName") or t.get("name"),
                "logo": t.get("logo"), "color": t.get("color"),
                "quarters": [int(l.get("value", 0)) for l in c.get("linescores", [])],
                "record": next((r.get("summary") for r in c.get("records", []) if r.get("type") == "total"), None),
            }
        out[ev["id"]] = {"final": final, "teams": teams, "venue": (comp.get("venue") or {}).get("fullName")}
    if all_final and out:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(name, "w", encoding="utf-8") as f:
            json.dump(out, f)
    return out
