# Early-Career Opportunity Agent — UMD Smith Finance, Class of 2030

Two scheduled jobs, one shared memory:

| Job | When | What it does |
|---|---|---|
| `agent.py daily` | 8:00 AM Eastern (DST-safe, once per day, catches up if delayed) | Full research across 5 topic batches → compares with **yesterday's digest** → emails a digest with **🆕 New today** shown separately, plus Urgent, Freshman/Early-Insight, Summer 2027, Deadline Watch, Updated, and a TODO checklist |
| `agent.py hourly` | every hour, 7 AM–10 PM Eastern | Lightweight check. **Emails only if something new appears** (never for sophomore-only items) |

## How "New today" works
* `state/catalog.json` remembers **every** opportunity ever seen. An item that a search happens to miss one day and
  find the next is **not** reported as new again (tested).
* `state/last_digest.json` is yesterday's digest snapshot. **New today = active today but not in yesterday's digest.**
  Items an hourly alert already caught since yesterday's digest still appear under New today, so nothing is lost.
* Programs auto-expire 7 days after their deadline; they stay in the catalog so they can't resurface as "new".
* `state/` ships pre-loaded with the 2026-10-04 baseline (relevant items only; see Run log).

## Relevance mode — `relevance_mode` in `config.json`
**Default: `"inclusive"`** (nothing is hidden because a program's class year is unclear — so you don't miss a real opportunity).

| | `inclusive` (default) | `strict` (opt-in) |
|---|---|---|
| Verified first-year (official page + evidence phrase, enforced in code) | ✅ shown | ✅ shown |
| Unverified, a source says first-years can apply | ✅ shown, marked ❓ | ✅ shown, marked ❓ |
| Unverified, class year unclear / no signal | ✅ shown, marked ❓ "confirm before applying" | ❌ hidden |
| Sophomore-only | 🔒 watch lists only — **never** urgent, freshman-eligible, TODO "review", or hourly alert | ❌ hidden |
| Research prompt | "do not drop possible opportunities" | "omit anything you can't tie to first-years" + retirement channel |

* Hourly alerts fire for any non-sophomore item that is new, **or** that just became alertable (e.g. a sophomore-only entry corrected
  to first-year eligible) — in both modes.
* `strict` also adds a one-time "Removed since last digest — reason" note when a listed program is retired. Switching modes is a
  one-word config change; hidden items are always remembered in `state/catalog.json`, so nothing re-appears as falsely "new".
* The `first_year_signal` tag (yes / no / unknown) is recorded in both modes, so you can switch to strict later with history intact.

## Setup (≈10 minutes, GitHub Actions)
1. Create a **private** GitHub repo and push this folder.
2. Repo → Settings → Secrets and variables → Actions → add `ANTHROPIC_API_KEY` and `AGENTMAIL_API_KEY`.
3. Repo → Settings → Actions → General → Workflow permissions → **Read and write** (state is committed back).
4. Test before trusting the schedule: run `daily-digest` and `hourly-check` from the Actions tab with **force** ticked.
5. Done. No GitHub? Use `crontab.example` on any always-on machine.

Local dry run (no email, no state writes): `python agent.py daily --dry-run --force`  (needs only ANTHROPIC_API_KEY)  
Offline demo: `python agent.py daily --dry-run --force --fixture sample_fixture.json`  (fixture items need `"first_year_signal": "yes"` to be shown)  
Tests: `python -m unittest tests.test_agent`

## Recipients (configurable)
```
python agent.py recipients list
python agent.py recipients add someone@example.com
python agent.py recipients remove someone@example.com
```
or set env `RECIPIENTS="a@x.com,b@y.com"` (e.g. as a repo variable). Each recipient gets their **own** message, so one bad
address can't block the others.

## Config knobs (`config.json`)
`digest_send_hour`, `urgent_days` (default 14), `watch_days`, `model`, `hourly.active_hours`, `hourly.enabled`,
`*.max_searches_per_batch`, and the `batches` list (add or remove topic areas).

## Known limits — please read
* **Handshake and HireSmith need a UMD login; the agent cannot read them.** Every digest's TODO list starts with
  "check them manually". Smith-exclusive postings will not appear unless they are also public.
* Research quality depends on web search. Aggregator blogs are often stale, so dates are labelled *verify*; a verified
  label requires an evidence quote, and the application link is only shown if search returned a real URL.
* **Cost:** web searches and tokens are billed. Hourly runs are the main cost driver (5 batches × 3 searches × ~15
  active hours/day). Reduce with `hourly.active_hours`, fewer `batches`, or by changing the hourly cron to every 2–3 hours.
* GitHub cron can run several minutes late, and GitHub may pause schedules on repos with long inactivity (state commits
  normally count as activity, but check the Actions tab occasionally).
* **Not yet exercised live:** the Anthropic and AgentMail calls were tested against mocks only (no keys in the build
  environment). Do step 4 above first. AgentMail note: an *unverified* AgentMail organization may only be able to email its
  attached human — if sends return 4xx, verify the org (`agent_verify`) in the AgentMail console.

## Run log
* **2026-10-03** — first digest (sent manually from chat; seeded the catalog).
* **2026-10-04** — one-off run. No `ANTHROPIC_API_KEY` was available, so research was done interactively with live web
  search and recorded in `runs/2026-10-04_manual_run.py`; diffing, eligibility guard, rendering and state handling are
  the agent's own code (real 8 AM guard applied, state tested on a sandbox copy, committed only after the email was
  delivered via AgentMail). Result: 9 new, 1 urgent, and three corrections to the 2026-10-03 digest
  (Bain Kickstart, Capital One AEIP, Goldman Virtual Insight timing). `state/` now holds the 10-04 baseline.
* `render_digest(..., notes=[...])` / `run_daily(..., notes=[...])` can print a "Corrections / notes — read first" banner.
* **2026-10-04 (later)** — refined to the strict first-year relevance filter (see above). `runs/2026-10-04_migrate_relevance.py`
  tagged the existing catalog and re-baselined `state/`: of 28 tracked programs, **11 are shown, 17 hidden** (13 sophomore-only,
  Bain ADvantage/BASE — graduate/MBA audience, and two T. Rowe Price summer tracks — upper-class requirements).
  `runs/2026-10-04_manual_run.py` predates the filter and is kept only as a historical record.
* **2026-10-04 (final)** — reverted to the previous inclusive behaviour as the default (strict filter kept as an opt-in switch).
  `runs/2026-10-04_revert_to_inclusive.py` re-baselined `state/` to the 27 items listed in the 10-04 email, so tomorrow's
  "New today" contains only genuinely new items. Known-weak entries remain visible but carry their caveat in "Why it fits"
  (Bain ADvantage/BASE targets graduate/MBA students; T. Rowe Price summer tracks have required upper-class graduation years).
