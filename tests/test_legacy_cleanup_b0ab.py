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
def test_user_confirmed_only_blocked(tmp_path):
    """--user-confirmed 단독 ACCEPT 차단 확인.

    gates accept --user-confirmed 는 acceptance_code_required 오류로 즉시
    실패해야 합니다. PIPELINE_STATE_PATH 격리를 사용하여 전역 상태를 건드리지 않습니다.
    """
    import json
    import os

    # 격리된 pipeline state 파일 생성 (최소 valid state)
    isolated_state = tmp_path / "pipeline_state.json"
    isolated_state.write_text(
        json.dumps({
            "pipeline_id": "IMP-20260531-B0AB",
            "current_phase": "sec",
            "phase_attestations": {"enabled": True},
            "three_gate": True,
        }),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["PIPELINE_STATE_PATH"] = str(isolated_state)

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
        env=env,
    )
    combined = result.stdout + result.stderr
    # gates accept --user-confirmed は 즉시 차단되어야 함
    assert result.returncode != 0, (
        "--user-confirmed 단독 ACCEPT 가 returncode 0으로 종료되어서는 안 됩니다"
    )
    # acceptance_code_required 또는 acceptance_request가 없다는 메시지 중 하나
    assert (
        "acceptance_code_required" in combined
        or "acceptance_code" in combined
        or "--acceptance-code" in combined
        or "acceptance_request" in combined
    ), (
        f"차단 메시지가 없습니다. 출력: {combined[:300]}"
    )
    # final_state: 격리된 state 파일이 변경되지 않았음을 확인 (차단 경로라면 state mutation 없어야 함)
    final_state = json.loads(isolated_state.read_text(encoding="utf-8"))
    assert final_state.get("pipeline_id") == "IMP-20260531-B0AB", (
        "격리된 state가 의도치 않게 변경되었습니다"
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


# ---------------------------------------------------------------------------
# 테스트 6: _is_internal_artifact() 변형 파일명 접두사 패턴 감지 확인
# ---------------------------------------------------------------------------
def test_preflight_catches_variant_filenames():
    """_is_internal_artifact()이 파일명 변형(접두사 포함)을 올바르게 잡는지 검증합니다."""
    import os
    sys.path.insert(0, str(REPO_ROOT))
    from pipeline import _is_internal_artifact  # noqa: PLC0415

    # 변형 파일명 (WORKSPACE_INTERNAL_PATTERNS 확장으로 새로 잡아야 하는 케이스)
    assert _is_internal_artifact("build_report_075a.xml"), "build_report 변형 미감지"
    assert _is_internal_artifact("pipeline_state_TMP_backup.json"), "pipeline_state_TMP 미감지"
    assert _is_internal_artifact("qa_report_fix.xml"), "qa_report 변형 미감지"
    assert _is_internal_artifact("comment_4584990276.txt"), "comment_ 변형 미감지"
    assert _is_internal_artifact("pr_body_final.txt"), "pr_body_ 변형 미감지"

    # 기존 정확한 매칭도 여전히 동작해야 함
    assert _is_internal_artifact("build_report.xml"), "build_report.xml 기존 매칭 깨짐"
    assert _is_internal_artifact("qa_report.xml"), "qa_report.xml 기존 매칭 깨짐"
    assert _is_internal_artifact("failure_packet.json"), "failure_packet.json 기존 매칭 깨짐"

    # tests/oracles/ 경로는 허용되어야 함 (내부 산출물 아님)
    assert not _is_internal_artifact(
        "tests/oracles/IMP-20260531-B0AB/normal/input.json"
    ), "tests/oracles/ 경로를 내부 산출물로 잘못 분류"

    _ = os  # suppress unused import warning
