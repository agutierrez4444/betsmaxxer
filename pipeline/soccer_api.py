"""Soccer data from ESPN's public site API (free, no key), for any league ESPN
covers (eng.1, esp.1, chi.1, uefa.champions, ...):
  * scoreboard/<date> - the day's matches: score, goal minutes, cards, team stats, odds
                        (also the season's calendar of match days)
  * summary/<event>   - player stats (lineups) and the pre-match line

Finished match days and matches are cached forever in .cache/soccer/<league>/.
"""
import os

from .nba_api import _get, _read, _write
from .nhl_api import ROOT

CACHE_DIR = os.path.join(ROOT, ".cache", "soccer")
BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer/%s/"


def _path(league, kind, key):
    d = os.path.join(CACHE_DIR, league, kind)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "%s.json" % key)


def _num(stats, name):
    for s in stats or []:
        if s.get("name") == name:
            try:
                return float(s.get("displayValue"))
            except (TypeError, ValueError):
                return None
    return None


def _minute(clock):
    """("45'+2'") -> (45, 2). Unknown clocks count as minute 0."""
    txt = (clock or "").replace("'", "")
    base, _, extra = txt.partition("+")
    try:
        return int(base), int(extra or 0)
    except ValueError:
        return 0, 0


def _lines(o):
    """DraftKings 3-way moneyline + total, from a scoreboard or pickcenter odds entry."""
    if not o:
        return None
    def ml(k, side):
        if (o.get(k) or {}).get("moneyLine") is not None:
            return o[k]["moneyLine"]
        x = (((o.get("moneyline") or {}).get(side) or {}).get("close") or {}).get("odds")  # scoreboard shape
        try:
            return int(str(x).replace("+", ""))
        except (TypeError, ValueError):
            return None
    out = {"homeML": ml("homeTeamOdds", "home"), "awayML": ml("awayTeamOdds", "away"), "drawML": ml("drawOdds", "draw"),
           "total": o.get("overUnder")}
    return out if any(v is not None for v in out.values()) else None


def _events(details, id_to_side, score):
    """Goals and cards with the half they happened in. ESPN credits own goals
    inconsistently, so own goals are flipped if that makes the tally match the score."""
    goals, cards = [], []
    for d in details or []:
        side = id_to_side.get((d.get("team") or {}).get("id"))
        if not side:
            continue
        m, extra = _minute((d.get("clock") or {}).get("displayValue"))
        half = 1 if m <= 45 else 2 if m <= 90 else 3  # 3 = extra time
        text = (d.get("type") or {}).get("text") or ""
        who = [a.get("displayName") for a in d.get("athletesInvolved") or []]
        if d.get("scoringPlay") and "Shootout" not in text:
            goals.append({"side": side, "min": m, "x": extra, "half": half, "og": bool(d.get("ownGoal")),
                          "pen": bool(d.get("penaltyKick")), "by": who[0] if who else None})
        elif "Card" in text:
            cards.append({"side": side, "half": half, "red": "Red" in text})
    tally = lambda gs: {s: sum(1 for g in gs if g["side"] == s) for s in ("home", "away")}
    if tally(goals) != score and any(g["og"] for g in goals):
        flipped = [dict(g, side="away" if g["side"] == "home" else "home") if g["og"] else g for g in goals]
        if tally(flipped) == score:
            goals = flipped
    goals.sort(key=lambda g: (g["min"], g["x"]))
    return goals, cards


def scoreboard(league, day_str, today_str):
    """{"games": [...], "calendar": [dates], "season": {...}, "league": {...}}"""
    path = _path(league, "days", day_str)
    cached = _read(path)
    if cached is not None:
        return cached
    data = _get(BASE % league + "scoreboard?dates=" + day_str.replace("-", ""))
    lg = (data.get("leagues") or [{}])[0]
    season = lg.get("season") or {}
    games = []
    for ev in data.get("events", []):
        comp = ev["competitions"][0]
        st = comp.get("status", {}).get("type", {})
        teams, id_to_side = {}, {}
        for c in comp["competitors"]:
            t = c["team"]
            id_to_side[t.get("id")] = c["homeAway"]
            s = c.get("statistics")
            teams[c["homeAway"]] = {
                "abbr": t.get("abbreviation"), "id": t.get("id"), "name": t.get("displayName"),
                "short": t.get("shortDisplayName") or t.get("name"), "logo": t.get("logo"),
                "score": int(float(c.get("score") or 0)), "form": c.get("form"),
                "record": next((r.get("summary") for r in c.get("records", []) if r.get("type") == "total"), None),
                "shots": _num(s, "totalShots"), "sot": _num(s, "shotsOnTarget"), "corners": _num(s, "wonCorners"),
                "poss": _num(s, "possessionPct"), "fouls": _num(s, "foulsCommitted"),
            }
        if "home" not in teams or "away" not in teams:
            continue
        final = bool(st.get("completed"))
        goals, cards = _events(comp.get("details"), id_to_side,
                               {"home": teams["home"]["score"], "away": teams["away"]["score"]}) if final else ([], [])
        games.append({
            "id": ev["id"], "date": day_str, "start": ev.get("date"), "season": (ev.get("season") or {}).get("year"),
            "stage": (ev.get("season") or {}).get("slug"), "state": st.get("state"), "final": final,
            "detail": st.get("shortDetail"), "venue": (comp.get("venue") or {}).get("fullName"),
            "home": teams["home"], "away": teams["away"], "goals": goals, "cards": cards,
            "lines": _lines((comp.get("odds") or [None])[0]),
        })
    out = {
        "games": games,
        "calendar": sorted({str(x)[:10] for x in lg.get("calendar") or [] if isinstance(x, str)}),
        "season": {"year": season.get("year"), "start": (season.get("startDate") or "")[:10], "end": (season.get("endDate") or "")[:10]},
        "league": {"name": lg.get("name"), "abbr": lg.get("abbreviation"),
                   "logo": ((lg.get("logos") or [{}])[0]).get("href")},
    }
    if day_str < today_str and all(g["final"] or g["state"] == "post" for g in games):
        _write(path, out)
    return out


def _pos(slot):
    s = (slot or "").upper()
    if s.startswith("G"):
        return "G"
    if "CD" in s or s in ("D", "LB", "RB", "LWB", "RWB", "SW") or s.startswith("CB"):
        return "D"
    if "M" in s:
        return "M"
    return "F"


def summary(league, event_id):
    """Starters' stats + pre-match line for one match (cached once final)."""
    path = _path(league, "games", event_id)
    cached = _read(path)
    if cached is not None:
        return cached
    d = _get(BASE % league + "summary?event=%s" % event_id)
    comp = (d.get("header") or {}).get("competitions", [{}])[0]
    final = bool(comp.get("status", {}).get("type", {}).get("completed"))
    players = []
    for r in d.get("rosters") or []:
        abbr = (r.get("team") or {}).get("abbreviation")
        for p in r.get("roster") or []:
            s = {x.get("name"): x.get("value") for x in p.get("stats") or []}
            if not s.get("appearances"):
                continue
            ath = p.get("athlete") or {}
            v = lambda k: int(s.get(k) or 0)
            players.append({
                "id": ath.get("id"), "name": ath.get("displayName"), "team": abbr,
                "pos": _pos((p.get("position") or {}).get("abbreviation")) if p.get("starter") else "S",
                "headshot": (ath.get("headshot") or {}).get("href"), "starter": bool(p.get("starter")),
                "g": v("totalGoals"), "a": v("goalAssists"), "shots": v("totalShots"), "sot": v("shotsOnTarget"),
                "saves": v("saves"), "yc": v("yellowCards"), "fouls": v("foulsCommitted"),
            })
    out = {"id": event_id, "final": final, "lines": _lines((d.get("pickcenter") or [None])[0]), "players": players}
    if final:
        _write(path, out)
    return out

