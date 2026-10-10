import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import manage


def example():
    cfg = {**manage.read_env(manage.ROOT / "versions.env"),
           **manage.read_env(manage.ROOT / ".env.example")}
    cfg.update(SERVER_ADDRESS="203.0.113.10", REALITY_SERVER_NAME="www.example.com")
    # Synthetic values, used only for structural tests; never accepted credentials.
    identity = dict(uuid="00000000-0000-4000-8000-000000000000", private_key="A" * 43,
                    public_key="B" * 43, short_id="0123456789abcdef",
                    proxy_user="test_user_12345678", proxy_password="test_password_12345678")
    return cfg, identity


class ConfigurationTests(unittest.TestCase):
    def test_only_client_egress_is_authenticated_warp(self):
        cfg, identity = example()
        configs = manage.configurations(cfg, identity)
        server = configs["server.json"]
        self.assertEqual({o["protocol"] for o in server["outbounds"]}, {"socks", "blackhole"})
        self.assertEqual(server["outbounds"][0]["settings"]["pass"], identity["proxy_password"])
        self.assertEqual(server["routing"]["rules"][0]["network"], "udp")
        self.assertEqual(server["routing"]["rules"][0]["outboundTag"], "blocked")
        self.assertFalse(configs["client.mihomo.json"]["proxies"][0]["udp"])

    def test_client_and_probe_match_server_identity(self):
        cfg, identity = example()
        configs = manage.configurations(cfg, identity)
        for name in ("probe.json", "client.xray.json"):
            outbound = configs[name]["outbounds"][0]
            self.assertEqual(outbound["settings"]["vnext"][0]["users"][0]["id"], identity["uuid"])
            self.assertEqual(outbound["streamSettings"]["realitySettings"]["password"], identity["public_key"])
            self.assertNotIn(identity["proxy_password"], json.dumps(configs[name]))
            self.assertNotIn(identity["private_key"], json.dumps(configs[name]))
        self.assertEqual(configs["client.xray.json"]["inbounds"][0]["listen"], "127.0.0.1")

    def test_rejects_bad_ports_subnet_and_shell_input(self):
        cfg, identity = example()
        for key, value in [("PUBLIC_PORT", "0"), ("PUBLIC_PORT", "65536"),
            ("REALITY_SERVER_NAME", "example.com;touch marker"),
            ("WARP_IPV4", cfg["XRAY_IPV4"]), ("WARP_IPV4", "172.30.90.1"),
            ("NETWORK_SUBNET", "0.0.0.0/0"), ("XRAY_IMAGE", "image:latest")]:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                manage.configurations({**cfg, key: value}, identity)

    def test_backend_credentials_cannot_inject_curl_config(self):
        cfg, identity = example()
        for bad in ['bad"\nurl = "https://example.com', "two words", "dollar$sign"]:
            with self.assertRaises(ValueError):
                manage.configurations(cfg, {**identity, "proxy_password": bad})

    def test_repeat_init_refuses_rotation(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(manage, "ROOT", Path(directory)):
            (Path(directory) / ".env").write_text("existing")
            with self.assertRaisesRegex(ValueError, "Already initialized"):
                manage.initialize(None)

    def test_render_preserves_identity_and_protects_host_files(self):
        cfg, identity = example()
        with tempfile.TemporaryDirectory() as directory, patch.object(manage, "ROOT", Path(directory)):
            root = Path(directory)
            manage.write_file(root / "secrets/identity.json", json.dumps(identity))
            with patch.object(manage, "settings", return_value=cfg), contextlib.redirect_stdout(io.StringIO()):
                manage.render()
                before = (root / "runtime/server.json").read_bytes()
                manage.render()
            self.assertEqual((root / "runtime/server.json").read_bytes(), before)
            self.assertEqual(json.loads((root / "secrets/identity.json").read_text()), identity)
            self.assertEqual((root / "secrets/identity.json").stat().st_mode & 0o777, 0o600)
            self.assertEqual((root / "runtime").stat().st_mode & 0o777, 0o700)

    def test_verify_rejects_native_egress(self):
        args = type("Args", (), {"port": 1090})()
        with patch.object(manage, "run", return_value="warp=off\n"):
            with self.assertRaisesRegex(RuntimeError, "not confirmed"):
                manage.verify(args)

    def test_verify_requires_second_hostname(self):
        args = type("Args", (), {"port": 1090})()
        with patch.object(manage, "run", side_effect=["warp=on\n", RuntimeError("DNS failed")]):
            with self.assertRaisesRegex(RuntimeError, "DNS failed"):
                manage.verify(args)


if __name__ == "__main__":
    unittest.main()
