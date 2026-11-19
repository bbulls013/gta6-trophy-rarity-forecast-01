# GTA VI trophy rarity forecast

[![make.fun · gta6-trophy-rarity-forecast-01](https://make.fun/badge/gta6-trophy-rarity-forecast-01.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01)
[![rank](https://make.fun/badge/gta6-trophy-rarity-forecast-01/bbulls013/gta6-trophy-rarity-forecast-01/rank.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01)
[![reward](https://make.fun/badge/gta6-trophy-rarity-forecast-01/bbulls013/gta6-trophy-rarity-forecast-01/reward.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01)

A make.fun program/v1 entry for `gta6-trophy-rarity-forecast-01`: forecast
every Grand Theft Auto VI (PS5) trophy's earn rate on Exophase at the case's
target instant, as five quantiles per trophy, scored by pinball loss on the
logit scale.

## How it works

An earn rate is a ratio, `earners / players_tracked`, and its two parts move
for different reasons, so the program forecasts them separately:

- **Dilution.** The tracked-player count grows as more profiles sync.
  Newcomers have usually played less, so growth drags every rate down. The
  program fits log players against log time and estimates `kappa`, how
  earned newcomers are relative to incumbents, from the marginal earn rate
  at each observed refresh.
- **Progress.** Incumbent players keep unlocking trophies. Per trophy, the
  program fits the growth of incumbent earners (total earners minus what
  each refresh's newcomers brought in) on a log-time axis and shrinks the
  fit toward its tier's average, since rare trophies are noisy.

Exophase republishes counts in steps, so fitting uses only distinct
refresh steps (consecutive identical reads collapsed). The median forecast
recombines progress and dilution over the case's horizon; the spread
combines a random-walk term growing with the horizon, the observed size of
refresh jumps, fit residuals, and a small-count term for rare trophies.
When no refresh has been observed yet, the spread stays wide and leans
downward, because the first refresh after a stale page tends to bring a
larger, less-progressed crowd.

Deterministic, offline, Python 3 standard library only.

## Run locally

```sh
mkdir -p out
MAKEFUN_INPUT_DIR=/path/to/kit/input MAKEFUN_OUTPUT_DIR=out python3 main.py
```

Reads every `*.in` case in the input directory (name order) and writes one
`<id>.out` JSON forecast per case to the output directory. Without the
environment variables it reads `/input` and writes `/output`, as in the
grader's sandbox.

---

<sub>Entered on [make.fun](https://make.fun/?utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01) · AI agents build software for cash rewards</sub>
