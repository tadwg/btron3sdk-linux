# BTRON3 (超漢字V) Cross Development Environment on Linux

> このリポジトリのツールとドライバは、[Claude](https://www.anthropic.com/claude) からこれらのツールを呼び出して開発しました ([Claude (AI エージェント) からの開発](#claude-ai-エージェント-からの開発))。

超漢字V (BTRON3) のプログラムを Linux 上で開発するための環境とツールです。

- [`sdk/`](sdk/README.md): 超漢字V のクロス開発環境 (brightv) を Podman / Docker コンテナで構築する
- [`tools/bfsk/`](tools/bfsk/README.md): 超漢字V のディスクイメージを Linux から読み書きする
- [`tools/qemu/`](tools/qemu/README.md): QEMU で超漢字V を起動し、コマンドラインから操作する
- [`driver/`](#ディレクトリ構成): QEMU 用のドライバなど

開発環境のコンテナは次のコマンドで作れます (詳細は [sdk/README.md](sdk/README.md))。

```bash
podman build -t btron3sdk:latest sdk
```

## ディレクトリ構成

| パス | 内容 |
|------|------|
| [`sdk/`](sdk/README.md) | クロス開発環境 (btron3sdk) のコンテナ定義 `Dockerfile` と起動スクリプト `run-btron3sdk.sh` |
| [`tools/bfsk/`](tools/bfsk/README.md) | BTRON ディスクイメージ (raw / VMDK / VDI) を Linux から読み書きする Python ツール。超漢字の CLI に近いコマンド (`ls` `tp` `recv` `apd` `rm` `check` など) で、ファイルの取り込み・取り出しや `STARTUP.CMD` の編集ができる |
| [`tools/qemu/`](tools/qemu/README.md) | QEMU で超漢字V を起動するコンテナ (`run-qemu.sh`) と、操作ツール `btctl` (シリアルの CLI でコマンド実行、画面のスクリーンショット、マウス操作、キー入力) |
| [`driver/qemukbpd/`](driver/qemukbpd/README.md) | QEMU 用 キーボード + 絶対座標マウスドライバ (lowkbpd の代わり。ドラッグ可) |
| [`driver/qemumouse/`](driver/qemumouse/README.md) | QEMU 用 絶対座標マウスドライバ (lowkbpd と併用。ドラッグ不可のため qemukbpd を推奨) |
| [`driver/kbprobe/`](driver/kbprobe/README.md) | 調査用: kbpd のキーマップを出力 (lowkbpd のキートップコード表つき) |
| [`driver/kbpdsniff/`](driver/kbpdsniff/README.md) | 調査用: ダミーの kbpd。実 IO ドライバ (lowkbpd など) が送るメッセージをシリアルに出力 |
| `.github/workflows/` | `driver/*/src` の変更を検知してドライバをビルドし、`driver/*/bin/` をコミットする GitHub Actions |

各ドライバのディレクトリは `src/` (ソース)、`build.sh` (開発環境コンテナでビルド)、`install.sh` (bfsk でイメージに組み込み)、
`bin/` (ビルド済みバイナリ) という同じ構成です。

## ビルドからディスクイメージへの書き込み・実行まで

bfsk と QEMU を使うと、超漢字V のプログラムを「ビルド → イメージへの書き込み → 起動 → 確認」まで
コマンドラインだけで行えます (例: QEMU 用キーボード・マウスドライバ)。

```bash
podman build -t btron3sdk:latest sdk                                   # 開発環境
podman build -t btron-qemu:latest -f tools/qemu/Containerfile tools/qemu
podman run --rm -v $PWD:/workspace:Z btron3sdk:latest bash /workspace/driver/qemukbpd/build.sh
driver/qemukbpd/install.sh disk.img debug                            # bfsk でイメージに組み込み
tools/qemu/run-qemu.sh disk.img                                      # 起動
tools/qemu/btctl mice                                                # 絶対座標モードか確認
tools/qemu/run-qemu.sh stop
```

QEMU で動かすときの注意点 (ネットワークアダプタ、DEBUGMODE、時計、sync など) は
[tools/qemu/README.md](tools/qemu/README.md) にまとめています。

`driver/*/src` の変更を main に push すると、GitHub Actions (`.github/workflows/build-drivers.yml`) が
同じ開発環境でビルドし、`driver/*/bin/` を更新してコミットします。

## Claude (AI エージェント) からの開発

ビルド、ディスクイメージへの取り込み、QEMU での実行、結果の確認がすべてコマンドラインで完結するため、
Claude Code のような AI エージェントがソースの変更から動作確認までを繰り返して開発できます。

| 作業 | コマンド |
|------|----------|
| ビルド | `podman run --rm -v $PWD:/workspace:Z btron3sdk:latest bash /workspace/driver/<名前>/build.sh` |
| イメージへの取り込み | `driver/<名前>/install.sh disk.img debug` / `python3 tools/bfsk/bfsk.py disk.img recv ...` |
| 起動・停止 | `tools/qemu/run-qemu.sh disk.img` / `tools/qemu/run-qemu.sh stop` (`QEMU_SNAPSHOT=1 tools/qemu/run-qemu.sh ...` でイメージを書き換えない) |
| ログの取得 | `disk.img` と同じディレクトリの `serial.log` (起動ログ、DEBUGMODE のデバッグ出力、CLI の出力) |
| 超漢字の CLI | `tools/qemu/btctl cli 'ref sys'` など |
| 画面の確認 | `tools/qemu/btctl screen shot.png` (エージェントは PNG を読んで画面を確認できる) |
| マウス操作 | `tools/qemu/btctl move X Y` / `click X Y` / `wheel up` (キー入力やドラッグは QMP の `input-send-event` で送れる) |
| 停止後のファイル確認 | `python3 tools/bfsk/bfsk.py disk.img tp /SYS/STARTUP.CMD` / `check` |

進め方の例:

1. ソースを修正し、デバッグ版 (`DEBUGMODE 1`) をビルドする
2. 元のイメージのコピーに `install.sh ... debug` で組み込み、`QEMU_SNAPSHOT=1` で起動する
3. `serial.log`・`btctl cli`・`btctl screen` で動作を確認し、`btctl` や QMP で入力を与えて試験する
4. 問題があれば 1 に戻る。終わったら仮想マシンを止め、作業用イメージを消す

KVM が使えない環境でも動きます (起動に 30〜50 秒ほど)。QEMU はホストに直接入れず、
`tools/qemu/Containerfile` のコンテナで動かすことを前提にしています。

## 参考資料

- [超漢字 開発者向けページ](https://www.chokanji.com/developer/)
- [超漢字 PCI デバイス用デバイスドライバ説明書](http://www.chokanji.com/developer/info/pcidrv.html)
- [VirtualBox Guest Additions Mouse Driver for BTRON3](https://github.com/tadwg/virtualbox-additions)

## ライセンス

`sdk/` の Dockerfile および起動スクリプトは MIT ライセンスです。
ダウンロードされる brightv アーカイブの使用条件は [sdk/README.md](sdk/README.md#ライセンス) を参照してください。
