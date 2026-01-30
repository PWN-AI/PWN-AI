# Command Injection Module

This module provides a wrapper around [Commix](https://github.com/commixproject/commix) for automated command injection vulnerability scanning.

## Files

- `cmd.py` - Main command injection scanner module
- `req` - Sample HTTP request file for DVWA (low security)
- `req_medium` - Sample HTTP request file for DVWA (medium security)
- `req_high` - Sample HTTP request file for DVWA (high security)

## Quick Start

```bash
# Run automatic scan (no prompts, streams output)
python cmd.py -r req --auto

# Or with URL directly
python cmd.py -u "http://localhost/vulnerabilities/exec/" \
    -d "ip=127.0.0.1&Submit=Submit" \
    --cookie "security=low; PHPSESSID=xxx" --auto
```

## Execution Modes

| Mode | Flag | Description |
|------|------|-------------|
| **Manual** | (none) | Interactive mode with prompts |
| **Auto** | `--auto` | Auto-answers prompts, streams output to terminal |
| **Batch** | `--batch` | Auto-answers prompts, captures output (for AI/programmatic use) |

## Usage

### As a Python Module

```python
from cmd import run_commix_tool, scan_command_injection, execute_command, enumerate_target

# Basic scan (for programmatic use)
result = scan_command_injection(
    url="http://target/vulnerable.php",
    data="ip=127.0.0.1&Submit=Submit",
    cookie="session=your_session_id",
    parameter="ip"
)

# Execute a specific command
result = execute_command(
    url="http://target/vulnerable.php",
    data="ip=127.0.0.1&Submit=Submit",
    cookie="session=your_session_id",
    command="whoami"
)

# Enumerate target system
result = enumerate_target(
    url="http://target/vulnerable.php",
    data="ip=127.0.0.1&Submit=Submit",
    cookie="session=your_session_id",
    get_current_user=True,
    get_hostname=True
)

# Low-level access with interactive mode
result = run_commix_tool(
    url="http://target/vulnerable.php",
    data="ip=127.0.0.1&Submit=Submit",
    options={"--cookie": "session=xxx", "-p": "ip"},
    interactive=True  # Streams output to terminal
)
```

### Command Line Interface

```bash
# Automatic scan (recommended for most use cases)
python cmd.py -r req --auto

# Manual interactive mode (with prompts)
python cmd.py -r req

# Batch mode (captures output, for scripts/automation)
python cmd.py -r req --batch

# With URL and POST data
python cmd.py -u "http://localhost/vulnerabilities/exec/" \
    -d "ip=127.0.0.1&Submit=Submit" \
    --cookie "security=low; PHPSESSID=xxx" --auto

# Execute single command
python cmd.py -u "http://target/page.php" -d "cmd=test" --os-cmd "whoami" --auto

# Enumerate system info
python cmd.py -r req --current-user --hostname --sys-info --auto
```

## Testing with DVWA

### Setup

1. Ensure DVWA is running on localhost
2. Login to DVWA and get your PHPSESSID from browser cookies
3. Update the `req` file with your session ID, or use URL mode with `--cookie`

### Low Security

```bash
python cmd.py -r req --auto
```

### Medium Security

Medium security filters `&&` and `;` characters. Commix automatically adapts.

```bash
python cmd.py -r req_medium --auto
```

### High Security

High security requires file-based technique with web root path.

```bash
python cmd.py -r req_high --auto --technique f --web-root "/var/www/html/"
```

## Injection Techniques

Commix supports multiple techniques (`--technique`):

- `c` - Classic/Results-based command injection
- `e` - Dynamic code evaluation
- `t` - Time-based blind command injection  
- `f` - File-based command injection

## Tamper Scripts

Use tamper scripts to bypass filters (`--tamper`):

- `space2ifs` - Replace spaces with $IFS
- `space2htab` - Replace spaces with horizontal tabs
- `base64encode` - Base64 encode the payload
- `hexencode` - Hex encode the payload
- `uninitializedvariable` - Insert uninitialized variables to evade keyword detection

Example:
```bash
python cmd.py -u "http://target/page.php" -d "cmd=test" --tamper "space2ifs"
```

## Available Options

Run `python cmd.py --help` for full options list.

### Target Options
- `-u`, `--url` - Target URL
- `-r`, `--request-file` - Load HTTP request from file
- `-d`, `--data` - POST data string

### Request Options
- `--cookie` - HTTP Cookie header
- `-p`, `--parameter` - Specific parameter(s) to test
- `--headers` - Extra headers

### Injection Options
- `--technique` - Injection technique(s)
- `--level` - Test level 1-3 (higher = more tests)
- `--web-root` - Web server document root
- `--tamper` - Tamper script(s)
- `--prefix` - Payload prefix
- `--suffix` - Payload suffix
- `--os-cmd` - Execute single OS command

### Enumeration Options
- `--current-user` - Get current username
- `--hostname` - Get system hostname
- `--is-root` - Check root privileges
- `--sys-info` - Get system information

### Miscellaneous Options
- `-v` - Verbosity level (0-4)
- `--auto` - Automatic mode (auto-answers prompts, streams output)
- `--batch` - Batch mode (auto-answers, captures output for programmatic use)
- `--flush-session` - Flush session files for current target
- `--skip-waf` - Skip WAF/IPS detection
