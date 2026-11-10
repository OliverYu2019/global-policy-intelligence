# -*- coding: utf-8 -*-
"""
Global Policy Intelligence Collector V4

目标：
1. 每篇原文逐条读取、逐条提取事实、逐条生成独立分析；
2. 取消“同主题共用一份解读”的模板式降级；
3. AI不可用时不生成套话，只保存事实并标记 analysisStatus=pending；
4. 支持重新分析历史数据：FORCE_REANALYZE=true；
5. 兼容现有 sources.json / data.json / GitHub Actions。

必需依赖：requests, beautifulsoup4
可选环境变量：
- LLM_API_KEY       OpenAI兼容API密钥
- LLM_BASE_URL      默认 https://api.openai.com/v1
- LLM_MODEL         默认 gpt-4o-mini，可替换为兼容模型
- FORCE_REANALYZE   true 时对现有条目重新生成分析
- MAX_NEW_PER_SOURCE 默认 8
- MAX_ANALYSIS_PER_RUN 默认 30
- MAX_AGE_DAYS      默认 45
"""

import datetime as dt
import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
SOURCES_FILE = ROOT / "sources.json"
DATA_FILE = ROOT / "data.json"
ERROR_FILE = ROOT / "collector_errors.json"

NOW = dt.datetime.now(dt.timezone.utc)
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/124 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

LLM_KEY = os.getenv("LLM_API_KEY", "").strip()
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini").strip()
FORCE_REANALYZE = os.getenv("FORCE_REANALYZE", "false").lower() == "true"
MAX_NEW_PER_SOURCE = int(os.getenv("MAX_NEW_PER_SOURCE", "8"))
MAX_ANALYSIS_PER_RUN = int(os.getenv("MAX_ANALYSIS_PER_RUN", "30"))
MAX_AGE_DAYS = int(os.getenv("MAX_AGE_DAYS", "45"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "35"))

KEYWORDS = re.compile(
    r"water|wastewater|climate|carbon|emission|methane|waste|recycl|packag|"
    r"air quality|pollut|biodivers|nature-related|deforest|forest|human rights|"
    r"forced labo|antitrust|competition|merger|acquisition|supply chain|trade|"
    r"tariff|sanction|export control|central bank|interest rate|inflation|IFRS|"
    r"HKFRS|ISSB|ESG|CBAM|EUDR|CSDDD|CSRD|PFAS|chemical|plastic|"
    r"水|污水|气候|碳|排放|甲烷|废弃物|固废|循环经济|包装|大气|空气|污染|"
    r"生物多样性|自然相关|毁林|森林|人权|强迫劳动|反垄断|竞争|并购|收购|"
    r"供应链|贸易|关税|制裁|出口管制|央行|利率|通胀|财务报告准则|可持续",
    re.I,
)

LISTING_BASENAME = re.compile(
    r"^(news|news_en|newsroom|press|press-room|press-releases?|releases|media|"
    r"latest|updates|publications|speeches|statements|events|all|list|index|"
    r"index_\d+\.s?html?|default\.aspx|zcwj|zcfb|hjywnews)$",
    re.I,
)

ARTICLE_HINT = re.compile(
    r"(19|20)\d{2}[/_-]?\d{2}|t20\d{6}|/\d{5,}|\.s?html?$|\.pdf$|"
    r"/(article|story|speech|statement|release|publication|detail|content)/",
    re.I,
)

DATE_PATTERNS = [
    (re.compile(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})"), "ymd"),
    (re.compile(r"(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+(20\d{2})", re.I), "dmy"),
    (re.compile(r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+(\d{1,2}),?\s+(20\d{2})", re.I), "mdy"),
]
MONTHS = {m: i for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}


# ---------- 基础工具 ----------

def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_url(url: str) -> str:
    p = urlparse(url)
    clean_query = "&".join(
        q for q in p.query.split("&")
        if q and not re.match(r"(?:utm_|spm=|from=|share=)", q, re.I)
    )
    return urlunparse((p.scheme, p.netloc.lower(), p.path, "", clean_query, ""))


def root_domain(host: str) -> str:
    parts = host.lower().split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host.lower()


def is_article_url(url: str, source_url: str) -> bool:
    p, src = urlparse(url), urlparse(source_url)
    if p.scheme not in ("http", "https"):
        return False
    if root_domain(p.netloc) != root_domain(src.netloc):
        return False
    path = p.path.rstrip("/")
    if not path or normalize_url(url).rstrip("/") == normalize_url(source_url).rstrip("/"):
        return False
    segments = [s for s in path.split("/") if s]
    if not segments or LISTING_BASENAME.match(segments[-1]):
        return False
    if re.search(r"/(about|contact|login|search|sitemap|privacy|cookie|careers|faq)(/|$)", path, re.I):
        return False
    return bool(ARTICLE_HINT.search(path)) or len(segments) >= 3


def parse_date(text: str) -> str:
    for pattern, kind in DATE_PATTERNS:
        m = pattern.search(text or "")
        if not m:
            continue
        try:
            if kind == "ymd":
                y, mo, day = map(int, m.groups())
            elif kind == "dmy":
                day, mon, y = int(m.group(1)), MONTHS[m.group(2)[:3].lower()], int(m.group(3))
                mo = mon
            else:
                mon, day, y = MONTHS[m.group(1)[:3].lower()], int(m.group(2)), int(m.group(3))
                mo = mon
            value = dt.date(y, mo, day)
            if dt.date(2015, 1, 1) <= value <= NOW.date() + dt.timedelta(days=2):
                return value.isoformat()
        except Exception:
            pass
    return ""


def request(url: str, *, method: str = "GET", json_body: Optional[dict] = None, retries: int = 3) -> requests.Response:
    last_error = None
    for attempt in range(retries):
        try:
            if method == "POST":
                response = requests.post(url, headers=HEADERS, json=json_body, timeout=REQUEST_TIMEOUT)
            else:
                response = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            if not response.encoding or response.encoding.lower() == "iso-8859-1":
                response.encoding = response.apparent_encoding
            return response
        except Exception as exc:
            last_error = exc
            time.sleep(2 ** attempt)
    raise RuntimeError(f"Request failed after {retries} attempts: {url}: {last_error}")


def meta_value(soup: BeautifulSoup, *names: str) -> str:
    for name in names:
        tag = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})
        if tag and tag.get("content"):
            return tag["content"].strip()
    return ""


# ---------- 列表发现与正文读取 ----------

def discover(source: dict) -> List[dict]:
    response = request(source["url"])
    soup = BeautifulSoup(response.text, "html.parser")
    results = []
    for anchor in soup.select("a[href]"):
        title = " ".join(anchor.get_text(" ", strip=True).split())
        if not (12 <= len(title) <= 260) or not KEYWORDS.search(title):
            continue
        url = normalize_url(urljoin(response.url, anchor.get("href")))
        if not is_article_url(url, source["url"]):
            continue
        parent = anchor.find_parent(["article", "li", "tr", "section", "div"]) or anchor.parent
        context = " ".join(parent.get_text(" ", strip=True).split())[:500] if parent else ""
        results.append({"title": title, "url": url, "listDate": parse_date(context) or parse_date(url)})
    return list({r["url"]: r for r in results}.values())


def read_article(url: str) -> dict:
    if url.lower().endswith(".pdf"):
        return {
            "url": url,
            "title": "",
            "date": parse_date(url),
            "description": "",
            "text": "",
            "contentHash": hashlib.sha256(url.encode()).hexdigest(),
        }

    response = request(url)
    soup = BeautifulSoup(response.text, "html.parser")
    canonical = ""
    tag = soup.find("link", rel=lambda x: x and "canonical" in x)
    if tag and tag.get("href"):
        canonical = urljoin(response.url, tag["href"])
    canonical = canonical or meta_value(soup, "og:url") or response.url
    if not is_article_url(canonical, url):
        canonical = response.url

    title = (
        meta_value(soup, "og:title", "twitter:title", "ArticleTitle")
        or (soup.h1.get_text(" ", strip=True) if soup.h1 else "")
    )
    description = meta_value(soup, "description", "og:description")
    date = parse_date(meta_value(
        soup, "article:published_time", "PubDate", "pubdate", "publishdate",
        "dc.date", "DC.date.issued", "date", "citation_publication_date"
    ))

    for unwanted in soup(["script", "style", "nav", "header", "footer", "aside", "form"]):
        unwanted.decompose()
    paragraphs = []
    for element in soup.find_all(["p", "li", "h2", "h3"]):
        text = " ".join(element.get_text(" ", strip=True).split())
        if len(text) >= 25:
            paragraphs.append(text)
    body = "\n".join(paragraphs)[:12000]
    if not date:
        date = parse_date(body[:1200]) or parse_date(response.url)
    content_hash = hashlib.sha256((title + "\n" + body).encode("utf-8")).hexdigest()
    return {
        "url": normalize_url(canonical),
        "title": title,
        "date": date,
        "description": description,
        "text": body,
        "contentHash": content_hash,
    }


# ---------- 规则分类（仅分类，不产生解读） ----------

def classify(title: str, body: str) -> Tuple[str, str]:
    text = (title + " " + body[:2500]).lower()
    rules = [
        ("热点新闻", "并购交易", r"merger|acquisition|takeover|divest|并购|收购|经营者集中|剥离"),
        ("热点新闻", "央行与市场", r"central bank|interest rate|monetary policy|federal reserve|fomc|ecb|央行|利率|货币政策|加息|降息"),
        ("政策法规", "反垄断", r"antitrust|competition law|cartel|abuse of dominance|反垄断|垄断协议|滥用市场支配"),
        ("政策法规", "人权", r"human rights|forced labo|child labo|csddd|人权|强迫劳动|童工"),
        ("政策法规", "零毁林", r"deforestation|eudr|毁林"),
        ("政策法规", "生物多样性", r"biodiversity|nature-related|nature restoration|生物多样性|自然相关|生态保护修复"),
        ("政策法规", "废弃物", r"waste|recycl|circular|packaging|ppwr|epr|plastic|废弃物|固废|循环经济|包装|塑料|回收"),
        ("政策法规", "水", r"water|wastewater|river|marine|ocean|水污染|污水|河湖|流域|地下水|饮用水|海洋"),
        ("政策法规", "大气污染物", r"air quality|air pollut|clean air|nox|so2|pm2\.5|voc|ozone|hfc|大气|空气质量|颗粒物|臭氧"),
        ("政策法规", "供应链安全", r"export control|critical raw|critical mineral|supply chain|sanction|investment screening|出口管制|关键原材料|供应链|制裁|安全审查|实体清单"),
        ("热点新闻", "全球与贸易", r"trade|tariff|anti-dumping|countervailing|wto|贸易|关税|反倾销|反补贴"),
        ("政策法规", "财务准则", r"ifrs|hkfrs|ias |issb|会计准则|财务报告准则"),
        ("政策法规", "气候", r"climate|carbon|emission|greenhouse|ets|cbam|methane|net zero|气候|碳|温室气体|排放|甲烷|减排"),
    ]
    for section, topic, pattern in rules:
        if re.search(pattern, text):
            return section, topic
    return "热点新闻", "全球与贸易"


def legal_status(text: str) -> str:
    value = text.lower()
    if re.search(r"consultation|call for evidence|draft|proposal|proposed rule|征求意见|草案|公开征求", value):
        return "征求意见/提案"
    if re.search(r"enter(?:s|ed)? into force|effective|施行|生效|起实施", value):
        return "已生效/即将生效"
    if re.search(r"adopt|final rule|approved|通过|印发|正式发布", value):
        return "已通过/发布"
    if re.search(r"fine|penalt|settlement|lawsuit|处罚|罚款|执法|诉讼", value):
        return "执法/诉讼"
    return "动态"


# ---------- 逐条独立AI分析 ----------
SYSTEM_PROMPT = """
你是为港股上市公司CFO、审计委员会和法务负责人服务的资深政策、监管与市场情报分析师。

任务：只基于用户提供的单篇原文资料，对这一篇事件做独立分析。不能复用其他事件的结论，不能套主题模板。

强制规则：
1. 原文未提供的信息必须写“原文未披露”，不得猜测数字、日期、适用对象或法律效果。
2. 明确区分：原文事实、专业分析、分析师判断。不得把提案写成已生效义务。
3. professionalAnalysis 必须具体说明：法律/政策性质、当前阶段、适用范围、核心机制、关键时间、与既有规则的关系、主要不确定性。
4. companyImpact 必须分别说明 financial、compliance、operations、supplyChain、disclosure、strategy 六个维度；不适用时写“不构成直接影响”。
5. actions 必须针对本事件，包含 owner、deadline、action、deliverable，不允许仅写“持续关注”或“建议相关企业关注”。
6. keyFacts 每条都必须能在原文中找到依据，优先保留日期、金额、比例、阈值、覆盖对象和生效时间。
7. sources 只允许使用用户提供的原文URL。
8. 全部使用简体中文，输出严格JSON，不输出Markdown。

JSON结构：
{
  "oneLiner": "不超过80字",
  "eventType": "法规/指令/提案/征求意见/执法/法院裁决/政策文件/会议决定/统计展望/并购新闻/央行决议/准则动态/其他",
  "legalStatus": "征求意见/提案|已通过/发布|已生效/即将生效|执法/诉讼|动态",
  "impactLevel": "高|中高|中",
  "keyFacts": [
    {"fact": "事实", "evidence": "原文中的简短证据，不超过70字"}
  ],
  "professionalAnalysis": "180-350字的独立分析",
  "plainLanguage": "80-140字的通俗解读",
  "companyImpact": {
    "financial": "具体财务影响，涉及收入、成本、资本开支、预计负债、减值、现金流或估值",
    "compliance": "具体合规义务、许可、报告、尽调、处罚或法律风险",
    "operations": "生产、项目、产品、系统、人员或流程影响",
    "supplyChain": "供应商、原材料、追溯、物流、采购或客户要求",
    "disclosure": "年报、ESG报告、交易所披露或数据一致性影响",
    "strategy": "市场准入、竞争优势、投资、退出或商业模式影响"
  },
  "affectedIndustries": ["只列与本事件直接相关的行业"],
  "affectedCompanyProfile": ["具有哪些业务/地区/产品特征的企业会受影响"],
  "actions": [
    {"owner": "负责部门", "deadline": "建议期限", "action": "具体动作", "deliverable": "需要产出的文件/数据/决策"}
  ],
  "monitoring": [
    {"date": "已知日期或待定", "milestone": "后续节点", "whyItMatters": "为何重要"}
  ],
  "analystJudgement": "80-160字，明确标记为分析师判断",
  "confidence": "高|中|低",
  "gaps": ["原文缺失但决策需要的信息"]
}
""".strip()


def extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    try:
        return json.loads(text)
    except Exception:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise ValueError("LLM response did not contain a JSON object")
        return json.loads(match.group(0))


def validate_analysis(value: dict) -> dict:
    required_strings = ["oneLiner", "eventType", "legalStatus", "impactLevel", "professionalAnalysis", "plainLanguage", "analystJudgement", "confidence"]
    for key in required_strings:
        if not isinstance(value.get(key), str) or not value[key].strip():
            raise ValueError(f"Invalid or missing field: {key}")
    if value["impactLevel"] not in ("高", "中高", "中"):
        raise ValueError("impactLevel must be 高/中高/中")
    for key in ("keyFacts", "affectedIndustries", "affectedCompanyProfile", "actions", "monitoring", "gaps"):
        if not isinstance(value.get(key), list):
            raise ValueError(f"{key} must be a list")
    impact = value.get("companyImpact")
    if not isinstance(impact, dict):
        raise ValueError("companyImpact must be an object")
    for dimension in ("financial", "compliance", "operations", "supplyChain", "disclosure", "strategy"):
        if not isinstance(impact.get(dimension), str) or not impact[dimension].strip():
            impact[dimension] = "原文信息不足，暂无法判断。"
    return value


def analyse_with_llm(item: dict, article: dict) -> dict:
    if not LLM_KEY:
        raise RuntimeError("LLM_API_KEY is not configured")

    payload = {
        "source": {
            "url": item["originalUrl"],
            "authority": item["authority"],
            "jurisdiction": item["jurisdiction"],
            "publishedDate": item.get("datePublished", ""),
            "title": item["title"],
        },
        "preClassification": {
            "section": item["section"],
            "topic": item["topic"],
            "legalStatus": item["legalStatus"],
        },
        "article": {
            "description": article.get("description", ""),
            "body": article.get("text", "")[:10000],
        },
    }

    headers = {
        **HEADERS,
        "Authorization": f"Bearer {LLM_KEY}",
        "Content-Type": "application/json",
    }
    endpoint = f"{LLM_BASE_URL}/chat/completions"
    body = {
        "model": LLM_MODEL,
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
    }

    last_error = None
    for attempt in range(3):
        try:
            response = requests.post(endpoint, headers=headers, json=body, timeout=120)
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            result = validate_analysis(extract_json(content))
            result["analysisMethod"] = "AI逐条独立分析"
            result["analysisModel"] = LLM_MODEL
            result["analysisAt"] = NOW.isoformat()
            result["analysisPromptVersion"] = "v4-independent-2026-11"
            return result
        except Exception as exc:
            last_error = exc
            time.sleep(2 ** attempt)
    raise RuntimeError(f"Independent analysis failed: {last_error}")


def pending_analysis(article: dict, reason: str) -> dict:
    """无AI时不生成主题套话，只保存原文事实摘要并标记待分析。"""
    facts = []
    sentences = re.split(r"(?<=[。；;.!?！？])\s*", article.get("text", ""))
    for sentence in sentences:
        sentence = sentence.strip()
        if 20 <= len(sentence) <= 160 and re.search(r"\d|应当|必须|不得|生效|截止|shall|must|required|effective", sentence, re.I):
            facts.append({"fact": sentence[:150], "evidence": sentence[:70]})
        if len(facts) >= 5:
            break
    return {
        "oneLiner": article.get("description") or "已收录原文，独立专业分析待生成。",
        "eventType": "其他",
        "keyFacts": facts,
        "professionalAnalysis": "独立专业分析待生成。",
        "plainLanguage": "已保存原文事实，待配置分析模型后补充逐条解读。",
        "companyImpact": {
            "financial": "待分析", "compliance": "待分析", "operations": "待分析",
            "supplyChain": "待分析", "disclosure": "待分析", "strategy": "待分析",
        },
        "affectedIndustries": [],
        "affectedCompanyProfile": [],
        "actions": [],
        "monitoring": [],
        "analystJudgement": "待分析",
        "confidence": "低",
        "gaps": [reason],
        "analysisMethod": "待分析",
        "analysisStatus": "pending",
        "analysisAt": NOW.isoformat(),
        "analysisPromptVersion": "v4-independent-2026-11",
    }


def flatten_for_legacy(record: dict) -> dict:
    """兼容现有 index.html / build_email.py 字段，同时保留V4结构。"""
    impact = record.get("companyImpact", {})
    record["hkImpact"] = "\n".join(
        f"{name}：{impact.get(key, '')}"
        for name, key in [
            ("财务", "financial"), ("合规", "compliance"), ("运营", "operations"),
            ("供应链", "supplyChain"), ("披露", "disclosure"), ("战略", "strategy")
        ] if impact.get(key)
    )
    record["industries"] = record.get("affectedIndustries", [])
    record["keyPoints"] = [f.get("fact", "") for f in record.get("keyFacts", []) if f.get("fact")]
    record["actionsLegacy"] = [
        f"{a.get('owner', '相关部门')}｜{a.get('deadline', '尽快')}：{a.get('action', '')}；产出：{a.get('deliverable', '')}"
        for a in record.get("actions", [])
    ]
    record["actions"] = record["actionsLegacy"]
    record["watchItems"] = [
        f"{m.get('date', '待定')}：{m.get('milestone', '')}（{m.get('whyItMatters', '')}）"
        for m in record.get("monitoring", [])
    ]
    return record


# ---------- 单条处理与历史重分析 ----------

def build_base_item(source: dict, candidate: dict, article: dict) -> dict:
    title = article.get("title") or candidate["title"]
    section, topic = classify(title, article.get("text", ""))
    status = legal_status(title + " " + article.get("text", "")[:2500])
    item_id = hashlib.sha1(article["url"].encode("utf-8")).hexdigest()[:16]
    return {
        "id": item_id,
        "title": title,
        "datePublished": article.get("date") or candidate.get("listDate", ""),
        "jurisdiction": source["region"],
        "authority": source["name"],
        "sourceType": source.get("type", "official"),
        "sourceTier": source.get("tier", "S"),
        "section": section,
        "topic": topic,
        "legalStatus": status,
        "originalUrl": article["url"],
        "linkVerified": True,
        "contentHash": article["contentHash"],
        "collectedAt": NOW.isoformat(),
    }


def analyse_item(item: dict, article: dict) -> dict:
    try:
        analysis = analyse_with_llm(item, article)
        analysis["analysisStatus"] = "complete"
    except Exception as exc:
        analysis = pending_analysis(article, str(exc))
    item.update(analysis)
    if item.get("legalStatus") == "动态" and analysis.get("legalStatus"):
        item["legalStatus"] = analysis["legalStatus"]
    item["impactLevel"] = analysis.get("impactLevel", item.get("impactLevel", "中"))
    return flatten_for_legacy(item)


def source_map(sources: List[dict]) -> Dict[str, dict]:
    return {s["name"]: s for s in sources}


def reanalyse_existing(record: dict, sources_by_name: Dict[str, dict]) -> dict:
    source = sources_by_name.get(record.get("authority"))
    if not source or not record.get("originalUrl"):
        return record
    article = read_article(record["originalUrl"])
    base = build_base_item(source, {"title": record["title"], "listDate": record.get("datePublished", "")}, article)
    base["id"] = record.get("id", base["id"])
    base["pinned"] = record.get("pinned", False)
    return analyse_item(base, article)


# ---------- 主流程 ----------

def main() -> None:
    config = load_json(SOURCES_FILE, {"sources": []})
    sources = config.get("sources", [])
    old_payload = load_json(DATA_FILE, {"items": []})
    existing = old_payload.get("items", [])
    records = {r["id"]: r for r in existing if r.get("id")}
    known_urls = {r.get("originalUrl") for r in records.values() if r.get("originalUrl")}
    errors = []
    analysis_count = 0
    added_count = 0
    reanalysed_count = 0

    # 1. 可选：重跑历史记录。只处理旧模板、pending，或FORCE_REANALYZE=true。
    by_name = source_map(sources)
    for record_id, record in list(records.items()):
        needs = (
            FORCE_REANALYZE
            or record.get("analysisStatus") == "pending"
            or record.get("analysisMethod") in ("规则生成", "规则初筛", "主题模板")
        )
        if not needs or analysis_count >= MAX_ANALYSIS_PER_RUN:
            continue
        try:
            records[record_id] = reanalyse_existing(record, by_name)
            analysis_count += 1
            reanalysed_count += 1
        except Exception as exc:
            errors.append({"source": record.get("authority", "历史数据"), "url": record.get("originalUrl", ""), "error": str(exc)[:300]})

    # 2. 发现并处理新文章。
    for source in sources:
        try:
            candidates = discover(source)
        except Exception as exc:
            errors.append({"source": source["name"], "url": source["url"], "error": str(exc)[:300]})
            continue

        per_source = 0
        for candidate in candidates:
            if per_source >= MAX_NEW_PER_SOURCE:
                break
            if candidate["url"] in known_urls:
                continue
            try:
                article = read_article(candidate["url"])
                if article["url"] in known_urls:
                    continue
                published = article.get("date") or candidate.get("listDate", "")
                if published:
                    age = (NOW.date() - dt.date.fromisoformat(published)).days
                    if age > MAX_AGE_DAYS:
                        continue
                item = build_base_item(source, candidate, article)
                if analysis_count < MAX_ANALYSIS_PER_RUN:
                    item = analyse_item(item, article)
                    analysis_count += 1
                else:
                    item.update(pending_analysis(article, "Reached MAX_ANALYSIS_PER_RUN"))
                    item = flatten_for_legacy(item)
                records[item["id"]] = item
                known_urls.add(article["url"])
                added_count += 1
                per_source += 1
                time.sleep(0.4)
            except Exception as exc:
                errors.append({"source": source["name"], "url": candidate["url"], "error": str(exc)[:300]})

    # 3. 保留最近120天，以及pinned记录。
    cutoff = (NOW.date() - dt.timedelta(days=120)).isoformat()
    output_records = [
        r for r in records.values()
        if r.get("pinned") or not r.get("datePublished") or r["datePublished"] >= cutoff
    ]
    output_records.sort(key=lambda r: r.get("datePublished", ""), reverse=True)
    output_records = output_records[:800]

    summary = {
        "generatedAt": NOW.isoformat(),
        "count": len(output_records),
        "analysis": {
            "mode": "independent-per-article",
            "model": LLM_MODEL if LLM_KEY else None,
            "analysedThisRun": analysis_count,
            "reanalysedThisRun": reanalysed_count,
            "addedThisRun": added_count,
            "pendingCount": sum(r.get("analysisStatus") == "pending" for r in output_records),
        },
        "errors": errors,
        "items": output_records,
    }
    save_json(DATA_FILE, summary)
    save_json(ERROR_FILE, {"generatedAt": NOW.isoformat(), "errors": errors})
    print(json.dumps(summary["analysis"] | {"total": len(output_records), "errors": len(errors)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
