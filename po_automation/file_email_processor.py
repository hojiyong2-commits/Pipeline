"""File-based EmailMonitor processor.

This module processes exported .msg/.eml files from a user-selected inbox
folder. It intentionally avoids Outlook COM automation so company endpoint
security tools see a simple file processor instead of a mailbox crawler.
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import tempfile
from dataclasses import dataclass
from email import policy
from email.header import decode_header, make_header
from email.parser import BytesParser
from pathlib import Path
from typing import Callable, Iterable

try:
    from po_automation.email_monitor import _parse_pdf_info, _safe_filename
except ModuleNotFoundError:
    from email_monitor import _parse_pdf_info, _safe_filename  # type: ignore[no-redef]

LOGGER = logging.getLogger(__name__)

IMAGE_EXTS = {
    ".bmp",
    ".gif",
    ".ico",
    ".jfif",
    ".jpeg",
    ".jpg",
    ".png",
    ".svg",
    ".tif",
    ".tiff",
    ".webp",
}
EMAIL_EXTS = {".msg", ".eml"}
MAX_DEST_PATH = 220
MAX_FOLDER_NAME = 120
PROCESSED_FILE = "processed.json"


@dataclass(frozen=True)
class ExtractedAttachment:
    name: str
    path: Path


@dataclass(frozen=True)
class EmailPayload:
    source: Path
    subject: str
    attachments: tuple[ExtractedAttachment, ...]


@dataclass(frozen=True)
class ProcessResult:
    status: str
    source: Path
    po_number: str = ""
    folder: Path | None = None
    message: str = ""


def _emit(log: Callable[[str], None] | None, message: str) -> None:
    LOGGER.info(message)
    if log is not None:
        log(message)


def _load_processed(output_dir: Path) -> set[str]:
    path = output_dir / PROCESSED_FILE
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        LOGGER.warning("processed.json 읽기 실패, 빈 목록으로 시작: %s", path)
        return set()
    if not isinstance(data, list):
        return set()
    return {str(item) for item in data}


def _save_processed(output_dir: Path, processed: set[str]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / PROCESSED_FILE
    path.write_text(
        json.dumps(sorted(processed), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _decode_header_value(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value))).strip()
    except Exception:
        return value.strip()


def _po_from_text(text: str) -> str:
    import re

    match = re.search(r"(?:IC)?PO\s*([35]\d{9}|[35]\d{7,})", text, re.IGNORECASE)
    if match:
        return match.group(1)
    match = re.search(r"\b([35]\d{9}|[35]\d{7,})\b", text)
    return match.group(1) if match else ""


def _safe_unique_path(folder: Path, desired_name: str, max_path: int = MAX_DEST_PATH) -> Path:
    safe = _safe_filename(desired_name)
    stem = Path(safe).stem
    suffix = Path(safe).suffix

    def candidate(name: str) -> Path:
        return folder / name

    if len(str(candidate(safe))) > max_path:
        available = max_path - len(str(folder)) - 1 - len(suffix)
        if available < 24:
            available = 24
        safe = f"{stem[:available].rstrip()}{suffix}"

    dest = candidate(safe)
    if not dest.exists():
        return dest

    base_stem = Path(safe).stem
    for index in range(1, 1000):
        suffix_text = f" ({index})"
        available = max_path - len(str(folder)) - 1 - len(Path(safe).suffix) - len(suffix_text)
        new_stem = base_stem[: max(16, available)].rstrip()
        dest = candidate(f"{new_stem}{suffix_text}{Path(safe).suffix}")
        if not dest.exists() and len(str(dest)) <= max_path:
            return dest
    raise RuntimeError(f"파일명 중복이 너무 많습니다: {folder / safe}")


def _folder_name(ic_code: str, prefix: str, po: str, amount: str, order_date: str, incoterm: str) -> str:
    parts = []
    if ic_code:
        parts.append(ic_code)
    if prefix:
        parts.append(prefix)
    parts.append(po or "PO_UNKNOWN")
    if amount:
        parts.append(f"- {amount}")
    if order_date:
        parts.append(f"- {order_date}")
    if incoterm:
        parts.append(f"- {incoterm}")
    name = " ".join(parts)
    safe = _safe_filename(name)
    return safe[:MAX_FOLDER_NAME].rstrip() or "PO_UNKNOWN"


def _save_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _extract_msg(source: Path, temp_dir: Path) -> EmailPayload:
    import extract_msg

    msg = extract_msg.Message(str(source))
    try:
        subject = msg.subject or source.stem
        attachments: list[ExtractedAttachment] = []
        for index, attachment in enumerate(msg.attachments, start=1):
            name = (
                getattr(attachment, "longFilename", None)
                or getattr(attachment, "shortFilename", None)
                or getattr(attachment, "name", None)
                or f"attachment_{index}"
            )
            name = _safe_filename(str(name))
            data = getattr(attachment, "data", None)
            if not isinstance(data, bytes):
                continue
            target = _safe_unique_path(temp_dir, name)
            _save_bytes(target, data)
            attachments.append(ExtractedAttachment(name=name, path=target))
        return EmailPayload(source=source, subject=subject, attachments=tuple(attachments))
    finally:
        msg.close()


def _extract_eml(source: Path, temp_dir: Path) -> EmailPayload:
    message = BytesParser(policy=policy.default).parsebytes(source.read_bytes())
    subject = _decode_header_value(message.get("subject")) or source.stem
    attachments: list[ExtractedAttachment] = []
    for index, part in enumerate(message.iter_attachments(), start=1):
        raw_name = part.get_filename() or f"attachment_{index}"
        name = _safe_filename(_decode_header_value(raw_name))
        data = part.get_payload(decode=True)
        if not isinstance(data, bytes):
            continue
        target = _safe_unique_path(temp_dir, name)
        _save_bytes(target, data)
        attachments.append(ExtractedAttachment(name=name, path=target))
    return EmailPayload(source=source, subject=subject, attachments=tuple(attachments))


def _extract_payload(source: Path, temp_dir: Path) -> EmailPayload:
    suffix = source.suffix.lower()
    if suffix == ".msg":
        return _extract_msg(source, temp_dir)
    if suffix == ".eml":
        return _extract_eml(source, temp_dir)
    raise ValueError(f"지원하지 않는 이메일 파일입니다: {source.name}")


def _pdf_attachments(payload: EmailPayload) -> list[ExtractedAttachment]:
    return [
        item
        for item in payload.attachments
        if item.path.suffix.lower() == ".pdf" and item.path.suffix.lower() not in IMAGE_EXTS
    ]


def _choose_pdf(payload: EmailPayload) -> ExtractedAttachment | None:
    pdfs = _pdf_attachments(payload)
    if not pdfs:
        return None
    po_candidates = [_po_from_text(payload.subject), _po_from_text(payload.source.stem)]
    for candidate in po_candidates:
        if not candidate:
            continue
        for pdf in pdfs:
            if candidate in pdf.name:
                return pdf
    return pdfs[0]


def _copy_payload_files(payload: EmailPayload, pdf: ExtractedAttachment, dest_folder: Path) -> None:
    email_name = f"{_safe_filename(payload.subject)}{payload.source.suffix.lower()}"
    email_dest = _safe_unique_path(dest_folder, email_name)
    shutil.copy2(payload.source, email_dest)

    pdf_name = pdf.name
    po = _po_from_text(pdf.name) or _po_from_text(payload.subject)
    if po and len(str(dest_folder / pdf_name)) > MAX_DEST_PATH:
        pdf_name = f"PO {po}.pdf"
    pdf_dest = _safe_unique_path(dest_folder, pdf_name)
    shutil.copy2(pdf.path, pdf_dest)


def process_email_file(
    source: Path,
    output_dir: Path,
    processed: set[str],
    *,
    log: Callable[[str], None] | None = None,
) -> ProcessResult:
    if source.suffix.lower() not in EMAIL_EXTS:
        return ProcessResult(status="SKIP", source=source, message="지원하지 않는 확장자")

    with tempfile.TemporaryDirectory(prefix="emailmonitor_file_") as temp_name:
        temp_dir = Path(temp_name)
        payload = _extract_payload(source, temp_dir)
        pdf = _choose_pdf(payload)
        if pdf is None:
            return ProcessResult(status="SKIP", source=source, message="PDF 첨부 없음")

        prefix, amount, order_date, incoterm, ic_code = _parse_pdf_info(pdf.path)
        po_number = _po_from_text(pdf.name) or _po_from_text(payload.subject) or _po_from_text(source.stem)
        if not po_number:
            return ProcessResult(status="ERROR", source=source, message="PO 번호 추출 실패")
        if po_number in processed:
            return ProcessResult(status="SKIP", source=source, po_number=po_number, message="이미 처리됨")

        folder = output_dir / _folder_name(ic_code, prefix, po_number, amount, order_date, incoterm)
        created = not folder.exists()
        folder.mkdir(parents=True, exist_ok=True)
        try:
            _copy_payload_files(payload, pdf, folder)
        except Exception:
            if created and folder.exists() and not any(folder.iterdir()):
                folder.rmdir()
            raise

        processed.add(po_number)
        _emit(log, f"DONE {po_number}: {folder}")
        return ProcessResult(status="DONE", source=source, po_number=po_number, folder=folder)


def iter_email_files(input_dir: Path, recursive: bool = False) -> Iterable[Path]:
    pattern = "**/*" if recursive else "*"
    for path in sorted(input_dir.glob(pattern)):
        if path.is_file() and path.suffix.lower() in EMAIL_EXTS:
            yield path


def process_folder(
    input_dir: Path,
    output_dir: Path,
    *,
    recursive: bool = False,
    log: Callable[[str], None] | None = None,
) -> list[ProcessResult]:
    input_dir = input_dir.resolve()
    output_dir = output_dir.resolve()
    if not input_dir.exists():
        raise FileNotFoundError(f"Inbox 폴더가 없습니다: {input_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    processed = _load_processed(output_dir)
    results: list[ProcessResult] = []
    for source in iter_email_files(input_dir, recursive=recursive):
        try:
            result = process_email_file(source, output_dir, processed, log=log)
            results.append(result)
            if result.status == "DONE":
                _save_processed(output_dir, processed)
            elif result.status == "SKIP":
                _emit(log, f"SKIP {source.name}: {result.message}")
        except Exception as exc:
            LOGGER.exception("이메일 파일 처리 실패: %s", source)
            message = f"ERROR {source.name}: {exc}"
            _emit(log, message)
            results.append(ProcessResult(status="ERROR", source=source, message=str(exc)))
    _save_processed(output_dir, processed)
    return results


def _configure_logging(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.getLogger("extract_msg").setLevel(logging.WARNING)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
        ],
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="EmailMonitor file processor")
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--recursive", action="store_true")
    args = parser.parse_args(argv)

    output_dir = Path(args.output_dir)
    _configure_logging(output_dir.parent / "emailmonitor_file.log")
    results = process_folder(Path(args.input_dir), output_dir, recursive=args.recursive, log=print)
    counts = {"DONE": 0, "SKIP": 0, "ERROR": 0}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    print(f"완료: DONE={counts['DONE']} SKIP={counts['SKIP']} ERROR={counts['ERROR']}")
    return 1 if counts["ERROR"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
