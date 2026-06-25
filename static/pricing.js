(function () {
  const statusEl = document.getElementById("pricing-status");
  const errorEl = document.getElementById("pricing-error");
  const checkoutBtn = document.getElementById("pricing-checkout-btn");
  const portalBtn = document.getElementById("pricing-portal-btn");

  let userEmail = "";

  function show(el, text, isError) {
    if (!el) return;
    el.textContent = text || "";
    el.classList.toggle("hidden", !text);
    if (isError !== undefined) {
      el.classList.toggle("auth-error", isError);
      el.classList.toggle("auth-note", !isError);
    }
  }

  function setCheckoutAction(action, label) {
    if (!checkoutBtn) return;
    checkoutBtn.disabled = false;
    checkoutBtn.classList.remove("pricing-btn-disabled", "hidden");
    checkoutBtn.textContent = label;
    checkoutBtn.dataset.action = action;
  }

  async function loadStatus() {
    const res = await fetch("/api/auth/status");
    const body = await res.json().catch(() => ({}));
    const ua = body.user_auth || {};
    userEmail = ua.email || "";

    if (!body.billing_enabled) {
      show(
        statusEl,
        "Billing not active — save STRIPE_SECRET_KEY and STRIPE_PRICE_ID in .env, then restart the server.",
        false
      );
      checkoutBtn.disabled = true;
      checkoutBtn.classList.add("pricing-btn-disabled");
      checkoutBtn.dataset.action = "none";
      return;
    }

    if (ua.is_premium) {
      show(statusEl, "You have an active Premium subscription.", false);
      checkoutBtn.classList.add("hidden");
      portalBtn.classList.remove("hidden");
      return;
    }

    if (!ua.signed_in) {
      show(statusEl, "Sign in or create a free account, then verify your email to subscribe.", false);
      setCheckoutAction("signin", "Sign in to subscribe");
      return;
    }

    if (!ua.email_verified) {
      show(statusEl, "Verify your email before starting a trial.", false);
      setCheckoutAction("verify", "Verify email");
      return;
    }

    show(statusEl, "7-day free trial · cancel anytime in billing portal.", false);
    setCheckoutAction("checkout", "Upgrade — start free trial");
  }

  async function startCheckout() {
    show(errorEl, "", true);
    checkoutBtn.disabled = true;
    try {
      const res = await fetch("/api/billing/checkout", { method: "POST" });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        show(errorEl, body.detail || "Checkout failed", true);
        return;
      }
      if (body.checkout_url) {
        window.location.href = body.checkout_url;
        return;
      }
      show(errorEl, "Checkout did not return a URL", true);
    } catch {
      show(errorEl, "Network error — try again", true);
    } finally {
      checkoutBtn.disabled = false;
    }
  }

  async function openPortal() {
    portalBtn.disabled = true;
    try {
      const res = await fetch("/api/billing/portal", { method: "POST" });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        show(errorEl, body.detail || "Could not open billing portal", true);
        return;
      }
      if (body.portal_url) {
        window.location.href = body.portal_url;
      }
    } catch {
      show(errorEl, "Network error — try again", true);
    } finally {
      portalBtn.disabled = false;
    }
  }

  function onCheckoutClick() {
    const action = checkoutBtn?.dataset.action || "checkout";
    if (action === "none" || checkoutBtn?.disabled) return;
    if (action === "signin") {
      window.location.href = "/signin?next=" + encodeURIComponent("/pricing");
      return;
    }
    if (action === "verify") {
      window.location.href =
        "/verify-email?email=" + encodeURIComponent(userEmail);
      return;
    }
    startCheckout();
  }

  checkoutBtn?.addEventListener("click", onCheckoutClick);
  portalBtn?.addEventListener("click", openPortal);
  loadStatus();

  const params = new URLSearchParams(window.location.search);
  if (params.get("checkout") === "success") {
    show(statusEl, "Welcome to Premium — refresh may take a few seconds.", false);
  }
})();
