/**
 * canvasRenderer.ts
 * Canvas 2D rendering functions for the Agent Office WebView.
 * Runs inside the WebView browser context — no Node.js or vscode APIs here.
 */

import { AgentState, CANVAS_WIDTH, TOKEN_BAR_HEIGHT, HAMSTER_SCALE } from '../agentState';
import { SpriteMap, getFrames } from './hamsterSprites';
import { getTotalTokens, getBarWidth } from '../tokenBar';

/** Color palette for index-based pixel art rendering. */
const PALETTE: Record<number, string> = {
  0: 'transparent',
  1: '', // filled per-agent from state.color
  2: '#AAAAAA', // default accessory — overridden per agent
  3: '#F5DEB3', // face / wheat
  4: '#1A1A1A', // dark outline
  5: '#E0E0E0', // secondary (paper, notepad, beaker)
};

/** Agent-specific accessory colors (index 2). */
const ACCESSORY_COLORS: Record<string, string> = {
  pm:       '#FF8C00',
  dev:      '#888888',
  qa:       '#228B22',
  security: '#DC143C',
  build:    '#DAA520',
  harness:  '#FFFFFF',
  architect:'#008B8B',
  ui:       '#FF69B4',
};

/** Status border/glow colors. */
const STATUS_COLORS: Record<string, string> = {
  idle:    '#555555',
  working: '#FFD700',
  done:    '#32CD32',
  error:   '#FF4444',
  stopped: '#FF0000',
};

/** Room background color (dark office). */
const ROOM_BG    = '#2A2A2A';
const ROOM_FLOOR = '#1E1E1E';
const OFFICE_BG  = '#1A1A1A';

/**
 * Draw the full office background grid.
 *
 * @param ctx - Canvas 2D rendering context.
 * @param width - Canvas width in pixels.
 * @param height - Canvas height in pixels.
 */
export function drawOfficeBackground(
  ctx: CanvasRenderingContext2D,
  width: number,
  height: number
): void {
  // Background fill.
  ctx.fillStyle = OFFICE_BG;
  ctx.fillRect(0, 0, width, height);

  // Subtle grid lines.
  ctx.strokeStyle = '#2C2C2C';
  ctx.lineWidth = 1;
  for (let x = 0; x < width; x += 20) {
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, height);
    ctx.stroke();
  }
  for (let y = 0; y < height - TOKEN_BAR_HEIGHT; y += 20) {
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(width, y);
    ctx.stroke();
  }
}

/**
 * Draw a single agent room (box + label).
 *
 * @param ctx - Canvas 2D context.
 * @param agent - Agent state to render.
 */
export function drawRoom(
  ctx: CanvasRenderingContext2D,
  agent: AgentState
): void {
  const { roomX, roomY, roomW, roomH, status, color, name } = agent;

  // Room background.
  ctx.fillStyle = ROOM_BG;
  ctx.fillRect(roomX, roomY, roomW, roomH);

  // Floor strip at bottom of room.
  ctx.fillStyle = ROOM_FLOOR;
  ctx.fillRect(roomX, roomY + roomH - 12, roomW, 12);

  // Status border.
  const borderColor = STATUS_COLORS[status] ?? '#555555';
  const borderWidth = status === 'working' ? 3 : status === 'stopped' ? 4 : 2;
  ctx.strokeStyle = borderColor;
  ctx.lineWidth = borderWidth;
  ctx.strokeRect(roomX, roomY, roomW, roomH);

  // Agent name label at top of room.
  ctx.fillStyle = color;
  ctx.font = 'bold 9px monospace';
  ctx.textAlign = 'center';
  ctx.fillText(name, roomX + roomW / 2, roomY + 11);
}

/**
 * Draw a hamster sprite at the center of the agent's room.
 * Upscales each pixel by HAMSTER_SCALE (4px).
 *
 * @param ctx - Canvas 2D context.
 * @param agent - Agent state (used for position, status, id, color).
 * @param sprites - Sprite map from hamsterSprites.ts.
 */
export function drawHamster(
  ctx: CanvasRenderingContext2D,
  agent: AgentState,
  sprites: SpriteMap
): void {
  const frames = getFrames(agent.id, agent.status);
  if (!frames || frames.length === 0) { return; }

  const frameIndex = Math.min(agent.currentFrame, frames.length - 1);
  const frame = frames[frameIndex];
  if (!frame || frame.length === 0) { return; }

  const spriteW = frame[0].length * HAMSTER_SCALE;
  const spriteH = frame.length * HAMSTER_SCALE;

  // Center sprite in room, above the floor strip.
  const startX = Math.floor(agent.roomX + (agent.roomW - spriteW) / 2);
  const startY = Math.floor(agent.roomY + (agent.roomH - spriteH) / 2) + 4;

  // Build per-pixel palette for this agent.
  const localPalette: Record<number, string> = {
    ...PALETTE,
    1: agent.color,
    2: ACCESSORY_COLORS[agent.id] ?? '#AAAAAA',
  };

  for (let row = 0; row < frame.length; row++) {
    const pixelRow = frame[row];
    if (!pixelRow) { continue; }
    for (let col = 0; col < pixelRow.length; col++) {
      const colorIndex = pixelRow[col];
      if (colorIndex === 0) { continue; } // transparent

      const fillColor = localPalette[colorIndex];
      if (!fillColor || fillColor === 'transparent') { continue; }

      ctx.fillStyle = fillColor;
      ctx.fillRect(
        startX + col * HAMSTER_SCALE,
        startY + row * HAMSTER_SCALE,
        HAMSTER_SCALE,
        HAMSTER_SCALE
      );
    }
  }
}

/**
 * Draw the status badge icon in the top-right corner of the agent's room.
 *
 * @param ctx - Canvas 2D context.
 * @param agent - Agent state.
 */
export function drawStatusBadge(
  ctx: CanvasRenderingContext2D,
  agent: AgentState
): void {
  const bx = agent.roomX + agent.roomW - 14;
  const by = agent.roomY + 4;
  const r = 5;

  switch (agent.status) {
    case 'working': {
      ctx.fillStyle = '#FFD700';
      ctx.beginPath();
      ctx.arc(bx, by, r, 0, Math.PI * 2);
      ctx.fill();
      // Pulsing dot inside.
      ctx.fillStyle = '#FFF';
      ctx.beginPath();
      ctx.arc(bx, by, 2, 0, Math.PI * 2);
      ctx.fill();
      break;
    }
    case 'done': {
      ctx.fillStyle = '#32CD32';
      ctx.beginPath();
      ctx.arc(bx, by, r, 0, Math.PI * 2);
      ctx.fill();
      // Checkmark.
      ctx.strokeStyle = '#FFF';
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(bx - 3, by);
      ctx.lineTo(bx - 1, by + 2);
      ctx.lineTo(bx + 3, by - 2);
      ctx.stroke();
      break;
    }
    case 'error': {
      ctx.fillStyle = '#FF4444';
      ctx.beginPath();
      ctx.arc(bx, by, r, 0, Math.PI * 2);
      ctx.fill();
      // X mark.
      ctx.strokeStyle = '#FFF';
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(bx - 3, by - 3); ctx.lineTo(bx + 3, by + 3);
      ctx.moveTo(bx + 3, by - 3); ctx.lineTo(bx - 3, by + 3);
      ctx.stroke();
      break;
    }
    case 'stopped': {
      ctx.fillStyle = '#FF0000';
      ctx.beginPath();
      ctx.arc(bx, by, r, 0, Math.PI * 2);
      ctx.fill();
      // Stop square.
      ctx.fillStyle = '#FFF';
      ctx.fillRect(bx - 3, by - 3, 6, 6);
      break;
    }
    default:
      // idle — grey dot.
      ctx.fillStyle = '#555555';
      ctx.beginPath();
      ctx.arc(bx, by, r, 0, Math.PI * 2);
      ctx.fill();
  }
}

/**
 * Draw the token bar at the bottom of the canvas.
 * Each agent gets a proportional color segment.
 *
 * @param ctx - Canvas 2D context.
 * @param states - All agent states.
 * @param canvasWidth - Total canvas width.
 * @param barY - Top Y coordinate of the token bar.
 * @param barH - Height of the token bar.
 */
export function drawTokenBar(
  ctx: CanvasRenderingContext2D,
  states: AgentState[],
  canvasWidth: number,
  barY: number,
  barH: number
): void {
  // Background.
  ctx.fillStyle = '#111111';
  ctx.fillRect(0, barY, canvasWidth, barH);

  // Header label.
  ctx.fillStyle = '#888888';
  ctx.font = '8px monospace';
  ctx.textAlign = 'left';
  ctx.fillText('TOKEN USAGE', 4, barY + 10);

  const total = getTotalTokens(states);
  const barAreaY = barY + 14;
  const barAreaH = barH - 18;
  const barAreaW = canvasWidth - 8;

  if (total <= 0) {
    ctx.fillStyle = '#333333';
    ctx.fillRect(4, barAreaY, barAreaW, barAreaH);
    ctx.fillStyle = '#555555';
    ctx.font = '7px monospace';
    ctx.textAlign = 'center';
    ctx.fillText('no tokens yet', canvasWidth / 2, barAreaY + barAreaH / 2 + 3);
    return;
  }

  let offsetX = 4;
  for (const agent of states) {
    if (agent.tokenCount <= 0) { continue; }
    const segW = getBarWidth(agent.tokenCount, total, barAreaW);
    if (segW <= 0) { continue; }
    ctx.fillStyle = agent.color;
    ctx.fillRect(offsetX, barAreaY, segW, barAreaH);
    // Token count label if segment is wide enough.
    if (segW > 20) {
      ctx.fillStyle = '#FFF';
      ctx.font = '6px monospace';
      ctx.textAlign = 'left';
      const label = agent.tokenCount >= 1000
        ? `${Math.floor(agent.tokenCount / 1000)}k`
        : String(agent.tokenCount);
      ctx.fillText(label, offsetX + 2, barAreaY + barAreaH / 2 + 2);
    }
    offsetX += segW;
  }

  // Outline.
  ctx.strokeStyle = '#444444';
  ctx.lineWidth = 1;
  ctx.strokeRect(4, barAreaY, barAreaW, barAreaH);
}

/**
 * Render the full office scene: background, all rooms, hamsters, badges, token bar.
 *
 * @param ctx - Canvas 2D context.
 * @param states - All agent states.
 * @param sprites - Sprite map.
 * @param canvasWidth - Canvas width.
 * @param canvasHeight - Canvas height.
 * @param stopActive - If true, draws a red STOP overlay.
 */
export function renderFrame(
  ctx: CanvasRenderingContext2D,
  states: AgentState[],
  sprites: SpriteMap,
  canvasWidth: number,
  canvasHeight: number,
  stopActive: boolean
): void {
  const officeH = canvasHeight - TOKEN_BAR_HEIGHT;

  drawOfficeBackground(ctx, canvasWidth, officeH);

  for (const agent of states) {
    drawRoom(ctx, agent);
    drawHamster(ctx, agent, sprites);
    drawStatusBadge(ctx, agent);
  }

  drawTokenBar(ctx, states, canvasWidth, officeH, TOKEN_BAR_HEIGHT);

  if (stopActive) {
    // Red translucent overlay with STOPPED text.
    ctx.fillStyle = 'rgba(255, 0, 0, 0.18)';
    ctx.fillRect(0, 0, canvasWidth, officeH);
    ctx.fillStyle = '#FF4444';
    ctx.font = 'bold 20px monospace';
    ctx.textAlign = 'center';
    ctx.fillText('EMERGENCY STOP ACTIVE', canvasWidth / 2, officeH / 2);
  }
}
