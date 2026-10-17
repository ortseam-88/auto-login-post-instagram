// create by Ortseam
"use strict";

const PHOTO_CROPS = ["Original", "1:1", "4:5", "16:9"];
const REEL_CROPS = ["Original", "1:1", "9:16", "16:9"];
const FILTERS = ["Aden", "Clarendon", "Crema", "Gingham", "Juno", "Lark", "Ludwig", "Moon", "Original", "Perpetua", "Reyes", "Slumber"];
const ADJUSTMENTS = [
  ["brightness", "Kecerahan", -100, 100], ["contrast", "Kontras", -100, 100], ["fade", "Fade", -100, 100],
  ["saturation", "Saturasi", -100, 100], ["temperature", "Suhu warna", -100, 100], ["vignette", "Vignette", 0, 100],
];
const KIND = {
  photo: { label: "Foto", icon: "image", tone: "tone-secondary" },
  carousel: { label: "Carousel", icon: "view_carousel", tone: "tone-primary" },
  reel: { label: "Reel", icon: "movie", tone: "tone-tertiary" },
};
const STATUS_TONE = {
  // "prepared" hanya untuk membaca hasil historis; bukan mode yang dapat dijalankan.
  success: "ok", prepared: "ok", published: "ok",
  submitting: "warn", unknown_after_submit: "warn", unknown: "warn", interrupted: "warn", verification_required: "warn",
  starting: "info",
};
const STATUS_ICON = { ok: "check_circle", warn: "warning", bad: "error", info: "pending", neutral: "radio_button_unchecked" };
/* Tindakan ulang berlaku per akun. Posting tetap melewati konfirmasi PUBLISH dan ledger. */
const STATUS_TEXT = {
  success: { label: "Login berhasil", advice: "Session tersimpan; akun siap dipakai.", retry: false },
  prepared: { label: "Draft lama", advice: "Catatan lama; tidak ada postingan dari proses ini.", retry: false },
  published: { label: "Terposting", advice: "Konten sudah tayang.", retry: false },
  starting: { label: "Diproses", advice: "", retry: false },
  invalid_credentials: { label: "Password salah", advice: "Perbaiki akun di menu Akun (hapus lalu tambah lagi), kemudian jalankan lagi.", retry: true },
  verification_required: { label: "Perlu verifikasi", advice: "Instagram meminta 2FA/checkpoint. Jalankan Login saja untuk akun ini dan selesaikan di jendela browser.", retry: true },
  rate_limited: { label: "Dibatasi Instagram", advice: "Terlalu banyak percobaan. Coba lagi beberapa jam kemudian.", retry: true },
  account_disabled: { label: "Akun dinonaktifkan", advice: "Selesaikan lewat Instagram, bukan dari sini.", retry: false },
  timeout: { label: "Waktu habis", advice: "Login tidak terkonfirmasi tepat waktu. Periksa koneksi, lalu jalankan lagi.", retry: true },
  unexpected_page: { label: "Halaman tak dikenal", advice: "Jalankan Login saja untuk akun ini dan periksa jendelanya.", retry: true },
  profile_busy: { label: "Profil sedang dipakai", advice: "Tutup jendela Chrome akun ini, lalu jalankan lagi.", retry: true },
  browser_error: { label: "Browser bermasalah", advice: "Gagal sebelum posting; tidak ada yang terposting. Jalankan lagi.", retry: true },
  media_unavailable: { label: "Media tidak terbaca", advice: "File media hilang atau berubah. Pulihkan file, lalu jalankan lagi.", retry: true },
  unsupported_option: { label: "Opsi tidak didukung", advice: "Ada opsi yang tidak tersedia di Instagram. Ubah konten, simpan, jalankan lagi.", retry: false },
  draft_invalid: { label: "Draft ditolak", advice: "Instagram tidak menerima media/opsi ini. Ubah konten, lalu jalankan lagi.", retry: false },
  blocked_by_account: { label: "Dilewati", advice: "Konten sebelumnya di akun ini gagal login. Perbaiki akun, lalu jalankan lagi.", retry: true },
  blocked_by_ledger: { label: "Sudah tercatat", advice: "Konten ini sudah pernah diposting atau hasilnya belum pasti. Lihat Ledger.", retry: false },
  interrupted: { label: "Dihentikan", advice: "Dihentikan sebelum posting. Aman dijalankan lagi.", retry: true },
  unknown_after_submit: { label: "Hasil belum pasti", advice: "Share sudah ditekan tetapi hasil tidak terbaca. Cek profil Instagram; jangan jalankan ulang.", retry: false },
  unknown: { label: "Status tak dikenal", advice: "Lihat Ledger sebelum melakukan apa pun.", retry: false },
  submitting: { label: "Sedang Share", advice: "Menunggu bukti dari Instagram.", retry: false },
};
const MODE_LABEL = { login: "Login saja", publish: "Posting sungguhan" };
const CATALOG_VERSION_ERROR = "Tutup lalu buka ulang program untuk memakai versi baru.";

const state = { token: null, accounts: [], media: [], jobs: [], runSelection: new Set(), accountSelection: new Set(), starting: false, settings: null, settingsError: null, paths: {}, history: [], environment: {}, run: null, manifestError: null, runStatusError: null, dirty: false, editRevision: 0, saving: false, lastRunning: false };
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
let fieldSequence = 0;
let snapshotGeneration = 0;
let settingsRevision = 0;
let settingsDirty = false;
let settingsQueue = Promise.resolve();
let settingsTimer = null;
let savedContents = [];

/* ---------- primitives ---------- */

async function api(path, body) {
  const init = body === undefined
    ? { headers: { "X-UI-Token": state.token } }
    : { method: "POST", headers: { "X-UI-Token": state.token, "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const response = await fetch(path, init);
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "Permintaan gagal.");
  return payload;
}

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === "class") node.className = value;
    else if (key === "dataset") Object.assign(node.dataset, value);
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (value !== null && value !== undefined) node[key] = value;
  }
  node.append(...children.filter((child) => child !== null && child !== undefined));
  return node;
}

function icon(name, extra = "") {
  return el("span", { class: `material-symbols-outlined ${extra}`.trim(), textContent: name, ariaHidden: "true" });
}

function chip(text, tone = "neutral", iconName) {
  return el("span", { class: `chip ${tone}` }, iconName ? icon(iconName) : null, el("span", { textContent: text }));
}

function statusChip(status) {
  const tone = statusTone(status);
  return chip(status || "?", tone, STATUS_ICON[tone]);
}

function statusTone(status) {
  if (!status) return "neutral";
  return STATUS_TONE[status] || "bad";
}

function resultTone(row) {
  const tone = statusTone(row.status);
  return tone === "ok" && row.cleanup && row.cleanup !== "closed" ? "warn" : tone;
}

function field(control, labelText, extraClass = "") {
  const isSelect = control.tagName === "SELECT";
  control.id ||= `editor-field-${++fieldSequence}`;
  return el("div", { class: `field ${isSelect ? "select" : ""} ${extraClass}`.trim() }, control, el("label", { textContent: labelText, htmlFor: control.id }), isSelect ? icon("arrow_drop_down", "trailing") : null);
}

function setStatus(element, message, ok) {
  element.textContent = message;
  element.classList.toggle("ok", ok === true);
  element.classList.toggle("bad", ok === false);
}

function manifestErrorMessage() {
  if (state.manifestError === CATALOG_VERSION_ERROR) return state.manifestError;
  return `File konten tidak dapat dibaca: ${state.manifestError} Perbaiki file ${state.paths.jobs || "input/jobs.json"} secara manual, lalu muat ulang halaman.`;
}

function updateRunAvailability() {
  const blocked = !!(state.manifestError || state.runStatusError || state.settingsError) || (readSettings().mode === "publish" && runPlan().blocked > 0);
  const running = !!state.run?.running;
  $("#start-run").disabled = running || state.starting || blocked;
  $("#rail-run").disabled = running ? !!state.run?.stopping : state.starting || blocked;
}

let snackbarTimer = null;
function snackbar(message) {
  const node = $("#snackbar");
  $("#snackbar-text").textContent = message;
  node.classList.remove("hidden");
  clearTimeout(snackbarTimer);
  snackbarTimer = setTimeout(() => node.classList.add("hidden"), 4000);
}

/* M3 basic dialog; returns the typed value (prompt) or boolean (confirm). */
function dialog({ title, text, confirmLabel = "OK", cancelLabel = "Batal", iconName = null, danger = false, input = null, content = null, onConfirm = null }) {
  return new Promise((resolve) => {
    const node = $("#dialog");
    const form = $("form", node);
    const iconNode = $("#dialog-icon");
    iconNode.classList.toggle("hidden", !iconName);
    iconNode.classList.toggle("error", danger);
    iconNode.textContent = iconName || "";
    $("#dialog-title").textContent = title;
    $("#dialog-text").textContent = text;
    const fieldNode = $("#dialog-field");
    const inputNode = $("#dialog-input");
    fieldNode.classList.toggle("hidden", !input);
    inputNode.value = "";
    $("#dialog-input-label").textContent = input ? input.label : "";
    const slot = $("#dialog-slot");
    slot.replaceChildren(...(content ? [content] : []));
    slot.classList.toggle("hidden", !content);
    const confirmButton = $("#dialog-confirm");
    const cancelButton = $("#dialog-cancel");
    confirmButton.textContent = confirmLabel;
    confirmButton.disabled = false;
    cancelButton.textContent = cancelLabel;
    confirmButton.classList.toggle("error", danger);
    const cancelled = input || onConfirm ? null : false;
    let result = cancelled;
    let settled = false;
    const complete = () => {
      if (settled) return;
      settled = true;
      confirmButton.onclick = null;
      cancelButton.onclick = null;
      node.onclose = null;
      form.onsubmit = null;
      slot.replaceChildren();
      resolve(result);
    };
    const finish = (value) => {
      result = value;
      if (node.open) node.close();
      else complete();
    };
    confirmButton.onclick = () => {
      // onConfirm mengembalikan undefined untuk menahan dialog saat validasi gagal.
      const value = onConfirm ? onConfirm() : (input ? inputNode.value : true);
      if (value !== undefined) finish(value);
    };
    form.onsubmit = (event) => {
      event.preventDefault();
      if (!confirmButton.disabled) confirmButton.click();
    };
    cancelButton.onclick = () => finish(cancelled);
    // Selesaikan promise sesudah event close, agar event lama tidak menutup
    // dialog berikutnya yang dibuka oleh pemanggil dalam microtask.
    node.onclose = complete;
    node.showModal();
    if (input) inputNode.focus();
    else confirmButton.focus();
  });
}

function formatTime(iso) {
  if (!iso) return "—";
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString("id-ID", { dateStyle: "medium", timeStyle: "medium" });
}

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function mediaUrl(item) {
  return `/api/media/file/${encodeURIComponent(item.name)}`;
}

function mediaByPath(path) {
  return state.media.find((item) => item.path === path) || null;
}

function tri(value) {
  return value === true ? "true" : value === false ? "false" : "";
}

function fromTri(value) {
  return value === "true" ? true : value === "false" ? false : null;
}

function splitUsernames(text) {
  return text.split(",").map((part) => part.trim().replace(/^@/, "")).filter(Boolean);
}

function emptyRow(table, columns, message) {
  table.append(el("tr", {}, el("td", { class: "empty", colSpan: columns, textContent: message })));
}

function markDirty() {
  snapshotGeneration++;
  state.dirty = true;
  state.editRevision += 1;
  setStatus($("#jobs-status"), "", null);
  $("#jobs-dirty").classList.remove("hidden");
  renderStats();
  renderRunContent();
  renderGuide();
}

/* ---------- snapshot ---------- */

function applySnapshot(data) {
  state.accounts = data.accounts;
  state.media = data.media;
  if (!settingsDirty) {
    state.settings = data.settings;
    state.settingsError = data.settings_error || null;
  }
  state.paths = data.paths;
  state.history = data.history || [];
  state.environment = data.environment || {};
  const compatible = data.manifest?.version === 2 && Array.isArray(data.manifest.contents);
  state.manifestError = compatible ? data.manifest_error || null : CATALOG_VERSION_ERROR;
  if (!state.dirty && !state.manifestError) {
    savedContents = data.manifest.contents;
    const folds = new Map(state.jobs.map((job) => [job.content_id, job._open]));
    state.jobs = data.manifest.contents.map((raw) => {
      const job = normalizeJob(raw);
      job._open = folds.get(job.content_id) ?? false;
      return job;
    });
  }
  pruneRunSelections();
  $$('[data-browser]').forEach((node) => { node.textContent = data.browser[node.dataset.browser]; });
  setStatus($("#accounts-error"), data.accounts_error || "", false);
  const message = state.manifestError ? manifestErrorMessage() : "";
  for (const selector of ["#manifest-error", "#run-manifest-error"]) {
    const notice = $(selector);
    setStatus(notice, message, false);
    notice.classList.toggle("hidden", !message);
  }
  if (message) setStatus($("#jobs-status"), "", null);
  $("#new-job").disabled = !!message;
  $("#save-jobs").disabled = state.saving || !!message;
  updateRunAvailability();
  renderAccounts();
  renderMedia();
  renderSettings();
  renderJobs();
  renderHistory();
  renderEnvironment();
  renderStats();
  renderGuide();
}

/* Identitas konten tetap; nama dapat diubah tanpa memengaruhi catatan posting. */
function normalizeJob(raw) {
  return {
    content_id: raw.content_id || "",
    name: raw.name || "",
    kind: raw.kind || "photo",
    media: (raw.media || []).map(normalizeMedia),
    caption: raw.caption || "",
    location: raw.location || "",
    collaborators: raw.collaborators || [],
    people_tags: raw.people_tags || [],
    ai_label: raw.ai_label ?? null,
    comments_enabled: raw.comments_enabled ?? null,
    hide_like_view_counts: raw.hide_like_view_counts ?? null,
    cover_path: raw.cover_path || "",
    cover_time: raw.cover_time ?? null,
    trim_start: raw.trim_start ?? null,
    trim_end: raw.trim_end ?? null,
    keep_original_audio: raw.keep_original_audio ?? null,
  };
}


function normalizeMedia(item) {
  return {
    path: item.path || "",
    alt_text: item.alt_text || "",
    people_tags: (item.people_tags || []).map((tag) => ({ username: tag.username || "", x: tag.x ?? null, y: tag.y ?? null })),
    crop: item.crop || null,
    zoom: item.zoom ?? null,
    filter_name: item.filter_name || null,
    adjustments: { ...(item.adjustments || {}) },
  };
}

/* ---------- stats ---------- */

function renderStats() {
  $("#stat-accounts").textContent = state.accounts.length;
  const bound = state.accounts.filter((account) => account.identity_bound).length;
  $("#stat-accounts-sub").textContent = state.accounts.length ? `${bound} session terikat` : "Belum ada akun";
  $("#stat-media").textContent = state.media.length;
  const videos = state.media.filter((item) => item.kind === "video").length;
  $("#stat-media-sub").textContent = state.media.length ? `${state.media.length - videos} gambar, ${videos} video` : "Belum ada media";
  $("#stat-jobs").textContent = state.jobs.length;
  const kinds = Object.keys(KIND).map((kind) => [kind, state.jobs.filter((job) => job.kind === kind).length]).filter(([, count]) => count);
  $("#stat-jobs-sub").textContent = state.jobs.length ? `${kinds.map(([kind, count]) => `${count} ${KIND[kind].label.toLowerCase()}`).join(", ")}${state.dirty ? " · belum disimpan" : ""}` : "Belum ada konten";
  $("#rail-badge-jobs").classList.toggle("hidden", !state.dirty);
}

/* ---------- accounts ---------- */

function renderAccounts() {
  const body = $("#account-table tbody");
  body.replaceChildren();
  if (!state.accounts.length) emptyRow(body, 3, "Belum ada akun. Tambahkan di bawah.");
  for (const account of state.accounts) {
    const remove = el("button", { type: "button", class: "icon-btn", title: "Hapus akun", ariaLabel: "Hapus akun", onclick: async () => {
      const sessionNote = account.profile_exists ? "Session browser akun ini ikut dihapus; login ulang diperlukan bila ditambahkan lagi." : "Belum ada session browser tersimpan.";
      const ok = await dialog({ title: "Hapus akun?", text: `${account.identifier} dihapus dari daftar akun. ${sessionNote}`, confirmLabel: "Hapus", iconName: "delete", danger: true });
      if (!ok) return;
      try {
        const result = await api("/api/accounts/delete", { identifier: account.identifier });
        await refresh();
        snackbar(result.profile_removed ? `Akun ${account.identifier} dan session browsernya dihapus.` : `Akun ${account.identifier} dihapus.`);
      } catch (error) { setStatus($("#accounts-error"), error.message, false); }
    } }, icon("delete"));
    const session = account.identity_bound ? chip("Terikat · login tersimpan", "ok", "verified")
      : account.profile_exists ? chip("Profil ada · belum terverifikasi", "warn", "pending")
      : chip("Belum pernah login", "neutral", "login");
    body.append(el("tr", {},
      el("td", {}, el("strong", { textContent: account.identifier })),
      el("td", {}, session),
      el("td", {}, remove)));
  }
  renderRunAccounts();
  renderRunContent();
}

/* ---------- media ---------- */

function renderMedia() {
  const grid = $("#media-grid");
  grid.replaceChildren();
  if (!state.media.length) {
    grid.append(el("div", { class: "media-empty" }, icon("add_photo_alternate"), el("span", { class: "body-medium", textContent: "Belum ada media di input/media. Pilih file atau tempel path." })));
    return;
  }
  for (const item of state.media) {
    const users = state.jobs.filter((job) => job.media.some((media) => media.path === item.path) || job.cover_path === item.path).map((job) => job.name || "?");
    const preview = item.kind === "video"
      ? el("video", { src: mediaUrl(item), muted: true, preload: "metadata", playsInline: true })
      : el("img", { src: mediaUrl(item), alt: item.name, loading: "lazy" });
    const remove = el("button", { type: "button", class: "icon-btn", title: "Pindahkan ke arsip", ariaLabel: "Pindahkan ke arsip", onclick: async () => {
      const preventUnsavedRemoval = () => {
        const names = state.dirty ? state.jobs.filter((job) => job.media.some((media) => media.path === item.path) || job.cover_path === item.path).map((job) => job.name || "?") : [];
        if (!names.length) return false;
        const message = `${item.name} masih dipakai konten yang belum disimpan (${names.join(", ")}). Lepas dari konten dan simpan dulu.`;
        setStatus($("#media-status"), message, false);
        snackbar(message);
        return true;
      };
      if (preventUnsavedRemoval()) return;
      const ok = await dialog({ title: "Pindahkan ke arsip?", text: `${item.name} dipindahkan ke folder arsip, tidak dihapus permanen.`, confirmLabel: "Pindahkan", iconName: "delete", danger: true });
      if (!ok || preventUnsavedRemoval()) return;
      try { const result = await api("/api/media/delete", { name: item.name }); await refresh(); setStatus($("#media-status"), `Dipindahkan ke ${result.moved_to}`, true); snackbar(`${item.name} dipindahkan ke arsip.`); }
      catch (error) { setStatus($("#media-status"), error.message, false); }
    } }, icon("delete"));
    const copy = el("button", { type: "button", class: "icon-btn", title: "Salin path", ariaLabel: "Salin path", onclick: () => {
      navigator.clipboard?.writeText(item.path);
      snackbar(`Path ${item.path} disalin.`);
    } }, icon("content_copy"));
    grid.append(el("div", { class: "media-card" },
      el("div", { class: "media-thumb" }, preview),
      el("div", { class: "media-info" },
        el("span", { class: "media-name", textContent: item.name }),
        el("span", { class: "media-meta", textContent: `${item.kind === "video" ? "Video" : "Gambar"} · ${formatBytes(item.bytes)} · ${item.content_type}` }),
        el("span", { class: "media-meta", textContent: `Diubah ${formatTime(item.modified)}` }),
        el("div", {}, users.length ? chip(`${state.dirty ? "Dipakai di editor belum disimpan" : "Dipakai"}: ${users.join(", ")}`, "info", "link") : chip("Belum dipakai konten", "neutral"))),
      el("div", { class: "media-actions" }, copy, remove)));
  }
}

/* ---------- settings ---------- */

function renderSettings() {
  const notice = $("#settings-file-error");
  notice.textContent = state.settingsError ? `Pengaturan tersimpan bermasalah: ${state.settingsError} Ubah pengaturan untuk menyimpan perbaikannya sebelum Start.` : "";
  notice.classList.toggle("hidden", !state.settingsError);
  $("#setting-mode").value = state.settings.mode;
  $$("input[name=mode]").forEach((radio) => { radio.checked = radio.value === state.settings.mode; });
  $("#setting-workers").value = state.settings.workers;
  $("#setting-timeout").value = state.settings.manual_timeout;
  $("#setting-headless").checked = state.settings.headless;
  renderModeHint();
}

function renderModeHint() {
  const mode = $("#setting-mode").value;
  if (renderModeHint.mode !== mode) $("#run-repost").checked = false;
  renderModeHint.mode = mode;
  $("#start-run .btn-label").textContent = mode === "publish" ? "Start posting" : "Start login";
  $("#start-run .material-symbols-outlined").textContent = mode === "publish" ? "rocket_launch" : "play_arrow";
  const settings = readSettings();
  $("#advanced-summary").textContent = `${settings.workers} akun paralel · tunggu verifikasi ${settings.manual_timeout} dtk${settings.headless ? " · headless" : ""}`;
  const fab = $("#rail-run");
  if (!state.run?.running) { fab.title = mode === "publish" ? "Start posting" : "Start login"; $(".material-symbols-outlined", fab).textContent = mode === "publish" ? "rocket_launch" : "play_arrow"; }
  renderRunContent();
  renderGuide();
}

/* ---------- pilihan konten dan akun untuk run ---------- */

function accountKey(identifier) {
  return identifier.trim().toLowerCase();
}

function pruneRunSelections() {
  const previousSize = state.runSelection.size + state.accountSelection.size;
  const contents = new Set(state.jobs.map((job) => job.content_id));
  const accounts = new Set(state.accounts.map((account) => accountKey(account.identifier)));
  for (const id of state.runSelection) if (!contents.has(id)) state.runSelection.delete(id);
  for (const id of state.accountSelection) if (!accounts.has(id)) state.accountSelection.delete(id);
  if (state.runSelection.size + state.accountSelection.size !== previousSize) $("#run-repost").checked = false;
}

function selectedRunAccounts() {
  return state.accounts.filter((account) => state.accountSelection.has(accountKey(account.identifier)));
}

function pairStatus(contentId, account) {
  return state.run?.content_status?.[contentId]?.[accountKey(account.identifier)]?.status || null;
}

function ledgerStateFor(contentId, accounts) {
  if (!accounts.length) return "none";
  let published = 0;
  for (const account of accounts) {
    const status = pairStatus(contentId, account);
    if (status === "published") published += 1;
    else if (status) return "uncertain";
  }
  return published === accounts.length ? "published" : published ? "partial" : "fresh";
}

function runPlan() {
  const contents = selectedRunJobs();
  const accounts = selectedRunAccounts();
  let fresh = 0;
  let published = 0;
  let blocked = 0;
  for (const content of contents) for (const account of accounts) {
    const status = pairStatus(content.content_id, account);
    if (status === "published") published += 1;
    else if (status) blocked += 1;
    else fresh += 1;
  }
  const repeat = $("#setting-mode").value === "publish" && $("#run-repost").checked;
  const repost = repeat ? published : 0;
  return { contents, accounts, total: contents.length * accounts.length, fresh, published, blocked, repost, skipped: published - repost, ready: fresh + repost };
}

function renderRunPlan() {
  const publish = $("#setting-mode").value === "publish";
  const plan = runPlan();
  $("#run-repost-field").classList.toggle("hidden", !publish || !plan.published);
  if (!publish || !plan.published) $("#run-repost").checked = false;
  const summary = $("#run-plan-summary");
  let message;
  if (!publish) message = plan.accounts.length ? `Login untuk ${plan.accounts.length} akun terpilih.` : "Pilih minimal satu akun untuk login.";
  else if (!plan.contents.length || !plan.accounts.length) message = `${plan.contents.length} konten × ${plan.accounts.length} akun = 0 postingan. Pilih minimal satu konten dan satu akun tujuan.`;
  else {
    message = `${plan.contents.length} konten × ${plan.accounts.length} akun = ${plan.total} postingan: ${plan.fresh} baru, ${plan.repost} diulang, ${plan.skipped} sudah berhasil dilewati.`;
    if (plan.blocked) message += ` ${plan.blocked} hasil belum pasti; periksa profil Instagram dan catatan posting. Start diblokir, termasuk posting ulang.`;
    else if (!plan.ready) message += " Aktifkan posting ulang hanya jika memang ingin mengulang.";
  }
  setStatus(summary, message, publish && plan.blocked ? false : null);
  updateRunAvailability();
}

function renderRunContentStatus() {
  const accounts = selectedRunAccounts();
  const badges = {
    none: ["Pilih akun tujuan", "neutral", "person"],
    fresh: ["Belum diposting", "neutral", "radio_button_unchecked"],
    published: ["Sudah terposting", "ok", "check_circle"],
    partial: ["Sebagian terposting", "info", "pending"],
    uncertain: ["Perlu diperiksa", "warn", "warning"],
  };
  for (const item of $$("#run-content-list .run-content-item")) {
    const contentId = item.dataset.contentId;
    const selected = state.runSelection.has(contentId);
    item.classList.toggle("selected", selected);
    $("input", item).checked = selected;
    const status = ledgerStateFor(contentId, accounts);
    const badge = $(".run-content-status", item);
    if (badge.dataset.status !== status) {
      const [label, tone, glyph] = badges[status];
      badge.dataset.status = status;
      badge.className = `chip run-content-status ${tone}`;
      badge.replaceChildren(icon(glyph), el("span", { textContent: label }));
    }
  }
  $("#run-content-summary").textContent = `${state.runSelection.size} dari ${state.jobs.length} konten dipilih.`;
  $("#run-accounts-summary").textContent = `${accounts.length} dari ${state.accounts.length} akun dipilih.`;
  renderRunPlan();
}

function runChoicesChanged() {
  $("#run-repost").checked = false;
  if (!state.manifestError && !state.runStatusError) setStatus($("#settings-error"), "", null);
  for (const item of $$("#run-accounts-list .run-account-item")) {
    const selected = state.accountSelection.has(item.dataset.account);
    item.classList.toggle("selected", selected);
    $("input", item).checked = selected;
  }
  renderRunContentStatus();
}

function renderRunAccounts() {
  const list = $("#run-accounts-list");
  list.replaceChildren();
  if (!state.accounts.length) list.append(el("p", { class: "supporting-text", textContent: "Belum ada akun. Tambahkan lewat menu Akun." }));
  for (const account of state.accounts) {
    const id = accountKey(account.identifier);
    const checked = state.accountSelection.has(id);
    const box = el("input", { type: "checkbox", value: id, dataset: { account: id }, checked, ariaLabel: `Pilih akun ${account.identifier}`, onchange: () => {
      if (box.checked) state.accountSelection.add(id); else state.accountSelection.delete(id);
      runChoicesChanged();
    } });
    list.append(el("label", { class: `run-account-item ${checked ? "selected" : ""}`.trim(), dataset: { account: id } }, box,
      el("span", { class: "run-content-text" }, el("strong", { class: "body-medium", textContent: account.identifier }),
        el("span", { class: "body-small supporting", textContent: account.identity_bound ? "Login tersimpan" : "Belum terverifikasi" }))));
  }
}

function selectedRunJobs() {
  return state.jobs.filter((job) => state.runSelection.has(job.content_id));
}

function renderRunContent() {
  pruneRunSelections();
  const list = $("#run-content-list");
  const publish = $("#setting-mode").value === "publish";
  $("#run-content").classList.toggle("hidden", !publish);
  list.replaceChildren();
  if (!state.jobs.length) list.append(el("p", { class: "supporting-text", textContent: "Belum ada konten. Siapkan lewat menu Konten." }));
  for (const job of state.jobs) {
    const id = job.content_id;
    const box = el("input", { type: "checkbox", value: id, dataset: { contentId: id }, checked: state.runSelection.has(id), ariaLabel: `Pilih konten ${job.name}`, onchange: () => {
      if (box.checked) state.runSelection.add(id); else state.runSelection.delete(id);
      runChoicesChanged();
    } });
    const first = mediaByPath(job.media[0]?.path);
    const thumb = el("span", { class: `run-content-thumb ${KIND[job.kind].tone}` }, first && first.kind === "image" ? el("img", { src: mediaUrl(first), alt: "" }) : icon(first ? "videocam" : KIND[job.kind].icon));
    const badge = chip("", "neutral");
    badge.classList.add("run-content-status");
    list.append(el("label", { class: "run-content-item", dataset: { contentId: id } }, box, thumb,
      el("span", { class: "run-content-text" },
        el("strong", { class: "body-medium", textContent: job.name || "(tanpa nama)" }),
        el("span", { class: "body-small supporting", textContent: `${KIND[job.kind].label}${job.caption ? ` · ${job.caption.split("\n")[0].slice(0, 60)}${job.caption.length > 60 ? "…" : ""}` : ""}` })), badge));
  }
  renderRunContentStatus();
}

function readSettings() {
  return {
    mode: $("#setting-mode").value,
    workers: Number($("#setting-workers").value),
    manual_timeout: Number($("#setting-timeout").value),
    headless: $("#setting-headless").checked,
  };
}

/* ---------- jobs ---------- */

function cropChoices(kind) {
  return kind === "reel" ? REEL_CROPS : PHOTO_CROPS;
}

function mediaOptions(select, filter, value, placeholder = "— pilih media —") {
  select.replaceChildren(new Option(placeholder, ""));
  state.media.filter(filter).forEach((item) => select.append(new Option(`${item.name} (${formatBytes(item.bytes)})`, item.path)));
  select.value = value && state.media.some((item) => item.path === value) ? value : "";
}

/* Ringkasan opsi lanjutan yang terisi, untuk summary expander & chip di kepala kartu. */
function advancedSummary(job) {
  const parts = [];
  if (job.location) parts.push(`lokasi ${job.location}`);
  if (job.collaborators.length) parts.push(`${job.collaborators.length} kolaborator`);
  if (job.kind === "reel" && job.people_tags.length) parts.push(`${job.people_tags.length} tag`);
  if (job.ai_label !== null) parts.push(`label AI ${job.ai_label ? "aktif" : "nonaktif"}`);
  if (job.comments_enabled !== null) parts.push(job.comments_enabled ? "komentar diizinkan" : "komentar dimatikan");
  if (job.hide_like_view_counts !== null) parts.push(job.hide_like_view_counts ? "like/view disembunyikan" : "like/view ditampilkan");
  if (job.kind === "reel") {
    if (job.cover_path) parts.push("cover gambar");
    else if (job.cover_time !== null) parts.push(`cover detik ${job.cover_time}`);
    if (job.trim_start !== null || job.trim_end !== null) parts.push(`trim ${job.trim_start ?? 0}–${job.trim_end ?? "akhir"} dtk`);
    if (job.keep_original_audio !== null) parts.push(`audio asli ${job.keep_original_audio ? "aktif" : "nonaktif"}`);
  } else {
    const perPhoto = job.media.filter((item) => item.alt_text || item.people_tags.length || item.zoom !== null || item.filter_name || Object.keys(item.adjustments).length).length;
    if (perPhoto) parts.push(`${perPhoto} foto dengan penyesuaian`);
  }
  return parts;
}

/* Pilih media dari galeri input/media lewat dialog bergambar; boleh impor file baru dari sini. */
function pickMediaDialog({ kind, exclude = [] }) {
  const accepts = (item) => kind === "carousel" || item.kind === (kind === "reel" ? "video" : "image");
  const label = kind === "reel" ? "video" : kind === "carousel" ? "foto atau video" : "foto";
  let chosen = null;
  let active = true;
  const grid = el("div", { class: "picker-grid" });
  const paint = () => {
    grid.replaceChildren();
    const items = state.media.filter(accepts);
    if (!items.length) grid.append(el("p", { class: "supporting-text", textContent: `Belum ada ${label} di folder media.` }));
    for (const item of items) {
      const used = exclude.includes(item.path);
      const tile = el("button", { type: "button", class: `picker-tile ${used ? "used" : ""} ${chosen === item.path ? "selected" : ""}`, disabled: used, ariaPressed: String(chosen === item.path), title: used ? "Sudah dipakai konten ini" : item.name, onclick: () => {
        chosen = item.path;
        $$(".picker-tile", grid).forEach((node) => { node.classList.toggle("selected", node === tile); node.ariaPressed = String(node === tile); });
        $("#dialog-confirm").disabled = false;
      } },
        item.kind === "video" ? el("video", { src: mediaUrl(item), muted: true, preload: "metadata", playsInline: true }) : el("img", { src: mediaUrl(item), alt: "", loading: "lazy" }),
        el("span", { class: "picker-name", textContent: item.name }));
      grid.append(tile);
    }
  };
  paint();
  const importButton = el("button", { type: "button", class: "btn text", onclick: async () => {
    importButton.disabled = true;
    $("#dialog-confirm").disabled = true;
    try {
      const result = await api("/api/media/pick", {});
      if (!ownsDialog()) return;
      // Jangan refresh kartu: dialog masih memegang job dan DOM yang sedang disunting.
      const snapshot = await api("/api/state");
      if (!ownsDialog()) return;
      state.media = snapshot.media;
      renderMedia(); renderStats();
      const fresh = result.imported.find((entry) => !exclude.includes(entry.path) && state.media.some((item) => item.path === entry.path && accepts(item)));
      if (fresh) chosen = fresh.path;
      paint();
    } catch (error) { if (ownsDialog()) snackbar(error.message); }
    finally { if (ownsDialog()) { importButton.disabled = false; $("#dialog-confirm").disabled = !chosen; } }
  } }, icon("folder_open"), el("span", { textContent: "Impor file baru" }));
  const content = el("div", { class: "picker" }, grid, el("div", { class: "form-row" }, importButton));
  const ownsDialog = () => active && content.isConnected && $("#dialog").open;
  const result = dialog({ title: `Pilih ${label}`, text: "Pilih satu file, lalu tekan Pakai.", confirmLabel: "Pakai", iconName: kind === "reel" ? "videocam" : "image", content,
    onConfirm: () => chosen || undefined });
  $("#dialog-confirm").disabled = true;
  return result.finally(() => { active = false; });
}

function jobMatches(job, query) {
  if (!query) return true;
  const haystack = `${job.name} ${job.kind} ${job.caption}`.toLowerCase();
  return query.split(/\s+/).filter(Boolean).every((term) => haystack.includes(term));
}

function renderJobs() {
  const container = $("#job-list");
  container.replaceChildren();
  if (!state.jobs.length) container.append(el("p", { class: "supporting-text", textContent: "Belum ada konten. Klik “Konten baru”." }));
  // Kartu yang dimuat dari file tertutup: pengguna membuka kartu yang ingin disunting.
  state.jobs.forEach((job) => { if (job._open === undefined) job._open = false; });
  const query = $("#job-filter").value.trim().toLowerCase();
  let shown = 0;
  state.jobs.forEach((job, index) => {
    const node = renderJob(job, index);
    if (!jobMatches(job, query)) node.classList.add("filtered-out"); else shown += 1;
    container.append(node);
  });
  $("#job-filter-count").textContent = query ? `${shown} dari ${state.jobs.length} konten` : "";
  $("#jobs-dirty").classList.toggle("hidden", !state.dirty);
  renderFoldToggle();
}

/* Satu tombol lipat/buka: label mengikuti keadaan mayoritas kartu. */
function renderFoldToggle() {
  const button = $("#fold-toggle");
  button.classList.toggle("hidden", state.jobs.length < 2);
  const anyOpen = $$("#job-list .job:not(.collapsed)").length > 0;
  $(".material-symbols-outlined", button).textContent = anyOpen ? "unfold_less" : "unfold_more";
  $(".btn-label", button).textContent = anyOpen ? "Lipat semua" : "Buka semua";
  $$("#job-list .job").forEach((node) => {
    const expanded = !node.classList.contains("collapsed");
    const toggle = $(".job-toggle", node);
    toggle.ariaExpanded = String(expanded);
    toggle.ariaLabel = `${expanded ? "Lipat" : "Buka"} ${$(".job-title", node).textContent}`;
  });
}

function applyFilter() {
  const query = $("#job-filter").value.trim().toLowerCase();
  let shown = 0;
  $$("#job-list .job").forEach((node, index) => {
    const match = jobMatches(state.jobs[index], query);
    node.classList.toggle("filtered-out", !match);
    if (match) shown += 1;
  });
  $("#job-filter-count").textContent = query ? `${shown} dari ${state.jobs.length} konten` : "";
}


function captionMeta(job, container) {
  const hashtags = (job.caption.match(/#[\p{L}\p{N}_]+/gu) || []).length;
  const mentions = (job.caption.match(/@[A-Za-z0-9._]+/g) || []).length;
  const length = Array.from(job.caption).length;
  container.replaceChildren(
    chip(`${length}/2200 karakter`, length > 2200 ? "bad" : "neutral", "notes"),
    chip(`${hashtags}/5 hashtag`, hashtags > 5 ? "bad" : hashtags ? "info" : "neutral", "tag"),
    chip(`${mentions} mention`, mentions ? "info" : "neutral", "alternate_email"));
}

function renderJob(job, index) {
  const node = $("#job-template").content.firstElementChild.cloneNode(true);
  node.dataset.contentId = job.content_id;
  $$(".field", node).forEach((wrapper) => {
    const control = $(".control", wrapper);
    const label = $("label", wrapper);
    if (control && label) { control.id = `editor-field-${++fieldSequence}`; label.htmlFor = control.id; }
  });
  const isReel = job.kind === "reel";
  const kind = KIND[job.kind];
  const thumb = $(".job-thumb", node);
  thumb.classList.add(kind.tone);
  const setTitle = () => {
    $(".job-title", node).textContent = `${index + 1}. ${job.name || "(tanpa nama)"}`;
    const mediaCount = job.media.filter((item) => item.path).length;
    const mediaText = job.kind === "carousel" ? `${mediaCount} media` : (mediaCount ? "" : (isReel ? "belum ada video" : "belum ada foto"));
    $(".job-subtitle", node).textContent = [kind.label, mediaText].filter(Boolean).join(" · ");
    const first = mediaByPath(job.media[0]?.path);
    thumb.replaceChildren(first && first.kind === "image" ? el("img", { src: mediaUrl(first), alt: "" }) : icon(first ? "videocam" : kind.icon));
    const advanced = advancedSummary(job);
    const advCount = $(".adv-count", node);
    advCount.classList.toggle("hidden", !advanced.length);
    advCount.textContent = advanced.length ? `${advanced.length} diubah` : "";
    $(".adv-hint", node).textContent = advanced.length ? advanced.join(" · ") : "lokasi, kolaborator, label AI, komentar, like/view" + (isReel ? ", cover, trim, audio" : ", alt text, tag orang, filter");
  };
  $(".f-job-id", node).value = job.name;
  $(".f-kind", node).value = job.kind;
  $(".f-caption", node).value = job.caption;
  captionMeta(job, $(".caption-meta", node));
  $(".f-location", node).value = job.location;
  $(".f-collaborators", node).value = job.collaborators.join(", ");
  $(".f-reel-tags", node).value = job.people_tags.map((tag) => tag.username).join(", ");
  $(".f-ai", node).value = tri(job.ai_label);
  $(".f-comments", node).value = tri(job.comments_enabled);
  $(".f-hide-counts", node).value = tri(job.hide_like_view_counts);
  $(".f-cover-time", node).value = job.cover_time ?? "";
  $(".f-trim-start", node).value = job.trim_start ?? "";
  $(".f-trim-end", node).value = job.trim_end ?? "";
  $(".f-audio", node).value = tri(job.keep_original_audio);
  mediaOptions($(".f-cover", node), (item) => /\.(jpe?g|png)$/i.test(item.name), job.cover_path, "Tidak ada");
  $$(".reel-only", node).forEach((part) => part.classList.toggle("hidden", !isReel));
  $$(".photo-only", node).forEach((part) => part.classList.toggle("hidden", isReel));

  /* Kolom kiri: pratinjau besar media aktif, strip carousel, tombol ganti/tambah/lepas, chip crop. */
  const preview = $(".job-preview", node);
  const strip = $(".job-strip", node);
  const mediaActions = $(".job-media-actions", node);
  const mediaName = $(".job-media-name", node);
  let active = 0;
  const limit = job.kind === "carousel" ? 20 : 1;
  const choose = async (position) => {
    const originalKind = job.kind;
    const originalPaths = job.media.map((item) => item.path);
    const path = await pickMediaDialog({ kind: job.kind, exclude: job.media.map((item) => item.path).filter((item, i) => item && i !== position) });
    if (!path) return;
    const current = state.jobs.find((item) => item.content_id === job.content_id);
    if (!current || current.kind !== originalKind || current.media.length !== originalPaths.length || current.media.some((item, i) => item.path !== originalPaths[i])) {
      snackbar("Konten berubah saat memilih media. Buka pemilih lagi.");
      return;
    }
    const media = mediaByPath(path);
    const sameKind = mediaByPath(current.media[position]?.path)?.kind === media?.kind;
    if (position >= current.media.length) current.media.push(normalizeMedia({ path, crop: current.media[0]?.crop || null }));
    else if (sameKind) current.media[position].path = path;
    else current.media[position] = normalizeMedia({ path, crop: current.media[0]?.crop || null });
    if (current !== job || !node.isConnected) {
      current._open = true;
      markDirty(); renderMedia(); renderJobs();
      return;
    }
    active = position;
    markDirty(); renderMedia(); setTitle(); renderPreview(); renderPerMedia();
    mediaOptions($(".f-cover", node), (entry) => /\.(jpe?g|png)$/i.test(entry.name), job.cover_path, "Tidak ada");
  };
  const renderPreview = () => {
    active = Math.min(active, Math.max(job.media.length - 1, 0));
    const item = job.media[active];
    const media = item ? mediaByPath(item.path) : null;
    preview.replaceChildren();
    preview.classList.toggle("empty", !media);
    preview.dataset.crop = media && item.crop && item.crop !== "Original" ? item.crop : "";
    if (media) preview.append(media.kind === "video" ? el("video", { src: mediaUrl(media), muted: true, controls: true, preload: "metadata", playsInline: true }) : el("img", { src: mediaUrl(media), alt: media.name }));
    else preview.append(el("button", { type: "button", class: "job-preview-pick", onclick: () => choose(active) }, icon(isReel ? "videocam" : "add_photo_alternate"), el("span", { class: "label-large", textContent: isReel ? "Pilih video" : "Pilih foto" })));
    mediaName.textContent = media ? `${job.kind === "carousel" ? `${active + 1}/${job.media.length} · ` : ""}${media.name} · ${formatBytes(media.bytes)}` : (item?.path ? `${item.path} (tidak ada di folder media)` : "");
    mediaName.classList.toggle("bad", !!item?.path && !media);
    mediaActions.replaceChildren();
    if (media || item?.path) mediaActions.append(el("button", { type: "button", class: "btn text", onclick: () => choose(active) }, icon("photo_library"), el("span", { textContent: "Ganti" })));
    if (job.kind === "carousel" && job.media.length < limit) mediaActions.append(el("button", { type: "button", class: "btn text", onclick: () => choose(job.media.length) }, icon("add"), el("span", { textContent: `Tambah (${job.media.length}/${limit})` })));
    if (item && (job.kind === "carousel" || media || item.path)) mediaActions.append(el("button", { type: "button", class: "btn text error", onclick: () => { job.media.splice(active, 1); if (!job.media.length && job.kind !== "carousel") job.media.push(normalizeMedia({})); markDirty(); renderMedia(); setTitle(); renderPreview(); renderPerMedia(); } }, icon("close"), el("span", { textContent: "Lepas" })));
    if (job.kind === "carousel" && job.media.length > 1) {
      const move = (offset) => {
        const destination = active + offset;
        [job.media[active], job.media[destination]] = [job.media[destination], job.media[active]];
        active = destination;
        markDirty(); setTitle(); renderPreview(); renderPerMedia();
      };
      mediaActions.append(
        el("button", { type: "button", class: "icon-btn", title: "Majukan urutan media", ariaLabel: "Majukan urutan media", disabled: active === 0, onclick: () => move(-1) }, icon("arrow_upward")),
        el("button", { type: "button", class: "icon-btn", title: "Mundurkan urutan media", ariaLabel: "Mundurkan urutan media", disabled: active === job.media.length - 1, onclick: () => move(1) }, icon("arrow_downward")));
    }
    strip.classList.toggle("hidden", job.kind !== "carousel");
    strip.replaceChildren();
    if (job.kind === "carousel") {
      job.media.forEach((entry, position) => {
        const thumbMedia = mediaByPath(entry.path);
        strip.append(el("button", { type: "button", class: `strip-item ${position === active ? "active" : ""}`, ariaPressed: String(position === active), ariaLabel: `Media ${position + 1}: ${thumbMedia?.name || "belum dipilih"}`, title: thumbMedia ? thumbMedia.name : "(belum dipilih)", onclick: () => { active = position; renderPreview(); } },
          thumbMedia ? (thumbMedia.kind === "video" ? icon("videocam") : el("img", { src: mediaUrl(thumbMedia), alt: "" })) : icon("image"),
          el("span", { class: "strip-num", textContent: position + 1 })));
      });
    }
    renderCropChips();
  };
  const cropChips = $(".crop-chips", node);
  const renderCropChips = () => {
    cropChips.replaceChildren();
    const current = job.media[0]?.crop || "";
    for (const [value, label] of [["", "Bawaan Instagram"], ...cropChoices(job.kind).map((crop) => [crop, crop === "Original" ? "Asli" : crop])]) {
      cropChips.append(el("button", { type: "button", class: `filter-chip ${current === value ? "selected" : ""}`, ariaPressed: String(current === value), disabled: !job.media.length, onclick: () => {
        job.media.forEach((entry) => { entry.crop = value || null; });
        markDirty(); renderPreview();
      } }, current === value ? icon("check") : null, el("span", { textContent: label })));
    }
  };

  const perMedia = $(".per-media", node);
  const renderPerMedia = () => {
    perMedia.replaceChildren();
    if (isReel) return;
    const photos = job.media.map((item, position) => ({ item, position })).filter(({ item }) => item.path && mediaByPath(item.path)?.kind !== "video");
    if (!photos.length) perMedia.append(el("p", { class: "supporting-text", textContent: "Belum ada foto untuk disesuaikan." }));
    photos.forEach(({ item, position }) => perMedia.append(renderPerMediaItem(item, position, setTitle)));
  };

  setTitle();
  renderPreview();
  renderPerMedia();

  const bind = (selector, handler) => $(selector, node).addEventListener("input", (event) => { handler(event.target); markDirty(); setTitle(); });
  bind(".f-job-id", (input) => { job.name = input.value.trim(); });
  bind(".f-caption", (input) => { job.caption = input.value; captionMeta(job, $(".caption-meta", node)); });
  bind(".f-location", (input) => { job.location = input.value.trim(); });
  bind(".f-collaborators", (input) => { job.collaborators = splitUsernames(input.value); });
  bind(".f-reel-tags", (input) => { job.people_tags = splitUsernames(input.value).map((username) => ({ username })); });
  bind(".f-ai", (input) => { job.ai_label = fromTri(input.value); });
  bind(".f-comments", (input) => { job.comments_enabled = fromTri(input.value); });
  bind(".f-hide-counts", (input) => { job.hide_like_view_counts = fromTri(input.value); });
  bind(".f-cover-time", (input) => { job.cover_time = input.value === "" ? null : Number(input.value); });
  bind(".f-trim-start", (input) => { job.trim_start = input.value === "" ? null : Number(input.value); });
  bind(".f-trim-end", (input) => { job.trim_end = input.value === "" ? null : Number(input.value); });
  bind(".f-audio", (input) => { job.keep_original_audio = fromTri(input.value); });
  bind(".f-cover", (input) => { job.cover_path = input.value; });
  $(".f-kind", node).addEventListener("change", (event) => {
    job.kind = event.target.value;
    const wantVideo = job.kind === "reel";
    // Media yang jenisnya tidak cocok dilepas; slot kosong tetap ada agar pratinjau menawarkan "Pilih".
    job.media = job.media.filter((item) => { const media = mediaByPath(item.path); return job.kind === "carousel" || !media || (media.kind === "video") === wantVideo; });
    job.media = job.kind === "carousel" ? job.media.slice(0, 20) : job.media.slice(0, 1);
    if (!job.media.length) job.media.push(normalizeMedia({}));
    if (job.kind !== "reel") { job.people_tags = []; job.cover_path = ""; job.cover_time = null; job.trim_start = null; job.trim_end = null; job.keep_original_audio = null; }
    job.media.forEach((item) => { item.crop = null; });
    job._open = true;
    markDirty();
    renderJobs();
  });
  $(".job-remove", node).addEventListener("click", async () => {
    const ok = await dialog({ title: "Hapus konten?", text: `${job.name || `Konten ${index + 1}`} dihapus dari daftar. Berlaku setelah Simpan.`, confirmLabel: "Hapus", iconName: "delete", danger: true });
    if (!ok) return;
    const currentIndex = state.jobs.findIndex((item) => item.content_id === job.content_id);
    if (currentIndex < 0) return;
    state.jobs.splice(currentIndex, 1); markDirty(); renderJobs();
  });
  $(".job-duplicate", node).addEventListener("click", () => {
    const copy = normalizeJob(JSON.parse(JSON.stringify(job)));
    copy.content_id = crypto.randomUUID().replaceAll("-", "");
    copy.name = nextContentName(job.name);
    copy._open = true;
    $("#job-filter").value = "";
    state.jobs.splice(index + 1, 0, copy); markDirty(); renderJobs();
  });
  node.classList.toggle("collapsed", job._open === false);
  $(".job-toggle", node).addEventListener("click", () => { job._open = node.classList.contains("collapsed"); node.classList.toggle("collapsed", !job._open); renderFoldToggle(); });
  $(".job-head", node).addEventListener("click", (event) => { if (!event.target.closest("button, a, input")) $(".job-toggle", node).click(); });
  $(".job-up", node).disabled = index === 0;
  $(".job-down", node).disabled = index === state.jobs.length - 1;
  $(".job-up", node).addEventListener("click", () => { if (index > 0) { [state.jobs[index - 1], state.jobs[index]] = [state.jobs[index], state.jobs[index - 1]]; markDirty(); renderJobs(); } });
  $(".job-down", node).addEventListener("click", () => { if (index < state.jobs.length - 1) { [state.jobs[index + 1], state.jobs[index]] = [state.jobs[index], state.jobs[index + 1]]; markDirty(); renderJobs(); } });
  return node;
}

function nextContentName(base) {
  const taken = new Set(state.jobs.map((job) => job.name));
  const match = /^(.*?)(\d{1,9})$/.exec(base || "konten-000");
  const prefix = match ? match[1] : `${base || "konten"}-`;
  let number = match ? Number(match[2]) : 0;
  const width = match ? match[2].length : 3;
  let candidate;
  do {
    number += 1;
    const suffix = String(number).padStart(width, "0");
    candidate = `${prefix.slice(0, 128 - suffix.length)}${suffix}`;
  } while (taken.has(candidate));
  return candidate;
}

function slider({ label, min, max, value, onInput, onReset }) {
  const out = el("output", { textContent: value ?? "default" });
  const range = el("input", { id: `editor-field-${++fieldSequence}`, type: "range", min, max, step: 1, value: value ?? (min < 0 ? 0 : min), oninput: () => { onInput(Number(range.value)); out.textContent = range.value; } });
  const reset = el("button", { type: "button", class: "icon-btn", title: "Kembalikan ke default", ariaLabel: "Kembalikan ke default", onclick: () => { onReset(); range.value = min < 0 ? 0 : min; out.textContent = "default"; } }, icon("restart_alt"));
  return el("div", { class: "slider" }, el("span", { class: "slider-head" }, el("label", { htmlFor: range.id, textContent: `${label} (${min}..${max})` }), el("span", {}, out, reset)), range);
}

function renderPerMediaItem(item, position, onChange = () => {}) {
  const fieldset = el("fieldset", {});
  const media = mediaByPath(item.path);
  const changed = () => { markDirty(); onChange(); };
  fieldset.append(el("legend", {}, media ? el("img", { src: mediaUrl(media), alt: "" }) : icon("image"), el("span", { textContent: media ? `${position + 1}. ${media.name}` : `${position + 1}. (media belum dipilih)` })));

  const alt = el("input", { class: "control", placeholder: " ", value: item.alt_text, oninput: () => { item.alt_text = alt.value; changed(); } });
  fieldset.append(field(alt, "Alt text (deskripsi foto untuk aksesibilitas)"));

  const tagsList = el("div", { class: "tags-list" });
  const renderTags = () => {
    tagsList.replaceChildren();
    item.people_tags.forEach((tag, tagIndex) => {
      const username = el("input", { class: "control", placeholder: " ", value: tag.username, oninput: () => { tag.username = username.value.trim().replace(/^@/, ""); changed(); } });
      const x = el("input", { class: "control", type: "number", min: 0, max: 1, step: 0.01, placeholder: " ", value: tag.x ?? "", oninput: () => { tag.x = x.value === "" ? null : Number(x.value); changed(); } });
      const y = el("input", { class: "control", type: "number", min: 0, max: 1, step: 0.01, placeholder: " ", value: tag.y ?? "", oninput: () => { tag.y = y.value === "" ? null : Number(y.value); changed(); } });
      const remove = el("button", { type: "button", class: "icon-btn", title: "Hapus tag", ariaLabel: "Hapus tag", onclick: () => { item.people_tags.splice(tagIndex, 1); changed(); renderTags(); } }, icon("close"));
      tagsList.append(el("div", { class: "tag-row" }, field(username, "Username"), field(x, "x (0–1)"), field(y, "y (0–1)"), remove));
    });
    tagsList.append(el("div", { class: "form-row" },
      el("button", { type: "button", class: "btn text", onclick: () => { item.people_tags.push({ username: "", x: null, y: null }); changed(); renderTags(); } }, icon("person_add"), el("span", { textContent: "Tambah tag orang" })),
      el("span", { class: "supporting-text", textContent: "x/y = posisi tag pada foto (0–1); kosongkan untuk posisi bawaan." })));
  };
  renderTags();
  fieldset.append(el("h4", { class: "label-large section-label", textContent: "Tag orang" }), tagsList);

  const filter = el("select", { class: "control", onchange: () => { item.filter_name = filter.value || null; changed(); } }, new Option("Tanpa filter", ""));
  FILTERS.forEach((name) => filter.append(new Option(name, name)));
  filter.value = item.filter_name || "";
  fieldset.append(el("div", { class: "grid-2" },
    slider({ label: "Zoom", min: 0, max: 100, value: item.zoom, onInput: (value) => { item.zoom = value; changed(); }, onReset: () => { item.zoom = null; changed(); } }),
    field(filter, "Filter")));

  const adjustGrid = el("div", { class: "adjust-grid" });
  for (const [key, label, min, max] of ADJUSTMENTS) {
    adjustGrid.append(slider({ label, min, max, value: item.adjustments[key] ?? null,
      onInput: (value) => { item.adjustments[key] = value; changed(); },
      onReset: () => { delete item.adjustments[key]; changed(); } }));
  }
  fieldset.append(el("h4", { class: "label-large section-label", textContent: "Penyesuaian" }), adjustGrid);
  return fieldset;
}

/* Pemeriksaan awal berbahasa manusia sebelum parser engine: mengembalikan {index, message} atau null.
   Parser engine tetap sumber kebenaran; ini hanya menangkap kesalahan umum lebih ramah. */
function firstJobProblem() {
  const names = new Set();
  for (const [index, job] of state.jobs.entries()) {
    const name = job.name || `konten #${index + 1}`;
    if (!job.name) return { index, message: `Konten #${index + 1}: isi nama konten.` };
    if (names.has(job.name)) return { index, message: `Nama konten "${job.name}" dipakai dua kali; ganti salah satunya.` };
    names.add(job.name);
    const missing = job.media.filter((item) => !item.path).length;
    if (missing || !job.media.length) return { index, message: `${name}: ${job.kind === "reel" ? "pilih videonya" : job.kind === "carousel" ? `${missing || 1} slot media masih kosong` : "pilih fotonya"} dahulu.` };
    if (job.kind === "carousel" && job.media.length < 2) return { index, message: `${name}: carousel butuh minimal 2 media.` };
  }
  return null;
}

/* Simpan = periksa (parser engine yang sama) lalu tulis. Dipakai tombol Simpan dan Start (simpan otomatis). */
async function saveJobs(prefix = "Valid dan tersimpan") {
  if (state.manifestError) throw new Error(manifestErrorMessage());
  if (state.saving) throw new Error("Penyimpanan masih berjalan; tunggu sebentar.");
  const problem = firstJobProblem();
  if (problem) {
    navigate("konten");
    $("#job-filter").value = "";
    applyFilter();
    setStatus($("#jobs-status"), problem.message, false);
    const node = $$("#job-list .job")[problem.index];
    if (node) { state.jobs[problem.index]._open = true; node.classList.remove("collapsed"); node.scrollIntoView({ block: "center" }); }
    renderFoldToggle();
    throw new Error(problem.message);
  }
  const revision = state.editRevision;
  const document = manifestFromState();
  snapshotGeneration++;
  state.saving = true;
  $("#save-jobs").disabled = true;
  try {
    const result = await api("/api/jobs", { manifest: document });
    if (state.editRevision !== revision) throw new Error("Versi sebelumnya tersimpan. Ada perubahan baru; tekan Simpan lagi.");
    state.dirty = false;
    savedContents = document.contents;
    setStatus($("#jobs-status"), `${prefix}: ${result.content_ids.length} konten.`, true);
    return result;
  } finally {
    state.saving = false;
    $("#save-jobs").disabled = !!state.manifestError;
  }
}

function manifestFromState() {
  return {
    version: 2,
    contents: state.jobs.map((job) => {
      const entry = { content_id: job.content_id, name: job.name, kind: job.kind, caption: job.caption };
      entry.media = job.media.map((item) => {
        const media = { path: item.path };
        if (item.alt_text) media.alt_text = item.alt_text;
        if (item.people_tags.length) media.people_tags = item.people_tags.map((tag) => tag.x === null && tag.y === null ? { username: tag.username } : { username: tag.username, x: tag.x, y: tag.y });
        if (item.crop) media.crop = item.crop;
        if (item.zoom !== null) media.zoom = item.zoom;
        if (item.filter_name) media.filter_name = item.filter_name;
        if (Object.keys(item.adjustments).length) media.adjustments = { ...item.adjustments };
        return media;
      });
      if (job.location) entry.location = job.location;
      if (job.collaborators.length) entry.collaborators = job.collaborators;
      if (job.kind === "reel" && job.people_tags.length) entry.people_tags = job.people_tags;
      for (const key of ["ai_label", "comments_enabled", "hide_like_view_counts"]) if (job[key] !== null) entry[key] = job[key];
      if (job.kind === "reel") {
        if (job.cover_path) entry.cover_path = job.cover_path;
        if (job.cover_time !== null) entry.cover_time = job.cover_time;
        if (job.trim_start !== null) entry.trim_start = job.trim_start;
        if (job.trim_end !== null) entry.trim_end = job.trim_end;
        if (job.keep_original_audio !== null) entry.keep_original_audio = job.keep_original_audio;
      }
      return entry;
    }),
  };
}

/* ---------- history / environment ---------- */

function renderHistory() {
  const body = $("#history-table tbody");
  body.replaceChildren();
  if (!state.history.length) emptyRow(body, 5, "Belum ada proses dari UI.");
  for (const entry of [...state.history].reverse()) {
    const rows = entry.results || [];
    const okCount = rows.filter((row) => resultTone(row) === "ok").length;
    const ok = rows.length && okCount === rows.length && entry.exit_code === 0;
    const summary = rows.length ? `${okCount}/${rows.length} selesai tanpa masalah` : entry.exit_code === 130 ? "dihentikan" : entry.exit_code === 2 ? "input tidak valid" : entry.exit_code === 0 ? "selesai" : "gagal";
    const logRow = el("tr", { class: "hidden" }, el("td", { colSpan: 5 }, el("pre", { class: "log", textContent: entry.log_tail || "(log kosong)" })));
    const toggle = el("button", { type: "button", class: "icon-btn", title: "Lihat log", ariaLabel: "Lihat log", onclick: () => logRow.classList.toggle("hidden") }, icon("terminal"));
    body.append(el("tr", {},
      el("td", { class: "nowrap", textContent: formatTime(entry.started_at) }),
      el("td", {}, chip(MODE_LABEL[entry.settings?.mode] || "Mode lama", entry.settings?.mode === "publish" ? "bad" : "info")),
      el("td", {}, el("strong", { textContent: runAccountLabel(entry), title: (entry.accounts || []).join(", ") }),
        entry.content_names?.length ? el("div", { class: "body-small supporting", textContent: entry.content_names.join(", ") }) : null),
      el("td", {}, chip(summary, ok ? "ok" : rows.length || entry.exit_code !== 0 ? "warn" : "neutral", ok ? "done_all" : null)),
      el("td", {}, toggle)), logRow);
  }
}

function renderEnvironment() {
  const list = $("#env-list");
  const env = state.environment;
  const rows = [
    ["Python", env.python, "terminal"], ["Playwright", env.playwright, "data_object"],
    ["Chrome portable", env.chrome, "open_in_new", env.chrome_ok ? chip("ditemukan", "ok", "check_circle") : chip("tidak ditemukan", "bad", "error")],
    ["Folder proyek", env.project_dir, "folder"], ["Profil session browser", env.session_root, "account_circle"],
    ["File akun", state.paths.accounts, "manage_accounts"], ["Folder media", state.paths.media, "perm_media"], ["File konten", state.paths.jobs, "list_alt"],
    ["Hasil proses", state.paths.results, "monitoring"], ["Ledger publikasi", env.ledger_file, "receipt_long"], ["Riwayat proses UI", env.history_file, "history"],
    ["Log engine", state.paths.log, "description"], ["Arsip media dihapus", env.media_trash, "inbox"],
  ];
  const pathSpan = (value) => {
    const span = el("span", { class: "path" });
    const parts = String(value ?? "—").split(/([\\/])/);
    parts.forEach((part) => { span.append(part); if (part === "\\" || part === "/") span.append(document.createElement("wbr")); });
    return span;
  };
  list.replaceChildren(...rows.map(([label, value, iconName, badge]) => el("div", {}, el("dt", {}, icon(iconName), el("span", { textContent: label })), el("dd", {}, badge, pathSpan(value)))));
}

/* ---------- run ---------- */

function renderLog(container, text) {
  const stick = container.scrollTop + container.clientHeight >= container.scrollHeight - 4;
  container.replaceChildren(...(text || "").split("\n").map((line) => {
    const level = /\[ERROR\]|\[CRITICAL\]/.test(line) ? "lvl-error" : /\[WARNING\]/.test(line) ? "lvl-warning" : "lvl-info";
    return el("span", { class: level, textContent: `${line}\n` });
  }));
  if (stick) container.scrollTop = container.scrollHeight;
}

/* ---------- guide strip ---------- */

function renderGuide() {
  const run = state.run;
  const savedJobs = state.jobs.length && !state.dirty;
  const done = { accounts: state.accounts.length > 0, media: state.media.length > 0, jobs: savedJobs, run: !!(run && !run.running && run.exit_code === 0) };
  const order = ["accounts", "media", "jobs", "run"];
  const current = order.find((key) => !done[key]) || "run";
  order.forEach((key) => {
    const step = $(`.step[data-step="${key}"]`);
    step.classList.toggle("done", done[key] && key !== current);
    step.classList.toggle("current", key === current);
  });
  const mode = $("#setting-mode").value;
  const action = $("#guide-action");
  const text = $("#guide-text");
  const iconNode = $("#guide-icon");
  let next;
  if (run?.running) next = { icon: "progress_activity", text: "Proses sedang berjalan. Hasil muncul di menu Jalankan.", label: null };
  else if (state.manifestError) next = { icon: "error", text: state.manifestError === CATALOG_VERSION_ERROR ? CATALOG_VERSION_ERROR : "File konten tidak dapat dibaca. Perbaiki file secara manual, lalu muat ulang.", label: "Lihat detail", route: "konten" };
  else if (mode === "login" && !done.accounts) next = { icon: "person_add", text: "Tambahkan akun Instagram untuk login; konten bisa disiapkan terpisah.", label: "Tambah akun", route: "akun", go: "#account-identifier" };
  else if (mode === "login" && done.run && run.settings?.mode === "login") next = { icon: "rocket_launch", text: "Login selesai. Siapkan konten dan pilih akun tujuan saat akan posting.", label: "Pilih posting", route: "jalankan", go: "mode:publish" };
  else if (mode === "login") next = { icon: "login", text: "Pilih akun yang akan login di menu Jalankan, lalu tekan Start login.", label: "Pilih akun", route: "jalankan" };
  else if (!done.media) next = { icon: "add_photo_alternate", text: "Pilih foto atau video untuk konten; belum perlu memilih akun.", label: "Pilih media", route: "media", go: "#pick-media", click: true };
  else if (!state.jobs.length) next = { icon: "add", text: "Buat konten, tulis caption, lalu simpan.", label: "Buat konten", route: "konten", go: "#new-job", click: true };
  else if (state.dirty) next = { icon: "save", text: "Konten belum disimpan; akun tujuan dipilih nanti di Jalankan.", label: "Simpan", route: "konten", go: "#save-jobs", click: true };
  else if (!done.accounts) next = { icon: "person_add", text: "Konten siap. Tambahkan akun tujuan sebelum menjalankan.", label: "Tambah akun", route: "akun", go: "#account-identifier" };
  else next = { icon: "rocket_launch", text: "Pilih konten dan akun tujuan di Jalankan; posting meminta konfirmasi PUBLISH.", label: "Ke Jalankan", route: "jalankan" };
  iconNode.textContent = next.icon;
  iconNode.classList.toggle("spin", next.icon === "progress_activity");
  text.textContent = next.text;
  action.classList.toggle("hidden", !next.label);
  if (next.label) {
    action.textContent = next.label;
    action.onclick = () => {
      if (next.go === "mode:publish") { setMode("publish"); navigate(next.route); return; }
      if (next.go) goToControl(next.route, next.go, { click: !!next.click }); else navigate(next.route);
    };
  }
}

function settingsSaved(result) {
  settingsDirty = false;
  snapshotGeneration++;
  state.settings = result.settings;
  state.settingsError = null;
  $("#settings-file-error").classList.add("hidden");
  updateRunAvailability();
}

function persistSettings(settings = readSettings()) {
  clearTimeout(settingsTimer);
  const revision = ++settingsRevision;
  snapshotGeneration++;
  state.settings = settings;
  settingsDirty = true;
  const request = settingsQueue.catch(() => {}).then(() => api("/api/settings", { settings }));
  settingsQueue = request;
  return request.then((result) => {
    if (revision === settingsRevision) settingsSaved(result);
    return result;
  });
}

function setMode(mode) {
  $("#setting-mode").value = mode;
  $$("input[name=mode]").forEach((radio) => { radio.checked = radio.value === mode; });
  renderModeHint();
  persistSettings().catch((error) => setStatus($("#settings-error"), error.message, false));
}

function runAccountLabel(run) {
  const accounts = Array.isArray(run.accounts) ? run.accounts : (run.account ? [run.account] : []);
  return accounts.length > 3 ? `${accounts.length} akun` : accounts.join(", ") || "akun tidak tercatat";
}

function renderProgress(run) {
  const bar = $("#run-progress");
  const text = $("#run-progress-text");
  if (!run.running) { bar.classList.add("hidden"); text.classList.add("hidden"); return; }
  const total = run.posting_count ?? (run.settings?.mode === "login" ? (run.accounts || []).length : 0);
  const finished = (run.results_fresh ? run.results : []).length;
  const active = (run.log || "").split("\n").filter(Boolean).reverse().find((line) => /\] \[([^\]]+)\]/.test(line));
  const account = active ? /\] \[([^\]]+)\]/.exec(active)[1] : null;
  bar.classList.remove("hidden");
  text.classList.remove("hidden");
  bar.classList.toggle("indeterminate", !total);
  $("#run-progress-bar").style.width = total ? `${Math.min(100, Math.round((finished / total) * 100))}%` : "";
  text.textContent = `${finished} dari ${total || "?"} selesai${account ? ` · sedang memproses ${account}` : ""}`;
}

function contentForResult(row, run) {
  if (row.content_id) return row.content_id;
  if (!row.job_id || !row.account) return null;
  const account = accountKey(row.account);
  const recorded = Object.entries(run.content_status || {}).find(([, accounts]) => accounts[account]?.job_id === row.job_id);
  if (recorded) return recorded[0];
  return run.results_fresh && run.contents?.length === 1 ? run.contents[0] : null;
}

function resultContentName(row, run) {
  if (!row.job_id) return "login";
  if (row.content_name) return row.content_name;
  const id = contentForResult(row, run);
  const current = state.jobs.find((job) => job.content_id === id);
  const index = (run.contents || []).indexOf(id);
  return current?.name || (index >= 0 ? run.content_names?.[index] : null) || row.job_id;
}

function retryAccount(row, mode, run) {
  const id = accountKey(row.account);
  if (!state.accounts.some((account) => accountKey(account.identifier) === id)) {
    snackbar("Akun ini sudah tidak ada; tambahkan lewat menu Akun jika masih diperlukan.");
    return;
  }
  state.accountSelection = new Set([id]);
  state.runSelection = new Set();
  const contentId = mode === "publish" ? contentForResult(row, run) : null;
  if (contentId && state.jobs.some((job) => job.content_id === contentId)) state.runSelection.add(contentId);
  $("#run-repost").checked = false;
  setMode(mode);
  renderRunAccounts();
  renderRunContent();
  navigate("jalankan");
  const message = mode === "publish" && !state.runSelection.size
    ? "Akun dipilih. Pilih konten yang sesuai, lalu periksa rencana sebelum Start."
    : "Pilihan disiapkan. Periksa rencana sebelum menekan Start.";
  setStatus($("#settings-error"), message, null);
  snackbar(message);
}


function resultViewKey(run) {
  return JSON.stringify([run.running, run.settings?.mode, run.results, run.results_fresh, run.contents, run.content_status, state.jobs.map((job) => [job.content_id, job.name])]);
}

async function pollRun() {
  try {
    state.run = await api("/api/run");
    state.runStatusError = null;
  } catch (error) {
    state.runStatusError = error.message;
    const message = `Status run tidak dapat dibaca: ${error.message} Perbaiki dulu, lalu muat ulang halaman.`;
    setStatus($("#run-summary"), message, false);
    setStatus($("#settings-error"), message, false);
    updateRunAvailability();
    return;
  }
  updateRunAvailability();
  const run = state.run;
  const phase = run.running ? "running" : run.exit_code === null ? "idle" : run.exit_code === 0 ? "done" : "failed";
  const failedCount = run.results_fresh ? run.results.filter((row) => resultTone(row) !== "ok").length : 0;
  const label = run.stopping ? "Sedang menghentikan" : run.exit_code === 130 ? "Dihentikan" : { running: "Berjalan", idle: "Siap", done: "Selesai", failed: failedCount ? `Selesai, ${failedCount} perlu perhatian` : "Selesai dengan masalah" }[phase];
  const badge = $("#run-badge");
  badge.className = `status-chip ${phase}`;
  $(".material-symbols-outlined", badge).textContent = { running: "progress_activity", idle: "radio_button_unchecked", done: "check_circle", failed: "error" }[phase];
  $(".material-symbols-outlined", badge).classList.toggle("spin", phase === "running");
  $(".status-chip-label", badge).textContent = run.running && run.settings ? `${label} · ${MODE_LABEL[run.settings.mode]}` : label;
  renderRailRun(run, phase, failedCount);
  $("#stop-run").disabled = !run.running || !!run.stopping;
  updateRunAvailability();
  renderProgress(run);

  // Satu baris meta di judul panel; detail per akun ada di tabel, exit code tidak ditampilkan dua kali.
  const summary = $("#run-summary");
  if (run.settings) {
    const outcome = run.stopping ? "sedang menghentikan; tunggu penutupan browser" : run.running ? "sedang berjalan" : run.exit_code === 0 ? "semua berhasil" : run.exit_code === 130 ? "dihentikan" : run.exit_code === 2 ? "input tidak valid" : "ada yang perlu perhatian";
    const contents = run.settings.mode === "publish" ? ` · ${(run.contents || []).length} konten${run.posting_count != null ? ` · ${run.posting_count} postingan` : ""}` : "";
    setStatus(summary, `${MODE_LABEL[run.settings.mode]} · ${runAccountLabel(run)}${contents} · ${run.settings.workers} paralel${run.settings.headless ? " · headless" : ""} · mulai ${formatTime(run.started_at)} · ${outcome}`, null);
  } else {
    setStatus(summary, run.results.length ? "Hasil proses sebelumnya." : "Belum ada proses di sesi ini.", null);
  }
  if ($("#settings-error").textContent.startsWith("Status run tidak dapat dibaca:")) setStatus($("#settings-error"), "", null);

  const body = $("#results-table tbody");
  const resultsKey = resultViewKey(run);
  if (body.dataset.renderKey !== resultsKey) {
  body.dataset.renderKey = resultsKey;
  body.replaceChildren();
  if (!run.results.length) emptyRow(body, 6, "Belum ada hasil.");
  for (const row of run.results) {
    const info = STATUS_TEXT[row.status] || { label: row.status || "?", advice: "", retry: false };
    const tone = resultTone(row);
    const cell = el("div", { class: "result-cell" });
    if (row.permalink) cell.append(el("a", { href: row.permalink, target: "_blank", rel: "noopener" }, el("span", { textContent: "Buka postingan" }), icon("open_in_new")));
    if (info.advice) cell.append(el("span", { class: "advice", textContent: info.advice }));
    if (row.detail && tone !== "ok") cell.append(el("span", { class: "raw", textContent: `${row.status}: ${row.detail}` }));
    if (row.reused !== undefined && tone === "ok") cell.append(el("span", { class: "advice", textContent: row.reused ? "Session lama dipakai." : "Login baru; session tersimpan." }));
    if (row.cleanup && row.cleanup !== "closed" && statusTone(row.status) === "ok") {
      cell.append(el("span", { class: "advice", textContent: row.status === "published" ? "Posting terkonfirmasi, tetapi penutupan browser atau penyimpanan hasil bermasalah. Periksa log; jangan posting ulang otomatis." : "Login berhasil, tetapi penutupan browser atau penyimpanan hasil bermasalah. Periksa log." }));
    }
    const actions = el("td", {});
    if (info.retry && !run.running && row.account) {
      const mode = row.status === "verification_required" || row.status === "unexpected_page" || row.status === "invalid_credentials" ? "login" : ((run.settings?.mode || row.mode) === "publish" ? "publish" : "login");
      actions.append(el("button", { type: "button", class: "btn text", title: `Siapkan pilihan ${mode === "login" ? "login" : "konten"} untuk ${row.account}; periksa sebelum Start`, onclick: () => retryAccount(row, mode, run) }, icon("restart_alt"), el("span", { textContent: mode === "login" ? "Siapkan login" : "Siapkan ulang" })));
    }
    body.append(el("tr", {},
      el("td", { class: "nowrap", textContent: row.account ?? "" }),
      el("td", { textContent: resultContentName(row, run), title: row.job_id || "" }),
      el("td", {}, chip(info.label, tone, STATUS_ICON[tone])),
      el("td", {}, cell),
      el("td", { class: "nowrap", textContent: formatTime(row.timestamp) }),
      actions));
  }
  }
  renderLog($("#run-log"), run.log);

  const ledger = $("#ledger-table tbody");
  const ledgerKey = JSON.stringify(run.ledger || {});
  if (ledger.dataset.renderKey !== ledgerKey) {
  ledger.dataset.renderKey = ledgerKey;
  ledger.replaceChildren();
  const entries = Object.entries(run.ledger || {});
  if (!entries.length) emptyRow(ledger, 6, "Belum ada catatan posting.");
  for (const [jobId, record] of entries) {
    ledger.append(el("tr", {},
      el("td", {}, el("strong", { textContent: jobId })),
      el("td", { textContent: record.account }),
      el("td", {}, statusChip(record.status)),
      el("td", {}, record.permalink ? el("a", { href: record.permalink, target: "_blank", rel: "noopener" }, el("span", { textContent: "buka" }), icon("open_in_new")) : el("span", { textContent: "—" })),
      el("td", { textContent: record.detail }),
      el("td", { textContent: formatTime(record.updated_at) })));
  }
  }
  renderStats();
  if (state.lastRunning && !run.running) {
    const total = run.results_fresh ? run.results.length : 0;
    snackbar(run.exit_code === 130 ? "Run dihentikan." : phase === "done" ? `Run selesai: ${total} berhasil.` : failedCount ? `Run selesai: ${total - failedCount} berhasil, ${failedCount} perlu perhatian.` : "Run selesai dengan masalah; lihat log.");
    $("#run-repost").checked = false;
    await refresh();
  }
  renderRunContentStatus();
  renderGuide();
  state.lastRunning = run.running;
}

/* ---------- router: satu view per menu rail (#/akun, #/media, …) ---------- */

const ROUTES = ["beranda", "akun", "media", "konten", "jalankan", "riwayat", "sistem"];

function currentRoute() {
  const name = location.hash.replace(/^#\/?/, "").split(/[?#]/)[0];
  return ROUTES.includes(name) ? name : "beranda";
}

/* Pindah halaman = ganti hash lalu render langsung; hashchange (Back/Forward, tautan) merender ulang. */
function navigate(route) {
  if (currentRoute() !== route || !location.hash) location.hash = `#/${route}`;
  renderRoute();
}

function renderRoute() {
  const route = currentRoute();
  if (renderRoute.current === route) return;
  renderRoute.current = route;
  $$(".view").forEach((view) => view.classList.toggle("active", view.dataset.route === route));
  $$(".rail-item").forEach((item) => {
    const active = item.dataset.route === route;
    item.classList.toggle("active", active);
    if (active) item.setAttribute("aria-current", "page"); else item.removeAttribute("aria-current");
  });
  const title = $(`.view[data-route="${route}"]`).dataset.title;
  $("#page-title").textContent = title;
  document.title = route === "beranda" ? "Instagram Program" : `${title} · Instagram Program`;
  window.scrollTo({ top: 0, behavior: "instant" });
}

function installRouter() {
  const bar = $(".top-app-bar");
  window.addEventListener("scroll", () => bar.classList.toggle("scrolled", window.scrollY > 4), { passive: true });
  window.addEventListener("hashchange", renderRoute);
  if (!location.hash) history.replaceState(null, "", "#/beranda");
  renderRoute();
}

/* Fokus ke satu kontrol di halaman lain: paksa layout view yang baru tampil, lalu sorot kontrolnya. */
function goToControl(route, selector, { click = false } = {}) {
  navigate(route);
  const target = $(selector);
  if (!target) return;
  void target.offsetHeight; // view baru saja berubah display:none→grid; tanpa layout flush focus() diabaikan
  target.scrollIntoView({ block: "center" });
  if (click) target.click(); else target.focus();
}

function renderRailRun(run, phase, failedCount) {
  const fab = $("#rail-run");
  const running = !!run.running;
  const mode = $("#setting-mode").value;
  fab.classList.toggle("stop", running);
  $(".material-symbols-outlined", fab).textContent = running ? "stop" : mode === "publish" ? "rocket_launch" : "play_arrow";
  fab.title = run.stopping ? "Sedang menghentikan; tunggu penutupan browser" : running ? "Hentikan run (interrupt; engine menutup browser sendiri)" : `Start ${MODE_LABEL[mode].toLowerCase()} — sama seperti tombol Start`;
  fab.setAttribute("aria-label", run.stopping ? "Sedang menghentikan" : running ? "Hentikan run" : "Mulai run");
  const badge = $("#rail-badge-results");
  const show = run.results_fresh && phase !== "running" && run.results.length > 0;
  badge.classList.toggle("hidden", !show);
  if (show) {
    badge.className = `rail-badge ${failedCount ? "" : "ok"}`;
    badge.textContent = failedCount ? (failedCount > 99 ? "99+" : String(failedCount)) : "✓";
    badge.title = failedCount ? `${failedCount} perlu perhatian` : "Semua berhasil";
  }
}

/* ---------- init ---------- */

async function refresh() {
  const generation = ++snapshotGeneration;
  const revision = state.editRevision;
  const settingsVersion = settingsRevision;
  const data = await api("/api/state");
  if (generation !== snapshotGeneration || revision !== state.editRevision || settingsVersion !== settingsRevision) return;
  applySnapshot(data);
}

async function init() {
  const boot = await (await fetch("/api/bootstrap")).json();
  state.token = boot.token;
  applySnapshot(boot);
  installRouter();
  $$(".step[data-route]").forEach((button) => button.addEventListener("click", () => navigate(button.dataset.route)));

  const passwordInput = $("#account-password");
  const passwordToggle = $("#account-password-toggle");
  const setPasswordVisibility = (visible) => {
    passwordInput.type = visible ? "text" : "password";
    const label = visible ? "Sembunyikan password" : "Tampilkan password";
    passwordToggle.setAttribute("aria-label", label);
    passwordToggle.title = label;
    $(".material-symbols-outlined", passwordToggle).textContent = visible ? "visibility_off" : "visibility";
  };
  passwordToggle.addEventListener("click", () => setPasswordVisibility(passwordInput.type === "password"));

  const refreshAccountsAfterSave = async (message) => {
    setStatus($("#accounts-error"), "", null);
    snackbar(message);
    try { await refresh(); }
    catch (error) {
      setStatus($("#accounts-error"), `${message} Daftar belum dapat diperbarui; muat ulang halaman. ${error.message}`, false);
    }
  };

  $("#account-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = $('#account-form button[type="submit"]');
    if (button.disabled) return;
    const identifierInput = $("#account-identifier");
    const identifier = identifierInput.value;
    const password = passwordInput.value;
    button.disabled = true;
    try {
      await api("/api/accounts", { identifier, password });
      if (identifierInput.value === identifier && passwordInput.value === password) {
        identifierInput.value = "";
        passwordInput.value = "";
        setPasswordVisibility(false);
      }
      await refreshAccountsAfterSave(`Akun ${identifier.trim()} ditambahkan.`);
    } catch (error) { setStatus($("#accounts-error"), error.message, false); }
    finally { button.disabled = false; }
  });

  $("#pick-media").addEventListener("click", async () => {
    try { const result = await api("/api/media/pick", {}); await refresh(); setStatus($("#media-status"), `${result.imported.length} file diimpor.`, true); }
    catch (error) { setStatus($("#media-status"), error.message, false); }
  });
  $("#bulk-accounts-submit").addEventListener("click", async () => {
    const button = $("#bulk-accounts-submit");
    if (button.disabled) return;
    const input = $("#bulk-accounts-text");
    const text = input.value;
    button.disabled = true;
    try {
      const result = await api("/api/accounts/bulk", { text });
      if (input.value === text) input.value = "";
      await refreshAccountsAfterSave(`${result.added.length} akun ditambahkan${result.skipped.length ? `, ${result.skipped.length} dilewati (sudah ada)` : ""}.`);
    } catch (error) { setStatus($("#accounts-error"), error.message, false); }
    finally { button.disabled = false; }
  });
  $("#import-media").addEventListener("click", async () => {
    try { const result = await api("/api/media/import", { path: $("#media-path").value }); $("#media-path").value = ""; await refresh(); setStatus($("#media-status"), `Diimpor: ${result.imported.map((item) => item.name).join(", ")}`, true); }
    catch (error) { setStatus($("#media-status"), error.message, false); }
  });

  $("#new-job").addEventListener("click", () => {
    // Konten baru memakai media pertama yang belum dipakai, tanpa memilih akun tujuan.
    const used = new Set(state.jobs.flatMap((job) => job.media.map((item) => item.path)));
    const firstMedia = state.media.find((item) => item.kind === "image" && !used.has(item.path)) || state.media.find((item) => item.kind === "image") || state.media[0] || null;
    const job = normalizeJob({ content_id: crypto.randomUUID().replaceAll("-", ""), name: nextContentName(state.jobs.at(-1)?.name || "konten-000"),
      kind: firstMedia?.kind === "video" ? "reel" : "photo", media: [{ path: firstMedia?.path || "" }] });
    job._open = true;
    $("#job-filter").value = "";
    state.jobs.push(job);
    markDirty();
    renderJobs();
    const node = $$("#job-list .job").at(-1);
    node.scrollIntoView({ block: "center" });
    $(".f-caption", node).focus();
  });
  $("#job-filter").addEventListener("input", applyFilter);
  $("#fold-toggle").addEventListener("click", () => {
    const open = $$("#job-list .job:not(.collapsed)").length === 0;
    state.jobs.forEach((job) => { job._open = open; });
    $$("#job-list .job").forEach((node) => node.classList.toggle("collapsed", !open));
    renderFoldToggle();
  });
  $("#save-jobs").addEventListener("click", async () => {
    try { await saveJobs(); await refresh(); snackbar("Konten tersimpan."); }
    catch (error) { setStatus($("#jobs-status"), error.message, false); }
  });

  $("#setting-mode").addEventListener("change", renderModeHint);
  $$("input[name=mode]").forEach((radio) => radio.addEventListener("change", () => setMode(radio.value)));
  // Pengaturan lanjutan tersimpan otomatis saat diubah (Start juga menyimpan); tidak ada tombol simpan terpisah.
  ["#setting-workers", "#setting-timeout", "#setting-headless"].forEach((selector) => $(selector).addEventListener("input", () => {
    settingsRevision++;
    snapshotGeneration++;
    state.settings = readSettings();
    settingsDirty = true;
    renderModeHint();
    clearTimeout(settingsTimer);
    settingsTimer = setTimeout(() => persistSettings().then(() => setStatus($("#settings-error"), "", null)).catch((error) => setStatus($("#settings-error"), error.message, false)), 400);
  }));
  $("#run-content-all").addEventListener("click", () => {
    state.runSelection = new Set(state.jobs.map((job) => job.content_id));
    runChoicesChanged();
  });
  $("#run-content-none").addEventListener("click", () => { state.runSelection.clear(); runChoicesChanged(); });
  $("#run-accounts-all").addEventListener("click", () => {
    state.accountSelection = new Set(state.accounts.map((account) => accountKey(account.identifier)));
    runChoicesChanged();
  });
  $("#run-accounts-none").addEventListener("click", () => { state.accountSelection.clear(); runChoicesChanged(); });
  $("#run-repost").addEventListener("change", renderRunPlan);
  $("#start-run").addEventListener("click", async () => {
    if (state.starting || state.run?.running) return;
    state.starting = true;
    updateRunAvailability();
    try {
      if (state.manifestError) throw new Error(manifestErrorMessage());
      if (state.runStatusError) throw new Error(`Status run tidak dapat dibaca: ${state.runStatusError} Perbaiki dulu, lalu muat ulang halaman.`);
      if (state.settingsError) throw new Error("Perbaiki pengaturan tersimpan dahulu melalui Pengaturan lanjutan.");
      const settings = readSettings();
      pruneRunSelections();
      if (settings.mode === "publish" && !selectedRunJobs().length) throw new Error("Pilih minimal satu konten yang akan diposting.");
      if (!selectedRunAccounts().length) throw new Error("Pilih minimal satu akun tujuan.");
      // Start menyimpan konten terbaru, tanpa mengubah pilihan akun atau konten.
      if (settings.mode === "publish" && state.dirty) {
        try {
          await saveJobs("Tersimpan otomatis sebelum posting");
          renderJobs();
          renderGuide();
        } catch (error) {
          setStatus($("#jobs-status"), error.message, false);
          navigate("konten");
          throw error;
        }
      }
      const preparedRevision = state.editRevision;
      await persistSettings(settings);
      if (preparedRevision !== state.editRevision || JSON.stringify(settings) !== JSON.stringify(readSettings())) {
        throw new Error("Konten atau pengaturan berubah. Periksa pilihan dan tekan Start kembali.");
      }
      const plan = runPlan();
      if (!plan.accounts.length || (settings.mode === "publish" && !plan.contents.length)) throw new Error("Pilihan berubah. Pilih konten dan akun tujuan, lalu tekan Start kembali.");
      const accounts = plan.accounts.map((account) => account.identifier);
      const body = { settings, accounts, repost: false };
      if (settings.mode === "publish") {
        if (plan.blocked) throw new Error(`${plan.blocked} hasil belum pasti. Periksa profil Instagram dan catatan posting sebelum melanjutkan; posting ulang juga diblokir.`);
        if (!plan.ready) throw new Error("Semua pasangan konten dan akun sudah berhasil. Aktifkan “Posting ulang yang sudah berhasil” jika memang ingin mengulang.");
        body.contents = plan.contents.map((job) => job.content_id);
        body.expected_contents = body.contents.map((id) => savedContents.find((item) => item.content_id === id));
        if (body.expected_contents.some((item) => !item)) throw new Error("Konten belum tersimpan; simpan dahulu sebelum Start.");
        body.repost = $("#run-repost").checked;
        const names = plan.contents.map((job) => job.name).join(", ");
        const revision = state.editRevision;
        const typed = await dialog({ title: "Posting sungguhan?", text: `Konten: ${names}. Akun tujuan: ${accounts.join(", ")}. ${plan.contents.length} konten × ${accounts.length} akun = ${plan.total} pasangan; ${plan.ready} postingan akan dikirim (${plan.fresh} baru, ${plan.repost} posting ulang), ${plan.skipped} yang sudah berhasil dilewati. Ketik PUBLISH untuk melanjutkan.`, confirmLabel: "Posting", iconName: "rocket_launch", danger: true, input: { label: "Ketik PUBLISH" } });
        if (typed !== "PUBLISH") return;
        const current = runPlan();
        const changed = state.editRevision !== revision || settings.mode !== readSettings().mode
          || body.contents.length !== state.runSelection.size || accounts.length !== state.accountSelection.size
          || body.contents.some((id) => !state.runSelection.has(id)) || accounts.some((id) => !state.accountSelection.has(accountKey(id)))
          || body.repost !== $("#run-repost").checked || current.fresh !== plan.fresh || current.published !== plan.published || current.blocked !== plan.blocked;
        if (changed) {
          throw new Error("Konten, pilihan, atau status posting berubah. Periksa rencana dan tekan Start kembali.");
        }
        body.confirm = "PUBLISH";
      }
      if (state.manifestError) throw new Error(manifestErrorMessage());
      if (state.runStatusError) throw new Error(`Status run tidak dapat dibaca: ${state.runStatusError}`);
      await api("/api/run/start", body);
      $("#run-repost").checked = false;
      setStatus($("#settings-error"), "", null);
      state.lastRunning = true;
      await pollRun();
      snackbar(`${MODE_LABEL[settings.mode]} dimulai untuk ${accounts.length} akun terpilih.`);
    } catch (error) {
      setStatus($("#settings-error"), error.message, false);
    } finally {
      state.starting = false;
      renderRunContentStatus();
    }
  });
  $("#stop-run").addEventListener("click", async () => {
    try {
      await api("/api/run/stop", {});
      await pollRun();
      snackbar(state.run?.running ? "Permintaan Stop diterima; tunggu proses dan penutupan browser selesai." : "Run sudah berhenti.");
    }
    catch (error) { setStatus($("#settings-error"), error.message, false); }
  });
  // FAB rail = Start/Stop yang sama; chip status di header membuka panel hasil.
  $("#rail-run").addEventListener("click", () => {
    if (state.run?.running) { $("#stop-run").click(); return; }
    navigate("jalankan");
    $("#start-run").click();
  });
  $("#run-badge").addEventListener("click", () => navigate("jalankan"));

  await pollRun();
  setInterval(pollRun, 2000);
}

init().catch((error) => { document.body.insertAdjacentText("afterbegin", `UI gagal dimuat: ${error.message}`); });
