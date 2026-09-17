from __future__ import annotations

import json
from pathlib import Path
import re
import runpy
import tempfile
import unittest

from core.product_glossary import (
    ProductGlossaryError,
    answer_product_term_question,
    load_product_glossary,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
GLOSSARY_PATH = (
    REPOSITORY_ROOT
    / "contract-fixtures/ai-lifecycle/v2/product-glossary.json"
)


def _quoted_values(value: str) -> set[str]:
    return set(re.findall(r"['\"]([^'\"]+)['\"]", value))


def _frontend_surface_terms() -> set[str]:
    terms: set[str] = set()
    process_source = (
        REPOSITORY_ROOT / "front-pjt/src/views/ai/data/processSteps.js"
    ).read_text(encoding="utf-8")
    terms.update(
        value.replace("\\n", " ")
        for value in re.findall(r"label:\s*'([^']+)'", process_source)
    )

    forms_root = REPOSITORY_ROOT / "front-pjt/src/components/forms"
    for path in forms_root.glob("*.vue"):
        script = path.read_text(encoding="utf-8").split("</script>", maxsplit=1)[0]
        terms.update(re.findall(r"label:\s*'([^']+)'", script))
        for values in re.findall(r"(?:options|items):\s*\[([^\]]*)\]", script):
            terms.update(_quoted_values(values))
        for values in re.findall(
            r"const\s+(?:JOBS|NEEDS|EXPENSE_CATS)\s*=\s*\[([^\]]*)\]",
            script,
        ):
            terms.update(_quoted_values(values))

    basic_source = (
        forms_root / "BasicInfoForm.vue"
    ).read_text(encoding="utf-8")
    terms.update(
        value.strip()
        for value in re.findall(
            r'<span class="field-label">([^<{]+)</span>',
            basic_source,
        )
    )
    return {
        term
        for term in terms
        if term and not re.fullmatch(r"\d+년", term)
    }


def _backend_surface_terms() -> set[str]:
    registry = runpy.run_path(
        str(REPOSITORY_ROOT / "backend-pjt/consulting/field_registry.py")
    )
    terms: set[str] = set()
    for field in registry["FIELDS"]:
        if field.sensitivity == "operational":
            continue
        terms.add(field.label)
        for choice in field.constraints.get("choices", []):
            if isinstance(choice, str) and not choice.isascii():
                terms.add(choice)

    grading_source = (
        REPOSITORY_ROOT / "backend-pjt/adminapi/grading.py"
    ).read_text(encoding="utf-8")
    indicator_block = grading_source.split("INDICATOR_DEFS = (", maxsplit=1)[1]
    indicator_block = indicator_block.split("\n)\n", maxsplit=1)[0]
    terms.update(re.findall(r"\('([^']+)',\s*'[^']+'", indicator_block))
    return terms


class ProductGlossaryContractTests(unittest.TestCase):
    def tearDown(self) -> None:
        load_product_glossary.cache_clear()

    def test_fixture_loads_and_source_paths_exist(self) -> None:
        glossary = load_product_glossary(GLOSSARY_PATH)
        self.assertEqual(glossary.version, "2026-09-17.v1")
        self.assertGreaterEqual(len(glossary.entries), 150)

        payload = json.loads(GLOSSARY_PATH.read_text(encoding="utf-8"))
        for source_id, relative_path in payload["sources"].items():
            with self.subTest(source=source_id):
                self.assertTrue((REPOSITORY_ROOT / relative_path).exists())

    def test_every_controlled_product_surface_has_a_definition(self) -> None:
        glossary = load_product_glossary(GLOSSARY_PATH)
        required_terms = _frontend_surface_terms() | _backend_surface_terms()
        missing = sorted(
            term for term in required_terms if not glossary.contains_surface(term)
        )
        self.assertEqual(missing, [])
        for term in required_terms:
            with self.subTest(explainable_term=term):
                self.assertIsNotNone(
                    answer_product_term_question(f"{term}이 뭐야?")
                )

    def test_longest_surface_wins_and_multiple_terms_can_be_compared(self) -> None:
        self.assertIn(
            "빠르게 현금으로 바꾸어",
            answer_product_term_question("유동성 중시에서 유동성이 뭐야?") or "",
        )
        comparison = answer_product_term_question(
            "수익 중시와 유동성 중시는 어떤 차이야?"
        ) or ""
        self.assertIn("‘수익 중시’", comparison)
        self.assertIn("‘유동성 중시’", comparison)

    def test_plain_product_term_mention_does_not_force_a_definition(self) -> None:
        self.assertIsNone(answer_product_term_question("기본 정보를 저장해 줘"))
        self.assertIsNone(answer_product_term_question("내 부채를 알려줘"))
        self.assertIsNone(answer_product_term_question("가계수지지표가 왜 위험이야?"))

    def test_one_character_units_match_only_as_standalone_terms(self) -> None:
        unit_answer = answer_product_term_question("월은 어떤 단위야?") or ""
        self.assertIn("‘월’", unit_answer)
        housing_answer = answer_product_term_question("주택자금이 뭐야?") or ""
        self.assertIn("‘주택자금’", housing_answer)
        self.assertNotIn("‘주’", housing_answer)

    def test_indicator_guide_uses_the_canonical_product_names(self) -> None:
        guide = (
            REPOSITORY_ROOT / "front-pjt/src/views/ai/data/stepGuideMessages.js"
        ).read_text(encoding="utf-8")
        for term in (
            "가계수지지표",
            "총저축성향지표",
            "총부채상환지표(DSR)",
            "비상예비금지표",
            "금융자산비중지표",
        ):
            self.assertIn(term, guide)
        for legacy_term in ("부채상환비율(DTI)", "유동성 비율", "투자자산 비율"):
            self.assertNotIn(legacy_term, guide)

    def test_duplicate_surface_is_rejected(self) -> None:
        payload = json.loads(GLOSSARY_PATH.read_text(encoding="utf-8"))
        payload["groups"][0]["entries"].append({
            "term_id": "duplicate-liquidity",
            "term": "유동성",
            "aliases": [],
            "definition": "중복 표면형입니다.",
        })
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "glossary.json"
            path.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
            with self.assertRaises(ProductGlossaryError):
                load_product_glossary(path)


if __name__ == "__main__":
    unittest.main()
