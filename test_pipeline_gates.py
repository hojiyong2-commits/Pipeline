"""
test_pipeline_gates.py — pipeline.py 게이트 네거티브 테스트 50종
BUG-20260507-C2E2 MT-3 / BUG-20260508-6198 MT-2 / BUG-20260508-D541 MT-6 / BUG-20260508-A53A MT-5
BUG-20260508-F7A8 MT-3 / BUG-20260509-FDBC MT-3 / BUG-20260509-05AD MT-4
BUG-20260509-7C32 MT-3 / BUG-20260509-7D6E MT-3 / BUG-20260509-5270 MT-2
BUG-20260509-4D25 MT-2 / BUG-20260509-FA5E (BUG-20260509-B208) MT-2
BUG-20260509-894D MT-2

실행: python test_pipeline_gates.py
모두 통과 시 exit 0, 실패 시 exit 1

테스트 항목:
  Test 1: XML comment만 있는 build report는 _verify_build_report_xml에서 거부됨
  Test 2: current_phase='Phase 8 - Architect (RCA)'일 때 check_gate("dev")는 BLOCKED
  Test 3: qa --result FAIL --numeric-score 60 without --failure-sig → exit 1
  Test 4: harness FAIL 후 check_gate("dev")는 BLOCKED (architect 먼저 필요)
  Test 5: harness without --user-confirmed → user-confirmed 때문에 차단되지 않음; evidence gate가 판단
  Test 6: harness FAIL without --test-output-file → exit 1 + HARNESS GATE BLOCKED 메시지

  BUG-20260508-D541 추가 (XML comment bypass 차단):
  Test 7: comment-only <harness_report> → harness FAIL 기록 거부 (exit 1)
  Test 8: 속성 있는 <harness_report verdict="FAIL"> → harness FAIL 기록 정상 허용 (comment-safe regex)
  Test 9: comment-only <test_code> → harness PASS 거부 (exit 1, _extract_test_code None 반환)
  Test 10: <test_code>만 있고 <harness_report> 없음 → harness PASS 거부 (exit 1)

  BUG-20260508-A53A 추가 (ElementTree 파싱 업그레이드 검증):
  Test 11: malformed/unclosed <harness_report> → _parse_harness_report_et() None 반환 (gate blocked)
  Test 12: <test_code>가 <harness_report> 밖에 있음 → _extract_test_code() None 반환 (gate blocked)

  BUG-20260508-F7A8 추가 (CDATA 권장 규칙 검증 — XML escape도 허용):
  Test 13: <test_code>에 raw '<' 포함 (CDATA 없음) → _parse_harness_report_et() None 반환 (ET.ParseError)
  Test 14: <test_code> 내용이 <harness_report> 바깥에 있는 동시에 raw '<' 포함 → _extract_test_code() None 반환
  Test 15: CDATA 없이 raw '<' 직접 포함 → ET 파싱 실패 확인 (xml.etree.ElementTree.ParseError 발생)
  Test 16: CDATA로 감싼 test_code → _extract_test_code() 코드 내용 정상 반환 (raw '<' 포함 코드도 허용)

  BUG-20260509-4D25 추가 (unittest runner 모델 — Tests 17-29 교체):
  Test 17: test_code에 unittest.TestCase 없음 → validate_test_evidence() False (testsRun=0)
  Test 18: unittest.TestCase 있지만 test_* 메서드 0개 → validate_test_evidence() False (testsRun=0)
  Test 19: 유효한 unittest + 통과하는 test_* → validate_test_evidence() True (양성 케이스)
  Test 20: unittest assertion 실패 (assertEqual(1,2)) → validate_test_evidence() False (returncode!=0)
  Test 21: test_code에 sys.exit(1) → validate_test_evidence() False (returncode!=0)
  Test 22: validate_test_evidence() 단일 호출 → subprocess 정확히 1회 실행 (double-exec 방지)
  Test 23: dead code (if False: pass) + TestCase 없음 → validate_test_evidence() False (testsRun=0)
  Test 24: print("ASSERTION PASSED") only + TestCase 없음 → validate_test_evidence() False (testsRun=0)
  Test 25: __file__ 기반 nonce 추출 시도 (구 bypass) + TestCase 없음 → False (testsRun=0)
  Test 26: print("ASSERTION PASSED") only + TestCase 없음 → validate_test_evidence() False
  Test 27: print("__ASSERT_COUNTER__") 하드코딩 + TestCase 없음 → validate_test_evidence() False

  BUG-20260509-FA5E (BUG-20260509-B208) 추가 (JSON runner + assertionCount 모델):
  Test 30: noop 테스트 (assertion 없이 pass만) → validate_test_evidence() False (assertionCount=0)
  Test 31: @unittest.skip 데코레이터 → validate_test_evidence() False (skipped=1)
  Test 32: @unittest.expectedFailure (실패하는 테스트) → validate_test_evidence() False (expectedFailures=1)
  Test 33: 유효한 self.assertEqual(1+1, 2) → validate_test_evidence() True (회귀 검증)

  BUG-20260509-894D 추가 (runner-owned JSON 채널 + executed_assertions 런타임 카운터):
  Test 34-39: sidecar/atexit/import __main__ 차단 (기존 Tests 34-39 유지)
  Test 40: dead code assert (if False: self.assertEqual(1,1)) → False (executed_assertions=0)
  Test 41: monkeypatch — unittest.TestCase.assertEqual 재할당 → False (AST hard-reject)
  Test 42: unittest.main() call inside test_* method body → False (AST hard-reject)
  Test 43: fake stderr injection (sys.stderr.write + no TestCase) → False (ast_count=0 or executed_assertions=0)
  Test 44: unreachable assert after return → False (AST hard-reject)
  Test 45: __main__._exec_assert_count spoof + dead assert → False (AST hard-reject)
  Test 46: atexit + __main__._result_path overwrite → False (AST hard-reject)
  Test 47: inspect.stack() result path overwrite → False (AST hard-reject)
  Test 48: sys.argv runner path probe → False (AST hard-reject)
  Test 49: sys.modules __main__ probe → False (AST hard-reject)
  Test 50: dynamic getattr runner/reflection probe → False (AST hard-reject)
"""
import subprocess
import sys

PASS_COUNT = 0
FAIL_COUNT = 0


def assert_result(condition: bool, test_name: str, detail: str = "") -> None:
    global PASS_COUNT, FAIL_COUNT
    if condition:
        print(f"[PASS] {test_name}")
        PASS_COUNT += 1
    else:
        print(f"[FAIL] {test_name}" + (f": {detail}" if detail else ""))
        FAIL_COUNT += 1


def run_cmd(cmd_args: list) -> subprocess.CompletedProcess:
    import os as _os
    env = dict(_os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, "pipeline.py"] + cmd_args,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env,
        cwd=__import__("pathlib").Path(__file__).resolve().parent
    )


# ── Test 1: XML comment bypass 차단 ─────────────────────────────────────────

def test_build_xml_comment_bypass() -> None:
    """_verify_build_report_xml은 XML comment 내 섹션 태그를 유효하지 않음으로 처리해야 함."""
    import sys as _sys
    _sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
    import importlib
    pl = importlib.import_module("pipeline")

    comment_only = """
    <build_report>
      <!-- <section_1_command>pyinstaller</section_1_command> -->
      <!-- <section_2_environment>OK</section_2_environment> -->
      <!-- <section_3_output>OK</section_3_output> -->
      <!-- <section_4_verification>OK</section_4_verification> -->
      <!-- <section_5_artifacts>OK</section_5_artifacts> -->
      <!-- <section_6_qa_mapping>OK</section_6_qa_mapping> -->
      <!-- <status>BUILD SUCCESS</status> -->
    </build_report>
    """
    ok, msg = pl._verify_build_report_xml(comment_only)
    assert_result(
        not ok,
        "test_build_xml_comment_bypass",
        f"expected rejection but got ok=True, msg={msg}" if ok else f"correctly rejected: {msg}"
    )


# ── Test 2: current_phase 불변식 ─────────────────────────────────────────────

def test_check_gate_phase_mismatch() -> None:
    """current_phase='Phase 8 - Architect (RCA)'일 때 check_gate('dev')는 BLOCKED여야 함."""
    import sys as _sys
    _sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
    import importlib
    pl = importlib.import_module("pipeline")

    test_state = {
        "pipeline_id": "TEST-GATE-PHASE",
        "current_phase": "Phase 8 - Architect (RCA)",
        "terminal_state": None,
        "blocked": False,
        "phases": {p: {"status": "PENDING"} for p in pl.PHASE_ORDER},
    }
    # pm, dev, qa, sec, build, harness 모두 DONE/PASS로 설정 (GATE_RULES 선행 조건 충족)
    test_state["phases"]["pm"]["status"] = "DONE"
    test_state["phases"]["dev"]["status"] = "DONE"
    test_state["phases"]["qa"]["status"] = "PASS"
    test_state["phases"]["sec"]["status"] = "PASS"
    test_state["phases"]["build"]["status"] = "DONE"
    test_state["phases"]["harness"]["status"] = "FAIL"
    test_state["phases"]["architect"]["status"] = "PENDING"

    ok, msg = pl.check_gate(test_state, "dev")
    assert_result(
        not ok and "current_phase" in msg,
        "test_check_gate_phase_mismatch",
        f"expected BLOCKED with 'current_phase' in msg, got ok={ok}, msg={msg}"
    )


# ── Test 3: qa FAIL without --failure-sig → exit 1 ───────────────────────────

def test_qa_fail_without_failure_sig() -> None:
    """pipeline.py qa --result FAIL --numeric-score 60 without --failure-sig은 exit 1이어야 하며,
    --failure-sig 관련 오류 메시지가 출력되어야 함 (terminal gate에서 막힌 경우와 구별).

    check_gate를 우회하기 위해 current_phase='qa'인 상태를 직접 주입하여 검증한다.
    이렇게 해야 --failure-sig 부재 검증 코드(MT-3)까지 실행 경로가 도달한다.
    """
    import sys as _sys
    import io
    import importlib
    _sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
    pl = importlib.import_module("pipeline")

    # current_phase='qa'인 상태를 직접 주입 — check_gate("qa") 통과 조건 충족
    test_state = {
        "pipeline_id": "TEST-QA-FAILSIG",
        "current_phase": "qa",
        "terminal_state": None,
        "blocked": False,
        "phases": {p: {"status": "PENDING", "completed_at": None, "evidence": None,
                       "report_file": None} for p in pl.PHASE_ORDER},
        "qa_fail_history": [],
        "harness_fail_count": 0,
    }
    test_state["phases"]["pm"]["status"] = "DONE"
    test_state["phases"]["dev"]["status"] = "DONE"

    # cmd_qa 로직을 직접 실행하여 --failure-sig 없는 FAIL 호출을 검증
    # _die()가 sys.exit(1)을 호출하므로 SystemExit을 잡아서 검증
    import argparse as _ap

    fake_args = _ap.Namespace(
        result="FAIL",
        numeric_score="60",
        failure_sig=None,
        branch=None,
    )

    captured_output = io.StringIO()
    exit_code = None
    sig_msg_found = False

    # pipeline.py의 _die()가 sys.exit(1) 호출 — SystemExit으로 잡는다
    import unittest.mock as _mock
    original_state_load = None

    try:
        with _mock.patch.object(pl, "_load_branch_state", return_value=test_state):
            with _mock.patch("sys.stdout", new_callable=io.StringIO) as mock_out:
                with _mock.patch("sys.stderr", new_callable=io.StringIO) as mock_err:
                    try:
                        pl.cmd_qa(fake_args)
                    except SystemExit as e:
                        exit_code = e.code
                    combined = mock_out.getvalue() + mock_err.getvalue()
                    sig_msg_found = (
                        "failure-sig" in combined.lower()
                        or "failure_sig" in combined.lower()
                        or "--failure-sig" in combined
                    )
    except Exception as e:
        assert_result(
            False,
            "test_qa_fail_without_failure_sig",
            f"unexpected exception: {e}"
        )
        return

    has_exit1 = exit_code == 1
    assert_result(
        has_exit1 and sig_msg_found,
        "test_qa_fail_without_failure_sig",
        f"expected exit 1 WITH failure-sig message, got exit={exit_code}, sig_msg_found={sig_msg_found}, "
        f"combined_output={combined[:300]}"
    )


# ── Test 4: harness FAIL 후 check_gate('dev') BLOCKED ────────────────────────

def test_harness_fail_requires_architect() -> None:
    """harness FAIL 상태에서 check_gate('dev')는 BLOCKED여야 함 (current_phase != Phase 2)."""
    import sys as _sys
    _sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
    import importlib
    pl = importlib.import_module("pipeline")

    test_state = {
        "pipeline_id": "TEST-HARNESS-FAIL-GATE",
        # harness FAIL 후 architect가 PENDING → current_phase = architect
        "current_phase": "Phase 8 - Architect (RCA)",
        "terminal_state": None,
        "blocked": False,
        "phases": {p: {"status": "PENDING"} for p in pl.PHASE_ORDER},
    }
    test_state["phases"]["pm"]["status"] = "DONE"
    test_state["phases"]["dev"]["status"] = "DONE"
    test_state["phases"]["qa"]["status"] = "PASS"
    test_state["phases"]["sec"]["status"] = "PASS"
    test_state["phases"]["build"]["status"] = "DONE"
    test_state["phases"]["harness"]["status"] = "FAIL"
    test_state["phases"]["architect"]["status"] = "PENDING"

    ok, msg = pl.check_gate(test_state, "dev")
    assert_result(
        not ok,
        "test_harness_fail_requires_architect",
        f"expected BLOCKED after harness FAIL, got ok={ok}, msg={msg}"
    )


# ── Test 5: harness without --user-confirmed → no user-confirmed gate ────────

def test_harness_without_user_confirmed() -> None:
    """user-confirmed 누락만으로는 더 이상 차단하지 않는다. 이후 evidence gate가 판단한다."""
    import tempfile
    import os
    # 절대경로 기반 cwd 사용 (anti-gaming 패턴 16: subprocess 상대경로 실패 방지)
    PROJECT_DIR = str(__import__("pathlib").Path(__file__).resolve().parent)
    with tempfile.NamedTemporaryFile(mode='w', suffix='.xml', delete=False) as f:
        f.write("<harness_report><test_code>print('x')</test_code></harness_report>")
        tmp = f.name
    try:
        result = subprocess.run(
            [sys.executable, "pipeline.py", "harness", "--score", "80",
             "--verdict", "PASS", "--test-output-file", tmp],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=dict(__import__("os").environ, PYTHONIOENCODING="utf-8"),
            cwd=PROJECT_DIR,
        )
        has_exit1 = result.returncode == 1
        combined = result.stdout + result.stderr
        has_msg = (
            "user-confirmed" not in combined.lower()
            and ("HARNESS GATE" in combined or "not a completion path" in combined or "PIPELINE ERROR" in combined)
        )
        assert_result(
            has_exit1 and has_msg,
            "test_harness_without_user_confirmed",
            f"expected exit 1 for non-user gate reason, got exit={result.returncode}, "
            f"stdout={result.stdout[:300]}"
        )
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


# ── Test 6: harness FAIL without --test-output-file → exit 1 ─────────────────

def test_harness_fail_without_test_output_file() -> None:
    """pipeline.py harness --score 40 --verdict FAIL without --test-output-file → exit 1."""
    PROJECT_DIR = str(__import__("pathlib").Path(__file__).resolve().parent)
    result = subprocess.run(
        [sys.executable, "pipeline.py", "harness", "--score", "40",
         "--verdict", "FAIL"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=dict(__import__("os").environ, PYTHONIOENCODING="utf-8"),
        cwd=PROJECT_DIR,
    )
    has_exit1 = result.returncode == 1
    combined = result.stdout + result.stderr
    has_msg = (
        "test-output-file" in combined.lower()
        or "test_output_file" in combined.lower()
        or "HARNESS GATE" in combined
    )
    assert_result(
        has_exit1 and has_msg,
        "test_harness_fail_without_test_output_file",
        f"expected exit 1 WITH test-output-file message, got exit={result.returncode}, "
        f"stdout={result.stdout[:300]}"
    )


# ── BUG-20260508-D541: Test 7~10 XML comment bypass 차단 검증 ────────────────

def _make_harness_tmp_file(content: str) -> str:
    """테스트용 임시 harness output 파일 생성. 절대 경로 반환."""
    import tempfile as _tf
    with _tf.NamedTemporaryFile(
        mode='w', suffix='.xml', delete=False, encoding='utf-8'
    ) as f:
        f.write(content)
        return f.name


def test_harness_fail_comment_only_harness_report() -> None:
    """Test 7: comment 내 <harness_report>만 있는 텍스트 — regex 검출 불가 검증.

    BUG-20260508-D541 High-1: _strip_xml_comments() + regex 조합이
    comment-only harness_report를 검출하지 않아야 한다(→ FAIL 경로 _die() 트리거).
    pipeline.py check_gate가 파이프라인 상태에 따라 BLOCKED될 수 있으므로
    내부 로직을 직접 단위 테스트로 검증한다 (Test 8과 동일 방식, 반대 케이스).
    """
    import sys as _sys
    import importlib
    import re as _re
    _sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
    pl = importlib.import_module("pipeline")

    # comment-only harness_report → strip 후 regex가 찾지 못해야 함
    text_comment_only = (
        "<!-- <harness_report>fake</harness_report> -->"
        "<test_code>print('x')</test_code>"
    )
    clean = pl._strip_xml_comments(text_comment_only)
    # comment 제거 후 harness_report 태그가 없어야 한다
    found_after_strip = bool(_re.search(r"<\s*harness_report\b", clean))

    # 반대로 원본 텍스트에서는 string-in으로 (구버전 검사) 찾을 수 있음을 확인
    old_style_found = "<harness_report>" in text_comment_only

    assert_result(
        old_style_found and not found_after_strip,
        "test_harness_fail_comment_only_harness_report",
        f"old_style_found={old_style_found} (want True, 우회 패턴 확인), "
        f"found_after_strip={found_after_strip} (want False, 새 검증이 차단)"
    )


def test_harness_fail_attr_harness_report_allowed() -> None:
    """Test 8: 속성 있는 <harness_report verdict="FAIL"> → harness FAIL 경로에서 harness_report 검증 통과.

    BUG-20260508-D541 High-1: 정상 태그(속성 포함)는 허용되어야 한다.
    FAIL 경로는 harness_report + test_code 모두 필요하므로 양쪽을 제공,
    실제 PASS/FAIL 여부가 아닌 "harness_report 거부가 발생하지 않는 것"을 검증한다.
    pipeline.py check gate가 BLOCKED일 수 있으므로, _strip_xml_comments + regex 로직만
    직접 단위 테스트로 검증한다.
    """
    import sys as _sys
    import importlib
    _sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
    pl = importlib.import_module("pipeline")

    # 속성 있는 harness_report — regex가 인식해야 함
    text_with_attr = '<harness_report verdict="FAIL"><summary>fail</summary></harness_report>'
    clean = pl._strip_xml_comments(text_with_attr)
    import re as _re
    matched = bool(_re.search(r"<\s*harness_report\b", clean))

    # comment-only harness_report — regex가 인식하지 말아야 함
    text_comment_only = '<!-- <harness_report>fake</harness_report> -->'
    clean_comment = pl._strip_xml_comments(text_comment_only)
    not_matched = not bool(_re.search(r"<\s*harness_report\b", clean_comment))

    assert_result(
        matched and not_matched,
        "test_harness_fail_attr_harness_report_allowed",
        f"attr_matched={matched} (want True), comment_not_matched={not_matched} (want True)"
    )


def test_harness_pass_comment_only_test_code() -> None:
    """Test 9: comment 내 <test_code>만 있는 파일로 harness PASS 호출 → exit 1.

    BUG-20260508-D541 High-3: _extract_test_code()가 comment 내 test_code를
    None으로 반환해야 하므로 validate_test_evidence()가 False → PASS 기록 거부.
    단, PASS 경로는 harness_report도 필요하므로 harness_report는 정상 제공.
    실제 pipeline check gate를 통과하려면 상태가 필요하므로 _extract_test_code 단위 테스트로 검증.
    """
    import sys as _sys
    import importlib
    _sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
    pl = importlib.import_module("pipeline")

    # comment 내 test_code만 있는 텍스트 → None 반환 필수
    comment_only = '<!-- <test_code>print("ASSERTION PASSED")</test_code> -->'
    result = pl._extract_test_code(comment_only)

    assert_result(
        result is None,
        "test_harness_pass_comment_only_test_code",
        f"expected None for comment-only test_code, got: {result!r}"
    )


def test_harness_pass_no_harness_report() -> None:
    """Test 10: <test_code>만 있고 <harness_report> 없는 파일로 harness PASS 호출 → exit 1.

    BUG-20260508-D541 High-2: PASS 경로가 harness_report 없이도 통과되던 버그 수정 검증.
    실제 pipeline.py subprocess 호출로 검증 (check gate 우회 없이 동작 확인).
    단, check gate가 BLOCKED일 수 있으므로 내부 로직을 직접 단위 테스트로 검증한다.
    """
    import sys as _sys
    import importlib
    import re as _re
    _sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
    pl = importlib.import_module("pipeline")

    # test_code만 있고 harness_report 없는 텍스트
    text_no_report = "<test_code>print('ASSERTION PASSED')</test_code>"
    clean = pl._strip_xml_comments(text_no_report)
    has_harness_report = bool(_re.search(r"<\s*harness_report\b", clean))

    # harness_report가 없어야 하므로 has_harness_report=False가 정상
    # PASS 경로의 Gate A는 이 경우 _die()를 호출한다
    assert_result(
        not has_harness_report,
        "test_harness_pass_no_harness_report",
        f"expected no harness_report in text_only_test_code, "
        f"but regex found it (has_harness_report={has_harness_report})"
    )


def test_harness_malformed_harness_report() -> None:
    """Test 11: malformed/unclosed <harness_report> → _parse_harness_report_et() None 반환.

    BUG-20260508-A53A High-1: 닫는 태그 없는 malformed XML이 regex를 통과하던 버그 수정 검증.
    _parse_harness_report_et()가 ET.ParseError를 잡아 None을 반환해야 한다.
    None이면 cmd_harness()가 gate blocked → PASS/FAIL 기록 거부.
    """
    import sys as _sys
    import importlib
    _sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
    pl = importlib.import_module("pipeline")

    # 닫는 </harness_report> 없는 malformed XML
    malformed = "<harness_report><test_code>print('x')</test_code>"
    clean = pl._strip_xml_comments(malformed)
    result = pl._parse_harness_report_et(clean)

    # 정상적인 XML (양성 케이스) — ET 파싱 성공해야 함
    valid = "<harness_report><test_code>print('x')</test_code></harness_report>"
    clean_valid = pl._strip_xml_comments(valid)
    result_valid = pl._parse_harness_report_et(clean_valid)

    # 속성 있는 태그도 정상 파싱돼야 함 (양성 케이스)
    with_attr = '<harness_report verdict="FAIL"><test_code>x</test_code></harness_report>'
    clean_attr = pl._strip_xml_comments(with_attr)
    result_attr = pl._parse_harness_report_et(clean_attr)

    assert_result(
        result is None and result_valid is not None and result_attr is not None,
        "test_harness_malformed_harness_report",
        f"malformed→None={result is None} (want True), "
        f"valid→not_None={result_valid is not None} (want True), "
        f"attr→not_None={result_attr is not None} (want True)"
    )


def test_harness_test_code_outside_harness_report() -> None:
    """Test 12: <test_code>가 <harness_report> 밖에 있으면 _extract_test_code() None 반환.

    BUG-20260508-A53A High-2: test_code가 harness_report 밖에 있어도 파일 전체 regex로
    추출되던 버그 수정 검증. 수정 후 _extract_test_code()는 ET로 harness_report를 파싱하고
    그 내부의 test_code만 반환하므로, 외부의 test_code는 None을 반환해야 한다.
    """
    import sys as _sys
    import importlib
    _sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
    pl = importlib.import_module("pipeline")

    # harness_report는 빈 태그, test_code는 harness_report 바깥에 위치
    outside = "<harness_report></harness_report><test_code>print('ASSERTION PASSED')</test_code>"
    result_outside = pl._extract_test_code(outside)

    # 정상 케이스: test_code가 harness_report 안에 있으면 추출돼야 함 (양성)
    inside = "<harness_report><test_code>print('ASSERTION PASSED')</test_code></harness_report>"
    result_inside = pl._extract_test_code(inside)

    assert_result(
        result_outside is None and result_inside is not None,
        "test_harness_test_code_outside_harness_report",
        f"outside→None={result_outside is None} (want True), "
        f"inside→not_None={result_inside is not None} (want True)"
    )


# ── BUG-20260508-F7A8: Test 13~16 CDATA 필수 규칙 검증 ───────────────────────

def test_raw_lt_in_test_code_no_cdata_fails_et() -> None:
    """Test 13: <test_code>에 raw '<' 포함 (CDATA 없음) → ET.ParseError → _parse_harness_report_et() None 반환.

    BUG-20260508-F7A8 High-1: CDATA 없이 '<' 등 XML 특수문자가 test_code 내에 포함되면
    ET 파서가 ParseError를 반환해야 한다. _parse_harness_report_et()는 None을 반환,
    _extract_test_code()도 None이 되어 harness 기록이 차단된다.
    """
    import sys as _sys
    import importlib
    _sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
    pl = importlib.import_module("pipeline")

    # CDATA 없이 raw '<' 포함 — ET.ParseError를 유발해야 함
    raw_lt_text = (
        "<harness_report>"
        "<test_code>"
        "assert x < 10\n"
        "print('ASSERTION PASSED')"
        "</test_code>"
        "</harness_report>"
    )
    clean = pl._strip_xml_comments(raw_lt_text)
    result = pl._parse_harness_report_et(clean)

    # ET.ParseError로 인해 None 반환 필수
    assert_result(
        result is None,
        "test_raw_lt_in_test_code_no_cdata_fails_et",
        f"expected None (ET.ParseError) for raw '<' in test_code, got: {result!r}"
    )


def test_raw_lt_outside_harness_report_also_blocked() -> None:
    """Test 14: <test_code>가 <harness_report> 바깥에 있고 raw '<' 포함 → _extract_test_code() None 반환.

    BUG-20260508-F7A8 High-2: harness_report 외부에 test_code가 있는 경우,
    BUG-20260508-A53A에 의해 외부 test_code는 이미 None 처리된다.
    여기서는 raw '<' 가 추가로 포함된 외부 test_code도 동일하게 None임을 확인한다.
    """
    import sys as _sys
    import importlib
    _sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
    pl = importlib.import_module("pipeline")

    # harness_report는 닫힌 정상 태그, test_code는 바깥에 있고 raw '<' 포함
    outside_with_lt = (
        "<harness_report></harness_report>"
        "<test_code>assert x < 10\nprint('ASSERTION PASSED')</test_code>"
    )
    result = pl._extract_test_code(outside_with_lt)

    # 외부 test_code이므로 ET 기반 추출에서 None 반환 필수
    assert_result(
        result is None,
        "test_raw_lt_outside_harness_report_also_blocked",
        f"expected None for test_code outside harness_report (with raw '<'), got: {result!r}"
    )


def test_raw_lt_directly_raises_et_parse_error() -> None:
    """Test 15: xml.etree.ElementTree가 raw '<' 포함 XML을 직접 파싱하면 ParseError가 발생하는지 확인.

    BUG-20260508-F7A8 Mid-1: ET 파서가 CDATA 없는 raw '<'에 대해 ET.ParseError를 실제로
    raise하는지 단위 검증. _parse_harness_report_et()가 except ET.ParseError로 None을 반환하는
    근거를 확인한다.
    """
    import xml.etree.ElementTree as ET

    raw_lt_xml = (
        "<harness_report>"
        "<test_code>assert x < 10</test_code>"
        "</harness_report>"
    )
    parse_error_raised = False
    try:
        ET.fromstring(raw_lt_xml)
    except ET.ParseError:
        parse_error_raised = True

    assert_result(
        parse_error_raised,
        "test_raw_lt_directly_raises_et_parse_error",
        "expected ET.ParseError for raw '<' in XML content, but no error was raised"
    )


def test_cdata_wrapped_test_code_extracted_correctly() -> None:
    """Test 16: CDATA로 감싼 test_code → _extract_test_code()가 코드 내용을 정상 반환.

    BUG-20260508-F7A8 양성 케이스: CDATA 섹션 내에 raw '<' 가 포함되어 있어도
    ET 파서가 CDATA를 text 노드로 정상 처리하므로 _extract_test_code()는 코드 내용을 반환해야 한다.
    이 테스트는 CDATA 사용이 올바른 해결책임을 검증한다.
    """
    import sys as _sys
    import importlib
    _sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
    pl = importlib.import_module("pipeline")

    # CDATA로 감싼 test_code (raw '<', '>' 포함 코드도 정상 파싱)
    cdata_text = (
        "<harness_report>"
        "<test_code><![CDATA[\n"
        "assert x < 10\n"
        "assert y > 0\n"
        "print('ASSERTION PASSED')\n"
        "]]></test_code>"
        "</harness_report>"
    )
    result = pl._extract_test_code(cdata_text)

    # CDATA로 감싸면 코드 내용이 정상 반환되어야 함
    has_content = result is not None and "ASSERTION PASSED" in result and "assert x < 10" in result
    assert_result(
        has_content,
        "test_cdata_wrapped_test_code_extracted_correctly",
        f"expected code content with 'ASSERTION PASSED' and 'assert x < 10', got: {result!r}"
    )


# ── BUG-20260509-4D25: Test 17~29 unittest runner 모델 계약 검증 ─────────────


def test_no_unittest_testcase() -> None:
    """Test 17: unittest.TestCase 없는 test_code → validate_test_evidence() False (testsRun=0).

    BUG-20260509-4D25 MT-2: unittest runner 모델에서 TestCase 서브클래스가 없으면
    unittest가 testsRun=0으로 종료하거나 returncode!=0 (exit 5)으로 종료한다.
    두 경우 모두 validate_test_evidence() False.
    """
    import sys as _sys
    import importlib
    _sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
    pl = importlib.import_module("pipeline")

    no_testcase_code = 'x = 1 + 1\nprint("no TestCase here")\n'
    text = (
        "<harness_report>"
        "<test_code><![CDATA["
        + no_testcase_code
        + "]]></test_code>"
        "</harness_report>"
    )
    result = pl.validate_test_evidence(text, pipeline_id="TEST-17")
    assert_result(
        result is False,
        "test_no_unittest_testcase",
        f"expected False (TestCase 없음 → testsRun=0 or returncode!=0), got: {result!r}"
    )


def test_testcase_zero_test_methods() -> None:
    """Test 18: unittest.TestCase 서브클래스 있지만 test_* 메서드 0개 → validate_test_evidence() False.

    BUG-20260509-4D25 MT-2: TestCase 클래스가 있어도 test_로 시작하는 메서드가 없으면
    unittest는 testsRun=0으로 종료한다. 이 경우 gate는 False 반환.
    """
    import sys as _sys
    import importlib
    _sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
    pl = importlib.import_module("pipeline")

    zero_methods_code = (
        "import unittest\n"
        "class MyTests(unittest.TestCase):\n"
        "    def helper(self):\n"
        "        pass\n"
    )
    text = (
        "<harness_report>"
        "<test_code><![CDATA["
        + zero_methods_code
        + "]]></test_code>"
        "</harness_report>"
    )
    result = pl.validate_test_evidence(text, pipeline_id="TEST-18")
    assert_result(
        result is False,
        "test_testcase_zero_test_methods",
        f"expected False (test_* 메서드 0개 → testsRun=0), got: {result!r}"
    )


def test_valid_unittest_passing() -> None:
    """Test 19: 유효한 unittest.TestCase + 통과하는 test_* 메서드 → validate_test_evidence() True (양성 케이스).

    BUG-20260509-4D25 MT-2: unittest runner 모델의 정상 경로 — returncode=0 AND testsRun>=1
    AND FAILED 없음 → True.
    """
    import sys as _sys
    import importlib
    _sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
    pl = importlib.import_module("pipeline")

    passing_code = (
        "import unittest\n"
        "class MyTests(unittest.TestCase):\n"
        "    def test_addition(self):\n"
        "        self.assertEqual(1 + 1, 2)\n"
        "if __name__ == '__main__':\n"
        "    unittest.main()\n"
    )
    text = (
        "<harness_report>"
        "<test_code><![CDATA["
        + passing_code
        + "]]></test_code>"
        "</harness_report>"
    )
    result = pl.validate_test_evidence(text, pipeline_id="TEST-19")
    assert_result(
        result is True,
        "test_valid_unittest_passing",
        f"expected True (유효한 unittest + passing test_*), got: {result!r}"
    )


def test_valid_unittest_failing_assertion() -> None:
    """Test 20: unittest assertion 실패 (assertEqual(1,2)) → validate_test_evidence() False (returncode!=0).

    BUG-20260509-4D25 MT-2: 테스트 실패 시 unittest가 returncode!=0으로 종료하고
    출력에 "FAILED"가 포함된다. gate는 False 반환.
    """
    import sys as _sys
    import importlib
    _sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
    pl = importlib.import_module("pipeline")

    failing_code = (
        "import unittest\n"
        "class MyTests(unittest.TestCase):\n"
        "    def test_will_fail(self):\n"
        "        self.assertEqual(1, 2)\n"
    )
    text = (
        "<harness_report>"
        "<test_code><![CDATA["
        + failing_code
        + "]]></test_code>"
        "</harness_report>"
    )
    result = pl.validate_test_evidence(text, pipeline_id="TEST-20")
    assert_result(
        result is False,
        "test_valid_unittest_failing_assertion",
        f"expected False (assertEqual(1,2) → unittest FAILED → returncode!=0), got: {result!r}"
    )


def test_sys_exit_in_test() -> None:
    """Test 21: test_code에 sys.exit(1) → validate_test_evidence() False (returncode!=0).

    BUG-20260509-4D25 MT-2: sys.exit()는 subprocess 자식에서만 발생하므로
    pipeline.py 자체는 종료되지 않는다 (격리 증명). returncode!=0 → gate False.
    """
    import sys as _sys
    import importlib
    _sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
    pl = importlib.import_module("pipeline")

    exit_code = (
        "import sys\n"
        "import unittest\n"
        "class MyTests(unittest.TestCase):\n"
        "    def test_exit(self):\n"
        "        sys.exit(1)\n"
    )
    text = (
        "<harness_report>"
        "<test_code><![CDATA["
        + exit_code
        + "]]></test_code>"
        "</harness_report>"
    )
    result = pl.validate_test_evidence(text, pipeline_id="TEST-21")
    assert_result(
        result is False,
        "test_sys_exit_in_test",
        f"expected False (sys.exit(1) in test → returncode!=0 → gate blocked), got: {result!r}"
    )


def test_single_call_no_double_exec() -> None:
    """Test 22: validate_test_evidence() 단일 호출 → subprocess 정확히 1회 실행 (double-exec 방지).

    BUG-20260509-4D25 MT-2: append 모드 파일 쓰기를 side-effect 계수기로 사용.
    1회 실행 → 파일 = "x" (1바이트). 2회 실행 → "xx" (2바이트).
    """
    import sys as _sys
    import importlib
    import tempfile
    import os
    _sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
    pl = importlib.import_module("pipeline")

    tmp = tempfile.mktemp(suffix=".txt")
    try:
        side_effect_code = (
            "import unittest\n"
            f"_TMP = r'{tmp}'\n"
            "class MyTests(unittest.TestCase):\n"
            "    def test_side_effect(self):\n"
            "        with open(_TMP, 'a') as f:\n"
            "            f.write('x')\n"
            "        self.assertTrue(True)\n"
        )
        text = (
            "<harness_report>"
            "<test_code><![CDATA["
            + side_effect_code
            + "]]></test_code>"
            "</harness_report>"
        )
        result = pl.validate_test_evidence(text, pipeline_id="TEST-22")
        content = open(tmp, "r").read() if os.path.exists(tmp) else ""
        assert_result(
            result is True and content == "x",
            "test_single_call_no_double_exec",
            f"expected result=True and file='x' (1회 실행), got: result={result!r}, content={content!r}"
        )
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def test_dead_code_no_testcase_fails() -> None:
    """Test 23: dead code (if False: pass) + TestCase 없음 → validate_test_evidence() False (testsRun=0).

    BUG-20260509-4D25 MT-2: dead code 안에 어떤 코드가 있어도 TestCase가 없으면 testsRun=0.
    """
    import sys as _sys
    import importlib
    _sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
    pl = importlib.import_module("pipeline")

    dead_code = "if False:\n    pass\nprint('no TestCase')\n"
    text = (
        "<harness_report>"
        "<test_code><![CDATA["
        + dead_code
        + "]]></test_code>"
        "</harness_report>"
    )
    result = pl.validate_test_evidence(text, pipeline_id="TEST-23")
    assert_result(
        result is False,
        "test_dead_code_no_testcase_fails",
        f"expected False (dead code + TestCase 없음 → testsRun=0), got: {result!r}"
    )


def test_spoof_print_no_testcase_fails() -> None:
    """Test 24: print("ASSERTION PASSED") only + TestCase 없음 → validate_test_evidence() False.

    BUG-20260509-4D25 MT-2: 구 모델의 스푸핑 기법(ASSERTION PASSED 직접 출력)이
    unittest runner 모델에서 무력화됨. TestCase 없음 → testsRun=0 → False.
    """
    import sys as _sys
    import importlib
    _sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
    pl = importlib.import_module("pipeline")

    spoof_code = 'print("ASSERTION PASSED")\n'
    text = (
        "<harness_report>"
        "<test_code><![CDATA["
        + spoof_code
        + "]]></test_code>"
        "</harness_report>"
    )
    result = pl.validate_test_evidence(text, pipeline_id="TEST-24")
    assert_result(
        result is False,
        "test_spoof_print_no_testcase_fails",
        f"expected False (print 스푸핑 + TestCase 없음 → testsRun=0), got: {result!r}"
    )


def test_file_nonce_extraction_attempt_fails() -> None:
    """Test 25: __file__ 기반 nonce 추출 시도 (구 bypass) + TestCase 없음 → False (testsRun=0).

    BUG-20260509-4D25 MT-2: 구 nonce 모델의 bypass 기법 — __file__에서 nonce를 읽어 출력.
    unittest runner에서는 TestCase가 없으므로 testsRun=0 → False.
    """
    import sys as _sys
    import importlib
    _sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
    pl = importlib.import_module("pipeline")

    file_nonce_code = (
        "import os\n"
        "try:\n"
        "    content = open(__file__).read()\n"
        "    for line in content.splitlines():\n"
        "        print(line)\n"
        "except Exception:\n"
        "    pass\n"
        'print("ASSERTION PASSED")\n'
    )
    text = (
        "<harness_report>"
        "<test_code><![CDATA["
        + file_nonce_code
        + "]]></test_code>"
        "</harness_report>"
    )
    result = pl.validate_test_evidence(text, pipeline_id="TEST-25")
    assert_result(
        result is False,
        "test_file_nonce_extraction_attempt_fails",
        f"expected False (__file__ nonce bypass + TestCase 없음 → testsRun=0), got: {result!r}"
    )


def test_print_assertion_passed_only_fails() -> None:
    """Test 26: print("ASSERTION PASSED") only → validate_test_evidence() False.

    BUG-20260509-4D25 MT-2: 추가 스푸핑 케이스 — TestCase 없이 ASSERTION PASSED만 출력.
    unittest runner는 testsRun=0 → False. (Test 24의 변형, 독립 검증)
    """
    import sys as _sys
    import importlib
    _sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
    pl = importlib.import_module("pipeline")

    only_print_code = 'print("ASSERTION PASSED")\n'
    text = (
        "<harness_report>"
        "<test_code><![CDATA["
        + only_print_code
        + "]]></test_code>"
        "</harness_report>"
    )
    result = pl.validate_test_evidence(text, pipeline_id="TEST-26")
    assert_result(
        result is False,
        "test_print_assertion_passed_only_fails",
        f"expected False (ASSERTION PASSED print only + TestCase 없음), got: {result!r}"
    )


def test_assert_counter_hardcoded_fails() -> None:
    """Test 27: print("__ASSERT_COUNTER__") 하드코딩 + TestCase 없음 → validate_test_evidence() False.

    BUG-20260509-4D25 MT-2: 구 모델 고정 마커 스푸핑 기법이 unittest runner에서 무력화됨.
    TestCase 없음 → testsRun=0 → False.
    """
    import sys as _sys
    import importlib
    _sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
    pl = importlib.import_module("pipeline")

    counter_spoof_code = (
        'print("__ASSERT_COUNTER__", flush=True)\n'
        'print("ASSERTION PASSED")\n'
    )
    text = (
        "<harness_report>"
        "<test_code><![CDATA["
        + counter_spoof_code
        + "]]></test_code>"
        "</harness_report>"
    )
    result = pl.validate_test_evidence(text, pipeline_id="TEST-27")
    assert_result(
        result is False,
        "test_assert_counter_hardcoded_fails",
        f"expected False (__ASSERT_COUNTER__ 스푸핑 + TestCase 없음 → testsRun=0), got: {result!r}"
    )


# ── BUG-20260509-FA5E (BUG-20260509-B208): Tests 30-33 JSON runner 모델 ──────


def test_noop_no_assertion_fails() -> None:
    """Test 30: noop 테스트 (assertion 없이 pass만) → validate_test_evidence() False.

    BUG-20260509-FA5E MT-2: JSON runner 모델에서 assertionCount==0이면 차단.
    TestCase + test_* 메서드가 있어도 실 assertion이 없으면 False.
    구 모델(python -m unittest)은 returncode=0 + testsRun=1 + no FAILED로 통과했었음.
    """
    import sys as _sys
    import importlib
    _sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
    pl = importlib.import_module("pipeline")

    noop_code = (
        "import unittest\n"
        "class MyTests(unittest.TestCase):\n"
        "    def test_noop(self):\n"
        "        pass\n"
        "if __name__ == '__main__':\n"
        "    unittest.main()\n"
    )
    text = (
        "<harness_report>"
        "<test_code><![CDATA["
        + noop_code
        + "]]></test_code>"
        "</harness_report>"
    )
    result = pl.validate_test_evidence(text, pipeline_id="TEST-30")
    assert_result(
        result is False,
        "test_noop_no_assertion_fails",
        f"expected False (noop — assertionCount=0), got: {result!r}"
    )


def test_skip_decorator_fails() -> None:
    """Test 31: @unittest.skip 데코레이터 → validate_test_evidence() False.

    BUG-20260509-FA5E MT-2: JSON runner 모델에서 skipped>=1이면 차단.
    구 모델(returncode=0 + no FAILED)은 skip된 테스트를 통과시켰었음.
    """
    import sys as _sys
    import importlib
    _sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
    pl = importlib.import_module("pipeline")

    skip_code = (
        "import unittest\n"
        "class MyTests(unittest.TestCase):\n"
        "    @unittest.skip('intentionally skipped')\n"
        "    def test_skipped(self):\n"
        "        self.assertEqual(1, 1)\n"
        "if __name__ == '__main__':\n"
        "    unittest.main()\n"
    )
    text = (
        "<harness_report>"
        "<test_code><![CDATA["
        + skip_code
        + "]]></test_code>"
        "</harness_report>"
    )
    result = pl.validate_test_evidence(text, pipeline_id="TEST-31")
    assert_result(
        result is False,
        "test_skip_decorator_fails",
        f"expected False (@unittest.skip → skipped>=1), got: {result!r}"
    )


def test_expected_failure_decorator_fails() -> None:
    """Test 32: @unittest.expectedFailure (실패하는 테스트) → validate_test_evidence() False.

    BUG-20260509-FA5E MT-2: JSON runner 모델에서 expectedFailures>=1이면 차단.
    실제로 실패하는 assertEqual(1,2)에 @expectedFailure가 붙으면 expectedFailures=1 → False.
    구 모델은 returncode=0 + no FAILED로 통과시켰었음.
    """
    import sys as _sys
    import importlib
    _sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
    pl = importlib.import_module("pipeline")

    expected_fail_code = (
        "import unittest\n"
        "class MyTests(unittest.TestCase):\n"
        "    @unittest.expectedFailure\n"
        "    def test_expected_to_fail(self):\n"
        "        self.assertEqual(1, 2)\n"
        "if __name__ == '__main__':\n"
        "    unittest.main()\n"
    )
    text = (
        "<harness_report>"
        "<test_code><![CDATA["
        + expected_fail_code
        + "]]></test_code>"
        "</harness_report>"
    )
    result = pl.validate_test_evidence(text, pipeline_id="TEST-32")
    assert_result(
        result is False,
        "test_expected_failure_decorator_fails",
        f"expected False (@expectedFailure + failing assert → expectedFailures>=1), got: {result!r}"
    )


def test_valid_assertion_still_passes() -> None:
    """Test 33: 유효한 self.assertEqual(1+1, 2) → validate_test_evidence() True (회귀 검증).

    BUG-20260509-FA5E MT-2: JSON runner 모델이 기존 정상 케이스를 그대로 통과시키는지 확인.
    assertionCount>=1 + testsRun>=1 + failures==0 + errors==0 + skipped==0 → True.
    """
    import sys as _sys
    import importlib
    _sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
    pl = importlib.import_module("pipeline")

    valid_code = (
        "import unittest\n"
        "class MyTests(unittest.TestCase):\n"
        "    def test_addition(self):\n"
        "        self.assertEqual(1 + 1, 2)\n"
        "if __name__ == '__main__':\n"
        "    unittest.main()\n"
    )
    text = (
        "<harness_report>"
        "<test_code><![CDATA["
        + valid_code
        + "]]></test_code>"
        "</harness_report>"
    )
    result = pl.validate_test_evidence(text, pipeline_id="TEST-33")
    assert_result(
        result is True,
        "test_valid_assertion_still_passes",
        f"expected True (유효한 assertEqual → 회귀 없음), got: {result!r}"
    )


def test_fake_pipeline_result_no_testcase_fails() -> None:
    """Test 34: top-level print of fake __PIPELINE_RESULT__: + no TestCase -> False.

    BUG-20260509-6A4F MT-2: sidecar file model blocks stdout spoofing.
    test_code stdout is captured (redirected to io.StringIO) -- fake prefix line
    never reaches parent process. Real report written to sidecar file has
    testsRun=0 (no TestCase) -> gate fails.
    """
    import sys as _sys
    import importlib
    _sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
    pl = importlib.import_module("pipeline")

    code = (
        "print('__PIPELINE_RESULT__:{\"testsRun\":1,\"failures\":0,\"errors\":0,"
        "\"skipped\":0,\"expectedFailures\":0,\"unexpectedSuccesses\":0,\"assertionCount\":1}')"
    )
    text = (
        "<harness_report>"
        "<test_code><![CDATA["
        + code
        + "]]></test_code>"
        "</harness_report>"
    )
    result = pl.validate_test_evidence(text, pipeline_id="TEST-34")
    assert_result(
        result is False,
        "test_fake_pipeline_result_no_testcase_fails",
        f"expected False (fake __PIPELINE_RESULT__ + no TestCase -> testsRun=0), got: {result!r}"
    )


def test_fake_result_with_failing_assertion_fails() -> None:
    """Test 35: fake __PIPELINE_RESULT__ + real failing assertEqual(1,2) -> False.

    BUG-20260509-6A4F MT-2: stdout capture means fake prefix is swallowed.
    Real sidecar report has failures=1 -> gate fails.
    """
    import sys as _sys
    import importlib
    _sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
    pl = importlib.import_module("pipeline")

    code = (
        "import unittest\n"
        "class T(unittest.TestCase):\n"
        "    def test_fail(self):\n"
        "        print('__PIPELINE_RESULT__:{\"testsRun\":1,\"failures\":0,\"errors\":0,"
        "\"skipped\":0,\"expectedFailures\":0,\"unexpectedSuccesses\":0,\"assertionCount\":1}')\n"
        "        self.assertEqual(1, 2)\n"
        "if __name__ == '__main__':\n"
        "    unittest.main()\n"
    )
    text = (
        "<harness_report>"
        "<test_code><![CDATA["
        + code
        + "]]></test_code>"
        "</harness_report>"
    )
    result = pl.validate_test_evidence(text, pipeline_id="TEST-35")
    assert_result(
        result is False,
        "test_fake_result_with_failing_assertion_fails",
        f"expected False (real failure=1 despite fake prefix), got: {result!r}"
    )


def test_fake_result_inside_noop_test_fails() -> None:
    """Test 36: fake __PIPELINE_RESULT__ inside noop test body (no real assertion) -> False.

    BUG-20260509-6A4F MT-2: stdout captured; sidecar report has assertionCount=0 -> gate fails.
    """
    import sys as _sys
    import importlib
    _sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
    pl = importlib.import_module("pipeline")

    code = (
        "import unittest\n"
        "class T(unittest.TestCase):\n"
        "    def test_noop(self):\n"
        "        print('__PIPELINE_RESULT__:{\"testsRun\":1,\"failures\":0,\"errors\":0,"
        "\"skipped\":0,\"expectedFailures\":0,\"unexpectedSuccesses\":0,\"assertionCount\":1}')\n"
        "if __name__ == '__main__':\n"
        "    unittest.main()\n"
    )
    text = (
        "<harness_report>"
        "<test_code><![CDATA["
        + code
        + "]]></test_code>"
        "</harness_report>"
    )
    result = pl.validate_test_evidence(text, pipeline_id="TEST-36")
    assert_result(
        result is False,
        "test_fake_result_inside_noop_test_fails",
        f"expected False (noop test, assertionCount=0 despite fake prefix), got: {result!r}"
    )


def test_import_main_manipulation_noop_fails() -> None:
    """Test 37: import __main__; __main__._assertion_count = 1 + noop test → False.

    BUG-20260509-ED9C: test_code가 import __main__로 runner의 전역을 조작하려 해도
    AST gate가 test_* 메서드 내 assert* 없음을 사전에 탐지하여 차단한다.
    """
    import sys as _sys
    import importlib
    _sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
    pl = importlib.import_module("pipeline")

    code = (
        "import unittest, __main__\n"
        "class T(unittest.TestCase):\n"
        "    def test_noop(self):\n"
        "        __main__._assertion_count = 1\n"
        "        pass\n"
    )
    text = (
        "<harness_report>"
        "<test_code><![CDATA["
        + code
        + "]]></test_code>"
        "</harness_report>"
    )
    result = pl.validate_test_evidence(text, pipeline_id="TEST-37")
    assert_result(
        result is False,
        "test_import_main_manipulation_noop_fails",
        f"expected False (noop test — no assert* in test_* methods, AST gate catches it), got: {result!r}"
    )


def test_atexit_failing_test_fails() -> None:
    """Test 38: atexit.register() + real failing assertEqual(1, 2) → False.

    BUG-20260509-ED9C: atexit은 test subprocess 종료 시에만 실행되며 부모 pipeline.py에
    영향을 주지 않는다. 실제 assertion 실패(returncode != 0)로 False 반환.
    """
    import sys as _sys
    import importlib
    _sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
    pl = importlib.import_module("pipeline")

    code = (
        "import sys, atexit, unittest\n"
        "atexit.register(lambda: print('atexit ran'))\n"
        "class T(unittest.TestCase):\n"
        "    def test_fail(self):\n"
        "        self.assertEqual(1, 2)\n"
    )
    text = (
        "<harness_report>"
        "<test_code><![CDATA["
        + code
        + "]]></test_code>"
        "</harness_report>"
    )
    result = pl.validate_test_evidence(text, pipeline_id="TEST-38")
    assert_result(
        result is False,
        "test_atexit_failing_test_fails",
        f"expected False (real assertion failure — returncode != 0), got: {result!r}"
    )


def test_atexit_no_testcase_fails() -> None:
    """Test 39: atexit only, no TestCase, no assert* → False.

    BUG-20260509-ED9C: assert* 없는 코드는 AST gate에서 사전 차단된다.
    atexit 등록은 test subprocess 내에서만 실행되어 부모에 영향 없음.
    """
    import sys as _sys
    import importlib
    _sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
    pl = importlib.import_module("pipeline")

    code = (
        "import atexit\n"
        "atexit.register(lambda: print('atexit'))\n"
    )
    text = (
        "<harness_report>"
        "<test_code><![CDATA["
        + code
        + "]]></test_code>"
        "</harness_report>"
    )
    result = pl.validate_test_evidence(text, pipeline_id="TEST-39")
    assert_result(
        result is False,
        "test_atexit_no_testcase_fails",
        f"expected False (no assert* in test_* methods — AST gate blocks it), got: {result!r}"
    )


# ── BUG-20260509-894D: Tests 40-44 runner-owned JSON 채널 + AST forbidden ────


def test_dead_code_assert_fails() -> None:
    """Test 40: dead code assert (if False: self.assertEqual(1,1)) → False.

    BUG-20260509-894D MT-2: AST 정적 카운트(ast_count)는 >=1이지만,
    실행 시 if False 브랜치가 실행되지 않아 executed_assertions=0.
    runner-owned JSON 채널 모델이 이 케이스를 차단한다.
    구 모델(testsRun>=1 + returncode==0)은 이 케이스를 통과시켰었음.
    """
    import sys as _sys
    import importlib
    _sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
    pl = importlib.import_module("pipeline")

    dead_code = (
        "import unittest\n"
        "class MyTests(unittest.TestCase):\n"
        "    def test_dead(self):\n"
        "        if False:\n"
        "            self.assertEqual(1, 1)  # dead code — never executed\n"
        "if __name__ == '__main__':\n"
        "    unittest.main()\n"
    )
    text = (
        "<harness_report>"
        "<test_code><![CDATA["
        + dead_code
        + "]]></test_code>"
        "</harness_report>"
    )
    result = pl.validate_test_evidence(text, pipeline_id="TEST-40")
    assert_result(
        result is False,
        "test_dead_code_assert_fails",
        f"expected False (dead code assert — executed_assertions=0), got: {result!r}"
    )


def test_monkeypatch_assert_ast_reject() -> None:
    """Test 41: monkeypatch — unittest.TestCase.assertEqual 재할당 → False (AST hard-reject).

    BUG-20260509-894D MT-2: test_code 내에서 assert* 메서드를 lambda로 교체하면
    _ast_forbidden_check()가 Assign-to-attribute 패턴을 탐지하여 즉시 False 반환.
    monkeypatch로 executed_assertions 카운터를 우회하려는 시도를 사전 차단.
    """
    import sys as _sys
    import importlib
    _sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
    pl = importlib.import_module("pipeline")

    monkeypatch_code = (
        "import unittest\n"
        "# monkeypatch: replace assertEqual so the counter never increments\n"
        "unittest.TestCase.assertEqual = lambda *a, **k: None\n"
        "class MyTests(unittest.TestCase):\n"
        "    def test_with_patched_assert(self):\n"
        "        self.assertEqual(1, 2)  # patched — no real assertion\n"
        "if __name__ == '__main__':\n"
        "    unittest.main()\n"
    )
    text = (
        "<harness_report>"
        "<test_code><![CDATA["
        + monkeypatch_code
        + "]]></test_code>"
        "</harness_report>"
    )
    result = pl.validate_test_evidence(text, pipeline_id="TEST-41")
    assert_result(
        result is False,
        "test_monkeypatch_assert_ast_reject",
        f"expected False (monkeypatch — AST hard-reject on assert* reassignment), got: {result!r}"
    )


def test_unittest_main_in_test_method_ast_reject() -> None:
    """Test 42: unittest.main() call inside test_* method body → False (AST hard-reject).

    BUG-20260509-894D MT-2: test_* 메서드 내부에서 unittest.main()을 직접 호출하면
    _ast_forbidden_check()가 탐지하여 즉시 False 반환.
    이 패턴은 runner 프로세스 내에서 재귀적 unittest 실행을 유발할 수 있음.
    """
    import sys as _sys
    import importlib
    _sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
    pl = importlib.import_module("pipeline")

    main_in_test_code = (
        "import unittest\n"
        "class MyTests(unittest.TestCase):\n"
        "    def test_bypass_via_main(self):\n"
        "        self.assertEqual(1, 1)\n"
        "        unittest.main()  # forbidden: unittest.main inside test_* method\n"
        "if __name__ == '__main__':\n"
        "    unittest.main()\n"
    )
    text = (
        "<harness_report>"
        "<test_code><![CDATA["
        + main_in_test_code
        + "]]></test_code>"
        "</harness_report>"
    )
    result = pl.validate_test_evidence(text, pipeline_id="TEST-42")
    assert_result(
        result is False,
        "test_unittest_main_in_test_method_ast_reject",
        f"expected False (unittest.main() in test_* body — AST hard-reject), got: {result!r}"
    )


def test_fake_stderr_no_testcase_fails() -> None:
    """Test 43: fake stderr injection + no TestCase → False.

    BUG-20260509-894D MT-2: test_code가 sys.stderr에 'Ran 1 test\\nOK' 같은
    가짜 unittest 출력을 직접 써도, 새 모델은 stderr를 파싱하지 않고
    runner-owned JSON 채널만 신뢰한다.
    TestCase 없음 → ast_count=0 → Step 1 AST gate에서 이미 차단.
    """
    import sys as _sys
    import importlib
    _sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
    pl = importlib.import_module("pipeline")

    # 가짜 stderr 출력으로 구 stderr-파싱 모델을 우회하려는 시도
    # 새 모델: stderr 파싱 없음 → ast_count=0 → False
    fake_stderr_code = (
        "import sys\n"
        "sys.stderr.write('Ran 1 test in 0.001s\\n')\n"
        "sys.stderr.write('OK\\n')\n"
        "print('Ran 1 test\\nOK')\n"
    )
    text = (
        "<harness_report>"
        "<test_code><![CDATA["
        + fake_stderr_code
        + "]]></test_code>"
        "</harness_report>"
    )
    result = pl.validate_test_evidence(text, pipeline_id="TEST-43")
    assert_result(
        result is False,
        "test_fake_stderr_no_testcase_fails",
        f"expected False (fake stderr + no TestCase → ast_count=0 → AST gate blocks), got: {result!r}"
    )


def test_unreachable_assert_after_return_ast_reject() -> None:
    """Test 44: unreachable assert after return → False (AST hard-reject).

    BUG-20260509-894D MT-2: test_* 메서드에서 return 문 이후에 assert*를 배치하면
    _ast_forbidden_check()가 dead code 패턴을 탐지하여 즉시 False 반환.
    이 패턴은 ast_count>=1이지만 executed_assertions=0이 될 수 있으나,
    AST 사전 탐지로 더 명확한 오류 메시지와 함께 차단한다.
    """
    import sys as _sys
    import importlib
    _sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
    pl = importlib.import_module("pipeline")

    unreachable_assert_code = (
        "import unittest\n"
        "class MyTests(unittest.TestCase):\n"
        "    def test_unreachable(self):\n"
        "        return  # early return\n"
        "        self.assertEqual(1, 1)  # unreachable — AST detects this\n"
        "if __name__ == '__main__':\n"
        "    unittest.main()\n"
    )
    text = (
        "<harness_report>"
        "<test_code><![CDATA["
        + unreachable_assert_code
        + "]]></test_code>"
        "</harness_report>"
    )
    result = pl.validate_test_evidence(text, pipeline_id="TEST-44")
    assert_result(
        result is False,
        "test_unreachable_assert_after_return_ast_reject",
        f"expected False (unreachable assert after return — AST hard-reject), got: {result!r}"
    )


def test_main_exec_assert_count_spoof_ast_reject() -> None:
    """Test 45: __main__._exec_assert_count spoof + dead assert → False.

    Runner globals must not be reachable from test_code. The strict AST policy
    rejects __main__ imports before subprocess launch.
    """
    import sys as _sys
    import importlib
    _sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
    pl = importlib.import_module("pipeline")

    spoof_code = (
        "import unittest, __main__\n"
        "class MyTests(unittest.TestCase):\n"
        "    def test_noop(self):\n"
        "        __main__._exec_assert_count = 1\n"
        "        if False:\n"
        "            self.assertEqual(1, 1)\n"
    )
    text = (
        "<harness_report>"
        "<test_code><![CDATA["
        + spoof_code
        + "]]></test_code>"
        "</harness_report>"
    )
    result = pl.validate_test_evidence(text, pipeline_id="TEST-45")
    assert_result(
        result is False,
        "test_main_exec_assert_count_spoof_ast_reject",
        f"expected False (__main__ runner counter spoof — AST hard-reject), got: {result!r}"
    )


def test_atexit_result_path_overwrite_ast_reject() -> None:
    """Test 46: atexit + __main__._result_path overwrite → False.

    The previous runner-owned JSON model still exposed _result_path through
    __main__. Strict policy rejects atexit/__main__ imports.
    """
    import sys as _sys
    import importlib
    _sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
    pl = importlib.import_module("pipeline")

    overwrite_code = (
        "import unittest, atexit, __main__\n"
        "atexit.register(lambda: open(__main__._result_path, 'w', encoding='utf-8').write('{}'))\n"
        "class MyTests(unittest.TestCase):\n"
        "    def test_fail(self):\n"
        "        self.assertEqual(1, 2)\n"
    )
    text = (
        "<harness_report>"
        "<test_code><![CDATA["
        + overwrite_code
        + "]]></test_code>"
        "</harness_report>"
    )
    result = pl.validate_test_evidence(text, pipeline_id="TEST-46")
    assert_result(
        result is False,
        "test_atexit_result_path_overwrite_ast_reject",
        f"expected False (atexit result overwrite — AST hard-reject), got: {result!r}"
    )


def test_inspect_result_path_overwrite_ast_reject() -> None:
    """Test 47: inspect.stack() result path overwrite → False.

    Frame introspection can reveal runner locals if allowed. Strict policy
    rejects inspect imports before runner launch.
    """
    import sys as _sys
    import importlib
    _sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
    pl = importlib.import_module("pipeline")

    inspect_code = (
        "import unittest, inspect\n"
        "class MyTests(unittest.TestCase):\n"
        "    def test_frame_probe(self):\n"
        "        for frame_info in inspect.stack():\n"
        "            self.assertIsNotNone(frame_info)\n"
    )
    text = (
        "<harness_report>"
        "<test_code><![CDATA["
        + inspect_code
        + "]]></test_code>"
        "</harness_report>"
    )
    result = pl.validate_test_evidence(text, pipeline_id="TEST-47")
    assert_result(
        result is False,
        "test_inspect_result_path_overwrite_ast_reject",
        f"expected False (inspect frame probe — AST hard-reject), got: {result!r}"
    )


def test_sys_argv_probe_ast_reject() -> None:
    """Test 48: sys.argv runner path probe → False."""
    import sys as _sys
    import importlib
    _sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
    pl = importlib.import_module("pipeline")

    code = (
        "import unittest, sys\n"
        "class MyTests(unittest.TestCase):\n"
        "    def test_probe(self):\n"
        "        self.assertTrue(bool(sys.argv))\n"
    )
    text = "<harness_report><test_code><![CDATA[" + code + "]]></test_code></harness_report>"
    result = pl.validate_test_evidence(text, pipeline_id="TEST-48")
    assert_result(
        result is False,
        "test_sys_argv_probe_ast_reject",
        f"expected False (sys.argv runner path probe — AST hard-reject), got: {result!r}"
    )


def test_sys_modules_probe_ast_reject() -> None:
    """Test 49: sys.modules __main__ probe → False."""
    import sys as _sys
    import importlib
    _sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
    pl = importlib.import_module("pipeline")

    code = (
        "import unittest, sys\n"
        "class MyTests(unittest.TestCase):\n"
        "    def test_probe(self):\n"
        "        self.assertIsNotNone(sys.modules.get('__main__'))\n"
    )
    text = "<harness_report><test_code><![CDATA[" + code + "]]></test_code></harness_report>"
    result = pl.validate_test_evidence(text, pipeline_id="TEST-49")
    assert_result(
        result is False,
        "test_sys_modules_probe_ast_reject",
        f"expected False (sys.modules runner probe — AST hard-reject), got: {result!r}"
    )


def test_getattr_probe_ast_reject() -> None:
    """Test 50: dynamic getattr runner/reflection probe → False."""
    import sys as _sys
    import importlib
    _sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
    pl = importlib.import_module("pipeline")

    code = (
        "import unittest\n"
        "class MyTests(unittest.TestCase):\n"
        "    def test_probe(self):\n"
        "        self.assertIsNotNone(getattr(self, 'assertEqual'))\n"
    )
    text = "<harness_report><test_code><![CDATA[" + code + "]]></test_code></harness_report>"
    result = pl.validate_test_evidence(text, pipeline_id="TEST-50")
    assert_result(
        result is False,
        "test_getattr_probe_ast_reject",
        f"expected False (getattr reflection probe — AST hard-reject), got: {result!r}"
    )


# ── Runner ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=== Pipeline Gate Negative Tests (BUG-20260507-C2E2 / BUG-20260508-6198 / BUG-20260508-D541 / BUG-20260508-A53A / BUG-20260508-F7A8 / BUG-20260509-4D25 / BUG-20260509-FA5E / BUG-20260509-6A4F / BUG-20260509-ED9C / BUG-20260509-894D) ===")
    print()
    test_build_xml_comment_bypass()
    test_check_gate_phase_mismatch()
    test_qa_fail_without_failure_sig()
    test_harness_fail_requires_architect()
    test_harness_without_user_confirmed()
    test_harness_fail_without_test_output_file()
    # BUG-20260508-D541 추가 테스트
    test_harness_fail_comment_only_harness_report()
    test_harness_fail_attr_harness_report_allowed()
    test_harness_pass_comment_only_test_code()
    test_harness_pass_no_harness_report()
    # BUG-20260508-A53A 추가 테스트
    test_harness_malformed_harness_report()
    test_harness_test_code_outside_harness_report()
    # BUG-20260508-F7A8 추가 테스트 (CDATA 권장 규칙)
    test_raw_lt_in_test_code_no_cdata_fails_et()
    test_raw_lt_outside_harness_report_also_blocked()
    test_raw_lt_directly_raises_et_parse_error()
    test_cdata_wrapped_test_code_extracted_correctly()
    # BUG-20260509-4D25 추가 테스트 (unittest runner 모델 계약)
    test_no_unittest_testcase()
    test_testcase_zero_test_methods()
    test_valid_unittest_passing()
    test_valid_unittest_failing_assertion()
    test_sys_exit_in_test()
    test_single_call_no_double_exec()
    test_dead_code_no_testcase_fails()
    test_spoof_print_no_testcase_fails()
    test_file_nonce_extraction_attempt_fails()
    test_print_assertion_passed_only_fails()
    test_assert_counter_hardcoded_fails()
    test_harness_evidence_gate_fail_no_testcase()
    test_harness_evidence_gate_fail_failing_unittest()
    # BUG-20260509-FA5E (BUG-20260509-B208) 추가 테스트 (JSON runner assertionCount 모델)
    test_noop_no_assertion_fails()
    test_skip_decorator_fails()
    test_expected_failure_decorator_fails()
    test_valid_assertion_still_passes()
    # BUG-20260509-6A4F 추가 테스트 (sidecar JSON file model / stdout spoof 차단)
    test_fake_pipeline_result_no_testcase_fails()
    test_fake_result_with_failing_assertion_fails()
    test_fake_result_inside_noop_test_fails()
    # BUG-20260509-ED9C 추가 테스트 (완전 프로세스 격리 / 공유 상태 조작 차단)
    test_import_main_manipulation_noop_fails()
    test_atexit_failing_test_fails()
    test_atexit_no_testcase_fails()
    # BUG-20260509-894D 추가 테스트 (runner-owned JSON 채널 + AST forbidden 패턴)
    test_dead_code_assert_fails()
    test_monkeypatch_assert_ast_reject()
    test_unittest_main_in_test_method_ast_reject()
    test_fake_stderr_no_testcase_fails()
    test_unreachable_assert_after_return_ast_reject()
    test_main_exec_assert_count_spoof_ast_reject()
    test_atexit_result_path_overwrite_ast_reject()
    test_inspect_result_path_overwrite_ast_reject()
    test_sys_argv_probe_ast_reject()
    test_sys_modules_probe_ast_reject()
    test_getattr_probe_ast_reject()
    print()
    print(f"결과: {PASS_COUNT} passed, {FAIL_COUNT} failed")

    if FAIL_COUNT == 0:
        print("ALL TESTS PASSED")
    sys.exit(0 if FAIL_COUNT == 0 else 1)
