import unittest

from models.betting import DatosPartido, aplicar_reglas_duras
from models.parlay import calcular_parlay, compatible, validar_boleto


class ParlayTests(unittest.TestCase):
    def test_same_match_markets_are_not_combined(self):
        self.assertFalse(compatible(
            {"partido": "A-B", "mercado": "Over 2.5"},
            {"partido": "A-B", "mercado": "Under 3.5"},
        ))

    def test_different_matches_are_compatible(self):
        self.assertTrue(compatible(
            {"partido": "A-B", "mercado": "Over 1.5"},
            {"partido": "C-D", "mercado": "Local gana"},
        ))

    def test_edge_requires_real_odds_and_calibration(self):
        metrics = calcular_parlay([
            {"momio": 1.8, "probEstimada": 0.65, "momioReal": False, "probCalibrada": False},
            {"momio": 1.7, "probEstimada": 0.62, "momioReal": False, "probCalibrada": False},
        ])
        self.assertIsNone(metrics["edge"])
        self.assertFalse(metrics["edgeValido"])

    def test_validation_explains_missing_edge(self):
        result = validar_boleto([{"mercado": "Over 1.5", "momio": 1.4, "probEstimada": 0.75}])
        self.assertTrue(any("edge" in warning.lower() for warning in result["avisos"]))

    def test_hard_rule_blocks_low_goal_under(self):
        allowed, _ = aplicar_reglas_duras("Under 2.5 goles", 2.5)
        self.assertFalse(allowed)

    def test_input_is_cleaned(self):
        match = DatosPartido(local="  América ", visitante=" Chivas ")
        self.assertEqual(match.local, "América")
        self.assertEqual(match.visitante, "Chivas")


if __name__ == "__main__":
    unittest.main()
