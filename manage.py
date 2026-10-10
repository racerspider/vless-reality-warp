#!/usr/bin/env python3
"""Generate private credentials, render Xray configs, and verify the proxy chain."""
import argparse
import base64
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile
import uuid
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parent
PRIVATE_NETS = [
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8",
    "169.254.0.0/16", "172.16.0.0/12", "192.168.0.0/16", "224.0.0.0/4",
    "240.0.0.0/4", "::/128", "::1/128", "fc00::/7",
    "fe80::/10", "ff00::/8",
]


def read_env(path):
    values = {}
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or key in values:
            raise ValueError(f"Invalid or duplicate setting in {path.name}")
        values[key] = value.strip()
    return values


def hostname(value):
    if len(value) > 253 or not all(
        re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
        for label in value.split(".")
    ):
        raise ValueError("Expected a DNS hostname without scheme, path, or port")


def validate_settings(cfg):
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,62}", cfg["COMPOSE_PROJECT_NAME"]):
        raise ValueError("Invalid Compose project name")
    try:
        ipaddress.ip_address(cfg["SERVER_ADDRESS"])
    except ValueError:
        hostname(cfg["SERVER_ADDRESS"])
    hostname(cfg["REALITY_SERVER_NAME"])
    for key in ("PUBLIC_PORT", "PROBE_PORT"):
        if not cfg[key].isdigit() or not 1 <= int(cfg[key]) <= 65535:
            raise ValueError(f"Invalid {key}")
    if cfg["PUBLIC_PORT"] == cfg["PROBE_PORT"]:
        raise ValueError("Public and probe ports must differ")
    ipaddress.IPv4Address(cfg["PUBLIC_BIND"])
    subnet = ipaddress.IPv4Network(cfg["NETWORK_SUBNET"])
    if subnet.prefixlen != 24 or not subnet.is_private:
        raise ValueError("Use a private /24 bridge subnet")
    addresses = [ipaddress.IPv4Address(cfg[k]) for k in ("WARP_IPV4", "XRAY_IPV4", "PROBE_IPV4")]
    if len(set(addresses)) != 3 or any(a not in subnet or a in
            (subnet.network_address, subnet.broadcast_address, subnet.network_address + 1)
            for a in addresses):
        raise ValueError("Container addresses must be distinct usable addresses in the subnet, excluding gateway .1")
    for key in ("XRAY_IMAGE", "WARP_IMAGE"):
        if not re.fullmatch(r"[a-zA-Z0-9./:_-]+@sha256:[a-f0-9]{64}", cfg[key]):
            raise ValueError(f"{key} must use an immutable SHA256 digest")
    return cfg


def settings():
    return validate_settings({**read_env(ROOT / "versions.env"), **read_env(ROOT / ".env")})


def private_dir(path):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


def write_file(path, content, mode=0o600):
    """Replace atomically; a failed write never truncates the previous file."""
    private_dir(path.parent) if path.parent != ROOT else None
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".stage-")
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, mode)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def run(args, **kwargs):
    result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, **kwargs)
    if result.returncode:
        # Third-party errors may contain configuration secrets. Never echo them.
        raise RuntimeError(f"{Path(args[0]).name} failed (exit {result.returncode}); inspect locally")
    return result.stdout


def validate_identity(identity):
    uuid.UUID(identity["uuid"])
    for key in ("private_key", "public_key"):
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", identity[key]) or len(
                base64.urlsafe_b64decode(identity[key] + "=")) != 32:
            raise ValueError("Invalid REALITY key")
    if not re.fullmatch(r"[a-f0-9]{16}", identity["short_id"]):
        raise ValueError("Invalid short ID")
    for key in ("proxy_user", "proxy_password"):
        if not re.fullmatch(r"[a-zA-Z0-9_-]{16,128}", identity[key]):
            raise ValueError("Invalid backend credential")


def client_config(cfg, identity, address, port, listen="127.0.0.1", socks_port=1080):
    return {
        "log": {"loglevel": "warning", "access": "none"},
        "inbounds": [{"listen": listen, "port": socks_port, "protocol": "socks",
                      "settings": {"auth": "noauth", "udp": False}}],
        "outbounds": [{"protocol": "vless", "settings": {"vnext": [{
            "address": address, "port": port, "users": [{"id": identity["uuid"],
            "encryption": "none", "flow": "xtls-rprx-vision"}]}]},
            "streamSettings": {"network": "tcp", "security": "reality", "realitySettings": {
                "fingerprint": "chrome", "serverName": cfg["REALITY_SERVER_NAME"],
                "password": identity["public_key"], "shortId": identity["short_id"]}}}],
    }


def configurations(cfg, identity):
    validate_settings(cfg)
    validate_identity(identity)
    server = {
        "log": {"loglevel": "warning", "access": "none"},
        "inbounds": [{"tag": "clients", "listen": "0.0.0.0", "port": 8443,
            "protocol": "vless", "settings": {"decryption": "none", "clients": [{
                "id": identity["uuid"], "flow": "xtls-rprx-vision"}]},
            "streamSettings": {"network": "tcp", "security": "reality", "realitySettings": {
                "show": False, "target": cfg["REALITY_SERVER_NAME"] + ":443", "xver": 0,
                "minClientVer": "1.8.2", "serverNames": [cfg["REALITY_SERVER_NAME"]],
                "privateKey": identity["private_key"], "shortIds": [identity["short_id"]]}}}],
        "outbounds": [
            {"tag": "warp", "protocol": "socks", "settings": {
                "address": cfg["WARP_IPV4"], "port": 1082,
                "user": identity["proxy_user"], "pass": identity["proxy_password"]}},
            {"tag": "blocked", "protocol": "blackhole"}],
        "routing": {"domainStrategy": "AsIs", "rules": [
            {"type": "field", "network": "udp", "outboundTag": "blocked"},
            {"type": "field", "ip": PRIVATE_NETS, "outboundTag": "blocked"},
            {"type": "field", "inboundTag": ["clients"], "outboundTag": "warp"}]},
    }
    probe = client_config(cfg, identity, cfg["XRAY_IPV4"], 8443, listen="0.0.0.0")
    client = client_config(cfg, identity, cfg["SERVER_ADDRESS"], int(cfg["PUBLIC_PORT"]))
    mihomo = {"mixed-port": 1080, "allow-lan": False, "mode": "rule", "log-level": "warning",
        "proxies": [{"name": "reality-warp", "type": "vless", "server": cfg["SERVER_ADDRESS"],
            "port": int(cfg["PUBLIC_PORT"]), "uuid": identity["uuid"], "udp": False,
            "tls": True, "network": "tcp", "flow": "xtls-rprx-vision",
            "servername": cfg["REALITY_SERVER_NAME"], "client-fingerprint": "chrome",
            "reality-opts": {"public-key": identity["public_key"], "short-id": identity["short_id"]}}],
        "rules": ["MATCH,reality-warp"]}
    return {"server.json": server, "probe.json": probe, "client.xray.json": client,
            "client.mihomo.json": mihomo}


def render():
    cfg = settings()
    identity = json.loads((ROOT / "secrets/identity.json").read_text())
    configs = configurations(cfg, identity)
    # These files are readable by the non-root container UID. Their host parent
    # directory is 0700, so other host users cannot traverse it.
    for name, config in configs.items():
        write_file(ROOT / "runtime" / name, json.dumps(config, indent=2) + "\n", 0o644)
    write_file(ROOT / "runtime/gost-users.txt",
               identity["proxy_user"] + " " + identity["proxy_password"] + "\n", 0o644)
    address = cfg["SERVER_ADDRESS"]
    if ":" in address:
        address = "[" + address + "]"
    query = urlencode({"encryption": "none", "security": "reality", "type": "tcp",
        "sni": cfg["REALITY_SERVER_NAME"], "fp": "chrome", "pbk": identity["public_key"],
        "sid": identity["short_id"], "flow": "xtls-rprx-vision"})
    write_file(ROOT / "runtime/client.txt",
        f"vless://{identity['uuid']}@{address}:{cfg['PUBLIC_PORT']}?{query}#reality-warp\n")
    private_dir(ROOT / "data")
    (ROOT / "data/warp").mkdir(exist_ok=True)
    (ROOT / "data/warp").chmod(0o755)
    print("Rendered private runtime files; existing identity preserved.")


def initialize(args):
    if (ROOT / "secrets/identity.json").exists() or (ROOT / ".env").exists():
        raise ValueError("Already initialized or .env exists; use render to preserve credentials")
    cfg = read_env(ROOT / ".env.example")
    cfg.update(SERVER_ADDRESS=args.server, REALITY_SERVER_NAME=args.sni,
               PUBLIC_PORT=str(args.port), PROBE_PORT=str(args.probe_port), NETWORK_SUBNET=args.subnet)
    network = ipaddress.IPv4Network(args.subnet)
    for index, key in enumerate(("WARP_IPV4", "XRAY_IPV4", "PROBE_IPV4"), 2):
        cfg[key] = str(network.network_address + index)
    validate_settings({**read_env(ROOT / "versions.env"), **cfg})
    image = read_env(ROOT / "versions.env")["XRAY_IMAGE"]
    output = run(["docker", "run", "--rm", "--network", "none", "--platform", "linux/amd64", image, "x25519"])
    keys = dict(line.split(":", 1) for line in output.splitlines() if ":" in line)
    identity = {"private_key": keys.get("PrivateKey", keys.get("Private key", "")).strip(),
        "public_key": keys.get("Password (PublicKey)", keys.get("Password", keys.get("Public key", ""))).strip(),
        "uuid": str(uuid.uuid4()), "short_id": secrets.token_hex(8),
        "proxy_user": secrets.token_hex(12), "proxy_password": secrets.token_urlsafe(36)}
    validate_identity(identity)
    write_file(ROOT / "secrets/identity.json", json.dumps(identity, indent=2) + "\n")
    write_file(ROOT / ".env", "".join(f"{k}={v}\n" for k, v in cfg.items()))
    render()


def check():
    cfg = settings()
    for name in ("server.json", "probe.json", "client.xray.json"):
        path = ROOT / "runtime" / name
        if not path.is_file():
            raise ValueError("Missing runtime config; run render first")
        run(["docker", "run", "--rm", "--network", "none", "--platform", "linux/amd64",
            "--mount", f"type=bind,src={path},dst=/etc/xray/config.json,readonly",
            cfg["XRAY_IMAGE"], "run", "-test", "-config", "/etc/xray/config.json"])
    run(["sh", "compose.sh", "--profile", "diagnostics", "config", "--quiet"])
    print("Compose and all three Xray configurations validated.")


def verify(args):
    port = args.port or int(settings()["PROBE_PORT"])
    if not 1 <= port <= 65535:
        raise ValueError("Invalid probe port")
    prefix = ["curl", "--noproxy", "", "-fsS", "--connect-timeout", "5", "--max-time", "20",
              "--proxy", f"socks5h://127.0.0.1:{port}"]
    trace = run(prefix + ["https://cloudflare.com/cdn-cgi/trace"])
    if not any(line in ("warp=on", "warp=plus") for line in trace.splitlines()):
        raise RuntimeError("Proxy answered, but WARP egress was not confirmed")
    run(prefix + ["https://www.gstatic.com/generate_204"])
    print("PASS: REALITY probe path, remote DNS, HTTPS, and WARP egress.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="create a new identity once; never overwrite it")
    init.add_argument("--server", required=True, help="public server IP or DNS name")
    init.add_argument("--sni", required=True, help="reachable TLS 1.3 camouflage hostname")
    init.add_argument("--port", type=int, default=443)
    init.add_argument("--probe-port", type=int, default=1090)
    init.add_argument("--subnet", default="172.30.90.0/24")
    commands.add_parser("render", help="render configs without rotating credentials")
    commands.add_parser("check", help="validate Compose and Xray configurations")
    probe = commands.add_parser("verify", help="check the local REALITY client's SOCKS port")
    probe.add_argument("--port", type=int)
    args = parser.parse_args()
    try:
        if args.command == "init":
            initialize(args)
        elif args.command == "render":
            render()
        elif args.command == "check":
            check()
        else:
            verify(args)
    except (ValueError, RuntimeError, OSError, KeyError) as exc:
        # Avoid printing exception payloads that could contain credential data.
        if isinstance(exc, (ValueError, RuntimeError)) and not isinstance(exc, json.JSONDecodeError):
            print(f"Error: {exc}", file=sys.stderr)
        else:
            print("Error: missing, unreadable, or malformed input; inspect private files locally", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
