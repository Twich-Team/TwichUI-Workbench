#!/bin/bash
# Owner runs this as Rich in the server terminal after approving the exact design.
# Only fixed system tools run under sudo. This script itself always runs as Rich.
set -euo pipefail
export PATH=/usr/bin:/usr/sbin:/bin
umask 077
test "$EUID" = 1000
test "$(hostname -s)" = llm
helper_sha=1fd4a561188560242d5cb5df9bd583d8c922cfa4282e4bd1e46b8dca04bc3afb
source_dir=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
source=$source_dir/workbench_admin.py
helper=/usr/local/sbin/twichui-workbench-admin
policy=/etc/twichui-workbench-admin
state=/var/lib/twichui-workbench-admin
pending=/etc/sudoers.d/twichui-workbench-admin.pending
sudoers=/etc/sudoers.d/twichui-workbench-admin
scope=/user.slice/user-1000.slice/session-78.scope
test ! -L "$source"
printf '%s  %s\n' "$helper_sha" "$source" | /usr/bin/sha256sum -c --quiet
for directory in /etc /etc/sudoers.d /usr /usr/local /usr/local/sbin /var /var/lib; do
  test -d "$directory" && test ! -L "$directory"
  read -r owner mode < <(/usr/bin/stat -c '%u %a' "$directory")
  test "$owner" = 0 && (( (8#$mode & 022) == 0 ))
done
scope_path=/sys/fs/cgroup$scope
test -d "$scope_path" && test ! -L "$scope_path"
test "$(stat -c %u:%a "$scope_path/cgroup.procs")" = 0:644
for path in "$helper" "$policy" "$state" "$pending" "$sudoers"; do
  test ! -e "$path" && test ! -L "$path"
done
scratch=$(mktemp -d /tmp/twichui-workbench-admin-bootstrap.XXXXXXXX)
trap 'rm -- "$scratch/grant.json" "$scratch/sudoers" 2>/dev/null || true; rmdir -- "$scratch"' EXIT
now=$(date +%s)
expires=$((now + 7 * 86400))
boot=$(cat /proc/sys/kernel/random/boot_id)
grant=$(printf '{"created":%s,"expires":%s,"boot_id":"%s","cgroup":"%s","session_id":"78"}' "$now" "$expires" "$boot" "$scope")
rule='rich llm=(root) NOPASSWD:NOSETENV: /usr/local/sbin/twichui-workbench-admin ""'
printf '%s\n' "$grant" > "$scratch/grant.json"
printf '%s\n' "$rule" > "$scratch/sudoers"
grant_sha=$(printf '%s\n' "$grant" | /usr/bin/sha256sum | cut -d' ' -f1)
rule_sha=$(printf '%s\n' "$rule" | /usr/bin/sha256sum | cut -d' ' -f1)
/usr/bin/sudo -v
/usr/bin/sudo /usr/bin/install -o root -g root -m 0755 -- "$source" "$helper"
printf '%s  %s\n' "$helper_sha" "$helper" | /usr/bin/sha256sum -c --quiet
/usr/bin/sudo /usr/bin/install -d -o root -g root -m 0700 -- "$policy" "$state"
/usr/bin/sudo /usr/bin/install -o root -g root -m 0600 -- "$scratch/grant.json" "$policy/grant.json"
test "$(/usr/bin/sudo /usr/bin/sha256sum "$policy/grant.json" | cut -d' ' -f1)" = "$grant_sha"
/usr/bin/sudo /usr/bin/install -o root -g root -m 0440 -- "$scratch/sudoers" "$pending"
test "$(/usr/bin/sudo /usr/bin/sha256sum "$pending" | cut -d' ' -f1)" = "$rule_sha"
/usr/bin/sudo /usr/sbin/visudo -c -f "$pending"
# Publish only after the root copy and its policy verify. The pending file is ignored by sudoers.
/usr/bin/sudo /usr/bin/mv -T --no-clobber -- "$pending" "$sudoers"
test ! -e "$pending"
if ! /usr/bin/sudo /usr/sbin/visudo -c -f /etc/sudoers; then
  /usr/bin/sudo /usr/bin/mv -T -- "$sudoers" "$pending"
  exit 1
fi
printf 'Workbench administrator grant prepared for session 78 through %s UTC.\n' "$(date -u -d "@$expires" +'%Y-%m-%d %H:%M:%S')"
