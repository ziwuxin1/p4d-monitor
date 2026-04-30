# 邮件告警配置

让 P4D Monitor 通过邮件给你发告警。

---

## Gmail App Password (推荐)

Gmail 不允许第三方应用用普通密码登录,必须用 **App Password**(16 位)。

### 前置: 启用两步验证

App Password 只对**已启用两步验证**的账号开放。

1. 访问 https://myaccount.google.com/security
2. 找到 "2-Step Verification" → 启用
3. 用手机短信 / Authenticator App 都行

### 生成 App Password

1. 访问 https://myaccount.google.com/apppasswords

   (如果看不到这个链接,说明两步验证没开)

2. **App name** 填 "P4D Monitor"
3. 点 "Create"
4. 看到 16 位密码,例如:
   ```
   abcd efgh ijkl mnop
   ```
5. **立刻复制!关闭页面就再也看不到了**

### 配置到 Monitor

编辑 `/opt/p4d-monitor/config/config.yaml`:

```yaml
email:
  enabled: true
  smtp_host: smtp.gmail.com
  smtp_port: 587
  smtp_user: your-monitor@gmail.com         # 你的 Gmail
  smtp_password: "abcd efgh ijkl mnop"       # 16 位 App Password,空格保留
  from_addr: "P4D Monitor <your-monitor@gmail.com>"
  to_addr: zho181501@gmail.com               # 接收告警的邮箱
```

⚠️ **注意**:
- `smtp_user` = 发件人(用来登录 Gmail SMTP),必须是 Gmail 邮箱
- `to_addr` = 接收人,可以是任何邮箱(Gmail/Outlook/QQ 都行)
- 两个可以是同一个(自己发给自己)

重启服务:
```bash
sudo systemctl restart p4d-monitor
```

---

## Outlook / Hotmail

类似 Gmail,需要 App Password。

### 生成 App Password

1. 访问 https://account.microsoft.com/security
2. Advanced security options → App passwords
3. 创建一个,复制

### 配置

```yaml
email:
  smtp_host: smtp.office365.com
  smtp_port: 587
  smtp_user: your-monitor@outlook.com
  smtp_password: "16-char-app-password"
  from_addr: "P4D Monitor <your-monitor@outlook.com>"
  to_addr: zho181501@gmail.com
```

---

## QQ 邮箱

需要开 SMTP 服务并获取**授权码**(类似 App Password)。

1. 登录 https://mail.qq.com
2. 设置 → 账户 → POP3/SMTP 服务 → 开启
3. 按提示发短信,获取**授权码**(16 位)

### 配置

```yaml
email:
  smtp_host: smtp.qq.com
  smtp_port: 587
  smtp_user: your@qq.com
  smtp_password: "QQ-授权码-16位"
  from_addr: "P4D Monitor <your@qq.com>"
  to_addr: zho181501@gmail.com
```

---

## 学校 / 公司 SMTP

如果你学校/公司有 SMTP 服务器,通常配置长这样:

```yaml
email:
  smtp_host: smtp.school.edu          # 学校 SMTP 主机
  smtp_port: 587                       # 587 (STARTTLS) 或 465 (SSL)
  smtp_user: your-account@school.edu
  smtp_password: "你的邮箱密码"          # 通常是登录密码
  from_addr: "your-account@school.edu"
  to_addr: zho181501@gmail.com
```

详细配置问 IT 部门要 SMTP 设置。

---

## 测试邮件

服务起来后,你可以**手动触发一个告警测试**邮件:

### 方法 1: 关掉一台 P4D 看看是否报警

在某台 P4D 上跑 `sudo systemctl stop p4d`,等 5-10 分钟(下一个探测周期),应该收到邮件:

```
🚨 [P4D-MAXs] P4D 服务停止运行
```

测完赶紧 `sudo systemctl start p4d`(再过 5 分钟会收到 ✅ 恢复邮件)。

### 方法 2: 手动跑探测脚本

```bash
sudo -u p4d-monitor /opt/p4d-monitor/venv/bin/python3 -c "
from src import config, scheduler
cfg = config.load()
scheduler.probe_all(cfg)
"
```

如果有问题会立刻发邮件。

### 方法 3: 手动发摘要邮件

```bash
sudo -u p4d-monitor /opt/p4d-monitor/venv/bin/python3 -c "
from src import config, db, alerter
cfg = config.load()
probes = []
for s in cfg['servers']:
    p = db.latest_probe(s['name'])
    if p: probes.append(p)
print('Sending summary...', alerter.send_daily_summary(cfg, probes))
"
```

---

## 邮件内容预览

### 异常告警

主题:
```
🚨 [P4D-MAXs] P4D 服务停止运行
```

正文:
```
🚨 P4D 服务停止运行

Server:    MAXs
Severity:  CRITICAL
Time:      2026-04-30 14:35:22 UTC
Alert:     service_down

详情:
systemctl is-active 返回: inactive
端口 1888 监听: False

建议操作:
SSH 进服务器跑 sudo systemctl status p4d 查看详情。
如果是 license 问题,跑 toolkit 菜单 6 (Counter 救援)。
```

### 恢复通知

主题:
```
✅ [P4D-MAXs] Recovered: service_down
```

正文:
```
✅ 服务恢复正常

MAXs 之前的告警 service_down 已恢复。
```

### 每日摘要

主题:
```
📊 P4D Daily Health Report — 2026-04-30
```

正文是个表格,显示每台机器的:
- Health 状态
- License
- 上次本地 checkpoint
- 上次 NAS 备份
- 磁盘 %

---

## 告警 cooldown

为防止"告警风暴"(同一个问题反复发邮件),Monitor 默认设了 **30 分钟 cooldown**:

> 同一个 server + 同一个 alert_key,30 分钟内只发第一次,后面的事件记录到 DB 但不发邮件。

在 `config.yaml` 调:
```yaml
alerts:
  cooldown_minutes: 30
```

设成 0 = 关闭 cooldown(每次探测都发,容易刷屏)。

---

## 关闭邮件功能(只看仪表盘)

```yaml
email:
  enabled: false
```

重启服务。仪表盘还是能用,只是不发邮件。

---

## 故障排查

### 没收到邮件

```bash
# 看日志
sudo journalctl -u p4d-monitor | grep -iE 'smtp|email|alert'
```

常见错误:

| 错误 | 原因 |
|------|------|
| `(534, b'5.7.9 Application-specific password required')` | 必须用 App Password,不是登录密码 |
| `(535, b'5.7.8 Username and Password not accepted')` | 用户名/密码错了 |
| `(530, b'Authentication required')` | smtp_user 没配 |
| `Connection refused` | smtp_host 错或端口错 |
| `gaierror` | DNS 解析失败,检查 smtp_host |

### 收到邮件但显示乱码

不会发生,我们用 UTF-8 + MIME multipart。
如果真有,可能是邮件客户端不识别 HTML,看纯文本部分应该正常。

### 邮件进垃圾箱

正常 — Gmail 对自动邮件比较敏感。把 `to_addr` 加白名单:
1. 收到一封告警邮件后,标记为"非垃圾邮件"
2. 添加发件人到联系人

---

## 进阶: 多接收人

支持发多个收件人:

```yaml
email:
  to_addr: "you@gmail.com, ops@company.com, admin@school.edu"
```

逗号分隔。
