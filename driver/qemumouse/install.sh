#!/bin/bash
# qemumouse を超漢字V のディスクイメージに組み込む (bfsk を使用. 仮想マシン停止中に実行)
#   driver/qemumouse/install.sh IMAGE [debug]
# - /SYS/qemumouse として取り込む (アプリケーションタイプ 3, レコードタイプ 31・サブタイプ 0x4530: 既存ドライバと同じ)
# - STARTUP.CMD の kbpd の次に "kerext qemumouse !23" を追加し、vmwdrv をコメントアウトする
set -e
IMG=$1
HERE=$(cd "$(dirname "$0")" && pwd)
BFSK="python3 $HERE/../../tools/bfsk/bfsk.py $IMG"
BIN=$HERE/bin/qemumouse
[ "$2" = debug ] && BIN=$HERE/bin/qemumouse.debug
$BFSK recv -d -a 3 -t 31.0x4530 -N qemumouse "$BIN" /SYS
if ! $BFSK tp /SYS/STARTUP.CMD | grep -q '^kerext[[:space:]]*qemumouse'; then
  $BFSK tp /SYS/STARTUP.CMD \
    | sed -e 's/^\(kerext[[:space:]]*vmwdrv\)/*\1/' \
          -e 's/^\(kerext[[:space:]]*kbpd.*\)$/\1\nkerext\tqemumouse\t!23/' \
    | $BFSK apd -d 0.1 /SYS/STARTUP.CMD -
fi
$BFSK tp /SYS/STARTUP.CMD | grep -n 'kerext' | head -4
