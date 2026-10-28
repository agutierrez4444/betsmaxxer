# BetsMaxxer

An NHL, NFL and NBA dashboard. It lists every game on the slate (by day for NHL and NBA,
by week for NFL), flags the ones that look like potential blowouts, and shows the stats you need to
judge them yourself. Each game page also has a bet builder to check your site's odds, plus
a Trends panel listing team and player bets that keep hitting.

Nothing here predicts results. It's historical numbers organized so you don't have to
look them up by hand.

## Run it locally

Double-click **`run.bat`**. It refreshes all three sports and opens http://localhost:8000.
Or run the steps yourself:

```bash
py -m pipeline.build
```

```bash
py -m pipeline.nfl_build
```

```bash
py -m pipeline.nba_build
```

```bash
py -m http.server 8000 -d site
```

- **NHL:** `--days-back 14` rebuilds the last 14 days. `--date 2026-10-20` builds one day.
- **NFL:** `--weeks-back 4` rebuilds earlier weeks. `--week 6` builds one week.
- **NBA:** same options as NHL (`--days-back`, `--date`).
- First runs download last season and the box scores once (a minute or two). After that,
  everything finished is cached in `.cache/`.

## Tuning: `config.yaml`

Everything adjustable lives in [config.yaml](config.yaml). It has an `nhl:`, an `nfl:` and an
`nba:` section. Edit it and re-run the build.

| Setting | What it does |
|---|---|
| `recent_games` | Size of the "last N games" window |
| `min_sample_games` | Early in the season, top up each team's sample with last season's games |
| `last_season_games` | How many of last season's games the builder's "+ Last season" toggle adds |
| `recent_weight` | How much recent form counts vs. the full season |
| `legs` | Lines for the default 3-leg combo |
| `trends` | `min_games`, `min_hit_rate`, `min_gap` (above the league norm), `max_per_game` |
| `builder.min_edge` | How big an edge must be before a price is called "Favorable" |
| `simulation` | Iterations, how much to steady small samples, overtime behaviour |
| `flag_tiers` | Strict, then Standard, then Loose. Looser tiers are only used if stricter ones flag too few |
| `conditions` | Each rule's `enabled`, `weight` and threshold. Negative weights subtract |

**Stomp score** = points from the conditions that are met ÷ total positive points × 100.
Each game is scored from both sides, and the higher side is the "stomp side".

**Default 3-leg combo:**
- **NHL:** favorite moneyline + regulation over 5.5 + favorite scores in the 1st period.
- **NFL:** favorite moneyline + over the posted total + favorite scores in the 1st quarter.
- **NBA:** favorite moneyline + over the posted total + favorite wins the 1st half.

NBA rules also look at back-to-backs, rest, the bookmaker's spread, and **today's injury
report**: a favorite whose top-2 scorer is out loses points, and an underdog missing one gains points.

## Bet builder

On every game page, add any legs:
- **Market:** Winner, Total, Team total, Spread / puck line, Both teams score (NHL)
- **Scope:** full game, regulation, a period (NHL), or a half or quarter (NFL, NBA)
- **Side and line:** over/under, home/away/draw, and the line

Type the odds from your betting site, in decimal or American. The builder shows:
- **Simulation:** each game simulated 20,000 times. NHL uses goals per period. NFL uses
  touchdowns and field goals per quarter, so scores look like real football scores. NBA uses
  points per quarter on a bell curve, with a shared pace swing so fast games lift both teams.
- **History:** how often the legs all hit in either team's real games.
- **Data window**, for both methods: last N games / this season / + last season.
- Implied probability, edge, EV, fair odds, confidence, and a verdict (Favorable / Close / Unfavorable).

## Trends

The game page lists bets that hit in at least 80% of this season's games **and** beat the
league norm by 25+ points. Comparing to the norm filters out obvious stuff like "QB throws
for 1+ yards". Only lines sportsbooks really offer are checked:

- **NHL players:** goals, assists, points, shots on goal; goalie saves
- **NFL players:** passing/rushing/receiving yards, receptions, completions, TDs, field goals, kicking points
- **NBA players:** points, rebounds, assists, threes, P+R+A and the other combos, steals + blocks
- **Teams:** scoring in a period/quarter, team totals, game totals, winning a period or half,
  scoring first (NHL), covering the spread and going over the posted total (NFL, NBA)

Each trend shows the hit count, the league norm, the current streak, and a matchup note
(e.g. "CGY allows 31.5 shots/gm, #1 most"). Type your odds to compare. The estimate is
pulled toward the norm, because short streaks overstate things. Team trends can be sent
to the builder, and any trend can be saved as a single bet.

**Click a trend** to open it: every game this season with the actual number, and an
"at least / at most" control to try other lines. Example: "Reichard 3+ field goals, 5/6"
→ set it to 4 and it shows 1/6, with the league norm and estimate recalculated for that line.

## My bets

Saved bets live in this browser. Results are graded automatically once the game is final
and the data has been refreshed, including player props (from box scores). The page
compares bets marked "Favorable" with everything else, which is the real test of whether
the estimates find value.

## How it works

```
NHL API / nflverse / ESPN  →  pipeline/ (Python)  →  site/data/<sport>/*.json  →  site/ (static dashboard)
```

- `pipeline/nhl_api.py`, `pipeline/nfl_api.py`, `pipeline/nba_api.py`: downloads, with caching.
- `pipeline/stats.py`: NHL game logs, period splits, averages, leg hit rates.
- `pipeline/scoring.py`, `pipeline/nfl_scoring.py`, `pipeline/nba_scoring.py`: flag conditions and tier logic.
- `pipeline/trends.py`: the trend engine (hit rates, league norms, filters), shared by all sports.
- `pipeline/nhl_trends.py`: the NHL list of props to check.
- `pipeline/build.py` (NHL), `pipeline/nfl_build.py` (NFL), `pipeline/nba_build.py` (NBA): tie it together and write the JSON.
- `site/`: plain HTML/CSS/JS with no build step. `bets.js` is the bet engine, `app.js` the pages.

Stats for a date or week only use games played **before** it. So past pages show what the
dashboard said beforehand, next to the actual result. That's how the "Flag track record"
panel works.

## Putting it online (free)

1. Create a GitHub repo and push this folder.
2. On vercel.com, import the repo. `vercel.json` already points it at `site/`.
3. That's it. `.github/workflows/refresh.yml` rebuilds all three sports every day at 9am and
   5pm Eastern and commits the data, and Vercel redeploys automatically.

## Data sources

- **NHL:** `api-web.nhle.com` (schedule with free moneyline odds, scores and goals by
  period, box scores, standings, player stats) and `api.nhle.com/stats` (PP% and PK%).
- **NFL:** [nflverse](https://github.com/nflverse) (schedules with closing moneylines,
  spreads and totals, rest days, weekly player and team stats) and ESPN's public scoreboard
  (quarter scores, logos).
- **NBA:** ESPN's public API (scoreboards with quarter scores and DraftKings lines, box
  scores, injury report). NBA.com's own stats site blocks automated requests, so it isn't used.

NHL "Likely starter" means the goalie with the most starts. Starters aren't confirmed in
advance. NFL stats update the night after games.
