const vscode = require("vscode");
const fs = require("fs");
const path = require("path");

const DEFAULT_TOKEN_BUDGET = 200000;

const PHASE_ORDER = ["pm", "dev", "qa", "sec", "build", "harness", "architect"];
const PHASE_LABELS = {
  pm: "Phase 1 - PM",
  dev: "Phase 2 - Dev",
  qa: "Phase 4 - QA",
  sec: "Phase 5 - Security",
  build: "Phase 6 - Build",
  harness: "Phase 7 - Harness",
  architect: "Phase 8 - Architect"
};

const AGENTS = [
  ["pm-agent", "PM", "Planning", "#facc15", "Reception", 2.1, 9.0, ["pm"], "요구사항을 작게 쪼개 담당자에게 전달합니다.", "요청을 어떤 단위로 나눠야 다음 에이전트가 바로 실행할 수 있는지 판단 중입니다."],
  ["dev-agent", "Dev", "Implementation", "#3b82f6", "Lab", 3.6, 6.2, ["dev"], "핵심 Python 로직을 구현합니다.", "기존 자동화 코드와 VS Code 확장 코드 사이의 책임 경계를 어디에 둘지 고르는 중입니다."],
  ["ui-app-agent", "UI", "Interface", "#f472b6", "Reception", 4.2, 9.0, ["ui"], "사용자가 보는 화면과 조작 흐름을 다듬습니다.", "좁은 Activity Bar 패널에서 지도, 단계, 명령 입력이 동시에 읽히게 만드는 배치를 고민 중입니다."],
  ["qa-agent", "QA", "Verification", "#22c55e", "Office", 9.4, 8.2, ["qa"], "요구사항과 실제 동작이 맞는지 검증합니다.", "사용자가 누른 Pause/Resume이 파일 상태와 화면 상태에 동시에 반영되는지 확인 중입니다."],
  ["security-agent", "Security", "Audit", "#ef4444", "Office", 10.7, 9.4, ["sec"], "경로, 입력값, 제어 API 위험을 점검합니다.", "명령 큐가 작업공간 밖 파일을 건드리지 않도록 저장 위치와 길이 제한을 점검 중입니다."],
  ["build-agent", "Build", "Packaging", "#fb923c", "Clinic", 1.5, 1.3, ["build"], "실행/패키징 흐름을 정리합니다.", "설치된 사용자 확장 복사본과 소스 확장 폴더가 같은 자산을 갖도록 맞추는 중입니다."],
  ["test-harness-agent", "Harness", "Benchmark", "#a855f7", "Clinic", 3.9, 1.4, ["harness"], "정량 점검과 결과 기록을 맡습니다.", "토큰 로그와 파이프라인 로그가 비어 있을 때도 화면이 멈춘 것처럼 보이지 않게 만드는 중입니다."],
  ["prompt-architect-agent", "Architect", "RCA", "#38bdf8", "Ward", 8.1, 1.35, ["architect"], "로그를 보고 다음 개선점을 정리합니다.", "에이전트가 실제로 생각 중인 지점과 단순 역할 설명을 화면에서 구분하는 문장을 다듬는 중입니다."],
  ["agent-factory-agent", "Factory", "Agent Design", "#5eead4", "Ward", 8.0, 4.45, [], "새 전문 에이전트가 필요할 때 설계합니다.", "새 에이전트를 만들지 기존 역할을 확장할지 결정할 기준을 보고 있습니다."],
  ["protocol-evolution-agent", "Protocol", "Sync", "#a3e635", "Ward", 10.8, 1.35, [], "새 규칙을 프로토콜 전체와 맞춥니다.", "Claude/GPT 공동 문서와 로컬 제어 파일이 서로 다른 말을 하지 않게 맞추는 중입니다."],
  ["power-automate-agent", "PowerAuto", "Flow", "#c084fc", "Ward", 10.8, 4.45, [], "Power Automate 플로우 산출물을 만듭니다.", "Python 앱과 Flow 자동화의 책임 범위를 어디서 끊을지 판단 중입니다."]
].map(([id, name, role, color, department, x, y, phaseKeys, summary, hardPart]) => ({
  id, name, role, color, department, desk: { x, y }, phaseKeys, summary, hardPart
}));

const PHASE_TO_AGENT = {};
for (const agent of AGENTS) {
  for (const key of agent.phaseKeys) PHASE_TO_AGENT[key] = agent.id;
}

let activeView;
let watchers = [];

function workspaceRoot() {
  const folders = vscode.workspace.workspaceFolders;
  return folders && folders.length ? folders[0].uri.fsPath : null;
}

function readText(file) {
  try {
    return fs.readFileSync(file, "utf8");
  } catch {
    return "";
  }
}

function readJson(file, fallback = {}) {
  const text = readText(file);
  if (!text.trim()) return { ...fallback };
  try {
    const parsed = JSON.parse(text);
    return parsed && typeof parsed === "object" ? parsed : { ...fallback };
  } catch {
    return { ...fallback };
  }
}

function writeJson(file, data) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, JSON.stringify(data, null, 2) + "\n", "utf8");
}

function nowIso() {
  return new Date().toISOString();
}

function appendPipelineEvent(root, message) {
  const file = path.join(root, "pipeline_state.json");
  const state = readJson(file, {});
  if (!Object.keys(state).length) return;
  if (!Array.isArray(state.event_log)) state.event_log = [];
  state.event_log.push({ ts: nowIso().replace(".000", ""), msg: message });
  state.event_log = state.event_log.slice(-100);
  state.updated_at = nowIso().replace(".000", "");
  writeJson(file, state);
}

function controlFile(root) {
  return path.join(root, "agent_office_control.json");
}

function readControl(root) {
  const fallback = {
    emergency_stop: false,
    paused: false,
    pm_instruction: "",
    last_command: null,
    command_queue: [],
    updated_at: null,
    events: []
  };
  const state = readJson(controlFile(root), fallback);
  const paused = Boolean(state.paused || state.emergency_stop);
  return {
    ...fallback,
    ...state,
    emergency_stop: paused,
    paused,
    command_queue: Array.isArray(state.command_queue) ? state.command_queue : [],
    events: Array.isArray(state.events) ? state.events : []
  };
}

function saveControl(root, state, message) {
  const next = { ...state, updated_at: nowIso() };
  next.events = Array.isArray(next.events) ? next.events : [];
  next.events.push({ ts: next.updated_at, msg: message });
  next.events = next.events.slice(-30);
  next.command_queue = Array.isArray(next.command_queue) ? next.command_queue.slice(-20) : [];
  next.paused = Boolean(next.paused || next.emergency_stop);
  next.emergency_stop = next.paused;
  writeJson(controlFile(root), next);
  return next;
}

function commandRecord(text, mode) {
  const trimmed = String(text || "").trim();
  return {
    id: `cmd-${Date.now()}`,
    ts: nowIso(),
    mode,
    message: trimmed
  };
}

function normalizePhases(raw) {
  const phasesRaw = raw.phases || {};
  return PHASE_ORDER.map((key, idx) => {
    const phase = phasesRaw[key] && typeof phasesRaw[key] === "object" ? phasesRaw[key] : {};
    const evidence = phase.evidence && typeof phase.evidence === "object" ? phase.evidence : {};
    let files = evidence.files || phase.files || [];
    if (typeof files === "string") files = files.split(",").map(s => s.trim()).filter(Boolean);
    return {
      name: PHASE_LABELS[key] || `Phase ${idx + 1}`,
      phase_key: key,
      status: String(phase.status || "PENDING").toUpperCase(),
      completed_at: phase.completed_at || null,
      files,
      score: phase.score ?? evidence.score,
      verdict: phase.verdict || evidence.verdict,
      risk: phase.risk || evidence.risk,
      exe: phase.exe || evidence.exe,
      notes: phase.notes || []
    };
  });
}

function loadPipeline(root) {
  const raw = readJson(path.join(root, "pipeline_state.json"), {});
  const phases = normalizePhases(raw);
  let currentIdx = phases.findIndex(p => p.phase_key === raw.current_phase);
  if (currentIdx < 0) {
    currentIdx = phases.findIndex(p => !["DONE", "PASS", "SAFE", "SKIP", "SKIPPED", "COMPLETE"].includes(p.status));
  }
  const events = Array.isArray(raw.event_log)
    ? raw.event_log.slice(-20).map(e => ({ timestamp: e.ts || e.timestamp || "", message: e.msg || e.message || "" }))
    : [];
  return {
    id: raw.pipeline_id || null,
    description: raw.description || "",
    terminal_state: raw.terminal_state || null,
    phases,
    current_phase_index: currentIdx,
    events
  };
}

function tokenBudget() {
  const raw = String(process.env.AGENT_OFFICE_TOKEN_BUDGET || "").replace(/,/g, "");
  const parsed = Number.parseInt(raw, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : DEFAULT_TOKEN_BUDGET;
}

function normalizeUsage(usage) {
  if (!usage || typeof usage !== "object") return { input: 0, output: 0, total: 0 };
  const input = ["input_tokens", "prompt_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"]
    .reduce((sum, key) => sum + (Number.isInteger(usage[key]) ? usage[key] : 0), 0);
  let output = ["output_tokens", "completion_tokens"]
    .reduce((sum, key) => sum + (Number.isInteger(usage[key]) ? usage[key] : 0), 0);
  if (Number.isInteger(usage.total_tokens) && usage.total_tokens > input + output) {
    output += usage.total_tokens - input - output;
  }
  return { input, output, total: input + output };
}

function inferAgent(record, fallback) {
  const text = JSON.stringify(record).toLowerCase();
  for (const agent of AGENTS) {
    if (text.includes(agent.id)) return agent.id;
  }
  return fallback || "pm-agent";
}

function loadTokens(root, fallbackAgent) {
  const file = path.join(root, "logs", "token_log.jsonl");
  const budget = tokenBudget();
  const byAgent = Object.fromEntries(AGENTS.map(a => [a.id, { input: 0, output: 0, total: 0 }]));
  let totalInput = 0;
  let totalOutput = 0;
  let entries = 0;
  let lastTs = null;
  for (const line of readText(file).split(/\r?\n/)) {
    if (!line.trim()) continue;
    let record;
    try { record = JSON.parse(line); } catch { continue; }
    entries += 1;
    lastTs = record.ts || lastTs;
    const usage = normalizeUsage((record.data && record.data.usage) || record.usage || {});
    totalInput += usage.input;
    totalOutput += usage.output;
    const agentId = inferAgent(record, fallbackAgent);
    byAgent[agentId].input += usage.input;
    byAgent[agentId].output += usage.output;
    byAgent[agentId].total += usage.total;
  }
  const total = totalInput + totalOutput;
  const remaining = Math.max(0, budget - total);
  const usedPercent = budget ? Math.min(100, Math.round((total / budget) * 1000) / 10) : 0;
  return {
    total_input: totalInput,
    total_output: totalOutput,
    total_tokens: total,
    remaining_tokens: remaining,
    budget_tokens: budget,
    used_percent: usedPercent,
    remaining_percent: Math.max(0, Math.round((100 - usedPercent) * 10) / 10),
    entries,
    last_ts: lastTs,
    by_agent: byAgent,
    source: "VS Code workspace files"
  };
}

function recentResults(root) {
  return readText(path.join(root, "test_results.jsonl"))
    .split(/\r?\n/)
    .filter(Boolean)
    .slice(-10)
    .map(line => {
      try { return JSON.parse(line); } catch { return null; }
    })
    .filter(Boolean);
}

function phaseStatusToAgent(status) {
  if (["DONE", "PASS", "SAFE", "COMPLETE"].includes(status)) return "idle";
  if (["RUNNING", "IN_PROGRESS", "ACTIVE"].includes(status)) return "working";
  if (["FAIL", "BLOCK", "ERROR"].includes(status)) return "alert";
  if (["SKIP", "SKIPPED", "N/A", "NA"].includes(status)) return "skipped";
  return "pending";
}

function activeHardPart(agent, phase, control, status) {
  if (control.paused) {
    if (agent.id === "pm-agent") {
      return control.pm_instruction
        ? `사용자 지시를 반영해 어느 단계부터 다시 시작할지 정리 중입니다: ${control.pm_instruction}`
        : "사용자가 새 지시를 넣을 때까지, 기존 작업을 어디서 끊었는지 보존하는 중입니다.";
    }
    return "Pause 상태라 지금은 새 판단을 진행하지 않고, PM의 재분배 지시를 기다리는 중입니다.";
  }
  if (phase && Array.isArray(phase.notes) && phase.notes.length) {
    return String(phase.notes[phase.notes.length - 1]);
  }
  if (status === "working" || status === "coordinating") return agent.hardPart;
  if (status === "idle") return "완료된 단계라 현재 고민 중인 지점은 없습니다.";
  if (status === "pending") return "아직 차례가 오지 않아 입력 조건과 이전 단계 산출물을 기다리는 중입니다.";
  return agent.hardPart;
}

function buildAgents(pipeline, control, tokens) {
  const phaseByKey = Object.fromEntries(pipeline.phases.map(p => [p.phase_key, p]));
  const current = pipeline.phases[pipeline.current_phase_index];
  const currentKey = current ? current.phase_key : "";
  const currentAgentId = PHASE_TO_AGENT[currentKey] || "";
  const complete = ["COMPLETE", "DONE", "FINISHED"].includes(String(pipeline.terminal_state || "").toUpperCase());

  return AGENTS.map(agent => {
    const phase = agent.phaseKeys.map(k => phaseByKey[k]).find(Boolean);
    let status = phase ? phaseStatusToAgent(phase.status) : "standby";
    let goal = phase ? phase.name : "대기";
    let progress = phase && ["DONE", "PASS", "SAFE", "COMPLETE"].includes(phase.status) ? 100 : 0;
    let phaseStatus = phase ? phase.status : "-";
    let issue = phase && ["FAIL", "BLOCK", "ERROR"].includes(phase.status) ? String(phase.notes || phase.status) : "";
    let position = { ...agent.desk };

    if (phase && currentKey && agent.phaseKeys.includes(currentKey) && !complete && !["DONE", "PASS", "SAFE", "SKIP", "SKIPPED", "N/A", "COMPLETE", "FAIL", "BLOCK", "ERROR"].includes(phase.status)) {
      status = "working";
      progress = 50;
    }
    if (complete && status !== "skipped") status = "idle";

    if (control.paused) {
      if (agent.id === "pm-agent") {
        status = "working";
        phaseStatus = "AWAITING_USER";
        goal = "Pause: 사용자 지시 입력 대기";
        progress = 50;
        issue = control.pm_instruction || "지시를 입력한 뒤 '지시 저장 후 재개'를 누르면 PM이 재분배 상태로 돌아갑니다.";
        position = { x: 5, y: 4 };
      } else {
        status = "paused";
        phaseStatus = "PAUSED";
        goal = "Pause로 작업 보류";
        issue = "PM 재지시를 기다립니다.";
        progress = 0;
      }
    } else if (agent.id === "pm-agent" && currentAgentId && currentAgentId !== "pm-agent") {
      const target = AGENTS.find(a => a.id === currentAgentId);
      if (target) {
        status = "coordinating";
        goal = `${target.name} 에이전트에게 업무 전달`;
        progress = 65;
        position = { x: target.desk.x - 0.45, y: target.desk.y - 0.25 };
      }
    }

    const usage = tokens.by_agent[agent.id] || { input: 0, output: 0, total: 0 };
    return {
      ...agent,
      position,
      status,
      phase_status: phaseStatus,
      current_task: goal,
      goal,
      progress,
      issue,
      hard_part: activeHardPart(agent, phase, control, status),
      files: phase ? phase.files : [],
      tokens_used: usage.total,
      tokens_input: usage.input,
      tokens_output: usage.output
    };
  });
}

function buildPayload(root) {
  const pipeline = loadPipeline(root);
  const current = pipeline.phases[pipeline.current_phase_index];
  const fallbackAgent = current ? PHASE_TO_AGENT[current.phase_key] || "pm-agent" : "pm-agent";
  const tokens = loadTokens(root, fallbackAgent);
  const control = readControl(root);
  return {
    pipeline,
    agents: buildAgents(pipeline, control, tokens),
    recent_results: recentResults(root),
    tokens,
    control,
    server_ts: nowIso()
  };
}

function sendState() {
  const root = workspaceRoot();
  if (!activeView || !root) return;
  activeView.webview.postMessage({ type: "state", payload: buildPayload(root) });
}

function resetWatchers(context) {
  for (const watcher of watchers) watcher.dispose();
  watchers = [];
  const patterns = [
    "pipeline_state.json",
    "test_results.jsonl",
    "logs/token_log.jsonl",
    "agent_office_control.json"
  ];
  for (const pattern of patterns) {
    const watcher = vscode.workspace.createFileSystemWatcher(new vscode.RelativePattern(vscode.workspace.workspaceFolders[0], pattern));
    watcher.onDidCreate(sendState, null, context.subscriptions);
    watcher.onDidChange(sendState, null, context.subscriptions);
    watcher.onDidDelete(sendState, null, context.subscriptions);
    watchers.push(watcher);
    context.subscriptions.push(watcher);
  }
}

async function runWorkspaceTask(label) {
  const tasks = await vscode.tasks.fetchTasks();
  const task = tasks.find(candidate => candidate.name === label);
  if (!task) {
    vscode.window.showWarningMessage(`VS Code task not found: ${label}`);
    return false;
  }
  await vscode.tasks.executeTask(task);
  return true;
}

class AgentOfficeViewProvider {
  constructor(context) {
    this.context = context;
  }

  resolveWebviewView(webviewView) {
    activeView = webviewView;
    webviewView.webview.options = {
      enableScripts: true,
      localResourceRoots: [vscode.Uri.joinPath(this.context.extensionUri, "media")]
    };
    const mapUri = webviewView.webview.asWebviewUri(vscode.Uri.joinPath(this.context.extensionUri, "media", "agent-office-map.png"));
    webviewView.webview.html = renderWebview(mapUri, webviewView.webview.cspSource);
    webviewView.webview.onDidReceiveMessage((message) => this.handleMessage(message));
    resetWatchers(this.context);
    setTimeout(sendState, 50);
  }

  async handleMessage(message) {
    const root = workspaceRoot();
    if (!root || !message || typeof message !== "object") return;
    const control = readControl(root);
    if (message.type === "ready" || message.type === "refresh") {
      sendState();
      return;
    }
    if (message.type === "pause") {
      if (control.paused) {
        saveControl(root, { ...control, paused: false, emergency_stop: false }, "Pause 해제: PM 재분배 모드");
        appendPipelineEvent(root, "VS Code Agent Office Pause 해제 - PM 재분배 모드");
      } else {
        saveControl(root, { ...control, paused: true, emergency_stop: true }, "Pause: 모든 에이전트 작업 보류, 사용자 지시 입력 대기");
        appendPipelineEvent(root, "VS Code Agent Office Pause - 사용자 지시 입력 대기");
      }
      sendState();
      return;
    }
    if (message.type === "saveInstruction" || message.type === "resumeWithInstruction") {
      const text = String(message.message || "").slice(0, 4000);
      const record = commandRecord(text, message.type === "resumeWithInstruction" ? "resume" : "pause");
      const commandQueue = [...control.command_queue, record].slice(-20);
      const shouldResume = message.type === "resumeWithInstruction";
      saveControl(root, {
        ...control,
        paused: !shouldResume,
        emergency_stop: !shouldResume,
        pm_instruction: text,
        last_command: record,
        command_queue: commandQueue
      }, shouldResume ? "PM 지시 저장 후 재개" : "PM 지시 저장");
      if (text.trim()) {
        appendPipelineEvent(root, `${shouldResume ? "PM 지시 저장 후 재개" : "PM 지시 저장"}: ${text.trim().slice(0, 160)}`);
      }
      sendState();
      return;
    }
    if (message.type === "resume") {
      saveControl(root, { ...control, paused: false, emergency_stop: false }, "Pause 해제: PM 재분배 모드");
      appendPipelineEvent(root, "VS Code Agent Office Pause 해제 - PM 재분배 모드");
      sendState();
      return;
    }
    if (message.type === "launchClaude") {
      const launched = await runWorkspaceTask("Launch Claude Code");
      appendPipelineEvent(root, launched ? "VS Code task 실행: Launch Claude Code" : "VS Code task 없음: Launch Claude Code");
      sendState();
    }
  }
}

function renderWebview(mapUri, cspSource) {
  const nonce = String(Date.now());
  return `<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src ${cspSource} data:; style-src 'unsafe-inline'; script-src 'nonce-${nonce}';">
<style>
  * { box-sizing: border-box; }
  html, body { margin: 0; width: 100%; height: 100%; overflow: hidden; background: #111827; color: #1f2937; font-family: "Segoe UI", "Malgun Gothic", sans-serif; }
  .app { height: 100vh; display: grid; grid-template-rows: auto 1fr; background: #f4f7fb; }
  header { display: flex; gap: 10px; align-items: center; justify-content: space-between; padding: 10px 12px; background: #facc15; border-bottom: 3px solid #92400e; }
  h1 { margin: 0; font-size: 15px; display: flex; align-items: center; gap: 8px; white-space: nowrap; }
  .meta { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; justify-content: flex-end; }
  .pill { font-size: 11px; font-weight: 800; padding: 4px 8px; background: rgba(255,255,255,.7); border: 1px solid rgba(0,0,0,.1); }
  .token { min-width: 250px; display: grid; grid-template-columns: 1fr auto; gap: 3px 8px; padding: 6px 8px; background: rgba(255,255,255,.75); border: 1px solid rgba(120,53,15,.2); }
  .token strong { font-size: 10px; color: #78350f; }
  .token span { font-size: 11px; font-weight: 900; }
  .track { grid-column: 1/-1; height: 12px; background: #111827; border: 2px solid #1f2937; box-shadow: inset 0 0 0 2px #fef3c7; }
  .fill { height: 100%; width: 100%; background: repeating-linear-gradient(90deg, rgba(255,255,255,.2) 0 6px, transparent 6px 12px), linear-gradient(90deg, #22c55e, #facc15 70%, #ef4444); transition: width .25s steps(12); }
  button { border: 0; font-weight: 900; cursor: pointer; }
  .stop { padding: 9px 12px; background: #991b1b; color: white; box-shadow: 0 3px 0 #450a0a; }
  .stop.paused { background: #111827; color: #facc15; }
  main { min-height: 0; display: grid; grid-template-columns: minmax(520px, 1fr) 340px; gap: 10px; padding: 10px; }
  .office, .panel { background: rgba(255,255,255,.96); border: 1px solid #dbe3ef; box-shadow: 0 6px 18px rgba(15,23,42,.12); }
  .office h2, .panel h3 { margin: 0; padding: 8px 10px; font-size: 13px; border-bottom: 1px solid #e5e7eb; display: flex; justify-content: space-between; gap: 8px; }
  .stage { position: relative; width: 100%; max-height: calc(100vh - 104px); min-height: 520px; aspect-ratio: 1 / 1; background: #dfe8ef url("${mapUri}") center / contain no-repeat; overflow: hidden; image-rendering: pixelated; }
  .stage::before { content: ""; position: absolute; inset: 0; pointer-events: none; box-shadow: inset 0 0 0 2px #283046; }
  .desk { display: none; }
  .agent { position: absolute; width: 38px; height: 38px; transform: translate(-50%,-50%); transition: left .8s cubic-bezier(.6,0,.3,1), top .8s cubic-bezier(.6,0,.3,1); image-rendering: pixelated; cursor: pointer; z-index: 5; filter: drop-shadow(0 2px 2px rgba(0,0,0,.3)); }
  .agent:hover { z-index: 20; transform: translate(-50%,-56%) scale(1.25); }
  .agent .name { position: absolute; bottom: -13px; left: 50%; transform: translateX(-50%); font-size: 9px; font-weight: 900; background: rgba(255,255,255,.92); padding: 1px 4px; white-space: nowrap; }
  .agent.working, .agent.coordinating { animation: wander 2.4s steps(4) infinite; }
  .agent.working .body, .agent.coordinating .body { animation: bob .7s steps(2) infinite; }
  .agent.paused { opacity: .55; filter: grayscale(.8); }
  .agent.paused::after { content: "정지"; position: absolute; top: -9px; right: -10px; background: #111827; color: #facc15; font-size: 9px; font-weight: 900; border: 2px solid white; }
  .agent.coordinating::after { content: "전달"; position: absolute; top: -9px; right: -12px; background: #f59e0b; color: #451a03; font-size: 9px; font-weight: 900; border: 2px solid white; }
  .agent.alert { animation: shake .5s infinite; }
  .agent.skipped { opacity: .55; filter: grayscale(.85); }
  @keyframes bob { 50% { transform: translateY(-3px); } }
  @keyframes wander { 0%,100% { margin-left: -3px; margin-top: 0; } 25% { margin-left: 3px; margin-top: -2px; } 50% { margin-left: 4px; margin-top: 3px; } 75% { margin-left: -2px; margin-top: 2px; } }
  @keyframes shake { 25% { transform: translate(-52%,-50%) rotate(-3deg); } 75% { transform: translate(-48%,-50%) rotate(3deg); } }
  aside { min-height: 0; display: flex; flex-direction: column; gap: 10px; overflow: auto; }
  .panel { padding-bottom: 10px; }
  .tokens, .phases, .events, .control { padding: 8px 10px; font-size: 12px; }
  .mini-token { display: grid; grid-template-columns: 76px 1fr auto; gap: 6px; align-items: center; margin: 4px 0; font-size: 11px; }
  .mini-token i { height: 6px; background: #e5e7eb; border: 1px solid #cbd5e1; } .mini-token i span { display: block; height: 100%; background: #38bdf8; }
  .phase { display: grid; grid-template-columns: 22px 1fr auto; gap: 6px; align-items: center; padding: 5px 0; border-bottom: 1px dashed #e5e7eb; }
  .dot { width: 20px; height: 20px; display: grid; place-items: center; color: white; font-weight: 900; background: #94a3b8; }
  .dot.done { background: #22c55e; } .dot.work { background: #3b82f6; } .dot.fail { background: #ef4444; }
  .control-panel { display: none; border: 2px solid #991b1b; background: #fff7ed; } .control-panel.open { display: block; }
  .control-help { margin: 0 0 8px; color: #7c2d12; font-size: 11px; line-height: 1.45; }
  textarea { width: 100%; min-height: 80px; resize: vertical; border: 1px solid #f97316; background: #fffbeb; padding: 8px; font-family: inherit; }
  .actions { display: flex; gap: 8px; margin-top: 7px; flex-wrap: wrap; } .actions button { padding: 8px 10px; } .send { background: #1f2937; color: white; } .resume { background: #22c55e; color: #052e16; } .launch { background: #dbeafe; color: #1e3a8a; }
  .tooltip { position: fixed; z-index: 50; pointer-events: none; opacity: 0; max-width: 310px; background: #fafafa; color: #1f2937; padding: 10px 12px; border: 4px solid #1f2937; box-shadow: 0 8px 0 rgba(0,0,0,.22); font-size: 12px; line-height: 1.45; transform: translate(-50%,-105%); }
  .tooltip.show { opacity: 1; } .tooltip h4 { margin: 0 0 6px; font-size: 13px; } .tooltip dl { margin: 0; display: grid; grid-template-columns: 70px 1fr; gap: 3px 8px; } .tooltip dt { color: #64748b; font-weight: 800; } .tooltip dd { margin: 0; }
  .issue { color: #b91c1c; font-weight: 900; margin-top: 5px; } .concern { color: #92400e; font-weight: 800; margin-top: 5px; }
  @media (max-width: 980px) { main { grid-template-columns: 1fr; } .stage { height: 620px; } }
</style>
</head>
<body>
<div class="app">
  <header>
    <h1>🐹 Agent Office <span class="pill" id="pid">파이프라인: -</span></h1>
    <div class="meta">
      <div class="token"><strong>RESET TOKEN</strong><span><b id="remain">-</b> / <b id="budget">-</b></span><div class="track"><div class="fill" id="fill"></div></div></div>
      <button class="stop" id="stop">STOP</button>
    </div>
  </header>
  <main>
    <section class="office"><h2>사무실 <small id="current">현재 단계: -</small></h2><div class="stage" id="stage"></div></section>
    <aside>
      <section class="panel control-panel" id="control"><h3>Pause <small>PM 추가 지시 대기</small></h3><div class="control"><p class="control-help">Pause 중에는 에이전트가 새 판단을 하지 않습니다. 지시를 적고 저장하거나, 저장 후 재개하면 명령 큐와 이벤트 로그에 남습니다.</p><textarea id="pmText" placeholder="PM에게 전달할 추가 지시를 적어주세요."></textarea><div class="actions"><button class="send" id="send">지시 저장</button><button class="resume" id="resumeWithInstruction">지시 저장 후 재개</button><button class="resume" id="resume">그냥 재개</button><button class="launch" id="launch">Claude 실행</button></div></div></section>
      <section class="panel"><h3>토큰 <small id="lastTs">로그 대기</small></h3><div class="tokens" id="tokens"></div></section>
      <section class="panel"><h3>단계</h3><div class="phases" id="phases"></div></section>
      <section class="panel"><h3>이벤트</h3><div class="events" id="events"></div></section>
    </aside>
  </main>
</div>
<div class="tooltip" id="tip"></div>
<script nonce="${nonce}">
const vscode = acquireVsCodeApi();
const stage = document.getElementById("stage");
const nodes = {};
let payload = null;
const fmt = n => Number(n || 0).toLocaleString();
function pct(pos) { return { left: (pos.x / 12) * 100, top: (pos.y / 12) * 100 }; }
function lighten(hex, amt) { const c=hex.replace("#",""); const n=parseInt(c,16); let r=(n>>16)+amt,g=((n>>8)&255)+amt,b=(n&255)+amt; r=Math.max(0,Math.min(255,r)); g=Math.max(0,Math.min(255,g)); b=Math.max(0,Math.min(255,b)); return "#"+((1<<24)+(r<<16)+(g<<8)+b).toString(16).slice(1); }
function face(id) { if(id==="pm-agent")return '<rect x="9" y="2" width="14" height="2" fill="#fbbf24"/><rect x="9" y="0" width="2" height="2" fill="#fbbf24"/><rect x="15" y="0" width="2" height="2" fill="#fbbf24"/><rect x="21" y="0" width="2" height="2" fill="#fbbf24"/>'; if(id==="dev-agent")return '<rect x="11" y="8" width="4" height="4" fill="none" stroke="#111827"/><rect x="17" y="8" width="4" height="4" fill="none" stroke="#111827"/>'; if(id==="ui-app-agent")return '<rect x="9" y="3" width="12" height="2" fill="#be185d"/><rect x="20" y="2" width="2" height="1" fill="#10b981"/>'; if(id==="qa-agent")return '<rect x="17" y="8" width="4" height="4" fill="none" stroke="#0369a1"/><rect x="21" y="12" width="2" height="2" fill="#0369a1"/>'; if(id==="security-agent")return '<rect x="9" y="8" width="14" height="3" fill="#111827"/>'; if(id==="build-agent")return '<rect x="10" y="2" width="12" height="3" fill="#f59e0b"/><rect x="9" y="5" width="14" height="1" fill="#92400e"/>'; if(id==="test-harness-agent")return '<rect x="9" y="5" width="14" height="2" fill="#7c3aed"/><rect x="9" y="6" width="14" height="1" fill="#fbbf24"/>'; if(id==="prompt-architect-agent")return '<rect x="7" y="3" width="18" height="1" fill="#1f2937"/><rect x="11" y="2" width="10" height="1" fill="#1f2937"/>'; if(id==="agent-factory-agent")return '<rect x="8" y="3" width="16" height="2" fill="#fafafa"/><rect x="11" y="9" width="3" height="2" fill="#bae6fd"/><rect x="18" y="9" width="3" height="2" fill="#bae6fd"/>'; if(id==="protocol-evolution-agent")return '<rect x="13" y="0" width="1" height="3" fill="#84cc16"/><rect x="18" y="0" width="1" height="3" fill="#84cc16"/>'; if(id==="power-automate-agent")return '<rect x="9" y="8" width="14" height="3" fill="#c084fc" opacity=".65"/>'; return ""; }
function tool(id) { if(id==="dev-agent")return '<rect x="10" y="19" width="12" height="6" fill="#1f2937"/><rect x="11" y="20" width="10" height="4" fill="#22d3ee"/>'; if(id==="pm-agent")return '<rect x="11" y="19" width="10" height="7" fill="#fafafa"/><rect x="13" y="21" width="6" height="1" fill="#22c55e"/>'; if(id==="qa-agent")return '<rect x="11" y="19" width="10" height="7" fill="#fafafa"/><rect x="13" y="21" width="6" height="1" fill="#22c55e"/>'; if(id==="security-agent")return '<rect x="11" y="20" width="10" height="4" fill="#dc2626"/><rect x="14" y="24" width="4" height="1" fill="#dc2626"/>'; if(id==="build-agent")return '<rect x="10" y="24" width="10" height="2" fill="#92400e"/><rect x="17" y="19" width="4" height="3" fill="#9ca3af"/>'; if(id==="test-harness-agent")return '<rect x="12" y="19" width="8" height="6" fill="#fafafa"/><rect x="12" y="19" width="8" height="1" fill="#5b21b6"/>'; if(id==="prompt-architect-agent")return '<rect x="11" y="19" width="10" height="7" fill="#1e3a8a"/><rect x="13" y="21" width="6" height="1" fill="#dbeafe"/>'; return '<rect x="12" y="21" width="8" height="4" fill="#fafafa"/>'; }
function hamster(color,id){ const belly=lighten(color,22), dark=lighten(color,-18), outline=lighten(color,-32); return \`<svg viewBox="0 0 32 32" width="100%" height="100%" shape-rendering="crispEdges"><rect x="9" y="30" width="14" height="2" fill="rgba(0,0,0,.25)"/><g class="body"><rect x="6" y="4" width="3" height="3" fill="\${color}"/><rect x="23" y="4" width="3" height="3" fill="\${color}"/><rect x="7" y="5" width="1" height="2" fill="#fde2e4"/><rect x="24" y="5" width="1" height="2" fill="#fde2e4"/><rect x="9" y="4" width="14" height="14" fill="\${color}"/><rect x="8" y="6" width="1" height="10" fill="\${outline}"/><rect x="23" y="6" width="1" height="10" fill="\${outline}"/><rect x="12" y="12" width="8" height="5" fill="\${belly}"/><rect x="12" y="9" width="2" height="2" fill="#111827"/><rect x="18" y="9" width="2" height="2" fill="#111827"/><rect x="12" y="9" width="1" height="1" fill="#fff"/><rect x="18" y="9" width="1" height="1" fill="#fff"/><rect x="15" y="13" width="2" height="1" fill="#111827"/><rect x="14" y="15" width="4" height="1" fill="#111827"/><rect x="15" y="16" width="1" height="2" fill="#fff"/><rect x="16" y="16" width="1" height="2" fill="#fff"/>\${face(id)}</g><rect x="9" y="18" width="14" height="8" fill="\${color}"/><rect x="12" y="20" width="8" height="5" fill="\${belly}"/><rect x="11" y="26" width="3" height="3" fill="\${dark}"/><rect x="18" y="26" width="3" height="3" fill="\${dark}"/><g>\${tool(id)}</g></svg>\`; }
function esc(s){ return String(s ?? "").replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch])); }
function ensure(ag){
  if(nodes[ag.id]) return;
  const d=document.createElement("div");
  d.className="desk";
  let p=pct(ag.desk);
  d.style.left=p.left+"%";
  d.style.top=p.top+"%";
  stage.appendChild(d);
  const n=document.createElement("div");
  n.className="agent";
  n.innerHTML=hamster(ag.color, ag.id)+'<span class="name">'+esc(ag.name)+'</span>';
  n.onmousemove=e=>tip(e, ag.id);
  n.onmouseleave=()=>document.getElementById("tip").classList.remove("show");
  stage.appendChild(n);
  nodes[ag.id]=n;
}
function tip(e,id){
  const ag=(payload?.agents||[]).find(a=>a.id===id);
  if(!ag)return;
  const t=document.getElementById("tip");
  const status={working:"작업중",coordinating:"전달중",paused:"Pause",idle:"대기",pending:"예정",standby:"대기",skipped:"스킵",alert:"문제"}[ag.status]||ag.status;
  t.innerHTML='<h4>'+esc(ag.name)+'</h4><dl><dt>상태</dt><dd>'+status+'</dd><dt>업무</dt><dd>'+esc(ag.summary)+'</dd><dt>현재</dt><dd>'+esc(ag.goal)+'</dd><dt>토큰</dt><dd>'+fmt(ag.tokens_used)+' 사용</dd></dl><div class="concern">현재 어려운 판단: '+esc(ag.hard_part||"없음")+'</div>'+(ag.issue?'<div class="issue">! '+esc(ag.issue)+'</div>':'');
  t.style.left=e.clientX+"px";
  t.style.top=(e.clientY-16)+"px";
  t.classList.add("show");
}
function render(data){
  payload=data;
  const paused=!!data.control.paused;
  document.getElementById("pid").textContent="파이프라인: "+(data.pipeline.id||"-");
  const cur=data.pipeline.phases[data.pipeline.current_phase_index];
  document.getElementById("current").textContent="현재 단계: "+(cur?cur.name:"완료");
  const tok=data.tokens;
  document.getElementById("remain").textContent=fmt(tok.remaining_tokens);
  document.getElementById("budget").textContent=fmt(tok.budget_tokens);
  document.getElementById("fill").style.width=Math.max(0,Math.min(100,tok.remaining_percent||100))+"%";
  document.getElementById("stop").classList.toggle("paused", paused);
  document.getElementById("stop").textContent=paused?"RESUME":"PAUSE";
  document.getElementById("control").classList.toggle("open", paused);
  if(data.control.pm_instruction && !document.getElementById("pmText").value) document.getElementById("pmText").value=data.control.pm_instruction;
  for(const ag of data.agents){
    ensure(ag);
    const p=pct(ag.position);
    const n=nodes[ag.id];
    n.style.left=p.left+"%";
    n.style.top=p.top+"%";
    n.className="agent "+(ag.status||"standby");
  }
  const max=Math.max(1,...data.agents.map(a=>a.tokens_used||0));
  document.getElementById("tokens").innerHTML=data.agents.map(a=>'<div class="mini-token"><span>'+esc(a.name)+'</span><i><span style="width:'+Math.round(((a.tokens_used||0)/max)*100)+'%"></span></i><b>'+fmt(a.tokens_used)+'</b></div>').join("");
  document.getElementById("lastTs").textContent=tok.last_ts?tok.last_ts.slice(11,16):"로그 대기";
  document.getElementById("phases").innerHTML=data.pipeline.phases.map((p,i)=>{
    const done=["DONE","PASS","SAFE","COMPLETE"].includes(p.status);
    const fail=["FAIL","BLOCK","ERROR"].includes(p.status);
    const work=i===data.pipeline.current_phase_index && !done && !fail && !paused;
    return '<div class="phase"><span class="dot '+(done?'done':fail?'fail':work?'work':'')+'">'+(done?'✓':fail?'!':work?'▶':'·')+'</span><span>'+esc(p.name)+'</span><b>'+esc(paused && work?"PAUSED":p.status)+'</b></div>';
  }).join("");
  document.getElementById("events").innerHTML=(data.pipeline.events||[]).slice().reverse().map(e=>'<div>'+esc((e.timestamp||"").slice(11,16))+" "+esc(e.message)+'</div>').join("");
}
document.getElementById("stop").onclick=()=>vscode.postMessage({type:"pause"});
document.getElementById("send").onclick=()=>vscode.postMessage({type:"saveInstruction", message:document.getElementById("pmText").value});
document.getElementById("resumeWithInstruction").onclick=()=>vscode.postMessage({type:"resumeWithInstruction", message:document.getElementById("pmText").value});
document.getElementById("resume").onclick=()=>vscode.postMessage({type:"resume"});
document.getElementById("launch").onclick=()=>vscode.postMessage({type:"launchClaude"});
window.addEventListener("message", e=>{ if(e.data?.type==="state") render(e.data.payload); });
vscode.postMessage({type:"ready"});
</script>
</body>
</html>`;
}

function activate(context) {
  const provider = new AgentOfficeViewProvider(context);
  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider("agentOffice.dashboard", provider),
    vscode.commands.registerCommand("agentOffice.refresh", sendState)
  );
}

function deactivate() {
  for (const watcher of watchers) watcher.dispose();
  watchers = [];
}

module.exports = { activate, deactivate };
