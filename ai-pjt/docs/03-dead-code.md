# Dead Code 명세

## `core/llm.py`

### `_create_embeddings()` 함수 및 `embeddings` 변수

```python
def _create_embeddings() -> Embeddings:
    ...

# embeddings: Embeddings = _create_embeddings()  ← 주석 처리됨
```

**위치**: `core/llm.py`

**상태**: 함수는 정의되어 있으나 호출되지 않음. `embeddings` 변수 선언도 주석 처리 상태.

**배경**: 초기에는 LangChain 기반 embedding (`OpenAIEmbeddings`, `GoogleGenerativeAIEmbeddings`)을 사용했으나,
`rag/retriever.py`가 LlamaIndex 기반으로 재작성되면서 LlamaIndex용 `embed_model`(`_create_llamaindex_embedding()`)로 대체됨.
`retriever_langchain.py`(구 LangChain retriever)가 `embeddings`를 import했으나 해당 파일은 삭제됨.

**영향**: 현재 어디서도 import되지 않아 런타임 오류 없음.

**처리 방향**: 추후 LangChain retriever 복원 계획이 없다면 `_create_embeddings()` 함수와 관련 import(`OpenAIEmbeddings`, `GoogleGenerativeAIEmbeddings`) 제거 가능.
