let currentFilter = "";

function showToast(msg) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.hidden = false;
  setTimeout(() => { el.hidden = true; }, 3500);
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (res.status === 401) {
    window.location.href = "/?signin=1";
    throw new Error("Please sign in");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = data.detail;
    throw new Error(typeof detail === "string" ? detail : detail?.message || "Request failed");
  }
  return data;
}

function fmtDate(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

async function signOut() {
  try {
    sessionStorage.removeItem("chappie_session_center_address");
    sessionStorage.removeItem("chappie_session_target_categories");
    sessionStorage.removeItem("chappie_session_search_results");
  } catch (_) {}
  await api("/api/auth/signout", { method: "POST" });
  window.location.href = "/";
}

function renderCounts(statuses) {
  const el = document.getElementById("pipelineCounts");
  el.innerHTML = (statuses || []).map((s) => `
    <div class="pipe-count">
      <div class="n">${s.count}</div>
      <div class="l">${s.label}</div>
    </div>
  `).join("");
}

function renderDeals(deals) {
  const body = document.getElementById("pipelineBody");
  if (!deals.length) {
    body.innerHTML = `<tr><td colspan="7" class="muted">No outreach yet. Send an email from Search to start tracking.</td></tr>`;
    return;
  }

  body.innerHTML = deals.map((d) => {
    const options = ["sent", "opened", "replied", "booked", "closed"]
      .map((s) => {
        const sel = s === d.status ? " selected" : "";
        return `<option value="${s}"${sel}>${s.charAt(0).toUpperCase() + s.slice(1)}</option>`;
      })
      .join("");
    return `
    <tr>
      <td>
        <div class="deal-name">${esc(d.business_name)}</div>
        <div class="deal-address">${esc(d.business_address || "")}</div>
      </td>
      <td>${esc((d.recipients || []).join(", "))}</td>
      <td>${esc(d.subject || "—")}</td>
      <td><strong>${esc(d.status_label)}</strong></td>
      <td>${d.open_count || 0}</td>
      <td>${fmtDate(d.updated_at)}</td>
      <td>
        <select class="status-select" data-deal-id="${esc(d.id)}" onchange="changeStatus(this)">
          ${options}
        </select>
      </td>
    </tr>`;
  }).join("");
}

function esc(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

async function changeStatus(selectEl) {
  const dealId = selectEl.dataset.dealId;
  const status = selectEl.value;
  try {
    await api(`/api/pipeline/${dealId}`, {
      method: "PATCH",
      body: JSON.stringify({ status }),
    });
    showToast(`Marked as ${status}`);
    await Promise.all([loadPipeline(currentFilter), loadInsights()]);
  } catch (e) {
    showToast(e.message);
  }
}

function filterPipeline(status) {
  currentFilter = status || "";
  document.querySelectorAll(".pipe-filter").forEach((btn) => {
    btn.classList.toggle("active", (btn.dataset.status || "") === currentFilter);
  });
  loadPipeline(currentFilter);
}

function renderInsights(data) {
  const msg = document.getElementById("learningMessage");
  const winners = document.getElementById("learningWinners");
  if (!msg || !winners) return;

  msg.textContent = data.message || "";
  const list = data.winners || [];
  if (!list.length) {
    winners.innerHTML = "";
    return;
  }

  winners.innerHTML = list.map((w) => {
    const typeLabel = {
      category_city: "Category + city",
      category: "Category",
      city: "City",
    }[w.type] || w.type;
    return `
      <div class="learning-chip">
        <div class="learning-chip-label">${esc(w.label)}</div>
        <div class="learning-chip-meta">${esc(typeLabel)} · ${w.deals} deals · strength ${w.strength}</div>
      </div>
    `;
  }).join("");
}

async function loadInsights() {
  const data = await api("/api/pipeline/insights");
  renderInsights(data);
}

async function loadPipeline(status = "") {
  const qs = status ? `?status=${encodeURIComponent(status)}` : "";
  const data = await api(`/api/pipeline${qs}`);
  renderCounts(data.statuses);
  renderDeals(data.deals);
}

window.signOut = signOut;
window.filterPipeline = filterPipeline;
window.changeStatus = changeStatus;

document.addEventListener("DOMContentLoaded", () => {
  Promise.all([loadPipeline(), loadInsights()]).catch((e) => showToast(e.message));
});
