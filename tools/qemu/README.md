# QEMU で超漢字V を動かす

ディスクイメージを podman コンテナ内の QEMU で起動し、シリアルの CLI・画面取得・マウス操作を
コマンドラインから行うためのツールです。bfsk (イメージの読み書き) と組み合わせると、
「ビルド → イメージへの書き込み → 起動 → 確認」をすべてコマンドで行えます。

## 準備

```bash
podman build -t btron-qemu:latest -f tools/qemu/Containerfile tools/qemu
```

イメージ側では、シリアルで CLI を使えるように `DEVCONF` の `DEBUGMODE` を 1 にします
(停止中に bfsk で書き換え)。

```bash
B="python3 tools/bfsk/bfsk.py disk.img"
$B tp /SYS/DEVCONF | sed 's/^DEBUGMODE .*/DEBUGMODE 1/' | $B apd -d 0.1 /SYS/DEVCONF -
```

## 起動と操作

```bash
tools/qemu/run-qemu.sh disk.img        # 起動 (バックグラウンド, 外部ネットワーク無し)
tools/qemu/btctl cli 'ls -l /SYS/WORK' # シリアルの CLI でコマンド実行
tools/qemu/btctl screen shot.png       # 画面を disk.img と同じディレクトリに保存
tools/qemu/btctl click 170 129 -2      # 画面座標 (170,129) をダブルクリック (qemumouse が必要)
tools/qemu/btctl mice                  # マウスの状態 ("vmmouse (absolute)" なら絶対座標)
tools/qemu/run-qemu.sh stop            # CLI で sync してから停止
```

`disk.img` と同じディレクトリに `serial.log` (起動ログと CLI の出力)、`serial.sock`、`qmp.sock` ができます。

## 注意

- 体験版のイメージはネットワークアダプタが無いと起動後に「ネットワークアダプタが見つかりません」で
  終了します。既定の NIC は AMD PCnet (`QEMU_NIC=pcnet`) です。QEMU 既定の e1000 では
  「ネットワークデバイスでエラー」になります。コンテナは `--network none` で、外部とは通信しません
- 時計は `-rtc base=localtime` (超漢字V はハードウェア時計をローカル時刻として扱う)
- VMware 用の設定 (`DEVCONF` の `VMW...`、`STARTUP.CMD` の `vmwdrv`) は QEMU では不要です。
  `vmwdrv` は vmmouse を相対座標モードで使うため、絶対座標のマウスには `driver/qemumouse` を使います
- KVM が無い環境ではソフトウェアエミュレーションになり、起動に 30〜50 秒ほどかかります
