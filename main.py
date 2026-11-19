#!/usr/bin/env python3
"""Forecast GTA VI trophy earn rates on Exophase (make.fun program/v1 entry).

Model idea
----------
An earn rate is a ratio:  rate = earners / players_tracked.  The two parts
move for different reasons, so this program forecasts them separately and
recombines them:

* players_tracked grows as more profiles sync.  Newly tracked players have
  usually played less, so growth *dilutes* every rate.
* earners grow as tracked players keep playing and unlock more trophies, so
  progress *lifts* every rate.

Exophase only republishes its counts in steps, so the recorded history is a
staircase: long flat stretches punctuated by refresh jumps.  All fitting is
done on the distinct refresh steps (consecutive identical reads collapsed),
on a log-time axis (hours since launch day began), which makes the curves
saturate instead of blowing up over a multi-day horizon.  Per-trophy fits
are shrunk toward their tier's average fit, because a rare trophy's own
history is mostly noise.  The spread combines a random-walk term that grows
with the horizon, the observed size of refresh jumps, and fit residuals;
when the history shows no refresh at all yet, the spread stays wide and
leans downward, because the first refresh after a stale page tends to bring
a larger, less-progressed crowd of tracked players.

Everything is deterministic: no randomness, no clock, no network.  Standard
library only.
"""

import json
import math
import os
import sys
from datetime import datetime, timezone

# Quantile z-values for the five fixed levels, and the launch-day anchor the
# log-time axis is measured from.
Z = (-1.645, -0.674, 0.0, 0.674, 1.645)
ANCHOR = datetime(2026, 11, 19, 0, 0, 0, tzinfo=timezone.utc)

RATE_LO = 0.01   # percent; the page prints 2 decimals, 0 and 100 have no logit
RATE_HI = 99.99


def parse_time(text):
    """Parse an ISO-8601 UTC timestamp like 2026-11-19T03:00:00Z."""
    if not isinstance(text, str):
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def log_time(moment):
    """ln(1 + hours since launch day began); saturating time axis."""
    if moment is None:
        return 0.0
    hours = (moment - ANCHOR).total_seconds() / 3600.0
    return math.log1p(max(0.0, hours))


def clamp_rate(rate):
    return min(RATE_HI, max(RATE_LO, rate))


def logit(rate_pct):
    p = clamp_rate(rate_pct) / 100.0
    return math.log(p / (1.0 - p))


def inv_logit(x):
    if x >= 0:
        return 100.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return 100.0 * e / (1.0 + e)


def median(values):
    vals = sorted(values)
    n = len(vals)
    if n == 0:
        return None
    mid = n // 2
    if n % 2:
        return vals[mid]
    return 0.5 * (vals[mid - 1] + vals[mid])


def mean(values):
    vals = list(values)
    if not vals:
        return None
    return sum(vals) / len(vals)


def fit_line(xs, ys):
    """Least-squares (slope, intercept, residual_sd); None if unidentifiable."""
    n = len(xs)
    if n < 2:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 1e-12:
        return None
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    intercept = my - slope * mx
    if n >= 3:
        resid = [y - (intercept + slope * x) for x, y in zip(xs, ys)]
        sd = math.sqrt(sum(r * r for r in resid) / (n - 2))
    else:
        sd = 0.0
    return slope, intercept, sd


def refresh_steps(history):
    """Collapse consecutive identical reads into distinct refresh steps.

    A step is kept when the tracked-player count or any rate differs from
    the previous kept read.  Flat repeats carry no information about how
    rates move, and counting them would fake precision.
    """
    steps = []
    prev_key = None
    for read in history:
        if not isinstance(read, dict):
            continue
        rates = read.get("rates")
        if not isinstance(rates, list):
            rates = []
        key = (read.get("players_tracked"), tuple(rates))
        if steps and key == prev_key:
            continue
        steps.append(read)
        prev_key = key
    return steps


def last_rate_for(history, idx):
    """Most recent non-null rate for trophy position idx, or None."""
    for read in reversed(history):
        rates = read.get("rates") if isinstance(read, dict) else None
        if isinstance(rates, list) and idx < len(rates):
            value = rates[idx]
            if isinstance(value, (int, float)) and math.isfinite(value):
                return float(value)
    return None


def tier_fallback_rates(trophies, last_rates):
    """Median last rate per tier (and overall), for trophies with no rate."""
    by_tier = {}
    for trophy, rate in zip(trophies, last_rates):
        if rate is not None:
            by_tier.setdefault(trophy.get("tier"), []).append(rate)
    tier_med = {tier: median(vals) for tier, vals in by_tier.items()}
    overall = median([r for r in last_rates if r is not None])
    priors = {"platinum": 0.05, "gold": 0.3, "silver": 1.0, "bronze": 2.0}
    out = []
    for trophy, rate in zip(trophies, last_rates):
        if rate is not None:
            out.append(rate)
        elif tier_med.get(trophy.get("tier")) is not None:
            out.append(tier_med[trophy.get("tier")])
        elif overall is not None:
            out.append(overall)
        else:
            out.append(priors.get(trophy.get("tier"), 1.0))
    return out


def forecast_case(case):
    trophies = case.get("trophies") or []
    history = case.get("history") or []
    horizon_days = max(0.0, float(case.get("horizon_minutes") or 0.0)) / 1440.0
    target_moment = parse_time(case.get("target"))
    u_target = log_time(target_moment)

    steps = refresh_steps(history)
    n_steps = len(steps)

    # Per-step axes: log-time and log players.
    step_u = []
    step_players = []
    for read in steps:
        step_u.append(log_time(parse_time(read.get("at") or read.get("slot"))))
        players = read.get("players_tracked")
        step_players.append(float(players) if isinstance(players, (int, float)) and players > 0 else None)

    # --- How much will the tracked-player count grow by the target? -------
    known = [(u, math.log(p)) for u, p in zip(step_u, step_players) if p]
    last_players = known[-1][1] if known else None
    growth = None
    if len(known) >= 2 and last_players is not None:
        fit = fit_line([k[0] for k in known], [k[1] for k in known])
        if fit is not None:
            slope = min(2.5, max(0.0, fit[0]))
            growth = math.exp(slope * max(0.0, u_target - known[-1][0]))
    if growth is None:
        # No refresh observed yet: a stale page is due one.  Expect a modest
        # jump in tracked players, more so over a longer horizon.
        growth = 1.0 + 2.2 * min(1.0, horizon_days * 2.0)
    growth = min(8.0, max(1.0, growth))

    # --- How earned are newly tracked players, relative to incumbents? ---
    # At each refresh, (change in earners) / (change in players) is the new
    # cohort's earn rate; kappa is that rate relative to the rate before.
    kappas = []
    for s in range(1, n_steps):
        p0, p1 = step_players[s - 1], step_players[s]
        if not p0 or not p1 or p1 <= p0:
            continue
        r0 = steps[s - 1].get("rates") or []
        r1 = steps[s].get("rates") or []
        for i in range(min(len(r0), len(r1))):
            a, b = r0[i], r1[i]
            if not (isinstance(a, (int, float)) and isinstance(b, (int, float))):
                continue
            if a <= 0.05:
                continue
            marginal = (b * p1 - a * p0) / (p1 - p0)
            if marginal >= 0:
                kappas.append(marginal / a)
    kappa = median(kappas)
    if kappa is None:
        kappa = 0.35
    kappa = min(0.95, max(0.05, kappa))

    # --- Per-trophy earner growth (progress), shrunk toward tier means. --
    last_rates = [last_rate_for(history, i) for i in range(len(trophies))]
    base_rates = tier_fallback_rates(trophies, last_rates)

    earner_slopes = []   # slope of ln(incumbent earners + 0.5) on log-time
    resid_sds = []
    jump_changes = []    # pooled step-to-step logit changes, for spread
    for i in range(len(trophies)):
        xs, ys, logits = [], [], []
        # Earners among the *original* tracked cohort: total earners minus
        # what each refresh's newcomers brought in (at kappa x the then
        # rate).  Fitting progress on raw earners would count dilution's
        # mirror image twice, once here and once in the recombination.
        newcomers = 0.0
        prev_players = None
        prev_rate = None
        for s, read in enumerate(steps):
            rates = read.get("rates") or []
            players = step_players[s]
            if i >= len(rates) or players is None:
                continue
            rate = rates[i]
            if not (isinstance(rate, (int, float)) and math.isfinite(rate)):
                continue
            rate = float(rate)
            if prev_players is not None and prev_rate is not None and players > prev_players:
                newcomers += kappa * prev_rate / 100.0 * (players - prev_players)
            earners = max(0.0, rate) * players / 100.0
            incumbent = max(0.1, earners - newcomers)
            xs.append(step_u[s])
            ys.append(math.log(incumbent + 0.5))
            logits.append(logit(rate))
            prev_players = players
            prev_rate = rate
        for a, b in zip(logits, logits[1:]):
            jump_changes.append(b - a)
        fit = fit_line(xs, ys)
        if fit is None:
            earner_slopes.append(None)
            resid_sds.append(None)
        else:
            earner_slopes.append(fit[0])
            resid_sds.append(fit[2])

    tier_slope = {}
    for trophy, slope in zip(trophies, earner_slopes):
        if slope is not None:
            tier_slope.setdefault(trophy.get("tier"), []).append(slope)
    tier_slope = {t: mean(v) for t, v in tier_slope.items()}
    global_slope = mean([s for s in earner_slopes if s is not None])

    # --- Spread ingredients ----------------------------------------------
    span_days = 0.0
    if n_steps >= 2:
        first = parse_time(steps[0].get("at") or steps[0].get("slot"))
        last = parse_time(steps[-1].get("at") or steps[-1].get("slot"))
        if first and last:
            span_days = max(0.0, (last - first).total_seconds() / 86400.0)
    if n_steps >= 2 and span_days > 0:
        refresh_per_day = min(6.0, max(0.2, (n_steps - 1) / span_days))
    else:
        refresh_per_day = 1.0
    expected_refreshes = max(0.5, refresh_per_day * horizon_days)

    if len(jump_changes) >= 4:
        mj = mean(jump_changes)
        jump_sd = math.sqrt(sum((c - mj) ** 2 for c in jump_changes) / len(jump_changes))
        jump_sd = min(1.2, max(0.15, jump_sd))
    else:
        jump_sd = 0.55

    rw_sd = 0.45 * math.sqrt(horizon_days)
    jump_term = jump_sd * math.sqrt(expected_refreshes)
    # Staleness: few refresh steps seen, or a long time since the last one,
    # both mean the page is overdue a refresh whose jump is unobserved.
    step_stale = 1.0 if n_steps <= 1 else max(0.0, 1.0 - n_steps / 6.0)
    time_stale = 0.0
    case_moment = parse_time(case.get("at"))
    if steps and case_moment is not None:
        last_step_moment = parse_time(steps[-1].get("at") or steps[-1].get("slot"))
        if last_step_moment is not None:
            hours_since = max(0.0, (case_moment - last_step_moment).total_seconds() / 3600.0)
            time_stale = min(1.0, hours_since / 6.0)
    stale = max(step_stale, 0.8 * time_stale)
    sigma_floor = 0.35 + 0.45 * stale
    last_players_now = step_players[-1] if step_players else None
    resid_pool = median([r for r in resid_sds if r is not None]) or 0.0

    rows = []
    for i, trophy in enumerate(trophies):
        rate_now = base_rates[i]

        # Progress factor for this trophy's earners over the horizon.
        slope = earner_slopes[i]
        tier_mean = tier_slope.get(trophy.get("tier"), global_slope)
        if slope is None:
            slope = tier_mean if tier_mean is not None else 0.0
            weight = 0.0
        else:
            n_pts = sum(1 for read in steps
                        if isinstance(read.get("rates"), list)
                        and i < len(read["rates"])
                        and isinstance(read["rates"][i], (int, float)))
            weight = min(0.8, n_pts / (n_pts + 4.0))
        if tier_mean is not None:
            slope = weight * slope + (1.0 - weight) * tier_mean
        slope = min(1.6, max(-0.4, slope))
        u_last = step_u[-1] if step_u else log_time(parse_time(case.get("at")))
        progress = math.exp(slope * max(0.0, u_target - u_last)) - 1.0
        progress = min(3.0, max(-0.2, progress))

        # Recombine: incumbents progress, newcomers earn at kappa x rate.
        factor = ((1.0 + progress) + (growth - 1.0) * kappa) / growth
        median_rate = clamp_rate(rate_now * factor)

        # A trophy essentially everyone already has stays that way.
        if rate_now >= 99.5:
            median_rate = 99.9
            sigma_down = sigma_up = 0.25
        else:
            resid = resid_sds[i] if resid_sds[i] is not None else resid_pool
            # A rate built on a handful of earners is intrinsically jumpy:
            # one refresh can halve or double it.
            earners_now = rate_now * last_players_now / 100.0 if last_players_now else 25.0
            count_sd = min(0.5, 0.6 / math.sqrt(earners_now + 1.0))
            sigma = math.sqrt(rw_sd ** 2 + jump_term ** 2 + min(0.8, resid) ** 2 + count_sd ** 2)
            sigma = min(2.2, max(sigma_floor, sigma))
            sigma_down = min(2.6, sigma * (1.0 + 0.4 * stale))
            sigma_up = min(2.6, sigma * (1.0 + 0.1 * stale))

        centre = logit(median_rate)
        quantiles = []
        for z in Z:
            sd = sigma_down if z < 0 else sigma_up
            quantiles.append(round(clamp_rate(inv_logit(centre + z * sd)), 4))
        # Enforce non-decreasing after rounding.
        for k in range(1, len(quantiles)):
            if quantiles[k] < quantiles[k - 1]:
                quantiles[k] = quantiles[k - 1]
        rows.append({"id": trophy.get("id"), "q": quantiles})

    return {"trophies": rows}


def main():
    in_dir = os.environ.get("MAKEFUN_INPUT_DIR") or "/input"
    out_dir = os.environ.get("MAKEFUN_OUTPUT_DIR") or "/output"
    os.makedirs(out_dir, exist_ok=True)
    names = sorted(n for n in os.listdir(in_dir) if n.endswith(".in"))
    for name in names:
        with open(os.path.join(in_dir, name), "r", encoding="utf-8") as fh:
            case = json.load(fh)
        case_id = case.get("id") or name[:-3]
        result = forecast_case(case)
        text = json.dumps(result, separators=(",", ":"), ensure_ascii=False)
        with open(os.path.join(out_dir, f"{case_id}.out"), "w", encoding="utf-8") as fh:
            fh.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
