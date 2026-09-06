"""Run a scenario's assertions and produce the categorised result (spec §19.4)."""

from __future__ import annotations

import asyncio

from app.labs.base import LabProvider
from app.models.domain import AssertionCategory, CategorySummary, VerificationResult
from app.scenarios.schema import ScenarioDefinition

from .assertions import Assertion, VerificationContext, build_assertion


def assertions_for(scenario: ScenarioDefinition) -> list[Assertion]:
    return [build_assertion(a.model_dump()) for a in scenario.verification]


class Verifier:
    def __init__(self, provider: LabProvider) -> None:
        self.provider = provider

    async def run(
        self,
        lab_id: str,
        assertions: list[Assertion],
        ctx: VerificationContext,
        *,
        session_id: str | None = None,
        concurrency: int = 4,
    ) -> VerificationResult:
        sem = asyncio.Semaphore(concurrency)

        async def one(a: Assertion):  # type: ignore[no-untyped-def]
            async with sem:
                return await a.evaluate(self.provider, lab_id, ctx)

        checks = await asyncio.gather(*(one(a) for a in assertions))
        cats = {c.value: CategorySummary() for c in AssertionCategory}
        for c in checks:
            summary = cats[c.category.value]
            if c.passed:
                summary.passed += 1
            else:
                summary.failed += 1
        return VerificationResult(
            session_id=session_id,
            lab_id=lab_id,
            passed=sum(1 for c in checks if c.passed),
            failed=sum(1 for c in checks if not c.passed),
            categories=cats,
            checks=list(checks),
        )

    async def run_scenario(
        self, lab_id: str, scenario: ScenarioDefinition, ctx: VerificationContext, *, session_id: str | None = None
    ) -> VerificationResult:
        return await self.run(lab_id, assertions_for(scenario), ctx, session_id=session_id)
