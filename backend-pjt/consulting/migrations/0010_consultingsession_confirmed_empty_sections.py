from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('consulting', '0009_airequestexecution_expires_at'),
    ]

    operations = [
        migrations.AddField(
            model_name='consultingsession',
            name='confirmed_empty_sections',
            field=models.JSONField(blank=True, default=list),
        ),
    ]
