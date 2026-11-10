# IFRS/HKFRS 自动采集看板

## 本地运行
```bash
pip install -r requirements.txt
python collector.py
python -m http.server 8000
```
访问 http://localhost:8000

## GitHub Pages 自动运行
1. 新建 GitHub 仓库，把本目录全部文件上传到仓库根目录。
2. 在 Actions 页允许工作流运行。`update.yml` 每天北京时间 08:00 采集一次。
3. Settings > Pages > Deploy from a branch，选择 `main` 和 `/root`。
4. 首次在 Actions 中手工运行 `Update IFRS dashboard`，随后访问 Pages 地址。

## 数据层
`data.json` 是统一数据层。采集失败时保留历史数据，并在 `errors` 字段记录来源错误。

## 生产注意
网站结构、robots.txt、使用条款或反爬策略变化时，采集器可能需要调整。请控制频率并仅保存标题、摘要、日期和原文链接，不复制受版权保护的完整文章。
