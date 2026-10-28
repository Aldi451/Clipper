/* Clipper — frontend tanpa framework, full offline. */
"use strict";

const state = {
  health: null,
  settings: {},
  route: "new",
  param: "",
  job: null,
  clips: [],
  poll: null,
  yt: null,
  editing: null,
  busy: {},
};

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

/* ------------------------------------------------------------------ */
/* util                                                               */
/* ------------------------------------------------------------------ */
function esc(value) {
  return String(value == null ? "" : value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function fmtTime(seconds, withMs = false) {
  const s = Math.max(0, Number(seconds) || 0);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = Math.floor(s % 60);
  const base = (h ? String(h) + ":" : "") + String(m).padStart(2, "0") + ":" + String(sec).padStart(2, "0");
  return withMs ? base + "." + String(Math.floor((s % 1) * 10)) : base;
}

function fmtDur(seconds) {
  const s = Math.round(Number(seconds) || 0);
  if (s < 60) return s + " detik";
  const m = Math.floor(s / 60);
  const rest = s % 60;
  return m + " mnt" + (rest ? " " + rest + " dtk" : "");
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function toast(message, kind = "") {
  const root = $("#toasts");
  const el = document.createElement("div");
  el.className = "toast " + kind;
  el.textContent = message;
  root.appendChild(el);
  setTimeout(() => {
    el.style.opacity = "0";
    el.style.transform = "translateY(6px)";
    el.style.transition = "0.25s";
    setTimeout(() => el.remove(), 300);
  }, 4200);
}

async function api(path, options = {}) {
  const opts = { method: options.method || "GET", headers: {} };
  if (options.body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(options.body);
  }
  const res = await fetch(path, opts);
  let data = null;
  const text = await res.text();
  if (text) {
    try {
      data = JSON.parse(text);
    } catch (err) {
      data = { detail: text };
    }
  }
  if (!res.ok) {
    const detail = data && (data.detail || data.message);
    throw new Error(typeof detail === "string" ? detail : "Permintaan gagal (" + res.status + ")");
  }
  return data;
}

function debounce(fn, wait = 500) {
  let timer = null;
  return function (...args) {
    clearTimeout(timer);
    timer = setTimeout(() => fn.apply(this, args), wait);
  };
}

function stopPolling() {
  if (state.poll) {
    clearInterval(state.poll);
    state.poll = null;
  }
}

function startPolling(fn, interval = 1600) {
  stopPolling();
  state.poll = setInterval(fn, interval);
}

/* ------------------------------------------------------------------ */
/* health & settings                                                  */
/* ------------------------------------------------------------------ */
async function loadHealth() {
  try {
    state.health = await api("/api/health");
    applyHealth();
  } catch (err) {
    console.warn(err);
  }
}

function applyHealth() {
  const h = state.health;
  if (!h) return;
  const ff = $("#chip-ffmpeg");
  const wh = $("#chip-whisper");
  const yt = $("#chip-youtube");
  if (ff) {
    const dot = $(".dot", ff);
    dot.className = "dot " + (h.ffmpeg ? "on" : "off");
    ff.title = h.ffmpeg ? "ffmpeg siap: " + h.ffmpeg_path : "ffmpeg tidak ditemukan";
  }
  if (wh) {
    const dot = $(".dot", wh);
    dot.className = "dot " + (h.whisper ? "on" : "warn");
    wh.title = h.whisper ? "faster-whisper terpasang" : "faster-whisper belum terpasang (mode potong otomatis)";
  }
  if (yt) {
    const dot = $(".dot", yt);
    dot.className = "dot " + (h.youtube ? "on" : "off");
    yt.title = h.youtube ? "YouTube terhubung" : "YouTube belum terhubung";
  }
  const version = $("#app-version");
  if (version) version.textContent = h.version || "—";
}

async function loadSettings() {
  try {
    state.settings = await api("/api/settings");
  } catch (err) {
    console.warn(err);
  }
}

async function saveSettings(patch) {
  try {
    state.settings = await api("/api/settings", { method: "PATCH", body: patch });
    return true;
  } catch (err) {
    toast("Gagal simpan pengaturan: " + err.message, "bad");
    return false;
  }
}

const saveSettingsDebounced = debounce((patch) => saveSettings(patch), 600);

/* ------------------------------------------------------------------ */
/* router                                                             */
/* ------------------------------------------------------------------ */
function parseRoute() {
  const hash = location.hash.replace(/^#\/?/, "");
  const parts = hash.split("/").filter(Boolean);
  if (!parts.length) return { route: "new", param: "" };
  if (parts[0] === "project" && parts[1]) return { route: "project", param: parts[1] };
  if (["new", "projects", "youtube", "settings"].includes(parts[0])) {
    return { route: parts[0], param: "" };
  }
  return { route: "new", param: "" };
}

async function renderRoute() {
  stopPolling();
  closeModal();
  const { route, param } = parseRoute();
  state.route = route;
  state.param = param;
  $$(".nav a").forEach((a) => a.classList.toggle("active", a.dataset.nav === route));
  const view = $("#view");
  view.innerHTML = `<div class="empty"><div class="spinner"></div> Memuat…</div>`;
  try {
    if (route === "new") await renderNew();
    else if (route === "projects") await renderProjects();
    else if (route === "project") await renderProject(param);
    else if (route === "youtube") await renderYouTube();
    else if (route === "settings") await renderSettings();
  } catch (err) {
    view.innerHTML = `<div class="card"><h2>Terjadi kesalahan</h2><p class="sub">${esc(err.message)}</p>
      <button class="btn" onclick="location.reload()">Muat ulang</button></div>`;
  }
}

window.addEventListener("hashchange", renderRoute);

/* ------------------------------------------------------------------ */
/* view: buat clip                                                    */
/* ------------------------------------------------------------------ */
function quickSettingsHtml() {
  const s = state.settings;
  return `
    <div class="field">
      <label>Jumlah clip</label>
      <input class="input" type="number" min="1" max="20" data-setting="target_clips" value="${esc(s.target_clips)}" />
    </div>
    <div class="field">
      <label>Durasi min (detik)</label>
      <input class="input" type="number" min="5" max="180" data-setting="min_clip_seconds" value="${esc(s.min_clip_seconds)}" />
    </div>
    <div class="field">
      <label>Durasi maks (detik)</label>
      <input class="input" type="number" min="10" max="180" data-setting="max_clip_seconds" value="${esc(s.max_clip_seconds)}" />
    </div>
    <div class="field">
      <label>Gaya video</label>
      <select class="select" data-setting="layout">
        <option value="fit" ${s.layout === "fit" ? "selected" : ""}>Blur background (video utuh)</option>
        <option value="crop" ${s.layout === "crop" ? "selected" : ""}>Crop penuh 9:16 (fokus orang)</option>
      </select>
    </div>
  `;
}

async function renderNew() {
  const view = $("#view");
  view.innerHTML = `
    <section class="hero">
      <h1>Video panjang jadi <span class="g">Shorts otomatis</span>.</h1>
      <p class="sub">
        Tempel link YouTube / TikTok / Instagram / X, atau lampirkan file dari komputer.
        Clipper transkripsi lokal, cari momen paling menarik, bikin caption karaoke 9:16,
        lalu kirim ke YouTube Shorts kamu — semua jalan di mesin sendiri, tanpa biaya.
      </p>

      <div class="url-bar">
        <input class="input" id="url-input" placeholder="https://www.youtube.com/watch?v=…  atau  https://vt.tiktok.com/…" spellcheck="false" />
        <button class="btn primary" id="btn-url">Buat Clip →</button>
      </div>

      <div class="drop" id="drop">
        <div class="ico-lg">⬆</div>
        <div class="big">Tarik &amp; lepas video di sini</div>
        <div class="hint">atau klik untuk pilih file · mp4, mov, mkv, webm, mp3 · tanpa batas durasi</div>
        <div id="upload-progress" style="display:none;margin-top:16px;max-width:420px;margin-inline:auto">
          <div class="bar"><i style="width:0%"></i></div>
          <div class="hint" id="upload-label" style="margin-top:6px"></div>
        </div>
        <input type="file" id="file-input" accept="video/*,audio/*,.mkv,.webm,.mov,.flv,.ts" hidden />
      </div>

      <div class="card tight" style="margin-top:16px">
        <div class="card-title" style="margin-bottom:10px">
          <h3 style="font-size:13px;color:var(--muted);text-transform:uppercase;letter-spacing:.07em">Pengaturan cepat</h3>
          <a href="#/settings" class="tiny">Lainnya →</a>
        </div>
        <div class="quick" id="quick">${quickSettingsHtml()}</div>
      </div>
    </section>

    <section class="card" style="margin-top:18px">
      <div class="card-title">
        <h2>Project terakhir</h2>
        <a href="#/projects" class="tiny">Lihat semua →</a>
      </div>
      <div id="recent"><div class="empty"><div class="spinner"></div></div></div>
    </section>
  `;

  const input = $("#file-input");
  const drop = $("#drop");

  drop.addEventListener("click", () => input.click());
  input.addEventListener("change", () => {
    if (input.files && input.files[0]) uploadFile(input.files[0]);
    input.value = "";
  });
  ["dragenter", "dragover"].forEach((ev) =>
    drop.addEventListener(ev, (e) => {
      e.preventDefault();
      drop.classList.add("drag");
    })
  );
  ["dragleave", "drop"].forEach((ev) =>
    drop.addEventListener(ev, (e) => {
      e.preventDefault();
      drop.classList.remove("drag");
    })
  );
  drop.addEventListener("drop", (e) => {
    const file = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
    if (file) uploadFile(file);
  });

  $("#btn-url").addEventListener("click", submitUrl);
  $("#url-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") submitUrl();
  });

  $$("#quick [data-setting]").forEach((el) => {
    el.addEventListener("change", () => {
      const raw = el.value;
      const value = el.type === "number" ? Number(raw) : raw;
      saveSettings({ [el.dataset.setting]: value });
      if (el.dataset.setting === "layout") toast("Gaya video disimpan");
    });
  });

  loadRecent();
  startPolling(loadRecent, 3500);
}

async function submitUrl() {
  const el = $("#url-input");
  const url = (el.value || "").trim();
  if (!url) {
    el.focus();
    return;
  }
  const btn = $("#btn-url");
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner"></span> Menyiapkan…`;
  try {
    const job = await api("/api/jobs/url", { method: "POST", body: { url } });
    toast("Video masuk antrean. Menganalisa momen terbaik…", "ok");
    location.hash = "#/project/" + job.id;
  } catch (err) {
    toast(err.message, "bad");
    btn.disabled = false;
    btn.innerHTML = "Buat Clip →";
  }
}

function uploadFile(file) {
  const bar = $("#upload-progress");
  const fill = $("#upload-progress .bar > i");
  const label = $("#upload-label");
  if (!bar) return;
  bar.style.display = "block";
  fill.style.width = "0%";
  label.textContent = "Mengunggah " + file.name + " (" + (file.size / 1048576).toFixed(1) + " MB)…";

  const form = new FormData();
  form.append("file", file);
  form.append("title", file.name.replace(/\.[^.]+$/, ""));

  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/jobs/upload");
  xhr.upload.onprogress = (e) => {
    if (!e.lengthComputable) return;
    const pct = (e.loaded / e.total) * 100;
    fill.style.width = pct.toFixed(1) + "%";
    label.textContent = "Mengunggah " + pct.toFixed(0) + "% · " + (e.loaded / 1048576).toFixed(1) + " / " + (e.total / 1048576).toFixed(1) + " MB";
  };
  xhr.onload = () => {
    fill.style.width = "100%";
    if (xhr.status >= 200 && xhr.status < 300) {
      let job = null;
      try {
        job = JSON.parse(xhr.responseText);
      } catch (err) {}
      label.textContent = "Upload selesai — mulai analisa…";
      toast("File terunggah. Menganalisa momen terbaik…", "ok");
      if (job && job.id) location.hash = "#/project/" + job.id;
      else loadRecent();
    } else {
      let msg = "Upload gagal";
      try {
        msg = JSON.parse(xhr.responseText).detail || msg;
      } catch (err) {}
      toast(msg, "bad");
      label.textContent = msg;
    }
    setTimeout(() => {
      bar.style.display = "none";
    }, 1500);
  };
  xhr.onerror = () => {
    toast("Koneksi upload terputus", "bad");
    label.textContent = "Gagal upload";
  };
  xhr.send(form);
}

function statusTag(job) {
  const map = {
    queued: ["Antrean", ""],
    running: ["Diproses", "run"],
    done: ["Selesai", "done"],
    error: ["Gagal", "err"],
  };
  const [label, cls] = map[job.status] || [job.status, ""];
  return `<span class="tag ${cls}">${esc(label)}</span>`;
}

function jobCardHtml(job) {
  const progress = Math.round((job.progress || 0) * 100);
  const thumb = job.thumb_clip
    ? `<img src="/api/clips/${esc(job.thumb_clip)}/thumb?v=${Math.round(job.updated_at || 0)}" onerror="this.remove()" />`
    : `<span>${job.kind === "url" ? "🔗" : "🎬"}</span>`;
  return `
    <div class="project" data-action="open-job" data-id="${esc(job.id)}">
      <div class="pthumb">${thumb}</div>
      <div class="pbody">
        <div class="ptitle">${esc(job.title || job.source || "Tanpa judul")}</div>
        <div class="meta-row">
          ${statusTag(job)}
          ${job.clip_count ? `<span>${job.clip_count} clip</span>` : ""}
          ${job.uploaded_count ? `<span class="tag up">${job.uploaded_count} upload</span>` : ""}
          ${job.duration ? `<span>${fmtDur(job.duration)}</span>` : ""}
        </div>
        ${
          job.status === "running" || job.status === "queued"
            ? `<div class="bar thin"><i style="width:${progress}%"></i></div>
               <div class="tiny dim">${esc(job.message || job.stage || "")}</div>`
            : `<div class="tiny dim">${new Date(job.created_at * 1000).toLocaleString("id-ID", { dateStyle: "medium", timeStyle: "short" })}</div>`
        }
      </div>
    </div>`;
}

async function loadRecent() {
  const box = $("#recent");
  if (!box) return;
  try {
    const jobs = await api("/api/jobs?limit=6");
    state.jobs = jobs;
    if (!jobs.length) {
      box.innerHTML = `<div class="empty"><div class="big">✂️</div>Belum ada project. Tempel link atau lampirkan video di atas.</div>`;
      return;
    }
    box.innerHTML = `<div class="projects">${jobs.map(jobCardHtml).join("")}</div>`;
  } catch (err) {
    box.innerHTML = `<div class="empty">${esc(err.message)}</div>`;
  }
}

/* ------------------------------------------------------------------ */
/* view: semua project                                                */
/* ------------------------------------------------------------------ */
async function renderProjects() {
  const view = $("#view");
  const jobs = await api("/api/jobs?limit=100");
  state.jobs = jobs;
  view.innerHTML = `
    <div class="page-head">
      <div>
        <h1>Project</h1>
        <p class="sub">Semua video yang pernah diproses. Data disimpan lokal di folder <span class="mono">data/</span>.</p>
      </div>
      <button class="btn primary" data-action="new-job">+ Project baru</button>
    </div>
    ${
      jobs.length
        ? `<div class="projects">${jobs.map(jobCardHtml).join("")}</div>`
        : `<div class="card"><div class="empty"><div class="big">🎬</div>Belum ada project.<br /><br /><button class="btn primary" data-action="new-job">Mulai dari link atau file</button></div></div>`
    }
  `;
  startPolling(async () => {
    try {
      const fresh = await api("/api/jobs?limit=100");
      const snapshot = JSON.stringify(fresh.map((j) => [j.id, j.status, Math.round(j.progress * 100), j.clip_count]));
      if (snapshot !== JSON.stringify(state.jobs.map((j) => [j.id, j.status, Math.round(j.progress * 100), j.clip_count]))) {
        state.jobs = fresh;
        await renderProjects();
      }
    } catch (err) {}
  }, 3000);
}

/* ------------------------------------------------------------------ */
/* view: detail project                                               */
/* ------------------------------------------------------------------ */
function clipStatusTag(clip) {
  const map = {
    suggested: ["Siap dirender", ""],
    rendering: ["Merender", "run"],
    rendered: ["Siap upload", "done"],
    uploading: ["Upload", "run"],
    uploaded: ["Terupload", "up"],
    error: ["Error", "err"],
  };
  const [label, cls] = map[clip.status] || [clip.status, ""];
  return `<span class="tag ${cls}">${esc(label)}</span>`;
}

function clipCardHtml(clip) {
  const thumb = clip.thumb
    ? `<img src="/api/clips/${esc(clip.id)}/thumb?v=${Math.round(clip.updated_at)}" onerror="this.style.display='none'" />`
    : `<span>${clip.status === "rendered" || clip.status === "uploaded" ? "🎞️" : "✂️"}</span>`;
  const active = clip.status === "rendering" || clip.status === "uploading";
  const progress = Math.round((clip.progress || 0) * 100);
  const score = Number(clip.score || 0);
  const scoreText = score > 0 && score <= 100 ? Math.round(score) : Math.round(Math.min(100, score / 1.2));
  return `
    <article class="clip" data-clip="${esc(clip.id)}">
      <div class="clip-head">
        <div class="clip-thumb" data-action="edit" data-id="${esc(clip.id)}">
          ${thumb}<div class="play">▶</div>
        </div>
        <div class="clip-info">
          <textarea class="clip-title" rows="2" data-field="title" data-id="${esc(clip.id)}"
            placeholder="Judul clip…">${esc(clip.title)}</textarea>
          <div class="meta-row">
            <span class="score">skor ${scoreText}</span>
            <span>${fmtTime(clip.start)} → ${fmtTime(clip.start + clip.duration)}</span>
            <span>${fmtDur(clip.duration)}</span>
          </div>
          <div class="pill-row">${clipStatusTag(clip)}${clip.meta && clip.meta.dirty ? '<span class="tag">perlu render ulang</span>' : ""}${clip.yt_url ? `<a class="tag up" href="${esc(clip.yt_url)}" target="_blank" rel="noopener">buka di YT ↗</a>` : ""}</div>
          <div class="tiny dim">${esc(clip.hook || "")}</div>
        </div>
      </div>
      <div class="clip-body">
        ${active || clip.error ? `<div class="status-line"><span>${esc(clip.message || "")}</span><span>${active ? progress + "%" : ""}</span></div>
          <div class="bar thin ${clip.status === "error" ? "err" : ""}"><i style="width:${clip.status === "error" ? 100 : progress}%"></i></div>
          ${clip.error ? `<div class="tiny" style="color:#fca5a5">${esc(clip.error)}</div>` : ""}` : ""}
        <div class="clip-actions">
          <button class="btn small" data-action="edit" data-id="${esc(clip.id)}">Edit</button>
          <button class="btn small" data-action="render" data-id="${esc(clip.id)}" ${active ? "disabled" : ""}>
            ${clip.has_file ? "Render ulang" : "Render"}
          </button>
          <button class="btn small yt" data-action="upload" data-id="${esc(clip.id)}" ${!clip.has_file || active ? "disabled" : ""}>Upload</button>
          ${clip.has_file ? `<a class="btn small ghost" href="/api/clips/${esc(clip.id)}/download">Unduh</a>` : ""}
          <button class="btn small ghost danger" data-action="delete-clip" data-id="${esc(clip.id)}">Hapus</button>
        </div>
      </div>
    </article>`;
}

async function renderProject(jobId) {
  const view = $("#view");
  const job = await api("/api/jobs/" + encodeURIComponent(jobId));
  const clips = job.clips || [];
  state.job = job;
  state.clips = clips;
  const running = job.status === "running" || job.status === "queued";
  const pct = Math.round((job.progress || 0) * 100);

  view.innerHTML = `
    <div class="page-head">
      <div style="min-width:0">
        <div class="tiny dim" style="margin-bottom:6px"><a href="#/projects">← Project</a></div>
        <h1 style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
          <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:min(60vw,600px)">${esc(job.title || job.source || "Project")}</span>
          ${statusTag(job)}
        </h1>
        <p class="sub">
          ${job.kind === "url" ? "Dari link · " : "File · "}
          ${job.duration ? fmtDur(job.duration) + " · " : ""}
          ${job.width ? job.width + "×" + job.height : ""}
          ${job.meta && job.meta.language ? " · bahasa: " + esc(job.meta.language) : ""}
        </p>
      </div>
      <div class="row">
        <button class="btn" data-action="render-all" ${!clips.length || running ? "disabled" : ""}>Render semua</button>
        <button class="btn yt" data-action="upload-all" ${!clips.some((c) => c.has_file) ? "disabled" : ""}>Upload semua</button>
        <button class="btn ghost danger" data-action="delete-job" data-id="${esc(job.id)}">Hapus project</button>
      </div>
    </div>

    ${
      running || job.error
        ? `<div class="card tight" style="margin-bottom:16px">
            <div class="status-line">
              <span>${esc(job.message || job.stage || "Memproses…")}</span>
              <span class="mono">${pct}%</span>
            </div>
            <div class="bar ${job.status === "error" ? "err" : ""}"><i style="width:${job.status === "error" ? 100 : pct}%"></i></div>
            ${job.error ? `<p class="tiny" style="color:#fca5a5;margin:10px 0 0">${esc(job.error)}</p>
              <button class="btn small" data-action="retry-job" data-id="${esc(job.id)}" style="margin-top:10px">Coba lagi</button>` : ""}
          </div>`
        : ""
    }

    <div class="card tight" style="margin-bottom:18px">
      <div class="row" style="justify-content:space-between">
        <div class="row" style="gap:18px">
          <div class="stat"><span class="v">${clips.length}</span><span class="k">clip</span></div>
          <div class="stat"><span class="v">${clips.filter((c) => c.has_file).length}</span><span class="k">dirender</span></div>
          <div class="stat"><span class="v">${clips.filter((c) => c.status === "uploaded").length}</span><span class="k">terupload</span></div>
        </div>
        <div class="row">
          <button class="btn small ghost" data-action="add-manual">+ Clip manual</button>
          ${job.source_path ? `<a class="btn small ghost" href="/api/jobs/${esc(job.id)}/source" target="_blank" rel="noopener">Lihat sumber</a>` : ""}
        </div>
      </div>
    </div>

    <div id="clips">
      ${
        clips.length
          ? `<div class="clips">${clips.map(clipCardHtml).join("")}</div>`
          : `<div class="card"><div class="empty"><div class="big">🔍</div>
              ${running ? "Sedang mencari momen terbaik…" : "Belum ada clip. Klik coba lagi atau tambah clip manual."}
            </div></div>`
      }
    </div>
  `;

  bindClipFieldHandlers();

  if (clips.some((c) => ["rendering", "uploading"].includes(c.status)) || running) {
    startPolling(async () => {
      if (document.activeElement && document.activeElement.closest && document.activeElement.closest(".clip")) return;
      const fresh = await api("/api/jobs/" + encodeURIComponent(jobId));
      const signature = (j) =>
        JSON.stringify([j.status, Math.round(j.progress * 100), (j.clips || []).map((c) => [c.id, c.status, Math.round(c.progress * 100), c.updated_at])]);
      if (signature(fresh) !== signature(state.job)) {
        state.job = fresh;
        state.clips = fresh.clips;
        await renderProject(jobId);
      }
    }, 1700);
  }
}

function bindClipFieldHandlers() {
  $$("[data-field]").forEach((el) => {
    const handler = debounce(async () => {
      const id = el.dataset.id;
      const field = el.dataset.field;
      try {
        await api("/api/clips/" + id, { method: "PATCH", body: { [field]: el.value } });
        toast("Tersimpan", "ok");
      } catch (err) {
        toast("Gagal simpan: " + err.message, "bad");
      }
    }, 700);
    el.addEventListener("input", handler);
    el.addEventListener("blur", () => {
      if (el.scrollHeight > el.clientHeight) el.style.height = el.scrollHeight + "px";
    });
  });
}

/* ------------------------------------------------------------------ */
/* modal editor                                                       */
/* ------------------------------------------------------------------ */
function closeModal() {
  const root = $("#modal-root");
  if (root) root.innerHTML = "";
  state.editing = null;
}

async function openClipEditor(clipId) {
  const root = $("#modal-root");
  root.innerHTML = `<div class="modal-back"><div class="modal"><div class="empty"><div class="spinner"></div></div></div></div>`;
  let clip;
  try {
    clip = await api("/api/clips/" + clipId);
  } catch (err) {
    root.innerHTML = "";
    toast(err.message, "bad");
    return;
  }
  state.editing = clip;
  const job = state.job && state.job.id === clip.job_id ? state.job : await api("/api/jobs/" + clip.job_id);
  const hasFile = clip.has_file;
  const src = hasFile ? `/api/clips/${clip.id}/video?v=${Math.round(clip.updated_at)}` : `/api/jobs/${clip.job_id}/source#t=${clip.start},${clip.start + clip.duration}`;
  const focus = (clip.meta && typeof clip.meta.focus === "number") ? clip.meta.focus : Number(state.settings.crop_focus || 0.5);
  const captionsOn = clip.meta && clip.meta.captions !== undefined ? !!clip.meta.captions : true;
  const ytHref = clip.yt_url ? `<a class="btn small yt" href="${esc(clip.yt_url)}" target="_blank" rel="noopener">Lihat di YouTube ↗</a>` : "";

  root.innerHTML = `
    <div class="modal-back" data-close="1">
      <div class="modal">
        <div class="modal-head">
          <div>
            <h2>Edit clip</h2>
            <p class="sub">${fmtTime(clip.start)} → ${fmtTime(clip.start + clip.duration)} · ${fmtDur(clip.duration)} · skor ${Math.round(clip.score)}</p>
          </div>
          <div class="row">
            ${ytHref}
            <button class="modal-close" data-action="close-modal">×</button>
          </div>
        </div>
        <div class="editor">
          <div>
            <div class="phone" id="preview-box">
              <video id="preview" controls playsinline src="${src}"></video>
            </div>
            <div class="row" style="margin-top:10px;justify-content:space-between">
              <span class="tiny dim">${hasFile ? "Preview hasil render 9:16" : "Preview sumber (belum dirender)"}</span>
              <button class="btn small ghost" data-action="preview-clip" data-id="${esc(clip.id)}">Putar potongan</button>
            </div>
          </div>
          <div class="field-stack">
            <div class="field">
              <label>Judul / caption YouTube</label>
              <textarea class="input" id="edit-title" rows="2">${esc(clip.title)}</textarea>
            </div>
            <div class="field">
              <label>Hook overlay (muncul 5 detik pertama)</label>
              <input class="input" id="edit-hook" value="${esc(clip.hook)}" maxlength="60" />
            </div>
            <div class="grid-2">
              <div class="field">
                <label>Mulai (detik)</label>
                <input class="input" id="edit-start" type="number" step="0.5" min="0" value="${Number(clip.start).toFixed(1)}" />
              </div>
              <div class="field">
                <label>Durasi (detik)</label>
                <input class="input" id="edit-dur" type="number" step="0.5" min="3" value="${Number(clip.duration).toFixed(1)}" />
              </div>
            </div>
            <div class="field">
              <label>Fokus crop horizontal (untuk gaya crop) — <span id="focus-val">${Math.round(focus * 100)}%</span></label>
              <input type="range" id="edit-focus" min="0" max="1" step="0.01" value="${focus}" />
              <div class="tiny dim">0% = kiri · 50% = tengah · 100% = kanan</div>
            </div>
            <label class="switch">
              <input type="checkbox" id="edit-captions" ${captionsOn ? "checked" : ""} />
              <span class="track"></span>
              <span class="tiny">Caption karaoke untuk clip ini</span>
            </label>
            <div class="field">
              <label>Deskripsi</label>
              <textarea class="input" id="edit-desc" rows="4">${esc(clip.description)}</textarea>
            </div>
            <div class="field">
              <label>Hashtag</label>
              <input class="input" id="edit-tags" value="${esc(clip.hashtags)}" />
            </div>
            <div class="row">
              <button class="btn primary" data-action="save-clip" data-id="${esc(clip.id)}">Simpan &amp; render</button>
              <button class="btn" data-action="save-only" data-id="${esc(clip.id)}">Simpan saja</button>
              <button class="btn yt" data-action="upload" data-id="${esc(clip.id)}" ${!hasFile ? "disabled" : ""}>Upload ke Shorts</button>
              ${hasFile ? `<a class="btn ghost" href="/api/clips/${esc(clip.id)}/download">Unduh</a>` : ""}
            </div>
            <div class="tiny dim">Render memakai pengaturan global (gaya video, caption, fps) + pengaturan khusus clip ini.</div>
          </div>
        </div>
      </div>
    </div>
  `;

  const video = $("#preview");
  const startInput = $("#edit-start");
  const durInput = $("#edit-dur");
  const playRange = () => {
    const start = Number(startInput.value) || 0;
    const dur = Number(durInput.value) || 30;
    video.currentTime = Math.max(0, start);
    video.play().catch(() => {});
    state.editing._rangeEnd = start + dur;
  };
  video.addEventListener("timeupdate", () => {
    if (!hasFile && state.editing && state.editing._rangeEnd && video.currentTime > state.editing._rangeEnd) {
      video.pause();
      video.currentTime = Number(startInput.value) || 0;
    }
  });
  $("#edit-focus").addEventListener("input", (e) => {
    $("#focus-val").textContent = Math.round(Number(e.target.value) * 100) + "%";
  });

}

function collectEditorValues() {
  const clip = state.editing;
  if (!clip) return null;
  return {
    title: $("#edit-title").value.trim(),
    hook: $("#edit-hook").value.trim(),
    description: $("#edit-desc").value,
    hashtags: $("#edit-tags").value.trim(),
    start: Number($("#edit-start").value) || 0,
    duration: Math.max(3, Number($("#edit-dur").value) || 30),
    focus: Number($("#edit-focus").value),
    captions: $("#edit-captions").checked,
  };
}

async function saveEditor(renderAfter) {
  const values = collectEditorValues();
  if (!values) return;
  const clip = state.editing;
  try {
    await api("/api/clips/" + clip.id, { method: "PATCH", body: values });
    if (renderAfter) await api("/api/clips/" + clip.id + "/render", { method: "POST" });
    toast(renderAfter ? "Clip masuk antrean render" : "Perubahan tersimpan", "ok");
    closeModal();
    if (state.route === "project") await renderProject(clip.job_id);
  } catch (err) {
    toast(err.message, "bad");
  }
}

/* ------------------------------------------------------------------ */
/* view: youtube                                                      */
/* ------------------------------------------------------------------ */
async function renderYouTube() {
  const view = $("#view");
  const [yt, settings] = await Promise.all([api("/api/youtube/status"), api("/api/settings")]);
  state.yt = yt;
  state.settings = settings;
  let channel = null;
  if (yt.connected) {
    try {
      channel = await api("/api/youtube/channel");
    } catch (err) {
      channel = null;
    }
  }

  const connectionCard = yt.connected
    ? `<div class="card">
        <div class="card-title"><h2>Akun terhubung</h2><button class="btn small ghost danger" data-action="yt-disconnect">Putuskan</button></div>
        <div class="row" style="gap:16px">
          <div style="width:56px;height:56px;border-radius:50%;overflow:hidden;background:#1b1b2b;display:grid;place-items:center;font-size:22px">
            ${channel && channel.avatar ? `<img src="${esc(channel.avatar)}" style="width:100%;height:100%;object-fit:cover" />` : "▶"}
          </div>
          <div>
            <h3>${esc((channel && channel.title) || yt.channel || "Channel YouTube")}</h3>
            <div class="tiny dim">
              ${channel ? Number(channel.subscribers || 0).toLocaleString("id-ID") + " subscriber · " + Number(channel.videos || 0).toLocaleString("id-ID") + " video" : "Terhubung"}
            </div>
          </div>
        </div>
      </div>`
    : `<div class="card">
        <div class="card-title"><h2>Hubungkan YouTube</h2><span class="tag">belum terhubung</span></div>
        <div class="grid-2">
          <div>
            <h3 style="margin-bottom:8px">1 · Buat OAuth client (sekali saja)</h3>
            <ol class="tiny muted" style="padding-left:18px;line-height:1.9;margin:0 0 12px">
              <li>Buka <a href="https://console.cloud.google.com/projectcreate" target="_blank" rel="noopener">Google Cloud Console</a>, buat project baru.</li>
              <li>Aktifkan <b>YouTube Data API v3</b> di menu Library.</li>
              <li>Menu <b>OAuth consent screen</b> → tipe External → tambahkan email kamu sebagai test user.</li>
              <li>Menu <b>Credentials</b> → Create credentials → OAuth client ID → tipe <b>TVs and Limited Input devices</b>.</li>
              <li>Salin <b>Client ID</b> dan <b>Client secret</b> ke kolom di bawah.</li>
            </ol>
            <div class="field">
              <label>Client ID</label>
              <input class="input" id="yt-client-id" placeholder="xxxx.apps.googleusercontent.com" value="${esc(settings.yt_client_id || "")}" spellcheck="false" />
            </div>
            <div class="field" style="margin-top:10px">
              <label>Client secret</label>
              <input class="input" id="yt-client-secret" type="password" placeholder="GOCSPX-…" value="" spellcheck="false" />
            </div>
            <div class="row" style="margin-top:10px">
              <button class="btn" data-action="yt-save-client">Simpan kredensial</button>
              <span class="tiny dim">${yt.client_configured ? "Kredensial sudah tersimpan" : "Belum ada kredensial"}</span>
            </div>
            <details style="margin-top:12px">
              <summary class="tiny dim" style="cursor:pointer">atau tempel isi file client_secret.json</summary>
              <textarea class="input mono tiny" id="yt-json" rows="4" placeholder='{"installed":{"client_id":"…","client_secret":"…"}}' style="margin-top:8px"></textarea>
              <button class="btn small" data-action="yt-save-json" style="margin-top:8px">Impor JSON</button>
            </details>
          </div>
          <div>
            <h3 style="margin-bottom:8px">2 · Login</h3>
            <p class="tiny muted" style="margin-top:0">
              Login pakai kode (cocok kalau Clipper jalan di server/HP lain). Kode berlaku ±15 menit.
            </p>
            <button class="btn primary" data-action="yt-device" ${yt.client_configured ? "" : "disabled"}>Login dengan kode</button>
            <div id="yt-device-box" style="margin-top:12px"></div>
            <div class="divider"></div>
            <p class="tiny muted" style="margin-top:0">
              Kalau Clipper jalan di komputer ini dan client bertipe <b>Desktop app</b>, bisa langsung buka browser:
            </p>
            <button class="btn ghost small" data-action="yt-browser" ${yt.client_has_secret ? "" : "disabled"}>Login via browser</button>
          </div>
        </div>
      </div>`;

  view.innerHTML = `
    <div class="page-head">
      <div>
        <h1>YouTube Shorts</h1>
        <p class="sub">Upload otomatis ke channel kamu. Video vertikal ≤ 3 menit otomatis dianggap Shorts, dan <span class="mono">#Shorts</span> ditambahkan sendiri.</p>
      </div>
      <span class="tag ${yt.connected ? "done" : ""}">${yt.connected ? "siap upload" : "belum login"}</span>
    </div>

    ${connectionCard}

    <div class="card">
      <div class="card-title"><h2>Default upload</h2><span class="tiny dim">berlaku untuk semua clip</span></div>
      <div class="grid-3">
        <div class="field">
          <label>Visibilitas</label>
          <select class="select" data-setting="yt_privacy">
            <option value="public" ${settings.yt_privacy === "public" ? "selected" : ""}>Public</option>
            <option value="unlisted" ${settings.yt_privacy === "unlisted" ? "selected" : ""}>Unlisted</option>
            <option value="private" ${settings.yt_privacy === "private" ? "selected" : ""}>Private</option>
          </select>
        </div>
        <div class="field">
          <label>Kategori</label>
          <select class="select" data-setting="yt_category">
            ${[
              ["24", "Entertainment"],
              ["22", "People & Blogs"],
              ["27", "Education"],
              ["28", "Science & Technology"],
              ["10", "Music"],
              ["20", "Gaming"],
              ["23", "Comedy"],
              ["26", "Howto & Style"],
              ["17", "Sports"],
            ]
              .map(([v, label]) => `<option value="${v}" ${String(settings.yt_category) === v ? "selected" : ""}>${label}</option>`)
              .join("")}
          </select>
        </div>
        <div class="field">
          <label>Hashtag default</label>
          <input class="input" data-setting="yt_hashtags" value="${esc(settings.yt_hashtags)}" />
        </div>
      </div>
      <div class="row" style="margin-top:14px;gap:22px">
        <label class="switch">
          <input type="checkbox" data-setting="yt_auto_upload" ${settings.yt_auto_upload ? "checked" : ""} />
          <span class="track"></span>
          <span>Upload otomatis setelah render</span>
        </label>
        <label class="switch">
          <input type="checkbox" data-setting="auto_render" ${settings.auto_render ? "checked" : ""} />
          <span class="track"></span>
          <span>Render otomatis setelah analisis</span>
        </label>
        <label class="switch">
          <input type="checkbox" data-setting="yt_notify_subscribers" ${settings.yt_notify_subscribers ? "checked" : ""} />
          <span class="track"></span>
          <span>Notif ke subscriber</span>
        </label>
      </div>
      <p class="hint" style="margin-top:12px">
        Kalau "render otomatis" + "upload otomatis" dua-duanya nyala, alurnya full otomatis: tempel link → clip → upload.
      </p>
    </div>
  `;

  bindSettingInputs();
  if (yt.connected || (yt.state && yt.state !== "idle" && yt.state !== "connected")) pollDeviceStatus();
}

function bindSettingInputs() {
  $$("[data-setting]").forEach((el) => {
    el.addEventListener("change", async () => {
      const key = el.dataset.setting;
      let value;
      if (el.type === "checkbox") value = el.checked;
      else if (el.type === "number") value = Number(el.value);
      else value = el.value;
      await saveSettings({ [key]: value });
      toast("Pengaturan disimpan", "ok");
    });
  });
}

async function pollDeviceStatus() {
  const box = $("#yt-device-box");
  try {
    const status = await api("/api/youtube/status");
    state.yt = status;
    if (status.state === "waiting") {
      if (box) {
        box.innerHTML = `
          <div class="code-box">${esc(status.user_code)}</div>
          <div class="tiny muted" style="margin-top:10px">
            Buka <a href="${esc(status.url)}" target="_blank" rel="noopener">${esc(status.url)}</a>, masukkan kode di atas,
            lalu izinkan akses. Halaman ini otomatis lanjut setelah kamu setuju.
          </div>`;
      }
      setTimeout(pollDeviceStatus, 4000);
    } else if (status.state === "connected" || status.connected) {
      toast("YouTube terhubung 🎉", "ok");
      await renderYouTube();
      loadHealth();
    } else if (status.state === "error" || status.state === "expired") {
      if (box) box.innerHTML = `<div class="tiny" style="color:#fca5a5">${esc(status.message)}</div>`;
    }
  } catch (err) {
    /* diamkan, coba lagi nanti */
  }
}

/* ------------------------------------------------------------------ */
/* view: pengaturan                                                   */
/* ------------------------------------------------------------------ */
async function renderSettings() {
  const view = $("#view");
  const [settings, health] = await Promise.all([api("/api/settings"), api("/api/health")]);
  state.settings = settings;
  const s = settings;

  view.innerHTML = `
    <div class="page-head">
      <div>
        <h1>Pengaturan</h1>
        <p class="sub">Semua proses jalan lokal. Perubahan langsung dipakai untuk clip berikutnya.</p>
      </div>
      <span class="tag ${health.whisper ? "done" : "err"}">${health.whisper ? "Whisper siap" : "Whisper belum ada"}</span>
    </div>

    <div class="card">
      <div class="card-title"><h2>Analisis & transkripsi</h2><span class="tiny dim">gratis, offline setelah model terunduh</span></div>
      <div class="grid-3">
        <div class="field">
          <label>Model Whisper</label>
          <select class="select" data-setting="whisper_model">
            ${["tiny", "base", "small", "medium", "large-v3"]
              .map((m) => `<option value="${m}" ${s.whisper_model === m ? "selected" : ""}>${m}${m === "small" ? " (seimbang)" : m === "large-v3" ? " (paling akurat)" : ""}</option>`)
              .join("")}
          </select>
        </div>
        <div class="field">
          <label>Bahasa audio</label>
          <select class="select" data-setting="language">
            ${[
              ["auto", "Deteksi otomatis"],
              ["id", "Indonesia"],
              ["en", "Inggris"],
              ["ms", "Melayu"],
              ["ja", "Jepang"],
              ["ko", "Korea"],
              ["zh", "Mandarin"],
              ["ar", "Arab"],
              ["es", "Spanyol"],
            ]
              .map(([v, l]) => `<option value="${v}" ${s.language === v ? "selected" : ""}>${l}</option>`)
              .join("")}
          </select>
        </div>
        <div class="field">
          <label>Perangkat</label>
          <select class="select" data-setting="whisper_device">
            <option value="auto" ${s.whisper_device === "auto" ? "selected" : ""}>Otomatis</option>
            <option value="cpu" ${s.whisper_device === "cpu" ? "selected" : ""}>CPU</option>
            <option value="cuda" ${s.whisper_device === "cuda" ? "selected" : ""}>GPU (CUDA)</option>
          </select>
        </div>
        <div class="field">
          <label>Jumlah clip per video</label>
          <input class="input" type="number" min="1" max="20" data-setting="target_clips" value="${s.target_clips}" />
        </div>
        <div class="field">
          <label>Durasi minimum (detik)</label>
          <input class="input" type="number" min="5" max="180" data-setting="min_clip_seconds" value="${s.min_clip_seconds}" />
        </div>
        <div class="field">
          <label>Durasi maksimum (detik)</label>
          <input class="input" type="number" min="10" max="180" data-setting="max_clip_seconds" value="${s.max_clip_seconds}" />
        </div>
      </div>
      <label class="switch" style="margin-top:14px">
        <input type="checkbox" data-setting="prefer_whisper" ${s.prefer_whisper ? "checked" : ""} />
        <span class="track"></span>
        <span class="tiny">Selalu pakai Whisper (walau video sudah punya subtitle) — lebih akurat, lebih lambat</span>
      </label>
      <p class="hint" style="margin-top:12px">
        Kalau video punya subtitle, Clipper langsung memakainya (cepat, tanpa unduh model).
        Model <b>${esc(s.whisper_model)}</b> diunduh sekali saat pertama dibutuhkan (butuh internet sekali),
        setelah itu transkripsi jalan 100% offline di <span class="mono">models/</span>.
      </p>
    </div>

    <div class="card">
      <div class="card-title"><h2>Tampilan video & caption</h2></div>
      <div class="grid-3">
        <div class="field">
          <label>Gaya video</label>
          <select class="select" data-setting="layout">
            <option value="fit" ${s.layout === "fit" ? "selected" : ""}>Blur background</option>
            <option value="crop" ${s.layout === "crop" ? "selected" : ""}>Crop penuh 9:16</option>
          </select>
        </div>
        <div class="field">
          <label>FPS</label>
          <select class="select" data-setting="fps">
            ${[24, 25, 30, 60].map((f) => `<option value="${f}" ${Number(s.fps) === f ? "selected" : ""}>${f} fps</option>`).join("")}
          </select>
        </div>
        <div class="field">
          <label>Kualitas (CRF: kecil = bagus)</label>
          <input class="input" type="number" min="16" max="30" data-setting="crf" value="${s.crf}" />
        </div>
        <div class="field">
          <label>Kata per baris caption</label>
          <select class="select" data-setting="caption_words">
            ${[1, 2, 3, 4, 5].map((n) => `<option value="${n}" ${Number(s.caption_words) === n ? "selected" : ""}>${n} kata</option>`).join("")}
          </select>
        </div>
        <div class="field">
          <label>Warna teks</label>
          <input class="input" type="color" data-setting="caption_color" value="${esc(s.caption_color)}" style="padding:4px;height:42px" />
        </div>
        <div class="field">
          <label>Warna highlight</label>
          <input class="input" type="color" data-setting="caption_highlight" value="${esc(s.caption_highlight)}" style="padding:4px;height:42px" />
        </div>
        <div class="field">
          <label>Font caption</label>
          <input class="input" data-setting="caption_font" value="${esc(s.caption_font)}" />
        </div>
      </div>
      <div class="row" style="margin-top:14px;gap:22px">
        <label class="switch">
          <input type="checkbox" data-setting="captions" ${s.captions ? "checked" : ""} />
          <span class="track"></span><span>Caption karaoke</span>
        </label>
        <label class="switch">
          <input type="checkbox" data-setting="caption_uppercase" ${s.caption_uppercase ? "checked" : ""} />
          <span class="track"></span><span>HURUF BESAR</span>
        </label>
        <label class="switch">
          <input type="checkbox" data-setting="hook_overlay" ${s.hook_overlay ? "checked" : ""} />
          <span class="track"></span><span>Judul hook di atas video</span>
        </label>
        <label class="switch">
          <input type="checkbox" data-setting="normalize_audio" ${s.normalize_audio ? "checked" : ""} />
          <span class="track"></span><span>Normalisasi audio</span>
        </label>
      </div>
      <p class="hint" style="margin-top:12px">
        Font harus tersedia di sistem untuk hasil terbaik. Install "Inter" (gratis) atau pakai
        <span class="mono">Arial</span> / <span class="mono">DejaVu Sans</span>.
      </p>
    </div>

    <div class="card">
      <div class="card-title"><h2>AI lokal (opsional)</h2><span class="tag ${health && s.llm_enabled ? "run" : ""}">Ollama</span></div>
      <p class="hint" style="margin-top:0">
        Kalau punya <a href="https://ollama.com" target="_blank" rel="noopener">Ollama</a> jalan lokal, judul & hook bisa dibuat AI.
        Tanpa ini pun Clipper tetap jalan (pakai scoring heuristik).
      </p>
      <div class="grid-3" style="margin-top:12px">
        <div class="field">
          <label>URL Ollama</label>
          <input class="input" data-setting="llm_url" value="${esc(s.llm_url)}" />
        </div>
        <div class="field">
          <label>Model</label>
          <input class="input" data-setting="llm_model" value="${esc(s.llm_model)}" />
        </div>
        <div class="field">
          <label>Aktifkan</label>
          <label class="switch" style="margin-top:6px">
            <input type="checkbox" data-setting="llm_enabled" ${s.llm_enabled ? "checked" : ""} />
            <span class="track"></span><span>Pakai LLM untuk judul</span>
          </label>
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card-title"><h2>Ambil video dari link</h2><span class="tiny dim">yt-dlp</span></div>
      <div class="grid-2">
        <div class="field">
          <label>Cookies browser (untuk video private / perlu login)</label>
          <select class="select" data-setting="ytdlp_cookies_browser">
            ${[
              ["", "Tidak pakai cookies"],
              ["chrome", "Chrome"],
              ["firefox", "Firefox"],
              ["edge", "Edge"],
              ["brave", "Brave"],
              ["safari", "Safari"],
            ]
              .map(([v, l]) => `<option value="${v}" ${s.ytdlp_cookies_browser === v ? "selected" : ""}>${l}</option>`)
              .join("")}
          </select>
        </div>
        <div class="field">
          <label>Proxy (opsional)</label>
          <input class="input" data-setting="ytdlp_proxy" placeholder="http://127.0.0.1:8080" value="${esc(s.ytdlp_proxy)}" />
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card-title"><h2>Diagnostik</h2><span class="tag ${health.ffmpeg ? "done" : "err"}">${health.ffmpeg ? "sehat" : "ffmpeg hilang"}</span></div>
      <div class="grid-3">
        <div class="stat"><span class="v">${health.ffmpeg ? "✓" : "✗"}</span><span class="k">ffmpeg</span></div>
        <div class="stat"><span class="v">${health.whisper ? "✓" : "✗"}</span><span class="k">faster-whisper</span></div>
        <div class="stat"><span class="v">${health.ytdlp ? esc(health.ytdlp_version) : "✗"}</span><span class="k">yt-dlp</span></div>
        <div class="stat"><span class="v">${health.cpu}</span><span class="k">core CPU</span></div>
        <div class="stat"><span class="v">${health.youtube ? "✓" : "—"}</span><span class="k">youtube</span></div>
      </div>
      <p class="hint mono" style="margin-top:12px;word-break:break-all">ffmpeg: ${esc(health.ffmpeg_path || "tidak ditemukan")}</p>
      <p class="hint">Kalau ada yang ✗, jalankan <span class="mono">pip install -r requirements.txt</span> lalu restart Clipper.</p>
    </div>
  `;

  bindSettingInputs();
}

/* ------------------------------------------------------------------ */
/* aksi global                                                        */
/* ------------------------------------------------------------------ */
document.addEventListener("click", async (e) => {
  const target = e.target.closest("[data-action]");
  if (!target) {
    if (e.target.classList && e.target.classList.contains("modal-back")) closeModal();
    return;
  }
  const action = target.dataset.action;
  const id = target.dataset.id;

  try {
    if (action === "open-job") {
      location.hash = "#/project/" + id;
    } else if (action === "new-job") {
      location.hash = "#/new";
    } else if (action === "edit") {
      e.preventDefault();
      await openClipEditor(id);
    } else if (action === "close-modal") {
      closeModal();
    } else if (action === "preview-clip") {
      const video = $("#preview");
      const start = Number($("#edit-start").value) || 0;
      const dur = Number($("#edit-dur").value) || 30;
      state.editing._rangeEnd = start + dur;
      video.currentTime = Math.max(0, start);
      video.play().catch(() => {});
    } else if (action === "render") {
      target.dataset.label = target.textContent.trim();
      target.disabled = true;
      target.innerHTML = `<span class="spinner"></span>`;
      await api("/api/clips/" + id + "/render", { method: "POST" });
      toast("Render dimulai", "ok");
      if (state.route === "project") renderProject(state.param);
    } else if (action === "upload") {
      target.dataset.label = target.textContent.trim();
      target.disabled = true;
      target.innerHTML = `<span class="spinner"></span>`;
      await api("/api/clips/" + id + "/upload", { method: "POST", body: {} });
      toast("Upload ke YouTube dimulai", "ok");
      closeModal();
      if (state.route === "project") renderProject(state.param);
    } else if (action === "delete-clip") {
      if (!confirm("Hapus clip ini? File hasil render juga dihapus.")) return;
      await api("/api/clips/" + id, { method: "DELETE" });
      toast("Clip dihapus", "ok");
      if (state.route === "project") renderProject(state.param);
    } else if (action === "delete-job") {
      if (!confirm("Hapus project ini beserta semua clip & file sumbernya?")) return;
      await api("/api/jobs/" + id, { method: "DELETE" });
      toast("Project dihapus", "ok");
      location.hash = "#/projects";
    } else if (action === "retry-job") {
      await api("/api/jobs/" + id + "/retry", { method: "POST" });
      toast("Mencoba ulang analisis…", "ok");
      renderProject(id);
    } else if (action === "render-all") {
      await api("/api/jobs/" + state.job.id + "/render", { method: "POST" });
      toast("Semua clip masuk antrean render", "ok");
      renderProject(state.job.id);
    } else if (action === "upload-all") {
      const res = await api("/api/clips/upload-all", { method: "POST", body: { job_id: state.job.id } });
      toast(res.queued + " clip masuk antrean upload", "ok");
      renderProject(state.job.id);
    } else if (action === "add-manual") {
      const start = prompt("Mulai dari detik ke berapa?", "0");
      if (start === null) return;
      const duration = prompt("Durasi clip (detik)?", "30");
      if (duration === null) return;
      await api("/api/jobs/" + state.job.id + "/clips", {
        method: "POST",
        body: { start: Number(start), duration: Number(duration) },
      });
      toast("Clip manual ditambahkan", "ok");
      renderProject(state.job.id);
    } else if (action === "save-clip") {
      await saveEditor(true);
    } else if (action === "save-only") {
      await saveEditor(false);
    } else if (action === "yt-save-client") {
      const clientId = ($("#yt-client-id") && $("#yt-client-id").value.trim()) || "";
      const secret = ($("#yt-client-secret") && $("#yt-client-secret").value.trim()) || "";
      if (!clientId) {
        toast("Isi Client ID dulu", "bad");
        return;
      }
      await api("/api/youtube/client", { method: "POST", body: { client_id: clientId, client_secret: secret } });
      toast("Kredensial YouTube disimpan", "ok");
      await renderYouTube();
    } else if (action === "yt-save-json") {
      const raw = ($("#yt-json") && $("#yt-json").value) || "";
      await api("/api/youtube/client", { method: "POST", body: { client: raw } });
      toast("Kredensial diimpor", "ok");
      await renderYouTube();
    } else if (action === "yt-device") {
      const status = await api("/api/youtube/login/device", { method: "POST" });
      state.yt = status;
      if (status.connected) {
        toast("Sudah terhubung", "ok");
        await renderYouTube();
      } else {
        const box = $("#yt-device-box");
        if (box) {
          box.innerHTML = `<div class="code-box">${esc(status.user_code)}</div>
            <div class="tiny muted" style="margin-top:10px">Buka
              <a href="${esc(status.url)}" target="_blank" rel="noopener">${esc(status.url)}</a>,
              masukkan kode di atas lalu izinkan. Halaman ini lanjut otomatis.</div>`;
        }
        pollDeviceStatus();
      }
    } else if (action === "yt-browser") {
      toast("Browser dibuka di mesin Clipper. Selesaikan login di sana…");
      const status = await api("/api/youtube/login/browser", { method: "POST" });
      state.yt = status;
      await renderYouTube();
      loadHealth();
    } else if (action === "yt-disconnect") {
      if (!confirm("Putuskan akun YouTube?")) return;
      await api("/api/youtube/disconnect", { method: "POST" });
      toast("Akun diputuskan", "ok");
      await renderYouTube();
      loadHealth();
    }
  } catch (err) {
    toast(err.message, "bad");
    if (target.tagName === "BUTTON") {
      target.disabled = false;
      target.innerHTML = target.dataset.label || "Coba lagi";
    }
  }
});

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeModal();
});

/* ------------------------------------------------------------------ */
/* boot                                                               */
/* ------------------------------------------------------------------ */
(async function boot() {
  await Promise.all([loadHealth(), loadSettings()]);
  if (!location.hash) location.hash = "#/new";
  await renderRoute();
  setInterval(loadHealth, 15000);
})();
