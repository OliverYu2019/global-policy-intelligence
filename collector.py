import json,re,hashlib,datetime,time
from pathlib import Path
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup
ROOT=Path(__file__).parent
CFG=json.loads((ROOT/'sources.json').read_text(encoding='utf-8'))
HEAD={'User-Agent':'Mozilla/5.0 (compatible; GlobalPolicyDashboard/1.0; +https://github.com/)'}
KEY=re.compile(r'水|water|climate|气候|carbon|碳|waste|废弃|air|大气|pollution|污染|biodiversity|生物多样性|deforestation|毁林|human rights|人权|labor|劳动|antitrust|competition|反垄断|供应链|supply chain|trade|贸易|merger|acquisition|并购|央行|central bank|interest rate|利率|IFRS|HKFRS|ESG|sanction|制裁|export control|出口管制',re.I)
DATE=[re.compile(r'(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})'),re.compile(r'(\d{1,2})\s+(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+(20\d{2})',re.I)]
MON={m.lower()[:3]:i for i,m in enumerate('January February March April May June July August September October November December'.split(),1)}
def getdate(s):
 for i,p in enumerate(DATE):
  m=p.search(s)
  if not m: continue
  try:
   if i==0:y,mo,d=map(int,m.groups())
   else:d,mo,y=m.groups();y,d=int(y),int(d);mo=MON[mo.lower()[:3]]
   return f'{y:04d}-{mo:02d}-{d:02d}'
  except: pass
 return ''
def classify(title,summary):
 s=(title+' '+summary).lower()
 if re.search(r'merger|acquisition|takeover|并购|收购|交易',s): section='热点新闻'; topic='并购交易'
 elif re.search(r'central bank|interest rate|央行|利率|货币政策|federal reserve|ecb',s): section='热点新闻'; topic='央行与市场'
 elif re.search(r'trade|tariff|贸易|关税|sanction|制裁|export control|出口管制',s): section='热点新闻'; topic='全球与贸易'
 elif re.search(r'antitrust|competition|反垄断|竞争',s): section='政策法规'; topic='反垄断'
 elif re.search(r'human rights|forced labor|人权|强迫劳动|童工|劳动',s): section='政策法规'; topic='人权'
 elif re.search(r'deforestation|毁林|eudr|forest',s): section='政策法规'; topic='零毁林'
 elif re.search(r'biodiversity|nature|生物多样性|自然',s): section='政策法规'; topic='生物多样性'
 elif re.search(r'waste|recycl|废弃|循环经济|包装',s): section='政策法规'; topic='废弃物'
 elif re.search(r'water|wastewater|水污染|污水|河湖|地下水',s): section='政策法规'; topic='水'
 elif re.search(r'air|pm2|so2|nox|voc|大气|空气|工业排放',s): section='政策法规'; topic='大气污染物'
 elif re.search(r'supply chain|critical mineral|供应链|关键矿产',s): section='政策法规'; topic='供应链安全'
 else: section='政策法规'; topic='气候'
 impact='高' if re.search(r'final rule|regulation|directive|正式|通过|生效|强制|enforcement|处罚|制裁',s) else ('中高' if re.search(r'proposal|consultation|draft|征求意见|草案|计划|审议',s) else '中')
 return section,topic,impact
def collect(src):
 r=requests.get(src['url'],headers=HEAD,timeout=35);r.raise_for_status();soup=BeautifulSoup(r.text,'html.parser');out=[]
 for a in soup.select('a[href]'):
  title=' '.join(a.get_text(' ',strip=True).split())
  if len(title)<12 or len(title)>220 or not KEY.search(title):continue
  box=a.find_parent(['article','li','tr','section','div']) or a.parent
  text=' '.join(box.get_text(' ',strip=True).split()) if box else title
  url=urljoin(src['url'],a.get('href'));section,topic,impact=classify(title,text)
  out.append({'id':hashlib.sha1((src['id']+'|'+url+'|'+title).encode()).hexdigest()[:16],'title':title,'datePublished':getdate(text),'jurisdiction':src['region'],'authority':src['name'],'sourceType':src['type'],'sourceTier':src['tier'],'section':section,'topic':topic,'legalStatus':'征求意见' if re.search(r'consultation|proposal|draft|征求意见|草案',text,re.I) else '动态','impactLevel':impact,'summary':text[:480],'professionalSummary':'关注适用范围、法律状态、生效时间、责任主体及执行机制。','plainLanguage':'该事项可能改变企业的合规义务、经营成本、供应链或市场预期。','companyImpact':'建议相关企业识别适用业务、合同和供应商，评估数据与流程差距，并跟踪后续正式文本。','originalUrl':url,'lastChecked':datetime.datetime.now(datetime.timezone.utc).isoformat()})
 seen={x['originalUrl']:x for x in out};return list(seen.values())[:120]
def main():
 p=ROOT/'data.json';old=[]
 if p.exists():
  try:old=json.loads(p.read_text(encoding='utf-8')).get('items',[])
  except:pass
 allx=list(old);errors=[]
 for src in CFG['sources']:
  try:allx+=collect(src)
  except Exception as e:errors.append({'source':src['name'],'error':str(e)[:180]})
  time.sleep(.4)
 unique={x.get('id') or x.get('originalUrl'):x for x in allx}
 items=sorted(unique.values(),key=lambda x:x.get('datePublished',''),reverse=True)
 p.write_text(json.dumps({'generatedAt':datetime.datetime.now(datetime.timezone.utc).isoformat(),'count':len(items),'errors':errors,'items':items},ensure_ascii=False,indent=2),encoding='utf-8')
 print({'count':len(items),'errors':len(errors)})
if __name__=='__main__':main()
