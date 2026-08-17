# Command-line entry point for FeedVerdict.

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Sequence

from feedverdict.agent import AgentPolicy, AgentRun, ReconciliationAgent
from feedverdict.catalog import discover_markets
from feedverdict.custom_sources import (SourceConfigError, build_custom_source,
                                        load_source_definition)
from feedverdict.evaluation import run_evaluation
from feedverdict.http import UrllibJsonHttpClient
from feedverdict.models import Market
from feedverdict.picker import (PickerUnavailableError, choose_market,
                                market_label)
from feedverdict.registry import SourceRegistry
from feedverdict.scenarios import DEMO_MARKET, build_scenario, scenario_names
from feedverdict.sources import (BitstampSource, CoinbaseSource, KrakenSource,
                                 SourceError)
from feedverdict.state import CanonicalRecord, StateStore

_BUILTIN_SOURCE_NAMES = ("Coinbase", "Kraken", "Bitstamp")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="feedverdict",
        description="Fetch and reconcile a cryptocurrency price from independent sources.",
        epilog=(
            "Management commands:\n"
            "  feedverdict source add|validate|list ...\n"
            "  feedverdict health\n"
            "  feedverdict canonical BTC USD\n"
            "  feedverdict demo stale\n"
            "  feedverdict eval"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "base",
        nargs="?",
        help="Base cryptocurrency symbol; omit it to open the fuzzy picker",
    )
    parser.add_argument("quote", nargs="?", default="USD", help="Quote currency (default: USD)")
    parser.add_argument(
        "--max-age",
        type=float,
        default=120.0,
        help="Maximum provider-data age in seconds (default: 120)",
    )
    parser.add_argument(
        "--max-spread-bps",
        default="100",
        help="Maximum accepted cross-source spread in basis points (default: 100)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=4.0,
        help="Per-request timeout in seconds (default: 4)",
    )
    parser.add_argument(
        "--list-markets",
        action="store_true",
        help="List markets covered by at least two sources and exit",
    )
    return parser


def _money(price: Decimal, quote: str) -> str:
    if quote == "USD":
        if price < Decimal("0.01"):
            # Do not display a real low-priced asset such as SHIB as "$0.00".
            return f"${format(price.normalize(), 'f')}"
        if price < Decimal("1"):
            return f"${price:,.4f}"
        return f"${price:,.2f}"
    return f"{price:,.8f} {quote}"


def _clip(value: str, width: int) -> str:
    return value if len(value) <= width else value[: width - 1] + "…"


def _print_result(result, now: datetime, *, persisted: bool) -> None:
    print()
    print(f"PRICE RECONCILIATION: {result.market.symbol}")
    print("-" * 86)
    print(f"{'Source':<16} {'Price':>20} {'Provider age':>16} {'Status':>28}")
    print("-" * 86)

    rejected_by_source = {item.observation.source: item for item in result.rejected}
    for observation in result.accepted:
        print(
            f"{_clip(observation.source, 16):<16} "
            f"{_money(observation.price, result.market.quote):>20} "
            f"{observation.age_seconds(now):>14.1f}s "
            f"{'HEALTHY':>28}"
        )
    for item in result.rejected:
        observation = item.observation
        print(
            f"{_clip(observation.source, 16):<16} "
            f"{_money(observation.price, result.market.quote):>20} "
            f"{observation.age_seconds(now):>14.1f}s "
            f"{item.code:>28}"
        )
    for failure in result.failures:
        if failure.source not in rejected_by_source:
            print(
                f"{_clip(failure.source, 16):<16} {'-':>20} "
                f"{'-':>16} {failure.code:>28}"
            )

    print("-" * 86)
    if result.canonical_price is not None:
        print(f"Canonical price: {_money(result.canonical_price, result.market.quote)}")
        print(f"Canonical persisted: {'YES' if persisted else 'NO'}")
    elif result.candidate_price is not None:
        print(f"Unverified candidate: {_money(result.candidate_price, result.market.quote)}")
        print("Canonical persisted: NO")
    else:
        print("Canonical price: unavailable")
        print("Canonical persisted: NO")
    print(f"Status: {result.status}")
    print(f"Confidence: {result.confidence}")
    if result.spread_bps is not None:
        print(f"Cross-source spread: {result.spread_bps:.2f} bps")
    print(f"Reason: {result.reason}")


def _print_trace(run: AgentRun) -> None:
    print()
    print("AGENT DECISION TRACE")
    print("-" * 72)
    for event in run.events:
        source = f" [{event.source}]" if event.source else ""
        print(f"{event.sequence:02d} {event.kind.value:<7} {event.code}{source}")
        print(f"   {event.message}")


def _builtin_sources(client: UrllibJsonHttpClient):
    return [
        CoinbaseSource(client),
        KrakenSource(client),
        BitstampSource(client),
    ]


def _source_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="feedverdict source",
        description="Manage validated config-driven price sources.",
    )
    commands = parser.add_subparsers(dest="source_command", required=True)

    add = commands.add_parser("add", help="Validate and register a JSON source config")
    add.add_argument("config", type=Path)
    add.add_argument("--replace", action="store_true")

    validate = commands.add_parser("validate", help="Validate a source config without saving it")
    validate.add_argument("config", type=Path)

    commands.add_parser("list", help="List built-in and registered sources")
    return parser


def _source_command(argv: Sequence[str]) -> int:
    args = _source_parser().parse_args(argv)
    registry = SourceRegistry()

    if args.source_command == "list":
        print("Built-in sources:")
        for name in _BUILTIN_SOURCE_NAMES:
            print(f"  {name:<16} built-in")
        print("Registered sources:")
        entries = registry.entries()
        if not entries:
            print("  (none)")
            return 0
        for entry in entries:
            if entry.definition is None:
                print(f"  {entry.path.stem:<16} INVALID: {entry.error}")
            else:
                print(f"  {entry.definition.name:<16} {entry.definition.kind}")
        return 0

    try:
        definition = load_source_definition(args.config.resolve())
        if definition.name.casefold() in {
            name.casefold() for name in _BUILTIN_SOURCE_NAMES
        }:
            raise SourceConfigError(
                f"{definition.name!r} is reserved for a built-in source"
            )

        if args.source_command == "validate":
            client = UrllibJsonHttpClient(timeout_seconds=4)
            source = build_custom_source(definition, client)
            markets = source.get_markets()
            print(
                f"Valid source config: {definition.name} ({definition.kind}, "
                f"{len(markets)} markets)"
            )
            print("No credentials were read and no live price request was made.")
            return 0

        destination = registry.add(args.config, replace=args.replace)
    except SourceConfigError as exc:
        raise SystemExit(f"Invalid source config: {exc}") from exc
    except (OSError, SourceError) as exc:
        raise SystemExit(f"Source validation failed: {exc}") from exc

    print(f"Registered {definition.name} from {destination}")
    print("API credentials must be supplied through the config's environment-variable names.")
    return 0


def _health_command(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="feedverdict health",
        description="Show persisted source reliability evidence.",
    )
    parser.parse_args(argv)
    store = StateStore()
    rows = store.all_health()
    if not rows:
        print("No source-health history yet. Run a reconciliation first.")
        return 0
    print(f"{'Source':<18}{'Score':>8}{'Good':>8}{'Failed':>9}{'Stale':>8}{'Outlier':>10}")
    print("-" * 61)
    for row in rows:
        print(
            f"{row.source:<18}{row.reliability_score:>8.3f}{row.successes:>8}"
            f"{row.failures:>9}{row.stale_observations:>8}{row.price_outliers:>10}"
        )
    return 0


def _canonical_command(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="feedverdict canonical",
        description="Read the last verified canonical price without fetching.",
    )
    parser.add_argument("base")
    parser.add_argument("quote", nargs="?", default="USD")
    args = parser.parse_args(argv)
    market = Market(args.base, args.quote)
    record = StateStore().canonical(market)
    if record is None:
        print(f"No verified canonical record exists for {market.symbol}.")
        return 1
    _print_canonical_record(record)
    return 0


def _demo_command(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="feedverdict demo",
        description="Run a clearly labelled deterministic fault-injection scenario.",
    )
    parser.add_argument("scenario", nargs="?", choices=scenario_names(), default="stale")
    args = parser.parse_args(argv)
    scenario = build_scenario(args.scenario)

    print("DETERMINISTIC FAULT-INJECTION DEMO (no live exchange data)")
    print("State writes: disabled (synthetic evidence never changes live memory)")
    print(f"Scenario: {scenario.name}")
    print(f"Injected condition: {scenario.description}")
    run = ReconciliationAgent(
        scenario.sources,
        clock=lambda: scenario.now,
    ).run(DEMO_MARKET)
    _print_trace(run)
    _print_result(run.result, run.completed_at, persisted=False)
    return 0


def _eval_command(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="feedverdict eval",
        description="Evaluate the decision policy against deterministic failure contracts.",
    )
    parser.parse_args(argv)
    report = run_evaluation()
    print(f"{'Scenario':<16}{'Result':<8}Observed")
    print("-" * 72)
    for outcome in report.outcomes:
        result = "PASS" if outcome.passed else "FAIL"
        print(f"{outcome.scenario:<16}{result:<8}{outcome.observed}")
        if outcome.failed_checks:
            print(f"  Expected {outcome.expected}; failed: {', '.join(outcome.failed_checks)}")
    print("-" * 72)
    print(
        f"Decision-contract accuracy: {report.passed}/{report.total} "
        f"({report.accuracy:.0%})"
    )
    return 0 if report.all_passed else 1


def _print_canonical_record(record: CanonicalRecord) -> None:
    print(f"Canonical {record.market.symbol}: {_money(record.price, record.market.quote)}")
    print(f"Confidence: {record.confidence.value}")
    print(f"Sources: {', '.join(record.sources)}")
    print(f"Provider timestamp: {record.provider_timestamp.isoformat()}")
    print(f"Persisted at: {record.updated_at.isoformat()}")


def _try_state_store() -> StateStore | None:
    try:
        return StateStore()
    except (OSError, sqlite3.Error) as exc:
        print(f"Warning: persistent state is unavailable: {exc}")
        return None


def _quote_command(argv: Sequence[str]) -> int:
    args = _parser().parse_args(argv)
    if args.max_age <= 0 or args.timeout <= 0:
        raise SystemExit("--max-age and --timeout must be positive")
    try:
        max_spread_bps = Decimal(args.max_spread_bps)
    except InvalidOperation as exc:
        raise SystemExit("--max-spread-bps must be a number") from exc
    if max_spread_bps <= 0:
        raise SystemExit("--max-spread-bps must be positive")

    client = UrllibJsonHttpClient(timeout_seconds=args.timeout)
    sources = _builtin_sources(client)
    custom_sources, config_failures = SourceRegistry().load_sources(client)
    existing_names = {source.name.casefold() for source in sources}
    for source in custom_sources:
        if source.name.casefold() in existing_names:
            print(
                f"Warning: custom source {source.name!r} duplicates another source "
                "and was skipped"
            )
            continue
        sources.append(source)
        existing_names.add(source.name.casefold())
    for failure in config_failures:
        print(f"Warning: {failure.source} config failed ({failure.code}): {failure.message}")

    store = _try_state_store()
    if store is not None:
        sources = store.rank_sources(sources)

    if args.base is None or args.list_markets:
        print("Discovering markets across configured sources...")
        catalog = discover_markets(sources, min_sources=2)
        for failure in catalog.failures:
            print(f"Warning: {failure.source} discovery failed ({failure.code}): {failure.message}")

        if args.list_markets:
            if not catalog.markets:
                print("No markets currently have coverage from at least two sources.")
                return 1
            for item in catalog.markets:
                print(market_label(item))
            return 0

        try:
            market = choose_market(catalog.markets)
        except PickerUnavailableError as exc:
            raise SystemExit(str(exc)) from exc

        availability = catalog.availability_for(market)
        assert availability is not None
        supported = set(availability.sources)
        sources = [source for source in sources if source.name in supported]
        print(f"Selected {market.symbol} ({len(sources)} independent sources available).")
    else:
        market = Market(args.base, args.quote)

    agent = ReconciliationAgent(
        sources,
        policy=AgentPolicy(
            max_age_seconds=args.max_age,
            max_spread_bps=max_spread_bps,
        ),
        source_scores=(
            {source.name: store.health(source.name).reliability_score for source in sources}
            if store is not None
            else None
        ),
    )

    print(f"Requesting real market data for {market.symbol}...")
    run = agent.run(market)
    previous_canonical = store.canonical(market) if store is not None else None
    persisted = False
    if store is not None:
        try:
            store.record_run(run)
            persisted = run.result.canonical_price is not None
        except (OSError, sqlite3.Error) as exc:
            print(f"Warning: run could not be persisted: {exc}")
    _print_trace(run)
    _print_result(run.result, run.completed_at, persisted=persisted)
    if run.result.canonical_price is None and previous_canonical is not None:
        print()
        print("Last verified canonical was retained unchanged:")
        _print_canonical_record(previous_canonical)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    raw_argv = list(argv) if argv is not None else sys.argv[1:]
    if raw_argv and raw_argv[0] == "source":
        return _source_command(raw_argv[1:])
    if raw_argv and raw_argv[0] == "health":
        return _health_command(raw_argv[1:])
    if raw_argv and raw_argv[0] == "canonical":
        return _canonical_command(raw_argv[1:])
    if raw_argv and raw_argv[0] == "demo":
        return _demo_command(raw_argv[1:])
    if raw_argv and raw_argv[0] == "eval":
        return _eval_command(raw_argv[1:])
    return _quote_command(raw_argv)


if __name__ == "__main__":
    raise SystemExit(main())
