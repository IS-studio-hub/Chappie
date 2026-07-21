/**
 * Shared accessibility helpers: modal focus trap/restore, live regions.
 */
(function (global) {
  const FOCUSABLE =
    'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

  let _restoreFocusEl = null;
  let _trapHandler = null;
  let _trapRoot = null;

  function getFocusable(root) {
    return [...root.querySelectorAll(FOCUSABLE)].filter(
      (el) => !el.hasAttribute("disabled") && el.offsetParent !== null
    );
  }

  function openDialog(dialogEl, { focusSelector } = {}) {
    if (!dialogEl) return;
    _restoreFocusEl = document.activeElement;
    dialogEl.hidden = false;
    document.body.style.overflow = "hidden";

    const panel =
      dialogEl.getAttribute("role") === "dialog"
        ? dialogEl
        : dialogEl.querySelector('[role="dialog"]') || dialogEl;

    const focusTarget =
      (focusSelector && panel.querySelector(focusSelector)) ||
      getFocusable(panel)[0] ||
      panel;
    try {
      focusTarget.focus({ preventScroll: true });
    } catch (_) {
      /* ignore */
    }

    _trapRoot = panel;
    _trapHandler = (e) => {
      if (e.key !== "Tab" || !_trapRoot) return;
      const list = getFocusable(_trapRoot);
      if (!list.length) {
        e.preventDefault();
        return;
      }
      const first = list[0];
      const last = list[list.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", _trapHandler);
  }

  function closeDialog(dialogEl) {
    if (!dialogEl) return;
    dialogEl.hidden = true;
    document.body.style.overflow = "";
    if (_trapHandler) {
      document.removeEventListener("keydown", _trapHandler);
      _trapHandler = null;
      _trapRoot = null;
    }
    const restore = _restoreFocusEl;
    _restoreFocusEl = null;
    if (restore && typeof restore.focus === "function") {
      try {
        restore.focus({ preventScroll: true });
      } catch (_) {
        /* ignore */
      }
    }
  }

  function announce(msg, politeness = "polite") {
    let live = document.getElementById("a11yLive");
    if (!live) {
      live = document.createElement("div");
      live.id = "a11yLive";
      live.className = "sr-only";
      live.setAttribute("aria-live", politeness);
      live.setAttribute("aria-atomic", "true");
      document.body.appendChild(live);
    }
    live.setAttribute("aria-live", politeness);
    live.textContent = "";
    // Force announcement on repeated identical messages
    setTimeout(() => {
      live.textContent = msg;
    }, 30);
  }

  global.ChappieA11y = { openDialog, closeDialog, announce, getFocusable };
})(window);
