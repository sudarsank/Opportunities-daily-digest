"""HISTORICAL (predates the first-year relevance filter; items below lack first_year_signal, so re-running would hide most).
Record of the 2026-10-04 one-off run. Research was done interactively (live web search) because no
ANTHROPIC_API_KEY existed in that environment; diffing, eligibility guard, rendering and state handling
are the agent's own code. Eligibility is 'verified_first_year' ONLY where a primary/official page was read."""
import json, shutil, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import agent as A

GWI = "https://www.girlswhoinvest.org/apply-faqs"
CITI = "https://jobs.citi.com/early-career-programs-pre-internships"
GS_POSS = "https://www.goldmansachs.com/careers/students/programs-and-internships/americas/possibilities-series"
GS_ELS = "https://www.goldmansachs.com/careers/students/programs-and-internships/americas/emerging-leaders-series"
COC = "https://www.capitalonecareers.com/get-ahead-with-early-career-programs-for-students"
COC_AEIP = "https://www.capitalonecareers.com/job/mclean/analyst-early-internship-program-summer-2027/31238/99109660560"
TUFTS_BAIN = "https://careers.tufts.edu/blog/2026/09/16/bain-company-upcoming-opportunities-and-deadlines/"
INTERNDOCK = "https://www.interndock.com/tracker/guides/ultimate-pre-internship-early-insight-programs-list"
CASEPREP = "https://www.case-prep.com/blog/kpmg-consulting-internship-deadline-2027"
TRP = "https://troweprice.gr8people.com/jobs/21275/fixed-income-part-time-academic-year-internship-2025-2026"
WSG = "https://www.wallstreetguide.net/post/top-10-sophomore-insights-programs-for-ib"

RESEARCH = [
 # ── verified against primary/official pages this run ──
 dict(company="Girls Who Invest", program="GWI Scholars Program (2027 cohort)", type="Tuition-free program; sophomores can opt into paid Internship Track",
      term="Spring 2027 -> Online/Summer Intensive", location="Virtual / in-person intensive", eligibility=A.VERIFIED,
      eligibility_evidence="GWI official FAQ: first-year or sophomore status, expected graduation fall 2028 - spring 2030",
      deadline="2026-10-15", apply_url=GWI, source=GWI, category="fellowship",
      why_fit="OPEN NOW; priority date (Sep 15) passed, FINAL deadline Oct 15. Needs essay (350-500 words), 1-min video, HS transcript. "
              "First-years cannot opt into the Internship Track. Historically serves women; one aggregator says all genders now eligible - confirm on GWI's FAQ."),
 dict(company="Citi", program="Freshman Discovery Program", type="Virtual workshop series (3 days, May)", term="Spring 2027 (program ~May)",
      location="Virtual", eligibility=A.VERIFIED, eligibility_evidence="Citi careers page: all first-year students in a four-year bachelor's program",
      opens="applications open ~late Mar/early Apr 2027 (2026 round closed Apr 27)", apply_url=CITI, source=CITI, category="freshman insight",
      why_fit="Official first-year program, any major; you stay eligible until summer 2027. Apply on Citi's site only (not Handshake)."),
 dict(company="Capital One", program="Early Experiences (Analyst / Product summits)", type="Short summits (May/Aug)", term="2027",
      location="McLean, VA area (confirm)", eligibility=A.VERIFIED,
      eligibility_evidence="Capital One early-career page: first-year students or sophomores pursuing a quantitative/STEM related major",
      opens="programs run May/Aug; contact AnalystEE@capitalone.com / ProductEE@capitalone.com to register interest", apply_url=COC, source=COC,
      category="freshman insight", why_fit="Official first-year eligibility; confirm Finance counts as 'quantitative' for the Analyst track (Product track lists business)."),
 dict(company="Goldman Sachs", program="Possibilities Series", type="Year-long virtual (first-years)", term="Spring 2027 cohort", eligibility=A.VERIFIED,
      eligibility_evidence="GS official page: first year undergraduate students attending a U.S. college/university",
      opens="2026 round CLOSED (Class of 2029); next round expected ~Feb 2027 - Class of 2030 eligibility not yet posted", apply_url=GS_POSS, source=GS_POSS,
      why_fit="Only year-long GS program built for first-years; set a February reminder."),
 # ── corrected: not open to Class of 2030 ──
 dict(company="Bain & Company", program="Consulting Kickstart", type="Virtual multi-session", term="2026-27", eligibility=A.SOPH_ONLY,
      eligibility_evidence="Open to Class of 2029 (Bain notice via Tufts career center)", source=TUFTS_BAIN,
      opens="Oct 5, 2026 round is Class of 2029 ONLY; Class of 2030 round not announced (historically spring)", category="consulting",
      why_fit="CORRECTION: yesterday's 'urgent Oct 5' flag does NOT apply to Class of 2030. Program historically targets underrepresented first-years - watch Bain's site this spring."),
 dict(company="Capital One", program="Analyst Early Internship Program (AEIP)", type="Paid 10-week internship", term="Summer 2027", location="McLean, VA",
      eligibility=A.SOPH_ONLY, eligibility_evidence="Must be in Sophomore Undergraduate Standing for the 2026-2027 academic year",
      opens="Summer 2027 posting closed; Summer 2028 posting expected ~Aug 2027 (apply as a sophomore)", apply_url=COC_AEIP, source=COC_AEIP,
      why_fit="CORRECTION: earlier 'freshmen eligible' claim came from an aggregator and was wrong - official posting is sophomore-only."),
 dict(company="Goldman Sachs", program="Emerging Leaders Series", type="Training/insight series", term="Fall 2026 - Spring 2027", eligibility=A.SOPH_ONLY,
      eligibility_evidence="GS official page: prepares second-year undergraduate and master's students (graduating Dec 2028 - Jun 2029)",
      opens="current round closes tonight (Oct 4, 2026) for Class of 2029; next cycle expected ~fall 2027", apply_url=GS_ELS, source=GS_ELS,
      why_fit="Not for Class of 2030 yet - calendar fall 2027."),
 dict(company="Goldman Sachs", program="Virtual Insight Series", type="4-week virtual insight", term="2027",
      eligibility=A.UNVERIFIED, opens="historically opens ~March (2024, 2026 cycles); an aggregator's 'Oct-Nov 2026' claim is unconfirmed - do NOT expect it this month",
      source=GS_POSS, why_fit="Division-wide exposure; first-year eligibility not confirmed on an official 2027 page."),
 dict(company="T. Rowe Price", program="Fixed Income Part-Time Academic Year Internship", type="Part-time (10 hrs/wk) academic-year internship, Oct-Mar",
      term="Academic year 2027-28 (watch)", location="Baltimore, MD (primarily virtual + 3 in-person days)", eligibility=A.SOPH_ONLY,
      eligibility_evidence="T. Rowe posting: minimum sophomore status in college; 3.5 GPA", source=TRP,
      why_fit="Matches your part-time/academic-year/hybrid interest and is local; not open to freshmen this year - target fall 2027."),
 dict(company="T. Rowe Price", program="2027 Internship Programs (9 tracks)", type="Summer internship", term="Summer 2027", location="Baltimore / Owings Mills, MD",
      eligibility=A.UNVERIFIED, source="https://www.extern.com/post/t-rowe-price-internship-guide", posted="2026-07-20",
      why_fit="Prior-cycle T. Rowe postings required graduation 2026-2028 (upper-class) -> likely NOT open to freshmen; verify each track before applying."),
 dict(company="T. Rowe Price", program="Digital Assets Strategy Internship", type="Summer internship", term="Summer 2027", location="Baltimore, MD",
      eligibility=A.UNVERIFIED, source="https://builtin.com/job/2025-digital-assets-strategy-summer-internship-program/4322189", posted="2026-09-04",
      why_fit="Prior-cycle posting required graduation May/June 2026-2027 (upper-class) -> likely not freshman-eligible; verify."),
 dict(company="KPMG", program="Embark Scholars", type="Multi-year paid internship (CPA / Technology tracks)", term="Summer 2027/2028", eligibility=A.UNVERIFIED,
      opens="expected ~Feb 2027, rolling; prior official posting allowed graduation through Sep 2030 with >=1 semester completed", source=CASEPREP,
      why_fit="Plausible Class of 2030 fit (Management Consulted lists 'class of 2030'); official 2027 posting not yet live."),
 # ── newly found (secondary sources; unverified) ──
 dict(company="KPMG", program="Rise", type="Leadership program", term="2027", eligibility=A.UNVERIFIED, opens="expected ~Feb 2027", source=CASEPREP,
      category="leadership", why_fit="Listed for freshmen and sophomores; verify on KPMG careers when it posts."),
 dict(company="JPMorganChase", program="Spring Insight", type="Short insight program", term="Spring 2027", eligibility=A.UNVERIFIED,
      opens="applications in winter (per aggregator)", source=INTERNDOCK, why_fit="Listed for first-years and sophomores; confirm on JPMC's programs directory."),
 dict(company="Jane Street", program="INSIGHT / SEE", type="Quant-trading insight programs", term="2027", eligibility=A.UNVERIFIED,
      opens="applications open fall-winter (per aggregator)", source=INTERNDOCK, why_fit="Listed for first-years and sophomores; trading/quant adjacency."),
 dict(company="Morgan Stanley", program="Early Insights", type="Virtual insight series", term="2027", eligibility=A.SOPH_ONLY,
      eligibility_evidence="Second-year students in a 4-year degree", opens="timing conflicts across sources (Sep-Oct vs Dec-Mar) - verify",
      source=WSG, why_fit="Not yet eligible; calendar for fall 2027."),
]

NOTES = [
    "CORRECTION — Bain Consulting Kickstart: yesterday's 'urgent, due Oct 5' flag does NOT apply to you. Bain's own notice says the Oct 5 round is open to the Class of 2029 only. Nothing to do now; watch for a Class of 2030 round (historically spring).",
    "CORRECTION — Capital One Analyst Early Internship Program: an aggregator said freshmen were eligible; Capital One's official posting requires SOPHOMORE standing for 2026-27 and has closed. Moved to watch for next year. Capital One's separate 'Early Experiences' summits DO list first-years (added below).",
    "CORRECTION — Goldman Sachs Virtual Insight Series: yesterday's 'opens Oct-Nov 2026' was an unconfirmed aggregator claim; GS's cycles have opened around March. Not expected this month.",
    "NEW & VERIFIED on official pages: Girls Who Invest GWI Scholars Program (final deadline Oct 15), Citi Freshman Discovery (opens ~Mar-Apr 2027), Capital One Early Experiences.",
    "Not for Class of 2030: Goldman Sachs Emerging Leaders Series (closes tonight, Class of 2029) and T. Rowe Price's part-time academic-year internship (sophomore minimum) — both are fall-2027 targets.",
]

def apply_operator_corrections(catalog):
    """State corrections the merge logic cannot express."""
    for k, it in catalog.items():
        if it["company"] == "Bain & Company" and "Kickstart" in it["program"]:
            it["deadline"] = ""                       # the Oct 5 date was for Class of 2029
        if it["company"] == "Girls Who Invest" and it["program"] == "Summer Intensive":
            it["expired"] = True                      # superseded by the GWI Scholars Program entry
            it["why_fit"] = "Superseded by 'GWI Scholars Program (2027 cohort)'."

def run(state_dir, session):
    cfg = A.load_config()
    state_dir = Path(state_dir)
    cat = A.load_json(state_dir / "catalog.json", {})
    apply_operator_corrections(cat)
    A.save_json(state_dir / "catalog.json", cat)
    fixture = [o for o in (A.clean_opportunity(r) for r in RESEARCH) if o]
    return A.run_daily(cfg, state_dir, fixture=fixture, session=session, notes=NOTES)

if __name__ == "__main__":
    print("Use via the interactive runner; see README.")
