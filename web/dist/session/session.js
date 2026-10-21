import {
  clearStoredAuth,
  fetchAuthContext,
  fetchProviderConfig,
  hideAuthNavLinks,
  initTheme,
  requireAuth,
  setHealthPill,
  setNavState,
  setUserText,
  streamText,
  updateProviderConfig,
  wireThemeToggle,
  wireSignOut,
} from "/common.js";

initTheme();
setNavState("/session");
wireThemeToggle(document.getElementById("theme-toggle"));
hideAuthNavLinks();

const auth = requireAuth();
if (!auth) {
  throw new Error("Authentication required");
}

const state = {
  token: auth.token,
  user: auth.user,
  health: null,
  provider: null,
  busy: false,
  providerBusy: false,
  output: "",
  buffer: "",
  frame: null,
  abort: null,
};

const elements = {
  healthPill: document.getElementById("health-pill"),
  currentUser: document.getElementById("current-user"),
  signout: document.getElementById("signout-button"),
  providerMode: document.getElementById("provider-mode"),
  providerNote: document.getElementById("provider-note"),
  providerButtons: Array.from(document.querySelectorAll(".provider-pill")),
  form: document.getElementById("session-form"),
  goal: document.getElementById("session-goal"),
  targetUrl: document.getElementById("session-target-url"),
  targetFile: document.getElementById("session-target-file"),
  loginUrl: document.getElementById("session-login-url"),
  crawlDepth: document.getElementById("session-crawl-depth"),
  username: document.getElementById("session-username"),
  password: document.getElementById("session-password"),
  autoApprove: document.getElementById("session-auto-approve"),
  printFinalState: document.getElementById("session-print-final-state"),
  submit: document.getElementById("session-submit"),
  clear: document.getElementById("clear-session"),
  meta: document.getElementById("session-meta"),
  terminal: document.getElementById("terminal"),
};

wireSignOut(elements.signout);

const renderProvider = () => {
  const activeProvider = state.provider?.activeProvider || state.provider?.fallbackProvider || "unknown";
  const availableProviders = state.provider?.availableProviders || [];
  const fallbackProvider = state.provider?.fallbackProvider || "unknown";

  elements.providerMode.textContent = activeProvider === "mixed" ? "Mixed providers" : `${activeProvider} provider`;
  elements.providerNote.textContent =
    activeProvider === "mixed"
      ? `Roles are split across providers. Fallback mode is ${fallbackProvider}.`
      : `Fallback mode is ${fallbackProvider}. Switch here to update all model roles together.`;

  elements.providerButtons.forEach((button) => {
    const isActive = button.dataset.provider === activeProvider;
    const isAvailable = availableProviders.includes(button.dataset.provider || "");
    button.classList.toggle("provider-pill--active", isActive);
    button.disabled = state.busy || state.providerBusy || !isAvailable;
    button.setAttribute("aria-pressed", isActive ? "true" : "false");
  });
};

const renderChrome = () => {
  setHealthPill(elements.healthPill, state.health);
  setUserText(elements.currentUser, state.user);
  elements.submit.disabled = state.busy || state.providerBusy || !elements.goal.value.trim();
  elements.clear.disabled = state.busy || state.providerBusy;

  [
    elements.goal,
    elements.targetUrl,
    elements.targetFile,
    elements.loginUrl,
    elements.crawlDepth,
    elements.username,
    elements.password,
    elements.autoApprove,
    elements.printFinalState,
  ].forEach((element) => {
    element.disabled = state.busy || state.providerBusy;
  });

  renderProvider();
};

const renderMeta = (message, isError = false) => {
  elements.meta.textContent = message;
  elements.meta.className = isError ? "error-text" : "helper-text";
};

const renderTerminal = () => {
  elements.terminal.textContent = state.output || "Session output will stream here in real time.";
  elements.terminal.scrollTop = elements.terminal.scrollHeight;
};

const flushBuffer = () => {
  state.frame = null;
  if (!state.buffer) {
    return;
  }

  state.output += state.buffer;
  state.buffer = "";
  renderTerminal();
};

const queueChunk = (chunk) => {
  state.buffer += chunk;
  if (state.frame === null) {
    state.frame = window.requestAnimationFrame(flushBuffer);
  }
};

const finalizeBuffer = () => {
  if (state.frame !== null) {
    window.cancelAnimationFrame(state.frame);
    state.frame = null;
  }
  flushBuffer();
};

elements.goal.addEventListener("input", renderChrome);
elements.clear.addEventListener("click", () => {
  state.output = "";
  renderTerminal();
});

elements.providerButtons.forEach((button) => {
  button.addEventListener("click", async () => {
    const nextProvider = button.dataset.provider;
    const currentProvider = state.provider?.activeProvider;

    if (!nextProvider || state.providerBusy || state.busy || currentProvider === nextProvider) {
      return;
    }

    state.providerBusy = true;
    renderMeta(`Switching provider to ${nextProvider}.`);
    renderChrome();

    try {
      state.provider = await updateProviderConfig(state.token, nextProvider);
      const context = await fetchAuthContext(state.token);
      state.user = context.user;
      state.health = context.health;
      state.provider = context.provider;

      renderMeta(
        state.health.ai === "connected"
          ? `Provider switched to ${nextProvider}. Session streaming is ready.`
          : `Provider switched to ${nextProvider}, but the selected backend is still unavailable.`,
        state.health.ai !== "connected"
      );
    } catch (error) {
      renderMeta(error.message || "Provider update failed.", true);
      state.provider = await fetchProviderConfig(state.token).catch(() => state.provider);
    } finally {
      state.providerBusy = false;
      renderChrome();
    }
  });
});

elements.form.addEventListener("submit", async (event) => {
  event.preventDefault();

  if (state.busy || state.providerBusy || !elements.goal.value.trim()) {
    return;
  }

  const payload = {
    goal: elements.goal.value.trim(),
    targetUrl: elements.targetUrl.value.trim() || undefined,
    targetRequestFile: elements.targetFile.value.trim() || undefined,
    loginUrl: elements.loginUrl.value.trim() || undefined,
    crawlDepth: Number(elements.crawlDepth.value || "2"),
    username: elements.username.value.trim() || undefined,
    password: elements.password.value || undefined,
    autoApprove: elements.autoApprove.checked,
    printFinalState: elements.printFinalState.checked,
  };

  state.output = "";
  state.busy = true;
  renderMeta("Streaming over POST /api/chat/session");
  renderTerminal();
  renderChrome();

  state.abort?.abort();
  state.abort = new AbortController();

  try {
    await streamText("/api/chat/session", state.token, payload, queueChunk, state.abort.signal);
    finalizeBuffer();
    renderMeta("Session stream completed cleanly.");
  } catch (error) {
    finalizeBuffer();
    renderMeta(error.message || "Session stream failed.", true);
  } finally {
    state.busy = false;
    state.abort = null;
    renderChrome();
  }
});

const bootstrap = async () => {
  try {
    const context = await fetchAuthContext(state.token);
    state.user = context.user;
    state.health = context.health;
    state.provider = context.provider;
  } catch (error) {
    if (error.status === 401) {
      clearStoredAuth();
      window.location.href = "/auth";
    } else {
      renderMeta(error.message || "Could not load session context.", true);
      renderChrome();
    }
    return;
  }

  renderMeta(
    state.health.ai === "connected"
      ? "Streaming over POST /api/chat/session"
      : "Selected provider is unavailable. Check local Ollama or cloud credentials before running a session.",
    state.health.ai !== "connected"
  );
  renderTerminal();
  renderChrome();
};

window.addEventListener("beforeunload", () => {
  state.abort?.abort();
});

bootstrap();
