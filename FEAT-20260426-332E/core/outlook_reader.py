"""Outlook COM reader for AFM-Kitting Notice emails.

Connects to a running Outlook instance via win32com and reads kitting email HTML.
Parses the embedded HTML table using BeautifulSoup with lxml parser.
"""

import logging
from datetime import date, timedelta
from typing import Optional

# win32timezone must be imported at module level so PyInstaller's static
# analyser bundles it into the frozen EXE. pywin32 loads it via dynamic/
# delayed imports at runtime, which the bundler cannot see otherwise.
try:
    import win32timezone  # noqa: F401  # type: ignore[import]
except ImportError:
    pass  # Non-Windows environments without pywin32 — safe to skip

from bs4 import BeautifulSoup, Tag

try:
    from .models import KitRow
except ImportError:
    # Fallback when run directly as __main__ (not as part of the package)
    import sys as _sys_early
    import os.path as _osp_early
    _sys_early.path.insert(0, _osp_early.dirname(_osp_early.dirname(_osp_early.abspath(__file__))))
    from core.models import KitRow  # type: ignore[no-redef]

logger = logging.getLogger(__name__)

# Column header names to search for (case-insensitive)
_COL_CUSTOMER = "customer"
_COL_PROJECT_ID = "project id"
_COL_SN = "s/n"
_COL_KIT_PLACE = "kit place"
_COL_REMARK = "remark"
_COL_PLANNED_DATE = "planned date"

# Customers to skip (case-insensitive, stripped)
_SKIP_CUSTOMERS = {"local", "warranty"}


def find_kitting_email(target_date: date) -> Optional[str]:
    """Search Outlook Inbox for AFM-Kitting Notice email received on target_date.

    Uses win32com.client to connect to a running (or newly launched) Outlook
    instance. Filters Inbox items by ReceivedTime within target_date using a
    DASL restrict query, then checks the subject.

    Args:
        target_date: The date on which the email was received. Must not be None.

    Returns:
        HTML body string of the first matching email, or None if not found.

    Raises:
        TypeError: If target_date is None or not a datetime.date instance.
        RuntimeError: If the Outlook COM object cannot be created or accessed.
    """
    if target_date is None:
        raise TypeError("target_date must not be None")
    if not isinstance(target_date, date):
        raise TypeError(
            f"target_date must be datetime.date, got {type(target_date).__name__}"
        )

    logger.info("Searching Outlook Inbox for kitting email on %s", target_date)

    try:
        import win32com.client  # type: ignore[import]
    except ImportError as exc:
        raise RuntimeError(
            "win32com.client is not available. Install pywin32."
        ) from exc

    try:
        outlook = win32com.client.Dispatch("Outlook.Application")
        namespace = outlook.GetNamespace("MAPI")
        inbox = namespace.GetDefaultFolder(6)  # 6 = olFolderInbox
        items = inbox.Items
    except Exception as exc:
        logger.error("Failed to access Outlook Inbox via COM: %s", exc)
        raise RuntimeError(
            f"Cannot connect to Outlook via COM: {exc}"
        ) from exc

    # Build DASL date-range filter for the target day (entire day)
    # Outlook DASL uses UTC ISO-8601; we use the local date boundaries
    start_str = target_date.strftime("%m/%d/%Y 00:00 AM")
    next_day = target_date + timedelta(days=1)
    end_str = next_day.strftime("%m/%d/%Y 00:00 AM")

    # DASL filter for ReceivedTime
    dasl_filter = (
        f"@SQL=\"urn:schemas:httpmail:datereceived\" >= '{start_str}' AND "
        f"\"urn:schemas:httpmail:datereceived\" < '{end_str}'"
    )

    try:
        restricted = items.Restrict(dasl_filter)
    except Exception as exc:
        logger.warning(
            "DASL restrict failed (%s); falling back to full Inbox scan", exc
        )
        restricted = items  # fallback: scan all

    keyword = "AFM-Kitting Notice dated on"

    try:
        for item in restricted:
            try:
                subject: str = str(item.Subject) if item.Subject else ""
                received_time = item.ReceivedTime
                # Verify date matches when fallback scan is used
                if received_time is not None:
                    # ReceivedTime is a pywintypes.datetime; compare date part
                    rec_date = date(
                        received_time.year,
                        received_time.month,
                        received_time.day,
                    )
                    if rec_date != target_date:
                        continue
                if keyword in subject:
                    html_body: str = str(item.HTMLBody) if item.HTMLBody else ""
                    logger.info(
                        "Found kitting email: subject='%s'", subject
                    )
                    return html_body
            except Exception as item_exc:
                logger.warning("Error reading mail item: %s", item_exc)
                continue
    except Exception as exc:
        logger.error("Error iterating Outlook items: %s", exc)
        raise RuntimeError(f"Error iterating Outlook Inbox items: {exc}") from exc

    logger.info("No kitting email found for date %s", target_date)
    return None


def _find_header_indices(header_row: Tag) -> dict:
    """Map column names to their zero-based index from a table header row.

    Args:
        header_row: A BeautifulSoup Tag representing a <tr> element.

    Returns:
        Dict mapping normalised header names to column index integers.
    """
    indices: dict = {}
    cells = header_row.find_all(["th", "td"])
    for idx, cell in enumerate(cells):
        text = cell.get_text(strip=True).lower()
        indices[text] = idx
    return indices


def parse_kitting_table(html_body: str) -> list:
    """Parse the AFM-Kitting HTML email body and extract kitting rows.

    Finds the kitting table by scanning all tables for one whose header row
    contains "kit place" and "project id" columns. Extracts rows excluding
    LOCAL and warranty customers.

    Args:
        html_body: Full HTML body string of the kitting email.
                   Must not be None.

    Returns:
        List of KitRow TypedDicts. Empty list if the table is not found
        or no qualifying rows exist.

    Raises:
        TypeError: If html_body is None or not a string.
        ValueError: If html_body is an empty string.
    """
    if html_body is None:
        raise TypeError("html_body must not be None")
    if not isinstance(html_body, str):
        raise TypeError(
            f"html_body must be str, got {type(html_body).__name__}"
        )
    if len(html_body.strip()) == 0:
        raise ValueError("html_body must not be empty")

    logger.info("Parsing kitting HTML body (%d chars)", len(html_body))

    soup = BeautifulSoup(html_body, "lxml")

    # Strategy 1: find table by required column headers (kit place + project id)
    table = _find_table_by_headers(soup)

    if table is None:
        logger.warning(
            "Could not locate kitting table by headers; trying first table fallback"
        )
        # Fallback: use the first table in the document
        table = soup.find("table")  # type: ignore[assignment]

    if table is None:
        logger.warning("No table found in HTML body")
        return []

    rows = table.find_all("tr")
    if not rows:
        logger.warning("Table found but contains no rows")
        return []

    # First row is header
    header_indices = _find_header_indices(rows[0])  # type: ignore[arg-type]
    logger.info("Table headers: %s", header_indices)

    # Resolve required column indices
    def _get_col(name: str) -> Optional[int]:
        """Return column index for header name (case-insensitive)."""
        return header_indices.get(name.lower())

    col_customer = _get_col(_COL_CUSTOMER)
    col_project_id = _get_col(_COL_PROJECT_ID)
    col_sn = _get_col(_COL_SN)
    col_kit_place = _get_col(_COL_KIT_PLACE)
    col_remark = _get_col(_COL_REMARK)
    col_planned_date = _get_col(_COL_PLANNED_DATE)

    if any(c is None for c in [col_customer, col_project_id, col_sn]):
        logger.error(
            "Required columns not found. headers=%s", header_indices
        )
        return []

    kit_rows: list = []

    for row in rows[1:]:
        cells = row.find_all(["td", "th"])  # type: ignore[union-attr]
        if not cells:
            continue

        def _cell_text(idx: Optional[int]) -> str:
            """Safely extract cell text by index."""
            if idx is None or idx >= len(cells):
                return ""
            return cells[idx].get_text(strip=True)

        customer = _cell_text(col_customer)
        customer_lower = customer.strip().lower()

        # Skip LOCAL, warranty, and empty customer rows
        if not customer_lower or customer_lower in _SKIP_CUSTOMERS:
            logger.info("Skipping row: customer='%s'", customer)
            continue

        kit_row: KitRow = {
            "customer": customer,
            "project_id": _cell_text(col_project_id),
            "sn": _cell_text(col_sn),
            "kit_place": _cell_text(col_kit_place) if col_kit_place is not None else "",
            "remark": _cell_text(col_remark) if col_remark is not None else "",
            "planned_date": _cell_text(col_planned_date) if col_planned_date is not None else "",
        }
        kit_rows.append(kit_row)
        logger.info(
            "Parsed row: project_id='%s' customer='%s'",
            kit_row["project_id"],
            kit_row["customer"],
        )

    logger.info("Parsed %d qualifying rows", len(kit_rows))
    return kit_rows


def _find_table_by_headers(soup: BeautifulSoup) -> Optional[Tag]:
    """Find the kitting table by scanning all <table> elements for required column headers.

    Scans every table in the document. Returns the first table whose header row
    (first <tr>) contains both 'kit place' AND 'project id' (case-insensitive).
    These two columns are unique to the kitting table and cannot appear together
    in any other table in the email.

    Args:
        soup: Parsed BeautifulSoup document.

    Returns:
        The matching <table> Tag, or None if not found.
    """
    for tbl in soup.find_all("table"):
        rows = tbl.find_all("tr")  # type: ignore[union-attr]
        if not rows:
            continue
        header_text = {cell.get_text(strip=True).lower() for cell in rows[0].find_all(["th", "td"])}  # type: ignore[union-attr]
        if "kit place" in header_text and "project id" in header_text:
            return tbl  # type: ignore[return-value]
    return None


if __name__ == "__main__":
    import sys as _sys
    import os.path as _osp
    # Allow running directly: add package parent to sys.path
    _sys.path.insert(0, _osp.dirname(_osp.dirname(_osp.abspath(__file__))))
    from core.models import KitRow  # noqa: F811 — override relative import for __main__

    # Self-verification: parse a synthetic HTML body
    sample_html = """
    <html><body>
    <p>안녕하세요. 아래와 같이 Kitting 되었습니다.</p>
    <table>
      <tr>
        <th>Customer</th><th>Project ID</th><th>S/N</th>
        <th>Kit Place</th><th>Remark</th>
      </tr>
      <tr>
        <td>ACME Corp</td><td>P100</td><td>SN-001</td>
        <td>창고</td><td>fragile</td>
      </tr>
      <tr>
        <td>LOCAL</td><td>P200</td><td>SN-002</td>
        <td>창고</td><td>skip me</td>
      </tr>
      <tr>
        <td>warranty</td><td>P300</td><td>SN-003</td>
        <td>포장반</td><td>also skip</td>
      </tr>
      <tr>
        <td>Beta Inc</td><td>P400</td><td>SN-004</td>
        <td>포장반</td><td>ok</td>
      </tr>
    </table>
    </body></html>
    """

    rows = parse_kitting_table(sample_html)
    assert len(rows) == 2, f"Expected 2 rows, got {len(rows)}"
    assert rows[0]["customer"] == "ACME Corp", "Customer mismatch"
    assert rows[0]["kit_place"] == "창고", "Kit place mismatch"
    assert rows[1]["project_id"] == "P400", "Project ID mismatch"

    # None input → TypeError
    try:
        parse_kitting_table(None)  # type: ignore[arg-type]
        assert False, "Expected TypeError"
    except TypeError:
        pass

    # Empty string → ValueError
    try:
        parse_kitting_table("   ")
        assert False, "Expected ValueError"
    except ValueError:
        pass

    # Test: header-based lookup finds the SECOND table (not the first)
    two_table_html = """
    <html><body>
    <table><tr><td>Unrelated header</td></tr><tr><td>data</td></tr></table>
    <table>
      <tr><th>S/N</th><th>Kit Place</th><th>Planned Date</th><th>Remark</th><th>Project ID</th><th>Customer</th></tr>
      <tr><td>1</td><td>포장반</td><td>2026-05-01</td><td></td><td>T001</td><td>SG</td></tr>
    </table>
    </body></html>
    """
    rows2 = parse_kitting_table(two_table_html)
    assert len(rows2) == 1, f"Expected 1 row from second table, got {len(rows2)}"
    assert rows2[0]["project_id"] == "T001", f"Wrong project_id: {rows2[0]['project_id']}"
    assert rows2[0]["customer"] == "SG", f"Wrong customer: {rows2[0]['customer']}"

    print("[SELF-VERIFY] outlook_reader.py OK")
