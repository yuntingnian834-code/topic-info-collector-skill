import os
import requests
from dotenv import load_dotenv

load_dotenv()

APP_ID = os.getenv("FEISHU_APP_ID")
APP_SECRET = os.getenv("FEISHU_APP_SECRET")

# 每日快报表
DAILY_BASE_TOKEN = os.getenv("DAILY_BASE_TOKEN", "KIC3b8SNba5pXZsudrQcfmxWnEe")
DAILY_TABLE_ID = os.getenv("DAILY_TABLE_ID", "tblwtLlIic6uPDoj")

# 信息点表
INFO_BASE_TOKEN = os.getenv("INFO_BASE_TOKEN", "MCl7bXz1Saw78MsUmT1cnmMUntb")
INFO_TABLE_ID = os.getenv("INFO_TABLE_ID", "tblkPWxHeAaShcuA")

BASE_URL = "https://open.feishu.cn/open-apis"


def get_tenant_token():
    url = f"{BASE_URL}/auth/v3/tenant_access_token/internal"
    try:
        resp = requests.post(url, json={"app_id": APP_ID, "app_secret": APP_SECRET}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"获取 tenant token 失败: {data}")
        return data["tenant_access_token"]
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"飞书认证请求失败: {e}") from e


def _headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def get_info_points():
    """读取信息点表所有记录，返回 list[dict]"""
    token = get_tenant_token()
    records = []
    page_token = None

    while True:
        url = f"{BASE_URL}/bitable/v1/apps/{INFO_BASE_TOKEN}/tables/{INFO_TABLE_ID}/records"
        params = {"page_size": 100}
        if page_token:
            params["page_token"] = page_token

        resp = requests.get(url, headers=_headers(token), params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        if data.get("code") != 0:
            raise RuntimeError(f"读取信息点表失败: {data}")

        items = data["data"]["items"]
        for item in items:
            f = item["fields"]
            level1 = f.get("level1", "")
            level2 = f.get("level2", "")
            # 跳过空行
            if not level1 and not level2:
                continue
            records.append({
                "record_id": item["record_id"],
                "level1": level1,
                "level2": level2,
                "research_core": f.get("research_core", ""),
                "source_urls": _parse_urls(f.get("source_urls", "")),
            })

        if not data["data"].get("has_more"):
            break
        page_token = data["data"]["page_token"]

    return records


def _parse_urls(raw):
    """解析 source_urls 字段，支持 JSON 数组或换行分隔"""
    if not raw:
        return []
    if isinstance(raw, list):
        return [u.strip() for u in raw if u.strip()]
    raw = str(raw).strip()
    if raw.startswith("["):
        import json
        try:
            return [u.strip() for u in json.loads(raw) if u.strip()]
        except Exception:
            pass
    return [u.strip() for u in raw.splitlines() if u.strip()]


def append_daily_record(fields: dict):
    """向每日快报表写入一条记录，fields 使用字段ID"""
    token = get_tenant_token()
    url = f"{BASE_URL}/bitable/v1/apps/{DAILY_BASE_TOKEN}/tables/{DAILY_TABLE_ID}/records"
    payload = {"fields": fields}
    resp = requests.post(url, headers=_headers(token), json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"写入每日快报失败: {data}")
    return data["data"]["record"]["record_id"]


def get_today_records(date_str: str):
    """读取今日快报表所有记录（用于日报生成），date_str 格式 yyyy-MM-dd"""
    import datetime as _dt
    # 计算当天 0 点和 23:59:59 的毫秒时间戳
    day = _dt.datetime.strptime(date_str, "%Y-%m-%d")
    start_ms = int(day.timestamp() * 1000)
    end_ms = int((day + _dt.timedelta(days=1)).timestamp() * 1000)

    token = get_tenant_token()
    records = []
    page_token = None

    while True:
        url = f"{BASE_URL}/bitable/v1/apps/{DAILY_BASE_TOKEN}/tables/{DAILY_TABLE_ID}/records"
        params = {"page_size": 100}
        if page_token:
            params["page_token"] = page_token

        resp = requests.get(url, headers=_headers(token), params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        if data.get("code") != 0:
            raise RuntimeError(f"读取每日快报失败: {data}")

        for item in data["data"]["items"]:
            f = item["fields"]
            ts = f.get("采集时间")
            if ts is not None:
                try:
                    ts = int(ts)
                    if start_ms <= ts < end_ms:
                        records.append(f)
                except (ValueError, TypeError):
                    pass
            else:
                # 无时间戳的记录也纳入（兼容手动填写的记录）
                records.append(f)

        if not data["data"].get("has_more"):
            break
        page_token = data["data"]["page_token"]

    return records
