from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import duckdb  # noqa: E402

from logistics_system.analytics import (  # noqa: E402
    get_anomalies,
    get_date_bounds,
    get_diagnostic_evidence,
    get_overview,
)
from logistics_system.generator import build_demo_database, generate_demo_frames  # noqa: E402
from logistics_system.importer import import_logistics_excel, parse_logistics_excel  # noqa: E402


class GeneratorTests(unittest.TestCase):
    def test_frames_have_expected_grains(self) -> None:
        frames = generate_demo_frames(shipment_count=1_000, days=30, seed=7)
        self.assertEqual(len(frames["dim_site"]), 24)
        self.assertEqual(len(frames["dim_route"]), 48)
        self.assertEqual(len(frames["fact_waybill"]), 1_000)
        self.assertEqual(len(frames["fact_scan_event"]), 4_000)
        self.assertEqual(frames["fact_waybill"]["waybill_id"].nunique(), 1_000)
        self.assertEqual(len(frames["scenario_truth"]), 5)

    def test_fixed_seed_is_reproducible(self) -> None:
        first = generate_demo_frames(shipment_count=500, days=20, seed=11)["fact_waybill"]
        second = generate_demo_frames(shipment_count=500, days=20, seed=11)["fact_waybill"]
        self.assertTrue(first.equals(second))


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.database_path = Path(cls.temp_dir.name) / "test.duckdb"
        cls.result = build_demo_database(
            cls.database_path,
            shipment_count=8_000,
            days=60,
            seed=2026,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def test_database_and_marts_are_created(self) -> None:
        self.assertTrue(self.database_path.exists())
        self.assertEqual(self.result.shipment_count, 8_000)
        with duckdb.connect(str(self.database_path), read_only=True) as connection:
            table_count = connection.execute(
                "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'main'"
            ).fetchone()[0]
            mart_count = connection.execute("SELECT COUNT(*) FROM mart_site_daily").fetchone()[0]
        self.assertGreaterEqual(table_count, 9)
        self.assertGreater(mart_count, 0)

    def test_overview_uses_consistent_denominators(self) -> None:
        start_date, end_date = get_date_bounds(self.database_path)
        overview = get_overview(start_date, end_date, database_path=self.database_path)
        self.assertEqual(int(overview["shipment_count"]), 8_000)
        self.assertGreaterEqual(overview["on_time_rate"], 0)
        self.assertLessEqual(overview["on_time_rate"], 100)
        self.assertGreaterEqual(overview["damage_rate"], 0)

    def test_anomalies_produce_evidence(self) -> None:
        start_date, end_date = get_date_bounds(self.database_path)
        anomalies = get_anomalies(start_date, end_date, database_path=self.database_path)
        self.assertFalse(anomalies.empty)
        selected = anomalies.iloc[0]
        evidence = get_diagnostic_evidence(
            selected["site_id"], selected["date"], database_path=self.database_path
        )
        self.assertIn("recommendation", evidence)
        self.assertIn(evidence["confidence"], {"中", "高"})


class ExcelImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workbook_path = PROJECT_ROOT / "data" / "templates" / "物流营运演示数据.xlsx"

    def test_demo_workbook_is_valid_and_analyzable(self) -> None:
        if not self.workbook_path.exists():
            self.skipTest("演示 Excel 尚未生成")
        parsed = parse_logistics_excel(self.workbook_path)
        self.assertTrue(parsed.is_valid, parsed.errors)
        self.assertEqual(parsed.row_counts["运单表"], 8_000)
        self.assertEqual(parsed.row_counts["扫描事件表"], 32_000)

        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "uploaded.duckdb"
            imported = import_logistics_excel(self.workbook_path, database_path)
            self.assertTrue(imported.is_valid, imported.errors)
            start_date, end_date = get_date_bounds(database_path)
            overview = get_overview(start_date, end_date, database_path=database_path)
            self.assertEqual(int(overview["shipment_count"]), 8_000)
            self.assertFalse(get_anomalies(start_date, end_date, database_path=database_path).empty)


if __name__ == "__main__":
    unittest.main()
