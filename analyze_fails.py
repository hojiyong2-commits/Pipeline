import json, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

results = []
with open('c:/Users/hojiy/OneDrive/Desktop/Projects/Really good agents for QA and Orchestra/test_results_v3.jsonl', encoding='utf-8') as f:
    for line in f:
        results.append(json.loads(line))

fails = [r for r in results if r['verdict'] == 'FAIL']

print(f"FAIL count: {len(fails)}\n")

# Per-category missing item frequency
missing_freq = {}
for r in fails:
    for tag, cs in r['category_scores'].items():
        if not isinstance(cs, dict):
            continue
        for item, score in cs['details'].items():
            max_item = 5 if tag != 'UI' else 4
            if score < max_item:
                key = f"{tag}.{item}"
                missing_freq[key] = missing_freq.get(key, 0) + 1

print("=== Missing item frequency (across FAIL tasks) ===")
for k, v in sorted(missing_freq.items(), key=lambda x: -x[1]):
    print(f"  {k:35s}: {v}x")

print("\n=== FAIL task detail ===")
for r in sorted(fails, key=lambda x: x['percentage']):
    cats = {k: v for k,v in r['category_scores'].items() if isinstance(v, dict)}
    low = {k: v for k,v in cats.items() if v['score'] < v['max']}
    print(f"\n{r['id']} ({r['percentage']}%)")
    for k, v in low.items():
        max_item = 5 if k != 'UI' else 4
        missing = {dk: dv for dk, dv in v['details'].items() if dv < max_item}
        print(f"  {k}: {v['score']}/{v['max']}  missing={missing}")
