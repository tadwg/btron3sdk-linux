#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
# qemukbpd を超漢字V のディスクイメージに組み込む (bfsk を使用. 仮想マシン停止中に実行)
#   driver/qemukbpd/install.sh IMAGE [debug]
# - /SYS/qemukbpd として取り込む (アプリケーションタイプ 3, レコードタイプ 31・サブタイプ 0x4530)
# - STARTUP.CMD の kbpd の次に "kerext qemukbpd !28" を追加し、
#   lowkbpd・qemumouse・vmwdrv をコメントアウトする
set -e
IMG=$1
HERE=$(cd "$(dirname "$0")" && pwd)
BFSK="python3 $HERE/../../tools/bfsk/bfsk.py $IMG"
BIN=$HERE/bin/qemukbpd
[ "$2" = debug ] && BIN=$HERE/bin/qemukbpd.debug
$BFSK recv -d -a 3 -t 31.0x4530 -N qemukbpd "$BIN" /SYS
$BFSK tp /SYS/STARTUP.CMD \
  | sed -e 's/^\(kerext[[:space:]]*\(vmwdrv\|lowkbpd\|qemumouse\)\)/*\1/' \
  | if $BFSK tp /SYS/STARTUP.CMD | grep -q '^kerext[[:space:]]*qemukbpd'; then cat; else
      sed -e 's/^\(kerext[[:space:]]*kbpd[[:space:]].*\)$/\1\nkerext\tqemukbpd\t!28/'; fi \
  | $BFSK apd -d 0.1 /SYS/STARTUP.CMD -
$BFSK tp /SYS/STARTUP.CMD | grep -n 'kerext' | head -6
