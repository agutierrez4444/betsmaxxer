"""NBA stomp conditions. Same contract as scoring.py: (met, value_text, rule_text)."""
from .scoring import _num, _pct

N_TEAMS = 30


def _bottom(rank, n):
    return rank is not None and rank > N_TEAMS - n


def fav_offense(fav, dog, p):
    return fav["gfRank"] <= p["top_n"], "#%d (%s pts/gm)" % (fav["gfRank"], _num(fav["gfPg"], 1)), "top %d" % p["top_n"]


def dog_defense_leaky(fav, dog, p):
    return _bottom(dog["gaRank"], p["bottom_n"]), "#%d (%s pts allowed)" % (dog["gaRank"], _num(dog["gaPg"], 1)), "bottom %d" % p["bottom_n"]


def dog_offense_weak(fav, dog, p):
    return _bottom(dog["gfRank"], p["bottom_n"]), "#%d (%s pts/gm)" % (dog["gfRank"], _num(dog["gfPg"], 1)), "bottom %d" % p["bottom_n"]


def fav_defense_solid(fav, dog, p):
    return fav["gaRank"] <= p["top_n"], "#%d (%s pts allowed)" % (fav["gaRank"], _num(fav["gaPg"], 1)), "top %d" % p["top_n"]


def point_diff_gap(fav, dog, p):
    gap = fav["gdPg"] - dog["gdPg"]
    return gap >= p["min_gap"], "%+.1f" % gap, "≥ %+.1f" % p["min_gap"]


def win_pct_gap(fav, dog, p):
    a, b = fav.get("winPct"), dog.get("winPct")
    if a is None or b is None:
        return False, "—", "≥ %s" % _pct(p["min_gap"])
    return a - b >= p["min_gap"], "%s vs %s" % (_pct(a), _pct(b)), "gap ≥ %s" % _pct(p["min_gap"])


def fav_home(fav, dog, p):
    return fav["isHome"], "home" if fav["isHome"] else "away", "at home"


def dog_back_to_back(fav, dog, p):
    return dog["daysOff"] == 0, "back-to-back" if dog["daysOff"] == 0 else "rested", "dog played yesterday"


def fav_back_to_back(fav, dog, p):
    return fav["daysOff"] == 0, "back-to-back" if fav["daysOff"] == 0 else "rested", "fav played yesterday"


def rest_edge(fav, dog, p):
    cap = lambda d: 3 if d is None else min(d, 3)
    edge = cap(fav["daysOff"]) - cap(dog["daysOff"])
    return edge >= p["min_days"], "%+d days" % edge, "≥ +%d days" % p["min_days"]


def market_agrees(fav, dog, p):
    s = fav.get("spread")  # negative = favored
    met = s is not None and -s >= p["min_spread"]
    return met, "—" if s is None else "%+.1f" % s, "fav by %.1f+" % p["min_spread"]


def dog_star_out(fav, dog, p):
    out = dog.get("starsOut") or []
    return bool(out), ", ".join(out) or "none", "a top-%d scorer is out" % p["top_n"]


def fav_star_out(fav, dog, p):
    out = fav.get("starsOut") or []
    return bool(out), ", ".join(out) or "none", "a top-%d scorer is out" % p["top_n"]


CONDITIONS = {f.__name__: f for f in [
    fav_offense, dog_defense_leaky, dog_offense_weak, fav_defense_solid, point_diff_gap, win_pct_gap,
    fav_home, dog_back_to_back, fav_back_to_back, rest_edge, market_agrees, dog_star_out, fav_star_out,
]}
