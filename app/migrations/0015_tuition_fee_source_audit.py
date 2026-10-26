from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('app', '0014_exchangerate')]
    operations = [
        migrations.CreateModel(name='TuitionSource', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ('academic_year', models.CharField(max_length=7)),
            ('source_url', models.URLField(max_length=500)),
            ('source_course_code', models.CharField(max_length=100)),
            ('source_course_name', models.CharField(max_length=255)),
            ('fee_basis', models.CharField(max_length=30)),
            ('student_contribution', models.DecimalField(max_digits=10, decimal_places=2, null=True)),
            ('published_total', models.DecimalField(max_digits=10, decimal_places=2, null=True)),
            ('raw_fees', models.JSONField(default=dict)),
            ('checked_at', models.DateTimeField(auto_now=True)),
            ('tuition', models.OneToOneField(to='app.tuition', related_name='source_metadata', on_delete=django.db.models.deletion.CASCADE)),
        ]),
        migrations.CreateModel(name='TuitionUpdateAudit', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ('course_identifier', models.CharField(max_length=30)),
            ('residency', models.CharField(max_length=5)),
            ('before', models.JSONField(default=dict)),
            ('after', models.JSONField(default=dict)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('tuition', models.ForeignKey(to='app.tuition', null=True, on_delete=django.db.models.deletion.SET_NULL)),
        ]),
    ]
