let currentJobId = null;
let pollInterval = null;
let allBusinesses = [];
let filteredBusinesses = [];
let selectedIndex = -1;
let currentView = "cards";
let map = null;
let markers = [];
let figmaConnected = false;
let openaiConnected = false;
let gmailConnected = false;
let usageInfo = null;
let currentUserId = null;

const STORAGE_KEYS = {
  senderBusinessName: "chappie_sender_business_name",
  senderInfo: "chappie_sender_business_info",
  figmaToken: "chappie_figma_token",
  openaiApiKey: "chappie_openai_api_key",
  figmaPrototypeLink: "chappie_figma_prototype_link",
  gmailEmail: "chappie_gmail_email",
  targetCategories: "chappie_target_categories",
};

/** Session-only search fields + results — session cache; server is source of truth */
const SEARCH_SESSION_KEYS = {
  address: "chappie_session_center_address",
  targetCategories: "chappie_session_target_categories",
  searchResults: "chappie_session_search_results",
  jobId: "chappie_session_job_id",
};

function clearSearchSessionFields() {
  try {
    sessionStorage.removeItem(SEARCH_SESSION_KEYS.address);
    sessionStorage.removeItem(SEARCH_SESSION_KEYS.targetCategories);
    sessionStorage.removeItem(SEARCH_SESSION_KEYS.searchResults);
    sessionStorage.removeItem(SEARCH_SESSION_KEYS.jobId);
    // Remove legacy localStorage target categories from older builds
    Object.keys(localStorage).forEach((key) => {
      if (key === STORAGE_KEYS.targetCategories || key.startsWith(`${STORAGE_KEYS.targetCategories}:`)) {
        localStorage.removeItem(key);
      }
    });
  } catch {
    /* ignore */
  }
}

function clearSavedSearchResults() {
  try {
    sessionStorage.removeItem(SEARCH_SESSION_KEYS.searchResults);
  } catch {
    /* ignore */
  }
}

function saveActiveJobId(jobId) {
  try {
    if (jobId) sessionStorage.setItem(SEARCH_SESSION_KEYS.jobId, jobId);
    else sessionStorage.removeItem(SEARCH_SESSION_KEYS.jobId);
    if (currentUserId) {
      const key = storageKey("chappie_active_job_id");
      if (jobId) localStorage.setItem(key, jobId);
      else localStorage.removeItem(key);
    }
  } catch {
    /* ignore */
  }
}

function loadActiveJobId() {
  try {
    return (
      sessionStorage.getItem(SEARCH_SESSION_KEYS.jobId)
      || (currentUserId ? localStorage.getItem(storageKey("chappie_active_job_id")) : null)
      || null
    );
  } catch {
    return null;
  }
}

function saveSearchResults(result) {
  if (!result || !Array.isArray(result.businesses)) return;
  try {
    sessionStorage.setItem(SEARCH_SESSION_KEYS.searchResults, JSON.stringify(result));
  } catch {
    // Quota: drop heavy brand_book payloads and retry
    try {
      const slim = {
        ...result,
        businesses: result.businesses.map((b) => {
          const { brand_book, ...rest } = b;
          return rest;
        }),
      };
      sessionStorage.setItem(SEARCH_SESSION_KEYS.searchResults, JSON.stringify(slim));
    } catch {
      /* ignore */
    }
  }
}

function loadSavedSearchResults() {
  try {
    const raw = sessionStorage.getItem(SEARCH_SESSION_KEYS.searchResults);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || !Array.isArray(parsed.businesses) || !parsed.businesses.length) return null;
    return parsed;
  } catch {
    return null;
  }
}

function storageKey(base) {
  return currentUserId ? `${base}:${currentUserId}` : base;
}

const SOCIAL_META = {
  facebook: {
    label: "Facebook",
    color: "#1877F2",
    icon: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M24 12.073c0-6.627-5.373-12-12-12s-12 5.373-12 12c0 5.99 4.388 10.954 10.125 11.854v-8.385H7.078v-3.47h3.047V9.43c0-3.007 1.792-4.669 4.533-4.669 1.312 0 2.686.235 2.686.235v2.953H15.83c-1.491 0-1.956.925-1.956 1.874v2.25h3.328l-.532 3.47h-2.796v8.385C19.612 23.027 24 18.062 24 12.073z"/></svg>',
  },
  instagram: {
    label: "Instagram",
    color: "#E4405F",
    icon: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2.163c3.204 0 3.584.012 4.85.07 3.252.148 4.771 1.691 4.919 4.919.058 1.265.069 1.645.069 4.849 0 3.205-.012 3.584-.069 4.849-.149 3.225-1.664 4.771-4.919 4.919-1.266.058-1.644.07-4.85.07-3.204 0-3.584-.012-4.849-.07-3.26-.149-4.771-1.699-4.919-4.92-.058-1.265-.07-1.644-.07-4.849 0-3.204.013-3.583.07-4.849.149-3.227 1.664-4.771 4.919-4.919 1.266-.057 1.645-.069 4.849-.069zM12 0C8.741 0 8.333.014 7.053.072 2.695.272.273 2.69.073 7.052.014 8.333 0 8.741 0 12c0 3.259.014 3.668.072 4.948.2 4.358 2.618 6.78 6.98 6.98C8.333 23.986 8.741 24 12 24c3.259 0 3.668-.014 4.948-.072 4.354-.2 6.782-2.618 6.979-6.98.059-1.28.073-1.689.073-4.948 0-3.259-.014-3.667-.072-4.947-.196-4.354-2.617-6.78-6.979-6.98C15.668.014 15.259 0 12 0zm0 5.838a6.162 6.162 0 100 12.324 6.162 6.162 0 000-12.324zM12 16a4 4 0 110-8 4 4 0 010 8zm6.406-11.845a1.44 1.44 0 100 2.881 1.44 1.44 0 000-2.881z"/></svg>',
  },
  linkedin: {
    label: "LinkedIn",
    color: "#0A66C2",
    icon: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.85 3.37-1.85 3.601 0 4.267 2.37 4.267 5.455v6.286zM5.337 7.433a2.062 2.062 0 01-2.063-2.065 2.064 2.064 0 114.127 0 2.063 2.063 0 01-2.063 2.065zm1.782 13.019H3.555V9h3.564v11.452zM22.225 0H1.771C.792 0 0 .774 0 1.729v20.542C0 23.227.792 24 1.771 24h20.451C23.2 24 24 23.227 24 22.271V1.729C24 .774 23.2 0 22.222 0h.003z"/></svg>',
  },
  twitter: {
    label: "X",
    color: "#ffffff",
    icon: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z"/></svg>',
  },
  youtube: {
    label: "YouTube",
    color: "#FF0000",
    icon: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M23.498 6.186a3.016 3.016 0 00-2.122-2.136C19.505 3.545 12 3.545 12 3.545s-7.505 0-9.377.505A3.017 3.017 0 00.502 6.186C0 8.07 0 12 0 12s0 3.93.502 5.814a3.016 3.016 0 002.122 2.136c1.871.505 9.376.505 9.376.505s7.505 0 9.377-.505a3.015 3.015 0 002.122-2.136C24 15.93 24 12 24 12s0-3.93-.502-5.814zM9.545 15.568V8.432L15.818 12l-6.273 3.568z"/></svg>',
  },
  tiktok: {
    label: "TikTok",
    color: "#ffffff",
    icon: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12.525.02c1.31-.02 2.61-.01 3.91-.02.08 1.53.63 3.09 1.75 4.17 1.12 1.11 2.7 1.62 4.24 1.79v4.03c-1.44-.05-2.89-.35-4.2-.97-.57-.26-1.1-.59-1.62-.93v10.01c0 4.42-3.58 8.01-8 8.01-1.98 0-3.86-.72-5.29-2.03-1.66-1.53-2.58-3.71-2.53-5.97.08-4.58 3.83-8.29 8.41-8.29.89 0 1.75.14 2.55.4v4.12a4.64 4.64 0 00-2.55-.75c-2.56 0-4.64 2.08-4.64 4.64 0 2.56 2.08 4.64 4.64 4.64 2.55 0 4.63-2.08 4.63-4.64V.02h.01z"/></svg>',
  },
  pinterest: {
    label: "Pinterest",
    color: "#BD081C",
    icon: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 0C5.373 0 0 5.372 0 12c0 5.084 3.163 9.426 7.627 11.174-.105-.949-.2-2.403.042-3.441.218-.937 1.407-5.965 1.407-5.965s-.359-.719-.359-1.782c0-1.668.967-2.914 2.171-2.914 1.023 0 1.518.769 1.518 1.69 0 1.029-.655 2.568-.994 3.995-.283 1.194.599 2.169 1.777 2.169 2.133 0 3.772-2.249 3.772-5.495 0-2.873-2.064-4.882-5.012-4.882-3.414 0-5.418 2.561-5.418 5.207 0 1.031.397 2.138.893 2.738a.36.36 0 01.083.345l-.333 1.36c-.053.22-.174.267-.402.161-1.499-.698-2.436-2.889-2.436-4.649 0-3.785 2.75-7.262 7.929-7.262 4.163 0 7.398 2.967 7.398 6.931 0 4.136-2.607 7.464-6.227 7.464-1.216 0-2.359-.631-2.75-1.378l-.748 2.853c-.271 1.043-1.002 2.35-1.492 3.146C9.57 23.812 10.763 24 12 24c6.627 0 12-5.373 12-12 0-6.628-5.373-12-12-12z"/></svg>',
  },
  yelp: {
    label: "Yelp",
    color: "#FF1A1A",
    icon: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12.271 15.324c-.313.846-.684 1.847-1.084 2.887l-.006.016c-.382 1.001-.8 2.092-1.195 3.043-.39.94-.77 1.74-1.095 2.28-.163.27-.31.48-.435.62-.125.14-.22.2-.285.2-.065 0-.16-.06-.285-.2-.125-.14-.272-.35-.435-.62-.325-.54-.705-1.34-1.095-2.28-.395-.951-.813-2.042-1.195-3.043l-.006-.016c-.4-1.04-.771-2.041-1.084-2.887-.313-.846-.47-1.38-.47-1.62 0-.24.157-.774.47-1.62.313-.846.684-1.847 1.084-2.887l.006-.016c.382-1.001.8-2.092 1.195-3.043.39-.94.77-1.74 1.095-2.28.163-.27.31-.48.435-.62.125-.14.22-.2.285-.2.065 0 .16.06.285.2.125.14.272.35.435.62.325.54.705 1.34 1.095 2.28.395.951.813 2.042 1.195 3.043l.006.016c.4 1.04.771 2.041 1.084 2.887.313.846.47 1.38.47 1.62 0 .24-.157.774-.47 1.62z"/></svg>',
  },
};

function socialIconLetter(key) {
  return `<span class="social-icon-letter">${esc(key.charAt(0).toUpperCase())}</span>`;
}

function renderSocialIcons(profiles, stopPropagation = false) {
  const entries = Object.entries(profiles || {}).filter(([, url]) => url);
  if (!entries.length) return "";
  const stop = stopPropagation ? ' onclick="event.stopPropagation()"' : "";
  return `<div class="social-icons">${entries.map(([key, url]) => {
    const meta = SOCIAL_META[key] || { label: key, color: "var(--accent)", icon: socialIconLetter(key) };
    return `<a class="social-icon social-${esc(key)}" href="${esc(url)}" target="_blank" rel="noopener noreferrer" title="${esc(meta.label)}" style="--social-color:${meta.color}"${stop}>${meta.icon}</a>`;
  }).join("")}</div>`;
}

// ── Init ──
document.addEventListener("DOMContentLoaded", async () => {
  checkApiStatus();
  await initCurrentUser();
  checkFigmaStatus();
  checkOpenAIStatus();
  checkGmailStatus();
  refreshUsage();
  setupRadiusSlider();
  loadSenderBusinessName();
  loadSenderInfo();
  loadFigmaPrototypeLink();
  loadSearchSessionFields();
  // Browser autofill often dumps the login email into random text fields — strip it
  sanitizeSearchSessionFields();
  setTimeout(sanitizeSearchSessionFields, 150);
  setTimeout(sanitizeSearchSessionFields, 600);
  document.getElementById("address").addEventListener("keydown", (e) => {
    if (e.key === "Enter") startSearch();
  });
  document.getElementById("address").addEventListener("input", saveCenterAddress);
  document.getElementById("filterInput").addEventListener("input", applyFilter);
  document.getElementById("sortSelect").addEventListener("change", applyFilter);
  document.getElementById("senderBusinessName").addEventListener("input", saveSenderBusinessName);
  document.getElementById("senderBusinessInfo").addEventListener("input", saveSenderInfo);
  document.getElementById("targetCategories")?.addEventListener("input", saveTargetCategories);
  document.getElementById("advancedFilters")?.addEventListener("change", applyFilter);
  document.getElementById("advancedFilters")?.addEventListener("input", applyFilter);
  // Ensure height fits after fonts/layout settle
  requestAnimationFrame(updateSenderInfoUi);
  document.querySelectorAll(".integrations-dropdown").forEach((el) => {
    el.addEventListener("toggle", (e) => {
      if (e.target.open) {
        syncSidebarPlanGates();
        requestAnimationFrame(updateSenderInfoUi);
      }
    });
  });
  setupPipelinePlanGate();
  document.getElementById("pipelineUpgradeModal")?.addEventListener("click", (e) => {
    if (e.target.id === "pipelineUpgradeModal") closePipelineUpgradeModal();
  });
  document.getElementById("campaignModal")?.addEventListener("click", (e) => {
    if (e.target.id === "campaignModal") closeCampaignModal();
  });
  document.getElementById("figmaPrototypeLink").addEventListener("input", saveFigmaPrototypeLink);
  document.getElementById("emailLanguage")?.addEventListener("change", syncEmailLanguageUi);
  document.getElementById("siteLanguage")?.addEventListener("change", syncSiteLanguageUi);

  // Do NOT auto-connect shared tokens — each user connects their own integrations

  // OAuth callback params
  const params = new URLSearchParams(window.location.search);
  if (params.get("gmail_connected")) {
    showToast("Gmail connected via Google!");
    window.history.replaceState({}, "", "/app");
    checkGmailStatus();
  }
  if (params.get("verified") === "1") {
    showToast("Email verified — welcome to CH4PP!3!");
    window.history.replaceState({}, "", "/app");
  }
  if (params.get("gmail_error")) {
    const err = params.get("gmail_error");
    if (err.includes("redirect_uri_mismatch") || err === "access_denied") {
      document.getElementById("gmailOAuthSetup").style.display = "block";
    }
    showToast("Gmail connect failed: " + err);
    window.history.replaceState({}, "", "/app");
  }

  loadGmailOAuthSetup();

  // Restore in-progress search or last completed results from the server
  await restorePersistedSearch();
});

async function restorePersistedSearch() {
  try {
    const activeRes = await fetch("/api/search/active", { credentials: "include" });
    if (activeRes.status === 401) return;
    if (activeRes.ok) {
      const activeData = await activeRes.json();
      const job = activeData?.job;
      if (job && (job.status === "pending" || job.status === "running")) {
        currentJobId = job.job_id;
        saveActiveJobId(job.job_id);
        document.getElementById("emptyState").style.display = "none";
        setSearching(true);
        const pct = job.total > 0 ? Math.round((job.progress / job.total) * 100) : 5;
        showProgress(pct, job.message || "Search still running…");
        if (pollInterval) clearInterval(pollInterval);
        pollInterval = setInterval(pollStatus, 1500);
        showToast("Resuming your search…");
        return;
      }
    }

    const latestRes = await fetch("/api/search/latest", { credentials: "include" });
    if (latestRes.ok) {
      const latestData = await latestRes.json();
      const job = latestData?.job;
      if (job?.status === "completed" && job.result?.businesses?.length) {
        currentJobId = job.job_id;
        saveActiveJobId(null);
        document.getElementById("emptyState").style.display = "none";
        renderResults(job.result);
        return;
      }
    }
  } catch {
    /* fall through to session cache */
  }

  const saved = loadSavedSearchResults();
  if (saved) {
    document.getElementById("emptyState").style.display = "none";
    renderResults(saved);
  }
}

async function initCurrentUser() {
  try {
    const res = await fetch("/api/auth/me", { credentials: "include" });
    if (!res.ok) return;
    const data = await res.json();
    currentUserId = data.user?.id || null;
  } catch {
    currentUserId = null;
  }
}

async function loadGmailOAuthSetup() {
  try {
    const res = await fetch("/api/gmail/oauth/setup", { credentials: "include" });
    if (!res.ok) return;
    const data = await res.json();
    const setup = document.getElementById("gmailOAuthSetup");
    if (data.redirect_uri) {
      document.getElementById("gmailRedirectUri").textContent = data.redirect_uri;
    }
    if (data.javascript_origin) {
      const originEl = document.getElementById("gmailJsOrigin");
      if (originEl) originEl.textContent = data.javascript_origin;
    }
    if (data.console_url) {
      document.getElementById("gmailConsoleLink").href = data.console_url;
    }
    if (setup) setup.style.display = "block";
  } catch (_) {}
}

function copyGmailRedirectUri() {
  const uri = document.getElementById("gmailRedirectUri").textContent;
  navigator.clipboard.writeText(uri).then(() => showToast("Redirect URI copied!"));
}

async function autoConnectFigma(token) {
  try {
    const res = await fetch("/api/figma/connect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    });
    if (res.ok) {
      const data = await res.json();
      figmaConnected = true;
      updateFigmaUI(data);
    }
  } catch {
    updateFigmaUI({ connected: false });
  }
}

async function checkApiStatus() {
  try {
    const res = await fetch("/api/health");
    const data = await res.json();
    const dot = document.getElementById("apiDot");
    const label = document.getElementById("apiLabel");
    if (data.credentials_configured) {
      dot.classList.add("ready");
      label.textContent = data.auth_method === "service_account"
        ? "Service Account Ready"
        : "API Ready";
    } else {
      label.textContent = "Credentials Missing";
    }
  } catch {
    document.getElementById("apiLabel").textContent = "Server Offline";
  }
}

function loadSenderBusinessName() {
  const el = document.getElementById("senderBusinessName");
  if (!el) return;
  const saved = localStorage.getItem(storageKey(STORAGE_KEYS.senderBusinessName));
  el.value = saved ? saved.slice(0, 80) : "";
}

function saveSenderBusinessName() {
  const el = document.getElementById("senderBusinessName");
  if (!el) return;
  if (el.value.length > 80) {
    el.value = el.value.slice(0, 80);
  }
  localStorage.setItem(storageKey(STORAGE_KEYS.senderBusinessName), el.value);
}

function getSenderBusinessName() {
  const el = document.getElementById("senderBusinessName");
  return el ? el.value.trim().slice(0, 80) : "";
}

function loadSenderInfo() {
  const el = document.getElementById("senderBusinessInfo");
  const saved = localStorage.getItem(storageKey(STORAGE_KEYS.senderInfo));
  el.value = saved ? saved.slice(0, 400) : "";
  updateSenderInfoUi();
}

function saveSenderInfo() {
  const el = document.getElementById("senderBusinessInfo");
  if (el.value.length > 400) {
    el.value = el.value.slice(0, 400);
  }
  localStorage.setItem(storageKey(STORAGE_KEYS.senderInfo), el.value);
  updateSenderInfoUi();
}

function updateSenderInfoUi() {
  const el = document.getElementById("senderBusinessInfo");
  if (!el) return;
  const count = document.getElementById("senderInfoCount");
  if (count) count.textContent = String(el.value.length);
  // Grow height to fit content — no scrollbar
  el.style.height = "auto";
  el.style.height = `${el.scrollHeight}px`;
}

function getSenderInfo() {
  return document.getElementById("senderBusinessInfo").value.trim().slice(0, 400);
}

function loadFigmaPrototypeLink() {
  const el = document.getElementById("figmaPrototypeLink");
  const saved = localStorage.getItem(storageKey(STORAGE_KEYS.figmaPrototypeLink));
  el.value = saved || "";
}

function saveFigmaPrototypeLink() {
  localStorage.setItem(
    storageKey(STORAGE_KEYS.figmaPrototypeLink),
    document.getElementById("figmaPrototypeLink").value.trim()
  );
}

function getFigmaPrototypeLink() {
  return document.getElementById("figmaPrototypeLink").value.trim();
}

function looksLikeEmail(value) {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(String(value || "").trim());
}

function loadSearchSessionFields() {
  const addressEl = document.getElementById("address");
  const catsEl = document.getElementById("targetCategories");
  if (addressEl) {
    addressEl.value = sessionStorage.getItem(SEARCH_SESSION_KEYS.address) || "";
  }
  if (catsEl) {
    catsEl.value = sessionStorage.getItem(SEARCH_SESSION_KEYS.targetCategories) || "";
  }
  // Drop legacy persisted categories so they don't survive logins
  try {
    Object.keys(localStorage).forEach((key) => {
      if (key === STORAGE_KEYS.targetCategories || key.startsWith(`${STORAGE_KEYS.targetCategories}:`)) {
        localStorage.removeItem(key);
      }
    });
  } catch {
    /* ignore */
  }
}

function sanitizeSearchSessionFields() {
  const catsEl = document.getElementById("targetCategories");
  if (catsEl && looksLikeEmail(catsEl.value)) {
    catsEl.value = "";
    try {
      sessionStorage.removeItem(SEARCH_SESSION_KEYS.targetCategories);
    } catch {
      /* ignore */
    }
  }
  // If session somehow stored an email as categories, clear it
  try {
    const stored = sessionStorage.getItem(SEARCH_SESSION_KEYS.targetCategories) || "";
    if (looksLikeEmail(stored)) {
      sessionStorage.removeItem(SEARCH_SESSION_KEYS.targetCategories);
      if (catsEl) catsEl.value = "";
    }
  } catch {
    /* ignore */
  }
}

function saveCenterAddress() {
  const el = document.getElementById("address");
  if (!el) return;
  sessionStorage.setItem(SEARCH_SESSION_KEYS.address, el.value.trim());
}

function saveTargetCategories() {
  const el = document.getElementById("targetCategories");
  if (!el) return;
  const value = el.value.trim();
  // Never persist autofilled emails into target categories
  if (looksLikeEmail(value)) {
    el.value = "";
    sessionStorage.removeItem(SEARCH_SESSION_KEYS.targetCategories);
    return;
  }
  sessionStorage.setItem(SEARCH_SESSION_KEYS.targetCategories, value);
}

function getTargetCategories() {
  const value = (document.getElementById("targetCategories")?.value || "").trim();
  return looksLikeEmail(value) ? "" : value;
}

async function checkGmailStatus() {
  try {
    const res = await fetch("/api/gmail/status", { credentials: "include" });
    if (res.status === 401) {
      updateGmailUI({ connected: false });
      return;
    }
    const data = await res.json();
    gmailConnected = data.connected;
    updateGmailUI(data);
  } catch {
    updateGmailUI({ connected: false });
  }
}

function updateGmailUI(data) {
  const dot = document.getElementById("gmailDot");
  const label = document.getElementById("gmailLabel");
  const btn = document.getElementById("gmailConnectBtn");

  if (data.connected) {
    dot.classList.add("connected");
    const via = data.method === "oauth" ? "Google" : "App Password";
    label.textContent = data.email ? `Connected (${via}) as ${data.email}` : `Connected via ${via}`;
    btn.textContent = "Disconnect";
    btn.onclick = disconnectGmail;
  } else {
    dot.classList.remove("connected");
    label.textContent = "Not connected";
    btn.textContent = "Connect";
    btn.onclick = connectGmail;
  }

  updateActionButtons();
}

async function connectGmail() {
  const email = document.getElementById("gmailEmail").value.trim();
  const appPassword = document.getElementById("gmailAppPassword").value.trim();

  if (!email || !appPassword) {
    showToast("Enter your Gmail address and app password.");
    return;
  }

  const btn = document.getElementById("gmailConnectBtn");
  btn.disabled = true;
  btn.textContent = "Connecting...";

  try {
    const res = await fetch("/api/gmail/connect", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, app_password: appPassword }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Connection failed");

    gmailConnected = true;
    updateGmailUI(data);
    showToast("Gmail connected — ready to send emails!");
  } catch (e) {
    const msg = e.message || "Connection failed";
    if (msg.includes("authentication failed") || msg.includes("535")) {
      showToast("App password rejected by Google. Use the blue “Connect with Google” button above instead.");
    } else {
      showToast(msg);
    }
    updateGmailUI({ connected: false });
  } finally {
    btn.disabled = false;
  }
}

async function disconnectGmail() {
  await fetch("/api/gmail/disconnect", { method: "POST", credentials: "include" });
  gmailConnected = false;
  document.getElementById("gmailAppPassword").value = "";
  updateGmailUI({ connected: false });
  showToast("Gmail disconnected.");
}

function connectGmailOAuth() {
  window.location.href = "/api/gmail/oauth/start";
}

let emailDraft = null; // { index, business, recipients, subject, body, language }
let dealByPlaceId = {};
let favoritePlaceIds = {};

const EMAIL_RTL_LANGS = new Set(["Arabic", "Hebrew", "Urdu", "Persian", "Farsi"]);

function getSelectedEmailLanguage() {
  const select = document.getElementById("emailLanguage");
  if (!select) return "English";
  if (select.value === "__custom__") {
    return (document.getElementById("emailLanguageCustom")?.value || "").trim() || "English";
  }
  return select.value || "English";
}

function syncEmailLanguageUi() {
  const select = document.getElementById("emailLanguage");
  const custom = document.getElementById("emailLanguageCustom");
  if (!select || !custom) return;
  const isCustom = select.value === "__custom__";
  custom.hidden = !isCustom;
  if (isCustom) custom.focus();
  applyEmailTextDirection(getSelectedEmailLanguage());
}

function applyEmailTextDirection(language) {
  const body = document.getElementById("emailPreviewBody");
  const subject = document.getElementById("emailPreviewSubject");
  const rtl = EMAIL_RTL_LANGS.has(language) || /arabic|hebrew|urdu|persian|farsi/i.test(language || "");
  body?.classList.toggle("is-rtl", rtl);
  if (subject) {
    subject.style.direction = rtl ? "rtl" : "ltr";
    subject.style.textAlign = rtl ? "right" : "left";
  }
}

async function fetchEmailPreview(business, language) {
  const res = await fetch("/api/email/preview", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      business,
      sender_business_name: getSenderBusinessName(),
      sender_business_info: getSenderInfo(),
      figma_prototype_link: getFigmaPrototypeLink(),
      language: language || "English",
      template_id: getEmailTemplateId(),
      logo_url: getEmailLogoUrl(),
    }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = data.detail;
    throw new Error(typeof detail === "string" ? detail : detail?.message || "Could not prepare email");
  }
  return data;
}

const EMAIL_LOGO_KEY = "chappie_email_logo_url";
const EMAIL_TEMPLATE_KEY = "chappie_email_template_id";
const PAID_PLANS = new Set(["small", "mid", "large"]);
const EMAIL_DESIGN_PLANS = PAID_PLANS;
let emailTemplatesCache = [];
let emailDesignRenderTimer = null;
let emailActiveTab = "message";

function isPaidPlan() {
  const plan = String(usageInfo?.plan || "").toLowerCase().trim();
  if (PAID_PLANS.has(plan)) return true;
  const name = String(usageInfo?.plan_name || "").toLowerCase();
  return ["small biz", "mid biz", "large biz"].some((n) => name.includes(n));
}

function canCustomizeEmailDesign() {
  return isPaidPlan();
}

function switchEmailTab(tab) {
  const next = tab === "design" ? "design" : "message";
  emailActiveTab = next;
  syncEmailDesignPlanGate();

  document.querySelectorAll(".email-tab").forEach((btn) => {
    const active = btn.dataset.emailTab === next;
    btn.classList.toggle("is-active", active);
    btn.setAttribute("aria-selected", active ? "true" : "false");
  });

  const messagePanel = document.getElementById("emailTabMessage");
  const designPanel = document.getElementById("emailTabDesign");
  if (messagePanel) {
    messagePanel.hidden = next !== "message";
    messagePanel.classList.toggle("is-active", next === "message");
  }
  if (designPanel) {
    designPanel.hidden = next !== "design";
    designPanel.classList.toggle("is-active", next === "design");
  }

  if (next === "design" && canCustomizeEmailDesign()) {
    scheduleEmailDesignRender();
  }
}

function syncEmailDesignPlanGate() {
  const allowed = canCustomizeEmailDesign();
  const lockBadge = document.getElementById("emailDesignLockBadge");
  const designBtn = document.getElementById("emailTabDesignBtn");
  const locked = document.getElementById("emailDesignLocked");
  const unlocked = document.getElementById("emailDesignUnlocked");

  if (lockBadge) lockBadge.hidden = allowed;
  if (designBtn) designBtn.classList.toggle("is-locked", !allowed);
  if (locked) locked.hidden = allowed;
  if (unlocked) unlocked.hidden = !allowed;

  if (!allowed) {
    // Free plan always sends the default template with no custom logo
    if (emailDraft) {
      emailDraft.template_id = "midnight_teal";
      emailDraft.logo_url = "";
    }
  }
}

function goUpgradeFromEmailDesign() {
  goToUpgradePlans();
}

function goToUpgradePlans() {
  closeEmailModal();
  closePipelineUpgradeModal();
  window.location.href = "/account#upgradePanel";
}

function syncSidebarPlanGates() {
  const allowed = isPaidPlan();
  const pairs = [
    ["integrationsLockBadge", "integrationsLocked", "integrationsUnlocked"],
    ["yourBusinessLockBadge", "yourBusinessLocked", "yourBusinessUnlocked"],
  ];
  for (const [badgeId, lockedId, unlockedId] of pairs) {
    const badge = document.getElementById(badgeId);
    const locked = document.getElementById(lockedId);
    const unlocked = document.getElementById(unlockedId);
    if (badge) badge.hidden = allowed;
    if (locked) locked.hidden = allowed;
    if (unlocked) unlocked.hidden = !allowed;
  }
}

function openPipelineUpgradeModal() {
  const modal = document.getElementById("pipelineUpgradeModal");
  if (!modal) {
    goToUpgradePlans();
    return;
  }
  if (window.ChappieA11y) {
    ChappieA11y.openDialog(modal);
  } else {
    modal.hidden = false;
    document.body.style.overflow = "hidden";
  }
}

function closePipelineUpgradeModal() {
  const modal = document.getElementById("pipelineUpgradeModal");
  if (!modal) return;
  if (window.ChappieA11y) {
    ChappieA11y.closeDialog(modal);
  } else {
    modal.hidden = true;
  }
  const emailOpen = document.getElementById("emailModal") && !document.getElementById("emailModal").hidden;
  if (!emailOpen) document.body.style.overflow = "";
}

function setupPipelinePlanGate() {
  const link = document.getElementById("pipelineLink");
  if (!link || link.dataset.planGateBound) return;
  link.dataset.planGateBound = "1";
  link.addEventListener("click", async (e) => {
    if (!usageInfo) await refreshUsage();
    if (isPaidPlan()) return;
    e.preventDefault();
    openPipelineUpgradeModal();
  });
}

function getEmailTemplateId() {
  if (!canCustomizeEmailDesign()) return "midnight_teal";
  return (
    emailDraft?.template_id
    || localStorage.getItem(EMAIL_TEMPLATE_KEY)
    || "midnight_teal"
  );
}

function getEmailLogoUrl() {
  if (!canCustomizeEmailDesign()) return "";
  const urlInput = document.getElementById("emailLogoUrl")?.value?.trim() || "";
  if (urlInput) return urlInput;
  return (
    emailDraft?.logo_url
    || localStorage.getItem(EMAIL_LOGO_KEY)
    || ""
  );
}

function setEmailDesignPreview(html) {
  const frame = document.getElementById("emailDesignPreviewFrame");
  if (!frame) return;
  frame.srcdoc = html || "<p style='padding:16px;font-family:sans-serif;color:#666'>No preview yet.</p>";
}

function renderEmailTemplateGrid(templates, activeId) {
  const grid = document.getElementById("emailTemplateGrid");
  if (!grid) return;
  const list = templates?.length ? templates : emailTemplatesCache;
  emailTemplatesCache = list;
  grid.innerHTML = list.map((t) => {
    const swatches = (t.swatches || [])
      .map((c) => `<span style="background:${esc(c)}"></span>`)
      .join("");
    const active = t.id === activeId ? " is-active" : "";
    return `
      <button type="button" class="email-template-card${active}" data-template-id="${t.id}">
        <strong>${esc(t.name)}</strong>
        <small>${esc(t.blurb || "")}</small>
        <div class="email-template-swatches">${swatches}</div>
      </button>
    `;
  }).join("");
  grid.querySelectorAll("[data-template-id]").forEach((btn) => {
    btn.addEventListener("click", () => selectEmailTemplate(btn.getAttribute("data-template-id")));
  });
  const label = document.getElementById("emailTemplateLabel");
  const active = list.find((t) => t.id === activeId);
  if (label) label.textContent = active?.name || activeId || "Template";
}

function selectEmailTemplate(templateId) {
  if (!emailDraft) return;
  if (!canCustomizeEmailDesign()) {
    showToast("Upgrade to Small Biz or higher to customize email design.");
    switchEmailTab("design");
    return;
  }
  emailDraft.template_id = templateId;
  localStorage.setItem(EMAIL_TEMPLATE_KEY, templateId);
  renderEmailTemplateGrid(emailTemplatesCache, templateId);
  scheduleEmailDesignRender();
}

function updateEmailLogoPreview(url) {
  const wrap = document.getElementById("emailLogoPreviewWrap");
  const img = document.getElementById("emailLogoPreviewImg");
  if (!wrap || !img) return;
  if (url) {
    img.src = url;
    wrap.hidden = false;
  } else {
    img.removeAttribute("src");
    wrap.hidden = true;
  }
}

function clearEmailLogo() {
  if (!canCustomizeEmailDesign()) return;
  const file = document.getElementById("emailLogoFile");
  const url = document.getElementById("emailLogoUrl");
  if (file) file.value = "";
  if (url) url.value = "";
  if (emailDraft) emailDraft.logo_url = "";
  localStorage.removeItem(EMAIL_LOGO_KEY);
  updateEmailLogoPreview("");
  scheduleEmailDesignRender();
}

function onEmailLogoFileChange(event) {
  if (!canCustomizeEmailDesign()) {
    showToast("Upgrade to Small Biz or higher to add a logo.");
    event.target.value = "";
    return;
  }
  const file = event.target?.files?.[0];
  if (!file) return;
  if (file.size > 400_000) {
    showToast("Logo should be under 400KB for reliable email delivery.");
    event.target.value = "";
    return;
  }
  const reader = new FileReader();
  reader.onload = () => {
    const dataUrl = String(reader.result || "");
    if (!dataUrl.startsWith("data:image/")) {
      showToast("Please choose an image file.");
      return;
    }
    if (emailDraft) emailDraft.logo_url = dataUrl;
    localStorage.setItem(EMAIL_LOGO_KEY, dataUrl);
    const urlInput = document.getElementById("emailLogoUrl");
    if (urlInput) urlInput.value = "";
    updateEmailLogoPreview(dataUrl);
    scheduleEmailDesignRender();
  };
  reader.readAsDataURL(file);
}

function onEmailLogoUrlChange() {
  if (!canCustomizeEmailDesign()) return;
  const url = document.getElementById("emailLogoUrl")?.value?.trim() || "";
  if (url && !/^https?:\/\//i.test(url)) {
    showToast("Logo URL must start with https://");
    return;
  }
  if (emailDraft) emailDraft.logo_url = url;
  if (url) localStorage.setItem(EMAIL_LOGO_KEY, url);
  else localStorage.removeItem(EMAIL_LOGO_KEY);
  const file = document.getElementById("emailLogoFile");
  if (file) file.value = "";
  updateEmailLogoPreview(url);
  scheduleEmailDesignRender();
}

function scheduleEmailDesignRender() {
  if (!emailDraft || !canCustomizeEmailDesign()) return;
  clearTimeout(emailDesignRenderTimer);
  emailDesignRenderTimer = setTimeout(() => {
    refreshEmailDesignPreview();
  }, 350);
}

async function refreshEmailDesignPreview() {
  if (!emailDraft || !canCustomizeEmailDesign()) return;
  const body = document.getElementById("emailPreviewBody")?.value || emailDraft.body || "";
  if (!body.trim()) return;
  try {
    const res = await fetch("/api/email/render", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        business_name: emailDraft.business?.name || "",
        sender_business_name: getSenderBusinessName(),
        sender_business_info: getSenderInfo(),
        figma_prototype_link: getFigmaPrototypeLink(),
        body,
        template_id: getEmailTemplateId(),
        logo_url: getEmailLogoUrl(),
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) return;
    emailDraft.html_body = data.html_body || "";
    emailDraft.template_id = data.template_id || getEmailTemplateId();
    setEmailDesignPreview(emailDraft.html_body);
  } catch (_) {}
}

async function sendEmail(index) {
  const business = filteredBusinesses[index];
  const emails = getBusinessEmails(business);
  if (!emails.length) {
    showToast("No email address for this business.");
    return;
  }

  if (!business.contact_email) {
    business.contact_email = emails[0];
  }

  const senderInfo = getSenderInfo();
  const senderBusinessName = getSenderBusinessName();
  if (!senderBusinessName) {
    showToast("Add your business name in the sidebar before sending.");
    return;
  }
  if (!senderInfo) {
    showToast("Add Your Business Info in the sidebar before sending.");
    return;
  }

  try {
    const statusRes = await fetch("/api/gmail/status", { credentials: "include" });
    const status = await statusRes.json();
    gmailConnected = !!status.connected;
    updateGmailUI(status);
  } catch (_) {}

  if (!gmailConnected) {
    showToast("Connect Gmail first — open Integrations and click Connect with Google.");
    return;
  }

  const btn = document.querySelector(`[data-send-email="${index}"]`);
  if (btn) {
    btn.disabled = true;
    btn.textContent = "Preparing...";
  }

  try {
    const language = getSelectedEmailLanguage();
    const data = await fetchEmailPreview(business, language);

    emailDraft = {
      index,
      business,
      recipients: data.recipients || emails,
      subject: data.subject || "",
      body: data.body || "",
      html_body: data.html_body || "",
      language: data.language || language,
      template_id: data.template_id || getEmailTemplateId(),
      logo_url: getEmailLogoUrl(),
      from_name: data.from_name || "",
      from_email: data.from_email || "",
    };
    if (data.templates?.length) emailTemplatesCache = data.templates;
    openEmailModal(emailDraft);
  } catch (e) {
    showToast(e.message);
  } finally {
    if (btn) {
      btn.disabled = !(gmailConnected && getBusinessEmails(business).length);
      btn.textContent = "Send Email" + (emails.length > 1 ? ` (${emails.length})` : "");
    }
  }
}

async function regenerateEmailLanguage() {
  if (!emailDraft?.business) return;
  const language = getSelectedEmailLanguage();
  if (!language) {
    showToast("Enter a language name.");
    return;
  }

  const btn = document.getElementById("emailLanguageApplyBtn");
  const sendBtn = document.getElementById("emailSendConfirmBtn");
  if (btn) {
    btn.disabled = true;
    btn.textContent = "Translating…";
  }
  if (sendBtn) sendBtn.disabled = true;

  try {
    const data = await fetchEmailPreview(emailDraft.business, language);
    emailDraft.subject = data.subject || "";
    emailDraft.body = data.body || "";
    emailDraft.html_body = data.html_body || "";
    emailDraft.language = data.language || language;
    emailDraft.template_id = data.template_id || emailDraft.template_id;
    emailDraft.recipients = data.recipients || emailDraft.recipients;
    if (data.templates?.length) emailTemplatesCache = data.templates;
    if (data.from_name) emailDraft.from_name = data.from_name;
    if (data.from_email) emailDraft.from_email = data.from_email;
    document.getElementById("emailPreviewSubject").value = emailDraft.subject;
    document.getElementById("emailPreviewBody").value = emailDraft.body;
    applyEmailTextDirection(emailDraft.language);
    if (canCustomizeEmailDesign()) {
      renderEmailTemplateGrid(emailTemplatesCache, emailDraft.template_id);
      setEmailDesignPreview(emailDraft.html_body);
    }
    const fromPreview = document.getElementById("emailFromPreview");
    if (fromPreview) {
      const name = emailDraft.from_name || "Your business";
      const email = emailDraft.from_email || "your Gmail";
      fromPreview.textContent = `From: ${name} <${email}> · Reply-To: ${email}`;
    }
    const hint = document.getElementById("emailPreviewHint");
    const base =
      emailDraft.recipients.length > 1
        ? `This email will be sent to ${emailDraft.recipients.length} addresses.`
        : "Review and edit the message, then click Send email.";
    hint.textContent = `${base} Language: ${emailDraft.language}.`;
    showToast(`Email rewritten in ${emailDraft.language}.`);
  } catch (e) {
    showToast(e.message);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = "Apply";
    }
    if (sendBtn) sendBtn.disabled = false;
  }
}

function openEmailModal(draft) {
  const modal = document.getElementById("emailModal");
  document.getElementById("emailModalSub").textContent =
    `Personalized for ${draft.business.name}`;
  document.getElementById("emailPreviewTo").value = draft.recipients.join(", ");
  document.getElementById("emailPreviewSubject").value = draft.subject;
  document.getElementById("emailPreviewBody").value = draft.body;

  const lang = draft.language || "English";
  const select = document.getElementById("emailLanguage");
  const custom = document.getElementById("emailLanguageCustom");
  if (select) {
    const match = [...select.options].find((o) => o.value === lang);
    if (match) {
      select.value = lang;
      if (custom) custom.value = "";
    } else {
      select.value = "__custom__";
      if (custom) custom.value = lang;
    }
  }
  syncEmailLanguageUi();
  applyEmailTextDirection(lang);

  syncEmailDesignPlanGate();
  switchEmailTab("message");

  const allowed = canCustomizeEmailDesign();
  const savedLogo = allowed
    ? (draft.logo_url || localStorage.getItem(EMAIL_LOGO_KEY) || "")
    : "";
  draft.logo_url = savedLogo;
  draft.template_id = allowed
    ? (draft.template_id || localStorage.getItem(EMAIL_TEMPLATE_KEY) || "midnight_teal")
    : "midnight_teal";
  const logoUrlInput = document.getElementById("emailLogoUrl");
  const logoFile = document.getElementById("emailLogoFile");
  if (logoFile) logoFile.value = "";
  if (logoUrlInput) {
    logoUrlInput.value = savedLogo.startsWith("http") ? savedLogo : "";
  }
  updateEmailLogoPreview(savedLogo);
  if (allowed) {
    renderEmailTemplateGrid(emailTemplatesCache, draft.template_id);
    setEmailDesignPreview(draft.html_body || "");
    if (!draft.html_body) scheduleEmailDesignRender();
  } else {
    setEmailDesignPreview("");
  }

  const trackEl = document.getElementById("emailTrackOpens");
  const unsubEl = document.getElementById("emailUnsubFooter");
  if (trackEl) trackEl.checked = false;
  if (unsubEl) unsubEl.checked = true;

  const fromPreview = document.getElementById("emailFromPreview");
  if (fromPreview) {
    const name = draft.from_name || "Your business";
    const email = draft.from_email || "your Gmail";
    fromPreview.textContent = `From: ${name} <${email}> · Reply-To: ${email}`;
  }

  const base =
    draft.recipients.length > 1
      ? `This email will be sent to ${draft.recipients.length} addresses.`
      : "Review and edit the message, then click Send email.";
  const langNote = lang && lang !== "English" ? ` Language: ${lang}.` : "";
  const designNote = allowed
    ? " Use the Design tab for templates and logo."
    : " Design customization unlocks on Small Biz, Mid Biz, and Large Biz.";
  document.getElementById("emailPreviewHint").textContent =
    `${base}${langNote}${designNote}`;
  document.getElementById("emailSendConfirmBtn").disabled = false;
  document.getElementById("emailSendConfirmBtn").textContent = "Send email";
  if (window.ChappieA11y) {
    ChappieA11y.openDialog(modal, { focusSelector: "#emailPreviewSubject" });
  } else {
    modal.hidden = false;
    document.body.style.overflow = "hidden";
  }
}

function closeEmailModal() {
  const modal = document.getElementById("emailModal");
  if (window.ChappieA11y) {
    ChappieA11y.closeDialog(modal);
  } else {
    modal.hidden = true;
    document.body.style.overflow = "";
  }
  clearTimeout(emailDesignRenderTimer);
  emailDraft = null;
}

async function confirmSendEmail() {
  if (!emailDraft) return;

  const subject = document.getElementById("emailPreviewSubject").value.trim();
  const body = document.getElementById("emailPreviewBody").value.trim();
  if (!subject || !body) {
    showToast("Subject and message are required.");
    return;
  }

  const btn = document.getElementById("emailSendConfirmBtn");
  btn.disabled = true;
  btn.textContent = "Sending...";

  try {
    const res = await fetch("/api/email/send", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        business: emailDraft.business,
        sender_business_name: getSenderBusinessName(),
        sender_business_info: getSenderInfo(),
        figma_prototype_link: getFigmaPrototypeLink(),
        subject,
        body,
        recipients: emailDraft.recipients,
        template_id: getEmailTemplateId(),
        logo_url: getEmailLogoUrl(),
        include_open_tracking: !!document.getElementById("emailTrackOpens")?.checked,
        include_unsubscribe_footer: document.getElementById("emailUnsubFooter")
          ? !!document.getElementById("emailUnsubFooter").checked
          : true,
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : data.detail?.message || "Send failed");

    const count = data.recipient_count || emailDraft.recipients.length;
    const base = count > 1 ? `Email sent to ${count} addresses!` : `Email sent to ${data.to}!`;
    const trackingNote = data.open_tracking ? " Open tracking on." : " Sent without open pixel.";
    showToast(`${base}${trackingNote} Tracking in Pipeline.`);
    if (data.deal?.place_id || emailDraft.business.place_id) {
      const pid = data.deal?.place_id || emailDraft.business.place_id;
      if (pid && data.deal) {
        dealByPlaceId[pid] = {
          id: data.deal.id,
          status: data.deal.status,
          status_label: data.deal.status_label,
          open_count: data.deal.open_count || 0,
        };
        renderView();
      }
    }
    closeEmailModal();
  } catch (e) {
    showToast(e.message);
    btn.disabled = false;
    btn.textContent = "Send email";
  }
}

document.getElementById("emailModal")?.addEventListener("click", (e) => {
  if (e.target.id === "emailModal") closeEmailModal();
});

document.getElementById("emailPreviewBody")?.addEventListener("input", scheduleEmailDesignRender);
document.getElementById("emailLogoFile")?.addEventListener("change", onEmailLogoFileChange);
document.getElementById("emailLogoUrl")?.addEventListener("change", onEmailLogoUrlChange);
document.getElementById("emailLogoUrl")?.addEventListener("blur", onEmailLogoUrlChange);

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    const createSiteModal = document.getElementById("createSiteModal");
    if (createSiteModal && !createSiteModal.hidden) {
      closeCreateSiteModal();
      return;
    }
    const campaignModal = document.getElementById("campaignModal");
    if (campaignModal && !campaignModal.hidden) {
      closeCampaignModal();
      return;
    }
    const pipelineModal = document.getElementById("pipelineUpgradeModal");
    if (pipelineModal && !pipelineModal.hidden) {
      closePipelineUpgradeModal();
      return;
    }
    if (document.getElementById("emailModal") && !document.getElementById("emailModal").hidden) {
      closeEmailModal();
      return;
    }
    const drawer = document.getElementById("drawer");
    if (drawer && drawer.classList.contains("open")) {
      closeDrawer();
    }
  }
});

function updateActionButtons() {
  document.querySelectorAll(".btn-create-site").forEach((el) => {
    el.disabled = !figmaConnected;
  });
  document.querySelectorAll(".btn-send-email").forEach((el) => {
    el.disabled = !gmailConnected;
  });
  document.querySelectorAll(".btn-campaign").forEach((el) => {
    el.disabled = !openaiConnected;
  });
}

async function checkFigmaStatus() {
  try {
    const res = await fetch("/api/figma/status", { credentials: "include" });
    if (res.status === 401) {
      updateFigmaUI({ connected: false });
      return;
    }
    const data = await res.json();
    figmaConnected = data.connected;
    updateFigmaUI(data);
  } catch {
    updateFigmaUI({ connected: false });
  }
}

function updateFigmaUI(data) {
  const dot = document.getElementById("figmaDot");
  const label = document.getElementById("figmaLabel");
  const btn = document.getElementById("figmaConnectBtn");

  if (data.connected) {
    dot.classList.add("connected");
    label.textContent = data.email ? `Connected as ${data.email}` : "Connected";
    btn.textContent = "Disconnect";
    btn.onclick = disconnectFigma;
  } else {
    dot.classList.remove("connected");
    label.textContent = "Not connected";
    btn.textContent = "Connect";
    btn.onclick = connectFigma;
  }

  updateActionButtons();
}

async function connectFigma() {
  const token = document.getElementById("figmaToken").value.trim();
  if (!token) {
    showToast("Enter your Figma API token first.");
    return;
  }

  const btn = document.getElementById("figmaConnectBtn");
  btn.disabled = true;
  btn.textContent = "Connecting...";

  try {
    const res = await fetch("/api/figma/connect", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Connection failed");

    figmaConnected = true;
    updateFigmaUI(data);
    showToast("Figma connected successfully!");
    renderView();
  } catch (e) {
    showToast(e.message);
    updateFigmaUI({ connected: false });
  } finally {
    btn.disabled = false;
  }
}

async function disconnectFigma() {
  await fetch("/api/figma/disconnect", { method: "POST", credentials: "include" });
  localStorage.removeItem(storageKey(STORAGE_KEYS.figmaToken));
  document.getElementById("figmaToken").value = "";
  figmaConnected = false;
  updateFigmaUI({ connected: false });
  renderView();
  showToast("Figma disconnected.");
}

async function checkOpenAIStatus() {
  try {
    const res = await fetch("/api/openai/status", { credentials: "include" });
    if (res.status === 401) {
      updateOpenAIUI({ connected: false });
      return;
    }
    const data = await res.json();
    openaiConnected = data.connected;
    updateOpenAIUI(data);
  } catch {
    updateOpenAIUI({ connected: false });
  }
}

function updateOpenAIUI(data) {
  const dot = document.getElementById("openaiDot");
  const label = document.getElementById("openaiLabel");
  const btn = document.getElementById("openaiConnectBtn");

  if (data.connected) {
    dot.classList.add("connected");
    label.textContent = data.model ? `Connected (${data.model})` : "Connected";
    btn.textContent = "Disconnect";
    btn.onclick = disconnectOpenAI;
  } else {
    dot.classList.remove("connected");
    label.textContent = "Not connected";
    btn.textContent = "Connect";
    btn.onclick = connectOpenAI;
  }
}

async function autoConnectOpenAI(apiKey) {
  try {
    const res = await fetch("/api/openai/connect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ api_key: apiKey }),
    });
    if (res.ok) {
      const data = await res.json();
      openaiConnected = true;
      updateOpenAIUI(data);
    }
  } catch {
    updateOpenAIUI({ connected: false });
  }
}

async function connectOpenAI() {
  const apiKey = document.getElementById("openaiApiKey").value.trim();
  if (!apiKey) {
    showToast("Enter your OpenAI API key first.");
    return;
  }

  const btn = document.getElementById("openaiConnectBtn");
  btn.disabled = true;
  btn.textContent = "Connecting...";

  try {
    const res = await fetch("/api/openai/connect", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ api_key: apiKey }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Connection failed");

    openaiConnected = true;
    updateOpenAIUI(data);
    showToast("OpenAI connected — will fill missing details on search.");
  } catch (e) {
    showToast(e.message);
    updateOpenAIUI({ connected: false });
  } finally {
    btn.disabled = false;
  }
}

async function disconnectOpenAI() {
  await fetch("/api/openai/disconnect", { method: "POST", credentials: "include" });
  localStorage.removeItem(storageKey(STORAGE_KEYS.openaiApiKey));
  document.getElementById("openaiApiKey").value = "";
  openaiConnected = false;
  updateOpenAIUI({ connected: false });
  showToast("OpenAI disconnected.");
}

async function createSite(index) {
  if (!figmaConnected) {
    showToast("Connect your Figma API token first.");
    return;
  }

  const business = filteredBusinesses[index];
  if (!business) return;

  openCreateSiteModal(index, business);
}

let campaignDraft = null; // { index, business }
let campaignResult = null;

function isLargePlan() {
  return String(usageInfo?.plan || "").toLowerCase() === "large";
}

function createCampaign(index) {
  if (!isLargePlan()) {
    showToast("Campaigns are available on Large Biz only.");
    return;
  }
  if (!openaiConnected) {
    showToast("Connect OpenAI in Integrations first.");
    return;
  }
  const business = filteredBusinesses[index];
  if (!business) return;
  openCampaignModal(index, business);
}

function openCampaignModal(index, business) {
  campaignDraft = { index, business };
  campaignResult = null;
  const modal = document.getElementById("campaignModal");
  const sub = document.getElementById("campaignModalSub");
  if (sub) sub.textContent = `Campaign for ${business.name}`;
  document.getElementById("campaignSetup").hidden = false;
  document.getElementById("campaignLoading").hidden = true;
  document.getElementById("campaignResult").hidden = true;
  document.getElementById("campaignResult").innerHTML = "";
  const genBtn = document.getElementById("campaignGenerateBtn");
  if (genBtn) {
    genBtn.hidden = false;
    genBtn.disabled = false;
    genBtn.textContent = "Generate campaign";
  }
  const bar = document.getElementById("campaignProgressBar");
  if (bar) bar.style.width = "18%";
  if (modal) {
    if (window.ChappieA11y) {
      ChappieA11y.openDialog(modal, { focusSelector: "#campaignGenerateBtn" });
    } else {
      modal.hidden = false;
      document.body.style.overflow = "hidden";
    }
  }
}

function closeCampaignModal() {
  const modal = document.getElementById("campaignModal");
  if (window.ChappieA11y) {
    ChappieA11y.closeDialog(modal);
  } else if (modal) {
    modal.hidden = true;
  }
  campaignDraft = null;
  const emailOpen = document.getElementById("emailModal") && !document.getElementById("emailModal").hidden;
  const pipelineOpen = document.getElementById("pipelineUpgradeModal") && !document.getElementById("pipelineUpgradeModal").hidden;
  if (!emailOpen && !pipelineOpen) document.body.style.overflow = "";
}

function copyCampaignText(text) {
  const value = text || "";
  if (!value) return;
  navigator.clipboard?.writeText(value).then(
    () => showToast("Copied"),
    () => showToast("Could not copy"),
  );
}

function renderCampaignResult(data) {
  const goalLabel = ({
    awareness: "Awareness",
    leads: "Leads",
    engagement: "Engagement",
  })[data.goal] || data.goal || "Campaign";

    const postsHtml = (data.static_posts || []).map((p) => {
    const tags = (p.hashtags || []).map((t) => `#${esc(String(t).replace(/^#/, ""))}`).join(" ");
    const img = p.image_url
      ? `<a href="${esc(p.image_url)}" target="_blank" rel="noopener"><img class="campaign-post-image" src="${esc(p.image_url)}" alt="Post ${p.id} visual"></a>`
      : `<div class="campaign-post-image-missing">${esc(p.image_error || "Image unavailable")}</div>`;
    const copyPayload = [p.caption, tags].filter(Boolean).join("\n\n");
    return `
      <article class="campaign-post">
        <h4>Post ${p.id}: ${esc(p.title || "")}</h4>
        <div class="campaign-post-grid">
          ${img}
          <div>
            <div class="campaign-caption">${esc(p.caption || "")}</div>
            ${p.cta ? `<div><strong>CTA:</strong> ${esc(p.cta)}</div>` : ""}
            ${tags ? `<div class="campaign-tags">${tags}</div>` : ""}
            <button type="button" class="btn btn-secondary campaign-copy-btn" data-copy="${esc(encodeURIComponent(copyPayload))}" onclick="copyCampaignText(decodeURIComponent(this.dataset.copy || ''))">Copy caption</button>
          </div>
        </div>
      </article>
    `;
  }).join("");

  const reelsHtml = (data.reels || []).map((r) => {
    const overlays = (r.on_screen_text || []).map((t) => esc(t)).join(" · ");
    const copyPayload = [
      r.hook ? `Hook: ${r.hook}` : "",
      r.script || "",
      overlays ? `On-screen: ${(r.on_screen_text || []).join(" · ")}` : "",
      r.cta ? `CTA: ${r.cta}` : "",
    ].filter(Boolean).join("\n\n");
    return `
      <article class="campaign-reel">
        <h4>Reel ${r.id}: ${esc(r.title || "")} · ${Number(r.duration_sec || 30)}s</h4>
        ${r.hook ? `<p><strong>Hook:</strong> ${esc(r.hook)}</p>` : ""}
        <div class="campaign-caption">${esc(r.script || "")}</div>
        ${overlays ? `<p><strong>On-screen:</strong> ${overlays}</p>` : ""}
        ${r.cta ? `<p><strong>CTA:</strong> ${esc(r.cta)}</p>` : ""}
        <button type="button" class="btn btn-secondary campaign-copy-btn" data-copy="${esc(encodeURIComponent(copyPayload))}" onclick="copyCampaignText(decodeURIComponent(this.dataset.copy || ''))">Copy reel script</button>
      </article>
    `;
  }).join("");

  return `
    <div class="campaign-concept">
      <div class="campaign-meta">
        <span class="campaign-pill">${esc(goalLabel)}</span>
        <span class="campaign-pill">Large Biz</span>
      </div>
      <h3>${esc(data.concept_title || "Campaign")}</h3>
      <p>${esc(data.concept_summary || "")}</p>
      ${data.hook ? `<p><strong>Hook:</strong> ${esc(data.hook)}</p>` : ""}
      ${data.primary_cta ? `<p><strong>Primary CTA:</strong> ${esc(data.primary_cta)}</p>` : ""}
      ${data.why_it_works ? `<p><strong>Why it works:</strong> ${esc(data.why_it_works)}</p>` : ""}
      ${data.brand_notes ? `<p><strong>Brand:</strong> ${esc(data.brand_notes)}</p>` : ""}
    </div>
    <div class="campaign-section-title">5 static posts · images without text</div>
    ${postsHtml || "<p class='muted'>No posts generated.</p>"}
    <div class="campaign-section-title">5 reels · scripts</div>
    ${reelsHtml || "<p class='muted'>No reels generated.</p>"}
  `;
}

async function confirmGenerateCampaign() {
  if (!campaignDraft?.business) return;
  if (!isLargePlan()) {
    showToast("Campaigns are available on Large Biz only.");
    return;
  }
  if (!openaiConnected) {
    showToast("Connect OpenAI in Integrations first.");
    return;
  }

  const goal = document.getElementById("campaignGoal")?.value || "auto";
  const setup = document.getElementById("campaignSetup");
  const loading = document.getElementById("campaignLoading");
  const resultEl = document.getElementById("campaignResult");
  const genBtn = document.getElementById("campaignGenerateBtn");
  const msg = document.getElementById("campaignLoadingMsg");
  const bar = document.getElementById("campaignProgressBar");

  if (setup) setup.hidden = true;
  if (loading) loading.hidden = false;
  if (resultEl) {
    resultEl.hidden = true;
    resultEl.innerHTML = "";
  }
  if (genBtn) {
    genBtn.disabled = true;
    genBtn.textContent = "Generating…";
  }
  if (msg) msg.textContent = "Writing campaign concept from brand + business data…";
  if (bar) bar.style.width = "28%";

  const progressTimer = setInterval(() => {
    if (!bar) return;
    const current = parseFloat(bar.style.width) || 28;
    if (current < 88) bar.style.width = `${current + 4}%`;
  }, 1200);

  try {
    // Drop heavy brand SVG from payload; server rebuilds brand book if needed
    const { brand_book, ...rest } = campaignDraft.business || {};
    const businessPayload = brand_book
      ? { ...rest, brand_book: { ...brand_book, logo_svg: undefined } }
      : rest;

    if (msg) msg.textContent = "Creating 5 post captions, 5 reel scripts, and 5 text-free images…";
    const res = await fetch("/api/campaign/generate", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ business: businessPayload, goal }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(typeof data.detail === "string" ? data.detail : "Campaign generation failed");
    }
    campaignResult = data;
    if (bar) bar.style.width = "100%";
    if (loading) loading.hidden = true;
    if (resultEl) {
      resultEl.hidden = false;
      resultEl.innerHTML = renderCampaignResult(data);
    }
    if (genBtn) {
      genBtn.hidden = true;
    }
    showToast("Campaign ready.");
  } catch (e) {
    if (loading) loading.hidden = true;
    if (setup) setup.hidden = false;
    if (genBtn) {
      genBtn.disabled = false;
      genBtn.textContent = "Generate campaign";
      genBtn.hidden = false;
    }
    showToast(e.message || "Campaign generation failed");
  } finally {
    clearInterval(progressTimer);
  }
}

let createSiteDraft = null; // { index, business }

function getSelectedSiteLanguage() {
  const select = document.getElementById("siteLanguage");
  if (!select) return "English";
  if (select.value === "__custom__") {
    return (document.getElementById("siteLanguageCustom")?.value || "").trim() || "English";
  }
  return select.value || "English";
}

function syncSiteLanguageUi() {
  const select = document.getElementById("siteLanguage");
  const custom = document.getElementById("siteLanguageCustom");
  if (!select || !custom) return;
  const isCustom = select.value === "__custom__";
  custom.hidden = !isCustom;
  if (isCustom) custom.focus();
}

function openCreateSiteModal(index, business) {
  createSiteDraft = { index, business };
  const modal = document.getElementById("createSiteModal");
  const sub = document.getElementById("createSiteModalSub");
  if (sub) {
    sub.textContent = `Language for ${business.name}'s website copy`;
  }
  const saved = localStorage.getItem(storageKey("site_language"));
  const select = document.getElementById("siteLanguage");
  const custom = document.getElementById("siteLanguageCustom");
  if (select && saved) {
    const optionExists = [...select.options].some((o) => o.value === saved);
    if (optionExists) {
      select.value = saved;
      if (custom) custom.hidden = true;
    } else {
      select.value = "__custom__";
      if (custom) {
        custom.hidden = false;
        custom.value = saved;
      }
    }
  }
  syncSiteLanguageUi();
  if (modal) {
    if (window.ChappieA11y) {
      ChappieA11y.openDialog(modal, { focusSelector: "#siteLanguage" });
    } else {
      modal.hidden = false;
    }
  }
}

function closeCreateSiteModal() {
  const modal = document.getElementById("createSiteModal");
  if (window.ChappieA11y) {
    ChappieA11y.closeDialog(modal);
  } else if (modal) {
    modal.hidden = true;
  }
  createSiteDraft = null;
}

async function confirmCreateSite() {
  if (!createSiteDraft) return;
  if (!figmaConnected) {
    showToast("Connect your Figma API token first.");
    return;
  }

  const { index, business } = createSiteDraft;
  const language = getSelectedSiteLanguage();
  localStorage.setItem(storageKey("site_language"), language);

  // Open immediately while we still have the user-gesture (async fetch would get popup-blocked).
  const makeTab = window.open("about:blank", "_blank");
  if (makeTab) {
    try {
      makeTab.document.write(
        "<!doctype html><title>Opening Figma Make…</title>" +
          "<body style=\"font-family:system-ui;background:#0f1219;color:#f0f2f5;display:grid;place-items:center;height:100vh;margin:0\">" +
          "<p>Preparing your Figma Make brief…</p></body>"
      );
      makeTab.document.close();
    } catch (_) {
      /* cross-origin / restricted about:blank — ignore */
    }
  }

  const modal = document.getElementById("createSiteModal");
  if (modal) modal.hidden = true;
  createSiteDraft = null;

  const btn = document.querySelector(`[data-create-site="${index}"]`);
  const confirmBtn = document.getElementById("createSiteConfirmBtn");
  if (btn) {
    btn.disabled = true;
    btn.textContent = "Crafting unique brief...";
  }
  if (confirmBtn) {
    confirmBtn.disabled = true;
    confirmBtn.textContent = "Opening...";
  }

  try {
    const res = await fetch("/api/figma/create-site", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ business, language }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Failed to create site");

    if (!data.make_url) throw new Error("No Figma Make URL returned");

    if (makeTab && !makeTab.closed) {
      makeTab.location.href = data.make_url;
    } else {
      // Popup blocked — last resort navigation still may be blocked; offer copy path
      const opened = window.open(data.make_url, "_blank");
      if (!opened) {
        try {
          await navigator.clipboard.writeText(data.make_url);
          showToast("Popup blocked — Figma Make link copied. Paste it in a new tab.");
          return;
        } catch (_) {
          showToast("Popup blocked — allow popups for this site, then try again.");
          return;
        }
      }
    }

    const mode = data.variant && data.variant.media_mode ? data.variant.media_mode : "hybrid";
    const photos = data.photo_count || 0;
    const langLabel = data.language || language;
    showToast(
      photos > 0
        ? `Figma Make (${mode}, ${langLabel}): ${business.name} — ${photos} Google photos`
        : `Figma Make (${mode}, ${langLabel}): unique brief for ${business.name}`
    );
  } catch (e) {
    if (makeTab && !makeTab.closed) makeTab.close();
    showToast(e.message);
  } finally {
    if (btn) {
      btn.disabled = !figmaConnected;
      btn.textContent = "Create Site";
    }
    if (confirmBtn) {
      confirmBtn.disabled = false;
      confirmBtn.textContent = "Open Figma Make";
    }
  }
}

function setupRadiusSlider() {
  const slider = document.getElementById("radiusSlider");
  const display = document.getElementById("radiusDisplay");
  slider.addEventListener("input", () => {
    display.textContent = `${slider.value} km`;
    document.getElementById("radius").value = slider.value;
    slider.setAttribute("aria-valuenow", slider.value);
  });
}

// ── Search ──
async function refreshUsage() {
  try {
    const res = await fetch("/api/account/usage", { credentials: "include" });
    if (res.status === 401) {
      window.location.href = "/?signin=1";
      return;
    }
    if (!res.ok) return;
    usageInfo = await res.json();
    applyUsageToUi();
  } catch {
    /* ignore */
  }
}

function applyUsageToUi() {
  if (!usageInfo) return;
  const label = document.getElementById("planLabel");
  if (label) {
    label.textContent = `${usageInfo.plan_name} · ${usageInfo.searches_used}/${usageInfo.max_searches} searches · $${Number(usageInfo.credit_balance_cad || 0).toFixed(2)} left`;
  }
  syncSidebarPlanGates();
  setupPipelinePlanGate();
  if (allBusinesses.length) renderView();
  const btn = document.getElementById("searchBtn");
  const btnText = document.getElementById("searchBtnText");
  if (!usageInfo.can_search) {
    // Keep clickable so users can go upgrade / contact
    btn.disabled = false;
    btn.classList.add("btn-budget-exhausted");
    btnText.textContent = usageInfo.exhausted_action === "contact"
      ? "Budget used — contact IS Studio"
      : "Budget used — upgrade plan";
    btn.onclick = () => showUpgradePrompt();
  } else {
    btn.disabled = false;
    btn.classList.remove("btn-budget-exhausted");
    btnText.textContent = "Start Search";
    btn.onclick = () => startSearch();
  }
}

function showUpgradePrompt() {
  if (!usageInfo) {
    window.location.href = "/account#upgradePanel";
    return;
  }
  if (usageInfo.exhausted_action === "contact") {
    const email = usageInfo.studio_email || "hello@isexperience.house";
    showToast(`Opening account — or email ${email}`);
    setTimeout(() => { window.location.href = "/account#upgradePanel"; }, 500);
    return;
  }
  window.location.href = "/account#upgradePanel";
}

async function startSearch() {
  if (usageInfo && !usageInfo.can_search) {
    showUpgradePrompt();
    return;
  }

  const address = document.getElementById("address").value.trim();
  const radius = parseFloat(document.getElementById("radius").value);

  if (!address) {
    showToast("Please enter an address.");
    return;
  }

  hideToast();
  setSearching(true);
  showProgress(0, "Starting search...");
  clearSavedSearchResults();
  allBusinesses = [];
  filteredBusinesses = [];
  clearAdvancedFilters();
  document.getElementById("filtersToggleBtn")?.classList.remove("active");
  const filtersPanel = document.getElementById("advancedFilters");
  if (filtersPanel) filtersPanel.hidden = true;

  document.getElementById("emptyState").style.display = "none";
  document.getElementById("resultsToolbar").classList.remove("active");
  document.getElementById("statsRow").classList.remove("active");
  document.getElementById("resultsBody").classList.remove("active");
  closeDrawer();

  try {
    const res = await fetch("/api/search", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        address,
        radius_km: radius,
        enrich_details: true,
        find_emails: true,
        max_results: usageInfo?.max_results || 50,
        target_categories: getTargetCategories(),
      }),
    });

    if (res.status === 401) {
      window.location.href = "/?signin=1";
      return;
    }

    if (!res.ok) {
      const err = await res.json();
      const detail = err.detail;
      if (res.status === 402) {
        usageInfo = {
          ...(usageInfo || {}),
          can_search: false,
          exhausted_action: detail?.exhausted_action || "upgrade",
          plan_name: usageInfo?.plan_name,
          studio_email: usageInfo?.studio_email,
          studio_name: usageInfo?.studio_name,
        };
        applyUsageToUi();
        showUpgradePrompt();
        throw new Error(typeof detail === "string" ? detail : detail?.message || "Search budget used");
      }
      throw new Error(typeof detail === "string" ? detail : detail?.message || "Search failed");
    }

    const data = await res.json();
    currentJobId = data.job_id;
    saveActiveJobId(data.job_id);
    if (data.user) {
      usageInfo = {
        ...(usageInfo || {}),
        ...data.user,
        plan_name: data.user.plan_name,
        searches_used: data.user.searches_used,
        max_searches: data.user.max_searches,
        credit_balance_cad: data.user.credit_balance_cad,
        can_search: data.user.can_search,
        exhausted_action: data.user.exhausted_action,
        max_results: data.user.max_results,
      };
      applyUsageToUi();
    } else {
      refreshUsage();
    }
    if (pollInterval) clearInterval(pollInterval);
    pollInterval = setInterval(pollStatus, 1500);
  } catch (e) {
    showToast(e.message);
    setSearching(false);
    hideProgress();
    document.getElementById("emptyState").style.display = "flex";
  }
}

async function pollStatus() {
  if (!currentJobId) return;

  try {
    const res = await fetch(`/api/search/${currentJobId}`, { credentials: "include" });
    if (res.status === 401) {
      clearInterval(pollInterval);
      saveActiveJobId(currentJobId);
      showToast("Session expired — sign in again to see your search.");
      return;
    }
    if (res.status === 404) {
      clearInterval(pollInterval);
      setSearching(false);
      hideProgress();
      saveActiveJobId(null);
      showToast("Search job not found.");
      return;
    }
    const job = await res.json();

    if (job.status === "running" || job.status === "pending") {
      const pct = job.total > 0 ? Math.round((job.progress / job.total) * 100) : 5;
      showProgress(pct, job.message);
    } else if (job.status === "completed") {
      clearInterval(pollInterval);
      setSearching(false);
      hideProgress();
      saveActiveJobId(null);
      renderResults(job.result);
    } else if (job.status === "failed") {
      clearInterval(pollInterval);
      setSearching(false);
      hideProgress();
      saveActiveJobId(null);
      showToast(job.error || "Search failed");
      document.getElementById("emptyState").style.display = "flex";
    }
  } catch {
    // Keep polling — transient network blips shouldn't kill a server-side search
  }
}

function setSearching(active) {
  const btn = document.getElementById("searchBtn");
  const btnText = document.getElementById("searchBtnText");
  if (active) {
    btn.disabled = true;
    btnText.textContent = "Searching...";
  } else {
    applyUsageToUi();
  }
}

function showProgress(pct, message) {
  document.getElementById("progressPanel").classList.add("active");
  document.getElementById("progressBar").style.width = `${pct}%`;
  document.getElementById("progressMessage").textContent = message;
  document.getElementById("progressPct").textContent = `${pct}%`;
}

function hideProgress() {
  document.getElementById("progressPanel").classList.remove("active");
}

// ── Results ──
function renderResults(result) {
  allBusinesses = result.businesses;
  filteredBusinesses = [...allBusinesses];
  saveSearchResults(result);

  document.getElementById("emptyState").style.display = "none";
  document.getElementById("resultsToolbar").classList.add("active");
  document.getElementById("statsRow").classList.add("active");
  document.getElementById("resultsBody").classList.add("active");

  const withPhone = allBusinesses.filter((b) => b.phone).length;
  const withEmail = allBusinesses.filter((b) => getBusinessEmails(b).length).length;
  const hotOpp = allBusinesses.filter((b) => (b.website_opportunity_score || 0) >= 75).length;

  document.getElementById("statsRow").innerHTML = `
    <div class="stat-card">
      <div class="stat-label">Found</div>
      <div class="stat-value">${result.total_found}</div>
      <div class="stat-sub">Leads in this search</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Hot opportunities</div>
      <div class="stat-value">${hotOpp}</div>
      <div class="stat-sub">Website opportunity 75+</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Emails Found</div>
      <div class="stat-value">${withEmail}</div>
      <div class="stat-sub">${Math.round((withEmail / (result.total_found || 1)) * 100)}% have contact email · ${withPhone} phone</div>
    </div>
  `;

  // Default sort: closest to search center first
  const sortSelect = document.getElementById("sortSelect");
  if (sortSelect && !sortSelect.dataset.scoreDefaulted) {
    sortSelect.value = "distance";
    sortSelect.dataset.scoreDefaulted = "1";
  }
  applyFilter();
  initMap(result.center_lat, result.center_lng, result.radius_km);
  loadPipelineBadges(allBusinesses);
  loadFavoriteBadges(allBusinesses);
}

async function loadFavoriteBadges(businesses) {
  const placeIds = businesses.map((b) => b.place_id).filter(Boolean);
  if (!placeIds.length) {
    favoritePlaceIds = {};
    return;
  }
  try {
    const res = await fetch("/api/favorites/lookup", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(placeIds),
    });
    if (!res.ok) return;
    const data = await res.json();
    favoritePlaceIds = data.favorites || {};
    renderView();
  } catch {
    /* ignore */
  }
}

async function toggleFavorite(index, event) {
  if (event) event.stopPropagation();
  const business = filteredBusinesses[index];
  if (!business) return;

  const placeId = business.place_id;
  const key = placeId || `name:${(business.name || "").trim().toLowerCase()}|addr:${(business.address || "").trim().toLowerCase()}`;
  const isFav = !!favoritePlaceIds[key];
  const btn = document.querySelector(`[data-favorite="${index}"]`);
  if (btn) btn.disabled = true;

  try {
    if (isFav) {
      const path = placeId
        ? `/api/favorites/by-place/${encodeURIComponent(placeId)}`
        : null;
      if (!path) {
        // Fallback: reload favorites list and remove by matching name if needed
        throw new Error("Open Favorites to remove this saved business.");
      }
      const res = await fetch(path, {
        method: "DELETE",
        credentials: "include",
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(typeof data.detail === "string" ? data.detail : "Could not remove favorite");
      }
      delete favoritePlaceIds[key];
      showToast("Removed from favorites");
    } else {
      const res = await fetch("/api/favorites", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ business }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(typeof data.detail === "string" ? data.detail : "Could not save favorite");
      }
      favoritePlaceIds[key] = true;
      showToast("Saved to favorites");
    }
    renderView();
  } catch (e) {
    showToast(e.message);
  } finally {
    if (btn) btn.disabled = false;
  }
}

function favoriteButton(b, index) {
  const key = b.place_id || `name:${(b.name || "").trim().toLowerCase()}|addr:${(b.address || "").trim().toLowerCase()}`;
  const isFav = !!favoritePlaceIds[key];
  const label = isFav ? "Remove from favorites" : "Save to favorites";
  return `
    <button
      type="button"
      class="btn-favorite ${isFav ? "is-favorite" : ""}"
      data-favorite="${index}"
      title="${label}"
      aria-label="${label}"
      aria-pressed="${isFav ? "true" : "false"}"
      onclick="event.stopPropagation(); toggleFavorite(${index}, event)"
    >${isFav ? "★" : "☆"}</button>
  `;
}

async function loadPipelineBadges(businesses) {
  const placeIds = businesses.map((b) => b.place_id).filter(Boolean);
  if (!placeIds.length) {
    dealByPlaceId = {};
    return;
  }
  try {
    const res = await fetch("/api/pipeline/lookup", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(placeIds),
    });
    if (!res.ok) return;
    const data = await res.json();
    dealByPlaceId = data.deals || {};
    renderView();
  } catch {
    /* ignore */
  }
}

function pipelineBadge(b) {
  const deal = b.place_id ? dealByPlaceId[b.place_id] : null;
  if (!deal) return "";
  const st = deal.status || "sent";
  const cls = `badge badge-pipeline badge-pipeline-${st}`;
  const opens = deal.open_count ? ` · ${deal.open_count} open` : "";
  return `<span class="${cls}" title="Outreach pipeline">${esc(deal.status_label)}${opens}</span>`;
}

function toggleAdvancedFilters() {
  const panel = document.getElementById("advancedFilters");
  if (!panel) return;
  const open = panel.classList.toggle("open");
  panel.hidden = !open;
  document.getElementById("filtersToggleBtn")?.classList.toggle("active", open);
}

function clearAdvancedFilters() {
  const panel = document.getElementById("advancedFilters");
  if (!panel) return;
  panel.querySelectorAll('input[type="checkbox"]').forEach((el) => { el.checked = false; });
  ["filterCategory", "filterCity", "filterMinRating", "filterMinReviews", "filterOppMin", "filterOppMax"]
    .forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.value = "";
    });
  applyFilter();
}

function getAdvancedFilterState() {
  const panel = document.getElementById("advancedFilters");
  const flags = [];
  panel?.querySelectorAll('input[data-flag]').forEach((el) => {
    if (el.checked) flags.push(el.dataset.flag);
  });
  const num = (id) => {
    const raw = document.getElementById(id)?.value;
    if (raw == null || String(raw).trim() === "") return null;
    const n = Number(raw);
    return Number.isFinite(n) ? n : null;
  };
  return {
    flags,
    category: (document.getElementById("filterCategory")?.value || "").trim().toLowerCase(),
    city: (document.getElementById("filterCity")?.value || "").trim().toLowerCase(),
    minRating: num("filterMinRating"),
    minReviews: num("filterMinReviews"),
    oppMin: num("filterOppMin"),
    oppMax: num("filterOppMax"),
  };
}

function countActiveFilters(state) {
  let n = state.flags.length;
  if (state.category) n += 1;
  if (state.city) n += 1;
  if (state.minRating != null) n += 1;
  if (state.minReviews != null) n += 1;
  if (state.oppMin != null) n += 1;
  if (state.oppMax != null) n += 1;
  return n;
}

function updateFiltersActiveBadge() {
  const badge = document.getElementById("filtersActiveCount");
  if (!badge) return;
  const n = countActiveFilters(getAdvancedFilterState());
  if (n > 0) {
    badge.hidden = false;
    badge.textContent = String(n);
  } else {
    badge.hidden = true;
    badge.textContent = "";
  }
}

function businessMatchesAdvancedFilters(b, state) {
  const flags = b.website_flags || {};

  for (const key of state.flags) {
    if (key === "no_website") {
      const noSite = flags.no_website
        || (!b.has_website && !b.website_url)
        || b.no_website_status === "verified_none"
        || b.no_website_status === "social_only";
      if (!noSite) return false;
      continue;
    }
    if (!flags[key]) return false;
  }

  if (state.category) {
    const blob = [b.category, ...(b.categories || []), b.name]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();
    if (!blob.includes(state.category)) return false;
  }

  if (state.city) {
    const addr = (b.address || "").toLowerCase();
    if (!addr.includes(state.city)) return false;
  }

  if (state.minRating != null) {
    if (b.rating == null || Number(b.rating) < state.minRating) return false;
  }

  if (state.minReviews != null) {
    if ((b.review_count || 0) < state.minReviews) return false;
  }

  const opp = b.website_opportunity_score;
  if (state.oppMin != null) {
    if (opp == null || opp < state.oppMin) return false;
  }
  if (state.oppMax != null) {
    if (opp == null || opp > state.oppMax) return false;
  }

  return true;
}

function applyFilter() {
  const query = document.getElementById("filterInput").value.toLowerCase().trim();
  const sort = document.getElementById("sortSelect").value;
  const advanced = getAdvancedFilterState();
  updateFiltersActiveBadge();

  filteredBusinesses = allBusinesses.filter((b) => {
    if (!businessMatchesAdvancedFilters(b, advanced)) return false;
    if (!query) return true;
    const haystack = [b.name, b.category, b.address, b.phone, b.province]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();
    return haystack.includes(query);
  });

  filteredBusinesses.sort((a, b) => {
    switch (sort) {
      case "distance": {
        const da = a.distance_km == null ? Number.POSITIVE_INFINITY : a.distance_km;
        const db = b.distance_km == null ? Number.POSITIVE_INFINITY : b.distance_km;
        return da - db;
      }
      case "opportunity": return (b.website_opportunity_score || 0) - (a.website_opportunity_score || 0);
      case "quality": return (b.lead_quality_score || 0) - (a.lead_quality_score || 0);
      case "score": return (b.no_website_score || 0) - (a.no_website_score || 0);
      case "rating": return (b.rating || 0) - (a.rating || 0);
      case "reviews": return (b.review_count || 0) - (a.review_count || 0);
      case "name": return (a.name || "").localeCompare(b.name || "");
      default: {
        const da = a.distance_km == null ? Number.POSITIVE_INFINITY : a.distance_km;
        const db = b.distance_km == null ? Number.POSITIVE_INFINITY : b.distance_km;
        return da - db;
      }
    }
  });

  selectedIndex = -1;
  updateResultCount();
  renderView();
  updateMapMarkers();
}

function updateResultCount() {
  const hot = filteredBusinesses.filter((b) => (b.website_opportunity_score || 0) >= 75).length;
  const sort = document.getElementById("sortSelect")?.value;
  const sortedNote = sort === "distance" || !sort ? " · closest to center first" : "";
  const total = allBusinesses.length;
  const showing = filteredBusinesses.length;
  const filteredNote = showing !== total ? ` of <span>${total}</span>` : "";
  document.getElementById("resultCount").innerHTML =
    `<span>${showing}</span>${filteredNote} businesses`
    + (hot ? ` · <span>${hot}</span> hot` : "")
    + sortedNote;
}

function setView(view) {
  currentView = view;
  document.getElementById("viewCards").classList.toggle("active", view === "cards");
  document.getElementById("viewTable").classList.toggle("active", view === "table");
  document.getElementById("mapPanel").classList.toggle("active", view === "cards");
  renderView();
}

function renderView() {
  const container = document.getElementById("resultsContainer");

  if (filteredBusinesses.length === 0) {
    container.innerHTML = `<p style="color:var(--text-muted);text-align:center;padding:3rem">No matching businesses found.</p>`;
    return;
  }

  if (currentView === "table") {
    container.innerHTML = renderTable();
  } else {
    container.innerHTML = `<div class="business-grid">${filteredBusinesses.map((b, i) => renderCard(b, i)).join("")}</div>`;
  }
}

function getBusinessEmails(b) {
  const list = Array.isArray(b.contact_emails) ? b.contact_emails.filter(Boolean) : [];
  if (b.contact_email && !list.includes(b.contact_email)) list.unshift(b.contact_email);
  return list;
}

function noWebsiteBadge(b) {
  // Only highlight businesses that do not have a website
  if (b.has_website || b.website_url || b.no_website_status === "has_website") {
    return "";
  }
  const score = b.no_website_score;
  const label = b.no_website_label || "No Website";
  const tier = b.no_website_tier || "medium";
  const cls = {
    high: "badge-score-high",
    medium: "badge-score-medium",
    low: "badge-score-low",
    fail: "badge-score-fail",
  }[tier] || "badge-no-site";
  const signals = (b.no_website_signals || []).join(" · ");
  const title = score != null
    ? `${label}: ${score}/100${signals ? ` · ${signals}` : ""}`
    : (signals || label);
  return `<span class="badge ${cls}" title="${esc(title)}">${esc(label)}</span>`;
}

function websiteOpportunityBadge(b) {
  const score = b.website_opportunity_score;
  if (score == null) return "";
  const tier = b.website_opportunity_tier || "cool";
  const cls = {
    hot: "badge-opp-hot",
    warm: "badge-opp-warm",
    cool: "badge-opp-cool",
    low: "badge-opp-low",
  }[tier] || "badge-opp-cool";
  const detail = (b.website_opportunity_breakdown || b.website_opportunity_signals || []).join(" · ");
  const title = detail || `Website opportunity: ${score}/100`;
  return `<span class="badge ${cls}" title="${esc(title)}">Opp</span>`;
}

function leadQualityBadge(b) {
  const score = b.lead_quality_score;
  if (score == null) return "";
  const label = b.lead_quality_label || "Lead";
  const tier = b.lead_quality_tier || "fair";
  const cls = {
    great: "badge-quality-great",
    good: "badge-quality-good",
    fair: "badge-quality-fair",
    weak: "badge-quality-weak",
  }[tier] || "badge-quality-fair";
  const signals = (b.lead_quality_signals || []).join(" · ");
  const boost = b.learning_boost ? ` · +${b.learning_boost} learning` : "";
  const title = `${label}: ${score}/100${boost}${signals ? ` · ${signals}` : ""}`;
  return `<span class="badge ${cls}" title="${esc(title)}">${esc(label)}</span>`;
}

function brandBookBadge(b) {
  const book = b.brand_book;
  if (!book || book.status !== "ready") return "";
  const colors = (book.colors || []).slice(0, 4);
  const swatches = colors
    .map((c) => `<span class="brand-swatch" style="background:${esc(c)}" title="${esc(c)}"></span>`)
    .join("");
  const src = book.source === "openai" ? "AI brand book" : "Brand book";
  return `<span class="badge badge-brand" title="${esc(book.tone || src)}">${swatches}${esc(src)}</span>`;
}

function noWebsiteMeta(b) {
  if (!b.no_website_status && b.no_website_score == null) return "";
  const bits = [];
  if (b.no_website_status === "social_only") bits.push("Social-only presence");
  if (b.no_website_status === "suspected_site" && b.suspected_website) {
    bits.push(`Possible site: ${b.suspected_website}`);
  }
  if (b.no_website_status === "verified_none") bits.push("No live website found on recheck");
  if (!bits.length) return "";
  return `<div class="score-meta">${esc(bits.join(" · "))}</div>`;
}

function renderCard(b, index) {
  const emails = getBusinessEmails(b);
  return `
    <div class="business-card ${selectedIndex === index ? "selected" : ""}" onclick="openDrawer(${index})">
      ${b.category ? `<div class="card-category">${esc(b.category)}</div>` : ""}
      <div class="card-header-row">
        <div class="card-name">${esc(b.name)}</div>
        <div class="card-header-meta">
          ${b.rating ? `<div class="card-rating">★ ${b.rating} <span>(${b.review_count || 0})</span></div>` : ""}
          ${favoriteButton(b, index)}
        </div>
      </div>
      <div class="card-score-tags">
        ${websiteOpportunityBadge(b)}
        ${leadQualityBadge(b)}
        ${noWebsiteBadge(b)}
        ${brandBookBadge(b)}
        ${pipelineBadge(b)}
      </div>
      <div class="card-details">
        ${b.distance_km != null ? `<div class="card-detail"><span class="d-icon">📏</span>${Number(b.distance_km).toFixed(1)} km from center</div>` : ""}
        ${b.address ? `<div class="card-detail"><span class="d-icon">📍</span>${esc(b.address)}</div>` : ""}
        ${emails.length
          ? emails.map((e) => `<div class="card-detail"><span class="d-icon">✉️</span><a href="mailto:${esc(e)}" style="color:var(--accent)" onclick="event.stopPropagation()">${esc(e)}</a></div>`).join("")
          : `<div class="card-detail card-detail-muted"><span class="d-icon">✉️</span>no email</div>`}
        ${b.phone ? `<div class="card-detail"><span class="d-icon">📞</span>${esc(b.phone)}</div>` : ""}
      </div>
      ${renderSocialIcons(b.social_profiles, true)}
      <div class="card-footer">
        ${b.google_maps_url
          ? `<a class="card-link" href="${esc(b.google_maps_url)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">Maps →</a>`
          : `<span></span>`}
      </div>
      <div class="card-actions">
        <button
          class="btn-create-site"
          data-create-site="${index}"
          ${figmaConnected ? "" : "disabled"}
          onclick="event.stopPropagation(); createSite(${index})"
        >Create Site</button>
        ${usageInfo?.plan === "large" ? `
        <button
          class="btn-campaign"
          data-campaign="${index}"
          ${openaiConnected ? "" : "disabled"}
          onclick="event.stopPropagation(); createCampaign(${index})"
          title="${openaiConnected ? "Generate a social campaign" : "Connect OpenAI in Integrations first"}"
        >Create Campaign</button>
        ` : ""}
        ${emails.length ? `
        <button
          class="btn-send-email"
          data-send-email="${index}"
          ${gmailConnected ? "" : "disabled"}
          onclick="event.stopPropagation(); sendEmail(${index})"
        >Send Email${emails.length > 1 ? ` (${emails.length})` : ""}</button>
        ` : ""}
      </div>
    </div>
  `;
}

function renderTable() {
  const rows = filteredBusinesses.map((b, i) => {
    const emails = getBusinessEmails(b);
    return `
    <tr class="${selectedIndex === i ? "selected" : ""}" onclick="openDrawer(${i})">
      <td class="name-cell">${esc(b.name)}</td>
      <td>${b.website_opportunity_score != null ? `${b.website_opportunity_score} · ${esc(b.website_opportunity_label || "")}` : "—"}</td>
      <td>${b.lead_quality_score != null ? `${b.lead_quality_score} · ${esc(b.lead_quality_label || "")}` : "—"}</td>
      <td>${b.no_website_score != null ? `${b.no_website_score} · ${esc(b.no_website_label || "")}` : "—"}</td>
      <td title="${esc(emails.join(', '))}">${emails.length ? esc(emails.join(", ")) : "—"}</td>
      <td>${b.rating ? `★ ${b.rating}` : "—"}</td>
      <td>${b.review_count || "—"}</td>
      <td>${esc(b.category || "—")}</td>
      <td title="${esc(b.address || "")}">${esc(b.address || "—")}</td>
      <td>${esc(b.phone || "—")}</td>
      <td>${esc(b.province || "—")}</td>
    </tr>
  `;
  }).join("");

  return `
    <div class="table-wrap">
      <table class="data-table">
        <thead>
          <tr>
            <th>Name</th><th>Opportunity</th><th>Quality</th><th>No-site</th><th>Email</th><th>Rating</th><th>Reviews</th><th>Category</th>
            <th>Address</th><th>Phone</th><th>Province</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;
}

// ── Drawer ──
function openDrawer(index) {
  selectedIndex = index;
  const b = filteredBusinesses[index];
  if (!b) return;

  renderView();

  document.getElementById("drawerTitle").innerHTML = `
    <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:0.75rem;width:100%">
      <div>
        ${esc(b.name)}
        <span style="margin-left:0.5rem">${websiteOpportunityBadge(b)}</span>
        <span style="margin-left:0.35rem">${leadQualityBadge(b)}</span>
        <span style="margin-left:0.35rem">${noWebsiteBadge(b)}</span>
      </div>
      ${favoriteButton(b, index)}
    </div>
  `;

  document.getElementById("drawerBody").innerHTML = `
    ${b.rating ? `<div style="color:var(--warning);font-weight:600;margin-bottom:1rem">★ ${b.rating} (${b.review_count || 0} reviews)</div>` : ""}
    ${b.category ? `<div style="color:var(--text-muted);margin-bottom:1rem">${esc(b.category)}</div>` : ""}

    <div class="drawer-section">
      <h4>Website Opportunity Score</h4>
      <div class="info-grid">
        ${infoRow(
          "Score",
          b.website_opportunity_score != null
            ? `<strong>${b.website_opportunity_score}/100</strong> · ${esc(b.website_opportunity_label || "")}`
            : "—"
        )}
      </div>
      ${(() => {
        const rows = b.website_opportunity_breakdown || b.website_opportunity_signals || [];
        if (!rows.length) return "";
        return `<ul class="opp-breakdown">${rows.map((s) => `<li>${esc(s)}</li>`).join("")}</ul>`;
      })()}
      ${(() => {
        const flags = b.website_flags || {};
        const labels = {
          no_website: "No website",
          outdated_website: "Outdated website",
          missing_online_store: "No online store",
          missing_booking: "No online booking",
          not_mobile_friendly: "Not mobile-friendly",
          slow_website: "Slow website",
          missing_https: "Missing HTTPS",
          has_email: "Email found",
          has_decision_maker: "Decision-maker email",
        };
        const chips = Object.entries(labels)
          .filter(([k]) => flags[k])
          .map(([, label]) => `<span class="flag-chip">${esc(label)}</span>`)
          .join("");
        return chips ? `<div class="flag-chip-row">${chips}</div>` : "";
      })()}
    </div>

    <div class="drawer-section">
      <h4>Lead quality</h4>
      <div class="info-grid">
        ${infoRow("Score", b.lead_quality_score != null ? `${b.lead_quality_score}/100 · ${esc(b.lead_quality_label || "")}` : "—")}
        ${b.learning_boost ? infoRow("Learning boost", `+${b.learning_boost} from converting categories/cities`) : ""}
        ${(b.lead_quality_signals || []).length ? infoRow("Why", esc(b.lead_quality_signals.join(" · "))) : ""}
      </div>
    </div>

    ${b.brand_book && b.brand_book.status === "ready" ? `
    <div class="drawer-section">
      <h4>Brand book</h4>
      <div class="info-grid">
        ${b.brand_book.colors?.length ? infoRow("Colors", `<span class="brand-swatch-row">${b.brand_book.colors.map((c) => `<span class="brand-swatch" style="background:${esc(c)}"></span><code>${esc(c)}</code>`).join(" ")}</span>`) : ""}
        ${b.brand_book.tone ? infoRow("Tone", esc(b.brand_book.tone)) : ""}
        ${b.brand_book.pricing_positioning ? infoRow("Pricing", esc(b.brand_book.pricing_positioning)) : ""}
        ${b.brand_book.logo_description ? infoRow("Logo", esc(b.brand_book.logo_description)) : ""}
        ${b.brand_book.imagery_style ? infoRow("Imagery", esc(b.brand_book.imagery_style)) : ""}
        ${b.brand_book.content_themes?.length ? infoRow("Content", esc(b.brand_book.content_themes.join(" · "))) : ""}
        ${b.brand_book.fonts_suggestion ? infoRow("Fonts", esc(b.brand_book.fonts_suggestion)) : ""}
        ${infoRow("Source", esc(b.brand_book.source || "—"))}
      </div>
    </div>
    ` : ""}

    <div class="drawer-section">
      <h4>Website status</h4>
      <div class="info-grid">
        ${infoRow("Has website", b.has_website || b.website_url ? "Yes" : "No")}
        ${b.website_url ? infoRow("Website", `<a href="${esc(b.website_url)}" target="_blank" rel="noopener" style="color:var(--accent)">${esc(b.website_url)}</a>`) : ""}
        ${infoRow("Score", b.no_website_score != null ? `${b.no_website_score}/100 · ${esc(b.no_website_label || "")}` : "—")}
        ${infoRow("Status", esc(b.no_website_status || "—"))}
        ${b.suspected_website ? infoRow("Suspected URL", `<a href="${esc(b.suspected_website)}" target="_blank" rel="noopener" style="color:var(--accent)">${esc(b.suspected_website)}</a>`) : ""}
        ${(b.no_website_signals || []).length ? infoRow("Signals", esc(b.no_website_signals.join(" · "))) : ""}
      </div>
    </div>

    <div class="drawer-section">
      <h4>Contact & Location</h4>
      <div class="info-grid">
        ${b.address ? infoRow("Address", b.address) : ""}
        ${(() => {
          const emails = getBusinessEmails(b);
          if (!emails.length) {
            return infoRow("Email", "<span style='color:var(--text-muted)'>Not found</span>");
          }
          return infoRow(
            emails.length > 1 ? "Emails" : "Email",
            emails.map((e) => `<a href="mailto:${esc(e)}" style="color:var(--accent)">${esc(e)}</a>`).join("<br>")
            + (b.email_source ? ` <span style="color:var(--text-muted)">(${esc(b.email_source)})</span>` : "")
          );
        })()}
        ${b.phone ? infoRow("Phone", `<a href="tel:${esc(b.phone)}" style="color:var(--accent)">${esc(b.phone)}</a>`) : ""}
        ${b.hours ? infoRow("Hours", b.hours.split(";").join("<br>")) : ""}
        ${b.province ? infoRow("Province", b.province) : ""}
        ${b.areas_served ? infoRow("Areas Served", b.areas_served) : ""}
        ${b.located_in ? infoRow("Located In", b.located_in) : ""}
        ${b.appointment_url ? infoRow("Appointments", `<a href="https://${esc(b.appointment_url)}" target="_blank" style="color:var(--accent)">${esc(b.appointment_url)}</a>`) : ""}
      </div>
    </div>

    ${b.description ? `
      <div class="drawer-section">
        <h4>About</h4>
        <p style="font-size:0.9rem;color:var(--text-secondary)">${esc(b.description)}</p>
      </div>
    ` : ""}

    ${b.products?.length ? `
      <div class="drawer-section">
        <h4>Products & Services (${b.products.length})</h4>
        <div class="product-list">
          ${b.products.map((p) => `
            <div class="product-item">
              <span>${esc(p.name)}</span>
              ${p.price ? `<span class="product-price">${esc(p.price)}</span>` : ""}
            </div>
          `).join("")}
        </div>
      </div>
    ` : ""}

    ${b.reviews?.length ? `
      <div class="drawer-section">
        <h4>Reviews</h4>
        ${b.reviews.map((r) => `
          <div class="review-block">
            <div class="review-author">${esc(r.author || "Anonymous")}${r.rating ? ` — ★ ${r.rating}` : ""}</div>
            <div class="review-text">"${esc(r.text || "")}"</div>
          </div>
        `).join("")}
      </div>
    ` : ""}

    ${Object.keys(b.social_profiles || {}).length ? `
      <div class="drawer-section">
        <h4>Social Profiles</h4>
        ${renderSocialIcons(b.social_profiles)}
        <div class="social-links-list">
          ${Object.entries(b.social_profiles).map(([k, v]) =>
            `<a href="${esc(v)}" target="_blank" rel="noopener noreferrer" class="social-link-item">${esc(SOCIAL_META[k]?.label || k)}</a>`
          ).join("")}
        </div>
      </div>
    ` : ""}

    ${b.google_maps_url ? `
      <a class="btn btn-primary" href="${esc(b.google_maps_url)}" target="_blank" style="margin-top:1rem;text-decoration:none">
        Open in Google Maps
      </a>
    ` : ""}
  `;

  document.getElementById("drawer").classList.add("open");
  document.getElementById("drawer").setAttribute("aria-hidden", "false");
  document.getElementById("drawerOverlay").classList.add("open");
  if (window.ChappieA11y) {
    const drawer = document.getElementById("drawer");
    const closeBtn = drawer.querySelector(".drawer-close");
    try {
      (closeBtn || drawer).focus({ preventScroll: true });
    } catch (_) { /* ignore */ }
  }
}

function infoRow(label, value) {
  return `<div class="info-item"><span class="info-label">${label}</span><span class="info-value">${value}</span></div>`;
}

function closeDrawer() {
  const drawer = document.getElementById("drawer");
  drawer.classList.remove("open");
  drawer.setAttribute("aria-hidden", "true");
  document.getElementById("drawerOverlay").classList.remove("open");
  selectedIndex = -1;
  renderView();
}

// ── Map ──
function initMap(lat, lng, radiusKm) {
  const panel = document.getElementById("mapPanel");
  if (!panel.classList.contains("active")) return;

  if (!map) {
    map = L.map("map").setView([lat, lng], 12);
    L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a>',
      maxZoom: 19,
    }).addTo(map);
  } else {
    map.setView([lat, lng], 12);
  }

  markers.forEach((m) => map.removeLayer(m));
  markers = [];

  L.circle([lat, lng], {
    radius: radiusKm * 1000,
    color: "#5b8def",
    fillColor: "#5b8def",
    fillOpacity: 0.08,
    weight: 2,
  }).addTo(map);

  L.marker([lat, lng], {
    icon: L.divIcon({
      className: "center-marker",
      html: '<div style="background:#5b8def;width:14px;height:14px;border-radius:50%;border:3px solid #fff;box-shadow:0 2px 6px rgba(0,0,0,0.4)"></div>',
      iconSize: [14, 14],
    }),
  }).addTo(map);

  updateMapMarkers();
}

function updateMapMarkers() {
  if (!map) return;

  markers.forEach((m) => map.removeLayer(m));
  markers = [];

  filteredBusinesses.forEach((b, i) => {
    if (!b.latitude || !b.longitude) return;
    const marker = L.marker([b.latitude, b.longitude]).addTo(map);
    marker.bindPopup(`<strong>${esc(b.name)}</strong><br>${esc(b.address || "")}`);
    marker.on("click", () => openDrawer(i));
    markers.push(marker);
  });
}

// ── Download ──
function download(format) {
  if (!currentJobId) return;
  window.location.href = `/api/download/${currentJobId}?format=${format}`;
}

// ── Utils ──
function showToast(msg) {
  const toast = document.getElementById("toast");
  toast.textContent = msg;
  toast.classList.add("show");
  if (window.ChappieA11y) ChappieA11y.announce(msg);
  setTimeout(() => toast.classList.remove("show"), 5000);
}

function hideToast() {
  document.getElementById("toast").classList.remove("show");
}

function esc(str) {
  if (!str) return "";
  const d = document.createElement("div");
  d.textContent = str;
  return d.innerHTML;
}
