/* Bet engine: leg definitions, game simulation, historical hit rates, odds math.
 *
 * A leg is { m, s, t, side, line, odds }:
 *   m    market: "win" | "total" | "team" | "spread" | "btts"
 *   s    scope:  "game" (incl. OT/SO) | "reg" | "p1".."pN" (periods/quarters) | "h1" | "h2" (halves)
 *   t    pick:   "home" | "away" | "draw"     (win, team, spread)
 *   side        "over" | "under" | "yes" | "no" (total, team, btts)
 *   line        number (total, team, spread)
 *   odds        optional decimal odds for this leg
 *
 * Every estimate works on "outcomes": { p: [[h,a], ...one per period], fh, fa }
 * (score per period home/away, and the final score incl. OT).
 * The sport (NHL: 3 periods of goals, NFL/NBA: 4 quarters of points) comes from
 * the data file's settings via configure().
 */
(function () {
  "use strict";

  var ALL_MARKETS = {
    win: "Winner",
    total: "Total",
    team: "Team total",
    spread: "Spread / puck line",
    btts: "Both teams score",
  };
  var MARKETS = {}, SCOPES = {}, SPORT = "nhl", UNIT = "goals", DEFAULTS = {}, PROP_STATS = [];

  // Called with a data file's settings before building legs for that sport.
  function configure(settings) {
    SPORT = settings.sport || (settings.periods === 4 ? "nfl" : "nhl");
    UNIT = SPORT === "nhl" ? "goals" : "points";
    var scopes = settings.scopes || [
      { key: "game", label: "Full game" }, { key: "reg", label: "Regulation" },
      { key: "p1", label: "1st period" }, { key: "p2", label: "2nd period" }, { key: "p3", label: "3rd period" },
    ];
    SCOPES = {};
    scopes.forEach(function (x) { SCOPES[x.key] = x.label; });
    MARKETS = {};
    (settings.markets || Object.keys(ALL_MARKETS)).forEach(function (k) { MARKETS[k] = ALL_MARKETS[k]; });
    if (SPORT !== "nhl") MARKETS.spread = "Spread";
    else MARKETS.spread = "Puck line";
    DEFAULTS = settings.lineDefaults || { total: 5.5, totalScope: "reg", team: 0.5, teamScope: "p1", spread: -1.5 };
    PROP_STATS = settings.propStats || [];
    if (PROP_STATS.length) MARKETS.prop = "Player prop";
    api.MARKETS = MARKETS; api.SCOPES = SCOPES; api.SPORT = SPORT; api.PROP_STATS = PROP_STATS;
  }

  // ---------- leg evaluation ----------
  function sumP(o, from, to) {
    var h = 0, a = 0;
    for (var i = from; i < to; i++) { h += o.p[i][0]; a += o.p[i][1]; }
    return [h, a];
  }
  function goals(o, s) {
    var n = o.p.length;
    if (s === "game") return [o.fh, o.fa];
    if (s === "reg") return sumP(o, 0, n);
    if (s === "h1") return sumP(o, 0, n / 2);
    if (s === "h2") return sumP(o, n / 2, n);
    return o.p[+s.charAt(1) - 1];
  }

  // true / false, or null when it can't be judged (e.g. a player prop with no box score).
  function hits(leg, o) {
    if (leg.m === "prop") {
      var row = o.box && o.box[String(leg.pid)];
      if (!row || row[leg.stat] == null) return null;
      return leg.side === "over" ? row[leg.stat] > leg.line : row[leg.stat] < leg.line;
    }
    if (!o.p && leg.s !== "game") return null; // no period data for this game
    var g = goals(o, leg.s), h = g[0], a = g[1];
    var line = +leg.line;
    switch (leg.m) {
      case "win":
        if (leg.t === "draw") return h === a;
        return leg.t === "home" ? h > a : a > h;
      case "total":
        return leg.side === "over" ? h + a > line : h + a < line;
      case "team":
        var x = leg.t === "home" ? h : a;
        return leg.side === "over" ? x > line : x < line;
      case "spread":
        var diff = leg.t === "home" ? h - a : a - h;
        return diff + line > 0;
      case "btts":
        var both = h > 0 && a > 0;
        return leg.side === "yes" ? both : !both;
    }
    return false;
  }

  function statLabel(key) {
    var s = PROP_STATS.filter(function (x) { return x.key === key; })[0];
    return s ? s.label.toLowerCase() : key;
  }

  function label(leg, home, away) {
    if (leg.m === "prop") return (leg.name || "Player") + " " + leg.side + " " + leg.line + " " + statLabel(leg.stat);
    var team = leg.t === "home" ? home : leg.t === "away" ? away : "";
    var sc = SCOPES[leg.s];
    var ln = function (v) { return (+v > 0 && leg.m === "spread" ? "+" : "") + v; };
    switch (leg.m) {
      case "win":
        if (leg.t === "draw") return "Draw · " + sc;
        return leg.s === "game" ? team + " moneyline" : team + " to win · " + sc;
      case "total": return (leg.side === "over" ? "Over " : "Under ") + leg.line + " " + UNIT + " · " + sc;
      case "team": return team + (leg.side === "over" ? " over " : " under ") + leg.line + " · " + sc;
      case "spread": return team + " " + ln(leg.line) + " · " + sc;
      case "btts": return "Both teams score: " + (leg.side === "yes" ? "Yes" : "No") + " · " + sc;
    }
    return "?";
  }

  // Default values when the market changes.
  function defaults(m, fav) {
    switch (m) {
      case "win": return { m: m, s: "game", t: fav };
      case "total": return { m: m, s: DEFAULTS.totalScope || "game", side: "over", line: DEFAULTS.total };
      case "team": return { m: m, s: DEFAULTS.teamScope || "game", t: fav, side: "over", line: DEFAULTS.team };
      case "spread": return { m: m, s: "game", t: fav, line: DEFAULTS.spread };
      case "btts": return { m: m, s: "game", side: "yes" };
      case "prop": return { m: m, s: "game", side: "over" };
    }
  }

  // ---------- player props ----------
  function findPlayer(game, pid) {
    var sides = [game.home, game.away];
    for (var i = 0; i < sides.length; i++) {
      var list = game.teams[sides[i]].players || [];
      for (var j = 0; j < list.length; j++) if (String(list[j].id) === String(pid)) return { p: list[j], team: sides[i] };
    }
    return null;
  }
  function statsFor(pos) { return PROP_STATS.filter(function (s) { return s.pos.indexOf(pos) >= 0; }); }

  // This player's values for a stat, newest first, limited to the data window.
  function propValues(game, leg, win, recentN) {
    var f = findPlayer(game, leg.pid);
    var idx = PROP_STATS.map(function (s) { return s.key; }).indexOf(leg.stat);
    if (!f || idx < 0) return [];
    var rows = win === "recent" ? f.p.rows.slice(0, recentN) : f.p.rows;
    return rows.map(function (r) { return r[idx]; });
  }
  // A sensible starting line: just under the player's typical (median) value.
  function propDefaultLine(game, leg) {
    var v = propValues(game, leg, "season").slice().sort(function (a, b) { return a - b; });
    if (!v.length) return 0.5;
    return Math.max(0.5, Math.floor(v[Math.floor(v.length / 2)]) - 0.5);
  }

  function propHist(leg, game, win, recentN) {
    var v = propValues(game, leg, win, recentN);
    var hit = v.filter(function (x) { return leg.side === "over" ? x > leg.line : x < leg.line; }).length;
    return { p: v.length ? hit / v.length : null, hits: hit, n: v.length };
  }

  function erf(x) {
    var t = 1 / (1 + 0.3275911 * Math.abs(x));
    var y = 1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * Math.exp(-x * x);
    return x >= 0 ? y : -y;
  }
  function poissonCdf(k, lambda) {
    var term = Math.exp(-lambda), sum = term;
    for (var i = 1; i <= k; i++) { term *= lambda / i; sum += term; }
    return Math.min(1, sum);
  }
  // Model the stat from the player's average: Poisson for small counts
  // (shots, receptions, TDs...), a bell curve for yards and other big numbers.
  function propSim(leg, game, win, recentN) {
    var v = propValues(game, leg, win, recentN);
    if (!v.length) return null;
    var mean = v.reduce(function (a, b) { return a + b; }, 0) / v.length;
    var line = +leg.line, pOver;
    if (mean < 12 && !/yds/.test(leg.stat)) {
      pOver = 1 - poissonCdf(Math.floor(line), Math.max(mean, 0.01));
    } else {
      var varc = v.reduce(function (a, b) { return a + (b - mean) * (b - mean); }, 0) / Math.max(1, v.length - 1);
      var sd = Math.max(Math.sqrt(varc), 0.35 * mean, 1);
      pOver = 1 - 0.5 * (1 + erf((line - mean) / (sd * Math.SQRT2)));
    }
    return leg.side === "over" ? pOver : 1 - pOver;
  }

  // ---------- simulation ----------
  function mulberry32(a) {
    return function () {
      a |= 0; a = (a + 0x6d2b79f5) | 0;
      var t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  function poisson(lambda, rnd) {
    var L = Math.exp(-lambda), k = 0, p = 1;
    do { k++; p *= rnd(); } while (p > L);
    return k - 1;
  }

  // Which of a team's games to use.
  //   "recent" = last N games · "season" = this season only · "both" = this season + last season
  function pickGames(team, win, recentN) {
    var log = (team.log || []).filter(function (r) { return r.p; });
    if (win === "recent") return log.slice(0, recentN);
    if (win === "season") return log.filter(function (r) { return !r.ls; });
    return log;
  }

  function avgPeriods(games, nP) {
    return range(nP).map(function (i) {
      if (!games.length) return { gf: null, ga: null };
      var f = 0, a = 0;
      games.forEach(function (r) { f += r.p[i][0]; a += r.p[i][1]; });
      return { gf: f / games.length, ga: a / games.length };
    });
  }

  // Per-period scoring rates for one team: the chosen games blended with
  // recent form, then pulled toward the league average.
  function periodRates(team, win, settings, shrink, league) {
    var N = settings.recentGames, w = settings.recentWeight;
    var base = pickGames(team, win, N);
    var nP = league.length;
    var B = avgPeriods(base, nP), R = avgPeriods(pickGames(team, "recent", N), nP);
    var n = base.length;
    return range(nP).map(function (i) {
      var mix = function (a, b) {
        if (win === "recent") return a;
        if (a == null) return b; if (b == null) return a;
        return (1 - w) * a + w * b;
      };
      var gf = mix(B[i].gf, R[i].gf), ga = mix(B[i].ga, R[i].ga);
      var lg = league[i] || 1;
      if (gf == null) gf = lg;
      if (ga == null) ga = lg;
      return {
        gf: (n * gf + shrink * lg) / (n + shrink || 1),
        ga: (n * ga + shrink * lg) / (n + shrink || 1),
      };
    });
  }

  function range(n) { var r = []; for (var i = 0; i < n; i++) r.push(i); return r; }

  function normal(rnd) {
    var u = 1 - rnd(), v = rnd();
    return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
  }

  // Returns Int16Array with (periods*2 + 2) values per simulated game:
  // p1h, p1a, p2h, p2a, ..., finalHome, finalAway
  var simCache = {};
  function simulate(game, settings, win) {
    var key = game.id + ":" + win;
    if (simCache[key]) return simCache[key];
    var cfg = settings.simulation || {};
    var N = cfg.iterations || 20000;
    var shrink = cfg.shrink_games == null ? 10 : cfg.shrink_games;
    var league = settings.leaguePeriodGf || [1, 1, 1];
    var nP = league.length, stride = nP * 2 + 2;
    var H = periodRates(game.teams[game.home], win, settings, shrink, league);
    var A = periodRates(game.teams[game.away], win, settings, shrink, league);
    // Expected score per period = my attack x their defence / league average.
    var lh = range(nP).map(function (i) { return H[i].gf * A[i].ga / (league[i] || 1); });
    var la = range(nP).map(function (i) { return A[i].gf * H[i].ga / (league[i] || 1); });
    var sum = function (x) { return x.reduce(function (s, v) { return s + v; }, 0); };
    var homeOtShare = sum(lh) / (sum(lh) + sum(la));

    var sport = settings.sport || (nP === 4 ? "nfl" : "nhl");
    var nfl = sport === "nfl", nba = sport === "nba";
    // NBA: quarter points are roughly bell-shaped; a shared pace factor moves both teams together.
    var qsd = cfg.quarter_sd || 7, paceSd = cfg.pace_sd == null ? 0.04 : cfg.pace_sd;
    function nbaPoints(lambda, pace, rnd) { return Math.max(0, Math.round(lambda * pace + qsd * normal(rnd))); }
    var otGoal = cfg.ot_goal_prob == null ? 0.62 : cfg.ot_goal_prob;
    var otDecided = cfg.ot_decided_prob == null ? 0.9 : cfg.ot_decided_prob;
    // NFL: points come as touchdowns (7) and field goals (3), split using each team's TD share.
    var tdH = (game.teams[game.home].sample || {}).tdShare || 0.75;
    var tdA = (game.teams[game.away].sample || {}).tdShare || 0.75;
    function nflPoints(lambda, td, rnd) {
      return 7 * poisson(lambda * td / 7, rnd) + 3 * poisson(lambda * (1 - td) / 3, rnd);
    }

    var rnd = mulberry32(parseInt(String(game.id).replace(/\D/g, "").slice(-9), 10) || 1);
    var out = new Int16Array(N * stride);
    for (var n = 0; n < N; n++) {
      var th = 0, ta = 0, o = n * stride;
      var pace = nba ? 1 + paceSd * normal(rnd) : 1;
      for (var i = 0; i < nP; i++) {
        var h = nfl ? nflPoints(lh[i], tdH, rnd) : nba ? nbaPoints(lh[i], pace, rnd) : poisson(lh[i], rnd);
        var a = nfl ? nflPoints(la[i], tdA, rnd) : nba ? nbaPoints(la[i], pace, rnd) : poisson(la[i], rnd);
        out[o + i * 2] = h; out[o + i * 2 + 1] = a;
        th += h; ta += a;
      }
      if (th === ta) {
        if (nba) {
          // 5-minute overtimes until someone wins.
          var otH = sum(lh) * 5 / 48, otA = sum(la) * 5 / 48, otSd = qsd * Math.sqrt(5 / 12);
          for (var k = 0; k < 6 && th === ta; k++) {
            th += Math.max(0, Math.round(otH + otSd * normal(rnd)));
            ta += Math.max(0, Math.round(otA + otSd * normal(rnd)));
          }
          if (th === ta) { if (rnd() < homeOtShare) th++; else ta++; }
        } else if (nfl) {
          // Most NFL overtimes end with a field goal or TD; some end tied.
          if (rnd() < otDecided) { var pts = rnd() < 0.55 ? 3 : 6; if (rnd() < homeOtShare) th += pts; else ta += pts; }
        } else {
          // OT goal or shootout: either way the winner gets +1 in the final.
          var homeWins = rnd() < otGoal ? rnd() < homeOtShare : rnd() < 0.5;
          if (homeWins) th++; else ta++;
        }
      }
      out[o + nP * 2] = th; out[o + nP * 2 + 1] = ta;
    }
    var res = { data: out, n: N, stride: stride, periods: nP, lambdaHome: lh, lambdaAway: la };
    simCache[key] = res;
    return res;
  }

  function simProb(legs, game, settings, win) {
    var props = legs.filter(function (l) { return l.m === "prop"; });
    legs = legs.filter(function (l) { return l.m !== "prop"; });
    var pp = props.reduce(function (acc, l) {
      var x = propSim(l, game, win, settings.recentGames);
      return acc == null || x == null ? null : acc * x;
    }, 1);
    if (!legs.length) return pp;
    if (pp == null) return null;
    return pp * teamSimProb(legs, game, settings, win);
  }

  function teamSimProb(legs, game, settings, win) {
    var sim = simulate(game, settings, win);
    var d = sim.data, hit = 0, nP = sim.periods;
    var o = { p: range(nP).map(function () { return [0, 0]; }), fh: 0, fa: 0 };
    for (var n = 0; n < sim.n; n++) {
      var b = n * sim.stride;
      for (var i = 0; i < nP; i++) { o.p[i][0] = d[b + i * 2]; o.p[i][1] = d[b + i * 2 + 1]; }
      o.fh = d[b + nP * 2]; o.fa = d[b + nP * 2 + 1];
      var ok = true;
      for (var j = 0; j < legs.length && ok; j++) ok = hits(legs[j], o);
      if (ok) hit++;
    }
    return hit / sim.n;
  }

  // ---------- history ----------
  // Each past game of either team, placed in the slot that team has tonight.
  function historyOutcomes(game, windowName, recentN) {
    var out = [];
    [game.home, game.away].forEach(function (ab) {
      var isHome = ab === game.home;
      pickGames(game.teams[ab], windowName, recentN).forEach(function (r) {
        var p = r.p.map(function (q) { return isHome ? [q[0], q[1]] : [q[1], q[0]]; });
        out.push({
          team: ab,
          o: { p: p, fh: isHome ? r.gf : r.ga, fa: isHome ? r.ga : r.gf },
        });
      });
    });
    return out;
  }

  function histProb(legs, game, windowName, recentN) {
    var props = legs.filter(function (l) { return l.m === "prop"; }).map(function (l) {
      var h = propHist(l, game, windowName, recentN);
      h.name = l.name; return h;
    });
    legs = legs.filter(function (l) { return l.m !== "prop"; });
    var team = teamHistProb(legs, game, windowName, recentN);
    var pp = props.reduce(function (acc, h) { return acc == null || h.p == null ? null : acc * h.p; }, 1);
    if (!props.length) return team;
    if (!legs.length) {
      var one = props.length === 1 ? props[0] : null;
      return { p: pp, hits: one ? one.hits : 0, n: one ? one.n : 0, byTeam: null, props: props };
    }
    team.p = team.p == null || pp == null ? null : team.p * pp;
    team.props = props;
    return team;
  }

  function teamHistProb(legs, game, windowName, recentN) {
    var rows = historyOutcomes(game, windowName, recentN);
    var tally = {};
    tally[game.home] = [0, 0]; tally[game.away] = [0, 0];
    var hit = 0;
    rows.forEach(function (r) {
      var ok = legs.every(function (l) { return hits(l, r.o); });
      tally[r.team][1]++;
      if (ok) { hit++; tally[r.team][0]++; }
    });
    return { p: rows.length ? hit / rows.length : null, hits: hit, n: rows.length, byTeam: tally };
  }

  // ---------- result of a finished game ----------
  function resultOutcome(result) {
    if (!result || result.homeScore == null) return null;
    return { p: result.periods || null, fh: result.homeScore, fa: result.awayScore, box: result.box || null };
  }

  // ---------- odds ----------
  function toDecimal(v, fmt) {
    var x = parseFloat(v);
    if (isNaN(x)) return null;
    if (fmt === "american") {
      if (x >= 100) return 1 + x / 100;
      if (x <= -100) return 1 + 100 / -x;
      return null;
    }
    return x > 1 ? x : null;
  }
  function fromDecimal(d, fmt) {
    if (!d || d <= 1) return "—";
    if (fmt === "american") {
      var a = d >= 2 ? (d - 1) * 100 : -100 / (d - 1);
      return (a > 0 ? "+" : "") + Math.round(a);
    }
    return d.toFixed(2);
  }

  function confidence(n) {
    if (n < 10) return "Low";
    if (n < 25) return "Medium";
    return "High";
  }

  function verdict(prob, dec, minEdge) {
    if (prob == null || !dec) return null;
    var implied = 1 / dec;
    var edge = prob - implied;
    return {
      implied: implied,
      edge: edge,
      ev: prob * dec - 1,
      fair: prob > 0 ? 1 / prob : null,
      call: edge >= minEdge ? "Favorable" : edge <= -minEdge ? "Unfavorable" : "Close",
    };
  }

  // ---------- saved bets (this browser only) ----------
  var KEY = "sf-bets";
  function loadBets() {
    try { return JSON.parse(localStorage.getItem(KEY) || "[]"); } catch (e) { return []; }
  }
  function saveBets(list) {
    try { localStorage.setItem(KEY, JSON.stringify(list)); return true; } catch (e) { return false; }
  }

  var api = window.Bets = {
    configure: configure, findPlayer: findPlayer, statsFor: statsFor, propDefaultLine: propDefaultLine,
    propHist: propHist, propSim: propSim,
    MARKETS: MARKETS, SCOPES: SCOPES,
    hits: hits, label: label, defaults: defaults,
    simulate: simulate, simProb: simProb, pickGames: pickGames, histProb: histProb, resultOutcome: resultOutcome,
    toDecimal: toDecimal, fromDecimal: fromDecimal, confidence: confidence, verdict: verdict,
    loadBets: loadBets, saveBets: saveBets,
  };
})();
