"""Command line entry point: `harness <command>`."""

from __future__ import annotations

import argparse
import sys

from harness import __version__
from harness.config import ConfigError, load_config


def cmd_doctor(args: argparse.Namespace) -> int:
    from harness.doctor import format_report, run_checks

    cfg = load_config()
    checks = run_checks(cfg)
    print(format_report(checks))
    return 1 if any(c.status == "fail" for c in checks) else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="harness", description="AI Game Development Harness")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor", help="check the environment")
    doctor.set_defaults(func=cmd_doctor)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
