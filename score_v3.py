"""Scorer for 100 solutions against eval_harness_v3.json
v3.1 — required_patterns-based N/A sub-items to eliminate false failures.
"""
import json
import re
import os
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

BASE = Path("c:/Users/hojiy/OneDrive/Desktop/Projects/Really good agents for QA and Orchestra")
SOLUTIONS_DIR = BASE / "solutions"
HARNESS_FILE = BASE / "eval_harness_v3.json"
RESULTS_FILE = BASE / "test_results_v3.jsonl"
SUMMARY_FILE = BASE / "benchmark_v3_summary.json"


def read_solution(task_id: str) -> str:
    p = SOLUTIONS_DIR / task_id / "solution.py"
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8")


# ─── required_patterns → sub-item gating ─────────────────────────────────────
# If the mapping list is empty [] → always score (never N/A for that category).
# If the mapping list has patterns → only score if at least one pattern is
# present in the task's required_patterns list.

SEC_SUBITEM_REQUIRES: Dict[str, List[str]] = {
    "credential":  ["credential_env"],       # only if task needs credential management
    "path_verify": ["path_verify"],           # only if task needs path verification
    "sanitize":    ["sanitize_input"],        # only if task needs input sanitization
    "injection":   ["injection_prevent"],     # only if task needs injection prevention
}

FS_SUBITEM_REQUIRES: Dict[str, List[str]] = {
    "traversal":  ["traversal_prevention"],   # only if task accepts user-controlled paths
    "encoding":   ["encoding_fallback"],      # only if task reads files with unknown encoding
    "safe_write": ["atomic_write", "safe_write"],  # only if task writes files
    "pathlib":    [],                         # always required for FS tasks
}

UI_SUBITEM_REQUIRES: Dict[str, List[str]] = {
    "threading":  ["threading"],              # only if task involves I/O-bound operations
    "loading":    ["loading"],                # only if task has threading
    "type_hint":  [],                         # always required
    "naming":     [],                         # always required (Tkinter only)
    "validation": [],                         # always required
}


def is_required(subitem_key: str, requires_map: Dict[str, List[str]],
                task_patterns: List[str]) -> bool:
    """Return True if the sub-item should be scored (not N/A)."""
    gate = requires_map.get(subitem_key, [])
    if not gate:
        return True  # always required
    return any(p in task_patterns for p in gate)


# ─── Category scorers ────────────────────────────────────────────────────────

def score_wa(code: str) -> Dict[str, int]:
    retry = 5 if re.search(r"Retry\(", code) and re.search(r"backoff_factor", code) else (
            3 if re.search(r"Retry\(", code) else 0)
    timeout = 5 if re.search(r"timeout=\(\d+,\s*\d+\)", code) else (
              3 if re.search(r"timeout=", code) else 0)
    error_cls = 5 if all(p in code for p in ["Timeout", "ConnectionError", "HTTPError"]) else (
                3 if re.search(r"except requests", code) else 0)
    session = 5 if re.search(r"Session\(\)", code) and re.search(r"HTTPAdapter", code) else (
              3 if re.search(r"Session\(\)", code) else 0)
    return {"retry": retry, "timeout": timeout, "error_classify": error_cls, "session": session}


def score_fs_raw(code: str) -> Dict[str, int]:
    """Return raw sub-item scores (gating is applied in score_task)."""
    pathlib = 5 if re.search(r"from pathlib import|Path\(", code) else 0
    encoding = 5 if re.search(r"encoding.*utf", code) and re.search(r"(cp949|UnicodeDecodeError|errors=)", code) else (
               3 if re.search(r"encoding.*utf", code) else 0)
    traversal = 5 if re.search(r"resolve\(\)", code) and re.search(r"startswith", code) else (
                3 if re.search(r"basename|resolve", code) else 0)
    safe_write = 5 if re.search(r"\.tmp", code) and re.search(r"replace\(", code) else (
                 3 if re.search(r"mkdir.*exist_ok|write_text", code) else 0)
    return {"pathlib": pathlib, "encoding": encoding, "traversal": traversal, "safe_write": safe_write}


def score_al(code: str) -> Dict[str, int]:
    zero_div = 5 if re.search(r"(denominator == 0|if.*den.*==.*0|ZeroDivision)", code) else (
               3 if re.search(r"safe_divide", code) else 0)
    range_check = 5 if re.search(r"(IndexError|len\(|< len|range_check|index.*<|>= 0)", code) else (
                  3 if re.search(r"isinstance.*int", code) else 0)
    type_valid = 5 if re.search(r"isinstance.*int|isinstance.*float|isinstance.*str", code) and re.search(r"raise (TypeError|ValueError)", code) else (
                 3 if re.search(r"isinstance", code) else 0)
    empty_data = 5 if re.search(r"(len\(.*\) == 0|not.*data|not isinstance.*list|empty)", code) else (
                 3 if re.search(r"if not", code) else 0)
    return {"zero_div": zero_div, "range_check": range_check, "type_valid": type_valid, "empty_data": empty_data}


def score_sec_raw(code: str) -> Dict[str, int]:
    """Return raw sub-item scores (gating is applied in score_task)."""
    cred = 5 if re.search(r"os\.environ\.get\(", code) and re.search(r"raise (EnvironmentError|ValueError)", code) else (
           3 if re.search(r"os\.environ", code) else 0)
    sanitize = 5 if re.search(r"html\.escape", code) and re.search(r"strip\(\)", code) else (
               3 if re.search(r"html\.escape|sanitize", code) else 0)
    injection = 5 if re.search(r"(html\.escape|prevent_injection|re\.sub.*pattern|parameterized)", code) else (
                3 if re.search(r"(strip|escape)", code) else 0)
    path_v = 5 if re.search(r"(startswith|basename|sanitize_filename)", code) and re.search(r"Path\(", code) else (
             3 if re.search(r"(basename|sanitize_filename)", code) else 0)
    return {"credential": cred, "sanitize": sanitize, "injection": injection, "path_verify": path_v}


def score_pd(code: str) -> Dict[str, int]:
    module_sep = 5 if re.search(r"(class.*Error.*Exception|class.*Result|dataclass)", code) else (
                 3 if re.search(r"TypedDict", code) else 0)
    interface = 5 if re.search(r"TypedDict", code) and re.search(r"Optional\[", code) else (
                3 if re.search(r"(TypedDict|dataclass)", code) else 0)
    error_prop = 5 if re.search(r"class.*Error.*Exception", code) and re.search(r"raise.*Error\(", code) else (
                 3 if re.search(r"raise", code) else 0)
    edge_case = 5 if re.search(r"(not isinstance|len\(.*\) == 0|None|empty)", code) and re.search(r"(raise|return.*False|return.*None)", code) else (
                3 if re.search(r"(isinstance|len\()", code) else 0)
    return {"module_sep": module_sep, "interface": interface, "error_prop": error_prop, "edge_case": edge_case}


def score_ui_raw(code: str) -> Dict[str, int]:
    """Return raw sub-item scores (gating is applied in score_task)."""
    type_hint = 4 if re.search(r"def \w+\(self.*\) -> ", code) and re.search(r"from typing import", code) else (
                2 if re.search(r"-> None", code) else 0)
    naming = 4 if re.search(r"self\.(btn|entry|lbl|text|listbox|frame|combo|progress)_", code) else (
             2 if re.search(r"self\.btn|self\.entry|self\.lbl", code) else 0)
    threading_ = 4 if re.search(r"threading\.Thread", code) and re.search(r"daemon=True", code) else (
                 2 if re.search(r"threading", code) else 0)
    loading = 4 if re.search(r"(Progressbar|indeterminate|progress.*start|config.*state.*disabled)", code) else (
              2 if re.search(r"(lbl_status|config.*text)", code) else 0)
    validation = 4 if re.search(r"(\.get\(\)\.strip\(\)|if not.*:)", code) and re.search(r"(lbl_status.*config|Error)", code) else (
                 2 if re.search(r"\.get\(\)", code) else 0)
    return {"type_hint": type_hint, "naming": naming, "threading": threading_,
            "loading": loading, "validation": validation}


def score_build(code: str) -> Dict[str, int]:
    exe_gen = 5 if re.search(r"--onefile", code) else 0
    resource = 5 if re.search(r"(_MEIPASS|add.data|datas)", code) else (
               3 if re.search(r"(resource|assets|Base64)", code) else 0)
    console = 5 if re.search(r"--windowed", code) else 0
    report = 5 if re.search(r"<build_report>", code) and re.search(r"section_[16]", code) else (
             3 if re.search(r"BUILD_REPORT|build_report", code) else 0)
    return {"exe_gen": exe_gen, "resource": resource, "console": console, "report": report}


# Sub-item max points per category
WA_MAX = {"retry": 5, "timeout": 5, "error_classify": 5, "session": 5}
FS_MAX = {"pathlib": 5, "encoding": 5, "traversal": 5, "safe_write": 5}
AL_MAX = {"zero_div": 5, "range_check": 5, "type_valid": 5, "empty_data": 5}
SEC_MAX = {"credential": 5, "sanitize": 5, "injection": 5, "path_verify": 5}
PD_MAX = {"module_sep": 5, "interface": 5, "error_prop": 5, "edge_case": 5}
UI_MAX = {"type_hint": 4, "naming": 4, "threading": 4, "loading": 4, "validation": 4}
BUILD_MAX = {"exe_gen": 5, "resource": 5, "console": 5, "report": 5}


def apply_na_filter(
    raw_scores: Dict[str, int],
    max_map: Dict[str, int],
    requires_map: Dict[str, List[str]],
    task_patterns: List[str],
) -> Tuple[Dict[str, Any], int, int]:
    """
    Apply N/A gating to sub-items.
    Returns (details_dict, actual_score, actual_max).
    N/A sub-items get value 'N/A' in the details dict.
    """
    details: Dict[str, Any] = {}
    actual_score = 0
    actual_max = 0

    for key, raw in raw_scores.items():
        if is_required(key, requires_map, task_patterns):
            details[key] = raw
            actual_score += raw
            actual_max += max_map[key]
        else:
            details[key] = "N/A"

    return details, actual_score, actual_max


def score_task(task: Dict[str, Any]) -> Dict[str, Any]:
    task_id = task["id"]
    tags = task["category_tags"]
    required_patterns: List[str] = task.get("required_patterns", [])
    code = read_solution(task_id)

    category_scores: Dict[str, Any] = {}
    total = 0
    max_total = 0

    for tag in ["WA", "FS", "AL", "SEC", "PD", "UI", "BUILD"]:
        if tag not in tags:
            category_scores[tag] = "N/A"
            continue

        if tag == "WA":
            raw = score_wa(code)
            # WA has no conditional sub-items — all always required
            cat_score = sum(raw.values())
            cat_max = sum(WA_MAX.values())
            category_scores[tag] = {
                "score": cat_score,
                "max": cat_max,
                "details": raw,
            }
            total += cat_score
            max_total += cat_max

        elif tag == "FS":
            raw = score_fs_raw(code)
            details, cat_score, cat_max = apply_na_filter(
                raw, FS_MAX, FS_SUBITEM_REQUIRES, required_patterns
            )
            category_scores[tag] = {"score": cat_score, "max": cat_max, "details": details}
            total += cat_score
            max_total += cat_max

        elif tag == "AL":
            raw = score_al(code)
            # AL has no conditional sub-items — all always required
            cat_score = sum(raw.values())
            cat_max = sum(AL_MAX.values())
            category_scores[tag] = {
                "score": cat_score,
                "max": cat_max,
                "details": raw,
            }
            total += cat_score
            max_total += cat_max

        elif tag == "SEC":
            raw = score_sec_raw(code)
            details, cat_score, cat_max = apply_na_filter(
                raw, SEC_MAX, SEC_SUBITEM_REQUIRES, required_patterns
            )
            category_scores[tag] = {"score": cat_score, "max": cat_max, "details": details}
            total += cat_score
            max_total += cat_max

        elif tag == "PD":
            raw = score_pd(code)
            # PD has no conditional sub-items — all always required
            cat_score = sum(raw.values())
            cat_max = sum(PD_MAX.values())
            category_scores[tag] = {
                "score": cat_score,
                "max": cat_max,
                "details": raw,
            }
            total += cat_score
            max_total += cat_max

        elif tag == "UI":
            raw = score_ui_raw(code)
            details, cat_score, cat_max = apply_na_filter(
                raw, UI_MAX, UI_SUBITEM_REQUIRES, required_patterns
            )
            category_scores[tag] = {"score": cat_score, "max": cat_max, "details": details}
            total += cat_score
            max_total += cat_max

        elif tag == "BUILD":
            raw = score_build(code)
            cat_score = sum(raw.values())
            cat_max = sum(BUILD_MAX.values())
            category_scores[tag] = {
                "score": cat_score,
                "max": cat_max,
                "details": raw,
            }
            total += cat_score
            max_total += cat_max

    percentage = round(total / max_total * 100, 1) if max_total > 0 else 0.0
    verdict = "PASS" if percentage >= 80 else "FAIL"

    low_cats: List[Tuple[float, str]] = []
    for tag in tags:
        cs = category_scores.get(tag)
        if isinstance(cs, dict) and cs.get("max", 0) > 0:
            pct = cs["score"] / cs["max"] * 100
            if pct < 100:
                low_cats.append((pct, tag))
    low_cats.sort()
    improvement_priority = [t for _, t in low_cats]

    return {
        "id": task_id,
        "total_score": total,
        "max_possible": max_total,
        "percentage": percentage,
        "verdict": verdict,
        "category_scores": category_scores,
        "improvement_priority": improvement_priority,
        "critical_flaw": None,
    }


def main() -> None:
    with open(HARNESS_FILE, encoding="utf-8") as f:
        harness = json.load(f)
    tasks: List[Dict[str, Any]] = harness["tasks"]

    results = []
    with open(RESULTS_FILE, "w", encoding="utf-8") as out:
        for task in tasks:
            result = score_task(task)
            line = json.dumps(result, ensure_ascii=False)
            out.write(line + "\n")
            results.append(result)
            print(f"  {result['id']:12s} {result['percentage']:6.1f}%  {result['verdict']}")

    # Summary
    pass_count = sum(1 for r in results if r["verdict"] == "PASS")
    fail_count = len(results) - pass_count
    avg_pct = sum(r["percentage"] for r in results) / len(results)

    by_cat: Dict[str, Dict[str, Any]] = {}
    for r in results:
        for tag, cs in r["category_scores"].items():
            if isinstance(cs, dict) and cs.get("max", 0) > 0:
                if tag not in by_cat:
                    by_cat[tag] = {"total_pct": 0.0, "tasks": 0}
                pct = cs["score"] / cs["max"] * 100
                by_cat[tag]["total_pct"] += pct
                by_cat[tag]["tasks"] += 1

    cat_summary = {
        tag: {"avg": round(v["total_pct"] / v["tasks"], 1), "tasks": v["tasks"]}
        for tag, v in by_cat.items()
    }

    summary = {
        "total_tasks": len(results),
        "pass_count": pass_count,
        "fail_count": fail_count,
        "pass_rate_percent": round(pass_count / len(results) * 100, 1),
        "average_score_percent": round(avg_pct, 1),
        "by_category": cat_summary,
    }

    with open(SUMMARY_FILE, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    print("\n" + "=" * 50)
    print(f"TOTAL: {len(results)} tasks | PASS: {pass_count} | FAIL: {fail_count}")
    print(f"Pass Rate: {summary['pass_rate_percent']}% | Avg Score: {summary['average_score_percent']}%")
    print("\nBy Category:")
    for tag, v in sorted(cat_summary.items()):
        print(f"  {tag:6s}: {v['avg']:5.1f}%  ({v['tasks']} tasks)")


if __name__ == "__main__":
    main()
