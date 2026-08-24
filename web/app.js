const state = {
  busy: false,
  scenarios: [],
  activeScenarioId: null,
};

const els = {
  welcome: document.getElementById("welcome"),
  startButton: document.getElementById("startButton"),
  newCaseButton: document.getElementById("newCaseButton"),
  scenarioList: document.getElementById("scenarioList"),
  messages: document.getElementById("messages"),
  composer: document.getElementById("composer"),
  input: document.getElementById("messageInput"),
  sendButton: document.getElementById("sendButton"),
  trace: document.getElementById("traceContent"),
  clearTrace: document.getElementById("clearTrace"),
  caseStatus: document.getElementById("caseStatus"),
  pipeline: document.querySelector(".pipeline"),
};

const agentNodes = [...document.querySelectorAll(".agent-node")];

function setStatus(kind, text) {
  els.caseStatus.className = `status-chip ${kind}`;
  els.caseStatus.lastChild.textContent = ` ${text}`;
}

function resetPipeline() {
  els.pipeline.classList.remove("active");
  agentNodes.forEach((node) => {
    node.className = "agent-node";
    node.querySelector(".agent-state").textContent = "Waiting";
  });
}

function setBusy(value) {
  state.busy = value;
  els.sendButton.disabled = value;
  els.input.disabled = value;
  if (value) {
    els.pipeline.classList.add("active");
    setStatus("running", "Crew running");
    agentNodes.forEach((node) => {
      node.className = "agent-node not-started";
      node.querySelector(".agent-state").textContent = "Awaiting trace";
    });
  }
}

function setActiveScenario(id) {
  state.activeScenarioId = id;
  [...els.scenarioList.querySelectorAll(".scenario-button")].forEach((button) => {
    button.classList.toggle("active", button.dataset.scenarioId === id);
  });
}

function clearConversation() {
  els.messages.innerHTML = "";
  const empty = document.createElement("div");
  empty.className = "empty-state";
  empty.id = "emptyState";
  empty.innerHTML = `
    <div class="empty-orbit"><span>GC</span></div>
    <h3>Start with a scenario or write your own case.</h3>
    <p>Every message launches a fresh Stage 2 crew execution and visualizes what each specialist actually did.</p>`;
  els.messages.appendChild(empty);
  els.trace.innerHTML = `<div class="trace-placeholder"><div class="trace-placeholder-icon">⌁</div><strong>No run yet</strong><span>Tool calls and handoffs will appear here.</span></div>`;
  resetPipeline();
  setStatus("idle", "Ready");
  setActiveScenario(null);
  els.input.value = "";
  els.input.focus();
}

function removeEmptyState() {
  const empty = document.getElementById("emptyState");
  if (empty) empty.remove();
}

function appendMessage(role, text, meta = "") {
  removeEmptyState();
  const row = document.createElement("div");
  row.className = `message-row ${role}`;

  if (role === "assistant") {
    const avatar = document.createElement("div");
    avatar.className = "avatar";
    avatar.textContent = "GC";
    row.appendChild(avatar);
  }

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text;
  if (meta) {
    const metaNode = document.createElement("div");
    metaNode.className = "message-meta";
    metaNode.textContent = meta;
    bubble.appendChild(metaNode);
  }
  row.appendChild(bubble);
  els.messages.appendChild(row);
  els.messages.scrollTop = els.messages.scrollHeight;
  return row;
}

function showTyping() {
  removeEmptyState();
  const fragment = document.getElementById("typingTemplate").content.cloneNode(true);
  const node = fragment.querySelector(".typing-row");
  node.dataset.typing = "true";
  els.messages.appendChild(fragment);
  els.messages.scrollTop = els.messages.scrollHeight;
}

function hideTyping() {
  const typing = els.messages.querySelector('[data-typing="true"]');
  if (typing) typing.remove();
}

function pretty(value) {
  return JSON.stringify(value, null, 2);
}

function makeMetric(label, value, tone = "") {
  const box = document.createElement("div");
  box.className = "metric";
  const span = document.createElement("span");
  span.textContent = label;
  const strong = document.createElement("strong");
  strong.textContent = value ?? "—";
  if (tone) strong.classList.add(tone);
  box.append(span, strong);
  return box;
}

function renderRuntimeSummary(data) {
  const summary = document.createElement("section");
  summary.className = "runtime-summary";
  const fields = [
    ["Case status", data.status.replaceAll("_", " ")],
    ["Escalation", data.communication?.escalation_required ? "Required" : "No"],
    ["Trusted stop", data.stop_reason || "Normal completion"],
  ];
  fields.forEach(([label, value]) => {
    const item = document.createElement("div");
    item.className = "summary-item";
    const span = document.createElement("span");
    span.textContent = label;
    const strong = document.createElement("strong");
    strong.textContent = value;
    item.append(span, strong);
    summary.appendChild(item);
  });
  return summary;
}

function renderRuntimeStop(data) {
  if (!data.stop_reason) return null;
  const descriptions = {
    ORDER_ID_REQUIRED: "No customer-grounded order identifier was supplied. The deterministic boundary stopped execution before any LLM or tool call could invent one.",
    ORDER_NOT_FOUND: "The trusted order lookup returned ORDER_NOT_FOUND, so the crew stopped instead of guessing another order.",
    USER_ORDER_MISMATCH: "The trusted fraud audit detected an order/user mismatch. Decision authority was never reached.",
  };
  const card = document.createElement("section");
  card.className = "runtime-stop-card";
  const label = document.createElement("span");
  label.className = "stop-label";
  label.textContent = "Deterministic runtime boundary";
  const strong = document.createElement("strong");
  strong.textContent = data.stop_reason;
  const copy = document.createElement("p");
  copy.textContent = descriptions[data.stop_reason] || "The runtime stopped this case at a trusted safety or completion boundary.";
  card.append(label, strong, copy);
  return card;
}

function renderHandoffs(data) {
  const fragment = document.createDocumentFragment();

  if (data.risk_report) {
    const card = document.createElement("section");
    card.className = "handoff-card";
    const label = document.createElement("p");
    label.className = "section-label";
    label.textContent = "RESEARCHER → DECISION · RISKREPORT";
    const grid = document.createElement("div");
    grid.className = "metric-grid";
    const high = data.risk_report.risk_band === "high";
    grid.append(
      makeMetric("Risk score", data.risk_report.risk_score, high ? "danger" : "success"),
      makeMetric("Risk band", data.risk_report.risk_band, high ? "danger" : "success"),
      makeMetric("Blocks refund", String(data.risk_report.blocks_automatic_refund), high ? "danger" : "success"),
      makeMetric("Rules fired", data.risk_report.triggered_rules?.length ?? 0)
    );
    card.append(label, grid);
    fragment.appendChild(card);
  }

  if (data.decision) {
    const card = document.createElement("section");
    card.className = "handoff-card";
    const label = document.createElement("p");
    label.className = "section-label";
    label.textContent = "DECISION → COMMS · DECISIONHANDOFF";
    const grid = document.createElement("div");
    grid.className = "metric-grid";
    const approved = data.decision.refund_status === "APPROVED";
    grid.append(
      makeMetric("Policy", data.decision.policy_verdict),
      makeMetric("Refund", data.decision.refund_status, approved ? "success" : "danger"),
      makeMetric("Requested", `$${Number(data.decision.requested_amount).toFixed(2)}`),
      makeMetric("Approved", `$${Number(data.decision.approved_amount).toFixed(2)}`, approved ? "success" : "")
    );
    card.append(label, grid);
    fragment.appendChild(card);
  }

  return fragment;
}

function renderAgent(agent) {
  const card = document.createElement("section");
  card.className = "trace-agent";
  const head = document.createElement("div");
  head.className = "trace-agent-head";
  const title = document.createElement("div");
  const strong = document.createElement("strong");
  strong.textContent = agent.label;
  const small = document.createElement("small");
  small.textContent = `${agent.steps.length} observable tool event${agent.steps.length === 1 ? "" : "s"}`;
  title.append(strong, small);
  const status = document.createElement("span");
  status.className = `trace-agent-status ${agent.status}`;
  status.textContent = agent.status;
  head.append(title, status);
  card.appendChild(head);

  if (!agent.steps.length) {
    const empty = document.createElement("div");
    empty.className = "tool-step";
    empty.textContent = agent.status === "skipped" ? "Not reached by the trusted crew flow." : "No tool calls recorded.";
    card.appendChild(empty);
    return card;
  }

  agent.steps.forEach((step, index) => {
    const wrapper = document.createElement("div");
    wrapper.className = "tool-step";
    const top = document.createElement("div");
    top.className = "tool-title";
    const count = document.createElement("span");
    count.className = "tool-index";
    count.textContent = String(index + 1).padStart(2, "0");
    const name = document.createElement("strong");
    name.textContent = step.tool;
    const outcome = document.createElement("span");
    outcome.className = `outcome ${step.outcome.toLowerCase()}`;
    outcome.textContent = step.outcome;
    top.append(count, name, outcome);
    wrapper.appendChild(top);

    if (step.guardrail_reason) {
      const guard = document.createElement("div");
      guard.className = "guardrail-note";
      guard.textContent = `Guardrail: ${step.guardrail_reason}`;
      wrapper.appendChild(guard);
    }

    const details = document.createElement("details");
    const summary = document.createElement("summary");
    summary.textContent = "Inspect inputs / trusted result";
    const pre = document.createElement("pre");
    pre.textContent = pretty({ arguments: step.arguments, result: step.result });
    details.append(summary, pre);
    wrapper.appendChild(details);
    card.appendChild(wrapper);
  });

  return card;
}

function renderExecution(data) {
  els.trace.innerHTML = "";
  els.trace.appendChild(renderRuntimeSummary(data));
  const stop = renderRuntimeStop(data);
  if (stop) els.trace.appendChild(stop);
  els.trace.appendChild(renderHandoffs(data));
  data.agents.forEach((agent) => els.trace.appendChild(renderAgent(agent)));

  agentNodes.forEach((node) => {
    const role = node.dataset.agent;
    const agent = data.agents.find((item) => item.role === role);
    const status = agent?.status ?? "skipped";
    node.className = `agent-node ${status === "skipped" ? "not-started" : status}`;
    const stateNode = node.querySelector(".agent-state");
    stateNode.textContent = status === "completed" ? "Done" : status === "failed" ? "Failed" : "Not run";
  });

  els.pipeline.classList.remove("active");
  if (data.status === "COMPLETED") setStatus("success", "Completed");
  else if (data.status === "ESCALATED" || data.status === "NEEDS_CLARIFICATION") setStatus("escalated", data.status.replaceAll("_", " "));
  else setStatus("failed", data.status.replaceAll("_", " "));
}

async function runCase(message) {
  if (state.busy || !message.trim()) return;
  appendMessage("user", message.trim(), "Customer case");
  showTyping();
  setBusy(true);

  try {
    const response = await fetch("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: message.trim() }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Crew request failed");
    hideTyping();
    appendMessage("assistant", data.customer_response, data.status.replaceAll("_", " "));
    renderExecution(data);
  } catch (error) {
    hideTyping();
    appendMessage("assistant", `Runtime error: ${error.message}`, "FAILED");
    resetPipeline();
    setStatus("failed", "Runtime error");
  } finally {
    state.busy = false;
    els.sendButton.disabled = false;
    els.input.disabled = false;
    els.input.focus();
  }
}

function renderScenarios() {
  els.scenarioList.innerHTML = "";
  state.scenarios.forEach((scenario) => {
    const button = document.createElement("button");
    button.className = "scenario-button";
    button.dataset.tone = scenario.tone;
    button.dataset.scenarioId = scenario.id;
    const title = document.createElement("strong");
    title.textContent = scenario.title;
    const subtitle = document.createElement("span");
    subtitle.textContent = scenario.subtitle;
    button.append(title, subtitle);
    button.addEventListener("click", () => {
      setActiveScenario(scenario.id);
      els.input.value = scenario.message;
      runCase(scenario.message);
    });
    els.scenarioList.appendChild(button);
  });
}

async function loadScenarios() {
  try {
    const response = await fetch("/api/scenarios");
    const data = await response.json();
    state.scenarios = data.scenarios || [];
    renderScenarios();
  } catch {
    state.scenarios = [];
  }
}

els.startButton.addEventListener("click", () => {
  els.welcome.classList.add("hidden");
  window.setTimeout(() => els.input.focus(), 360);
});
els.newCaseButton.addEventListener("click", clearConversation);
els.clearTrace.addEventListener("click", () => {
  els.trace.innerHTML = `<div class="trace-placeholder"><div class="trace-placeholder-icon">⌁</div><strong>Trace cleared</strong><span>Run another case to inspect the crew.</span></div>`;
  resetPipeline();
});
els.composer.addEventListener("submit", (event) => {
  event.preventDefault();
  const message = els.input.value;
  els.input.value = "";
  setActiveScenario(null);
  runCase(message);
});
els.input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    els.composer.requestSubmit();
  }
});
els.input.addEventListener("input", () => {
  els.input.style.height = "auto";
  els.input.style.height = `${Math.min(130, els.input.scrollHeight)}px`;
});

resetPipeline();
loadScenarios();
