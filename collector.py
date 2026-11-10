import json,re,hashlib,datetime,time
from pathlib import Path
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup
ROOT=Path(__file__).parent
CFG=json.loads((ROOT/'sources.json').read_text(encoding='utf-8'))
HEAD={'User-Agent':'Mozilla/5.0 IFRS-HKFRS-Dashboard/1.0'}
KEY=re.compile(r'IFRS|IASB|ISSB|HKFRS|HKICPA|HKEX|财务报告|可持续|气候|会计|上市规则|ESG|IFRIC|SASB|温室气体',re.I)
DATE_PAT=[re.compile(r'(20\d{2})年(\d{1,2})月(\d{1,2})日'),re.compile(r'(\d{1,2})\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(20\d{2})',re.I)]
MONTH={m:i for i,m in enumerate('January February March April May June July August September October November December'.split(),1)}
def date_of(s):
 for p in DATE_PAT:
  m=p.search(s)
  if m:
   try:
    if '年' in p.pattern:y,mo,d=map(int,m.groups())
    else:d,mon,y=m.groups(); y,d=int(y),int(d); mo=MONTH[mon.title()]
    return f'{y:04d}-{mo:02d}-{d:02d}'
   except: pass
 return ''
def enrich(x):
 s=(x['title']+' '+x.get('summary','')).lower()
 x['topic']='可持续披露' if re.search(r'issb|s1|s2|气候|可持续|nature|esg|ghg|sasb',s) else ('香港本地' if re.search(r'hkfrs|hkicpa|hkex|香港|上市规则',s) else '财务报告')
 x['impact']='高' if re.search(r'ifrs\s*18|hkfrs\s*18|ifrs\s*s[12]|hkfrs\s*s[12]|climate|气候|上市规则|ifrs\s*20',s) else ('中高' if re.search(r'ias\s*28|ifrs\s*16|ifrs\s*9|risk mitigation|风险缓释|nature|自然',s) else '中')
 x['professional']='重点核查发布阶段、适用范围、生效日期、过渡安排、确认计量及披露要求。'
 x['plain']='该动态可能改变报表呈现、管理层指标或投资者获得的信息，需要把技术要求转化为流程和数据。'
 x['hkImpact']='港股公司应先做适用性筛查；高影响项目建议由CFO牵头，联动审计委员会、审计师、ESG、系统及投资者关系团队。'
 return x
def collect(src):
 r=requests.get(src['url'],headers=HEAD,timeout=35); r.raise_for_status(); soup=BeautifulSoup(r.text,'html.parser'); out=[]
 for a in soup.select('a[href]'):
  title=' '.join(a.get_text(' ',strip=True).split())
  if len(title)<10 or len(title)>220 or not KEY.search(title): continue
  box=a.find_parent(['article','li','tr','div']) or a.parent
  text=' '.join(box.get_text(' ',strip=True).split()) if box else title
  href=urljoin(src['url'],a.get('href'))
  ident=hashlib.sha1((src['id']+'|'+href+'|'+title).encode()).hexdigest()[:16]
  out.append(enrich({'id':ident,'date':date_of(text),'title':title,'url':href,'summary':text[:500],'source':src['name'],'sourceId':src['id'],'collectedAt':datetime.datetime.now(datetime.timezone.utc).isoformat()}))
 # unique and cap noisy sources
 seen={};
 for x in out: seen[x['url']]=x
 return list(seen.values())[:150]
def main():
 prior=[]
 p=ROOT/'data.json'
 if p.exists():
  try: prior=json.loads(p.read_text(encoding='utf-8')).get('items',[])
  except: pass
 all_items=prior; errors=[]
 for s in CFG['sources']:
  try: all_items += collect(s)
  except Exception as e: errors.append({'source':s['name'],'error':str(e)})
  time.sleep(1)
 unique={x.get('id') or x.get('url'):x for x in all_items}
 items=sorted(unique.values(),key=lambda x:x.get('date',''),reverse=True)
 payload={'generatedAt':datetime.datetime.now(datetime.timezone.utc).isoformat(),'count':len(items),'errors':errors,'items':items}
 p.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps({'count':len(items),'errors':errors},ensure_ascii=False))
if __name__=='__main__': main()
