"""
회귀 테스트: PR diff runtime artifact gate (IMP-20260703-B985 MT-32)
oracle: tests/oracles/IMP-20260703-B985/TC-RT-gate/
"""
import json
import os
import sys
import unittest.mock as mock

# 프로젝트 루트를 sys.path에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ORACLE_DIR = os.path.join(os.path.dirname(__file__), "oracles", "IMP-20260703-B985", "TC-RT-gate")


def _load_oracle(name):
    path = os.path.join(ORACLE_DIR, name)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return None


def _call_check(diff_files):
    """_check_pr_diff_runtime_artifacts를 직접 호출하는 헬퍼"""
    from pipeline import _check_pr_diff_runtime_artifacts
    # monkeypatch: git diff를 mock으로 대체
    diff_output = "\n".join(diff_files)
    with mock.patch("subprocess.run") as mrun:
        mrun.return_value = mock.Mock(returncode=0, stdout=diff_output, stderr="")
        result = _check_pr_diff_runtime_artifacts({})
    return result


def test_rt_gate_blocks_root_xml():
    """root-level dev_handover.xml → BLOCKED"""
    result = _call_check(["dev_handover_mt27.xml", "pipeline.py"])
    assert result.get("status") == "BLOCKED", f"Expected BLOCKED, got: {result}"
    assert result.get("failure_code") == "pr_diff_runtime_artifact"
    oracle = _load_oracle("expected_blocked.json")
    if oracle is not None:
        assert result.get("status") == oracle["status"]
        assert result.get("failure_code") == oracle["failure_code"]


def test_rt_gate_blocks_root_integration_report():
    """root-level integration_report.xml → BLOCKED"""
    result = _call_check(["integration_report_r6.xml"])
    assert result.get("status") == "BLOCKED", f"Expected BLOCKED, got: {result}"


def test_rt_gate_blocks_architect_rca():
    """root-level architect_rca_b985_mt27.xml → BLOCKED"""
    result = _call_check(["architect_rca_b985_mt27.xml"])
    assert result.get("status") == "BLOCKED", f"Expected BLOCKED, got: {result}"
    assert result.get("failure_code") == "pr_diff_runtime_artifact"


def test_rt_gate_allows_tests_dir():
    """tests/ 아래 파일 → PASS"""
    result = _call_check(["tests/test_something.py", "tests/oracles/x/y.json"])
    assert result.get("status") == "OK", f"Expected OK, got: {result}"
    oracle = _load_oracle("expected_ok.json")
    if oracle is not None:
        assert result.get("status") == oracle["status"]


def test_rt_gate_allows_tests_dir_xml():
    """tests/ 아래 XML(oracle) → PASS (별도 게이트 처리)"""
    result = _call_check(["tests/oracles/IMP-x/case/step_plan.xml"])
    assert result.get("status") == "OK", f"Expected OK, got: {result}"


def test_rt_gate_allows_pipeline_py():
    """pipeline.py 단독 → PASS (xml 아님)"""
    result = _call_check(["pipeline.py"])
    assert result.get("status") == "OK", f"Expected OK, got: {result}"


def test_rt_gate_allows_gitignore():
    """.gitignore → PASS"""
    result = _call_check([".gitignore"])
    assert result.get("status") == "OK", f"Expected OK, got: {result}"


def test_rt_gate_allows_empty_diff():
    """빈 diff → PASS"""
    result = _call_check([])
    assert result.get("status") == "OK", f"Expected OK, got: {result}"


def test_rt_gate_none_state_raises():
    """state=None → TypeError"""
    from pipeline import _check_pr_diff_runtime_artifacts
    try:
        _check_pr_diff_runtime_artifacts(None)
        assert False, "예외 미발생"
    except TypeError:
        pass


if __name__ == "__main__":
    test_rt_gate_blocks_root_xml()
    test_rt_gate_blocks_root_integration_report()
    test_rt_gate_blocks_architect_rca()
    test_rt_gate_allows_tests_dir()
    test_rt_gate_allows_tests_dir_xml()
    test_rt_gate_allows_pipeline_py()
    test_rt_gate_allows_gitignore()
    test_rt_gate_allows_empty_diff()
    test_rt_gate_none_state_raises()
    print("[SELF-VERIFY] OK")
