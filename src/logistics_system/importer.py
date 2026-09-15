from __future__ import annotations

import warnings
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import BinaryIO

import pandas as pd

from .generator import write_frames_to_database


SHEET_TO_TABLE = {
    "网点表": "dim_site",
    "线路表": "dim_route",
    "运单表": "fact_waybill",
    "扫描事件表": "fact_scan_event",
    "网点日产能表": "fact_site_daily_capacity",
    "异常事件表": "fact_exception",
    "场景真值": "scenario_truth",
}

REQUIRED_SHEETS = tuple(list(SHEET_TO_TABLE)[:5])

EXPECTED_COLUMNS = {
    "网点表": [
        "site_id", "site_name", "region", "city", "site_type",
        "base_daily_capacity", "opened_date",
    ],
    "线路表": [
        "route_id", "origin_site_id", "destination_site_id", "distance_km",
        "route_type", "standard_linehaul_hours",
    ],
    "运单表": [
        "waybill_id", "route_id", "origin_site_id", "destination_site_id",
        "service_type", "shipped_at", "promised_delivery_at", "delivered_at",
        "actual_duration_hours", "is_on_time", "is_damaged", "weight_kg",
        "transport_cost",
    ],
    "扫描事件表": [
        "event_id", "waybill_id", "event_order", "event_type", "site_id", "event_at",
    ],
    "网点日产能表": [
        "date", "site_id", "planned_capacity", "actual_processed", "staff_count",
        "backlog_count",
    ],
    "异常事件表": [
        "exception_id", "waybill_id", "exception_type", "exception_stage", "site_id",
        "route_id", "occurred_at", "severity",
    ],
    "场景真值": [
        "scenario_id", "scenario_type", "site_id", "route_id", "start_date",
        "end_date", "injected_effect",
    ],
}

DATE_COLUMNS = {
    "网点表": ["opened_date"],
    "运单表": ["shipped_at", "promised_delivery_at", "delivered_at"],
    "扫描事件表": ["event_at"],
    "网点日产能表": ["date"],
    "异常事件表": ["occurred_at"],
    "场景真值": ["start_date", "end_date"],
}

NUMERIC_COLUMNS = {
    "网点表": ["base_daily_capacity"],
    "线路表": ["distance_km", "standard_linehaul_hours"],
    "运单表": ["actual_duration_hours", "weight_kg", "transport_cost"],
    "扫描事件表": ["event_order"],
    "网点日产能表": [
        "planned_capacity", "actual_processed", "staff_count", "backlog_count",
    ],
}

PRIMARY_KEYS = {
    "网点表": ["site_id"],
    "线路表": ["route_id"],
    "运单表": ["waybill_id"],
    "扫描事件表": ["event_id"],
    "网点日产能表": ["date", "site_id"],
    "异常事件表": ["exception_id"],
    "场景真值": ["scenario_id"],
}

MAX_ROWS = {
    "网点表": 10_000,
    "线路表": 100_000,
    "运单表": 200_000,
    "扫描事件表": 1_000_000,
    "网点日产能表": 500_000,
    "异常事件表": 500_000,
    "场景真值": 10_000,
}

FORBIDDEN_PII_HEADERS = {
    "customer_name", "sender_name", "recipient_name", "phone", "mobile",
    "address", "id_card", "姓名", "电话", "手机号", "地址", "身份证",
    "收件人", "寄件人",
}


@dataclass
class ExcelImportResult:
    frames: dict[str, pd.DataFrame]
    row_counts: dict[str, int]
    errors: list[str]
    warnings: list[str]

    @property
    def is_valid(self) -> bool:
        return not self.errors


def _source_bytes(source: bytes | bytearray | BinaryIO | str | Path) -> bytes:
    if isinstance(source, (str, Path)):
        return Path(source).read_bytes()
    if isinstance(source, (bytes, bytearray)):
        return bytes(source)
    source.seek(0)
    return source.read()


def _coerce_boolean(series: pd.Series) -> tuple[pd.Series, int]:
    mapping = {
        True: True, False: False, 1: True, 0: False,
        "true": True, "false": False, "yes": True, "no": False,
        "是": True, "否": False, "1": True, "0": False,
    }
    converted = series.map(lambda value: mapping.get(value, mapping.get(str(value).strip().lower())))
    invalid = int((series.notna() & converted.isna()).sum())
    return converted.astype("boolean"), invalid


def _clean_frame(sheet_name: str, frame: pd.DataFrame, errors: list[str]) -> pd.DataFrame:
    frame = frame.dropna(how="all").copy()
    frame.columns = [str(column).strip() for column in frame.columns]
    if sheet_name in REQUIRED_SHEETS and frame.empty:
        errors.append(f"{sheet_name} 不能为空")
    pii = sorted(set(frame.columns) & FORBIDDEN_PII_HEADERS)
    if pii:
        errors.append(f"{sheet_name} 包含禁止上传的个人信息字段：{', '.join(pii)}")

    missing = [column for column in EXPECTED_COLUMNS[sheet_name] if column not in frame.columns]
    if missing:
        errors.append(f"{sheet_name} 缺少字段：{', '.join(missing)}")
        return frame

    extra = [column for column in frame.columns if column not in EXPECTED_COLUMNS[sheet_name]]
    if extra:
        frame = frame.drop(columns=extra)
    frame = frame[EXPECTED_COLUMNS[sheet_name]]

    for column in frame.select_dtypes(include="object").columns:
        frame[column] = frame[column].map(
            lambda value: value.strip() if isinstance(value, str) else value
        )
        frame[column] = frame[column].replace("", pd.NA)

    for column in DATE_COLUMNS.get(sheet_name, []):
        original = frame[column].copy()
        frame[column] = pd.to_datetime(frame[column], errors="coerce")
        invalid = int((original.notna() & frame[column].isna()).sum())
        if invalid:
            errors.append(f"{sheet_name}.{column} 有 {invalid} 个无法识别的日期")

    for column in NUMERIC_COLUMNS.get(sheet_name, []):
        original = frame[column].copy()
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
        invalid = int((original.notna() & frame[column].isna()).sum())
        if invalid:
            errors.append(f"{sheet_name}.{column} 有 {invalid} 个无法识别的数字")
        negative = int((frame[column] < 0).fillna(False).sum())
        if negative:
            errors.append(f"{sheet_name}.{column} 有 {negative} 个负数")

    if sheet_name == "运单表":
        for column in ["is_on_time", "is_damaged"]:
            frame[column], invalid = _coerce_boolean(frame[column])
            if invalid:
                errors.append(f"运单表.{column} 有 {invalid} 个无法识别的布尔值")

    required_columns = EXPECTED_COLUMNS[sheet_name]
    if sheet_name in {"异常事件表", "场景真值"}:
        nullable = {"site_id", "route_id"}
        required_columns = [column for column in required_columns if column not in nullable]
    missing_values = {
        column: int(frame[column].isna().sum())
        for column in required_columns
        if frame[column].isna().any()
    }
    if missing_values:
        detail = ", ".join(f"{column}={count}" for column, count in missing_values.items())
        errors.append(f"{sheet_name} 存在必填空值：{detail}")

    keys = PRIMARY_KEYS[sheet_name]
    duplicate_count = int(frame.duplicated(keys, keep=False).sum())
    if duplicate_count:
        errors.append(f"{sheet_name} 主键重复，涉及 {duplicate_count} 行（键：{', '.join(keys)}）")
    return frame


def _missing_refs(values: pd.Series, valid_values: set[object]) -> list[str]:
    return sorted(str(value) for value in set(values.dropna()) - valid_values)[:5]


def _validate_relationships(frames: dict[str, pd.DataFrame], errors: list[str]) -> None:
    sites = set(frames["dim_site"]["site_id"].dropna())
    routes = set(frames["dim_route"]["route_id"].dropna())
    waybills = set(frames["fact_waybill"]["waybill_id"].dropna())
    checks = [
        ("线路表.origin_site_id", frames["dim_route"]["origin_site_id"], sites),
        ("线路表.destination_site_id", frames["dim_route"]["destination_site_id"], sites),
        ("运单表.origin_site_id", frames["fact_waybill"]["origin_site_id"], sites),
        ("运单表.destination_site_id", frames["fact_waybill"]["destination_site_id"], sites),
        ("运单表.route_id", frames["fact_waybill"]["route_id"], routes),
        ("扫描事件表.site_id", frames["fact_scan_event"]["site_id"], sites),
        ("扫描事件表.waybill_id", frames["fact_scan_event"]["waybill_id"], waybills),
        ("网点日产能表.site_id", frames["fact_site_daily_capacity"]["site_id"], sites),
    ]
    if "fact_exception" in frames:
        checks.extend([
            ("异常事件表.site_id", frames["fact_exception"]["site_id"], sites),
            ("异常事件表.route_id", frames["fact_exception"]["route_id"], routes),
            ("异常事件表.waybill_id", frames["fact_exception"]["waybill_id"], waybills),
        ])
    for label, values, valid_values in checks:
        missing = _missing_refs(values, valid_values)
        if missing:
            errors.append(f"{label} 存在无效引用，例如：{', '.join(missing)}")


def _derive_supporting_frames(frames: dict[str, pd.DataFrame]) -> None:
    waybills = frames["fact_waybill"]
    capacity = frames["fact_site_daily_capacity"]
    start = min(waybills["shipped_at"].min().normalize(), capacity["date"].min().normalize())
    end = max(waybills["shipped_at"].max().normalize(), capacity["date"].max().normalize())
    dates = pd.date_range(start=start, end=end, freq="D")
    date_dim = pd.DataFrame({"date": dates})
    date_dim["year"] = date_dim["date"].dt.year
    date_dim["month"] = date_dim["date"].dt.month
    date_dim["week"] = date_dim["date"].dt.isocalendar().week.astype(int)
    date_dim["weekday"] = date_dim["date"].dt.weekday + 1
    date_dim["is_weekend"] = date_dim["weekday"] >= 6
    frames["dim_date"] = date_dim

    sla_defaults = {"特快": 24, "标快": 48, "经济件": 72}
    services = sorted(waybills["service_type"].dropna().unique())
    frames["dim_service_sla"] = pd.DataFrame({
        "service_type": services,
        "sla_hours": [sla_defaults.get(service, 48) for service in services],
        "effective_from": [start] * len(services),
    })

    if "fact_exception" not in frames:
        late = waybills.loc[~waybills["is_on_time"].fillna(False)].copy()
        damage = waybills.loc[waybills["is_damaged"].fillna(False)].copy()
        rows: list[dict[str, object]] = []
        for row in late.itertuples():
            rows.append({
                "waybill_id": row.waybill_id, "exception_type": "时效异常",
                "exception_stage": "待人工判定", "site_id": row.destination_site_id,
                "route_id": row.route_id, "occurred_at": row.delivered_at,
                "severity": "中",
            })
        for row in damage.itertuples():
            rows.append({
                "waybill_id": row.waybill_id, "exception_type": "破损异常",
                "exception_stage": "待人工判定", "site_id": row.destination_site_id,
                "route_id": row.route_id, "occurred_at": row.delivered_at,
                "severity": "高",
            })
        exception_columns = EXPECTED_COLUMNS["异常事件表"]
        exception_frame = pd.DataFrame(rows)
        if exception_frame.empty:
            exception_frame = pd.DataFrame({column: pd.Series(dtype="object") for column in exception_columns})
            exception_frame["occurred_at"] = pd.Series(dtype="datetime64[ns]")
        else:
            exception_frame.insert(0, "exception_id", [
                f"EX{index:07d}" for index in range(1, len(exception_frame) + 1)
            ])
            exception_frame = exception_frame[exception_columns]
        frames["fact_exception"] = exception_frame

    if "scenario_truth" not in frames:
        columns = EXPECTED_COLUMNS["场景真值"]
        scenario_frame = pd.DataFrame({column: pd.Series(dtype="object") for column in columns})
        scenario_frame["start_date"] = pd.Series(dtype="datetime64[ns]")
        scenario_frame["end_date"] = pd.Series(dtype="datetime64[ns]")
        frames["scenario_truth"] = scenario_frame


def parse_logistics_excel(
    source: bytes | bytearray | BinaryIO | str | Path,
) -> ExcelImportResult:
    """Parse and validate the standard logistics Excel workbook without persisting it."""
    errors: list[str] = []
    warning_messages: list[str] = []
    frames: dict[str, pd.DataFrame] = {}
    raw = _source_bytes(source)
    if len(raw) > 80 * 1024 * 1024:
        return ExcelImportResult({}, {}, ["文件超过 80 MB，请拆分或减少演示数据量"], [])

    try:
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="Unknown extension.*")
            warnings.filterwarnings("ignore", message="Conditional Formatting extension.*")
            workbook = pd.ExcelFile(BytesIO(raw), engine="openpyxl")
    except Exception as exc:
        return ExcelImportResult({}, {}, [f"无法读取 Excel 文件：{exc}"], [])

    missing_sheets = [sheet for sheet in REQUIRED_SHEETS if sheet not in workbook.sheet_names]
    if missing_sheets:
        errors.append(f"缺少必需工作表：{', '.join(missing_sheets)}")

    for sheet_name, table_name in SHEET_TO_TABLE.items():
        if sheet_name not in workbook.sheet_names:
            if sheet_name not in REQUIRED_SHEETS:
                warning_messages.append(
                    f"未提供可选工作表“{sheet_name}”，系统将自动生成兼容数据"
                )
            continue
        try:
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", message="Unknown extension.*")
                warnings.filterwarnings("ignore", message="Conditional Formatting extension.*")
                frame = pd.read_excel(workbook, sheet_name=sheet_name, dtype=object)
        except Exception as exc:
            errors.append(f"读取工作表“{sheet_name}”失败：{exc}")
            continue
        if len(frame) > MAX_ROWS[sheet_name]:
            errors.append(f"{sheet_name} 超过 {MAX_ROWS[sheet_name]:,} 行上限")
        frames[table_name] = _clean_frame(sheet_name, frame, errors)

    required_tables = {SHEET_TO_TABLE[sheet] for sheet in REQUIRED_SHEETS}
    if errors or not required_tables.issubset(frames):
        return ExcelImportResult(frames, {
            sheet: len(frames[table]) for sheet, table in SHEET_TO_TABLE.items() if table in frames
        }, errors, warning_messages)

    _validate_relationships(frames, errors)
    if not errors:
        _derive_supporting_frames(frames)
    row_counts = {
        sheet: len(frames[table]) for sheet, table in SHEET_TO_TABLE.items() if table in frames
    }
    return ExcelImportResult(frames, row_counts, errors, warning_messages)


def import_logistics_excel(
    source: bytes | bytearray | BinaryIO | str | Path,
    database_path: Path | str,
) -> ExcelImportResult:
    """Validate a workbook and persist it as an analysis-ready DuckDB database."""
    result = parse_logistics_excel(source)
    if result.is_valid:
        write_frames_to_database(result.frames, database_path)
    return result
