import os
import shlex
import subprocess
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Union

# Path to commix.py relative to this file's location.
# Can be overridden with COMMIX_BIN env var for portability.
COMMIX_BIN = os.environ.get(
    "COMMIX_BIN",
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "tools",
        "commix",
        "commix.py",
    ),
)

ALLOWED_FLAGS = {
    # General
    "-v": "value",
    "--install": "bool",
    "--version": "bool",
    "--update": "bool",
    "--output-dir": "value",
    "-s": "value",  # session file
    "--flush-session": "bool",
    "--ignore-session": "bool",
    "-t": "value",  # traffic file
    "--time-limit": "value",
    "--batch": "bool",
    "--ignore-stdin": "bool",
    "--skip-heuristics": "bool",
    "--codec": "value",
    "--charset": "value",
    "--check-internet": "bool",
    "--answers": "value",

    # Target
    "-u": "value",
    "--url": "value",
    "--url-reload": "bool",
    "-l": "value",  # logfile
    "-m": "value",  # bulkfile
    "-r": "value",  # request file
    "--crawl": "value",
    "--crawl-exclude": "value",
    "-x": "value",  # sitemap
    "--method": "value",

    # Request
    "-d": "value",
    "--data": "value",
    "--host": "value",
    "--referer": "value",
    "--user-agent": "value",
    "--random-agent": "bool",
    "--param-del": "value",
    "--cookie": "value",
    "--cookie-del": "value",
    "--http1.0": "bool",
    "-H": "value",
    "--header": "value",
    "--headers": "value",
    "--proxy": "value",
    "--tor": "bool",
    "--tor-port": "value",
    "--tor-check": "bool",
    "--auth-url": "value",
    "--auth-data": "value",
    "--auth-type": "value",
    "--auth-cred": "value",
    "--ignore-code": "value",
    "--force-ssl": "bool",
    "--ignore-proxy": "bool",
    "--ignore-redirects": "bool",
    "--timeout": "value",
    "--retries": "value",
    "--drop-set-cookie": "bool",

    # Enumeration
    "--all": "bool",
    "--current-user": "bool",
    "--hostname": "bool",
    "--is-root": "bool",
    "--is-admin": "bool",
    "--sys-info": "bool",
    "--users": "bool",
    "--passwords": "bool",
    "--privileges": "bool",
    "--ps-version": "bool",

    # File access
    "--file-read": "value",
    "--file-write": "value",
    "--file-upload": "value",
    "--file-dest": "value",

    # Modules
    "--shellshock": "bool",

    # Injection
    "-p": "value",  # test parameter
    "--skip": "value",  # skip parameter
    "--suffix": "value",
    "--prefix": "value",
    "--technique": "value",
    "--skip-technique": "value",
    "--maxlen": "value",
    "--delay": "value",
    "--time-sec": "value",
    "--tmp-path": "value",
    "--web-root": "value",
    "--alter-shell": "value",
    "--os-cmd": "value",
    "--os": "value",
    "--tamper": "value",
    "--msf-path": "value",

    # Detection
    "--level": "value",
    "--skip-calc": "bool",
    "--skip-empty": "bool",
    "--failed-tries": "value",
    "--smart": "bool",

    # Miscellaneous
    "--ignore-dependencies": "bool",
    "--list-tampers": "bool",
    "--alert": "value",
    "--no-logging": "bool",
    "--purge": "bool",
    "--skip-waf": "bool",
    "--mobile": "bool",
    "--offline": "bool",
    "--wizard": "bool",
    "--disable-coloring": "bool",
}


@dataclass
class CommixResult:
    """Result object containing commix execution details."""
    command: List[str]
    returncode: int
    stdout: str
    stderr: str


def _build_args(options: Dict[str, Any]) -> List[str]:
    """
    Convert options dict to a list of command-line arguments.
    
    Example:
      {"--url": "http://localhost/...", "--batch": True, "-p": "ip"}
    """
    args: List[str] = []

    for k, v in options.items():
        if k not in ALLOWED_FLAGS:
            raise ValueError(f"Flag not allowed: {k}")

        kind = ALLOWED_FLAGS[k]
        if kind == "bool":
            if v:
                args.append(k)
        else:
            if v is None:
                continue
            # Force everything to string and reject newlines to prevent header injection
            sv = str(v)
            if "\n" in sv or "\r" in sv:
                raise ValueError(f"Invalid value for {k}: contains newline characters")
            args.extend([k, sv])

    return args


def run_commix_tool(
    *,
    url: Optional[str] = None,
    request_file: Optional[str] = None,
    data: Optional[str] = None,
    options: Optional[Dict[str, Any]] = None,
    cwd: Optional[str] = None,
    python_bin: str = "python",
    max_runtime_seconds: int = 600,
    interactive: bool = False
) -> CommixResult:
    """
    Run commix command injection scanner.
    
    Args:
        url: Target URL (use this OR request_file, not both)
        request_file: Path to HTTP request file (use this OR url, not both)
        data: POST data string (e.g., "ip=127.0.0.1&Submit=submit")
        options: Dictionary of commix flags and their values
        cwd: Working directory for commix execution
        python_bin: Python interpreter to use (default: "python")
        max_runtime_seconds: Maximum runtime in seconds
        interactive: If True, stream output directly to terminal (for CLI use)
        
    Returns:
        CommixResult object with command, returncode, stdout, and stderr
        
    Raises:
        ValueError: If neither url nor request_file provided, or if both are provided
        FileNotFoundError: If request_file doesn't exist
    """
    options = options or {}
    if not os.path.exists(COMMIX_BIN):
        raise FileNotFoundError(f"Commix entrypoint not found: {COMMIX_BIN}")

    # Require exactly one target mode
    if (url is None) == (request_file is None):
        raise ValueError("Provide exactly one of: url or request_file")

    if request_file is not None:
        if not os.path.exists(request_file):
            raise FileNotFoundError(f"Request file not found: {request_file}")
        options["-r"] = request_file
    else:
        options["--url"] = url

    if data is not None:
        options["--data"] = data

    # Enforce batch mode by default (agent-friendly, no prompts) - but not in interactive mode
    if not interactive:
        options.setdefault("--batch", True)

    # Build command: python commix.py [args]
    cmd = [python_bin, COMMIX_BIN] + _build_args(options)

    # Set working directory to commix folder if not specified
    if cwd is None:
        cwd = os.path.dirname(COMMIX_BIN)

    if interactive:
        # Interactive mode: stream output directly to terminal
        # This allows the user to interact with commix's shell
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            shell=False,
        )
        return CommixResult(
            command=cmd,
            returncode=proc.returncode,
            stdout="[Interactive mode - output streamed to terminal]",
            stderr="",
        )
    else:
        # Non-interactive mode: capture output for programmatic use
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=max_runtime_seconds,
            shell=False,
        )
        return CommixResult(
            command=cmd,
            returncode=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
        )


def scan_command_injection(
    *,
    url: Optional[str] = None,
    request_file: Optional[str] = None,
    data: Optional[str] = None,
    cookie: Optional[str] = None,
    parameter: Optional[str] = None,
    technique: Optional[str] = None,
    level: Optional[int] = None,
    web_root: Optional[str] = None,
    tamper: Optional[str] = None,
    os_cmd: Optional[str] = None,
    extra_options: Optional[Dict[str, Any]] = None,
    python_bin: str = "python",
    max_runtime_seconds: int = 999
) -> CommixResult:
    """
    High-level function to scan for command injection vulnerabilities.
    
    This is a convenience wrapper around run_commix_tool with common options.
    
    Args:
        url: Target URL with injection point
        request_file: Path to HTTP request file (alternative to url)
        data: POST data string
        cookie: Cookie header value
        parameter: Specific parameter to test (e.g., "ip")
        technique: Injection technique(s) to use (c=classic, e=eval-based, 
                   t=time-based, f=file-based)
        level: Test level 1-3 (higher = more tests)
        web_root: Web server document root (needed for file-based technique)
        tamper: Tamper script(s) to use (e.g., "space2ifs", "base64encode")
        os_cmd: Execute a single OS command
        extra_options: Additional commix options as dict
        python_bin: Python interpreter to use
        max_runtime_seconds: Maximum runtime in seconds
        
    Returns:
        CommixResult object

    """
    options = extra_options.copy() if extra_options else {}
    
    if cookie:
        options["--cookie"] = cookie
    if parameter:
        options["-p"] = parameter
    if technique:
        options["--technique"] = technique
    if level:
        options["--level"] = level
    if web_root:
        options["--web-root"] = web_root
    if tamper:
        options["--tamper"] = tamper
    if os_cmd:
        options["--os-cmd"] = os_cmd

    return run_commix_tool(
        url=url,
        request_file=request_file,
        data=data,
        options=options,
        python_bin=python_bin,
        max_runtime_seconds=max_runtime_seconds
    )


def enumerate_target(
    *,
    url: Optional[str] = None,
    request_file: Optional[str] = None,
    data: Optional[str] = None,
    cookie: Optional[str] = None,
    parameter: Optional[str] = None,
    technique: Optional[str] = None,
    web_root: Optional[str] = None,
    get_current_user: bool = False,
    get_hostname: bool = False,
    check_root: bool = False,
    check_admin: bool = False,
    get_sys_info: bool = False,
    get_users: bool = False,
    get_passwords: bool = False,
    get_all: bool = False,
    extra_options: Optional[Dict[str, Any]] = None,
    python_bin: str = "python",
    max_runtime_seconds: int = 999
) -> CommixResult:
    """
    Enumerate target system after confirming command injection.
    
    Args:
        url: Target URL
        request_file: Path to HTTP request file
        data: POST data string
        cookie: Cookie header value
        parameter: Specific parameter to test
        technique: Injection technique(s)
        web_root: Web server document root
        get_current_user: Retrieve current user name
        get_hostname: Retrieve hostname
        check_root: Check if current user has root privileges
        check_admin: Check if current user has admin privileges
        get_sys_info: Retrieve system information
        get_users: Retrieve system users
        get_passwords: Retrieve system user password hashes
        get_all: Retrieve everything
        extra_options: Additional commix options
        python_bin: Python interpreter to use
        max_runtime_seconds: Maximum runtime
        
    Returns:
        CommixResult object
    """
    options = extra_options.copy() if extra_options else {}
    
    if cookie:
        options["--cookie"] = cookie
    if parameter:
        options["-p"] = parameter
    if technique:
        options["--technique"] = technique
    if web_root:
        options["--web-root"] = web_root
    if get_current_user:
        options["--current-user"] = True
    if get_hostname:
        options["--hostname"] = True
    if check_root:
        options["--is-root"] = True
    if check_admin:
        options["--is-admin"] = True
    if get_sys_info:
        options["--sys-info"] = True
    if get_users:
        options["--users"] = True
    if get_passwords:
        options["--passwords"] = True
    if get_all:
        options["--all"] = True

    return run_commix_tool(
        url=url,
        request_file=request_file,
        data=data,
        options=options,
        python_bin=python_bin,
        max_runtime_seconds=max_runtime_seconds
    )


def read_remote_file(
    *,
    url: Optional[str] = None,
    request_file: Optional[str] = None,
    data: Optional[str] = None,
    cookie: Optional[str] = None,
    parameter: Optional[str] = None,
    technique: Optional[str] = None,
    web_root: Optional[str] = None,
    file_path: str,
    extra_options: Optional[Dict[str, Any]] = None,
    python_bin: str = "python",
    max_runtime_seconds: int = 999
) -> CommixResult:
    """
    Read a file from the target system.
    
    Args:
        url: Target URL
        request_file: Path to HTTP request file
        data: POST data string
        cookie: Cookie header value
        parameter: Specific parameter to test
        technique: Injection technique(s)
        web_root: Web server document root
        file_path: Path to the file to read on target system
        extra_options: Additional commix options
        python_bin: Python interpreter to use
        max_runtime_seconds: Maximum runtime
        
    Returns:
        CommixResult object
    """
    options = extra_options.copy() if extra_options else {}
    
    if cookie:
        options["--cookie"] = cookie
    if parameter:
        options["-p"] = parameter
    if technique:
        options["--technique"] = technique
    if web_root:
        options["--web-root"] = web_root
    options["--file-read"] = file_path

    return run_commix_tool(
        url=url,
        request_file=request_file,
        data=data,
        options=options,
        python_bin=python_bin,
        max_runtime_seconds=max_runtime_seconds
    )


def execute_command(
    *,
    url: Optional[str] = None,
    request_file: Optional[str] = None,
    data: Optional[str] = None,
    cookie: Optional[str] = None,
    parameter: Optional[str] = None,
    technique: Optional[str] = None,
    web_root: Optional[str] = None,
    command: str,
    extra_options: Optional[Dict[str, Any]] = None,
    python_bin: str = "python",
    max_runtime_seconds: int = 999
) -> CommixResult:
    """
    Execute a single OS command on the target system.
    
    Args:
        url: Target URL
        request_file: Path to HTTP request file
        data: POST data string
        cookie: Cookie header value
        parameter: Specific parameter to test
        technique: Injection technique(s)
        web_root: Web server document root
        command: The OS command to execute
        extra_options: Additional commix options
        python_bin: Python interpreter to use
        max_runtime_seconds: Maximum runtime
        
    Returns:
        CommixResult object
    """
    options = extra_options.copy() if extra_options else {}
    
    if cookie:
        options["--cookie"] = cookie
    if parameter:
        options["-p"] = parameter
    if technique:
        options["--technique"] = technique
    if web_root:
        options["--web-root"] = web_root
    options["--os-cmd"] = command

    return run_commix_tool(
        url=url,
        request_file=request_file,
        data=data,
        options=options,
        python_bin=python_bin,
        max_runtime_seconds=max_runtime_seconds
    )


# Example usage and testing
if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Command Injection Scanner using Commix",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Scan using URL and POST data
  python cmd.py -u "http://localhost/vulnerabilities/exec/" -d "ip=127.0.0.1&Submit=Submit" --cookie "security=low; PHPSESSID=xxx"
  
  # Scan using request file
  python cmd.py -r req
  
  # High security DVWA (file-based technique)
  python cmd.py -u "http://localhost/vulnerabilities/exec/" -d "ip=127.0.0.1&Submit=Submit" --cookie "security=high; PHPSESSID=xxx" --technique f --web-root "/var/www/html/"
  
  # Execute specific command
  python cmd.py -u "http://localhost/vulnerabilities/exec/" -d "ip=127.0.0.1&Submit=Submit" --cookie "security=low; PHPSESSID=xxx" --os-cmd "whoami"
        """
    )
    
    # Target options
    target_group = parser.add_argument_group("Target")
    target_group.add_argument("-u", "--url", help="Target URL")
    target_group.add_argument("-r", "--request-file", help="Load HTTP request from a file")
    target_group.add_argument("-d", "--data", help="POST data string")
    
    # Request options
    request_group = parser.add_argument_group("Request")
    request_group.add_argument("--cookie", help="HTTP Cookie header")
    request_group.add_argument("-p", "--parameter", help="Testable parameter(s)")
    request_group.add_argument("--headers", help="Extra headers")
    
    # Injection options
    inject_group = parser.add_argument_group("Injection")
    inject_group.add_argument("--technique", help="Injection technique(s): c=classic, e=eval, t=time, f=file")
    inject_group.add_argument("--level", type=int, choices=[1, 2, 3], help="Level of tests (1-3)")
    inject_group.add_argument("--web-root", help="Web server document root")
    inject_group.add_argument("--tamper", help="Tamper script(s)")
    inject_group.add_argument("--prefix", help="Injection payload prefix")
    inject_group.add_argument("--suffix", help="Injection payload suffix")
    inject_group.add_argument("--os-cmd", help="Execute a single OS command")
    
    # Enumeration options
    enum_group = parser.add_argument_group("Enumeration")
    enum_group.add_argument("--current-user", action="store_true", help="Retrieve current user")
    enum_group.add_argument("--hostname", action="store_true", help="Retrieve hostname")
    enum_group.add_argument("--is-root", action="store_true", help="Check root privileges")
    enum_group.add_argument("--sys-info", action="store_true", help="Retrieve system info")
    
    # Misc options
    misc_group = parser.add_argument_group("Miscellaneous")
    misc_group.add_argument("-v", "--verbose", type=int, choices=[0, 1, 2, 3, 4], default=0, help="Verbosity level")
    misc_group.add_argument("--flush-session", action="store_true", help="Flush session files")
    misc_group.add_argument("--skip-waf", action="store_true", help="Skip WAF detection")
    misc_group.add_argument("--auto", action="store_true", help="Automatic mode (auto-answers prompts, streams output)")
    misc_group.add_argument("--batch", action="store_true", help="Batch mode (auto-answers, captures output for programmatic use)")
    
    args = parser.parse_args()
    
    # Build extra options from CLI args
    extra_options: Dict[str, Any] = {}
    if args.verbose:
        extra_options["-v"] = args.verbose
    if args.headers:
        extra_options["--headers"] = args.headers
    if args.prefix:
        extra_options["--prefix"] = args.prefix
    if args.suffix:
        extra_options["--suffix"] = args.suffix
    if args.flush_session:
        extra_options["--flush-session"] = True
    if args.skip_waf:
        extra_options["--skip-waf"] = True
    if args.current_user:
        extra_options["--current-user"] = True
    if args.hostname:
        extra_options["--hostname"] = True
    if args.is_root:
        extra_options["--is-root"] = True
    if args.sys_info:
        extra_options["--sys-info"] = True
    # Add injection-related options
    if args.cookie:
        extra_options["--cookie"] = args.cookie
    if args.parameter:
        extra_options["-p"] = args.parameter
    if args.technique:
        extra_options["--technique"] = args.technique
    if args.level:
        extra_options["--level"] = args.level
    if args.web_root:
        extra_options["--web-root"] = args.web_root
    if args.tamper:
        extra_options["--tamper"] = args.tamper
    if args.os_cmd:
        extra_options["--os-cmd"] = args.os_cmd
    
    try:
        # Modes:
        # - Default: Interactive with prompts (manual mode)
        # - --auto: Auto-answers prompts but streams output (automated scanning)
        # - --batch: Auto-answers and captures output (for programmatic/AI use)
        
        if args.auto or args.batch:
            extra_options["--batch"] = True
        
        # Stream output unless --batch is used
        interactive = not args.batch
        
        res = run_commix_tool(
            url=args.url,
            request_file=args.request_file,
            data=args.data,
            options=extra_options if extra_options else None,
            interactive=interactive,
        )
        
        if not interactive:
            # Only print summary in batch mode (output was captured)
            print("=" * 60)
            print("COMMAND:")
            print(" ".join(shlex.quote(x) for x in res.command))
            print("=" * 60)
            print("\nSTDOUT:")
            print(res.stdout)
            if res.stderr:
                print("\nSTDERR:")
                print(res.stderr)
            print("=" * 60)
            print(f"Return code: {res.returncode}")
        
    except ValueError as e:
        print(f"Error: {e}")
        parser.print_help()
    except FileNotFoundError as e:
        print(f"Error: {e}")
    except subprocess.TimeoutExpired:
        print("Error: Command timed out")
