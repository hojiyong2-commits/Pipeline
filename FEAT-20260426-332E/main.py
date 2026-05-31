"""AFM-Kitting Automation — main orchestration entry point.

Reads yesterday's Outlook kitting email, parses the HTML table,
maps rows to MappedRow format, and writes results into Excel A.
"""

import logging
import sys
from datetime import date
from pathlib import Path

# --- Log Handler Injection (Build Agent mandatory) ---
# Determine log directory: next to EXE when frozen, next to main.py otherwise.
_LOG_DIR: Path = (
    Path(sys.executable).parent
    if getattr(sys, "frozen", False)
    else Path(__file__).parent
)
_LOG_FILE: Path = _LOG_DIR / "app.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(_LOG_FILE, encoding="utf-8"),
    ],
)

logger = logging.getLogger(__name__)


def _compress_line_nos(raw_nos: list) -> str:
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


def _cli_main() -> None:
    """Orchestrate the full AFM-Kitting email-to-Excel pipeline.

    Steps:
    1. Load configuration.
    2. Determine the previous business day (target email date).
    3. Find and read the kitting email from Outlook.
    4. Parse the HTML table into KitRow list.
    5. Map each KitRow to MappedRow (note construction, order line lookup).
    6. Write all MappedRows to Excel A.
    7. Print a summary.

    Raises:
        SystemExit: On any unrecoverable error (exit code 1).
    """
    from core.config_loader import load_config
    from core.business_day import get_previous_business_day, get_next_business_day
    from core.outlook_reader import find_kitting_email, parse_kitting_table
    from core.packing_detail_reader import lookup_packing_dimensions
    from core.order_lines_reader import lookup_order_lines
    from core.excel_mapper import write_to_excel_a
    from core.models import MappedRow

    try:
        # Step 1: Load configuration
        logger.info("Loading configuration...")
        config = load_config()

        packing_detail_path: str = config.get("packing_detail_path", "")
        packing_detail_sheet: str = config.get("packing_detail_sheet", "2021년")
        order_lines_path: str = config.get("order_lines_path", "")
        output_excel_path: str = config.get("output_excel_path", "")
        pm_name_map: dict = config.get("pm_name_map", {})
        excel_a_columns: dict = config.get("excel_a_columns", {})
        excel_b_sheet: str = config.get("excel_b_sheet", "Customer Order Lines")
        excel_b_search_col: str = config.get("excel_b_search_column", "A")
        excel_b_columns: dict = config.get("excel_b_columns", {"fp": "J", "f_col": "F", "jm": "KJ", "if_col": "C", "jy_col": "E"})

        # Step 2: Determine target date (previous business day) and write date (next business day)
        today = date.today()
        target_date = get_previous_business_day(today)
        write_date = get_next_business_day(today)
        logger.info(
            "Today: %s | Target email date: %s | Excel write date: %s",
            today, target_date, write_date,
        )

        # Step 3: Find kitting email in Outlook
        logger.info("Searching Outlook for kitting email on %s...", target_date)
        html_body = find_kitting_email(target_date)
        if html_body is None:
            print("이메일을 찾을 수 없습니다.")
            logger.warning("No kitting email found for %s", target_date)
            sys.exit(0)

        # Step 4: Parse HTML table
        logger.info("Parsing kitting table from email HTML...")
        kit_rows = parse_kitting_table(html_body)
        if len(kit_rows) == 0:
            print("처리할 행이 없습니다.")
            logger.warning("No qualifying rows found in kitting email")
            sys.exit(0)

        logger.info("Found %d qualifying kit rows", len(kit_rows))

        # Step 5: Map each KitRow to MappedRow
        mapped_rows: list = []

        for kit_row in kit_rows:
            customer: str = kit_row["customer"]
            project_id: str = kit_row["project_id"]
            sn: str = kit_row["sn"]
            kit_place: str = kit_row["kit_place"]
            remark: str = kit_row["remark"]

            # Step 5a: Build note string
            note: str
            if "창고" in kit_place:
                note = f"창고 ({remark})"
            elif "포장반" in kit_place:
                try:
                    dims_result = lookup_packing_dimensions(
                        packing_detail_path, sn,
                        sheet_name=packing_detail_sheet,
                    )
                except FileNotFoundError:
                    logger.warning(
                        "Packing detail file not found; skipping dimensions for sn '%s'",
                        sn,
                    )
                    dims_result = None
                except Exception as exc:
                    logger.warning(
                        "Error looking up dimensions for sn '%s': %s", sn, exc
                    )
                    dims_result = None

                if isinstance(dims_result, str):
                    note = f"포장반 ({dims_result})"
                else:  # None — no valid K-date rows found
                    note = "포장반"
            else:
                # kit_place is neither 창고 nor 포장반 — use raw value
                note = kit_place

            # Step 5b: Lookup order lines from Excel B
            try:
                order = lookup_order_lines(
                    order_lines_path,
                    project_id,
                    sheet_name=excel_b_sheet,
                    search_col_letter=excel_b_search_col,
                    fp_col_letter=excel_b_columns.get("fp", "J"),
                    f_col_letter=excel_b_columns.get("f_col", "F"),
                    jm_col_letter=excel_b_columns.get("jm", "G"),
                    if_col_letter=excel_b_columns.get("if_col", "C"),
                    jy_col_letter=excel_b_columns.get("jy_col", "E"),
                    planned_date=kit_row.get("planned_date"),
                )
            except Exception as exc:
                logger.warning("Order lines 조회 실패 '%s': %s", project_id, exc)
                order = {"fp": "", "f_col": "", "jm": "", "if_col": "", "line_nos": [],
                         "f_col_conflict": False, "f_col_all": [], "jy_col": "", "found": False}

            # Skip if not found in CustomerOrderLines
            if not order.get("found"):
                logger.info("스킵: project_id='%s' — CustomerOrderLines 미존재", project_id)
                continue

            # Step 5c: Append Line No suffix to note only when f_col_conflict is True
            line_nos = [str(n) for n in (order.get("line_nos") or [])]  # type: ignore[attr-defined]
            if line_nos and order.get("f_col_conflict", False):
                note = f"{note} #{_compress_line_nos(line_nos)}"

            jm_value: str = str(order.get("jm", "")).strip()
            if not jm_value or jm_value not in pm_name_map:
                logger.info("스킵: project_id='%s' — PM '%s' 매핑 없음", project_id, jm_value)
                continue
            pm_korean: str = pm_name_map[jm_value]

            # MT-3: Contract = F column (Target Date) value directly, no JY fallback
            contract_val: str = str(order.get("f_col", ""))  # allowed: order dict values are Any; str() safe for f_col extraction

            # Step 5d: Build MappedRow
            mapped_row: MappedRow = {
                "month":      today.month,
                "day":        today.day,
                "pm":         pm_korean,
                "sn":         sn,
                "location":   customer,
                "po":         str(order.get("if_col", "")),
                "project_id": project_id,
                "contract":   contract_val,
                "incoterm":   str(order.get("fp", "")),
                "note":       note,
            }
            mapped_rows.append(mapped_row)
            logger.info(
                "Mapped: project_id='%s' location='%s' note='%s'",
                project_id, customer, note,
            )

        # Step 6: Write to Excel A (sorted by PM via write_to_excel_a)
        logger.info(
            "Writing %d rows to Excel A: '%s'", len(mapped_rows), output_excel_path
        )
        write_to_excel_a(
            output_excel_path,
            mapped_rows,
            column_map=excel_a_columns if excel_a_columns else None,
        )

        # Step 7: Print summary
        print(f"완료: {len(mapped_rows)}건 처리됨")
        for row in mapped_rows:
            print(
                f"  project_id={row['project_id']}"
                f"  location={row['location']}"
                f"  note={row['note']}"
            )

    except KeyboardInterrupt:
        print("\n사용자에 의해 중단되었습니다.")
        sys.exit(0)
    except Exception as exc:
        logger.error("Unhandled error in main(): %s", exc, exc_info=True)
        print(f"오류 발생: {exc}")
        sys.exit(1)


def main() -> None:
    """Entry point: launch GUI unless --cli flag is passed.

    With --cli: runs the command-line pipeline (_cli_main).
    Without --cli: launches the Tkinter GUI via ui.app.run_app().
    """
    if "--cli" in sys.argv:
        _cli_main()
    else:
        from ui.app import run_app
        run_app()


if __name__ == "__main__":
    main()
