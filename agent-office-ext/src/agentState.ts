/**
 * agentState.ts
 * Agent state type definitions and layout constants.
 * All 8 agents are defined here with their canvas room positions and color coding.
 */

/** Agent animation status driving sprite frame selection. */
export type AgentStatus = 'idle' | 'working' | 'done' | 'error' | 'stopped';

/** Full runtime state for a single agent. */
export interface AgentState {
  /** Unique agent identifier (e.g. 'pm', 'dev'). */
  id: string;
  /** Display name shown in the room label. */
  name: string;
  /** Hex color string for room border and token bar segment. */
  color: string;
  /** Canvas room top-left X coordinate (pixels). */
  roomX: number;
  /** Canvas room top-left Y coordinate (pixels). */
  roomY: number;
  /** Room width in pixels. */
  roomW: number;
  /** Room height in pixels. */
  roomH: number;
  /** Current operational status. */
  status: AgentStatus;
  /** Cumulative token count parsed from token_log.jsonl. */
  tokenCount: number;
  /** Current sprite frame index (0-based). */
  currentFrame: number;
  /** Elapsed ms since last frame switch. */
  frameTimer: number;
}

/** Canvas layout constants. */
export const CANVAS_WIDTH = 600;
export const CANVAS_HEIGHT = 500;
export const TOKEN_BAR_HEIGHT = 40;
export const ROOM_W = 160;
export const ROOM_H = 120;
export const HAMSTER_SCALE = 4; // pixels per pixel-art dot

/**
 * Initial layout for all 8 agents.
 * roomX/roomY define the top-left corner of each room rectangle.
 * Values are computed to tile 3 columns x 3 rows (centre, tl, tr, ml, mr, bl, br, bc).
 */
export const AGENT_LAYOUT: AgentState[] = [
  // Row 0 — top
  {
    id: 'dev',
    name: 'Dev',
    color: '#4169E1',
    roomX: 10,
    roomY: 10,
    roomW: ROOM_W,
    roomH: ROOM_H,
    status: 'idle',
    tokenCount: 0,
    currentFrame: 0,
    frameTimer: 0,
  },
  {
    id: 'pm',
    name: 'PM',
    color: '#FF8C00',
    roomX: 220,
    roomY: 10,
    roomW: ROOM_W,
    roomH: ROOM_H,
    status: 'idle',
    tokenCount: 0,
    currentFrame: 0,
    frameTimer: 0,
  },
  {
    id: 'qa',
    name: 'QA',
    color: '#228B22',
    roomX: 430,
    roomY: 10,
    roomW: ROOM_W,
    roomH: ROOM_H,
    status: 'idle',
    tokenCount: 0,
    currentFrame: 0,
    frameTimer: 0,
  },
  // Row 1 — middle
  {
    id: 'security',
    name: 'Security',
    color: '#DC143C',
    roomX: 10,
    roomY: 145,
    roomW: ROOM_W,
    roomH: ROOM_H,
    status: 'idle',
    tokenCount: 0,
    currentFrame: 0,
    frameTimer: 0,
  },
  {
    id: 'build',
    name: 'Build',
    color: '#DAA520',
    roomX: 430,
    roomY: 145,
    roomW: ROOM_W,
    roomH: ROOM_H,
    status: 'idle',
    tokenCount: 0,
    currentFrame: 0,
    frameTimer: 0,
  },
  // Row 2 — bottom
  {
    id: 'harness',
    name: 'Harness',
    color: '#8B008B',
    roomX: 10,
    roomY: 280,
    roomW: ROOM_W,
    roomH: ROOM_H,
    status: 'idle',
    tokenCount: 0,
    currentFrame: 0,
    frameTimer: 0,
  },
  {
    id: 'ui',
    name: 'UI/App',
    color: '#FF69B4',
    roomX: 220,
    roomY: 280,
    roomW: ROOM_W,
    roomH: ROOM_H,
    status: 'idle',
    tokenCount: 0,
    currentFrame: 0,
    frameTimer: 0,
  },
  {
    id: 'architect',
    name: 'Architect',
    color: '#008B8B',
    roomX: 430,
    roomY: 280,
    roomW: ROOM_W,
    roomH: ROOM_H,
    status: 'idle',
    tokenCount: 0,
    currentFrame: 0,
    frameTimer: 0,
  },
];

/**
 * Deep-clone the initial layout so callers can mutate without affecting the template.
 */
export function createInitialStates(): AgentState[] {
  return AGENT_LAYOUT.map((a) => ({ ...a }));
}
