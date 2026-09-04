"use strict";

const state = {
  revisions: [],
  left: null,
  right: null,
  rows: [],
  preferences: { nodes: {}, edges: [], incomparables: [], maximal: [], suggestions: [], counts: {} },
  pendingPreference: null,
  activeTab: "content",
  visualMode: "normalized",
};

const byId = (id) => document.getElementById(id);
const controls = {
  leftRevision: byId("left-revision"),
  leftProfile: byId("left-profile"),
  rightRevision: byId("right-revision"),
  rightProfile: byId("right-profile"),
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: options.body ? { "Content-Type": "application/json" } : {},
    ...options,
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}

function showError(error) {
  const element = byId("error");
  element.textContent = error instanceof Error ? error.message : String(error);
  element.hidden = false;
  window.setTimeout(() => { element.hidden = true; }, 7000);
}

function revisionLabel(revision) {
  const label = [revision.short, revision.date, revision.subject].filter(Boolean).join(" · ");
  return revision.recordable === false ? `${label || revision.id} (not recordable)` : (label || revision.id);
}

function blockKey(block) {
  return block?.match_key || block?.id;
}

function fillRevisionSelect(select, selected) {
  select.innerHTML = state.revisions
    .map((revision) => `<option value="${escapeHtml(revision.id)}">${escapeHtml(revisionLabel(revision))}</option>`)
    .join("");
  if (selected && state.revisions.some((revision) => revision.id === selected)) {
    select.value = selected;
  }
}

async function fillProfiles(side, preferred) {
  const revision = controls[`${side}Revision`].value;
  const select = controls[`${side}Profile`];
  const { profiles } = await api(`/api/profiles?revision=${encodeURIComponent(revision)}`);
  select.innerHTML = profiles
    .map((profile) => `<option value="${escapeHtml(profile.id)}">${escapeHtml(profile.label)}</option>`)
    .join("");
  if (preferred && profiles.some((profile) => profile.id === preferred)) {
    select.value = preferred;
  } else if (side === "right" && profiles.length > 1) {
    select.value = profiles[1].id;
  } else {
    select.value = profiles[0]?.id || "";
  }
}

function versionQuery(side) {
  const revision = controls[`${side}Revision`].value;
  const profile = controls[`${side}Profile`].value;
  return `/api/version?revision=${encodeURIComponent(revision)}&profile=${encodeURIComponent(profile)}`;
}

async function compare() {
  try {
    [state.left, state.right] = await Promise.all([
      api(versionQuery("left")),
      api(versionQuery("right")),
    ]);
    byId("left-label").textContent = state.left.label;
    byId("right-label").textContent = state.right.label;
    byId("graph-left-label").textContent = state.left.label;
    byId("graph-right-label").textContent = state.right.label;
    buildRows();
    renderDiff();
    if (state.activeTab === "visual") renderVisual();
    updatePreferenceControls();
  } catch (error) {
    showError(error);
  }
}

function visualBlockHtml(block, row, side) {
  if (!block) return "";
  let content = escapeHtml(block.text);
  if (row?.status === "changed" && row.left && row.right) {
    content = tokenDiff(row.left.text, row.right.text)[side];
  }
  const tag = block.kind === "section" ? "h2" : block.kind === "heading" ? "h3" : "div";
  const bullet = block.kind === "bullet" ? '<span class="visual-bullet-mark" aria-hidden="true">•</span>' : "";
  return `<${tag} class="visual-block visual-${escapeHtml(block.kind)} is-${escapeHtml(row?.status || "unchanged")}" data-block-id="${escapeHtml(block.id)}">${bullet}<span>${content}</span></${tag}>`;
}

function renderNormalizedVisual(status = "") {
  const rowsByBlock = new Map();
  state.rows.forEach((row) => {
    if (row.left) rowsByBlock.set(`left:${blockKey(row.left)}`, row);
    if (row.right) rowsByBlock.set(`right:${blockKey(row.right)}`, row);
  });
  const documentHtml = (version, side) => version.blocks
    .map((block) => visualBlockHtml(block, rowsByBlock.get(`${side}:${blockKey(block)}`), side))
    .join("");
  byId("visual-status").textContent = status || "Normalized layout ignores pagination while preserving document hierarchy; changed blocks and words are highlighted.";
  byId("visual-comparison").innerHTML = `<div class="visual-documents">
    <article class="visual-document" aria-label="Left document preview">
      <header><strong>Left</strong><span>${escapeHtml(state.left.label)}</span></header>
      <div class="visual-paper">${documentHtml(state.left, "left")}</div>
    </article>
    <article class="visual-document" aria-label="Right document preview">
      <header><strong>Right</strong><span>${escapeHtml(state.right.label)}</span></header>
      <div class="visual-paper">${documentHtml(state.right, "right")}</div>
    </article>
  </div>`;
}

function visualQuery(side) {
  const revision = controls[`${side}Revision`].value;
  const profile = controls[`${side}Profile`].value;
  return `/api/visual?revision=${encodeURIComponent(revision)}&profile=${encodeURIComponent(profile)}`;
}

async function renderPdfVisual() {
  byId("visual-status").textContent = "Rendering PDF pages…";
  byId("visual-comparison").innerHTML = '<div class="empty-state">Rendering PDF pages…</div>';
  try {
    const [left, right] = await Promise.all([api(visualQuery("left")), api(visualQuery("right"))]);
    if (!left.available || !right.available) {
      const unavailable = [left, right].filter((item) => !item.available).map((item) => item.label).join(" and ");
      state.visualMode = "normalized";
      updateVisualModeButtons();
      renderNormalizedVisual(`Exact PDF pages are unavailable for ${unavailable}. Showing normalized layout instead.`);
      return;
    }
    const pageCount = Math.max(left.pages.length, right.pages.length);
    const pairs = Array.from({ length: pageCount }, (_, index) => {
      const page = (item, side) => item.pages[index]
        ? `<a class="pdf-page" href="${escapeHtml(item.pages[index])}" target="_blank" rel="noopener"><img src="${escapeHtml(item.pages[index])}" alt="${side} document, page ${index + 1}"><span>Page ${index + 1}</span></a>`
        : `<div class="pdf-page missing"><span>No page ${index + 1}</span></div>`;
      return `<section class="pdf-page-pair" aria-label="Page ${index + 1} comparison">${page(left, "Left")}${page(right, "Right")}</section>`;
    }).join("");
    byId("visual-status").textContent = `Exact locally generated PDFs · ${left.pages.length} left page${left.pages.length === 1 ? "" : "s"} · ${right.pages.length} right page${right.pages.length === 1 ? "" : "s"}. Select a page to open it full size.`;
    byId("visual-comparison").innerHTML = `<div class="pdf-column-labels"><strong>Left · ${escapeHtml(state.left.profile_label)}</strong><strong>Right · ${escapeHtml(state.right.profile_label)}</strong></div><div class="pdf-pages">${pairs}</div>`;
  } catch (error) {
    showError(error);
    state.visualMode = "normalized";
    updateVisualModeButtons();
    renderNormalizedVisual();
  }
}

function updateVisualModeButtons() {
  const normalized = state.visualMode === "normalized";
  byId("visual-normalized").setAttribute("aria-pressed", String(normalized));
  byId("visual-normalized").classList.toggle("secondary", !normalized);
  byId("visual-pdf").setAttribute("aria-pressed", String(!normalized));
  byId("visual-pdf").classList.toggle("secondary", normalized);
}

function renderVisual() {
  if (!state.left || !state.right) return;
  if (state.visualMode === "pdf") renderPdfVisual();
  else renderNormalizedVisual();
}

function buildRows() {
  const leftBlocks = state.left.blocks;
  const rightBlocks = state.right.blocks;
  const unmatchedLeft = new Set(leftBlocks);
  const unmatchedRight = new Set(rightBlocks);
  const exactRight = new Map();
  const exactKey = (block) => `${block.kind}\u0000${block.text}`;
  rightBlocks.forEach((block) => {
    const key = exactKey(block);
    if (!exactRight.has(key)) exactRight.set(key, []);
    exactRight.get(key).push(block);
  });
  const pairs = [];
  // Content that merely moved is unchanged. Pair it before using a paragraph
  // identifier, which is positional and can shift after a section is removed.
  leftBlocks.forEach((leftBlock) => {
    const candidates = exactRight.get(exactKey(leftBlock)) || [];
    const rightBlock = candidates.find((candidate) => unmatchedRight.has(candidate));
    if (!rightBlock) return;
    unmatchedLeft.delete(leftBlock);
    unmatchedRight.delete(rightBlock);
    pairs.push([leftBlock, rightBlock]);
  });
  const rightById = new Map();
  [...unmatchedRight].forEach((block) => {
    const key = blockKey(block);
    if (!rightById.has(key)) rightById.set(key, []);
    rightById.get(key).push(block);
  });
  [...unmatchedLeft].forEach((leftBlock) => {
    const candidates = rightById.get(blockKey(leftBlock)) || [];
    const rightBlock = candidates.find((candidate) => unmatchedRight.has(candidate));
    if (rightBlock) {
      unmatchedRight.delete(rightBlock);
      pairs.push([leftBlock, rightBlock]);
    } else {
      pairs.push([leftBlock, null]);
    }
  });
  [...unmatchedRight].forEach((rightBlock) => pairs.push([null, rightBlock]));
  state.rows = pairs.map(([leftBlock, rightBlock]) => {
    let status = "unchanged";
    if (!leftBlock) status = "only-right";
    else if (!rightBlock) status = "only-left";
    else if (leftBlock.text !== rightBlock.text || leftBlock.kind !== rightBlock.kind) status = "changed";
    return {
      id: leftBlock && rightBlock && blockKey(leftBlock) !== blockKey(rightBlock)
        ? `${blockKey(leftBlock)} ↔ ${blockKey(rightBlock)}`
        : blockKey(leftBlock || rightBlock),
      left: leftBlock,
      right: rightBlock,
      status,
      section: leftBlock?.section || rightBlock?.section || "Other",
      index: leftBlock?.index ?? rightBlock?.index ?? 9999,
    };
  }).sort((leftRow, rightRow) => leftRow.index - rightRow.index);
}

function tokenDiff(leftText, rightText) {
  const left = leftText ? leftText.trim().split(/\s+/) : [];
  const right = rightText ? rightText.trim().split(/\s+/) : [];
  const rows = left.length + 1;
  const cols = right.length + 1;
  const matrix = Array.from({ length: rows }, () => new Uint16Array(cols));
  for (let i = left.length - 1; i >= 0; i -= 1) {
    for (let j = right.length - 1; j >= 0; j -= 1) {
      matrix[i][j] = left[i] === right[j]
        ? matrix[i + 1][j + 1] + 1
        : Math.max(matrix[i + 1][j], matrix[i][j + 1]);
    }
  }
  const leftParts = [];
  const rightParts = [];
  let i = 0;
  let j = 0;
  while (i < left.length || j < right.length) {
    if (i < left.length && j < right.length && left[i] === right[j]) {
      leftParts.push(escapeHtml(left[i]));
      rightParts.push(escapeHtml(right[j]));
      i += 1;
      j += 1;
    } else if (j < right.length && (i === left.length || matrix[i][j + 1] >= matrix[i + 1][j])) {
      rightParts.push(`<ins>${escapeHtml(right[j])}</ins>`);
      j += 1;
    } else {
      leftParts.push(`<del>${escapeHtml(left[i])}</del>`);
      i += 1;
    }
  }
  return { left: leftParts.join(" "), right: rightParts.join(" ") };
}

function sectionSlug(section) {
  return `section-${section.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "")}`;
}

function rowHtml(row) {
  const leftText = row.left?.text || "";
  const rightText = row.right?.text || "";
  const rendered = row.status === "changed"
    ? tokenDiff(leftText, rightText)
    : { left: escapeHtml(leftText), right: escapeHtml(rightText) };
  const statusLabels = {
    changed: "Changed",
    "only-left": "Only left",
    "only-right": "Only right",
    unchanged: "Unchanged",
  };
  const kind = row.left?.kind || row.right?.kind || "text";
  const leftClass = leftText ? "" : " empty";
  const rightClass = rightText ? "" : " empty";
  return `<article class="diff-row is-${row.status}">
    <div class="row-meta"><strong>${escapeHtml(row.id)}</strong>${escapeHtml(kind)}<span class="change-kind">${statusLabels[row.status]}</span></div>
    <div class="diff-cell left${leftClass}">${leftText ? rendered.left : "Not present"}</div>
    <div class="diff-cell right${rightClass}">${rightText ? rendered.right : "Not present"}</div>
  </article>`;
}

function renderDiff() {
  const changesOnly = byId("changes-only").checked;
  const visible = state.rows.filter((row) => !changesOnly || row.status !== "unchanged");
  const sections = new Map();
  visible.forEach((row) => {
    if (!sections.has(row.section)) sections.set(row.section, []);
    sections.get(row.section).push(row);
  });

  byId("diff").innerHTML = [...sections.entries()].map(([section, rows]) => `
    <section class="diff-section" id="${sectionSlug(section)}">
      <h2>${escapeHtml(section)}</h2>
      ${rows.map(rowHtml).join("")}
    </section>`).join("") || '<div class="empty-state">These versions have identical structured content.</div>';

  byId("section-nav").innerHTML = [...sections.keys()]
    .map((section) => `<a href="#${sectionSlug(section)}">${escapeHtml(section)}</a>`)
    .join("");

  const counts = Object.groupBy
    ? Object.groupBy(state.rows, (row) => row.status)
    : state.rows.reduce((result, row) => ((result[row.status] ||= []).push(row), result), {});
  const count = (key) => counts[key]?.length || 0;
  byId("stats").innerHTML = [
    ["Changed", count("changed")],
    ["Only left", count("only-left")],
    ["Only right", count("only-right")],
    ["Unchanged", count("unchanged")],
  ].map(([label, value]) => `<span>${label}: <strong>${value}</strong></span>`).join("");
}

function updatePreferenceControls() {
  const disabled = !state.left || !state.right
    || state.left.recordable === false
    || state.right.recordable === false
    || (state.left.content_hash === state.right.content_hash);
  byId("prefer-left").disabled = disabled;
  byId("prefer-right").disabled = disabled;
  byId("mark-incomparable").disabled = disabled;
  byId("graph-prefer-left").disabled = disabled;
  byId("graph-prefer-right").disabled = disabled;
  byId("graph-mark-incomparable").disabled = disabled;
  byId("preference-status").textContent = disabled
    ? "Choose two different recordable versions to record a durable preference."
    : "";
  byId("graph-preference-status").textContent = disabled
    ? "Choose two different recordable versions above."
    : "";
}

function potentialLosses(better, worse) {
  const betterBlocks = new Map(better.blocks.map((block) => [blockKey(block), block]));
  return worse.blocks.flatMap((block) => {
    const replacement = betterBlocks.get(blockKey(block));
    if (!replacement) return [{ type: "Removed", block, replacement: null }];
    if (replacement.text !== block.text) return [{ type: "Changed", block, replacement }];
    return [];
  });
}

function startPreference(direction, reasonId = "preference-reason") {
  const better = direction === "left" ? state.left : state.right;
  const worse = direction === "left" ? state.right : state.left;
  const losses = potentialLosses(better, worse);
  state.pendingPreference = { better, worse, reason: byId(reasonId).value };
  byId("loss-summary").textContent = losses.length
    ? `${losses.length} blocks from the dominated version are removed or rewritten in the preferred version.`
    : "The preferred version retains every block from the dominated version.";
  byId("loss-list").innerHTML = losses.slice(0, 40).map(({ type, block, replacement }) => `
    <div class="loss-item"><strong>${escapeHtml(type)} · ${escapeHtml(block.section)} · ${escapeHtml(block.id)}</strong>
    <div>${escapeHtml(block.text)}</div>
    ${replacement ? `<div><em>Becomes:</em> ${escapeHtml(replacement.text)}</div>` : ""}</div>`).join("");
  if (losses.length > 40) {
    byId("loss-list").insertAdjacentHTML("beforeend", `<p>…and ${losses.length - 40} more blocks.</p>`);
  }
  byId("loss-reviewed").checked = false;
  byId("confirm-preference").disabled = true;
  byId("loss-dialog").showModal();
}

async function recordPendingPreference() {
  if (!state.pendingPreference) return;
  const { better, worse, reason } = state.pendingPreference;
  try {
    state.preferences = await api("/api/preferences", {
      method: "POST",
      body: JSON.stringify({
        better_revision: better.revision,
        better_profile: better.profile,
        worse_revision: worse.revision,
        worse_profile: worse.profile,
        reason,
      }),
    });
    byId("preference-status").classList.remove("error");
    byId("preference-status").textContent = "Preference recorded. Commit the updated preference graph when ready.";
    byId("graph-preference-status").classList.remove("error");
    byId("graph-preference-status").textContent = "Strict preference recorded.";
    renderGraph();
  } catch (error) {
    byId("preference-status").classList.add("error");
    byId("preference-status").textContent = error.message;
    byId("graph-preference-status").classList.add("error");
    byId("graph-preference-status").textContent = error.message;
  } finally {
    state.pendingPreference = null;
  }
}

async function recordIncomparable(reasonId = "preference-reason") {
  try {
    state.preferences = await api("/api/incomparables", {
      method: "POST",
      body: JSON.stringify({
        left_revision: state.left.revision,
        left_profile: state.left.profile,
        right_revision: state.right.revision,
        right_profile: state.right.profile,
        reason: byId(reasonId).value,
      }),
    });
    byId("preference-status").classList.remove("error");
    byId("preference-status").textContent = "Incomparability recorded. Both versions can remain maximal candidates.";
    byId("graph-preference-status").classList.remove("error");
    byId("graph-preference-status").textContent = "Intentional incomparability recorded.";
    renderGraph();
  } catch (error) {
    byId("preference-status").classList.add("error");
    byId("preference-status").textContent = error.message;
    byId("graph-preference-status").classList.add("error");
    byId("graph-preference-status").textContent = error.message;
  }
}

function shortNodeLabel(node) {
  const revision = node.revision.slice(0, 7);
  return `${revision} · ${node.profile_label || node.profile} · ${node.date || "undated"}`;
}

async function loadGraphPair(leftKey, rightKey) {
  const left = state.preferences.nodes[leftKey];
  const right = state.preferences.nodes[rightKey];
  if (!left || !right) return;
  controls.leftRevision.value = left.revision;
  controls.rightRevision.value = right.revision;
  await Promise.all([
    fillProfiles("left", left.profile),
    fillProfiles("right", right.profile),
  ]);
  await compare();
  byId("graph-decision-heading").scrollIntoView({ behavior: "smooth", block: "center" });
}

function renderGraph() {
  const nodes = state.preferences.nodes || {};
  const edges = state.preferences.edges || [];
  const incomparables = state.preferences.incomparables || [];
  const maximal = new Set(state.preferences.maximal || []);
  const suggestions = state.preferences.suggestions || [];
  const keys = Object.keys(nodes);
  byId("graph-empty").hidden = keys.length > 0;
  const svg = byId("preference-graph");
  svg.hidden = keys.length === 0;
  if (!keys.length) {
    byId("edge-list").innerHTML = "";
    return;
  }

  const profiles = [...new Set(keys.map((key) => nodes[key].profile))];
  const laneWidth = 250;
  const rowHeight = 74;
  const nodeWidth = 218;
  const nodeHeight = 54;
  const maxOrder = Math.max(0, ...keys.map((key) => Number(nodes[key].order ?? 0)));
  const width = Math.max(820, profiles.length * laneWidth + 50);
  const height = Math.max(240, (maxOrder + 1) * rowHeight + 100);
  const positions = {};
  keys.forEach((key) => {
    const lane = profiles.indexOf(nodes[key].profile);
    positions[key] = { x: 25 + lane * laneWidth, y: 50 + Number(nodes[key].order ?? maxOrder + 1) * rowHeight };
  });

  const pathBetween = (leftKey, rightKey, className, marker = "") => {
    const start = positions[leftKey];
    const end = positions[rightKey];
    if (!start || !end) return "";
    const x1 = start.x + nodeWidth / 2;
    const y1 = start.y + nodeHeight / 2;
    const x2 = end.x + nodeWidth / 2;
    const y2 = end.y + nodeHeight / 2;
    const bend = Math.max(24, Math.abs(y2 - y1) / 2);
    return `<path class="${className}" ${marker} d="M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 + bend} ${y2}, ${x2} ${y2}"></path>`;
  };
  const suggestionSvg = suggestions.map((item) => pathBetween(item.left, item.right, "graph-suggestion")).join("");
  const incomparableSvg = incomparables.map((item) => pathBetween(item.left, item.right, "graph-incomparable")).join("");
  const edgeSvg = edges.map((edge) => pathBetween(edge.better, edge.worse, "graph-edge", 'marker-end="url(#arrow)"')).join("");
  const laneSvg = profiles.map((profile, index) => {
    const node = Object.values(nodes).find((item) => item.profile === profile);
    return `<text class="graph-lane-label" x="${25 + index * laneWidth + nodeWidth / 2}" y="25">${escapeHtml(node?.profile_label || profile)}</text>`;
  }).join("");
  const nodeSvg = keys.map((key) => {
    const node = nodes[key];
    const position = positions[key];
    const title = node.subject.length > 28 ? `${node.subject.slice(0, 25)}…` : node.subject;
    return `<g class="graph-node ${maximal.has(key) ? "is-maximal" : "is-dominated"}" transform="translate(${position.x},${position.y})">
      <rect width="${nodeWidth}" height="${nodeHeight}" rx="8"></rect>
      <text x="10" y="21">${escapeHtml(node.revision.slice(0, 7))} · ${escapeHtml(node.date || "")}</text>
      <text class="node-detail" x="10" y="40">${escapeHtml(title)}</text>
    </g>`;
  }).join("");
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("width", String(width));
  svg.setAttribute("height", String(height));
  svg.innerHTML = `<defs><marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="currentColor"></path></marker></defs>${laneSvg}${suggestionSvg}${incomparableSvg}${edgeSvg}${nodeSvg}`;

  const counts = state.preferences.counts || {};
  byId("graph-stats").innerHTML = [
    ["Versions", counts.versions ?? keys.length],
    ["Maximal", counts.maximal ?? maximal.size],
    ["Strict", counts.strict_preferences ?? edges.length],
    ["Incomparable", counts.incomparable_pairs ?? incomparables.length],
    ["Next decisions", counts.unresolved_suggestions ?? suggestions.length],
  ].map(([label, value]) => `<span><strong>${value}</strong>${label}</span>`).join("");
  byId("maximal-list").innerHTML = [...maximal]
    .sort((a, b) => (nodes[a]?.order ?? 9999) - (nodes[b]?.order ?? 9999))
    .map((key) => `<article class="maximal-card"><strong>${escapeHtml(shortNodeLabel(nodes[key]))}</strong><span>${escapeHtml(nodes[key].subject)}</span><button type="button" class="secondary use-version" data-node="${escapeHtml(key)}">Compare</button></article>`)
    .join("");

  byId("suggestion-list").innerHTML = suggestions.length
    ? suggestions.map((item) => `<article class="suggestion-item"><div><strong>${escapeHtml(shortNodeLabel(nodes[item.left]))}</strong><span>↔</span><strong>${escapeHtml(shortNodeLabel(nodes[item.right]))}</strong><small>${item.kind === "revision-lineage" ? "Same profile lineage" : "Cross-profile frontier"}</small></div><button type="button" class="secondary load-pair" data-left="${escapeHtml(item.left)}" data-right="${escapeHtml(item.right)}">Load comparison</button></article>`).join("")
    : '<div class="empty-state compact">No queued comparisons. Every current maximal pair is resolved.</div>';

  const strictHistory = edges.map((edge) => {
    const better = nodes[edge.better];
    const worse = nodes[edge.worse];
    const losses = edge.loss_summary || {};
    return `<article class="edge-item">
      <strong>${escapeHtml(better?.label || edge.better)}</strong><span>→</span><strong>${escapeHtml(worse?.label || edge.worse)}</strong>
      <div class="reason">${edge.reason ? `Reason: ${escapeHtml(edge.reason)} · ` : ""}Loss guard: ${(losses.removed || []).length} removed, ${(losses.changed || []).length} changed.</div>
    </article>`;
  });
  const incomparableHistory = incomparables.map((item) => `<article class="edge-item incomparable-item">
    <strong>${escapeHtml(nodes[item.left]?.label || item.left)}</strong><span>∥</span><strong>${escapeHtml(nodes[item.right]?.label || item.right)}</strong>
    <div class="reason">Intentionally incomparable${item.reason ? ` · ${escapeHtml(item.reason)}` : ""}</div>
  </article>`);
  byId("edge-list").innerHTML = [...strictHistory, ...incomparableHistory].join("") || '<div class="empty-state compact">No decisions recorded yet.</div>';
}

function activateTab(tabId) {
  state.activeTab = tabId;
  ["content", "visual", "graph"].forEach((name) => {
    const selected = name === tabId;
    byId(`${name}-tab`).setAttribute("aria-selected", String(selected));
    byId(`${name}-panel`).hidden = !selected;
  });
  if (tabId === "visual") renderVisual();
  if (tabId === "graph") renderGraph();
}

async function initialize() {
  try {
    const [{ revisions }, preferences] = await Promise.all([
      api("/api/catalog"),
      api("/api/preferences"),
    ]);
    state.revisions = revisions;
    state.preferences = preferences;
    const defaultRevision = revisions.find((revision) => revision.recordable !== false)?.id || revisions[0]?.id;
    if (!defaultRevision) throw new Error("The project adapter returned no revisions.");
    fillRevisionSelect(controls.leftRevision, defaultRevision);
    fillRevisionSelect(controls.rightRevision, defaultRevision);
    await Promise.all([fillProfiles("left"), fillProfiles("right")]);
    renderGraph();
    await compare();
  } catch (error) {
    showError(error);
  }
}

controls.leftRevision.addEventListener("change", async () => { await fillProfiles("left"); await compare(); });
controls.rightRevision.addEventListener("change", async () => { await fillProfiles("right"); await compare(); });
controls.leftProfile.addEventListener("change", compare);
controls.rightProfile.addEventListener("change", compare);
byId("changes-only").addEventListener("change", renderDiff);
byId("swap").addEventListener("click", async () => {
  const leftRevision = controls.leftRevision.value;
  const leftProfile = controls.leftProfile.value;
  const rightRevision = controls.rightRevision.value;
  const rightProfile = controls.rightProfile.value;
  controls.leftRevision.value = rightRevision;
  controls.rightRevision.value = leftRevision;
  await Promise.all([fillProfiles("left", rightProfile), fillProfiles("right", leftProfile)]);
  await compare();
});
byId("content-tab").addEventListener("click", () => activateTab("content"));
byId("visual-tab").addEventListener("click", () => activateTab("visual"));
byId("graph-tab").addEventListener("click", () => activateTab("graph"));
byId("visual-normalized").addEventListener("click", () => {
  state.visualMode = "normalized";
  updateVisualModeButtons();
  renderVisual();
});
byId("visual-pdf").addEventListener("click", () => {
  state.visualMode = "pdf";
  updateVisualModeButtons();
  renderVisual();
});
byId("prefer-left").addEventListener("click", () => startPreference("left"));
byId("prefer-right").addEventListener("click", () => startPreference("right"));
byId("mark-incomparable").addEventListener("click", () => recordIncomparable());
byId("graph-prefer-left").addEventListener("click", () => startPreference("left", "graph-preference-reason"));
byId("graph-prefer-right").addEventListener("click", () => startPreference("right", "graph-preference-reason"));
byId("graph-mark-incomparable").addEventListener("click", () => recordIncomparable("graph-preference-reason"));
byId("suggestion-list").addEventListener("click", (event) => {
  const button = event.target.closest(".load-pair");
  if (button) loadGraphPair(button.dataset.left, button.dataset.right).catch(showError);
});
byId("maximal-list").addEventListener("click", (event) => {
  const button = event.target.closest(".use-version");
  if (!button) return;
  const selectedKey = button.dataset.node;
  const rightKey = Object.keys(state.preferences.nodes).find((key) => {
    const node = state.preferences.nodes[key];
    return node.revision === state.right?.revision && node.profile === state.right?.profile && key !== selectedKey;
  });
  const suggestion = state.preferences.suggestions.find((item) => item.left === selectedKey || item.right === selectedKey);
  const partner = suggestion ? (suggestion.left === selectedKey ? suggestion.right : suggestion.left) : null;
  if (rightKey || partner) loadGraphPair(selectedKey, rightKey || partner).catch(showError);
});
byId("loss-reviewed").addEventListener("change", (event) => { byId("confirm-preference").disabled = !event.target.checked; });
byId("loss-dialog").addEventListener("close", () => {
  if (byId("loss-dialog").returnValue === "confirm") recordPendingPreference();
  else state.pendingPreference = null;
});
async function refreshVersions() {
  const button = byId("refresh-versions");
  button.disabled = true;
  button.textContent = "Refreshing…";
  try {
    const { revisions } = await api("/api/refresh", { method: "POST", body: "{}" });
    const left = controls.leftRevision.value;
    const right = controls.rightRevision.value;
    state.revisions = revisions;
    fillRevisionSelect(controls.leftRevision, left);
    fillRevisionSelect(controls.rightRevision, right);
    state.preferences = await api("/api/preferences");
    renderGraph();
  } catch (error) {
    showError(error);
  } finally {
    button.disabled = false;
    button.textContent = "Refresh versions";
  }
}

byId("refresh-versions").addEventListener("click", refreshVersions);
window.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && !event.altKey && !event.shiftKey && event.key.toLowerCase() === "r") {
    event.preventDefault();
    refreshVersions();
  }
});

initialize();
