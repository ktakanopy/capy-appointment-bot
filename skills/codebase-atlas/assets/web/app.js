"use strict";

const { project, flows, catalogs, sources, sourceFiles } = JSON.parse(document.querySelector("#atlas-data").textContent);
const $ = (id) => document.getElementById(id);
const svgNamespace = "http://www.w3.org/2000/svg";
const icons = {
  atlas: '<circle cx="5" cy="5" r="2"/><circle cx="17" cy="6" r="2"/><circle cx="12" cy="17" r="2"/><path d="m7 5 8 1m-9 1 5 8m5-7-3 7"/>',
  search: '<circle cx="9.5" cy="9.5" r="5.5"/><path d="m14 14 4 4"/>',
  file: '<path d="M6 3h7l4 4v12H6zM13 3v5h4M9 12h5m-5 3h5"/>',
  book: '<path d="M4 5c3-1 5-1 7 1 2-2 4-2 7-1v12c-3-1-5-1-7 1-2-2-4-2-7-1zm7 1v12"/>',
  panel: '<rect x="3" y="4" width="16" height="14" rx="2"/><path d="M13 4v14"/>',
  runtime: '<path d="M3 10.5 19 4l-4 14-4.5-5L3 10.5zm7.5 2.5L19 4"/>',
  conversation: '<path d="M4 4h14v10h-7l-5 4v-4H4zM8 8h6m-6 3h4"/>',
  booking: '<rect x="4" y="5" width="14" height="13" rx="2"/><path d="M4 9h14M8 3v4m6-4v4m-6 7 2 2 4-4"/>',
  failure: '<path d="M11 4 19 18H3l8-14zm0 5v4"/><circle cx="11" cy="15.5" r=".2"/>',
  management: '<path d="M4 7h13m-3-3 3 3-3 3M18 15H5m3-3-3 3 3 3"/>',
  files: '<path d="M3 6h7l2 2h7v10H3z"/><path d="M5 6V4h6l2 2h4"/>',
  class: '<path d="m11 3 7 4v8l-7 4-7-4V7zm0 8 7-4m-7 4L4 7m7 4v8"/>',
  step: '<rect x="5" y="5" width="12" height="12" rx="2"/><path d="M9 11h4"/>',
  entry: '<path d="M5 16V7h12m-4-4 4 4-4 4"/>',
  storage: '<ellipse cx="11" cy="6" rx="7" ry="3"/><path d="M4 6v10c0 4 14 4 14 0V6M4 11c0 4 14 4 14 0"/>',
  success: '<circle cx="11" cy="11" r="7"/><path d="m8 11 2 2 4-4"/>',
  plus: '<path d="M11 4v14M4 11h14"/>',
  minus: '<path d="M4 11h14"/>',
  fit: '<path d="M4 8V4h4m6 0h4v4m0 6v4h-4m-6 0H4v-4"/>',
  close: '<path d="m6 6 10 10M16 6 6 16"/>',
  incoming: '<path d="M5 5h9v12m-4-4 4 4 4-4"/>',
  outgoing: '<path d="M5 17V5h12m-4-4 4 4-4 4"/>',
};
const toneInfo = {
  normal: ["step", "Workflow step"],
  entry: ["entry", "Entry point"],
  data: ["storage", "Data boundary"],
  success: ["success", "Outcome"],
  failure: ["failure", "Failure trigger"],
};
const flowLabels = {
  walkthrough: ["Booking walkthrough", "booking"],
  runtime: ["Telegram runtime", "runtime"],
  conversation: ["Conversation graph", "conversation"],
  booking: ["Booking happy path", "booking"],
  "failure-paths": ["Failure paths", "failure"],
  management: ["Manage a booking", "management"],
};
const areas = {
  "files-telegram": "Telegram & workers",
  "files-conversation": "Conversation & models",
  "files-scheduling": "Scheduling & setup",
  "files-data": "Contracts & database",
  "files-evaluation": "Evaluation",
};

function element(tag, classes, value) {
  const node = document.createElement(tag);
  if (classes) node.className = classes;
  if (value !== undefined) node.textContent = value;
  return node;
}

function svgElement(tag, attributes = {}) {
  const node = document.createElementNS(svgNamespace, tag);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
  return node;
}

function icon(name) {
  const node = element("span");
  node.dataset.icon = name;
  node.setAttribute("aria-hidden", "true");
  node.innerHTML = `<svg class="icon" viewBox="0 0 22 22" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round">${icons[name]}</svg>`;
  return node;
}

function button(classes, caption, onClick, symbol) {
  const node = element("button", classes);
  node.type = "button";
  if (symbol) node.append(icon(symbol));
  if (caption) node.append(element("span", null, caption));
  node.addEventListener("click", onClick);
  return node;
}

function fileName(path) {
  return path.slice(path.lastIndexOf("/") + 1);
}

function flowLabel(flow) {
  return flowLabels[flow.id] || [flow.title, "atlas"];
}

function catalogLabel(catalog) {
  return areas[catalog.id] || catalog.title;
}

const files = new Map(catalogs.flatMap((catalog) => catalog.files.map(([path, detail, entries, classes]) => [
  path, { path, detail, entries, classes, area: catalogLabel(catalog) },
])));
const classCount = [...files.values()].reduce((sum, file) => sum + file.classes.length, 0);
const flowById = new Map(flows.map((flow) => [flow.id, flow]));
const firstView = flows[0]?.id ?? "files";
const allNodes = flows.flatMap((flow) => flow.lanes.flatMap((lane) => lane.nodes
  .filter(Boolean).map((node) => ({ ...node, flow }))));
const fileResults = [...files.values()].flatMap((file) => [
  { kind: "File", title: file.path, sub: file.area, detail: file.detail, route: ["files", file.path] },
  ...file.classes.map(([name, detail, method]) => ({
    kind: "Class", title: name, sub: file.path, detail: `${detail} ${method || ""}`, route: ["files", file.path, name],
  })),
  ...file.entries.filter((name) => name && !file.classes.some(([cls]) => cls === name)).map((symbol) => ({
    kind: "Entry", title: symbol, sub: file.path, detail: file.detail, route: ["files", file.path, symbol],
  })),
]);
const searchIndex = [
  ...allNodes.map((node) => ({
    kind: "Step", title: node.title, sub: flowLabel(node.flow)[0],
    detail: `${node.detail} ${node.why || ""} ${node.ref || ""} ${node.flow.transcript?.find((message) => message.node === node.id)?.text || ""}`,
    route: [node.flow.id, node.id],
  })),
  ...fileResults,
];

const viewport = $("viewport");
const scene = $("scene");
const inspector = $("inspector");
const detailPanel = $("inspector-content");
const minimap = $("map");
let route = [];
let layout = null;
let camera = { x: 0, y: 0, scale: 1 };
let mapView = null;
let viewportSize = null;

function routeHash(parts) {
  return "#" + parts.filter(Boolean).map(encodeURIComponent).join("/");
}

function readRoute() {
  try {
    return location.hash.slice(1).split("/").map(decodeURIComponent);
  } catch {
    return [firstView];
  }
}

function go(parts, reveal = false) {
  if (location.hash !== routeHash(parts)) history.pushState(null, "", routeHash(parts));
  renderRoute(parts, reveal);
  if (reveal) showInspector(true);
}

function showInspector(open) {
  inspector.classList.toggle("is-open", open);
  $("open-inspector").setAttribute("aria-expanded", String(open));
}

function renderRoute(parts, reveal = false) {
  const [view, id, symbol] = parts;
  const directory = view === "files";
  const flow = flowById.get(view) || (!directory && flows[0]);
  const viewId = flow ? flow.id : "files";
  const changed = route[0] !== viewId;
  route = [viewId, id, symbol];
  const showFiles = !flow;
  for (const item of document.querySelectorAll(".nav-item")) {
    if (item.dataset.view === viewId) {
      item.setAttribute("aria-current", "page");
      if (changed && matchMedia("(max-width: 660px)").matches) {
        item.scrollIntoView({ block: "nearest", inline: "nearest" });
      }
    } else item.removeAttribute("aria-current");
  }
  $("view-title").textContent = showFiles ? "Files & classes" : flowLabel(flow)[0];
  $("view-description").textContent = showFiles
    ? `${files.size} files and ${classCount} classes, with source locations and recommended entry points.`
    : flow.subtitle;
  $("graph").hidden = $("graph-footer").hidden = $("svg-link").hidden = showFiles;
  $("open-transcript").hidden = !flow?.transcript?.length;
  $("directory").hidden = !showFiles;

  if (showFiles) {
    if (changed) renderDirectory();
    const selected = files.get(id);
    for (const item of $("directory-results").querySelectorAll(".file-card")) {
      const match = item.dataset.file === selected?.path;
      item.classList.toggle("selected", match);
      item.setAttribute("aria-pressed", String(match));
    }
    renderFileDetails(selected, symbol);
    if (reveal && selected) {
      const card = [...$("directory-results").querySelectorAll(".file-card")].find((item) => item.dataset.file === selected.path);
      card?.scrollIntoView({ block: "nearest" });
    }
    return;
  }

  $("svg-link").href = `${flow.id}.svg`;
  if (changed || !layout) renderGraph(flow);
  const nodes = [...layout.nodes.values()];
  const defaultNode = viewport.clientWidth < 600 ? nodes[0] : nodes.find((node) => node.ref);
  const selected = layout.nodes.get(id) || defaultNode || nodes[0];
  updateSelection(selected, Boolean(id));
  if (selected && id && (changed || reveal)) revealNode(selected);
}

function placed(node, x, y, width, height) {
  Object.assign(node.style, { left: `${x}px`, top: `${y}px`, width: `${width}px`, height: `${height}px` });
  return node;
}

function edgePath(from, to, type) {
  if (from.y === to.y) {
    const dir = Math.sign(to.x - from.x);
    const start = (dir > 0 ? from.x + from.width : from.x) + 2 * dir;
    const end = (dir > 0 ? to.x : to.x + to.width) - 3 * dir;
    return `M ${start} ${from.y + from.height / 2} H ${end}`;
  }
  if (type === "async" && from.x === 16 && to.x === 16) {
    return `M 14 ${from.y + from.height / 2} H 2 V ${to.y + to.height / 2} H 13`;
  }
  const dir = Math.sign(to.y - from.y);
  const x1 = from.x + from.width / 2;
  const x2 = to.x + to.width / 2;
  const y1 = (dir > 0 ? from.y + from.height : from.y) + 2 * dir;
  const y2 = (dir > 0 ? to.y : to.y + to.height) - 3 * dir;
  if (x1 === x2) return `M ${x1} ${y1} V ${y2}`;
  const yMid = (y1 + y2) / 2;
  const step = Math.sign(x2 - x1) * 11;
  return `M ${x1} ${y1} V ${yMid - dir * 11} Q ${x1} ${yMid} ${x1 + step} ${yMid} H ${x2 - step} Q ${x2} ${yMid} ${x2} ${yMid + dir * 11} V ${y2}`;
}

function renderGraph(flow) {
  const gap = flow.columns === 5 ? 36 : 47;
  const width = 224 + (5 - flow.columns) * 17;
  const laneWidth = 32 + width * flow.columns + gap * (flow.columns - 1);
  const laneHeight = 160;
  const lanePitch = 183;
  const nodes = new Map();
  const fragment = document.createDocumentFragment();

  flow.lanes.forEach((lane, row) => {
    const y = row * lanePitch;
    fragment.append(placed(element("div", "lane", lane.label), 0, y, laneWidth, laneHeight));
    lane.nodes.forEach((node, column) => {
      if (!node) return;
      const position = node.column ?? column;
      nodes.set(node.id, {
        ...node, x: 16 + position * (width + gap), y: y + 39, width, height: 104,
      });
    });
  });

  const sceneHeight = laneHeight + (flow.lanes.length - 1) * lanePitch;
  const edges = svgElement("svg", { class: "edge-layer", width: laneWidth, height: sceneHeight, "aria-hidden": "true" });
  const defs = svgElement("defs");
  for (const [name, color] of [
    ["call", "#547767"], ["async", "#a46b26"], ["focus", "#108257"], ["focus-async", "#a75a12"],
  ]) {
    const marker = svgElement("marker", {
      id: `arrow-${name}`, viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 10, markerHeight: 10,
      orient: "auto", markerUnits: "userSpaceOnUse",
    });
    marker.append(svgElement("path", { d: "M0 0 L10 5 L0 10 Z", fill: color }));
    defs.append(marker);
  }
  edges.append(defs);
  const connections = flow.edges.map(([from, to, type]) => {
    const path = svgElement("path", {
      d: edgePath(nodes.get(from), nodes.get(to), type), class: type === "async" ? "edge async" : "edge",
      "data-from": from, "data-to": to,
    });
    edges.append(path);
    return { from, to, type, path };
  });
  fragment.append(edges);

  for (const node of nodes.values()) {
    const tone = node.tone || "normal";
    const [symbol, label] = toneInfo[tone];
    const card = placed(button(`node tone-${tone}`, null, () => {
      go([flow.id, node.id]);
      showInspector(true);
    }), node.x, node.y, node.width, node.height);
    const caption = element("span", "node-caption");
    const marker = element("span", "node-icon");
    marker.append(icon(symbol));
    caption.append(marker);
    if (tone !== "normal") caption.append(element("span", null, label));
    const ref = sources[node.ref];
    card.append(caption, element("span", "node-title", node.title));
    if (ref) card.append(element("span", "node-ref", `${fileName(ref.file)}${ref.line ? ":" + ref.line : ""}`));
    card.dataset.node = node.id;
    card.setAttribute("aria-label", node.title);
    node.card = card;
    fragment.append(card);
  }

  layout = { width: laneWidth, height: sceneHeight, nodes, connections, flow };
  scene.replaceChildren(fragment);
  scene.style.width = `${laneWidth}px`;
  scene.style.height = `${sceneHeight}px`;
  renderMinimap();
  viewportSize = { width: viewport.clientWidth, height: viewport.clientHeight };
  fit(true);
}

function updateSelection(selected, isolate) {
  const neighbors = new Set(selected ? [selected.id] : []);
  for (const { from, to, path } of layout.connections) {
    const adjacent = from === selected?.id || to === selected?.id;
    path.classList.toggle("active", adjacent);
    path.classList.toggle("dim", isolate && !adjacent);
    if (adjacent) {
      neighbors.add(from);
      neighbors.add(to);
    }
  }
  for (const node of layout.nodes.values()) {
    const chosen = node === selected;
    node.card.classList.toggle("selected", chosen);
    node.card.classList.toggle("dim", isolate && !neighbors.has(node.id));
    node.card.setAttribute("aria-pressed", String(chosen));
    node.mini.setAttribute("fill", chosen ? "#218962" : node.tone === "failure" ? "#bf7362" : "#96b7a7");
  }
  renderNodeDetails(selected);
}

const codeDialog = $("source-dialog");
const transcriptDialog = $("transcript-dialog");

function codeLine(parts) {
  const code = element("code");
  if (typeof parts === "string") {
    code.textContent = parts;
  } else {
    for (const part of parts) {
      code.append(typeof part === "string" ? part : element("span", `token-${part[0]}`, part[1]));
    }
  }
  return code;
}

function numberedCode(source, expanded) {
  const lines = sourceFiles[source.file];
  const target = source.line || 1;
  const start = expanded ? 0 : Math.max(0, target - 2);
  const end = expanded ? lines.length : Math.min(lines.length, target + 3);
  const code = element("div", `code-lines${expanded ? " full-code" : ""}`);
  code.tabIndex = 0;
  code.setAttribute("role", "region");
  code.setAttribute("aria-label", `${source.file}, line ${target}`);
  for (let index = start; index < end; index++) {
    const line = index + 1;
    const row = element("div", line === target ? "code-row target-line" : "code-row");
    row.append(element("span", "line-number", line), codeLine(lines[index]));
    if (line === target) row.setAttribute("aria-current", "location");
    code.append(row);
  }
  return code;
}

function openSource(source) {
  $("source-title").textContent = source.symbol || fileName(source.file);
  $("source-location").textContent = `${source.file}:${source.line || 1}`;
  const code = numberedCode(source, true);
  $("source-file-body").replaceChildren(code);
  codeDialog.showModal();
  code.querySelector(".target-line")?.scrollIntoView({ block: "center" });
}

function sourceBlock(key, preview = false) {
  const source = sources[key];
  const block = button("source-block", null, () => openSource(source));
  block.append(element("strong", null, source.symbol || source.file));
  block.append(element("span", "source-file", `${source.file}:${source.line || 1}`));
  block.title = "View file in the atlas";
  if (!preview) return block;
  block.append(element("span", "source-action", "View full file"));
  const frame = element("div", "source-preview");
  frame.append(block, numberedCode(source, false));
  return frame;
}

function openTranscript() {
  const flow = layout.flow;
  $("transcript-context").textContent = flow.transcriptContext;
  $("transcript-messages").replaceChildren(...flow.transcript.map((message) => {
    const control = button(`chat-message${message.speaker === "Customer" ? " customer" : ""}`, null, () => {
      transcriptDialog.close();
      go([flow.id, message.node], true);
    });
    control.append(element("strong", null, message.speaker), element("span", null, message.text));
    return control;
  }));
  transcriptDialog.showModal();
}

function walkthroughControls(node) {
  const links = layout.connections;
  const previous = links.find((edge) => edge.to === node.id);
  const next = links.find((edge) => edge.from === node.id);
  const strip = element("div", "step-controls");
  strip.setAttribute("aria-label", "Walkthrough navigation");
  const back = button("button", "Previous", () => go([layout.flow.id, previous.from], true));
  const forward = button("button", "Next step", () => go([layout.flow.id, next.to], true));
  back.disabled = !previous;
  forward.disabled = !next;
  strip.append(back, forward);
  return strip;
}

function section(title, ...children) {
  const group = element("section", "inspector-section");
  group.append(element("h4", null, title), ...children);
  return group;
}

function item(title, caption, symbol, action) {
  const control = button("item", null, action, symbol);
  const block = element("span");
  block.append(element("span", null, title));
  if (caption) block.append(element("small", "item-caption", caption));
  control.append(block);
  return control;
}

function renderNodeDetails(node) {
  detailPanel.replaceChildren();
  detailPanel.scrollTop = 0;
  if (!node) {
    detailPanel.append(element("p", "empty", "Select a node to see its role and connections."));
    return;
  }
  const tone = node.tone || "normal";
  const [symbol, label] = toneInfo[tone];
  const context = element("div", `inspector-context tone-${tone}`);
  context.append(icon(symbol), element("span", null, label));
  detailPanel.append(context, element("h3", null, node.title), element("p", null, node.detail));
  if (layout.flow.transcript) {
    detailPanel.append(walkthroughControls(node));
    const example = layout.flow.transcript.find((message) => message.node === node.id);
    if (example) {
      detailPanel.append(section(`${example.speaker} message`, element("blockquote", "example-message", example.text)));
    }
  }
  if (node.why) {
    const rationale = section("Why this step matters", element("p", null, node.why));
    rationale.classList.add("rationale");
    detailPanel.append(rationale);
  }
  if (node.ref && sources[node.ref]) {
    const file = sources[node.ref].file;
    const code = section("Source", sourceBlock(node.ref, true));
    const ref = node.ref.split("#")[1];
    if (files.has(file)) {
      const link = button("item", "View file & classes", () => go(["files", file, ref], true), "files");
      code.append(link);
    }
    detailPanel.append(code);
  }

  for (const [title, here, there, direction] of [
    ["Incoming", "to", "from", "incoming"], ["Outgoing", "from", "to", "outgoing"],
  ]) {
    const controls = layout.connections.filter((edge) => edge[here] === node.id).map((edge) => {
      const next = layout.nodes.get(edge[there]);
      const async = edge.type === "async";
      const control = item(next.title, async ? "Async or framework handoff" : "Call or flow", direction, () => go([route[0], next.id], true));
      if (async) control.classList.add("is-async");
      return control;
    });
    if (controls.length) detailPanel.append(section(title, ...controls));
  }

  const related = node.ref ? allNodes.filter((other) => other.ref === node.ref && other.flow.id !== route[0]) : [];
  if (related.length) {
    detailPanel.append(section("Also in", ...related.map((other) => item(
      flowLabel(other.flow)[0], other.title, flowLabel(other.flow)[1], () => go([other.flow.id, other.id], true),
    ))));
  }
}

function renderDirectory() {
  const query = $("file-filter").value.trim().toLowerCase();
  const tokens = query.split(/\s+/).filter(Boolean);
  const area = $("group-filter").value;
  const result = $("directory-results");
  result.replaceChildren();
  for (const catalog of catalogs) {
    if (area && area !== catalog.id) continue;
    const members = catalog.files.filter(([path, detail, entries, classes]) => {
      const searchable = [path, detail, ...entries, ...classes.flat()].join(" ").toLowerCase();
      return tokens.every((token) => searchable.includes(token));
    });
    if (!members.length) continue;
    result.append(element("h2", "directory-group", catalogLabel(catalog)));
    const group = element("div", "file-grid");
    for (const [path, detail, entries, classes] of members) {
      const matchingClass = query && classes.find(([name]) => name.toLowerCase().includes(query));
      const match = route[1] === path;
      const card = button(`file-card${match ? " selected" : ""}`, null,
        () => go(["files", path, matchingClass?.[0]], true));
      card.setAttribute("aria-pressed", String(match));
      card.dataset.file = path;
      card.append(element("strong", null, path), element("span", null, detail));
      group.append(card);
    }
    result.append(group);
  }
  if (!result.childElementCount) result.append(element("p", "empty", "No matching files or classes."));
}

function renderFileDetails(file, symbol) {
  detailPanel.replaceChildren();
  detailPanel.scrollTop = 0;
  if (!file) {
    detailPanel.append(element("p", "empty", "Select a file or search for a class to read its purpose and entry points."));
    return;
  }
  const classRow = symbol && file.classes.find(([name, , method]) => name === symbol || `${name}.${method}` === symbol);
  const classRef = classRow ? [classRow[0], classRow[2]].filter(Boolean).join(".") : symbol;
  const entry = file.entries[0];
  const context = element("div", "inspector-context");
  context.append(icon(classRow ? "class" : "file"), element("span", null, classRow ? "Class" : file.area));
  detailPanel.append(context);
  if (symbol) {
    detailPanel.append(item(fileName(file.path), "File overview", "file", () => go(["files", file.path], true)));
  }
  detailPanel.append(element("h3", null, classRow?.[0] || symbol || fileName(file.path)));
  detailPanel.append(element("p", null, classRow?.[1] || file.detail));
  const mainKey = classRef || entry;
  const ref = mainKey ? `${file.path}#${mainKey}` : file.path;
  if (sources[ref]) detailPanel.append(section(symbol ? "Source" : "Start here", sourceBlock(ref, true)));
  if (file.entries.length > 1 && !symbol) {
    detailPanel.append(section("Other entry points", ...file.entries.slice(1).map((name) => sourceBlock(`${file.path}#${name}`))));
  }
  if (file.classes.length && !classRow) {
    detailPanel.append(section("Classes", ...file.classes.map(([name, description]) =>
      item(name, description, "class", () => go(["files", file.path, name], true)))));
  }
  const related = allNodes.filter((node) => sources[node.ref]?.file === file.path);
  if (related.length) {
    detailPanel.append(section("In the graph", ...related.map((node) => item(
      node.title, flowLabel(node.flow)[0], flowLabel(node.flow)[1], () => go([node.flow.id, node.id], true),
    ))));
  }
}

const minZoom = 0.2;
const maxZoom = 2.5;
function updateCamera() {
  scene.style.transform = `translate(${camera.x}px, ${camera.y}px) scale(${camera.scale})`;
  viewport.style.backgroundPosition = `${camera.x}px ${camera.y}px`;
  $("zoom-level").value = `${Math.round(camera.scale * 100)}%`;
  $("zoom-in").disabled = camera.scale >= maxZoom;
  $("zoom-out").disabled = camera.scale <= minZoom;
  if (!mapView) return;
  const x = -camera.x / camera.scale;
  const y = -camera.y / camera.scale;
  const width = viewport.clientWidth / camera.scale;
  const height = viewport.clientHeight / camera.scale;
  const left = Math.max(x, -14);
  const top = Math.max(y, -14);
  const right = Math.min(x + width, layout.width + 14);
  const bottom = Math.min(y + height, layout.height + 14);
  for (const [key, value] of Object.entries({ x: left, y: top, width: Math.max(0, right - left), height: Math.max(0, bottom - top) })) {
    mapView.setAttribute(key, value);
  }
}

function fit(initial = false) {
  if (!layout || $("graph").hidden) return;
  const width = viewport.clientWidth;
  const height = viewport.clientHeight;
  const margin = width < 600 ? 23 : 56;
  const reservedHeight = layout.flow.transcript ? 126 : 100;
  const usableHeight = Math.max(height - reservedHeight, height * .7);
  const fitWidth = Math.min(1.28, (width - margin) / layout.width);
  let scale = Math.max(minZoom, Math.min(fitWidth, usableHeight / layout.height));
  if (initial && width < 600) {
    scale = Math.max(scale, .75);
    camera = { x: 12, y: 20, scale };
  } else {
    if (initial && !layout.flow.transcript && scale < .6) scale = Math.max(scale, Math.min(fitWidth, .85));
    camera = { x: (width - layout.width * scale) / 2, y: Math.max(8, (usableHeight - layout.height * scale) / 2 + 12), scale };
  }
  updateCamera();
}

function centerAt(x, y, minimum = minZoom) {
  const scale = Math.max(camera.scale, minimum);
  camera = { x: viewport.clientWidth / 2 - x * scale, y: viewport.clientHeight / 2 - y * scale, scale };
  updateCamera();
}

function revealNode(node) {
  const scale = camera.scale;
  const left = camera.x + node.x * scale;
  const top = camera.y + node.y * scale;
  if (scale < .65 || left < 12 || top < 12 || left + node.width * scale > viewport.clientWidth - 12 ||
      top + node.height * scale > viewport.clientHeight - 55) {
    centerAt(node.x + node.width / 2, node.y + node.height / 2, .85);
  }
}

function zoom(factor, anchorX = viewport.clientWidth / 2, anchorY = viewport.clientHeight / 2, nextX = anchorX, nextY = anchorY) {
  const scale = Math.max(minZoom, Math.min(maxZoom, camera.scale * factor));
  camera = {
    x: nextX - (anchorX - camera.x) * scale / camera.scale,
    y: nextY - (anchorY - camera.y) * scale / camera.scale,
    scale,
  };
  updateCamera();
}

function renderMinimap() {
  const map = svgElement("svg", { viewBox: `-15 -15 ${layout.width + 30} ${layout.height + 30}`, "aria-hidden": "true" });
  map.append(svgElement("rect", { x: -14, y: -14, width: layout.width + 28, height: layout.height + 28, rx: 12, fill: "#edf3ee" }));
  for (const { path } of layout.connections) {
    map.append(svgElement("path", { d: path.getAttribute("d"), fill: "none", stroke: "#87a396", "stroke-width": 9 }));
  }
  for (const node of layout.nodes.values()) {
    node.mini = svgElement("rect", { x: node.x, y: node.y, width: node.width, height: node.height, rx: 10 });
    map.append(node.mini);
  }
  mapView = svgElement("rect", { rx: 9, fill: "#ffffff22", stroke: "#16674c", "vector-effect": "non-scaling-stroke" });
  map.append(mapView);
  minimap.replaceChildren(map);
}

function localPoint(event) {
  const rect = viewport.getBoundingClientRect();
  return { x: event.clientX - rect.left, y: event.clientY - rect.top };
}

const pointers = new Map();
function gesture() {
  const [a, b] = pointers.values();
  if (!b) return { x: a.x, y: a.y, distance: 1 };
  return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2, distance: Math.max(1, Math.hypot(a.x - b.x, a.y - b.y)) };
}

viewport.addEventListener("pointerdown", (event) => {
  if (event.button !== 0 || event.target.closest("button") || pointers.size >= 2) return;
  pointers.set(event.pointerId, localPoint(event));
  viewport.setPointerCapture(event.pointerId);
  viewport.classList.add("is-panning");
});
viewport.addEventListener("pointermove", (event) => {
  if (!pointers.has(event.pointerId)) return;
  const before = gesture();
  pointers.set(event.pointerId, localPoint(event));
  const after = gesture();
  zoom(after.distance / before.distance, before.x, before.y, after.x, after.y);
});
for (const type of ["pointerup", "pointercancel", "lostpointercapture"]) {
  viewport.addEventListener(type, (event) => {
    pointers.delete(event.pointerId);
    if (!pointers.size) viewport.classList.remove("is-panning");
  });
}
viewport.addEventListener("wheel", (event) => {
  event.preventDefault();
  const { x, y } = localPoint(event);
  const delta = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? viewport.clientHeight : 1);
  zoom(Math.exp(-delta * .0015), x, y);
}, { passive: false });
viewport.addEventListener("keydown", (event) => {
  const commands = {
    "+": () => zoom(1.24), "=": () => zoom(1.24), "-": () => zoom(.81), "0": () => fit(),
    ArrowLeft: () => { camera.x += 55; updateCamera(); }, ArrowRight: () => { camera.x -= 55; updateCamera(); },
    ArrowUp: () => { camera.y += 55; updateCamera(); }, ArrowDown: () => { camera.y -= 55; updateCamera(); },
  };
  if (!Object.hasOwn(commands, event.key)) return;
  event.preventDefault();
  commands[event.key]();
});
scene.addEventListener("focusin", (event) => {
  if (event.target.matches(".node:focus-visible")) revealNode(layout.nodes.get(event.target.dataset.node));
});

$("zoom-in").addEventListener("click", () => zoom(1.24));
$("zoom-out").addEventListener("click", () => zoom(.81));
$("fit").addEventListener("click", () => fit());
minimap.addEventListener("click", (event) => {
  const matrix = mapView.ownerSVGElement.getScreenCTM();
  if (!event.detail || !matrix) {
    fit();
    return;
  }
  const point = new DOMPoint(event.clientX, event.clientY).matrixTransform(matrix.inverse());
  centerAt(point.x, point.y);
});

new ResizeObserver(([entry]) => {
  const { width, height } = entry.contentRect;
  if (!width || !height) {
    viewportSize = null;
    return;
  }
  if (!viewportSize) fit(true);
  else {
    camera.x += (width - viewportSize.width) / 2;
    camera.y += (height - viewportSize.height) / 2;
    updateCamera();
  }
  viewportSize = { width, height };
}).observe(viewport);

const searchDialog = $("search-dialog");
function renderSearch() {
  const query = $("search-input").value.trim().toLowerCase();
  const tokens = query.split(/\s+/).filter(Boolean);
  const matches = query ? searchIndex.filter(({ title, sub, detail }) => {
    const haystack = `${title} ${sub} ${detail}`.toLowerCase();
    return tokens.every((token) => haystack.includes(token));
  }).sort((a, b) => rank(b, query) - rank(a, query)) : searchIndex.filter((item) => item.kind === "Step");

  const result = $("search-results");
  result.replaceChildren();
  for (const entry of matches.slice(0, query ? 24 : 6)) {
    const symbol = { File: "file", Step: "step", Class: "class", Entry: "entry" }[entry.kind];
    const control = item(entry.title, entry.sub, symbol, () => {
      searchDialog.close();
      if (entry.route[0] === "files") {
        $("file-filter").value = "";
        $("group-filter").value = "";
        renderDirectory();
      }
      go(entry.route, true);
    });
    const title = control.lastElementChild.firstElementChild;
    title.classList.add("result-line");
    title.append(element("span", "result-kind", entry.kind));
    result.append(control);
  }
  if (!result.childElementCount) result.append(element("p", "empty", "No matching files, classes, or steps."));
}

function rank({ title, kind }, query) {
  const value = title.toLowerCase();
  const typePriority = { Step: 0, Entry: 1, File: 2, Class: 3 };
  const match = value === query ? 3 : value.startsWith(query) ? 2 : value.includes(query) ? 1 : 0;
  return match * 4 + typePriority[kind];
}

function openSearch() {
  if (searchDialog.open) return;
  $("search-input").value = "";
  renderSearch();
  searchDialog.showModal();
  $("search-input").focus();
}

$("open-search").setAttribute("aria-label", "Search files, classes, and steps");
$("open-search").addEventListener("click", openSearch);
$("open-transcript").addEventListener("click", openTranscript);
$("close-transcript").addEventListener("click", () => transcriptDialog.close());
$("close-source").addEventListener("click", () => codeDialog.close());
$("close-search").addEventListener("click", () => searchDialog.close());
$("search-input").addEventListener("input", renderSearch);
$("search-form").addEventListener("submit", (event) => {
  event.preventDefault();
  $("search-results").querySelector("button")?.click();
});
searchDialog.addEventListener("click", (event) => {
  if (event.target === searchDialog) searchDialog.close();
});
searchDialog.addEventListener("keydown", (event) => {
  if (!["ArrowDown", "ArrowUp"].includes(event.key)) return;
  const results = [...$("search-results").querySelectorAll("button")];
  const index = results.indexOf(document.activeElement);
  const next = index + (event.key === "ArrowDown" ? 1 : -1);
  const destination = next < 0 ? $("search-input") : results[next];
  if (destination) {
    event.preventDefault();
    destination.focus();
  }
});

document.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    openSearch();
  }
});
$("open-inspector").addEventListener("click", () => {
  showInspector(true);
  $("close-inspector").focus();
});
$("close-inspector").addEventListener("click", () => {
  showInspector(false);
  $("open-inspector").focus();
});
$("file-filter").addEventListener("input", renderDirectory);
$("group-filter").addEventListener("change", renderDirectory);

for (const target of document.querySelectorAll("[data-icon]")) target.replaceWith(icon(target.dataset.icon));
$("search-key").textContent = /Mac|iPad|iPhone/.test(navigator.userAgent) ? "⌘ K" : "Ctrl K";
$("project-name").textContent = project;
$("project-mark").textContent = project.slice(0, 1).toUpperCase();
$("brand").href = routeHash([firstView]);
for (const [id, label, glyph] of [...flows.map((flow) => [flow.id, ...flowLabel(flow)]), ["files", "Files & classes", "files"]]) {
  const tab = button("nav-item", label, () => {
    showInspector(false);
    go([id]);
  }, glyph);
  tab.dataset.view = id;
  $(id === "files" ? "file-nav" : "flow-nav").append(tab);
}
$("group-filter").append(new Option("All areas", ""));
for (const catalog of catalogs) $("group-filter").append(new Option(catalogLabel(catalog), catalog.id));
window.addEventListener("hashchange", () => renderRoute(readRoute(), true));
renderRoute(readRoute());
