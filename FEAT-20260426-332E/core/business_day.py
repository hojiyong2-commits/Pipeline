"""Business day utilities for AFM-Kitting automation.

Provides functions to compute the previous business day (Mon→Fri skip).
stdlib only — no external dependencies.
"""

import logging
from datetime import date, timedelta

logger = logging.getLogger(__name__)


def get_previous_business_day(reference_date: date) -> date:
    """Return the previous business day relative to reference_date.

    Rules:
    - Monday   (weekday == 0) → return Friday (3 days back)
    - Sunday   (weekday == 6) → return Friday (2 days back)
    - Saturday (weekday == 5) → return Friday (1 day back)
    - Tue–Fri               → return reference_date - 1 day

    Args:
        reference_date: The date from which to compute the previous business day.
                        Must be a datetime.date instance; None is not allowed.

    Returns:
        The previous business day as a datetime.date.

    Raises:
        TypeError: If reference_date is None or not a datetime.date instance.
    """
    if reference_date is None:
        raise TypeError("reference_date must not be None")
    if not isinstance(reference_date, date):
        raise TypeError(
            f"reference_date must be datetime.date, got {type(reference_date).__name__}"
        )

    weekday = reference_date.weekday()  # 0=Monday … 6=Sunday
    if weekday == 0:
        # Monday → go back to Friday (3 days)
        delta = timedelta(days=3)
    elif weekday == 6:
        # Sunday → go back to Friday (2 days)
        delta = timedelta(days=2)
    else:
        # Tuesday–Saturday → go back 1 day (Saturday-1=Friday is correct)
        delta = timedelta(days=1)

    result = reference_date - delta
    logger.info(
        "Previous business day of %s (weekday=%d) → %s", reference_date, weekday, result
    )
    return result


def get_next_business_day(reference_date: date) -> date:
    """Return the next business day relative to reference_date.

    Rules:
    - Mon–Thu (weekday 0–3) → reference_date + 1 day
    - Fri (weekday 4)       → reference_date + 3 days (→ Monday)
    - Sat (weekday 5)       → reference_date + 2 days (→ Monday)
    - Sun (weekday 6)       → reference_date + 1 day  (→ Monday)

    Args:
        reference_date: Must be datetime.date; None not allowed.

    Returns:
        Next business day as datetime.date.

    Raises:
        TypeError: If reference_date is None or not datetime.date.
    """
    # AL type guards
    if reference_date is None:
        raise TypeError("reference_date must not be None")
    if not isinstance(reference_date, date):
        raise TypeError(
            f"reference_date must be datetime.date, got {type(reference_date).__name__}"
        )

    weekday = reference_date.weekday()  # 0=Mon … 6=Sun
    if weekday == 4:
        # Friday → next Monday (+3); negative not allowed: forward-only
        delta = timedelta(days=3)
    elif weekday == 5:
        # Saturday → next Monday (+2); negative not allowed: forward-only
        delta = timedelta(days=2)
    else:
        # Mon/Tue/Wed/Thu/Sun → next day (+1); negative not allowed: forward-only
        delta = timedelta(days=1)

    result = reference_date + delta
    logger.info(
        "Next business day of %s (weekday=%d) → %s", reference_date, weekday, result
    )
    return result


if __name__ == "__main__":
    from datetime import date as d

    # Monday → Friday
    monday = d(2026, 4, 27)  # Monday
    assert monday.weekday() == 0, "Test date is not Monday"
    assert get_previous_business_day(monday) == d(2026, 4, 24), "Monday→Friday failed"

    # Tuesday → Monday
    tuesday = d(2026, 4, 28)
    assert get_previous_business_day(tuesday) == d(2026, 4, 27), "Tuesday→Monday failed"

    # Wednesday → Tuesday
    wednesday = d(2026, 4, 29)
    assert get_previous_business_day(wednesday) == d(2026, 4, 28), "Wed→Tue failed"

    # None input → TypeError
    try:
        get_previous_business_day(None)  # type: ignore[arg-type]
        assert False, "Expected TypeError not raised"
    except TypeError:
        pass

    # Bad type → TypeError
    try:
        get_previous_business_day("2026-04-27")  # type: ignore[arg-type]
        assert False, "Expected TypeError not raised"
    except TypeError:
        pass

    # get_next_business_day tests
    thu = d(2026, 4, 30)   # Thursday
    assert get_next_business_day(thu) == d(2026, 5, 1), "Thu→Fri failed"

    fri = d(2026, 5, 1)    # Friday
    assert get_next_business_day(fri) == d(2026, 5, 4), "Fri→Mon failed"

    sat = d(2026, 5, 2)    # Saturday
    assert get_next_business_day(sat) == d(2026, 5, 4), "Sat→Mon failed"

    sun = d(2026, 5, 3)    # Sunday
    assert get_next_business_day(sun) == d(2026, 5, 4), "Sun→Mon failed"

    mon = d(2026, 4, 27)   # Monday
    assert get_next_business_day(mon) == d(2026, 4, 28), "Mon→Tue failed"

    try:
        get_next_business_day(None)  # type: ignore[arg-type]
        assert False, "Expected TypeError not raised"
    except TypeError:
        pass

    print("[SELF-VERIFY] business_day.py OK")
