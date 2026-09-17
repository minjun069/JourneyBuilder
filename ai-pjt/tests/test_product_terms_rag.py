from __future__ import annotations

from pathlib import Path
import re
import runpy
import unittest

from rag.pipeline.chunking import MarkdownStructureChunker
from rag.pipeline.indexing import IndexedRetriever
from rag.pipeline.models import (
    Chunk,
    DocumentMetadata,
    EmbeddedChunk,
    SearchResult,
    SourceDocument,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
TERM_DOCUMENT_PATH = (
    REPOSITORY_ROOT
    / "ai-pjt/rag/data/financial-knowledge/product-terms.md"
)
_TERM_BLOCK = re.compile(
    r"(?ms)^<!-- product-term:start -->\s*\n"
    r"(?P<body>## [^\n]+\n.*?)"
    r"^<!-- product-term:end -->\s*$"
)
_FIELD = re.compile(r"(?m)^- \*\*(?P<name>[^*]+):\*\*\s*(?P<value>.+?)\s*$")


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

    basic_source = (forms_root / "BasicInfoForm.vue").read_text(encoding="utf-8")
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


def _metadata() -> DocumentMetadata:
    return DocumentMetadata(
        source_id="retirewise-product-terms-test",
        title="RetireWise 제품 용어 설명",
        publisher="S15P21B109",
        source_locators=(str(TERM_DOCUMENT_PATH),),
        published_at="2026-09-17",
        as_of_date="2026-09-17",
        version="1.0.0",
        scope="test",
        review_status="synthetic",
        reviewed_by="test",
        document_kind="financial-knowledge",
        collection="synthetic_financial",
        license="synthetic only",
        sensitivity="public",
        sha256="",
        catalog_document_id="product-terms-test",
        file_path=str(TERM_DOCUMENT_PATH),
    )


def _term_document() -> SourceDocument:
    return SourceDocument(
        metadata=_metadata(),
        text=TERM_DOCUMENT_PATH.read_text(encoding="utf-8"),
    )


def _term_records() -> list[tuple[str, dict[str, str]]]:
    text = TERM_DOCUMENT_PATH.read_text(encoding="utf-8")
    records: list[tuple[str, dict[str, str]]] = []
    for match in _TERM_BLOCK.finditer(text):
        body = match.group("body")
        title = re.search(r"(?m)^##\s+(.+?)\s*$", body)
        if title is None:
            raise AssertionError("term block has no title")
        records.append((title.group(1), {
            field.group("name"): field.group("value")
            for field in _FIELD.finditer(body)
        }))
    return records


def _heading(chunk: Chunk) -> str:
    match = re.search(r"(?m)^##\s+(.+?)\s*$", chunk.text)
    if match is None:
        raise AssertionError("chunk has no term heading")
    return match.group(1)


def _embedded_term_chunks() -> list[EmbeddedChunk]:
    chunks = MarkdownStructureChunker().chunk_document(_term_document())
    return [EmbeddedChunk(chunk=chunk, vector=(1.0, 0.0)) for chunk in chunks]


class ProductTermDocumentTests(unittest.TestCase):
    def test_every_term_has_required_markdown_fields(self) -> None:
        records = _term_records()
        self.assertEqual(len(records), 188)
        self.assertEqual(len({term for term, _ in records}), len(records))
        for term, fields in records:
            with self.subTest(term=term):
                self.assertEqual(
                    set(fields),
                    {"용어 ID", "별칭", "정의", "사용 맥락", "주의사항"},
                )
                self.assertTrue(all(value.strip() for value in fields.values()))

    def test_every_controlled_product_surface_is_in_markdown(self) -> None:
        surfaces: set[str] = set()
        for term, fields in _term_records():
            surfaces.add(term)
            if fields["별칭"] != "없음":
                surfaces.update(alias.strip() for alias in fields["별칭"].split(","))
        required = _frontend_surface_terms() | _backend_surface_terms()
        self.assertEqual(sorted(required - surfaces), [])

    def test_each_product_term_is_one_independent_chunk(self) -> None:
        chunks = MarkdownStructureChunker().chunk_document(_term_document())
        self.assertEqual(len(chunks), 188)
        for chunk in chunks:
            with self.subTest(chunk=chunk.chunk_id):
                self.assertEqual(len(re.findall(r"(?m)^##\s+", chunk.text)), 1)
                self.assertIn("- **정의:**", chunk.text)
                self.assertIn("- **사용 맥락:**", chunk.text)
                self.assertIn("- **주의사항:**", chunk.text)


class ProductTermRetrievalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.embedded = _embedded_term_chunks()

    def test_particles_spacing_aliases_and_similar_terms(self) -> None:
        cases = {
            "유동성이 뭐야?": "유동성",
            "유동성을 설명해줘": "유동성",
            "유동성은 어떤 뜻이야?": "유동성",
            "유동성 중시의 의미": "유동성 중시",
            "유동성중시는 무슨 뜻이야?": "유동성 중시",
            "현금화 용이성을 설명해줘": "유동성",
            "현금흐름 관리가 뭐야?": "현금흐름 관리",
            "재무 관리의 뜻": "재무관리",
        }
        for query, expected in cases.items():
            with self.subTest(query=query):
                ranked = IndexedRetriever._bm25(self.embedded, query, top_k=3)
                self.assertTrue(ranked)
                self.assertEqual(_heading(ranked[0][0].chunk), expected)

    def test_title_exact_match_outranks_alias_then_body(self) -> None:
        text = """<!-- product-term-document -->
<!-- product-term:start -->
## 유동성
- **용어 ID:** title
- **별칭:** 없음
- **정의:** 제목 일치
- **사용 맥락:** 테스트
- **주의사항:** 없음
<!-- product-term:end -->
<!-- product-term:start -->
## 환금성
- **용어 ID:** alias
- **별칭:** 유동성
- **정의:** 별칭 일치
- **사용 맥락:** 테스트
- **주의사항:** 없음
<!-- product-term:end -->
<!-- product-term:start -->
## 현금성
- **용어 ID:** body
- **별칭:** 없음
- **정의:** 본문에서 유동성을 언급
- **사용 맥락:** 테스트
- **주의사항:** 없음
<!-- product-term:end -->"""
        chunks = MarkdownStructureChunker().chunk_document(
            SourceDocument(metadata=_metadata(), text=text)
        )
        embedded = [
            EmbeddedChunk(chunk=chunk, vector=(1.0, 0.0))
            for chunk in chunks
        ]
        ranked = IndexedRetriever._bm25(embedded, "유동성은 뭐야?", top_k=3)

        self.assertEqual(
            [_heading(item.chunk) for item, _ in ranked],
            ["유동성", "환금성", "현금성"],
        )
        self.assertGreater(ranked[0][1], ranked[1][1])
        self.assertGreater(ranked[1][1], ranked[2][1])

    def test_dense_and_weighted_bm25_are_combined_with_rrf(self) -> None:
        title, alias, body = IndexedRetriever._bm25(
            self.embedded,
            "유동성은 뭐야?",
            top_k=3,
        )
        dense = [body[0], alias[0], title[0]]

        class Store:
            @staticmethod
            def active_collection(_logical: str) -> str:
                return "physical"

            @staticmethod
            def search(_collection: str, _vector: tuple[float, ...], _top_k: int):
                return [
                    SearchResult(chunk=item.chunk, score=0.9 - rank * 0.1, rank=rank)
                    for rank, item in enumerate(dense, start=1)
                ]

            def list_chunks(self, _collection: str):
                return self_chunks

        class Embedding:
            dimensions = 2

            @staticmethod
            def embed_texts(_texts: list[str]) -> list[tuple[float, ...]]:
                return [(1.0, 0.0)]

        self_chunks = [title[0], alias[0], body[0]]
        retriever = IndexedRetriever(
            store=Store(),
            embedding=Embedding(),
            fetch_k=3,
            minimum_dense_score=0.0,
        )
        result = retriever.retrieve("logical", "유동성은 뭐야?", top_k=3)

        self.assertEqual(_heading(result[0].chunk), "유동성")
        self.assertIsNotNone(result[0].dense_score)
        self.assertIsNotNone(result[0].sparse_score)


if __name__ == "__main__":
    unittest.main()
