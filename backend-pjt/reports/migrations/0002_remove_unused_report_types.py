from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('reports', '0001_initial'),
    ]

    operations = [
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
