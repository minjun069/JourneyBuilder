from django.urls import path

from .views import CustomerDetailView, CustomerListView, PingView, SummaryView

urlpatterns = [
    path('ping/', PingView.as_view(), name='admin-ping'),
    path('customers/', CustomerListView.as_view(), name='admin-customer-list'),
    path('customers/<int:user_id>/', CustomerDetailView.as_view(), name='admin-customer-detail'),
    path('analytics/summary/', SummaryView.as_view(), name='admin-analytics-summary'),
]
