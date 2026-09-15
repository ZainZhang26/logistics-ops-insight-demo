from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from .config import (
    DATABASE_PATH,
    DEFAULT_DAYS,
    DEFAULT_END_DATE,
    DEFAULT_SEED,
    DEFAULT_SHIPMENTS,
    PARQUET_DIR,
)


@dataclass(frozen=True)
class DemoBuildResult:
    database_path: Path
    shipment_count: int
    event_count: int
    exception_count: int
    site_count: int
    route_count: int
    date_start: str
    date_end: str


def _build_sites() -> pd.DataFrame:
    regions = {
        "华东": ["上海浦东", "上海虹桥", "杭州滨江", "宁波鄞州", "南京江宁", "苏州工业园"],
        "华南": ["深圳南山", "深圳龙岗", "广州白云", "佛山顺德", "东莞松山湖", "厦门湖里"],
        "华北": ["北京顺义", "北京通州", "天津滨海", "石家庄裕华", "济南历城", "青岛城阳"],
        "西南": ["成都双流", "成都龙泉", "重庆渝北", "昆明官渡", "贵阳南明", "南宁江南"],
    }
    rows: list[dict[str, object]] = []
    site_no = 1
    for region_no, (region, names) in enumerate(regions.items(), start=1):
        for local_no, name in enumerate(names, start=1):
            rows.append(
                {
                    "site_id": f"S{site_no:03d}",
                    "site_name": f"{name}网点",
                    "region": region,
                    "city": name[:2],
                    "site_type": "枢纽" if local_no == 1 else "营业网点",
                    "base_daily_capacity": 72 + region_no * 5 + local_no * 4,
                    "opened_date": pd.Timestamp("2021-01-01")
                    + pd.Timedelta(site_no * 17, unit="D"),
                }
            )
            site_no += 1
    return pd.DataFrame(rows)


def _build_routes(sites: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    site_ids = sites["site_id"].tolist()
    pairs: list[tuple[str, str]] = []
    for index, origin in enumerate(site_ids):
        pairs.append((origin, site_ids[(index + 1) % len(site_ids)]))
        pairs.append((origin, site_ids[(index + 6) % len(site_ids)]))
    pairs = list(dict.fromkeys(pairs))
    rows = []
    for index, (origin, destination) in enumerate(pairs, start=1):
        distance = int(rng.integers(80, 2_200))
        rows.append(
            {
                "route_id": f"R{index:03d}",
                "origin_site_id": origin,
                "destination_site_id": destination,
                "distance_km": distance,
                "route_type": "跨区干线" if distance >= 800 else "区域线路",
                "standard_linehaul_hours": round(3.0 + distance / 150 + rng.uniform(0, 1.8), 2),
            }
        )
    return pd.DataFrame(rows)


def _scenario_windows(dates: pd.DatetimeIndex) -> dict[str, tuple[pd.Timestamp, pd.Timestamp]]:
    def window(start_fraction: float, length: int) -> tuple[pd.Timestamp, pd.Timestamp]:
        start_index = min(len(dates) - 1, max(0, int((len(dates) - 1) * start_fraction)))
        end_index = min(len(dates) - 1, start_index + length - 1)
        return dates[start_index], dates[end_index]

    return {
        "sorting_backlog": window(0.25, 7),
        "capacity_overload": window(0.40, 5),
        "linehaul_delay": window(0.55, 4),
        "staff_shortage": window(0.70, 7),
        "damage_spike": window(0.85, 6),
    }


def generate_demo_frames(
    shipment_count: int = DEFAULT_SHIPMENTS,
    days: int = DEFAULT_DAYS,
    seed: int = DEFAULT_SEED,
    end_date: str = DEFAULT_END_DATE,
) -> dict[str, pd.DataFrame]:
    if shipment_count < 100:
        raise ValueError("shipment_count 至少为 100")
    if days < 14:
        raise ValueError("days 至少为 14")

    rng = np.random.default_rng(seed)
    dates = pd.date_range(end=pd.Timestamp(end_date), periods=days, freq="D")
    sites = _build_sites()
    routes = _build_routes(sites, rng)
    windows = _scenario_windows(dates)

    service_sla = pd.DataFrame(
        [
            {"service_type": "特快", "sla_hours": 24, "effective_from": dates.min()},
            {"service_type": "标快", "sla_hours": 48, "effective_from": dates.min()},
            {"service_type": "经济件", "sla_hours": 72, "effective_from": dates.min()},
        ]
    )
    date_dim = pd.DataFrame({"date": dates})
    date_dim["year"] = date_dim["date"].dt.year
    date_dim["month"] = date_dim["date"].dt.month
    date_dim["week"] = date_dim["date"].dt.isocalendar().week.astype(int)
    date_dim["weekday"] = date_dim["date"].dt.weekday + 1
    date_dim["is_weekend"] = date_dim["weekday"] >= 6

    day_weights = np.where(date_dim["is_weekend"].to_numpy(), 0.82, 1.0)
    day_weights *= np.linspace(0.92, 1.08, len(dates))
    day_weights = day_weights / day_weights.sum()
    ship_day_index = rng.choice(len(dates), size=shipment_count, p=day_weights)
    shipped_at = dates.to_numpy()[ship_day_index] + pd.to_timedelta(
        rng.integers(6 * 3600, 22 * 3600, size=shipment_count), unit="s"
    )

    route_index = rng.integers(0, len(routes), size=shipment_count)
    route_values = routes.iloc[route_index].reset_index(drop=True)
    service_type = rng.choice(["特快", "标快", "经济件"], size=shipment_count, p=[0.30, 0.55, 0.15])
    sla_lookup = {"特快": 24, "标快": 48, "经济件": 72}
    sla_hours = np.array([sla_lookup[value] for value in service_type], dtype=float)
    speed_factor = np.select(
        [service_type == "特快", service_type == "经济件"],
        [0.78, 1.12],
        default=1.0,
    )

    origin_process = np.clip(rng.normal(2.8, 0.9, shipment_count), 0.5, None)
    linehaul = route_values["standard_linehaul_hours"].to_numpy() * speed_factor
    linehaul += np.clip(rng.normal(0.8, 1.4, shipment_count), -1.5, None)
    destination_process = np.clip(rng.normal(3.8, 1.2, shipment_count), 0.7, None)
    last_mile = np.clip(rng.normal(5.5, 1.8, shipment_count), 1.0, None)

    ship_dates = pd.DatetimeIndex(shipped_at).normalize()
    destination_ids = route_values["destination_site_id"].to_numpy()
    route_ids = route_values["route_id"].to_numpy()

    sorting_start, sorting_end = windows["sorting_backlog"]
    sorting_mask = (
        (destination_ids == "S006")
        & (ship_dates >= sorting_start)
        & (ship_dates <= sorting_end)
    )
    destination_process[sorting_mask] += rng.uniform(6.0, 9.0, sorting_mask.sum())

    linehaul_start, linehaul_end = windows["linehaul_delay"]
    linehaul_mask = (
        (route_ids == "R010")
        & (ship_dates >= linehaul_start)
        & (ship_dates <= linehaul_end)
    )
    linehaul[linehaul_mask] += rng.uniform(9.0, 13.0, linehaul_mask.sum())

    staff_start, staff_end = windows["staff_shortage"]
    staff_mask = (
        (destination_ids == "S014")
        & (ship_dates >= staff_start)
        & (ship_dates <= staff_end)
    )
    destination_process[staff_mask] += rng.uniform(7.0, 10.0, staff_mask.sum())

    origin_out_at = pd.DatetimeIndex(shipped_at) + pd.to_timedelta(origin_process, unit="h")
    destination_in_at = origin_out_at + pd.to_timedelta(linehaul, unit="h")
    sort_complete_at = destination_in_at + pd.to_timedelta(destination_process, unit="h")
    delivered_at = sort_complete_at + pd.to_timedelta(last_mile, unit="h")
    promised_delivery_at = pd.DatetimeIndex(shipped_at) + pd.to_timedelta(sla_hours, unit="h")
    actual_duration = (delivered_at - pd.DatetimeIndex(shipped_at)).total_seconds() / 3600

    damage_start, damage_end = windows["damage_spike"]
    damage_mask = (
        (destination_ids == "S020")
        & (ship_dates >= damage_start)
        & (ship_dates <= damage_end)
    )
    damage_probability = np.full(shipment_count, 0.006)
    damage_probability[damage_mask] = 0.075
    damage_probability[sorting_mask | staff_mask] += 0.012
    damaged = rng.random(shipment_count) < damage_probability

    weight_kg = np.round(np.clip(rng.lognormal(1.0, 0.7, shipment_count), 0.2, 40), 2)
    distance_km = route_values["distance_km"].to_numpy()
    transport_cost = np.round(7.5 + distance_km * 0.025 + weight_kg * 0.85, 2)
    waybill_ids = np.array([f"WB{index:07d}" for index in range(1, shipment_count + 1)])

    waybills = pd.DataFrame(
        {
            "waybill_id": waybill_ids,
            "route_id": route_ids,
            "origin_site_id": route_values["origin_site_id"].to_numpy(),
            "destination_site_id": destination_ids,
            "service_type": service_type,
            "shipped_at": pd.DatetimeIndex(shipped_at),
            "promised_delivery_at": promised_delivery_at,
            "delivered_at": delivered_at,
            "actual_duration_hours": np.round(actual_duration, 2),
            "is_on_time": delivered_at <= promised_delivery_at,
            "is_damaged": damaged,
            "weight_kg": weight_kg,
            "transport_cost": transport_cost,
        }
    )

    event_frames = []
    event_specs = [
        ("始发出库", route_values["origin_site_id"].to_numpy(), origin_out_at),
        ("到达目的网点", destination_ids, destination_in_at),
        ("分拣完成", destination_ids, sort_complete_at),
        ("签收", destination_ids, delivered_at),
    ]
    for event_order, (event_type, site_values, timestamps) in enumerate(event_specs, start=1):
        event_frames.append(
            pd.DataFrame(
                {
                    "event_id": [f"EV{event_order}{index:07d}" for index in range(1, shipment_count + 1)],
                    "waybill_id": waybill_ids,
                    "event_order": event_order,
                    "event_type": event_type,
                    "site_id": site_values,
                    "event_at": timestamps,
                }
            )
        )
    scan_events = pd.concat(event_frames, ignore_index=True)

    late_mask = ~waybills["is_on_time"].to_numpy()
    delay_stage = np.where(
        linehaul_mask,
        "干线运输",
        np.where(sorting_mask | staff_mask, "目的网点分拣", "末端派送"),
    )
    late_exceptions = pd.DataFrame(
        {
            "waybill_id": waybill_ids[late_mask],
            "exception_type": "时效异常",
            "exception_stage": delay_stage[late_mask],
            "site_id": destination_ids[late_mask],
            "route_id": route_ids[late_mask],
            "occurred_at": delivered_at[late_mask],
            "severity": np.where(actual_duration[late_mask] > sla_hours[late_mask] + 8, "高", "中"),
        }
    )
    damage_exceptions = pd.DataFrame(
        {
            "waybill_id": waybill_ids[damaged],
            "exception_type": "破损异常",
            "exception_stage": "目的网点分拣",
            "site_id": destination_ids[damaged],
            "route_id": route_ids[damaged],
            "occurred_at": sort_complete_at[damaged],
            "severity": "高",
        }
    )
    exceptions = pd.concat([late_exceptions, damage_exceptions], ignore_index=True)
    exceptions.insert(0, "exception_id", [f"EX{index:07d}" for index in range(1, len(exceptions) + 1)])

    capacity_index = pd.MultiIndex.from_product(
        [dates, sites["site_id"]], names=["date", "site_id"]
    ).to_frame(index=False)
    daily_volume = (
        waybills.assign(date=waybills["shipped_at"].dt.normalize())
        .groupby(["date", "destination_site_id"])
        .size()
        .rename("actual_processed")
        .reset_index()
        .rename(columns={"destination_site_id": "site_id"})
    )
    capacity = capacity_index.merge(daily_volume, on=["date", "site_id"], how="left")
    capacity["actual_processed"] = capacity["actual_processed"].fillna(0).astype(int)
    capacity = capacity.merge(sites[["site_id", "base_daily_capacity"]], on="site_id", how="left")
    weekday_factor = np.where(capacity["date"].dt.weekday >= 5, 0.86, 1.0)
    volume_scale = (shipment_count / days) / (DEFAULT_SHIPMENTS / DEFAULT_DAYS)
    capacity["planned_capacity"] = np.maximum(
        1,
        np.round(capacity["base_daily_capacity"] * weekday_factor * volume_scale),
    ).astype(int)
    capacity["staff_count"] = np.maximum(2, np.round(capacity["planned_capacity"] / 30)).astype(int)
    capacity["backlog_count"] = np.maximum(
        capacity["actual_processed"] - capacity["planned_capacity"], 0
    ).astype(int)

    def apply_capacity_scenario(
        site_id: str,
        window_key: str,
        capacity_factor: float,
        backlog_add: int,
        staff_factor: float = 1.0,
    ) -> None:
        start, end = windows[window_key]
        mask = (
            (capacity["site_id"] == site_id)
            & (capacity["date"] >= start)
            & (capacity["date"] <= end)
        )
        capacity.loc[mask, "planned_capacity"] = np.maximum(
            1, np.round(capacity.loc[mask, "planned_capacity"] * capacity_factor)
        ).astype(int)
        capacity.loc[mask, "staff_count"] = np.maximum(
            1, np.round(capacity.loc[mask, "staff_count"] * staff_factor)
        ).astype(int)
        capacity.loc[mask, "backlog_count"] += backlog_add

    apply_capacity_scenario("S006", "sorting_backlog", 0.72, 120)
    apply_capacity_scenario("S003", "capacity_overload", 0.48, 150)
    apply_capacity_scenario("S014", "staff_shortage", 0.65, 135, staff_factor=0.60)
    capacity = capacity.drop(columns=["base_daily_capacity"])

    scenario_rows = [
        ("SC001", "分拣积压", "S006", None, *windows["sorting_backlog"], "分拣耗时增加 6–9 小时"),
        ("SC002", "产能过载", "S003", None, *windows["capacity_overload"], "计划产能下调且积压增加"),
        ("SC003", "干线延误", None, "R010", *windows["linehaul_delay"], "干线耗时增加 9–13 小时"),
        ("SC004", "人手不足", "S014", None, *windows["staff_shortage"], "分拣耗时增加且排班减少"),
        ("SC005", "破损升高", "S020", None, *windows["damage_spike"], "破损概率显著增加"),
    ]
    scenario_truth = pd.DataFrame(
        scenario_rows,
        columns=[
            "scenario_id",
            "scenario_type",
            "site_id",
            "route_id",
            "start_date",
            "end_date",
            "injected_effect",
        ],
    )

    return {
        "dim_site": sites,
        "dim_route": routes,
        "dim_date": date_dim,
        "dim_service_sla": service_sla,
        "fact_waybill": waybills,
        "fact_scan_event": scan_events,
        "fact_exception": exceptions,
        "fact_site_daily_capacity": capacity,
        "scenario_truth": scenario_truth,
    }


def build_demo_database(
    database_path: Path | str = DATABASE_PATH,
    shipment_count: int = DEFAULT_SHIPMENTS,
    days: int = DEFAULT_DAYS,
    seed: int = DEFAULT_SEED,
    end_date: str = DEFAULT_END_DATE,
) -> DemoBuildResult:
    database_path = Path(database_path)
    parquet_dir = database_path.parent / PARQUET_DIR.name
    frames = generate_demo_frames(
        shipment_count=shipment_count,
        days=days,
        seed=seed,
        end_date=end_date,
    )
    write_frames_to_database(frames, database_path, parquet_dir=parquet_dir)

    return DemoBuildResult(
        database_path=database_path,
        shipment_count=len(frames["fact_waybill"]),
        event_count=len(frames["fact_scan_event"]),
        exception_count=len(frames["fact_exception"]),
        site_count=len(frames["dim_site"]),
        route_count=len(frames["dim_route"]),
        date_start=str(frames["dim_date"]["date"].min().date()),
        date_end=str(frames["dim_date"]["date"].max().date()),
    )


def write_frames_to_database(
    frames: dict[str, pd.DataFrame],
    database_path: Path | str,
    parquet_dir: Path | str | None = None,
) -> Path:
    """Write validated logistics frames and recreate the analytical marts."""
    database_path = Path(database_path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    output_parquet_dir = Path(parquet_dir) if parquet_dir is not None else None
    if output_parquet_dir is not None:
        output_parquet_dir.mkdir(parents=True, exist_ok=True)
    if database_path.exists():
        database_path.unlink()

    connection = duckdb.connect(str(database_path))
    try:
        for table_name, frame in frames.items():
            registered_name = f"_{table_name}_frame"
            connection.register(registered_name, frame)
            connection.execute(
                f'CREATE OR REPLACE TABLE "{table_name}" AS SELECT * FROM "{registered_name}"'
            )
            if output_parquet_dir is not None:
                parquet_path = (
                    output_parquet_dir / f"{table_name}.parquet"
                ).as_posix().replace("'", "''")
                connection.execute(
                    f"COPY \"{table_name}\" TO '{parquet_path}' "
                    "(FORMAT PARQUET, COMPRESSION ZSTD)"
                )
            connection.unregister(registered_name)

        connection.execute(
            """
            CREATE OR REPLACE VIEW mart_site_daily AS
            WITH shipment_metrics AS (
                SELECT
                    CAST(shipped_at AS DATE) AS date,
                    destination_site_id AS site_id,
                    COUNT(*) AS shipment_count,
                    SUM(CASE WHEN is_on_time THEN 1 ELSE 0 END) AS on_time_count,
                    SUM(CASE WHEN is_damaged THEN 1 ELSE 0 END) AS damage_count,
                    AVG(actual_duration_hours) AS avg_duration_hours,
                    QUANTILE_CONT(actual_duration_hours, 0.9) AS p90_duration_hours,
                    SUM(transport_cost) AS transport_cost
                FROM fact_waybill
                GROUP BY 1, 2
            )
            SELECT
                metrics.date,
                metrics.site_id,
                site.site_name,
                site.region,
                metrics.shipment_count,
                metrics.on_time_count,
                metrics.damage_count,
                ROUND(100.0 * metrics.on_time_count / NULLIF(metrics.shipment_count, 0), 2) AS on_time_rate,
                ROUND(100.0 * metrics.damage_count / NULLIF(metrics.shipment_count, 0), 2) AS damage_rate,
                ROUND(metrics.avg_duration_hours, 2) AS avg_duration_hours,
                ROUND(metrics.p90_duration_hours, 2) AS p90_duration_hours,
                ROUND(metrics.transport_cost, 2) AS transport_cost,
                capacity.planned_capacity,
                capacity.actual_processed,
                capacity.staff_count,
                capacity.backlog_count,
                ROUND(100.0 * capacity.actual_processed / NULLIF(capacity.planned_capacity, 0), 2) AS capacity_utilization_rate
            FROM shipment_metrics AS metrics
            JOIN dim_site AS site ON metrics.site_id = site.site_id
            LEFT JOIN fact_site_daily_capacity AS capacity
                ON metrics.date = capacity.date AND metrics.site_id = capacity.site_id
            """
        )
        connection.execute(
            """
            CREATE OR REPLACE VIEW mart_route_daily AS
            SELECT
                CAST(waybill.shipped_at AS DATE) AS date,
                waybill.route_id,
                route.origin_site_id,
                route.destination_site_id,
                COUNT(*) AS shipment_count,
                ROUND(100.0 * AVG(CASE WHEN waybill.is_on_time THEN 1 ELSE 0 END), 2) AS on_time_rate,
                ROUND(AVG(waybill.actual_duration_hours), 2) AS avg_duration_hours,
                ROUND(QUANTILE_CONT(waybill.actual_duration_hours, 0.9), 2) AS p90_duration_hours
            FROM fact_waybill AS waybill
            JOIN dim_route AS route ON waybill.route_id = route.route_id
            GROUP BY 1, 2, 3, 4
            """
        )
        connection.execute("CHECKPOINT")
    finally:
        connection.close()
    return database_path
