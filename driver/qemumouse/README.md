# qemumouse - QEMU 用 絶対座標マウスドライバ (超漢字V)

QEMU 上で動く超漢字V 向けのマウスドライバです。
QEMU の PC マシンがエミュレートする VMware 互換の vmmouse (I/O ポート 0x5658 の「バックドア」)を
絶対座標モードにして、ホストのマウス座標・ボタン・ホイールを kbpd ドライバにポインタイベントとして渡します。
ホストのマウスがウィンドウの端で引っかかったり、ゲストのポインタとずれたりしなくなります。

[tadwg/virtualbox-additions](https://github.com/tadwg/virtualbox-additions) (VirtualBox 用) の
kbpd への送信部分をそのまま使い、デバイスへのアクセスを vmmouse に置き換えたものです。
PCI デバイスや DMA は使わず、I/O 命令だけで動作します。

## 機能

- vmmouse の絶対座標モード (QEMU 既定の `-machine pc` で使える。vmport=on が既定)
- 左 / 右 / 中ボタン (ホイールプッシュ)、ホイール回転
- 10ms 間隔のポーリング

QEMU 9.0 + 超漢字V (体験版イメージ) で、絶対座標での移動 (画面の 1/4・3/4 の位置へ正確に移動)、
各ボタンの押下と解放、ホイール、ダブルクリックで仮身を開く操作を確認しています。

## ファイル構成

```
src/qemumouse.c    ドライバ本体
src/qemumouse.h    インクルードヘッダ
src/Makefile       ビルド用 Makefile
build.sh           ビルド (btron3sdk コンテナ内で実行)
install.sh         ディスクイメージへの組み込み (bfsk を使用)
bin/qemumouse         リリースビルド
bin/qemumouse.debug   デバッグビルド (DEBUG_PRINT 有効. DEBUGMODE 1 のときシリアルに出力)
```

## ビルド

リポジトリ直下で:

```bash
podman build -t btron3sdk:latest .
podman run --rm -v $PWD:/workspace:Z btron3sdk:latest bash /workspace/driver/qemumouse/build.sh
```

`driver/qemumouse/bin/` に `qemumouse` と `qemumouse.debug` ができます。

## 導入

仮想マシンを止めた状態で、ディスクイメージ (raw / VMDK / VDI) に組み込みます。

```bash
driver/qemumouse/install.sh disk.img            # リリース版
driver/qemumouse/install.sh disk.img debug      # デバッグ版
```

`/SYS/qemumouse` を取り込み、`STARTUP.CMD` の `kerext kbpd` の次に次の行を追加します。
VMware 用の `vmwdrv` は vmmouse を相対座標モードで使うため、コメントアウトします。

```
kerext	qemumouse	!23
```

手作業で導入する場合は、`qemumouse` を `/SYS` に置き、上の行を `STARTUP.CMD` に追加してください。

## エラーコード

起動失敗時は `ER_NOSPT (0xFFFD0000)` の下位 16bit に原因が入ります。

| 値 | 原因 |
|---|---|
| 0x0001 | kbpd のオープン失敗 |
| 0x0002 | kbpd のメールボックス ID 取得失敗 |
| 0x0003 | バックドアが応答しない (QEMU の vmport が無効) |
| 0x0004 | vmmouse が応答しない |
| 0x000A / 0x000B | ポーリングタスクの生成 / 起動失敗 |

## ライセンス

GPL version 2 またはそれ以降 (virtualbox-additions と同じ)。
