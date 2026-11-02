#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
# qemukbpd を超漢字V のディスクイメージに組み込む (bfsk を使用. 仮想マシン停止中に実行)
#   driver/qemukbpd/install.sh IMAGE [debug] [log|log2|log3] [poll]
#     debug         デバッグ版を組み込む
#     log/log2/log3 動作ログをコンソール (シリアル) に出す (STARTUP.CMD の引数になる)
#     poll          割り込みを使わず 10ms ごとのポーリングで動かす
# - /SYS/qemukbpd として取り込む (アプリケーションタイプ 3, レコードタイプ 31・サブタイプ 0x4530)
# - STARTUP.CMD の kbpd の次に "kerext qemukbpd !28 [引数]" を追加 (既にあれば置き換え) し、
#   lowkbpd・qemumouse・vmwdrv をコメントアウトする
set -e
IMG=$1; shift
HERE=$(cd "$(dirname "$0")" && pwd)
BFSK="python3 $HERE/../../tools/bfsk/bfsk.py $IMG"
BIN=$HERE/bin/qemukbpd
ARGS=
for a in "$@"; do
  case $a in
    debug) BIN=$HERE/bin/qemukbpd.debug ;;
    log|log2|log3|poll) ARGS="$ARGS $a" ;;
    *) echo "unknown option: $a" >&2; exit 1 ;;
  esac
done
LINE="kerext\tqemukbpd\t!28"
[ -n "$ARGS" ] && LINE="$LINE\t${ARGS# }"
$BFSK recv -d -a 3 -t 31.0x4530 -N qemukbpd "$BIN" /SYS
$BFSK tp /SYS/STARTUP.CMD \
  | sed -e 's/^\(kerext[[:space:]]*\(vmwdrv\|lowkbpd\|qemumouse\)\)/*\1/' \
        -e '/^kerext[[:space:]]*qemukbpd/d' \
        -e "s/^\(kerext[[:space:]]*kbpd[[:space:]].*\)\$/\1\n$LINE/" \
  | $BFSK apd -d 0.1 /SYS/STARTUP.CMD -
$BFSK tp /SYS/STARTUP.CMD | grep -n 'kerext' | head -6
