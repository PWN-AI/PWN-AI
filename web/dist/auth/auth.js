import { apiFetch, readStoredAuth, saveStoredAuth, setNavState } from "/common.js";

setNavState("/auth");

const state = {
  mode: "register",
};

const elements = {
  registerTab: document.getElementById("register-tab"),
  loginTab: document.getElementById("login-tab"),
  form: document.getElementById("auth-form"),
  email: document.getElementById("auth-email"),
  password: document.getElementById("auth-password"),
  submit: document.getElementById("auth-submit"),
  error: document.getElementById("auth-error"),
  continueBox: document.getElementById("continue-box"),
  continueEmail: document.getElementById("continue-email"),
  apiStatusLabel: document.getElementById("api-status-label"),
  apiStatusNote: document.getElementById("api-status-note"),
};

const setMode = (mode) => {
  state.mode = mode;
  elements.registerTab.classList.toggle("tab-button--active", mode === "register");
  elements.loginTab.classList.toggle("tab-button--active", mode === "login");
  elements.submit.textContent = mode === "register" ? "Create operator" : "Enter workspace";
  elements.error.hidden = true;
};

const showError = (message = "") => {
  elements.error.hidden = !message;
  elements.error.textContent = message;
};

elements.registerTab.addEventListener("click", () => setMode("register"));
elements.loginTab.addEventListener("click", () => setMode("login"));

elements.form.addEventListener("submit", async (event) => {
  event.preventDefault();
  showError("");
  elements.submit.disabled = true;

  try {
    const endpoint = state.mode === "register" ? "/api/auth/register" : "/api/auth/login";
    const payload = await apiFetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        email: elements.email.value,
        password: elements.password.value,
      }),
    });

    saveStoredAuth(payload.token, payload.user);
    window.location.href = "/chat";
  } catch (error) {
    showError(error.message || "Authentication failed.");
  } finally {
    elements.submit.disabled = false;
  }
});

const bootstrap = async () => {
  const storedAuth = readStoredAuth();
  if (storedAuth?.user?.email) {
    elements.continueBox.hidden = false;
    elements.continueEmail.textContent = storedAuth.user.email;
    elements.email.value = storedAuth.user.email;
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
