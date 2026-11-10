import json, os
from pathlib import Path
old_path=Path(os.getenv('OLD_DATA','data.before.json'))
new_path=Path(os.getenv('NEW_DATA','data.json'))
old=json.loads(old_path.read_text(encoding='utf-8')) if old_path.exists() else {'items':[]}
new=json.loads(new_path.read_text(encoding='utf-8')) if new_path.exists() else {'items':[]}
def key(x): return x.get('id') or x.get('originalUrl') or (x.get('title','')+'|'+x.get('datePublished',''))
old_keys={key(x) for x in old.get('items',[])}
added=[x for x in new.get('items',[]) if key(x) not in old_keys]
order={'高':0,'中高':1,'中':2}
added.sort(key=lambda x:(order.get(x.get('impactLevel'),9),x.get('datePublished','')), reverse=False)
notify=[x for x in added if x.get('impactLevel') in ('高','中高')]
Path('new_items.json').write_text(json.dumps({'items':added},ensure_ascii=False,indent=2),encoding='utf-8')
Path('notify_items.json').write_text(json.dumps({'items':notify},ensure_ascii=False,indent=2),encoding='utf-8')
with open(os.environ['GITHUB_OUTPUT'],'a',encoding='utf-8') as f:
 f.write(f"new_count={len(added)}\nnotify_count={len(notify)}\nhigh_count={sum(x.get('impactLevel')=='高' for x in notify)}\n")
