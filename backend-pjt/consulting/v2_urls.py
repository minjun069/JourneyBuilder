from django.urls import path

from .v2_views import (
    V2ChangeApproveView,
    V2ChangeRejectView,
    V2MessageStreamView,
)


urlpatterns = [
    path(
        'consultations/<int:session_id>/messages/stream/',
        V2MessageStreamView.as_view(),
        name='v2-consultation-messages-stream',
    ),
    path(
        'consultations/<int:session_id>/ai-actions/<str:action_id>/changes/'
        '<str:change_id>/approve/',
        V2ChangeApproveView.as_view(),
        name='v2-ai-change-approve',
    ),
    path(
        'consultations/<int:session_id>/ai-actions/<str:action_id>/changes/'
        '<str:change_id>/reject/',
        V2ChangeRejectView.as_view(),
        name='v2-ai-change-reject',
    ),
]
