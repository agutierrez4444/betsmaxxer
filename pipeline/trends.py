"""Trends: things that keep happening for a team or player, compared with
what's normal for the league, so obvious stuff ("QB throws for 1+ yards")
is filtered out and unusual streaks stand out.

Sport modules supply:
  * team props   - checked on each team's games (outcome dicts, see `outcome`)
  * player props - checked on each player's per-game stat rows
and this module does the counting, baselines, filtering and dedupe.

An outcome is a game seen from one team's side, with that team in the
"home" slot:  {"p": [[for, against], ...], "fh": for, "fa": against,
               "first": "home"|"away"|None, "end": "REG"/"OT"/...,
               plus any sport extras (e.g. "spread", "totalLine")}.
"""


def outcome(r, extras=()):
    o = {"p": r["periods"], "fh": r["gf"], "fa": r["ga"], "end": r["end"],
         "first": None if r.get("scoredFirst") is None else ("home" if r["scoredFirst"] else "away")}
    for k in extras:
        o[k] = r.get(k)
    return o


def mirror(o):
    """The same game seen from the other team's side."""
    m = dict(o)
    m["p"] = [[q[1], q[0]] for q in o["p"]]
    m["fh"], m["fa"] = o["fa"], o["fh"]
    if o.get("first"):
        m["first"] = "away" if o["first"] == "home" else "home"
    if o.get("spread") is not None:
        m["spread"] = -o["spread"]
    return m


# ---------- leg evaluation (same rules as site/bets.js) ----------

def _goals(o, scope):
    if scope == "game":
        return o["fh"], o["fa"]
    n = len(o["p"])
    if scope == "reg":
        idx = range(n)
    elif scope == "h1":
        idx = range(n // 2)
    elif scope == "h2":
        idx = range(n // 2, n)
    else:
        idx = [int(scope[1:]) - 1]
    return sum(o["p"][i][0] for i in idx), sum(o["p"][i][1] for i in idx)


def leg_hits(leg, o):
    h, a = _goals(o, leg["s"])
    m = leg["m"]
    if m == "win":
        if leg["t"] == "draw":
            return h == a
        return h > a if leg["t"] == "home" else a > h
    if m == "total":
        return h + a > leg["line"] if leg["side"] == "over" else h + a < leg["line"]
    if m == "team":
        x = h if leg["t"] == "home" else a
        return x > leg["line"] if leg["side"] == "over" else x < leg["line"]
    if m == "spread":
        d = h - a if leg["t"] == "home" else a - h
        return d + leg["line"] > 0
    if m == "btts":
        both = h > 0 and a > 0
        return both if leg["side"] == "yes" else not both
    raise ValueError(m)


def _prop_test(prop, o):
    """True/False, or None if this game doesn't count (missing data)."""
    if "pred" in prop:
        return prop["pred"](o)
    return leg_hits(prop["leg"], o)


def _rate(flags):
    flags = [f for f in flags if f is not None]
    return (sum(1 for f in flags if f) / float(len(flags)), sum(1 for f in flags if f), len(flags)) if flags else (None, 0, 0)


def _streak(flags):
    n = 0
    for f in flags:  # newest first
        if f is None:
            continue
        if not f:
            break
        n += 1
    return n


def _qualifies(rate, base, n, cfg):
    return (rate is not None and base is not None and n >= cfg["min_games"]
            and rate >= cfg["min_hit_rate"] and rate - base >= cfg["min_gap"])


def _dedupe(found):
    """Within a group (same stat & side) keep only the hardest line that qualified."""
    best = {}
    for t in found:
        g = t.pop("_group")
        r = t.pop("_rank")
        if g not in best or r > best[g][0]:
            best[g] = (r, t)
    return [v[1] for v in best.values()]


def league_outcomes(all_logs, extras=()):
    return [outcome(r, extras) for t in all_logs for r in all_logs[t]]


def team_trends(team, opp, slot, logs, league, props, cfg, extras=(), base_cache=None):
    """props: list of {"key", "label" (with {T}), "leg" or "pred", "group", "rank"}.
    `slot` is the team's side tonight ("home"/"away") so legs can be sent to
    the bet builder in tonight's terms. `league` = league_outcomes(...)."""
    mine = [outcome(r, extras) for r in logs.get(team, [])]
    theirs = [mirror(outcome(r, extras)) for r in logs.get(opp, [])]
    base_cache = {} if base_cache is None else base_cache
    found = []
    for p in props:
        flags = [_prop_test(p, o) for o in mine]
        rate, hits, n = _rate(flags)
        if p["key"] not in base_cache:
            base_cache[p["key"]] = _rate(_prop_test(p, o) for o in league)[0]
        base = base_cache[p["key"]]
        if not _qualifies(rate, base, n, cfg):
            continue
        orate, ohits, on = _rate(_prop_test(p, o) for o in theirs)
        leg = None
        if "leg" in p:
            leg = dict(p["leg"])
            if leg.get("t") in ("home", "away") and slot == "away":
                leg["t"] = "away" if leg["t"] == "home" else "home"
        found.append({
            "kind": "team", "team": team, "label": p["label"].replace("{T}", team),
            "hits": hits, "n": n, "rate": round(rate, 3), "base": round(base, 3),
            "gap": round(rate - base, 3), "streak": _streak(flags),
            "ctx": ("%s side: %d/%d" % (opp, ohits, on)) if on else None,
            "ctxRate": round(orate, 3) if orate is not None else None,
            "leg": leg, "_group": (team, p["group"]), "_rank": p.get("rank", 0),
        })
    return _dedupe(found)


def player_trends(team, opp, rows_by_player, all_rows, props, cfg, ctx_fn=None, info=None):
    """rows_by_player: {pid: [stat rows newest first]} for this team.
    all_rows: every qualifying player-game row in the league sample (baselines).
    props: list of {"stat", "label", "lines", "sides", "pos" (set), "usage" fn,
                    "value" fn(row) -> number}.
    """
    found = []
    base_cache = {}
    for pid, rows in rows_by_player.items():
        if not rows:
            continue
        pos = rows[0]["pos"]
        name = rows[0]["name"]
        for p in props:
            if pos not in p["pos"]:
                continue
            used = [r for r in rows if p["usage"](r)]
            if len(used) < cfg["min_games"]:
                continue
            vals = [p["value"](r) for r in used]
            for line in p["lines"]:
                for side in p["sides"]:
                    flags = [(v > line) if side == "over" else (v < line) for v in vals]
                    rate, hits, n = _rate(flags)
                    key = (p["stat"], pos, line, side)
                    if key not in base_cache:
                        pool = [p["value"](r) for r in all_rows if r["pos"] == pos and p["usage"](r)]
                        base_cache[key] = _rate([(v > line) if side == "over" else (v < line) for v in pool])[0]
                    base = base_cache[key]
                    if not _qualifies(rate, base, n, cfg):
                        continue
                    word = "over" if side == "over" else "under"
                    found.append({
                        "kind": "player", "team": team, "pid": pid, "name": name, "pos": pos,
                        "label": "%s %s %s %s" % (name, word, line, p["label"]),
                        "hits": hits, "n": n, "rate": round(rate, 3), "base": round(base, 3),
                        "gap": round(rate - base, 3), "streak": _streak(flags),
                        "avg": round(sum(vals) / len(vals), 2),
                        "ctx": ctx_fn(p["stat"], opp) if ctx_fn else None,
                        "prop": {"pid": pid, "stat": p["stat"], "side": side, "line": line},
                        "_group": (pid, p["stat"], side),
                        # overs: higher line is harder; unders: lower line is harder
                        "_rank": line if side == "over" else -line,
                    })
    out = _dedupe(found)
    if info:
        for t in out:
            t.update(info(t["pid"]) or {})
    return out


def finalize(items, cfg):
    items.sort(key=lambda t: (t["gap"], t["n"], t["rate"]), reverse=True)
    return items[: cfg.get("max_per_game", 30)]
