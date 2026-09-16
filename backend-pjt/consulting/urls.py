from django.urls import path
from .views import (
    ConsultationListCreateView, ConsultationDetailView,
    MessageListView,
    BasicInfoView, FinancialMindsetView, FinancialSatisfactionView,
    FinancialAssetsView, FinancialAssetsEmptyConfirmationView,
    PensionAssetsView, RealEstateAssetsView,
    LiabilitiesView, IncomesView, ExpensesView,
    GoalPeriodsView, GoalMilestonesView,
)

urlpatterns = [
    # 세션
    path('consultations/', ConsultationListCreateView.as_view(), name='consultation-list'),
    path('consultations/<int:session_id>/', ConsultationDetailView.as_view(), name='consultation-detail'),

    # 메시지
    path('consultations/<int:session_id>/messages/', MessageListView.as_view(), name='consultation-messages'),

    # 재무정보 — 1:1 폼
    path('consultations/<int:session_id>/data/basic-info/', BasicInfoView.as_view(), name='financial-basic-info'),
    path('consultations/<int:session_id>/data/financial-mindset/', FinancialMindsetView.as_view(), name='financial-mindset'),
    path('consultations/<int:session_id>/data/financial-satisfaction/', FinancialSatisfactionView.as_view(), name='financial-satisfaction'),

    # 재무정보 — 1:N 폼 
    path('consultations/<int:session_id>/data/financial-assets/', FinancialAssetsView.as_view(), name='financial-assets'),
    path('consultations/<int:session_id>/data/financial-assets/confirm-empty/', FinancialAssetsEmptyConfirmationView.as_view(), name='financial-assets-confirm-empty'),
    path('consultations/<int:session_id>/data/pension-assets/', PensionAssetsView.as_view(), name='pension-assets'),
    path('consultations/<int:session_id>/data/real-estate-assets/', RealEstateAssetsView.as_view(), name='real-estate-assets'),
    path('consultations/<int:session_id>/data/liabilities/', LiabilitiesView.as_view(), name='liabilities'),
    path('consultations/<int:session_id>/data/incomes/', IncomesView.as_view(), name='incomes'),
    path('consultations/<int:session_id>/data/expenses/', ExpensesView.as_view(), name='expenses'),

    # 재무목표
    path('consultations/<int:session_id>/data/goal-periods/', GoalPeriodsView.as_view(), name='goal-periods'),
    path('consultations/<int:session_id>/data/goal-milestones/', GoalMilestonesView.as_view(), name='goal-milestones'),
]
