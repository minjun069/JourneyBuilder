from django.urls import path

from .v2_views import V2MessageStreamView


urlpatterns = [
    path(
        'consultations/<int:session_id>/messages/stream/',
        V2MessageStreamView.as_view(),
        name='v2-consultation-messages-stream',
    ),
]
