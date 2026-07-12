"""
app/scripts/simulate_trigger.py

CLI entrypoint for triggering simulated banking workflows.

Usage:
    python -m app.scripts.simulate_trigger --type subsidy_loan --applicant APP-001
    python -m app.scripts.simulate_trigger --type emergency_payout --applicant APP-005
    python -m app.scripts.simulate_trigger --type adversarial_threshold_dodge --applicant APP-001
    python -m app.scripts.simulate_trigger --type adversarial_round_cap --applicant APP-004
    python -m app.scripts.simulate_trigger --list-applicants

Available trigger types:
    subsidy_loan               Standard serial flow: A -> B -> C
    emergency_payout           Disaster bypass: A -> C, targets frozen fund (human review)
    adversarial_threshold_dodge  Amount just under ₹50k; validator must still catch human-review
    adversarial_round_cap      Insufficient fund forces repeated PARTIAL; round cap escalates

The script prints the TransactionID immediately on startup — open your Notion
dashboard before running to watch the card appear live.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from app.scripts.persistence import VALID_TRIGGER_TYPES, list_applicants, FixtureError
from app.scripts.simulate_engine import run_simulation


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.scripts.simulate_trigger",
        description="Run a simulated banking workflow trigger end-to-end.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--type", "-t",
        dest="trigger_type",
        choices=sorted(VALID_TRIGGER_TYPES),
        help="The type of trigger to simulate.",
    )
    parser.add_argument(
        "--applicant", "-a",
        dest="applicant_id",
        default=None,
        help="Applicant ID from fixtures.json (e.g. APP-001).",
    )
    parser.add_argument(
        "--list-applicants",
        action="store_true",
        help="List all available applicant IDs and exit.",
    )
    return parser


def _print_applicants() -> None:
    applicants = list_applicants()
    print("\nAvailable applicants in fixtures.json:\n")
    for app_id, data in sorted(applicants.items()):
        tags = ", ".join(data.get("tags", []))
        print(f"  {app_id:10s}  {data['name']:25s}  [{tags}]")
        print(f"             {data.get('notes', '')[:80]}")
    print()


def _print_result(result) -> None:
    icon = {
        "approved":  "✅",
        "rejected":  "❌",
        "escalated": "⚠️ ",
    }.get(result.final_status.lower(), "❓")

    duration_ms = (
        int((result.completed_at - result.started_at).total_seconds() * 1000)
        if result.completed_at else 0
    )

    print(
        f"\n{'='*60}\n"
        f"  {icon}  SIMULATION COMPLETE\n"
        f"{'='*60}\n"
        f"  Transaction ID   : {result.transaction_id}\n"
        f"  Applicant        : {result.applicant_id} — {result.applicant_name}\n"
        f"  Trigger          : {result.trigger_type}\n"
        f"  Route            : {result.route}\n"
        f"  Rounds           : {result.rounds}\n"
        f"  Final Status     : {result.final_status.upper()}\n"
        f"  Human Review     : {'YES — card on Manager Desk' if result.requires_human_review else 'No'}\n"
        f"  Policy Version   : {result.policy_version}\n"
        f"  Outcome          : {result.outcome_reason}\n"
        f"  Duration         : {duration_ms}ms\n"
        f"{'='*60}\n",
        flush=True,
    )

    if result.error:
        print(f"  ⚠️  Pipeline error: {result.error}\n", flush=True)


async def _async_main(trigger_type: str, applicant_id: str) -> int:
    """Run the simulation. Returns exit code."""
    try:
        result = await run_simulation(trigger_type, applicant_id)
        _print_result(result)
        return 0
    except FixtureError as exc:
        print(f"\n❌  Fixture error: {exc}\n", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n⏹  Interrupted.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"\n❌  Unexpected error: {exc}\n", file=sys.stderr)
        return 1


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.list_applicants:
        _print_applicants()
        sys.exit(0)

    if not args.trigger_type:
        parser.error("--type is required (use --list-applicants to see applicants).")

    if not args.applicant_id:
        parser.error("--applicant is required (use --list-applicants to see available IDs).")

    exit_code = asyncio.run(_async_main(args.trigger_type, args.applicant_id))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
