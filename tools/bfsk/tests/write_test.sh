#!/bin/bash
# bfsk 書き込みコマンドのテスト. 各操作の後に check を実行する.
# 使い方: tests/write_test.sh IMAGE   (IMAGE は書き換えるので必ずコピーを渡すこと)
set -u
IMG=$1
HERE=$(cd "$(dirname "$0")/.." && pwd)
B="python3 $HERE/bfsk.py $IMG"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
fail=0
chk() {
  out=$($B check 2>&1); rc=$?
  if [ $rc -ne 0 ]; then echo "  CHECK NG after: $*"; echo "$out" | head -8; fail=1
  else echo "  ok   $* | $(echo "$out" | tail -1)"; fi
}
run() { echo "+ $*"; $B "$@" || { echo "  CMD FAILED: $*"; fail=1; }; chk "$*"; }
expect_fail() { echo "+ (エラーになるはず) $*"; if $B "$@" 2>/dev/null; then echo "  UNEXPECTED SUCCESS"; fail=1; fi; chk "$*"; }

head -c 100000 /dev/urandom > $TMP/r.bin
head -c 32767 /dev/urandom > $TMP/r32767.bin   # 末尾に 1 バイトだけ余る
head -c 32768 /dev/urandom > $TMP/r32768.bin   # ちょうど 1 ブロック (32KB ブロックの場合)
head -c 9000000 /dev/urandom > $TMP/big.bin    # 255 ブロック超 (接続インデックス)
printf 'hello\n' > $TMP/h.txt
: > $TMP/empty.bin

run mkf -v /WORK/新規A /WORK/B
run recv $TMP/r.bin $TMP/h.txt $TMP/r32767.bin $TMP/r32768.bin $TMP/empty.bin $TMP/big.bin /WORK
for f in r.bin r32767.bin r32768.bin big.bin empty.bin; do
  $B tp WORK/$f | cmp -s - $TMP/$f && echo "  data ok $f" || { echo "  DATA NG $f"; fail=1; }
done
run recv -d $TMP/h.txt /WORK
run apd /WORK/新規A /WORK/h.txt
run apd -t 8.0x45 /WORK/新規A /WORK/r.bin
run apd -p 0 /WORK/新規A /WORK/h.txt
run apd -d 1.1 /WORK/新規A
echo "+ apd -d 0.1 /WORK/新規A - (標準入力で差し替え)"
printf 'replaced\n' | $B apd -d 0.1 /WORK/新規A - && $B tp /WORK/新規A | grep -q replaced && echo "  stdin ok" || { echo "  STDIN NG"; fail=1; }
chk "apd stdin"
echo "+ (エラーになるはず) 空の標準入力"
if $B apd -d 0.1 /WORK/新規A - < /dev/null 2>/dev/null; then echo "  UNEXPECTED SUCCESS"; fail=1; fi
$B fs /WORK/新規A | grep -q '^  0: 31' && echo "  unchanged ok" || { echo "  CHANGED"; fail=1; }
run ln /WORK/h.txt /WORK/新規A
run ln -p 0 -a 0x8000.0.0.0.0 /SBOOT /WORK/B
run cp /WORK/新規A /WORK/B
run cp -r -v /WORK/B /WORK/新規A
run rcp /WORK/新規A /WORK/C
run rcp /WORK/r.bin /WORK/C
expect_fail rm /WORK/B
run rm -r -v /WORK/新規A /WORK/B
run empf /WORK/C
run rm /WORK/big.bin /WORK/r32767.bin
run ren /WORK/h.txt テキスト
run ls -f -l /SYS:0 /SYS:1
run mkf /WORK/X
run recv $TMP/h.txt /WORK/X
run apd -d 0.1 /WORK/X            # h.txt へのリンクだけ消す → 参照 0 のくずファイル
run cleanup -v
echo "fail=$fail"
exit $fail
