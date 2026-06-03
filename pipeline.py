#!/usr/bin/env python3
"""Work Protocol Pipeline Enforcer.

현재 `/Task` 파이프라인의 완료 조건은 숫자 점수가 아니라 아래 trust chain입니다.

    local pipeline.py -> agent receipts -> GitHub Actions -> CODEOWNERS -> human ACCEPT

세션 언어 규칙: 사용자에게 보이는 모든 메시지는 한국어로 작성합니다.
최신 상태 확인: `python pipeline.py status` — 현재 파이프라인 상태를 표시합니다.

핵심 기능:
  - PM/Dev/QA/Build phase receipt와 GitHub phase attestation 검증
  - PM micro-task 분해, module design/dev/qa, integration gate 강제
  - Technical/Oracle/GitHub CI/User Acceptance external gate 강제
  - Execution Profile(Fast Path)로 단순 업무의 불필요한 반복 축소
  - Output Registry로 최종 사용자가 열어볼 결과물 링크 관리
  - Failure Packet으로 실패 gate의 수리 담당자와 증거 파일 기록

현재 `pipeline.py harness --score ...`는 완료 경로가 아니며 CLI에서 차단됩니다.

대표 사용법:
    python pipeline.py new --type BUG --desc "버튼 작동 안 함"
    python pipeline.py status
    python pipeline.py check --phase dev
    python pipeline.py agent start --phase pm_planner
    python pipeline.py agent finish --run-id <planner_run_id> --token <token> --output-file step_plan.xml
    python pipeline.py agent start --phase pipeline_manager
    python pipeline.py agent finish --run-id <manager_run_id> --token <token> --output-file manager_handoff.xml
    python pipeline.py done --phase pm --report-file step_plan.xml --decomp --clarification --roadmap --planner-run-id <planner_run_id> --manager-run-id <manager_run_id> --manager-report manager_handoff.xml
    # Legacy PM (구 상태 호환): done --phase pm --report-file step_plan.xml --decomp --clarification --roadmap --agent-run-id <pm_run_id>
    python pipeline.py done --phase dev --files "core/ai_engine.py,ui/app.py" --report-file dev_handover.xml --scope-declared --scope-manifest scope_manifest.json --agent-run-id <run_id>
    python pipeline.py qa --result PASS --numeric-score 110 --report-file qa_report.xml --agent-run-id <run_id>
    python pipeline.py qa --result FAIL --numeric-score 70 --failure-sig "AL:a1b2c3d4" --report-file qa_report.xml --agent-run-id <run_id>
    python pipeline.py sec --result PASS --risk LOW
    python pipeline.py sec --skip
    python pipeline.py build --exe "dist/SmartNotepad.exe" --report-file dist/build_report.xml --agent-run-id <run_id>
    python pipeline.py build --exe "N/A" --skip-reason "meta-task" --agent-run-id <run_id>
    python pipeline.py contract init
    python pipeline.py module design --mt-id MT-1 --report-file module_design_MT-1.xml
    python pipeline.py module dev --mt-id MT-1 --files "core/ai_engine.py" --report-file module_handover_MT-1.xml --scope-manifest scope_manifest_MT-1.json
    python pipeline.py module qa --mt-id MT-1 --result PASS --report-file module_qa_MT-1.xml
    python pipeline.py module integrate --result PASS --report-file integration_report.xml
    python pipeline.py gates prepare-phase --phase pm
    git add -f .pipeline/phase_attestation_request.json .pipeline/phase_evidence
    python pipeline.py gates phase-ci --phase pm --repo hojiyong2-commits/Pipeline
    python pipeline.py gates technical
    python pipeline.py gates oracle
    python pipeline.py gates github-ci --repo hojiyong2-commits/Pipeline
    python pipeline.py outputs add --kind report --path report.md --label "최종 보고서"
    python pipeline.py gates request-accept --evidence output.png
    python pipeline.py gates accept --result ACCEPT --evidence output.png --acceptance-code ACCEPT-<pid>-<nonce>
    python pipeline.py advisory status
    python pipeline.py architect --report-file architect_report.xml
    python pipeline.py terminate
    python pipeline.py list
    python pipeline.py log --message "rate limit으로 QA 대기 중"
"""

import argparse
import json
import logging
import sys
import os
import xml.etree.ElementTree as ET

def _force_utf8_stdio() -> None:
    """Force UTF-8 CLI output on Windows cp949/cmd.exe/redirected streams."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


_force_utf8_stdio()

import base64
import hashlib
import importlib.util
import secrets
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, NoReturn, Optional, Set, Tuple, TypedDict
import re
import socket
import subprocess
import tempfile
import shutil
import urllib.error
import urllib.parse
import urllib.request
import io
import zipfile

QA_MAX_SCORE = 120
QA_PASS_RATIO = 0.8
QA_PASS_THRESHOLD = int(QA_MAX_SCORE * QA_PASS_RATIO)


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
    forbidden_os_attrs = {
        "system", "popen", "spawnl", "spawnle", "spawnlp", "spawnlpe",
        "spawnv", "spawnve", "spawnvp", "spawnvpe",
        "execv", "execve", "execvp", "execvpe",
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
                if root == "os" and func.attr in forbidden_os_attrs:
                    return f"FORBIDDEN: os.{func.attr}() can bypass deterministic test evidence"
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


_DESIGN_CONFIRMATION_BANNED_TERMS = (
    "todo",
    "tbd",
    "n/a",
    "placeholder",
    "sample",
    "알아서",
    "적당히",
    "아무거나",
    "추후",
    "모름",
    "확정 안됨",
)


def _require_design_text(element: Any, path: str, label: str, min_len: int = 10) -> str:
    value = _child_text(element, path)
    if len(value) < min_len:
        _die(f"[PM DESIGN GATE] {label} must be clear and specific: <{path}>")
    lowered = value.lower()
    for term in _DESIGN_CONFIRMATION_BANNED_TERMS:
        if term in lowered:
            _die(f"[PM DESIGN GATE] {label} contains vague placeholder text: {term}")
    return value


def _require_design_true(element: Any, path: str, label: str) -> None:
    value = _child_text(element, path).strip().lower()
    if value not in {"true", "yes", "y", "1", "confirmed", "확인", "완료"}:
        _die(f"[PM DESIGN GATE] {label} must be true/confirmed: <{path}>")


# ─── Structured AC Tracking SSoT (IMP-20260602-1ABE MT-1) ────────────────────
# [Purpose]: PM step_plan.xml의 <acceptance_criteria> 구조화 블록을 파싱/검증하고,
#   pipeline 전 phase에서 AC가 끊기지 않고 추적되도록 한다.
# [Assumptions]: AC id는 AC-숫자 형식. micro_task의 covers_ac 또는 covers_iqr이
#   PM step_plan에 존재. legacy 파이프라인은 acceptance_criteria 블록이 없을 수
#   있고 그 경우에는 structured AC 검증을 건너뛴다 (하위 호환).
# [Vulnerability & Risks]: 추상 AC 차단 패턴은 frozenset 단순 매칭이므로
#   완전 일치/공백 trim만 검사한다. "정상 동작 + 구체값" 같은 결합 문구는
#   허용하므로 단독 추상 문구만 차단한다.
# [Improvement]: 추후 fuzzy matching, 문장 임베딩 기반 의미 추출, AC 자동
#   품질 점수화 등을 추가할 수 있다.
ABSTRACT_AC_PATTERNS: frozenset = frozenset({
    "정상 동작", "테스트 통과", "문제 없음", "잘 처리됨", "오류 없음",
    "사용자 요구 반영", "작동", "동작", "works", "working", "implemented",
    "기능 구현", "완료", "done", "finished",
})


def _parse_structured_ac(step_plan_root: Any) -> List[Dict[str, Any]]:
    """step_plan.xml의 <acceptance_criteria>/<criterion> 블록을 파싱한다.

    Args:
        step_plan_root: ElementTree element (step_plan 또는 root XML element).
    Returns:
        list[dict] — 각 dict는 ac_id, requirement, must_verify, source,
        user_visible, expected_evidence 키를 포함한다.
        acceptance_criteria 블록이 없으면 빈 리스트 반환.
    Raises:
        TypeError: step_plan_root가 None인 경우.
    """
    if step_plan_root is None:
        raise TypeError("step_plan_root must not be None")

    ac_root = step_plan_root.find("acceptance_criteria")
    if ac_root is None:
        return []

    structured: List[Dict[str, Any]] = []
    for crit in ac_root.findall("criterion"):
        # ac_id: id 속성 또는 ac_id 속성
        ac_id = (crit.get("id") or crit.get("ac_id") or "").strip()
        # requirement: text 자식 요소 또는 requirement 자식 요소
        text_el = crit.find("text")
        requirement_el = crit.find("requirement")
        if text_el is not None and text_el.text:
            requirement = text_el.text.strip()
        elif requirement_el is not None and requirement_el.text:
            requirement = requirement_el.text.strip()
        else:
            # 직접 텍스트만 있는 경우
            requirement = (crit.text or "").strip()

        # must_verify: 기본 true
        must_verify_raw = (crit.get("must_verify") or "true").strip().lower()
        must_verify = must_verify_raw in ("true", "yes", "y", "1")

        # source: 기본 "pm"
        source = (crit.get("source") or "pm").strip()

        # user_visible: 기본 true
        user_visible_raw = (crit.get("user_visible") or "true").strip().lower()
        user_visible = user_visible_raw in ("true", "yes", "y", "1")

        # expected_evidence: 속성 또는 자식 요소
        expected_evidence = (crit.get("expected_evidence") or "").strip()
        if not expected_evidence:
            evidence_el = crit.find("expected_evidence")
            if evidence_el is not None and evidence_el.text:
                expected_evidence = evidence_el.text.strip()

        structured.append({
            "ac_id": ac_id,
            "requirement": requirement,
            "must_verify": must_verify,
            "source": source,
            "user_visible": user_visible,
            "expected_evidence": expected_evidence,
        })

    return structured


def _validate_structured_ac_block(
    structured_ac: List[Dict[str, Any]],
    micro_tasks: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """structured AC 8개 검증 규칙 적용.

    Args:
        structured_ac: _parse_structured_ac()가 반환한 리스트.
        micro_tasks: PM step_plan의 micro_tasks 리스트 (covers_ac/covers_iqr 포함 가능).
    Returns:
        {"valid": True, "ac_count": N, "mt_count": N} 또는
        {"valid": False, "error": "[AC GATE] ..."}
    """
    if structured_ac is None:
        return {"valid": False, "error": "[AC GATE] structured_ac is None"}
    if micro_tasks is None:
        return {"valid": False, "error": "[AC GATE] micro_tasks is None"}

    # 규칙 1: AC 없음
    if not structured_ac:
        return {"valid": False, "error": "[AC GATE] step_plan에 criterion 요소가 없습니다"}

    # 규칙 2, 3, 4: 각 AC 검증
    seen_ids: set = set()
    ac_id_pattern = re.compile(r"^AC-\d+$")
    valid_ac_ids: set = set()
    for ac in structured_ac:
        ac_id = ac.get("ac_id", "")
        requirement = ac.get("requirement", "")

        # 규칙 2: AC id 형식
        if not ac_id_pattern.match(ac_id):
            return {
                "valid": False,
                "error": f"[AC GATE] AC id 형식 오류: {ac_id} — AC-숫자 형식이어야 합니다",
            }

        # 규칙 3: 중복 AC id
        if ac_id in seen_ids:
            return {"valid": False, "error": f"[AC GATE] AC id 중복: {ac_id}"}
        seen_ids.add(ac_id)
        valid_ac_ids.add(ac_id)

        # 규칙 4: 추상 AC 단독 (requirement 전체가 ABSTRACT_AC_PATTERNS 중 하나)
        stripped = requirement.strip()
        if stripped in ABSTRACT_AC_PATTERNS:
            return {
                "valid": False,
                "error": (
                    f"[AC GATE] AC-{ac_id.replace('AC-', '')} 문구가 추상적입니다: "
                    f"'{stripped}' — 구체적인 성공 조건으로 바꾸세요"
                ),
            }

    # 규칙 5, 6: MT covers_ac 검증
    mt_covers_ac: Dict[str, List[str]] = {}
    for mt in micro_tasks:
        mt_id = str(mt.get("id", ""))
        covers_ac_raw = mt.get("covers_ac")
        covers_iqr_raw = mt.get("covers_iqr")

        # covers_ac, covers_iqr이 문자열이면 콤마 분리, 리스트면 그대로
        def _normalize_covers(value: Any) -> List[str]:
            if value is None:
                return []
            if isinstance(value, list):
                return [str(v).strip() for v in value if str(v).strip()]
            if isinstance(value, str):
                return [v.strip() for v in value.split(",") if v.strip()]
            return []

        covers_ac_list = _normalize_covers(covers_ac_raw)
        covers_iqr_list = _normalize_covers(covers_iqr_raw)
        mt_covers_ac[mt_id] = covers_ac_list

        # 규칙 5: MT에 covers_ac도 covers_iqr도 없음 → 차단
        if not covers_ac_list and not covers_iqr_list:
            return {"valid": False, "error": f"[AC GATE] MT-{mt_id.replace('MT-', '')}에 covers_ac가 없습니다"}

        # 규칙 6: covers_ac의 모든 id가 valid_ac_ids에 있어야 함
        for ac_id in covers_ac_list:
            if ac_id not in valid_ac_ids:
                return {
                    "valid": False,
                    "error": (
                        f"[AC GATE] MT-{mt_id.replace('MT-', '')}.covers_ac에 "
                        f"알 수 없는 AC id: {ac_id}"
                    ),
                }

    # 규칙 7: must_verify=true AC가 어떤 MT covers_ac에도 없으면 차단
    all_covered_ac: set = set()
    for cov in mt_covers_ac.values():
        all_covered_ac.update(cov)

    for ac in structured_ac:
        ac_id = ac.get("ac_id", "")
        if ac.get("must_verify") and ac_id not in all_covered_ac:
            return {
                "valid": False,
                "error": (
                    f"[AC GATE] AC-{ac_id.replace('AC-', '')} (must_verify=true)이 "
                    "어떤 MT와도 연결되지 않았습니다"
                ),
            }

    return {"valid": True, "ac_count": len(structured_ac), "mt_count": len(micro_tasks)}


def _validate_pm_design_confirmation(step_plan: Any, micro_tasks: List[Dict[str, Any]]) -> Dict[str, Any]:
    confirmation = step_plan.find("design_confirmation")
    if confirmation is None:
        _die(
            "[PM DESIGN GATE] <design_confirmation> is required inside <step_plan>. "
            "PM must show the module split, explain tradeoffs in easy Korean, and record the user's answer before Dev."
        )

    _require_design_true(confirmation, "module_split_presented", "module split presentation")
    _require_design_true(confirmation, "module_split_user_confirmed", "module split user confirmation")
    _require_design_true(confirmation, "low_value_questions_filtered", "low-value question filter")
    filter_summary = _require_design_text(
        confirmation,
        "filter_summary",
        "low-value question filter summary",
        min_len=20,
    )

    maintenance_priority = _child_text(confirmation, "maintenance_priority")
    if "maintain" not in maintenance_priority.lower() and "유지보수" not in maintenance_priority:
        _die(
            "[PM DESIGN GATE] <maintenance_priority> must explicitly say maintainability comes first "
            "(for example: maintainability_first or 유지보수성 우선)."
        )

    questions_root = confirmation.find("decision_questions")
    if questions_root is None:
        _die("[PM DESIGN GATE] <decision_questions> is required inside <design_confirmation>")
    questions = questions_root.findall("question")
    if not questions:
        _die("[PM DESIGN GATE] at least one user-visible decision question is required")

    allowed_priorities = {"P0", "P1"}
    summaries: List[Dict[str, Any]] = []
    has_module_split_question = False
    known_mt_ids = {str(item.get("id")) for item in micro_tasks if item.get("id")}

    for question in questions:
        qid = str(question.get("id") or _child_text(question, "id")).strip()
        if not qid:
            _die("[PM DESIGN GATE] every decision question requires id")
        priority = str(question.get("priority") or _child_text(question, "priority")).strip().upper()
        if priority not in allowed_priorities:
            _die(
                f"[PM DESIGN GATE] {qid} priority must be P0 or P1. "
                "P2/internal implementation preferences must be filtered, not asked to the user."
            )
        category = str(question.get("category") or _child_text(question, "category")).strip()
        if not category:
            _die(f"[PM DESIGN GATE] {qid} requires category")
        if category == "module_split":
            has_module_split_question = True

        mt_id = str(question.get("mt_id") or _child_text(question, "mt_id")).strip()
        if mt_id and known_mt_ids and mt_id not in known_mt_ids:
            _die(f"[PM DESIGN GATE] {qid} references unknown mt_id: {mt_id}")

        question_text = _require_design_text(
            question,
            "user_facing_question",
            f"{qid} user-facing question",
            min_len=16,
        )
        if not any(marker in question_text for marker in ("?", "까요", "선택", "확인")):
            _die(f"[PM DESIGN GATE] {qid} must be phrased as a clear user decision question")

        _evidence = _require_design_text(question, "evidence", f"{qid} evidence", min_len=12)
        _why = _require_design_text(question, "why_it_matters", f"{qid} why_it_matters", min_len=20)
        recommended = _require_design_text(
            question,
            "recommended_option",
            f"{qid} recommended_option",
            min_len=1,
        )
        user_answer = _require_design_text(question, "user_answer", f"{qid} user_answer", min_len=2)

        options_root = question.find("options")
        if options_root is None:
            _die(f"[PM DESIGN GATE] {qid} requires <options>")
        options = options_root.findall("option")
        if len(options) < 2:
            _die(f"[PM DESIGN GATE] {qid} requires at least two options with clear tradeoffs")

        option_ids: List[str] = []
        for option in options:
            option_id = str(option.get("id") or _child_text(option, "id")).strip()
            if not option_id:
                _die(f"[PM DESIGN GATE] {qid} every option requires id")
            if option_id in option_ids:
                _die(f"[PM DESIGN GATE] {qid} duplicate option id: {option_id}")
            option_ids.append(option_id)
            _require_design_text(option, "label", f"{qid} option {option_id} label", min_len=4)
            _require_design_text(option, "benefit", f"{qid} option {option_id} benefit", min_len=12)
            _require_design_text(option, "cost", f"{qid} option {option_id} cost", min_len=12)

        if recommended not in option_ids:
            _die(
                f"[PM DESIGN GATE] {qid} recommended_option must match one option id "
                f"({', '.join(option_ids)})"
            )

        summaries.append({
            "id": qid,
            "priority": priority,
            "category": category,
            "mt_id": mt_id or None,
            "recommended_option": recommended,
            "option_count": len(options),
            "user_answer": user_answer,
        })

    if not has_module_split_question:
        _die(
            "[PM DESIGN GATE] a module_split decision question is always required, "
            "even when the PM recommends a single MT."
        )

    return {
        "validated_at": _now(),
        "maintenance_priority": maintenance_priority,
        "filter_summary": filter_summary,
        "question_count": len(summaries),
        "questions": summaries,
    }


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
    ".pipeline",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "venv",
    "env",
    "htmlcov",
    # IMP-20260519-E979: pipeline_outputs는 배포 산출물 저장소이며 scope gate 대상이 아님
    "pipeline_outputs",
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
    "integration_report.xml",
    "stop_signal.json",
    "test_results.jsonl",
    "test_results_v3.jsonl",
    # IMP-20260519-E979: Codex review 인프라 파일 — pipeline.py review codex-record 명령이 갱신하는 파일.
    # PM done 이후 review 기록 시 scope gate 충돌을 방지하기 위해 제외.
    "codex_review_result.json",
    "codex_run_raw.json",
    # IMP-20260520-D0BB: Pipeline runtime artifact — gates/consistency 명령이 자동 생성·삭제하는 파일.
    # 이전 파이프라인 실행 시 존재했던 파일이 PM snapshot에 포함되어 scope gate 오탐을 유발하지 않도록 제외.
    "failure_packet.json",
    "protocol_consistency_result.json",
    # IMP-20260522-0C83: Pipeline Manager 출력 파일 — PM done 이후 LF 정규화 등으로
    # 파일이 갱신될 수 있으므로 dev scope gate 오탐을 방지하기 위해 제외.
    "manager_handoff.xml",
    # IMP-20260528-3898: gates accept 완료 시 pipeline.py가 자동 갱신하는 런타임 파일.
    # PM snapshot 이후 accept 또는 다른 파이프라인 완료 시 변경되어 scope gate 오탐을 유발함.
    "human_acceptance_packet.md",
    # BUG-20260529-40C9 MT-2: golden task 실행 시 생성되는 런타임 파일.
    # 개발·테스트 중 golden run으로 인해 생성/삭제될 수 있으므로 scope gate 오탐 방지.
    "golden_failure_packet.json",
    # IMP-20260603-2E3D: gates request-accept가 생성하는 런타임 파일.
    # PM snapshot 이후 nonce 발급 시 생성되거나 갱신되므로 dev scope gate 오탐 방지.
    "acceptance_request.json",
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
    if name.startswith("module_") and name.endswith(".xml"):
        return False
    if "scope_manifest" in name and name.endswith(".json"):
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
    # 구 스냅샷에 포함됐지만 현재 제외 대상인 파일은 비교에서 제거.
    # 이렇게 하면 나중에 ATOMIC_SNAPSHOT_EXCLUDED_FILES에 추가된 파일이 "deleted"로 오탐되지 않음.
    before = {rel: info for rel, info in before.items() if Path(rel).name not in ATOMIC_SNAPSHOT_EXCLUDED_FILES}
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

        # IMP-20260602-1ABE MT-1: covers_ac / covers_iqr 파싱 (있을 때만)
        covers_ac_text = _child_text(element, "covers_ac")
        covers_iqr_text = _child_text(element, "covers_iqr")
        covers_ac_list = [s.strip() for s in covers_ac_text.split(",") if s.strip()] if covers_ac_text else []
        covers_iqr_list = [s.strip() for s in covers_iqr_text.split(",") if s.strip()] if covers_iqr_text else []

        micro_tasks.append({
            "id": mt_id,
            "affected_function": affected_function,
            "target_files": target_files,
            "change_summary": change_summary,
            "covers_ac": covers_ac_list,
            "covers_iqr": covers_iqr_list,
        })

    design_confirmation = _validate_pm_design_confirmation(step_plan, micro_tasks)
    execution_profile = _parse_task_complexity(step_plan, micro_tasks)

    # IMP-20260602-1ABE MT-1: structured AC 파싱 및 검증 (AC 없으면 항상 실패)
    structured_ac = _parse_structured_ac(step_plan)
    ac_result = _validate_structured_ac_block(structured_ac, micro_tasks)
    if not ac_result.get("valid"):
        _die(ac_result.get("error", "[AC GATE] structured AC validation failed"))

    return {
        "report_file": str(path),
        "validated_at": _now(),
        "audit_result": audit_result,
        "micro_task_count": len(micro_tasks),
        "micro_tasks": micro_tasks,
        "design_confirmation": design_confirmation,
        "execution_profile": execution_profile,
        "project_snapshot": _atomic_project_snapshot(),
        "structured_acceptance_criteria": structured_ac,
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


def _validate_manager_handoff_file(
    report_file: Optional[str],
    state: Dict[str, Any],
    *,
    step_plan_file: str,
    planner_run_id: str,
) -> Dict[str, Any]:
    path, text = _read_phase_report_or_die(report_file, "PM MANAGER GATE")

    forbidden_tags = [
        "step_plan",
        "decomposition_audit",
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
    found_forbidden = [tag for tag in forbidden_tags if _extract_xml_element(text, tag) is not None]
    if found_forbidden:
        _die(
            "[PM MANAGER GATE] manager_handoff contains forbidden planning/downstream blocks: "
            + ", ".join(f"<{tag}>" for tag in found_forbidden)
        )

    handoff = _extract_xml_element(text, "manager_handoff")
    if handoff is None:
        _die("[PM MANAGER GATE] <manager_handoff> XML block is required")

    pid = str(state.get("pipeline_id") or "")
    actual_pid = _child_text(handoff, "pipeline_id")
    if actual_pid != pid:
        _die(f"[PM MANAGER GATE] pipeline_id mismatch: expected {pid}, got {actual_pid or '<missing>'}")
    sender = _child_text(handoff, "from")
    if sender != "pipeline-manager-agent":
        _die("[PM MANAGER GATE] <from> must be pipeline-manager-agent")

    step_path = Path(step_plan_file)
    if not step_path.is_absolute():
        step_path = BASE_DIR / step_path
    expected_sha = _sha256_file(step_path.resolve())
    actual_sha = _child_text(handoff, "step_plan_sha256")
    if actual_sha.lower() != expected_sha.lower():
        _die("[PM MANAGER GATE] step_plan_sha256 does not match the PM planner output")
    actual_planner = _child_text(handoff, "planner_run_id")
    if actual_planner != planner_run_id:
        _die(f"[PM MANAGER GATE] planner_run_id mismatch: expected {planner_run_id}, got {actual_planner or '<missing>'}")
    if _child_text(handoff, "accepted_for_execution").strip().lower() != "true":
        _die("[PM MANAGER GATE] <accepted_for_execution>true</accepted_for_execution> is required")
    if _child_text(handoff, "will_not_modify_step_plan").strip().lower() != "true":
        _die("[PM MANAGER GATE] <will_not_modify_step_plan>true</will_not_modify_step_plan> is required")
    next_phase = _child_text(handoff, "next_phase", "dev").strip().lower()
    if next_phase != "dev":
        _die("[PM MANAGER GATE] <next_phase> must be dev")

    return {
        "validated_at": _now(),
        "report_file": _display_path(path.resolve()),
        "step_plan_sha256": expected_sha,
        "planner_run_id": planner_run_id,
        "accepted_for_execution": True,
        "will_not_modify_step_plan": True,
        "next_phase": next_phase,
    }


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


# ─── Dev scope_manifest implemented_tasks (IMP-20260602-1ABE MT-5) ───────────
_ABSTRACT_EVIDENCE_PATTERNS: frozenset = frozenset({
    "구현 완료", "완료", "done", "finished", "implemented", "구현됨", "구현",
    "works", "working", "ok", "OK",
})


def _validate_implemented_tasks(
    state: Dict[str, Any],
    manifest: Dict[str, Any],
) -> Dict[str, Any]:
    """scope_manifest.json의 micro_tasks[i].implemented_tasks 검증.

    requirements_tracking.enabled=true 파이프라인에서만 강제. legacy는 자동 PASS.

    Args:
        state: 현재 파이프라인 state.
        manifest: 이미 로드된 scope_manifest dict.
    Returns:
        {"valid": True, "reason": str} 또는 {"valid": False, "error": str}
    """
    rt = state.get("requirements_tracking") or {}
    if not rt.get("enabled"):
        return {"valid": True, "reason": "legacy state — implemented_tasks 검증 생략"}

    micro_tasks_in_manifest = manifest.get("micro_tasks") or []
    if not micro_tasks_in_manifest:
        return {"valid": True, "reason": "scope_manifest micro_tasks 비어있음"}

    structured_ac = state.get("structured_acceptance_criteria") or []
    valid_ac_ids: set = {
        str(ac.get("ac_id", "")).strip()
        for ac in structured_ac
        if isinstance(ac, dict) and ac.get("ac_id")
    }

    atomic_plan = state.get("atomic_plan") or {}
    pm_mt_ids: set = {
        str(mt.get("id", ""))
        for mt in atomic_plan.get("micro_tasks", [])
        if isinstance(mt, dict) and mt.get("id")
    }

    for mt in micro_tasks_in_manifest:
        if not isinstance(mt, dict):
            continue
        mt_id = str(mt.get("id", ""))
        implemented_tasks = mt.get("implemented_tasks")

        if not implemented_tasks:
            if valid_ac_ids:
                return {
                    "valid": False,
                    "error": (
                        f"[DEV SCOPE GATE] {mt_id}에 implemented_tasks가 없습니다. "
                        "requirements_tracking.enabled=true 파이프라인에서는 필수입니다."
                    ),
                }
            continue

        if not isinstance(implemented_tasks, list):
            return {
                "valid": False,
                "error": f"[DEV SCOPE GATE] {mt_id}.implemented_tasks는 리스트여야 합니다",
            }

        for task in implemented_tasks:
            if not isinstance(task, dict):
                continue
            task_mt_id = str(task.get("mt_id", ""))
            if pm_mt_ids and task_mt_id and task_mt_id not in pm_mt_ids:
                return {
                    "valid": False,
                    "error": f"[DEV SCOPE GATE] 알 수 없는 mt_id: {task_mt_id}",
                }

            for ac_id in task.get("implemented_ac", []):
                ac_id_str = str(ac_id).strip()
                if valid_ac_ids and ac_id_str not in valid_ac_ids:
                    return {
                        "valid": False,
                        "error": f"[DEV SCOPE GATE] 알 수 없는 AC id: {ac_id_str}",
                    }

            evidence_list = task.get("implementation_evidence") or []
            if isinstance(evidence_list, list) and evidence_list:
                # 모든 evidence가 abstract면 차단
                normalized = [str(e).strip() for e in evidence_list if str(e).strip()]
                if normalized and all(
                    e in _ABSTRACT_EVIDENCE_PATTERNS for e in normalized
                ):
                    return {
                        "valid": False,
                        "error": (
                            f"[DEV SCOPE GATE] {task_mt_id}의 implementation_evidence가 "
                            f"추상적입니다: {evidence_list}"
                        ),
                    }

    return {"valid": True, "checked_mt_count": len(micro_tasks_in_manifest)}


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
    if not _product_code_write_allowed(state):
        declared_product_code = sorted(path for path in (manifest_files | evidence_files) if _is_product_code_path(path))
        if declared_product_code:
            profile = _execution_profile(state)
            _die(
                "[FAST PATH SCOPE GATE] "
                f"{profile.get('mode')} does not allow product code files in scope/evidence: {declared_product_code}. "
                "제품 코드 수정이 필요하면 PM이 STANDARD 프로필로 다시 계획해야 합니다."
            )

    actual_diff = _atomic_changed_files(project_snapshot)
    actual_changed = set(actual_diff.get("changed", []))
    if not _product_code_write_allowed(state):
        product_code_changes = sorted(path for path in actual_changed if _is_product_code_path(path))
        if product_code_changes:
            profile = _execution_profile(state)
            _die(
                "[FAST PATH SCOPE GATE] "
                f"{profile.get('mode')} does not allow product code changes: {product_code_changes}. "
                "제품 코드 수정이 필요하면 PM이 STANDARD 프로필로 다시 계획해야 합니다."
            )
    # D6 수정: trust-root 파일이 allowed_files(scope_manifest)에 없는데 실제 변경되었으면 차단
    # bootstrap_exception이면 trust-root 자체가 대상이므로 skip
    _bootstrap_exception = state.get("codex_bootstrap_exception", False)
    if not _bootstrap_exception:
        for _changed_file in actual_changed:
            for _tr_pat in TRUST_ROOT_PATTERNS:
                if _changed_file.startswith(_tr_pat) or _changed_file == _tr_pat:
                    if _changed_file not in allowed_files:
                        _die(
                            f"[SCOPE LOCK] trust-root 파일 '{_changed_file}'이 scope_manifest의 allowed_files에 없습니다. "
                            f"trust-root 파일(pipeline.py/.github/workflows/**/.claude/agents/**/ CLAUDE.md)을 "
                            "수정하려면 PM이 해당 파일을 target_files에 명시한 별도 IMP 파이프라인을 사용하세요. "
                            "IMP-20260516-A627 등 bootstrap 파이프라인은 codex_bootstrap_exception=true를 설정하세요."
                        )
    actual_outside_scope = sorted(actual_changed - allowed_files)
    if actual_outside_scope:
        _die(f"[ATOMIC SCOPE GATE] actual file changes outside PM micro_task target_files: {actual_outside_scope}")
    actual_missing_manifest = sorted(actual_changed - manifest_files)
    if actual_missing_manifest:
        _die(f"[ATOMIC SCOPE GATE] actual file changes missing from scope_manifest files: {actual_missing_manifest}")
    actual_missing_evidence = sorted(actual_changed - evidence_files)
    if actual_missing_evidence:
        _die(f"[ATOMIC SCOPE GATE] actual file changes missing from --files evidence: {actual_missing_evidence}")

    # IMP-20260602-1ABE MT-5: implemented_tasks 검증 (requirements_tracking.enabled=true만 적용)
    impl_check = _validate_implemented_tasks(state, manifest)
    if not impl_check.get("valid"):
        _die(impl_check.get("error", "[DEV SCOPE GATE] implemented_tasks validation failed"))

    # implemented_tasks를 scope dict에 보존하여 _build_ac_fulfillment_table에서 활용
    implemented_tasks_collected: List[Dict[str, Any]] = []
    for mt in manifest.get("micro_tasks", []):
        if isinstance(mt, dict):
            for task in mt.get("implemented_tasks") or []:
                if isinstance(task, dict):
                    implemented_tasks_collected.append(task)

    return {
        "manifest_file": str(path),
        "validated_at": _now(),
        "micro_task_ids": sorted(manifest_ids),
        "files": sorted(manifest_files),
        "affected_functions": sorted(manifest_functions),
        "implemented_tasks": implemented_tasks_collected,
        "actual_diff": {
            "added": actual_diff.get("added", []),
            "modified": actual_diff.get("modified", []),
            "deleted": actual_diff.get("deleted", []),
            "changed": actual_diff.get("changed", []),
        },
    }

def _module_task_from_plan(state: Dict[str, Any], mt_id: str) -> Dict[str, Any]:
    atomic_plan = state.get("atomic_plan")
    if not isinstance(atomic_plan, dict):
        _die("[MODULE GATE] PM atomic_plan missing; complete PM first")
    for item in atomic_plan.get("micro_tasks", []):
        if isinstance(item, dict) and str(item.get("id")) == mt_id:
            return item
    _die(f"[MODULE GATE] unknown micro_task id: {mt_id}")


def _init_module_gates_from_atomic_plan(state: Dict[str, Any]) -> Dict[str, Any]:
    state["module_gates"] = _ensure_module_gates(state)
    atomic_plan = state.get("atomic_plan")
    if not isinstance(atomic_plan, dict):
        return state["module_gates"]
    micro_tasks = [
        item for item in atomic_plan.get("micro_tasks", [])
        if isinstance(item, dict) and item.get("id")
    ]
    sequence = [str(item["id"]) for item in micro_tasks]
    existing_modules = state["module_gates"].setdefault("modules", {})
    modules: Dict[str, Any] = {}
    for index, item in enumerate(micro_tasks, start=1):
        mt_id = str(item["id"])
        previous = existing_modules.get(mt_id, {}) if isinstance(existing_modules, dict) else {}
        module = {
            "id": mt_id,
            "status": "PENDING",
            "order": index,
            "target_files": [str(path) for path in item.get("target_files", [])],
            "affected_function": item.get("affected_function"),
            "design": _empty_module_step(),
            "dev": _empty_module_step(),
            "qa": _empty_module_step(),
            "checkpoint": None,
        }
        if isinstance(previous, dict):
            module.update(previous)
            module["order"] = index
            module["target_files"] = [str(path) for path in item.get("target_files", [])]
            module["affected_function"] = item.get("affected_function")
            for step_name in ("design", "dev", "qa"):
                step = _empty_module_step()
                if isinstance(previous.get(step_name), dict):
                    step.update(previous[step_name])
                module[step_name] = step
        modules[mt_id] = module
    state["module_gates"]["enabled"] = True
    state["module_gates"]["sequence"] = sequence
    state["module_gates"]["modules"] = modules
    state["module_gates"]["integration"] = state["module_gates"].get("integration") or _empty_module_step()
    return state["module_gates"]


def _module_gate_state(state: Dict[str, Any]) -> Dict[str, Any]:
    state["module_gates"] = _ensure_module_gates(state)
    if not state["module_gates"].get("sequence") and isinstance(state.get("atomic_plan"), dict):
        _init_module_gates_from_atomic_plan(state)
    return state["module_gates"]


def _module_gate_blockers(state: Dict[str, Any]) -> List[str]:
    gates = _module_gate_state(state)
    blockers: List[str] = []
    sequence = gates.get("sequence", [])
    modules = gates.get("modules", {})
    if not sequence:
        blockers.append("PM micro_tasks must initialize module gates")
        return blockers
    for mt_id in sequence:
        module = modules.get(mt_id, {}) if isinstance(modules, dict) else {}
        if not isinstance(module, dict) or module.get("status") != "PASS":
            blockers.append(f"{mt_id} module QA must be PASS")
    integration = gates.get("integration", {})
    if not isinstance(integration, dict) or integration.get("status") != "PASS":
        blockers.append("integration module gate must be PASS")
    return blockers


def _require_module_current(state: Dict[str, Any], mt_id: str) -> Dict[str, Any]:
    gates = _module_gate_state(state)
    sequence = gates.get("sequence", [])
    modules = gates.get("modules", {})
    if mt_id not in sequence:
        _die(f"[MODULE GATE] unknown micro_task id: {mt_id}")
    index = sequence.index(mt_id)
    for prior_id in sequence[:index]:
        prior = modules.get(prior_id, {}) if isinstance(modules, dict) else {}
        if not isinstance(prior, dict) or prior.get("status") != "PASS":
            _die(f"[MODULE GATE] {prior_id} must PASS module QA before {mt_id} can start")
    for later_id in sequence[index + 1:]:
        later = modules.get(later_id, {}) if isinstance(modules, dict) else {}
        if isinstance(later, dict) and later.get("status") in {"DESIGN_DONE", "DEV_DONE", "PASS"}:
            _die(f"[MODULE GATE] later module {later_id} already advanced before {mt_id}")
    module = modules.get(mt_id)
    if not isinstance(module, dict):
        _die(f"[MODULE GATE] module state missing for {mt_id}")
    return module


def _resolve_workspace_file(raw: str) -> Path:
    path = Path(str(raw))
    if not path.is_absolute():
        path = BASE_DIR / path
    return path


def _module_checkpoint_for_files(files: List[str]) -> Dict[str, Any]:
    checked: List[Dict[str, Any]] = []
    missing: List[str] = []
    for raw in files:
        path = _resolve_workspace_file(raw)
        if not path.exists() or not path.is_file():
            missing.append(str(raw))
            continue
        checked.append(_workspace_check_for_file(path))
    if missing:
        _die(f"[MODULE CHECKPOINT] target files missing for checkpoint: {missing}")
    if not checked:
        _die("[MODULE CHECKPOINT] no files available for checkpoint")
    return {
        "created_at": _now(),
        "files": checked,
    }


def _validate_module_checkpoints(state: Dict[str, Any]) -> None:
    gates = _module_gate_state(state)
    modules = gates.get("modules", {})
    if not isinstance(modules, dict):
        return
    for mt_id, module in modules.items():
        if not isinstance(module, dict) or module.get("status") != "PASS":
            continue
        checkpoint = module.get("checkpoint")
        if not isinstance(checkpoint, dict):
            _die(f"[MODULE CHECKPOINT] {mt_id} PASS module has no checkpoint")
        for item in checkpoint.get("files", []):
            if not isinstance(item, dict):
                continue
            path = _resolve_workspace_file(str(item.get("path") or ""))
            if not path.exists() or not path.is_file():
                _die(f"[MODULE CHECKPOINT] {mt_id} checkpoint file missing: {item.get('path')}")
            actual = _sha256_file(path)
            expected = str(item.get("sha256") or "")
            if actual.lower() != expected.lower():
                _die(f"[MODULE CHECKPOINT] {mt_id} checkpoint changed after PASS: {item.get('path')}")


def _validate_module_report_file(
    report_file: Optional[str],
    *,
    label: str,
    required_tags: List[str],
    mt_id: Optional[str] = None,
) -> Dict[str, Any]:
    path, text = _read_phase_report_or_die(report_file, label)
    _verify_required_xml_tags(
        str(path),
        required_tags=[f"<{tag}>" for tag in required_tags],
        context_label=label,
        hard_fail=True,
    )
    if mt_id is not None:
        found = None
        element = _extract_xml_element(text, "mt_id")
        if element is not None and element.text:
            found = element.text.strip()
        if found != mt_id:
            _die(f"[{label}] <mt_id> must be {mt_id}, got {found!r}")
    return {
        "report_file": str(path),
        "validated_at": _now(),
    }


def _validate_module_scope_manifest(
    manifest_file: str,
    state: Dict[str, Any],
    mt_id: str,
    evidence: Optional[str],
) -> Dict[str, Any]:
    if not manifest_file:
        _die("[MODULE SCOPE GATE] --scope-manifest is required")
    path = Path(manifest_file)
    if not path.exists():
        _die(f"[MODULE SCOPE GATE] --scope-manifest not found: {manifest_file}")
    manifest = _load_json_file(path)
    pid = str(state.get("pipeline_id", ""))
    if manifest.get("pipeline_id") and manifest.get("pipeline_id") != pid:
        _die(f"[MODULE SCOPE GATE] scope_manifest pipeline_id mismatch: expected {pid}, got {manifest.get('pipeline_id')}")
    task = _module_task_from_plan(state, mt_id)
    allowed_files = {_normalize_rel_path(item) for item in task.get("target_files", [])}
    allowed_functions = {str(task.get("affected_function"))}
    manifest_tasks = manifest.get("micro_tasks")
    if not isinstance(manifest_tasks, list) or len(manifest_tasks) != 1:
        _die("[MODULE SCOPE GATE] module scope_manifest must contain exactly one micro_task")
    item = manifest_tasks[0]
    if not isinstance(item, dict) or str(item.get("id") or "") != mt_id:
        _die(f"[MODULE SCOPE GATE] module scope_manifest must contain only {mt_id}")
    files = item.get("files", [])
    funcs = item.get("affected_functions", [])
    if not isinstance(files, list) or not files:
        _die(f"[MODULE SCOPE GATE] {mt_id} requires non-empty files list")
    if not isinstance(funcs, list) or not funcs:
        _die(f"[MODULE SCOPE GATE] {mt_id} requires non-empty affected_functions list")
    manifest_files = {_normalize_rel_path(file) for file in files}
    manifest_functions = {str(func).strip() for func in funcs if str(func).strip()}
    evidence_files = {
        _normalize_rel_path(item)
        for item in (evidence or "").split(",")
        if item.strip()
    }
    extra_files = sorted((manifest_files | evidence_files) - allowed_files)
    if extra_files:
        _die(f"[MODULE SCOPE GATE] files outside {mt_id} target_files: {extra_files}")
    if not _product_code_write_allowed(state):
        product_code_changes = sorted(path for path in (manifest_files | evidence_files) if _is_product_code_path(path))
        if product_code_changes:
            profile = _execution_profile(state)
            _die(
                "[FAST PATH MODULE GATE] "
                f"{profile.get('mode')} does not allow product code changes: {product_code_changes}. "
                "제품 코드 수정이 필요하면 PM이 STANDARD 프로필로 다시 계획해야 합니다."
            )
    missing_evidence = sorted(manifest_files - evidence_files)
    if missing_evidence:
        _die(f"[MODULE SCOPE GATE] manifest files missing from --files evidence: {missing_evidence}")
    extra_functions = sorted(manifest_functions - allowed_functions)
    if extra_functions:
        _die(f"[MODULE SCOPE GATE] affected_functions outside {mt_id} plan: {extra_functions}")

    # IMP-20260602-1ABE MT-5: implemented_tasks 검증 + 보존
    impl_check = _validate_implemented_tasks(state, manifest)
    if not impl_check.get("valid"):
        _die(impl_check.get("error", "[MODULE SCOPE GATE] implemented_tasks validation failed"))
    implemented_tasks_preserved: List[Dict[str, Any]] = []
    for _mt_entry in manifest.get("micro_tasks", []):
        if isinstance(_mt_entry, dict):
            for _task in _mt_entry.get("implemented_tasks") or []:
                if isinstance(_task, dict):
                    implemented_tasks_preserved.append(_task)

    return {
        "manifest_file": str(path),
        "validated_at": _now(),
        "micro_task_id": mt_id,
        "files": sorted(manifest_files),
        "affected_functions": sorted(manifest_functions),
        "implemented_tasks": implemented_tasks_preserved,
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

# IMP-20260518-150C: failure_packet schema_version=2 표준화
# 14종 명시적 카테고리 + unknown fallback (총 15개)
FAILURE_PACKET_SCHEMA_VERSION = 2
FAILURE_CATEGORIES: frozenset = frozenset({
    "scope_mismatch",
    "missing_evidence",
    "stale_evidence",
    "model_verification_failed",
    "ci_failed",
    "test_failed",
    "typecheck_failed",
    "security_failed",
    "oracle_failed",
    "user_acceptance_rejected",
    "setup_required",
    "provider_unavailable",
    "document_implementation_mismatch",
    "protocol_violation",
    "unknown",
})
# 동일 (gate, failure_code) 조합이 이 횟수 이상 attempt되면 status=BLOCKED 전이
FAILURE_BLOCKED_THRESHOLD = 3
# gate -> (owner, return_phase) 표준 매핑
_GATE_OWNER_RETURN_MAP: Dict[str, Tuple[str, str]] = {
    "technical": ("Dev", "dev"),
    "oracle": ("Dev", "dev"),
    "github_ci": ("Dev", "dev"),
    "acceptance": ("Dev", "dev"),
    "codex_plan_review": ("PM", "pm"),
    "codex_scope_review": ("PM", "pm"),
    "codex_code_review": ("Dev", "dev"),
    "codex_hygiene_review": ("Dev", "dev"),
    "codex_pr_review": ("Dev", "dev"),
    "codex_rca_review": ("PM", "pm"),
}
BASE_DIR   = Path(__file__).resolve().parent
_state_path_env = os.environ.get("PIPELINE_STATE_PATH")
STATE_FILE = Path(_state_path_env) if _state_path_env else BASE_DIR / "pipeline_state.json"
HISTORY_DIR = BASE_DIR / "pipeline_history"
CONTRACTS_DIR = BASE_DIR / "pipeline_contracts"
PIPELINE_CI_DIR = BASE_DIR / ".pipeline"
PHASE_ATTESTATION_REQUEST = PIPELINE_CI_DIR / "phase_attestation_request.json"
PHASE_ATTESTATION_EVIDENCE_DIR = PIPELINE_CI_DIR / "phase_evidence"
AGENT_RECEIPT_DIR = PIPELINE_CI_DIR / "agent_receipts"
PHASE_ATTESTATION_PHASES = ("pm", "dev", "qa", "build")
AGENT_RUN_PHASES = ("pm", "pm_planner", "pipeline_manager", "dev", "qa", "build")
# "pm" is kept for legacy backward compat; new pipelines use "pm_planner" + "pipeline_manager"

# 신뢰 루트 파일 패턴 — 이 패턴에 해당하는 파일이 변경된 경우 per_phase CI 필수
# (batched CI 불허). "gates batch-ci --probe"에서 ci_mode 결정에 사용.
TRUST_ROOT_PATTERNS: List[str] = [
    "pipeline.py",
    "CLAUDE.md",
    ".claude/agents/",
    ".github/workflows/",
    ".github/CODEOWNERS",
    ".codex/skills/",
]

# PM Planner 재시도 허용 최대 횟수 (초과 시 [PM PLANNER RETRY LIMIT] + exit 1)
PM_PLANNER_MAX_RETRIES: int = 2
PHASE_RECEIPT_RUN_PHASES = {
    "pm": "pm_planner",
    "dev": "dev",
    "qa": "qa",
    "build": "build",
}
PHASE_AGENT_IDS = {
    "pm": "pm-agent",            # legacy: old state uses pm-agent as the receipt agent_id
    "pm_planner": "pm-planner-agent",
    "pipeline_manager": "pipeline-manager-agent",
    "dev": "dev-agent",
    "qa": "qa-agent",
    "build": "build-agent",
}
DEFAULT_DEPLOY_ROOT_CANDIDATES = (
    Path(r"G:\내 드라이브\터미널"),
    Path(r"G:\내드라이브\터미널"),
)

# IMP-20260528-3898 MT-1: 워크스페이스 내부 산출물 패턴 — SSoT.
# pipeline 실행 중 생성되는 임시/내부 파일들로, PR diff 또는 배포물에 섞이면 안 됩니다.
# 이 상수를 수정하면 _is_internal_artifact(), preflight-pr-impl,
# _deployment_artifacts() 필터가 모두 자동으로 업데이트됩니다.
WORKSPACE_INTERNAL_PATTERNS: List[str] = [
    # phase 보고서 및 인수인계 파일
    "build_report.xml",
    "qa_report.xml",
    "dev_handover.xml",
    "architect_report.xml",
    "integration_report.xml",
    "manager_handoff.xml",
    # scope / module gate 파일
    "scope_manifest.json",
    # MT-N 단위 scope/module 파일 (접두사 패턴)
    "scope_manifest_MT-",
    "module_design_MT-",
    "module_handover_MT-",
    "module_qa_MT-",
    # 실패 / 진단 파일
    "failure_packet.json",
    "acceptance_result.json",
    "codex_review_result.json",
    "codex_run_raw.json",
    "protocol_consistency_result.json",
    # 기타 파이프라인 런타임 파일
    "step_plan.xml",
    "stop_signal.json",
    "test_results.jsonl",
    "test_results_v3.jsonl",
    "acceptance_comment.json",
    "acceptance_packet.md",
    "human_acceptance_packet.md",  # IMP-20260531-BBDB: nonce gate 생성 파일
    "acceptance_request.json",     # IMP-20260531-BBDB: nonce gate 생성 파일
    "bandit_e2e_result.json",
    "bandit_e2e_result2.json",
    # 변형 파일명 접두사 패턴 (IMP-20260531-B0AB: PR 오염 방지)
    "build_report_",          # build_report_075a.xml 등 변형
    "qa_report_",             # qa_report_fix.xml 등 변형
    "dev_handover_fix",       # dev_handover_fix*.xml
    "pipeline_state_TMP_",    # pipeline_state_TMP_*.json
    "comment_",               # comment_*.txt, comment_*.json
    "pr_body_",               # pr_body_*.txt, pr_body_*.json
    # IMP-20260602-1ABE: PR #407 오염 사례로 추가된 파일명 패턴
    "scheduled_tasks.lock",   # Claude Code 스케줄 잠금 파일
    "claude_patch.",          # git patch 임시 파일 (claude_patch.patch 등)
    "restore_",               # restore_*.py 복구 스크립트
    "analyze_",               # analyze_*.py 임시 분석 스크립트
]

# .spec 확장자 파일 (PyInstaller spec — workspace 전용, PR에 포함 금지)
WORKSPACE_INTERNAL_EXTENSIONS: List[str] = [
    ".spec",    # PyInstaller spec 파일 (IMP-20260602-1ABE: PR #407 오염 사례)
]

# 디렉터리 접두사 패턴 (이 경로 아래 모든 파일은 내부 산출물)
WORKSPACE_INTERNAL_DIR_PREFIXES: List[str] = [
    ".pipeline/",
    "pipeline_contracts/",
    # IMP-20260602-1ABE: PR #407 오염 사례로 추가된 workspace 임시 디렉터리
    ".codex/",                   # Codex configuration 파일 (workspace 전용)
    "po_automation/",            # Power Automate 작업 디렉터리 (workspace 전용)
    "_pipeline_backup_tmp/",     # 파이프라인 백업 임시 디렉터리
    "pipeline_outputs/",         # pipeline outputs 디렉터리 (배포 전 임시 보관)
]

# ---------------------------------------------------------------------------
# IMP-20260601-0DF5 MT-1: Hygiene Scan/Archive — SSoT 상수
# ---------------------------------------------------------------------------
# 이 상수들을 수정하면 cmd_hygiene_scan, cmd_hygiene_archive,
# _hygiene_collect_candidates, _hygiene_classify가 모두 자동 반영됩니다.
#
# HYGIENE_ARCHIVE_PATTERNS: glob 패턴으로 정의된 임시 산출물 파일명 패턴.
#   pipeline 실행 중 생성되는 보고서·댓글·진단 파일로,
#   7일 이상 방치 시 Google Drive 찌꺼기 폴더로 정리합니다.
#
# HYGIENE_PROTECTED_PATHS: 절대 archive하지 않을 정확한 파일명 목록.
# HYGIENE_PROTECTED_PREFIXES: 절대 archive하지 않을 경로 접두사 목록.
#   이 목록의 경로 아래 있는 파일은 나이/git 상태에 관계없이 보호됩니다.
# ---------------------------------------------------------------------------

HYGIENE_ARCHIVE_PATTERNS: List[str] = [
    "build_report*.xml",
    "qa_report*.xml",
    "dev_handover*.xml",
    "architect_report*.xml",
    "acceptance_request.json",
    "acceptance_packet_body.txt",
    "human_acceptance_packet*.md",
    "comment_*.txt",
    "comment_*.json",
    "pr_body_*.txt",
    "pr_body_*.json",
    "runs_tmp.json",
    "tmp*.json",
    "*_req.json",
    "debug_parse.py",
    "test_packet_parse.py",
    "test_pr_parse.py",
    "bandit_e2e_result*.json",
    "codex_review_result*.json",
    "codex_run_raw*.json",
    "protocol_consistency_result*.json",
    "failure_packet.json",
    "acceptance_comment.json",
    "acceptance_packet.md",
]

HYGIENE_PROTECTED_PATHS: List[str] = [
    "pipeline.py",
    "CLAUDE.md",
    "README.md",
    "RELEASE_NOTES.md",
    "pyproject.toml",
    ".gitignore",
    ".gitattributes",
]

HYGIENE_PROTECTED_PREFIXES: List[str] = [
    ".github/",
    ".claude/",
    ".codex/",
    ".pipeline/",
    "tests/oracles/",
    "tests/",
    "pipeline_contracts/",
    "pipeline_outputs/",
]


def _is_internal_artifact(path: str) -> bool:
    """경로가 workspace 내부 산출물인지 판정합니다 (WORKSPACE_INTERNAL_PATTERNS SSoT 사용).

    PR diff 또는 배포물에 포함될 수 없는 파이프라인 런타임 파일을 감지합니다.

    Args:
        path: 검사할 파일 경로 (절대/상대 모두 허용, 백슬래시 자동 변환)

    Returns:
        True이면 내부 산출물, False이면 일반 파일
    """
    normalized = path.replace("\\", "/").strip()
    # 파일명(basename) 추출
    basename = normalized.split("/")[-1] if "/" in normalized else normalized

    # 디렉터리 접두사 검사
    for dir_prefix in WORKSPACE_INTERNAL_DIR_PREFIXES:
        if normalized.startswith(dir_prefix) or normalized == dir_prefix.rstrip("/"):
            return True

    # 확장자 검사 (IMP-20260602-1ABE: .spec 등 workspace 전용 확장자)
    for ext in WORKSPACE_INTERNAL_EXTENSIONS:
        if basename.endswith(ext):
            return True

    # 정확한 파일명 일치 또는 접두사 패턴 일치
    for pattern in WORKSPACE_INTERNAL_PATTERNS:
        if pattern.endswith("-") or not pattern.endswith((".xml", ".json", ".jsonl", ".md")):
            # 접두사 패턴 (예: "scope_manifest_MT-", "module_design_MT-")
            if basename.startswith(pattern):
                return True
        else:
            # 정확한 파일명 일치
            if basename == pattern:
                return True

    return False


# ─── Secret Patterns SSoT (IMP-20260529-D8BA MT-1) ───────────────────────────
# [Purpose]: 민감 정보(API key/token/private key 등) 탐지 패턴을 단일 출처(SSoT)로
#   관리한다. gates secrets 명령, 배포 필터(_deployment_artifacts), 외부 리뷰
#   redaction(_redact_for_external_review)이 동일한 패턴 목록을 사용한다.
# [Assumptions]: 패턴은 형식만 매칭한다(실제 검증 X). false positive는 마스킹
#   처리로 사용자에게 알리며 원문은 절대 출력/저장하지 않는다.
# [Vulnerability & Risks]: 패턴이 너무 느슨하면 일반 텍스트에서 false positive
#   가 발생할 수 있다. 너무 빡세면 새로운 비밀 포맷을 놓친다. 신규 비밀 포맷이
#   등장하면 이 SSoT에만 추가하면 전체 게이트가 동기 업데이트된다.
# [Improvement]: entropy 기반 휴리스틱(Shannon entropy)을 추가하면 EXAMPLE/
#   AAAA 패딩 false positive를 더 줄일 수 있다.
#
# 절대 금지: 이 상수나 헬퍼 함수에 실제 secret 원문을 포함하지 마세요.
SECRET_PATTERNS: List[Tuple[str, "re.Pattern[str]"]] = [
    ("openai_api_key",          re.compile(r"sk-[A-Za-z0-9_\-]{20,}")),
    ("github_pat",              re.compile(r"gh[psoua]_[A-Za-z0-9_]{10,}")),
    ("github_pat_classic",      re.compile(r"github_pat_[A-Za-z0-9_]{10,}")),
    ("bearer_token",            re.compile(r"Bearer\s+[A-Za-z0-9+/=_.\-]{20,}")),
    ("approval_secret",         re.compile(r"approval[-_]secret\s*[:=]\s*\S+")),
    ("server_identity_key",     re.compile(r"server[-_]identity[-_]key\s*[:=]\s*\S+")),
    ("codex_relay_pairing_url", re.compile(r"(?:https?://[^/\s]*codex[^/\s]*/pair[^\s\"']*|codex-relay://[^\s\"']*pair[^\s\"']*)")),
    ("private_key_block",       re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
]

# secret 파일명 패턴 (배포 차단용)
SECRET_FILENAME_PATTERNS: List[str] = [
    ".env", ".envrc", "*.key", "*.pem", "server-identity-key*",
]


def _mask_secret(value: str, prefix_len: int = 8) -> str:
    """민감 정보 문자열을 마스킹한다. prefix_len 자 이후는 ****로 대체.

    Args:
        value: 마스킹할 원본 문자열.
        prefix_len: 노출할 접두사 길이(기본 8).
    Returns:
        prefix + "****" 형태의 마스킹된 문자열. 원본보다 짧으면 "****" 단독.
    Raises:
        TypeError: value가 str이 아닌 경우.
    """
    if value is None:
        raise TypeError("value must not be None")
    if not isinstance(value, str):
        raise TypeError(f"value must be str, got {type(value).__name__}")
    if prefix_len < 0:
        raise ValueError(f"prefix_len must be >= 0, got {prefix_len}")  # negative not allowed: prefix length cannot be negative
    if len(value) <= prefix_len:
        return "****"
    return value[:prefix_len] + "****"


def _scan_text_for_secrets(text: str) -> List[Dict[str, Any]]:
    """텍스트에서 SECRET_PATTERNS를 검색한다.

    Args:
        text: 검사 대상 텍스트.
    Returns:
        List of {pattern_name, masked, position} dicts. 원문은 반환하지 않는다.
    Raises:
        TypeError: text가 None이거나 str이 아닌 경우.
    """
    if text is None:
        raise TypeError("text must not be None")
    if not isinstance(text, str):
        raise TypeError(f"text must be str, got {type(text).__name__}")
    findings: List[Dict[str, Any]] = []
    for pattern_name, pattern in SECRET_PATTERNS:
        for m in pattern.finditer(text):
            findings.append({
                "pattern_name": pattern_name,
                "masked": _mask_secret(m.group(0)),
                "position": m.start(),
            })
    return findings


def _is_secret_filename(path: str) -> bool:
    """파일명이 secret 파일 패턴인지 판정한다.

    Args:
        path: 검사할 파일 경로(절대/상대 모두 허용).
    Returns:
        secret 파일명 패턴과 일치하면 True.
    Raises:
        TypeError: path가 None이거나 str이 아닌 경우.
    """
    import fnmatch
    if path is None:
        raise TypeError("path must not be None")
    if not isinstance(path, str):
        raise TypeError(f"path must be str, got {type(path).__name__}")
    normalized = path.replace("\\", "/").strip()
    basename = normalized.split("/")[-1] if "/" in normalized else normalized
    for pattern in SECRET_FILENAME_PATTERNS:
        if "*" in pattern or "?" in pattern or "[" in pattern:
            if fnmatch.fnmatch(basename, pattern):
                return True
        else:
            if basename == pattern:
                return True
    return False


PHASE_ORDER = ["pm", "dev", "qa", "sec", "build", "harness", "architect"]

PHASE_LABELS = {
    "pm":        "Phase 1 - PM (Planning)",
    "dev":       "Phase 2 - Dev (Implementation)",
    "qa":        "Phase 4 - QA (Verification)",
    "sec":       "Phase 5 - Security (Audit)",
    "build":     "Phase 6 - Build (Packaging)",
    "harness":   "Phase 7 - External Gates (Acceptance)",
    "architect": "Phase 8 - Architect (RCA)",
}

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
    """Return state file path. branch=None means the main pipeline state."""
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
        # IMP-20260522-29C1 MT-2: phase 시작 시점. metrics status의
        # phase_elapsed 계산에 사용. cmd_done/cmd_qa에서 채워진다.
        "started_at":     None,
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
        # IMP-20260522-29C1 MT-3: gate started_at 추가. _gate_elapsed_summary에서
        # started_at/completed_at을 사용하여 실제 gate 소요 시간을 계산한다.
        "started_at": None,
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
        "github_ci": _empty_external_gate(),
    }


def _empty_phase_attestation() -> Dict[str, Any]:
    return {
        "status": "PENDING",
        "completed_at": None,
        "phase": None,
        "run_id": None,
        "commit_sha": None,
        "evidence": None,
        "report_file": None,
        "notes": [],
    }


def _new_phase_attestations(enabled: bool = False) -> Dict[str, Any]:
    return {
        "enabled": enabled,
        "mode": "github_actions_per_phase",
        "required_phases": list(PHASE_ATTESTATION_PHASES),
        "phases": {phase: _empty_phase_attestation() for phase in PHASE_ATTESTATION_PHASES},
    }


def _new_execution_profile(mode: str = "STANDARD") -> Dict[str, Any]:
    return {
        "mode": mode,
        "status": "ACTIVE",
        "reason": "",
        "max_micro_tasks": None,
        "product_code_write_allowed": True,
        "phase_ci_mode": "per_phase",
        "repair_mode": "standard",
        "risk_review_required": False,
        "risk_categories": [],
        "declared_at": None,
        "escalated_at": None,
        "escalation_reason": None,
    }


def _empty_module_step() -> Dict[str, Any]:
    return {
        "status": "PENDING",
        "completed_at": None,
        "report_file": None,
        "evidence": None,
        "notes": [],
    }


def _new_module_gates(enabled: bool = True) -> Dict[str, Any]:
    return {
        "enabled": enabled,
        "mode": "incremental_module_gate",
        "sequence": [],
        "modules": {},
        "integration": _empty_module_step(),
    }


def _ensure_module_gates(state: Dict[str, Any]) -> Dict[str, Any]:
    existing = state.get("module_gates")
    normalized = _new_module_gates(enabled=True)
    if isinstance(existing, dict):
        normalized["mode"] = str(existing.get("mode") or "incremental_module_gate")
        sequence = existing.get("sequence")
        if isinstance(sequence, list):
            normalized["sequence"] = [str(item) for item in sequence if str(item)]
        modules = existing.get("modules")
        if isinstance(modules, dict):
            for mt_id, item in modules.items():
                if not isinstance(item, dict):
                    continue
                merged = {
                    "id": str(mt_id),
                    "status": "PENDING",
                    "order": 0,
                    "target_files": [],
                    "affected_function": None,
                    "design": _empty_module_step(),
                    "dev": _empty_module_step(),
                    "qa": _empty_module_step(),
                    "checkpoint": None,
                }
                merged.update(item)
                for step_name in ("design", "dev", "qa"):
                    step = merged.get(step_name)
                    base = _empty_module_step()
                    if isinstance(step, dict):
                        base.update(step)
                    merged[step_name] = base
                normalized["modules"][str(mt_id)] = merged
        integration = existing.get("integration")
        if isinstance(integration, dict):
            merged_integration = _empty_module_step()
            merged_integration.update(integration)
            normalized["integration"] = merged_integration
    normalized["enabled"] = True
    return normalized


def _ensure_phase_attestations(state: Dict[str, Any]) -> Dict[str, Any]:
    existing = state.get("phase_attestations")
    if not isinstance(existing, dict):
        return _new_phase_attestations(enabled=True)
    normalized = _new_phase_attestations(enabled=True)
    normalized["mode"] = str(existing.get("mode") or "github_actions_per_phase")
    required = existing.get("required_phases")
    if isinstance(required, list) and required:
        normalized["required_phases"] = [
            str(item) for item in required if str(item) in PHASE_ATTESTATION_PHASES
        ] or list(PHASE_ATTESTATION_PHASES)
    phases = existing.get("phases")
    if isinstance(phases, dict):
        for phase in PHASE_ATTESTATION_PHASES:
            item = phases.get(phase)
            if isinstance(item, dict):
                merged = _empty_phase_attestation()
                merged.update(item)
                merged["phase"] = phase
                normalized["phases"][phase] = merged
    return normalized


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
        # IMP-20260522-29C1 MT-1: pipeline lifecycle timestamps.
        # metrics status의 total_elapsed 계산을 위해 명시적 시작/종료 시점을 기록.
        "pipeline_started_at": _now(),    # 파이프라인 최초 생성 시점
        "pipeline_completed_at": None,    # cmd_architect COMPLETE 시 기록
        "acceptance_requested_at": None,  # cmd_gates accept 시작 시 기록
        "acceptance_recorded_at": None,   # ACCEPT PASS 기록 직후 기록
        "total_elapsed_seconds": None,    # COMPLETE 시 (completed - started) 초
        "current_phase": "pm",
        "blocked":       False,
        "blocked_reason": None,
        "phases": {p: _empty_phase(p) for p in PHASE_ORDER},
        "event_log":     [],
        # v2.10 Auto-Compact 지원: 파이프라인 종료 상태 표시
        # null = in progress, "COMPLETE" = Phase 8 architect completion, "FAILED" = repeated external gate FAIL,
        # "TERMINATED" = 사용자 명시적 중단
        "terminal_state": None,
        "harness_fail_count": 0,
        "agent_runs": {},
        "external_gates": _new_external_gates(enabled=True),
        "phase_attestations": _new_phase_attestations(enabled=True),
        "module_gates": _new_module_gates(enabled=True),
        "execution_profile": _new_execution_profile("STANDARD"),
        "outputs": {"items": []},
        "failure_packets": [],
        "protocol_evolution_decision": None,
        # IMP-20260526-82E3 MT-1: 관측성 강화 - 5개 alias 키 (역호환 위해 기존 phases/external_gates와 병행 보존)
        "phase_timings": {},                   # {phase: {elapsed_seconds: int}}
        "gate_timings": {},                    # {gate: {elapsed_seconds: int}}
        "agent_timings": {},                   # {run_id: {elapsed_seconds: int}}
        "github_actions_timings": {            # 5상태 누적 시간 (초)
            "WAITING_FOR_TRIGGER": 0,
            "QUEUED": 0,
            "IN_PROGRESS": 0,
            "COMPLETED": 0,
            "TIMEOUT": 0,
        },
        "failure_summary": {},                 # {failure_code: {count: int, is_repeat: bool}}
        # IMP-20260527-075A MT-1: Cost/Attempt Budget Gate
        "attempt_budget": {
            "config": {
                "dev_max_attempts": 3,
                "qa_max_attempts": 3,
                "gate_max_attempts": 5,
                "repeat_failure_code_threshold": 3,
            },
            "attempts": {"dev": [], "qa": [], "gate": []},
            "blocked_phases": {},
        },
    }


def _ensure_external_gates(state: Dict[str, Any]) -> Dict[str, Any]:
    gates = state.get("external_gates")
    if not isinstance(gates, dict):
        return _new_external_gates(enabled=True)

    normalized = _new_external_gates(enabled=True)
    normalized["mode"] = str(gates.get("mode") or "three_gate")
    for gate_name in ("technical", "oracle", "acceptance", "github_ci"):
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
    if not isinstance(state.get("agent_runs"), dict):
        state["agent_runs"] = {}
    if "protocol_evolution_decision" not in state:
        state["protocol_evolution_decision"] = None
    if not isinstance(state.get("execution_profile"), dict):
        state["execution_profile"] = _new_execution_profile("STANDARD")
    else:
        merged_profile = _new_execution_profile(str(state["execution_profile"].get("mode") or "STANDARD"))
        merged_profile.update(state["execution_profile"])
        state["execution_profile"] = merged_profile
    if not isinstance(state.get("outputs"), dict):
        state["outputs"] = {"items": []}
    elif not isinstance(state["outputs"].get("items"), list):
        state["outputs"]["items"] = []
    if not isinstance(state.get("failure_packets"), list):
        state["failure_packets"] = []
    state["external_gates"] = _ensure_external_gates(state)
    state["external_gates"]["enabled"] = True
    state["phase_attestations"] = _ensure_phase_attestations(state)
    state["phase_attestations"]["enabled"] = True
    state["module_gates"] = _ensure_module_gates(state)
    # IMP-20260522-29C1 MT-1: pipeline lifecycle timestamps 마이그레이션 (idempotent).
    # 구버전 state에는 이 5개 필드가 없으므로 기본값을 채운다.
    # 기존 파이프라인은 pipeline_started_at을 created_at으로 대체한다.
    if "pipeline_started_at" not in state:
        state["pipeline_started_at"] = state.get("created_at")
    if "pipeline_completed_at" not in state:
        state["pipeline_completed_at"] = None
    if "acceptance_requested_at" not in state:
        state["acceptance_requested_at"] = None
    if "acceptance_recorded_at" not in state:
        state["acceptance_recorded_at"] = None
    if "total_elapsed_seconds" not in state:
        state["total_elapsed_seconds"] = None
    # IMP-20260524-48C4 MT-1: oracle_quality 상태 필드 초기화
    if "oracle_quality" not in state:
        state["oracle_quality"] = {}
    # IMP-20260526-82E3 MT-1: 5개 관측성 alias 키 마이그레이션 (역호환, idempotent)
    if "phase_timings" not in state:
        state["phase_timings"] = {}
    if "gate_timings" not in state:
        state["gate_timings"] = {}
    if "agent_timings" not in state:
        state["agent_timings"] = {}
    if "github_actions_timings" not in state or not isinstance(state.get("github_actions_timings"), dict):
        state["github_actions_timings"] = {
            "WAITING_FOR_TRIGGER": 0,
            "QUEUED": 0,
            "IN_PROGRESS": 0,
            "COMPLETED": 0,
            "TIMEOUT": 0,
        }
    else:
        for _gh_state in ("WAITING_FOR_TRIGGER", "QUEUED", "IN_PROGRESS", "COMPLETED", "TIMEOUT"):
            if _gh_state not in state["github_actions_timings"]:
                state["github_actions_timings"][_gh_state] = 0
    if "failure_summary" not in state:
        state["failure_summary"] = {}
    # IMP-20260527-075A MT-1: Cost/Attempt Budget Gate 마이그레이션
    # _ensure_attempt_budget_keys는 더 정교한 검증을 수행하지만 _ensure_v210_fields는
    # 호출 순서상 _record_failure_packet 정의 전에 실행되므로 여기서는 dict 형태만 보장.
    if not isinstance(state.get("attempt_budget"), dict):
        state["attempt_budget"] = {
            "config": {
                "dev_max_attempts": 3,
                "qa_max_attempts": 3,
                "gate_max_attempts": 5,
                "repeat_failure_code_threshold": 3,
            },
            "attempts": {"dev": [], "qa": [], "gate": []},
            "blocked_phases": {},
        }
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


def _die(message: str, exit_code: int = 1) -> NoReturn:
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
    """API advisory is opt-in because ChatGPT subscriptions do not include API quota.

    ``ENABLE_GPT_ADVISORY=1`` only means "API 호출 허용".
    Auto-execution + COMPLETE blocker는 ``_openai_advisory_required()`` 가 분리하여 담당합니다.
    """
    return os.environ.get("ENABLE_GPT_ADVISORY") == "1"


def _openai_advisory_required() -> bool:
    """``ENABLE_GPT_ADVISORY_REQUIRED=1`` 일 때만 자동 실행 + unresolved CRITICAL이 COMPLETE를 차단.

    기본 모드(REQUIRED 미설정)에서는 advisory가 수동 진단 도구이며 COMPLETE를 막지 않습니다.
    REQUIRED=1이면 ENABLE_GPT_ADVISORY=1로 간주(API 호출 허용)되므로 별도 flag 없이 자동 실행 가능합니다.
    """
    return os.environ.get("ENABLE_GPT_ADVISORY_REQUIRED") == "1"


def _advisory_run_counts(pid: str) -> Tuple[int, int]:
    """Returns (review_count, api_call_count) for the given pipeline's advisory directory."""
    paths = _contract_paths(pid)
    advisory_root = paths["advisory_root"]
    review_count = 0
    api_call_count = 0
    if advisory_root.exists():
        for rp in sorted(advisory_root.glob("*_review.json")):
            data = _load_json_file(rp, {})
            review_count += 1
            if bool(data.get("api_called", str(data.get("status")) == "COMPLETED")):
                api_call_count += 1
    return review_count, api_call_count


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
        "github_ci_result": root / "gates" / "github_ci_result.json",
        "phase_ci_root": root / "gates" / "phase_ci",
        "failures_root": root / "failures",
        "outputs_manifest": OUTPUTS_ROOT / pipeline_id / "outputs_manifest.json",
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
            mode="wb",
            dir=path.parent,
            delete=False,
            suffix=".tmp",
        ) as tmp:
            payload = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            tmp.write(payload)
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


# IMP-20260531-BBDB MT-1: User Acceptance Nonce Gate 헬퍼 (5개)
# [Purpose]: --user-confirmed 단독 통과 차단을 위한 일회용 승인 코드(nonce) 파일 I/O.
# [Assumptions]: BASE_DIR 또는 현재 작업 디렉토리에 acceptance_request.json 저장.
#                evidence는 파일 경로(SHA-256 계산) 또는 URL(http://, https://) 둘 다 허용.
# [Vulnerability & Risks]:
#   - 40bit nonce 엔트로피는 단일 PR 라이프사이클 동안 충돌 가능성 무시 가능하지만,
#     수십만 회 발급 시 birthday paradox 위험 존재.
#   - 파일 시스템 권한이 group-writable이면 다른 사용자가 nonce 위조 가능 (Windows 권한 의존).
# [Improvement]: HMAC 서명 + per-pipeline 비밀키로 확장하여 위조 차단.

def _issue_acceptance_nonce() -> str:
    """8자 base32 uppercase nonce 생성 (40bit 엔트로피, 일회용 승인 코드용).

    Returns:
        8자 base32 문자열 (예: "A2B3C4D5"). secrets 모듈로 암호학적 안전성 보장.
    Raises:
        없음.
    """
    raw = secrets.token_bytes(5)  # 40 bits → base32 8자
    return base64.b32encode(raw).decode("ascii").rstrip("=")[:8]


def _compute_file_sha256(path: str) -> Optional[str]:
    """파일 SHA-256 계산. 파일이 없거나 읽기 불가하면 None.

    Args:
        path: 파일 경로 문자열.
    Returns:
        SHA-256 hex digest 또는 None (파일 없음/IO 오류).
    Raises:
        없음 (OSError swallow — gate 로직이 None을 검사하여 stale 판정).
    """
    if path is None:
        return None
    if not isinstance(path, str):
        raise TypeError(f"path must be str, got {type(path).__name__}")
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


# ─── IMP-20260603-9934 MT-1: packet SHA-256 계산 ────────────────────────────────
def _compute_packet_sha256(packet_path: "Union[str, Path]") -> Optional[str]:
    """human_acceptance_packet.md 파일의 SHA-256 hex digest를 반환한다.

    acceptance_request.json에 저장되는 packet_sha256 값으로 사용된다.
    파일이 없거나 읽기 불가이면 None을 반환한다 (예외 미발생).

    Args:
        packet_path: human_acceptance_packet.md 경로 (str 또는 Path).
    Returns:
        SHA-256 hex digest 문자열 또는 None (파일 없음 / IO 오류).
    Raises:
        없음.
    """
    if packet_path is None:
        return None
    path_str = str(packet_path)
    return _compute_file_sha256(path_str)


# ─── IMP-20260531-AEF0 MT-1: nonce 재사용 판단 헬퍼 ─────────────────────────────
def _should_reuse_acceptance_nonce(
    existing_req: Dict[str, Any],
    new_pipeline_id: str,
    new_evidence: str,
    new_evidence_sha256: Optional[str],
    new_pr_head_sha: str,
    new_ci_run_id: str,
    force_new: bool = False,
) -> "tuple[bool, str]":
    """기존 acceptance_request를 재사용할지 5-field 비교로 판단.

    동일 조건(pipeline_id / evidence / evidence_sha256 또는 evidence_url /
    pr_head_sha / github_ci_run_id)이고 기존 코드가 PENDING 상태이면 재사용.
    하나라도 다르거나 force_new=True이면 새 코드 발급.

    Args:
        existing_req: 기존 acceptance_request.json 데이터 dict.
        new_pipeline_id: 현재 pipeline_id.
        new_evidence: 결과물 경로 또는 URL.
        new_evidence_sha256: 결과물 파일 SHA-256 (URL이면 None).
        new_pr_head_sha: 현재 PR head commit SHA.
        new_ci_run_id: 현재 GitHub Actions run ID.
        force_new: True이면 조건과 무관하게 항상 새 코드 발급.
    Returns:
        (should_reuse: bool, reason_ko: str) 튜플.
    Raises:
        없음.
    """
    if force_new:
        return False, "--force-new-code 옵션이 지정되어 새 코드를 발급합니다."
    if existing_req.get("status") != "PENDING":
        status_val = existing_req.get("status", "알 수 없음")
        return False, f"기존 코드 상태가 {status_val}이어서 새 코드를 발급합니다."
    if existing_req.get("pipeline_id") != new_pipeline_id:
        return False, "파이프라인 ID가 달라서 새 코드를 발급합니다."
    if existing_req.get("evidence") != new_evidence:
        return False, "결과물 경로가 달라서 새 코드를 발급합니다."
    if new_evidence_sha256 is not None:
        if existing_req.get("evidence_sha256") != new_evidence_sha256:
            return False, "결과물 파일 내용이 달라서(SHA-256 변경) 새 코드를 발급합니다."
    else:
        # URL 기반 증거: evidence_url 필드로 비교
        if existing_req.get("evidence_url") != new_evidence:
            return False, "결과물 URL이 달라서 새 코드를 발급합니다."
    if existing_req.get("pr_head_sha") != new_pr_head_sha:
        return False, "PR head SHA가 달라서(새 커밋이 push됨) 새 코드를 발급합니다."
    if str(existing_req.get("github_ci_run_id", "")) != str(new_ci_run_id):
        return False, "GitHub Actions run ID가 달라서 새 코드를 발급합니다."
    return True, "PR, 결과물, CI 상태가 모두 같습니다."


def _write_acceptance_request(
    pipeline_id: str,
    evidence: str,
    pr_url: str,
    pr_head_sha: str,
    ci_run_id: str,
    packet_path: Optional[str] = None,
) -> Dict[str, Any]:
    """acceptance_request.json 작성 후 데이터 dict 반환.

    IMP-20260603-9934 MT-1: schema_version=2로 확장.
    packet_path, packet_sha256, packet_frozen_at 3개 필드를 추가 저장한다.
    packet_path가 None이면 _packet_output_path() 경로를 사용한다.
    packet 파일이 없으면 packet_sha256=None, packet_frozen_at=None으로 기록한다.

    Args:
        pipeline_id: 활성 pipeline_id (예: IMP-20260603-9934).
        evidence: 결과물 경로(파일) 또는 URL(http://, https://).
        pr_url: 현재 PR URL (gh CLI 없으면 빈 문자열).
        pr_head_sha: PR head commit SHA (gh CLI 없으면 빈 문자열).
        ci_run_id: GitHub Actions run ID (gh CLI 없으면 빈 문자열).
        packet_path: human_acceptance_packet.md 절대 경로 (None이면 자동 탐색).
    Returns:
        기록된 acceptance_request 데이터 dict (status=PENDING, schema_version=2).
    Raises:
        TypeError: pipeline_id 또는 evidence가 None.
        ValueError: pipeline_id가 빈 문자열.
    """
    if pipeline_id is None:
        raise TypeError("pipeline_id must not be None")
    if evidence is None:
        raise TypeError("evidence must not be None")
    if not isinstance(pipeline_id, str):
        raise TypeError(f"pipeline_id must be str, got {type(pipeline_id).__name__}")
    if not isinstance(evidence, str):
        raise TypeError(f"evidence must be str, got {type(evidence).__name__}")
    if len(pipeline_id) == 0:
        raise ValueError("pipeline_id must not be empty")
    nonce = _issue_acceptance_nonce()
    request_id = str(uuid.uuid4())[:8]
    is_url = evidence.startswith(("http://", "https://"))
    evidence_sha256 = None if is_url else _compute_file_sha256(evidence)

    # IMP-20260603-9934 MT-1: packet freeze 필드 계산
    resolved_packet_path: Path = (
        Path(packet_path) if packet_path is not None else _packet_output_path()
    )
    computed_packet_sha256 = _compute_packet_sha256(resolved_packet_path)
    packet_frozen_at: Optional[str] = _now() if computed_packet_sha256 is not None else None

    data: Dict[str, Any] = {
        "schema_version": 2,
        "pipeline_id": pipeline_id,
        "request_id": request_id,
        "nonce": nonce,
        "created_at": _now(),
        "pr_url": pr_url or "",
        "pr_head_sha": pr_head_sha or "",
        "github_ci_run_id": str(ci_run_id) if ci_run_id else "",
        "evidence": evidence,
        "evidence_sha256": evidence_sha256,
        "evidence_url": evidence if is_url else None,
        "status": "PENDING",
        # IMP-20260603-9934 MT-1: Final Packet Freeze Guard 필드 (schema_version=2)
        "packet_path": str(resolved_packet_path),
        "packet_sha256": computed_packet_sha256,
        "packet_frozen_at": packet_frozen_at,
    }
    with open(ACCEPTANCE_REQUEST_FILE, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    return data


def _load_acceptance_request() -> Optional[Dict[str, Any]]:
    """acceptance_request.json 로드. 없거나 파싱 오류 시 None.

    Returns:
        파싱된 dict 또는 None (파일 없음/JSON 오류).
    Raises:
        없음 (OSError/JSONDecodeError swallow).
    """
    try:
        with open(ACCEPTANCE_REQUEST_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return None
        return data
    except (OSError, json.JSONDecodeError):
        return None


def _consume_acceptance_request(req: Dict[str, Any], result: str) -> None:
    """acceptance_request.json 상태를 CONSUMED로 갱신하여 재기록.

    Args:
        req: 기존 acceptance_request dict.
        result: ACCEPT 또는 REJECT.
    Raises:
        TypeError: req 또는 result가 None.
        ValueError: result가 ACCEPT/REJECT 외 값.
    """
    if req is None:
        raise TypeError("req must not be None")
    if result is None:
        raise TypeError("result must not be None")
    if not isinstance(req, dict):
        raise TypeError(f"req must be dict, got {type(req).__name__}")
    if not isinstance(result, str):
        raise TypeError(f"result must be str, got {type(result).__name__}")
    if result not in {"ACCEPT", "REJECT"}:
        raise ValueError(f"result must be ACCEPT or REJECT, got {result!r}")
    req["status"] = "CONSUMED"
    req["consumed_at"] = _now()
    req["consumed_result"] = result
    with open(ACCEPTANCE_REQUEST_FILE, "w", encoding="utf-8") as fh:
        json.dump(req, fh, ensure_ascii=False, indent=2)


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(BASE_DIR))
    except ValueError:
        return str(path.resolve())


def _deployment_root() -> Path:
    configured = os.environ.get("PIPELINE_DEPLOY_ROOT", "").strip()
    if configured:
        root = Path(configured)
        parent = root.parent if root.parent != root else root
        if not parent.exists():
            _die(
                "[DEPLOY ROOT BLOCKED] PIPELINE_DEPLOY_ROOT 상위 폴더가 없습니다: "
                f"{parent}. Google Drive를 마운트하거나 PIPELINE_DEPLOY_ROOT를 실제 존재하는 폴더로 바꾸세요."
            )
        return root
    for candidate in DEFAULT_DEPLOY_ROOT_CANDIDATES:
        if candidate.exists() or candidate.parent.exists():
            return candidate
    candidates = ", ".join(str(path) for path in DEFAULT_DEPLOY_ROOT_CANDIDATES)
    _die(
        "[DEPLOY ROOT BLOCKED] Google Drive 배포 폴더를 찾지 못했습니다. "
        f"확인한 경로: {candidates}. Google Drive를 마운트하거나 PIPELINE_DEPLOY_ROOT를 설정하세요."
    )


PLACEHOLDER_EVIDENCE_VALUES = {"N/A", "NA", "NONE", "SKIP", "SKIPPED", "USER_CONFIRMED", "MANUAL-SMOKE"}


def _split_evidence_items(raw: Any) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        items: List[str] = []
        for item in raw:
            items.extend(_split_evidence_items(item))
        return items
    text = str(raw).strip()
    if not text:
        return []
    parts = re.split(r"[,;\n]+", text)
    return [part.strip() for part in parts if part.strip()]


def _split_evidence_paths(raw: Any) -> List[str]:
    return [item for item in _split_evidence_items(raw) if item.upper() not in PLACEHOLDER_EVIDENCE_VALUES]


def _is_evidence_url(raw: str) -> bool:
    return bool(re.match(r"^https?://[^\s]+$", raw.strip(), flags=re.IGNORECASE))


def _validate_pipeline_branch_isolation(state: Dict[str, Any]) -> None:
    """gates prepare-phase --phase pm 실행 시 브랜치가 pipeline_id를 포함하는지 강제 검증."""
    pipeline_id = state["pipeline_id"]
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, check=True
        )
        current_branch = result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return  # git 미사용 환경 — 검사 생략
    protected = ("main", "master", "HEAD")
    if current_branch in protected:
        _die(
            f"[BRANCH ISOLATION] '{current_branch}' 브랜치에서 gates prepare-phase --phase pm을 실행할 수 없습니다.\n"
            f"  파이프라인: {pipeline_id}\n"
            f"  필요 브랜치: phase-attestation/{pipeline_id}\n"
            f"  실행: git checkout -b phase-attestation/{pipeline_id}"
        )
    if pipeline_id not in current_branch:
        _die(
            f"[BRANCH ISOLATION] 현재 브랜치 '{current_branch}'가 파이프라인 ID '{pipeline_id}'를 포함하지 않습니다.\n"
            f"  다른 파이프라인 브랜치에서 push하면 PR이 오염됩니다.\n"
            f"  필요 브랜치: phase-attestation/{pipeline_id}\n"
            f"  실행: git checkout -b phase-attestation/{pipeline_id}"
        )


def _validate_pr_title_matches_pipeline(state: Dict[str, Any]) -> None:
    """gates accept --result ACCEPT 실행 시 열려 있는 PR 제목에 pipeline_id가 포함되는지 검증."""
    pipeline_id = state["pipeline_id"]
    try:
        pr_result = subprocess.run(
            ["gh", "pr", "view", "--json", "title,number,url"],
            capture_output=True, text=True, encoding="utf-8", check=False
        )
        if pr_result.returncode != 0:
            return  # PR 없음 — 다른 gate에서 차단됨
        if not pr_result.stdout:
            return  # stdout 없음 (인코딩 오류 등) — 검사 생략
        pr_data = json.loads(pr_result.stdout)
        pr_title = pr_data.get("title", "")
        pr_number = pr_data.get("number", "?")
        pr_url = pr_data.get("url", "")
        if pipeline_id not in pr_title:
            _die(
                f"[PR TITLE MISMATCH] PR #{pr_number} 제목에 파이프라인 ID가 없습니다.\n"
                f"  현재 PR 제목: '{pr_title}'\n"
                f"  필요한 파이프라인 ID: [{pipeline_id}]\n"
                f"  PR URL: {pr_url}\n"
                f"  수정 후 다시 실행하세요: gh pr edit {pr_number} --title '[{pipeline_id}] ...'"
            )
    except (subprocess.CalledProcessError, FileNotFoundError, json.JSONDecodeError, ValueError):
        return  # gh CLI 미설치 환경 — 검사 생략


# IMP-20260519-E979: Final Acceptance Readiness Gate 상수 정의
# (Documentation-Code Drift 방지 — CLAUDE.md와 동기화됨)
# Blocker 3 수정: "Draft PR" 임시 문구를 명시적으로 추가.
# 매칭 방식: PR 본문을 줄 단위로 끊고, strip한 줄이 패턴으로 "시작"하면 임시 PR로 간주한다.
# (정상 PR이 본문 문장 중간에 'Draft PR' 같은 단어를 언급하는 경우를 거짓 양성으로
#  차단하지 않기 위해, substring 전체 검색이 아니라 줄-접두 검색을 사용한다.)
TEMPORARY_PR_BODY_PATTERNS: List[str] = [
    "작업 중",
    "Draft PR",
    "PM phase attestation CI 확인용",
    "작업 중입니다",
    "진행 중",
    "Dev 완료 후 업데이트됩니다",
    "아직 Dev 구현 완료 전",
    # IMP-20260531-BBDB: PR #368 stale 문구 패턴 명시 추가
    "Dev phase 진행 중",
    "빌드 완료 후 업데이트 예정",
    "KittingMapper.exe 빌드 완료 후",
    "빌드 완료 후 업데이트됩니다",
]

# IMP-20260531-BBDB MT-1: User Acceptance Nonce Gate
# acceptance_request.json 파일명 + ACCEPT/REJECT 코드 정규식.
# nonce는 8자 base32 uppercase (예: A2B3C4D5). pipeline_id 패턴: TYPE-YYYYMMDD-XXXX.
ACCEPTANCE_REQUEST_FILE = "acceptance_request.json"
ACCEPT_CODE_PATTERN = re.compile(r"^ACCEPT-([A-Z]+-\d{8}-[A-Z0-9]{4})-([A-Z2-7]{8})$")
REJECT_CODE_PATTERN = re.compile(r"^REJECT-([A-Z]+-\d{8}-[A-Z0-9]{4})-([A-Z2-7]{8})$")

# PR 본문에 반드시 포함되어야 하는 섹션 헤더 목록 (순서 무관, OR 쌍 지원)
# 형식: str → 단일 필수 / tuple → 안의 항목 중 하나라도 있으면 통과 (OR 조건)
# Blocker 3 수정: 첫 항목에 "최종 판단 요약"을 OR 후보로 추가.
PR_REQUIRED_SECTIONS: List[Any] = [
    ("작업 요약", "최종 판단 요약", "이번 요청과 완료 결과"),  # 셋 중 하나 (OR)
    "사용자가 확인할 결과물",
    "기대 결과와 실제 결과",
    "중요한 선택과 트레이드오프",
    "검증",
]

# Blocker 2: human acceptance packet readiness — GitHub PR 댓글 태그
_ACCEPTANCE_PACKET_COMMENT_TAG = "<!-- pipeline-human-acceptance-packet -->"
# 판단 정보 상태: **정보 부족** 또는 판단 정보 상태: 정보 부족 형태를 매칭.
# 단순 substring "정보 부족"은 false positive를 일으키므로 regex 기반으로 변경.
# "판단 정보 상태: 정보 부족이면 ..." 같은 안내 문구는 PASS.
_ACCEPTANCE_PACKET_INSUFFICIENT_MARKER = "정보 부족"  # 레거시 참조용 (삭제 금지)
_ACCEPTANCE_PACKET_INSUFFICIENT_PATTERN = re.compile(
    r"판단\s*정보\s*상태\s*:\s*\*{0,2}\s*정보\s*부족\s*\*{0,2}"
)
_ACCEPTANCE_PACKET_SUFFICIENT_MARKER = "판단 가능"
# PR URL → owner/repo 추출용 정규식 (모듈 레벨 1회 컴파일 — Rule D2)
_PR_URL_REPO_PATTERN = re.compile(
    r"github\.com/([^/]+/[^/]+)/pull/(\d+)"
)


def _find_temporary_pr_body_pattern(pr_body: str) -> Optional[str]:
    """PR 본문에 임시(placeholder) 문구가 줄 단위로 존재하면 그 패턴을 반환한다.

    매칭 규칙: 본문을 줄 단위로 끊고, 각 줄을 strip한 뒤
    그 줄이 TEMPORARY_PR_BODY_PATTERNS 중 하나로 "시작"하면 임시 문구로 간주한다.
    (정상 PR이 문장 중간에 'Draft PR' 같은 단어를 설명용으로 언급하는 경우는
     거짓 양성으로 차단하지 않기 위해 substring 전체 검색을 쓰지 않는다.)

    Args:
        pr_body: PR 본문 전체 텍스트.
    Returns:
        탐지된 임시 문구 패턴 문자열. 없으면 None.
    """
    if not pr_body:
        return None
    for raw_line in pr_body.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        for pattern in TEMPORARY_PR_BODY_PATTERNS:
            if line.startswith(pattern):
                return pattern
    return None


def _acceptance_blocked(
    failure_code: str,
    blocked_reason: str,
    *,
    return_phase: str = "build",
    missing_sections: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """_check_acceptance_readiness용 BLOCKED 결과 dict 생성 헬퍼.

    Args:
        failure_code: 실패 원인 코드 (failure_packet schema_v2의 failure_code).
        blocked_reason: 사용자에게 보여줄 차단 사유 (한국어).
        return_phase: 재작업 담당 phase (기본 build).
        missing_sections: PR 본문 누락 섹션 목록 (없으면 빈 리스트).
    Returns:
        status=BLOCKED, allow_accept=False인 결과 dict.
    """
    return {
        "status": "BLOCKED",
        "failure_code": failure_code,
        "failure_category": "missing_evidence",
        "blocked_reason": blocked_reason,
        "missing_sections": list(missing_sections) if missing_sections else [],
        "return_phase": return_phase,
        "allow_accept": False,
    }


def _check_acceptance_packet_via_github(pr_url: str) -> Dict[str, Any]:
    """GitHub PR 댓글에서 human acceptance packet readiness를 검증한다.

    Blocker 2: 로컬 파일 대신 GitHub PR 댓글을 기본 검증 대상으로 한다.
    `<!-- pipeline-human-acceptance-packet -->` 태그 댓글을 찾아
    '판단 가능'/'정보 부족' 상태를 판정한다.

    Args:
        pr_url: gh pr view 가 반환한 PR URL (owner/repo/pr_number 추출용).
    Returns:
        status=PASS 또는 status=BLOCKED 결과 dict.
        - 태그 댓글 없음 → acceptance_packet_missing
        - 댓글에 '정보 부족' → acceptance_packet_insufficient
        - 댓글 조회 실패(API/네트워크) → pr_comments_fetch_failed
        - '판단 가능' 확인 → PASS
    """
    pass_result: Dict[str, Any] = {
        "status": "PASS",
        "failure_code": "",
        "failure_category": "",
        "blocked_reason": None,
        "missing_sections": [],
        "allow_accept": True,
    }

    # PR URL에서 owner/repo, PR 번호 추출
    match = _PR_URL_REPO_PATTERN.search(pr_url or "")
    if match is None:
        return _acceptance_blocked(
            "pr_comments_fetch_failed",
            (
                f"PR URL에서 저장소/번호를 추출하지 못해 acceptance packet 댓글을 "
                f"조회할 수 없습니다: '{pr_url}'. PR 상태를 확인한 뒤 다시 실행하세요."
            ),
        )
    repo_slug = match.group(1)
    pr_number = match.group(2)

    # gh api 로 PR 댓글 목록 조회
    try:
        comments_result = subprocess.run(
            ["gh", "api", f"repos/{repo_slug}/issues/{pr_number}/comments",
             "--paginate"],
            capture_output=True, text=True, encoding="utf-8", check=False,
        )
    except (FileNotFoundError, OSError) as exc:
        return _acceptance_blocked(
            "pr_comments_fetch_failed",
            (
                f"GitHub PR 댓글 조회에 실패했습니다(gh CLI 오류: {exc}). "
                "네트워크와 gh 인증 상태를 확인한 뒤 다시 실행하세요."
            ),
        )

    if comments_result.returncode != 0:
        return _acceptance_blocked(
            "pr_comments_fetch_failed",
            (
                "GitHub PR 댓글 조회에 실패했습니다"
                f"(gh api exit code {comments_result.returncode}). "
                + ((comments_result.stderr or "").strip()[:200])
                + " 네트워크와 gh 인증 상태를 확인한 뒤 다시 실행하세요."
            ),
        )

    raw_stdout = (comments_result.stdout or "").strip()
    if not raw_stdout:
        # 댓글이 0개여도 acceptance packet 댓글은 없는 것 — missing 처리
        return _acceptance_blocked(
            "acceptance_packet_missing",
            (
                "GitHub PR에 사용자 판단용 acceptance packet 댓글이 없습니다. "
                "최종 확인 안내 댓글이 게시된 뒤 다시 실행하세요."
            ),
        )

    try:
        comments = json.loads(raw_stdout)
    except (json.JSONDecodeError, ValueError) as exc:
        return _acceptance_blocked(
            "pr_comments_fetch_failed",
            (
                f"GitHub PR 댓글 응답을 해석하지 못했습니다(JSON 오류: {exc}). "
                "잠시 후 다시 실행하세요."
            ),
        )

    if not isinstance(comments, list):
        return _acceptance_blocked(
            "pr_comments_fetch_failed",
            (
                "GitHub PR 댓글 응답 형식이 올바르지 않습니다. "
                "잠시 후 다시 실행하세요."
            ),
        )

    # acceptance packet 태그 댓글 탐색
    packet_comment_body: Optional[str] = None
    for comment in comments:
        if not isinstance(comment, dict):
            continue
        body = comment.get("body") or ""
        if _ACCEPTANCE_PACKET_COMMENT_TAG in body:
            packet_comment_body = body  # 마지막(최신) 태그 댓글을 사용

    if packet_comment_body is None:
        return _acceptance_blocked(
            "acceptance_packet_missing",
            (
                "GitHub PR에 사용자 판단용 acceptance packet 댓글이 없습니다"
                f"('{_ACCEPTANCE_PACKET_COMMENT_TAG}' 태그 미발견). "
                "최종 확인 안내 댓글이 게시된 뒤 다시 실행하세요."
            ),
        )

    # 정보 부족 우선 검사 (정보 부족이 있으면 판단 가능 문구가 있어도 BLOCKED)
    # AB-1: regex 기반 검사 — "판단 정보 상태: 정보 부족" 형식만 매칭
    # 단순 substring은 "판단 정보 상태가 정보 부족이면..." 같은 안내 문구를 false positive 처리함
    if _ACCEPTANCE_PACKET_INSUFFICIENT_PATTERN.search(packet_comment_body):
        return _acceptance_blocked(
            "acceptance_packet_insufficient",
            (
                "GitHub PR의 acceptance packet 댓글 판단 정보 상태가 '정보 부족'입니다. "
                "PR 본문과 acceptance packet 댓글을 보완한 뒤 다시 실행하세요."
            ),
        )

    if _ACCEPTANCE_PACKET_SUFFICIENT_MARKER in packet_comment_body:
        return pass_result

    # 태그 댓글은 있으나 판단 가능/정보 부족 상태 표기가 없음 — 불완전 packet
    return _acceptance_blocked(
        "acceptance_packet_insufficient",
        (
            "GitHub PR의 acceptance packet 댓글에 판단 정보 상태"
            f"('{_ACCEPTANCE_PACKET_SUFFICIENT_MARKER}'/'"
            f"{_ACCEPTANCE_PACKET_INSUFFICIENT_MARKER}') 표기가 없습니다. "
            "acceptance packet 댓글을 보완한 뒤 다시 실행하세요."
        ),
    )


def _check_acceptance_packet_via_local_file(state: Dict[str, Any]) -> Dict[str, Any]:
    """로컬 human_acceptance_packet*.md 파일로 readiness를 검증하는 fallback.

    Blocker 2: 테스트 환경 전용 fallback. 환경변수
    PIPELINE_TEST_ACCEPTANCE_PACKET_PATH 가 설정된 경우 해당 경로를 우선 사용한다.

    Args:
        state: 파이프라인 state dict (pipeline_id 추출용).
    Returns:
        status=PASS 또는 status=BLOCKED 결과 dict.
    """
    pass_result: Dict[str, Any] = {
        "status": "PASS",
        "failure_code": "",
        "failure_category": "",
        "blocked_reason": None,
        "missing_sections": [],
        "allow_accept": True,
    }

    pid = str(state.get("pipeline_id") or "")
    env_override = os.environ.get("PIPELINE_TEST_ACCEPTANCE_PACKET_PATH", "").strip()
    packet_candidates: List[Path] = []
    if env_override:
        packet_candidates.append(Path(env_override))
    packet_candidates.extend([
        BASE_DIR / f"human_acceptance_packet_{pid}.md",
        BASE_DIR / "human_acceptance_packet_v2.md",
        BASE_DIR / "human_acceptance_packet.md",
    ])

    for packet_path in packet_candidates:
        if packet_path.is_file():
            try:
                packet_text = packet_path.read_text(encoding="utf-8")
            except OSError:
                break  # 읽기 실패 시 검사 생략
            # AB-1: regex 기반 검사 — "판단 정보 상태: 정보 부족" 형식만 매칭
            if _ACCEPTANCE_PACKET_INSUFFICIENT_PATTERN.search(packet_text):
                return _acceptance_blocked(
                    "acceptance_packet_insufficient",
                    (
                        "human acceptance packet의 판단 정보 상태가 '정보 부족'입니다. "
                        "PR 본문과 acceptance packet을 보완한 뒤 다시 실행하세요."
                    ),
                )
            break  # 첫 번째 존재 파일만 검사

    return pass_result


# ===================================================================
# IMP-20260520-D0BB: Protocol Consistency Guard
# [Purpose]: gates accept 전 PR body / acceptance packet / 실제 CI run ID /
#   head SHA / changed files 사이의 불일치를 hard gate로 차단한다.
#   IMP-20260519-E979가 "정보 부족"을 막는다면, 이 가드는 "정보 불일치"를 막는다.
# [Assumptions]: 호출자(_run_protocol_consistency_check)가 gh CLI로 수집한
#   PR 메타데이터를 인자로 전달한다. 이 함수 자체는 순수 함수이며 외부 I/O 없음.
# [Vulnerability & Risks]: PR body 자유 형식 텍스트를 정규식으로 파싱하므로,
#   비정형 본문에서 run ID / SHA / 파일명을 놓칠 수 있다. 이를 false PASS로
#   흘리지 않도록, 추출 실패 시 해당 검사는 SKIP하고 다른 검사로 넘어간다.
# [Improvement]: PR body에 기계가 읽는 구조화 블록(예: HTML 주석 JSON)을
#   강제하면 정규식 의존을 제거할 수 있다.
# ===================================================================

# acceptance packet 식별 태그 — _ACCEPTANCE_PACKET_COMMENT_TAG 재사용
# GitHub Actions run ID 추출 정규식 (모듈 레벨 1회 컴파일 — Rule D2)
# "runs/\d+" URL 기반 추출: "dev-abc123", "pm_planner-xxx" 등
# phase attestation receipt run ID는 URL에 "runs/"가 없으므로 자동 제외됨.
_CONSISTENCY_RUN_ID_PATTERN = re.compile(r"runs/(\d+)")
# head SHA 추출 정규식 (7~40자리 16진수, 단어 경계)
_CONSISTENCY_SHA_PATTERN = re.compile(r"\b([0-9a-f]{7,40})\b")
# 테스트 통과 수 추출 정규식 (예: "434 PASS", "408 passed")
_CONSISTENCY_TEST_COUNT_PATTERN = re.compile(
    r"(\d+)\s*(?:PASS|passed|tests?\s*PASS)"
)
# trust-root 파일 / 패턴 (PR body 설명 의무 대상)
CONSISTENCY_TRUST_ROOT_FILES = [
    "pipeline.py", "CLAUDE.md", ".bandit", ".gitattributes",
    "setup.cfg", "pyproject.toml",
]
CONSISTENCY_TRUST_ROOT_PATTERNS = [
    ".claude/", ".github/workflows/", ".github/CODEOWNERS", "tests/", "test_",
]


def _consistency_extract_run_ids(text: str) -> List[str]:
    """텍스트에서 GitHub Actions run ID(숫자)를 모두 추출한다.

    Args:
        text: 검색 대상 텍스트 (PR body 또는 acceptance packet).
    Returns:
        추출된 run ID 문자열 리스트 (중복 제거, 등장 순서 유지).
    """
    if not text:
        return []
    found: List[str] = []
    for match in _CONSISTENCY_RUN_ID_PATTERN.findall(text):
        if match not in found:
            found.append(match)
    return found


def _consistency_extract_shas(text: str) -> List[str]:
    """텍스트에서 16진수 SHA 후보(7~40자리)를 모두 추출한다.

    Args:
        text: 검색 대상 텍스트.
    Returns:
        추출된 SHA 후보 문자열 리스트 (중복 제거, 등장 순서 유지).
    """
    if not text:
        return []
    found: List[str] = []
    for match in _CONSISTENCY_SHA_PATTERN.findall(text):
        if match not in found:
            found.append(match)
    return found


def _consistency_listed_files(text: str) -> "tuple[set, bool]":
    """PR body / packet의 불릿 목록(`- 파일`, `* 파일`)에서 파일명을 추출한다.

    불릿 첫 토큰 중 `.`(확장자/숨김파일) 또는 `/`(경로 구분자)를 포함하는
    토큰만 파일 후보로 인정한다. `- 장점: 빠름`, `- python -m pytest` 같은
    서술/명령 텍스트의 첫 토큰은 파일이 아니므로 제외한다 (비파일 오탐 방지,
    IMP-20260521-90F4 MT-1).

    AB-2 (BUG-20260521-C675): bold 마커(**) 및 em dash(—) 뒤 설명을 정규화한다.
    AB-3 (BUG-20260521-C675): `...`, `... 외 N개 파일`, `and N more files` 같은
    truncation marker를 파일명으로 취급하지 않는다.

    Args:
        text: 검색 대상 텍스트.
    Returns:
        (files, truncated) — 파일명 집합과 truncation 감지 여부.
    """
    # AB-3: truncation marker 패턴 (파일명으로 취급하지 않음)
    _TRUNCATION_PATTERN = re.compile(
        r"^\.\.\.$|^\.{3}\s*외\s*\d+\s*개?\s*파일|^and\s+\d+\s+more\s+files?",
        re.IGNORECASE,
    )
    if not text:
        return set(), False
    files: set = set()
    truncated = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not (line.startswith("- ") or line.startswith("* ")):
            continue
        # 불릿 기호 제거 후 첫 토큰을 파일 후보로 본다.
        body = line[2:].strip()
        if not body:
            continue
        # AB-2: bold 마커(**) 제거
        body = re.sub(r"\*\*", "", body)
        # 백틱 제거
        body = body.replace("`", "")
        # AB-2: 괄호 안 설명 제거 — `tests/foo.py (추가 10줄)` → `tests/foo.py`
        body = re.sub(r"\([^)]*\)", "", body).strip()
        # AB-2: em dash(—) 및 일반 dash(–) 뒤 설명 제거 — `pipeline.py — 전체 파싱 수정`
        body = re.sub(r"\s*[—–]\s*.*$", "", body).strip()
        tokens = body.split()
        token = tokens[0] if tokens else ""
        # AB-2: colon 뒤 설명 제거 — `- 수정됨: **tests/test_x.py**` → `수정됨:` → 다음 토큰
        # `수정됨:` 같은 한국어 라벨은 파일이 아니므로 다음 토큰을 시도한다.
        # `pipeline.py:` 처럼 파일명에 붙은 콜론은 콜론만 제거하고 파일명 보존.
        if token.endswith(":") and len(tokens) > 1:
            base = token.rstrip(":")
            if "." in base or "/" in base or "\\" in base:
                # `pipeline.py:` 또는 `tests/bar.py:` — 파일명에 붙은 콜론, 콜론만 제거
                token = base
            else:
                # `수정됨:` 같은 한국어 라벨 — 다음 토큰에서 파일 후보 찾기
                token = ""  # nosec B105 — 라벨 뒤 파일 후보 초기화, 비밀번호 아님
                for t in tokens[1:]:
                    t_clean = re.sub(r"\*\*", "", t).rstrip(":").strip()
                    if t_clean and ("." in t_clean or "/" in t_clean or "\\" in t_clean):
                        token = t_clean
                        break
        else:
            # 후행 콜론 제거 — `- 변경된 파일:` 같은 라벨성 토큰의 콜론 정리.
            token = token.rstrip(":")
        if not token:
            continue
        # AB-3: truncation marker 감지
        if _TRUNCATION_PATTERN.search(token):
            truncated = True
            continue
        # IMP-20260525-AA88: 점수/타이밍 오탐 필터
        # `120/120` 같은 숫자/숫자 패턴(점수 표기)은 파일 경로가 아니다.
        if re.search(r"^\d+/\d+$", token):
            continue
        # `34.74s`, `0.5s` 같은 숫자.숫자s 패턴(타이밍 표기)은 파일 확장자가 아니다.
        if re.search(r"^\d+(\.\d+)?s$", token):
            continue
        # IMP-20260531-BBDB: URL은 파일 경로가 아니다 — http/https로 시작하면 제외.
        if token.startswith("http://") or token.startswith("https://"):
            continue
        # 파일처럼 보이는 토큰만 인정: 경로(`/`) 포함이거나 올바른 확장자(`.` + 영숫자 접미어) 보유.
        # 한국어 문장 끝의 `.`(예: `됩니다.`)은 파일명으로 취급하지 않는다 (IMP-20260522-29C1 fix-forward v5).
        if "/" not in token and "\\" not in token and not re.search(r"\.[A-Za-z0-9_]{1,15}$", token):
            continue
        files.add(token)
    return files, truncated


def _check_protocol_consistency(
    pr_body: str,
    acceptance_packet_body: str,
    pr_changed_files: List[str],
    pr_head_sha: str,
    latest_ci_run_id: str,
    latest_ci_run_conclusion: str,
) -> Dict[str, Any]:
    """PR body / acceptance packet / 실제 CI 상태 사이의 일치성을 검사하는 순수 함수.

    검사 항목 (하나라도 불일치하면 BLOCKED):
        A. CI run ID 일치 (stale_run_id)
        B. head SHA 일치 (stale_head_sha)
        C. 테스트 통과 수 일치 (test_count_mismatch)
        D. changed files 일치 (changed_files_mismatch)
        E. trust-root 변경 설명 의무 (trust_root_change_undocumented)
        F. stale 파일 설명 탐지 (stale_file_description)

    이 함수는 순수 함수이다 — subprocess, _save, 파일 I/O를 호출하지 않는다.

    Args:
        pr_body: PR 본문 전체 텍스트.
        acceptance_packet_body: acceptance packet 댓글 본문 (없으면 빈 문자열).
        pr_changed_files: 실제 PR diff의 변경 파일 경로 리스트.
        pr_head_sha: 실제 PR head commit SHA.
        latest_ci_run_id: 실제 최신 GitHub Actions run ID.
        latest_ci_run_conclusion: 최신 CI run의 결론 (success 등).
    Returns:
        {
            "status": "PASS" | "BLOCKED",
            "failure_code": str,
            "failure_category": str,
            "blocked_reason": Optional[str],
            "return_phase": str,
            "allow_accept": bool,
            "details": Dict[str, Any],
        }
    """
    # --- 입력 방어 (AL.type_valid) ---
    if pr_body is None:
        raise TypeError("pr_body must not be None")
    if not isinstance(pr_body, str):
        raise TypeError(
            f"pr_body must be str, got {type(pr_body).__name__}"
        )
    if acceptance_packet_body is None:
        raise TypeError("acceptance_packet_body must not be None")
    if not isinstance(acceptance_packet_body, str):
        raise TypeError(
            f"acceptance_packet_body must be str, "
            f"got {type(acceptance_packet_body).__name__}"
        )
    if pr_changed_files is None:
        raise TypeError("pr_changed_files must not be None")
    if not isinstance(pr_changed_files, list):
        raise TypeError(
            f"pr_changed_files must be list, "
            f"got {type(pr_changed_files).__name__}"
        )
    if pr_head_sha is None:
        raise TypeError("pr_head_sha must not be None")
    if not isinstance(pr_head_sha, str):
        raise TypeError(
            f"pr_head_sha must be str, got {type(pr_head_sha).__name__}"
        )
    if latest_ci_run_id is None:
        raise TypeError("latest_ci_run_id must not be None")
    if not isinstance(latest_ci_run_id, str):
        raise TypeError(
            f"latest_ci_run_id must be str, "
            f"got {type(latest_ci_run_id).__name__}"
        )
    # latest_ci_run_conclusion: 검사에 직접 쓰지 않지만 시그니처 계약 유지.
    if latest_ci_run_conclusion is not None and not isinstance(
        latest_ci_run_conclusion, str
    ):
        raise TypeError(
            f"latest_ci_run_conclusion must be str, "
            f"got {type(latest_ci_run_conclusion).__name__}"
        )

    def _blocked(
        failure_code: str,
        blocked_reason: str,
        details: Dict[str, Any],
    ) -> Dict[str, Any]:
        """BLOCKED 결과 dict 생성 (내부 클로저)."""
        return {
            "status": "BLOCKED",
            "failure_code": failure_code,
            "failure_category": "protocol_violation",
            "blocked_reason": blocked_reason,
            "return_phase": "build",
            "allow_accept": False,
            "details": details,
        }

    has_packet_tag = _ACCEPTANCE_PACKET_COMMENT_TAG in acceptance_packet_body

    # --- 검사 A: CI run ID 일치 ---
    body_run_ids = _consistency_extract_run_ids(pr_body)
    packet_run_ids = (
        _consistency_extract_run_ids(acceptance_packet_body)
        if has_packet_tag else []
    )
    if body_run_ids:
        body_run_id = body_run_ids[0]
        if latest_ci_run_id and body_run_id != latest_ci_run_id:
            return _blocked(
                "stale_run_id",
                (
                    f"PR 본문의 GitHub CI run ID({body_run_id})가 실제 "
                    f"최신 CI run ID({latest_ci_run_id})와 다릅니다. "
                    "stale run ID를 최신 값으로 갱신하세요."
                ),
                {
                    "expected_run_id": latest_ci_run_id,
                    "pr_body_run_id": body_run_id,
                    "location": "pr_body",
                },
            )
    if packet_run_ids:
        packet_run_id = packet_run_ids[0]
        if latest_ci_run_id and packet_run_id != latest_ci_run_id:
            return _blocked(
                "stale_run_id",
                (
                    f"acceptance packet의 GitHub CI run ID({packet_run_id})가 "
                    f"실제 최신 CI run ID({latest_ci_run_id})와 다릅니다. "
                    "stale run ID를 최신 값으로 갱신하세요."
                ),
                {
                    "expected_run_id": latest_ci_run_id,
                    "packet_run_id": packet_run_id,
                    "location": "acceptance_packet",
                },
            )
    if body_run_ids and packet_run_ids:
        if body_run_ids[0] != packet_run_ids[0]:
            return _blocked(
                "stale_run_id",
                (
                    f"PR 본문의 run ID({body_run_ids[0]})와 acceptance packet의 "
                    f"run ID({packet_run_ids[0]})가 서로 다릅니다. "
                    "두 값을 최신 run ID로 일치시키세요."
                ),
                {
                    "expected_run_id": latest_ci_run_id,
                    "pr_body_run_id": body_run_ids[0],
                    "packet_run_id": packet_run_ids[0],
                    "location": "pr_body_vs_packet",
                },
            )

    # --- 검사 B: head SHA 일치 ---
    # run ID(순수 숫자)는 SHA 후보에서 제외 — 16진수지만 SHA가 아님.
    body_run_id_set = set(body_run_ids)
    if pr_head_sha:
        head_sha_lower = pr_head_sha.lower()
        body_sha_candidates = [
            s for s in _consistency_extract_shas(pr_body)
            if s not in body_run_id_set
        ]
        # SHA 후보 중 head SHA의 prefix(>=7자)인 것이 하나라도 있으면 PASS.
        if body_sha_candidates:
            sha_match = any(
                len(s) >= 7 and head_sha_lower.startswith(s)
                for s in body_sha_candidates
            )
            if not sha_match:
                return _blocked(
                    "stale_head_sha",
                    (
                        f"PR 본문의 head SHA가 실제 PR head SHA"
                        f"({pr_head_sha[:12]}...)와 일치하지 않습니다. "
                        "최신 commit SHA로 갱신하세요."
                    ),
                    {
                        "expected_head_sha": pr_head_sha,
                        "pr_body_sha_candidates": body_sha_candidates,
                        "location": "pr_body",
                    },
                )

    # --- 검사 C: 테스트 통과 수 일치 ---
    # findall 전체 결과 중 최대값을 전체 테스트 수로 사용한다.
    # 개별 파일 테스트 수(예: "20 passed")와 전체 테스트 수(예: "456 PASS")가
    # 공존할 때, 첫 번째 값 대신 최대값을 사용해야 전체 수를 올바르게 비교한다.
    body_counts_raw = _CONSISTENCY_TEST_COUNT_PATTERN.findall(pr_body)
    packet_counts_raw = (
        _CONSISTENCY_TEST_COUNT_PATTERN.findall(acceptance_packet_body)
        if has_packet_tag else []
    )
    body_count = max(int(c) for c in body_counts_raw) if body_counts_raw else None
    packet_count = max(int(c) for c in packet_counts_raw) if packet_counts_raw else None
    if body_count is not None and packet_count is not None:
        if body_count != packet_count:
            return _blocked(
                "test_count_mismatch",
                (
                    f"PR 본문의 테스트 통과 수({body_count} PASS)와 "
                    f"acceptance packet의 테스트 통과 수"
                    f"({packet_count} PASS)가 다릅니다. "
                    "두 값을 동일 수치로 맞추세요."
                ),
                {
                    "pr_body_test_count": str(body_count),
                    "packet_test_count": str(packet_count),
                },
            )

    # --- 검사 F: stale 파일 설명 탐지 ---
    # PR body에 실제 변경되지 않은 파일이 기술된 경우 차단한다.
    # acceptance packet의 파일 목록은 검사 D에서 별도 처리한다.
    changed_set = {str(f).replace("\\", "/") for f in pr_changed_files}
    body_listed_files, body_truncated = _consistency_listed_files(pr_body)
    if has_packet_tag:
        packet_listed_files, packet_truncated = _consistency_listed_files(acceptance_packet_body)
    else:
        packet_listed_files, _ = set(), False  # packet_truncated unused in this branch
    for listed in body_listed_files:
        normalized = listed.replace("\\", "/")
        if normalized and normalized not in changed_set:
            # PR body에 적힌 파일이 실제 diff에 없음 → stale 설명.
            return _blocked(
                "stale_file_description",
                (
                    f"PR 본문의 변경 파일 목록에 "
                    f"'{listed}'가 있으나 실제 PR diff에는 변경되지 않았습니다. "
                    "실제 변경되지 않은 파일 설명을 제거하세요."
                ),
                {
                    "stale_file": listed,
                    "actual_changed_files": sorted(changed_set),
                },
            )

    # --- 검사 D: changed files 일치 ---
    # PR body에 명시적 파일 목록이 있을 때만 body 기반 누락 파일 검사한다.
    if body_listed_files:
        body_listed_set = {f.replace("\\", "/") for f in body_listed_files}
        # 실제 changed files 중 body 목록에 없는 핵심 파일을 찾는다.
        # 테스트 파일(tests/test_*.py 등)은 다수 변경 시 모두 나열이 어려우므로
        # 비-테스트 핵심 파일만 검사한다.
        missing_from_body: List[str] = []
        trust_root_files = {".github/workflows", "pipeline.py", "CLAUDE.md", "tests/", ".claude/agents/"}
        for actual in sorted(changed_set):
            base = actual.rsplit("/", 1)[-1]
            is_test_file = (
                actual.startswith("tests/")
                or base.startswith("test_")
            )
            if is_test_file:
                continue
            if actual not in body_listed_set:
                # AB-3: truncation marker가 있으면 일반 파일 누락은 허용.
                # trust-root 파일은 truncation이 있어도 반드시 명시 필요.
                is_trust_root = any(actual.startswith(tr) for tr in trust_root_files)
                if body_truncated and not is_trust_root:
                    continue  # truncated body에서 일반 파일 누락은 허용
                missing_from_body.append(actual)
        if missing_from_body:
            return _blocked(
                "changed_files_mismatch",
                (
                    "PR 본문의 변경 파일 목록이 실제 PR diff와 다릅니다. "
                    f"실제 변경되었으나 본문에 없는 파일: "
                    f"{', '.join(missing_from_body)}. "
                    "PR 본문 목록을 실제 diff와 일치시키세요."
                ),
                {
                    "missing_from_body": missing_from_body,
                    "actual_changed_files": sorted(changed_set),
                    "pr_body_listed_files": sorted(body_listed_set),
                },
            )

    # acceptance packet의 파일 목록이 있을 때 실제 diff와 비교한다.
    # packet에만 있고 diff에 없는 파일 → changed_files_mismatch (stale 설명).
    # diff에 있지만 packet에 없는 파일(tests/ 제외) → changed_files_mismatch (누락).
    if has_packet_tag and packet_listed_files:
        packet_listed_set = {f.replace("\\", "/") for f in packet_listed_files}
        # packet에만 있고 diff에 없는 파일 탐지
        extra_in_packet: List[str] = []
        for pf in sorted(packet_listed_set):
            if pf not in changed_set:
                extra_in_packet.append(pf)
        if extra_in_packet:
            return _blocked(
                "changed_files_mismatch",
                (
                    "acceptance packet의 변경 파일 목록에 실제 PR diff에 없는 파일이 포함되어 있습니다. "
                    f"실제 diff에 없는 파일: {', '.join(extra_in_packet)}. "
                    "acceptance packet을 실제 diff와 일치시키세요."
                ),
                {
                    "extra_in_packet": extra_in_packet,
                    "actual_changed_files": sorted(changed_set),
                    "packet_listed_files": sorted(packet_listed_set),
                },
            )
        # diff에 있지만 packet에 없는 비-테스트 파일 탐지
        missing_from_packet: List[str] = []
        for actual in sorted(changed_set):
            base = actual.rsplit("/", 1)[-1]
            is_test_file = (
                actual.startswith("tests/")
                or base.startswith("test_")
            )
            if is_test_file:
                continue
            if actual not in packet_listed_set:
                missing_from_packet.append(actual)
        if missing_from_packet:
            return _blocked(
                "changed_files_mismatch",
                (
                    "acceptance packet의 변경 파일 목록이 실제 PR diff와 다릅니다. "
                    f"실제 변경되었으나 packet에 없는 파일: "
                    f"{', '.join(missing_from_packet)}. "
                    "acceptance packet을 실제 diff와 일치시키세요."
                ),
                {
                    "missing_from_packet": missing_from_packet,
                    "actual_changed_files": sorted(changed_set),
                    "packet_listed_files": sorted(packet_listed_set),
                },
            )

    # --- 검사 E: trust-root 변경 설명 의무 ---
    # trust-root 파일은 PR body 또는 acceptance packet에 언급되어야 한다.
    combined_text = pr_body + "\n" + acceptance_packet_body
    for actual in sorted(changed_set):
        base = actual.rsplit("/", 1)[-1]
        is_trust_root = base in CONSISTENCY_TRUST_ROOT_FILES or any(
            pat in actual for pat in CONSISTENCY_TRUST_ROOT_PATTERNS
        )
        if not is_trust_root:
            continue
        # trust-root 파일은 PR body 또는 acceptance packet에 substring으로 언급되어야 한다.
        if base not in combined_text and actual not in combined_text:
            return _blocked(
                "trust_root_change_undocumented",
                (
                    f"trust-root 파일 '{actual}'이 변경되었으나 PR 본문에 "
                    "변경 내용이 명시되어 있지 않습니다. "
                    "PR 본문에 해당 파일 변경 내용을 기술하세요."
                ),
                {
                    "undocumented_file": actual,
                    "actual_changed_files": sorted(changed_set),
                },
            )

    # --- 모든 검사 통과 ---
    return {
        "status": "PASS",
        "failure_code": "",
        "failure_category": "",
        "blocked_reason": None,
        "return_phase": "build",
        "allow_accept": True,
        "details": {},
    }


def _check_acceptance_readiness(
    state: Dict[str, Any],
) -> Dict[str, Any]:
    """gates accept --result ACCEPT 실행 전 PR 품질 및 acceptance packet readiness hard gate.

    반환값:
        {
            "status": "PASS" | "BLOCKED",
            "failure_code": str,      # BLOCKED 시 원인 코드
            "failure_category": str,
            "blocked_reason": Optional[str],
            "missing_sections": List[str],  # pr_body_incomplete 시 누락 섹션
            "return_phase": str,      # BLOCKED 시 재작업 담당 phase
            "allow_accept": bool,
        }

    IMP-20260519-E979 REJECT 재작업:
    - Blocker 1: gh CLI 없음 / gh pr view 실패 / PR 없음 / JSON 파싱 실패는 더 이상
      PASS로 통과시키지 않고 BLOCKED를 반환한다(allow_accept=False).
    - Blocker 2: human acceptance packet readiness는 GitHub PR 댓글을 기본으로 검증한다.
      로컬 파일 검사는 PIPELINE_TEST_ACCEPTANCE_PACKET_PATH 환경변수가 설정된
      테스트 환경에서만 사용하는 fallback이다.
    """
    pass_result: Dict[str, Any] = {
        "status": "PASS",
        "failure_code": "",
        "failure_category": "",
        "blocked_reason": None,
        "missing_sections": [],
        "return_phase": "build",
        "allow_accept": True,
    }

    # --- Blocker 1: gh CLI로 PR 메타데이터 조회 — 실패 시 BLOCKED ---
    try:
        pr_result = subprocess.run(
            ["gh", "pr", "view", "--json", "isDraft,title,body,number,url"],
            capture_output=True, text=True, encoding="utf-8", check=False,
        )
    except (FileNotFoundError, OSError):
        # gh CLI 미설치 — 더 이상 PASS로 통과시키지 않음
        return _acceptance_blocked(
            "gh_cli_not_available",
            (
                "GitHub CLI(gh)가 설치되어 있지 않아 PR 상태를 검증할 수 없습니다. "
                "gh CLI를 설치하고 인증한 뒤 gates accept를 다시 실행하세요."
            ),
        )

    if pr_result.returncode != 0:
        stderr_text = (pr_result.stderr or "").strip()
        # PR 없음(404 / no pull requests found) → pr_not_found
        lowered = stderr_text.lower()
        if "no pull requests found" in lowered or "404" in lowered or "not found" in lowered:
            return _acceptance_blocked(
                "pr_not_found",
                (
                    "현재 브랜치에 연결된 GitHub PR을 찾을 수 없습니다. "
                    "PR을 생성한 뒤 gates accept를 다시 실행하세요. "
                    + stderr_text[:200]
                ),
            )
        # 그 외 gh pr view 실패 → pr_view_failed
        return _acceptance_blocked(
            "pr_view_failed",
            (
                "gh pr view 명령이 실패했습니다"
                f"(exit code {pr_result.returncode}). "
                + stderr_text[:200]
                + " gh 인증과 네트워크 상태를 확인한 뒤 다시 실행하세요."
            ),
        )

    raw_stdout = (pr_result.stdout or "").strip()
    if not raw_stdout:
        # 출력이 비어 있음 → PR을 찾지 못한 것으로 간주
        return _acceptance_blocked(
            "pr_not_found",
            (
                "gh pr view 출력이 비어 있어 PR을 확인할 수 없습니다. "
                "현재 브랜치에 연결된 PR이 있는지 확인한 뒤 다시 실행하세요."
            ),
        )

    try:
        pr_data = json.loads(raw_stdout)
    except (json.JSONDecodeError, ValueError):
        # JSON 파싱 실패 → pr_metadata_parse_error
        return _acceptance_blocked(
            "pr_metadata_parse_error",
            (
                "gh pr view 가 반환한 PR 메타데이터(JSON)를 해석하지 못했습니다. "
                "잠시 후 gates accept를 다시 실행하세요."
            ),
        )

    if not isinstance(pr_data, dict):
        return _acceptance_blocked(
            "pr_metadata_parse_error",
            (
                "gh pr view 가 반환한 PR 메타데이터 형식이 올바르지 않습니다. "
                "잠시 후 gates accept를 다시 실행하세요."
            ),
        )

    # --- 1. Draft PR 차단 ---
    if pr_data.get("isDraft", False):
        return _acceptance_blocked(
            "pr_is_draft",
            (
                "PR이 Draft 상태입니다. "
                "Draft를 해제한 뒤 gates accept를 다시 실행하세요."
            ),
        )

    pr_body: str = (pr_data.get("body") or "").lstrip("﻿")
    pr_url: str = pr_data.get("url") or ""

    # --- 2. 필수 섹션 검사 (섹션 누락이 임시 문구보다 우선 탐지) ---
    missing_sections: List[str] = []
    for section_spec in PR_REQUIRED_SECTIONS:
        if isinstance(section_spec, tuple):
            # OR 그룹: 안의 항목 중 하나라도 있으면 통과
            found = any(s in pr_body for s in section_spec)
            if not found:
                missing_sections.append(" 또는 ".join(section_spec))
        else:
            if section_spec not in pr_body:
                missing_sections.append(section_spec)

    if missing_sections:
        return _acceptance_blocked(
            "pr_body_incomplete",
            "PR 본문에 임시 문구가 포함되어 있거나 필수 섹션이 누락되어 있습니다.",
            missing_sections=missing_sections,
        )

    # --- 3. 임시 문구 탐지 (섹션이 갖춰진 경우에만 검사) ---
    # 줄 단위 접두 매칭 — 정상 본문의 우연한 단어 언급은 차단하지 않는다.
    temporary_pattern = _find_temporary_pr_body_pattern(pr_body)
    if temporary_pattern is not None:
        return _acceptance_blocked(
            "pr_body_temporary",
            (
                f"PR 본문에 임시 문구가 포함되어 있습니다: '{temporary_pattern}'. "
                "PR 본문을 완성한 뒤 다시 실행하세요."
            ),
        )

    # --- 4. Blocker 2: human acceptance packet readiness 확인 ---
    # 기본: GitHub PR 댓글 검증. fallback: 로컬 파일(테스트 환경 전용).
    env_override = os.environ.get("PIPELINE_TEST_ACCEPTANCE_PACKET_PATH", "").strip()
    if env_override:
        # 테스트 환경 fallback — 로컬 파일로 검증
        packet_result = _check_acceptance_packet_via_local_file(state)
    else:
        # 운영 기본 경로 — GitHub PR 댓글로 검증
        packet_result = _check_acceptance_packet_via_github(pr_url)
    if not packet_result.get("allow_accept", True):
        return packet_result

    return pass_result


_FORBIDDEN_ACCEPTANCE_EVIDENCE_PREFIXES = (
    ".pipeline/phase_evidence/",
    ".pipeline/agent_receipts/",
    "pipeline_contracts/",
)
_FORBIDDEN_ACCEPTANCE_EVIDENCE_EXACT = {
    "pipeline_state.json",
}


def _is_internal_pipeline_path(item: str) -> bool:
    """acceptance evidence로 사용할 수 없는 내부 파이프라인 경로인지 검사합니다."""
    normalized = item.replace("\\", "/").strip()
    for prefix in _FORBIDDEN_ACCEPTANCE_EVIDENCE_PREFIXES:
        if normalized.startswith(prefix) or normalized == prefix.rstrip("/"):
            return True
    if normalized in _FORBIDDEN_ACCEPTANCE_EVIDENCE_EXACT:
        return True
    return False


def _validate_user_acceptance_evidence(raw: Any) -> Dict[str, Any]:
    items = _split_evidence_items(raw)
    if not items:
        _die(
            "[USER ACCEPTANCE BLOCKED] ACCEPT에는 실제 확인 증거가 필요합니다. "
            "실제 결과 파일/폴더 경로, 스크린샷 경로, PR 링크, GitHub Actions 첨부파일 링크 중 하나를 넣으세요."
        )

    placeholders = [item for item in items if item.upper() in PLACEHOLDER_EVIDENCE_VALUES]
    if placeholders:
        _die(
            "[USER ACCEPTANCE BLOCKED] ACCEPT에는 placeholder 증거를 쓸 수 없습니다: "
            + ", ".join(placeholders)
            + ". 실제 결과 경로나 검토 링크를 넣으세요."
        )

    # MT-2: 내부 파이프라인 경로 거부
    internal = [item for item in items if not _is_evidence_url(item) and _is_internal_pipeline_path(item)]
    if internal:
        _die(
            "[PIPELINE ERROR] acceptance evidence는 pipeline_outputs/ 또는 GitHub URL이어야 합니다. "
            "내부 phase evidence 경로는 허용되지 않습니다: "
            + ", ".join(internal)
        )

    files: List[Dict[str, Any]] = []
    urls: List[str] = []
    missing: List[str] = []
    for item in items:
        if _is_evidence_url(item):
            urls.append(item)
            continue
        path = _resolve_artifact_path(item)
        if path is None:
            missing.append(item)
            continue
        if path.is_file():
            files.append(_path_sha_payload(path))
        else:
            files.append({
                "path": _display_path(path),
                "kind": "directory",
                "exists": True,
            })

    if missing:
        _die(
            "[USER ACCEPTANCE BLOCKED] 증거 경로/링크를 확인할 수 없습니다: "
            + ", ".join(missing)
        )
    if not files and not urls:
        _die(
            "[USER ACCEPTANCE BLOCKED] ACCEPT에는 실제 결과 파일/폴더 경로 또는 검토 링크가 최소 1개 필요합니다."
        )
    return {"files": files, "urls": urls, "raw_items": items}


def _resolve_artifact_path(raw: str) -> Optional[Path]:
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", raw):
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = BASE_DIR / path
    try:
        resolved = path.resolve()
    except OSError:
        return None
    if not resolved.exists():
        return None
    return resolved


def _ensure_output_registry(state: Dict[str, Any]) -> Dict[str, Any]:
    outputs = state.get("outputs")
    if not isinstance(outputs, dict):
        outputs = {"items": []}
    if not isinstance(outputs.get("items"), list):
        outputs["items"] = []
    state["outputs"] = outputs
    return outputs


def _pipeline_output_dir(pid: str) -> Path:
    return OUTPUTS_ROOT / pid


def _copy_to_pipeline_outputs(pid: str, source: Path, label: str = "") -> Path:
    out_dir = _pipeline_output_dir(pid)
    safe_label = re.sub(r"[^A-Za-z0-9_.-]+", "-", label.strip()).strip("-")
    dest_name = source.name if not safe_label else f"{safe_label}-{source.name}"
    dest = out_dir / dest_name
    dest.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() != dest.resolve():
        shutil.copy2(source, dest)
    return dest


def _register_output_item(
    state: Dict[str, Any],
    *,
    kind: str,
    path: str,
    label: str,
    copy_to_outputs: bool = True,
    notes: str = "",
) -> Dict[str, Any]:
    pid = str(state.get("pipeline_id") or "")
    if not pid:
        _die("[OUTPUT REGISTRY] active pipeline_id is required")
    resolved = _resolve_artifact_path(path)
    if not resolved or not resolved.is_file():
        _die(f"[OUTPUT REGISTRY] output file not found: {path}")
    public_path = resolved
    if copy_to_outputs:
        public_path = _copy_to_pipeline_outputs(pid, resolved, label)
    item = {
        "kind": kind,
        "label": label or kind,
        "source_path": _display_path(resolved),
        "public_path": _display_path(public_path),
        "sha256": _sha256_file(public_path),
        "size_bytes": public_path.stat().st_size,
        "notes": notes,
        "registered_at": _now(),
    }
    outputs = _ensure_output_registry(state)
    existing = [
        old for old in outputs["items"]
        if not (isinstance(old, dict) and old.get("public_path") == item["public_path"])
    ]
    existing.append(item)
    outputs["items"] = existing
    manifest_path = _contract_paths(pid)["outputs_manifest"]
    manifest = {
        "schema_version": 1,
        "pipeline_id": pid,
        "generated_at": _now(),
        "items": outputs["items"],
    }
    _write_json(manifest_path, manifest)
    return item


def _deployment_artifacts(
    state: Dict[str, Any],
    evidence: Optional[str],
) -> Tuple[List[Path], List[str], List[str]]:
    """배포 대상 산출물 경로 목록과 차단된 산출물 이름 목록을 반환한다.

    IMP-20260528-3898 MT-3: _is_internal_artifact() SSoT를 사용하여 내부 산출물을
    배포 대상에서 자동 제외한다.
    IMP-20260529-D8BA MT-1: SECRET_PATTERNS / SECRET_FILENAME_PATTERNS SSoT를
    사용하여 민감 정보가 포함된/민감 정보 파일명을 배포 대상에서 자동 제외한다.

    차단된 파일 이름 목록은 deployment_manifest.json의 blocked_internal_artifacts /
    blocked_secret_artifacts 필드에 각각 기록된다.

    Returns:
        (allowed_paths, blocked_internal, blocked_secret) 3-tuple.
        allowed_paths: 배포할 파일 Path 목록 (내부/민감 산출물 제외)
        blocked_internal: 차단된 내부 산출물 파일 이름 목록
        blocked_secret: 차단된 민감 정보 산출물 파일 이름 목록
    """
    candidates: List[str] = []
    candidates.extend(_split_evidence_paths(evidence))
    outputs = _ensure_output_registry(state)
    for item in outputs.get("items", []):
        if isinstance(item, dict):
            candidates.extend(_split_evidence_paths(item.get("public_path")))
    phases = state.get("phases", {})
    if isinstance(phases, dict):
        for phase_name in ("build",):
            phase = phases.get(phase_name, {})
            if isinstance(phase, dict):
                candidates.extend(_split_evidence_paths(phase.get("evidence")))

    result: List[Path] = []
    blocked: List[str] = []
    blocked_secret: List[str] = []
    seen: set = set()
    for raw in candidates:
        # 내부 산출물은 배포에서 제외 (SSoT: WORKSPACE_INTERNAL_PATTERNS)
        normalized = raw.replace("\\", "/").strip() if raw else ""
        basename = normalized.split("/")[-1] if "/" in normalized else normalized
        if _is_internal_artifact(raw):
            if basename and basename not in blocked:
                blocked.append(basename)
            continue
        # IMP-20260529-D8BA MT-1: secret 파일명 패턴 검사 (.env/*.key/*.pem 등)
        if _is_secret_filename(raw):
            if basename and basename not in blocked_secret:
                blocked_secret.append(basename)
            continue
        # IMP-20260529-D8BA MT-1: 파일 내용에서 secret 검사
        candidate_path = _resolve_artifact_path(raw)
        if candidate_path and candidate_path.is_file():
            try:
                content = candidate_path.read_text(encoding="utf-8", errors="replace")
                if _scan_text_for_secrets(content):
                    if basename and basename not in blocked_secret:
                        blocked_secret.append(basename)
                    continue
            except Exception:
                pass  # 읽기 실패는 secret 검사 통과로 간주(다른 경로에서 차단됨)
        if not candidate_path:
            continue
        key = str(candidate_path).lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(candidate_path)
    return result, blocked, blocked_secret


def _copy_deployment_artifact(src: Path, deploy_dir: Path) -> Dict[str, Any]:
    try:
        rel = src.resolve().relative_to(BASE_DIR)
    except ValueError:
        rel = Path(src.name)
    dest = deploy_dir / rel
    if src.is_dir():
        shutil.copytree(src, dest, dirs_exist_ok=True)
        kind = "directory"
        sha256 = None
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        kind = "file"
        sha256 = _sha256_file(dest)
    return {
        "kind": kind,
        "source": _display_path(src),
        "destination": str(dest),
        "sha256": sha256,
    }


def _deploy_accepted_outputs(
    state: Dict[str, Any],
    evidence: Optional[str],
    notes: Optional[str],
    evidence_validation: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    pid = str(state.get("pipeline_id") or "UNKNOWN")
    deploy_root = _deployment_root()
    deploy_dir = deploy_root / pid
    # IMP-20260528-3898 MT-3 / IMP-20260529-D8BA MT-1: 내부/민감 산출물 필터 — 3-tuple
    artifacts, blocked_internal, blocked_secret = _deployment_artifacts(state, evidence)
    external_urls = list((evidence_validation or {}).get("urls") or [])
    if not artifacts and not external_urls:
        _die(
            "[ACCEPTANCE DEPLOY BLOCKED] 배포할 결과물을 찾지 못했습니다. "
            "--evidence에 실제 파일/폴더 경로 또는 GitHub 첨부파일/PR 링크를 넣으세요."
        )
    deploy_dir.mkdir(parents=True, exist_ok=True)
    copied = [_copy_deployment_artifact(path, deploy_dir) for path in artifacts]
    manifest = {
        "schema_version": 1,
        "pipeline_id": pid,
        "deployed_at": _now(),
        "deploy_root": str(deploy_root),
        "deploy_dir": str(deploy_dir),
        "evidence": evidence,
        "validated_evidence": evidence_validation or {},
        "notes": notes or "",
        "artifacts": copied,
        "external_urls": external_urls,
        # IMP-20260528-3898 MT-3: 차단된 내부 산출물 목록 기록
        "blocked_internal_artifacts": blocked_internal,
        # IMP-20260529-D8BA MT-1: 차단된 민감 정보 산출물 목록 기록
        "blocked_secret_artifacts": blocked_secret,
    }
    manifest_path = deploy_dir / "deployment_manifest.json"
    _write_json(manifest_path, manifest)
    manifest["manifest_path"] = str(manifest_path)
    return manifest


def _enable_phase_attestations(state: Dict[str, Any]) -> None:
    state["phase_attestations"] = _ensure_phase_attestations(state)
    state["phase_attestations"]["enabled"] = True


def _expected_agent_id(phase: str) -> str:
    if phase not in PHASE_AGENT_IDS:
        _die(f"agent receipts are not supported for phase: {phase}", exit_code=2)
    return PHASE_AGENT_IDS[phase]


def _agent_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _new_agent_run_id(phase: str) -> str:
    return f"{phase}-{secrets.token_hex(8)}"


def _receipt_path_for_run(pid: str, phase: str, run_id: str) -> Path:
    safe_run = re.sub(r"[^A-Za-z0-9_.-]+", "_", run_id)
    return AGENT_RECEIPT_DIR / pid / phase / f"{safe_run}.json"


def _path_sha_payload(path: Path) -> Dict[str, Any]:
    return {
        "path": _display_path(path),
        "sha256": _sha256_file(path),
        "size_bytes": path.stat().st_size,
    }


def _agent_run_start(state: Dict[str, Any], phase: str, agent_id: Optional[str]) -> Tuple[Dict[str, Any], str]:
    if phase not in AGENT_RUN_PHASES:
        _die(f"agent start supports phases: {', '.join(AGENT_RUN_PHASES)}", exit_code=2)
    expected = _expected_agent_id(phase)
    actual_agent = agent_id or expected
    if actual_agent != expected:
        _die(f"[AGENT RECEIPT GATE] {phase} must be executed by {expected}, got {actual_agent!r}")

    # PM Planner 재시도 제한: 동일 파이프라인에서 pm_planner phase가 PM_PLANNER_MAX_RETRIES회
    # 이상 시작되면 failure_packet을 기록하고 종료.
    if phase == "pm_planner":
        existing_runs = state.get("agent_runs", {})
        planner_run_count = sum(
            1 for r in existing_runs.values()
            if isinstance(r, dict) and r.get("phase") == "pm_planner"
        )
        if planner_run_count >= PM_PLANNER_MAX_RETRIES:
            pid = str(state.get("pipeline_id") or "UNKNOWN")
            failure_data = {
                "schema_version": 1,
                "pipeline_id": pid,
                "gate": "PM_PLANNER_RETRY_LIMIT",
                "recorded_at": _now(),
                "retry_count": planner_run_count,
                "max_retries": PM_PLANNER_MAX_RETRIES,
                "message": (
                    f"[PM PLANNER RETRY LIMIT] pm-planner-agent가 {planner_run_count}회 이상 실행되었습니다. "
                    f"최대 허용 횟수({PM_PLANNER_MAX_RETRIES})를 초과했습니다. "
                    "근본 원인을 분석하고 새 파이프라인을 시작하거나 관리자에게 문의하세요."
                ),
            }
            failure_path = BASE_DIR / "failure_packet.json"
            try:
                failure_path.write_text(json.dumps(failure_data, ensure_ascii=False, indent=2), encoding="utf-8")
            except OSError:
                pass
            _die(str(failure_data["message"]))

    gate_phase = "pm" if phase in {"pm_planner", "pipeline_manager"} else phase
    ok, reason = check_gate(state, gate_phase)
    if not ok:
        _die(f"[GATE BLOCKED] {reason}")
    pid = str(state.get("pipeline_id") or "")
    run_id = _new_agent_run_id(phase)
    token = f"tok_{secrets.token_urlsafe(32)}"
    run = {
        "schema_version": 1,
        "run_id": run_id,
        "pipeline_id": pid,
        "phase": phase,
        "agent_id": actual_agent,
        "status": "RUNNING",
        "started_at": _now(),
        "completed_at": None,
        "token_hash": _agent_token_hash(token),
        "output_file": None,
        "output_sha256": None,
        "evidence_files": [],
        "receipt_path": None,
        "receipt_sha256": None,
        "used_by_phase": None,
        "used_at": None,
        "commit_sha": _git_rev_parse("HEAD"),
    }
    state.setdefault("agent_runs", {})[run_id] = run
    _log_event(state, f"agent run started phase={phase} agent={actual_agent} run_id={run_id}")
    return run, token


def _agent_run_finish(
    state: Dict[str, Any],
    *,
    run_id: str,
    token: str,
    output_file: str,
    evidence: Optional[str],
    notes: Optional[str],
) -> Dict[str, Any]:
    runs = state.setdefault("agent_runs", {})
    run = runs.get(run_id)
    if not isinstance(run, dict):
        _die(f"[AGENT RECEIPT GATE] unknown run_id: {run_id}")
    if run.get("status") != "RUNNING":
        _die(f"[AGENT RECEIPT GATE] run {run_id} is not RUNNING")
    if not token or _agent_token_hash(token) != run.get("token_hash"):
        _die("[AGENT RECEIPT GATE] invalid agent run token")

    output_path = Path(output_file)
    if not output_path.is_absolute():
        output_path = BASE_DIR / output_path
    if not output_path.exists() or not output_path.is_file():
        _die(f"[AGENT RECEIPT GATE] output file not found: {output_file}")

    evidence_files: List[Dict[str, Any]] = []
    for raw in _split_evidence_paths(evidence):
        path = _resolve_artifact_path(raw)
        if path and path.is_file():
            evidence_files.append(_path_sha_payload(path))

    run["status"] = "COMPLETED"
    run["completed_at"] = _now()
    run["output_file"] = _display_path(output_path)
    run["output_sha256"] = _sha256_file(output_path)
    run["evidence_files"] = evidence_files
    run["notes"] = notes or ""
    receipt = {
        "schema_version": 1,
        "receipt_type": "agent-run-receipt-v1",
        "pipeline_id": run.get("pipeline_id"),
        "phase": run.get("phase"),
        "agent_id": run.get("agent_id"),
        "run_id": run_id,
        "status": run.get("status"),
        "started_at": run.get("started_at"),
        "completed_at": run.get("completed_at"),
        "output_file": run.get("output_file"),
        "output_sha256": run.get("output_sha256"),
        "evidence_files": evidence_files,
        "commit_sha": run.get("commit_sha"),
    }
    receipt_path = _receipt_path_for_run(str(run.get("pipeline_id")), str(run.get("phase")), run_id)
    _write_json(receipt_path, receipt)
    run["receipt_path"] = _display_path(receipt_path)
    run["receipt_sha256"] = _sha256_file(receipt_path)
    _log_event(state, f"agent run completed phase={run.get('phase')} agent={run.get('agent_id')} run_id={run_id}")
    return run


def _validate_agent_run_receipt(
    state: Dict[str, Any],
    run_phase: str,
    run_id: Optional[str],
    report_file: Optional[str],
    *,
    consume_phase: str,
) -> Optional[Dict[str, Any]]:
    if not _phase_attestations_enabled(state):
        return None
    if not run_id:
        _die(
            f"[AGENT RECEIPT GATE] {consume_phase} requires a completed {run_phase} receipt. "
            f"Start with `python pipeline.py agent start --phase {run_phase}`, pass the token to {_expected_agent_id(run_phase)}, "
            "then finish the run before recording the phase."
        )
    run = state.setdefault("agent_runs", {}).get(run_id)
    if not isinstance(run, dict):
        _die(f"[AGENT RECEIPT GATE] unknown run_id: {run_id}")
    if run.get("pipeline_id") != state.get("pipeline_id"):
        _die("[AGENT RECEIPT GATE] run pipeline_id mismatch")
    # Legacy PM compat: "pm_planner" run_phase accepts run.phase="pm" (older state schema)
    _legacy_pm_phases = {"pm"} if run_phase == "pm_planner" else set()
    if run.get("phase") != run_phase and run.get("phase") not in _legacy_pm_phases:
        _die(f"[AGENT RECEIPT GATE] run phase mismatch: expected {run_phase}, got {run.get('phase')}")
    # Resolve expected agent: for legacy pm receipt (phase="pm") use pm-agent, else normal lookup
    actual_run_phase = run.get("phase", run_phase)
    expected_agent = _expected_agent_id(actual_run_phase)
    if run.get("agent_id") != expected_agent:
        _die(f"[AGENT RECEIPT GATE] {run_phase} requires {expected_agent}, got {run.get('agent_id')}")
    if run.get("status") != "COMPLETED":
        _die(f"[AGENT RECEIPT GATE] run {run_id} must be COMPLETED")
    used = run.get("used_by_phase")
    if used and used != consume_phase:
        _die(f"[AGENT RECEIPT GATE] run {run_id} was already used by {used}")
    if not run.get("receipt_path") or not run.get("receipt_sha256"):
        _die(f"[AGENT RECEIPT GATE] run {run_id} is missing receipt file")
    receipt_path = _resolve_artifact_path(str(run.get("receipt_path")))
    if not receipt_path or not receipt_path.is_file():
        _die(f"[AGENT RECEIPT GATE] receipt file missing for run {run_id}")
    if _sha256_file(receipt_path) != run.get("receipt_sha256"):
        _die(f"[AGENT RECEIPT GATE] receipt hash mismatch for run {run_id}")
    if report_file:
        report_path = Path(report_file)
        if not report_path.is_absolute():
            report_path = BASE_DIR / report_path
        output_path = _resolve_artifact_path(str(run.get("output_file") or ""))
        if not output_path or report_path.resolve() != output_path.resolve():
            _die("[AGENT RECEIPT GATE] --report-file must match the completed agent run output_file")
        if _sha256_file(report_path.resolve()) != run.get("output_sha256"):
            _die("[AGENT RECEIPT GATE] report hash mismatch against agent run output")
    run["used_by_phase"] = consume_phase
    run["used_at"] = _now()
    return run


def _validate_agent_run_for_phase(
    state: Dict[str, Any],
    phase: str,
    run_id: Optional[str],
    report_file: Optional[str],
) -> Optional[Dict[str, Any]]:
    run_phase = PHASE_RECEIPT_RUN_PHASES.get(phase, phase)
    return _validate_agent_run_receipt(
        state,
        run_phase,
        run_id,
        report_file,
        consume_phase=phase,
    )


def _safe_phase_artifact_name(path: Path) -> str:
    name = path.name or "evidence"
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name)


def _git_check_ignored(path: Path) -> bool:
    try:
        rel = path.resolve().relative_to(BASE_DIR)
    except ValueError:
        return True
    try:
        proc = subprocess.run(
            ["git", "check-ignore", "-v", str(rel)],
            cwd=str(BASE_DIR),
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode != 0:
            return False
        last_match = ""
        for line in proc.stdout.splitlines():
            if line.strip():
                last_match = line
        if not last_match:
            return False
        pattern_part = last_match.split("\t", 1)[0]
        parts = pattern_part.split(":", 2)
        pattern = parts[2] if len(parts) == 3 else pattern_part
        return not pattern.startswith("!")
    except OSError:
        return False


def _workspace_check_for_file(path: Path) -> Dict[str, Any]:
    return {
        "path": _normalize_rel_path(str(path)),
        "sha256": _sha256_file(path),
        "size_bytes": path.stat().st_size,
    }


def _copy_phase_evidence_file(pid: str, phase: str, label: str, raw_path: Optional[str]) -> Optional[Dict[str, Any]]:
    if not raw_path:
        return None
    path = Path(str(raw_path))
    if not path.is_absolute():
        path = BASE_DIR / path
    if not path.exists() or not path.is_file():
        return None
    dest_dir = PHASE_ATTESTATION_EVIDENCE_DIR / pid / phase
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_name = f"{label}_{_safe_phase_artifact_name(path)}"
    dest = dest_dir / dest_name
    shutil.copy2(path, dest)
    ignored_by_git = _git_check_ignored(dest)
    return {
        "label": label,
        "source": _display_path(path),
        "path": _normalize_rel_path(str(dest)),
        "sha256": _sha256_file(dest),
        "size_bytes": dest.stat().st_size,
        "requires_force_add": ignored_by_git,
    }


def _phase_status_is_closed(phase: str, status: str) -> bool:
    if phase in ("pm", "dev", "build"):
        return status == "DONE"
    if phase == "qa":
        return status == "PASS"
    return False


def _prepare_phase_attestation_request(state: Dict[str, Any], phase: str) -> Dict[str, Any]:
    if phase not in PHASE_ATTESTATION_PHASES:
        _die(f"phase attestation only supports: {', '.join(PHASE_ATTESTATION_PHASES)}", exit_code=2)
    if not _phase_attestations_enabled(state):
        _die(
            "phase attestations must be enabled for every pipeline. Use "
            "`python pipeline.py contract init` or `python pipeline.py gates init` "
            "to migrate this state."
        )
    pid = str(state.get("pipeline_id") or "")
    phase_info = state.get("phases", {}).get(phase, {})
    if not isinstance(phase_info, dict):
        _die(f"missing phase state for {phase}")
    phase_status = str(phase_info.get("status") or "PENDING")
    if not _phase_status_is_closed(phase, phase_status):
        _die(f"{phase} phase must be completed before preparing phase CI attestation; current status={phase_status}")

    copied: List[Dict[str, Any]] = []
    workspace_checks: List[Dict[str, Any]] = []
    local_only: List[Dict[str, Any]] = []
    agent_run: Optional[Dict[str, Any]] = None

    run_id = phase_info.get("agent_run_id")
    if not run_id:
        _die(f"[AGENT RECEIPT GATE] {phase} phase cannot prepare CI attestation without agent_run_id")
    run = state.setdefault("agent_runs", {}).get(str(run_id))
    if not isinstance(run, dict):
        _die(f"[AGENT RECEIPT GATE] agent run not found for {phase}: {run_id}")
    _validate_agent_run_for_phase(state, phase, str(run_id), phase_info.get("report_file"))
    receipt_path = _resolve_artifact_path(str(run.get("receipt_path") or ""))
    if not receipt_path:
        _die(f"[AGENT RECEIPT GATE] receipt file missing for {phase}: {run_id}")
    agent_run = {
        "run_id": run.get("run_id"),
        "phase": run.get("phase"),
        "agent_id": run.get("agent_id"),
        "status": run.get("status"),
        "output_file": run.get("output_file"),
        "output_sha256": run.get("output_sha256"),
        "receipt_path": run.get("receipt_path"),
        "receipt_sha256": run.get("receipt_sha256"),
        "used_by_phase": run.get("used_by_phase"),
    }
    receipt_copy = _copy_phase_evidence_file(pid, phase, "agent_receipt", str(receipt_path))
    if not receipt_copy:
        _die(f"[AGENT RECEIPT GATE] could not copy receipt evidence for {phase}: {run_id}")
    agent_run["receipt_source_path"] = agent_run["receipt_path"]
    agent_run["receipt_path"] = receipt_copy["path"]
    agent_run["receipt_sha256"] = receipt_copy["sha256"]
    copied.append(receipt_copy)

    manager_run: Optional[Dict[str, Any]] = None
    if phase == "pm":
        manager_run_id = phase_info.get("manager_run_id")
        # Legacy PM compat: if no manager_run_id, skip manager CI check (old single-receipt flow)
        if manager_run_id:
            manager = state.setdefault("agent_runs", {}).get(str(manager_run_id))
            if not isinstance(manager, dict):
                _die(f"[PM MANAGER GATE] manager run not found for pm: {manager_run_id}")
            _validate_agent_run_receipt(
                state,
                "pipeline_manager",
                str(manager_run_id),
                phase_info.get("manager_report_file"),
                consume_phase="pm_manager",
            )
            manager_receipt_path = _resolve_artifact_path(str(manager.get("receipt_path") or ""))
            if not manager_receipt_path:
                _die(f"[PM MANAGER GATE] manager receipt file missing for pm: {manager_run_id}")
            manager_run = {
                "run_id": manager.get("run_id"),
                "phase": manager.get("phase"),
                "agent_id": manager.get("agent_id"),
                "status": manager.get("status"),
                "output_file": manager.get("output_file"),
                "output_sha256": manager.get("output_sha256"),
                "receipt_path": manager.get("receipt_path"),
                "receipt_sha256": manager.get("receipt_sha256"),
                "used_by_phase": manager.get("used_by_phase"),
            }
            manager_receipt_copy = _copy_phase_evidence_file(pid, phase, "manager_receipt", str(manager_receipt_path))
            if not manager_receipt_copy:
                _die(f"[PM MANAGER GATE] could not copy manager receipt evidence for pm: {manager_run_id}")
            manager_run["receipt_source_path"] = manager_run["receipt_path"]
            manager_run["receipt_path"] = manager_receipt_copy["path"]
            manager_run["receipt_sha256"] = manager_receipt_copy["sha256"]
            copied.append(manager_receipt_copy)

    report = _copy_phase_evidence_file(pid, phase, "report", phase_info.get("report_file"))
    if report:
        copied.append(report)
    if phase == "pm":
        manager_report = _copy_phase_evidence_file(pid, phase, "manager_report", phase_info.get("manager_report_file"))
        if manager_report:
            copied.append(manager_report)
    if phase == "dev":
        atomic_scope = state.get("atomic_scope", {})
        if isinstance(atomic_scope, dict):
            scope = _copy_phase_evidence_file(pid, phase, "scope_manifest", atomic_scope.get("manifest_file"))
            if scope:
                copied.append(scope)

    for raw in _split_evidence_paths(phase_info.get("evidence")):
        path = _resolve_artifact_path(raw)
        if not path or not path.is_file():
            continue
        if _git_check_ignored(path):
            local_only.append({
                "path": _display_path(path),
                "sha256": _sha256_file(path),
                "reason": "ignored_or_outside_git_checkout",
            })
            continue
        workspace_checks.append(_workspace_check_for_file(path))

    request = {
        "schema_version": 1,
        "request_type": "pipeline-phase-attestation-request-v1",
        "generated_at": _now(),
        "pipeline_id": pid,
        "phase": phase,
        "phase_status": phase_status,
        "commit_hint": _git_rev_parse("HEAD"),
        "state": {
            "current_phase": state.get("current_phase"),
            "phase_completed_at": phase_info.get("completed_at"),
            "report_file": phase_info.get("report_file"),
            "evidence": phase_info.get("evidence"),
        },
        "agent_run": agent_run,
        "manager_run": manager_run,
        "copied_evidence": copied,
        "workspace_file_checks": workspace_checks,
        "local_only_files": local_only,
    }
    _write_json(PHASE_ATTESTATION_REQUEST, request)
    return request


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
        "harness": "Phase 7 - External Gates (Acceptance)",
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

    phase_attestation_blocker = _phase_attestation_blocker_for_phase(state, phase)
    if phase_attestation_blocker:
        return False, phase_attestation_blocker

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
        print(DIM("  대시보드 자동 시작 보류 — 수동으로 'VS Code 태스크: 에이전트 대시보드 시작' 실행 가능"))


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
    print(BOLD(GREEN("  파이프라인 생성 완료")))
    print(f"  ID:   {CYAN(pipeline_id)}")
    print(f"  유형: {pipeline_type}")
    print(f"  설명: {args.desc}")
    print()
    print(BOLD(YELLOW("  세션 언어 규칙")))
    print("  사용자에게 보이는 진행 설명, 도구 설명, PR 안내, 승인/거절 질문은 모두 쉬운 한국어로 작성하세요.")
    print("  예: 'Check latest status' 대신 '최신 상태 확인'. 코드 식별자와 명령어는 그대로 두되 한국어 설명을 붙이세요.")
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

    print(f"  다음 단계: {YELLOW('python pipeline.py agent start --phase pm_planner')}")
    print(f"  PM 인수 기록: {YELLOW('python pipeline.py agent start --phase pipeline_manager')}")
    print(f"  PM 완료 기록: {YELLOW('python pipeline.py done --phase pm --report-file step_plan.xml --decomp --clarification --roadmap --planner-run-id <planner_run_id> --manager-run-id <manager_run_id> --manager-report manager_handoff.xml')}")
    print()


# ── Codex Review Schema v2 Validator (MT-1: IMP-20260516-A627) ───────────────

CODEX_VALID_STAGES = {"plan", "scope", "code", "hygiene", "pr", "rca"}
CODEX_VALID_RESULTS = {"ACCEPT", "REJECT", "PENDING"}
# IMP-20260517-30DD MT-1: 상수 분리
# - CODEX_REQUIRED_REVIEW_MODEL: schema review_model 필드 표시용 (대문자, 사람이 읽는 값)
# - CODEX_REQUIRED_MODEL_ID: API payload model 필드 + actual_model_id 비교용 (소문자 exact string)
CODEX_REQUIRED_REVIEW_MODEL: str = "GPT-5.5"
CODEX_REQUIRED_MODEL_ID: str = "gpt-5.5"
# 하위호환: 기존 CODEX_REQUIRED_MODEL 참조는 CODEX_REQUIRED_REVIEW_MODEL로 유지
CODEX_REQUIRED_MODEL: str = CODEX_REQUIRED_REVIEW_MODEL

# IMP-20260517-30DD MT-1: failure code 12개
CODEX_FAILURE_CODES = {
    "SETUP_REQUIRED": "OPENAI_API_KEY 환경변수가 설정되지 않았습니다.",
    "AUTH_REQUIRED": "API 키가 유효하지 않거나 만료되었습니다. (401 Unauthorized)",
    "BILLING_REQUIRED": "청구 문제로 API 호출이 거부되었습니다. (402 Payment Required)",
    "MODEL_UNAVAILABLE": "요청한 모델(gpt-5.5)을 사용할 수 없습니다. (404 Model Not Found)",
    "MODEL_METADATA_UNAVAILABLE": "Provider가 actual model metadata를 노출하지 않습니다. actual_model_id 검증 불가.",
    "PROVIDER_CAPABILITY_MISSING": "Provider가 필요한 기능(structured output 등)을 지원하지 않습니다.",
    "RATE_LIMITED": "요청 한도를 초과했습니다. (429 Rate Limited) 잠시 후 재시도하세요.",
    "PROVIDER_FAIL": "Provider 서버 오류가 발생했습니다. (5xx Server Error)",
    "PROVIDER_OUTPUT_INVALID": "Provider 응답이 유효한 JSON이 아니거나 스키마를 위반합니다.",
    "STALE_REVIEW": "diff_sha256 또는 head_sha가 현재 코드베이스와 다릅니다. 재실행이 필요합니다.",
    "REVIEW_REJECTED": "Codex review 결과가 REJECT입니다. failure_packet을 확인하세요.",
    "MANUAL_SETUP_REQUIRED": "동일 provider/stage/failure_code가 2회 반복되었습니다. 수동 설정이 필요합니다.",
}

# IMP-20260517-30DD MT-1: attempt budget 상수
CODEX_ATTEMPT_BUDGET_TOTAL: int = 6
CODEX_ATTEMPT_BUDGET_PER_STAGE: int = 2

# IMP-20260517-30DD MT-1: secret redaction 패턴
import re as _re
_SECRET_PATTERNS = [
    _re.compile(r"sk-[A-Za-z0-9\-_]{20,}", _re.IGNORECASE),
    _re.compile(r"Bearer\s+[A-Za-z0-9\-_\.]{16,}", _re.IGNORECASE),
    _re.compile(r"\"Authorization\"\s*:\s*\"[^\"]{8,}\"", _re.IGNORECASE),
    _re.compile(r"access_token[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9\-_\.]{16,}", _re.IGNORECASE),
    _re.compile(r"refresh_token[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9\-_\.]{16,}", _re.IGNORECASE),
]


def _redact_secrets(text: str) -> str:
    """IMP-20260517-30DD MT-1: 민감 정보(API key, Bearer token 등)를 REDACTED로 치환."""
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text
CODEX_REQUIRED_FIELDS = [
    "schema_version",
    "pipeline_id",
    "stage",
    "result",
    "reviewer",
    "review_model",
    "diff_sha256",
    "reviewed_files",
    "findings",
    "created_at",
]


def _validate_codex_review_schema(data: Dict[str, Any]) -> None:
    """codex_review_result.json schema v2 유효성 검증 순수 함수.

    IMP-20260516-A627 MT-1: schema_version, stage, result, review_model 등
    신규 필수 필드를 검증한다. 검증 실패 시 ValueError를 발생시킨다.

    Args:
        data: codex_review_result.json에서 읽은 dict 객체.

    Raises:
        ValueError: 필수 필드 누락, 허용 외 값(stage/result/review_model) 등
                    스키마 위반 시 상세 메시지와 함께 발생.
    """
    if not isinstance(data, dict):
        raise ValueError("codex_review_result.json 최상위 값이 dict가 아닙니다.")

    # 1. 필수 필드 존재 여부 검증
    missing: List[str] = [f for f in CODEX_REQUIRED_FIELDS if f not in data]
    if missing:
        raise ValueError(
            f"[CODEX SCHEMA] 필수 필드 누락: {', '.join(missing)}. "
            f"codex_review_result.json에 해당 필드가 있어야 합니다."
        )

    # 2. schema_version 검증 (정수 1 이상)
    schema_version = data.get("schema_version")
    if not isinstance(schema_version, int) or schema_version < 1:
        raise ValueError(
            f"[CODEX SCHEMA] schema_version은 1 이상의 정수여야 합니다. 현재 값: {schema_version!r}"
        )

    # 3. pipeline_id 검증 (비어 있지 않은 문자열, 1~255자)
    pipeline_id = data.get("pipeline_id", "")
    if not isinstance(pipeline_id, str) or not pipeline_id.strip():
        raise ValueError(
            "[CODEX SCHEMA] pipeline_id는 비어 있지 않은 문자열이어야 합니다."
        )
    if len(pipeline_id.strip()) > 255:
        raise ValueError(
            f"[CODEX SCHEMA] pipeline_id 길이가 255자를 초과합니다: {len(pipeline_id.strip())}자"
        )

    # 4. stage 검증 (허용 값: plan/scope/code/hygiene/pr/rca)
    stage = data.get("stage", "")
    if not isinstance(stage, str) or stage.lower() not in CODEX_VALID_STAGES:
        raise ValueError(
            f"[CODEX SCHEMA] stage 값 '{stage}'는 허용되지 않습니다. "
            f"허용 값: {', '.join(sorted(CODEX_VALID_STAGES))}"
        )

    # 5. result 검증 (허용 값: ACCEPT/REJECT/PENDING)
    result = data.get("result", "")
    if not isinstance(result, str) or result.upper() not in CODEX_VALID_RESULTS:
        raise ValueError(
            f"[CODEX SCHEMA] result 값 '{result}'는 허용되지 않습니다. "
            f"허용 값: {', '.join(sorted(CODEX_VALID_RESULTS))}"
        )

    # 6. reviewer 검증 (비어 있지 않은 문자열)
    reviewer = data.get("reviewer", "")
    if not isinstance(reviewer, str) or not reviewer.strip():
        raise ValueError(
            "[CODEX SCHEMA] reviewer는 비어 있지 않은 문자열이어야 합니다."
        )

    # 7. review_model 검증 (반드시 GPT-5.5 — 표시용 대문자)
    review_model = data.get("review_model", "")
    if not isinstance(review_model, str) or review_model.strip() != CODEX_REQUIRED_REVIEW_MODEL:
        raise ValueError(
            f"[CODEX SCHEMA] review_model은 반드시 '{CODEX_REQUIRED_REVIEW_MODEL}'이어야 합니다. "
            f"현재 값: '{review_model}'. "
            f"GPT-5.5 외 모델(Claude, GPT-4 등)로 수행한 리뷰는 인정되지 않습니다."
        )

    # 8. diff_sha256 검증 (문자열, 비어 있어도 허용하되 빈 문자열 시 경고용으로 별도 처리)
    diff_sha256 = data.get("diff_sha256", None)
    if diff_sha256 is not None and not isinstance(diff_sha256, str):
        raise ValueError(
            f"[CODEX SCHEMA] diff_sha256는 문자열이어야 합니다. 현재 타입: {type(diff_sha256).__name__}"
        )

    # 9. reviewed_files 검증 (리스트여야 함)
    reviewed_files = data.get("reviewed_files", None)
    if not isinstance(reviewed_files, list):
        raise ValueError(
            f"[CODEX SCHEMA] reviewed_files는 리스트여야 합니다. 현재 타입: {type(reviewed_files).__name__}"
        )

    # 10. findings 검증 (리스트여야 함)
    findings = data.get("findings", None)
    if not isinstance(findings, list):
        raise ValueError(
            f"[CODEX SCHEMA] findings는 리스트여야 합니다. 현재 타입: {type(findings).__name__}"
        )

    # 11. findings 내 항목 검증 (있는 경우)
    for idx, finding in enumerate(findings):
        if not isinstance(finding, dict):
            raise ValueError(
                f"[CODEX SCHEMA] findings[{idx}]는 dict여야 합니다. "
                f"현재 타입: {type(finding).__name__}"
            )

    # 12. created_at 검증 (비어 있지 않은 문자열)
    created_at = data.get("created_at", "")
    if not isinstance(created_at, str) or not created_at.strip():
        raise ValueError(
            "[CODEX SCHEMA] created_at은 비어 있지 않은 문자열(ISO-8601 타임스탬프)이어야 합니다."
        )

    # 13. optional 필드 타입 검증 (존재하는 경우에만)
    optional_str_fields = ["review_type", "pr_number", "base_ref", "head_sha", "updated_at", "return_phase"]
    for field_name in optional_str_fields:
        field_val = data.get(field_name)
        if field_val is not None and not isinstance(field_val, (str, int)):
            raise ValueError(
                f"[CODEX SCHEMA] {field_name}는 문자열 또는 정수여야 합니다. "
                f"현재 타입: {type(field_val).__name__}"
            )

    optional_list_fields = ["allowed_files", "forbidden_files", "required_actions"]
    for field_name in optional_list_fields:
        field_val = data.get(field_name)
        if field_val is not None and not isinstance(field_val, list):
            raise ValueError(
                f"[CODEX SCHEMA] {field_name}는 리스트여야 합니다. "
                f"현재 타입: {type(field_val).__name__}"
            )


def _check_pm_clarification_gate(state: Dict[str, Any]) -> Tuple[bool, str]:
    """PM Clarification Gate — clarification_needed or empty acceptance_criteria blocks Dev entry.

    IMP-20260523-D80A: PM이 모호한 요구사항을 Dev로 넘기지 못하게 막는 hard gate.

    Args:
        state: 현재 파이프라인 state dict.

    Returns:
        Tuple[bool, str]: (ok, reason_message).
        - 레거시 파이프라인(pm_clarification_gate 필드 없음): PASS (하위 호환)
        - clarification_needed=true: FAIL
        - acceptance_criteria 비어 있음: FAIL
        - 둘 다 통과: PASS
    """
    cg = state.get("pm_clarification_gate")
    if cg is None:
        return (True, "")  # Legacy pipeline: no field -> PASS (사용자 답변 B)
    if cg.get("clarification_needed"):
        return (False, "PM clarification이 미해소 상태입니다. done --phase pm --clarification-needed false로 해소 후 진행하세요.")
    if not cg.get("acceptance_criteria"):
        return (False, "acceptance_criteria가 비어 있습니다. done --phase pm --clarification-criteria로 기준을 제공하세요.")
    return (True, "")


# ─── Codex Review coverage_checks (IMP-20260602-1ABE MT-7) ──────────────────
CODEX_COVERAGE_CHECK_FIELDS: List[str] = [
    "all_ac_reviewed",
    "diff_values_match_ac",
    "tests_assert_core_values",
    "no_dry_run_substitution",
    "no_stale_sha",
    "no_stale_ci_run",
    "user_facing_korean_ok",
]


def _validate_codex_coverage_checks(review_data: Dict[str, Any]) -> Dict[str, Any]:
    """coverage_checks 7개 필드 검증. 하나라도 false/누락이면 FAIL.

    legacy codex_review_result.json(coverage_checks 키 자체가 없음)은 PASS 처리하여
    하위 호환성을 유지한다.
    """
    coverage_checks = review_data.get("coverage_checks")
    if coverage_checks is None:
        return {"valid": True, "reason": "coverage_checks 없음 — legacy codex review"}

    if not isinstance(coverage_checks, dict):
        return {"valid": False, "errors": ["coverage_checks는 dict여야 합니다"]}

    issues: List[str] = []
    for field in CODEX_COVERAGE_CHECK_FIELDS:
        if field not in coverage_checks:
            issues.append(f"coverage_checks.{field} 필드가 누락되었습니다")
        elif coverage_checks[field] is not True:
            issues.append(f"coverage_checks.{field} = {coverage_checks[field]} (FAIL)")

    if issues:
        return {"valid": False, "errors": issues}
    return {"valid": True}


def _check_codex_review_gate(
    state: Dict[str, Any],
    required_stage: Optional[str] = None,
) -> Tuple[bool, str]:
    """Codex Review Gate 검증 (MT-4: IMP-20260516-A627 — absent=FAIL 강제 반전).

    codex_review_result.json이 없으면 FAIL (선택적 skip 제거).
    stage별 요구 stage 확인:
      - Dev 진입 (phase=dev): required_stage="plan" 또는 "scope" ACCEPT 확인
      - QA 진입 (phase=qa): required_stage="code" ACCEPT 확인
      - PR 생성 진입 (phase=pr): required_stage="hygiene" ACCEPT 확인

    Args:
        state: 현재 파이프라인 state dict.
        required_stage: 이 stage의 ACCEPT가 필요한지 확인 (None이면 stage 미검증).

    Returns:
        Tuple[bool, str]: (ok, reason_message).
    """
    review_path = BASE_DIR / "codex_review_result.json"

    # MT-4 핵심 변경: absent=FAIL (더 이상 skip 없음)
    if not review_path.exists():
        return False, (
            "[CODEX REVIEW REQUIRED] codex_review_result.json이 없습니다. "
            "'python pipeline.py review codex-run --stage plan --review-model GPT-5.5' "
            "로 Codex review를 먼저 수행하세요. "
            "legacy 파이프라인 등 waiver가 필요하면 "
            "--codex-review-waiver legacy-bootstrap 인자를 사용하세요."
        )

    try:
        review_data = json.loads(review_path.read_text(encoding="utf-8", errors="replace"))
    except Exception as exc:
        return False, f"[CODEX REVIEW REQUIRED] codex_review_result.json 파싱 실패: {exc}"

    # IMP-20260517-30DD MT-1: review_model 검증 (표시용 GPT-5.5)
    review_model = str(review_data.get("review_model", "")).strip()
    if review_model != CODEX_REQUIRED_REVIEW_MODEL:
        return False, (
            f"[CODEX REVIEW REQUIRED] review_model='{review_model}'은 허용되지 않습니다. "
            f"반드시 '{CODEX_REQUIRED_REVIEW_MODEL}'이어야 합니다. "
            f"GPT-5.5 세션으로 수행한 리뷰 파일을 제공하세요."
        )

    # IMP-20260517-30DD MT-1: actual_model_verified 검증 (provider-level evidence 필수)
    actual_model_verified = review_data.get("actual_model_verified")
    actual_model_id = str(review_data.get("actual_model_id", "")).strip()
    actual_model_source = str(review_data.get("actual_model_source", "")).strip()

    # actual_model_verified가 명시적으로 false이면 FAIL
    if actual_model_verified is False:
        return False, (
            "[CODEX REVIEW REQUIRED] actual_model_verified=false: "
            "provider-level evidence에서 실제 사용 모델이 gpt-5.5임을 검증하지 못했습니다. "
            "python pipeline.py review codex-run --stage <STAGE> --provider openai-api 를 실행하세요."
        )

    # actual_model_verified가 True인 경우 actual_model_id가 gpt-5.5(소문자 exact)인지 확인
    if actual_model_verified is True:
        if actual_model_id != CODEX_REQUIRED_MODEL_ID:
            return False, (
                f"[CODEX REVIEW REQUIRED] actual_model_id='{actual_model_id}'가 "
                f"'{CODEX_REQUIRED_MODEL_ID}'와 다릅니다. "
                "provider-level evidence의 실제 model ID가 gpt-5.5이어야 합니다."
            )
        # actual_model_source가 model output JSON이면 FAIL (provider-level이 아님)
        if actual_model_source and "model_output" in actual_model_source.lower():
            return False, (
                "[CODEX REVIEW REQUIRED] actual_model_source가 model output JSON입니다. "
                "model output의 review_model 필드는 actual evidence로 인정되지 않습니다. "
                "openai-api response.model 또는 codex-cli JSONL metadata를 사용하세요."
            )

    # result 검증 (PENDING이면 FAIL)
    result_val = str(review_data.get("result", "")).upper()
    if result_val == "PENDING":
        return False, (
            "[CODEX REVIEW REQUIRED] Codex review 결과가 PENDING 상태입니다. "
            "ACCEPT 또는 REJECT로 확정 후 재시도하세요."
        )
    if result_val == "REJECT":
        return False, (
            "[CODEX REVIEW REQUIRED] Codex review 결과가 REJECT입니다. "
            "failure_packet.json을 참조하여 지적 사항을 수정 후 재시도하세요."
        )

    # stage 검증 (required_stage가 지정된 경우)
    if required_stage is not None:
        current_stage = str(review_data.get("stage", "")).lower()
        # history 배열도 확인 (이전에 required_stage를 ACCEPT했는지)
        all_stages_accepted: List[str] = []
        if result_val == "ACCEPT" and current_stage:
            all_stages_accepted.append(current_stage)
        history = review_data.get("history", [])
        for h in history:
            if str(h.get("result", "")).upper() == "ACCEPT" and h.get("stage"):
                all_stages_accepted.append(str(h.get("stage", "")).lower())

        # Dev 진입은 plan AND scope 모두 ACCEPT여야 함 (D3 수정: OR → AND)
        if required_stage in {"plan", "scope"}:
            # bootstrap_exception: 이 IMP 자체가 codex gate를 구현하므로 waiver 적용
            bootstrap_exception = state.get("codex_bootstrap_exception", False)
            if bootstrap_exception:
                # bootstrap_exception이면 plan/scope gate skip
                pass
            else:
                has_plan = "plan" in all_stages_accepted
                has_scope = "scope" in all_stages_accepted
                if not has_plan and not has_scope:
                    return False, (
                        "[CODEX REVIEW REQUIRED] Dev 진입 전 plan AND scope stage 모두 ACCEPT가 필요합니다. "
                        f"현재 통과된 stages: {all_stages_accepted if all_stages_accepted else '없음'}. "
                        "'python pipeline.py review codex --stage plan ...' 및 "
                        "'python pipeline.py review codex --stage scope ...' 를 모두 수행하세요."
                    )
                elif not has_plan:
                    return False, (
                        "[CODEX REVIEW REQUIRED] Dev 진입 전 plan stage ACCEPT가 필요합니다. "
                        f"현재 통과된 stages: {all_stages_accepted}. "
                        "'python pipeline.py review codex-run --stage plan --review-model GPT-5.5' 를 먼저 수행하세요."
                    )
                elif not has_scope:
                    return False, (
                        "[CODEX REVIEW REQUIRED] Dev 진입 전 scope stage ACCEPT가 필요합니다. "
                        f"현재 통과된 stages: {all_stages_accepted}. "
                        "'python pipeline.py review codex-run --stage scope --review-model GPT-5.5' 를 먼저 수행하세요."
                    )
        elif required_stage not in all_stages_accepted:
            return False, (
                f"[CODEX REVIEW REQUIRED] {required_stage} stage ACCEPT가 필요합니다. "
                f"현재 통과된 stages: {all_stages_accepted if all_stages_accepted else '없음'}. "
                f"'python pipeline.py review codex-run --stage {required_stage} ...' 를 먼저 수행하세요."
            )

    # findings 검증 (미해결 HIGH/CRITICAL)
    findings: List[Dict[str, Any]] = review_data.get("findings", [])
    unresolved_hc: List[Dict[str, Any]] = [
        f for f in findings
        if not f.get("resolved", False) and str(f.get("severity", "")).upper() in {"HIGH", "CRITICAL"}
    ]
    if unresolved_hc:
        ids = [str(f.get("id", "?")) for f in unresolved_hc]
        return False, (
            f"[CODEX REVIEW REQUIRED] 미해결 HIGH/CRITICAL findings {len(unresolved_hc)}개: "
            f"{', '.join(ids)}. "
            f"'python pipeline.py review resolve --id <ID>' 로 해소 후 재시도."
        )

    # IMP-20260602-1ABE MT-7: coverage_checks 7개 필드 검증
    coverage_result = _validate_codex_coverage_checks(review_data)
    if not coverage_result.get("valid"):
        return False, (
            "[CODEX REVIEW REQUIRED] coverage_checks 검증 실패: "
            + "; ".join(coverage_result.get("errors", []))
        )

    # IMP-20260602-1ABE MT-7: criteria_review blocking FAIL/UNCLEAR 검사
    criteria_review = review_data.get("criteria_review") or []
    if isinstance(criteria_review, list) and criteria_review:
        blocking_items = [
            item for item in criteria_review
            if isinstance(item, dict)
            and item.get("blocking") is True
            and str(item.get("status", "")).upper() in ("FAIL", "UNCLEAR")
        ]
        if blocking_items:
            details = ", ".join(
                f"{item.get('ac_id', '?')}={item.get('status', '?')}"
                for item in blocking_items
            )
            return False, (
                "[CODEX REVIEW REQUIRED] criteria_review FAIL/UNCLEAR blocking 항목 "
                f"{len(blocking_items)}개: {details}"
            )

    # diff_sha256 최신성 검증
    stored_sha = str(review_data.get("diff_sha256", ""))
    base_ref = str(review_data.get("base_ref", "main") or "main")
    if stored_sha:
        try:
            diff_proc = subprocess.run(
                ["git", "diff", f"{base_ref}...HEAD"],
                capture_output=True,
                cwd=str(BASE_DIR),
                timeout=30,
            )
            if diff_proc.returncode == 0 and diff_proc.stdout is not None:
                current_sha = hashlib.sha256(diff_proc.stdout).hexdigest()
                if current_sha != stored_sha:
                    return False, (
                        "[CODEX REVIEW REQUIRED] diff SHA256 불일치 — 코드가 Codex 리뷰 이후에 변경되었습니다. "
                        "'python pipeline.py review codex-run --stage code ...' 로 리뷰를 갱신하세요."
                    )
        except Exception as exc:
            logging.getLogger(__name__).warning("Codex review diff SHA check 실패: %s", exc)

    return True, "codex review gate passed"


def _check_codex_pr_gate_for_technical(state: Dict[str, Any]) -> Optional[str]:
    """D4: pr stage ACCEPT 여부를 확인하는 헬퍼. 문제 있으면 에러 메시지 반환, 없으면 None.

    gates technical 및 gates accept ACCEPT 시 codex pr stage ACCEPT를 요구한다.
    bootstrap_exception=true인 파이프라인은 이 함수를 호출하지 않는다.

    Args:
        state: 파이프라인 state dict.

    Returns:
        str: 에러 메시지 (차단 사유). None이면 통과.
    """
    review_path = BASE_DIR / "codex_review_result.json"
    if not review_path.exists():
        # D4: Codex review 파일이 없는 파이프라인(레거시/비Codex)은 통과.
        # 파일이 있는 경우에만 pr stage ACCEPT를 검증한다.
        return None
    try:
        review_data = json.loads(review_path.read_text(encoding="utf-8", errors="replace"))
    except Exception as exc:
        return f"[CODEX PR GATE] codex_review_result.json 파싱 실패: {exc}"

    # pr stage ACCEPT 여부를 history + 현재 top-level에서 확인
    all_accepted_stages: List[str] = []
    current_stage = str(review_data.get("stage", "")).lower()
    current_result = str(review_data.get("result", "")).upper()
    if current_result == "ACCEPT" and current_stage:
        all_accepted_stages.append(current_stage)
    for h in review_data.get("history", []):
        if str(h.get("result", "")).upper() == "ACCEPT" and h.get("stage"):
            all_accepted_stages.append(str(h.get("stage", "")).lower())

    if "pr" not in all_accepted_stages:
        return (
            "[CODEX PR GATE] Technical gate 진입 전 pr stage Codex review ACCEPT가 필요합니다. "
            f"현재 ACCEPT된 stages: {all_accepted_stages if all_accepted_stages else '없음'}. "
            "'python pipeline.py review codex-record --stage pr --result ACCEPT --review-model GPT-5.5 ...' 를 먼저 수행하세요."
        )
    return None


def cmd_check(args: argparse.Namespace) -> None:
    """gate 검증 -exit 0: 통과, exit 1: 차단."""
    state = _load_branch_state(args)
    phase = args.phase.lower()

    # Phase 6 -> 7 does not ask the user anymore. Phase 7 is deterministic
    # automation; the only user decision is the final gates accept ACCEPT/REJECT.

    # Codex Review Gate — MT-4: stage별 분기 적용 (IMP-20260516-A627)
    # --codex-review-waiver legacy-bootstrap 인자가 있으면 waiver 허용
    codex_waiver: str = getattr(args, "codex_review_waiver", "") or ""
    skip_codex_gate = (codex_waiver.strip().lower() == "legacy-bootstrap")

    if not skip_codex_gate:
        # Dev 진입: plan 또는 scope ACCEPT 필요
        if phase == "dev":
            cr_ok, cr_reason = _check_codex_review_gate(state, required_stage="plan")
            if not cr_ok:
                _cr_dev_category = (
                    "stale_evidence" if ("SHA256" in cr_reason or "diff" in cr_reason)
                    else "model_verification_failed" if ("review_model" in cr_reason or "model" in cr_reason.lower())
                    else "missing_evidence"
                )
                _record_failure_packet(
                    state, "technical", {},
                    command=[sys.executable, "pipeline.py", "review", "codex-run",
                             "--stage", "plan", "--review-model", "GPT-5.5"],
                    note=cr_reason,
                    status="BLOCKED",
                    phase="dev",
                    failure_code=f"codex_review_{_cr_dev_category}",
                    failure_category=_cr_dev_category,
                    summary_ko=f"Dev 진입 차단 — Codex review gate: {cr_reason}",
                    expected="codex_review_result.json에 plan/scope ACCEPT + GPT-5.5 + 최신 diff_sha256",
                    actual=cr_reason,
                    exit_code=1,
                    owner="Dev",
                    return_phase="pm",
                    required_actions=["python pipeline.py review codex-run --stage plan --review-model GPT-5.5 재실행"],
                    retry_allowed=True,
                )
                _save(state)
                print()
                print(RED("[CODEX REVIEW REQUIRED] Dev 진입 차단"))
                print(RED(f"  사유: {cr_reason}"))
                print()
                sys.exit(1)
        # QA 진입: code ACCEPT 필요
        elif phase == "qa":
            cr_ok, cr_reason = _check_codex_review_gate(state, required_stage="code")
            if not cr_ok:
                _cr_qa_category = (
                    "stale_evidence" if ("SHA256" in cr_reason or "diff" in cr_reason)
                    else "model_verification_failed" if ("review_model" in cr_reason or "model" in cr_reason.lower())
                    else "missing_evidence"
                )
                _record_failure_packet(
                    state, "technical", {},
                    command=[sys.executable, "pipeline.py", "review", "codex-run",
                             "--stage", "code", "--review-model", "GPT-5.5"],
                    note=cr_reason,
                    status="BLOCKED",
                    phase="qa",
                    failure_code=f"codex_review_{_cr_qa_category}",
                    failure_category=_cr_qa_category,
                    summary_ko=f"QA 진입 차단 — Codex review gate: {cr_reason}",
                    expected="codex_review_result.json에 code ACCEPT + GPT-5.5 + 최신 diff_sha256",
                    actual=cr_reason,
                    exit_code=1,
                    owner="QA",
                    return_phase="dev",
                    required_actions=["python pipeline.py review codex-run --stage code --review-model GPT-5.5 재실행"],
                    retry_allowed=True,
                )
                _save(state)
                print()
                print(RED("[CODEX REVIEW REQUIRED] QA 진입 차단"))
                print(RED(f"  사유: {cr_reason}"))
                print()
                sys.exit(1)

    # PM Clarification Gate (IMP-20260523-D80A) — codex gate skip 여부와 무관하게 항상 동작
    if phase == "dev":
        cg_ok, cg_reason = _check_pm_clarification_gate(state)
        if not cg_ok:
            _record_failure_packet(
                state, "technical", {},
                command=[sys.executable, "pipeline.py", "done", "--phase", "pm",
                         "--clarification-needed", "false", "--clarification-criteria", "..."],
                note=cg_reason,
                status="BLOCKED",
                phase="dev",
                failure_code="pm_clarification_gate_blocked",
                failure_category="missing_evidence",
                summary_ko=f"Dev 진입 차단 — PM Clarification Gate: {cg_reason}",
                expected="clarification_needed=false AND acceptance_criteria 비어있지 않음",
                actual=cg_reason,
                exit_code=1,
                owner="PM",
                return_phase="pm",
                required_actions=["python pipeline.py done --phase pm --clarification-needed false --clarification-criteria '...'"],
                retry_allowed=True,
            )
            _save(state)
            print()
            print(RED("[PM CLARIFICATION GATE BLOCKED] Dev 진입 차단"))
            print(RED(f"  사유: {cg_reason}"))
            print()
            sys.exit(1)

    # IMP-20260527-075A MT-3: attempt budget gate — dev/qa/build 진입 직전 budget 차단 검사.
    # phase별 한도 초과 또는 동일 failure_code 반복 시 exit 1 + 한국어 메시지 + failure_packet.
    if phase in ("dev", "qa", "build"):
        # build phase는 budget 추적 대상이 아니므로 dev/qa만 검사 (build는 gate 카테고리)
        budget_phase = phase if phase in ("dev", "qa") else None
        if budget_phase is not None:
            _ensure_attempt_budget_keys(state)
            bg_check = _check_attempt_budget(state, budget_phase)
            if bg_check["blocked"]:
                msg = _korean_budget_message(
                    budget_phase,
                    bg_check["attempts_used"],
                    bg_check["max_attempts"],
                    bg_check["failure_code"],
                    bg_check.get("repeat_failure_code"),
                )
                _record_failure_packet(
                    state, "technical", {},
                    command=[sys.executable, "pipeline.py", "budget", "reset",
                             "--phase", budget_phase, "--reason", "재시도 한도 초과 후 사용자 확인 재시작"],
                    note=msg,
                    status="BLOCKED",
                    phase=budget_phase,
                    failure_code=str(bg_check["failure_code"]),
                    failure_category="missing_evidence",
                    summary_ko=msg,
                    expected=f"{budget_phase} phase 재시도 한도 내",
                    actual=msg,
                    exit_code=1,
                    owner="Pipeline Manager",
                    return_phase="architect",
                    required_actions=[
                        "prompt-architect-agent로 이관하여 RCA 수행",
                        f"사용자 확인 후 `python pipeline.py budget reset --phase {budget_phase} --reason ...`로 재시작",
                    ],
                    retry_allowed=False,
                )
                _save(state)
                print()
                print(RED(f"[GATE BLOCKED] {budget_phase} phase 재시도 한도 초과"))
                print(RED(f"  {msg}"))
                print()
                sys.exit(1)

    ok, reason = check_gate(state, phase)

    if ok:
        # IMP-20260522-29C1 fix-forward v3: phase 진입 시점(check PASS)에 started_at 기록.
        # done/qa 완료 시점이 아닌 check PASS 순간에 기록해야 elapsed가 정확해진다.
        _chk_phase = state.get("phases", {}).get(phase)
        if isinstance(_chk_phase, dict) and not _chk_phase.get("started_at"):
            _chk_phase["started_at"] = _now()
            _save(state)
        print(GREEN(f"\n[GATE OK] {PHASE_LABELS.get(phase, phase)} 진입 가능\n"))
        sys.exit(0)
    else:
        # MT-2 (IMP-20260519-EC9F): check gate 차단 시 failure_packet 생성
        # 선행 phase가 완료되지 않아 진입이 차단된 경우
        _record_failure_packet(
            state,
            f"check_{phase}",
            {},
            failure_code=f"gate_blocked_{phase}",
            failure_category="missing_evidence",
            summary_ko=f"{PHASE_LABELS.get(phase, phase)} 진입 차단 — {reason}",
            expected=f"Phase {phase} 진입을 위한 선행 단계 완료",
            actual=reason,
            owner="Pipeline Manager",
            return_phase=_gate_return_phase_for_check(phase),
            required_actions=[
                "python pipeline.py status 로 미완료 phase 확인",
                f"미완료 선행 phase를 완료한 후 다시 python pipeline.py check --phase {phase} 실행",
            ],
            retry_allowed=True,
        )
        _save(state)
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


def _gate_return_phase_for_check(phase: str) -> str:
    """check --phase [phase] 차단 시 복구해야 할 return_phase를 반환.

    IMP-20260519-EC9F MT-2: cmd_check 실패 packet의 return_phase를 일관되게 결정.
    """
    mapping: Dict[str, str] = {
        "dev": "pm",
        "qa": "dev",
        "sec": "dev",
        "build": "qa",
        "architect": "harness",
    }
    return mapping.get(phase, "pm")


def cmd_done(args: argparse.Namespace) -> None:
    """pm 또는 dev phase 완료 처리."""
    branch: Optional[str] = getattr(args, "branch", None)
    state = _load_branch_state(args, "state 파일이 없습니다. tournament-start 먼저 실행하세요.")
    phase = args.phase.lower()

    if phase not in ("pm", "dev"):
        _die("'done' 명령은 pm/dev 전용입니다. qa/sec/build/harness는 전용 명령 사용.")

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
        # PM Clarification Gate 저장 (IMP-20260523-D80A)
        _cn = bool(getattr(args, "clarification_needed", False))
        _assumptions = str(getattr(args, "clarification_assumptions", "없음") or "없음")
        _criteria_source = str(getattr(args, "clarification_criteria_source", "user") or "user")
        _criteria_raw = getattr(args, "clarification_criteria", None)
        _criteria_list: list = []
        if _criteria_raw:
            _criteria_list = [c.strip() for c in str(_criteria_raw).split(",") if c.strip()]
        state["pm_clarification_gate"] = {
            "clarification_needed": _cn,
            "assumptions": _assumptions,
            "acceptance_criteria_source": _criteria_source,
            "acceptance_criteria": _criteria_list,
            "recorded_at": _now(),
        }
        print(GREEN(f"  [PM CLARIFICATION GATE] clarification_needed={_cn}, criteria={len(_criteria_list)}개"))
        # IMP-20260602-1ABE MT-2: structured AC를 state 상위 키로 저장 + requirements_tracking 플래그
        # _validate_pm_step_plan_file가 atomic_plan에 structured_acceptance_criteria를 채워둔다 (MT-1).
        # 여기서는 그 값을 state 상위로 복사하고 requirements_tracking을 활성화한다.
        # legacy 호환: structured AC가 있고 pm_clarification_gate.acceptance_criteria가 비어있으면
        # structured AC의 requirement 문자열로 자동 백필 (기존 코드 깨지지 않도록).
        _atomic_plan = state.get("atomic_plan") or {}
        _structured_ac_from_plan = _atomic_plan.get("structured_acceptance_criteria") or []
        if _structured_ac_from_plan:
            state["requirements_tracking"] = {
                "enabled": True,
                "schema_version": 1,
                "recorded_at": _now(),
            }
            state["structured_acceptance_criteria"] = _structured_ac_from_plan
            # legacy 자동 백필: pm_clarification_gate.acceptance_criteria가 비어있을 때만
            if not state["pm_clarification_gate"].get("acceptance_criteria"):
                state["pm_clarification_gate"]["acceptance_criteria"] = [
                    str(ac.get("requirement", "")).strip()
                    for ac in _structured_ac_from_plan
                    if ac.get("requirement")
                ]
            print(GREEN(
                f"  [REQUIREMENTS TRACKING] enabled=true structured_ac={len(_structured_ac_from_plan)}개"
            ))
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
    agent_run: Optional[Dict[str, Any]] = None
    if phase == "pm":
        if not report_file:
            _die(
                "[ATOMIC PLAN GATE] PM done requires "
                "`python pipeline.py done --phase pm --report-file step_plan.xml --planner-run-id <planner_run_id> "
                "--manager-run-id <manager_run_id> --manager-report manager_handoff.xml --decomp --clarification ...`"
            )
        # Legacy compat: old pipelines pass --agent-run-id for pm. New pipelines use
        # --planner-run-id + --manager-run-id. Accept both; reject only if neither provided
        # when phase_attestations are enabled (which requires receipts).
        legacy_agent_run_id = getattr(args, "agent_run_id", None)
        planner_run_id = getattr(args, "planner_run_id", None)
        manager_run_id = getattr(args, "manager_run_id", None)
        manager_report = getattr(args, "manager_report", None)

        _is_legacy_pm = bool(legacy_agent_run_id and not planner_run_id and not manager_run_id)

        if _is_legacy_pm:
            # Legacy PM path: single --agent-run-id pointing at a "pm" phase receipt
            planner_run = _validate_agent_run_receipt(
                state,
                "pm_planner",  # logical phase; legacy receipt has phase="pm"
                legacy_agent_run_id,
                report_file,
                consume_phase="pm",
            )
            manager_run = None
        else:
            planner_run = _validate_agent_run_receipt(
                state,
                "pm_planner",
                planner_run_id,
                report_file,
                consume_phase="pm",
            )
            manager_run = _validate_agent_run_receipt(
                state,
                "pipeline_manager",
                manager_run_id,
                manager_report,
                consume_phase="pm_manager",
            )
        state["atomic_plan"] = _validate_pm_step_plan_file(report_file, state)
        state["execution_profile"] = state["atomic_plan"].get("execution_profile") or _new_execution_profile("STANDARD")
        if not _is_legacy_pm:
            state["pm_manager_handoff"] = _validate_manager_handoff_file(
                manager_report,
                state,
                step_plan_file=report_file,
                planner_run_id=str(planner_run_id),
            )
        if state["atomic_plan"]["audit_result"] == "AMBIGUOUS" and not judgment_confirmed:
            _die(
                "[ATOMIC PLAN GATE] AMBIGUOUS decomposition requires --judgment-confirmed "
                "and <judgment_calls_resolved> in the PM report."
            )
        print(GREEN(
            f"  [ATOMIC PLAN GATE] micro_tasks={state['atomic_plan']['micro_task_count']} "
            f"audit={state['atomic_plan']['audit_result']}"
        ))
        module_gates = _init_module_gates_from_atomic_plan(state)
        print(GREEN(
            f"  [MODULE GATE] initialized {len(module_gates.get('sequence', []))} incremental modules"
        ))
        profile = _execution_profile(state)
        print(GREEN(
            f"  [EXECUTION PROFILE] {profile.get('mode')} "
            f"product_code_write_allowed={profile.get('product_code_write_allowed')}"
        ))
        agent_run = planner_run
        if manager_run is not None:
            state["phases"]["pm"]["manager_run_id"] = manager_run["run_id"]
            state["phases"]["pm"]["manager_agent_id"] = manager_run["agent_id"]
            state["phases"]["pm"]["manager_report_file"] = manager_report

    evidence = args.files if hasattr(args, "files") and args.files else None

    if phase == "dev":
        scope_declared: bool = bool(getattr(args, "scope_declared", False))
        if not scope_declared:
            _die(
                "[SCOPE GATE] Dev DONE requires --scope-declared. "
                "dev-agent must provide <scope_declaration> before Phase 2 can close."
            )
        module_blockers = _module_gate_blockers(state)
        if module_blockers:
            _die(
                "[MODULE GATE] Dev DONE requires every micro_task to pass module QA "
                "and integration first: " + "; ".join(module_blockers)
            )
        _validate_module_checkpoints(state)
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

    # dev phase: --files에 나열된 경로가 실제로 존재하는지 검증
    if phase == "dev" and evidence:
        missing = [t.strip() for t in evidence.split(",") if t.strip() and not Path(t.strip()).exists()]
        if missing:
            print(RED("\n[FILE NOT FOUND] DONE 기록 거부 — 존재하지 않는 파일:"))
            for p in missing: print(RED(f"  - {p}"))
            print(RED("dev-agent가 실제로 파일을 작성한 후 다시 실행하세요.\n"))
            sys.exit(1)

    if phase != "pm":
        agent_run = _validate_agent_run_for_phase(
            state,
            phase,
            getattr(args, "agent_run_id", None),
            report_file,
        )

    # IMP-20260522-29C1 MT-2 (fix-forward): phase started_at fallback.
    # pm phase는 pipeline_started_at을 시작 시점으로 사용한다.
    # 그 외 phase는 check --phase 시점에 started_at이 기록되어야 한다.
    # done 시점에 fallback으로 _now()를 기록하면 elapsed가 ≈0이 되므로 제거한다.
    if not state["phases"]["pm"].get("started_at") and phase == "pm":
        state["phases"]["pm"]["started_at"] = (
            state.get("pipeline_started_at") or state.get("created_at")
        )
    state["phases"][phase]["status"]       = "DONE"
    state["phases"][phase]["completed_at"] = _now()
    state["phases"][phase]["evidence"]     = evidence
    state["phases"][phase]["report_file"]  = report_file
    if agent_run:
        state["phases"][phase]["agent_run_id"] = agent_run["run_id"]
        state["phases"][phase]["agent_id"] = agent_run["agent_id"]
    state["current_phase"] = PHASE_ORDER[PHASE_ORDER.index(phase) + 1]
    _log_event(state, f"{phase} DONE | evidence: {evidence}")
    # IMP-20260527-075A MT-3: dev DONE PASS 시 dev attempt budget 초기화 (성공 = 새 사이클)
    if phase == "dev":
        _ensure_attempt_budget_keys(state)
        _record_attempt_budget(state, "dev", "PASS")
    if phase == "dev":
        if _openai_advisory_required():
            review_files = _advisory_review_files_from_args(state, evidence)
            advisory_result = _auto_run_openai_advisory(state, kind="gpt-code", files=review_files)
            status = advisory_result.get("status")
            if status == "COMPLETED":
                print(GREEN(f"  [GPT ADVISORY] gpt-code completed via {OPENAI_ADVISORY_MODEL}"))
            elif status == "SKIPPED":
                print(YELLOW(f"  [GPT ADVISORY] gpt-code skipped: {advisory_result.get('reason')}"))
            else:
                print(RED(f"  [GPT ADVISORY] gpt-code error: {advisory_result.get('reason')}"))
        else:
            print(DIM("  [GPT ADVISORY] auto-run disabled by default (set ENABLE_GPT_ADVISORY_REQUIRED=1 to enable)"))
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
    # --numeric-score: QA가 산출한 수치 점수(0~QA_MAX_SCORE 정수).
    # PASS/FAIL 공통 hard gate: --numeric-score 필수
    # PASS 시: QA_PASS_THRESHOLD점(QA_MAX_SCORE의 80%) 이상 추가 요건.
    # FAIL 시: 점수 하한 없음. 이 값은 QA 하한선과 Circuit Breaker 추적용이며
    # Phase 7 COMPLETE 판정이나 external gate를 대체하지 않는다.
    numeric_score_raw: Optional[str] = getattr(args, "numeric_score", None)
    numeric_score: Optional[int] = None
    if numeric_score_raw is None:
        _die(
            "\n[QA NUMERIC GATE] --numeric-score 필수 (PASS/FAIL 공통).\n"
            f"  qa-agent는 <numeric_score> 블록을 출력하고 0~{QA_MAX_SCORE} 점수를 반드시 제출해야 합니다.\n"
            "  예: python pipeline.py qa --result FAIL --numeric-score 60 --failure-sig \"PD:abc123\"\n"
        )
    if numeric_score_raw is not None:
        try:
            numeric_score = int(numeric_score_raw)
        except (ValueError, TypeError):
            _die(f"--numeric-score 는 0~{QA_MAX_SCORE} 정수여야 합니다.")
        if not (0 <= numeric_score <= QA_MAX_SCORE):
            _die(f"--numeric-score 범위 오류: 0~{QA_MAX_SCORE} 이어야 합니다.")
        if result == "PASS" and numeric_score < QA_PASS_THRESHOLD:
            print(RED(
                f"\n[QA NUMERIC GATE] PASS 기록 거부 — numeric_score={numeric_score} < {QA_PASS_THRESHOLD} ({int(QA_PASS_RATIO * 100)}% of {QA_MAX_SCORE})"
            ))
            print(RED("  QA numeric_verdict가 FAIL이면 --result FAIL로 기록하세요. 최종 COMPLETE는 external gates가 결정합니다.\n"))
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
        sig_match = re.fullmatch(r"([A-Z][A-Z0-9_-]{1,15}):([0-9a-fA-F]{8})", failure_sig)
        if not sig_match:
            _die(
                "[QA GATE] --failure-sig는 '[CATEGORY]:[HASH8]' 형식이어야 합니다. "
                "HASH8은 정확히 8자리 16진수입니다. 슬러그 signature는 동일 오류를 "
                "다른 오류처럼 쪼개 Circuit Breaker를 약화시키므로 허용하지 않습니다."
            )
        failure_sig = f"{sig_match.group(1)}:{sig_match.group(2).lower()}"
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

    # IMP-20260527-075A MT-3: attempt budget 누적/초기화 (qa phase)
    _ensure_attempt_budget_keys(state)
    if result == "FAIL":
        _record_attempt_budget(state, "qa", "FAIL", failure_code=failure_sig)
    else:
        _record_attempt_budget(state, "qa", "PASS")

    # ── QA Report Hallucination Gate ──────────────────────────────────────────
    report_file: Optional[str] = getattr(args, "report_file", None)
    qa_report_validation = _validate_qa_report_file(
        report_file,
        result=result,
        numeric_score=int(numeric_score),
    )
    state["qa_report_validation"] = qa_report_validation

    agent_run = _validate_agent_run_for_phase(
        state,
        "qa",
        getattr(args, "agent_run_id", None),
        report_file,
    )

    # IMP-20260522-29C1 MT-2 (fix-forward): qa phase started_at.
    # check --phase qa 시점에 started_at이 기록되어야 한다.
    # qa 결과 기록 시점에 fallback으로 _now()를 쓰면 elapsed ≈ 0이 되므로 제거한다.
    state["phases"]["qa"]["status"]       = result
    state["phases"]["qa"]["completed_at"] = _now()
    state["phases"]["qa"]["evidence"]     = getattr(args, "agent_id", None)
    state["phases"]["qa"]["agent_id"]     = getattr(args, "agent_id", None)
    state["phases"]["qa"]["report_file"]  = report_file
    if agent_run:
        state["phases"]["qa"]["agent_run_id"] = agent_run["run_id"]
        state["phases"]["qa"]["agent_id"] = agent_run["agent_id"]
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
        next_cmd = "python pipeline.py done --phase dev --files \"수정된파일들\" --report-file dev_handover.xml --scope-declared --scope-manifest scope_manifest.json --agent-run-id <dev_run_id>"
        # MT-2 (IMP-20260519-EC9F): QA FAIL 시 failure_packet 생성
        _sig = str(failure_sig or "")
        _record_failure_packet(
            state,
            "qa",
            {},
            failure_code=f"qa_fail_{_sig.split(':')[0].lower() if _sig else 'unknown'}",
            failure_category="test_failed",
            summary_ko=f"QA FAIL — failure_signature={_sig or 'N/A'}, score={numeric_score}/{QA_MAX_SCORE}",
            expected=f"QA numeric_score >= {QA_PASS_THRESHOLD}/{QA_MAX_SCORE} 모든 카테고리 PASS",
            actual=f"numeric_score={numeric_score}, failure_signature={_sig or 'N/A'}",
            owner="Dev",
            return_phase="dev",
            required_actions=[
                "qa_report.xml 의 critical_issues 항목을 수정하세요",
                "python pipeline.py done --phase dev --files \"수정된파일들\" --report-file dev_handover.xml --scope-declared --scope-manifest scope_manifest.json --agent-run-id <dev_run_id>",
            ],
            retry_allowed=True,
        )

    _log_event(state, f"qa {result}" + (f" numeric={numeric_score}" if numeric_score is not None else ""))
    _record_snapshot(state, "qa", branch)
    _save_state_for(state, branch)
    branch_tag = f" [Branch {branch}]" if branch else ""
    print(f"\n{msg}{branch_tag}")
    if numeric_score is not None:
        score_color = GREEN if (result == "PASS") else RED
        print(score_color(f"  numeric_score={numeric_score}/{QA_MAX_SCORE}"))
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
            # MT-2 (IMP-20260519-EC9F): SEC BLOCK 시 failure_packet 생성
            _record_failure_packet(
                state,
                "sec",
                {},
                failure_code="sec_block_critical",
                failure_category="security_failed",
                summary_ko=f"보안 감사 BLOCK — risk_level={risk}, Critical 취약점 발견",
                expected="security_agent 감사 결과 SAFE (risk_level=LOW)",
                actual=f"risk_level={risk}, BLOCK 판정",
                owner="Dev",
                return_phase="dev",
                required_actions=[
                    "security_audit 리포트의 CRITICAL finding remediation_code를 적용하세요",
                    "python pipeline.py done --phase dev 이후 sec 재감사 실행",
                ],
                retry_allowed=True,
            )
            _save_state_for(state, branch)
            _die(f"[SEC BLOCK] risk_level={risk} -dev-agent 수정 후 재감사 필요.", exit_code=2)
        if result == "FAIL":
            state["phases"]["sec"]["status"]       = "FAIL"
            state["phases"]["sec"]["completed_at"] = _now()
            state["phases"]["sec"]["evidence"]     = risk
            state["current_phase"] = "dev"
            state["phases"]["dev"]["status"] = "PENDING"
            _log_event(state, f"sec FAIL risk={risk}")
            # MT-2 (IMP-20260519-EC9F): SEC FAIL 시 failure_packet 생성
            _record_failure_packet(
                state,
                "sec",
                {},
                failure_code="sec_fail_high",
                failure_category="security_failed",
                summary_ko=f"보안 감사 FAIL — risk_level={risk}, HIGH 이상 취약점 발견",
                expected="security_agent 감사 결과 SAFE 또는 LOW risk",
                actual=f"risk_level={risk}, FAIL 판정 (Tier2 이상)",
                owner="Dev",
                return_phase="dev",
                required_actions=[
                    "security_audit 리포트의 HIGH/MEDIUM finding을 수정하세요",
                    "python pipeline.py done --phase dev 이후 python pipeline.py sec 재실행",
                ],
                retry_allowed=True,
            )
            _save_state_for(state, branch)
            print(YELLOW(f"\n[SEC FAIL] risk_level={risk} — Tier2 이상 발견"))
            print("\n  다음: " + YELLOW('python pipeline.py done --phase dev --files "수정된파일들" --report-file dev_handover.xml --scope-declared --scope-manifest scope_manifest.json --agent-run-id <dev_run_id>') + "\n")
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

    # --build-deferred: 패키징 파일 변경이 감지되었으나 최종 ACCEPT 직전까지 빌드를 유보.
    # build_deferred=true를 pipeline_state.json에 기록하고 즉시 종료.
    # 실제 EXE 빌드 기록은 ACCEPT 직전 별도 명령으로 수행한다.
    build_deferred_flag: bool = bool(getattr(args, "build_deferred", False))
    if build_deferred_flag:
        state["build_deferred"] = True
        _log_event(state, "build_deferred=true recorded (EXE build deferred to pre-ACCEPT step)")
        _save(state)
        print(GREEN("[BUILD DEFERRED] build_deferred=true 기록됨. 최종 ACCEPT 전에 EXE 빌드를 완료하세요."))
        return

    ok, reason = check_gate(state, "build")
    if not ok:
        _record_failure_packet(
            state,
            "build_gate",
            {},
            failure_code="build_gate_blocked",
            failure_category="missing_evidence",
            summary_ko=f"Build 진입 차단 — {reason}",
            expected="QA PASS, SEC SKIP/PASS 완료",
            actual=reason,
            owner="Pipeline Manager",
            return_phase="qa",
            required_actions=[
                "python pipeline.py status 로 미완료 phase 확인",
                "QA PASS 및 SEC SKIP/PASS 완료 후 재시도",
            ],
            retry_allowed=True,
        )
        _save(state)
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
            _record_failure_packet(
                state,
                "build_exe_missing",
                {},
                failure_code="build_exe_missing",
                failure_category="missing_artifact",
                summary_ko=f"Build 실패 — EXE 파일 없음: {exe}",
                expected=f"EXE 파일 존재: {exe}",
                actual="파일 없음",
                owner="Build",
                return_phase="build",
                required_actions=[
                    "PyInstaller를 실행하여 EXE를 생성하세요: pyinstaller --onefile main.py",
                    f"EXE 생성 확인 후 재실행: python pipeline.py build --exe {exe}",
                ],
                retry_allowed=True,
            )
            _save(state)
            _die(
                f"\n[BUILD EXE GATE] EXE 파일 없음: {exe}\n"
                "  PyInstaller dist/ 폴더에 EXE가 생성된 후 이 명령을 실행하세요.\n"
            )
        # dist/build_report.xml 기본 경로 또는 --report-file 지정 경로
        if build_report_file is None:
            build_report_file = str(BASE_DIR / "dist" / "build_report.xml")
        build_report_path = Path(build_report_file)
        if not build_report_path.exists():
            _record_failure_packet(
                state,
                "build_report_missing",
                {},
                failure_code="build_report_xml_missing",
                failure_category="missing_artifact",
                summary_ko=f"Build 실패 — build_report.xml 없음: {build_report_file}",
                expected=f"build_report.xml 존재: {build_report_file}",
                actual="파일 없음",
                owner="Build",
                return_phase="build",
                required_actions=[
                    "build-agent.md '## Output Format' 섹션의 6-Section Report 형식으로 build_report.xml을 작성하세요.",
                    f"{build_report_file} 파일 생성 후 재실행",
                ],
                retry_allowed=True,
            )
            _save(state)
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
            _record_failure_packet(
                state,
                "build_report_read_error",
                {},
                failure_code="build_report_xml_read_error",
                failure_category="invalid_artifact",
                summary_ko=f"Build 실패 — build_report.xml 읽기 오류: {build_report_file}",
                expected="UTF-8/CP949/Latin-1 인코딩으로 읽기 성공",
                actual="모든 인코딩으로 읽기 실패",
                owner="Build",
                return_phase="build",
                required_actions=[
                    "build_report.xml 파일이 비어있지 않은지 확인하세요.",
                    "UTF-8로 재저장 후 재실행하세요.",
                ],
                retry_allowed=True,
            )
            _save(state)
            print(RED(f"\n[BUILD REPORT GATE] build_report.xml 읽기 실패: {build_report_file}\n"))
            sys.exit(1)
        xml_ok, xml_msg = _verify_build_report_xml(build_report_text)
        if not xml_ok:
            _record_failure_packet(
                state,
                "build_report_xml_invalid",
                {},
                failure_code="build_report_xml_invalid_structure",
                failure_category="invalid_artifact",
                summary_ko=f"Build 실패 — build_report.xml 6-Section 검증 실패: {xml_msg}",
                expected="6개 섹션(section_1~section_6) + <status>BUILD SUCCESS</status>",
                actual=xml_msg,
                owner="Build",
                return_phase="build",
                required_actions=[
                    "build_report.xml에 6개 섹션 XML 태그를 추가하세요 (build-agent.md '## Output Format' 참조).",
                    "XML comment(<!-- -->)로 감싼 섹션은 유효하지 않으므로 실제 태그로 교체하세요.",
                ],
                retry_allowed=True,
            )
            _save(state)
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
            _record_failure_packet(
                state,
                "build_na_skip_reason_invalid",
                {},
                failure_code="build_na_invalid_skip_reason",
                failure_category="invalid_argument",
                summary_ko=(
                    f"Build N/A 거부 — skip-reason이 whitelist에 없거나 길이 < 5: '{reason}'"
                ),
                expected=f"허용 목록 중 하나: {sorted(SKIP_REASON_WHITELIST)}",
                actual=f"제공된 skip-reason: '{reason}'",
                owner="Build",
                return_phase="build",
                required_actions=[
                    f"--skip-reason을 허용 목록 중 하나로 변경하세요: {sorted(SKIP_REASON_WHITELIST)}",
                    "예: --skip-reason \"meta-task\" 또는 --skip-reason \"streamlit\"",
                ],
                retry_allowed=True,
            )
            _save(state)
            _die(
                "\n[BUILD N/A GATE] --exe \"N/A\" 기록 거부 — --skip-reason이 whitelist에 없거나 길이 < 5.\n"
                f"  허용 목록(대소문자 무관): {sorted(SKIP_REASON_WHITELIST)}\n"
                "  예: --skip-reason \"meta-task\", --skip-reason \"streamlit\""
            )
        skip_reason = reason
        print(YELLOW("  [BUILD REPORT GATE] N/A 빌드 — build_report.xml 검증 생략, 최종 ACCEPT 보고서에 사유 기록"))

    agent_run = _validate_agent_run_for_phase(
        state,
        "build",
        getattr(args, "agent_run_id", None),
        build_report_file,
    )

    state["phases"]["build"]["status"]       = "DONE"
    state["phases"]["build"]["completed_at"] = _now()
    state["phases"]["build"]["evidence"]     = exe
    state["phases"]["build"]["report_file"]  = build_report_file if not is_na_build else None
    state["phases"]["build"]["skip_reason"]  = skip_reason if is_na_build else None
    if agent_run:
        state["phases"]["build"]["agent_run_id"] = agent_run["run_id"]
        state["phases"]["build"]["agent_id"] = agent_run["agent_id"]
    state["current_phase"] = "harness"
    _log_event(state, f"build DONE exe={exe}" + (f" skip_reason={skip_reason}" if is_na_build else ""))
    _record_snapshot(state, "build", branch)
    _save_state_for(state, branch)

    print(GREEN(f"\n[BUILD DONE] EXE: {exe or '경로 미지정'}"))
    print()
    print(BOLD(YELLOW("  ★ Phase 7 External Gates 실행 의무 -생략 불가")))
    print("  다음 절차:")
    print("    1. Build evidence commit/push 후 GitHub Actions phase attestation 확인:")
    print(f"       {YELLOW('python pipeline.py gates phase-ci --phase build --repo hojiyong2-commits/Pipeline')}")
    print("    2. test-harness-agent는 진단만 수행하고, 아래 external gates를 기록:")
    print(f"       {YELLOW('python pipeline.py gates technical')}")
    print(f"       {YELLOW('python pipeline.py gates oracle')}")
    print(f"       {YELLOW('python pipeline.py gates github-ci --repo hojiyong2-commits/Pipeline')}")
    print(f"       {YELLOW('python pipeline.py gates request-accept --evidence [결과물-경로]')}  ← 1단계: 사용자에게 코드 표시")
    print(f"       {YELLOW('python pipeline.py gates accept --result ACCEPT --evidence [경로] --acceptance-code ACCEPT-<pid>-<nonce>')}  ← 2단계: 사용자 코드 입력 후")
    print()


def cmd_harness(args: argparse.Namespace) -> None:
    """Reject the removed legacy harness score path.

    Harness helpers such as validate_test_evidence() remain available for unit tests and
    diagnostics, but the CLI command no longer mutates pipeline_state.json. Completion is
    owned by external gates only.
    """
    _load_branch_state(args)
    _die(
        "\n[THREE GATE BLOCKED] `pipeline.py harness --score`는 현재 필수 파이프라인의 완료 경로가 아닙니다 (not a completion path).\n"
        "  대신 아래 외부 게이트를 순서대로 사용하세요:\n"
        "       python pipeline.py gates technical\n"
        "       python pipeline.py gates oracle\n"
        "       python pipeline.py gates github-ci --repo hojiyong2-commits/Pipeline\n"
        "       python pipeline.py gates request-accept --evidence [결과물-경로]  (1단계: 사용자에게 코드 표시)\n"
        "       python pipeline.py gates accept --result ACCEPT --evidence [경로] --acceptance-code ACCEPT-<pid>-<nonce>  (2단계: 사용자 코드 입력 후)"
    )


def _verify_phase_attestation_consistency(state: Dict[str, Any]) -> List[str]:
    """phase_ci 결과 파일의 run_id/commit_sha와 pipeline_state 기록 불일치를 검사합니다.

    Returns:
        불일치 항목 목록 (비어 있으면 일치)
    """
    mismatches: List[str] = []
    pid = str(state.get("pipeline_id", ""))
    paths = _contract_paths(pid)
    phase_ci_root = paths.get("phase_ci_root")
    if not phase_ci_root:
        return mismatches

    phase_attestations = state.get("phase_attestations", {})
    phases_data = phase_attestations.get("phases", {})

    for phase in ("pm", "dev", "qa", "build"):
        result_file = Path(str(phase_ci_root)) / f"{phase}_result.json"
        if not result_file.exists():
            continue  # phase CI 결과 없으면 검사 생략

        try:
            result_json = json.loads(result_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        ci_run_id = str(result_json.get("run_id") or "")
        ci_commit = str(result_json.get("head_sha") or result_json.get("commit_sha") or "")

        phase_state = phases_data.get(phase, {})
        recorded_run_id = str(phase_state.get("phase_ci_run_id") or "")
        recorded_commit = str(phase_state.get("phase_ci_commit_sha") or "")

        if ci_run_id and recorded_run_id and ci_run_id != recorded_run_id:
            mismatches.append(
                f"phase={phase}: ci_run_id={ci_run_id} != state_run_id={recorded_run_id}"
            )
        if ci_commit and recorded_commit and ci_commit != recorded_commit:
            mismatches.append(
                f"phase={phase}: ci_commit={ci_commit} != state_commit={recorded_commit}"
            )

    return mismatches


def _inject_phase_attestation_facts(report_path: str, state: Dict[str, Any]) -> None:
    """architect_report.xml에 phase attestation 사실(run_id, commit_sha)을 자동으로 주입합니다."""
    try:
        path = Path(report_path)
        if not path.is_absolute():
            path = BASE_DIR / path
        if not path.exists():
            return

        content = path.read_text(encoding="utf-8")

        phase_attestations = state.get("phase_attestations", {})
        phases_data = phase_attestations.get("phases", {})

        facts_lines = ["<phase_attestation_facts>"]
        for phase in ("pm", "dev", "qa", "build"):
            phase_state = phases_data.get(phase, {})
            run_id = phase_state.get("phase_ci_run_id", "")
            commit = phase_state.get("phase_ci_commit_sha", "")
            pr_number = phase_state.get("phase_ci_pr_number", "")
            facts_lines.append(f"  <phase name=\"{phase}\">")
            facts_lines.append(f"    <phase_ci_run_id>{run_id}</phase_ci_run_id>")
            facts_lines.append(f"    <phase_ci_commit_sha>{commit}</phase_ci_commit_sha>")
            facts_lines.append(f"    <phase_ci_pr_number>{pr_number}</phase_ci_pr_number>")
            facts_lines.append("  </phase>")
        facts_lines.append("</phase_attestation_facts>")
        facts_block = "\n".join(facts_lines)

        if "<phase_attestation_facts>" in content:
            # 기존 블록 갱신
            import re as _re
            content = _re.sub(
                r"<phase_attestation_facts>.*?</phase_attestation_facts>",
                facts_block,
                content,
                flags=_re.DOTALL,
            )
        else:
            # XML 닫는 태그 직전에 추가
            last_close = content.rfind("</")
            if last_close > 0:
                close_end = content.find(">", last_close) + 1
                content = content[:close_end - len(content[last_close:close_end])] + "\n" + facts_block + "\n" + content[last_close:]
            else:
                content = content.rstrip() + "\n" + facts_block + "\n"

        path.write_text(content, encoding="utf-8")
    except Exception:
        pass  # 주입 실패는 architect 진행을 막지 않음


def cmd_architect(args: argparse.Namespace) -> None:
    """Architect RCA 완료 기록."""
    branch: Optional[str] = getattr(args, "branch", None)
    state = _load_branch_state(args)
    ok, reason = check_gate(state, "architect")
    if not ok:
        _die(f"[GATE BLOCKED] {reason}")

    # MT-3: phase attestation run_id/commit_sha 일관성 검증
    mismatches = _verify_phase_attestation_consistency(state)
    if mismatches:
        _die(
            "[ARCHITECT BLOCKED] phase attestation run_id/commit_sha 불일치:\n  "
            + "\n  ".join(mismatches)
        )

    protocol_decision = _parse_protocol_evolution_decision(getattr(args, "report_file", None))
    harness_verdict = state["phases"]["harness"].get("status", "PENDING")
    rca_mode = str(protocol_decision.get("rca_mode") or "").upper()
    if harness_verdict == "PASS" and "FAIL" in rca_mode:
        _die(
            "[ARCHITECT REPORT GATE] external gates are PASS but architect report rca_mode is fail-oriented. "
            "Use a completion/retrospective mode for PASS, or record a real external gate FAIL first."
        )

    state["phases"]["architect"]["status"]       = "DONE"
    state["phases"]["architect"]["completed_at"] = _now()
    state["phases"]["architect"]["report_file"]  = protocol_decision["report_file"]
    state["protocol_evolution_decision"] = protocol_decision

    # MT-3: architect_report.xml에 phase attestation 사실 자동 주입
    if protocol_decision.get("report_file"):
        _inject_phase_attestation_facts(str(protocol_decision["report_file"]), state)

    _record_snapshot(state, "architect", branch)

    if harness_verdict == "FAIL":
        # External gate FAIL path: Architect RCA 완료 후 Phase 2 재작업으로 루프백
        state["current_phase"] = "dev"
        state["phases"]["dev"]["status"] = "PENDING"
        state["terminal_state"] = None
        _log_event(state, "architect DONE — external gate FAIL path: dev PENDING reset for rework")
        _save_state_for(state, branch)
    else:
        external_blockers = _external_gate_blockers(state)
        if external_blockers:
            _die(
                "[THREE GATE BLOCKED] COMPLETE requires external gates and advisory resolution: "
                + "; ".join(external_blockers)
            )
        # External gate PASS path: 파이프라인 정상 완료
        # IMP-20260522-29C1 MT-1: 파이프라인 종료 시점과 전체 소요 시간 기록.
        state["pipeline_completed_at"] = _now()
        _ts_start = state.get("pipeline_started_at") or state.get("created_at")
        if _ts_start:
            try:
                from datetime import datetime, timezone
                _fmt = "%Y-%m-%dT%H:%M:%SZ"
                _t0 = datetime.strptime(_ts_start, _fmt).replace(tzinfo=timezone.utc)
                _t1 = datetime.strptime(
                    state["pipeline_completed_at"], _fmt
                ).replace(tzinfo=timezone.utc)
                state["total_elapsed_seconds"] = int((_t1 - _t0).total_seconds())
            except (ValueError, TypeError):
                state["total_elapsed_seconds"] = None
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
        print(YELLOW("  External gate FAIL 경로: Phase 2 (Dev) 재작업 필요"))
        print("\n  다음 단계: " + YELLOW('python pipeline.py done --phase dev --files ".." --report-file dev_handover.xml --scope-declared --scope-manifest scope_manifest.json --agent-run-id <dev_run_id>'))
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
    """현재 파이프라인 상태 출력 (BUG-20260527-5CF4: 항상 exit 0 보장).

    설계 원칙:
    - state 누락/손상/필드 부분 누락 등 모든 비정상 상황에서도 unhandled exception 없이
      안내 메시지를 출력하고 정상 반환한다. argparse subcommand 함수가 정상 반환하면
      Python interpreter는 exit code 0으로 종료한다.
    - terminal_state(COMPLETE/FAILED/TERMINATED/None), blocked=true, phases 키 부분 누락
      모든 케이스를 모두 통과해야 한다.
    - 각 출력 블록은 독립적으로 try/except를 가지며 한 블록 실패가 다른 블록을 차단하지
      않는다.
    """
    try:
        state = _load()
    except SystemExit:
        # _die() → sys.exit(1)이 호출된 경우. status는 항상 0이어야 하므로 안내만.
        print(YELLOW("\n  상태 로드 실패 — pipeline_state.json을 확인하세요.\n"))
        return
    except Exception as exc:
        print(YELLOW(f"\n  상태 로드 중 오류: {exc}\n"))
        return

    if state is None:
        print(YELLOW("\n  활성 파이프라인 없음. `python pipeline.py new` 로 시작하세요.\n"))
        return

    try:
        state = _ensure_v210_fields(state)
    except Exception as exc:
        print(YELLOW(f"\n  state 마이그레이션 중 오류: {exc}\n"))
        # 마이그레이션 실패해도 가능한 만큼 출력
    if not isinstance(state, dict):
        print(YELLOW("\n  상태 파일 구조가 예상과 다릅니다 (dict 아님).\n"))
        return

    pid         = state.get("pipeline_id", "UNKNOWN")
    description = state.get("description", "")
    current     = state.get("current_phase", "UNKNOWN")
    blocked     = state.get("blocked", False)
    terminal    = state.get("terminal_state")

    try:
        print()
        print(BOLD(f"  파이프라인: {CYAN(pid)}"))
        print(f"  설명: {description}")
        print(f"  생성: {state.get('created_at', '')}  갱신: {state.get('updated_at', '')}")
    except Exception as exc:
        print(DIM(f"    [헤더 출력 오류: {exc}]"))

    try:
        profile = _execution_profile(state)
        profile_mode = str(profile.get("mode") or "STANDARD")
        fast_label = "빠른 경로" if profile_mode in FAST_EXECUTION_PROFILES else "표준 경로"
        print(f"  실행 프로필: {profile_mode} ({fast_label})")
    except Exception as exc:
        print(DIM(f"    [실행 프로필 조회 오류: {exc}]"))

    if blocked:
        try:
            print(RED(f"  [차단] {state.get('blocked_reason', '')}"))
        except Exception:
            print(RED("  [차단]"))
    # v2.10 Auto-Compact: 종료 상태 표시 (Stop hook이 이 필드를 읽음)
    if terminal:
        print(YELLOW(f"  [종료 상태] terminal_state={terminal}"))
    print()
    print(BOLD("  Phase 현황:"))
    print()

    phases_dict = state.get("phases") if isinstance(state.get("phases"), dict) else {}
    for phase in PHASE_ORDER:
        try:
            info = phases_dict.get(phase) if isinstance(phases_dict, dict) else None
            if not isinstance(info, dict):
                # 누락된 phase는 PENDING으로 표시
                info = {"status": "PENDING", "evidence": "", "completed_at": ""}
            status  = info.get("status", "PENDING")
            label   = PHASE_LABELS.get(phase, phase)
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

            completed_at = info.get("completed_at") or ""
            ts = f"  {completed_at[:16] if completed_at else ''}"
            print(f"    {icon} {color(label):<42} [{color(status):<8}]{ts}")
            if ev:
                print(DIM(f"        증거: {ev}"))
        except Exception as exc:
            print(DIM(f"    [phase={phase} 표시 오류: {exc}]"))

    try:
        gates = state.get("external_gates", {})
        if isinstance(gates, dict) and gates.get("enabled"):
            print()
            print(BOLD("  External Gate 현황:"))
            for gate_name in ("technical", "oracle", "acceptance", "github_ci"):
                gate = gates.get(gate_name, {})
                if not isinstance(gate, dict):
                    continue
                gstatus = str(gate.get("status", "PENDING"))
                gcolor = GREEN if gstatus == "PASS" else RED if gstatus == "FAIL" else YELLOW
                print(f"    {gcolor(gate_name):<16} [{gcolor(gstatus):<8}] {gate.get('completed_at') or ''}")
                if gate.get("evidence"):
                    print(DIM(f"        증거: {gate.get('evidence')}"))
            try:
                blockers = _external_gate_blockers(state)
            except Exception:
                blockers = []
            if blockers and terminal != "COMPLETE":
                print(RED("    blockers: " + "; ".join(blockers)))
    except Exception as exc:
        print(DIM(f"    [External Gate 출력 오류: {exc}]"))

    try:
        outputs = _ensure_output_registry(state)
        if outputs.get("items"):
            print()
            print(BOLD("  사용자가 확인할 결과물:"))
            for item in outputs.get("items", [])[:10]:
                if not isinstance(item, dict):
                    continue
                label = item.get("label") or item.get("kind") or "output"
                public_path = item.get("public_path") or item.get("source_path")
                print(f"    - {label}: {public_path}")
    except Exception as exc:
        print(DIM(f"    [출력 등록부 표시 오류: {exc}]"))

    try:
        failures = state.get("failure_packets")
        if isinstance(failures, list) and failures:
            print()
            print(BOLD("  최근 실패 패킷:"))
            for item in failures[-3:]:
                if not isinstance(item, dict):
                    continue
                print(f"    - {item.get('gate')} -> {item.get('repair_owner')} ({item.get('packet_path')})")
    except Exception as exc:
        print(DIM(f"    [실패 패킷 표시 오류: {exc}]"))

    try:
        advisory = _advisory_status_summary(str(pid))
        print()
        print(BOLD("  GPT Advisory status:"))
        mode = str(advisory.get("advisory_mode") or "not_run")
        mode_reason = str(advisory.get("advisory_mode_reason") or "")
        mode_label_map = {
            "not_run": "NOT RUN — disabled by default",
            "skipped": "SKIPPED",
            "required": "REQUIRED",
            "blocking": "BLOCKING",
        }
        mode_color = {
            "not_run": YELLOW,
            "skipped": DIM,
            "required": GREEN,
            "blocking": RED,
        }.get(mode, YELLOW)
        print(mode_color(f"    mode={mode_label_map.get(mode, mode.upper())}"))
        if mode_reason:
            print(DIM(f"    reason: {mode_reason}"))
        if advisory.get("review_count", 0) == 0:
            print(DIM("    NOT RUN — no advisory review files recorded"))
        else:
            status_counts = advisory.get("status_counts", {}) or {}
            status_bits = ", ".join(f"{k}={v}" for k, v in sorted(status_counts.items()))
            print(f"    reviews={advisory.get('review_count', 0)} api_calls={advisory.get('api_call_count', 0)} statuses={status_bits}")
            print(f"    unresolved_critical={advisory.get('unresolved_critical_count', 0)}")
        if not advisory.get("api_key_present"):
            print(DIM("    OPENAI_API_KEY not set; GPT advisory cannot call OpenAI."))
        elif not advisory.get("enabled"):
            print(DIM("    ENABLE_GPT_ADVISORY is not 1; GPT advisory API calls are disabled."))
        elif advisory.get("required"):
            print(DIM(
                f"    GPT advisory model fixed to {OPENAI_ADVISORY_MODEL}; "
                f"REQUIRED mode — auto-calls enabled (key source: {advisory.get('api_key_source', 'unknown')})."
            ))
        else:
            print(DIM(
                f"    GPT advisory model fixed to {OPENAI_ADVISORY_MODEL}; "
                f"API calls allowed manually (key source: {advisory.get('api_key_source', 'unknown')}). "
                f"Auto-run is disabled because ENABLE_GPT_ADVISORY_REQUIRED is not 1."
            ))
    except Exception as exc:
        print(DIM(f"    [Advisory 상태 표시 오류: {exc}]"))

    try:
        print()
        if current == "COMPLETE":
            print(GREEN("  ✓ 파이프라인 완료\n"))
        else:
            try:
                gate_ok, reason = check_gate(state, current) if current in GATE_RULES else (True, "")
            except Exception as exc:
                gate_ok, reason = True, ""
                print(DIM(f"    [게이트 체크 오류: {exc}]"))
            if gate_ok:
                print(f"  현재 단계: {YELLOW(PHASE_LABELS.get(current, current))}")
                if current == "harness":
                    print(f"  확인 명령: {YELLOW('python pipeline.py gates status')}")
                    print(f"  다음 게이트: {YELLOW('technical -> oracle -> github-ci -> accept')}")
                else:
                    print(f"  확인 명령: {YELLOW(f'python pipeline.py check --phase {current}')}")
            else:
                print(RED(f"  [차단] {reason}"))
            print()
    except Exception as exc:
        print(DIM(f"    [현재 단계 안내 오류: {exc}]"))

    # Recent log
    try:
        log = state.get("event_log", [])
        if isinstance(log, list):
            log = log[-5:]
        else:
            log = []
        if log:
            print(DIM("  최근 이벤트:"))
            for entry in log:
                if not isinstance(entry, dict):
                    continue
                ts = str(entry.get("ts", ""))[:16]
                msg = entry.get("msg", "")
                print(DIM(f"    {ts}  {msg}"))
            print()
    except Exception as exc:
        print(DIM(f"    [최근 이벤트 표시 오류: {exc}]"))


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
    _log_event(state, "파이프라인 명시적 종료 (사용자 terminate 명령)")

    # 보관
    HISTORY_DIR.mkdir(exist_ok=True)
    archive = HISTORY_DIR / f"{pid}_TERMINATED_{_now().replace(':', '-')}.json"
    archive.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    _save(state)
    print(RED(f"\n[TERMINATED] 파이프라인 {pid} 종료됨"))
    print(f"  보관: {archive.name}")
    print("  새 파이프라인 시작: " + YELLOW('python pipeline.py new --type FEAT|BUG|IMP --desc ".."'))
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
            "agent": "pm-planner-agent + pipeline-manager-agent",
            "next_cmd": 'python pipeline.py done --phase pm --report-file step_plan.xml --decomp --clarification --roadmap --planner-run-id <planner_run_id> --manager-run-id <manager_run_id> --manager-report manager_handoff.xml [--judgment-confirmed]',
            "required_xml": ["<decomposition_audit>", "<step_plan>", "<design_confirmation>", "<micro_tasks>", "<manager_handoff>"],
        },
        "dev": {
            "agent": "dev-agent",
            "next_cmd": 'python pipeline.py done --phase dev --files "core/x.py,ui/app.py" --report-file dev_handover.xml --scope-declared --scope-manifest scope_manifest.json --agent-run-id <dev_run_id>',
            "required_xml": ["<scope_declaration>", "<impact_analysis>", "<handover>"],
        },
        "qa": {
            "agent": "qa-agent",
            "next_cmd": (
                'PASS: python pipeline.py qa --result PASS --numeric-score N --report-file qa_report.xml --agent-run-id <qa_run_id>\n'
                '        FAIL: python pipeline.py qa --result FAIL --numeric-score N --failure-sig "[category]:[hash]" --report-file qa_report.xml --agent-run-id <qa_run_id>'
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
            "next_cmd": 'python pipeline.py build --exe "dist/app.exe" --report-file dist/build_report.xml --agent-run-id <build_run_id>  (N/A: --exe "N/A" --skip-reason "meta-task" --agent-run-id <build_run_id>)',
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
            "next_cmd": (
                'External gates only: python pipeline.py gates technical; '
                'python pipeline.py gates oracle; '
                'python pipeline.py gates github-ci --repo hojiyong2-commits/Pipeline; '
                'python pipeline.py gates request-accept --evidence PATH; '
                'python pipeline.py gates accept --result ACCEPT --evidence PATH --acceptance-code ACCEPT-<pid>-<nonce>'
            ),
            "required_xml": ["<harness_diagnostic>"],
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
ORACLE_STORAGE_ROOT_REL = Path("tests") / "oracles"
NON_ORACLE_DELIVERABLE_KINDS = {"doc", "docs", "markdown", "prompt", "analysis", "research", "policy", "config", "configuration"}

# IMP-20260524-48C4 MT-1: Oracle Quality Gate 상수
# oracle 단순 존재 여부 PASS 차단 — quality 기준 강제
ORACLE_QUALITY_PLACEHOLDER_STRINGS: Set[str] = {"TODO", "PLACEHOLDER", "TBD", "N/A", "", "todo", "placeholder", "tbd", "n/a"}
ORACLE_QUALITY_SHALLOW_TEST_TYPES: Set[str] = {"file_exists_check", "exe_launch_check", "process_check", "output_not_empty"}
ORACLE_QUALITY_MIN_NORMAL: int = 1
ORACLE_QUALITY_MIN_EDGE_ERROR: int = 1
ORACLE_QUALITY_EXPECTED_SOURCE_ALLOWED: Set[str] = {"user_provided", "production_sample", "regression_capture", "user"}

EXECUTION_PROFILES = {"FAST_DOC", "FAST_ANALYSIS", "FAST_SINGLE_CODE", "STANDARD", "HIGH_RISK"}
FAST_EXECUTION_PROFILES = {"FAST_DOC", "FAST_ANALYSIS", "FAST_SINGLE_CODE"}
FAST_PROFILE_MAX_FILES = 2
FAST_PROFILE_MAX_FUNCTIONS = 2
FAST_PROFILE_MAX_LINES = 80
PRODUCT_CODE_EXTENSIONS = {
    ".py", ".pyw", ".js", ".jsx", ".ts", ".tsx", ".java", ".cs", ".go", ".rs",
    ".cpp", ".cc", ".c", ".h", ".hpp", ".php", ".rb", ".swift", ".kt", ".kts",
    ".ps1", ".sh", ".bat", ".cmd",
}
OUTPUTS_ROOT = BASE_DIR / "pipeline_outputs"


def _bool_xml_text(parent: ET.Element, name: str, default: bool = False) -> bool:
    raw = _child_text(parent, name, "true" if default else "false").strip().lower()
    if raw in {"true", "1", "yes", "y"}:
        return True
    if raw in {"false", "0", "no", "n", ""}:
        return False
    _die(f"[EXECUTION PROFILE GATE] <{name}> must be true or false")


def _int_xml_text(parent: ET.Element, name: str, default: int = 0) -> int:
    raw = _child_text(parent, name, str(default)).strip()
    try:
        return int(raw)
    except ValueError:
        _die(f"[EXECUTION PROFILE GATE] <{name}> must be an integer")


def _is_product_code_path(raw: str) -> bool:
    rel = _normalize_rel_path(raw)
    path = Path(rel)
    if path.suffix.lower() not in PRODUCT_CODE_EXTENSIONS:
        return False
    parts = set(path.parts)
    if "tests" in parts or path.name.startswith("test_") or path.name.endswith("_test.py"):
        return False
    return True


def _parse_task_complexity(step_plan: ET.Element, micro_tasks: List[Dict[str, Any]]) -> Dict[str, Any]:
    complexity = step_plan.find("task_complexity")
    if complexity is None:
        _die("[EXECUTION PROFILE GATE] <task_complexity> is required inside <step_plan>")

    mode = _child_text(complexity, "execution_profile", "STANDARD").strip().upper()
    if mode not in EXECUTION_PROFILES:
        _die(f"[EXECUTION PROFILE GATE] execution_profile must be one of {sorted(EXECUTION_PROFILES)}")

    profile = _new_execution_profile(mode)
    profile["declared_at"] = _now()
    profile["reason"] = _child_text(complexity, "reason").strip()
    uncertainty = complexity.find("uncertainty")
    if uncertainty is None:
        uncertainty = ET.Element("uncertainty")
    blast_radius = complexity.find("blast_radius")
    if blast_radius is None:
        blast_radius = ET.Element("blast_radius")
    risk_flags = complexity.find("risk_flags")
    if risk_flags is None:
        risk_flags = ET.Element("risk_flags")
    profile["uncertainty"] = {
        "p0_questions": _int_xml_text(uncertainty, "p0_questions", 0),
        "p1_questions": _int_xml_text(uncertainty, "p1_questions", 0),
        "output_format_clear": _bool_xml_text(uncertainty, "output_format_clear", mode not in FAST_EXECUTION_PROFILES),
    }
    profile["blast_radius"] = {
        "expected_changed_files": _int_xml_text(blast_radius, "expected_changed_files", len({p for task in micro_tasks for p in task.get("target_files", [])})),
        "expected_changed_functions": _int_xml_text(blast_radius, "expected_changed_functions", len({str(task.get("affected_function")) for task in micro_tasks})),
        "expected_changed_lines": _int_xml_text(blast_radius, "expected_changed_lines", 0),
    }
    risk_names = [
        "data_deletion", "file_move", "external_api", "auth_or_secret", "pipeline_protocol",
        "build_or_deploy", "core_parser_logic", "database_or_migration", "new_dependency",
    ]
    profile["risk_flags"] = {name: _bool_xml_text(risk_flags, name, False) for name in risk_names}

    if mode in FAST_EXECUTION_PROFILES:
        blockers: List[str] = []
        profile["max_micro_tasks"] = 1
        profile["phase_ci_mode"] = "batched"
        profile["repair_mode"] = "targeted"
        if mode in {"FAST_DOC", "FAST_ANALYSIS"}:
            profile["product_code_write_allowed"] = False
        if not profile["reason"]:
            blockers.append("fast profile requires <reason>")
        if len(micro_tasks) != 1:
            blockers.append("fast profile requires exactly one <micro_task>")
        if profile["uncertainty"]["p0_questions"] != 0:
            blockers.append("fast profile requires p0_questions=0")
        if profile["uncertainty"]["p1_questions"] > 2:
            blockers.append("fast profile allows at most 2 P1 questions")
        if not profile["uncertainty"]["output_format_clear"]:
            blockers.append("fast profile requires output_format_clear=true")
        if profile["blast_radius"]["expected_changed_files"] > FAST_PROFILE_MAX_FILES:
            blockers.append(f"fast profile allows expected_changed_files <= {FAST_PROFILE_MAX_FILES}")
        if profile["blast_radius"]["expected_changed_functions"] > FAST_PROFILE_MAX_FUNCTIONS:
            blockers.append(f"fast profile allows expected_changed_functions <= {FAST_PROFILE_MAX_FUNCTIONS}")
        if profile["blast_radius"]["expected_changed_lines"] > FAST_PROFILE_MAX_LINES:
            blockers.append(f"fast profile allows expected_changed_lines <= {FAST_PROFILE_MAX_LINES}")
        risky = [name for name, enabled in profile["risk_flags"].items() if enabled]
        if risky:
            blockers.append("fast profile cannot set risk flags: " + ", ".join(sorted(risky)))
        if mode in {"FAST_DOC", "FAST_ANALYSIS"}:
            product_targets = sorted({
                path for task in micro_tasks for path in task.get("target_files", [])
                if _is_product_code_path(str(path))
            })
            if product_targets:
                blockers.append(f"{mode} cannot target product code files: {product_targets}")
        if blockers:
            _die("[EXECUTION PROFILE GATE] " + "; ".join(blockers))
    elif mode == "HIGH_RISK":
        blockers = []
        profile["phase_ci_mode"] = "per_phase"
        profile["repair_mode"] = "conservative"
        profile["risk_review_required"] = True
        if not profile["reason"]:
            blockers.append("HIGH_RISK requires <reason>")
        risky = [name for name, enabled in profile["risk_flags"].items() if enabled]
        if not risky:
            blockers.append("HIGH_RISK requires at least one risk flag")
        profile["risk_categories"] = sorted(risky)
        if blockers:
            _die("[EXECUTION PROFILE GATE] " + "; ".join(blockers))
    return profile


def _execution_profile(state: Dict[str, Any]) -> Dict[str, Any]:
    profile = state.get("execution_profile")
    return profile if isinstance(profile, dict) else _new_execution_profile("STANDARD")

def _product_code_write_allowed(state: Dict[str, Any]) -> bool:
    return bool(_execution_profile(state).get("product_code_write_allowed", True))


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


def _audit_oracle_quality(
    oracle_entries: List[Dict[str, Any]],
    allow_agent_generated: bool = False,
    state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """IMP-20260524-48C4 MT-1: Oracle Quality Gate 감사 함수.

    oracle 단순 존재 여부 PASS 차단 — 아래 품질 기준을 강제합니다:
    1. normal 케이스 >= ORACLE_QUALITY_MIN_NORMAL (1개)
    2. edge|error|exception|regression 케이스 >= ORACLE_QUALITY_MIN_EDGE_ERROR (1개)
    3. expected 파일이 빈 JSON {}, 빈 배열 [], 빈 파일이면 FAIL
    4. expected 파일에 ORACLE_QUALITY_PLACEHOLDER_STRINGS가 포함되면 FAIL
    5. 모든 oracle entry가 ORACLE_QUALITY_SHALLOW_TEST_TYPES만이면 FAIL
    6. expected_source == 'agent_generated' 이고 allow_agent_generated=False이면 BLOCKED
    7. expected_sha256 필드가 있으면 실제 파일 해시와 비교, 불일치시 FAIL

    Args:
        oracle_entries: oracle_manifest.json의 entries 배열 (normalized list)
        allow_agent_generated: agent_generated source를 허용할지 여부 (기본 False)

    Returns:
        {
            "status": "PASS" | "FAIL" | "BLOCKED",
            "failures": [...],
            "case_summary": {"normal": N, "edge": N, "error": N, "regression": N}
        }
    """
    failures: List[str] = []
    case_summary: Dict[str, int] = {"normal": 0, "edge": 0, "error": 0, "regression": 0}
    blocked: bool = False

    if not oracle_entries:
        return {
            "status": "FAIL",
            "failures": ["oracle_quality: no oracle entries to audit"],
            "case_summary": case_summary,
        }

    # case_kind 집계
    for entry in oracle_entries:
        kind = str(entry.get("case_kind") or "normal").strip().lower()
        if kind == "normal":
            case_summary["normal"] += 1
        elif kind in ("edge", "exception"):
            case_summary["edge"] += 1
        elif kind == "error":
            case_summary["error"] += 1
        elif kind == "regression":
            case_summary["regression"] += 1

    # 검사 1: normal 케이스 최소 1개
    if case_summary["normal"] < ORACLE_QUALITY_MIN_NORMAL:
        failures.append(
            "oracle_quality: edge_required — normal 케이스가 부족합니다 "
            f"(최소 {ORACLE_QUALITY_MIN_NORMAL}개 필요, 현재 {case_summary['normal']}개)"
        )

    # 검사 2: edge|error|exception|regression 최소 1개
    edge_total = case_summary["edge"] + case_summary["error"] + case_summary["regression"]
    if edge_total < ORACLE_QUALITY_MIN_EDGE_ERROR:
        failures.append(
            "oracle_quality: edge_required — edge|error|exception|regression 케이스가 부족합니다 "
            f"(최소 {ORACLE_QUALITY_MIN_EDGE_ERROR}개 필요, 현재 {edge_total}개)"
        )

    # 검사 3~6: 각 entry 상세 검사
    all_shallow = len(oracle_entries) > 0
    for entry in oracle_entries:
        name = str(entry.get("name") or entry.get("case_id") or "oracle")
        # oracle source: 매니페스트의 source 필드 (데이터 출처), expected_source (기대 출력 출처) 별도 확인
        data_source = str(entry.get("source") or "").strip().lower()
        expected_source = str(entry.get("expected_source") or "").strip().lower()
        # agent_generated 검사: expected_source가 명시적이면 그것을, 없으면 data_source를 사용
        source_for_quality_check = expected_source if expected_source else data_source
        test_type = str(entry.get("test_type") or entry.get("type") or "").strip().lower()
        expected_path_str = str(entry.get("expected_path") or "")
        expected_sha256 = str(entry.get("expected_sha256") or "").strip()

        # 검사 6: agent_generated BLOCKED
        if source_for_quality_check == "agent_generated" and not allow_agent_generated:
            blocked = True
            failures.append(
                f"oracle_quality: {name}: expected_source=agent_generated — "
                "에이전트 생성 expected는 기본 BLOCKED입니다. "
                "--allow-agent-generated 플래그로 해제하거나 user_provided로 교체하세요."
            )

        # IMP-20260602-1ABE MT-6: ac_ids 검증 (requirements_tracking.enabled=true만 적용)
        if state is not None and state.get("requirements_tracking", {}).get("enabled"):
            structured_ac = state.get("structured_acceptance_criteria") or []
            valid_ac_ids: set = {
                str(ac.get("ac_id", "")).strip()
                for ac in structured_ac
                if isinstance(ac, dict) and ac.get("ac_id")
            }
            if valid_ac_ids:
                entry_ac_ids = entry.get("ac_ids")
                if not entry_ac_ids:
                    failures.append(
                        f"[ORACLE AC GATE] oracle entry {name}: "
                        "requirements_tracking.enabled=true 파이프라인에서 ac_ids가 없습니다"
                    )
                elif not isinstance(entry_ac_ids, list):
                    failures.append(
                        f"[ORACLE AC GATE] oracle entry {name}: ac_ids는 리스트여야 합니다"
                    )
                else:
                    for ac_id in entry_ac_ids:
                        if str(ac_id).strip() not in valid_ac_ids:
                            failures.append(
                                f"[ORACLE AC GATE] oracle entry {name}: "
                                f"AC id '{ac_id}'는 PM에 없는 id입니다"
                            )

        # 검사 5: shallow test type 전용 여부
        if test_type and test_type not in ORACLE_QUALITY_SHALLOW_TEST_TYPES:
            all_shallow = False

        # 검사 3, 4: expected 파일 내용 검사
        if expected_path_str:
            expected_path = Path(expected_path_str)
            if not expected_path.is_absolute():
                expected_path = BASE_DIR / expected_path
            if expected_path.exists():
                # 검사 7: sha256 불일치
                if expected_sha256:
                    actual_sha = _sha256_file(expected_path)
                    if actual_sha != expected_sha256:
                        failures.append(
                            f"oracle_quality: {name}: expected_sha256 불일치 "
                            f"(저장={expected_sha256[:8]}..., 실제={actual_sha[:8]}...)"
                        )

                # 검사 3, 4: 파일 내용 품질
                if expected_path.suffix.lower() == ".json":
                    try:
                        value = json.loads(expected_path.read_text(encoding="utf-8-sig"))
                        if value in ({}, [], "", None):
                            failures.append(
                                f"oracle_quality: {name}: expected JSON은 빈 값입니다 ({value!r})"
                            )
                        elif isinstance(value, dict):
                            # 모든 값이 placeholder인지 검사
                            all_placeholder = all(
                                isinstance(v, str) and v.strip() in ORACLE_QUALITY_PLACEHOLDER_STRINGS
                                for v in value.values()
                            ) if value else False
                            if all_placeholder:
                                failures.append(
                                    f"oracle_quality: {name}: expected JSON의 모든 값이 placeholder입니다"
                                )
                        elif isinstance(value, str):
                            if value.strip() in ORACLE_QUALITY_PLACEHOLDER_STRINGS:
                                failures.append(
                                    f"oracle_quality: {name}: expected 값이 placeholder입니다 ({value!r})"
                                )
                    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                        pass  # 파일 내용 파싱 실패는 다른 게이트가 처리
                else:
                    # JSON이 아닌 파일: 전체 내용을 읽어 placeholder 검사
                    try:
                        text_content = expected_path.read_text(encoding="utf-8", errors="replace").strip()
                        if not text_content:
                            failures.append(
                                f"oracle_quality: {name}: expected 파일이 비어 있습니다"
                            )
                        elif text_content.upper() in {s.upper() for s in ORACLE_QUALITY_PLACEHOLDER_STRINGS if s}:
                            failures.append(
                                f"oracle_quality: {name}: expected 파일이 placeholder입니다 ({text_content[:20]!r})"
                            )
                    except (OSError, UnicodeDecodeError):
                        pass

    # 검사 5: 모든 entry가 shallow test type만인 경우
    if all_shallow and oracle_entries and not any(
        str(e.get("test_type") or e.get("type") or "").strip().lower()
        not in ORACLE_QUALITY_SHALLOW_TEST_TYPES
        for e in oracle_entries
        if str(e.get("test_type") or e.get("type") or "").strip()
    ):
        # test_type 필드가 있고 모두 shallow인 경우만 FAIL
        has_test_type = any(
            str(e.get("test_type") or e.get("type") or "").strip()
            for e in oracle_entries
        )
        if has_test_type:
            failures.append(
                "oracle_quality: shallow_only — 모든 oracle이 file_exists/exe_launch 등 shallow 검사만 "
                "포함합니다. 핵심 비즈니스 로직을 검증하는 oracle이 최소 1개 필요합니다."
            )

    if blocked:
        status = "BLOCKED"
    elif failures:
        status = "FAIL"
    else:
        status = "PASS"

    return {
        "status": status,
        "failures": failures,
        "case_summary": case_summary,
    }


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


def _oracle_storage_blockers(name: str, input_path: Path, expected_path: Path) -> List[str]:
    blockers: List[str] = []
    root = (BASE_DIR / ORACLE_STORAGE_ROOT_REL).resolve()
    for label, path in (("input", input_path), ("expected", expected_path)):
        try:
            path.resolve().relative_to(root)
        except ValueError:
            blockers.append(
                f"{name}: oracle {label}_path must be under "
                f"{ORACLE_STORAGE_ROOT_REL.as_posix()}/** so GitHub CODEOWNERS can protect the answer key"
            )
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
        blockers.extend(_oracle_storage_blockers(name, input_path, expected_path))
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
            "ac_ids": entry.get("ac_ids", []),   # IMP-20260602-1ABE: ac_ids 검증을 위해 보존
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

    # IMP-20260524-48C4 MT-1: oracle quality 감사 통합 (contract audit 단계)
    # oracle 단순 존재 여부 PASS 차단 — normal+edge 최소 케이스, placeholder, agent_generated 검사
    if oracle_entries and not oracle_blockers:
        # IMP-20260602-1ABE MT-6: state는 _audit_contract_bundle 컨텍스트에서 사용 불가 — None 전달
        quality_result = _audit_oracle_quality(oracle_entries, state=None)
        if quality_result.get("status") == "BLOCKED":
            blockers.append("oracle_quality: BLOCKED — agent_generated expected 감지. --allow-agent-generated 또는 user_provided로 교체하세요.")
            blockers.extend(quality_result.get("failures", []))
        elif quality_result.get("status") == "FAIL":
            blockers.extend(quality_result.get("failures", []))

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
            state["external_gates"] = _new_external_gates(enabled=True)
            _enable_phase_attestations(state)
            state["module_gates"] = _ensure_module_gates(state)
            _log_event(state, "three_gate mode enabled (mandatory)")
            _log_event(state, "GitHub phase attestations enabled (mandatory)")
            _log_event(state, f"contract_v2 initialized: {paths['root']}")
            _save(state)
        print(GREEN(f"\n[CONTRACT INIT] {pid}"))
        print(f"  contract: {paths['contract']}")
        print(f"  test_set: {paths['test_set']}\n")
        return

    if action in {"add-module", "add-question", "answer", "add-test", "add-oracle", "audit", "ready", "freeze", "show"}:
        contract, test_set = _load_contract_bundle(load_json, paths, pid, action)
    elif action == "audit-oracle":
        # audit-oracle: --oracle-dir 지정 시 contract 파일 로딩 생략 가능
        oracle_dir_early = getattr(args, "oracle_dir", None)
        if oracle_dir_early is not None:
            contract, test_set = {}, {}
        else:
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
        storage_blockers = _oracle_storage_blockers(name, input_path, expected_path)
        if storage_blockers:
            _die(
                "; ".join(storage_blockers)
                + f". Move user-provided oracle files to {ORACLE_STORAGE_ROOT_REL.as_posix()}/{pid}/{name}/ and retry."
            )
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

    if action == "audit-oracle":
        # IMP-20260524-48C4: oracle quality 전용 감사 커맨드
        allow_agent_gen = bool(getattr(args, "allow_agent_generated", False))
        oracle_dir_arg = getattr(args, "oracle_dir", None)

        if oracle_dir_arg is not None:
            # --oracle-dir 지정 시: TC 디렉토리의 input.json에서 oracle entries 로드
            oracle_dir_path = Path(oracle_dir_arg)
            if not oracle_dir_path.is_absolute():
                oracle_dir_path = BASE_DIR / oracle_dir_path
            input_json_path = oracle_dir_path / "input.json"
            if not input_json_path.exists():
                print(RED(f"\n[CONTRACT AUDIT-ORACLE FAIL] {pid}"))
                print(RED(f"  FAIL: input.json not found in oracle-dir: {oracle_dir_path}"))
                sys.exit(1)
            try:
                tc_input = load_json(input_json_path)
            except Exception as exc:
                print(RED(f"\n[CONTRACT AUDIT-ORACLE FAIL] {pid}"))
                print(RED(f"  FAIL: cannot load input.json: {exc}"))
                sys.exit(1)
            oracle_entries = tc_input.get("oracles") or tc_input.get("entries") or []
            # TC input.json의 allow_agent_generated 필드도 참조 (args flag가 없으면)
            if not allow_agent_gen:
                allow_agent_gen = bool(tc_input.get("allow_agent_generated", False))
        else:
            # 기본: pipeline oracle_manifest.json 사용
            oracle_manifest_path = paths["oracle_manifest"]
            if not oracle_manifest_path.exists():
                print(RED(f"\n[CONTRACT AUDIT-ORACLE FAIL] {pid}"))
                print(RED("  FAIL: oracle_manifest.json not found"))
                sys.exit(1)
            try:
                oracle_manifest = load_json(oracle_manifest_path)
            except Exception as exc:
                print(RED(f"\n[CONTRACT AUDIT-ORACLE FAIL] {pid}"))
                print(RED(f"  FAIL: cannot load oracle_manifest.json: {exc}"))
                sys.exit(1)
            oracle_entries = oracle_manifest.get("entries") or oracle_manifest.get("oracles") or []

        # IMP-20260602-1ABE MT-6: state 전달로 ac_ids 검증 활성화
        quality_result = _audit_oracle_quality(oracle_entries, allow_agent_generated=allow_agent_gen, state=state)
        status = quality_result["status"]
        failures = quality_result.get("failures", [])
        color = GREEN if status == "PASS" else RED
        print(color(f"\n[CONTRACT AUDIT-ORACLE {status}] {pid}"))
        for f in failures:
            print(RED(f"  {status}: {f}"))
        if status == "PASS":
            print(GREEN("  PASS: oracle quality checks passed"))
        sys.exit(0 if status == "PASS" else 1)

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
            if _openai_advisory_required():
                advisory_result = _auto_run_openai_advisory(state, kind="gpt-contract")
                if advisory_result.get("status") == "COMPLETED":
                    print(GREEN(f"  [GPT ADVISORY] gpt-contract completed via {OPENAI_ADVISORY_MODEL}"))
                elif advisory_result.get("status") == "SKIPPED":
                    print(YELLOW(f"  [GPT ADVISORY] gpt-contract skipped: {advisory_result.get('reason')}"))
                else:
                    print(RED(f"  [GPT ADVISORY] gpt-contract error: {advisory_result.get('reason')}"))
            else:
                print(DIM("  [GPT ADVISORY] auto-run disabled by default (set ENABLE_GPT_ADVISORY_REQUIRED=1 to enable)"))
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
    if args.record:
        _die(
            "[ACCEPTANCE RECORD BLOCKED] `acceptance run --record`는 legacy 점수 경로라 더 이상 "
            "pipeline_state.json을 바꾸지 않습니다. 진짜 Oracle 검증은 `pipeline.py gates oracle`, "
            "사용자 최종 결정은 `pipeline.py gates accept`를 사용하세요."
        )
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

    print("  diagnostic only; pipeline completion is recorded by external gates")
    print()

    sys.exit(0 if summary["verdict"] == "PASS" else 1)


def _external_gates_enabled(state: Dict[str, Any]) -> bool:
    state = _ensure_v210_fields(state)
    gates = state.get("external_gates", {})
    return isinstance(gates, dict) and bool(gates.get("enabled"))


def _phase_attestations_enabled(state: Dict[str, Any]) -> bool:
    state = _ensure_v210_fields(state)
    info = state.get("phase_attestations", {})
    return isinstance(info, dict) and bool(info.get("enabled"))


def _phase_attestation_info(state: Dict[str, Any], phase: str) -> Dict[str, Any]:
    state = _ensure_v210_fields(state)
    info = state.get("phase_attestations", {})
    phases = info.get("phases", {}) if isinstance(info, dict) else {}
    item = phases.get(phase, {}) if isinstance(phases, dict) else {}
    return item if isinstance(item, dict) else {}


def _phase_attestation_required_before(phase: str) -> Optional[str]:
    return {
        "dev": "pm",
        "qa": "dev",
        "sec": "qa",
        "build": "qa",
        "harness": "build",
        "architect": "build",
    }.get(phase)


def _phase_attestation_blocker_for_phase(state: Dict[str, Any], phase: str) -> Optional[str]:
    if not _phase_attestations_enabled(state):
        return None
    required = _phase_attestation_required_before(phase)
    if not required:
        return None
    info = _phase_attestation_info(state, required)
    if info.get("status") != "PASS":
        return (
            f"{required} GitHub phase attestation must be PASS before {phase}. "
            f"Run `python pipeline.py gates prepare-phase --phase {required}`, push the branch, "
            f"wait for CI, then run `python pipeline.py gates phase-ci --phase {required}`."
        )
    return None


def _phase_attestation_blockers(state: Dict[str, Any]) -> List[str]:
    if not _phase_attestations_enabled(state):
        return []
    blockers: List[str] = []
    phases = state.get("phase_attestations", {}).get("phases", {})
    for phase in PHASE_ATTESTATION_PHASES:
        info = phases.get(phase, {}) if isinstance(phases, dict) else {}
        if not isinstance(info, dict) or info.get("status") != "PASS":
            blockers.append(f"{phase} GitHub phase attestation must be PASS")
    return blockers


def _external_gate_blockers(state: Dict[str, Any]) -> List[str]:
    if not _external_gates_enabled(state):
        return []
    gates = state.get("external_gates", {})
    blockers: List[str] = []
    for gate_name in ("technical", "oracle", "acceptance", "github_ci"):
        info = gates.get(gate_name, {}) if isinstance(gates, dict) else {}
        if not isinstance(info, dict) or info.get("status") != "PASS":
            blockers.append(f"{gate_name} gate must be PASS")
    # IMP-20260524-48C4 MT-2 + BUG-20260524-B794: oracle_quality PASS 조건 강제
    # oracle_quality={} (초기값) / None / 누락 모두 차단
    oracle_quality = state.get("oracle_quality")
    oq_status = str((oracle_quality.get("status") if isinstance(oracle_quality, dict) else None) or "").upper()
    if oq_status != "PASS":
        blockers.append(
            f"oracle_quality gate must be PASS (current: {oq_status or 'PENDING'}). "
            "Run `python pipeline.py gates oracle` to pass oracle quality gate."
        )
    blockers.extend(_phase_attestation_blockers(state))
    # GPT advisory CRITICAL은 ENABLE_GPT_ADVISORY_REQUIRED=1 일 때만 COMPLETE를 차단합니다.
    # 기본 모드(REQUIRED 미설정)에서는 advisory가 수동 진단 도구이며 blocker가 아닙니다.
    if _openai_advisory_required():
        pid = str(state.get("pipeline_id", ""))
        api_key, _src = _openai_api_key()
        if not api_key:
            blockers.append("advisory required but OPENAI_API_KEY missing")
        _review_count, _api_call_count = _advisory_run_counts(pid)
        if _review_count == 0:
            blockers.append("advisory required but not run (review_count=0)")
        elif _api_call_count == 0:
            blockers.append("advisory required but API was never called (all results SKIPPED or ERROR)")
        else:
            unresolved = _unresolved_critical_advisories(pid)
            if unresolved:
                blockers.append(f"unresolved GPT advisory CRITICAL findings: {len(unresolved)}")
    return blockers


# ─── Module QA AC Verification (IMP-20260602-1ABE MT-3) ──────────────────────
def _get_mt_covers_ac(state: Dict[str, Any], mt_id: str) -> List[str]:
    """state.atomic_plan에서 mt_id의 covers_ac 리스트 반환 (없으면 빈 리스트)."""
    atomic_plan = state.get("atomic_plan") or {}
    for mt in atomic_plan.get("micro_tasks", []):
        if isinstance(mt, dict) and str(mt.get("id", "")) == mt_id:
            covers = mt.get("covers_ac")
            if isinstance(covers, list):
                return [str(c).strip() for c in covers if str(c).strip()]
            if isinstance(covers, str):
                return [c.strip() for c in covers.split(",") if c.strip()]
            return []
    return []


def _get_mt_covers_iqr(state: Dict[str, Any], mt_id: str) -> List[str]:
    """state.atomic_plan에서 mt_id의 covers_iqr 리스트 반환 (없으면 빈 리스트)."""
    atomic_plan = state.get("atomic_plan") or {}
    for mt in atomic_plan.get("micro_tasks", []):
        if isinstance(mt, dict) and str(mt.get("id", "")) == mt_id:
            covers = mt.get("covers_iqr")
            if isinstance(covers, list):
                return [str(c).strip() for c in covers if str(c).strip()]
            if isinstance(covers, str):
                return [c.strip() for c in covers.split(",") if c.strip()]
            return []
    return []


def _check_module_qa_ac_verification(
    state: Dict[str, Any],
    mt_id: str,
    report_xml_path: Optional[str],
) -> Dict[str, Any]:
    """module qa report의 ac_verification 블록 검증.

    Args:
        state: 현재 파이프라인 state.
        mt_id: 검증 대상 micro_task id.
        report_xml_path: module qa report XML 파일 경로.
    Returns:
        {"valid": True, "reason": str} 또는 {"valid": False, "error": str}

    적용 정책:
    - requirements_tracking.enabled=true가 아닌 legacy state: skip (PASS)
    - structured_acceptance_criteria 비어있는데 requirements_tracking.enabled=true: FAIL
    - covers_ac 없는 MT (legacy 또는 covers_iqr만 있는 문서 MT): PASS
    - covers_ac 있는 MT: ac_verification 블록에 모든 covers_ac id가 있어야 PASS
    """
    rt = state.get("requirements_tracking") or {}
    if not rt.get("enabled"):
        return {"valid": True, "reason": "legacy state — requirements_tracking 비활성, ac 검증 생략"}

    structured_ac = state.get("structured_acceptance_criteria") or []
    if not structured_ac:
        return {
            "valid": False,
            "error": (
                "[AC GATE] requirements_tracking.enabled=true이지만 "
                "structured_acceptance_criteria가 비어 있습니다. PM step_plan에 "
                "<acceptance_criteria> 블록이 있는지 확인하세요."
            ),
        }

    mt_covers_ac = _get_mt_covers_ac(state, mt_id)
    if not mt_covers_ac:
        mt_covers_iqr = _get_mt_covers_iqr(state, mt_id)
        if mt_covers_iqr:
            return {
                "valid": True,
                "reason": f"{mt_id}는 covers_iqr만 있는 문서 전용 MT — ac_verification 생략",
            }
        return {"valid": True, "reason": f"{mt_id}에 covers_ac 없음 — legacy MT 취급"}

    if not report_xml_path or not Path(report_xml_path).exists():
        return {
            "valid": False,
            "error": f"[AC GATE] {mt_id} module qa report 파일이 없습니다: {report_xml_path}",
        }

    try:
        tree = ET.parse(report_xml_path)
        root = tree.getroot()
        ac_verification = root.find(".//ac_verification")
        if ac_verification is None:
            return {
                "valid": False,
                "error": (
                    f"[AC GATE] {mt_id} module qa report에 ac_verification 블록이 없습니다. "
                    f"covers_ac={mt_covers_ac}"
                ),
            }
        verified_ac_ids: set = {
            (c.get("ac_id") or "").strip()
            for c in ac_verification.findall("criterion")
            if c.get("ac_id")
        }
        missing = [ac for ac in mt_covers_ac if ac not in verified_ac_ids]
        if missing:
            return {
                "valid": False,
                "error": (
                    f"[AC GATE] {mt_id} ac_verification에서 아래 AC가 누락되었습니다: {missing}"
                ),
            }
        return {"valid": True, "verified_ac": sorted(verified_ac_ids)}
    except (ET.ParseError, OSError) as exc:
        return {
            "valid": False,
            "error": f"[AC GATE] module qa report 파싱 오류: {exc}",
        }


def cmd_module(args: argparse.Namespace) -> None:
    """Incremental module gate for PM micro_tasks."""
    action = args.module_action
    state = _require_state()
    state = _ensure_v210_fields(state)
    if action == "status":
        print(json.dumps({
            "pipeline_id": state.get("pipeline_id"),
            "module_gates": _module_gate_state(state),
            "blockers": _module_gate_blockers(state),
        }, ensure_ascii=False, indent=2))
        return

    current = state.get("current_phase")
    if current != "dev":
        _die(f"[MODULE GATE] module commands are allowed only during Dev phase; current_phase={current!r}")
    if not isinstance(state.get("atomic_plan"), dict):
        _die("[MODULE GATE] PM atomic_plan missing; complete PM first")

    gates = _module_gate_state(state)
    mt_id = str(getattr(args, "mt_id", "") or "")

    if action in {"design", "dev", "qa"}:
        if not mt_id:
            _die("[MODULE GATE] --mt-id is required", exit_code=2)
        module = _require_module_current(state, mt_id)
        _validate_module_checkpoints(state)

    if action == "design":
        report = _validate_module_report_file(
            getattr(args, "report_file", None),
            label="MODULE DESIGN GATE",
            required_tags=["module_design", "mt_id", "interface_contract", "implementation_plan", "verification_plan"],
            mt_id=mt_id,
        )
        module["design"] = {
            **_empty_module_step(),
            "status": "PASS",
            "completed_at": _now(),
            "report_file": report["report_file"],
        }
        module["status"] = "DESIGN_DONE"
        state["module_gates"]["modules"][mt_id] = module
        _log_event(state, f"module design PASS {mt_id}")
        _save(state)
        print(GREEN(f"\n[MODULE DESIGN PASS] {mt_id}"))
        print(f"  next: {YELLOW(f'python pipeline.py module dev --mt-id {mt_id} --files ... --report-file module_handover_{mt_id}.xml --scope-manifest scope_manifest_{mt_id}.json')}\n")
        return

    if action == "dev":
        if module.get("design", {}).get("status") != "PASS":
            _die(f"[MODULE GATE] {mt_id} requires module design PASS before module dev")
        report = _validate_module_report_file(
            getattr(args, "report_file", None),
            label="MODULE DEV GATE",
            required_tags=["module_handover", "mt_id", "implemented_files", "self_check"],
            mt_id=mt_id,
        )
        scope = _validate_module_scope_manifest(
            getattr(args, "scope_manifest", None) or "",
            state,
            mt_id,
            getattr(args, "files", None),
        )
        module["dev"] = {
            **_empty_module_step(),
            "status": "DONE",
            "completed_at": _now(),
            "report_file": report["report_file"],
            "evidence": getattr(args, "files", None),
            "scope": scope,
        }
        module["status"] = "DEV_DONE"
        state["module_gates"]["modules"][mt_id] = module
        _log_event(state, f"module dev DONE {mt_id}")
        _save(state)
        print(GREEN(f"\n[MODULE DEV DONE] {mt_id}"))
        print(f"  next: {YELLOW(f'python pipeline.py module qa --mt-id {mt_id} --result PASS --report-file module_qa_{mt_id}.xml')}\n")
        return

    if action == "qa":
        if module.get("dev", {}).get("status") != "DONE":
            _die(f"[MODULE GATE] {mt_id} requires module dev DONE before module QA")
        result = str(getattr(args, "result", "")).upper()
        report = _validate_module_report_file(
            getattr(args, "report_file", None),
            label="MODULE QA GATE",
            required_tags=["module_qa_report", "mt_id", "verdict", "verification_evidence"],
            mt_id=mt_id,
        )
        path, text = _read_phase_report_or_die(getattr(args, "report_file", None), "MODULE QA GATE")
        verdict_el = _extract_xml_element(text, "verdict")
        verdict = (verdict_el.text or "").strip().upper() if verdict_el is not None and verdict_el.text else ""
        if verdict != result:
            _die(f"[MODULE QA GATE] CLI --result {result} does not match report <verdict> {verdict}")
        # IMP-20260602-1ABE MT-3: PASS 시 ac_verification 블록 검증 (requirements_tracking.enabled=true만 적용)
        if result == "PASS":
            ac_check = _check_module_qa_ac_verification(
                state, mt_id, report["report_file"]
            )
            if not ac_check.get("valid"):
                _die(ac_check.get("error", "[AC GATE] module qa ac_verification 검증 실패"))
        module["qa"] = {
            **_empty_module_step(),
            "status": result,
            "completed_at": _now(),
            "report_file": report["report_file"],
        }
        if result == "PASS":
            module["status"] = "PASS"
            module["checkpoint"] = _module_checkpoint_for_files(module.get("target_files", []))
            _log_event(state, f"module QA PASS {mt_id}")
            print(GREEN(f"\n[MODULE QA PASS] {mt_id} checkpoint saved"))
        else:
            module["status"] = "QA_FAIL"
            _log_event(state, f"module QA FAIL {mt_id}")
            print(RED(f"\n[MODULE QA FAIL] {mt_id}"))
        state["module_gates"]["modules"][mt_id] = module
        _save(state)
        blockers = _module_gate_blockers(state)
        if blockers:
            print(f"  remaining: {', '.join(blockers)}\n")
        else:
            print(f"  next: {YELLOW('python pipeline.py module integrate --result PASS --report-file integration_report.xml')}\n")
        sys.exit(0 if result == "PASS" else 1)

    if action == "integrate":
        blockers = [
            blocker for blocker in _module_gate_blockers(state)
            if blocker != "integration module gate must be PASS"
        ]
        if blockers:
            _die("[MODULE INTEGRATION GATE] all modules must pass before integration: " + "; ".join(blockers))
        _validate_module_checkpoints(state)
        result = str(getattr(args, "result", "")).upper()
        report = _validate_module_report_file(
            getattr(args, "report_file", None),
            label="MODULE INTEGRATION GATE",
            required_tags=["integration_report", "modules_integrated", "integration_verdict"],
        )
        path, text = _read_phase_report_or_die(getattr(args, "report_file", None), "MODULE INTEGRATION GATE")
        verdict_el = _extract_xml_element(text, "integration_verdict")
        verdict = (verdict_el.text or "").strip().upper() if verdict_el is not None and verdict_el.text else ""
        if verdict != result:
            _die(f"[MODULE INTEGRATION GATE] CLI --result {result} does not match report <integration_verdict> {verdict}")
        gates["integration"] = {
            **_empty_module_step(),
            "status": "PASS" if result == "PASS" else "FAIL",
            "completed_at": _now(),
            "report_file": report["report_file"],
        }
        state["module_gates"]["integration"] = gates["integration"]
        _log_event(state, f"module integration {result}")
        _save(state)
        color = GREEN if result == "PASS" else RED
        print(color(f"\n[MODULE INTEGRATION {result}]"))
        if result == "PASS":
            print(f"  next: {YELLOW('python pipeline.py done --phase dev --files ... --report-file dev_handover.xml --scope-declared --scope-manifest scope_manifest.json --agent-run-id ...')}\n")
        sys.exit(0 if result == "PASS" else 1)

    _die(f"unknown module action: {action}", exit_code=2)


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
    review_count = len(review_files)
    api_call_count = sum(1 for item in review_files if item.get("api_called"))
    unresolved_critical_count = len(_unresolved_critical_advisories(pid))
    api_call_enabled = bool(api_key) and _openai_advisory_enabled()
    required = _openai_advisory_required()
    # advisory_mode 4상태 분류:
    #   not_run    : REQUIRED 미설정 (기본). advisory가 수동 진단 도구 (COMPLETE 차단 안함).
    #   skipped    : ENABLE_GPT_ADVISORY=0 또는 API key 없음.
    #   required   : REQUIRED=1이지만 unresolved CRITICAL=0.
    #   blocking   : REQUIRED=1 + unresolved CRITICAL≥1 → COMPLETE 차단.
    if required:
        if unresolved_critical_count > 0:
            advisory_mode = "blocking"
            advisory_mode_reason = (
                f"REQUIRED=1 + unresolved CRITICAL findings={unresolved_critical_count}"
            )
        elif not api_key:
            advisory_mode = "blocking"
            advisory_mode_reason = "REQUIRED=1 but OPENAI_API_KEY missing"
        else:
            advisory_mode = "required"
            advisory_mode_reason = "REQUIRED=1; no unresolved CRITICAL"
    elif not api_call_enabled:
        advisory_mode = "skipped"
        if not api_key:
            advisory_mode_reason = "OPENAI_API_KEY not set"
        else:
            advisory_mode_reason = "ENABLE_GPT_ADVISORY not 1"
    else:
        # ENABLE_GPT_ADVISORY=1이지만 REQUIRED 미설정 → API 호출은 허용되나 자동 실행/blocker 없음
        advisory_mode = "not_run"
        advisory_mode_reason = (
            "disabled by default (manual diagnostic only — set ENABLE_GPT_ADVISORY_REQUIRED=1 for blocker)"
        )
    return {
        "api_key_present": bool(api_key),
        "api_key_source": api_key_source,
        "enabled": api_call_enabled,
        "required": required,
        "advisory_mode": advisory_mode,
        "advisory_mode_reason": advisory_mode_reason,
        "review_count": review_count,
        "api_call_count": api_call_count,
        "status_counts": status_counts,
        "reviews": review_files,
        "unresolved_critical_count": unresolved_critical_count,
    }


def _github_repo_from_remote(remote_url: Optional[str] = None) -> str:
    if remote_url is None:
        try:
            completed = subprocess.run(
                ["git", "remote", "get-url", "origin"],
                cwd=str(BASE_DIR),
                text=True,
                capture_output=True,
                check=True,
            )
            remote_url = completed.stdout.strip()
        except (OSError, subprocess.CalledProcessError) as exc:
            _die(f"could not infer GitHub repo from origin remote: {exc}")
    remote = str(remote_url or "").strip()
    match = re.search(r"github\.com[:/](?P<owner>[^/\s]+)/(?P<repo>[^/\s]+?)(?:\.git)?$", remote)
    if not match:
        _die(f"origin remote is not a GitHub repository URL: {remote!r}")
    return f"{match.group('owner')}/{match.group('repo')}"


def _git_rev_parse(ref: str) -> Optional[str]:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", ref],
            cwd=str(BASE_DIR),
            text=True,
            capture_output=True,
            check=True,
        )
        return completed.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _github_token(token_env: str = "GITHUB_TOKEN") -> Optional[str]:
    token = os.environ.get(token_env) or os.environ.get("GH_TOKEN")
    if token:
        return token.strip()
    return _github_token_from_git_credentials()


def _github_token_from_git_credentials() -> Optional[str]:
    """Reuse the GitHub login already configured for git push/pull.

    Claude Code usually runs in the same Windows user session as git. If Git
    Credential Manager is authenticated, `git credential fill` can supply the
    same token without asking the user to create or paste a PAT into the
    pipeline.
    """
    try:
        completed = subprocess.run(
            ["git", "credential", "fill"],
            cwd=str(BASE_DIR),
            input="protocol=https\nhost=github.com\n\n",
            text=True,
            capture_output=True,
            check=True,
            timeout=10,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    values: Dict[str, str] = {}
    for line in completed.stdout.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    password = values.get("password")
    return password or None


def _github_api_json(url: str, token: Optional[str]) -> Dict[str, Any]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "pipeline-trust-verifier",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            body: Any = json.loads(raw)
        except json.JSONDecodeError:
            body = {"raw": raw[:1000]}
        auth_hint = ""
        if not token and exc.code in {401, 403, 404}:
            auth_hint = " Set GITHUB_TOKEN or GH_TOKEN with repo/actions read permission for private repositories."
        _die(f"GitHub API request failed HTTP {exc.code}: {body}{auth_hint}")
    except (OSError, json.JSONDecodeError) as exc:
        _die(f"GitHub API request failed: {exc}")


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


def _github_download_bytes(url: str, token: Optional[str]) -> bytes:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "pipeline-trust-verifier",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers, method="GET")
    opener = urllib.request.build_opener(_NoRedirectHandler)
    try:
        with opener.open(request, timeout=120) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        if exc.code in {301, 302, 303, 307, 308}:
            redirect_url = exc.headers.get("Location")
            if not redirect_url:
                _die(f"GitHub artifact download redirect missing Location header HTTP {exc.code}")
            redirect_request = urllib.request.Request(
                redirect_url,
                headers={"User-Agent": "pipeline-trust-verifier"},
                method="GET",
            )
            try:
                with urllib.request.urlopen(redirect_request, timeout=120) as response:
                    return response.read()
            except urllib.error.HTTPError as redirected_exc:
                raw = redirected_exc.read().decode("utf-8", errors="replace")
                _die(f"GitHub artifact redirected download failed HTTP {redirected_exc.code}: {raw[:1000]}")
            except OSError as redirected_exc:
                _die(f"GitHub artifact redirected download failed: {redirected_exc}")
        raw = exc.read().decode("utf-8", errors="replace")
        _die(f"GitHub artifact download failed HTTP {exc.code}: {raw[:1000]}")
    except OSError as exc:
        _die(f"GitHub artifact download failed: {exc}")


def _github_latest_run_for_commit(
    repo: str,
    commit_sha: str,
    token: Optional[str],
    *,
    workflow: Optional[str] = None,
) -> Dict[str, Any]:
    query = urllib.parse.urlencode({"head_sha": commit_sha, "per_page": "50"})
    response = _github_api_json(f"https://api.github.com/repos/{repo}/actions/runs?{query}", token)
    runs = response.get("workflow_runs", [])
    if not isinstance(runs, list):
        _die("GitHub workflow runs response did not contain workflow_runs")
    candidates = [run for run in runs if isinstance(run, dict)]
    if workflow:
        candidates = [run for run in candidates if str(run.get("name") or "") == workflow]
    if not candidates:
        workflow_hint = f" named {workflow!r}" if workflow else ""
        _die(
            f"no GitHub Actions workflow run{workflow_hint} found for commit {commit_sha[:12]}. "
            "Push the branch and wait for CI before running `python pipeline.py gates github-ci`."
        )
    successful = [
        run for run in candidates
        if run.get("status") == "completed" and run.get("conclusion") == "success"
    ]
    return successful[0] if successful else candidates[0]


def _poll_github_ci_run(
    repo: str,
    expected_head_sha: str,
    timeout_sec: int,
    poll_sec: int,
    token: Optional[str],
    pr_num: Optional[int] = None,
) -> Dict[str, Any]:
    """GitHub CI run을 expected_head_sha 기준으로 polling하여 완료 여부를 반환합니다.

    IMP-20260524-C097 MT-1: blind wait(time.sleep) 대신 SHA 기반 정확한 CI run 추적.

    Args:
        repo: owner/repo 형식 GitHub 저장소.
        expected_head_sha: 기대하는 head SHA (40자 full SHA 또는 prefix).
        timeout_sec: 최대 대기 시간(초).
        poll_sec: polling 간격(초).
        token: GitHub API 토큰 (없으면 None).
        pr_num: PR 번호 (로그 출력용, 없으면 None).

    Returns:
        Dict with keys:
            wait_status: PASS | FAIL | TIMEOUT | WAITING_FOR_TRIGGER | CANCELLED
            matched_head_sha: bool
            conclusion: GitHub conclusion 값 또는 빈 문자열
            run_id: GitHub run id 문자열 또는 None
            elapsed_sec: 실제 소요 시간(초)
    """
    import time as _time

    sha_prefix = expected_head_sha.lower()
    pr_hint = f" (PR #{pr_num})" if pr_num else ""
    start = _time.monotonic()
    print(f"[CI 대기{pr_hint}] SHA={sha_prefix[:12]} 기준 run 검색 시작 (최대 {timeout_sec}초, {poll_sec}초 간격)")

    while True:
        elapsed = _time.monotonic() - start
        if elapsed >= timeout_sec:
            print(f"[CI 대기] {elapsed:.0f}초 경과 — TIMEOUT (기대 SHA run 미발견)")
            return {
                "wait_status": "TIMEOUT",
                "matched_head_sha": False,
                "conclusion": "",
                "run_id": None,
                "elapsed_sec": elapsed,
            }

        # GitHub API: head_sha 기반 run 목록 조회
        query = urllib.parse.urlencode({"head_sha": expected_head_sha, "per_page": "20"})
        try:
            response = _github_api_json(
                f"https://api.github.com/repos/{repo}/actions/runs?{query}",
                token,
            )
        except SystemExit:
            # _die()가 sys.exit(1)을 호출하므로 API 오류 시 FAIL 반환
            elapsed = _time.monotonic() - start
            return {
                "wait_status": "FAIL",
                "matched_head_sha": False,
                "conclusion": "",
                "run_id": None,
                "elapsed_sec": elapsed,
            }

        runs = response.get("workflow_runs", [])
        if not isinstance(runs, list):
            runs = []

        # SHA 일치 run 필터
        sha_matched: List[Dict[str, Any]] = []
        for run in runs:
            if not isinstance(run, dict):
                continue
            run_sha = str(run.get("head_sha") or "").lower()
            if run_sha.startswith(sha_prefix) or sha_prefix.startswith(run_sha[:len(sha_prefix)]):
                sha_matched.append(run)

        if not sha_matched:
            print(f"[CI 대기] {elapsed:.0f}초 경과 — WAITING_FOR_TRIGGER (SHA 일치 run 없음)")
            _time.sleep(poll_sec)
            continue

        # 가장 최신 SHA 일치 run 선택 (id 내림차순)
        sha_matched.sort(key=lambda r: int(r.get("id") or 0), reverse=True)
        current_run = sha_matched[0]
        run_status = str(current_run.get("status") or "")
        conclusion = str(current_run.get("conclusion") or "")
        run_id_val = str(current_run.get("id") or "")

        if run_status != "completed":
            status_label = run_status or "queued/in_progress"
            print(f"[CI 대기] {elapsed:.0f}초 경과 — {status_label.upper()} (run_id={run_id_val})")
            _time.sleep(poll_sec)
            continue

        # 완료된 run
        elapsed = _time.monotonic() - start
        if conclusion == "success":
            wait_status = "PASS"
        elif conclusion in ("cancelled", "skipped"):
            wait_status = "CANCELLED"
        else:
            wait_status = "FAIL"

        print(f"[CI 대기] {elapsed:.0f}초 경과 — {wait_status} (conclusion={conclusion}, run_id={run_id_val})")
        return {
            "wait_status": wait_status,
            "matched_head_sha": True,
            "conclusion": conclusion,
            "run_id": run_id_val,
            "elapsed_sec": elapsed,
        }


def _read_attestation_from_zip(zip_bytes: bytes, file_name: str = "pipeline_attestation.json") -> Dict[str, Any]:
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
            if file_name not in archive.namelist():
                _die(f"artifact zip is missing {file_name}")
            with archive.open(file_name) as item:
                return json.loads(item.read().decode("utf-8-sig"))
    except zipfile.BadZipFile as exc:
        _die(f"artifact is not a valid zip archive: {exc}")
    except json.JSONDecodeError as exc:
        _die(f"attestation JSON is invalid: {exc}")


def _validate_github_ci_attestation(
    attestation: Dict[str, Any],
    *,
    repo: str,
    run_id: str,
    commit_sha: str,
    tree_sha: Optional[str] = None,
) -> Dict[str, Any]:
    blockers: List[str] = []
    warnings: List[str] = []
    if attestation.get("schema_version") != 1:
        blockers.append("schema_version must be 1")
    if attestation.get("attestation_type") != "pipeline-ci-v1":
        blockers.append("attestation_type must be pipeline-ci-v1")
    if str(attestation.get("repository") or "") != repo:
        blockers.append(f"repository mismatch: expected {repo}, got {attestation.get('repository')}")
    if str(attestation.get("run_id") or "") != str(run_id):
        blockers.append(f"run_id mismatch: expected {run_id}, got {attestation.get('run_id')}")
    attested_head_sha = str(attestation.get("head_sha") or attestation.get("commit_sha") or "")
    checkout_sha = str(attestation.get("checkout_sha") or attestation.get("commit_sha") or "")
    if attested_head_sha.lower() != str(commit_sha).lower():
        blockers.append("commit_sha mismatch")
    if tree_sha:
        if checkout_sha and checkout_sha.lower() != str(commit_sha).lower():
            warnings.append("tree_sha comparison skipped for pull_request merge checkout")
        elif str(attestation.get("tree_sha") or "").lower() != tree_sha.lower():
            blockers.append("tree_sha mismatch")
    tests = attestation.get("tests", {})
    if not isinstance(tests, dict) or tests.get("status") != "PASS":
        blockers.append("tests.status must be PASS")
    return {
        "schema_version": 1,
        "verified_at": _now(),
        "status": "PASS" if not blockers else "FAIL",
        "blockers": blockers,
        "warnings": warnings,
        "repository": repo,
        "run_id": str(run_id),
        "commit_sha": commit_sha,
        "tree_sha": tree_sha or attestation.get("tree_sha"),
        "attestation": attestation,
    }


def _validate_github_phase_attestation(
    attestation: Dict[str, Any],
    *,
    repo: str,
    run_id: str,
    commit_sha: str,
    pipeline_id: str,
    phase: str,
) -> Dict[str, Any]:
    blockers: List[str] = []
    warnings: List[str] = []
    if attestation.get("schema_version") != 1:
        blockers.append("schema_version must be 1")
    if attestation.get("attestation_type") != "pipeline-phase-v1":
        blockers.append("attestation_type must be pipeline-phase-v1")
    if str(attestation.get("repository") or "") != repo:
        blockers.append(f"repository mismatch: expected {repo}, got {attestation.get('repository')}")
    if str(attestation.get("run_id") or "") != str(run_id):
        blockers.append(f"run_id mismatch: expected {run_id}, got {attestation.get('run_id')}")
    if str(attestation.get("pipeline_id") or "") != pipeline_id:
        blockers.append(f"pipeline_id mismatch: expected {pipeline_id}, got {attestation.get('pipeline_id')}")
    if str(attestation.get("phase") or "") != phase:
        blockers.append(f"phase mismatch: expected {phase}, got {attestation.get('phase')}")
    head_sha = str(attestation.get("head_sha") or attestation.get("commit_sha") or "")
    if head_sha.lower() != str(commit_sha).lower():
        blockers.append("commit_sha mismatch")
    validation = attestation.get("validation", {})
    if not isinstance(validation, dict):
        blockers.append("validation must be an object")
    elif validation.get("status") != "PASS":
        blockers.extend(str(item) for item in validation.get("blockers", ["phase validation did not PASS"]))
    request = attestation.get("request", {})
    if not isinstance(request, dict):
        blockers.append("request must be an object")
        request = {}
    agent_run = request.get("agent_run")
    if not isinstance(agent_run, dict):
        blockers.append("request.agent_run receipt is required")
    else:
        expected_run_phase = PHASE_RECEIPT_RUN_PHASES.get(phase, phase)
        expected_agent = _expected_agent_id(expected_run_phase)
        if agent_run.get("phase") != expected_run_phase:
            blockers.append("request.agent_run phase mismatch")
        if agent_run.get("agent_id") != expected_agent:
            blockers.append(f"request.agent_run agent_id must be {expected_agent}")
        if agent_run.get("status") != "COMPLETED":
            blockers.append("request.agent_run status must be COMPLETED")
        if agent_run.get("used_by_phase") != phase:
            blockers.append("request.agent_run must be consumed by the attested phase")
    if phase == "pm":
        manager_run = request.get("manager_run")
        if not isinstance(manager_run, dict):
            blockers.append("request.manager_run receipt is required for pm")
        else:
            if manager_run.get("phase") != "pipeline_manager":
                blockers.append("request.manager_run phase must be pipeline_manager")
            if manager_run.get("agent_id") != _expected_agent_id("pipeline_manager"):
                blockers.append("request.manager_run agent_id must be pipeline-manager-agent")
            if manager_run.get("status") != "COMPLETED":
                blockers.append("request.manager_run status must be COMPLETED")
            if manager_run.get("used_by_phase") != "pm_manager":
                blockers.append("request.manager_run must be consumed by pm_manager")
    if isinstance(request, dict) and request.get("local_only_files"):
        warnings.append("phase request included local-only files that GitHub Actions could not re-read")
    return {
        "schema_version": 1,
        "verified_at": _now(),
        "status": "PASS" if not blockers else "FAIL",
        "blockers": blockers,
        "warnings": warnings,
        "repository": repo,
        "run_id": str(run_id),
        "commit_sha": commit_sha,
        "pipeline_id": pipeline_id,
        "phase": phase,
        "attestation": attestation,
    }


def _verify_github_ci_run(
    *,
    repo: Optional[str],
    commit: Optional[str],
    run_id: Optional[str],
    artifact_name: str,
    token_env: str,
    workflow: Optional[str],
) -> Dict[str, Any]:
    resolved_repo = repo or _github_repo_from_remote()
    if commit and re.fullmatch(r"[0-9a-fA-F]{40}", commit):
        commit_sha = commit
    else:
        commit_sha = _git_rev_parse(commit or "HEAD") or ""
    if not commit_sha:
        _die("could not infer commit sha; pass --commit explicitly")
    token = _github_token(token_env)

    if run_id:
        run = _github_api_json(f"https://api.github.com/repos/{resolved_repo}/actions/runs/{run_id}", token)
    else:
        run = _github_latest_run_for_commit(resolved_repo, commit_sha, token, workflow=workflow)
    resolved_run_id = str(run.get("id") or run_id or "")
    if not resolved_run_id:
        _die("GitHub workflow run response is missing id")

    run_blockers: List[str] = []
    if str(run.get("head_sha") or "").lower() != str(commit_sha).lower():
        run_blockers.append(f"workflow run head_sha mismatch: expected {commit_sha}, got {run.get('head_sha')}")
    if run.get("status") != "completed":
        run_blockers.append(f"workflow run status is not completed: {run.get('status')}")
    if run.get("conclusion") != "success":
        run_blockers.append(f"workflow run conclusion is not success: {run.get('conclusion')}")

    artifacts = _github_api_json(f"https://api.github.com/repos/{resolved_repo}/actions/runs/{resolved_run_id}/artifacts", token)
    artifact_items = artifacts.get("artifacts", [])
    if not isinstance(artifact_items, list):
        _die("GitHub artifacts response did not contain an artifacts list")
    artifact = next((item for item in artifact_items if isinstance(item, dict) and item.get("name") == artifact_name), None)
    if not artifact:
        _die(f"workflow run is missing required artifact: {artifact_name}")
    download_url = artifact.get("archive_download_url")
    if not download_url:
        _die("workflow artifact response is missing archive_download_url")
    zip_bytes = _github_download_bytes(str(download_url), token)
    attestation = _read_attestation_from_zip(zip_bytes)
    tree_sha = _git_rev_parse(f"{commit_sha}^{{tree}}")
    verification = _validate_github_ci_attestation(
        attestation,
        repo=resolved_repo,
        run_id=resolved_run_id,
        commit_sha=commit_sha,
        tree_sha=tree_sha,
    )
    if run_blockers:
        verification["status"] = "FAIL"
        verification["blockers"] = run_blockers + list(verification.get("blockers", []))
    verification["workflow_run"] = {
        "id": run.get("id"),
        "name": run.get("name"),
        "html_url": run.get("html_url"),
        "status": run.get("status"),
        "conclusion": run.get("conclusion"),
        "head_sha": run.get("head_sha"),
    }
    verification["artifact"] = {
        "id": artifact.get("id"),
        "name": artifact.get("name"),
        "size_in_bytes": artifact.get("size_in_bytes"),
        "expired": artifact.get("expired"),
    }
    return verification


def _verify_github_phase_attestation_run(
    *,
    repo: Optional[str],
    commit: Optional[str],
    run_id: Optional[str],
    artifact_name: str,
    token_env: str,
    workflow: Optional[str],
    pipeline_id: str,
    phase: str,
) -> Dict[str, Any]:
    resolved_repo = repo or _github_repo_from_remote()
    if commit and re.fullmatch(r"[0-9a-fA-F]{40}", commit):
        commit_sha = commit
    else:
        commit_sha = _git_rev_parse(commit or "HEAD") or ""
    if not commit_sha:
        _die("could not infer commit sha; pass --commit explicitly")
    token = _github_token(token_env)

    if run_id:
        run = _github_api_json(f"https://api.github.com/repos/{resolved_repo}/actions/runs/{run_id}", token)
    else:
        run = _github_latest_run_for_commit(resolved_repo, commit_sha, token, workflow=workflow)
    resolved_run_id = str(run.get("id") or run_id or "")
    if not resolved_run_id:
        _die("GitHub workflow run response is missing id")

    run_blockers: List[str] = []
    if str(run.get("head_sha") or "").lower() != str(commit_sha).lower():
        run_blockers.append(f"workflow run head_sha mismatch: expected {commit_sha}, got {run.get('head_sha')}")
    if run.get("status") != "completed":
        run_blockers.append(f"workflow run status is not completed: {run.get('status')}")
    if run.get("conclusion") != "success":
        run_blockers.append(f"workflow run conclusion is not success: {run.get('conclusion')}")

    artifacts = _github_api_json(f"https://api.github.com/repos/{resolved_repo}/actions/runs/{resolved_run_id}/artifacts", token)
    artifact_items = artifacts.get("artifacts", [])
    if not isinstance(artifact_items, list):
        _die("GitHub artifacts response did not contain an artifacts list")
    artifact = next((item for item in artifact_items if isinstance(item, dict) and item.get("name") == artifact_name), None)
    if not artifact:
        _die(f"workflow run is missing required artifact: {artifact_name}")
    download_url = artifact.get("archive_download_url")
    if not download_url:
        _die("workflow artifact response is missing archive_download_url")
    zip_bytes = _github_download_bytes(str(download_url), token)
    attestation = _read_attestation_from_zip(zip_bytes, file_name="phase_attestation.json")
    verification = _validate_github_phase_attestation(
        attestation,
        repo=resolved_repo,
        run_id=resolved_run_id,
        commit_sha=commit_sha,
        pipeline_id=pipeline_id,
        phase=phase,
    )
    if run_blockers:
        verification["status"] = "FAIL"
        verification["blockers"] = run_blockers + list(verification.get("blockers", []))
    verification["workflow_run"] = {
        "id": run.get("id"),
        "name": run.get("name"),
        "html_url": run.get("html_url"),
        "status": run.get("status"),
        "conclusion": run.get("conclusion"),
        "head_sha": run.get("head_sha"),
    }
    verification["artifact"] = {
        "id": artifact.get("id"),
        "name": artifact.get("name"),
        "size_in_bytes": artifact.get("size_in_bytes"),
        "expired": artifact.get("expired"),
    }
    return verification


def _record_github_ci_verification(state: Dict[str, Any], verification: Dict[str, Any], artifact_name: str) -> None:
    pid = str(state.get("pipeline_id"))
    paths = _contract_paths(pid)
    _write_json(paths["github_ci_result"], verification)
    run_id = str(verification.get("run_id") or "")
    commit_sha = str(verification.get("commit_sha") or "")
    state.setdefault("trusted_github_runs", [])
    state["trusted_github_runs"].append({
        "recorded_at": _now(),
        "status": verification["status"],
        "repo": verification.get("repository"),
        "run_id": run_id,
        "commit_sha": commit_sha,
        "artifact": artifact_name,
    })
    if _external_gates_enabled(state):
        _set_external_gate(
            state,
            "github_ci",
            str(verification["status"]),
            evidence=f"github_actions_run:{run_id}",
            report_file=str(paths["github_ci_result"]),
        )
    _log_event(state, f"github verify-run {verification['status']} run_id={run_id} commit={commit_sha[:12]}")


def _record_phase_ci_verification(
    state: Dict[str, Any],
    phase: str,
    verification: Dict[str, Any],
    artifact_name: str,
) -> None:
    pid = str(state.get("pipeline_id"))
    paths = _contract_paths(pid)
    result_path = paths["phase_ci_root"] / f"{phase}_result.json"
    _write_json(result_path, verification)
    state["phase_attestations"] = _ensure_phase_attestations(state)
    phase_state = state["phase_attestations"]["phases"][phase]
    run_id = str(verification.get("run_id") or "")
    commit_sha = str(verification.get("commit_sha") or "")
    phase_state.update({
        "status": verification["status"],
        "completed_at": _now(),
        "phase": phase,
        "run_id": run_id,
        "commit_sha": commit_sha,
        "evidence": f"github_actions_phase_run:{run_id}",
        "report_file": str(result_path),
        "artifact": artifact_name,
    })
    state.setdefault("trusted_phase_runs", [])
    state["trusted_phase_runs"].append({
        "recorded_at": _now(),
        "status": verification["status"],
        "repo": verification.get("repository"),
        "phase": phase,
        "run_id": run_id,
        "commit_sha": commit_sha,
        "artifact": artifact_name,
    })
    _log_event(state, f"github phase-ci {phase} {verification['status']} run_id={run_id} commit={commit_sha[:12]}")


def cmd_github(args: argparse.Namespace) -> None:
    action = args.github_action
    if action != "verify-run":
        _die(f"unknown github action: {action}", exit_code=2)

    verification = _verify_github_ci_run(
        repo=args.repo,
        commit=args.commit,
        run_id=args.run_id,
        artifact_name=args.artifact,
        token_env=args.token_env,
        workflow=args.workflow,
    )

    if getattr(args, "record", False):
        state = _require_state()
        _record_github_ci_verification(state, verification, args.artifact)
        _save(state)

    print(json.dumps(verification, ensure_ascii=False, indent=2))
    sys.exit(0 if verification["status"] == "PASS" else 1)


def cmd_agent(args: argparse.Namespace) -> None:
    action = args.agent_action
    state = _require_state()
    state = _ensure_v210_fields(state)
    if action == "start":
        run, token = _agent_run_start(state, args.phase, args.agent_id)
        _save(state)
        safe_run = {k: v for k, v in run.items() if k != "token_hash"}
        print(GREEN(f"\n[AGENT RUN STARTED] {run['phase']} {run['agent_id']}"))
        print(f"  run_id: {run['run_id']}")
        print(f"  token: {token}")
        print("  Pass this token only to the assigned agent; it is shown once.\n")
        print(json.dumps({"run": safe_run, "token": token}, ensure_ascii=False, indent=2))
        return

    if action == "finish":
        run = _agent_run_finish(
            state,
            run_id=args.run_id,
            token=args.token,
            output_file=args.output_file,
            evidence=args.evidence,
            notes=args.notes,
        )
        _save(state)
        safe_run = {k: v for k, v in run.items() if k != "token_hash"}
        print(GREEN(f"\n[AGENT RUN COMPLETED] {run['phase']} {run['agent_id']}"))
        print(f"  run_id: {run['run_id']}")
        print(f"  receipt: {run['receipt_path']}\n")
        print(json.dumps(safe_run, ensure_ascii=False, indent=2))
        return

    if action == "status":
        runs = state.get("agent_runs", {})
        if not isinstance(runs, dict):
            runs = {}
        safe_runs = {
            rid: {k: v for k, v in run.items() if k != "token_hash"}
            for rid, run in runs.items()
            if isinstance(run, dict)
        }
        print(json.dumps({
            "pipeline_id": state.get("pipeline_id"),
            "agent_runs": safe_runs,
        }, ensure_ascii=False, indent=2))
        return

    _die(f"unknown agent action: {action}", exit_code=2)


def cmd_outputs(args: argparse.Namespace) -> None:
    """Register or list user-visible output artifacts for final ACCEPT."""
    state = _require_state()
    state = _ensure_v210_fields(state)
    action = args.outputs_action
    if action == "add":
        item = _register_output_item(
            state,
            kind=str(args.kind),
            path=str(args.path),
            label=str(args.label or args.kind),
            copy_to_outputs=not bool(getattr(args, "no_copy", False)),
            notes=str(args.notes or ""),
        )
        _log_event(state, f"output registered {item['kind']} {item['public_path']}")
        _save(state)
        print(GREEN("\n[OUTPUT REGISTERED]"))
        print(f"  label: {item['label']}")
        print(f"  path:  {item['public_path']}")
        print("  이 파일은 GitHub PR 최종 확인 안내에서 결과물 링크 후보로 표시됩니다.\n")
        return
    if action == "status":
        print(json.dumps({
            "pipeline_id": state.get("pipeline_id"),
            "outputs": _ensure_output_registry(state),
        }, ensure_ascii=False, indent=2))
        return
    _die(f"unknown outputs action: {action}", exit_code=2)


# ─── IMP-20260603-2E3D MT-1: PR Packet SSoT ───────────────────────────────────
# 사용자 ACCEPT 판단 자료(human_acceptance_packet.md)와 PR 본문의 최종 확인 안내
# 블록을 에이전트 자유서술 대신 pipeline.py가 실제 git/gh/state 데이터로
# 자동 생성한다.

PIPELINE_FINAL_PACKET_START_MARKER = "<!-- PIPELINE_FINAL_PACKET_START -->"
PIPELINE_FINAL_PACKET_END_MARKER = "<!-- PIPELINE_FINAL_PACKET_END -->"
HUMAN_ACCEPTANCE_PACKET_FILE = "human_acceptance_packet.md"
PACKET_LINE_MAX_WIDTH = 120


def _wrap_packet_line(line: str, max_width: int = PACKET_LINE_MAX_WIDTH) -> List[str]:
    """packet의 한 줄을 max_width 이하로 줄바꿈한다.

    URL, 코드, 절대경로 같은 공백 없는 토큰이 들어 있어도 max_width 한도를 깨지 않도록
    공백 분할 후 길이 누적 방식으로 줄바꿈한다. 공백 없는 단일 토큰이 한도를 넘으면
    그대로 한 줄로 둔다(승인 코드, 긴 URL은 그대로 사용해야 하므로 강제 분할 금지).

    Args:
        line: 입력 줄 (개행 없음 가정).
        max_width: 한 줄 최대 길이.
    Returns:
        max_width 이하 줄들의 list.
    """
    if not isinstance(line, str):
        return [str(line)]
    if len(line) <= max_width:
        return [line]
    tokens = line.split(" ")
    out: List[str] = []
    buf = ""
    for tok in tokens:
        if not buf:
            buf = tok
            continue
        candidate = buf + " " + tok
        if len(candidate) <= max_width:
            buf = candidate
        else:
            out.append(buf)
            buf = tok
    if buf:
        out.append(buf)
    return out


def _wrap_packet_text(text: str, max_width: int = PACKET_LINE_MAX_WIDTH) -> str:
    """전체 packet text를 줄 단위로 max_width 이하로 보장.

    Args:
        text: 멀티라인 텍스트.
        max_width: 한 줄 최대 길이.
    Returns:
        모든 줄이 max_width 이하인 텍스트 (개행 유지).
    """
    if not isinstance(text, str):
        text = str(text)
    out_lines: List[str] = []
    for raw in text.split("\n"):
        out_lines.extend(_wrap_packet_line(raw, max_width))
    return "\n".join(out_lines)


def _get_git_diff_files(base: str = "origin/main") -> List[str]:
    """git diff base...HEAD --name-only 결과를 그대로 반환.

    Args:
        base: 비교 기준 ref (기본 origin/main).
    Returns:
        변경 파일 경로 list. 외부 도구 부재 시 빈 list.
    Raises:
        없음.
    """
    git_path = shutil.which("git") or "git"
    diff_range = f"{base}...HEAD"
    try:
        r = subprocess.run(
            [git_path, "diff", diff_range, "--name-only"],
            capture_output=True, text=True, timeout=15,
            encoding="utf-8", errors="replace",
        )
        if r.returncode == 0:
            lines = [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]
            return lines
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return []


def _get_pr_number_from_url(pr_url: str) -> Optional[str]:
    """PR URL에서 PR 번호를 추출한다.

    Args:
        pr_url: https://github.com/<owner>/<repo>/pull/<num> 형식 URL.
    Returns:
        PR 번호 문자열 또는 None.
    """
    if not isinstance(pr_url, str) or not pr_url:
        return None
    m = re.search(r"/pull/(\d+)", pr_url)
    if m:
        return m.group(1)
    return None


def _collect_packet_evidence(
    state: Dict[str, Any],
    acceptance_request: Optional[Dict[str, Any]] = None,
    base_ref: str = "origin/main",
) -> Dict[str, Any]:
    """실제 git/gh/state 데이터를 모아 packet 자료를 반환한다.

    gh CLI나 git이 없으면 해당 필드는 빈 문자열/빈 리스트로 채워지며 graceful degradation.

    Args:
        state: 활성 pipeline_state.
        acceptance_request: 있으면 nonce/승인 코드 포함, 없으면 "발급 전".
        base_ref: git diff 비교 기준 ref.
    Returns:
        dict with keys: pipeline_id, pr_url, pr_number, pr_head_sha,
            ci_run_id, actions_url, changed_files (list[str]),
            gate_status (dict), structured_ac (list),
            ac_fulfillment_table (list or None),
            acceptance_request (dict or None), generated_at.
    Raises:
        없음.
    """
    pipeline_id = str(state.get("pipeline_id", "") or "")
    pr_url = _get_current_pr_url() or ""
    pr_head_sha = _get_current_pr_head_sha() or ""
    ci_run_id = _get_pr_branch_ci_run_id() or ""
    pr_number = _get_pr_number_from_url(pr_url) or ""
    changed_files = _get_git_diff_files(base=base_ref)
    actions_url = ""
    if ci_run_id:
        actions_url = (
            f"https://github.com/hojiyong2-commits/Pipeline/actions/runs/{ci_run_id}"
        )

    external_gates = state.get("external_gates") or {}
    # 버그 1 수정 (IMP-20260603-2E3D): pipeline_state.json의 external_gates는
    # {"technical": {...}, "oracle": {...}, ...} 형식이다. ".get("gates")"는 없는 키이므로
    # 항상 빈 dict를 반환해 모든 게이트 상태가 PENDING으로 표시되었다.
    gates = external_gates
    gate_status: Dict[str, str] = {}
    for key in ("technical", "oracle", "github_ci", "acceptance"):
        g = gates.get(key) or {}
        if isinstance(g, dict):
            gate_status[key] = str(g.get("status", "PENDING"))
        else:
            gate_status[key] = "PENDING"

    structured_ac = state.get("structured_acceptance_criteria") or []
    ac_table = _build_ac_fulfillment_table(state)

    return {
        "pipeline_id": pipeline_id,
        "pr_url": pr_url,
        "pr_number": pr_number,
        "pr_head_sha": pr_head_sha,
        "ci_run_id": ci_run_id,
        "actions_url": actions_url,
        "changed_files": changed_files,
        "gate_status": gate_status,
        "structured_ac": structured_ac,
        "ac_fulfillment_table": ac_table,
        "acceptance_request": acceptance_request,
        "generated_at": _now(),
    }


def _build_final_packet_content(evidence: Dict[str, Any]) -> str:
    """packet 텍스트를 생성한다. 120자/줄 제한 + 승인 코드 독립 줄.

    Args:
        evidence: _collect_packet_evidence 결과 dict.
    Returns:
        packet 본문 문자열 (markdown-friendly, 헤더는 일반 텍스트).
    Raises:
        없음.
    """
    pipeline_id = str(evidence.get("pipeline_id", "") or "")
    pr_url = str(evidence.get("pr_url", "") or "")
    pr_head_sha = str(evidence.get("pr_head_sha", "") or "")
    ci_run_id = str(evidence.get("ci_run_id", "") or "")
    actions_url = str(evidence.get("actions_url", "") or "")
    changed_files = list(evidence.get("changed_files") or [])
    gate_status = evidence.get("gate_status") or {}
    ac_table = evidence.get("ac_fulfillment_table")
    acceptance_request = evidence.get("acceptance_request")

    lines: List[str] = []
    lines.append("[최종 확인 안내]")
    lines.append("")
    lines.append("파이프라인:")
    lines.append(pipeline_id or "(없음)")
    lines.append("")
    lines.append("PR:")
    lines.append(pr_url or "(gh CLI 없음 또는 PR 없음)")
    lines.append("")
    lines.append("GitHub Actions:")
    lines.append(actions_url or "(CI run 없음)")
    lines.append("")
    lines.append("PR head SHA:")
    lines.append(pr_head_sha or "(조회 불가)")
    lines.append("")
    lines.append("CI run ID:")
    lines.append(ci_run_id or "(조회 불가)")
    lines.append("")
    lines.append("게이트 상태:")
    lines.append(f"Technical: {gate_status.get('technical', 'PENDING')}")
    lines.append(f"Oracle: {gate_status.get('oracle', 'PENDING')}")
    lines.append(f"GitHub CI: {gate_status.get('github_ci', 'PENDING')}")
    lines.append(f"User Acceptance: {gate_status.get('acceptance', 'PENDING')}")
    lines.append("")
    lines.append("변경 파일:")
    lines.append(f"총 {len(changed_files)}개")
    lines.append("")
    if changed_files:
        for path in changed_files:
            lines.append(path)
    else:
        lines.append("(git diff 결과 없음 또는 git CLI 없음)")
    lines.append("")
    lines.append("요구사항 충족표:")
    lines.append("")
    if ac_table:
        ac_block = _format_ac_fulfillment_output(ac_table)
        for ac_line in ac_block.split("\n"):
            lines.append(ac_line)
    else:
        lines.append("(structured AC 없음 — legacy 파이프라인)")
        lines.append("")
    lines.append("사용자가 확인할 것:")
    lines.append("")
    lines.append("1. PR 링크를 연다.")
    lines.append("2. GitHub Actions 자동 검사가 성공인지 본다.")
    lines.append("3. 요구사항 충족표를 본다.")
    lines.append("4. 결과물이 요청과 맞으면 승인 코드를 입력한다.")
    lines.append("5. 틀리면 거절 코드 뒤에 이유를 적는다.")
    lines.append("")
    lines.append("승인 코드:")
    if isinstance(acceptance_request, dict) and acceptance_request.get("nonce"):
        nonce = str(acceptance_request.get("nonce"))
        lines.append(f"ACCEPT-{pipeline_id}-{nonce}")
        lines.append("")
        lines.append("거절 예시:")
        lines.append(f"REJECT-{pipeline_id}-{nonce}: 이유")
    else:
        lines.append("승인 코드 발급 전 — gates request-accept를 먼저 실행하세요")
        lines.append("")
        lines.append("거절 예시:")
        lines.append("승인 코드 발급 전 — gates request-accept를 먼저 실행하세요")

    raw = "\n".join(lines)
    return _wrap_packet_text(raw, PACKET_LINE_MAX_WIDTH)


def _packet_output_path() -> Path:
    """현재 cwd 기준 human_acceptance_packet.md 경로를 반환한다.

    BASE_DIR은 pipeline.py 위치라 격리된 E2E 테스트에서 활성 cwd와 다를 수 있다.
    배포 환경에서는 보통 BASE_DIR == cwd이므로 결과는 동일하나, 테스트에서 cwd가
    다르면 그 cwd에 packet을 작성한다.
    """
    try:
        return Path(os.getcwd()) / HUMAN_ACCEPTANCE_PACKET_FILE
    except OSError:
        return BASE_DIR / HUMAN_ACCEPTANCE_PACKET_FILE


def _write_human_acceptance_packet(content: str) -> Path:
    """human_acceptance_packet.md를 작업 디렉터리에 저장한다.

    Args:
        content: packet 본문 문자열.
    Returns:
        저장 파일 절대 Path.
    """
    path = _packet_output_path()
    path.write_text(content, encoding="utf-8")
    return path


def _clean_pr_body_artifacts(
    pr_body: str, pipeline_id: str = "", current_nonce: str = ""
) -> str:
    """PR 본문에서 파이프라인 콘솔 아티팩트와 구 승인 코드를 정리한다.

    PIPELINE_FINAL_PACKET 블록 안은 건드리지 않음. 블록 밖의 아래 패턴을 제거한다:
    - [FINAL PACKET ...] 콘솔 덤프 라인
    - [PR 본문 자동 업데이트] / [새 코드 발급] / [재사용] 라인
    - ={20,} 구분선
    - [O] 승인하시려면 / [X] 거절하시려면 라인
    - "위 결과물을 확인하신 후" 라인
    - "다음 단계: python pipeline.py report update" 라인
    - 현재 nonce가 아닌 구 ACCEPT-<pipeline_id>-... 코드 라인

    Args:
        pr_body: 현재 PR 본문 텍스트.
        pipeline_id: 현재 파이프라인 ID (구 승인 코드 식별용).
        current_nonce: 현재 유효한 nonce (이 nonce를 포함한 코드는 보존).
    Returns:
        정리된 PR 본문 텍스트.
    """
    lines = pr_body.split("\n")
    cleaned: List[str] = []
    in_block = False
    start_marker = PIPELINE_FINAL_PACKET_START_MARKER
    end_marker = PIPELINE_FINAL_PACKET_END_MARKER
    # 아티팩트 패턴 목록 (블록 밖에서만 적용)
    _artifact_patterns = [
        r"\[FINAL PACKET",
        r"\[PR 본문 자동 업데이트\]",
        r"\[새 코드 발급\]",
        r"\[재사용\]",
        r"={20,}",
        r"\[O\]\s+승인하시려면",
        r"\[X\]\s+거절하시려면",
        r"위\s+결과물을\s+확인하신\s+후",
        r"다음\s+단계:\s+python\s+pipeline\.py\s+report\s+update",
        r"사용자\s+최종\s+확인\s+요청",
        r"^\s*PR:\s*\(gh",
        r"^\s*CI\s+run:\s*\(없음\)",
    ]
    for line in lines:
        if start_marker in line:
            in_block = True
            cleaned.append(line)
            continue
        if end_marker in line:
            in_block = False
            cleaned.append(line)
            continue
        if in_block:
            cleaned.append(line)
            continue
        # 블록 밖: 아티팩트 패턴 검사
        artifact = False
        for pat in _artifact_patterns:
            if re.search(pat, line):
                artifact = True
                break
        # 구 승인/거절 코드 라인 제거 (현재 nonce 제외)
        if not artifact and pipeline_id:
            pid_esc = re.escape(pipeline_id)
            accept_m = re.search(rf"ACCEPT-{pid_esc}-([A-Z0-9]{{4,16}})", line)
            if accept_m and accept_m.group(1) != current_nonce:
                artifact = True
            if not artifact:
                reject_m = re.search(rf"REJECT-{pid_esc}-([A-Z0-9]{{4,16}})", line)
                if reject_m and reject_m.group(1) != current_nonce:
                    artifact = True
        if not artifact:
            cleaned.append(line)
    # 연속 빈 줄 3개 이상 → 2개로 정리
    result = re.sub(r"\n{3,}", "\n\n", "\n".join(cleaned))
    return result


def _replace_pr_body_packet_block(pr_body: str, packet_content: str) -> str:
    """PR 본문에서 PIPELINE_FINAL_PACKET_START/END 블록을 교체하고, 없으면 끝에 추가.

    Args:
        pr_body: 현재 PR 본문 텍스트.
        packet_content: 새 packet 본문.
    Returns:
        교체/추가된 PR 본문 텍스트.
    """
    start = PIPELINE_FINAL_PACKET_START_MARKER
    end = PIPELINE_FINAL_PACKET_END_MARKER
    new_block = f"{start}\n{packet_content}\n{end}"
    if pr_body is None:
        pr_body = ""
    if start in pr_body and end in pr_body:
        pattern = re.compile(
            re.escape(start) + r".*?" + re.escape(end),
            re.DOTALL,
        )
        return pattern.sub(new_block, pr_body, count=1)
    if pr_body and not pr_body.endswith("\n"):
        pr_body += "\n"
    return pr_body + "\n" + new_block + "\n"


def _gh_edit_pr_body(new_body: str) -> bool:
    """gh CLI로 현재 PR 본문을 갱신한다. gh 없으면 False 반환.

    Args:
        new_body: 새 PR 본문.
    Returns:
        True 갱신 성공, False gh 없음 또는 오류.
    """
    gh_path = shutil.which("gh")
    if not gh_path:
        return False
    try:
        r = subprocess.run(
            [gh_path, "pr", "edit", "--body", new_body],
            capture_output=True, text=True, timeout=30,
            encoding="utf-8", errors="replace",
        )
        return r.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def _cmd_report_final_packet(args: argparse.Namespace) -> None:
    """report final-packet 핸들러.

    실제 git/gh/state/acceptance_request 자료를 모아 human_acceptance_packet.md를 작성한다.
    acceptance_request.json이 있으면 승인 코드를 포함하고, 없으면 "승인 코드 발급 전" 라인을
    출력한다. 콘솔에 요약을 출력한다.

    IMP-20260603-9934 MT-3: PENDING + packet_sha256 상태에서 기본 차단.
    --force-new-request 옵션으로 해제 가능 (기존 PENDING을 EXPIRED로 처리 후 재생성).
    _cmd_gates_accept는 NEVER 이 함수를 호출하지 않는다.
    """
    state = _require_state()
    state = _ensure_v210_fields(state)
    base_ref = str(getattr(args, "base", "origin/main") or "origin/main")
    force_new_request = bool(getattr(args, "force_new_request", False))

    # IMP-20260603-9934 MT-3: PENDING + packet_sha256 있으면 재생성 차단
    acceptance_request = _load_acceptance_request()
    if (
        not force_new_request
        and acceptance_request is not None
        and acceptance_request.get("status") == "PENDING"
        and acceptance_request.get("packet_sha256") is not None
    ):
        req_id = acceptance_request.get("request_id", "(unknown)")
        _die(
            "[FINAL PACKET FREEZE] acceptance_request.json이 PENDING 상태입니다. "
            "packet 재생성이 차단됩니다.\n"
            f"  request_id: {req_id}\n"
            "  이미 발급된 승인 코드로 진행하거나,\n"
            "  강제 재생성이 필요하면 --force-new-request 옵션을 사용하세요:\n"
            "  python pipeline.py report final-packet --force-new-request"
        )

    # IMP-20260603-9934 MT-3: --force-new-request 시 기존 PENDING을 EXPIRED로 처리
    if force_new_request and acceptance_request is not None and acceptance_request.get("status") == "PENDING":
        acceptance_request["status"] = "EXPIRED"
        acceptance_request["expired_reason"] = "final_packet_regenerated"
        try:
            with open(ACCEPTANCE_REQUEST_FILE, "w", encoding="utf-8") as _fh:
                json.dump(acceptance_request, _fh, ensure_ascii=False, indent=2)
            print(YELLOW(
                "  [--force-new-request] 기존 PENDING 요청이 EXPIRED로 처리되었습니다. "
                "새 packet을 생성합니다. 새 승인 코드가 필요하면 gates request-accept를 재실행하세요."
            ))
        except OSError:
            pass
        # 재로드 (EXPIRED 상태 반영)
        acceptance_request = _load_acceptance_request()
    evidence = _collect_packet_evidence(
        state, acceptance_request=acceptance_request, base_ref=base_ref
    )
    content = _build_final_packet_content(evidence)
    out_path = _write_human_acceptance_packet(content)

    pid = evidence.get("pipeline_id") or "(unknown)"
    pr_url = evidence.get("pr_url") or "(gh 없음)"
    ci_run_id = evidence.get("ci_run_id") or "(없음)"
    n_files = len(evidence.get("changed_files") or [])
    has_code = bool(acceptance_request and acceptance_request.get("nonce"))
    code_label = "포함" if has_code else "발급 전"

    print(GREEN("\n[FINAL PACKET 작성 완료]"))
    print(f"  파일: {_display_path(out_path)}")
    print(f"  파이프라인: {pid}")
    print(f"  PR: {pr_url}")
    print(f"  CI run: {ci_run_id}")
    print(f"  변경 파일: {n_files}개")
    print(f"  승인 코드: {code_label}")
    print(
        "  다음 단계: python pipeline.py report update-pr-body 후 "
        "gates request-accept --evidence <결과물>\n"
    )


def _cmd_report_update_pr_body(args: argparse.Namespace) -> None:
    """report update-pr-body 핸들러.

    human_acceptance_packet.md를 읽어 PR 본문의 PIPELINE_FINAL_PACKET 블록을 교체한다.
    블록이 없으면 PR 본문 끝에 추가한다. gh CLI가 없으면 안내 후 graceful skip.

    IMP-20260603-9934 MT-3: PENDING + packet_sha256 상태에서 기본 차단.
    --force-new-request 옵션으로 해제 가능.
    """
    force_new_request = bool(getattr(args, "force_new_request", False))

    # IMP-20260603-9934 MT-3: PENDING + packet_sha256 있으면 PR 본문 업데이트 차단
    _acceptance_req_upd = _load_acceptance_request()
    if (
        not force_new_request
        and _acceptance_req_upd is not None
        and _acceptance_req_upd.get("status") == "PENDING"
        and _acceptance_req_upd.get("packet_sha256") is not None
    ):
        req_id = _acceptance_req_upd.get("request_id", "(unknown)")
        _die(
            "[FINAL PACKET FREEZE] acceptance_request.json이 PENDING 상태입니다. "
            "PR 본문 업데이트가 차단됩니다.\n"
            f"  request_id: {req_id}\n"
            "  이미 발급된 승인 코드로 진행하거나,\n"
            "  강제 갱신이 필요하면 --force-new-request 옵션을 사용하세요:\n"
            "  python pipeline.py report update-pr-body --force-new-request"
        )

    packet_path = _packet_output_path()
    if not packet_path.exists():
        _die(
            "[REPORT UPDATE-PR-BODY] human_acceptance_packet.md가 없습니다. "
            "먼저 'python pipeline.py report final-packet'을 실행하세요."
        )
    packet_content = packet_path.read_text(encoding="utf-8", errors="replace")

    if not shutil.which("gh"):
        print(YELLOW(
            "\n[REPORT UPDATE-PR-BODY] gh CLI가 없어 PR 본문을 갱신하지 않습니다.\n"
            "  현재 packet 내용은 다음 파일에 보존됩니다: "
            f"{_display_path(packet_path)}\n"
        ))
        return

    current_body = _get_pr_body_text() or ""

    # 버그 2+3 수정 (IMP-20260603-2E3D): 블록 교체 전 콘솔 아티팩트와 구 승인 코드 제거
    try:
        state = _require_state()
    except SystemExit:
        state = {}
    acceptance_req = _load_acceptance_request()
    current_nonce = (
        acceptance_req.get("nonce", "") if isinstance(acceptance_req, dict) else ""
    )
    pipeline_id_str = str(state.get("pipeline_id", "") if state else "") or ""
    cleaned_body = _clean_pr_body_artifacts(current_body, pipeline_id_str, current_nonce)

    new_body = _replace_pr_body_packet_block(cleaned_body, packet_content)
    ok = _gh_edit_pr_body(new_body)
    if not ok:
        print(YELLOW(
            "\n[REPORT UPDATE-PR-BODY] PR 본문 갱신에 실패했습니다. "
            "PR이 열려 있고 gh 인증이 유효한지 확인하세요.\n"
        ))
        return
    print(GREEN("\n[PR 본문 갱신 완료] PIPELINE_FINAL_PACKET 블록이 최신 packet으로 교체되었습니다.\n"))


def cmd_report(args: argparse.Namespace) -> None:
    """report 서브커맨드 디스패처 — final-packet | update-pr-body."""
    action = getattr(args, "report_action", None)
    if action == "final-packet":
        _cmd_report_final_packet(args)
        return
    if action == "update-pr-body":
        _cmd_report_update_pr_body(args)
        return
    _die(
        "[REPORT ERROR] report 서브명령이 필요합니다. final-packet|update-pr-body 중 선택하세요.",
        exit_code=2,
    )


def cmd_codex(args: argparse.Namespace) -> None:
    """Codex compatibility hooks for running the same mandatory pipeline."""
    action = args.codex_action
    if action != "doctor":
        _die(f"unknown codex action: {action}", exit_code=2)

    required_files = [
        "pipeline.py",
        "CLAUDE.md",
        ".claude/commands/task.md",
        ".claude/agents/pm-planner-agent.md",
        ".claude/agents/pipeline-manager-agent.md",
        ".codex/skills/pipeline-task/SKILL.md",
    ]
    checks: List[Dict[str, Any]] = []

    for rel in required_files:
        path = BASE_DIR / rel
        checks.append({
            "name": f"file:{rel}",
            "status": "PASS" if path.exists() else "FAIL",
            "message": "exists" if path.exists() else "missing",
        })

    phase_checks = [
        ("pm_planner phase", "pm_planner" in AGENT_RUN_PHASES),
        ("pipeline_manager phase", "pipeline_manager" in AGENT_RUN_PHASES),
        ("pm planner agent id", PHASE_AGENT_IDS.get("pm_planner") == "pm-planner-agent"),
        ("pipeline manager agent id", PHASE_AGENT_IDS.get("pipeline_manager") == "pipeline-manager-agent"),
        ("pm receipt run phase", PHASE_RECEIPT_RUN_PHASES.get("pm") == "pm_planner"),
    ]
    for name, ok in phase_checks:
        checks.append({
            "name": name,
            "status": "PASS" if ok else "FAIL",
            "message": "ok" if ok else "not configured",
        })

    skill_path = BASE_DIR / ".codex/skills/pipeline-task/SKILL.md"
    if skill_path.exists():
        skill_text = skill_path.read_text(encoding="utf-8", errors="replace")
        for token in (
            "Three-Gate",
            "No Shortcut Rule",
            "gpt-5.5",
            "pm_planner",
            "pipeline_manager",
            "manager_handoff.xml",
            "anti_gaming_read",
            "Architect complete",
        ):
            ok = token in skill_text
            checks.append({
                "name": f"skill-token:{token}",
                "status": "PASS" if ok else "FAIL",
                "message": "present" if ok else "missing",
            })

    status = "PASS" if all(item["status"] == "PASS" for item in checks) else "FAIL"

    # IMP-20260517-30DD MT-1: 14개 provider/model 진단 필드 수집
    import shutil as _shutil
    import os as _os

    _openai_api_key_raw: str = _os.environ.get("OPENAI_API_KEY", "")
    _openai_api_key_present: bool = bool(_openai_api_key_raw)
    _openai_api_key_format_valid: bool = (
        _openai_api_key_present and _openai_api_key_raw.startswith("sk-")
    )
    _codex_cli_installed: bool = _shutil.which("codex") is not None
    _codex_cli_version: Optional[str] = None
    _codex_cli_auth_status: str = "UNKNOWN"
    _codex_cli_auth_method: Optional[str] = None
    _codex_cli_jsonl_metadata_support: bool = False

    if _codex_cli_installed:
        try:
            _ver_proc = subprocess.run(
                ["codex", "--version"],
                capture_output=True,
                timeout=10,
            )
            if _ver_proc.returncode == 0:
                _codex_cli_version = _ver_proc.stdout.decode("utf-8", errors="replace").strip()
        except Exception:
            pass
        try:
            _auth_proc = subprocess.run(
                ["codex", "whoami", "--json"],
                capture_output=True,
                timeout=10,
            )
            _auth_out = _auth_proc.stdout.decode("utf-8", errors="replace").strip()
            if _auth_proc.returncode == 0:
                _codex_cli_auth_status = "OK"
                try:
                    _auth_data = json.loads(_auth_out)
                    _codex_cli_auth_method = _auth_data.get("auth_method")
                except Exception:
                    _codex_cli_auth_method = "unknown"
            else:
                _codex_cli_auth_status = "FAIL"
        except Exception:
            _codex_cli_auth_status = "UNKNOWN"

        # JSONL metadata support: codex exec --json으로 model 필드 노출 여부 추정
        # conservative: 버전 파싱 가능 시 True로 마킹 (실제 확인은 codex-run 시도에서)
        _codex_cli_jsonl_metadata_support = _codex_cli_version is not None

    # model_availability: openai-api 키 유효 또는 codex-cli 인증 OK 중 하나면 AVAILABLE
    _model_availability: str
    if _openai_api_key_format_valid:
        _model_availability = "AVAILABLE_VIA_OPENAI_API"
    elif _codex_cli_installed and _codex_cli_auth_status == "OK":
        _model_availability = "AVAILABLE_VIA_CODEX_CLI"
    elif _codex_cli_installed:
        _model_availability = "CODEX_CLI_AUTH_REQUIRED"
    else:
        _model_availability = "UNAVAILABLE"

    # pipeline_state에서 마지막 codex review 상태 읽기
    _last_review_stage: Optional[str] = None
    _last_review_result: Optional[str] = None
    _last_review_model_verified: Optional[bool] = None
    _attempt_budget_remaining: int = CODEX_ATTEMPT_BUDGET_TOTAL
    _setup_blockers: List[str] = []

    try:
        _state_path = STATE_FILE
        if _state_path.exists():
            _state_data = json.loads(_state_path.read_text(encoding="utf-8", errors="replace"))
            _codex_log: List[Dict[str, Any]] = _state_data.get("codex_attempt_log") or []
            if _codex_log:
                _last_entry = _codex_log[-1]
                _last_review_stage = _last_entry.get("stage")
                _last_review_result = _last_entry.get("result")
                _last_review_model_verified = _last_entry.get("actual_model_verified")
                _attempt_budget_remaining = max(0, CODEX_ATTEMPT_BUDGET_TOTAL - len(_codex_log))
    except Exception:
        pass

    if not _openai_api_key_present and not _codex_cli_installed:
        _setup_blockers.append("OPENAI_API_KEY 미설정 + codex CLI 미설치")
    elif not _openai_api_key_format_valid and not _codex_cli_installed:
        _setup_blockers.append("OPENAI_API_KEY 형식 불일치 + codex CLI 미설치")
    if _codex_cli_installed and _codex_cli_auth_status != "OK" and not _openai_api_key_format_valid:
        _setup_blockers.append("codex CLI 인증 필요 (AUTH_REQUIRED)")
    if _attempt_budget_remaining == 0:
        _setup_blockers.append("attempt budget 소진 — MANUAL_SETUP_REQUIRED 상태")

    _provider_available: bool = _model_availability.startswith("AVAILABLE")

    # 14개 진단 필드 payload에 포함
    _provider_fields: Dict[str, Any] = {
        "provider_available": _provider_available,
        "openai_api_key_present": _openai_api_key_present,
        "openai_api_key_format_valid": _openai_api_key_format_valid,
        "codex_cli_installed": _codex_cli_installed,
        "codex_cli_version": _codex_cli_version,
        "codex_cli_auth_status": _codex_cli_auth_status,
        "codex_cli_auth_method": _codex_cli_auth_method,
        "codex_cli_jsonl_metadata_support": _codex_cli_jsonl_metadata_support,
        "model_availability": _model_availability,
        "last_review_stage": _last_review_stage,
        "last_review_result": _last_review_result,
        "last_review_model_verified": _last_review_model_verified,
        "attempt_budget_remaining": _attempt_budget_remaining,
        "setup_blockers": _setup_blockers,
    }

    payload = {
        "status": status,
        "checks": checks,
        "provider_diagnostics": _provider_fields,
        "quick_start": [
            "Use gpt-5.5 for every LLM role; stop instead of silently downgrading.",
            "python pipeline.py new --type IMP --desc \"...\"",
            "python pipeline.py agent start --phase pm_planner",
            "python pipeline.py agent finish --run-id <planner_run_id> --token <token> --output-file step_plan.xml",
            "python pipeline.py agent start --phase pipeline_manager",
            "python pipeline.py agent finish --run-id <manager_run_id> --token <token> --output-file manager_handoff.xml",
            "python pipeline.py done --phase pm --report-file step_plan.xml --decomp --clarification --roadmap --planner-run-id <planner_run_id> --manager-run-id <manager_run_id> --manager-report manager_handoff.xml",
        ],
    }

    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        label = GREEN("[CODEX DOCTOR PASS]") if status == "PASS" else RED("[CODEX DOCTOR FAIL]")
        print(label)
        for item in checks:
            mark = "OK" if item["status"] == "PASS" else "FAIL"
            print(f"  {mark} {item['name']}: {item['message']}")
        print()
        print("[ Provider Diagnostics ]")
        for k, v in _provider_fields.items():
            print(f"  {k}: {v}")
        print("\nCodex에서 시작할 때는 `/task` 문자열 대신 `.codex/skills/pipeline-task/SKILL.md` 지침을 먼저 읽고, 위 quick_start 순서를 따르세요.")

    if status != "PASS":
        sys.exit(1)


def cmd_preflight(args: argparse.Namespace) -> None:
    """파이프라인 사전 점검 — preflight_report.json 생성.

    수집 항목:
    - related_files: git diff HEAD~1 --name-only 결과
    - recent_pipelines_same_file: pipeline_state / pipeline_history 에서 동일 파일 포함 최근 7개 파이프라인
    - tool_facts.ruff_rules_verified / ruff_rules_not_found: ruff rule [code] 반환 코드로 판별
    - build_required / build_reason: 패키징 파일(.spec, requirements.txt, pyproject.toml, entrypoint) 변경 여부
    - writer_reader_pairs: _inject / scanner 패턴이 같은 파일에 공존하는 쌍 목록
    """
    pipeline_id: Optional[str] = getattr(args, "pipeline_id", None)
    ruff_codes_raw: str = getattr(args, "ruff_codes", "") or ""
    output_path_arg: Optional[str] = getattr(args, "output", None)

    # 1. active pipeline_id 결정
    if not pipeline_id:
        try:
            state_path = STATE_FILE
            if state_path.exists():
                state_data = json.loads(state_path.read_text(encoding="utf-8", errors="replace"))
                pipeline_id = state_data.get("pipeline_id") or "UNKNOWN"
            else:
                pipeline_id = "UNKNOWN"
        except Exception:
            pipeline_id = "UNKNOWN"

    # 2. related_files — git diff HEAD~1 --name-only
    related_files: List[str] = []
    try:
        proc = subprocess.run(
            ["git", "diff", "HEAD~1", "--name-only"],
            capture_output=True,
            cwd=str(BASE_DIR),
            timeout=15,
        )
        if proc.returncode == 0 and proc.stdout:
            related_files = [f.strip() for f in proc.stdout.decode("utf-8", errors="replace").strip().splitlines() if f.strip()]
    except Exception as exc:
        logging.getLogger(__name__).warning("git diff failed: %s", exc)

    # 3. recent_pipelines_same_file — pipeline_history 및 현재 state 검색
    recent_pipelines_same_file: List[Dict[str, Any]] = []
    try:
        history_dir = BASE_DIR / "pipeline_history"
        candidate_state_files: List[Path] = []
        state_path = STATE_FILE
        if state_path.exists():
            candidate_state_files.append(state_path)
        if history_dir.exists():
            candidate_state_files.extend(sorted(history_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:20])

        seen_pids: set = set()
        for sf in candidate_state_files:
            if len(recent_pipelines_same_file) >= 7:
                break
            try:
                sdata = json.loads(sf.read_text(encoding="utf-8", errors="replace"))
                pid = sdata.get("pipeline_id", "")
                if not pid or pid in seen_pids:
                    continue
                # extract all file references from micro_tasks and scope manifests
                mt_files: List[str] = []
                for mt in sdata.get("micro_tasks", []):
                    mt_files.extend(mt.get("files", []))
                if related_files and mt_files:
                    overlap = set(related_files) & set(mt_files)
                    if overlap:
                        seen_pids.add(pid)
                        recent_pipelines_same_file.append({
                            "pipeline_id": pid,
                            "description": sdata.get("description", ""),
                            "overlapping_files": sorted(overlap),
                        })
            except Exception:  # nosec B112 — 개별 파이프라인 파일 파싱 오류는 무시하고 계속
                continue
    except Exception as exc:
        logging.getLogger(__name__).warning("pipeline history scan failed: %s", exc)

    # 4. tool_facts — ruff rule [code] 검증
    ruff_rules_verified: List[str] = []
    ruff_rules_not_found: List[str] = []
    ruff_codes: List[str] = [c.strip() for c in ruff_codes_raw.split(",") if c.strip()]
    if not ruff_codes:
        # default set of commonly misunderstood rules
        ruff_codes = ["PLW0621", "E501", "B006", "SIM117"]
    for code in ruff_codes:
        try:
            ruff_proc = subprocess.run(
                ["ruff", "rule", code],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if ruff_proc.returncode == 0:
                ruff_rules_verified.append(code)
            else:
                ruff_rules_not_found.append(code)
        except FileNotFoundError:
            ruff_rules_not_found.append(code)
        except Exception as exc:
            logging.getLogger(__name__).warning("ruff rule check failed for %s: %s", code, exc)
            ruff_rules_not_found.append(code)

    # 5. build_required — 패키징 파일 변경 감지
    PACKAGING_PATTERNS = (".spec", "requirements.txt", "pyproject.toml", "setup.py", "setup.cfg", "MANIFEST.in")
    ENTRYPOINT_PATTERNS = ("main.py", "app.py", "entrypoint.py", "__main__.py")
    build_required: bool = False
    build_reason: str = "no packaging file changes detected"
    packaging_changed: List[str] = []
    for f in related_files:
        fname = Path(f).name
        if any(fname == pat or fname.endswith(pat) for pat in PACKAGING_PATTERNS):
            packaging_changed.append(f)
        elif fname in ENTRYPOINT_PATTERNS:
            packaging_changed.append(f)
    if packaging_changed:
        build_required = True
        build_reason = f"packaging/entrypoint files changed: {', '.join(packaging_changed)}"

    # 6. writer_reader_pairs — _inject + scanner 패턴이 같은 파일에 공존하는 쌍
    writer_reader_pairs: List[Dict[str, str]] = []
    try:
        inject_proc = subprocess.run(
            ["git", "grep", "-l", "_inject"],
            capture_output=True,
            cwd=str(BASE_DIR),
            timeout=15,
        )
        inject_files: List[str] = []
        if inject_proc.returncode == 0 and inject_proc.stdout:
            inject_files = [f.strip() for f in inject_proc.stdout.decode("utf-8", errors="replace").strip().splitlines() if f.strip()]

        scanner_proc = subprocess.run(
            ["git", "grep", "-l", "scanner"],
            capture_output=True,
            cwd=str(BASE_DIR),
            timeout=15,
        )
        scanner_files: List[str] = []
        if scanner_proc.returncode == 0 and scanner_proc.stdout:
            scanner_files = [f.strip() for f in scanner_proc.stdout.decode("utf-8", errors="replace").strip().splitlines() if f.strip()]

        # files where both writer (_inject) and reader (scanner) are present
        both = set(inject_files) & set(scanner_files)
        for f in sorted(both):
            writer_reader_pairs.append({"file": f, "writer_pattern": "_inject", "reader_pattern": "scanner"})
    except Exception as exc:
        logging.getLogger(__name__).warning("writer-reader grep failed: %s", exc)

    # 7. Assemble report
    report: Dict[str, Any] = {
        "schema_version": 1,
        "pipeline_id": pipeline_id,
        "generated_at": _now(),
        "related_files": related_files,
        "recent_pipelines_same_file": recent_pipelines_same_file,
        "tool_facts": {
            "ruff_rules_verified": ruff_rules_verified,
            "ruff_rules_not_found": ruff_rules_not_found,
        },
        "build_required": build_required,
        "build_reason": build_reason,
        "writer_reader_pairs": writer_reader_pairs,
    }

    out_path = Path(output_path_arg) if output_path_arg else BASE_DIR / "preflight_report.json"
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[PREFLIGHT OK] 보고서 저장: {out_path}")
        print(f"  관련 파일: {len(related_files)}개")
        print(f"  동일 파일 포함 이전 파이프라인: {len(recent_pipelines_same_file)}개")
        print(f"  ruff 규칙 확인됨: {ruff_rules_verified}")
        print(f"  ruff 규칙 미발견: {ruff_rules_not_found}")
        print(f"  빌드 필요: {build_required} ({build_reason})")
        print(f"  writer-reader 쌍: {len(writer_reader_pairs)}개")
    except OSError as exc:
        _die(f"preflight_report.json 저장 실패: {exc}", exit_code=2)


# ── Codex Review 내부 산출물 forbidden 기본값 (MT-2: IMP-20260516-A627) ───────

CODEX_DEFAULT_FORBIDDEN_PATTERNS: List[str] = [
    ".pipeline/",
    "pipeline_contracts/",
    "pipeline_outputs/",
    "pipeline_state",
    "build_report.xml",
    "qa_report.xml",
    "codex_review_result.json",
    "failure_packet.json",
    "module_handover_",
    "module_qa_",
    "module_design_",
    "scope_manifest_",
    "dev_handover.xml",
    "integration_report.xml",
    "manager_handoff.xml",
]

CODEX_DEEP_REVIEW_TRIGGERS: List[str] = [
    "pipeline.py",
    ".github/workflows/",
    "tests/",
    "CLAUDE.md",
    ".claude/",
]


def _collect_git_diff_meta(base_ref: str) -> Tuple[List[str], str]:
    """git diff --name-only 와 전체 diff SHA256을 수집한다.

    Args:
        base_ref: 비교 기준 브랜치/커밋 (예: 'main').

    Returns:
        Tuple[List[str], str]: (변경된 파일 목록, diff SHA256 16진수 문자열).
    """
    reviewed_files: List[str] = []
    diff_sha256: str = ""

    try:
        diff_names_proc = subprocess.run(
            ["git", "diff", "--name-only", f"{base_ref}...HEAD"],
            capture_output=True,
            cwd=str(BASE_DIR),
            timeout=15,
        )
        if diff_names_proc.returncode == 0 and diff_names_proc.stdout:
            names_text = diff_names_proc.stdout.decode("utf-8", errors="replace")
            reviewed_files = [f.strip() for f in names_text.strip().splitlines() if f.strip()]
    except Exception as exc:
        logging.getLogger(__name__).warning("git diff --name-only 실패: %s", exc)

    try:
        diff_full_proc = subprocess.run(
            ["git", "diff", f"{base_ref}...HEAD"],
            capture_output=True,
            cwd=str(BASE_DIR),
            timeout=30,
        )
        if diff_full_proc.returncode == 0 and diff_full_proc.stdout is not None:
            diff_sha256 = hashlib.sha256(diff_full_proc.stdout).hexdigest()
    except Exception as exc:
        logging.getLogger(__name__).warning("git diff (full) 실패: %s", exc)

    return reviewed_files, diff_sha256


def _compute_scope_files(
    reviewed_files: List[str],
    forbidden_patterns: Optional[List[str]] = None,
) -> Tuple[List[str], List[str]]:
    """scope stage용 allowed_files / forbidden_files 분류 함수.

    reviewed_files에서 forbidden_patterns에 해당하는 파일을 분리한다.

    Args:
        reviewed_files: git diff --name-only 결과 파일 목록.
        forbidden_patterns: 금지 패턴 목록. None이면 기본값 사용.

    Returns:
        Tuple[List[str], List[str]]: (allowed_files, forbidden_files).
    """
    if forbidden_patterns is None:
        forbidden_patterns = CODEX_DEFAULT_FORBIDDEN_PATTERNS

    allowed_files: List[str] = []
    forbidden_files_out: List[str] = []

    for filepath in (reviewed_files or []):
        is_forbidden = any(pattern in filepath for pattern in forbidden_patterns)
        if is_forbidden:
            forbidden_files_out.append(filepath)
        else:
            allowed_files.append(filepath)

    return allowed_files, forbidden_files_out


def _check_deep_review_required(reviewed_files: List[str]) -> bool:
    """code stage deep review 필요 여부 판단 함수.

    pipeline.py, .github/workflows/**, tests/**, CLAUDE.md, .claude/** 변경 시 REQUIRED.

    Args:
        reviewed_files: git diff 변경 파일 목록.

    Returns:
        bool: True이면 deep review REQUIRED.
    """
    for filepath in (reviewed_files or []):
        for trigger in CODEX_DEEP_REVIEW_TRIGGERS:
            if filepath.startswith(trigger) or trigger in filepath:
                return True
    return False


def cmd_review(args: argparse.Namespace) -> None:
    """Codex Review Gate — 코드 리뷰 결과 관리.

    subaction:
      codex       git diff 메타데이터를 수집하여 codex_review_result.json에 stage 기록 추가.
                  --stage: plan|scope|code|hygiene|pr|rca (필수)
                  --result: ACCEPT|REJECT|PENDING (기본값 PENDING)
                  --review-model: 리뷰 모델 (기본값 GPT-5.5, 다른 값 시 경고)
                  --reviewer: 리뷰어 식별자
                  --pipeline-id: 파이프라인 ID
                  history 배열에 이전 기록이 누적되고, 최신 기록이 top-level에 반영된다.
      codex-record 사용자의 실제 Codex review ACCEPT/REJECT 세션을 공식 기록으로 등록.
                   pr/rca stage 전용. 4중 검증 적용.
      status      codex_review_result.json에서 미해결 HIGH/CRITICAL findings 수 출력.
      resolve     특정 finding을 resolved=true로 표시.
    """
    review_action: str = getattr(args, "review_action", "") or ""
    base_ref: str = getattr(args, "base", "main") or "main"
    output_path_arg: Optional[str] = getattr(args, "output", None)
    finding_id: Optional[str] = getattr(args, "finding_id", None)
    resolution_file: Optional[str] = getattr(args, "resolution_file", None)

    review_result_path = Path(output_path_arg) if output_path_arg else BASE_DIR / "codex_review_result.json"

    if review_action == "codex":
        # MT-2: stage별 6-stage 확장 구현
        stage_arg: str = getattr(args, "stage", "") or ""
        result_arg: str = getattr(args, "result_value", "") or "PENDING"
        review_model_arg: str = getattr(args, "review_model", CODEX_REQUIRED_MODEL) or CODEX_REQUIRED_MODEL
        reviewer_arg: str = getattr(args, "reviewer", "unknown") or "unknown"
        pipeline_id_arg: str = getattr(args, "pipeline_id_arg", "") or ""
        pr_number_arg: Optional[str] = getattr(args, "pr_number", None)
        head_sha_arg: Optional[str] = getattr(args, "head_sha", None)
        notes_arg: Optional[str] = getattr(args, "notes", None)
        findings_arg: Optional[str] = getattr(args, "findings_file", None)

        # stage 필수 검증
        if not stage_arg or stage_arg.lower() not in CODEX_VALID_STAGES:
            _die(
                f"[REVIEW CODEX] --stage는 필수이며 허용 값: {', '.join(sorted(CODEX_VALID_STAGES))}. "
                f"현재 값: '{stage_arg}'",
                exit_code=2,
            )
            return
        stage: str = stage_arg.lower()

        # D1 수정: review codex는 ACCEPT/REJECT 직접 입력 금지 — PENDING 전용
        result_upper: str = result_arg.upper() if result_arg else "PENDING"
        if result_upper in {"ACCEPT", "REJECT"}:
            _die(
                "[PIPELINE ERROR] `review codex` 명령은 --result ACCEPT 또는 REJECT를 허용하지 않습니다. "
                "`review codex`는 메타데이터/PENDING 생성 전용입니다. "
                "실제 ACCEPT/REJECT 판정 기록은 `python pipeline.py review codex-record --stage <STAGE> --result ACCEPT|REJECT` 를 사용하세요.",
                exit_code=1,
            )
            return
        if result_upper not in CODEX_VALID_RESULTS:
            _die(
                f"[REVIEW CODEX] --result 허용 값: {', '.join(sorted(CODEX_VALID_RESULTS))}. "
                f"현재 값: '{result_arg}'",
                exit_code=2,
            )
            return

        # review_model 경고 (GPT-5.5 아닌 경우)
        if review_model_arg.strip() != CODEX_REQUIRED_MODEL:
            print(
                f"[REVIEW CODEX] 경고: review_model='{review_model_arg}'은 "
                f"'{CODEX_REQUIRED_MODEL}'이 아닙니다. "
                f"codex-record 시 검증이 실패할 수 있습니다."
            )

        # git diff 메타데이터 수집
        reviewed_files, diff_sha256 = _collect_git_diff_meta(base_ref)

        # pipeline_id 결정 (인자 > 활성 state에서 자동 추출)
        active_pipeline_id: str = pipeline_id_arg
        if not active_pipeline_id:
            try:
                st = _load_state()
                active_pipeline_id = st.get("pipeline_id", "") if st is not None else ""
            except Exception:
                active_pipeline_id = ""

        # scope stage: allowed_files / forbidden_files 자동 계산
        allowed_files_out: List[str] = []
        forbidden_files_out: List[str] = []
        if stage == "scope":
            allowed_files_out, forbidden_files_out = _compute_scope_files(reviewed_files)

        # code stage: deep review 필요 여부 확인
        deep_review_required: bool = False
        if stage == "code":
            deep_review_required = _check_deep_review_required(reviewed_files)
            if deep_review_required:
                print(
                    "[REVIEW CODEX] [REQUIRED] 신뢰 루트 파일 변경 감지 — "
                    "code stage deep review가 필수입니다: "
                    "pipeline.py/.github/workflows/**/tests/**/CLAUDE.md/.claude/** 중 하나 이상 변경됨."
                )

        # findings 로드 (파일에서 제공된 경우)
        extra_findings: List[Dict[str, Any]] = []
        if findings_arg:
            try:
                findings_path = Path(findings_arg)
                if findings_path.exists():
                    findings_data = json.loads(findings_path.read_text(encoding="utf-8", errors="replace"))
                    if isinstance(findings_data, list):
                        extra_findings = findings_data
                    elif isinstance(findings_data, dict) and "findings" in findings_data:
                        extra_findings = findings_data.get("findings", [])
            except Exception as exc:
                logging.getLogger(__name__).warning("findings 파일 로드 실패: %s", exc)

        # 기존 파일 로드 (history 누적을 위해)
        existing_history: List[Dict[str, Any]] = []
        existing_findings_preserved: List[Dict[str, Any]] = []
        if review_result_path.exists():
            try:
                existing_raw = json.loads(
                    review_result_path.read_text(encoding="utf-8", errors="replace")
                )
                existing_history = existing_raw.get("history", [])
                # 이전 top-level도 history로 보존
                if existing_raw.get("stage"):
                    existing_history.insert(0, {
                        "stage": existing_raw.get("stage"),
                        "result": existing_raw.get("result"),
                        "review_model": existing_raw.get("review_model"),
                        "reviewer": existing_raw.get("reviewer"),
                        "created_at": existing_raw.get("created_at", existing_raw.get("generated_at", "")),
                        "diff_sha256": existing_raw.get("diff_sha256", ""),
                    })
                existing_findings_preserved = existing_raw.get("findings", [])
            except Exception as exc:
                logging.getLogger(__name__).warning("기존 review 파일 로드 실패: %s", exc)

        # 신규 stage 기록 생성
        now_ts = _now()
        new_record: Dict[str, Any] = {
            "schema_version": 2,
            "pipeline_id": active_pipeline_id,
            "stage": stage,
            "review_type": "quick" if stage in {"plan", "scope", "hygiene"} else "deep",
            "result": result_upper,
            "reviewer": reviewer_arg,
            "review_model": review_model_arg.strip(),
            "base_ref": base_ref,
            "reviewed_files": reviewed_files,
            "diff_sha256": diff_sha256,
            "findings": extra_findings if extra_findings else existing_findings_preserved,
            "created_at": now_ts,
            "updated_at": now_ts,
        }

        # stage별 선택 필드 추가
        if pr_number_arg:
            new_record["pr_number"] = pr_number_arg
        if head_sha_arg:
            new_record["head_sha"] = head_sha_arg
        if notes_arg:
            new_record["notes"] = notes_arg
        if stage == "scope":
            new_record["allowed_files"] = allowed_files_out
            new_record["forbidden_files"] = forbidden_files_out
        if stage == "code" and deep_review_required:
            new_record["deep_review_required"] = True

        # history 배열 업데이트 (현재 stage의 이전 기록은 history로 이동)
        history_snapshot = {
            "stage": stage,
            "result": result_upper,
            "review_model": review_model_arg.strip(),
            "reviewer": reviewer_arg,
            "created_at": now_ts,
            "diff_sha256": diff_sha256,
        }
        # history에서 같은 stage의 기존 기록을 보존하고 새 기록을 앞에 추가
        updated_history: List[Dict[str, Any]] = [history_snapshot] + [
            h for h in existing_history
            if h.get("stage") != stage
        ]
        new_record["history"] = updated_history

        # schema v2 검증 (MT-1 validator 활용)
        try:
            _validate_codex_review_schema(new_record)
        except ValueError as ve:
            _die(f"[REVIEW CODEX] schema 검증 실패: {ve}", exit_code=2)
            return

        try:
            review_result_path.parent.mkdir(parents=True, exist_ok=True)
            review_result_path.write_text(
                json.dumps(new_record, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"[REVIEW CODEX] stage={stage} result={result_upper} 기록 완료: {review_result_path}")
            print(f"  검토 파일: {len(reviewed_files)}개")
            if diff_sha256:
                print(f"  diff SHA256: {diff_sha256[:16]}...")
            if stage == "scope":
                print(f"  허용 파일: {len(allowed_files_out)}개, 금지 파일: {len(forbidden_files_out)}개")
            if stage == "code" and deep_review_required:
                print("  [REQUIRED] 신뢰 루트 변경 — deep review 필수")
            print(f"  history 항목: {len(updated_history)}개")
        except OSError as exc:
            _die(f"codex_review_result.json 저장 실패: {exc}", exit_code=2)

    elif review_action == "status":
        if not review_result_path.exists():
            print(json.dumps({
                "schema_version": 2,
                "status": "NO_REVIEW_FILE",
                "unresolved_high_critical": 0,
                "findings": [],
            }, ensure_ascii=False, indent=2))
            return

        try:
            data = json.loads(review_result_path.read_text(encoding="utf-8", errors="replace"))
        except Exception as exc:
            _die(f"codex_review_result.json 읽기 실패: {exc}", exit_code=2)
            return

        findings: List[Dict[str, Any]] = data.get("findings", [])
        unresolved: List[Dict[str, Any]] = [
            f for f in findings
            if not f.get("resolved", False) and str(f.get("severity", "")).upper() in {"HIGH", "CRITICAL"}
        ]
        print(json.dumps({
            "schema_version": 2,
            "status": "OK",
            "reviewed_files": data.get("reviewed_files", []),
            "diff_sha256": data.get("diff_sha256", ""),
            "total_findings": len(findings),
            "unresolved_high_critical": len(unresolved),
            "unresolved_findings": unresolved,
        }, ensure_ascii=False, indent=2))

    elif review_action == "resolve":
        if not finding_id:
            _die("--id 파라미터가 필요합니다 (예: --id CR-001)", exit_code=2)
            return
        if not review_result_path.exists():
            _die(f"codex_review_result.json 파일 없음: {review_result_path}", exit_code=2)
            return

        try:
            data = json.loads(review_result_path.read_text(encoding="utf-8", errors="replace"))
        except Exception as exc:
            _die(f"codex_review_result.json 읽기 실패: {exc}", exit_code=2)
            return

        # optionally load resolution notes
        resolution_notes: str = ""
        if resolution_file:
            try:
                res_data = json.loads(Path(resolution_file).read_text(encoding="utf-8", errors="replace"))
                resolution_notes = str(res_data.get("resolution", ""))
            except Exception as exc:
                logging.getLogger(__name__).warning("resolution_file 읽기 실패: %s", exc)

        findings = data.get("findings", [])
        matched = False
        for f in findings:
            if str(f.get("id", "")) == finding_id:
                f["resolved"] = True
                f["resolved_at"] = _now()
                if resolution_notes:
                    f["resolution_notes"] = resolution_notes
                matched = True
                break

        if not matched:
            _die(f"finding id '{finding_id}' 없음", exit_code=2)
            return

        try:
            review_result_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[REVIEW RESOLVE] {finding_id} 해소 처리 완료")
        except OSError as exc:
            _die(f"codex_review_result.json 저장 실패: {exc}", exit_code=2)

    elif review_action == "codex-run":
        cmd_review_codex_run(args)

    elif review_action == "codex-record":
        # MT-3: codex-record는 별도 함수로 위임
        cmd_review_codex_record(args)

    else:
        _die(f"알 수 없는 review 하위 명령: {review_action}", exit_code=2)


def _get_current_head_sha() -> str:
    """현재 git HEAD commit SHA를 반환한다.

    Returns:
        str: 40자 hex SHA 문자열. 실패 시 빈 문자열.
    """
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            cwd=str(BASE_DIR),
            timeout=10,
        )
        if proc.returncode == 0 and proc.stdout:
            return proc.stdout.decode("utf-8", errors="replace").strip()
    except Exception as exc:
        logging.getLogger(__name__).warning("git rev-parse HEAD 실패: %s", exc)
    return ""


def _save_codex_attempt_log(state: Dict[str, Any], attempt_log: List[Dict[str, Any]]) -> None:
    """IMP-20260517-30DD MT-1: codex_attempt_log를 pipeline_state.json에 저장한다.

    Args:
        state: 현재 pipeline state 딕셔너리 (in-place 수정).
        attempt_log: 저장할 시도 로그 리스트.
    """
    state["codex_attempt_log"] = attempt_log
    state_path = STATE_FILE
    try:
        state_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        logging.getLogger(__name__).warning("codex_attempt_log 저장 실패: %s", exc)


def _codex_run_via_openai_api(
    api_key: str,
    prompt: str,
    schema: Dict[str, Any],
) -> Tuple[Dict[str, Any], str]:
    """IMP-20260517-30DD MT-1: openai-api provider로 Responses API를 호출한다.

    Args:
        api_key: OPENAI_API_KEY (절대 출력/로그에 포함 안 됨)
        prompt: 리뷰 프롬프트 텍스트
        schema: JSON schema dict

    Returns:
        Tuple[response_payload, provider_response_id]: API 응답 dict와 응답 ID

    Raises:
        SystemExit: API 오류 시 failure code 출력 후 exit(1)
    """
    import urllib.request  # noqa: PLC0415
    import urllib.error  # noqa: PLC0415

    body: Dict[str, Any] = {
        "model": CODEX_REQUIRED_MODEL_ID,  # API payload에는 소문자 exact model ID
        "instructions": (
            "You are a Codex technical reviewer. Find bugs, security issues, edge cases, "
            "and ways the pipeline could give an undeserved COMPLETE. Output only JSON."
        ),
        "input": prompt,
        "text": {
            "format": {
                "type": "json_schema",
                "name": "codex_review",
                "strict": True,
                "schema": schema,
            }
        },
    }

    req = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            response_payload: Dict[str, Any] = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw_body = exc.read().decode("utf-8", errors="replace")
        # secret redaction 적용
        raw_body_safe = _redact_secrets(raw_body[:2000])
        try:
            error_body: Any = json.loads(raw_body)
        except json.JSONDecodeError:
            error_body = {"raw": raw_body_safe}
        error_obj = error_body.get("error", {}) if isinstance(error_body, dict) else {}
        err_code = error_obj.get("code") if isinstance(error_obj, dict) else None
        err_msg = error_obj.get("message") if isinstance(error_obj, dict) else raw_body_safe[:500]
        http_code = exc.code
        if http_code == 401:
            failure_code = "AUTH_REQUIRED"
        elif http_code == 402:
            failure_code = "BILLING_REQUIRED"
        elif http_code == 404:
            failure_code = "MODEL_UNAVAILABLE"
        elif http_code == 429:
            failure_code = "RATE_LIMITED"
        elif http_code >= 500:
            failure_code = "PROVIDER_FAIL"
        else:
            failure_code = "PROVIDER_FAIL"
        print(
            f"[CODEX {failure_code}] OpenAI API HTTP {http_code}: {err_code or err_msg}. "
            f"{CODEX_FAILURE_CODES.get(failure_code, '')}",
            file=sys.stderr,
        )
        sys.exit(1)
    except Exception as exc:
        print(
            f"[CODEX PROVIDER_FAIL] OpenAI API 호출 실패: {exc}. "
            f"{CODEX_FAILURE_CODES['PROVIDER_FAIL']}",
            file=sys.stderr,
        )
        sys.exit(1)

    provider_response_id: str = str(response_payload.get("id", ""))
    return response_payload, provider_response_id


def _codex_run_via_codex_cli(
    prompt: str,
) -> Tuple[Dict[str, Any], str, Optional[str]]:
    """IMP-20260517-30DD MT-1: codex-cli provider로 실행하고 JSONL event stream에서 actual_model_id를 추출한다.

    Args:
        prompt: 리뷰 프롬프트 텍스트

    Returns:
        Tuple[parsed_output_dict, actual_model_id_or_sentinel, auth_method]:
            - parsed_output_dict: 모델 응답 JSON dict (또는 빈 dict)
            - actual_model_id: 추출된 model ID, 또는 "MODEL_METADATA_UNAVAILABLE"
            - auth_method: 인증 방식 (예: "browser_login", "api_key", None)

    Raises:
        SystemExit: codex-cli 호출 실패 시
    """
    # codex-cli 설치 여부 확인
    try:
        which_proc = subprocess.run(
            ["codex", "--version"],
            capture_output=True,
            shell=False,
            timeout=10,
        )
        if which_proc.returncode != 0:
            print(
                f"[CODEX PROVIDER_CAPABILITY_MISSING] codex-cli를 찾을 수 없습니다. "
                f"{CODEX_FAILURE_CODES['PROVIDER_CAPABILITY_MISSING']}",
                file=sys.stderr,
            )
            sys.exit(1)
    except FileNotFoundError:
        print(
            f"[CODEX PROVIDER_CAPABILITY_MISSING] codex-cli가 설치되지 않았습니다. "
            f"{CODEX_FAILURE_CODES['PROVIDER_CAPABILITY_MISSING']}",
            file=sys.stderr,
        )
        sys.exit(1)
    except Exception as exc:
        print(
            f"[CODEX PROVIDER_CAPABILITY_MISSING] codex-cli 버전 확인 실패: {exc}",
            file=sys.stderr,
        )
        sys.exit(1)

    # codex-cli 실행 (shell=False, list args — 절대 shell=True 사용 안 함)
    cmd_args: List[str] = [
        "codex", "exec",
        "-m", CODEX_REQUIRED_MODEL_ID,  # 반드시 gpt-5.5
        "--json",
        prompt[:4000],  # 프롬프트 길이 제한
    ]
    try:
        proc = subprocess.run(
            cmd_args,
            capture_output=True,
            shell=False,
            timeout=180,
        )
    except FileNotFoundError:
        print(
            "[CODEX PROVIDER_CAPABILITY_MISSING] codex 명령을 실행할 수 없습니다.",
            file=sys.stderr,
        )
        sys.exit(1)
    except subprocess.TimeoutExpired:
        print(
            "[CODEX PROVIDER_FAIL] codex-cli 실행 시간 초과(180초).",
            file=sys.stderr,
        )
        sys.exit(1)
    except Exception as exc:
        print(f"[CODEX PROVIDER_FAIL] codex-cli 실행 실패: {exc}", file=sys.stderr)
        sys.exit(1)

    if proc.returncode != 0:
        stderr_safe = _redact_secrets(proc.stderr.decode("utf-8", errors="replace")[:500])
        print(
            f"[CODEX PROVIDER_FAIL] codex-cli exit code {proc.returncode}: {stderr_safe}",
            file=sys.stderr,
        )
        sys.exit(1)

    # JSONL event stream 파싱 — actual_model_id 추출
    actual_model_id: str = "MODEL_METADATA_UNAVAILABLE"
    auth_method: Optional[str] = None
    parsed_output: Dict[str, Any] = {}

    stdout_text = proc.stdout.decode("utf-8", errors="replace")
    for line in stdout_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        # model metadata 추출 시도
        if event.get("type") in ("message_start", "response.created", "metadata"):
            meta_model = (
                event.get("model")
                or (event.get("message", {}) or {}).get("model")
                or (event.get("response", {}) or {}).get("model")
            )
            if meta_model and isinstance(meta_model, str):
                actual_model_id = meta_model.strip().lower()
        # auth_method 추출 시도
        if event.get("type") == "auth_info" or "auth_method" in event:
            auth_method = str(event.get("auth_method", ""))
        # 최종 output 파싱 시도
        if event.get("type") in ("message_stop", "response.done", "output"):
            output_text_raw = (
                event.get("output_text")
                or (event.get("message", {}) or {}).get("content", [{}])[0].get("text", "")
                if isinstance((event.get("message", {}) or {}).get("content"), list)
                else ""
            )
            if output_text_raw:
                try:
                    parsed_output = json.loads(output_text_raw)
                except json.JSONDecodeError:
                    pass

    return parsed_output, actual_model_id, auth_method


def cmd_review_codex_run(args: argparse.Namespace) -> None:
    """review codex-run — 실제 OpenAI Responses API 또는 Codex CLI로 Codex review를 실행하고 결과를 저장.

    IMP-20260517-30DD MT-1:
    - --provider {openai-api,codex-cli} 인자 추가 (기본값: openai-api)
    - openai-api: response_payload.get("model") → actual_model_id (actual_model_source="openai_api_response_object")
    - codex-cli: JSONL event stream에서 model 필드 추출. metadata 없으면 MODEL_METADATA_UNAVAILABLE
    - model output JSON의 review_model 필드는 actual evidence로 절대 인정 금지
    - provider_response_id 기록 (openai-api: response id 필드)
    - shell=False, list args만 사용
    - API key는 출력/로그/JSON에 절대 기록하지 않는다 (secret redaction 적용)
    - attempt budget: 전체 6회 / stage당 2회. 동일 provider/stage/failure_code 2회 → MANUAL_SETUP_REQUIRED
    - codex-cli MODEL_METADATA_UNAVAILABLE 시 openai-api로 1회 fallback (왕복 금지)
    """
    stage_arg: str = getattr(args, "stage", "") or ""
    base_ref: str = getattr(args, "base_ref", "main") or "main"
    output_path_arg: Optional[str] = getattr(args, "output", None)
    raw_output_path_arg: Optional[str] = getattr(args, "raw_output", None)
    provider_arg: str = str(getattr(args, "provider", "openai-api") or "openai-api").strip().lower()

    # 1. stage 검증
    if stage_arg not in CODEX_VALID_STAGES:
        _die(
            f"[CODEX FAIL] --stage 값이 유효하지 않습니다: '{stage_arg}'. "
            f"허용값: {', '.join(sorted(CODEX_VALID_STAGES))}",
            exit_code=1,
        )
        return

    # 2. provider 검증
    valid_providers = {"openai-api", "codex-cli"}
    if provider_arg not in valid_providers:
        _die(
            f"[CODEX FAIL] --provider 값이 유효하지 않습니다: '{provider_arg}'. "
            f"허용값: {', '.join(sorted(valid_providers))}",
            exit_code=1,
        )
        return

    # 3. attempt budget 확인 및 갱신
    state = _load() or {}
    attempt_log: List[Dict[str, Any]] = state.get("codex_attempt_log", [])
    if not isinstance(attempt_log, list):
        attempt_log = []

    total_attempts = len(attempt_log)
    stage_attempts = [a for a in attempt_log if a.get("stage") == stage_arg]
    stage_attempt_count = len(stage_attempts)

    if total_attempts >= CODEX_ATTEMPT_BUDGET_TOTAL:
        print(
            f"[CODEX MANUAL_SETUP_REQUIRED] 전체 attempt budget({CODEX_ATTEMPT_BUDGET_TOTAL}회) 초과. "
            f"현재 누적: {total_attempts}회. {CODEX_FAILURE_CODES['MANUAL_SETUP_REQUIRED']}",
            file=sys.stderr,
        )
        sys.exit(1)

    if stage_attempt_count >= CODEX_ATTEMPT_BUDGET_PER_STAGE:
        # 동일 stage에서 같은 failure_code 2회 반복 확인
        last_failure = None
        for a in reversed(stage_attempts):
            if a.get("failure_code"):
                last_failure = a.get("failure_code")
                break
        print(
            f"[CODEX MANUAL_SETUP_REQUIRED] stage={stage_arg} attempt budget({CODEX_ATTEMPT_BUDGET_PER_STAGE}회) 초과. "
            f"마지막 실패 코드: {last_failure or 'N/A'}. {CODEX_FAILURE_CODES['MANUAL_SETUP_REQUIRED']}",
            file=sys.stderr,
        )
        sys.exit(1)

    # 4. 현재 HEAD SHA 수집
    current_head_sha: str = _get_current_head_sha()

    # 5. git diff 수집
    diff_text: str = ""
    try:
        diff_proc = subprocess.run(
            ["git", "diff", base_ref, "HEAD"],
            capture_output=True,
            shell=False,
            cwd=str(BASE_DIR),
            timeout=30,
        )
        if diff_proc.returncode == 0:
            diff_text = diff_proc.stdout.decode("utf-8", errors="replace")
        else:
            diff_text = f"[git diff 실패: returncode={diff_proc.returncode}]"
    except Exception as exc:
        diff_text = f"[git diff 예외: {exc}]"

    # diff sha256 계산
    diff_bytes: bytes = diff_text.encode("utf-8")
    current_diff_sha: str = hashlib.sha256(diff_bytes).hexdigest()

    # 6. 프롬프트 구성 (API key 절대 미포함)
    prompt = (
        f"Stage: {stage_arg}\n"
        f"Base ref: {base_ref}\n"
        f"Diff (truncated to 8000 chars):\n"
        f"{diff_text[:8000]}\n\n"
        "Review the diff above for bugs, security issues, edge cases, and incomplete "
        "implementations. Output only JSON matching the required schema."
    )

    schema: Dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "result": {"type": "string", "enum": ["ACCEPT", "REJECT"]},
            "review_model": {"type": "string"},
            "diff_sha256": {"type": "string"},
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
            "required_actions": {"type": "array", "items": {"type": "string"}},
            "return_phase": {"type": ["string", "null"]},
        },
        "required": ["result", "review_model", "diff_sha256", "summary", "findings", "required_actions", "return_phase"],
    }

    # 7. provider별 실행 + actual_model_id 추출
    actual_model_id: str = ""
    actual_model_source: str = ""
    actual_model_verified: bool = False
    provider_response_id: str = ""
    provider_exit_code: int = 0
    auth_method: Optional[str] = None
    response_payload: Dict[str, Any] = {}
    parsed: Dict[str, Any] = {}
    effective_provider: str = provider_arg

    if provider_arg == "openai-api":
        # openai-api: API key 필수
        api_key, _api_key_source = _openai_api_key()
        if not api_key:
            print(
                f"[CODEX SETUP_REQUIRED] OPENAI_API_KEY 환경변수가 설정되지 않았습니다.\n"
                f"  설정 방법: $env:OPENAI_API_KEY = 'sk-...' (PowerShell)\n"
                f"  또는: setx OPENAI_API_KEY sk-... (영구 설정, 터미널 재시작 필요)\n"
                f"  {CODEX_FAILURE_CODES['SETUP_REQUIRED']}",
                file=sys.stderr,
            )
            # attempt 기록
            attempt_log.append({
                "provider": provider_arg, "stage": stage_arg,
                "failure_code": "SETUP_REQUIRED", "ts": _now(),
            })
            _save_codex_attempt_log(state, attempt_log)
            sys.exit(1)

        response_payload, provider_response_id = _codex_run_via_openai_api(
            api_key=api_key,
            prompt=prompt,
            schema=schema,
        )
        # actual_model_id는 response_payload.get("model") — provider-level evidence
        raw_actual = response_payload.get("model", "")
        actual_model_id = str(raw_actual).strip().lower() if raw_actual else ""
        actual_model_source = "openai_api_response_object"
        actual_model_verified = (actual_model_id == CODEX_REQUIRED_MODEL_ID)

        # 모델 응답 JSON 파싱
        output_text = _extract_response_output_text(response_payload)
        try:
            parsed = json.loads(output_text)
        except json.JSONDecodeError as exc:
            print(
                f"[CODEX PROVIDER_OUTPUT_INVALID] 모델 응답이 유효한 JSON이 아닙니다: {exc}",
                file=sys.stderr,
            )
            sys.exit(1)

    elif provider_arg == "codex-cli":
        # codex-cli: JSONL event stream에서 model 추출
        parsed, actual_model_id_raw, auth_method = _codex_run_via_codex_cli(prompt=prompt)
        actual_model_id = actual_model_id_raw if actual_model_id_raw != "MODEL_METADATA_UNAVAILABLE" else ""
        actual_model_source = "codex_cli_jsonl_metadata"
        effective_provider = "codex-cli"

        if actual_model_id_raw == "MODEL_METADATA_UNAVAILABLE":
            # codex-cli가 metadata를 노출하지 않음 → openai-api로 1회 fallback (왕복 금지)
            print(
                "[CODEX MODEL_METADATA_UNAVAILABLE] codex-cli가 actual model metadata를 제공하지 않습니다. "
                "openai-api로 1회 fallback을 시도합니다.",
                file=sys.stderr,
            )
            # fallback 시도 전 openai-api 버짓 확인
            fallback_attempts = [a for a in attempt_log if a.get("provider") == "openai-api" and a.get("stage") == stage_arg]
            if len(fallback_attempts) >= CODEX_ATTEMPT_BUDGET_PER_STAGE:
                print(
                    f"[CODEX MANUAL_SETUP_REQUIRED] openai-api fallback도 budget 초과. "
                    f"{CODEX_FAILURE_CODES['MANUAL_SETUP_REQUIRED']}",
                    file=sys.stderr,
                )
                sys.exit(1)
            api_key, _api_key_source = _openai_api_key()
            if not api_key:
                print(
                    f"[CODEX SETUP_REQUIRED] fallback openai-api에도 OPENAI_API_KEY가 없습니다. "
                    f"{CODEX_FAILURE_CODES['SETUP_REQUIRED']}",
                    file=sys.stderr,
                )
                sys.exit(1)
            response_payload, provider_response_id = _codex_run_via_openai_api(
                api_key=api_key,
                prompt=prompt,
                schema=schema,
            )
            raw_actual = response_payload.get("model", "")
            actual_model_id = str(raw_actual).strip().lower() if raw_actual else ""
            actual_model_source = "openai_api_response_object_fallback_from_codex_cli"
            actual_model_verified = (actual_model_id == CODEX_REQUIRED_MODEL_ID)
            effective_provider = "openai-api"  # fallback provider 기록
            output_text = _extract_response_output_text(response_payload)
            try:
                parsed = json.loads(output_text)
            except json.JSONDecodeError as exc:
                print(
                    f"[CODEX PROVIDER_OUTPUT_INVALID] fallback 모델 응답이 유효한 JSON이 아닙니다: {exc}",
                    file=sys.stderr,
                )
                sys.exit(1)
        else:
            # codex-cli metadata 정상 추출
            actual_model_verified = (actual_model_id == CODEX_REQUIRED_MODEL_ID)

    else:
        _die(f"[CODEX FAIL] 알 수 없는 provider: {provider_arg}", exit_code=1)
        return

    # 8. 파싱된 응답 검증
    if not isinstance(parsed, dict) or not isinstance(parsed.get("findings"), list):
        print(
            f"[CODEX PROVIDER_OUTPUT_INVALID] 모델 응답이 스키마를 만족하지 않습니다. "
            f"{CODEX_FAILURE_CODES['PROVIDER_OUTPUT_INVALID']}",
            file=sys.stderr,
        )
        sys.exit(1)

    # result 검증: ACCEPT 또는 REJECT만 허용
    result_from_model: str = str(parsed.get("result", "")).upper().strip()
    if result_from_model not in {"ACCEPT", "REJECT"}:
        print(
            f"[CODEX PROVIDER_OUTPUT_INVALID] 모델 응답 result가 ACCEPT 또는 REJECT가 아닙니다: '{result_from_model}'",
            file=sys.stderr,
        )
        sys.exit(1)

    # review_model 필드는 표시용으로만 사용; actual evidence는 provider-level actual_model_id 기준
    # diff_sha256 비교: 모델 응답 hash와 현재 hash 비교
    response_diff_sha: str = str(parsed.get("diff_sha256", "")).strip()
    if not response_diff_sha:
        print(
            f"[CODEX PROVIDER_OUTPUT_INVALID] 모델 응답에 diff_sha256 필드가 없습니다. "
            f"{CODEX_FAILURE_CODES['PROVIDER_OUTPUT_INVALID']}",
            file=sys.stderr,
        )
        sys.exit(1)
    if response_diff_sha != current_diff_sha:
        print(
            f"[CODEX STALE_REVIEW] diff_sha256 불일치: "
            f"모델응답={response_diff_sha[:16]}... 현재={current_diff_sha[:16]}... "
            f"{CODEX_FAILURE_CODES['STALE_REVIEW']}",
            file=sys.stderr,
        )
        sys.exit(1)

    # actual_model_verified 최종 확인
    if not actual_model_verified:
        print(
            f"[CODEX MODEL_UNAVAILABLE] actual_model_id='{actual_model_id}'가 "
            f"'{CODEX_REQUIRED_MODEL_ID}'와 다릅니다. "
            f"{CODEX_FAILURE_CODES['MODEL_UNAVAILABLE']}",
            file=sys.stderr,
        )
        sys.exit(1)

    # 9. raw 응답 저장 (secret redaction 적용)
    raw_output_path = Path(raw_output_path_arg) if raw_output_path_arg else BASE_DIR / "codex_run_raw.json"
    try:
        raw_safe = _redact_secrets(json.dumps(response_payload, ensure_ascii=False, indent=2))
        raw_output_path.write_text(raw_safe, encoding="utf-8")
    except OSError as exc:
        print(f"[CODEX PROVIDER_FAIL] raw 응답 저장 실패: {exc}", file=sys.stderr)
        sys.exit(1)

    # 10. 결과 파일 저장
    findings: List[Dict[str, Any]] = parsed.get("findings", [])
    _current_state = _load()
    _current_pipeline_id: str = _current_state.get("pipeline_id", "") if _current_state is not None else ""
    result_data: Dict[str, Any] = {
        "schema_version": 2,
        "pipeline_id": _current_pipeline_id,
        "stage": stage_arg,
        "review_model": CODEX_REQUIRED_REVIEW_MODEL,  # 표시용 대문자 GPT-5.5
        "result": result_from_model,
        "diff_sha256": current_diff_sha,
        "head_sha": current_head_sha,
        "review_provider": effective_provider,
        "requested_model_id": CODEX_REQUIRED_MODEL_ID,  # API payload model 필드
        "actual_model_id": actual_model_id,
        "actual_model_verified": actual_model_verified,
        "actual_model_source": actual_model_source,
        "provider_response_id": provider_response_id,
        "provider_exit_code": provider_exit_code,
        "attempt_count": total_attempts + 1,
        "required_actions": parsed.get("required_actions", []),
        "return_phase": parsed.get("return_phase"),
        "reviewer": "codex-run",
        "reviewed_at": _now(),
        "summary": parsed.get("summary", ""),
        "findings": findings,
        "history": [],
    }
    if auth_method:
        result_data["codex_cli_auth_method"] = auth_method

    output_path = Path(output_path_arg) if output_path_arg else BASE_DIR / "codex_review_result.json"
    # 기존 파일이 있으면 history에 누적
    if output_path.exists():
        try:
            existing_data = json.loads(output_path.read_text(encoding="utf-8"))
            if isinstance(existing_data, dict):
                prev_history: List[Any] = existing_data.get("history", [])
                if not isinstance(prev_history, list):
                    prev_history = []
                prev_entry: Dict[str, Any] = {k: v for k, v in existing_data.items() if k != "history"}
                prev_history.append(prev_entry)
                result_data["history"] = prev_history
        except (OSError, json.JSONDecodeError):
            pass

    try:
        output_path.write_text(json.dumps(result_data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        print(f"[CODEX PROVIDER_FAIL] 결과 파일 저장 실패: {exc}", file=sys.stderr)
        sys.exit(1)

    # 11. attempt log 갱신 (성공)
    attempt_log.append({
        "provider": effective_provider,
        "stage": stage_arg,
        "failure_code": None,
        "result": result_from_model,
        "ts": _now(),
    })
    _save_codex_attempt_log(state, attempt_log)

    critical_count = sum(1 for f in findings if isinstance(f, dict) and f.get("level") == "CRITICAL")
    high_count = sum(1 for f in findings if isinstance(f, dict) and f.get("level") == "HIGH")
    print(
        f"[CODEX RUN] stage={stage_arg} provider={effective_provider} "
        f"model={CODEX_REQUIRED_REVIEW_MODEL} actual_model_id={actual_model_id} "
        f"actual_model_verified={actual_model_verified} "
        f"findings={len(findings)} CRITICAL={critical_count} HIGH={high_count}\n"
        f"  결과 파일: {output_path}\n"
        f"  raw 응답: {raw_output_path}"
    )


def cmd_review_codex_record(args: argparse.Namespace) -> None:
    """review codex-record — 실제 Codex review ACCEPT/REJECT 세션을 공식 기록으로 등록.

    MT-3 (IMP-20260516-A627): pr/rca stage 전용 4중 검증 적용:
      1. --review-model == "GPT-5.5" 검증
      2. --head-sha == 현재 git HEAD SHA 검증 (ACCEPT인 경우)
      3. --diff-sha256 비어 있지 않음 검증 (ACCEPT인 경우)
      4. --evidence 파일 존재 + JSON 파싱으로 review_model 필드 확인

    REJECT인 경우:
      - --notes 또는 --required-actions 중 하나는 필수
      - failure_packet.json 생성 (gate, owner, return_phase 포함)

    Args:
        args: argparse.Namespace — codex-record 전용 인자.

    Raises:
        SystemExit: 검증 실패 시 exit_code=1(내용 오류) 또는 exit_code=2(인자 오류).
    """
    stage_arg: str = getattr(args, "stage", "") or ""
    result_arg: str = getattr(args, "result_value", "") or ""
    review_model_arg: str = getattr(args, "review_model", "") or ""
    head_sha_arg: Optional[str] = getattr(args, "head_sha", None)
    diff_sha256_arg: Optional[str] = getattr(args, "diff_sha256_arg", None)
    evidence_arg: Optional[str] = getattr(args, "evidence", None)
    notes_arg: Optional[str] = getattr(args, "notes", None)
    required_actions_arg: Optional[str] = getattr(args, "required_actions", None)
    return_phase_arg: Optional[str] = getattr(args, "return_phase", None)
    pr_number_arg: Optional[str] = getattr(args, "pr_number", None)
    output_path_arg: Optional[str] = getattr(args, "output", None)
    reviewer_arg: str = getattr(args, "reviewer", "unknown") or "unknown"
    pipeline_id_arg: str = getattr(args, "pipeline_id_arg", "") or ""

    review_result_path = Path(output_path_arg) if output_path_arg else BASE_DIR / "codex_review_result.json"

    # D2 수정: codex-record를 6개 stage 전부 지원 (plan/scope/code/hygiene/pr/rca)
    if not stage_arg or stage_arg.lower() not in CODEX_VALID_STAGES:
        _die(
            f"[CODEX RECORD] --stage는 필수이며 허용 값: {', '.join(sorted(CODEX_VALID_STAGES))}. "
            f"현재 값: '{stage_arg}'",
            exit_code=2,
        )
        return
    stage: str = stage_arg.lower()

    # pr/rca stage는 4중 검증, 나머지 stage는 review_model 검증만 (간소화 검증)
    is_full_validation_stage = stage in {"pr", "rca"}

    # result 검증
    result_upper: str = result_arg.upper() if result_arg else ""
    if result_upper not in {"ACCEPT", "REJECT"}:
        _die(
            f"[CODEX RECORD] --result는 ACCEPT 또는 REJECT만 허용합니다. 현재: '{result_arg}'",
            exit_code=2,
        )
        return

    # 검증 1: review_model == GPT-5.5 (강제)
    if not review_model_arg or review_model_arg.strip() != CODEX_REQUIRED_MODEL:
        _die(
            f"[CODEX RECORD] 검증 실패(1/4): --review-model은 반드시 '{CODEX_REQUIRED_MODEL}'이어야 합니다. "
            f"현재: '{review_model_arg}'. "
            f"GPT-5.5 외 모델로 수행한 리뷰는 공식 기록으로 인정되지 않습니다.",
            exit_code=2,
        )
        return

    now_ts = _now()

    # ACCEPT인 경우 추가 검증 (pr/rca stage는 4중 검증, 그 외 stage는 review_model만)
    if result_upper == "ACCEPT" and is_full_validation_stage:
        # 검증 2: head_sha == 현재 git HEAD
        if head_sha_arg:
            current_head = _get_current_head_sha()
            # PR SHA일 수도 있으므로 완전 일치 실패 시 경고만 (엄격 모드)
            # 사용자 Q2 답변: "head_sha == git HEAD or PR head SHA" → 불일치 시 FAIL
            if current_head and head_sha_arg.strip() != current_head:
                _die(
                    f"[CODEX RECORD] 검증 실패(2/4): head_sha 불일치 — "
                    f"제출한 SHA='{head_sha_arg.strip()[:16]}...'가 "
                    f"현재 git HEAD='{current_head[:16]}...'와 다릅니다. "
                    f"코드가 리뷰 이후 변경되었을 수 있습니다. "
                    f"최신 HEAD에 대해 다시 리뷰를 수행하거나 PR head SHA를 확인하세요.",
                    exit_code=1,
                )
                return
        else:
            _die(
                f"[CODEX RECORD] 검증 실패(2/4): {stage} stage ACCEPT 기록 시 --head-sha는 필수입니다.",
                exit_code=2,
            )
            return

        # 검증 3: diff_sha256 비어 있지 않음 (D5: 실제 diff와 비교)
        if not diff_sha256_arg or not diff_sha256_arg.strip():
            _die(
                f"[CODEX RECORD] 검증 실패(3/4): {stage} stage ACCEPT 기록 시 --diff-sha256는 비어 있으면 안 됩니다. "
                "리뷰한 diff의 SHA256 값을 제공하세요.",
                exit_code=2,
            )
            return
        # D5 수정: diff_sha256 실제 비교 (codex-record 실행 시 현재 diff 해시와 비교)
        # 빈 diff(returncode=0, stdout=b"")도 sha256(b"")과 비교하여 검증을 건너뛰지 않는다.
        try:
            _diff_proc = subprocess.run(
                ["git", "diff", "main...HEAD"],
                capture_output=True,
                cwd=str(BASE_DIR),
                timeout=30,
            )
            if _diff_proc.returncode == 0:
                _diff_bytes = _diff_proc.stdout if _diff_proc.stdout else b""
                _current_diff_sha = hashlib.sha256(_diff_bytes).hexdigest()
                if _current_diff_sha != diff_sha256_arg.strip():
                    _die(
                        f"[PIPELINE ERROR] diff_sha256 mismatch: recorded={diff_sha256_arg.strip()[:16]}... "
                        f"current={_current_diff_sha[:16]}... — "
                        "코드가 Codex 리뷰 이후 변경되었습니다. 최신 diff에 대해 다시 리뷰를 수행하세요.",
                        exit_code=1,
                    )
                    return
        except Exception as _dse:
            logging.getLogger(__name__).warning("diff_sha256 비교 실패: %s", _dse)

    # REJECT인 경우: pr/rca stage만 notes 또는 required_actions 필수 (간소화 stage는 선택)
    if result_upper == "REJECT" and is_full_validation_stage:
        if not notes_arg and not required_actions_arg:
            _die(
                "[CODEX RECORD] REJECT 기록 시 --notes 또는 --required-actions 중 하나는 필수입니다. "
                "거절 사유 또는 필요 조치를 명시하세요.",
                exit_code=2,
            )
            return

    # 검증 4: evidence 파일 존재 + JSON parse + review_model 확인
    # pr/rca stage ACCEPT에서만 evidence 필수. 간소화 stage(plan/scope/code/hygiene)는 선택.
    evidence_data: Dict[str, Any] = {}
    if evidence_arg:
        evidence_path = Path(evidence_arg)
        if not evidence_path.exists() or not evidence_path.is_file():
            _die(
                f"[CODEX RECORD] 검증 실패(4/4): evidence 파일이 존재하지 않습니다: {evidence_arg}",
                exit_code=1,
            )
            return

        # evidence JSON 파싱 + review_model 필드 확인 (fallback grep 금지)
        try:
            evidence_data = json.loads(evidence_path.read_text(encoding="utf-8", errors="replace"))
        except (json.JSONDecodeError, ValueError, OSError) as exc:
            _die(
                f"[CODEX RECORD] 검증 실패(4/4): evidence 파일 JSON 파싱 실패 — {exc}. "
                f"유효한 JSON 파일이어야 합니다. fallback grep은 허용되지 않습니다.",
                exit_code=1,
            )
            return

        evidence_model = str(evidence_data.get("review_model", "")).strip()
        if evidence_model != CODEX_REQUIRED_MODEL:
            _die(
                f"[CODEX RECORD] 검증 실패(4/4): evidence JSON의 review_model='{evidence_model}'이 "
                f"'{CODEX_REQUIRED_MODEL}'과 다릅니다. "
                f"실제 GPT-5.5 세션에서 생성된 리뷰 파일을 제공하세요.",
                exit_code=1,
            )
            return
    elif is_full_validation_stage and result_upper == "ACCEPT":
        # pr/rca stage ACCEPT에서는 evidence 필수
        _die(
            f"[CODEX RECORD] 검증 실패(4/4): {stage} stage ACCEPT 기록 시 --evidence는 필수입니다. "
            "Codex review 결과 JSON 파일 경로를 제공하세요.",
            exit_code=2,
        )
        return

    # pipeline_id 결정
    active_pipeline_id: str = pipeline_id_arg
    if not active_pipeline_id:
        try:
            st = _load_state()
            active_pipeline_id = st.get("pipeline_id", "") if st is not None else ""
        except Exception:
            active_pipeline_id = ""

    # 기존 파일 로드 (history 누적)
    existing_history: List[Dict[str, Any]] = []
    existing_findings: List[Dict[str, Any]] = []
    if review_result_path.exists():
        try:
            existing_raw = json.loads(
                review_result_path.read_text(encoding="utf-8", errors="replace")
            )
            existing_history = existing_raw.get("history", [])
            if existing_raw.get("stage"):
                existing_history.insert(0, {
                    "stage": existing_raw.get("stage"),
                    "result": existing_raw.get("result"),
                    "review_model": existing_raw.get("review_model"),
                    "reviewer": existing_raw.get("reviewer"),
                    "created_at": existing_raw.get("created_at", ""),
                    "diff_sha256": existing_raw.get("diff_sha256", ""),
                })
            existing_findings = existing_raw.get("findings", [])
        except Exception as exc:
            logging.getLogger(__name__).warning("기존 review 파일 로드 실패: %s", exc)

    # diff_sha256 계산 (인자로 없으면 현재 diff에서 계산)
    if not diff_sha256_arg or not diff_sha256_arg.strip():
        _, computed_diff_sha = _collect_git_diff_meta("main")
        diff_sha256_final = computed_diff_sha
    else:
        diff_sha256_final = diff_sha256_arg.strip()

    # reviewed_files 수집
    reviewed_files, _ = _collect_git_diff_meta("main")

    # 신규 기록 생성
    new_record: Dict[str, Any] = {
        "schema_version": 2,
        "pipeline_id": active_pipeline_id,
        "stage": stage,
        "review_type": "deep",
        "result": result_upper,
        "reviewer": reviewer_arg,
        "review_model": CODEX_REQUIRED_MODEL,
        "reviewed_files": reviewed_files,
        "diff_sha256": diff_sha256_final,
        "findings": existing_findings,
        "created_at": now_ts,
        "updated_at": now_ts,
    }

    if pr_number_arg:
        new_record["pr_number"] = pr_number_arg
    if head_sha_arg:
        new_record["head_sha"] = head_sha_arg.strip() if head_sha_arg else ""
    if notes_arg:
        new_record["notes"] = notes_arg
    if required_actions_arg:
        new_record["required_actions"] = [a.strip() for a in required_actions_arg.split(",") if a.strip()]
    if return_phase_arg:
        new_record["return_phase"] = return_phase_arg

    # history 누적
    history_snapshot = {
        "stage": stage,
        "result": result_upper,
        "review_model": CODEX_REQUIRED_MODEL,
        "reviewer": reviewer_arg,
        "created_at": now_ts,
        "diff_sha256": diff_sha256_final,
        "recorded_via": "codex-record",
    }
    updated_history: List[Dict[str, Any]] = [history_snapshot] + [
        h for h in existing_history
        if h.get("stage") != stage
    ]
    new_record["history"] = updated_history

    # schema v2 검증
    try:
        _validate_codex_review_schema(new_record)
    except ValueError as ve:
        _die(f"[CODEX RECORD] schema 검증 실패: {ve}", exit_code=2)
        return

    # 파일 저장
    try:
        review_result_path.parent.mkdir(parents=True, exist_ok=True)
        review_result_path.write_text(
            json.dumps(new_record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError as exc:
        _die(f"[CODEX RECORD] codex_review_result.json 저장 실패: {exc}", exit_code=2)
        return

    # D2: stage별 pipeline_state에 gate 기록 (plan/scope/code/hygiene/pr/rca 모두)
    try:
        _st = _load_state()
        if _st:
            _cr_gates = _st.setdefault("codex_review_gates", {})
            _ts_key = f"codex_{stage}_{'accepted' if result_upper == 'ACCEPT' else 'rejected'}_at"
            _cr_gates[_ts_key] = now_ts
            _cr_gates[f"codex_{stage}_result"] = result_upper
            if stage == "scope" and new_record.get("allowed_files") is not None:
                _cr_gates["codex_scope_allowed_files"] = new_record.get("allowed_files", [])
                _cr_gates["codex_scope_forbidden_files"] = new_record.get("forbidden_files", [])
            if stage == "pr" and result_upper == "ACCEPT":
                _cr_gates["codex_pr_accepted_at"] = now_ts
            _save(_st)
    except Exception as _ste:
        logging.getLogger(__name__).warning("codex gate 기록 저장 실패: %s", _ste)

    # REJECT인 경우 failure_packet.json 생성 (schema_v2)
    if result_upper == "REJECT":
        gate_name = f"codex_{stage}_review"
        # D2: stage별 owner/return_phase 결정
        _stage_owner_map: Dict[str, str] = {
            "plan": "PM", "scope": "PM", "code": "Dev",
            "hygiene": "Dev", "pr": "Dev", "rca": "PM"
        }
        _stage_return_map: Dict[str, str] = {
            "plan": "pm", "scope": "pm", "code": "dev",
            "hygiene": "dev", "pr": "dev", "rca": "pm"
        }
        owner = _stage_owner_map.get(stage, "Dev")
        _return_phase = return_phase_arg or _stage_return_map.get(stage, "dev")
        _required_actions = new_record.get("required_actions") or []
        # required_actions가 비어있으면 notes를 폴백으로 사용
        if not _required_actions and notes_arg:
            _required_actions = [notes_arg]
        if not _required_actions:
            _required_actions = [
                f"Codex review가 REJECT를 반환했습니다. {owner}가 {_return_phase} 단계로 돌아가 수정하세요."
            ]
        packet_v2: Dict[str, Any] = {
            "schema_version": FAILURE_PACKET_SCHEMA_VERSION,
            "pipeline_id": active_pipeline_id,
            "phase": _return_phase,
            "gate": gate_name,
            "status": "FAIL",
            "failure_code": f"codex_{stage}_reject",
            "failure_category": "model_verification_failed",
            "summary_ko": f"Codex {stage} stage 리뷰가 REJECT 되었습니다.",
            "blocking_condition": f"codex {stage} stage ACCEPT 필요",
            "expected": "Codex GPT-5.5 리뷰 ACCEPT",
            "actual": "REJECT",
            "evidence_paths": [str(evidence_arg)] if evidence_arg else [],
            "command": [
                sys.executable, "pipeline.py", "review", "codex-record",
                "--stage", stage, "--result", "ACCEPT",
                "--review-model", CODEX_REQUIRED_MODEL,
            ],
            "exit_code": 1,
            "owner": owner,
            "return_phase": _return_phase,
            "minimal_rerun": [
                f"python pipeline.py review codex-record --stage {stage} --result ACCEPT --review-model GPT-5.5 ..."
            ],
            "required_actions": list(_required_actions),
            "retry_allowed": True,
            "attempt_count": 1,
            "created_at": now_ts,
            # codex 특수 필드 (legacy 호환)
            "stage": stage,
            "result": "REJECT",
            "notes": notes_arg or "",
            "evidence_file": str(evidence_arg) if evidence_arg else "",
            "review_model": CODEX_REQUIRED_MODEL,
        }
        packet_path = BASE_DIR / "failure_packet.json"
        try:
            packet_path.write_text(json.dumps(packet_v2, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[CODEX RECORD] REJECT — failure_packet.json 생성 (schema_v2): {packet_path}")
            print(f"  gate: {gate_name}")
            print(f"  owner: {owner}")
            print(f"  return_phase: {packet_v2['return_phase']}")
            print(f"  failure_category: {packet_v2['failure_category']}")
        except OSError as exc:
            logging.getLogger(__name__).warning("failure_packet.json 저장 실패: %s", exc)

    print(f"[CODEX RECORD] stage={stage} result={result_upper} 공식 기록 완료: {review_result_path}")
    if result_upper == "ACCEPT":
        print("  4중 검증: review_model(1/4) + head_sha(2/4) + diff_sha256(3/4) + evidence JSON(4/4) 모두 통과")
    else:
        print(f"  REJECT 기록 완료. failure_packet.json을 참조하여 {owner}가 수정 후 재시도하세요.")


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
    # IMP-20260522-29C1 fix-forward: started_at은 각 gate 핸들러의 진입 시점에 기록한다.
    # 이 함수는 completed_at만 기록한다. started_at fallback(_now())을 여기서 쓰면
    # completed_at과 동일 시점이 되어 elapsed ≈ 0이 된다.
    gate["completed_at"] = _now()
    gate["evidence"] = evidence
    gate["report_file"] = report_file
    if note:
        notes = gate.setdefault("notes", [])
        if isinstance(notes, list):
            notes.append(note)


def _repair_owner_for_gate(gate_name: str, report: Dict[str, Any]) -> str:
    if gate_name == "technical":
        failed = [
            str(item.get("name"))
            for item in report.get("checks", [])
            if isinstance(item, dict) and item.get("status") in {"FAIL", "ERROR"}
        ]
        if any(name in {"ruff", "mypy", "bandit", "py_compile", "pytest"} for name in failed):
            return "Dev tooling repair"
        return "Dev repair"
    if gate_name == "oracle":
        return "PM/oracle path repair or Dev behavior repair"
    if gate_name == "github_ci":
        return "CI/environment repair"
    if gate_name == "acceptance":
        return "PM clarification or Dev result repair"
    return "Pipeline Manager repair"


def _failure_root_from_paths(paths: Dict[str, Path]) -> Path:
    root = paths.get("failures_root")
    if isinstance(root, Path):
        return root
    for key in ("technical_result", "oracle_result", "github_ci_result", "acceptance_result"):
        candidate = paths.get(key)
        if isinstance(candidate, Path):
            return candidate.parent / "failures"
    return CONTRACTS_DIR / "UNKNOWN" / "failures"


def _next_failure_attempt(paths: Dict[str, Path], gate_name: str) -> int:
    root = _failure_root_from_paths(paths)
    if not root.exists():
        return 1
    prefix = f"{gate_name}_attempt_"
    attempts: List[int] = []
    for path in root.glob(f"{prefix}*.json"):
        match = re.search(r"_attempt_(\d+)\.json$", path.name)
        if match:
            attempts.append(int(match.group(1)))
    return (max(attempts) + 1) if attempts else 1


def _normalize_failure_category(category: Optional[str]) -> str:
    """invalid 카테고리는 'unknown'으로 대체하고 stderr 경고를 출력합니다."""
    if not category:
        return "unknown"
    cat = str(category).strip().lower()
    if cat in FAILURE_CATEGORIES:
        return cat
    sys.stderr.write(
        f"[FAILURE PACKET WARN] failure_category='{category}' is not in FAILURE_CATEGORIES; "
        f"defaulting to 'unknown'.\n"
    )
    return "unknown"


def _gate_owner_and_return(gate_name: str, status: str = "FAIL") -> Tuple[str, str]:
    """gate 이름과 status로부터 (owner, return_phase) 튜플 반환.

    status=SETUP_REQUIRED 인 경우 owner='User'로 강제 라우팅.
    """
    if str(status).upper() == "SETUP_REQUIRED":
        return ("User", "pm")
    return _GATE_OWNER_RETURN_MAP.get(gate_name, ("Pipeline Manager", "dev"))


def _count_same_failure_code_attempts(
    state: Dict[str, Any], gate_name: str, failure_code: str
) -> int:
    """동일 (gate_name, failure_code) 조합의 누적 attempt 횟수를 반환."""
    if not failure_code:
        return 0
    existing = state.get("failure_packets")
    if not isinstance(existing, list):
        return 0
    count = 0
    for item in existing:
        if not isinstance(item, dict):
            continue
        if str(item.get("gate") or "") == gate_name and str(item.get("failure_code") or "") == failure_code:
            count += 1
    return count


# ---------------------------------------------------------------------------
# IMP-20260527-075A: Cost/Attempt Budget Gate (MT-1)
# ---------------------------------------------------------------------------
# 목적: phase별(dev/qa/gate)·gate별 최대 재시도 횟수를 강제하고, 동일 failure_code
#       3회 반복 시 Architect/RCA로 자동 이관한다. Circuit Breaker(qa 동일 signature
#       2회)와 보완적으로 작동한다.
#
# state 신규 키:
#   state["attempt_budget"] = {
#     "config": {
#       "dev_max_attempts": 3, "qa_max_attempts": 3, "gate_max_attempts": 5,
#       "repeat_failure_code_threshold": 3,
#     },
#     "attempts": {"dev": [...], "qa": [...], "gate": [...]},
#     "blocked_phases": {phase: {"failure_code": str, "blocked_at": iso}},
#   }
# attempts 항목: {"outcome": "FAIL"|"PASS", "failure_code": str|None, "timestamp": iso}
# ---------------------------------------------------------------------------

ATTEMPT_BUDGET_PHASES = ("dev", "qa", "gate")
ATTEMPT_BUDGET_DEFAULTS = {
    "dev_max_attempts": 3,
    "qa_max_attempts": 3,
    "gate_max_attempts": 5,
    "repeat_failure_code_threshold": 3,
}


def _ensure_attempt_budget_keys(state: Dict[str, Any]) -> None:
    """state["attempt_budget"] dict 구조를 idempotent 하게 보장한다.

    구버전 state에 attempt_budget이 없거나 일부 키만 있는 경우 기본값으로 채운다.
    이미 존재하는 값은 변경하지 않는다.
    """
    if not isinstance(state.get("attempt_budget"), dict):
        state["attempt_budget"] = {}
    ab = state["attempt_budget"]
    if not isinstance(ab.get("config"), dict):
        ab["config"] = dict(ATTEMPT_BUDGET_DEFAULTS)
    else:
        for key, default in ATTEMPT_BUDGET_DEFAULTS.items():
            if key not in ab["config"] or not isinstance(ab["config"][key], int):
                ab["config"][key] = default
    if not isinstance(ab.get("attempts"), dict):
        ab["attempts"] = {p: [] for p in ATTEMPT_BUDGET_PHASES}
    else:
        for p in ATTEMPT_BUDGET_PHASES:
            if not isinstance(ab["attempts"].get(p), list):
                ab["attempts"][p] = []
    if not isinstance(ab.get("blocked_phases"), dict):
        ab["blocked_phases"] = {}


def _record_attempt_budget(
    state: Dict[str, Any],
    phase: str,
    outcome: str,
    failure_code: Optional[str] = None,
) -> None:
    """phase별 attempts 리스트에 시도 결과를 누적한다.

    - outcome="FAIL": attempts에 FAIL entry 추가. failure_code도 함께 기록.
      _detect_repeat_failure_code 호출하여 반복 failure_code 감지.
    - outcome="PASS": attempts 리스트와 blocked_phases 항목 초기화 (성공 시 한도 리셋).
    """
    if phase not in ATTEMPT_BUDGET_PHASES:
        return
    if outcome not in ("FAIL", "PASS"):
        return
    _ensure_attempt_budget_keys(state)
    ab = state["attempt_budget"]
    if outcome == "PASS":
        ab["attempts"][phase] = []
        if phase in ab["blocked_phases"]:
            del ab["blocked_phases"][phase]
        return
    # FAIL
    entry: Dict[str, Any] = {
        "outcome": "FAIL",
        "failure_code": failure_code or None,
        "timestamp": _now(),
    }
    ab["attempts"][phase].append(entry)
    # 반복 failure_code 감지 (3회째에 자동 표시)
    repeat_fc = _detect_repeat_failure_code(state, phase)
    if repeat_fc is not None:
        ab["blocked_phases"][phase] = {
            "failure_code": "REPEAT_FAILURE_CODE",
            "repeat_failure_code": repeat_fc,
            "blocked_at": _now(),
        }


def _check_attempt_budget(state: Dict[str, Any], phase: str) -> Dict[str, Any]:
    """phase의 attempt budget 상태를 반환한다.

    반환 dict 키:
      - blocked (bool): 한도 초과 또는 반복 failure_code 시 True
      - attempts_used (int): 누적 FAIL 횟수
      - max_attempts (int): 해당 phase 한도
      - failure_code (str|None): "BUDGET_EXCEEDED" | "REPEAT_FAILURE_CODE" | None
      - repeat_failure_code (str|None): 반복 감지된 failure_code 문자열
    """
    _ensure_attempt_budget_keys(state)
    ab = state["attempt_budget"]
    if phase not in ATTEMPT_BUDGET_PHASES:
        return {
            "blocked": False,
            "attempts_used": 0,
            "max_attempts": 0,
            "failure_code": None,
            "repeat_failure_code": None,
        }
    max_key = f"{phase}_max_attempts"
    max_attempts = int(ab["config"].get(max_key, ATTEMPT_BUDGET_DEFAULTS.get(max_key, 3)))
    attempts = [e for e in ab["attempts"].get(phase, []) if isinstance(e, dict) and e.get("outcome") == "FAIL"]
    attempts_used = len(attempts)
    repeat_fc = _detect_repeat_failure_code(state, phase)
    blocked = False
    failure_code: Optional[str] = None
    if repeat_fc is not None:
        blocked = True
        failure_code = "REPEAT_FAILURE_CODE"
    elif attempts_used >= max_attempts:
        blocked = True
        failure_code = "BUDGET_EXCEEDED"
    return {
        "blocked": blocked,
        "attempts_used": attempts_used,
        "max_attempts": max_attempts,
        "failure_code": failure_code,
        "repeat_failure_code": repeat_fc,
    }


def _detect_repeat_failure_code(state: Dict[str, Any], phase: str) -> Optional[str]:
    """동일 failure_code가 threshold 횟수 이상 연속/누적되었는지 검사.

    config["repeat_failure_code_threshold"](기본 3) 회 이상 같은 failure_code가
    attempts 리스트에 나타나면 그 failure_code 문자열을 반환. 없으면 None.
    """
    _ensure_attempt_budget_keys(state)
    ab = state["attempt_budget"]
    if phase not in ATTEMPT_BUDGET_PHASES:
        return None
    threshold = int(ab["config"].get("repeat_failure_code_threshold",
                                     ATTEMPT_BUDGET_DEFAULTS["repeat_failure_code_threshold"]))
    if threshold <= 0:
        return None
    counts: Dict[str, int] = {}
    for entry in ab["attempts"].get(phase, []):
        if not isinstance(entry, dict):
            continue
        if entry.get("outcome") != "FAIL":
            continue
        fc = entry.get("failure_code")
        if not fc:
            continue
        counts[str(fc)] = counts.get(str(fc), 0) + 1
    for fc, n in counts.items():
        if n >= threshold:
            return fc
    return None


def _korean_budget_message(
    phase: str,
    attempts_used: int,
    max_attempts: int,
    failure_code: Optional[str],
    repeat_failure_code: Optional[str] = None,
) -> str:
    """budget 차단 시 사용자에게 보여줄 한국어 메시지 생성.

    "재시도 한도", "초과", "Architect", phase 이름, 남은 횟수를 항상 포함한다.
    """
    remaining = max(0, max_attempts - attempts_used)
    if failure_code == "REPEAT_FAILURE_CODE" and repeat_failure_code:
        return (
            f"동일 failure_code '{repeat_failure_code}' {attempts_used}회 반복 — "
            f"{phase} phase 재시도 한도 안에 있어도 같은 원인이 누적되어 "
            f"Architect/RCA로 이관합니다. 남은 재시도: {remaining}회."
        )
    return (
        f"재시도 한도 초과 — {phase} phase {attempts_used}/{max_attempts}회 실패. "
        f"남은 재시도: {remaining}회. Architect로 이관합니다."
    )


def _record_failure_packet(
    state: Dict[str, Any],
    gate_name: str,
    report: Dict[str, Any],
    *,
    command: Optional[List[str]] = None,
    note: str = "",
    # IMP-20260518-150C schema_v2 확장 (모두 keyword-only, backward-compatible 기본값)
    status: Optional[str] = None,
    phase: str = "",
    failure_code: str = "",
    failure_category: Optional[str] = None,
    summary_ko: str = "",
    blocking_condition: str = "",
    expected: str = "",
    actual: str = "",
    evidence_paths: Optional[List[str]] = None,
    exit_code: int = -1,
    owner: Optional[str] = None,
    return_phase: Optional[str] = None,
    required_actions: Optional[List[str]] = None,
    retry_allowed: bool = True,
    # IMP-20260522-0C83 metrics schema 확장 (모두 keyword-only, backward-compatible 기본값 None)
    elapsed_before_failure: Optional[int] = None,
    previous_attempt_count: Optional[int] = None,
    repeated_failure_count: Optional[int] = None,
    last_same_failure_at: Optional[str] = None,
    suggested_minimal_rerun_reason: Optional[str] = None,
) -> Dict[str, Any]:
    """schema_version=2 failure packet 생성 + 저장.

    필수 검증:
    - required_actions=[] → SystemExit(2) (호출자가 최소 1개 조치를 제공해야 함).
      ``required_actions=None`` 인 backward-compatible 호출은 검증을 건너뛰고
      report.summary 또는 기본 안내 문구를 사용한다.
    - failure_category invalid → "unknown" 으로 대체 + stderr 경고.
    - status=SETUP_REQUIRED → owner="User" 강제 라우팅.
    - 동일 (gate, failure_code) 조합 attempt_count >= FAILURE_BLOCKED_THRESHOLD →
      status=BLOCKED + escalation_reason='same_failure_code_repeated_3x' + external blocker.
    """
    pid = str(state.get("pipeline_id") or "UNKNOWN")
    paths = _contract_paths(pid)
    attempt = _next_failure_attempt(paths, gate_name)
    packet_path = _failure_root_from_paths(paths) / f"{gate_name}_attempt_{attempt}.json"
    failed_checks = [
        item for item in report.get("checks", [])
        if isinstance(item, dict) and item.get("status") in {"FAIL", "ERROR"}
    ]
    if command is None:
        minimal_rerun: List[str] = []
    elif isinstance(command, str):
        minimal_rerun = [command]
    else:
        minimal_rerun = [str(item) for item in command]

    # required_actions 검증 — schema_v2 신규 호출에서만 strict
    if required_actions is not None:
        if not isinstance(required_actions, list) or len(required_actions) == 0:
            _die(
                "[FAILURE PACKET ERROR] required_actions가 비어 있습니다. "
                "최소 1개 조치를 제공하세요.",
                exit_code=2,
            )
        required_actions_final: List[str] = [str(a) for a in required_actions if str(a).strip()]
        if not required_actions_final:
            _die(
                "[FAILURE PACKET ERROR] required_actions가 비어 있습니다. "
                "최소 1개 조치를 제공하세요.",
                exit_code=2,
            )
    else:
        # backward-compatible: 호출자가 제공하지 않으면 note + 기본 안내로 폴백
        required_actions_final = [note] if note else ["Inspect failed_checks and report_excerpt to repair the gate."]

    # status 결정
    raw_status = (
        status
        or report.get("status")
        or report.get("summary", {}).get("verdict")
        or "FAIL"
    )
    final_status = str(raw_status).upper()
    if final_status not in {"FAIL", "BLOCKED", "SETUP_REQUIRED"}:
        # FAIL 아닌 임의 verdict는 그대로 보존
        pass

    # failure_category 정규화 (None 또는 invalid → 'unknown')
    normalized_category = _normalize_failure_category(failure_category)

    # owner/return_phase 결정
    auto_owner, auto_return = _gate_owner_and_return(gate_name, final_status)
    final_owner = owner if owner else auto_owner
    final_return_phase = return_phase if return_phase else auto_return
    if final_status == "SETUP_REQUIRED":
        # SETUP_REQUIRED는 owner=User 강제 (호출자 override 차단)
        final_owner = "User"

    # 동일 (gate, failure_code) 조합 3회 이상이면 BLOCKED 전이
    escalation_reason: Optional[str] = None
    same_code_count = _count_same_failure_code_attempts(state, gate_name, failure_code) + 1
    # MT-3 (IMP-20260519-EC9F): 2회 반복 시 YELLOW 경고 (3회에서 BLOCKED 전환 전 조기 경보)
    if failure_code and same_code_count == FAILURE_BLOCKED_THRESHOLD - 1 and final_status != "SETUP_REQUIRED":
        print(YELLOW(
            f"\n[FAILURE WARNING] 동일 failure_code '{failure_code}' {same_code_count}회 반복 "
            f"— 다음 발생 시 BLOCKED로 전환됩니다\n"
            f"  gate={gate_name}, 현재까지 {same_code_count}회 / 최대 {FAILURE_BLOCKED_THRESHOLD}회\n"
        ))
    if failure_code and same_code_count >= FAILURE_BLOCKED_THRESHOLD and final_status != "SETUP_REQUIRED":
        final_status = "BLOCKED"
        escalation_reason = "same_failure_code_repeated_3x"
        retry_allowed = False

    packet: Dict[str, Any] = {
        "schema_version": FAILURE_PACKET_SCHEMA_VERSION,
        "pipeline_id": pid,
        "phase": phase or final_return_phase,
        "gate": gate_name,
        "status": final_status,
        "failure_code": failure_code,
        "failure_category": normalized_category,
        "summary_ko": summary_ko or note or "",
        "blocking_condition": blocking_condition or "",
        "expected": expected or "",
        "actual": actual or "",
        "evidence_paths": list(evidence_paths) if isinstance(evidence_paths, list) else [],
        "command": minimal_rerun,
        "exit_code": int(exit_code) if isinstance(exit_code, int) else -1,
        "owner": final_owner,
        "return_phase": final_return_phase,
        "minimal_rerun": minimal_rerun,
        "required_actions": required_actions_final,
        "retry_allowed": bool(retry_allowed),
        "attempt_count": same_code_count if failure_code else attempt,
        "created_at": _now(),
        # legacy 호환 필드
        "attempt": attempt,
        "packet_path": str(packet_path),
        "recorded_at": _now(),
        "repair_owner": _repair_owner_for_gate(gate_name, report),
        "note": note,
        "failed_checks": failed_checks,
        "report_excerpt": {
            "blockers": report.get("blockers", []),
            "summary": report.get("summary", {}),
            "results": report.get("results", [])[:10] if isinstance(report.get("results"), list) else [],
        },
    }
    if escalation_reason:
        packet["escalation_reason"] = escalation_reason
    # IMP-20260522-0C83 metrics 선택 필드 (None이 아닌 값만 포함)
    if elapsed_before_failure is not None:
        packet["elapsed_before_failure"] = int(elapsed_before_failure)
    if previous_attempt_count is not None:
        packet["previous_attempt_count"] = int(previous_attempt_count)
    if repeated_failure_count is not None:
        packet["repeated_failure_count"] = int(repeated_failure_count)
    if last_same_failure_at is not None:
        packet["last_same_failure_at"] = str(last_same_failure_at)
    if suggested_minimal_rerun_reason is not None:
        packet["suggested_minimal_rerun_reason"] = str(suggested_minimal_rerun_reason)
    _write_json(packet_path, packet)
    state.setdefault("failure_packets", [])
    if isinstance(state["failure_packets"], list):
        state["failure_packets"].append({
            "gate": gate_name,
            "attempt": attempt,
            "path": str(packet_path),
            "packet_path": str(packet_path),
            "recorded_at": packet["recorded_at"],
            "repair_owner": packet["repair_owner"],
            # schema_v2 핵심 필드 (조회용)
            "schema_version": FAILURE_PACKET_SCHEMA_VERSION,
            "status": final_status,
            "failure_code": failure_code,
            "failure_category": normalized_category,
            "owner": final_owner,
            "return_phase": final_return_phase,
            "attempt_count": packet["attempt_count"],
            "escalation_reason": escalation_reason,
        })
    # MT-1 (IMP-20260519-EC9F): packet 생성 후 콘솔에 구조화된 요약 출력
    _print_failure_packet_console(packet)
    return packet


def _print_failure_packet_console(packet: Dict[str, Any]) -> None:
    """failure_packet 내용을 구조화된 형식으로 콘솔에 출력.

    IMP-20260519-EC9F MT-1: _record_failure_packet 호출 후 자동 실행되어
    사용자/에이전트가 failure 원인과 복구 방법을 즉시 파악할 수 있도록 한다.

    Args:
        packet: _record_failure_packet이 반환한 schema_v2 failure packet dict.
    """
    if not isinstance(packet, dict):
        return

    status = str(packet.get("status") or "FAIL")
    gate = str(packet.get("gate") or "")
    failure_code = str(packet.get("failure_code") or "")
    failure_category = str(packet.get("failure_category") or "")
    summary_ko = str(packet.get("summary_ko") or "")
    expected = str(packet.get("expected") or "")
    actual = str(packet.get("actual") or "")
    return_phase = str(packet.get("return_phase") or "")
    owner = str(packet.get("owner") or "")
    packet_path = str(packet.get("packet_path") or "")
    minimal_rerun = packet.get("minimal_rerun") or []
    required_actions = packet.get("required_actions") or []
    escalation_reason = str(packet.get("escalation_reason") or "")

    # 상태에 따른 색상 선택
    if status == "BLOCKED":
        color_fn = RED
        header_label = "[FAILURE BLOCKED]"
    else:
        color_fn = YELLOW
        header_label = "[FAILURE PACKET]"

    print()
    print(color_fn(f"{'=' * 60}"))
    print(color_fn(f"  {header_label}  gate={gate}  status={status}"))
    print(color_fn(f"{'=' * 60}"))

    if failure_code:
        print(color_fn(f"  failure_code     : {failure_code}"))
    if failure_category:
        print(color_fn(f"  failure_category : {failure_category}"))
    if summary_ko:
        print(color_fn(f"  summary_ko       : {summary_ko}"))
    if expected:
        print(color_fn(f"  expected         : {expected}"))
    if actual:
        print(color_fn(f"  actual           : {actual}"))
    if return_phase:
        print(color_fn(f"  return_phase     : {return_phase}"))
    if owner:
        print(color_fn(f"  owner            : {owner}"))
    if escalation_reason:
        print(RED(f"  escalation       : {escalation_reason}"))

    if required_actions:
        print(color_fn("  required_actions :"))
        for idx, action in enumerate(required_actions, 1):
            print(color_fn(f"    {idx}. {action}"))

    if minimal_rerun:
        rerun_str = " ".join(str(x) for x in minimal_rerun)
        print(color_fn(f"  minimal_rerun    : {rerun_str}"))

    if packet_path:
        print(color_fn(f"  packet_path      : {packet_path}"))

    print(color_fn(f"{'=' * 60}"))
    print()


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
        ("bandit", ["-m", "bandit", "-q", "--ini", str(BASE_DIR / ".bandit"), *[str(path) for path in target_files]]),
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

    profile = _execution_profile(state)
    fast_non_code_profile = profile.get("mode") in {"FAST_DOC", "FAST_ANALYSIS"} and not target_files
    test_files = list(BASE_DIR.glob("test_*.py")) + list((BASE_DIR / "tests").glob("test_*.py")) if (BASE_DIR / "tests").exists() else list(BASE_DIR.glob("test_*.py"))
    if fast_non_code_profile:
        checks.append({
            "name": "pytest",
            "status": "SKIP",
            "command": [sys.executable, "-m", "pytest", "-q"],
            "version": _technical_gate_tool_version("pytest", timeout) if importlib.util.find_spec("pytest") is not None else None,
            "message": f"{profile.get('mode')} has no Python evidence; full pytest is deferred to GitHub CI",
        })
    elif not test_files:
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
        # pytest 실행 전에 전역 STATE_FILE 내용을 메모리에 백업한다.
        # pytest 스위트 안의 일부 테스트(예: test_three_gate_pipeline.py)가
        # pipeline.py 내부 함수를 임포트하여 _save(state)를 호출하면서
        # TMP-HARNESS-AUTO 등 테스트 전용 state로 STATE_FILE을 덮어쓸 수 있다.
        # 실행 완료 후 STATE_FILE이 오염되었으면 백업으로 복원한다.
        _orig_state_bytes: Optional[bytes] = None
        try:
            if STATE_FILE.exists():
                _orig_state_bytes = STATE_FILE.read_bytes()
        except OSError:
            pass
        _proc_exc: Optional[Exception] = None
        pytest_proc: Optional[subprocess.CompletedProcess[str]] = None
        try:
            pytest_proc = subprocess.run(
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
            _proc_exc = exc
        finally:
            # STATE_FILE이 오염되었으면 원래 내용으로 복원한다.
            try:
                if _orig_state_bytes is not None and STATE_FILE.exists():
                    current_bytes = STATE_FILE.read_bytes()
                    if current_bytes != _orig_state_bytes:
                        with tempfile.NamedTemporaryFile(
                            mode="wb", dir=STATE_FILE.parent, delete=False, suffix=".tmp"
                        ) as _restore_tmp:
                            _restore_tmp.write(_orig_state_bytes)
                            _restore_path = _restore_tmp.name
                        os.replace(_restore_path, str(STATE_FILE))
            except Exception:
                pass
        if _proc_exc is not None:
            failures += 1
            checks.append({
                "name": "pytest",
                "status": "ERROR",
                "command": command,
                "version": _technical_gate_tool_version("pytest", timeout),
                "message": str(_proc_exc),
            })
            return {
                "schema_version": 1,
                "generated_at": _now(),
                "pipeline_id": state.get("pipeline_id"),
                "status": "FAIL",
                "evidence_files": [_display_path(path) for path in evidence_files],
                "target_files": [_display_path(path) for path in target_files],
                "strict_tools": strict_tools,
                "execution_profile": profile.get("mode"),
                "checks": checks,
            }
        status = "PASS" if pytest_proc is not None and pytest_proc.returncode == 0 else "FAIL"
        if status == "FAIL":
            failures += 1
        checks.append({
            "name": "pytest",
            "status": status,
            "command": command,
            "version": _technical_gate_tool_version("pytest", timeout),
            "returncode": pytest_proc.returncode if pytest_proc is not None else -1,
            "stdout": pytest_proc.stdout[-4000:] if pytest_proc is not None else "",
            "stderr": pytest_proc.stderr[-4000:] if pytest_proc is not None else "",
        })

    return {
        "schema_version": 1,
        "generated_at": _now(),
        "pipeline_id": state.get("pipeline_id"),
        "status": "PASS" if failures == 0 else "FAIL",
        "evidence_files": [_display_path(path) for path in evidence_files],
        "target_files": [_display_path(path) for path in target_files],
        "strict_tools": strict_tools,
        "execution_profile": profile.get("mode"),
        "checks": checks,
    }


def _classify_pr_file(path: str, phase: str, pid: str) -> str:
    """Phase attestation PR에서 허용되는 파일인지 분류합니다.

    attestation PR에는 오직 아래 파일만 포함되어야 합니다:
    1. .pipeline/phase_attestation_request.json
    2. .pipeline/phase_evidence/{pid}/{phase}/**
    그 외 모든 파일은 금지합니다.
    """
    path = path.replace("\\", "/")
    if path == ".pipeline/phase_attestation_request.json":
        return "allowed"
    phase_evidence_prefix = ".pipeline/phase_evidence/"
    if path.startswith(phase_evidence_prefix):
        rest = path[len(phase_evidence_prefix):]
        parts = rest.split("/")
        if len(parts) >= 2 and parts[0] == pid and parts[1] == phase:
            return "allowed"
        return f"forbidden:다른 pipeline_id 또는 phase의 evidence 경로 ({path})"
    return f"forbidden:phase attestation PR에 포함할 수 없는 파일 ({path})"


def _cmd_gates_preflight_pr(args: argparse.Namespace) -> None:
    """preflight-pr: pipeline_state.json 없이도 동작하는 독립 함수 (CI 환경 지원)."""
    phase = str(getattr(args, "phase", "") or "").strip().lower()
    if not phase:
        _die("[PIPELINE ERROR] preflight-pr requires --phase {pm,dev,qa,build}", exit_code=2)

    # --pipeline-id 인수 우선, 없으면 pipeline_state.json에서 시도
    arg_pid = str(getattr(args, "pipeline_id", "") or "").strip()
    if arg_pid:
        effective_pid = arg_pid
    else:
        _state = _load()
        effective_pid = str(_state.get("pipeline_id", "")) if _state else ""

    # request file 검증 (--request-file 지원)
    request_file = str(getattr(args, "request_file", "") or ".pipeline/phase_attestation_request.json").strip()
    if os.path.isfile(request_file):
        try:
            with open(request_file, "r", encoding="utf-8") as _rf:
                _req = json.load(_rf)
            req_phase = str(_req.get("phase", "")).strip().lower()
            req_pid = str(_req.get("pipeline_id", "")).strip()
            if req_phase and req_phase != phase:
                _die(
                    f"[PIPELINE ERROR] preflight-pr FAIL: request.phase={req_phase!r}가 --phase={phase!r}와 다릅니다.",
                    exit_code=1,
                )
            if arg_pid and req_pid and req_pid != arg_pid:
                _die(
                    f"[PIPELINE ERROR] preflight-pr FAIL: request.pipeline_id={req_pid!r}가 --pipeline-id={arg_pid!r}와 다릅니다.",
                    exit_code=1,
                )
        except (json.JSONDecodeError, OSError) as _exc:
            _die(f"[PIPELINE ERROR] preflight-pr: request 파일 읽기 실패: {_exc}", exit_code=1)

    # merge-base 기반 git diff (CI shallow clone 지원)
    base_sha = os.environ.get("GITHUB_BASE_SHA", "").strip()
    if not base_sha:
        _mb = subprocess.run(
            ["git", "merge-base", "origin/main", "HEAD"],
            capture_output=True, text=True, check=False,
        )
        if _mb.returncode == 0:
            base_sha = _mb.stdout.strip()

    if base_sha:
        diff_cmd = ["git", "diff", "--name-only", f"{base_sha}...HEAD"]
    else:
        diff_cmd = ["git", "diff", "--name-only", "origin/main...HEAD"]

    result = subprocess.run(diff_cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        _die(
            f"[PIPELINE ERROR] preflight-pr: git diff failed: {result.stderr.strip()}",
            exit_code=1,
        )
    changed_files = [f.strip() for f in result.stdout.splitlines() if f.strip()]

    forbidden: List[str] = []
    for f in changed_files:
        verdict = _classify_pr_file(f, phase, effective_pid)
        if verdict.startswith("forbidden"):
            forbidden.append(f)

    if forbidden:
        # MT-2 (IMP-20260519-EC9F): preflight-pr FAIL 시 failure_packet 생성 (state 로드 시도)
        _pf_state: Optional[Dict[str, Any]] = None
        try:
            _pf_state = _load()
        except Exception:
            _pf_state = None
        if _pf_state is not None and isinstance(_pf_state, dict):
            _pf_pid = str(_pf_state.get("pipeline_id") or effective_pid or "UNKNOWN")
            _pf_paths = _contract_paths(_pf_pid)
            _attempt = _next_failure_attempt(_pf_paths, f"preflight_pr_{phase}")
            _pf_packet_path = _failure_root_from_paths(_pf_paths) / f"preflight_pr_{phase}_attempt_{_attempt}.json"
            _pf_packet: Dict[str, Any] = {
                "schema_version": FAILURE_PACKET_SCHEMA_VERSION,
                "pipeline_id": _pf_pid,
                "phase": phase,
                "gate": f"preflight_pr_{phase}",
                "status": "FAIL",
                "failure_code": f"preflight_pr_scope_violation_{phase}",
                "failure_category": "scope_mismatch",
                "summary_ko": f"preflight-pr FAIL: phase={phase} PR 범위 초과 파일 발견",
                "expected": f"PR에는 phase={phase} scope_manifest 내 파일만 포함",
                "actual": f"forbidden 파일 {len(forbidden)}개: {', '.join(forbidden[:5])}",
                "owner": "Dev",
                "return_phase": "dev",
                "required_actions": [
                    f"금지 파일을 되돌리세요: {', '.join(forbidden[:3])}",
                    "Trust-Root 파일(pipeline.py, CLAUDE.md, .github/workflows)은 별도 IMP 파이프라인으로 처리하세요",
                ],
                "retry_allowed": True,
                "minimal_rerun": ["python", "pipeline.py", "gates", "preflight-pr", "--phase", phase],
                "command": ["python", "pipeline.py", "gates", "preflight-pr", "--phase", phase],
                "packet_path": str(_pf_packet_path),
                "attempt": _attempt,
                "attempt_count": _attempt,
                "recorded_at": _now(),
                "created_at": _now(),
                "exit_code": 1,
                "blocking_condition": "",
                "evidence_paths": [],
                "note": "",
                "failed_checks": [],
                "repair_owner": "Dev",
                "report_excerpt": {"blockers": forbidden, "summary": {}, "results": []},
                "escalation_reason": None,
            }
            _write_json(_pf_packet_path, _pf_packet)
            _pf_state.setdefault("failure_packets", [])
            if isinstance(_pf_state.get("failure_packets"), list):
                _pf_state["failure_packets"].append({
                    "gate": f"preflight_pr_{phase}",
                    "attempt": _attempt,
                    "path": str(_pf_packet_path),
                    "packet_path": str(_pf_packet_path),
                    "recorded_at": _pf_packet["recorded_at"],
                    "repair_owner": "Dev",
                    "schema_version": FAILURE_PACKET_SCHEMA_VERSION,
                    "status": "FAIL",
                    "failure_code": _pf_packet["failure_code"],
                    "failure_category": "scope_mismatch",
                    "owner": "Dev",
                    "return_phase": "dev",
                    "attempt_count": _attempt,
                    "escalation_reason": None,
                })
            _save(_pf_state)
            _print_failure_packet_console(_pf_packet)
        print(f"[PIPELINE ERROR] preflight-pr FAIL: 다음 파일이 phase={phase} PR 범위를 벗어납니다:")
        for f in forbidden:
            print(f"  - {f}")
        sys.exit(1)

    print(GREEN(f"[PREFLIGHT-PR PASS] phase={phase} changed={len(changed_files)}개 파일"))
    sys.exit(0)


# IMP-20260528-3898 MT-2: preflight-pr-impl — 구현 PR(impl 브랜치) 위생 검사.
# preflight-pr(phase-attestation PR 전용)과 별개로, 구현 PR에 내부 산출물이
# 섞이지 않았는지 _is_internal_artifact() SSoT를 사용하여 검사합니다.
def _cmd_gates_preflight_pr_impl(args: argparse.Namespace) -> None:
    """preflight-pr-impl: 구현 PR에 내부 산출물이 포함되지 않았는지 검사합니다.

    _is_internal_artifact() (WORKSPACE_INTERNAL_PATTERNS SSoT)를 사용하여
    pipeline 런타임 파일(build_report.xml, scope_manifest_MT-*.json 등)이
    구현 PR diff에 포함되어 있으면 exit 1로 차단합니다.

    tests/oracles/ 경로는 의도적으로 허용됩니다 (사용자 제공 oracle 파일).
    """
    # BUG-20260529-40C9 MT-1: --files 옵션이 지정된 경우 git diff 대신 명시적 파일 목록 사용
    explicit_files: Optional[str] = getattr(args, "files", None)
    if explicit_files is not None:
        changed_files = [f.strip() for f in explicit_files.split(",") if f.strip()]
        # --files 모드에서는 삭제 파일 추적 불가 → 빈 세트로 처리
        deleted_files: set = set()
    else:
        # merge-base 기반 git diff (CI shallow clone 지원)
        base_sha = os.environ.get("GITHUB_BASE_SHA", "").strip()
        if not base_sha:
            _mb = subprocess.run(
                ["git", "merge-base", "origin/main", "HEAD"],
                capture_output=True, text=True, check=False,
            )
            if _mb.returncode == 0:
                base_sha = _mb.stdout.strip()

        if base_sha:
            diff_cmd = ["git", "diff", "--name-only", f"{base_sha}...HEAD"]
        else:
            diff_cmd = ["git", "diff", "--name-only", "origin/main...HEAD"]

        result = subprocess.run(diff_cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            _die(
                f"[PIPELINE ERROR] preflight-pr-impl: git diff 실패: {result.stderr.strip()}",
                exit_code=1,
            )
        changed_files = [f.strip() for f in result.stdout.splitlines() if f.strip()]

        # 삭제된 파일은 내부 산출물 검사에서 제외 (정리 행위로 허용)
        if base_sha:
            deleted_diff_cmd = ["git", "diff", "--diff-filter=D", "--name-only", f"{base_sha}...HEAD"]
        else:
            deleted_diff_cmd = ["git", "diff", "--diff-filter=D", "--name-only", "origin/main...HEAD"]
        deleted_result = subprocess.run(
            deleted_diff_cmd,
            capture_output=True, text=True, check=False,
        )
        deleted_files = set()
        if deleted_result.returncode == 0:
            deleted_files = {f.strip() for f in deleted_result.stdout.splitlines() if f.strip()}

    # 내부 산출물 감지 — tests/oracles/, phase attestation 경로, 삭제된 파일은 허용
    blocked: List[str] = []
    allowed: List[str] = []
    for f in changed_files:
        normalized = f.replace("\\", "/").strip()
        # 삭제된 파일은 허용 (내부 파일 정리 행위)
        if f in deleted_files:
            allowed.append(f)
        # oracle 파일은 명시적 허용 (WORKSPACE_INTERNAL_PATTERNS와 무관)
        elif normalized.startswith("tests/oracles/"):
            allowed.append(f)
        # .pipeline/** 내부 디렉토리는 impl 브랜치에서 차단 (.gitignore 대상, 강제 추가로만 가능)
        # phase-attestation/* 브랜치의 evidence 파일도 impl PR에는 포함하면 안 됨
        # (IMP-20260528-3898: 사용자 REJECT — impl PR diff에 .pipeline/** 포함 차단)
        elif normalized.startswith(".pipeline/"):
            blocked.append(f)
        elif _is_internal_artifact(f):
            blocked.append(f)
        else:
            allowed.append(f)

    if blocked:
        # failure_packet 기록 (state 로드 시도)
        _pf_state: Optional[Dict[str, Any]] = None
        try:
            _pf_state = _load()
        except Exception:
            _pf_state = None
        if _pf_state is not None and isinstance(_pf_state, dict):
            _pf_pid = str(_pf_state.get("pipeline_id") or "UNKNOWN")
            _pf_paths = _contract_paths(_pf_pid)
            _attempt = _next_failure_attempt(_pf_paths, "preflight_pr_impl")
            _pf_packet_path = (
                _failure_root_from_paths(_pf_paths) / f"preflight_pr_impl_attempt_{_attempt}.json"
            )
            _pf_packet: Dict[str, Any] = {
                "schema_version": FAILURE_PACKET_SCHEMA_VERSION,
                "pipeline_id": _pf_pid,
                "phase": "dev",
                "gate": "preflight_pr_impl",
                "status": "FAIL",
                "failure_code": "preflight_pr_impl_internal_artifact",
                "failure_category": "internal_artifact_in_pr",
                "summary_ko": (
                    f"preflight-pr-impl FAIL: 구현 PR에 내부 산출물 {len(blocked)}개 포함"
                ),
                "expected": "구현 PR에 내부 산출물(build_report.xml 등) 미포함",
                "actual": f"차단 파일 {len(blocked)}개: {', '.join(blocked[:5])}",
                "owner": "Dev",
                "return_phase": "dev",
                "required_actions": [
                    "아래 내부 산출물을 .gitignore에 추가하거나 git rm --cached로 되돌리세요: "
                    + ", ".join(blocked[:3]),
                    "내부 산출물은 PR에 포함하지 않습니다 (pipeline 런타임 전용 파일).",
                ],
                "retry_allowed": True,
                "minimal_rerun": [
                    "python", "pipeline.py", "gates", "preflight-pr-impl",
                ],
                "command": [
                    "python", "pipeline.py", "gates", "preflight-pr-impl",
                ],
                "packet_path": str(_pf_packet_path),
                "attempt": _attempt,
                "attempt_count": _attempt,
                "recorded_at": _now(),
                "created_at": _now(),
                "exit_code": 1,
                "blocking_condition": "",
                "evidence_paths": [],
                "note": "",
                "failed_checks": [],
                "repair_owner": "Dev",
                "report_excerpt": {"blockers": blocked, "allowed": allowed, "summary": {}},
                "escalation_reason": None,
            }
            _write_json(_pf_packet_path, _pf_packet)
            _pf_state.setdefault("failure_packets", [])
            if isinstance(_pf_state.get("failure_packets"), list):
                _pf_state["failure_packets"].append({
                    "gate": "preflight_pr_impl",
                    "attempt": _attempt,
                    "path": str(_pf_packet_path),
                    "packet_path": str(_pf_packet_path),
                    "recorded_at": _pf_packet["recorded_at"],
                    "repair_owner": "Dev",
                    "schema_version": FAILURE_PACKET_SCHEMA_VERSION,
                    "status": "FAIL",
                    "failure_code": _pf_packet["failure_code"],
                    "failure_category": "internal_artifact_in_pr",
                    "owner": "Dev",
                    "return_phase": "dev",
                    "attempt_count": _attempt,
                    "escalation_reason": None,
                })
            _save(_pf_state)
            _print_failure_packet_console(_pf_packet)
        print("[PIPELINE ERROR] preflight-pr-impl FAIL: 다음 내부 산출물이 구현 PR에 포함되어 있습니다:")
        for f in blocked:
            print(f"  - {f}")
        print("  해결: .gitignore에 해당 파일을 추가하거나 git rm --cached로 되돌리세요.")
        sys.exit(1)

    print(
        GREEN(
            f"[PREFLIGHT-PR-IMPL PASS] "
            f"changed={len(changed_files)}개 파일 / "
            f"blocked=0 / allowed={len(allowed)}개"
        )
    )
    sys.exit(0)


# ─── IMP-20260529-D8BA MT-1: gates secrets 서브커맨드 ─────────────────────────
def _cmd_gates_secrets(args: argparse.Namespace) -> None:
    """민감 정보 검사 게이트: PR diff와 주요 보고서에서 secret-like 문자열을 검사한다.

    발견 시 exit 1. 원문은 절대 출력하지 않고 마스킹된 접두사만 표시한다.
    state 파일 없이도 동작하며 pipeline_state.json을 변경하지 않는 read-only gate.

    Args:
        args: argparse Namespace (files, base_ref, report_files 옵션).
    """
    files_to_scan: List[Path] = []
    explicit_files = getattr(args, "files", None)

    if explicit_files:
        # --files 명시 시 git diff 우회
        for raw in explicit_files.split(","):
            f = raw.strip()
            if not f:
                continue
            p = Path(f)
            if p.exists() and p.is_file():
                files_to_scan.append(p)
    else:
        # git diff 기반 (PR diff)
        try:
            base_ref = getattr(args, "base_ref", None) or "origin/main"
            result = subprocess.run(
                ["git", "diff", "--name-only", f"{base_ref}...HEAD"],
                capture_output=True, text=True, cwd=str(BASE_DIR), timeout=30,
                encoding="utf-8", errors="replace",
            )
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    rel = line.strip()
                    if not rel:
                        continue
                    p = BASE_DIR / rel
                    if p.exists() and p.is_file():
                        files_to_scan.append(p)
        except Exception:
            pass  # git 실패 시 기본 보고서만 검사

    # 기본 보고서 파일 자동 포함 (PR diff와 무관하게 항상 검사)
    if not explicit_files:
        for fname in (
            "failure_packet.json", "deployment_manifest.json", "build_report.xml",
            "qa_report.xml", "human_acceptance_packet.md", "acceptance_packet.md",
        ):
            p = BASE_DIR / fname
            if p.exists() and p.is_file() and p not in files_to_scan:
                files_to_scan.append(p)

    # --report-files 추가
    extra = getattr(args, "report_files", None)
    if extra:
        for raw in extra.split(","):
            f = raw.strip()
            if not f:
                continue
            p = Path(f)
            if p.exists() and p.is_file() and p not in files_to_scan:
                files_to_scan.append(p)

    # 파일명 검사 (내용과 무관하게 secret 파일명 패턴이면 차단)
    all_findings: List[Dict[str, Any]] = []
    for file_path in files_to_scan:
        fname = file_path.name
        if _is_secret_filename(fname):
            all_findings.append({
                "file": str(file_path),
                "pattern_name": "secret_filename",
                "masked": f"[파일명 차단] {_mask_secret(fname, prefix_len=2)}",
            })

    # git diff 파일 경로 기반 파일명 검사 (--files 미지정 시 PR diff 경로도 검사)
    if not explicit_files:
        try:
            base_ref = getattr(args, "base_ref", None) or "origin/main"
            result = subprocess.run(
                ["git", "diff", "--name-only", f"{base_ref}...HEAD"],
                capture_output=True, text=True, cwd=str(BASE_DIR), timeout=30,
                encoding="utf-8", errors="replace",
            )
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    rel = line.strip()
                    if not rel:
                        continue
                    # 파일명만 추출하여 secret filename 패턴 검사
                    diff_fname = rel.split("/")[-1]
                    if _is_secret_filename(diff_fname):
                        all_findings.append({
                            "file": rel,
                            "pattern_name": "secret_filename_in_diff",
                            "masked": f"[PR diff 파일명 차단] {_mask_secret(diff_fname, prefix_len=2)}",
                        })
        except Exception:
            pass

    # 파일 내용 스캔
    for file_path in files_to_scan:
        # 자기 자신(pipeline.py) 및 SSoT 정의 파일은 검사 제외:
        # SECRET_PATTERNS 자체가 정규식 리터럴이므로 false positive 발생 위험.
        try:
            resolved = file_path.resolve()
            if resolved.name == "pipeline.py":
                continue
            # tests/oracles 및 tests/e2e의 dummy 테스트 데이터는 검사 제외
            try:
                rel_path = resolved.relative_to(BASE_DIR)
                rel_str = str(rel_path).replace("\\", "/")
                if rel_str.startswith("tests/oracles/") or rel_str.startswith("tests/e2e/"):
                    continue
            except ValueError:
                pass
        except Exception:
            pass
        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for finding in _scan_text_for_secrets(text):
            finding["file"] = str(file_path)
            all_findings.append(finding)

    # 결과 저장 (.pipeline/secrets_gate_result.json — read-only diagnostic)
    gate_result: Dict[str, Any] = {
        "status": "FAIL" if all_findings else "PASS",
        "finding_count": len(all_findings),
        "files_scanned": [str(f) for f in files_to_scan],
        "findings_masked": [
            {"file": f["file"], "pattern": f["pattern_name"], "masked": f["masked"]}
            for f in all_findings
        ],
        "generated_at": _now(),
    }
    try:
        gate_result_path = BASE_DIR / ".pipeline" / "secrets_gate_result.json"
        gate_result_path.parent.mkdir(exist_ok=True)
        _write_json(gate_result_path, gate_result)
    except Exception:
        pass  # 결과 파일 저장 실패는 게이트 결과에 영향 없음

    if all_findings:
        print(f"\n[민감 정보 검사 실패] {len(all_findings)}개의 민감 정보 형식 문자열을 발견했습니다.")
        for f in all_findings:
            print(f"  ⚠  패턴: {f['pattern_name']} | 파일: {f['file']} | 마스킹: {f['masked']}")
        print("\n원문은 출력하지 않습니다. 마스킹된 접두사만 표시합니다.")
        print("해결: 해당 파일에서 민감 정보를 제거하거나 환경변수로 분리하세요.")
        # failure_packet console 출력 (dict 형식)
        failure_packet: Dict[str, Any] = {
            "gate": "secrets",
            "status": "FAIL",
            "failure_code": "secrets_found",
            "failure_category": "security",
            "summary_ko": f"민감 정보 형식 문자열 {len(all_findings)}개 발견 — 원문 마스킹 처리됨",
            "expected": "민감 정보 패턴 0개",
            "actual": f"민감 정보 패턴 {len(all_findings)}개 발견",
            "return_phase": "dev",
            "owner": "Dev",
            "required_actions": ["해당 파일에서 민감 정보 제거 후 재실행"],
        }
        try:
            _print_failure_packet_console(failure_packet)
        except Exception:
            pass
        sys.exit(1)

    print(GREEN(
        f"[민감 정보 검사 통과] "
        f"검사 파일 {len(files_to_scan)}개에서 민감 정보를 발견하지 못했습니다."
    ))
    sys.exit(0)


# ===================================================================
# IMP-20260520-D0BB: Protocol Consistency Guard — CLI 레이어
# [Purpose]: gates consistency 명령과 gates accept hard gate에서 gh CLI로
#   PR 데이터를 수집하고 _check_protocol_consistency() 순수 함수를 실행한다.
# [Assumptions]: gh CLI가 설치/인증되어 있고, 현재 브랜치에 PR이 있다.
#   없으면 PASS가 아니라 BLOCKED로 처리하여 ACCEPT를 막는다.
# [Vulnerability & Risks]: gh API 응답 형식 변경 시 JSON 파싱이 깨질 수 있다.
#   파싱 실패는 BLOCKED로 처리하여 안전하게 차단한다.
# [Improvement]: gh API 호출 결과를 캐시하면 accept/consistency 중복 호출을
#   줄일 수 있다.
# ===================================================================


def _get_consistency_required_actions(result: Dict[str, Any]) -> List[str]:
    """consistency BLOCKED 결과에 대한 재작업 조치 목록을 생성한다.

    Args:
        result: _check_protocol_consistency 또는 CLI 레이어가 만든 결과 dict.
    Returns:
        failure_code에 맞는 한국어 조치 문자열 리스트 (최소 1개).
    """
    code = result.get("failure_code", "")
    actions: List[str] = []
    details = result.get("details", {}) or {}

    if code in ("stale_run_id", "ci_run_id_mismatch"):
        expected_run = details.get("expected_run_id", "최신 run ID")
        actions.append(
            f"PR body와 acceptance packet의 GitHub CI run ID를 "
            f"최신 값({expected_run})으로 갱신하세요."
        )
        actions.append(
            "stale run ID가 남아 있는 모든 위치(PR body, PR 댓글)를 확인하세요."
        )
    elif code == "stale_head_sha":
        actions.append(
            "PR body와 acceptance packet의 head SHA를 "
            "실제 PR head SHA로 갱신하세요."
        )
    elif code == "test_count_mismatch":
        actions.append(
            "PR body와 acceptance packet의 테스트 통과 수를 일치시키세요."
        )
    elif code == "changed_files_mismatch":
        actions.append(
            "PR body의 변경 파일 목록을 실제 PR diff와 일치시키세요."
        )
    elif code == "trust_root_change_undocumented":
        fname = details.get("undocumented_file", "trust-root 파일")
        actions.append(
            f"PR body에 {fname} 변경 내용을 명시적으로 기술하세요."
        )
    elif code == "stale_file_description":
        fname = details.get("stale_file", "파일")
        actions.append(
            f"PR body에서 실제 변경되지 않은 {fname} 관련 설명을 제거하세요."
        )
    elif code in (
        "gh_cli_not_available_for_consistency",
        "pr_not_found_for_consistency",
        "pr_json_parse_error_for_consistency",
    ):
        actions.append(
            "GitHub CLI(gh)가 설치되어 있고 인증되었는지 확인하세요."
        )
        actions.append(
            "올바른 repo(--repo)와 PR 번호(--pr)를 지정했는지 확인하세요."
        )

    if not actions:
        actions.append(
            "일치하지 않는 항목을 PR body 또는 acceptance packet에서 수정하세요."
        )
    return actions


def _write_consistency_result(
    state: Dict[str, Any],
    result: Dict[str, Any],
    pid: str,
) -> None:
    """consistency check 결과를 파일에 기록한다.

    PASS → protocol_consistency_result.json 생성.
    BLOCKED → failure_packet.json (schema_version=2) 생성.

    Args:
        state: 파이프라인 state dict (현재는 직접 사용하지 않으나 시그니처 계약 유지).
        result: consistency 결과 dict.
        pid: pipeline_id.
    """
    out = {
        "schema_version": 2,
        "pipeline_id": pid,
        "generated_at": _now(),
        "status": result.get("status", "BLOCKED"),
        "failure_code": result.get("failure_code", ""),
        "failure_category": result.get("failure_category", ""),
        "blocked_reason": result.get("blocked_reason") or "",
        "allow_accept": result.get("allow_accept", False),
        "details": result.get("details", {}),
    }

    base_dir = Path(".")
    if result.get("status") == "PASS":
        outpath = base_dir / "protocol_consistency_result.json"
        outpath.write_text(
            json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"  결과 파일: {outpath}")
    else:
        # failure_packet schema_v2 생성
        fp = {
            "schema_version": 2,
            "pipeline_id": pid,
            "phase": "harness",
            "gate": "protocol_consistency",
            "status": "BLOCKED",
            "failure_code": result.get(
                "failure_code", "consistency_blocked"
            ),
            "failure_category": result.get(
                "failure_category", "protocol_violation"
            ),
            "summary_ko": (
                result.get("blocked_reason")
                or "Protocol consistency check BLOCKED"
            ),
            "blocking_condition": result.get("blocked_reason") or "",
            "expected": (
                "PR body, acceptance packet, CI run ID, head SHA, "
                "changed files 모두 일치"
            ),
            "actual": (
                result.get("blocked_reason")
                or str(result.get("failure_code"))
            ),
            "evidence_paths": [],
            "command": [
                sys.executable, "pipeline.py", "gates", "consistency",
                "--repo", "...", "--pr", "...",
            ],
            "exit_code": 1,
            "owner": "Pipeline Manager",
            "return_phase": result.get("return_phase", "build"),
            "minimal_rerun": [
                sys.executable, "pipeline.py", "gates", "consistency",
                "--repo", "...", "--pr", "...",
            ],
            "required_actions": _get_consistency_required_actions(result),
            "retry_allowed": True,
            "attempt_count": 1,
            "created_at": _now(),
            "details": result.get("details", {}),
        }
        fp_path = base_dir / "failure_packet.json"
        fp_path.write_text(
            json.dumps(fp, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"  실패 패킷: {fp_path}")


def _collect_pr_consistency_data(
    repo: str,
    pr_num: str,
) -> Dict[str, Any]:
    """gh CLI로 consistency 검사에 필요한 PR 데이터를 수집한다.

    Args:
        repo: owner/repo 문자열.
        pr_num: PR 번호.
    Returns:
        성공 시 {"ok": True, "pr_body": ..., "acceptance_packet_body": ...,
                 "pr_changed_files": [...], "pr_head_sha": ...,
                 "latest_ci_run_id": ..., "latest_ci_run_conclusion": ...}.
        실패 시 {"ok": False, "result": <BLOCKED 결과 dict>}.
    """
    # 1. gh CLI로 PR 메타데이터 조회
    try:
        pr_res = subprocess.run(
            ["gh", "pr", "view", pr_num, "--repo", repo,
             "--json",
             "body,headRefOid,headRefName,files,statusCheckRollup,"
             "isDraft,title,url,number"],
            capture_output=True, encoding="utf-8", text=True, check=False,
        )
    except (FileNotFoundError, OSError):
        return {
            "ok": False,
            "result": {
                "status": "BLOCKED",
                "failure_code": "gh_cli_not_available_for_consistency",
                "failure_category": "protocol_violation",
                "blocked_reason": (
                    "GitHub CLI(gh)가 설치되지 않아 consistency check를 "
                    "수행할 수 없습니다."
                ),
                "return_phase": "build",
                "allow_accept": False,
                "details": {},
            },
        }

    if pr_res.returncode != 0 or not (pr_res.stdout or "").strip():
        err = (pr_res.stderr or "").strip()[:200]
        return {
            "ok": False,
            "result": {
                "status": "BLOCKED",
                "failure_code": "pr_not_found_for_consistency",
                "failure_category": "protocol_violation",
                "blocked_reason": f"PR 조회 실패: {err}",
                "return_phase": "build",
                "allow_accept": False,
                "details": {},
            },
        }

    try:
        pr_data = json.loads(pr_res.stdout)
    except (json.JSONDecodeError, ValueError):
        return {
            "ok": False,
            "result": {
                "status": "BLOCKED",
                "failure_code": "pr_json_parse_error_for_consistency",
                "failure_category": "protocol_violation",
                "blocked_reason": "PR 메타데이터 JSON 파싱 실패.",
                "return_phase": "build",
                "allow_accept": False,
                "details": {},
            },
        }

    if not isinstance(pr_data, dict):
        return {
            "ok": False,
            "result": {
                "status": "BLOCKED",
                "failure_code": "pr_json_parse_error_for_consistency",
                "failure_category": "protocol_violation",
                "blocked_reason": "PR 메타데이터 형식이 올바르지 않습니다.",
                "return_phase": "build",
                "allow_accept": False,
                "details": {},
            },
        }

    # 2. GitHub PR 댓글에서 acceptance packet 조회
    acceptance_packet_body = ""
    try:
        comments_res = subprocess.run(
            ["gh", "api",
             f"repos/{repo}/issues/{pr_num}/comments", "--paginate"],
            capture_output=True, encoding="utf-8", text=True, check=False,
        )
        if comments_res.returncode == 0 and comments_res.stdout:
            comments = json.loads(comments_res.stdout)
            for comment in (comments if isinstance(comments, list) else []):
                body = comment.get("body", "") if isinstance(comment, dict) else ""
                if _ACCEPTANCE_PACKET_COMMENT_TAG in body:
                    acceptance_packet_body = body  # 마지막(최신) 태그 댓글 사용
    except (FileNotFoundError, OSError, json.JSONDecodeError, ValueError):
        pass  # packet 없으면 일부 검사 SKIP

    # 3. 최신 CI run ID 수집 (run ID 최대값 = 가장 최신 run)
    # statusCheckRollup은 oldest-first로 반환되므로 첫 항목 break 대신
    # 정규식 매치 중 run ID 정수값이 최대인 항목을 선택한다.
    latest_ci_run_id = ""
    latest_ci_run_conclusion = ""
    latest_ci_run_id_int = 0
    try:
        status_checks = pr_data.get("statusCheckRollup") or []
        for check in status_checks:
            if not isinstance(check, dict):
                continue
            url = check.get("detailsUrl", "")
            m = _CONSISTENCY_RUN_ID_PATTERN.search(str(url))
            if m:
                run_id_str = m.group(1)
                try:
                    run_id_int = int(run_id_str)
                except ValueError:
                    run_id_int = 0
                if run_id_int > latest_ci_run_id_int:
                    latest_ci_run_id_int = run_id_int
                    latest_ci_run_id = run_id_str
                    latest_ci_run_conclusion = str(check.get("conclusion", ""))
        if not latest_ci_run_id:
            # gh run list로 최신 run 조회
            run_res = subprocess.run(
                ["gh", "run", "list", "--repo", repo, "--branch",
                 str(pr_data.get("headRefName", "")), "--limit", "1",
                 "--json", "databaseId,conclusion"],
                capture_output=True, encoding="utf-8", text=True, check=False,
            )
            if run_res.returncode == 0 and run_res.stdout:
                runs = json.loads(run_res.stdout)
                if runs and isinstance(runs, list):
                    latest_ci_run_id = str(runs[0].get("databaseId", ""))
                    latest_ci_run_conclusion = str(
                        runs[0].get("conclusion", "")
                    )
    except (FileNotFoundError, OSError, json.JSONDecodeError, ValueError):
        pass

    # 4. changed files 수집
    pr_changed_files: List[str] = []
    try:
        files_list = pr_data.get("files") or []
        for f in files_list:
            path = f.get("path", "") if isinstance(f, dict) else str(f)
            if path:
                pr_changed_files.append(path)
    except (AttributeError, TypeError):
        pass

    return {
        "ok": True,
        "pr_body": pr_data.get("body") or "",
        "acceptance_packet_body": acceptance_packet_body,
        "pr_changed_files": pr_changed_files,
        "pr_head_sha": pr_data.get("headRefOid") or "",
        "latest_ci_run_id": latest_ci_run_id,
        "latest_ci_run_conclusion": latest_ci_run_conclusion,
    }


def _run_protocol_consistency_check(
    state: Dict[str, Any],
    args: argparse.Namespace,
    pid: str,
) -> None:
    """gates consistency CLI 핸들러.

    gh CLI로 PR 데이터를 수집하고 _check_protocol_consistency()를 실행한다.
    PASS → protocol_consistency_result.json 생성 + exit 0.
    BLOCKED → failure_packet.json 생성 + exit 1.

    Args:
        state: 파이프라인 state dict.
        args: argparse Namespace (repo, pr 속성 필요).
        pid: pipeline_id.
    """
    if getattr(args, "dry_run", False):
        input_file = getattr(args, "input_file", None)
        if not input_file:
            print("[CONSISTENCY ERROR] --dry-run requires --input-file")
            sys.exit(2)
        try:
            input_data = json.loads(Path(input_file).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"[CONSISTENCY ERROR] --input-file 읽기 실패: {exc}")
            sys.exit(2)
        result = _check_protocol_consistency(
            pr_body=input_data.get("pr_body", ""),
            acceptance_packet_body=input_data.get("acceptance_packet_body", ""),
            pr_changed_files=input_data.get("pr_changed_files", []),
            pr_head_sha=input_data.get("pr_head_sha", ""),
            latest_ci_run_id=input_data.get("latest_ci_run_id", ""),
            latest_ci_run_conclusion=input_data.get("latest_ci_run_conclusion", ""),
        )
        print(json.dumps(result, ensure_ascii=False))
        sys.exit(0 if result.get("status") == "PASS" else 1)

    repo = args.repo
    pr_num = args.pr

    collected = _collect_pr_consistency_data(repo, pr_num)
    if not collected.get("ok"):
        result = collected["result"]
        _write_consistency_result(state, result, pid)
        print(f"[CONSISTENCY BLOCKED] {result.get('blocked_reason')}")
        sys.exit(1)

    # consistency 검사 실행
    result = _check_protocol_consistency(
        pr_body=collected["pr_body"],
        acceptance_packet_body=collected["acceptance_packet_body"],
        pr_changed_files=collected["pr_changed_files"],
        pr_head_sha=collected["pr_head_sha"],
        latest_ci_run_id=collected["latest_ci_run_id"],
        latest_ci_run_conclusion=collected["latest_ci_run_conclusion"],
    )

    _write_consistency_result(state, result, pid)

    if result["status"] == "PASS":
        print("[CONSISTENCY PASS] 모든 일치 검사 통과")
        sys.exit(0)
    else:
        msg = (
            result.get("blocked_reason")
            or result.get("failure_code")
            or "consistency check failed"
        )
        print(f"[CONSISTENCY BLOCKED] {msg}")
        sys.exit(1)


def _run_protocol_consistency_inline(
    state: Dict[str, Any],
    repo: str,
    pr_num: str,
    pid: str,
) -> Dict[str, Any]:
    """gates accept 내부에서 consistency 검사를 실행하고 결과 dict를 반환한다.

    _run_protocol_consistency_check 와 달리 sys.exit를 호출하지 않고,
    호출자(gates accept)가 BLOCKED 처리(failure packet + _die)를 하도록
    결과 dict만 반환한다.

    Args:
        state: 파이프라인 state dict.
        repo: owner/repo 문자열.
        pr_num: PR 번호.
        pid: pipeline_id.
    Returns:
        consistency 결과 dict (status / failure_code / allow_accept / details 등).
    """
    collected = _collect_pr_consistency_data(repo, pr_num)
    if not collected.get("ok"):
        return collected["result"]

    return _check_protocol_consistency(
        pr_body=collected["pr_body"],
        acceptance_packet_body=collected["acceptance_packet_body"],
        pr_changed_files=collected["pr_changed_files"],
        pr_head_sha=collected["pr_head_sha"],
        latest_ci_run_id=collected["latest_ci_run_id"],
        latest_ci_run_conclusion=collected["latest_ci_run_conclusion"],
    )


def _get_consistency_pr_target(state: Dict[str, Any]) -> Dict[str, str]:
    """현재 브랜치에 연결된 PR의 repo/번호를 gh CLI로 조회한다.

    consistency 검사는 현재 파이프라인 소유의 PR에 대해서만 의미가 있다.
    조회된 PR 제목에 현재 pipeline_id가 포함되지 않으면(= 다른 파이프라인의
    PR이거나 무관한 PR이면) consistency 대상에서 제외한다. 이 경우 PR 소유권
    불일치는 _validate_pr_title_matches_pipeline 가 별도로 차단한다.

    Args:
        state: 파이프라인 state dict (pipeline_id로 PR 소유권 검증).
    Returns:
        {"repo": owner/repo, "pr": PR번호}. 조회 실패 또는 PR이 현재
        파이프라인 소유가 아니면 빈 값 dict.
    """
    try:
        res = subprocess.run(
            ["gh", "pr", "view", "--json", "number,url,title"],
            capture_output=True, encoding="utf-8", text=True, check=False,
        )
    except (FileNotFoundError, OSError):
        return {"repo": "", "pr": ""}
    if res.returncode != 0 or not (res.stdout or "").strip():
        return {"repo": "", "pr": ""}
    try:
        data = json.loads(res.stdout)
    except (json.JSONDecodeError, ValueError):
        return {"repo": "", "pr": ""}
    if not isinstance(data, dict):
        return {"repo": "", "pr": ""}
    pr_number = str(data.get("number") or "")
    pr_url = str(data.get("url") or "")
    pr_title = str(data.get("title") or "")
    # PR 소유권 검증 — 현재 pipeline_id가 PR 제목에 없으면 무관한 PR로 간주.
    pipeline_id = str(state.get("pipeline_id") or "")
    if pipeline_id and pipeline_id not in pr_title:
        return {"repo": "", "pr": ""}
    match = _PR_URL_REPO_PATTERN.search(pr_url)
    repo_slug = match.group(1) if match else ""
    return {"repo": repo_slug, "pr": pr_number}


def _update_pr_body_with_metrics(state: Dict[str, Any]) -> None:
    """github-ci PASS 후 PR body에 소요 시간 요약 섹션을 업데이트한다.

    IMP-20260522-29C1 fix-forward v3: 사용자가 ACCEPT/REJECT를 결정하기 전에
    GitHub PR 화면에서 metrics 요약을 볼 수 있도록 PR body를 갱신한다.
    gh CLI가 없거나 열린 PR이 없으면 조용히 skip한다.
    """
    try:
        metrics_str = _format_metrics_summary_ko(_collect_pipeline_metrics(state))
        metrics_section = f"\n\n## 소요 시간 요약\n```\n{metrics_str}\n```\n"
        pr_view = subprocess.run(
            ["gh", "pr", "view", "--json", "body,number"],
            capture_output=True, text=True, encoding="utf-8", check=False,
        )
        if pr_view.returncode != 0 or not pr_view.stdout.strip():
            return
        pr_info = json.loads(pr_view.stdout)
        pr_body = pr_info.get("body") or ""
        pr_num = pr_info.get("number")
        if not pr_num:
            return
        if "## 소요 시간 요약" in pr_body:
            pr_body = re.sub(
                r"\n\n## 소요 시간 요약\n```\n.*?```\n",
                metrics_section.rstrip("\n"),
                pr_body,
                flags=re.DOTALL,
            )
        else:
            pr_body = pr_body.rstrip("\n") + metrics_section
        subprocess.run(
            ["gh", "pr", "edit", str(pr_num), "--body", pr_body],
            capture_output=True, text=True, encoding="utf-8", check=False,
        )
        print("[METRICS] PR body에 소요 시간 요약 업데이트 완료")
    except Exception:
        pass


# ─── IMP-20260531-BBDB MT-4: GitHub PR/CI 조회 + 댓글 갱신 헬퍼 ─────────────────
# [Purpose]: gh CLI로 현재 PR URL/head SHA/CI run ID/PR body 조회. PR 댓글 갱신.
# [Assumptions]: gh CLI 설치 + 인증된 환경. 미설치 시 모든 함수가 None/빈 문자열 반환.
# [Vulnerability & Risks]:
#   - gh CLI timeout 미준수 시 hang 가능 (각 호출 10~15초 timeout 설정).
#   - PR 댓글 갱신 실패 시 silent — 콘솔 발급 코드는 항상 진행.
# [Improvement]: GitHub REST API 직접 호출로 gh CLI 의존성 제거.

def _get_current_pr_url() -> Optional[str]:
    """현재 브랜치의 PR URL을 gh CLI로 조회. 없으면 None.

    Returns:
        PR URL 문자열 또는 None (gh CLI 미설치/PR 없음/오류).
    Raises:
        없음.
    """
    try:
        r = subprocess.run(
            ["gh", "pr", "view", "--json", "url", "--jq", ".url"],
            capture_output=True, text=True, timeout=10,
            encoding="utf-8", errors="replace",
        )
        if r.returncode == 0:
            out = r.stdout.strip()
            return out if out else None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return None


def _get_current_pr_head_sha() -> Optional[str]:
    """현재 PR의 head commit SHA를 gh CLI로 조회.

    Returns:
        head SHA 문자열 또는 None (gh CLI 미설치/오류).
    Raises:
        없음.
    """
    try:
        r = subprocess.run(
            ["gh", "pr", "view", "--json", "headRefOid", "--jq", ".headRefOid"],
            capture_output=True, text=True, timeout=10,
            encoding="utf-8", errors="replace",
        )
        if r.returncode == 0:
            out = r.stdout.strip()
            return out if out else None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return None


def _get_pr_branch_ci_run_id(branch: Optional[str] = None) -> Optional[str]:
    """현재 PR/브랜치에 해당하는 GitHub Actions CI run ID 조회.

    전역 최신 run 대신 현재 브랜치의 최신 run만 선택하여
    phase-attestation 브랜치나 다른 PR run 오염을 방지한다.
    IMP-20260531-4AC2: _get_latest_ci_run_id 대체 함수.

    Args:
        branch: 명시적 브랜치명. None이면 git rev-parse --abbrev-ref HEAD 자동 조회.
    Returns:
        run ID 문자열 또는 None (gh CLI 미설치/run 없음/detached HEAD).
    Raises:
        없음.
    """
    if branch is None:
        # 현재 git 브랜치 자동 조회 (shutil.which로 PATH에서 첫 번째 git 실행 파일 사용)
        git_path = shutil.which("git") or "git"
        try:
            br_res = subprocess.run(
                [git_path, "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True, text=True, timeout=5,
                encoding="utf-8", errors="replace",
            )
            if br_res.returncode == 0:
                branch = br_res.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return None
    # detached HEAD 또는 빈 문자열 안전 폴백
    if not branch or branch == "HEAD":
        return None
    # shutil.which로 PATH에서 첫 번째 gh 실행 파일 사용
    # Windows에서 .CMD가 .EXE보다 PATH 우선이 아니므로 which로 명시적 경로 확보
    gh_path = shutil.which("gh")
    if not gh_path:
        return None
    try:
        r = subprocess.run(
            [gh_path, "run", "list", "--branch", branch, "--limit", "1",
             "--json", "databaseId",
             "--jq", ".[0].databaseId"],
            capture_output=True, text=True, timeout=10,
            encoding="utf-8", errors="replace",
        )
        if r.returncode == 0:
            out = r.stdout.strip()
            return out if out else None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return None


def _get_latest_ci_run_id() -> Optional[str]:
    """[DEPRECATED] 브랜치 필터 없는 전역 최신 CI run 조회.

    IMP-20260531-4AC2: _get_pr_branch_ci_run_id()로 위임.
    하위 호환성 유지 목적으로 보존.

    Returns:
        run ID 문자열 또는 None (gh CLI 미설치/run 없음/detached HEAD).
    Raises:
        없음.
    """
    return _get_pr_branch_ci_run_id()


def _get_pr_body_text() -> Optional[str]:
    """현재 PR 본문 텍스트를 gh CLI로 조회.

    Returns:
        PR 본문 문자열 또는 None (gh CLI 미설치/PR 없음).
    Raises:
        없음.
    """
    try:
        r = subprocess.run(
            ["gh", "pr", "view", "--json", "body", "--jq", ".body"],
            capture_output=True, text=True, timeout=10,
            encoding="utf-8", errors="replace",
        )
        if r.returncode == 0:
            out = r.stdout
            return out if out else None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return None


def _get_pr_changed_files() -> List[str]:
    """현재 PR의 변경 파일 목록을 gh CLI로 조회.

    Returns:
        변경 파일 경로 리스트 (빈 리스트면 gh CLI 없거나 PR 없음).
    Raises:
        없음.
    """
    try:
        r = subprocess.run(
            ["gh", "pr", "view", "--json", "files", "--jq", "[.files[].path]"],
            capture_output=True, text=True, timeout=10,
            encoding="utf-8", errors="replace",
        )
        if r.returncode == 0:
            out = r.stdout.strip()
            if out:
                parsed = json.loads(out)
                if isinstance(parsed, list):
                    return [str(p) for p in parsed]
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError, json.JSONDecodeError):
        pass
    return []


def _update_github_acceptance_comment(req: Dict[str, Any], evidence: str) -> None:
    """GitHub PR에 최종 확인 안내 댓글을 생성하거나 최신 내용으로 갱신.

    기존 acceptance-packet 태그(<!-- pipeline-human-acceptance-packet -->)
    가 있는 댓글이 있으면 PATCH로 갱신, 없으면 신규 생성.

    Args:
        req: acceptance_request dict (pipeline_id, nonce, request_id, pr_url, github_ci_run_id 포함).
        evidence: 결과물 경로 또는 URL.
    Raises:
        없음 (모든 외부 호출 실패는 swallow).
    """
    if req is None:
        return
    if not isinstance(req, dict):
        return
    pipeline_id = str(req.get("pipeline_id", ""))
    nonce = str(req.get("nonce", ""))
    request_id = str(req.get("request_id", ""))
    pr_url = str(req.get("pr_url", "") or "")
    ci_run_id = str(req.get("github_ci_run_id", "") or "")

    accept_code = f"ACCEPT-{pipeline_id}-{nonce}"
    reject_code = f"REJECT-{pipeline_id}-{nonce}"

    ci_link = ""
    if ci_run_id:
        ci_link = f"https://github.com/hojiyong2-commits/Pipeline/actions/runs/{ci_run_id}"

    # PR 변경 파일 목록 (IMP-20260531-BBDB: 정확한 파일 수 표시로 stale "3개" 문제 해결)
    changed_files = _get_pr_changed_files()
    files_section = ""
    if changed_files:
        files_list = "\n".join(f"- {f}" for f in sorted(changed_files))
        files_section = f"\n### 변경된 파일 ({len(changed_files)}개)\n{files_list}\n"

    comment_body = f"""<!-- pipeline-human-acceptance-packet -->
## 최종 확인 안내

이 댓글은 사용자 최종 승인/거절 판단에 사용됩니다.
**아래 확인 코드를 통해서만 승인이 가능합니다.**

판단 정보 상태: **판단 가능**

### 확인할 결과물
결과물: {evidence}
PR: {pr_url}
GitHub Actions: {ci_link}
승인 요청 ID: {request_id}
{files_section}
### 승인 방법
결과물을 확인하신 후 아래 코드를 **정확히** 입력하세요.

**[O] 승인:** {accept_code}

**[X] 거절:** {reject_code}: 거절 이유

주의: 이 코드는 일회용입니다. PR에 새 커밋이 push되면 새 코드가 필요합니다.
재발급: python pipeline.py gates request-accept --evidence <결과물>
"""

    try:
        # PR 번호 조회 (pr_url에서 추출 또는 gh CLI 사용)
        pr_number = ""
        if pr_url:
            import re as _re
            m = _re.search(r"/pull/(\d+)", pr_url)
            if m:
                pr_number = m.group(1)
        if not pr_number:
            r_num = subprocess.run(
                ["gh", "pr", "view", "--json", "number", "--jq", ".number"],
                capture_output=True, text=True, timeout=10,
                encoding="utf-8", errors="replace",
            )
            if r_num.returncode == 0:
                pr_number = r_num.stdout.strip()

        # 기존 acceptance-packet 댓글 전부 삭제 (Python 파싱 — jq 의존 없음)
        if pr_number:
            r_comments = subprocess.run(
                ["gh", "api",
                 f"repos/hojiyong2-commits/Pipeline/issues/{pr_number}/comments",
                 "--paginate"],
                capture_output=True, text=True, timeout=30,
                encoding="utf-8", errors="replace",
            )
            if r_comments.returncode == 0 and r_comments.stdout.strip():
                try:
                    all_comments = json.loads(r_comments.stdout.strip())
                    tag = "pipeline-human-acceptance-packet"
                    old_ids = [
                        str(c["id"])
                        for c in all_comments
                        if isinstance(c, dict) and tag in str(c.get("body", ""))
                    ]
                except (json.JSONDecodeError, ValueError, TypeError):
                    old_ids = []
                for old_id in old_ids:
                    subprocess.run(
                        ["gh", "api",
                         f"repos/hojiyong2-commits/Pipeline/issues/comments/{old_id}",
                         "-X", "DELETE"],
                        capture_output=True, timeout=10,
                        encoding="utf-8", errors="replace",
                    )

        # 새 댓글 생성
        subprocess.run(
            ["gh", "pr", "comment", "--body", comment_body],
            capture_output=True, timeout=15,
            encoding="utf-8", errors="replace",
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return  # gh CLI 실패는 silent — 콘솔 발급 코드는 항상 표시됨


# ─── IMP-20260531-BBDB MT-2: gates request-accept 서브커맨드 ───────────────────
# [Purpose]: 사용자 최종 확인 코드(nonce) 발급. acceptance_request.json 생성 + PR 댓글 갱신.
# [Assumptions]: 활성 pipeline_state.json 존재. gh CLI는 선택적(없으면 빈 문자열).
# [Vulnerability & Risks]:
#   - PR 본문 stale 문구 검사가 gh CLI 의존이라 CI 외 환경에서는 검사 생략 가능.
#   - 동일 evidence 경로에 대해 여러 번 호출하면 마지막 nonce만 유효 (이전 코드 무효화는 정상 동작).
# [Improvement]: pre-flight로 모든 외부 gate PASS 여부도 함께 검사하여 조기 차단.
# ─── AC Fulfillment Table (IMP-20260602-1ABE MT-4) ──────────────────────────
def _get_acs_linked_mts(state: Dict[str, Any], ac_id: str) -> List[str]:
    """structured AC id에 연결된 MT 목록 반환."""
    linked: List[str] = []
    atomic_plan = state.get("atomic_plan") or {}
    for mt in atomic_plan.get("micro_tasks", []):
        if not isinstance(mt, dict):
            continue
        mt_id = str(mt.get("id", ""))
        covers = mt.get("covers_ac")
        if isinstance(covers, list) and ac_id in [str(c).strip() for c in covers]:
            linked.append(mt_id)
        elif isinstance(covers, str):
            cov_list = [c.strip() for c in covers.split(",")]
            if ac_id in cov_list:
                linked.append(mt_id)
    return linked


def _get_impl_evidence_for_ac(state: Dict[str, Any], ac_id: str) -> List[str]:
    """scope_manifest의 implemented_tasks에서 AC id의 implementation_evidence 수집."""
    evidence: List[str] = []
    gates = state.get("module_gates") or {}
    modules = gates.get("modules") or {}
    if not isinstance(modules, dict):
        return evidence
    for mt_id, module in modules.items():
        if not isinstance(module, dict):
            continue
        dev_step = module.get("dev") or {}
        scope = dev_step.get("scope") or {}
        # scope에는 _validate_module_scope_manifest 결과가 들어있을 수도,
        # 또는 implemented_tasks가 별도 저장될 수도 있다.
        # implemented_tasks가 scope에 보존되었으면 거기서 추출
        implemented = scope.get("implemented_tasks") if isinstance(scope, dict) else None
        if not isinstance(implemented, list):
            continue
        for task in implemented:
            if not isinstance(task, dict):
                continue
            if ac_id in task.get("implemented_ac", []):
                for ev in task.get("implementation_evidence", []):
                    evidence.append(f"{mt_id}: {ev}")
    return evidence


def _get_qa_verification_for_ac(state: Dict[str, Any], ac_id: str) -> List[str]:
    """module qa report 파일에서 ac_verification 결과 수집."""
    verifications: List[str] = []
    gates = state.get("module_gates") or {}
    modules = gates.get("modules") or {}
    if not isinstance(modules, dict):
        return verifications
    for mt_id, module in modules.items():
        if not isinstance(module, dict):
            continue
        qa_step = module.get("qa") or {}
        report_file = qa_step.get("report_file")
        if not report_file or not Path(report_file).exists():
            continue
        try:
            tree = ET.parse(report_file)
            root = tree.getroot()
            ac_ver = root.find(".//ac_verification")
            if ac_ver is None:
                continue
            for crit in ac_ver.findall("criterion"):
                if crit.get("ac_id") == ac_id:
                    status = crit.get("status", "?")
                    ev = crit.get("evidence", "")
                    verifications.append(f"{mt_id} qa: {status} — {ev[:80]}")
        except (ET.ParseError, OSError):
            continue
    return verifications


def _get_codex_status_for_ac(ac_id: str) -> str:
    """codex_review_result.json의 criteria_review에서 AC id 상태 반환."""
    review_path = BASE_DIR / "codex_review_result.json"
    if not review_path.exists():
        return "N/A"
    try:
        data = json.loads(review_path.read_text(encoding="utf-8", errors="replace"))
    except (json.JSONDecodeError, OSError):
        return "N/A"
    criteria_review = data.get("criteria_review") or []
    if not isinstance(criteria_review, list):
        return "N/A"
    for item in criteria_review:
        if isinstance(item, dict) and item.get("ac_id") == ac_id:
            return str(item.get("status", "?"))
    return "N/A"


def _build_ac_fulfillment_table(state: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
    """structured AC 기록에서 AC 충족표를 자동 조립한다 (legacy면 None)."""
    structured_ac = state.get("structured_acceptance_criteria") or []
    if not structured_ac:
        return None

    table: List[Dict[str, Any]] = []
    for ac in structured_ac:
        if not isinstance(ac, dict):
            continue
        ac_id = ac.get("ac_id", "")
        linked_mt = _get_acs_linked_mts(state, ac_id)
        impl_evidence = _get_impl_evidence_for_ac(state, ac_id)
        verifications = _get_qa_verification_for_ac(state, ac_id)
        codex_status = _get_codex_status_for_ac(ac_id)

        # result 판정
        result = "PASS"
        if not impl_evidence:
            result = "PENDING"
        if not verifications:
            result = "PENDING"

        table.append({
            "ac_id": ac_id,
            "requirement": ac.get("requirement", ""),
            "linked_mt": linked_mt,
            "implementation_evidence": impl_evidence,
            "verification": verifications,
            "codex_status": codex_status,
            "result": result,
            "user_visible": ac.get("user_visible", True),
        })
    return table


def _format_ac_fulfillment_output(
    table: List[Dict[str, Any]],
    iqr_summary: Optional[List[str]] = None,
) -> str:
    """모바일 친화적 줄바꿈 형식으로 충족표 출력 문자열 생성."""
    lines: List[str] = []
    lines.append("[요구사항 충족표]")
    lines.append("")

    user_visible = [e for e in table if e.get("user_visible", True)]
    internal = [e for e in table if not e.get("user_visible", True)]

    for entry in user_visible:
        lines.append(f"{entry['ac_id']}:")
        lines.append("요구사항:")
        req = entry.get("requirement", "")
        # 60자 단위 줄바꿈
        for i in range(0, len(req), 60):
            lines.append(f"  {req[i:i+60]}")
        linked = entry.get("linked_mt", [])
        lines.append(f"구현 작업: {', '.join(linked) if linked else '(없음)'}")
        impl = entry.get("implementation_evidence", [])
        lines.append(f"구현 근거: {' / '.join(impl[:2]) if impl else '(없음)'}")
        verif = entry.get("verification", [])
        lines.append(f"검증: {' / '.join(verif[:2]) if verif else '(없음)'}")
        lines.append(f"결과: {entry.get('result', '?')}")
        lines.append("")

    if internal:
        lines.append("[자동 검증 요약]")
        lines.append("")
        for entry in internal:
            req = entry.get("requirement", "")[:60]
            lines.append(f"{entry['ac_id']}: {entry.get('result', '?')} — {req}")
        lines.append("")

    if iqr_summary:
        lines.append("[내부 품질 조건]")
        for iqr in iqr_summary:
            lines.append(f"  {iqr}")
        lines.append("")

    return "\n".join(lines)


# ─── IMP-20260603-9934 MT-2: Final Packet Freeze Guard — gates accept 검증 ──────
def _check_packet_freeze_status(req: Dict[str, Any]) -> Optional[str]:
    """gates accept 호출 시 packet_sha256 freeze 상태를 검증한다.

    IMP-20260603-9934 MT-2 핵심 로직:
    - schema_version=1 또는 packet_sha256=None이면 검증 생략 (하위 호환).
    - schema_version=2 + packet_sha256 있음: 현재 packet 파일의 sha256을 계산하여 비교.
    - sha256 불일치 시 STALE_PACKET failure_code 문자열을 반환.
    - 파일 없음/읽기 실패 시에도 STALE_PACKET 반환 (packet이 삭제된 경우 차단).

    Args:
        req: acceptance_request.json 데이터 dict (최소 schema_version, packet_sha256,
             packet_path 필드 포함).
    Returns:
        None — 검증 통과 (하위 호환 포함).
        str — 오류 메시지 (STALE_PACKET 차단 사유).
    Raises:
        없음.
    """
    schema_version = req.get("schema_version", 1)
    stored_packet_sha256 = req.get("packet_sha256")

    # schema_version=1 또는 packet_sha256=None이면 검증 생략 (legacy 하위 호환)
    if schema_version != 2 or stored_packet_sha256 is None:
        return None

    stored_packet_path = req.get("packet_path")
    if not stored_packet_path:
        # packet_path 없음: 검증 불가 → STALE_PACKET 처리
        return (
            "[FINAL PACKET FREEZE] acceptance_request.json에 packet_path가 없습니다. "
            "gates request-accept 를 재실행하세요."
        )

    current_sha256 = _compute_packet_sha256(stored_packet_path)
    if current_sha256 is None:
        return (
            f"[FINAL PACKET FREEZE] packet 파일이 없거나 읽을 수 없습니다: {stored_packet_path}\n"
            "  gates request-accept 를 재실행하거나 packet 파일을 복원하세요."
        )

    if current_sha256 != stored_packet_sha256:
        return (
            "[FINAL PACKET FREEZE] human_acceptance_packet.md가 request-accept 이후 변경되었습니다. "
            "(STALE_PACKET)\n"
            f"  저장된 sha256: {stored_packet_sha256[:16]}...\n"
            f"  현재 sha256:   {current_sha256[:16]}...\n"
            "  gates request-accept 를 재실행하여 새 코드를 발급받으세요."
        )

    return None


def _check_packet_freshness_against_actual(
    packet_path: Path,
    actual_pr_head_sha: str,
    actual_ci_run_id: str,
    actual_changed_files: List[str],
) -> Optional[str]:
    """human_acceptance_packet.md가 이미 있을 때 실제 PR/CI/git 상태와 stale 비교.

    IMP-20260603-2E3D MT-2: packet 부재는 차단하지 않고, packet이 있고 실제 상태와 다를 때만
    BLOCKED 메시지를 반환한다. None 반환 시 stale 아님.

    Args:
        packet_path: human_acceptance_packet.md 경로.
        actual_pr_head_sha: 현재 PR head SHA (gh CLI 없으면 빈 문자열).
        actual_ci_run_id: 현재 latest CI run ID.
        actual_changed_files: 현재 git diff 변경 파일 목록.
    Returns:
        stale 메시지(한국어) 또는 None(stale 아님 / packet 부재 / 실제 정보 없음).
    Raises:
        없음.
    """
    if not packet_path.exists():
        return None
    try:
        packet_text = packet_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    rerun_hint = (
        "\n  다음 명령으로 packet을 새로 만들고 재시도하세요:\n"
        "    python pipeline.py report final-packet\n"
        "    python pipeline.py report update-pr-body\n"
        "    python pipeline.py gates request-accept --evidence <결과물>"
    )

    # PR head SHA 비교 — packet의 "PR head SHA:" 다음 줄
    if actual_pr_head_sha:
        m = re.search(r"^PR head SHA:\s*\n([^\n]+)", packet_text, re.MULTILINE)
        if m:
            packet_sha = m.group(1).strip()
            if packet_sha and not packet_sha.startswith("(") and packet_sha != actual_pr_head_sha:
                return (
                    "[FINAL PACKET GATE] 최종 확인 안내가 최신 PR 상태와 다릅니다.\n"
                    "  PR head SHA가 바뀌었습니다.\n\n"
                    f"  현재 PR head SHA: {actual_pr_head_sha}\n"
                    f"  packet의 PR head SHA: {packet_sha}"
                    + rerun_hint
                )

    # CI run ID 비교 — packet의 "CI run ID:" 다음 줄
    if actual_ci_run_id:
        m = re.search(r"^CI run ID:\s*\n([^\n]+)", packet_text, re.MULTILINE)
        if m:
            packet_ci = m.group(1).strip()
            if packet_ci and not packet_ci.startswith("(") and packet_ci != actual_ci_run_id:
                return (
                    "[FINAL PACKET GATE] 최종 확인 안내가 최신 CI 상태와 다릅니다.\n"
                    "  GitHub Actions run ID가 바뀌었습니다.\n\n"
                    f"  현재 CI run ID: {actual_ci_run_id}\n"
                    f"  packet의 CI run ID: {packet_ci}"
                    + rerun_hint
                )

    # 변경 파일 set 비교 — "변경 파일:\n총 N개\n" 다음 빈 줄까지의 파일 경로 추출
    if actual_changed_files:
        m = re.search(
            r"^변경 파일:\s*\n총\s+\d+개\s*\n\s*\n((?:[^\n]+\n)+?)\s*\n",
            packet_text,
            re.MULTILINE,
        )
        if m:
            packet_files_block = m.group(1)
            packet_files = {
                ln.strip() for ln in packet_files_block.split("\n")
                if ln.strip() and not ln.strip().startswith("(")
            }
            actual_set = {str(f).strip() for f in actual_changed_files if str(f).strip()}
            if packet_files and packet_files != actual_set:
                only_actual = sorted(actual_set - packet_files)
                only_packet = sorted(packet_files - actual_set)
                detail_lines: List[str] = []
                if only_actual:
                    detail_lines.append(
                        f"  실제 diff에만 있는 파일: {', '.join(only_actual[:5])}"
                        + (" 외" if len(only_actual) > 5 else "")
                    )
                if only_packet:
                    detail_lines.append(
                        f"  packet에만 있는 파일: {', '.join(only_packet[:5])}"
                        + (" 외" if len(only_packet) > 5 else "")
                    )
                detail = "\n".join(detail_lines)
                return (
                    "[FINAL PACKET GATE] 최종 확인 안내가 실제 변경 파일과 다릅니다.\n\n"
                    + detail + rerun_hint
                )

    return None


def _auto_generate_final_packet_and_update_pr(
    state: Dict[str, Any],
    acceptance_request: Dict[str, Any],
) -> Dict[str, Any]:
    """nonce 발급 직후 final packet을 자동 생성하고 PR 본문을 자동 업데이트한다.

    Args:
        state: 활성 pipeline_state.
        acceptance_request: 방금 발급/재사용한 acceptance_request dict (nonce 포함).
    Returns:
        dict {"packet_path": str, "pr_body_updated": bool}.
    Raises:
        없음 (외부 도구 부재 시 graceful skip).
    """
    evidence_payload = _collect_packet_evidence(
        state, acceptance_request=acceptance_request, base_ref="origin/main"
    )
    content = _build_final_packet_content(evidence_payload)
    packet_path = _write_human_acceptance_packet(content)
    pr_updated = False
    if shutil.which("gh"):
        current_body = _get_pr_body_text() or ""
        new_body = _replace_pr_body_packet_block(current_body, content)
        pr_updated = _gh_edit_pr_body(new_body)
    return {
        "packet_path": _display_path(packet_path),
        "pr_body_updated": pr_updated,
    }


def _cmd_gates_request_accept(args: argparse.Namespace, state: Dict[str, Any]) -> None:
    """gates request-accept 핸들러: stale 검증 + nonce 발급 + packet 자동 생성 + PR 본문 자동 업데이트.

    IMP-20260603-2E3D MT-2: packet 부재로는 차단하지 않는다. packet이 있고 실제 PR/CI/git 상태와
    다르면 BLOCKED를 반환한다. 검증 통과 시 nonce 발급/재사용 후 final packet을 자동 생성하고
    gh CLI가 있으면 PR 본문의 PIPELINE_FINAL_PACKET 블록도 자동 업데이트한다.

    Args:
        args: argparse Namespace (evidence 필수).
        state: 활성 pipeline_state.
    Raises:
        SystemExit: PR 본문 stale 문구 또는 packet stale 발견 시 BLOCKED.
    """
    pipeline_id = str(state.get("pipeline_id", ""))
    if not pipeline_id:
        _die("[BLOCKED] pipeline_state.json에 pipeline_id가 없습니다.")
    evidence = getattr(args, "evidence", None)
    if evidence is None or not str(evidence).strip():
        _die("[BLOCKED] --evidence는 필수입니다 (결과물 경로 또는 URL).")

    # stale 문구 차단 (기존 TEMPORARY_PR_BODY_PATTERNS SSoT 사용)
    pr_body = _get_pr_body_text()
    if pr_body:
        stale = _find_temporary_pr_body_pattern(pr_body)
        if stale:
            _die(
                f"[BLOCKED] PR 본문에 stale 문구가 있습니다: '{stale}'\n"
                "  PR 본문을 최신 상태로 갱신한 후 gates request-accept를 다시 실행하세요."
            )

    # PR/CI 정보 가져오기 (gh CLI 없으면 빈 문자열)
    pr_url = _get_current_pr_url() or ""
    pr_head_sha = _get_current_pr_head_sha() or ""
    ci_run_id = _get_pr_branch_ci_run_id() or ""
    actual_changed_files = _get_git_diff_files(base="origin/main")

    # IMP-20260603-2E3D MT-2: final packet stale 검증 (packet 부재는 차단하지 않음)
    packet_path = _packet_output_path()
    stale_msg = _check_packet_freshness_against_actual(
        packet_path, pr_head_sha, ci_run_id, actual_changed_files
    )
    if stale_msg:
        _die(stale_msg)

    # IMP-20260531-AEF0 MT-1: 기존 요청 로드 → 재사용 판단
    force_new = bool(getattr(args, "force_new_code", False))
    evidence_str = str(evidence)
    is_url = evidence_str.startswith(("http://", "https://"))
    new_evidence_sha256: Optional[str] = None if is_url else _compute_file_sha256(evidence_str)

    existing_req = _load_acceptance_request()
    reuse = False
    reuse_reason = ""
    if existing_req is not None:
        reuse, reuse_reason = _should_reuse_acceptance_nonce(
            existing_req,
            pipeline_id,
            evidence_str,
            new_evidence_sha256,
            pr_head_sha,
            ci_run_id,
            force_new=force_new,
        )

    if reuse and existing_req is not None:
        # 기존 코드 재사용: acceptance_request.json 유지, 표시만 업데이트
        req = existing_req
        nonce = req["nonce"]
        print()
        print(f"  [재사용] {reuse_reason}")
    else:
        # 새 코드 발급
        # IMP-20260603-9934 MT-1: --force-new-code 시 기존 PENDING을 EXPIRED로 처리
        if existing_req is not None:
            print()
            print(f"  [새 코드 발급] {reuse_reason}")
            if force_new and existing_req.get("status") == "PENDING":
                existing_req["status"] = "EXPIRED"
                existing_req["expired_reason"] = "force_new_code"
                try:
                    with open(ACCEPTANCE_REQUEST_FILE, "w", encoding="utf-8") as _fh:
                        json.dump(existing_req, _fh, ensure_ascii=False, indent=2)
                except OSError:
                    pass  # 덮어쓰기 실패해도 새 요청 발급은 계속
        # IMP-20260603-9934 MT-1: packet_path를 _write_acceptance_request에 전달 (schema_version=2)
        req = _write_acceptance_request(
            pipeline_id, evidence_str, pr_url, pr_head_sha, ci_run_id,
            packet_path=str(packet_path),
        )
        nonce = req["nonce"]

    accept_code = f"ACCEPT-{pipeline_id}-{nonce}"
    reject_code = f"REJECT-{pipeline_id}-{nonce}"

    # GitHub 최종 확인 댓글 생성/갱신 (gh CLI 없으면 건너뜀)
    try:
        _update_github_acceptance_comment(req, evidence_str)
    except Exception:
        pass  # GitHub 댓글 실패해도 코드 발급은 계속

    # IMP-20260602-1ABE MT-4: AC 충족표 자동 조립 + 출력 (legacy면 생략)
    ac_table = _build_ac_fulfillment_table(state)
    if ac_table is not None:
        # acceptance_request.json에 ac_fulfillment_table 저장 (재로드 후 갱신)
        try:
            req_path = BASE_DIR / "acceptance_request.json"
            if req_path.exists():
                req_data = json.loads(req_path.read_text(encoding="utf-8", errors="replace"))
                req_data["ac_fulfillment_table"] = ac_table
                req_path.write_text(
                    json.dumps(req_data, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
        except (OSError, json.JSONDecodeError) as exc:
            print(YELLOW(f"  [AC TABLE] acceptance_request.json 저장 실패: {exc}"))

    # IMP-20260603-2E3D MT-2: final packet 자동 생성 + PR 본문 자동 업데이트
    try:
        # acceptance_request.json을 디스크에서 다시 읽어 최신 ac_fulfillment_table 반영
        latest_req = _load_acceptance_request() or req
        auto_result = _auto_generate_final_packet_and_update_pr(state, latest_req)
        print()
        print(f"  [FINAL PACKET 자동 생성] {auto_result['packet_path']}")
        if auto_result["pr_body_updated"]:
            print("  [PR 본문 자동 업데이트] PIPELINE_FINAL_PACKET 블록 교체 완료")
        else:
            print("  [PR 본문 자동 업데이트] gh CLI 없음 또는 갱신 실패 — packet 파일은 보존됨")
    except (OSError, ValueError, KeyError) as exc:
        print(YELLOW(f"  [FINAL PACKET] 자동 생성 중 오류 (계속 진행): {exc}"))

    print()
    print("=" * 62)
    print("  사용자 최종 확인 요청")
    print("=" * 62)
    if pr_url:
        print(f"  PR: {pr_url}")
    if ci_run_id:
        print(f"  GitHub Actions: https://github.com/hojiyong2-commits/Pipeline/actions/runs/{ci_run_id}")
    print(f"  결과물: {evidence}")
    print()

    # IMP-20260602-1ABE MT-4: AC 충족표 출력 (PR/CI 정보 다음, 승인 코드 직전)
    if ac_table:
        print()
        print(_format_ac_fulfillment_output(ac_table))

    print("  위 결과물을 확인하신 후 아래 코드를 입력해 주세요.")
    print()
    print("  [O] 승인하시려면 정확히 아래 코드를 입력하세요:")
    print(f"     {accept_code}")
    print()
    print("  [X] 거절하시려면 아래 형식으로 입력하세요:")
    print(f"     {reject_code}: 거절 이유")
    print("=" * 62)
    print()
    print(f"  승인 요청 ID: {req['request_id']}  (acceptance_request.json 저장됨)")
    reused_label = "재사용" if reuse else "신규 발급"
    _log_event(state, f"acceptance request {reused_label}: request_id={req['request_id']} nonce={nonce}")
    _save(state)


def cmd_gates(args: argparse.Namespace) -> None:
    action = args.gates_action

    # preflight-pr는 pipeline_state.json 없이도 동작 (CI 환경 지원)
    if action == "preflight-pr":
        _cmd_gates_preflight_pr(args)
        return

    # IMP-20260528-3898 MT-2: preflight-pr-impl — 구현 PR 내부 산출물 검사 (state 없이도 동작)
    if action == "preflight-pr-impl":
        _cmd_gates_preflight_pr_impl(args)
        return

    # IMP-20260529-D8BA MT-1: secrets — 민감 정보 검사 (state 없이도 동작)
    if action == "secrets":
        _cmd_gates_secrets(args)
        return

    state = _require_state()
    state = _ensure_v210_fields(state)
    pid = str(state.get("pipeline_id"))
    paths = _contract_paths(pid)

    if action == "init":
        state["external_gates"] = _new_external_gates(enabled=True)
        _enable_phase_attestations(state)
        state["module_gates"] = _ensure_module_gates(state)
        _log_event(state, "three_gate mode enabled (mandatory)")
        _log_event(state, "GitHub phase attestations enabled (mandatory)")
        _save(state)
        print(GREEN(f"\n[THREE GATE + PHASE ATTESTATION ENABLED] {pid}\n"))
        return

    # IMP-20260531-BBDB MT-2: gates request-accept — User Acceptance Nonce 발급
    if action == "request-accept":
        _cmd_gates_request_accept(args, state)
        return

    if action == "status":
        result = {
            "pipeline_id": pid,
            "external_gates": state.get("external_gates"),
            "phase_attestations": state.get("phase_attestations"),
            "blockers": _external_gate_blockers(state),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if not _external_gates_enabled(state):
        state["external_gates"] = _new_external_gates(enabled=True)
        _enable_phase_attestations(state)

    if action == "prepare-phase":
        if args.phase == "pm":
            _validate_pipeline_branch_isolation(state)
        request = _prepare_phase_attestation_request(state, args.phase)
        _log_event(state, f"phase attestation request prepared: {args.phase}")
        _save(state)
        print(GREEN(f"\n[PHASE ATTESTATION REQUEST READY] {args.phase}"))
        print(f"  request: {PHASE_ATTESTATION_REQUEST}")
        print("  next: 일회성 요청/증거를 force-add로 커밋/푸시하고, GitHub Actions 완료 후 아래 명령을 실행하세요:")
        print("        git add -f .pipeline/phase_attestation_request.json .pipeline/phase_evidence")
        print(f"        {YELLOW(f'python pipeline.py gates phase-ci --phase {args.phase} --repo hojiyong2-commits/Pipeline')}\n")
        print(json.dumps(request, ensure_ascii=False, indent=2))
        return

    if action == "phase-ci":
        if not _phase_attestations_enabled(state):
            _die("phase attestations are mandatory. Run `python pipeline.py gates init` to migrate this state.")
        verification = _verify_github_phase_attestation_run(
            repo=args.repo,
            commit=args.commit,
            run_id=args.run_id,
            artifact_name=args.artifact,
            token_env=args.token_env,
            workflow=args.workflow,
            pipeline_id=pid,
            phase=args.phase,
        )
        _record_phase_ci_verification(state, args.phase, verification, args.artifact)
        _save(state)
        color = GREEN if verification["status"] == "PASS" else RED
        print(color(f"\n[PHASE CI GATE {verification['status']}] {args.phase}"))
        print(f"  run_id: {verification.get('run_id')}")
        print(f"  report: {_contract_paths(pid)['phase_ci_root'] / (args.phase + '_result.json')}\n")
        sys.exit(0 if verification["status"] == "PASS" else 1)

    if action == "technical":
        build_blocker = _phase_attestation_blocker_for_phase(state, "harness")
        if build_blocker:
            _die(build_blocker)
        # D4: pr gate → technical gate 연결 (bootstrap_exception 제외)
        bootstrap_exception = state.get("codex_bootstrap_exception", False)
        if not bootstrap_exception:
            _codex_pr_gate_check = _check_codex_pr_gate_for_technical(state)
            if _codex_pr_gate_check:
                _record_failure_packet(
                    state,
                    "technical",
                    {},
                    command=[sys.executable, "pipeline.py", "review", "codex-record",
                             "--stage", "pr", "--result", "ACCEPT",
                             "--review-model", "GPT-5.5", "..."],
                    note=_codex_pr_gate_check,
                    status="BLOCKED",
                    phase="dev",
                    failure_code="codex_pr_gate_missing",
                    failure_category="missing_evidence",
                    summary_ko="Codex PR stage ACCEPT가 없어 technical gate에 진입할 수 없습니다.",
                    expected="codex_review_result.json에 pr stage ACCEPT 기록",
                    actual=_codex_pr_gate_check,
                    exit_code=1,
                    owner="Dev",
                    return_phase="dev",
                    required_actions=["python pipeline.py review codex-record --stage pr --result ACCEPT --review-model GPT-5.5 ... 실행"],
                    retry_allowed=True,
                )
                _save(state)
                _die(_codex_pr_gate_check)
        # IMP-20260522-29C1 fix-forward: technical gate 시작 시점을 명령 진입 직후에 기록한다.
        # _set_external_gate() 호출 시점이 아니라 여기서 기록해야 실제 소요 시간이 측정된다.
        _tg = state.setdefault("external_gates", {}).setdefault("technical", {})
        if not _tg.get("started_at"):
            _tg["started_at"] = _now()
            _save(state)
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
        if result["status"] != "PASS":
            failed_names = [
                str(item.get("name"))
                for item in result.get("checks", [])
                if isinstance(item, dict) and item.get("status") in {"FAIL", "ERROR"}
            ]
            # 카테고리 분류: pytest/py_compile=test_failed, mypy=typecheck_failed, bandit=security_failed
            if any(n == "mypy" for n in failed_names):
                _tech_category = "typecheck_failed"
            elif any(n == "bandit" for n in failed_names):
                _tech_category = "security_failed"
            elif any(n in {"pytest", "py_compile", "ruff"} for n in failed_names):
                _tech_category = "test_failed"
            else:
                _tech_category = "test_failed"
            packet = _record_failure_packet(
                state,
                "technical",
                result,
                command=[sys.executable, "pipeline.py", "gates", "technical"],
                note="Technical gate failed; use failed_checks and minimal_rerun for targeted repair.",
                status="FAIL",
                phase="dev",
                failure_code=f"technical_{_tech_category}",
                failure_category=_tech_category,
                summary_ko="기술 게이트 실패 — 실패한 도구 검사를 수정해야 합니다.",
                expected="ruff/mypy/bandit/py_compile/pytest 모두 PASS",
                actual=f"실패한 검사: {', '.join(failed_names) if failed_names else 'unknown'}",
                exit_code=1,
                owner="Dev",
                return_phase="dev",
                required_actions=[
                    "failed_checks 항목에서 실패한 도구의 로그를 확인하세요.",
                    "해당 도구를 로컬에서 재실행하여 0개의 오류가 나올 때까지 코드를 수정하세요.",
                    "수정 후 `python pipeline.py gates technical` 을 다시 실행하세요.",
                ],
            )
            print(YELLOW(f"  failure packet: {packet['gate']} attempt {packet['attempt']}"))
        _log_event(state, f"technical gate {result['status']}")
        _save(state)
        color = GREEN if result["status"] == "PASS" else RED
        print(color(f"\n[TECHNICAL GATE {result['status']}]"))
        print(f"  report: {paths['technical_result']}\n")
        sys.exit(0 if result["status"] == "PASS" else 1)

    if action == "oracle":
        technical_gate = state.get("external_gates", {}).get("technical", {})
        if not isinstance(technical_gate, dict) or technical_gate.get("status") != "PASS":
            _die("oracle gate requires technical gate PASS first. Run `python pipeline.py gates technical`.")
        # IMP-20260522-29C1 fix-forward: oracle gate 시작 시점을 명령 진입 직후에 기록한다.
        _og = state.setdefault("external_gates", {}).setdefault("oracle", {})
        if not _og.get("started_at"):
            _og["started_at"] = _now()
            _save(state)
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
            report = {
                "schema_version": 1,
                "generated_at": _now(),
                "pipeline_id": pid,
                "status": "FAIL",
                "blockers": oracle_blockers,
            }
            _write_json(paths["oracle_result"], report)
            _set_external_gate(
                state,
                "oracle",
                "FAIL",
                evidence="oracle_manifest_blocked",
                report_file=str(paths["oracle_result"]),
            )
            packet = _record_failure_packet(
                state,
                "oracle",
                report,
                command=[sys.executable, "pipeline.py", "contract", "audit"],
                note="Oracle manifest is missing or malformed; repair user-owned oracle files before rerunning oracle gate.",
                status="FAIL",
                phase="qa",
                failure_code="oracle_manifest_blocked",
                failure_category="missing_evidence",
                summary_ko="오라클 매니페스트가 누락되거나 잘못되었습니다.",
                expected="tests/oracles/{pipeline_id}/ 아래 사용자 소유 오라클 파일이 audit PASS",
                actual="; ".join(oracle_blockers),
                exit_code=1,
                owner="PM",
                return_phase="pm",
                required_actions=[
                    "PM이 tests/oracles/{pipeline_id}/ 경로에 입력/기대 출력 파일을 추가하세요.",
                    "`python pipeline.py contract add-oracle ...` 로 매니페스트에 등록하세요.",
                    "`python pipeline.py contract audit` PASS 후 oracle gate를 다시 실행하세요.",
                ],
            )
            _log_event(state, "oracle gate FAIL (manifest blockers)")
            _save(state)
            print(YELLOW(f"  failure packet: {packet['gate']} attempt {packet['attempt']}"))
            _die("; ".join(oracle_blockers))
        # IMP-20260524-48C4 MT-1: oracle quality 감사 통합 (gates oracle)
        allow_agent_gen = getattr(args, "allow_agent_generated", False)
        # IMP-20260602-1ABE MT-6: state 전달로 ac_ids 검증 활성화
        quality_result = _audit_oracle_quality(oracle_entries, allow_agent_generated=allow_agent_gen, state=state)
        state["oracle_quality"] = quality_result
        _save(state)
        if quality_result.get("status") == "BLOCKED":
            failures_str = "; ".join(quality_result.get("failures", []))
            packet = _record_failure_packet(
                state,
                "oracle",
                quality_result,
                command=[sys.executable, "pipeline.py", "gates", "oracle"],
                note="Oracle quality gate BLOCKED: agent_generated expected 감지. user_provided로 교체하거나 --allow-agent-generated 사용.",
                status="BLOCKED",
                phase="pm",
                failure_code="oracle_quality_blocked",
                failure_category="oracle_quality",
                summary_ko="오라클 품질 게이트 BLOCKED — agent_generated expected 감지.",
                expected="source=user_provided/production_sample/regression_capture",
                actual=failures_str,
                exit_code=1,
                owner="PM",
                return_phase="pm",
                required_actions=[
                    "oracle_manifest.json의 agent_generated expected를 user_provided로 교체하세요.",
                    "또는 `python pipeline.py gates oracle --allow-agent-generated` 를 사용하세요.",
                ],
            )
            _log_event(state, "oracle quality gate BLOCKED")
            _save(state)
            print(YELLOW(f"  failure packet: {packet['gate']} attempt {packet['attempt']}"))
            _die(f"[ORACLE QUALITY BLOCKED] {failures_str}")
        elif quality_result.get("status") == "FAIL":
            failures_str = "; ".join(quality_result.get("failures", []))
            packet = _record_failure_packet(
                state,
                "oracle",
                quality_result,
                command=[sys.executable, "pipeline.py", "gates", "oracle"],
                note="Oracle quality gate FAIL: normal+edge 최소 케이스 또는 placeholder expected 문제.",
                status="FAIL",
                phase="pm",
                failure_code="oracle_quality_fail",
                failure_category="oracle_quality",
                summary_ko="오라클 품질 게이트 FAIL — 최소 케이스 또는 placeholder 위반.",
                expected="normal >= 1, edge/error >= 1, non-placeholder expected",
                actual=failures_str,
                exit_code=1,
                owner="PM",
                return_phase="pm",
                required_actions=[
                    "normal case와 edge/error case가 각각 1개 이상 있는지 확인하세요.",
                    "expected 파일에 TODO/PLACEHOLDER/TBD 같은 임시 값이 없는지 확인하세요.",
                ],
            )
            _log_event(state, "oracle quality gate FAIL")
            _save(state)
            print(YELLOW(f"  failure packet: {packet['gate']} attempt {packet['attempt']}"))
            _die(f"[ORACLE QUALITY FAIL] {failures_str}")

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
            packet = _record_failure_packet(
                state,
                "oracle",
                report,
                command=[sys.executable, "pipeline.py", "gates", "oracle"],
                note="Oracle gate failed; inspect failing results and output path resolution before broad rework.",
                status="FAIL",
                phase="qa",
                failure_code="oracle_acceptance_fail",
                failure_category="oracle_failed",
                summary_ko="오라클 게이트 실패 — 실제 출력이 기대 출력과 다릅니다.",
                expected="모든 oracle 케이스 PASS",
                actual=f"verdict={verdict} (자세한 내용은 oracle_result.json 참조)",
                evidence_paths=[str(paths["oracle_result"])],
                exit_code=1,
                owner="Dev",
                return_phase="dev",
                required_actions=[
                    "oracle_result.json의 failing 케이스를 분석하세요.",
                    "기대 출력과 실제 출력 차이를 좁히도록 Dev 코드를 수정하세요.",
                    "수정 후 `python pipeline.py gates oracle` 을 다시 실행하세요.",
                ],
            )
            print(YELLOW(f"  failure packet: {packet['gate']} attempt {packet['attempt']}"))
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
        # IMP-20260522-29C1 fix-forward: acceptance gate 시작 시점을 명령 진입 직후에 기록한다.
        _ag = state.setdefault("external_gates", {}).setdefault("acceptance", {})
        if not _ag.get("started_at"):
            _ag["started_at"] = _now()
            _save(state)
        # IMP-20260531-BBDB MT-3: --acceptance-code 기반 nonce 검증으로 교체.
        # --user-confirmed 단독은 backward-compatible no-op (경고 후 BLOCKED).
        if getattr(args, "user_confirmed", False) and not getattr(args, "acceptance_code", None):
            print(YELLOW("[경고] --user-confirmed는 더 이상 ACCEPT를 통과시키지 않습니다."))
            print(YELLOW("  gates request-accept --evidence <경로> 를 먼저 실행하여 승인 코드를 발급받으세요."))
            _die("[BLOCKED] --acceptance-code 가 필요합니다. (acceptance_code_required)")
        if not getattr(args, "acceptance_code", None):
            _die(
                "[BLOCKED] 승인 코드가 없습니다. (acceptance_code_required)\n"
                "  python pipeline.py gates request-accept --evidence <결과물-경로>\n"
                "를 먼저 실행하여 ACCEPT-...-XXXXXXXX 코드를 발급받으세요."
            )
        accept_decision: str = str(args.result).upper()
        if accept_decision not in {"ACCEPT", "REJECT"}:
            _die("[USER ACCEPTANCE BLOCKED] --result는 ACCEPT 또는 REJECT만 허용됩니다.")

        # IMP-20260531-BBDB MT-3: acceptance_request.json 로드 + nonce/SHA/run_id 검증
        _req = _load_acceptance_request()
        if _req is None:
            _record_failure_packet(
                state, "acceptance", {},
                command=[sys.executable, "pipeline.py", "gates", "request-accept",
                         "--evidence", "<result-path>"],
                note="acceptance_request.json missing — gates request-accept 미실행",
                status="BLOCKED", phase="harness",
                failure_code="missing_acceptance_request",
                failure_category="missing_evidence",
                summary_ko="acceptance_request.json이 없습니다. gates request-accept를 먼저 실행하세요.",
                expected="acceptance_request.json 존재 (status=PENDING)",
                actual="파일 없음",
                exit_code=1, owner="Pipeline Manager", return_phase="build",
                required_actions=["python pipeline.py gates request-accept --evidence <결과물-경로> 를 먼저 실행"],
                retry_allowed=True,
            )
            _save(state)
            _die(
                "[BLOCKED] acceptance_request.json 이 없습니다. (missing_acceptance_request)\n"
                "  python pipeline.py gates request-accept --evidence <경로>\n"
                "를 먼저 실행하세요."
            )

        _req_status = str(_req.get("status", ""))
        if _req_status != "PENDING":
            _record_failure_packet(
                state, "acceptance", {},
                command=[sys.executable, "pipeline.py", "gates", "request-accept",
                         "--evidence", "<result-path>"],
                note=f"acceptance_request.json status={_req_status} (이미 사용됨)",
                status="BLOCKED", phase="harness",
                failure_code="consumed_or_expired",
                failure_category="missing_evidence",
                summary_ko=f"이미 사용된 승인 요청입니다 (status={_req_status}).",
                expected="status=PENDING",
                actual=f"status={_req_status}",
                exit_code=1, owner="Pipeline Manager", return_phase="build",
                required_actions=["python pipeline.py gates request-accept 를 다시 실행하여 새 코드 발급"],
                retry_allowed=True,
            )
            _save(state)
            _die(
                f"[BLOCKED] 이미 사용된 승인 요청입니다 (status={_req_status}). "
                "(consumed_or_expired)\n"
                "  python pipeline.py gates request-accept 를 다시 실행하여 새 코드를 발급받으세요."
            )

        _req_pipeline_id = str(_req.get("pipeline_id", ""))
        if _req_pipeline_id != pid:
            _record_failure_packet(
                state, "acceptance", {},
                command=[sys.executable, "pipeline.py", "gates", "request-accept",
                         "--evidence", "<result-path>"],
                note=f"acceptance_request.json pipeline_id={_req_pipeline_id} != active {pid}",
                status="BLOCKED", phase="harness",
                failure_code="pipeline_id_mismatch",
                failure_category="missing_evidence",
                summary_ko="승인 요청의 pipeline_id가 현재 파이프라인과 다릅니다.",
                expected=f"pipeline_id={pid}", actual=f"pipeline_id={_req_pipeline_id}",
                exit_code=1, owner="Pipeline Manager", return_phase="build",
                required_actions=["python pipeline.py gates request-accept 를 현재 파이프라인에서 다시 실행"],
                retry_allowed=True,
            )
            _save(state)
            _die(
                f"[BLOCKED] 승인 요청의 pipeline_id ({_req_pipeline_id}) 가 "
                f"현재 파이프라인 ({pid}) 과 다릅니다. (pipeline_id_mismatch)\n"
                "  gates request-accept 를 다시 실행하세요."
            )

        # 코드 형식 및 nonce 검증
        _code_str = str(getattr(args, "acceptance_code", "") or "")
        _expected_prefix = f"{accept_decision}-{pid}-"
        if not _code_str.startswith(_expected_prefix):
            _record_failure_packet(
                state, "acceptance", {},
                command=[sys.executable, "pipeline.py", "gates", "accept",
                         "--result", accept_decision, "--evidence", "<path>",
                         "--acceptance-code", _expected_prefix + "XXXXXXXX"],
                note=f"acceptance code format mismatch: {_code_str!r}",
                status="BLOCKED", phase="harness",
                failure_code="acceptance_code_mismatch",
                failure_category="missing_evidence",
                summary_ko=f"승인 코드 형식이 올바르지 않습니다. 예: {_expected_prefix}XXXXXXXX",
                expected=f"{_expected_prefix}<8자 nonce>",
                actual=_code_str,
                exit_code=1, owner="Pipeline Manager", return_phase="build",
                required_actions=[f"{_expected_prefix}<8자> 형태의 코드를 입력하세요."],
                retry_allowed=True,
            )
            _save(state)
            _die(f"[BLOCKED] 승인 코드 형식 오류 (acceptance_code_mismatch). 예: {_expected_prefix}XXXXXXXX")

        _submitted_nonce = _code_str[len(_expected_prefix):]
        _stored_nonce = str(_req.get("nonce", ""))
        if _submitted_nonce != _stored_nonce:
            _record_failure_packet(
                state, "acceptance", {},
                command=[sys.executable, "pipeline.py", "gates", "request-accept",
                         "--evidence", "<result-path>"],
                note=f"nonce mismatch: submitted={_submitted_nonce!r} stored={_stored_nonce!r}",
                status="BLOCKED", phase="harness",
                failure_code="acceptance_code_mismatch",
                failure_category="missing_evidence",
                summary_ko="승인 코드의 nonce가 일치하지 않습니다.",
                expected=f"nonce={_stored_nonce}", actual=f"nonce={_submitted_nonce}",
                exit_code=1, owner="Pipeline Manager", return_phase="build",
                required_actions=["올바른 nonce 코드를 입력하거나 gates request-accept 를 다시 실행하세요."],
                retry_allowed=True,
            )
            _save(state)
            _die("[BLOCKED] 승인 코드 nonce 불일치 (acceptance_code_mismatch)")

        # PR head SHA 변경 확인 (gh CLI 실패 시 BLOCKED — 검증 불가 = 안전 실패)
        _stored_sha = str(_req.get("pr_head_sha", "") or "")
        if _stored_sha:
            _current_sha = _get_current_pr_head_sha()
            if not _current_sha:
                _record_failure_packet(
                    state, "acceptance", {},
                    command=[sys.executable, "pipeline.py", "gates", "request-accept",
                             "--evidence", "<result-path>"],
                    note="gh CLI 실패 또는 PR 정보 조회 불가 — PR head SHA 검증 불가",
                    status="BLOCKED", phase="harness",
                    failure_code="sha_verification_failed",
                    failure_category="missing_evidence",
                    summary_ko="PR head SHA 검증 불가 (gh CLI 실패) — gates request-accept 재실행 필요",
                    expected=f"head_sha={_stored_sha[:7]}", actual="unknown (gh CLI failed)",
                    exit_code=1, owner="Pipeline Manager", return_phase="build",
                    required_actions=["gh CLI 설치/인증 확인 후 python pipeline.py gates request-accept 재실행"],
                    retry_allowed=True,
                )
                _save(state)
                _die("[BLOCKED] PR head SHA 검증 불가 (sha_verification_failed) — gh CLI 실패")
            elif not (
                _current_sha.startswith(_stored_sha[:7]) or _stored_sha.startswith(_current_sha[:7])
            ):
                _record_failure_packet(
                    state, "acceptance", {},
                    command=[sys.executable, "pipeline.py", "gates", "request-accept",
                             "--evidence", "<result-path>"],
                    note=f"PR head SHA changed: stored={_stored_sha[:7]} current={_current_sha[:7]}",
                    status="BLOCKED", phase="harness",
                    failure_code="stale_head_sha",
                    failure_category="missing_evidence",
                    summary_ko="PR 에 새 커밋이 추가되었습니다. 새 코드를 발급받아야 합니다.",
                    expected=f"head_sha={_stored_sha[:7]}", actual=f"head_sha={_current_sha[:7]}",
                    exit_code=1, owner="Pipeline Manager", return_phase="build",
                    required_actions=["python pipeline.py gates request-accept 를 다시 실행하여 최신 코드 발급"],
                    retry_allowed=True,
                )
                _save(state)
                _die("[BLOCKED] PR head SHA 변경됨 (stale_head_sha) — gates request-accept 재실행 필요")

        # CI run ID 변경 확인 (gh CLI 실패 시 BLOCKED — 검증 불가 = 안전 실패)
        _stored_run = str(_req.get("github_ci_run_id", "") or "")
        if _stored_run:
            _current_run_raw = _get_pr_branch_ci_run_id()
            _current_run = str(_current_run_raw).strip() if _current_run_raw is not None else ""
            if not _current_run:
                _record_failure_packet(
                    state, "acceptance", {},
                    command=[sys.executable, "pipeline.py", "gates", "request-accept",
                             "--evidence", "<result-path>"],
                    note="gh CLI 실패 또는 CI run 정보 조회 불가 — run ID 검증 불가",
                    status="BLOCKED", phase="harness",
                    failure_code="run_id_verification_failed",
                    failure_category="missing_evidence",
                    summary_ko="CI run ID 검증 불가 (gh CLI 실패) — gates request-accept 재실행 필요",
                    expected=f"run_id={_stored_run}", actual="unknown (gh CLI failed)",
                    exit_code=1, owner="Pipeline Manager", return_phase="build",
                    required_actions=["gh CLI 설치/인증 확인 후 python pipeline.py gates request-accept 재실행"],
                    retry_allowed=True,
                )
                _save(state)
                _die("[BLOCKED] CI run ID 검증 불가 (run_id_verification_failed) — gh CLI 실패")
            elif _current_run != str(_stored_run):
                _record_failure_packet(
                    state, "acceptance", {},
                    command=[sys.executable, "pipeline.py", "gates", "request-accept",
                             "--evidence", "<result-path>"],
                    note=f"CI run ID changed: stored={_stored_run} current={_current_run}",
                    status="BLOCKED", phase="harness",
                    failure_code="stale_run_id",
                    failure_category="missing_evidence",
                    summary_ko="GitHub Actions run 이 변경되었습니다. 새 코드를 발급받아야 합니다.",
                    expected=f"run_id={_stored_run}", actual=f"run_id={_current_run}",
                    exit_code=1, owner="Pipeline Manager", return_phase="build",
                    required_actions=["python pipeline.py gates request-accept 를 다시 실행하여 최신 코드 발급"],
                    retry_allowed=True,
                )
                _save(state)
                _die("[BLOCKED] CI run ID 변경됨 (stale_run_id) — gates request-accept 재실행 필요")

        # Issue 2: evidence 경로 일치 확인 (request-accept 시 기록한 경로와 동일해야 함)
        _stored_evidence_path = str(_req.get("evidence", "") or "")
        _provided_evidence_path = str(args.evidence or "")
        if _provided_evidence_path and _stored_evidence_path:
            _is_url_ev = (
                _stored_evidence_path.startswith(("http://", "https://"))
                or _provided_evidence_path.startswith(("http://", "https://"))
            )
            if _is_url_ev:
                if _stored_evidence_path != _provided_evidence_path:
                    _record_failure_packet(
                        state, "acceptance", {},
                        command=[sys.executable, "pipeline.py", "gates", "request-accept",
                                 "--evidence", _stored_evidence_path],
                        note=(
                            f"evidence URL mismatch: stored={_stored_evidence_path}"
                            f" provided={_provided_evidence_path}"
                        ),
                        status="BLOCKED", phase="harness",
                        failure_code="evidence_path_mismatch",
                        failure_category="missing_evidence",
                        summary_ko="evidence URL이 request-accept 시 기록과 다릅니다.",
                        expected=_stored_evidence_path, actual=_provided_evidence_path,
                        exit_code=1, owner="Pipeline Manager", return_phase="build",
                        required_actions=[
                            "request-accept 시 기록한 동일한 evidence URL을 사용하거나"
                            " request-accept 재실행"
                        ],
                        retry_allowed=True,
                    )
                    _save(state)
                    _die("[BLOCKED] evidence URL 불일치 (evidence_path_mismatch)")
            else:
                try:
                    _norm_stored_ev = str(Path(_stored_evidence_path).resolve())
                    _norm_provided_ev = str(Path(_provided_evidence_path).resolve())
                except Exception:  # noqa: BLE001
                    _norm_stored_ev = _stored_evidence_path
                    _norm_provided_ev = _provided_evidence_path
                if _norm_stored_ev != _norm_provided_ev:
                    _record_failure_packet(
                        state, "acceptance", {},
                        command=[sys.executable, "pipeline.py", "gates", "request-accept",
                                 "--evidence", _stored_evidence_path],
                        note=(
                            f"evidence path mismatch: stored={_stored_evidence_path}"
                            f" provided={_provided_evidence_path}"
                        ),
                        status="BLOCKED", phase="harness",
                        failure_code="evidence_path_mismatch",
                        failure_category="missing_evidence",
                        summary_ko="evidence 경로가 request-accept 시 기록과 다릅니다.",
                        expected=_stored_evidence_path, actual=_provided_evidence_path,
                        exit_code=1, owner="Pipeline Manager", return_phase="build",
                        required_actions=[
                            "request-accept 시 기록한 동일한 evidence 경로를 사용하거나"
                            " request-accept 재실행"
                        ],
                        retry_allowed=True,
                    )
                    _save(state)
                    _die("[BLOCKED] evidence 경로 불일치 (evidence_path_mismatch)")

        # evidence 파일 hash 확인 (URL이면 hash skip; 파일 없음/읽기실패 → BLOCKED)
        _stored_sha256 = _req.get("evidence_sha256")
        _evidence_arg = args.evidence or _req.get("evidence", "")
        if (
            _stored_sha256
            and isinstance(_evidence_arg, str)
            and not _evidence_arg.startswith(("http://", "https://"))
        ):
            _current_sha256 = _compute_file_sha256(_evidence_arg)
            if _current_sha256 is None:
                _record_failure_packet(
                    state, "acceptance", {},
                    command=[sys.executable, "pipeline.py", "gates", "request-accept",
                             "--evidence", _evidence_arg],
                    note=f"evidence file missing or unreadable: {_evidence_arg}",
                    status="BLOCKED", phase="harness",
                    failure_code="evidence_missing",
                    failure_category="missing_evidence",
                    summary_ko="결과물 파일이 없거나 읽을 수 없습니다.",
                    expected=f"sha256={_stored_sha256[:12]}...",
                    actual="file_missing_or_unreadable",
                    exit_code=1, owner="Pipeline Manager", return_phase="build",
                    required_actions=[
                        "결과물 파일이 존재하는지 확인 후"
                        " python pipeline.py gates request-accept 재실행"
                    ],
                    retry_allowed=True,
                )
                _save(state)
                _die("[BLOCKED] evidence 파일 없음/읽기 실패 (evidence_missing)")
            elif _current_sha256 != _stored_sha256:
                _record_failure_packet(
                    state, "acceptance", {},
                    command=[sys.executable, "pipeline.py", "gates", "request-accept",
                             "--evidence", _evidence_arg],
                    note=(
                        f"evidence file hash changed:"
                        f" stored={_stored_sha256[:12]} current={_current_sha256[:12]}"
                    ),
                    status="BLOCKED", phase="harness",
                    failure_code="evidence_changed",
                    failure_category="missing_evidence",
                    summary_ko="결과물 파일이 변경되었습니다. 새 코드를 발급받아야 합니다.",
                    expected=f"sha256={_stored_sha256[:12]}...",
                    actual=f"sha256={_current_sha256[:12]}...",
                    exit_code=1, owner="Pipeline Manager", return_phase="build",
                    required_actions=["python pipeline.py gates request-accept 를 다시 실행"],
                    retry_allowed=True,
                )
                _save(state)
                _die("[BLOCKED] evidence 파일 변경됨 (evidence_changed) — gates request-accept 재실행 필요")
        # IMP-20260603-9934 MT-2: Final Packet Freeze Guard — packet_sha256 검증
        _freeze_msg = _check_packet_freeze_status(_req)
        if _freeze_msg:
            _record_failure_packet(
                state, "acceptance", {},
                command=[sys.executable, "pipeline.py", "gates", "request-accept",
                         "--evidence", str(args.evidence or "")],
                note=_freeze_msg,
                status="BLOCKED", phase="harness",
                failure_code="stale_packet",
                failure_category="missing_evidence",
                summary_ko="human_acceptance_packet.md가 승인 요청 이후 변경되었습니다. (STALE_PACKET)",
                expected="packet_sha256 일치",
                actual="packet_sha256 불일치",
                exit_code=1, owner="Pipeline Manager", return_phase="build",
                required_actions=["python pipeline.py gates request-accept 를 재실행하여 새 코드 발급"],
                retry_allowed=True,
            )
            _save(state)
            _die(_freeze_msg)

        # D4: pr gate → acceptance gate 연결 (bootstrap_exception 제외)
        bootstrap_exception_accept = state.get("codex_bootstrap_exception", False)
        if not bootstrap_exception_accept and accept_decision == "ACCEPT":
            _codex_pr_gate_check_accept = _check_codex_pr_gate_for_technical(state)
            if _codex_pr_gate_check_accept:
                _record_failure_packet(
                    state,
                    "acceptance",
                    {},
                    command=[sys.executable, "pipeline.py", "review", "codex-record",
                             "--stage", "pr", "--result", "ACCEPT",
                             "--review-model", "GPT-5.5", "..."],
                    note=_codex_pr_gate_check_accept,
                    status="BLOCKED",
                    phase="dev",
                    failure_code="codex_pr_gate_missing_for_accept",
                    failure_category="missing_evidence",
                    summary_ko="ACCEPT 전에 Codex PR stage ACCEPT가 필요합니다.",
                    expected="codex_review_result.json에 pr stage ACCEPT 기록",
                    actual=_codex_pr_gate_check_accept,
                    exit_code=1,
                    owner="Dev",
                    return_phase="dev",
                    required_actions=["python pipeline.py review codex-record --stage pr --result ACCEPT --review-model GPT-5.5 ... 실행"],
                    retry_allowed=True,
                )
                _save(state)
                _die(f"[CODEX PR GATE REQUIRED] ACCEPT 전에 codex pr stage ACCEPT가 필요합니다: {_codex_pr_gate_check_accept}")
        deployment: Optional[Dict[str, Any]] = None
        evidence_validation: Optional[Dict[str, Any]] = None
        if accept_decision == "ACCEPT":
            prereq = []
            for gate_name in ("technical", "oracle", "github_ci"):
                gate = state["external_gates"].get(gate_name, {})
                if not isinstance(gate, dict) or gate.get("status") != "PASS":
                    prereq.append(f"{gate_name} gate must be PASS before user ACCEPT")
            if prereq:
                _die("; ".join(prereq))
            # IMP-20260519-E979: Final Acceptance Readiness Gate hard block
            _readiness = _check_acceptance_readiness(state)
            if not _readiness.get("allow_accept", True):
                _readiness_return_phase = str(_readiness.get("return_phase") or "build")
                _record_failure_packet(
                    state,
                    "acceptance",
                    {},
                    command=[sys.executable, "pipeline.py", "gates", "request-accept",
                             "--evidence", "<result-path>"],
                    note=_readiness.get("blocked_reason") or "acceptance readiness check failed",
                    status="BLOCKED",
                    phase=_readiness_return_phase,
                    failure_code=str(_readiness.get("failure_code") or "readiness_blocked"),
                    failure_category="missing_evidence",
                    summary_ko=_readiness.get("blocked_reason") or "acceptance readiness gate BLOCKED",
                    expected="PR 본문 완성 + acceptance packet 준비 완료 상태",
                    actual=str(_readiness.get("failure_code") or "readiness_blocked"),
                    exit_code=1,
                    owner="Dev",
                    return_phase=_readiness_return_phase,
                    required_actions=[
                        _readiness.get("blocked_reason") or "PR 본문 및 acceptance packet을 보완하세요.",
                        "PR이 Draft가 아닌 정식 PR 상태이고 gh CLI가 설치/인증되어 있는지 확인하세요.",
                        "PR 본문에 필수 섹션(작업 요약 또는 최종 판단 요약/사용자가 확인할 결과물/기대 결과와 실제 결과/중요한 선택과 트레이드오프/검증)이 있는지 확인하세요.",
                        "GitHub PR에 acceptance packet 댓글이 게시되어 있고 '판단 가능' 상태인지 확인하세요.",
                        "보완 완료 후: (1) python pipeline.py gates request-accept --evidence <result-path> 로 새 코드 발급"
                        " → (2) 사용자가 코드 입력 → (3) python pipeline.py gates accept --result ACCEPT"
                        " --evidence <result-path> --acceptance-code ACCEPT-<pid>-<nonce>",
                    ],
                    retry_allowed=True,
                )
                _save(state)
                missing = _readiness.get("missing_sections", [])
                msg_parts = [
                    f"[ACCEPTANCE READINESS GATE BLOCKED] {_readiness.get('blocked_reason')}",
                ]
                if missing:
                    msg_parts.append("  누락 섹션: " + ", ".join(missing))
                msg_parts.append("  PR 본문과 acceptance packet을 보완한 뒤 다시 실행하세요.")
                _die("\n".join(msg_parts))
            # IMP-20260520-D0BB: Protocol Consistency Guard hard gate.
            # PR body / acceptance packet / 실제 CI run ID / head SHA /
            # changed files 사이의 불일치를 ACCEPT 전에 차단한다.
            _consistency_target = _get_consistency_pr_target(state)
            _consistency_repo = _consistency_target.get("repo", "")
            _consistency_pr_num = _consistency_target.get("pr", "")
            if _consistency_repo and _consistency_pr_num:
                _consistency_result = _run_protocol_consistency_inline(
                    state, _consistency_repo, _consistency_pr_num, pid
                )
            else:
                # PR 자동 감지 실패 → inline 검사를 SKIP.
                # _check_acceptance_readiness와 _validate_pr_title_matches_pipeline이
                # PR 존재/소유권을 이미 검증하므로 여기서는 SKIP으로 통과.
                # (명시적 gates consistency --repo ... --pr ... CLI는 BLOCKED 유지)
                _consistency_result = {
                    "status": "PASS",
                    "failure_code": "",
                    "failure_category": "",
                    "blocked_reason": None,
                    "return_phase": "build",
                    "allow_accept": True,
                    "details": {"skipped": "pr_auto_detection_failed"},
                }
            if not _consistency_result.get("allow_accept", True):
                _consistency_return_phase = str(
                    _consistency_result.get("return_phase") or "build"
                )
                _record_failure_packet(
                    state,
                    "acceptance",
                    {},
                    command=[sys.executable, "pipeline.py", "gates", "request-accept",
                             "--evidence", "<result-path>"],
                    note=(
                        _consistency_result.get("blocked_reason")
                        or "protocol consistency check failed"
                    ),
                    status="BLOCKED",
                    phase=_consistency_return_phase,
                    failure_code=str(
                        _consistency_result.get("failure_code")
                        or "consistency_blocked"
                    ),
                    failure_category="protocol_violation",
                    summary_ko=(
                        _consistency_result.get("blocked_reason")
                        or "protocol consistency gate BLOCKED"
                    ),
                    expected=(
                        "PR body, acceptance packet, CI run ID, head SHA, "
                        "changed files 모두 일치"
                    ),
                    actual=str(
                        _consistency_result.get("failure_code")
                        or "consistency_blocked"
                    ),
                    exit_code=1,
                    owner="Pipeline Manager",
                    return_phase=_consistency_return_phase,
                    required_actions=_get_consistency_required_actions(
                        _consistency_result
                    ),
                    retry_allowed=True,
                )
                _save(state)
                _die(
                    "[PROTOCOL CONSISTENCY GATE BLOCKED] "
                    + str(_consistency_result.get("blocked_reason")
                          or _consistency_result.get("failure_code")
                          or "consistency check failed")
                    + "\n  PR body와 acceptance packet의 정보를 실제 "
                    "CI 상태와 일치시킨 뒤 다시 실행하세요."
                )
            _validate_pr_title_matches_pipeline(state)
            evidence_validation = _validate_user_acceptance_evidence(args.evidence)
            deployment = _deploy_accepted_outputs(state, args.evidence, args.notes, evidence_validation)
        # Issue 5: 모든 blocker 검증 통과 후 CONSUMED 처리 (D4/prereq/readiness/consistency 이후)
        # 위 _req is None 분기에서 _die 종료 보장 — None 가능성 없음
        assert _req is not None  # nosec B101
        _consume_acceptance_request(_req, accept_decision)
        _log_event(state, f"acceptance code consumed: request_id={_req.get('request_id')} result={accept_decision}")
        gate_status = "PASS" if accept_decision == "ACCEPT" else "FAIL"
        report = {
            "schema_version": 1,
            "generated_at": _now(),
            "pipeline_id": pid,
            "status": gate_status,
            "result": accept_decision,
            "evidence": args.evidence,
            "validated_evidence": evidence_validation or {},
            "notes": args.notes or "",
            "deployment": deployment,
        }
        _write_json(paths["user_validation"], report)
        _set_external_gate(
            state,
            "acceptance",
            gate_status,
            evidence=args.evidence or "acceptance_code_confirmed",
            report_file=str(paths["user_validation"]),
            note=args.notes,
        )
        # IMP-20260522-29C1 MT-1: acceptance 요청/기록 시점 저장.
        # acceptance_requested_at은 accept 명령이 readiness/consistency gate를
        # 통과하고 ACCEPT/REJECT를 실제로 기록하는 시점에 1회 기록한다.
        if not state.get("acceptance_requested_at"):
            state["acceptance_requested_at"] = _now()
        state["phases"]["harness"]["status"] = gate_status
        state["phases"]["harness"]["completed_at"] = _now()
        state["phases"]["harness"]["evidence"] = "three_gate_user_acceptance"
        state["phases"]["harness"]["report_file"] = str(paths["user_validation"])
        if gate_status == "PASS":
            # ACCEPT가 성공적으로 기록된 직후 시점.
            state["acceptance_recorded_at"] = _now()
        if deployment:
            state["deployment"] = deployment
        if gate_status != "PASS":
            _record_failure_packet(
                state,
                "acceptance",
                report,
                command=[sys.executable, "pipeline.py", "gates", "request-accept", "--evidence", "<repaired-result>"],
                note="User rejected the visible result; PM/Dev should repair the requested behavior or clarify requirements.",
                status="FAIL",
                phase="harness",
                failure_code="user_acceptance_rejected",
                failure_category="user_acceptance_rejected",
                summary_ko="사용자가 결과물을 REJECT 했습니다.",
                expected="사용자가 PR 결과/첨부물을 보고 ACCEPT 선택",
                actual=f"result=REJECT notes={args.notes or '(no notes)'}",
                exit_code=1,
                owner="Dev",
                return_phase="dev",
                required_actions=[
                    "사용자 거절 사유를 확인하고 PM이 요구사항을 명확히 하거나 Dev가 결과물을 수정하세요.",
                    "수정 결과를 PR에 push하고 GitHub Actions가 PASS 인지 확인하세요.",
                    "사용자에게 다시 PR 링크와 결과물을 제시하여 ACCEPT/REJECT를 받으세요.",
                ],
            )
        state["current_phase"] = "architect"
        _log_event(state, f"user acceptance gate {gate_status}")
        _record_snapshot(state, "harness", None)
        _save(state)
        # IMP-20260522-29C1 MT-4 (fix-forward): gates accept 완료 시 메트릭 요약을
        # 콘솔 출력뿐 아니라 human_acceptance_packet.md 파일에도 기록한다.
        _metrics_str = "확인 불가"
        try:
            _metrics_str = _format_metrics_summary_ko(_collect_pipeline_metrics(state))
            print("\n" + "-" * 60)
            print("[ 최종 확인 안내 — 소요 시간 요약 ]")
            print(_metrics_str)
            print("-" * 60)
        except Exception:
            pass
        # human_acceptance_packet.md 에 metrics summary 섹션 추가/갱신
        try:
            _packet_path = BASE_DIR / f"human_acceptance_packet_{pid}.md"
            if not _packet_path.exists():
                _packet_path = BASE_DIR / "human_acceptance_packet.md"
            if _packet_path.exists():
                _existing_text = _packet_path.read_text(encoding="utf-8")
                _metrics_section = f"\n\n## 소요 시간 요약\n{_metrics_str}\n"
                # 기존 섹션이 있으면 교체, 없으면 append
                if "## 소요 시간 요약" in _existing_text:
                    import re as _re
                    _existing_text = _re.sub(
                        r"\n\n## 소요 시간 요약\n.*?(?=\n\n#|\Z)",
                        _metrics_section.rstrip("\n"),
                        _existing_text,
                        flags=_re.DOTALL,
                    )
                else:
                    _existing_text = _existing_text.rstrip("\n") + _metrics_section
                _packet_path.write_text(_existing_text, encoding="utf-8")
        except Exception:
            pass
        color = GREEN if gate_status == "PASS" else RED
        print(color(f"\n[USER ACCEPTANCE GATE {gate_status}]"))
        print(f"  report: {paths['user_validation']}")
        if deployment:
            print(f"  deployed: {deployment['deploy_dir']}")
        print(f"  next: {YELLOW('python pipeline.py architect --report-file architect_report.xml')}\n")
        sys.exit(0 if gate_status == "PASS" else 1)

    if action == "consistency":
        # IMP-20260520-D0BB: Protocol Consistency Guard 단독 실행
        _run_protocol_consistency_check(state, args, pid)
        return

    if action == "github-ci":
        # IMP-20260522-29C1 fix-forward: github_ci gate 시작 시점을 명령 진입 직후에 기록한다.
        _gcg = state.setdefault("external_gates", {}).setdefault("github_ci", {})
        if not _gcg.get("started_at"):
            _gcg["started_at"] = _now()
            _save(state)
        # IMP-20260524-C097 MT-2: --head-sha 옵션으로 SHA 이중 검증 강화.
        # --commit과 --head-sha 모두 지정된 경우 이중 일치 여부를 사전 경고한다.
        _cli_head_sha: Optional[str] = getattr(args, "head_sha", None) or None
        _cli_commit: Optional[str] = getattr(args, "commit", None) or None
        if _cli_head_sha and _cli_commit:
            _sha_prefix = _cli_head_sha.lower()
            _commit_lower = _cli_commit.lower()
            if not (_commit_lower.startswith(_sha_prefix) or _sha_prefix.startswith(_commit_lower)):
                print(
                    f"[GITHUB CI] 경고: --head-sha ({_cli_head_sha[:12]})와 "
                    f"--commit ({_cli_commit[:12]})이 일치하지 않습니다. "
                    "--commit 값을 우선합니다."
                )
        # --head-sha만 지정된 경우 --commit 대용으로 사용
        effective_commit: Optional[str] = _cli_commit or _cli_head_sha
        verification = _verify_github_ci_run(
            repo=args.repo,
            commit=effective_commit,
            run_id=args.run_id,
            artifact_name=args.artifact,
            token_env=args.token_env,
            workflow=args.workflow,
        )
        _record_github_ci_verification(state, verification, args.artifact)
        if verification["status"] != "PASS":
            _record_failure_packet(
                state,
                "github_ci",
                verification,
                command=[sys.executable, "pipeline.py", "gates", "github-ci", "--repo", args.repo or _github_repo_from_remote()],
                note="GitHub CI gate failed; inspect Actions logs before local code changes.",
                status="FAIL",
                phase="build",
                failure_code="github_ci_failed",
                failure_category="ci_failed",
                summary_ko="GitHub Actions CI가 실패했습니다.",
                expected="모든 GitHub Actions workflow run PASS",
                actual=f"run_id={verification.get('run_id')} status={verification.get('status')}",
                evidence_paths=[str(_contract_paths(pid)["github_ci_result"])],
                exit_code=1,
                owner="Dev",
                return_phase="dev",
                required_actions=[
                    "GitHub Actions 실패 로그를 확인하여 원인을 파악하세요.",
                    "로컬 환경에서 동일 검사를 재현하고 코드를 수정하세요.",
                    "수정 후 PR에 push하고 Actions가 PASS 한 뒤 `python pipeline.py gates github-ci` 를 다시 실행하세요.",
                ],
            )
        _save(state)
        # IMP-20260522-29C1 fix-forward v3: github-ci PASS 후 PR body에 metrics 업데이트.
        # 사용자가 ACCEPT/REJECT를 결정하기 전에 PR 화면에서 소요 시간 요약을 볼 수 있도록 한다.
        if verification["status"] == "PASS":
            _update_pr_body_with_metrics(state)
        color = GREEN if verification["status"] == "PASS" else RED
        print(color(f"\n[GITHUB CI GATE {verification['status']}]"))
        print(f"  run_id: {verification.get('run_id')}")
        print(f"  report: {_contract_paths(pid)['github_ci_result']}\n")
        sys.exit(0 if verification["status"] == "PASS" else 1)

    if action == "wait-github-ci":
        # IMP-20260524-C097 MT-1: SHA 기반 CI run 추적 (blind wait 제거)
        repo_arg: str = args.repo or _github_repo_from_remote()
        head_sha_arg: str = getattr(args, "head_sha", "") or ""
        timeout_arg: int = int(getattr(args, "timeout_sec", 600) or 600)
        poll_arg: int = int(getattr(args, "poll_sec", 15) or 15)
        pr_num_arg: Optional[int] = getattr(args, "pr", None)
        token_arg = _github_token(getattr(args, "token_env", "GITHUB_TOKEN") or "GITHUB_TOKEN")

        if not head_sha_arg:
            # --head-sha 미지정 시 로컬 HEAD 사용
            head_sha_arg = _git_rev_parse("HEAD") or ""
        if not head_sha_arg:
            _die("[CI 대기] --head-sha 인자 또는 로컬 git HEAD SHA를 확인할 수 없습니다.")

        wait_result = _poll_github_ci_run(
            repo=repo_arg,
            expected_head_sha=head_sha_arg,
            timeout_sec=timeout_arg,
            poll_sec=poll_arg,
            token=token_arg,
            pr_num=pr_num_arg,
        )

        # pipeline_state.json에 결과 기록
        # wait_status 키 이름은 oracle TC01/TC02 expected 스키마와 일치시킴 (IMP-20260524-C097 MT-3)
        state.setdefault("github_ci_wait", {}).update({
            "repo": repo_arg,
            "head_sha": head_sha_arg,
            "wait_status": wait_result["wait_status"],
            "matched_head_sha": wait_result["matched_head_sha"],
            "conclusion": wait_result["conclusion"],
            "run_id": wait_result["run_id"],
            "elapsed_sec": wait_result["elapsed_sec"],
            "recorded_at": _now(),
        })
        _log_event(state, f"gates wait-github-ci: status={wait_result['wait_status']} sha={head_sha_arg[:12]}")
        _save(state)

        wait_status = wait_result["wait_status"]
        color = GREEN if wait_status == "PASS" else RED
        print(color(f"\n[CI 대기 결과] {wait_status}"))
        print(f"  SHA: {head_sha_arg[:12]}")
        print(f"  소요 시간: {wait_result['elapsed_sec']:.0f}초")
        if wait_result["run_id"]:
            print(f"  run_id: {wait_result['run_id']}")
        sys.exit(0 if wait_status == "PASS" else 1)

    if action == "batch-ci":
        # batch-ci --probe --changed-files a,b,c
        # 신뢰 루트 파일 포함 여부에 따라 ci_mode 결정.
        probe: bool = getattr(args, "probe", False)
        changed_files_raw: str = getattr(args, "changed_files", "") or ""
        changed_files: List[str] = [f.strip() for f in changed_files_raw.split(",") if f.strip()]

        if not changed_files:
            # 변경 파일이 없으면 batched
            result_payload: Dict[str, Any] = {
                "is_trust_root": False,
                "ci_mode": "batched",
                "changed_files": [],
                "matched_patterns": [],
            }
        else:
            matched: List[str] = []
            for f in changed_files:
                for pat in TRUST_ROOT_PATTERNS:
                    if pat.endswith("/"):
                        # directory prefix match
                        if f.startswith(pat) or ("/" + pat in f):
                            matched.append(pat)
                            break
                    else:
                        # exact filename or basename match
                        if f == pat or Path(f).name == pat:
                            matched.append(pat)
                            break

            is_trust_root = len(matched) > 0
            result_payload = {
                "is_trust_root": is_trust_root,
                "ci_mode": "per_phase" if is_trust_root else "batched",
                "changed_files": changed_files,
                "matched_patterns": matched,
            }

        print(json.dumps(result_payload, ensure_ascii=False, indent=2))
        if not probe:
            # 비probe 모드: 상태 기록 없음, 단순 stdout 출력 후 종료
            pass
        return

    _die(f"unknown gates action: {action}", exit_code=2)


# IMP-20260529-D8BA MT-1: SECRET_PATTERNS는 SSoT(상단 ~line 2030)로 통합되었다.
# 기존 _redact_for_external_review 전용 보조 패턴(키-값 형식)은 별도 상수로 보존하고,
# 외부 리뷰 redaction은 SSoT 패턴 + 보조 패턴을 함께 적용한다.
_REDACTION_KV_PATTERN = re.compile(
    r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*['\"]?([A-Za-z0-9_\-\.]{12,})"
)


def _redact_for_external_review(text: str) -> str:
    """외부(OpenAI 등) 리뷰에 코드/보고서 텍스트를 전달하기 전 민감 정보를 [REDACTED]로 치환.

    IMP-20260529-D8BA MT-1: SECRET_PATTERNS SSoT(상단)와 키-값 형식 보조 패턴을
    함께 적용한다.
    """
    redacted = text
    # SSoT 패턴(SECRET_PATTERNS)은 전체 매치를 [REDACTED]로 치환
    for _pattern_name, pattern in SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    # 키-값 형식 보조 패턴은 값(group 2)만 치환하여 키 이름은 컨텍스트로 남긴다
    redacted = _REDACTION_KV_PATTERN.sub(
        lambda m: m.group(0).replace(m.group(2), "[REDACTED]"), redacted
    )
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
    if not _openai_advisory_enabled() and not _openai_advisory_required():
        return {
            "status": "SKIPPED",
            "reason": "ENABLE_GPT_ADVISORY is not 1 and ENABLE_GPT_ADVISORY_REQUIRED is not 1",
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
            ambiguous_files: List[str] = sorted({str(item.get("review_file") or "") for item in candidates})
            _die(f"advisory finding id is ambiguous; re-run with --review-file. Matches: {ambiguous_files}")
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
    """브랜치 external gate/artifact 상태 비교 출력."""
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
    print(f"  {'브랜치':<8} {'External Gates':<28} {'Build':<10} {'결과'}")
    print(f"  {'-'*8} {'-'*28} {'-'*10} {'-'*18}")

    candidates: List[str] = []
    for b in t.get("branches", []):
        if not isinstance(b, str):
            continue  # allowed: 비정상 항목 방어 (skip non-str entries)
        bs = _load_state_for(b)
        phases = bs.get("phases", {})
        harness_info = phases.get("harness", {})
        build_info = phases.get("build", {})
        external_gates = bs.get("external_gates", {})

        build_ok = isinstance(build_info, dict) and build_info.get("status") not in (None, "FAILED", "PENDING")
        harness_status = harness_info.get("status") if isinstance(harness_info, dict) else None
        gate_bits: List[str] = []
        all_pass = True
        if isinstance(external_gates, dict):
            for gate_name in ("technical", "oracle", "github_ci", "acceptance"):
                gate = external_gates.get(gate_name, {})
                status = gate.get("status", "PENDING") if isinstance(gate, dict) else "PENDING"
                gate_bits.append(f"{gate_name}:{status}")
                if status != "PASS":
                    all_pass = False
        else:
            all_pass = False
        gate_summary = ", ".join(gate_bits) if gate_bits else "no gate state"

        if not build_ok:
            result = "ELIMINATED (Build FAIL)"
        elif all_pass:
            result = "후보"
            candidates.append(b)
        else:
            result = harness_status if harness_status in ("PASS", "FAIL") else "진행중"

        build_label = "OK" if build_ok else "FAIL"
        print(f"  {b:<8} {gate_summary:<28} {build_label:<10} {result}")

    if candidates:
        print(f"\n  후보 브랜치: {', '.join(candidates)}")
        print("  사용자에게 결과물 비교표를 보여준 뒤 승자를 확정하세요:")
        print(f"  python pipeline.py tournament-finalize --pipeline-id {args.pipeline_id} --winner <BRANCH>")
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
    print("  결과는 pipeline_history/ 에 보관됩니다.")
    print()


# ── CLI parser ───────────────────────────────────────────────────────────────
# IMP-20260528-0A9E MT-2: golden task 상수 (build_parser보다 앞서 정의)
_GOLDEN_TASKS_DIR: str = "tests/golden_tasks"
_GOLDEN_SCHEMA_REQUIRED_FIELDS: List[str] = [
    "id", "description", "command", "smoke",
    "allowed_files", "forbidden_files", "acceptance_criteria", "return_phase",
]


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
                        help="PM 출력 파일 경로 (<decomposition_audit>/<step_plan>/<design_confirmation>/<micro_tasks> hard 검증 필수)")
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
    # PM Clarification Gate 인자 (IMP-20260523-D80A)
    p_done.add_argument("--clarification-needed", action="store_true", default=False,
                        help="[pm 전용] clarification이 아직 필요함을 기록 (해소 전)")
    p_done.add_argument("--no-clarification-needed", action="store_false", dest="clarification_needed",
                        help="[pm 전용] clarification이 해소되었음을 기록")
    p_done.add_argument("--clarification-assumptions", default="없음",
                        help="[pm 전용] PM이 추론한 전제 사항 (기본값: '없음')")
    p_done.add_argument("--clarification-criteria-source", default="user",
                        choices=["user", "pm", "inferred"],
                        help="[pm 전용] acceptance_criteria 출처 (user/pm/inferred)")
    p_done.add_argument("--clarification-criteria", default=None,
                        help="[pm 전용] 검수 기준 목록 (쉼표 구분)")
    # MT-5: Frozen Codebase scope_declaration 선언 플래그 (IMP-20260506-A064)
    p_done.add_argument("--scope-declared", action="store_true", default=False,
                        help="[dev 전용] dev-agent가 <scope_declaration>을 출력했음을 선언")
    p_done.add_argument("--scope-manifest", default=None,
                        help="[dev 전용] 모든 파이프라인에서 필수인 scope_manifest.json 경로")

    p_done.add_argument("--agent-run-id", default=None,
                        help="[dev 전용] Option A completed dev-agent run receipt id")
    p_done.add_argument("--planner-run-id", default=None,
                        help="[pm 전용] completed pm-planner-agent run receipt id")
    p_done.add_argument("--manager-run-id", default=None,
                        help="[pm 전용] completed pipeline-manager-agent run receipt id")
    p_done.add_argument("--manager-report", default=None,
                        help="[pm 전용] manager_handoff.xml path from pipeline-manager-agent")

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
                      help=f"QA 중간 hard-gate 값 0~{QA_MAX_SCORE}. PASS 시 {QA_PASS_THRESHOLD}점 이상 필수; 최종 COMPLETE 점수가 아님.")
    # MT-3: Circuit Breaker failure_signature 추적 (IMP-20260506-A064)
    p_qa.add_argument("--failure-sig", default=None, metavar="SIG",
                      help="QA FAIL 시 <failure_signature>[category]:[hash]</failure_signature> 값. "
                           "동일 시그니처 연속 2회 감지 시 RECURRING 경고 출력.")

    p_qa.add_argument("--agent-run-id", default=None,
                      help="Option A: completed qa-agent run receipt id")

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
    # Backward-compatible only: N/A build no longer asks for an intermediate user confirmation.
    p_build.add_argument(
        "--user-confirmed",
        action="store_true",
        default=False,
        help='Backward-compatible no-op. N/A 빌드는 중간 사용자 확인 없이 기록되며 최종 gates accept에서만 사용자 판단을 받음.',
    )

    p_build.add_argument("--agent-run-id", default=None,
                         help="Option A: completed build-agent run receipt id")
    p_build.add_argument(
        "--build-deferred",
        action="store_true",
        default=False,
        help="패키징 파일 변경이 감지되었으나 빌드를 최종 ACCEPT 직전으로 유보. "
             "build_deferred=true를 pipeline_state.json에 기록하고 gate 검증 없이 종료.",
    )

    # agent run receipts
    p_agent = sub.add_parser("agent", help="Start/finish trusted per-phase agent run receipts")
    agsub = p_agent.add_subparsers(dest="agent_action", required=True)
    p_agent_start = agsub.add_parser("start", help="Start an agent run and print one-time token")
    p_agent_start.add_argument("--phase", required=True, choices=list(AGENT_RUN_PHASES))
    p_agent_start.add_argument("--agent-id", default=None, help="Defaults to the required agent for the phase")
    p_agent_finish = agsub.add_parser("finish", help="Finish an agent run and write receipt")
    p_agent_finish.add_argument("--run-id", required=True)
    p_agent_finish.add_argument("--token", required=True)
    p_agent_finish.add_argument("--output-file", required=True)
    p_agent_finish.add_argument("--evidence", default=None, help="Comma-separated files created or verified by the agent")
    p_agent_finish.add_argument("--notes", default=None)
    agsub.add_parser("status", help="Show agent run receipts without token hashes")

    # user-visible outputs for final ACCEPT
    p_outputs = sub.add_parser("outputs", help="Register or list final user-visible result files")
    osub = p_outputs.add_subparsers(dest="outputs_action", required=True)
    p_outputs_add = osub.add_parser("add", help="Register a result file and copy it under pipeline_outputs/<pipeline_id>/")
    p_outputs_add.add_argument("--kind", required=True,
                               choices=["report", "screenshot", "excel", "exe", "log", "other"],
                               help="Result file kind shown in the final PR acceptance packet")
    p_outputs_add.add_argument("--path", required=True, help="File path to register")
    p_outputs_add.add_argument("--label", default="", help="Short Korean label for the user-visible result")
    p_outputs_add.add_argument("--notes", default="", help="Short Korean notes explaining what the user should inspect")
    p_outputs_add.add_argument("--no-copy", action="store_true", default=False,
                               help="Keep the original file path instead of copying to pipeline_outputs/<pipeline_id>/")
    osub.add_parser("status", help="Show registered user-visible outputs")

    # IMP-20260603-2E3D MT-1: report — PR Packet SSoT 명령
    p_report = sub.add_parser(
        "report",
        help="PR 본문 / 최종 확인 안내 자동 생성 (final-packet | update-pr-body)",
    )
    report_sub = p_report.add_subparsers(dest="report_action", required=True)
    p_report_final = report_sub.add_parser(
        "final-packet",
        help="human_acceptance_packet.md 생성 (실제 git/gh/state 자료 기반)",
    )
    p_report_final.add_argument(
        "--base", default="origin/main", metavar="REF",
        help="git diff 비교 기준 ref (기본값: origin/main)",
    )
    # IMP-20260603-9934 MT-3: --force-new-request — PENDING 차단 해제 옵션
    p_report_final.add_argument(
        "--force-new-request", dest="force_new_request", action="store_true", default=False,
        help="PENDING acceptance_request가 있어도 packet 재생성 (기존 PENDING은 EXPIRED 처리)",
    )
    p_report_update = report_sub.add_parser(
        "update-pr-body",
        help="현재 PR 본문의 PIPELINE_FINAL_PACKET 블록을 최신 packet 내용으로 교체",
    )
    # IMP-20260603-9934 MT-3: --force-new-request — PENDING 차단 해제 옵션
    p_report_update.add_argument(
        "--force-new-request", dest="force_new_request", action="store_true", default=False,
        help="PENDING acceptance_request가 있어도 PR 본문 업데이트 (기존 PENDING은 EXPIRED 처리)",
    )

    # Codex compatibility hooks
    p_codex = sub.add_parser("codex", help="Codex compatibility checks for the mandatory pipeline")
    codex_sub = p_codex.add_subparsers(dest="codex_action", required=True)
    p_codex_doctor = codex_sub.add_parser("doctor", help="Check whether Codex can use the pipeline task hook")
    p_codex_doctor.add_argument("--json", action="store_true", default=False)

    # review — Codex Review Gate namespace
    p_review = sub.add_parser("review", help="Codex Review Gate — 코드 리뷰 결과 관리")
    review_sub = p_review.add_subparsers(dest="review_action", required=True)

    p_review_codex = review_sub.add_parser(
        "codex",
        help="git diff 메타데이터 수집 및 codex_review_result.json에 stage 기록 추가 (MT-2: IMP-20260516-A627)",
    )
    p_review_codex.add_argument("--base", default="main", metavar="REF",
                                help="비교 기준 브랜치/커밋 (기본값: main)")
    p_review_codex.add_argument("--output", default=None, metavar="PATH",
                                help="출력 파일 경로 (기본값: codex_review_result.json)")
    p_review_codex.add_argument(
        "--stage",
        dest="stage",
        default="",
        metavar="STAGE",
        help="리뷰 단계: plan|scope|code|hygiene|pr|rca (필수)",
    )
    p_review_codex.add_argument(
        "--result",
        dest="result_value",
        default="PENDING",
        metavar="RESULT",
        help="리뷰 결과: ACCEPT|REJECT|PENDING (기본값: PENDING)",
    )
    p_review_codex.add_argument(
        "--review-model",
        dest="review_model",
        default=CODEX_REQUIRED_MODEL,
        metavar="MODEL",
        help=f"리뷰 모델 식별자 (기본값: {CODEX_REQUIRED_MODEL}, 다른 값 시 경고)",
    )
    p_review_codex.add_argument(
        "--reviewer",
        dest="reviewer",
        default="unknown",
        metavar="REVIEWER",
        help="리뷰어 식별자 (기본값: unknown)",
    )
    p_review_codex.add_argument(
        "--pipeline-id",
        dest="pipeline_id_arg",
        default="",
        metavar="ID",
        help="파이프라인 ID (생략 시 활성 state에서 자동 추출)",
    )
    p_review_codex.add_argument(
        "--pr-number",
        dest="pr_number",
        default=None,
        metavar="PR",
        help="PR 번호 또는 URL (선택)",
    )
    p_review_codex.add_argument(
        "--head-sha",
        dest="head_sha",
        default=None,
        metavar="SHA",
        help="리뷰 시점의 git HEAD SHA (선택; codex-record에서 4중 검증에 사용됨)",
    )
    p_review_codex.add_argument(
        "--notes",
        dest="notes",
        default=None,
        metavar="TEXT",
        help="리뷰 노트 (선택)",
    )
    p_review_codex.add_argument(
        "--findings-file",
        dest="findings_file",
        default=None,
        metavar="PATH",
        help="findings 배열 JSON 파일 경로 (선택; 없으면 빈 배열 또는 기존 findings 유지)",
    )

    p_review_status = review_sub.add_parser("status", help="미해결 HIGH/CRITICAL findings 수 출력")
    p_review_status.add_argument("--output", default=None, metavar="PATH",
                                 help="codex_review_result.json 경로 (기본값: codex_review_result.json)")
    p_review_status.add_argument("--json", dest="json_output", action="store_true",
                                 help="JSON 형식으로 출력 (기본값과 동일)")

    p_review_resolve = review_sub.add_parser("resolve", help="특정 finding을 resolved=true로 표시")
    p_review_resolve.add_argument("--id", dest="finding_id", required=True, metavar="ID",
                                  help="해소할 finding ID (예: CR-001)")
    p_review_resolve.add_argument("--resolution-file", dest="resolution_file", default=None,
                                  metavar="PATH", help="해소 내용 JSON 파일 (선택)")
    p_review_resolve.add_argument("--output", default=None, metavar="PATH",
                                  help="codex_review_result.json 경로 (기본값: codex_review_result.json)")

    # codex-run — 실제 OpenAI Responses API 호출 (MT-1: IMP-20260516-00DE)
    p_review_codex_run = review_sub.add_parser(
        "codex-run",
        help="실제 OpenAI Responses API(GPT-5.5)로 Codex review 실행 및 결과 저장",
    )
    p_review_codex_run.add_argument(
        "--stage", dest="stage", required=True,
        metavar="STAGE", help="리뷰 단계: plan|scope|code|hygiene|pr|rca",
    )
    p_review_codex_run.add_argument(
        "--base", dest="base_ref", default="main",
        metavar="REF", help="비교 기준 브랜치 (기본값: main)",
    )
    p_review_codex_run.add_argument(
        "--output", dest="output", default="codex_review_result.json",
        metavar="PATH", help="결과 출력 경로 (기본값: codex_review_result.json)",
    )
    p_review_codex_run.add_argument(
        "--raw-output", dest="raw_output", default="codex_run_raw.json",
        metavar="PATH", help="raw provider 응답 저장 경로 (기본값: codex_run_raw.json)",
    )
    # IMP-20260517-30DD MT-1: provider 선택 인수
    p_review_codex_run.add_argument(
        "--provider", dest="provider", default="openai-api",
        choices=["openai-api", "codex-cli"],
        help="Codex review provider (기본값: openai-api). openai-api: OPENAI_API_KEY 환경변수 필요. "
             "codex-cli: codex CLI 설치 및 인증 필요.",
    )

    # codex-record — 실제 Codex review ACCEPT/REJECT 공식 기록 (MT-3: IMP-20260516-A627)
    p_review_record = review_sub.add_parser(
        "codex-record",
        help="실제 Codex review(GPT-5.5) 세션 ACCEPT/REJECT를 공식 기록 (pr/rca stage 전용, 4중 검증)",
    )
    p_review_record.add_argument(
        "--stage", dest="stage", required=True,
        metavar="STAGE", help="리뷰 단계: plan|scope|code|hygiene|pr|rca",
    )
    p_review_record.add_argument(
        "--result", dest="result_value", required=True,
        metavar="RESULT", help="리뷰 결과: ACCEPT|REJECT",
    )
    p_review_record.add_argument(
        "--review-model", dest="review_model", default=CODEX_REQUIRED_MODEL,
        metavar="MODEL", help=f"리뷰 모델 (반드시 {CODEX_REQUIRED_MODEL})",
    )
    p_review_record.add_argument(
        "--head-sha", dest="head_sha", default=None,
        metavar="SHA", help="리뷰 시점의 git HEAD SHA (ACCEPT 시 필수)",
    )
    p_review_record.add_argument(
        "--diff-sha256", dest="diff_sha256_arg", default=None,
        metavar="SHA256", help="리뷰한 diff의 SHA256 (ACCEPT 시 필수)",
    )
    p_review_record.add_argument(
        "--evidence", dest="evidence", required=False, default=None,
        metavar="PATH",
        help="Codex review 결과 JSON 파일 경로 (review_model 필드 포함). pr/rca stage에서 ACCEPT 시 필수.",
    )
    p_review_record.add_argument(
        "--notes", dest="notes", default=None,
        metavar="TEXT", help="리뷰 노트 (REJECT 시 --notes 또는 --required-actions 중 하나 필수)",
    )
    p_review_record.add_argument(
        "--required-actions", dest="required_actions", default=None,
        metavar="TEXT", help="필요 조치 콤마 구분 목록 (REJECT 시 --notes 또는 이 인자 중 하나 필수)",
    )
    p_review_record.add_argument(
        "--return-phase", dest="return_phase", default=None,
        metavar="PHASE", help="REJECT 시 되돌아갈 phase (예: dev, qa, pm)",
    )
    p_review_record.add_argument(
        "--pr", dest="pr_number", default=None,
        metavar="PR", help="PR 번호 또는 URL (선택)",
    )
    p_review_record.add_argument(
        "--reviewer", dest="reviewer", default="unknown",
        metavar="REVIEWER", help="리뷰어 식별자 (기본값: unknown)",
    )
    p_review_record.add_argument(
        "--pipeline-id", dest="pipeline_id_arg", default="",
        metavar="ID", help="파이프라인 ID (생략 시 활성 state에서 자동 추출)",
    )
    p_review_record.add_argument(
        "--output", default=None,
        metavar="PATH", help="codex_review_result.json 출력 경로 (기본값: codex_review_result.json)",
    )

    p_review.set_defaults(func=cmd_review)

    # preflight — pre-PM fact collection
    p_preflight = sub.add_parser("preflight", help="파이프라인 사전 점검 — preflight_report.json 생성")
    p_preflight.add_argument("--pipeline-id", default=None, help="파이프라인 ID (생략 시 active pipeline_state에서 자동 추출)")
    p_preflight.add_argument("--ruff-codes", default="", metavar="CODES",
                             help="검증할 ruff rule 코드 콤마 구분 목록 (예: PLW0621,E501). 생략 시 기본 4개 코드 사용.")
    p_preflight.add_argument("--output", default=None, metavar="PATH",
                             help="출력 파일 경로 (생략 시 preflight_report.json)")
    p_preflight.set_defaults(func=cmd_preflight)

    # harness
    p_harness = sub.add_parser("harness", help="Legacy harness diagnostic 기록. 현재 /Task 완료 경로에서는 차단됨")
    p_harness.add_argument("--score", required=True, type=int, help="Legacy diagnostic percentage only; not a completion score")
    p_harness.add_argument("--verdict", required=True, choices=["PASS", "FAIL", "pass", "fail"])
    p_harness.add_argument("--branch", metavar="BRANCH", default=None,
                           help="브랜치 ID (A-Z 대문자 1글자). 지정 시 브랜치 state 파일 사용.")
    p_harness.add_argument("--test-output-file", default=None,
                           help="harness-agent 출력 파일 경로. PASS/FAIL 공통 필수. PASS/FAIL 양쪽 모두: <harness_report>(ET 검증) + <test_code> strict unittest evidence gate 필요. 통과 조건: astAsserts>=1, 금지 패턴 없음(__main__/atexit/inspect/os/sys.argv/sys.modules/getattr/setattr 등), runner nonce JSON 일치, executed_assertions>=1, testsRun>=1, failures/errors/skipped/expectedFailures/unexpectedSuccesses==0. <test_code>는 CDATA 권장.")
    p_harness.add_argument(
        "--user-confirmed",
        action="store_true",
        default=False,
        help="Backward-compatible no-op. 신규 /Task 완료는 gates accept에서만 사용자 확인.",
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
                                 help="Backward-compatible no-op; external gates are mandatory")
    p_contract_init.add_argument("--phase-attestations", action="store_true", default=False,
                                 help="Backward-compatible no-op; phase attestations are mandatory")

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

    p_contract_audit_oracle = csub.add_parser("audit-oracle", help="Oracle quality audit for a pipeline (IMP-20260524-48C4)")
    p_contract_audit_oracle.add_argument("--pipeline-id", default=None)
    p_contract_audit_oracle.add_argument("--allow-agent-generated", action="store_true", default=False)
    p_contract_audit_oracle.add_argument("--oracle-dir", default=None, help="TC directory containing input.json with oracle entries (overrides oracle_manifest.json)")

    p_contract_ready = csub.add_parser("ready", help="Check Definition of Ready")
    p_contract_ready.add_argument("--pipeline-id", default=None)
    p_contract_ready.add_argument("--json", action="store_true", default=False)

    p_contract_freeze = csub.add_parser("freeze", help="Freeze contract and test_set before Dev")
    p_contract_freeze.add_argument("--pipeline-id", default=None)
    p_contract_freeze.add_argument("--force", action="store_true", default=False)

    p_contract_show = csub.add_parser("show", help="Show contract v2 status")
    p_contract_show.add_argument("--pipeline-id", default=None)

    # acceptance v2
    p_acceptance = sub.add_parser("acceptance", help="Run contract-based acceptance diagnostics")
    asub = p_acceptance.add_subparsers(dest="acceptance_action", required=True)
    p_acceptance_run = asub.add_parser("run", help="Run frozen test_set.json and write diagnostic result")
    p_acceptance_run.add_argument("--pipeline-id", default=None)
    p_acceptance_run.add_argument("--output", default=None)
    p_acceptance_run.add_argument("--record", action="store_true", default=False)
    p_acceptance_run.add_argument("--user-confirmed", action="store_true", default=False,
                                  help="Backward-compatible no-op. Acceptance diagnostics are automatic; final user decision is gates accept.")

    # incremental module gates
    p_module = sub.add_parser("module", help="Incremental Dev/QA gates for each PM micro_task")
    msub = p_module.add_subparsers(dest="module_action", required=True)
    p_module_design = msub.add_parser("design", help="Record detailed design for one micro_task")
    p_module_design.add_argument("--mt-id", required=True)
    p_module_design.add_argument("--report-file", required=True)
    p_module_dev = msub.add_parser("dev", help="Record implementation for one micro_task")
    p_module_dev.add_argument("--mt-id", required=True)
    p_module_dev.add_argument("--files", required=True)
    p_module_dev.add_argument("--report-file", required=True)
    p_module_dev.add_argument("--scope-manifest", required=True)
    p_module_qa = msub.add_parser("qa", help="Record QA result for one micro_task")
    p_module_qa.add_argument("--mt-id", required=True)
    p_module_qa.add_argument("--result", required=True, choices=["PASS", "FAIL", "pass", "fail"])
    p_module_qa.add_argument("--report-file", required=True)
    p_module_integrate = msub.add_parser("integrate", help="Record final integration after all module QA gates pass")
    p_module_integrate.add_argument("--result", required=True, choices=["PASS", "FAIL", "pass", "fail"])
    p_module_integrate.add_argument("--report-file", required=True)
    msub.add_parser("status", help="Show incremental module gate state")

    # three_gate external gates
    p_gates = sub.add_parser("gates", help="External three-gate status and runners")
    gsub = p_gates.add_subparsers(dest="gates_action", required=True)
    p_gates_init = gsub.add_parser("init", help="Migrate or show mandatory external gate state")
    p_gates_init.add_argument("--phase-attestations", action="store_true", default=False,
                              help="Backward-compatible no-op; phase attestations are mandatory")
    gsub.add_parser("status", help="Show external gate state")
    p_gate_prepare = gsub.add_parser("prepare-phase", help="Write .pipeline phase attestation request for CI")
    p_gate_prepare.add_argument("--phase", required=True, choices=list(PHASE_ATTESTATION_PHASES))
    p_gate_phase_ci = gsub.add_parser("phase-ci", help="Verify GitHub Actions phase attestation artifact")
    p_gate_phase_ci.add_argument("--phase", required=True, choices=list(PHASE_ATTESTATION_PHASES))
    p_gate_phase_ci.add_argument("--repo", default=None, help="owner/repo; defaults to origin remote")
    p_gate_phase_ci.add_argument("--run-id", default=None, help="Specific GitHub Actions workflow run id; omitted means latest run for HEAD")
    p_gate_phase_ci.add_argument("--commit", default=None, help="Expected commit SHA/ref; defaults to local HEAD")
    p_gate_phase_ci.add_argument("--workflow", default="CI", help="Workflow run name to search when --run-id is omitted")
    p_gate_phase_ci.add_argument("--artifact", default="pipeline-phase-attestation", help="Required phase artifact name")
    p_gate_phase_ci.add_argument("--token-env", default="GITHUB_TOKEN", help="Optional env var containing a GitHub token")
    p_gate_tech = gsub.add_parser("technical", help="Run deterministic technical tool gate")
    p_gate_tech.add_argument("--strict-tools", action="store_true", default=False,
                             help="Deprecated no-op; strict tool checks are the default")
    p_gate_tech.add_argument("--relaxed-tools", action="store_true", default=False,
                             help="Allow missing optional tools to be recorded as SKIP instead of FAIL")
    p_gate_tech.add_argument("--timeout", type=int, default=120)
    p_gate_oracle = gsub.add_parser("oracle", help="Run oracle/acceptance gate and record external gate result")
    p_gate_oracle.add_argument("--user-confirmed", action="store_true", default=False,
                               help="Backward-compatible no-op. Oracle gate is automatic; final user decision is gates accept.")
    # IMP-20260524-48C4 MT-1: agent_generated expected 허용 옵션
    p_gate_oracle.add_argument("--allow-agent-generated", action="store_true", default=False, dest="allow_agent_generated",
                               help="agent_generated source oracle을 BLOCKED 처리하지 않고 허용한다 (기본 비허용)")
    # IMP-20260531-BBDB MT-2: gates request-accept — 사용자 최종 확인 코드(nonce) 발급
    p_gate_req = gsub.add_parser(
        "request-accept",
        help="사용자 최종 확인 코드(nonce) 발급 — acceptance_request.json 생성 + PR 댓글 갱신",
    )
    p_gate_req.add_argument("--evidence", required=True, help="결과물 경로(파일) 또는 URL(http://, https://)")
    # IMP-20260531-AEF0 MT-1: --force-new-code — 동일 조건이어도 새 nonce 강제 발급
    p_gate_req.add_argument(
        "--force-new-code",
        dest="force_new_code",
        action="store_true",
        default=False,
        help="기존 코드가 PENDING이고 조건이 같아도 새 코드를 강제 발급합니다.",
    )

    p_gate_accept = gsub.add_parser("accept", help="Record user behavior acceptance")
    p_gate_accept.add_argument("--result", required=True, choices=["ACCEPT", "REJECT", "accept", "reject"])
    p_gate_accept.add_argument("--evidence", default=None, help="Output file, screenshot, or report shown to user")
    p_gate_accept.add_argument("--notes", default=None)
    p_gate_accept.add_argument("--user-confirmed", action="store_true", default=False)
    # IMP-20260531-BBDB MT-3: --acceptance-code (gates request-accept가 발급한 일회용 nonce 코드)
    p_gate_accept.add_argument("--acceptance-code", dest="acceptance_code", default=None,
        help="One-time code from gates request-accept (e.g. ACCEPT-IMP-20260531-BBDB-XXXXXXXX)")
    p_gate_preflight_pr = gsub.add_parser("preflight-pr", help="PR에 섞인 무관한 파일을 검사하여 phase attestation 오염을 차단")
    p_gate_preflight_pr.add_argument("--phase", required=True, choices=["pm", "dev", "qa", "build"],
                                     help="검사할 phase (pm|dev|qa|build)")
    p_gate_preflight_pr.add_argument("--pipeline-id", dest="pipeline_id", default=None,
                                     help="pipeline_id (없으면 pipeline_state.json에서 읽음)")
    p_gate_preflight_pr.add_argument("--request-file", dest="request_file",
                                     default=".pipeline/phase_attestation_request.json",
                                     help="phase_attestation_request.json 경로")

    # IMP-20260528-3898 MT-2: preflight-pr-impl — 구현 PR 내부 산출물 검사
    # BUG-20260529-40C9 MT-1: --files 옵션 추가 (git diff 대신 명시적 파일 목록 지정 가능)
    _p_preflight_impl = gsub.add_parser(
        "preflight-pr-impl",
        help=(
            "구현 PR(impl 브랜치)에 pipeline 내부 산출물(build_report.xml, "
            "scope_manifest_MT-*.json 등)이 포함되지 않았는지 검사합니다. "
            "WORKSPACE_INTERNAL_PATTERNS SSoT 사용. CI에서 gates preflight-pr-impl로 호출."
        ),
    )
    _p_preflight_impl.add_argument(
        "--files",
        default=None,
        help=(
            "쉼표로 구분된 파일 목록 (git diff 대신 사용). "
            "golden task 등 테스트 픽스처에서 재현 가능한 입력을 제공할 때 사용합니다."
        ),
    )

    # IMP-20260529-D8BA MT-1: gates secrets — 민감 정보 검사 gate
    p_gate_secrets = gsub.add_parser(
        "secrets",
        help="민감 정보 검사 — PR diff와 주요 보고서에서 API 키/토큰/비밀 문자열을 검사한다.",
    )
    p_gate_secrets.add_argument(
        "--files", default=None,
        help="검사할 파일 목록 (콤마 구분). 지정 시 git diff 대신 해당 파일 내용을 스캔한다.",
    )
    p_gate_secrets.add_argument(
        "--base-ref", dest="base_ref", default=None,
        help="git diff base reference (기본: origin/main). --files 없을 때 사용.",
    )
    p_gate_secrets.add_argument(
        "--report-files", dest="report_files", default=None,
        help="추가 검사 대상 보고서 파일 목록 (콤마 구분).",
    )

    p_gate_consistency = gsub.add_parser(
        "consistency",
        help="Protocol consistency check between PR body, acceptance packet, and actual CI state",
    )
    p_gate_consistency.add_argument(
        "--repo", required=False, default=None,
        help="owner/repo (예: hojiyong2-commits/Pipeline)",
    )
    p_gate_consistency.add_argument(
        "--pr", required=False, default=None, help="PR 번호",
    )
    p_gate_consistency.add_argument(
        "--dry-run", action="store_true", default=False,
        help="gh CLI 없이 --input-file JSON으로 consistency 검사를 실행한다 (테스트/CI 용)",
    )
    p_gate_consistency.add_argument(
        "--input-file", default=None,
        help="--dry-run 시 사용할 입력 JSON 파일 경로",
    )

    p_gate_batch_ci = gsub.add_parser("batch-ci", help="변경 파일 목록을 기반으로 CI 모드(per_phase/batched) 결정")
    p_gate_batch_ci.add_argument("--probe", action="store_true", default=False,
                                 help="프로브 모드: 상태 기록 없이 ci_mode만 출력")
    p_gate_batch_ci.add_argument("--changed-files", default="", metavar="FILES",
                                 help="콤마 구분 변경 파일 목록 (예: pipeline.py,README.md)")

    p_gate_github = gsub.add_parser("github-ci", help="Verify latest GitHub Actions CI run and record github_ci gate")
    p_gate_github.add_argument("--repo", default=None, help="owner/repo; defaults to origin remote")
    p_gate_github.add_argument("--run-id", default=None, help="Specific GitHub Actions workflow run id; omitted means latest run for HEAD")
    p_gate_github.add_argument("--commit", default=None, help="Expected commit SHA; defaults to local HEAD")
    p_gate_github.add_argument("--workflow", default="CI", help="Workflow run name to search when --run-id is omitted")
    p_gate_github.add_argument("--artifact", default="pipeline-attestation", help="Required artifact name")
    p_gate_github.add_argument("--token-env", default="GITHUB_TOKEN", help="Optional env var containing a GitHub token")
    # IMP-20260524-C097 MT-2: SHA 검증 강화 — 예상 head SHA를 명시적으로 전달 가능
    p_gate_github.add_argument(
        "--head-sha",
        dest="head_sha",
        default=None,
        help="기대하는 head SHA (선택). 지정 시 CI run의 head_sha와 이중 검증 수행.",
    )

    # IMP-20260524-C097 MT-1: SHA 기반 CI run polling (blind wait 제거)
    p_gate_wait_ci = gsub.add_parser(
        "wait-github-ci",
        help="head SHA 기준으로 GitHub CI run을 polling하여 완료를 대기합니다 (blind wait 대체)",
    )
    p_gate_wait_ci.add_argument("--repo", default=None, help="owner/repo; 기본값: origin remote")
    p_gate_wait_ci.add_argument("--pr", type=int, default=None, help="PR 번호 (로그 출력용)")
    p_gate_wait_ci.add_argument("--head-sha", dest="head_sha", default=None,
                                help="기대하는 head SHA (기본값: 로컬 HEAD)")
    p_gate_wait_ci.add_argument("--timeout-sec", dest="timeout_sec", type=int, default=600,
                                help="최대 대기 시간(초, 기본값: 600)")
    p_gate_wait_ci.add_argument("--poll-sec", dest="poll_sec", type=int, default=15,
                                help="polling 간격(초, 기본값: 15)")
    p_gate_wait_ci.add_argument("--token-env", dest="token_env", default="GITHUB_TOKEN",
                                help="GitHub 토큰 환경 변수명")

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

    # GitHub external runner / CI artifact verification
    p_github = sub.add_parser("github", help="Verify GitHub Actions external-runner artifacts")
    ghsub = p_github.add_subparsers(dest="github_action", required=True)
    p_gh_verify = ghsub.add_parser("verify-run", help="Verify a GitHub Actions run attestation artifact")
    p_gh_verify.add_argument("--repo", default=None, help="owner/repo; defaults to origin remote")
    p_gh_verify.add_argument("--run-id", default=None, help="GitHub Actions workflow run id; omitted means latest run for HEAD")
    p_gh_verify.add_argument("--commit", default=None, help="Expected commit SHA; defaults to local HEAD")
    p_gh_verify.add_argument("--workflow", default="CI", help="Workflow run name to search when --run-id is omitted")
    p_gh_verify.add_argument("--artifact", default="pipeline-attestation", help="Required artifact name")
    p_gh_verify.add_argument("--token-env", default="GITHUB_TOKEN", help="Optional env var containing a GitHub token")
    p_gh_verify.add_argument("--record", action="store_true", default=False, help="Record verification summary in pipeline_state.json")

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
    p_check.add_argument(
        "--user-confirmed",
        action="store_true",
        default=False,
        help="Backward-compatible no-op. Phase 6→7은 자동 진행되며 최종 gates accept에서만 사용자 확인.",
    )
    p_check.add_argument(
        "--codex-review-waiver",
        dest="codex_review_waiver",
        default="",
        metavar="REASON",
        help=(
            "Codex Review Gate waiver 이유. "
            "허용 값: 'legacy-bootstrap' (IMP 머지 직전 기존 파이프라인 보호용). "
            "이 플래그 사용 시 해당 check 결과는 waived로 기록됩니다. (MT-4: IMP-20260516-A627)"
        ),
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
    p_tr = sub.add_parser("tournament-rank", help="브랜치 external gate/artifact 비교")
    p_tr.add_argument("--pipeline-id", required=True, help="토너먼트 파이프라인 ID")

    # tournament-finalize
    p_tf = sub.add_parser("tournament-finalize", help="토너먼트 종료 및 승자 확정")
    p_tf.add_argument("--pipeline-id", required=True, help="토너먼트 파이프라인 ID")
    p_tf.add_argument("--winner", required=True, help="승자 브랜치 ID (A-Z 대문자 1글자)")

    # IMP-20260513-4C0B: cluster subcommand
    p_cl = sub.add_parser("cluster", help="Incident Cluster 관리")
    cl_sub = p_cl.add_subparsers(dest="cluster_sub", required=True)

    p_cl_detect = cl_sub.add_parser("detect", help="유사 클러스터 탐색")
    p_cl_detect.add_argument("--desc", default="", help="탐색 키워드")

    p_cl_init = cl_sub.add_parser("init", help="새 클러스터 생성")
    p_cl_init.add_argument("--desc", default="", help="클러스터 설명")

    p_cl_status = cl_sub.add_parser("status", help="클러스터 상태 조회")
    p_cl_status.add_argument("--cluster-id", default=None, help="특정 클러스터 ID (미지정 시 전체)")

    p_cl_attach = cl_sub.add_parser("attach", help="파이프라인을 클러스터에 연결")
    p_cl_attach.add_argument("--cluster-id", required=True, help="대상 클러스터 ID")

    p_cl_close = cl_sub.add_parser("close", help="클러스터 종료")
    p_cl_close.add_argument("--cluster-id", required=True, help="종료할 클러스터 ID")

    # IMP-20260527-075A MT-2: budget subcommand (Cost/Attempt Budget Gate)
    p_bg = sub.add_parser("budget", help="attempt budget 관리 (phase별 재시도 한도)")
    bg_sub = p_bg.add_subparsers(dest="budget_sub", required=True)

    p_bg_status = bg_sub.add_parser("status", help="attempt budget 현황 출력 (한국어)")
    p_bg_status.add_argument("--pipeline-id", default=None, help="대상 파이프라인 ID (미지정 시 현재 활성)")

    p_bg_reset = bg_sub.add_parser("reset", help="phase별 attempt budget 초기화 (관리자 작업)")
    p_bg_reset.add_argument("--phase", required=True, choices=["dev", "qa", "gate"],
                            help="초기화할 phase 이름")
    p_bg_reset.add_argument("--reason", required=True,
                            help="초기화 사유 (감사 로그용, 필수)")

    # IMP-20260513-4C0B: patch subcommand
    p_pt = sub.add_parser("patch", help="Patch Lane 관리")
    pt_sub = p_pt.add_subparsers(dest="patch_sub", required=True)

    p_pt_plan = pt_sub.add_parser("plan", help="Patch Lane 진입 조건 검사")
    p_pt_plan.add_argument("--plan", default="patch_plan.json", help="patch_plan.json 경로")

    p_pt_audit = pt_sub.add_parser("audit", help="패치 감사 및 자동 에스컬레이션 검사")
    p_pt_audit.add_argument("--plan", default="patch_plan.json", help="patch_plan.json 경로")

    p_pt_verify = pt_sub.add_parser("verify", help="패치 결과 검증")
    p_pt_verify.add_argument("--plan", default="patch_plan.json", help="patch_plan.json 경로")
    p_pt_verify.add_argument("--result", required=True, choices=["PASS", "FAIL"], help="검증 결과")
    p_pt_verify.add_argument("--test-command", dest="test_command", default="", help="검증에 사용한 테스트 명령어 (PASS 시 필수 — --evidence-file 대안)")
    p_pt_verify.add_argument("--evidence-file", dest="evidence_file", default="", help="검증 증거 파일 경로 (PASS 시 필수 — --test-command 대안)")

    p_pt_attest = pt_sub.add_parser("attest", help="패치 완료 증거 기록")
    p_pt_attest.add_argument("--plan", default="patch_plan.json", help="patch_plan.json 경로")

    # IMP-20260522-0C83: metrics subcommand (MT-2)
    p_mt = sub.add_parser("metrics", help="파이프라인 관측성 메트릭 수집 및 요약")
    mt_sub = p_mt.add_subparsers(dest="metrics_sub", required=True)

    p_mt_collect = mt_sub.add_parser("collect", help="메트릭 수집 및 JSON 저장")
    p_mt_collect.add_argument("--repo", default=None, help="GitHub 리포지토리 (owner/repo)")
    p_mt_collect.add_argument("--pr", type=int, default=None, help="PR 번호")
    p_mt_collect.add_argument("--output", default="pipeline_metrics.json", help="출력 JSON 경로")

    p_mt_summary = mt_sub.add_parser("summary", help="저장된 메트릭 JSON을 한국어 요약으로 출력")
    p_mt_summary.add_argument("--input", default="pipeline_metrics.json", help="입력 JSON 경로")

    mt_sub.add_parser("status", help="현재 활성 파이프라인 기준 메트릭 상태 요약 출력")

    # IMP-20260526-82E3 MT-4: metrics report --json / --markdown
    p_mt_report = mt_sub.add_parser(
        "report",
        help="현재 state 기준 metrics 보고서를 JSON 또는 한국어 Markdown으로 출력",
    )
    p_mt_report_fmt = p_mt_report.add_mutually_exclusive_group()
    p_mt_report_fmt.add_argument(
        "--json",
        dest="format",
        action="store_const",
        const="json",
        help="JSON 형식 출력 (기본값)",
    )
    p_mt_report_fmt.add_argument(
        "--markdown",
        dest="format",
        action="store_const",
        const="markdown",
        help="한국어 Markdown 형식 출력",
    )

    # IMP-20260528-0A9E MT-2: golden task CLI 서브파서
    p_golden = sub.add_parser("golden", help="Golden Task Regression Suite — 회귀 검증")
    golden_sub = p_golden.add_subparsers(dest="golden_sub", required=True)

    p_golden_list = golden_sub.add_parser("list", help="등록된 golden task 목록 조회")
    p_golden_list.add_argument(
        "--tasks-dir", dest="tasks_dir", default=_GOLDEN_TASKS_DIR,
        help=f"golden tasks 디렉터리 (기본값: {_GOLDEN_TASKS_DIR})",
    )

    p_golden_run = golden_sub.add_parser("run", help="golden task 실행")
    p_golden_run.add_argument("--task", default=None, help="실행할 태스크 ID")
    p_golden_run.add_argument("--all", action="store_true", default=False, help="모든 태스크 실행")
    p_golden_run.add_argument("--smoke", action="store_true", default=False, help="smoke=true 태스크만 실행")
    p_golden_run.add_argument(
        "--tasks-dir", dest="tasks_dir", default=_GOLDEN_TASKS_DIR,
        help=f"golden tasks 디렉터리 (기본값: {_GOLDEN_TASKS_DIR})",
    )

    # IMP-20260601-0DF5 MT-1: hygiene scan/archive 서브파서
    p_hygiene = sub.add_parser("hygiene", help="임시 산출물 정리 (scan/archive)")
    hygiene_sub = p_hygiene.add_subparsers(dest="hygiene_sub", required=True)

    p_hy_scan = hygiene_sub.add_parser("scan", help="임시 산출물 스캔 (이동 없음)")
    p_hy_scan.add_argument(
        "--older-than", dest="older_than", default="7d",
        help="이 일수 이상 된 파일만 후보로 표시 (기본값: 7d)",
    )
    p_hy_scan.add_argument(
        "--json", dest="json", action="store_true", default=False,
        help="JSON 형식으로 출력",
    )

    p_hy_archive = hygiene_sub.add_parser("archive", help="임시 산출물을 찌꺼기 폴더로 이동")
    p_hy_archive.add_argument(
        "--older-than", dest="older_than", default="7d",
        help="이 일수 이상 된 파일만 이동 (기본값: 7d)",
    )
    p_hy_archive.add_argument(
        "--json", dest="json", action="store_true", default=False,
        help="JSON 형식으로 출력",
    )
    p_hy_archive.add_argument(
        "--dry-run", dest="dry_run", action="store_true", default=False,
        help="실제 이동 없이 결과만 미리 확인",
    )

    # IMP-20260601-0DF5 MT-2: hygiene schedule 서브파서
    p_hy_schedule = hygiene_sub.add_parser("schedule", help="Windows 작업 스케줄러 등록/조회")
    schedule_sub_parser = p_hy_schedule.add_subparsers(dest="schedule_sub", required=True)

    p_hy_sch_install = schedule_sub_parser.add_parser("install", help="매주 월요일 09:00 hygiene archive 등록")
    p_hy_sch_install.add_argument(
        "--dry-run", dest="dry_run", action="store_true", default=False,
        help="실제 등록 없이 명령어만 출력",
    )
    p_hy_sch_install.add_argument(
        "--json", dest="json", action="store_true", default=False,
        help="JSON 형식으로 출력",
    )

    p_hy_sch_status = schedule_sub_parser.add_parser("status", help="작업 스케줄러 등록 상태 조회")
    p_hy_sch_status.add_argument(
        "--json", dest="json", action="store_true", default=False,
        help="JSON 형식으로 출력",
    )

    return parser


# ---------------------------------------------------------------------------
# IMP-20260513-4C0B: Patch Lane + Incident Cluster
# ---------------------------------------------------------------------------
# cluster.json 스키마:
#   {"id": "CL-XXXX", "desc": "...", "pipelines": [], "created_at": "...",
#    "closed_at": null, "patch_failures": 0, "patch_lane_forbidden": false}
#
# patch_plan.json 스키마 (schema_version=1):
#   {"schema_version": 1, "lane": "patch|full", "pipeline_id": "...",
#    "cluster_id": "...", "patch_scope": {...}, "verification": {...},
#    "forbidden": {...}, "user_confirmation": {...}}
# ---------------------------------------------------------------------------

import random as _random_mod

_CLUSTER_DIR = BASE_DIR / ".pipeline" / "clusters"
_PATCH_LANE_AUTO_ESCALATION_LINES = 15
_CLUSTER_MAX_PATCH_FAILURES = 2
_CLUSTER_AUTO_CLOSE_DAYS = 7


def _cluster_id_generate() -> str:
    """CL-XXXX 형태의 고유 클러스터 ID 생성."""
    chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    return "CL-" + "".join(_random_mod.choices(chars, k=4))


def _load_cluster_json(cluster_id: str) -> Optional[Dict[str, Any]]:
    """cluster.json 파일 로드. 없으면 None 반환."""
    _CLUSTER_DIR.mkdir(parents=True, exist_ok=True)
    path = _CLUSTER_DIR / f"{cluster_id}.json"
    if not path.is_file():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        _die(f"[CLUSTER ERROR] cluster.json 읽기 실패 ({cluster_id}): {exc}")


def _save_cluster_json(data: Dict[str, Any]) -> None:
    """cluster.json 원자적 쓰기 (UTF-8 LF)."""
    _CLUSTER_DIR.mkdir(parents=True, exist_ok=True)
    cluster_id = data.get("id", "unknown")
    path = _CLUSTER_DIR / f"{cluster_id}.json"
    tmp = path.with_suffix(".json.tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        tmp.replace(path)
    except OSError as exc:
        _die(f"[CLUSTER ERROR] cluster.json 쓰기 실패 ({cluster_id}): {exc}")


def _list_all_clusters() -> List[Dict[str, Any]]:
    """모든 cluster.json 파일 목록 반환."""
    _CLUSTER_DIR.mkdir(parents=True, exist_ok=True)
    result: List[Dict[str, Any]] = []
    for fp in sorted(_CLUSTER_DIR.glob("CL-*.json")):
        try:
            with open(fp, encoding="utf-8") as f:
                result.append(json.load(f))
        except (json.JSONDecodeError, OSError):
            continue
    return result


def _cluster_check_auto_close(cluster: Dict[str, Any]) -> bool:
    """7일 경과 시 자동 close. True 반환 시 방금 close됨."""
    if cluster.get("closed_at"):
        return False
    created = cluster.get("created_at", "")
    if not created:
        return False
    try:
        import datetime as _dt
        created_dt = _dt.datetime.fromisoformat(created.replace("Z", "+00:00"))
        now_dt = _dt.datetime.now(_dt.timezone.utc)
        if (now_dt - created_dt).days >= _CLUSTER_AUTO_CLOSE_DAYS:
            cluster["closed_at"] = _now()
            _save_cluster_json(cluster)
            return True
    except (ValueError, TypeError):
        pass
    return False


def _check_patch_lane_conditions(plan: Dict[str, Any]) -> List[str]:
    """patch_plan.json이 Patch Lane 진입 조건을 만족하는지 검사.
    위반 항목 목록 반환. 빈 리스트 = 모두 통과.
    조건: 1파일/1함수/15줄이하/비-trust-root/새의존성없음/파일이동삭제없음/기존oracle존재.
    """
    violations: List[str] = []
    scope = plan.get("patch_scope") or {}
    forbidden = plan.get("forbidden") or {}

    files_changed = scope.get("file")
    if not files_changed:
        violations.append("patch_scope.file 미지정")
    else:
        if isinstance(files_changed, list) and len(files_changed) > 1:
            violations.append(f"파일 수 초과: {len(files_changed)}개 (최대 1개)")
        elif isinstance(files_changed, str) and "," in files_changed:
            violations.append("파일 수 초과: 복수 파일 지정됨 (최대 1개)")

    func_changed = scope.get("function")
    if not func_changed:
        violations.append("patch_scope.function 미지정")
    else:
        if isinstance(func_changed, list) and len(func_changed) > 1:
            violations.append(f"함수 수 초과: {len(func_changed)}개 (최대 1개)")

    max_lines = scope.get("expected_lines_changed_max")
    if max_lines is not None:
        try:
            if int(max_lines) > _PATCH_LANE_AUTO_ESCALATION_LINES:
                violations.append(
                    f"예상 변경 줄 수 초과: {max_lines}줄 (최대 {_PATCH_LANE_AUTO_ESCALATION_LINES}줄)"
                )
        except (TypeError, ValueError):
            violations.append(f"patch_scope.expected_lines_changed_max 숫자 아님: {max_lines!r}")

    if forbidden.get("trust_root_changes"):
        violations.append("trust_root_changes=true: Patch Lane 금지 (신뢰 루트 파일 수정 불가)")
    if forbidden.get("new_dependencies"):
        violations.append("new_dependencies=true: Patch Lane 금지 (새 의존성 추가 불가)")
    if forbidden.get("file_move_or_delete"):
        violations.append("file_move_or_delete=true: Patch Lane 금지 (파일 이동/삭제 불가)")
    if forbidden.get("packaging_changes"):
        violations.append("packaging_changes=true: Patch Lane 금지 (패키징 변경 불가)")

    return violations


def _run_patch_audit(plan: Dict[str, Any], cluster: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """patch audit 실행. 위반 시 ESCALATE, 통과 시 PASS 반환.
    auto-escalation 조건: files>1 OR functions>1 OR trust_root 포함 OR lines>15.
    """
    escalate_reasons: List[str] = []
    scope = plan.get("patch_scope") or {}
    forbidden = plan.get("forbidden") or {}

    # 파일 수 체크
    files_changed = scope.get("file")
    if isinstance(files_changed, list):
        if len(files_changed) > 1:
            escalate_reasons.append(f"files={len(files_changed)} (max 1)")
    elif isinstance(files_changed, str) and "," in files_changed:
        escalate_reasons.append("multiple files specified")

    # 함수 수 체크
    func_changed = scope.get("function")
    if isinstance(func_changed, list):
        if len(func_changed) > 1:
            escalate_reasons.append(f"functions={len(func_changed)} (max 1)")

    # trust_root 체크
    if forbidden.get("trust_root_changes"):
        escalate_reasons.append("trust_root_changes=true")

    # 줄 수 체크
    max_lines = scope.get("expected_lines_changed_max")
    if max_lines is not None:
        try:
            if int(max_lines) > _PATCH_LANE_AUTO_ESCALATION_LINES:
                escalate_reasons.append(
                    f"lines={max_lines} > {_PATCH_LANE_AUTO_ESCALATION_LINES}"
                )
        except (TypeError, ValueError):
            pass

    if escalate_reasons:
        return {
            "verdict": "ESCALATE",
            "lane": "full",
            "reasons": escalate_reasons,
            "message": "auto-escalation: " + "; ".join(escalate_reasons),
        }
    return {"verdict": "PASS", "lane": "patch", "reasons": [], "message": "Patch Lane audit passed"}


def _load_patch_plan(plan_file: str) -> Dict[str, Any]:
    """patch_plan.json 로드 및 기본 스키마 검증."""
    path = Path(plan_file)
    if not path.is_absolute():
        path = BASE_DIR / path
    if not path.is_file():
        _die(f"[PIPELINE ERROR] patch_plan.json 없음: {plan_file}")
    try:
        with open(path, encoding="utf-8") as f:
            plan = json.load(f)
    except json.JSONDecodeError as exc:
        _die(f"[PIPELINE ERROR] patch_plan.json JSON 파싱 실패: {exc}")
    schema_ver = plan.get("schema_version")
    if schema_ver != 1:
        _die(
            f"[PIPELINE ERROR] patch_plan.json schema_version은 1이어야 합니다. 현재: {schema_ver!r}"
        )
    return plan


# ---------------------------------------------------------------------------
# IMP-20260527-075A MT-2: budget CLI (Cost/Attempt Budget Gate)
# ---------------------------------------------------------------------------

def cmd_budget(args: "argparse.Namespace") -> None:
    """budget 서브커맨드 라우터.

    서브커맨드:
      status -- phase별 attempt budget 현황 한국어 출력
      reset  -- 특정 phase의 attempts 초기화 (--reason 필수)
    """
    sub = getattr(args, "budget_sub", None)
    if sub == "status":
        _cmd_budget_status(args)
    elif sub == "reset":
        _cmd_budget_reset(args)
    else:
        _die(f"[BUDGET ERROR] 알 수 없는 서브커맨드: {sub!r}")


def _cmd_budget_status(args: "argparse.Namespace") -> None:
    """phase별 attempt budget 현황을 한국어로 출력."""
    state = _load_state()
    if state is None:
        _die("[BUDGET] pipeline_state.json이 없습니다.")
    pid = str(state.get("pipeline_id") or "UNKNOWN")
    _ensure_attempt_budget_keys(state)
    ab = state["attempt_budget"]

    print(f"\n=== {pid} attempt budget 현황 ===\n")
    for phase in ATTEMPT_BUDGET_PHASES:
        result = _check_attempt_budget(state, phase)
        used = result["attempts_used"]
        maxn = result["max_attempts"]
        remaining = max(0, maxn - used)
        if result["blocked"]:
            status_label = "[차단됨]"
        else:
            status_label = "[정상]"
        print(f"  {phase} phase: 사용 {used}회 / 한도 {maxn}회 (남은 재시도: {remaining}회) {status_label}")

    # 반복 failure_code 요약
    print()
    repeat_found = False
    for phase in ATTEMPT_BUDGET_PHASES:
        repeat_fc = _detect_repeat_failure_code(state, phase)
        if repeat_fc:
            print(f"  반복 failure_code 감지 — {phase} phase: '{repeat_fc}' (Architect/RCA 이관 권장)")
            repeat_found = True
    if not repeat_found:
        print("  반복 failure_code: 없음")

    # 차단된 phase 요약
    blocked = ab.get("blocked_phases") or {}
    if blocked:
        print()
        print("  차단된 phase 상세:")
        for phase, info in blocked.items():
            if isinstance(info, dict):
                fc = info.get("failure_code", "?")
                print(f"    - {phase}: {fc}")

    # 보존 정책: state 저장하지 않음 (status는 read-only)
    print()
    sys.exit(0)


def _cmd_budget_reset(args: "argparse.Namespace") -> None:
    """특정 phase의 attempts 리스트와 blocked_phases 항목을 초기화.

    --phase: 초기화할 phase (dev/qa/gate)
    --reason: 초기화 사유 (감사 로그용, argparse required=True로 강제)
    """
    state = _load_state()
    if state is None:
        _die("[BUDGET] pipeline_state.json이 없습니다.")
    pid = str(state.get("pipeline_id") or "UNKNOWN")
    phase = args.phase
    reason = str(args.reason or "").strip()
    if not reason:
        _die("[BUDGET ERROR] --reason 값이 비어 있습니다. 초기화 사유를 명시하세요.")
    _ensure_attempt_budget_keys(state)
    ab = state["attempt_budget"]
    prev_attempts = len(ab["attempts"].get(phase, []))
    ab["attempts"][phase] = []
    if phase in ab["blocked_phases"]:
        del ab["blocked_phases"][phase]
    # 감사 이벤트 기록 (state.event_log에 추가)
    state.setdefault("event_log", []).append({
        "ts": _now(),
        "type": "BUDGET_RESET",
        "phase": phase,
        "previous_attempts": prev_attempts,
        "reason": reason,
    })
    _save(state)
    print(GREEN(
        f"\n[BUDGET RESET] {pid} — {phase} phase attempt budget 초기화 완료\n"
        f"  이전 attempts: {prev_attempts}회 -> 0회\n"
        f"  사유: {reason}\n"
    ))
    sys.exit(0)


def cmd_cluster(args: "argparse.Namespace") -> None:
    """cluster 서브커맨드 라우터.

    서브커맨드:
      detect  -- 현재 파이프라인과 유사한 클러스터 탐색
      init    -- 새 클러스터 생성
      status  -- 클러스터 상태 조회
      attach  -- 파이프라인을 클러스터에 연결
      close   -- 클러스터 수동 close
    """
    sub = args.cluster_sub
    if sub == "detect":
        _cmd_cluster_detect(args)
    elif sub == "init":
        _cmd_cluster_init(args)
    elif sub == "status":
        _cmd_cluster_status(args)
    elif sub == "attach":
        _cmd_cluster_attach(args)
    elif sub == "close":
        _cmd_cluster_close(args)
    else:
        _die(f"[CLUSTER ERROR] 알 수 없는 서브커맨드: {sub!r}")


def _cmd_cluster_detect(args: "argparse.Namespace") -> None:
    """현재 파이프라인과 유사한 활성 클러스터를 탐색."""
    desc = getattr(args, "desc", "") or ""
    clusters = _list_all_clusters()
    active = []
    for cl in clusters:
        # 7일 자동 close 체크
        just_closed = _cluster_check_auto_close(cl)
        if just_closed:
            print(YELLOW(f"  [AUTO-CLOSE] 클러스터 {cl['id']} — 7일 경과로 자동 close됨"))
            continue
        if cl.get("closed_at"):
            continue
        active.append(cl)

    if not active:
        result = {"match_found": False, "clusters": [], "note": "활성 클러스터 없음"}
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    # desc 키워드 매칭 (대소문자 무시)
    matched = []
    desc_lower = desc.lower()
    for cl in active:
        cl_desc = (cl.get("desc") or "").lower()
        if desc_lower and desc_lower in cl_desc:
            matched.append(cl)
        elif not desc_lower:
            matched.append(cl)

    result = {
        "match_found": len(matched) > 0,
        "clusters": [
            {
                "id": cl["id"],
                "desc": cl.get("desc", ""),
                "created_at": cl.get("created_at", ""),
                "pipelines": cl.get("pipelines", []),
                "patch_failures": cl.get("patch_failures", 0),
                "patch_lane_forbidden": cl.get("patch_lane_forbidden", False),
            }
            for cl in matched
        ],
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))


def _cmd_cluster_init(args: "argparse.Namespace") -> None:
    """새 클러스터 생성."""
    desc = getattr(args, "desc", "") or ""
    cluster_id = _cluster_id_generate()
    # 중복 방지 (매우 낮은 확률이지만 체크)
    attempts = 0
    while (_CLUSTER_DIR / f"{cluster_id}.json").exists() and attempts < 10:
        cluster_id = _cluster_id_generate()
        attempts += 1

    cluster: Dict[str, Any] = {
        "id": cluster_id,
        "desc": desc,
        "pipelines": [],
        "created_at": _now(),
        "closed_at": None,
        "patch_failures": 0,
        "patch_lane_forbidden": False,
    }
    _save_cluster_json(cluster)
    print(GREEN(f"  [CLUSTER CREATED] {cluster_id}"))
    print(json.dumps(cluster, indent=2, ensure_ascii=False))


def _cmd_cluster_status(args: "argparse.Namespace") -> None:
    """클러스터 상태 조회."""
    cluster_id = getattr(args, "cluster_id", None)
    if cluster_id:
        cl = _load_cluster_json(cluster_id)
        if cl is None:
            _die(f"[CLUSTER ERROR] 클러스터를 찾을 수 없습니다: {cluster_id}")
        # 자동 close 체크
        just_closed = _cluster_check_auto_close(cl)
        if just_closed:
            print(YELLOW(f"  [AUTO-CLOSE] {cluster_id} — 7일 경과로 자동 close됨"))
        print(json.dumps(cl, indent=2, ensure_ascii=False))
    else:
        # 전체 목록
        clusters = _list_all_clusters()
        summary = []
        for cl in clusters:
            just_closed = _cluster_check_auto_close(cl)
            if just_closed:
                print(YELLOW(f"  [AUTO-CLOSE] {cl['id']} — 7일 경과로 자동 close됨"))
            summary.append({
                "id": cl["id"],
                "desc": cl.get("desc", ""),
                "closed": bool(cl.get("closed_at")),
                "patch_failures": cl.get("patch_failures", 0),
                "patch_lane_forbidden": cl.get("patch_lane_forbidden", False),
                "pipelines_count": len(cl.get("pipelines", [])),
            })
        print(json.dumps({"clusters": summary, "total": len(summary)}, indent=2, ensure_ascii=False))


def _cmd_cluster_attach(args: "argparse.Namespace") -> None:
    """현재 파이프라인을 클러스터에 연결."""
    cluster_id = getattr(args, "cluster_id", None)
    if not cluster_id:
        _die("[CLUSTER ERROR] --cluster-id 필수")
    cl = _load_cluster_json(cluster_id)
    if cl is None:
        _die(f"[CLUSTER ERROR] 클러스터를 찾을 수 없습니다: {cluster_id}")
    if cl.get("closed_at"):
        _die(f"[CLUSTER ERROR] 닫힌 클러스터에 연결할 수 없습니다: {cluster_id}")
    just_closed = _cluster_check_auto_close(cl)
    if just_closed:
        _die(f"[CLUSTER ERROR] 클러스터가 7일 경과로 자동 close됨: {cluster_id}")

    state = _load_state()
    pipeline_id = str(state.get("pipeline_id") or "") if state is not None else ""
    if pipeline_id and pipeline_id not in cl.get("pipelines", []):
        cl.setdefault("pipelines", []).append(pipeline_id)
        _save_cluster_json(cl)
    print(GREEN(f"  [CLUSTER ATTACHED] pipeline={pipeline_id} -> cluster={cluster_id}"))
    print(json.dumps({"cluster_id": cluster_id, "pipeline_id": pipeline_id}, indent=2, ensure_ascii=False))


def _cmd_cluster_close(args: "argparse.Namespace") -> None:
    """클러스터 수동 close."""
    cluster_id = getattr(args, "cluster_id", None)
    if not cluster_id:
        _die("[CLUSTER ERROR] --cluster-id 필수")
    cl = _load_cluster_json(cluster_id)
    if cl is None:
        _die(f"[CLUSTER ERROR] 클러스터를 찾을 수 없습니다: {cluster_id}")
    if cl.get("closed_at"):
        print(YELLOW(f"  [CLUSTER] {cluster_id} 이미 닫혀 있습니다 (closed_at={cl['closed_at']})"))
        return
    cl["closed_at"] = _now()
    _save_cluster_json(cl)
    print(GREEN(f"  [CLUSTER CLOSED] {cluster_id} (closed_at={cl['closed_at']})"))
    print(json.dumps(cl, indent=2, ensure_ascii=False))


def cmd_patch(args: "argparse.Namespace") -> None:
    """patch 서브커맨드 라우터.

    서브커맨드:
      plan    -- patch_plan.json 진입 조건 검사 및 lane 확인
      audit   -- patch_plan.json 범위 감사 (auto-escalation 트리거)
      verify  -- patch 결과 검증
      attest  -- patch lane 완료 증거 기록
    """
    sub = args.patch_sub
    if sub == "plan":
        _cmd_patch_plan(args)
    elif sub == "audit":
        _cmd_patch_audit(args)
    elif sub == "verify":
        _cmd_patch_verify(args)
    elif sub == "attest":
        _cmd_patch_attest(args)
    else:
        _die(f"[PATCH ERROR] 알 수 없는 서브커맨드: {sub!r}")


def _cmd_patch_plan(args: "argparse.Namespace") -> None:
    """patch_plan.json 진입 조건 검사.
    patch_lane_forbidden 클러스터이면 즉시 오류. 조건 위반 목록 출력.
    """
    plan_file = getattr(args, "plan", None) or "patch_plan.json"
    plan = _load_patch_plan(plan_file)

    # cluster_id 체크
    cluster_id = plan.get("cluster_id")
    if cluster_id:
        cl = _load_cluster_json(cluster_id)
        if cl is not None:
            _cluster_check_auto_close(cl)
            if cl.get("patch_lane_forbidden"):
                _die(
                    f"[PIPELINE ERROR] PATCH_LANE_FORBIDDEN: 클러스터 {cluster_id}에서 "
                    "Patch Lane이 금지됩니다 (patch_failures >= 2). Full Lane을 사용하세요."
                )

    violations = _check_patch_lane_conditions(plan)
    if violations:
        result = {
            "verdict": "FAIL",
            "lane": "full",
            "violations": violations,
            "message": "Patch Lane 진입 조건 불만족 — Full Lane 필요",
        }
        print(json.dumps(result, indent=2, ensure_ascii=False))
        sys.exit(1)

    result = {
        "verdict": "PASS",
        "lane": "patch",
        "violations": [],
        "message": "Patch Lane 진입 조건 통과",
        "pipeline_id": plan.get("pipeline_id", ""),
        "cluster_id": cluster_id or "",
    }
    # patch_lane.plan_passed 상태 저장 (attest gate에서 검증)
    state = _load_state()
    if state is not None:
        state["patch_lane"] = state.get("patch_lane") or {}
        state["patch_lane"]["plan_passed"] = True
        state["patch_lane"]["plan_passed_at"] = _now()
        _save_state(state)
    print(GREEN("  [PATCH PLAN] Patch Lane 진입 조건 통과"))
    print(json.dumps(result, indent=2, ensure_ascii=False))


def _cmd_patch_audit(args: "argparse.Namespace") -> None:
    """patch audit 실행. auto-escalation 시 lane='full', exit 1."""
    plan_file = getattr(args, "plan", None) or "patch_plan.json"
    plan = _load_patch_plan(plan_file)

    # cluster_id 체크
    cluster_id = plan.get("cluster_id")
    cl = None
    if cluster_id:
        cl = _load_cluster_json(cluster_id)
        if cl is not None:
            _cluster_check_auto_close(cl)
            if cl.get("patch_lane_forbidden"):
                _die(
                    f"[PIPELINE ERROR] PATCH_LANE_FORBIDDEN: 클러스터 {cluster_id}에서 "
                    "Patch Lane이 금지됩니다. Full Lane을 사용하세요."
                )

    audit_result = _run_patch_audit(plan, cl)

    if audit_result["verdict"] == "ESCALATE":
        # pipeline_state에 lane=full 기록
        state = _load_state()
        if state is None:
            _die("[PATCH] pipeline_state.json이 없습니다.")
        state["patch_lane"] = {"lane": "full", "escalated_at": _now(), "reasons": audit_result["reasons"]}
        _save_state(state)

        print(RED("  [PATCH AUDIT] ESCALATE — Full Lane으로 전환"))
        print(json.dumps(audit_result, indent=2, ensure_ascii=False))
        sys.exit(1)

    # patch_lane.audit_passed 상태 저장 (attest gate에서 검증)
    state = _load_state()
    if state is not None:
        state["patch_lane"] = state.get("patch_lane") or {}
        state["patch_lane"]["audit_passed"] = True
        state["patch_lane"]["audit_passed_at"] = _now()
        _save_state(state)
    print(GREEN("  [PATCH AUDIT] PASS — Patch Lane 감사 통과"))
    print(json.dumps(audit_result, indent=2, ensure_ascii=False))


def _cmd_patch_verify(args: "argparse.Namespace") -> None:
    """patch 결과 검증. --result PASS|FAIL + --plan으로 cluster 실패 누적.
    PASS 시에는 --test-command 또는 --evidence-file 중 하나가 필수입니다.
    """
    plan_file = getattr(args, "plan", None) or "patch_plan.json"
    result_val = (getattr(args, "result", None) or "").strip().upper()
    if result_val not in ("PASS", "FAIL"):
        _die("[PATCH ERROR] --result PASS 또는 --result FAIL 필수")

    # PASS 시 증거 필수 (Item 7)
    if result_val == "PASS":
        test_cmd = getattr(args, "test_command", None) or ""
        evidence_file = getattr(args, "evidence_file", None) or ""
        if not test_cmd.strip() and not evidence_file.strip():
            _die(
                "[PATCH ERROR] patch verify --result PASS에는 --test-command 또는 "
                "--evidence-file 중 하나가 필수입니다."
            )

    plan = _load_patch_plan(plan_file)
    cluster_id = plan.get("cluster_id")

    if result_val == "FAIL" and cluster_id:
        cl = _load_cluster_json(cluster_id)
        if cl is not None and not cl.get("closed_at"):
            cl["patch_failures"] = int(cl.get("patch_failures", 0)) + 1
            if cl["patch_failures"] >= _CLUSTER_MAX_PATCH_FAILURES:
                cl["patch_lane_forbidden"] = True
                print(RED(
                    f"  [PATCH VERIFY] 클러스터 {cluster_id} patch_failures={cl['patch_failures']} "
                    f"— PATCH_LANE_FORBIDDEN 설정됨"
                ))
            _save_cluster_json(cl)

    # verify_passed 상태 저장 (attest gate에서 검증)
    if result_val == "PASS":
        state = _load_state()
        if state is None:
            state = {}
        state["patch_lane"] = state.get("patch_lane") or {}
        state["patch_lane"]["verify_passed"] = True
        state["patch_lane"]["verify_passed_at"] = _now()
        # 증거 기록
        test_cmd = getattr(args, "test_command", None) or ""
        evidence_file = getattr(args, "evidence_file", None) or ""
        if test_cmd.strip():
            state["patch_lane"]["verify_test_command"] = test_cmd.strip()
        if evidence_file.strip():
            state["patch_lane"]["verify_evidence_file"] = evidence_file.strip()
        _save_state(state)

    verdict_color = GREEN if result_val == "PASS" else RED
    print(verdict_color(f"  [PATCH VERIFY] {result_val}"))
    print(json.dumps({
        "verdict": result_val,
        "cluster_id": cluster_id or "",
        "pipeline_id": plan.get("pipeline_id", ""),
    }, indent=2, ensure_ascii=False))

    if result_val == "FAIL":
        sys.exit(1)


def _cmd_patch_attest(args: "argparse.Namespace") -> None:
    """patch lane 완료 증거 기록.
    plan PASS + audit PASS + verify PASS 없이는 실패합니다 (Item 6).
    """
    plan_file = getattr(args, "plan", None) or "patch_plan.json"
    plan = _load_patch_plan(plan_file)

    state = _load_state()
    if state is None:
        state = {}
    pl = state.get("patch_lane") or {}

    # Item 6: 세 단계 모두 PASS 확인
    missing = []
    if not pl.get("plan_passed"):
        missing.append("plan (patch plan --plan <file> 실행 필요)")
    if not pl.get("audit_passed"):
        missing.append("audit (patch audit --plan <file> 실행 필요)")
    if not pl.get("verify_passed"):
        missing.append("verify (patch verify --result PASS ... 실행 필요)")

    if missing:
        _die(
            "[PATCH ERROR] patch attest는 아래 단계 완료 후에만 실행할 수 있습니다:\n"
            + "\n".join(f"  - {m}" for m in missing)
        )

    state["patch_lane"] = pl
    state["patch_lane"]["attested_at"] = _now()
    state["patch_lane"]["lane"] = plan.get("lane", "patch")
    state["patch_lane"]["pipeline_id"] = plan.get("pipeline_id", "")
    state["patch_lane"]["cluster_id"] = plan.get("cluster_id", "")
    _save_state(state)

    print(GREEN("  [PATCH ATTEST] Patch Lane 완료 증거 기록됨"))
    print(json.dumps(state["patch_lane"], indent=2, ensure_ascii=False))


# ---------------------------------------------------------------------------
# IMP-20260522-0C83: Pipeline Observability & Cycle Time Metrics (MT-1)
# ---------------------------------------------------------------------------

def _phase_elapsed_summary(state: Dict[str, Any]) -> Dict[str, Any]:
    """phase별 started_at/completed_at에서 elapsed 계산. 값 없으면 '확인 불가' 반환."""
    phases_data = state.get("phases", {})
    result: Dict[str, Any] = {}
    for phase_name, phase_info in phases_data.items():
        if not isinstance(phase_info, dict):
            continue
        started = phase_info.get("started_at")
        completed = phase_info.get("completed_at")
        entry: Dict[str, Any] = {
            "started_at": started,
            "completed_at": completed,
        }
        if not started or not completed:
            entry["elapsed_seconds"] = "확인 불가"
            entry["elapsed_human"] = "확인 불가"
            if not started and not completed:
                entry["reason"] = "started_at 없음, completed_at 없음"
            elif not started:
                entry["reason"] = "started_at 없음"
            else:
                entry["reason"] = "completed_at 없음"
        else:
            try:
                from datetime import datetime, timezone
                fmt = "%Y-%m-%dT%H:%M:%SZ"
                t_start = datetime.strptime(started, fmt).replace(tzinfo=timezone.utc)
                t_end = datetime.strptime(completed, fmt).replace(tzinfo=timezone.utc)
                elapsed_sec = int((t_end - t_start).total_seconds())
                entry["elapsed_seconds"] = elapsed_sec
                hours, remainder = divmod(elapsed_sec, 3600)
                minutes, seconds = divmod(remainder, 60)
                if hours > 0:
                    entry["elapsed_human"] = f"{hours}시간 {minutes}분 {seconds}초"
                elif minutes > 0:
                    entry["elapsed_human"] = f"{minutes}분 {seconds}초"
                else:
                    entry["elapsed_human"] = f"{seconds}초"
            except (ValueError, TypeError):
                entry["elapsed_seconds"] = "확인 불가"
                entry["elapsed_human"] = "확인 불가"
                entry["reason"] = "타임스탬프 파싱 오류"
        result[phase_name] = entry
    return result


def _gate_elapsed_summary(state: Dict[str, Any]) -> Dict[str, Any]:
    """gate별 상태/소요시간. 없으면 '확인 불가'."""
    gates_data = state.get("external_gates", {})
    result: Dict[str, Any] = {}
    for gate_name, gate_info in gates_data.items():
        if not isinstance(gate_info, dict):
            continue
        gstatus = gate_info.get("status", "확인 불가")
        started = gate_info.get("started_at")
        completed = gate_info.get("completed_at")
        entry: Dict[str, Any] = {
            "status": gstatus,
            "started_at": started,
            "completed_at": completed,
        }
        if not started or not completed:
            entry["elapsed_seconds"] = "확인 불가"
            entry["elapsed_human"] = "확인 불가"
        else:
            try:
                from datetime import datetime, timezone
                fmt = "%Y-%m-%dT%H:%M:%SZ"
                t_start = datetime.strptime(started, fmt).replace(tzinfo=timezone.utc)
                t_end = datetime.strptime(completed, fmt).replace(tzinfo=timezone.utc)
                elapsed_sec = int((t_end - t_start).total_seconds())
                entry["elapsed_seconds"] = elapsed_sec
                hours, remainder = divmod(elapsed_sec, 3600)
                minutes, seconds = divmod(remainder, 60)
                if hours > 0:
                    entry["elapsed_human"] = f"{hours}시간 {minutes}분 {seconds}초"
                elif minutes > 0:
                    entry["elapsed_human"] = f"{minutes}분 {seconds}초"
                else:
                    entry["elapsed_human"] = f"{seconds}초"
            except (ValueError, TypeError):
                entry["elapsed_seconds"] = "확인 불가"
                entry["elapsed_human"] = "확인 불가"
        result[gate_name] = entry
    return result


# ── IMP-20260526-82E3 Observability Metrics Gate (MT-1~MT-4) ─────────────────


def _parse_iso8601_z(ts: str) -> "datetime":
    """ISO8601 'Z' 시각 문자열을 timezone-aware datetime으로 변환.

    'YYYY-MM-DDTHH:MM:SSZ' 또는 'YYYY-MM-DDTHH:MM:SS+00:00' 형식을 지원한다.
    IMP-20260526-82E3 MT-1.
    """
    from datetime import datetime
    if not isinstance(ts, str) or not ts:
        raise ValueError("타임스탬프가 비어 있습니다.")
    cleaned = ts.replace("Z", "+00:00")
    return datetime.fromisoformat(cleaned)


def _elapsed_seconds(start: str, end: str) -> int:
    """ISO8601 시작/종료 시각으로부터 elapsed_seconds(정수)를 계산.

    음수가 발생하면 0으로 보정(잘못된 입력 방어).
    IMP-20260526-82E3 MT-1.
    """
    t_start = _parse_iso8601_z(start)
    t_end = _parse_iso8601_z(end)
    delta = (t_end - t_start).total_seconds()
    if delta < 0:
        return 0
    return int(delta)


def _record_phase_timing(phase: str, start: str, end: str) -> Dict[str, Any]:
    """Phase별 elapsed_seconds 기록용 순수 함수.

    입력:
      phase: phase 이름 (예: "dev", "qa")
      start, end: ISO8601 'Z' 시각 문자열

    출력:
      {"phase": phase, "elapsed_seconds": int, "status": "recorded"}

    Oracle TC-normal-phase-timing 사양 매칭.
    IMP-20260526-82E3 MT-1.
    """
    return {
        "phase": phase,
        "elapsed_seconds": _elapsed_seconds(start, end),
        "status": "recorded",
    }


def _record_gate_timing(gate: str, start: str, end: str) -> Dict[str, Any]:
    """Gate별 elapsed_seconds 기록용 순수 함수.

    입력:
      gate: gate 이름 (예: "technical", "oracle")
      start, end: ISO8601 'Z' 시각 문자열

    출력:
      {"gate": gate, "elapsed_seconds": int, "status": "recorded"}

    Oracle TC-normal-gate-timing 사양 매칭.
    IMP-20260526-82E3 MT-1.
    """
    return {
        "gate": gate,
        "elapsed_seconds": _elapsed_seconds(start, end),
        "status": "recorded",
    }


# IMP-20260526-82E3 MT-2: GitHub Actions 5상태 표준 키
_GITHUB_ACTIONS_STATES: tuple = (
    "WAITING_FOR_TRIGGER",
    "QUEUED",
    "IN_PROGRESS",
    "COMPLETED",
    "TIMEOUT",
)


def _aggregate_github_actions_state_durations(
    transitions: Optional[List[Dict[str, Any]]],
) -> Dict[str, int]:
    """GitHub Actions 상태 전이 리스트로부터 5상태 누적 시간 dict를 계산.

    입력:
      transitions: [{"state": "...", "duration": int 초}] 형태의 리스트.
        state는 _GITHUB_ACTIONS_STATES 중 하나여야 한다(아니면 무시).
        duration은 정수(초). 음수/잘못된 타입은 0으로 처리.

    출력:
      {"WAITING_FOR_TRIGGER":int, "QUEUED":int, "IN_PROGRESS":int,
       "COMPLETED":int, "TIMEOUT":int}
      누락된 상태 키는 0 유지.

    오라클 사양: TC-normal-github-actions-timing.
    IMP-20260526-82E3 MT-2.
    """
    result: Dict[str, int] = {key: 0 for key in _GITHUB_ACTIONS_STATES}
    if not transitions:
        return result
    if not isinstance(transitions, list):
        return result
    for item in transitions:
        if not isinstance(item, dict):
            continue
        state_name = item.get("state")
        if state_name not in _GITHUB_ACTIONS_STATES:
            continue
        duration = item.get("duration")
        if isinstance(duration, bool):  # bool은 int 서브타입이지만 의미상 부적합
            continue
        if not isinstance(duration, int):
            continue
        if duration < 0:
            continue
        result[str(state_name)] += duration
    return result


def _compute_failure_summary_from_list(
    failures: Optional[List[Dict[str, Any]]],
    *,
    repeat_threshold: int = 2,
) -> Dict[str, Dict[str, Any]]:
    """실패 리스트로부터 failure_code별 {count, is_repeat} 요약을 계산.

    입력:
      failures: [{"code": "..."}, ...] 형태 리스트.
        code 키가 없거나 빈 항목은 무시.
      repeat_threshold: is_repeat=True로 표시할 최소 반복 횟수 (기본 2회).

    출력:
      {code: {"count": int, "is_repeat": bool}, ...}
      누락/빈 입력이면 빈 dict 반환.

    오라클 사양: TC-normal-failure-summary.
    IMP-20260526-82E3 MT-3.
    """
    result: Dict[str, Dict[str, Any]] = {}
    if not failures or not isinstance(failures, list):
        return result
    counts: Dict[str, int] = {}
    for item in failures:
        if not isinstance(item, dict):
            continue
        code = item.get("code") or item.get("failure_code")
        if not code or not isinstance(code, str):
            continue
        counts[code] = counts.get(code, 0) + 1
    for code, cnt in counts.items():
        result[code] = {
            "count": cnt,
            "is_repeat": cnt >= repeat_threshold,
        }
    return result


def _failure_retry_summary(state: Dict[str, Any]) -> Dict[str, Any]:
    """failure_packet 리스트에서 code별 카운트, return_phase 분포 집계."""
    pid = str(state.get("pipeline_id") or "UNKNOWN")
    paths = _contract_paths(pid)
    failure_root = _failure_root_from_paths(paths)
    packets: List[Dict[str, Any]] = []
    if failure_root.exists():
        for fp in failure_root.glob("*.json"):
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    packets.append(data)
            except (json.JSONDecodeError, OSError):
                pass
    total = len(packets)
    code_counts: Dict[str, int] = {}
    return_phase_dist: Dict[str, int] = {}
    for pkt in packets:
        fc = pkt.get("failure_code") or pkt.get("gate_name") or "unknown"
        code_counts[fc] = code_counts.get(fc, 0) + 1
        rp = pkt.get("return_phase") or "unknown"
        return_phase_dist[rp] = return_phase_dist.get(rp, 0) + 1
    most_repeated: Optional[str] = None
    most_repeated_count: int = 0
    if code_counts:
        most_repeated = max(code_counts, key=lambda k: code_counts[k])
        most_repeated_count = code_counts[most_repeated]
    result: Dict[str, Any] = {
        "total_failure_packets": total,
        "failure_code_counts": code_counts,
        "return_phase_distribution": return_phase_dist,
    }
    if most_repeated:
        result["most_repeated_failure_code"] = most_repeated
        result["most_repeated_failure_code_count"] = most_repeated_count
    return result


def _github_actions_duration_summary(repo: Optional[str], run_id: Optional[str]) -> Dict[str, Any]:
    """GitHub Actions API 조회. 실패 시 '확인 불가' (추정값 금지)."""
    unavailable: Dict[str, Any] = {
        "status": "확인 불가",
        "run_id": "확인 불가",
        "url": "확인 불가",
        "conclusion": "확인 불가",
        "started_at": "확인 불가",
        "completed_at": "확인 불가",
        "duration_seconds": "확인 불가",
        "duration_human": "확인 불가",
        "elapsed_seconds": "확인 불가",
        "elapsed_human": "확인 불가",
        "commit_sha": "확인 불가",
        "workflow_name": "확인 불가",
        "unavailable_reason": "GitHub Actions API 조회 실패",
    }
    if not repo or not run_id:
        return unavailable
    try:
        import subprocess
        result = subprocess.run(
            ["gh", "run", "view", str(run_id), "--repo", repo, "--json",
             "status,conclusion,createdAt,updatedAt,url,databaseId"],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode != 0:
            return unavailable
        data = json.loads(result.stdout)
        started = data.get("createdAt")
        completed = data.get("updatedAt")
        elapsed_sec: Any = "확인 불가"
        elapsed_human: Any = "확인 불가"
        if started and completed:
            try:
                from datetime import datetime
                t_start = datetime.fromisoformat(started.replace("Z", "+00:00"))
                t_end = datetime.fromisoformat(completed.replace("Z", "+00:00"))
                elapsed_sec = int((t_end - t_start).total_seconds())
                hours, remainder = divmod(elapsed_sec, 3600)
                minutes, seconds = divmod(remainder, 60)
                if hours > 0:
                    elapsed_human = f"{hours}시간 {minutes}분 {seconds}초"
                elif minutes > 0:
                    elapsed_human = f"{minutes}분 {seconds}초"
                else:
                    elapsed_human = f"{seconds}초"
            except (ValueError, TypeError):
                pass
        metrics: Dict[str, Any] = {
            "status": data.get("status", "확인 불가"),
            "run_id": str(data.get("databaseId", run_id)),
            "url": data.get("url", "확인 불가"),
            "conclusion": data.get("conclusion", "확인 불가"),
            "started_at": started or "확인 불가",
            "completed_at": completed or "확인 불가",
            "duration_seconds": elapsed_sec,
            "duration_human": elapsed_human,
            "elapsed_seconds": elapsed_sec,
            "elapsed_human": elapsed_human,
            "commit_sha": data.get("headSha", "확인 불가") or "확인 불가",
            "workflow_name": data.get("workflowName", "확인 불가") or "확인 불가",
        }
        return metrics
    except Exception:
        return unavailable


def _agent_session_metrics_summary(state: Dict[str, Any]) -> Dict[str, Any]:
    """receipt에서 agent_id/run_id/elapsed/tokens 읽기. token 없으면 unavailable."""
    agent_runs = state.get("agent_runs", {})
    result: Dict[str, Any] = {}
    for run_id, run_info in agent_runs.items():
        if not isinstance(run_info, dict):
            continue
        entry: Dict[str, Any] = {
            "agent_id": run_info.get("agent_id", "확인 불가"),
            "phase": run_info.get("phase", "확인 불가"),
            "status": run_info.get("status", "확인 불가"),
            "started_at": run_info.get("started_at", "확인 불가"),
            "completed_at": run_info.get("completed_at", "확인 불가"),
            "tokens_used": "unavailable",
        }
        started = run_info.get("started_at")
        completed = run_info.get("completed_at")
        if started and completed:
            try:
                from datetime import datetime, timezone
                fmt = "%Y-%m-%dT%H:%M:%SZ"
                t_start = datetime.strptime(started, fmt).replace(tzinfo=timezone.utc)
                t_end = datetime.strptime(completed, fmt).replace(tzinfo=timezone.utc)
                elapsed_sec = int((t_end - t_start).total_seconds())
                hours, remainder = divmod(elapsed_sec, 3600)
                minutes, seconds = divmod(remainder, 60)
                if hours > 0:
                    entry["elapsed_human"] = f"{hours}시간 {minutes}분 {seconds}초"
                elif minutes > 0:
                    entry["elapsed_human"] = f"{minutes}분 {seconds}초"
                else:
                    entry["elapsed_human"] = f"{seconds}초"
                entry["elapsed_seconds"] = elapsed_sec
            except (ValueError, TypeError):
                entry["elapsed_seconds"] = "확인 불가"
                entry["elapsed_human"] = "확인 불가"
        else:
            entry["elapsed_seconds"] = "확인 불가"
            entry["elapsed_human"] = "확인 불가"
        result[run_id] = entry
    return result


def _format_metrics_summary_ko(metrics: Dict[str, Any]) -> str:
    """한국어 요약 문자열 반환. 6개 필수 섹션 포함."""
    lines: List[str] = []
    pid = metrics.get("pipeline_id", "확인 불가")
    lines.append(f"=== 파이프라인 metrics 요약 [{pid}] ===")
    lines.append("")
    note = metrics.get("note")
    if note:
        lines.append(f"※ {note}")
        lines.append("")

    # 섹션 1: 전체 소요 시간
    lines.append("[ 전체 소요 시간 ]")
    total = metrics.get("total_elapsed", {})
    if isinstance(total, dict):
        t_started = total.get("started_at", "확인 불가")
        t_completed = total.get("completed_at", "확인 불가")
        t_elapsed = total.get("elapsed_human", "확인 불가")
        lines.append(f"  시작: {t_started}")
        lines.append(f"  종료: {t_completed}")
        lines.append(f"  소요: {t_elapsed}")
    else:
        lines.append("  확인 불가")
    lines.append("")

    # 섹션 2: Phase별 소요 시간
    lines.append("[ Phase별 소요 시간 ]")
    phase_elapsed = metrics.get("phase_elapsed", {})
    if isinstance(phase_elapsed, dict) and phase_elapsed:
        for pname, pdata in phase_elapsed.items():
            if isinstance(pdata, dict):
                elapsed = pdata.get("elapsed_human", "확인 불가")
                lines.append(f"  {pname}: {elapsed}")
    else:
        lines.append("  확인 불가")
    lines.append("")

    # 섹션 3: Gate별 상태 및 소요 시간
    lines.append("[ Gate별 상태 및 소요 시간 ]")
    gate_elapsed = metrics.get("gate_elapsed", {})
    if isinstance(gate_elapsed, dict) and gate_elapsed:
        for gname, gdata in gate_elapsed.items():
            if isinstance(gdata, dict):
                gstatus = gdata.get("status", "확인 불가")
                gelapsed = gdata.get("elapsed_human", "확인 불가")
                lines.append(f"  {gname}: {gstatus} ({gelapsed})")
    else:
        lines.append("  확인 불가")
    lines.append("")

    # 섹션 4: 실패/재시도 요약
    lines.append("[ 실패/재시도 요약 ]")
    failure_summary = metrics.get("failure_retry", {})
    if isinstance(failure_summary, dict):
        total_fp = failure_summary.get("total_failure_packets", 0)
        lines.append(f"  총 실패 패킷: {total_fp}건")
        code_counts = failure_summary.get("failure_code_counts", {})
        if code_counts:
            for code, cnt in code_counts.items():
                lines.append(f"    {code}: {cnt}건")
        rp_dist = failure_summary.get("return_phase_distribution", {})
        if rp_dist:
            lines.append("  return_phase 분포:")
            for rp, cnt in rp_dist.items():
                lines.append(f"    {rp}: {cnt}건")
    else:
        lines.append("  확인 불가")
    lines.append("")

    # 섹션 5: GitHub Actions 요약
    lines.append("[ GitHub Actions 요약 ]")
    gh_summary = metrics.get("github_actions", {})
    if isinstance(gh_summary, dict):
        run_id = gh_summary.get("run_id", "확인 불가")
        conclusion = gh_summary.get("conclusion", "확인 불가")
        elapsed = gh_summary.get("elapsed_human", "확인 불가")
        url = gh_summary.get("url", "확인 불가")
        lines.append(f"  run_id: {run_id}")
        lines.append(f"  결과: {conclusion}")
        lines.append(f"  소요: {elapsed}")
        lines.append(f"  URL: {url}")
    else:
        lines.append("  확인 불가")
    lines.append("")

    # 섹션 6: 병목 요약
    lines.append("[ 병목 요약 ]")
    bottleneck = metrics.get("bottleneck", {})
    if isinstance(bottleneck, dict) and bottleneck:
        longest_phase = bottleneck.get("longest_phase", "확인 불가")
        longest_elapsed = bottleneck.get("longest_phase_elapsed_human", "확인 불가")
        lines.append(f"  가장 오래 걸린 Phase: {longest_phase} ({longest_elapsed})")
        most_failures = bottleneck.get("most_failed_gate", "없음")
        lines.append(f"  가장 많이 실패한 Gate: {most_failures}")
    else:
        lines.append("  확인 불가")
    lines.append("")

    # IMP-20260522-29C1 MT-4: 섹션 7 — 에이전트/세션별 소요 시간
    lines.append("[ 에이전트/세션별 소요 시간 ]")
    agent_sessions = metrics.get("agent_sessions", {})
    if isinstance(agent_sessions, dict) and agent_sessions:
        for _run_id, session in agent_sessions.items():
            if isinstance(session, dict):
                agent_id = session.get("agent_id", "확인 불가")
                elapsed = session.get("elapsed_human", "확인 불가")
                lines.append(f"  {agent_id}: {elapsed}")
    lines.append("  (토큰 사용량: 확인 불가)")
    lines.append("")

    return "\n".join(lines)


def _sanitized_metrics_unavailable(note: str) -> Dict[str, Any]:
    """state가 없거나 TMP-* pipeline_id일 때 안전한 확인 불가 metrics 반환."""
    return {
        "pipeline_id": "확인 불가",
        "collected_at": _now(),
        "note": note,
        "total_elapsed": {
            "started_at": "확인 불가",
            "completed_at": "확인 불가",
            "elapsed_seconds": "확인 불가",
            "elapsed_human": "확인 불가",
        },
        "phase_elapsed": {},
        "gate_elapsed": {},
        "failure_retry": {"total_failure_packets": 0, "failure_code_counts": {}, "return_phase_distribution": {}},
        "github_actions": {},
        "agent_sessions": {},
        "bottleneck": {},
    }


def _collect_pipeline_metrics(
    state: Optional[Dict[str, Any]],
    repo: Optional[str] = None,
    pr: Optional[int] = None,
) -> Dict[str, Any]:
    """전체 메트릭 수집 entry point."""
    if state is None:
        return _sanitized_metrics_unavailable(
            "pipeline_state.json 없음 — CI 환경에서 실제 pipeline_state를 사용할 수 없습니다."
        )
    pid = str(state.get("pipeline_id") or "UNKNOWN")
    if pid.startswith("TMP-"):
        return _sanitized_metrics_unavailable(
            "임시 pipeline — CI 환경에서 실제 pipeline_state를 사용할 수 없습니다."
        )

    # phase elapsed
    phase_elapsed = _phase_elapsed_summary(state)

    # gate elapsed
    gate_elapsed = _gate_elapsed_summary(state)

    # failure retry
    failure_retry = _failure_retry_summary(state)

    # github actions (latest phase CI run_id 조회 시도)
    run_id_for_gh: Optional[str] = None
    phase_ci_data = state.get("phase_attestations", {})
    if isinstance(phase_ci_data, dict):
        for _ph, att_data in phase_ci_data.items():
            if isinstance(att_data, dict):
                ci_run = att_data.get("ci_run_id")
                if ci_run:
                    run_id_for_gh = str(ci_run)
    github_actions = _github_actions_duration_summary(repo, run_id_for_gh)

    # agent session
    agent_sessions = _agent_session_metrics_summary(state)

    # total elapsed (pipeline start → completion)
    # IMP-20260522-29C1 MT-1: 명시적 lifecycle 필드를 우선 사용하고,
    # 없으면 기존 created_at/updated_at로 fallback (구버전 state 호환).
    pipeline_created = state.get("pipeline_started_at") or state.get("created_at")
    pipeline_completed = state.get("pipeline_completed_at") or state.get("updated_at")
    total_elapsed: Dict[str, Any] = {
        "started_at": pipeline_created or "확인 불가",
        "completed_at": pipeline_completed or "확인 불가",
    }
    if pipeline_created and pipeline_completed:
        try:
            from datetime import datetime, timezone
            fmt = "%Y-%m-%dT%H:%M:%SZ"
            t_start = datetime.strptime(pipeline_created, fmt).replace(tzinfo=timezone.utc)
            t_end = datetime.strptime(pipeline_completed, fmt).replace(tzinfo=timezone.utc)
            elapsed_sec = int((t_end - t_start).total_seconds())
            total_elapsed["elapsed_seconds"] = elapsed_sec
            hours, remainder = divmod(elapsed_sec, 3600)
            minutes, seconds = divmod(remainder, 60)
            if hours > 0:
                total_elapsed["elapsed_human"] = f"{hours}시간 {minutes}분 {seconds}초"
            elif minutes > 0:
                total_elapsed["elapsed_human"] = f"{minutes}분 {seconds}초"
            else:
                total_elapsed["elapsed_human"] = f"{seconds}초"
        except (ValueError, TypeError):
            total_elapsed["elapsed_seconds"] = "확인 불가"
            total_elapsed["elapsed_human"] = "확인 불가"
    else:
        total_elapsed["elapsed_seconds"] = "확인 불가"
        total_elapsed["elapsed_human"] = "확인 불가"

    # bottleneck 계산
    longest_phase: Optional[str] = None
    longest_seconds: int = -1
    for pname, pdata in phase_elapsed.items():
        if isinstance(pdata, dict):
            es = pdata.get("elapsed_seconds")
            if isinstance(es, int) and es > longest_seconds:
                longest_seconds = es
                longest_phase = pname

    failure_code_counts = failure_retry.get("failure_code_counts", {})
    most_failed_gate: Optional[str] = None
    if failure_code_counts:
        most_failed_gate = max(failure_code_counts, key=lambda k: failure_code_counts[k])

    bottleneck: Dict[str, Any] = {}
    if longest_phase:
        bottleneck["longest_phase"] = longest_phase
        # get elapsed_human for that phase
        lph_data = phase_elapsed.get(longest_phase, {})
        bottleneck["longest_phase_elapsed_human"] = (
            lph_data.get("elapsed_human", "확인 불가") if isinstance(lph_data, dict) else "확인 불가"
        )
    if most_failed_gate:
        bottleneck["most_failed_gate"] = most_failed_gate
    else:
        bottleneck["most_failed_gate"] = "없음"

    return {
        "pipeline_id": pid,
        "collected_at": _now(),
        "total_elapsed": total_elapsed,
        "phase_elapsed": phase_elapsed,
        "gate_elapsed": gate_elapsed,
        "failure_retry": failure_retry,
        "github_actions": github_actions,
        "agent_sessions": agent_sessions,
        "bottleneck": bottleneck,
    }


def _format_metrics_report_json(state: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """state로부터 metrics report --json 출력용 dict를 계산.

    IMP-20260526-82E3 MT-4. 오라클 사양:
      - 입력에 phase_timings에 elapsed_seconds 정보가 있으면 longest_phase 계산
      - github_actions_timings의 IN_PROGRESS 합계를 github_actions_wait_seconds로 노출
      - pipeline_id를 그대로 보존
      - 불확실한 값(null/빈 dict)은 "확인 불가"로 표시 (0초 속임 금지)

    출력 키:
      pipeline_id, longest_phase, github_actions_wait_seconds,
      total_elapsed_display, longest_phase_display,
      failure_summary, phase_timings, gate_timings, github_actions_timings,
      collected_at

    IMP-20260526-82E3 REJECT fix: 합성 필드(phase_timings/failure_summary 등)를
    직접 state에서 읽는 대신 _collect_pipeline_metrics(state)를 통해 실제 phases
    딕셔너리 기반 데이터를 수집하도록 변경.
    """
    UNAVAILABLE = "확인 불가"
    if not isinstance(state, dict):
        return {
            "pipeline_id": UNAVAILABLE,
            "longest_phase": None,
            "github_actions_wait_seconds": UNAVAILABLE,
            "total_elapsed_display": UNAVAILABLE,
            "longest_phase_display": UNAVAILABLE,
            "failure_summary": {},
            "phase_timings": {},
            "gate_timings": {},
            "github_actions_timings": {
                "WAITING_FOR_TRIGGER": 0,
                "QUEUED": 0,
                "IN_PROGRESS": 0,
                "COMPLETED": 0,
                "TIMEOUT": 0,
            },
            "collected_at": _now(),
        }

    pid = state.get("pipeline_id") or UNAVAILABLE

    # 실제 phases 딕셔너리가 있으면 _collect_pipeline_metrics를 통해 실제 데이터 수집.
    # phases 딕셔너리가 없는 경우(oracle test fixture 등)는 기존 합성 필드 방식으로 fallback.
    has_phases = isinstance(state.get("phases"), dict) and bool(state.get("phases"))

    if has_phases:
        # _collect_pipeline_metrics를 통해 실제 phases 데이터 기반으로 수집
        metrics = _collect_pipeline_metrics(state)

        # phase_timings: _collect_pipeline_metrics의 phase_elapsed 기반
        phase_elapsed = metrics.get("phase_elapsed", {})
        phase_timings: Dict[str, Any] = {}
        for pname, pdata in phase_elapsed.items():
            if isinstance(pdata, dict):
                phase_timings[pname] = {
                    "elapsed_seconds": pdata.get("elapsed_seconds"),
                    "elapsed_human": pdata.get("elapsed_human"),
                }

        # gate_timings: _collect_pipeline_metrics의 gate_elapsed 기반
        gate_elapsed = metrics.get("gate_elapsed", {})
        gate_timings: Dict[str, Any] = {}
        for gname, gdata in gate_elapsed.items():
            if isinstance(gdata, dict):
                gate_timings[gname] = {
                    "elapsed_seconds": gdata.get("elapsed_seconds"),
                    "elapsed_human": gdata.get("elapsed_human"),
                }

        # failure_summary: _collect_pipeline_metrics의 failure_retry 기반
        failure_retry = metrics.get("failure_retry", {})
        failure_summary: Dict[str, Any] = {}
        fc = failure_retry.get("failure_code_counts", {})
        repeated = failure_retry.get("repeated_failures", [])
        for code, cnt in fc.items():
            failure_summary[code] = {
                "count": cnt,
                "is_repeat": code in repeated,
            }

        # total_elapsed: _collect_pipeline_metrics의 total_elapsed 기반
        total_elapsed_info = metrics.get("total_elapsed", {})
        total_elapsed_display = total_elapsed_info.get("elapsed_human", UNAVAILABLE)
        if not total_elapsed_display or total_elapsed_display == UNAVAILABLE:
            total_elapsed_display = UNAVAILABLE

        # longest_phase: _collect_pipeline_metrics의 bottleneck 기반
        bottleneck = metrics.get("bottleneck", {})
        longest_phase_name = bottleneck.get("longest_phase")
        longest_phase_elapsed_human = bottleneck.get("longest_phase_elapsed_human", UNAVAILABLE)
        longest_phase: Optional[Dict[str, Any]] = None
        longest_phase_display: Any = UNAVAILABLE
        if longest_phase_name:
            lp_data = phase_elapsed.get(longest_phase_name, {})
            lp_secs = lp_data.get("elapsed_seconds") if isinstance(lp_data, dict) else None
            if isinstance(lp_secs, int) and not isinstance(lp_secs, bool):
                longest_phase = {"name": longest_phase_name, "elapsed_seconds": lp_secs}
            longest_phase_display = f"{longest_phase_name} ({longest_phase_elapsed_human})"
    else:
        # Fallback: phases 딕셔너리 없음 — 기존 합성 필드 방식으로 처리 (oracle test fixture 호환)
        phase_timings = state.get("phase_timings") or {}
        gate_timings = state.get("gate_timings") or {}
        failure_summary = state.get("failure_summary") or {}
        total_elapsed = state.get("total_elapsed_seconds")

        longest_phase = None
        longest_phase_display = UNAVAILABLE
        if isinstance(phase_timings, dict) and phase_timings:
            best_name: Optional[str] = None
            best_secs: int = -1
            for name, info in phase_timings.items():
                if not isinstance(info, dict):
                    continue
                secs = info.get("elapsed_seconds")
                if isinstance(secs, int) and not isinstance(secs, bool) and secs > best_secs:
                    best_secs = secs
                    best_name = str(name)
            if best_name is not None:
                longest_phase = {"name": best_name, "elapsed_seconds": best_secs}
                longest_phase_display = f"{best_name} ({best_secs}초)"

        if isinstance(total_elapsed, int) and not isinstance(total_elapsed, bool):
            total_elapsed_display = f"{total_elapsed}초"
        else:
            total_elapsed_display = UNAVAILABLE

    # github_actions_timings: 기존 state 필드 또는 기본값 (has_phases와 무관하게 동일 처리)
    gh_timings = state.get("github_actions_timings") or {
        "WAITING_FOR_TRIGGER": 0,
        "QUEUED": 0,
        "IN_PROGRESS": 0,
        "COMPLETED": 0,
        "TIMEOUT": 0,
    }

    # github_actions_wait_seconds
    gh_wait: Any
    if isinstance(gh_timings, dict) and "IN_PROGRESS" in gh_timings:
        in_progress = gh_timings.get("IN_PROGRESS")
        gh_wait = in_progress if isinstance(in_progress, int) and not isinstance(in_progress, bool) else UNAVAILABLE
    else:
        gh_wait = UNAVAILABLE

    return {
        "pipeline_id": pid,
        "longest_phase": longest_phase,
        "github_actions_wait_seconds": gh_wait,
        "total_elapsed_display": total_elapsed_display,
        "longest_phase_display": longest_phase_display,
        "failure_summary": failure_summary if isinstance(failure_summary, dict) else {},
        "phase_timings": phase_timings if isinstance(phase_timings, dict) else {},
        "gate_timings": gate_timings if isinstance(gate_timings, dict) else {},
        "github_actions_timings": gh_timings if isinstance(gh_timings, dict) else {},
        "collected_at": _now(),
    }


def _format_metrics_report_markdown(state: Optional[Dict[str, Any]]) -> str:
    """state로부터 metrics report --markdown 한국어 출력을 생성.

    IMP-20260526-82E3 MT-4. 섹션 4개 포함:
      - 전체 소요 시간
      - Phase별 소요 시간
      - GitHub Actions 대기 시간
      - 실패/재시도 요약
    누락 정보는 "확인 불가"로 표시.
    """
    report = _format_metrics_report_json(state)
    pid = report.get("pipeline_id", "확인 불가")
    lines: List[str] = []
    lines.append(f"# 파이프라인 메트릭 보고서 [{pid}]")
    lines.append("")
    lines.append("## 전체 소요 시간")
    lines.append(f"- 총 소요: {report.get('total_elapsed_display', '확인 불가')}")
    lines.append(f"- 가장 오래 걸린 Phase: {report.get('longest_phase_display', '확인 불가')}")
    lines.append("")
    lines.append("## Phase별 소요 시간")
    phase_timings = report.get("phase_timings", {})
    if isinstance(phase_timings, dict) and phase_timings:
        for name, info in phase_timings.items():
            if isinstance(info, dict):
                secs = info.get("elapsed_seconds", "확인 불가")
                elapsed_human = info.get("elapsed_human")
                if elapsed_human and elapsed_human != "확인 불가":
                    lines.append(f"- {name}: {elapsed_human}")
                elif isinstance(secs, int) and not isinstance(secs, bool):
                    lines.append(f"- {name}: {secs}초")
                else:
                    lines.append(f"- {name}: 확인 불가")
    else:
        lines.append("- 확인 불가")
    lines.append("")
    lines.append("## GitHub Actions 대기 시간")
    gh = report.get("github_actions_timings", {})
    if isinstance(gh, dict) and gh:
        for st in ("WAITING_FOR_TRIGGER", "QUEUED", "IN_PROGRESS", "COMPLETED", "TIMEOUT"):
            val = gh.get(st, "확인 불가")
            lines.append(f"- {st}: {val}초")
    else:
        lines.append("- 확인 불가")
    lines.append("")
    lines.append("## 실패/재시도 요약")
    fs = report.get("failure_summary", {})
    if isinstance(fs, dict) and fs:
        for code, info in fs.items():
            if isinstance(info, dict):
                cnt = info.get("count", "확인 불가")
                repeat = "반복" if info.get("is_repeat") else "1회"
                lines.append(f"- {code}: {cnt}회 ({repeat})")
    else:
        lines.append("- 확인 불가")
    lines.append("")
    return "\n".join(lines)


def cmd_metrics(args: "argparse.Namespace") -> None:
    """metrics collect|summary|status|report 명령 처리.

    IMP-20260526-82E3 MT-4: report --json / report --markdown 서브명령 추가.
    """
    sub = getattr(args, "metrics_sub", None)
    if sub == "collect":
        state = _load_state()
        repo = getattr(args, "repo", None)
        pr = getattr(args, "pr", None)
        output_path = getattr(args, "output", "pipeline_metrics.json")
        metrics = _collect_pipeline_metrics(state, repo=repo, pr=pr)
        out = Path(output_path)
        out.write_text(json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8")
        print(GREEN(f"  [METRICS COLLECT] 수집 완료: {out}"))
        print(json.dumps(metrics, indent=2, ensure_ascii=False))
    elif sub == "summary":
        input_path = getattr(args, "input", "pipeline_metrics.json")
        in_file = Path(input_path)
        if not in_file.exists():
            _die(f"[METRICS ERROR] 입력 파일 없음: {input_path}")
        metrics = json.loads(in_file.read_text(encoding="utf-8"))
        print(_format_metrics_summary_ko(metrics))
    elif sub == "status":
        state = _load_state()
        metrics = _collect_pipeline_metrics(state)
        print(_format_metrics_summary_ko(metrics))
    elif sub == "report":
        # IMP-20260526-82E3 MT-4: metrics report --json / --markdown
        fmt = (getattr(args, "format", None) or "json").lower()
        state_path_env = os.environ.get("PIPELINE_STATE_PATH")
        report_state: Optional[Dict[str, Any]] = None
        if state_path_env and Path(state_path_env).exists():
            try:
                report_state = json.loads(Path(state_path_env).read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                report_state = None
        else:
            report_state = _load()
        if fmt == "json":
            report = _format_metrics_report_json(report_state)
            print(json.dumps(report, ensure_ascii=False, indent=2))
        elif fmt == "markdown":
            print(_format_metrics_report_markdown(report_state))
        else:
            _die(f"[METRICS ERROR] 지원하지 않는 --format: {fmt}. json|markdown 중 선택하세요.")
    else:
        _die("[METRICS ERROR] 알 수 없는 metrics 서브명령. collect|summary|status|report 중 선택하세요.")


# ---------------------------------------------------------------------------
# IMP-20260528-0A9E: Golden Task Regression Suite (MT-2 + MT-3)
# ---------------------------------------------------------------------------
# 상수(_GOLDEN_TASKS_DIR, _GOLDEN_SCHEMA_REQUIRED_FIELDS)는 build_parser 앞에 정의됨


def _validate_golden_schema(task_data: Dict[str, Any], task_file: str) -> None:
    """golden_task.json 필수 필드 검증 — 누락 시 SystemExit(2)."""
    missing = [f for f in _GOLDEN_SCHEMA_REQUIRED_FIELDS if f not in task_data]
    if missing:
        print(f"[PIPELINE ERROR] golden_task.json 스키마 오류 — 필수 필드 누락: {missing} (파일: {task_file})")
        raise SystemExit(2)


def _load_golden_tasks(tasks_dir: str) -> List[Dict[str, Any]]:
    """tasks_dir 하위의 모든 golden_task.json을 로드하여 반환.

    각 태스크 디렉터리의 golden_task.json을 읽어 스키마 검증 후 반환합니다.
    스키마 오류 시 exit code 2 + [PIPELINE ERROR] 출력.
    """
    base = Path(tasks_dir)
    if not base.is_dir():
        print(f"[PIPELINE ERROR] golden tasks 디렉터리를 찾을 수 없습니다: {tasks_dir}")
        raise SystemExit(2)
    tasks: List[Dict[str, Any]] = []
    for task_dir in sorted(base.iterdir()):
        if not task_dir.is_dir():
            continue
        task_file = task_dir / "golden_task.json"
        if not task_file.exists():
            continue
        try:
            raw = task_file.read_text(encoding="utf-8")
            data: Dict[str, Any] = json.loads(raw)
        except (json.JSONDecodeError, OSError) as exc:
            print(f"[PIPELINE ERROR] golden_task.json 읽기 실패: {task_file} — {exc}")
            raise SystemExit(2)
        _validate_golden_schema(data, str(task_file))
        data["_task_dir"] = str(task_dir)
        tasks.append(data)
    return tasks


def _cmd_golden_list(args: "argparse.Namespace") -> None:
    """golden list — 등록된 golden task 목록을 표 형식으로 출력."""
    tasks_dir = getattr(args, "tasks_dir", _GOLDEN_TASKS_DIR)
    tasks = _load_golden_tasks(tasks_dir)
    if not tasks:
        print("  (등록된 golden task 없음)")
        return
    print(f"{'ID':<50} {'Smoke':<7} {'Return Phase'}")
    print("-" * 75)
    for t in tasks:
        smoke_flag = "true " if t.get("smoke") else "false"
        print(f"{t['id']:<50} {smoke_flag:<7} {t.get('return_phase', 'dev')}")
    print(f"\n  총 {len(tasks)}개 태스크")


def _verify_forbidden_files(
    task: Dict[str, Any],
    file_list: Optional[List[str]] = None,
) -> List[str]:
    """task의 forbidden_files 패턴에 매칭되는 파일이 있는지 확인.

    BUG-20260529-40C9 MT-2: 워크스페이스 전체 rglob 스캔 제거.
    file_list 파라미터가 제공된 경우 해당 목록만 검사합니다.
    file_list가 None이면 빈 목록으로 처리(워크스페이스 스캔 금지).

    Args:
        task: golden_task.json 내용 (forbidden_files 패턴 목록 포함)
        file_list: 검사할 파일 경로 목록. None이면 [] 처리 (스캔 없음).

    Returns: 발견된 금지 파일 목록 (빈 리스트면 위반 없음).
    """
    import fnmatch
    violations: List[str] = []
    forbidden_patterns: List[str] = task.get("forbidden_files", [])
    if not forbidden_patterns:
        return violations
    # file_list가 None이면 검사 대상 없음 — 워크스페이스 rglob 스캔 금지
    if file_list is None:
        return violations
    for entry_str in file_list:
        rel = entry_str.replace("\\", "/").strip()
        if not rel:
            continue
        entry_name = rel.rsplit("/", 1)[-1]
        for pattern in forbidden_patterns:
            if fnmatch.fnmatch(entry_name, pattern) or fnmatch.fnmatch(rel, pattern):
                violations.append(rel)
                break
    return violations


def _compare_expected_actual(task: Dict[str, Any], run_result: Dict[str, Any]) -> List[str]:
    """expected/ 디렉터리의 기대값과 실제 실행 결과를 비교.

    Returns: 불일치 항목 목록 (빈 리스트면 모두 일치).
    """
    task_dir = Path(task.get("_task_dir", ""))
    expected_dir = task_dir / "expected"
    if not expected_dir.is_dir():
        return []
    mismatches: List[str] = []
    for exp_file in sorted(expected_dir.iterdir()):
        if not exp_file.is_file() or exp_file.suffix != ".json":
            continue
        try:
            expected_data: Dict[str, Any] = json.loads(exp_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            mismatches.append(f"{exp_file.name}: 파일 읽기 실패")
            continue
        for key, exp_val in expected_data.items():
            actual_val = run_result.get(key)
            if key == "stdout_contains":
                # stdout_contains는 목록 — 각 항목이 실제 stdout에 포함되는지 확인
                stdout_text = run_result.get("stdout", "")
                items: List[str] = exp_val if isinstance(exp_val, list) else [exp_val]
                for item in items:
                    if item not in stdout_text:
                        mismatches.append(f"{exp_file.name}: stdout에 '{item}' 미포함")
            elif key == "error_contains":
                stderr_text = run_result.get("stderr", "") + run_result.get("stdout", "")
                if exp_val not in stderr_text:
                    mismatches.append(f"{exp_file.name}: 오류 메시지에 '{exp_val}' 미포함")
            elif key == "failure_reason_contains":
                fp_text = run_result.get("failure_packet_content", "")
                if exp_val not in fp_text:
                    mismatches.append(f"{exp_file.name}: failure_packet에 '{exp_val}' 미포함")
            elif actual_val != exp_val:
                mismatches.append(f"{exp_file.name}: {key} 기대={exp_val} 실제={actual_val}")
    return mismatches


def _write_golden_failure_packet(task: Dict[str, Any], mismatches: List[str], violations: List[str]) -> None:
    """FAIL 시 golden_failure_packet.json 생성."""
    packet: Dict[str, Any] = {
        "task_id": task.get("id", "unknown"),
        "result": "FAIL",
        "return_phase": task.get("return_phase", "dev"),
        "mismatches": mismatches,
        "forbidden_violations": violations,
        "recorded_at": _now(),
    }
    out_path = Path("golden_failure_packet.json")
    try:
        out_path.write_text(json.dumps(packet, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        print(f"[GOLDEN] failure_packet 기록 실패: {exc}")


def _golden_run_one(task: Dict[str, Any]) -> Dict[str, Any]:
    """단일 golden task 실행 및 검증.

    실행 순서:
      1. forbidden_files 검사 — 위반 파일 존재 시 즉시 FAIL
      2. command 실행 (subprocess)
      3. expected/ 파일과 실제 결과 비교
      4. FAIL 시 golden_failure_packet.json 생성

    Returns: {"result": "PASS"|"FAIL", "exit_code": int, "violations": [...], "mismatches": [...]}
    """
    import subprocess as _subprocess
    task_id: str = task.get("id", "unknown")

    # 1. forbidden_files 검사
    # BUG-20260529-40C9 MT-2: file_list를 input/changed_files.json에서 읽어 전달
    # (워크스페이스 rglob 스캔 제거 — 재현 불가 문제 해결)
    _file_list: Optional[List[str]] = None
    _task_dir = Path(task.get("_task_dir", ""))
    _input_cf = _task_dir / "input" / "changed_files.json"
    if _input_cf.is_file():
        try:
            _cf_data = json.loads(_input_cf.read_text(encoding="utf-8"))
            if isinstance(_cf_data.get("changed_files"), list):
                _file_list = [str(f) for f in _cf_data["changed_files"] if f]
        except (OSError, json.JSONDecodeError):
            pass
    violations = _verify_forbidden_files(task, file_list=_file_list)
    if violations:
        print(f"  [GOLDEN FAIL] {task_id} — forbidden 파일 발견: {violations[:3]}")
        run_result: Dict[str, Any] = {
            "result": "FAIL",
            "exit_code": 1,
            "stdout": "",
            "stderr": "",
            "failure_packet_written": True,
            "violations": violations,
            "mismatches": [],
        }
        _write_golden_failure_packet(task, [], violations)
        return run_result

    # 2. command 실행
    cmd: str = task.get("command", "")
    if not cmd:
        return {"result": "FAIL", "exit_code": 1, "stdout": "", "stderr": "command 없음", "violations": [], "mismatches": ["command 필드 비어 있음"]}

    try:
        proc = _subprocess.run(
            cmd.split(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        stdout_text = proc.stdout or ""
        stderr_text = proc.stderr or ""
        actual_exit = proc.returncode
    except _subprocess.TimeoutExpired:
        return {"result": "FAIL", "exit_code": 1, "stdout": "", "stderr": "TimeoutExpired", "violations": [], "mismatches": ["command 실행 타임아웃"]}
    except OSError as exc:
        return {"result": "FAIL", "exit_code": 1, "stdout": "", "stderr": str(exc), "violations": [], "mismatches": [f"command 실행 오류: {exc}"]}

    # 3. acceptance_criteria 기반 expected/ 비교
    run_result_data: Dict[str, Any] = {
        "exit_code": actual_exit,
        "stdout": stdout_text,
        "stderr": stderr_text,
        "result": "PASS" if actual_exit == 0 else "FAIL",
        "failure_packet_written": Path("golden_failure_packet.json").exists(),
        "failure_packet_content": "",
    }
    fp_path = Path("golden_failure_packet.json")
    if fp_path.exists():
        try:
            run_result_data["failure_packet_content"] = fp_path.read_text(encoding="utf-8")
        except OSError:
            pass

    mismatches = _compare_expected_actual(task, run_result_data)

    if mismatches or violations:
        run_result_data["result"] = "FAIL"
        run_result_data["failure_packet_written"] = True
        run_result_data["violations"] = violations
        run_result_data["mismatches"] = mismatches
        _write_golden_failure_packet(task, mismatches, violations)
        print(f"  [GOLDEN FAIL] {task_id} — 불일치: {mismatches[:3]}")
    else:
        run_result_data["result"] = "PASS"
        run_result_data["violations"] = []
        run_result_data["mismatches"] = []
        print(f"  [GOLDEN PASS] {task_id}")

    return run_result_data


def _cmd_golden_run(args: "argparse.Namespace") -> None:
    """golden run [--task ID | --all | --smoke] — golden task 실행."""
    tasks_dir = getattr(args, "tasks_dir", _GOLDEN_TASKS_DIR)
    all_tasks = _load_golden_tasks(tasks_dir)

    task_id = getattr(args, "task", None)
    run_all = getattr(args, "all", False)
    smoke_only = getattr(args, "smoke", False)

    if task_id:
        targets = [t for t in all_tasks if t["id"] == task_id]
        if not targets:
            print(f"[PIPELINE ERROR] golden task를 찾을 수 없습니다: {task_id}")
            raise SystemExit(2)
    elif run_all:
        targets = all_tasks
    elif smoke_only:
        targets = [t for t in all_tasks if t.get("smoke")]
    else:
        print("[PIPELINE ERROR] golden run 옵션이 필요합니다: --task ID | --all | --smoke")
        raise SystemExit(2)

    if not targets:
        print("  (실행할 golden task 없음)")
        return

    pass_count = 0
    fail_count = 0
    results: List[Dict[str, Any]] = []
    for task in targets:
        print(f"\n  [GOLDEN RUN] {task['id']}")
        result = _golden_run_one(task)
        results.append({"id": task["id"], **result})
        if result.get("result") == "PASS":
            pass_count += 1
        else:
            fail_count += 1

    print(f"\n  Golden Run 결과: PASS={pass_count} FAIL={fail_count} / 총 {len(targets)}")

    if fail_count > 0:
        raise SystemExit(1)


def cmd_golden(args: "argparse.Namespace") -> None:
    """golden list | run — Golden Task Regression Suite CLI.

    IMP-20260528-0A9E MT-2/MT-3: golden task 목록 조회 및 실행.
    """
    sub = getattr(args, "golden_sub", None)
    if sub == "list":
        _cmd_golden_list(args)
    elif sub == "run":
        _cmd_golden_run(args)
    else:
        _die("[GOLDEN ERROR] 알 수 없는 golden 서브명령. list|run 중 선택하세요.")


# ---------------------------------------------------------------------------
# IMP-20260601-0DF5 MT-1: Hygiene Scan/Archive 핵심 로직
# ---------------------------------------------------------------------------

import fnmatch as _fnmatch
import datetime as _datetime
import shutil as _shutil


def _hygiene_is_git_tracked(rel_path: str) -> bool:
    """파일이 git tracked 상태인지 확인합니다.

    Args:
        rel_path: BASE_DIR 기준 상대 경로 (슬래시 구분자 사용 가능).

    Returns:
        True이면 git이 추적하는 파일, False이면 untracked 또는 git 미사용.
    """
    import subprocess as _subprocess
    try:
        result = _subprocess.run(
            ["git", "ls-files", "--error-unmatch", rel_path],
            capture_output=True,
            cwd=str(BASE_DIR),
        )
        return result.returncode == 0
    except OSError:
        return False


def _hygiene_is_git_staged(rel_path: str) -> bool:
    """파일이 git staged(index에 추가된) 상태인지 확인합니다.

    Args:
        rel_path: BASE_DIR 기준 상대 경로.

    Returns:
        True이면 staged, False이면 아님.
    """
    import subprocess as _subprocess
    try:
        result = _subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            capture_output=True,
            text=True,
            cwd=str(BASE_DIR),
        )
        if result.returncode != 0:
            return False
        staged_files = [line.strip().replace("\\", "/") for line in result.stdout.splitlines()]
        normalized = rel_path.replace("\\", "/")
        return normalized in staged_files
    except OSError:
        return False


def _hygiene_matches_archive_pattern(filename: str) -> bool:
    """파일명이 HYGIENE_ARCHIVE_PATTERNS 중 하나와 일치하는지 확인합니다.

    Args:
        filename: 검사할 파일명 (basename only).

    Returns:
        일치 패턴이 있으면 True.
    """
    for pattern in HYGIENE_ARCHIVE_PATTERNS:
        if _fnmatch.fnmatch(filename, pattern):
            return True
    return False


def _hygiene_classify(
    rel_path: str,
    mtime_epoch: float,
    older_than_days: int,
    active_pipeline_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """파일 하나의 hygiene 분류 결과를 반환합니다 (단독 호출용, git을 직접 호출).

    Args:
        rel_path: BASE_DIR 기준 상대 경로.
        mtime_epoch: 파일 최종 수정 시각 (Unix timestamp).
        older_than_days: 이 일수 이상이어야 후보가 됨.
        active_pipeline_ids: 현재 활성 파이프라인 ID 목록 (파일명 보호에 사용).

    Returns:
        {
            "rel_path": str,
            "age_days": float,
            "disposition": "candidate" | "excluded" | "blocked",
            "reason": str | None,
        }
    """
    tracked = _hygiene_get_tracked_files()
    staged = _hygiene_get_staged_files()
    return _hygiene_classify_fast(rel_path, mtime_epoch, older_than_days,
                                  active_pipeline_ids, tracked, staged)


def _hygiene_classify_fast(
    rel_path: str,
    mtime_epoch: float,
    older_than_days: int,
    active_pipeline_ids: Optional[List[str]],
    tracked_files: set,
    staged_files: set,
) -> Dict[str, Any]:
    """파일 하나의 hygiene 분류 결과를 반환합니다 (사전 조회된 git 집합 사용).

    보호 우선순위 (높을수록 먼저 적용):
      1. git tracked → exclude(reason=git_tracked)
      2. git staged  → exclude(reason=git_staged)
      3. HYGIENE_PROTECTED_PATHS/PREFIXES → exclude(reason=trust_root_protected 등)
      4. 활성 pipeline_id가 파일명에 포함 → exclude(reason=active_pipeline_in_name)
      5. HYGIENE_ARCHIVE_PATTERNS 불일치 → exclude(reason=not_archive_pattern)
      6. secret 패턴 감지 → blocked(reason=secret_detected)
      7. 임계값 미만 나이 → exclude(reason=younger_than_threshold)
      8. 모두 통과 → candidate

    Args:
        rel_path: BASE_DIR 기준 상대 경로.
        mtime_epoch: 파일 최종 수정 시각 (Unix timestamp).
        older_than_days: 이 일수 이상이어야 후보가 됨.
        active_pipeline_ids: 현재 활성 파이프라인 ID 목록.
        tracked_files: git ls-files로 사전 조회한 tracked 파일 집합.
        staged_files: git diff --cached로 사전 조회한 staged 파일 집합.

    Returns:
        {
            "rel_path": str,
            "age_days": float,
            "disposition": "candidate" | "excluded" | "blocked",
            "reason": str | None,
        }
    """
    normalized = rel_path.replace("\\", "/")
    basename = normalized.split("/")[-1]
    now_epoch = _datetime.datetime.now(_datetime.timezone.utc).timestamp()
    age_days = (now_epoch - mtime_epoch) / 86400.0

    # 1. git tracked
    if normalized in tracked_files or basename in tracked_files:
        return {"rel_path": normalized, "age_days": age_days,
                "disposition": "excluded", "reason": "git_tracked"}

    # 2. git staged
    if normalized in staged_files:
        return {"rel_path": normalized, "age_days": age_days,
                "disposition": "excluded", "reason": "git_staged"}

    # 3. HYGIENE_PROTECTED_PATHS (정확한 파일명 일치)
    if basename in HYGIENE_PROTECTED_PATHS or normalized in HYGIENE_PROTECTED_PATHS:
        return {"rel_path": normalized, "age_days": age_days,
                "disposition": "excluded", "reason": "trust_root_protected"}

    # 3b. HYGIENE_PROTECTED_PREFIXES
    for prefix in HYGIENE_PROTECTED_PREFIXES:
        if normalized.startswith(prefix):
            if prefix in (".github/",):
                reason = "github_dir_protected"
            elif prefix in (".claude/",):
                reason = "claude_dir_protected"
            elif prefix in ("tests/oracles/", "tests/"):
                reason = "oracle_protected"
            else:
                reason = "trust_root_protected"
            return {"rel_path": normalized, "age_days": age_days,
                    "disposition": "excluded", "reason": reason}

    # 4. 활성 파이프라인 ID가 파일명에 포함
    if active_pipeline_ids:
        for pid in active_pipeline_ids:
            if pid and pid in basename:
                return {"rel_path": normalized, "age_days": age_days,
                        "disposition": "excluded", "reason": "active_pipeline_in_name"}

    # 5. 아카이브 패턴 일치 여부 확인 (secret 검사 전에 패턴 필터링)
    if not _hygiene_matches_archive_pattern(basename):
        return {"rel_path": normalized, "age_days": age_days,
                "disposition": "excluded", "reason": "not_archive_pattern"}

    # 6. secret 패턴 검사 (아카이브 대상 파일만)
    full_path = BASE_DIR / normalized
    try:
        content = full_path.read_text(encoding="utf-8", errors="replace")
        findings = _scan_text_for_secrets(content)
        if findings:
            return {"rel_path": normalized, "age_days": age_days,
                    "disposition": "blocked", "reason": "secret_detected"}
    except (OSError, PermissionError):
        return {"rel_path": normalized, "age_days": age_days,
                "disposition": "excluded", "reason": "read_error"}

    # 7. 나이 임계값
    if age_days < older_than_days:
        return {"rel_path": normalized, "age_days": age_days,
                "disposition": "excluded", "reason": "younger_than_threshold"}

    # 8. 모두 통과 → 후보
    return {"rel_path": normalized, "age_days": age_days,
            "disposition": "candidate", "reason": None}


def _hygiene_get_tracked_files() -> set:
    """git ls-files를 한 번 실행하여 tracked 파일 집합을 반환합니다.

    개별 파일마다 git을 호출하는 대신 한 번의 호출로 성능을 최적화합니다.

    Returns:
        tracked 파일명(최상위 파일의 basename) 집합. git 사용 불가 시 빈 집합.
    """
    import subprocess as _subprocess
    try:
        result = _subprocess.run(
            ["git", "ls-files"],
            capture_output=True,
            text=True,
            cwd=str(BASE_DIR),
            timeout=10,
        )
        if result.returncode != 0:
            return set()
        return {line.strip() for line in result.stdout.splitlines() if line.strip()}
    except (OSError, _subprocess.TimeoutExpired):
        return set()


def _hygiene_get_staged_files() -> set:
    """git diff --cached를 한 번 실행하여 staged 파일 집합을 반환합니다.

    Returns:
        staged 파일명 집합. git 사용 불가 시 빈 집합.
    """
    import subprocess as _subprocess
    try:
        result = _subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            capture_output=True,
            text=True,
            cwd=str(BASE_DIR),
            timeout=10,
        )
        if result.returncode != 0:
            return set()
        return {line.strip().replace("\\", "/") for line in result.stdout.splitlines() if line.strip()}
    except (OSError, _subprocess.TimeoutExpired):
        return set()


def _hygiene_collect_candidates(
    older_than_days: int,
    active_pipeline_ids: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """BASE_DIR를 스캔하여 hygiene 대상 파일 목록을 수집합니다.

    서브디렉터리는 탐색하지 않습니다 (최상위 파일만).
    숨김 디렉터리(.git 등) 아래 파일은 제외됩니다.
    git ls-files를 한 번만 호출하여 성능을 최적화합니다.

    Args:
        older_than_days: 이 일수 이상 된 파일만 후보.
        active_pipeline_ids: 활성 파이프라인 ID 목록 (파일명 보호).

    Returns:
        각 파일의 분류 결과 딕셔너리 목록.
    """
    # git tracked/staged 파일을 한 번에 조회 (파일마다 git 호출 방지)
    tracked_files = _hygiene_get_tracked_files()
    staged_files = _hygiene_get_staged_files()

    results: List[Dict[str, Any]] = []
    try:
        for entry in BASE_DIR.iterdir():
            if entry.is_dir():
                continue
            try:
                stat = entry.stat()
                rel = entry.name  # 최상위 파일이므로 basename = rel_path
                classification = _hygiene_classify_fast(
                    rel_path=rel,
                    mtime_epoch=stat.st_mtime,
                    older_than_days=older_than_days,
                    active_pipeline_ids=active_pipeline_ids,
                    tracked_files=tracked_files,
                    staged_files=staged_files,
                )
                results.append(classification)
            except (OSError, PermissionError):
                continue
    except (OSError, PermissionError) as exc:
        print(f"[HYGIENE] BASE_DIR 스캔 오류: {exc}")
    return results


def _hygiene_move_file(
    rel_path: str,
    archive_date_str: str,
) -> Dict[str, Any]:
    """파일을 archive 폴더로 이동합니다.

    대상: _deployment_root() / "찌꺼기" / YYYY-MM-DD / rel_path
    원본 상대 경로 구조를 보존합니다.

    Args:
        rel_path: BASE_DIR 기준 상대 경로.
        archive_date_str: "YYYY-MM-DD" 형식 날짜 문자열.

    Returns:
        {
            "rel_path": str,
            "dest": str (이동 완료 대상 경로),
            "status": "moved" | "error",
            "error": str | None,
        }
    """
    src = BASE_DIR / rel_path
    try:
        deploy_root = _deployment_root()
    except SystemExit:
        return {"rel_path": rel_path, "dest": None, "status": "error",
                "error": "deploy_root_not_found"}

    dest_dir = deploy_root / "찌꺼기" / archive_date_str / Path(rel_path).parent
    dest_file = deploy_root / "찌꺼기" / archive_date_str / rel_path

    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        _shutil.move(str(src), str(dest_file))
        return {"rel_path": rel_path, "dest": str(dest_file), "status": "moved", "error": None}
    except (OSError, PermissionError) as exc:
        return {"rel_path": rel_path, "dest": None, "status": "error", "error": str(exc)}


def _hygiene_write_manifest(
    result: Dict[str, Any],
    manifest_path: Path,
) -> None:
    """hygiene archive 결과를 JSON manifest 파일로 기록합니다.

    blocked 항목은 파일을 이동하지 않았음을 manifest에 명시합니다.

    Args:
        result: cmd_hygiene_archive가 반환하는 결과 딕셔너리.
        manifest_path: manifest를 저장할 파일 경로.
    """
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(manifest_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)


def cmd_hygiene_scan(args: "argparse.Namespace") -> None:
    """hygiene scan — 임시 산출물 스캔 (실제 이동 없음).

    7일 이상 된 HYGIENE_ARCHIVE_PATTERNS 일치 파일을 찾아 목록을 출력합니다.
    실제 파일 이동은 하지 않습니다.

    IMP-20260601-0DF5 MT-1
    """
    older_than_str: str = getattr(args, "older_than", "7d")
    json_output: bool = getattr(args, "json", False)

    try:
        older_than_days = _parse_older_than(older_than_str)
    except ValueError as exc:
        _die(f"[HYGIENE ERROR] --older-than 형식 오류: {exc}. 예: 7d, 14d")

    # 활성 파이프라인 ID 수집
    state = _load_state() or {}
    active_ids: List[str] = []
    pid = state.get("pipeline_id")
    if pid:
        active_ids.append(pid)

    all_items = _hygiene_collect_candidates(older_than_days, active_ids)

    candidates = [i for i in all_items if i["disposition"] == "candidate"]
    blocked = [i for i in all_items if i["disposition"] == "blocked"]
    excluded = [i for i in all_items if i["disposition"] == "excluded"]

    result: Dict[str, Any] = {
        "status": "OK",
        "older_than_days": older_than_days,
        "scanned_total": len(all_items),
        "candidates": [
            {
                "path": c["rel_path"],
                "age_days_gte": int(c["age_days"]),
                "blocked": False,
            }
            for c in candidates
        ],
        "blocked": [
            {"path": b["rel_path"], "reason": b["reason"]}
            for b in blocked
        ],
        "excluded": [
            {"path": e["rel_path"], "reason": e["reason"]}
            for e in excluded
        ],
        "moved": [],
        "note": "scan 모드는 후보만 표시. 실제 이동 없음.",
    }

    if json_output:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"[HYGIENE SCAN] {older_than_days}일 이상 된 임시 산출물 스캔 결과")
        print(f"  후보: {len(candidates)}개  차단: {len(blocked)}개  제외: {len(excluded)}개")
        for c in candidates:
            print(f"  [후보] {c['rel_path']}  ({int(c['age_days'])}일 경과)")
        for b in blocked:
            print(f"  [차단] {b['rel_path']}  reason={b['reason']}")


def cmd_hygiene_archive(args: "argparse.Namespace") -> None:
    """hygiene archive — 임시 산출물을 Google Drive 찌꺼기 폴더로 이동.

    후보 파일을 _deployment_root()/찌꺼기/YYYY-MM-DD/ 아래로 이동합니다.
    blocked 파일(secret 감지)은 이동하지 않고 manifest에 기록합니다.

    IMP-20260601-0DF5 MT-1
    """
    older_than_str: str = getattr(args, "older_than", "7d")
    json_output: bool = getattr(args, "json", False)
    dry_run: bool = getattr(args, "dry_run", False)

    try:
        older_than_days = _parse_older_than(older_than_str)
    except ValueError as exc:
        _die(f"[HYGIENE ERROR] --older-than 형식 오류: {exc}. 예: 7d, 14d")

    state = _load_state() or {}
    active_ids: List[str] = []
    pid = state.get("pipeline_id")
    if pid:
        active_ids.append(pid)

    all_items = _hygiene_collect_candidates(older_than_days, active_ids)

    candidates = [i for i in all_items if i["disposition"] == "candidate"]
    blocked_items = [i for i in all_items if i["disposition"] == "blocked"]
    excluded_items = [i for i in all_items if i["disposition"] == "excluded"]

    archive_date = _datetime.datetime.now(_datetime.timezone.utc).strftime("%Y-%m-%d")
    moved: List[Dict[str, Any]] = []
    move_errors: List[Dict[str, Any]] = []

    if not dry_run:
        for c in candidates:
            move_result = _hygiene_move_file(c["rel_path"], archive_date)
            if move_result["status"] == "moved":
                moved.append({"path": c["rel_path"], "dest": move_result["dest"]})
            else:
                move_errors.append({"path": c["rel_path"], "error": move_result["error"]})

    status = "OK_WITH_BLOCKED" if blocked_items else "OK"

    result: Dict[str, Any] = {
        "status": status,
        "older_than_days": older_than_days,
        "archive_date": archive_date,
        "dry_run": dry_run,
        "moved": moved,
        "move_errors": move_errors,
        "blocked": [
            {"path": b["rel_path"], "reason": b["reason"]}
            for b in blocked_items
        ],
        "excluded": [
            {"path": e["rel_path"], "reason": e["reason"]}
            for e in excluded_items
        ],
        "manifest_must_record_blocked": bool(blocked_items),
    }

    # manifest 기록
    if not dry_run:
        manifest_path = BASE_DIR / ".pipeline" / "hygiene" / f"archive_{archive_date}.json"
        _hygiene_write_manifest(result, manifest_path)
        result["manifest_path"] = str(manifest_path)
        # cleanup_manifest.json을 pipeline_contracts/{pid}/ 에도 저장 (oracle gate용)
        try:
            state_pid = (state.get("pipeline_id") or "").strip()
            if state_pid:
                cleanup_path = BASE_DIR / "pipeline_contracts" / state_pid / "cleanup_manifest.json"
                cleanup_path.parent.mkdir(parents=True, exist_ok=True)
                cleanup_manifest: Dict[str, Any] = {
                    "pipeline_id": state_pid,
                    "executed_at": archive_date,
                    "moved": result["moved"],
                    "blocked": result["blocked"],
                    "excluded": [
                        {"path": e["path"], "reason": e["reason"]}
                        for e in result["excluded"]
                    ],
                    "move_errors": result["move_errors"],
                    "total_bytes": 0,
                    "status": result["status"],
                }
                cleanup_path.write_text(
                    json.dumps(cleanup_manifest, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
        except Exception:
            pass

    if json_output:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"[HYGIENE ARCHIVE] {older_than_days}일 이상 된 산출물 이동 결과")
        if dry_run:
            print("  [DRY RUN] 실제 파일 이동 없음")
        print(f"  이동: {len(moved)}개  차단: {len(blocked_items)}개  오류: {len(move_errors)}개")
        for m in moved:
            print(f"  [이동] {m['path']} → {m['dest']}")
        for b in blocked_items:
            print(f"  [차단] {b['path']}  reason={b['reason']}")
        for e in move_errors:
            print(f"  [오류] {e['path']}  error={e['error']}")


def _parse_older_than(value: str) -> int:
    """'7d', '14d' 형식의 문자열을 일수(int)로 변환합니다.

    Args:
        value: '7d' 또는 '14d' 형식 문자열.

    Returns:
        일수 (양수 정수).

    Raises:
        ValueError: 형식이 잘못된 경우.
    """
    if not value or not isinstance(value, str):
        raise ValueError(f"올바른 형식이 아닙니다: {value!r}")
    v = value.strip().lower()
    if v.endswith("d"):
        try:
            days = int(v[:-1])
        except ValueError:
            raise ValueError(f"일수를 파싱할 수 없습니다: {value!r}")
        if days <= 0:
            raise ValueError(f"일수는 양수여야 합니다: {days}")
        return days
    raise ValueError(f"'7d' 형식이 필요합니다. 받은 값: {value!r}")


def cmd_hygiene(args: "argparse.Namespace") -> None:
    """hygiene scan | archive | schedule — 임시 산출물 정리 CLI.

    scan: 후보 목록만 표시 (파일 이동 없음)
    archive: 후보를 Google Drive 찌꺼기 폴더로 이동
    schedule: Windows 작업 스케줄러 등록/상태 확인

    IMP-20260601-0DF5 MT-1/MT-2
    """
    sub = getattr(args, "hygiene_sub", None)
    if sub == "scan":
        cmd_hygiene_scan(args)
    elif sub == "archive":
        cmd_hygiene_archive(args)
    elif sub == "schedule":
        cmd_hygiene_schedule(args)
    else:
        _die("[HYGIENE ERROR] 알 수 없는 hygiene 서브명령. scan|archive|schedule 중 선택하세요.")


# ---------------------------------------------------------------------------
# IMP-20260601-0DF5 MT-2: Hygiene Schedule — Windows 작업 스케줄러 연동
# ---------------------------------------------------------------------------

import platform as _platform
import subprocess as _subprocess_mt2


_HYGIENE_TASK_NAME = "PipelineHygieneWeekly"
_HYGIENE_SCHEDULE_TRIGGER = "WEEKLY"
_HYGIENE_SCHEDULE_DAY = "MON"
_HYGIENE_SCHEDULE_TIME = "09:00"


def _hygiene_schtasks_dry_run() -> str:
    """schtasks 등록에 사용할 명령어 문자열을 반환합니다 (실제 실행 없음).

    Returns:
        schtasks 등록 명령어 문자열.
    """
    import sys
    python_exe = sys.executable
    pipeline_path = str(BASE_DIR / "pipeline.py")
    cmd = (
        f'schtasks /Create /SC {_HYGIENE_SCHEDULE_TRIGGER} '
        f'/D {_HYGIENE_SCHEDULE_DAY} '
        f'/ST {_HYGIENE_SCHEDULE_TIME} '
        f'/TN "{_HYGIENE_TASK_NAME}" '
        f'/TR "\\"{python_exe}\\" \\"{pipeline_path}\\" hygiene archive --older-than 7d" '
        f'/F'
    )
    return cmd


def _hygiene_schedule_install(dry_run: bool = False) -> Dict[str, Any]:
    """Windows 작업 스케줄러에 매주 월요일 09:00 hygiene archive 작업을 등록합니다.

    Args:
        dry_run: True이면 명령어만 출력하고 실제 등록하지 않음.

    Returns:
        {
            "status": "INSTALLED" | "DRY_RUN" | "BLOCKED",
            "command": str,
            "error": str | None,
            "manual_hint": str | None,
        }
    """
    cmd_str = _hygiene_schtasks_dry_run()

    if dry_run:
        return {
            "status": "DRY_RUN",
            "command": cmd_str,
            "error": None,
            "manual_hint": f"수동 등록 명령: {cmd_str}",
        }

    if _platform.system() != "Windows":
        return {
            "status": "BLOCKED",
            "command": cmd_str,
            "error": f"Windows 전용 기능입니다. 현재 OS: {_platform.system()}",
            "manual_hint": "Windows 환경에서 실행하세요.",
        }

    import sys
    python_exe = sys.executable
    pipeline_path = str(BASE_DIR / "pipeline.py")
    schtasks_args = [
        "schtasks", "/Create",
        "/SC", _HYGIENE_SCHEDULE_TRIGGER,
        "/D", _HYGIENE_SCHEDULE_DAY,
        "/ST", _HYGIENE_SCHEDULE_TIME,
        "/TN", _HYGIENE_TASK_NAME,
        "/TR", f'"{python_exe}" "{pipeline_path}" hygiene archive --older-than 7d',
        "/F",
    ]

    try:
        result = _subprocess_mt2.run(
            schtasks_args,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            return {
                "status": "INSTALLED",
                "command": cmd_str,
                "error": None,
                "manual_hint": None,
            }
        else:
            error_msg = result.stderr.strip() or result.stdout.strip()
            return {
                "status": "BLOCKED",
                "command": cmd_str,
                "error": f"schtasks 등록 실패 (exit {result.returncode}): {error_msg}",
                "manual_hint": f"수동 등록 명령: {cmd_str}",
            }
    except _subprocess_mt2.TimeoutExpired:
        return {
            "status": "BLOCKED",
            "command": cmd_str,
            "error": "schtasks 명령 타임아웃 (30초)",
            "manual_hint": f"수동 등록 명령: {cmd_str}",
        }
    except (OSError, FileNotFoundError) as exc:
        return {
            "status": "BLOCKED",
            "command": cmd_str,
            "error": f"schtasks 실행 오류: {exc}",
            "manual_hint": f"수동 등록 명령: {cmd_str}",
        }


def _hygiene_schedule_status() -> Dict[str, Any]:
    """Windows 작업 스케줄러에서 hygiene 작업 등록 상태를 조회합니다.

    Returns:
        {
            "status": "INSTALLED" | "NOT_INSTALLED" | "ERROR" | "NOT_WINDOWS",
            "task_name": str,
            "details": str | None,
        }
    """
    if _platform.system() != "Windows":
        return {
            "status": "NOT_WINDOWS",
            "task_name": _HYGIENE_TASK_NAME,
            "details": f"Windows 전용 기능. 현재 OS: {_platform.system()}",
        }

    try:
        result = _subprocess_mt2.run(
            ["schtasks", "/Query", "/TN", _HYGIENE_TASK_NAME, "/FO", "LIST"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0:
            return {
                "status": "INSTALLED",
                "task_name": _HYGIENE_TASK_NAME,
                "details": result.stdout.strip(),
            }
        else:
            return {
                "status": "NOT_INSTALLED",
                "task_name": _HYGIENE_TASK_NAME,
                "details": result.stderr.strip() or "작업 없음",
            }
    except _subprocess_mt2.TimeoutExpired:
        return {
            "status": "ERROR",
            "task_name": _HYGIENE_TASK_NAME,
            "details": "schtasks 조회 타임아웃 (15초)",
        }
    except (OSError, FileNotFoundError) as exc:
        return {
            "status": "ERROR",
            "task_name": _HYGIENE_TASK_NAME,
            "details": f"schtasks 실행 오류: {exc}",
        }


def cmd_hygiene_schedule(args: "argparse.Namespace") -> None:
    """hygiene schedule install | status — Windows 작업 스케줄러 등록/조회.

    install: 매주 월요일 09:00 hygiene archive 작업 등록
    status: 등록 상태 조회

    IMP-20260601-0DF5 MT-2
    """
    schedule_sub = getattr(args, "schedule_sub", None)
    json_output: bool = getattr(args, "json", False)
    dry_run: bool = getattr(args, "dry_run", False)

    if schedule_sub == "install":
        result = _hygiene_schedule_install(dry_run=dry_run)
        if json_output:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            status = result["status"]
            if status == "INSTALLED":
                print(f"[HYGIENE SCHEDULE] 작업 등록 완료: {_HYGIENE_TASK_NAME}")
            elif status == "DRY_RUN":
                print("[HYGIENE SCHEDULE DRY RUN] 등록 명령:")
                print(f"  {result['command']}")
            else:
                print(f"[HYGIENE SCHEDULE BLOCKED] {result['error']}")
                if result.get("manual_hint"):
                    print(f"  수동 명령: {result['manual_hint']}")
                raise SystemExit(1)

    elif schedule_sub == "status":
        result = _hygiene_schedule_status()
        if json_output:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            status = result["status"]
            if status == "INSTALLED":
                print(f"[HYGIENE SCHEDULE] 등록됨: {_HYGIENE_TASK_NAME}")
                if result.get("details"):
                    print(result["details"])
            elif status == "NOT_INSTALLED":
                print(f"[HYGIENE SCHEDULE] 미등록: {_HYGIENE_TASK_NAME}")
                print("  등록하려면: python pipeline.py hygiene schedule install")
            elif status == "NOT_WINDOWS":
                print(f"[HYGIENE SCHEDULE] {result['details']}")
            else:
                print(f"[HYGIENE SCHEDULE ERROR] {result.get('details', '알 수 없는 오류')}")
    else:
        _die("[HYGIENE ERROR] hygiene schedule 서브명령이 필요합니다. install|status 중 선택하세요.")


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
    "module":               cmd_module,
    "gates":                cmd_gates,
    "advisory":             cmd_advisory,
    "github":               cmd_github,
    "agent":                cmd_agent,
    "outputs":              cmd_outputs,
    "report":               cmd_report,
    "codex":                cmd_codex,
    "preflight":            cmd_preflight,
    "review":               cmd_review,
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
    "cluster":              cmd_cluster,
    "patch":                cmd_patch,
    "metrics":              cmd_metrics,
    "budget":               cmd_budget,
    "golden":               cmd_golden,
    "hygiene":              cmd_hygiene,
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
