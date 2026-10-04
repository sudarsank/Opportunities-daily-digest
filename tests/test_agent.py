import copy, datetime as dt, json, os, sys, tempfile, unittest
from pathlib import Path
from zoneinfo import ZoneInfo
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import agent as A

CFG = json.loads((ROOT / "config.json").read_text())
os.environ.setdefault("AGENTMAIL_API_KEY", "test"); os.environ.setdefault("ANTHROPIC_API_KEY", "test")

def opp(company, program, **kw):
    """Default: unverified WITH a first-year signal (i.e. relevant). Override first_year_signal / eligibility as needed."""
    base = dict(company=company, program=program, type="Insight", term="Summer 2027", eligibility="unverified", first_year_signal="yes")
    base.update(kw); return A.clean_opportunity(base)

def soph(company, program, **kw):
    return opp(company, program, eligibility="sophomore_only", eligibility_evidence="second-year students", first_year_signal="no", **kw)

class FakeResp:
    def __init__(self, code=200, payload=None): self.status_code, self._p, self.text = code, payload or {}, "x"
    def json(self): return self._p
    def raise_for_status(self):
        if self.status_code >= 400: raise A.requests.HTTPError("boom")

class MailSession:
    def __init__(self, fail_for=()): self.calls, self.fail_for = [], set(fail_for)
    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append((url, headers, json)); to = json["to"][0]
        return FakeResp(500 if to in self.fail_for else 200)

def at(y, m, d, h, mi=0, tz="America/New_York"):
    return dt.datetime(y, m, d, h, mi, tzinfo=ZoneInfo(tz))

class Keys(unittest.TestCase):
    def test_year_and_filler_ignored(self):
        self.assertEqual(A.norm_key("Goldman Sachs", "Possibilities Series 2026 Program"),
                         A.norm_key("goldman sachs", "The Possibilities Series 2027"))
    def test_distinct_programs_stay_distinct(self):
        self.assertNotEqual(A.norm_key("Goldman Sachs", "Possibilities Series"), A.norm_key("Goldman Sachs", "Possibilities Summit"))
    def test_dedupe_exact_and_fuzzy_and_fill(self):
        a = opp("Capital One", "Analyst Early Internship Program", apply_url="")
        b = opp("Capital One", "Analyst Early Internship Program 2027", apply_url="https://x")
        c = opp("Capital One", "Technology Early Internship Program")
        out = A.dedupe([a, b, c]); self.assertEqual(len(out), 2); self.assertEqual(out[0]["apply_url"], "https://x")

class Eligibility(unittest.TestCase):
    def test_verified_without_evidence_is_downgraded_but_claim_kept(self):
        o = opp("X", "P", eligibility="verified_first_year", first_year_signal="")
        self.assertEqual(o["eligibility"], "unverified"); self.assertEqual(o["first_year_signal"], "yes"); self.assertTrue(A.is_relevant(o))
    def test_verified_with_freshman_evidence_kept(self):
        self.assertEqual(opp("X", "P", eligibility="verified_first_year", eligibility_evidence="Open to first-year students")["eligibility"], "verified_first_year")
    def test_sophomore_evidence_overrides_verified(self):
        o = opp("X", "P", eligibility="verified_first_year", eligibility_evidence="Open to sophomores only")
        self.assertEqual((o["eligibility"], o["first_year_signal"]), ("sophomore_only", "no"))
    def test_bad_label_normalised(self):
        self.assertEqual(opp("X", "P", eligibility="definitely!")["eligibility"], "unverified")

class Relevance(unittest.TestCase):
    def test_rule_table(self):
        self.assertTrue(A.is_relevant(opp("A", "a", eligibility="verified_first_year", eligibility_evidence="first-year students")))
        self.assertTrue(A.is_relevant(opp("A", "b")))                                         # unverified + yes
        self.assertFalse(A.is_relevant(opp("A", "c", first_year_signal="unknown")))           # no signal -> hidden
        self.assertFalse(A.is_relevant(opp("A", "d", first_year_signal="no")))
        self.assertFalse(A.is_relevant(soph("A", "e")))
        self.assertFalse(A.is_relevant(opp("A", "f", first_year_signal="banana")))            # junk normalised to unknown
    def test_irrelevant_kept_in_catalog_but_never_active(self):
        cat = {}; A.merge_into_catalog(cat, [soph("BofA", "Soph"), opp("Citi", "FD")], "t")
        self.assertEqual(len(cat), 2); self.assertEqual([i["company"] for i in A.active_items(cat)], ["Citi"])
    def test_merge_signal_precedence_and_quiet_updates(self):
        cat = {}; a = opp("A", "P", deadline="2026-11-01"); A.merge_into_catalog(cat, [a], "t1")
        _, ch = A.merge_into_catalog(cat, [opp("A", "P", first_year_signal="unknown")], "t2")
        self.assertEqual(cat[a["key"]]["first_year_signal"], "yes")                           # unknown never erases a real signal
        _, ch = A.merge_into_catalog(cat, [soph("A", "P", deadline="2026-12-01")], "t3")
        self.assertFalse(A.is_relevant(cat[a["key"]])); self.assertEqual(ch, [])              # retired: no 'updated' noise

class Catalog(unittest.TestCase):
    def test_new_changed_and_no_downgrade(self):
        cat = {}; a = opp("A", "Prog", eligibility="verified_first_year", eligibility_evidence="first-year students", deadline="2026-11-01")
        new, ch = A.merge_into_catalog(cat, [a], "t1"); self.assertEqual((len(new), ch), (1, []))
        new, ch = A.merge_into_catalog(cat, [opp("A", "Prog", deadline="2026-12-01", apply_url="https://apply")], "t2")
        self.assertEqual(new, []); self.assertEqual(len(ch), 1)
        self.assertIn("deadline 2026-11-01 → 2026-12-01", ch[0]["notes"]); self.assertIn("application link now available", ch[0]["notes"])
        self.assertEqual(cat[a["key"]]["eligibility"], "verified_first_year"); self.assertEqual(cat[a["key"]]["first_seen"], "t1")
    def test_expiry_keeps_record_so_it_never_returns_as_new(self):
        cat = {}; a = opp("A", "Old", deadline="2026-09-01"); A.merge_into_catalog(cat, [a], "t")
        A.expire_old(cat, dt.date(2026, 10, 3)); self.assertEqual(A.active_items(cat), [])
        new, _ = A.merge_into_catalog(cat, [a], "t2"); self.assertEqual(new, [])

class Guards(unittest.TestCase):
    def test_digest_guard_dst_and_catchup(self):
        edt = dt.datetime(2026, 7, 15, 12, 0, tzinfo=dt.timezone.utc).astimezone(ZoneInfo(CFG["timezone"]))
        est = dt.datetime(2026, 12, 15, 13, 0, tzinfo=dt.timezone.utc).astimezone(ZoneInfo(CFG["timezone"]))
        self.assertEqual((edt.hour, est.hour), (8, 8))
        self.assertTrue(A.should_run_digest(edt, "2026-07-14", 8))
        self.assertFalse(A.should_run_digest(at(2026, 7, 15, 7, 59), "2026-07-14", 8))
        self.assertFalse(A.should_run_digest(at(2026, 7, 15, 9), "2026-07-15", 8))
        self.assertTrue(A.should_run_digest(at(2026, 7, 15, 10), "2026-07-14", 8))
    def test_urgency_window(self):
        today = dt.date(2026, 10, 3)
        self.assertEqual(A.days_until("2026-10-05", today), 2); self.assertIsNone(A.days_until("rolling", today))

class Flow(unittest.TestCase):
    """STRICT mode behaviour (opt-in via relevance_mode='strict')."""
    def setUp(self):
        self.tmp = tempfile.mkdtemp(); self.cfg = copy.deepcopy(CFG); self.cfg["relevance_mode"] = "strict"
        self.d1 = [opp("Bain", "Consulting Kickstart", deadline="2026-10-05"), opp("Goldman Sachs", "Virtual Insight Series", opens="Oct 2026"),
                   soph("BofA", "Sophomore Summer Analyst", deadline="2026-12-01"),
                   opp("T. Rowe", "Summer Tracks", first_year_signal="unknown")]
    def test_two_day_new_today_flow_and_irrelevant_never_shown(self):
        s = MailSession()
        r1 = A.run_daily(self.cfg, self.tmp, fixture=self.d1, session=s, now=at(2026, 10, 3, 8))
        self.assertEqual(sorted(i["company"] for i in r1["sections"]["new"]), ["Bain", "Goldman Sachs"])   # soph + no-signal hidden
        self.assertEqual(len(s.calls), 2)
        for needle in ["BofA", "T. Rowe", "Sophomore-only", "🔒"]: self.assertNotIn(needle, r1["text"])
        d2 = self.d1 + [opp("Citi", "Freshman Discovery", deadline="2026-10-09")]
        r2 = A.run_daily(self.cfg, self.tmp, fixture=d2, session=MailSession(), now=at(2026, 10, 4, 8))
        self.assertEqual([i["company"] for i in r2["sections"]["new"]], ["Citi"])
        self.assertIn("NEW TODAY", r2["text"]); self.assertTrue(any(i["company"] == "Citi" for i in r2["sections"]["urgent"]))
        self.assertIsNone(A.run_daily(self.cfg, self.tmp, fixture=d2, session=MailSession(), now=at(2026, 10, 4, 9)))
    def test_removed_section_when_listed_item_becomes_irrelevant(self):
        A.run_daily(self.cfg, self.tmp, fixture=self.d1, session=MailSession(), now=at(2026, 10, 3, 8))
        retired = [soph("Bain", "Consulting Kickstart", why_fit="Oct 5 round is Class of 2029 only")]
        r = A.run_daily(self.cfg, self.tmp, fixture=retired, session=MailSession(), now=at(2026, 10, 4, 8))
        self.assertIn("REMOVED SINCE LAST DIGEST", r["text"]); self.assertIn("Bain — Consulting Kickstart: not open to first-years", r["text"])
        self.assertIn("Class of 2029 only", r["text"])
        self.assertNotIn("Bain — Consulting Kickstart\n", r["text"])                                     # no longer shown as an opportunity
        r2 = A.run_daily(self.cfg, self.tmp, fixture=retired, session=MailSession(), now=at(2026, 10, 5, 8))
        self.assertNotIn("REMOVED SINCE LAST DIGEST", r2["text"])                                        # told once, not repeatedly
    def test_expired_item_reported_as_removed(self):
        A.run_daily(self.cfg, self.tmp, fixture=[opp("Old", "Prog", deadline="2026-10-04"), opp("Keep", "K")], session=MailSession(), now=at(2026, 10, 3, 8))
        r = A.run_daily(self.cfg, self.tmp, fixture=[], session=MailSession(), now=at(2026, 10, 20, 8))
        self.assertIn("Old — Prog: deadline has passed", r["text"])
    def test_missing_item_does_not_reappear_as_new(self):
        A.run_daily(self.cfg, self.tmp, fixture=self.d1, session=MailSession(), now=at(2026, 10, 3, 8))
        A.run_daily(self.cfg, self.tmp, fixture=self.d1[:1], session=MailSession(), now=at(2026, 10, 4, 8))
        r = A.run_daily(self.cfg, self.tmp, fixture=self.d1, session=MailSession(), now=at(2026, 10, 5, 8))
        self.assertEqual(r["sections"]["new"], [])
    def test_hourly_alerts_only_on_relevant_new_or_newly_relevant(self):
        A.run_daily(self.cfg, self.tmp, fixture=self.d1, session=MailSession(), now=at(2026, 10, 3, 8))
        s = MailSession(); self.assertIsNone(A.run_hourly(self.cfg, self.tmp, fixture=self.d1, session=s, now=at(2026, 10, 3, 9))); self.assertEqual(s.calls, [])
        fresh = [opp("Fidelity", "Freshman Exploration", deadline="2026-10-08"), soph("Evercore", "Sophomore Program"),
                 opp("Mystery", "Unknown Class Year", first_year_signal="unknown")]
        r = A.run_hourly(self.cfg, self.tmp, fixture=self.d1 + fresh, session=s, now=at(2026, 10, 3, 10))
        self.assertEqual([o["company"] for o in r["new"]], ["Fidelity"]); self.assertEqual(len(s.calls), 2); self.assertIn("DUE IN 5D", r["text"])
        s2 = MailSession(); self.assertIsNone(A.run_hourly(self.cfg, self.tmp, fixture=self.d1 + fresh, session=s2, now=at(2026, 10, 3, 11))); self.assertEqual(s2.calls, [])
        # a hidden item gets a first-year signal later -> NOW it alerts (newly relevant)
        s3 = MailSession()
        r = A.run_hourly(self.cfg, self.tmp, fixture=[opp("Mystery", "Unknown Class Year", first_year_signal="yes")], session=s3, now=at(2026, 10, 3, 12))
        self.assertEqual([o["company"] for o in r["new"]], ["Mystery"])
        r = A.run_daily(self.cfg, self.tmp, fixture=self.d1 + fresh, session=MailSession(), now=at(2026, 10, 4, 8))
        self.assertEqual(sorted(i["company"] for i in r["sections"]["new"]), ["Fidelity", "Mystery"])      # Evercore (sophomore) never appears
    def test_hourly_respects_active_hours(self):
        s = MailSession(); self.assertIsNone(A.run_hourly(self.cfg, self.tmp, fixture=[opp("Z", "Zed")], session=s, now=at(2026, 10, 3, 3))); self.assertEqual(s.calls, [])
    def test_failed_send_keeps_state_for_retry(self):
        with self.assertRaises(RuntimeError):
            A.run_hourly(self.cfg, self.tmp, fixture=[opp("Q", "Qprog")], session=MailSession(fail_for=self.cfg["recipients"]), now=at(2026, 10, 3, 10))
        self.assertFalse((Path(self.tmp) / "catalog.json").exists())
        r = A.run_hourly(self.cfg, self.tmp, fixture=[opp("Q", "Qprog")], session=MailSession(), now=at(2026, 10, 3, 11)); self.assertEqual(len(r["new"]), 1)
    def test_one_bad_recipient_does_not_block_other(self):
        s = MailSession(fail_for=[self.cfg["recipients"][0]])
        A.run_hourly(self.cfg, self.tmp, fixture=[opp("Q", "Qprog")], session=s, now=at(2026, 10, 3, 10)); self.assertEqual(len(s.calls), 2)
    def test_blank_research_does_not_send_blank_digest(self):
        with self.assertRaises(RuntimeError):
            A.run_daily(self.cfg, self.tmp, fixture=[], session=MailSession(), now=at(2026, 10, 3, 8))
    def test_payload_shape_and_auth(self):
        s = MailSession(); A.send_email(self.cfg, "S", "T", "<b>H</b>", s); url, headers, body = s.calls[0]
        self.assertTrue(url.endswith("/v0/inboxes/sudarsan-0409@agentmail.to/messages/send"))
        self.assertTrue(headers["Authorization"].startswith("Bearer ")); self.assertEqual(set(body), {"to", "subject", "text", "html"})

class Research(unittest.TestCase):
    def test_extract_json_variants(self):
        self.assertEqual(len(A.extract_json('blah ```json\n{"opportunities":[{"company":"a"}]}\n``` bye')["opportunities"]), 1)
        self.assertEqual(len(A.extract_json('Here: {"opportunities":[{"company":"a"},{"company":"b"}]} done')["opportunities"]), 2)
        self.assertEqual(A.extract_json("no json at all")["opportunities"], [])
    def test_pause_turn_continuation_and_tool_config(self):
        class S:
            n = 0; bodies = []
            def post(self, url, headers=None, json=None, timeout=None):
                S.bodies.append(json); S.n += 1
                if S.n == 1: return FakeResp(200, {"stop_reason": "pause_turn", "content": [{"type": "server_tool_use"}]})
                return FakeResp(200, {"stop_reason": "end_turn", "content": [{"type": "text", "text": '{"opportunities":[]}'}]})
        out = A.call_anthropic(CFG, "sys", "usr", 4, 100, S())
        self.assertEqual(S.n, 2); self.assertEqual(S.bodies[0]["tools"][0]["max_uses"], 4)
        self.assertEqual(S.bodies[1]["messages"][-1]["role"], "assistant"); self.assertIn("opportunities", out)
    def test_batch_failure_is_isolated(self):
        calls = {"n": 0}
        class S:
            def post(self, *a, **k):
                calls["n"] += 1
                if calls["n"] == 1: raise A.requests.ConnectionError("down")
                return FakeResp(200, {"stop_reason": "end_turn", "content": [{"type": "text", "text":
                    '{"opportunities":[{"company":"KPMG","program":"Embark","eligibility":"verified_first_year","eligibility_evidence":"first-year students"}]}'}]})
        out = A.research(CFG, "daily", dt.date(2026, 10, 3), S())
        self.assertEqual(len(out), 1); self.assertEqual(calls["n"], len(CFG["batches"]))
    def test_prompt_enforces_relevance_and_passes_tracked_list(self):
        class S:
            bodies = []
            def post(self, url, headers=None, json=None, timeout=None):
                S.bodies.append(json); return FakeResp(200, {"stop_reason": "end_turn", "content": [{"type": "text", "text": '{"opportunities":[]}'}]})
        A.research({**CFG, "relevance_mode": "strict"}, "daily", dt.date(2026, 10, 3), S(), tracked=["Citi — Freshman Discovery Program"])
        sysmsg, usr = S.bodies[0]["system"], S.bodies[0]["messages"][0]["content"]
        for needle in ["STRICT RELEVANCE", "OMIT sophomore-only", "first_year_signal", "RETIREMENT CHANNEL"]: self.assertIn(needle, sysmsg)
        self.assertIn("Citi — Freshman Discovery Program", usr)
    def test_model_junk_is_neutralised(self):
        class S:
            def post(self, *a, **k):
                return FakeResp(200, {"stop_reason": "end_turn", "content": [{"type": "text", "text": json.dumps({"opportunities": [
                    {"company": "X", "program": "Soph", "eligibility": "verified_first_year", "eligibility_evidence": "open to sophomores"},
                    {"company": "Y", "program": "NoSignal"},
                    {"company": "Z", "program": "Good", "first_year_signal": "yes"}]})}]})
        got = A.research({**CFG, "batches": CFG["batches"][:1], "relevance_mode": "strict"}, "daily", dt.date(2026, 10, 3), S())
        self.assertEqual(sorted(o["company"] for o in got if A.is_relevant(o)), ["Z"])

class Render(unittest.TestCase):
    def test_digest_sections_and_flags(self):
        cat = {}; items = [opp("Bain", "Kickstart", deadline="2026-10-05"), opp("GS", "Virtual Insight", opens="Oct"), soph("BofA", "Soph", deadline="2026-12-01")]
        A.merge_into_catalog(cat, items, "t"); act = A.active_items(cat); today = dt.date(2026, 10, 3)
        sec = A.classify(act, {act[0]["key"]}, today, CFG)
        title, text, html = A.render_digest(sec, [], cat, today, {**CFG, "relevance_mode": "strict"}, "stats")
        for needle in ["URGENT DEADLINES", "NEW TODAY", "FRESHMAN / EARLY-INSIGHT", "SUMMER 2027", "DEADLINE WATCH", "DAILY TODO CHECKLIST",
                       "DUE IN 2D", "Handshake/HireSmith", "sophomore-only, upper-class and graduate programs are filtered out"]:
            self.assertIn(needle, text, needle)
        self.assertNotIn("BofA", text); self.assertNotIn("BofA", html)
        self.assertIn('<h3 style="background:#fff3cd', html)

class Recipients(unittest.TestCase):
    def test_env_override(self):
        os.environ["RECIPIENTS"] = "a@x.com, b@y.com"
        try: self.assertEqual(A.load_config()["recipients"], ["a@x.com", "b@y.com"])
        finally: del os.environ["RECIPIENTS"]

class Migration(unittest.TestCase):
    def test_shipped_state_matches_default_mode(self):
        cat = json.loads((ROOT / "state" / "catalog.json").read_text())
        last = json.loads((ROOT / "state" / "last_digest.json").read_text())
        self.assertEqual(CFG["relevance_mode"], "inclusive")                      # the restored default
        listed = {k for k, v in cat.items() if not v.get("expired") and A.is_relevant(v, A.mode_of(CFG))}
        self.assertEqual(set(last["keys"]), listed)                                # tomorrow's 'new' will only be genuinely new
        self.assertTrue(all("first_year_signal" in v for v in cat.values()))
        strict = {k for k, v in cat.items() if not v.get("expired") and A.is_relevant(v)}
        self.assertTrue(strict < listed)                                           # strict is a strict subset (switch back any time)

class Inclusive(unittest.TestCase):
    """DEFAULT mode: nothing is hidden for unclear class year; sophomore-only stays a 🔒 watch item only."""
    def setUp(self):
        self.tmp = tempfile.mkdtemp(); self.cfg = copy.deepcopy(CFG)
        assert A.mode_of(self.cfg) == "inclusive"
        self.d1 = [opp("Bain", "Consulting Kickstart", deadline="2026-10-05"),
                   soph("BofA", "Sophomore Summer Analyst", deadline="2026-10-06"),
                   opp("T. Rowe", "Summer Tracks", first_year_signal="unknown", eligibility="unverified"),
                   opp("Mystery", "Class Year Unclear", first_year_signal="unknown")]
    def test_default_mode_when_key_missing(self):
        self.assertEqual(A.mode_of({}), "inclusive")
    def test_nothing_hidden_but_sophomore_only_is_never_urgent_or_freshman(self):
        r = A.run_daily(self.cfg, self.tmp, fixture=self.d1, session=MailSession(), now=at(2026, 10, 3, 8))
        new = [i["company"] for i in r["sections"]["new"]]
        self.assertEqual(sorted(new), ["Bain", "BofA", "Mystery", "T. Rowe"])      # unknown-class-year items ARE shown
        self.assertEqual(new[-1], "BofA")                                           # sophomore-only sorted last
        self.assertIn("Sophomore-only (not yet eligible)", r["text"])
        self.assertEqual([i["company"] for i in r["sections"]["urgent"]], ["Bain"])  # BofA due in 3 days but NOT urgent
        self.assertNotIn("BofA", [i["company"] for i in r["sections"]["freshman"]])
        self.assertIn("Handshake/HireSmith", r["text"]); self.assertIn("Nothing is hidden for unclear class year", r["text"])
        self.assertNotIn("Review new: BofA", r["text"]); self.assertIn("Review new: Mystery", r["text"])
    def test_item_turning_sophomore_only_stays_listed_not_removed(self):
        A.run_daily(self.cfg, self.tmp, fixture=self.d1, session=MailSession(), now=at(2026, 10, 3, 8))
        r = A.run_daily(self.cfg, self.tmp, fixture=[soph("Mystery", "Class Year Unclear", why_fit="official: sophomores")], session=MailSession(), now=at(2026, 10, 4, 8))
        self.assertNotIn("REMOVED SINCE LAST DIGEST", r["text"]); self.assertIn("Mystery — Class Year Unclear", r["text"])
    def test_hourly_alerts_on_unclear_class_year_never_on_sophomore(self):
        A.run_daily(self.cfg, self.tmp, fixture=self.d1, session=MailSession(), now=at(2026, 10, 3, 8))
        s = MailSession()
        fresh = [opp("Fidelity", "Maybe Open", first_year_signal="unknown"), soph("Evercore", "Soph Program")]
        r = A.run_hourly(self.cfg, self.tmp, fixture=fresh, session=s, now=at(2026, 10, 3, 10))
        self.assertEqual([o["company"] for o in r["new"]], ["Fidelity"]); self.assertEqual(len(s.calls), 2)
        # sophomore-only item later corrected to first-year eligible -> now it alerts (an opportunity we must not miss)
        r = A.run_hourly(self.cfg, self.tmp, fixture=[opp("Evercore", "Soph Program", eligibility="verified_first_year", eligibility_evidence="open to first-year students")],
                         session=MailSession(), now=at(2026, 10, 3, 11))
        self.assertEqual([o["company"] for o in r["new"]], ["Evercore"])
    def test_prompt_is_inclusive_and_has_no_retirement_channel(self):
        class S:
            bodies = []
            def post(self, url, headers=None, json=None, timeout=None):
                S.bodies.append(json); return FakeResp(200, {"stop_reason": "end_turn", "content": [{"type": "text", "text": '{"opportunities":[]}'}]})
        A.research(self.cfg, "daily", dt.date(2026, 10, 3), S(), tracked=["Citi — Freshman Discovery Program"])
        sysmsg, usr = S.bodies[0]["system"], S.bodies[0]["messages"][0]["content"]
        self.assertIn("DO NOT DROP POSSIBLE OPPORTUNITIES", sysmsg); self.assertNotIn("STRICT RELEVANCE", sysmsg)
        self.assertNotIn("RETIREMENT CHANNEL", sysmsg); self.assertNotIn("Currently tracked", usr)
    def test_mode_switch_round_trip(self):
        cat = {}; A.merge_into_catalog(cat, self.d1, "t", "inclusive")
        self.assertEqual(len(A.active_items(cat, "inclusive")), 4)
        self.assertEqual(sorted(i["company"] for i in A.active_items(cat, "strict")), ["Bain"])      # only the 'yes' signal survives strict

if __name__ == "__main__":
    unittest.main(verbosity=1)
