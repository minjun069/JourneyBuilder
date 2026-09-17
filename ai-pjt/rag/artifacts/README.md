# RAG DB artifacts

이 디렉터리는 RAG 담당자가 확정한 공식 PostgreSQL 논리 백업을 보관한다. 파일은
`official__official-<index-version>.tar.gz` 형식이며 다음 내용만 포함한다.

- 통과 여부와 관계없이 먼저 확정되는 두 collection qualification report
- qualified physical index에 속한 `rag_index_manifests`, `rag_index_chunks` 행
- 생성 DB의 `rag_active_indexes` 스냅샷
- catalog, 계산 정책, embedding 모델, Git revision, 생성자와 checksum manifest

첫 실제 공식 파일은 크기를 확인해 20 MiB 이하이면 일반 Git으로 추적한다. 이후
누적 크기가 저장소 운영에 부담이 될 때 이 디렉터리의 `*.tar.gz`만 Git LFS로
전환한다. 자격 증명, 전체 PostgreSQL DB, Django 테이블은 artifact에 넣지 않는다.

`experiment__...` 파일은 로컬 검증용이며 배포 DB에서 활성화할 수 없다. 공식
artifact 생성·교체와 승인 corpus 갱신은 RAG 담당자만 수행한다. 다른 개발자와
운영자는 전달받은 공식 파일의 checksum을 검사한 뒤 import만 수행한다.

배포 이미지에는 이 디렉터리가 포함된다. `rag.bootstrap`은 모든 `official__*.tar.gz`
파일의 checksum·qualification·manifest 결합을 검증하고 가장 높은 catalog semantic
version 하나를 선택해 멱등 import 후 두 active pointer를 원자적으로 전환한다. Compose의
`rag-bootstrap` one-shot이 성공해야 `ai-pjt`가 시작하며, AI readiness는 두 logical
collection에 active manifest와 chunk가 있는지 다시 확인한다. 새 공식 artifact를 이
디렉터리에 추가하면 다음 GitLab 배포부터 자동 선택되고, 이미지 롤백 때는 직전
`rag-bootstrap:last-good` 이미지의 artifact가 다시 활성화된다.

현재 최신 공식 artifact는 `official__official-catalog-2-2-0.tar.gz`이다. catalog
2.2.0·66문항 GMS qualification을 통과했으며 파일 SHA-256은
`3e6e5e20e3e490578ad06a435275c9e196655ff915fe4d98b309e87435792a56`다.
