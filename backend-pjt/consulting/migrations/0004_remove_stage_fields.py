from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('consulting', '0003_remove_plan_generation_stage'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='consultingsession',
            name='current_stage',
        ),
        migrations.RemoveField(
            model_name='message',
            name='stage',
        ),
    ]
