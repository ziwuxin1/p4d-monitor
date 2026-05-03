"""SSH 探测 — 远程进 P4D 服务器跑命令,采集状态。

Strategy:
- 一台服务器一个 SSH 连接(短连接,跑完即关)
- 所有命令组合成一个 bash -c 减少往返
- 解析输出生成结构化 ProbeResult
- 采集失败 → 单独标记,不影响其他服务器
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any

import paramiko


# 单条命令打包,用 ===== 分隔便于解析
PROBE_SCRIPT = r"""
set +e
echo "===SECTION:hostname==="
hostname
echo "===SECTION:uptime==="
uptime -p
echo "===SECTION:service_active==="
systemctl is-active p4d
echo "===SECTION:service_status==="
systemctl is-failed p4d
echo "===SECTION:port_listening==="
ss -tlnp 2>/dev/null | grep ":__P4PORT__ " | head -1
echo "===SECTION:p4_info==="
/opt/perforce/bin/p4 -p localhost:__P4PORT__ info 2>/dev/null
echo "===SECTION:p4_counter==="
if [[ -f /opt/perforce/.p4_admin_passwd ]]; then
    /opt/perforce/bin/p4 -p localhost:__P4PORT__ -u admin login < /opt/perforce/.p4_admin_passwd >/dev/null 2>&1
    /opt/perforce/bin/p4 -p localhost:__P4PORT__ -u admin counter change 2>/dev/null
fi
echo "===SECTION:p4_max_change==="
/opt/perforce/bin/p4 -p localhost:__P4PORT__ -u admin changes -m 1 2>/dev/null | awk 'NR==1 && $1=="Change" { print $2 }'
echo "===SECTION:checkpoint_local==="
ls -t __BACKUP_DIR__/checkpoint.* 2>/dev/null | grep -v '\.md5$' | head -1
echo "===SECTION:checkpoint_local_age==="
last_ckpt=$(ls -t __BACKUP_DIR__/checkpoint.* 2>/dev/null | grep -v '\.md5$' | head -1)
if [[ -n "$last_ckpt" ]]; then
    echo $(( ($(date +%s) - $(stat -c %Y "$last_ckpt")) / 60 ))
fi
echo "===SECTION:checkpoint_nas==="
ls -t __NAS_DIR__/checkpoints/checkpoint.* 2>/dev/null | grep -v '\.md5$' | head -1
echo "===SECTION:checkpoint_nas_age==="
nas_ckpt=$(ls -t __NAS_DIR__/checkpoints/checkpoint.* 2>/dev/null | grep -v '\.md5$' | head -1)
if [[ -n "$nas_ckpt" ]]; then
    echo $(( ($(date +%s) - $(stat -c %Y "$nas_ckpt")) / 60 ))
fi
echo "===SECTION:nas_mounted==="
mountpoint -q __NAS_DIR__ && echo "yes" || echo "no"
echo "===SECTION:disk_p4root==="
df -P __P4ROOT__ 2>/dev/null | awk 'NR==2 {print $2,$3,$4,$5}'
echo "===SECTION:depot_size==="
du -sh __P4ROOT__/*/ 2>/dev/null | grep -v '^[0-9.]*K' | head -20
echo "===SECTION:errors_recent==="
journalctl -u p4d --since '1h ago' -p err --no-pager 2>/dev/null | wc -l
echo "===SECTION:cpu_count==="
nproc
echo "===SECTION:loadavg==="
cat /proc/loadavg
echo "===SECTION:meminfo==="
grep -E "^(MemTotal|MemAvailable|MemFree|Buffers|Cached):" /proc/meminfo
echo "===SECTION:cpu_pct==="
# 取 0.3 秒 sample 算 CPU 利用率(idle 反推)
read -r _ user nice system idle iowait irq softirq steal _ < /proc/stat
total1=$((user + nice + system + idle + iowait + irq + softirq + steal))
idle1=$((idle + iowait))
sleep 0.3
read -r _ user nice system idle iowait irq softirq steal _ < /proc/stat
total2=$((user + nice + system + idle + iowait + irq + softirq + steal))
idle2=$((idle + iowait))
dt=$((total2 - total1))
di=$((idle2 - idle1))
[[ $dt -gt 0 ]] && echo $(( 100 * (dt - di) / dt )) || echo 0
echo "===SECTION:p4d_proc==="
# p4d 主进程的 RSS / CPU
ps -o pid,rss,pcpu,etime,comm -C p4d --no-headers 2>/dev/null | head -5
echo "===SECTION:monitor_show==="
if [[ -f /opt/perforce/.p4_admin_passwd ]]; then
    /opt/perforce/bin/p4 -p localhost:__P4PORT__ -u admin login < /opt/perforce/.p4_admin_passwd >/dev/null 2>&1
    /opt/perforce/bin/p4 -p localhost:__P4PORT__ -u admin monitor show -ael 2>/dev/null
fi
echo "===END==="
""".strip()


@dataclass
class ProbeResult:
    server_name: str
    timestamp: str  # ISO 8601 UTC
    success: bool  # 探测过程本身成功(SSH 通了 + 命令跑了)
    error: str | None = None  # 失败原因

    # 服务状态
    hostname: str | None = None
    uptime: str | None = None
    service_active: bool = False  # systemctl is-active = active
    service_state: str | None = None  # active / inactive / failed
    port_listening: bool = False
    port_address: str | None = None  # e.g. "0.0.0.0:1888"

    # P4D 状态
    p4_version: str | None = None
    p4_license: str | None = None
    p4_case_handling: str | None = None  # sensitive / insensitive
    p4_server_address: str | None = None
    counter_change: int | None = None
    max_change: int | None = None
    counter_consistent: bool = True  # counter == max_change + 1 或 max_change

    # 备份状态
    checkpoint_local_path: str | None = None
    checkpoint_local_age_min: int | None = None
    checkpoint_nas_path: str | None = None
    checkpoint_nas_age_min: int | None = None
    nas_mounted: bool = False

    # 资源
    disk_total_kb: int | None = None
    disk_used_kb: int | None = None
    disk_free_kb: int | None = None
    disk_pct: int | None = None
    depot_sizes: list[dict[str, str]] = field(default_factory=list)

    # 日志
    recent_errors_1h: int = 0

    # 系统资源(Phase 2 性能监控)
    cpu_count: int | None = None
    cpu_pct: int | None = None         # 0-100
    load_1m: float | None = None
    load_5m: float | None = None
    load_15m: float | None = None
    mem_total_mb: int | None = None
    mem_used_mb: int | None = None     # MemTotal - MemAvailable
    mem_pct: int | None = None         # 0-100
    p4d_rss_mb: int | None = None      # p4d 主进程内存
    p4d_cpu_pct: float | None = None   # p4d 主进程 CPU

    # 当前活跃操作(p4 monitor show -ael 解析结果)
    active_ops: list[dict[str, Any]] = field(default_factory=list)

    # 整体健康评级
    health: str = "unknown"  # ok / warning / critical / down


def probe_server(srv_cfg: dict[str, Any], ssh_key_path: str, ssh_timeout: int = 10) -> ProbeResult:
    """探测单台 P4D 服务器。永不抛异常,失败时返回 success=False 的 result。"""
    name = srv_cfg["name"]
    result = ProbeResult(
        server_name=name,
        timestamp=datetime.now(timezone.utc).isoformat(),
        success=False,
    )

    try:
        # SSH 连接
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        try:
            pkey = paramiko.Ed25519Key.from_private_key_file(ssh_key_path)
        except paramiko.SSHException:
            pkey = paramiko.RSAKey.from_private_key_file(ssh_key_path)

        client.connect(
            hostname=srv_cfg["host"],
            port=srv_cfg.get("ssh_port", 22),
            username=srv_cfg.get("ssh_user", "root"),
            pkey=pkey,
            timeout=ssh_timeout,
            banner_timeout=ssh_timeout,
            auth_timeout=ssh_timeout,
        )

        # 渲染探测脚本
        script = (
            PROBE_SCRIPT
            .replace("__P4PORT__", str(srv_cfg.get("p4d_port", 1888)))
            .replace("__P4ROOT__", srv_cfg.get("p4d_root", "/opt/perforce/servers/master"))
            .replace("__BACKUP_DIR__", srv_cfg.get("backup_dir", "/opt/perforce/backups"))
            .replace("__NAS_DIR__", srv_cfg.get("nas_dir", "/mnt/nas/p4d-backups/vm1"))
        )

        stdin, stdout, stderr = client.exec_command(script, timeout=ssh_timeout * 3)
        output = stdout.read().decode("utf-8", errors="replace")
        client.close()

        # 解析
        _parse_probe_output(output, result)
        result.success = True
        result.health = _classify_health(result)

    except paramiko.AuthenticationException:
        result.error = "SSH 认证失败 (检查 ssh_key 是否加到目标机器的 authorized_keys)"
        result.health = "down"
    except (paramiko.SSHException, OSError, TimeoutError) as e:
        result.error = f"SSH 连接失败: {type(e).__name__}: {e}"
        result.health = "down"
    except Exception as e:
        result.error = f"探测异常: {type(e).__name__}: {e}"
        result.health = "down"

    return result


def _parse_probe_output(output: str, result: ProbeResult) -> None:
    """把 ===SECTION:xxx=== 切开的输出解析到 ProbeResult。"""
    sections: dict[str, str] = {}
    current_key: str | None = None
    current_lines: list[str] = []

    for line in output.splitlines():
        m = re.match(r"^===SECTION:(\w+)===$", line)
        if m:
            if current_key is not None:
                sections[current_key] = "\n".join(current_lines).strip()
            current_key = m.group(1)
            current_lines = []
        elif line == "===END===":
            if current_key is not None:
                sections[current_key] = "\n".join(current_lines).strip()
            current_key = None
        elif current_key is not None:
            current_lines.append(line)

    # 填字段
    result.hostname = sections.get("hostname") or None
    result.uptime = sections.get("uptime") or None

    state = sections.get("service_active", "").strip()
    result.service_state = state or None
    result.service_active = state == "active"

    port_line = sections.get("port_listening", "").strip()
    result.port_listening = bool(port_line)
    if port_line:
        # ss 输出: LISTEN 0 128 0.0.0.0:1888 0.0.0.0:* users:...
        parts = port_line.split()
        for p in parts:
            if ":" in p and not p.startswith("users:"):
                result.port_address = p
                break

    # p4 info 解析
    info = sections.get("p4_info", "")
    for line in info.splitlines():
        if line.startswith("Server version:"):
            result.p4_version = line.split(":", 1)[1].strip()
        elif line.startswith("Server license:"):
            result.p4_license = line.split(":", 1)[1].strip()
        elif line.startswith("Server address:"):
            result.p4_server_address = line.split(":", 1)[1].strip()
        elif line.startswith("Case Handling:") or line.startswith("Case-handling:"):
            result.p4_case_handling = line.split(":", 1)[1].strip().lower()

    # Counter
    counter_str = sections.get("p4_counter", "").strip()
    if counter_str.isdigit():
        result.counter_change = int(counter_str)

    max_str = sections.get("p4_max_change", "").strip()
    if max_str.isdigit():
        result.max_change = int(max_str)

    if result.counter_change is not None and result.max_change is not None:
        # counter 应该 == max+1 (新提交准备用) 或 == max (刚 reset)
        result.counter_consistent = result.counter_change in (
            result.max_change,
            result.max_change + 1,
        )

    # Checkpoint local
    result.checkpoint_local_path = sections.get("checkpoint_local") or None
    age_str = sections.get("checkpoint_local_age", "").strip()
    if age_str.isdigit():
        result.checkpoint_local_age_min = int(age_str)

    # Checkpoint NAS
    result.checkpoint_nas_path = sections.get("checkpoint_nas") or None
    age_str = sections.get("checkpoint_nas_age", "").strip()
    if age_str.isdigit():
        result.checkpoint_nas_age_min = int(age_str)

    # NAS 挂载
    result.nas_mounted = sections.get("nas_mounted", "").strip() == "yes"

    # Disk
    disk_line = sections.get("disk_p4root", "").strip()
    if disk_line:
        parts = disk_line.split()
        if len(parts) >= 4:
            try:
                result.disk_total_kb = int(parts[0])
                result.disk_used_kb = int(parts[1])
                result.disk_free_kb = int(parts[2])
                result.disk_pct = int(parts[3].rstrip("%"))
            except (ValueError, IndexError):
                pass

    # Depot sizes
    depot_lines = sections.get("depot_size", "")
    for line in depot_lines.splitlines():
        line = line.strip()
        if not line:
            continue
        # "393G    /opt/perforce/servers/master/maxs_internal/"
        parts = line.split()
        if len(parts) >= 2:
            size = parts[0]
            path = parts[1].rstrip("/")
            depot_name = path.split("/")[-1]
            result.depot_sizes.append({"name": depot_name, "size": size, "path": path})

    # Errors
    err_str = sections.get("errors_recent", "0").strip()
    if err_str.isdigit():
        result.recent_errors_1h = int(err_str)

    # CPU count
    cpu_count_str = sections.get("cpu_count", "").strip()
    if cpu_count_str.isdigit():
        result.cpu_count = int(cpu_count_str)

    # Load average — /proc/loadavg 格式: 0.12 0.34 0.56 1/234 12345
    loadavg = sections.get("loadavg", "").strip().split()
    if len(loadavg) >= 3:
        try:
            result.load_1m = float(loadavg[0])
            result.load_5m = float(loadavg[1])
            result.load_15m = float(loadavg[2])
        except ValueError:
            pass

    # /proc/meminfo
    mem = {}
    for line in sections.get("meminfo", "").splitlines():
        parts = line.split(":")
        if len(parts) == 2:
            v = parts[1].strip().split()
            if v and v[0].isdigit():
                mem[parts[0].strip()] = int(v[0])  # kB
    if "MemTotal" in mem:
        result.mem_total_mb = mem["MemTotal"] // 1024
        if "MemAvailable" in mem:
            used_kb = mem["MemTotal"] - mem["MemAvailable"]
            result.mem_used_mb = used_kb // 1024
            if mem["MemTotal"] > 0:
                result.mem_pct = round(100 * used_kb / mem["MemTotal"])

    # CPU pct
    cpu_pct_str = sections.get("cpu_pct", "").strip()
    if cpu_pct_str.lstrip("-").isdigit():
        result.cpu_pct = max(0, min(100, int(cpu_pct_str)))

    # p4d 主进程 RSS / CPU(取第一个 p4d 进程,通常就是 server daemon)
    proc_lines = sections.get("p4d_proc", "").strip().splitlines()
    if proc_lines:
        parts = proc_lines[0].split()
        # PID RSS PCPU ETIME COMM
        if len(parts) >= 5:
            try:
                result.p4d_rss_mb = int(parts[1]) // 1024  # rss in kB
                result.p4d_cpu_pct = float(parts[2])
            except (ValueError, IndexError):
                pass

    # Active operations (p4 monitor show -ael 输出)
    # 格式举例:
    #   12345 R user_zhang 00:00:42 submit -d 'updated character'
    #   12346 R user_li    00:01:23 sync //depot/...
    # 字段: PID 状态 用户 运行时长 命令 [参数]
    monitor_text = sections.get("monitor_show", "").strip()
    if monitor_text:
        # admin 自己跑的 monitor show 也会出现在结果里 — 需要过滤掉
        for line in monitor_text.splitlines():
            m = re.match(
                r"^\s*(\d+)\s+(\w+)\s+(\S+)\s+(\d+:\d+:\d+)\s+(\S+)\s*(.*)$",
                line,
            )
            if not m:
                continue
            pid, status, user, runtime, cmd, args = m.groups()
            # 跳过 monitor 自己
            if cmd == "monitor":
                continue
            # 解析时长成秒
            h, mi, s = runtime.split(":")
            runtime_sec = int(h) * 3600 + int(mi) * 60 + int(s)
            result.active_ops.append({
                "pid": int(pid),
                "status": status,
                "user": user,
                "runtime": runtime,
                "runtime_sec": runtime_sec,
                "command": cmd,
                "args": args.strip(),
            })


def _classify_health(r: ProbeResult) -> str:
    """根据探测结果决定整体健康等级。"""
    # 关键服务挂了 → critical
    if not r.service_active:
        return "critical"
    if not r.port_listening:
        return "critical"

    # license 异常 → critical
    if r.p4_license and ("none" in r.p4_license.lower() or "5 user" in r.p4_license.lower()):
        return "critical"

    # 备份很久没跑 → warning(可恢复但需关注)
    warnings = []
    if r.checkpoint_local_age_min is not None and r.checkpoint_local_age_min > 26 * 60:
        warnings.append("checkpoint_stale")
    if r.checkpoint_nas_age_min is not None and r.checkpoint_nas_age_min > 26 * 60:
        warnings.append("nas_stale")
    if r.disk_pct is not None and r.disk_pct >= 90:
        return "critical"
    if r.disk_pct is not None and r.disk_pct >= 80:
        warnings.append("disk_warn")
    if not r.counter_consistent:
        warnings.append("counter_drift")

    if warnings:
        return "warning"
    return "ok"


def to_dict(r: ProbeResult) -> dict[str, Any]:
    """转 dict(给 JSON 序列化用)。"""
    return asdict(r)
