"""Stomp score: config-driven conditions + tiered flagging.

Every condition is a small function that looks at the favorite (fav)
and underdog (dog) profiles and returns (met, value_text, rule_text).
Weights and thresholds all come from config.yaml.
"""

N_TEAMS = 32


def _pct(x):
    return "—" if x is None else "%d%%" % round(x * 100)


def _num(x, d=2):
    return "—" if x is None else ("%." + str(d) + "f") % x


def _bottom(rank, n):
    return rank is not None and rank > N_TEAMS - n


def fav_offense(fav, dog, p):
    return fav["gfRank"] <= p["top_n"], "#%d (%s GF/gm)" % (fav["gfRank"], _num(fav["gfPg"])), "top %d" % p["top_n"]


def dog_defense_leaky(fav, dog, p):
    return _bottom(dog["gaRank"], p["bottom_n"]), "#%d (%s GA/gm)" % (dog["gaRank"], _num(dog["gaPg"])), "bottom %d" % p["bottom_n"]


def dog_offense_weak(fav, dog, p):
    return _bottom(dog["gfRank"], p["bottom_n"]), "#%d (%s GF/gm)" % (dog["gfRank"], _num(dog["gfPg"])), "bottom %d" % p["bottom_n"]


def fav_defense_solid(fav, dog, p):
    return fav["gaRank"] <= p["top_n"], "#%d (%s GA/gm)" % (fav["gaRank"], _num(fav["gaPg"])), "top %d" % p["top_n"]


def goal_diff_gap(fav, dog, p):
    gap = fav["gdPg"] - dog["gdPg"]
    return gap >= p["min_gap"], "%+.2f" % gap, "≥ %+.2f" % p["min_gap"]


def fav_1p_scoring(fav, dog, p):
    v = fav["scored1pPct"]
    return v is not None and v >= p["min_pct"], _pct(v), "≥ " + _pct(p["min_pct"])


def dog_1p_leaky(fav, dog, p):
    v = dog["conceded1pPct"]
    return v is not None and v >= p["min_pct"], _pct(v), "≥ " + _pct(p["min_pct"])


def projected_total(fav, dog, p):
    v = (fav["gfPg"] + dog["gaPg"]) / 2 + (dog["gfPg"] + fav["gaPg"]) / 2
    return v >= p["min_goals"], _num(v, 1) + " goals", "≥ %.1f" % p["min_goals"]


def fav_home(fav, dog, p):
    return fav["isHome"], "home" if fav["isHome"] else "away", "at home"


def dog_back_to_back(fav, dog, p):
    return dog["b2b"], "yes" if dog["b2b"] else "no", "played yesterday"


def fav_back_to_back(fav, dog, p):
    return fav["b2b"], "yes" if fav["b2b"] else "no", "played yesterday"


def rest_edge(fav, dog, p):
    # A team coming off the summer counts as fully rested.
    f = fav.get("daysOff")
    d = dog.get("daysOff")
    f = 3 if f is None else min(f, 3)
    d = 3 if d is None else min(d, 3)
    edge = f - d
    return edge >= p["min_days"], "%+d day%s" % (edge, "" if abs(edge) == 1 else "s"), "≥ +%d days" % p["min_days"]


def dog_goalie_hot(fav, dog, p):
    g = dog.get("goalie")
    if not g or g.get("savePct") is None:
        return False, "unknown", "sv% ≥ %.3f" % p["min_save_pct"]
    met = g["savePct"] >= p["min_save_pct"] and (g.get("starts") or 0) >= p.get("min_starts", 0)
    return met, "%s %.3f (%d GS)" % (g["name"], g["savePct"], g.get("starts") or 0), "sv% ≥ " + ("%.3f" % p["min_save_pct"])


CONDITIONS = {f.__name__: f for f in [
    fav_offense, dog_defense_leaky, dog_offense_weak, fav_defense_solid, goal_diff_gap,
    fav_1p_scoring, dog_1p_leaky, projected_total, fav_home, dog_back_to_back,
    fav_back_to_back, rest_edge, dog_goalie_hot,
]}


def score_side(fav, dog, cond_cfg, registry=None):
    """Score one orientation (fav vs dog). Returns (score 0-100, details).
    `registry` maps condition names to functions (defaults to the NHL set)."""
    registry = registry or CONDITIONS
    max_points = sum(c["weight"] for c in cond_cfg.values() if c.get("enabled", True) and c["weight"] > 0)
    points = 0
    details = []
    for key, c in cond_cfg.items():
        if not c.get("enabled", True):
            continue
        fn = registry.get(key)
        if fn is None:
            print("  ! config: unknown condition '%s' (skipped)" % key)
            continue
        met, value, rule = fn(fav, dog, c)
        if met:
            points += c["weight"]
        details.append({
            "key": key,
            "label": c.get("label", key),
            "weight": c["weight"],
            "met": bool(met),
            "value": value,
            "rule": rule,
        })
    score = 0 if max_points <= 0 else max(0, min(100, round(100.0 * points / max_points)))
    return score, details


def assign_flags(games, cfg):
    """Mark games as flagged using the tier ladder. Mutates `games`."""
    tiers = cfg["flag_tiers"]
    min_n = cfg.get("min_flagged_per_day", 1)
    max_n = cfg.get("max_flagged_per_day", 3)
    ranked = sorted(games, key=lambda g: g["stomp"]["score"], reverse=True)
    flagged = []
    for tier in tiers:
        for g in ranked:
            if len(flagged) >= max_n:
                break
            if g not in flagged and g["stomp"]["score"] >= tier["min_score"]:
                g["stomp"]["flag"] = tier["name"]
                flagged.append(g)
        if len(flagged) >= min_n:
            break
    if cfg.get("fill_to_minimum", True):
        for g in ranked:
            if len(flagged) >= min_n:
                break
            if g not in flagged:
                g["stomp"]["flag"] = "Best available"
                flagged.append(g)
    return flagged
