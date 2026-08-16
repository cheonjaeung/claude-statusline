#!/usr/bin/env python3

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import date

RESET = "\x1b[0m"
BLUE = "\x1b[0;34m"
GREEN = "\x1b[0;32m"
YELLOW = "\x1b[0;33m"
CYAN = "\x1b[0;36m"
RED = "\x1b[0;31m"
GRAY = "\x1b[0;90m"
MAGENTA = "\x1b[0;35m"
WHITE = "\x1b[0;37m"
PINK = "\x1b[0;95m"
GOLD = "\x1b[38;5;136m"

SEP = f"{GRAY} | {RESET}"

CCUSAGE_CACHE_FILE = "/tmp/claude_statusline_ccusage_cache"
CCUSAGE_CACHE_TTL = 10  # seconds

def scale_num(n):
    """Split a number into (value, unit), e.g. 1500 -> (1.5, 'K')."""
    n = float(n)
    if n >= 1_000_000_000:
        return n / 1_000_000_000, "B"
    if n >= 1_000_000:
        return n / 1_000_000, "M"
    if n >= 1_000:
        return n / 1_000, "K"
    return n, ""


def format_tokens(n):
    """Format a token count, e.g. 1500 -> '1.50KTok', 0 -> '0Tok'."""
    value, unit = scale_num(n)
    if not unit:
        return f"{int(value)}Tok"
    return f"{value:.2f}{unit}Tok"


def tok_has_val(v):
    return v not in (None, 0)


def model_icon(model_id):
    if model_id.startswith("claude-fable-"):
        return "𝔉"
    if model_id.startswith("claude-opus-"):
        return "𝄢"
    if model_id.startswith("claude-sonnet-"):
        return "𝄞"
    if model_id.startswith("claude-haiku-"):
        return "♬"
    return ""


def model_color(model_id):
    if model_id.startswith("claude-fable-"):
        return GOLD
    if model_id.startswith("claude-opus-"):
        return MAGENTA
    if model_id.startswith("claude-sonnet-"):
        return CYAN
    if model_id.startswith("claude-haiku-"):
        return GREEN
    return WHITE


def usage_color(pct):
    rounded = round(pct)
    if rounded < 50:
        return GREEN
    if rounded < 75:
        return YELLOW
    return RED


def shorten_dir(path, max_len):
    """Collapse the middle of a path into '...' if it's longer than max_len,
    keeping the first component and the last two components."""
    if len(path) <= max_len:
        return path
    parts = path.split("/")
    if len(parts) <= 3:
        return path
    first = parts[0]
    tail = "/".join(parts[-2:])
    return f"{first}/.../{tail}"


def format_resets_in(resets_at):
    """Human-readable time-until string for a Unix epoch timestamp."""
    if not resets_at:
        return ""
    diff = int(resets_at - time.time())
    if diff <= 0:
        return ""
    days = diff // 86400
    hours = (diff % 86400) // 3600
    minutes = (diff % 3600) // 60
    if days >= 1:
        return f"{days}d"
    if hours > 0 and minutes == 0:
        return f"{hours}h"
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def get_git_info(dir_path):
    """Return (branch, dirty) for the git repo at dir_path, if any."""
    try:
        result = subprocess.run(
            ["git", "-C", dir_path, "status", "--porcelain=v1", "--branch"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "", False

    if result.returncode != 0 or not result.stdout:
        return "", False

    lines = result.stdout.splitlines()
    header = lines[0].removeprefix("## ")
    if header.startswith("No commits yet on "):
        branch = header.removeprefix("No commits yet on ")
    elif header == "HEAD (no branch)":
        branch = ""
    else:
        branch = header.split("...")[0]

    dirty = len(lines) > 1
    return branch, dirty


def get_ccusage_tokens():
    """Return (daily_tokens, monthly_tokens) via ccusage, or (None, None)
    if ccusage isn't available. Results are cached briefly on disk."""
    if shutil.which("ccusage") is None:
        return None, None

    if os.path.isfile(CCUSAGE_CACHE_FILE):
        age = time.time() - os.path.getmtime(CCUSAGE_CACHE_FILE)
        if age < CCUSAGE_CACHE_TTL:
            try:
                with open(CCUSAGE_CACHE_FILE) as f:
                    daily_str, monthly_str = f.read().splitlines()
                return int(daily_str), int(monthly_str)
            except (OSError, ValueError):
                pass

    today = date.today().isoformat()
    this_month = today[:7]
    try:
        daily_json = subprocess.run(
            ["ccusage", "claude", "daily", "--json", "--offline"],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout
        monthly_json = subprocess.run(
            ["ccusage", "claude", "monthly", "--json", "--offline"],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout
        daily_data = json.loads(daily_json)
        monthly_data = json.loads(monthly_json)
        daily = next(
            (d["totalTokens"] for d in daily_data.get("daily", []) if d.get("date") == today),
            0,
        )
        monthly = next(
            (m["totalTokens"] for m in monthly_data.get("monthly", []) if m.get("month") == this_month),
            0,
        )
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None, None

    try:
        with open(CCUSAGE_CACHE_FILE, "w") as f:
            f.write(f"{daily}\n{monthly}\n")
    except OSError:
        pass

    return daily, monthly


def main():
    # JSON payload that fed by Claude Code.
    # Schema Reference: https://code.claude.com/docs/en/statusline
    data = json.load(sys.stdin)

    # Get current workspace.
    workspace = data.get("workspace") or {}
    dir_path = workspace.get("current_dir") or data.get("cwd") or ""

    # Get AI model information.
    model_data = data.get("model") or {}
    model = model_data.get("display_name") or ""
    model_id = model_data.get("id") or ""
    effort = (data.get("effort") or {}).get("level") or ""
    thinking_enabled = bool((data.get("thinking") or {}).get("enabled"))

    # Get context information.
    context_window = data.get("context_window") or {}
    used_context = context_window.get("used_percentage")
    context_size = context_window.get("context_window_size")
    session_tokens = (context_window.get("total_input_tokens") or 0) + (
        context_window.get("total_output_tokens") or 0
    )

    # Get rate limits.
    rate_limits = data.get("rate_limits") or {}
    five_hour_data = rate_limits.get("five_hour") or {}
    five_hour = five_hour_data.get("used_percentage")
    five_hour_resets_at = five_hour_data.get("resets_at")
    seven_day_data = rate_limits.get("seven_day") or {}
    seven_day = seven_day_data.get("used_percentage")
    seven_day_resets_at = seven_day_data.get("resets_at")

    # Get daily, monthly usage (optional)
    daily_tokens, monthly_tokens = get_ccusage_tokens()

    # Get git repository information (optional)
    branch, dirty = get_git_info(dir_path)

    home = os.path.expanduser("~")
    if dir_path == home:
        short_dir = "~"
    elif dir_path.startswith(home + "/"):
        short_dir = "~" + dir_path[len(home):]
    else:
        short_dir = dir_path

    # Precompute the non-path parts of line 1 (plain, uncolored) so we know
    # how much width is left for the directory before truncating it.
    branch_plain = ""
    if branch:
        branch_plain = f" {branch}"
        if dirty:
            branch_plain += "*"

    model_plain = ""
    if model:
        model_plain = f"{model_icon(model_id)} {model}"
        if effort:
            model_plain += f" {effort}"
        if thinking_enabled:
            model_plain += " thinking"

    contextsize_str = ""
    if used_context is not None and context_size:
        cs_value, cs_unit = scale_num(context_size)
        if not cs_unit:
            contextsize_str = f"({int(cs_value)})"
        else:
            contextsize_str = f"({cs_value:.0f}{cs_unit})"

    context_plain = ""
    if used_context is not None:
        context_plain = f"⛁ Context: {used_context:.0f}%"
        if contextsize_str:
            context_plain += f" {contextsize_str}"

    reserved_len = len(branch_plain)
    if model_plain:
        reserved_len += 3 + len(model_plain)
    if context_plain:
        reserved_len += 3 + len(context_plain)

    try:
        term_width = int(os.environ.get("COLUMNS", "80"))
    except ValueError:
        term_width = 80
    if term_width <= 0:
        term_width = 80

    min_dir_len = 10
    max_dir_len = max(term_width - reserved_len, min_dir_len)

    short_dir = shorten_dir(short_dir, max_dir_len)

    # Build line 1: path, branch, model, used context
    line1 = f"{BLUE}{short_dir}{RESET}"
    if branch:
        line1 += f"{GRAY} {branch}{RESET}"
        if dirty:
            line1 += f"{PINK}*{RESET}"
    if model:
        mcolor = model_color(model_id)
        line1 += f"{SEP}{mcolor}{model_icon(model_id)} {model}{RESET}"
        if effort:
            line1 += f"{mcolor} {effort}{RESET}"
        if thinking_enabled:
            line1 += f"{mcolor} thinking{RESET}"
    if used_context is not None:
        context_color = usage_color(used_context)
        line1 += f"{SEP}{context_color}⛁ Context: {used_context:.0f}%{RESET}"
        if contextsize_str:
            line1 += f"{context_color} {contextsize_str}{RESET}"

    # Build line 2: rate limits, tokens
    line2 = ""
    if five_hour is not None:
        five_color = usage_color(five_hour)
        five_resets_str = format_resets_in(five_hour_resets_at)
        if line2:
            line2 += SEP
        line2 += f"{five_color}5h: {five_hour:.0f}%{RESET}"
        if five_resets_str:
            line2 += f"{five_color} (⏱ {five_resets_str}){RESET}"

    if seven_day is not None:
        seven_color = usage_color(seven_day)
        seven_resets_str = format_resets_in(seven_day_resets_at)
        if line2:
            line2 += SEP
        line2 += f"{seven_color}7d: {seven_day:.0f}%{RESET}"
        if seven_resets_str:
            line2 += f"{seven_color} (⏱ {seven_resets_str}){RESET}"

    if tok_has_val(session_tokens) or tok_has_val(daily_tokens) or tok_has_val(monthly_tokens):
        if line2:
            line2 += SEP
        tok_str = f"{WHITE}{format_tokens(session_tokens)}{RESET}"
        if tok_has_val(daily_tokens) or tok_has_val(monthly_tokens):
            d_str = format_tokens(daily_tokens or 0)
            mo_str = format_tokens(monthly_tokens or 0)
            tok_str += f"{WHITE} ({d_str}/d, {mo_str}/mo){RESET}"
        line2 += tok_str

    # Print the status line: always show line 1, show line 2 only if it has content
    if line2:
        sys.stdout.write(f"{line1}\n{line2}")
    else:
        sys.stdout.write(line1)


if __name__ == "__main__":
    main()
