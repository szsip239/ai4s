#!/usr/bin/env bash
# ai4s mTLS 测试证书生成（issue #28，幂等）。
# 产物全部在 deploy/.local/mtls/（gitignored）：
#   ca.crt/ca.key          测试 CA（客户端证书的信任根）
#   server.crt/server.key  网关服务端证书（CN=localhost，SAN localhost/127.0.0.1）
#   client-ok.crt/.key     合法客户端证书（CA 签发）
#   wrong-ca.crt/.key      对照组：另一个不被信任的 CA 签发的客户端证书
set -euo pipefail
cd "$(dirname "$0")/.."
OUT=".local/mtls"
mkdir -p "$OUT"
cd "$OUT"

if [ -f ca.crt ] && [ -f client-ok.crt ] && [ -f wrong-ca.crt ]; then
  echo "已存在，跳过（删除 $OUT 后重跑可重新生成）"
  exit 0
fi

DAYS=825  # 约 2 年，PoC 用；生产路径=短寿命证书+轮换（Casdoor CA）

echo "==> 测试 CA"
openssl req -x509 -newkey rsa:2048 -keyout ca.key -out ca.crt -days $DAYS -nodes \
  -subj "/CN=ai4s Test CA/O=ai4s" 2>/dev/null

echo "==> 网关服务端证书"
openssl req -newkey rsa:2048 -keyout server.key -out server.csr -nodes \
  -subj "/CN=localhost/O=ai4s" 2>/dev/null
cat > server-ext.cnf <<'EOF'
subjectAltName=DNS:localhost,DNS:host.docker.internal,IP:127.0.0.1
extendedKeyUsage=serverAuth
EOF
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
  -out server.crt -days $DAYS -extfile server-ext.cnf 2>/dev/null
rm -f server.csr server-ext.cnf

echo "==> 合法客户端证书（client-ok，CN=device-01）"
openssl req -newkey rsa:2048 -keyout client-ok.key -out client-ok.csr -nodes \
  -subj "/CN=device-01/O=ai4s-employee" 2>/dev/null
cat > client-ext.cnf <<'EOF'
extendedKeyUsage=clientAuth
EOF
openssl x509 -req -in client-ok.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
  -out client-ok.crt -days $DAYS -extfile client-ext.cnf 2>/dev/null
rm -f client-ok.csr client-ext.cnf

echo "==> 对照组：不被信任的 CA + 其签发的客户端证书（wrong-ca）"
openssl req -x509 -newkey rsa:2048 -keyout wrong-ca.key -out wrong-ca.crt -days $DAYS -nodes \
  -subj "/CN=Untrusted CA/O=evil" 2>/dev/null
openssl req -newkey rsa:2048 -keyout wrong-client.key -out wrong-client.csr -nodes \
  -subj "/CN=device-99/O=evil" 2>/dev/null
cat > wrong-ext.cnf <<'EOF'
extendedKeyUsage=clientAuth
EOF
openssl x509 -req -in wrong-client.csr -CA wrong-ca.crt -CAkey wrong-ca.key -CAcreateserial \
  -out wrong-client.crt -days $DAYS -extfile wrong-ext.cnf 2>/dev/null
rm -f wrong-client.csr wrong-ext.cnf

chmod 600 *.key
echo "完成：$(pwd)"
ls -1 *.crt *.key
