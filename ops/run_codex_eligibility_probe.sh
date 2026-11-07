#!/bin/bash
# Invoked by the owner in their server terminal after the exact one-shot approval.
set -euo pipefail
umask 077
if [[ $# != 1 || ( $1 != 1 && $1 != 2 ) ]]; then
  printf '%s\n' 'Use slot 1, or slot 2 only for the recorded follow-up.' >&2
  exit 2
fi
slot=$1
base=$(/usr/bin/realpath -e -- "$(dirname -- "${BASH_SOURCE[0]}")")
cd "$base"
[[ $(/usr/bin/id -u) == 1000 && $(/usr/bin/stat -c '%u:%a' "$base") == 1000:700 ]]
/usr/bin/sha256sum --status --check SHA256SUMS
[[ -f window.json && ! -L window.json && $(/usr/bin/stat -c '%u:%a' window.json) == 1000:600 ]]
if [[ $slot == 2 ]]; then
  [[ -f followup.json && ! -L followup.json && ! -e probe-2.jsonl ]]
else
  [[ ! -e probe-1.jsonl ]]
fi
unit="workbench-codex-eligibility-$slot"
[[ $(/usr/bin/systemctl show "$unit.service" -p LoadState --value) == not-found ]]
deny='0.0.0.0/8 10.0.0.0/8 100.64.0.0/10 127.0.0.0/8 169.254.0.0/16 172.16.0.0/12 192.168.0.0/16 224.0.0.0/4 240.0.0.0/4 ::/128 ::1/128 fc00::/7 fe80::/10 ff00::/8'
exec /usr/bin/sudo -- /usr/bin/systemd-run --system --wait --pipe --collect --expand-environment=no \
  "--unit=$unit" --description='Workbench read-only Codex eligibility' \
  -p User=rich -p Group=rich -p Slice=system.slice -p Type=exec -p UMask=0077 \
  -p NoNewPrivileges=yes -p RuntimeMaxSec=120s -p TimeoutStopSec=5s -p KillMode=control-group \
  -p "RuntimeDirectory=$unit" -p RuntimeDirectoryMode=0700 \
  -p "IPAddressDeny=$deny" -p IPAddressAllow=127.0.0.53/32 -p SocketBindDeny=any \
  -p MemoryMax=512M -p MemorySwapMax=0 -p CPUQuota=100% -p TasksMax=64 -p LimitNOFILE=128 \
  /usr/bin/env -i HOME=/home/rich PATH=/usr/bin:/bin LANG=C.UTF-8 PYTHONDONTWRITEBYTECODE=1 \
  "RUNTIME_DIRECTORY=/run/$unit" "PYTHONPATH=$base/release/backend" \
  /usr/bin/python3 "$base/release/scripts/codex_eligibility_probe.py" "$slot"
