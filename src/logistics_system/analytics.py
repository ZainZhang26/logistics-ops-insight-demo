from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from .config import DATABASE_PATH
from .database import connect_read_only


def _filters(
    start_date: date | str | None,
    end_date: date | str | None,
    region: str | None = None,
    site_id: str | None = None,
    alias: str = "m",
) -> tuple[str, list[object]]:
    clauses = ["1 = 1"]
    params: list[object] = []
    if start_date is not None:
        clauses.append(f"{alias}.date >= ?")
        params.append(str(start_date))
    if end_date is not None:
        clauses.append(f"{alias}.date <= ?")
        params.append(str(end_date))
    if region and region != "全部":
        clauses.append(f"{alias}.region = ?")
        params.append(region)
    if site_id and site_id != "全部":
        clauses.append(f"{alias}.site_id = ?")
        params.append(site_id)
    return " AND ".join(clauses), params


def get_date_bounds(database_path: Path | str = DATABASE_PATH) -> tuple[date, date]:
    with connect_read_only(database_path) as connection:
        start, end = connection.execute(
            "SELECT MIN(date), MAX(date) FROM mart_site_daily"
        ).fetchone()
    return start, end


def get_sites(database_path: Path | str = DATABASE_PATH) -> pd.DataFrame:
    with connect_read_only(database_path) as connection:
        return connection.execute(
            "SELECT site_id, site_name, region, city, site_type FROM dim_site ORDER BY region, site_id"
        ).df()


def get_overview(
    start_date: date | str,
    end_date: date | str,
    region: str | None = None,
    site_id: str | None = None,
    database_path: Path | str = DATABASE_PATH,
) -> dict[str, float]:
    where_clause, params = _filters(start_date, end_date, region, site_id)
    query = f"""
        SELECT
            COALESCE(SUM(m.shipment_count), 0) AS shipment_count,
            COALESCE(100.0 * SUM(m.on_time_count) / NULLIF(SUM(m.shipment_count), 0), 0) AS on_time_rate,
            COALESCE(100.0 * SUM(m.damage_count) / NULLIF(SUM(m.shipment_count), 0), 0) AS damage_rate,
            COALESCE(SUM(m.avg_duration_hours * m.shipment_count) / NULLIF(SUM(m.shipment_count), 0), 0) AS avg_duration_hours,
            COALESCE(AVG(m.capacity_utilization_rate), 0) AS capacity_utilization_rate,
            COALESCE(SUM(m.backlog_count), 0) AS backlog_count
        FROM mart_site_daily AS m
        WHERE {where_clause}
    """
    with connect_read_only(database_path) as connection:
        row = connection.execute(query, params).fetchone()
    keys = [
        "shipment_count",
        "on_time_rate",
        "damage_rate",
        "avg_duration_hours",
        "capacity_utilization_rate",
        "backlog_count",
    ]
    return {key: round(float(value or 0), 2) for key, value in zip(keys, row, strict=True)}


def get_site_daily(
    start_date: date | str,
    end_date: date | str,
    region: str | None = None,
    site_id: str | None = None,
    database_path: Path | str = DATABASE_PATH,
) -> pd.DataFrame:
    where_clause, params = _filters(start_date, end_date, region, site_id)
    with connect_read_only(database_path) as connection:
        return connection.execute(
            f"SELECT * FROM mart_site_daily AS m WHERE {where_clause} ORDER BY m.date, m.site_id",
            params,
        ).df()


def get_site_ranking(
    start_date: date | str,
    end_date: date | str,
    region: str | None = None,
    database_path: Path | str = DATABASE_PATH,
) -> pd.DataFrame:
    where_clause, params = _filters(start_date, end_date, region)
    query = f"""
        SELECT
            m.site_id,
            m.site_name,
            m.region,
            SUM(m.shipment_count) AS shipment_count,
            ROUND(100.0 * SUM(m.on_time_count) / NULLIF(SUM(m.shipment_count), 0), 2) AS on_time_rate,
            ROUND(100.0 * SUM(m.damage_count) / NULLIF(SUM(m.shipment_count), 0), 2) AS damage_rate,
            ROUND(AVG(m.capacity_utilization_rate), 2) AS capacity_utilization_rate,
            SUM(m.backlog_count) AS backlog_count
        FROM mart_site_daily AS m
        WHERE {where_clause}
        GROUP BY 1, 2, 3
        ORDER BY on_time_rate ASC, damage_rate DESC
    """
    with connect_read_only(database_path) as connection:
        return connection.execute(query, params).df()


def get_anomalies(
    start_date: date | str,
    end_date: date | str,
    region: str | None = None,
    limit: int = 100,
    database_path: Path | str = DATABASE_PATH,
) -> pd.DataFrame:
    where_clause, params = _filters(start_date, end_date, region, alias="scored")
    params.append(limit)
    query = f"""
        WITH baseline AS (
            SELECT
                m.*,
                AVG(m.on_time_rate) OVER (
                    PARTITION BY m.site_id ORDER BY m.date ROWS BETWEEN 14 PRECEDING AND 1 PRECEDING
                ) AS baseline_on_time_rate,
                AVG(m.damage_rate) OVER (
                    PARTITION BY m.site_id ORDER BY m.date ROWS BETWEEN 14 PRECEDING AND 1 PRECEDING
                ) AS baseline_damage_rate,
                AVG(m.p90_duration_hours) OVER (
                    PARTITION BY m.site_id ORDER BY m.date ROWS BETWEEN 14 PRECEDING AND 1 PRECEDING
                ) AS baseline_p90_duration
            FROM mart_site_daily AS m
        ),
        scored AS (
            SELECT
                *,
                (
                    GREATEST(COALESCE(baseline_on_time_rate - on_time_rate, 0), 0) * 2
                    + GREATEST(COALESCE(damage_rate - baseline_damage_rate, 0), 0) * 8
                    + GREATEST(
                        COALESCE(p90_duration_hours / NULLIF(baseline_p90_duration, 0) - 1, 0), 0
                      ) * 30
                ) * LEAST(shipment_count / 30.0, 1.0)
                + GREATEST(COALESCE(capacity_utilization_rate - 105, 0), 0) * 0.35
                + LEAST(backlog_count, 250) * 0.08
                AS anomaly_score
            FROM baseline
        )
        SELECT
            scored.date,
            scored.site_id,
            scored.site_name,
            scored.region,
            scored.shipment_count,
            scored.on_time_rate,
            ROUND(scored.baseline_on_time_rate, 2) AS baseline_on_time_rate,
            scored.damage_rate,
            ROUND(scored.baseline_damage_rate, 2) AS baseline_damage_rate,
            scored.p90_duration_hours,
            ROUND(scored.baseline_p90_duration, 2) AS baseline_p90_duration,
            scored.capacity_utilization_rate,
            scored.backlog_count,
            ROUND(scored.anomaly_score, 2) AS anomaly_score,
            CASE
                WHEN scored.anomaly_score >= 45 THEN '高'
                WHEN scored.anomaly_score >= 22 THEN '中'
                ELSE '低'
            END AS severity
        FROM scored
        WHERE {where_clause}
          AND scored.baseline_on_time_rate IS NOT NULL
          AND scored.anomaly_score >= 10
        ORDER BY scored.anomaly_score DESC
        LIMIT ?
    """
    with connect_read_only(database_path) as connection:
        return connection.execute(query, params).df()


def get_diagnostic_evidence(
    site_id: str,
    selected_date: date | str,
    database_path: Path | str = DATABASE_PATH,
) -> dict[str, object]:
    with connect_read_only(database_path) as connection:
        metrics = connection.execute(
            """
            SELECT * FROM mart_site_daily
            WHERE site_id = ? AND date = ?
            """,
            [site_id, str(selected_date)],
        ).df()
        stages = connection.execute(
            """
            SELECT exception_stage, exception_type, COUNT(*) AS exception_count
            FROM fact_exception
            WHERE site_id = ? AND CAST(occurred_at AS DATE) BETWEEN CAST(? AS DATE) AND CAST(? AS DATE) + 3
            GROUP BY 1, 2
            ORDER BY exception_count DESC
            """,
            [site_id, str(selected_date), str(selected_date)],
        ).df()
        scenarios = connection.execute(
            """
            SELECT scenario_type, injected_effect
            FROM scenario_truth
            WHERE site_id = ? AND CAST(? AS DATE) BETWEEN start_date AND end_date
            """,
            [site_id, str(selected_date)],
        ).df()

    if metrics.empty:
        return {"summary": "未找到所选网点和日期的数据", "metrics": metrics, "stages": stages}

    row = metrics.iloc[0]
    evidence: list[str] = []
    if row["on_time_rate"] < 90:
        evidence.append(f"时效达成率为 {row['on_time_rate']:.1f}%")
    if row["damage_rate"] > 1.5:
        evidence.append(f"破损率升至 {row['damage_rate']:.2f}%")
    if row["capacity_utilization_rate"] > 105:
        evidence.append(f"产能利用率达到 {row['capacity_utilization_rate']:.1f}%")
    if row["backlog_count"] > 50:
        evidence.append(f"积压量为 {int(row['backlog_count'])} 票")
    if not stages.empty:
        top_stage = stages.iloc[0]
        evidence.append(
            f"异常主要集中在{top_stage['exception_stage']}，共 {int(top_stage['exception_count'])} 条"
        )

    if row["backlog_count"] > 100:
        recommendation = "优先核查排班和分拣工位负荷，短时调拨人力，并监控未来两个班次的积压消化速度。"
    elif row["damage_rate"] > 1.5:
        recommendation = "抽查高破损线路的装卸和包装记录，按线路与班次定位集中发生点。"
    elif row["on_time_rate"] < 90:
        recommendation = "按干线到达、分拣完成和末端派送三个环节拆分耗时，优先处理偏离最大的环节。"
    else:
        recommendation = "继续观察后续两个统计周期，并与同片区同规模网点进行对比。"

    return {
        "summary": "；".join(evidence) if evidence else "未发现明显异常证据",
        "recommendation": recommendation,
        "confidence": "高" if len(evidence) >= 3 else "中",
        "metrics": metrics,
        "stages": stages,
        "scenario_truth": scenarios,
    }
