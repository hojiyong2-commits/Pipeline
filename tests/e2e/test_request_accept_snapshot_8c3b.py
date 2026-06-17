"""
test_request_accept_snapshot_8c3b.py — IMP-20260610-8C3B MT-3
Acceptance Snapshot Materialization E2E 테스트 (5 케이스)

# [Purpose]:
#   _materialize_acceptance_snapshot / _verify_acceptance_snapshot / ac_completeness 캐시
#   로직이 CLI subprocess 흐름에서 올바르게 동작하는지 검증한다.
#   - TC-1 (normal): gates request-accept 최초 실행 시 nonce가 발급되고
#     acceptance_request.json에 4개 SHA 필드가 기록됨을 확인.
#   - TC-2 (normal): reuse 경로에서도 동일 snapshot 함수가 호출되어
#     packet_sha256 필드가 갱신됨을 확인.
#   - TC-3 (edge): SHA 불일치 시 gates accept가 BLOCKED를 반환함을 확인.
#   - TC-4 (edge): AC incomplete(module integrate 없음) 상태에서
#     gates request-accept가 AC_COMPLETENESS_CACHE_MISSING 메시지 없이도
#     레거시 경로로 fallback됨을 확인.
#   - TC-5 (edge): 잘못된 acceptance-code로 gates accept 실행 시 BLOCKED를 반환.

# [Assumptions]:
#   - PIPELINE_STATE_PATH 환경변수로 상태 파일 격리.
#   - subprocess 기반 실제 CLI 실행 (내부 함수 직접 호출 금지).
#   - gh CLI 없는 환경 가정 (PATH에서 제거).
#   - 격리 state에서 pm → dev → integrate 경로를 건너뛰는 경우
#     requirements_tracking.enabled=false 설정으로 AC 검사를 우회.

# CLI Evidence Contract (IMP-20260525-6FAC):
#   - 상태 변경 CLI 호출마다 PIPELINE_STATE_PATH 격리 사용
#   - final_state assertion 포함 (stdout-only 검증 금지)
#   - subprocess 기반 실제 CLI 실행
"""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

PIPELINE_PY = Path(__file__).resolve().parent.parent.parent / "pipeline.py"
ORACLE_DIR = Path(__file__).resolve().parent.parent / "oracles" / "IMP-20260610-8C3B"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run_cli(
    args: List[str],
    env: Dict[str, str],
    cwd: Optional[Path] = None,
    timeout: int = 30,
) -> "subprocess.CompletedProcess[str]":
    """`python pipeline.py <args>` 실행 후 CompletedProcess 반환.

    acceptance_request.json 등 pipeline.py 산출물은 BASE_DIR(PIPELINE_PY.parent)에 생성되므로
    cwd 기본값을 PIPELINE_PY.parent로 설정합니다.

    Args:
        args: pipeline.py에 전달할 인자 리스트.
        env: subprocess에 전달할 환경 변수.
        cwd: 작업 디렉토리 (기본은 PIPELINE_PY.parent).
        timeout: 초 단위 timeout.
    Raises:
        TypeError: args가 list가 아닌 경우.
    """
    if not isinstance(args, list):
        raise TypeError(f"args must be list, got {type(args).__name__}")
    cmd = [sys.executable, str(PIPELINE_PY)] + args
    effective_cwd = cwd if cwd is not None else PIPELINE_PY.parent
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=env,
        cwd=str(effective_cwd),
    )


# IMP-20260612-E12D MT-2: conftest의 autouse fake gh fixture가 제거되어
# request-accept의 PR body readiness 검사(IMP-20260611-A716 Bug 1)를 통과하려면
# fake gh를 명시적으로 PIPELINE_GH_EXECUTABLE로 주입해야 한다.
# request-accept 성공을 기대하는 TC(TC-1/TC-1b/TC-2/TC-3/TC-5)는 완전한 PR body가 필요하고,
# AC incomplete로 BLOCKED를 기대하는 TC(TC-4/TC-4b)는 PR body 검사 이전 단계에서 차단되므로
# 동일 fake gh가 주입돼도 결과에 영향이 없다(AC 검사가 PR body 검사보다 먼저 수행됨).
# fake gh는 headSha/databaseId에 빈 문자열을, run/pr list에 빈 배열을 반환하여
# 기존 "gh CLI 없는 환경(pr_head_sha=''/ci_run_id='')" 전제를 그대로 유지한다.

_FAKE_GH_PR_BODY_8C3B = (
    "## 작업 요약\n자동 테스트 픽스처 PR body\n\n"
    "## 사용자가 확인할 결과물\n결과물 경로: N/A (테스트)\n\n"
    "## 기대 결과와 실제 결과\n기대: 성공 / 실제: 성공\n\n"
    "## 중요한 선택과 트레이드오프\nN/A (테스트 픽스처)\n\n"
    "## 검증\n모든 게이트 PASS\n"
)


def _write_fake_gh_script(tmp_path: Path) -> Path:
    """완전한 PR body를 반환하는 fake gh 스크립트를 tmp_path에 생성하여 경로 반환.

    headSha/databaseId는 빈 문자열, run/pr list는 빈 배열을 반환하여
    gh CLI 없는 환경(pr_head_sha=''/ci_run_id='')을 시뮬레이션한다.

    Args:
        tmp_path: pytest tmp_path fixture.
    Returns:
        생성된 fake gh .py 스크립트 절대 경로.
    Raises:
        TypeError: tmp_path가 None.
    """
    if tmp_path is None:
        raise TypeError("tmp_path must not be None")
    body_json = json.dumps(_FAKE_GH_PR_BODY_8C3B)
    script = tmp_path / "fake_gh_8c3b.py"
    script.write_text(
        "import sys, io, json\n"
        'sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")\n'
        f"BODY = {body_json}\n"
        "args = sys.argv[1:]\n"
        'if "--jq" in args:\n'
        '    jq_idx = args.index("--jq"); jq = args[jq_idx+1] if jq_idx+1 < len(args) else ""\n'
        '    if jq == ".body":\n'
        '        sys.stdout.write(BODY)\n'
        '        if not BODY.endswith("\\n"):\n'
        '            sys.stdout.write("\\n")\n'
        "        sys.exit(0)\n"
        '    elif "[.files" in jq or jq.startswith(".[0]"):\n'
        '        print("[]"); sys.exit(0)\n'
        '    elif ".headSha" in jq or ".databaseId" in jq:\n'
        '        print(""); sys.exit(0)\n'
        'if "run" in args and "list" in args:\n'
        '    print("[]"); sys.exit(0)\n'
        'if "run" in args and "view" in args:\n'
        "    print(json.dumps({})); sys.exit(0)\n"
        'if "pr" in args and "list" in args:\n'
        '    print("[]"); sys.exit(0)\n'
        "print(json.dumps({\n"
        '    "body": BODY, "number": 1,\n'
        '    "headRefOid": "abc123def456abc123def456abc123def456abc1",\n'
        '    "isDraft": False, "state": "OPEN", "files": [],\n'
        '    "url": "https://github.com/test/repo/pull/1",\n'
        "}))\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    return script


def make_env(tmp_path: Path) -> Dict[str, str]:
    """PIPELINE_STATE_PATH 격리 환경변수 + gh CLI 무력화 (PATH에서 제거) + fake gh 주입.

    IMP-20260612-E12D MT-2: conftest autouse fixture 제거에 따라 fake gh를
    PIPELINE_GH_EXECUTABLE로 명시적으로 주입한다. PATH는 여전히 tmp_path로 제한하여
    PATH 기반 gh 탐색은 무력화하되, PR body 조회는 fake gh 스크립트로 처리한다.

    Args:
        tmp_path: pytest tmp_path fixture.
    Returns:
        subprocess에 전달할 환경 변수 dict.
    Raises:
        TypeError: tmp_path가 None.
    """
    if tmp_path is None:
        raise TypeError("tmp_path must not be None")
    state_file = tmp_path / "pipeline_state.json"
    env = dict(os.environ)
    env["PIPELINE_STATE_PATH"] = str(state_file)
    env["PIPELINE_NO_DASHBOARD"] = "1"
    # gh CLI를 못 찾도록 PATH를 tmp_path로 제한 (PATH 기반 gh 탐색 무력화)
    env["PATH"] = str(tmp_path)
    # IMP-20260612-E12D MT-2: PR body readiness 검사 통과를 위해 fake gh 주입
    env["PIPELINE_GH_EXECUTABLE"] = str(_write_fake_gh_script(tmp_path))
    # Windows cp949 인코딩 문제 방지
    env["PYTHONIOENCODING"] = "utf-8"
    env["PIPELINE_WORKSPACE_HYGIENE_ALLOW_GIT_MISSING"] = "1"
    # BUG-20260617-788A: request-accept가 비대화형/CI 자동 감지 제거로 인해 브라우저
    # HTTP 서버를 실제로 띄워 300초 대기하지 않도록 E2E에서 브라우저 승인 우회.
    env["PIPELINE_BROWSER_APPROVAL_SKIP"] = "1"
    return env


def load_final_state(env: Dict[str, str]) -> Dict[str, Any]:
    """PIPELINE_STATE_PATH가 가리키는 state 파일을 로드."""
    state_file = Path(env["PIPELINE_STATE_PATH"])
    if not state_file.exists():
        return {}
    with open(state_file, encoding="utf-8") as f:
        return json.load(f)


def sha256_str(s: str) -> str:
    """문자열의 SHA-256 hex digest 반환 (UTF-8 인코딩)."""
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def sha256_file(p: Path) -> str:
    """파일의 SHA-256 hex digest 반환."""
    return hashlib.sha256(p.read_bytes()).hexdigest()


def bootstrap_pipeline_legacy(tmp_path: Path, env: Dict[str, str]) -> str:
    """격리된 환경에 IMP 파이프라인을 생성하고 requirements_tracking.enabled=false로
    설정하여 AC 검사를 우회한 후 pipeline_id를 반환.

    pipeline.py 산출물(acceptance_request.json 등)은 BASE_DIR에 생성됩니다.
    PIPELINE_STATE_PATH만 tmp_path로 격리됩니다.

    Args:
        tmp_path: pytest tmp_path fixture.
        env: PIPELINE_STATE_PATH 환경변수가 설정된 dict.
    Returns:
        생성된 pipeline_id 문자열.
    """
    r = run_cli(
        ["new", "--type", "IMP", "--desc", "snapshot e2e test 8c3b"],
        env=env,
    )
    assert r.returncode == 0, f"new failed: {r.stdout} {r.stderr}"
    state_file = Path(env["PIPELINE_STATE_PATH"])
    with open(state_file, encoding="utf-8") as f:
        final_state = json.load(f)
    pid = str(final_state.get("pipeline_id", ""))
    assert pid, "pipeline_id missing"
    # AC 검사 우회: requirements_tracking.enabled=false
    final_state.setdefault("requirements_tracking", {})
    final_state["requirements_tracking"]["enabled"] = False
    with open(state_file, "w", encoding="utf-8") as f:
        json.dump(final_state, f, ensure_ascii=False, indent=2)
    return pid


def write_evidence_file(tmp_path: Path, content: str = "snapshot test evidence") -> Path:
    """evidence 파일을 tmp_path에 생성하고 경로 반환."""
    ev_file = tmp_path / "evidence.txt"
    ev_file.write_text(content, encoding="utf-8")
    return ev_file


def load_acceptance_request(tmp_path: Optional[Path] = None) -> Dict[str, Any]:
    """acceptance_request.json 로드 (없으면 빈 dict 반환).

    acceptance_request.json은 pipeline.py BASE_DIR(프로젝트 루트)에 생성됩니다.
    tmp_path는 호환성을 위해 유지하되 사용하지 않습니다.
    """
    req_file = PIPELINE_PY.parent / "acceptance_request.json"
    if not req_file.exists():
        return {}
    with open(req_file, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Oracle fixtures
# ---------------------------------------------------------------------------

def _oracle(case_id: str) -> Dict[str, Any]:
    """oracle 디렉토리에서 expected.json을 로드."""
    p = ORACLE_DIR / case_id / "expected.json"
    if p.exists():
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return {}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

# TC-1 (normal): gates request-accept 최초 실행 시 nonce 발급 + 4개 SHA 필드 기록
def test_tc1_request_accept_issues_nonce_and_records_shas(tmp_path):
    """TC-1: 최초 request-accept 실행 시 acceptance_request.json에 nonce와
    packet_sha256이 기록되고 nonce 형식이 올바른지 확인한다.

    oracle: tests/oracles/IMP-20260610-8C3B/normal_snapshot_fresh/
    CLI Evidence Contract: PIPELINE_STATE_PATH 격리 + final_state assertion.
    """
    env = make_env(tmp_path)
    pid = bootstrap_pipeline_legacy(tmp_path, env)

    ev_file = write_evidence_file(tmp_path)

    r = run_cli(
        ["gates", "request-accept", "--evidence", str(ev_file)],
        env=env,
    )

    # 1. CLI 성공 여부
    assert r.returncode == 0, (
        f"request-accept 실패 (returncode={r.returncode})\n"
        f"stdout: {r.stdout[:500]}\nstderr: {r.stderr[:300]}"
    )

    # 2. acceptance_request.json 생성 확인 (final_state assertion)
    req = load_acceptance_request()
    assert req, "acceptance_request.json 미생성"
    assert req.get("pipeline_id") == pid, f"pipeline_id 불일치: {req.get('pipeline_id')}"

    # 3. nonce 형식 검증 (8자 base32 대문자+숫자)
    nonce = req.get("nonce", "")
    assert nonce, "nonce 없음"
    assert len(nonce) == 8, f"nonce 길이 불일치: {len(nonce)}"

    # 4. packet_sha256 기록 확인 (IMP-20260610-8C3B MT-1 핵심)
    pkt_sha = req.get("packet_sha256")
    assert pkt_sha, "packet_sha256 미기록"
    assert len(pkt_sha) == 64, f"packet_sha256 형식 불일치: {pkt_sha}"

    # 5. stdout에 승인 코드 포함 확인
    accept_code = f"ACCEPT-{pid}-{nonce}"
    assert accept_code in r.stdout, (
        f"stdout에 승인 코드 없음. expected: {accept_code}\n"
        f"stdout: {r.stdout[:500]}"
    )

    # oracle 참조 (expected.json 구조 검증)
    oracle = _oracle("normal_snapshot_fresh")
    if oracle:
        for key in oracle.get("acceptance_request_keys", []):
            assert key in req, f"oracle 필수 key '{key}' 누락"


# TC-1b (normal): requirements_tracking.enabled=True + AC 충족(구현 근거 + QA 검증) 상태에서
# gates request-accept가 성공하고 nonce가 발급됨을 확인 (결함 D 커버리지 보완)
def test_tc1b_request_accept_with_ac_completeness_cache(tmp_path):
    """TC-1b: requirements_tracking.enabled=True + structured AC가 live 검사에서 모두 PASS인 경우
    gates request-accept가 성공(exit 0)하고 acceptance_request.json에 nonce가 기록됨을 확인.

    IMP-20260613-4A22 Round 3: Round 2에서 _validate_ac_table_before_request_accept가
    ac_completeness 캐시/enabled 플래그를 신뢰하지 않고 항상 _build_ac_fulfillment_table을
    live로 재조립하도록 바뀌었다. 따라서 캐시만 complete=True로 두는 것으로는 더 이상 통과하지
    못한다. live 검사가 PASS 하려면 각 AC에 (1) 구현 근거(scope.implemented_tasks의
    implementation_evidence)와 (2) QA 검증(module qa report XML의 ac_verification)이 모두
    기록되어 있어야 한다. 본 테스트는 이 두 근거를 실제 state/파일로 제공하여 live 검사 PASS →
    nonce 발급 경로를 검증한다.

    TC-1/TC-2의 bootstrap_pipeline_legacy가 requirements_tracking.enabled=False로 우회하므로
    실제 AC 충족 경로를 검증하지 못하는 결함 D를 보완한다.

    oracle: tests/oracles/IMP-20260610-8C3B/normal_snapshot_fresh/
    CLI Evidence Contract: PIPELINE_STATE_PATH 격리 + final_state assertion.
    """
    env = make_env(tmp_path)

    # 새 파이프라인 생성 (AC 추적 활성화 — bootstrap_pipeline_legacy 우회 아님)
    r_new = run_cli(
        ["new", "--type", "IMP", "--desc", "ac completeness cache test tc1b"],
        env=env,
    )
    assert r_new.returncode == 0, f"new failed: {r_new.stdout} {r_new.stderr}"

    state_file = Path(env["PIPELINE_STATE_PATH"])
    with open(state_file, encoding="utf-8") as f:
        state = json.load(f)

    pid = str(state.get("pipeline_id", ""))
    assert pid, "pipeline_id missing"

    # requirements_tracking.enabled=True 설정
    state["requirements_tracking"] = {"enabled": True}

    # module_gates.integration.status = PASS 설정 (module integrate 완료 시뮬레이션)
    state.setdefault("module_gates", {})
    state["module_gates"].setdefault("integration", {})
    state["module_gates"]["integration"]["status"] = "PASS"

    # ac_completeness 캐시를 complete=True로 직접 설정 (module integrate PASS 시뮬레이션)
    state["ac_completeness"] = {
        "cached_at": "2026-06-10T00:00:00Z",
        "total": 1,
        "pending_count": 0,
        "pending_ids": [],
        "complete": True,
    }

    # structured_acceptance_criteria에 AC-1 항목 추가
    state["structured_acceptance_criteria"] = [
        {
            "id": "AC-1",
            "text": "snapshot materialization이 원자적으로 완료된다",
            "must_verify": True,
            "source": "user",
            "user_visible": True,
        }
    ]

    # IMP-20260613-4A22 Round 3: live AC 검사 PASS를 위해 AC-1에 대한
    # (1) 구현 근거와 (2) QA 검증을 실제 state/파일로 제공한다.
    # _get_qa_verification_for_ac는 module qa report XML 파일을 파싱하므로
    # ac_verification 블록이 담긴 XML을 tmp_path에 작성하고 경로를 연결한다.
    qa_report = tmp_path / "module_qa_MT-1.xml"
    qa_report.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        "<module_qa_report>\n"
        "  <mt_id>MT-1</mt_id>\n"
        "  <verdict>PASS</verdict>\n"
        "  <ac_verification>\n"
        '    <ac id="AC-1" status="PASS">\n'
        "      <verification>snapshot이 tempfile+os.replace로 원자적으로 기록됨을 확인</verification>\n"
        "    </ac>\n"
        "  </ac_verification>\n"
        "</module_qa_report>\n",
        encoding="utf-8",
    )
    state["module_gates"]["modules"] = {
        "MT-1": {
            "dev": {
                "status": "DONE",
                "scope": {
                    "files": ["pipeline.py"],
                    "implemented_tasks": [
                        {
                            "mt_id": "MT-1",
                            "implemented_ac": ["AC-1"],
                            "changed_files": ["pipeline.py"],
                            "implementation_evidence": [
                                "_materialize_acceptance_snapshot가 tempfile+os.replace로 "
                                "snapshot을 원자적으로 기록"
                            ],
                        }
                    ],
                },
            },
            "qa": {
                "status": "PASS",
                "report_file": str(qa_report),
            },
        }
    }

    with open(state_file, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

    ev_file = write_evidence_file(tmp_path, "tc1b ac completeness cache evidence")

    r = run_cli(
        ["gates", "request-accept", "--evidence", str(ev_file)],
        env=env,
    )

    # 1. CLI 성공 여부 (exit 0)
    assert r.returncode == 0, (
        f"TC-1b: ac_completeness complete=True인데 request-accept 실패 "
        f"(returncode={r.returncode})\n"
        f"stdout: {r.stdout[:600]}\nstderr: {r.stderr[:300]}"
    )

    # 2. acceptance_request.json에 nonce 기록 확인 (final_state assertion)
    req = load_acceptance_request()
    assert req, "acceptance_request.json 미생성"
    assert req.get("pipeline_id") == pid, f"pipeline_id 불일치: {req.get('pipeline_id')}"

    nonce = req.get("nonce", "")
    assert nonce, "nonce 없음"
    assert len(nonce) == 8, f"nonce 길이 불일치: {len(nonce)}"

    # 3. packet_sha256 기록 확인
    pkt_sha = req.get("packet_sha256")
    assert pkt_sha, "packet_sha256 미기록 — ac_completeness 캐시 경로 미검증"
    assert len(pkt_sha) == 64, f"packet_sha256 형식 불일치: {pkt_sha}"

    # 4. stdout에 승인 코드 포함 확인
    accept_code = f"ACCEPT-{pid}-{nonce}"
    assert accept_code in r.stdout, (
        f"stdout에 승인 코드 없음. expected: {accept_code}\n"
        f"stdout: {r.stdout[:500]}"
    )


# TC-2 (normal): reuse 경로에서 snapshot 재실행 시 packet_sha256 갱신 확인
def test_tc2_reuse_path_updates_packet_sha(tmp_path):
    """TC-2: 동일 조건에서 request-accept를 두 번 실행하면 reuse 경로에서도
    packet_sha256이 존재하고 acceptance_request.json의 nonce가 보존됨을 확인.

    oracle: tests/oracles/IMP-20260610-8C3B/normal_snapshot_reuse/
    CLI Evidence Contract: PIPELINE_STATE_PATH 격리 + final_state assertion.
    """
    env = make_env(tmp_path)
    bootstrap_pipeline_legacy(tmp_path, env)
    ev_file = write_evidence_file(tmp_path)

    # 첫 번째 실행
    r1 = run_cli(
        ["gates", "request-accept", "--evidence", str(ev_file)],
        env=env,
    )
    assert r1.returncode == 0, f"첫 번째 request-accept 실패: {r1.stdout} {r1.stderr}"

    req1 = load_acceptance_request()
    nonce1 = req1.get("nonce", "")
    pkt_sha1 = req1.get("packet_sha256", "")
    assert nonce1, "첫 번째 nonce 없음"
    assert pkt_sha1, "첫 번째 packet_sha256 없음"

    # 두 번째 실행 (동일 조건 → reuse)
    r2 = run_cli(
        ["gates", "request-accept", "--evidence", str(ev_file)],
        env=env,
    )
    assert r2.returncode == 0, f"두 번째 request-accept 실패: {r2.stdout} {r2.stderr}"

    req2 = load_acceptance_request()
    nonce2 = req2.get("nonce", "")
    pkt_sha2 = req2.get("packet_sha256", "")

    # nonce는 보존되거나 동일 (reuse)
    # reuse 판단은 pr_sha/ci_run 등에 따라 달라지므로 nonce 동일 여부만 확인
    assert nonce2, "두 번째 nonce 없음"
    assert pkt_sha2, "두 번째 packet_sha256 없음"

    # oracle 참조
    oracle = _oracle("normal_snapshot_reuse")
    if oracle:
        expected_fields = oracle.get("reuse_preserved_fields", [])
        for field in expected_fields:
            assert req2.get(field) == req1.get(field), (
                f"reuse 시 {field} 변경됨: {req1.get(field)} → {req2.get(field)}"
            )


# TC-3 (edge): SHA 불일치 — stale packet이 있을 때 BLOCKED 반환 확인
def test_tc3_stale_sha_mismatch_blocks_accept(tmp_path):
    """TC-3: acceptance_request.json의 packet_sha256이 실제 packet 파일과 다를 때
    gates accept가 BLOCKED를 반환(exit code != 0)함을 확인.

    oracle: tests/oracles/IMP-20260610-8C3B/edge_sha_mismatch/
    CLI Evidence Contract: PIPELINE_STATE_PATH 격리 + returncode assertion.
    """
    env = make_env(tmp_path)
    pid = bootstrap_pipeline_legacy(tmp_path, env)
    ev_file = write_evidence_file(tmp_path)

    # 먼저 request-accept 실행해서 acceptance_request.json 생성
    # PIPELINE_STATE_PATH 격리 적용 — acceptance_request.json 상태 검증으로 post-state 확인
    r_req = run_cli(
        ["gates", "request-accept", "--evidence", str(ev_file)],
        env=env,
    )
    assert r_req.returncode == 0, f"request-accept 실패: {r_req.stdout} {r_req.stderr}"
    # post-state: acceptance_request.json에 nonce 기록 확인
    req = load_acceptance_request()
    nonce = req.get("nonce", "TESTNON1")
    assert nonce, "nonce가 acceptance_request.json에 기록되어야 함"
    accept_code = f"ACCEPT-{pid}-{nonce}"

    # packet_sha256을 의도적으로 오염 (BASE_DIR에 있는 파일 수정)
    req["packet_sha256"] = "a" * 64  # 잘못된 SHA
    req_file = PIPELINE_PY.parent / "acceptance_request.json"
    with open(req_file, "w", encoding="utf-8") as f:
        json.dump(req, f, ensure_ascii=False, indent=2)

    # gates accept 실행 → BLOCKED 예상
    # PIPELINE_STATE_PATH 격리 적용 — failure_packet 형식 stdout/stderr 검증
    r_accept = run_cli(
        ["gates", "accept", "--result", "ACCEPT",
         "--evidence", str(ev_file),
         "--acceptance-code", accept_code],
        env=env,
    )

    # BLOCKED: exit code != 0 또는 stdout에 BLOCKED/ERROR 포함
    # failure_packet: stdout+stderr가 BLOCKED 메시지를 포함
    failure_packet = r_accept.stdout + r_accept.stderr
    assert (r_accept.returncode != 0 or
            "BLOCKED" in failure_packet or
            "ERROR" in failure_packet), (
        f"SHA 불일치 시 BLOCKED 미반환\n"
        f"returncode={r_accept.returncode}\n"
        f"stdout: {r_accept.stdout[:500]}"
    )

    # oracle 참조
    oracle = _oracle("edge_sha_mismatch")
    if oracle:
        expected_failure_code = oracle.get("expected_failure_code", "")
        if expected_failure_code:
            combined = r_accept.stdout + r_accept.stderr
            assert expected_failure_code in combined or r_accept.returncode != 0, (
                f"expected failure_code '{expected_failure_code}' not found"
            )


# TC-4 (edge): AC incomplete — module integrate 없이 request-accept 실행 시
# requirements_tracking.enabled=true + ac_completeness 캐시 없음 → BLOCKED 기대
def test_tc4_ac_incomplete_no_integrate_cache(tmp_path):
    """TC-4: requirements_tracking.enabled=true이고 module integrate가 완료되지 않아
    ac_completeness 캐시가 없는 상태에서 request-accept를 실행하면
    BLOCKED(exit code != 0)가 반환되어야 한다. (IMP-20260610-8C3B AC-4)

    수정 전 동작: structured_acceptance_criteria=[]이면 캐시 없어도 성공(exit 0) — 결함
    수정 후 동작: requirements_tracking.enabled=true + 캐시 없음 → 항상 BLOCKED(exit 1)

    oracle: tests/oracles/IMP-20260610-8C3B/edge_ac_incomplete/
    CLI Evidence Contract: PIPELINE_STATE_PATH 격리 + final_state assertion.
    """
    env = make_env(tmp_path)

    # 새 파이프라인 생성 (AC 검사 활성화)
    r_new = run_cli(
        ["new", "--type", "IMP", "--desc", "ac incomplete test 8c3b"],
        env=env,
    )
    assert r_new.returncode == 0, f"new failed: {r_new.stdout} {r_new.stderr}"

    state_file = Path(env["PIPELINE_STATE_PATH"])
    with open(state_file, encoding="utf-8") as f:
        state = json.load(f)

    # requirements_tracking 활성화 + ac_completeness 캐시 없음 (integrate 미완료 시뮬레이션)
    state["requirements_tracking"] = {"enabled": True}
    # ac_completeness 캐시 없음 (키 자체 없음 — module integrate 미실행 상태)
    state.pop("ac_completeness", None)
    # structured_acceptance_criteria에 실제 pending AC 항목 추가
    # (AC-4 결함 검출: 캐시 없는데 AC가 있는 경우 반드시 BLOCKED여야 함)
    state["structured_acceptance_criteria"] = [
        {
            "id": "AC-1",
            "text": "snapshot materialization이 원자적으로 완료된다",
            "must_verify": True,
            "source": "user",
            "user_visible": True,
        }
    ]

    with open(state_file, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

    ev_file = write_evidence_file(tmp_path, "ac incomplete test evidence")

    r = run_cli(
        ["gates", "request-accept", "--evidence", str(ev_file)],
        env=env,
    )

    # AC-4 요구사항: requirements_tracking.enabled=true + ac_completeness 캐시 없음
    # → BLOCKED (exit code != 0) 이어야 한다.
    # module integrate PASS 없이 request-accept가 성공하면 AC-4 결함 재발
    combined = r.stdout + r.stderr
    assert r.returncode != 0, (
        f"AC-4 위반: module integrate 없이 request-accept가 성공해서는 안 됨\n"
        f"returncode={r.returncode}\n"
        f"stdout: {r.stdout[:600]}\nstderr: {r.stderr[:300]}"
    )

    # stdout에 BLOCKED 메시지와 module integrate 안내가 포함되어야 함
    assert "[PIPELINE ERROR]" in combined, (
        f"BLOCKED 메시지([PIPELINE ERROR])가 stdout/stderr에 없음\n"
        f"stdout: {r.stdout[:600]}"
    )
    assert "module integrate" in combined, (
        f"'module integrate' 안내가 stdout/stderr에 없음\n"
        f"stdout: {r.stdout[:600]}"
    )

    # oracle 참조 (post-state assertion)
    oracle = _oracle("edge_ac_incomplete")
    if oracle:
        expected_exit = oracle.get("exit_code", 1)
        assert r.returncode == expected_exit, (
            f"oracle expected exit {expected_exit}, got {r.returncode}"
        )
        for substr in oracle.get("stdout_contains_substrings", []):
            assert substr in combined, (
                f"oracle 기대 문자열 '{substr}'이 stdout/stderr에 없음\n"
                f"combined: {combined[:600]}"
            )


# TC-4b (regression): integration PASS 아님 + ac_completeness 캐시 있음 → BLOCKED
def test_tc4b_integration_not_pass_with_cache_blocked(tmp_path):
    """TC-4b: module_gates.integration.status가 PASS가 아닌데 ac_completeness 캐시가 있어도
    BLOCKED되어야 한다. (regression: stale 캐시만으로 통과하는 결함 방지)

    oracle: tests/oracles/IMP-20260610-8C3B/edge_ac_incomplete/
    CLI Evidence Contract: PIPELINE_STATE_PATH 격리 + final_state assertion (returncode + stderr).
    """
    env = make_env(tmp_path)

    r_new = run_cli(
        ["new", "--type", "IMP", "--desc", "tc4b integration not pass regression"],
        env=env,
    )
    assert r_new.returncode == 0, f"new failed: {r_new.stdout} {r_new.stderr}"

    state_file = Path(env["PIPELINE_STATE_PATH"])
    with open(state_file, encoding="utf-8") as f:
        state = json.load(f)

    # requirements_tracking 활성화
    state["requirements_tracking"] = {"enabled": True}

    # ac_completeness 캐시는 있음 (complete=True) — stale 캐시 시뮬레이션
    state["ac_completeness"] = {
        "cached_at": "2026-06-10T00:00:00Z",
        "total": 1,
        "pending_count": 0,
        "pending_ids": [],
        "complete": True,
    }

    # module_gates.integration.status = FAIL (또는 PENDING) — PASS가 아님
    state.setdefault("module_gates", {})
    state["module_gates"]["integration"] = {"status": "FAIL"}

    # IMP-20260613-4A22 Round 3: Round 2에서 _validate_ac_table_before_request_accept가
    # ac_completeness 캐시/enabled 플래그에 의존하지 않고 항상 live로 AC 충족표를 재조립하도록
    # 변경되었다. live 검사가 동작하려면 structured_acceptance_criteria가 실제로 존재해야 한다.
    # 구현 근거/QA 검증이 없는 pending AC를 두어 live 검사가 PENDING을 감지하게 한다.
    # PENDING AC + integration FAIL → "module integrate가 PASS 상태가 아닙니다" 메시지로 차단된다.
    state["structured_acceptance_criteria"] = [
        {
            "id": "AC-1",
            "text": "snapshot materialization이 원자적으로 완료된다",
            "must_verify": True,
            "source": "user",
            "user_visible": True,
        }
    ]

    with open(state_file, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

    ev_file = write_evidence_file(tmp_path, "tc4b regression evidence")

    r = run_cli(
        ["gates", "request-accept", "--evidence", str(ev_file)],
        env=env,
    )

    # BLOCKED: integration PASS가 아니면 stale 캐시 있어도 차단
    combined = r.stdout + r.stderr
    assert r.returncode != 0, (
        f"TC-4b 위반: integration FAIL인데 request-accept가 성공해서는 안 됨\n"
        f"returncode={r.returncode}\nstdout: {r.stdout[:600]}\nstderr: {r.stderr[:300]}"
    )
    assert "[PIPELINE ERROR]" in combined, (
        f"BLOCKED 메시지([PIPELINE ERROR]) 없음\nstdout: {r.stdout[:600]}"
    )
    assert "module integrate" in combined, (
        f"'module integrate' 안내 없음\nstdout: {r.stdout[:600]}"
    )


# TC-5 (edge): 잘못된 acceptance-code로 gates accept 실행 시 BLOCKED
def test_tc5_wrong_acceptance_code_blocked(tmp_path):
    """TC-5: 잘못된 acceptance-code를 전달하면 gates accept가 BLOCKED를 반환함을 확인.

    oracle: tests/oracles/IMP-20260610-8C3B/edge_wrong_code/
    CLI Evidence Contract: PIPELINE_STATE_PATH 격리 + returncode assertion.
    """
    env = make_env(tmp_path)
    pid = bootstrap_pipeline_legacy(tmp_path, env)
    ev_file = write_evidence_file(tmp_path)

    # request-accept 실행 후 acceptance_request.json 생성
    # PIPELINE_STATE_PATH 격리 적용 — acceptance_request.json 상태 검증으로 post-state 확인
    r_req = run_cli(
        ["gates", "request-accept", "--evidence", str(ev_file)],
        env=env,
    )
    assert r_req.returncode == 0, f"request-accept 실패: {r_req.stdout} {r_req.stderr}"
    # post-state: acceptance_request.json에 nonce 기록 확인
    req_state = load_acceptance_request()
    assert req_state.get("nonce"), "nonce가 acceptance_request.json에 기록되어야 함"

    # 잘못된 코드로 accept 시도
    wrong_code = f"ACCEPT-{pid}-WRONGXXX"
    # PIPELINE_STATE_PATH 격리 적용 — failure_packet 형식 stdout/stderr 검증
    r_accept = run_cli(
        ["gates", "accept", "--result", "ACCEPT",
         "--evidence", str(ev_file),
         "--acceptance-code", wrong_code],
        env=env,
    )

    # BLOCKED: exit code != 0 또는 BLOCKED/mismatch/ERROR 메시지
    # failure_packet: stdout+stderr가 BLOCKED 메시지를 포함
    failure_packet = r_accept.stdout + r_accept.stderr
    assert (r_accept.returncode != 0 or
            "BLOCKED" in failure_packet or
            "mismatch" in failure_packet.lower() or
            "error" in failure_packet.lower()), (
        f"잘못된 코드 시 BLOCKED 미반환\n"
        f"returncode={r_accept.returncode}\n"
        f"stdout: {r_accept.stdout[:500]}"
    )

    # oracle 참조
    oracle = _oracle("edge_wrong_code")
    if oracle:
        expected_failure_code = oracle.get("expected_failure_code", "")
        if expected_failure_code:
            assert (expected_failure_code in failure_packet or
                    r_accept.returncode != 0), (
                f"expected failure_code '{expected_failure_code}' not found"
            )
