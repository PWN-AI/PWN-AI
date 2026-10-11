import {
  createId,
  escapeHtml,
  fetchProviderConfig,
  fetchAuthContext,
  getModelByRole,
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
  clearStoredAuth,
} from "/common.js";

initTheme();
setNavState("/chat");
wireThemeToggle(document.getElementById("theme-toggle"));
hideAuthNavLinks();

const auth = requireAuth();
if (!auth) {
  throw new Error("Authentication required");
}

const state = {
  token: auth.token,
  user: auth.user,
  models: [],
  health: null,
  provider: null,
  messages: [],
  busy: false,
  providerBusy: false,
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
  chatStatus: document.getElementById("chat-status"),
  chatStatusNote: document.getElementById("chat-status-note"),
  headModel: document.getElementById("head-model"),
  headModelNote: document.getElementById("head-model-note"),
  implementorModel: document.getElementById("implementor-model"),
  implementorModelNote: document.getElementById("implementor-model-note"),
  transcript: document.getElementById("transcript"),
  clearChat: document.getElementById("clear-chat"),
  form: document.getElementById("chat-form"),
  input: document.getElementById("chat-input"),
  submit: document.getElementById("chat-submit"),
  meta: document.getElementById("chat-meta"),
  promptChips: document.querySelectorAll(".prompt-chip"),
};

wireSignOut(elements.signout);

const renderProvider = () => {
  const activeProvider = state.provider?.activeProvider || state.provider?.fallbackProvider || "unknown";
  const availableProviders = state.provider?.availableProviders || [];
  const fallbackProvider = state.provider?.fallbackProvider || "unknown";
  const modeLabel = activeProvider === "mixed" ? "Mixed providers" : `${activeProvider} provider`;

  elements.providerMode.textContent = modeLabel;
  elements.providerNote.textContent =
    activeProvider === "mixed"
      ? `Model roles are split across providers. Fallback mode is ${fallbackProvider}.`
      : `Fallback mode is ${fallbackProvider}. Switch here to update all model roles together.`;

  elements.providerButtons.forEach((button) => {
    const isActive = button.dataset.provider === activeProvider;
    const isAvailable = availableProviders.includes(button.dataset.provider || "");
    button.classList.toggle("provider-pill--active", isActive);
    button.disabled = state.busy || state.providerBusy || !isAvailable;
    button.setAttribute("aria-pressed", isActive ? "true" : "false");
  });
};

const renderStatus = (headline, note) => {
  elements.chatStatus.textContent = headline;
  elements.chatStatusNote.textContent = note;
  setHealthPill(elements.healthPill, state.health);
  setUserText(elements.currentUser, state.user);
  elements.input.disabled = state.busy || state.providerBusy;
  elements.submit.disabled = state.busy || state.providerBusy || !elements.input.value.trim();
  elements.clearChat.disabled = state.busy || state.providerBusy;
  elements.promptChips.forEach((chip) => {
    chip.disabled = state.busy || state.providerBusy;
  });
  renderProvider();
};

const renderMeta = (message, isError = false) => {
  elements.meta.textContent = message;
  elements.meta.className = isError ? "error-text" : "helper-text";
};

const renderTranscript = () => {
  if (!state.messages.length) {
    elements.transcript.innerHTML = `
      <div class="empty-state">
        <span class="badge badge--ghost">Ready</span>
        <h3>Start a streamed conversation</h3>
        <p>The backend emits the assistant response incrementally over normal HTTP.</p>
      </div>
    `;
    return;
  }

  elements.transcript.innerHTML = state.messages
    .map(
      (message) => `
        <article class="bubble bubble--${message.role}">
          <span class="bubble__role">${message.role}</span>
          <p>${escapeHtml(message.content || (message.role === "assistant" && state.busy ? "..." : ""))}</p>
        </article>
      `
    )
    .join("");

  elements.transcript.scrollTop = elements.transcript.scrollHeight;
};

const getRequestMessages = () =>
  state.messages
    .filter((message, index) => message.content.trim() || index !== state.messages.length - 1 || message.role !== "assistant")
    .map(({ role, content }) => ({ role, content }));

const removePendingAssistant = () => {
  const tail = state.messages[state.messages.length - 1];
  if (tail?.role === "assistant" && !tail.content.trim()) {
    state.messages.pop();
  }
};

const flushBuffer = () => {
  state.frame = null;
  if (!state.buffer) {
    return;
  }

  const tail = state.messages[state.messages.length - 1];
  if (tail && tail.role === "assistant") {
    tail.content += state.buffer;
  }
  state.buffer = "";
  renderTranscript();
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

elements.input.addEventListener("input", () => {
  renderStatus(elements.chatStatus.textContent, elements.chatStatusNote.textContent);
});

elements.promptChips.forEach((chip) => {
  chip.addEventListener("click", () => {
    elements.input.value = chip.dataset.prompt || "";
    renderStatus(elements.chatStatus.textContent, elements.chatStatusNote.textContent);
  });
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
    renderStatus("Updating AI provider.", "Refreshing model routing and connection state.");

    try {
      state.provider = await updateProviderConfig(state.token, nextProvider);
      const context = await fetchAuthContext(state.token);
      state.user = context.user;
      state.health = context.health;
      state.models = context.models;
      state.provider = context.provider;

      const head = getModelByRole(state.models, "head");
      const implementor = getModelByRole(state.models, "implementor");

      elements.headModel.textContent = head?.name || "Unavailable";
      elements.headModelNote.textContent = head ? `${head.provider} provider` : "No head model resolved.";
      elements.implementorModel.textContent = implementor?.name || "Unavailable";
      elements.implementorModelNote.textContent = implementor
        ? `${implementor.provider} provider`
        : "No implementor model resolved.";

      renderMeta(`Provider switched to ${nextProvider}.`);
      renderStatus(
        state.health.ai === "connected" ? "Chat surface armed." : "Selected provider is currently unavailable.",
        state.health.ai === "connected"
          ? "Prompts will stream live when the provider responds."
          : "Check your local Ollama instance or cloud credentials, then try again."
      );
    } catch (error) {
      renderMeta(error.message || "Provider update failed.", true);
      state.provider = await fetchProviderConfig(state.token).catch(() => state.provider);
      renderStatus("Provider update failed.", error.message || "The backend rejected the provider switch.");
    } finally {
      state.providerBusy = false;
      renderStatus(elements.chatStatus.textContent, elements.chatStatusNote.textContent);
    }
  });
});

elements.clearChat.addEventListener("click", () => {
  state.messages = [];
  renderTranscript();
});

elements.form.addEventListener("submit", async (event) => {
  event.preventDefault();

  if (state.busy || state.providerBusy || !elements.input.value.trim()) {
    return;
  }

  const prompt = elements.input.value.trim();
  state.messages.push({ id: createId(), role: "user", content: prompt });
  state.messages.push({ id: createId(), role: "assistant", content: "" });
  elements.input.value = "";
  state.busy = true;
  renderMeta("Streaming over POST /api/chat/completions");
  renderStatus("Streaming response from the head model.", "Assistant output is arriving incrementally.");
  renderTranscript();

  state.abort?.abort();
  state.abort = new AbortController();

  try {
    await streamText(
      "/api/chat/completions",
      state.token,
      {
        messages: getRequestMessages(),
      },
      queueChunk,
      state.abort.signal
    );
    finalizeBuffer();
    renderMeta("Stream completed cleanly.");
    renderStatus("Chat surface ready.", "The assistant stream finished without transport errors.");
  } catch (error) {
    finalizeBuffer();
    removePendingAssistant();
    renderMeta(error.message || "Chat stream failed.", true);
    renderStatus("Chat surface blocked.", error.message || "The backend rejected the chat request.");
    renderTranscript();
  } finally {
    state.busy = false;
    state.abort = null;
    renderStatus(elements.chatStatus.textContent, elements.chatStatusNote.textContent);
  }
});

const bootstrap = async () => {
  try {
    const context = await fetchAuthContext(state.token);
    state.user = context.user;
    state.health = context.health;
    state.models = context.models;
    state.provider = context.provider;

    const head = getModelByRole(state.models, "head");
    const implementor = getModelByRole(state.models, "implementor");

    elements.headModel.textContent = head?.name || "Unavailable";
    elements.headModelNote.textContent = head ? `${head.provider} provider` : "No head model resolved.";
    elements.implementorModel.textContent = implementor?.name || "Unavailable";
    elements.implementorModelNote.textContent = implementor
      ? `${implementor.provider} provider`
      : "No implementor model resolved.";

    renderStatus(
      state.health.ai === "connected" ? "Chat surface armed." : "AI backend is currently disconnected.",
      state.health.ai === "connected"
        ? "Prompts will stream live when the provider responds."
        : "Check your local Ollama instance or cloud credentials to enable live output."
    );
  } catch (error) {
    if (error.status === 401) {
      clearStoredAuth();
      window.location.href = "/auth";
    } else {
      renderStatus("Connection unavailable.", error.message || "Could not load AI context.");
    }
    return;
  }

  renderMeta("Streaming over POST /api/chat/completions");
  renderTranscript();
};

window.addEventListener("beforeunload", () => {
  state.abort?.abort();
});

bootstrap();
