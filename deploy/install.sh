#!/usr/bin/env bash
# 在西雅图 VPS 上部署中继：Python 环境 + 自签证书 + Nginx + systemd
# 用法：sudo bash install.sh <中继设备令牌>
# 幂等，可重复执行。
set -euo pipefail

TOKEN="${1:-}"
if [ -z "$TOKEN" ]; then
  echo "用法: sudo bash install.sh <设备令牌>"
  exit 1
fi

SECRET="${RELAY_HOST_SECRET:-$(head -c 32 /dev/urandom | base64 | tr -d '=+/' | cut -c1-40)}"
echo "主机密钥: $SECRET"
echo "设备令牌: $TOKEN"

apt-get update -qq
apt-get install -y -qq python3 python3-venv nginx openssl >/dev/null

# 1. 目录与代码
mkdir -p /opt/relay
cp /tmp/relay_server.py /opt/relay/ 2>/dev/null || true
python3 -m venv /opt/relay/venv
/opt/relay/venv/bin/pip install -q --upgrade pip
/opt/relay/venv/bin/pip install -q aiohttp

# 2. 自签证书
mkdir -p /etc/ssl/hub
if [ ! -f /etc/ssl/hub/relay.crt ]; then
  openssl req -x509 -newkey rsa:2048 -days 3650 -nodes \
    -keyout /etc/ssl/hub/relay.key -out /etc/ssl/hub/relay.crt \
    -subj "/CN=relay.local" >/dev/null 2>&1
fi
echo "证书指纹(SPKI):"
openssl x509 -in /etc/ssl/hub/relay.crt -noout -pubkey \
  | openssl pkey -pubin -outform der \
  | openssl dgst -sha256 -binary | base64

# 3. systemd
cat >/etc/systemd/system/relay.service <<EOF
[Unit]
Description=Hub Relay
After=network.target

[Service]
WorkingDirectory=/opt/relay
Environment=RELAY_HOST_SECRET=$SECRET
Environment=RELAY_DEVICES=$TOKEN
Environment=RELAY_PORT=8788
ExecStart=/opt/relay/venv/bin/python /opt/relay/relay_server.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now relay

# 4. Nginx
cat >/etc/nginx/hub-proxy.conf <<'EOF'
proxy_set_header Host $host;
proxy_set_header X-Real-IP $remote_addr;
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
EOF
cp /tmp/nginx-hub.conf /etc/nginx/sites-available/hub
ln -sf /etc/nginx/sites-available/hub /etc/nginx/sites-enabled/hub
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

echo "---"
echo "完成。检查：curl -k https://127.0.0.1/health"
echo "主机密钥（电脑端 relay_host 要用）: $SECRET"
