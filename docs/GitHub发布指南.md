# GitHub 发布指南

## 发布前检查

在项目根目录执行：

```powershell
python -m pip install -e .
python -m unittest discover -s tests -v
git status --short
git add --dry-run .
```

确认以下内容：

- `.venv/`、`data/logistics_demo.duckdb`、`data/parquet/*.parquet` 和密钥文件没有进入提交清单。
- `data/templates/物流营运演示数据.xlsx` 在提交清单中。
- Excel 只包含模拟数据，不包含个人信息或生产系统标识。
- 已根据发布目的选择许可证；未选择许可证时，默认不授予他人复制、修改或分发权利。

## 首次推送

在 GitHub 新建一个空仓库，不要让网页端额外创建 README、`.gitignore` 或许可证，以避免首次推送冲突。然后在本地执行：

```powershell
git add .
git commit -m "feat: publish logistics operations demo"
git remote add origin https://github.com/<your-account>/<repository>.git
git push -u origin main
```

将占位地址替换成实际仓库地址。如果已经配置 `origin`，不要重复执行 `git remote add`。

## 托管运行参数

对于支持 Streamlit 的部署平台：

- Python：3.11 或 3.12。
- 应用入口：`streamlit_app.py`。
- 安装方式：`python -m pip install -r requirements.txt` 或 `python -m pip install .`。
- 首次启动会在临时文件系统生成内置 DuckDB；上传的 Excel 也只保存在会话级临时目录。

公开部署前建议设置上传大小、并发会话和临时磁盘配额，并明确禁止上传真实个人信息。
