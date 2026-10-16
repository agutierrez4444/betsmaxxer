/* BetsMaxxer — static dashboard. Reads data/<sport>/*.json built by the pipeline. */
(function () {
  "use strict";

  var app = document.getElementById("app");
  var cache = {};
  var state = { window: "recent" }; // which sample the game page shows: "recent" | "sample"
  try { state.window = localStorage.getItem("sf-window") || "recent"; } catch (e) {}

  // ---------- helpers ----------
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function pct(v) { return v == null ? "—" : Math.round(v * 100) + "%"; }
  function num(v, d) { return v == null ? "—" : Number(v).toFixed(d == null ? 2 : d); }
  function todayISO() {
    var d = new Date();
    return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
  }
  function parseISO(s) { var p = s.split("-"); return new Date(+p[0], +p[1] - 1, +p[2]); }
  function fmtDay(s, opts) { return parseISO(s).toLocaleDateString(undefined, opts); }
  function fmtTime(utc) { return new Date(utc).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }); }
  function isDark() {
    var t = document.documentElement.getAttribute("data-theme");
    if (t) return t === "dark";
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  }
  function logo(t) { return isDark() ? (t.darkLogo || t.logo) : t.logo; }
  function tierClass(flag) { return flag ? "tier-" + flag.split(" ")[0] : ""; }
  function ringColor(score) {
    if (score >= 70) return "var(--tier-strict)";
    if (score >= 55) return "var(--tier-standard)";
    if (score >= 40) return "var(--tier-loose)";
    return "var(--text-3)";
  }
  function ring(score, big) {
    return '<div class="ring' + (big ? " lg" : "") + '" style="--v:' + score + ";--c:" + ringColor(score) + '">' +
      '<span class="num">' + score + "<small>STOMP</small></span></div>";
  }
  var ICON = {
    check: '<svg viewBox="0 0 24 24"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>',
    x: '<svg viewBox="0 0 24 24"><path d="M7 7l10 10M17 7L7 17"/></svg>',
    left: '<svg viewBox="0 0 24 24"><path d="M15 6l-6 6 6 6"/></svg>',
    right: '<svg viewBox="0 0 24 24"><path d="M9 6l6 6-6 6"/></svg>',
  };

  function load(path) {
    if (cache[path]) return Promise.resolve(cache[path]);
    return fetch(path, { cache: "no-cache" }).then(function (r) {
      if (!r.ok) throw new Error(path + " → " + r.status);
      return r.json();
    }).then(function (d) { cache[path] = d; return d; });
  }

  // ---------- theme ----------
  document.getElementById("themeToggle").addEventListener("click", function () {
    var next = isDark() ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    try { localStorage.setItem("sf-theme", next); } catch (e) {}
    route(); // re-render so team logos switch variant
  });

  // ---------- routing ----------
  // #/nhl · #/nhl/2026-10-15 · #/nhl/2026-10-15/2026020118 · #/nfl · #/nfl/2026-w06 · #/nfl/2026-w06/<id> · #/bets
  function route() {
    var parts = (location.hash || "#/nhl").replace(/^#\/?/, "").split("/");
    var sport = parts[0] || "nhl";
    document.querySelectorAll(".sport-switch a").forEach(function (a) {
      a.classList.toggle("active", a.getAttribute("data-sport") === sport);
    });
    document.querySelector(".mybets").classList.toggle("active", sport === "bets");
    if (sport === "bets") return renderBets();
    if (sport !== "nfl") sport = "nhl";
    currentSport = sport;
    if (parts[2]) return renderGame(sport, parts[1], parts[2]);
    return renderDay(sport, parts[1]);
  }
  window.addEventListener("hashchange", function () { route(); window.scrollTo(0, 0); });

  function loading() {
    app.innerHTML = '<div class="skeleton" style="height:70px;max-width:420px"></div>' +
      '<div class="grid" style="margin-top:28px"><div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div></div>';
  }
  function fail(err) {
    app.innerHTML = '<div class="card empty"><h3>Couldn\'t load data</h3><p class="muted">' + esc(err.message) +
      '</p><p class="faint">Run <code>run.bat</code> (or <code>py -m pipeline.build</code> / <code>py -m pipeline.nfl_build</code>) and serve the <code>site</code> folder.</p></div>';
  }
  var currentSport = "nhl";

  // Rest since the last game. NHL: days off (0 = back-to-back). NFL: days between games.
  function restText(t, short) {
    if (t.daysOff !== undefined) {
      if (t.daysOff === null) return short ? "rested" : "First game back (well rested)";
      if (t.daysOff === 0) return short ? "B2B" : "Back-to-back";
      return short ? t.daysOff + "d off" : t.daysOff + (t.daysOff === 1 ? " day off" : " days off");
    }
    if (t.rest != null) return short ? t.rest + "d rest" : t.rest + " days rest";
    return "";
  }
  function isNFL(data) { return data && data.sport === "nfl"; }
  function fmtRange(a, b) {
    var x = fmtDay(a, { month: "short", day: "numeric" });
    return a === b ? x : x + " – " + fmtDay(b, { month: a.slice(5, 7) === b.slice(5, 7) ? undefined : "short", day: "numeric" });
  }

  // ---------- day view ----------
  function renderDay(sport, date) {
    loading();
    load("data/" + sport + "/index.json").then(function (idx) {
      var days = idx.days.map(function (d) { return d.date; });
      if (!days.length) throw new Error("No " + sport.toUpperCase() + " data built yet");
      if (!date || days.indexOf(date) < 0) {
        var t = todayISO();
        if (sport === "nfl") {
          // the week that is in progress or next up
          var cur = idx.days.filter(function (d) { return d.end >= t; })[0];
          date = cur ? cur.date : days[days.length - 1];
        } else {
          date = days.indexOf(t) >= 0 ? t : (days.filter(function (d) { return d > t; })[0] || days[days.length - 1]);
        }
      }
      return load("data/" + sport + "/" + date + ".json").then(function (data) { dayView(idx, date, data); });
    }).catch(fail);
  }

  function dayView(idx, date, data) {
    currentDate = date;
    var days = idx.days;
    var i = days.findIndex(function (d) { return d.date === date; });
    var prev = days[i - 1], next = days[i + 1];
    var flagged = data.games.filter(function (g) { return g.stomp.flag; });
    var rest = data.games.filter(function (g) { return !g.stomp.flag; });
    var nfl = isNFL(data), S = "#/" + data.sport + "/", t0 = todayISO();
    var isToday = nfl ? data.start <= t0 && t0 <= data.end : date === t0;
    var isPast = nfl ? data.end < t0 : date < t0;

    var strip = days.map(function (d) {
      var now = nfl ? d.start <= t0 && t0 <= d.end : d.date === t0;
      return '<a class="day-pill' + (d.date === date ? " active" : "") + '" href="' + S + d.date + '">' +
        '<div class="dow">' + (now ? (nfl ? "This week" : "Today") : nfl ? d.label : fmtDay(d.date, { weekday: "short" })) + "</div>" +
        '<div class="dnum">' + (nfl ? fmtRange(d.start, d.end) : fmtDay(d.date, { month: "short", day: "numeric" })) + "</div>" +
        '<div class="dmeta">' + d.games + " gm" + (d.flagged ? " · <b>" + d.flagged + "⚑</b>" : "") + "</div></a>";
    }).join("");

    var tiers = data.settings.tiers.map(function (t) {
      return '<span class="tier-' + esc(t.name) + '"><i style="background:var(--tier-color)"></i>' + esc(t.name) + " ≥ " + t.min_score + "</span>";
    }).join("") + '<span class="tier-Best"><i style="background:var(--tier-color)"></i>Best available</span>';

    app.innerHTML =
      '<div class="fade-in">' +
      '<div class="datebar"><div>' +
      "<h1>" + (nfl ? esc(data.label) + ' <span class="h1-sub">' + fmtRange(data.start, data.end) + "</span>"
        : fmtDay(date, { weekday: "long", month: "long", day: "numeric" })) + "</h1>" +
      '<div class="sub">' + data.games.length + " games · " + flagged.length + " worth a look" +
      (isToday ? "" : isPast ? (nfl ? " · past week, results shown" : " · past day, results shown") : " · upcoming") + "</div></div>" +
      '<div class="date-nav">' +
      '<a class="icon-btn" ' + (prev ? 'href="' + S + prev.date + '"' : 'aria-disabled="true" style="opacity:.35;pointer-events:none"') + ' aria-label="Previous">' + ICON.left + "</a>" +
      '<a class="icon-btn" href="#/' + data.sport + '">' + (nfl ? "This week" : "Today") + "</a>" +
      '<a class="icon-btn" ' + (next ? 'href="' + S + next.date + '"' : 'aria-disabled="true" style="opacity:.35;pointer-events:none"') + ' aria-label="Next">' + ICON.right + "</a>" +
      "</div></div>" +
      '<div class="day-strip">' + strip + "</div>" +
      '<div class="explain card"><div class="ex-icon" aria-hidden="true">' + ring(72) + "</div><div>" +
      "<b>What's the Stomp Index?</b> Every game gets a 0–100 score from simple rules: the favorite scores a lot, the underdog allows a lot, " +
      "a big " + (nfl ? "point" : "goal") + "-differential gap, rest, home " + (nfl ? "field" : "ice") + " and more. The higher the score, the more one-sided the matchup looks on paper. " +
      "<b>Worth a look</b> are the most lopsided games " + (nfl ? "this week" : "today") + ", your best candidates for favorite-side bets. " +
      "The rest of the slate is much closer and harder to call. It's a shortlist for your own research, not a prediction." +
      '<div class="legend">' + tiers + "</div></div></div>" +
      trackRecord(days, nfl) +
      '<div class="section-title">Worth a look <span class="count">' + flagged.length + "</span></div>" +
      (flagged.length ? '<div class="grid flagged">' + flagged.map(gameCard).join("") + "</div>"
        : '<div class="card empty">Nothing flagged for this ' + (nfl ? "week" : "day") + ".</div>") +
      (rest.length ? '<div class="section-title">Rest of the slate <span class="count">' + rest.length + "</span></div>" +
        '<div class="grid">' + rest.map(gameCard).join("") + "</div>" : "") +
      '<p class="note">Built ' + new Date(data.generatedAt).toLocaleString() + " · stats use games before this " + (nfl ? "week" : "date") +
      " · recent window " + data.settings.recentGames + " games" + (nfl ? " · odds and lines from nflverse (closing lines)" : "") + "</p></div>";

    var active = app.querySelector(".day-pill.active");
    if (active) active.scrollIntoView({ block: "nearest", inline: "center" });
  }

  // How flagged games have actually done, across every built day that has results.
  function trackRecord(days, nfl) {
    var n = 0, h = { ml: 0, over: 0, firstPeriod: 0, all3: 0 };
    days.forEach(function (d) {
      if (!d.flaggedFinal) return;
      n += d.flaggedFinal;
      Object.keys(h).forEach(function (k) { h[k] += (d.hits || {})[k] || 0; });
    });
    if (!n) return "";
    var cell = function (label, k) {
      return '<div class="tr-cell"><div class="tr-v num">' + h[k] + "/" + n + " <small>" + pct(h[k] / n) + "</small></div>" +
        '<div class="tr-l">' + label + "</div></div>";
    };
    return '<div class="card track"><div class="tr-title">Flag track record<span>' + n + " finished flagged games</span></div>" +
      cell("Moneyline", "ml") + cell(nfl ? "Over (posted)" : "Over (reg.)", "over") + cell(nfl ? "Fav scores Q1" : "Fav scores 1st", "firstPeriod") + cell("All 3", "all3") + "</div>";
  }

  function gameCard(g) {
    var fav = g.stomp.fav, dog = g.stomp.dog;
    var order = [g.away, g.home];
    var r = g.result;
    var rows = order.map(function (ab) {
      var t = g.teams[ab];
      var right;
      if (r) {
        var sc = ab === g.home ? r.homeScore : r.awayScore;
        right = '<span class="final">' + sc + "</span>";
      } else {
        right = t.odds ? '<span class="odds">' + num(t.odds, 2) + "</span>" : "";
      }
      var lost = r && ((ab === g.home) ? r.homeScore < r.awayScore : r.awayScore < r.homeScore);
      return '<div class="gc-team' + (ab === fav ? " fav" : "") + (lost ? " lost" : "") + '">' +
        '<img src="' + esc(logo(t)) + '" alt="" loading="lazy">' +
        '<div style="min-width:0"><div class="nm"><span class="ha ' + (ab === g.home ? "h" : "a") + '">' + (ab === g.home ? "HOME" : "AWAY") + "</span>" + esc(t.short) + "</div>" +
        '<div class="rec">' + esc(t.record || "") +
        (t.spread != null && t.spread < 0 ? " · " + t.spread : "") +
        (restText(t, true) ? ' · <span class="' + (t.daysOff === 0 ? "tired" : "") + '">' + esc(restText(t, true)) + "</span>" : "") + "</div></div>" + right + "</div>";
    }).join("");

    var met = g.stomp.conditions.filter(function (c) { return c.met; });
    met.sort(function (a, b) { return Math.abs(b.weight) - Math.abs(a.weight); });
    var chips = met.slice(0, 4).map(function (c) {
      return '<span class="chip ' + (c.weight < 0 ? "bad" : "good") + '">' + esc(c.label) + "</span>";
    }).join("") + (met.length > 4 ? '<span class="chip">+' + (met.length - 4) + "</span>" : "");

    var mk = marketFav(g);
    if (mk && mk !== fav) chips = '<span class="chip warn">Market favors ' + esc(mk) + "</span>" + chips;

    var legs = "";
    if (r) {
      legs = '<div class="legs-result">' +
        legChip(fav + " ML", r.legs.ml) + legChip((g.lines ? "Total " : "Reg. total ") + r.regTotal, r.legs.over) +
        legChip(fav + (g.lines ? " Q1" : " 1st"), r.legs.firstPeriod) + legChip("All 3", r.legs.all3) + "</div>";
    }

    var top = (g.stomp.flag ? '<span class="badge">' + esc(g.stomp.flag) + "</span>" : "<span>" + esc(g.venue || "") + "</span>") +
      "<span>" + (r ? "Final" + (r.end !== "REG" ? " / " + r.end : "") : g.state === "LIVE" || g.state === "CRIT" ? "Live" : (g.lines ? new Date(g.startTimeUTC).toLocaleDateString(undefined, { weekday: "short" }) + " " : "") + fmtTime(g.startTimeUTC)) + "</span>";

    return '<a class="card game-card ' + (g.stomp.flag ? "is-flagged " + tierClass(g.stomp.flag) : "") + '" href="#/' + currentSport + "/" + currentDate + "/" + g.id + '">' +
      '<div class="gc-top">' + top + "</div>" +
      '<div class="gc-body"><div class="gc-teams">' + rows + "</div>" + ring(g.stomp.score) + "</div>" +
      (chips ? '<div class="chips">' + chips + "</div>" : "") + legs + "</a>";
  }
  // The bookmaker's favorite (lower decimal odds), if odds are available.
  function marketFav(g) {
    var a = g.teams[g.away].odds, h = g.teams[g.home].odds;
    if (!a || !h || a === h) return null;
    return a < h ? g.away : g.home;
  }
  function legChip(label, hit) {
    return '<span class="chip ' + (hit ? "good" : "bad") + '">' + (hit ? "✓ " : "✗ ") + esc(label) + "</span>";
  }
  var currentDate = null; // date of the page being shown, used in card links

  // ---------- game view ----------
  function renderGame(sport, date, id) {
    loading();
    currentDate = date;
    load("data/" + sport + "/" + date + ".json").then(function (data) {
      var g = data.games.find(function (x) { return String(x.id) === String(id); });
      if (!g) throw new Error("Game " + id + " not found on " + date);
      gameView(data, g);
    }).catch(fail);
  }

  function gameView(data, g) {
    var fav = g.teams[g.stomp.fav], dog = g.teams[g.stomp.dog];
    var away = g.teams[g.away], home = g.teams[g.home];
    var w = state.window;
    var legsCfg = data.settings.legs;
    var r = g.result;
    var nfl = isNFL(data);
    Bets.configure(data.settings);

    function heroTeam(t) {
      var isFav = t.abbrev === fav.abbrev;
      var isHome = t.abbrev === g.home;
      return '<div class="hero-team">' +
        '<span class="ha big ' + (isHome ? "h" : "a") + '">' + (isHome ? "HOME" : "AWAY") + "</span>" +
        '<img src="' + esc(logo(t)) + '" alt="">' +
        '<div class="nm">' + esc(t.name) + "</div>" +
        '<div class="meta"><span>' + esc(t.record) + "</span>" + (t.streak ? "<span>Streak " + esc(t.streak) + "</span>" : "") +
        (t.odds ? '<span class="num">ML ' + num(t.odds, 2) + "</span>" : "") +
        (t.spread != null ? '<span class="num">' + (t.spread > 0 ? "+" : "") + t.spread + "</span>" : "") +
        "</div>" +
        '<div class="meta rest-line"><span class="rest' + (t.daysOff === 0 ? " tired" : "") + '">' + esc(restText(t)) + "</span>" +
        (t.gamesLast7 != null ? "<span>" + t.gamesLast7 + " game" + (t.gamesLast7 === 1 ? "" : "s") + " in last 7 days</span>" : "") + "</div>" +
        '<span class="role ' + (isFav ? "fav" : "dog") + '">' + (isFav ? "Stomp side" : "Underdog") + "</span>" +
        (marketFav(g) === t.abbrev && !isFav ? '<span class="chip warn">Market favorite</span>' : "") + "</div>";
    }

    var mid = r
      ? '<div class="hero-final">' + r.awayScore + " – " + r.homeScore + '</div><div class="when">Final' + (r.end !== "REG" ? " (" + r.end + ")" : "") + "</div>"
      : ring(g.stomp.score, true) + '<div class="when">' + new Date(g.startTimeUTC).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" }) +
        " · " + fmtTime(g.startTimeUTC) + "<br>" + esc(away.short) + " @ " + esc(home.short) + (g.venue ? " · " + esc(g.venue) : "") + "</div>";
    if (r) mid = ring(g.stomp.score, true) + mid;

    var seg = '<div class="seg" role="tablist">' +
      '<button data-w="recent" class="' + (w === "recent" ? "on" : "") + '">Last ' + data.settings.recentGames + "</button>" +
      '<button data-w="sample" class="' + (w === "sample" ? "on" : "") + '">Season</button></div>';

    var sampleNote = [fav, dog].filter(function (t) { return t.nPrior > 0; }).map(function (t) {
      return t.abbrev + " season sample includes " + t.nPrior + " games from last season";
    }).join(" · ");

    app.innerHTML =
      '<div class="fade-in">' +
      '<a class="back" href="#/' + data.sport + "/" + data.date + '">' + ICON.left + "All games</a>" +
      '<div class="card hero">' + heroTeam(away) + '<div class="hero-mid">' + mid + "</div>" + heroTeam(home) + "</div>" +

      '<div class="stack">' +
      // Legs
      '<div class="card panel"><div class="panel-head"><div><h3>The 3 legs — how often they\'ve hit</h3>' +
      '<p class="hint">' + esc(fav.short) + " rates are from their games as the favorite side; " + esc(dog.short) +
      " rates are the mirror image from theirs (lost, over, " + (nfl ? "allowed points in Q1" : "conceded in 1st") + "). " +
      (nfl ? "The over uses each past game's posted total. " : "") + "Past results, not a prediction.</p></div>" + seg + "</div>" +
      legsPanel(g, fav, dog, w, legsCfg) +
      (sampleNote && w === "sample" ? '<p class="note">' + esc(sampleNote) + "</p>" : "") + "</div>" +

      '<div class="card panel builder" id="builder"></div>' +
      '<div class="card panel" id="trends"></div>' +

      '<div class="cols-2">' +
      // Conditions
      '<div class="card panel"><h3>Why it scored ' + g.stomp.score + '</h3><p class="hint">From ' + esc(fav.short) +
      "'s side (the other way round it scores " + g.stomp.otherSideScore + "). Edit rules in config.yaml → " + data.sport + ".</p>" + condList(g) + "</div>" +
      // Comparison
      '<div class="card panel"><div class="panel-head"><div><h3>Head to head numbers</h3><p class="hint">Per game · ' +
      (w === "recent" ? "last " + data.settings.recentGames : "season") + "</p></div>" + seg + "</div>" + compare(away, home, w, nfl) + "</div>" +
      "</div>" +

      // Periods
      '<div class="card panel"><div class="panel-head"><div><h3>' + (nfl ? "Points by quarter" : "Goals by period") + '</h3><p class="hint">Average ' +
      (nfl ? "points" : "goals") + " for and against per " + (nfl ? "quarter" : "period") + " · " +
      (w === "recent" ? "last " + data.settings.recentGames : "season") + "</p></div>" + seg + "</div>" + periodChart(away, home, w) + "</div>" +

      // Recent games
      '<div class="cols-2">' + recentList(away, legsCfg, nfl) + recentList(home, legsCfg, nfl) + "</div>" +

      // Players
      (nfl ? '<div class="cols-2">' + keyPlayers(away) + keyPlayers(home) + "</div>"
        : '<div class="cols-2">' + skaterTable(away, data.settings.recentGames) + skaterTable(home, data.settings.recentGames) + "</div>" +
          '<div class="cols-2">' + goalieCard(away) + goalieCard(home) + "</div>") +

      (g.h2h.length ? '<div class="card panel"><h3>Recent meetings</h3><p class="hint">From ' + esc(home.short) + "'s side</p>" +
        '<div class="games-list">' + g.h2h.map(function (x) { return gameRow(x, legsCfg, nfl); }).join("") + "</div></div>" : "") +
      "</div></div>";

    app.querySelectorAll(".seg button").forEach(function (b) {
      b.addEventListener("click", function () {
        state.window = b.getAttribute("data-w");
        try { localStorage.setItem("sf-window", state.window); } catch (e) {}
        var y = window.scrollY;
        gameView(data, g);
        window.scrollTo(0, y);
      });
    });
    initBuilder(data, g);
    initTrends(data, g);
  }

  // ---------- trends ----------
  function initTrends(data, g) {
    var box = document.getElementById("trends");
    var list = g.trends || [];
    var tcfg = data.settings.trends || {};
    var minEdge = (data.settings.builder || {}).min_edge || 0.05;
    var filter = "all";
    var odds = {}; // index -> decimal

    // Raw hit rates like 8/8 overstate things, so the estimate is pulled toward
    // the league norm as if we'd also seen 5 "normal" games.
    function est(t) { return (t.hits + 5 * t.base) / (t.n + 5); }

    function row(t, i) {
      var img = t.kind === "player"
        ? '<img class="tr-img round" src="' + esc(t.headshot) + '" alt="" loading="lazy">'
        : '<img class="tr-img" src="' + esc(logo(g.teams[t.team])) + '" alt="">';
      return '<div class="trend" data-i="' + i + '">' + img +
        '<div class="tr-main"><div class="tr-label">' + esc(t.label) + "</div>" +
        '<div class="tr-sub">' +
        '<span class="num"><b>' + t.hits + "/" + t.n + "</b> games</span>" +
        "<span>normal " + pct(t.base) + "</span>" +
        (t.streak >= 3 ? '<span class="streak">' + t.streak + " in a row</span>" : "") +
        (t.avg != null ? "<span>avg " + t.avg + "</span>" : "") +
        (t.ctx ? '<span class="faint">' + esc(t.ctx) + "</span>" : "") +
        "</div></div>" +
        '<div class="tr-gap num">+' + Math.round(t.gap * 100) + '<small>vs normal</small></div>' +
        '<div class="tr-act">' +
        '<input class="tr-odds" data-i="' + i + '" inputmode="decimal" placeholder="' + (pref("sf-odds", "decimal") === "american" ? "+150" : "odds") + '" aria-label="Odds">' +
        '<span class="tr-v" data-v="' + i + '"></span>' +
        (t.leg || t.prop ? '<button class="icon-btn sm" data-add="' + i + '" title="Add to bet builder">+ Builder</button>' : "") +
        '<button class="icon-btn sm" data-save="' + i + '" title="Save as a single bet" disabled>Save</button>' +
        "</div></div>";
    }

    function render() {
      var shown = list.map(function (t, i) { return [t, i]; }).filter(function (x) {
        var t = x[0];
        if (filter === "team") return t.kind === "team";
        if (filter === "player") return t.kind === "player";
        if (filter === g.home || filter === g.away) return t.team === filter;
        return true;
      });
      var f = function (k, label) { return '<button data-f="' + k + '" class="' + (filter === k ? "on" : "") + '">' + esc(label) + "</button>"; };
      box.innerHTML =
        '<div class="panel-head"><div><h3>Trends <span class="count faint">' + list.length + "</span></h3>" +
        '<p class="hint" title="Hit in at least ' + pct(tcfg.min_hit_rate) + " of this season's games (min " + tcfg.min_games + ") and beat the league norm by " +
        Math.round((tcfg.min_gap || 0) * 100) + '+ points. The estimate next to your odds is pulled toward normal, because short streaks overstate things.">' +
        "Bets that keep hitting this season, well above the league norm. Type your odds to check one.</p></div>" +
        '<div class="seg">' + f("all", "All") + f("team", "Team") + f("player", "Players") + f(g.away, g.away) + f(g.home, g.home) + "</div></div>" +
        (shown.length ? '<div class="trend-list">' + shown.map(function (x) { return row(x[0], x[1]); }).join("") + "</div>"
          : '<div class="bempty">Nothing unusual enough for this game with the current thresholds (config.yaml → trends).</div>');
      shown.forEach(function (x) { if (odds[x[1]]) { box.querySelector('.tr-odds[data-i="' + x[1] + '"]').value = Bets.fromDecimal(odds[x[1]], pref("sf-odds", "decimal")); update(x[1]); } });
    }

    function update(i) {
      var t = list[i], el = box.querySelector('[data-v="' + i + '"]');
      var save = box.querySelector('[data-save="' + i + '"]');
      var v = Bets.verdict(est(t), odds[i], minEdge);
      el.innerHTML = v ? '<span class="vpill v-' + v.call + '" title="Estimate ' + pct(est(t)) + " vs implied " + pct(v.implied) + '">' +
        pct(est(t)) + " vs " + pct(v.implied) + "</span>" : '<span class="faint">est ' + pct(est(t)) + "</span>";
      save.disabled = !v;
    }

    box.addEventListener("input", function (ev) {
      if (!ev.target.classList.contains("tr-odds")) return;
      var i = +ev.target.getAttribute("data-i");
      odds[i] = Bets.toDecimal(ev.target.value, pref("sf-odds", "decimal"));
      update(i);
    });
    box.addEventListener("click", function (ev) {
      var b = ev.target.closest("button");
      if (!b) return;
      if (b.hasAttribute("data-f")) { filter = b.getAttribute("data-f"); render(); return; }
      if (b.hasAttribute("data-add")) {
        var t = list[+b.getAttribute("data-add")];
        var B = builderState(g.id);
        B.legs.push(t.leg ? Object.assign({}, t.leg)
          : Object.assign({ m: "prop", s: "game", name: t.name, pos: t.pos }, t.prop));
        persistBuilder(g.id);
        initBuilder(data, g);
        toast("Added to the bet builder");
        return;
      }
      if (b.hasAttribute("data-save")) {
        var i = +b.getAttribute("data-save"), tr = list[i];
        var leg = tr.leg ? Object.assign({}, tr.leg) : Object.assign({ m: "prop", name: tr.name }, tr.prop);
        leg.label = tr.label;
        var v = Bets.verdict(est(tr), odds[i], minEdge);
        var all = Bets.loadBets();
        all.push({
          id: Date.now().toString(36), sport: data.sport, date: data.date, gameId: g.id, home: g.home, away: g.away,
          legs: [leg], odds: odds[i], method: "trend", prob: est(tr), probHist: tr.rate,
          call: v ? v.call : null, savedAt: new Date().toISOString(),
        });
        Bets.saveBets(all);
        updateBetCount();
        toast("Saved to My bets");
      }
    });
    render();
  }

  function legsPanel(g, fav, dog, w, cfg) {
    var fl = fav.legs[w] || {}, dl = dog.legs[w] || {};
    var r = g.result;
    var nfl = !!g.lines;
    var legs = [
      { key: "ml", name: fav.abbrev + " moneyline", fl: "won", dl: "lost", res: r && r.legs.ml },
      { key: "over", name: nfl ? "Over the posted total" + (g.lines.total ? " (" + g.lines.total + ")" : "")
          : "Over " + cfg.regulation_total_line + " (regulation)", fl: "over", dl: "over", res: r && r.legs.over },
      { key: "firstPeriod", name: nfl ? fav.abbrev + " scores in Q1" : fav.abbrev + " 1st period o" + cfg.first_period_team_line,
        fl: "scored", dl: "allowed", res: r && r.legs.firstPeriod },
      { key: "all3", name: "All three together", fl: "all 3", dl: "all 3", res: r && r.legs.all3 },
    ];
    return '<div class="cols-2" style="grid-template-columns:repeat(auto-fit,minmax(220px,1fr))">' + legs.map(function (L) {
      var a = fl[L.key], b = dl[L.key];
      var avg = a != null && b != null ? (a + b) / 2 : (a != null ? a : b);
      return '<div class="leg' + (L.key === "all3" ? " all3" : "") + '">' +
        '<div class="lname">' + esc(L.name) + "</div>" +
        '<div class="lbig">' + pct(avg) + "<small>avg</small></div>" +
        legRow(fav.abbrev, a, "fav", fl.gp) + legRow(dog.abbrev, b, "dog", dl.gp) +
        (r ? '<div class="result" style="color:var(--' + (L.res ? "good" : "bad") + ')">' + (L.res ? "✓ Hit" : "✗ Missed") + " in this game</div>" : "") +
        "</div>";
    }).join("") + "</div>";
  }
  function legRow(label, v, cls, gp) {
    return '<div class="lrow"><b>' + esc(label) + '</b><div class="bar ' + cls + '"><i style="width:' + (v == null ? 0 : Math.round(v * 100)) +
      '%"></i></div><span class="v" title="' + (gp || 0) + ' games">' + pct(v) + "</span></div>";
  }

  // ---------- bet builder ----------
  // Builder legs per game survive re-renders and reloads (this browser only).
  function builderState(id) {
    state.builders = state.builders || {};
    if (!state.builders[id]) {
      var saved = null;
      try { saved = JSON.parse(localStorage.getItem("sf-builder-" + id) || "null"); } catch (e) {}
      state.builders[id] = saved || { legs: [], total: null };
    }
    return state.builders[id];
  }
  function persistBuilder(id) {
    try { localStorage.setItem("sf-builder-" + id, JSON.stringify(state.builders[id])); } catch (e) {}
  }
  function pref(key, def) { try { return localStorage.getItem(key) || def; } catch (e) { return def; } }
  function setPref(key, v) { try { localStorage.setItem(key, v); } catch (e) {} }

  function opt(value, text, cur) {
    return '<option value="' + esc(value) + '"' + (String(value) === String(cur) ? " selected" : "") + ">" + esc(text) + "</option>";
  }

  function initBuilder(data, g) {
    var box = document.getElementById("builder");
    var B = builderState(g.id);
    var favSide = g.stomp.fav === g.home ? "home" : "away";
    var method = pref("sf-method", "sim"), fmt = pref("sf-odds", "decimal");
    var minEdge = (data.settings.builder || {}).min_edge || 0.05;
    var recentN = data.settings.recentGames;
    var bwin = pref("sf-bwindow", "season"); // which games feed both methods

    function oddsText(dec) { return dec ? Bets.fromDecimal(dec, fmt) : ""; }
    function seg(key, cur, items) {
      return '<div class="seg" data-pref="' + key + '">' + items.map(function (x) {
        return '<button data-v="' + x[0] + '" class="' + (cur === x[0] ? "on" : "") + '" title="' + esc(x[2] || "") + '">' + esc(x[1]) + "</button>";
      }).join("") + "</div>";
    }

    // Fill in a player-prop leg: first player of the favorite, first stat, a line near their median.
    function setupProp(leg, pid) {
      var teams = [g.stomp.fav, g.stomp.dog];
      var p = pid ? Bets.findPlayer(g, pid) : null;
      for (var i = 0; !p && i < teams.length; i++) {
        var list = g.teams[teams[i]].players || [];
        if (list.length) p = { p: list[0], team: teams[i] };
      }
      if (!p) return leg;
      leg.pid = p.p.id; leg.name = p.p.name; leg.pos = p.p.pos;
      var stats = Bets.statsFor(p.p.pos);
      if (!stats.some(function (s) { return s.key === leg.stat; })) leg.stat = stats[0].key;
      leg.line = Bets.propDefaultLine(g, leg);
      return leg;
    }

    function legRow(leg, i) {
      var fields;
      var odds = '<input data-f="odds" class="odds-in" inputmode="decimal" placeholder="odds" value="' + esc(oddsText(leg.odds)) + '" aria-label="This leg\'s odds (optional)" title="Optional: this leg\'s own odds">';
      var market = '<select data-f="m">' + Object.keys(Bets.MARKETS).map(function (k) { return opt(k, Bets.MARKETS[k], leg.m); }).join("") + "</select>";
      if (leg.m === "prop") {
        var players = [g.away, g.home].map(function (ab) {
          return '<optgroup label="' + esc(g.teams[ab].short) + '">' + (g.teams[ab].players || []).map(function (p) {
            return opt(p.id, p.name + " (" + p.pos + ")", leg.pid);
          }).join("") + "</optgroup>";
        }).join("");
        fields = market + '<select data-f="pid">' + players + "</select>" +
          '<select data-f="stat">' + Bets.statsFor(leg.pos || "").map(function (s) { return opt(s.key, s.label, leg.stat); }).join("") + "</select>" +
          '<select data-f="side">' + opt("over", "Over", leg.side) + opt("under", "Under", leg.side) + "</select>" +
          '<input data-f="line" type="number" step="0.5" value="' + esc(leg.line) + '" aria-label="Line">';
      } else {
        var scopes = leg.m === "spread" && data.sport === "nhl" ? ["game", "reg"] : Object.keys(Bets.SCOPES);
        var pick = "", side = "", line = "";
        if (leg.m === "win" || leg.m === "team" || leg.m === "spread") {
          pick = '<select data-f="t">' + opt("away", g.away + " (away)", leg.t) + opt("home", g.home + " (home)", leg.t) +
            (leg.m === "win" && leg.s !== "game" ? opt("draw", "Draw", leg.t) : "") + "</select>";
        }
        if (leg.m === "total" || leg.m === "team") side = '<select data-f="side">' + opt("over", "Over", leg.side) + opt("under", "Under", leg.side) + "</select>";
        if (leg.m === "btts") side = '<select data-f="side">' + opt("yes", "Yes", leg.side) + opt("no", "No", leg.side) + "</select>";
        if (leg.m === "total" || leg.m === "team" || leg.m === "spread") line = '<input data-f="line" type="number" step="0.5" value="' + esc(leg.line) + '" aria-label="Line">';
        fields = market + '<select data-f="s">' + scopes.map(function (k) { return opt(k, Bets.SCOPES[k], leg.s); }).join("") + "</select>" + pick + side + line;
      }
      return '<div class="bleg" data-i="' + i + '"><div class="bleg-fields">' + fields + odds + "</div>" +
        '<div class="bleg-out" data-out="' + i + '"></div>' +
        '<button class="bleg-x" data-act="remove" data-i="' + i + '" aria-label="Remove leg">' + ICON.x + "</button></div>";
    }

    function render() {
      box.innerHTML =
        '<div class="panel-head"><div><h3>Bet builder</h3><p class="hint">Add legs, type your odds, and see if the price is worth it.</p></div>' +
        '<div class="toggles">' +
        seg("sf-method", method, [["sim", "Simulation", "Simulates the game 20,000 times from both teams' scoring"], ["hist", "History", "Counts how often this bet won in the teams' real games"]]) +
        seg("sf-bwindow", bwin, [["recent", "Last " + recentN, "Each team's last " + recentN + " games"], ["season", "Season", "This season only"], ["both", "+ Last season", "This season plus last season's final games"]]) +
        seg("sf-odds", fmt, [["decimal", "2.50", "Decimal odds"], ["american", "+150", "American odds"]]) +
        "</div></div>" +
        '<div class="blegs">' + (B.legs.length ? B.legs.map(legRow).join("")
          : '<div class="bempty">No legs yet.</div>') + "</div>" +
        '<div class="bactions">' +
        '<button class="icon-btn" data-act="add">+ Add leg</button>' +
        (Bets.MARKETS.prop ? '<button class="icon-btn" data-act="addprop">+ Player prop</button>' : "") +
        '<button class="icon-btn" data-act="combo">+ My 3-leg combo</button>' +
        (B.legs.length ? '<button class="icon-btn ghost" data-act="clear">Clear</button>' : "") + "</div>" +
        (B.legs.length ? '<div class="bsum">' +
          '<label class="btotal">' + (B.legs.length > 1 ? "Parlay odds" : "Your odds") +
          '<input id="bTotal" inputmode="decimal" placeholder="' + (fmt === "american" ? "+175" : "2.75") + '" value="' + esc(oddsText(B.total)) + '"></label>' +
          '<div id="bOut" class="bout"></div></div>' : "") +
        '<details class="bhelp"><summary>How is this calculated?</summary><ul>' +
        "<li><b>Simulation</b> plays this matchup 20,000 times using both teams' scoring (my attack vs. their defense, per " +
        (isNFL(data) ? "quarter, in touchdowns and field goals" : "period") + "). Legs are checked together in the same simulated games, so related legs (a blowout + the over) are counted properly.</li>" +
        "<li><b>History</b> replays each team's real games as if they were tonight's game and counts how many would have won the bet.</li>" +
        "<li><b>Player props</b> use the player's own games this season (simulation: their average turned into a probability; history: how often they went over that line). They're combined with the other legs as if independent.</li>" +
        "<li><b>Favorable</b> means our estimate beats the chance your odds imply by at least " + Math.round(minEdge * 100) + "%. Confidence depends on how many games the estimate is based on.</li>" +
        "<li>Use .5 lines: a whole-number line can push.</li></ul></details>";
      update();
    }

    function estimate(legs) {
      var sim = Bets.simProb(legs, g, data.settings, bwin);
      var hist = Bets.histProb(legs, g, bwin, recentN);
      return { sim: sim, hist: hist };
    }
    function gamesUsed(ab) { return Bets.pickGames(g.teams[ab], bwin, recentN).length; }
    function histShort(h) { return h.n ? h.hits + "/" + h.n + " games" : "no games"; }

    function update() {
      B.legs.forEach(function (leg, i) {
        var el = box.querySelector('[data-out="' + i + '"]');
        if (!el) return;
        var e = estimate([leg]);
        var p = method === "sim" ? e.sim : e.hist.p;
        var v = Bets.verdict(p, leg.odds, minEdge);
        el.innerHTML = '<span class="bp num">' + pct(p) + "</span>" +
          '<span class="faint" title="' + (method === "sim" ? "History" : "Simulation") + '">' + (method === "sim" ? histShort(e.hist) : "sim " + pct(e.sim)) + "</span>" +
          (v ? '<span class="vpill v-' + v.call + '">' + (v.edge >= 0 ? "+" : "") + Math.round(v.edge * 100) + "%</span>" : "");
      });
      var out = document.getElementById("bOut");
      if (!out) return;
      var legOdds = B.legs.map(function (l) { return l.odds; });
      var product = legOdds.every(Boolean) ? legOdds.reduce(function (a, b) { return a * b; }, 1) : null;
      if (product && !B.total) document.getElementById("bTotal").placeholder = oddsText(product);
      var dec = B.total || product;

      var e = estimate(B.legs);
      var p = method === "sim" ? e.sim : e.hist.p;
      var n = method === "sim" ? Math.min(gamesUsed(g.home), gamesUsed(g.away)) : e.hist.n;
      var v = Bets.verdict(p, dec, minEdge);
      var other = method === "sim" ? "History " + pct(e.hist.p) + " (" + histShort(e.hist) + ")" : "Simulation " + pct(e.sim);

      out.innerHTML =
        '<div class="bline">' +
        '<div class="bnum"><span class="k">Our estimate</span><b class="num accent">' + pct(p) + "</b></div>" +
        '<span class="bvs">vs</span>' +
        '<div class="bnum"><span class="k">Your odds imply</span><b class="num">' + (v ? pct(v.implied) : "—") + "</b></div>" +
        (v ? '<div class="bverdict v-' + v.call + '"><b>' + v.call + "</b><span>" + (v.edge >= 0 ? "+" : "") + (v.edge * 100).toFixed(1) + "% edge</span></div>"
          : '<div class="bverdict"><span>Enter your odds</span></div>') +
        '<button class="icon-btn primary" data-act="save"' + (v ? "" : " disabled") + ">Save bet</button></div>" +
        '<div class="bmeta">Fair odds <b class="num">' + (p ? Bets.fromDecimal(1 / p, fmt) : "—") + "</b> · " + esc(other) +
        " · " + Bets.confidence(n) + " confidence</div>";
    }

    box.onchange = function (ev) {
      var f = ev.target.getAttribute("data-f");
      var row = ev.target.closest(".bleg");
      if (!f || !row || f === "odds" || f === "line") return;
      var i = +row.getAttribute("data-i"), leg = B.legs[i];
      if (f === "m") {
        B.legs[i] = Object.assign(Bets.defaults(ev.target.value, favSide), { odds: leg.odds });
        if (ev.target.value === "prop") setupProp(B.legs[i]);
      } else if (f === "pid") {
        setupProp(leg, ev.target.value);
      } else {
        leg[f] = ev.target.value;
        if (f === "stat") leg.line = Bets.propDefaultLine(g, leg);
        if (f === "s" && leg.t === "draw" && leg.s === "game") leg.t = favSide;
      }
      persistBuilder(g.id);
      render();
    };
    box.oninput = function (ev) {
      var f = ev.target.getAttribute("data-f");
      if (ev.target.id === "bTotal") {
        B.total = Bets.toDecimal(ev.target.value, fmt);
      } else if (f === "line" || f === "odds") {
        var i = +ev.target.closest(".bleg").getAttribute("data-i");
        if (f === "line") B.legs[i].line = ev.target.value === "" ? 0 : +ev.target.value;
        else B.legs[i].odds = Bets.toDecimal(ev.target.value, fmt);
      } else return;
      persistBuilder(g.id);
      update();
    };
    box.onclick = function (ev) {
      var segBtn = ev.target.closest(".seg button");
      if (segBtn) {
        var key = segBtn.parentNode.getAttribute("data-pref"), val = segBtn.getAttribute("data-v");
        setPref(key, val);
        if (key === "sf-method") method = val;
        if (key === "sf-odds") fmt = val;
        if (key === "sf-bwindow") bwin = val;
        render();
        return;
      }
      var btn = ev.target.closest("[data-act]");
      if (!btn) return;
      var act = btn.getAttribute("data-act");
      var cfg = data.settings.legs;
      if (act === "add") B.legs.push(Bets.defaults("win", favSide));
      if (act === "addprop") B.legs.push(setupProp(Bets.defaults("prop", favSide)));
      if (act === "combo") {
        B.legs = B.legs.concat(isNFL(data) ? [
          { m: "win", s: "game", t: favSide },
          { m: "total", s: "game", side: "over", line: (g.lines && g.lines.total) || cfg.total_line_fallback },
          { m: "team", s: "p1", t: favSide, side: "over", line: cfg.first_quarter_team_line },
        ] : [
          { m: "win", s: "game", t: favSide },
          { m: "total", s: "reg", side: "over", line: cfg.regulation_total_line },
          { m: "team", s: "p1", t: favSide, side: "over", line: cfg.first_period_team_line },
        ]);
      }
      if (act === "clear") { B.legs = []; B.total = null; }
      if (act === "remove") B.legs.splice(+btn.getAttribute("data-i"), 1);
      if (act === "save") return saveBet();
      persistBuilder(g.id);
      render();
    };

    function saveBet() {
      var legOdds = B.legs.map(function (l) { return l.odds; });
      var dec = B.total || (legOdds.every(Boolean) ? legOdds.reduce(function (a, b) { return a * b; }, 1) : null);
      var e = estimate(B.legs);
      var p = method === "sim" ? e.sim : e.hist.p;
      var v = Bets.verdict(p, dec, minEdge);
      var list = Bets.loadBets();
      list.push({
        id: Date.now().toString(36),
        sport: data.sport, date: data.date, gameId: g.id, home: g.home, away: g.away,
        legs: B.legs.map(function (l) { return Object.assign({}, l, { label: Bets.label(l, g.home, g.away) }); }),
        odds: dec, method: method, window: bwin, prob: p, probSim: e.sim, probHist: e.hist.p,
        call: v ? v.call : null, savedAt: new Date().toISOString(),
      });
      Bets.saveBets(list);
      updateBetCount();
      toast("Saved to My bets");
    }

    render();
  }

  function toast(msg) {
    var t = document.createElement("div");
    t.className = "toast";
    t.innerHTML = esc(msg) + ' <a href="#/bets">View</a>';
    document.body.appendChild(t);
    setTimeout(function () { t.classList.add("out"); }, 2200);
    setTimeout(function () { t.remove(); }, 2600);
  }
  function updateBetCount() {
    var el = document.getElementById("betCount");
    var n = Bets.loadBets().length;
    if (el) el.textContent = n ? n : "";
  }

  function condList(g) {
    return '<div class="conds">' + g.stomp.conditions.map(function (c) {
      return '<div class="cond' + (c.met ? " met" : "") + (c.weight < 0 ? " neg" : "") + '">' +
        '<span class="ck">' + (c.met ? (c.weight < 0 ? ICON.x : ICON.check) : "") + "</span>" +
        "<span>" + esc(c.label) + "</span>" +
        '<span class="cv">' + esc(c.value) + ' <span class="rule">(' + esc(c.rule) + ")</span></span>" +
        '<span class="cw">' + (c.weight > 0 ? "+" : "") + c.weight + "</span></div>";
    }).join("") + "</div>";
  }

  function compare(a, b, w, nfl) {
    var A = a[w] || {}, B = b[w] || {};
    var rows = nfl ? [
      ["Points for", A.gfPg, B.gfPg, true, 1],
      ["Points against", A.gaPg, B.gaPg, false, 1],
      ["Point diff", A.gdPg, B.gdPg, true, 1],
      ["Game total (both)", A.regTotalPg, B.regTotalPg, null, 1],
      ["Yards for", A.ydsFPg, B.ydsFPg, true, 0],
      ["Yards allowed", A.ydsAPg, B.ydsAPg, false, 0],
      ["Pass yds for", A.passFPg, B.passFPg, true, 0],
      ["Pass yds allowed", A.passAPg, B.passAPg, false, 0],
      ["Rush yds for", A.rushFPg, B.rushFPg, true, 0],
      ["Rush yds allowed", A.rushAPg, B.rushAPg, false, 0],
      ["Giveaways", A.toPg, B.toPg, false, 1],
      ["Takeaways", A.toAPg, B.toAPg, true, 1],
      ["Win %", A.winPct, B.winPct, true, "%"],
      ["Covers spread", A.atsPct, B.atsPct, true, "%"],
      ["Over posted total", A.overPct, B.overPct, null, "%"],
      ["Scores in Q1", A.scored1pPct, B.scored1pPct, true, "%"],
      ["Allows in Q1", A.conceded1pPct, B.conceded1pPct, false, "%"],
    ] : [
      ["Goals for", A.gfPg, B.gfPg, true, 2],
      ["Goals against", A.gaPg, B.gaPg, false, 2],
      ["Goal diff", A.gdPg, B.gdPg, true, 2],
      ["Reg. total (both)", A.regTotalPg, B.regTotalPg, null, 2],
      ["Shots for", A.sogFPg, B.sogFPg, true, 1],
      ["Shots against", A.sogAPg, B.sogAPg, false, 1],
      ["Win %", A.winPct, B.winPct, true, "%"],
      ["Scores in 1st", A.scored1pPct, B.scored1pPct, true, "%"],
      ["Allows in 1st", A.conceded1pPct, B.conceded1pPct, false, "%"],
      ["Over " + "% (reg.)", A.overPct, B.overPct, null, "%"],
      ["Power play", a.ppPct, b.ppPct, true, "%"],
      ["Penalty kill", a.pkPct, b.pkPct, true, "%"],
    ];
    var head = '<div class="cmp-head"><span><img src="' + esc(logo(a)) + '" alt="">' + esc(a.abbrev) + "</span><span></span>" +
      '<span class="r">' + esc(b.abbrev) + '<img src="' + esc(logo(b)) + '" alt=""></span></div>';
    return head + rows.map(function (r) {
      var va = r[1], vb = r[2];
      var max = Math.max(Math.abs(va || 0), Math.abs(vb || 0)) || 1;
      var fmt = function (v) { return r[4] === "%" ? pct(v) : num(v, r[4]); };
      var betterA = r[3] == null || va == null || vb == null ? false : (r[3] ? va > vb : va < vb);
      var betterB = r[3] == null || va == null || vb == null ? false : (r[3] ? vb > va : vb < va);
      var wa = Math.max(0, (va || 0) / max * 100), wb = Math.max(0, (vb || 0) / max * 100);
      return '<div class="cmp">' +
        '<div class="side l' + (betterA ? " better" : "") + '"><span class="v">' + fmt(va) + '</span><div class="track"><i style="width:' + wa + '%"></i></div></div>' +
        '<div class="lbl">' + esc(r[0]) + "</div>" +
        '<div class="side r' + (betterB ? " better" : "") + '"><div class="track"><i style="width:' + wb + '%"></i></div><span class="v">' + fmt(vb) + "</span></div></div>";
    }).join("");
  }

  function periodChart(a, b, w) {
    var A = (a[w] || {}).byPeriod || [], B = (b[w] || {}).byPeriod || [];
    var all = [];
    A.concat(B).forEach(function (p) { all.push(p.gf || 0, p.ga || 0); });
    var max = Math.max.apply(null, all.concat([0.5]));
    var bar = function (v, color, op) {
      return '<div style="height:' + ((v || 0) / max * 100) + "%;background:" + color + ";opacity:" + op + '"><span>' + num(v, 1) + "</span></div>";
    };
    var n = Math.max(A.length, B.length, 3), q = n === 4;
    var html = '<div class="periods" style="grid-template-columns:repeat(' + n + ',1fr)">' + A.map(function (_, i) { return i; }).map(function (i) {
      var pa = A[i] || {}, pb = B[i] || {};
      return '<div class="period"><div class="bars">' +
        bar(pa.gf, "var(--away)", 1) + bar(pa.ga, "var(--away)", 0.35) +
        '<div style="width:10px;background:none"></div>' +
        bar(pb.gf, "var(--home)", 1) + bar(pb.ga, "var(--home)", 0.35) +
        '</div><div class="lbl">' + (q ? "Q" + (i + 1) : ["1st", "2nd", "3rd"][i] + " period") + "</div></div>";
    }).join("") + "</div>";
    return html + '<div class="key">' +
      '<span><i style="background:var(--away)"></i>' + esc(a.abbrev) + " scored</span>" +
      '<span><i style="background:var(--away);opacity:.35"></i>' + esc(a.abbrev) + " allowed</span>" +
      '<span><i style="background:var(--home)"></i>' + esc(b.abbrev) + " scored</span>" +
      '<span><i style="background:var(--home);opacity:.35"></i>' + esc(b.abbrev) + " allowed</span></div>";
  }

  function gameRow(x, cfg, nfl) {
    var p = x.periods.map(function (q) { return "<b>" + q[0] + "</b>-" + q[1]; }).join(" · ");
    var tags;
    if (nfl) {
      var over = x.totalLine != null && x.regTotal > x.totalLine;
      var ats = x.spread != null && x.gf - x.ga + x.spread > 0;
      tags = '<span class="tag' + (over ? " on" : "") + '" title="Over the posted total ' + (x.totalLine || "") + '">O' + (x.totalLine || "") + "</span>" +
        '<span class="tag' + (ats ? " on" : "") + '" title="Covered the spread ' + (x.spread || "") + '">ATS</span>' +
        '<span class="tag' + (x.periods[0][0] > 0 ? " on" : "") + '">Q1</span>';
    } else {
      tags = '<span class="tag' + (x.regTotal > cfg.regulation_total_line ? " on" : "") + '">O' + cfg.regulation_total_line + "</span>" +
        '<span class="tag' + (x.periods[0][0] > cfg.first_period_team_line ? " on" : "") + '">1P</span>';
    }
    return '<div class="gl-row' + (x.prior ? " prior" : "") + '" title="' + esc(x.date) + '">' +
      '<span class="wl ' + (x.win ? "w" : x.gf === x.ga ? "" : "l") + '">' + (x.win ? "W" : x.gf === x.ga ? "T" : "L") + "</span>" +
      '<span class="sc">' + x.gf + "-" + x.ga + (x.end !== "REG" ? "<small>" + x.end + "</small>" : "") + "</span>" +
      '<span><span class="faint">' + (x.home ? "vs" : "@") + "</span> " + esc(x.opp) +
      ' <span class="pl">' + p + "</span></span>" +
      '<span class="tags">' + tags + "</span></div>";
  }
  function recentList(t, cfg, nfl) {
    var cur = t.lastGames.length ? t.lastGames[0].season : null;
    var rows = t.lastGames.map(function (x) {
      return gameRow(Object.assign({}, x, { prior: x.season !== cur }), cfg, nfl);
    }).join("");
    return '<div class="card panel"><h3><img src="' + esc(logo(t)) + '" alt="" style="width:22px;height:22px;display:inline-block;vertical-align:-5px;margin-right:6px">' +
      esc(t.short) + ' — last 10</h3><p class="hint">' + (nfl
        ? "Quarter scores shown as team-opponent · <b>O</b> = went over the posted total · <b>ATS</b> = covered the spread · <b>Q1</b> = scored in Q1 · faded = last season"
        : "Period scores shown as team-opponent · <b>O</b> = regulation over · <b>1P</b> = scored in the 1st · faded = last season") + "</p>" +
      '<div class="games-list">' + rows + "</div></div>";
  }

  function skaterTable(t, n) {
    var rows = t.skaters.map(function (p) {
      return "<tr><td><span class=\"pname\"><img src=\"" + esc(p.headshot) + "\" alt=\"\" loading=\"lazy\">" + esc(p.name) + " <small>" + esc(p.pos) + "</small></span></td>" +
        "<td>" + p.gp + "</td><td>" + p.g + "</td><td>" + p.a + "</td><td><b>" + p.pts + "</b></td><td>" + num(p.gpg, 2) + "</td>" +
        "<td class=\"" + (p.recentG >= 3 ? "hot" : "") + "\">" + p.recentG + "</td><td>" + p.recent1pG + "</td></tr>";
    }).join("");
    return '<div class="card panel"><h3>' + esc(t.short) + ' — top skaters</h3><p class="hint">Season totals · L' + n + " G = goals in last " + n + " team games · L" + n + " 1P = of those, in the 1st</p>" +
      '<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Player</th><th>GP</th><th>G</th><th>A</th><th>P</th><th>G/GP</th><th>L' + n + " G</th><th>L" + n + " 1P</th></tr></thead><tbody>" +
      rows + "</tbody></table></div></div>";
  }

  function goalieCard(t) {
    var rows = t.goalies.map(function (g, i) {
      return '<div class="goalie"><img src="' + esc(g.headshot) + '" alt="" loading="lazy"><div><div class="gn">' + esc(g.name) +
        (i === 0 ? '<span class="likely">Likely starter</span>' : "") + '</div><div class="gm">' + g.starts + " GS · " + esc(g.record) +
        " · " + num(g.gaa, 2) + " GAA" + (g.shutouts ? " · " + g.shutouts + " SO" : "") + '</div></div><div class="gs"><b>' +
        (g.savePct == null ? "—" : g.savePct.toFixed(3).replace(/^0/, "")) + "</b><div>SV%</div></div></div>";
    }).join("");
    return '<div class="card panel"><h3>' + esc(t.short) + ' — goalies</h3><p class="hint">"Likely" = most starts this season, not a confirmed starter.</p>' +
      (rows || '<p class="faint">No goalie data.</p>') + "</div>";
  }

  // ---------- My bets ----------
  function renderBets() {
    var list = Bets.loadBets().slice().reverse();
    var fmt = pref("sf-odds", "decimal");
    if (!list.length) {
      app.innerHTML = '<div class="card empty fade-in"><h3>No saved bets yet</h3><p class="muted">Open a game, build a bet in the Bet builder and hit “Save bet”. ' +
        "Results fill in automatically once the game is final and the data is refreshed.</p></div>";
      return;
    }
    loading();
    var dates = [];
    list.forEach(function (b) { var k = (b.sport || "nhl") + "/" + b.date; if (dates.indexOf(k) < 0) dates.push(k); });
    Promise.all(dates.map(function (d) {
      return load("data/" + d + ".json").catch(function () { return null; });
    })).then(function (files) {
      var byDate = {};
      dates.forEach(function (d, i) { byDate[d] = files[i]; });

      // Settle each bet against the real final score.
      list.forEach(function (b) {
        var day = byDate[(b.sport || "nhl") + "/" + b.date];
        var g = day && day.games.find(function (x) { return x.id === b.gameId; });
        var o = g && Bets.resultOutcome(g.result);
        b._game = g;
        if (!o) { b._status = "pending"; return; }
        b._legHits = b.legs.map(function (l) { return Bets.hits(l, o); });
        b._status = b._legHits.some(function (h) { return h === false; }) ? "lost"
          : b._legHits.some(function (h) { return h === null; }) ? "void" : "won";
        b._score = g.away + " " + g.result.awayScore + " – " + g.result.homeScore + " " + g.home;
      });

      function stats(rows) {
        var settled = rows.filter(function (b) { return b._status === "won" || b._status === "lost"; });
        var won = settled.filter(function (b) { return b._status === "won"; });
        var profit = settled.reduce(function (s, b) { return s + (b._status === "won" ? b.odds - 1 : -1); }, 0);
        return { n: rows.length, settled: settled.length, won: won.length, profit: profit, roi: settled.length ? profit / settled.length : null };
      }
      var all = stats(list), fav = stats(list.filter(function (b) { return b.call === "Favorable"; })),
        rest = stats(list.filter(function (b) { return b.call !== "Favorable"; }));
      function statCard(title, s) {
        return '<div class="card bstat-card"><div class="k">' + title + '</div><div class="v num" style="color:var(--' + (s.profit >= 0 ? "good" : "bad") + ')">' +
          (s.profit >= 0 ? "+" : "") + s.profit.toFixed(2) + 'u</div><div class="s">' + s.won + "/" + s.settled + " won" +
          (s.roi != null ? " · ROI " + (s.roi >= 0 ? "+" : "") + Math.round(s.roi * 100) + "%" : "") + (s.n - s.settled ? " · " + (s.n - s.settled) + " open/void" : "") + "</div></div>";
      }

      app.innerHTML = '<div class="fade-in">' +
        '<div class="datebar"><div><h1>My bets</h1><div class="sub">' + list.length + " saved · 1 unit per bet · stored in this browser</div></div></div>" +
        '<div class="cols-3" style="margin-top:18px">' + statCard("All bets", all) + statCard("Marked favorable", fav) + statCard("Everything else", rest) + "</div>" +
        '<p class="note">If “Marked favorable” keeps beating “Everything else” over many bets, the estimates are finding real value. A few dozen bets is still a small sample.</p>' +
        '<div class="stack">' + list.map(function (b) { return betCard(b, fmt); }).join("") + "</div></div>";

      app.querySelectorAll("[data-del]").forEach(function (btn) {
        btn.addEventListener("click", function () {
          var id = btn.getAttribute("data-del");
          if (!confirm("Delete this saved bet?")) return;
          Bets.saveBets(Bets.loadBets().filter(function (b) { return b.id !== id; }));
          updateBetCount();
          renderBets();
        });
      });
    }).catch(fail);
  }

  function betCard(b, fmt) {
    var t = b._game ? b._game.teams : null;
    var status = { pending: "Pending", won: "Won", lost: "Lost", void: "Void" }[b._status];
    var legs = b.legs.map(function (l, i) {
      var h = b._legHits ? b._legHits[i] : null;
      return '<li class="' + (h == null ? "" : h ? "hit" : "miss") + '">' + (h == null ? "•" : h ? "✓" : "✗") + " " + esc(l.label) + "</li>";
    }).join("");
    return '<div class="card betcard">' +
      '<div class="bc-head">' +
      (t ? '<img src="' + esc(logo(t[b.away])) + '" alt=""><img src="' + esc(logo(t[b.home])) + '" alt="">' : "") +
      '<div><a href="#/' + (b.sport || "nhl") + "/" + b.date + "/" + b.gameId + '" class="bc-title">' + esc(b.away) + " @ " + esc(b.home) +
      ' <span class="sport-tag">' + (b.sport || "nhl").toUpperCase() + "</span></a>" +
      '<div class="faint">' + (b._game ? new Date(b._game.startTimeUTC).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" })
        : esc(b.date)) + (b._score ? " · " + esc(b._score) : "") + "</div></div>" +
      '<span class="status s-' + b._status + '">' + status + "</span></div>" +
      '<ul class="bc-legs">' + legs + "</ul>" +
      '<div class="bc-foot">' +
      '<span>Odds <b class="num">' + Bets.fromDecimal(b.odds, fmt) + "</b></span>" +
      "<span>" + ({ sim: "Sim", hist: "History", trend: "Trend est." }[b.method] || "Est.") + ' <b class="num">' + pct(b.prob) + "</b></span>" +
      '<span>Implied <b class="num">' + pct(1 / b.odds) + "</b></span>" +
      (b.call ? '<span class="vpill v-' + b.call + '">' + b.call + "</span>" : "") +
      (b._status === "won" || b._status === "lost" ? '<span class="num" style="color:var(--' + (b._status === "won" ? "good" : "bad") + ')">' +
        (b._status === "won" ? "+" + (b.odds - 1).toFixed(2) : "−1.00") + "u</span>" : "") +
      '<button class="bc-del" data-del="' + esc(b.id) + '" aria-label="Delete bet">' + ICON.x + "</button></div></div>";
  }

  // ---------- NFL key players ----------
  function keyPlayers(t) {
    var line = function (p) {
      if (p.pos === "QB") return p.cmpPg + "/" + p.attPg + " · " + p.pass_ydsPg + " pass yds · " + p.pass_tdsPg + " TD · " + p.intsPg + " INT";
      if (p.pos === "RB") return p.carriesPg + " car · " + p.rush_ydsPg + " rush yds · " + p.recPg + " rec · " + (p.rush_tdsPg + p.rec_tdsPg).toFixed(1) + " TD";
      if (p.pos === "K") return p.fg_madePg + "/" + p.fg_attPg + " FG · " + p.pat_madePg + " XP · " + p.kick_ptsPg + " pts";
      return p.recPg + "/" + p.tgtPg + " rec/tgt · " + p.rec_ydsPg + " yds · " + p.rec_tdsPg + " TD";
    };
    var rows = (t.keyPlayers || []).map(function (p) {
      return '<div class="kp"><img src="' + esc(p.headshot) + '" alt="" loading="lazy"><div><div class="gn">' + esc(p.name) +
        ' <span class="pos">' + esc(p.pos) + '</span></div><div class="gm">' + line(p) + "</div></div>" +
        '<div class="gs"><b class="num">' + p.gp + "</b><div>GP</div></div></div>";
    }).join("");
    return '<div class="card panel"><h3>' + esc(t.short) + ' — key players</h3><p class="hint">Per-game averages this season (earlier weeks only).</p>' +
      (rows || '<p class="faint">No player data yet.</p>') + "</div>";
  }

  updateBetCount();
  route();
})();
