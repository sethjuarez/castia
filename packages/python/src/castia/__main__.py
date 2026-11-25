"""``python -m castia`` -- compose the capability CLIs."""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    from castia.building.cli import register_commands as register_building
    from castia.building.cli import register_dev_command, register_doctor_command
    from castia.delivery.cli import register_commands as register_delivery
    from castia.evaluation.cli import register_commands as register_evaluation
    from castia.finetuning.cli import register_commands as register_finetuning
    from castia.lifecycle.cli import register_commands as register_lifecycle
    from castia.observe.cli import register_commands as register_observe
    from castia.optimizing.cli import register_commands as register_optimizing

    parser = argparse.ArgumentParser(
        prog="python -m castia",
        description="Build-time tooling for Castia agents.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    register_delivery(sub)
    register_optimizing(sub)
    register_evaluation(sub)
    register_finetuning(sub)
    register_building(sub)
    register_dev_command(sub)
    register_doctor_command(sub)
    register_lifecycle(sub)
    register_observe(sub)

    args = parser.parse_args(argv)
    from castia.lifecycle.output import emit_envelope, error_envelope, wants_json

    if not wants_json(args):
        return args.func(args)
    try:
        return args.func(args)
    except Exception as exc:  # noqa: BLE001 - --json promises one envelope on stdout
        tokens = sys.argv[1:] if argv is None else argv
        command = " ".join(t for t in tokens[:2] if not t.startswith("-")) or "castia"
        emit_envelope(error_envelope(command, exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
