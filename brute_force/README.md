# Brute Force Scanner

Advanced HTTP login brute force scanner with wordlist generation, mutations, and detailed logging.

## Features

- **Wordlist Generation**
  - Load from file
  - Generate from character sets (digits, lowercase, uppercase, special, etc.)
  - Prefix/suffix support
  - Case mutations (upper, lower, capitalize, toggle)
  - Leetspeak substitutions (a→4, e→3, o→0, etc.)
  - Number appending (years, common sequences)
  - Custom transformations

- **Request Handling**
  - HTTP GET/POST form brute force  
  - Request file support (Burp Suite format)
  - Automatic CSRF token extraction
  - Custom headers and cookies

- **Detection Options**
  - Success/failure string indicators
  - HTTP status code detection
  - Automatic common pattern detection

- **Advanced Logging**
  - Multiple verbosity levels (quiet, normal, verbose, debug)
  - JSON log file output
  - CSV log file output
  - Real-time progress and statistics
  - Response time tracking
  - Per-attempt detailed logs

## Usage

### Basic Usage

```python
from brute import run_bruteforce

result = run_bruteforce(
    url="http://localhost/vulnerabilities/brute/",
    cookie="PHPSESSID=abc123; security=low",
    username_param="username",
    password_param="password",
    usernames=["admin", "user"],
    passwords=["password", "123456", "admin"],
    method="GET",
)
print("Found:", result.credentials_found)
```

### Using WordlistGenerator

```python
from brute import run_bruteforce, WordlistGenerator

# Password generator with mutations
pw_gen = (WordlistGenerator()
    .add_words(["password", "admin", "letmein"])
    .add_case_mutations()           # password, PASSWORD, Password
    .add_leet_mutations()           # p4ssword, p@ssword, pa55word
    .add_number_suffix(["1", "123", "!"]))  # password1, password123, password!

result = run_bruteforce(
    url="http://target.com/login",
    usernames=["admin"],
    password_generator=pw_gen,
)
```

### Character Set Generation

```python
from brute import WordlistGenerator

# Generate 4-digit PINs
pin_gen = WordlistGenerator().generate_charset(
    charset="digits",      # Only 0-9
    min_length=4,
    max_length=4,
    limit=1000            # Limit to first 1000
)

# Generate alphanumeric passwords
alpha_gen = WordlistGenerator().generate_charset(
    charset="alphanumeric",  # a-z, A-Z, 0-9
    min_length=3,
    max_length=4,
)

# Custom charset
custom_gen = WordlistGenerator().generate_charset(
    charset="abc123!@#",   # Custom characters
    min_length=2,
    max_length=3,
)
```

### Predefined Character Sets

| Name | Characters |
|------|------------|
| `digits` | 0-9 |
| `lower` | a-z |
| `upper` | A-Z |
| `letters` | a-zA-Z |
| `alphanumeric` | a-zA-Z0-9 |
| `special` | !@#$%^&*()_+-=[]{}|;':\",./<>? |
| `all` | alphanumeric + special |

### Prefix and Suffix

```python
from brute import run_bruteforce

# Add prefix/suffix to all passwords
result = run_bruteforce(
    url="http://target.com/login",
    usernames=["admin"],
    passwords=["pass", "admin"],
    password_prefix="company_",    # company_pass, company_admin
    password_suffix="2024",        # company_pass2024, company_admin2024
)

# Or using WordlistGenerator
gen = (WordlistGenerator()
    .add_words(["pass", "admin"])
    .set_prefix("company_")
    .set_suffix("2024"))
```

### Advanced Logging

```python
from brute import run_bruteforce, BruteForceLogger

# JSON logging with verbose output
result = run_bruteforce(
    url="http://target.com/login",
    usernames=["admin"],
    passwords=["pass1", "pass2", "pass3"],
    verbosity=BruteForceLogger.VERBOSITY_VERBOSE,  # Show all attempts
    log_file="bruteforce.json",
    log_format="json",
    progress_interval=5,  # Show progress every 5 attempts
)

# CSV logging for spreadsheet analysis
result = run_bruteforce(
    url="http://target.com/login",
    usernames=["admin"],
    passwords=["pass1", "pass2"],
    log_file="results.csv",
    log_format="csv",
)

# Access statistics
print(f"Total attempts: {result.stats['total_attempts']}")
print(f"Duration: {result.stats['duration_seconds']}s")
print(f"Rate: {result.stats['rate_per_second']}/s")
```

### Verbosity Levels

| Level | Value | Description |
|-------|-------|-------------|
| QUIET | 0 | Only show found credentials |
| NORMAL | 1 | Show progress and found credentials |
| VERBOSE | 2 | Show all attempts |
| DEBUG | 3 | Show everything including errors |

### Using Request Files

```python
result = run_bruteforce(
    request_file="login.req",
    usernames=["admin"],
    passwords=["password", "admin123"],
)
```

Request file format (Burp Suite style):
```
GET /login?username=test&password=test HTTP/1.1
Host: target.com
Cookie: session=abc123
User-Agent: Mozilla/5.0

```

### Detection Options

```python
# String-based detection
result = run_bruteforce(
    url="http://target.com/login",
    success_indicator="Welcome back",    # Success if response contains this
    failure_indicator="Invalid password", # Success if response doesn't contain this
)

# HTTP status code detection  
result = run_bruteforce(
    url="http://target.com/login",
    success_codes=[302, 200],   # These codes indicate success
    failure_codes=[401, 403],   # These codes indicate failure
)
```

### Rate Limiting

```python
result = run_bruteforce(
    url="http://target.com/login",
    usernames=["admin"],
    passwords=["pass1", "pass2", "pass3"],
    delay=0.5,         # 0.5 second delay between requests
    timeout=10,        # 10 second request timeout
    max_runtime_seconds=600,  # Stop after 10 minutes
)
```

## Result Object

```python
@dataclass
class BruteForceResult:
    command: List[str]              # Description of the attack
    returncode: int                 # 0 if credentials found
    stdout: str                     # Summary message
    stderr: str                     # Error messages
    credentials_found: List[Dict]   # Found credentials
    stats: Dict[str, Any]           # Attack statistics
    logs: List[AttemptLog]          # Detailed attempt logs

@dataclass  
class AttemptLog:
    timestamp: str
    username: str
    password: str
    status_code: int
    response_time_ms: float
    content_length: int
    success: bool
    error: Optional[str]
```

## JSON Log Format

```json
{
  "stats": {
    "total_attempts": 100,
    "successful": 1,
    "failed": 99,
    "errors": 0,
    "duration_seconds": 45.23,
    "rate_per_second": 2.21,
    "start_time": "2024-01-15T10:30:00",
    "end_time": "2024-01-15T10:30:45"
  },
  "attempts": [
    {
      "timestamp": "2024-01-15T10:30:01",
      "username": "admin",
      "password": "password",
      "status_code": 200,
      "response_time_ms": 156.32,
      "content_length": 4523,
      "success": true,
      "error": null
    }
  ]
}
```

## Requirements

```
requests
beautifulsoup4
```

Install with:
```bash
pip install requests beautifulsoup4
```

## Legal Disclaimer

This tool is intended for authorized security testing only. Always obtain proper authorization before testing any system you do not own.
