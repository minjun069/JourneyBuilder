from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
import unittest


AI_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AI_PROJECT_ROOT))

from core.context import (  # noqa: E402
    ContextBudgetPolicy,
    ContextIsolationError,
    ContextScope,
    ContextStateAPI,
    ContextSummary,
    ConversationContext,
    ConversationTurn,
    FactRevision,
    advance_context,
    build_context_window,
    resolve_confirmed_facts,
)


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "context_scenarios.json"
LEAK_SENTINEL = "FORBIDDEN-CROSS-SCOPE"


class InMemoryCheckpointTuple:
    def __init__(self, values: dict[str, object]) -> None:
        self.checkpoint = {"channel_values": values}


class InMemoryContextCheckpointer:
    """Exact-config fake for exercising the production context state boundary."""

    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], InMemoryCheckpointTuple] = {}

    @staticmethod
    def _key(config) -> tuple[str, str]:
        configurable = config["configurable"]
        return configurable["thread_id"], configurable["checkpoint_ns"]

    def save(self, config, values: dict[str, object]) -> None:
        serialized = json.loads(json.dumps(values, ensure_ascii=False))
        self._rows[self._key(config)] = InMemoryCheckpointTuple(serialized)

    async def aget_tuple(self, config):
        return self._rows.get(self._key(config))

    async def adelete_thread(self, thread_id: str) -> None:
        self._rows = {
            key: value
            for key, value in self._rows.items()
            if key[0] != thread_id
        }


def _append_turns(
    scenario: dict[str, object],
) -> tuple[ConversationContext, ContextBudgetPolicy | None]:
    category = str(scenario["primary_category"])
    scenario_id = str(scenario["id"])
    policy = (
        ContextBudgetPolicy(500, 80, 250)
        if category == "budget"
        else None
    )
    context = ConversationContext()
    for index in range(int(scenario["turn_count"])):
        facts: dict[str, object] = {
            "profile.anchor": f"{scenario_id}-anchor",
        }
        fact_kinds: dict[str, str] = {}
        if category == "correction":
            facts["income.monthly"] = index + 1
            fact_kinds["income.monthly"] = "amount"
        if category == "budget":
            facts["budget.latest"] = index
            fact_kinds["budget.latest"] = "amount"
        user_message = f"{scenario_id}의 {index + 1}번째 발화"
        if category == "budget":
            user_message += " 장기문맥" * 60
        context = advance_context(
            context=context,
            new_turn=ConversationTurn(
                turn_id=f"{scenario_id}-turn-{index + 1}",
                user_message=user_message,
                assistant_message="확인했습니다.",
                confirmed_facts=facts,
                fact_kinds=fact_kinds,
            ),
            policy=policy,
        )
    return context, policy


def _foreign_context() -> ConversationContext:
    return ConversationContext(
        turns=(
            ConversationTurn(
                turn_id="foreign-raw",
                user_message=f"{LEAK_SENTINEL}-RAW",
                assistant_message="외부 scope 응답",
            ),
        ),
        summary=ContextSummary(
            text=f"{LEAK_SENTINEL}-SUMMARY",
            source_turn_ids=("foreign-summary-source",),
            estimated_units=1,
        ),
        fact_revisions=(
            FactRevision(
                revision_id="foreign-revision",
                path="foreign.secret",
                value=f"{LEAK_SENTINEL}-FACT",
                value_kind="text",
                source_turn_id="foreign-fact-source",
                supersedes_revision_id=None,
            ),
        ),
        turn_digests=(("foreign-raw", "foreign-digest"),),
    )


def _current_facts_for(scenario: dict[str, object]) -> dict[str, object]:
    if scenario["primary_category"] != "correction":
        return {}
    if scenario["expected"] == "latest_fact_wins":
        return {}

    variant = str(scenario["variant"])
    saved = {"income.monthly": 100, "assets.cash": 900}
    current: dict[str, object] = {"assets.cash": 999}
    if variant == "falsey-zero":
        current["income.monthly"] = 0
    elif variant == "falsey-empty-string":
        current["income.monthly"] = ""
    elif variant == "explicit-null":
        current["income.monthly"] = None
    return dict(
        resolve_confirmed_facts(
            active_step_fields={"income.monthly"},
            saved_data=saved,
            current_form=current,
        )
    )


def _model_payload(window) -> dict[str, object]:
    return {
        "turns": [turn.to_state() for turn in window.turns],
        "summary": window.summary.to_state() if window.summary else {},
        "confirmed_facts": dict(window.confirmed_facts),
        "structured_facts": [
            revision.to_state() for revision in window.structured_facts
        ],
        "input_units": window.input_units,
    }


class ContextIsolationVerificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_thirty_scenarios_have_zero_cross_scope_leaks(self) -> None:
        fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        checkpointer = InMemoryContextCheckpointer()
        api = ContextStateAPI(checkpointer)
        foreign_context = _foreign_context()
        passed = 0
        leaks: list[str] = []

        for index, scenario in enumerate(fixture["scenarios"], start=1):
            scenario_id = str(scenario["id"])
            target = ContextScope(
                session_id=10_000 + index,
                thread_id=f"target-{scenario_id}",
            )
            same_session_other_thread = ContextScope(
                session_id=target.session_id,
                thread_id=f"foreign-{scenario_id}",
            )
            same_thread_other_session = ContextScope(
                session_id=20_000 + index,
                thread_id=target.thread_id,
            )
            context, policy = _append_turns(scenario)

            checkpointer.save(
                api.config_for(target),
                api.state_channels(target, context),
            )
            for foreign_scope in (
                same_session_other_thread,
                same_thread_other_session,
            ):
                checkpointer.save(
                    api.config_for(foreign_scope),
                    api.state_channels(foreign_scope, foreign_context),
                )

            restored, restored_by_session, restored_by_thread = await asyncio.gather(
                api.read_context(target),
                api.read_context(same_session_other_thread),
                api.read_context(same_thread_other_session),
            )
            self.assertEqual(restored, context)
            self.assertEqual(restored_by_session, foreign_context)
            self.assertEqual(restored_by_thread, foreign_context)

            window = build_context_window(
                context=restored,
                current_confirmed_facts=_current_facts_for(scenario),
                policy=policy,
            )
            serialized_model_input = json.dumps(
                _model_payload(window),
                ensure_ascii=False,
                sort_keys=True,
            )
            if LEAK_SENTINEL in serialized_model_input:
                leaks.append(scenario_id)
            self.assertNotIn("foreign.secret", window.confirmed_facts)
            self.assertLessEqual(
                window.input_units,
                (policy or ContextBudgetPolicy()).max_model_input_units,
            )

            if scenario["primary_category"] == "correction":
                expected_amount = (
                    int(scenario["turn_count"])
                    if scenario["expected"] == "latest_fact_wins"
                    else _current_facts_for(scenario)["income.monthly"]
                )
                self.assertEqual(
                    window.confirmed_facts["income.monthly"],
                    expected_amount,
                )
            if scenario["primary_category"] == "budget":
                self.assertIsNotNone(restored.summary)

            await api.delete_scope(target)
            self.assertEqual(await api.read_context(target), ConversationContext())
            self.assertEqual(
                await api.read_context(same_session_other_thread),
                foreign_context,
            )
            self.assertEqual(
                await api.read_context(same_thread_other_session),
                foreign_context,
            )
            await api.delete_scope(same_session_other_thread)
            await api.delete_scope(same_thread_other_session)
            passed += 1

        self.assertEqual(passed, 30)
        self.assertEqual(leaks, [])

    async def test_scope_swapped_checkpoint_fails_closed(self) -> None:
        checkpointer = InMemoryContextCheckpointer()
        api = ContextStateAPI(checkpointer)
        requested = ContextScope(session_id=1, thread_id="requested")
        foreign = ContextScope(session_id=2, thread_id="requested")
        checkpointer.save(
            api.config_for(requested),
            api.state_channels(foreign, _foreign_context()),
        )

        with self.assertRaises(ContextIsolationError):
            await api.read_context(requested)


if __name__ == "__main__":
    unittest.main()
