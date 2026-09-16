from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('consulting', '0006_ai_terminal_receipt'),
    ]

    operations = [
        migrations.CreateModel(
            name='AIRequestExecution',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('request_id', models.CharField(max_length=128, unique=True)),
                ('thread_id', models.CharField(max_length=255)),
                ('step_id', models.CharField(max_length=100)),
                ('payload_digest', models.CharField(max_length=64)),
                ('request_payload', models.JSONField()),
                ('status', models.CharField(choices=[('processing', '처리 중'), ('completed', '완료'), ('failed', '실패'), ('expired', '만료')], default='processing', max_length=10)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('session', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='ai_request_executions', to='consulting.consultingsession')),
                ('user_message', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='consulting.message')),
            ],
            options={
                'db_table': 'consulting_ai_request_executions',
            },
        ),
        migrations.AddConstraint(
            model_name='airequestexecution',
            constraint=models.UniqueConstraint(condition=models.Q(('status', 'processing')), fields=('session',), name='one_active_ai_request_per_session'),
        ),
    ]
