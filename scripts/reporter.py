import os
import sys
import datetime
import requests
from collections import defaultdict
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from feishu import get_today_records, DAILY_BASE_TOKEN

load_dotenv()

WEBHOOK_URL      = os.getenv("FEISHU_WEBHOOK_URL")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

F_LEVEL1  = "一级分类"
F_LEVEL2  = "二级信息点"
F_TITLE   = "标题"
F_SUMMARY = "摘要"
F_SOURCE  = "来源链接"

BITABLE_URL = f"https://feishu.cn/base/{DAILY_BASE_TOKEN}"


# ── 工具函数 ──────────────────────────────────────────────────────────────────────

def _get_field(record: dict, *keys) -> str:
    for k in keys:
        v = record.get(k)
        if v:
            if isinstance(v, list):
                return "".join(
                    item.get("text", "") if isinstance(item, dict) else str(item)
                    for item in v
                )
            return str(v)
    return ""


def _send_webhook(payload: dict):
    if not WEBHOOK_URL:
        raise RuntimeError("未配置 FEISHU_WEBHOOK_URL，请在 .env 中添加")
    resp = requests.post(WEBHOOK_URL, json=payload, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") not in (0, None):
        raise RuntimeError(f"Webhook 返回错误: {data}")
    return data


def _deepseek_executive_summary(insights: str) -> str:
    """调用 DeepSeek 生成 ≤100 字的今日核心看点。失败时返回空字符串。"""
    if not DEEPSEEK_API_KEY or not insights.strip():
        return ""
    try:
        prompt = (
            "将以下几条大宗商品与宏观洞察总结为100字以内的【今日宏观与大宗看点】，"
            "语言专业高浓度，禁止出现英文，直接输出正文不要标题：\n\n" + insights
        )
        resp = requests.post(
            "https://api.deepseek.com/chat/completions",
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                     "Content-Type": "application/json"},
            json={"model": "deepseek-chat",
                  "messages": [{"role": "user", "content": prompt}],
                  "max_tokens": 200, "temperature": 0.3},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"  [DeepSeek 看点生成失败] {str(e)[:60]}")
        return ""


# ── 卡片构建 ──────────────────────────────────────────────────────────────────────

def build_report(records: list[dict], date_str: str) -> dict:
    total        = len(records)
    today_display = datetime.datetime.strptime(date_str, "%Y-%m-%d").strftime("%Y年%m月%d日")

    # ── Executive Summary：取前 6 条的核心洞察拼接后送 DeepSeek ──────────────
    insight_snippets = []
    for r in records[:8]:
        summary = _get_field(r, F_SUMMARY)
        level2  = _get_field(r, F_LEVEL2)
        if summary:
            snippet = summary[:120].replace("\n", " ")
            insight_snippets.append(f"[{level2}] {snippet}" if level2 else snippet)

    ai_summary = _deepseek_executive_summary("\n".join(insight_snippets))

    # ── 分组 ──────────────────────────────────────────────────────────────────
    grouped: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        level1 = _get_field(r, F_LEVEL1) or "其他"
        grouped[level1].append(r)

    # ── 卡片 elements ────────────────────────────────────────────────────────
    elements = []

    # 顶部总览 + AI 看点
    overview_lines = [f"📊 **今日共采集 {total} 条高纯度洞察**"]
    if ai_summary:
        overview_lines.append(f"\n💡 **今日核心看点**：\n{ai_summary}")
    elements.append({
        "tag": "div",
        "text": {"tag": "lark_md", "content": "\n".join(overview_lines)}
    })
    elements.append({"tag": "hr"})

    category_icons = {
        "市场基本面": "📈",
        "航运物流":   "🚢",
        "港口与航道": "⚓",
        "宏观政策":   "🏛️",
    }

    for level1, items in grouped.items():
        icon = category_icons.get(level1, "📌")

        elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**{icon} {level1}**（{len(items)} 条）"
            }
        })

        for item in items[:3]:
            level2  = _get_field(item, F_LEVEL2)
            title   = _get_field(item, F_TITLE)
            summary = _get_field(item, F_SUMMARY)
            source  = _get_field(item, F_SOURCE)

            # 摘要截断
            short_summary = summary[:150] + ("…" if len(summary) > 150 else "")

            header = f"**📌 [{level2}] {title}**" if level2 else f"**📌 {title}**"
            lines  = [header]
            if short_summary:
                lines.append(f" • 摘要：{short_summary}")
            if source:
                lines.append(f" 🔗 [查看源网页]({source})")

            elements.append({
                "tag": "div",
                "text": {"tag": "lark_md", "content": "\n".join(lines)}
            })

        if len(items) > 3:
            elements.append({
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f" *…还有 {len(items) - 3} 条高价值数据，详见多维表格*"
                }
            })

        elements.append({"tag": "hr"})

    # 底部跳转按钮
    elements.append({
        "tag": "action",
        "actions": [{
            "tag":  "button",
            "text": {"tag": "plain_text", "content": "查看飞书多维完整面板"},
            "type": "primary",
            "url":  BITABLE_URL,
        }]
    })

    return {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title":    {"tag": "plain_text", "content": f"🌐 大宗商品与宏观每日快报 · {today_display}"},
                "template": "blue",
            },
            "elements": elements,
        }
    }


# ── 推送入口 ──────────────────────────────────────────────────────────────────────

def send_daily_report(date_str: str = None):
    if date_str is None:
        date_str = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime("%Y-%m-%d")

    print(f"读取 {date_str} 的快报记录…")
    records = get_today_records(date_str)
    print(f"共 {len(records)} 条记录")

    if not records:
        print("[Skip Push] 今日无新增数据，跳过简报推送")
        return

    payload = build_report(records, date_str)
    _send_webhook(payload)
    print(f"日报推送成功（{len(records)} 条）")


if __name__ == "__main__":
    date_arg = sys.argv[1] if len(sys.argv) > 1 else None
    send_daily_report(date_arg)
