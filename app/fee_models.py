from django.db import models


class TuitionSource(models.Model):
    tuition = models.OneToOneField('app.Tuition', on_delete=models.CASCADE, related_name='source_metadata')
    academic_year = models.CharField(max_length=7)
    source_url = models.URLField(max_length=500)
    source_course_code = models.CharField(max_length=100)
    source_course_name = models.CharField(max_length=255)
    fee_basis = models.CharField(max_length=30)
    student_contribution = models.DecimalField(max_digits=10, decimal_places=2, null=True)
    published_total = models.DecimalField(max_digits=10, decimal_places=2, null=True)
    raw_fees = models.JSONField(default=dict)
    checked_at = models.DateTimeField(auto_now=True)


class TuitionUpdateAudit(models.Model):
    tuition = models.ForeignKey('app.Tuition', on_delete=models.SET_NULL, null=True)
    course_identifier = models.CharField(max_length=30)
    residency = models.CharField(max_length=5)
    before = models.JSONField(default=dict)
    after = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)


def fee_metadata(tuition):
    try:
        source = tuition.source_metadata
    except TuitionSource.DoesNotExist:
        return {
            "fee_label": "Tuition Fee",
            "academic_year": "",
            "fee_note": "",
        }

    labels = {
        "annual_tuition": "Annual tuition fee",
        "tuition_year_1": "First-year tuition",
        "published_total_year_1": "First-year published total fee",
    }

    note = ""

    if source.fee_basis == "published_total_year_1":
        note = (
            "Published first-year total; this may include charges "
            "in addition to tuition."
        )

    if source.student_contribution is not None:
        contribution_note = (
            "Student contribution is recorded separately. "
            "Check the university's eligibility and contribution rules."
        )
        note = f"{note} {contribution_note}".strip()

    return {
        "fee_label": labels.get(source.fee_basis, "Tuition Fee"),
        "academic_year": source.academic_year,
        "fee_note": note,
    }