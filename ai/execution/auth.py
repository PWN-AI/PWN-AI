from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse
import re

try:
    import requests
except ImportError:
    requests = None

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

LOGIN_INDICATORS = [
    "login",
    "signin",
    "sign-in",
    "log-in",
    "auth",
    "sso",
    "session",
    "account/login",
    "users/sign_in",
    "wp-login",
    "administrator",
    # Avoid following links that would invalidate sessions
    "logout",
    "log-out",
    "signout",
    "sign-out",
    "logoff",
]

DANGEROUS_INDICATORS = [
    "logout",
    "log-out",
    "signout",
    "sign-out",
    "logoff",
    "delete",
    "remove",
    "destroy",
    "reset",
    "setup",
    "install",
    "uninstall",
    "drop",
    "truncate",
    "shutdown",
    "reboot",
]

USERNAME_CANDIDATES = {
    "username",
    "user",
    "email",
    "login",
    "userid",
    "name",
    "admin",
    "uname",
    "user_name",
    "login_id",
    "account",
    "log",
    "user_login",
    "mod-login-username",
    "id",
}

PASSWORD_CANDIDATES = {
    "password",
    "pass",
    "passwd",
    "pwd",
    "passwrd",
    "user_pass",
    "mod-login-password",
}

SUBMIT_CANDIDATES = {
    "submit",
    "login",
    "signin",
    "sign-in",
    "log-in",
    "commit",
    "log on",
    "Log in",
    "Sign in",
    "Login",
    "Submit",
    "wp-submit",
}

SUCCESS_MARKERS = [
    "logout",
    "dashboard",
    "welcome",
    "my account",
    "profile",
    "admin",
    "control panel",
    "home",
    "index",
    "security level",
    "vulnerability",
    "csrf token",
    "setup",
    "database setup",
    "wordpress",
    "joomla",
    "drupal",
    "wp-admin",
    "administrator",
    "management",
    "panel",
    "backend",
]

FAILURE_MARKERS = [
    "invalid",
    "incorrect",
    "try again",
    "login failed",
    "wrong password",
    "authentication failed",
    "access denied",
    "unauthorized",
    "error",
    "invalid username",
    "invalid password",
    "credentials",
    "rejected",
]


@dataclass
class AuthLoginResult:
    attempted: bool
    success: bool
    verified: bool = False
    login_url: str = ""
    final_url: str = ""
    cookie: str = ""
    status_code: int = 0
    message: str = ""
    tried_urls: List[str] = field(default_factory=list)
    discovered_endpoints: List[str] = field(default_factory=list)
    authenticated_page_html: str = ""
    authenticated_page_url: str = ""
    navigation_structure: Dict[str, List[str]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        return {
            "attempted": self.attempted,
            "success": self.success,
            "verified": self.verified,
            "login_url": self.login_url,
            "final_url": self.final_url,
            "cookie": self.cookie,
            "status_code": self.status_code,
            "message": self.message,
            "tried_urls": self.tried_urls,
            "discovered_endpoints": self.discovered_endpoints,
            "authenticated_page_html": self.authenticated_page_html[:10000] if self.authenticated_page_html else "",
            "authenticated_page_url": self.authenticated_page_url,
            "navigation_structure": self.navigation_structure,
        }


def _base_origin(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


def _session_cookie_header(session: "requests.Session") -> str:  # type: ignore[name-defined]
    cookies = []
    for cookie in session.cookies:
        cookies.append(f"{cookie.name}={cookie.value}")
    return "; ".join(cookies)


def _is_login_like_url(url: str) -> bool:
    url_l = (url or "").lower()
    if not url_l:
        return False
    parsed = urlparse(url_l)
    hay = f"{parsed.path}?{parsed.query}"
    return any(ind in hay for ind in LOGIN_INDICATORS)


def _is_state_changing_url(url: str) -> bool:
    url_l = (url or "").lower()
    if not url_l:
        return False
    parsed = urlparse(url_l)
    hay = f"{parsed.path}?{parsed.query}"
    return any(ind in hay for ind in DANGEROUS_INDICATORS)


def _looks_logged_in(url: str, body: str) -> bool:
    url_l = (url or "").lower()
    body_l = (body or "").lower()

    if "login" in url_l and "logout" not in body_l:
        return False

    has_success = any(x in body_l for x in SUCCESS_MARKERS)
    has_failure = any(x in body_l for x in FAILURE_MARKERS)

    if "logout" in body_l:
        return True
    if "login" in body_l and has_failure:
        return False

    return has_success and not has_failure


def _candidate_login_urls(target_url: str, explicit_login_url: Optional[str]) -> List[str]:
    base = _base_origin(target_url)
    candidates: List[str] = []
    if explicit_login_url:
        candidates.append(explicit_login_url)

    candidates.append(target_url)
    candidates.append(base)

    common_paths = [
        "/login",
        "/login.php",
        "/signin",
        "/sign-in",
        "/auth/login",
        "/account/login",
        "/user/login",
        "/users/sign_in",
        "/session/new",
        "/admin/login",
        "/admin/login.php",
        "/admin",
        "/wp-login.php",
        "/administrator/index.php",
    ]
    for path in common_paths:
        candidates.append(f"{base}{path}")

    seen = set()
    ordered: List[str] = []
    for c in candidates:
        if c and c not in seen:
            ordered.append(c)
            seen.add(c)
    return ordered


def _extract_csrf_tokens(soup: Any) -> List[str]:
    tokens: List[str] = []
    if not soup:
        return tokens
    for meta in soup.find_all("meta"):
        name = (meta.get("name") or meta.get("id") or "").lower()
        content = (meta.get("content") or "").strip()
        if content and "csrf" in name:
            tokens.append(content)
    return tokens


def _extract_forms(html: str, base_url: str) -> List[Dict[str, Any]]:
    if BeautifulSoup is None:
        return []

    soup = BeautifulSoup(html or "", "html.parser")
    forms = []
    for form in soup.find_all("form"):
        action = (form.get("action") or "").strip()
        method = (form.get("method") or "post").lower()
        form_id = (form.get("id") or "").strip()
        form_class = " ".join(form.get("class") or [])

        inputs = []
        for inp in form.find_all("input"):
            name = (inp.get("name") or "").strip()
            if not name:
                continue
            typ = (inp.get("type") or "text").lower()
            val = inp.get("value") or ""
            inputs.append(
                {
                    "name": name,
                    "type": typ,
                    "value": val,
                    "id": (inp.get("id") or "").strip(),
                    "placeholder": (inp.get("placeholder") or "").strip(),
                    "checked": bool(inp.get("checked")),
                    "required": bool(inp.get("required")),
                }
            )

        selects = []
        for sel in form.find_all("select"):
            name = (sel.get("name") or "").strip()
            if not name:
                continue
            value = ""
            option = sel.find("option", selected=True)
            if option is None:
                option = sel.find("option")
            if option is not None:
                value = option.get("value") or option.get_text(strip=True) or ""
            selects.append({"name": name, "value": value})

        textareas = []
        for ta in form.find_all("textarea"):
            name = (ta.get("name") or "").strip()
            if not name:
                continue
            textareas.append({"name": name, "value": ta.get_text(strip=True)})

        buttons = []
        for btn in form.find_all("button"):
            btn_type = (btn.get("type") or "submit").lower()
            btn_name = (btn.get("name") or "").strip()
            btn_value = (btn.get("value") or "").strip() or btn.get_text(strip=True)
            buttons.append({"type": btn_type, "name": btn_name, "value": btn_value})

        forms.append(
            {
                "action": urljoin(base_url, action) if action else base_url,
                "method": method,
                "inputs": inputs,
                "selects": selects,
                "textareas": textareas,
                "buttons": buttons,
                "form_id": form_id,
                "form_class": form_class,
                "soup": soup,
            }
        )

    return forms


def _score_login_form(form: Dict[str, Any], username_field: str, password_field: str) -> int:
    score = 0
    action = (form.get("action") or "").lower()
    form_id = (form.get("form_id") or "").lower()
    form_class = (form.get("form_class") or "").lower()

    inputs = form.get("inputs", [])
    has_password = any(i.get("type") == "password" for i in inputs)
    if has_password:
        score += 5

    def _matches_user(inp: Dict[str, Any]) -> bool:
        name = (inp.get("name") or "").lower()
        inp_id = (inp.get("id") or "").lower()
        placeholder = (inp.get("placeholder") or "").lower()
        return (
            name in USERNAME_CANDIDATES
            or inp_id in USERNAME_CANDIDATES
            or placeholder in USERNAME_CANDIDATES
            or name == username_field.lower()
        )

    if any(_matches_user(i) for i in inputs):
        score += 3

    if any(ind in action for ind in LOGIN_INDICATORS):
        score += 2
    if any(ind in form_id for ind in LOGIN_INDICATORS):
        score += 1
    if any(ind in form_class for ind in LOGIN_INDICATORS):
        score += 1

    for inp in inputs:
        if (inp.get("type") or "").lower() in {"submit", "button"}:
            val = (inp.get("value") or "").lower()
            if any(cand.lower() in val for cand in SUBMIT_CANDIDATES):
                score += 2

    for btn in form.get("buttons", []):
        if (btn.get("type") or "").lower() == "submit":
            val = (btn.get("value") or "").lower()
            if any(cand.lower() in val for cand in SUBMIT_CANDIDATES):
                score += 2

    return score


def _select_login_form(
    html: str,
    base_url: str,
    username_field: str,
    password_field: str,
) -> Optional[Dict[str, Any]]:
    forms = _extract_forms(html, base_url)
    if not forms:
        return None

    scored = []
    for form in forms:
        score = _score_login_form(form, username_field, password_field)
        if score > 0:
            scored.append((score, form))

    if not scored:
        return None

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1]


def _guess_username_field(inputs: List[Dict[str, Any]], username_field: str) -> str:
    for inp in inputs:
        name = (inp.get("name") or "").lower()
        if name == username_field.lower():
            return inp.get("name") or username_field
    for inp in inputs:
        name = (inp.get("name") or "").lower()
        inp_id = (inp.get("id") or "").lower()
        placeholder = (inp.get("placeholder") or "").lower()
        if name in USERNAME_CANDIDATES or inp_id in USERNAME_CANDIDATES or placeholder in USERNAME_CANDIDATES:
            return inp.get("name") or username_field
    for inp in inputs:
        typ = (inp.get("type") or "").lower()
        if typ in {"text", "email"}:
            return inp.get("name") or username_field
    return username_field


def _guess_password_field(inputs: List[Dict[str, Any]], password_field: str) -> str:
    for inp in inputs:
        typ = (inp.get("type") or "").lower()
        if typ == "password":
            return inp.get("name") or password_field
    for inp in inputs:
        name = (inp.get("name") or "").lower()
        inp_id = (inp.get("id") or "").lower()
        if name == password_field.lower() or inp_id == password_field.lower():
            return inp.get("name") or password_field
    for inp in inputs:
        name = (inp.get("name") or "").lower()
        if name in PASSWORD_CANDIDATES:
            return inp.get("name") or password_field
    return password_field


def _guess_submit_field(form: Dict[str, Any], submit_field: Optional[str]) -> Optional[str]:
    if submit_field:
        return submit_field

    for inp in form.get("inputs", []):
        typ = (inp.get("type") or "").lower()
        name = (inp.get("name") or "").strip()
        val = (inp.get("value") or "").strip().lower()
        if typ in {"submit", "button"} and name:
            if any(cand.lower() in val for cand in SUBMIT_CANDIDATES):
                return name

    for btn in form.get("buttons", []):
        if (btn.get("type") or "").lower() == "submit":
            name = (btn.get("name") or "").strip()
            val = (btn.get("value") or "").strip().lower()
            if name and any(cand.lower() in val for cand in SUBMIT_CANDIDATES):
                return name

    return None


def _build_login_payload(
    form: Dict[str, Any],
    username: str,
    password: str,
    username_field: str,
    password_field: str,
    submit_field: Optional[str],
    submit_value: str,
    extra_form_fields: Optional[Dict[str, str]],
) -> Dict[str, str]:
    inputs = form.get("inputs", [])
    payload: Dict[str, str] = {}

    user_key = _guess_username_field(inputs, username_field)
    pass_key = _guess_password_field(inputs, password_field)
    submit_key = _guess_submit_field(form, submit_field)

    for inp in inputs:
        name = (inp.get("name") or "").strip()
        if not name:
            continue
        typ = (inp.get("type") or "text").lower()
        val = inp.get("value") or ""

        if name == user_key:
            payload[name] = username
            continue
        if name == pass_key:
            payload[name] = password
            continue

        if typ == "hidden":
            payload[name] = val
        elif typ in {"checkbox", "radio"}:
            if inp.get("checked"):
                payload[name] = val or "on"
        elif typ in {"submit", "button", "file"}:
            continue
        else:
            if val:
                payload[name] = val

    for sel in form.get("selects", []):
        name = (sel.get("name") or "").strip()
        if name and name not in payload:
            payload[name] = sel.get("value") or ""

    for ta in form.get("textareas", []):
        name = (ta.get("name") or "").strip()
        if name and name not in payload:
            payload[name] = ta.get("value") or ""

    if extra_form_fields:
        for k, v in extra_form_fields.items():
            payload[str(k)] = str(v)

    if submit_key:
        payload[submit_key] = submit_value

    # Try to fill empty csrf fields from meta tags if present
    soup = form.get("soup")
    tokens = _extract_csrf_tokens(soup)
    if tokens:
        token = tokens[0]
        for key in list(payload.keys()):
            if "csrf" in key.lower() and not payload[key]:
                payload[key] = token

    return payload


def _verify_auth_strict(
    session: "requests.Session",  # type: ignore[name-defined]
    *,
    target_url: str,
    probe_url: Optional[str] = None,
    success_indicator: Optional[str] = None,
    failure_indicator: Optional[str] = None,
    timeout_seconds: int = 15,
    verify_tls: bool = True,
) -> bool:
    if requests is None:
        return False

    probe_target = probe_url or target_url
    try:
        resp = session.get(probe_target, timeout=timeout_seconds, allow_redirects=True, verify=verify_tls)
    except Exception:
        return False

    final_url = resp.url or ""
    body = resp.text or ""

    if success_indicator and success_indicator.lower() in body.lower():
        return True
    if failure_indicator and failure_indicator.lower() in body.lower():
        return False

    if resp.status_code >= 400:
        return False

    if _is_login_like_url(final_url) and not _looks_logged_in(final_url, body):
        return False

    if _session_cookie_header(session) and _looks_logged_in(final_url, body):
        return True

    return False


def _extract_navigation_structure(soup: Any, base_url: str) -> Dict[str, List[str]]:
    navigation = {
        "menus": [],
        "sidebars": [],
        "dropdowns": [],
        "vulnerability_links": [],
        "admin_links": [],
        "api_endpoints": [],
    }

    if not soup:
        return navigation

    menu_selectors = [
        "nav",
        "menu",
        "#menu",
        ".menu",
        "#nav",
        ".nav",
        "#sidebar",
        ".sidebar",
        "#navigation",
        ".navigation",
        "[id*=menu]",
        "[class*=menu]",
        "[id*=nav]",
        "[class*=nav]",
    ]

    for selector in menu_selectors:
        try:
            elements = soup.select(selector)
            for elem in elements:
                links = []
                for a in elem.find_all("a", href=True):
                    href = a["href"]
                    full_url = urljoin(base_url, href)
                    text = a.get_text(strip=True)
                    links.append(f"{text}: {full_url}")
                if links:
                    navigation["menus"].extend(links[:20])
        except Exception:
            continue

    vuln_patterns = ["vulnerabilit", "sqli", "xss", "csrf", "lfi", "rfi", "cmd", "exec", "upload"]
    for a in soup.find_all("a", href=True):
        href = a["href"].lower()
        text = a.get_text(strip=True)
        full_url = urljoin(base_url, a["href"])
        if any(pattern in href or pattern in text.lower() for pattern in vuln_patterns):
            navigation["vulnerability_links"].append(f"{text}: {full_url}")

    admin_patterns = ["admin", "setup", "config", "security", "settings"]
    for a in soup.find_all("a", href=True):
        href = a["href"].lower()
        text = a.get_text(strip=True)
        if any(pattern in href for pattern in admin_patterns):
            full_url = urljoin(base_url, a["href"])
            navigation["admin_links"].append(f"{text}: {full_url}")

    for key in navigation:
        seen = set()
        unique = []
        for item in navigation[key]:
            if item not in seen:
                seen.add(item)
                unique.append(item)
        navigation[key] = unique[:30]

    return navigation


def _deep_crawl_endpoints(
    session: "requests.Session",  # type: ignore[name-defined]
    *,
    start_url: str,
    base_origin: str,
    max_depth: int = 2,
    timeout_seconds: int = 15,
    verify_tls: bool = True,
) -> Tuple[List[str], str, Dict[str, List[str]]]:
    if requests is None or BeautifulSoup is None:
        return [], "", {}

    discovered = set()
    to_visit = [(start_url, 0)]
    visited = set()
    all_html = []
    all_navigation = {
        "menus": [],
        "sidebars": [],
        "vulnerability_links": [],
        "admin_links": [],
    }

    while to_visit:
        current_url, depth = to_visit.pop(0)
        if current_url in visited or depth > max_depth:
            continue
        if _is_login_like_url(current_url) or _is_state_changing_url(current_url):
            continue
        visited.add(current_url)

        try:
            resp = session.get(current_url, timeout=timeout_seconds, allow_redirects=True, verify=verify_tls)
            if resp.status_code != 200:
                continue

            soup = BeautifulSoup(resp.text or "", "html.parser")
            if depth == 0:
                all_html.append(resp.text)

            nav = _extract_navigation_structure(soup, current_url)
            for key in all_navigation:
                all_navigation[key].extend(nav.get(key, []))

            for a in soup.find_all("a", href=True):
                href = a["href"]
                full_url = urljoin(resp.url, href)
                parsed = urlparse(full_url)

                if parsed.netloc and parsed.netloc != urlparse(base_origin).netloc:
                    continue
                if _is_login_like_url(full_url) or _is_state_changing_url(full_url):
                    continue

                if full_url not in discovered:
                    discovered.add(full_url)
                    if depth < max_depth and full_url not in visited:
                        to_visit.append((full_url, depth + 1))

            for form in soup.find_all("form"):
                action = form.get("action", "")
                if action:
                    full_url = urljoin(resp.url, action)
                    if not _is_login_like_url(full_url) and not _is_state_changing_url(full_url):
                        discovered.add(full_url)

            for iframe in soup.find_all("iframe", src=True):
                src = iframe["src"]
                full_url = urljoin(resp.url, src)
                if not _is_login_like_url(full_url) and not _is_state_changing_url(full_url):
                    discovered.add(full_url)

            scripts = soup.find_all("script")
            for script in scripts:
                if script.string:
                    api_patterns = re.findall(r"['\"]([^'\"]*(?:api|ajax|json)[^'\"]*)['\"]", script.string, re.IGNORECASE)
                    for match in api_patterns:
                        full_url = urljoin(resp.url, match)
                        if not _is_login_like_url(full_url) and not _is_state_changing_url(full_url):
                            discovered.add(full_url)

        except Exception as e:
            print(f"[Auth Crawl] Error crawling {current_url}: {e}")
            continue

    for key in all_navigation:
        seen = set()
        unique = []
        for item in all_navigation[key]:
            if item not in seen:
                seen.add(item)
                unique.append(item)
        all_navigation[key] = unique[:30]

    return list(discovered), "\n".join(all_html), all_navigation


def refresh_session_cookie(
    *,
    target_url: str,
    current_cookie: str,
    username: str = "",
    password: str = "",
    login_url: Optional[str] = None,
    timeout_seconds: int = 10,
    verify_tls: bool = True,
    allow_relogin: bool = False,
) -> str:
    """Lightweight session keepalive.

    Sends a single GET with the current cookie to refresh the session.
    If the response lands on a login page (session expired), returns the
    existing cookie unchanged by default. Optional re-login can be enabled
    via allow_relogin.
    """
    if requests is None or not current_cookie:
        return current_cookie

    session = requests.Session()
    session.headers.setdefault("User-Agent", DEFAULT_UA)

    for pair in current_cookie.split(";"):
        pair = pair.strip()
        if "=" in pair:
            name, value = pair.split("=", 1)
            session.cookies.set(name.strip(), value.strip())

    try:
        resp = session.get(target_url, timeout=timeout_seconds, allow_redirects=True, verify=verify_tls)
        if resp.status_code >= 400:
            return current_cookie

        final_url = resp.url or ""
        body = resp.text or ""
        refreshed = _session_cookie_header(session)

        if _is_login_like_url(final_url) and not _looks_logged_in(final_url, body):
            if not allow_relogin:
                return current_cookie
            if not username or not password:
                return current_cookie

            print("[Session Keepalive] Session expired, performing quick re-login...")
            result = attempt_pre_auth(
                target_url=target_url,
                username=username,
                password=password,
                login_url=login_url,
                timeout_seconds=timeout_seconds,
                verify_tls=verify_tls,
            )
            if result.success and result.cookie:
                print("[Session Keepalive] Re-login succeeded. New cookie obtained.")
                return result.cookie
            print(f"[Session Keepalive] Re-login failed: {result.message}")
            return current_cookie

        return refreshed or current_cookie
    except Exception as e:
        print(f"[Session Keepalive] Ping failed ({e}), proceeding with existing cookie.")
        return current_cookie


def attempt_pre_auth(
    *,
    target_url: str,
    username: str,
    password: str,
    login_url: Optional[str] = None,
    username_field: str = "username",
    password_field: str = "password",
    submit_field: Optional[str] = None,
    submit_value: str = "Login",
    extra_form_fields: Optional[Dict[str, str]] = None,
    timeout_seconds: int = 15,
    verify_tls: bool = True,
    auth_probe_url: Optional[str] = None,
    auth_success_indicator: Optional[str] = None,
    auth_failure_indicator: Optional[str] = None,
    crawl_depth: int = 2,
) -> AuthLoginResult:
    if requests is None:
        return AuthLoginResult(
            attempted=False,
            success=False,
            message="requests library is not installed.",
        )
    if not username or not password:
        return AuthLoginResult(
            attempted=False,
            success=False,
            message="Missing username/password; skipping pre-auth.",
        )

    session = requests.Session()
    session.headers.setdefault("User-Agent", DEFAULT_UA)

    tried_urls: List[str] = []
    last_status = 0
    last_url = ""

    print(f"\n[Auth] Starting authentication attempt for: {target_url}")
    print(f"[Auth] Username: {username}, Password: {'*' * len(password)}")

    candidates = _candidate_login_urls(target_url, login_url)
    print(f"[Auth] Will try {len(candidates)} candidate URLs: {candidates}")

    for candidate in candidates:
        tried_urls.append(candidate)
        try:
            print(f"\n[Auth] Fetching: {candidate}")
            page = session.get(candidate, timeout=timeout_seconds, allow_redirects=True, verify=verify_tls)
            print(f"[Auth] Response: {page.status_code} at {page.url}")
        except Exception as e:
            print(f"[Auth] Failed to fetch {candidate}: {e}")
            continue

        last_status = page.status_code
        last_url = page.url

        if _looks_logged_in(page.url, page.text):
            cookie = _session_cookie_header(session)
            if cookie:
                print("[Auth] Already authenticated based on page heuristics.")
                verified = _verify_auth_strict(
                    session,
                    target_url=target_url,
                    probe_url=auth_probe_url,
                    success_indicator=auth_success_indicator,
                    failure_indicator=auth_failure_indicator,
                    timeout_seconds=timeout_seconds,
                    verify_tls=verify_tls,
                )
                if verified:
                    discovered_endpoints, full_html, nav_structure = _deep_crawl_endpoints(
                        session,
                        start_url=page.url,
                        base_origin=_base_origin(target_url),
                        max_depth=max(0, int(crawl_depth)),
                        timeout_seconds=timeout_seconds,
                        verify_tls=verify_tls,
                    )
                    return AuthLoginResult(
                        attempted=True,
                        success=True,
                        verified=True,
                        login_url=page.url,
                        final_url=page.url,
                        cookie=cookie,
                        status_code=page.status_code,
                        message="Already authenticated (heuristic and strict verification).",
                        tried_urls=tried_urls,
                        discovered_endpoints=discovered_endpoints,
                        authenticated_page_html=full_html,
                        authenticated_page_url=page.url,
                        navigation_structure=nav_structure,
                    )

        form = _select_login_form(page.text, page.url, username_field, password_field)
        if form is None:
            print("[Auth] No login form detected on this page.")
            continue

        payload = _build_login_payload(
            form,
            username,
            password,
            username_field,
            password_field,
            submit_field,
            submit_value,
            extra_form_fields,
        )

        post_target = form.get("action") or page.url
        method = (form.get("method") or "post").lower()

        print(f"[Auth] Form action: {post_target}")
        print(f"[Auth] Method: {method}")
        print(f"[Auth] Payload keys: {list(payload.keys())}")

        try:
            headers = {"Referer": page.url}
            if method == "get":
                login_resp = session.get(post_target, params=payload, timeout=timeout_seconds, allow_redirects=True, verify=verify_tls, headers=headers)
            else:
                login_resp = session.post(post_target, data=payload, timeout=timeout_seconds, allow_redirects=True, verify=verify_tls, headers=headers)
            print(f"[Auth] Login response: {login_resp.status_code} at {login_resp.url}")
        except Exception as e:
            print(f"[Auth] Login request failed: {e}")
            continue

        last_status = login_resp.status_code
        last_url = login_resp.url
        cookie = _session_cookie_header(session)
        print(f"[Auth] Session cookie: {cookie[:50] if cookie else 'None'}...")

        looks_logged = _looks_logged_in(login_resp.url, login_resp.text)
        print(f"[Auth] Looks logged in (heuristic): {looks_logged}")

        if not cookie:
            print("[Auth] WARNING: No session cookie received")
            continue

        verified = _verify_auth_strict(
            session,
            target_url=target_url,
            probe_url=auth_probe_url,
            success_indicator=auth_success_indicator,
            failure_indicator=auth_failure_indicator,
            timeout_seconds=timeout_seconds,
            verify_tls=verify_tls,
        )
        print(f"[Auth] Strict verification: {verified}")

        if verified or looks_logged:
            print("[Auth] SUCCESS: Authentication verified, starting deep crawl...")
            base = _base_origin(target_url)
            discovered_endpoints, full_html, nav_structure = _deep_crawl_endpoints(
                session,
                start_url=login_resp.url,
                base_origin=base,
                max_depth=max(0, int(crawl_depth)),
                timeout_seconds=timeout_seconds,
                verify_tls=verify_tls,
            )

            msg = "Pre-auth succeeded with strict verification." if verified else "Pre-auth succeeded (heuristic)."
            print(f"[Auth] Discovered {len(discovered_endpoints)} endpoints via deep crawl")
            print(f"[Auth] Captured {len(full_html)} characters of HTML")
            print(f"[Auth] Navigation menus found: {len(nav_structure.get('menus', []))}")
            print(f"[Auth] Vulnerability links found: {len(nav_structure.get('vulnerability_links', []))}")

            return AuthLoginResult(
                attempted=True,
                success=True,
                verified=verified,
                login_url=page.url,
                final_url=login_resp.url,
                cookie=cookie,
                status_code=login_resp.status_code,
                message=msg,
                tried_urls=tried_urls,
                discovered_endpoints=discovered_endpoints,
                authenticated_page_html=full_html,
                authenticated_page_url=login_resp.url,
                navigation_structure=nav_structure,
            )
        else:
            print("[Auth] Login attempt did not pass verification checks")

    print(f"\n[Auth] FAILED: All {len(tried_urls)} login attempts failed")
    return AuthLoginResult(
        attempted=True,
        success=False,
        login_url=last_url,
        final_url=last_url,
        status_code=last_status,
        message="Pre-auth attempt failed; no valid authenticated session detected. Tried: " + ", ".join(tried_urls),
        tried_urls=tried_urls,
    )
