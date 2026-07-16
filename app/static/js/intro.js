let authMode = "signin";
let pendingPlan = null;
let currentUser = null;

async function api(path, options = {}) {
  const res = await fetch(path, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = data.detail;
    const msg = typeof detail === "string" ? detail : detail?.message || data.message || "Request failed";
    throw new Error(msg);
  }
  return data;
}

async function refreshUser() {
  const data = await api("/api/auth/me");
  currentUser = data.user;
  const navSignIn = document.getElementById("navSignIn");
  const navCta = document.getElementById("navCta");
  if (currentUser) {
    navSignIn.textContent = "My account";
    navSignIn.onclick = () => { window.location.href = "/account"; };
    navCta.textContent = "Open app";
    navCta.href = "/app";
    navCta.onclick = null;
  }
  return currentUser;
}

function resetPasswordVisibility() {
  const input = document.getElementById("authPassword");
  const btn = document.getElementById("passwordToggle");
  if (!input || !btn) return;
  input.type = "password";
  btn.querySelector(".eye-open")?.classList.add("is-visible");
  btn.querySelector(".eye-slash")?.classList.remove("is-visible");
  btn.dataset.visible = "false";
  btn.setAttribute("aria-label", "Show password");
  btn.title = "Show password";
}

function openAuth(mode = "signin") {
  authMode = mode;
  const modal = document.getElementById("authModal");
  modal.hidden = false;
  document.getElementById("authForm").hidden = false;
  document.getElementById("verifySent").hidden = true;
  document.getElementById("authSwitchRow").hidden = false;
  resetPasswordVisibility();
  syncAuthUi();
  document.getElementById("authError").hidden = true;
}

function closeAuth() {
  document.getElementById("authModal").hidden = true;
  pendingPlan = null;
}

function toggleAuthMode() {
  authMode = authMode === "signin" ? "signup" : "signin";
  document.getElementById("authForm").hidden = false;
  document.getElementById("verifySent").hidden = true;
  document.getElementById("authSwitchRow").hidden = false;
  syncAuthUi();
}

function syncAuthUi() {
  const isSignup = authMode === "signup";
  document.getElementById("authTitle").textContent = isSignup ? "Create account" : "Sign in";
  document.getElementById("authSub").textContent = isSignup
    ? "We'll email you a verification link to finish signup."
    : "Welcome back to Chappie.";
  document.getElementById("nameField").hidden = !isSignup;
  document.getElementById("authSubmit").textContent = isSignup ? "Sign up" : "Sign in";
  document.getElementById("authSwitchText").textContent = isSignup ? "Already have an account?" : "No account?";
  document.getElementById("authSwitchBtn").textContent = isSignup ? "Sign in" : "Sign up";
  document.getElementById("authPassword").autocomplete = isSignup ? "new-password" : "current-password";
}

function showVerifySent(email) {
  document.getElementById("authForm").hidden = true;
  document.getElementById("authSwitchRow").hidden = true;
  document.getElementById("verifySent").hidden = false;
  document.getElementById("authTitle").textContent = "Check your email";
  document.getElementById("authSub").textContent = "";
  document.getElementById("verifySentText").innerHTML =
    `We sent a verification link to <strong>${email}</strong>. Click <strong>Verify email</strong> in that message to create your account.`;
}

async function submitAuth(event) {
  event.preventDefault();
  const err = document.getElementById("authError");
  err.hidden = true;
  const email = document.getElementById("authEmail").value.trim();
  const password = document.getElementById("authPassword").value;
  const name = document.getElementById("authName").value.trim();
  const btn = document.getElementById("authSubmit");
  btn.disabled = true;
  try {
    if (authMode === "signup") {
      const body = { email, password, name };
      if (pendingPlan) body.pending_plan = pendingPlan;
      await api("/api/auth/signup", { method: "POST", body: JSON.stringify(body) });
      pendingPlan = null;
      showVerifySent(email);
    } else {
      await api("/api/auth/signin", { method: "POST", body: JSON.stringify({ email, password }) });
      // Fresh login → empty search fields + no leftover results from prior session
      try {
        sessionStorage.removeItem("chappie_session_center_address");
        sessionStorage.removeItem("chappie_session_target_categories");
        sessionStorage.removeItem("chappie_session_search_results");
      } catch (_) {}
      await refreshUser();
      closeAuth();
      if (pendingPlan) {
        const plan = pendingPlan;
        pendingPlan = null;
        await checkout(plan);
      } else {
        window.location.href = "/app";
      }
    }
  } catch (e) {
    err.textContent = e.message;
    err.hidden = false;
  } finally {
    btn.disabled = false;
  }
  return false;
}

function togglePasswordVisibility() {
  const input = document.getElementById("authPassword");
  const btn = document.getElementById("passwordToggle");
  const eyeOpen = btn.querySelector(".eye-open");
  const eyeSlash = btn.querySelector(".eye-slash");
  const willShow = input.type === "password";

  input.type = willShow ? "text" : "password";
  eyeOpen.classList.toggle("is-visible", !willShow);
  eyeSlash.classList.toggle("is-visible", willShow);
  btn.dataset.visible = willShow ? "true" : "false";
  btn.setAttribute("aria-label", willShow ? "Hide password" : "Show password");
  btn.title = willShow ? "Hide password" : "Show password";
}

function handlePrimaryCta(event) {
  event.preventDefault();
  if (currentUser) {
    window.location.href = "/app";
  } else {
    openAuth("signup");
  }
  return false;
}

function startPlan(plan) {
  if (!currentUser) {
    pendingPlan = plan;
    openAuth("signup");
    return;
  }
  checkout(plan);
}

async function checkout(plan) {
  try {
    const data = await api("/api/billing/checkout", {
      method: "POST",
      body: JSON.stringify({ plan }),
    });
    window.location.href = data.url;
  } catch (e) {
    alert(e.message);
  }
}

document.addEventListener("DOMContentLoaded", async () => {
  const params = new URLSearchParams(window.location.search);
  try {
    await refreshUser();
  } catch {
    /* ignore */
  }
  if (params.get("signin") === "1") openAuth("signin");
  if (params.get("signup") === "1") openAuth("signup");
  if (params.get("verify_error")) {
    openAuth("signup");
    const err = document.getElementById("authError");
    err.textContent = params.get("verify_error");
    err.hidden = false;
    history.replaceState({}, "", "/");
  }
});

document.getElementById("authModal")?.addEventListener("click", (e) => {
  if (e.target.id === "authModal") closeAuth();
});
