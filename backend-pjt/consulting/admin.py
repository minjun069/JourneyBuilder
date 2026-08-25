from django.contrib import admin
from .models import (
    ConsultingSession, Message,
    BasicInfo,
    FinancialMindset, FinancialSatisfaction,
    FinancialAsset, PensionAsset, RealEstateAsset,
    Liability, Income, Expense,
    FinancialGoalPeriod, FinancialGoalPeriodItem, FinancialGoalMilestone,
)


@admin.register(ConsultingSession)
class ConsultingSessionAdmin(admin.ModelAdmin):
    list_display  = ['id', 'user', 'status', 'created_at', 'completed_at']
    search_fields = ['user__email', 'user__name']
    list_filter   = ['status']


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ['id', 'session', 'role', 'created_at']
    list_filter  = ['role']


@admin.register(BasicInfo)
class BasicInfoAdmin(admin.ModelAdmin):
    list_display = ['id', 'session', 'age', 'job', 'marital_status', 'need', 'retirement_age', 'end_age', 'updated_at']


@admin.register(FinancialMindset)
class FinancialMindsetAdmin(admin.ModelAdmin):
    list_display = ['id', 'session', 'updated_at']


@admin.register(FinancialSatisfaction)
class FinancialSatisfactionAdmin(admin.ModelAdmin):
    list_display = ['id', 'session', 'cashflow_score', 'risk_score', 'investment_score', 'management_score', 'inheritance_score', 'updated_at']


@admin.register(FinancialAsset)
class FinancialAssetAdmin(admin.ModelAdmin):
    list_display = ['id', 'session', 'name', 'current_value', 'purpose']


@admin.register(PensionAsset)
class PensionAssetAdmin(admin.ModelAdmin):
    list_display = ['id', 'session', 'type', 'name', 'current_value', 'pension_start_age']


@admin.register(RealEstateAsset)
class RealEstateAssetAdmin(admin.ModelAdmin):
    list_display = ['id', 'session', 'type', 'name', 'current_value', 'acquisition_date']


@admin.register(Liability)
class LiabilityAdmin(admin.ModelAdmin):
    list_display = ['id', 'session', 'type', 'name', 'balance', 'interest_rate', 'maturity_date']


@admin.register(Income)
class IncomeAdmin(admin.ModelAdmin):
    list_display = ['id', 'session', 'type', 'amount', 'is_fixed']


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ['id', 'session', 'name', 'amount', 'is_fixed']


class FinancialGoalPeriodItemInline(admin.TabularInline):
    model = FinancialGoalPeriodItem
    extra = 0


@admin.register(FinancialGoalPeriod)
class FinancialGoalPeriodAdmin(admin.ModelAdmin):
    list_display = ['id', 'session', 'phase', 'from_age', 'to_age']
    list_filter  = ['phase']
    inlines      = [FinancialGoalPeriodItemInline]


@admin.register(FinancialGoalMilestone)
class FinancialGoalMilestoneAdmin(admin.ModelAdmin):
    list_display = ['id', 'session', 'phase', 'age', 'category', 'content']
    list_filter  = ['phase']
