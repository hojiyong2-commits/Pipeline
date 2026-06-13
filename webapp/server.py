"""Agent Office Live Dashboard — FastAPI + WebSocket server.

Pipeline: FEAT-20260505-5CCB / IMP-20260505-0DC9
Visualizes the Self-Evolving Multi-Agent pipeline as an isometric office
with 11 colored hamster agents and Mindasol (the orchestrator).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

try:
    from watchfiles import awatch
except ImportError:
    awatch = None  # type: ignore[assignment]

# ----------------------------------------------------------------------
# Path resolution — sys._MEIPASS aware (PyInstaller compatible even though
# this app is not packaged; rule applies per CLAUDE.md FS guidance).
# ----------------------------------------------------------------------
def _resolve_base_dir() -> Path:
    """Return the directory that anchors all relative paths.

    Priority:
      1. PyInstaller _MEIPASS (frozen)
      2. Module file directory's parent (project root when run as
         `python -m webapp.server` or `python webapp/server.py`)
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return Path(__file__).resolve().parent.parent


BASE_DIR: Path = _resolve_base_dir()
WEBAPP_DIR: Path = Path(__file__).resolve().parent
STATIC_DIR: Path = WEBAPP_DIR / "static"
PIPELINE_STATE_FILE: Path = BASE_DIR / "pipeline_state.json"
TEST_RESULTS_FILE: Path = BASE_DIR / "test_results.jsonl"
TOKEN_LOG_FILE: Path = BASE_DIR / "logs" / "token_log.jsonl"
CONTROL_FILE: Path = BASE_DIR / "agent_office_control.json"
CLAUDE_LOG_DIR: Path = BASE_DIR / "logs" / "claude_runs"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_TOKEN_BUDGET = 200_000

AGENT_TOKEN_IDS = [
    "pm-agent",
    "dev-agent",
    "ui-app-agent",
    "qa-agent",
    "security-agent",
    "build-agent",
    "test-harness-agent",
    "prompt-architect-agent",
    "agent-factory-agent",
    "protocol-evolution-agent",
    "power-automate-agent",
]

# Encoding fallback chain per CLAUDE.md FS rules
_ENCODINGS = ("utf-8", "cp949", "latin-1")

logger = logging.getLogger("agent_office")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

CLAUDE_PROCESS: Optional[subprocess.Popen] = None
CLAUDE_LOG_PATH: Optional[Path] = None


# ----------------------------------------------------------------------
# Safe file reading with encoding fallback (FS.encoding requirement)
# ----------------------------------------------------------------------
def _safe_read_text(path: Path) -> Optional[str]:
    """Read a text file with utf-8 -> cp949 -> latin-1 fallback.

    Returns None if the file does not exist or cannot be read.
    """
    if not isinstance(path, Path):
        raise TypeError(f"path must be Path, got {type(path).__name__}")
    if not path.exists():
        return None
    last_err: Optional[BaseException] = None
    for enc in _ENCODINGS:
        try:
            return path.read_text(encoding=enc)
        except UnicodeDecodeError as exc:
            last_err = exc
            continue
        except OSError as exc:
            logger.warning("OS error reading %s: %s", path, exc)
            return None
    logger.warning("All encodings failed for %s: %s", path, last_err)
    return None


def _safe_path(p: str | Path) -> Path:
    """Resolve a path, blocking traversal segments (FS.traversal)."""
    candidate = Path(p)
    if ".." in candidate.parts:
        raise ValueError(f"path traversal segments not allowed: {p}")
    return candidate.resolve()


def _safe_read_json(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    """Read a JSON object with the same encoding fallback as text files."""
    text = _safe_read_text(path)
    if text is None:
        return dict(default)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("JSON parse error in %s", path)
        return dict(default)
    if not isinstance(data, dict):
        return dict(default)
    return data


def _write_json_atomic(path: Path, data: Dict[str, Any]) -> None:
    """Atomically write a UTF-8 JSON file in the project directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=str(path.parent), delete=False
    ) as tmp:
        json.dump(data, tmp, ensure_ascii=False, indent=2)
        tmp.write("\n")
        tmp_name = tmp.name
    os.replace(tmp_name, path)


def load_control_state() -> Dict[str, Any]:
    """Load the local Agent Office control state."""
    default = {
        "emergency_stop": False,
        "pm_instruction": "",
        "last_submitted_task": "",
        "claude_running": False,
        "claude_pid": None,
        "claude_log": "",
        "claude_prompt_file": "",
        "submit_error": "",
        "updated_at": None,
        "events": [],
    }
    data = _safe_read_json(CONTROL_FILE, default)
    for key, value in default.items():
        data.setdefault(key, value)
    if not isinstance(data.get("events"), list):
        data["events"] = []
    return data


def _now_iso() -> str:
    import datetime as _dt

    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def save_control_state(state: Dict[str, Any], message: str) -> Dict[str, Any]:
    """Persist control state and append a small local event."""
    state["updated_at"] = _now_iso()
    events = state.setdefault("events", [])
    if isinstance(events, list):
        events.append({"ts": state["updated_at"], "msg": message})
        del events[:-30]
    _write_json_atomic(CONTROL_FILE, state)
    return state


def append_pipeline_event(message: str) -> None:
    """Append a user-visible event to pipeline_state.json without changing phases."""
    state = _safe_read_json(PIPELINE_STATE_FILE, {})
    if not state:
        return
    ts = _now_iso().replace("+00:00", "Z")
    events = state.setdefault("event_log", [])
    if isinstance(events, list):
        events.append({"ts": ts, "msg": message})
        del events[:-100]
    state["updated_at"] = ts
    _write_json_atomic(PIPELINE_STATE_FILE, state)


def _refresh_claude_status(control: Dict[str, Any]) -> Dict[str, Any]:
    """Reflect the currently launched Claude CLI process in the payload."""
    global CLAUDE_PROCESS

    proc = CLAUDE_PROCESS
    if proc is None:
        control["claude_running"] = False
        return control

    returncode = proc.poll()
    control["claude_pid"] = proc.pid
    control["claude_running"] = returncode is None
    if returncode is not None:
        control["last_claude_returncode"] = returncode
        CLAUDE_PROCESS = None
    return control


def _build_claude_prompt(user_message: str) -> str:
    return (
        "Agent Office webapp submitted the following user task.\n\n"
        "Use Claude Code itself as the orchestrator. Follow CLAUDE.md and the "
        ".claude/agents contracts in this workspace. Start from the current "
        "pipeline state, call pipeline.py check/interface/record commands as the "
        "protocol requires, and do not wait for manual PowerShell instructions "
        "when the next safe action is clear.\n\n"
        "User task:\n"
        f"{user_message.strip()}\n"
    )


def _launch_claude_cli(user_message: str) -> Dict[str, Any]:
    """Launch `claude -p` as a thin process-management bridge."""
    global CLAUDE_PROCESS, CLAUDE_LOG_PATH

    if CLAUDE_PROCESS is not None and CLAUDE_PROCESS.poll() is None:
        raise RuntimeError("Claude Code CLI is already running.")

    claude_path = shutil.which("claude.cmd") if os.name == "nt" else shutil.which("claude")
    if not claude_path:
        claude_path = shutil.which("claude")
    if not claude_path:
        raise FileNotFoundError("claude CLI not found on PATH.")

    CLAUDE_LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = _now_iso().replace(":", "").replace("+0000", "Z").replace("+00:00", "Z")
    log_path = CLAUDE_LOG_DIR / f"claude_{stamp}.log"
    prompt_path = CLAUDE_LOG_DIR / f"claude_{stamp}.prompt.txt"
    prompt_path.write_text(_build_claude_prompt(user_message), encoding="utf-8")

    cli_prompt = (
        f"Read the Agent Office task prompt from {prompt_path} and execute it. "
        "Use this workspace's CLAUDE.md and .claude/agents protocol."
    )
    command = [claude_path, "-p", cli_prompt]

    startupinfo = None
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

    with log_path.open("ab", buffering=0) as log_handle:
        log_handle.write(
            (
                f"[{_now_iso()}] Agent Office submit -> claude -p\n"
                f"cwd={BASE_DIR}\n\n"
                f"prompt_file={prompt_path}\n\n"
            ).encode("utf-8")
        )
        proc = subprocess.Popen(
            command,
            cwd=str(BASE_DIR),
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            startupinfo=startupinfo,
        )

    CLAUDE_PROCESS = proc
    CLAUDE_LOG_PATH = log_path
    return {"pid": proc.pid, "log": str(log_path), "prompt_file": str(prompt_path)}


async def _handle_submit_task(request: Request) -> JSONResponse:
    body = await request.json()
    message = body.get("message") if isinstance(body, dict) else ""
    if not isinstance(message, str):
        message = ""
    message = message.strip()[:12000]

    control = load_control_state()
    control["pm_instruction"] = message
    control["last_submitted_task"] = message
    control["submit_error"] = ""

    if not message:
        control["submit_error"] = "empty task"
        save_control_state(control, "Agent Office submit rejected: empty task")
        append_pipeline_event("Agent Office submit rejected: empty task")
        payload = build_full_payload()
        await manager.broadcast(payload)
        return JSONResponse(payload)

    try:
        launched = _launch_claude_cli(message)
    except RuntimeError as exc:
        control["submit_error"] = str(exc)
        save_control_state(control, f"Agent Office submit queued/blocked: {exc}")
        append_pipeline_event(f"Agent Office submit blocked: {exc}")
    except FileNotFoundError as exc:
        control["submit_error"] = str(exc)
        save_control_state(control, f"Agent Office submit failed: {exc}")
        append_pipeline_event(f"Agent Office submit failed: {exc}")
    except OSError as exc:
        control["submit_error"] = str(exc)
        save_control_state(control, f"Agent Office submit failed: {exc}")
        append_pipeline_event(f"Agent Office submit failed: {exc}")
    else:
        control["emergency_stop"] = False
        control["claude_running"] = True
        control["claude_pid"] = launched["pid"]
        control["claude_log"] = launched["log"]
        control["claude_prompt_file"] = launched["prompt_file"]
        save_control_state(control, f"Claude Code CLI launched: pid={launched['pid']}")
        append_pipeline_event(f"Agent Office submit -> Claude Code CLI launched pid={launched['pid']}")

    payload = build_full_payload()
    await manager.broadcast(payload)
    return JSONResponse(payload)


# ----------------------------------------------------------------------
# Pipeline state parsing
# ----------------------------------------------------------------------
def load_pipeline_state() -> Dict[str, Any]:
    """Parse pipeline_state.json into a normalized payload.

    Returns a dict with shape:
      {
        "pipeline_id": str | None,
        "description": str,
        "phases": [ {name, status, ...}, ... ],
        "current_phase_index": int,
        "events": [ ... last 20 ... ],
        "raw": <full json dict>,
      }

    Falls back to an empty payload if the file is missing or unparseable.
    """
    text = _safe_read_text(PIPELINE_STATE_FILE)
    if text is None:
        return {
            "pipeline_id": None,
            "description": "(no pipeline running)",
            "phases": [],
            "current_phase_index": -1,
            "events": [],
            "raw": {},
        }
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("pipeline_state.json parse error: %s", exc)
        return {
            "pipeline_id": None,
            "description": f"(parse error: {exc.msg})",
            "phases": [],
            "current_phase_index": -1,
            "events": [],
            "raw": {},
        }

    # pipeline.py stores phases as a dict keyed by phase short-name (pm/dev/qa/...).
    # Some external states may use a list — we accept both.
    PHASE_ORDER = ["pm", "dev", "qa", "sec", "build", "harness", "architect"]
    PHASE_LABELS = {
        "pm": "Phase 1 - PM (Planning)",
        "dev": "Phase 2 - Dev (Implementation)",
        "qa": "Phase 4 - QA (Verification)",
        "sec": "Phase 5 - Security (Audit)",
        "build": "Phase 6 - Build (Packaging)",
        "harness": "Phase 7 - Harness (Benchmark)",
        "architect": "Phase 8 - Architect (RCA)",
    }

    phases_raw = raw.get("phases")
    phases: List[Dict[str, Any]] = []
    current_idx = -1

    def _normalize_one(key: str, p: Dict[str, Any], idx: int) -> Dict[str, Any]:
        if not isinstance(p, dict):
            p = {}
        status = (p.get("status") or "PENDING")
        if not isinstance(status, str):
            status = "PENDING"
        status = status.upper()
        evidence = p.get("evidence") or {}
        if not isinstance(evidence, dict):
            evidence = {}
        files = evidence.get("files") or p.get("files") or []
        if isinstance(files, str):
            files = [f.strip() for f in files.split(",") if f.strip()]
        return {
            "name": p.get("name") or PHASE_LABELS.get(key, f"Phase {idx + 1}"),
            "phase_key": key,
            "status": status,
            "started_at": p.get("started_at"),
            "completed_at": p.get("completed_at"),
            "files": files,
            "result": p.get("result") or evidence.get("result"),
            "score": p.get("score") if p.get("score") is not None else evidence.get("score"),
            "verdict": p.get("verdict") or evidence.get("verdict"),
            "risk": p.get("risk") or evidence.get("risk"),
            "exe": p.get("exe") or evidence.get("exe"),
            "notes": p.get("notes"),
        }

    if isinstance(phases_raw, dict):
        for idx, key in enumerate(PHASE_ORDER):
            if key not in phases_raw:
                continue
            phases.append(_normalize_one(key, phases_raw[key], idx))
    elif isinstance(phases_raw, list):
        for idx, item in enumerate(phases_raw):
            if isinstance(item, dict):
                key = item.get("phase") or item.get("key") or PHASE_ORDER[idx] if idx < len(PHASE_ORDER) else ""
                phases.append(_normalize_one(key, item, idx))

    # Determine current phase: prefer raw current_phase, fallback to first non-done.
    current_phase_key = raw.get("current_phase")
    if isinstance(current_phase_key, str):
        for i, ph in enumerate(phases):
            if ph["phase_key"] == current_phase_key:
                current_idx = i
                break
    if current_idx == -1:
        for i, ph in enumerate(phases):
            if ph["status"] not in ("DONE", "PASS", "SAFE", "SKIPPED", "COMPLETE"):
                current_idx = i
                break

    raw_events = raw.get("event_log") or raw.get("events") or []
    events: List[Dict[str, Any]] = []
    if isinstance(raw_events, list):
        for ev in raw_events[-20:]:
            if isinstance(ev, dict):
                events.append(
                    {
                        "timestamp": ev.get("ts") or ev.get("timestamp") or "",
                        "message": ev.get("msg") or ev.get("message") or "",
                    }
                )

    return {
        "pipeline_id": raw.get("pipeline_id"),
        "description": raw.get("description") or "",
        "phases": phases,
        "current_phase_index": current_idx,
        "events": events,
        "raw": raw,
    }


def load_recent_test_results(limit: int = 10) -> List[Dict[str, Any]]:
    """Read the last `limit` lines of test_results.jsonl."""
    text = _safe_read_text(TEST_RESULTS_FILE)
    if text is None:
        return []
    lines = [ln for ln in text.splitlines() if ln.strip()]
    out: List[Dict[str, Any]] = []
    for line in lines[-limit:]:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _token_budget() -> int:
    raw = os.environ.get("AGENT_OFFICE_TOKEN_BUDGET", "").strip()
    if raw:
        try:
            return max(1, int(raw.replace(",", "")))
        except ValueError:
            logger.warning("Invalid AGENT_OFFICE_TOKEN_BUDGET=%r", raw)
    return DEFAULT_TOKEN_BUDGET


def _usage_total(usage: Dict[str, Any]) -> Dict[str, int]:
    """Normalize token fields from Claude/OpenAI-style usage objects."""
    input_keys = (
        "input_tokens",
        "prompt_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
    )
    output_keys = ("output_tokens", "completion_tokens")
    input_total = 0
    output_total = 0
    for key in input_keys:
        val = usage.get(key)
        if isinstance(val, int):
            input_total += val
    for key in output_keys:
        val = usage.get(key)
        if isinstance(val, int):
            output_total += val
    total = usage.get("total_tokens")
    if isinstance(total, int) and total > input_total + output_total:
        output_total += total - input_total - output_total
    return {"input": input_total, "output": output_total, "total": input_total + output_total}


def _infer_agent_from_record(record: Dict[str, Any], fallback_agent: str) -> str:
    """Best-effort attribution for token records that do not carry agent_id."""
    direct = record.get("agent") or record.get("agent_id")
    data = record.get("data") if isinstance(record.get("data"), dict) else {}
    direct = direct or data.get("agent") or data.get("agent_id") or data.get("subagent")
    if isinstance(direct, str):
        direct = direct.lower()
        for agent_id in AGENT_TOKEN_IDS:
            if agent_id == direct or agent_id.replace("-agent", "") == direct:
                return agent_id

    haystack = json.dumps(record, ensure_ascii=False).lower()
    for agent_id in AGENT_TOKEN_IDS:
        if agent_id in haystack:
            return agent_id
    return fallback_agent


def load_token_summary(fallback_agent: str = "pm-agent") -> Dict[str, Any]:
    """Read token_log.jsonl and aggregate cumulative token usage.

    Returns a dict with:
      {
        "total_input": int,
        "total_output": int,
        "total_tokens": int,
        "entries": int,
        "last_ts": str | None,
      }

    Falls back to zeros if the file does not exist or is malformed.
    """
    budget = _token_budget()
    by_agent: Dict[str, Dict[str, int]] = {
        agent_id: {"input": 0, "output": 0, "total": 0} for agent_id in AGENT_TOKEN_IDS
    }
    text = _safe_read_text(TOKEN_LOG_FILE)
    if text is None:
        return {
            "total_input": 0,
            "total_output": 0,
            "total_tokens": 0,
            "remaining_tokens": budget,
            "budget_tokens": budget,
            "used_percent": 0,
            "remaining_percent": 100,
            "entries": 0,
            "last_ts": None,
            "by_agent": by_agent,
            "source": "logs/token_log.jsonl",
        }

    total_input = 0
    total_output = 0
    entries = 0
    last_ts: Optional[str] = None

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        entries += 1
        last_ts = record.get("ts") or last_ts
        data = record.get("data") or {}
        # Claude Code Stop hook delivers usage under data.usage
        usage = data.get("usage") or record.get("usage") or {}
        if not isinstance(usage, dict):
            usage = {}
        normalized = _usage_total(usage)
        total_input += normalized["input"]
        total_output += normalized["output"]
        agent_id = _infer_agent_from_record(record, fallback_agent)
        bucket = by_agent.setdefault(agent_id, {"input": 0, "output": 0, "total": 0})
        bucket["input"] += normalized["input"]
        bucket["output"] += normalized["output"]
        bucket["total"] += normalized["total"]

    total_tokens = total_input + total_output
    remaining = max(0, budget - total_tokens)
    used_percent = min(100, round((total_tokens / budget) * 100, 1)) if budget else 0
    return {
        "total_input": total_input,
        "total_output": total_output,
        "total_tokens": total_tokens,
        "remaining_tokens": remaining,
        "budget_tokens": budget,
        "used_percent": used_percent,
        "remaining_percent": max(0, round(100 - used_percent, 1)),
        "entries": entries,
        "last_ts": last_ts,
        "by_agent": by_agent,
        "source": "logs/token_log.jsonl",
    }


# ----------------------------------------------------------------------
# Agent roster — color, role, default desk position (isometric grid)
# ----------------------------------------------------------------------
AGENT_ROSTER: List[Dict[str, Any]] = [
    {
        "id": "pm-agent",
        "name": "PM",
        "role": "Planning",
        "color": "#FFC83D",  # golden
        "department": "Meta",
        "desk": {"x": 5, "y": 3},
        "phase_keys": ["pm"],
    },
    {
        "id": "pm-planner-agent",
        "name": "PM Planner",
        "role": "Planning",
        "color": "#F59E0B",  # amber
        "department": "Meta",
        "desk": {"x": 4, "y": 3},
        "phase_keys": ["pm_planner"],
    },
    {
        "id": "pipeline-manager-agent",
        "name": "Pipeline Manager",
        "role": "Pipeline Coordination",
        "color": "#FBBF24",  # yellow
        "department": "Meta",
        "desk": {"x": 6, "y": 3},
        "phase_keys": ["pipeline_manager"],
    },
    {
        "id": "dev-agent",
        "name": "Dev",
        "role": "Implementation",
        "color": "#3B82F6",  # blue
        "department": "Development",
        "desk": {"x": 2, "y": 6},
        "phase_keys": ["dev"],
    },
    {
        "id": "ui-app-agent",
        "name": "UI",
        "role": "GUI Wrapping",
        "color": "#F472B6",  # pink
        "department": "Development",
        "desk": {"x": 3, "y": 7},
        "phase_keys": ["ui"],
    },
    {
        "id": "qa-agent",
        "name": "QA",
        "role": "Verification",
        "color": "#22C55E",  # green
        "department": "QA",
        "desk": {"x": 7, "y": 6},
        "phase_keys": ["qa"],
    },
    {
        "id": "security-agent",
        "name": "Security",
        "role": "Audit",
        "color": "#EF4444",  # red
        "department": "QA",
        "desk": {"x": 8, "y": 7},
        "phase_keys": ["sec"],
    },
    {
        "id": "build-agent",
        "name": "Build",
        "role": "Packaging",
        "color": "#FB923C",  # orange
        "department": "Build",
        "desk": {"x": 2, "y": 9},
        "phase_keys": ["build"],
    },
    {
        "id": "test-harness-agent",
        "name": "Harness",
        "role": "Benchmark",
        "color": "#A855F7",  # purple
        "department": "Build",
        "desk": {"x": 3, "y": 10},
        "phase_keys": ["harness"],
    },
    {
        "id": "prompt-architect-agent",
        "name": "Architect",
        "role": "RCA / Evolution",
        "color": "#38BDF8",  # sky blue
        "department": "Meta",
        "desk": {"x": 7, "y": 9},
        "phase_keys": ["architect"],
    },
    {
        "id": "agent-factory-agent",
        "name": "Factory",
        "role": "New Agent Design",
        "color": "#5EEAD4",  # mint
        "department": "Meta",
        "desk": {"x": 8, "y": 10},
        "phase_keys": [],
    },
    {
        "id": "protocol-evolution-agent",
        "name": "Protocol",
        "role": "Sync",
        "color": "#A3E635",  # lime
        "department": "Meta",
        "desk": {"x": 9, "y": 3},
        "phase_keys": [],
    },
    {
        "id": "power-automate-agent",
        "name": "PowerAuto",
        "role": "Flow Builder",
        "color": "#C084FC",  # violet
        "department": "Development",
        "desk": {"x": 4, "y": 8},
        "phase_keys": [],
    },
]

PHASE_TO_AGENT: Dict[str, str] = {}
for _ag in AGENT_ROSTER:
    for _pk in _ag["phase_keys"]:
        PHASE_TO_AGENT[_pk] = _ag["id"]

ORCHESTRATOR = {
    "id": "mindasol",
    "name": "Mindasol",
    "role": "Orchestrator",
    "color": "#1F2937",
    "desk": {"x": 5, "y": 1},
}

AGENT_EXPLAINERS: Dict[str, Dict[str, str]] = {
    "pm-agent": {
        "summary": "요구사항을 작은 일감으로 쪼개고, 담당 에이전트에게 순서대로 전달합니다.",
        "concern": "분기점에서 사용자 확인이 필요한지, 어떤 에이전트에게 맡겨야 낭비가 적은지 판단합니다.",
    },
    "dev-agent": {
        "summary": "핵심 Python 로직을 구현하고 기존 코드와 충돌하지 않게 연결합니다.",
        "concern": "작은 수정으로 끝낼 수 있는지, 기존 자동화 앱을 건드리지 않아야 하는 경계가 어딘지 봅니다.",
    },
    "ui-app-agent": {
        "summary": "사용자가 볼 화면과 조작 흐름을 다듬습니다.",
        "concern": "정보가 겹치지 않고, 작은 화면에서도 토큰/상태가 한눈에 보이는지 확인합니다.",
    },
    "qa-agent": {
        "summary": "변경된 동작이 요구사항과 맞는지 검증합니다.",
        "concern": "겉보기만 맞고 실제 상태/API가 어긋나는 부분이 없는지 찾습니다.",
    },
    "security-agent": {
        "summary": "경로, 입력값, 제어 API가 위험하게 열려 있지 않은지 점검합니다.",
        "concern": "긴급 정지와 PM 지시 API가 로컬 작업공간 밖을 건드리지 않는지 봅니다.",
    },
    "build-agent": {
        "summary": "실행 방법과 VS Code 연결을 패키징 가능한 형태로 정리합니다.",
        "concern": "Activity Bar 확장이 현재 VS Code에서 실제로 로드되는 방식인지 확인합니다.",
    },
    "test-harness-agent": {
        "summary": "대시보드와 상태 데이터가 정량적으로 잘 표시되는지 점검합니다.",
        "concern": "토큰 잔량 바가 로그 기반 추정치라는 점을 명확히 표현해야 합니다.",
    },
    "prompt-architect-agent": {
        "summary": "작업 로그를 보고 다음 파이프라인 개선점을 정리합니다.",
        "concern": "Claude용 문서를 바꾸지 않고 GPT 해석 레이어만 유지해야 합니다.",
    },
    "agent-factory-agent": {
        "summary": "기존 에이전트로 부족할 때 새 전문 에이전트 설계를 맡습니다.",
        "concern": "새 에이전트가 정말 필요한지, 기존 역할 확장으로 충분한지 따집니다.",
    },
    "protocol-evolution-agent": {
        "summary": "새 규칙이나 에이전트가 생겼을 때 프로토콜 전체와 맞춥니다.",
        "concern": "Claude와 GPT가 같은 문서를 쓰므로 원문 호환성을 깨지 않아야 합니다.",
    },
    "power-automate-agent": {
        "summary": "Power Automate 플로우 정의와 배포 가이드를 만듭니다.",
        "concern": "Python 앱과 Flow 산출물의 책임 범위가 섞이지 않게 합니다.",
    },
}


def _phase_to_agent_status(phase_status: str) -> str:
    s = (phase_status or "").upper()
    if s in ("DONE", "PASS", "SAFE", "COMPLETE"):
        return "idle"
    if s in ("RUNNING", "IN_PROGRESS", "ACTIVE"):
        return "working"
    if s in ("FAIL", "BLOCK", "ERROR"):
        return "alert"
    if s in ("SKIPPED", "SKIP", "N/A", "NA"):
        return "skipped"
    return "pending"


def _is_skipped_evidence(phase: Dict[str, Any]) -> bool:
    """Detect skipped phases via evidence fields (result/score/exe = SKIP/N/A)."""
    if not isinstance(phase, dict):
        return False
    for key in ("result", "verdict", "score", "exe", "risk"):
        v = phase.get(key)
        if isinstance(v, str) and v.strip().upper() in ("SKIP", "SKIPPED", "N/A", "NA"):
            return True
    return False


def build_agent_states(
    state: Dict[str, Any], control: Optional[Dict[str, Any]] = None
) -> List[Dict[str, Any]]:
    """Combine roster + pipeline state into agent overlays.

    Status precedence:
      1. Pipeline terminal_state == COMPLETE -> all agents idle (override).
      2. Phase result/score == SKIP/N/A -> skipped (no working override).
      3. Phase status SKIPPED/SKIP/N/A -> skipped.
      4. Current phase + non-terminal status -> working.
      5. Otherwise map from status.
    """
    control = control or {}
    emergency_stop = bool(control.get("emergency_stop"))
    pm_instruction = str(control.get("pm_instruction") or "").strip()
    phases = state.get("phases", [])
    raw = state.get("raw") or {}
    terminal_state = (raw.get("terminal_state") or "").upper()
    pipeline_complete = terminal_state in ("COMPLETE", "DONE", "FINISHED")

    phase_by_key: Dict[str, Dict[str, Any]] = {}
    for p in phases:
        key = (p.get("phase_key") or "").lower()
        if key:
            phase_by_key[key] = p

    current_idx = state.get("current_phase_index", -1)
    current_phase_key = ""
    if 0 <= current_idx < len(phases) and not pipeline_complete:
        current_phase_key = (phases[current_idx].get("phase_key") or "").lower()
    current_agent_id = PHASE_TO_AGENT.get(current_phase_key, "")

    out: List[Dict[str, Any]] = []
    for agent in AGENT_ROSTER:
        related_phase: Optional[Dict[str, Any]] = None
        for pk in agent["phase_keys"]:
            if pk in phase_by_key:
                related_phase = phase_by_key[pk]
                break

        # Status: working if this agent's phase is current, else map from phase status
        if related_phase:
            phase_status = related_phase.get("status", "PENDING")
            agent_status = _phase_to_agent_status(phase_status)

            # Detect skip via evidence fields (e.g., result="SKIP", score="N/A").
            is_skipped = (
                agent_status == "skipped" or _is_skipped_evidence(related_phase)
            )
            if is_skipped:
                agent_status = "skipped"

            # Working override only when phase is current AND not terminal AND not skipped.
            terminal_phase_states = (
                "DONE", "PASS", "SAFE", "SKIPPED", "SKIP", "N/A",
                "COMPLETE", "FAIL", "BLOCK", "ERROR",
            )
            if (
                current_phase_key
                and current_phase_key in agent["phase_keys"]
                and (phase_status or "").upper() not in terminal_phase_states
                and not is_skipped
                and not pipeline_complete
            ):
                agent_status = "working"

            # Pipeline COMPLETE forces idle for non-skipped agents.
            if pipeline_complete and not is_skipped:
                agent_status = "idle"

            goal = related_phase.get("name") or "—"
            if is_skipped:
                progress_pct = 0
            elif (phase_status or "").upper() in ("DONE", "PASS", "SAFE", "COMPLETE"):
                progress_pct = 100
            elif agent_status == "working":
                progress_pct = 50
            else:
                progress_pct = 0
            issue = ""
            if (phase_status or "").upper() in ("FAIL", "BLOCK", "ERROR"):
                issue = related_phase.get("notes") or f"{phase_status} state"
        else:
            agent_status = "standby"
            goal = "Standby"
            progress_pct = 0
            phase_status = "—"
            issue = ""

        explainer = AGENT_EXPLAINERS.get(agent["id"], {})
        summary = explainer.get("summary", agent["role"])
        concern = explainer.get("concern", "")

        # Position: agents work at their own desks; PM walks over for handoff.
        desk = agent["desk"]
        if emergency_stop:
            if agent["id"] == "pm-agent":
                agent_status = "working"
                phase_status = "AWAITING_USER"
                goal = "긴급 정지 상태: 사용자 추가 명령을 기다리는 중"
                progress_pct = 50
                issue = pm_instruction or "추가 지시가 들어오면 원자 단위로 다시 쪼개 재배정합니다."
                concern = "멈춘 작업과 새 지시가 충돌하지 않도록 PM이 재분해해야 합니다."
                position = {"x": 5, "y": 4}
            else:
                agent_status = "paused"
                phase_status = "PAUSED"
                goal = "긴급 정지로 현재 작업 보류"
                progress_pct = 0
                issue = "PM의 재지시를 기다립니다."
                concern = "재개 시 현재 하던 일과 새 지시의 충돌 여부를 PM에게 보고합니다."
                position = desk
        elif agent["id"] == "pm-agent" and current_agent_id and current_agent_id != "pm-agent":
            target = next((a for a in AGENT_ROSTER if a["id"] == current_agent_id), None)
            if target:
                agent_status = "coordinating"
                goal = f"{target['name']} 에이전트에게 업무 전달 및 진행 확인"
                progress_pct = 65
                position = {
                    "x": max(0, target["desk"]["x"] - 0.45),
                    "y": max(0, target["desk"]["y"] - 0.25),
                }
            else:
                position = desk
        else:
            position = desk

        out.append(
            {
                "id": agent["id"],
                "name": agent["name"],
                "role": agent["role"],
                "color": agent["color"],
                "department": agent["department"],
                "desk": desk,
                "position": position,
                "status": agent_status,
                "phase_status": phase_status,
                "current_task": goal,
                "goal": goal,
                "progress": progress_pct,
                "issue": issue,
                "summary": summary,
                "concern": concern,
                "files": (related_phase or {}).get("files", []),
                # ISO timestamp of when this phase started — used by frontend
                # for elapsed-time based progress smoothing (working: 50→99%).
                "started_at": (related_phase or {}).get("started_at"),
                "tokens_used": 0,
            }
        )
    return out


def build_full_payload() -> Dict[str, Any]:
    state = load_pipeline_state()
    control = _refresh_claude_status(load_control_state())
    phases = state.get("phases") or []
    current_idx = state.get("current_phase_index", -1)
    fallback_agent = "pm-agent"
    if isinstance(current_idx, int) and 0 <= current_idx < len(phases):
        phase_key = str(phases[current_idx].get("phase_key") or "").lower()
        fallback_agent = PHASE_TO_AGENT.get(phase_key, "pm-agent")
    agents = build_agent_states(state, control)
    results = load_recent_test_results(limit=10)
    tokens = load_token_summary(fallback_agent=fallback_agent)
    token_by_agent = tokens.get("by_agent") if isinstance(tokens.get("by_agent"), dict) else {}
    for agent in agents:
        usage = token_by_agent.get(agent["id"], {})
        if isinstance(usage, dict):
            agent["tokens_used"] = int(usage.get("total") or 0)
            agent["tokens_input"] = int(usage.get("input") or 0)
            agent["tokens_output"] = int(usage.get("output") or 0)
    # Expose server timestamp so frontend can compute elapsed time for progress smoothing
    import datetime as _dt
    server_ts = _dt.datetime.now(_dt.timezone.utc).isoformat()
    return {
        "pipeline": {
            "id": state.get("pipeline_id"),
            "description": state.get("description"),
            "phases": state.get("phases"),
            "current_phase_index": state.get("current_phase_index"),
            "events": state.get("events"),
            "external_gates": (state.get("raw") or {}).get("external_gates"),
            "phase_attestations": (state.get("raw") or {}).get("phase_attestations"),
            "deployment": (state.get("raw") or {}).get("deployment"),
        },
        "agents": agents,
        "orchestrator": ORCHESTRATOR,
        "recent_results": results,
        "tokens": tokens,
        "control": control,
        "server_ts": server_ts,
    }


def _run_pipeline_cli(args: List[str]) -> Dict[str, Any]:
    command = [sys.executable, "pipeline.py", *args]
    try:
        proc = subprocess.run(
            command,
            cwd=str(BASE_DIR),
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "command": ["python", "pipeline.py", *args],
            "returncode": 124,
            "stdout": (exc.stdout or "")[-8000:],
            "stderr": f"pipeline command timed out: {exc}",
        }
    except OSError as exc:
        return {
            "command": ["python", "pipeline.py", *args],
            "returncode": 1,
            "stdout": "",
            "stderr": str(exc),
        }
    return {
        "command": ["python", "pipeline.py", *args],
        "returncode": proc.returncode,
        "stdout": proc.stdout[-8000:],
        "stderr": proc.stderr[-8000:],
    }


# ----------------------------------------------------------------------
# WebSocket connection manager
# ----------------------------------------------------------------------
class ConnectionManager:
    def __init__(self) -> None:
        self.connections: Set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self.connections.add(ws)
        logger.info("WS connected. total=%d", len(self.connections))

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self.connections.discard(ws)
        logger.info("WS disconnected. total=%d", len(self.connections))

    async def broadcast(self, payload: Dict[str, Any]) -> None:
        async with self._lock:
            targets = list(self.connections)
        msg = json.dumps(payload, ensure_ascii=False)
        dead: List[WebSocket] = []
        for ws in targets:
            try:
                await ws.send_text(msg)
            except (WebSocketDisconnect, RuntimeError):
                dead.append(ws)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("broadcast error: %s", exc)
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self.connections.discard(ws)


manager = ConnectionManager()


# ----------------------------------------------------------------------
# Background watcher — re-broadcast when pipeline_state.json changes
# ----------------------------------------------------------------------
async def watch_pipeline_state() -> None:
    """Watch pipeline_state.json and broadcast on change.

    Falls back to polling if `watchfiles` is unavailable.
    """
    if awatch is None:
        logger.info("watchfiles unavailable; falling back to 1.5s polling")
        last_state = ""
        last_tokens = ""
        last_control = ""
        while True:
            try:
                state_text = _safe_read_text(PIPELINE_STATE_FILE) or ""
                token_text = _safe_read_text(TOKEN_LOG_FILE) or ""
                control_text = _safe_read_text(CONTROL_FILE) or ""
                if (
                    state_text != last_state
                    or token_text != last_tokens
                    or control_text != last_control
                ):
                    last_state = state_text
                    last_tokens = token_text
                    last_control = control_text
                    await manager.broadcast(build_full_payload())
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("poll error: %s", exc)
            await asyncio.sleep(1.5)
        return

    watch_dir = BASE_DIR
    logger.info("watchfiles watching %s", watch_dir)
    try:
        async for changes in awatch(str(watch_dir), stop_event=None, debounce=400):
            relevant = False
            for _change_type, change_path in changes:
                cp = Path(change_path).name.lower()
                if cp in (
                    "pipeline_state.json",
                    "test_results.jsonl",
                    "token_log.jsonl",
                    "agent_office_control.json",
                ):
                    relevant = True
                    break
            if relevant:
                await manager.broadcast(build_full_payload())
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("watcher crashed: %s; restarting in 2s", exc)
        await asyncio.sleep(2)


# ----------------------------------------------------------------------
# FastAPI app + routes
# ----------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(watch_pipeline_state())
    logger.info("watcher task started")
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        logger.info("watcher task stopped")


app = FastAPI(title="Agent Office Live Dashboard", lifespan=lifespan)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def root() -> FileResponse:
    index = STATIC_DIR / "index.html"
    if not index.exists():
        return JSONResponse(
            {"error": "static/index.html missing"}, status_code=500
        )
    return FileResponse(str(index))


@app.get("/api/state")
async def api_state() -> JSONResponse:
    return JSONResponse(build_full_payload())


@app.post("/api/emergency-stop")
async def api_emergency_stop() -> JSONResponse:
    control = load_control_state()
    control["emergency_stop"] = True
    save_control_state(control, "긴급 정지: 모든 에이전트 작업 보류, PM 추가 지시 대기")
    append_pipeline_event("Agent Office 긴급 정지 요청 - PM 추가 지시 대기")
    payload = build_full_payload()
    await manager.broadcast(payload)
    return JSONResponse(payload)


@app.post("/api/toggle-pause")
async def api_toggle_pause() -> JSONResponse:
    control = load_control_state()
    paused = bool(control.get("emergency_stop"))
    control["emergency_stop"] = not paused
    if paused:
        save_control_state(control, "Pause 해제: PM이 재분해 후 담당 에이전트에 재지시")
        append_pipeline_event("Agent Office Pause 해제 - PM 재분배 모드")
    else:
        save_control_state(control, "Pause: 모든 에이전트 작업 보류, PM 추가 지시 대기")
        append_pipeline_event("Agent Office Pause 요청 - PM 추가 지시 대기")
    payload = build_full_payload()
    await manager.broadcast(payload)
    return JSONResponse(payload)


@app.post("/api/pm-instruction")
async def api_pm_instruction(request: Request) -> JSONResponse:
    body = await request.json()
    message = body.get("message") if isinstance(body, dict) else ""
    if not isinstance(message, str):
        message = ""
    message = message.strip()[:4000]
    control = load_control_state()
    control["emergency_stop"] = True
    control["pm_instruction"] = message
    save_control_state(control, "PM 추가 지시 접수")
    if message:
        append_pipeline_event(f"PM 추가 지시 접수: {message[:160]}")
    payload = build_full_payload()
    await manager.broadcast(payload)
    return JSONResponse(payload)


@app.post("/api/submit-task")
async def api_submit_task(request: Request) -> JSONResponse:
    return await _handle_submit_task(request)


@app.post("/api/pipeline-decision")
async def api_pipeline_decision(request: Request) -> JSONResponse:
    body = await request.json()
    if not isinstance(body, dict):
        body = {}
    result = str(body.get("result") or "").strip().upper()
    evidence = str(body.get("evidence") or "").strip()
    notes = str(body.get("notes") or "").strip()
    if result not in {"ACCEPT", "REJECT"}:
        return JSONResponse({"error": "result must be ACCEPT or REJECT"}, status_code=400)

    args = ["gates", "accept", "--result", result, "--user-confirmed"]
    if evidence:
        args.extend(["--evidence", evidence])
    if notes:
        args.extend(["--notes", notes])
    command_result = _run_pipeline_cli(args)
    if command_result["returncode"] == 0:
        append_pipeline_event(f"Dashboard decision recorded: {result}")
    else:
        append_pipeline_event(f"Dashboard decision failed: {result}")
    payload = build_full_payload()
    payload["decision_result"] = command_result
    await manager.broadcast(payload)
    status_code = 200 if command_result["returncode"] == 0 else 409
    return JSONResponse(payload, status_code=status_code)


@app.post("/api/resume")
async def api_resume() -> JSONResponse:
    control = load_control_state()
    control["emergency_stop"] = False
    save_control_state(control, "긴급 정지 해제: PM이 재분해 후 담당 에이전트에 재지시")
    append_pipeline_event("Agent Office 긴급 정지 해제 - PM 재지시 모드")
    payload = build_full_payload()
    await manager.broadcast(payload)
    return JSONResponse(payload)


@app.get("/api/health")
async def api_health() -> JSONResponse:
    return JSONResponse(
        {
            "status": "ok",
            "base_dir": str(BASE_DIR),
            "pipeline_state_exists": PIPELINE_STATE_FILE.exists(),
            "test_results_exists": TEST_RESULTS_FILE.exists(),
            "control_exists": CONTROL_FILE.exists(),
            "ws_clients": len(manager.connections),
        }
    )


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await manager.connect(ws)
    try:
        # Send initial snapshot
        await ws.send_text(json.dumps(build_full_payload(), ensure_ascii=False))
        while True:
            # We don't expect client messages, but keep loop for keepalive
            msg = await ws.receive_text()
            if msg == "ping":
                await ws.send_text(json.dumps({"type": "pong"}))
            # ignore other inbound messages
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("ws error: %s", exc)
    finally:
        await manager.disconnect(ws)


# ----------------------------------------------------------------------
# Main entry
# ----------------------------------------------------------------------
def main(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    import uvicorn

    # Validate path safety on entry (FS.traversal wiring)
    _ = _safe_path(BASE_DIR)
    _ = _safe_path(WEBAPP_DIR)
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Agent Office Live Dashboard server")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()
    main(host=args.host, port=args.port)
