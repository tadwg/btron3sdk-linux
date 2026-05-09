# =============================================================================
# BTRON3 (超漢字V) Cross Development Environment
# ベースイメージ: Rocky Linux 9
#
# このファイルは Claude Sonnet 4.6 (Anthropic) の支援を受けて書かれました。
# Written with the assistance of Claude Sonnet 4.6 (Anthropic).
#
# brightv クロス開発環境 (超漢字開発者向けアーカイブ) を自動ダウンロードし、
# パッチ適用・サンプルビルドまで行う。
#
# 使い方:
#   podman build -t btron3sdk:latest .
#   podman run --rm -it -v $PWD:/workspace:z btron3sdk:latest
# =============================================================================

FROM rockylinux:9

# --- 基本ツール・32bit ライブラリ・開発ツール ---
RUN dnf install -y \
    glibc.i686 \
    libgcc.i686 \
    libstdc++.i686 \
    zlib.i686 \
    perl \
    make \
    file \
    which \
    wget \
    glibc-langpack-ja \
    socat \
    && dnf clean all

WORKDIR /usr/local/brightv

# --- brightv アーカイブのダウンロード ---
# 超漢字クロス開発環境アーカイブ (Personal Media Corporation)
# https://www.chokanji.com/developer/download.html
RUN wget -q https://www.chokanji.com/archive/brightv.common.tar.gz -O /tmp/brightv.common.tar.gz \
    && wget -q https://www.chokanji.com/archive/brightv.linux.tar.gz -O /tmp/brightv.linux.tar.gz \
    && wget -q https://www.chokanji.com/archive/gterm.tar.gz         -O /tmp/gterm.tar.gz

# --- アーカイブ展開 ---
RUN cd /usr/local/brightv \
    && tar xzf /tmp/brightv.common.tar.gz \
    && tar xzf /tmp/brightv.linux.tar.gz \
    && cp -r /usr/local/brightv/tool/etc /usr/local/brightv/ \
    && tar xzf /tmp/gterm.tar.gz -C /tmp \
    && cp /tmp/gterm/linux/gterm /usr/local/brightv/etc/ \
    && chmod +x /usr/local/brightv/etc/gterm \
    && rm -f /tmp/*.tar.gz

# --- パッチ適用 ---

# [1] mymake: gmake → /usr/bin/make
RUN sed -i 's|gmake|/usr/bin/make|g' /usr/local/brightv/etc/mymake

# [2] etc/makerules: /lib/cpp を EUC-JP 対応ラッパーに差し替え
#     gcc 2.95 は EUC-JP コメントを含む .d ファイルを -traditional-cpp なしで拒否する
RUN printf '#!/bin/bash\nexec /usr/bin/cpp -traditional-cpp "$@"\n' \
        > /usr/local/bin/cpp-eucjp \
    && chmod +x /usr/local/bin/cpp-eucjp \
    && ln -sf /usr/local/bin/cpp-eucjp /lib/cpp \
    && sed -i 's|CPP = /lib/cpp|CPP = /usr/local/bin/cpp-eucjp|g' \
           /usr/local/brightv/etc/makerules \
    && sed -i 's|CPP = /usr/libexec/cpp|CPP = /usr/local/bin/cpp-eucjp|g' \
           /usr/local/brightv/etc/makerules

# [3] driver/etc/makerules: 末尾の余分な endif を削除
#     pcat/ ディレクトリから make すると "extraneous endif" エラーになる
RUN sed -i '75{/^endif$/d}' /usr/local/brightv/driver/etc/makerules

# [4] driver/sample/pcat: Makefile シンボリックリンクを確認・作成
RUN ls /usr/local/brightv/driver/sample/pcat/Makefile 2>/dev/null \
    || ln -sf ../src/Makefile /usr/local/brightv/driver/sample/pcat/Makefile

# --- 環境変数スクリプト ---
RUN printf '\
export BD=/usr/local/brightv\n\
export GNUs=/usr/bin\n\
export GNU_BD=$BD/tool/gnu\n\
export GNUi386=$GNU_BD/i386-unknown-gnu\n\
export PATH=$BD/tool/gnu/bin:$BD/etc:/usr/local/bin:$PATH\n\
export LANG=ja_JP.eucJP\n\
' > /usr/local/brightv/env.sh \
    && echo 'source /usr/local/brightv/env.sh' >> /etc/bashrc

# --- gterm TCP 接続ラッパー ---
RUN printf '#!/bin/bash\n\
# gterm-tcp HOST PORT [gterm オプション]\n\
HOST="${1:-127.0.0.1}"\n\
PORT="${2:-9999}"\n\
shift 2 2>/dev/null\n\
socat TCP:$HOST:$PORT PTY,link=/tmp/brightv-pty,rawer &\n\
SPID=$!\n\
sleep 0.5\n\
PTY=$(readlink -f /tmp/brightv-pty)\n\
/usr/local/brightv/etc/gterm -l$PTY $*\n\
kill $SPID 2>/dev/null\n\
' > /usr/local/bin/gterm-tcp \
    && chmod +x /usr/local/bin/gterm-tcp

# --- サンプルアプリのビルド ---
RUN . /usr/local/brightv/env.sh \
    && MKARGS="BD=$BD GNUs=$GNUs GNU_BD=$GNU_BD GNUi386=$GNUi386" \
    && for sample in sample1 sample2 tagsamp1 tagsamp2; do \
         echo "=== Building appl/$sample ===" \
         && cd $BD/appl/$sample/pcat \
         && make clean 2>/dev/null || true \
         && make $MKARGS \
         && echo "OK: $sample" \
         || echo "FAILED: $sample"; \
       done

# --- サンプルドライバのビルド ---
RUN . /usr/local/brightv/env.sh \
    && MKARGS="BD=$BD GNUs=$GNUs GNU_BD=$GNU_BD GNUi386=$GNUi386" \
    && echo "=== Building driver/sample ===" \
    && cd $BD/driver/sample/pcat \
    && make clean 2>/dev/null || true \
    && make $MKARGS \
    && echo "OK: driver/sample" \
    || echo "FAILED: driver/sample"

CMD ["/bin/bash"]
