from django.urls import path

from .views import SimulationRunView

urlpatterns = [
    # reports/urls.py 의 <slug:report_type> 이 catch-all 이라
    # .../reports/simulation/ 로 두면 ReportDetailView 가 먼저 잡는다. 별도 경로를 쓴다.
    path(
        'consultations/<int:session_id>/simulations/',
        SimulationRunView.as_view(),
        name='simulation-run',
    ),
]
