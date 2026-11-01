"""Soccer stomp conditions. Same contract as scoring.py: (met, value_text, rule_text).
League sizes differ, so "bottom N" uses each profile's nTeams."""
from .scoring import _num, _pct


def _bottom(p, rank, n):
    return rank is not None and rank > p["nTeams"] - n


def fav_offense(fav, dog, p):
    return fav["gfRank"] <= p["top_n"], "#%d (%s goals/gm)" % (fav["gfRank"], _num(fav["gfPg"])), "top %d" % p["top_n"]


def dog_defense_leaky(fav, dog, p):
    return _bottom(dog, dog["gaRank"], p["bottom_n"]), "#%d (%s allowed/gm)" % (dog["gaRank"], _num(dog["gaPg"])), "bottom %d" % p["bottom_n"]


def dog_offense_weak(fav, dog, p):
    return _bottom(dog, dog["gfRank"], p["bottom_n"]), "#%d (%s goals/gm)" % (dog["gfRank"], _num(dog["gfPg"])), "bottom %d" % p["bottom_n"]


def fav_defense_solid(fav, dog, p):
    return fav["gaRank"] <= p["top_n"], "#%d (%s allowed/gm)" % (fav["gaRank"], _num(fav["gaPg"])), "top %d" % p["top_n"]


def goal_diff_gap(fav, dog, p):
    gap = fav["gdPg"] - dog["gdPg"]
    return gap >= p["min_gap"], "%+.2f" % gap, "≥ %+.2f" % p["min_gap"]


def points_gap(fav, dog, p):
    gap = (fav["ppg"] or 0) - (dog["ppg"] or 0)
    return gap >= p["min_gap"], "%.2f vs %.2f pts/gm" % (fav["ppg"] or 0, dog["ppg"] or 0), "gap ≥ %.1f" % p["min_gap"]


def shots_gap(fav, dog, p):
    f = (fav.get("sotFPg") or 0) - (fav.get("sotAPg") or 0)
    d = (dog.get("sotFPg") or 0) - (dog.get("sotAPg") or 0)
    return f - d >= p["min_gap"], "%+.1f on target/gm" % (f - d), "≥ %+.1f" % p["min_gap"]


def fav_home(fav, dog, p):
    return fav["isHome"], "home" if fav["isHome"] else "away", "at home"


def rest_edge(fav, dog, p):
    cap = lambda d: 7 if d is None else min(d, 7)
    edge = cap(fav["daysOff"]) - cap(dog["daysOff"])
    return edge >= p["min_days"], "%+d days" % edge, "≥ +%d days" % p["min_days"]


def market_agrees(fav, dog, p):
    o = fav.get("odds")
    met = o is not None and 1.0 / o >= p["min_prob"]
    return met, "—" if o is None else "%.2f (%s)" % (o, _pct(1.0 / o)), "fav ≥ %s to win" % _pct(p["min_prob"])


CONDITIONS = {f.__name__: f for f in [
    fav_offense, dog_defense_leaky, dog_offense_weak, fav_defense_solid, goal_diff_gap, points_gap,
    shots_gap, fav_home, rest_edge, market_agrees,
]}
