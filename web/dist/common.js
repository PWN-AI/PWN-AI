export const STORAGE_KEY = "pwn-ai-auth";

export const readStoredAuth = () => {
  const raw = window.localStorage.getItem(STORAGE_KEY);
  if (!raw) {
    return null;
  }

  try {
    return JSON.parse(raw);
  } catch {
    window.localStorage.removeItem(STORAGE_KEY);
    return null;
  }
};

export const saveStoredAuth = (token, user) => {
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ token, user }));
};

export const clearStoredAuth = () => {
  window.localStorage.removeItem(STORAGE_KEY);
};

export const readErrorMessage = async (response) => {
  try {
    const payload = await response.json();
    return payload.error || `Request failed with status ${response.status}`;
  } catch {
    return `Request failed with status ${response.status}`;
  }
};

export const apiFetch = async (path, init = {}) => {
  const response = await fetch(path, init);
  if (!response.ok) {
    throw new Error(await readErrorMessage(response));
  }
  return response.json();
};

export const streamText = async (path, token, body, onChunk, signal) => {
  const response = await fetch(path, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ ...body, stream: true }),
    signal,
  });

  if (!response.ok) {
    throw new Error(await readErrorMessage(response));
  }

  if (!response.body) {
    throw new Error("Readable stream is not available in this browser.");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();

  while (true) {
    const { value, done } = await reader.read();
    if (done) {
      break;
    }
    onChunk(decoder.decode(value, { stream: true }));
  }

  const trailing = decoder.decode();
  if (trailing) {
    onChunk(trailing);
  }
};

export const fetchAuthContext = async (token) => {
  const [me, health, models] = await Promise.all([
    apiFetch("/api/auth/me", { headers: { Authorization: `Bearer ${token}` } }),
    apiFetch("/api/chat/health", { headers: { Authorization: `Bearer ${token}` } }),
    apiFetch("/api/chat/models", { headers: { Authorization: `Bearer ${token}` } }),
  ]);

  return {
    user: me.user,
    health,
    models: models.models || [],
  };
};

export const requireAuth = () => {
  const auth = readStoredAuth();
  if (!auth?.token) {
    window.location.href = "/auth";
    return null;
  }
  return auth;
};

export const escapeHtml = (value) =>
  value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");

export const createId = () =>
  typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;

export const setHealthPill = (element, health) => {
  if (!element) {
    return;
  }

  const connected = health?.ai === "connected";
  element.textContent = connected ? "AI connected" : "AI disconnected";
  element.className = `badge ${connected ? "badge--online" : "badge--ghost"}`;
};

export const setUserText = (element, user) => {
  if (element) {
    element.textContent = user?.email || "Unauthenticated";
  }
};

export const getModelByRole = (models, role) => models.find((entry) => entry.role === role);

export const wireSignOut = (button, destination = "/auth") => {
  if (!button) {
    return;
  }

  button.hidden = false;
  button.addEventListener("click", () => {
    clearStoredAuth();
    window.location.href = destination;
  });
};

export const setNavState = (pathname) => {
  document.querySelectorAll("[data-nav]").forEach((link) => {
    link.classList.toggle("nav-link--active", link.getAttribute("href") === pathname);
  });
};
