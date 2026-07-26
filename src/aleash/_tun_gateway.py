#!/usr/bin/env python3
"""
TUN-based network gateway for aleash sandboxes.

Replaces pasta: creates a TUN device in the sandbox's isolated network namespace,
intercepts all TCP and UDP, and gates each new connection through
/api/proxy/request-approval before proxying it from the host.

Usage: sys.executable _tun_gateway.py <child_pid> <sandbox_id> <server_url> <proxy_port>
"""
import asyncio
import ctypes
import fcntl
import os
import random
import socket
import struct
import sys
from pathlib import Path

import httpx

# ── constants ──────────────────────────────────────────────────────────────────

CLONE_NEWUSER = 0x10000000
CLONE_NEWNET  = 0x40000000
TUNSETIFF     = 0x400454ca
IFF_TUN       = 0x0001
IFF_NO_PI     = 0x1000

_PROTO_TCP    = 6
_PROTO_UDP    = 17

_HOST_ADDR    = "169.254.0.1"   # host alias inside sandbox (maps to 127.0.0.1)
_SANDBOX_ADDR = "169.254.0.2"   # sandbox IP on the TUN link

_HOST_IP      = socket.inet_aton(_HOST_ADDR)

# ── ioctl / netlink constants for interface setup ──────────────────────────────
SIOCGIFFLAGS   = 0x8913
SIOCSIFFLAGS   = 0x8914
SIOCSIFADDR    = 0x8916
SIOCSIFNETMASK = 0x891c
NETLINK_ROUTE  = 0
RTM_NEWROUTE   = 24
NLM_F_REQUEST  = 0x01
NLM_F_ACK      = 0x04   # always get a response (error=0 on success)
NLM_F_CREATE   = 0x400
RT_TABLE_MAIN  = 254
RTPROT_STATIC  = 4
RT_SCOPE_UNIVERSE = 0
RTN_UNICAST    = 1
RTA_OIF        = 4

TH_FIN = 0x01
TH_SYN = 0x02
TH_RST = 0x04
TH_PSH = 0x08
TH_ACK = 0x10

# ── checksum ───────────────────────────────────────────────────────────────────

def _cksum(data: bytes) -> int:
    if len(data) % 2:
        data += b"\x00"
    # Must unpack as big-endian (network byte order); array.array("H") is native
    # (little-endian on x86_64) which produces a byte-swapped, incorrect result.
    s = sum(struct.unpack("!%dH" % (len(data) // 2), data))
    s = (s >> 16) + (s & 0xFFFF)
    s += s >> 16
    return (~s) & 0xFFFF


def _tcp_cksum(src: bytes, dst: bytes, seg: bytes) -> int:
    ph = src + dst + struct.pack("!BBH", 0, _PROTO_TCP, len(seg))
    return _cksum(ph + seg)


def _udp_cksum(src: bytes, dst: bytes, seg: bytes) -> int:
    ph = src + dst + struct.pack("!BBH", 0, _PROTO_UDP, len(seg))
    return _cksum(ph + seg)


# ── packet builders ────────────────────────────────────────────────────────────

def _mk_ip(src: bytes, dst: bytes, proto: int, payload: bytes) -> bytes:
    total = 20 + len(payload)
    hdr = struct.pack(
        "!BBHHHBBH4s4s",
        0x45, 0, total,
        random.randint(0, 0xFFFF), 0,
        64, proto, 0,
        src, dst,
    )
    ck = _cksum(hdr)
    return hdr[:10] + struct.pack("!H", ck) + hdr[12:] + payload


def _mk_tcp(
    src_ip: bytes, dst_ip: bytes,
    sport: int, dport: int,
    seq: int, ack: int, flags: int,
    win: int = 65535,
    data: bytes = b"",
) -> bytes:
    seg = struct.pack(
        "!HHIIHHHH",
        sport, dport,
        seq & 0xFFFFFFFF, ack & 0xFFFFFFFF,
        (5 << 12) | (flags & 0xFF),
        win, 0, 0,
    ) + data
    ck = _tcp_cksum(src_ip, dst_ip, seg)
    seg = seg[:16] + struct.pack("!H", ck) + seg[18:]
    return _mk_ip(src_ip, dst_ip, _PROTO_TCP, seg)


def _mk_udp(
    src_ip: bytes, dst_ip: bytes,
    sport: int, dport: int,
    data: bytes,
) -> bytes:
    seg = struct.pack("!HHHH", sport, dport, 8 + len(data), 0) + data
    ck = _udp_cksum(src_ip, dst_ip, seg)
    seg = seg[:6] + struct.pack("!H", ck) + seg[8:]
    return _mk_ip(src_ip, dst_ip, _PROTO_UDP, seg)


# ── packet parsers ─────────────────────────────────────────────────────────────

def _parse_ip(data: bytes):
    """Return (version, proto, src, dst, payload) or None on error."""
    if len(data) < 20:
        return None
    ihl = (data[0] & 0x0F) * 4
    if ihl < 20 or len(data) < ihl:
        return None
    version = data[0] >> 4
    proto = data[9]
    src = data[12:16]
    dst = data[16:20]
    return version, proto, src, dst, data[ihl:]


def _parse_tcp(payload: bytes):
    """Return (sport, dport, seq, ack, flags, tcp_payload) or None on error."""
    if len(payload) < 20:
        return None
    sport, dport, seq, ack, off_flags, _win, _ck, _urg = struct.unpack(
        "!HHIIHHHH", payload[:20]
    )
    flags = off_flags & 0x1FF
    hdr_len = ((off_flags >> 12) & 0xF) * 4
    if hdr_len < 20 or len(payload) < hdr_len:
        return None
    return sport, dport, seq, ack, flags, payload[hdr_len:]


def _parse_udp(payload: bytes):
    """Return (sport, dport, udp_payload) or None on error."""
    if len(payload) < 8:
        return None
    sport, dport, _length, _ck = struct.unpack("!HHHH", payload[:8])
    return sport, dport, payload[8:]


# ── SCM_RIGHTS helpers ─────────────────────────────────────────────────────────

def _send_fd(sock: socket.socket, fd: int) -> None:
    sock.sendmsg([b"\x00"], [(socket.SOL_SOCKET, socket.SCM_RIGHTS,
                              struct.pack("i", fd))])


def _recv_fd(sock: socket.socket) -> int:
    _, anc, _, _ = sock.recvmsg(1, socket.CMSG_LEN(struct.calcsize("i")))
    if not anc:
        raise RuntimeError("TUN setup fork exited without sending fd")
    return struct.unpack("i", anc[0][2])[0]


# ── interface configuration (no subprocess — capabilities don't survive exec) ──

def _configure_iface(ifname: str, addr: str, prefix: int) -> None:
    """Set IP address/netmask, bring interface up, add default route."""
    mask_int = (0xFFFFFFFF << (32 - prefix)) & 0xFFFFFFFF
    mask_str = socket.inet_ntoa(struct.pack("!I", mask_int))
    name_b = ifname.encode()[:15].ljust(16, b"\x00")

    def _ifreq_addr(ip: str) -> bytes:
        # sockaddr_in: sa_family(2) + sin_port(2) + sin_addr(4) + padding(8)
        sa = struct.pack("HH4s8x", socket.AF_INET, 0, socket.inet_aton(ip))
        return name_b + sa

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        fcntl.ioctl(s, SIOCSIFADDR, _ifreq_addr(addr))
        fcntl.ioctl(s, SIOCSIFNETMASK, _ifreq_addr(mask_str))
        ifreq = bytearray(name_b + b"\x00" * 16)
        res = fcntl.ioctl(s, SIOCGIFFLAGS, bytes(ifreq))
        flags = struct.unpack_from("H", res, 16)[0] | 0x1  # IFF_UP
        struct.pack_into("H", ifreq, 16, flags)
        fcntl.ioctl(s, SIOCSIFFLAGS, bytes(ifreq))
    finally:
        s.close()

    # Add default route: RTM_NEWROUTE via NETLINK_ROUTE
    ifindex = socket.if_nametoindex(ifname)
    rtmsg = struct.pack("<BBBBBBBBI",
        socket.AF_INET, 0, 0, 0,       # family, dst_len, src_len, tos
        RT_TABLE_MAIN, RTPROT_STATIC,
        RT_SCOPE_UNIVERSE, RTN_UNICAST, 0,
    )
    rta = struct.pack("<HHI", 8, RTA_OIF, ifindex)
    payload = rtmsg + rta
    nlhdr = struct.pack("<IHHII",
        16 + len(payload), RTM_NEWROUTE, NLM_F_REQUEST | NLM_F_CREATE | NLM_F_ACK, 1, 0,
    )
    nl = socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, NETLINK_ROUTE)
    try:
        nl.bind((0, 0))
        nl.sendto(nlhdr + payload, (0, 0))
        resp = nl.recv(4096)
    finally:
        nl.close()
    if len(resp) >= 20:
        nl_type = struct.unpack_from("<H", resp, 4)[0]
        if nl_type == 2:  # NLMSG_ERROR
            err = struct.unpack_from("<i", resp, 16)[0]
            if err != 0:
                raise OSError(-err, os.strerror(-err))


# ── TUN setup (runs in a fork to avoid affecting the main process's namespaces) ─

def _setns(libc, path: str, flag: int) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        rc = libc.setns(fd, flag)
        if rc != 0:
            raise OSError(ctypes.get_errno(), f"setns({path})")
    finally:
        os.close(fd)


def _setup_tun(child_pid: int) -> int:
    """Fork into child namespaces, create+configure TUN, return fd via SCM_RIGHTS."""
    parent_s, child_s = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    pid = os.fork()
    if pid == 0:
        parent_s.close()
        try:
            libc = ctypes.CDLL("libc.so.6", use_errno=True)
            # Enter child user namespace first (grants ns_capable on the net ns)
            _setns(libc, f"/proc/{child_pid}/ns/user", CLONE_NEWUSER)
            _setns(libc, f"/proc/{child_pid}/ns/net",  CLONE_NEWNET)

            tfd = os.open("/dev/net/tun", os.O_RDWR)
            fcntl.ioctl(tfd, TUNSETIFF,
                        struct.pack("16sH14x", b"aleash0", IFF_TUN | IFF_NO_PI))

            _configure_iface("aleash0", _SANDBOX_ADDR, 30)

            _send_fd(child_s, tfd)
        except Exception as exc:
            print(f"aleash gateway: TUN setup failed: {exc}", file=sys.stderr)
        finally:
            os._exit(0)
    else:
        child_s.close()
        tfd = _recv_fd(parent_s)
        os.waitpid(pid, 0)
        parent_s.close()
        return tfd


# ── per-connection TCP state ───────────────────────────────────────────────────

class TcpConn:
    __slots__ = (
        "key", "gw",
        "client_isn", "server_isn",
        "client_next", "server_next",
        "host_reader", "host_writer",
        "state", "_relay",
    )

    def __init__(
        self, key: tuple, gw: "TunGateway",
        client_isn: int, server_isn: int,
        hr: asyncio.StreamReader, hw: asyncio.StreamWriter,
    ) -> None:
        self.key = key
        self.gw = gw
        self.client_isn = client_isn
        self.server_isn = server_isn
        self.client_next = (client_isn + 1) & 0xFFFFFFFF
        self.server_next = (server_isn + 1) & 0xFFFFFFFF
        self.host_reader = hr
        self.host_writer = hw
        self.state = "ESTABLISHED"
        self._relay: asyncio.Task = asyncio.ensure_future(self._host_relay())

    async def _host_relay(self) -> None:
        """Read from host socket, inject into TUN as TCP data packets."""
        src_ip, sport, dst_ip, dport = self.key
        try:
            while True:
                await self.host_writer.drain()
                data = await self.host_reader.read(32768)
                if not data:
                    break
                pkt = _mk_tcp(dst_ip, src_ip, dport, sport,
                               self.server_next, self.client_next,
                               TH_ACK | TH_PSH, data=data)
                self.server_next = (self.server_next + len(data)) & 0xFFFFFFFF
                self.gw._inject(pkt)
        except Exception:
            pass
        finally:
            if self.state != "CLOSED":
                self.state = "CLOSED"
                pkt = _mk_tcp(dst_ip, src_ip, dport, sport,
                               self.server_next, self.client_next,
                               TH_FIN | TH_ACK)
                self.server_next = (self.server_next + 1) & 0xFFFFFFFF
                self.gw._inject(pkt)
            self.gw._tcp.pop(self.key, None)

    def on_data(self, flags: int, tcp_payload: bytes) -> None:
        """Handle a data/FIN/RST packet from the sandbox."""
        if tcp_payload:
            self.host_writer.write(tcp_payload)
            self.client_next = (self.client_next + len(tcp_payload)) & 0xFFFFFFFF
            # ACK the received data
            src_ip, sport, dst_ip, dport = self.key
            ack_pkt = _mk_tcp(dst_ip, src_ip, dport, sport,
                               self.server_next, self.client_next, TH_ACK)
            self.gw._inject(ack_pkt)

        if flags & TH_FIN:
            self.client_next = (self.client_next + 1) & 0xFFFFFFFF
            try:
                self.host_writer.close()
            except Exception:
                pass
            src_ip, sport, dst_ip, dport = self.key
            pkt = _mk_tcp(dst_ip, src_ip, dport, sport,
                           self.server_next, self.client_next, TH_ACK)
            self.gw._inject(pkt)

        if flags & TH_RST:
            self.state = "CLOSED"
            try:
                self.host_writer.close()
            except Exception:
                pass
            self.gw._tcp.pop(self.key, None)
            self._relay.cancel()

    def close(self) -> None:
        self.state = "CLOSED"
        try:
            self.host_writer.close()
        except Exception:
            pass
        self._relay.cancel()


# ── gateway ────────────────────────────────────────────────────────────────────

class TunGateway:
    def __init__(
        self,
        tun_fd: int,
        sandbox_id: str,
        server_url: str,
        proxy_port: int,
        host_dns: str,
    ) -> None:
        self._fd = tun_fd
        self._sandbox_id = sandbox_id
        self._server_url = server_url
        self._proxy_port = proxy_port
        self._host_dns = host_dns
        self._tcp: dict[tuple, TcpConn] = {}
        self._pending: set[tuple] = set()

    def _inject(self, pkt: bytes) -> None:
        try:
            os.write(self._fd, pkt)
        except OSError:
            pass

    async def _gate(self, host: str, port: int) -> bool:
        try:
            async with httpx.AsyncClient(timeout=70.0) as c:
                r = await c.post(
                    f"{self._server_url}/api/proxy/request-approval",
                    json={"sandbox_id": self._sandbox_id,
                          "domain": f"{host}:{port}"},
                )
            return r.json().get("action") == "allow"
        except Exception:
            return False

    def _on_readable(self) -> None:
        try:
            data = os.read(self._fd, 65536)
        except OSError:
            return
        asyncio.ensure_future(self._dispatch(data))

    async def _dispatch(self, data: bytes) -> None:
        parsed = _parse_ip(data)
        if parsed is None:
            return
        version, proto, src, dst, payload = parsed
        if version != 4:
            return
        try:
            if proto == _PROTO_TCP:
                await self._handle_tcp(src, dst, payload)
            elif proto == _PROTO_UDP:
                await self._handle_udp(src, dst, payload)
        except Exception:
            pass

    async def _handle_tcp(self, src_ip: bytes, dst_ip: bytes, payload: bytes) -> None:
        parsed = _parse_tcp(payload)
        if parsed is None:
            return
        sport, dport, seq, ack, flags, tcp_payload = parsed
        key = (src_ip, sport, dst_ip, dport)

        # New connection (SYN without ACK)
        if (flags & TH_SYN) and not (flags & TH_ACK):
            if key in self._pending:
                return  # already gating this connection
            if key in self._tcp:
                # SYN retransmit after established — resend SYN-ACK
                conn = self._tcp[key]
                pkt = _mk_tcp(dst_ip, src_ip, dport, sport,
                               conn.server_isn, conn.client_isn + 1,
                               TH_SYN | TH_ACK)
                self._inject(pkt)
                return
            # Block direct HTTP/HTTPS — must use mitmproxy (via env var proxy)
            if dport in (80, 443):
                self._inject(_mk_tcp(dst_ip, src_ip, dport, sport,
                                     0, seq + 1, TH_RST | TH_ACK))
                return
            self._pending.add(key)
            asyncio.ensure_future(self._connect(key, seq))
            return

        # Existing connection
        conn = self._tcp.get(key)
        if conn is None:
            if not (flags & TH_RST):
                self._inject(_mk_tcp(dst_ip, src_ip, dport, sport,
                                     ack, 0, TH_RST))
            return
        conn.on_data(flags, tcp_payload)

    async def _connect(self, key: tuple, client_isn: int) -> None:
        src_ip, sport, dst_ip, dport = key
        try:
            # Map 169.254.0.1 → 127.0.0.1 (host loopback alias, no gating)
            if dst_ip == _HOST_IP:
                real_ip = self._host_dns if dport == 53 else "127.0.0.1"
                allowed = True
            else:
                real_ip = socket.inet_ntoa(dst_ip)
                allowed = await self._gate(real_ip, dport)

            if not allowed:
                self._inject(_mk_tcp(dst_ip, src_ip, dport, sport,
                                     0, (client_isn + 1) & 0xFFFFFFFF,
                                     TH_RST | TH_ACK))
                return

            hr, hw = await asyncio.open_connection(real_ip, dport)
            server_isn = random.randint(0, 0xFFFFFFFF)

            # Send SYN-ACK
            pkt = _mk_tcp(dst_ip, src_ip, dport, sport,
                           server_isn, (client_isn + 1) & 0xFFFFFFFF,
                           TH_SYN | TH_ACK)
            self._inject(pkt)

            conn = TcpConn(key, self, client_isn, server_isn, hr, hw)
            self._tcp[key] = conn
        except Exception:
            self._inject(_mk_tcp(dst_ip, src_ip, dport, sport,
                                 0, (client_isn + 1) & 0xFFFFFFFF,
                                 TH_RST | TH_ACK))
        finally:
            self._pending.discard(key)

    async def _handle_udp(self, src_ip: bytes, dst_ip: bytes, payload: bytes) -> None:
        parsed = _parse_udp(payload)
        if parsed is None:
            return
        sport, dport, udp_payload = parsed

        # DNS: forward to host resolver (no gating)
        if dport == 53:
            srv = self._host_dns
            try:
                loop = asyncio.get_running_loop()
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.setblocking(False)
                await loop.sock_connect(sock, (srv, 53))
                await loop.sock_sendall(sock, udp_payload)
                reply = await asyncio.wait_for(
                    loop.sock_recv(sock, 65535), timeout=10.0
                )
                sock.close()
            except Exception:
                return
            self._inject(_mk_udp(dst_ip, src_ip, dport, sport, reply))
            return

        # Block direct QUIC (HTTP/3) to 80/443
        if dport in (80, 443):
            return

        # Host alias: map to 127.0.0.1, no gating
        if dst_ip == _HOST_IP:
            real_ip = "127.0.0.1"
            allowed = True
        else:
            real_ip = socket.inet_ntoa(dst_ip)
            allowed = await self._gate(real_ip, dport)

        if not allowed:
            return

        try:
            loop = asyncio.get_running_loop()
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setblocking(False)
            await loop.sock_connect(sock, (real_ip, dport))
            await loop.sock_sendall(sock, udp_payload)
            reply = await asyncio.wait_for(
                loop.sock_recv(sock, 65535), timeout=30.0
            )
            sock.close()
        except Exception:
            return
        self._inject(_mk_udp(dst_ip, src_ip, dport, sport, reply))

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        loop.add_reader(self._fd, self._on_readable)
        await asyncio.sleep(float("inf"))


# ── host DNS detection ─────────────────────────────────────────────────────────

def _detect_host_dns() -> str:
    """Read the host's real DNS server before any namespace setup."""
    # Prefer upstream resolvers from systemd-resolved (non-stub)
    for path in ("/run/systemd/resolve/resolv.conf",):
        try:
            for line in Path(path).read_text().splitlines():
                line = line.strip()
                if line.startswith("nameserver "):
                    addr = line.split()[1]
                    if ":" not in addr:  # skip IPv6
                        return addr
        except OSError:
            pass
    # Fall back to resolving the symlink manually
    try:
        real = os.path.realpath("/etc/resolv.conf")
        for line in Path(real).read_text().splitlines():
            line = line.strip()
            if line.startswith("nameserver "):
                addr = line.split()[1]
                if ":" not in addr and addr not in ("127.0.0.53",):
                    return addr
    except OSError:
        pass
    # systemd-resolved stub is available on the host even if not upstream
    return "127.0.0.53"


# ── entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    if len(sys.argv) < 5:
        print("usage: _tun_gateway.py <child_pid> <sandbox_id> <server_url> <proxy_port>",
              file=sys.stderr)
        sys.exit(1)

    child_pid   = int(sys.argv[1])
    sandbox_id  = sys.argv[2]
    server_url  = sys.argv[3]
    proxy_port  = int(sys.argv[4])   # mitmproxy port (currently unused in gateway logic)
    host_dns    = _detect_host_dns()

    tun_fd = _setup_tun(child_pid)
    gw = TunGateway(tun_fd, sandbox_id, server_url, proxy_port, host_dns)
    asyncio.run(gw.run())


if __name__ == "__main__":
    main()
