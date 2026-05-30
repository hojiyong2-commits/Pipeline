# [Purpose]: 한국주식 Rule Watcher Streamlit 앱 진입점. `streamlit run main.py` 또는 직접 실행 시 동작.
# [Assumptions]: streamlit이 설치되어 있고 `streamlit run main.py`로 실행. 직접 `python main.py` 호출 시 안내 메시지 출력.
# [Vulnerability & Risks]:
#   - 직접 실행 시 Streamlit 컨텍스트 없이 st.* 호출이 noop이 됨 → 사용자에게 streamlit run 안내 필수.
#   - DB 초기화 실패 시 사용자에게 명확한 에러 메시지 전달 필요.
# [Improvement]: streamlit-cli 자동 invoke (subprocess), 환경변수 자동 검증, --check-only 옵션.
"""한국주식 Rule Watcher — 룰 기반 종목 감시 앱 진입점.

Usage:
    streamlit run main.py

이 앱은 룰 기반 감시 전용이며 매매 추천을 하지 않습니다. 자동 주문 기능도 없습니다.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path


def _setup_logging() -> None:
    """루트 로거 설정 — 파일과 stdout에 모두 기록.

    Returns:
        없음.
    """
    log_dir = Path(__file__).parent
    log_path = log_dir / "rule_watcher.log"

    # Windows cp949 콘솔 호환을 위해 stdout 핸들러에 errors='backslashreplace' 적용
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")  # type: ignore[union-attr]
    except (AttributeError, Exception):  # noqa: BLE001
        # reconfigure 미지원 환경 (e.g., redirected stream)에서는 기본 stream 유지
        pass

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    logging.getLogger(__name__).info(
        "Logging initialised - log file: %s", log_path
    )


def _is_streamlit_runtime() -> bool:
    """현재 프로세스가 `streamlit run`을 통해 실행 중인지 감지.

    Returns:
        True: streamlit runtime context 존재. False: 일반 python 실행.
    """
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
        return get_script_run_ctx() is not None
    except Exception:  # noqa: BLE001
        return False


def main() -> None:
    """앱 진입 — DB 초기화 후 Streamlit UI 실행.

    Returns:
        없음.
    Raises:
        없음. 모든 예외는 사용자 메시지로 안내한 뒤 sys.exit(1).
    """
    _setup_logging()
    logger = logging.getLogger(__name__)

    try:
        from rule_watcher.config import DB_PATH, get_provider_name
        from rule_watcher.db import init_db
    except ImportError as exc:
        print(f"⚠️  필수 모듈 import 실패: {exc}", file=sys.stderr)
        print(
            "   requirements.txt를 설치했는지 확인하세요: pip install -r requirements.txt",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        init_db(DB_PATH)
        logger.info("DB init done - path=%s", DB_PATH)
    except Exception as exc:  # noqa: BLE001
        logger.error("DB init failed: %s", exc)
        print(f"[WARN] DB 초기화 실패: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        provider_name = get_provider_name()
    except Exception as exc:  # noqa: BLE001
        logger.error("provider status query failed: %s", exc)
        provider_name = "mock"

    if provider_name == "mock":
        logger.info("Mock provider mode - KIS API key not set")
    else:
        logger.info("KIS provider mode")

    # streamlit run으로 호출된 경우만 UI 렌더링
    if not _is_streamlit_runtime():
        # ASCII 메시지 — Windows cp949 콘솔 호환을 위해 한글/이모지 회피
        msg = (
            "[INFO] This app must be launched with Streamlit.\n"
            "       Run: streamlit run main.py\n"
            "       Direct `python main.py` will not render the UI."
        )
        try:
            sys.stderr.write(msg + "\n")
        except UnicodeEncodeError:
            sys.stderr.write(msg.encode("ascii", "replace").decode("ascii") + "\n")
        return

    from rule_watcher.ui.app import run_app
    run_app()


if __name__ == "__main__":
    main()
