/**
 * animationLoop.ts
 * requestAnimationFrame-based animation loop for the Agent Office canvas.
 * Runs inside the WebView browser context.
 */

import { AgentState, AgentStatus } from '../agentState';
import { SpriteMap } from './hamsterSprites';
import { renderFrame } from './canvasRenderer';

/** Frame duration (ms) per status. */
const FRAME_DURATION: Record<AgentStatus, number> = {
  idle:    600,
  working: 200,
  done:    400,
  error:   400,
  stopped: 400,
};

/**
 * AnimationLoop manages the requestAnimationFrame loop, frame timer logic,
 * and triggers a full canvas redraw every ~16ms.
 */
export class AnimationLoop {
  private readonly _canvas: HTMLCanvasElement;
  private readonly _ctx: CanvasRenderingContext2D;
  private readonly _sprites: SpriteMap;
  private _states: AgentState[];
  private _rafId: number | null = null;
  private _lastTimestamp: number = 0;
  private _stopActive: boolean = false;
  private _running: boolean = false;

  /**
   * @param canvas - The target canvas element.
   * @param sprites - Pre-loaded sprite map.
   * @param initialStates - Initial agent states (will be replaced via setStates).
   */
  constructor(
    canvas: HTMLCanvasElement,
    sprites: SpriteMap,
    initialStates: AgentState[]
  ) {
    const ctx = canvas.getContext('2d');
    if (!ctx) {
      throw new Error('[AnimationLoop] Canvas 2D context unavailable.');
    }
    this._canvas = canvas;
    this._ctx = ctx;
    this._sprites = sprites;
    this._states = initialStates.map((s) => ({ ...s }));
  }

  /**
   * Start the animation loop.
   * Safe to call multiple times — will no-op if already running.
   */
  public start(): void {
    if (this._running) { return; }
    this._running = true;
    this._lastTimestamp = performance.now();
    this._rafId = requestAnimationFrame((ts) => this._tick(ts));
  }

  /**
   * Stop the animation loop and cancel pending frame.
   */
  public stop(): void {
    this._running = false;
    if (this._rafId !== null) {
      cancelAnimationFrame(this._rafId);
      this._rafId = null;
    }
  }

  /**
   * Update agent states from outside (e.g. postMessage received).
   * Deep-copies the provided array to avoid aliasing.
   *
   * @param states - Fresh agent state array from the extension host.
   */
  public setStates(states: AgentState[]): void {
    if (!states || states.length === 0) { return; }
    // Preserve running frameTimer/currentFrame so animation is continuous.
    const prev = new Map<string, { currentFrame: number; frameTimer: number }>();
    for (const s of this._states) {
      prev.set(s.id, { currentFrame: s.currentFrame, frameTimer: s.frameTimer });
    }
    this._states = states.map((s) => {
      const existing = prev.get(s.id);
      return {
        ...s,
        currentFrame: existing ? existing.currentFrame : 0,
        frameTimer:   existing ? existing.frameTimer   : 0,
      };
    });
  }

  /**
   * Toggle the stop overlay.
   *
   * @param active - True if the emergency stop is active.
   */
  public setStopActive(active: boolean): void {
    this._stopActive = active;
  }

  // ---- Private ----

  /**
   * RAF tick — advances frame timers and triggers render.
   *
   * @param timestamp - DOMHighResTimeStamp from requestAnimationFrame.
   */
  private _tick(timestamp: number): void {
    if (!this._running) { return; }

    const delta = timestamp - this._lastTimestamp;
    this._lastTimestamp = timestamp;

    this._advanceFrames(delta);
    this._render();

    this._rafId = requestAnimationFrame((ts) => this._tick(ts));
  }

  /**
   * Advance frame timers for all agents and wrap currentFrame when the
   * duration threshold is exceeded.
   *
   * @param delta - Elapsed ms since last tick.
   */
  private _advanceFrames(delta: number): void {
    if (delta <= 0 || !isFinite(delta)) { return; }

    for (const state of this._states) {
      const duration = FRAME_DURATION[state.status] ?? FRAME_DURATION.idle;
      state.frameTimer += delta;
      if (state.frameTimer >= duration) {
        state.frameTimer = 0;
        const spriteGroup = this._sprites[state.id];
        if (!spriteGroup) { continue; }
        const frames = spriteGroup[state.status as keyof typeof spriteGroup] ?? spriteGroup.idle;
        if (!frames || frames.length === 0) { continue; }
        state.currentFrame = (state.currentFrame + 1) % frames.length;
      }
    }
  }

  /**
   * Render the current frame to the canvas.
   */
  private _render(): void {
    const w = this._canvas.width;
    const h = this._canvas.height;
    renderFrame(
      this._ctx,
      this._states,
      this._sprites,
      w,
      h,
      this._stopActive
    );
  }
}
