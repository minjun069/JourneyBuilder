from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import sys
import unittest


AI_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AI_PROJECT_ROOT))

from core.context import (  # noqa: E402
    ApproximateUnitCounter,
    CONTEXT_CHECKPOINT_NAMESPACE,
    ContextBudgetPolicy,
    ContextIsolationError,
    ContextScope,
    ContextStateAPI,
    ConversationTurn,
    FakeContextStore,
    build_context_window,
    checkpoint_config,
    checkpoint_thread_key,
    resolve_confirmed_facts,
)


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "context_scenarios.json"
EXPECTED_DISTRIBUTION = {
    "follow_up": 6,
    "ellipsis": 5,
    "correction": 7,
    "budget": 4,
    "user_isolation": 4,
    "session_isolation": 4,
}


def _turn(
    turn_id: str,
    *,
    user_message: str | None = None,
    assistant_message: str = "확인했습니다.",
    facts: dict[str, object] | None = None,
) -> ConversationTurn:
    return ConversationTurn(
        turn_id=turn_id,
        user_message=user_message or f"{turn_id} 사용자 발화",
        assistant_message=assistant_message,
        confirmed_facts=facts or {},
    )


class ApproximateUnitCounterTests(unittest.TestCase):
    def test_estimates_korean_ascii_punctuation_and_unicode(self) -> None:
        counter = ApproximateUnitCounter()

        self.assertEqual(counter.estimate_text("가"), 2)
        self.assertEqual(counter.estimate_text("ㄱ"), 3)
        self.assertEqual(counter.estimate_text("abcd1234"), 2)
        self.assertEqual(counter.estimate_text("a b"), 2)
        self.assertEqual(counter.estimate_text("!"), 1)
        self.assertEqual(counter.estimate_text("🙂"), 3)
        self.assertEqual(counter.estimate_text("한글 abc!"), 6)

    def test_rejects_non_string_input(self) -> None:
        with self.assertRaises(TypeError):
            ApproximateUnitCounter().estimate_text(123)  # type: ignore[arg-type]


class ContextScopeTests(unittest.TestCase):
    def test_rejects_invalid_scope_parts(self) -> None:
        with self.assertRaises(ValueError):
            ContextScope(session_id=0, thread_id="thread")
        with self.assertRaises(TypeError):
            ContextScope(session_id=True, thread_id="thread")
        with self.assertRaises(ValueError):
            ContextScope(session_id=1, thread_id="")

    def test_checkpoint_key_is_stable_opaque_and_scope_specific(self) -> None:
        first = ContextScope(session_id=1, thread_id="user-3-session-1")
        same = ContextScope(session_id=1, thread_id="user-3-session-1")
        other_session = ContextScope(session_id=2, thread_id="user-3-session-1")

        self.assertEqual(checkpoint_thread_key(first), checkpoint_thread_key(same))
        self.assertNotEqual(
            checkpoint_thread_key(first),
            checkpoint_thread_key(other_session),
        )
        self.assertNotIn(first.thread_id, checkpoint_thread_key(first))
        self.assertEqual(
            checkpoint_config(first)["configurable"]["checkpoint_ns"],
            CONTEXT_CHECKPOINT_NAMESPACE,
        )


class ContextStateAPITests(unittest.IsolatedAsyncioTestCase):
    class FakeCheckpointer:
        def __init__(self, values: dict[str, object] | None = None) -> None:
            if values is None:
                self.result = None
            else:
                self.result = type(
                    "CheckpointTuple",
                    (),
                    {"checkpoint": {"channel_values": values}},
                )()
            self.read_configs: list[object] = []
            self.deleted_thread_ids: list[str] = []

        async def aget_tuple(self, config):
            self.read_configs.append(config)
            return self.result

        async def adelete_thread(self, thread_id: str) -> None:
            self.deleted_thread_ids.append(thread_id)

    async def test_reads_only_state_bound_to_requested_scope(self) -> None:
        scope = ContextScope(session_id=1, thread_id="thread-a")
        checkpointer = self.FakeCheckpointer(
            {
                "context_scope": scope.to_state(),
                "conversation_context": {"schema_version": "1.0"},
            }
        )
        api = ContextStateAPI(checkpointer)

        values = await api.read_values(scope)

        self.assertEqual(values["context_scope"], scope.to_state())
        self.assertEqual(checkpointer.read_configs, [checkpoint_config(scope)])

    async def test_rejects_checkpoint_with_mismatched_embedded_scope(self) -> None:
        requested = ContextScope(session_id=1, thread_id="thread-a")
        foreign = ContextScope(session_id=2, thread_id="thread-a")
        api = ContextStateAPI(
            self.FakeCheckpointer({"context_scope": foreign.to_state()})
        )

        with self.assertRaises(ContextIsolationError):
            await api.read_values(requested)

    async def test_deletes_all_checkpoints_for_the_opaque_scope_key(self) -> None:
        scope = ContextScope(session_id=1, thread_id="thread-a")
        checkpointer = self.FakeCheckpointer()

        await ContextStateAPI(checkpointer).delete_scope(scope)

        self.assertEqual(
            checkpointer.deleted_thread_ids,
            [checkpoint_thread_key(scope)],
        )


class ConfirmedFactResolutionTests(unittest.TestCase):
    def test_active_current_form_wins_and_other_steps_use_saved_data(self) -> None:
        resolved = resolve_confirmed_facts(
            active_step_fields={
                "income.monthly",
                "income.bonus",
                "income.memo",
            },
            saved_data={
                "income.monthly": 3_000_000,
                "income.bonus": 1_000_000,
                "income.memo": "기존 값",
                "assets.cash": 10_000_000,
            },
            current_form={
                "income.monthly": 0,
                "income.bonus": None,
                "income.memo": "",
                "assets.cash": 99_000_000,
            },
        )

        self.assertEqual(resolved["income.monthly"], 0)
        self.assertIsNone(resolved["income.bonus"])
        self.assertEqual(resolved["income.memo"], "")
        self.assertEqual(resolved["assets.cash"], 10_000_000)

    def test_missing_active_form_field_falls_back_to_saved_data(self) -> None:
        resolved = resolve_confirmed_facts(
            active_step_fields={"income.monthly"},
            saved_data={"income.monthly": 3_000_000},
            current_form={},
        )

        self.assertEqual(resolved["income.monthly"], 3_000_000)


class FakeContextStoreTests(unittest.TestCase):
    def test_same_turn_is_idempotent_but_conflict_is_rejected(self) -> None:
        store = FakeContextStore()
        scope = ContextScope(session_id=1, thread_id="thread-a")
        original = _turn("turn-1", facts={"income.monthly": 100})

        store.append(scope, original)
        store.append(scope, original)
        self.assertEqual(store.read(scope), (original,))

        with self.assertRaises(ValueError):
            store.append(scope, _turn("turn-1", facts={"income.monthly": 200}))

    def test_composite_scope_prevents_user_and_session_leaks(self) -> None:
        store = FakeContextStore()
        target = ContextScope(session_id=1, thread_id="user-a-thread")
        other_user = ContextScope(session_id=1, thread_id="user-b-thread")
        other_session = ContextScope(session_id=2, thread_id="user-a-thread")

        store.append(target, _turn("target", facts={"target.value": 1}))
        store.append(other_user, _turn("other-user", facts={"secret.user": 2}))
        store.append(other_session, _turn("other-session", facts={"secret.session": 3}))

        window = build_context_window(
            store=store,
            scope=target,
            current_confirmed_facts={},
        )

        self.assertEqual([turn.turn_id for turn in window.turns], ["target"])
        self.assertEqual(dict(window.confirmed_facts), {"target.value": 1})


class ContextWindowTests(unittest.TestCase):
    def test_injected_counter_controls_the_same_budget_algorithm(self) -> None:
        class FixedCounter:
            def estimate_text(self, text: str) -> int:
                return 10

        store = FakeContextStore()
        scope = ContextScope(session_id=1, thread_id="thread")
        store.append(scope, _turn("turn-1"))

        window = build_context_window(
            store=store,
            scope=scope,
            current_confirmed_facts={},
            policy=ContextBudgetPolicy(
                max_recent_turns=5,
                max_history_units=27,
                max_summary_units=0,
            ),
            counter=FixedCounter(),
        )

        self.assertEqual(window.turns, ())
        self.assertEqual(window.history_units, 0)

    def test_latest_fact_and_current_snapshot_win(self) -> None:
        store = FakeContextStore()
        scope = ContextScope(session_id=1, thread_id="thread")
        for index, amount in enumerate((100, 200, 300), start=1):
            store.append(
                scope,
                _turn(f"turn-{index}", facts={"income.monthly": amount}),
            )

        history_window = build_context_window(
            store=store,
            scope=scope,
            current_confirmed_facts={},
        )
        current_window = build_context_window(
            store=store,
            scope=scope,
            current_confirmed_facts={"income.monthly": 400},
        )

        self.assertEqual(history_window.confirmed_facts["income.monthly"], 300)
        self.assertEqual(current_window.confirmed_facts["income.monthly"], 400)

    def test_turn_limit_keeps_a_contiguous_recent_suffix(self) -> None:
        store = FakeContextStore()
        scope = ContextScope(session_id=1, thread_id="thread")
        for index in range(6):
            store.append(
                scope,
                _turn(f"turn-{index}", facts={f"fact.{index}": index}),
            )

        window = build_context_window(
            store=store,
            scope=scope,
            current_confirmed_facts={},
            policy=ContextBudgetPolicy(
                max_recent_turns=2,
                max_history_units=3_200,
                max_summary_units=600,
            ),
        )

        self.assertEqual([turn.turn_id for turn in window.turns], ["turn-4", "turn-5"])
        self.assertIsNotNone(window.summary)
        assert window.summary is not None
        self.assertEqual(window.summary.omitted_turn_ids, ("turn-0", "turn-1", "turn-2", "turn-3"))
        self.assertLessEqual(window.history_units, 3_200)

    def test_unit_limit_never_skips_newest_turn_for_an_older_one(self) -> None:
        store = FakeContextStore()
        scope = ContextScope(session_id=1, thread_id="thread")
        store.append(scope, _turn("short-old", user_message="짧음"))
        store.append(scope, _turn("long-new", user_message="긴메시지" * 20))

        window = build_context_window(
            store=store,
            scope=scope,
            current_confirmed_facts={},
            policy=ContextBudgetPolicy(
                max_recent_turns=5,
                max_history_units=20,
                max_summary_units=10,
            ),
        )

        self.assertEqual(window.turns, ())
        self.assertIsNotNone(window.summary)
        self.assertLessEqual(window.history_units, 20)

    def test_summary_does_not_repeat_a_fact_superseded_by_retained_history(self) -> None:
        store = FakeContextStore()
        scope = ContextScope(session_id=1, thread_id="thread")
        store.append(scope, _turn("old", facts={"income.monthly": 100}))
        store.append(scope, _turn("new", facts={"income.monthly": 200}))

        window = build_context_window(
            store=store,
            scope=scope,
            current_confirmed_facts={},
            policy=ContextBudgetPolicy(
                max_recent_turns=1,
                max_history_units=3_200,
                max_summary_units=600,
            ),
        )

        self.assertEqual(window.confirmed_facts["income.monthly"], 200)
        self.assertIsNotNone(window.summary)
        assert window.summary is not None
        self.assertEqual(window.summary.fact_items, ())


class ScenarioMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    def test_matrix_has_thirty_three_to_five_turn_scenarios(self) -> None:
        scenarios = self.fixture["scenarios"]
        distribution = Counter(
            scenario["primary_category"] for scenario in scenarios
        )

        self.assertEqual(self.fixture["distribution"], EXPECTED_DISTRIBUTION)
        self.assertEqual(len(scenarios), 30)
        self.assertEqual(dict(distribution), EXPECTED_DISTRIBUTION)
        self.assertEqual(len({scenario["id"] for scenario in scenarios}), 30)
        self.assertTrue(
            all(3 <= scenario["turn_count"] <= 5 for scenario in scenarios)
        )

    def test_all_declared_scenarios_satisfy_the_context_invariant(self) -> None:
        for scenario in self.fixture["scenarios"]:
            with self.subTest(scenario=scenario["id"]):
                self._run_scenario(scenario)

    def _run_scenario(self, scenario: dict[str, object]) -> None:
        category = str(scenario["primary_category"])
        turn_count = int(scenario["turn_count"])
        scenario_id = str(scenario["id"])
        store = FakeContextStore()
        target = ContextScope(session_id=1, thread_id="user-a-thread")

        for index in range(turn_count):
            facts: dict[str, object] = {}
            if index == 0:
                facts["profile.anchor"] = f"{scenario_id}-anchor"
            if category == "correction":
                facts["income.monthly"] = index + 1
            if category == "budget":
                if scenario["variant"] == "latest-correction-under-cap":
                    facts["budget.latest"] = index
                else:
                    facts[f"budget.fact.{index}"] = index
            store.append(
                target,
                _turn(
                    f"{scenario_id}-turn-{index + 1}",
                    user_message=f"{scenario_id}의 {index + 1}번째 합성 발화입니다.",
                    facts=facts,
                ),
            )

        if category in {"follow_up", "ellipsis"}:
            window = build_context_window(
                store=store,
                scope=target,
                current_confirmed_facts={},
            )
            self.assertEqual(len(window.turns), turn_count)
            self.assertEqual(
                window.confirmed_facts["profile.anchor"],
                f"{scenario_id}-anchor",
            )
            return

        if category == "correction":
            self._assert_correction_scenario(
                scenario=scenario,
                store=store,
                scope=target,
                turn_count=turn_count,
            )
            return

        if category == "budget":
            self._assert_budget_scenario(
                scenario=scenario,
                store=store,
                scope=target,
                turn_count=turn_count,
            )
            return

        if category in {"user_isolation", "session_isolation"}:
            if category == "user_isolation":
                foreign = ContextScope(session_id=1, thread_id="user-b-thread")
            else:
                foreign = ContextScope(session_id=2, thread_id="user-a-thread")
            store.append(
                foreign,
                _turn(
                    f"{scenario_id}-foreign",
                    user_message="FORBIDDEN foreign message",
                    facts={"foreign.secret": scenario_id},
                ),
            )
            window = build_context_window(
                store=store,
                scope=target,
                current_confirmed_facts={},
            )
            self.assertNotIn("foreign.secret", window.confirmed_facts)
            self.assertFalse(
                any("FORBIDDEN" in turn.user_message for turn in window.turns)
            )
            return

        self.fail(f"unsupported scenario category: {category}")

    def _assert_correction_scenario(
        self,
        *,
        scenario: dict[str, object],
        store: FakeContextStore,
        scope: ContextScope,
        turn_count: int,
    ) -> None:
        expected = str(scenario["expected"])
        variant = str(scenario["variant"])

        if expected == "latest_fact_wins":
            current_facts: dict[str, object] = {}
            expected_amount: object = turn_count
        else:
            saved_data = {
                "income.monthly": 100,
                "assets.cash": 900,
            }
            current_form: dict[str, object] = {"assets.cash": 999}
            if variant == "falsey-zero":
                current_form["income.monthly"] = 0
                expected_amount = 0
            elif variant == "falsey-empty-string":
                current_form["income.monthly"] = ""
                expected_amount = ""
            elif variant == "explicit-null":
                current_form["income.monthly"] = None
                expected_amount = None
            else:
                expected_amount = 100
            current_facts = dict(
                resolve_confirmed_facts(
                    active_step_fields={"income.monthly"},
                    saved_data=saved_data,
                    current_form=current_form,
                )
            )
            self.assertEqual(current_facts["assets.cash"], 900)

        window = build_context_window(
            store=store,
            scope=scope,
            current_confirmed_facts=current_facts,
        )
        self.assertEqual(window.confirmed_facts["income.monthly"], expected_amount)

    def _assert_budget_scenario(
        self,
        *,
        scenario: dict[str, object],
        store: FakeContextStore,
        scope: ContextScope,
        turn_count: int,
    ) -> None:
        variant = str(scenario["variant"])
        if variant == "unit-cap":
            policy = ContextBudgetPolicy(5, 20, 10)
        elif variant == "summary-cap":
            policy = ContextBudgetPolicy(1, 3_200, 5)
        else:
            policy = ContextBudgetPolicy(2 if variant == "turn-cap" else 1, 3_200, 600)

        window = build_context_window(
            store=store,
            scope=scope,
            current_confirmed_facts={},
            policy=policy,
        )

        self.assertLess(len(window.turns), turn_count)
        self.assertIsNotNone(window.summary)
        self.assertLessEqual(window.history_units, policy.max_history_units)
        if variant == "summary-cap":
            assert window.summary is not None
            self.assertGreater(window.summary.truncated_fact_count, 0)
        if variant == "latest-correction-under-cap":
            self.assertEqual(window.confirmed_facts["budget.latest"], turn_count - 1)


if __name__ == "__main__":
    unittest.main()
