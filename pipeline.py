#!/usr/bin/env python3
"""Work Protocol Pipeline Enforcer v1.3

IMP-20260506-A064 — 5개 강제 게이트 추가:
  MT-1: PM 분석 게이트 (done --phase pm --decomp --clarification --roadmap)
  MT-2: QA numeric_score 기록 강제 (qa --result --numeric-score)
  MT-3: Circuit Breaker failure_signature 추적 (qa --result FAIL --failure-sig FAILURE_SIG)
  MT-4: BUILD 6-Section Report 파일 존재 검증 (build --report-file)
  MT-5: Frozen Codebase scope 선언 (done --phase dev --scope-declared)

BUG-20260507-C2E2 — 4개 추가 게이트:
  MT-1: BUILD XML comment bypass 차단 (_verify_build_report_xml, ET only, regex fallback 없음)
  MT-2: check_gate current_phase 불변식 추가 (phase != current_phase → BLOCKED)
  MT-3: TERMINATED terminal state 추가 + cmd_terminate()
  MT-4: docstring 플래그 표기 수정 (required 항목은 대괄호 없이)

BUG-20260508-D541 — XML evidence gate 강화:
  MT-1: _strip_xml_comments() 공통 유틸 추출 (comment bypass 차단)
  MT-2: _extract_test_code() comment 제거 후 추출
  MT-3: cmd_harness() PASS 경로 harness_report 검증 추가
  MT-4: cmd_harness() FAIL 경로 comment-safe 검증 + 속성 있는 태그 허용 + test_code 검증 추가
  MT-5: PHASE_INTERFACE["harness"] required_xml 계약 FAIL 경로 일치

BUG-20260508-A53A — harness XML evidence gate ElementTree 업그레이드:
  MT-1: _parse_harness_report_et() 신규 함수 + _extract_test_code() 재구현
        (harness_report 내부 test_code만 인정 — 파일 전체 regex 검색 폐기)
  MT-2: cmd_harness() PASS 경로 Gate A — regex → _parse_harness_report_et() 교체
  MT-3: cmd_harness() FAIL 경로 — regex 2개 → hr_element ET 검사 교체
  MT-4: argparse help + Gate 2 _die() 메시지 — FAIL 경로 계약(harness_report+test_code) 동기화
  MT-5: test_pipeline_gates.py — cmd_harness() 직접 호출 negative test 2건 추가

BUG-20260509-7D6E — exec() 격리 위반 수정 + 문서 동기화:
  MT-1: _AssertCounter + _count_executed_asserts(exec() 기반) 완전 제거
        → _instrument_assert_code() 신설 (AST에서 assert 직전 print("__ASSERT_COUNTER__") 삽입,
           ast.unparse() 소스 반환, 실행 금지)
        validate_test_evidence() Gate 0: SyntaxError + assert 0개 fast fail
        → instrumented code를 subprocess에 전달, stdout "__ASSERT_COUNTER__" AND "ASSERTION PASSED" 모두 확인
        exec() 완전 제거 — sys.exit() 격리 위반 및 이중 실행 방지
  MT-2: 4개 MD 파일 문서 동기화 (test-harness-agent.md, CLAUDE.md, Global_Wiki.md, agents.md)
        "AST 파싱으로 assert 존재 확인" → "instrumented subprocess + __ASSERT_COUNTER__ + ASSERTION PASSED"
  MT-3: test_pipeline_gates.py — Test 24(sys.exit 격리), Test 25(double execution 방지) 추가
        stale CDATA 주석 2개 수정 (BUG-20260509-7D6E)

BUG-20260509-4D25 — validate_test_evidence() 루트 픽스 (unittest runner 모델):
  MT-1: _instrument_assert_code() 완전 삭제
        validate_test_evidence() 재작성 — python -m unittest subprocess 실행
        pass criteria: returncode==0 + testsRun>=1 + FAILED 미포함 (폐기됨; 최신 모델은 BUG-20260509-894D 참조)
        nonce/AST instrumentation/ASSERTION PASSED 방식 완전 폐기
  MT-2: test_pipeline_gates.py Tests 17-29 교체 — unittest 계약 기반
  MT-3: 6 MD 파일 문서 동기화 (test-harness-agent.md, CLAUDE.md, agents.md,
        Global_Wiki.md, anti_gaming_rules.md, task.md)

BUG-20260509-FA5E / BUG-20260509-B208 — JSON runner 모델 (assertionCount 검사):
  MT-1: _RUNNER_TEMPLATE 상수 추가 (validate_test_evidence 위)
        validate_test_evidence() 재작성 — _RUNNER_TEMPLATE 기반 tmp_runner.py 실행
        pass criteria: testsRun>=1 + failures==0 + errors==0 + skipped==0 +
                       expectedFailures==0 + unexpectedSuccesses==0 + assertionCount>=1
        구 python -m unittest / Ran N test regex / "FAILED" in output 방식 폐기
  MT-2: test_pipeline_gates.py Tests 30-33 추가 (noop/skip/expectedFailure/regression)
  MT-3: 6 MD 파일 문서 동기화 (test-harness-agent.md, CLAUDE.md, agents.md,
        Global_Wiki.md, anti_gaming_rules.md, task.md)

BUG-20260509-6A4F — 사이드카 JSON 파일 모델 (stdout 채널 분리):
  MT-1: _RUNNER_TEMPLATE 재작성 — sys.argv[2] sidecar 파일에 JSON 기록
        test_code stdout을 io.StringIO()로 리디렉션 → print() 기반 스푸핑 원천 차단
        __PIPELINE_RESULT__: 출력 완전 제거
        validate_test_evidence() 재작성 — subprocess 완료 후 sidecar 파일 읽기
        stdout __PIPELINE_RESULT__: 파싱 로직 완전 제거
  MT-2: test_pipeline_gates.py Tests 34-36 추가 (스푸핑 차단 검증)
  MT-3: 6 MD 파일 문서 동기화 (test-harness-agent.md, CLAUDE.md, agents.md,
        Global_Wiki.md, anti_gaming_rules.md, task.md)

BUG-20260509-ED9C — 완전 프로세스 격리 모델 (AST + isolated subprocess):
  MT-1: _RUNNER_TEMPLATE 상수 완전 삭제
        sidecar 파일 로직 완전 삭제 (report_path, uuid, os.path.exists, json.load, io.StringIO)
        _ast_assert_count() 신설 — AST 기반 test_* 메서드 내 assert* 정적 계수
        validate_test_evidence() 재작성 — AST check FIRST + python tmp_test.py isolated subprocess
        test_code 서브프로세스는 자체 __main__, argv, atexit 보유 — 공유 상태 없음
        차단 패턴: import __main__ 조작 (AST gate), atexit sidecar overwrite (경로 없음)
        통과 기준: returncode==0 + testsRun>=1 + skipped==0 + expectedFailures==0 + unexpectedSuccesses==0 (폐기됨)
  MT-2: test_pipeline_gates.py Tests 37-39 추가
  MT-3: 6 MD 파일 문서 동기화 (test-harness-agent.md, CLAUDE.md, agents.md,
        Global_Wiki.md, anti_gaming_rules.md, task.md)

BUG-20260509-894D — runner-owned JSON 채널 모델 (executed_assertions 런타임 카운터):
  MT-1: _ast_forbidden_check() 신규 추가 — AST로 금지 패턴 hard-reject
          (monkeypatch: TestCase 클래스/인스턴스 assert* 재할당,
           unittest.main() in test_* method body,
           unreachable assert: return 후 assert* 호출)
        validate_test_evidence() 재작성 — stdin nonce + runner_{nonce}.py 기반 executed_assertions 런타임 카운터
          Step 1: _ast_assert_count() → 0이면 즉시 False (AST 정적 검사)
          Step 2: _ast_forbidden_check() → 금지 패턴 발견 시 즉시 False
          Step 3: runner_{nonce}.py 생성, nonce/test_code는 stdin으로 전달
          Step 4: subprocess.run([python, runner_nonce.py], cwd=work_dir)
          Step 5: stdout JSON line nonce 확인 + executed_assertions >= 1 + 기타 조건 AND 체크
          stderr 텍스트 파싱/sidecar result file 완전 폐기 — runner-owned nonce JSON line만 신뢰
        차단 추가 케이스:
          dead code assert (if False: self.assertEqual): executed_assertions=0 → False
          monkeypatch (TestCase.assertEqual = lambda): AST hard-reject → False
          unreachable assert (return 후 assert): executed_assertions=0 → False
        통과 기준: executed_assertions>=1 AND testsRun>=1 AND failures==0 AND errors==0
                   AND skipped==0 AND expectedFailures==0 AND unexpectedSuccesses==0
  MT-2: test_pipeline_gates.py Tests 40-44 추가
          Test 40: dead code assert → False (executed_assertions=0)
          Test 41: monkeypatch → False (AST hard-reject)
          Test 42: unittest.main in test_* → False (AST hard-reject)
          Test 43: fake stderr → False (stderr 파싱 없음 + executed_assertions=0)
          Test 44: unreachable assert after return → False (executed_assertions=0)
          Test 45: __main__ runner counter spoof → False (AST hard-reject)
          Test 46: atexit result overwrite → False (AST hard-reject)
          Test 47: inspect frame probe → False (AST hard-reject)
  MT-3: 6 MD 파일 동기화 (test-harness-agent.md, CLAUDE.md, agents.md,
        Global_Wiki.md, anti_gaming_rules.md, task.md)

파이프라인 상태를 파일로 관리하여 Phase 순서를 기술적으로 강제합니다.
텍스트 규칙이 아닌 실제 gate 검증으로 우회를 차단합니다.

사용법:
    python pipeline.py new --type BUG --desc "버튼 작동 안 함"
    python pipeline.py status
    python pipeline.py check --phase dev
    python pipeline.py done --phase pm --report-file step_plan.xml --decomp --clarification --roadmap
    python pipeline.py done --phase dev --files "core/ai_engine.py,ui/app.py" --report-file dev_handover.xml --scope-declared --scope-manifest scope_manifest.json
    python pipeline.py qa --result PASS --numeric-score 110 --report-file qa_report.xml
    python pipeline.py qa --result FAIL --numeric-score 70 --failure-sig "AL:a1b2c3d4"
    python pipeline.py sec --result PASS --risk LOW
    python pipeline.py sec --skip
    python pipeline.py build --exe "dist/SmartNotepad.exe" [--report-file dist/build_report.xml]
    python pipeline.py build --exe "N/A" --skip-reason "meta-task" --user-confirmed
    python pipeline.py harness --score 95 --verdict PASS --test-output-file harness_output.xml --user-confirmed
    python pipeline.py contract init --three-gate
    python pipeline.py gates technical
    python pipeline.py gates oracle --user-confirmed
    python pipeline.py gates accept --result ACCEPT --evidence output.png --user-confirmed
    python pipeline.py advisory status
    python pipeline.py architect --report-file architect_report.xml
    python pipeline.py terminate
    python pipeline.py list
    python pipeline.py log --message "rate limit으로 QA 대기 중"
"""

import argparse
import json
import sys
import os

def _force_utf8_stdio() -> None:
    """Force UTF-8 CLI output on Windows cp949/cmd.exe/redirected streams."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


_force_utf8_stdio()

import hashlib
import importlib.util
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, TypedDict
import re
import socket
import subprocess
import tempfile
import shutil
import urllib.error
import urllib.request


# ── Execution Evidence Validator ─────────────────────────────────────────────

def _strip_xml_comments(text: str) -> str:
    """XML comment (<!-- ... -->) 를 텍스트에서 제거하여 comment 내 태그 우회를 차단.

    BUG-20260508-D541 MT-1: _scan_xml_tags() 내 중복 로직을 이 유틸로 통합.
    _extract_test_code(), cmd_harness() PASS/FAIL 경로에서 공통으로 사용.
    """
    return re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)


def _parse_harness_report_et(clean_text: str) -> "Optional[Any]":
    """comment-stripped 텍스트에서 <harness_report>...</harness_report> 블록을 추출하여
    ElementTree로 파싱하고 root Element를 반환한다.

    BUG-20260508-A53A MT-1: regex-only 검증 → ElementTree 파싱으로 업그레이드.
    - 닫는 태그 없는 malformed XML → ET.ParseError → None 반환 (gate blocked).
    - <harness_report> 블록 자체가 없으면 → None 반환.
    - 속성 있는 태그(<harness_report verdict="FAIL">) 정상 허용.

    Args:
        clean_text: _strip_xml_comments() 처리가 완료된 텍스트.

    Returns:
        ET.Element (parse 성공) 또는 None (블록 없음 / malformed XML).
    """
    import xml.etree.ElementTree as ET

    # <harness_report ...>...</harness_report> 블록 추출 (속성 포함 허용)
    m = re.search(r"(<harness_report\b[^>]*>.*?</harness_report\s*>)", clean_text, re.DOTALL)
    if not m:
        return None

    try:
        root = ET.fromstring(m.group(1))
    except ET.ParseError:
        # malformed / unclosed XML → parse 실패 → gate block
        return None

    return root


def _extract_test_code(agent_output: str) -> Optional[str]:
    """에이전트 출력에서 <test_code> 블록을 추출한다.

    BUG-20260508-A53A MT-1: <harness_report> 내부에 위치한 test_code만 인정.
    - comment 제거(_strip_xml_comments) 후 _parse_harness_report_et()로 harness_report를
      ElementTree 파싱하고, 그 element 안의 test_code 텍스트를 반환한다.
    - harness_report 없음 / malformed XML / test_code가 harness_report 밖에 있음 → None.
    이전 방식(파일 전체 regex 검색)을 폐기하여 High-2 결함(test_code 외부 배치 우회)을 차단.
    """
    clean = _strip_xml_comments(agent_output)
    root = _parse_harness_report_et(clean)
    if root is None:
        return None
    text = root.findtext("test_code")
    return text.strip() if text else None


def _validate_harness_evidence_gate(
    clean_text: str,
    agent_output: str,
    pipeline_id: str,
    verdict_label: str,
) -> None:
    """PASS/FAIL 공통 3-gate 검증: Gate A(harness_report ET 파싱) → Gate B(test_code 비어있지 않음) → Gate C(validate_test_evidence 실행).
    실패 시 _die() 호출. 성공 시 반환.

    BUG-20260509-FDBC MT-1: PASS/FAIL 검증 비대칭 해소.
    - 기존에는 PASS 경로만 Gate C(validate_test_evidence)를 실행하고 FAIL 경로는 Gate B(존재 확인)에서 멈췄다.
    - 수정 후 FAIL 경로도 동일한 3-gate를 통과해야 한다.
    - FAIL verdict는 harness_score < 80을 뜻하며, test_code 자체가 실패 코드가 아님.
      따라서 FAIL 기록 시에도 test_code는 unittest.TestCase + test_* 메서드 + testsRun>=1 필수.

    Args:
        clean_text: _strip_xml_comments() 처리된 텍스트 (Gate A 입력).
        agent_output: 원본 에이전트 출력 (Gate C 입력 — validate_test_evidence 내부에서 _extract_test_code 재호출).
        pipeline_id: 로깅용 파이프라인 ID.
        verdict_label: 오류 메시지에 포함할 경로 레이블 ("PASS" 또는 "FAIL").
    """
    # Gate A: <harness_report> ElementTree 파싱 검증
    hr_element = _parse_harness_report_et(clean_text)
    if hr_element is None:
        _die(
            f"\n[HARNESS GATE BLOCKED] {verdict_label} 기록 거부 — --test-output-file에 유효한 <harness_report>가 없습니다.\n"
            "  harness 분석 보고서(<harness_report>...</harness_report>)와 실행 가능한 <test_code>가 모두 필요합니다.\n"
            "  malformed/unclosed <harness_report> 또는 XML comment 내 태그는 유효하지 않습니다.\n"
        )

    # Gate B: <test_code> 존재 + 비어있지 않음 확인
    # hr_element는 None이 아님이 Gate A에서 보장되었으므로 안전하게 접근 가능
    test_code_text: Optional[str] = hr_element.findtext("test_code")  # type: ignore[union-attr]
    if not test_code_text or not test_code_text.strip():
        _die(
            f"\n[HARNESS GATE BLOCKED] {verdict_label} 기록 거부 — <test_code>가 없거나 비어 있습니다.\n"
            "  <test_code>는 반드시 <harness_report>...</harness_report> 내부에 위치해야 하며 내용이 있어야 합니다.\n"
            "  XML 특수문자(<, >, &) 포함 시 CDATA 또는 XML escape 필수: <test_code><![CDATA[...]]></test_code>\n"
        )

    # Gate C: <test_code> unittest 실행 검증
    # validate_test_evidence()가 strict AST policy + runner-owned JSON 결과를 검증한다.
    if not validate_test_evidence(agent_output, pipeline_id=pipeline_id):
        _die(
            f"\n[HARNESS GATE BLOCKED] {verdict_label} 기록 거부 — <test_code> unittest 실행 검증 실패.\n"
            "  test_code는 unittest.TestCase 서브클래스 + 최소 1개 test_* 메서드 필수.\n"
            "  빈 test_code, 실행 불가 Python, unittest.TestCase 없는 코드, testsRun=0은 모두 거부됩니다.\n"
        )


# ── Strict Test Evidence Policy ──────────────────────────────────────────────
# Static pre-execution check: count direct self.assert*/cls.assert* calls in
# test_* methods via AST, then reject runner/result-channel tampering patterns.

def _ast_assert_count(code: str) -> int:
    """Count direct self.assert*/cls.assert* calls in test_* methods via AST.

    BUG-20260509-ED9C: Static check performed BEFORE subprocess launch.
    Catches noop tests (no assertion) and import __main__ manipulation attempts
    (which have no assert* calls in test_* methods).

    Returns 0 on SyntaxError or if no assert* calls found in test_* methods.
    """
    import ast as _ast
    try:
        tree = _ast.parse(code)
    except SyntaxError:
        return 0
    count = 0
    for cls_node in _ast.walk(tree):
        if not isinstance(cls_node, _ast.ClassDef):
            continue
        for method in cls_node.body:
            if not isinstance(method, _ast.FunctionDef):
                continue
            if not method.name.startswith("test"):
                continue
            for node in _ast.walk(method):
                if not isinstance(node, _ast.Call):
                    continue
                func = node.func
                if (
                    isinstance(func, _ast.Attribute)
                    and func.attr.startswith("assert")
                    and isinstance(func.value, _ast.Name)
                    and func.value.id in {"self", "cls"}
                ):
                    count += 1
    return count


def _ast_forbidden_check(code: str) -> Optional[str]:
    """AST 기반 금지 패턴 탐지 — 우회 시도를 하드 리젝한다.

    BUG-20260509-894D: _ast_assert_count()가 정적 존재 여부만 검사하는 것을 보완하여,
    실행 결과를 조작하거나 검증 자체를 우회하려는 코드 패턴을 사전에 차단한다.

    탐지 패턴:
      1. Runner/process introspection: __main__, inspect, atexit, sys.argv,
         sys.modules, sys._getframe 등 runner 전역/결과 채널 접근.
      2. Dynamic reflection/eval: getattr/setattr/globals/locals/vars/exec/eval 등.
      3. Monkeypatch: unittest/TestCase/result/assert 메서드 재할당.
      4. load_tests/unittest.main in test_* body/unreachable assert.

    Args:
        code: test_code 문자열.

    Returns:
        금지 패턴 발견 시 사유 문자열. 패턴 없으면 None.
    """
    import ast as _ast

    try:
        tree = _ast.parse(code)
    except SyntaxError:
        return None  # SyntaxError는 _ast_assert_count에서 이미 차단됨

    forbidden_import_roots = {
        "__main__", "atexit", "inspect", "gc", "ctypes", "importlib", "runpy", "traceback",
        "os",
    }
    forbidden_calls = {
        "eval", "exec", "compile", "globals", "locals", "vars",
        "getattr", "setattr", "delattr", "__import__",
    }
    forbidden_dunder_attrs = {
        "__dict__", "__class__", "__bases__", "__mro__", "__subclasses__",
        "__globals__", "__closure__", "__code__", "__getattribute__", "__setattr__",
    }
    forbidden_frame_attrs = {
        "f_globals", "f_locals", "f_back", "tb_frame", "gi_frame", "cr_frame",
    }
    forbidden_sys_attrs = {
        "argv", "modules", "_getframe", "_current_frames", "settrace", "setprofile",
    }
    runner_private_names = {
        "_result_path", "_exec_assert_count", "_counter", "_result", "_test_src",
        "_suite", "_tr", "_loader", "_orig_methods",
    }

    def _root_name(expr: Any) -> Optional[str]:
        cur = expr
        while isinstance(cur, _ast.Attribute):
            cur = cur.value
        if isinstance(cur, _ast.Name):
            return cur.id
        return None

    for node in _ast.walk(tree):
        # 패턴 1: runner/process introspection imports
        if isinstance(node, (_ast.Import, _ast.ImportFrom)):
            names: List[str] = []
            if isinstance(node, _ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            else:
                if node.module:
                    names = [node.module.split(".")[0]]
            for name in names:
                if name in forbidden_import_roots:
                    return f"FORBIDDEN: import of runner/process introspection module '{name}'"
                if name == "unittest" and isinstance(node, _ast.ImportFrom) and node.module == "unittest.mock":
                    return "FORBIDDEN: unittest.mock import can monkeypatch runner/test results"
            if isinstance(node, _ast.ImportFrom) and node.module and node.module.startswith("unittest.mock"):
                return "FORBIDDEN: unittest.mock import can monkeypatch runner/test results"

        # 패턴 2: load_tests can replace unittest discovery with forged suites.
        if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)) and node.name == "load_tests":
            return "FORBIDDEN: load_tests hook can override unittest discovery"

        # 패턴 3: reflection/eval calls and unittest.mock patch calls.
        if isinstance(node, _ast.Call):
            func = node.func
            if isinstance(func, _ast.Name) and func.id in forbidden_calls:
                return f"FORBIDDEN: dynamic reflection call '{func.id}()'"
            if isinstance(func, _ast.Attribute):
                root = _root_name(func)
                if func.attr in {"patch", "patch.object"} or (root in {"mock", "unittest"} and func.attr == "patch"):
                    return "FORBIDDEN: mock.patch can alter assertions or test results"
                if root == "unittest" and func.attr == "main":
                    # Top-level guarded unittest.main is harmless because the runner loads the module as _test_module.
                    # Calls inside test_* methods are rejected below with a clearer message.
                    pass

        # 패턴 4: dangerous attribute access.
        if isinstance(node, _ast.Attribute):
            root = _root_name(node)
            if root == "sys" and node.attr in forbidden_sys_attrs:
                return f"FORBIDDEN: sys.{node.attr} can expose runner internals"
            if node.attr in forbidden_dunder_attrs or node.attr in forbidden_frame_attrs:
                return f"FORBIDDEN: introspective attribute '{node.attr}' can expose runner internals"
            if node.attr in runner_private_names:
                return f"FORBIDDEN: runner private attribute '{node.attr}' access"
            if root == "unittest" and node.attr == "mock":
                return "FORBIDDEN: unittest.mock can monkeypatch runner/test results"

        # 패턴 5: runner private names.
        if isinstance(node, _ast.Name) and node.id in runner_private_names:
            return f"FORBIDDEN: runner private name '{node.id}' access"

        # 패턴 6: Monkeypatch — attribute reassignment.
        if isinstance(node, _ast.Assign):
            for target in node.targets:
                if not isinstance(target, _ast.Attribute):
                    continue
                root = _root_name(target)
                if (
                    target.attr.startswith("assert")
                    or root == "unittest"
                    or target.attr in {"addFailure", "addError", "addSkip", "addSuccess"}
                ):
                    return (
                        f"FORBIDDEN: monkeypatch detected — "
                        f"'{target.attr}' attribute reassignment can alter evidence results"
                    )

        # 패턴 2: unittest.main() call inside test_* method body
        # ClassDef 내 FunctionDef(test_*)에서 unittest.main 또는 bare unittest.main() 탐지
        if isinstance(node, _ast.ClassDef):
            for item in node.body:
                if not isinstance(item, _ast.FunctionDef):
                    continue
                if not item.name.startswith("test"):
                    continue
                for stmt in _ast.walk(item):
                    if not isinstance(stmt, _ast.Call):
                        continue
                    func = stmt.func
                    # unittest.main() — Attribute call: unittest.main
                    if (
                        isinstance(func, _ast.Attribute)
                        and func.attr == "main"
                        and isinstance(func.value, _ast.Name)
                        and func.value.id == "unittest"
                    ):
                        return (
                            "FORBIDDEN: unittest.main() call inside test_* method — "
                            "causes runner result corruption"
                        )

        # 패턴 3: Unreachable assert — return 이후 assert* 호출 (in test_* methods)
        # ClassDef 내 FunctionDef(test_*) body에서 Return 노드 이후 assert* 탐지
        if isinstance(node, _ast.ClassDef):
            for item in node.body:
                if not isinstance(item, _ast.FunctionDef):
                    continue
                if not item.name.startswith("test"):
                    continue
                # test_* method body를 순서대로 검사: Return 이후 Expr(Call(assert*)) 차단
                found_return = False
                for stmt in item.body:
                    if isinstance(stmt, _ast.Return):
                        found_return = True
                        continue
                    if found_return:
                        # Return 이후 모든 assert* 호출 탐지
                        for sub in _ast.walk(stmt):
                            if not isinstance(sub, _ast.Call):
                                continue
                            func = sub.func
                            if (
                                isinstance(func, _ast.Attribute)
                                and func.attr.startswith("assert")
                            ):
                                return (
                                    f"FORBIDDEN: unreachable assert* call after return in "
                                    f"'{item.name}' — executed_assertions=0 (dead code)"
                                )

    return None


# ── runner template for executed_assertions counting ─────────────────────────
# runner_{nonce}.py receives {nonce, test_src} through stdin. The nonce is not
# embedded in source, argv, env, or filesystem paths. The runner emits exactly
# one trusted JSON line with that nonce after test execution.

_RUNNER_SOURCE_TEMPLATE = (
    "import sys, json, unittest, types as _types, io, contextlib\n"
    "\n"
    "def _pipeline_runner_main():\n"
    "    cfg = json.loads(sys.stdin.read())\n"
    "    expected_nonce = cfg['nonce']\n"
    "    test_src = cfg['test_src']\n"
    "    trusted_stdout = sys.stdout\n"
    "    sys.stdin = io.StringIO('')\n"
    "    counter = {'executed_assertions': 0}\n"
    "    default = {'nonce': expected_nonce, 'testsRun': 0, 'failures': 1, 'errors': 0,\n"
    "               'skipped': 0, 'expectedFailures': 0, 'unexpectedSuccesses': 0,\n"
    "               'executed_assertions': 0}\n"
    "\n"
    "    def emit_result(payload):\n"
    "        payload = dict(payload)\n"
    "        payload['nonce'] = expected_nonce\n"
    "        print('__PIPELINE_RESULT_JSON__:' + json.dumps(payload, separators=(',', ':')), file=trusted_stdout)\n"
    "        trusted_stdout.flush()\n"
    "\n"
    "    def make_counting_assert(name, orig):\n"
    "        def counting(*args, **kwargs):\n"
    "            counter['executed_assertions'] += 1\n"
    "            return orig(*args, **kwargs)\n"
    "        counting.__name__ = name\n"
    "        return counting\n"
    "\n"
    "    orig_methods = {}\n"
    "    for attr in dir(unittest.TestCase):\n"
    "        if attr.startswith('assert'):\n"
    "            orig = getattr(unittest.TestCase, attr)\n"
    "            if callable(orig):\n"
    "                orig_methods[attr] = orig\n"
    "                setattr(unittest.TestCase, attr, make_counting_assert(attr, orig))\n"
    "\n"
    "    mod = _types.ModuleType('_test_module')\n"
    "    mod.__file__ = '<test_code>'\n"
    "    mod.__name__ = '_test_module'\n"
    "    captured_stdout = io.StringIO()\n"
    "    captured_stderr = io.StringIO()\n"
    "    try:\n"
    "        with contextlib.redirect_stdout(captured_stdout), contextlib.redirect_stderr(captured_stderr):\n"
    "            exec(compile(test_src, '<test_code>', 'exec'), mod.__dict__)\n"
    "            loader = unittest.TestLoader()\n"
    "            suite = loader.loadTestsFromModule(mod)\n"
    "            tr = unittest.TestResult()\n"
    "            suite.run(tr)\n"
    "        result = {\n"
    "            'nonce': expected_nonce,\n"
    "            'testsRun': tr.testsRun,\n"
    "            'failures': len(tr.failures),\n"
    "            'errors': len(tr.errors),\n"
    "            'skipped': len(tr.skipped),\n"
    "            'expectedFailures': len(tr.expectedFailures),\n"
    "            'unexpectedSuccesses': len(tr.unexpectedSuccesses),\n"
    "            'executed_assertions': counter['executed_assertions'],\n"
    "        }\n"
    "    except BaseException:\n"
    "        result = dict(default)\n"
    "        result['errors'] = 1\n"
    "        emit_result(result)\n"
    "        raise\n"
    "    emit_result(result)\n"
    "    if result['failures'] or result['errors']:\n"
    "        sys.exit(1)\n"
    "\n"
    "if __name__ == '__main__':\n"
    "    _pipeline_runner_main()\n"
)


def validate_test_evidence(agent_output: str, pipeline_id: str = "") -> bool:  # noqa: ARG001 reserved for future audit
    """<test_code> 블록을 runner-owned JSON 채널 모델로 실행하여 검증한다.

    runner-owned JSON 채널 + executed_assertions 런타임 카운터.
    - Step 1: _ast_assert_count()로 AST 정적 검사 (사전 실행, 런타임 조작 불가).
      * test_* 메서드 내 assert* 호출 0개 → 즉시 False (noop/dead code 사전 차단).
    - Step 2: _ast_forbidden_check()로 금지 패턴 하드 리젝.
      * monkeypatch (assert* 재할당): AST에서 탐지 → 즉시 False.
      * unittest.main() in test_* body: AST에서 탐지 → 즉시 False.
      * unreachable assert (return 후 assert*): AST에서 탐지 → 즉시 False.
      * __main__/inspect/atexit/sys.argv/sys.modules 등 runner introspection 차단.
    - Step 3: runner_{nonce}.py 생성.
      * unittest.TestCase.assert* 메서드 패치 → executed_assertions 카운터 증가.
      * test_code를 exec()으로 모듈로 로드 → TestLoader로 스위트 수집 → TestResult 실행.
      * stdin으로 받은 nonce/test_code 기반으로 trusted JSON line 출력.
    - Step 4: subprocess.run([python, runner_nonce.py], cwd=work_dir) — 독립 프로세스.
      * runner 파일과 test cwd는 서로 다른 디렉터리.
      * stderr 텍스트 파싱 없음 — runner-owned JSON 채널만 신뢰.
    - Step 5: returncode + JSON line nonce + 7개 조건 ALL AND 체크.
    - Pass criteria (모두 AND):
        * _ast_assert_count(code) >= 1        ← AST 정적 검사 (Step 1)
        * _ast_forbidden_check(code) is None  ← 금지 패턴 없음 (Step 2)
        * proc.returncode == 0
        * result["nonce"] == expected_nonce
        * executed_assertions >= 1             ← 런타임 실행 카운터 (Step 5)
        * testsRun >= 1
        * failures == 0
        * errors == 0
        * skipped == 0                         ← @unittest.skip 차단
        * expectedFailures == 0                ← @unittest.expectedFailure 차단
        * unexpectedSuccesses == 0
    - timeout: 30초.
    - tmp_dir 전체 삭제 (shutil.rmtree) — 단일 cleanup.

    추가 차단 케이스 (BUG-20260509-894D):
        - dead code assert (if False: self.assertEqual): ast_count>=1이지만
          executed_assertions=0 → Step 5에서 차단
        - monkeypatch: Step 2 AST hard-reject
        - unittest.main() in test_* method: Step 2 AST hard-reject
        - fake stderr injection: stderr 파싱 없으므로 무효,
          executed_assertions=0 → Step 5에서 차단
        - __main__/_result_path/inspect/atexit: Step 2 AST hard-reject
        - unreachable assert after return: Step 2 AST hard-reject
          (또는 executed_assertions=0으로 Step 5에서도 차단)
    """
    import shutil as _shutil

    code = _extract_test_code(agent_output)
    if not code:
        print(RED("[EVIDENCE GATE] <test_code> 블록 없음 — 채점 거부"))
        return False

    # Step 1: AST check FIRST — static, cannot be manipulated at runtime
    ast_count = _ast_assert_count(code)
    if ast_count == 0:
        print(RED("[EVIDENCE GATE] assert* calls not found in test_* methods (AST) — noop 차단"))
        return False

    # Step 2: AST forbidden pattern check — hard-reject before subprocess launch
    forbidden_reason = _ast_forbidden_check(code)
    if forbidden_reason is not None:
        print(RED(f"[EVIDENCE GATE] 금지 패턴 탐지 — AST hard-reject: {forbidden_reason}"))
        return False

    tmp_root: Optional[str] = None
    try:
        tmp_root = tempfile.mkdtemp()
        runner_dir = os.path.join(tmp_root, "runner")
        work_dir = os.path.join(tmp_root, "work")
        os.makedirs(runner_dir, exist_ok=True)
        os.makedirs(work_dir, exist_ok=True)

        # Step 3: Build runner_{nonce}.py with unique result path
        # Use repr() for safe embedding of arbitrary strings into Python source.
        # str.replace() is used instead of .format() to avoid conflicts with
        # dict-literal braces ({}) that appear in the template source code.
        nonce = secrets.token_hex(16)
        runner_path = os.path.join(runner_dir, f"runner_{nonce}.py")

        with open(runner_path, mode="w", encoding="utf-8") as f:
            f.write(_RUNNER_SOURCE_TEMPLATE)

        # Step 4: Run runner in isolated subprocess — runner owns the JSON channel.
        # cwd is separate from runner location so cwd scans cannot discover runner source.
        runner_input = json.dumps({"nonce": nonce, "test_src": code}, ensure_ascii=True)
        try:
            proc = subprocess.run(
                [sys.executable, runner_path],
                input=runner_input,
                capture_output=True, text=True, encoding="utf-8", timeout=30,
                cwd=work_dir,
            )
        except subprocess.TimeoutExpired:
            print(RED("[EVIDENCE GATE] subprocess timeout (30초)"))
            return False

        # Step 5: Read runner-owned JSON result line — no stderr parsing.
        result = None
        prefix = "__PIPELINE_RESULT_JSON__:"
        for line in (proc.stdout or "").splitlines():
            if not line.startswith(prefix):
                continue
            try:
                candidate = json.loads(line[len(prefix):])
            except json.JSONDecodeError:
                continue
            if candidate.get("nonce") == nonce:
                result = candidate
                break
        if result is None:
            print(RED("[EVIDENCE GATE] runner JSON 결과 라인 없음 또는 nonce 불일치 — 실행 실패"))
            combined = (proc.stderr or "") + (proc.stdout or "")
            if combined:
                print(DIM(f"  출력:\n{combined[:500]}"))
            return False

        if result.get("nonce") != nonce:
            print(RED("[EVIDENCE GATE] 검증 실패: runner JSON nonce 불일치 — 결과 채널 오염 의심"))
            return False

        if proc.returncode != 0:
            print(RED(f"[EVIDENCE GATE] 검증 실패: returncode={proc.returncode} (failures/errors 존재)"))
            combined = (proc.stderr or "") + (proc.stdout or "")
            if combined:
                print(DIM(f"  출력:\n{combined[:500]}"))
            return False

        tests_run = int(result.get("testsRun", 0))
        failures = int(result.get("failures", 0))
        errors = int(result.get("errors", 0))
        skipped = int(result.get("skipped", 0))
        expected_failures = int(result.get("expectedFailures", 0))
        unexpected_successes = int(result.get("unexpectedSuccesses", 0))
        executed_assertions = int(result.get("executed_assertions", 0))

        # 7-condition AND gate
        if executed_assertions < 1:
            print(RED(
                f"[EVIDENCE GATE] 검증 실패: executed_assertions={executed_assertions} (요구: >=1) "
                "— dead code assert 또는 noop 차단"
            ))
            return False
        if tests_run < 1:
            print(RED(f"[EVIDENCE GATE] 검증 실패: testsRun={tests_run} (요구: >=1)"))
            return False
        if failures > 0:
            print(RED(f"[EVIDENCE GATE] 검증 실패: failures={failures} (요구: ==0)"))
            return False
        if errors > 0:
            print(RED(f"[EVIDENCE GATE] 검증 실패: errors={errors} (요구: ==0)"))
            return False
        if skipped > 0:
            print(RED(f"[EVIDENCE GATE] 검증 실패: skipped={skipped} (요구: ==0) — @unittest.skip 차단"))
            return False
        if expected_failures > 0:
            print(RED(
                f"[EVIDENCE GATE] 검증 실패: expectedFailures={expected_failures} (요구: ==0) "
                "— @expectedFailure 차단"
            ))
            return False
        if unexpected_successes > 0:
            print(RED(f"[EVIDENCE GATE] 검증 실패: unexpectedSuccesses={unexpected_successes} (요구: ==0)"))
            return False

        print(GREEN(
            f"[EVIDENCE GATE] 검증 통과 — testsRun={tests_run}, "
            f"executed_assertions={executed_assertions}, astAsserts={ast_count}"
        ))
        return True

    except Exception as e:
        print(RED(f"[EVIDENCE GATE] 실행 실패: {e}"))
        return False
    finally:
        if tmp_root is not None:
            try:
                _shutil.rmtree(tmp_root, ignore_errors=True)
            except Exception:
                pass


def validate_harness_evidence(submitted_files: List[str]) -> bool:
    """제출된 .py 파일에 py_compile + (tests/ 존재 시) pytest 실행. 모두 통과해야 True."""
    py_files = [
        f for f in submitted_files
        if f.strip().endswith(".py") and Path(f.strip()).exists()
    ]

    if not py_files:
        return True

    for f in py_files:
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", f.strip()],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            print(RED(f"[HARNESS GATE] Syntax error: {f}"))
            print(DIM(result.stderr[:300]))
            return False

    print(GREEN(f"[HARNESS GATE] py_compile 통과: {len(py_files)}개 파일"))

    # pytest 실행 — py_files가 있으면 tests/ 디렉토리 필수
    tests_dir = BASE_DIR / "tests"
    if not tests_dir.exists():
        print(DIM("[HARNESS GATE] tests/ 디렉토리 없음 — py_compile 통과만으로 검증 완료"))
        return True

    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", str(tests_dir), "-q", "--tb=short"],
            capture_output=True, text=True, cwd=str(BASE_DIR), timeout=120
        )
        if result.returncode != 0:
            print(RED("[HARNESS GATE] pytest 실패"))
            print(DIM(result.stdout[-500:]))
            return False
        print(GREEN("[HARNESS GATE] pytest 통과"))
    except subprocess.TimeoutExpired:
        print(RED("[HARNESS GATE] pytest timeout (120초)"))
        return False

    return True


def _strip_xml_brackets(tag: str) -> str:
    """'<qa_report>' → 'qa_report'. 시작/종료 마크 모두 제거."""
    return tag.strip().lstrip("<").rstrip(">").rstrip("/").strip()


def _scan_xml_tags(text: str, required_tags: List[str]) -> Tuple[List[str], Dict[str, int]]:
    """텍스트에서 required_tags(이름만)가 등장하는 라인 번호를 수집.

    1차: xml.etree.ElementTree로 보고서를 dummy root로 감싸 파싱 시도.
    2차(파싱 실패 시): 안정적인 정규식(`<tagname[\\s>/]`)으로 라인별 스캔.
    Returns: (missing_tags, hit_lines{tag: first_lineno})
    """
    import xml.etree.ElementTree as ET

    names = [_strip_xml_brackets(t) for t in required_tags]
    found: Dict[str, int] = {}

    # ── 1차 시도: ElementTree (느슨한 wrapper-root) ──────────────────────────
    try:
        wrapped = f"<__pipeline_root__>{text}</__pipeline_root__>"
        root = ET.fromstring(wrapped)
        present = {elem.tag for elem in root.iter()}
        # ElementTree는 라인 번호를 직접 노출하지 않으므로 라인 매핑은 정규식으로 보강
        for n in names:
            if n in present:
                found[n] = -1  # 존재만 확인, 라인은 미상
    except ET.ParseError:
        pass  # 정규식 폴백으로 진행

    # ── 2차: 안정적 정규식으로 라인 번호까지 수집 (1차에서 누락된 것만) ──────
    # `<tagname` 뒤에 공백/`>`/`/` 가 와야 매치 (False positive 최소화)
    # BUG-20260508-D541 MT-1: _strip_xml_comments() 공통 유틸로 comment 제거 (중복 로직 통합)
    clean_for_regex = _strip_xml_comments(text)
    for n in names:
        if n in found and found[n] != -1:
            continue
        pat = re.compile(rf"<\s*{re.escape(n)}\b[\s>/]")
        for lineno, line in enumerate(clean_for_regex.splitlines(), 1):
            if pat.search(line):
                found[n] = lineno
                break

    missing = [t for t in required_tags if _strip_xml_brackets(t) not in found]
    return missing, found


def _verify_required_xml_tags(
    report_path: str,
    required_tags: List[str],
    context_label: str,
    hard_fail: bool = True,
) -> bool:
    """보고서 파일에 required_tags(<tag> 형식)가 모두 존재하는지 검증.

    1차 ElementTree 파싱, 2차 정규식 fallback. 누락 시 hard_fail=True면 exit 1.
    """
    p = Path(report_path)
    if not p.exists():
        msg = f"[{context_label}] --report-file 경로 없음: {report_path}"
        if hard_fail:
            print(RED(f"\n{msg}\n"))
            sys.exit(1)
        print(YELLOW(f"\n[WARN] {msg}\n"))
        return False

    text = ""
    for enc in ("utf-8", "cp949", "latin-1"):
        try:
            text = p.read_text(encoding=enc)
            break
        except (UnicodeDecodeError, OSError):
            continue
    if not text:
        msg = f"[{context_label}] --report-file 읽기 실패: {report_path}"
        if hard_fail:
            print(RED(f"\n{msg}\n"))
            sys.exit(1)
        print(YELLOW(f"\n[WARN] {msg}\n"))
        return False

    missing, hit_lines = _scan_xml_tags(text, required_tags)
    if missing:
        found_summary = ", ".join(
            f"{t}@line{ln}" if ln != -1 else f"{t}@(parsed)"
            for t, ln in hit_lines.items()
        ) or "(없음)"
        msg = (
            f"[{context_label}] 필수 XML 블록 누락: {', '.join(missing)}\n"
            f"  파일: {report_path}\n"
            f"  발견된 태그: {found_summary}"
        )
        if hard_fail:
            print(RED(f"\n{msg}"))
            print(RED("에이전트 출력에 필수 블록이 모두 포함되어야 기록이 허용됩니다.\n"))
            sys.exit(1)
        print(YELLOW(f"\n[WARN] {msg}\n"))
        return False

    print(GREEN(f"[{context_label}] 필수 XML 블록 검증 통과: {', '.join(required_tags)}"))
    return True


def _read_text_fallback(path: Path) -> str:
    for enc in ("utf-8", "utf-8-sig", "cp949", "latin-1"):
        try:
            return path.read_text(encoding=enc)
        except (UnicodeDecodeError, OSError):
            continue
    raise OSError(f"failed to read text file: {path}")


def _extract_xml_element(text: str, tag: str) -> Optional[Any]:
    import xml.etree.ElementTree as ET

    clean = _strip_xml_comments(text)
    pattern = re.compile(rf"<\s*{re.escape(tag)}\b[^>]*>.*?</\s*{re.escape(tag)}\s*>", re.DOTALL)
    match = pattern.search(clean)
    if not match:
        return None
    try:
        return ET.fromstring(match.group(0))
    except ET.ParseError:
        return None


def _child_text(element: Any, path: str, default: str = "") -> str:
    found = element.find(path)
    if found is None or found.text is None:
        return default
    return found.text.strip()


def _collect_texts(element: Any, path: str) -> List[str]:
    values: List[str] = []
    for item in element.findall(path):
        text = (item.text or "").strip()
        if text:
            values.append(text)
    return values


def _parse_protocol_evolution_decision(report_file: Optional[str]) -> Dict[str, Any]:
    """Parse the Phase 8 decision that keeps protocol evolution out of the main pipeline.

    Phase 9 is not a pipeline.py phase. Architect must explicitly say whether the
    completed work exposed a protocol/agent/gate/doc defect that needs a separate
    IMP follow-up.
    """
    if not report_file:
        _die(
            "architect requires --report-file with <protocol_evolution_decision>. "
            "Phase 9 is not automatic; record required=false or required=true with a separate IMP recommendation."
        )

    path = Path(report_file)
    if not path.exists():
        _die(f"architect report file not found: {path}")
    if not path.is_file():
        _die(f"architect report path is not a file: {path}")

    text = _read_text_fallback(path)
    decision = _extract_xml_element(text, "protocol_evolution_decision")
    if decision is None:
        _die(
            "architect report is missing <protocol_evolution_decision>. "
            "Use required=false for normal completion, or required=true for a separate IMP follow-up."
        )

    raw_required = _child_text(decision, "required").strip().lower()
    if raw_required not in {"true", "false"}:
        _die("<protocol_evolution_decision><required> must be true or false")

    required = raw_required == "true"
    reason = _child_text(decision, "reason").strip()
    scope = _child_text(decision, "scope", "none").strip() or "none"
    recommended_type = _child_text(decision, "recommended_pipeline_type", "IMP").strip().upper() or "IMP"
    rca_mode_element = _extract_xml_element(text, "rca_mode")
    rca_mode = ""
    if rca_mode_element is not None and rca_mode_element.text:
        rca_mode = rca_mode_element.text.strip()

    if required and not reason:
        _die("protocol evolution required=true must include a non-empty <reason>")
    if required and scope.lower() == "none":
        _die("protocol evolution required=true must include a concrete <scope>")
    if recommended_type != "IMP":
        _die("<recommended_pipeline_type> must be IMP; protocol evolution is always a separate IMP pipeline")

    return {
        "required": required,
        "reason": reason or "none",
        "scope": scope,
        "recommended_pipeline_type": "IMP",
        "rca_mode": rca_mode,
        "report_file": str(path),
        "recorded_at": _now(),
    }


ATOMIC_SNAPSHOT_EXCLUDED_DIRS = {
    ".git",
    ".pytest_cache",
    "__pycache__",
    "node_modules",
    "build",
    "dist",
    "logs",
    "pipeline_contracts",
    "pipeline_history",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "venv",
    "env",
    "htmlcov",
}

ATOMIC_SNAPSHOT_EXCLUDED_FILES = {
    "agent_office_control.json",
    "pipeline_state.json",
    "scope_manifest.json",
    "step_plan.xml",
    "dev_handover.xml",
    "qa_report.xml",
    "architect_report.xml",
    "build_report.xml",
    "stop_signal.json",
    "test_results.jsonl",
    "test_results_v3.jsonl",
}


def _atomic_snapshot_include_path(path: Path) -> bool:
    try:
        rel = path.resolve().relative_to(BASE_DIR)
    except ValueError:
        return False
    parts = set(rel.parts)
    if parts & ATOMIC_SNAPSHOT_EXCLUDED_DIRS:
        return False
    name = rel.name
    if name in ATOMIC_SNAPSHOT_EXCLUDED_FILES:
        return False
    if name.startswith("pipeline_state") and name.endswith(".json"):
        return False
    if name.startswith("harness_output") and name.endswith(".xml"):
        return False
    if name.endswith(".pyc") or name.endswith(".tmp"):
        return False
    return True


def _atomic_project_snapshot() -> Dict[str, Any]:
    files: Dict[str, Dict[str, Any]] = {}
    skipped: List[str] = []
    for path in BASE_DIR.rglob("*"):
        if not path.is_file() or not _atomic_snapshot_include_path(path):
            continue
        rel = _normalize_rel_path(str(path))
        try:
            stat = path.stat()
            files[rel] = {
                "sha256": _sha256_file(path),
                "size": stat.st_size,
            }
        except OSError:
            skipped.append(rel)
    return {
        "created_at": _now(),
        "files": files,
        "skipped": skipped,
    }


def _atomic_changed_files(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    before = snapshot.get("files", {}) if isinstance(snapshot, dict) else {}
    if not isinstance(before, dict):
        before = {}
    current_snapshot = _atomic_project_snapshot()
    current = current_snapshot.get("files", {})
    if not isinstance(current, dict):
        current = {}

    added: List[str] = []
    modified: List[str] = []
    deleted: List[str] = []
    for rel in sorted(set(before) | set(current)):
        before_item = before.get(rel)
        current_item = current.get(rel)
        if before_item is None and current_item is not None:
            added.append(rel)
        elif before_item is not None and current_item is None:
            deleted.append(rel)
        elif before_item != current_item:
            modified.append(rel)

    return {
        "current_snapshot": current_snapshot,
        "added": added,
        "modified": modified,
        "deleted": deleted,
        "changed": sorted(set(added + modified + deleted)),
    }


def _validate_pm_step_plan_file(report_file: str, state: Dict[str, Any]) -> Dict[str, Any]:
    path = Path(report_file)
    if not path.exists():
        _die(f"[ATOMIC PLAN GATE] PM --report-file not found: {report_file}")
    try:
        text = _read_text_fallback(path)
    except OSError as exc:
        _die(f"[ATOMIC PLAN GATE] PM --report-file read failed: {exc}")

    forbidden_role_tags = [
        "dev_output",
        "handover",
        "impact_analysis",
        "scope_declaration",
        "qa_report",
        "security_audit",
        "build_report",
        "harness_report",
        "optimization_report",
    ]
    found_forbidden = [
        tag for tag in forbidden_role_tags
        if _extract_xml_element(text, tag) is not None
    ]
    if found_forbidden:
        _die(
            "[PM ROLE GATE] PM report contains non-PM output blocks: "
            + ", ".join(f"<{tag}>" for tag in found_forbidden)
            + ". PM must output planning XML only; Dev/QA/SEC/Build/Harness/Architect output requires separate agent phases."
        )

    step_plan = _extract_xml_element(text, "step_plan")
    decomp = _extract_xml_element(text, "decomposition_audit")
    if step_plan is None:
        _die("[ATOMIC PLAN GATE] <step_plan> XML block is required")
    if decomp is None:
        _die("[ATOMIC PLAN GATE] <decomposition_audit> XML block is required")

    pid = str(state.get("pipeline_id", ""))
    plan_pid = _child_text(step_plan, "pipeline_id")
    if plan_pid and plan_pid != pid:
        _die(f"[ATOMIC PLAN GATE] step_plan pipeline_id mismatch: expected {pid}, got {plan_pid}")

    audit_result = _child_text(decomp, "audit_result")
    micro_task_count_raw = _child_text(decomp, "micro_task_count", "0")
    grep_executions_raw = _child_text(decomp, "grep_executions", "0")
    try:
        expected_count = int(micro_task_count_raw)
    except ValueError:
        _die("[ATOMIC PLAN GATE] decomposition_audit.micro_task_count must be an integer")
    try:
        grep_executions = int(grep_executions_raw)
    except ValueError:
        _die("[ATOMIC PLAN GATE] decomposition_audit.grep_executions must be an integer")
    if expected_count < 1:
        _die("[ATOMIC PLAN GATE] at least one micro_task is required")
    if grep_executions < 1:
        _die("[ATOMIC PLAN GATE] grep_executions must be >= 1")
    if audit_result not in {"SPLIT_REQUIRED", "SINGLE_TASK_OK", "AMBIGUOUS"}:
        _die("[ATOMIC PLAN GATE] audit_result must be SPLIT_REQUIRED, SINGLE_TASK_OK, or AMBIGUOUS")

    if audit_result == "AMBIGUOUS":
        if _extract_xml_element(text, "judgment_calls_resolved") is None:
            _die("[ATOMIC PLAN GATE] AMBIGUOUS audit requires <judgment_calls_resolved>")

    micro_root = step_plan.find("micro_tasks")
    if micro_root is None:
        _die("[ATOMIC PLAN GATE] <micro_tasks> is required inside <step_plan>")
    micro_elements = micro_root.findall("micro_task")
    if len(micro_elements) != expected_count:
        _die(
            f"[ATOMIC PLAN GATE] micro_task_count mismatch: "
            f"audit={expected_count}, actual={len(micro_elements)}"
        )

    micro_tasks: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    for element in micro_elements:
        mt_id = str(element.get("id") or "").strip()
        if not mt_id:
            _die("[ATOMIC PLAN GATE] every <micro_task> requires id")
        if mt_id in seen_ids:
            _die(f"[ATOMIC PLAN GATE] duplicate micro_task id: {mt_id}")
        seen_ids.add(mt_id)

        affected_function = _child_text(element, "affected_function")
        change_summary = _child_text(element, "change_summary")
        grep = element.find("grep_evidence")
        if not affected_function:
            _die(f"[ATOMIC PLAN GATE] {mt_id} requires <affected_function>")
        if not change_summary:
            _die(f"[ATOMIC PLAN GATE] {mt_id} requires <change_summary>")
        if grep is None:
            _die(f"[ATOMIC PLAN GATE] {mt_id} requires <grep_evidence>")
        if _child_text(grep, "executed").lower() != "true":
            _die(f"[ATOMIC PLAN GATE] {mt_id} grep_evidence.executed must be true")
        if not _child_text(grep, "pattern"):
            _die(f"[ATOMIC PLAN GATE] {mt_id} requires grep_evidence.pattern")
        match_count_raw = _child_text(grep, "match_count", "0")
        try:
            int(match_count_raw)
        except ValueError:
            _die(f"[ATOMIC PLAN GATE] {mt_id} grep_evidence.match_count must be an integer")

        target_files = _collect_texts(element, "target_files/file")
        if not target_files:
            target_files = _collect_texts(step_plan, "target_files/file")
        if not target_files:
            _die(f"[ATOMIC PLAN GATE] {mt_id} requires <target_files><file>...</file></target_files>")

        micro_tasks.append({
            "id": mt_id,
            "affected_function": affected_function,
            "target_files": target_files,
            "change_summary": change_summary,
        })

    return {
        "report_file": str(path),
        "validated_at": _now(),
        "audit_result": audit_result,
        "micro_task_count": len(micro_tasks),
        "micro_tasks": micro_tasks,
        "project_snapshot": _atomic_project_snapshot(),
    }


def _read_phase_report_or_die(report_file: Optional[str], label: str) -> Tuple[Path, str]:
    if not report_file:
        _die(f"[{label}] --report-file is required")
    path = Path(report_file)
    if not path.exists():
        _die(f"[{label}] --report-file not found: {report_file}")
    if not path.is_file():
        _die(f"[{label}] --report-file is not a file: {report_file}")
    try:
        return path, _read_text_fallback(path)
    except OSError as exc:
        _die(f"[{label}] --report-file read failed: {exc}")


def _validate_dev_handover_file(report_file: Optional[str], state: Dict[str, Any]) -> Dict[str, Any]:
    path, text = _read_phase_report_or_die(report_file, "DEV HANDOVER GATE")
    handover = _extract_xml_element(text, "handover")
    impact = _extract_xml_element(text, "impact_analysis")
    scope = _extract_xml_element(text, "scope_declaration")
    if handover is None:
        _die("[DEV HANDOVER GATE] <handover> XML block is required")
    producer = _child_text(handover, "from")
    if producer and producer != "dev-agent":
        _die(f"[DEV HANDOVER GATE] <handover><from> must be dev-agent, got {producer!r}")
    if impact is None:
        _die("[DEV HANDOVER GATE] <impact_analysis> XML block is required")
    if scope is None:
        _die("[DEV HANDOVER GATE] <scope_declaration> XML block is required")

    forbidden_tags = ["qa_report", "security_audit", "build_report", "harness_report", "optimization_report"]
    found_forbidden = [tag for tag in forbidden_tags if _extract_xml_element(text, tag) is not None]
    if found_forbidden:
        _die(
            "[DEV ROLE GATE] Dev report contains non-Dev output blocks: "
            + ", ".join(f"<{tag}>" for tag in found_forbidden)
        )
    return {
        "report_file": str(path),
        "validated_at": _now(),
        "producer": producer or "dev-agent",
    }


def _validate_qa_report_file(
    report_file: Optional[str],
    *,
    result: str,
    numeric_score: int,
) -> Dict[str, Any]:
    path, text = _read_phase_report_or_die(report_file, "QA REPORT GATE")
    _verify_required_xml_tags(
        str(path),
        required_tags=["<qa_report>", "<numeric_score>", "<verdict>", "<micro_task_boundary>"],
        context_label="QA REPORT GATE",
        hard_fail=True,
    )
    score_element = _extract_xml_element(text, "numeric_score")
    verdict_element = _extract_xml_element(text, "verdict")
    score_text = (score_element.text or "").strip() if score_element is not None and score_element.text else ""
    verdict_text = (verdict_element.text or "").strip().upper() if verdict_element is not None and verdict_element.text else ""
    try:
        report_score = int(score_text)
    except ValueError:
        _die("[QA REPORT GATE] <numeric_score> must be an integer")
    if report_score != numeric_score:
        _die(f"[QA REPORT GATE] CLI --numeric-score {numeric_score} does not match report <numeric_score> {report_score}")
    if verdict_text != result:
        _die(f"[QA REPORT GATE] CLI --result {result} does not match report <verdict> {verdict_text}")
    return {
        "report_file": str(path),
        "validated_at": _now(),
        "numeric_score": report_score,
        "verdict": verdict_text,
    }


def _normalize_rel_path(raw: str) -> str:
    path = Path(str(raw).strip())
    if path.is_absolute():
        try:
            return str(path.resolve().relative_to(BASE_DIR)).replace("\\", "/")
        except ValueError:
            return str(path.resolve()).replace("\\", "/")
    normalized = str(path).replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _validate_dev_scope_manifest(
    manifest_file: str,
    state: Dict[str, Any],
    evidence: Optional[str],
) -> Dict[str, Any]:
    path = Path(manifest_file)
    if not path.exists():
        _die(f"[ATOMIC SCOPE GATE] --scope-manifest not found: {manifest_file}")
    manifest = _load_json_file(path)
    pid = str(state.get("pipeline_id", ""))
    if manifest.get("pipeline_id") and manifest.get("pipeline_id") != pid:
        _die(f"[ATOMIC SCOPE GATE] scope_manifest pipeline_id mismatch: expected {pid}, got {manifest.get('pipeline_id')}")

    atomic_plan = state.get("atomic_plan")
    if not isinstance(atomic_plan, dict) or not atomic_plan.get("micro_tasks"):
        _die("[ATOMIC SCOPE GATE] PM atomic_plan missing; run PM done with --report-file first")
    project_snapshot = atomic_plan.get("project_snapshot")
    if not isinstance(project_snapshot, dict):
        _die("[ATOMIC SCOPE GATE] PM project snapshot missing; rerun PM done with --report-file first")
    plan_tasks = {
        str(item.get("id")): item
        for item in atomic_plan.get("micro_tasks", [])
        if isinstance(item, dict) and item.get("id")
    }
    manifest_tasks = manifest.get("micro_tasks", [])
    if not isinstance(manifest_tasks, list) or not manifest_tasks:
        _die("[ATOMIC SCOPE GATE] scope_manifest.micro_tasks must be a non-empty list")

    manifest_ids = {
        str(item.get("id"))
        for item in manifest_tasks
        if isinstance(item, dict) and item.get("id")
    }
    missing_ids = sorted(set(plan_tasks) - manifest_ids)
    extra_ids = sorted(manifest_ids - set(plan_tasks))
    if missing_ids:
        _die(f"[ATOMIC SCOPE GATE] scope_manifest missing micro_task ids: {missing_ids}")
    if extra_ids:
        _die(f"[ATOMIC SCOPE GATE] scope_manifest has unknown micro_task ids: {extra_ids}")

    allowed_files: set[str] = set()
    allowed_functions: set[str] = set()
    for task in plan_tasks.values():
        allowed_files.update(_normalize_rel_path(item) for item in task.get("target_files", []))
        allowed_functions.add(str(task.get("affected_function")))

    manifest_files: set[str] = set()
    manifest_functions: set[str] = set()
    for item in manifest_tasks:
        if not isinstance(item, dict):
            _die("[ATOMIC SCOPE GATE] every scope_manifest micro_task must be an object")
        mt_id = str(item.get("id") or "")
        if not mt_id:
            _die("[ATOMIC SCOPE GATE] every scope_manifest micro_task requires id")
        files = item.get("files", [])
        funcs = item.get("affected_functions", [])
        if not isinstance(files, list) or not files:
            _die(f"[ATOMIC SCOPE GATE] {mt_id} requires non-empty files list")
        if not isinstance(funcs, list) or not funcs:
            _die(f"[ATOMIC SCOPE GATE] {mt_id} requires non-empty affected_functions list")
        normalized_funcs = [str(func).strip() for func in funcs if str(func).strip()]
        if not normalized_funcs:
            _die(f"[ATOMIC SCOPE GATE] {mt_id} requires non-empty affected_functions values")
        manifest_files.update(_normalize_rel_path(file) for file in files)
        manifest_functions.update(normalized_funcs)

    evidence_files = {
        _normalize_rel_path(item)
        for item in (evidence or "").split(",")
        if item.strip()
    }
    extra_files = sorted((manifest_files | evidence_files) - allowed_files)
    if extra_files:
        _die(f"[ATOMIC SCOPE GATE] files outside PM micro_task target_files: {extra_files}")
    missing_evidence = sorted(manifest_files - evidence_files)
    if missing_evidence:
        _die(f"[ATOMIC SCOPE GATE] manifest files missing from --files evidence: {missing_evidence}")
    extra_functions = sorted(manifest_functions - allowed_functions)
    if extra_functions:
        _die(f"[ATOMIC SCOPE GATE] affected_functions outside PM plan: {extra_functions}")

    actual_diff = _atomic_changed_files(project_snapshot)
    actual_changed = set(actual_diff.get("changed", []))
    actual_outside_scope = sorted(actual_changed - allowed_files)
    if actual_outside_scope:
        _die(f"[ATOMIC SCOPE GATE] actual file changes outside PM micro_task target_files: {actual_outside_scope}")
    actual_missing_manifest = sorted(actual_changed - manifest_files)
    if actual_missing_manifest:
        _die(f"[ATOMIC SCOPE GATE] actual file changes missing from scope_manifest files: {actual_missing_manifest}")
    actual_missing_evidence = sorted(actual_changed - evidence_files)
    if actual_missing_evidence:
        _die(f"[ATOMIC SCOPE GATE] actual file changes missing from --files evidence: {actual_missing_evidence}")

    return {
        "manifest_file": str(path),
        "validated_at": _now(),
        "micro_task_ids": sorted(manifest_ids),
        "files": sorted(manifest_files),
        "affected_functions": sorted(manifest_functions),
        "actual_diff": {
            "added": actual_diff.get("added", []),
            "modified": actual_diff.get("modified", []),
            "deleted": actual_diff.get("deleted", []),
            "changed": actual_diff.get("changed", []),
        },
    }


def _verify_build_report_xml(report: str) -> tuple:
    """Build report XML 검증. XML comment 우회 차단, regex fallback 없음.

    BUG-20260507-C2E2 MT-1: ElementTree 전용 검증.
    XML comment로 필수 섹션을 숨겨 통과하는 우회 패턴을 완전히 차단합니다.

    Returns:
        (ok: bool, message: str)
    """
    import xml.etree.ElementTree as ET

    # 1. XML comment 제거 (comment 내용은 파싱 대상에서 완전 배제)
    clean = re.sub(r'<!--.*?-->', '', report, flags=re.DOTALL)

    # 2. <build_report> 블록 추출
    m = re.search(r'<build_report[^>]*>(.*?)</build_report>', clean, re.DOTALL)
    if not m:
        return False, "build_report 블록 없음 (XML comment로 감싸진 블록은 유효하지 않음)"
    inner = m.group(0)

    # 3. ElementTree 파싱 (regex fallback 없음 — 실제 XML 구조만 허용)
    try:
        root = ET.fromstring(inner)
    except ET.ParseError as e:
        return False, f"XML 파싱 오류: {e}"

    # 4. 필수 section 태그 확인 (comment 제거 후 실제 태그만 검사)
    # 각 섹션은 (primary, alias) 튜플로 정의 — 둘 중 하나만 있으면 통과
    required_sections_with_alias = [
        ('section_1_command', None),
        ('section_2_environment', 'section_2_spec'),   # build-agent: section_2_spec 허용
        ('section_3_output', None),
        ('section_4_verification', None),
        ('section_5_artifacts', 'section_5_optimization'),  # build-agent: section_5_optimization 허용
        ('section_6_qa_mapping', None),
    ]
    for sec, alias in required_sections_with_alias:
        found_primary = root.find(sec) is not None
        found_alias = (alias is not None and root.find(alias) is not None)
        if not found_primary and not found_alias:
            alias_note = f" (alias '{alias}'도 없음)" if alias else ""
            return False, f"필수 섹션 없음: '{sec}'{alias_note}"

    # 5. <status>BUILD SUCCESS</status> 확인 (정확 일치 비교 — "BUILD SUCCESS / BUILD FAILED" 같은 복합값 차단)
    status_el = root.find('.//status')
    if status_el is None:
        return False, "<status> 태그 없음"
    status_text = (status_el.text or '').strip()
    if status_text.strip().upper() != 'BUILD SUCCESS':
        return False, f"<status>BUILD SUCCESS</status> 없음 (발견: '{status_text}')"

    return True, "OK"


class TournamentMeta(TypedDict, total=False):
    """토너먼트 상태 메타데이터."""

    active: bool
    pipeline_id: str
    branches: list[str]
    branch_states: dict[str, str]  # "in_progress" | "build_failed" | "harness_passed" | "harness_failed"
    winner: Optional[str]
    started_at: str
    finalized_at: Optional[str]


# ── Constants ────────────────────────────────────────────────────────────────

VERSION = "1.2.0"
OPENAI_ADVISORY_MODEL = "gpt-5.5"
BASE_DIR   = Path(__file__).resolve().parent
STATE_FILE = BASE_DIR / "pipeline_state.json"
HISTORY_DIR = BASE_DIR / "pipeline_history"
TEST_RESULTS_FILE = BASE_DIR / "test_results.jsonl"
CONTRACTS_DIR = BASE_DIR / "pipeline_contracts"

PHASE_ORDER = ["pm", "dev", "qa", "sec", "build", "harness", "architect"]

PHASE_LABELS = {
    "pm":        "Phase 1 - PM (Planning)",
    "dev":       "Phase 2 - Dev (Implementation)",
    "qa":        "Phase 4 - QA (Verification)",
    "sec":       "Phase 5 - Security (Audit)",
    "build":     "Phase 6 - Build (Packaging)",
    "harness":   "Phase 7 - Harness (Benchmark)",
    "architect": "Phase 8 - Architect (RCA)",
}


def _dedupe_test_results_jsonl() -> Optional[str]:
    """Keep the latest JSONL result per id after Harness writes its record."""
    if not TEST_RESULTS_FILE.exists():
        return None
    try:
        raw_lines = [
            line for line in TEST_RESULTS_FILE.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        seen: Dict[str, Dict[str, Any]] = {}
        for line in raw_lines:
            item = json.loads(line)
            if not isinstance(item, dict):
                return "skipped: non-object JSONL record found"
            seen[str(item.get("id", ""))] = item
        cleaned = list(seen.values())
        if len(cleaned) == len(raw_lines):
            return f"{len(raw_lines)} rows, no duplicates"
        tmp = TEST_RESULTS_FILE.with_suffix(".jsonl.tmp")
        tmp.write_text(
            "\n".join(json.dumps(item, ensure_ascii=False) for item in cleaned) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, TEST_RESULTS_FILE)
        return f"{len(raw_lines)} -> {len(cleaned)} rows, removed {len(raw_lines) - len(cleaned)} duplicates"
    except Exception as exc:
        return f"skipped: {exc}"


# gate_rules[phase] = list of (required_phase, required_status_or_list)
GATE_RULES: Dict[str, List[Tuple[str, Any]]] = {
    "pm":        [],
    "dev":       [("pm",    "DONE")],
    "qa":        [("dev",   "DONE")],
    "sec":       [("qa",    "PASS")],
    "build":     [("qa",    "PASS"),
                  ("sec",   ["PASS", "SKIP"])],
    "harness":   [("build", "DONE")],
    "architect": [("harness", ["PASS", "FAIL"])],
}

# ANSI colours (disabled on Windows if not supported)
_USE_COLOR = (sys.stdout.isatty() or bool(os.environ.get("FORCE_COLOR")))

def _c(text: str, code: str) -> str:
    if _USE_COLOR:
        return f"\033[{code}m{text}\033[0m"
    return text

RED    = lambda t: _c(t, "31")
GREEN  = lambda t: _c(t, "32")
YELLOW = lambda t: _c(t, "33")
CYAN   = lambda t: _c(t, "36")
BOLD   = lambda t: _c(t, "1")
DIM    = lambda t: _c(t, "2")


# ── Branch / Tournament state helpers ───────────────────────────────────────

def _state_path(branch: Optional[str] = None) -> Path:
    """Return state file path. branch=None → legacy single-file mode."""
    if branch is None:
        return STATE_FILE
    state = _load_state_for(None)
    pid = state.get("pipeline_id", "UNKNOWN")
    short = pid[-4:] if len(pid) >= 4 else pid
    return BASE_DIR / f"pipeline_state_{short}-{branch}.json"


def _load_state_for(branch: Optional[str] = None) -> dict:
    """브랜치별 state 파일 로드. branch=None 이면 메인 STATE_FILE 로드."""
    if branch is None:
        path = STATE_FILE
    else:
        # branch 경우: 이미 계산된 경로를 직접 산출 (재귀 없이)
        # 메인 state 에서 pipeline_id 를 읽어 short suffix 계산
        main_state: dict = {}
        if STATE_FILE.exists():
            for enc in ("utf-8", "cp949", "latin-1"):
                try:
                    main_state = json.loads(STATE_FILE.read_text(encoding=enc))
                    break
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
        pid = main_state.get("pipeline_id", "UNKNOWN")
        short = pid[-4:] if len(pid) >= 4 else pid
        path = BASE_DIR / f"pipeline_state_{short}-{branch}.json"

    if not path.exists():
        return {}
    for enc in ("utf-8", "cp949", "latin-1"):
        try:
            return json.loads(path.read_text(encoding=enc))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    raise SystemExit(3)


def _save_state_for(state: dict, branch: Optional[str] = None) -> None:
    """브랜치별 state 파일 원자적 저장. branch=None 이면 메인 STATE_FILE 저장."""
    if branch is None:
        path = STATE_FILE
    else:
        pid = state.get("parent_pipeline_id") or state.get("pipeline_id", "UNKNOWN")
        short = pid[-4:] if len(pid) >= 4 else pid
        path = BASE_DIR / f"pipeline_state_{short}-{branch}.json"

    # updated_at 자동 갱신 (기존 _save() 와 동일한 동작)
    state["updated_at"] = _now()

    tmp_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            delete=False, suffix=".tmp"
        ) as tmp:
            json.dump(state, tmp, ensure_ascii=False, indent=2)
            tmp_path = Path(tmp.name)
        os.replace(str(tmp_path), str(path))
    except Exception:
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise


def _validate_branch(branch: str) -> None:
    """브랜치 ID 형식 검증 (대문자 알파벳 1글자만 허용)."""
    if branch is None:
        raise SystemExit(2)
    if not isinstance(branch, str):
        raise SystemExit(2)
    if not re.fullmatch(r"[A-Z]", branch):
        print(f"[ERROR] branch는 대문자 알파벳 1글자만 허용합니다: '{branch}'")
        raise SystemExit(2)


# ── State helpers ────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _generate_id(pipeline_type: str) -> str:
    date = datetime.now(timezone.utc).strftime("%Y%m%d")
    suffix = secrets.token_hex(2).upper()
    return f"{pipeline_type}-{date}-{suffix}"


def _empty_phase(name: str) -> Dict[str, Any]:
    return {
        "status":         "PENDING",
        "completed_at":   None,
        "evidence":       None,
        "notes":          [],
        # ── v1.2 structured fields ──────────────────────────────────────────
        "report_file":    None,   # 에이전트 출력 보고서 경로 (있는 경우)
        "agent_id":       None,   # 처리한 에이전트 식별자
        "snapshot_path":  None,   # pipeline_history/{pid}_phase_{name}.json 경로
    }


def _empty_external_gate() -> Dict[str, Any]:
    return {
        "status": "PENDING",
        "completed_at": None,
        "evidence": None,
        "report_file": None,
        "notes": [],
    }


def _new_external_gates(enabled: bool = False) -> Dict[str, Any]:
    return {
        "enabled": enabled,
        "mode": "three_gate",
        "technical": _empty_external_gate(),
        "oracle": _empty_external_gate(),
        "acceptance": _empty_external_gate(),
    }


def _save_phase_snapshot(state: Dict[str, Any], phase: str) -> Optional[str]:
    """현재 state를 pipeline_history/{pid}_phase_{phase}.json에 스냅샷.

    동일 phase 재시도 시 .N 접미사로 회전(_phase_dev.json, _phase_dev.1.json, ...).
    실패는 무시 (스냅샷이 핵심 게이트를 막아서는 안 됨).
    """
    try:
        HISTORY_DIR.mkdir(exist_ok=True)
        pid = state.get("pipeline_id", "UNKNOWN")
        base = HISTORY_DIR / f"{pid}_phase_{phase}.json"
        path = base
        idx = 1
        while path.exists():
            path = HISTORY_DIR / f"{pid}_phase_{phase}.{idx}.json"
            idx += 1
        path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return str(path)
    except OSError as e:
        print(DIM(f"  [SNAPSHOT WARN] {phase} 스냅샷 실패: {e}"))
        return None


def _new_state(pipeline_id: str, pipeline_type: str, description: str) -> Dict[str, Any]:
    return {
        "version":       VERSION,
        "pipeline_id":   pipeline_id,
        "type":          pipeline_type,
        "description":   description,
        "created_at":    _now(),
        "updated_at":    _now(),
        "current_phase": "pm",
        "blocked":       False,
        "blocked_reason": None,
        "phases": {p: _empty_phase(p) for p in PHASE_ORDER},
        "event_log":     [],
        # v2.10 Auto-Compact 지원: 파이프라인 종료 상태 표시
        # null = in progress, "COMPLETE" = Phase 8 architect completion, "FAILED" = repeated harness FAIL,
        # "TERMINATED" = 사용자 명시적 중단
        "terminal_state": None,
        "harness_fail_count": 0,
        "external_gates": _new_external_gates(enabled=False),
        "protocol_evolution_decision": None,
    }


def _ensure_external_gates(state: Dict[str, Any]) -> Dict[str, Any]:
    gates = state.get("external_gates")
    if not isinstance(gates, dict):
        return _new_external_gates(enabled=False)

    normalized = _new_external_gates(enabled=bool(gates.get("enabled")))
    normalized["mode"] = str(gates.get("mode") or "three_gate")
    for gate_name in ("technical", "oracle", "acceptance"):
        existing = gates.get(gate_name)
        if isinstance(existing, dict):
            merged = _empty_external_gate()
            merged.update(existing)
            normalized[gate_name] = merged
    return normalized


def _ensure_v210_fields(state: Dict[str, Any]) -> Dict[str, Any]:
    """v2.10 신규 필드를 구버전 state에 자동 마이그레이션 (idempotent)."""
    if "terminal_state" not in state:
        state["terminal_state"] = None
    if "harness_fail_count" not in state:
        state["harness_fail_count"] = 0
    if "protocol_evolution_decision" not in state:
        state["protocol_evolution_decision"] = None
    state["external_gates"] = _ensure_external_gates(state)
    return state


def _load() -> Optional[Dict[str, Any]]:
    if not STATE_FILE.exists():
        return None
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        # v2.10 backward compatibility: 구버전 state 파일 자동 마이그레이션
        return _ensure_v210_fields(state)
    except (json.JSONDecodeError, OSError) as e:
        _die(f"pipeline_state.json 읽기 실패: {e}")


def _save(state: Dict[str, Any]) -> None:
    """Atomically write master state file (tempfile → os.replace)."""
    state["updated_at"] = _now()
    tmp_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=STATE_FILE.parent,
            delete=False, suffix=".tmp"
        ) as tmp:
            json.dump(state, tmp, ensure_ascii=False, indent=2)
            tmp_path = Path(tmp.name)
        os.replace(str(tmp_path), str(STATE_FILE))
    except Exception:
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise


def _log_event(state: Dict[str, Any], message: str) -> None:
    state["event_log"].append({"ts": _now(), "msg": message})


def _die(message: str, exit_code: int = 1) -> None:
    print(RED(f"\n[PIPELINE ERROR] {message}"), file=sys.stderr)
    sys.exit(exit_code)


# Aliases used by tournament commands (branch-aware variants use _load_state / _save_state)
_load_state = _load
_save_state = _save


def _openai_api_key() -> Tuple[Optional[str], str]:
    """Return the OpenAI API key without printing or persisting it.

    PowerShell's [Environment]::SetEnvironmentVariable(..., "User") updates the
    Windows user environment, but already-open shells do not inherit that value.
    Reading HKCU\\Environment lets pipeline.py work immediately after the user
    stores the key, without requiring a new terminal.
    """
    process_value = os.environ.get("OPENAI_API_KEY")
    if process_value:
        return process_value, "process"
    if os.name == "nt":
        try:
            import winreg  # type: ignore[import-not-found]

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                value, _ = winreg.QueryValueEx(key, "OPENAI_API_KEY")
            if isinstance(value, str) and value.strip():
                return value.strip(), "user"
        except OSError:
            pass
    return None, "missing"


def _openai_advisory_enabled() -> bool:
    """API advisory is opt-in because ChatGPT subscriptions do not include API quota."""
    return os.environ.get("ENABLE_GPT_ADVISORY") == "1"


def _require_state() -> Dict[str, Any]:
    state = _load()
    if state is None:
        _die("활성 파이프라인 없음. 먼저 `python pipeline.py new` 를 실행하세요.")
    return state  # type: ignore[return-value]


def _load_branch_state(args: argparse.Namespace, die_msg: str = "state 파일이 없습니다.") -> Dict[str, Any]:
    """branch 인자가 있으면 브랜치 state, 없으면 메인 state 반환."""
    branch: Optional[str] = getattr(args, "branch", None)
    if branch is not None:
        state = _load_state_for(branch)
        if not state:
            _die(f"Branch '{branch}' {die_msg}")
        return state
    return _require_state()


def _record_snapshot(state: Dict[str, Any], phase: str, branch: Optional[str]) -> None:
    """branch 없을 때만 phase 스냅샷 저장 후 snapshot_path 기록."""
    if branch is None:
        snap = _save_phase_snapshot(state, phase)
        if snap:
            state["phases"][phase]["snapshot_path"] = snap


def _contract_paths(pipeline_id: str) -> Dict[str, Path]:
    if not pipeline_id or ".." in pipeline_id or "/" in pipeline_id or "\\" in pipeline_id:
        _die(f"invalid pipeline_id for contract path: {pipeline_id!r}", exit_code=2)
    root = CONTRACTS_DIR / pipeline_id
    return {
        "root": root,
        "contract": root / "task_contract.json",
        "test_set": root / "test_set.json",
        "summary": root / "acceptance_summary.md",
        "result": root / "acceptance_result.json",
        "oracle_manifest": root / "oracle_manifest.json",
        "contract_audit": root / "contract_audit.json",
        "gates_root": root / "gates",
        "technical_result": root / "gates" / "technical_result.json",
        "oracle_result": root / "gates" / "oracle_result.json",
        "user_validation": root / "gates" / "user_validation.json",
        "advisory_root": root / "advisory",
        "advisory_resolutions": root / "advisory" / "resolutions.json",
    }


def _contract_init_command(pid: str) -> str:
    return f"python pipeline.py contract init --pipeline-id {pid}"


def _require_contract_initialized(paths: Dict[str, Path], pid: str, action: str) -> None:
    missing = [path for path in (paths["contract"], paths["test_set"]) if not path.exists()]
    if not missing:
        return
    missing_lines = "\n".join(f"  - {path}" for path in missing)
    _die(
        "\n[CONTRACT NOT INITIALIZED]\n"
        f"  contract action `{action}` requires initialized contract files for pipeline `{pid}`.\n"
        f"  Run first: {_contract_init_command(pid)}\n"
        "  Missing files:\n"
        f"{missing_lines}"
    )


def _load_contract_bundle(
    load_json_func: Any,
    paths: Dict[str, Path],
    pid: str,
    action: str,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    _require_contract_initialized(paths, pid, action)
    try:
        contract = load_json_func(paths["contract"])
        test_set = load_json_func(paths["test_set"])
    except ValueError as exc:
        _die(
            "\n[CONTRACT FILE ERROR]\n"
            f"  Could not read contract files for pipeline `{pid}`: {exc}\n"
            f"  Repair the JSON files or re-run: {_contract_init_command(pid)} --force"
        )
    return contract, test_set


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            delete=False,
            suffix=".tmp",
        ) as tmp:
            json.dump(data, tmp, ensure_ascii=False, indent=2)
            tmp.write("\n")
            tmp_path = Path(tmp.name)
        os.replace(str(tmp_path), str(path))
    except Exception:
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise


def _load_json_file(path: Path, default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if not path.exists():
        return dict(default or {})
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        _die(f"invalid JSON file {path}: {exc}")
    if not isinstance(data, dict):
        _die(f"JSON root must be an object: {path}")
    return data


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(BASE_DIR))
    except ValueError:
        return str(path.resolve())


def _state_contract_enabled(state: Dict[str, Any]) -> bool:
    info = state.get("contract_v2")
    return isinstance(info, dict) and bool(info.get("enabled"))


def _state_contract_frozen(state: Dict[str, Any]) -> bool:
    info = state.get("contract_v2")
    return isinstance(info, dict) and bool(info.get("frozen"))


def _resolve_pipeline_context(args: argparse.Namespace) -> Tuple[str, str, str, Optional[Dict[str, Any]]]:
    state = _load()
    pid = getattr(args, "pipeline_id", None) or (state or {}).get("pipeline_id")
    if not pid:
        _die("--pipeline-id is required when no active pipeline exists", exit_code=2)
    desc = getattr(args, "desc", None) or (state or {}).get("description") or ""
    ptype = getattr(args, "type", None) or (state or {}).get("type") or "FEAT"
    return str(pid), str(desc), str(ptype), state


# ── Gate validation ──────────────────────────────────────────────────────────

def check_gate(state: Dict[str, Any], phase: str) -> Tuple[bool, str]:
    """phase에 진입할 수 있는지 검증합니다.

    BUG-20260507-C2E2 MT-2: current_phase 불변식 추가.
    요청 phase가 state의 current_phase와 일치하지 않으면 BLOCKED 반환.

    Returns:
        (ok, reason) -ok=True면 진입 가능, False면 reason에 차단 사유.
    """
    if phase not in GATE_RULES:
        return False, f"알 수 없는 phase: {phase}"

    terminal = state.get("terminal_state")
    if terminal in ("COMPLETE", "FAILED", "TERMINATED"):
        return False, f"파이프라인이 이미 종료되었습니다 (terminal_state={terminal}). 새 파이프라인을 시작하세요: python pipeline.py new"
    if state.get("blocked"):
        return False, f"파이프라인 차단 중: {state.get('blocked_reason', '알 수 없음')}"

    # BUG-20260508-78D2 MT-1: current_phase 불변식 강화 — PM 면제 제거 + None 방어
    # state["current_phase"]는 단축형("pm", "dev", "qa", ...) 으로 저장됨.
    # current_phase 누락(None) 시 즉시 BLOCKED — 손상된 상태 파일 방어.
    # _new_state()는 항상 current_phase="pm"으로 초기화하므로 신규 파이프라인에는 영향 없음.
    _label_map = {
        "pm": "Phase 1 - PM (Planning)",
        "dev": "Phase 2 - Dev (Implementation)",
        "qa": "Phase 4 - QA (Verification)",
        "sec": "Phase 5 - Security (Audit)",
        "build": "Phase 6 - Build (Packaging)",
        "harness": "Phase 7 - Harness (Benchmark)",
        "architect": "Phase 8 - Architect (RCA)",
    }
    current_phase_raw = state.get("current_phase")
    if current_phase_raw is None:
        return (
            False,
            "current_phase 필드 누락 — 상태 파일이 손상되었거나 파이프라인이 초기화되지 않았습니다. "
            "python pipeline.py status 확인."
        )
    # current_phase가 단축형인 경우 그대로 비교. 레이블 형식이면 역매핑.
    _label_to_short = {v: k for k, v in _label_map.items()}
    current_phase_short = _label_to_short.get(current_phase_raw, current_phase_raw)
    if current_phase_short != phase:
        return (
            False,
            f"current_phase 불일치 — 현재={current_phase_raw!r} (={current_phase_short}), 요청={phase}. "
            f"순서대로 진행하세요: python pipeline.py status"
        )

    if phase == "dev" and _state_contract_enabled(state) and not _state_contract_frozen(state):
        return (
            False,
            "contract_v2 is enabled but not frozen. Run `python pipeline.py contract ready` "
            "and `python pipeline.py contract freeze` before Dev.",
        )

    for required_phase, required_status in GATE_RULES[phase]:
        actual = state["phases"][required_phase]["status"]
        if isinstance(required_status, list):
            if actual not in required_status:
                options = " 또는 ".join(required_status)
                return (
                    False,
                    f"{PHASE_LABELS[required_phase]} 상태가 [{options}] 이어야 합니다. "
                    f"현재: [{actual}]",
                )
        else:
            if actual != required_status:
                return (
                    False,
                    f"{PHASE_LABELS[required_phase]} 상태가 [{required_status}] 이어야 합니다. "
                    f"현재: [{actual}]",
                )
    return True, ""


# ── Agent Office Dashboard auto-start helpers ────────────────────────────────

DASHBOARD_HOST = "127.0.0.1"
DASHBOARD_PORT = 8765
DASHBOARD_URL = f"http://{DASHBOARD_HOST}:{DASHBOARD_PORT}/"
DASHBOARD_HEALTH = f"{DASHBOARD_URL}api/health"
DASHBOARD_PROJECT_ROOT = Path(__file__).resolve().parent
DASHBOARD_LOG = DASHBOARD_PROJECT_ROOT / "logs" / "dashboard.log"


def _dashboard_is_alive(timeout: float = 0.6) -> bool:
    """대시보드 서버가 응답하는지 확인. 호출 실패 시 False."""
    try:
        with urllib.request.urlopen(DASHBOARD_HEALTH, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False


def _dashboard_port_in_use() -> bool:
    """포트가 점유 상태이면 True (다른 프로세스가 listen 중)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.settimeout(0.4)
        return sock.connect_ex((DASHBOARD_HOST, DASHBOARD_PORT)) == 0
    except OSError:
        return False
    finally:
        try:
            sock.close()
        except OSError:
            pass


def _start_dashboard_background() -> Optional[int]:
    """대시보드 서버를 백그라운드로 기동. 이미 실행 중이면 None 반환.

    반환값: subprocess PID 또는 None.
    """
    if _dashboard_is_alive():
        return None
    if _dashboard_port_in_use():
        # 다른 프로세스가 점유 중 — 우리가 띄운 것이 아닐 가능성. 건드리지 않음.
        return None

    server_module = DASHBOARD_PROJECT_ROOT / "webapp" / "server.py"
    if not server_module.exists():
        return None

    try:
        DASHBOARD_LOG.parent.mkdir(exist_ok=True)
    except OSError:
        pass

    cmd = [
        sys.executable, "-m", "uvicorn",
        "webapp.server:app",
        "--host", DASHBOARD_HOST,
        "--port", str(DASHBOARD_PORT),
        "--log-level", "warning",
    ]

    # Windows: DETACHED_PROCESS + CREATE_NEW_PROCESS_GROUP, no console window.
    creationflags = 0
    startupinfo = None
    if os.name == "nt":
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        CREATE_NO_WINDOW = 0x08000000
        creationflags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

    try:
        log_handle = open(DASHBOARD_LOG, "ab", buffering=0)
    except OSError:
        log_handle = subprocess.DEVNULL  # type: ignore[assignment]

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(DASHBOARD_PROJECT_ROOT),
            stdout=log_handle,
            stderr=log_handle,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
            startupinfo=startupinfo,
            close_fds=(os.name != "nt"),
        )
        return proc.pid
    except (OSError, FileNotFoundError) as exc:
        print(DIM(f"  대시보드 자동 시작 실패: {exc}"))
        return None
    except Exception as exc:  # pragma: no cover - defensive
        print(DIM(f"  대시보드 자동 시작 예외: {exc}"))
        return None


def _ensure_dashboard_running(open_browser: bool = True) -> None:
    """대시보드 서버가 살아있도록 보장하고, 필요 시 브라우저를 연다."""
    already_alive = _dashboard_is_alive()
    pid = None
    if not already_alive:
        pid = _start_dashboard_background()
        # 서버 부팅 대기 (최대 ~3초)
        if pid is not None:
            import time
            for _ in range(15):
                time.sleep(0.2)
                if _dashboard_is_alive():
                    break

    if _dashboard_is_alive():
        if pid is not None:
            print(DIM(f"  에이전트 오피스 대시보드 시작됨 (PID {pid}) → {DASHBOARD_URL}"))
        else:
            print(DIM(f"  에이전트 오피스 대시보드 이미 실행 중 → {DASHBOARD_URL}"))
        # 브라우저 자동 오픈 제거 (IMP-20260505-C0FC) — open_browser 파라미터는 시그니처 호환을 위해 유지하되 동작은 no-op.
    else:
        print(DIM(f"  대시보드 자동 시작 보류 — 수동으로 'VS Code 태스크: 에이전트 대시보드 시작' 실행 가능"))


# ── Commands ─────────────────────────────────────────────────────────────────

def cmd_new(args: argparse.Namespace) -> None:
    """새 파이프라인 시작."""
    if STATE_FILE.exists():
        existing = _load()
        if existing:
            pid = existing.get("pipeline_id", "?")
            # Archive existing
            HISTORY_DIR.mkdir(exist_ok=True)
            archive = HISTORY_DIR / f"{pid}_{existing.get('updated_at', 'old').replace(':', '-')}.json"
            archive.write_text(
                json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(DIM(f"이전 파이프라인 [{pid}] → {archive.name} 보관"))

    pipeline_type = args.type.upper()
    pipeline_id   = _generate_id(pipeline_type)
    state = _new_state(pipeline_id, pipeline_type, args.desc)
    _log_event(state, f"파이프라인 생성: {pipeline_id}")
    _save(state)

    print()
    print(BOLD(GREEN(f"  파이프라인 생성 완료")))
    print(f"  ID:   {CYAN(pipeline_id)}")
    print(f"  유형: {pipeline_type}")
    print(f"  설명: {args.desc}")
    print()

    # Auto-start the Agent Office Dashboard so the user can see live activity.
    skip_dashboard = bool(getattr(args, "no_dashboard", False)) or os.environ.get(
        "PIPELINE_NO_DASHBOARD"
    )
    if not skip_dashboard:
        try:
            _ensure_dashboard_running(open_browser=False)
        except Exception as exc:  # pragma: no cover - defensive, never block pipeline
            print(DIM(f"  대시보드 부트 예외 무시: {exc}"))

    print(f"  다음 단계: {YELLOW('python pipeline.py done --phase pm --report-file step_plan.xml --decomp --clarification --roadmap')}")
    print()


def cmd_check(args: argparse.Namespace) -> None:
    """gate 검증 -exit 0: 통과, exit 1: 차단."""
    state = _load_branch_state(args)
    phase = args.phase.lower()

    # MT-2 (IMP-20260507-4D27): HARNESS GATE — 사용자 Phase 7 진행 확인 hard gate
    # --user-confirmed 플래그 없이 check --phase harness 호출 시 즉시 차단.
    # AskUserQuestion으로 사용자 "진행" 응답을 받은 후에만 --user-confirmed 추가 가능.
    if phase == "harness":
        user_confirmed: bool = getattr(args, "user_confirmed", False)
        if not user_confirmed:
            print()
            print(RED("[HARNESS GATE] Phase 7 진입 거부 — 사용자 Phase 7 진행 확인 필요."))
            print(RED("  AskUserQuestion으로 사용자에게 \"Phase 7 진행하시겠습니까?\" 질문 후,"))
            print(RED("  사용자 \"진행\" 응답을 받은 후에만 아래 명령 실행 가능:"))
            print(RED("  python pipeline.py check --phase harness --user-confirmed"))
            print()
            sys.exit(1)

    ok, reason = check_gate(state, phase)

    if ok:
        print(GREEN(f"\n[GATE OK] {PHASE_LABELS.get(phase, phase)} 진입 가능\n"))
        sys.exit(0)
    else:
        print()
        print(RED(f"[GATE BLOCKED] {PHASE_LABELS.get(phase, phase)} 진입 차단"))
        print(RED(f"  사유: {reason}"))
        print()
        print("  해결 방법:")
        for req_phase, req_status in GATE_RULES.get(phase, []):
            actual = state["phases"][req_phase]["status"]
            if isinstance(req_status, list):
                ok_now = actual in req_status
            else:
                ok_now = actual == req_status
            icon = GREEN("✓") if ok_now else RED("✗")
            expected = "/".join(req_status) if isinstance(req_status, list) else req_status
            print(f"    {icon} {PHASE_LABELS[req_phase]} → [{expected}] (현재: {actual})")
        print()
        sys.exit(1)


def cmd_done(args: argparse.Namespace) -> None:
    """pm 또는 dev phase 완료 처리."""
    branch: Optional[str] = getattr(args, "branch", None)
    state = _load_branch_state(args, "state 파일이 없습니다. tournament-start 먼저 실행하세요.")
    phase = args.phase.lower()

    if phase not in ("pm", "dev"):
        _die(f"'done' 명령은 pm/dev 전용입니다. qa/sec/build/harness는 전용 명령 사용.")

    ok, reason = check_gate(state, phase)
    if not ok:
        _die(f"[GATE BLOCKED] {reason}")

    # ── MT-1: PM Analysis Gate (IMP-20260506-A064) ───────────────────────────
    # PM이 step_plan 발행 전 3가지 분석 단계를 완료했는지 기록합니다.
    # --decomp: decomposition_audit 출력 완료 (micro-task 분해)
    # --clarification: Mandatory Clarification Triggers 판정 완료
    # --roadmap: User Roadmap Presentation Gate 처리 완료 (면제 조건 적용 시에도 기록)
    # --judgment-confirmed: AMBIGUOUS decomposition_audit 발행 시 judgment_calls_resolved
    #   블록이 step_plan에 포함되었음을 선언. AMBIGUOUS 외 상황에서는 플래그 불필요.
    if phase == "pm":
        decomp_done: bool = bool(getattr(args, "decomp", False))
        clarification_done: bool = bool(getattr(args, "clarification", False))
        roadmap_done: bool = bool(getattr(args, "roadmap", False))
        judgment_confirmed: bool = bool(getattr(args, "judgment_confirmed", False))
        pm_gate_flags: Dict[str, bool] = {
            "decomp": decomp_done,
            "clarification": clarification_done,
            "roadmap": roadmap_done,
            "judgment_confirmed": judgment_confirmed,
        }
        # ── Hard gates: decomp, clarification 필수 ────────────────────────────
        if not decomp_done:
            _die(
                "[PM GATE] --decomp 플래그 필수 — PM이 <decomposition_audit> 블록을 출력한 후 "
                "이 플래그를 포함하여 done --phase pm을 호출하세요."
            )
        if not clarification_done:
            _die(
                "[PM GATE] --clarification 플래그 필수 — PM이 Mandatory Clarification Triggers 판정을 "
                "완료한 후 이 플래그를 포함하여 done --phase pm을 호출하세요."
            )
        if not roadmap_done:
            _die(
                "[PM ROADMAP GATE] --roadmap 플래그 필수 — PM이 dev-agent spawn 전에 사용자에게 "
                "로드맵을 보고하고 '진행' 승인을 받은 뒤 기록해야 합니다."
            )
        # judgment_confirmed: AMBIGUOUS audit일 때 아래 atomic plan gate에서 hard 검증
        state.setdefault("pm_analysis_gate", {})
        state["pm_analysis_gate"].update(pm_gate_flags)
        flags_summary = ", ".join(f"{k}={'Y' if v else 'N'}" for k, v in pm_gate_flags.items())
        print(GREEN(f"  [PM ANALYSIS GATE] {flags_summary}"))
        # AMBIGUOUS 감지: --decomp 선언했으나 --judgment-confirmed 미선언 시 경고
        if decomp_done and not judgment_confirmed:
            print(YELLOW(
                "  [JUDGMENT WARN] decomp 선언됨 — decomposition_audit이 AMBIGUOUS인 경우 "
                "--judgment-confirmed 플래그를 추가하여 judgment_calls_resolved 포함을 선언하세요. "
                "(AMBIGUOUS가 아니면 무시)"
            ))

    # PM atomic plan gate: the --decomp flag is never accepted as mere metadata.
    # Every PM completion must provide parseable planning XML so Round 1 cannot
    # silently skip decomposition or impersonate Dev/QA output.
    report_file: Optional[str] = getattr(args, "report_file", None)
    if phase == "pm":
        if not report_file:
            _die(
                "[ATOMIC PLAN GATE] PM done requires "
                "`python pipeline.py done --phase pm --report-file step_plan.xml --decomp --clarification ...`"
            )
        state["atomic_plan"] = _validate_pm_step_plan_file(report_file, state)
        if state["atomic_plan"]["audit_result"] == "AMBIGUOUS" and not judgment_confirmed:
            _die(
                "[ATOMIC PLAN GATE] AMBIGUOUS decomposition requires --judgment-confirmed "
                "and <judgment_calls_resolved> in the PM report."
            )
        print(GREEN(
            f"  [ATOMIC PLAN GATE] micro_tasks={state['atomic_plan']['micro_task_count']} "
            f"audit={state['atomic_plan']['audit_result']}"
        ))

    evidence = args.files if hasattr(args, "files") and args.files else None

    if phase == "dev":
        scope_declared: bool = bool(getattr(args, "scope_declared", False))
        if not scope_declared:
            _die(
                "[SCOPE GATE] Dev DONE requires --scope-declared. "
                "dev-agent must provide <scope_declaration> before Phase 2 can close."
            )
        print(GREEN("  [SCOPE GATE] scope_declaration confirmed"))
        state["dev_handover"] = _validate_dev_handover_file(report_file, state)
        state.setdefault("dev_gate_flags", {})
        state["dev_gate_flags"]["scope_declared"] = True
        scope_manifest: Optional[str] = getattr(args, "scope_manifest", None)
        if not scope_manifest:
            _die(
                "[ATOMIC SCOPE GATE] Dev DONE requires "
                "`--scope-manifest scope_manifest.json` in every pipeline."
            )
        state["atomic_scope"] = _validate_dev_scope_manifest(scope_manifest, state, args.files)
        print(GREEN(
            f"  [ATOMIC SCOPE GATE] micro_tasks={len(state['atomic_scope']['micro_task_ids'])} "
            f"files={len(state['atomic_scope']['files'])}"
        ))

    # ── MT-5: Frozen Codebase scope_declaration 선언 여부 기록 ────────────────
    # dev --scope-declared 플래그: dev-agent가 <scope_declaration>을 출력했음을 명시.
    # 선언 누락 시 경고 출력 (hard fail 아님 — 하위 호환성 유지).
    if False and phase == "dev":
        scope_declared: bool = bool(getattr(args, "scope_declared", False))
        if not scope_declared:
            print(YELLOW(
                "[SCOPE WARN] --scope-declared 플래그 없음 — dev-agent가 <scope_declaration>을 출력했는지 확인 필요. "
                "(QA가 scope_declaration 누락 시 FAIL 처리할 수 있음)"
            ))
        else:
            print(GREEN("  [SCOPE GATE] scope_declaration 선언 확인됨"))
        state.setdefault("dev_gate_flags", {})
        state["dev_gate_flags"]["scope_declared"] = scope_declared
        scope_manifest: Optional[str] = getattr(args, "scope_manifest", None)
        if _external_gates_enabled(state):
            if not scope_manifest:
                _die(
                    "[ATOMIC SCOPE GATE] Three-Gate mode requires "
                    "`--scope-manifest scope_manifest.json` for dev DONE."
                )
            state["atomic_scope"] = _validate_dev_scope_manifest(scope_manifest, state, args.files)
            print(GREEN(
                f"  [ATOMIC SCOPE GATE] micro_tasks={len(state['atomic_scope']['micro_task_ids'])} "
                f"files={len(state['atomic_scope']['files'])}"
            ))
        elif scope_manifest:
            print(YELLOW("  [SCOPE WARN] --scope-manifest supplied but atomic hard gate is active only in Three-Gate mode."))

    # dev phase: --files에 나열된 경로가 실제로 존재하는지 검증
    if phase == "dev" and evidence:
        missing = [t.strip() for t in evidence.split(",") if t.strip() and not Path(t.strip()).exists()]
        if missing:
            print(RED("\n[FILE NOT FOUND] DONE 기록 거부 — 존재하지 않는 파일:"))
            for p in missing: print(RED(f"  - {p}"))
            print(RED("dev-agent가 실제로 파일을 작성한 후 다시 실행하세요.\n"))
            sys.exit(1)

    state["phases"][phase]["status"]       = "DONE"
    state["phases"][phase]["completed_at"] = _now()
    state["phases"][phase]["evidence"]     = evidence
    state["phases"][phase]["report_file"]  = report_file
    state["current_phase"] = PHASE_ORDER[PHASE_ORDER.index(phase) + 1]
    _log_event(state, f"{phase} DONE | evidence: {evidence}")
    if phase == "dev":
        review_files = _advisory_review_files_from_args(state, evidence)
        advisory_result = _auto_run_openai_advisory(state, kind="gpt-code", files=review_files)
        status = advisory_result.get("status")
        if status == "COMPLETED":
            print(GREEN(f"  [GPT ADVISORY] gpt-code completed via {OPENAI_ADVISORY_MODEL}"))
        elif status == "SKIPPED":
            print(YELLOW(f"  [GPT ADVISORY] gpt-code skipped: {advisory_result.get('reason')}"))
        else:
            print(RED(f"  [GPT ADVISORY] gpt-code error: {advisory_result.get('reason')}"))
    _record_snapshot(state, phase, branch)
    _save_state_for(state, branch)

    next_phase = state["current_phase"]
    branch_tag = f" [Branch {branch}]" if branch else ""
    print(GREEN(f"\n[{phase.upper()} DONE]{branch_tag} {PHASE_LABELS[phase]} 완료"))
    if evidence:
        for f in evidence.split(","):
            print(f"  파일: {f.strip()}")
    print(f"\n  다음 단계: {YELLOW(f'python pipeline.py check --phase {next_phase}')}")
    print()


def cmd_qa(args: argparse.Namespace) -> None:
    """QA 결과 기록."""
    branch: Optional[str] = getattr(args, "branch", None)
    state = _load_branch_state(args)
    ok, reason = check_gate(state, "qa")
    if not ok:
        _die(f"[GATE BLOCKED] {reason}")

    result = args.result.upper()
    if result not in ("PASS", "FAIL"):
        _die("--result 는 PASS 또는 FAIL 이어야 합니다.")

    # ── MT-2: QA numeric_score 기록 강제 (IMP-20260506-A064) ────────────────
    # --numeric-score: QA가 산출한 수치 점수(0~120 정수).
    # PASS/FAIL 공통 hard gate: --numeric-score 필수
    # PASS 시: 96점(80%) 이상 추가 요건. FAIL 시: 점수 하한 없음 (회로 차단기 추적 및 harness 합산용).
    numeric_score_raw: Optional[str] = getattr(args, "numeric_score", None)
    numeric_score: Optional[int] = None
    if numeric_score_raw is None:
        _die(
            "\n[QA NUMERIC GATE] --numeric-score 필수 (PASS/FAIL 공통).\n"
            "  qa-agent는 <numeric_score> 블록을 출력하고 0~120 점수를 반드시 제출해야 합니다.\n"
            "  예: python pipeline.py qa --result FAIL --numeric-score 60 --failure-sig \"PD:abc123\"\n"
        )
    if numeric_score_raw is not None:
        try:
            numeric_score = int(numeric_score_raw)
        except (ValueError, TypeError):
            _die("--numeric-score 는 0~120 정수여야 합니다.")
        if not (0 <= numeric_score <= 120):
            _die("--numeric-score 범위 오류: 0~120 이어야 합니다.")
        if result == "PASS" and numeric_score < 96:  # 80% of 120 = 96
            print(RED(
                f"\n[QA NUMERIC GATE] PASS 기록 거부 — numeric_score={numeric_score} < 96 (80% of 120)"
            ))
            print(RED("  QA numeric_verdict가 FAIL(점수 < 80%)이면 --result FAIL로 기록하세요.\n"))
            sys.exit(1)

    # ── MT-3: Circuit Breaker failure_signature 추적 (IMP-20260506-A064) ─────
    # --failure-sig: QA FAIL 시 <failure_signature>[category]:[hash]</failure_signature> 값.
    # 동일 failure_signature가 연속 2회 감지되면 RECURRING 경고 출력 (PM이 Circuit Breaker 발동 판단).
    failure_sig: Optional[str] = getattr(args, "failure_sig", None)
    if result == "FAIL":
        if not failure_sig:
            _die(
                "[QA GATE] --failure-sig 필수 — QA FAIL 시 <failure_signature> 값을 포함하여 "
                "pipeline.py qa --result FAIL --numeric-score N --failure-sig '[category]:[hash]'를 호출하세요. "
                "Circuit Breaker 패턴 추적에 필수입니다."
            )
        # qa_fail_history 초기화 (없을 시 신규 생성)
        state.setdefault("qa_fail_history", [])
        fail_history: List[Dict[str, Any]] = state["qa_fail_history"]
        round_n = len(fail_history) + 1

        repeat_indicator = "FIRST"
        if failure_sig and fail_history:
            # 직전 라운드의 failure_signature와 비교
            prev_entry = fail_history[-1]
            prev_sig = prev_entry.get("failure_signature")
            if prev_sig and prev_sig == failure_sig:
                repeat_indicator = "RECURRING"

        history_entry: Dict[str, Any] = {
            "round": round_n,
            "verdict": "FAIL",
            "failure_signature": failure_sig or "N/A",
            "repeat_indicator": repeat_indicator,
            "recorded_at": _now(),
        }
        fail_history.append(history_entry)
        state["qa_fail_history"] = fail_history

        if repeat_indicator == "RECURRING":
            print(RED(
                f"\n[CIRCUIT BREAKER] QA FAIL 동일 시그니처 2회 연속 감지 — "
                f"failure_signature='{failure_sig}'\n"
                f"  PM은 dev-agent 3회 재spawn 대신 Phase 8 (prompt-architect-agent)로 즉시 이관하세요.\n"
                f"  참조: CLAUDE.md v3.0 Circuit Breaker Protocol\n"
            ))
        elif failure_sig:
            print(YELLOW(
                f"  [CIRCUIT BREAKER] Round {round_n} FAIL 기록 — "
                f"signature='{failure_sig}' ({repeat_indicator})"
            ))
    elif result == "PASS":
        # PASS 시 qa_fail_history 초기화 (새 사이클 시작)
        if state.get("qa_fail_history"):
            state["qa_fail_history"] = []

    # ── QA Report Hallucination Gate ──────────────────────────────────────────
    report_file: Optional[str] = getattr(args, "report_file", None)
    qa_report_validation = _validate_qa_report_file(
        report_file,
        result=result,
        numeric_score=int(numeric_score),
    )
    state["qa_report_validation"] = qa_report_validation
    if report_file:
        _verify_required_xml_tags(
            report_file,
            required_tags=["<qa_report>", "<numeric_score>", "<verdict>"],
            context_label="QA REPORT GATE",
            hard_fail=True,
        )

    state["phases"]["qa"]["status"]       = result
    state["phases"]["qa"]["completed_at"] = _now()
    state["phases"]["qa"]["evidence"]     = getattr(args, "agent_id", None)
    state["phases"]["qa"]["agent_id"]     = getattr(args, "agent_id", None)
    state["phases"]["qa"]["report_file"]  = report_file
    # MT-2: numeric_score를 phase 메타데이터에 저장
    if numeric_score is not None:
        state["phases"]["qa"]["numeric_score"] = numeric_score

    if result == "PASS":
        state["current_phase"] = "sec"
        msg = GREEN("[QA PASS] 다음: Phase 5 -Security 또는 Phase 6 -Build")
        next_cmd = "python pipeline.py check --phase sec  # 네트워크/DB 포함 시\n  python pipeline.py sec --skip      # 해당 없을 시"
    else:
        state["current_phase"] = "dev"
        state["phases"]["dev"]["status"] = "PENDING"
        msg = RED("[QA FAIL] Phase 2 -Dev 재작업 필요")
        next_cmd = "python pipeline.py done --phase dev --files \"수정된파일들\" --report-file dev_handover.xml --scope-declared --scope-manifest scope_manifest.json"

    _log_event(state, f"qa {result}" + (f" numeric={numeric_score}" if numeric_score is not None else ""))
    _record_snapshot(state, "qa", branch)
    _save_state_for(state, branch)
    branch_tag = f" [Branch {branch}]" if branch else ""
    print(f"\n{msg}{branch_tag}")
    if numeric_score is not None:
        score_color = GREEN if (result == "PASS") else RED
        print(score_color(f"  numeric_score={numeric_score}/120"))
    print(f"\n  다음: {YELLOW(next_cmd)}\n")


def cmd_sec(args: argparse.Namespace) -> None:
    """Security 결과 기록."""
    branch: Optional[str] = getattr(args, "branch", None)
    state = _load_branch_state(args)
    ok, reason = check_gate(state, "sec")
    if not ok:
        _die(f"[GATE BLOCKED] {reason}")

    if getattr(args, "skip", False):
        status   = "SKIP"
        risk     = "N/A"
        msg      = YELLOW("[SEC SKIP] 네트워크/DB 없음 -보안 감사 생략")
    else:
        result = args.result.upper()
        risk   = getattr(args, "risk", "UNKNOWN").upper()
        if result not in ("PASS", "BLOCK", "FAIL"):
            _die("--result 는 PASS, BLOCK 또는 FAIL 이어야 합니다.")
        if result == "BLOCK":
            state["blocked"]        = True
            state["blocked_reason"] = f"SEC BLOCK -risk: {risk}"
            _log_event(state, f"sec BLOCK risk={risk}")
            _save_state_for(state, branch)
            _die(f"[SEC BLOCK] risk_level={risk} -dev-agent 수정 후 재감사 필요.", exit_code=2)
        if result == "FAIL":
            state["phases"]["sec"]["status"]       = "FAIL"
            state["phases"]["sec"]["completed_at"] = _now()
            state["phases"]["sec"]["evidence"]     = risk
            state["current_phase"] = "dev"
            state["phases"]["dev"]["status"] = "PENDING"
            _log_event(state, f"sec FAIL risk={risk}")
            _save_state_for(state, branch)
            print(YELLOW(f"\n[SEC FAIL] risk_level={risk} — Tier2 이상 발견"))
            print(f"\n  다음: {YELLOW('python pipeline.py done --phase dev --files \"수정된파일들\" --report-file dev_handover.xml --scope-declared --scope-manifest scope_manifest.json')}\n")
            return
        status = "PASS"
        msg    = GREEN(f"[SEC PASS] risk_level={risk}")

    state["phases"]["sec"]["status"]       = status
    state["phases"]["sec"]["completed_at"] = _now()
    state["phases"]["sec"]["evidence"]     = risk
    state["current_phase"] = "build"
    _log_event(state, f"sec {status} risk={risk}")
    _record_snapshot(state, "sec", branch)
    _save_state_for(state, branch)

    print(f"\n{msg}")
    print(f"\n  다음: {YELLOW('python pipeline.py check --phase build')}\n")


def cmd_build(args: argparse.Namespace) -> None:
    """Build 결과 기록."""
    branch: Optional[str] = getattr(args, "branch", None)
    state = _load_branch_state(args)
    ok, reason = check_gate(state, "build")
    if not ok:
        _die(f"[GATE BLOCKED] {reason}")

    exe = getattr(args, "exe", None)

    # ── MT-4: BUILD 6-Section Report 파일 존재 검증 (IMP-20260506-A064) ──────
    # EXE 빌드인 경우 dist/build_report.xml 파일이 존재해야 DONE 기록 허용.
    # N/A 빌드(Streamlit/MD-only/메타-태스크)는 검증 생략.
    # --report-file 로 커스텀 경로 지정 가능 (기본: dist/build_report.xml).
    is_na_build = (exe is None) or (str(exe).strip().upper() == "N/A")
    build_report_file: Optional[str] = getattr(args, "report_file", None)
    skip_reason: Optional[str] = getattr(args, "skip_reason", None)
    if not is_na_build:
        # EXE 파일 실제 존재 검증
        exe_path = Path(str(exe))
        if not exe_path.exists():
            _die(
                f"\n[BUILD EXE GATE] EXE 파일 없음: {exe}\n"
                "  PyInstaller dist/ 폴더에 EXE가 생성된 후 이 명령을 실행하세요.\n"
            )
        # dist/build_report.xml 기본 경로 또는 --report-file 지정 경로
        if build_report_file is None:
            build_report_file = str(BASE_DIR / "dist" / "build_report.xml")
        build_report_path = Path(build_report_file)
        if not build_report_path.exists():
            print(RED(
                f"\n[BUILD REPORT GATE] build_report.xml 파일 없음: {build_report_file}"
            ))
            print(RED(
                "  build-agent는 dist/build_report.xml 파일을 저장한 후 이 명령을 실행해야 합니다."
            ))
            print(RED(
                "  (build-agent.md '## Output Format' 섹션 참조 — 6-Section Report 파일 저장 의무)\n"
            ))
            sys.exit(1)
        # 6-Section XML 블록 검증 — BUG-20260507-C2E2: _verify_build_report_xml (ET only, no regex fallback)
        # XML comment bypass 차단: comment 내 가짜 섹션 태그는 무효 처리됨
        build_report_text = ""
        bp = Path(build_report_file)
        for enc in ("utf-8", "cp949", "latin-1"):
            try:
                build_report_text = bp.read_text(encoding=enc)
                break
            except (UnicodeDecodeError, OSError):
                continue
        if not build_report_text:
            print(RED(f"\n[BUILD REPORT GATE] build_report.xml 읽기 실패: {build_report_file}\n"))
            sys.exit(1)
        xml_ok, xml_msg = _verify_build_report_xml(build_report_text)
        if not xml_ok:
            print(RED(f"\n[BUILD 6-SECTION GATE] XML 검증 실패: {xml_msg}"))
            print(RED("  build_report.xml에 6개 섹션과 <status>BUILD SUCCESS</status>가 실제 XML 태그로 포함되어야 합니다."))
            print(RED("  XML comment(<!-- -->)로 감싼 섹션은 유효하지 않습니다.\n"))
            sys.exit(1)
        print(GREEN("  [BUILD REPORT GATE] 6-Section Report 검증 통과 (XML comment bypass 차단 완료)"))
    else:
        # MT-2 (IMP-20260507-49F7): whitelist 방식으로 교체.
        # 조건: len >= 5 AND reason.lower() in whitelist (AND 논리, OR 아님).
        # 허용 목록 외 값 또는 축약어("skip" 등)는 모두 차단.
        SKIP_REASON_WHITELIST = {
            "md-only", "meta-task", "streamlit", "power-automate", "no-code", "docs-only"
        }
        reason = (skip_reason or "").strip()
        if len(reason) < 5 or reason.lower() not in SKIP_REASON_WHITELIST:
            _die(
                "\n[BUILD N/A GATE] --exe \"N/A\" 기록 거부 — --skip-reason이 whitelist에 없거나 길이 < 5.\n"
                f"  허용 목록(대소문자 무관): {sorted(SKIP_REASON_WHITELIST)}\n"
                "  예: --skip-reason \"meta-task\", --skip-reason \"streamlit\""
            )
        # MT-1 (IMP-20260507-FC80): N/A 빌드 --user-confirmed hard gate
        # AskUserQuestion으로 사용자 "진행" 응답을 받은 후에만 이 플래그를 추가할 수 있다.
        # 플래그 없이 호출하면 사용자 확인 없이 파이프라인이 진행된 것이므로 즉시 차단.
        user_confirmed: bool = getattr(args, "user_confirmed", False)
        if not user_confirmed:
            _die(
                "\n[BUILD N/A GATE] --exe \"N/A\" 기록 거부 — --user-confirmed 플래그 누락.\n"
                "  N/A 빌드는 AskUserQuestion으로 사용자에게 Phase 7 진행 확인을 받은 후에만 기록할 수 있습니다.\n"
                "  예: python pipeline.py build --exe \"N/A\" --skip-reason \"meta-task\" --user-confirmed"
            )
        skip_reason = reason
        print(YELLOW("  [BUILD REPORT GATE] N/A 빌드 — build_report.xml 검증 생략"))

    state["phases"]["build"]["status"]       = "DONE"
    state["phases"]["build"]["completed_at"] = _now()
    state["phases"]["build"]["evidence"]     = exe
    state["phases"]["build"]["report_file"]  = build_report_file if not is_na_build else None
    state["phases"]["build"]["skip_reason"]  = skip_reason if is_na_build else None
    state["current_phase"] = "harness"
    _log_event(state, f"build DONE exe={exe}" + (f" skip_reason={skip_reason}" if is_na_build else ""))
    _record_snapshot(state, "build", branch)
    _save_state_for(state, branch)

    print(GREEN(f"\n[BUILD DONE] EXE: {exe or '경로 미지정'}"))
    print()
    print(BOLD(YELLOW("  ★ Phase 7 (Harness) 실행 의무 -생략 불가")))
    print(f"  다음 절차:")
    print(f"    1. AskUserQuestion으로 사용자에게 Phase 7 진행 확인 요청")
    print(f"    2. 사용자 '진행' 응답 수신 후:")
    print(f"       {YELLOW('python pipeline.py check --phase harness --user-confirmed')}")
    print(f"    3. test-harness-agent 를 spawn하여 채점 후 아래 명령 실행:")
    print(f"       {YELLOW('python pipeline.py harness --score [점수] --verdict [PASS/FAIL] --test-output-file [harness_output.xml] --user-confirmed')}")
    print()


def cmd_harness(args: argparse.Namespace) -> None:
    """Harness 채점 결과 기록."""
    branch: Optional[str] = getattr(args, "branch", None)

    # ── Hard gates: 상태를 기록하는 명령 안에서 가장 먼저 검사 (check_gate 전) ──
    # 원칙: hard gate는 상태를 실제로 바꾸는 명령(기록 명령) 안에 있어야 함.
    # check_gate(state) 호출 전에 검사하여 파이프라인 상태와 무관하게 항상 적용.

    # Gate 1: --user-confirmed
    # AskUserQuestion으로 사용자 "진행" 응답을 확인한 후에만 --user-confirmed 추가 가능.
    user_confirmed: bool = getattr(args, "user_confirmed", False)
    if not user_confirmed:
        print()
        print(RED("[HARNESS GATE] Phase 7 기록 거부 — 사용자 Phase 7 진행 확인 필요."))
        print(RED("  AskUserQuestion으로 사용자에게 Phase 7 진행 여부를 확인한 후,"))
        print(RED("  사용자 '진행' 응답을 받은 후에만 아래 명령 실행 가능:"))
        print(RED("  python pipeline.py harness --score N --verdict PASS|FAIL --test-output-file PATH --user-confirmed"))
        print()
        sys.exit(1)

    # Gate 2: --test-output-file (PASS/FAIL 공통 필수)
    # BUG-20260508-6198 MT-1: --test-output-file은 PASS뿐 아니라 FAIL에도 필수.
    # FAIL 시에는 <harness_report>가 포함된 RCA 증거 파일이 필요하다.
    test_output_file: Optional[str] = getattr(args, "test_output_file", None)
    if not test_output_file:
        _die(
            "\n[HARNESS GATE BLOCKED] --test-output-file 필수입니다 (PASS/FAIL 공통).\n"
            "  PASS: <harness_report> + 실행 가능한 <test_code> 포함 파일 필요\n"
            "  FAIL: <harness_report> + <test_code> 포함 파일 필요 (PHASE_INTERFACE 계약 통일)\n"
            "  (meta-task 포함 예외 없음)"
        )

    state = _load_branch_state(args)
    if _external_gates_enabled(state):
        _die(
            "\n[THREE GATE BLOCKED] legacy `pipeline.py harness --score` is disabled when "
            "external_gates.enabled=true.\n"
            "  Use: python pipeline.py gates technical\n"
            "       python pipeline.py gates oracle --user-confirmed\n"
            "       python pipeline.py gates accept --result ACCEPT --user-confirmed"
        )

    ok, reason = check_gate(state, "harness")
    if not ok:
        _die(f"[GATE BLOCKED] {reason}")

    score   = int(args.score)
    verdict = args.verdict.upper()
    if verdict not in ("PASS", "FAIL"):
        _die("--verdict 는 PASS 또는 FAIL 이어야 합니다.")
    if not (0 <= score <= 100):
        _die("--score 는 0~100 사이여야 합니다.")

    p = Path(test_output_file)
    if not p.exists():
        _die(f"\n[HARNESS GATE BLOCKED] --test-output-file 경로 없음: {test_output_file}\n")

    # 파일 읽기 (인코딩 자동 감지)
    agent_output = ""
    for enc in ("utf-8", "cp949", "latin-1"):
        try:
            agent_output = p.read_text(encoding=enc)
            break
        except (UnicodeDecodeError, OSError):
            continue
    if not agent_output:
        _die(f"\n[HARNESS GATE BLOCKED] --test-output-file 읽기 실패: {test_output_file}\n")

    # BUG-20260508-D541: comment 제거 후 검증에 사용할 공통 clean 텍스트
    agent_output_clean = _strip_xml_comments(agent_output)

    # ── 공통 Gate A: <harness_report> ElementTree 파싱 (PASS/FAIL 양쪽에서 사용) ──────
    # BUG-20260508-A53A MT-2/MT-3: regex-only 검증 → ElementTree 파싱으로 업그레이드.
    #   - malformed/unclosed <harness_report> → ET.ParseError → None → gate blocked.
    #   - <harness_report> 없음 → None → gate blocked.
    #   - 속성 있는 태그(<harness_report verdict="FAIL">) → ET 정상 파싱 → 허용.
    #   - comment 내 <harness_report> → _strip_xml_comments로 제거 후 파싱 → None → blocked.
    hr_element = _parse_harness_report_et(agent_output_clean)

    # ── Execution Evidence Gate (PASS/FAIL 공통: _validate_harness_evidence_gate() 경유) ──────
    # BUG-20260509-FDBC MT-1: PASS/FAIL 검증 비대칭 해소.
    #   기존 FAIL 경로는 Gate A(harness_report 존재) + Gate B(test_code 존재)만 확인하고
    #   Gate C(validate_test_evidence 실행)를 생략하여 빈/무효/ASSERTION없는 test_code가
    #   RECORDED FAIL로 통과되는 결함이 있었다.
    #   수정: PASS/FAIL 양쪽 모두 동일한 _validate_harness_evidence_gate()를 경유.
    #   단, PASS 경로 전용 dev 파일 syntax 검증(validate_harness_evidence)은 PASS 이후에 유지.
    pid: str = state.get("pipeline_id", "")

    if verdict == "PASS":
        # PASS 전용 dev 파일 syntax 검증 (FAIL 경로에는 불필요)
        dev_evidence: str = state["phases"]["dev"].get("evidence") or ""
        dev_files: List[str] = (
            [f.strip() for f in dev_evidence.split(",") if f.strip()]
            if dev_evidence else []
        )
        if dev_files and not validate_harness_evidence(dev_files):
            print(RED("\n[HARNESS GATE BLOCKED] 실행 검증 실패 — verdict PASS 기록 거부"))
            print(RED("dev-agent가 제출한 파일의 syntax 오류 또는 테스트 실패를 수정 후 재시도하세요."))
            sys.exit(1)
        # Gate A + B + C: 공통 helper 경유 (PASS 경로)
        _validate_harness_evidence_gate(agent_output_clean, agent_output, pid, "PASS")

    # ── FAIL: PASS와 동일한 3-gate (_validate_harness_evidence_gate) 경유 ────────────
    # BUG-20260509-FDBC MT-1: FAIL도 Gate C(validate_test_evidence)를 경유.
    #   FAIL verdict는 harness_score < 80을 의미하며, test_code 자체가 실패 코드라는 뜻이 아님.
    #   따라서 FAIL 기록 시에도 test_code는 unittest.TestCase + testsRun>=1 필수.
    if verdict == "FAIL":
        # Gate A + B + C: 공통 helper 경유 (FAIL 경로)
        _validate_harness_evidence_gate(agent_output_clean, agent_output, pid, "FAIL")

    cleanup_msg = _dedupe_test_results_jsonl()
    if cleanup_msg:
        print(YELLOW(f"  [RESULTS CLEANUP] {cleanup_msg}"))

    state["phases"]["harness"]["status"]       = verdict
    state["phases"]["harness"]["completed_at"] = _now()
    state["phases"]["harness"]["evidence"]     = f"score={score}"
    state["phases"]["harness"]["report_file"]  = test_output_file
    state["current_phase"] = "architect"

    # v2.10 Auto-Compact: harness FAIL 누적 카운터 + 3회 누적 시 terminal_state="FAILED"
    # 메인 state(브랜치 None)에서만 카운팅. 토너먼트 브랜치는 별도.
    if branch is None:
        state = _ensure_v210_fields(state)
        if verdict == "FAIL":
            state["harness_fail_count"] = int(state.get("harness_fail_count", 0)) + 1
            if state["harness_fail_count"] >= 3:
                state["terminal_state"] = "FAILED"
                _log_event(state, f"harness FAIL 3회 누적 — terminal_state=FAILED")
        else:
            state["harness_fail_count"] = 0

    _log_event(state, f"harness {verdict} score={score}")
    _record_snapshot(state, "harness", branch)
    _save_state_for(state, branch)

    # 토너먼트 모드: 마스터 state 의 branch_states 업데이트
    if branch is not None:
        master = _load_state()
        t = master.get("tournament", {})
        if t.get("active") and branch in t.get("branches", []):
            new_bs = "harness_passed" if verdict == "PASS" else "harness_failed"
            t["branch_states"][branch] = new_bs
            master["tournament"] = t
            _save_state(master)

    color = GREEN if verdict == "PASS" else RED
    branch_tag = f" [Branch {branch}]" if branch else ""
    print(color(f"\n[HARNESS {verdict}]{branch_tag} score={score}/100"))
    print()
    print(BOLD(YELLOW("  ★ Phase 8 (Architect RCA) 실행 의무 -생략 불가")))
    print(f"  다음: prompt-architect-agent spawn → RCA 완료 후:")
    print(f"        {YELLOW('python pipeline.py architect --report-file architect_report.xml')}")
    if verdict == "FAIL":
        print()
        print(RED("  [FAIL] score < 80 -Phase 2 재작업 필요"))
        print(f"  Architect RCA 완료 후: {YELLOW('python pipeline.py done --phase dev --files \"..\" --report-file dev_handover.xml --scope-declared --scope-manifest scope_manifest.json')}")
    print()


def cmd_architect(args: argparse.Namespace) -> None:
    """Architect RCA 완료 기록."""
    branch: Optional[str] = getattr(args, "branch", None)
    state = _load_branch_state(args)
    ok, reason = check_gate(state, "architect")
    if not ok:
        _die(f"[GATE BLOCKED] {reason}")

    protocol_decision = _parse_protocol_evolution_decision(getattr(args, "report_file", None))
    harness_verdict = state["phases"]["harness"].get("status", "PENDING")
    rca_mode = str(protocol_decision.get("rca_mode") or "").upper()
    if harness_verdict == "PASS" and "FAIL" in rca_mode:
        _die(
            "[ARCHITECT REPORT GATE] harness is PASS but architect report rca_mode is fail-oriented. "
            "Use a completion/retrospective mode for PASS, or record a real harness FAIL first."
        )

    state["phases"]["architect"]["status"]       = "DONE"
    state["phases"]["architect"]["completed_at"] = _now()
    state["phases"]["architect"]["report_file"]  = protocol_decision["report_file"]
    state["protocol_evolution_decision"] = protocol_decision
    _record_snapshot(state, "architect", branch)

    if harness_verdict == "FAIL":
        # Harness FAIL path: Architect RCA 완료 후 Phase 2 재작업으로 루프백
        state["current_phase"] = "dev"
        state["phases"]["dev"]["status"] = "PENDING"
        state["terminal_state"] = None
        _log_event(state, "architect DONE — harness FAIL path: dev PENDING reset for rework")
        _save_state_for(state, branch)
    else:
        external_blockers = _external_gate_blockers(state)
        if external_blockers:
            _die(
                "[THREE GATE BLOCKED] COMPLETE requires external gates and advisory resolution: "
                + "; ".join(external_blockers)
            )
        # Harness PASS path: 파이프라인 정상 완료
        state["current_phase"] = "COMPLETE"
        # COMPLETE is a Phase 8 transition; protocol evolution is a separate IMP follow-up.
        state["terminal_state"] = "COMPLETE"
        _log_event(state, "architect DONE -pipeline complete (terminal_state=COMPLETE)")

        # Archive completed pipeline
        HISTORY_DIR.mkdir(exist_ok=True)
        pid     = state.get("pipeline_id", "UNKNOWN")
        archive = HISTORY_DIR / f"{pid}_COMPLETE.json"
        archive.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        _save_state_for(state, branch)

    branch_tag = f" [Branch {branch}]" if branch else ""
    pid_display = state.get("pipeline_id", "UNKNOWN")
    if harness_verdict == "FAIL":
        print(YELLOW(f"\n[ARCHITECT DONE — REWORK]{branch_tag} {pid_display}"))
        print(YELLOW("  Harness FAIL 경로: Phase 2 (Dev) 재작업 필요"))
        print(f"\n  다음 단계: {YELLOW('python pipeline.py done --phase dev --files \"..\" --report-file dev_handover.xml --scope-declared --scope-manifest scope_manifest.json')}")
    else:
        print(GREEN(f"\n[PIPELINE COMPLETE]{branch_tag} {pid_display}"))
        archive_path = HISTORY_DIR / f"{pid_display}_COMPLETE.json"
        if protocol_decision.get("required"):
            print()
            print(YELLOW("  Protocol evolution follow-up required. No automatic Phase 9 was entered."))
            print(YELLOW(f"  Start a separate IMP pipeline for scope: {protocol_decision.get('scope')}"))
            print(YELLOW(f"  Reason: {protocol_decision.get('reason')}"))
        print(f"  보관: {archive_path}")
        print()
        print("  모든 Phase 완료. 새 작업은 `python pipeline.py new` 로 시작합니다.")
    print()


def cmd_status(args: argparse.Namespace) -> None:
    """현재 파이프라인 상태 출력."""
    state = _load()
    if state is None:
        print(YELLOW("\n  활성 파이프라인 없음. `python pipeline.py new` 로 시작하세요.\n"))
        return

    state = _ensure_v210_fields(state)
    pid         = state["pipeline_id"]
    description = state["description"]
    current     = state["current_phase"]
    blocked     = state.get("blocked", False)
    terminal    = state.get("terminal_state")

    print()
    print(BOLD(f"  파이프라인: {CYAN(pid)}"))
    print(f"  설명: {description}")
    print(f"  생성: {state['created_at']}  갱신: {state['updated_at']}")
    if blocked:
        print(RED(f"  [차단] {state.get('blocked_reason', '')}"))
    # v2.10 Auto-Compact: 종료 상태 표시 (Stop hook이 이 필드를 읽음)
    if terminal:
        print(YELLOW(f"  [종료 상태] terminal_state={terminal}"))
    print()
    print(BOLD("  Phase 현황:"))
    print()

    for phase in PHASE_ORDER:
        info    = state["phases"][phase]
        status  = info["status"]
        label   = PHASE_LABELS[phase]
        ev      = info.get("evidence") or ""

        if status in ("DONE", "PASS", "SKIP"):
            icon  = GREEN("✓")
            color = GREEN
        elif status == "FAIL":
            icon  = RED("✗")
            color = RED
        elif status == "PENDING" and phase == current:
            icon  = YELLOW("→")
            color = YELLOW
        else:
            icon  = DIM("·")
            color = DIM

        ts = f"  {info['completed_at'][:16] if info['completed_at'] else ''}"
        print(f"    {icon} {color(label):<42} [{color(status):<8}]{ts}")
        if ev:
            print(DIM(f"        증거: {ev}"))

    gates = state.get("external_gates", {})
    if isinstance(gates, dict) and gates.get("enabled"):
        print()
        print(BOLD("  External Gate 현황:"))
        for gate_name in ("technical", "oracle", "acceptance"):
            gate = gates.get(gate_name, {})
            if not isinstance(gate, dict):
                continue
            status = str(gate.get("status", "PENDING"))
            color = GREEN if status == "PASS" else RED if status == "FAIL" else YELLOW
            print(f"    {color(gate_name):<16} [{color(status):<8}] {gate.get('completed_at') or ''}")
            if gate.get("evidence"):
                print(DIM(f"        증거: {gate.get('evidence')}"))
        blockers = _external_gate_blockers(state)
        if blockers:
            print(RED("    blockers: " + "; ".join(blockers)))

    advisory = _advisory_status_summary(str(pid))
    print()
    print(BOLD("  GPT Advisory status:"))
    if advisory["review_count"] == 0:
        print(YELLOW("    NOT RUN — no advisory review files recorded"))
    else:
        status_bits = ", ".join(f"{k}={v}" for k, v in sorted(advisory["status_counts"].items()))
        print(f"    reviews={advisory['review_count']} api_calls={advisory['api_call_count']} statuses={status_bits}")
        print(f"    unresolved_critical={advisory['unresolved_critical_count']}")
    if not advisory["api_key_present"]:
        print(DIM("    OPENAI_API_KEY not set; GPT advisory cannot call OpenAI."))
    elif not advisory["enabled"]:
        print(DIM("    ENABLE_GPT_ADVISORY is not 1; GPT advisory auto-calls are disabled."))
    else:
        print(DIM(
            f"    GPT advisory model fixed to {OPENAI_ADVISORY_MODEL}; "
            f"auto-calls enabled (key source: {advisory.get('api_key_source', 'unknown')})."
        ))

    print()
    if current == "COMPLETE":
        print(GREEN("  ✓ 파이프라인 완료\n"))
    else:
        gate_ok, reason = check_gate(state, current) if current in GATE_RULES else (True, "")
        if gate_ok:
            print(f"  현재 단계: {YELLOW(PHASE_LABELS.get(current, current))}")
            hint_suffix = " --user-confirmed" if current == "harness" else ""
            print(f"  확인 명령: {YELLOW(f'python pipeline.py check --phase {current}{hint_suffix}')}")
        else:
            print(RED(f"  [차단] {reason}"))
        print()

    # Recent log
    log = state.get("event_log", [])[-5:]
    if log:
        print(DIM("  최근 이벤트:"))
        for entry in log:
            print(DIM(f"    {entry['ts'][:16]}  {entry['msg']}"))
        print()


def cmd_log(args: argparse.Namespace) -> None:
    state = _require_state()
    _log_event(state, args.message)
    _save(state)
    print(GREEN(f"\n  [LOG] {args.message}\n"))


def cmd_unblock(args: argparse.Namespace) -> None:
    state = _require_state()
    state["blocked"]        = False
    state["blocked_reason"] = None
    _log_event(state, "파이프라인 차단 해제")
    _save(state)
    print(GREEN("\n  [UNBLOCK] 파이프라인 차단 해제 완료\n"))


def cmd_terminate(args: argparse.Namespace) -> None:
    """파이프라인 명시적 종료 (TERMINATED terminal state 기록).

    BUG-20260507-C2E2 MT-3: TERMINATED 상태 추가.
    사용자가 명시적으로 파이프라인을 중단할 때 사용합니다.
    TERMINATED 상태 후 모든 check_gate 호출은 BLOCKED됩니다.
    새 파이프라인 시작: python pipeline.py new
    """
    state = _require_state()
    pid = state.get("pipeline_id", "UNKNOWN")

    if state.get("terminal_state") in ("COMPLETE", "FAILED", "TERMINATED"):
        existing = state.get("terminal_state")
        print(YELLOW(f"\n  [TERMINATE] 파이프라인 이미 종료 상태: terminal_state={existing}\n"))
        return

    state["terminal_state"] = "TERMINATED"
    state["current_phase"] = "TERMINATED"
    _log_event(state, f"파이프라인 명시적 종료 (사용자 terminate 명령)")

    # 보관
    HISTORY_DIR.mkdir(exist_ok=True)
    archive = HISTORY_DIR / f"{pid}_TERMINATED_{_now().replace(':', '-')}.json"
    archive.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    _save(state)
    print(RED(f"\n[TERMINATED] 파이프라인 {pid} 종료됨"))
    print(f"  보관: {archive.name}")
    print(f"  새 파이프라인 시작: {YELLOW('python pipeline.py new --type FEAT|BUG|IMP --desc \"..\"')}")
    print()


def cmd_list(args: argparse.Namespace) -> None:
    HISTORY_DIR.mkdir(exist_ok=True)
    files = sorted(HISTORY_DIR.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
    if not files:
        print(YELLOW("\n  파이프라인 이력 없음\n"))
        return
    print()
    print(BOLD("  파이프라인 이력:"))
    print()
    for f in files[:20]:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            pid    = data.get("pipeline_id", "?")
            desc   = data.get("description", "")[:50]
            status = data.get("current_phase", "?")
            upd    = data.get("updated_at", "")[:16]
            color  = GREEN if status == "COMPLETE" else YELLOW
            print(f"    {color(pid):<28} {upd}  {DIM(desc)}")
        except Exception:
            print(DIM(f"    {f.name}"))
    print()


def cmd_interface(args: argparse.Namespace) -> None:
    """에이전트가 spawn 시 알아야 할 최소 컨텍스트만 출력 (토큰 절감용).

    출력: pipeline_id, 현재 phase, 게이트 상태, 다음에 실행할 명령어 시그니처.
    `pipeline.py` 전체나 `CLAUDE.md`를 읽지 않고도 다음 액션을 알 수 있게 한다.
    """
    state = _load()
    if state is None:
        print("[NO_PIPELINE] 활성 파이프라인 없음 — `python pipeline.py new --type ... --desc ...` 먼저 실행")
        sys.exit(0)
    state = _ensure_v210_fields(state)

    pid = state["pipeline_id"]
    current = state["current_phase"]
    requested = (getattr(args, "phase", None) or current or "").lower()

    print(f"pipeline_id={pid}")
    print(f"type={state.get('type', '?')}")
    print(f"current_phase={current}")
    if state.get("blocked"):
        print(f"blocked=true reason={state.get('blocked_reason', '?')}")
    if state.get("terminal_state"):
        print(f"terminal_state={state['terminal_state']}")

    # phase 별 최소 인터페이스 사양 (에이전트가 즉시 호출 가능한 명령어)
    PHASE_INTERFACE: Dict[str, Dict[str, Any]] = {
        "pm": {
            "agent": "pm-agent",
            "next_cmd": 'python pipeline.py done --phase pm --report-file step_plan.xml --decomp --clarification --roadmap [--judgment-confirmed]',
            "required_xml": ["<decomposition_audit>", "<step_plan>", "<micro_tasks>"],
        },
        "dev": {
            "agent": "dev-agent",
            "next_cmd": 'python pipeline.py done --phase dev --files "core/x.py,ui/app.py" --report-file dev_handover.xml --scope-declared --scope-manifest scope_manifest.json',
            "required_xml": ["<scope_declaration>", "<impact_analysis>", "<handover>"],
        },
        "qa": {
            "agent": "qa-agent",
            "next_cmd": (
                'PASS: python pipeline.py qa --result PASS --numeric-score N --report-file qa_report.xml\n'
                '        FAIL: python pipeline.py qa --result FAIL --numeric-score N --failure-sig "[category]:[hash]" --report-file qa_report.xml'
            ),
            "required_xml": ["<qa_report>", "<numeric_score>", "<verdict>", "<micro_task_boundary>"],
        },
        "sec": {
            "agent": "security-agent",
            "next_cmd": 'python pipeline.py sec --result PASS --risk LOW|MEDIUM|HIGH | --skip',
            "required_xml": ["<security_audit>", "<risk_level>"],
        },
        "build": {
            "agent": "build-agent",
            "next_cmd": 'python pipeline.py build --exe "dist/app.exe" --report-file dist/build_report.xml  (N/A: --exe "N/A" --skip-reason "meta-task" --user-confirmed)',
            "required_xml": [
                "<build_report>",
                "<section_1_command>",
                "<section_2_spec>",
                "<section_3_output>",
                "<section_4_verification>",
                "<section_5_optimization>",
                "<section_6_qa_mapping>",
                "<status>BUILD SUCCESS</status>",  # BUG-20260508-6198 MT-1 수정 3
            ],
        },
        "harness": {
            "agent": "test-harness-agent",
            # BUG-20260508-D541 MT-5: PASS/FAIL 공통으로 <harness_report> + <test_code> 필수.
            # XML comment 내 태그는 유효하지 않음 (_strip_xml_comments 후 검증).
            "next_cmd": (
                'python pipeline.py harness --score 0..100 --verdict PASS|FAIL '
                '--test-output-file PATH --user-confirmed  '
                '(PASS/FAIL 공통: <harness_report> + <test_code> 필수, XML comment 내 태그 무효)'
            ),
            "required_xml": ["<harness_report>", "<test_code>"],
        },
        "architect": {
            "agent": "prompt-architect-agent",
            "next_cmd": 'python pipeline.py architect --report-file architect_report.xml',
            "required_xml": ["<optimization_report>"],
        },
    }

    spec = PHASE_INTERFACE.get(requested)
    if spec is None:
        print(f"phase={requested}  (no interface spec)")
        sys.exit(0)

    ok, reason = check_gate(state, requested) if requested in GATE_RULES else (True, "")
    print(f"phase={requested}")
    print(f"agent={spec['agent']}")
    print(f"gate={'OK' if ok else 'BLOCKED'}")
    if not ok:
        print(f"gate_reason={reason}")
    print(f"next_cmd={spec['next_cmd']}")
    print(f"required_xml={','.join(spec['required_xml'])}")

    # 직전 완료 phase의 보고서 경로 (있다면 노출)
    try:
        idx = PHASE_ORDER.index(requested)
        if idx > 0:
            prev = PHASE_ORDER[idx - 1]
            prev_info = state["phases"].get(prev, {})
            rf = prev_info.get("report_file")
            sp = prev_info.get("snapshot_path")
            if rf:
                print(f"prev_report_file={rf}")
            if sp:
                print(f"prev_snapshot={sp}")
    except (ValueError, KeyError):
        pass


def _write_acceptance_summary(path: Path, contract: Dict[str, Any], test_set: Dict[str, Any], ready: Dict[str, Any]) -> None:
    modules = contract.get("modules", [])
    tests = test_set.get("tests", [])
    lines = [
        f"# Acceptance Summary - {contract.get('pipeline_id', 'UNKNOWN')}",
        "",
        f"Goal: {contract.get('goal', '')}",
        f"Status: {contract.get('status', 'draft')}",
        f"Ready: {ready.get('ready')}",
        "",
        "## Modules",
    ]
    if isinstance(modules, list) and modules:
        for module in modules:
            if isinstance(module, dict):
                lines.append(f"- {module.get('id', '?')}: {module.get('name', '')}")
    else:
        lines.append("- none")
    lines.extend(["", "## Acceptance Tests"])
    if isinstance(tests, list) and tests:
        for test in tests:
            if isinstance(test, dict):
                lines.append(
                    f"- {test.get('id', '?')} [{test.get('priority', 'P1')}] "
                    f"{test.get('type', '?')} ({test.get('points', 0)}pt)"
                )
    else:
        lines.append("- none")
    if ready.get("blockers"):
        lines.extend(["", "## Blockers"])
        lines.extend(f"- {item}" for item in ready["blockers"])
    if ready.get("warnings"):
        lines.extend(["", "## Warnings"])
        lines.extend(f"- {item}" for item in ready["warnings"])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _load_contract_pair(pid: str) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Path]]:
    from core.contracts import load_json

    paths = _contract_paths(pid)
    contract = load_json(paths["contract"])
    test_set = load_json(paths["test_set"])
    return contract, test_set, paths


def _parse_contract_json_arg(raw: str, base_dir: Path) -> Any:
    if raw.startswith("@"):
        path = Path(raw[1:])
        if not path.is_absolute():
            path = base_dir / path
        try:
            return json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            _die(f"invalid JSON file argument {raw}: {exc}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        _die(f"invalid JSON argument: {exc}. Tip: use @path/to/file.json on PowerShell.")


BEHAVIOR_TEST_TYPES = {
    "command_check",
    "csv_row_match",
    "email_parse_check",
    "excel_row_match",
    "excel_schema_check",
    "file_output_check",
    "json_exact_match",
    "mapping_rule_check",
}
SHALLOW_TEST_TYPES = {"file_exists_check", "exe_launch_check"}
ORACLE_CASE_KINDS = {"normal", "edge", "exception", "error"}
ORACLE_EDGE_CASE_KINDS = {"edge", "exception", "error"}
ORACLE_PLACEHOLDER_STRINGS = {"", "todo", "tbd", "placeholder", "sample", "example", "n/a", "na", "none", "null"}
NON_ORACLE_DELIVERABLE_KINDS = {"doc", "docs", "markdown", "prompt", "analysis", "research", "policy", "config", "configuration"}


def _get_nested(mapping: Dict[str, Any], path: str) -> Any:
    current: Any = mapping
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _has_any_path(mapping: Dict[str, Any], paths: List[str]) -> bool:
    return any(_get_nested(mapping, path) not in (None, "", [], {}) for path in paths)


def _validate_test_semantics(test_set: Dict[str, Any]) -> List[str]:
    blockers: List[str] = []
    tests = test_set.get("tests", [])
    if not isinstance(tests, list):
        return ["test_set.tests must be a list"]

    for index, test in enumerate(tests):
        if not isinstance(test, dict):
            continue
        test_id = str(test.get("id") or f"tests[{index}]")
        test_type = str(test.get("type") or "")
        priority = str(test.get("priority", "P1")).upper()
        given = test.get("given", {})
        when = test.get("when", {})
        then = test.get("then", {})
        if not isinstance(given, dict) or not isinstance(when, dict) or not isinstance(then, dict):
            blockers.append(f"{test_id}: given/when/then must be JSON objects")
            continue

        if test_type == "file_exists_check" and not then.get("path"):
            blockers.append(f"{test_id}: file_exists_check requires then.path")
        elif test_type == "file_output_check" and not then.get("path"):
            blockers.append(f"{test_id}: file_output_check requires then.path")
        elif test_type in {"json_exact_match", "mapping_rule_check", "email_parse_check"}:
            if not _has_any_path(test, ["given.actual", "given.actual_file"]):
                blockers.append(f"{test_id}: {test_type} requires given.actual or given.actual_file")
            if not _has_any_path(test, ["then.expected", "then.expected_file"]):
                blockers.append(f"{test_id}: {test_type} requires then.expected or then.expected_file")
            if priority == "P0" and not _has_any_path(test, ["given.actual_file"]):
                blockers.append(f"{test_id}: P0 behavior tests must use a generated actual_file, not only inline actual")
        elif test_type == "csv_row_match":
            if not _has_any_path(test, ["given.actual_file", "given.file"]):
                blockers.append(f"{test_id}: csv_row_match requires given.actual_file or given.file")
            if not _has_any_path(test, ["then.expected_rows", "then.expected_file"]):
                blockers.append(f"{test_id}: csv_row_match requires then.expected_rows or then.expected_file")
        elif test_type in {"excel_row_match", "excel_schema_check"}:
            if not _has_any_path(test, ["given.actual_file", "given.file"]):
                blockers.append(f"{test_id}: {test_type} requires given.actual_file or given.file")
            if test_type == "excel_row_match" and not _has_any_path(test, ["then.expected_rows", "then.expected_file"]):
                blockers.append(f"{test_id}: excel_row_match requires then.expected_rows or then.expected_file")
            if test_type == "excel_schema_check" and not _has_any_path(test, ["then.expected_columns"]):
                blockers.append(f"{test_id}: excel_schema_check requires then.expected_columns")
        elif test_type == "command_check":
            command = when.get("command")
            if not isinstance(command, list) or not command:
                blockers.append(f"{test_id}: command_check requires non-empty when.command list")
            if not _has_any_path(test, ["then.returncode", "then.stdout_contains", "then.stderr_contains"]):
                blockers.append(f"{test_id}: command_check requires returncode or stdout/stderr assertion")
        elif test_type == "exe_launch_check" and not _has_any_path(test, ["given.exe", "given.path"]):
            blockers.append(f"{test_id}: exe_launch_check requires given.exe or given.path")

    return blockers


def _is_placeholder_scalar(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in ORACLE_PLACEHOLDER_STRINGS
    return False


def _oracle_expected_quality_blockers(name: str, expected_path: Path) -> List[str]:
    blockers: List[str] = []
    try:
        if expected_path.stat().st_size == 0:
            return [f"{name}: oracle expected output is empty"]
    except OSError as exc:
        return [f"{name}: cannot inspect oracle expected output: {exc}"]

    if expected_path.suffix.lower() != ".json":
        return blockers

    try:
        value = json.loads(expected_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return blockers

    if value in ({}, [], "", None):
        blockers.append(f"{name}: oracle expected JSON is empty")
    elif _is_placeholder_scalar(value):
        blockers.append(f"{name}: oracle expected JSON is a placeholder")
    elif isinstance(value, dict) and len(value) == 1 and _is_placeholder_scalar(next(iter(value.values()))):
        blockers.append(f"{name}: oracle expected JSON contains only a placeholder")
    elif isinstance(value, list) and len(value) == 1 and _is_placeholder_scalar(value[0]):
        blockers.append(f"{name}: oracle expected JSON contains only a placeholder")

    return blockers


def _oracle_blockers_are_waivable(blockers: List[str]) -> bool:
    waivable = {
        "oracle_manifest.json is required for three_gate mode",
        "oracle_manifest.json must contain at least one oracle entry",
    }
    return bool(blockers) and all(item in waivable for item in blockers)


def _contract_allows_oracle_waiver(contract: Dict[str, Any]) -> bool:
    task_profile = contract.get("task_profile", {})
    deliverable_kind = ""
    if isinstance(task_profile, dict):
        deliverable_kind = str(task_profile.get("deliverable_kind") or "").strip().lower()
    if deliverable_kind in NON_ORACLE_DELIVERABLE_KINDS:
        return True
    return False


def _oracle_manifest_status(paths: Dict[str, Path]) -> Tuple[List[Dict[str, Any]], List[str]]:
    manifest_path = paths["oracle_manifest"]
    if not manifest_path.exists():
        return [], ["oracle_manifest.json is required for three_gate mode"]
    manifest = _load_json_file(manifest_path)
    entries = manifest.get("oracles", [])
    if not isinstance(entries, list) or not entries:
        return [], ["oracle_manifest.json must contain at least one oracle entry"]

    blockers: List[str] = []
    normalized: List[Dict[str, Any]] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            blockers.append(f"oracle[{index}] must be an object")
            continue
        name = str(entry.get("name") or f"oracle-{index + 1}")
        source = str(entry.get("source") or "").strip().lower()
        case_kind = str(entry.get("case_kind") or "normal").strip().lower()
        if source != "user":
            blockers.append(f"{name}: oracle source must be user")
        if case_kind not in ORACLE_CASE_KINDS:
            blockers.append(f"{name}: oracle case_kind must be one of {sorted(ORACLE_CASE_KINDS)}")
        input_path = Path(str(entry.get("input_path") or ""))
        expected_path = Path(str(entry.get("expected_path") or ""))
        if not input_path.is_absolute():
            input_path = BASE_DIR / input_path
        if not expected_path.is_absolute():
            expected_path = BASE_DIR / expected_path
        if not input_path.exists():
            blockers.append(f"{name}: input_path missing: {input_path}")
            continue
        if not expected_path.exists():
            blockers.append(f"{name}: expected_path missing: {expected_path}")
            continue
        input_hash = _sha256_file(input_path)
        expected_hash = _sha256_file(expected_path)
        if not entry.get("input_sha256"):
            blockers.append(f"{name}: input_sha256 is required")
        elif entry.get("input_sha256") != input_hash:
            blockers.append(f"{name}: input hash mismatch")
        if not entry.get("expected_sha256"):
            blockers.append(f"{name}: expected_sha256 is required")
        elif entry.get("expected_sha256") != expected_hash:
            blockers.append(f"{name}: expected hash mismatch")
        blockers.extend(_oracle_expected_quality_blockers(name, expected_path))
        normalized.append({
            "name": name,
            "case_kind": case_kind,
            "source": source,
            "input_path": _display_path(input_path),
            "expected_path": _display_path(expected_path),
            "input_sha256": input_hash,
            "expected_sha256": expected_hash,
        })
    return normalized, blockers


def _audit_contract_bundle(
    contract: Dict[str, Any],
    test_set: Dict[str, Any],
    paths: Dict[str, Path],
    *,
    allow_no_oracle: bool = False,
    waiver_reason: str = "",
) -> Dict[str, Any]:
    from core.contracts import readiness_report, validate_contract_shape, validate_test_set_shape

    readiness = readiness_report(contract, test_set)
    blockers: List[str] = []
    warnings: List[str] = []
    blockers.extend(validate_contract_shape(contract))
    blockers.extend(validate_test_set_shape(test_set))
    blockers.extend(readiness.get("blockers", []))
    warnings.extend(readiness.get("warnings", []))
    blockers.extend(_validate_test_semantics(test_set))

    tests = [test for test in test_set.get("tests", []) if isinstance(test, dict)]
    p0_tests = [test for test in tests if str(test.get("priority", "P1")).upper() == "P0"]
    behavior_p0 = [test for test in p0_tests if str(test.get("type") or "") in BEHAVIOR_TEST_TYPES]
    shallow_tests = [test for test in tests if str(test.get("type") or "") in SHALLOW_TEST_TYPES]
    if p0_tests and not behavior_p0:
        blockers.append("at least one P0 behavior test is required; shallow smoke tests cannot be the P0 oracle")
    if tests and len(shallow_tests) == len(tests):
        blockers.append("test_set cannot contain only shallow smoke tests")

    oracle_entries, oracle_blockers = _oracle_manifest_status(paths)
    if oracle_entries:
        oracle_case_kinds = {str(entry.get("case_kind") or "normal").lower() for entry in oracle_entries}
        if "normal" not in oracle_case_kinds:
            oracle_blockers.append("oracle_manifest.json requires at least one normal oracle")
        if not (oracle_case_kinds & ORACLE_EDGE_CASE_KINDS):
            oracle_blockers.append("oracle_manifest.json requires at least one edge/exception/error oracle")
    if oracle_blockers and allow_no_oracle:
        if not _contract_allows_oracle_waiver(contract):
            blockers.append("oracle waiver is only allowed for explicitly non-runnable docs/analysis/config work")
            blockers.extend(oracle_blockers)
        elif not _oracle_blockers_are_waivable(oracle_blockers):
            blockers.append("oracle waiver cannot cover malformed, agent-sourced, hashless, or weak oracle entries")
            blockers.extend(oracle_blockers)
        else:
            warnings.append(f"oracle gate waived by user: {waiver_reason or 'no reason provided'}")
    else:
        blockers.extend(oracle_blockers)

    status = "PASS" if not blockers else "FAIL"
    return {
        "schema_version": 1,
        "generated_at": _now(),
        "pipeline_id": contract.get("pipeline_id"),
        "status": status,
        "blockers": blockers,
        "warnings": warnings,
        "metrics": {
            "test_count": len(tests),
            "p0_tests": len(p0_tests),
            "p0_behavior_tests": len(behavior_p0),
            "shallow_tests": len(shallow_tests),
            "oracle_count": len(oracle_entries),
        },
        "oracle_entries": oracle_entries,
        "readiness": readiness,
        "allow_no_oracle": allow_no_oracle,
        "waiver_reason": waiver_reason,
    }


def _contract_audit_passed(paths: Dict[str, Path]) -> bool:
    if not paths["contract_audit"].exists():
        return False
    audit = _load_json_file(paths["contract_audit"])
    return audit.get("status") == "PASS"


def cmd_contract(args: argparse.Namespace) -> None:
    from core.contracts import (
        build_initial_contract,
        build_initial_test_set,
        freeze_bundle,
        load_json,
        readiness_report,
        save_json_atomic,
        validate_contract_shape,
        validate_test_set_shape,
    )
    from core.contracts.schema import utc_now

    action = args.contract_action
    pid, desc, ptype, state = _resolve_pipeline_context(args)
    paths = _contract_paths(pid)

    if action == "init":
        if (paths["contract"].exists() or paths["test_set"].exists()) and not args.force:
            _die(f"contract files already exist for {pid}. Use --force to overwrite.")
        contract = build_initial_contract(pid, desc, ptype)
        test_set = build_initial_test_set(pid)
        save_json_atomic(paths["contract"], contract)
        save_json_atomic(paths["test_set"], test_set)
        if state and state.get("pipeline_id") == pid:
            state["contract_v2"] = {
                "enabled": True,
                "frozen": False,
                "status": "draft",
                "contract_path": str(paths["contract"]),
                "test_set_path": str(paths["test_set"]),
            }
            if getattr(args, "three_gate", False):
                state["external_gates"] = _new_external_gates(enabled=True)
                _log_event(state, "three_gate mode enabled")
            _log_event(state, f"contract_v2 initialized: {paths['root']}")
            _save(state)
        print(GREEN(f"\n[CONTRACT INIT] {pid}"))
        print(f"  contract: {paths['contract']}")
        print(f"  test_set: {paths['test_set']}\n")
        return

    if action in {"add-module", "add-question", "answer", "add-test", "add-oracle", "audit", "ready", "freeze", "show"}:
        contract, test_set = _load_contract_bundle(load_json, paths, pid, action)
    else:
        _die(f"unknown contract action: {action}", exit_code=2)

    if action == "add-module":
        if contract.get("frozen") and not args.force:
            _die("contract is frozen. Use --force only for explicit repair.")
        modules = contract.setdefault("modules", [])
        if not isinstance(modules, list):
            _die("contract.modules is not a list")
        if any(isinstance(item, dict) and item.get("id") == args.id for item in modules):
            _die(f"module id already exists: {args.id}")
        modules.append({
            "id": args.id,
            "name": args.name,
            "inputs": [],
            "outputs": [],
            "acceptance_rules": [],
            "exceptions": [],
        })
        contract["updated_at"] = utc_now()
        save_json_atomic(paths["contract"], contract)
        print(GREEN(f"\n[CONTRACT MODULE ADDED] {args.id}: {args.name}\n"))
        return

    if action == "add-question":
        if contract.get("frozen") and not args.force:
            _die("contract is frozen. Use --force only for explicit repair.")
        questions = contract.setdefault("questions", [])
        if not isinstance(questions, list):
            _die("contract.questions is not a list")
        qid = args.id or f"Q{len(questions) + 1:03d}"
        if any(isinstance(item, dict) and item.get("id") == qid for item in questions):
            _die(f"question id already exists: {qid}")
        answer = args.answer or ""
        options = [item.strip() for item in (args.options or "").split("|") if item.strip()]
        questions.append({
            "id": qid,
            "module": args.module,
            "severity": args.severity.upper(),
            "question": args.question,
            "options": options,
            "answer": answer,
            "resolved": bool(answer),
        })
        contract["updated_at"] = utc_now()
        save_json_atomic(paths["contract"], contract)
        print(GREEN(f"\n[CONTRACT QUESTION ADDED] {qid} ({args.severity.upper()})\n"))
        return

    if action == "answer":
        if contract.get("frozen") and not args.force:
            _die("contract is frozen. Use --force only for explicit repair.")
        questions = contract.get("questions", [])
        if not isinstance(questions, list):
            _die("contract.questions is not a list")
        target = None
        for question in questions:
            if isinstance(question, dict) and question.get("id") == args.id:
                target = question
                break
        if target is None:
            _die(f"question not found: {args.id}")
        target["answer"] = args.answer
        target["resolved"] = True
        target["answered_at"] = utc_now()
        contract["updated_at"] = utc_now()
        save_json_atomic(paths["contract"], contract)
        print(GREEN(f"\n[CONTRACT QUESTION ANSWERED] {args.id}\n"))
        return

    if action == "add-test":
        if test_set.get("frozen") and not args.force:
            _die("test_set is frozen. Use --force only for explicit repair.")
        tests = test_set.setdefault("tests", [])
        if not isinstance(tests, list):
            _die("test_set.tests is not a list")
        if any(isinstance(item, dict) and item.get("id") == args.id for item in tests):
            _die(f"test id already exists: {args.id}")
        given = _parse_contract_json_arg(args.given_json or "{}", paths["root"])
        when = _parse_contract_json_arg(args.when_json or "{}", paths["root"])
        then = _parse_contract_json_arg(args.then_json or "{}", paths["root"])
        tests.append({
            "id": args.id,
            "module": args.module,
            "type": args.test_type,
            "priority": args.priority.upper(),
            "case_kind": args.case_kind,
            "points": args.points,
            "given": given,
            "when": when,
            "then": then,
        })
        test_set["updated_at"] = utc_now()
        save_json_atomic(paths["test_set"], test_set)
        print(GREEN(f"\n[CONTRACT TEST ADDED] {args.id} ({args.test_type})\n"))
        return

    if action == "add-oracle":
        if contract.get("frozen") and not args.force:
            _die("contract is frozen. Use --force only for explicit repair.")
        input_path = Path(args.input)
        expected_path = Path(args.expected)
        if not input_path.is_absolute():
            input_path = BASE_DIR / input_path
        if not expected_path.is_absolute():
            expected_path = BASE_DIR / expected_path
        if not input_path.exists():
            _die(f"oracle input does not exist: {input_path}")
        if not expected_path.exists():
            _die(f"oracle expected output does not exist: {expected_path}")
        manifest = _load_json_file(paths["oracle_manifest"], {
            "schema_version": 1,
            "pipeline_id": pid,
            "created_at": _now(),
            "oracles": [],
        })
        entries = manifest.setdefault("oracles", [])
        if not isinstance(entries, list):
            _die("oracle_manifest.oracles must be a list")
        name = args.name or f"O{len(entries) + 1:03d}"
        if any(isinstance(item, dict) and item.get("name") == name for item in entries):
            _die(f"oracle name already exists: {name}")
        entries.append({
            "name": name,
            "description": args.description or "",
            "source": "user",
            "case_kind": args.case_kind,
            "input_path": _display_path(input_path),
            "expected_path": _display_path(expected_path),
            "input_sha256": _sha256_file(input_path),
            "expected_sha256": _sha256_file(expected_path),
            "added_at": _now(),
        })
        manifest["updated_at"] = _now()
        _write_json(paths["oracle_manifest"], manifest)
        print(GREEN(f"\n[ORACLE ADDED] {name}"))
        print(f"  input:    {_display_path(input_path)}")
        print(f"  expected: {_display_path(expected_path)}\n")
        return

    if action == "audit":
        audit = _audit_contract_bundle(
            contract,
            test_set,
            paths,
            allow_no_oracle=bool(getattr(args, "allow_no_oracle", False)),
            waiver_reason=str(getattr(args, "waiver_reason", "") or ""),
        )
        _write_json(paths["contract_audit"], audit)
        color = GREEN if audit["status"] == "PASS" else RED
        print(color(f"\n[CONTRACT AUDIT {audit['status']}] {pid}"))
        for item in audit["blockers"]:
            print(RED(f"  BLOCKER: {item}"))
        for item in audit["warnings"]:
            print(YELLOW(f"  WARN: {item}"))
        print(f"  report: {paths['contract_audit']}\n")
        sys.exit(0 if audit["status"] == "PASS" else 1)

    if action == "ready":
        report = readiness_report(contract, test_set)
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print(GREEN("\n[CONTRACT READY]" if report["ready"] else "\n[CONTRACT NOT READY]"))
            for item in report["blockers"]:
                print(RED(f"  BLOCKER: {item}"))
            for item in report["warnings"]:
                print(YELLOW(f"  WARN: {item}"))
            print(f"  metrics: {json.dumps(report['metrics'], ensure_ascii=False)}\n")
        sys.exit(0 if report["ready"] else 1)

    if action == "freeze":
        if state and state.get("pipeline_id") == pid:
            state = _ensure_v210_fields(state)
            gates = state.get("external_gates", {})
            if isinstance(gates, dict) and gates.get("enabled") and not args.force:
                if not _contract_audit_passed(paths):
                    _die(
                        "three_gate mode requires PASS contract audit before freeze. "
                        "Run `python pipeline.py contract audit`."
                    )
        try:
            frozen_contract, frozen_test_set, report = freeze_bundle(contract, test_set, force=args.force)
        except ValueError as exc:
            _die(str(exc))
        save_json_atomic(paths["contract"], frozen_contract)
        save_json_atomic(paths["test_set"], frozen_test_set)
        _write_acceptance_summary(paths["summary"], frozen_contract, frozen_test_set, report)
        if state and state.get("pipeline_id") == pid:
            state["contract_v2"] = {
                "enabled": True,
                "frozen": True,
                "status": "frozen",
                "contract_hash": frozen_contract.get("contract_hash"),
                "test_set_hash": frozen_test_set.get("test_set_hash"),
                "contract_path": str(paths["contract"]),
                "test_set_path": str(paths["test_set"]),
                "summary_path": str(paths["summary"]),
            }
            _log_event(state, f"contract_v2 frozen: {frozen_contract.get('contract_hash', '')[:12]}")
            advisory_result = _auto_run_openai_advisory(state, kind="gpt-contract")
            if advisory_result.get("status") == "COMPLETED":
                print(GREEN(f"  [GPT ADVISORY] gpt-contract completed via {OPENAI_ADVISORY_MODEL}"))
            elif advisory_result.get("status") == "SKIPPED":
                print(YELLOW(f"  [GPT ADVISORY] gpt-contract skipped: {advisory_result.get('reason')}"))
            else:
                print(RED(f"  [GPT ADVISORY] gpt-contract error: {advisory_result.get('reason')}"))
            _save(state)
        print(GREEN(f"\n[CONTRACT FROZEN] {pid}"))
        print(f"  contract_hash: {frozen_contract.get('contract_hash')}")
        print(f"  test_set_hash: {frozen_test_set.get('test_set_hash')}")
        print(f"  summary: {paths['summary']}\n")
        return

    if action == "show":
        report = readiness_report(contract, test_set)
        status = {
            "pipeline_id": pid,
            "contract_path": str(paths["contract"]),
            "test_set_path": str(paths["test_set"]),
            "contract_status": contract.get("status"),
            "test_set_status": test_set.get("status"),
            "frozen": bool(contract.get("frozen")) and bool(test_set.get("frozen")),
            "ready": report["ready"],
            "contract_errors": validate_contract_shape(contract),
            "test_set_errors": validate_test_set_shape(test_set),
            "blockers": report["blockers"],
            "warnings": report["warnings"],
        }
        print(json.dumps(status, ensure_ascii=False, indent=2))
        return


def cmd_acceptance(args: argparse.Namespace) -> None:
    from core.acceptance import run_acceptance

    action = args.acceptance_action
    if action != "run":
        _die(f"unknown acceptance action: {action}", exit_code=2)

    pid, _, _, state = _resolve_pipeline_context(args)
    paths = _contract_paths(pid)
    output_path = Path(args.output) if args.output else paths["result"]
    report = run_acceptance(
        contract_path=paths["contract"],
        test_set_path=paths["test_set"],
        project_dir=BASE_DIR,
        output_path=output_path,
    )
    summary = report["summary"]
    print(GREEN(f"\n[ACCEPTANCE {summary['verdict']}]") if summary["verdict"] == "PASS" else RED(f"\n[ACCEPTANCE {summary['verdict']}]"))
    print(f"  score: {summary['score']} / 100")
    print(f"  points: {summary['earned_points']} / {summary['total_points']}")
    print(f"  result: {output_path}")

    if args.record:
        if state is None or state.get("pipeline_id") != pid:
            _die("acceptance --record requires the active pipeline state")
        if not getattr(args, "user_confirmed", False):
            _die("acceptance --record requires --user-confirmed")
        ok, reason = check_gate(state, "harness")
        if not ok:
            _die(f"[GATE BLOCKED] {reason}")
        verdict = str(summary["verdict"])
        state["phases"]["harness"]["status"] = verdict
        state["phases"]["harness"]["completed_at"] = _now()
        state["phases"]["harness"]["evidence"] = f"acceptance_score={summary['score']}"
        state["phases"]["harness"]["report_file"] = str(output_path)
        state["current_phase"] = "architect"
        if verdict == "FAIL":
            state["harness_fail_count"] = int(state.get("harness_fail_count", 0)) + 1
            if state["harness_fail_count"] >= 3:
                state["terminal_state"] = "FAILED"
        else:
            state["harness_fail_count"] = 0
        _log_event(state, f"acceptance {verdict} score={summary['score']}")
        _record_snapshot(state, "harness", None)
        _save(state)
        with TEST_RESULTS_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "id": pid,
                "framework": "ACCEPTANCE_V2",
                "percentage": summary["score"],
                "verdict": verdict,
                "score": summary["earned_points"],
                "max_score": summary["total_points"],
                "result_file": str(output_path),
            }, ensure_ascii=False) + "\n")
        print(GREEN("  recorded to pipeline_state.json"))
        print(f"  next: {YELLOW('python pipeline.py check --phase architect')}\n")
    else:
        print("  not recorded; add --record --user-confirmed to update Phase 7")
        print()

    sys.exit(0 if summary["verdict"] == "PASS" else 1)


def _external_gates_enabled(state: Dict[str, Any]) -> bool:
    state = _ensure_v210_fields(state)
    gates = state.get("external_gates", {})
    return isinstance(gates, dict) and bool(gates.get("enabled"))


def _external_gate_blockers(state: Dict[str, Any]) -> List[str]:
    if not _external_gates_enabled(state):
        return []
    gates = state.get("external_gates", {})
    blockers: List[str] = []
    for gate_name in ("technical", "oracle", "acceptance"):
        info = gates.get(gate_name, {}) if isinstance(gates, dict) else {}
        if not isinstance(info, dict) or info.get("status") != "PASS":
            blockers.append(f"{gate_name} gate must be PASS")
    pid = str(state.get("pipeline_id", ""))
    unresolved = _unresolved_critical_advisories(pid)
    if unresolved:
        blockers.append(f"unresolved GPT advisory CRITICAL findings: {len(unresolved)}")
    return blockers


def _advisory_status_summary(pid: str) -> Dict[str, Any]:
    paths = _contract_paths(pid)
    advisory_root = paths["advisory_root"]
    review_files: List[Dict[str, Any]] = []
    status_counts: Dict[str, int] = {}
    api_key, api_key_source = _openai_api_key()
    if advisory_root.exists():
        for review_path in sorted(advisory_root.glob("*_review.json")):
            data = _load_json_file(review_path, {})
            status = str(data.get("status") or "UNKNOWN")
            status_counts[status] = status_counts.get(status, 0) + 1
            review_files.append({
                "path": _display_path(review_path),
                "kind": data.get("kind"),
                "status": status,
                "model": data.get("model"),
                "generated_at": data.get("generated_at"),
                "api_called": bool(data.get("api_called", status == "COMPLETED")),
                "reason": data.get("reason"),
                "finding_count": len(data.get("findings", [])) if isinstance(data.get("findings"), list) else 0,
            })
    return {
        "api_key_present": bool(api_key),
        "api_key_source": api_key_source,
        "enabled": bool(api_key) and _openai_advisory_enabled(),
        "review_count": len(review_files),
        "api_call_count": sum(1 for item in review_files if item.get("api_called")),
        "status_counts": status_counts,
        "reviews": review_files,
        "unresolved_critical_count": len(_unresolved_critical_advisories(pid)),
    }


def _set_external_gate(
    state: Dict[str, Any],
    gate_name: str,
    status: str,
    *,
    evidence: Optional[str] = None,
    report_file: Optional[str] = None,
    note: Optional[str] = None,
) -> None:
    state["external_gates"] = _ensure_external_gates(state)
    gate = state["external_gates"][gate_name]
    gate["status"] = status
    gate["completed_at"] = _now()
    gate["evidence"] = evidence
    gate["report_file"] = report_file
    if note:
        notes = gate.setdefault("notes", [])
        if isinstance(notes, list):
            notes.append(note)


def _dev_evidence_files(state: Dict[str, Any]) -> List[Path]:
    evidence = str(state.get("phases", {}).get("dev", {}).get("evidence") or "")
    result: List[Path] = []
    for raw in evidence.split(","):
        item = raw.strip()
        if not item or item.upper() == "N/A":
            continue
        path = Path(item)
        if not path.is_absolute():
            path = BASE_DIR / path
        if path.exists() and path.is_file():
            result.append(path)
    return result


def _technical_gate_tool_version(module_name: str, timeout: int) -> Dict[str, Any]:
    command = [sys.executable, "-m", module_name, "--version"]
    try:
        proc = subprocess.run(
            command,
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"command": command, "status": "ERROR", "message": str(exc)}
    return {
        "command": command,
        "status": "PASS" if proc.returncode == 0 else "FAIL",
        "returncode": proc.returncode,
        "stdout": proc.stdout.strip()[-1000:],
        "stderr": proc.stderr.strip()[-1000:],
    }


def _run_technical_gate(state: Dict[str, Any], *, strict_tools: bool = True, timeout: int = 120) -> Dict[str, Any]:
    import py_compile

    evidence_files = _dev_evidence_files(state)
    target_files = [path for path in evidence_files if path.suffix == ".py"]
    has_non_python_evidence = bool(evidence_files) and not target_files
    checks: List[Dict[str, Any]] = []
    failures = 0

    compile_details: List[Dict[str, Any]] = []
    py_compile_failures = 0
    if not target_files:
        status = "SKIP" if has_non_python_evidence else ("FAIL" if strict_tools else "SKIP")
        checks.append({
            "name": "py_compile",
            "status": status,
            "command": [sys.executable, "-m", "py_compile"],
            "message": "non-Python evidence files; py_compile not applicable" if has_non_python_evidence else "no Python evidence files",
            "details": [],
        })
        if strict_tools and not has_non_python_evidence:
            failures += 1
    else:
        for path in target_files:
            command = [sys.executable, "-m", "py_compile", str(path)]
            try:
                py_compile.compile(str(path), doraise=True)
                compile_details.append({"file": _display_path(path), "status": "PASS", "command": command})
            except py_compile.PyCompileError as exc:
                py_compile_failures += 1
                compile_details.append({
                    "file": _display_path(path),
                    "status": "FAIL",
                    "command": command,
                    "message": str(exc),
                })
        failures += py_compile_failures
        checks.append({
            "name": "py_compile",
            "status": "PASS" if py_compile_failures == 0 else "FAIL",
            "command": [sys.executable, "-m", "py_compile", *[str(path) for path in target_files]],
            "details": compile_details,
        })

    optional_tools = [
        ("ruff", ["-m", "ruff", "check", *[str(path) for path in target_files]]),
        ("mypy", ["-m", "mypy", *[str(path) for path in target_files]]),
        ("bandit", ["-m", "bandit", "-q", *[str(path) for path in target_files]]),
    ]
    for module_name, command_tail in optional_tools:
        if not target_files:
            status = "SKIP" if has_non_python_evidence else ("FAIL" if strict_tools else "SKIP")
            if strict_tools and not has_non_python_evidence:
                failures += 1
            checks.append({
                "name": module_name,
                "status": status,
                "command": [sys.executable, *command_tail],
                "message": "non-Python evidence files; Python tool not applicable" if has_non_python_evidence else "no Python evidence files",
            })
            continue
        if importlib.util.find_spec(module_name) is None:
            status = "FAIL" if strict_tools else "SKIP"
            if strict_tools:
                failures += 1
            checks.append({
                "name": module_name,
                "status": status,
                "command": [sys.executable, *command_tail],
                "version": None,
                "message": f"{module_name} is not installed",
            })
            continue
        command = [sys.executable, *command_tail]
        try:
            proc = subprocess.run(
                command,
                cwd=str(BASE_DIR),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            failures += 1
            checks.append({
                "name": module_name,
                "status": "ERROR",
                "command": command,
                "version": _technical_gate_tool_version(module_name, timeout),
                "message": str(exc),
            })
            continue
        status = "PASS" if proc.returncode == 0 else "FAIL"
        if status == "FAIL":
            failures += 1
        checks.append({
            "name": module_name,
            "status": status,
            "command": command,
            "version": _technical_gate_tool_version(module_name, timeout),
            "returncode": proc.returncode,
            "stdout": proc.stdout[-4000:],
            "stderr": proc.stderr[-4000:],
        })

    test_files = list(BASE_DIR.glob("test_*.py")) + list((BASE_DIR / "tests").glob("test_*.py")) if (BASE_DIR / "tests").exists() else list(BASE_DIR.glob("test_*.py"))
    if not test_files:
        checks.append({
            "name": "pytest",
            "status": "SKIP",
            "command": [sys.executable, "-m", "pytest", "-q"],
            "version": _technical_gate_tool_version("pytest", timeout) if importlib.util.find_spec("pytest") is not None else None,
            "message": "no test files found",
        })
    elif importlib.util.find_spec("pytest") is None:
        checks.append({
            "name": "pytest",
            "status": "FAIL" if strict_tools else "SKIP",
            "command": [sys.executable, "-m", "pytest", "-q"],
            "version": None,
            "message": "pytest is not installed",
        })
        if strict_tools:
            failures += 1
    else:
        command = [sys.executable, "-m", "pytest", "-q"]
        try:
            proc = subprocess.run(
                command,
                cwd=str(BASE_DIR),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            failures += 1
            checks.append({
                "name": "pytest",
                "status": "ERROR",
                "command": command,
                "version": _technical_gate_tool_version("pytest", timeout),
                "message": str(exc),
            })
            return {
                "schema_version": 1,
                "generated_at": _now(),
                "pipeline_id": state.get("pipeline_id"),
                "status": "FAIL",
                "evidence_files": [_display_path(path) for path in evidence_files],
                "target_files": [_display_path(path) for path in target_files],
                "strict_tools": strict_tools,
                "checks": checks,
            }
        status = "PASS" if proc.returncode == 0 else "FAIL"
        if status == "FAIL":
            failures += 1
        checks.append({
            "name": "pytest",
            "status": status,
            "command": command,
            "version": _technical_gate_tool_version("pytest", timeout),
            "returncode": proc.returncode,
            "stdout": proc.stdout[-4000:],
            "stderr": proc.stderr[-4000:],
        })

    return {
        "schema_version": 1,
        "generated_at": _now(),
        "pipeline_id": state.get("pipeline_id"),
        "status": "PASS" if failures == 0 else "FAIL",
        "evidence_files": [_display_path(path) for path in evidence_files],
        "target_files": [_display_path(path) for path in target_files],
        "strict_tools": strict_tools,
        "checks": checks,
    }


def cmd_gates(args: argparse.Namespace) -> None:
    action = args.gates_action
    state = _require_state()
    state = _ensure_v210_fields(state)
    pid = str(state.get("pipeline_id"))
    paths = _contract_paths(pid)

    if action == "init":
        state["external_gates"] = _new_external_gates(enabled=True)
        _log_event(state, "three_gate mode enabled")
        _save(state)
        print(GREEN(f"\n[THREE GATE ENABLED] {pid}\n"))
        return

    if action == "status":
        result = {
            "pipeline_id": pid,
            "external_gates": state.get("external_gates"),
            "blockers": _external_gate_blockers(state),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if not _external_gates_enabled(state):
        _die("three_gate mode is not enabled. Run `python pipeline.py gates init` first.")

    if action == "technical":
        strict_tools = not bool(getattr(args, "relaxed_tools", False))
        if bool(getattr(args, "strict_tools", False)):
            strict_tools = True
        result = _run_technical_gate(
            state,
            strict_tools=strict_tools,
            timeout=int(getattr(args, "timeout", 120)),
        )
        if not strict_tools:
            blockers = result.setdefault("blockers", [])
            if isinstance(blockers, list):
                blockers.append("--relaxed-tools runs are diagnostic only and cannot satisfy COMPLETE")
            result["complete_eligible"] = False
            result["status"] = "FAIL"
        else:
            result["complete_eligible"] = True
        _write_json(paths["technical_result"], result)
        _set_external_gate(
            state,
            "technical",
            str(result["status"]),
            evidence="deterministic_tool_gate",
            report_file=str(paths["technical_result"]),
        )
        _log_event(state, f"technical gate {result['status']}")
        _save(state)
        color = GREEN if result["status"] == "PASS" else RED
        print(color(f"\n[TECHNICAL GATE {result['status']}]"))
        print(f"  report: {paths['technical_result']}\n")
        sys.exit(0 if result["status"] == "PASS" else 1)

    if action == "oracle":
        if not getattr(args, "user_confirmed", False):
            _die("oracle gate requires --user-confirmed after user agrees to run Phase 7 external gates")
        technical_gate = state.get("external_gates", {}).get("technical", {})
        if not isinstance(technical_gate, dict) or technical_gate.get("status") != "PASS":
            _die("oracle gate requires technical gate PASS first. Run `python pipeline.py gates technical`.")
        oracle_entries, oracle_blockers = _oracle_manifest_status(paths)
        if oracle_blockers:
            audit = _load_json_file(paths["contract_audit"])
            if (
                audit.get("status") == "PASS"
                and audit.get("allow_no_oracle")
                and _oracle_blockers_are_waivable(oracle_blockers)
            ):
                report = {
                    "schema_version": 1,
                    "generated_at": _now(),
                    "pipeline_id": pid,
                    "verdict": "PASS",
                    "waived": True,
                    "reason": audit.get("waiver_reason") or "user-approved non-oracle task",
                    "oracle_blockers": oracle_blockers,
                }
                _write_json(paths["oracle_result"], report)
                _set_external_gate(
                    state,
                    "oracle",
                    "PASS",
                    evidence="oracle_waived_by_user",
                    report_file=str(paths["oracle_result"]),
                    note=str(report["reason"]),
                )
                _log_event(state, "oracle gate PASS (user waiver)")
                _save(state)
                print(YELLOW("\n[ORACLE GATE PASS] user waiver recorded"))
                print(f"  report: {paths['oracle_result']}\n")
                return
            _die("; ".join(oracle_blockers))
        from core.acceptance import run_acceptance

        report = run_acceptance(
            contract_path=paths["contract"],
            test_set_path=paths["test_set"],
            project_dir=BASE_DIR,
            output_path=paths["oracle_result"],
        )
        report["oracle_entries"] = oracle_entries
        _write_json(paths["oracle_result"], report)
        verdict = str(report.get("summary", {}).get("verdict", "FAIL"))
        _set_external_gate(
            state,
            "oracle",
            "PASS" if verdict == "PASS" else "FAIL",
            evidence="oracle_acceptance",
            report_file=str(paths["oracle_result"]),
        )
        if verdict != "PASS":
            state["phases"]["harness"]["status"] = "FAIL"
            state["phases"]["harness"]["completed_at"] = _now()
            state["phases"]["harness"]["evidence"] = "oracle_gate_failed"
            state["phases"]["harness"]["report_file"] = str(paths["oracle_result"])
            state["current_phase"] = "architect"
        _log_event(state, f"oracle gate {verdict}")
        _save(state)
        color = GREEN if verdict == "PASS" else RED
        print(color(f"\n[ORACLE GATE {verdict}]"))
        print(f"  report: {paths['oracle_result']}\n")
        sys.exit(0 if verdict == "PASS" else 1)

    if action == "accept":
        if not getattr(args, "user_confirmed", False):
            _die("user acceptance gate requires --user-confirmed")
        result = str(args.result).upper()
        if result == "ACCEPT":
            prereq = []
            for gate_name in ("technical", "oracle"):
                gate = state["external_gates"].get(gate_name, {})
                if not isinstance(gate, dict) or gate.get("status") != "PASS":
                    prereq.append(f"{gate_name} gate must be PASS before user ACCEPT")
            if prereq:
                _die("; ".join(prereq))
        report = {
            "schema_version": 1,
            "generated_at": _now(),
            "pipeline_id": pid,
            "result": result,
            "evidence": args.evidence,
            "notes": args.notes or "",
        }
        _write_json(paths["user_validation"], report)
        gate_status = "PASS" if result == "ACCEPT" else "FAIL"
        _set_external_gate(
            state,
            "acceptance",
            gate_status,
            evidence=args.evidence or "user_confirmed",
            report_file=str(paths["user_validation"]),
            note=args.notes,
        )
        state["phases"]["harness"]["status"] = gate_status
        state["phases"]["harness"]["completed_at"] = _now()
        state["phases"]["harness"]["evidence"] = "three_gate_user_acceptance"
        state["phases"]["harness"]["report_file"] = str(paths["user_validation"])
        state["current_phase"] = "architect"
        _log_event(state, f"user acceptance gate {gate_status}")
        _record_snapshot(state, "harness", None)
        _save(state)
        color = GREEN if gate_status == "PASS" else RED
        print(color(f"\n[USER ACCEPTANCE GATE {gate_status}]"))
        print(f"  report: {paths['user_validation']}")
        print(f"  next: {YELLOW('python pipeline.py architect --report-file architect_report.xml')}\n")
        sys.exit(0 if gate_status == "PASS" else 1)

    _die(f"unknown gates action: {action}", exit_code=2)


SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*['\"]?([A-Za-z0-9_\-\.]{12,})"),
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
]


def _redact_for_external_review(text: str) -> str:
    redacted = text
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub(lambda m: m.group(0).replace(m.group(2), "[REDACTED]") if len(m.groups()) >= 2 else "[REDACTED]", redacted)
    return redacted


def _read_review_files(files: List[Path], max_chars: int) -> str:
    chunks: List[str] = []
    remaining = max_chars
    for path in files:
        if remaining <= 0:
            break
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            chunks.append(f"\n--- FILE: {_display_path(path)} (read error: {exc}) ---\n")
            continue
        text = _redact_for_external_review(text)
        snippet = text[:remaining]
        remaining -= len(snippet)
        chunks.append(f"\n--- FILE: {_display_path(path)} ---\n{snippet}\n")
    return "\n".join(chunks)


def _extract_response_output_text(payload: Dict[str, Any]) -> str:
    if isinstance(payload.get("output_text"), str):
        return str(payload["output_text"])
    parts: List[str] = []
    output = payload.get("output", [])
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content", [])
            if isinstance(content, list):
                for content_item in content:
                    if isinstance(content_item, dict) and isinstance(content_item.get("text"), str):
                        parts.append(str(content_item["text"]))
    return "".join(parts)


def _call_openai_advisory(prompt: str, *, model: str, timeout: int) -> Dict[str, Any]:
    api_key, api_key_source = _openai_api_key()
    if not api_key:
        return {"status": "SKIPPED", "reason": "OPENAI_API_KEY is not set", "api_called": False, "findings": []}
    if not _openai_advisory_enabled():
        return {
            "status": "SKIPPED",
            "reason": "ENABLE_GPT_ADVISORY is not 1",
            "api_called": False,
            "api_key_source": api_key_source,
            "findings": [],
        }

    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "summary": {"type": "string"},
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "id": {"type": "string"},
                        "level": {"type": "string", "enum": ["CRITICAL", "HIGH", "MEDIUM", "LOW"]},
                        "file": {"type": "string"},
                        "line": {"type": ["integer", "null"]},
                        "message": {"type": "string"},
                        "recommendation": {"type": "string"},
                    },
                    "required": ["id", "level", "file", "line", "message", "recommendation"],
                },
            },
        },
        "required": ["summary", "findings"],
    }
    body = {
        "model": model,
        "instructions": (
            "You are an advisory code reviewer. Find bugs, security issues, edge cases, "
            "and ways the pipeline could give an undeserved COMPLETE. Output only JSON."
        ),
        "input": prompt,
        "text": {
            "format": {
                "type": "json_schema",
                "name": "gpt_advisory_review",
                "strict": True,
                "schema": schema,
            }
        },
    }
    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw_body = exc.read().decode("utf-8", errors="replace")
        try:
            error_body: Any = json.loads(raw_body)
        except json.JSONDecodeError:
            error_body = {"raw": raw_body[:2000]}
        error = error_body.get("error", {}) if isinstance(error_body, dict) else {}
        code = error.get("code") if isinstance(error, dict) else None
        message = error.get("message") if isinstance(error, dict) else raw_body[:500]
        return {
            "status": "ERROR",
            "reason": f"HTTP {exc.code}: {code or message}",
            "http_status": exc.code,
            "error_body": error_body,
            "api_called": True,
            "api_key_source": api_key_source,
            "findings": [],
        }
    except Exception as exc:
        return {"status": "ERROR", "reason": str(exc), "api_called": True, "api_key_source": api_key_source, "findings": []}

    output_text = _extract_response_output_text(response_payload)
    try:
        parsed = json.loads(output_text)
    except json.JSONDecodeError as exc:
        return {"status": "ERROR", "reason": f"invalid JSON from model: {exc}", "raw_output": output_text, "findings": []}
    if not isinstance(parsed, dict) or not isinstance(parsed.get("findings"), list):
        return {"status": "ERROR", "reason": "model output failed schema post-check", "findings": []}
    parsed["status"] = "COMPLETED"
    parsed["model"] = model
    parsed["generated_at"] = _now()
    parsed["api_called"] = True
    parsed["api_key_source"] = api_key_source
    return parsed


def _advisory_review_files_from_args(state: Dict[str, Any], raw_files: Optional[str]) -> List[Path]:
    if raw_files:
        files = []
        for raw in raw_files.split(","):
            item = raw.strip()
            if not item:
                continue
            path = Path(item)
            if not path.is_absolute():
                path = BASE_DIR / path
            if path.exists() and path.is_file():
                files.append(path)
        return files
    return _dev_evidence_files(state)


def _advisory_review_path(pid: str, kind: str) -> Path:
    safe_kind = re.sub(r"[^A-Za-z0-9_.-]+", "_", kind)
    return _contract_paths(pid)["advisory_root"] / f"{safe_kind}_review.json"


def _auto_run_openai_advisory(
    state: Dict[str, Any],
    *,
    kind: str,
    files: Optional[List[Path]] = None,
    timeout: int = 120,
    max_chars: int = 120000,
) -> Dict[str, Any]:
    """Run the fixed GPT-5.5 advisory path and persist the review result.

    Missing or disabled API keys are recorded as SKIPPED so status cannot be
    confused with "GPT found no issues". Actual model calls happen only when
    OPENAI_API_KEY is present and ENABLE_GPT_ADVISORY=1.
    """
    pid = str(state.get("pipeline_id") or "")
    paths = _contract_paths(pid)
    if kind == "gpt-code":
        review_files = files if files is not None else _dev_evidence_files(state)
        content = _read_review_files(review_files, max_chars)
        prompt = (
            "Review the submitted implementation files. Focus on concrete bugs, security issues, "
            "missing deterministic gates, and any path that lets an LLM mark work complete without external evidence.\n"
            f"{content}"
        )
    elif kind == "gpt-contract":
        contract_files = [
            paths["contract"],
            paths["test_set"],
            paths["oracle_manifest"],
            paths["contract_audit"],
        ]
        review_files = [path for path in contract_files if path.exists()]
        content = _read_review_files(review_files, max_chars)
        prompt = (
            "Review this contract/oracle setup. Find weak tests, missing user oracle coverage, "
            "shallow file-existence checks, and any way the task could pass without proving behavior.\n"
            f"{content}"
        )
    else:
        _die(f"unknown auto advisory kind: {kind}", exit_code=2)

    result = _call_openai_advisory(prompt, model=OPENAI_ADVISORY_MODEL, timeout=timeout)
    result.setdefault("model", OPENAI_ADVISORY_MODEL)
    result.update({
        "schema_version": 1,
        "pipeline_id": pid,
        "kind": kind,
        "auto_run": True,
        "files": [_display_path(path) for path in review_files],
    })
    output_path = _advisory_review_path(pid, kind)
    _write_json(output_path, result)
    status = result.get("status")
    _log_event(state, f"advisory {kind} {status} api_called={bool(result.get('api_called'))}")
    return result


def _load_advisory_resolutions(pid: str) -> Dict[str, Any]:
    return _load_json_file(_contract_paths(pid)["advisory_resolutions"], {"schema_version": 1, "pipeline_id": pid, "items": []})


def _advisory_finding_fingerprint(finding: Dict[str, Any]) -> str:
    canonical = {
        "id": str(finding.get("id") or ""),
        "level": str(finding.get("level") or ""),
        "file": str(finding.get("file") or ""),
        "line": finding.get("line"),
        "message": str(finding.get("message") or ""),
        "recommendation": str(finding.get("recommendation") or ""),
    }
    payload = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _advisory_findings(pid: str) -> List[Dict[str, Any]]:
    paths = _contract_paths(pid)
    findings: List[Dict[str, Any]] = []
    advisory_root = paths["advisory_root"]
    if not advisory_root.exists():
        return findings
    for review_path in advisory_root.glob("*_review.json"):
        review = _load_json_file(review_path)
        for finding in review.get("findings", []):
            if not isinstance(finding, dict):
                continue
            item = dict(finding)
            item["review_file"] = str(review_path)
            item["review_generated_at"] = str(review.get("generated_at") or "")
            item["finding_fingerprint"] = _advisory_finding_fingerprint(item)
            findings.append(item)
    return findings


def _advisory_resolution_matches_finding(resolution: Dict[str, Any], finding: Dict[str, Any]) -> bool:
    if str(resolution.get("id") or "") != str(finding.get("id") or ""):
        return False
    if str(resolution.get("finding_fingerprint") or "") != str(finding.get("finding_fingerprint") or ""):
        return False
    if str(resolution.get("review_file") or "") != str(finding.get("review_file") or ""):
        return False
    resolved_at = str(resolution.get("resolved_at") or "")
    review_generated_at = str(finding.get("review_generated_at") or "")
    if resolved_at and review_generated_at and resolved_at < review_generated_at:
        return False
    return bool(resolution.get("resolution"))


def _unresolved_critical_advisories(pid: str) -> List[Dict[str, Any]]:
    resolutions = _load_advisory_resolutions(pid)
    resolution_items = [
        item for item in resolutions.get("items", [])
        if isinstance(item, dict)
    ]
    unresolved: List[Dict[str, Any]] = []
    for finding in _advisory_findings(pid):
        if finding.get("level") != "CRITICAL":
            continue
        if not any(_advisory_resolution_matches_finding(item, finding) for item in resolution_items):
            unresolved.append(finding)
    return unresolved


def cmd_advisory(args: argparse.Namespace) -> None:
    action = args.advisory_action
    state = _require_state()
    pid = str(state.get("pipeline_id"))
    paths = _contract_paths(pid)

    if action in {"gpt-code", "gpt-contract"}:
        model_arg = getattr(args, "model", None)
        if model_arg and model_arg != OPENAI_ADVISORY_MODEL:
            _die(f"advisory model is fixed to {OPENAI_ADVISORY_MODEL}; got {model_arg!r}")
        model = OPENAI_ADVISORY_MODEL
        if action == "gpt-code":
            files = _advisory_review_files_from_args(state, args.files)
            content = _read_review_files(files, int(args.max_chars))
            prompt = (
                "Review the submitted implementation files. Focus on concrete bugs, security issues, "
                "missing deterministic gates, and any path that lets an LLM mark work complete without external evidence.\n"
                f"{content}"
            )
        else:
            review_files = [
                paths["contract"],
                paths["test_set"],
                paths["oracle_manifest"],
                paths["contract_audit"],
            ]
            files = [path for path in review_files if path.exists()]
            content = _read_review_files(files, int(args.max_chars))
            prompt = (
                "Review this contract/oracle setup. Find weak tests, missing user oracle coverage, "
                "shallow file-existence checks, and any way the task could pass without proving behavior.\n"
                f"{content}"
            )
        result = _call_openai_advisory(prompt, model=model, timeout=int(args.timeout))
        result.setdefault("model", model)
        result.update({
            "schema_version": 1,
            "pipeline_id": pid,
            "kind": action,
            "files": [_display_path(path) for path in files],
        })
        output_path = _advisory_review_path(pid, action)
        _write_json(output_path, result)
        status = result.get("status")
        color = GREEN if status in {"COMPLETED", "SKIPPED"} else RED
        print(color(f"\n[ADVISORY {status}] {action}"))
        print(f"  report: {output_path}")
        if result.get("reason"):
            print(f"  reason: {result['reason']}")
        critical = [item for item in result.get("findings", []) if isinstance(item, dict) and item.get("level") == "CRITICAL"]
        if critical:
            print(RED(f"  unresolved CRITICAL findings require resolution: {len(critical)}"))
        print()
        sys.exit(1 if status == "ERROR" and args.require else 0)

    if action == "resolve":
        review_filter = str(getattr(args, "review_file", "") or "")
        candidates = [
            finding for finding in _advisory_findings(pid)
            if str(finding.get("id") or "") == str(args.id)
            and (not review_filter or _normalize_rel_path(str(finding.get("review_file") or "")) == _normalize_rel_path(review_filter))
        ]
        if not candidates:
            _die(f"advisory finding id not found in current review files: {args.id}")
        if len(candidates) > 1:
            files = sorted({str(item.get("review_file") or "") for item in candidates})
            _die(f"advisory finding id is ambiguous; re-run with --review-file. Matches: {files}")
        finding = candidates[0]
        resolutions = _load_advisory_resolutions(pid)
        items = resolutions.setdefault("items", [])
        if not isinstance(items, list):
            _die("advisory resolutions items must be a list")
        item = {
            "id": args.id,
            "level": finding.get("level"),
            "review_file": finding.get("review_file"),
            "review_generated_at": finding.get("review_generated_at"),
            "finding_fingerprint": finding.get("finding_fingerprint"),
            "resolution": args.resolution,
            "notes": args.notes or "",
            "resolved_at": _now(),
        }
        replaced = False
        for index, existing in enumerate(items):
            if not isinstance(existing, dict):
                continue
            if (
                str(existing.get("id") or "") == str(item["id"])
                and str(existing.get("review_file") or "") == str(item["review_file"])
                and str(existing.get("finding_fingerprint") or "") == str(item["finding_fingerprint"])
            ):
                items[index] = item
                replaced = True
                break
        if not replaced:
            items.append(item)
        _write_json(paths["advisory_resolutions"], resolutions)
        print(GREEN(f"\n[ADVISORY RESOLVED] {args.id} -> {args.resolution}\n"))
        return

    if action == "status":
        unresolved = _unresolved_critical_advisories(pid)
        status = {
            "pipeline_id": pid,
            "summary": _advisory_status_summary(pid),
            "unresolved_critical": unresolved,
            "resolutions": _load_advisory_resolutions(pid),
        }
        print(json.dumps(status, ensure_ascii=False, indent=2))
        sys.exit(1 if unresolved else 0)

    _die(f"unknown advisory action: {action}", exit_code=2)


def cmd_reset(args: argparse.Namespace) -> None:
    state = _load()
    if state:
        HISTORY_DIR.mkdir(exist_ok=True)
        pid     = state.get("pipeline_id", "unknown")
        archive = HISTORY_DIR / f"{pid}_RESET_{_now().replace(':', '-')}.json"
        archive.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        STATE_FILE.unlink()
        print(YELLOW(f"\n  [RESET] 이전 상태 → {archive.name} 보관 후 초기화\n"))
    else:
        print(YELLOW("\n  초기화할 파이프라인 없음\n"))


# ── Tournament commands ───────────────────────────────────────────────────────

def cmd_tournament_start(args: argparse.Namespace) -> None:
    """토너먼트 브랜치 초기화."""
    if args.branches is None:
        print("[ERROR] --branches 파라미터가 필요합니다.")
        raise SystemExit(2)
    if not isinstance(args.branches, str):
        print("[ERROR] --branches 는 문자열이어야 합니다.")
        raise SystemExit(2)

    raw_branches = [b.strip() for b in args.branches.split(",") if b.strip()]
    if len(raw_branches) == 0:
        print("[ERROR] --branches 에 유효한 브랜치 ID가 없습니다.")
        raise SystemExit(2)

    for b in raw_branches:
        _validate_branch(b)  # 원본값(소문자 포함) 그대로 검증 — [A-Z] 미통과 시 exit 2
    branches = raw_branches

    pid = args.pipeline_id
    if pid is None:
        print("[ERROR] --pipeline-id 파라미터가 필요합니다.")
        raise SystemExit(2)
    if not isinstance(pid, str):
        raise TypeError(f"pipeline_id must be str, got {type(pid).__name__}")
    # pipeline_id 경로 인젝션 방어 (negative not allowed: ".." 포함 ID는 허용하지 않음)
    if ".." in Path(pid).parts:
        print(f"[ERROR] Invalid pipeline_id: '{pid}' contains path traversal")
        raise SystemExit(2)

    master = _load_state()
    if master is None:
        master = {}
    if master.get("tournament", {}).get("active"):
        existing_pid = master["tournament"].get("pipeline_id", "?")
        print(f"[ERROR] 이미 활성 토너먼트가 존재합니다: {existing_pid}")
        raise SystemExit(4)

    now = _now()
    master.setdefault("tournament", {})
    master["tournament"] = TournamentMeta(
        active=True,
        pipeline_id=pid,
        branches=branches,
        branch_states={b: "in_progress" for b in branches},
        winner=None,
        started_at=now,
        finalized_at=None,
    )
    _save_state(master)

    # 각 브랜치 state 파일 초기화
    short = pid[-4:] if len(pid) >= 4 else pid
    for b in branches:
        branch_state: Dict[str, Any] = {
            "pipeline_id": f"{pid}-{b}",
            "parent_pipeline_id": pid,
            "branch": b,
            "created_at": now,
            "current_phase": "pm",
            "blocked": False,
            "blocked_reason": None,
            "phases": {p: _empty_phase(p) for p in PHASE_ORDER},
            "event_log": [],
        }
        _save_state_for(branch_state, b)

    print(f"\n  토너먼트 시작: {pid}")
    print(f"  브랜치: {', '.join(branches)}")
    for b in branches:
        print(f"    Branch {b} -> pipeline_state_{short}-{b}.json 생성됨")
    print()


def cmd_tournament_status(args: argparse.Namespace) -> None:
    """토너먼트 진행 현황 출력."""
    if args.pipeline_id is None:
        print("[ERROR] --pipeline-id 파라미터가 필요합니다.")
        raise SystemExit(2)
    if not isinstance(args.pipeline_id, str):
        raise TypeError(f"pipeline_id must be str, got {type(args.pipeline_id).__name__}")

    master = _load_state()
    if master is None:
        master = {}
    t = master.get("tournament")
    if not t or t.get("pipeline_id") != args.pipeline_id:
        print(f"[ERROR] 토너먼트를 찾을 수 없습니다: {args.pipeline_id}")
        raise SystemExit(4)

    active_label = "활성" if t.get("active") else "완료"
    print(f"\n  토너먼트: {t['pipeline_id']}  ({active_label})")
    print(f"  시작: {t.get('started_at', '?')}")
    print()
    print(f"  {'브랜치':<8} {'상태':<20} {'현재 Phase'}")
    print(f"  {'-'*8} {'-'*20} {'-'*15}")

    for b in t.get("branches", []):
        if not isinstance(b, str):
            continue  # allowed: 브랜치 목록의 비정상 항목 방어 (skip non-str entries)
        bs = _load_state_for(b)
        phases = bs.get("phases", {})
        phase_done = [
            k for k, v in phases.items()
            if isinstance(v, dict) and v.get("status") in ("DONE", "PASS", "SKIP")
        ]
        current = phase_done[-1] if phase_done else "대기중"
        status = t.get("branch_states", {}).get(b, "unknown")
        print(f"  {b:<8} {status:<20} {current}")

    winner = t.get("winner")
    if winner:
        print(f"\n  [WINNER] Branch {winner}")
    print()


def cmd_tournament_rank(args: argparse.Namespace) -> None:
    """브랜치 Harness 점수 비교 출력."""
    if args.pipeline_id is None:
        print("[ERROR] --pipeline-id 파라미터가 필요합니다.")
        raise SystemExit(2)
    if not isinstance(args.pipeline_id, str):
        raise TypeError(f"pipeline_id must be str, got {type(args.pipeline_id).__name__}")

    master = _load_state()
    if master is None:
        master = {}
    t = master.get("tournament")
    if not t or t.get("pipeline_id") != args.pipeline_id:
        print(f"[ERROR] 토너먼트를 찾을 수 없습니다: {args.pipeline_id}")
        raise SystemExit(4)

    print(f"\n  토너먼트 순위: {t['pipeline_id']}")
    print()
    print(f"  {'브랜치':<8} {'Harness 점수':<15} {'Build':<10} {'결과'}")
    print(f"  {'-'*8} {'-'*15} {'-'*10} {'-'*12}")

    scores: List[tuple] = []
    for b in t.get("branches", []):
        if not isinstance(b, str):
            continue  # allowed: 비정상 항목 방어 (skip non-str entries)
        bs = _load_state_for(b)
        phases = bs.get("phases", {})
        harness_info = phases.get("harness", {})
        build_info = phases.get("build", {})

        build_ok = isinstance(build_info, dict) and build_info.get("status") not in (None, "FAILED", "PENDING")
        evidence = harness_info.get("evidence") if isinstance(harness_info, dict) else None
        verdict = harness_info.get("verdict") if isinstance(harness_info, dict) else None

        # evidence 형식: "score=N"
        score: Optional[int] = None
        if isinstance(evidence, str) and evidence.startswith("score="):
            try:
                score = int(evidence.split("=", 1)[1])
            except (ValueError, IndexError):
                score = None

        # harness status 필드에서 PASS/FAIL 판정
        harness_status = harness_info.get("status") if isinstance(harness_info, dict) else None

        if not build_ok:
            result = "ELIMINATED (Build FAIL)"
            display_score = "-"
        elif score is None:
            result = "진행중"
            display_score = "-"
        else:
            result = harness_status if harness_status in ("PASS", "FAIL") else "진행중"
            display_score = f"{score}%"
            scores.append((b, score))

        build_label = "OK" if build_ok else "FAIL"
        print(f"  {b:<8} {display_score:<15} {build_label:<10} {result}")

    if len(scores) > 0:
        scores.sort(key=lambda x: x[1], reverse=True)
        winner_branch, winner_score = scores[0]
        print(f"\n  현재 선두: Branch {winner_branch} ({winner_score}%)")
        print(f"  확정하려면: python pipeline.py tournament-finalize --pipeline-id {args.pipeline_id} --winner {winner_branch}")
    print()


def cmd_tournament_finalize(args: argparse.Namespace) -> None:
    """토너먼트 종료 및 승자 확정."""
    if args.winner is None:
        print("[ERROR] --winner 파라미터가 필요합니다.")
        raise SystemExit(2)
    if not isinstance(args.winner, str):
        raise TypeError(f"winner must be str, got {type(args.winner).__name__}")
    # negative not allowed: 단일 대문자가 아닌 winner는 허용하지 않음
    _validate_branch(args.winner)

    if args.pipeline_id is None:
        print("[ERROR] --pipeline-id 파라미터가 필요합니다.")
        raise SystemExit(2)
    if not isinstance(args.pipeline_id, str):
        raise TypeError(f"pipeline_id must be str, got {type(args.pipeline_id).__name__}")

    master = _load_state()
    if master is None:
        master = {}
    t = master.get("tournament")
    if not t or t.get("pipeline_id") != args.pipeline_id:
        print(f"[ERROR] 토너먼트를 찾을 수 없습니다: {args.pipeline_id}")
        raise SystemExit(4)

    if not t.get("active"):
        print("[ERROR] 이미 완료된 토너먼트입니다.")
        raise SystemExit(4)

    branches = t.get("branches", [])
    if not isinstance(branches, list):
        raise TypeError(f"tournament branches must be list, got {type(branches).__name__}")

    if args.winner not in branches:
        print(f"[ERROR] 브랜치 '{args.winner}'는 이 토너먼트에 없습니다: {branches}")
        raise SystemExit(4)

    now = _now()
    t["active"] = False
    t["winner"] = args.winner
    t["finalized_at"] = now
    master["tournament"] = t
    _save_state(master)

    # 모든 브랜치 state 파일을 pipeline_history/ 로 이동
    HISTORY_DIR.mkdir(exist_ok=True)
    pid = args.pipeline_id
    short = pid[-4:] if len(pid) >= 4 else pid
    now_safe = now.replace(":", "-")

    for b in branches:
        if not isinstance(b, str):
            continue  # allowed: 비정상 항목 방어 (skip non-str entries)
        src = BASE_DIR / f"pipeline_state_{short}-{b}.json"
        if src.exists():
            dst = HISTORY_DIR / f"{pid}-{b}_{now_safe}.json"
            src.rename(dst)
            label = "[WINNER]" if b == args.winner else "  [LOSER]"
            print(f"  {label} Branch {b} -> {dst.name}")

    print(f"\n  토너먼트 완료: {pid}")
    print(f"  승자: Branch {args.winner}")
    print(f"  결과는 pipeline_history/ 에 보관됩니다.")
    print()


# ── CLI parser ───────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pipeline",
        description="Work Protocol Pipeline Enforcer — Phase 순서를 기술적으로 강제합니다.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--debug", action="store_true", default=False,
                        help="Show Python traceback for unexpected pipeline errors")
    sub = parser.add_subparsers(dest="command", required=True)

    # new
    p_new = sub.add_parser("new", help="새 파이프라인 시작")
    p_new.add_argument("--type", required=True, choices=["FEAT", "BUG", "IMP", "feat", "bug", "imp"],
                       help="파이프라인 유형 (FEAT/BUG/IMP)")
    p_new.add_argument("--desc", required=True, help="작업 설명")
    p_new.add_argument("--no-dashboard", action="store_true",
                       help="에이전트 오피스 대시보드 자동 시작 비활성화")

    # check
    p_check = sub.add_parser("check", help="phase gate 검증 (exit 1 = 차단)")
    p_check.add_argument("--phase", required=True,
                         choices=PHASE_ORDER, help="진입하려는 phase")

    # done (pm, dev)
    p_done = sub.add_parser("done", help="pm 또는 dev phase 완료")
    p_done.add_argument("--phase", required=True, choices=["pm", "dev"])
    p_done.add_argument("--files", default=None,
                        help="변경 파일 목록 (쉼표 구분, dev 전용)")
    p_done.add_argument("--report-file", default=None,
                        help="PM 출력 파일 경로 (<decomposition_audit>/<step_plan>/<micro_tasks> hard 검증 필수)")
    p_done.add_argument("--branch", metavar="BRANCH", default=None,
                        help="브랜치 ID (A-Z 대문자 1글자). 지정 시 브랜치 state 파일 사용.")
    # MT-1: PM Analysis Gate 플래그 (IMP-20260506-A064)
    p_done.add_argument("--decomp", action="store_true", default=False,
                        help="[pm 전용] decomposition_audit 출력 완료 여부 기록")
    p_done.add_argument("--clarification", action="store_true", default=False,
                        help="[pm 전용] Mandatory Clarification Triggers 판정 완료 여부 기록")
    p_done.add_argument("--roadmap", action="store_true", default=False,
                        help="[pm 전용] User Roadmap Presentation Gate 처리 완료 여부 기록")
    p_done.add_argument("--judgment-confirmed", action="store_true", default=False,
                        help="[pm 전용] decomposition_audit이 AMBIGUOUS였고 "
                             "step_plan에 judgment_calls_resolved 블록이 포함됨을 선언 "
                             "(AMBIGUOUS 외 상황에서는 불필요)")
    # MT-5: Frozen Codebase scope_declaration 선언 플래그 (IMP-20260506-A064)
    p_done.add_argument("--scope-declared", action="store_true", default=False,
                        help="[dev 전용] dev-agent가 <scope_declaration>을 출력했음을 선언")
    p_done.add_argument("--scope-manifest", default=None,
                        help="[dev 전용] 모든 파이프라인에서 필수인 scope_manifest.json 경로")

    # qa
    p_qa = sub.add_parser("qa", help="QA 결과 기록")
    p_qa.add_argument("--result", required=True, choices=["PASS", "FAIL", "pass", "fail"])
    p_qa.add_argument("--agent-id", default=None, help="qa-agent ID (검증용)")
    p_qa.add_argument("--report-file", default=None,
                      help="QA 보고서 파일 경로 (필수 XML 블록 hard 검증, 선택)")
    p_qa.add_argument("--branch", metavar="BRANCH", default=None,
                      help="브랜치 ID (A-Z 대문자 1글자). 지정 시 브랜치 state 파일 사용.")
    # MT-2: QA numeric_score 기록 강제 (IMP-20260506-A064)
    p_qa.add_argument("--numeric-score", default=None, metavar="SCORE",
                      help="QA 수치 점수 0~120. PASS 시 96점 이상 필수 (80%% of 120).")
    # MT-3: Circuit Breaker failure_signature 추적 (IMP-20260506-A064)
    p_qa.add_argument("--failure-sig", default=None, metavar="SIG",
                      help="QA FAIL 시 <failure_signature>[category]:[hash]</failure_signature> 값. "
                           "동일 시그니처 연속 2회 감지 시 RECURRING 경고 출력.")

    # sec
    p_sec = sub.add_parser("sec", help="Security 결과 기록")
    grp = p_sec.add_mutually_exclusive_group(required=True)
    grp.add_argument("--result", choices=["PASS", "BLOCK", "FAIL", "pass", "block", "fail"],
                     help="감사 결과")
    grp.add_argument("--skip", action="store_true", help="네트워크/DB 없음 -생략")
    p_sec.add_argument("--risk", default="LOW",
                       choices=["LOW", "MEDIUM", "HIGH", "SAFE", "low", "medium", "high", "safe"])
    p_sec.add_argument("--branch", metavar="BRANCH", default=None,
                       help="브랜치 ID (A-Z 대문자 1글자). 지정 시 브랜치 state 파일 사용.")

    # build
    p_build = sub.add_parser("build", help="Build 완료 기록")
    p_build.add_argument("--exe", default=None, help="EXE 경로 (N/A = EXE 빌드 대상 아님)")
    p_build.add_argument("--branch", metavar="BRANCH", default=None,
                         help="브랜치 ID (A-Z 대문자 1글자). 지정 시 브랜치 state 파일 사용.")
    # MT-4: BUILD 6-Section Report 파일 경로 (IMP-20260506-A064)
    p_build.add_argument("--report-file", default=None, metavar="PATH",
                         help="build_report.xml 경로 (기본: dist/build_report.xml). "
                              "EXE 빌드 시 파일 존재 + 6-Section XML 블록 hard 검증.")
    p_build.add_argument("--skip-reason", default=None, metavar="REASON",
                         help='--exe "N/A" 사용 시 필수. 예: "meta-task", "streamlit", "power-automate".')
    # MT-1 (IMP-20260507-FC80): N/A 빌드 사용자 확인 hard gate 플래그
    p_build.add_argument(
        "--user-confirmed",
        action="store_true",
        default=False,
        help='N/A 빌드(--exe "N/A") 시 필수. Phase 7 진행 전 사용자 "진행" 응답 확인 완료 플래그. 없으면 빌드 기록 거부.',
    )

    # harness
    p_harness = sub.add_parser("harness", help="Harness 채점 결과 기록")
    p_harness.add_argument("--score", required=True, type=int, help="0~100 점수")
    p_harness.add_argument("--verdict", required=True, choices=["PASS", "FAIL", "pass", "fail"])
    p_harness.add_argument("--branch", metavar="BRANCH", default=None,
                           help="브랜치 ID (A-Z 대문자 1글자). 지정 시 브랜치 state 파일 사용.")
    p_harness.add_argument("--test-output-file", default=None,
                           help="harness-agent 출력 파일 경로. PASS/FAIL 공통 필수. PASS/FAIL 양쪽 모두: <harness_report>(ET 검증) + <test_code> strict unittest evidence gate 필요. 통과 조건: astAsserts>=1, 금지 패턴 없음(__main__/atexit/inspect/os/sys.argv/sys.modules/getattr/setattr 등), runner nonce JSON 일치, executed_assertions>=1, testsRun>=1, failures/errors/skipped/expectedFailures/unexpectedSuccesses==0. <test_code>는 CDATA 권장.")
    # BUG-20260508-6198 MT-1: harness 기록 명령에도 --user-confirmed hard gate 추가
    # hard gate는 상태를 실제로 바꾸는 기록 명령(harness) 안에 위치해야 함.
    p_harness.add_argument(
        "--user-confirmed",
        action="store_true",
        default=False,
        help="Phase 7 진행 전 사용자 '진행' 응답 확인 완료 플래그. 없으면 harness 기록 거부.",
    )

    # contract v2
    p_contract = sub.add_parser("contract", help="Contract/test-set v2 management")
    csub = p_contract.add_subparsers(dest="contract_action", required=True)

    p_contract_init = csub.add_parser("init", help="Initialize task_contract.json and test_set.json")
    p_contract_init.add_argument("--pipeline-id", default=None)
    p_contract_init.add_argument("--desc", default=None)
    p_contract_init.add_argument("--type", default=None, choices=["FEAT", "BUG", "IMP", "feat", "bug", "imp"])
    p_contract_init.add_argument("--force", action="store_true", default=False)
    p_contract_init.add_argument("--three-gate", action="store_true", default=False,
                                 help="Enable external Technical/Oracle/User Acceptance gates for this pipeline")

    p_contract_module = csub.add_parser("add-module", help="Add a module/component to the contract")
    p_contract_module.add_argument("--pipeline-id", default=None)
    p_contract_module.add_argument("--id", required=True)
    p_contract_module.add_argument("--name", required=True)
    p_contract_module.add_argument("--force", action="store_true", default=False)

    p_contract_q = csub.add_parser("add-question", help="Add a Discovery question")
    p_contract_q.add_argument("--pipeline-id", default=None)
    p_contract_q.add_argument("--id", default=None)
    p_contract_q.add_argument("--module", default=None)
    p_contract_q.add_argument("--severity", required=True, choices=["P0", "P1", "P2", "p0", "p1", "p2"])
    p_contract_q.add_argument("--question", required=True)
    p_contract_q.add_argument("--options", default=None, help='Optional pipe-delimited options: "A|B|C"')
    p_contract_q.add_argument("--answer", default=None)
    p_contract_q.add_argument("--force", action="store_true", default=False)

    p_contract_answer = csub.add_parser("answer", help="Record a user answer for a Discovery question")
    p_contract_answer.add_argument("--pipeline-id", default=None)
    p_contract_answer.add_argument("--id", required=True)
    p_contract_answer.add_argument("--answer", required=True)
    p_contract_answer.add_argument("--force", action="store_true", default=False)

    p_contract_test = csub.add_parser("add-test", help="Add an acceptance test case")
    p_contract_test.add_argument("--pipeline-id", default=None)
    p_contract_test.add_argument("--id", required=True)
    p_contract_test.add_argument("--module", required=True)
    p_contract_test.add_argument("--test-type", required=True)
    p_contract_test.add_argument("--priority", default="P1", choices=["P0", "P1", "P2", "p0", "p1", "p2"])
    p_contract_test.add_argument("--case-kind", default="normal", choices=["normal", "edge", "exception", "error"])
    p_contract_test.add_argument("--points", required=True, type=int)
    p_contract_test.add_argument("--given-json", default="{}")
    p_contract_test.add_argument("--when-json", default="{}")
    p_contract_test.add_argument("--then-json", default="{}")
    p_contract_test.add_argument("--force", action="store_true", default=False)

    p_contract_oracle = csub.add_parser("add-oracle", help="Register user-supplied input and expected output files")
    p_contract_oracle.add_argument("--pipeline-id", default=None)
    p_contract_oracle.add_argument("--name", default=None)
    p_contract_oracle.add_argument("--input", required=True, help="User oracle input sample file")
    p_contract_oracle.add_argument("--expected", required=True, help="Expected output file for the sample")
    p_contract_oracle.add_argument("--case-kind", default="normal", choices=["normal", "edge", "exception", "error"])
    p_contract_oracle.add_argument("--description", default=None)
    p_contract_oracle.add_argument("--force", action="store_true", default=False)

    p_contract_audit = csub.add_parser("audit", help="Rule-based Contract v3/three_gate audit before freeze")
    p_contract_audit.add_argument("--pipeline-id", default=None)
    p_contract_audit.add_argument("--allow-no-oracle", action="store_true", default=False,
                                  help="User waiver for non-runnable/docs tasks; records warning, not a score")
    p_contract_audit.add_argument("--waiver-reason", default="")

    p_contract_ready = csub.add_parser("ready", help="Check Definition of Ready")
    p_contract_ready.add_argument("--pipeline-id", default=None)
    p_contract_ready.add_argument("--json", action="store_true", default=False)

    p_contract_freeze = csub.add_parser("freeze", help="Freeze contract and test_set before Dev")
    p_contract_freeze.add_argument("--pipeline-id", default=None)
    p_contract_freeze.add_argument("--force", action="store_true", default=False)

    p_contract_show = csub.add_parser("show", help="Show contract v2 status")
    p_contract_show.add_argument("--pipeline-id", default=None)

    # acceptance v2
    p_acceptance = sub.add_parser("acceptance", help="Run contract-based acceptance harness v2")
    asub = p_acceptance.add_subparsers(dest="acceptance_action", required=True)
    p_acceptance_run = asub.add_parser("run", help="Run frozen test_set.json and compute score")
    p_acceptance_run.add_argument("--pipeline-id", default=None)
    p_acceptance_run.add_argument("--output", default=None)
    p_acceptance_run.add_argument("--record", action="store_true", default=False)
    p_acceptance_run.add_argument("--user-confirmed", action="store_true", default=False)

    # three_gate external gates
    p_gates = sub.add_parser("gates", help="External three-gate status and runners")
    gsub = p_gates.add_subparsers(dest="gates_action", required=True)
    gsub.add_parser("init", help="Enable three_gate mode on the active pipeline")
    gsub.add_parser("status", help="Show external gate state")
    p_gate_tech = gsub.add_parser("technical", help="Run deterministic technical tool gate")
    p_gate_tech.add_argument("--strict-tools", action="store_true", default=False,
                             help="Deprecated no-op; strict tool checks are the default")
    p_gate_tech.add_argument("--relaxed-tools", action="store_true", default=False,
                             help="Allow missing optional tools to be recorded as SKIP instead of FAIL")
    p_gate_tech.add_argument("--timeout", type=int, default=120)
    p_gate_oracle = gsub.add_parser("oracle", help="Run oracle/acceptance gate and record external gate result")
    p_gate_oracle.add_argument("--user-confirmed", action="store_true", default=False)
    p_gate_accept = gsub.add_parser("accept", help="Record user behavior acceptance")
    p_gate_accept.add_argument("--result", required=True, choices=["ACCEPT", "REJECT", "accept", "reject"])
    p_gate_accept.add_argument("--evidence", default=None, help="Output file, screenshot, or report shown to user")
    p_gate_accept.add_argument("--notes", default=None)
    p_gate_accept.add_argument("--user-confirmed", action="store_true", default=False)

    # GPT/OpenAI advisory reviews (non-binding; CRITICAL must be resolved)
    p_advisory = sub.add_parser("advisory", help="External advisory reviews and resolutions")
    advsub = p_advisory.add_subparsers(dest="advisory_action", required=True)
    for name in ("gpt-code", "gpt-contract"):
        p_adv = advsub.add_parser(name, help=f"Run {name} OpenAI advisory review")
        p_adv.add_argument("--files", default=None, help="Comma-separated file list; code review defaults to dev evidence")
        p_adv.add_argument("--model", default=None,
                           help=f"OpenAI review model; fixed to {OPENAI_ADVISORY_MODEL}")
        p_adv.add_argument("--max-chars", type=int, default=120000)
        p_adv.add_argument("--timeout", type=int, default=120)
        p_adv.add_argument("--require", action="store_true", default=False,
                           help="Exit 1 if the advisory API call errors")
    p_adv_resolve = advsub.add_parser("resolve", help="Resolve one GPT advisory finding")
    p_adv_resolve.add_argument("--id", required=True)
    p_adv_resolve.add_argument("--resolution", required=True,
                               choices=["fixed", "oracle_added", "tool_gate_added", "waived", "false_positive"])
    p_adv_resolve.add_argument("--review-file", default=None,
                               help="Required when the same finding id appears in multiple advisory review files")
    p_adv_resolve.add_argument("--notes", default=None)
    advsub.add_parser("status", help="Show unresolved advisory findings")

    # architect
    p_architect = sub.add_parser("architect", help="Architect RCA 완료 기록")
    p_architect.add_argument("--report-file", required=True,
                             help="Architect XML report containing <protocol_evolution_decision>")
    p_architect.add_argument("--branch", metavar="BRANCH", default=None,
                             help="브랜치 ID (A-Z 대문자 1글자). 지정 시 브랜치 state 파일 사용.")

    # check
    # (already defined above; add --branch support)
    p_check.add_argument("--branch", metavar="BRANCH", default=None,
                         help="브랜치 ID (A-Z 대문자 1글자). 지정 시 브랜치 state 파일 사용.")
    # MT-1 (IMP-20260507-4D27): HARNESS GATE — Phase 7 사용자 확인 hard gate 플래그
    # AskUserQuestion으로 사용자 "진행" 응답을 받은 후에만 이 플래그를 추가할 수 있다.
    p_check.add_argument(
        "--user-confirmed",
        action="store_true",
        default=False,
        help="--phase harness 전용. 사용자 Phase 7 진행 확인 완료 플래그. 없으면 harness 진입 차단.",
    )

    # status
    sub.add_parser("status", help="현재 파이프라인 상태 출력")

    # interface (token-saver: agent spawn 시 최소 컨텍스트 출력)
    p_iface = sub.add_parser("interface", help="현재 phase의 명령어 시그니처와 게이트 상태만 출력")
    p_iface.add_argument("--phase", choices=PHASE_ORDER, default=None,
                         help="조회할 phase (생략 시 current_phase)")

    # log
    p_log = sub.add_parser("log", help="이벤트 로그 메시지 추가")
    p_log.add_argument("--message", required=True, help="기록할 메시지")

    # unblock
    sub.add_parser("unblock", help="파이프라인 차단 해제")

    # list
    sub.add_parser("list", help="파이프라인 이력 출력")

    # reset
    sub.add_parser("reset", help="현재 상태 초기화 (긴급용)")

    # terminate (BUG-20260507-C2E2 MT-3)
    sub.add_parser("terminate", help="파이프라인 명시적 종료 (TERMINATED terminal state 기록)")

    # tournament-start
    p_ts = sub.add_parser("tournament-start", help="토너먼트 브랜치 초기화")
    p_ts.add_argument("--pipeline-id", required=True, help="토너먼트 파이프라인 ID")
    p_ts.add_argument("--branches", required=True, help="쉼표 구분 브랜치 ID (예: A,B,C)")

    # tournament-status
    p_tstatus = sub.add_parser("tournament-status", help="토너먼트 진행 현황")
    p_tstatus.add_argument("--pipeline-id", required=True, help="토너먼트 파이프라인 ID")

    # tournament-rank
    p_tr = sub.add_parser("tournament-rank", help="브랜치 Harness 점수 비교")
    p_tr.add_argument("--pipeline-id", required=True, help="토너먼트 파이프라인 ID")

    # tournament-finalize
    p_tf = sub.add_parser("tournament-finalize", help="토너먼트 종료 및 승자 확정")
    p_tf.add_argument("--pipeline-id", required=True, help="토너먼트 파이프라인 ID")
    p_tf.add_argument("--winner", required=True, help="승자 브랜치 ID (A-Z 대문자 1글자)")

    return parser


COMMAND_MAP = {
    "new":                  cmd_new,
    "check":                cmd_check,
    "done":                 cmd_done,
    "qa":                   cmd_qa,
    "sec":                  cmd_sec,
    "build":                cmd_build,
    "harness":              cmd_harness,
    "contract":             cmd_contract,
    "acceptance":           cmd_acceptance,
    "gates":                cmd_gates,
    "advisory":             cmd_advisory,
    "architect":            cmd_architect,
    "status":               cmd_status,
    "interface":            cmd_interface,
    "log":                  cmd_log,
    "unblock":              cmd_unblock,
    "terminate":            cmd_terminate,
    "list":                 cmd_list,
    "reset":                cmd_reset,
    "tournament-start":     cmd_tournament_start,
    "tournament-status":    cmd_tournament_status,
    "tournament-rank":      cmd_tournament_rank,
    "tournament-finalize":  cmd_tournament_finalize,
}


EXPECTED_CLI_EXCEPTIONS = (ValueError, FileNotFoundError, json.JSONDecodeError, OSError)


def _main_impl() -> None:
    parser = build_parser()
    args   = parser.parse_args()
    fn     = COMMAND_MAP.get(args.command)
    if fn is None:
        parser.print_help()
        sys.exit(1)
    fn(args)


def main() -> None:
    debug = "--debug" in sys.argv[1:] or os.environ.get("PIPELINE_DEBUG") == "1"
    try:
        _main_impl()
    except EXPECTED_CLI_EXCEPTIONS as exc:
        if debug:
            raise
        print(RED(f"\n[PIPELINE ERROR] {exc}"), file=sys.stderr)
        print("  Re-run with --debug for a Python traceback.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
