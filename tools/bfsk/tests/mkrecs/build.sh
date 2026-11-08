#!/bin/bash
# SPDX-License-Identifier: MIT
# mkrecs のビルド (btron3sdk コンテナ内で実行)
#   podman run --rm -v $PWD:/workspace:z btron3sdk:latest bash /workspace/tools/bfsk/tests/mkrecs/build.sh
# 出力: tools/bfsk/tests/mkrecs/bin/mkrecs
set -e
source /usr/local/brightv/env.sh
HERE=$(cd "$(dirname "$0")" && pwd)
D=$BD/util/mkrecs
rm -rf $D && mkdir -p $D/pcat
cp -r $HERE/src $D/src
ln -sf ../src/Makefile $D/pcat/Makefile
(cd $D/pcat && make BD=$BD GNUs=$GNUs GNU_BD=$GNU_BD GNUi386=$GNUi386)
mkdir -p $HERE/bin
cp $D/pcat/mkrecs $HERE/bin/mkrecs
ls -l $HERE/bin
