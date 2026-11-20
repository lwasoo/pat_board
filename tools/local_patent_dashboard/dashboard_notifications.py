"""WeCom delivery and Windows notification scheduling."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
import dashboard_config as settings
from dashboard_config import TASK_NAME
from dashboard_database import connect
from dashboard_validation import clean_text


def default_notification_config() -> dict:
    return {"enabled": False, "webhook": "", "scheduleTime": "17:30"}



def load_notification_config() -> dict:
    config = default_notification_config()
    if settings.NOTIFICATION_CONFIG_PATH.exists():
        try:
            saved = json.loads(settings.NOTIFICATION_CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                config.update(saved)
        except (OSError, ValueError):
            pass
    return config



def validate_wecom_webhook(value: str) -> str:
    webhook = clean_text(value)
    if not webhook:
        raise ValueError("请填写企业微信群机器人的 Webhook 地址。")
    parsed = urllib.parse.urlsplit(webhook)
    if parsed.scheme != "https" or parsed.hostname != "qyapi.weixin.qq.com":
        raise ValueError("Webhook 必须使用企业微信官方地址 qyapi.weixin.qq.com。")
    if parsed.path != "/cgi-bin/webhook/send" or not urllib.parse.parse_qs(parsed.query).get("key"):
        raise ValueError("Webhook 地址格式不正确，请从企业微信群机器人资料中完整复制。")
    return webhook



def normalized_notification_config(payload: dict) -> dict:
    current = load_notification_config()
    webhook = clean_text(payload.get("webhook")) or clean_text(current.get("webhook"))
    enabled = bool(payload.get("enabled"))
    schedule_time = clean_text(payload.get("scheduleTime"), "17:30")
    try:
        datetime.strptime(schedule_time, "%H:%M")
    except ValueError as exc:
        raise ValueError("每日推送时间格式不正确。") from exc
    if webhook:
        webhook = validate_wecom_webhook(webhook)
    if enabled and not webhook:
        raise ValueError("启用自动推送前，请先填写企业微信 Webhook。")
    return {"enabled": enabled, "webhook": webhook, "scheduleTime": schedule_time}



def save_notification_config(config: dict) -> None:
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    temporary = settings.NOTIFICATION_CONFIG_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(settings.NOTIFICATION_CONFIG_PATH)



def scheduled_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable).resolve()}" --check-reminders'
    return f'"{Path(sys.executable).resolve()}" "{settings.APP_SCRIPT}" --check-reminders'



def configure_notification_task(enabled: bool, schedule_time: str) -> dict:
    if os.name != "nt":
        return {"scheduled": False, "message": "自动定时任务仅支持 Windows。"}
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if enabled:
        command = ["schtasks.exe", "/Create", "/SC", "DAILY", "/TN", TASK_NAME,
                   "/TR", scheduled_command(), "/ST", schedule_time, "/F"]
        result = subprocess.run(command, capture_output=True, text=True, encoding="mbcs",
                                errors="replace", creationflags=flags, timeout=20)
        if result.returncode:
            detail = clean_text(result.stderr or result.stdout, "Windows 拒绝创建定时任务。")
            raise ValueError(f"定时任务创建失败：{detail}")
        return {"scheduled": True, "message": f"每天 {schedule_time} 自动检查。"}
    subprocess.run(["schtasks.exe", "/Delete", "/TN", TASK_NAME, "/F"], capture_output=True,
                   text=True, encoding="mbcs", errors="replace", creationflags=flags, timeout=20)
    return {"scheduled": False, "message": "自动推送已关闭。"}



def send_wecom_markdown(webhook: str, content: str) -> None:
    webhook = validate_wecom_webhook(webhook)
    body = json.dumps({"msgtype": "markdown", "markdown": {"content": content}},
                      ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(webhook, data=body, method="POST",
                                     headers={"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(request, timeout=12) as response:
        result = json.loads(response.read().decode("utf-8"))
    if int(result.get("errcode", -1)) != 0:
        raise ValueError(f"企业微信发送失败：{result.get('errmsg') or result.get('errcode')}")



def reminder_stage(days_left: int) -> tuple[str, str]:
    if days_left < 0:
        return "overdue", f"逾期 {-days_left} 天"
    if days_left == 0:
        return "due-0", "今天到期"
    if days_left <= 7:
        return "due-7", f"剩余 {days_left} 天"
    if days_left <= 30:
        return "due-30", f"剩余 {days_left} 天"
    return "due-90", f"剩余 {days_left} 天"



def record_notification_run(status: str, count: int, message: str) -> None:
    with connect() as conn:
        conn.execute("INSERT INTO notification_runs(status,reminder_count,message) VALUES(?,?,?)",
                     (status, count, clean_text(message)[:500]))



def run_due_notifications() -> dict:
    config = load_notification_config()
    if not config.get("enabled"):
        raise ValueError("自动推送尚未启用。")
    webhook = validate_wecom_webhook(config.get("webhook", ""))
    today = datetime.now().date()
    horizon = (today + timedelta(days=90)).isoformat()
    with connect() as conn:
        rows = [dict(row) for row in conn.execute("""SELECT f.id, f.fee_type AS feeType,
        f.due_date AS dueDate, f.amount, f.currency, p.application_number AS applicationNumber,
        p.title AS patentTitle FROM fees f JOIN patents p ON p.id=f.patent_id
        WHERE f.status NOT IN ('已缴费','无需缴费','费用记录') AND f.due_date<>'' AND f.due_date<=?
        ORDER BY f.due_date,f.id""", (horizon,))]
        reminders = []
        for row in rows:
            try:
                days_left = (datetime.strptime(row["dueDate"], "%Y-%m-%d").date() - today).days
            except (TypeError, ValueError):
                continue
            key, label = reminder_stage(days_left)
            key = f"{row['dueDate']}:{key}"
            sent = conn.execute("SELECT 1 FROM notification_deliveries WHERE fee_id=? AND reminder_key=?",
                                (row["id"], key)).fetchone()
            if not sent:
                row.update({"reminderKey": key, "label": label, "daysLeft": days_left})
                reminders.append(row)
    if not reminders:
        record_notification_run("success", 0, "没有新的临期提醒。")
        return {"sent": 0, "message": "没有新的临期提醒。"}
    visible = reminders[:10]
    lines = ["## 专利缴费临期提醒", f"> 检查时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}"]
    for item in visible:
        color = "warning" if item["daysLeft"] >= 0 else "red"
        amount = "" if item["amount"] is None else f" · {item['currency']} {float(item['amount']):.2f}"
        lines.append(f"\n<font color=\"{color}\">{item['label']}</font> **{item['patentTitle']}**")
        lines.append(f"> {item['applicationNumber']} · {item['feeType']}{amount} · 截止 {item['dueDate']}")
    if len(reminders) > len(visible):
        lines.append(f"\n> 另有 {len(reminders) - len(visible)} 条，请打开本机看板查看。")
    try:
        send_wecom_markdown(webhook, "\n".join(lines))
        with connect() as conn:
            conn.executemany("INSERT OR IGNORE INTO notification_deliveries(fee_id,reminder_key) VALUES(?,?)",
                             [(row["id"], row["reminderKey"]) for row in reminders])
        record_notification_run("success", len(reminders), f"已推送 {len(reminders)} 条提醒。")
        return {"sent": len(reminders), "message": f"已推送 {len(reminders)} 条提醒。"}
    except Exception as exc:
        record_notification_run("failed", 0, str(exc))
        raise



def notification_status() -> dict:
    config = load_notification_config()
    webhook = clean_text(config.get("webhook"))
    with connect() as conn:
        last = conn.execute("SELECT run_at AS runAt,status,reminder_count AS reminderCount,message FROM notification_runs ORDER BY id DESC LIMIT 1").fetchone()
    return {"enabled": bool(config.get("enabled")), "scheduleTime": config.get("scheduleTime", "09:00"),
            "webhookConfigured": bool(webhook), "webhookMasked": ("••••••" + webhook[-6:]) if webhook else "",
            "lastRun": dict(last) if last else None}
