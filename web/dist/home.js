import { hydratePublicChrome, initTheme, setNavState, wireThemeToggle } from "/common.js";

initTheme();
setNavState("/");
wireThemeToggle(document.getElementById("theme-toggle"));

hydratePublicChrome({
  onAuthenticated: (auth) => {
    const primary = document.getElementById("home-primary-link");
    const secondary = document.getElementById("home-secondary-link");
    const userLine = document.getElementById("home-user-line");

    if (primary) {
      primary.textContent = "Open chat";
      primary.href = "/chat";
    }

    if (secondary) {
      secondary.textContent = "Open session";
      secondary.href = "/session";
    }

    if (userLine) {
      userLine.hidden = false;
      userLine.textContent = `Signed in as ${auth.user.email}`;
    }
  },
});
