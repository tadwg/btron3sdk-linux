#!/bin/bash
# SPDX-License-Identifier: MIT
# 超漢字V のディスクイメージを QEMU で起動する (podman コンテナ内, 外部ネットワーク無し)
#
#   tools/qemu/run-qemu.sh IMAGE          起動 (バックグラウンド)
#   tools/qemu/run-qemu.sh stop           sync してから停止
#
# 環境変数:
#   QEMU_NAME   コンテナ名 (既定 btron-qemu)
#   QEMU_IMAGE  コンテナイメージ (既定 btron-qemu:latest, tools/qemu/Containerfile)
#   QEMU_NIC    NIC のモデル (既定 pcnet. 体験版はネットワークアダプタが必須)
#   QEMU_MEM    メモリ MB (既定 256)
#   QEMU_SNAPSHOT=1  イメージに書き込まない (変更は QEMU 終了時に捨てる)
#   QEMU_RTC    時計の初期値 (既定 localtime. 例: 2026-10-01T09:00:00)
#
# IMAGE と同じディレクトリに serial.sock / serial.log / qmp.sock を作る.
# 操作は tools/qemu/btctl (シリアルの CLI、画面取得、マウス) を使う.
set -e
NAME=${QEMU_NAME:-btron-qemu}
CIMG=${QEMU_IMAGE:-btron-qemu:latest}
HERE=$(cd "$(dirname "$0")" && pwd)

if [ "$1" = stop ]; then
  "$HERE/btctl" cli sync >/dev/null 2>&1 || true
  podman stop -t 5 "$NAME" >/dev/null
  exit 0
fi

IMG=$(readlink -f "$1")
DIR=$(dirname "$IMG")
FILE=$(basename "$IMG")
case "$FILE" in
  *.vmdk) FMT=vmdk ;;
  *.vdi)  FMT=vdi ;;
  *)      FMT=raw ;;
esac
SNAP=
[ -n "$QEMU_SNAPSHOT" ] && SNAP=-snapshot
podman rm -f "$NAME" >/dev/null 2>&1 || true
rm -f "$DIR/serial.sock" "$DIR/qmp.sock" "$DIR/serial.log"
podman run -d --name "$NAME" --network none --memory 700m \
  -v "$DIR":/w:Z -v "$HERE":/t:ro,Z "$CIMG" \
  qemu-system-i386 -m "${QEMU_MEM:-256}" \
    -drive file="/w/$FILE",format=$FMT,if=ide,index=0 \
    -nic user,model="${QEMU_NIC:-pcnet}",restrict=on \
    -display none -vga std \
    -chardev socket,id=s0,path=/w/serial.sock,server=on,wait=off,logfile=/w/serial.log \
    -serial chardev:s0 \
    -qmp unix:/w/qmp.sock,server=on,wait=off \
    -rtc base="${QEMU_RTC:-localtime}" -no-reboot $SNAP >/dev/null
echo "started $NAME ($FILE). serial log: $DIR/serial.log"
