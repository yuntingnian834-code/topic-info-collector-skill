"""
更新信息点表 source_urls：全面切换为 RSS feed 链接。
"""
import sys, os, requests
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
from feishu import get_tenant_token, _headers, BASE_URL, INFO_BASE_TOKEN, INFO_TABLE_ID

load_dotenv()

SUPPLEMENTS = {
    # 实体大宗商品供需平衡表
    "rec27AC3qGbpaO": [
        "https://mining.com/feed/",
        "https://www.hellenicshippingnews.com/feed/",
        "https://feeds.bbci.co.uk/news/business/rss.xml",
        "https://www.scmp.com/rss/4/feed",
        "https://www.agrimoney.com/feed/",
        "https://splash247.com/feed/",
    ],

    # 高频交易所定价与 PRAs 现货价格发现机制
    "rec27AC3qGbsmM": [
        "https://mining.com/feed/",
        "https://www.hellenicshippingnews.com/feed/",
        "https://feeds.bbci.co.uk/news/business/rss.xml",
        "https://www.scmp.com/rss/4/feed",
        "https://news.research.stlouisfed.org/category/fred-announcements/feed/",
    ],

    # 宏观大宗商品综合价格指数
    "rec27AC3qGbuhN": [
        "https://mining.com/feed/",
        "https://feeds.bbci.co.uk/news/business/rss.xml",
        "https://www.scmp.com/rss/4/feed",
        "https://news.research.stlouisfed.org/category/fred-announcements/feed/",
        "https://www.hellenicshippingnews.com/feed/",
        "https://www.ft.com/rss/home/uk",
    ],

    # 运价指数、散货吨位与船舶利用效率
    "rec27AC3qGbw4B": [
        "https://gcaptain.com/feed/",
        "https://www.hellenicshippingnews.com/feed/",
        "https://splash247.com/feed/",
        "https://www.freightwaves.com/news/feed",
        "https://feeds.bbci.co.uk/news/business/rss.xml",
    ],

    # 全球海运吨英里变动与货物重定向
    "rec27AC3qGbypd": [
        "https://gcaptain.com/feed/",
        "https://www.hellenicshippingnews.com/feed/",
        "https://splash247.com/feed/",
        "https://www.freightwaves.com/news/feed",
        "https://www.maritime-executive.com/rss",
        "https://www.ft.com/rss/home/uk",
    ],

    # 关键航运咽喉通道实时流量与过境实物吨位
    "rec27AC3qGbApC": [
        "https://gcaptain.com/feed/",
        "https://www.hellenicshippingnews.com/feed/",
        "https://splash247.com/feed/",
        "https://feeds.bbci.co.uk/news/world/rss.xml",
        "https://www.scmp.com/rss/4/feed",
    ],

    # 关键枢纽港口拥堵、运营效率与 AIS 定位异常监测
    "rec27AC3qGbCOW": [
        "https://gcaptain.com/feed/",
        "https://www.hellenicshippingnews.com/feed/",
        "https://splash247.com/feed/",
        "https://www.freightwaves.com/news/feed",
        "https://feeds.bbci.co.uk/news/business/rss.xml",
    ],

    # 海陆多式联运、东西向管线与电网跨境互换
    "rec27AC3qGbEyA": [
        "https://mining.com/feed/",
        "https://www.hellenicshippingnews.com/feed/",
        "https://feeds.bbci.co.uk/news/business/rss.xml",
        "https://www.scmp.com/rss/4/feed",
        "https://www.ft.com/rss/home/uk",
    ],

    # 双向出口管制、对美禁运与"0.1%规则"治外法权
    "rec27AC3qGbGsi": [
        "https://mining.com/feed/",
        "https://www.scmp.com/rss/4/feed",
        "https://feeds.bbci.co.uk/news/world/rss.xml",
        "https://www.ft.com/rss/home/uk",
        "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml",
    ],

    # 国家安全物资应急储备政策
    "rec27AC3qGbIlL": [
        "https://mining.com/feed/",
        "https://feeds.bbci.co.uk/news/world/rss.xml",
        "https://www.scmp.com/rss/4/feed",
        "https://www.ft.com/rss/home/uk",
        "https://news.research.stlouisfed.org/category/fred-announcements/feed/",
    ],

    # 跨国绿色关税、环境合规与能源转型双轨规制
    "recvmqrr4iHWP6": [
        "https://mining.com/feed/",
        "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml",
        "https://feeds.bbci.co.uk/news/business/rss.xml",
        "https://www.scmp.com/rss/4/feed",
        "https://www.ft.com/rss/home/uk",
    ],

    # 粮食主权、大食物观与"统筹进口与国内生产"政策
    "recvmqrr4iHWkk": [
        "https://mining.com/feed/",
        "https://feeds.bbci.co.uk/news/world/rss.xml",
        "https://www.scmp.com/rss/4/feed",
        "https://www.ft.com/rss/home/uk",
        "https://www.hellenicshippingnews.com/feed/",
        "https://splash247.com/feed/",
    ],
}


def update_record_urls(record_id: str, urls: list):
    token = get_tenant_token()
    url = f"{BASE_URL}/bitable/v1/apps/{INFO_BASE_TOKEN}/tables/{INFO_TABLE_ID}/records/{record_id}"
    payload = {"fields": {"source_urls": "\n".join(urls)}}
    resp = requests.put(url, headers=_headers(token), json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"更新失败 {record_id}: {data}")


def main():
    print("更新信息点数据源（RSS feeds）...\n")
    for record_id, urls in SUPPLEMENTS.items():
        try:
            update_record_urls(record_id, urls)
            print(f"  ok {record_id}  ({len(urls)} 个RSS)")
        except Exception as e:
            print(f"  ✗ {record_id}: {e}")
    print("\n完成")


if __name__ == "__main__":
    main()
