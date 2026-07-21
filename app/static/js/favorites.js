function showToast(msg) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.hidden = false;
  if (window.ChappieA11y) ChappieA11y.announce(msg);
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

function esc(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
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

function emailsFor(fav) {
  const list = Array.isArray(fav.contact_emails) ? fav.contact_emails.filter(Boolean) : [];
  if (fav.contact_email && !list.includes(fav.contact_email)) list.unshift(fav.contact_email);
  return list;
}

function renderFavorites(favorites) {
  const grid = document.getElementById("favoritesGrid");
  const empty = document.getElementById("favoritesEmpty");
  const count = document.getElementById("favoritesCount");
  count.textContent = `${favorites.length} saved`;

  if (!favorites.length) {
    grid.innerHTML = "";
    empty.hidden = false;
    return;
  }
  empty.hidden = true;

  grid.innerHTML = favorites.map((f) => {
    const emails = emailsFor(f);
    const rating = f.rating
      ? `★ ${f.rating}${f.review_count != null ? ` (${f.review_count})` : ""}`
      : "";
    return `
      <article class="favorite-card">
        <div class="favorite-card-top">
          <div class="favorite-name">${esc(f.business_name)}</div>
          ${rating ? `<div class="favorite-rating">${esc(rating)}</div>` : ""}
        </div>
        ${(() => {
          const biz = f.business || {};
          const score = biz.website_opportunity_score;
          if (score == null) return "";
          return `<div class="favorite-opp">Website Opportunity: <strong>${score}/100</strong>${biz.website_opportunity_label ? ` · ${esc(biz.website_opportunity_label)}` : ""}</div>`;
        })()}
        ${f.category ? `<div class="favorite-category">${esc(f.category)}</div>` : ""}
        ${f.business_address ? `<div class="favorite-meta">${esc(f.business_address)}</div>` : ""}
        ${f.phone ? `<div class="favorite-meta">${esc(f.phone)}</div>` : ""}
        ${emails.length
          ? `<div class="favorite-meta">${emails.map((e) => `<a href="mailto:${esc(e)}">${esc(e)}</a>`).join("<br>")}</div>`
          : ""}
        <div class="favorite-actions">
          ${f.google_maps_url
            ? `<a href="${esc(f.google_maps_url)}" target="_blank" rel="noopener">Maps</a>`
            : ""}
          ${f.website_url
            ? `<a href="${esc(f.website_url)}" target="_blank" rel="noopener">Website</a>`
            : ""}
          <button type="button" class="btn-remove" onclick="removeFavorite('${esc(f.id)}')">Remove</button>
        </div>
      </article>
    `;
  }).join("");
}

async function removeFavorite(id) {
  try {
    await api(`/api/favorites/${id}`, { method: "DELETE" });
    showToast("Removed from favorites");
    await loadFavorites();
  } catch (e) {
    showToast(e.message);
  }
}

async function loadFavorites() {
  const data = await api("/api/favorites");
  renderFavorites(data.favorites || []);
}

window.signOut = signOut;
window.removeFavorite = removeFavorite;

document.addEventListener("DOMContentLoaded", () => {
  loadFavorites().catch((e) => showToast(e.message));
});
