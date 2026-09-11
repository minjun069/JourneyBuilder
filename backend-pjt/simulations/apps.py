from django.apps import AppConfig


class SimulationsConfig(AppConfig):
    """은퇴 몬테카를로·What-if 실행 API. 모델이 없어 마이그레이션도 없다.

    시뮬레이션은 아무것도 저장하지 않는다. 원본 상담 데이터와 보고서를 읽기만 하고
    결과는 응답으로만 나간다. reports 앱에 넣지 않은 이유가 이것이다 —
    그 앱의 정체성은 영속되는 Report 모델과 버전 관리다.
    """

    default_auto_field = 'django.db.models.BigAutoField'
    name = 'simulations'
