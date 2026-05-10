"""
email_monitor.py -- Outlook 이메일 모니터링 모듈 (재작성 v4).

PO 관련 이메일을 실시간 감지하여 지정 폴더에 저장하고
log_queue를 통해 GUI에 결과를 전달합니다.

Pattern: (?:IC)?PO\\s?([35]\\d+)  -- PO/ICPO 접두사 + 3 또는 5 로 시작하는 번호

변경 이력 (v4):
- 이메일 본문 저장 방식: .html → .msg (olMSG 포맷, mail_item.SaveAs)
- 이미지 첨부파일 자동 제외 (IMAGE_EXTS frozenset 기준)
- 발신자 이메일 필터 (allowed_senders, 비어있으면 전체 허용)
- 수신자 이메일 필터 (allowed_recipients, 비어있으면 전체 허용)
- PDF 파싱으로 폴더명 prefix(ACT/Valve) 및 금액 결정 (pdfplumber)
- scan_past_emails / start_monitoring 에 allowed_senders/recipients 파라미터 전달
"""

from __future__ import annotations

import json
import logging
import os
import queue
import re
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 경로 보안 헬퍼
# ---------------------------------------------------------------------------

def _safe_resolve(user_path: str, allowed_root: Path) -> Path:
    """경로 순회 공격 방지: resolve() 후 allowed_root 검증.

    Args:
        user_path: 검증할 상대 또는 절대 경로 문자열.
        allowed_root: 허용된 루트 디렉토리 Path.

    Returns:
        검증된 절대 경로 Path 객체.

    Raises:
        TypeError: user_path 또는 allowed_root 가 None 이면 raise.
        ValueError: 경로가 allowed_root 외부로 탈출하면 raise.
    """
    # None 방어
    if user_path is None:
        raise TypeError("user_path must not be None")
    if not isinstance(user_path, str):
        raise TypeError(f"user_path must be str, got {type(user_path).__name__}")
    if allowed_root is None:
        raise TypeError("allowed_root must not be None")
    if not isinstance(allowed_root, Path):
        raise TypeError(f"allowed_root must be Path, got {type(allowed_root).__name__}")

    resolved = (allowed_root / user_path).resolve()
    try:
        resolved.relative_to(allowed_root.resolve())
    except ValueError:
        raise ValueError(
            f"Path traversal detected: '{user_path}' escapes allowed root '{allowed_root}'"
        )
    return resolved


# ---------------------------------------------------------------------------
# 파일명 보조 헬퍼
# ---------------------------------------------------------------------------

def _safe_filename(name: str) -> str:
    """파일 시스템에 안전한 파일명으로 변환.

    Args:
        name: 원본 파일명 또는 제목 문자열.

    Returns:
        특수문자가 제거된 안전한 파일명 문자열.
    """
    # None 방어
    if name is None:
        raise TypeError("name must not be None")
    if not isinstance(name, str):
        raise TypeError(f"name must be str, got {type(name).__name__}")
    # 빈 문자열은 "untitled" 로 처리 (음수/0 개념 없음, 빈 허용 근거: 파일명 기본값 필요)
    sanitized = re.sub(r'[\\/:*?"<>|]', "_", name)
    sanitized = sanitized.strip()
    return sanitized[:200] if sanitized else "untitled"


def _get_unique_path(folder: Path, filename: str) -> Path:
    """중복 파일명에 (1), (2) 접미사를 부여하여 고유 경로 반환.

    Args:
        folder: 저장 대상 폴더 Path.
        filename: 원본 파일명 문자열.

    Returns:
        중복되지 않는 고유 파일 경로 Path.
    """
    if folder is None:
        raise TypeError("folder must not be None")
    if not isinstance(folder, Path):
        raise TypeError(f"folder must be Path, got {type(folder).__name__}")
    if filename is None:
        raise TypeError("filename must not be None")
    if not isinstance(filename, str):
        raise TypeError(f"filename must be str, got {type(filename).__name__}")

    dest = folder / filename
    if not dest.exists():
        return dest

    stem = Path(filename).stem
    suffix = Path(filename).suffix
    counter = 1
    while True:
        candidate = folder / f"{stem} ({counter}){suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


# ---------------------------------------------------------------------------
# 핵심 추출/판별 함수
# ---------------------------------------------------------------------------

def _has_new_po_keyword(text: str) -> bool:
    """Body/HTMLBody 텍스트에 new PO 관련 키워드가 포함되어 있는지 검사합니다.

    패턴: new\\s*(?:IC)?PO (re.IGNORECASE)
    매칭 예: "new PO", "new ICPO", "newPO", "newICPO", "NEW PO", "NEW ICPO"

    Args:
        text: 검색 대상 문자열 (Body 또는 HTMLBody 내용).

    Returns:
        키워드가 발견되면 True, 없으면 False.
        # ② 빈 문자열은 유효한 입력이나 키워드 없음 → False 반환 허용 (경계값, 0길이 문자열)

    Raises:
        TypeError: text 가 None 이면 raise.
        TypeError: text 가 str 이 아니면 raise.
    """
    # ① None 방어
    if text is None:
        raise TypeError("text must not be None")
    # ③ 비정상 타입 isinstance 체크 → TypeError (암묵적 형변환 금지)
    if not isinstance(text, str):
        raise TypeError(f"text must be str, got {type(text).__name__}")
    # ② 빈 문자열은 유효한 입력 — 키워드 없음으로 False 반환 (ValueError 불필요)
    # ④ 수치형 파라미터 없음 — str 길이 0 은 False 반환으로 처리

    return bool(re.search(r"new\s*(?:IC)?PO", text, re.IGNORECASE))


def _extract_po_number(subject: str) -> Optional[str]:
    """이메일 제목에서 PO 번호를 추출합니다.

    1차 패턴: (?:IC)?PO\\s?([35]\\d+)  (re.IGNORECASE)
        접두사 PO 또는 ICPO 를 허용하고, 3 또는 5 로 시작하는 번호 부분만 반환합니다.
        예) "New ICPO 3100035140", "CCI ICPO 3100035131 / ..."

    2차 패턴 (AT format 폴백): 번호가 제목 앞에 오고 "new ICPO" 가 뒤따르는 경우.
        조건: 제목에 "new\\s+ICPO" 가 포함되어 있을 때만 활성화 (무관 숫자 오탐 방지).
        예) "3100035130 B43234AT new ICPO"

    Args:
        subject: 이메일 제목 문자열.

    Returns:
        추출된 숫자 문자열 (예: "3100035130", "50001"), 매칭 없으면 None.
        # 빈 문자열은 매칭 없음, None 반환 허용 — 파라미터 자체는 유효한 str

    Raises:
        TypeError: subject 가 None 이면 raise.
        TypeError: subject 가 str 이 아니면 raise.
    """
    # ① None 방어
    if subject is None:
        raise TypeError("subject must not be None")
    # ③ 비정상 타입 isinstance 체크 → TypeError (암묵적 형변환 금지)
    if not isinstance(subject, str):
        raise TypeError(f"subject must be str, got {type(subject).__name__}")
    # ② 빈 문자열은 매칭 없음, None 반환 허용 (인라인 주석으로 근거 명시)
    # empty string is valid input but yields no match → None is acceptable
    # ④ 수치형 파라미터 없음 — str 길이 0 은 None 반환으로 처리 (ValueError 불필요)

    # 1차 패턴: PO/ICPO 접두사 + 번호
    match = re.search(r"(?:IC)?PO\s?([35]\d+)", subject, re.IGNORECASE)
    if match:
        return match.group(1)

    # 2차 패턴 (AT format): 번호가 제목 앞에 위치하고 "new ICPO" 가 제목에 존재하는 경우.
    # "new ICPO" 존재 여부를 선행 조건으로 두어 무관한 숫자를 잘못 추출하는 오탐을 방지합니다.
    if re.search(r"\bnew\s+ICPO\b", subject, re.IGNORECASE):
        fallback = re.search(r"\b([35]\d{9,})\b", subject)
        if fallback:
            return fallback.group(1)

    return None


def _is_reply_or_forward(subject: str) -> bool:
    """제목이 Re:, Fw:, Fwd: 로 시작하면 True 반환.

    Args:
        subject: 이메일 제목 문자열.

    Returns:
        회신/전달 이메일이면 True, 아니면 False.

    Raises:
        TypeError: subject 가 None 이면 raise.
        TypeError: subject 가 str 이 아니면 raise.
    """
    # ① None 방어
    if subject is None:
        raise TypeError("subject must not be None")
    # ③ 비정상 타입 isinstance 체크
    if not isinstance(subject, str):
        raise TypeError(f"subject must be str, got {type(subject).__name__}")

    return bool(re.match(r"(?:re|fw|fwd|automatic reply|자동 회신)\s*:", subject.strip(), re.IGNORECASE))


# ---------------------------------------------------------------------------
# 이미지 확장자 집합 (첨부파일 제외 기준)
# ---------------------------------------------------------------------------

IMAGE_EXTS: frozenset = frozenset({
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".tif",
    ".webp", ".ico", ".svg", ".heic", ".heif",
})


# ---------------------------------------------------------------------------
# PDF 파싱 상수 및 함수
# ---------------------------------------------------------------------------

# IC location prefix 매핑: PDF 회사명(소문자) → IC 코드
_COMPANY_IC_MAP: Dict[str, str] = {
    "imi critical engineering llc": "SoCal",
    "cci valve technology gmbh": "AT",
    "imi critical engineering (apac) pte. ltd.": "SG",
}

# ACT 키워드: "ACT," 또는 "ACT " 패턴
ACT_PATTERN: re.Pattern = re.compile(r"\bACT[,\s]", re.IGNORECASE)

# Valve 관련 키워드 집합
VALVE_KEYWORDS: Set[str] = {
    "DRE", "HBSE", "NBSE", "VLB", "VLR", "VS", "VST", "DHP",
    "IPLP", "VLN", "LLP", "LTB", "100D", "DRAG", "900D", "830T",
    "840", "860", "Globe",
}
VALVE_PATTERN: re.Pattern = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in VALVE_KEYWORDS) + r")\b",
    re.IGNORECASE,
)

# 스페어 파트 지시어 패턴 (본품 판별용)
SPARE_PATTERN: re.Pattern = re.compile(
    r"\b(SOFTGOODS|SOFT\s+GOODS|SPARE|REPAIR\s+KIT|SEAL\s+KIT|SERVICE\s+KIT|O[\-\s]RING\s+KIT)\b",
    re.IGNORECASE,
)

# Our Order Date 추출 패턴
OUR_ORDER_DATE_PATTERN: re.Pattern = re.compile(
    r"Our\s+Order\s+Date[\s:]+([A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4})",
    re.IGNORECASE,
)

# Incoterm 매핑 (전체 명칭 우선 — 긴 키가 짧은 키보다 먼저 매칭되도록 삽입 순서 보장)
INCOTERM_MAP: Dict[str, str] = {
    # Full Incoterms 2020 names (as they appear in PDFs) — checked first
    "EX WORKS": "EXW",
    "FREE CARRIER": "FCA",
    "CARRIAGE PAID TO": "CPT",
    "CARRIAGE AND INSURANCE PAID": "CIP",
    "DELIVERED AT PLACE UNLOADED": "DPU",
    "DELIVERED AT PLACE": "DAP",
    "DELIVERED DUTY PAID": "DDP",
    "FREE ALONGSIDE SHIP": "FAS",
    "FREE ON BOARD": "FOB",
    "COST AND FREIGHT": "CFR",
    "COST INSURANCE AND FREIGHT": "CIF",
    "COST, INSURANCE AND FREIGHT": "CIF",
    # Abbreviations as fallback
    "EXW": "EXW",
    "FCA": "FCA",
    "CPT": "CPT",
    "CIP": "CIP",
    "DAP": "DAP",
    "DPU": "DPU",
    "DDP": "DDP",
    "FAS": "FAS",
    "FOB": "FOB",
    "CFR": "CFR",
    "CIF": "CIF",
}


def _parse_pdf_info(pdf_path: Path) -> Tuple[str, str, str, str, str]:
    """PDF에서 폴더 prefix, 금액 문자열, 주문일, Incoterm, IC location 코드를 추출합니다.

    pdfplumber 를 사용하여 PDF 전체 텍스트를 읽고:
    - "Total Amount (KRW)" 패턴에서 금액을 추출합니다 (마지막 매칭값 사용).
    - ACT 키워드 우선, 없으면 Valve 키워드 검사.
    - "Our Order Date" 패턴에서 주문일을 추출합니다.
    - "Terms of Delivery" 패턴에서 Incoterm 을 추출합니다.
    - "Order\\n[회사명]" 패턴에서 회사명을 추출하여 _COMPANY_IC_MAP 으로 IC 코드를 결정합니다.

    Args:
        pdf_path: 파싱할 PDF 파일 절대 경로 Path.

    Returns:
        (prefix, amount_str, order_date, incoterm, ic_code) 5-튜플.
        prefix: "(ACT)", "(Valve)", "" 중 하나.
        amount_str: "66,800,300" 형식 (반올림 정수, 천단위 콤마), 추출 실패 시 "".
        order_date: "YYYY-MM-DD" 형식, 추출 실패 시 "".
        incoterm: "FOB", "EXW" 등 약어, 추출 실패 시 "".
        ic_code: "SoCal", "AT", "SG" 등 IC location 코드, 미매핑 시 "".
        pdfplumber ImportError 시 ("", "", "", "", "") 반환.

    Raises:
        TypeError: pdf_path 가 None 이면 raise.
        TypeError: pdf_path 가 Path 가 아니면 raise.
    """
    # ① None 방어
    if pdf_path is None:
        raise TypeError("pdf_path must not be None")
    # ③ isinstance 체크
    if not isinstance(pdf_path, Path):
        raise TypeError(f"pdf_path must be Path, got {type(pdf_path).__name__}")
    # ② 경로 존재 여부: 존재하지 않아도 시도 (파일 시스템 경쟁 조건 방어, 예외 내부 처리)

    prefix: str = ""
    amount_str: str = ""
    order_date: str = ""
    incoterm: str = ""
    ic_code: str = ""

    try:
        full_text: str = ""

        # --- Primary: pymupdf (fitz) — handles more PDF types than pdfplumber ---
        try:
            import fitz  # type: ignore[import]  # pymupdf
            with fitz.open(str(pdf_path)) as doc:
                for page in doc:
                    full_text += page.get_text() + "\n"
            logger.info("PDF텍스트 추출(pymupdf): %d chars", len(full_text))
        except ImportError:
            logger.info("pymupdf 미설치, pdfplumber로 fallback")
        except Exception as fitz_exc:
            logger.warning("pymupdf 추출 실패: %s", fitz_exc)

        # --- Fallback: pdfplumber ---
        if not full_text.strip():
            try:
                import pdfplumber  # type: ignore[import]
                with pdfplumber.open(str(pdf_path)) as pdf:
                    for page in pdf.pages:
                        text = page.extract_text() or ""
                        full_text += text + "\n"
                    # extract_words() as additional fallback inside pdfplumber block
                    if not full_text.strip():
                        for page in pdf.pages:
                            words = page.extract_words()
                            full_text += " ".join(w["text"] for w in words) + "\n"
                logger.info("PDF텍스트 추출(pdfplumber): %d chars", len(full_text))
            except ImportError:
                logger.info("pdfplumber 미설치 — PDF 파싱 생략")
                return ("", "", "", "", "")
            except Exception as plumber_exc:
                logger.warning("pdfplumber 추출 실패: %s", plumber_exc)

        # --- Diagnostic log ---
        ta_idx = full_text.lower().find("total amount")
        if ta_idx >= 0:
            snippet = full_text[max(0, ta_idx - 10):ta_idx + 150]
            logger.info("PDF Total Amount context: %r", snippet)
        else:
            logger.info(
                "PDF: 'Total Amount' not found (chars=%d, may be scanned image PDF)",
                len(full_text),
            )

        if not full_text.strip():
            return ("", "", "", "", "")

        # --- Company name extraction → IC location code ---
        # Regex-based approach was unreliable: "Order\n" matched "Purchase Order\nSupplier No"
        # causing "Supplier No" to be treated as the company name.
        # Fix: scan full_text for all known company keys directly (case-insensitive substring match).
        _full_text_lower: str = full_text.lower()  # allowed: str.lower() is safe for comparison
        ic_code = ""
        for _company_key, _ic_val in _COMPANY_IC_MAP.items():
            if _company_key in _full_text_lower:
                ic_code = _ic_val
                logger.info("PDF company matched: %r -> ic_code: %r", _company_key, ic_code)
                break

        # --- Our Order Date extraction ---
        date_m = OUR_ORDER_DATE_PATTERN.search(full_text)
        if date_m:
            raw_date = date_m.group(1).replace(",", "").strip()
            try:
                order_date = datetime.strptime(raw_date, "%b %d %Y").strftime("%Y-%m-%d")
            except ValueError:
                logger.warning("Our Order Date 파싱 실패: %r", raw_date)

        # --- Incoterm extraction ---
        # Diagnostic: Terms context
        tod_idx = full_text.lower().find("terms")
        if tod_idx >= 0:
            snippet = full_text[max(0, tod_idx):tod_idx + 120]
            logger.info("PDF Terms context: %r", snippet)
        else:
            logger.info("PDF: 'terms' not found in text")

        # Strategy 1: inline (레이블과 값이 같은 줄)
        tod_m = re.search(
            r"Terms\s+[Oo]f\s+Delivery[\s:]+([^\n]{2,80})",
            full_text,
            re.IGNORECASE,
        )
        if tod_m:
            raw_tod = tod_m.group(1).strip().upper()
            for key, abbr in INCOTERM_MAP.items():
                if key in raw_tod:
                    incoterm = abbr
                    break

        # Strategy 2: next-line (레이블 다음 줄에 값)
        if not incoterm:
            tod_m2 = re.search(
                r"Terms\s+[Oo]f\s+Delivery[^\n]*\n\s*([^\n]{2,80})",
                full_text,
                re.IGNORECASE,
            )
            if tod_m2:
                raw_tod = tod_m2.group(1).strip().upper()
                for key, abbr in INCOTERM_MAP.items():
                    if key in raw_tod:
                        incoterm = abbr
                        break

        # Strategy 3: skip-2-lines (레이블 → 중간행 → 실제값)
        if not incoterm:
            tod_m3 = re.search(
                r"Terms\s+[Oo]f\s+Delivery[^\n]*\n[^\n]*\n\s*([^\n]{2,80})",
                full_text,
                re.IGNORECASE,
            )
            if tod_m3:
                raw_tod = tod_m3.group(1).strip().upper()
                for key, abbr in INCOTERM_MAP.items():
                    if key in raw_tod:
                        incoterm = abbr
                        break

        # --- Amount extraction: 3 strategies ---

        # Strategy 1: inline — label and amount on same line
        for m in re.finditer(
            r"Total\s+Amount\s*\([A-Z]{2,4}\)[\t ]*([\d,]+\.?\d*)",
            full_text,
            re.IGNORECASE,
        ):
            raw: str = m.group(1).replace(",", "")
            if raw:
                try:
                    amount_str = f"{round(float(raw)):,}"
                except ValueError:
                    pass  # nosec B110

        # Strategy 2: next-line — amount on the line after label
        if not amount_str:
            for m in re.finditer(
                r"Total\s+Amount\s*\([A-Z]{2,4}\)[^\n]*\n\s*([\d,]+\.?\d*)",
                full_text,
                re.IGNORECASE,
            ):
                raw = m.group(1).replace(",", "")
                if raw:
                    try:
                        amount_str = f"{round(float(raw)):,}"
                    except ValueError:
                        pass  # nosec B110

        # Strategy 3: loose — find any large number after "Total Amount" within 300 chars
        if not amount_str and ta_idx >= 0:
            window = full_text[ta_idx:ta_idx + 300]
            nums = re.findall(r"[\d,]{4,}\.?\d*", window)
            for num in nums:
                raw = num.replace(",", "")
                try:
                    val = float(raw)
                    if val > 1000:  # plausible order amount
                        amount_str = f"{round(val):,}"
                        break
                except ValueError:
                    pass  # nosec B110

        # --- Prefix extraction (ACT / Valve) ---
        # 컨텍스트 윈도우(±150자): ACT/Valve 매치 주변에 SPARE 키워드 없으면 본품으로 판단
        act_matches = list(ACT_PATTERN.finditer(full_text))
        if act_matches:
            if any(
                not SPARE_PATTERN.search(
                    full_text[max(0, m.start() - 150):min(len(full_text), m.end() + 150)]
                )
                for m in act_matches
            ):
                prefix = "(ACT)"

        if not prefix:
            valve_matches = list(VALVE_PATTERN.finditer(full_text))
            if valve_matches:
                if any(
                    not SPARE_PATTERN.search(
                        full_text[max(0, m.start() - 150):min(len(full_text), m.end() + 150)]
                    )
                    for m in valve_matches
                ):
                    prefix = "(Valve)"

        logger.info(
            "PDF 파싱 완료: %s | prefix=%s | amount=%s | date=%s | incoterm=%s | ic_code=%s",
            pdf_path, prefix, amount_str, order_date, incoterm, ic_code
        )
    except Exception as exc:
        logger.warning("PDF 파싱 실패, 기본값 사용: %s — %s", pdf_path, exc)

    return (prefix, amount_str, order_date, incoterm, ic_code)


# ---------------------------------------------------------------------------
# 이메일 저장 헬퍼
# ---------------------------------------------------------------------------

def _save_email_msg(mail_item: object, dest_folder: Path, safe_subject: str) -> None:
    """이메일을 .msg 파일로 원자적 저장 (olMSG 포맷).

    mail_item.SaveAs(path, 3) 을 사용합니다.
    3 = olMSG 포맷 상수 (win32com Outlook 상수 OlSaveAsType.olMSG).

    임시 경로(.msg.tmp)에 SaveAs 후 os.replace() 로 원자적 이동합니다.

    Args:
        mail_item: win32com MailItem 객체.
        dest_folder: 저장 대상 폴더 Path.
        safe_subject: 안전한 파일명용 제목 문자열.

    Raises:
        TypeError: dest_folder 또는 safe_subject 가 None 이면 raise.
        RuntimeError: 파일 저장 실패 시 원인 체이닝.
    """
    # ① None 방어
    if dest_folder is None:
        raise TypeError("dest_folder must not be None")
    # ③ isinstance 체크
    if not isinstance(dest_folder, Path):
        raise TypeError(f"dest_folder must be Path, got {type(dest_folder).__name__}")
    if safe_subject is None:
        raise TypeError("safe_subject must not be None")
    if not isinstance(safe_subject, str):
        raise TypeError(f"safe_subject must be str, got {type(safe_subject).__name__}")
    # ② 빈 safe_subject 허용 — 파일명 기본값은 _safe_filename 에서 보장됨

    # Windows MAX_PATH(260자) 초과 방지: 폴더 경로 길이에 맞게 파일명 트리밍
    MAX_WIN_PATH = 259
    folder_len = len(str(dest_folder))
    avail = MAX_WIN_PATH - folder_len - 1 - len(".msg.tmp")  # tmp file suffix is .msg.tmp (8 chars)
    avail = max(20, min(avail, 200))
    trimmed_subject = safe_subject[:avail]
    msg_path = _get_unique_path(dest_folder, f"{trimmed_subject}.msg")
    # FS ①: 원자적 쓰기 — 임시 경로에 SaveAs 후 os.replace()
    tmp = msg_path.with_suffix(".msg.tmp")
    try:
        mail_item.SaveAs(str(tmp), 3)   # 3 = olMSG 포맷 상수
        os.replace(str(tmp), str(msg_path))
        logger.info("이메일 .msg 저장 완료: %s", msg_path)
    except Exception as exc:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass  # nosec B110
        raise RuntimeError(f"이메일 .msg 저장 실패: {msg_path}") from exc


def _save_attachments(mail_item: object, dest_folder: Path) -> list:
    """이메일 첨부파일을 dest_folder 에 저장.

    이미지 확장자(IMAGE_EXTS)는 자동으로 건너뜁니다.
    중복 파일명이면 (1), (2) 접미사 부여.

    Args:
        mail_item: win32com MailItem 객체.
        dest_folder: 저장 대상 폴더 Path.

    Returns:
        저장된 첨부파일 절대 경로 문자열 목록 (List[str]).
    """
    # ① None 방어
    if dest_folder is None:
        raise TypeError("dest_folder must not be None")
    # ③ isinstance 체크
    if not isinstance(dest_folder, Path):
        raise TypeError(f"dest_folder must be Path, got {type(dest_folder).__name__}")

    saved: list = []
    attachments = getattr(mail_item, "Attachments", None)
    if attachments is None:
        return saved

    count = getattr(attachments, "Count", 0)
    # count 는 0 이상이므로 허용 (0 = 첨부파일 없음, 정상 케이스)
    if not isinstance(count, int) or count < 0:
        logger.warning("첨부파일 개수 비정상: %s", count)
        return saved

    for i in range(1, count + 1):
        try:
            attachment = attachments.Item(i)
            orig_name: str = getattr(attachment, "FileName", f"attachment_{i}")

            # 이미지 확장자 제외 (인라인 이미지/서명 이미지 포함 방지)
            if Path(orig_name).suffix.lower() in IMAGE_EXTS:
                logger.info("이미지 첨부파일 제외: %s", orig_name)
                continue

            dest_path = _get_unique_path(dest_folder, orig_name)

            # FS ①: 임시 경로에 저장 후 원자적 이동
            tmp_path = dest_path.with_suffix(dest_path.suffix + ".tmp")
            attachment.SaveAsFile(str(tmp_path))
            os.replace(str(tmp_path), str(dest_path))
            saved.append(str(dest_path))
            logger.info("첨부파일 저장: %s", dest_path)
        except Exception as exc:
            logger.error("첨부파일 %d 저장 실패: %s", i, exc)

    return saved


# ---------------------------------------------------------------------------
# processed.json 영속성
# ---------------------------------------------------------------------------

def load_processed_set(base_dir: Path) -> Set[str]:
    """처리 완료된 PO 번호 집합을 base_dir/processed.json 에서 로드합니다.

    FS ②: utf-8 → cp949 → latin-1 다중 fallback.

    Args:
        base_dir: processed.json 이 위치한 디렉토리 Path.

    Returns:
        처리 완료된 PO 번호 문자열 집합. 파일 없으면 빈 set 반환.

    Raises:
        TypeError: base_dir 이 None 이면 raise.
    """
    # ① None 방어
    if base_dir is None:
        raise TypeError("base_dir must not be None")
    # ③ isinstance 체크
    if not isinstance(base_dir, Path):
        raise TypeError(f"base_dir must be Path, got {type(base_dir).__name__}")

    json_path = base_dir / "processed.json"
    if not json_path.exists():
        logger.info("processed.json 없음, 빈 set 반환: %s", json_path)
        return set()

    # FS ②: utf-8 → cp949 → latin-1 다중 fallback
    for enc in ("utf-8", "cp949", "latin-1"):
        try:
            text = json_path.read_text(encoding=enc)
            data = json.loads(text)
            if isinstance(data, list):
                result: Set[str] = set(str(x) for x in data)
                logger.info("processed.json 로드 완료 (encoding=%s): %d 항목", enc, len(result))
                return result
            logger.warning("processed.json 형식 오류 (list 아님), 빈 set 반환")
            return set()
        except (UnicodeDecodeError, LookupError):
            continue
        except json.JSONDecodeError as exc:
            logger.error("processed.json JSON 파싱 실패: %s", exc)
            return set()

    logger.error("processed.json 모든 인코딩 실패: %s", json_path)
    return set()


def save_processed_set(base_dir: Path, processed_set: Set[str]) -> None:
    """처리 완료된 PO 번호 집합을 base_dir/processed.json 에 저장합니다.

    FS ①: tempfile → os.replace() 원자적 패턴.

    Args:
        base_dir: processed.json 을 저장할 디렉토리 Path.
        processed_set: 저장할 PO 번호 집합.

    Raises:
        TypeError: base_dir 또는 processed_set 이 None 이면 raise.
        RuntimeError: 파일 저장 실패 시 원인 체이닝.
    """
    # ① None 방어
    if base_dir is None:
        raise TypeError("base_dir must not be None")
    if not isinstance(base_dir, Path):
        raise TypeError(f"base_dir must be Path, got {type(base_dir).__name__}")
    if processed_set is None:
        raise TypeError("processed_set must not be None")
    if not isinstance(processed_set, set):
        raise TypeError(f"processed_set must be set, got {type(processed_set).__name__}")

    json_path = base_dir / "processed.json"
    # FS ①: 원자적 쓰기
    tmp = json_path.with_suffix(json_path.suffix + ".tmp")
    try:
        content = json.dumps(sorted(processed_set), ensure_ascii=False, indent=2)
        tmp.write_text(content, encoding="utf-8")
        os.replace(str(tmp), str(json_path))
        logger.info("processed.json 저장 완료: %d 항목", len(processed_set))
    except Exception as exc:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass  # nosec B110
        raise RuntimeError(f"processed.json 저장 실패: {json_path}") from exc


# ---------------------------------------------------------------------------
# 발신자 SMTP 주소 헬퍼 (Exchange DN 대응)
# ---------------------------------------------------------------------------

def _get_smtp_address(mail_item: object) -> str:
    """발신자 SMTP 주소를 반환. Exchange DN인 경우 MAPI를 통해 실제 주소 조회.

    Outlook COM에서 Exchange 계정의 SenderEmailAddress는 SMTP 주소 대신
    '/o=ExchangeLabs/ou=.../cn=...' 형식의 DN(Distinguished Name)을 반환할 수 있음.
    SenderEmailType == "EX"인 경우 Sender.GetExchangeUser().PrimarySmtpAddress 로 조회.

    Args:
        mail_item: win32com MailItem 객체.

    Returns:
        소문자 SMTP 이메일 주소 문자열. 조회 실패 또는 주소 없는 경우 빈 문자열.

    Raises:
        TypeError: mail_item 이 None 이면 raise.
    """
    # ① None 방어
    if mail_item is None:
        raise TypeError("mail_item must not be None")
    # ③ isinstance 체크 — object 타입이므로 추가 isinstance 불필요(모든 객체 허용)
    # ② 경계값: 주소가 빈 문자열이면 그대로 "" 반환 (0/음수 개념 없음)
    # ④ 허용 근거: mail_item 은 win32com COM 객체로 정적 타입 불가, object 타입 허용

    addr: str = (getattr(mail_item, "SenderEmailAddress", "") or "").strip()
    email_type: str = (getattr(mail_item, "SenderEmailType", "") or "").strip().upper()

    # SMTP 주소면 그대로 반환
    if email_type == "SMTP":
        return addr.lower()

    # Exchange DN이면 Sender 객체를 통해 SMTP 주소 조회
    if email_type == "EX":
        try:
            sender = getattr(mail_item, "Sender", None)
            if sender is not None:
                exchange_user = sender.GetExchangeUser()
                if exchange_user is not None:
                    smtp: str = getattr(exchange_user, "PrimarySmtpAddress", "") or ""
                    if smtp:
                        return smtp.lower()
        except Exception as exc:
            logger.warning("Exchange SMTP 주소 조회 실패, fallback 사용: %s", exc)

    # fallback: @ 포함하면 그대로 사용 (SMTP 형식으로 간주)
    if "@" in addr:
        return addr.lower()

    return ""


# ---------------------------------------------------------------------------
# 메일 처리 핵심 함수
# ---------------------------------------------------------------------------

def _process_mail_item(
    mail_item: object,
    root_folder: Path,
    processed_set: Set[str],
    log_queue: "queue.Queue[str]",
    allowed_senders: Optional[Set[str]] = None,
    allowed_recipients: Optional[Set[str]] = None,
) -> bool:
    """단일 MailItem 을 처리합니다.

    제목에서 PO 번호를 추출하고, 폴더 생성 및 .msg/첨부파일 저장을 수행합니다.
    중복 po_number 는 processed_set 으로 차단합니다.
    발신자/수신자 필터가 지정된 경우 해당 조건을 통과한 메일만 처리합니다.

    Args:
        mail_item: win32com MailItem 객체.
        root_folder: PO 폴더를 생성할 루트 Path.
        processed_set: 이미 처리된 PO 번호 집합 (변경됨).
        log_queue: GUI 로그 전달용 Queue.
        allowed_senders: 허용할 발신자 이메일 소문자 집합.
            None 또는 빈 set 이면 필터 비적용 (전체 허용).
        allowed_recipients: 허용할 수신자 이메일 소문자 집합.
            None 또는 빈 set 이면 필터 비적용 (전체 허용).
            수신자 중 하나라도 집합에 포함되면 통과.

    Returns:
        처리 성공 시 True, 스킵 또는 실패 시 False.

    Raises:
        TypeError: root_folder, processed_set, log_queue 가 None 이면 raise.
    """
    # ① None 방어
    if root_folder is None:
        raise TypeError("root_folder must not be None")
    if not isinstance(root_folder, Path):
        raise TypeError(f"root_folder must be Path, got {type(root_folder).__name__}")
    if processed_set is None:
        raise TypeError("processed_set must not be None")
    if not isinstance(processed_set, set):
        raise TypeError(f"processed_set must be set, got {type(processed_set).__name__}")
    if log_queue is None:
        raise TypeError("log_queue must not be None")
    if not isinstance(log_queue, queue.Queue):
        raise TypeError(f"log_queue must be queue.Queue, got {type(log_queue).__name__}")
    # allowed_senders / allowed_recipients: None 은 필터 미적용 의미로 허용
    # (None vs 빈 set 모두 전체 허용 — 음수 개념 없음)
    if allowed_senders is not None and not isinstance(allowed_senders, set):
        raise TypeError(
            f"allowed_senders must be set or None, got {type(allowed_senders).__name__}"
        )
    if allowed_recipients is not None and not isinstance(allowed_recipients, set):
        raise TypeError(
            f"allowed_recipients must be set or None, got {type(allowed_recipients).__name__}"
        )

    def _log(msg: str) -> None:
        """로그 큐 및 logger 에 동시 전달."""
        logger.info(msg)
        try:
            log_queue.put_nowait(msg)
        except queue.Full:
            logger.warning("log_queue 가득 참, 메시지 드롭: %s", msg)

    try:
        subject: str = getattr(mail_item, "Subject", "") or ""

        # 회신/전달 스킵
        if _is_reply_or_forward(subject):
            logger.info("회신/전달 메일 스킵: %s", subject)
            return False

        po_number = _extract_po_number(subject)
        if po_number is None:
            # 본문 키워드 폴백: Body + HTMLBody 에서 new PO/ICPO 키워드 검색
            body_text: str = ""
            try:
                body_text = (getattr(mail_item, "Body", "") or "")
            except Exception:
                body_text = ""
            html_text: str = ""
            try:
                html_text = (getattr(mail_item, "HTMLBody", "") or "")
            except Exception:
                html_text = ""
            combined: str = body_text + "\n" + html_text
            if _has_new_po_keyword(combined):
                # 본문에 키워드 존재 → 제목에서 숫자([35]\d{9,}) 직접 추출 (2차 패턴 폴백과 동일 방식)
                fallback_m = re.search(r"\b([35]\d{9,})\b", subject)
                if fallback_m:
                    po_number = fallback_m.group(1)
                    logger.info(
                        "본문 키워드 폴백 — 제목 숫자 추출: %s (subject=%s)", po_number, subject
                    )
                else:
                    logger.info("본문 키워드 있으나 제목 숫자 미발견, 스킵: %s", subject)
                    return False
            else:
                logger.info("PO 번호 미포함 메일 스킵: %s", subject)
                return False

        # 발신자 필터 (allowed_senders 비어있으면 전체 허용)
        if allowed_senders:
            # Bug 2 수정: Exchange DN 대응 — _get_smtp_address() 로 실제 SMTP 주소 조회
            sender_addr: str = _get_smtp_address(mail_item)
            if sender_addr not in allowed_senders:
                logger.info(
                    "발신자 필터 미통과, 스킵: sender=%s (subject=%s)", sender_addr, subject
                )
                return False

        # 수신자 필터 (allowed_recipients 비어있으면 전체 허용)
        if allowed_recipients:
            # mail_item.To 는 단일 문자열(세미콜론/쉼표 구분), Recipients 컬렉션이 더 신뢰도 높음
            recipients_str: str = (getattr(mail_item, "To", "") or "").lower()
            # Recipients 컬렉션에서 개별 이메일 추출 시도
            recipient_set: Set[str] = set()
            try:
                recipients_col = getattr(mail_item, "Recipients", None)
                if recipients_col is not None:
                    rec_count = getattr(recipients_col, "Count", 0)
                    for ri in range(1, rec_count + 1):
                        try:
                            rec = recipients_col.Item(ri)
                            addr = (
                                getattr(rec, "Address", "") or ""
                            ).lower().strip()
                            if addr:
                                recipient_set.add(addr)
                        except Exception:
                            pass  # nosec B110
            except Exception:
                pass  # nosec B110

            # Recipients 컬렉션 추출 실패 시 To 필드 파싱 fallback
            if not recipient_set and recipients_str:
                for part in re.split(r"[;,]", recipients_str):
                    part = part.strip()
                    if part:
                        recipient_set.add(part)

            # 수신자 중 하나라도 allowed_recipients 에 포함되면 통과
            if not recipient_set.intersection(allowed_recipients):
                logger.info(
                    "수신자 필터 미통과, 스킵: recipients=%s (subject=%s)",
                    recipient_set,
                    subject,
                )
                return False

        # 중복 처리 차단
        if po_number in processed_set:
            logger.info("중복 PO 번호 스킵: %s (subject=%s)", po_number, subject)
            return False

        # ---------------------------------------------------------------
        # PDF 첨부파일 파싱으로 폴더명 prefix 및 금액 결정
        # ---------------------------------------------------------------
        # dest_folder 를 우선 임시 경로로 확보 (PDF 임시 저장 용도)
        tmp_dest_folder: Path = root_folder / _safe_filename(po_number)
        tmp_dest_folder.mkdir(parents=True, exist_ok=True)

        prefix: str = ""
        amount_str: str = ""
        order_date: str = ""
        incoterm: str = ""
        ic_code: str = ""
        attachments_obj = getattr(mail_item, "Attachments", None)
        if attachments_obj is not None:
            att_count: int = getattr(attachments_obj, "Count", 0)
            # att_count 는 0 이상 (0 = 첨부 없음, 정상)
            # 1단계: 모든 PDF 첨부 수집 (att_name, tmp_path) 형태로 리스트에 저장
            pdf_list: list = []  # List[Tuple[str, Path]] — Python 3.9 typing 모듈 없이 list 리터럴
            for ai in range(1, att_count + 1):
                try:
                    att = attachments_obj.Item(ai)
                    att_name: str = getattr(att, "FileName", f"attachment_{ai}")
                    if Path(att_name).suffix.lower() == ".pdf":
                        tmp_pdf: Path = tmp_dest_folder / f"_parse_{att_name}"
                        att.SaveAsFile(str(tmp_pdf))
                        pdf_list.append((att_name, tmp_pdf))
                except Exception as pdf_exc:
                    logger.warning("PDF 임시 저장 실패 (ai=%d): %s", ai, pdf_exc)

            # 2단계: 우선순위 정렬 — po_number가 파일명(소문자)에 포함되면 sort key=0, 아니면 1
            # AL type_valid: att_name이 None이거나 빈 문자열이면 sort key=1로 처리 (None 방어)
            def _pdf_sort_key(item: tuple) -> int:
                """sort key: po_number 포함 시 0, 아니면 1. None/빈문자열 방어."""
                name = item[0]
                # None 방어 — isinstance 체크 후 처리
                if name is None:
                    return 1  # None 입력: sort key=1 (후순위)
                if not isinstance(name, str):
                    return 1  # 비정상 타입: sort key=1 (후순위; 암묵적 형변환 금지)
                if len(name) == 0:
                    return 1  # 빈 문자열: sort key=1 (후순위)
                return 0 if po_number.lower() in name.lower() else 1

            sorted_pdfs = sorted(pdf_list, key=_pdf_sort_key)  # 안정 정렬 (동순위 원래 순서 유지)

            # 3단계 + 4단계: 순차 파싱, 유효 결과 채택 후 break, 개별 예외 격리
            # 5단계: try/finally로 임시 파일 최종 정리 (파싱 성공 여부 무관)
            try:
                for _pdf_name, _tmp_pdf in sorted_pdfs:
                    try:
                        _prefix, _amount, _date, _inco, _ic = _parse_pdf_info(_tmp_pdf)
                        if _amount or _date or _inco:
                            # 유효한 파싱 결과 채택
                            prefix, amount_str, order_date, incoterm, ic_code = (
                                _prefix, _amount, _date, _inco, _ic
                            )
                            logger.info(
                                "PDF 파싱 채택: %s (amount=%s, date=%s, incoterm=%s)",
                                _pdf_name, _amount, _date, _inco,
                            )
                            break
                        else:
                            logger.info("PDF 파싱 결과 없음, 다음 PDF로 진행: %s", _pdf_name)
                    except Exception as parse_exc:
                        # 4단계: 개별 예외 격리 — 전체 루프 중단 금지
                        logger.warning("PDF 파싱 실패, 다음 PDF로 진행: %s — %s", _pdf_name, parse_exc)
            finally:
                # 5단계: 모든 임시 PDF 정리 (파싱 성공 여부 무관)
                for _pdf_name, _tmp_pdf in sorted_pdfs:
                    try:
                        _tmp_pdf.unlink(missing_ok=True)
                    except OSError as _unlink_exc:
                        logger.warning("임시 PDF 삭제 실패: %s — %s", _pdf_name, _unlink_exc)

        # 폴더명 조합: prefix + po_number + amount [+ date] [+ incoterm]
        if prefix:
            base = f"{prefix} {po_number}"
        else:
            base = po_number

        if amount_str:
            base = f"{base} - {amount_str}"

        if order_date:
            base = f"{base} - {order_date}"

        if incoterm:
            base = f"{base} - {incoterm}"

        # IC location prefix (폴더명 맨 앞에 추가, 공백 구분자 사용)
        if ic_code:
            base = f"{ic_code} {base}"

        folder_name: str = base

        dest_folder: Path = root_folder / _safe_filename(folder_name)

        # 임시 폴더와 최종 폴더가 다르면 rename (이미 생성된 임시 폴더 활용)
        if tmp_dest_folder != dest_folder:
            try:
                if dest_folder.exists():
                    # 이미 존재하면 임시 폴더 내 파일을 이동하지 않고 그냥 사용
                    pass  # nosec B110
                else:
                    shutil.move(str(tmp_dest_folder), str(dest_folder))
            except OSError as rename_exc:
                logger.warning(
                    "폴더 rename 실패(%s→%s): %s, 새로 생성",
                    tmp_dest_folder, dest_folder, rename_exc,
                )
                dest_folder.mkdir(parents=True, exist_ok=True)
        else:
            dest_folder.mkdir(parents=True, exist_ok=True)

        logger.info("PO 폴더 생성/확인: %s", dest_folder)

        # 이메일 .msg 저장 (HTML 저장 대체)
        safe_subject = _safe_filename(subject)
        _save_email_msg(mail_item, dest_folder, safe_subject)

        # 첨부파일 저장 (이미지 확장자 자동 제외)
        saved_files = _save_attachments(mail_item, dest_folder)

        # 처리 완료 기록
        processed_set.add(po_number)
        result_msg = (
            f"[PO{po_number}] 처리 완료 — 제목: {subject} | "
            f"첨부파일 {len(saved_files)}개 저장"
        )
        _log(result_msg)
        return True

    except Exception as exc:
        logger.error("MailItem 처리 실패: %s", exc)
        try:
            log_queue.put_nowait(f"[오류] MailItem 처리 실패: {exc}")
        except queue.Full:
            pass  # nosec B110
        return False


# ---------------------------------------------------------------------------
# 과거 이메일 스캔
# ---------------------------------------------------------------------------

def scan_past_emails(
    root_folder: Path,
    since_dt: datetime,
    log_queue: "queue.Queue[str]",
    processed_set: Set[str],
    allowed_senders: Optional[Set[str]] = None,
    allowed_recipients: Optional[Set[str]] = None,
) -> int:
    """Outlook Inbox 에서 since_dt 이후 이메일을 일괄 스캔하여 처리합니다.

    Args:
        root_folder: PO 폴더를 생성할 루트 Path.
        since_dt: 이 시각 이후의 메일만 처리 (datetime, naive 또는 aware).
        log_queue: GUI 로그 전달용 Queue.
        processed_set: 이미 처리된 PO 번호 집합 (변경됨).
        allowed_senders: 허용할 발신자 이메일 소문자 집합.
            None 또는 빈 set 이면 전체 허용.
        allowed_recipients: 허용할 수신자 이메일 소문자 집합.
            None 또는 빈 set 이면 전체 허용.

    Returns:
        처리(저장)된 메일 수 (int, 0 이상).
        Outlook 미실행 등 예외 시 0 반환.

    Raises:
        TypeError: 필수 파라미터가 None 이면 raise.
        TypeError: since_dt 가 datetime 이 아니면 raise.
    """
    # ① None 방어
    if root_folder is None:
        raise TypeError("root_folder must not be None")
    if not isinstance(root_folder, Path):
        raise TypeError(f"root_folder must be Path, got {type(root_folder).__name__}")
    if since_dt is None:
        raise TypeError("since_dt must not be None")
    # ③ isinstance 체크
    if not isinstance(since_dt, datetime):
        raise TypeError(f"since_dt must be datetime, got {type(since_dt).__name__}")
    if log_queue is None:
        raise TypeError("log_queue must not be None")
    if not isinstance(log_queue, queue.Queue):
        raise TypeError(f"log_queue must be queue.Queue, got {type(log_queue).__name__}")
    if processed_set is None:
        raise TypeError("processed_set must not be None")
    if not isinstance(processed_set, set):
        raise TypeError(f"processed_set must be set, got {type(processed_set).__name__}")
    # allowed_senders / allowed_recipients: None 은 필터 미적용 의미로 허용
    if allowed_senders is not None and not isinstance(allowed_senders, set):
        raise TypeError(
            f"allowed_senders must be set or None, got {type(allowed_senders).__name__}"
        )
    if allowed_recipients is not None and not isinstance(allowed_recipients, set):
        raise TypeError(
            f"allowed_recipients must be set or None, got {type(allowed_recipients).__name__}"
        )

    def _log(msg: str) -> None:
        logger.info(msg)
        try:
            log_queue.put_nowait(msg)
        except queue.Full:
            pass  # nosec B110

    try:
        import pythoncom  # type: ignore[import]
        import win32com.client  # type: ignore[import]
    except ImportError as exc:
        errmsg = f"win32com/pythoncom import 실패: {exc}"
        logger.error(errmsg)
        try:
            log_queue.put_nowait(f"[오류] {errmsg}")
        except queue.Full:
            pass  # nosec B110
        return 0

    try:
        pythoncom.CoInitialize()
        try:
            outlook = win32com.client.Dispatch("Outlook.Application")
            namespace = outlook.GetNamespace("MAPI")
            inbox = namespace.GetDefaultFolder(6)  # olFolderInbox = 6
            items = inbox.Items
            # 최신순 정렬 — since_dt 이전 아이템 감지 후 조기 종료 가능
            items.Sort("[ReceivedTime]", True)

            _log(
                f"[스캔] 기준 시각: {since_dt.strftime('%Y-%m-%d %H:%M')}"
                " | Inbox 전체 순회 (Python datetime 비교)"
            )

            count = 0
            try:
                item = items.GetFirst()
                while item is not None:
                    try:
                        received = getattr(item, "ReceivedTime", None)
                        if received is not None:
                            # pywintypes.datetime is a datetime subclass with tzinfo.
                            # Strip tzinfo so naive comparison with since_dt works.
                            # Avoid importing pywintypes inside the loop — win32timezone
                            # dependency causes ModuleNotFoundError in frozen EXE.
                            try:
                                if hasattr(received, "replace"):
                                    received_dt: datetime = received.replace(tzinfo=None)
                                else:
                                    received_dt = datetime(
                                        received.year, received.month, received.day,
                                        received.hour, received.minute, received.second,
                                    )
                            except Exception:
                                received_dt = None

                            if received_dt is not None and received_dt < since_dt:
                                item = items.GetNext()
                                continue

                        processed = _process_mail_item(
                            item,
                            root_folder,
                            processed_set,
                            log_queue,
                            allowed_senders=allowed_senders,
                            allowed_recipients=allowed_recipients,
                        )
                        if processed:
                            count += 1
                    except Exception as item_exc:
                        logger.error("스캔 중 아이템 처리 오류: %s", item_exc)
                    item = items.GetNext()
            except Exception as iter_exc:
                logger.error("이메일 순회 중 오류: %s", iter_exc)
                _log(f"[오류] 이메일 순회 중 오류: {iter_exc}")

            _log(f"[스캔] 완료 — {count}건 처리")
            logger.info("과거 이메일 스캔 완료: %d건 처리", count)
            return count

        except Exception as exc:
            errmsg = f"Outlook 스캔 오류: {exc}"
            logger.error(errmsg)
            _log(f"[오류] {errmsg}")
            return 0
        finally:
            pythoncom.CoUninitialize()

    except Exception as exc:
        errmsg = f"Outlook COM 초기화 실패: {exc}"
        logger.error(errmsg)
        try:
            log_queue.put_nowait(f"[오류] {errmsg}")
        except queue.Full:
            pass  # nosec B110
        return 0


# ---------------------------------------------------------------------------
# Outlook 실시간 이벤트 핸들러
# ---------------------------------------------------------------------------

class OutlookEventHandler:
    """Outlook DispatchWithEvents 바인딩용 이벤트 핸들러.

    win32com.client.DispatchWithEvents() 에 전달되어
    신규 메일 이벤트(OnNewMailEx) 를 처리합니다.
    """

    def __init__(self) -> None:
        """핸들러 초기화. 인스턴스 변수는 start_monitoring 에서 주입."""
        self._root_folder: Optional[Path] = None
        self._processed_set: Optional[Set[str]] = None
        self._log_queue: Optional["queue.Queue[str]"] = None
        # 발신자/수신자 필터 (None = 전체 허용)
        self._allowed_senders: Optional[Set[str]] = None
        self._allowed_recipients: Optional[Set[str]] = None

    def _log(self, message: str) -> None:
        """GUI 로그 큐와 logger 에 동시 전달.

        Args:
            message: 전달할 로그 메시지 문자열.
        """
        logger.info(message)
        if self._log_queue is not None:
            try:
                self._log_queue.put_nowait(message)
            except queue.Full:
                logger.warning("log_queue 가득 참, 메시지 드롭: %s", message)

    def OnNewMailEx(self, entryIDsCollection: str) -> None:  # noqa: N802
        """신규 메일 이벤트 핸들러.

        entryIDsCollection 을 쉼표로 분리하여 각 MailItem 을 처리합니다.

        Args:
            entryIDsCollection: 쉼표로 구분된 EntryID 문자열.
        """
        # ① None 방어
        if entryIDsCollection is None:
            raise TypeError("entryIDsCollection must not be None")
        # ③ isinstance 체크
        if not isinstance(entryIDsCollection, str):
            raise TypeError(
                f"entryIDsCollection must be str, got {type(entryIDsCollection).__name__}"
            )
        # ② 빈 문자열은 정상 — 이벤트 없음으로 처리 (음수/0 개념 없음)
        if len(entryIDsCollection.strip()) == 0:
            logger.warning("OnNewMailEx: 빈 entryIDsCollection 수신, 스킵")
            return

        if self._root_folder is None or self._processed_set is None or self._log_queue is None:
            logger.error("OnNewMailEx: 핸들러 미초기화 상태")
            return

        try:
            import pythoncom  # type: ignore[import]  # noqa: F401
            import win32com.client  # type: ignore[import]
        except ImportError as exc:
            logger.error("win32com/pythoncom import 실패: %s", exc)
            return

        entry_ids = [eid.strip() for eid in entryIDsCollection.split(",") if eid.strip()]
        if len(entry_ids) == 0:
            logger.warning("OnNewMailEx: 파싱 후 EntryID 목록 비어있음, 스킵")
            return

        for entry_id in entry_ids:
            try:
                namespace = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
                mail_item = namespace.GetItemFromID(entry_id)
                _process_mail_item(
                    mail_item,
                    self._root_folder,
                    self._processed_set,
                    self._log_queue,
                    allowed_senders=self._allowed_senders,
                    allowed_recipients=self._allowed_recipients,
                )
            except Exception as exc:
                logger.error("EntryID %s 처리 실패: %s", entry_id, exc)
                self._log(f"[오류] EntryID {entry_id} 처리 실패: {exc}")


# ---------------------------------------------------------------------------
# 모니터링 스레드 시작
# ---------------------------------------------------------------------------

def start_monitoring(
    root_folder: Path,
    log_queue: "queue.Queue[str]",
    processed_set: Set[str],
    allowed_senders: Optional[Set[str]] = None,
    allowed_recipients: Optional[Set[str]] = None,
    stop_event: Optional[threading.Event] = None,
) -> threading.Thread:
    """Outlook 이메일 모니터링 daemon 스레드를 시작합니다.

    pythoncom.CoInitialize() 후 DispatchWithEvents 로 Outlook 을 바인딩하고
    0.5 초 간격으로 PumpWaitingMessages() 를 실행합니다.

    Args:
        root_folder: PO 폴더를 생성할 루트 Path.
        log_queue: GUI 에 로그를 전달할 Queue 객체.
        processed_set: 이미 처리된 PO 번호 집합 (공유, 변경됨).
        allowed_senders: 허용할 발신자 이메일 소문자 집합.
            None 또는 빈 set 이면 전체 허용.
        allowed_recipients: 허용할 수신자 이메일 소문자 집합.
            None 또는 빈 set 이면 전체 허용.

    Returns:
        시작된 daemon threading.Thread 객체.

    Raises:
        TypeError: 파라미터가 None 이거나 타입 불일치 시 raise.
    """
    # ① None → ③ isinstance 순서 고정
    if root_folder is None:
        raise TypeError("root_folder must not be None")
    if not isinstance(root_folder, Path):
        raise TypeError(f"root_folder must be Path, got {type(root_folder).__name__}")
    if log_queue is None:
        raise TypeError("log_queue must not be None")
    if not isinstance(log_queue, queue.Queue):
        raise TypeError(f"log_queue must be queue.Queue, got {type(log_queue).__name__}")
    if processed_set is None:
        raise TypeError("processed_set must not be None")
    if not isinstance(processed_set, set):
        raise TypeError(f"processed_set must be set, got {type(processed_set).__name__}")
    # allowed_senders / allowed_recipients: None 은 필터 미적용 의미로 허용
    if allowed_senders is not None and not isinstance(allowed_senders, set):
        raise TypeError(
            f"allowed_senders must be set or None, got {type(allowed_senders).__name__}"
        )
    if allowed_recipients is not None and not isinstance(allowed_recipients, set):
        raise TypeError(
            f"allowed_recipients must be set or None, got {type(allowed_recipients).__name__}"
        )
    # None is allowed — internal fallback Event created
    if stop_event is not None and not isinstance(stop_event, threading.Event):
        raise TypeError(
            f"stop_event must be threading.Event or None, got {type(stop_event).__name__}"
        )

    def _monitor_loop() -> None:
        """모니터링 루프 (daemon 스레드 내 실행)."""
        _stop_event = stop_event if stop_event is not None else threading.Event()

        try:
            import pythoncom  # type: ignore[import]
            import win32com.client  # type: ignore[import]
        except ImportError as exc:
            logger.error("win32com/pythoncom 로드 실패: %s", exc)
            return

        pythoncom.CoInitialize()
        try:
            handler = win32com.client.DispatchWithEvents(
                "Outlook.Application", OutlookEventHandler
            )
            # 인스턴스 변수 주입
            handler._root_folder = root_folder
            handler._processed_set = processed_set
            handler._log_queue = log_queue
            handler._allowed_senders = allowed_senders
            handler._allowed_recipients = allowed_recipients

            logger.info("Outlook 이벤트 모니터링 시작")
            try:
                log_queue.put_nowait("[모니터] Outlook 감시 시작")
            except queue.Full:
                pass  # nosec B110

            while not _stop_event.is_set():
                pythoncom.PumpWaitingMessages()
                time.sleep(0.5)

        except Exception as exc:
            logger.error("모니터링 루프 오류: %s", exc)
            try:
                log_queue.put_nowait(f"[오류] 모니터링 중단: {exc}")
            except queue.Full:
                pass  # nosec B110
        finally:
            pythoncom.CoUninitialize()
            logger.info("Outlook COM 해제 완료")

        logger.info("모니터링 루프 정상 종료 (stop_event 수신)")
        try:
            log_queue.put_nowait("[모니터] 감시 중지됨")
        except queue.Full:
            pass  # nosec B110

    thread = threading.Thread(target=_monitor_loop, daemon=True, name="OutlookMonitor")
    thread.start()
    logger.info("모니터링 스레드 시작됨: %s", thread.name)
    return thread


# ---------------------------------------------------------------------------
# Self-Verification Block
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # --- _has_new_po_keyword ---
    assert _has_new_po_keyword("Please find new PO attached") is True, "new PO 감지 실패"  # nosec B101
    assert _has_new_po_keyword("new ICPO 3100035140 issued") is True, "new ICPO 감지 실패"  # nosec B101
    assert _has_new_po_keyword("newPO from customer") is True, "newPO 감지 실패"  # nosec B101
    assert _has_new_po_keyword("newICPO document") is True, "newICPO 감지 실패"  # nosec B101
    assert _has_new_po_keyword("NEW PO ARRIVED") is True, "대문자 NEW PO 감지 실패"  # nosec B101
    assert _has_new_po_keyword("NEW ICPO ISSUED") is True, "대문자 NEW ICPO 감지 실패"  # nosec B101
    assert _has_new_po_keyword("regular invoice email") is False, "키워드 없음 → False 실패"  # nosec B101
    assert _has_new_po_keyword("") is False, "빈 문자열 → False 실패"  # nosec B101

    try:
        _has_new_po_keyword(None)  # type: ignore[arg-type]
        assert False, "_has_new_po_keyword None TypeError 미발생"  # nosec B101
    except TypeError:
        pass  # nosec B110

    try:
        _has_new_po_keyword(42)  # type: ignore[arg-type]
        assert False, "_has_new_po_keyword int TypeError 미발생"  # nosec B101
    except TypeError:
        pass  # nosec B110

    print("[SELF-VERIFY] _has_new_po_keyword OK")

    # --- _extract_po_number 정상 케이스 ---
    assert _extract_po_number("PO3213131") == "3213131", "PO3213131 실패"  # nosec B101
    assert _extract_po_number("PO 512345") == "512345", "PO 512345 실패"  # nosec B101
    assert _extract_po_number("ICPO3001") == "3001", "ICPO3001 실패"  # nosec B101
    assert _extract_po_number("ICPO 50001") == "50001", "ICPO 50001 실패"  # nosec B101
    assert _extract_po_number("New ICPO 3100035140") == "3100035140", "1차 패턴 ICPO 앞 케이스 실패"  # nosec B101
    assert _extract_po_number("CCI ICPO 3100035131 / some details") == "3100035131", "CCI ICPO 케이스 실패"  # nosec B101
    assert _extract_po_number("3100035130 B43234AT new ICPO") == "3100035130", "AT format 미감지"  # nosec B101
    assert _extract_po_number("Invoice Only") is None, "Invoice Only → None 실패"  # nosec B101
    assert _extract_po_number("") is None, "빈 문자열 → None 실패"  # nosec B101
    # AT 폴백은 "new ICPO" 없으면 발동하지 않음 (오탐 방지 검증)
    assert _extract_po_number("3100035130 B43234AT some other text") is None, "AT 폴백 오탐 방지 실패"  # nosec B101

    # --- _extract_po_number 예외 케이스 ---
    try:
        _extract_po_number(None)  # type: ignore[arg-type]
        assert False, "None 입력 TypeError 미발생"  # nosec B101
    except TypeError:
        pass  # nosec B110

    try:
        _extract_po_number(123)  # type: ignore[arg-type]
        assert False, "int 입력 TypeError 미발생"  # nosec B101
    except TypeError:
        pass  # nosec B110

    # --- _is_reply_or_forward ---
    assert _is_reply_or_forward("Re: test") is True, "Re: 감지 실패"  # nosec B101
    assert _is_reply_or_forward("FWD: test") is True, "FWD: 감지 실패"  # nosec B101
    assert _is_reply_or_forward("fw: hello") is True, "fw: 감지 실패"  # nosec B101
    assert _is_reply_or_forward("PO3213131 신규") is False, "일반 메일 오탐 실패"  # nosec B101

    try:
        _is_reply_or_forward(None)  # type: ignore[arg-type]
        assert False, "_is_reply_or_forward None TypeError 미발생"  # nosec B101
    except TypeError:
        pass  # nosec B110

    try:
        _is_reply_or_forward(42)  # type: ignore[arg-type]
        assert False, "_is_reply_or_forward int TypeError 미발생"  # nosec B101
    except TypeError:
        pass  # nosec B110

    # --- load_processed_set / save_processed_set (임시 디렉토리 사용) ---
    import tempfile as _tempfile

    with _tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)

        # 파일 없을 때 빈 set
        ps = load_processed_set(tmp_path)
        assert ps == set(), "처음 로드 시 빈 set 아님"  # nosec B101

        # 저장 후 재로드
        sample: Set[str] = {"3001", "50001", "3213131"}
        save_processed_set(tmp_path, sample)
        ps2 = load_processed_set(tmp_path)
        assert ps2 == sample, f"재로드 불일치: {ps2} != {sample}"  # nosec B101

    # --- None 입력 방어 ---
    try:
        load_processed_set(None)  # type: ignore[arg-type]
        assert False, "load_processed_set None TypeError 미발생"  # nosec B101
    except TypeError:
        pass  # nosec B110

    try:
        save_processed_set(None, set())  # type: ignore[arg-type]
        assert False, "save_processed_set None TypeError 미발생"  # nosec B101
    except TypeError:
        pass  # nosec B110

    try:
        start_monitoring(None, __import__("queue").Queue(), set())  # type: ignore[arg-type]
        assert False, "start_monitoring root_folder None TypeError 미발생"  # nosec B101
    except TypeError:
        pass  # nosec B110

    try:
        start_monitoring(Path("."), None, set())  # type: ignore[arg-type]
        assert False, "start_monitoring log_queue None TypeError 미발생"  # nosec B101
    except TypeError:
        pass  # nosec B110

    # --- _parse_pdf_info 타입 방어 ---
    try:
        _parse_pdf_info(None)  # type: ignore[arg-type]
        assert False, "_parse_pdf_info None TypeError 미발생"  # nosec B101
    except TypeError:
        pass  # nosec B110

    try:
        _parse_pdf_info("not_a_path")  # type: ignore[arg-type]
        assert False, "_parse_pdf_info str TypeError 미발생"  # nosec B101
    except TypeError:
        pass  # nosec B110

    # pdfplumber/pymupdf 없을 때 ("", "", "", "", "") 반환 확인 (존재하지 않는 PDF 경로)
    result = _parse_pdf_info(Path("nonexistent_test.pdf"))
    assert isinstance(result, tuple) and len(result) == 5, "_parse_pdf_info 반환 형식 오류 (5-tuple 필요)"  # nosec B101
    assert result[2] == "", "_parse_pdf_info 실패 시 order_date 빈 문자열 필요"  # nosec B101
    assert result[3] == "", "_parse_pdf_info 실패 시 incoterm 빈 문자열 필요"  # nosec B101
    assert result[4] == "", "_parse_pdf_info 실패 시 ic_code 빈 문자열 필요"  # nosec B101

    # _COMPANY_IC_MAP 매핑 검증
    assert _COMPANY_IC_MAP.get("imi critical engineering llc") == "SoCal", "SoCal 매핑 실패"  # nosec B101
    assert _COMPANY_IC_MAP.get("cci valve technology gmbh") == "AT", "AT 매핑 실패"  # nosec B101
    assert _COMPANY_IC_MAP.get("imi critical engineering (apac) pte. ltd.") == "SG", "SG 매핑 실패"  # nosec B101
    assert _COMPANY_IC_MAP.get("unknown company") is None, "미매핑 회사 None 필요"  # nosec B101
    assert _COMPANY_IC_MAP.get("unknown company", "") == "", "미매핑 회사 빈 문자열 필요"  # nosec B101

    # Bug 1: Incoterm Strategy 2 (next-line) 검증
    _full_inline = "Terms Of Delivery: FOB BUSAN\nother"
    _full_nextline = "Terms Of Delivery\nEX WORKS(NAMED PLACE), PAJU FACTORY\nother"
    _m1 = re.search(r"Terms\s+[Oo]f\s+Delivery[\s:]+([^\n]{2,80})", _full_inline, re.IGNORECASE)
    assert _m1 and "FOB" in _m1.group(1).upper(), "Bug1 Strategy1 실패"  # nosec B101
    _m2 = re.search(r"Terms\s+[Oo]f\s+Delivery[^\n]*\n\s*([^\n]{2,80})", _full_nextline, re.IGNORECASE)
    assert _m2 and "EX WORKS" in _m2.group(1).upper(), "Bug1 Strategy2 실패"  # nosec B101

    # Bug 2: 스페어 오탐 방지 검증
    _spare_pat = re.compile(r"\b(SOFTGOODS|SOFT\s+GOODS|SPARE|REPAIR\s+KIT|SEAL\s+KIT|SERVICE\s+KIT|O[\-\s]RING\s+KIT)\b", re.IGNORECASE)
    _act_pat = re.compile(r"\bACT[,\s]", re.IGNORECASE)
    _spare_only = ["ACT SOFTGOODS REPLACEMENT", "SPARE PART"]
    _actual_act = ["ACT, VALVE ITEM #1234", "description"]
    assert not any(_act_pat.search(ln) and not _spare_pat.search(ln) for ln in _spare_only), "Bug2 스페어 오탐"  # nosec B101
    assert any(_act_pat.search(ln) and not _spare_pat.search(ln) for ln in _actual_act), "Bug2 본품 미탐"  # nosec B101
    _soft_goods_line = ["ACT, Soft Goods replacement item"]
    assert not any(_act_pat.search(ln) and not _spare_pat.search(ln) for ln in _soft_goods_line), "Bug2 SOFT GOODS 오탐"  # nosec B101

    # Bug 3: MAX_PATH 트리밍 검증
    _folder_len = 80
    _avail = 259 - _folder_len - 1 - len(".msg.tmp")
    _avail = max(20, min(_avail, 200))
    assert _avail == 170, f"Bug3 avail 계산 오류: expected 170, got {_avail}"  # nosec B101
    _trimmed = ("A" * 250)[:_avail]
    assert len(_trimmed) == 170, "Bug3 트리밍 길이 오류"  # nosec B101

    # stop_event type check
    assert callable(start_monitoring)  # nosec B101
    _ev = threading.Event()
    # Verify TypeError on bad type (no Outlook needed — TypeError raised before COM call)
    try:
        start_monitoring(Path("."), queue.Queue(), set(), stop_event=42)  # type: ignore[arg-type]
        assert False, "Expected TypeError for stop_event=42"  # nosec B101
    except TypeError:
        pass  # nosec B110
    print("[SELF-VERIFY] stop_event type guard OK")

    print("[SELF-VERIFY] email_monitor.py OK")
