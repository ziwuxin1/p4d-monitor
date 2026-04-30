<h1 align="center">P4D Monitor</h1>

<p align="center">
  <b>实时 Web 仪表盘 + 邮件告警 — 监控 Perforce P4D 服务器健康状态</b><br>
  <sub>跨网络访问(LAN + Tailscale) · 一键部署到 PVE Ubuntu VM</sub>
</p>

---

## 它干嘛的

```
你的 P4D 服务器(MAXs / Student / ...)
            ↓ SSH 探测(每 5 分钟)
┌─────────────────────────────┐
│   Monitor VM (PVE)          │
│   ────────────────────────  │
│   ✓ 服务在跑吗?             │
│   ✓ 端口监听吗?             │
│   ✓ License 没炸吧?         │
│   ✓ Counter 一致吗?         │
│   ✓ 上次本地 checkpoint?    │
│   ✓ NAS 备份新鲜吗?         │
│   ✓ 磁盘没满吧?             │
└─────────────────────────────┘
            ↓
   Web 仪表盘 (浏览器访问)
            ↓
   异常 → 邮件 → 你
```

## 特性

- 🌐 **Web 仪表盘** — 浏览器打开就能看,手机响应式
- 📧 **邮件告警** — 异常立即报 + 每天 09:00 摘要
- 🔒 **登录认证** — bcrypt 密码 + Flask session
- 🌍 **远程访问** — Tailscale Mesh VPN(不暴露公网)
- 📊 **历史数据** — SQLite 存 30 天,看趋势
- 🤖 **零侵入** — 监控目标只需开 SSH,什么都不用装
- ⚙️ **一键部署** — `sudo bash install.sh` 自动搞定
- 🔄 **自愈** — systemd 守护,挂了自动重启

## 监控指标

每 5 分钟探测一次,采集:

| 指标 | 怎么采 | 异常阈值 |
|------|------|---------|
| 服务运行 | `systemctl is-active p4d` | 不 active → 🚨 critical |
| 端口监听 | `ss -tlnp \| grep 1888` | 不监听 → 🚨 critical |
| License | `p4 info \| grep license` | none / 5-user → 🚨 critical |
| Counter | `p4 counter change` + `p4 changes -m1` | counter ≠ MAX/MAX+1 → ⚠️ warning |
| 本地 Checkpoint | `ls -t /opt/perforce/backups/checkpoint.*` | > 26h → ⚠️ warning |
| NAS 备份 | `ls -t /mnt/nas/.../checkpoints/` | > 26h → ⚠️ warning |
| NAS 挂载 | `mountpoint -q /mnt/nas/...` | 没挂载 → ⚠️ warning |
| 磁盘 | `df -P P4ROOT` | ≥80% warn / ≥90% critical |
| 错误日志 | `journalctl -u p4d -p err` 最近 1h | > 5 条 → ⚠️ |

## 快速部署

### 1. 在 PVE 上开一台 Monitor VM

```
Ubuntu 24.04 LTS, 1 核 / 1 GB RAM / 16 GB 磁盘
桥接到家里 LAN
```

### 2. 一键安装(在 Monitor VM 上)

```bash
# 用私有 token 拉脚本(repo 是 private)
export GH_TOKEN=ghp_你的_token
git clone https://${GH_TOKEN}@github.com/ziwuxin1/p4d-monitor.git
cd p4d-monitor
sudo bash install.sh
```

### 3. 按 install.sh 输出的提示完成 4 件事

1. 设登录密码 (生成 bcrypt hash)
2. 填 Gmail App Password
3. 把 SSH 公钥加到两台 P4D 的 `authorized_keys`
4. 启动服务

### 4. 浏览器访问

```
家里:    http://Monitor-VM-IP:8080
外面:    http://Tailscale-IP:8080  (装 Tailscale 后)
```

详见 [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)。

## 文档

| 文档 | 用途 |
|------|------|
| [DEPLOYMENT.md](docs/DEPLOYMENT.md) | 详细部署步骤 |
| [TAILSCALE-SETUP.md](docs/TAILSCALE-SETUP.md) | Tailscale 远程访问配置 |
| [EMAIL-SETUP.md](docs/EMAIL-SETUP.md) | Gmail App Password 申请 + 邮件配置 |

## 跟其他工程的关系

```
ssh-toolkit-linux       (P4D 服务器自己的部署/救援/自愈脚本)
        ↑ SSH 探测
p4d-monitor             (这个 repo - Web 监控仪表盘)
        ↑ 邮件告警
你                      (在外面也能远程看)
```

- **ssh-toolkit-linux**: 在 P4D 服务器上跑,负责自愈
- **p4d-monitor**: 在独立 VM 上跑,负责观测

互补,不冲突。

## License

私有 repo,仅作者本人使用。

---

*基于 ssh-toolkit-linux 的实战经验,采集逻辑参考 toolkit 的健康体检设计*
