const state = {
  missions: [],
  roster: [],
  plugins: [],
  current: null,
  agent: "flash",
};

const $ = (id) => document.getElementById(id);

function formatTokens(n) {
  if (!n) return "";
  if (n >= 1_000_000) return `${n / 1_000_000}M ctx`;
  if (n >= 1_000) return `${Math.round(n / 1_000)}k ctx`;
  return `${n} tok`;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || response.statusText);
  return data;
}

function renderMissions() {
  const nav = $("mission-list");
  nav.innerHTML = "";
  for (const item of state.missions) {
    const button = document.createElement("button");
    const title = document.createElement("b");
    title.textContent = item.title;
    const meta = document.createElement("small");
    meta.textContent = `${item.status} · ${item.id}`;
    button.append(title, meta);
    if (state.current && state.current.id === item.id) button.classList.add("active");
    button.onclick = () => openMission(item.id);
    nav.appendChild(button);
  }
}

function renderOps() {
  const root = $("ops");
  root.innerHTML = "";
  for (const agent of state.roster) {
    const card = document.createElement("div");
    card.className = "op";
    card.dataset.agent = agent.name;
    const display = document.createElement("b");
    display.textContent = agent.display;
    const phase = document.createElement("span");
    phase.textContent = agent.ooda || agent.feature;
    const tokens = document.createElement("span");
    tokens.textContent = formatTokens(agent.context_tokens);
    card.append(display, phase, tokens);
    root.appendChild(card);
  }
}

function renderBoard() {
  $("board").textContent = state.current?.board || "Board is empty.";
  const link = $("feishu-link");
  if (state.current?.feishu_url) {
    link.hidden = false;
    link.href = state.current.feishu_url;
  } else {
    link.hidden = true;
  }
}

function renderCreator() {
  const tabs = $("agent-tabs");
  tabs.innerHTML = "";
  for (const agent of state.roster) {
    const button = document.createElement("button");
    button.textContent = agent.display;
    if (agent.name === state.agent) button.classList.add("active");
    button.onclick = () => {
      state.agent = agent.name;
      renderCreator();
      loadCell();
    };
    tabs.appendChild(button);
  }
  const selected = new Set(state.current?.cells?.[state.agent]?.plugins || []);
  const list = $("plugin-list");
  list.innerHTML = "";
  for (const plugin of state.plugins) {
    const row = document.createElement("label");
    row.className = "plugin";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = selected.has(plugin.id);
    input.dataset.id = plugin.id;
    const copy = document.createElement("div");
    const title = document.createElement("b");
    title.textContent = plugin.title;
    const summary = document.createElement("span");
    summary.textContent = plugin.summary;
    copy.append(title, summary);
    row.append(input, copy);
    input.onchange = savePlugins;
    list.appendChild(row);
  }
}

async function savePlugins() {
  if (!state.current) return;
  const plugins = [...document.querySelectorAll("#plugin-list input:checked")].map((el) => el.dataset.id);
  await api(`/api/missions/${state.current.id}/cells/${state.agent}/plugins`, {
    method: "POST",
    body: { plugins },
  });
  await openMission(state.current.id);
}

async function loadCell() {
  if (!state.current) {
    $("agents-md").textContent = "";
    return;
  }
  const cell = await api(`/api/missions/${state.current.id}/cells/${state.agent}`);
  $("agents-md").textContent = cell.agents_md;
}

async function refreshList() {
  const data = await api("/api/missions");
  state.missions = data.missions;
  renderMissions();
}

async function openMission(id) {
  state.current = await api(`/api/missions/${id}`);
  $("mission-title").textContent = state.current.title;
  $("mission-meta").textContent = `${state.current.status} · ${state.current.id}`;
  $("task").value = state.current.mission.trim();
  renderMissions();
  renderOps();
  renderBoard();
  renderCreator();
  await loadCell();
}

async function boot() {
  const [roster, plugins, doctor] = await Promise.all([
    api("/api/roster"),
    api("/api/plugins"),
    api("/api/doctor"),
  ]);
  state.roster = roster.agents;
  state.plugins = plugins.plugins;
  $("feishu-status").textContent = doctor.feishu.startsWith("credentials") ? "Feishu ready" : "Feishu unset";
  $("feishu-status").className = "pill " + (doctor.feishu.startsWith("credentials") ? "ok" : "bad");
  const blocked = doctor.roster.filter((item) => String(item.status).startsWith("blocked")).length;
  $("doctor-status").textContent = blocked ? `${blocked} blocked` : "roster ready";
  $("doctor-status").className = "pill " + (blocked ? "bad" : "ok");
  renderOps();
  renderCreator();
  await refreshList();
  if (state.missions[0]) await openMission(state.missions[0].id);
}

$("new-mission").onclick = () => $("create-dialog").showModal();

$("create-dialog").addEventListener("close", async () => {
  if ($("create-dialog").returnValue !== "ok") return;
  const form = $("create-form");
  const payload = {
    title: form.title.value,
    mission: form.mission.value,
    attachments: form.attachments.value,
    no_sync: form.no_sync.checked,
  };
  const created = await api("/api/missions", { method: "POST", body: payload });
  form.reset();
  await refreshList();
  await openMission(created.id);
});

$("composer").onsubmit = async (event) => {
  event.preventDefault();
  if (!state.current) return;
  await api(`/api/missions/${state.current.id}/dispatch`, {
    method: "POST",
    body: { task: $("task").value, mock: $("mock").checked, parallel: $("parallel").checked },
  });
  await openMission(state.current.id);
};

$("compose-form").onsubmit = async (event) => {
  event.preventDefault();
  if (!state.current) return;
  await api(`/api/missions/${state.current.id}/cells/${state.agent}/compose`, {
    method: "POST",
    body: { description: $("compose-text").value },
  });
  await openMission(state.current.id);
};

$("pull").onclick = async () => {
  if (!state.current) return;
  await api(`/api/missions/${state.current.id}/pull`, { method: "POST", body: {} });
  await openMission(state.current.id);
};
$("pause").onclick = async () => {
  if (!state.current) return;
  await api(`/api/missions/${state.current.id}/pause`, { method: "POST", body: {} });
  await openMission(state.current.id);
};
$("finish").onclick = async () => {
  if (!state.current) return;
  await api(`/api/missions/${state.current.id}/finish`, { method: "POST", body: {} });
  await openMission(state.current.id);
};

boot().catch((error) => {
  $("mission-meta").textContent = error.message;
});

setInterval(async () => {
  if (!state.current) return;
  try {
    const data = await api(`/api/missions/${state.current.id}/board`);
    if (data.board !== state.current.board) {
      state.current.board = data.board;
      renderBoard();
    }
  } catch {
    /* keep the last board on screen */
  }
}, 4000);
