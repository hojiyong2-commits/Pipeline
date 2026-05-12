"""Shared utility functions for KittingMapper core modules."""

from typing import List


def _compress_line_nos(raw_nos: List[object]) -> str:
    """Deduplicate, sort, and compress consecutive line numbers using '~' range notation.

    Examples:
        [1, 1, 2, 3] -> "1~3"
        [1, 2, 3, 5, 6, 7, 8, 11] -> "1~3,5~8,11"
        [] -> ""

    Args:
        raw_nos: Raw list of line number values (str or numeric).

    Returns:
        Compressed string representation, or empty string for empty input.
    """
    try:
        nums = sorted(set(int(float(str(n).strip())) for n in raw_nos if str(n).strip()))
    except (ValueError, TypeError):
        return ",".join(str(n) for n in raw_nos)
    if not nums:
        return ""
    ranges = []
    start = end = nums[0]
    for n in nums[1:]:
        if n == end + 1:
            end = n
        else:
            ranges.append(str(start) if start == end else f"{start}~{end}")
            start = end = n
    ranges.append(str(start) if start == end else f"{start}~{end}")
    return ",".join(ranges)
