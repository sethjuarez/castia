"""``python -m castia`` -- compose the capability CLIs."""

from __future__ import annotations

import argparse


def main(argv: list[str] | None = None) -> int:
    from castia.building.cli import register_commands as register_building
    from castia.building.cli import register_dev_command
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
    register_lifecycle(sub)
    register_observe(sub)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
