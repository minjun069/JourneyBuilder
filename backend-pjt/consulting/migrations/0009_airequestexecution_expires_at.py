from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('consulting', '0008_ai_request_execution'),
    ]

    operations = [
        migrations.AddField(
            model_name='airequestexecution',
            name='expires_at',
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
    ]
