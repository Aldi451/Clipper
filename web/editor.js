/* Clipper — video editor UI (timeline, teks, auto subtitle, template, export 4K). */
"use strict";

const ED = {
  clipId: null,
  clip: null,
  job: null,
  source: null,
  spec: null,
  meta: null,
  summary: null,
  dirty: false,
  saving: false,
  time: 0,
  playing: false,
  tab: "text",
  selectedText: null,
  selectedSegment: 0,
  zoom: 1,
  saveTimer: null,
  lastRenderStatus: "",
};

/* ------------------------------------------------------------------ */
/* util                                                              */
/* ------------------------------------------------------------------ */
const edEl = (id) => document.getElementById(id);

function edFmt(t, withMs) {
  const s = Math.max(0, Number(t) || 0);
  const m = Math.floor(s / 60);
  const sec = s % 60;
  if (withMs) return `${m}:${sec.toFixed(2).padStart(5, "0")}`;
  return `${m}:${Math.floor(sec).toString().padStart(2, "0")}`;
}

function edDeepClone(obj) {
  return JSON.parse(JSON.stringify(obj));
}

function specDuration(spec) {
  return (spec.segments || []).reduce(
    (sum, s) => sum + Math.max(0.05, (Number(s.end) - Number(s.start)) / Math.max(0.5, Number(s.speed) || 1)),
    0
  );
}

function segmentsWithOut(segments) {
  let cursor = 0;
  return (segments || []).map((seg) => {
    const dur = Math.max(0.05, (Number(seg.end) - Number(seg.start)) / Math.max(0.5, Number(seg.speed) || 1));
    const item = { ...seg, outStart: cursor, outEnd: cursor + dur, outDur: dur };
    cursor += dur;
    return item;
  });
}

function outToSrc(t, segments) {
  for (const seg of segmentsWithOut(segments)) {
    if (t >= seg.outStart && t <= seg.outEnd) {
      return Number(seg.start) + (t - seg.outStart) * Math.max(0.5, Number(seg.speed) || 1);
    }
  }
  const list = segmentsWithOut(segments);
  return list.length ? Number(list[list.length - 1].end) : 0;
}

/* ------------------------------------------------------------------ */
/* muat state                                                        */
/* ------------------------------------------------------------------ */
async function loadEditor(clipId) {
  const data = await api(`/api/clips/${clipId}/editor`);
  ED.clipId = clipId;
  ED.clip = data.clip;
  ED.job = data.job;
  ED.source = data.source;
  ED.spec = data.spec;
  ED.meta = data.meta;
  ED.summary = data.summary;
  ED.dirty = false;
  ED.time = 0;
  ED.selectedText = null;
  ED.selectedSegment = 0;
  ED.lastRenderStatus = data.clip.status;
}

/* ------------------------------------------------------------------ */
/* simpan (auto)                                                     */
/* ------------------------------------------------------------------ */
function scheduleSave(immediate) {
  ED.dirty = true;
  updateSaveBadge();
  clearTimeout(ED.saveTimer);
  ED.saveTimer = setTimeout(() => saveSpec(), immediate ? 0 : 650);
}

async function saveSpec() {
  if (!ED.spec || ED.saving) return;
  ED.saving = true;
  updateSaveBadge();
  try {
    const res = await api(`/api/clips/${ED.clipId}/spec`, { method: "PUT", body: { spec: ED.spec } });
    ED.spec = res.spec;
    ED.summary = res.summary;
    ED.dirty = false;
    refreshChrome();
  } catch (err) {
    toast("Gagal menyimpan: " + err.message, "bad");
  } finally {
    ED.saving = false;
    updateSaveBadge();
  }
}

function updateSaveBadge() {
  const badge = edEl("ed-save-state");
  if (!badge) return;
  if (ED.saving) badge.innerHTML = `<span class="spinner"></span> menyimpan…`;
  else if (ED.dirty) badge.textContent = "● belum disimpan";
  else badge.textContent = "✓ tersimpan";
  badge.className = "tiny " + (ED.dirty ? "warn" : "dim");
}

/* ------------------------------------------------------------------ */
/* render halaman                                                    */
/* ------------------------------------------------------------------ */
async function renderEditor(clipId) {
  stopPolling();
  if (ED.dirty && ED.clipId && ED.clipId !== clipId) await saveSpec();
  await loadEditor(clipId);
  const view = $("#view");
  const clip = ED.clip;
  view.innerHTML = `
    <div class="ed-page">
      <div class="ed-topbar">
        <div class="row" style="gap:10px;min-width:0">
          <a class="btn small ghost" href="#/project/${esc(clip.job_id)}">← Project</a>
          <div style="min-width:0">
            <input class="ed-title-input" id="ed-title" value="${esc(clip.title)}" maxlength="100" />
            <div class="tiny dim" id="ed-meta-line"></div>
          </div>
        </div>
        <div class="row" style="gap:8px">
          <span id="ed-save-state" class="tiny dim">✓ tersimpan</span>
          <select class="select" id="ed-template" style="width:auto;padding:8px 34px 8px 12px">
            <option value="">Template…</option>
            ${ED.meta.templates.map((t) => `<option value="${t.id}">${esc(t.name)}</option>`).join("")}
          </select>
          <button class="btn small" data-ed="auto-subtitle">✨ Auto subtitle</button>
          <select class="select" id="ed-resolution" style="width:auto;padding:8px 34px 8px 12px">
            ${ED.meta.resolutions.map((r) => `<option value="${r.height}" ${Number(ED.spec.output.height) === r.height ? "selected" : ""}>${esc(r.label)}</option>`).join("")}
          </select>
          <button class="btn small primary" data-ed="render">Render</button>
          <a class="btn small ghost" id="ed-download" href="/api/clips/${esc(clip.id)}/download" ${clip.has_file ? "" : "hidden"}>Unduh</a>
        </div>
      </div>

      <div class="ed-grid">
        <div class="ed-left">
          <div class="ed-preview-wrap">
            <div class="ed-preview" id="ed-preview">
              <video id="ed-video" playsinline preload="auto"></video>
              <div class="ed-overlay" id="ed-overlay">
                <div class="ed-caption" id="ed-caption"></div>
                <div id="ed-texts"></div>
                <img id="ed-logo" alt="" hidden />
              </div>
              <div class="ed-render-badge" id="ed-render-badge" hidden></div>
            </div>
          </div>

          <div class="ed-transport">
            <button class="btn small ghost" data-ed="play">▶</button>
            <span class="mono tiny" id="ed-time">0:00 / 0:00</span>
            <div class="ed-seek" id="ed-seek"><i></i></div>
            <button class="btn small ghost" data-ed="mark-in" title="Set awal segmen ke playhead">⇤</button>
            <button class="btn small ghost" data-ed="mark-out" title="Set akhir segmen ke playhead">⇥</button>
          </div>

          <div class="ed-timeline" id="ed-timeline"></div>
          <div class="tiny dim" style="margin-top:6px">
            Timeline = potongan video sumber yang dipakai. Klik segmen untuk mengatur, atau pakai tombol ⇤ ⇥ saat video diputar.
          </div>
        </div>

        <div class="ed-right">
          <div class="ed-tabs">
            ${[
              ["text", "Teks"],
              ["caption", "Caption"],
              ["layout", "Layout & Logo"],
              ["export", "Export & 4K"],
            ]
              .map(([id, label]) => `<button class="ed-tab ${ED.tab === id ? "active" : ""}" data-ed-tab="${id}">${label}</button>`)
              .join("")}
          </div>
          <div class="ed-panel" id="ed-panel"></div>
        </div>
      </div>
    </div>
  `;

  bindEditorEvents();
  renderPreviewSource();
  renderTimeline();
  renderPanel();
  refreshChrome();
}

function refreshChrome() {
  if (!ED.spec) return;
  const dur = specDuration(ED.spec);
  const res = ED.spec.output;
  const meta = edEl("ed-meta-line");
  if (meta) {
    meta.textContent =
      `${ED.spec.segments.length} segmen · ${edFmt(dur)} · ${res.width}×${res.height} · ` +
      `${(ED.spec.subtitles || []).filter((s) => !s.hidden).length} baris caption · ` +
      `${(ED.spec.texts || []).length} teks`;
  }
  const timeLabel = edEl("ed-time");
  if (timeLabel) timeLabel.textContent = `${edFmt(ED.time)} / ${edFmt(dur)}`;
  updateSaveBadge();
}

/* ------------------------------------------------------------------ */
/* preview                                                           */
/* ------------------------------------------------------------------ */
function renderPreviewSource() {
  const video = edEl("ed-video");
  if (!video) return;
  const hasFile = ED.clip.has_file && !ED.dirty;
  const src = hasFile
    ? `/api/clips/${ED.clipId}/video?v=${Math.round(Date.now() / 1000)}`
    : `/api/jobs/${ED.clip.job_id}/source`;
  if (video.dataset.src !== src) {
    video.dataset.src = src;
    video.src = src;
  }
  video.muted = false;
  video.volume = 0.85;
  // posisi awal = awal segmen pertama
  seekTo(0);
}

function seekTo(outTime) {
  const video = edEl("ed-video");
  if (!video || !ED.spec) return;
  const dur = specDuration(ED.spec);
  const t = Math.max(0, Math.min(dur, outTime));
  ED.time = t;
  const srcTime = outToSrc(t, ED.spec.segments);
  if (Math.abs((video.currentTime || 0) - srcTime) > 0.15) {
    try {
      video.currentTime = srcTime;
    } catch (err) {}
  }
  renderOverlay();
  updatePlayhead();
  refreshChrome();
}

function setPlayheadLabel() {}

function updatePlayhead() {
  const seek = edEl("ed-seek");
  const dur = specDuration(ED.spec);
  if (seek && dur > 0) {
    const fill = seek.querySelector("i");
    if (fill) fill.style.width = ((ED.time / dur) * 100).toFixed(2) + "%";
  }
  const marker = edEl("ed-playhead");
  const timeline = edEl("ed-timeline");
  if (marker && timeline && dur > 0) {
    marker.style.left = ((ED.time / dur) * 100).toFixed(2) + "%";
  }
  const timeLabel = edEl("ed-time");
  if (timeLabel) timeLabel.textContent = `${edFmt(ED.time)} / ${edFmt(dur)}`;
}

function renderOverlay() {
  if (!ED.spec) return;
  const t = ED.time;
  const caps = ED.spec.captions || {};
  const capBox = edEl("ed-caption");
  const textsBox = edEl("ed-texts");
  const logo = edEl("ed-logo");
  if (!capBox || !textsBox) return;

  // caption
  let activeLine = null;
  if (caps.enabled) {
    activeLine = (ED.spec.subtitles || []).find((line) => !line.hidden && t >= line.start && t <= line.end);
  }
  if (activeLine) {
    const words = activeLine.words && activeLine.words.length
      ? activeLine.words
      : [{ text: activeLine.text, start: activeLine.start, end: activeLine.end }];
    capBox.innerHTML = words
      .map((w) => {
        const isActive = caps.word_level !== false && t >= w.start && t <= w.end;
        let text = caps.uppercase ? String(w.text).toUpperCase() : w.text;
        return `<span class="${isActive ? "on" : ""}">${esc(text)}</span>`;
      })
      .join(" ");
    capBox.hidden = false;
    capBox.style.bottom = `${(1 - Number(caps.y || 0.76)) * 100}%`;
    capBox.style.fontSize = `${Number(caps.size || 86) * 0.075}vw`;
    capBox.style.webkitTextStroke = `${Math.max(1, Number(caps.stroke || 9) * 0.09)}vh ${caps.stroke_color || "#000"}`;
    capBox.style.color = caps.color || "#fff";
    capBox.querySelectorAll(".on").forEach((el) => {
      el.style.color = caps.highlight || "#FFE02B";
    });
  } else {
    capBox.hidden = true;
  }

  // teks overlay
  textsBox.innerHTML = (ED.spec.texts || [])
    .filter((item) => t >= item.start && t <= item.end)
    .map((item) => {
      const style = [
        `left:${Number(item.x) * 100}%`,
        `top:${Number(item.y) * 100}%`,
        `transform:translate(-50%,-50%) rotate(${Number(item.rotation) || 0}deg) scale(${animScale(item, t)})`,
        `font-size:${Number(item.size || 80) * 0.07}vw`,
        `color:${item.color || "#fff"}`,
        `font-weight:${item.bold ? 800 : 600}`,
        `-webkit-text-stroke:${Math.max(0, Number(item.stroke || 8) * 0.08)}vh ${item.stroke_color || "#000"}`,
        item.box
          ? `background:${item.box_color || "#000"};opacity:1;padding:0.4em 0.6em;border-radius:0.15em;`
          : "",
        `--box-opacity:${Number(item.box_opacity ?? 0.55)}`,
      ].join(";");
      const text = item.uppercase ? String(item.text).toUpperCase() : item.text;
      return `<div class="ed-text" style="${style}">${esc(text)}</div>`;
    })
    .join("");
  textsBox.querySelectorAll(".ed-text").forEach((el) => {
    const item = ED.spec.texts.find((x) => String(x.text).toUpperCase() === el.textContent);
    if (item && item.box) el.style.background = hexToRgba(item.box_color || "#000", Number(item.box_opacity ?? 0.55));
  });

  // logo
  const logoSpec = ED.spec.logo || {};
  if (logoSpec.path && (Number(logoSpec.end) === 0 || (t >= Number(logoSpec.start) && t <= Number(logoSpec.end)))) {
    logo.hidden = false;
    logo.src = `/api/clips/${ED.clipId}/assets/${logoSpec.path.split("/").pop()}`;
    logo.style.left = `${Number(logoSpec.x) * 100}%`;
    logo.style.top = `${Number(logoSpec.y) * 100}%`;
    logo.style.width = `${Number(logoSpec.width) * 100}%`;
    logo.style.opacity = String(Number(logoSpec.opacity ?? 1));
  } else {
    logo.hidden = true;
  }
}

function animScale(item, t) {
  if (item.anim === "pop") {
    const progress = Math.min(1, Math.max(0, (t - item.start) / 0.22));
    return (0.55 + 0.45 * progress).toFixed(3);
  }
  return 1;
}

function hexToRgba(hex, alpha) {
  const value = String(hex || "#000000").replace("#", "");
  const full = value.length === 3 ? value.split("").map((c) => c + c).join("") : value;
  const r = parseInt(full.slice(0, 2), 16) || 0;
  const g = parseInt(full.slice(2, 4), 16) || 0;
  const b = parseInt(full.slice(4, 6), 16) || 0;
  return `rgba(${r},${g},${b},${alpha})`;
}

/* ------------------------------------------------------------------ */
/* timeline                                                          */
/* ------------------------------------------------------------------ */
function renderTimeline() {
  const box = edEl("ed-timeline");
  if (!box) return;
  const segments = segmentsWithOut(ED.spec.segments);
  const total = Math.max(0.1, segments.reduce((s, x) => s + x.outDur, 0));
  box.innerHTML = `
    <div class="ed-track">
      ${segments
        .map((seg, idx) => {
          const width = (seg.outDur / total) * 100;
          return `<div class="ed-seg ${idx === ED.selectedSegment ? "sel" : ""}" data-seg="${idx}"
            style="width:${width}%">
            <span class="ed-seg-label">${idx + 1}</span>
            <span class="ed-seg-time">${edFmt(seg.start, true)}→${edFmt(seg.end, true)}</span>
            ${Number(seg.speed) !== 1 ? `<span class="ed-seg-speed">${seg.speed}×</span>` : ""}
          </div>`;
        })
        .join("")}
      <div class="ed-playhead" id="ed-playhead"></div>
    </div>
    <div class="ed-seg-editor" id="ed-seg-editor"></div>
  `;
  renderSegmentEditor();
  updatePlayhead();
  box.querySelectorAll("[data-seg]").forEach((el) => {
    el.addEventListener("click", () => {
      ED.selectedSegment = Number(el.dataset.seg);
      renderTimeline();
    });
  });
}

function renderSegmentEditor() {
  const box = edEl("ed-seg-editor");
  if (!box) return;
  const idx = ED.selectedSegment;
  const seg = ED.spec.segments[idx];
  if (!seg) {
    box.innerHTML = `<div class="tiny dim">Belum ada segmen.</div>`;
    return;
  }
  box.innerHTML = `
    <div class="ed-seg-row">
      <span class="tiny dim">Segmen ${idx + 1}</span>
      <label class="tiny">awal <input class="input ed-mini" type="number" step="0.1" min="0" value="${Number(seg.start).toFixed(2)}" data-seg-field="start" /></label>
      <label class="tiny">akhir <input class="input ed-mini" type="number" step="0.1" min="0" value="${Number(seg.end).toFixed(2)}" data-seg-field="end" /></label>
      <label class="tiny">kecepatan
        <select class="select ed-mini" data-seg-field="speed">
          ${[0.5, 0.75, 1, 1.25, 1.5, 2].map((v) => `<option value="${v}" ${Number(seg.speed) === v ? "selected" : ""}>${v}×</option>`).join("")}
        </select>
      </label>
      <button class="btn small ghost" data-ed="seg-split">✂ Pecah di playhead</button>
      <button class="btn small ghost" data-ed="seg-add">+ Segmen</button>
      <button class="btn small ghost danger" data-ed="seg-remove" ${ED.spec.segments.length <= 1 ? "disabled" : ""}>Hapus</button>
    </div>
  `;
  box.querySelectorAll("[data-seg-field]").forEach((el) => {
    el.addEventListener("change", () => {
      const field = el.dataset.segField;
      const value = Number(el.value);
      const segs = ED.spec.segments;
      if (field === "speed") segs[idx].speed = Math.min(2, Math.max(0.5, value || 1));
      else if (field === "start") segs[idx].start = Math.max(0, Math.min(value, segs[idx].end - 0.2));
      else if (field === "end") segs[idx].end = Math.max(segs[idx].start + 0.2, Math.min(value, ED.source.duration || 99999));
      scheduleSave();
      renderTimeline();
      renderPanel();
    });
  });
}

/* ------------------------------------------------------------------ */
/* panel kanan                                                       */
/* ------------------------------------------------------------------ */
function renderPanel() {
  const panel = edEl("ed-panel");
  if (!panel) return;
  if (ED.tab === "text") panel.innerHTML = panelTextHtml();
  else if (ED.tab === "caption") panel.innerHTML = panelCaptionHtml();
  else if (ED.tab === "layout") panel.innerHTML = panelLayoutHtml();
  else panel.innerHTML = panelExportHtml();
  bindPanelEvents();
}

function fieldRow(label, inputHtml, hint) {
  return `<div class="ed-field"><label class="tiny">${label}</label>${inputHtml}${hint ? `<span class="tiny dim">${hint}</span>` : ""}</div>`;
}

function panelTextHtml() {
  const texts = ED.spec.texts || [];
  return `
    <div class="row" style="justify-content:space-between;margin-bottom:10px">
      <span class="tiny dim">${texts.length} teks overlay</span>
      <button class="btn small" data-ed="text-add">+ Tambah teks</button>
    </div>
    ${texts.length ? "" : `<div class="empty tiny">Belum ada teks. Tambahkan hook, judul, atau call-to-action.</div>`}
    <div class="ed-list">
      ${texts
        .map((item, idx) => {
          const open = ED.selectedText === item.id;
          return `<div class="ed-item ${open ? "open" : ""}">
            <div class="ed-item-head" data-ed-text="${esc(item.id)}">
              <span class="ed-item-title">${esc(item.text).slice(0, 40)}</span>
              <span class="tiny dim">${edFmt(item.start)}–${edFmt(item.end)}</span>
              <span class="ed-caret">${open ? "▾" : "▸"}</span>
            </div>
            ${open ? textEditorHtml(item, idx) : ""}
          </div>`;
        })
        .join("")}
    </div>
  `;
}

function textEditorHtml(item) {
  const t = ED.time;
  return `
    <div class="ed-item-body">
      ${fieldRow("Isi teks", `<textarea class="input" rows="2" data-text-field="text">${esc(item.text)}</textarea>`)}
      <div class="ed-two">
        ${fieldRow("Mulai (detik)", `<input class="input" type="number" step="0.1" min="0" data-text-field="start" value="${Number(item.start).toFixed(2)}" />`)}
        ${fieldRow("Selesai", `<input class="input" type="number" step="0.1" min="0" data-text-field="end" value="${Number(item.end).toFixed(2)}" />`)}
      </div>
      <div class="row" style="gap:6px;margin:-2px 0 8px">
        <button class="btn small ghost" data-ed="text-start-here">awal = playhead (${edFmt(t)})</button>
        <button class="btn small ghost" data-ed="text-end-here">akhir = playhead</button>
      </div>
      <div class="ed-two">
        ${fieldRow("Posisi X", `<input type="range" min="0" max="1" step="0.01" data-text-field="x" value="${Number(item.x)}" />`)}
        ${fieldRow("Posisi Y", `<input type="range" min="0" max="1" step="0.01" data-text-field="y" value="${Number(item.y)}" />`)}
      </div>
      <div class="ed-two">
        ${fieldRow("Ukuran", `<input type="range" min="24" max="140" step="1" data-text-field="size" value="${Number(item.size)}" />`)}
        ${fieldRow("Rotasi", `<input type="range" min="-30" max="30" step="1" data-text-field="rotation" value="${Number(item.rotation || 0)}" />`)}
      </div>
      <div class="ed-two">
        ${fieldRow("Warna", `<input class="input ed-color" type="color" data-text-field="color" value="${esc(item.color)}" />`)}
        ${fieldRow("Warna garis", `<input class="input ed-color" type="color" data-text-field="stroke_color" value="${esc(item.stroke_color || "#0B0B10")}" />`)}
      </div>
      <div class="ed-two">
        ${fieldRow("Tebal garis", `<input type="range" min="0" max="20" step="1" data-text-field="stroke" value="${Number(item.stroke || 0)}" />`)}
        ${fieldRow("Animasi", `<select class="select" data-text-field="anim">
          ${[["pop", "Pop"], ["fade", "Fade"], ["none", "Tanpa"]]
            .map(([v, l]) => `<option value="${v}" ${item.anim === v ? "selected" : ""}>${l}</option>`)
            .join("")}
        </select>`)}
      </div>
      <label class="switch tiny" style="margin:4px 0">
        <input type="checkbox" data-text-field="box" ${item.box ? "checked" : ""} />
        <span class="track"></span><span>Kotak latar</span>
      </label>
      ${item.box ? `<div class="ed-two">
        ${fieldRow("Warna kotak", `<input class="input ed-color" type="color" data-text-field="box_color" value="${esc(item.box_color || "#000000")}" />`)}
        ${fieldRow("Transparansi", `<input type="range" min="0" max="1" step="0.05" data-text-field="box_opacity" value="${Number(item.box_opacity ?? 0.55)}" />`)}
      </div>` : ""}
      <div class="row" style="gap:14px;margin-top:4px">
        <label class="switch tiny"><input type="checkbox" data-text-field="uppercase" ${item.uppercase ? "checked" : ""} /><span class="track"></span><span>HURUF BESAR</span></label>
        <label class="switch tiny"><input type="checkbox" data-text-field="bold" ${item.bold ? "checked" : ""} /><span class="track"></span><span>Tebal</span></label>
      </div>
      <button class="btn small ghost danger" style="margin-top:10px" data-ed="text-remove">Hapus teks</button>
    </div>
  `;
}

function panelCaptionHtml() {
  const caps = ED.spec.captions || {};
  const lines = ED.spec.subtitles || [];
  return `
    <label class="switch" style="margin-bottom:12px">
      <input type="checkbox" data-caption-field="enabled" ${caps.enabled ? "checked" : ""} />
      <span class="track"></span><span class="tiny">Caption aktif</span>
    </label>
    ${fieldRow("Gaya caption", `<select class="select" data-caption-style>
      ${ED.meta.caption_styles
        .map((s) => `<option value="${s.id}" ${caps.style === s.id ? "selected" : ""}>${esc(s.label)}</option>`)
        .join("")}
    </select>`)}
    <div class="ed-two">
      ${fieldRow("Kata / baris", `<input class="input" type="number" min="1" max="12" data-caption-field="words_per_line" value="${Number(caps.words_per_line || 3)}" />`)}
      ${fieldRow("Ukuran", `<input type="range" min="36" max="140" step="1" data-caption-field="size" value="${Number(caps.size || 86)}" />`)}
    </div>
    ${fieldRow("Posisi vertikal", `<input type="range" min="0.1" max="0.95" step="0.01" data-caption-field="y" value="${Number(caps.y || 0.76)}" />`)}
    <div class="ed-two">
      ${fieldRow("Warna teks", `<input class="input ed-color" type="color" data-caption-field="color" value="${esc(caps.color || "#FFFFFF")}" />`)}
      ${fieldRow("Highlight", `<input class="input ed-color" type="color" data-caption-field="highlight" value="${esc(caps.highlight || "#FFE02B")}" />`)}
    </div>
    <div class="ed-two">
      ${fieldRow("Garis luar", `<input type="range" min="0" max="20" step="1" data-caption-field="stroke" value="${Number(caps.stroke || 9)}" />`)}
      ${fieldRow("Warna garis", `<input class="input ed-color" type="color" data-caption-field="stroke_color" value="${esc(caps.stroke_color || "#0B0B10")}" />`)}
    </div>
    <div class="row" style="gap:14px;margin:6px 0 12px">
      <label class="switch tiny"><input type="checkbox" data-caption-field="uppercase" ${caps.uppercase ? "checked" : ""} /><span class="track"></span><span>HURUF BESAR</span></label>
      <label class="switch tiny"><input type="checkbox" data-caption-field="word_level" ${caps.word_level !== false ? "checked" : ""} /><span class="track"></span><span>Highlight per kata</span></label>
    </div>
    <div class="row" style="gap:6px;flex-wrap:wrap;margin-bottom:12px">
      <button class="btn small primary" data-ed="auto-subtitle">✨ Generate dari transkrip</button>
      <button class="btn small ghost" data-ed="sub-shift" data-delta="-0.2">−0.2s</button>
      <button class="btn small ghost" data-ed="sub-shift" data-delta="0.2">+0.2s</button>
      <button class="btn small ghost danger" data-ed="sub-clear">Kosongkan</button>
    </div>
    <div class="row" style="justify-content:space-between;margin-bottom:6px">
      <span class="tiny dim">${lines.filter((l) => !l.hidden).length} baris · ${lines.length} total</span>
      <span class="tiny dim">klik baris untuk edit</span>
    </div>
    <div class="ed-subs" id="ed-subs">
      ${lines
        .map(
          (line, idx) => `
        <div class="ed-sub ${line.hidden ? "hidden" : ""}" data-sub="${idx}">
          <span class="ed-sub-time mono tiny">${edFmt(line.start, true)}</span>
          <span class="ed-sub-text">${esc(line.text)}</span>
          <button class="ed-sub-btn" data-ed="sub-toggle" data-idx="${idx}" title="Sembunyikan">${line.hidden ? "◌" : "◉"}</button>
        </div>`
        )
        .join("") || `<div class="empty tiny">Belum ada caption. Klik “Generate dari transkrip”.</div>`}
    </div>
  `;
}

function panelLayoutHtml() {
  const spec = ED.spec;
  const bg = spec.background || {};
  const logo = spec.logo || {};
  return `
    ${fieldRow("Layout video", `<select class="select" data-spec="layout">
      ${[["fit", "Video utuh + background"], ["crop", "Crop penuh 9:16"], ["black", "Letterbox hitam"]]
        .map(([v, l]) => `<option value="${v}" ${spec.layout === v ? "selected" : ""}>${l}</option>`)
        .join("")}
    </select>`)}
    ${fieldRow("Fokus crop horizontal", `<input type="range" min="0" max="1" step="0.01" data-spec="crop_focus" value="${Number(spec.crop_focus)}" />`, "0% kiri · 50% tengah · 100% kanan")}
    ${fieldRow("Jenis background", `<select class="select" data-bg="type">
      ${[["blur", "Blur video"], ["gradient", "Gradien warna"], ["color", "Warna solid"]]
        .map(([v, l]) => `<option value="${v}" ${bg.type === v ? "selected" : ""}>${l}</option>`)
        .join("")}
    </select>`)}
    <div class="ed-two">
      ${fieldRow("Warna 1", `<input class="input ed-color" type="color" data-bg="color1" value="${esc(bg.color1 || "#0B0B12")}" />`)}
      ${fieldRow("Warna 2 (gradien)", `<input class="input ed-color" type="color" data-bg="color2" value="${esc(bg.color2 || "#7C5CFF")}" />`)}
    </div>
    <div class="ed-two">
      ${fieldRow("Blur", `<input type="range" min="0" max="60" step="1" data-bg="blur" value="${Number(bg.blur ?? 30)}" />`)}
      ${fieldRow("Redupkan", `<input type="range" min="0" max="0.7" step="0.02" data-bg="darken" value="${Number(bg.darken ?? 0.12)}" />`)}
    </div>
    <div class="ed-divider"></div>
    <h3 class="tiny" style="margin-bottom:8px">Logo / watermark sendiri (opsional)</h3>
    <div class="row" style="gap:8px;margin-bottom:8px">
      <input type="file" id="ed-logo-file" accept="image/png,image/jpeg,image/webp" hidden />
      <button class="btn small" data-ed="logo-upload">${logo.path ? "Ganti logo" : "Unggah logo"}</button>
      ${logo.path ? `<button class="btn small ghost danger" data-ed="logo-remove">Hapus</button>` : ""}
    </div>
    ${logo.path ? `<div class="ed-two">
      ${fieldRow("Posisi X", `<input type="range" min="0" max="1" step="0.01" data-logo="x" value="${Number(logo.x)}" />`)}
      ${fieldRow("Posisi Y", `<input type="range" min="0" max="1" step="0.01" data-logo="y" value="${Number(logo.y)}" />`)}
    </div>
    <div class="ed-two">
      ${fieldRow("Ukuran", `<input type="range" min="0.05" max="1" step="0.01" data-logo="width" value="${Number(logo.width)}" />`)}
      ${fieldRow("Opacity", `<input type="range" min="0.1" max="1" step="0.05" data-logo="opacity" value="${Number(logo.opacity)}" />`)}
    </div>` : `<div class="tiny dim">Tanpa watermark. Logo hanya kalau kamu unggah sendiri. Export dari Clipper tidak pernah menambahkan watermark.</div>`}
    <div class="ed-divider"></div>
    <h3 class="tiny" style="margin-bottom:8px">Transisi</h3>
    <div class="ed-two">
      ${fieldRow("Fade in (detik)", `<input class="input" type="number" min="0" max="3" step="0.1" data-out="fade_in" value="${Number(spec.output.fade_in || 0)}" />`)}
      ${fieldRow("Fade out (detik)", `<input class="input" type="number" min="0" max="3" step="0.1" data-out="fade_out" value="${Number(spec.output.fade_out || 0)}" />`)}
    </div>
  `;
}

function panelExportHtml() {
  const out = ED.spec.output;
  const dur = specDuration(ED.spec);
  const scale = Number(out.height) / 1920;
  const est = Math.round(dur * (1.4 * Math.pow(scale, 1.8) + 0.6) + 12);
  const clip = ED.clip;
  const rendering = clip.status === "rendering";
  return `
    ${fieldRow("Resolusi", `<select class="select" data-out="height">
      ${ED.meta.resolutions.map((r) => `<option value="${r.height}" ${Number(out.height) === r.height ? "selected" : ""}>${esc(r.label)}</option>`).join("")}
    </select>`, "4K = upscale dari sumber (detail tidak bertambah, tapi tajam & lolos syarat platform 4K)")}
    <div class="ed-two">
      ${fieldRow("FPS", `<select class="select" data-out="fps">
        ${[24, 25, 30, 50, 60].map((f) => `<option value="${f}" ${Number(out.fps) === f ? "selected" : ""}>${f} fps</option>`).join("")}
      </select>`)}
      ${fieldRow("Kualitas (CRF)", `<input class="input" type="number" min="14" max="32" data-out="crf" value="${Number(out.crf)}" />`)}
    </div>
    <label class="switch tiny" style="margin:6px 0"><input type="checkbox" data-out="sharpen" ${out.sharpen ? "checked" : ""} /><span class="track"></span><span>Pertajam (unsharp) saat upscale</span></label>
    <label class="switch tiny" style="margin:6px 0"><input type="checkbox" data-out="normalize" ${out.normalize ? "checked" : ""} /><span class="track"></span><span>Normalisasi audio −14 LUFS</span></label>
    <div class="ed-divider"></div>
    <h3 class="tiny" style="margin-bottom:8px">Metadata upload YouTube</h3>
    ${fieldRow("Deskripsi", `<textarea class="input" rows="3" data-clip-meta="description">${esc(clip.description || "")}</textarea>`)}
    ${fieldRow("Hashtag", `<input class="input" data-clip-meta="hashtags" value="${esc(clip.hashtags || "")}" />`)}
    ${fieldRow("Hook (teks overlay pertama)", `<input class="input" data-clip-meta="hook" value="${esc(clip.hook || "")}" />`)}
    <div class="ed-divider"></div>
    <div class="ed-export-summary">
      <div class="stat"><span class="v">${edFmt(dur)}</span><span class="k">durasi</span></div>
      <div class="stat"><span class="v">${out.width}×${out.height}</span><span class="k">resolusi</span></div>
      <div class="stat"><span class="v">≈ ${est}s</span><span class="k">estimasi render</span></div>
    </div>
    <div class="row" style="gap:8px;margin-top:14px;flex-wrap:wrap">
      <button class="btn primary" data-ed="render" ${rendering ? "disabled" : ""}>
        ${rendering ? `<span class="spinner"></span> Merender…` : "Render video"}
      </button>
      <button class="btn ghost" data-ed="download-srt">Unduh subtitle (.srt)</button>
      ${clip.has_file ? `<a class="btn ghost" href="/api/clips/${esc(clip.id)}/download">Unduh video</a>` : ""}
      <button class="btn ghost" data-ed="upload-yt" ${clip.has_file ? "" : "disabled"}>Upload ke Shorts</button>
    </div>
    <div class="tiny dim" style="margin-top:12px">
      Export tanpa watermark, tanpa batas. File disimpan di folder <span class="mono">data/clips/</span>.
    </div>
  `;
}

/* ------------------------------------------------------------------ */
/* binding                                                           */
/* ------------------------------------------------------------------ */
function bindEditorEvents() {
  const video = edEl("ed-video");

  video.addEventListener("timeupdate", () => {
    if (!ED.spec) return;
    const segments = segmentsWithOut(ED.spec.segments);
    const srcTime = video.currentTime;
    // cari segmen aktif menurut waktu sumber
    let active = segments.find((s) => srcTime >= s.start && srcTime <= s.end);
    if (!active) {
      const next = segments.find((s) => s.start > srcTime);
      if (next) {
        video.currentTime = next.start + 0.02;
        return;
      }
      const last = segments[segments.length - 1];
      if (last && srcTime >= last.end) {
        video.pause();
        ED.playing = false;
        ED.time = segments.reduce((sum, s) => sum + s.outDur, 0);
        renderOverlay();
        updatePlayhead();
        return;
      }
      return;
    }
    ED.time = active.outStart + (srcTime - active.start) / Math.max(0.5, Number(active.speed) || 1);
    renderOverlay();
    updatePlayhead();
  });

  video.addEventListener("play", () => {
    ED.playing = true;
    const btn = document.querySelector('[data-ed="play"]');
    if (btn) btn.textContent = "❚❚";
  });
  video.addEventListener("pause", () => {
    ED.playing = false;
    const btn = document.querySelector('[data-ed="play"]');
    if (btn) btn.textContent = "▶";
  });

  const title = edEl("ed-title");
  if (title) {
    const handler = debounce(async () => {
      try {
        await api(`/api/clips/${ED.clipId}`, { method: "PATCH", body: { title: title.value } });
        ED.clip.title = title.value;
        toast("Judul tersimpan", "ok");
      } catch (err) {
        toast(err.message, "bad");
      }
    }, 800);
    title.addEventListener("input", handler);
  }

  const seek = edEl("ed-seek");
  if (seek) {
    seek.addEventListener("click", (e) => {
      const rect = seek.getBoundingClientRect();
      const ratio = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
      video.pause();
      seekTo(ratio * specDuration(ED.spec));
    });
  }

  const template = edEl("ed-template");
  if (template) {
    template.addEventListener("change", async () => {
      if (!template.value) return;
      try {
        const res = await api(`/api/clips/${ED.clipId}/spec/template`, {
          method: "POST",
          body: { template: template.value },
        });
        ED.spec = res.spec;
        ED.summary = res.summary;
        ED.dirty = true;
        renderTimeline();
        renderPanel();
        refreshChrome();
        renderOverlay();
        toast("Template diterapkan — klik Render untuk menyimpan hasil", "ok");
      } catch (err) {
        toast(err.message, "bad");
      } finally {
        template.value = "";
      }
    });
  }

  const res = edEl("ed-resolution");
  if (res) {
    res.addEventListener("change", () => {
      ED.spec.output.height = Number(res.value);
      ED.spec.output.width = Math.round((Number(res.value) * 9) / 16 / 2) * 2;
      scheduleSave(true);
      renderPanel();
      refreshChrome();
      toast(Number(res.value) >= 3840 ? "Export 4K dipilih (render lebih lama)" : "Resolusi diperbarui");
    });
  }

  $$("[data-ed-tab]").forEach((btn) => {
    btn.addEventListener("click", () => {
      ED.tab = btn.dataset.edTab;
      $$("[data-ed-tab]").forEach((b) => b.classList.toggle("active", b === btn));
      renderPanel();
    });
  });
}

function bindPanelEvents() {
  // teks
  $$("[data-ed-text]").forEach((el) => {
    el.addEventListener("click", () => {
      const id = el.dataset.edText;
      ED.selectedText = ED.selectedText === id ? null : id;
      renderPanel();
    });
  });
  $$("[data-text-field]").forEach((el) => {
    el.addEventListener("input", () => applyTextField(el));
    el.addEventListener("change", () => applyTextField(el));
  });
  // caption
  $$("[data-caption-field]").forEach((el) => {
    el.addEventListener("change", () => applyCaptionField(el));
  });
  const styleSel = document.querySelector("[data-caption-style]");
  if (styleSel) {
    styleSel.addEventListener("change", () => {
      const style = ED.meta.caption_styles.find((s) => s.id === styleSel.value);
      if (!style) return;
      Object.assign(ED.spec.captions, {
        style: style.id,
        size: style.size,
        uppercase: style.uppercase,
        color: style.color,
        highlight: style.highlight,
        stroke: style.stroke,
        stroke_color: style.stroke_color,
        y: style.y,
        words_per_line: style.words_per_line,
      });
      scheduleSave();
      renderPanel();
      renderOverlay();
    });
  }
  // layout/spec
  $$("[data-spec]").forEach((el) => {
    el.addEventListener("input", () => {
      const key = el.dataset.spec;
      ED.spec[key] = el.type === "range" ? Number(el.value) : el.value;
      scheduleSave();
      renderOverlay();
      refreshChrome();
    });
  });
  $$("[data-bg]").forEach((el) => {
    el.addEventListener("input", () => {
      ED.spec.background[el.dataset.bg] = el.type === "range" ? Number(el.value) : el.value;
      scheduleSave();
    });
  });
  $$("[data-logo]").forEach((el) => {
    el.addEventListener("input", () => {
      ED.spec.logo[el.dataset.logo] = Number(el.value);
      scheduleSave();
      renderOverlay();
    });
  });
  $$("[data-out]").forEach((el) => {
    el.addEventListener("change", () => {
      const key = el.dataset.out;
      if (el.type === "checkbox") ED.spec.output[key] = el.checked;
      else if (el.type === "number" || el.type === "range") ED.spec.output[key] = Number(el.value);
      else ED.spec.output[key] = el.value;
      if (key === "height") ED.spec.output.width = Math.round((Number(el.value) * 9) / 16 / 2) * 2;
      scheduleSave(true);
      refreshChrome();
      if (key === "height") renderPanel();
    });
  });
  // metadata clip (deskripsi/hashtag/hook)
  $$("[data-clip-meta]").forEach((el) => {
    const handler = debounce(async () => {
      try {
        await api(`/api/clips/${ED.clipId}`, { method: "PATCH", body: { [el.dataset.clipMeta]: el.value } });
        ED.clip[el.dataset.clipMeta] = el.value;
        toast("Metadata tersimpan", "ok");
      } catch (err) {
        toast(err.message, "bad");
      }
    }, 900);
    el.addEventListener("input", handler);
  });
  // subtitle list
  $$(".ed-sub").forEach((el) => {
    el.addEventListener("click", (e) => {
      if (e.target.closest("[data-ed]")) return;
      const idx = Number(el.dataset.sub);
      const line = ED.spec.subtitles[idx];
      if (!line) return;
      const text = prompt("Teks caption:", line.text);
      if (text === null) return;
      line.text = text;
      scheduleSave();
      renderPanel();
      renderOverlay();
    });
  });
}

function applyTextField(el) {
  const item = (ED.spec.texts || []).find((t) => t.id === ED.selectedText);
  if (!item) return;
  const field = el.dataset.textField;
  let value;
  if (el.type === "checkbox") value = el.checked;
  else if (el.type === "range" || el.type === "number") value = Number(el.value);
  else value = el.value;
  item[field] = value;
  scheduleSave();
  renderOverlay();
  refreshChrome();
  if (["box", "anim"].includes(field)) renderPanel();
}

function applyCaptionField(el) {
  const field = el.dataset.captionField;
  let value;
  if (el.type === "checkbox") value = el.checked;
  else if (el.type === "number") value = Number(el.value);
  else value = el.value;
  ED.spec.captions[field] = value;
  scheduleSave();
  renderOverlay();
  refreshChrome();
}

/* ------------------------------------------------------------------ */
/* aksi tombol editor                                                */
/* ------------------------------------------------------------------ */
async function editorAction(action, target) {
  const spec = ED.spec;
  if (!spec) return;

  if (action === "play") {
    const video = edEl("ed-video");
    if (!video) return;
    if (video.paused) {
      if (ED.time >= specDuration(spec) - 0.1) seekTo(0);
      video.play().catch(() => toast("Preview tidak bisa diputar (format sumber)", "bad"));
    } else video.pause();
  } else if (action === "mark-in" || action === "mark-out") {
    const seg = spec.segments[ED.selectedSegment];
    if (!seg) return;
    const srcTime = outToSrc(ED.time, spec.segments);
    if (action === "mark-in") seg.start = Math.min(Number(srcTime.toFixed(2)), seg.end - 0.3);
    else seg.end = Math.max(Number(srcTime.toFixed(2)), seg.start + 0.3);
    scheduleSave();
    renderTimeline();
  } else if (action === "seg-add") {
    const last = spec.segments[spec.segments.length - 1];
    const start = Math.min((ED.source.duration || 60) - 1, Number(last ? last.end : 0) + 0.5);
    spec.segments.push({ start: Number(start.toFixed(2)), end: Number(Math.min(start + 8, ED.source.duration || start + 8).toFixed(2)), speed: 1 });
    ED.selectedSegment = spec.segments.length - 1;
    scheduleSave();
    renderTimeline();
    renderPanel();
  } else if (action === "seg-remove") {
    if (spec.segments.length <= 1) return;
    spec.segments.splice(ED.selectedSegment, 1);
    ED.selectedSegment = Math.max(0, ED.selectedSegment - 1);
    scheduleSave();
    renderTimeline();
    renderPanel();
  } else if (action === "seg-split") {
    const idx = ED.selectedSegment;
    const seg = spec.segments[idx];
    const splitSrc = outToSrc(ED.time, spec.segments);
    if (!seg || splitSrc <= seg.start + 0.3 || splitSrc >= seg.end - 0.3) {
      toast("Playhead harus di dalam segmen untuk memecah", "bad");
      return;
    }
    spec.segments.splice(idx, 1, { ...seg, end: Number(splitSrc.toFixed(2)) },
      { ...seg, start: Number(splitSrc.toFixed(2)) });
    scheduleSave();
    renderTimeline();
    renderPanel();
  } else if (action === "text-add") {
    const start = Number(ED.time.toFixed(2));
    const item = {
      id: "t" + Date.now().toString().slice(-6),
      text: "TEKS BARU",
      start,
      end: Number(Math.min(specDuration(spec), start + 3).toFixed(2)),
      x: 0.5, y: 0.3, size: 72, color: "#FFFFFF", stroke: 8, stroke_color: "#0B0B10",
      box: false, box_color: "#000000", box_opacity: 0.55, bold: true, uppercase: true,
      rotation: 0, anim: "pop", align: "center",
    };
    spec.texts.push(item);
    ED.selectedText = item.id;
    scheduleSave();
    renderPanel();
    renderOverlay();
  } else if (action === "text-remove") {
    spec.texts = spec.texts.filter((t) => t.id !== ED.selectedText);
    ED.selectedText = null;
    scheduleSave();
    renderPanel();
    renderOverlay();
  } else if (action === "text-start-here" || action === "text-end-here") {
    const item = spec.texts.find((t) => t.id === ED.selectedText);
    if (!item) return;
    if (action === "text-start-here") item.start = Number(ED.time.toFixed(2));
    else item.end = Math.max(item.start + 0.3, Number(ED.time.toFixed(2)));
    scheduleSave();
    renderPanel();
    renderOverlay();
  } else if (action === "auto-subtitle") {
    await runAutoSubtitle();
  } else if (action === "sub-shift") {
    const delta = Number(target.dataset.delta || 0);
    try {
      const res = await api(`/api/clips/${ED.clipId}/subtitles`, {
        method: "POST",
        body: { action: "shift", seconds: delta },
      });
      ED.spec = res.spec;
      renderOverlay();
      renderPanel();
      refreshChrome();
      toast(`Caption digeser ${delta > 0 ? "+" : ""}${delta}s`, "ok");
    } catch (err) {
      toast(err.message, "bad");
    }
  } else if (action === "sub-clear") {
    spec.subtitles = [];
    scheduleSave();
    renderPanel();
    renderOverlay();
  } else if (action === "sub-toggle") {
    const idx = Number(target.dataset.idx);
    if (spec.subtitles[idx]) {
      spec.subtitles[idx].hidden = !spec.subtitles[idx].hidden;
      scheduleSave();
      renderPanel();
      renderOverlay();
    }
  } else if (action === "logo-upload") {
    const input = edEl("ed-logo-file");
    if (input) input.click();
  } else if (action === "logo-remove") {
    spec.logo = { path: "", x: 0.5, y: 0.93, width: 0.22, opacity: 1, start: 0, end: 0 };
    scheduleSave();
    renderPanel();
    renderOverlay();
  } else if (action === "download-srt") {
    const lines = (spec.subtitles || []).filter((l) => !l.hidden);
    if (!lines.length) {
      toast("Belum ada caption", "bad");
      return;
    }
    const rows = lines.map((l, i) => `${i + 1}\n${srtTs(l.start)} --> ${srtTs(l.end)}\n${l.text}\n`);
    downloadText(rows.join("\n"), `${(ED.clip.title || "clip").slice(0, 40)}.srt`);
  } else if (action === "upload-yt") {
    try {
      await api(`/api/clips/${ED.clipId}/upload`, { method: "POST", body: {} });
      toast("Upload ke YouTube dimulai", "ok");
    } catch (err) {
      toast(err.message, "bad");
    }
  } else if (action === "render") {
    await renderFromEditor();
  }
}

function srtTs(seconds) {
  const s = Math.max(0, Number(seconds) || 0);
  const ms = Math.round((s % 1) * 1000);
  const total = Math.floor(s);
  return `${String(Math.floor(total / 3600)).padStart(2, "0")}:${String(Math.floor((total % 3600) / 60)).padStart(2, "0")}:${String(total % 60).padStart(2, "0")},${String(ms).padStart(3, "0")}`;
}

function downloadText(text, filename) {
  const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 3000);
}

async function runAutoSubtitle() {
  const btn = document.querySelector('[data-ed="auto-subtitle"]');
  if (btn) {
    btn.dataset.label = btn.textContent.trim();
    btn.disabled = true;
    btn.innerHTML = `<span class="spinner"></span>`;
  }
  try {
    const res = await api(`/api/clips/${ED.clipId}/subtitles`, {
      method: "POST",
      body: { action: "regenerate", words_per_line: ED.spec.captions.words_per_line, word_level: true },
    });
    ED.spec = res.spec;
    ED.summary = res.summary;
    ED.dirty = false;
    ED.tab = "caption";
    $$("[data-ed-tab]").forEach((b) => b.classList.toggle("active", b.dataset.edTab === "caption"));
    renderPanel();
    renderTimeline();
    renderOverlay();
    refreshChrome();
    toast(`${(ED.spec.subtitles || []).length} baris caption dibuat dari transkrip`, "ok");
  } catch (err) {
    toast(err.message, "bad");
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = btn.dataset.label || "✨ Auto subtitle";
    }
  }
}

async function renderFromEditor() {
  const btn = document.querySelector('[data-ed="render"]');
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = `<span class="spinner"></span> Merender…`;
  }
  try {
    await saveSpec();
    await api(`/api/clips/${ED.clipId}/render`, { method: "POST" });
    showRenderBadge("Render dimulai…", 0);
    pollRenderStatus();
  } catch (err) {
    toast(err.message, "bad");
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = "Render video";
    }
  }
}

function showRenderBadge(text, progress) {
  const badge = edEl("ed-render-badge");
  if (!badge) return;
  badge.hidden = false;
  badge.innerHTML = `<div>${esc(text)}</div>${
    progress >= 0 ? `<div class="bar thin"><i style="width:${progress}%"></i></div>` : ""
  }`;
}

function pollRenderStatus() {
  stopPolling();
  state.poll = setInterval(async () => {
    try {
      const clip = await api(`/api/clips/${ED.clipId}`);
      const pct = Math.round((clip.progress || 0) * 100);
      if (clip.status === "rendering") {
        showRenderBadge(clip.message || "Merender…", pct);
        const btn = document.querySelector('[data-ed="render"]');
        if (btn) btn.innerHTML = `<span class="spinner"></span> ${pct}%`;
      } else {
        stopPolling();
        const badge = edEl("ed-render-badge");
        if (clip.status === "rendered") {
          ED.clip = { ...ED.clip, has_file: true, status: "rendered" };
          showRenderBadge("✓ Render selesai", 100);
          setTimeout(() => {
            if (badge) badge.hidden = true;
          }, 2500);
          toast("Render selesai — preview memakai hasil baru", "ok");
          ED.clip.has_file = true;
          renderPreviewSource();
          renderPanel();
          const dl = edEl("ed-download");
          if (dl) dl.hidden = false;
        } else if (clip.status === "error") {
          showRenderBadge("✗ " + (clip.error || "Render gagal"), 100);
          toast(clip.error || "Render gagal", "bad");
          const btn = document.querySelector('[data-ed="render"]');
          if (btn) {
            btn.disabled = false;
            btn.innerHTML = "Render video";
          }
        }
      }
    } catch (err) {
      stopPolling();
    }
  }, 1500);
}

/* ------------------------------------------------------------------ */
/* route entry                                                       */
/* ------------------------------------------------------------------ */
async function renderEditorRoute(clipId) {
  const view = $("#view");
  view.innerHTML = `<div class="empty"><div class="spinner"></div> Memuat editor…</div>`;
  try {
    await renderEditor(clipId);
    const upload = edEl("ed-logo-file");
    if (upload) {
      upload.addEventListener("change", async () => {
        const file = upload.files && upload.files[0];
        if (!file) return;
        const form = new FormData();
        form.append("file", file);
        try {
          const res = await fetch(`/api/clips/${ED.clipId}/assets`, { method: "POST", body: form });
          const data = await res.json();
          if (!res.ok) throw new Error(data.detail || "Upload logo gagal");
          ED.spec.logo = { ...ED.spec.logo, path: data.path, start: 0, end: 0 };
          scheduleSave(true);
          renderPanel();
          renderOverlay();
          toast("Logo ditambahkan", "ok");
        } catch (err) {
          toast(err.message, "bad");
        }
      });
    }
  } catch (err) {
    view.innerHTML = `<div class="card"><h2>Gagal membuka editor</h2><p class="sub">${esc(err.message)}</p>
      <a class="btn" href="#/projects">← Kembali</a></div>`;
  }
}

/* delegasi klik khusus editor */
document.addEventListener("click", async (e) => {
  const tab = e.target.closest("[data-ed-tab]");
  if (tab) return; // ditangani bindEditorEvents
  const btn = e.target.closest("[data-ed]");
  if (!btn) return;
  e.preventDefault();
  try {
    await editorAction(btn.dataset.ed, btn);
  } catch (err) {
    toast(err.message, "bad");
  }
});
