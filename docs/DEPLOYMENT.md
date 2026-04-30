# P4D Monitor 部署指南

完整的部署流程,从开 VM 到浏览器看到仪表盘。

---

## 总览

```
1. PVE 上开 Ubuntu 24.04 VM        (10 分钟)
2. 一键安装脚本                    (5 分钟)
3. 设登录密码 + Gmail SMTP         (5 分钟)
4. 把 SSH 公钥加到两台 P4D         (3 分钟)
5. 启动服务 + 浏览器测试           (1 分钟)
6. 装 Tailscale 实现远程访问       (5 分钟,可选)
```

总共 30 分钟内可以全部跑通。

---

## Step 1 — PVE 上开 Monitor VM

参考视频 [手把手 PVE 安装 Ubuntu Server 24](https://youtu.be/xa5iCt0OY5w)。

VM 规格:
```
名称: p4d-monitor
CPU:  1 核
内存: 1 GB
磁盘: 16 GB
网卡: 桥接到 LAN
系统: Ubuntu 24.04 LTS
```

装系统时:
- ☑ OpenSSH Server
- ☐ 跳过 Docker

装完确认能 SSH 进去,记下 IP(下面例子用 `192.168.1.40`)。

---

## Step 2 — 一键安装

### 准备 GitHub Personal Access Token

repo 是 private,需要 token 才能 clone。

1. 访问 https://github.com/settings/tokens?type=beta
2. Generate new token (fine-grained)
3. Repository access → Only select repositories → 选 `p4d-monitor`
4. Permissions → Contents: Read-only
5. Generate → 复制 token (类似 `github_pat_xxxxx`)

### 在 Monitor VM 上跑

```bash
ssh ubuntu@192.168.1.40
sudo apt update && sudo apt install -y git curl

# clone (用 token)
export GH_TOKEN=github_pat_你的token
git clone https://${GH_TOKEN}@github.com/ziwuxin1/p4d-monitor.git
cd p4d-monitor

# 一键安装
sudo bash install.sh
```

`install.sh` 会:
- 装 Python venv + 依赖
- 创建 `p4d-monitor` 系统用户
- 生成 SSH ed25519 keypair
- 生成 Flask secret key
- 安装 systemd 服务
- 输出后续手动操作的指引

---

## Step 3 — 配置 dashboard 密码 + Gmail

### 3.1 生成登录密码

按 install.sh 的提示跑(在 VM 上):

```bash
sudo -u p4d-monitor /opt/p4d-monitor/venv/bin/python3 -c \
    'import bcrypt,getpass;p=getpass.getpass("Password: ");print(bcrypt.hashpw(p.encode(),bcrypt.gensalt()).decode())'
```

输入你想要的密码(不会显示),回车后会输出一行 bcrypt hash:
```
$2b$12$abcdefghijklmnopqrstuvwxyz...
```

复制这一行。

### 3.2 编辑配置文件

```bash
sudo -u p4d-monitor nano /opt/p4d-monitor/config/config.yaml
```

填:

```yaml
dashboard:
  username: admin                        # 你想用的用户名
  password_hash: "$2b$12$abcdefg..."     # 上面生成的 hash
  # secret_key 已经自动生成,别动
```

### 3.3 配置 Gmail SMTP

详见 [EMAIL-SETUP.md](EMAIL-SETUP.md)。简单说:

1. 启用 Gmail 两步验证: https://myaccount.google.com/security
2. 创建 App Password: https://myaccount.google.com/apppasswords
3. 16 位密码填进 config.yaml:

```yaml
email:
  enabled: true
  smtp_host: smtp.gmail.com
  smtp_port: 587
  smtp_user: your-monitor@gmail.com
  smtp_password: "abcd efgh ijkl mnop"   # 16 位 App Password,空格保留也行
  from_addr: "P4D Monitor <your-monitor@gmail.com>"
  to_addr: zho181501@gmail.com
```

⚠️ **`smtp_user` 和 `to_addr` 可以是同一个邮箱**(自己发给自己),也可以分开。

---

## Step 4 — SSH 公钥加到两台 P4D

### 4.1 看 Monitor VM 上生成的公钥

```bash
sudo cat /etc/p4d-monitor/ssh_key.pub
# ssh-ed25519 AAAAC3NzaC1lZDI1NTE5...something p4d-monitor@p4d-monitor
```

复制整行。

### 4.2 加到两台 P4D 服务器

#### MAXs (192.168.1.51)

```bash
ssh root@192.168.1.51

# 加公钥
mkdir -p /root/.ssh
chmod 700 /root/.ssh
echo 'ssh-ed25519 AAAAC3... p4d-monitor@p4d-monitor' >> /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys
exit
```

#### Student (192.168.1.50)

同样:
```bash
ssh root@192.168.1.50
mkdir -p /root/.ssh && chmod 700 /root/.ssh
echo 'ssh-ed25519 AAAAC3... p4d-monitor@p4d-monitor' >> /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys
exit
```

### 4.3 在 Monitor VM 上测连通

```bash
sudo -u p4d-monitor ssh -i /etc/p4d-monitor/ssh_key \
    -o StrictHostKeyChecking=accept-new \
    root@192.168.1.51 "hostname && systemctl is-active p4d"

sudo -u p4d-monitor ssh -i /etc/p4d-monitor/ssh_key \
    -o StrictHostKeyChecking=accept-new \
    root@192.168.1.50 "hostname && systemctl is-active p4d"
```

应该输出:
```
p4d-maxs
active
```

```
p4d-student
active
```

---

## Step 5 — 启动服务 + 测试

```bash
sudo systemctl start p4d-monitor
sudo systemctl status p4d-monitor
```

看到 `active (running)` 就好。

实时看日志:
```bash
sudo journalctl -u p4d-monitor -f
```

预期看到:
```
Scheduler started: probe every 300s, daily summary at 09:00
Probing MAXs ...
Probing Student ...
```

### 浏览器访问

```
http://192.168.1.40:8080
```

输入用户名 + 密码登录,看到仪表盘。

如果没显示数据,等 30 秒后刷新(第一轮探测要点时间)。

---

## Step 6 — Tailscale 远程访问(可选)

详见 [TAILSCALE-SETUP.md](TAILSCALE-SETUP.md)。

简版:
1. https://tailscale.com 注册账号
2. 在 Monitor VM 上装:
   ```bash
   curl -fsSL https://tailscale.com/install.sh | sh
   sudo tailscale up
   ```
3. 在你手机/笔记本上装 Tailscale 客户端
4. 在外面打开 Tailscale → 访问 `http://100.x.x.x:8080`

**不需要路由器映射,不暴露公网**。

---

## 常用运维命令

```bash
# 服务管理
sudo systemctl start p4d-monitor
sudo systemctl stop p4d-monitor
sudo systemctl restart p4d-monitor
sudo systemctl status p4d-monitor

# 看日志
sudo journalctl -u p4d-monitor -f       # 实时
sudo journalctl -u p4d-monitor -n 100   # 最近 100 行

# 编辑配置(改完要 restart)
sudo -u p4d-monitor nano /opt/p4d-monitor/config/config.yaml
sudo systemctl restart p4d-monitor

# 拉最新代码
cd /opt/p4d-monitor
sudo -u p4d-monitor git pull
sudo systemctl restart p4d-monitor
```

---

## 故障排查

### Q: 服务起不来

```bash
sudo journalctl -u p4d-monitor -n 50
```

常见原因:
- `config.yaml` 里 password_hash 没填 → bcrypt 报错
- `secret_key` 为空 → Flask 拒绝启动(install.sh 应该自动填了,检查一下)

### Q: 浏览器登录密码错

- 确认 username 对(默认 `admin`)
- 重新生成 bcrypt hash(可能复制时丢了 `$` 之后的内容)

### Q: 仪表盘空,没数据

```bash
# 看探测有没有跑
sudo journalctl -u p4d-monitor | grep -i probe

# 手动测 SSH
sudo -u p4d-monitor ssh -i /etc/p4d-monitor/ssh_key root@192.168.1.51 hostname
```

如果 SSH 报 `Permission denied (publickey)`:
- 在目标 P4D 上确认 `/root/.ssh/authorized_keys` 含我们的公钥
- `chmod 600 /root/.ssh/authorized_keys` + `chmod 700 /root/.ssh`

### Q: 邮件发不出去

- Gmail 必须用 **App Password**,不是登录密码
- App Password 需要先启用两步验证才能生成
- 看日志: `sudo journalctl -u p4d-monitor | grep -i smtp`

### Q: 我想改探测频率

```bash
sudo -u p4d-monitor nano /opt/p4d-monitor/config/config.yaml
```

```yaml
probe:
  interval_seconds: 300   # 改成 60(每分钟)或 600(10 分钟)
```

```bash
sudo systemctl restart p4d-monitor
```

---

## 卸载

```bash
sudo systemctl stop p4d-monitor
sudo systemctl disable p4d-monitor
sudo rm /etc/systemd/system/p4d-monitor.service
sudo userdel -r p4d-monitor
sudo rm -rf /opt/p4d-monitor /etc/p4d-monitor /var/log/p4d-monitor
sudo systemctl daemon-reload
```
