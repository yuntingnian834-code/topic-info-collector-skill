import schedule
import time
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from fetch_data import fetch_and_write
from reporter import send_daily_report


def run_pipeline():
    print("定时任务触发：开始每日采集+推送…")
    written, failed = fetch_and_write()
    if written > 0:
        send_daily_report()
    else:
        print("无新数据写入，跳过推送")


# 每天 05:00 采集并推送
schedule.every().day.at("05:00").do(run_pipeline)

print("定时任务已启动，每天 05:00 自动采集并推送日报")
print("按 Ctrl+C 停止")
print("立即运行一次…\n")

run_pipeline()

while True:
    schedule.run_pending()
    time.sleep(60)
