from __future__ import annotations

import sys
import tempfile
import uuid
from datetime import timedelta
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st


PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from logistics_system.analytics import (  # noqa: E402
    get_anomalies,
    get_date_bounds,
    get_diagnostic_evidence,
    get_overview,
    get_site_daily,
    get_site_ranking,
    get_sites,
)
from logistics_system.database import ensure_demo_database  # noqa: E402
from logistics_system.importer import (  # noqa: E402
    SHEET_TO_TABLE,
    import_logistics_excel,
    parse_logistics_excel,
)


st.set_page_config(
    page_title="物流网点营运监控",
    page_icon="🚚",
    layout="wide",
    initial_sidebar_state="expanded",
)


@st.cache_resource
def initialize_database() -> str:
    return str(ensure_demo_database())


@st.cache_data(ttl=600)
def cached_sites(database_path: str) -> pd.DataFrame:
    return get_sites(database_path)


@st.cache_data(ttl=600)
def cached_date_bounds(database_path: str):
    return get_date_bounds(database_path)


@st.cache_data(show_spinner=False)
def cached_parse_upload(raw: bytes):
    return parse_logistics_excel(raw)


def render_header(title: str, description: str) -> None:
    st.title(title)
    st.caption(description)


def render_overview(start_date, end_date, region: str, database_path: str) -> None:
    render_header("营运总览", "从片区与网点两个层级观察运量、时效、质量和产能。")
    metrics = get_overview(start_date, end_date, region=region, database_path=database_path)
    columns = st.columns(6)
    columns[0].metric("总运单量", f"{int(metrics['shipment_count']):,}")
    columns[1].metric("时效达成率", f"{metrics['on_time_rate']:.1f}%")
    columns[2].metric("破损率", f"{metrics['damage_rate']:.2f}%")
    columns[3].metric("平均时长", f"{metrics['avg_duration_hours']:.1f} 小时")
    columns[4].metric("产能利用率", f"{metrics['capacity_utilization_rate']:.1f}%")
    columns[5].metric("累计积压", f"{int(metrics['backlog_count']):,}")

    daily = get_site_daily(start_date, end_date, region=region, database_path=database_path)
    if daily.empty:
        st.info("当前筛选条件下没有数据。")
        return
    trend = (
        daily.groupby("date", as_index=False)
        .agg(
            shipment_count=("shipment_count", "sum"),
            on_time_count=("on_time_count", "sum"),
            damage_count=("damage_count", "sum"),
        )
    )
    trend["时效达成率"] = 100 * trend["on_time_count"] / trend["shipment_count"]
    trend["破损率"] = 100 * trend["damage_count"] / trend["shipment_count"]

    left, right = st.columns([1.5, 1])
    with left:
        st.subheader("时效与质量趋势")
        chart_data = trend.melt(
            id_vars="date",
            value_vars=["时效达成率", "破损率"],
            var_name="指标",
            value_name="指标值",
        )
        figure = px.line(chart_data, x="date", y="指标值", color="指标", markers=True)
        figure.update_layout(xaxis_title="日期", yaxis_title="比例（%）", legend_title="指标")
        st.plotly_chart(figure, width="stretch")
    with right:
        st.subheader("网点时效排名")
        ranking = get_site_ranking(
            start_date, end_date, region=region, database_path=database_path
        ).head(10)
        figure = px.bar(
            ranking.sort_values("on_time_rate"),
            x="on_time_rate",
            y="site_name",
            orientation="h",
            color="on_time_rate",
            color_continuous_scale="RdYlGn",
        )
        figure.update_layout(xaxis_title="时效达成率（%）", yaxis_title="网点", coloraxis_showscale=False)
        st.plotly_chart(figure, width="stretch")

    st.subheader("网点营运明细")
    ranking_display = get_site_ranking(
        start_date, end_date, region=region, database_path=database_path
    ).rename(
        columns={
            "site_name": "网点",
            "region": "片区",
            "shipment_count": "运单量",
            "on_time_rate": "时效达成率（%）",
            "damage_rate": "破损率（%）",
            "capacity_utilization_rate": "平均产能利用率（%）",
            "backlog_count": "累计积压",
        }
    )
    st.dataframe(ranking_display.drop(columns=["site_id"]), width="stretch", hide_index=True)


def render_anomalies(start_date, end_date, region: str, database_path: str) -> None:
    render_header("异常诊断", "规则和统计基线先生成证据，再提供运营建议。")
    anomalies = get_anomalies(
        start_date, end_date, region=region, database_path=database_path
    )
    if anomalies.empty:
        st.success("当前时间范围内未检测到达到阈值的异常。")
        return

    high_count = int((anomalies["severity"] == "高").sum())
    medium_count = int((anomalies["severity"] == "中").sum())
    metric_cols = st.columns(3)
    metric_cols[0].metric("异常记录", len(anomalies))
    metric_cols[1].metric("高等级", high_count)
    metric_cols[2].metric("中等级", medium_count)

    display = anomalies.rename(
        columns={
            "date": "日期",
            "site_name": "网点",
            "region": "片区",
            "severity": "等级",
            "anomaly_score": "异常分",
            "on_time_rate": "时效达成率（%）",
            "damage_rate": "破损率（%）",
            "capacity_utilization_rate": "产能利用率（%）",
            "backlog_count": "积压量",
        }
    )
    st.dataframe(
        display[
            [
                "日期",
                "网点",
                "片区",
                "等级",
                "异常分",
                "时效达成率（%）",
                "破损率（%）",
                "产能利用率（%）",
                "积压量",
            ]
        ],
        width="stretch",
        hide_index=True,
    )

    st.subheader("异常下钻")
    options = {
        f"{row.date:%Y-%m-%d} · {row.site_name} · {row.severity} · {row.anomaly_score:.1f} 分": index
        for index, row in anomalies.iterrows()
    }
    selected_label = st.selectbox("选择异常记录", list(options))
    selected = anomalies.loc[options[selected_label]]
    evidence = get_diagnostic_evidence(
        selected["site_id"], selected["date"], database_path=database_path
    )

    st.markdown(f"**诊断摘要：** {evidence['summary']}")
    st.markdown(f"**诊断置信度：** {evidence['confidence']}")
    st.markdown(f"**运营建议：** {evidence['recommendation']}")
    if not evidence["stages"].empty:
        st.caption("异常环节证据")
        st.dataframe(evidence["stages"], width="stretch", hide_index=True)


def render_site_profile(
    start_date, end_date, sites: pd.DataFrame, database_path: str
) -> None:
    render_header("网点画像", "查看单个网点的运量、时效、质量、产能及历史异常。")
    selected_name = st.selectbox("选择网点", sites["site_name"].tolist())
    site = sites.loc[sites["site_name"] == selected_name].iloc[0]
    metrics = get_overview(
        start_date, end_date, site_id=site["site_id"], database_path=database_path
    )

    columns = st.columns(5)
    columns[0].metric("运单量", f"{int(metrics['shipment_count']):,}")
    columns[1].metric("时效达成率", f"{metrics['on_time_rate']:.1f}%")
    columns[2].metric("破损率", f"{metrics['damage_rate']:.2f}%")
    columns[3].metric("产能利用率", f"{metrics['capacity_utilization_rate']:.1f}%")
    columns[4].metric("累计积压", f"{int(metrics['backlog_count']):,}")

    daily = get_site_daily(
        start_date, end_date, site_id=site["site_id"], database_path=database_path
    )
    if daily.empty:
        st.info("当前筛选条件下没有数据。")
        return
    chart_data = daily.melt(
        id_vars="date",
        value_vars=["on_time_rate", "capacity_utilization_rate"],
        var_name="指标",
        value_name="指标值",
    )
    chart_data["指标"] = chart_data["指标"].replace(
        {"on_time_rate": "时效达成率", "capacity_utilization_rate": "产能利用率"}
    )
    figure = px.line(chart_data, x="date", y="指标值", color="指标", markers=True)
    figure.update_layout(xaxis_title="日期", yaxis_title="比例（%）", legend_title="指标")
    st.plotly_chart(figure, width="stretch")

    anomalies = get_anomalies(
        start_date, end_date, region=None, database_path=database_path
    )
    anomalies = anomalies.loc[anomalies["site_id"] == site["site_id"]]
    st.subheader("历史异常")
    if anomalies.empty:
        st.info("所选时间范围内没有达到阈值的异常。")
    else:
        st.dataframe(
            anomalies[["date", "severity", "anomaly_score", "on_time_rate", "damage_rate", "backlog_count"]],
            width="stretch",
            hide_index=True,
        )


def render_about() -> None:
    render_header("项目说明", "以业务问题、指标体系和可验证诊断为核心的数据产品 Demo。")
    st.markdown(
        """
        ### 产品闭环

        1. 营运大盘发现指标异常。
        2. 异常中心定位网点与日期。
        3. 扫描事件、产能和积压形成诊断证据。
        4. 规则服务输出运营建议，大模型仅作为可选解释层。
        5. 后续版本记录处置动作并复盘改善结果。

        ### 当前版本边界

        当前版本使用模拟数据，不包含真实客户、地址或运单隐私数据。线路时效预测将在具备更长历史窗口和可评估基线后加入。
        """
    )


def render_data_import(default_database_path: str) -> None:
    render_header(
        "数据导入",
        "上传标准 Excel，校验通过后将它作为本次会话的数据源，所有分析页面同步切换。",
    )
    st.info("上传数据只写入本机临时目录，不会覆盖内置演示库；关闭会话后可直接恢复默认数据。")

    template_path = PROJECT_ROOT / "data" / "templates" / "物流营运演示数据.xlsx"
    if template_path.exists():
        st.download_button(
            "下载标准演示表格",
            data=template_path.read_bytes(),
            file_name=template_path.name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    else:
        st.warning("演示表格尚未生成，请先运行项目随附的数据构建步骤。")

    uploaded_file = st.file_uploader("选择 Excel 文件", type=["xlsx"])
    if uploaded_file is None:
        st.caption("请保留英文列名；不要上传客户姓名、电话、详细地址或身份证等个人信息。")
    else:
        raw = uploaded_file.getvalue()
        with st.spinner("正在检查工作表结构和数据关系..."):
            result = cached_parse_upload(raw)
        if result.errors:
            st.error("文件未通过校验，请修正后重新上传。")
            for message in result.errors:
                st.write(f"- {message}")
        else:
            st.success("校验通过，可以导入并开始分析。")
            if result.warnings:
                for message in result.warnings:
                    st.warning(message)

            summary = pd.DataFrame(
                [{"工作表": sheet, "数据行数": count} for sheet, count in result.row_counts.items()]
            )
            st.subheader("数据概况")
            st.dataframe(summary, width="stretch", hide_index=True)

            preview_sheet = st.selectbox("预览工作表", list(result.row_counts))
            preview_table = SHEET_TO_TABLE[preview_sheet]
            st.dataframe(result.frames[preview_table].head(20), width="stretch", hide_index=True)

            if st.button("导入并开始分析", type="primary"):
                upload_dir = Path(tempfile.gettempdir()) / "logistics-system-demo"
                upload_dir.mkdir(parents=True, exist_ok=True)
                database_path = upload_dir / f"upload-{uuid.uuid4().hex}.duckdb"
                with st.spinner("正在建立临时分析库..."):
                    imported = import_logistics_excel(raw, database_path)
                if imported.is_valid:
                    previous = st.session_state.get("uploaded_database_path")
                    if previous and Path(previous).exists():
                        Path(previous).unlink(missing_ok=True)
                    st.session_state["uploaded_database_path"] = str(database_path)
                    st.session_state["active_database_path"] = str(database_path)
                    st.session_state["data_source_name"] = uploaded_file.name
                    cached_sites.clear()
                    cached_date_bounds.clear()
                    st.success("导入完成，正在切换到营运总览。")
                    st.session_state["pending_page_selector"] = "营运总览"
                    st.rerun()

    if st.session_state.get("active_database_path") != default_database_path:
        if st.button("恢复内置演示数据"):
            st.session_state["active_database_path"] = default_database_path
            st.session_state["data_source_name"] = "内置演示数据"
            cached_sites.clear()
            cached_date_bounds.clear()
            st.rerun()


default_database_path = initialize_database()
active_path = st.session_state.get("active_database_path", default_database_path)
if not Path(active_path).exists():
    active_path = default_database_path
    st.session_state["active_database_path"] = default_database_path
    st.session_state["data_source_name"] = "内置演示数据"
st.session_state.setdefault("active_database_path", active_path)
st.session_state.setdefault("data_source_name", "内置演示数据")

sites = cached_sites(active_path)
minimum_date, maximum_date = cached_date_bounds(active_path)

st.sidebar.header("物流营运监控")
st.sidebar.caption(f"当前数据源：{st.session_state['data_source_name']}")
if "pending_page_selector" in st.session_state:
    st.session_state["page_selector"] = st.session_state.pop("pending_page_selector")
page = st.sidebar.radio(
    "页面",
    ["数据导入", "营运总览", "异常诊断", "网点画像", "项目说明"],
    key="page_selector",
)
default_start = max(minimum_date, maximum_date - timedelta(days=29))
selected_dates = st.sidebar.date_input(
    "统计日期",
    value=(default_start, maximum_date),
    min_value=minimum_date,
    max_value=maximum_date,
)
if isinstance(selected_dates, tuple) and len(selected_dates) == 2:
    start_date, end_date = selected_dates
else:
    start_date = end_date = selected_dates
regions = ["全部"] + sorted(sites["region"].unique().tolist())
region = st.sidebar.selectbox("片区", regions)
st.sidebar.caption("数据为可复现的模拟物流数据，不包含真实个人信息。")

if page == "数据导入":
    render_data_import(default_database_path)
elif page == "营运总览":
    render_overview(start_date, end_date, region, active_path)
elif page == "异常诊断":
    render_anomalies(start_date, end_date, region, active_path)
elif page == "网点画像":
    render_site_profile(start_date, end_date, sites, active_path)
else:
    render_about()
