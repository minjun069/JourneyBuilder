"""
⚠️  EXPERIMENTS ONLY — InputAssist Agent 실행 결과 확인용 스크립트

실행:
  cd ai-pjt
  python experiments/test_input_assist.py
"""
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import find_dotenv, load_dotenv
load_dotenv(find_dotenv())

from agents.input_assist import graph as input_assist_graph

CUSTOMER_INFO = {
    "confirmed_info": {
        "name": "홍길동",
        "age": 55,
        "annual_income": 60_000_000,
        "job": "회사원",
    },
}


def print_output(output: dict, elapsed: float) -> None:
    response = output.get("response", "")
    update = output.get("update", {})
    print(f"\n[응답] {response}")
    if update:
        print(f"[폼 업데이트] {json.dumps(update, ensure_ascii=False)}")
    else:
        print("[폼 업데이트] 없음")
    print(f"({elapsed:.1f}s)\n")


async def main() -> None:
    while True:
        message = input("입력 (종료: q): ").strip()
        if message.lower() == "q":
            break
        if not message:
            continue

        t0 = time.perf_counter()
        result = await input_assist_graph.ainvoke({
            "message": message,
            "step_id": "financial-assets",
            "customer_info": CUSTOMER_INFO,
            "next": "",
            "output": {},
        })
        print_output(result["output"], time.perf_counter() - t0)


if __name__ == "__main__":
    asyncio.run(main())
