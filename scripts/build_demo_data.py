from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from logistics_system.config import (  # noqa: E402
    DATABASE_PATH,
    DEFAULT_DAYS,
    DEFAULT_END_DATE,
    DEFAULT_SEED,
    DEFAULT_SHIPMENTS,
)
from logistics_system.generator import build_demo_database  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成物流营运 Demo 数据库")
    parser.add_argument("--shipments", type=int, default=DEFAULT_SHIPMENTS)
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--end-date", default=DEFAULT_END_DATE)
    parser.add_argument("--database", type=Path, default=DATABASE_PATH)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = build_demo_database(
        database_path=args.database,
        shipment_count=args.shipments,
        days=args.days,
        seed=args.seed,
        end_date=args.end_date,
    )
    print(
        "生成完成："
        f"{result.database_path}，{result.shipment_count:,} 票运单，"
        f"{result.event_count:,} 条扫描事件，{result.exception_count:,} 条异常记录，"
        f"日期 {result.date_start} 至 {result.date_end}。"
    )


if __name__ == "__main__":
    main()
