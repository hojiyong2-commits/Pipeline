# [Purpose]: IMP-20260627-3907 User Acceptance 승인 요청문 renderer 단일 SSoT 검증.
#   A형/B형 양식, contract fail-closed 로드, contract 11개 항목, 운영 코드(pipeline.py/hook)의
#   renderer 단일 소스 사용, PR URL/pipeline_id 정확 포함을 검증한다.
# [Assumptions]: .claude/acceptance_renderer.py / .claude/codex_review_contract.md /
#   .claude/hooks/codex_user_acceptance_review.py / pipeline.py가 존재하고 import 가능하다.
#   oracle 파일은 tests/oracles/IMP-20260627-3907/ 하위에 있으며 SSoT로 사용한다.
# [Vulnerability & Risks]: 운영 코드의 renderer 사용 검사는 소스 텍스트 정적 검사라 동적 우회는
#   잡지 못한다. importlib 로드 검사로 보강한다.
# [Improvement]: 시간이 더 있다면 AST로 render 함수 정의/호출 그래프를 추적할 것이다.
"""User Acceptance renderer 단일 SSoT 검증 테스트 (IMP-20260627-3907)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_RENDERER_PATH = _REPO_ROOT / ".claude" / "acceptance_renderer.py"
_CONTRACT_PATH = _REPO_ROOT / ".claude" / "codex_review_contract.md"
_HOOK_PATH = _REPO_ROOT / ".claude" / "hooks" / "codex_user_acceptance_review.py"
_PIPELINE_PATH = _REPO_ROOT / "pipeline.py"
_AGENT_MD_PATH = _REPO_ROOT / ".claude" / "agents" / "pipeline-manager-agent.md"
_ORACLE_DIR = _REPO_ROOT / "tests" / "oracles" / "IMP-20260627-3907"

_PIPELINE_ID = "IMP-20260627-3907"
_PR_URL = "https://github.com/hojiyong2-commits/Pipeline/pull/748"


def _load_renderer():
    """acceptance_renderer 모듈을 importlib로 로드하여 반환."""
    spec = importlib.util.spec_from_file_location(
        "acceptance_renderer_under_test", str(_RENDERER_PATH)
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _read_text(path: Path) -> str:
    """utf-8 → cp949 → latin-1 fallback 읽기."""
    for enc in ("utf-8", "utf-8-sig", "cp949", "latin-1"):
        try:
            return path.read_text(encoding=enc)
        except (UnicodeDecodeError, LookupError):
            continue
    raise AssertionError(f"cannot read {path}")


rn = _load_renderer()


# ---------------------------------------------------------------------------
# A) A형 exact match (CODEX 검토 필요 포함)
# ---------------------------------------------------------------------------
def test_a_form_snapshot():
    """A형 출력이 oracle normal_A_form/expected.txt와 일치한다(CODEX 검토 필요 포함)."""
    out = rn.render_user_acceptance_request(
        mode="codex_review_required", pr_url=_PR_URL, pipeline_id=_PIPELINE_ID
    )
    expected = _read_text(_ORACLE_DIR / "normal_A_form" / "expected.txt")
    # expected.txt는 끝에 단일 newline 포함, render는 trailing newline 미포함
    assert out + "\n" == expected
    assert "CODEX 검토 필요" in out


# ---------------------------------------------------------------------------
# B) B형 exact match (CODEX 검토 필요 미포함)
# ---------------------------------------------------------------------------
def test_b_form_snapshot():
    """B형 출력이 oracle normal_B_form/expected.txt와 일치한다(CODEX 검토 필요 미포함)."""
    out = rn.render_user_acceptance_request(
        mode="user_final", pr_url=_PR_URL, pipeline_id=_PIPELINE_ID
    )
    expected = _read_text(_ORACLE_DIR / "normal_B_form" / "expected.txt")
    assert out + "\n" == expected
    assert "CODEX 검토 필요" not in out


# ---------------------------------------------------------------------------
# C) contract 부재 시 RuntimeError (fail-closed)
# ---------------------------------------------------------------------------
def test_contract_missing_fail_closed():
    """없는 contract 경로에서 load_contract가 RuntimeError를 발생시킨다."""
    with pytest.raises(RuntimeError) as exc:
        rn.load_contract("/nonexistent/path/codex_review_contract.md")
    assert "contract" in str(exc.value).lower()


# ---------------------------------------------------------------------------
# D) contract 11개 항목 키워드 검사
# ---------------------------------------------------------------------------
def test_contract_11_items():
    """codex_review_contract.md가 11개 항목 키워드를 모두 포함한다."""
    txt = rn.load_contract(str(_CONTRACT_PATH))
    keywords = [
        "자동 ACCEPT",        # 1
        "nonce",              # 2
        "근본 원인",          # 3
        "fail-open",          # 4
        "SHA",                # 5
        "scratch",            # 6
        "placeholder",        # 7
        "되돌리",             # 8
        "원인 제거",          # 9
        "APPROVE_TO_USER",    # 10
        "에스컬레이션",       # 11
    ]
    missing = [k for k in keywords if k not in txt]
    assert missing == [], f"contract 누락 키워드: {missing}"
    # 11개 ## 섹션 존재
    assert txt.count("\n## ") >= 11


# ---------------------------------------------------------------------------
# E) 운영 코드가 renderer를 단일 소스에서 로드 (중복 정의 없음)
# ---------------------------------------------------------------------------
def test_single_renderer_source():
    """pipeline.py와 hook이 render_user_acceptance_request를 자체 def하지 않고 importlib로 로드한다."""
    pipeline_src = _read_text(_PIPELINE_PATH)
    hook_src = _read_text(_HOOK_PATH)
    # 운영 코드에 render 함수 자체 정의(def)가 없어야 한다 — renderer SSoT만 정의.
    assert "def render_user_" + "acceptance_request" not in pipeline_src
    assert "def render_user_" + "acceptance_request" not in hook_src
    # 두 운영 코드 모두 acceptance_renderer를 importlib로 로드해야 한다.
    assert "acceptance_renderer.py" in pipeline_src
    assert "acceptance_renderer" in hook_src
    assert "spec_from_file_location" in pipeline_src
    assert "spec_from_file_location" in hook_src
    # renderer 모듈 자체에만 함수 정의가 존재한다 (단일 SSoT).
    renderer_src = _read_text(_RENDERER_PATH)
    assert renderer_src.count("def render_user_" + "acceptance_request") == 1


# ---------------------------------------------------------------------------
# F) PR URL / pipeline_id가 출력에 정확히 포함
# ---------------------------------------------------------------------------
def test_pr_url_pipeline_id_normalization():
    """PR URL과 pipeline_id가 A형/B형 출력에 정확히 포함된다."""
    pr = "https://github.com/owner/repo/pull/999"
    pid = "IMP-20260627-ABCD"
    for mode in ("codex_review_required", "user_final"):
        out = rn.render_user_acceptance_request(mode=mode, pr_url=pr, pipeline_id=pid)
        assert f"PR: {pr}" in out
        assert f"ACCEPT-{pid}" in out
        # 줄 단위로 PR/승인 코드가 정확히 한 줄로 존재
        assert any(ln == f"PR: {pr}" for ln in out.splitlines())
        assert any(ln == f"ACCEPT-{pid}" for ln in out.splitlines())


# ---------------------------------------------------------------------------
# 추가: mode 화이트리스트 ValueError
# ---------------------------------------------------------------------------
def test_invalid_mode_raises():
    """허용되지 않은 mode는 ValueError를 발생시킨다."""
    with pytest.raises(ValueError):
        rn.render_user_acceptance_request(mode="bad", pr_url=_PR_URL, pipeline_id=_PIPELINE_ID)


# ---------------------------------------------------------------------------
# 추가: hook APPROVE 출력이 renderer B형 사용
# ---------------------------------------------------------------------------
def test_hook_approve_uses_renderer_b():
    """hook의 APPROVE 출력이 renderer B형(CODEX 검토 필요 미포함)을 사용한다."""
    spec = importlib.util.spec_from_file_location(
        "hook_under_test_3907", str(_HOOK_PATH)
    )
    cx = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cx)
    out = cx.process_verdict("APPROVE_TO_USER", _PIPELINE_ID, _PR_URL, reject_count=0)
    assert out["decision"] == "APPROVE"
    # B형: CODEX 검토 필요 미포함, ACCEPT-pipeline_id 포함
    assert "CODEX 검토 필요" not in out["output"]
    assert f"ACCEPT-{_PIPELINE_ID}" in out["output"]
    assert out["output"].startswith("사용자 승인 요청")
    # B형은 renderer 출력과 동일해야 한다
    expected_b = rn.render_user_acceptance_request(
        mode="user_final", pr_url=_PR_URL, pipeline_id=_PIPELINE_ID
    )
    assert out["output"] == expected_b


# ---------------------------------------------------------------------------
# 추가: pipeline.py gates request-accept가 renderer A형 사용
# ---------------------------------------------------------------------------
def test_pipeline_request_accept_uses_renderer_a():
    """pipeline.py _cmd_gates_request_accept가 renderer A형(codex_review_required)을 호출한다."""
    pipeline_src = _read_text(_PIPELINE_PATH)
    # _cmd_gates_request_accept 함수 본문 추출
    start = pipeline_src.index("def _cmd_gates_request_accept")
    # 다음 def(들여쓰기 없는) 위치까지
    rest = pipeline_src[start + 1 :]
    end_rel = rest.find("\ndef ")
    body = rest if end_rel == -1 else rest[:end_rel]
    # renderer 호출 + 기본 A형 mode가 본문에 존재
    assert "render_user_" + "acceptance_request" in body
    assert "codex_review_required" in body
    # A형 출력이 기존 5줄 고정 양식과 동일함을 직접 검증
    a_form = rn.render_user_acceptance_request(
        mode="codex_review_required", pr_url=_PR_URL, pipeline_id=_PIPELINE_ID
    )
    legacy_lines = [
        "사용자 승인 요청",
        "",
        f"PR: {_PR_URL}",
        "",
        "승인 코드:",
        f"ACCEPT-{_PIPELINE_ID}",
        "",
        "CODEX 검토 필요",
    ]
    assert a_form == "\n".join(legacy_lines)


# ---------------------------------------------------------------------------
# 추가: agent MD에 renderer 규칙 포함
# ---------------------------------------------------------------------------
def test_agent_md_rule():
    """pipeline-manager-agent.md에 renderer 단일 SSoT 강제 규칙이 포함된다."""
    md = _read_text(_AGENT_MD_PATH)
    assert "renderer" in md
    assert "직접" in md
    assert "금지" in md
    assert "CODEX 검토 필요" in md
    # 승인 요청문을 직접 작성/재구성하지 말라는 규칙 존재
    assert "재구성" in md or "직접 승인 요청문" in md or "직접 작성" in md
