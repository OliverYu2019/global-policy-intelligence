# -*- coding: utf-8 -*-
"""对比采集前后的 data.json，找出真正新增、且未推送过的高/中高影响条目"""
import json, os
from pathlib import Path
load = lambda p: json.loads(Path(p).read_text(encoding='utf-8')) if Path(p).exists() else {}
old, new = load('data.before.json'), load('data.json')
sent = set(load('notified.json').get('ids', []))
old_ids = {x['id'] for x in old.get('items', [])}
added = [x for x in new.get('items', []) if x['id'] not in old_ids]
rank = {'高': 0, '中高': 1, '中': 2}
notify = sorted([x for x in added if x.get('impactLevel') in ('高', '中高') and x['id'] not in sent],
                key=lambda x: (rank.get(x.get('impactLevel'), 9), -(int((x.get('datePublished') or '0').replace('-', '') or 0))))
Path('notify_items.json').write_text(json.dumps({'items': notify}, ensure_ascii=False, indent=1), encoding='utf-8')
if notify:
    ids = (list(sent) + [x['id'] for x in notify])[-3000:]
    Path('notified.json').write_text(json.dumps({'ids': ids}, ensure_ascii=False), encoding='utf-8')
out = os.environ.get('GITHUB_OUTPUT')
line = f"new_count={len(added)}\nnotify_count={len(notify)}\n"
if out:
    open(out, 'a', encoding='utf-8').write(line)
print(line)
