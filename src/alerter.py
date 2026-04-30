"""告警邮件 — 异常立即报 + 每日摘要。"""
from __future__ import annotations

import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

from . import db


SEVERITY_EMOJI = {
    "info": "ℹ️",
    "warning": "⚠️",
    "critical": "🚨",
}


def _send_email(
    cfg: dict[str, Any],
    subject: str,
    body_html: str,
    body_text: str | None = None,
) -> bool:
    """底层 SMTP 发邮件,成功返回 True。"""
    email_cfg = cfg.get("email", {})
    if not email_cfg.get("enabled", False):
        return False
    if not all(email_cfg.get(k) for k in ("smtp_host", "smtp_user", "smtp_password", "to_addr")):
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = email_cfg.get("from_addr") or email_cfg["smtp_user"]
    msg["To"] = email_cfg["to_addr"]

    if body_text:
        msg.attach(MIMEText(body_text, "plain", "utf-8"))
    msg.attach(MIMEText(body_html, "html", "utf-8"))

    try:
        with smtplib.SMTP(email_cfg["smtp_host"], email_cfg.get("smtp_port", 587), timeout=15) as smtp:
            smtp.starttls()
            smtp.login(email_cfg["smtp_user"], email_cfg["smtp_password"])
            smtp.send_message(msg)
        return True
    except Exception as e:
        # 失败也记录,但不抛
        print(f"[alerter] 邮件发送失败: {type(e).__name__}: {e}")
        return False


def send_alert(
    cfg: dict[str, Any],
    server_name: str,
    severity: str,
    alert_key: str,
    title: str,
    detail: str,
    suggestion: str = "",
) -> bool:
    """单条告警。带 cooldown(同一 alert_key 30 分钟内不重发)。"""
    cooldown_min = cfg.get("alerts", {}).get("cooldown_minutes", 30)
    last = db.last_alert_of_key(server_name, alert_key)
    if last:
        last_ts = datetime.fromisoformat(last["timestamp"])
        now = datetime.now(timezone.utc)
        if (now - last_ts) < timedelta(minutes=cooldown_min):
            # cooldown 内 — 记 DB 但不发邮件
            db.insert_alert(server_name, severity, alert_key, title, sent=False)
            return False

    emoji = SEVERITY_EMOJI.get(severity, "")
    subject = f"{emoji} [P4D-{server_name}] {title}"

    body_html = f"""
    <html><body style="font-family:-apple-system,sans-serif;padding:20px;">
    <h2 style="color:{'#dc2626' if severity == 'critical' else '#f59e0b' if severity == 'warning' else '#2563eb'}">
        {emoji} {title}
    </h2>
    <table style="border-collapse:collapse;margin-top:10px;">
        <tr><td style="padding:5px 15px 5px 0;color:#666;">Server:</td><td><b>{server_name}</b></td></tr>
        <tr><td style="padding:5px 15px 5px 0;color:#666;">Severity:</td><td>{severity.upper()}</td></tr>
        <tr><td style="padding:5px 15px 5px 0;color:#666;">Time:</td><td>{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}</td></tr>
        <tr><td style="padding:5px 15px 5px 0;color:#666;">Alert:</td><td><code>{alert_key}</code></td></tr>
    </table>

    <h3 style="margin-top:20px;">详情</h3>
    <pre style="background:#f4f4f4;padding:12px;border-radius:4px;overflow-x:auto;">{detail}</pre>

    {f'<h3 style="margin-top:20px;">建议操作</h3><p>{suggestion}</p>' if suggestion else ''}

    <hr style="margin-top:30px;border:none;border-top:1px solid #eee;">
    <p style="color:#999;font-size:12px;">
        发件: P4D Monitor · 这是自动告警邮件 · 不要直接回复<br>
        当前 cooldown: {cooldown_min} 分钟内同一告警不重复发送
    </p>
    </body></html>
    """

    body_text = f"""
{emoji} {title}

Server: {server_name}
Severity: {severity.upper()}
Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}
Alert: {alert_key}

详情:
{detail}

{f'建议操作: {suggestion}' if suggestion else ''}

---
P4D Monitor 自动告警
    """.strip()

    sent = _send_email(cfg, subject, body_html, body_text)
    db.insert_alert(server_name, severity, alert_key, title, sent=sent)
    return sent


def send_recovery(
    cfg: dict[str, Any],
    server_name: str,
    alert_key: str,
    message: str,
) -> bool:
    """服务从异常恢复时发的"喜报"。"""
    if not cfg.get("alerts", {}).get("recovery_notification", True):
        return False

    subject = f"✅ [P4D-{server_name}] Recovered: {alert_key}"
    body_html = f"""
    <html><body style="font-family:-apple-system,sans-serif;padding:20px;">
    <h2 style="color:#10b981;">✅ 服务恢复正常</h2>
    <p><b>{server_name}</b> 之前的告警 <code>{alert_key}</code> 已恢复。</p>
    <p>{message}</p>
    <p style="color:#999;font-size:12px;margin-top:20px;">
        Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}
    </p>
    </body></html>
    """
    sent = _send_email(cfg, subject, body_html)
    db.insert_alert(server_name, "info", f"recovery:{alert_key}", message, sent=sent)
    return sent


def send_daily_summary(cfg: dict[str, Any], probes: list[dict[str, Any]]) -> bool:
    """每天 09:00 摘要邮件。"""
    now = datetime.now(timezone.utc)
    subject = f"📊 P4D Daily Health Report — {now.strftime('%Y-%m-%d')}"

    rows = []
    overall_ok = True
    for p in probes:
        health = p.get("health", "unknown")
        emoji = {"ok": "✅", "warning": "⚠️", "critical": "🚨", "down": "❌"}.get(health, "❓")
        if health != "ok":
            overall_ok = False

        license_str = (p.get("p4_license") or "—")[:60]
        ckpt_age = p.get("checkpoint_local_age_min")
        ckpt_str = f"{ckpt_age // 60}h {ckpt_age % 60}m 前" if ckpt_age else "未知"
        nas_age = p.get("checkpoint_nas_age_min")
        nas_str = f"{nas_age // 60}h {nas_age % 60}m 前" if nas_age else "未知"

        rows.append(f"""
        <tr>
            <td style="padding:10px;border:1px solid #ddd;"><b>{p.get('server_name', '?')}</b></td>
            <td style="padding:10px;border:1px solid #ddd;text-align:center;font-size:20px;">{emoji}</td>
            <td style="padding:10px;border:1px solid #ddd;">{license_str}</td>
            <td style="padding:10px;border:1px solid #ddd;">{ckpt_str}</td>
            <td style="padding:10px;border:1px solid #ddd;">{nas_str}</td>
            <td style="padding:10px;border:1px solid #ddd;text-align:center;">{p.get('disk_pct', '?')}%</td>
        </tr>
        """)

    overall_emoji = "✅" if overall_ok else "⚠️"
    body_html = f"""
    <html><body style="font-family:-apple-system,sans-serif;padding:20px;">
    <h2>{overall_emoji} P4D Daily Health Report</h2>
    <p>Time: {now.strftime('%Y-%m-%d %H:%M UTC')}</p>

    <table style="border-collapse:collapse;width:100%;margin-top:15px;">
        <thead>
            <tr style="background:#f4f4f4;">
                <th style="padding:10px;border:1px solid #ddd;">Server</th>
                <th style="padding:10px;border:1px solid #ddd;">Health</th>
                <th style="padding:10px;border:1px solid #ddd;">License</th>
                <th style="padding:10px;border:1px solid #ddd;">Last Checkpoint</th>
                <th style="padding:10px;border:1px solid #ddd;">Last NAS Backup</th>
                <th style="padding:10px;border:1px solid #ddd;">Disk %</th>
            </tr>
        </thead>
        <tbody>
            {''.join(rows)}
        </tbody>
    </table>

    <hr style="margin-top:30px;">
    <p style="color:#999;font-size:12px;">
        P4D Monitor 每日摘要 · {len(probes)} 台服务器
    </p>
    </body></html>
    """

    return _send_email(cfg, subject, body_html)


# ---- 状态机:决定何时发告警 ----

def evaluate_and_alert(
    cfg: dict[str, Any],
    current: dict[str, Any],
    previous: dict[str, Any] | None,
) -> list[str]:
    """对比新旧探测结果,决定发哪些告警。返回触发的 alert_key 列表。"""
    triggered: list[str] = []
    server = current["server_name"]
    thr = cfg.get("alerts", {}).get("thresholds", {})

    def alert(key: str, severity: str, title: str, detail: str, suggestion: str = "") -> None:
        if send_alert(cfg, server, severity, key, title, detail, suggestion):
            triggered.append(key)

    def recovery(key: str, msg: str) -> None:
        send_recovery(cfg, server, key, msg)

    # 1. 服务挂了
    if not current.get("service_active", False):
        alert(
            "service_down",
            "critical",
            "P4D 服务停止运行",
            f"systemctl is-active 返回: {current.get('service_state', 'unknown')}\n"
            f"端口 {current.get('port_address', '未知')} 监听: {current.get('port_listening')}",
            "SSH 进服务器跑 sudo systemctl status p4d 查看详情。"
            "如果是 license 问题,跑 toolkit 菜单 6 (Counter 救援)。",
        )
    elif previous and not previous.get("service_active", False):
        recovery("service_down", "P4D 服务已恢复运行")

    # 2. 端口不监听(服务跑着但 P4D 没起来)
    if current.get("service_active") and not current.get("port_listening"):
        alert(
            "port_not_listening",
            "critical",
            "P4D 服务跑着但端口不监听",
            f"systemctl 显示 active,但端口 {current.get('port_address', '?')} 没在监听。\n"
            "可能 ExecStartPost 失败或 P4D 内部异常。",
            "查 sudo journalctl -u p4d -n 100",
        )

    # 3. License 异常
    license_str = (current.get("p4_license") or "").lower()
    if license_str and ("none" in license_str or "5 user" in license_str):
        alert(
            "license_degraded",
            "critical",
            "License 降级到 5-user 免费版",
            f"当前 license: {current.get('p4_license')}",
            "可能 counter 漂移导致。跑 toolkit 菜单 6 Counter 救援。",
        )

    # 4. Checkpoint 太久没生成
    ckpt_age = current.get("checkpoint_local_age_min")
    threshold = thr.get("checkpoint_age_hours", 26) * 60
    if ckpt_age is not None and ckpt_age > threshold:
        alert(
            "checkpoint_stale",
            "warning",
            "本地 checkpoint 超过预期未生成",
            f"上次本地 checkpoint: {ckpt_age // 60}h {ckpt_age % 60}m 前\n"
            f"阈值: {threshold // 60}h",
            "查 cron 是否在跑: sudo grep CRON /var/log/syslog | grep p4d",
        )

    # 5. NAS 备份太久没成功
    nas_age = current.get("checkpoint_nas_age_min")
    threshold = thr.get("nas_age_hours", 26) * 60
    if nas_age is not None and nas_age > threshold:
        alert(
            "nas_stale",
            "warning",
            "NAS 备份超过预期未更新",
            f"NAS 上次 checkpoint: {nas_age // 60}h {nas_age % 60}m 前",
            "查 NAS 挂载: mountpoint /mnt/nas/p4d-backups/vm1\n"
            "查 rsync 日志: cat /mnt/nas/p4d-backups/vm1/checkpoints/last-rsync.log",
        )

    # 6. NAS 没挂载
    if not current.get("nas_mounted", False):
        alert(
            "nas_not_mounted",
            "warning",
            "NAS 没有挂载",
            f"/mnt/nas/p4d-backups/vm1 不是 mountpoint",
            "检查网络 + 群晖 NFS 服务 + sudo mount -a",
        )

    # 7. 磁盘
    disk_pct = current.get("disk_pct")
    crit = thr.get("disk_pct_critical", 90)
    warn = thr.get("disk_pct_warn", 80)
    if disk_pct is not None:
        if disk_pct >= crit:
            alert(
                "disk_critical",
                "critical",
                f"磁盘使用 {disk_pct}% (>={crit}%)",
                f"P4ROOT 所在盘已用 {disk_pct}%,剩 {(current.get('disk_free_kb') or 0) // 1024 // 1024} GB",
                "立即清理或扩盘,否则可能写入失败导致 P4D 崩溃。",
            )
        elif disk_pct >= warn:
            alert(
                "disk_warn",
                "warning",
                f"磁盘使用 {disk_pct}% (>={warn}%)",
                f"P4ROOT 所在盘已用 {disk_pct}%",
                "考虑清理旧数据或加盘。",
            )

    # 8. Counter 漂移
    if not current.get("counter_consistent", True):
        alert(
            "counter_drift",
            "warning",
            "Counter 不一致",
            f"counter change = {current.get('counter_change')}, "
            f"max change = {current.get('max_change')}\n"
            f"期望: counter == max 或 max+1",
            "跑 toolkit 菜单 6 Counter 救援。",
        )

    return triggered
