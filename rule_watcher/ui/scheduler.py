# [Purpose]: 백그라운드 스케줄러 — 일정 간격으로 refresh_fn을 호출하여 시세/선별을 갱신.
# [Assumptions]: threading.Thread(daemon=True)로 동작. Streamlit 메인 스레드와 별개로 실행.
# [Vulnerability & Risks]:
#   - daemon thread는 인터프리터 종료 시 자동 종료되지만, 외부 리소스(파일/HTTP) 정리는 보장되지 않음.
#   - refresh_fn 내부 예외는 로그로만 남기고 무시 — 무한 루프 방지를 위해 의도된 동작.
# [Improvement]: APScheduler 도입, 작업별 cron 표현식, 우선순위 큐, 실패 카운터 + circuit breaker.
"""백그라운드 데이터 갱신 스케줄러 (v1)."""
from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class AppScheduler:
    """주기적으로 refresh_fn을 호출하는 간단한 스케줄러.

    v1: Streamlit의 st.rerun() 기반 갱신을 보조하는 데몬 스레드.
    실패한 refresh_fn 예외는 로그만 남기고 다음 주기에 재시도한다.
    """

    def __init__(self, interval_sec: int = 60) -> None:
        """스케줄러 초기화.

        Args:
            interval_sec: refresh 호출 간격(초). 양의 정수여야 함.
        Raises:
            TypeError: interval_sec이 None이거나 int가 아닌 경우.
            ValueError: interval_sec이 0 이하인 경우.
        """
        if interval_sec is None:
            raise TypeError("interval_sec must not be None")
        if not isinstance(interval_sec, int) or isinstance(interval_sec, bool):
            raise TypeError(
                f"interval_sec must be int, got {type(interval_sec).__name__}"
            )
        if interval_sec <= 0:
            # negative not allowed: 음수/0 간격은 무한 루프 또는 즉시 반복 위험
            raise ValueError(
                f"interval_sec must be positive, got {interval_sec}"
            )

        self._interval: int = interval_sec
        self._thread: Optional[threading.Thread] = None
        self._stop_event: threading.Event = threading.Event()
        self._refresh_fn: Optional[Callable[[], None]] = None
        self._running: bool = False
        self._lock: threading.Lock = threading.Lock()

    def set_refresh_fn(self, fn: Callable[[], None]) -> None:
        """갱신 함수를 등록한다.

        Args:
            fn: 인자 없이 호출 가능한 callable. None을 넘기면 등록 해제.
        Raises:
            TypeError: fn이 None도 callable도 아닌 경우.
        """
        if fn is not None and not callable(fn):
            raise TypeError(f"fn must be callable or None, got {type(fn).__name__}")
        with self._lock:
            self._refresh_fn = fn

    def start(self) -> None:
        """스케줄러 백그라운드 스레드 시작. 이미 실행 중이면 no-op."""
        with self._lock:
            if self._running:
                logger.debug("AppScheduler.start: already running")
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run, daemon=True, name="rule-watcher-scheduler"
            )
            self._thread.start()
            self._running = True
            logger.info("AppScheduler started (interval=%ds)", self._interval)

    def stop(self) -> None:
        """스케줄러 정지. 최대 5초 join 대기."""
        with self._lock:
            self._stop_event.set()
            thread = self._thread
            self._running = False
        if thread is not None:
            thread.join(timeout=5)
            logger.info("AppScheduler stopped")

    def _run(self) -> None:
        """백그라운드 루프 — stop_event가 set되면 종료.

        wait(self._interval)는 timeout 경과 시 False를 반환하므로
        그 사이에만 refresh_fn을 호출한다.
        """
        # MAX_ITER 상한 — 비정상 빠른 interval 또는 외부 시간 조작 방어
        MAX_ITER = 10_000_000
        iter_count = 0
        while not self._stop_event.wait(self._interval):
            iter_count += 1
            if iter_count > MAX_ITER:
                logger.error(
                    "AppScheduler: MAX_ITER=%d 초과로 루프 종료 (안전장치)", MAX_ITER
                )
                with self._lock:
                    self._running = False
                return
            with self._lock:
                fn = self._refresh_fn
            if fn is None:
                continue
            try:
                fn()
            except Exception as exc:  # noqa: BLE001
                # refresh 실패는 다음 주기에 재시도 — 무한 루프 방지를 위해 swallow
                logger.warning("AppScheduler.refresh_fn 호출 실패: %s", exc)

    @property
    def running(self) -> bool:
        """현재 스레드 실행 여부."""
        return self._running

    @property
    def interval_sec(self) -> int:
        """현재 갱신 간격(초)."""
        return self._interval


if __name__ == "__main__":
    # SELF-VERIFY 블록 — 단순 기본 동작 검증
    import time

    counter = {"n": 0}

    def _tick() -> None:
        counter["n"] += 1

    sched = AppScheduler(interval_sec=1)
    sched.set_refresh_fn(_tick)

    # 음수/None 검증
    try:
        AppScheduler(interval_sec=0)
        assert False, "interval_sec=0이 예외를 발생시키지 않음"
    except ValueError:
        pass
    try:
        AppScheduler(interval_sec=None)  # type: ignore[arg-type]
        assert False, "interval_sec=None이 예외를 발생시키지 않음"
    except TypeError:
        pass
    try:
        sched.set_refresh_fn("not_callable")  # type: ignore[arg-type]
        assert False, "fn=str이 예외를 발생시키지 않음"
    except TypeError:
        pass

    sched.start()
    time.sleep(2.5)
    sched.stop()
    assert counter["n"] >= 1, f"refresh_fn이 한 번도 호출되지 않음 (n={counter['n']})"
    # ASCII 출력 — Windows cp949 콘솔 호환
    print(f"[SELF-VERIFY] AppScheduler OK - refresh call count={counter['n']}")
