"""
email_composer.py — EmailComposer class.

Builds the email subject and body from templates stored in AppConfig,
substituting {date} and {data} placeholders.
No network I/O; pure in-memory string processing.
"""

import html
import logging
import re
from datetime import date
from typing import Any, Dict, List, Optional

from core.config_manager import AppConfig, ExcelReadResult

logger = logging.getLogger(__name__)

_PLACEHOLDER_DATE = "{date}"
_PLACEHOLDER_DATA = "{data}"
_EMPTY_DATA_FALLBACK = "[데이터 없음]"

# SEC-005: Email address validation constants
_EMAIL_RE = re.compile(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$')

# SEC-007: Maximum lengths for composed subject and body strings
_MAX_SUBJECT_LEN = 255
_MAX_BODY_LEN = 1_000_000
_MAX_EMAIL_LEN = 254
_MAX_RECIPIENTS = 50


class EmailComposer:
    """Composes email content (subject, body, recipients) from AppConfig templates.

    AL-01 compliance:
    - {date} is replaced with today's date in YYYY-MM-DD format.
    - {data} is replaced with the Excel data text; an empty string becomes
      the fallback string "[데이터 없음]".
    """

    def __init__(self, config: AppConfig) -> None:
        """Initialise with the application configuration.

        Args:
            config: The loaded AppConfig instance.
        """
        self._config = config

    def compose(
        self,
        excel_data: ExcelReadResult,
        highlight_ranges: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        """Build the email content dict from templates and Excel data.

        Args:
            excel_data:       The result returned by ExcelHandler.read_cells().
            highlight_ranges: Optional list of highlight range dicts, each containing
                              "start", "end" (Tkinter index strings) and "color"
                              (6-char HEX without '#').  When None or empty, no span
                              tags are emitted in the HTML body.

        Returns:
            A dict with keys:
                "subject"   (str),
                "body"      (str)  — plain-text body,
                "html_body" (str)  — HTML body with optional highlight spans,
                "to"        (List[str]),
                "cc"        (List[str]).
        """
        today_str: str = date.today().strftime("%Y-%m-%d")

        # Resolve the data text with EC-03 fallback
        raw_text: str = excel_data.get("data_as_text", "") or ""
        data_text: str = raw_text if raw_text.strip() else _EMPTY_DATA_FALLBACK

        subject_template: str = self._config.get(
            "email_subject_template", "[일일보고] {date}"
        )
        body_template: str = self._config.get(
            "email_body_template", "일자: {date}\n\n내용:\n{data}"
        )

        subject: str = self._substitute(subject_template, today_str, data_text)
        body: str = self._substitute(body_template, today_str, data_text)

        # SEC-007: enforce maximum lengths on composed subject and body
        if len(subject) > _MAX_SUBJECT_LEN:
            logger.warning(
                "compose: subject truncated from %d to %d chars",
                len(subject),
                _MAX_SUBJECT_LEN,
            )
            subject = subject[:_MAX_SUBJECT_LEN]

        if len(body) > _MAX_BODY_LEN:
            logger.warning(
                "compose: body truncated from %d to %d chars",
                len(body),
                _MAX_BODY_LEN,
            )
            body = body[:_MAX_BODY_LEN]

        # Build HTML body (plain body is already resolved above).
        html_body: str = self._build_html_body(body, highlight_ranges)

        to_list: List[str] = self._clean_recipients(
            self._config.get("to_recipients", [])
        )
        cc_list: List[str] = self._clean_recipients(
            self._config.get("cc_recipients", [])
        )

        logger.debug(
            "compose: subject='%s', to=%s, cc=%s", subject, to_list, cc_list
        )

        return {
            "subject": subject,
            "body": body,
            "html_body": html_body,
            "to": to_list,
            "cc": cc_list,
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _tk_index_to_offset(text: str, tk_index: str) -> int:
        """Convert a Tkinter text index (LINE.CHAR) to a flat character offset.

        Tkinter convention:
            LINE is 1-based (first line = 1).
            CHAR is 0-based (first character of a line = 0).

        Out-of-bounds indices are clamped to valid positions:
            - LINE < 1 → clamped to line 1.
            - LINE > total lines → clamped to last line.
            - CHAR > line length → clamped to end of that line.

        Args:
            text:     The full plain-text string (may contain '\\n' line separators).
            tk_index: A Tkinter index string in "LINE.CHAR" format.

        Returns:
            The corresponding integer character offset within ``text``.
            Returns 0 for any malformed ``tk_index`` that cannot be parsed.

        Example:
            >>> EmailComposer._tk_index_to_offset("hello\\nworld", "2.3")
            9
        """
        if not isinstance(tk_index, str) or "." not in tk_index:
            logger.warning("_tk_index_to_offset: invalid index format '%s'", tk_index)
            return 0

        parts: List[str] = tk_index.split(".", 1)
        try:
            line_num: int = int(parts[0])
            char_num: int = int(parts[1])
        except ValueError:
            logger.warning(
                "_tk_index_to_offset: cannot parse index '%s' as int pair", tk_index
            )
            return 0

        lines: List[str] = text.split("\n")
        total_lines: int = len(lines)

        # Clamp LINE (1-based)
        if line_num < 1:
            line_num = 1
        if line_num > total_lines:
            line_num = total_lines

        # Compute flat offset: sum of all preceding lines including their '\n'
        offset: int = 0
        for i in range(line_num - 1):
            offset += len(lines[i]) + 1  # +1 for the '\n' separator

        # Clamp CHAR (0-based)
        line_content: str = lines[line_num - 1]
        if char_num < 0:
            char_num = 0
        if char_num > len(line_content):
            char_num = len(line_content)

        return offset + char_num

    def _build_html_body(
        self,
        plain_body: str,
        highlight_ranges: Optional[List[Dict[str, str]]],
    ) -> str:
        """Construct an HTML body from a plain-text body with optional highlight spans.

        Processing order:
            1. Placeholder substitution has already been applied to ``plain_body``.
            2. html.escape() is applied to the whole plain_body string.
            3. Newlines (\\n) are converted to <br> tags.
            4. Highlight spans are inserted at the correct byte offsets (computed
               on the *pre-escape* plain_body to match Tkinter char positions).

        Span format:
            ``<span style="background-color: #RRGGBB;">text</span>``

        Args:
            plain_body:       The fully-substituted plain-text body.
            highlight_ranges: List of dicts with keys "start", "end" (Tkinter
                              indices), and "color" (6-char HEX without '#').
                              None or [] → no spans emitted.

        Returns:
            A complete HTML body string suitable for mail.HTMLBody.
        """
        _HEX_RE = re.compile(r"^[0-9A-Fa-f]{6}$")

        # ── Step 1: build sorted, valid range list ────────────────────────────
        valid_ranges: List[Dict[str, Any]] = []
        if highlight_ranges:
            for rng in highlight_ranges:
                if not isinstance(rng, dict):
                    continue
                color: Any = rng.get("color", "")
                start_idx: Any = rng.get("start", "")
                end_idx: Any = rng.get("end", "")
                if not isinstance(color, str) or not _HEX_RE.match(color):
                    logger.warning(
                        "_build_html_body: invalid color '%s' — skipping range", color
                    )
                    continue
                if not isinstance(start_idx, str) or not isinstance(end_idx, str):
                    continue
                start_off: int = self._tk_index_to_offset(plain_body, start_idx)
                end_off: int = self._tk_index_to_offset(plain_body, end_idx)
                if start_off >= end_off:
                    logger.warning(
                        "_build_html_body: start_off(%d) >= end_off(%d) — skipping",
                        start_off,
                        end_off,
                    )
                    continue
                valid_ranges.append(
                    {
                        "start": start_off,
                        "end": end_off,
                        "color": color.upper(),
                    }
                )
            # Sort by start offset; earlier ranges processed first
            valid_ranges.sort(key=lambda r: r["start"])

        # ── Step 2: split plain_body at range boundaries ──────────────────────
        # Collect boundary positions and map each segment to a color (or None).
        if not valid_ranges:
            # Fast path: no highlights — just escape + br conversion.
            return html.escape(plain_body).replace("\n", "<br>")

        # Build list of (start, end, color_or_None) non-overlapping segments.
        # Overlapping ranges: first (by start offset) wins.
        segments: List[Dict[str, Any]] = []
        cursor: int = 0
        text_len: int = len(plain_body)

        for rng in valid_ranges:
            s: int = rng["start"]
            e: int = rng["end"]
            # If this range starts before cursor it overlaps a previous one — skip.
            if s < cursor:
                logger.warning(
                    "_build_html_body: overlapping range [%d,%d) skipped", s, e
                )
                continue
            # Gap before this range
            if cursor < s:
                segments.append({"text": plain_body[cursor:s], "color": None})
            segments.append({"text": plain_body[s:e], "color": rng["color"]})
            cursor = e

        # Trailing segment after last range
        if cursor < text_len:
            segments.append({"text": plain_body[cursor:], "color": None})

        # ── Step 3: escape each segment and wrap spans ────────────────────────
        parts: List[str] = []
        for seg in segments:
            escaped: str = html.escape(seg["text"]).replace("\n", "<br>")
            if seg["color"] is not None:
                parts.append(
                    f'<span style="background-color: #{seg["color"]};">{escaped}</span>'
                )
            else:
                parts.append(escaped)

        return "".join(parts)

    def _substitute(self, template: str, today_str: str, data_text: str) -> str:
        """Replace {date} and {data} placeholders in a template string.

        Args:
            template:   The raw template string from AppConfig.
            today_str:  The formatted today date (YYYY-MM-DD).
            data_text:  The resolved data text (never empty at this point).

        Returns:
            The fully substituted string.
        """
        if not isinstance(template, str):
            logger.warning("_substitute: template is not a string — using empty string")
            template = ""
        result = template.replace(_PLACEHOLDER_DATE, today_str)
        result = result.replace(_PLACEHOLDER_DATA, data_text)
        return result

    def _clean_recipients(self, recipients: Any) -> List[str]:
        """Validate and clean a recipient list.

        Filters out non-string entries, empty strings, addresses exceeding
        _MAX_EMAIL_LEN (254), addresses that fail RFC-5321 regex validation,
        and stops processing once _MAX_RECIPIENTS (50) valid entries are found.

        Args:
            recipients: Expected to be a List[str]; accepts any type defensively.

        Returns:
            A list of validated, non-empty stripped email address strings.
        """
        if not isinstance(recipients, list):
            logger.warning(
                "_clean_recipients: expected list, got %s — returning empty",
                type(recipients).__name__,
            )
            return []
        cleaned: List[str] = []
        for item in recipients:
            if not isinstance(item, str):
                continue
            stripped = item.strip()
            if not stripped:
                continue
            if len(stripped) > _MAX_EMAIL_LEN:
                logger.warning(
                    "_clean_recipients: address exceeds %d chars — skipped",
                    _MAX_EMAIL_LEN,
                )
                continue
            if not _EMAIL_RE.match(stripped):
                logger.warning(
                    "_clean_recipients: invalid email format — skipped"
                )
                continue
            cleaned.append(stripped)
            if len(cleaned) >= _MAX_RECIPIENTS:
                logger.warning(
                    "_clean_recipients: recipient limit (%d) reached — stopping",
                    _MAX_RECIPIENTS,
                )
                break
        return cleaned
