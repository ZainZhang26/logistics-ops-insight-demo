# 物流网点营运监控与智能诊断 Demo

面向物流片区运营经理的轻量化数据产品，通过营运指标、异常检测和证据化诊断，帮助用户快速发现异常网点并形成可执行的处置建议。项目可直接在本地运行，也适合作为数据产品、物流分析和 Streamlit 工程示例。

> 本项目只使用固定随机种子生成的模拟数据。请勿上传包含客户姓名、电话、详细地址或身份证等个人信息的数据。

## 当前功能

- 营运总览：运单量、时效达成率、破损率、产能利用率、中转时长和积压量。
- 多维筛选：按日期和片区查看趋势、排名与网点明细。
- 异常诊断：基于历史基线、产能和积压计算异常分与严重等级。
- 异常下钻：展示异常环节、诊断证据、置信度和运营建议。
- 网点画像：查看单个网点的规模、时效、质量、产能和异常历史。
- Excel 数据导入：下载标准模板、上传校验、数据预览，并将分析页面切换到上传数据。
- 模拟数据：生成 24 个网点、48 条线路、10 万票运单和 40 万条扫描事件。

当前版本不使用真实物流数据，也不包含客户姓名、电话或地址等个人信息。

## 使用 Excel 演示数据

1. 启动应用后进入“数据导入”。
2. 点击“下载标准演示表格”，或直接使用 `data/templates/物流营运演示数据.xlsx`。
3. 上传 `.xlsx` 文件。系统会检查必需工作表、字段、主键、日期/数字格式和跨表引用。
4. 校验通过后点击“导入并开始分析”。营运总览、异常诊断和网点画像会全部切换到该文件。
5. 需要切回默认 10 万票数据时，在“数据导入”点击“恢复内置演示数据”。

必需工作表为 `网点表`、`线路表`、`运单表`、`扫描事件表` 和 `网点日产能表`；`异常事件表` 与 `场景真值` 为可选表。上传库仅保存在本机临时目录，不会覆盖内置数据库。

## 技术架构

```text
场景化模拟数据
      ↓
Parquet + DuckDB
      ↓
指标服务 + 规则诊断
      ↓
Streamlit + Plotly
```

数据生成器会注入分拣积压、产能过载、干线延误、人手不足和破损升高等已知场景，并将真值保存在 `scenario_truth` 表中。

## 快速开始

建议使用 Python 3.11 或 3.12。

```powershell
git clone https://github.com/ZainZhang26/logistics-ops-insight-demo.git
cd logistics-ops-insight-demo
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
streamlit run streamlit_app.py
```

macOS / Linux：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
streamlit run streamlit_app.py
```

浏览器打开 Streamlit 给出的本地地址即可。首次启动时，如果数据库不存在，系统会自动生成默认数据，无需预先提交 DuckDB 或 Parquet 文件。

## 生成不同规模的数据

```powershell
python scripts\build_demo_data.py --shipments 100000 --days 60 --seed 20260915
```

输出内容：

- `data/logistics_demo.duckdb`：本地分析数据库。
- `data/parquet/`：各事实表、维度表和场景真值的 Parquet 文件。

这些生成文件默认不会提交到 GitHub。

## 测试

```powershell
python -m pip install -e .
python -m unittest discover -s tests -v
```

GitHub Actions 会在 Python 3.11 和 3.12 下执行源码编译检查、Excel 导入测试和完整数据分析测试。

## GitHub 与部署

仓库已包含：

- `.gitignore`：排除虚拟环境、本地数据库、Parquet、密钥和构建产物。
- `.gitattributes`：统一文本换行并将 Excel、DuckDB、Parquet 标记为二进制文件。
- `.github/workflows/ci.yml`：推送和 Pull Request 自动测试。
- `SECURITY.md`：公开仓库的数据安全与漏洞报告规则。
- `data/templates/物流营运演示数据.xlsx`：可直接上传测试的模拟数据。

首次推送命令、发布检查和托管参数见 [GitHub 发布指南](docs/GitHub发布指南.md)。部署平台的应用入口为 `streamlit_app.py`。

## 项目目录

```text
.
├── .github/workflows/ci.yml
├── .streamlit/
├── .gitattributes
├── .gitignore
├── data/
│   └── templates/物流营运演示数据.xlsx
├── docs/
│   ├── GitHub发布指南.md
│   ├── PRD.md
│   ├── 指标体系.md
│   └── 数据模型设计.md
├── scripts/build_demo_data.py
├── src/logistics_system/
│   ├── analytics.py
│   ├── config.py
│   ├── database.py
│   ├── generator.py
│   └── importer.py
├── tests/test_pipeline.py
├── SECURITY.md
├── requirements.txt
└── streamlit_app.py
```

## 产品文档

- [精简 PRD](docs/PRD.md)
- [指标体系](docs/指标体系.md)
- [数据模型设计](docs/数据模型设计.md)
- [GitHub 发布指南](docs/GitHub发布指南.md)

## 许可证

当前仓库尚未选择开源许可证。公开发布不等于自动授权他人复制、修改或分发；如需开源协作，请在首次发布前根据实际用途选择并添加许可证。

## 后续计划

- 增加异常处置记录与效果复盘。
- 在具备更长历史窗口后增加线路时效预测和基线评估。
- 接入可选大模型解释层，限制输入为聚合证据并提供超时降级。
