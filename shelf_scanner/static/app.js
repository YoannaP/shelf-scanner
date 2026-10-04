const STATUSES = [
  { key: "read", label: "Read", short: "Read", hotkey: "1" },
  { key: "to-read", label: "Want to Read", short: "Want", hotkey: "2" },
  { key: "not-finished", label: "Did not finish", short: "DNF", hotkey: "3" },
  { key: "skipped", label: "Skip", short: "Skip", hotkey: "4" },
];
const LABEL = Object.fromEntries(STATUSES.map((s) => [s.key, s.label]));

const app = document.getElementById("app");
const state = { config: {}, scan: null, filter: "all", history: [], poll: null, view: null, bookId: null };

// ---------- helpers ----------

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (body instanceof FormData) opts.body = body;
  else if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `${res.status} ${res.statusText}`);
  }
  return res.json();
}

let toastTimer;
function toast(msg) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 2600);
}

const fmtDate = (iso) =>
  new Date(iso).toLocaleString(undefined, { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

const isActive = (b) => b.status !== "removed" && (!b.vault_match || b.classify_anyway);
const activeBooks = () => state.scan.books.filter(isActive);
const queue = () => activeBooks().filter((b) => !b.status);
const bookNumber = (b) => state.scan.books.indexOf(b) + 1;
const cropUrl = (b) => (b.crop ? `/files/${state.scan.id}/crops/${b.crop}` : null);
const photoUrl = (scanId, photoId) => `/files/${scanId}/photos/${photoId}.jpg`;
const scanHref = (view, bookId) => `#/scan/${state.scan.id}/${view}${bookId ? "/" + bookId : ""}`;
const obsidianUrl = (name) =>
  `obsidian://open?vault=${encodeURIComponent(state.config.vault)}&file=${encodeURIComponent("References/" + name)}`;

function badges(b) {
  const out = [];
  if (!b.title) out.push(`<span class="badge warn">unreadable spine</span>`);
  else if (!b.verified) out.push(`<span class="badge warn">⚠️ not verified</span>`);
  if (b.title && b.confidence === "low") out.push(`<span class="badge warn">low confidence</span>`);
  if (b.seen_in.length > 1) out.push(`<span class="badge">seen in ${b.seen_in.length} photos</span>`);
  if (b.manual) out.push(`<span class="badge">added by you</span>`);
  if (b.classify_anyway) out.push(`<span class="badge">not “${esc(b.vault_match)}”</span>`);
  return out.join("");
}

// ---------- routing ----------

async function route() {
  clearTimeout(state.poll);
  const [, page, id, view, bookId] = location.hash.split("/");
  try {
    if (page === "scan" && id) {
      if (state.scan?.id !== id) {
        state.scan = await api("GET", `/api/scans/${id}`);
        state.history = [];
        state.filter = "all";
      }
      state.view = view === "run" ? "run" : "list";
      state.bookId = bookId || null;
      renderScan();
    } else {
      state.scan = null;
      await renderHome();
    }
  } catch (e) {
    app.innerHTML = `<div class="center"><p class="error">${esc(e.message)}</p><a href="#/">Back to home</a></div>`;
  }
}

function setScan(scan) {
  state.scan = scan;
  renderScan();
}

// ---------- home ----------

async function renderHome() {
  const scans = await api("GET", "/api/scans");
  app.innerHTML = `
    <header class="topbar"><div class="inner"><h1 class="grow">📚 Shelf Scanner</h1>
      <span class="muted small">${state.config.vault ? `Vault: ${esc(state.config.vault)}` : "CSV only (no vault)"}</span>
    </div></header>
    <main class="wrap">
      <section class="card new-scan">
        <h2>New scan</h2>
        <label class="drop" id="drop">
          <input type="file" id="files" multiple accept="image/*,.heic,.heif">
          <strong>Choose shelf photos</strong>
          <span class="small">or drop them here. Several photos are fine for a wide shelf, and overlapping shots are merged.</span>
        </label>
        <div class="picked" id="picked"></div>
        <div class="row-end">
          <label class="toggle"><input type="checkbox" id="owned" checked> These are my books</label>
          <button class="primary" id="go" disabled>Scan shelf</button>
        </div>
      </section>
      <section>
        <h2>Past scans</h2>
        ${scans.length ? `<div class="scan-list">${scans.map(scanRow).join("")}</div>` : `<p class="muted">No scans yet.</p>`}
      </section>
    </main>`;

  const input = document.getElementById("files");
  const drop = document.getElementById("drop");
  const go = document.getElementById("go");
  let files = [];
  const pick = (list) => {
    files = [...list];
    document.getElementById("picked").innerHTML = files.map((f) => `<span>${esc(f.name)}</span>`).join("");
    go.disabled = !files.length;
    go.textContent = files.length > 1 ? `Scan ${files.length} photos` : "Scan shelf";
  };
  input.addEventListener("change", () => pick(input.files));
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); pick(e.dataTransfer.files); });
  go.addEventListener("click", async () => {
    const form = new FormData();
    files.forEach((f) => form.append("files", f));
    form.append("owned", document.getElementById("owned").checked);
    go.disabled = true;
    go.textContent = "Uploading…";
    try {
      const scan = await api("POST", "/api/scans", form);
      location.hash = `#/scan/${scan.id}/list`;
    } catch (e) {
      toast(e.message);
      go.disabled = false;
      go.textContent = "Scan shelf";
    }
  });
}

function scanRow(s) {
  const pct = s.total ? Math.round((100 * s.classified) / s.total) : 0;
  const status =
    s.state === "detecting" ? "Reading spines…" : s.state === "error" ? "Failed" : `${s.classified}/${s.total} classified`;
  return `
    <a class="scan-row" href="#/scan/${s.id}/list">
      ${s.thumb ? `<img src="${photoUrl(s.id, s.thumb)}" alt="">` : ""}
      <div class="grow"><div><b>${esc(fmtDate(s.created))}</b></div>
        <div class="muted small">${s.photos} photo${s.photos === 1 ? "" : "s"} · ${s.owned ? "my books" : "not mine"} · ${status}</div></div>
      ${s.state === "ready" ? `<div class="meter"><div style="width:${pct}%"></div></div>` : ""}
    </a>`;
}

// ---------- scan shell ----------

function renderScan() {
  const s = state.scan;
  const active = activeBooks();
  const done = active.filter((b) => b.status).length;
  const header = `
    <header class="topbar"><div class="inner">
      <a class="back" href="#/" title="All scans">←</a>
      <div class="grow"><h1>${esc(fmtDate(s.created))}</h1>
        <div class="muted small">${s.state === "ready" ? `${active.length} books · ${done} classified · ` : ""}${s.owned ? "my books" : "not my books"}</div></div>
      ${s.state === "ready" ? `<nav class="tabs">
        <a href="${scanHref("list")}" class="${state.view === "list" ? "active" : ""}">List</a>
        <a href="${scanHref("run")}" class="${state.view === "run" ? "active" : ""}">Run-through</a></nav>` : ""}
    </div></header>`;

  if (s.state === "detecting") {
    app.innerHTML = `${header}<main class="wrap"><div class="center"><div class="spinner"></div>
      <p>${esc(s.progress || "Working…")}</p><p class="muted small">Usually 10–20 seconds per photo.</p></div></main>`;
    state.poll = setTimeout(async () => {
      if (state.scan?.id !== s.id) return;
      setScan(await api("GET", `/api/scans/${s.id}`));
    }, 2000);
    return;
  }
  if (s.state === "error") {
    app.innerHTML = `${header}<main class="wrap"><div class="center"><p class="error">Detection failed</p>
      <p class="muted">${esc(s.error)}</p><a href="#/">Back to home</a></div></main>`;
    return;
  }
  app.innerHTML = `${header}<main class="wrap">${state.view === "run" ? runHtml() : listHtml()}</main>`;
  (state.view === "run" ? bindRun : bindList)();
}

// ---------- list view ----------

function listHtml() {
  const s = state.scan;
  const active = activeBooks();
  const counts = { all: active.length, unclassified: active.filter((b) => !b.status).length };
  STATUSES.forEach((st) => (counts[st.key] = active.filter((b) => b.status === st.key).length));
  const filters = [["all", "All"], ["unclassified", "Unclassified"], ...STATUSES.map((st) => [st.key, st.short])];
  const shown = active.filter((b) =>
    state.filter === "all" ? true : state.filter === "unclassified" ? !b.status : b.status === state.filter);
  const inVault = s.books.filter((b) => b.vault_match && !b.classify_anyway && b.status !== "removed");
  const removed = s.books.filter((b) => b.status === "removed");

  const photos = s.photos.map((p) => {
    const boxes = s.books
      .filter((b) => b.photo_id === p.id && b.box && b.status !== "removed")
      .map((b) => {
        const [x0, y0, x1, y1] = b.box;
        const cls = isActive(b) ? (b.status ? "done" : b.title ? b.confidence : "low") : "done";
        return `<div class="box ${cls}" data-jump="${b.id}" title="${esc(b.title || "Unreadable spine")}"
          style="left:${x0 / 10}%;top:${y0 / 10}%;width:${(x1 - x0) / 10}%;height:${(y1 - y0) / 10}%"><span>${bookNumber(b)}</span></div>`;
      }).join("");
    return `<div class="photo"><img src="${photoUrl(s.id, p.id)}" alt="${esc(p.filename)}">${boxes}</div>`;
  }).join("");

  return `
    <section class="photos">${photos}</section>
    <div class="chips">${filters.map(([k, label]) =>
      `<button data-filter="${k}" class="${state.filter === k ? "on" : ""}">${label} <span class="muted">${counts[k]}</span></button>`).join("")}</div>
    <div class="books">${shown.map(bookRow).join("") || `<p class="muted">Nothing here.</p>`}</div>
    <form class="add-form" id="add-form">
      <input name="title" placeholder="Missed a book? Title" required autocomplete="off">
      <input name="author" placeholder="Author" autocomplete="off">
      <button class="primary">Add book</button>
    </form>
    ${inVault.length ? `<details class="card"><summary>Already in vault (${inVault.length})</summary>
      <p class="muted small">These matched an existing note and are left alone. If a match is wrong, classify the book anyway.</p>
      ${inVault.map((b) => `<div class="mini-row"><span class="num">${bookNumber(b)}</span>
        <div class="grow"><b>${esc(b.title)}</b> <span class="muted">${esc(b.author)}</span><br>
        <span class="small muted">→ <a href="${obsidianUrl(b.vault_match)}">${esc(b.vault_match)}</a></span></div>
        <button data-anyway="${b.id}">Actually, classify this</button></div>`).join("")}</details>` : ""}
    ${removed.length ? `<details class="card"><summary>Removed (${removed.length})</summary>
      ${removed.map((b) => `<div class="mini-row"><div class="grow">${esc(b.title || "Unreadable spine")}
        <span class="muted">${esc(b.author)}</span></div><button data-restore="${b.id}">Restore</button></div>`).join("")}</details>` : ""}`;
}

function bookRow(b) {
  const crop = cropUrl(b);
  return `
    <div class="book-row" id="row-${b.id}">
      <span class="num">${bookNumber(b)}</span>
      ${crop ? `<img class="thumb" src="${crop}" alt="" loading="lazy">` : `<div class="thumb empty">—</div>`}
      <div class="cover-cell">${b.cover_url ? `<img class="thumb" src="${esc(b.cover_url)}" alt="" loading="lazy" onerror="this.style.visibility='hidden'">` : `<div class="thumb empty">no cover</div>`}</div>
      <div class="info">
        <a class="title" href="${scanHref("run", b.id)}">${esc(b.title || "Unreadable spine")}</a>
        <div class="author">${esc(b.author)}</div>
        <div class="badges">${badges(b)}</div>
      </div>
      <div class="seg">${STATUSES.map((st) =>
        `<button class="${st.key} ${b.status === st.key ? "on" : ""}" data-classify="${b.id}" data-status="${st.key}" ${b.title ? "" : "disabled"}>${st.short}</button>`).join("")}</div>
      <div class="actions">
        <button class="icon" data-edit="${b.id}" title="Edit">✏️</button>
        <button class="icon" data-remove="${b.id}" title="Not a book">🗑</button>
      </div>
    </div>`;
}

function bindList() {
  app.querySelectorAll("[data-filter]").forEach((el) =>
    el.addEventListener("click", () => { state.filter = el.dataset.filter; renderScan(); }));
  app.querySelectorAll("[data-classify]").forEach((el) =>
    el.addEventListener("click", () => classify(el.dataset.classify, el.dataset.status)));
  app.querySelectorAll("[data-edit]").forEach((el) => el.addEventListener("click", () => openEdit(el.dataset.edit)));
  app.querySelectorAll("[data-remove]").forEach((el) => el.addEventListener("click", () => classify(el.dataset.remove, "removed")));
  app.querySelectorAll("[data-restore]").forEach((el) => el.addEventListener("click", () => classify(el.dataset.restore, null)));
  app.querySelectorAll("[data-anyway]").forEach((el) =>
    el.addEventListener("click", () => mutate(() =>
      api("PATCH", `/api/scans/${state.scan.id}/books/${el.dataset.anyway}`, { classify_anyway: true }), "Moved to the list")));
  app.querySelectorAll("[data-jump]").forEach((el) =>
    el.addEventListener("click", () => {
      const book = state.scan.books.find((b) => b.id === el.dataset.jump);
      if (!isActive(book)) return toast(book.vault_match ? `Already in vault as “${book.vault_match}”` : "Classified");
      state.filter = "all";
      renderScan();
      const row = document.getElementById(`row-${book.id}`);
      row?.scrollIntoView({ behavior: "smooth", block: "center" });
      row?.classList.add("flash");
      setTimeout(() => row?.classList.remove("flash"), 1500);
    }));
  document.getElementById("add-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    mutate(() => api("POST", `/api/scans/${state.scan.id}/books`, { title: f.get("title"), author: f.get("author") }), "Book added");
  });
}

// ---------- run-through ----------

function currentBook() {
  if (state.bookId) return state.scan.books.find((b) => b.id === state.bookId) || null;
  return queue()[0] || null;
}

function runHtml() {
  const active = activeBooks();
  const done = active.filter((b) => b.status).length;
  const pct = active.length ? Math.round((100 * done) / active.length) : 100;
  const b = currentBook();
  const progress = `<div class="progress"><div class="meter"><div style="width:${pct}%"></div></div>
    <span class="muted small">${done} of ${active.length} classified</span></div>`;
  if (!b) {
    return `<div class="run">${progress}<div class="center"><h2>🎉 All classified</h2>
      <p class="muted">${state.config.vault ? "Notes are in your vault and" : "Every choice is"} logged in the CSV.</p>
      <div style="display:flex;gap:8px"><button id="back" ${state.history.length ? "" : "disabled"}>← Back</button>
      <a href="${scanHref("list")}"><button class="primary">See the list</button></a></div></div></div>`;
  }
  const crop = cropUrl(b);
  return `
    <div class="run">${progress}
      <div class="card">
        <div class="book-card">
          <div class="book-images">
            ${crop ? `<img class="spine-lg" src="${crop}" alt="Spine from your photo">` : ""}
            ${b.cover_url ? `<img class="cover-lg" src="${esc(b.cover_url)}" alt="Cover" onerror="this.remove()">` : ""}
          </div>
          <div class="book-meta">
            <span class="num">#${bookNumber(b)}</span>
            <h2>${esc(b.title || "Unreadable spine")}</h2>
            <div class="author">${esc(b.author)}</div>
            <div class="badges">${badges(b)}</div>
            ${b.status ? `<div class="current">Currently: <b>${LABEL[b.status] || b.status}</b></div>` : ""}
            ${!b.title ? `<p class="unknown-hint">Couldn't read this spine. Edit it to name the book, or mark it Not a book.</p>` : ""}
          </div>
        </div>
        <div class="choices">${STATUSES.map((st) =>
          `<button class="choice ${st.key} ${b.status === st.key ? "on" : ""}" data-status="${st.key}" ${b.title && (isActive(b) || b.status === "removed") ? "" : "disabled"}>
            ${st.label}<kbd>${st.hotkey}</kbd></button>`).join("")}</div>
        <div class="secondary">
          <button id="back" ${state.history.length ? "" : "disabled"}>← Back <kbd>←</kbd></button>
          <span style="display:flex;gap:8px">
            <button id="edit">✏️ Edit <kbd>E</kbd></button>
            <button id="remove" ${b.status === "removed" ? "disabled" : ""}>🗑 Not a book <kbd>Del</kbd></button>
          </span>
        </div>
      </div>
    </div>`;
}

function bindRun() {
  const b = currentBook();
  document.getElementById("back")?.addEventListener("click", goBack);
  if (!b) return;
  app.querySelectorAll(".choice").forEach((el) => el.addEventListener("click", () => runClassify(b, el.dataset.status)));
  document.getElementById("edit").addEventListener("click", () => openEdit(b.id));
  document.getElementById("remove").addEventListener("click", () => runClassify(b, "removed"));
}

async function runClassify(book, status) {
  if (status !== "removed" && (!book.title || !(isActive(book) || book.status === "removed"))) return;
  const ok = await classify(book.id, status, { quiet: true });
  if (!ok) return;
  state.history.push(book.id);
  location.hash = scanHref("run"); // next unclassified book
  if (!state.bookId) renderScan(); // hash unchanged: re-render ourselves
}

function goBack() {
  const prev = state.history.pop();
  if (prev) location.hash = scanHref("run", prev);
}

document.addEventListener("keydown", (e) => {
  if (state.view !== "run" || !state.scan || state.scan.state !== "ready") return;
  if (document.getElementById("edit-dialog").open || e.target.closest("input, textarea") || e.metaKey || e.ctrlKey || e.altKey) return;
  const b = currentBook();
  const st = STATUSES.find((s) => s.hotkey === e.key);
  if (st && b) runClassify(b, st.key);
  else if (e.key === "ArrowLeft") goBack();
  else if ((e.key === "e" || e.key === "E") && b) openEdit(b.id);
  else if ((e.key === "Delete" || e.key === "Backspace") && b) runClassify(b, "removed");
  else return;
  e.preventDefault();
});

// ---------- mutations ----------

async function mutate(fn, msg) {
  try {
    setScan(await fn());
    if (msg) toast(msg);
    return true;
  } catch (e) {
    toast(e.message);
    return false;
  }
}

function classify(bookId, status, { quiet } = {}) {
  const msg = quiet ? null : status === "removed" ? "Removed" : status === null ? "Restored" : `Marked ${LABEL[status]}`;
  return mutate(() => api("POST", `/api/scans/${state.scan.id}/books/${bookId}/classify`, { status }), msg);
}

const dialog = document.getElementById("edit-dialog");
const editForm = document.getElementById("edit-form");
const field = (name) => editForm.elements.namedItem(name); // not editForm.title: that is the form's own title attribute
let editing = null;

function openEdit(bookId) {
  const b = state.scan.books.find((x) => x.id === bookId);
  editing = bookId;
  field("title").value = b.title;
  field("author").value = b.author;
  document.getElementById("edit-heading").textContent = b.title ? "Edit book" : "Name this book";
  dialog.showModal();
  field("title").focus();
}
document.getElementById("edit-cancel").addEventListener("click", () => dialog.close());
editForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const title = field("title").value.trim();
  if (!title) return;
  dialog.close();
  await mutate(() => api("PATCH", `/api/scans/${state.scan.id}/books/${editing}`,
    { title, author: field("author").value.trim() }), "Saved");
});

// ---------- boot ----------

window.addEventListener("hashchange", route);
api("GET", "/api/config").then((c) => { state.config = c; route(); });
