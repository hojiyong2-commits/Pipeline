#!/usr/bin/env python3
"""Work Protocol Pipeline Enforcer.

현재 `/Task` 파이프라인의 완료 조건은 숫자 점수가 아니라 아래 trust chain입니다.

    local pipeline.py -> agent receipts -> GitHub Actions -> CODEOWNERS -> human ACCEPT

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

        evidence = _require_design_text(question, "evidence", f"{qid} evidence", min_len=12)
        why = _require_design_text(question, "why_it_matters", f"{qid} why_it_matters", min_len=20)
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

    design_confirmation = _validate_pm_design_confirmation(step_plan, micro_tasks)
    execution_profile = _parse_task_complexity(step_plan, micro_tasks)

    return {
        "report_file": str(path),
        "validated_at": _now(),
        "audit_result": audit_result,
        "micro_task_count": len(micro_tasks),
        "micro_tasks": micro_tasks,
        "design_confirmation": design_confirmation,
        "execution_profile": execution_profile,
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
    return {
        "manifest_file": str(path),
        "validated_at": _now(),
        "micro_task_id": mt_id,
        "files": sorted(manifest_files),
        "affected_functions": sorted(manifest_functions),
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
CONTRACTS_DIR = BASE_DIR / "pipeline_contracts"
PIPELINE_CI_DIR = BASE_DIR / ".pipeline"
PHASE_ATTESTATION_REQUEST = PIPELINE_CI_DIR / "phase_attestation_request.json"
PHASE_ATTESTATION_EVIDENCE_DIR = PIPELINE_CI_DIR / "phase_evidence"
AGENT_RECEIPT_DIR = PIPELINE_CI_DIR / "agent_receipts"
PHASE_ATTESTATION_PHASES = ("pm", "dev", "qa", "build")
AGENT_RUN_PHASES = ("pm_planner", "pipeline_manager", "dev", "qa", "build")
PHASE_RECEIPT_RUN_PHASES = {
    "pm": "pm_planner",
    "dev": "dev",
    "qa": "qa",
    "build": "build",
}
PHASE_AGENT_IDS = {
    "pm": "pm-planner-agent",
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


def _deployment_artifacts(state: Dict[str, Any], evidence: Optional[str]) -> List[Path]:
    candidates: List[str] = []
    candidates.extend(_split_evidence_paths(evidence))
    outputs = _ensure_output_registry(state)
    for item in outputs.get("items", []):
        if isinstance(item, dict):
            candidates.extend(_split_evidence_paths(item.get("public_path")))
    phases = state.get("phases", {})
    if isinstance(phases, dict):
        for phase_name in ("build", "dev"):
            phase = phases.get(phase_name, {})
            if isinstance(phase, dict):
                candidates.extend(_split_evidence_paths(phase.get("evidence")))

    result: List[Path] = []
    seen: set[str] = set()
    for raw in candidates:
        path = _resolve_artifact_path(raw)
        if not path:
            continue
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result


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
    artifacts = _deployment_artifacts(state, evidence)
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
    if run.get("phase") != run_phase:
        _die(f"[AGENT RECEIPT GATE] run phase mismatch: expected {run_phase}, got {run.get('phase')}")
    expected_agent = _expected_agent_id(run_phase)
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
        "path": _normalize_rel_path(path),
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
        "path": _normalize_rel_path(dest),
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
        if not manager_run_id:
            _die("[PM MANAGER GATE] pm phase cannot prepare CI attestation without manager_run_id")
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


def cmd_check(args: argparse.Namespace) -> None:
    """gate 검증 -exit 0: 통과, exit 1: 차단."""
    state = _load_branch_state(args)
    phase = args.phase.lower()

    # Phase 6 -> 7 does not ask the user anymore. Phase 7 is deterministic
    # automation; the only user decision is the final gates accept ACCEPT/REJECT.

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
    agent_run: Optional[Dict[str, Any]] = None
    if phase == "pm":
        if not report_file:
            _die(
                "[ATOMIC PLAN GATE] PM done requires "
                "`python pipeline.py done --phase pm --report-file step_plan.xml --planner-run-id <planner_run_id> "
                "--manager-run-id <manager_run_id> --manager-report manager_handoff.xml --decomp --clarification ...`"
            )
        if getattr(args, "agent_run_id", None):
            _die(
                "[PM SPLIT GATE] --agent-run-id is no longer accepted for PM. "
                "Use --planner-run-id and --manager-run-id."
            )
        planner_run_id = getattr(args, "planner_run_id", None)
        manager_run_id = getattr(args, "manager_run_id", None)
        manager_report = getattr(args, "manager_report", None)
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

    state["phases"][phase]["status"]       = "DONE"
    state["phases"][phase]["completed_at"] = _now()
    state["phases"][phase]["evidence"]     = evidence
    state["phases"][phase]["report_file"]  = report_file
    if agent_run:
        state["phases"][phase]["agent_run_id"] = agent_run["run_id"]
        state["phases"][phase]["agent_id"] = agent_run["agent_id"]
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
            print(f"\n  다음: {YELLOW('python pipeline.py done --phase dev --files \"수정된파일들\" --report-file dev_handover.xml --scope-declared --scope-manifest scope_manifest.json --agent-run-id <dev_run_id>')}\n")
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
    print(f"  다음 절차:")
    print(f"    1. Build evidence commit/push 후 GitHub Actions phase attestation 확인:")
    print(f"       {YELLOW('python pipeline.py gates phase-ci --phase build --repo hojiyong2-commits/Pipeline')}")
    print(f"    2. test-harness-agent는 진단만 수행하고, 아래 external gates를 기록:")
    print(f"       {YELLOW('python pipeline.py gates technical')}")
    print(f"       {YELLOW('python pipeline.py gates oracle')}")
    print(f"       {YELLOW('python pipeline.py gates github-ci --repo hojiyong2-commits/Pipeline')}")
    print(f"       {YELLOW('python pipeline.py gates accept --result ACCEPT --evidence [실제-결과물-경로-또는-첨부파일] --user-confirmed')}")
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
        "       python pipeline.py gates accept --result ACCEPT --evidence [실제-결과물-경로-또는-첨부파일] --user-confirmed"
    )


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
            "[ARCHITECT REPORT GATE] external gates are PASS but architect report rca_mode is fail-oriented. "
            "Use a completion/retrospective mode for PASS, or record a real external gate FAIL first."
        )

    state["phases"]["architect"]["status"]       = "DONE"
    state["phases"]["architect"]["completed_at"] = _now()
    state["phases"]["architect"]["report_file"]  = protocol_decision["report_file"]
    state["protocol_evolution_decision"] = protocol_decision
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
        print(f"\n  다음 단계: {YELLOW('python pipeline.py done --phase dev --files \"..\" --report-file dev_handover.xml --scope-declared --scope-manifest scope_manifest.json --agent-run-id <dev_run_id>')}")
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
    profile = _execution_profile(state)
    profile_mode = str(profile.get("mode") or "STANDARD")
    fast_label = "빠른 경로" if profile_mode in FAST_EXECUTION_PROFILES else "표준 경로"
    print(f"  실행 프로필: {profile_mode} ({fast_label})")
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
        for gate_name in ("technical", "oracle", "acceptance", "github_ci"):
            gate = gates.get(gate_name, {})
            if not isinstance(gate, dict):
                continue
            status = str(gate.get("status", "PENDING"))
            color = GREEN if status == "PASS" else RED if status == "FAIL" else YELLOW
            print(f"    {color(gate_name):<16} [{color(status):<8}] {gate.get('completed_at') or ''}")
            if gate.get("evidence"):
                print(DIM(f"        증거: {gate.get('evidence')}"))
        blockers = _external_gate_blockers(state)
        if blockers and terminal != "COMPLETE":
            print(RED("    blockers: " + "; ".join(blockers)))

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

    failures = state.get("failure_packets")
    if isinstance(failures, list) and failures:
        print()
        print(BOLD("  최근 실패 패킷:"))
        for item in failures[-3:]:
            if not isinstance(item, dict):
                continue
            print(f"    - {item.get('gate')} -> {item.get('repair_owner')} ({item.get('packet_path')})")

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
            if current == "harness":
                print(f"  확인 명령: {YELLOW('python pipeline.py gates status')}")
                print(f"  다음 게이트: {YELLOW('technical -> oracle -> github-ci -> accept')}")
            else:
                print(f"  확인 명령: {YELLOW(f'python pipeline.py check --phase {current}')}")
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
                'python pipeline.py gates accept --result ACCEPT --evidence PATH --user-confirmed'
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
    if not path.suffix.lower() in PRODUCT_CODE_EXTENSIONS:
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
    blockers.extend(_phase_attestation_blockers(state))
    pid = str(state.get("pipeline_id", ""))
    unresolved = _unresolved_critical_advisories(pid)
    if unresolved:
        blockers.append(f"unresolved GPT advisory CRITICAL findings: {len(unresolved)}")
    return blockers


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
            getattr(args, "scope_manifest", None),
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
        commit_sha = _git_rev_parse(commit or "HEAD")
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
        commit_sha = _git_rev_parse(commit or "HEAD")
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
    payload = {
        "status": status,
        "checks": checks,
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
        print("\nCodex에서 시작할 때는 `/task` 문자열 대신 `.codex/skills/pipeline-task/SKILL.md` 지침을 먼저 읽고, 위 quick_start 순서를 따르세요.")

    if status != "PASS":
        sys.exit(1)


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


def _record_failure_packet(
    state: Dict[str, Any],
    gate_name: str,
    report: Dict[str, Any],
    *,
    command: Optional[List[str]] = None,
    note: str = "",
) -> Dict[str, Any]:
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
    packet = {
        "schema_version": 1,
        "pipeline_id": pid,
        "gate": gate_name,
        "attempt": attempt,
        "packet_path": str(packet_path),
        "recorded_at": _now(),
        "status": report.get("status") or report.get("summary", {}).get("verdict") or "FAIL",
        "repair_owner": _repair_owner_for_gate(gate_name, report),
        "minimal_rerun": minimal_rerun,
        "note": note,
        "failed_checks": failed_checks,
        "report_excerpt": {
            "blockers": report.get("blockers", []),
            "summary": report.get("summary", {}),
            "results": report.get("results", [])[:10] if isinstance(report.get("results"), list) else [],
        },
    }
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
        })
    return packet


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
                "execution_profile": profile.get("mode"),
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
        "execution_profile": profile.get("mode"),
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
        _enable_phase_attestations(state)
        state["module_gates"] = _ensure_module_gates(state)
        _log_event(state, "three_gate mode enabled (mandatory)")
        _log_event(state, "GitHub phase attestations enabled (mandatory)")
        _save(state)
        print(GREEN(f"\n[THREE GATE + PHASE ATTESTATION ENABLED] {pid}\n"))
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
            packet = _record_failure_packet(
                state,
                "technical",
                result,
                command=[sys.executable, "pipeline.py", "gates", "technical"],
                note="Technical gate failed; use failed_checks and minimal_rerun for targeted repair.",
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
            )
            _log_event(state, "oracle gate FAIL (manifest blockers)")
            _save(state)
            print(YELLOW(f"  failure packet: {packet['gate']} attempt {packet['attempt']}"))
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
            packet = _record_failure_packet(
                state,
                "oracle",
                report,
                command=[sys.executable, "pipeline.py", "gates", "oracle"],
                note="Oracle gate failed; inspect failing results and output path resolution before broad rework.",
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
        if not getattr(args, "user_confirmed", False):
            _die("user acceptance gate requires --user-confirmed")
        result = str(args.result).upper()
        if result not in {"ACCEPT", "REJECT"}:
            _die("[USER ACCEPTANCE BLOCKED] --result는 ACCEPT 또는 REJECT만 허용됩니다.")
        deployment: Optional[Dict[str, Any]] = None
        evidence_validation: Optional[Dict[str, Any]] = None
        if result == "ACCEPT":
            prereq = []
            for gate_name in ("technical", "oracle", "github_ci"):
                gate = state["external_gates"].get(gate_name, {})
                if not isinstance(gate, dict) or gate.get("status") != "PASS":
                    prereq.append(f"{gate_name} gate must be PASS before user ACCEPT")
            if prereq:
                _die("; ".join(prereq))
            evidence_validation = _validate_user_acceptance_evidence(args.evidence)
            deployment = _deploy_accepted_outputs(state, args.evidence, args.notes, evidence_validation)
        gate_status = "PASS" if result == "ACCEPT" else "FAIL"
        report = {
            "schema_version": 1,
            "generated_at": _now(),
            "pipeline_id": pid,
            "status": gate_status,
            "result": result,
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
            evidence=args.evidence or "user_confirmed",
            report_file=str(paths["user_validation"]),
            note=args.notes,
        )
        state["phases"]["harness"]["status"] = gate_status
        state["phases"]["harness"]["completed_at"] = _now()
        state["phases"]["harness"]["evidence"] = "three_gate_user_acceptance"
        state["phases"]["harness"]["report_file"] = str(paths["user_validation"])
        if deployment:
            state["deployment"] = deployment
        if gate_status != "PASS":
            _record_failure_packet(
                state,
                "acceptance",
                report,
                command=[sys.executable, "pipeline.py", "gates", "accept", "--result", "ACCEPT", "--evidence", "<repaired-result>", "--user-confirmed"],
                note="User rejected the visible result; PM/Dev should repair the requested behavior or clarify requirements.",
            )
        state["current_phase"] = "architect"
        _log_event(state, f"user acceptance gate {gate_status}")
        _record_snapshot(state, "harness", None)
        _save(state)
        color = GREEN if gate_status == "PASS" else RED
        print(color(f"\n[USER ACCEPTANCE GATE {gate_status}]"))
        print(f"  report: {paths['user_validation']}")
        if deployment:
            print(f"  deployed: {deployment['deploy_dir']}")
        print(f"  next: {YELLOW('python pipeline.py architect --report-file architect_report.xml')}\n")
        sys.exit(0 if gate_status == "PASS" else 1)

    if action == "github-ci":
        verification = _verify_github_ci_run(
            repo=args.repo,
            commit=args.commit,
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
            )
        _save(state)
        color = GREEN if verification["status"] == "PASS" else RED
        print(color(f"\n[GITHUB CI GATE {verification['status']}]"))
        print(f"  run_id: {verification.get('run_id')}")
        print(f"  report: {_contract_paths(pid)['github_ci_result']}\n")
        sys.exit(0 if verification["status"] == "PASS" else 1)

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

    # Codex compatibility hooks
    p_codex = sub.add_parser("codex", help="Codex compatibility checks for the mandatory pipeline")
    codex_sub = p_codex.add_subparsers(dest="codex_action", required=True)
    p_codex_doctor = codex_sub.add_parser("doctor", help="Check whether Codex can use the pipeline task hook")
    p_codex_doctor.add_argument("--json", action="store_true", default=False)

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
    p_gate_accept = gsub.add_parser("accept", help="Record user behavior acceptance")
    p_gate_accept.add_argument("--result", required=True, choices=["ACCEPT", "REJECT", "accept", "reject"])
    p_gate_accept.add_argument("--evidence", default=None, help="Output file, screenshot, or report shown to user")
    p_gate_accept.add_argument("--notes", default=None)
    p_gate_accept.add_argument("--user-confirmed", action="store_true", default=False)
    p_gate_github = gsub.add_parser("github-ci", help="Verify latest GitHub Actions CI run and record github_ci gate")
    p_gate_github.add_argument("--repo", default=None, help="owner/repo; defaults to origin remote")
    p_gate_github.add_argument("--run-id", default=None, help="Specific GitHub Actions workflow run id; omitted means latest run for HEAD")
    p_gate_github.add_argument("--commit", default=None, help="Expected commit SHA; defaults to local HEAD")
    p_gate_github.add_argument("--workflow", default="CI", help="Workflow run name to search when --run-id is omitted")
    p_gate_github.add_argument("--artifact", default="pipeline-attestation", help="Required artifact name")
    p_gate_github.add_argument("--token-env", default="GITHUB_TOKEN", help="Optional env var containing a GitHub token")

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
    "module":               cmd_module,
    "gates":                cmd_gates,
    "advisory":             cmd_advisory,
    "github":               cmd_github,
    "agent":                cmd_agent,
    "outputs":              cmd_outputs,
    "codex":                cmd_codex,
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
