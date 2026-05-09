/**
 * stopSignalHandler.ts
 * Writes and clears stop_signal.json using an atomic tempfile → rename pattern.
 * This mirrors the Python FS safe_write pattern: write to .tmp then replace.
 */

import * as fs from 'fs';
import * as path from 'path';
import * as vscode from 'vscode';

/** Contents written to stop_signal.json. */
interface StopSignalPayload {
  stop: boolean;
  timestamp: string;
  pm_command: string;
}

/**
 * Resolve the workspace root for the stop_signal.json location.
 * Falls back to the first workspace folder or the home directory.
 *
 * @returns Absolute path string.
 */
function resolveWorkspaceRoot(): string {
  const folders = vscode.workspace.workspaceFolders;
  if (folders && folders.length > 0) {
    return folders[0].uri.fsPath;
  }
  return require('os').homedir();
}

/**
 * Validate that a file path does not escape the allowed root.
 * Blocks path traversal via ".." segments.
 *
 * @param filePath - Resolved absolute path to validate.
 * @param allowedRoot - Resolved absolute root path.
 * @returns True if the path is safe.
 */
function isPathSafe(filePath: string, allowedRoot: string): boolean {
  const resolvedFile = path.resolve(filePath);
  const resolvedRoot = path.resolve(allowedRoot);
  return resolvedFile.startsWith(resolvedRoot + path.sep) ||
         resolvedFile === resolvedRoot;
}

/**
 * Atomic write: write content to a .tmp file then rename to the final path.
 * Throws on I/O failure.
 *
 * @param finalPath - Destination file path.
 * @param content - String content to write (UTF-8).
 */
function atomicWriteSync(finalPath: string, content: string): void {
  const tmpPath = finalPath + '.tmp';
  fs.writeFileSync(tmpPath, content, { encoding: 'utf-8' });
  fs.renameSync(tmpPath, finalPath);
}

/**
 * Write stop_signal.json to the workspace root.
 * Provides immediate UI feedback by returning the payload that was written.
 *
 * @param pmCommand - Optional PM directive message to embed in the signal.
 * @returns The payload written, or null if the write failed.
 */
export function writeStopSignal(pmCommand?: string): StopSignalPayload | null {
  const workspaceRoot = resolveWorkspaceRoot();
  const signalPath = path.join(workspaceRoot, 'stop_signal.json');

  // Path traversal guard.
  if (!isPathSafe(signalPath, workspaceRoot)) {
    vscode.window.showErrorMessage('[AgentOffice] Stop signal path validation failed.');
    return null;
  }

  const payload: StopSignalPayload = {
    stop: true,
    timestamp: new Date().toISOString(),
    pm_command: typeof pmCommand === 'string' ? pmCommand.trim() : '',
  };

  try {
    atomicWriteSync(signalPath, JSON.stringify(payload, null, 2));
    return payload;
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    vscode.window.showErrorMessage(`[AgentOffice] Failed to write stop signal: ${message}`);
    return null;
  }
}

/**
 * Remove stop_signal.json from the workspace root if it exists.
 *
 * @returns True if the file was removed (or did not exist), false on error.
 */
export function clearStopSignal(): boolean {
  const workspaceRoot = resolveWorkspaceRoot();
  const signalPath = path.join(workspaceRoot, 'stop_signal.json');

  if (!isPathSafe(signalPath, workspaceRoot)) {
    vscode.window.showErrorMessage('[AgentOffice] Clear stop signal path validation failed.');
    return false;
  }

  try {
    if (fs.existsSync(signalPath)) {
      fs.unlinkSync(signalPath);
    }
    return true;
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    vscode.window.showErrorMessage(`[AgentOffice] Failed to clear stop signal: ${message}`);
    return false;
  }
}

/**
 * Check whether a stop_signal.json currently exists in the workspace root.
 *
 * @returns True if the file exists.
 */
export function isStopSignalActive(): boolean {
  const workspaceRoot = resolveWorkspaceRoot();
  const signalPath = path.join(workspaceRoot, 'stop_signal.json');
  try {
    return fs.existsSync(signalPath);
  } catch {
    return false;
  }
}
