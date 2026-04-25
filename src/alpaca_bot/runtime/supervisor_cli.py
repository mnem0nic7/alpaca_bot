from __future__ import annotations

import argparse
import json
import sys
from typing import Callable, Sequence, TextIO

from alpaca_bot.config import Settings
from alpaca_bot.runtime.supervisor import RuntimeSupervisor, SupervisorLoopReport


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="alpaca-bot-supervisor")
    parser.add_argument("--max-iterations", type=int)
    parser.add_argument("--poll-interval-seconds", type=float, default=60.0)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    settings: Settings | None = None,
    supervisor_factory: Callable[[Settings], RuntimeSupervisor] | None = None,
    stdout: TextIO | None = None,
) -> int:
    args = build_parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    resolved_settings = settings or Settings.from_env()
    factory = supervisor_factory or RuntimeSupervisor.from_settings
    supervisor = factory(resolved_settings)
    report = supervisor.run_forever(
        max_iterations=args.max_iterations,
        poll_interval_seconds=args.poll_interval_seconds,
    )
    _write_report(report, stdout or sys.stdout)
    return 0


def _write_report(report: SupervisorLoopReport, output: TextIO) -> None:
    output.write(
        json.dumps(
            {
                "iterations": report.iterations,
                "active_iterations": report.active_iterations,
                "idle_iterations": report.idle_iterations,
            }
        )
    )
