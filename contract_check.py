#!/usr/bin/env python3
"""
contract_check.py — Pipeline Contract Drift Detector
pipeline.py hard gate와 MD 파일 명령어 예시의 불일치를 자동 검출합니다.
실행: python contract_check.py
종료 코드: 0 = 위반 없음, 1 = 위반 발견
"""
import sys
import re
from pathlib import Path

# ── Force UTF-8 stdout on Windows cp949 environments ──
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

BASE = Path(__file__).parent

# 검사 규칙: list of (pattern, description, match_means_violation)
# match_means_violation=True: 패턴이 매칭되면 위반 (결과가 bad 형식)
# match_means_violation=False: 패턴이 매칭되지 않는 경우는 별도 로직으로 처리 (미사용)
#
# 전략: 각 규칙은 "잘못된 형태"를 직접 매칭한다.
#   - Rule 1: `check --phase harness` 뒤에 `--user-confirmed`가 없는 형태
#   - Rule 2: `done --phase pm` 뒤에 `--decomp` 또는 `[--decomp`가 없는 형태
#   - Rule 3: `done --phase dev --files ...` 에 `--scope-declared`가 없는 형태
#             → 행 전체에 --scope-declared가 없을 때만 위반
#   - Rule 4: `qa --result FAIL` 뒤에 `--failure-sig`가 없는 형태
RULES = [
    (
        r"check\s+--phase\s+harness(?!\s+--user-confirmed)",
        "check --phase harness 에는 --user-confirmed 필수 (pipeline.py line ~773)",
    ),
    (
        r"done\s+--phase\s+pm(?!\s+--decomp|\s+\[--decomp)",
        "done --phase pm 에는 --decomp --clarification 필수 (pipeline.py line ~839)",
    ),
    # Rule 3은 별도 함수로 처리 (아래 check_scope_declared_rule 참조)
    (
        r"qa\s+--result\s+FAIL(?![^\n]*--failure-sig)",
        "qa --result FAIL 에는 --failure-sig 필수 (pipeline.py line ~960)",
    ),
]

# Rule 3 전용: done --phase dev --files 패턴이 있는 행에 --scope-declared 없으면 위반
SCOPE_RULE_TRIGGER = re.compile(r"done\s+--phase\s+dev\s+--files")
SCOPE_RULE_REQUIRED = re.compile(r"--scope-declared")
SCOPE_RULE_DESC = "done --phase dev --files 에는 --scope-declared 필수 (pipeline.py line ~870)"

EXCLUDE_DIRS = {"node_modules", ".git", "__pycache__", "dist", "build", "pipeline_history"}
EXCLUDE_FILES = {"contract_check.py"}  # 자기 자신 제외
# 이력/설명 목적 파일 — 실제 명령어 예시가 아닌 변경 이력 기록이므로 드리프트 검사 제외
EXCLUDE_LOG_FILES = {"self_evolving_log.md", "anti_gaming_rules.md"}


def collect_md_files() -> list:
    files = []
    # .claude/ 내 모든 MD
    claude_dir = BASE / ".claude"
    if claude_dir.exists():
        for f in claude_dir.rglob("*.md"):
            if not any(ex in f.parts for ex in EXCLUDE_DIRS):
                if f.name not in EXCLUDE_LOG_FILES:
                    files.append(f)
    # 루트 MD (CLAUDE.md 등)
    for f in BASE.glob("*.md"):
        if f.name not in EXCLUDE_FILES and f.name not in EXCLUDE_LOG_FILES:
            files.append(f)
    return files


# prose 제외 패턴: 명령어 예시가 아닌 설명 문장임을 나타내는 패턴
# 예: "마지막 기록 그대로 유지", "추가." 등 — 동작 설명이지 명령어 호출이 아님
PROSE_EXCLUSION_PATTERNS = [
    re.compile(r"마지막\s+기록\s+그대로\s+유지"),  # CLAUDE.md Circuit Breaker 절차 설명
    re.compile(r"재기록\s+없음"),
    re.compile(r"추가\s*\(action="),               # self_evolving_log 설명 (혹시 누락 방지)
    re.compile(r"hard\s+gate\s+추가"),
]


def _is_prose_line(line: str) -> bool:
    """명령어 예시가 아닌 설명/이력 산문 줄인지 판단."""
    for pat in PROSE_EXCLUSION_PATTERNS:
        if pat.search(line):
            return True
    return False


def check_file(path: Path) -> list:
    violations = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return violations
    lines = text.splitlines()
    for lineno, line in enumerate(lines, 1):
        if _is_prose_line(line):
            continue  # 설명 문장 — 드리프트 검사 제외
        # Standard rules (negated lookahead patterns)
        for pattern, desc in RULES:
            if re.search(pattern, line):
                violations.append((lineno, line.strip()[:120], desc))
        # Rule 3: scope-declared check (two-step: trigger then verify)
        if SCOPE_RULE_TRIGGER.search(line) and not SCOPE_RULE_REQUIRED.search(line):
            violations.append((lineno, line.strip()[:120], SCOPE_RULE_DESC))
    return violations


def main():
    files = collect_md_files()
    total_violations = 0
    for f in sorted(files):
        violations = check_file(f)
        if violations:
            try:
                rel = f.relative_to(BASE)
            except ValueError:
                rel = f
            print(f"\n[DRIFT] {rel}")
            for lineno, snippet, desc in violations:
                print(f"  line {lineno}: {desc}")
                # Encode safely for any terminal
                safe_snippet = snippet.encode("utf-8", errors="replace").decode("utf-8", errors="replace")
                print(f"    > {safe_snippet}")
            total_violations += len(violations)
    print()
    if total_violations == 0:
        print("[OK] 계약 드리프트 없음 — 모든 MD 파일이 pipeline.py hard gate와 일치합니다.")
        sys.exit(0)
    else:
        print(
            f"[FAIL] 총 {total_violations}개 위반 발견 — "
            "MD 파일을 pipeline.py hard gate에 맞게 수정하거나 contract_check.py 규칙을 업데이트하세요."
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
