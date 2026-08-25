from django.db import migrations, models
from django.utils.timezone import now


def complete_plan_generation_sessions(apps, schema_editor):
    ConsultingSession = apps.get_model('consulting', 'ConsultingSession')
    ConsultingSession.objects.filter(
        current_stage='plan_generation',
        status='abandoned',
    ).update(current_stage='cashflow_analysis')
    ConsultingSession.objects.filter(
        current_stage='plan_generation',
    ).exclude(status='abandoned').update(
        status='completed',
        current_stage='cashflow_analysis',
        completed_at=now(),
    )


class Migration(migrations.Migration):

    dependencies = [
        ('consulting', '0002_basicinfo_expense_financialasset_and_more'),
    ]

    operations = [
        migrations.RunPython(
            complete_plan_generation_sessions,
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name='consultingsession',
            name='current_stage',
            field=models.CharField(
                blank=True,
                choices=[
                    ('goal_setting', '재무 목표 설정'),
                    ('asset_analysis', '자산현황 파악'),
                    ('cashflow_analysis', '현금흐름 파악'),
                ],
                max_length=30,
                null=True,
            ),
        ),
        migrations.AlterField(
            model_name='message',
            name='stage',
            field=models.CharField(
                blank=True,
                choices=[
                    ('goal_setting', '재무 목표 설정'),
                    ('asset_analysis', '자산현황 파악'),
                    ('cashflow_analysis', '현금흐름 파악'),
                ],
                max_length=30,
                null=True,
            ),
        ),
    ]
