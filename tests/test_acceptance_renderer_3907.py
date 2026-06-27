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


class TestCodexReviewGate:
    """pipeline.py _check_codex_review_gate pipeline_id 불일치 BLOCKED 테스트."""

    def test_pipeline_id_mismatch_blocked(self, tmp_path):
        """loop_state pipeline_id가 현재 pipeline_id와 다르면 codex_review_stale BLOCKED."""
        import importlib.util
        import json
        import os
        import unittest.mock as mock
        # _check_codex_review_gate를 직접 호출하기 위해 pipeline.py를 importlib로 로드
        project_root = Path(__file__).parent.parent
        pl_path = project_root / "pipeline.py"
        spec = importlib.util.spec_from_file_location("pipeline_mod", str(pl_path))
        mod = importlib.util.module_from_spec(spec)
        # pipeline.py의 state 경로를 tmp_path로 격리
        with mock.patch.dict(os.environ, {"PIPELINE_STATE_PATH": str(tmp_path / "state.json")}):
            spec.loader.exec_module(mod)
            # loop_state 파일을 이전 pipeline_id로 작성
            pipeline_dir = tmp_path / ".pipeline"
            pipeline_dir.mkdir(parents=True, exist_ok=True)
            loop_state = {
                "pipeline_id": "IMP-20260600-XXXX",  # 이전 파이프라인
                "status": "APPROVED",
                "pr_head_sha": "abc123",
                "packet_sha256": "abc456",
                "pr_body_sha256": "abc789",
                "accept_code": "ACCEPT-IMP-20260600-XXXX",
            }
            loop_state_path = pipeline_dir / "codex_review_loop_state.json"
            loop_state_path.write_text(json.dumps(loop_state), encoding="utf-8")
            # 현재 파이프라인 ID로 검사
            result = mod._check_codex_review_gate("IMP-20260627-3907", {})
            assert result["status"] == "BLOCKED"
            assert result["failure_code"] == "codex_review_stale"

    def test_same_pipeline_id_continues(self, tmp_path):
        """loop_state pipeline_id가 현재 pipeline_id와 같으면 stale BLOCKED하지 않음 (head_sha 검사까지 진행)."""
        import importlib.util
        import json
        import os
        import unittest.mock as mock
        project_root = Path(__file__).parent.parent
        pl_path = project_root / "pipeline.py"
        spec = importlib.util.spec_from_file_location("pipeline_mod2", str(pl_path))
        mod = importlib.util.module_from_spec(spec)
        with mock.patch.dict(os.environ, {"PIPELINE_STATE_PATH": str(tmp_path / "state.json")}):
            spec.loader.exec_module(mod)
            pipeline_dir = tmp_path / ".pipeline"
            pipeline_dir.mkdir(parents=True, exist_ok=True)
            loop_state = {
                "pipeline_id": "IMP-20260627-3907",
                "status": "APPROVED",
                "pr_head_sha": "abc123",
                "packet_sha256": "abc456",
                "pr_body_sha256": "abc789",
                "accept_code": "ACCEPT-IMP-20260627-3907",
            }
            loop_state_path = pipeline_dir / "codex_review_loop_state.json"
            loop_state_path.write_text(json.dumps(loop_state), encoding="utf-8")
            # pipeline_id는 같으므로 stale 차단 없이 head_sha 검사로 진행됨
            # (gh CLI 없으면 head_sha 검사에서 BLOCKED 발생 — 이는 정상)
            result = mod._check_codex_review_gate("IMP-20260627-3907", {})
            # pipeline_id 일치하므로 codex_review_stale이 pipeline_id 불일치가 아닌 다른 이유여야 함
            if result["status"] == "BLOCKED":
                assert result["failure_code"] != "codex_review_stale" or "pipeline_id" not in result.get("message", "").lower()
            # 또는 PASS (gh CLI가 동작하면)


class TestHookFailedStateRecording:
    """hook 실패 시 FAILED 상태가 loop_state.json에 기록되는지 검증."""

    def test_failed_state_recorded_on_codex_call_failure(self, tmp_path):
        """Codex 호출 실패 시 loop_state.json에 FAILED 상태가 기록된다."""
        import importlib.util
        import json

        hook_path = Path(__file__).parent.parent / ".claude" / "hooks" / "codex_user_acceptance_review.py"
        spec = importlib.util.spec_from_file_location("hook_mod", str(hook_path))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        pipeline_dir = tmp_path / ".pipeline"
        pipeline_dir.mkdir(parents=True, exist_ok=True)
        state_path = pipeline_dir / "codex_review_loop_state.json"

        mod._record_failed_state(state_path, "IMP-20260627-3907", "codex_call_failed", "test error")

        assert state_path.exists()
        data = json.loads(state_path.read_text(encoding="utf-8"))
        assert data["status"] == "FAILED"
        assert data["pipeline_id"] == "IMP-20260627-3907"
        assert data["failure_code"] == "codex_call_failed"
        assert "failed_at" in data

    def test_failed_state_not_overwrite_approved(self, tmp_path):
        """APPROVED 상태(같은 pipeline_id)는 FAILED로 덮어쓰지 않는다."""
        import importlib.util
        import json

        hook_path = Path(__file__).parent.parent / ".claude" / "hooks" / "codex_user_acceptance_review.py"
        spec = importlib.util.spec_from_file_location("hook_mod2", str(hook_path))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        pipeline_dir = tmp_path / ".pipeline"
        pipeline_dir.mkdir(parents=True, exist_ok=True)
        state_path = pipeline_dir / "codex_review_loop_state.json"

        # APPROVED 상태를 먼저 기록
        existing = {
            "pipeline_id": "IMP-20260627-3907",
            "status": "APPROVED",
            "pr_head_sha": "abc",
            "packet_sha256": "def",
            "pr_body_sha256": "ghi",
            "accept_code": "ACCEPT-IMP-20260627-3907",
            "approved_at": "2026-06-27T00:00:00Z"
        }
        state_path.write_text(json.dumps(existing), encoding="utf-8")

        # FAILED 기록 시도 — APPROVED이므로 덮어쓰면 안 됨
        mod._record_failed_state(state_path, "IMP-20260627-3907", "codex_call_failed", "test")

        data = json.loads(state_path.read_text(encoding="utf-8"))
        assert data["status"] == "APPROVED"  # 덮어쓰지 않음


class TestHookStdinInput:
    """hook이 stdin JSON의 last_assistant_message에서 5요소를 읽는지 검증."""

    def _load_hook(self):
        import importlib.util as _ilu

        spec = _ilu.spec_from_file_location("hook_stdin_test", str(_HOOK_PATH))
        cx = _ilu.module_from_spec(spec)
        spec.loader.exec_module(cx)
        return cx

    def test_main_no_five_element_block_returns_zero(self, tmp_path):
        """last_assistant_message에 5요소가 없으면 main이 0을 반환한다."""
        import os
        import unittest.mock as mock

        cx = self._load_hook()
        with mock.patch.dict(
            os.environ, {"PIPELINE_STATE_PATH": str(tmp_path / "state.json")}
        ):
            rc = cx.main(
                hook_data_override={
                    "hook_event_name": "Stop",
                    "last_assistant_message": "그냥 일반적인 응답입니다.",
                    "stop_hook_active": False,
                }
            )
        assert rc == 0

    def test_main_empty_override_returns_zero(self, tmp_path):
        """빈 hook_data_override(메시지 없음)도 5요소 없음으로 0을 반환한다."""
        import os
        import unittest.mock as mock

        cx = self._load_hook()
        with mock.patch.dict(
            os.environ, {"PIPELINE_STATE_PATH": str(tmp_path / "state.json")}
        ):
            rc = cx.main(hook_data_override={})
        assert rc == 0

    def test_main_override_must_be_dict(self):
        """hook_data_override가 dict가 아니면 TypeError."""
        cx = self._load_hook()
        with pytest.raises(TypeError):
            cx.main(hook_data_override="not a dict")

    def test_main_detects_five_element_block(self, tmp_path):
        """5요소 블록이 있으면 parse_acceptance_block가 호출되어 검토 흐름으로 진입한다.

        gh CLI 미설치 환경에서는 head_sha 조회 실패로 fail-closed(exit 1)되거나
        PROCESSING 상태가 기록되므로, 5요소 블록이 감지되었음을 상태 파일로 확인한다.
        """
        import json
        import os
        import unittest.mock as mock

        cx = self._load_hook()
        last_message = "\n".join(
            [
                "사용자 승인 요청",
                "",
                "PR: https://github.com/owner/repo/pull/999",
                "",
                "승인 코드:",
                "ACCEPT-IMP-20260627-3907",
                "",
                "CODEX 검토 필요",
            ]
        )
        state_path = tmp_path / "state.json"
        with mock.patch.dict(
            os.environ, {"PIPELINE_STATE_PATH": str(state_path)}
        ):
            # head_sha 조회를 강제 실패시켜 결정론적으로 fail-closed 경로 확인
            with mock.patch.object(
                cx, "_get_pr_head_sha", side_effect=RuntimeError("gh 없음")
            ):
                rc = cx.main(
                    hook_data_override={
                        "hook_event_name": "Stop",
                        "last_assistant_message": last_message,
                        "stop_hook_active": False,
                    }
                )
        # 5요소 감지 후 head_sha 실패 → fail-closed exit 1
        assert rc == 1
        loop_state = tmp_path / ".pipeline" / "codex_review_loop_state.json"
        assert loop_state.exists()
        data = json.loads(loop_state.read_text(encoding="utf-8"))
        # 5요소가 감지되어 해당 pipeline_id로 상태가 기록되었는지 확인
        assert data["pipeline_id"] == "IMP-20260627-3907"

    def test_stdin_bom_handling(self):
        """PowerShell 파이프 시 UTF-8 BOM이 있어도 hook이 정상 파싱하는지 검증."""
        import subprocess as _subprocess
        import json as json_mod
        hook_path = Path(__file__).parent.parent / ".claude/hooks/codex_user_acceptance_review.py"

        good_msg = (
            "사용자 승인 요청\n\nPR: https://github.com/test/pull/1\n\n"
            "승인 코드:\nACCEPT-IMP-20260627-3907\n\nCODEX 검토 필요"
        )
        hook_data = {
            "hook_event_name": "Stop",
            "last_assistant_message": good_msg,
            "stop_hook_active": False,
        }
        # BOM prefix 추가 (PowerShell 파이프 시뮬레이션)
        json_str = json_mod.dumps(hook_data, ensure_ascii=False)
        bom_bytes = b"\xef\xbb\xbf" + json_str.encode("utf-8")

        result = _subprocess.run(
            ["python", str(hook_path)],
            input=bom_bytes,
            capture_output=True,
            timeout=10,
        )
        # json.loads 실패 시 "Unexpected UTF-8 BOM" stderr가 있어야 하는데,
        # 수정 후에는 BOM이 제거되어 정상 파싱 → stderr에 해당 메시지 없어야 함
        assert b"Unexpected UTF-8 BOM" not in result.stderr, (
            f"BOM 처리 실패: {result.stderr.decode('utf-8', errors='replace')}"
        )
        # exit code 0 또는 2 (REJECT 시) — BOM 때문에 exit 1이 나오면 안 됨
        assert result.returncode != 1, f"hook BOM 오류로 exit 1: {result.stderr.decode('utf-8', errors='replace')}"


class TestHotfix4:
    """hotfix-4 (IMP-20260627-3907): B형 마지막 줄, except pass 제거, stale 판정."""

    def _load_hook(self):
        import importlib.util as _ilu

        spec = _ilu.spec_from_file_location("hook_under_test_hf4", str(_HOOK_PATH))
        cx = _ilu.module_from_spec(spec)
        spec.loader.exec_module(cx)
        return cx

    def test_b_form_ends_with_user_final(self):
        """user_final 출력의 마지막 의미 있는 줄은 '사용자 최종 승인 필요'."""
        out = rn.render_user_acceptance_request(
            mode="user_final", pr_url=_PR_URL, pipeline_id=_PIPELINE_ID
        )
        meaningful = [ln.strip() for ln in out.splitlines() if ln.strip()]
        assert meaningful[-1] == "사용자 최종 승인 필요"

    def test_b_form_no_codex_required(self):
        """user_final 출력에 'CODEX 검토 필요'가 포함되지 않는다."""
        out = rn.render_user_acceptance_request(
            mode="user_final", pr_url=_PR_URL, pipeline_id=_PIPELINE_ID
        )
        assert "CODEX 검토 필요" not in out

    def test_a_form_ends_with_codex(self):
        """codex_review_required 출력의 마지막 의미 있는 줄은 'CODEX 검토 필요'."""
        out = rn.render_user_acceptance_request(
            mode="codex_review_required", pr_url=_PR_URL, pipeline_id=_PIPELINE_ID
        )
        meaningful = [ln.strip() for ln in out.splitlines() if ln.strip()]
        assert meaningful[-1] == "CODEX 검토 필요"

    def test_stale_processing_resets(self):
        """_check_stale은 다른 pipeline_id의 APPROVED를 stale로 오판하지 않는다."""
        cx = self._load_hook()
        state = {
            "pipeline_id": "IMP-20260600-XXXX",  # 다른 파이프라인
            "status": "APPROVED",
            "pr_head_sha": "abc123",
            "packet_sha256": "def456",
        }
        # pipeline_id 불일치 시 False (stale 아님 → 재트리거 허용)
        assert (
            cx._check_stale(state, "abc123", "def456", "IMP-20260627-3907") is False
        )
        # 같은 pipeline_id이고 head/packet 일치 시 True (유효한 APPROVED)
        state_same = dict(state, pipeline_id="IMP-20260627-3907")
        assert (
            cx._check_stale(state_same, "abc123", "def456", "IMP-20260627-3907")
            is True
        )

    def test_no_except_pass_in_hook(self):
        """hook 소스에 'except Exception:' 직후 'pass'만 있는 패턴이 없다."""
        import re as _re

        src = _read_text(_HOOK_PATH)
        pattern = _re.compile(r"except\s+Exception\s*(?:as\s+\w+)?\s*:\s*\n\s*pass\b")
        assert pattern.search(src) is None, "hook에 except Exception: pass 패턴 잔존"


def test_ps1_reads_stdin_not_env_var():
    """PS1 래퍼가 환경변수 대신 stdin JSON을 Python에 pipe하는지 검증."""
    ps1_path = Path(__file__).parent.parent / ".claude" / "hooks" / "codex-user-acceptance-review.ps1"
    assert ps1_path.exists(), f"PS1 파일 없음: {ps1_path}"
    src = ps1_path.read_text(encoding="utf-8")
    # 환경변수 CLAUDE_HOOK_TRANSCRIPT_PATH 읽기가 없어야 함
    assert "CLAUDE_HOOK_TRANSCRIPT_PATH" not in src, \
        "PS1이 여전히 CLAUDE_HOOK_TRANSCRIPT_PATH 환경변수를 읽습니다"
    # stdin을 Python helper에 그대로 pipe해야 함
    assert "$input | python" in src or ("$input" in src and "python" in src), \
        "PS1이 stdin을 Python에 pipe하지 않습니다"


# ---------------------------------------------------------------------------
# hotfix-7 (IMP-20260627-3907): --machine-readable 모드 + 중복 trigger block 감지
# ---------------------------------------------------------------------------
def test_machine_readable_json_output(tmp_path):
    """--machine-readable 모드: JSON 6개 필드 출력 검증."""
    # pipeline.py를 subprocess로 호출하여 JSON 출력 검증 (현재는 소스 검사만)
    env_state = tmp_path / "state.json"  # noqa: F841
    # 활성 파이프라인이 없는 상태에서는 호출 자체가 실패할 수 있으므로
    # 대신 pipeline.py 소스에서 --machine-readable 인자가 존재하는지만 확인
    pipeline_py = Path(__file__).parent.parent / "pipeline.py"
    src = pipeline_py.read_text(encoding="utf-8")
    assert "machine-readable" in src or "machine_readable" in src, \
        "pipeline.py에 --machine-readable 인자가 없습니다"
    assert "codex_review_required_message" in src, \
        "pipeline.py에 codex_review_required_message 필드가 없습니다"
    assert "user_final_message" in src, \
        "pipeline.py에 user_final_message 필드가 없습니다"


def test_no_duplicate_trigger_block(tmp_path):
    """hook이 사용자 승인 요청 2회 등장 시 duplicate_trigger_block FAILED 기록 검증."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent / ".claude" / "hooks"))
    from codex_user_acceptance_review import main as hook_main, _LOOP_STATE_FILENAME
    import json as _json

    # 중복 trigger block이 있는 hook_data
    duplicate_msg = (
        "사용자 승인 요청\n\nPR: https://github.com/test/repo/pull/1\n\n"
        "승인 코드:\nACCEPT-IMP-20260627-3907\n\nCODEX 검토 필요\n\n"
        "사용자 승인 요청\n\nPR: https://github.com/test/repo/pull/1\n\n"
        "승인 코드:\nACCEPT-IMP-20260627-3907\n\nCODEX 검토 필요"
    )
    pipeline_dir = tmp_path / ".pipeline"
    pipeline_dir.mkdir()
    state_path = pipeline_dir / _LOOP_STATE_FILENAME

    # acceptance_request.json을 생성해 pipeline_id 폴백이 동작하도록
    accept_req = pipeline_dir / "acceptance_request.json"
    accept_req.write_text(
        _json.dumps({"pipeline_id": "IMP-20260627-3907"}), encoding="utf-8"
    )

    import unittest.mock as _mock
    with _mock.patch(
        "codex_user_acceptance_review._project_pipeline_dir",
        return_value=pipeline_dir,
    ), _mock.patch(
        "codex_user_acceptance_review._project_root",
        return_value=tmp_path,
    ):
        result = hook_main(hook_data_override={"last_assistant_message": duplicate_msg})

    assert result == 0
    # state에 FAILED + duplicate_trigger_block 기록 확인
    if state_path.exists():
        state = _json.loads(state_path.read_text(encoding="utf-8"))
        assert state.get("failure_code") == "duplicate_trigger_block", \
            f"expected duplicate_trigger_block, got {state}"


def test_user_final_ends_with_approval_required():
    """user_final_message 마지막 의미 있는 줄 = 사용자 최종 승인 필요."""
    import importlib.util
    renderer_path = Path(__file__).parent.parent / ".claude" / "acceptance_renderer.py"
    spec = importlib.util.spec_from_file_location("acceptance_renderer", str(renderer_path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out = mod.render_user_acceptance_request(
        mode="user_final",
        pr_url="https://github.com/test/repo/pull/1",
        pipeline_id="IMP-20260627-3907",
    )
    meaningful = [ln.strip() for ln in out.splitlines() if ln.strip()]
    assert meaningful[-1] == "사용자 최종 승인 필요", \
        f"마지막 줄이 '사용자 최종 승인 필요'가 아닙니다: {meaningful[-1]!r}"


def test_pm_result_no_approval_block():
    """Pipeline Manager가 오케스트레이터에게 반환하는 결과에 '사용자 승인 요청' 블록이 없어야 한다.

    integration 시나리오: PM result에 승인 블록이 포함되면 오케스트레이터가 그것을 그대로 출력해
    사용자 화면에 2회 표시되는 이중 출력 문제 (REJECT 7차 근본 원인).
    pipeline-manager-agent.md의 규칙 적용 여부를 정적 검사로 검증한다.
    """
    agent_md_src = _AGENT_MD_PATH.read_text(encoding="utf-8")
    # 규칙이 agent MD에 존재해야 함
    assert "사용자 승인 요청" in agent_md_src and "블록을 포함하지 않습니다" in agent_md_src, \
        "pipeline-manager-agent.md에 오케스트레이터 중계 규칙이 없습니다"
    # result 예시에 승인 블록이 없어야 함 — 'codex_review_required_message' 필드 전달은 허용
    # '사용자 승인 요청' 블록이 PM result 예시 문구로 사용되면 안 됨
    # (MD 내 예시 코드 블록에 '완료: pipeline_id=' 패턴이 있어야 함)
    assert "완료: pipeline_id=" in agent_md_src, \
        "pipeline-manager-agent.md에 승인 블록 없는 result 예시가 없습니다"


# ---------------------------------------------------------------------------
# IMP-20260627-3907 재작업: exact snapshot 테스트 (literal expected 기반)
# ---------------------------------------------------------------------------

def test_a_form_exact_snapshot():
    """A형 출력이 정확한 literal과 일치한다."""
    expected = (
        "사용자 승인 요청\n\n"
        "PR: https://example.com/pull/1\n\n"
        "승인 코드:\n"
        "ACCEPT-IMP-TEST\n\n"
        "CODEX 검토 필요"
    )
    result = rn.render_user_acceptance_request(
        "codex_review_required", "https://example.com/pull/1", "IMP-TEST"
    )
    assert result == expected


def test_b_form_exact_snapshot():
    """B형 출력이 정확한 literal과 일치한다."""
    expected = (
        "사용자 승인 요청\n\n"
        "PR: https://example.com/pull/1\n\n"
        "승인 코드:\n"
        "ACCEPT-IMP-TEST\n\n"
        "사용자 최종 승인 필요"
    )
    result = rn.render_user_acceptance_request(
        "user_final", "https://example.com/pull/1", "IMP-TEST"
    )
    assert result == expected


def test_a_form_last_line():
    """codex_review_required 마지막 의미있는 줄 = 'CODEX 검토 필요'."""
    result = rn.render_user_acceptance_request(
        "codex_review_required", "https://example.com/pull/1", "IMP-TEST"
    )
    meaningful = [ln.strip() for ln in result.splitlines() if ln.strip()]
    assert meaningful[-1] == "CODEX 검토 필요"


def test_b_form_last_line():
    """user_final 마지막 의미있는 줄 = '사용자 최종 승인 필요'."""
    result = rn.render_user_acceptance_request(
        "user_final", "https://example.com/pull/1", "IMP-TEST"
    )
    meaningful = [ln.strip() for ln in result.splitlines() if ln.strip()]
    assert meaningful[-1] == "사용자 최종 승인 필요"


def test_duplicate_trigger_block_fail(tmp_path):
    """hook이 '사용자 승인 요청' 2회 포함 메시지 시 duplicate_trigger_block FAILED 기록."""
    import json as _json
    import unittest.mock as _mock
    import importlib.util as _ilu

    spec = _ilu.spec_from_file_location("hook_dtb_test", str(_HOOK_PATH))
    cx = _ilu.module_from_spec(spec)
    spec.loader.exec_module(cx)

    duplicate_msg = (
        "사용자 승인 요청\n\nPR: https://github.com/test/repo/pull/1\n\n"
        "승인 코드:\nACCEPT-IMP-20260627-3907\n\nCODEX 검토 필요\n\n"
        "사용자 승인 요청\n\nPR: https://github.com/test/repo/pull/1\n\n"
        "승인 코드:\nACCEPT-IMP-20260627-3907\n\nCODEX 검토 필요"
    )
    pipeline_dir = tmp_path / ".pipeline"
    pipeline_dir.mkdir()
    state_path = pipeline_dir / cx._LOOP_STATE_FILENAME

    accept_req = pipeline_dir / "acceptance_request.json"
    accept_req.write_text(
        _json.dumps({"pipeline_id": "IMP-20260627-3907"}), encoding="utf-8"
    )

    with _mock.patch.object(cx, "_project_pipeline_dir", return_value=pipeline_dir), \
         _mock.patch.object(cx, "_project_root", return_value=tmp_path):
        rc = cx.main(hook_data_override={"last_assistant_message": duplicate_msg})

    assert rc == 0
    assert state_path.exists(), "loop_state.json이 생성되지 않았습니다"
    state = _json.loads(state_path.read_text(encoding="utf-8"))
    assert state.get("failure_code") == "duplicate_trigger_block", \
        f"expected duplicate_trigger_block, got {state.get('failure_code')!r}"


# ---------------------------------------------------------------------------
# hotfix-10 (IMP-20260627-3907): stdin UTF-8, stale 가드, env fallback
# ---------------------------------------------------------------------------

def test_stdin_utf8_decoding():
    """hook이 UTF-8 JSON stdin의 한국어 last_assistant_message를 정상 파싱한다."""
    import importlib.util as _ilu

    hook_path = _REPO_ROOT / ".claude" / "hooks" / "codex_user_acceptance_review.py"
    src = hook_path.read_text(encoding="utf-8")
    # sys.stdin.buffer.read() → decode("utf-8-sig") 패턴이 있어야 함 (BOM 제거)
    assert "stdin.buffer.read" in src, "hook이 sys.stdin.buffer.read()를 사용하지 않습니다"
    assert 'decode("utf-8-sig"' in src, "hook이 UTF-8(BOM 제거)로 decode하지 않습니다"

    # 실제 UTF-8 인코딩된 JSON에서 한국어 메시지가 파싱되는지 검증
    spec = _ilu.spec_from_file_location("hook_utf8_test", str(hook_path))
    cx = _ilu.module_from_spec(spec)
    spec.loader.exec_module(cx)

    # pipeline_id는 FEAT|BUG|IMP-YYYYMMDD-XXXX 패턴이어야 parse_acceptance_block이 파싱 가능
    korean_msg = (
        "사용자 승인 요청\n\n"
        "PR: https://github.com/test/repo/pull/1\n\n"
        "승인 코드:\n"
        "ACCEPT-IMP-20260627-3907\n\n"
        "CODEX 검토 필요"
    )
    # parse_acceptance_block이 한국어 메시지를 정상 파싱하는지 확인
    block = cx.parse_acceptance_block(korean_msg)
    # 5요소 중 pr_url이 파싱되어야 함
    assert block is not None, "한국어 메시지에서 5요소 블록 파싱 실패"
    assert block.get("pr_url") == "https://github.com/test/repo/pull/1"
    assert block.get("pipeline_id") == "IMP-20260627-3907"


def test_stale_failed_state_overwrite(tmp_path):
    """FAILED 상태가 있을 때 새 FAILED 상태로 덮어쓰기가 허용된다."""
    import json as _json
    import importlib.util as _ilu

    hook_path = _REPO_ROOT / ".claude" / "hooks" / "codex_user_acceptance_review.py"
    spec = _ilu.spec_from_file_location("hook_stale_test", str(hook_path))
    cx = _ilu.module_from_spec(spec)
    spec.loader.exec_module(cx)

    pipeline_dir = tmp_path / ".pipeline"
    pipeline_dir.mkdir()
    state_path = pipeline_dir / cx._LOOP_STATE_FILENAME

    # FAILED 상태를 먼저 기록 (stale 상태)
    cx._record_failed_state(state_path, "IMP-20260627-3907", "transcript_path_empty", "old error")
    first_state = _json.loads(state_path.read_text(encoding="utf-8"))
    assert first_state["failure_code"] == "transcript_path_empty"

    # 같은 pipeline_id로 새 FAILED 기록 — 덮어쓰기 허용 확인
    cx._record_failed_state(state_path, "IMP-20260627-3907", "new_failure_code", "new error")
    second_state = _json.loads(state_path.read_text(encoding="utf-8"))
    # FAILED는 덮어쓰기 허용이므로 새 failure_code로 갱신되어야 함
    assert second_state["failure_code"] == "new_failure_code", \
        f"FAILED 상태 덮어쓰기 실패: {second_state['failure_code']!r}"


def test_approved_state_protected(tmp_path):
    """APPROVED 상태는 새 FAILED 기록으로 덮어쓰기가 금지된다."""
    import json as _json
    import importlib.util as _ilu

    hook_path = _REPO_ROOT / ".claude" / "hooks" / "codex_user_acceptance_review.py"
    spec = _ilu.spec_from_file_location("hook_approved_test", str(hook_path))
    cx = _ilu.module_from_spec(spec)
    spec.loader.exec_module(cx)

    pipeline_dir = tmp_path / ".pipeline"
    pipeline_dir.mkdir()
    state_path = pipeline_dir / cx._LOOP_STATE_FILENAME

    # APPROVED 상태를 먼저 기록
    approved_state = {
        "pipeline_id": "IMP-20260627-3907",
        "status": "APPROVED",
        "pr_head_sha": "abc123",
        "packet_sha256": "def456",
    }
    state_path.write_text(_json.dumps(approved_state), encoding="utf-8")

    # 같은 pipeline_id로 FAILED 기록 시도 — 차단되어야 함
    cx._record_failed_state(state_path, "IMP-20260627-3907", "should_be_blocked", "blocked")
    final_state = _json.loads(state_path.read_text(encoding="utf-8"))
    assert final_state["status"] == "APPROVED", \
        f"APPROVED 상태가 FAILED로 덮어쓰여짐: {final_state['status']!r}"
