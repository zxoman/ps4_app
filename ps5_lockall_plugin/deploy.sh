#!/usr/bin/env bash
# Upload the built plugin to the PS5 over FTP.
#
#   ./deploy.sh <ps5-ip> [port]
#
# The etaHEN FTP port can differ between setups; if the default below does not
# connect, find the port your FTP/toolbox uses and pass it explicitly. You can
# always copy to a USB stick instead (see README).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
IP="${1:?usage: $0 <ps5-ip> [port]}"
PORT="${2:-2121}"
PLUGIN="$HERE/build/lockall_ps5.plugin"
DEST="data/etaHEN/plugins/lockall_ps5.plugin"

if [ ! -f "$PLUGIN" ]; then
    echo "build first: ./build.sh" >&2
    exit 1
fi

echo "uploading $PLUGIN"
echo "        -> ftp://$IP:$PORT/$DEST"
curl --ftp-create-dirs -T "$PLUGIN" "ftp://$IP:$PORT/$DEST" --user "anonymous:anonymous"

echo "done. reboot the PS5 (or reload plugins from the etaHEN toolbox) to load it."
