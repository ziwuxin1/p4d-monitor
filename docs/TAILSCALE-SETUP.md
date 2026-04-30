# Tailscale 远程访问配置

让你**家里 + 外面**都能访问 Monitor 仪表盘,**不暴露公网**。

---

## Tailscale 是什么

Tailscale 是基于 WireGuard 的 mesh VPN。装好之后给每台设备一个虚拟 IP(`100.x.x.x`),**任何地方任何网络**都能互相访问。

```
家里 (在家 WiFi):
   你电脑 ────LAN────► Monitor VM:8080  ✓

外面 (4G/咖啡店):
   你手机 ──Tailscale Mesh──► Monitor VM:8080  ✓

陌生人:
   ✗ 完全连不到(没 Tailscale 网络入口)
```

**免费 100 设备**(以前是 20),个人用绰绰有余。

---

## Step 1 — 注册账号

访问 https://login.tailscale.com/start 用 Google / Microsoft / GitHub 账号登录,**不需要单独注册**。

---

## Step 2 — Monitor VM 装 Tailscale

```bash
ssh ubuntu@192.168.1.40

# 一键安装
curl -fsSL https://tailscale.com/install.sh | sh

# 启动 + 关联账号
sudo tailscale up
```

会输出一个 URL 类似 `https://login.tailscale.com/a/abcdef...`,复制到浏览器打开,授权这台设备。

授权完看 IP:
```bash
tailscale ip -4
# 100.123.45.67   ← 这就是 Monitor VM 的 Tailscale IP
```

---

## Step 3 — 你的设备装 Tailscale

### 手机
- iOS: App Store 搜 "Tailscale"
- Android: Play Store / 国内市场都有

装好用同一个账号登录,自动加入网络。

### 笔记本

| 系统 | 装法 |
|------|------|
| Windows | https://tailscale.com/download/windows |
| macOS | App Store 或 https://tailscale.com/download/mac |
| Linux | `curl -fsSL https://tailscale.com/install.sh \| sh` |

装好后用同一账号登录。

---

## Step 4 — 在外面访问 Monitor

打开手机/笔记本的 Tailscale,**确保它显示已连接**(状态栏 / 菜单栏图标亮)。

然后浏览器访问:
```
http://100.123.45.67:8080
```

(用 step 2 里 `tailscale ip -4` 输出的实际 IP)

---

## 高级: 用域名而不是 IP

Tailscale 自带 DNS,叫 **MagicDNS**。开启后可以用主机名直接访问。

### 启用 MagicDNS

1. 打开 https://login.tailscale.com/admin/dns
2. ☑ Enable MagicDNS
3. (可选) 自定义 tailnet 名(比如 `your-name.ts.net`)

### 然后访问

```
http://p4d-monitor:8080
```

(取决于你 VM 的 hostname)

或者完整域名:
```
http://p4d-monitor.your-name.ts.net:8080
```

---

## 高级: HTTPS 自动证书

Tailscale 可以给你的服务发 HTTPS 证书,浏览器不会报警告。

### 启用 HTTPS

1. https://login.tailscale.com/admin/dns → 启用 HTTPS Certificates
2. 在 Monitor VM 上跑:
   ```bash
   sudo tailscale cert p4d-monitor.your-name.ts.net
   ```

会输出两个文件:
- `p4d-monitor.your-name.ts.net.crt`
- `p4d-monitor.your-name.ts.net.key`

把这俩配进 nginx / caddy 反代即可。详细 https://tailscale.com/kb/1153/enabling-https。

### 简化版: 用 Caddy 自动反代

```bash
sudo apt install -y caddy
```

```caddyfile
# /etc/caddy/Caddyfile
p4d-monitor.your-name.ts.net {
    reverse_proxy localhost:8080
}
```

```bash
sudo systemctl restart caddy
```

Caddy 会自动跟 Tailscale 拿证书。访问:
```
https://p4d-monitor.your-name.ts.net
```

---

## 安全建议

### 1. ACL 限制

Tailscale 默认所有设备互通。你可以限制只有特定设备能访问 Monitor:

https://login.tailscale.com/admin/acls/file

例子:
```jsonc
{
  "acls": [
    {
      "action": "accept",
      "src":    ["你的-iPhone-Tag", "你的-MacBook-Tag"],
      "dst":    ["p4d-monitor:8080"],
    }
  ],
}
```

### 2. 关 Tailscale 的暴露开关

确认你**没有**用 `tailscale serve` 或 `tailscale funnel` 暴露到公网(默认就是关的)。

```bash
tailscale serve status     # 应该是空
tailscale funnel status    # 应该是空
```

### 3. Subnet routing(可选)

如果想在外面也能访问 P4D 服务器(MAXs / Student)而不只是 Monitor,可以让 Monitor VM 当 Subnet Router:

```bash
# Monitor VM 上
sudo tailscale up --advertise-routes=192.168.1.0/24
```

然后在 https://login.tailscale.com/admin/machines 把这台机器的 routes approve。

之后你的 Tailscale 设备可以直接访问 `192.168.1.51`、`192.168.1.50` 等局域网 IP。

⚠️ **慎用!**这等于把家里整个 LAN 暴露给所有 Tailscale 设备。

---

## 故障排查

### Q: `tailscale up` 卡住

确保 Monitor VM 能访问外网。
```bash
ping 8.8.8.8
curl https://login.tailscale.com
```

### Q: 手机连了但访问 100.x 超时

- 确认 Monitor VM 的防火墙允许 8080:
  ```bash
  sudo ufw allow 8080/tcp
  ```
- 确认 Flask 监听 `0.0.0.0` 而不只 `127.0.0.1`(检查 `config.yaml` 的 `host: 0.0.0.0`)

### Q: Tailscale IP 变了

不会变。每台设备绑定的 IP 永久固定,除非你在 admin 面板手动删除设备。

### Q: 我不想用 Tailscale,有别的方案吗?

替代方案:
- **Cloudflare Tunnel**: 类似 Tailscale,免费但需要域名
- **ZeroTier**: 类似 Tailscale,开源
- **WireGuard 自建**: 完全控制,但配置复杂
- **路由器端口映射 + IP 白名单**: 简单粗暴,但安全性低

---

## 卸载 Tailscale

```bash
sudo tailscale logout
sudo apt remove -y tailscale
```

在 admin 面板 https://login.tailscale.com/admin/machines 删除这台设备。
