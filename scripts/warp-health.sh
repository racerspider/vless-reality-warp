#!/bin/sh
set -eu
# Feed authentication through stdin so it is absent from curl's argv.
credentials=$(awk 'NF == 2 && $1 != "reload" && $1 !~ /^#/ {print $1 ":" $2; exit}' /etc/gost/users.txt)
[ -n "$credentials" ]
request() {
  printf 'proxy-user = "%s"\n' "$credentials" |
    curl --config - --noproxy '' -fsS --connect-timeout 5 --max-time 15 \
      --proxy socks5h://127.0.0.1:1082 "$1"
}
request https://www.gstatic.com/generate_204 >/dev/null
request https://cloudflare.com/cdn-cgi/trace | grep -Eq '^warp=(on|plus)$'
