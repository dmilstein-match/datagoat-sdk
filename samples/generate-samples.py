"""Generate the sample datasets, deterministically: three tables, one of every other shape, and
Parley's three raw exports (a billing table and two event logs) that dg_map turns into a record.

Each carries a REAL joint pattern — a combination of conditions that together predict the
outcome — because that is what the engine looks for and what a demo must actually find. A
sample that returns honest-empty is a worse first impression than no sample at all.

Seeded and pure: re-running produces byte-identical CSVs, so the committed fixtures can be
regenerated and diffed rather than trusted.
"""
import csv
import random
import sys

N = 800


def w(name, header, rows):
    path = f"{sys.argv[1]}/{name}.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        wr = csv.writer(f, lineterminator="\n")
        wr.writerow(header)
        wr.writerows(rows)
    pos = sum(1 for r in rows if str(r[-1]).lower() in ("yes", "true", "1"))
    print(f"{name}: {len(rows)} rows, outcome positive {pos} ({pos/len(rows):.1%})")


def saas_churn():
    rnd = random.Random(20260901)
    rows = []
    for i in range(N):
        tenure = rnd.randint(1, 48)
        tickets = rnd.randint(0, 12)
        logins = rnd.randint(0, 60)
        charges = round(rnd.uniform(20, 200), 2)
        plan = rnd.choice(["basic", "basic", "pro", "pro", "enterprise"])
        seats = rnd.randint(1, 50)
        # The joint pattern: new AND struggling AND disengaged.
        hot = tenure <= 24 and tickets >= 3 and logins <= 25
        p = 0.78 if hot else 0.05
        churned = "yes" if rnd.random() < p else "no"
        rows.append([f"cust_{i:04d}", tenure, charges, tickets, logins, plan, seats, churned])
    w("saas_churn",
      ["customer_id", "tenure_months", "monthly_charges", "support_tickets",
       "logins_last_30d", "plan", "seats", "churned"], rows)


def b2b_leads():
    rnd = random.Random(20260902)
    rows = []
    for i in range(N):
        pages = rnd.randint(0, 25)
        demo = rnd.choice(["yes", "no", "no"])
        size = rnd.choice([10, 25, 50, 100, 250, 500, 1000])
        days = rnd.randint(0, 30)
        industry = rnd.choice(["software", "finance", "retail", "healthcare", "manufacturing"])
        opens = rnd.randint(0, 20)
        hot = demo == "yes" and pages >= 6 and days <= 12
        p = 0.76 if hot else 0.05
        converted = "yes" if rnd.random() < p else "no"
        rows.append([f"lead_{i:04d}", pages, demo, size, days, industry, opens, converted])
    w("b2b_leads",
      ["lead_id", "pages_viewed", "demo_requested", "company_size",
       "days_to_first_touch", "industry", "email_opens", "converted"], rows)


def telco_churn():
    rnd = random.Random(20260903)
    rows = []
    for i in range(N):
        tenure = rnd.randint(1, 72)
        contract = rnd.choice(["month-to-month", "month-to-month", "one_year", "two_year"])
        support = rnd.choice(["yes", "no"])
        charges = round(rnd.uniform(18, 120), 2)
        internet = rnd.choice(["fiber", "dsl", "none"])
        total = round(charges * tenure, 2)
        hot = contract == "month-to-month" and support == "no" and tenure <= 36
        p = 0.77 if hot else 0.05
        churned = "yes" if rnd.random() < p else "no"
        rows.append([f"acct_{i:04d}", tenure, contract, support, charges, internet, total, churned])
    w("telco_churn",
      ["account_id", "tenure_months", "contract", "tech_support",
       "monthly_charges", "internet_service", "total_charges", "churned"], rows)


def agent_traces():
    """A labeled AGENT-RUNS table — the trace@1 demo. One row per run; `failed` is 0/1
    because trace@1 refuses to guess which token means the outcome occurred.

    TWO planted patterns, and the split between them is the lesson:
    - the `browser` tool DEGRADES mid-window (fails ~6% before day 12, ~54% after) — a
      time-shaped pattern that per-group PRIOR failure rates track and a static column
      average smears;
    - `planner` doing `refactor` fails ~34% throughout — a stable joint pattern.

    RANKED RAW (no reading) this table returns an honest non-finding: only 3 viable arms,
    and the engine needs 4. Reduced with `reading: {kind: "trace", version: 1}` the prior
    features push it to a high-tier clearing — that contrast IS the demo, and it is why the
    catalogue entry tells the caller to pass the reading."""
    rnd = random.Random(20260904)
    agents = ["planner", "researcher", "coder", "reviewer"]
    tasks = ["web_search", "summarize", "refactor", "classify", "extract"]
    tools = ["browser", "python", "retrieval", "shell"]
    rows = []
    for i in range(N):
        day = rnd.randint(0, 29)
        ts = f"2026-08-{day+1:02d}T{rnd.randint(0,23):02d}:{rnd.randint(0,59):02d}:00Z"
        agent, task, tool = rnd.choice(agents), rnd.choice(tasks), rnd.choice(tools)
        p = 0.06
        if tool == "browser" and day >= 12:
            p += 0.48
        if agent == "planner" and task == "refactor":
            p += 0.28
        failed = 1 if rnd.random() < p else 0
        rows.append([f"run_{i:04d}", ts, agent, task, tool,
                     int(rnd.lognormvariate(9.2, 0.6)), rnd.randint(300, 9000), failed])
    rows.sort(key=lambda r: r[1])
    w("agent_traces",
      ["run_id", "ts", "agent", "task", "tool", "duration_ms", "input_tokens", "failed"],
      rows)


def usage_panel():
    """An entity x period PANEL — the `panel@1` demo. 60 accounts, 12 monthly periods each.

    THE OUTCOME IS NOT IN THIS TABLE, and that is the point. There is no churn column to rank
    on; what a caller wants to know — "which accounts are on their way out" — is a SHAPE in the
    usage series, and `panel@1` with a `trend` label is how you say so. The engine runs
    Mann-Kendall on `usage` per account and derives the 0/1 itself.

    ONE COLUMN, TWO SOURCES, and that is the teaching point. `usage_declining` ships as a NOISY
    PER-PERIOD SELF-REPORT — an independent coin flip, the kind of hand-maintained flag a real
    CRM is full of. Ranked raw it is exactly what it looks like: nothing, an honest non-finding.
    Pass `panel@1` with a `trend` label and the ENGINE derives the same column from the shape of
    `usage` itself and overwrites it. Same question, same column name; one answer typed by a
    human and one computed from the series, and only the second one clears.

    Planted, for the reduced run: an account whose usage declines also runs hotter on support
    and lighter on seats, and is likelier to be on `basic`. Those become the features
    (`support_tickets__mean`, `seats__last`, `plan__last`, ...) that predict the derived trend
    label. `usage`'s OWN aggregates are excluded by the collapse — same-signal leakage — so
    nothing here predicts itself.
    """
    rnd = random.Random(20260905)
    accounts, periods = 800, 12
    rows = []
    for a in range(accounts):
        declining = rnd.random() < 0.28
        base_usage = rnd.randint(400, 1200)
        # DELIBERATE OVERLAP. An earlier cut drew seats/plan/adoption from disjoint ranges for
        # declining and healthy accounts, and the leak guard was right to flag it: a single
        # feature at AUC 0.96 is not a pattern, it is the answer written in another column. The
        # ranges below overlap heavily, so no ONE feature separates the classes and the engine has
        # to find the COMBINATION — which is the thing this sample exists to demonstrate.
        seats = rnd.randint(2, 22) if declining else rnd.randint(4, 40)
        plan = (rnd.choice(["basic", "basic", "pro", "enterprise"]) if declining
                else rnd.choice(["basic", "pro", "pro", "enterprise"]))
        slope = rnd.uniform(0.05, 0.11) if declining else rnd.uniform(-0.012, 0.012)
        base_logins = rnd.randint(12, 60)
        base_adoption = rnd.uniform(0.25, 0.80) - (0.12 if declining else 0.0)
        ticket_floor = 2 if declining else 0
        for p in range(periods):
            usage = int(base_usage * (1 - slope * p) + rnd.gauss(0, base_usage * 0.03))
            tickets = max(0, ticket_floor + rnd.randint(0, 7) + (1 if declining and p > 6 else 0))
            # Every one of these VARIES per period, which is what makes them real features after
            # the collapse. A column constant within an account collapses to four identical
            # aggregates and contributes one arm, not four — the reason an earlier cut of this
            # sample reduced to "1 viable arm, need 4".
            logins = max(0, int(base_logins * (1 - slope * p * 0.9) + rnd.gauss(0, 6)))
            adoption = round(min(1.0, max(0.0,
                base_adoption - slope * p * 0.5 + rnd.gauss(0, 0.09))), 3)
            days_since_login = max(0, rnd.randint(1, 14)
                                   + int(p * (0.9 if declining else 0.1)) + rnd.randint(-2, 3))
            rows.append([
                f"acct_{a:03d}", f"2026-{p + 1:02d}-01", max(usage, 0), tickets, logins,
                adoption, days_since_login, seats, plan,
                1 if rnd.random() < 0.12 else 0,
            ])
    w("usage_panel",
      ["account_id", "period", "usage", "support_tickets", "logins", "feature_adoption",
       "days_since_login", "seats", "plan", "usage_declining"], rows)


def sensor_stream():
    """A sensor STREAM plus fault intervals — the `stream@1` demo. 40 assets, 30 days, hourly.

    Four signals and, in the SAME table, the fault intervals: a row carrying an `event_type` is
    an interval, a row without one is a reading. One table, because `hs_rank_topk.data` takes
    exactly one source.

    Planted: on an asset that fails, vibration slopes UP and temperature's max climbs over the
    week before the fault. `stream@1` reduces the stream to daily snapshots with trailing
    1/3/7-day aggregates and labels a snapshot 1 when a fault starts within the next 3 days.

    Ranked RAW this is a wall of hourly readings with no per-entity structure and a
    `maintenance_due` flag that is noise — an honest non-finding. The reduction is what makes
    it answerable.
    """
    rnd = random.Random(20260906)
    # SIX-HOURLY, not hourly. 40 assets x 30 days of hourly readings is 28,800 rows and a 6 MB
    # JSON fixture — and this file is IMPORTED at module load, so every MCP cold start would
    # parse it. Four readings a day over thirty days still gives the 1/3/7-day trailing windows
    # real data to work with (12 / 28 observations per window against a floor of 3), and the
    # planted pattern survives it: the verification below still clears at lift 3.5 with a
    # confirmed holdout.
    assets, days, per_day = 40, 30, 4
    rows = []
    for a in range(assets):
        fails = rnd.random() < 0.45
        fail_day = rnd.randint(16, 27) if fails else None
        # A FALSE ALARM: an asset that heats up and shakes for a while and does not fail. Without
        # these, "the signal ramped" and "the asset failed" are the same event, every trailing
        # slope hits AUC 0.98, and the leak guard quarantines exactly the columns the sample
        # exists to show. They are also what a real fleet looks like.
        false_alarm = (not fails) and rnd.random() < 0.35
        alarm_day = rnd.randint(10, 24) if false_alarm else None
        ramp_days = rnd.randint(4, 11)
        gain_v, gain_t = rnd.uniform(1.1, 2.2), rnd.uniform(6.0, 13.0)
        v_base, t_base = rnd.uniform(0.8, 1.6), rnd.uniform(52, 70)
        noise_v, noise_t = rnd.uniform(0.16, 0.30), rnd.uniform(1.4, 2.6)
        for h in range(per_day * days):
            day = h // per_day
            ramp = 0.0
            onset = fail_day if fails else alarm_day
            if onset is not None and day >= onset - ramp_days:
                ramp = min((day - (onset - ramp_days)) / float(ramp_days), 1.0)
                ramp *= rnd.uniform(0.72, 1.0)     # the climb is ragged, not a clean line
            rows.append([
                f"asset_{a:03d}",
                f"2026-04-{day + 1:02d}T{(h % per_day) * (24 // per_day):02d}:00:00",
                round(v_base + gain_v * ramp + rnd.gauss(0, noise_v), 4),
                round(t_base + gain_t * ramp + rnd.gauss(0, noise_t), 3),
                round(rnd.uniform(40, 60) + rnd.gauss(0, 1.5), 3),
                round(rnd.uniform(0.9, 1.1) + rnd.gauss(0, 0.05), 4),
                1 if rnd.random() < 0.10 else 0,
                "", "", "",
            ])
        if fails:
            start = f"2026-04-{fail_day + 1:02d}T06:00:00"
            rows.append([
                f"asset_{a:03d}", start, "", "", "", "", "",
                "failure", start, f"2026-04-{fail_day + 1:02d}T10:00:00",
            ])
    rows.sort(key=lambda r: (r[1], r[0]))
    w("sensor_stream",
      ["asset_id", "ts", "vibration", "temp_c", "pressure_psi", "current_a",
       "maintenance_due", "event_type", "event_start", "event_end"], rows)


def customer_events():
    """An EVENT LOG, the `events` shape. 800 shop customers, one row per thing they did, over a
    year. There is no outcome column: the question "who has gone quiet?" is answered by the log
    itself. Read as events with `label: {lapsed: true}`, each customer becomes one row of
    activity counts, recency, per-type counts and transitions up to 90 days before the end of the
    log, and `lapsed_90d` is 1 when they did nothing in the last 90 days.

    Planted: a customer on their way out visits less over time, opens support tickets, and more
    often has a refund after a purchase. Healthy customers keep a steady rhythm. The overlap is
    deliberate, so no single count gives the answer away."""
    rnd = random.Random(20260906)
    start = 0          # day 0 = 2025-10-01; the log runs 365 days
    days = 365
    rows = []
    import datetime as _dt
    d0 = _dt.date(2025, 10, 1)
    for c in range(N):
        leaving = rnd.random() < 0.3
        rate = rnd.uniform(0.05, 0.16)                  # events per day at the start
        quit_day = rnd.randint(200, 270) if leaving else days + 1
        ticket_p = 0.10 if leaving else 0.03
        refund_p = 0.14 if leaving else 0.05
        for day in range(start, days):
            if day >= quit_day:
                # a leaving customer stops, except the odd straggler visit
                if rnd.random() < 0.002:
                    rows.append([f"cust_{c:04d}", (d0 + _dt.timedelta(days=day)).isoformat(), "visit", ""])
                continue
            r = rate * (1 - 0.6 * day / quit_day) if leaving else rate
            if rnd.random() >= r:
                continue
            ts = (d0 + _dt.timedelta(days=day)).isoformat()
            kind = rnd.choices(["visit", "purchase", "ticket"], [0.6, 0.35, ticket_p])[0]
            amount = round(rnd.lognormvariate(3.6, 0.5), 2) if kind == "purchase" else ""
            rows.append([f"cust_{c:04d}", ts, kind, amount])
            if kind == "purchase" and rnd.random() < refund_p:
                rows.append([f"cust_{c:04d}", ts, "refund", amount])
        # a healthy customer who happens to be quiet in the last quarter still reads as lapsed:
        # the label is a fact about the log, not about the plan behind it
    rows.sort(key=lambda r: (r[1], r[0]))
    w("customer_events", ["customer_id", "date", "event", "amount"], rows)


def store_weekly():
    """A TIME SERIES, the `series` shape. 60 stores, 60 weeks each, one row per store-week, with
    this week's metrics and whether the store ran out of stock the FOLLOWING week.

    Read as a series, each row gains trailing statistics of its own store's metrics over the
    last 4 and 12 weeks (mean, max, slope, change, last), still one row per store-week.
    Planted: a stockout follows falling stock cover and rising sales over the previous weeks,
    worse with late deliveries. Any one week's numbers say little; the trend says a lot."""
    rnd = random.Random(20260907)
    import datetime as _dt
    rows = []
    for s_ in range(60):
        base_sales = rnd.randint(300, 900)
        cover = rnd.uniform(2.0, 4.0)       # weeks of stock on hand
        drift = 0.0
        for wk in range(60):
            if rnd.random() < 0.08:
                drift = rnd.uniform(-0.18, -0.06)   # a supply squeeze begins
            elif rnd.random() < 0.12:
                drift = 0.0
            cover = max(0.2, min(5.0, cover + drift + rnd.gauss(0, 0.12)))
            sales = int(base_sales * (1 + 0.25 * max(0.0, 2.5 - cover) / 2.5) + rnd.gauss(0, base_sales * 0.06))
            late = rnd.randint(0, 3) + (2 if drift < 0 else 0)
            footfall = int(sales * rnd.uniform(3.5, 4.5))
            z = -3.2 + 1.3 * max(0.0, 1.6 - cover) + 0.35 * late + (0.8 if drift < -0.1 else 0.0)
            stockout = 1 if rnd.random() < 1 / (1 + pow(2.718281828, -z)) else 0
            week = (_dt.date(2025, 7, 7) + _dt.timedelta(weeks=wk)).isoformat()
            rows.append([f"store_{s_:02d}", week, sales, footfall, round(cover, 2), late, stockout])
    w("store_weekly", ["store_id", "week", "sales", "footfall", "stock_cover_weeks", "late_deliveries", "stockout_next_week"], rows)


def deal_stages():
    """Snapshots: 1,200 synthetic B2B deals, each seen as it entered three stages (the spine,
    deal_stages.csv), and the sales activity on each deal (the event log, deal_activity.csv).
    Winning goes with inbound interest and a recent meeting, together; a deal's outcome is the same
    on all three of its snapshots, which is why the snapshots shape holds out whole deals."""
    rnd = random.Random(20260924)
    import datetime as _dt
    t0 = _dt.datetime(2025, 1, 6, 9, 0)
    stages, events = [], []
    for d in range(1200):
        deal = f"deal_{d:04d}"
        engaged = rnd.random() < 0.45
        champion = rnd.random() < 0.5
        size = rnd.choice([12, 18, 24, 36, 48, 60, 96, 120]) * 1000
        t = t0 + _dt.timedelta(hours=rnd.randint(0, 24 * 330))
        snaps = []
        for stage in ("qualified", "demo", "proposal"):
            t = t + _dt.timedelta(hours=rnd.randint(48, 24 * 25))
            snaps.append((stage, t))
            for _ in range(rnd.randint(1, 3) + (rnd.randint(2, 5) if engaged else 0)):
                events.append([deal, (t - _dt.timedelta(hours=rnd.uniform(1, 24 * 28))).isoformat(timespec="minutes"), "inbound", ""])
            for _ in range(rnd.randint(2, 5)):
                events.append([deal, (t - _dt.timedelta(hours=rnd.uniform(1, 24 * 28))).isoformat(timespec="minutes"), "outbound", ""])
            if champion and rnd.random() < 0.8:
                events.append([deal, (t - _dt.timedelta(hours=rnd.uniform(1, 24 * 12))).isoformat(timespec="minutes"), "meeting", rnd.randint(2, 6)])
            elif rnd.random() < 0.25:
                events.append([deal, (t - _dt.timedelta(hours=rnd.uniform(1, 24 * 28))).isoformat(timespec="minutes"), "meeting", rnd.randint(1, 3)])
        z = -2.4 + (1.3 if engaged else 0.0) + (1.1 if champion else 0.0) + (0.9 if engaged and champion else 0.0) - 0.000006 * size
        won = 1 if rnd.random() < 1 / (1 + pow(2.718281828, -z)) else 0
        closed = t + _dt.timedelta(days=rnd.randint(5, 40))
        events.append([deal, closed.isoformat(timespec="minutes"), "closed_won" if won else "closed_lost", ""])
        for stage, at in snaps:
            stages.append([f"{deal}@{stage}", deal, stage, at.isoformat(timespec="minutes"), size, closed.isoformat(timespec="minutes"), won])
    events.sort(key=lambda e: (e[1], e[0]))
    w("deal_stages", ["snapshot_id", "deal_id", "stage", "entered_at", "amount", "closed_at", "won"], stages)
    with open(f"{sys.argv[1]}/deal_activity.csv", "w", newline="", encoding="utf-8") as f:
        wr = csv.writer(f, lineterminator="\n")
        wr.writerow(["deal_id", "ts", "activity", "attendees"])
        wr.writerows(events)
    print(f"deal_activity: {len(events)} rows")


#: Parley's log ends on this day: 90 days after the 4-week grid point 2025-09-01 (the grid runs every
#: 28 days from Monday 1970-01-05), so that point's window closes on the log's last day. A
#: cancellation (never on the last day) then has a closed first positive window unless the account's
#: first reading came under 90 days before it (3 accounts at the documented mapping: positives_open),
#: and the billing export's `status` restates the outcome to the entity-grain guard.
PARLEY_END = (2025, 11, 30)


def _poisson(rnd, lam):
    """A Poisson draw from random.Random (Knuth; lam is small here)."""
    if lam <= 0:
        return 0
    import math as _m
    limit, k, p = _m.exp(-lam), 0, 1.0
    while True:
        p *= rnd.random()
        if p <= limit:
            return k
        k += 1


def _hid(prefix, key, taken):
    """A shuffled, non-monotone text id (trap 9): the first 8 hex digits of sha256(key)."""
    import hashlib
    h = hashlib.sha256(key.encode("utf-8")).hexdigest()
    n = 8
    while f"{prefix}_{h[:n]}" in taken:
        n += 1
    out = f"{prefix}_{h[:n]}"
    taken.add(out)
    return out


def parley():
    """Parley, a synthetic AI support assistant sold to small companies: its three raw exports, the
    founder scenario's sources (spec v3 S12, §8). No outcome column anywhere: dg_map builds one.

    - parley_billing.csv (a table, one row per account): plan, billing_cycle, helpdesk, seats, mrr,
      status, signed_up_at, cancelled_at. `status` and `cancelled_at` are the export as it stood at
      the end, so they restate the outcome (the leak guard quarantines both); seats and mrr are
      numbers as of the export (excluded as_of_export); plan, billing_cycle and helpdesk are carried.
    - parley_events.csv (a log): account_id, ts, event in {ai_resolution, login, handoff}.
    - parley_tickets.csv (a log, part of billing): ticket_id, account_id, opened_at, topic in
      {how_to, bug, billing}.

    The world, a churny small-business book: accounts sign up from 2024-01-01 (a launch cohort in
    the first eight weeks, then a steady trickle) until the log ends on PARLEY_END, and about three
    in four of them cancel by then. An account on starter cancels more often than one on scale,
    annual billing and a zendesk helpdesk less often, a gorgias helpdesk and a poor fit (the
    assistant hands off more of its conversations) more often, and every account most often in its
    first 90 days. Most cancellations follow three to seven months of souring: logins fall first,
    then resolutions, while hand-offs and bug and billing tickets rise. One in five is abrupt (no warning),
    and some healthy accounts dip for a few months and recover (false alarms), so no one column
    gives the answer away. Account size varies widely, so a raw count says little on its own.

    Measured against the engine at dg_map's documented mapping (4w grid, 90-day horizon, outcome
    words "cancel"; docs/map.md pins the values): closed-row prevalence of cancelled_at about 0.12,
    inside the gate's [0.05, 0.80]; every cutoff of the default backtest graded. Seeded;
    byte-reproducible (LF).
    """
    import datetime as _dt
    rnd = random.Random(20260929)
    start = _dt.date(2024, 1, 1)
    end = _dt.date(*PARLEY_END)
    days = (end - start).days + 1
    taken = set()
    signups = sorted([rnd.randint(0, 55) for _ in range(360)]
                     + [d for d in range(56, days - 14) if rnd.random() < 0.7])
    rate28 = {"starter": 0.16, "growth": 0.11, "scale": 0.07}
    price = {"starter": 49, "growth": 149, "scale": 499}
    accounts, events, tickets = [], [], []
    for i, s_day in enumerate(signups):
        aid = _hid("acct", f"parley-account-{i}", taken)
        plan = rnd.choices(["starter", "growth", "scale"], [0.45, 0.35, 0.20])[0]
        annual = rnd.random() < {"starter": 0.10, "growth": 0.25, "scale": 0.50}[plan]
        helpdesk = rnd.choice(["zendesk", "intercom", "freshdesk", "gorgias"])
        poor_fit = rnd.random() < 0.30
        seats = {"starter": rnd.randint(1, 3), "growth": rnd.randint(3, 10), "scale": rnd.randint(8, 40)}[plan]
        volume = {"starter": 0.5, "growth": 0.9, "scale": 1.6}[plan] * rnd.lognormvariate(0.0, 0.45)
        # When the account starts to sour: a daily hazard from its plan, billing, fit and helpdesk,
        # doubled in its first 90 days (onboarding that never took). How it ends: abrupt (no warning),
        # or a decline of 90-210 days before it cancels, logins falling first.
        h = (rate28[plan] / 28.0 * (0.6 if annual else 1.0) * (1.8 if poor_fit else 1.0)
             * {"gorgias": 1.4, "zendesk": 0.85}.get(helpdesk, 1.0))
        early = rnd.expovariate(2.0 * h)
        sour = s_day + 20 + int(early if early < 90 else 90 + rnd.expovariate(h))
        abrupt = rnd.random() < 0.20
        decline = 0 if abrupt else rnd.randint(90, 210)
        cancel = sour + decline + (rnd.randint(0, 10) if abrupt else 0)
        floor = rnd.uniform(0.05, 0.35)
        cancelled = cancel <= days - 2          # never on the log's last day (see PARLEY_END)
        # A false alarm: a dip of 60-120 days to 40-70% of normal, then back.
        dip_at = s_day + 30 + int(rnd.expovariate(0.02 / 28.0))
        dip_len, dip_to = rnd.randint(60, 120), rnd.uniform(0.40, 0.70)
        stop = cancel if cancelled else days
        week_noise = 1.0
        for d in range(s_day, stop):
            if (d - s_day) % 7 == 0:
                week_noise = max(0.4, min(1.8, week_noise * 0.5 + 0.5 * rnd.lognormvariate(0.0, 0.2)))
            m = min(1.0, 0.5 + 0.5 * (d - s_day) / 21.0) * week_noise
            logins = m
            souring = 0.0
            if not abrupt and sour <= d < cancel:
                souring = (d - sour) / float(max(1, decline))
                m *= 1.0 - (1.0 - floor) * souring
                logins *= 1.0 - (1.0 - floor) * min(1.0, 1.6 * souring)
            if dip_at <= d < dip_at + dip_len:
                x = (d - dip_at) / float(dip_len)
                dip = 1.0 - (1.0 - dip_to) * (1.0 - abs(2.0 * x - 1.0))
                m *= dip
                logins *= dip
            share = min(0.6, (0.30 if poor_fit else 0.15) + 0.30 * souring)
            day = start + _dt.timedelta(days=d)
            for kind, lam in (("ai_resolution", volume * m * (1 - share) / 7.0),
                              ("handoff", volume * m * share / 7.0),
                              ("login", 0.45 * min(1.0, logins) / 7.0)):
                for _ in range(_poisson(rnd, lam)):
                    events.append([aid, f"{day.isoformat()}T{rnd.randint(7, 20):02d}:{rnd.randint(0, 59):02d}:00", kind])
            onboarding = d - s_day < 45
            t_rate = (0.05 + (0.25 if onboarding else 0.0) + (0.05 if poor_fit else 0.0) + 0.30 * souring) / 7.0
            for _ in range(_poisson(rnd, t_rate)):
                w_how = 0.45 if onboarding else 0.25
                w_bug = 0.40 + (0.15 if poor_fit else 0.0) + 0.6 * souring
                w_bill = 0.15 + 0.5 * souring
                topic = rnd.choices(["how_to", "bug", "billing"], [w_how, w_bug, w_bill])[0]
                tickets.append([None, aid, f"{day.isoformat()}T{rnd.randint(8, 18):02d}:{rnd.randint(0, 59):02d}:00", topic])
        status = "cancelled" if cancelled else ("past_due" if rnd.random() < 0.04 else "active")
        accounts.append([aid, plan, "annual" if annual else "monthly", helpdesk, seats,
                         seats * price[plan] // (3 if plan == "scale" else 1) * (10 if annual else 1) // (12 if annual else 1),
                         status, (start + _dt.timedelta(days=s_day)).isoformat(),
                         f"{(start + _dt.timedelta(days=cancel)).isoformat()}T{rnd.randint(9, 17):02d}:00:00" if cancelled else ""])
    events.sort(key=lambda r: (r[1], r[0], r[2]))
    tickets.sort(key=lambda r: (r[2], r[1], r[3]))
    for n, t in enumerate(tickets):
        t[0] = _hid("tkt", f"parley-ticket-{n}", taken)
    accounts.sort(key=lambda r: r[0])
    out = sys.argv[1]
    for name, header, rows in (
        ("parley_billing", ["account_id", "plan", "billing_cycle", "helpdesk", "seats", "mrr", "status", "signed_up_at", "cancelled_at"], accounts),
        ("parley_events", ["account_id", "ts", "event"], events),
        ("parley_tickets", ["ticket_id", "account_id", "opened_at", "topic"], tickets),
    ):
        with open(f"{out}/{name}.csv", "w", newline="", encoding="utf-8") as f:
            wr = csv.writer(f, lineterminator="\n")
            wr.writerow(header)
            wr.writerows(rows)
    first_year = sum(1 for a in accounts if a[8] and a[8][:10] < "2025-01-01")
    print(f"parley_billing: {len(accounts)} accounts, {sum(1 for a in accounts if a[8])} cancelled "
          f"({first_year} in 2024); parley_events: {len(events)} rows; parley_tickets: {len(tickets)} rows")


saas_churn()
b2b_leads()
telco_churn()
agent_traces()
usage_panel()
sensor_stream()
customer_events()
store_weekly()
deal_stages()

parley()
