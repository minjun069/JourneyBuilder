"""Regression checks for synthetic diversity design, with no model execution."""

from copy import deepcopy
import json
from pathlib import Path
import unittest

from evals.counseling_diversity import AXES, ORIGINAL_COUNTS, STEP_IDS, validate_diversity


class CounselingDiversityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[2]
        cls.originals = {}
        for kind, filename in (("coverage", "counseling-coverage-v1.json"),
                               ("behavior", "counseling-behavior-cases-v1.json")):
            source = json.loads((root / "rag/evaluation" / filename).read_text(encoding="utf-8"))
            source["cases"] = source["cases"][:ORIGINAL_COUNTS[kind]]
            cls.originals[kind] = source

    def setUp(self):
        self.coverage, self.behavior = (deepcopy(self.originals[kind]) for kind in ("coverage", "behavior"))
        # These small repeated examples test structure, not document relevance.
        for kind, count, document in (("coverage", 66, self.coverage), ("behavior", 54, self.behavior)):
            for index in range(count):
                axis = AXES[(index // 2) % len(AXES)]
                family = f"{kind}-family-{index // 2}"
                variant = f"variant-{index % 2}"
                variation = {"axis": axis, "family_id": family, "variant": variant,
                             "background": f"가상 배경 {variant}",
                             "financial_state": ["가상 자산 항목을 제공했다.", "가상 지출의 기준 기간을 제공했다."]}
                question = f"{family}의 같은 질문" if axis == AXES[0] else f"{family}의 질문 {variant}"
                case = deepcopy(document["cases"][0])
                case["variation"] = variation
                if kind == "coverage":
                    case.update(id=f"coverage-{index + 55}", question=question, concern=f"가상 관심사 {variant}",
                                step_ids=[STEP_IDS[(index * 4 + offset) % len(STEP_IDS)] for offset in range(4)])
                else:
                    case.update(id=f"dialogue-{index + 1:02d}", category=f"concern-{variant}",
                                turns=[{"turn_id": turn_id, "role": "user", "content": content}
                                       for turn_id, content in enumerate((question, "앞선 설명을 정정합니다.", "고맙습니다."), 1)],
                                checkpoints=[self.checkpoint(1, "initialize", "가상 최초 이해", None),
                                             self.checkpoint(2, "revise", "가상 정정 이해", None),
                                             self.checkpoint(3, "retain", "가상 정정 이해", 2)])
                document["cases"].append(case)

    @staticmethod
    def checkpoint(number, action, expected, reference):
        return {"turn_id": number, "understanding_action": action, "expected_understanding": expected,
                "must_preserve": ["명시한 관심사의 의미를 보존한다."],
                "must_not_infer": ["미확인 재무 상태를 확정하지 않는다."],
                "answer_expectations": ["현재 요청에 필요한 범위로 답한다."],
                "retrieval_policy": "none" if action == "retain" else "conditional",
                "carry_forward_from": reference}

    def validate(self):
        return validate_diversity(self.coverage, self.behavior)

    def test_summary_counts_design_without_copying_expected_results(self):
        result = self.validate()
        self.assertEqual({"coverage": 120, "behavior": 80}, result["case_counts"])
        self.assertEqual({"coverage": 66, "behavior": 54}, result["added_case_counts"])
        self.assertEqual({"coverage": 54, "behavior": 26}, result["preserved_case_counts"])
        self.assertEqual(320, result["review_template_slots"])
        self.assertEqual(162, result["checkpoint_count"])
        self.assertEqual(648, result["added_checkpoint_review_slots"])
        self.assertEqual(752, result["turn_review_template_slots"])
        self.assertEqual("design-validated-not-executed", result["status"])
        self.assertFalse(result["llm_evaluated"])
        self.assertFalse(result["ax_gain_measured"])
        self.assertIsNone(result["official_scores"])
        self.assertIsNone(result["measured_outcomes"])
        self.assertNotIn("가상 정정 이해", json.dumps(result, ensure_ascii=False))
        self.assertEqual(set(STEP_IDS), set(result["coverage_step_counts"]))
        self.assertTrue(all(count >= 4 for count in result["coverage_step_counts"].values()))

    def test_original_change_and_reordering_are_rejected(self):
        for kind, document in (("coverage", self.coverage), ("behavior", self.behavior)):
            with self.subTest(kind=kind):
                original = deepcopy(document["cases"])
                document["cases"][0]["id"] = "changed"
                with self.assertRaisesRegex(ValueError, "original .* changed or reordered"):
                    self.validate()
                document["cases"] = deepcopy(original)
                document["cases"][0], document["cases"][1] = document["cases"][1], document["cases"][0]
                with self.assertRaisesRegex(ValueError, "changed or reordered"):
                    self.validate()
                document["cases"] = original

    def test_fixed_counts_ids_and_unexecuted_status_are_required(self):
        self.coverage["cases"].pop()
        with self.assertRaisesRegex(ValueError, "exactly 120"):
            self.validate()
        self.setUp()
        self.behavior["cases"][-1]["id"] = "dialogue-55"
        with self.assertRaisesRegex(ValueError, "fixed ordered ID range"):
            self.validate()
        self.setUp()
        self.behavior["status"] = "executed"
        with self.assertRaisesRegex(ValueError, "not-executed"):
            self.validate()
        self.setUp()
        self.behavior["cases"][-1]["actual_response"] = "invented observation"
        with self.assertRaisesRegex(ValueError, "actual_response must remain empty"):
            self.validate()

    def test_paired_questions_backgrounds_and_finances_are_checked(self):
        mutations = (
            (lambda: self.coverage["cases"][55].update(question="다른 질문"), "same-question texts"),
            (lambda: self.coverage["cases"][55]["variation"].update(background="가상 배경 variant-0"), "backgrounds must differ"),
            (lambda: self.behavior["cases"][29]["variation"]["financial_state"].append("다른 재무사실"), "same-finances facts"),
            (lambda: self.behavior["cases"][29].update(category="concern-variant-0"), "concerns must differ"),
        )
        for mutate, expected in mutations:
            with self.subTest(expected=expected):
                self.setUp()
                mutate()
                with self.assertRaisesRegex(ValueError, expected):
                    self.validate()

    def test_missing_axis_unpaired_family_and_mixed_family_are_rejected(self):
        for case in self.coverage["cases"][54:]:
            if case["variation"]["axis"] == AXES[3]:
                case["variation"]["axis"] = AXES[2]
        with self.assertRaisesRegex(ValueError, "all four variation axes"):
            self.validate()
        self.setUp()
        self.coverage["cases"][55]["variation"]["family_id"] = "unpaired"
        with self.assertRaisesRegex(ValueError, "at least two cases"):
            self.validate()
        self.setUp()
        self.coverage["cases"][55]["variation"]["axis"] = AXES[2]
        with self.assertRaisesRegex(ValueError, "same axis"):
            self.validate()

    def test_step_counts_count_related_cases_once(self):
        for case in self.coverage["cases"][54:]:
            case["step_ids"] = ["intro"]
        with self.assertRaisesRegex(ValueError, "each step needs at least four"):
            self.validate()
        self.setUp()
        self.coverage["cases"][54]["step_ids"] = ["intro", "intro"]
        with self.assertRaisesRegex(ValueError, "supported and unique"):
            self.validate()

    def test_turn_checkpoint_alignment_and_prior_references_are_required(self):
        mutations = (
            (lambda c: c["turns"].pop(), "at least three user turns"),
            (lambda c: c["checkpoints"].pop(), "one-to-one"),
            (lambda c: c["turns"][1].update(turn_id=True), "consecutive integers"),
            (lambda c: c["checkpoints"][1].update(turn_id=1), "checkpoint IDs"),
            (lambda c: c["checkpoints"][1].update(carry_forward_from=2), "earlier turn"),
            (lambda c: c["checkpoints"][0].update(carry_forward_from=0), "earlier turn"),
            (lambda c: c["checkpoints"][2].update(carry_forward_from=True), "earlier turn"),
            (lambda c: c["checkpoints"][0].update(expected_understanding=" "), "nonempty text"),
        )
        for mutate, expected in mutations:
            with self.subTest(expected=expected):
                self.setUp()
                mutate(self.behavior["cases"][26])
                with self.assertRaisesRegex(ValueError, expected):
                    self.validate()

    def test_revision_must_have_followup_and_retain_is_an_observation_plan(self):
        revision = self.behavior["cases"][30]
        revision["checkpoints"][2]["carry_forward_from"] = 1
        with self.assertRaisesRegex(ValueError, "follow-up referencing an earlier revise"):
            self.validate()
        self.setUp()
        # A paraphrase is allowed: structural validation cannot judge meaning.
        self.behavior["cases"][26]["checkpoints"][2]["expected_understanding"] = "정정된 관심사를 같은 의미로 유지한다."
        self.validate()
        for case in self.behavior["cases"][26:]:
            case["checkpoints"][2]["understanding_action"] = "revise"
        with self.assertRaisesRegex(ValueError, "at least one retain"):
            self.validate()


if __name__ == "__main__":
    unittest.main()
