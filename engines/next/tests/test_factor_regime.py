import unittest

from build_factor_regime import (
    align_factors,
    build_payload,
    droughts,
    parse_monthly_rows,
    rolling_annualized_premium,
)


class FactorRegimeTests(unittest.TestCase):
    def test_parse_monthly_rows_ignores_headers_and_annual_rows(self):
        text = """
        This file was created by TEST
        ,Mkt-RF,SMB,HML,RMW,CMA,RF
        196307, 1.00, 2.00, 3.00, 4.00, 5.00, 0.20
        196308, -1.50, 1.00, 0.00, 2.50, -0.50, 0.20

        Annual Factors: January-December
        1963, 10, 10, 10, 10, 10, 2
        """
        rows = parse_monthly_rows(text, ("Mkt-RF", "SMB", "HML", "RMW", "CMA", "RF"))
        self.assertEqual(sorted(rows), ["1963-07", "1963-08"])
        self.assertEqual(rows["1963-07"]["HML"], 3.0)
        self.assertEqual(rows["1963-08"]["Mkt-RF"], -1.5)

    def test_align_factors_uses_common_months_only(self):
        ff5 = {
            "2020-01": {"Mkt-RF": 1, "SMB": 2, "HML": 3, "RMW": 4, "CMA": 5, "RF": 0},
            "2020-02": {"Mkt-RF": 2, "SMB": 3, "HML": 4, "RMW": 5, "CMA": 6, "RF": 0},
        }
        mom = {"2020-02": {"Mom": 7}}
        # align_factors enforces the production 120-month minimum, so pad both
        # sources with a deterministic monthly history first.
        for index in range(119):
            year = 2010 + index // 12
            month = index % 12 + 1
            key = f"{year:04d}-{month:02d}"
            ff5[key] = {"Mkt-RF": 1, "SMB": 1, "HML": 1, "RMW": 1, "CMA": 1, "RF": 0}
            mom[key] = {"Mom": 1}
        aligned = align_factors(ff5, mom)
        self.assertEqual(aligned[-1]["month"], "2020-02")
        self.assertEqual(aligned[-1]["MOM"], 7.0)
        self.assertNotIn("2020-01", [row["month"] for row in aligned])

    def test_rolling_annualized_premium_compounds_then_annualizes(self):
        values = [1.0] * 120 + [2.0]
        series = rolling_annualized_premium(values)
        self.assertAlmostEqual(series[0], ((1.01**120) ** (12 / 120) - 1) * 100)
        self.assertAlmostEqual(series[1], (((1.01**119) * 1.02) ** (12 / 120) - 1) * 100)

    def test_droughts_detect_completed_and_ongoing_runs(self):
        runs = droughts([1.0, -1.0, -2.0, 0.1, -0.2, -0.3, -0.4])
        self.assertEqual(len(runs), 2)
        self.assertEqual((runs[0].start_index, runs[0].end_index, runs[0].months, runs[0].ongoing), (1, 2, 2, False))
        self.assertEqual((runs[1].start_index, runs[1].end_index, runs[1].months, runs[1].ongoing), (4, 6, 3, True))

    def test_payload_exposes_change_drought_and_regime_without_stockscout_impact(self):
        rows = []
        year, month = 2000, 1
        for index in range(132):
            month_key = f"{year:04d}-{month:02d}"
            rows.append(
                {
                    "month": month_key,
                    "MKT_RF": 1.0,
                    "SMB": -1.0,
                    "HML": -1.0 if index < 120 else 5.0,
                    "RMW": 0.5,
                    "CMA": -0.5,
                    "MOM": 1.5,
                }
            )
            month += 1
            if month == 13:
                month = 1
                year += 1

        payload = build_payload(rows, generated_at="2026-08-22T00:00:00Z")
        self.assertEqual(payload["method"]["stockScoutImpact"], "none; read-only independent macro/factor module")
        self.assertEqual(payload["range"]["rollingFirstMonth"], "2009-12")
        self.assertEqual(len(payload["factors"]), 6)

        factors = {factor["id"]: factor for factor in payload["factors"]}
        self.assertEqual(factors["MKT_RF"]["latest"]["regime"], "STRONG")
        self.assertEqual(factors["SMB"]["latest"]["regime"], "RECOVERY")
        self.assertTrue(factors["SMB"]["currentDrought"]["active"])
        self.assertEqual(factors["SMB"]["currentDrought"]["months"], 13)
        self.assertGreater(factors["HML"]["latest"]["delta12mPp"], 0)


if __name__ == "__main__":
    unittest.main()
