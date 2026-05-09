/**
 * tokenBar.ts
 * Parses token_log.jsonl and computes per-agent token counts and bar widths.
 */

import { AgentState } from './agentState';

/** Shape of a single line in token_log.jsonl. */
interface TokenLogEntry {
  agent: string;
  tokens: number;
  timestamp?: string;
}

/**
 * Parse raw JSONL text lines into a map of agentId → cumulative token count.
 * Malformed lines are silently skipped.
 *
 * @param lines - Array of raw text lines from token_log.jsonl.
 * @returns Map from agent id to token count.
 */
export function parseTokenLog(lines: string[]): Map<string, number> {
  const result = new Map<string, number>();

  if (!lines || lines.length === 0) {
    return result;
  }

  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) {
      continue;
    }
    try {
      const entry = JSON.parse(trimmed) as Partial<TokenLogEntry>;
      if (
        typeof entry.agent === 'string' &&
        entry.agent.length > 0 &&
        typeof entry.tokens === 'number' &&
        isFinite(entry.tokens) &&
        entry.tokens >= 0
      ) {
        const agentId = entry.agent.toLowerCase().trim();
        const existing = result.get(agentId) ?? 0;
        result.set(agentId, existing + entry.tokens);
      }
    } catch {
      // Malformed JSON line — skip silently.
    }
  }

  return result;
}

/**
 * Apply parsed token counts to the agent state array (mutates in place).
 *
 * @param states - Mutable array of agent states.
 * @param tokenMap - Map from parseTokenLog().
 */
export function applyTokenCounts(
  states: AgentState[],
  tokenMap: Map<string, number>
): void {
  if (!states || states.length === 0) {
    return;
  }
  for (const state of states) {
    const count = tokenMap.get(state.id);
    state.tokenCount = typeof count === 'number' ? count : 0;
  }
}

/**
 * Compute the total token count across all agents.
 *
 * @param states - Agent state array.
 * @returns Sum of all tokenCount values.
 */
export function getTotalTokens(states: AgentState[]): number {
  if (!states || states.length === 0) {
    return 0;
  }
  return states.reduce((sum, s) => sum + (s.tokenCount ?? 0), 0);
}

/**
 * Compute the proportional bar width for a single agent.
 * Returns 0 if total is 0 to avoid division by zero.
 *
 * @param tokenCount - This agent's token count.
 * @param totalTokens - Sum of all agents' token counts.
 * @param maxWidth - Maximum pixel width of the token bar.
 * @returns Pixel width for this agent's segment.
 */
export function getBarWidth(
  tokenCount: number,
  totalTokens: number,
  maxWidth: number
): number {
  if (totalTokens <= 0 || maxWidth <= 0 || tokenCount <= 0) {
    return 0;
  }
  return Math.floor((tokenCount / totalTokens) * maxWidth);
}
