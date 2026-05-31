"""IMP-20260531-B0AB 회귀 테스트.

검증 항목:
1. pipeline.py에서 dead helper 3개 삭제 확인
2. harness --score 완료 경로 차단 확인
3. --user-confirmed 단독 ACCEPT 차단 확인
4. root docs (CLAUDE.md/AGENTS.md/README.md)에 성공 경로 stale 문구 없음
5. agent MD/TOML 파일에 성공 경로 stale 문구 없음
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.resolve()


def _read_utf8(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 성공 경로 stale 문구 판별
# 아래 패턴은 --user-confirmed 가 ACCEPT 성공 경로로 설명되는 경우만 매칭.
# "더 이상 ACCEPT를 통과시키지 않는다", "BLOCKED 처리됨", "acceptance_code_required"
# 같은 차단/금지 설명에서 인용된 경우는 허용.
# ---------------------------------------------------------------------------

# 스캔 대상: gates accept --result ACCEPT ... --user-confirmed 로 끝나는 라인
# (즉, 성공 명령 예시 형태)
_SUCCESS_CMD_EXAMPLE_RE = re.compile(
    r"gates\s+accept\s+--result\s+ACCEPT\b[^\n]*\b--user-confirmed\b",
    re.IGNORECASE,
)

# 차단 문구 키워드: 이 단어 포함 줄은 허용
_BLOCK_KEYWORDS = (
    "BLOCKED",
    "blocked",
    "더 이상",
    "통과시키지",
    "acceptance_code_required",
    "forbidden",
    "단독으로는",
)


def _has_stale_success_path(text: str) -> list[str]:
    """stale 성공 경로 패턴이 있으면 매칭된 문자열 목록 반환."""
    hits: list[str] = []
    for m in _SUCCESS_CMD_EXAMPLE_RE.finditer(text):
        # 매칭된 라인에 차단 키워드가 포함되어 있으면 허용 (설명용 인용)
        line_start = text.rfind("\n", 0, m.start()) + 1
        line_end = text.find("\n", m.end())
        if line_end == -1:
            line_end = len(text)
        line = text[line_start:line_end]
        if any(kw in line for kw in _BLOCK_KEYWORDS):
            continue
        hits.append(line[:120])
    return hits


# ---------------------------------------------------------------------------
# 테스트 1: dead helper 함수 삭제 확인
# ---------------------------------------------------------------------------
def test_dead_helpers_removed():
    src = _read_utf8(REPO_ROOT / "pipeline.py")
    for fname in (
        "def _is_budget_blocked",
        "def _wait_for_github_ci",
        "def _verify_allowed_files",
    ):
        assert fname not in src, (
            f"pipeline.py에 삭제되어야 할 함수가 여전히 존재합니다: {fname}"
        )


# ---------------------------------------------------------------------------
# 테스트 2: harness --score 차단 확인
# ---------------------------------------------------------------------------
def test_harness_score_blocked():
    result = subprocess.run(
        [sys.executable, "pipeline.py", "harness", "--score", "100", "--verdict", "PASS"],
        capture_output=True,
        cwd=str(REPO_ROOT),
        timeout=30,
        encoding="utf-8",
        errors="replace",
    )
    combined = result.stdout + result.stderr
    assert result.returncode != 0, (
        "harness --score 가 returncode 0으로 종료되어서는 안 됩니다"
    )
    assert "THREE GATE BLOCKED" in combined or "THREE_GATE_BLOCKED" in combined, (
        f"THREE GATE BLOCKED 메시지가 없습니다. 출력: {combined[:300]}"
    )


# ---------------------------------------------------------------------------
# 테스트 3: --user-confirmed 단독 ACCEPT 차단 확인
# ---------------------------------------------------------------------------
def test_user_confirmed_only_blocked():
    result = subprocess.run(
        [
            sys.executable, "pipeline.py",
            "gates", "accept",
            "--result", "ACCEPT",
            "--evidence", "nonexistent.txt",
            "--user-confirmed",
        ],
        capture_output=True,
        cwd=str(REPO_ROOT),
        timeout=30,
        encoding="utf-8",
        errors="replace",
    )
    combined = result.stdout + result.stderr
    assert result.returncode != 0, (
        "--user-confirmed 단독 ACCEPT 가 returncode 0으로 종료되어서는 안 됩니다"
    )
    assert "acceptance_code_required" in combined, (
        f"acceptance_code_required 메시지가 없습니다. 출력: {combined[:300]}"
    )
    assert "더 이상 ACCEPT를 통과시키지 않습니다" in combined, (
        f"한국어 경고 문구가 없습니다. 출력: {combined[:300]}"
    )


# ---------------------------------------------------------------------------
# 테스트 4: root docs stale 문구 없음
# ---------------------------------------------------------------------------
def test_stale_phrases_removed_in_root_docs():
    docs = ["CLAUDE.md", "AGENTS.md", "README.md"]
    for doc_name in docs:
        path = REPO_ROOT / doc_name
        if not path.exists():
            continue
        text = _read_utf8(path)
        hits = _has_stale_success_path(text)
        assert not hits, (
            f"{doc_name}에 --user-confirmed 성공 경로 stale 문구가 남아 있습니다:\n"
            + "\n".join(hits)
        )


# ---------------------------------------------------------------------------
# 테스트 5: agent MD/TOML 파일 stale 문구 없음
# ---------------------------------------------------------------------------
def test_agent_md_files_have_no_user_confirmed_success_path():
    agent_files = [
        REPO_ROOT / ".codex" / "agents" / "build-agent.toml",
        REPO_ROOT / ".codex" / "agents" / "test-harness-agent.toml",
        REPO_ROOT / ".codex" / "agents" / "pm-agent.toml",
        REPO_ROOT / ".claude" / "agents" / "pm-agent.md",
    ]
    for fpath in agent_files:
        if not fpath.exists():
            continue
        text = _read_utf8(fpath)
        hits = _has_stale_success_path(text)
        assert not hits, (
            f"{fpath.name}에 --user-confirmed 성공 경로 stale 문구가 남아 있습니다:\n"
            + "\n".join(hits)
        )
