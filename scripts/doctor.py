#!/usr/bin/env python3
"""
HELM Doctor: Diagnostic CLI utility to verify environment prerequisites,
configuration, and infrastructure readiness.
"""
from __future__ import annotations
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from rich.console import Console
from rich.table import Table

console = Console()


def check_python() -> tuple[bool, str]:
    ver = sys.version.split()[0]
    major, minor = sys.version_info[:2]
    if (major, minor) >= (3, 11):
        return True, f"Python {ver} (Compatible)"
    return False, f"Python {ver} (Requires Python >= 3.11)"


def check_git() -> tuple[bool, str]:
    if not shutil.which("git"):
        return False, "Git executable not found in PATH"
    try:
        out = subprocess.check_output(["git", "--version"], text=True).strip()
        return True, out
    except Exception as e:
        return False, f"Git error: {e}"


def check_docker() -> tuple[bool, str]:
    if not shutil.which("docker"):
        return False, "Docker CLI not found in PATH"
    try:
        out = subprocess.check_output(["docker", "--version"], text=True).strip()
        # Check daemon ping
        p = subprocess.run(["docker", "info"], capture_output=True, timeout=3)
        if p.returncode == 0:
            return True, f"{out} (Daemon Running)"
        return True, f"{out} (Daemon inactive - LocalProcess fallback will be used)"
    except Exception:
        return True, "Docker CLI detected (Daemon inactive - LocalProcess fallback will be used)"


def check_node() -> tuple[bool, str]:
    if not shutil.which("node"):
        return True, "Node.js not installed (Optional for backend; required for Next.js web UI)"
    try:
        out = subprocess.check_output(["node", "--version"], text=True).strip()
        return True, f"Node.js {out}"
    except Exception:
        return True, "Node.js check skipped"


def check_hermes() -> tuple[bool, str]:
    hermes_dir = PROJECT_ROOT / "packages" / "agent" / "hermes"
    if hermes_dir.exists() and any(hermes_dir.iterdir()):
        return True, f"Vendored in packages/agent/hermes"
    return False, "Hermes not found in packages/agent/hermes"


def check_env_config() -> tuple[bool, str]:
    api_key = os.getenv("DEEPSEEK_API_KEY", "")
    provider = os.getenv("MODEL_PROVIDER", "deepseek")
    if provider == "mock":
        return True, "MODEL_PROVIDER=mock (Hermetic testing mode)"
    if api_key and api_key != "your-deepseek-api-key-here":
        return True, f"DEEPSEEK_API_KEY configured ({api_key[:6]}...)"
    return True, "DEEPSEEK_API_KEY not set in current shell (Set in .env or use mock for testing)"


def run_doctor():
    console.print("\n[bold cyan]HELM Environment Doctor[/bold cyan]")
    console.print("Checking local platform prerequisites and system health...\n")

    checks = [
        ("Python Runtime", check_python),
        ("Git Version Control", check_git),
        ("Docker Sandbox Runtime", check_docker),
        ("Node.js Runtime", check_node),
        ("Hermes Agent Vendoring", check_hermes),
        ("Model Provider Configuration", check_env_config),
    ]

    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Component", style="bold", width=30)
    table.add_column("Status", width=12)
    table.add_column("Details", width=50)

    all_passed = True
    for name, check_fn in checks:
        passed, details = check_fn()
        status_text = "[green]✓ READY[/green]" if passed else "[red]✗ FAILED[/red]"
        if not passed:
            all_passed = False
        table.add_row(name, status_text, details)

    console.print(table)
    console.print()

    if all_passed:
        console.print("[bold green]All systems operational! HELM is ready to run.[/bold green]\n")
    else:
        console.print("[bold yellow]Some components require attention. See above for details.[/bold yellow]\n")


if __name__ == "__main__":
    run_doctor()
