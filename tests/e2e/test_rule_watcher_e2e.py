# [Purpose]: Rule Watcher 앱의 핵심 흐름을 subprocess 기반 실제 CLI/모듈 호출로 검증.
# [Assumptions]: Python 실행파일이 가용하고 rule_watcher 패키지가 import path에 있음.
# [Vulnerability & Risks]:
#   - subprocess timeout=30s — 느린 환경에서 timeout 발생 가능.
#   - KIS API 키가 OS 환경변수에 있더라도 subprocess env로 명시 차단.
# [Improvement]: streamlit smoke test 추가(streamlit run --headless), 다국어 로케일 검증.
"""E2E 테스트 — Rule Watcher 핵심 흐름 실제 subprocess 검증."""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys

# 외부 의존성: pytest는 테스트 collection 용
import pytest  # noqa: F401

_REPO = pathlib.Path(__file__).parent.parent.parent
_PYTHON = sys.executable


def _run_subprocess(script: str, extra_env: dict | None = None, timeout_sec: int = 30) -> subprocess.CompletedProcess:
    """subprocess로 inline python 스크립트 실행 (E2E 격리 환경).

    Args:
        script: 실행할 python 코드 문자열.
        extra_env: 환경변수 override (선택).
        timeout_sec: timeout 초 (기본 30).
    Returns:
        subprocess.CompletedProcess.
    """
    env = dict(os.environ)
    # KIS 환경변수는 mock 강제를 위해 항상 빈 값
    env["KIS_APP_KEY"] = ""
    env["KIS_APP_SECRET"] = ""
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [_PYTHON, "-c", script],
        capture_output=True,
        text=True,
        cwd=str(_REPO),
        env=env,
        timeout=timeout_sec,
    )


def test_main_py_runs_without_error() -> None:
    """main.py 진입 흐름이 API 키 없이 mock 모드로 정상 진입하는지 확인."""
    script = (
        "from rule_watcher.db import init_db; "
        "from rule_watcher import config as _cfg; "
        "_cfg.KIS_APP_KEY = ''; _cfg.KIS_APP_SECRET = ''; "
        "from rule_watcher.config import get_provider_name; "
        "init_db(':memory:'); "
        "provider = get_provider_name(); "
        "assert provider == 'mock', "
        "f'expected mock, got {provider}'"
    )
    result = _run_subprocess(script)
    assert result.returncode == 0, (
        f"E2E 실패\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_mock_provider_returns_ohlcv() -> None:
    """MockProvider.get_ohlcv()가 20개 OHLCV row를 반환하는지 E2E 검증."""
    script = (
        "from rule_watcher.providers.mock_provider import MockProvider; "
        "df = MockProvider().get_ohlcv('005930', 20); "
        "assert len(df) == 20, f'expected 20 rows, got {len(df)}'; "
        "expected_cols = {'open', 'high', 'low', 'close', 'volume'}; "
        "assert expected_cols.issubset(df.columns), "
        "f'missing columns: {expected_cols - set(df.columns)}'"
    )
    result = _run_subprocess(script)
    assert result.returncode == 0, (
        f"E2E 실패\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_sample_rulebooks_valid() -> None:
    """샘플 룰북 JSON 3개가 모두 RuleBook.from_dict + validate 통과."""
    script = (
        "import json, pathlib, sys; "
        "sys.path.insert(0, '.'); "
        "from rule_watcher.engine.rule_model import RuleBook; "
        "samples = ['sample_rulebooks/oversold_rebound.json', "
        "'sample_rulebooks/trend_breakout.json', "
        "'sample_rulebooks/volume_surge.json']; "
        "import collections; results = collections.OrderedDict(); "
        "[results.__setitem__(p, RuleBook.from_dict(json.loads(pathlib.Path(p).read_text(encoding='utf-8')))) for p in samples]; "
        "[r.validate() for r in results.values()]; "
        "print('LOADED', len(results))"
    )
    result = _run_subprocess(script)
    assert result.returncode == 0, (
        f"E2E 실패\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "LOADED 3" in result.stdout, (
        f"3개 룰북 로드 안 됨\nstdout: {result.stdout}"
    )


def test_full_screening_pipeline() -> None:
    """전체 선별 파이프라인 E2E — mock → 룰북 → ScreeningEngine."""
    script = (
        "import sys; sys.path.insert(0, '.'); "
        "from rule_watcher.providers.mock_provider import MockProvider; "
        "from rule_watcher.engine.rule_model import RuleBook, RuleGroup, RuleCondition; "
        "from rule_watcher.engine.screening_engine import ScreeningEngine; "
        "provider = MockProvider(); "
        "df = provider.get_ohlcv('005930', 60); "
        "rb = RuleBook(name='e2e', groups=[RuleGroup(name='g', conditions=[RuleCondition(indicator='RSI', operator='<', threshold=100.0, params={'period': 14})])]); "
        "engine = ScreeningEngine(rb); "
        "results = engine.screen_watchlist([{'ticker': '005930', 'name': 'Samsung', 'df': df}]); "
        "matched = [r for r in results if r.matched]; "
        "assert matched, 'No matched results'; "
        "assert matched[0].reason, 'Empty reason'; "
        "print('E2E_OK')"
    )
    result = _run_subprocess(script)
    assert result.returncode == 0, (
        f"E2E 실패\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "E2E_OK" in result.stdout, (
        f"E2E 출력 누락\nstdout: {result.stdout}"
    )


if __name__ == "__main__":
    # 자가 검증
    test_main_py_runs_without_error()
    test_mock_provider_returns_ohlcv()
    test_sample_rulebooks_valid()
    test_full_screening_pipeline()
    print("[SELF-VERIFY] test_rule_watcher_e2e.py OK")
