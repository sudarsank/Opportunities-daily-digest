"""One-time migration (2026-10-04) to the strict first-year relevance model.
 * tags every catalog entry with first_year_signal
 * re-baselines state/last_digest.json to RELEVANT items only, so tomorrow's digest does not announce a one-off
   'removed' list for the sophomore-only items that were shown in the 2026-10-04 email."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import agent as A

SIGNAL_YES = {("Citadel / Citadel Securities", "Discover Citadel"), ("Goldman Sachs", "Virtual Insight Series"),
              ("JPMorganChase", "Spring Insight"), ("Jane Street", "INSIGHT / SEE"), ("KPMG", "Embark Scholars"),
              ("KPMG", "Rise"), ("SEO Career", "Finance track placement program")}
SIGNAL_NO = {
    ("Bain & Company", "ADvantage / BASE"): "Not for undergraduates: ADvantage targets graduate students/post-docs and BASE incoming MBAs.",
    ("T. Rowe Price", "2027 Internship Programs (9 tracks)"): "Prior-cycle postings required graduation 2026-2028 (upper-class); no first-year signal found.",
    ("T. Rowe Price", "Digital Assets Strategy Internship"): "Prior-cycle posting required graduation 2026-2027 (upper-class); no first-year signal found.",
}

def migrate(state_dir):
    state_dir = Path(state_dir)
    cat = A.load_json(state_dir / "catalog.json", {})
    for it in cat.values():
        ident = (it["company"], it["program"])
        if it["eligibility"] == A.VERIFIED:
            it["first_year_signal"] = "yes"
        elif it["eligibility"] == A.SOPH_ONLY:
            it["first_year_signal"] = "no"
        elif ident in SIGNAL_YES:
            it["first_year_signal"] = "yes"
        elif ident in SIGNAL_NO:
            it["first_year_signal"], it["why_fit"] = "no", SIGNAL_NO[ident]
        else:
            it["first_year_signal"] = "unknown"
    A.save_json(state_dir / "catalog.json", cat)
    last = A.load_json(state_dir / "last_digest.json", {"date": None, "keys": []})
    keys = sorted(i["key"] for i in A.active_items(cat))
    A.save_json(state_dir / "last_digest.json", {"date": last["date"], "keys": keys})
    return cat, keys

if __name__ == "__main__":
    cat, keys = migrate(Path(__file__).resolve().parent.parent / "state")
    print(f"catalog entries: {len(cat)} | relevant (will be shown): {len(keys)} | hidden: {len(cat) - len(keys)}")
    for k in keys:
        print("  +", cat[k]["company"], "—", cat[k]["program"], f"[{cat[k]['eligibility']}]")
