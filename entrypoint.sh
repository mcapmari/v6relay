#!/bin/sh
set -e

log() {
  echo "[INIT] $*"
}

log "Loading sit kernel module..."
modprobe sit 2>&1 && log "OK" || { log "FAILED: could not load sit module"; exit 1; }

log "Removing stale tunnel (if any)..."
ip tunnel del tun-ipv6 2>/dev/null || true

log "Creating SIT tunnel (remote: ${TUNNEL_SERVER_IPV4})..."
# No 'local' param — kernel auto-selects source IP (works behind NAT)
ip tunnel add tun-ipv6 mode sit remote ${TUNNEL_SERVER_IPV4} ttl 255 2>&1 \
  && log "OK" || { log "FAILED: tunnel creation error"; exit 1; }

log "Bringing up tunnel tun-ipv6..."
ip link set tun-ipv6 up 2>&1 && log "OK" || { log "FAILED: could not bring up tunnel"; exit 1; }

log "Assigning client IPv6: ${TUNNEL_CLIENT_IPV6}..."
if [ -n "$TUNNEL_CLIENT_IPV6" ]; then
  ip addr add ${TUNNEL_CLIENT_IPV6} dev tun-ipv6 2>&1 && log "OK" || { log "FAILED: address assignment error"; exit 1; }
fi

log "Adding route for pool: ${TUNNEL_ROUTED_NET} via tun-ipv6..."
if [ -n "$TUNNEL_ROUTED_NET" ]; then
  ip -6 route add ${TUNNEL_ROUTED_NET} dev tun-ipv6 2>&1 && log "OK" || log "WARNING: could not add subnet route"
fi

log "Setting default IPv6 route via tun-ipv6..."
ip -6 route add ::/0 dev tun-ipv6 2>&1 && log "OK" || { log "FAILED: could not set default route"; exit 1; }

log "Testing tunnel: ping -6 -c 2 ${TUNNEL_SERVER_IPV6%%/*}..."
if ping -6 -c 2 -W 3 ${TUNNEL_SERVER_IPV6%%/*} >/dev/null 2>&1; then
  log "OK"
else
  log "WARNING: tunnel far end unreachable"
fi

if [ -n "$TUNNEL_IPV6_DNS" ]; then
  log "Adding IPv6 DNS: ${TUNNEL_IPV6_DNS}"
  # Keep Docker DNS (127.0.0.11) as primary, add IPv6 DNS as secondary
  echo "nameserver ${TUNNEL_IPV6_DNS}" >> /etc/resolv.conf 2>&1 && log "OK" || log "WARNING: could not set DNS"
fi

WARMUP=${IP_POOL_WARMUP:-50}
if [ -n "$TUNNEL_ROUTED_NET" ] && [ "$WARMUP" -gt 0 ]; then
  log "Pre-assigning ${WARMUP} IPv6 addresses to tun-ipv6..."
  python3 -c "
import ipaddress, subprocess, sys
net = ipaddress.IPv6Network('${TUNNEL_ROUTED_NET}', strict=False)
count = int('${WARMUP}')
base = bytearray(net.network_address.packed)
host_bytes = (128 - net.prefixlen + 7) // 8
for i in range(1, min(count + 1, 2 ** (host_bytes * 8))):
    addr = bytearray(base)
    for j in range(host_bytes):
        addr[15 - j] = (i >> (j * 8)) & 0xFF
    ip_str = str(ipaddress.IPv6Address(bytes(addr)))
    r = subprocess.run(['ip', 'addr', 'add', f'{ip_str}/128', 'dev', 'tun-ipv6'],
                       capture_output=True, timeout=5)
    if r.returncode != 0 and b'File exists' not in r.stderr:
        pass
  " 2>&1 && log "OK" || log "WARNING: partial assignment"
fi

if [ "${PROXY_IPV4_ENABLED}" = "true" ]; then
  log "IPv4 proxying: ENABLED (direct IPv4 for IPv4-only destinations)"
else
  log "IPv4 proxying: DISABLED (IPv6-only mode)"
fi

log "Starting SOCKS5 proxy on 0.0.0.0:${PROXY_PORT:-1080}"
exec python3 /app/socks5-proxy.py
