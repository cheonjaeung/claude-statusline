#!/usr/bin/env python3

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date
from unittest.mock import patch

import statusline

# Fixed epoch used for every test so time-until calculations (format_resets_in)
# are deterministic instead of depending on when the test happens to run.
# statusline.time.time is patched to this value for every run_statusline()
# call, and date.today() (used by get_ccusage_tokens) derives from it too,
# so "today"/"this month" for ccusage fixtures must be computed from it.
FIXED_NOW = 1700000000.0
FIXED_TODAY = date.fromtimestamp(FIXED_NOW).isoformat()
FIXED_MONTH = FIXED_TODAY[:7]

HOME = os.path.expanduser("~")


def run_statusline(
    payload,
    *,
    git_stdout=None,
    ccusage_available=False,
    daily_data=None,
    monthly_data=None,
    columns=None,
):
    """Run statusline.main() end-to-end for a JSON payload, with the git
    and ccusage subprocess calls mocked out. Returns captured stdout."""

    def fake_run(cmd, **kwargs):
        if cmd[0] == "git":
            if git_stdout is None:
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout=git_stdout, stderr="")
        if cmd[0] == "ccusage" and cmd[2] == "daily":
            return subprocess.CompletedProcess(
                cmd, 0, stdout=json.dumps(daily_data or {"daily": []}), stderr=""
            )
        if cmd[0] == "ccusage" and cmd[2] == "monthly":
            return subprocess.CompletedProcess(
                cmd, 0, stdout=json.dumps(monthly_data or {"monthly": []}), stderr=""
            )
        raise AssertionError(f"unexpected subprocess call: {cmd}")

    env = dict(os.environ)
    env.pop("COLUMNS", None)
    if columns is not None:
        env["COLUMNS"] = columns

    stdin = io.StringIO(json.dumps(payload))
    stdout = io.StringIO()
    # Unlike real stdout, StringIO has no reconfigure(), which main() calls
    # unconditionally, so stub it out.
    stdout.reconfigure = lambda **kwargs: None

    with (
        tempfile.TemporaryDirectory() as tmp_dir,
        patch.object(sys, "stdin", stdin),
        patch("statusline.subprocess.run", side_effect=fake_run),
        patch(
            "statusline.shutil.which",
            return_value="/usr/local/bin/ccusage" if ccusage_available else None,
        ),
        patch("statusline.CCUSAGE_CACHE_FILE", os.path.join(tmp_dir, "ccusage_cache")),
        patch("statusline.time.time", return_value=FIXED_NOW),
        patch.dict(os.environ, env, clear=True),
        redirect_stdout(stdout),
    ):
        statusline.main()

    return stdout.getvalue()


class StatuslineIntegrationTest(unittest.TestCase):
    def test_full_status_line(self):
        payload = {
            "workspace": {"current_dir": f"{HOME}/Projects/claude-statusline"},
            "model": {"display_name": "Sonnet 5", "id": "claude-sonnet-5"},
            "effort": {"level": "high"},
            "thinking": {"enabled": True},
            "context_window": {
                "used_percentage": 42.3,
                "context_window_size": 200000,
            },
            "rate_limits": {
                "five_hour": {"used_percentage": 30, "resets_at": FIXED_NOW + 3661},
                "seven_day": {"used_percentage": 80, "resets_at": FIXED_NOW + 200000},
            },
        }

        output = run_statusline(
            payload,
            git_stdout="## main...origin/main\n M statusline.py\n",
            ccusage_available=True,
            daily_data={"daily": [{"date": FIXED_TODAY, "totalTokens": 50000}]},
            monthly_data={"monthly": [{"month": FIXED_MONTH, "totalTokens": 2000000}]},
        )

        s = statusline
        line1 = (
            f"{s.BLUE}~/Projects/claude-statusline{s.RESET}"
            f"{s.GRAY} main{s.RESET}{s.PINK}*{s.RESET}"
            f"{s.SEP}{s.CYAN}𝄞 Sonnet 5{s.RESET}{s.CYAN} high{s.RESET}{s.CYAN} thinking{s.RESET}"
            f"{s.SEP}{s.GREEN}⛁ Context: 42%{s.RESET}{s.GREEN} (200K){s.RESET}"
        )
        line2 = (
            f"{s.GREEN}5h: 30%{s.RESET}{s.GREEN} (⏱ 1h 1m){s.RESET}"
            f"{s.SEP}{s.RED}7d: 80%{s.RESET}{s.RED} (⏱ 2d){s.RESET}"
            f"{s.SEP}{s.WHITE}50.00KTok/d{s.RESET}"
            f"{s.SEP}{s.WHITE}2.00MTok/mo{s.RESET}"
        )
        self.assertEqual(output, f"{line1}\n{line2}")

    def test_minimal_no_git_no_model(self):
        output = run_statusline({"workspace": {"current_dir": "/tmp"}})
        self.assertEqual(output, f"{statusline.BLUE}/tmp{statusline.RESET}")

    def test_empty_payload(self):
        output = run_statusline({})
        self.assertEqual(output, f"{statusline.BLUE}{statusline.RESET}")

    def test_high_context_usage(self):
        payload = {
            "workspace": {"current_dir": f"{HOME}/Projects/claude-statusline"},
            "context_window": {"used_percentage": 90},
        }
        output = run_statusline(payload, git_stdout="## main...origin/main\n")

        s = statusline
        expected = (
            f"{s.BLUE}~/Projects/claude-statusline{s.RESET}"
            f"{s.GRAY} main{s.RESET}"
            f"{s.SEP}{s.RED}⛁ Context: 90%{s.RESET}"
        )
        self.assertEqual(output, expected)

    def test_long_path_gets_truncated(self):
        path = "/Users/nobody/very/deep/nested/path/that/is/quite/long/indeed/for/sure"
        output = run_statusline({"workspace": {"current_dir": path}}, columns="30")

        expected_short = statusline.shorten_dir(path, 30)
        self.assertIn("...", expected_short)
        self.assertEqual(output, f"{statusline.BLUE}{expected_short}{statusline.RESET}")

    def test_ccusage_available_but_no_data_for_today(self):
        payload = {"workspace": {"current_dir": "/tmp"}}
        output = run_statusline(payload, ccusage_available=True)

        # daily/monthly both resolve to 0 (no matching entry), which is
        # treated the same as "no value" and hidden, same as ccusage
        # being unavailable entirely.
        self.assertEqual(output, f"{statusline.BLUE}/tmp{statusline.RESET}")

    def test_only_daily_tokens_available(self):
        payload = {"workspace": {"current_dir": "/tmp"}}
        output = run_statusline(
            payload,
            ccusage_available=True,
            daily_data={"daily": [{"date": FIXED_TODAY, "totalTokens": 50000}]},
        )

        expected = f"{statusline.BLUE}/tmp{statusline.RESET}\n{statusline.WHITE}50.00KTok/d{statusline.RESET}"
        self.assertEqual(output, expected)

    def test_detached_head_has_no_branch_shown(self):
        output = run_statusline(
            {"workspace": {"current_dir": HOME}}, git_stdout="## HEAD (no branch)\n"
        )
        self.assertEqual(output, f"{statusline.BLUE}~{statusline.RESET}")

    def test_fresh_repo_with_no_commits(self):
        output = run_statusline(
            {"workspace": {"current_dir": "/tmp"}},
            git_stdout="## No commits yet on main\n",
        )
        expected = f"{statusline.BLUE}/tmp{statusline.RESET}{statusline.GRAY} main{statusline.RESET}"
        self.assertEqual(output, expected)


if __name__ == "__main__":
    unittest.main()
