from django.db import migrations, models


def remove_news_reports(apps, schema_editor):
    report_model = apps.get_model('reports', 'Report')
    report_model.objects.filter(report_type='news_report').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('reports', '0003_add_news_report_type'),
    ]

    operations = [
        migrations.RunPython(remove_news_reports, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='report',
            name='report_type',
            field=models.CharField(
                choices=[
                    ('asset_statement', '자산현황표'),
                    ('cash_flow', '현금흐름표'),
                    ('financial-indicators', '가계재무지표'),
                    ('financial-goal-result', '재무목표'),
                    ('retirement-fund', '은퇴 필요자금'),
                    ('retirement-readiness', '은퇴 준비자금'),
                    ('pension-schedule', '연금 스케줄'),
                    ('lifetime-cashflow', '생애현금흐름 및 생애순자산'),
                    ('required-savings', '필요저축액'),
                ],
                max_length=30,
            ),
        ),
    ]
