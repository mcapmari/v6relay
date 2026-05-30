# IPv6 Rotating SOCKS5 Proxy

Docker-based SOCKS5 proxy with rotating IPv6 source addresses over a 6in4 (SIT) tunnel. Each connection uses a different random IPv6 from a routed subnet.

## How it works

```
Client → SOCKS5 (port 1080) → random IPv6 source → 6in4 tunnel → internet
```

1. Creates a SIT tunnel to an IPv6 tunnel broker (Hurricane Electric, NetAssist, etc.)
2. Assigns a routed /48 or /64 subnet to the tunnel interface
3. Runs an async SOCKS5 proxy that picks a **random IPv6 source address** for each outbound connection
4. Auto-assigns new /128 addresses on-the-fly — pool is effectively unlimited

## Requirements

- Docker + Docker Compose
- A 6in4 tunnel broker account (free from [Hurricane Electric](https://tunnelbroker.net))
- A public IPv4 address (or NAT with protocol 41 forwarding)
- Kernel module: `sit` (loaded automatically with `SYS_MODULE` capability)

## Quick start

### 1. Configure

Edit `docker-compose.yml` with your tunnel broker details:

```yaml
environment:
  TUNNEL_SERVER_IPV4: "216.xx.xx.xx"         # Broker's IPv4 endpoint
  TUNNEL_SERVER_IPV6: "2001:470:xxxx::1/64"  # Broker's IPv6 tunnel address
  TUNNEL_CLIENT_IPV6: "2001:470:xxxx::2/64"  # Your IPv6 tunnel address
  TUNNEL_ROUTED_NET: "2001:470:yyyy::/64"    # Routed subnet for source IP pool
  TUNNEL_IPV6_DNS: "2001:470:20::2"         # IPv6 DNS resolver
```

### 2. Run

```bash
docker compose up -d
```

### 3. Use

```bash
curl --socks5-hostname your-server-ip:1080 https://api6.ipify.org
# Returns a random IPv6 from your pool
```

## Configuration

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `TUNNEL_SERVER_IPV4` | — | Broker's IPv4 endpoint (required) |
| `TUNNEL_CLIENT_IPV4` | — | Your public IPv4 (for reference only) |
| `TUNNEL_SERVER_IPV6` | — | Broker's tunnel IPv6 address |
| `TUNNEL_CLIENT_IPV6` | — | Your tunnel IPv6 address |
| `TUNNEL_ROUTED_NET` | — | Routed /48 or /64 subnet for source IP pool |
| `TUNNEL_IPV6_DNS` | — | IPv6 DNS resolver |
| `PROXY_PORT` | `1080` | SOCKS5 listen port |
| `PROXY_IPV4_ENABLED` | `false` | Allow direct IPv4 for IPv4-only destinations |
| `IP_POOL_WARMUP` | `50` | Number of IPv6 addresses to pre-assign at startup |

### IPv4 fallback

By default (`PROXY_IPV4_ENABLED=false`), the proxy rejects connections to IPv4-only destinations. Set to `true` to allow direct IPv4 connections alongside IPv6.

## Architecture

```
┌─────────────────────────────────────────────┐
│  Docker container (network_mode: host)      │
│                                             │
│  SOCKS5 proxy ← → tun-ipv6 (SIT tunnel)    │
│  :1080                │                     │
│                       │ 6in4 (protocol 41)  │
│                       ▼                     │
│               eth0 → NAT → broker           │
└─────────────────────────────────────────────┘
```

- `network_mode: host` — required for protocol 41 tunnel traffic
- `NET_ADMIN` + `SYS_MODULE` capabilities — for tunnel creation and IP assignment
- SIT tunnel uses `remote` only (no `local`) — works behind NAT

```
