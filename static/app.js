const queryInput = document.getElementById("query");
const addForm = document.getElementById("add-form");
const addButton = document.getElementById("add-button");
const suggestButton = document.getElementById("suggest-button");
const refreshButton = document.getElementById("refresh-button");
const formError = document.getElementById("form-error");
const nameSearchEl = document.getElementById("name-search");
const summary = document.getElementById("summary");
const suggestionsEl = document.getElementById("suggestions");
const emptyEl = document.getElementById("empty");
const listEl = document.getElementById("list");
const bootEl = document.getElementById("boot");
const refreshNote = document.getElementById("refresh-note");

let latestRooms = [];
let suggestions = [];
let suggestionsOpen = false;
let suggestionsError = "";
let suggestionsLoading = false;
let nameSearchOpen = false;
let nameSearchLoading = false;
let nameSearchError = "";
let nameSearchNote = "";
let nameSearchRooms = [];
let nameSearchQuery = "";
let polling = false;
let renderKey = "";

function formatNumber(value) {
  return new Intl.NumberFormat("zh-CN").format(value);
}

function clock(timestamp) {
  if (!timestamp) return "";
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(timestamp * 1000));
}

function setFormError(message) {
  if (!message) {
    formError.hidden = true;
    formError.textContent = "";
    return;
  }
  formError.hidden = false;
  formError.textContent = message;
}

async function api(path, options) {
  let response;
  try {
    response = await fetch(path, options);
  } catch {
    throw new Error("连不上本地服务");
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.error || "请求失败");
  }
  return data;
}

function letter(room) {
  const span = document.createElement("span");
  span.textContent = (room.nickname || room.display_id || room.query || "?").slice(0, 1);
  return span;
}

function avatarUrls(room) {
  const listed = Array.isArray(room.avatar_urls) ? room.avatar_urls.filter(Boolean) : [];
  if (listed.length) return listed.slice(0, 3);
  return room.avatar ? [room.avatar] : [];
}

function avatarNode(room) {
  const wrap = document.createElement("div");
  wrap.className = "avatar";
  wrap.append(letter(room));
  const urls = avatarUrls(room);
  if (!urls.length) return wrap;
  const img = document.createElement("img");
  img.alt = "";
  const params = new URLSearchParams();
  urls.forEach((url) => params.append("url", url));
  img.src = "/api/avatar?" + params.toString();
  img.addEventListener("error", () => img.remove());
  wrap.append(img);
  return wrap;
}

function sparkline(history) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 128 40");
  svg.setAttribute("class", "spark");
  svg.setAttribute("aria-hidden", "true");
  if (!history || history.length < 2) {
    svg.classList.add("is-empty");
    const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
    line.setAttribute("x1", "4");
    line.setAttribute("x2", "124");
    line.setAttribute("y1", "28");
    line.setAttribute("y2", "28");
    line.setAttribute("stroke", "#6e7882");
    line.setAttribute("stroke-dasharray", "3 4");
    svg.append(line);
    return svg;
  }
  const counts = history.map((point) => point.count);
  const min = Math.min(...counts);
  const max = Math.max(...counts);
  const span = Math.max(1, max - min);
  const points = history.map((point, index) => {
    const x = (index / (history.length - 1)) * 128;
    const y = 36 - ((point.count - min) / span) * 32;
    return x.toFixed(1) + "," + y.toFixed(1);
  });
  const poly = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
  poly.setAttribute("points", points.join(" "));
  svg.append(poly);
  return svg;
}

function deltaOf(history) {
  if (!history || history.length < 2) return null;
  const diff = history[history.length - 1].count - history[history.length - 2].count;
  if (diff === 0) return { text: "较上次持平", kind: "flat" };
  if (diff > 0) return { text: "较上次 +" + formatNumber(diff), kind: "up" };
  return { text: "较上次 " + formatNumber(diff), kind: "down" };
}

function renderSummary(rooms) {
  if (!rooms.length) {
    summary.hidden = true;
    summary.replaceChildren();
    return;
  }
  const live = rooms.filter((room) => room.live);
  const counts = live.map((room) => room.viewer_count).filter((value) => typeof value === "number");
  summary.hidden = false;
  summary.replaceChildren();
  const bits = [
    ["开播", String(live.length)],
    ["未开播", String(rooms.length - live.length)],
    ["在线合计", counts.length ? formatNumber(counts.reduce((sum, value) => sum + value, 0)) : "—"],
  ];
  bits.forEach(([label, value]) => {
    const span = document.createElement("span");
    span.append(label + " ");
    const strong = document.createElement("strong");
    strong.textContent = value;
    span.append(strong);
    summary.append(span);
  });
}

function renderEmpty(interval) {
  emptyEl.hidden = false;
  emptyEl.replaceChildren();
  const title = document.createElement("h2");
  title.textContent = "还没有要盯的直播间";
  const lead = document.createElement("p");
  lead.className = "hint";
  lead.textContent = "输入主播昵称，或粘贴直播间链接、房间号、抖音号。昵称会在当前公开直播信息流里查找，同名会列出来让你选。添加成功后立刻显示真实人数，之后大约每 " + interval + " 秒更新一次。";
  const steps = document.createElement("ol");
  steps.className = "steps";
  [
    ["1", "输入主播昵称", "含中文的名字会按昵称查找。只能找到这次拉到的正在直播的房间，可能漏人。"],
    ["2", "或粘贴链接、抖音号", "直播间链接、一长串房间号、纯英文数字抖音号仍会直接添加。"],
    ["3", "看人数变化", "开播后这里出现当前在线人数。本次打开期间会留下一小段走势。"],
  ].forEach(([index, heading, body]) => {
    const item = document.createElement("li");
    const em = document.createElement("em");
    em.textContent = index + "  " + heading;
    const paragraph = document.createElement("p");
    paragraph.textContent = body;
    item.append(em, paragraph);
    steps.append(item);
  });
  const formats = document.createElement("div");
  formats.className = "formats";
  ["主播昵称", "live.douyin.com/抖音号", "房间号或短链"].forEach((sample) => {
    const code = document.createElement("code");
    code.textContent = sample;
    formats.append(code);
  });
  const actions = document.createElement("div");
  actions.className = "empty-actions";
  const button = document.createElement("button");
  button.type = "button";
  button.dataset.primary = "true";
  button.textContent = "看看正在热播";
  button.addEventListener("click", () => loadSuggestions(false));
  actions.append(button);
  emptyEl.append(title, lead, steps, formats, actions);
}

function renderCard(room, index) {
  const card = document.createElement("article");
  card.className = "card" + (room.live ? " is-live" : "");

  const top = document.createElement("div");
  top.className = "card-top";
  const who = document.createElement("div");
  who.className = "who";
  const nameRow = document.createElement("div");
  nameRow.className = "name-row";
  const name = document.createElement("h2");
  name.textContent = room.nickname || room.display_id || room.query || "未命名直播间";
  const badge = document.createElement("span");
  badge.className = "badge" + (room.live ? " live" : "");
  if (room.live) {
    const dot = document.createElement("span");
    dot.className = "dot";
    badge.append(dot);
  }
  badge.append(room.live ? "开播" : "未开播");
  nameRow.append(name, badge);
  const meta = document.createElement("p");
  meta.className = "meta";
  const bits = [];
  if (room.display_id) bits.push("抖音号 " + room.display_id);
  else if (room.web_path) bits.push(room.web_path);
  bits.push(String(index + 1).padStart(2, "0"));
  meta.textContent = bits.join("  ·  ");
  who.append(nameRow, meta);
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "remove";
  remove.textContent = "移出";
  remove.addEventListener("click", () => removeRoom(room.id));
  top.append(avatarNode(room), who, remove);

  const title = document.createElement("p");
  title.className = "title";
  title.textContent = room.title || (room.live ? "直播间没有标题" : "当前没有进行中的直播");

  const metric = document.createElement("div");
  metric.className = "metric";
  const figures = document.createElement("div");
  const kicker = document.createElement("p");
  kicker.className = "kicker";
  kicker.textContent = room.live ? "当前在线" : "直播状态";
  const viewers = document.createElement("p");
  viewers.className = "viewers";
  if (room.live && typeof room.viewer_count === "number") {
    viewers.textContent = formatNumber(room.viewer_count) + (room.viewer_approximate ? "+" : "");
  } else if (room.live) {
    viewers.classList.add("quiet");
    viewers.textContent = "暂无人数";
  } else {
    viewers.classList.add("quiet");
    viewers.textContent = "未开播";
  }
  figures.append(kicker, viewers);
  const delta = deltaOf(room.history);
  if (room.live && delta) {
    const deltaLine = document.createElement("p");
    deltaLine.className = "delta " + delta.kind;
    deltaLine.textContent = delta.text;
    figures.append(deltaLine);
  } else if (room.live) {
    const deltaLine = document.createElement("p");
    deltaLine.className = "delta";
    deltaLine.textContent = "走势会在后续刷新里出现";
    figures.append(deltaLine);
  }
  if (room.live && typeof room.total_user === "number" && room.total_user > 0) {
    const total = document.createElement("p");
    total.className = "total";
    total.textContent = "本场累计 " + formatNumber(room.total_user);
    figures.append(total);
  }
  metric.append(figures, sparkline(room.history));

  const foot = document.createElement("div");
  foot.className = "card-foot";
  const updated = document.createElement("p");
  updated.textContent = room.updated_at ? clock(room.updated_at) + " 更新" : "等待第一次刷新";
  foot.append(updated);
  if (room.link) {
    const link = document.createElement("a");
    link.href = room.link;
    link.target = "_blank";
    link.rel = "noreferrer";
    link.textContent = "打开直播间";
    foot.append(link);
  }

  card.append(top, title, metric, foot);
  if (room.error) {
    const warn = document.createElement("p");
    warn.className = "warn";
    warn.textContent = room.nickname ? "暂时没刷新成功，仍显示上次结果：" + room.error : room.error;
    card.append(warn);
  }
  return card;
}

function renderList(rooms) {
  listEl.replaceChildren();
  rooms.forEach((room, index) => listEl.append(renderCard(room, index)));
}

function alreadyWatching(suggestion) {
  return latestRooms.some((room) => {
    if (suggestion.room_id && room.room_id === suggestion.room_id) return true;
    const display = (suggestion.display_id || "").toLowerCase();
    if (!display) return false;
    return [room.display_id, room.web_path].some((value) => (value || "").toLowerCase() === display);
  });
}

function renderSuggestions() {
  if (!suggestionsOpen) {
    suggestionsEl.hidden = true;
    return;
  }
  suggestionsEl.hidden = false;
  suggestionsEl.replaceChildren();
  const header = document.createElement("header");
  const title = document.createElement("h2");
  title.textContent = "正在热播";
  const close = document.createElement("button");
  close.type = "button";
  close.className = "ghost";
  close.textContent = "收起";
  close.addEventListener("click", () => {
    suggestionsOpen = false;
    renderSuggestions();
  });
  header.append(title, close);
  suggestionsEl.append(header);
  const note = document.createElement("p");
  note.className = "suggest-note";
  if (suggestionsLoading) {
    note.textContent = "正在读取公开热播列表…";
    suggestionsEl.append(note);
    return;
  }
  if (suggestionsError) {
    note.textContent = suggestionsError + "。不影响你手动添加直播间。";
    suggestionsEl.append(note);
    return;
  }
  if (!suggestions.length) {
    note.textContent = "这次没有拿到正在直播的房间。可以直接粘贴链接或抖音号。";
    suggestionsEl.append(note);
    return;
  }
  note.textContent = "来自抖音公开直播信息流，不是完整榜单。点添加后会再查一次这个房间的实时人数。";
  const list = document.createElement("div");
  list.className = "suggest-list";
  suggestions.forEach((suggestion) => {
    const row = document.createElement("div");
    row.className = "suggest-row";
    const who = document.createElement("div");
    who.className = "who";
    const name = document.createElement("strong");
    name.textContent = suggestion.nickname || suggestion.display_id || "未命名";
    const meta = document.createElement("p");
    meta.className = "meta";
    const count = typeof suggestion.viewer_count === "number" ? formatNumber(suggestion.viewer_count) + " 人在看" : "人数未知";
    meta.textContent = [suggestion.title || "无标题", suggestion.display_id ? "抖音号 " + suggestion.display_id : ""].filter(Boolean).join("  ·  ");
    who.append(name, meta);
    const countEl = document.createElement("div");
    countEl.className = "suggest-count";
    countEl.textContent = count;
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.roomId = suggestion.room_id || "";
    button.dataset.displayId = suggestion.display_id || "";
    const taken = alreadyWatching(suggestion);
    button.disabled = taken;
    button.textContent = taken ? "已在列表" : "添加";
    button.addEventListener("click", () => addSuggestion(suggestion, button));
    row.append(avatarNode(suggestion), who, countEl, button);
    list.append(row);
  });
  suggestionsEl.append(note, list);
}

function syncSuggestionButtons() {
  document.querySelectorAll(".suggest-row button[data-room-id]").forEach((button) => {
    const taken = latestRooms.some((room) => {
      if (button.dataset.roomId && room.room_id === button.dataset.roomId) return true;
      const display = (button.dataset.displayId || "").toLowerCase();
      if (!display) return false;
      return [room.display_id, room.web_path].some((value) => (value || "").toLowerCase() === display);
    });
    if (button.dataset.busy === "1") return;
    button.disabled = taken;
    button.textContent = taken ? "已在列表" : "添加";
  });
}

function render(state) {
  bootEl.hidden = true;
  latestRooms = state.rooms || [];
  const interval = state.interval_seconds || 10;
  refreshNote.textContent = state.refreshing
    ? "正在向抖音查询…"
    : "大约每 " + interval + " 秒自动刷新" + (state.last_refresh_at ? " · 最近 " + clock(state.last_refresh_at) : "");
  const key = JSON.stringify({
    rooms: latestRooms,
    refreshing: state.refreshing,
    last: state.last_refresh_at,
    interval,
  });
  if (key === renderKey) {
    syncSuggestionButtons();
    return;
  }
  renderKey = key;
  renderSummary(latestRooms);
  if (!latestRooms.length) renderEmpty(interval);
  else emptyEl.hidden = true;
  renderList(latestRooms);
  syncSuggestionButtons();
}

async function tick() {
  if (polling) return;
  polling = true;
  try {
    const state = await api("/api/state");
    render(state);
  } catch (error) {
    bootEl.hidden = true;
    setFormError(error.message);
  } finally {
    polling = false;
  }
}

async function addQuery(query) {
  addButton.disabled = true;
  addButton.textContent = "正在查询…";
  setFormError("");
  try {
    await api("/api/rooms", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query }),
    });
    queryInput.value = "";
    await tick();
  } catch (error) {
    setFormError(error.message);
  } finally {
    addButton.disabled = false;
    refreshAddLabel();
  }
}

async function addSuggestion(suggestion, button) {
  button.dataset.busy = "1";
  button.disabled = true;
  button.textContent = "添加中";
  try {
    await addQuery(suggestion.room_id || suggestion.display_id || suggestion.web_path);
  } finally {
    delete button.dataset.busy;
    const taken = alreadyWatching(suggestion);
    button.disabled = taken;
    button.textContent = taken ? "已在列表" : "添加";
  }
}

async function removeRoom(id) {
  try {
    await api("/api/rooms/" + encodeURIComponent(id), { method: "DELETE" });
    await tick();
  } catch (error) {
    setFormError(error.message);
  }
}

async function loadSuggestions(fresh) {
  suggestionsOpen = true;
  suggestionsLoading = true;
  suggestionsError = "";
  renderSuggestions();
  suggestionsEl.scrollIntoView({ behavior: "smooth", block: "nearest" });
  try {
    const data = await api("/api/suggestions" + (fresh ? "?fresh=1" : ""));
    suggestions = data.rooms || [];
    suggestionsError = data.error || "";
  } catch (error) {
    suggestions = [];
    suggestionsError = error.message;
  } finally {
    suggestionsLoading = false;
    renderSuggestions();
  }
}

function isNameQuery(text) {
  const value = (text || "").trim().replace(/^@/, "");
  if (!value) return false;
  if (/^[a-z][a-z0-9+.-]*:\/\//i.test(value)) return false;
  if (/douyin\.com|amemv\.com/i.test(value)) return false;
  return !/^[A-Za-z0-9._-]{1,80}$/.test(value);
}

function refreshAddLabel() {
  if (addButton.disabled) return;
  addButton.textContent = isNameQuery(queryInput.value) ? "查找" : "添加";
}

function renderNameSearch() {
  if (!nameSearchOpen) {
    nameSearchEl.hidden = true;
    return;
  }
  nameSearchEl.hidden = false;
  nameSearchEl.replaceChildren();
  const header = document.createElement("header");
  const title = document.createElement("h2");
  title.textContent = nameSearchQuery ? "「" + nameSearchQuery + "」的匹配" : "按昵称查找";
  const close = document.createElement("button");
  close.type = "button";
  close.className = "ghost";
  close.textContent = "收起";
  close.addEventListener("click", () => {
    nameSearchOpen = false;
    renderNameSearch();
  });
  header.append(title, close);
  nameSearchEl.append(header);
  const note = document.createElement("p");
  note.className = "suggest-note";
  if (nameSearchLoading) {
    note.textContent = "正在读取公开直播信息流，并按昵称查找…";
    nameSearchEl.append(note);
    return;
  }
  if (nameSearchError) {
    note.textContent = nameSearchError;
    nameSearchEl.append(note);
    return;
  }
  note.textContent = nameSearchNote || "没有更多说明。";
  if (!nameSearchRooms.length) {
    const empty = document.createElement("p");
    empty.className = "suggest-note";
    empty.textContent = "这批公开直播里没有昵称包含「" + nameSearchQuery + "」的房间。对方可能没开播，或不在这次拉到的热门信息流里。可以改用直播间链接或抖音号。";
    nameSearchEl.append(note, empty);
    return;
  }
  const list = document.createElement("div");
  list.className = "suggest-list";
  nameSearchRooms.forEach((match) => {
    const row = document.createElement("div");
    row.className = "suggest-row";
    const who = document.createElement("div");
    who.className = "who";
    const name = document.createElement("strong");
    name.textContent = match.nickname || match.display_id || "未命名";
    const meta = document.createElement("p");
    meta.className = "meta";
    const bits = [];
    if (match.title) bits.push(match.title);
    if (match.display_id) bits.push("抖音号 " + match.display_id);
    if (!match.live) bits.push("开播状态未在信息流里确认");
    meta.textContent = bits.join("  ·  ");
    who.append(name, meta);
    const countEl = document.createElement("div");
    countEl.className = "suggest-count";
    if (typeof match.viewer_count === "number") {
      countEl.textContent = formatNumber(match.viewer_count) + (match.viewer_approximate ? "+" : "") + " 人在看";
    } else {
      countEl.textContent = "人数未知";
    }
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.roomId = match.room_id || "";
    button.dataset.displayId = match.display_id || "";
    const taken = alreadyWatching(match);
    button.disabled = taken;
    button.textContent = taken ? "已在列表" : "添加";
    button.addEventListener("click", () => addSuggestion(match, button));
    row.append(avatarNode(match), who, countEl, button);
    list.append(row);
  });
  nameSearchEl.append(note, list);
}

async function loadNameSearch(query) {
  nameSearchOpen = true;
  nameSearchLoading = true;
  nameSearchError = "";
  nameSearchNote = "";
  nameSearchRooms = [];
  nameSearchQuery = query;
  renderNameSearch();
  nameSearchEl.scrollIntoView({ behavior: "smooth", block: "nearest" });
  addButton.disabled = true;
  addButton.textContent = "正在查找…";
  setFormError("");
  try {
    const data = await api("/api/search?q=" + encodeURIComponent(query));
    nameSearchRooms = data.rooms || [];
    nameSearchNote = data.note || "";
    nameSearchError = data.error || "";
    nameSearchQuery = data.query || query;
  } catch (error) {
    nameSearchRooms = [];
    nameSearchError = error.message;
  } finally {
    nameSearchLoading = false;
    addButton.disabled = false;
    refreshAddLabel();
    renderNameSearch();
  }
}

addForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const query = queryInput.value.trim();
  if (!query) {
    setFormError("请输入主播昵称，或粘贴直播间链接、房间号、抖音号");
    queryInput.focus();
    return;
  }
  if (isNameQuery(query)) {
    loadNameSearch(query);
    return;
  }
  addQuery(query);
});

queryInput.addEventListener("input", refreshAddLabel);

suggestButton.addEventListener("click", () => {
  if (suggestionsOpen && !suggestionsLoading) {
    suggestionsOpen = false;
    renderSuggestions();
    return;
  }
  loadSuggestions(suggestions.length > 0);
});

refreshButton.addEventListener("click", async () => {
  refreshButton.disabled = true;
  const previous = refreshButton.textContent;
  refreshButton.textContent = "刷新中";
  try {
    const state = await api("/api/refresh", { method: "POST" });
    render(state);
  } catch (error) {
    setFormError(error.message);
  } finally {
    refreshButton.disabled = false;
    refreshButton.textContent = previous;
  }
});

tick();
setInterval(tick, 2000);
