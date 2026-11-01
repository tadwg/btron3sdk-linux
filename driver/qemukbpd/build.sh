#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
# qemukbpd のビルド (btron3sdk コンテナ内で実行)
#   podman run --rm -v $PWD:/workspace:z btron3sdk:latest bash /workspace/driver/qemukbpd/build.sh
# 出力: driver/qemukbpd/bin/qemukbpd (リリース), qemukbpd.debug (DEBUG_PRINT 有効)
set -e
source /usr/local/brightv/env.sh
HERE=$(cd "$(dirname "$0")" && pwd)
D=$BD/driver/qemukbpd
rm -rf $D && mkdir -p $D/pcat $D/pcat.debug
cp -r $HERE/src $D/src
ln -sf ../src/Makefile $D/pcat/Makefile
ln -sf ../src/Makefile $D/pcat.debug/Makefile
MK="BD=$BD GNUs=$GNUs GNU_BD=$GNU_BD GNUi386=$GNUi386"
(cd $D/pcat && make $MK)
(cd $D/pcat.debug && make $MK mode=debug)
mkdir -p $HERE/bin
cp $D/pcat/qemukbpd $HERE/bin/qemukbpd
cp $D/pcat.debug/qemukbpd $HERE/bin/qemukbpd.debug
ls -l $HERE/bin
