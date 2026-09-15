from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
PARQUET_DIR = DATA_DIR / "parquet"
DATABASE_PATH = DATA_DIR / "logistics_demo.duckdb"

DEFAULT_SEED = 20260915
DEFAULT_DAYS = 60
DEFAULT_SHIPMENTS = 100_000
DEFAULT_END_DATE = "2026-08-31"

