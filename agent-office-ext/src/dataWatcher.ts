/**
 * dataWatcher.ts
 * FileSystemWatcher for the four pipeline data files.
 * Parses changes and updates the shared AgentState array.
 */

import * as vscode from 'vscode';
import * as fs from 'fs';
import * as path from 'path';
import { AgentState, AgentStatus } from './agentState';
import { parseTokenLog, applyTokenCounts } from './tokenBar';

/** Callback type invoked whenever any watched file changes. */
export type OnStatesUpdated = (states: AgentState[]) => void;

/** Shape expected in pipeline_state.json (partial — only used fields). */
interface PipelineState {
  current_phase?: string;
  phases?: Array<{
    name?: string;
    status?: string;
    agent?: string;
  }>;
  status?: string;
}

/** Shape expected in agent_status.json. */
type AgentStatusMap = Record<string, string>;

/**
 * Resolve workspace root: first folder or home directory.
 */
function resolveRoot(): string {
  const folders = vscode.workspace.workspaceFolders;
  if (folders && folders.length > 0) {
    return folders[0].uri.fsPath;
  }
  return require('os').homedir();
}

/**
 * Safely read a text file with UTF-8 → CP949 → Latin-1 fallback.
 * Returns null on any read error or if the file does not exist.
 *
 * @param filePath - Absolute path to the file.
 * @returns File contents as string, or null.
 */
function safeReadFile(filePath: string): string | null {
  // Path traversal guard: reject paths with ".." segments.
  const normalized = path.normalize(filePath);
  if (normalized.includes('..')) {
    return null;
  }
  if (!fs.existsSync(filePath)) {
    return null;
  }
  const encodings: BufferEncoding[] = ['utf-8', 'latin1'];
  for (const enc of encodings) {
    try {
      return fs.readFileSync(filePath, { encoding: enc });
    } catch {
      continue;
    }
  }
  return null;
}

/**
 * Parse pipeline_state.json and map phase statuses to agent statuses.
 * Returns a map of agentId → AgentStatus or empty map on parse failure.
 *
 * @param raw - Raw JSON string.
 * @returns Map from agent id to status.
 */
function parsePipelineState(raw: string): Map<string, AgentStatus> {
  const result = new Map<string, AgentStatus>();
  try {
    const data = JSON.parse(raw) as PipelineState;

    // Map current_phase to 'working' for the active agent.
    if (typeof data.current_phase === 'string' && data.current_phase.length > 0) {
      const phaseUpper = data.current_phase.toUpperCase();
      if (phaseUpper.includes('PM')) { result.set('pm', 'working'); }
      else if (phaseUpper.includes('DEV')) { result.set('dev', 'working'); }
      else if (phaseUpper.includes('QA')) { result.set('qa', 'working'); }
      else if (phaseUpper.includes('SEC')) { result.set('security', 'working'); }
      else if (phaseUpper.includes('BUILD')) { result.set('build', 'working'); }
      else if (phaseUpper.includes('HARNESS')) { result.set('harness', 'working'); }
      else if (phaseUpper.includes('ARCHITECT')) { result.set('architect', 'working'); }
      else if (phaseUpper.includes('UI')) { result.set('ui', 'working'); }
    }

    // Parse phases array for done/failed states.
    if (Array.isArray(data.phases)) {
      for (const phase of data.phases) {
        if (!phase || typeof phase.name !== 'string') { continue; }
        const agentId = phaseNameToAgentId(phase.name);
        if (!agentId) { continue; }
        const phaseStatus = typeof phase.status === 'string' ? phase.status.toUpperCase() : '';
        if (phaseStatus === 'DONE' || phaseStatus === 'COMPLETE' || phaseStatus === 'PASS') {
          // Don't override 'working' with 'done' for the currently active phase.
          if (!result.has(agentId)) {
            result.set(agentId, 'done');
          }
        } else if (phaseStatus === 'FAIL' || phaseStatus === 'FAILED' || phaseStatus === 'BLOCK') {
          result.set(agentId, 'error');
        }
      }
    }
  } catch {
    // Return empty map — callers keep agents at 'idle'.
  }
  return result;
}

/**
 * Map a phase name string to an agent id.
 *
 * @param name - Phase name from pipeline_state.json.
 * @returns Agent id string or null.
 */
function phaseNameToAgentId(name: string): string | null {
  const upper = name.toUpperCase();
  if (upper.includes('PM') || upper.includes('PLANNING')) { return 'pm'; }
  if (upper.includes('DEV') || upper.includes('IMPL')) { return 'dev'; }
  if (upper.includes('QA') || upper.includes('VERIF')) { return 'qa'; }
  if (upper.includes('SEC') || upper.includes('AUDIT')) { return 'security'; }
  if (upper.includes('BUILD') || upper.includes('PACK')) { return 'build'; }
  if (upper.includes('HARNESS') || upper.includes('BENCH')) { return 'harness'; }
  if (upper.includes('ARCHITECT') || upper.includes('RCA')) { return 'architect'; }
  if (upper.includes('UI') || upper.includes('INTERFACE')) { return 'ui'; }
  return null;
}

/**
 * Parse agent_status.json and return a map of agentId → AgentStatus.
 * Returns empty map on parse failure.
 *
 * @param raw - Raw JSON string.
 * @returns Map from agent id to status.
 */
function parseAgentStatus(raw: string): Map<string, AgentStatus> {
  const result = new Map<string, AgentStatus>();
  const validStatuses: Set<AgentStatus> = new Set([
    'idle', 'working', 'done', 'error', 'stopped',
  ]);
  try {
    const data = JSON.parse(raw) as AgentStatusMap;
    if (data && typeof data === 'object') {
      for (const [agentId, statusRaw] of Object.entries(data)) {
        if (typeof statusRaw === 'string') {
          const status = statusRaw.toLowerCase() as AgentStatus;
          if (validStatuses.has(status)) {
            result.set(agentId.toLowerCase(), status);
          }
        }
      }
    }
  } catch {
    // Return empty map.
  }
  return result;
}

/**
 * DataWatcher — manages FileSystemWatcher instances for all four pipeline files
 * and merges updates into the shared states array.
 */
export class DataWatcher {
  private readonly _states: AgentState[];
  private readonly _onUpdate: OnStatesUpdated;
  private readonly _disposables: vscode.Disposable[] = [];
  private readonly _root: string;

  /**
   * @param states - Mutable agent state array to update in place.
   * @param onUpdate - Callback invoked after any state change.
   */
  constructor(states: AgentState[], onUpdate: OnStatesUpdated) {
    this._states = states;
    this._onUpdate = onUpdate;
    this._root = resolveRoot();
  }

  /**
   * Start watching all four files.
   * Existing content is read immediately on start.
   */
  public start(): void {
    this._watchFile('token_log.jsonl', () => this._reloadTokenLog());
    this._watchFile('pipeline_state.json', () => this._reloadPipelineState());
    this._watchFile('agent_status.json', () => this._reloadAgentStatus());
    this._watchFile('stop_signal.json', () => this._reloadStopSignal());

    // Initial read.
    this._reloadTokenLog();
    this._reloadPipelineState();
    this._reloadAgentStatus();
    this._reloadStopSignal();
  }

  /**
   * Dispose all watchers.
   */
  public dispose(): void {
    for (const d of this._disposables) {
      d.dispose();
    }
    this._disposables.length = 0;
  }

  // ---- Private helpers ----

  /**
   * Register a FileSystemWatcher for a single filename in the workspace root.
   *
   * @param filename - Base filename to watch.
   * @param handler - Callback on create/change/delete.
   */
  private _watchFile(filename: string, handler: () => void): void {
    const pattern = new vscode.RelativePattern(this._root, filename);
    const watcher = vscode.workspace.createFileSystemWatcher(pattern);
    watcher.onDidCreate(handler, this, this._disposables);
    watcher.onDidChange(handler, this, this._disposables);
    watcher.onDidDelete(handler, this, this._disposables);
    this._disposables.push(watcher);
  }

  /** Reload and apply token_log.jsonl. */
  private _reloadTokenLog(): void {
    const filePath = path.join(this._root, 'token_log.jsonl');
    const raw = safeReadFile(filePath);
    if (raw === null) {
      // File absent — reset all token counts to 0.
      for (const s of this._states) { s.tokenCount = 0; }
    } else {
      const lines = raw.split('\n');
      const tokenMap = parseTokenLog(lines);
      applyTokenCounts(this._states, tokenMap);
    }
    this._onUpdate([...this._states]);
  }

  /** Reload and apply pipeline_state.json. */
  private _reloadPipelineState(): void {
    const filePath = path.join(this._root, 'pipeline_state.json');
    const raw = safeReadFile(filePath);
    if (raw === null) {
      // File absent — keep current statuses.
      this._onUpdate([...this._states]);
      return;
    }
    const statusMap = parsePipelineState(raw);
    for (const state of this._states) {
      const mapped = statusMap.get(state.id);
      if (mapped !== undefined) {
        state.status = mapped;
      }
    }
    this._onUpdate([...this._states]);
  }

  /** Reload and apply agent_status.json. */
  private _reloadAgentStatus(): void {
    const filePath = path.join(this._root, 'agent_status.json');
    const raw = safeReadFile(filePath);
    if (raw === null) {
      this._onUpdate([...this._states]);
      return;
    }
    const statusMap = parseAgentStatus(raw);
    for (const state of this._states) {
      const mapped = statusMap.get(state.id);
      if (mapped !== undefined) {
        state.status = mapped;
      }
    }
    this._onUpdate([...this._states]);
  }

  /** Reload stop_signal.json — if present, set all agents to 'stopped'. */
  private _reloadStopSignal(): void {
    const filePath = path.join(this._root, 'stop_signal.json');
    const exists = fs.existsSync(filePath);
    if (exists) {
      for (const state of this._states) {
        state.status = 'stopped';
      }
    }
    this._onUpdate([...this._states]);
  }
}
