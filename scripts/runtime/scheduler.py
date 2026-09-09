import schedule
import time
import os
import sys
import subprocess

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PIPELINE_SCRIPT = os.path.join(SCRIPT_DIR, "daily_pipeline.py")


def run_pipeline():
    print("定时任务触发：开始采集、飞书推送与网站更新…")
    # 每日启动一个新进程，确保日期、24h 窗口和采集时间不会停留在调度器启动日。
    process_env = os.environ.copy()
    process_env["SKILL_TRIGGER_TYPE"] = "schedule"
    completed = subprocess.run(
        [sys.executable, PIPELINE_SCRIPT],
        check=False,
        env=process_env,
    )
    if completed.returncode != 0:
        print(f"自动更新失败，退出码：{completed.returncode}")


# 无论服务器系统时区是什么，都在北京时间 05:00 运行。
schedule.every().day.at("05:00", "Asia/Shanghai").do(run_pipeline)

print("定时任务已启动，每天北京时间 05:00 自动采集、推送并更新网站")
print("按 Ctrl+C 停止")
print("立即运行一次…\n")

run_pipeline()

while True:
    schedule.run_pending()
    time.sleep(60)
