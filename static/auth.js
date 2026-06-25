function authNextPath(defaultPath) {
  const params = new URLSearchParams(window.location.search);
  const next = params.get("next");
  if (!next || !next.startsWith("/") || next.startsWith("//")) return defaultPath || "/";
  return next;
}

function showAuthNote(el, text, isError) {
  if (!el) return;
  el.textContent = text || "";
  el.classList.toggle("hidden", !text);
  if (isError !== undefined) {
    el.classList.toggle("auth-error", !!isError);
    el.classList.toggle("auth-note", !isError);
  }
}

function showDevVerificationLink(el, url) {
  if (!el || !url) return;
  el.replaceChildren();
  el.append("Local dev — no email was sent. ");
  const link = document.createElement("a");
  link.href = url;
  link.textContent = "Click here to verify your email";
  el.appendChild(link);
  el.classList.remove("hidden");
  el.classList.add("auth-note");
  el.classList.remove("auth-error");
  try {
    sessionStorage.setItem("dev_verification_url", url);
  } catch (_) {}
}

function initSignInPage() {
  const form = document.getElementById("signin-form");
  const errorEl = document.getElementById("signin-error");
  const noteEl = document.getElementById("signin-note");
  const btn = document.getElementById("signin-btn");
  form?.addEventListener("submit", async (e) => {
    e.preventDefault();
    showAuthNote(errorEl, "", true);
    btn.disabled = true;
    try {
      const res = await fetch("/api/auth/user/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email: document.getElementById("email").value,
          password: document.getElementById("password").value,
        }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        showAuthNote(errorEl, body.detail || "Sign in failed", true);
        return;
      }
      window.location.href = authNextPath("/");
    } catch {
      showAuthNote(errorEl, "Network error — try again", true);
    } finally {
      btn.disabled = false;
    }
  });
}

function initSignUpPage() {
  const form = document.getElementById("signup-form");
  const errorEl = document.getElementById("signup-error");
  const noteEl = document.getElementById("signup-note");
  const btn = document.getElementById("signup-btn");

  form?.addEventListener("submit", async (e) => {
    e.preventDefault();
    showAuthNote(errorEl, "", true);
    const password = document.getElementById("password").value;
    const password2 = document.getElementById("password2").value;
    if (password !== password2) {
      showAuthNote(errorEl, "Passwords do not match", true);
      return;
    }
    const acceptTerms = document.getElementById("accept-terms")?.checked;
    if (!acceptTerms) {
      showAuthNote(errorEl, "You must accept the Terms and Privacy Policy", true);
      return;
    }
    btn.disabled = true;
    try {
      const res = await fetch("/api/auth/user/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email: document.getElementById("email").value,
          password,
          accept_terms: true,
        }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        showAuthNote(errorEl, body.detail || "Sign up failed", true);
        return;
      }
      showAuthNote(noteEl, body.message || "Check your email to verify your account.", false);
      if (body.dev_verification_url) {
        showDevVerificationLink(noteEl, body.dev_verification_url);
        return;
      }
      window.setTimeout(() => {
        window.location.href = authNextPath("/verify-email?email=" + encodeURIComponent(body.email || document.getElementById("email").value));
      }, 1200);
    } catch {
      showAuthNote(errorEl, "Network error — try again", true);
    } finally {
      btn.disabled = false;
    }
  });
}

function initVerifyEmailPage() {
  const params = new URLSearchParams(window.location.search);
  const token = params.get("token");
  const email = params.get("email") || "";
  const intro = document.getElementById("verify-intro");
  const errorEl = document.getElementById("verify-error");
  const noteEl = document.getElementById("verify-note");
  const resendForm = document.getElementById("resend-form");
  const emailInput = document.getElementById("email");

  if (emailInput && email) emailInput.value = email;

  async function verifyNow() {
    if (!token) {
      intro.textContent = "Verify your email to subscribe and unlock full picks.";
      const devUrl = (() => {
        try {
          return sessionStorage.getItem("dev_verification_url");
        } catch (_) {
          return null;
        }
      })();
      if (devUrl) {
        showDevVerificationLink(noteEl, devUrl);
        intro.textContent = "Local dev — no email was sent. Use the link below:";
      } else {
        intro.textContent =
          "Enter your email to get a verification link, or open the link from your inbox.";
      }
      resendForm?.classList.remove("hidden");
      return;
    }
    intro.textContent = "Confirming your account…";
    try {
      const res = await fetch("/api/auth/user/verify-email", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        intro.textContent = "Verification failed";
        showAuthNote(errorEl, body.detail || "Invalid or expired link", true);
        resendForm?.classList.remove("hidden");
        return;
      }
      intro.textContent = "Email verified";
      showAuthNote(noteEl, "Your account is ready — player props are unlocked.", false);
      window.setTimeout(() => {
        window.location.href = authNextPath("/mlb/props");
      }, 1200);
    } catch {
      intro.textContent = "Verification failed";
      showAuthNote(errorEl, "Network error — try again", true);
      resendForm?.classList.remove("hidden");
    }
  }

  resendForm?.addEventListener("submit", async (e) => {
    e.preventDefault();
    showAuthNote(errorEl, "", true);
    const btn = document.getElementById("resend-btn");
    btn.disabled = true;
    try {
      const res = await fetch("/api/auth/user/resend-verification", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: emailInput.value }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        showAuthNote(errorEl, body.detail || "Could not resend email", true);
        return;
      }
      showAuthNote(noteEl, body.message || "Verification email sent.", false);
      if (body.dev_verification_url) {
        showDevVerificationLink(noteEl, body.dev_verification_url);
      }
    } catch {
      showAuthNote(errorEl, "Network error — try again", true);
    } finally {
      btn.disabled = false;
    }
  });

  verifyNow();
}

window.initSignInPage = initSignInPage;
window.initSignUpPage = initSignUpPage;
window.initVerifyEmailPage = initVerifyEmailPage;
