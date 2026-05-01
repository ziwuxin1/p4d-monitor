# P4D 实时监控与告警系统

> 基于 **Uptime Kuma + Discord** 搭建的 Perforce 服务器实时状态监控方案，对外提供公开状态页，对内提供 Discord 实时告警。

## 目录

- [架构概览](#架构概览)
- [部署 Uptime Kuma（Ubuntu + Docker）](#部署-uptime-kumaubuntu--docker)
- [配置监控项](#配置监控项)
- [配置公开状态页](#配置公开状态页)
- [配置 Discord 告警频道](#配置-discord-告警频道)
- [配置自定义 Webhook 通知](#配置自定义-webhook-通知)
- [测试与验证](#测试与验证)
- [运维与维护](#运维与维护)
- [常见问题](#常见问题)

---

## 架构概览

```
┌──────────────────────┐         ┌──────────────────────┐
│ Perforce-MAXs (P4D)  │         │ Perforce-Student     │
│ perforce-maxs.ddns.me│         │ perforce-maxs.ddns.me│
│ :30888               │         │ :xxxxx               │
└──────────┬───────────┘         └──────────┬───────────┘
           │                                │
           │  TCP Port 探测（每 60 秒）       │
           ▼                                ▼
        ┌──────────────────────────────────────┐
        │      Uptime Kuma (Docker, Ubuntu)    │
        │  - 持续 TCP 连接探测                  │
        │  - 心跳条 / 可用率统计                │
        │  - 公开状态页 /status/p4d            │
        └──────────────┬───────────────────────┘
                       │
                       │  状态变更（DOWN / UP）
                       ▼
        ┌──────────────────────────────────────┐
        │   Discord Webhook（自定义 JSON）      │
        │   推送到 #🚨ＰＥＲＦＯＲＣＥ-服务器实时状态 │
        └──────────────────────────────────────┘
```

**对外暴露**：
- 公开状态页：`https://status.maxsacademy.com/status/p4d`
- Discord 频道：`#🚨｜ＰＥＲＦＯＲＣＥ-服务器实时状态`（只读）

**对内不暴露**：
- 内网 P4D 真实 IP（一律走 DDNS）
- Uptime Kuma 后台

---

## 部署 Uptime Kuma（Ubuntu + Docker）

### 1. 安装 Docker（如未安装）

```bash
sudo apt update
sudo apt install -y docker.io docker-compose
sudo systemctl enable --now docker
sudo usermod -aG docker $USER
# 重新登录或新开 shell 让组生效
```

### 2. 启动 Uptime Kuma

```bash
# 创建数据目录
sudo mkdir -p /opt/uptime-kuma/data

# 启动容器
docker run -d \
  --name uptime-kuma \
  --restart=always \
  -p 3001:3001 \
  -v /opt/uptime-kuma/data:/app/data \
  louislam/uptime-kuma:1
```

### 3. 首次访问

浏览器打开 `http://服务器IP:3001`，按引导：

1. 设置管理员账号 + 密码
2. 选择语言：**简体中文**

> ⚠️ **重要**：Uptime Kuma 在内网部署时，监控公网 DDNS 需要路由器支持 **Hairpin NAT（NAT 回流）**。如不支持，建议把 Uptime Kuma 部署到外部 VPS。

### 4. 反向代理（可选，推荐）

为了让公开状态页用域名访问（`https://status.maxsacademy.com`），用 Nginx 或 Caddy 反代：

**Caddy 示例**（最简）：

```caddy
status.maxsacademy.com {
    reverse_proxy localhost:3001
}
```

**Nginx 示例**：

```nginx
server {
    listen 443 ssl http2;
    server_name status.maxsacademy.com;

    ssl_certificate     /etc/letsencrypt/live/status.maxsacademy.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/status.maxsacademy.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:3001;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

---

## 配置监控项

为每个 P4D 实例创建一个 **TCP Port** 监控。

### 添加监控项

1. 左上角 → **添加新的监控项**
2. 填写以下字段：

| 字段 | Perforce-MAXs | Perforce-Student |
|---|---|---|
| **监控类型** | TCP Port | TCP Port |
| **显示名称** | `Perforce-MAXs` | `Perforce-Student` |
| **主机名** | `perforce-maxs.ddns.me` | `perforce-maxs.ddns.me` |
| **端口** | `30888` | `<对应端口>` |
| **心跳间隔** | `60`（秒） | `60` |
| **重试次数** | `3` | `3` |
| **心跳重试间隔** | `60` | `60` |
| **重复发送通知间隔** | `48` | `48` |

> 🔒 **安全提示**：**始终用 DDNS 域名 + 公网端口**，不要直接填内网 IP（`192.168.x.x`），否则 Discord 告警 / 公开状态页都会泄露内网信息。

### 推荐参数说明

- **心跳间隔 60 秒**：平衡实时性和服务器压力
- **重试 3 次**：避免网络抖动误报，约 4 分钟才正式 DOWN
- **重复通知间隔 48**：DOWN 持续期间每 48 个心跳（48 分钟）重复推送一次提醒

---

## 配置公开状态页

### 创建状态页

1. 设置 → **状态页** → **新状态页**
2. 填写：

| 字段 | 值 |
|---|---|
| **路径** | `p4d` |
| **标题** | `MAXs Perforce 服务器实时状态` |
| **底部自定义文本** | `状态由 MAXs Education 监控` |
| **主题** | 自动 |

### 添加监控项到状态页

1. 编辑状态页 → **添加分组**：`服务`
2. 把 `Perforce-MAXs` 和 `Perforce-Student` 拖入分组
3. 保存

### 访问

公开 URL：`https://status.maxsacademy.com/status/p4d`

匿名用户可看到：
- 整体状态（All Systems Operational / Issue）
- 每个监控项的心跳条（24 小时内每分钟一格）
- 可用率百分比
- 自动每 5 分钟刷新

---

## 配置 Discord 告警频道

### 1. 创建频道

在 Discord 服务器对应分类下创建文字频道：

```
🚨｜ＰＥＲＦＯＲＣＥ-服务器实时状态｜𝐏𝐄𝐑𝐅𝐎𝐑𝐂𝐄-𝐒𝐄𝐑𝐕𝐄𝐑-𝐒𝐓𝐀𝐓𝐔𝐒
```

> 📝 大写英文部分用 **Unicode 数学粗体**（𝐏𝐄𝐑𝐅𝐎𝐑𝐂𝐄）—— Discord 文字频道会把普通英文转小写，必须用 Unicode 替代字符才能保留视觉上的大写效果。

### 2. 频道权限（@everyone）

只读频道：成员只能看，不能发。

| 权限 | 设置 | 说明 |
|---|---|---|
| 查看频道 | ✅ | 必须开 |
| 发送消息 | ❌ | 禁止发言 |
| 在子区内发送消息 | ❌ | |
| 创建公共子区 | ❌ | |
| 创建私密子区 | ❌ | |
| 添加附件 | ❌ | |
| 添加反应 | ❌ | 防止刷表情 |
| 提及 @everyone | ❌ | 防止滥用提醒 |
| 管理消息 | ❌ | |
| 标注消息 | ❌ | |
| 管理子区 | ❌ | |
| **阅读消息历史** | ✅ | **必须开**，否则看不到推送 |
| 发送 TTS 消息 | ❌ | |
| 发送语音消息 | ❌ | |
| 创建投票 | ❌ | |
| 使用 APP 命令 | ❌ | |
| 管理频道 | ❌ | |
| 管理权限 | ❌ | |
| **管理 webhook** | ❌ | **重要**，防止删 webhook |

### 3. 创建 Webhook

1. 频道 → 编辑频道 → **整合（Integrations）** → **Webhook** → **新 Webhook**
2. 命名 `Uptime Kuma`
3. 复制 **Webhook URL** 备用（格式 `https://discord.com/api/webhooks/<ID>/<TOKEN>`）

> 🔒 **Webhook URL 视为机密**：泄露后任何人都能伪造消息发到频道，定期轮换。

---

## 配置自定义 Webhook 通知

为了支持中文标题和自定义样式，使用 Uptime Kuma 的 **Webhook** 通知类型（不是内置 Discord 类型）。

### 创建通知

1. 设置 → **通知** → **新建通知**
2. 填写：

| 字段 | 值 |
|---|---|
| **通知类型** | Webhook |
| **显示名称** | `Discord-Perforce-自定义` |
| **Post URL** | 粘贴 Discord Webhook URL |
| **请求体** | Custom Body |
| **附加 Header** | None |

### 自定义 JSON 模板

粘贴到「请求体」字段：

```json
{
  "username": "🚨 MAXs Perforce 服务器实时状态 🚨",
  "embeds": [
    {
      {% if heartbeatJSON %}{% if heartbeatJSON.status == 1 %}
      "title": "🟢 {{ monitorJSON.name }} 服务已恢复正常",
      "color": 3066993,
      {% else %}
      "title": "🔴 {{ monitorJSON.name }} 服务异常告警",
      "color": 15158332,
      {% endif %}{% else %}
      "title": "🔔 测试通知",
      "color": 5814783,
      {% endif %}
      "fields": [
        {
          "name": "📌 服务名称",
          "value": "{{ monitorJSON.name }}",
          "inline": true
        },
        {
          "name": "🌐 服务地址",
          "value": "{{ monitorJSON.hostname }}:{{ monitorJSON.port }}",
          "inline": true
        },
        {
          "name": "📝 状态详情",
          "value": "{{ msg }}"
        }
      ],
      "footer": {
        "text": "MAXs Academy 监控系统"
      },
      "timestamp": "{{ heartbeatJSON.time }}"
    }
  ]
}
```

### 颜色表（embed `color` 字段，十进制）

| 颜色 | 值 |
|---|---|
| 🔴 红 | `15158332` |
| 🟢 绿 | `3066993` |
| 🟡 黄 | `15844367` |
| 🔵 蓝 | `3447003` |
| 🟣 紫 | `10181046` |

### 启用

- 勾选 **默认开启**：新建监控项自动用此通知
- 勾选 **应用到所有现有监控项**：一键绑到 `Perforce-MAXs` 和 `Perforce-Student`

---

## 测试与验证

### 触发 DOWN 告警（不影响生产）

1. 编辑某个监控项
2. **重试次数** 临时改为 `0`（默认 3，加快触发）
3. **端口** 改成无效值（如 `9999`）
4. **保存** → 等约 60 秒
5. Discord 频道应收到 🔴 红色告警

### 恢复

1. **端口** 改回正确值
2. **重试次数** 改回 `3`
3. **保存** → 等约 60 秒
4. Discord 频道应收到 🟢 绿色恢复通知

### 验证清单

- [ ] 公开状态页可访问 `https://status.maxsacademy.com/status/p4d`
- [ ] 状态页心跳条正常滚动
- [ ] DOWN 告警能推送到 Discord
- [ ] UP 恢复通知能推送到 Discord
- [ ] Discord 消息颜色正确（红 / 绿）
- [ ] 消息中无内网 IP 泄露
- [ ] Discord 频道成员无法发言
- [ ] Discord 频道成员能看到历史消息

---

## 运维与维护

### 备份 Uptime Kuma 数据

所有配置存于 `/opt/uptime-kuma/data/kuma.db`（SQLite）。

```bash
# 手动备份
sudo cp /opt/uptime-kuma/data/kuma.db /backup/kuma-$(date +%F).db

# 定时备份（crontab -e）
0 3 * * * cp /opt/uptime-kuma/data/kuma.db /backup/kuma-$(date +\%F).db
```

> ⚠️ **不要把 `kuma.db` 提交到 GitHub**，里面有 Webhook URL 等敏感信息。

### 升级 Uptime Kuma

```bash
docker stop uptime-kuma
docker rm uptime-kuma
docker pull louislam/uptime-kuma:1
# 用相同 docker run 命令重新启动（数据卷不变）
docker run -d \
  --name uptime-kuma \
  --restart=always \
  -p 3001:3001 \
  -v /opt/uptime-kuma/data:/app/data \
  louislam/uptime-kuma:1
```

### 查看容器日志

```bash
docker logs -f --tail=100 uptime-kuma
```

### 添加新监控项

1. Uptime Kuma → 添加新监控项
2. 类型选 TCP Port / HTTP / Ping 等
3. 填 DDNS + 端口
4. 在状态页编辑里加入对应分组
5. 通知设置默认已绑定（如启用了 **应用到所有现有监控项**）

---

## 常见问题

### Q1：状态页打不开 / Discord 不告警

**排查顺序**：

1. `docker ps` 看容器是否在运行
2. `docker logs uptime-kuma` 看日志有没有错误
3. 浏览器 F12 看请求是否 4xx / 5xx
4. Discord Webhook URL 是否还有效（被删过会 404）

### Q2：监控项一直 DOWN，但服务实际正常

**可能原因**：

- Uptime Kuma 部署在内网，监控公网 DDNS，路由器不支持 Hairpin NAT
- DDNS 解析延迟未更新到当前公网 IP
- 防火墙拦截 Uptime Kuma 出站请求

**验证**：在 Uptime Kuma 服务器上执行：
```bash
telnet perforce-maxs.ddns.me 30888
# 或
nc -zv perforce-maxs.ddns.me 30888
```

### Q3：Discord 收不到测试消息

**检查**：

- Webhook URL 是否完整复制（包含末尾 token 部分）
- 通知类型是否选了 **Webhook**（不是 Discord）
- 自定义 JSON 是否合法（可用 [JSONLint](https://jsonlint.com) 验证）
- Uptime Kuma 版本是否支持 Liquid 模板（≥ 1.21）

### Q4：消息显示 `{{ monitorJSON.name }}` 原文

**原因**：Uptime Kuma 版本 < 1.21，不支持 Liquid 模板。

**解决**：升级到最新版（见上方「升级 Uptime Kuma」）。

### Q5：想加邮件 / 短信 / 钉钉 / Bark 告警

Uptime Kuma 内置支持 90+ 种通知方式，按相同流程：

1. 设置 → 通知 → 新建通知
2. 选对应类型（Email / Telegram / DingTalk / Bark / Slack ...）
3. 填参数 → 测试 → 保存

---

## 维护人员

| 角色 | 负责 |
|---|---|
| MAXs Academy 教学团队 | 状态页内容 / 频道公告 |
| 运维 | Uptime Kuma 部署 / 升级 / 备份 |

如需修改本文档，请提交 PR 或直接 commit 到 `main` 分支。
