#!/usr/bin/env python3
"""
Early-career opportunity agent for a UMD Smith Finance freshman (Class of 2030).

Modes
  python agent.py daily            8 AM digest: full research, diff vs. yesterday's digest, "New today"
  python agent.py hourly           lightweight check; emails ONLY if something new appears
  python agent.py recipients ...   list | add EMAIL | remove EMAIL   (edits config.json)

Environment
  ANTHROPIC_API_KEY   required for research
  AGENTMAIL_API_KEY   required for sending
  RECIPIENTS          optional comma-separated override of config.json recipients

Design notes
  * State lives in ./state (catalog.json, last_digest.json). The catalog remembers every
    opportunity ever seen, so search variance cannot make an item "disappear" and then
    re-appear as falsely "new".
  * "New today" = in today's active catalog but NOT in yesterday's digest snapshot. That
    includes items already flagged by an hourly alert since yesterday's digest.
  * RELEVANCE MODE (config.json "relevance_mode"):
      - "inclusive" (DEFAULT, nothing is hidden for unclear class year): everything not expired is listed. Verified and
        unverified items go in the main sections; sophomore-only programs appear only as 🔒 "not yet eligible" watch
        items and never as urgent, freshman-eligible or in hourly alerts.
      - "strict": only verified items, or unverified items with a first-year signal, are ever emailed; everything else is
        remembered silently, and a one-line "Removed since last digest" note explains anything that drops out.
  * "Verified freshman-eligible" is enforced in code: the model must supply an evidence quote
    that actually mentions first-year/freshman/Class of 2030; otherwise it is downgraded.
  * Handshake and HireSmith require a UMD login and cannot be searched; the TODO list always
    reminds the student to check them manually.
"""
import argparse
import datetime as dt
import difflib
import html
import json
import os
import re
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
AGENTMAIL_URL = "https://api.agentmail.to/v0/inboxes/{inbox}/messages/send"

VERIFIED, UNVERIFIED, SOPH_ONLY = "verified_first_year", "unverified", "sophomore_only"
FRESHMAN_RE = re.compile(r"first[- ]year|freshm[ae]n|class of 2030|2030|rising sophomore", re.I)
SOPH_RE = re.compile(r"sophomore|second[- ]year", re.I)
SIGNALS = ("yes", "no", "unknown")


def mode_of(cfg):
    return cfg.get("relevance_mode", "inclusive")


def is_relevant(o, mode="strict"):
    """What is eligible to be SHOWN. inclusive: everything. strict: first-year signal required."""
    if mode == "inclusive":
        return True
    return o.get("eligibility") == VERIFIED or (
        o.get("eligibility") == UNVERIFIED and o.get("first_year_signal") == "yes")


def alertable(o, mode="strict"):
    """What may trigger an hourly alert. Sophomore-only programs never do, in either mode."""
    if o.get("eligibility") == SOPH_ONLY:
        return False
    return is_relevant(o, mode)


# ───────────────────────── helpers ─────────────────────────
def load_json(path, default):
    p = Path(path)
    return json.loads(p.read_text()) if p.exists() else default


def save_json(path, data):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    tmp.replace(p)  # atomic


def load_config(path=CONFIG_PATH):
    cfg = json.loads(Path(path).read_text())
    if os.environ.get("RECIPIENTS"):
        cfg["recipients"] = [r.strip() for r in os.environ["RECIPIENTS"].split(",") if r.strip()]
    return cfg


def now_local(cfg):
    return dt.datetime.now(ZoneInfo(cfg["timezone"]))


def norm_key(company, program):
    s = f"{company}|{program}".lower()
    s = re.sub(r"\b20\d\d\b", " ", s)               # drop cycle years
    s = re.sub(r"\b(the|program|programme)\b", " ", s)
    s = re.sub(r"[^a-z0-9|]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return re.sub(r"\s*\|\s*", "|", s)             # no stray spaces around the separator


def parse_deadline(value):
    try:
        return dt.date.fromisoformat((value or "")[:10])
    except ValueError:
        return None


def days_until(deadline, today):
    d = parse_deadline(deadline)
    return None if d is None else (d - today).days


# ───────────────────────── eligibility guard ─────────────────────────
def apply_eligibility_guard(opp):
    """Never trust a 'verified' label without matching evidence text."""
    label = opp.get("eligibility", UNVERIFIED)
    evidence = (opp.get("eligibility_evidence") or "").strip()
    if label not in (VERIFIED, UNVERIFIED, SOPH_ONLY):
        label = UNVERIFIED
    if label == VERIFIED and not FRESHMAN_RE.search(evidence):
        label = UNVERIFIED
    if label != SOPH_ONLY and evidence and SOPH_RE.search(evidence) and not FRESHMAN_RE.search(evidence):
        label = SOPH_ONLY
    opp["eligibility"] = label
    return opp


def clean_opportunity(raw):
    keep = ["company", "program", "type", "location", "term", "eligibility", "eligibility_evidence",
            "deadline", "opens", "apply_url", "source", "why_fit", "category", "posted", "first_year_signal"]
    o = {k: (raw.get(k) or "") for k in keep}
    o["company"], o["program"] = o["company"].strip(), o["program"].strip()
    if not o["company"] or not o["program"]:
        return None
    sig = str(o["first_year_signal"]).lower().strip()
    sig = sig if sig in SIGNALS else "unknown"
    if o["eligibility"] == VERIFIED and sig == "unknown":
        sig = "yes"                      # model asserted first-year eligibility; the guard may downgrade evidence, not the claim
    o = apply_eligibility_guard(o)
    if o["eligibility"] == VERIFIED:
        sig = "yes"
    elif o["eligibility"] == SOPH_ONLY:
        sig = "no"
    o["first_year_signal"] = sig
    o["key"] = norm_key(o["company"], o["program"])
    return o


def dedupe(opps):
    """Exact-key and fuzzy (same company, >=0.9 similar program) de-duplication."""
    out = []
    for o in opps:
        dup = None
        for e in out:
            if e["key"] == o["key"]:
                dup = e
                break
            if norm_key(e["company"], "") == norm_key(o["company"], "") and \
                    difflib.SequenceMatcher(None, e["key"], o["key"]).ratio() >= 0.9:
                dup = e
                break
        if dup is None:
            out.append(o)
        else:  # fill gaps in the kept record
            for k, v in o.items():
                if not dup.get(k) and v:
                    dup[k] = v
    return out


# ───────────────────────── catalog / diff ─────────────────────────
def merge_into_catalog(catalog, opps, now_iso, mode="strict"):
    """Returns (new_keys, changed). Mutates catalog in place."""
    new_keys, changed = [], []
    for o in opps:
        k = o["key"]
        if k not in catalog:
            catalog[k] = {**o, "first_seen": now_iso, "last_seen": now_iso, "expired": False}
            new_keys.append(k)
            continue
        old, notes, was_rel = catalog[k], [], is_relevant(catalog[k], mode)
        if o["deadline"] and o["deadline"] != old.get("deadline"):
            notes.append(f"deadline {old.get('deadline') or 'unknown'} → {o['deadline']}")
        if o["eligibility"] != old.get("eligibility") and not (
                o["eligibility"] == UNVERIFIED and old.get("eligibility") == VERIFIED):
            notes.append(f"eligibility {old.get('eligibility')} → {o['eligibility']}")
        if o["apply_url"] and not old.get("apply_url"):
            notes.append("application link now available")
        for f, v in o.items():
            if f == "eligibility" and v == UNVERIFIED and old.get("eligibility") == VERIFIED:
                continue
            if f == "first_year_signal" and v == "unknown" and old.get("first_year_signal") in ("yes", "no"):
                continue
            if v:
                old[f] = v
        if old["eligibility"] == VERIFIED:
            old["first_year_signal"] = "yes"
        elif old["eligibility"] == SOPH_ONLY:
            old["first_year_signal"] = "no"
        old["last_seen"], old["expired"] = now_iso, False
        if notes and was_rel and is_relevant(old, mode):       # only report updates to items that are being shown
            changed.append({"key": k, "notes": notes})
    return new_keys, changed


def expire_old(catalog, today, grace_days=7):
    for it in catalog.values():
        d = parse_deadline(it.get("deadline"))
        if d and (today - d).days > grace_days:
            it["expired"] = True


def active_items(catalog, mode="strict"):
    """Not expired AND shown under the current relevance mode. Everything else is remembered but not listed."""
    return [v for v in catalog.values() if not v.get("expired") and is_relevant(v, mode)]


def removal_reason(it):
    if it.get("expired"):
        return "deadline has passed"
    if it.get("eligibility") == SOPH_ONLY or it.get("first_year_signal") == "no":
        why = (it.get("why_fit") or "").strip()
        return "not open to first-years" + (f" — {why[:170]}" if why else "")
    return "no longer confirmed as first-year relevant"


# ───────────────────────── research (Anthropic API + web search) ─────────────────────────
SYSTEM_STRICT = """You are a meticulous early-career research assistant. Student profile: {school}, major {major}, \
{class_year} (graduation window {grad}). Today is {today}.

GOAL: find REAL opportunities a FIRST-YEAR (Class of 2030) student can use: apply to now, or that will open before they \
finish their first year (by Aug 2027). Cover freshman/early insight, pre-internships, leadership programs, fellowships, \
externships, academic-year/part-time/remote/hybrid roles, and Spring/Summer/Fall 2027 internships OPEN TO FIRST-YEARS.

Rules:
1. STRICT RELEVANCE. Return ONLY opportunities where a source states first-years / freshmen / Class of 2030 (or graduation \
in {grad}) may apply. OMIT sophomore-only, junior/senior-only, MBA/graduate/post-doc, high-school-only, and anything whose \
class-year eligibility you cannot tie to first-years. Do not pad the list: fewer, correct items beat many doubtful ones.
2. first_year_signal: "yes" for every returned item. eligibility="verified_first_year" ONLY if an official/primary page \
explicitly allows first-years; paste a short supporting phrase in eligibility_evidence. Otherwise eligibility="unverified".
3. RETIREMENT CHANNEL: you will be given programs we currently track. If you find evidence that one of THEM is not open \
to first-years (sophomore-only, other class year, closed for good), return it with eligibility="sophomore_only", \
first_year_signal="no" and why_fit stating the evidence. Use this channel only for tracked programs.
4. Never invent URLs or dates. Use only URLs returned by your searches. Unknown deadline -> "". Use ISO dates (YYYY-MM-DD) \
for deadline; put estimates like "expected Feb 2027" in "opens".
5. {mode_rule}
6. Answer with ONE JSON object and nothing else: {{"opportunities":[{{"company","program","type","location","term",\
"eligibility","eligibility_evidence","first_year_signal","deadline","opens","apply_url","source","why_fit","category","posted"}}]}}
"""


SYSTEM_INCLUSIVE = """You are a meticulous early-career research assistant. Student profile: {school}, major {major}, \
{class_year} (graduation window {grad}). Today is {today}.

Find REAL, currently relevant opportunities (2026-27 academic year, Spring/Summer/Fall 2027, academic-year, part-time, \
remote/hybrid, pre-internships, freshman/early insight, leadership, fellowships, externships).

Rules:
1. Eligibility honesty. eligibility="verified_first_year" ONLY if an official or primary page explicitly allows \
first-year students / freshmen / Class of 2030 / graduating {grad}; paste that exact sentence in eligibility_evidence. \
If the page says sophomores / second-year only, use "sophomore_only". If you cannot confirm, use "unverified".
2. DO NOT DROP POSSIBLE OPPORTUNITIES. When class-year eligibility is unclear, INCLUDE the item as "unverified" with \
first_year_signal "unknown" (use "yes" if any source says first-years may apply, "no" if a source says they may not). \
Never omit something for lack of proof; the reader will verify.
3. Never invent URLs or dates. Use only URLs returned by your searches. Unknown deadline -> "". Use ISO dates (YYYY-MM-DD) \
for deadline; put estimates like "expected Feb 2027" in "opens".
4. Skip anything clearly closed, junior/senior-only, or requiring sponsorship the student lacks, unless it is a useful \
sophomore-year watch item (then label sophomore_only).
5. {mode_rule}
6. Answer with ONE JSON object and nothing else: {{"opportunities":[{{"company","program","type","location","term",\
"eligibility","eligibility_evidence","first_year_signal","deadline","opens","apply_url","source","why_fit","category","posted"}}]}}
"""
SYSTEMS = {"strict": SYSTEM_STRICT, "inclusive": SYSTEM_INCLUSIVE}


def extract_json(text):
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    blob = m.group(1) if m else text[text.find("{"): text.rfind("}") + 1]
    try:
        return json.loads(blob)
    except (json.JSONDecodeError, ValueError):
        return {"opportunities": []}


def call_anthropic(cfg, system, user, max_searches, max_tokens, session=requests):
    headers = {"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01",
               "content-type": "application/json"}
    messages = [{"role": "user", "content": user}]
    text = ""
    for _ in range(6):  # handle pause_turn continuations
        body = {"model": cfg["model"], "max_tokens": max_tokens, "system": system, "messages": messages,
                "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": max_searches}]}
        r = session.post(ANTHROPIC_URL, headers=headers, json=body, timeout=300)
        r.raise_for_status()
        data = r.json()
        text += "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        if data.get("stop_reason") != "pause_turn":
            break
        messages.append({"role": "assistant", "content": data["content"]})
    return text


def research(cfg, mode, today, session=requests, tracked=None):
    prof, sect = cfg["profile"], cfg["daily"] if mode == "daily" else cfg["hourly"]
    mode_rule = ("Be comprehensive for this topic batch." if mode == "daily" else
                 "NEW-POSTING CHECK: prioritise opportunities posted or updated in the last 48 hours.")
    mode = mode_of(cfg)
    system = SYSTEMS[mode].format(school=prof["school"], major=prof["major"], class_year=prof["class_year"],
                           grad=prof["graduation_window"], today=today.isoformat(), mode_rule=mode_rule)
    found = []
    for batch in cfg["batches"]:
        tracked_txt = ("\nCurrently tracked programs (retirement channel applies to these only):\n- " + "\n- ".join(tracked)
                       if (tracked and mode == "strict") else "")
        user = f"Topic batch: {batch['name']}\nCover: {batch['topics']}{tracked_txt}\nReturn the JSON object now."
        try:
            text = call_anthropic(cfg, system, user, sect["max_searches_per_batch"], sect["max_tokens"], session)
        except requests.RequestException as e:
            print(f"[warn] batch '{batch['name']}' failed: {e}", file=sys.stderr)
            continue
        for raw in extract_json(text).get("opportunities", []):
            o = clean_opportunity(raw) if isinstance(raw, dict) else None
            if o:
                found.append(o)
    return dedupe(found)


# ───────────────────────── email rendering ─────────────────────────
BADGE = {VERIFIED: "✅ First-year eligible (evidence on file)",
         UNVERIFIED: "❓ Eligibility unverified — confirm before applying",
         SOPH_ONLY: "🔒 Sophomore-only (not yet eligible)"}


def classify(items, new_keys, today, cfg):
    urgent, watch = [], []
    for it in items:
        du = days_until(it.get("deadline"), today)
        if du is not None and 0 <= du <= cfg["urgent_days"] and it["eligibility"] != SOPH_ONLY:
            urgent.append((du, it))
        elif (du is not None and cfg["urgent_days"] < du <= cfg["watch_days"]) or (du is None and it.get("opens")):
            watch.append((du if du is not None else 9999, it))
    sec = {
        "urgent": [i for _, i in sorted(urgent, key=lambda x: x[0])],
        "new": sorted([i for i in items if i["key"] in new_keys],        # verified first, sophomore-only last
                      key=lambda i: (i["eligibility"] == SOPH_ONLY, i["eligibility"] != VERIFIED, i["company"].lower())),
        "freshman": sorted([i for i in items if i["eligibility"] != SOPH_ONLY],
                           key=lambda i: (i["eligibility"] != VERIFIED, i["company"].lower())),
        "summer": [i for i in items if re.search(r"summer.*2027|2027.*summer", f"{i['term']} {i['type']}", re.I)],
        "watch": [i for _, i in sorted(watch, key=lambda x: x[0])],
    }
    return sec


def todo_list(sec, today):
    todo = ["Log into Handshake and HireSmith (UMD login required — the agent cannot read them) and search: "
            "freshman, first-year, insight, fellowship, externship"]
    for it in sec["urgent"]:
        du = days_until(it["deadline"], today)
        todo.append(f"⏰ {it['company']} — {it['program']}: confirm eligibility & apply (due {it['deadline']}, "
                    f"{'TODAY' if du == 0 else f'in {du} day(s)'})")
    for it in sec["new"]:
        if it not in sec["urgent"] and it["eligibility"] != SOPH_ONLY:      # no noise for items you can't apply to yet
            todo.append(f"🆕 Review new: {it['company']} — {it['program']}")
    for it in sec["freshman"]:
        if it["eligibility"] == UNVERIFIED and it not in sec["urgent"]:
            todo.append(f"Verify first-year eligibility on the official page: {it['company']} — {it['program']}")
            if len(todo) > 14:
                break
    todo.append("Check the UMD Smith Office of Career Services page for first-year events this week")
    return todo


def _row_text(it, flag=""):
    link = it.get("apply_url") or "(no verified link yet — search the company careers site)"
    dl = it.get("deadline") or (f"timing: {it['opens']}" if it.get("opens") else "deadline unknown")
    return (f"{flag}{it['company']} — {it['program']}\n"
            f"    Type: {it.get('type') or 'n/a'} | Term: {it.get('term') or 'n/a'} | Location: {it.get('location') or 'n/a'}\n"
            f"    Eligibility: {BADGE[it['eligibility']]}\n"
            f"    Deadline: {dl} | Source: {it.get('source') or 'n/a'}\n"
            f"    Why it fits: {it.get('why_fit') or 'n/a'}\n    Apply: {link}")


def _row_html(it, flag=""):
    e = html.escape
    link = it.get("apply_url")
    a = f'<a href="{e(link)}">{e(link)}</a>' if link else "<i>no verified link yet — search the company careers site</i>"
    dl = it.get("deadline") or (f"timing: {it['opens']}" if it.get("opens") else "deadline unknown")
    color = {VERIFIED: "#2e7d32", UNVERIFIED: "#b45309", SOPH_ONLY: "#6b7280"}[it["eligibility"]]
    return (f'<div style="margin:0 0 12px;padding:8px 10px;border-left:4px solid {color};background:#fafafa">'
            f'<b>{flag}{e(it["company"])} — {e(it["program"])}</b><br>'
            f'<span style="color:{color}">{e(BADGE[it["eligibility"]])}</span><br>'
            f'{e(it.get("type") or "n/a")} | {e(it.get("term") or "n/a")} | {e(it.get("location") or "n/a")}<br>'
            f'<b>Deadline:</b> {e(dl)} | <b>Source:</b> {e(it.get("source") or "n/a")}<br>'
            f'<b>Why:</b> {e(it.get("why_fit") or "n/a")}<br><b>Apply:</b> {a}</div>')


def render_digest(sec, changed, catalog, today, cfg, stats, notes=None, removed=None):
    def flag_for(it):
        du = days_until(it.get("deadline"), today)
        f = ""
        if it["key"] in {i["key"] for i in sec["new"]}:
            f += "🆕 NEW TODAY · "
        if du is not None and 0 <= du <= cfg["urgent_days"] and it["eligibility"] != SOPH_ONLY:
            f += f"⏰ DUE {'TODAY' if du == 0 else f'IN {du}D'} · "
        return f

    blocks = [
        ("⏰ URGENT DEADLINES (next %d days)" % cfg["urgent_days"], sec["urgent"], "None in the next %d days." % cfg["urgent_days"]),
        ("🆕 NEW TODAY (not in yesterday's digest)", sec["new"], "Nothing new since yesterday's digest."),
        ("🎓 FRESHMAN / EARLY-INSIGHT (Class of 2030)", sec["freshman"], "None found."),
        ("☀️ SUMMER 2027", sec["summer"], "None found."),
        ("👀 DEADLINE WATCH", sec["watch"], "Nothing upcoming."),
    ]
    todo = todo_list(sec, today)
    changed_lines = [f"{catalog[c['key']]['company']} — {catalog[c['key']]['program']}: " + "; ".join(c["notes"])
                     for c in changed if c["key"] in catalog]
    title = (f"Early-Career Digest — {today.strftime('%a %b %d, %Y')} — "
             f"{len(sec['new'])} new, {len(sec['urgent'])} urgent")
    if mode_of(cfg) == "strict":
        cav = ("Handshake/HireSmith are login-gated and not searched. Only programs with a first-year (Class of 2030) "
               "signal are listed — sophomore-only, upper-class and graduate programs are filtered out. Third-party sources "
               "may be stale: treat every date as 'verify before relying'.")
    else:
        cav = ("Handshake/HireSmith are login-gated and not searched. Nothing is hidden for unclear class year: items marked ❓ "
               "are unverified — confirm on the official page. Sophomore-only programs appear only as 🔒 watch items, never as "
               "eligible. Third-party sources may be stale: treat every date as 'verify before relying'.")

    text = [title.upper(), "=" * len(title), f"Caveat: {cav}", f"Run stats: {stats}", ""]
    if notes:
        text += ["=== ⚠ CORRECTIONS / NOTES — READ FIRST ==="] + [f"! {n}" for n in notes] + [""]
    h = [f'<div style="font-family:Arial,sans-serif;max-width:760px"><h2>{html.escape(title)}</h2>'
         f'<p style="color:#555;font-size:13px"><i>{html.escape(cav)}<br>Run stats: {html.escape(stats)}</i></p>']
    shown = set()
    if notes:
        h.append('<div style="background:#ffe8cc;border:1px solid #f59e0b;padding:8px 12px;margin:8px 0"><b>⚠ Corrections / notes — read first</b><ul>'
                 + "".join(f"<li>{html.escape(n)}</li>" for n in notes) + "</ul></div>")
    for head, items, empty in blocks:
        t_rows, h_rows = [], []
        for i in items:
            if i["key"] in shown:       # already printed in full above -> compact reference
                ref = f"• {i['company']} — {i['program']} ({BADGE[i['eligibility']].split(' ')[0]} see full details above)"
                t_rows.append(ref)
                h_rows.append(f'<div style="margin:0 0 6px;color:#555">{html.escape(ref)}</div>')
            else:
                shown.add(i["key"])
                t_rows.append(_row_text(i, flag_for(i)))
                h_rows.append(_row_html(i, flag_for(i)))
        text += [f"=== {head} ({len(items)}) ==="] + (t_rows or [empty]) + [""]
        banner = ' style="background:#fff3cd;padding:6px"' if head.startswith(("⏰", "🆕")) and items else ""
        h.append(f"<h3{banner}>{html.escape(head)} ({len(items)})</h3>" + ("".join(h_rows) or f"<p><i>{empty}</i></p>"))
    if changed_lines:
        text += ["=== UPDATED SINCE LAST CHECK ==="] + [f"- {c}" for c in changed_lines] + [""]
        h.append("<h3>🔄 Updated since last check</h3><ul>" + "".join(f"<li>{html.escape(c)}</li>" for c in changed_lines) + "</ul>")
    removed = removed or []
    if removed:
        text += ["=== REMOVED SINCE LAST DIGEST (no longer relevant for freshmen) ==="] + [f"- {r}" for r in removed] + [""]
        h.append("<h3>🗑 Removed since last digest (no longer relevant for freshmen)</h3><ul>"
                 + "".join(f"<li>{html.escape(r)}</li>" for r in removed) + "</ul>")
    text += ["=== DAILY TODO CHECKLIST ==="] + [f"[ ] {t}" for t in todo]
    h.append("<h3>✅ Daily TODO checklist</h3>" + "".join(f"<div>☐ {html.escape(t)}</div>" for t in todo) + "</div>")
    return title, "\n".join(text), "".join(h)


def render_alert(new_items, today, cfg):
    first = new_items[0]
    extra = f" (+{len(new_items) - 1} more)" if len(new_items) > 1 else ""
    subject = f"🆕 New opportunity: {first['company']} — {first['program']}{extra}"
    text = [subject, "", "Newly detected since the last check (verify details on the official page):", ""]
    h = [f'<div style="font-family:Arial,sans-serif;max-width:760px"><h2 style="background:#fff3cd;padding:6px">{html.escape(subject)}</h2>']
    for it in new_items:
        du = days_until(it.get("deadline"), today)
        flag = "🆕 NEW · " + (f"⏰ DUE IN {du}D · " if du is not None and 0 <= du <= cfg["urgent_days"] else "")
        text += [_row_text(it, flag), ""]
        h.append(_row_html(it, flag))
    h.append("</div>")
    return subject, "\n".join(text), "".join(h)


# ───────────────────────── sending ─────────────────────────
def send_email(cfg, subject, text, html_body, session=requests):
    """One message per recipient so a single bad address can't block the others."""
    key = os.environ["AGENTMAIL_API_KEY"]
    url = AGENTMAIL_URL.format(inbox=cfg["agentmail_inbox_id"])
    results = {}
    for rcpt in cfg["recipients"]:
        r = session.post(url, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                         json={"to": [rcpt], "subject": subject, "text": text, "html": html_body}, timeout=60)
        results[rcpt] = r.status_code
        if r.status_code >= 300:
            print(f"[error] send to {rcpt} failed: {r.status_code} {r.text[:300]}", file=sys.stderr)
    return results


# ───────────────────────── run modes ─────────────────────────
def should_run_digest(now, last_digest_date, send_hour):
    """Catch-up semantics: runs once per local day, at/after send_hour (survives DST + delayed cron)."""
    return now.hour >= send_hour and last_digest_date != now.date().isoformat()


def run_daily(cfg, state_dir, dry_run=False, force=False, fixture=None, session=requests, now=None, notes=None):
    now = now or now_local(cfg)
    today = now.date()
    state_dir = Path(state_dir)
    last = load_json(state_dir / "last_digest.json", {"date": None, "keys": []})
    if not force and not should_run_digest(now, last["date"], cfg["digest_send_hour"]):
        print(f"[skip] digest not due (local {now:%Y-%m-%d %H:%M}, last sent {last['date']})")
        return None
    catalog = load_json(state_dir / "catalog.json", {})
    mode = mode_of(cfg)
    tracked = [f"{i['company']} — {i['program']}" for i in active_items(catalog, mode)]
    opps = fixture if fixture is not None else research(cfg, "daily", today, session, tracked)
    if not opps and not catalog:
        raise RuntimeError("Research returned nothing and catalog is empty — refusing to send a blank digest.")
    new_in_catalog, changed = merge_into_catalog(catalog, opps, now.isoformat(timespec="minutes"), mode)
    expire_old(catalog, today)
    items = active_items(catalog, mode)
    cur_keys = {i["key"] for i in items}
    new_today = cur_keys - set(last["keys"])                       # vs. YESTERDAY'S DIGEST (relevant items only)
    gone = [catalog[k] for k in sorted(set(last["keys"]) - cur_keys) if k in catalog]
    removed = [f"{g['company']} — {g['program']}: {removal_reason(g)}" for g in gone]
    sec = classify(items, new_today, today, cfg)
    stats = (f"{len(opps)} found this run, {len(items)} tracked, {len(new_today)} new and "
             f"{len(removed)} removed vs. last digest ({last['date'] or 'first run'})")
    subject, text, body = render_digest(sec, changed, catalog, today, cfg, stats, notes, removed)
    subject = ("🆕 " if sec["new"] else "") + subject
    if dry_run:
        Path(ROOT / "preview").mkdir(exist_ok=True)
        (ROOT / "preview" / f"digest_{today}.html").write_text(body)
        print(subject, "\n", stats)
        return {"subject": subject, "sections": sec, "text": text}
    res = send_email(cfg, subject, text, body, session)
    if not any(code < 300 for code in res.values()):
        raise RuntimeError(f"All sends failed: {res}")
    save_json(state_dir / "catalog.json", catalog)
    save_json(state_dir / "last_digest.json", {"date": today.isoformat(), "keys": sorted(i["key"] for i in items)})
    print("[ok] digest sent:", res)
    return {"subject": subject, "sections": sec, "text": text}


def run_hourly(cfg, state_dir, dry_run=False, force=False, fixture=None, session=requests, now=None):
    now = now or now_local(cfg)
    h = cfg["hourly"]
    lo, hi = h["active_hours"]
    if not h["enabled"] or (not force and not (lo <= now.hour < hi)):
        print(f"[skip] hourly check outside active window ({lo}:00-{hi}:00) or disabled")
        return None
    state_dir = Path(state_dir)
    catalog = load_json(state_dir / "catalog.json", {})
    mode = mode_of(cfg)
    tracked = [f"{i['company']} — {i['program']}" for i in active_items(catalog, mode)]
    opps = fixture if fixture is not None else research(cfg, "hourly", now.date(), session, tracked)
    was_alertable = {k: alertable(v, mode) for k, v in catalog.items()}
    # alert on items that are brand new OR just became alertable (e.g. sophomore-only -> eligibility corrected, or a first-year signal appeared)
    unseen = [o for o in opps if alertable(o, mode) and not was_alertable.get(o["key"], False)]
    quiet = [o for o in opps if not alertable(o, mode)]
    _, changed = merge_into_catalog(catalog, opps, now.isoformat(timespec="minutes"), mode)
    expire_old(catalog, now.date())
    unseen = [o for o in unseen if alertable(catalog[o["key"]], mode)]    # drop anything the merge just retired
    if not unseen:
        if not dry_run:
            save_json(state_dir / "catalog.json", catalog)
        print(f"[ok] nothing new to alert on ({len(quiet)} non-alertable finds recorded silently, "
              f"{len(changed)} minor updates folded into the next digest)")
        return None
    subject, text, body = render_alert(unseen, now.date(), cfg)
    if dry_run:
        print(subject)
        return {"subject": subject, "new": unseen, "text": text}
    res = send_email(cfg, subject, text, body, session)
    if not any(code < 300 for code in res.values()):
        raise RuntimeError(f"All alert sends failed: {res}")   # catalog NOT saved -> retried next hour
    save_json(state_dir / "catalog.json", catalog)
    print(f"[ok] alert sent for {len(unseen)} new item(s):", res)
    return {"subject": subject, "new": unseen, "text": text}


def cmd_recipients(args):
    cfg = json.loads(CONFIG_PATH.read_text())
    r = cfg["recipients"]
    if args.action == "add" and args.email.lower() not in [x.lower() for x in r]:
        r.append(args.email)
    elif args.action == "remove":
        r[:] = [x for x in r if x.lower() != args.email.lower()]
    if args.action in ("add", "remove"):
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
    print("Recipients:", ", ".join(r))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("daily", "hourly"):
        sp = sub.add_parser(name)
        sp.add_argument("--dry-run", action="store_true", help="no email, no state writes; writes preview HTML (daily)")
        sp.add_argument("--force", action="store_true", help="ignore the time-of-day guard")
        sp.add_argument("--fixture", help="JSON file with {'opportunities': [...]} instead of live research")
        sp.add_argument("--state-dir", default=str(ROOT / "state"))
    rp = sub.add_parser("recipients")
    rp.add_argument("action", choices=["list", "add", "remove"])
    rp.add_argument("email", nargs="?")
    a = p.parse_args(argv)
    if a.cmd == "recipients":
        return cmd_recipients(a)
    cfg = load_config()
    fixture = None
    if a.fixture:
        fixture = [o for o in (clean_opportunity(x) for x in json.loads(Path(a.fixture).read_text())["opportunities"]) if o]
    fn = run_daily if a.cmd == "daily" else run_hourly
    fn(cfg, a.state_dir, dry_run=a.dry_run, force=a.force, fixture=fixture)


if __name__ == "__main__":
    main()
