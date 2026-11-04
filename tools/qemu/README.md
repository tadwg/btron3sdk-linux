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
tools/qemu/btctl click 170 129 -2      # 画面座標 (170,129) をダブルクリック (qemukbpd が必要)
tools/qemu/btctl mice                  # マウスの状態 ("vmmouse (absolute)" なら絶対座標)
tools/qemu/run-qemu.sh stop            # CLI で sync してから停止
```

`disk.img` と同じディレクトリに `serial.log` (起動ログと CLI の出力)、`serial.sock`、`qmp.sock` ができます。

## 注意点

QEMU 9.0 (KVM なし) で、超漢字V の体験版イメージ (VMware 用の 40GB ディスク) を動かして分かったことです。

### イメージ

- QEMU はイメージに直接書き込みます。元のイメージは残しておき、コピーを使ってください。
  試すだけなら `QEMU_SNAPSHOT=1` (QEMU の `-snapshot`) で、変更を書き込まずに起動できます
- raw / VMDK / VDI のどれでも起動できます (拡張子で判別)
- 起動中のイメージを bfsk で書き換えないでください。超漢字V 側のキャッシュと食い違います。
  bfsk で書き換えるときは、`run-qemu.sh stop` (CLI で `sync` してから停止) の後にしてください。
  停止後に `bfsk check` で整合性を確認できます
- QEMU の停止は電源断と同じです。必ず `sync` してから止めてください (`run-qemu.sh stop` は自動で行います)

### 起動設定 (DEVCONF / STARTUP.CMD)

- シリアルの CLI を使うには `DEVCONF` の `DEBUGMODE` を 1 にします。起動ログもシリアルに出ます
  (`serial.log`)
- VMware 用の設定は QEMU では不要です。`STARTUP.CMD` の `kerext vmwdrv` は読み込まれますが、
  vmmouse を相対座標モードで使うだけで、絶対座標にはなりません。絶対座標のマウスには
  `driver/qemumouse` を使い、`vmwdrv` はコメントアウトします (`driver/qemumouse/install.sh` が行います)。
  `DEVCONF` の `VMW...` の行は残っていても問題ありません
- `VIDEOMODE 9` (1152×864) は QEMU の標準 VGA では使えず、1024×768 で表示されました。
  `VIDEOMODE 1` (640×480) などは設定どおりになります
- `DEVCONF` のレコードのサブタイプ (0x4500) は読み込みに影響しません (0 でも読まれることを確認)

### ハードウェア

- 体験版はネットワークアダプタが必須です。無いと起動後に「ネットワークアダプタが見つかりません。
  システムを終了します。」となります。既定の NIC は AMD PCnet (`QEMU_NIC=pcnet`) です。
  QEMU 既定の e1000 では「ネットワークデバイスでエラーが発生しました」で終了します。
  コンテナは `--network none` で、QEMU の内部ネットワークも外部とは通信しません
- 起動ログの `InitUsbMgr ER_SYS` は USB コントローラが無いためで、動作には影響しません
- 時計は `-rtc base=localtime` にしています。超漢字V はハードウェア時計をローカル時刻として扱うため、
  UTC のままだと作成したファイルの日時がずれます (日本時間なら 9 時間)
- Windows 用の QEMU (`qemu-system-i386`、本家 v11.1.3 のビルド) を [tadwg/qemu のリリース](https://github.com/tadwg/qemu/releases/tag/btron-latest)
  に置いています ([qemu-i386-win64.zip](https://github.com/tadwg/qemu/releases/download/btron-latest/qemu-i386-win64.zip)、
  説明は [btron/README.md](https://github.com/tadwg/qemu/blob/btron/btron/README.md))
  このビルドには、GTK 表示の起動オプション `grab-on-focus` を追加しています。`-display gtk,grab-on-focus=on` を付けると、
  ウィンドウにフォーカスがある間だけキーボードをつかみ (ポインタはつかまない)、Windows の日本語入力にキーを取られません
- Windows のホストで GTK 表示を使い、qemukbpd (絶対座標のマウス) で動かすときは、Ctrl+Alt+G で入力をつかむか
  `-display gtk,grab-on-hover=on` にしてください。つかんでいないと、ホストの日本語入力がオンのときにキーを
  ホストに取られ、ゲストでかな漢字変換ができません (詳細は `driver/qemukbpd/README.md`)
- 体験版は動作確認の期間を過ぎると、起動時に「認証コード入力」のパネルが出て、ほかの操作を受け付けなくなります
  (クリックやダブルクリックが効かないように見えます)。試験のときは `QEMU_RTC=2026-10-01T09:00:00` のように
  時計の初期値を期間内の日時にして起動できます
- KVM が無い環境ではソフトウェアエミュレーションになり、起動に 30〜50 秒ほどかかります。
  メモリは 256MB で足ります

### シリアルの CLI

- `btctl cli` は 1 行ずつ送り、プロンプト (`[/SYS]%`) が返るまで待ちます
- CLI のオプションはパスより前に書きます (例: `apd -d0.1 /SYS/A /SYS/B`。パスの後に書くと使用法が表示されます)
- ファイル名は全角英数で保存されていますが、CLI は半角で表示し、半角で入力できます
- ウィンドウで開いている実身を CLI で変更しても、ウィンドウには反映されません (CLI の仕様)

### キーボードとマウス

- `driver/qemukbpd` (lowkbpd の代わり) を組み込むと、キーボードは i8042 から、マウスは vmmouse から絶対座標で
  入力され、ドラッグも含めて操作できます。`driver/qemumouse` は lowkbpd と一緒に使うため、QEMU が vmmouse の
  イベントごとに作るダミーの PS/2 パケットのせいでドラッグができません
- 起動中の lowkbpd を `unlspg` で外すとカーネルが停止します (割り込みハンドラが残るため)。
  lowkbpd は `STARTUP.CMD` で読み込まないようにしてください
- `qemukbpd` (または `qemumouse`) を組み込むと `btctl mice` が `vmmouse (absolute)` になり、`btctl move` / `click` で
  画面座標を指定して操作できます (QEMU へは 0〜32767、ドライバへは 0〜65535 の値で渡ります)
- `qemukbpd` / `qemumouse` が無いときは PS/2 マウス (相対座標) だけで、`btctl move` / `click` は
  「Input handler not found for event type abs」というエラーになります

## ライセンス

MIT ライセンス ([LICENSE](../../LICENSE))。
