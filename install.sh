#!/usr/bin/env bash
#
# P4D Monitor 一键部署脚本(在 Monitor VM 上跑)
#
# 用法:
#   curl -fsSL https://raw.githubusercontent.com/ziwuxin1/p4d-monitor/main/install.sh \
#     -H "Authorization: token YOUR_GH_TOKEN" -o install.sh
#   sudo bash install.sh
#
# 或者 clone 后在仓库目录跑:
#   sudo bash install.sh
#
set -o errexit
set -o nounset
set -o pipefail

readonly INSTALL_DIR="/opt/p4d-monitor"
readonly RUN_USER="p4d-monitor"
readonly SERVICE_NAME="p4d-monitor"

# ANSI 颜色
if [[ -t 1 ]]; then
    C_RESET=$'\033[0m'
    C_BOLD=$'\033[1m'
    C_GREEN=$'\033[32m'
    C_YELLOW=$'\033[33m'
    C_RED=$'\033[31m'
    C_CYAN=$'\033[36m'
else
    C_RESET="" C_BOLD="" C_GREEN="" C_YELLOW="" C_RED="" C_CYAN=""
fi

info()    { printf "${C_CYAN}ℹ${C_RESET} %s\n" "$*"; }
ok()      { printf "${C_GREEN}✓${C_RESET} %s\n" "$*"; }
warn()    { printf "${C_YELLOW}⚠${C_RESET} %s\n" "$*"; }
err()     { printf "${C_RED}✗${C_RESET} %s\n" "$*" >&2; }
die()     { err "$@"; exit 1; }
section() { printf "\n${C_BOLD}${C_CYAN}── %s ──${C_RESET}\n" "$*"; }

# 检查 root
[[ "$EUID" -eq 0 ]] || die "必须用 sudo / root 跑"

section "1/6  系统依赖"

apt update -q
apt install -y python3 python3-venv python3-pip git curl openssh-client
ok "依赖安装完成"

section "2/6  创建运行用户 + 目录"

if ! id "$RUN_USER" &>/dev/null; then
    useradd -r -m -d "$INSTALL_DIR" -s /bin/bash "$RUN_USER"
    ok "创建用户 $RUN_USER"
else
    info "用户 $RUN_USER 已存在"
fi

mkdir -p "$INSTALL_DIR" /etc/p4d-monitor /var/log/p4d-monitor
chown -R "$RUN_USER:$RUN_USER" "$INSTALL_DIR" /etc/p4d-monitor /var/log/p4d-monitor

section "3/6  下载代码到 $INSTALL_DIR"

# 如果当前目录就是 repo,直接 rsync;否则 git clone
if [[ -f "./src/app.py" && -f "./requirements.txt" ]]; then
    info "在 repo 目录,直接同步当前代码"
    rsync -a --delete --exclude='venv' --exclude='__pycache__' --exclude='.git' \
        --exclude='config/config.yaml' --exclude='data' \
        ./ "$INSTALL_DIR/"
else
    info "Clone from GitHub..."
    if [[ -d "$INSTALL_DIR/.git" ]]; then
        cd "$INSTALL_DIR"
        sudo -u "$RUN_USER" git pull
    else
        # 私有 repo 需要 token
        if [[ -z "${GH_TOKEN:-}" ]]; then
            warn "私有 repo: 请设 GH_TOKEN 环境变量"
            warn "  export GH_TOKEN=你的_personal_access_token"
            warn "或者: 把代码放到本目录后重跑此脚本"
            die "缺少 GH_TOKEN"
        fi
        sudo -u "$RUN_USER" git clone \
            "https://${GH_TOKEN}@github.com/ziwuxin1/p4d-monitor.git" \
            "$INSTALL_DIR"
    fi
fi
chown -R "$RUN_USER:$RUN_USER" "$INSTALL_DIR"
ok "代码就位"

section "4/6  Python venv + 依赖"

cd "$INSTALL_DIR"
sudo -u "$RUN_USER" python3 -m venv venv
sudo -u "$RUN_USER" "$INSTALL_DIR/venv/bin/pip" install --upgrade pip wheel -q
sudo -u "$RUN_USER" "$INSTALL_DIR/venv/bin/pip" install -r requirements.txt -q
ok "Python 依赖安装完成"

section "5/6  生成 SSH key + 配置文件"

# 生成 SSH key (Ed25519,如果不存在)
SSH_KEY="/etc/p4d-monitor/ssh_key"
if [[ ! -f "$SSH_KEY" ]]; then
    sudo -u "$RUN_USER" ssh-keygen -t ed25519 -N "" -f "$SSH_KEY" -C "p4d-monitor@$(hostname)"
    chmod 600 "$SSH_KEY"
    chmod 644 "$SSH_KEY.pub"
    chown "$RUN_USER:$RUN_USER" "$SSH_KEY" "$SSH_KEY.pub"
    ok "生成 SSH key: $SSH_KEY"
else
    info "SSH key 已存在: $SSH_KEY"
fi

# 复制配置模板
CONFIG="$INSTALL_DIR/config/config.yaml"
if [[ ! -f "$CONFIG" ]]; then
    cp "$INSTALL_DIR/config/config.example.yaml" "$CONFIG"
    chown "$RUN_USER:$RUN_USER" "$CONFIG"
    chmod 640 "$CONFIG"

    # 生成 Flask secret key
    SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
    sed -i "s|^  secret_key:.*|  secret_key: \"$SECRET\"|" "$CONFIG"
    ok "已生成 Flask secret key"

    warn "！ 还需要手动填:"
    warn "  1. dashboard.password_hash"
    warn "     生成命令:"
    warn "       $INSTALL_DIR/venv/bin/python3 -c 'import bcrypt;import getpass;p=getpass.getpass();print(bcrypt.hashpw(p.encode(),bcrypt.gensalt()).decode())'"
    warn ""
    warn "  2. email.smtp_user / smtp_password / from_addr"
    warn "     Gmail 用 App Password: https://myaccount.google.com/apppasswords"
    warn ""
    warn "  3. servers[].host / ssh_user 检查是否对"
    warn ""
    warn "编辑: sudo -u $RUN_USER nano $CONFIG"
fi

section "6/6  systemd 服务"

cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=P4D Monitor Web Dashboard
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
Group=$RUN_USER
WorkingDirectory=$INSTALL_DIR
Environment=PYTHONUNBUFFERED=1
Environment=P4D_MONITOR_CONFIG=$CONFIG
ExecStart=$INSTALL_DIR/venv/bin/python -m src.app
Restart=on-failure
RestartSec=10

# 安全加固
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=$INSTALL_DIR /var/log/p4d-monitor /etc/p4d-monitor
ReadOnlyPaths=$CONFIG

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
ok "systemd unit 创建并 enable"

# 确保 data 目录可写
mkdir -p "$INSTALL_DIR/data"
chown "$RUN_USER:$RUN_USER" "$INSTALL_DIR/data"

# 防火墙(如果开启 ufw)
if command -v ufw &>/dev/null && ufw status | grep -q "Status: active"; then
    PORT=$(grep -E "^\s*port:" "$CONFIG" | head -1 | awk '{print $2}' || echo "8080")
    ufw allow "$PORT/tcp" comment 'p4d-monitor' || true
    info "已开放防火墙 $PORT/tcp"
fi

cat <<EOF

${C_BOLD}${C_GREEN}════════════════════════════════════════════════════════${C_RESET}
${C_BOLD}P4D Monitor 安装完成${C_RESET}
${C_BOLD}${C_GREEN}════════════════════════════════════════════════════════${C_RESET}

下一步:

${C_BOLD}1. 设置 dashboard 登录密码${C_RESET}
   sudo -u $RUN_USER $INSTALL_DIR/venv/bin/python3 -c \\
       'import bcrypt,getpass;p=getpass.getpass("Password: ");print(bcrypt.hashpw(p.encode(),bcrypt.gensalt()).decode())'
   把输出粘到 $CONFIG 的 password_hash 里。

${C_BOLD}2. 配置 Gmail SMTP${C_RESET}
   编辑 $CONFIG,填入 email.smtp_user / smtp_password (App Password) / from_addr。

${C_BOLD}3. 把 SSH 公钥加到两台 P4D 服务器${C_RESET}
   公钥内容:
$(cat "$SSH_KEY.pub" | sed 's/^/   /')

   在每台 P4D 服务器跑:
   echo '$(cat "$SSH_KEY.pub")' | sudo tee -a /root/.ssh/authorized_keys

${C_BOLD}4. 测试 SSH 连通${C_RESET}
   sudo -u $RUN_USER ssh -i $SSH_KEY -o StrictHostKeyChecking=no \\
       root@192.168.1.51 hostname
   sudo -u $RUN_USER ssh -i $SSH_KEY -o StrictHostKeyChecking=no \\
       root@192.168.1.50 hostname

${C_BOLD}5. 启动服务${C_RESET}
   sudo systemctl start $SERVICE_NAME
   sudo systemctl status $SERVICE_NAME

${C_BOLD}6. 访问仪表盘${C_RESET}
   局域网: http://$(hostname -I | awk '{print $1}'):8080
   用 step 1 里设的密码登录

${C_BOLD}日志${C_RESET}
   sudo journalctl -u $SERVICE_NAME -f

${C_BOLD}更新代码${C_RESET}
   sudo -u $RUN_USER bash -c 'cd $INSTALL_DIR && git pull'
   sudo systemctl restart $SERVICE_NAME

EOF
