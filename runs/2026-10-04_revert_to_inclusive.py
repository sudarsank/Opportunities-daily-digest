"""One-time (2026-10-04): revert to the previous, inclusive behaviour (nothing hidden for unclear class year).
Re-baselines state/last_digest.json to every non-expired tracked item -- exactly what the 2026-10-04 email listed --
so tomorrow's 'New today' contains only genuinely new items and nothing re-appears as 'new'."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import agent as A

state = Path(__file__).resolve().parent.parent / "state"
cat = A.load_json(state / "catalog.json", {})
last = A.load_json(state / "last_digest.json", {"date": None, "keys": []})
keys = sorted(i["key"] for i in A.active_items(cat, "inclusive"))
A.save_json(state / "last_digest.json", {"date": last["date"], "keys": keys})
print(f"baseline ({last['date']}): {len(keys)} items now listed in inclusive mode (was {len(last['keys'])} in strict mode)")
