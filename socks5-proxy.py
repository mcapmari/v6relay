#!/usr/bin/env python3
import asyncio
import ipaddress
import os
import random
import socket
import struct
import subprocess

PROXY_PORT = int(os.environ.get("PROXY_PORT", "1080"))
PROXY_IPV4_ENABLED = os.environ.get("PROXY_IPV4_ENABLED", "").lower() == "true"
ROUTED_NET = os.environ.get("TUNNEL_ROUTED_NET", "")
TUN_IFACE = "tun-ipv6"

NETWORK_ADDR = None
PREFIXLEN = None
if ROUTED_NET:
    try:
        net = ipaddress.IPv6Network(ROUTED_NET, strict=False)
        NETWORK_ADDR = net.network_address
        PREFIXLEN = net.prefixlen
    except ValueError as e:
        print(f"[PROXY] ERROR: invalid routed network: {e}", flush=True)

_assigned_ips = set()


def assign_ip(ip_str):
    if ip_str in _assigned_ips:
        return True
    try:
        subprocess.run(
            ["ip", "addr", "add", f"{ip_str}/128", "dev", TUN_IFACE],
            capture_output=True, timeout=5, check=True,
        )
        _assigned_ips.add(ip_str)
        return True
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode().strip() if e.stderr else ""
        if "File exists" in stderr:
            _assigned_ips.add(ip_str)
            return True
        return False
    except Exception:
        return False


def random_ipv6():
    if NETWORK_ADDR is None:
        return None
    raw = bytearray(NETWORK_ADDR.packed)
    host_bytes = (128 - PREFIXLEN + 7) // 8
    for i in range(host_bytes):
        raw[15 - i] = random.randint(0, 255)
    return str(ipaddress.IPv6Address(bytes(raw)))


def get_random_assigned_ipv6():
    for _ in range(20):
        ip = random_ipv6()
        if ip is None:
            return None
        if assign_ip(ip):
            return ip
    return None


async def recvn(sock, n, loop):
    buf = b""
    while len(buf) < n:
        chunk = await loop.sock_recv(sock, n - len(buf))
        if not chunk:
            raise ConnectionError("connection closed")
        buf += chunk
    return buf


async def relay_loop(a, b, loop):
    async def copy(src, dst):
        try:
            while True:
                data = await loop.sock_recv(src, 65536)
                if not data:
                    break
                await loop.sock_sendall(dst, data)
        except Exception:
            pass

    t1 = asyncio.create_task(copy(a, b))
    t2 = asyncio.create_task(copy(b, a))
    await asyncio.wait([t1, t2], return_when=asyncio.FIRST_COMPLETED)


async def handle_client(client, addr, loop):
    dst_str = "(unknown)"
    src_info = "(unknown)"
    try:
        ver = await recvn(client, 1, loop)
        if ver[0] != 5:
            return

        nmethods = (await recvn(client, 1, loop))[0]
        await recvn(client, nmethods, loop)

        await loop.sock_sendall(client, struct.pack("!BB", 5, 0))

        ver, cmd, rsv, atyp = struct.unpack("!BBBB", await recvn(client, 4, loop))
        if cmd != 1:
            await loop.sock_sendall(
                client,
                struct.pack("!BBBB", 5, 7, 0, 1)
                + socket.inet_aton("0.0.0.0")
                + struct.pack("!H", 0),
            )
            return

        if atyp == 1:
            dst_addr = socket.inet_ntop(socket.AF_INET, await recvn(client, 4, loop))
        elif atyp == 3:
            dlen = (await recvn(client, 1, loop))[0]
            dst_addr = (await recvn(client, dlen, loop)).decode()
        elif atyp == 4:
            dst_addr = socket.inet_ntop(
                socket.AF_INET6, await recvn(client, 16, loop)
            )
        else:
            return

        dst_port = struct.unpack("!H", await recvn(client, 2, loop))[0]
        dst_str = f"{dst_addr}:{dst_port}"

        rejected = False
        use_ipv6 = False
        resolved = None

        if atyp == 4:
            use_ipv6 = True
            resolved = (dst_addr, dst_port, 0, 0)
        elif atyp == 3:
            infos = await loop.getaddrinfo(
                dst_addr, dst_port, type=socket.SOCK_STREAM
            )
            for info in infos:
                if info[0] == socket.AF_INET6:
                    use_ipv6 = True
                    resolved = info[4]
                    break
            if not use_ipv6 and infos:
                if PROXY_IPV4_ENABLED:
                    resolved = infos[0][4]
                else:
                    rejected = True
        elif atyp == 1:
            if PROXY_IPV4_ENABLED:
                resolved = (dst_addr, dst_port)
            else:
                rejected = True

        if rejected:
            await loop.sock_sendall(
                client,
                struct.pack("!BBBB", 5, 3, 0, 1)
                + socket.inet_aton("0.0.0.0")
                + struct.pack("!H", 0),
            )
            print(
                f"[PROXY] REJECT from {addr[0]}:{addr[1]} → {dst_str} (IPv6 only)",
                flush=True,
            )
            return

        if resolved is None:
            return

        if use_ipv6 and NETWORK_ADDR is not None:
            src_ip = get_random_assigned_ipv6()
            if src_ip is None:
                src_info = "(error: no available source IPv6)"
                print(
                    f"[PROXY] CONNECT from {addr[0]}:{addr[1]} → {dst_str} {src_info}",
                    flush=True,
                )
                return

            remote = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
            remote.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
            remote.setblocking(False)
            remote.bind((src_ip, 0))
            await loop.sock_connect(remote, (resolved[0], resolved[1]))
            src_info = f"via source {src_ip}"
        else:
            remote = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            remote.setblocking(False)
            await loop.sock_connect(remote, (resolved[0], resolved[1]))
            src_info = "(direct IPv4)"

        bnd = remote.getsockname()
        if len(bnd) == 4:
            resp = (
                struct.pack("!BBBB", 5, 0, 0, 4)
                + socket.inet_pton(socket.AF_INET6, bnd[0])
                + struct.pack("!H", bnd[1])
            )
        else:
            resp = (
                struct.pack("!BBBB", 5, 0, 0, 1)
                + socket.inet_aton(bnd[0])
                + struct.pack("!H", bnd[1])
            )
        await loop.sock_sendall(client, resp)

        print(
            f"[PROXY] CONNECT from {addr[0]}:{addr[1]} → {dst_str} {src_info}",
            flush=True,
        )

        await relay_loop(client, remote, loop)

    except (ConnectionError, TimeoutError, OSError) as e:
        print(
            f"[PROXY] CONNECT from {addr[0]}:{addr[1]} → {dst_str} (error: {e})",
            flush=True,
        )
    finally:
        try:
            client.close()
        except Exception:
            pass


async def main():
    print(f"[PROXY] SOCKS5 proxy listening on 0.0.0.0:{PROXY_PORT}", flush=True)
    if NETWORK_ADDR:
        print(f"[PROXY] Source IPv6 pool: {ROUTED_NET} (on-the-fly)", flush=True)
    else:
        print(
            "[PROXY] WARNING: no TUNNEL_ROUTED_NET set, IPv6 source rotation disabled",
            flush=True,
        )

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("0.0.0.0", PROXY_PORT))
    server.listen(128)
    server.setblocking(False)

    loop = asyncio.get_event_loop()

    while True:
        client, addr = await loop.sock_accept(server)
        client.setblocking(False)
        asyncio.create_task(handle_client(client, addr, loop))


if __name__ == "__main__":
    asyncio.run(main())
