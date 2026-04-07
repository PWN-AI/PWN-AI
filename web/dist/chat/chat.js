import {
  createId,
  escapeHtml,
  fetchAuthContext,
  getModelByRole,
  requireAuth,
  setHealthPill,
  setNavState,
  setUserText,
  streamText,
  wireSignOut,
  clearStoredAuth,
} from "/common.js";

setNavState("/chat");

const auth = requireAuth();
if (!auth) {
  throw new Error("Authentication required");
}

const state = {
  token: auth.token,
  user: auth.user,
  models: [],
  health: null,
  messages: [],
  busy: false,
  buffer: "",
  frame: null,
  abort: null,
};

const elements = {
  healthPill: document.getElementById("health-pill"),
  currentUser: document.getElementById("current-user"),
  signout: document.getElementById("signout-button"),
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

const renderStatus = (headline, note) => {
  elements.chatStatus.textContent = headline;
  elements.chatStatusNote.textContent = note;
  setHealthPill(elements.healthPill, state.health);
  setUserText(elements.currentUser, state.user);
  elements.input.disabled = state.busy;
  elements.submit.disabled = state.busy || !elements.input.value.trim();
  elements.clearChat.disabled = state.busy;
  elements.promptChips.forEach((chip) => {
    chip.disabled = state.busy;
  });
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

elements.clearChat.addEventListener("click", () => {
  state.messages = [];
  renderTranscript();
});

elements.form.addEventListener("submit", async (event) => {
  event.preventDefault();

  if (state.busy || !elements.input.value.trim()) {
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
        messages: state.messages.map(({ role, content }) => ({ role, content })),
      },
      queueChunk,
      state.abort.signal
    );
    finalizeBuffer();
    renderMeta("Stream completed cleanly.");
    renderStatus("Chat surface ready.", "The assistant stream finished without transport errors.");
  } catch (error) {
    finalizeBuffer();
    renderMeta(error.message || "Chat stream failed.", true);
    renderStatus("Chat surface blocked.", error.message || "The backend rejected the chat request.");
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
        ? "Prompts will stream live when the model responds."
        : "Start Ollama to enable real model output."
    );
  } catch (error) {
    clearStoredAuth();
    window.location.href = "/auth";
    return;
  }

  renderMeta("Streaming over POST /api/chat/completions");
  renderTranscript();
};

window.addEventListener("beforeunload", () => {
  state.abort?.abort();
});

bootstrap();
