"""
⚠️  EXPERIMENTS ONLY — QA Agent 실행 결과 확인용 스크립트

실행:
  cd ai-pjt
  python experiments/test_qa.py
"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from llama_index.core import Document

from agents.qa import graph as qa_graph
from rag.retriever import initialize_test_retriever

SCRIPTS_DIR = Path(__file__).parent / "scripts"

CUSTOMER_INFO = {
    "name": "홍길동",
    "age": 55,
    "annual_income": 60_000_000,
    "assets": 300_000_000,
    "pension_savings_balance": 15_000_000,
}


def load_test_docs() -> list[Document]:
    docs = []
    for txt_file in sorted(SCRIPTS_DIR.glob("*.txt")):
        content = txt_file.read_text(encoding="utf-8")
        docs.append(Document(text=content, metadata={"source": txt_file.name}))
    return docs


async def main() -> None:
    docs = load_test_docs()
    if not docs:
        print(f"ERROR: {SCRIPTS_DIR} 에 .txt 파일이 없습니다.")
        sys.exit(1)

    print(f"문서 {len(docs)}개 로드 완료")
    initialize_test_retriever(docs)
    print("리트리버 초기화 완료\n")

    while True:
        question = input("질문 (종료: q): ").strip()
        if question.lower() == "q":
            break
        if not question:
            continue
        t0 = time.perf_counter()
        result = await qa_graph.ainvoke({
            "message": question,
            "step_id": "test-asset-diagnosis",
            "customer_info": CUSTOMER_INFO,
            "next": "",
        })
        print(f"→ {result['message']}  ({time.perf_counter() - t0:.1f}s)\n")


if __name__ == "__main__":
    asyncio.run(main())
