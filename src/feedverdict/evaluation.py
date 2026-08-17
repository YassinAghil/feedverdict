# Decision-policy checks that can be run separately from the unit tests.

from __future__ import annotations

from dataclasses import dataclass

from feedverdict.agent import ReconciliationAgent
from feedverdict.scenarios import DEMO_MARKET, build_scenario, scenario_names


@dataclass(frozen=True, slots=True)
class EvaluationOutcome:
    scenario: str
    passed: bool
    observed: str
    expected: str
    failed_checks: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    outcomes: tuple[EvaluationOutcome, ...]

    @property
    def passed(self) -> int:
        return sum(outcome.passed for outcome in self.outcomes)

    @property
    def total(self) -> int:
        return len(self.outcomes)

    @property
    def accuracy(self) -> float:
        return self.passed / self.total if self.total else 0.0

    @property
    def all_passed(self) -> bool:
        return self.passed == self.total


def run_evaluation() -> EvaluationReport:
    outcomes: list[EvaluationOutcome] = []
    for name in scenario_names():
        scenario = build_scenario(name)
        run = ReconciliationAgent(
            scenario.sources,
            clock=lambda scenario=scenario: scenario.now,
        ).run(DEMO_MARKET)
        result = run.result
        expectation = scenario.expectation
        failed_checks: list[str] = []

        if result.status != expectation.status:
            failed_checks.append("status")
        if result.confidence != expectation.confidence:
            failed_checks.append("confidence")
        if result.canonical_updated != expectation.canonical_updated:
            failed_checks.append("canonical update")
        if len(run.queried_sources) != expectation.queried_sources:
            failed_checks.append("source count")
        all_codes = {
            *(event.code for event in run.events),
            *(item.code for item in result.rejected),
            *(item.code for item in result.failures),
        }
        if expectation.required_code and expectation.required_code not in all_codes:
            failed_checks.append(f"reason code {expectation.required_code}")

        outcomes.append(
            EvaluationOutcome(
                scenario=name,
                passed=not failed_checks,
                observed=(
                    f"{result.status.value}/{result.confidence.value}, "
                    f"canonical={'yes' if result.canonical_updated else 'no'}, "
                    f"queries={len(run.queried_sources)}"
                ),
                expected=(
                    f"{expectation.status.value}/{expectation.confidence.value}, "
                    f"canonical={'yes' if expectation.canonical_updated else 'no'}, "
                    f"queries={expectation.queried_sources}"
                ),
                failed_checks=tuple(failed_checks),
            )
        )
    return EvaluationReport(tuple(outcomes))
