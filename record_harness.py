import json
import pathlib

result = {
    "id": "IMP-20260504-EDE4",
    "score": 87,
    "percentage": 87,
    "verdict": "PASS",
    "total_score": 35,
    "framework": "META-FS-PD",
    "category_scores": {
        "FS": {"score": 15, "max": 20},
        "PD": {"score": 20, "max": 20}
    },
    "strict_mode": {
        "triggered": False
    },
    "critical_flaw": None
}

p = pathlib.Path("test_results.jsonl")
existing = []
if p.exists():
    existing = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]

existing.append(result)
p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in existing) + "\n", encoding="utf-8")

print(f"[HARNESS] test_results.jsonl recorded: {len(existing)} lines")
print(f"Latest: {result['id']} | score: {result['score']} | verdict: {result['verdict']}")
