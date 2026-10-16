"""NFL stomp conditions. Same contract as scoring.py: (met, value_text, rule_text)."""
from .scoring import _bottom, _num

N_TEAMS = 32


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


def yards_gap(fav, dog, p):
    gap = (fav["ydsFPg"] - fav["ydsAPg"]) - (dog["ydsFPg"] - dog["ydsAPg"])
    return gap >= p["min_gap"], "%+.0f yds" % gap, "≥ %+.0f" % p["min_gap"]


def turnover_gap(fav, dog, p):
    gap = (fav["toAPg"] - fav["toPg"]) - (dog["toAPg"] - dog["toPg"])
    return gap >= p["min_gap"], "%+.1f/gm" % gap, "≥ %+.1f" % p["min_gap"]


def fav_home(fav, dog, p):
    return fav["isHome"], "home" if fav["isHome"] else "away", "at home"


def rest_edge(fav, dog, p):
    edge = (fav["rest"] or 7) - (dog["rest"] or 7)
    return edge >= p["min_days"], "%+d days" % edge, "≥ +%d days" % p["min_days"]


def dog_short_week(fav, dog, p):
    return (dog["rest"] or 7) <= p["max_rest"], "%s days" % (dog["rest"] or "?"), "≤ %d days rest" % p["max_rest"]


def market_agrees(fav, dog, p):
    s = fav.get("spread")  # negative = favored
    met = s is not None and -s >= p["min_spread"]
    return met, "—" if s is None else "%+.1f" % s, "fav by %.1f+" % p["min_spread"]


CONDITIONS = {f.__name__: f for f in [
    fav_offense, dog_defense_leaky, dog_offense_weak, fav_defense_solid, point_diff_gap,
    yards_gap, turnover_gap, fav_home, rest_edge, dog_short_week, market_agrees,
]}
