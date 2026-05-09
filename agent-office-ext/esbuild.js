// esbuild.js — bundle extension.ts (Node host) and webview-entry.ts (browser)
const esbuild = require('esbuild');
const path = require('path');

const isProduction = process.argv.includes('--production');
const isWatch = process.argv.includes('--watch');

/**
 * Extension host bundle — runs in Node.js inside VS Code.
 * 'vscode' is provided by the host at runtime, so it must be external.
 * @type {import('esbuild').BuildOptions}
 */
const extensionBuildOptions = {
  entryPoints: [path.join(__dirname, 'src', 'extension.ts')],
  bundle: true,
  outfile: path.join(__dirname, 'out', 'extension.js'),
  external: ['vscode'],
  format: 'cjs',
  platform: 'node',
  target: 'node18',
  sourcemap: !isProduction,
  minify: isProduction,
  logLevel: 'info',
};

/**
 * WebView bundle — runs in the sandboxed browser context inside VS Code WebView.
 * No Node.js APIs allowed; 'vscode' is NOT available (acquireVsCodeApi() is used instead).
 * Output is a self-contained IIFE so it can be injected via a <script> tag.
 * @type {import('esbuild').BuildOptions}
 */
const webviewBuildOptions = {
  entryPoints: [path.join(__dirname, 'src', 'webview', 'webview-entry.ts')],
  bundle: true,
  outfile: path.join(__dirname, 'out', 'webview.js'),
  // No externals — everything must be bundled for the sandboxed WebView.
  format: 'iife',
  platform: 'browser',
  target: 'es2020',
  sourcemap: !isProduction,
  minify: isProduction,
  logLevel: 'info',
};

async function main() {
  if (isWatch) {
    const [extCtx, wvCtx] = await Promise.all([
      esbuild.context(extensionBuildOptions),
      esbuild.context(webviewBuildOptions),
    ]);
    await Promise.all([extCtx.watch(), wvCtx.watch()]);
    console.log('[esbuild] Watching for changes (extension + webview)...');
  } else {
    await Promise.all([
      esbuild.build(extensionBuildOptions),
      esbuild.build(webviewBuildOptions),
    ]);
    console.log('[esbuild] Build complete (extension + webview).');
  }
}

main().catch((err) => {
  console.error('[esbuild] Build failed:', err);
  process.exit(1);
});
