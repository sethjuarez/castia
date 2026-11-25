"""``optimize diff`` / ``optimize verify`` handlers and ``--from-version`` seeding."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from castia.evaluation.suite import DEFAULT_CONFIG as DEFAULT_EVAL_CONFIG
from castia.optimizing.baseline import DEFAULT_CONFIG_DIR


def register_verify_commands(optsub: argparse._SubParsersAction) -> None:
    diff = optsub.add_parser(
        "diff",
        help="diff an applied candidate against the baseline and flag copied training text",
    )
    _common(diff)
    diff.set_defaults(func=_cmd_optimize_diff)

    verify = optsub.add_parser(
        "verify",
        help="gate an applied candidate on a held-out metric, leak scan and overlap check",
    )
    _common(verify)
    verify.add_argument("--held-out", dest="held_out", default=None,
                        help="held-out rows JSONL; checked for overlap with training")
    verify.add_argument("--baseline-results", dest="baseline_results", default=None,
                        help="per-row held-out results for the baseline (JSON or JSONL)")
    verify.add_argument("--candidate-results", dest="candidate_results", default=None,
                        help="per-row held-out results for the candidate (JSON or JSONL)")
    verify.add_argument("--metric", default="score",
                        help="per-row metric field, or a key under scores/metrics/results "
                             "(default: score)")
    verify.add_argument("--min-delta", dest="min_delta", type=float, default=0.0,
                        help="required candidate - baseline mean (default: 0.0, no regression)")
    verify.add_argument("--out", default=None, help="write the verify report JSON here")
    verify.set_defaults(func=_cmd_optimize_verify)


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--candidate", required=True,
                        help="applied candidate folder under the config dir")
    parser.add_argument("--baseline", default="baseline",
                        help="baseline folder under the config dir (default: baseline)")
    parser.add_argument("--config", default=DEFAULT_EVAL_CONFIG,
                        help=f"eval config with the training dataset (default: {DEFAULT_EVAL_CONFIG})")
    parser.add_argument("--config-dir", default=DEFAULT_CONFIG_DIR,
                        help=f"path to .agent_configs (default: {DEFAULT_CONFIG_DIR})")


def _config_text(folder: Path) -> str:
    """Prompt-facing text of a candidate folder: markdown and tools.json."""

    if not folder.is_dir():
        raise FileNotFoundError(f"{folder} not found (run `optimize apply` first)")
    parts = [
        path.read_text(encoding="utf-8")
        for path in sorted(folder.rglob("*"))
        if path.is_file() and (path.suffix == ".md" or path.name == "tools.json")
    ]
    return "\n".join(parts)


def _instructions(folder: Path) -> str:
    path = folder / "instructions.md"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _training_rows(config: str) -> list[dict[str, Any]] | None:
    from castia.optimizing.jobs import _dataset_payload, load_eval_config

    cfg = load_eval_config(config)
    payload = _dataset_payload(Path(config).parent, cfg.get("dataset"), cfg.get("dataset_file"))
    if isinstance(payload, dict) and payload.get("type") == "inline":
        return list(payload["items"])
    return None


def _read_rows(path: str) -> list[Any]:
    text = Path(path).read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        rows = data.get("rows") or data.get("results")
        return rows if isinstance(rows, list) else [data]
    return []


def _folders(args: argparse.Namespace) -> tuple[Path, Path]:
    from castia.optimizing.cli import _resolve_config_dir

    config_dir = _resolve_config_dir(args.config_dir)
    return config_dir / args.baseline, config_dir / args.candidate


def _leaks(args: argparse.Namespace) -> tuple[list[dict[str, Any]] | None, Path, Path]:
    from castia.optimizing.verify import training_leaks

    base_dir, cand_dir = _folders(args)
    cand_text = _config_text(cand_dir)
    base_text = _config_text(base_dir) if base_dir.is_dir() else ""
    rows = _training_rows(args.config)
    if rows is None:
        return None, base_dir, cand_dir
    return training_leaks(cand_text, rows, baseline_text=base_text), base_dir, cand_dir


def _print_leaks(leaks: list[dict[str, Any]] | None) -> None:
    if leaks is None:
        print("leaks         : not checked (training dataset is a registered reference)",
              file=sys.stderr)
        return
    print(f"leaks         : {len(leaks)}")
    for leak in leaks[:20]:
        print(f"copied        : row {leak['row']} {leak['field']}: \"{leak['match']}\"")


def _cmd_optimize_diff(args: argparse.Namespace) -> int:
    from castia.optimizing.verify import candidate_diff

    try:
        leaks, base_dir, cand_dir = _leaks(args)
        diff = candidate_diff(_instructions(base_dir), _instructions(cand_dir))
    except (OSError, TypeError, ValueError, KeyError, ModuleNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(diff or "(instructions unchanged)")
    _print_leaks(leaks)
    if leaks:
        print("result        : candidate copies training text", file=sys.stderr)
        return 1
    print("result        : no verbatim training text found")
    return 0


def _cmd_optimize_verify(args: argparse.Namespace) -> int:
    from castia.optimizing.verify import heldout_overlap, metric_values, verify_verdict

    try:
        leaks, _base, _cand = _leaks(args)
        overlap: list[int] = []
        if args.held_out:
            training = _training_rows(args.config) or []
            overlap = heldout_overlap(training, _read_rows(args.held_out))
        baseline = (
            metric_values(_read_rows(args.baseline_results), args.metric)
            if args.baseline_results else []
        )
        candidate = (
            metric_values(_read_rows(args.candidate_results), args.metric)
            if args.candidate_results else []
        )
    except (OSError, TypeError, ValueError, KeyError, ModuleNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    verdict = verify_verdict(
        baseline,
        candidate,
        min_delta=args.min_delta,
        leaks=len(leaks or []),
        overlap=len(overlap),
    )
    print(f"candidate     : {args.candidate}")
    print(f"metric        : {args.metric} (held-out, not the optimizer score)")
    print(f"baseline      : {verdict['baseline_mean']} over {len(baseline)} row(s)")
    print(f"candidate     : {verdict['candidate_mean']} over {len(candidate)} row(s)")
    print(f"delta         : {verdict['delta']}")
    _print_leaks(leaks)
    if args.held_out:
        print(f"overlap       : {len(overlap)} held-out row(s) also in training")
    for reason in verdict["reasons"]:
        print(f"reason        : {reason}", file=sys.stderr)
    print(f"result        : {verdict['status']}")
    if args.out:
        report = {
            "candidate": args.candidate,
            "metric": args.metric,
            "min_delta": args.min_delta,
            "verdict": verdict,
            "leaks": leaks,
            "overlap_rows": overlap,
        }
        Path(args.out).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return {"pass": 0, "fail": 1}.get(verdict["status"], 3)


def fetch_agent_version(endpoint: str, agent_name: str, version: str) -> dict[str, Any]:
    """Fetch ``AgentVersionDetails`` as a plain dict."""

    from azure.ai.projects import AIProjectClient
    from azure.identity import DefaultAzureCredential

    project = AIProjectClient(endpoint=endpoint, credential=DefaultAzureCredential())
    details = project.agents.get_version(agent_name, version)
    return details.as_dict() if hasattr(details, "as_dict") else dict(details)


__all__ = ["fetch_agent_version", "register_verify_commands"]
