# qemukbpd - QEMU 用 キーボード + 絶対座標マウス ドライバ (超漢字V)

QEMU 上の超漢字V で、lowkbpd (PS/2 キーボード・マウスの実 IO ドライバ) の代わりに使うドライバです。
キーボードは QEMU の i8042 から、マウスは vmmouse (VMware 互換バックドア) から絶対座標で読み、
kbpd に送ります。PS/2 マウスのデータは kbpd に送りません。

## qemumouse との違い

`driver/qemumouse` は lowkbpd と一緒に使いますが、QEMU は vmmouse のイベントごとにダミーの PS/2 マウスの
パケット (相対移動 +1、ボタンすべて OFF) を作るため、lowkbpd 経由で「ボタンを離した」イベントが
kbpd に届き、**ドラッグができません** (`driver/kbpdsniff` で確認)。
qemukbpd は lowkbpd を置き換え、PS/2 マウスのパケットを読み捨てるので、この問題が起きません。

## 機能

- キーボード (キーボード ID 0x41 = IBM 106 日本語キーボード)
  - lowkbpd と同じキートップコードを送る (scan code set 1。E0/E1 付きのキーは 0x80 以降。
    対応表は `driver/kbprobe/keytop-ibm106.md`)
  - NumLock は内部で処理し、キーボードの LED を切り替える。OFF (起動直後) のときテンキーの数字・小数点は
    scan code + 0x48。テンキーのキーは `KeyInStat.tenkey` = 1
  - CapsLock の LED は kbpd の入力モード (英大) に合わせる
  - Menu キーは送らない (lowkbpd と同じ)。キーリピート (同じキーの make の繰り返し) は送らない
- マウス: vmmouse の絶対座標、左 / 右 / 中ボタン、ホイール (`driver/qemumouse` と同じ)
- kbpd からのコマンド (`INP_FLG` で登録したイベントフラグ): 入力モード (LED)、サスペンド / リジューム
- 割り込み待ちで動く (常時のポーリングをしない)
  - キーボードは IRQ1、vmmouse は PS/2 マウスの IRQ12 で読む。QEMU は vmmouse のイベントごとにダミーの
    PS/2 マウスのパケットを作るので、その割り込みを「vmmouse にデータがある」合図に使い、パケットは捨てる
  - 割り込みの取りこぼしに備えて、0.5 秒ごとにも確認する
  - 引数 `poll` を付けると、割り込みを使わず 10ms ごとのポーリングで動く (割り込みがうまく動かない環境向け)
- 引数 `log` / `log2` / `log3` で動作ログをコンソール (シリアル) に出す (後述)

## 確認したこと (QEMU 9.0 + 超漢字V)

- kbpd からのコマンド (入力モード・スキャン速度・感度) を受け付ける
- 左右 Shift + ↑ で PD シミュレーションに入り、矢印キーでポインタが動く (kbpd がメタキーと変換表を正しく解釈)。
  解除後は矢印キーがウィンドウに届く
- NumLock で LED (ON = 2 / OFF = 0) とテンキーのコードが lowkbpd と同じに切り替わる。Pause = 0x82、PrintScreen = 0x54
- ウィンドウのタイトルバーをドラッグして移動できる。PS/2 マウスのパケットは 0 個
- 絶対座標の移動、各ボタン、ホイール (qemumouse と同じ部分)
- 割り込み待ち: 何もしていない 1 分間の割り込みは初期化の応答だけ (キーボード 2 回、マウス 1 回)。
  キー入力・移動・ドラッグはすべて割り込みで受け取れ、0.5 秒ごとの確認で見つかったデータは 0 件。
  何もしていないときの QEMU の CPU 使用率 (KVM なし) は、10ms ポーリングの約 1.7〜1.9% に対して約 1.4%
- ダブルクリックの判定は 10ms ポーリングのときと変わらない (押す・離すの間隔 125ms で開き、150ms で開かない。
  lowkbpd + qemumouse でも同じ。判定する間隔は超漢字の設定による)

## ビルドと導入

```bash
podman run --rm -v $PWD:/workspace:Z btron3sdk:latest bash /workspace/driver/qemukbpd/build.sh
driver/qemukbpd/install.sh disk.img            # リリース版
driver/qemukbpd/install.sh disk.img log        # リリース版 + 動作ログ
driver/qemukbpd/install.sh disk.img debug      # デバッグ版 (既定で log2 相当のログ)
driver/qemukbpd/install.sh disk.img poll       # 割り込みを使わない
```

`install.sh` は `/SYS/qemukbpd` を取り込み、`STARTUP.CMD` の `kerext kbpd` の次に `kerext qemukbpd !28`
(引数を指定したときは `kerext qemukbpd !28 log` のように後ろに付ける。既にあれば置き換える) を追加し、
`lowkbpd`・`qemumouse`・`vmwdrv` をコメントアウトします。GitHub では main への push で GitHub Actions が
`bin/` を更新します。

### bfsk で手作業で組み込む

`install.sh` を使わずに、`tools/bfsk/bfsk.py` で同じことができます (仮想マシンは止めておくこと)。

```bash
BFSK="python3 tools/bfsk/bfsk.py disk.img"

# 1. ドライバを /SYS/qemukbpd として取り込む
#    -d: 同じ名前のファイルがあれば置き換える / -a 3: アプリケーションタイプ / -t: レコードタイプ.サブタイプ
$BFSK recv -d -a 3 -t 31.0x4530 -N qemukbpd driver/qemukbpd/bin/qemukbpd /SYS

# 2. STARTUP.CMD を取り出して編集する
$BFSK tp /SYS/STARTUP.CMD > startup.txt
#    - "kerext lowkbpd ..." "kerext qemumouse ..." "kerext vmwdrv ..." の行頭に * を付けてコメントにする
#    - "kerext kbpd ..." の次の行に次の 1 行を追加する (区切りはタブ)
#        kerext	qemukbpd	!28

# 3. 編集した内容で STARTUP.CMD のテキストレコードを置き換える (- は標準入力)
$BFSK apd -d 0.1 /SYS/STARTUP.CMD - < startup.txt

# 4. 確かめる
$BFSK tp /SYS/STARTUP.CMD | grep kerext
$BFSK stat /SYS/qemukbpd
$BFSK check
```

元に戻すときは、`STARTUP.CMD` の `lowkbpd` (と必要なら `vmwdrv`) の行頭の `*` を外し、
`qemukbpd` の行を削除するかコメントにします。ドライバのファイルは `$BFSK rm /SYS/qemukbpd` で消せます。

**QEMU 専用です。** 実機や VMware / VirtualBox では lowkbpd を使ってください (i8042 や vmmouse の動作を QEMU 前提にしています)。

## 動作ログ

不具合の調査用に、リリース版でも動作ログをコンソールに出せます。QEMU では、コンソールをシリアルポートに
していれば (`tools/qemu/run-qemu.sh` では `serial.log`)、そこに出ます。`STARTUP.CMD` の行の後ろに
引数を付けます (`install.sh` の引数でも指定できます)。

```
kerext	qemukbpd	!28	log
```

| 引数 | 出力する内容 |
|------|-------------|
| `log` | 起動情報 (引数、vmmouse の有無、割り込み / ポーリング)、キーの押下・解放、ボタンとホイール、kbpd からのコマンド、異常 (バッファが空かず捨てた、割り込みなしで見つかったデータ、vmmouse のエラー) |
| `log2` | `log` に加えて、ポインタの移動と、約 1 分ごとの割り込み回数 |
| `log3` | `log2` に加えて、i8042 から読んだ全バイト (キーボード・PS/2 マウス) |

各行の先頭は `qemukbpd 秒.ミリ秒:` です。出力例:

```
qemukbpd 27200.070: kbpd mbx 1, vmmouse absolute, interrupt (IRQ1/IRQ12)
qemukbpd 27200.070: command 0x03000001
qemukbpd 27220.300: pd 2562,8372 btn 0x20 wheel 0
qemukbpd 27220.450: pd 2562,8372 btn 0x00 wheel 0
qemukbpd 27390.870: irq kbd 6 aux 85
```

`pd` の座標は vmmouse の値 (0〜65535)、`btn` は左 0x20 / 右 0x10 / 中 0x08 です。
`... found without interrupt` が出る場合は、割り込みが届いていません (`poll` を試してください)。

## エラーコード

起動失敗時は `ER_NOSPT (0xFFFD0000)` の下位 16bit に原因が入ります。

| 値 | 原因 |
|---|---|
| 0x0001 / 0x0002 | kbpd のオープン / メイルボックス ID 取得失敗 |
| 0x0003 | i8042 が応答しない |
| 0x0004 | イベントフラグの生成失敗 |
| 0x000A / 0x000B | タスクの生成 / 起動失敗 |

vmmouse が使えないとき (QEMU の vmport が無効など) はキーボードだけで動作します。

## ライセンス

GPL version 2 またはそれ以降 ([driver/COPYING](../COPYING))。
