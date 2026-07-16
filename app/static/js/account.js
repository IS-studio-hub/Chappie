function money(n) {
  const v = Number(n || 0);
  return `$${v.toFixed(2)} CAD`;
}

function fmtDate(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

function showToast(msg) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.hidden = false;
  setTimeout(() => { el.hidden = true; }, 4000);
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

function renderUpgrade(usage, currentPlan) {
  const grid = document.getElementById("upgradeGrid");
  const paid = (usage.plans || []).filter((p) => p.id !== "free");
  grid.innerHTML = paid.map((p) => {
    const isCurrent = p.id === currentPlan;
    const order = ["free", "small", "mid", "large"];
    const canUpgrade = order.indexOf(p.id) > order.indexOf(currentPlan);
    const label = isCurrent ? "Current plan" : canUpgrade ? "Upgrade" : "Lower plan";
    const disabled = isCurrent || !canUpgrade;
    return `
      <div class="upgrade-card">
        <h3>${p.name}</h3>
        <div class="price">$${p.price_cad}<span style="font-size:0.8rem;color:var(--muted)"> CAD/mo</span></div>
        <ul>
          <li>${p.max_searches} searches per month</li>
          <li>Up to ${p.max_results} businesses per search</li>
        </ul>
        <button type="button" class="btn-plan" ${disabled ? "disabled" : ""} onclick="checkout('${p.id}')">${label}</button>
      </div>
    `;
  }).join("");

  document.getElementById("contactBox").hidden = false;
  document.getElementById("studioName").textContent = usage.studio_name || "IS Studio";
  const email = usage.studio_email || "hello@isexperience.house";
  const emailEl = document.getElementById("studioEmail");
  emailEl.href = `mailto:${email}`;
  emailEl.textContent = email;
  if (usage.studio_url) {
    document.getElementById("studioUrl").href = usage.studio_url;
  }
}

function renderBilling(events) {
  const body = document.getElementById("billingBody");
  if (!events.length) {
    body.innerHTML = `<tr><td colspan="4" class="muted">No billing activity yet.</td></tr>`;
    return;
  }
  body.innerHTML = events.map((e) => {
    const amt = Number(e.amount_cad || 0);
    const cls = amt >= 0 ? "amount-pos" : "amount-neg";
    const sign = amt > 0 ? "+" : "";
    return `
      <tr>
        <td>${fmtDate(e.created_at)}</td>
        <td>${e.type}</td>
        <td>${e.description || "—"}</td>
        <td class="${cls}">${sign}${money(amt)}</td>
      </tr>
    `;
  }).join("");
}

async function checkout(plan) {
  try {
    const data = await api("/api/billing/checkout", {
      method: "POST",
      body: JSON.stringify({ plan }),
    });
    window.location.href = data.url;
  } catch (e) {
    showToast(e.message);
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

async function loadAccount() {
  const params = new URLSearchParams(window.location.search);
  if (params.get("verified") === "1") {
    showToast("Email verified — your account is ready.");
  }
  if (params.get("checkout") === "success" && params.get("session_id")) {
    try {
      await api(`/api/billing/confirm?session_id=${encodeURIComponent(params.get("session_id"))}`);
      showToast("Payment successful — your new plan is active.");
      history.replaceState({}, "", "/account");
    } catch (e) {
      showToast(e.message);
    }
  } else if (params.get("checkout") === "cancelled") {
    showToast("Checkout cancelled.");
    history.replaceState({}, "", "/account");
  }

  const data = await api("/api/account/billing");
  const user = data.user;
  const usage = data.usage;

  document.getElementById("accountName").textContent = user.name || "My account";
  document.getElementById("accountEmail").textContent = user.email;
  document.getElementById("planBadge").textContent = usage.plan_name;
  document.getElementById("creditBalance").textContent = money(usage.credit_balance_cad);
  document.getElementById("searchesUsed").textContent =
    `${usage.searches_used} / ${usage.max_searches}`;
  document.getElementById("maxResults").textContent = `${usage.max_results} businesses`;
  document.getElementById("periodStart").textContent = fmtDate(usage.period_start);
  document.getElementById("periodEnd").textContent = fmtDate(usage.period_end);
  document.getElementById("totalSearchSpend").textContent = money(usage.total_search_spend_cad);
  document.getElementById("totalPayments").textContent = money(usage.total_payments_cad);
  document.getElementById("usageNote").textContent = usage.can_search
    ? "You have searches remaining this period."
    : usage.exhausted_action === "contact"
      ? "Budget used — contact IS Studio for a special offer."
      : "Budget used — upgrade your plan to continue searching.";

  renderUpgrade(usage, usage.plan);
  renderBilling(data.events || []);

  const upgradePlan = params.get("upgrade");
  if (upgradePlan && ["small", "mid", "large"].includes(upgradePlan)) {
    history.replaceState({}, "", "/account");
    showToast("Starting checkout for your selected plan…");
    setTimeout(() => checkout(upgradePlan), 600);
  } else if (params.get("verified") === "1") {
    history.replaceState({}, "", "/account");
  }

  scrollToUpgradePanelIfNeeded();
}

function scrollToUpgradePanelIfNeeded() {
  if (window.location.hash !== "#upgradePanel") return;
  const panel = document.getElementById("upgradePanel");
  if (!panel) return;
  requestAnimationFrame(() => {
    panel.scrollIntoView({ behavior: "smooth", block: "start" });
    panel.classList.add("panel-highlight");
    setTimeout(() => panel.classList.remove("panel-highlight"), 2200);
  });
}

window.checkout = checkout;
window.signOut = signOut;

document.addEventListener("DOMContentLoaded", () => {
  loadAccount().catch((e) => showToast(e.message));
});
