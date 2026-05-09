"""
outlook_sender.py — OutlookSender class.

Sends email through the local Outlook COM automation interface using pywin32.
Only usable on Windows with Outlook installed.
"""

import logging
from typing import Any, Dict, List, Optional

from core.config_manager import SendResult

logger = logging.getLogger(__name__)


def _mask_email(addr: str) -> str:
    """Mask an email address for safe logging.

    Keeps only the first 2 characters of the local part and replaces
    the rest with '*'. Returns '***' if the address contains no '@'.

    Args:
        addr: The raw email address string.

    Returns:
        A masked representation suitable for log output.
    """
    if not isinstance(addr, str) or "@" not in addr:
        return "***"
    local, domain = addr.split("@", 1)
    visible = local[:2]
    masked_local = visible + "*" * max(0, len(local) - 2)
    return f"{masked_local}@{domain}"


class OutlookSender:
    """Sends email via the Outlook COM interface (pywin32 / win32com.client).

    Raises ImportError on construction if pywin32 is not installed, so the
    caller learns immediately rather than at send-time.
    """

    def __init__(self) -> None:
        """Import win32com.client eagerly; raise ImportError with a clear message if absent."""
        try:
            import win32com.client  # type: ignore[import]
            self._win32com = win32com.client
            logger.debug("OutlookSender: win32com.client loaded successfully")
        except ImportError as exc:
            raise ImportError(
                "pywin32 is required for Outlook COM automation. "
                "Install it with: pip install pywin32>=305"
            ) from exc

    def send(self, email_content: Dict[str, Any]) -> SendResult:
        """Create and send an Outlook MailItem via COM.

        Args:
            email_content: Dict with keys "subject" (str), "body" (str),
                           "to" (List[str]), "cc" (List[str]).

        Returns:
            SendResult with success=True on success, or success=False and
            error_msg populated on failure.
        """
        subject: str = email_content.get("subject", "") or ""
        body: str = email_content.get("body", "") or ""
        to_list: List[str] = email_content.get("to", []) or []
        cc_list: List[str] = email_content.get("cc", []) or []

        # SEC-005 defense_recommendations: strip header-injection characters
        sanitized_subject: str = subject.translate(str.maketrans("", "", "\r\n\x00"))

        if not to_list:
            logger.error("send: no recipients specified in 'to'")
            return SendResult(success=False, error_msg="No recipients specified in 'to'")

        to_str: str = "; ".join(to_list)
        cc_str: str = "; ".join(cc_list)

        # SEC-004: mask email addresses for logging
        masked_to_str: str = "; ".join(_mask_email(addr) for addr in to_list)

        html_body: str = email_content.get("html_body", "") or ""

        try:
            outlook = self._win32com.Dispatch("Outlook.Application")
            mail = outlook.CreateItem(0)  # 0 = olMailItem
            mail.Subject = sanitized_subject
            # Use HTMLBody when available; never set both Body and HTMLBody simultaneously.
            if html_body:
                mail.HTMLBody = html_body
            else:
                mail.Body = body
            mail.To = to_str
            if cc_str:
                mail.CC = cc_str
            mail.Send()
            logger.info(
                "send: email sent successfully (subject_len=%d, to_count=%d, to='%s')",
                len(sanitized_subject),
                len(to_list),
                masked_to_str,
            )
            return SendResult(success=True, error_msg=None)

        except Exception as exc:
            # Catch pywintypes.com_error and any other COM / runtime errors.
            logger.error("send: COM error while sending email: %s", str(exc))
            return SendResult(success=False, error_msg="이메일 전송 중 오류가 발생했습니다.")
