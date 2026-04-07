import {
  clearStoredAuth,
  fetchAuthContext,
  requireAuth,
  setHealthPill,
  setNavState,
  setUserText,
  streamText,
  wireSignOut,
} from "/common.js";

setNavState("/session");

const auth = requireAuth();
if (!auth) {
  throw new Error("Authentication required");
}

const state = {
  token: auth.token,
  user: auth.user,
  health: null,
  busy: false,
  output: "",
  buffer: "",
  frame: null,
  abort: null,
};

const elements = {
  healthPill: document.getElementById("health-pill"),
  currentUser: document.getElementById("current-user"),
  signout: document.getElementById("signout-button"),
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

const renderChrome = () => {
  setHealthPill(elements.healthPill, state.health);
  setUserText(elements.currentUser, state.user);
  elements.submit.disabled = state.busy || !elements.goal.value.trim();
  elements.clear.disabled = state.busy;

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
    element.disabled = state.busy;
  });
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

elements.form.addEventListener("submit", async (event) => {
  event.preventDefault();

  if (state.busy || !elements.goal.value.trim()) {
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
  } catch (error) {
    clearStoredAuth();
    window.location.href = "/auth";
    return;
  }

  renderMeta(
    state.health.ai === "connected"
      ? "Streaming over POST /api/chat/session"
      : "AI disconnected. Start Ollama before running a live pentest session.",
    state.health.ai !== "connected"
  );
  renderTerminal();
  renderChrome();
};

window.addEventListener("beforeunload", () => {
  state.abort?.abort();
});

bootstrap();
