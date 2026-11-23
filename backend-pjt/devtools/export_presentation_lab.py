"""Generate chart previews using real calculators and a fresh in-memory database.

Run in a separate Python process; config.settings and external databases are never
loaded. Only the synthetic inputs in presentation_inputs.py are accepted.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
from unittest.mock import patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))


def configure_database():
    import django
    from django.conf import settings

    if settings.configured:
        raise RuntimeError("Run the presentation export in a fresh process, never inside a running backend.")
    settings.configure(
        SECRET_KEY="isolated-presentation-lab-only",
        INSTALLED_APPS=["django.contrib.contenttypes", "django.contrib.auth", "accounts", "consulting", "reports"],
        AUTH_USER_MODEL="accounts.User",
        DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
        USE_TZ=True,
        TIME_ZONE="Asia/Seoul",
        DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
    )
    django.setup()
    from django.apps import apps
    from django.db import connection

    with connection.schema_editor() as editor:
        for model in apps.get_models():
            editor.create_model(model)


def create_session(scenario):
    from accounts.models import User
    from consulting import models

    user = User.objects.create(email=f"{scenario['id']}@presentation.invalid", name=scenario["title"])
    session = models.ConsultingSession.objects.create(user=user, status="completed")
    source = scenario["input"]
    models.BasicInfo.objects.create(session=session, **source["basic_info"])
    relations = {
        "financial_assets": models.FinancialAsset,
        "pension_assets": models.PensionAsset,
        "real_estate_assets": models.RealEstateAsset,
        "liabilities": models.Liability,
        "incomes": models.Income,
        "expenses": models.Expense,
        "goal_milestones": models.FinancialGoalMilestone,
    }
    for key, model in relations.items():
        for row in source[key]:
            model.objects.create(session=session, **row)
    for row in source["goal_periods"]:
        period = models.FinancialGoalPeriod.objects.create(
            session=session, **{key: value for key, value in row.items() if key != "items"},
        )
        for item in row["items"]:
            models.FinancialGoalPeriodItem.objects.create(period=period, **item)
    return session


def export_scenario(scenario, as_of):
    from reports.services import generate_presentation_reports
    from reports.snapshots import build_calculation_input_snapshot, digest_snapshot
    from simulations.errors import SimulationError
    from simulations.services import load_policy, package_version, prepare_input, run_analysis

    session = create_session(scenario)
    reports = generate_presentation_reports(session)
    snapshot = build_calculation_input_snapshot(session)
    assumptions, policy_version = load_policy()
    try:
        prepared = prepare_input(snapshot)
        analysis, _elapsed_ms = run_analysis(prepared, assumptions)
        simulation = {
            "success": True,
            "data": {"baseline_key": f"dev:{scenario['id']}:{digest_snapshot(snapshot)}", "analysis": analysis},
            "meta": {"simulation_policy_version": policy_version, "package_version": package_version(), "executed_at": as_of},
        }
    except SimulationError as error:
        # Empty inputs keep the same unavailable state as the real calculation path.
        if error.code != "INPUT_NOT_READY":
            raise
        simulation = error.response_body()
    return {
        "id": scenario["id"], "title": scenario["title"], "description": scenario["description"],
        "input": snapshot,
        "reports": [{
            "id": report.id, "report_type": report.report_type, "version": report.version,
            "status": report.status, "content": report.content, "generated_at": as_of,
        } for report in reports],
        "simulation": simulation,
    }


def export_bundle():
    from devtools.presentation_inputs import AS_OF, scenarios

    configure_database()
    # Freeze subscription years and report timestamps for reproducible previews.
    with patch("django.utils.timezone.now", return_value=datetime.fromisoformat(AS_OF)):
        return {"schema_version": "presentation-lab-v1", "as_of": AS_OF,
                "scenarios": [export_scenario(scenario, AS_OF) for scenario in scenarios()]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    from django.core.serializers.json import DjangoJSONEncoder

    payload = json.dumps(export_bundle(), cls=DjangoJSONEncoder, ensure_ascii=False, allow_nan=False, indent=2)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(".tmp")
    temporary.write_text(payload + "\n", encoding="utf-8")
    temporary.replace(args.output)
    print(f"Presentation Lab: input scenarios → reports + simulation outcomes: {args.output}")


if __name__ == "__main__":
    main()
