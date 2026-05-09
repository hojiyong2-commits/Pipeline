/**
 * extension.ts
 * VS Code extension entry point for Agent Office.
 * Registers the WebviewViewProvider, FileSystemWatcher, and command handlers.
 *
 * Architecture change (v0.1.0):
 *   Previous: WebviewPanel opened in an editor tab (ViewColumn.Two).
 *   Current:  WebviewViewProvider registered for 'agentOffice.dashboard',
 *             rendered directly in the Activity Bar sidebar.
 *
 * The provider is registered once; VS Code calls resolveWebviewView()
 * each time the sidebar panel is revealed.
 */

import * as vscode from 'vscode';
import { createInitialStates } from './agentState';
import { DataWatcher } from './dataWatcher';
import { AgentOfficeViewProvider } from './webview/panel';
import {
  writeStopSignal,
  clearStopSignal,
  isStopSignalActive,
} from './stopSignalHandler';
import { getTotalTokens } from './tokenBar';

/**
 * Extension activation entry point.
 * Called by VS Code when the 'agentOffice.dashboard' view container is first accessed.
 *
 * @param context - Extension context provided by VS Code.
 */
export function activate(context: vscode.ExtensionContext): void {
  // Initialize shared agent state.
  const states = createInitialStates();

  // Check for pre-existing stop signal on activation.
  let stopActive = isStopSignalActive();

  // Create the WebviewViewProvider and register it for the sidebar view.
  // VS Code will call provider.resolveWebviewView() when the view is first shown.
  const provider = new AgentOfficeViewProvider(context.extensionUri);

  const providerRegistration = vscode.window.registerWebviewViewProvider(
    AgentOfficeViewProvider.viewType,
    provider
  );
  context.subscriptions.push(providerRegistration);

  // Handle messages sent from the WebView back to the extension.
  provider.onDidReceiveMessage((msg) => {
    switch (msg.type) {
      case 'emergencyStop': {
        const payload = writeStopSignal(undefined);
        if (payload) {
          stopActive = true;
          for (const s of states) { s.status = 'stopped'; }
          provider.postUpdate([...states], getTotalTokens(states), stopActive);
          vscode.window.showWarningMessage('[AgentOffice] Emergency stop signal sent.');
        }
        break;
      }
      case 'clearStop': {
        clearStopSignal();
        stopActive = false;
        // Reset all agents to idle after clearing.
        for (const s of states) {
          if (s.status === 'stopped') { s.status = 'idle'; }
        }
        provider.postUpdate([...states], getTotalTokens(states), stopActive);
        vscode.window.showInformationMessage('[AgentOffice] Stop signal cleared.');
        break;
      }
      case 'pmCommand': {
        if (typeof msg.pmCommand === 'string' && msg.pmCommand.trim().length > 0) {
          const payload = writeStopSignal(msg.pmCommand.trim());
          if (payload) {
            stopActive = true;
            for (const s of states) { s.status = 'stopped'; }
            provider.postUpdate([...states], getTotalTokens(states), stopActive);
            vscode.window.showInformationMessage(
              `[AgentOffice] PM directive sent: "${msg.pmCommand.trim()}"`
            );
          }
        }
        break;
      }
    }
  });

  // Start the data watcher — updates states in-place and calls postUpdate.
  const watcher = new DataWatcher(states, (updatedStates) => {
    stopActive = isStopSignalActive();
    provider.postUpdate(updatedStates, getTotalTokens(updatedStates), stopActive);
  });
  watcher.start();
  context.subscriptions.push({ dispose: () => watcher.dispose() });

  // Send an initial update so the WebView shows the current state immediately
  // once resolveWebviewView() has been called by VS Code.
  provider.postUpdate([...states], getTotalTokens(states), stopActive);

  // Register emergencyStop command (also bound to Ctrl+Shift+S via package.json).
  const stopCmd = vscode.commands.registerCommand(
    'agentOffice.emergencyStop',
    () => {
      const payload = writeStopSignal(undefined);
      if (payload) {
        stopActive = true;
        for (const s of states) { s.status = 'stopped'; }
        provider.postUpdate([...states], getTotalTokens(states), stopActive);
        vscode.window.showWarningMessage('[AgentOffice] Emergency stop activated via command.');
      }
    }
  );
  context.subscriptions.push(stopCmd);

  // Register clearStop command.
  const clearCmd = vscode.commands.registerCommand(
    'agentOffice.clearStop',
    () => {
      clearStopSignal();
      stopActive = false;
      for (const s of states) {
        if (s.status === 'stopped') { s.status = 'idle'; }
      }
      provider.postUpdate([...states], getTotalTokens(states), stopActive);
      vscode.window.showInformationMessage('[AgentOffice] Stop signal cleared via command.');
    }
  );
  context.subscriptions.push(clearCmd);

  // Push provider dispose to subscriptions for clean deactivation.
  context.subscriptions.push({ dispose: () => provider.dispose() });
}

/**
 * Extension deactivation hook.
 * VS Code will call dispose() on all context.subscriptions automatically.
 */
export function deactivate(): void {
  // All cleanup is handled via context.subscriptions.
}
