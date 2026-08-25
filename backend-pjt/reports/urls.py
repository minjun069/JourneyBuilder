from django.urls import path
from .views import ReportListView, ReportDetailView

urlpatterns = [
    path('consultations/<int:session_id>/reports/', ReportListView.as_view()),
    path('consultations/<int:session_id>/reports/<slug:report_type>/', ReportDetailView.as_view()),
]
