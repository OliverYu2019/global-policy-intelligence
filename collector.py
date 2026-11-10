# -*- coding: utf-8 -*-
"""全球政策与市场情报采集器
1. 从各来源列表页发现条目，只保留"文章级"链接（排除首页、栏目页、导航页）
2. 打开文章页，提取规范链接(canonical)、标题、发布日期和正文要点
3. 生成解读：配置了大模型密钥则用AI深度解读，否则使用分主题规则库
4. 与历史数据合并去重，保留最近120天、最多800条
"""
import json, re, hashlib, datetime, time, os
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse
import requests
from bs4 import BeautifulSoup
from knowledge import kb

ROOT = Path(__file__).parent
CFG = json.loads((ROOT / 'sources.json').read_text(encoding='utf-8'))
HEAD = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8'}
NOW = datetime.datetime.now(datetime.timezone.utc)
MAX_NEW_PER_SOURCE = int(os.getenv('MAX_NEW_PER_SOURCE', '8'))
MAX_LLM_PER_RUN = int(os.getenv('MAX_LLM_PER_RUN', '20'))
MAX_AGE_DAYS = int(os.getenv('MAX_AGE_DAYS', '60'))
LLM_KEY = os.getenv('LLM_API_KEY', '').strip()
LLM_BASE = os.getenv('LLM_BASE_URL', 'https://api.openai.com/v1').strip().rstrip('/')
LLM_MODEL = os.getenv('LLM_MODEL', 'gpt-4o-mini').strip()

KEY = re.compile(r'水|water|climate|气候|carbon|碳|waste|废|air quality|大气|空气|pollut|污染|biodivers|生物多样性|nature|自然|deforest|毁林|forest|森林|human rights|人权|forced labo|强迫劳动|labou?r|劳动|antitrust|competition|反垄断|竞争|垄断|merger|acquisition|并购|收购|集中|supply chain|供应链|critical (raw )?material|关键|trade|贸易|tariff|关税|sanction|制裁|export control|出口管制|central bank|interest rate|利率|货币政策|monetary|inflation|通胀|IFRS|HKFRS|ESG|可持续|sustainab|emission|排放|PFAS|chemical|化学品|plastic|塑料|packag|包装|methane|甲烷|ETS|CBAM|EUDR|CSDDD|CSRD', re.I)
NAV_WORDS = re.compile(r'(^|/)(about|contact|login|signin|register|search|sitemap|privacy|cookie|subscribe|careers|jobs|help|faq|rss|feed|tag|tags|category|topics?|index(_\d+)?\.s?html?|default\.aspx|home)(/|$)', re.I)
LISTING = re.compile(r'^(news|news_en|newsroom|press|press-?room|press-?releases?|press_releases?|releases|media|latest|updates|publications|speeches|statements|events|all|list|index[_\d]*\.s?html?|zcwj|zcfb|hjywnews)$', re.I)
ARTICLE_HINT = re.compile(r'(19|20)\d{2}[/_-]?\d{2}|t20\d{6}|/\d{5,}|\.s?html?$|\.pdf$|/(news|press|pr|article|articles|story|stories|speech|statement|release|releases|publication|publications|detail|content)[/_-]', re.I)
DATE_PATS = [
    (re.compile(r'(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})'), 'ymd'),
    (re.compile(r'(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+(20\d{2})', re.I), 'dmy'),
    (re.compile(r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+(\d{1,2}),?\s+(20\d{2})', re.I), 'mdy'),
]
MON = {m: i for i, m in enumerate(['jan','feb','mar','apr','may','jun','jul','aug','sep','oct','nov','dec'], 1)}

# ---------------- 工具函数 ----------------
def norm_url(u):
    p = urlparse(u)
    q = '&'.join(x for x in p.query.split('&') if x and not re.match(r'(utm_|spm|from=|share)', x))
    return urlunparse((p.scheme, p.netloc.lower(), p.path, '', q, ''))

def find_date(s):
    for pat, kind in DATE_PATS:
        m = pat.search(s or '')
        if not m:
            continue
        try:
            if kind == 'ymd': y, mo, d = map(int, m.groups())
            elif kind == 'dmy': d, mo, y = int(m.group(1)), MON[m.group(2)[:3].lower()], int(m.group(3))
            else: mo, d, y = MON[m.group(1)[:3].lower()], int(m.group(2)), int(m.group(3))
            dt = datetime.date(y, mo, d)
            if dt.year >= 2015 and dt <= NOW.date() + datetime.timedelta(days=2):
                return dt.isoformat()
        except Exception:
            pass
    return ''

def is_article_url(url, source_url):
    """判断是否为文章级链接：排除首页、栏目页、导航页、锚点和外站社交链接"""
    p, s = urlparse(url), urlparse(source_url)
    if p.scheme not in ('http', 'https'): return False
    path = p.path.rstrip('/')
    if not path or path.lower() in ('/index', '/index.html', '/index.htm', '/index.shtml', '/default.aspx', '/en', '/zh', '/cn'): return False
    if norm_url(url).rstrip('/') == norm_url(source_url).rstrip('/'): return False
    if NAV_WORDS.search(path): return False
    if re.search(r'(facebook|twitter|x\.com|linkedin|weibo|youtube|instagram|wechat)', p.netloc, re.I): return False
    root = lambda n: '.'.join(n.split('.')[-2:])
    if root(p.netloc) != root(s.netloc): return False
    segs = [x for x in path.split('/') if x]
    if LISTING.match(segs[-1]): return False
    if len(segs) < 2 and not ARTICLE_HINT.search(path): return False
    return bool(ARTICLE_HINT.search(path)) or len(segs) >= 3

def fetch(url, timeout=30):
    r = requests.get(url, headers=HEAD, timeout=timeout)
    r.raise_for_status()
    if not r.encoding or r.encoding.lower() == 'iso-8859-1':
        r.encoding = r.apparent_encoding
    return r

def meta(soup, *names):
    for n in names:
        t = soup.find('meta', attrs={'property': n}) or soup.find('meta', attrs={'name': n})
        if t and t.get('content'): return t['content'].strip()
    return ''

def read_article(url):
    """打开文章页：返回规范链接、标题、日期、正文节选"""
    if url.lower().endswith('.pdf'):
        return {'url': url, 'title': '', 'date': find_date(url), 'text': ''}
    r = fetch(url)
    soup = BeautifulSoup(r.text, 'html.parser')
    canon = ''
    c = soup.find('link', rel=lambda v: v and 'canonical' in v)
    if c and c.get('href'): canon = urljoin(r.url, c['href'])
    canon = canon or meta(soup, 'og:url') or r.url
    if not is_article_url(canon, url):  # canonical 指向首页时，保留实际文章地址
        canon = r.url
    title = meta(soup, 'og:title', 'ArticleTitle', 'twitter:title') or (soup.h1.get_text(' ', strip=True) if soup.h1 else '')
    date = find_date(meta(soup, 'article:published_time', 'PubDate', 'pubdate', 'publishdate', 'dc.date', 'DC.date.issued', 'date', 'citation_publication_date'))
    for t in soup(['script', 'style', 'nav', 'header', 'footer', 'aside', 'form']): t.decompose()
    paras = [p.get_text(' ', strip=True) for p in soup.find_all(['p', 'li']) if len(p.get_text(strip=True)) > 30]
    text = ' '.join(paras)[:6000]
    if not date: date = find_date(text[:800]) or find_date(r.url)
    desc = meta(soup, 'description', 'og:description')
    return {'url': norm_url(canon), 'title': title, 'date': date, 'text': text, 'desc': desc}

# ---------------- 分类 ----------------
def classify(title, text):
    s = (title + ' ' + text[:1500]).lower()
    rules = [
        ('热点新闻', '并购交易', r'merger|acquisition|takeover|divest|并购|收购|经营者集中|剥离'),
        ('热点新闻', '央行与市场', r'central bank|interest rate|monetary policy|federal reserve|fomc|ecb|央行|利率|货币政策|加息|降息'),
        ('政策法规', '反垄断', r'antitrust|competition law|cartel|abuse of dominance|反垄断|垄断协议|滥用市场支配'),
        ('政策法规', '人权', r'human rights|forced labo|child labo|due diligence directive|csddd|人权|强迫劳动|童工'),
        ('政策法规', '零毁林', r'deforestation|eudr|毁林'),
        ('政策法规', '生物多样性', r'biodiversity|nature-related|nature restoration|cop17|生物多样性|自然相关|生态保护修复'),
        ('政策法规', '废弃物', r'waste|recycl|circular|packaging|ppwr|epr|plastic|废弃物|固体废物|固废|循环经济|包装|塑料|回收'),
        ('政策法规', '水', r'water|wastewater|river|marine|ocean|水污染|污水|河湖|流域|长江|黄河|地下水|饮用水|海洋'),
        ('政策法规', '大气污染物', r'air quality|air pollut|clean air|nox|so2|pm2\.5|voc|ozone|hfc|大气|空气质量|颗粒物|臭氧|消耗臭氧层'),
        ('政策法规', '供应链安全', r'export control|critical raw|critical mineral|supply chain|sanction|investment screening|economic security|出口管制|关键原材料|供应链|制裁|安全审查|实体清单'),
        ('热点新闻', '全球与贸易', r'trade|tariff|anti-dumping|countervailing|wto|贸易|关税|反倾销|反补贴'),
        ('政策法规', '财务准则', r'ifrs|hkfrs|ias |issb|会计准则|财务报告准则'),
        ('政策法规', '气候', r'climate|carbon|emission|greenhouse|ets|cbam|methane|net zero|气候|碳|温室气体|排放|甲烷|减排'),
    ]
    for sec, top, pat in rules:
        if re.search(pat, s): return sec, top
    return '热点新闻', '全球与贸易'

def status_of(s):
    s = s.lower()
    if re.search(r'consultation|call for evidence|draft|proposal|proposed rule|征求意见|草案|公开征求', s): return '征求意见/提案'
    if re.search(r'enter(s|ed)? into force|effective|施行|生效|起实施', s): return '已生效/即将生效'
    if re.search(r'adopt|final rule|approved|通过|印发|发布.*(办法|规定|条例|方案)', s): return '已通过/发布'
    if re.search(r'fine|penalt|settlement|lawsuit|sue|处罚|罚款|立案|执法', s): return '执法/诉讼'
    return '动态'

def impact_of(section, topic, status, s):
    s = s.lower()
    if status in ('已生效/即将生效', '已通过/发布', '执法/诉讼') and section == '政策法规': return '高'
    if re.search(r'regulation|directive|law|act|条例|法典|法律|规定|办法|标准', s) and section == '政策法规': return '高'
    if topic in ('央行与市场', '并购交易') and re.search(r'hike|raise|cut|加息|降息|block|prohibit|divest|剥离|禁止', s): return '高'
    if status == '征求意见/提案': return '中高'
    return '中高' if section == '政策法规' else '中'

# ---------------- 解读 ----------------
def fact_sentences(text, n=4):
    """抽取含数字、日期或义务性措辞的句子，作为关键要点(短句，避免长篇引用)"""
    sents = re.split(r'(?<=[。；;.!?！？])\s*', text or '')
    good = []
    for x in sents:
        x = x.strip()
        if 18 <= len(x) <= 140 and re.search(r'\d|shall|must|required|will apply|应当|必须|不得|要求|目标|到20\d\d', x):
            if x not in good: good.append(x)
        if len(good) >= n: break
    return good

def rule_analysis(item, art):
    k = kb(item['topic'])
    facts = fact_sentences(art.get('text', ''))
    one = art.get('desc') or (facts[0] if facts else '') or item['title']
    return {
        'oneLiner': one[:120],
        'keyPoints': facts or ['原文要点待提取，请点击原文查看。'],
        'professionalAnalysis': k['mechanism'],
        'plainLanguage': f"简单说：这是{item['jurisdiction']}在“{item['topic']}”领域的一项{item['legalStatus']}，可能改变相关企业的合规义务、经营成本或市场预期。",
        'hkImpact': k['hk'],
        'industries': k['industries'],
        'actions': k['actions'],
        'watchItems': k['watch'],
        'analysisMethod': '规则生成',
    }

PROMPT = """你是服务于香港上市公司CFO和董事会的资深政策与ESG分析师。仅依据下面提供的原文信息进行分析；原文没有的信息写“原文未披露”，不得编造数字、日期或机构名称。
输出严格的JSON对象，字段如下（全部用简体中文）：
oneLiner: 一句话结论(≤60字)
keyPoints: 3-5条关键事实(每条≤60字，含具体数字/日期/适用范围)
professionalAnalysis: 专业解读(150-300字：法律性质与阶段、适用范围、核心机制、时间表、与既有规则关系)
plainLanguage: 通俗解读(≤100字)
hkImpact: 对港股上市公司及中资企业的影响(120-250字：受影响业务、财务报表科目、披露要求、成本或机会)
industries: 受影响行业数组(3-6个)
actions: 建议动作数组(3-5条，具体可执行，带对象和动作)
watchItems: 后续关注事项数组(2-4条)
impactLevel: 高/中高/中 之一
"""

def llm_analysis(item, art):
    body = f"标题：{item['title']}\n地区：{item['jurisdiction']}\n机构：{item['authority']}\n日期：{item['datePublished']}\n主题：{item['topic']}\n原文节选：{(art.get('desc') or '')} {art.get('text', '')[:4500]}"
    r = requests.post(f"{LLM_BASE}/chat/completions", timeout=90,
                      headers={'Authorization': f'Bearer {LLM_KEY}', 'Content-Type': 'application/json'},
                      json={'model': LLM_MODEL, 'temperature': 0.2,
                            'response_format': {'type': 'json_object'},
                            'messages': [{'role': 'system', 'content': PROMPT}, {'role': 'user', 'content': body}]})
    r.raise_for_status()
    content = r.json()['choices'][0]['message']['content']
    m = re.search(r'\{.*\}', content, re.S)
    data = json.loads(m.group(0) if m else content)
    out = {k: data.get(k) for k in ['oneLiner', 'keyPoints', 'professionalAnalysis', 'plainLanguage', 'hkImpact', 'industries', 'actions', 'watchItems']}
    if data.get('impactLevel') in ('高', '中高', '中'): out['impactLevel'] = data['impactLevel']
    out['analysisMethod'] = 'AI生成'
    return out

# ---------------- 主流程 ----------------
def discover(src):
    r = fetch(src['url'])
    soup = BeautifulSoup(r.text, 'html.parser')
    found = []
    for a in soup.select('a[href]'):
        title = ' '.join(a.get_text(' ', strip=True).split())
        if len(title) < 12 or len(title) > 240 or not KEY.search(title): continue
        url = norm_url(urljoin(r.url, a['href']))
        if not is_article_url(url, src['url']): continue
        box = a.find_parent(['li', 'article', 'tr', 'div']) or a.parent
        ctx = ' '.join(box.get_text(' ', strip=True).split())[:400] if box else ''
        found.append({'title': title, 'url': url, 'listDate': find_date(ctx) or find_date(url)})
    uniq = {x['url']: x for x in found}
    return list(uniq.values())

def main():
    p = ROOT / 'data.json'
    old = json.loads(p.read_text(encoding='utf-8')) if p.exists() else {'items': []}
    items = {x['id']: x for x in old.get('items', [])}
    known_urls = {x.get('originalUrl') for x in items.values()}
    errors, llm_used, added = [], 0, 0
    for src in CFG['sources']:
        try:
            cands = [c for c in discover(src) if c['url'] not in known_urls][:MAX_NEW_PER_SOURCE * 2]
        except Exception as e:
            errors.append({'source': src['name'], 'error': str(e)[:160]}); continue
        n = 0
        for c in cands:
            if n >= MAX_NEW_PER_SOURCE: break
            try:
                art = read_article(c['url'])
            except Exception as e:
                errors.append({'source': src['name'], 'error': f"文章读取失败 {c['url'][:80]}: {str(e)[:80]}"}); continue
            date = art['date'] or c['listDate']
            if date and (NOW.date() - datetime.date.fromisoformat(date)).days > MAX_AGE_DAYS: continue
            if art['url'] in known_urls: continue
            title = c['title'] if len(c['title']) >= len(art['title'] or '') * 0.5 else art['title']
            section, topic = classify(title, art['text'])
            status = status_of(title + ' ' + art['text'][:1500])
            item = {
                'id': hashlib.sha1(art['url'].encode()).hexdigest()[:16], 'title': title, 'datePublished': date,
                'jurisdiction': src['region'], 'authority': src['name'], 'sourceType': src['type'], 'sourceTier': src['tier'],
                'section': section, 'topic': topic, 'legalStatus': status,
                'impactLevel': impact_of(section, topic, status, title + ' ' + art['text'][:1500]),
                'originalUrl': art['url'], 'linkVerified': True, 'collectedAt': NOW.isoformat(),
            }
            analysis = None
            if LLM_KEY and llm_used < MAX_LLM_PER_RUN and (art['text'] or art.get('desc')):
                try:
                    analysis = llm_analysis(item, art); llm_used += 1
                except Exception as e:
                    errors.append({'source': 'AI解读', 'error': str(e)[:160]})
            item.update(analysis or rule_analysis(item, art))
            items[item['id']] = item; known_urls.add(art['url']); n += 1; added += 1
            time.sleep(0.5)
    cutoff = (NOW.date() - datetime.timedelta(days=120)).isoformat()
    keep = [x for x in items.values() if x.get('pinned') or not x.get('datePublished') or x['datePublished'] >= cutoff]
    keep.sort(key=lambda x: (x.get('datePublished') or ''), reverse=True)
    keep = keep[:800]
    p.write_text(json.dumps({'generatedAt': NOW.isoformat(), 'count': len(keep), 'errors': errors, 'items': keep},
                            ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps({'added': added, 'total': len(keep), 'ai': llm_used, 'errors': len(errors)}, ensure_ascii=False))

if __name__ == '__main__':
    main()
