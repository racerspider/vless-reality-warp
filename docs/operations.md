# Operations

## Scope and routing

The Compose project owns one bridge, a WARP service, an Xray frontend, and an
optional probe. It does not join existing external networks or change host routes.
For another installation on the same server, use a different checkout, project
name, bridge subnet, and published ports. Generate a separate identity.

All authenticated TCP client requests go to the WARP SOCKS backend. There is no
Xray `freedom` outbound and no native fallback. REALITY's camouflage connection
uses the frontend's native network; it is not client proxy egress. The WARP
daemon owns its own tunnel and firewall behavior. An unhealthy Docker state
alone is not a packet-level kill switch.

Literal private, loopback, link-local, and multicast destinations are blocked.
Domain names remain unresolved until GOST so destination DNS travels through
WARP's DNS proxies. Consequently this is **not a complete SSRF boundary**:
hostnames resolving to private addresses are not covered by Xray's literal-IP
rules. Give access only to trusted users; use an audited backend egress firewall
if untrusted clients need access.

## Health and recovery

```sh
./compose.sh ps
./compose.sh exec -T warp /bin/sh /healthcheck/functional.sh
python3 manage.py verify          # requires the diagnostics probe
./compose.sh logs --tail 80 warp xray
```

Logs can include destination or registration details. Inspect them locally and
redact them before sharing. Xray access logging is disabled and container logs
are rotated. Registration files must not be deleted as a troubleshooting shortcut.

The WARP health timeout is 35 seconds for two requests capped at 15 seconds each.
Its 1 GiB memory limit contains growth; it does not fix a leak. Adjust limits
after measuring your workload and host capacity.

`restart: unless-stopped` restarts exited containers. Docker does **not** restart
a running container merely because it is unhealthy. Investigate health failures,
then restart just the affected service, for example `./compose.sh restart warp`.
There is deliberately no privileged recovery daemon. Integrate an external
monitor if you need alerts or a bounded recovery policy; choose one recovery owner.

If WARP passes but the probe fails, check the camouflage host, client identity,
clock synchronization, and Xray configuration. If the probe passes but an external
client fails, check firewall rules, the public endpoint, and the client's network.

## Change settings or upgrade

Back up first. Change `.env` for the public endpoint, SNI, bind address, or ports.
Changing SNI requires distributing new client files; changing bridge addresses
requires a coordinated maintenance plan. Changing project name after deployment
creates a separate Compose project and is not a rename procedure.

```sh
python3 manage.py render
python3 manage.py check
./compose.sh pull xray
./compose.sh up -d --no-deps --force-recreate xray
./compose.sh --profile diagnostics up -d --no-deps --force-recreate probe
python3 manage.py verify
```

For a WARP upgrade, review the upstream release, change the digest in
`versions.env`, pull `warp`, then recreate only `warp` and wait for health.
Check both the probe and an external client afterward. Do not replace pinned
references with floating tags. Dependency pins provide reproducibility, not a
claim that a release is current or free of vulnerabilities.

Updating a bind-mounted file atomically does not update the inode mounted in an
existing container. Always recreate the corresponding container after `render`.
If backend credentials change, recreate WARP as well as Xray and the probe.

## Back up and restore

The authoritative private files are `.env`, `secrets/identity.json`, and
`data/warp/`. Keep the matching source revision and `versions.env` with the backup.
`runtime/` is regenerable. Backups contain credentials and must stay outside Git.
Keep an encrypted off-host copy and test restoration on an isolated replacement.

For a consistent registration backup, plan a short outage and stop only this
project's `probe`, `xray`, and `warp` services. Archive `.env`, `secrets/`,
`data/warp/`, and `versions.env` with restrictive permissions using your normal
backup tool. Restart WARP, wait for health, then start Xray and the probe.

Restore source at the recorded revision and restore those private files before
running `render` and `check`. Restore the parent directories' `0700` permissions
and `secrets/identity.json` / `.env` to `0600`. Start WARP first and verify both
local and external paths. Do not run `init` over the restored identity.

For a failed release, restore the previous source, image pins, and matching
configuration, then recreate only changed services and verify. Never roll back
WARP registration state blindly or delete data to force re-registration.
