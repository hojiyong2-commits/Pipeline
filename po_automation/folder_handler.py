"""
folder_handler.py — 프로젝트 폴더 생성 및 임시 파일 복사 모듈.

location 값("SoCal" / "SG")에 따라 적절한 경로에 폴더를 생성하고,
임시 폴더에서 프로젝트 폴더로 파일을 복사합니다.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 허용된 location 값 집합
_VALID_LOCATIONS: frozenset[str] = frozenset({"SoCal", "SG"})


def _safe_resolve(user_path: str, allowed_root: Path) -> Path:
    """경로 순회 공격 방지: resolve() 후 allowed_root 검증.

    Args:
        user_path: 검증할 상대 또는 절대 경로 문자열.
        allowed_root: 허용된 루트 디렉토리 Path.

    Returns:
        검증된 절대 경로 Path 객체.

    Raises:
        ValueError: 경로가 allowed_root 외부로 탈출하면 raise.
    """
    resolved = (allowed_root / user_path).resolve()
    try:
        resolved.relative_to(allowed_root.resolve())
    except ValueError:
        raise ValueError(
            f"Path traversal detected: '{user_path}' escapes allowed root '{allowed_root}'"
        )
    return resolved


def create_project_folder(location: str, folder_name: str, config: dict) -> str:
    """location에 따라 적절한 경로에 프로젝트 폴더를 생성합니다.

    "SoCal" → config["socal_path"] / folder_name
    "SG"    → config["sg_path"]    / folder_name
    그 외   → ValueError raise

    Args:
        location: 프로젝트 위치 문자열 ("SoCal" 또는 "SG").
        folder_name: 생성할 폴더명 문자열 (1~255자).
        config: 설정 딕셔너리 (socal_path, sg_path 포함).

    Returns:
        생성된 폴더의 절대 경로 문자열.

    Raises:
        TypeError: 인자가 None이거나 타입 불일치 시.
        ValueError: location이 허용되지 않는 값이거나 folder_name이 빈 문자열/255자 초과 시.
        ValueError: config에 필수 키가 없을 때.
        OSError: 폴더 생성 실패 시.
    """
    # AL type_valid: None → isinstance 순서 고정
    if location is None:
        raise TypeError("location must not be None")
    if not isinstance(location, str):
        raise TypeError(f"location must be str, got {type(location).__name__}")
    if len(location) == 0:
        raise ValueError("location must not be empty string")

    if folder_name is None:
        raise TypeError("folder_name must not be None")
    if not isinstance(folder_name, str):
        raise TypeError(f"folder_name must be str, got {type(folder_name).__name__}")
    if len(folder_name) == 0:
        raise ValueError("folder_name must not be empty string")
    if len(folder_name) > 255:
        raise ValueError(f"folder_name must be 1~255 chars, got {len(folder_name)}")

    if config is None:
        raise TypeError("config must not be None")
    if not isinstance(config, dict):
        raise TypeError(f"config must be dict, got {type(config).__name__}")
    if len(config) == 0:
        raise ValueError("config must not be empty")

    # location 값 검증
    if location not in _VALID_LOCATIONS:
        raise ValueError(
            f"location must be one of {sorted(_VALID_LOCATIONS)}, got '{location}'"
        )

    # config에서 베이스 경로 결정
    if location == "SoCal":
        if "socal_path" not in config:
            raise ValueError("config에 'socal_path' 키 없음")
        base_path = Path(config["socal_path"])
    else:  # "SG"
        if "sg_path" not in config:
            raise ValueError("config에 'sg_path' 키 없음")
        base_path = Path(config["sg_path"])

    # 경로 순회 방지 검증 (_safe_resolve 패턴)
    dest_folder = _safe_resolve(folder_name, base_path)

    dest_folder.mkdir(parents=True, exist_ok=True)
    logger.info("프로젝트 폴더 생성/확인: %s", dest_folder)

    return str(dest_folder)


def copy_temp_to_project(
    temp_folder: str, po_number: str, dest_folder: str
) -> list[str]:
    """임시 폴더(temp_folder/po_number)의 파일을 dest_folder로 복사합니다.

    임시 폴더가 없으면 경고 로그를 남기고 빈 리스트를 반환합니다(예외 금지).
    shutil.copy2를 사용하여 메타데이터 포함 복사합니다.

    Args:
        temp_folder: 임시 폴더 루트 경로 문자열.
        po_number: PO 번호 문자열 (서브 폴더명으로 사용, 1~255자).
        dest_folder: 복사 대상 프로젝트 폴더 절대 경로 문자열.

    Returns:
        복사된 파일의 절대 경로 문자열 목록 (복사 실패 또는 소스 없으면 빈 리스트).

    Raises:
        TypeError: 인자가 None이거나 타입 불일치 시.
        ValueError: 인자가 빈 문자열이거나 255자 초과 시.
    """
    # AL type_valid: None → isinstance 순서 고정
    if temp_folder is None:
        raise TypeError("temp_folder must not be None")
    if not isinstance(temp_folder, str):
        raise TypeError(f"temp_folder must be str, got {type(temp_folder).__name__}")
    if len(temp_folder) == 0:
        raise ValueError("temp_folder must not be empty string")
    if len(temp_folder) > 255:
        raise ValueError(f"temp_folder must be 1~255 chars, got {len(temp_folder)}")

    if po_number is None:
        raise TypeError("po_number must not be None")
    if not isinstance(po_number, str):
        raise TypeError(f"po_number must be str, got {type(po_number).__name__}")
    if len(po_number) == 0:
        raise ValueError("po_number must not be empty string")
    if len(po_number) > 255:
        raise ValueError(f"po_number must be 1~255 chars, got {len(po_number)}")

    if dest_folder is None:
        raise TypeError("dest_folder must not be None")
    if not isinstance(dest_folder, str):
        raise TypeError(f"dest_folder must be str, got {type(dest_folder).__name__}")
    if len(dest_folder) == 0:
        raise ValueError("dest_folder must not be empty string")
    if len(dest_folder) > 255:
        raise ValueError(f"dest_folder must be 1~255 chars, got {len(dest_folder)}")

    src_folder = Path(temp_folder) / po_number

    # 소스 폴더가 없으면 경고 로그 후 빈 리스트 반환 (예외 금지)
    if not src_folder.exists():
        logger.warning(
            "임시 폴더 없음 — 복사 건너뜀: %s (PO번호=%s)", src_folder, po_number
        )
        return []

    if not src_folder.is_dir():
        logger.warning(
            "임시 경로가 디렉토리가 아님 — 복사 건너뜀: %s", src_folder
        )
        return []

    dest_path = Path(dest_folder)
    dest_path.mkdir(parents=True, exist_ok=True)

    copied_files: list[str] = []

    for src_file in src_folder.iterdir():
        if not src_file.is_file():
            # 서브 디렉토리는 건너뜀 (단순 파일 복사)
            logger.debug("서브 디렉토리 건너뜀: %s", src_file)
            continue

        dest_file = dest_path / src_file.name
        try:
            shutil.copy2(str(src_file), str(dest_file))
            copied_files.append(str(dest_file))
            logger.info("파일 복사 완료: %s → %s", src_file, dest_file)
        except OSError as exc:
            logger.error("파일 복사 실패: %s → %s | 오류: %s", src_file, dest_file, exc)
            # 전체 실패 시에도 빈 결과 반환 정책에 따라 계속 진행
            continue

    if len(copied_files) == 0:
        logger.warning(
            "복사된 파일 없음 (소스 폴더=%s, PO번호=%s)", src_folder, po_number
        )
    else:
        logger.info("복사 완료: %d개 파일 → %s", len(copied_files), dest_folder)

    return copied_files


# ---------------------------------------------------------------------------
# Self-Verification Block
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import os
    import tempfile as _tempfile

    # --- create_project_folder: None 입력 → TypeError ---
    try:
        create_project_folder(None, "folder", {})  # type: ignore[arg-type]
        assert False, "location None 예외 미발생"
    except TypeError:
        pass

    try:
        create_project_folder("SoCal", None, {})  # type: ignore[arg-type]
        assert False, "folder_name None 예외 미발생"
    except TypeError:
        pass

    try:
        create_project_folder("SoCal", "folder", None)  # type: ignore[arg-type]
        assert False, "config None 예외 미발생"
    except TypeError:
        pass

    # --- create_project_folder: 허용되지 않는 location → ValueError ---
    try:
        create_project_folder("NYC", "folder", {"socal_path": "/tmp", "sg_path": "/tmp"})
        assert False, "잘못된 location 예외 미발생"
    except ValueError:
        pass

    # --- create_project_folder: 정상 케이스 ---
    with _tempfile.TemporaryDirectory() as tmpdir:
        cfg = {"socal_path": tmpdir, "sg_path": tmpdir}
        result = create_project_folder("SoCal", "TestProject", cfg)
        assert os.path.isdir(result), "폴더 생성 실패"
        assert result.endswith("TestProject"), f"경로 오류: {result}"

    # --- copy_temp_to_project: None 입력 → TypeError ---
    try:
        copy_temp_to_project(None, "31", "/dest")  # type: ignore[arg-type]
        assert False, "temp_folder None 예외 미발생"
    except TypeError:
        pass

    # --- copy_temp_to_project: 존재하지 않는 소스 → 빈 리스트 반환 ---
    result_empty = copy_temp_to_project("/nonexistent_path_abc123", "31", "/tmp")
    assert result_empty == [], f"빈 리스트 반환 실패: {result_empty}"

    # --- copy_temp_to_project: 정상 케이스 ---
    with _tempfile.TemporaryDirectory() as src_root:
        with _tempfile.TemporaryDirectory() as dst_root:
            src_po = Path(src_root) / "31"
            src_po.mkdir()
            (src_po / "invoice.pdf").write_text("test content", encoding="utf-8")
            (src_po / "email.html").write_text("<html>test</html>", encoding="utf-8")

            copied = copy_temp_to_project(src_root, "31", dst_root)
            assert len(copied) == 2, f"복사 파일 수 불일치: {len(copied)}"
            for cp in copied:
                assert os.path.exists(cp), f"복사된 파일 없음: {cp}"

    print("[SELF-VERIFY] folder_handler.py OK")
