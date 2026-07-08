"""MT-29: Codex/acceptance PR body SHA 불변식 hard gate 테스트.

검증 대상:
  1. _check_codex_pr_body_sha_invariant: SHA 일치 시 PASS
  2. _check_codex_pr_body_sha_invariant: SHA 불일치 시 BLOCKED (올바른 failure_code)
  3. _check_codex_pr_body_sha_invariant: codex_result에 SHA 없으면 codex_pr_body_sha_missing
  4. "CODEX SHA 동기화" 자동 override 코드가 pipeline.py에 없음 확인 (grep)
  5. .claude/hooks/codex_user_acceptance_review.py 파일이 존재하지 않음
  6. .claude/settings.json Stop hooks에 codex 관련 항목 없음
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import pytest

# pipeline.py를 직접 import하기 위해 프로젝트 루트를 sys.path에 추가
_REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from pipeline import _check_codex_pr_body_sha_invariant  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# TC-1: SHA 일치 → PASS
# ─────────────────────────────────────────────────────────────────────────────
def test_sha_match_returns_ok():
    """codex_review_result의 SHA와 staged candidate SHA가 일치하면 ok=True."""
    sha = "abc123def456" * 5  # 60자 — 형식 검사 없음
    codex_result = {"pr_body_candidate_sha256": sha, "verdict": "APPROVE_TO_USER"}
    result = _check_codex_pr_body_sha_invariant(codex_result, sha)
    assert result.get("ok") is True


# ─────────────────────────────────────────────────────────────────────────────
# TC-2: SHA 불일치 → BLOCKED
# ─────────────────────────────────────────────────────────────────────────────
def test_sha_mismatch_returns_blocked():
    """codex SHA != staged SHA이면 ok=False, failure_code=codex_pr_body_sha_mismatch."""
    codex_sha = "aaaa1111bbbb2222"
    staged_sha = "cccc3333dddd4444"
    codex_result = {
        "pr_body_candidate_sha256": codex_sha,
        "verdict": "APPROVE_TO_USER",
    }
    result = _check_codex_pr_body_sha_invariant(codex_result, staged_sha)
    assert result.get("ok") is False
    assert result.get("failure_code") == "codex_pr_body_sha_mismatch"
    assert "message" in result


# ─────────────────────────────────────────────────────────────────────────────
# TC-3: SHA 필드 없음 → codex_pr_body_sha_missing
# ─────────────────────────────────────────────────────────────────────────────
def test_missing_sha_field_returns_missing_code():
    """codex_result에 pr_body_sha256/pr_body_candidate_sha256가 없으면 missing failure_code."""
    codex_result = {"verdict": "APPROVE_TO_USER"}
    result = _check_codex_pr_body_sha_invariant(codex_result, "somesha256")
    assert result.get("ok") is False
    assert result.get("failure_code") == "codex_pr_body_sha_missing"


# ─────────────────────────────────────────────────────────────────────────────
# TC-4: "CODEX SHA 동기화" 자동 override 코드가 pipeline.py에 없음 (grep)
# ─────────────────────────────────────────────────────────────────────────────
def test_no_codex_sha_auto_override_in_pipeline():
    """pipeline.py에 'CODEX SHA 동기화' 자동 override 코드(pr_body_sha256 덮어쓰기)가 없어야 한다."""
    pipeline_py = _REPO_ROOT / "pipeline.py"
    content = pipeline_py.read_text(encoding="utf-8", errors="replace")

    # 자동 덮어쓰기 패턴: _cx_sync["pr_body_sha256"] = staged_pr_body_sha 형태
    # MT-29에서 제거된 라인이 없어야 함
    override_pattern = re.compile(
        r'_cx_sync\["pr_body_sha256"\]\s*=\s*staged_pr_body_sha',
        re.MULTILINE,
    )
    matches = override_pattern.findall(content)
    assert len(matches) == 0, (
        f"'CODEX SHA 동기화' 자동 override 코드가 pipeline.py에 아직 남아 있습니다: "
        f"{len(matches)}건 발견"
    )


# ─────────────────────────────────────────────────────────────────────────────
# TC-5: Stop hook 파일이 존재하지 않음
# ─────────────────────────────────────────────────────────────────────────────
def test_codex_hook_py_deleted():
    """.claude/hooks/codex_user_acceptance_review.py 파일이 삭제되어 없어야 한다."""
    hook_py = _REPO_ROOT / ".claude" / "hooks" / "codex_user_acceptance_review.py"
    assert not hook_py.exists(), (
        f"Stop hook 파일이 아직 존재합니다: {hook_py} — 삭제가 필요합니다."
    )


# ─────────────────────────────────────────────────────────────────────────────
# TC-6: settings.json Stop hooks에 codex 관련 항목 없음
# ─────────────────────────────────────────────────────────────────────────────
def test_settings_json_no_codex_stop_hook():
    """.claude/settings.json의 Stop hooks에 codex 관련 command가 없어야 한다."""
    settings_path = _REPO_ROOT / ".claude" / "settings.json"
    assert settings_path.exists(), f"settings.json을 찾을 수 없습니다: {settings_path}"
    data = json.loads(settings_path.read_text(encoding="utf-8"))

    stop_hooks = data.get("hooks", {}).get("Stop", [])
    for group in stop_hooks:
        for hook_entry in group.get("hooks", []):
            cmd = hook_entry.get("command", "")
            assert "codex" not in cmd.lower(), (
                f"Stop hooks에 codex 관련 command가 남아 있습니다: {cmd!r}"
            )
            assert "acceptance-review" not in cmd.lower(), (
                f"Stop hooks에 acceptance-review command가 남아 있습니다: {cmd!r}"
            )
