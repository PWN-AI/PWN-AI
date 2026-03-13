"""Comprehensive tool argument knowledge base for all PwnAI tools.

Each tool's documentation is structured as a list of documents, where each
document covers a logical group of arguments (e.g., detection flags, enumeration
flags). This granularity lets the vector search return highly relevant chunks
rather than entire tool manuals.
"""

from __future__ import annotations

from typing import Dict, List


def _doc(tool: str, category: str, content: str) -> Dict[str, str]:
    return {
        "tool": tool,
        "category": category,
        "content": content.strip(),
    }


def get_tool_documents() -> List[Dict[str, str]]:
    """Return all tool knowledge documents for vector indexing."""
    docs: List[Dict[str, str]] = []
    docs.extend(_sql_injection_docs())
    docs.extend(_command_injection_docs())
    docs.extend(_nuclei_xss_docs())
    docs.extend(_brute_force_docs())
    return docs


# ═══════════════════════════════════════════════════════════════
# SQL INJECTION (sqlmap)
# ═══════════════════════════════════════════════════════════════

def _sql_injection_docs() -> List[Dict[str, str]]:
    return [
        _doc("sql_injection", "overview", """
Tool: sql_injection (sqlmap wrapper)
Description: Automated SQL injection detection and exploitation tool.
Available actions: scan, enumerate, dump (action is informational — actual behavior is controlled by flags in args.options).
Target modes: url, request_file
The args.options dictionary is passed DIRECTLY as sqlmap CLI flags. If a flag is not included, it will NOT be used.
--batch is the ONLY flag automatically added (to prevent interactive prompts). Everything else must come from the LLM.
CRITICAL: The target URL MUST contain a query parameter for sqlmap to test (e.g., http://target/page?id=1). Without a parameter, sqlmap has nothing to inject into and will fail.
"""),

        _doc("sql_injection", "targeting_flags", """
Tool: sql_injection — Targeting Flags
These flags control how sqlmap connects to the target.

-u (value): Target URL with injectable parameter. Example: -u "http://target/page?id=1"
-r (value): Path to HTTP request file captured from a proxy. Example: -r "request.txt"
--data (value): POST data body string. Example: --data "id=1&Submit=Submit". Use this for POST-based injection when the URL has no query parameters.
--cookie (value): HTTP cookie string for authenticated scanning. Example: --cookie "PHPSESSID=abc123; security=low"
--headers (value): Extra HTTP headers. Example: --headers "X-Custom: value"
--method (value): Force HTTP method. Example: --method POST
--user-agent (value): Custom User-Agent header. Example: --user-agent "Mozilla/5.0"
--referer (value): Custom Referer header. Example: --referer "http://target/"
--host (value): Custom Host header value. Example: --host "target.com"
"""),

        _doc("sql_injection", "detection_flags", """
Tool: sql_injection — Detection & Technique Flags
These flags control how aggressively sqlmap tests for SQL injection.

-p (value): Specific parameter to test for injection. Example: -p "id". Use when URL has multiple parameters and you want to focus on one.
--level (value): Detection thoroughness level 1-5. Default: 1. Higher levels test more injection points (cookies at level 2, User-Agent/Referer at level 3+). Example: --level 3
--risk (value): Risk of tests 1-3. Default: 1. Higher risk includes OR-based and UPDATE/INSERT tests. Risk 2 adds time-based heavy queries. Risk 3 adds OR-based tests. Example: --risk 2
--technique (value): SQL injection techniques to test. String of letters: B=Boolean-based blind, E=Error-based, U=UNION query, S=Stacked queries, T=Time-based blind, Q=Inline queries. Default: BEUSTQ (all). Example: --technique "BEU"
--tamper (value): Tamper script(s) for WAF/filter bypass. Example: --tamper "space2comment,between". Common scripts: space2comment, between, randomcase, charencode, equaltolike, base64encode.
--random-agent (bool): Use random User-Agent header to avoid detection. Example: --random-agent true
--proxy (value): Use HTTP proxy. Example: --proxy "http://127.0.0.1:8080"
--tor (bool): Use Tor anonymity network. Example: --tor true
--check-tor (bool): Check that Tor is being used properly. Example: --check-tor true
"""),

        _doc("sql_injection", "enumeration_flags", """
Tool: sql_injection — Enumeration Flags
These flags enumerate database structure. Use AFTER confirming SQL injection exists.

--dbs (bool): List all databases on the server. This is typically the FIRST enumeration step.
--tables (bool): List tables. Use with -D to target a specific database. Example: --tables true -D "dvwa"
--columns (bool): List columns of a table. Use with -D and -T. Example: --columns true -D "dvwa" -T "users"
--schema (bool): Enumerate entire DBMS schema (all databases, tables, columns). Comprehensive but slow.
--current-db (bool): Show the name of the current database being used by the application.
--current-user (bool): Show the current database user.
--is-dba (bool): Check if the current user has DBA (admin) privileges.
--banner (bool): Show the DBMS version banner (e.g., "MySQL 5.7.34").
--users (bool): List all database users.
--passwords (bool): Dump password hashes for database users.
--privileges (bool): Show privileges for each database user.
--roles (bool): Show roles for each database user.
"""),

        _doc("sql_injection", "selection_filters", """
Tool: sql_injection — Database/Table/Column Selection Filters
These flags narrow the scope of enumeration and extraction.

-D (value): Target a specific database by name. Example: -D "dvwa". Required when using --tables, --columns, or --dump on a specific database.
-T (value): Target a specific table by name. Example: -T "users". Required when using --columns or --dump on a specific table.
-C (value): Target specific columns by name (comma-separated). Example: -C "user,password". Use with --dump to extract only certain columns.
--exclude-sysdbs (bool): Skip system databases (information_schema, mysql, etc.) when using --dump-all or --schema. Highly recommended to avoid dumping irrelevant system data.
"""),

        _doc("sql_injection", "extraction_flags", """
Tool: sql_injection — Data Extraction (Dump) Flags
These flags extract actual data from the database.

--dump (bool): Dump entries from a table. Use with -D and -T to target specific database and table. Example: --dump true -D "dvwa" -T "users"
--dump-all (bool): Dump ALL tables from ALL databases. Very aggressive — use --exclude-sysdbs with it. Example: --dump-all true --exclude-sysdbs true
--where (value): WHERE condition to filter dumped rows. Example: --where "id>5"
--start (value): First row to dump (0-indexed). Example: --start 0
--stop (value): Last row to dump. Example: --stop 10
--first (value): First query output character to retrieve (for blind injection). Example: --first 1
--last (value): Last query output character to retrieve. Example: --last 50
--limit (value): Limit number of rows to dump. Example: --limit 100
--order-by (value): Column to order results by. Example: --order-by "id"
"""),

        _doc("sql_injection", "tuning_flags", """
Tool: sql_injection — Performance & Tuning Flags
These flags control sqlmap's performance and behavior.

--batch (bool): Auto-answer all prompts with defaults. AUTOMATICALLY added by the adapter — do NOT include manually.
--flush-session (bool): Clear previous session data and start fresh. Use when re-scanning the same target.
--fresh-queries (bool): Ignore stored query results and re-execute.
--threads (value): Number of parallel threads 1-10. Example: --threads 5. Higher = faster but more noisy.
--timeout (value): Connection timeout in seconds. Example: --timeout 30
--retries (value): Number of connection retries. Example: --retries 3
--delay (value): Delay in seconds between requests. Example: --delay 0.5
--safe-url (value): URL to visit between tests to maintain session. Example: --safe-url "http://target/dashboard"
--safe-post (value): POST data for safe URL. Example: --safe-post "data=1"
--safe-freq (value): Visit safe URL every N requests. Example: --safe-freq 10
--safe-time (value): Delay in seconds between safe URL visits. Example: --safe-time 1
--output-dir (value): Custom output directory. Example: --output-dir "/tmp/sqlmap_output"
-v (value): Verbosity level 0-6. Default 1. Level 2+ shows HTTP traffic. Example: -v 2
"""),

        _doc("sql_injection", "advanced_flags", """
Tool: sql_injection — Advanced Flags
These flags enable advanced exploitation capabilities.

--os-shell (bool): Prompt for an interactive OS shell (requires DBA privileges and writable web directory).
--sql-shell (bool): Prompt for an interactive SQL shell.
--forms (bool): Parse and test forms found on the target page. Useful when the page has HTML forms instead of URL parameters.
--crawl (value): Crawl the site starting from target URL to specified depth. Example: --crawl 2
--dbms (value): Force specific DBMS type. Example: --dbms "MySQL". Use when you know the backend database. Speeds up detection by skipping irrelevant tests.
--prefix (value): Injection payload prefix string. Example: --prefix "')". Use to close existing SQL context.
--suffix (value): Injection payload suffix string. Example: --suffix "-- -"
"""),

        _doc("sql_injection", "common_patterns", """
Tool: sql_injection — Common Usage Patterns

PATTERN 1 - Initial scan for SQL injection:
  action: "scan"
  args.options: {"--level": 2, "--risk": 1, "--cookie": "PHPSESSID=xxx; security=low"}
  target URL must have ?param=value

PATTERN 2 - Enumerate databases after confirming SQLi:
  action: "enumerate"
  args.options: {"--dbs": true, "--cookie": "PHPSESSID=xxx; security=low"}

PATTERN 3 - Enumerate tables in a specific database:
  action: "enumerate"
  args.options: {"--tables": true, "-D": "dvwa", "--cookie": "..."}

PATTERN 4 - Dump specific table data:
  action: "dump"
  args.options: {"--dump": true, "-D": "dvwa", "-T": "users", "--cookie": "..."}

PATTERN 5 - Dump all non-system data:
  action: "dump"
  args.options: {"--dump-all": true, "--exclude-sysdbs": true, "--cookie": "..."}

PATTERN 6 - POST-based injection (form data):
  action: "scan"
  target URL: "http://target/page" (no query params needed)
  args.options: {"--data": "username=admin&password=test", "-p": "username", "--level": 3, "--risk": 2}
"""),
    ]


# ═══════════════════════════════════════════════════════════════
# COMMAND INJECTION (commix)
# ═══════════════════════════════════════════════════════════════

def _command_injection_docs() -> List[Dict[str, str]]:
    return [
        _doc("command_injection", "overview", """
Tool: command_injection (commix wrapper)
Description: Automated command injection detection and exploitation tool.
Available actions: scan, enumerate, execute_command, read_remote_file
Target modes: url, request_file
The target URL should point to a page that accepts user input which is passed to an OS command.
CRITICAL: For command injection, you typically need POST data with an injectable parameter (e.g., data="ip=127.0.0.1&Submit=submit" parameter="ip").
"""),

        _doc("command_injection", "scan_action", """
Tool: command_injection — Action: scan (scan_command_injection)
Scans for command injection vulnerabilities. This is the primary detection action.

Function arguments (passed directly as kwargs in args):
  url (string): Target URL with injection point. Example: "http://target/cmd_page"
  request_file (string): Alternative to url — path to HTTP request file.
  data (string): POST data string with injectable parameter. Example: "ip=127.0.0.1&Submit=submit"
  cookie (string): Cookie header value for authenticated scanning. Example: "PHPSESSID=abc; security=low"
  parameter (string): Which specific parameter to test for injection. Example: "ip"
  technique (string): Injection technique(s) to use. c=classic, e=eval-based, t=time-based, f=file-based. Example: "c" for classic only, "cet" for classic+eval+time.
  level (integer): Test level 1-3. Higher = more tests but slower. Example: 2
  web_root (string): Web server document root path (needed for file-based technique). Example: "/var/www/html"
  tamper (string): Tamper script(s) for WAF bypass. Example: "space2ifs"
  os_cmd (string): Execute a single OS command during scan. Example: "whoami"
  extra_options (dict): Additional commix CLI flags as key-value pairs. Example: {"--timeout": 30}
  max_runtime_seconds (integer): Maximum scan duration. Default: 999
"""),

        _doc("command_injection", "enumerate_action", """
Tool: command_injection — Action: enumerate (enumerate_target)
Enumerates system information after confirming command injection.

Function arguments (passed directly as kwargs in args):
  url (string): Target URL
  request_file (string): Alternative to url
  data (string): POST data string. Example: "ip=127.0.0.1&Submit=submit"
  cookie (string): Cookie header value
  parameter (string): Injectable parameter name. Example: "ip"
  technique (string): Injection technique. Example: "c"
  web_root (string): Web server document root

Enumeration boolean flags (set to true to activate):
  get_current_user (bool): Retrieve current OS user name
  get_hostname (bool): Retrieve system hostname
  check_root (bool): Check if current user has root privileges
  check_admin (bool): Check if current user has admin privileges
  get_sys_info (bool): Retrieve system information (OS, architecture)
  get_users (bool): Retrieve list of system users (/etc/passwd)
  get_passwords (bool): Retrieve password hashes (/etc/shadow if readable)
  get_all (bool): Retrieve ALL of the above in one shot

  extra_options (dict): Additional commix CLI flags
  max_runtime_seconds (integer): Maximum duration. Default: 999
"""),

        _doc("command_injection", "execute_command_action", """
Tool: command_injection — Action: execute_command
Executes a specific OS command on the target system via confirmed injection point.

Function arguments (passed directly as kwargs in args):
  url (string): Target URL
  request_file (string): Alternative to url
  data (string): POST data string. Example: "ip=127.0.0.1&Submit=submit"
  cookie (string): Cookie header value
  parameter (string): Injectable parameter name
  technique (string): Injection technique
  command (string): The OS command to execute. Example: "whoami", "cat /etc/passwd", "id", "ls -la /var/www"

  extra_options (dict): Additional commix CLI flags
  max_runtime_seconds (integer): Maximum duration
"""),

        _doc("command_injection", "read_remote_file_action", """
Tool: command_injection — Action: read_remote_file
Reads a file from the remote system via confirmed injection point.

Function arguments (passed directly as kwargs in args):
  url (string): Target URL
  request_file (string): Alternative to url
  data (string): POST data string
  cookie (string): Cookie header value
  parameter (string): Injectable parameter name
  technique (string): Injection technique
  file_path (string): Full path to the file to read. Example: "/etc/passwd", "/etc/shadow", "/var/www/html/config.php"

  extra_options (dict): Additional commix CLI flags
  max_runtime_seconds (integer): Maximum duration
"""),

        _doc("command_injection", "allowed_cli_flags", """
Tool: command_injection — All Allowed CLI Flags (for extra_options dict)
These flags can be passed in the extra_options dictionary:

General: -v (verbosity), --batch (auto-answer), --flush-session, --ignore-session, --time-limit, --skip-heuristics, --codec, --charset
Target: --url, --url-reload, -l (logfile), -m (bulkfile), -r (request file), --crawl, --crawl-exclude, -x (sitemap), --method
Request: -d/--data (POST data), --host, --referer, --user-agent, --random-agent, --param-del, --cookie, --cookie-del, -H/--header/--headers, --proxy, --tor, --tor-port, --tor-check, --auth-url, --auth-data, --auth-type, --auth-cred, --ignore-code, --force-ssl, --ignore-redirects, --timeout, --retries, --drop-set-cookie
Enumeration: --all, --current-user, --hostname, --is-root, --is-admin, --sys-info, --users, --passwords, --privileges, --ps-version
File access: --file-read, --file-write, --file-upload, --file-dest
Injection: -p (test parameter), --skip, --suffix, --prefix, --technique, --skip-technique, --maxlen, --delay, --time-sec, --tmp-path, --web-root, --alter-shell, --os-cmd, --os, --tamper
Detection: --level (1-3), --skip-calc, --skip-empty, --failed-tries, --smart
Modules: --shellshock
"""),

        _doc("command_injection", "common_patterns", """
Tool: command_injection — Common Usage Patterns

PATTERN 1 - Scan for command injection on a form:
  action: "scan"
  args: {"data": "ip=127.0.0.1&Submit=submit", "parameter": "ip", "cookie": "PHPSESSID=xxx; security=low"}

PATTERN 2 - Scan with specific technique:
  action: "scan"
  args: {"data": "ip=127.0.0.1&Submit=submit", "parameter": "ip", "technique": "c", "level": 2}

PATTERN 3 - Enumerate after confirming injection:
  action: "enumerate"
  args: {"data": "ip=127.0.0.1&Submit=submit", "parameter": "ip", "get_all": true, "cookie": "..."}

PATTERN 4 - Execute OS command:
  action: "execute_command"
  args: {"data": "ip=127.0.0.1&Submit=submit", "parameter": "ip", "command": "cat /etc/passwd", "cookie": "..."}

PATTERN 5 - Read a remote file:
  action: "read_remote_file"
  args: {"data": "ip=127.0.0.1&Submit=submit", "parameter": "ip", "file_path": "/etc/passwd", "cookie": "..."}
"""),
    ]


# ═══════════════════════════════════════════════════════════════
# NUCLEI XSS
# ═══════════════════════════════════════════════════════════════

def _nuclei_xss_docs() -> List[Dict[str, str]]:
    return [
        _doc("nuclei_xss", "overview", """
Tool: nuclei_xss (Nuclei XSS scanner wrapper)
Description: Template-based XSS vulnerability scanner using ProjectDiscovery's Nuclei engine.
Available actions: scan
Target modes: url, request_file
The tool runs nuclei with XSS-specific templates against the target URL(s).
Allowlist is auto-derived from the target URL's hostname.
"""),

        _doc("nuclei_xss", "scan_action", """
Tool: nuclei_xss — Action: scan
Runs nuclei XSS templates against the target.

Function arguments (passed in args):
  tags (string): Nuclei template tags to run. Default: "xss". Can be comma-separated for multiple tags. Example: "xss,injection"
  timeout (integer): Scan timeout in seconds. Default: 300. Example: 600
  cookie (string): Cookie string for authenticated scanning. Example: "PHPSESSID=abc123; security=low"
  headers (list of strings): Extra HTTP headers to include. Example: ["X-Custom: value", "Authorization: Bearer token"]
  allow_host (string or list): Allowed host(s) for scanning. Auto-derived from target URL if not set.
  keep_raw (bool): Keep raw nuclei JSONL output files. Default: false
  out (string): Output file path for normalized findings JSON. Default: "xss_findings.json"
"""),

        _doc("nuclei_xss", "common_patterns", """
Tool: nuclei_xss — Common Usage Patterns

PATTERN 1 - Basic XSS scan:
  action: "scan"
  args: {}  (defaults are sufficient — tags=xss, timeout=300)

PATTERN 2 - Authenticated XSS scan:
  action: "scan"
  args: {"cookie": "PHPSESSID=abc123; security=low", "timeout": 600}

PATTERN 3 - Scan with custom headers:
  action: "scan"
  args: {"cookie": "...", "headers": ["X-Forwarded-For: 127.0.0.1"]}

PATTERN 4 - Extended tag scan:
  action: "scan"
  args: {"tags": "xss,injection,ssti", "timeout": 900}
"""),
    ]


# ═══════════════════════════════════════════════════════════════
# BRUTE FORCE
# ═══════════════════════════════════════════════════════════════

def _brute_force_docs() -> List[Dict[str, str]]:
    return [
        _doc("brute_force", "overview", """
Tool: brute_force (HTTP brute force credential tester)
Description: Automated HTTP login brute force tool with wordlist generation and mutation capabilities.
Available actions: scan_login, run_bruteforce
Target modes: url, request_file
scan_login is a convenience wrapper around run_bruteforce with common defaults.
run_bruteforce provides full control over all options.
"""),

        _doc("brute_force", "scan_login_action", """
Tool: brute_force — Action: scan_login
High-level convenience scan for weak credentials. Simplified interface.

Function arguments (passed directly as kwargs in args):
  url (string): Target login URL. Example: "http://target/login"
  request_file (string): Alternative to url — path to HTTP request file
  cookie (string): Cookie header value for session. Example: "PHPSESSID=abc; security=low"
  username_param (string): Form parameter name for username. Default: "username". Example: "user", "email", "login"
  password_param (string): Form parameter name for password. Default: "password". Example: "pass", "pwd"
  usernames (list of strings): List of usernames to try. Example: ["admin", "user", "test"]
  passwords (list of strings): List of passwords to try. Example: ["password", "admin", "123456", "letmein"]
  username_file (string): Path to username wordlist file. Example: "/path/to/usernames.txt"
  password_file (string): Path to password wordlist file. Example: "/path/to/passwords.txt"
  success_indicator (string): Text in response body indicating successful login. Example: "Welcome", "Dashboard", "Logout"
  failure_indicator (string): Text in response body indicating failed login. Example: "Invalid", "incorrect", "failed"
  max_runtime_seconds (integer): Overall timeout. Default: 600
"""),

        _doc("brute_force", "run_bruteforce_action", """
Tool: brute_force — Action: run_bruteforce
Full-featured brute force with all options. Use when scan_login is too limited.

Function arguments (passed directly as kwargs in args):
  url (string): Target login URL
  request_file (string): Alternative to url

  Wordlist options:
    usernames (list of strings): Username list. Example: ["admin", "root"]
    passwords (list of strings): Password list. Example: ["password", "123456"]
    username_file (string): Path to username wordlist file
    password_file (string): Path to password wordlist file

  Prefix/suffix modification:
    username_prefix (string): Prepend to all usernames. Default: ""
    username_suffix (string): Append to all usernames. Default: ""
    password_prefix (string): Prepend to all passwords. Default: ""
    password_suffix (string): Append to all passwords. Default: ""

  Request options:
    username_param (string): Username form parameter name. Default: "username"
    password_param (string): Password form parameter name. Default: "password"
    method (string): HTTP method — "GET" or "POST". Auto-detected from request_file.
    data (string): POST data template with {username} and {password} placeholders. Example: "user={username}&pass={password}&submit=Login"
    cookie (string): Cookie header value
    headers (dict): Additional HTTP headers as key-value pairs. Example: {"X-Custom": "value"}

  Detection options:
    success_indicator (string): Text indicating successful login in response body
    failure_indicator (string): Text indicating failed login in response body
    success_codes (list of integers): HTTP status codes indicating success. Example: [200, 302]
    failure_codes (list of integers): HTTP status codes indicating failure. Example: [401, 403]

  Behavior options:
    stop_on_success (bool): Stop after finding first valid credential. Default: true
    timeout (integer): Per-request timeout in seconds. Default: 10
    delay (float): Delay between requests in seconds. Default: 0
    max_runtime_seconds (integer): Overall timeout. Default: 600

  Logging options:
    verbosity (integer): 0=quiet, 1=normal, 2=verbose, 3=debug. Default: 1
    log_file (string): Path to log file for attempt history
    log_format (string): "json" or "csv". Default: "json"
    progress_interval (integer): Show progress every N attempts. Default: 10
"""),

        _doc("brute_force", "common_patterns", """
Tool: brute_force — Common Usage Patterns

PATTERN 1 - Quick credential scan with common defaults:
  action: "scan_login"
  args: {"usernames": ["admin", "user"], "passwords": ["password", "admin", "123456"], "cookie": "PHPSESSID=xxx; security=low"}

PATTERN 2 - Full brute force with failure detection:
  action: "run_bruteforce"
  args: {"usernames": ["admin"], "passwords": ["password", "admin", "123456", "letmein", "welcome"], "failure_indicator": "Username and/or password incorrect", "username_param": "username", "password_param": "password", "method": "GET"}

PATTERN 3 - Using custom param names:
  action: "scan_login"
  args: {"username_param": "email", "password_param": "pass", "usernames": ["admin@target.com"], "passwords": ["password", "admin123"]}

PATTERN 4 - POST-based with data template:
  action: "run_bruteforce"
  args: {"method": "POST", "data": "username={username}&password={password}&Login=Login", "usernames": ["admin"], "passwords": ["password", "admin"]}
"""),
    ]
