#!/bin/bash
# Owner terminal action: retain the current helper and add schema0013 compatibility.
set -euo pipefail
export PATH=/usr/bin:/usr/sbin:/bin
source_dir=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
source=$source_dir/workbench_admin.py
helper=/usr/local/sbin/twichui-workbench-admin
backup=/var/lib/twichui-workbench-admin/helper-before-schema0013.py
test "$EUID" = 1000
test ! -L "$source"
printf '%s  %s\n' 'd7c20bde1a7fe764caca764965a469ddf6f4516c709747da2984dd4085cabdae' "$source" | sha256sum -c --quiet
printf '%s  %s\n' '1fd4a561188560242d5cb5df9bd583d8c922cfa4282e4bd1e46b8dca04bc3afb' "$helper" | sha256sum -c --quiet
# Privileges are required only for these exact root-owned helper files.
sudo -v
sudo test ! -e "$backup"
sudo install -o root -g root -m 0755 -- "$helper" "$backup"
sudo install -o root -g root -m 0755 -- "$source" "$helper"
printf '%s  %s\n' 'd7c20bde1a7fe764caca764965a469ddf6f4516c709747da2984dd4085cabdae' "$helper" | sha256sum -c --quiet
printf '%s\n' '{"op":"status"}' | sudo -n "$helper"
printf '%s\n' 'Schema0012/0013 settlement checks installed. Existing service grants and network restrictions are unchanged.'
