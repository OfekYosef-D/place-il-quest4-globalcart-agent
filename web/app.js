const state = {
  busy: false,
  scenarios: [],
  activeScenarioId: null,
  sessionId: null,
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
    setStatus("running", "Understanding request");
    agentNodes.forEach((node) => {
      node.className = "agent-node not-started";
      node.querySelector(".agent-state").textContent = "Not started";
    });
  }
}

function setActiveScenario(id) {
  state.activeScenarioId = id;
  [...els.scenarioList.querySelectorAll(".scenario-button")].forEach((button) => {
    button.classList.toggle("active", button.dataset.scenarioId === id);
  });
}

async function createConversationSession() {
  const response = await fetch("/api/session", { method: "POST" });
  const data = await response.json();
  if (!response.ok || !data.session_id) throw new Error("Could not create conversation session");
  state.sessionId = data.session_id;
}

function resetConversationView() {
  els.messages.innerHTML = "";
  const empty = document.createElement("div");
  empty.className = "empty-state";
  empty.id = "emptyState";
  empty.innerHTML = `
    <div class="empty-orbit"><span>GC</span></div>
    <h3>Start with a scenario or write your own message.</h3>
    <p>The intake layer understands the conversation first. Only a grounded support case can launch the specialist crew.</p>`;
  els.messages.appendChild(empty);
  els.trace.innerHTML = `<div class="trace-placeholder"><div class="trace-placeholder-icon">⌁</div><strong>No run yet</strong><span>Intake, tool calls and trusted handoffs will appear here.</span></div>`;
  resetPipeline();
  setStatus("idle", "Ready");
  setActiveScenario(null);
  els.input.value = "";
}

async function startNewConversation() {
  if (state.busy) return;
  await createConversationSession();
  resetConversationView();
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
    ["Intake", data.intake?.assessment?.intent || (data.intake?.failure ? "FAILED" : "—")],
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

function renderIntake(data) {
  if (!data.intake) return null;
  const card = document.createElement("section");
  card.className = "handoff-card intake-card";
  const label = document.createElement("p");
  label.className = "section-label";
  label.textContent = "CONVERSATION INTAKE · NO TOOLS / NO BUSINESS AUTHORITY";
  card.appendChild(label);

  if (data.intake.failure) {
    const failure = document.createElement("div");
    failure.className = "guardrail-note";
    failure.textContent = `Intake failed closed: ${data.intake.failure}`;
    card.appendChild(failure);
    return card;
  }

  const assessment = data.intake.assessment;
  if (!assessment) return card;
  const grid = document.createElement("div");
  grid.className = "metric-grid";
  grid.append(
    makeMetric("Intent", assessment.intent),
    makeMetric("Goal", assessment.support_goal),
    makeMetric("Case reason", assessment.case_reason),
    makeMetric("Tools exposed", "No", "success")
  );
  card.appendChild(grid);
  if (assessment.issue_summary) {
    const summary = document.createElement("p");
    summary.className = "intake-summary";
    summary.textContent = assessment.issue_summary;
    card.appendChild(summary);
  }
  return card;
}

function renderRuntimeStop(data) {
  if (!data.stop_reason) return null;
  const descriptions = {
    INTAKE_GREETING: "The intake layer recognized a conversational greeting. No specialist agent or business tool was started.",
    ORDER_ID_REQUIRED: "The intake layer recognized a support case, but deterministic grounding found no customer-supplied order id. The specialist crew was not started.",
    MULTIPLE_ORDER_IDS: "More than one order id was grounded in the current request. The runtime refused to choose one on the customer's behalf.",
    MULTIPLE_USER_IDS: "More than one customer id was grounded. The runtime refused to guess which identity should control the case.",
    MULTIPLE_REQUESTED_AMOUNTS: "More than one refund amount was grounded. The runtime requires one unambiguous amount before financial action.",
    RETURN_REASON_REQUIRED: "The order was grounded, but the reason could not be safely resolved from customer evidence or trusted order data.",
    ORDER_NOT_FOUND: "The trusted order lookup returned ORDER_NOT_FOUND, so the crew stopped instead of guessing another order.",
    USER_ORDER_MISMATCH: "The trusted fraud audit detected an order/user mismatch. Decision authority was never reached.",
    ORDER_STATUS_RESOLVED: "This was a status-only intent. The Researcher used only the order lookup; fraud, policy and refund agents were not needed.",
  };
  const card = document.createElement("section");
  card.className = "runtime-stop-card";
  const label = document.createElement("span");
  label.className = "stop-label";
  label.textContent = "Trusted runtime boundary";
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
  const intake = renderIntake(data);
  if (intake) els.trace.appendChild(intake);
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

  if (data.status === "COMPLETED") setStatus("success", "Completed");
  else if (data.status === "ESCALATED" || data.status === "NEEDS_CLARIFICATION") setStatus("escalated", data.status.replaceAll("_", " "));
  else setStatus("failed", data.status.replaceAll("_", " "));
}

async function runCase(message) {
  if (state.busy || !message.trim()) return;
  if (!state.sessionId) await createConversationSession();
  appendMessage("user", message.trim(), "Customer message");
  showTyping();
  setBusy(true);

  try {
    const response = await fetch("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: message.trim(), session_id: state.sessionId }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Crew request failed");
    state.sessionId = data.session_id || state.sessionId;
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
    button.addEventListener("click", async () => {
      if (state.busy) return;
      await createConversationSession();
      resetConversationView();
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

els.startButton.addEventListener("click", async () => {
  els.welcome.classList.add("hidden");
  if (!state.sessionId) await createConversationSession();
  window.setTimeout(() => els.input.focus(), 360);
});
els.newCaseButton.addEventListener("click", startNewConversation);
els.clearTrace.addEventListener("click", () => {
  els.trace.innerHTML = `<div class="trace-placeholder"><div class="trace-placeholder-icon">⌁</div><strong>Trace cleared</strong><span>Send another message to inspect the next turn.</span></div>`;
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
