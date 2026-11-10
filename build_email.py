# -*- coding: utf-8 -*-
"""生成适合手机阅读的提醒邮件：先给本轮小结，再逐条给出结论、要点、影响和动作"""
import json, os, html, re, requests
from pathlib import Path
from collections import Counter

items = json.loads(Path('notify_items.json').read_text(encoding='utf-8')).get('items', [])
DASH = os.getenv('DASHBOARD_URL', '').strip()
KEY, BASE, MODEL = os.getenv('LLM_API_KEY', '').strip(), os.getenv('LLM_BASE_URL', 'https://api.openai.com/v1').rstrip('/'), os.getenv('LLM_MODEL', 'gpt-4o-mini')
e = lambda s: html.escape(str(s or ''))

def ai_summary(xs):
    if not KEY: return None
    brief = '\n'.join(f"- [{x.get('jurisdiction')}/{x.get('topic')}/{x.get('impactLevel')}] {x.get('title')}：{x.get('oneLiner')}" for x in xs[:20])
    try:
        r = requests.post(f'{BASE}/chat/completions', timeout=60, headers={'Authorization': f'Bearer {KEY}'},
            json={'model': MODEL, 'temperature': 0.2, 'messages': [
                {'role': 'system', 'content': '你是港股上市公司CFO的政策顾问。基于给定条目写本轮小结：3-4条要点，每条≤50字，先说趋势再说对港股/中资企业意味着什么。只输出要点，每行一条，不要编号以外的内容，不得编造条目中没有的信息。'},
                {'role': 'user', 'content': brief}]})
        r.raise_for_status()
        lines = [re.sub(r'^[\s\-•\d\.、]+', '', l).strip() for l in r.json()['choices'][0]['message']['content'].splitlines()]
        return [l for l in lines if l][:4]
    except Exception:
        return None

def rule_summary(xs):
    reg = Counter(x.get('jurisdiction') for x in xs).most_common(3)
    top = Counter(x.get('topic') for x in xs).most_common(3)
    high = [x for x in xs if x.get('impactLevel') == '高']
    pts = [f"本轮新增{len(xs)}条需关注动态，其中高影响{len(high)}条；集中在" + '、'.join(f'{k}（{v}）' for k, v in top) + '。',
           '涉及地区：' + '、'.join(f'{k}{v}条' for k, v in reg) + '。']
    for x in (high or xs)[:2]:
        pts.append(f"{x.get('jurisdiction')}·{x.get('topic')}：{x.get('oneLiner') or x.get('title')}")
    return pts

summary = ai_summary(items) or rule_summary(items)
n_high = sum(x.get('impactLevel') == '高' for x in items)
first = (items[0].get('title') or '') if items else ''
first = first if len(first) <= 28 else first[:27] + '…'
subject = (f"【政策情报】{len(items)}条新增" + (f"（{n_high}条高影响）" if n_high else '') + f"：{first}") if items else '【政策情报】无新增'

def card(x):
    color = '#b42318' if x.get('impactLevel') == '高' else '#b54708'
    kp = ''.join(f'<li style="margin:0 0 4px">{e(k)}</li>' for k in (x.get('keyPoints') or [])[:3])
    acts = ''.join(f'<li style="margin:0 0 4px">{e(a)}</li>' for a in (x.get('actions') or [])[:3])
    hk = x.get('hkImpact') or ''
    hk = hk if len(hk) <= 220 else hk[:218] + '…'
    detail = f'{DASH}#{x["id"]}' if DASH else ''
    links = f'<a href="{e(x.get("originalUrl"))}" style="color:#155f92">阅读原文</a>'
    if detail: links += f'　|　<a href="{e(detail)}" style="color:#155f92">看板完整解读</a>'
    return f'''<div style="border:1px solid #e3e8ee;border-radius:10px;padding:14px;margin:0 0 14px;background:#fff">
<div style="font-size:12px;color:#667788;margin-bottom:6px"><span style="background:{color};color:#fff;border-radius:9px;padding:1px 7px;font-weight:bold">{e(x.get('impactLevel'))}</span>
 {e(x.get('jurisdiction'))} · {e(x.get('topic'))} · {e(x.get('authority'))} · {e(x.get('datePublished'))}</div>
<div style="font-size:16px;font-weight:bold;line-height:1.45;margin-bottom:8px;color:#0d2438">{e(x.get('title'))}</div>
<div style="background:#f3f7fb;border-left:3px solid #155f92;padding:8px 10px;font-size:14px;margin-bottom:8px"><b>结论：</b>{e(x.get('oneLiner'))}</div>
{'<div style="font-size:13px;font-weight:bold;margin:8px 0 4px">关键要点</div><ul style="padding-left:18px;margin:0;font-size:14px;line-height:1.55">' + kp + '</ul>' if kp else ''}
<div style="font-size:13px;font-weight:bold;margin:10px 0 4px">对港股/中资企业的影响</div><div style="font-size:14px;line-height:1.6">{e(hk)}</div>
{'<div style="font-size:13px;font-weight:bold;margin:10px 0 4px">建议动作</div><ul style="padding-left:18px;margin:0;font-size:14px;line-height:1.55">' + acts + '</ul>' if acts else ''}
<div style="font-size:13px;margin-top:10px">{links}</div></div>'''

bul = ''.join(f'<li style="margin:0 0 6px">{e(s)}</li>' for s in summary)
btn = f'<div style="text-align:center;margin:18px 0"><a href="{e(DASH)}" style="background:#155f92;color:#fff;padding:11px 22px;border-radius:8px;text-decoration:none;font-weight:bold">打开情报看板</a></div>' if DASH else ''
body = f'''<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;background:#f3f6f9;font-family:-apple-system,'PingFang SC','Microsoft YaHei',Arial,sans-serif;color:#1d2d3b">
<div style="max-width:600px;margin:0 auto;padding:14px">
<div style="background:#0d2438;color:#fff;border-radius:10px;padding:16px;margin-bottom:14px">
<div style="font-size:12px;opacity:.75">GLOBAL POLICY INTELLIGENCE</div><div style="font-size:19px;font-weight:bold;margin-top:4px">全球政策与市场情报更新</div></div>
<div style="background:#fff;border-radius:10px;padding:14px;margin-bottom:14px;border:1px solid #e3e8ee">
<div style="font-size:15px;font-weight:bold;margin-bottom:8px">本轮小结</div><ul style="padding-left:18px;margin:0;font-size:14px;line-height:1.6">{bul}</ul></div>
{''.join(card(x) for x in items[:12])}
{f'<p style="font-size:13px;color:#667788">另有{len(items)-12}条，请在看板查看。</p>' if len(items) > 12 else ''}
{btn}
<p style="font-size:11px;color:#8899aa;line-height:1.5">仅推送新增的“高/中高”影响事项；“中”影响事项只进入看板。解读用于内部参考，不构成法律或会计意见，正式判断请以原文为准。</p>
</div></body></html>'''
Path('email_subject.txt').write_text(subject, encoding='utf-8')
Path('email_body.html').write_text(body, encoding='utf-8')
print(subject)
