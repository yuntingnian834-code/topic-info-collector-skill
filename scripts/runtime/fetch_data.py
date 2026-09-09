from __future__ import annotations

import os
import sys
import re
import json
import hashlib
import random
import datetime
import logging
import time
import requests
import feedparser
import trafilatura
from zoneinfo import ZoneInfo
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, TimeoutError as ThreadTimeoutError
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, SCRIPTS_DIR)
from feishu import get_info_points, append_daily_record, get_today_records
from monitoring import (
    article_id_for_url,
    finish_run,
    format_run_summary,
    get_run,
    get_source_summary,
    publish_run_report,
    record_source_run,
    source_id_for_url,
    start_run,
    track_article_event,
)
from monitoring.phase2.model_monitor import (
    PROMPT_VERSIONS,
    record_model_call,
    update_model_call,
)

load_dotenv()

BAIDU_APPID      = os.getenv("BAIDU_TRANSLATE_APPID")
BAIDU_KEY        = os.getenv("BAIDU_TRANSLATE_KEY")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

BEIJING_TZ = ZoneInfo("Asia/Shanghai")
RUN_STARTED_AT = datetime.datetime.now(BEIJING_TZ)
TODAY     = RUN_STARTED_AT.date()
TODAY_STR = TODAY.strftime("%Y-%m-%d")
# 记录真实采集时刻，而不是当天 00:00。这样网站可以准确展示更新时间，
# 同时避免 GitHub Runner（UTC）与本地电脑（Asia/Shanghai）产生 8 小时时差。
TODAY_MS  = int(RUN_STARTED_AT.timestamp() * 1000)

_CUTOFF          = TODAY - datetime.timedelta(days=1)     # 24h 滑动窗口，覆盖国际时差
_CUTOFF_30       = TODAY - datetime.timedelta(days=30)   # 官方月报宽窗口

# 官方月报类（使用 30 天窗口）
_OFFICIAL_REPORT_KEYWORDS = ("供需平衡表", "WASDE", "库存消费比", "宏观大宗商品综合价格指数")

# 定价机制（摘要中注入 contango/backwardation 提示）
_PRICING_MECH_KEYWORD = "高频交易所定价与 PRAs 现货价格发现机制"

# 政策类关键词（影响 market_or_policy_status 的 prompt 分支）
_POLICY_KEYWORDS = ("出口管制", "禁运", "0.1%", "储备政策", "储备", "绿色关税", "合规", "双向出口", "制裁")

_FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


# ── 日期工具 ──────────────────────────────────────────────────────────────────────

def _parse_date(raw: str) -> datetime.date | None:
    if not raw:
        return None
    raw = raw.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d", "%d %B %Y", "%B %d, %Y",
                "%b %d, %Y", "%d/%m/%Y", "%m/%d/%Y",
                "%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z"):
        try:
            return datetime.datetime.strptime(raw[:len(fmt)+8], fmt).date()
        except Exception:
            continue
    m = re.search(r'(\d{4}-\d{2}-\d{2})', raw)
    if m:
        try:
            return datetime.date.fromisoformat(m.group(1))
        except Exception:
            pass
    return None


def _date_label(d: datetime.date | None) -> str:
    if d is None:
        return ""
    return f"【报告时间：{d.year}年{d.month}月{d.day}日】\n"


def _is_fresh(d: datetime.date | None, cutoff: datetime.date | None = None) -> bool:
    """
    默认（cutoff=None）：d >= TODAY-1 放行，兼容国际时差；日期未知保守丢弃。
    传入 cutoff（官方月报宽窗口）：d >= cutoff 放行。
    """
    if d is None:
        return False
    return d >= (cutoff if cutoff is not None else _CUTOFF)


def _is_recent_year(text: str) -> bool:
    """检查文本是否包含 2026 年相关的日期模式（月+日），而非仅四位年份数字。"""
    patterns = [
        r'2026[/\-\.]\d{1,2}',          # 2026-07, 2026/7, 2026.07
        r'(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+2026',  # July 2026
        r'2026\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)',  # 2026 July
        r'(?:1[月]|2[月]|3[月]|4[月]|5[月]|6[月]|7[月]|8[月]|9[月]|10[月]|11[月]|12[月]).*2026',  # 7月 2026
    ]
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


# ── 网站名称映射 ──────────────────────────────────────────────────────────────────

SITE_NAMES = {
    "fao.org":                    "联合国粮农组织（FAO）",
    "usda.gov":                   "美国农业部（USDA）",
    "eia.gov":                    "美国能源信息署（EIA）",
    "cftc.gov":                   "美国商品期货交易委员会（CFTC）",
    "stlouisfed.org":             "美联储 FRED 数据库",
    "nber.org":                   "美国国家经济研究局（NBER）",
    "worldbank.org":              "世界银行",
    "iea.org":                    "国际能源署（IEA）",
    "igc.int":                    "国际谷物理事会（IGC）",
    "imf.org":                    "国际货币基金组织（IMF）",
    "oecd.org":                   "经合组织（OECD）",
    "tradingeconomics.com":       "Trading Economics",
    "spglobal.com":               "标普全球（S&P Global）",
    "lme.com":                    "伦敦金属交易所（LME）",
    "cmegroup.com":               "芝商所（CME Group）",
    "metal.com":                  "金属网（Metal.com）",
    "shfe.com.cn":                "上海期货交易所",
    "gfex.com.cn":                "广州期货交易所",
    "100ppi.com":                 "生意社",
    "benchmarkminerals.com":      "Benchmark Minerals Intelligence",
    "fastmarkets.com":            "Fastmarkets",
    "argusmedia.com":             "Argus Media",
    "balticexchange.com":         "波罗的海交易所",
    "sse.net.cn":                 "上海航运交易所（SCFI）",
    "hellenicshippingnews.com":   "Hellenic Shipping News",
    "drewry.co.uk":               "Drewry 航运咨询",
    "tradewindsnews.com":         "TradeWinds 航运新闻",
    "splash247.com":              "Splash247 航运新闻",
    "kpler.com":                  "Kpler 大宗商品追踪",
    "portwatch.imf.org":          "IMF PortWatch",
    "freightwaves.com":           "FreightWaves",
    "maritime-executive.com":     "The Maritime Executive",
    "marinetraffic.com":          "MarineTraffic AIS",
    "suezcanal.gov.eg":           "苏伊士运河管理局",
    "pancanal.com":               "巴拿马运河管理局",
    "gcaptain.com":               "gCaptain 航运资讯",
    "lloydslist.maritimeintelligence.informa.com": "劳氏船舶日报",
    "datalab.wto.org":            "世贸组织数据实验室",
    "railfreight.com":            "RailFreight 铁路货运",
    "unctad.org":                 "联合国贸发会议（UNCTAD）",
    "bis.doc.gov":                "美国商务部 BIS",
    "mofcom.gov.cn":              "中国商务部",
    "csis.org":                   "战略与国际研究中心（CSIS）",
    "home.treasury.gov":          "美国财政部 OFAC",
    "reuters.com":                "路透社",
    "bbc.com":                    "BBC",
    "bbc.co.uk":                  "BBC",
    "ft.com":                     "英国金融时报（FT）",
    "oilprice.com":               "OilPrice.com",
    "mining.com":                 "Mining.com",
    "agrimoney.com":              "Agrimoney",
    "worldgrain.com":             "World Grain 粮食新闻",
    "scmp.com":                   "南华早报（SCMP）",
    "defensenews.com":            "Defense News",
    "energy.gov":                 "美国能源部（DOE）",
    "taxation-customs.ec.europa.eu": "欧盟税务与海关委员会",
    "climate.ec.europa.eu":       "欧盟气候行动委员会",
    "moa.gov.cn":                 "中国农业农村部",
    "stats.gov.cn":               "国家统计局",
    "customs.gov.cn":             "海关总署",
    "ndrc.gov.cn":                "国家发改委价格监测中心",
    "eastmoney.com":              "东方财富网",
    "ifpri.org":                  "国际食物政策研究所（IFPRI）",
    "grain.org":                  "GRAIN 粮食主权组织",
    "investing.com":              "Investing.com",
}

# 噪声域名黑名单（混入结果的无关网站）
_BLOCKED_DOMAINS = (
    "baike.baidu.com", "wikipedia.org", "baidu.com",
    "zhidao.baidu.com", "minecraft.wiki", "wikia.com",
    "fandom.com", "worldometers.info", "tvmao.com",
    "pandalearnchines",
)

# ── 全局 RSS Feed Cache（同一进程内，相同 URL 只请求一次）────────────────────────────
_RSS_FEED_CACHE: dict[str, list[dict]] = {}
_RSS_FEED_META: dict[str, dict] = {}


def collection_source_health() -> dict[str, int]:
    """Return in-process feed health even if the monitoring store is unavailable."""
    total_count = len(_RSS_FEED_META)
    success_count = sum(
        1 for item in _RSS_FEED_META.values() if item.get("status") == "success"
    )
    return {
        "total_count": total_count,
        "success_count": success_count,
        "failed_count": max(0, total_count - success_count),
    }


def _site_name(url: str) -> str:
    try:
        if url and not url.startswith(("http://", "https://")):
            url = "https://" + url
        parsed = urlparse(url)
        netloc = parsed.netloc or parsed.path.split("/")[0]
        domain = netloc.removeprefix("www.")
        for key, name in SITE_NAMES.items():
            if domain.endswith(key):
                return name
        return domain
    except Exception:
        return url


def _domain_of(url: str) -> str:
    try:
        if url and not url.startswith(("http://", "https://")):
            url = "https://" + url
        return urlparse(url).netloc.removeprefix("www.")
    except Exception:
        return ""


def _is_blocked(url: str) -> bool:
    return any(b in url for b in _BLOCKED_DOMAINS)


# ── 百度翻译兜底 ──────────────────────────────────────────────────────────────────

def _is_chinese(text: str) -> bool:
    if not text:
        return True
    zh_count = sum(1 for c in text if '一' <= c <= '鿿')
    return zh_count / max(len(text), 1) > 0.2


def translate_to_zh(text: str, max_chars: int = 450) -> str:
    if not text:
        return ""
    text = text[:2000]
    if _is_chinese(text):
        return text[:max_chars]
    if not BAIDU_APPID or not BAIDU_KEY:
        return text[:max_chars]
    try:
        salt = str(random.randint(10000, 99999))
        sign = hashlib.md5(
            (BAIDU_APPID + text + salt + BAIDU_KEY).encode("utf-8")
        ).hexdigest()
        resp = requests.get(
            "https://fanyi-api.baidu.com/api/trans/vip/translate",
            params={"q": text, "from": "auto", "to": "zh",
                    "appid": BAIDU_APPID, "salt": salt, "sign": sign},
            timeout=12,
        )
        resp.raise_for_status()
        data = resp.json()
        if "trans_result" in data:
            return "".join(r["dst"] for r in data["trans_result"])[:max_chars]
    except Exception:
        pass
    return text[:max_chars]


# ── DeepSeek ──────────────────────────────────────────────────────────────────────

def _deepseek_chat(
    prompt: str,
    max_tokens: int = 900,
    is_json: bool = True,
    *,
    run_id: str | None = None,
    article_id: str | None = None,
    call_type: str = "unspecified",
    prompt_version: str | None = None,
    retry_count: int = 0,
    fallback_used: bool = False,
    call_meta: dict | None = None,
) -> str:
    model_name = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
    temperature = 0.3
    prompt_version = prompt_version or PROMPT_VERSIONS.get(call_type, "unversioned")
    started_at = time.monotonic()
    try:
        body = {"model": model_name,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens, "temperature": temperature}
        if is_json:
            body["response_format"] = {"type": "json_object"}
        resp = requests.post(
            "https://api.deepseek.com/chat/completions",
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                     "Content-Type": "application/json"},
            json=body,
            timeout=35,
        )
        resp.raise_for_status()
        response_data = resp.json()
        usage = response_data.get("usage") or {}
        content = response_data["choices"][0]["message"]["content"].strip()
        call_id = record_model_call(
            run_id=run_id,
            article_id=article_id,
            call_type=call_type,
            model_name=model_name,
            model_version=response_data.get("model"),
            prompt_version=prompt_version,
            temperature=temperature,
            input_chars=len(prompt),
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            latency_ms=int((time.monotonic() - started_at) * 1000),
            retry_count=retry_count,
            fallback_used=fallback_used,
            result_status="success",
        )
        if call_meta is not None:
            call_meta["call_id"] = call_id
        return content
    except Exception as e:
        call_id = record_model_call(
            run_id=run_id,
            article_id=article_id,
            call_type=call_type,
            model_name=model_name,
            prompt_version=prompt_version,
            temperature=temperature,
            input_chars=len(prompt),
            latency_ms=int((time.monotonic() - started_at) * 1000),
            retry_count=retry_count,
            fallback_used=fallback_used,
            result_status="failed",
            error=e,
        )
        if call_meta is not None:
            call_meta["call_id"] = call_id
        logging.warning("DeepSeek API 请求失败: %s", str(e)[:80])
        raise


def _rule_title(text: str, fallback: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if len(line) < 8 or len(line) > 100:
            continue
        if sum(1 for c in line if c.isalnum()) >= len(line) * 0.5:
            return line[:80]
    return fallback[:60]


# ── 中文兜底翻译（summarize_v2 异常或 insight 缺失时使用）──────────────────────────

def _deepseek_fallback_zh(
    content: str,
    level2: str,
    *,
    run_id: str | None = None,
    article_id: str | None = None,
) -> str:
    """
    发起一次极简 DeepSeek 请求，将英文内容压缩为 ≤150 字的中文商业摘要。
    若 DeepSeek 不可用或再次异常，降级到百度翻译；百度也不可用则截断原文。
    """
    if not DEEPSEEK_API_KEY:
        return translate_to_zh(content, 150)
    try:
        prompt = (
            f"将以下英文内容提炼为150字以内的中文高浓度商业摘要，"
            f"聚焦大宗商品/航运/宏观数据变化，禁止出现任何英文长句：\n\n{content[:2000]}"
        )
        return _deepseek_chat(
            prompt,
            max_tokens=300,
            is_json=False,
            run_id=run_id,
            article_id=article_id,
            call_type="summary_fallback",
            prompt_version=PROMPT_VERSIONS["summary_fallback"],
            fallback_used=True,
        ).strip()
    except Exception:
        return translate_to_zh(content, 150)


# ── Summary Engine V2（全局唯一提炼函数）────────────────────────────────────────────

def summarize_v2(content: str, level2: str, research_core: str,
                 date_label: str, *, run_id: str | None = None,
                 article_id: str | None = None) -> tuple[str, str, datetime.date | None, dict]:
    """
    返回 (title, summary, event_date, metadata)。
    is_target_related=false 时直接返回 None-equivalent，由 _build_record 拦截。
    """
    if not DEEPSEEK_API_KEY or not content:
        return (
            _rule_title(content, level2),
            date_label + translate_to_zh(content, 400),
            None,
            {"relevance_status": "fallback_no_model"},
        )

    is_pricing = _PRICING_MECH_KEYWORD in level2
    content_trunc = content[:5000]

    if is_pricing:
        status_hint = (
            "原文是否明确提及期限结构？明确提及 Contango 则输出 Contango，"
            "明确提及 Backwardation 则输出 Backwardation；原文未明确说明则输出 null，不得推断"
        )
    elif any(kw in level2 for kw in _POLICY_KEYWORDS):
        status_hint = (
            "提取政策当前所处阶段，如：草案征求意见期、正式生效、合规审查中、立法审议中、已废止；"
            "原文无明确阶段信息则输出 null，不得推断"
        )
    else:
        status_hint = (
            "若原文涉及市场结构（Contango/Backwardation）则输出对应英文词；"
            "若涉及政策阶段则输出中文阶段描述；原文无明确信息则输出 null，不得推断"
        )

    rc_hint = research_core[:120] if research_core else level2

    prompt = (
        f"你是大宗商品量化分析师。任务：从原文中严格提取结构化信息，并执行强制相关性拦截。\n"
        f"研究主题：「{level2}」\n"
        f"研究核心：{rc_hint}\n\n"
        f"【绝对语言约束】无论原文是何种语言，所有输出字段必须 100% 使用专业简洁的中文表达，"
        f"绝对禁止在任何字段中保留英文长句或英文原文段落！\n\n"
        f"━━━━━━ 【第一步：绝对拦截阀】━━━━━━\n"
        f"判断原文是否属于以下【无条件拦截类别】，如属于其中任意一类，必须将 is_target_related 设为 false，"
        f"并在 rejection_reason 中注明原因，禁止继续填写其他字段（填null即可）：\n"
        f"  ❌ 体育赛事：足球/世界杯/奥运会/NBA/F1/板球/橄榄球及其财务/赞助/转播权分析\n"
        f"  ❌ 个人理财与消费：个人储蓄建议、信用卡、抵押贷款、消费品零售、生活成本类\n"
        f"  ❌ 娱乐八卦：影视、音乐、名人广告代言、网红、综艺节目\n"
        f"  ❌ 纯科技产品发布：消费电子、手机、操作系统、AI工具/ChatGPT/Gemini等软件发布（除非明确影响大宗商品需求）\n"
        f"  ❌ 地方民生新闻：城市基础设施、地方政府人事、学校医院等无宏观经济含义的新闻\n"
        f"  ❌ 纯公司治理八卦：CEO任免、公司内部纠纷（除非涉及大宗商品生产商/交易所/监管机构）\n"
        f"  ❌ 宗教、文化、旅游类内容\n\n"
        f"━━━━━━ 【第二步：正向准入标准】━━━━━━\n"
        f"仅当原文明确涉及以下之一时，才可将 is_target_related 设为 true：\n"
        f"  ✅ 大宗商品价格、库存、产量、贸易流向、供需平衡\n"
        f"  ✅ 宏观经济数据（GDP、CPI、PMI、利率、汇率）且与大宗商品需求/成本传导相关\n"
        f"  ✅ 地缘政治事件（战争、制裁、出口管制）且直接影响商品供应链\n"
        f"  ✅ 航运运价、港口拥堵、航道中断、船队动态\n"
        f"  ✅ 大宗商品相关监管政策（关税、储备、CBAM、合规框架）\n"
        f"  ✅ 交易所数据（LME/CME/SHFE 持仓、结算价、期货曲线结构）\n\n"
        f"【强制要求】严格按以下 JSON Schema 输出，不得输出任何额外文字，所有字段值必须为中文：\n"
        f'{{\n'
        f'  "is_target_related": true或false，【绝对拦截阀】符合上方任意拦截类别则必须为false，符合准入标准才可为true，\n'
        f'  "rejection_reason": "is_target_related为false时必填，说明拦截类别（如：体育赛事-世界杯赞助/个人理财/娱乐八卦）；为true时填null",\n'
        f'  "date_of_event": "必须从正文精确提取数据发布或事件发生日期，格式YYYY-MM-DD；原文没有则填null，绝对不能编造",\n'
        f'  "commodity_type": "原文涉及的具体大宗商品、宏观指数或政策领域全称（如：WTI原油、LME铜、CBOT大豆、欧盟CBAM、稀土出口管制）；跨品种填综合；is_target_related为false时填null",\n'
        f'  "quantitative_metrics": "请严格根据研究核心「{rc_hint}」的要求提取核心计量经济变量，格式：指标名+具体数值+单位+变动方向；原文无相关数字数据或is_target_related为false时填null",\n'
        f'  "market_or_policy_status": "{status_hint}；is_target_related为false时填null",\n'
        f'  "analytical_summary": "【必须为中文】详实的商业与数据洞察：①保留必要的政策背景或市场前置条件 ②明确列出核心变量（如价格、税率、数据量）的具体数值与变化幅度 ③深入分析对微观企业定价或宏观经济的传导逻辑。请提供细节丰富的深度分析，字数尽量控制在 300 到 400 字左右。is_target_related为false时填null"\n'
        f'}}\n\n'
        f"原文（{len(content_trunc)}字）：\n{content_trunc}"
    )

    try:
        main_call_meta = {}
        raw = _deepseek_chat(
            prompt,
            max_tokens=1200,
            run_id=run_id,
            article_id=article_id,
            call_type="relevance_extract",
            prompt_version=PROMPT_VERSIONS["relevance_extract"],
            call_meta=main_call_meta,
        )
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        data = {}
        if m:
            try:
                parsed = json.loads(m.group())
                data = parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                pass
        update_model_call(
            main_call_meta.get("call_id"),
            json_valid=bool(data),
            result_status="success" if data else "invalid_output",
        )

        # ── JSON 解析失败时，向 DeepSeek 请求一次 JSON 修正 ──
        if not data:
            retry_prompt = (
                f"你之前对原文的提炼输出不是合法 JSON，请严格按照之前给出的 JSON Schema "
                f"重新输出一次，只输出纯 JSON，不要添加任何额外文字或 Markdown 标记。\n"
                f"原文摘要：{content_trunc[:300]}"
            )
            try:
                repair_call_meta = {}
                raw2 = _deepseek_chat(
                    retry_prompt,
                    max_tokens=600,
                    run_id=run_id,
                    article_id=article_id,
                    call_type="json_repair",
                    prompt_version=PROMPT_VERSIONS["json_repair"],
                    retry_count=1,
                    call_meta=repair_call_meta,
                )
                m2 = re.search(r'\{.*\}', raw2, re.DOTALL)
                if m2:
                    try:
                        parsed = json.loads(m2.group())
                        data = parsed if isinstance(parsed, dict) else {}
                    except json.JSONDecodeError:
                        pass
                update_model_call(
                    repair_call_meta.get("call_id"),
                    json_valid=bool(data),
                    result_status="success" if data else "invalid_output",
                )
            except Exception:
                pass

        # ── Relevance Gateway：默认视为不相关，仅当 LLM 明确标记 true 才放行 ──
        is_relevant = str(data.get("is_target_related", "")).lower()
        if is_relevant != "true":
            reason = (data.get("rejection_reason") or "JSON解析失败或LLM判定不相关").strip()
            print(f"    [相关性拦截] 原因：{reason[:80]}")
            return (
                "__NOT_RELEVANT__",
                "",
                None,
                {
                    "relevance_status": "blocked",
                    "rejection_code": "llm_not_relevant" if data else "invalid_structured_output",
                    "rejection_reason": reason,
                },
            )

        event_date_str = (data.get("date_of_event") or "").strip()
        event_date: datetime.date | None = None
        if event_date_str and event_date_str.lower() != "null" and re.match(r'\d{4}-\d{2}-\d{2}', event_date_str):
            event_date = _parse_date(event_date_str)
            if event_date:
                date_label = _date_label(event_date)

        commodity = (data.get("commodity_type") or "").strip()
        metrics   = (data.get("quantitative_metrics") or "").strip()
        status    = (data.get("market_or_policy_status") or "").strip()
        insight   = (data.get("analytical_summary") or "").strip()

        title_base = commodity if commodity and commodity != "综合" else level2[:20]
        title = f"{title_base}最新动态"[:40]

        lines = []
        if commodity:
            lines.append(f"商品品类：{commodity}")
        if metrics and metrics not in ("null", ""):
            lines.append(f"关键指标：{metrics}")
        if status and status not in ("null", "无法判断", ""):
            label = "期限结构" if is_pricing else "市场/政策状态"
            lines.append(f"{label}：{status}")
        if insight:
            lines.append(f"核心洞察：{insight}")
        summary = "\n".join(lines) if lines else _deepseek_fallback_zh(
            content,
            level2,
            run_id=run_id,
            article_id=article_id,
        )

        return (
            title,
            date_label + summary,
            event_date,
            {"relevance_status": "passed"},
        )

    except Exception as e:
        logging.error("[summarize_v2 崩溃] 大模型失去相关性判断能力，阻断入库: %s", str(e)[:80])
        return (
            "__NOT_RELEVANT__",
            "",
            None,
            {
                "relevance_status": "failed",
                "rejection_code": "model_processing_error",
                "rejection_reason": type(e).__name__,
            },
        )


# ── 字段名 ────────────────────────────────────────────────────────────────────────

FIELD_COLLECT_TIME  = "采集时间"
FIELD_TITLE         = "标题"
FIELD_LEVEL1        = "一级分类"
FIELD_SUMMARY       = "摘要"
FIELD_RESEARCH_CORE = "研究核心"
FIELD_SOURCE_URL    = "来源链接"
FIELD_LEVEL2        = "二级信息点"
FIELD_SITE_NAME     = "来源网站名称"
_MONITOR_SOURCE_ID  = "__monitoring_source_id"


# ── RSS 摄入引擎 ──────────────────────────────────────────────────────────────────

def _trafilatura_extract_with_timeout(html: str, timeout: int = 12, **kwargs) -> str:
    """在独立线程中运行 trafilatura.extract，超时则返回空字符串。"""
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(trafilatura.extract, html, **kwargs)
            return (future.result(timeout=timeout) or "").strip()
    except (ThreadTimeoutError, Exception):
        return ""


def _fetch_full_text(url: str, timeout: int = 20) -> str:
    """
    用 trafilatura 三层策略抓取文章完整正文，所有解析均带超时保护。
    返回正文字符串，失败返回空串。
    """
    try:
        resp = requests.get(url, headers=_FETCH_HEADERS, timeout=timeout, allow_redirects=True)
        resp.raise_for_status()
        if resp.encoding and resp.encoding.lower() in ('iso-8859-1', 'latin-1'):
            detected = resp.apparent_encoding
            if detected:
                resp.encoding = detected
        html = resp.text

        # 层1: precision（线程超时保护）
        text = _trafilatura_extract_with_timeout(
            html, timeout=12, include_comments=False, include_tables=True,
            no_fallback=False, favor_precision=True)
        # 层2: recall
        if not text or len(text) < 300:
            text2 = _trafilatura_extract_with_timeout(
                html, timeout=12, include_comments=False, include_tables=True,
                no_fallback=False, favor_recall=True)
            if text2 and len(text2) > len(text):
                text = text2
        # 层3: <p> 标签正则
        if not text or len(text) < 300:
            raw_paras = re.findall(r'<p[^>]*>(.*?)</p>', html, re.DOTALL | re.IGNORECASE)
            clean_paras = [re.sub(r'<[^>]+>', '', p).strip()
                           for p in raw_paras if len(re.sub(r'<[^>]+>', '', p).strip()) > 30]
            p_text = "\n".join(clean_paras)
            if p_text and len(p_text) > len(text):
                text = p_text

        return text.strip()
    except Exception:
        return ""


_CURRENT_YEAR     = TODAY.year
_CURRENT_YEAR_STR = str(_CURRENT_YEAR)
# 历史年份正则：匹配 2000-2025（早于当前年份的四位年份）
_HIST_YEAR_RE = re.compile(
    r'\b(20(?:0\d|1\d|2[0-' + str(_CURRENT_YEAR % 10 - 1) + r']))\b'
)


def _has_stale_year(text: str) -> str | None:
    """若 text 中含有历史年份返回该年份字符串，否则返回 None。"""
    m = _HIST_YEAR_RE.search(text)
    return m.group(1) if m else None


def _has_current_year(text: str) -> bool:
    """text 中是否含有当前年份字符串。"""
    return _CURRENT_YEAR_STR in text


def _parse_rss_feed(
    feed_url: str,
    cutoff: datetime.date,
    *,
    run_id: str | None = None,
    level1: str = "",
    level2: str = "",
) -> list[dict]:
    """
    解析单个 RSS feed，返回通过漏斗一（pubDate 时间拦截）的新鲜条目列表。
    全局缓存：同一 feed_url 在整个采集进程内只请求一次网络，后续直接复用原始条目列表，
    再按各信息点的 cutoff 单独过滤，节省网络 I/O 并规避限流。
    每条目格式: {title, link, pub_date, content, site_name}
    """
    # ── 全局缓存：原始条目（未按 cutoff 过滤，但已做年份硬拦截）────────────────
    feed_was_cached = feed_url in _RSS_FEED_CACHE
    if not feed_was_cached:
        raw_entries = []
        skipped_year = 0
        request_started = time.monotonic()
        try:
            resp = requests.get(feed_url, headers=_FETCH_HEADERS, timeout=15)
            resp.raise_for_status()
            feed = feedparser.parse(resp.text)
        except Exception as e:
            print(f"    [RSS 抓取失败] {feed_url[:60]}: {str(e)[:50]}")
            record_source_run(
                run_id,
                feed_url,
                status="failed",
                http_status=getattr(locals().get("resp"), "status_code", None),
                latency_ms=int((time.monotonic() - request_started) * 1000),
                failure_reason=e,
            )
            _RSS_FEED_META[feed_url] = {
                "status": "failed",
                "http_status": getattr(locals().get("resp"), "status_code", None),
                "latency_ms": int((time.monotonic() - request_started) * 1000),
                "entry_count": 0,
                "failure_reason": e,
            }
            _RSS_FEED_CACHE[feed_url] = []
            return []

        for entry in feed.get("entries", []):
            pub_raw  = entry.get("published") or entry.get("updated") or ""
            pub_date = _parse_date(pub_raw)
            link     = entry.get("link", "")
            if not link or _is_blocked(link):
                continue
            title = entry.get("title", "").strip()
            article_id = article_id_for_url(link)
            source_id = source_id_for_url(feed_url)
            track_article_event(
                run_id,
                article_id,
                "article_collected",
                source_id=source_id,
                level1=level1,
                level2=level2,
                status="collected",
                properties={
                    "publish_date": pub_date.isoformat() if pub_date else None,
                    "title": title[:160],
                },
            )

            # ── 物理年份硬拦截：链接或标题含历史年份则直接丢弃 ──────────────
            stale_year = _has_stale_year(link) or _has_stale_year(title)
            if stale_year:
                print(f"    [RSS 历史年份拦截] {stale_year} 年 丢弃历史条目: {title[:60]}")
                track_article_event(
                    run_id,
                    article_id,
                    "article_time_blocked",
                    source_id=source_id,
                    level1=level1,
                    level2=level2,
                    status="blocked",
                    reason_code="historical_year",
                    reason_detail=stale_year,
                )
                skipped_year += 1
                continue

            desc = re.sub(r'<[^>]+>', '', entry.get("summary", "")).strip()
            raw_entries.append({
                "title":    title,
                "link":     link,
                "pub_date": pub_date,
                "desc":     desc,
            })

        _RSS_FEED_CACHE[feed_url] = raw_entries
        _RSS_FEED_META[feed_url] = {
            "status": "success",
            "http_status": resp.status_code,
            "latency_ms": int((time.monotonic() - request_started) * 1000),
            "entry_count": len(feed.get("entries", [])),
            "failure_reason": None,
        }
        record_source_run(
            run_id,
            feed_url,
            status="success",
            http_status=resp.status_code,
            latency_ms=int((time.monotonic() - request_started) * 1000),
            entry_count=len(feed.get("entries", [])),
        )
        print(f"    [RSS 缓存] {feed_url[:55]} → {len(raw_entries)} 条原始条目"
              + (f"（年份拦截丢弃 {skipped_year} 条）" if skipped_year else ""))
    else:
        print(f"    [RSS 命中缓存] {feed_url[:55]}")
        cached_meta = _RSS_FEED_META.get(feed_url, {"status": "success"})
        record_source_run(
            run_id,
            feed_url,
            status=cached_meta.get("status", "success"),
            http_status=cached_meta.get("http_status"),
            latency_ms=cached_meta.get("latency_ms"),
            entry_count=cached_meta.get("entry_count", len(_RSS_FEED_CACHE[feed_url])),
            cache_hit=True,
            failure_reason=cached_meta.get("failure_reason"),
        )
        if cached_meta.get("status") == "failed":
            return []
        for raw in _RSS_FEED_CACHE[feed_url]:
            track_article_event(
                run_id,
                article_id_for_url(raw["link"]),
                "article_collected",
                source_id=source_id_for_url(feed_url),
                level1=level1,
                level2=level2,
                status="collected",
                properties={
                    "publish_date": (
                        raw["pub_date"].isoformat() if raw["pub_date"] else None
                    ),
                    "title": raw["title"][:160],
                    "cache_hit": True,
                },
            )

    # ── 漏斗一：pubDate 过滤 + 无时间戳保守抛弃 + 正文补全 ──────────────────
    entries = []
    fresh_entry_count = 0
    parse_success_count = 0
    for raw in _RSS_FEED_CACHE[feed_url]:
        pub_date = raw["pub_date"]
        link     = raw["link"]
        title    = raw["title"]
        article_id = article_id_for_url(link)
        source_id = source_id_for_url(feed_url)

        if pub_date is not None:
            # 有明确日期：走标准 cutoff 判断
            if not _is_fresh(pub_date, cutoff):
                print(f"    [拦截旧数据] 节点时间 {pub_date} 早于阈值，阻断入库")
                track_article_event(
                    run_id,
                    article_id,
                    "article_time_blocked",
                    source_id=source_id,
                    level1=level1,
                    level2=level2,
                    status="blocked",
                    reason_code="before_cutoff",
                    reason_detail=pub_date,
                    properties={"cutoff": cutoff.isoformat()},
                )
                continue
        else:
            # 无时间戳：仅当 URL 或标题含当年日期模式时才放行，否则保守丢弃
            if not (_is_recent_year(link) or _is_recent_year(title)):
                print(f"    [无时间戳保守拦截] 无法确认为当年内容，阻断: {title[:60]}")
                track_article_event(
                    run_id,
                    article_id,
                    "article_time_blocked",
                    source_id=source_id,
                    level1=level1,
                    level2=level2,
                    status="blocked",
                    reason_code="missing_timestamp",
                )
                continue

        track_article_event(
            run_id,
            article_id,
            "article_time_passed",
            source_id=source_id,
            level1=level1,
            level2=level2,
            status="passed",
            properties={"cutoff": cutoff.isoformat()},
        )
        fresh_entry_count += 1

        desc = raw["desc"]

        # 正文补全：desc < 300 字则触发 trafilatura 抓取原文
        if len(desc) >= 300:
            content = desc
        else:
            full = _fetch_full_text(link)
            content = full if len(full) > len(desc) else desc

        if not content or len(content) < 50:
            track_article_event(
                run_id,
                article_id,
                "content_parse_failed",
                source_id=source_id,
                level1=level1,
                level2=level2,
                status="failed",
                reason_code="content_too_short",
                properties={"content_length": len(content or "")},
            )
            continue

        track_article_event(
            run_id,
            article_id,
            "content_parse_completed",
            source_id=source_id,
            level1=level1,
            level2=level2,
            status="success",
            properties={"content_length": len(content)},
        )
        parse_success_count += 1

        if title and title not in content[:200]:
            content = f"{title}\n\n{content}"

        entries.append({
            "title":     title,
            "link":      link,
            "pub_date":  pub_date,
            "content":   content,
            "site_name": _site_name(link),
            "source_id": source_id,
        })

    source_meta = _RSS_FEED_META.get(feed_url, {"status": "success"})
    record_source_run(
        run_id,
        feed_url,
        status=source_meta.get("status", "success"),
        http_status=source_meta.get("http_status"),
        latency_ms=source_meta.get("latency_ms"),
        entry_count=source_meta.get("entry_count", len(_RSS_FEED_CACHE[feed_url])),
        fresh_entry_count=fresh_entry_count,
        parse_success_count=parse_success_count,
        cache_hit=feed_was_cached,
        failure_reason=source_meta.get("failure_reason"),
    )
    return entries


# ── 记录组装（含第二道熔断）─────────────────────────────────────────────────────────

def _build_record(level1, level2, research_core,
                  content, url, site_name,
                  pub_date: datetime.date | None,
                  cutoff: datetime.date | None = None,
                  run_id: str | None = None,
                  source_id: str | None = None) -> dict | None:
    """
    漏斗一已在 _parse_rss_feed 完成。
    本函数负责：
      - 调用 summarize_v2 提炼结构化 JSON
      - 漏斗二：event_date 由 LLM 明确提取后须严格等于 TODAY（官方月报走 cutoff 宽窗口）
    """
    article_id = article_id_for_url(url)
    date_label = _date_label(pub_date)
    title, summary, event_date, extraction_meta = summarize_v2(
        content,
        level2,
        research_core,
        date_label,
        run_id=run_id,
        article_id=article_id,
    )
    source_id = source_id or source_id_for_url(url)

    # Relevance Gateway：LLM 判定不相关，物理阻断入库
    if title == "__NOT_RELEVANT__":
        print(f"    [相关性拦截] 内容与研究主题无关，已丢弃")
        track_article_event(
            run_id,
            article_id,
            "relevance_blocked",
            source_id=source_id,
            level1=level1,
            level2=level2,
            status="blocked",
            reason_code=extraction_meta.get("rejection_code", "llm_not_relevant"),
            reason_detail=extraction_meta.get("rejection_reason"),
        )
        return None

    track_article_event(
        run_id,
        article_id,
        "relevance_passed",
        source_id=source_id,
        level1=level1,
        level2=level2,
        status="passed",
    )

    # ── 漏斗二：LLM 提取事件日期熔断 ─────────────────────────────────────
    if event_date is not None:
        if cutoff is not None:
            # 官方月报宽窗口：event_date >= cutoff
            if event_date < cutoff:
                print(f"    [拦截旧数据] 提取日期为 {event_date}，早于宽窗口阈值 {cutoff}，阻断入库")
                track_article_event(
                    run_id,
                    article_id,
                    "event_date_blocked",
                    source_id=source_id,
                    level1=level1,
                    level2=level2,
                    status="blocked",
                    reason_code="before_report_cutoff",
                    reason_detail=event_date,
                    properties={"cutoff": cutoff.isoformat()},
                )
                return None
        else:
            # 默认严格模式：仅放行今天
            if event_date != TODAY:
                print(f"    [非当天数据拦截] 提取日期为 {event_date}，阻断入库")
                track_article_event(
                    run_id,
                    article_id,
                    "event_date_blocked",
                    source_id=source_id,
                    level1=level1,
                    level2=level2,
                    status="blocked",
                    reason_code="not_today",
                    reason_detail=event_date,
                )
                return None

    track_article_event(
        run_id,
        article_id,
        "event_date_passed",
        source_id=source_id,
        level1=level1,
        level2=level2,
        status="passed",
        properties={"event_date": event_date.isoformat() if event_date else None},
    )

    return {
        FIELD_COLLECT_TIME:  TODAY_MS,
        FIELD_LEVEL1:        level1,
        FIELD_LEVEL2:        level2,
        FIELD_RESEARCH_CORE: research_core[:300] if research_core else "",
        FIELD_TITLE:         title,
        FIELD_SUMMARY:       summary,
        FIELD_SOURCE_URL:    url,
        FIELD_SITE_NAME:     site_name,
        _MONITOR_SOURCE_ID:  source_id,
    }


# ── 主采集逻辑（纯 RSS 驱动）─────────────────────────────────────────────────────────

def _collect_one(point: dict, *, run_id: str | None = None) -> list[dict]:
    level1        = point["level1"]
    level2        = point["level2"]
    research_core = point["research_core"]
    source_urls   = point["source_urls"]   # 全部为 RSS feed URL

    is_official_rpt = any(kw in level2 for kw in _OFFICIAL_REPORT_KEYWORDS)
    cutoff = _CUTOFF_30 if is_official_rpt else _CUTOFF

    records = []
    seen_links: set[str] = set()   # 同一轮去重，避免多个 feed 收录同一篇文章

    for feed_url in source_urls:
        entries = _parse_rss_feed(
            feed_url,
            cutoff,
            run_id=run_id,
            level1=level1,
            level2=level2,
        )
        for entry in entries:
            link = entry["link"]
            if link in seen_links:
                continue
            seen_links.add(link)

            rec = _build_record(
                level1, level2, research_core,
                entry["content"], link, entry["site_name"], entry["pub_date"],
                cutoff=cutoff,
                run_id=run_id,
                source_id=entry.get("source_id"),
            )
            if rec:
                records.append(rec)

    return records


# ── 全量采集入口 ──────────────────────────────────────────────────────────────────

def _load_existing_fingerprints() -> tuple[set[str], set[str]]:
    """
    拉取飞书表格当日已落库记录，返回 (existing_urls, existing_fingerprints)。
    existing_urls:         来源链接全集（精确去重）
    existing_fingerprints: 摘要前50字集合（内容高度雷同去重）
    拉取失败时返回空集合，降级为不去重（允许写入，不阻塞主流程）。
    """
    try:
        records = get_today_records(TODAY_STR)
    except Exception as e:
        print(f"  [Write-Guard] 拉取已落库记录失败，跳过去重: {e}")
        return set(), set()

    existing_urls: set[str] = set()
    existing_fingerprints: set[str] = set()
    for r in records:
        url = r.get(FIELD_SOURCE_URL, "")
        if isinstance(url, list):
            url = "".join(item.get("text", "") if isinstance(item, dict) else str(item) for item in url)
        url = str(url).strip()
        if url:
            existing_urls.add(url)

        summary = r.get(FIELD_SUMMARY, "")
        if isinstance(summary, list):
            summary = "".join(item.get("text", "") if isinstance(item, dict) else str(item) for item in summary)
        summary = str(summary).strip()
        fp = summary[:50]
        if fp:
            existing_fingerprints.add(fp)

    print(f"  [Write-Guard] 当日已落库：{len(records)} 条，URL指纹 {len(existing_urls)} 个，摘要指纹 {len(existing_fingerprints)} 个")
    return existing_urls, existing_fingerprints


def fetch_and_write(filter_level2: set | None = None, *, run_id: str | None = None):
    print(f"\n{'='*55}")
    label = "测试采集" if filter_level2 else "大宗商品信息采集"
    print(f"{label}  {TODAY_STR}")
    print(f"{'='*55}")

    owns_run = run_id is None
    if owns_run:
        run_id = start_run(
            TODAY_STR,
            "manual_test" if filter_level2 else "manual",
            environment="test" if filter_level2 else "production",
        )

    try:
        info_points = get_info_points()
    except Exception as exc:
        if owns_run:
            finish_run(run_id, status="failed", failed_count=1, error_stage="load_config", error=exc)
            publish_run_report(run_id)
        raise
    if filter_level2:
        info_points = [p for p in info_points if p.get("level2") in filter_level2]
    print(f"采集信息点: {len(info_points)} 条\n")

    # ── Write-Guard：拉取当日已落库指纹 ──────────────────────────────────
    existing_urls, existing_fingerprints = _load_existing_fingerprints()

    total_written  = 0
    total_failed   = 0
    total_deduped  = 0

    for point in info_points:
        level2 = point["level2"] or point["level1"] or "未知"
        try:
            records = _collect_one(point, run_id=run_id)
            for fields in records:
                url     = str(fields.get(FIELD_SOURCE_URL, "")).strip()
                summary = str(fields.get(FIELD_SUMMARY, "")).strip()
                fp      = summary[:50]
                article_id = article_id_for_url(url)
                source_id = str(fields.pop(_MONITOR_SOURCE_ID, "")) or source_id_for_url(url)

                # 校验一：URL 精确碰撞
                if url and url in existing_urls:
                    print(f"  [URL重复阻断] {url[:70]}")
                    track_article_event(
                        run_id,
                        article_id,
                        "url_duplicate_blocked",
                        source_id=source_id,
                        level1=fields.get(FIELD_LEVEL1),
                        level2=fields.get(FIELD_LEVEL2),
                        status="blocked",
                        reason_code="existing_url",
                    )
                    total_deduped += 1
                    continue

                # 校验二：摘要前50字指纹碰撞
                if fp and fp in existing_fingerprints:
                    print(f"  [内容高度雷同阻断] {fields.get(FIELD_TITLE, '')[:40]}")
                    track_article_event(
                        run_id,
                        article_id,
                        "content_duplicate_blocked",
                        source_id=source_id,
                        level1=fields.get(FIELD_LEVEL1),
                        level2=fields.get(FIELD_LEVEL2),
                        status="blocked",
                        reason_code="existing_content_fingerprint",
                    )
                    total_deduped += 1
                    continue

                track_article_event(
                    run_id,
                    article_id,
                    "dedupe_passed",
                    source_id=source_id,
                    level1=fields.get(FIELD_LEVEL1),
                    level2=fields.get(FIELD_LEVEL2),
                    status="passed",
                )

                try:
                    record_id = append_daily_record(fields)
                    # 写入成功后立即更新本次进程内的指纹集，阻断同批次重复
                    if url:
                        existing_urls.add(url)
                    if fp:
                        existing_fingerprints.add(fp)
                    site = fields.get(FIELD_SITE_NAME, "")[:16]
                    print(f"  ✓ {level2[:18]:<18}  [{site}]  {fields[FIELD_TITLE][:28]}")
                    track_article_event(
                        run_id,
                        article_id,
                        "record_written",
                        source_id=source_id,
                        level1=fields.get(FIELD_LEVEL1),
                        level2=fields.get(FIELD_LEVEL2),
                        status="success",
                        properties={"record_id": record_id},
                    )
                    total_written += 1
                except Exception as e:
                    print(f"  ✗ 写入失败 [{level2}]: {e}")
                    track_article_event(
                        run_id,
                        article_id,
                        "record_write_failed",
                        source_id=source_id,
                        level1=fields.get(FIELD_LEVEL1),
                        level2=fields.get(FIELD_LEVEL2),
                        status="failed",
                        reason_code="feishu_write_failed",
                        reason_detail=e,
                    )
                    total_failed += 1
        except Exception as e:
            print(f"  ✗ 采集失败 [{level2}]: {e}")
            total_failed += 1

    print(f"\n{'='*55}")
    print(f"完成：写入 {total_written} 条，去重阻断 {total_deduped} 条，失败 {total_failed} 条")
    print(f"{'='*55}\n")
    if owns_run:
        source_summary = get_source_summary(run_id)
        if source_summary["total_count"] and not source_summary["success_count"]:
            status = "failed"
        elif total_failed or source_summary["failed_count"]:
            status = "partial_success"
        elif total_written == 0:
            status = "empty_success"
        else:
            status = "success"
        finish_run(run_id, status=status, failed_count=total_failed)
        print(format_run_summary(get_run(run_id)))
        publish_run_report(run_id)
    return total_written, total_failed


if __name__ == "__main__":
    if "--test5" in sys.argv:
        _all_pts = __import__('feishu', fromlist=['get_info_points']).get_info_points()
        _top5 = {p["level2"] for p in _all_pts[:5]}
        fetch_and_write(filter_level2=_top5)
    else:
        fetch_and_write()
