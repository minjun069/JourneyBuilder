"""의도적으로 비어 있다.

CI(.gitlab-ci.yml test:backend)는 backend-pjt/tests 와 contract_tests 만 discover 한다.
앱 내부 tests.py 는 실행되지 않으므로 여기에 중요한 검사를 두면 게이트가 걸리지 않는다.
시뮬레이션 테스트는 backend-pjt/tests/ 와 backend-pjt/contract_tests/ 에 있다.
"""
