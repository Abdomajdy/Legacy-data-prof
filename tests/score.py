"""Score the profiler against the fixture's ground truth.

This is the number that tells you whether a change helped. Without it you are
tuning heuristics by eyeball, which on a corpus of edge cases means you will
fix one file and silently break three others.
"""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from legacyprof.cli import profile_file

ROOT = Path(__file__).resolve().parents[1]
truth = json.loads((ROOT / "fixtures/customer_master.truth.json").read_text())
prof = profile_file(ROOT / "fixtures/customer_master.dat", include_samples=False)

print(f"{'ground truth':<36} {'detected':<24} {'result'}")
print("-" * 92)
exact = partial = 0
for t in truth["fields"]:
    span = f"{t['start']}-{t['end']}"
    hit = next((f for f in prof["fields"] if f["start"] == t["start"] and f["end"] == t["end"]), None)
    overlap = [f for f in prof["fields"] if f["start"] <= t["end"] and f["end"] >= t["start"]]
    if hit:
        ok = hit["kind"] == t["kind"]
        exact += ok
        partial += 1
        mark = "EXACT" if ok else f"span ok, kind={hit['kind']}"
    elif len(overlap) == 1:
        partial += 1
        mark = "partial (merged with neighbour)"
    else:
        mark = f"MISSED (split into {len(overlap)})"
    label = f"{span:<8} {t['kind']:<15} {t['name'][:10]}"
    detected = f"{overlap[0]['start']}-{overlap[0]['end']} {overlap[0]['kind']}" if overlap else "-"
    print(f"{label:<36} {detected:<24} {mark}")

n = len(truth["fields"])
print("-" * 92)
print(f"exact boundary + kind : {exact}/{n}  ({exact/n:.0%})")
print(f"boundary recovered    : {partial}/{n}  ({partial/n:.0%})")
want = truth["format_change_record"]
found = [d["approx_record"] for d in prof["drift"]]
print(f"format change @{want:,}  : "
      f"{'FOUND' if any(abs(f-want) < 3000 for f in found) else 'MISSED'}"
      f"   false positives: {len([f for f in found if abs(f-want) >= 3000])}")
sent = next((f for f in prof["fields"] if f["start"] == 21), None)
print(f"date sentinel 8%      : {sent['nulls'] if sent and sent['nulls'] else 'MISSED'}")
