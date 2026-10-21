export const STORAGE_KEY = "pwn-ai-auth";
export const THEME_KEY = "pwn-ai-theme";

export const getStoredTheme = () => {
  const stored = window.localStorage.getItem(THEME_KEY);
  if (stored === "light" || stored === "dark") {
    return stored;
  }

  return null;
};

export const getPreferredTheme = () => {
  const stored = getStoredTheme();
  if (stored) {
    return stored;
  }

  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
};

export const applyTheme = (theme, options = {}) => {
  const { persist = true } = options;
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme;
  if (persist) {
    window.localStorage.setItem(THEME_KEY, theme);
  }
};

export const initTheme = () => {
  const theme = getPreferredTheme();
  applyTheme(theme, { persist: Boolean(getStoredTheme()) });
  return theme;
};

export const wireThemeToggle = (button) => {
  if (!button) {
    return;
  }

  const SUN = `<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>`;
  const MOON = `<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>`;

  const renderButton = (theme) => {
    button.innerHTML = theme === "dark" ? SUN : MOON;
    button.setAttribute("aria-pressed", theme === "dark" ? "true" : "false");
    button.setAttribute("title", theme === "dark" ? "Switch to light mode" : "Switch to dark mode");
    button.setAttribute("aria-label", theme === "dark" ? "Switch to light mode" : "Switch to dark mode");
  };

  renderButton(document.documentElement.dataset.theme || initTheme());

  button.addEventListener("click", () => {
    const nextTheme = (document.documentElement.dataset.theme || "light") === "dark" ? "light" : "dark";
    applyTheme(nextTheme);
    renderButton(nextTheme);
  });

  const media = window.matchMedia("(prefers-color-scheme: dark)");
  const handleSystemThemeChange = () => {
    if (getStoredTheme()) {
      return;
    }

    const nextTheme = media.matches ? "dark" : "light";
    applyTheme(nextTheme, { persist: false });
    renderButton(nextTheme);
  };

  if (typeof media.addEventListener === "function") {
    media.addEventListener("change", handleSystemThemeChange);
  } else if (typeof media.addListener === "function") {
    media.addListener(handleSystemThemeChange);
  }

  window.addEventListener("storage", (event) => {
    if (event.key !== THEME_KEY) {
      return;
    }

    const nextTheme = getPreferredTheme();
    applyTheme(nextTheme, { persist: false });
    renderButton(nextTheme);
  });
};

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

export const verifyStoredAuth = async () => {
  const auth = readStoredAuth();
  if (!auth?.token) {
    return null;
  }

  try {
    const payload = await apiFetch("/api/auth/me", {
      headers: { Authorization: `Bearer ${auth.token}` },
    });

    return {
      token: auth.token,
      user: payload.user,
    };
  } catch (error) {
    if (error.status === 401) {
      clearStoredAuth();
      return null;
    }
    // A failed/cancelled network request does not invalidate the token.
    return auth;
  }
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
    const error = new Error(await readErrorMessage(response));
    error.status = response.status;
    throw error;
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
  const [me, health, models, provider] = await Promise.all([
    apiFetch("/api/auth/me", { headers: { Authorization: `Bearer ${token}` } }),
    apiFetch("/api/chat/health", { headers: { Authorization: `Bearer ${token}` } }),
    apiFetch("/api/chat/models", { headers: { Authorization: `Bearer ${token}` } }),
    apiFetch("/api/chat/provider", { headers: { Authorization: `Bearer ${token}` } }),
  ]);

  return {
    user: me.user,
    health,
    models: models.models || [],
    provider,
  };
};

export const fetchProviderConfig = (token) =>
  apiFetch("/api/chat/provider", { headers: { Authorization: `Bearer ${token}` } });

export const updateProviderConfig = (token, provider) =>
  apiFetch("/api/chat/provider", {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ provider }),
  });

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

export const hideAuthNavLinks = () => {
  document.querySelectorAll("[data-auth-nav]").forEach((link) => {
    link.hidden = true;
  });
};

export const hydratePublicChrome = async (options = {}) => {
  const {
    redirectIfAuthed = false,
    redirectTarget = "/chat",
    onAuthenticated,
  } = options;

  const auth = await verifyStoredAuth();
  if (!auth) {
    document.querySelectorAll("[data-auth-nav]").forEach((link) => {
      link.hidden = false;
    });
    return null;
  }

  if (redirectIfAuthed) {
    window.location.replace(redirectTarget);
    return { redirected: true };
  }

  document.querySelectorAll("[data-auth-nav]").forEach((link) => {
    link.hidden = true;
  });

  const publicUserChip = document.getElementById("public-user-chip");
  if (publicUserChip) {
    publicUserChip.hidden = false;
    publicUserChip.textContent = auth.user.email;
  }

  const workspaceLink = document.getElementById("workspace-link");
  if (workspaceLink) {
    workspaceLink.hidden = false;
  }

  const signoutButton = document.getElementById("public-signout-button");
  if (signoutButton) {
    wireSignOut(signoutButton, "/");
  }

  onAuthenticated?.(auth);
  return auth;
};

export const setNavState = (pathname) => {
  document.querySelectorAll("[data-nav]").forEach((link) => {
    link.classList.toggle("nav-link--active", link.getAttribute("href") === pathname);
  });
};
