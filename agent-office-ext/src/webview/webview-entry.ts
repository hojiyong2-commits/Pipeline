/**
 * webview-entry.ts
 * Entry point for the Agent Office WebView bundle (out/webview.js).
 * Runs entirely inside the VS Code WebView browser context.
 * IMPORTANT: No Node.js APIs, no 'vscode' module — browser APIs only.
 */

import { AgentState } from '../agentState';
import { HAMSTER_SPRITES } from './hamsterSprites';
import { AnimationLoop } from './animationLoop';

// ---------------------------------------------------------------------------
// Ambient declaration for the VS Code WebView API
// ---------------------------------------------------------------------------
declare function acquireVsCodeApi(): {
  postMessage(msg: unknown): void;
  getState(): unknown;
  setState(state: unknown): void;
};

// ---------------------------------------------------------------------------
// Bootstrap — runs once on DOMContentLoaded
// ---------------------------------------------------------------------------

(function bootstrap(): void {
  const vscode = acquireVsCodeApi();

  // ---- DOM references ----
  const canvas = document.getElementById('office-canvas') as HTMLCanvasElement | null;
  const btnStop = document.getElementById('btn-stop') as HTMLButtonElement | null;
  const btnClear = document.getElementById('btn-clear') as HTMLButtonElement | null;
  const pmInputWrapper = document.getElementById('pm-input-wrapper') as HTMLDivElement | null;
  const pmInput = document.getElementById('pm-input') as HTMLInputElement | null;
  const btnSendPm = document.getElementById('btn-send-pm') as HTMLButtonElement | null;
  const statusLabel = document.getElementById('status-label') as HTMLDivElement | null;

  if (!canvas) {
    console.error('[AgentOffice] Canvas element not found.');
    return;
  }

  // ---- Initial placeholder render ----
  const initCtx = canvas.getContext('2d');
  if (initCtx) {
    initCtx.fillStyle = '#1a1a1a';
    initCtx.fillRect(0, 0, 600, 500);
    initCtx.fillStyle = '#555';
    initCtx.font = '12px monospace';
    initCtx.textAlign = 'center';
    initCtx.fillText('Connecting to Agent Office...', 300, 250);
  }

  // ---- Animation loop (lazy-initialized on first 'update' message) ----
  let loop: AnimationLoop | null = null;

  // ---- Message handler: extension host → webview ----
  window.addEventListener('message', (event: MessageEvent) => {
    const msg = event.data as { type?: string; agents?: AgentState[]; totalTokens?: number; stopActive?: boolean } | null;
    if (!msg || msg.type !== 'update') { return; }

    const agents: AgentState[] = Array.isArray(msg.agents) ? msg.agents : [];
    const stopActive: boolean = msg.stopActive === true;
    const totalTokens: number = typeof msg.totalTokens === 'number' ? msg.totalTokens : 0;

    // Initialize AnimationLoop on first real data received.
    if (!loop) {
      try {
        loop = new AnimationLoop(canvas, HAMSTER_SPRITES, agents);
        loop.start();
      } catch (err) {
        console.error('[AgentOffice] AnimationLoop init failed:', err);
        return;
      }
    } else {
      loop.setStates(agents);
    }

    loop.setStopActive(stopActive);

    // Update PM input visibility.
    if (pmInputWrapper) {
      if (stopActive) {
        pmInputWrapper.classList.add('visible');
      } else {
        pmInputWrapper.classList.remove('visible');
      }
    }

    // Update status label.
    if (statusLabel) {
      if (stopActive) {
        statusLabel.textContent = 'EMERGENCY STOP ACTIVE — enter PM directive below';
        statusLabel.style.color = '#ff4444';
      } else {
        statusLabel.textContent = 'Total tokens: ' + totalTokens.toLocaleString();
        statusLabel.style.color = '#666';
      }
    }
  });

  // ---- Button: Emergency Stop ----
  if (btnStop) {
    btnStop.addEventListener('click', () => {
      vscode.postMessage({ type: 'emergencyStop' });
    });
  }

  // ---- Button: Clear Stop ----
  if (btnClear) {
    btnClear.addEventListener('click', () => {
      vscode.postMessage({ type: 'clearStop' });
    });
  }

  // ---- Button: Send PM directive ----
  if (btnSendPm && pmInput) {
    btnSendPm.addEventListener('click', () => {
      const cmd = (pmInput.value ?? '').trim();
      if (cmd.length === 0) { return; }
      vscode.postMessage({ type: 'pmCommand', pmCommand: cmd });
      pmInput.value = '';
    });

    pmInput.addEventListener('keydown', (e: KeyboardEvent) => {
      if (e.key === 'Enter') { btnSendPm.click(); }
    });
  }

  // ---- Keyboard shortcut: Ctrl+Shift+S ----
  document.addEventListener('keydown', (e: KeyboardEvent) => {
    if (e.ctrlKey && e.shiftKey && e.key === 'S') {
      e.preventDefault();
      vscode.postMessage({ type: 'emergencyStop' });
    }
  });
})();
