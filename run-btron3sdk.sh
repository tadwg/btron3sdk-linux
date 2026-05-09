#!/bin/bash
# BTRON3 (超漢字V) 開発環境コンテナ起動スクリプト
#
# 使い方:
#   ./run-btron3sdk.sh               # 開発シェルに入る
#   ./run-btron3sdk.sh gterm         # gterm 起動 (TCP 9999 経由)
#   ./run-btron3sdk.sh gterm HOST PORT  # 指定ホストに gterm 接続

IMAGE="${BTRON3SDK_IMAGE:-btron3sdk:latest}"
WORKDIR="${BTRON3SDK_WORKDIR:-$PWD}"
mkdir -p "$WORKDIR"

if [[ "$1" == "gterm" ]]; then
  HOST="${2:-127.0.0.1}"
  PORT="${3:-9999}"
  echo "gterm: TCP $HOST:$PORT 経由でデバッグ接続します"
  exec podman run --rm -it \
    --network=host \
    -v "$WORKDIR":/workspace:z \
    "$IMAGE" \
    bash -c "
      source /usr/local/brightv/env.sh
      socat TCP:${HOST}:${PORT} PTY,link=/tmp/brightv-pty,rawer &
      SPID=\$!
      sleep 0.5
      PTY=\$(readlink -f /tmp/brightv-pty)
      echo \"接続先: \$PTY\"
      /usr/local/brightv/etc/gterm -l\$PTY
      kill \$SPID 2>/dev/null
    "
fi

exec podman run --rm -it \
  -v "$WORKDIR":/workspace:z \
  --network=host \
  "$IMAGE" \
  bash -c "source /usr/local/brightv/env.sh && cd /workspace && exec bash"
