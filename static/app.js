const queryInput = document.getElementById("query");
const addForm = document.getElementById("add-form");
const addButton = document.getElementById("add-button");
const suggestButton = document.getElementById("suggest-button");
const calendarButton = document.getElementById("calendar-button");
const calendarEl = document.getElementById("calendar");
const refreshButton = document.getElementById("refresh-button");
const formError = document.getElementById("form-error");
const nameSearchEl = document.getElementById("name-search");
const summary = document.getElementById("summary");
const suggestionsEl = document.getElementById("suggestions");
const emptyEl = document.getElementById("empty");
const stageEl = document.getElementById("stage");
const listEl = document.getElementById("list");
const detailEl = document.getElementById("detail");
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
let selectedId = "";
let pinSelection = false;
let calendarOpen = false;
let calendarCursor = new Date();
let calendarMonth = null;
let calendarDay = "";
let calendarDayView = null;
let calendarLoading = false;
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

function formatDuration(seconds) {
  const whole = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(whole / 3600);
  const minutes = Math.floor((whole % 3600) / 60);
  const remain = whole % 60;
  if (hours) return hours + "小时" + minutes + "分";
  if (minutes) return minutes + "分" + remain + "秒";
  return remain + "秒";
}

function renderRoster(rooms) {
  listEl.replaceChildren();
  rooms.forEach((room) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "roster-row" + (room.id === selectedId ? " is-selected" : "");
    button.setAttribute("aria-pressed", room.id === selectedId ? "true" : "false");
    const who = document.createElement("div");
    who.className = "who";
    const name = document.createElement("p");
    name.className = "roster-name";
    name.textContent = room.nickname || room.display_id || room.query || "未命名直播间";
    const meta = document.createElement("p");
    meta.className = "meta";
    meta.textContent = room.live ? "开播" : "未开播";
    who.append(name, meta);
    const count = document.createElement("div");
    count.className = "roster-count";
    if (room.live && typeof room.viewer_count === "number") {
      count.textContent = formatNumber(room.viewer_count);
    } else if (room.live) {
      count.textContent = "—";
    } else {
      count.textContent = "未开播";
    }
    button.append(avatarNode(room), who, count);
    button.addEventListener("click", () => {
      pinSelection = false;
      selectedId = room.id;
      renderRoster(latestRooms);
      renderDetail(latestRooms);
    });
    listEl.append(button);
  });
}

function chartTime(timestamp, withSeconds) {
  const options = { hour: "2-digit", minute: "2-digit", hour12: false };
  if (withSeconds) options.second = "2-digit";
  return new Intl.DateTimeFormat("zh-CN", options).format(new Date(timestamp * 1000));
}

function countLabel(point) {
  return formatNumber(point.count) + (point.approximate ? "+" : "");
}

function svgEl(name, attrs) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", name);
  Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, value));
  return node;
}

function detailChart(history) {
  const wrap = document.createElement("div");
  wrap.className = "trend-wrap";
  const svg = svgEl("svg", { viewBox: "0 0 640 300", class: "trend", role: "img" });
  const usable = (history || []).filter((point) => typeof point.count === "number" && typeof point.t === "number");
  const axisY = 214;
  svg.append(svgEl("line", { class: "axis", x1: "36", x2: "604", y1: String(axisY), y2: String(axisY) }));
  wrap.append(svg);
  if (usable.length < 2) {
    svg.setAttribute("aria-label", "采样点还不够，暂时画不出走势");
    return wrap;
  }
  const left = 36;
  const right = 604;
  const top = 28;
  const bottom = 198;
  const start = usable[0].t;
  const end = usable[usable.length - 1].t;
  const timeSpan = Math.max(end - start, 1);
  const counts = usable.map((point) => point.count);
  const min = Math.min(...counts);
  const max = Math.max(...counts);
  const countSpan = Math.max(1, max - min);
  const coords = usable.map((point, index) => ({
    index: index,
    t: point.t,
    count: point.count,
    approximate: Boolean(point.approximate),
    x: left + ((point.t - start) / timeSpan) * (right - left),
    y: bottom - ((point.count - min) / countSpan) * (bottom - top),
  }));
  svg.append(svgEl("polyline", { points: coords.map((point) => point.x.toFixed(1) + "," + point.y.toFixed(1)).join(" ") }));
  const layout = chartLayout;
  const countFont = 15;
  const countHeight = 18;
  let peak = coords.findIndex((point) => !point.approximate);
  if (peak < 0) peak = 0;
  coords.forEach((point, index) => {
    const betterExact = !point.approximate && (coords[peak].approximate || point.count >= coords[peak].count);
    const betterApprox = point.approximate && coords[peak].approximate && point.count >= coords[peak].count;
    if (betterExact || betterApprox) peak = index;
  });
  const maxCountWidth = Math.max.apply(null, coords.map((point) => layout.textWidth(countLabel(point), countFont)));
  const slot = maxCountWidth + 14;
  const innerLeft = left + maxCountWidth / 2;
  const innerRight = right - maxCountWidth / 2;
  const slotCount = Math.max(1, Math.floor((innerRight - innerLeft) / slot) + 1);
  const targets = [];
  for (let index = 0; index < slotCount; index += 1) {
    targets.push(slotCount === 1 ? (innerLeft + innerRight) / 2 : innerLeft + ((innerRight - innerLeft) * index) / (slotCount - 1));
  }
  const countCandidates = [];
  function addCountCandidate(point, priority) {
    const width = layout.textWidth(countLabel(point), countFont);
    let box = layout.boxFor(point.x, Math.max(16, point.y - 8), width, countHeight, "middle");
    if (box.left < 4) box = layout.boxFor(4 + width / 2, box.bottom, width, countHeight, "middle");
    if (box.right > 636) box = layout.boxFor(636 - width / 2, box.bottom, width, countHeight, "middle");
    countCandidates.push({ id: point.index, priority: priority, box: box, point: point });
  }
  addCountCandidate(coords[peak], 100);
  const used = new Set([peak]);
  targets.forEach((target) => {
    let best = null;
    let bestDistance = Infinity;
    coords.forEach((point) => {
      if (used.has(point.index)) return;
      const distance = Math.abs(point.x - target);
      if (distance < bestDistance) {
        best = point;
        bestDistance = distance;
      }
    });
    if (!best || bestDistance > slot) return;
    used.add(best.index);
    addCountCandidate(best, 60);
  });
  const visibleCounts = new Set(layout.chooseLabels(countCandidates));
  coords.forEach((point) => {
    const dot = svgEl("circle", { cx: point.x.toFixed(1), cy: point.y.toFixed(1), r: "3.5", class: "node", "data-index": String(point.index) });
    svg.append(dot);
    if (!visibleCounts.has(point.index)) return;
    const chosen = countCandidates.find((item) => item.id === point.index && visibleCounts.has(item.id));
    const label = svgEl("text", {
      x: ((chosen.box.left + chosen.box.right) / 2).toFixed(1),
      y: chosen.box.bottom.toFixed(1),
      "text-anchor": "middle",
      class: "count-label",
      "font-size": String(countFont),
      "data-priority": point.index === peak ? "100" : "60",
    });
    label.textContent = countLabel(point);
    svg.append(label);
  });
  const withSeconds = timeSpan < 600;
  const timeFont = 14;
  const tickCount = Math.max(2, Math.min(5, Math.floor((right - left) / (layout.textWidth(chartTime(start, withSeconds), timeFont) + 16)) + 1));
  const timeCandidates = [];
  for (let index = 0; index < tickCount; index += 1) {
    const tick = start + (timeSpan * index) / Math.max(1, tickCount - 1);
    const x = left + ((tick - start) / timeSpan) * (right - left);
    const anchor = index === 0 ? "start" : index === tickCount - 1 ? "end" : "middle";
    const text = chartTime(tick, withSeconds);
    const width = layout.textWidth(text, timeFont);
    timeCandidates.push({
      id: "t" + index,
      priority: index === 0 || index === tickCount - 1 ? 100 : 50,
      box: layout.boxFor(x, 262, width, 16, anchor),
      x: x,
      anchor: anchor,
      text: text,
    });
  }
  const visibleTimes = new Set(layout.chooseLabels(timeCandidates));
  timeCandidates.forEach((tick) => {
    if (!visibleTimes.has(tick.id)) return;
    svg.append(svgEl("line", { class: "tick", x1: tick.x.toFixed(1), x2: tick.x.toFixed(1), y1: String(axisY), y2: String(axisY + 6) }));
    const timeLabel = svgEl("text", {
      x: tick.x.toFixed(1),
      y: "262",
      "text-anchor": tick.anchor,
      class: "time-label",
      "font-size": String(timeFont),
      "data-priority": String(tick.priority),
    });
    timeLabel.textContent = tick.text;
    svg.append(timeLabel);
  });
  const crosshair = svgEl("line", { class: "crosshair", x1: "0", x2: "0", y1: String(top), y2: String(axisY), visibility: "hidden" });
  const active = svgEl("circle", { class: "node is-active", cx: "0", cy: "0", r: "5.5", visibility: "hidden" });
  svg.append(crosshair, active);
  const tip = document.createElement("div");
  tip.className = "trend-tip";
  tip.hidden = true;
  wrap.append(tip);
  function hideTip() {
    tip.hidden = true;
    crosshair.setAttribute("visibility", "hidden");
    active.setAttribute("visibility", "hidden");
  }
  svg.addEventListener("pointermove", (event) => {
    const rect = svg.getBoundingClientRect();
    if (!rect.width) return;
    const viewX = ((event.clientX - rect.left) / rect.width) * 640;
    if (viewX < left - 8 || viewX > right + 8) {
      hideTip();
      return;
    }
    const nearest = layout.nearestByX(coords, viewX);
    crosshair.setAttribute("visibility", "visible");
    crosshair.setAttribute("x1", nearest.x.toFixed(1));
    crosshair.setAttribute("x2", nearest.x.toFixed(1));
    active.setAttribute("visibility", "visible");
    active.setAttribute("cx", nearest.x.toFixed(1));
    active.setAttribute("cy", nearest.y.toFixed(1));
    tip.replaceChildren();
    const kicker = document.createElement("p");
    kicker.className = "tip-kicker";
    kicker.textContent = "最近一次记录";
    const when = document.createElement("p");
    when.textContent = chartTime(nearest.t, true);
    const value = document.createElement("p");
    value.className = "tip-count";
    value.textContent = countLabel(nearest) + " 人";
    tip.append(kicker, when, value);
    if (nearest.approximate) {
      const note = document.createElement("p");
      note.className = "tip-note";
      note.textContent = "这是约数，不是精确整数。没有在两次采样之间估人数。";
      tip.append(note);
    }
    tip.hidden = false;
    const wrapRect = wrap.getBoundingClientRect();
    const localX = event.clientX - wrapRect.left + 14;
    const localY = event.clientY - wrapRect.top - 12;
    tip.style.left = Math.min(Math.max(8, localX), wrapRect.width - 150) + "px";
    tip.style.top = Math.max(8, localY) + "px";
  });
  svg.addEventListener("pointerleave", hideTip);
  svg.setAttribute("aria-label", "本机采样的在线人数，从 " + formatNumber(min) + " 到 " + formatNumber(max) + "。鼠标移到图上会显示最近一次真实采样。");
  requestAnimationFrame(function dropOverlaps() {
    if (!svg.isConnected) {
      requestAnimationFrame(dropOverlaps);
      return;
    }
    const labels = [...svg.querySelectorAll(".count-label, .time-label")];
    const items = labels.map((el) => ({ el: el, box: el.getBBox(), priority: Number(el.dataset.priority || 0) }));
    items.sort((a, b) => b.priority - a.priority);
    const kept = [];
    items.forEach((item) => {
      if (kept.some((other) => layout.intersects(item.box, other.box, 2))) {
        item.el.remove();
        return;
      }
      kept.push(item);
    });
  });
  return wrap;
}

function metricTile(title, value, note, unavailable) {
  const tile = document.createElement("article");
  tile.className = "metric-tile" + (unavailable ? " is-unavailable" : "");
  const heading = document.createElement("h3");
  heading.textContent = title;
  const number = document.createElement("p");
  number.className = "metric-value";
  number.textContent = value;
  tile.append(heading, number);
  if (note) {
    const paragraph = document.createElement("p");
    paragraph.className = "metric-note";
    paragraph.textContent = note;
    tile.append(paragraph);
  }
  return tile;
}

function renderDetail(rooms) {
  const room = rooms.find((item) => item.id === selectedId);
  if (!room) {
    detailEl.hidden = true;
    stageEl.classList.remove("is-open");
    detailEl.replaceChildren();
    return;
  }
  detailEl.hidden = false;
  stageEl.classList.add("is-open");
  const detail = room.detail || {};
  detailEl.replaceChildren();

  const head = document.createElement("div");
  head.className = "detail-head";
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
  if (room.title) bits.push(room.title);
  meta.textContent = bits.join("  ·  ");
  who.append(nameRow, meta);
  const actions = document.createElement("div");
  actions.className = "detail-actions";
  const back = document.createElement("button");
  back.type = "button";
  back.className = "ghost back-list";
  back.textContent = "返回列表";
  back.addEventListener("click", () => {
    pinSelection = true;
    selectedId = "";
    renderList(latestRooms);
  });
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "remove";
  remove.textContent = "移出";
  remove.addEventListener("click", () => removeRoom(room.id));
  actions.append(back, remove);
  head.append(avatarNode(room), who, actions);

  const kicker = document.createElement("p");
  kicker.className = "kicker";
  kicker.textContent = room.live ? "当前在线" : "直播状态";
  const viewers = document.createElement("p");
  viewers.className = "hero-count" + (room.live && typeof room.viewer_count === "number" ? "" : " quiet");
  if (room.live && typeof room.viewer_count === "number") {
    viewers.textContent = formatNumber(room.viewer_count) + (room.viewer_approximate ? "+" : "");
  } else if (room.live) {
    viewers.textContent = "暂无人数";
  } else {
    viewers.textContent = "未开播";
  }

  const grid = document.createElement("div");
  grid.className = "metric-grid";
  grid.append(
    metricTile(
      "今日新增粉丝",
      "公开数据里没有这项",
      detail.today_followers_reason || "没有自然日的新增粉丝。",
      true
    )
  );
  if (typeof detail.session_follows === "number") {
    grid.append(metricTile("本场新增关注", formatNumber(detail.session_follows), "来自公开房间数据里的 follow_count，只计这一场开播之后，不是今天全天。", false));
  } else {
    grid.append(metricTile("本场新增关注", "这次没拿到", "房间接口没有返回 follow_count。", true));
  }
  if (typeof detail.live_seconds === "number") {
    const started = detail.started_at ? clock(detail.started_at) + " 开播" : "";
    grid.append(metricTile("直播时间", formatDuration(detail.live_seconds), started ? "按公开房间的 start_time 算到现在。" + started + "。" : "按公开房间的 start_time 算到现在。", false));
  } else {
    grid.append(metricTile("直播时间", room.live ? "没有开播时间" : "当前没有进行中的直播", room.live ? "这次的房间数据里没有可用的 start_time。" : "未开播时不估算上一场时长。", true));
  }
  if (typeof detail.like_count === "number") {
    grid.append(metricTile("点赞数量", formatNumber(detail.like_count), "来自公开房间上的 like_count。同一接口里 stats.like_count 一直是 0，没有采用。", false));
  } else {
    grid.append(metricTile("点赞数量", "这次没拿到", "房间数据里没有 like_count。", true));
  }
  if (typeof detail.observed_peak === "number") {
    grid.append(metricTile("峰值在线人数", formatNumber(detail.observed_peak), "公开接口里的 max_audience_cnt 在未登录时一直是 0，不能当成峰值。这里是本机刷新到的整数在线人数里的最大值，程序没开着的时段不会算进去。", false));
  } else {
    grid.append(metricTile("峰值在线人数", "还没有整数采样", "要有一次成功刷新到的整数在线人数后才会显示。不把接口里的 0 当成峰值。", true));
  }
  if (typeof detail.session_viewers === "number") {
    grid.append(metricTile("场观人数", formatNumber(detail.session_viewers), detail.session_viewers_note || "来自公开的「人看过」整数。", false));
  } else {
    grid.append(metricTile("场观人数", "这次没有「人看过」", "只有文案写明人看过、而且给出整数时才显示。不用另一个对不上的 total_user 来填。", true));
  }
  grid.append(metricTile("平均在线人数", "公开数据里没有这项", detail.average_online_reason || "没有整场平均在线。", true));
  grid.append(metricTile("人均停留时间", "公开数据里没有这项", detail.average_stay_reason || "没有人均停留。", true));

  const viewersBlock = document.createElement("section");
  viewersBlock.className = "segment-block";
  const viewersTitle = document.createElement("h3");
  viewersTitle.textContent = "直播时间段的人数";
  const viewersNote = document.createElement("p");
  viewersNote.className = "metric-note";
  viewersNote.textContent = detail.viewer_segments_note || "";
  const segments = Array.isArray(detail.viewer_segments) ? detail.viewer_segments : room.history || [];
  viewersBlock.append(viewersTitle, viewersNote, detailChart(segments));
  if (segments.length >= 2) {
    const span = document.createElement("p");
    span.className = "metric-note";
    span.textContent = clock(segments[0].t) + " – " + clock(segments[segments.length - 1].t) + "，共 " + segments.length + " 个采样。固定数字只标在隔得开的时间上。鼠标在图里移动，显示离指针最近的一次真实采样，不会在两点之间估人数。";
    viewersBlock.append(span);
  }

  const giftBlock = document.createElement("section");
  giftBlock.className = "segment-block";
  const giftTitle = document.createElement("h3");
  giftTitle.textContent = "时间段礼物数量";
  const giftNote = document.createElement("p");
  giftNote.className = "metric-note";
  giftNote.textContent = detail.gift_reason || "没有可用的礼物分段。";
  giftBlock.append(giftTitle, giftNote);

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
  detailEl.append(head, kicker, viewers, grid, viewersBlock, giftBlock, foot);
  if (room.error) {
    const warn = document.createElement("p");
    warn.className = "warn";
    warn.textContent = room.error;
    detailEl.append(warn);
  }
}

function renderList(rooms) {
  if (!rooms.length) {
    stageEl.hidden = true;
    detailEl.hidden = true;
    stageEl.classList.remove("is-open");
    listEl.replaceChildren();
    return;
  }
  stageEl.hidden = false;
  if (!rooms.some((room) => room.id === selectedId)) {
    selectedId = pinSelection ? "" : rooms[0].id;
  }
  renderRoster(rooms);
  renderDetail(rooms);
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
  if (!latestRooms.length) {
    selectedId = "";
    pinSelection = false;
    renderEmpty(interval);
  } else emptyEl.hidden = true;
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

function monthKey(date) {
  return date.getFullYear() + "-" + String(date.getMonth() + 1).padStart(2, "0");
}

function dayKey(date) {
  return monthKey(date) + "-" + String(date.getDate()).padStart(2, "0");
}

function renderCalendar() {
  if (!calendarOpen) {
    calendarEl.hidden = true;
    return;
  }
  calendarEl.hidden = false;
  calendarEl.replaceChildren();
  const header = document.createElement("header");
  const title = document.createElement("h2");
  title.textContent = "每日记录";
  const close = document.createElement("button");
  close.type = "button";
  close.className = "ghost";
  close.textContent = "收起";
  close.addEventListener("click", () => {
    calendarOpen = false;
    renderCalendar();
  });
  header.append(title, close);
  const nav = document.createElement("div");
  nav.className = "calendar-nav";
  const previous = document.createElement("button");
  previous.type = "button";
  previous.className = "ghost";
  previous.textContent = "上个月";
  previous.addEventListener("click", () => shiftMonth(-1));
  const label = document.createElement("strong");
  label.textContent = calendarCursor.getFullYear() + "年" + (calendarCursor.getMonth() + 1) + "月";
  const next = document.createElement("button");
  next.type = "button";
  next.className = "ghost";
  next.textContent = "下个月";
  next.addEventListener("click", () => shiftMonth(1));
  nav.append(previous, label, next);
  const note = document.createElement("p");
  note.className = "suggest-note";
  note.textContent = calendarLoading
    ? "正在读取本机日历…"
    : (calendarMonth && calendarMonth.note) || "按这台电脑的本地日期查看。没有刷新过的日子是空的。";
  calendarEl.append(header, nav, note, buildMonthGrid(), buildDayPanel());
}

function buildMonthGrid() {
  const grid = document.createElement("div");
  grid.className = "calendar-grid";
  ["一", "二", "三", "四", "五", "六", "日"].forEach((name) => {
    const cell = document.createElement("span");
    cell.className = "weekday";
    cell.textContent = name;
    grid.append(cell);
  });
  const year = calendarCursor.getFullYear();
  const month = calendarCursor.getMonth();
  const first = new Date(year, month, 1);
  const start = new Date(first);
  start.setDate(1 - ((first.getDay() + 6) % 7));
  const today = dayKey(new Date());
  const marked = (calendarMonth && calendarMonth.days) || {};
  for (let index = 0; index < 42; index += 1) {
    const cell = new Date(start);
    cell.setDate(start.getDate() + index);
    const key = dayKey(cell);
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = String(cell.getDate());
    if (cell.getMonth() !== month) button.disabled = true;
    if (marked[key]) button.classList.add("has-data");
    if (key === today) button.classList.add("is-today");
    if (key === calendarDay) button.classList.add("is-selected");
    button.addEventListener("click", () => selectDay(key));
    grid.append(button);
  }
  return grid;
}

function buildDayPanel() {
  const panel = document.createElement("div");
  panel.className = "day-panel";
  if (!calendarDay) {
    const empty = document.createElement("p");
    empty.className = "suggest-note";
    empty.textContent = "点一天查看记录。";
    panel.append(empty);
    return panel;
  }
  const heading = document.createElement("h3");
  heading.textContent = calendarDay;
  panel.append(heading);
  if (!calendarDayView) {
    const waiting = document.createElement("p");
    waiting.className = "suggest-note";
    waiting.textContent = "正在读取这一天…";
    panel.append(waiting);
    return panel;
  }
  if (calendarDayView.empty || !(calendarDayView.rooms || []).length) {
    const empty = document.createElement("p");
    empty.className = "suggest-note";
    empty.textContent = "这一天没有记录。程序只在打开并刷新到直播间时写入，关掉之后的时段不会补上。";
    panel.append(empty);
    return panel;
  }
  const intro = document.createElement("p");
  intro.className = "suggest-note";
  intro.textContent = calendarDayView.note || "";
  panel.append(intro);
  calendarDayView.rooms.forEach((room) => panel.append(renderDayRoom(room)));
  return panel;
}

function renderDayRoom(room) {
  const block = document.createElement("article");
  block.className = "day-room";
  const title = document.createElement("h3");
  title.textContent = room.nickname || room.display_id || room.room_id || "未命名直播间";
  const meta = document.createElement("p");
  meta.className = "meta";
  meta.textContent = room.display_id ? "抖音号 " + room.display_id : "";
  const grid = document.createElement("div");
  grid.className = "metric-grid";
  if (typeof room.peak_viewers === "number") {
    grid.append(metricTile("峰值在线人数", formatNumber(room.peak_viewers), "这一天采样到的最高整数在线人数，不是抖音公布的整场峰值。", false));
  } else {
    grid.append(metricTile("峰值在线人数", "没有整数在线人数", "这一天的记录里没有 viewer 整数。", true));
  }
  if (typeof room.likes === "number") {
    grid.append(metricTile("点赞数量", formatNumber(room.likes), "这一天最后一次读到的房间累计点赞，不是当天新增加的赞。", false));
  } else {
    grid.append(metricTile("点赞数量", "没有读到", "这一天没有 like_count。", true));
  }
  if (typeof room.watched === "number") {
    grid.append(metricTile("场观人数", formatNumber(room.watched), "这一天最后一次读到的「人看过」。", false));
  } else {
    grid.append(metricTile("场观人数", "没有读到", "这一天没有「人看过」整数。", true));
  }
  if (typeof room.follows === "number") {
    grid.append(metricTile("本场新增关注", formatNumber(room.follows), "这一天最后一次读到的本场新增关注，不是自然日新增粉丝。", false));
  } else {
    grid.append(metricTile("本场新增关注", "没有读到", "这一天没有 follow_count。", true));
  }
  grid.append(metricTile("今日新增粉丝", "没有这项", room.today_followers_reason || "", true));
  if (typeof room.recorded_seconds === "number") {
    grid.append(metricTile("记录时长", formatDuration(room.recorded_seconds), "从这一天第一条记录到最后一条，不是整场直播时长。", false));
  } else {
    grid.append(metricTile("记录时长", "只有一个点", "至少两次记录才能算出这段时间。", true));
  }
  grid.append(metricTile("平均在线人数", "没有这项", room.average_online_reason || "", true));
  grid.append(metricTile("人均停留时间", "没有这项", room.average_stay_reason || "", true));
  grid.append(metricTile("礼物", "没有这项", room.gift_reason || "", true));
  const chartTitle = document.createElement("h3");
  chartTitle.textContent = "这一天的在线人数";
  const chart = detailChart(room.viewer_segments || []);
  block.append(title, meta, grid, chartTitle, chart);
  return block;
}

async function shiftMonth(offset) {
  calendarCursor = new Date(calendarCursor.getFullYear(), calendarCursor.getMonth() + offset, 1);
  calendarDay = "";
  calendarDayView = null;
  await loadCalendarMonth();
}

async function selectDay(day) {
  calendarDay = day;
  calendarDayView = null;
  renderCalendar();
  try {
    calendarDayView = await api("/api/history?day=" + encodeURIComponent(day));
  } catch (error) {
    calendarDayView = { empty: true, note: error.message, rooms: [] };
  }
  renderCalendar();
}

async function loadCalendarMonth() {
  calendarOpen = true;
  calendarLoading = true;
  renderCalendar();
  calendarEl.scrollIntoView({ behavior: "smooth", block: "nearest" });
  try {
    calendarMonth = await api("/api/history?month=" + monthKey(calendarCursor));
  } catch (error) {
    calendarMonth = { days: {}, note: error.message };
  } finally {
    calendarLoading = false;
    renderCalendar();
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

calendarButton.addEventListener("click", () => {
  if (calendarOpen) {
    calendarOpen = false;
    renderCalendar();
    return;
  }
  if (!calendarDay) calendarDay = dayKey(new Date());
  loadCalendarMonth().then(() => selectDay(calendarDay));
});

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
