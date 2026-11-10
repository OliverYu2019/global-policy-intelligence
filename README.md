# 全球政策、可持续发展与市场情报看板

## GitHub 部署
将本目录所有文件上传到仓库根目录并 Commit。GitHub Actions 工作流每小时运行一次，更新 `data.json`。GitHub Pages 选择 `main` 和 `/(root)`。

## 文件
- `index.html`：最终网页展示
- `data.json`：统一数据层
- `collector.py`：采集、分类和去重
- `sources.json`：数据源
- `.github/workflows/update.yml`：每小时更新

部分网站可能限制自动访问。采集失败时保留历史数据，并在 `data.json.errors` 中记录。正式合规结论应复核官方原文。
