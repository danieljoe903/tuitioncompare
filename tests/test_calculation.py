import unittest
from decimal import Decimal
from types import SimpleNamespace

from app.scholarship_calculation import (
    calculate_scholarships,
    evaluate_award,
    select_awards,
)


class RulesTest(unittest.TestCase):
    def setUp(self):
        self.rule = {
            "verified": True,
            "source_url": "https://example.edu/award",
            "rules": {
                "fee_basis_confirmed": True,
                "benefit": "tuition",
                "kind": "percentage",
                "value": "20",
                "currency": "EUR",
                "academic_year": "2026/27",
                "study_years": [1],
                "conditions": {
                    "nationality": ["NG"],
                },
                "award_status": "competitive",
                "stacking": "exclusive",
                "compatible_ids": [],
                "percentage_basis": "original_tuition",
            },
        }

    def evaluate(self, context=None, study_year=1):
        return evaluate_award(
            {
                "id": 1,
                "name": "Award",
                "deadline": None,
            },
            self.rule,
            context or {},
            Decimal("20000"),
            "EUR",
            "2026/27",
            study_year,
        )

    def test_percentage(self):
        candidate, error = self.evaluate({
            "nationality": "NG",
        })

        self.assertIsNone(error)
        self.assertEqual(
            candidate["amount"],
            Decimal("4000"),
        )

    def test_residence_is_not_nationality(self):
        candidate, error = self.evaluate({
            "residence": "NG",
        })

        self.assertIsNone(candidate)
        self.assertIn("nationality", error)

    def test_wrong_nationality(self):
        candidate, _ = self.evaluate({
            "nationality": "GH",
        })

        self.assertIsNone(candidate)

    def test_first_year_only(self):
        candidate, _ = self.evaluate(
            {"nationality": "NG"},
            study_year=2,
        )

        self.assertIsNone(candidate)

    def test_unverified(self):
        self.rule["verified"] = False

        candidate, _ = self.evaluate({
            "nationality": "NG",
        })

        self.assertIsNone(candidate)

    def test_fixed_currency(self):
        self.rule["rules"].update({
            "kind": "fixed",
            "currency": "GBP",
        })

        candidate, _ = self.evaluate({
            "nationality": "NG",
        })

        self.assertIsNone(candidate)

    def test_non_tuition(self):
        self.rule["rules"]["benefit"] = "living_cost"

        candidate, _ = self.evaluate({
            "nationality": "NG",
        })

        self.assertIsNone(candidate)

    def test_combination(self):
        first = {
            "id": "1",
            "amount": Decimal("3000"),
            "stacking": "explicit",
            "compatible_ids": ["2"],
        }

        second = {
            "id": "2",
            "amount": Decimal("2000"),
            "stacking": "explicit",
            "compatible_ids": ["1"],
        }

        selected, _ = select_awards(
            [first, second],
            Decimal("20000"),
        )

        self.assertEqual(len(selected), 2)

        second["compatible_ids"] = []

        selected, _ = select_awards(
            [first, second],
            Decimal("20000"),
        )

        self.assertEqual(len(selected), 1)

    def test_cap(self):
        self.rule["rules"].update({
            "kind": "fixed",
            "value": "50000",
        })

        candidate, _ = self.evaluate({
            "nationality": "NG",
        })

        self.assertEqual(
            candidate["amount"],
            Decimal("20000"),
        )

    def test_maximum_type(self):
        self.rule["rules"].update({
            "kind": "fixed_maximum",
            "value": "3000",
        })

        candidate, error = self.evaluate({
            "nationality": "NG",
        })

        self.assertIsNone(error)
        self.assertTrue(candidate["is_maximum"])
        self.assertEqual(
            candidate["amount"],
            Decimal("3000"),
        )

    def test_nan_amount_rejected(self):
        self.rule["rules"]["value"] = "NaN"

        candidate, _ = self.evaluate({
            "nationality": "NG",
        })

        self.assertIsNone(candidate)

    def test_maximum_wrong_currency(self):
        self.rule["rules"].update({
            "kind": "fixed_maximum",
            "value": "3000",
            "currency": "GBP",
        })

        candidate, _ = self.evaluate({
            "nationality": "NG",
        })

        self.assertIsNone(candidate)


class IntegrationTest(unittest.TestCase):
    def fixture(self, linked):
        course = SimpleNamespace(
            course_id="C",
            level="UG",
        )

        tuition = SimpleNamespace(
            tuition_fee=Decimal("16950"),
            scholarship=Decimal("3000"),
            currency="GBP",
            residency="INTL",
            course=course,
            scholarship_source_id=85 if linked else None,
        )

        rules = {
            "fee_basis_confirmed": True,
            "benefit": "tuition",
            "kind": "fixed",
            "value": "3000",
            "currency": "GBP",
            "academic_year": "2026/27",
            "study_years": [1],
            "conditions": {
                "academic_conditions_met": [True],
            },
            "award_status": "conditional",
            "stacking": "exclusive",
            "compatible_ids": [],
            "percentage_basis": "original_tuition",
        }

        award = SimpleNamespace(
            pk=85,
            name="Award",
            deadline=None,
            calculation_rule=SimpleNamespace(
                verified=True,
                source_url="https://example.edu/award",
                rules=rules,
            ),
        )

        return tuition, award

    def test_linked_not_double_counted(self):
        tuition, award = self.fixture(True)

        result = calculate_scholarships(
            tuition,
            [award],
            {"academic_conditions_met": True},
            "2026/27",
            1,
        )

        self.assertEqual(result["net"], "13950.00")
        self.assertEqual(
            result["label"],
            "Estimated net tuition if awarded",
        )

    def test_unknown_condition_blocks_linked_discount(self):
        tuition, award = self.fixture(True)

        result = calculate_scholarships(
            tuition,
            [award],
            {},
            "2026/27",
            1,
        )

        self.assertEqual(result["net"], "16950.00")

    def test_unlinked_legacy_preserved(self):
        tuition, award = self.fixture(False)

        result = calculate_scholarships(
            tuition,
            [award],
            {},
            "2026/27",
            1,
        )

        self.assertEqual(result["net"], "13950")

    def test_linked_award_does_not_exclude_other_award(self):
        tuition, original = self.fixture(True)

        other_rules = dict(
            original.calculation_rule.rules
        )
        other_rules["value"] = "5000"

        other = SimpleNamespace(
            pk=86,
            name="Other eligible award",
            deadline=None,
            calculation_rule=SimpleNamespace(
                verified=True,
                source_url="https://example.edu/other-award",
                rules=other_rules,
            ),
        )

        self.assertEqual(
            original.calculation_rule.rules["value"],
            "3000",
        )

        result = calculate_scholarships(
            tuition,
            [original, other],
            {"academic_conditions_met": True},
            "2026/27",
            1,
        )

        self.assertEqual(
            Decimal(result["discount"]),
            Decimal("5000"),
        )
        self.assertEqual(
            Decimal(result["net"]),
            Decimal("11950"),
        )
        self.assertEqual(
            result["applied"][0]["id"],
            "86",
        )

    def test_legacy_discount_is_not_reported_as_unapplied(self):
        tuition, award = self.fixture(False)

        result = calculate_scholarships(
            tuition,
            [award],
            {},
            "2026/27",
            1,
        )

        self.assertEqual(result["review"], [])
        self.assertTrue(result["notes"])

    def test_maximum_label_and_no_double_count(self):
        tuition, award = self.fixture(True)
        award.calculation_rule.rules["kind"] = "fixed_maximum"

        result = calculate_scholarships(
            tuition,
            [award],
            {"academic_conditions_met": True},
            "2026/27",
            1,
        )

        self.assertEqual(result["net"], "13950.00")
        self.assertEqual(result["discount"], "3000.00")
        self.assertTrue(result["maximum_award_estimate"])
        self.assertIn(
            "maximum scholarship",
            result["label"],
        )
        self.assertTrue(
            result["applied"][0]["is_maximum"]
        )

    def test_maximum_never_labelled_automatic(self):
        tuition, award = self.fixture(True)

        award.calculation_rule.rules.update({
            "kind": "fixed_maximum",
            "award_status": "automatic",
        })

        result = calculate_scholarships(
            tuition,
            [award],
            {"academic_conditions_met": True},
            "2026/27",
            1,
        )

        self.assertIn(
            "maximum scholarship",
            result["label"],
        )

    def test_maximum_unknown_conditions_no_deduction(self):
        tuition, award = self.fixture(True)
        award.calculation_rule.rules["kind"] = "fixed_maximum"

        result = calculate_scholarships(
            tuition,
            [award],
            {},
            "2026/27",
            1,
        )

        self.assertEqual(result["discount"], "0.00")
        self.assertEqual(result["net"], "16950.00")

    def test_maximum_not_added_to_legacy(self):
        tuition, award = self.fixture(False)
        award.calculation_rule.rules["kind"] = "fixed_maximum"

        result = calculate_scholarships(
            tuition,
            [award],
            {"academic_conditions_met": True},
            "2026/27",
            1,
        )

        self.assertEqual(
            Decimal(result["discount"]),
            Decimal("3000"),
        )
        self.assertFalse(
            result["maximum_award_estimate"]
        )
        self.assertEqual(result["applied"], [])

    def test_pg_unverified_fee_base_blocks_award(self):
        tuition, award = self.fixture(True)

        award.calculation_rule.rules.update({
            "kind": "fixed_maximum",
            "fee_basis_confirmed": False,
        })

        result = calculate_scholarships(
            tuition,
            [award],
            {"academic_conditions_met": True},
            "2026/27",
            1,
        )

        self.assertEqual(result["discount"], "0.00")
        self.assertEqual(result["net"], "16950.00")


if __name__ == "__main__":
    unittest.main()