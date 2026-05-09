"""
scheduler.py -- Scheduler class.

Wraps APScheduler BackgroundScheduler with a weekday (mon-fri) CronTrigger.
Provides start/stop/update_time/is_running lifecycle methods.
"""

import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)


def _validate_hour(hour: Optional[int]) -> None:
    """Validate hour parameter (0-23).

    AL.type_valid 4 items:
    1. None → TypeError
    2. out-of-range → ValueError
    3. non-int (float/str/bool) → TypeError
    4. 0 allowed — hour=0 is midnight, a valid schedule time

    Args:
        hour: The hour value to validate.

    Raises:
        TypeError:  If hour is None or not an int.
        ValueError: If hour is outside 0–23.
    """
    # 1. None check
    if hour is None:
        raise TypeError("hour must not be None")
    # 3. isinstance check — bool is a subclass of int so explicitly excluded
    if isinstance(hour, bool) or not isinstance(hour, int):
        raise TypeError(f"hour must be int, got {type(hour).__name__}")
    # 2. Boundary check — 0 allowed (midnight), negative or >23 invalid
    # 4. 0 allowed — hour=0 represents midnight (valid schedule time)
    if hour < 0 or hour > 23:
        raise ValueError(f"hour must be in range 0–23, got {hour}")


def _validate_minute(minute: Optional[int]) -> None:
    """Validate minute parameter (0-59).

    AL.type_valid 4 items:
    1. None → TypeError
    2. out-of-range → ValueError
    3. non-int (float/str/bool) → TypeError
    4. 0 allowed — minute=0 is on-the-hour, a valid schedule time

    Args:
        minute: The minute value to validate.

    Raises:
        TypeError:  If minute is None or not an int.
        ValueError: If minute is outside 0–59.
    """
    # 1. None check
    if minute is None:
        raise TypeError("minute must not be None")
    # 3. isinstance check — bool is a subclass of int so explicitly excluded
    if isinstance(minute, bool) or not isinstance(minute, int):
        raise TypeError(f"minute must be int, got {type(minute).__name__}")
    # 2. Boundary check — 0 allowed (on-the-hour), negative or >59 invalid
    # 4. 0 allowed — minute=0 represents the start of the hour (valid)
    if minute < 0 or minute > 59:
        raise ValueError(f"minute must be in range 0–59, got {minute}")


class Scheduler:
    """Weekday background scheduler backed by APScheduler.

    Fires job_func at the specified hour:minute on Monday through Friday.
    Thread-safe start/stop/update_time lifecycle.
    """

    _JOB_ID = "daily_automation_job"

    def __init__(self, job_func: Callable, hour: int, minute: int) -> None:
        """Initialise the scheduler with a target callable and fire time.

        The underlying BackgroundScheduler is created here but NOT started.
        Call start() to begin scheduling.

        Args:
            job_func: The callable to invoke on the schedule.
            hour:     Fire hour in 24-hour format (0–23).
            minute:   Fire minute (0–59).

        Raises:
            TypeError:  If job_func is None or not callable; or hour/minute
                        are None or non-int.
            ValueError: If hour or minute are out of range.
        """
        # AL.type_valid for job_func
        if job_func is None:
            raise TypeError("job_func must not be None")
        if not callable(job_func):
            raise TypeError(
                f"job_func must be callable, got {type(job_func).__name__}"
            )

        _validate_hour(hour)
        _validate_minute(minute)

        self._job_func: Callable = job_func
        self._hour: int = hour
        self._minute: int = minute

        try:
            from apscheduler.schedulers.background import BackgroundScheduler  # type: ignore[import]
        except ImportError as exc:
            raise ImportError(
                "apscheduler is required. Install with: pip install apscheduler"
            ) from exc

        self._scheduler: BackgroundScheduler = BackgroundScheduler()
        logger.info(
            "Scheduler: initialised — job will fire weekdays at %02d:%02d",
            hour,
            minute,
        )

    def start(self) -> None:
        """Start the BackgroundScheduler and register the weekday CronTrigger.

        The trigger fires Monday through Friday at the hour:minute specified
        at construction time (or last updated via update_time()).

        Raises:
            RuntimeError: If APScheduler fails to start (propagated from
                          apscheduler).
        """
        from apscheduler.triggers.cron import CronTrigger  # type: ignore[import]

        trigger = CronTrigger(
            day_of_week="mon-fri",
            hour=self._hour,
            minute=self._minute,
        )
        self._scheduler.add_job(
            self._job_func,
            trigger=trigger,
            id=self._JOB_ID,
            replace_existing=True,
        )
        try:
            self._scheduler.start()
        except Exception as exc:
            raise RuntimeError(
                f"Scheduler.start: failed to start BackgroundScheduler: {exc}"
            ) from exc

        logger.info(
            "Scheduler: started — weekdays %02d:%02d", self._hour, self._minute
        )

    def stop(self) -> None:
        """Shut down the BackgroundScheduler without waiting for running jobs.

        Safe to call even if the scheduler is not running.
        """
        try:
            self._scheduler.shutdown(wait=False)
            logger.info("Scheduler: stopped")
        except Exception as exc:
            logger.warning("Scheduler.stop: shutdown raised (ignored): %s", exc)

    def update_time(self, hour: int, minute: int) -> None:
        """Replace the existing job with a new CronTrigger at hour:minute.

        Removes the current job and re-adds it with the updated schedule.
        Does NOT require the scheduler to be restarted.

        Args:
            hour:   New fire hour (0–23).
            minute: New fire minute (0–59).

        Raises:
            TypeError:  If hour or minute are None or non-int.
            ValueError: If hour or minute are out of range.
        """
        _validate_hour(hour)
        _validate_minute(minute)

        from apscheduler.triggers.cron import CronTrigger  # type: ignore[import]

        # Remove existing job if present (silently ignore if absent)
        try:
            self._scheduler.remove_job(self._JOB_ID)
        except Exception:
            pass

        self._hour = hour
        self._minute = minute

        trigger = CronTrigger(
            day_of_week="mon-fri",
            hour=self._hour,
            minute=self._minute,
        )
        self._scheduler.add_job(
            self._job_func,
            trigger=trigger,
            id=self._JOB_ID,
            replace_existing=True,
        )
        logger.info(
            "Scheduler.update_time: rescheduled to weekdays %02d:%02d",
            hour,
            minute,
        )

    def is_running(self) -> bool:
        """Return True if the BackgroundScheduler is currently running.

        Returns:
            bool: True if running, False otherwise.
        """
        try:
            return self._scheduler.running
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Self-verification block
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # --- _validate_hour: valid values ---
    _validate_hour(0)   # boundary: midnight allowed
    _validate_hour(23)  # boundary: max allowed

    # --- _validate_hour: None raises TypeError ---
    try:
        _validate_hour(None)  # type: ignore[arg-type]
        assert False, "Expected TypeError"
    except TypeError:
        pass

    # --- _validate_hour: float raises TypeError ---
    try:
        _validate_hour(10.0)  # type: ignore[arg-type]
        assert False, "Expected TypeError"
    except TypeError:
        pass

    # --- _validate_hour: out of range raises ValueError ---
    try:
        _validate_hour(24)
        assert False, "Expected ValueError"
    except ValueError:
        pass

    # --- _validate_minute: valid values ---
    _validate_minute(0)
    _validate_minute(59)

    # --- _validate_minute: None raises TypeError ---
    try:
        _validate_minute(None)  # type: ignore[arg-type]
        assert False, "Expected TypeError"
    except TypeError:
        pass

    # --- _validate_minute: out of range raises ValueError ---
    try:
        _validate_minute(60)
        assert False, "Expected ValueError"
    except ValueError:
        pass

    # --- Scheduler: bad job_func raises TypeError ---
    try:
        Scheduler(None, 9, 0)  # type: ignore[arg-type]
        assert False, "Expected TypeError"
    except TypeError:
        pass

    try:
        Scheduler("not_callable", 9, 0)  # type: ignore[arg-type]
        assert False, "Expected TypeError"
    except TypeError:
        pass

    # --- Scheduler: invalid hour raises ---
    try:
        Scheduler(lambda: None, -1, 0)
        assert False, "Expected ValueError"
    except ValueError:
        pass

    # --- Scheduler: construct and stop (no apscheduler needed for stop) ---
    sched = Scheduler(lambda: None, 9, 30)
    assert not sched.is_running(), "Should not be running before start()"

    print("[SELF-VERIFY] OK")
