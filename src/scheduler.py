"""APScheduler — 定时探测 + 每日摘要 + DB 清理。"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from . import alerter, db, prober


log = logging.getLogger("p4d-monitor.scheduler")


# 探测结果缓存(给 Web 用,避免每次访问都查 DB)
_latest_lock = threading.Lock()
_latest_probes: dict[str, dict[str, Any]] = {}


def get_latest_probes() -> dict[str, dict[str, Any]]:
    """线程安全读取最新探测结果。"""
    with _latest_lock:
        return dict(_latest_probes)


def _probe_one_server(cfg: dict[str, Any], srv: dict[str, Any]) -> None:
    """探测单台 + 持久化 + 评估告警。"""
    name = srv["name"]
    ssh_key = cfg["probe"]["ssh_key_path"]
    timeout = cfg["probe"].get("ssh_timeout", 10)

    log.info("Probing %s ...", name)
    result = prober.probe_server(srv, ssh_key, timeout)
    result_dict = prober.to_dict(result)

    # 取上次探测对比(用于检测状态变化 → recovery 通知)
    previous = db.latest_probe(name)

    # 持久化新探测结果
    db.insert_probe(result_dict)

    # 更新内存缓存
    with _latest_lock:
        _latest_probes[name] = result_dict

    # 评估 + 发告警
    try:
        triggered = alerter.evaluate_and_alert(cfg, result_dict, previous)
        if triggered:
            log.warning("[%s] 触发告警: %s", name, triggered)
    except Exception as e:
        log.exception("[%s] 告警评估异常: %s", name, e)


def probe_all(cfg: dict[str, Any]) -> None:
    """探测所有配置的服务器。"""
    for srv in cfg.get("servers", []):
        try:
            _probe_one_server(cfg, srv)
        except Exception as e:
            log.exception("Probe %s failed: %s", srv.get("name"), e)


def daily_summary_job(cfg: dict[str, Any]) -> None:
    """每天 09:00 跑一次,把最新状态发邮件。"""
    log.info("Sending daily summary...")
    probes = []
    for srv in cfg.get("servers", []):
        latest = db.latest_probe(srv["name"])
        if latest:
            probes.append(latest)
    if probes:
        ok = alerter.send_daily_summary(cfg, probes)
        log.info("Daily summary sent: %s", ok)


def cleanup_job() -> None:
    """每天清理 30 天以上的探测记录。"""
    deleted = db.cleanup_old_probes(keep_days=30)
    log.info("Cleaned up %d old probe records", deleted)


def start(cfg: dict[str, Any]) -> BackgroundScheduler:
    """启动调度器。"""
    db.init_db()

    scheduler = BackgroundScheduler(
        timezone=cfg.get("timezone", "America/Los_Angeles"),
        job_defaults={
            "coalesce": True,         # 错过的任务合并执行
            "max_instances": 1,       # 同一任务不并发
            "misfire_grace_time": 60,
        },
    )

    # 1. 定时探测
    interval = cfg["probe"].get("interval_seconds", 300)
    scheduler.add_job(
        probe_all,
        trigger=IntervalTrigger(seconds=interval),
        args=[cfg],
        id="probe_all",
        name="probe all servers",
        next_run_time=datetime.now(timezone.utc),  # 启动后立刻跑一次
    )

    # 2. 每日摘要
    summary_time = cfg.get("alerts", {}).get("daily_summary_time", "09:00")
    hour, minute = map(int, summary_time.split(":"))
    scheduler.add_job(
        daily_summary_job,
        trigger=CronTrigger(hour=hour, minute=minute),
        args=[cfg],
        id="daily_summary",
        name="daily health summary",
    )

    # 3. 每天清理旧数据
    scheduler.add_job(
        cleanup_job,
        trigger=CronTrigger(hour=4, minute=0),
        id="cleanup",
        name="cleanup old probes",
    )

    scheduler.start()
    log.info(
        "Scheduler started: probe every %ds, daily summary at %s",
        interval, summary_time,
    )
    return scheduler


def stop(scheduler: BackgroundScheduler) -> None:
    scheduler.shutdown(wait=False)
