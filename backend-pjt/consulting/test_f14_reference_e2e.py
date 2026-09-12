from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.models import User
from reports.policy import active_policy_version

from .ai_lifecycle import create_v2_assistant_message, decide_change
from .models import (
    ConsultingSession,
    FinancialGoalPeriod,
    FinancialGoalPeriodItem,
)
from .views import GoalPeriodsView


class F14ReferenceApprovalSaveE2ETests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="f14-reference@example.invalid",
            password="test-only-password",
            name="F14 reference tester",
        )
        self.session = ConsultingSession.objects.create(
            user=self.user,
            status=ConsultingSession.Status.IN_PROGRESS,
        )
        self.period = FinancialGoalPeriod.objects.create(
            session=self.session,
            phase="before",
            from_age=45,
            to_age=65,
        )
        self.item = FinancialGoalPeriodItem.objects.create(
            period=self.period,
            name="주거비",
            amount=None,
            is_fixed=True,
        )

    def test_housing_reference_proposal_requires_approval_then_saves_exact_db_value(self):
        reference = {
            "reference_id": "household_monthly_expense_housing_avg",
            "age": 45,
            "unit": "KRW",
            "period": "monthly",
            "population": "kr_general_households_1plus",
            "comparator": "eq",
            "source_id": "kostat-household-expenses-2025",
            "source_version": "2025",
            "as_of": "2025-12-31",
            "calculation_policy_version": active_policy_version(),
        }
        output = {
            "response": "승인된 주거비 기준으로 기존 빈 항목 변경안을 만들었습니다.",
            "step_id": "financial-goals",
            "action": {
                "type": "form_change",
                "target": "draft_form",
                "user_requested": True,
                "confidence": 1.0,
                "changes": [{
                    "section": "goal_periods",
                    "path": "items[].amount",
                    "operation": "update",
                    "parent_item_id": self.period.id,
                    "item_id": self.item.id,
                    "value": 361_000,
                    "unit": "KRW",
                    "reason": "검증된 선행 task artifact binding",
                    "policy_version": active_policy_version(),
                    "source": {
                        "source_id": "kostat-household-expenses-2025",
                        "title": "2025년 연간 가계동향조사 결과",
                        "reference_period": "monthly",
                    },
                    "retrieved_at": "2025-12-31",
                    "transform": "승인 기준값을 원 단위 월 금액으로 직접 매핑",
                    "assumption_level": "household_benchmark",
                    "reference": reference,
                }],
            },
        }

        message = create_v2_assistant_message(
            self.session,
            {"request_id": "ai_req_f14_reference", "output": output},
            "financial-goals",
        )
        action = message.extracted_data["action"]
        change = action["changes"][0]
        self.assertEqual(action["mode"], "proposal")
        self.assertEqual(action["status"], "pending")
        self.assertEqual(change["decision_status"], "pending")
        self.item.refresh_from_db()
        self.assertIsNone(self.item.amount)

        approved = decide_change(
            message,
            change_id=change["change_id"],
            decision="approved",
        )
        self.assertEqual(approved["decision_status"], "approved")
        self.assertEqual(approved["change"]["value"], 361_000)
        self.assertEqual(approved["change"]["reference"], reference)
        self.item.refresh_from_db()
        self.assertIsNone(self.item.amount)

        request = APIRequestFactory().put(
            "/api/consultations/ignored/data/goal-periods/",
            [{
                "id": self.period.id,
                "phase": "before",
                "from_age": 45,
                "to_age": 65,
                "items": [{
                    "id": self.item.id,
                    "name": "주거비",
                    "amount": approved["change"]["value"],
                    "is_fixed": True,
                }],
            }],
            format="json",
        )
        force_authenticate(request, user=self.user)
        response = GoalPeriodsView.as_view()(request, session_id=self.session.id)

        self.assertEqual(response.status_code, 200, response.data)
        self.item.refresh_from_db()
        self.assertEqual(self.item.amount, 361_000)
        self.assertEqual(
            response.data["data"][0]["items"][0]["amount"],
            361_000,
        )
