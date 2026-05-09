/**
 * webview/panel.ts
 * Implements vscode.WebviewViewProvider for Agent Office.
 * Renders the hamster canvas dashboard directly in the Activity Bar sidebar
 * using the 'agentOffice.dashboard' view ID registered in package.json.
 *
 * All canvas rendering logic lives in out/webview.js (compiled from
 * src/webview/webview-entry.ts via esbuild with platform=browser).
 * The bundle is loaded via a nonce-gated <script src="..."> tag so that the
 * VS Code Content-Security-Policy is fully satisfied.
 *
 * Key difference from the old WebviewPanel approach:
 *   - VS Code owns the lifecycle; resolveWebviewView() is called each time
 *     the sidebar view becomes visible.
 *   - postMessage is sent via this._view?.webview.postMessage() — silent
 *     no-op when the view is not yet resolved or has been disposed.
 */

import * as vscode from 'vscode';
import { AgentState } from '../agentState';

/** Message sent from extension host to WebView. */
interface UpdateMessage {
  type: 'update';
  agents: AgentState[];
  totalTokens: number;
  stopActive: boolean;
}

/** Message received from WebView to extension host. */
interface WebviewMessage {
  type: 'emergencyStop' | 'clearStop' | 'pmCommand';
  pmCommand?: string;
}

/** Callback invoked when the WebView sends a message back to the extension. */
export type OnWebviewMessage = (msg: WebviewMessage) => void;

/**
 * AgentOfficeViewProvider — WebviewViewProvider implementation.
 *
 * Registered via:
 *   vscode.window.registerWebviewViewProvider('agentOffice.dashboard', provider)
 *
 * VS Code calls resolveWebviewView() each time the sidebar panel is revealed.
 * The provider keeps a reference to the current WebviewView so that
 * postUpdate() can deliver state updates at any time.
 */
export class AgentOfficeViewProvider implements vscode.WebviewViewProvider {
  /** Stable view type identifier — must match package.json views[].id */
  public static readonly viewType = 'agentOffice.dashboard';

  /** Reference to the currently resolved WebviewView (undefined until first resolve). */
  private _view?: vscode.WebviewView;

  /** Disposables scoped to the current WebviewView lifetime. */
  private _viewDisposables: vscode.Disposable[] = [];

  /** Optional message handler registered by the extension host. */
  private _onMessage?: OnWebviewMessage;

  /**
   * @param _extensionUri - Extension root URI, used for localResourceRoots
   *                        and resolving the webview bundle path.
   */
  constructor(private readonly _extensionUri: vscode.Uri) {}

  /**
   * Called by VS Code when the 'agentOffice.dashboard' view is first shown
   * or revealed after being hidden.  May be called multiple times over the
   * extension's lifetime (e.g. panel re-open, VS Code window reload).
   *
   * @param webviewView  - The WebviewView instance provided by VS Code.
   * @param _context     - Resolve context (unused — reserved for future use).
   * @param _token       - Cancellation token (unused).
   */
  public resolveWebviewView(
    webviewView: vscode.WebviewView,
    _context: vscode.WebviewViewResolveContext,
    _token: vscode.CancellationToken
  ): void {
    // Dispose any listeners from a previous resolve cycle.
    this._disposeViewListeners();

    this._view = webviewView;

    // Configure webview options — scripts enabled, resources scoped to extension.
    webviewView.webview.options = {
      enableScripts: true,
      localResourceRoots: [this._extensionUri],
    };

    // Inject the HTML content with the compiled webview bundle.
    webviewView.webview.html = this._getWebviewContent(webviewView.webview);

    // Relay WebView → extension host messages.
    webviewView.webview.onDidReceiveMessage(
      (msg: WebviewMessage) => {
        if (this._onMessage) {
          this._onMessage(msg);
        }
      },
      null,
      this._viewDisposables
    );

    // Clear the _view reference when the view is disposed by VS Code.
    webviewView.onDidDispose(
      () => {
        this._view = undefined;
        this._disposeViewListeners();
      },
      null,
      this._viewDisposables
    );
  }

  /**
   * Register a handler for messages coming in from the WebView.
   *
   * @param handler - Callback function.
   */
  public onDidReceiveMessage(handler: OnWebviewMessage): void {
    this._onMessage = handler;
  }

  /**
   * Post an agent state update to the WebView.
   * Silent no-op if the view has not yet been resolved or has been disposed —
   * the optional chaining (?.) ensures no exception is thrown.
   *
   * @param states      - Current agent state array.
   * @param totalTokens - Sum of all token counts.
   * @param stopActive  - Whether the emergency stop is active.
   */
  public postUpdate(
    states: AgentState[],
    totalTokens: number,
    stopActive: boolean
  ): void {
    const msg: UpdateMessage = {
      type: 'update',
      agents: states,
      totalTokens,
      stopActive,
    };
    // Uses optional chaining — silent no-op when _view is undefined.
    this._view?.webview.postMessage(msg);
  }

  /**
   * Dispose the provider.
   * Clears the view reference and all scoped event listeners.
   */
  public dispose(): void {
    this._view = undefined;
    this._disposeViewListeners();
  }

  // ── Private helpers ──────────────────────────────────────────────────────

  /**
   * Dispose all event listeners scoped to the current WebviewView.
   * Called both on resolveWebviewView (start fresh) and on dispose.
   */
  private _disposeViewListeners(): void {
    for (const d of this._viewDisposables) {
      d.dispose();
    }
    this._viewDisposables = [];
  }

  /**
   * Build the WebView HTML content.
   * Injects out/webview.js as a nonce-gated external script so that
   * AnimationLoop, HAMSTER_SPRITES and all canvas rendering run inside
   * the WebView browser context (not the extension host).
   *
   * @param webview - The webview instance used to resolve URIs and CSP source.
   * @returns Full HTML string for the WebView.
   */
  private _getWebviewContent(webview: vscode.Webview): string {
    const nonce = this._getNonce();
    const cspSource = webview.cspSource;

    // Resolve the compiled webview bundle as a webview-safe URI.
    const webviewScriptUri = webview.asWebviewUri(
      vscode.Uri.joinPath(this._extensionUri, 'out', 'webview.js')
    );

    return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta http-equiv="Content-Security-Policy"
    content="default-src 'none';
             img-src ${cspSource} https:;
             script-src 'nonce-${nonce}' ${cspSource};
             style-src 'unsafe-inline';" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Agent Office</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: #1a1a1a;
      color: #ccc;
      font-family: monospace;
      display: flex;
      flex-direction: column;
      align-items: center;
      padding: 8px;
      user-select: none;
    }
    #canvas-wrapper {
      position: relative;
      border: 1px solid #333;
    }
    canvas {
      display: block;
    }
    #controls {
      display: flex;
      gap: 8px;
      margin-top: 6px;
      width: 100%;
      max-width: 600px;
      align-items: center;
    }
    #btn-stop {
      background: #cc0000;
      color: #fff;
      border: none;
      padding: 6px 14px;
      font-family: monospace;
      font-size: 12px;
      cursor: pointer;
      border-radius: 3px;
      flex-shrink: 0;
    }
    #btn-stop:hover { background: #ff2222; }
    #btn-clear {
      background: #333;
      color: #ccc;
      border: 1px solid #555;
      padding: 6px 10px;
      font-family: monospace;
      font-size: 11px;
      cursor: pointer;
      border-radius: 3px;
      flex-shrink: 0;
    }
    #btn-clear:hover { background: #444; }
    #pm-input-wrapper {
      display: none;
      gap: 4px;
      align-items: center;
      flex: 1;
    }
    #pm-input-wrapper.visible { display: flex; }
    #pm-input {
      flex: 1;
      background: #2a2a2a;
      color: #eee;
      border: 1px solid #555;
      padding: 5px 8px;
      font-family: monospace;
      font-size: 11px;
      border-radius: 3px;
    }
    #btn-send-pm {
      background: #FF8C00;
      color: #fff;
      border: none;
      padding: 5px 10px;
      font-family: monospace;
      font-size: 11px;
      cursor: pointer;
      border-radius: 3px;
      flex-shrink: 0;
    }
    #btn-send-pm:hover { background: #FFA500; }
    #status-label {
      font-size: 10px;
      color: #666;
      margin-top: 4px;
      min-height: 14px;
      width: 100%;
      max-width: 600px;
    }
  </style>
</head>
<body>
  <div id="canvas-wrapper">
    <canvas id="office-canvas" width="600" height="500"></canvas>
  </div>

  <div id="controls">
    <button id="btn-stop" title="Ctrl+Shift+S">STOP (Ctrl+Shift+S)</button>
    <button id="btn-clear">Clear Stop</button>
    <div id="pm-input-wrapper">
      <input id="pm-input" type="text" placeholder="PM directive for next run..." maxlength="500" />
      <button id="btn-send-pm">Send</button>
    </div>
  </div>
  <div id="status-label">Waiting for agent data...</div>

  <!-- webview.js contains AnimationLoop + HAMSTER_SPRITES + all canvas rendering.
       The nonce attribute satisfies the Content-Security-Policy script-src directive. -->
  <script nonce="${nonce}" src="${webviewScriptUri}"></script>
</body>
</html>`;
  }

  /**
   * Generate a random nonce string for Content-Security-Policy.
   *
   * @returns 32-character alphanumeric nonce.
   */
  private _getNonce(): string {
    const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
    let result = '';
    for (let i = 0; i < 32; i++) {
      result += chars.charAt(Math.floor(Math.random() * chars.length));
    }
    return result;
  }
}
