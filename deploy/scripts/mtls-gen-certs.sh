#!/usr/bin/env bash
# ai4s mTLS 测试证书生成（issue #28，幂等）。
# 产物分两处（均 gitignored）：
#   deploy/.local/mtls/          仅容器挂载所需：ca.crt / server.crt / server.key
#   deploy/.local/mtls-private/  其余私钥与客户端材料：ca.key（可签发任意设备证书）、
#                                client-ok.crt/.key（合法客户端）、wrong-ca/wrong-client（负面对照组）
# 私钥不留在挂载目录：agentgateway 容器只需三件套，ca.key 随目录挂载等于把签发权交给容器。
set -euo pipefail
cd "$(dirname "$0")/.."
OUT=".local/mtls"
PRIV=".local/mtls-private"
mkdir -p "$OUT" "$PRIV"
cd "$OUT"

if [ -f ca.crt ] && [ -f server.crt ] && [ -f "../mtls-private/ca.key" ]; then
  echo "已存在，跳过（删除 $OUT 与 $PRIV 后重跑可重新生成）"
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

# 私钥与客户端材料移出容器挂载目录（ca.key 签发权、客户端身份不交给 agentgateway 容器）
mv -f ca.key ca.srl client-ok.crt client-ok.key wrong-ca.crt wrong-ca.key wrong-ca.srl wrong-client.crt wrong-client.key "../mtls-private/" 2>/dev/null || true

echo "完成：容器挂载 $(pwd)（仅 ca.crt/server.crt/server.key）"
ls -1 *.crt *.key
echo "私钥与客户端材料：$(cd ../mtls-private && pwd)"
ls -1 ../mtls-private
