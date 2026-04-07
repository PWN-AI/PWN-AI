import { apiFetch, hydratePublicChrome, initTheme, saveStoredAuth, setNavState, wireThemeToggle } from "/common.js";

initTheme();
setNavState("/auth");
wireThemeToggle(document.getElementById("theme-toggle"));

const state = {
  mode: "register",
};

const elements = {
  registerTab: document.getElementById("register-tab"),
  loginTab: document.getElementById("login-tab"),
  title: document.getElementById("auth-title"),
  registerForm: document.getElementById("register-form"),
  registerEmail: document.getElementById("register-email"),
  registerPassword: document.getElementById("register-password"),
  registerSubmit: document.getElementById("register-submit"),
  registerError: document.getElementById("register-error"),
  loginForm: document.getElementById("login-form"),
  loginEmail: document.getElementById("login-email"),
  loginPassword: document.getElementById("login-password"),
  loginSubmit: document.getElementById("login-submit"),
  loginError: document.getElementById("login-error"),
  apiStatusLabel: document.getElementById("api-status-label"),
  apiStatusNote: document.getElementById("api-status-note"),
};

const setMode = (mode) => {
  state.mode = mode;
  elements.registerTab.classList.toggle("tab-button--active", mode === "register");
  elements.loginTab.classList.toggle("tab-button--active", mode === "login");
  elements.title.textContent = mode === "register" ? "Create account" : "Welcome back";
  elements.registerForm.hidden = mode !== "register";
  elements.loginForm.hidden = mode !== "login";
};

const showError = (mode, message = "") => {
  const el = mode === "register" ? elements.registerError : elements.loginError;
  el.hidden = !message;
  el.textContent = message;
};

elements.registerTab.addEventListener("click", () => setMode("register"));
elements.loginTab.addEventListener("click", () => setMode("login"));

elements.registerForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  showError("register", "");
  elements.registerSubmit.disabled = true;

  try {
    const payload = await apiFetch("/api/auth/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        email: elements.registerEmail.value,
        password: elements.registerPassword.value,
      }),
    });

    saveStoredAuth(payload.token, payload.user);
    window.location.href = "/chat";
  } catch (error) {
    showError("register", error.message || "Registration failed.");
  } finally {
    elements.registerSubmit.disabled = false;
  }
});

elements.loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  showError("login", "");
  elements.loginSubmit.disabled = true;

  try {
    const payload = await apiFetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        email: elements.loginEmail.value,
        password: elements.loginPassword.value,
      }),
    });

    saveStoredAuth(payload.token, payload.user);
    window.location.href = "/chat";
  } catch (error) {
    showError("login", error.message || "Sign in failed.");
  } finally {
    elements.loginSubmit.disabled = false;
  }
});

const bootstrap = async () => {
  const auth = await hydratePublicChrome({
    redirectIfAuthed: true,
    redirectTarget: "/chat",
  });
  if (auth?.redirected) {
    return;
  }

  try {
    const health = await apiFetch("/api/health");
    elements.apiStatusLabel.textContent = health.status === "ok" ? "API online" : "API unavailable";
    elements.apiStatusNote.textContent = `Local API responded at ${new Date(health.timestamp).toLocaleString()}.`;
  } catch (error) {
    elements.apiStatusLabel.textContent = "API unavailable";
    elements.apiStatusNote.textContent = error.message || "The local API did not respond.";
  }
};

bootstrap();

bootstrap();
