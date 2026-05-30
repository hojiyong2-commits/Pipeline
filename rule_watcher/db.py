# [Purpose]: SQLite 기반 영속 저장소 초기화 및 트랜잭션 컨텍스트 매니저 제공.
# [Assumptions]: 단일 프로세스 환경에서 threading.Lock으로 동시성 제어. WAL 모드로 reader/writer 분리.
# [Vulnerability & Risks]: db_path가 사용자 입력에서 파생되면 traversal 방어 필요 — 현재는 config.DB_PATH (환경변수 통제). 향후 UI에서 동적 변경 시 _safe_resolve() 도입 필요.
# [Improvement]: SQLAlchemy ORM 도입, alembic 마이그레이션, 연결 풀.
"""SQLite 데이터베이스 초기화 및 공통 유틸."""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from typing import Generator

from rule_watcher.config import DB_PATH

_lock = threading.Lock()


def get_connection(db_path: str = DB_PATH) -> sqlite3.Connection:
    """SQLite 연결 객체 생성. WAL 모드 + FK 활성화.

    Args:
        db_path: SQLite 파일 경로.
    Returns:
        sqlite3.Connection (row_factory=sqlite3.Row).
    Raises:
        TypeError: db_path가 None이거나 str이 아닌 경우.
        ValueError: db_path가 빈 문자열인 경우.
    """
    if db_path is None:
        raise TypeError("db_path must not be None")
    if not isinstance(db_path, str):
        raise TypeError(f"db_path must be str, got {type(db_path).__name__}")
    if len(db_path) == 0:
        raise ValueError("db_path must not be empty")

    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def db_conn(db_path: str = DB_PATH) -> Generator[sqlite3.Connection, None, None]:
    """트랜잭션 컨텍스트 매니저 — 자동 commit/rollback/close.

    Args:
        db_path: SQLite 파일 경로.
    Yields:
        sqlite3.Connection.
    """
    if db_path is None:
        raise TypeError("db_path must not be None")
    if not isinstance(db_path, str):
        raise TypeError(f"db_path must be str, got {type(db_path).__name__}")

    with _lock:
        conn = get_connection(db_path)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def init_db(db_path: str = DB_PATH) -> None:
    """스키마 초기화 — 앱 시작 시 1회 호출.

    Args:
        db_path: SQLite 파일 경로.
    Raises:
        TypeError: db_path가 None이거나 str이 아닌 경우.
        ValueError: db_path가 빈 문자열인 경우.
    """
    if db_path is None:
        raise TypeError("db_path must not be None")
    if not isinstance(db_path, str):
        raise TypeError(f"db_path must be str, got {type(db_path).__name__}")
    if len(db_path) == 0:
        raise ValueError("db_path must not be empty")

    with db_conn(db_path) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS watchlist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL DEFAULT '',
                market TEXT DEFAULT 'KOSPI',
                added_at TEXT DEFAULT (datetime('now')),
                notes TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS rulebook (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                conditions_json TEXT NOT NULL DEFAULT '[]',
                logic TEXT NOT NULL DEFAULT 'AND',
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS alerts_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                rule_id INTEGER NOT NULL,
                rule_name TEXT NOT NULL,
                triggered_at TEXT DEFAULT (datetime('now')),
                reason_ko TEXT NOT NULL DEFAULT '',
                acknowledged INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS ohlcv_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                date TEXT NOT NULL,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                cached_at TEXT DEFAULT (datetime('now')),
                UNIQUE(ticker, date)
            );

            CREATE TABLE IF NOT EXISTS provider_config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_alerts_ticker ON alerts_log(ticker);
            CREATE INDEX IF NOT EXISTS idx_alerts_triggered ON alerts_log(triggered_at);
            CREATE INDEX IF NOT EXISTS idx_ohlcv_ticker_date ON ohlcv_cache(ticker, date);
        """)


if __name__ == "__main__":
    # 자가 검증
    import tempfile
    import os as _os

    tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
    try:
        init_db(tmp_db)
        with db_conn(tmp_db) as c:
            tables = [r[0] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()]
        expected = {"alerts_log", "ohlcv_cache", "provider_config", "rulebook", "watchlist"}
        assert expected.issubset(set(tables)), f"테이블 누락: {expected - set(tables)}"

        # None 입력 방어 검증
        try:
            init_db(None)  # type: ignore[arg-type]
            raise AssertionError("None 입력 예외 미발생")
        except TypeError:
            pass

        # 빈 문자열 방어
        try:
            init_db("")
            raise AssertionError("빈 문자열 예외 미발생")
        except ValueError:
            pass

        print("[SELF-VERIFY] db.py OK")
    finally:
        if _os.path.exists(tmp_db):
            _os.unlink(tmp_db)
