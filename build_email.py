import json, os, html
from pathlib import Path
j=json.loads(Path('notify_items.json').read_text(encoding='utf-8')); xs=j.get('items',[])
dash=os.getenv('DASHBOARD_URL','#')
rows=[]
for x in xs[:20]:
 color='#b42318' if x.get('impactLevel')=='高' else '#b54708'
 rows.append(f'''<tr><td style="padding:14px 0;border-bottom:1px solid #e7ebef"><div style="font-size:12px;color:#667788">{html.escape(x.get('datePublished',''))} · {html.escape(x.get('jurisdiction',''))} · {html.escape(x.get('authority',''))}</div><div style="font-size:16px;font-weight:700;margin:4px 0"><a style="color:#164d73" href="{html.escape(x.get('originalUrl','#'))}">{html.escape(x.get('title',''))}</a></div><span style="background:{color};color:#fff;border-radius:10px;padding:2px 8px;font-size:12px">{html.escape(x.get('impactLevel',''))}</span> <b>{html.escape(x.get('topic',''))}</b><div style="margin-top:8px">{html.escape(x.get('companyImpact') or x.get('summary',''))}</div></td></tr>''')
subject=f"全球政策情报：{len(xs)}条中高/高影响新增"
body=f'''<html><body style="font-family:Arial,'Microsoft YaHei';color:#1d2d3b"><div style="max-width:760px;margin:auto"><h2 style="color:#0d2438">全球政策与市场情报更新</h2><p>本轮发现 <b>{len(xs)}</b> 条需要关注的中高/高影响新增。</p><table style="width:100%;border-collapse:collapse">{''.join(rows)}</table><p style="margin-top:22px"><a href="{html.escape(dash)}" style="background:#155f92;color:#fff;padding:10px 16px;border-radius:7px;text-decoration:none">打开完整看板</a></p><p style="font-size:12px;color:#778899">此邮件由 GitHub Actions 自动生成。普通影响事项仅进入看板，不发送提醒。</p></div></body></html>'''
Path('email_subject.txt').write_text(subject,encoding='utf-8');Path('email_body.html').write_text(body,encoding='utf-8')
