# BTRON3 (超漢字V) Cross Development Environment on Linux

> このリポジトリのコードは [Claude Sonnet 4.6](https://www.anthropic.com/claude) (Anthropic) の支援を受けて書かれました。

超漢字V 向けのクロスコンパイル環境を Linux 上の Podman (または Docker) コンテナで構築します。

`docker build` または `podman build` 一発で以下まで自動的に行います:

- 超漢字クロス開発環境アーカイブ (brightv) のダウンロード
- 32bit ELF バイナリ実行に必要な glibc.i686 等のインストール
- EUC-JP コメント対応 cpp ラッパーのパッチ適用
- `driver/etc/makerules` の余分な `endif` 削除パッチ
- アプリケーションサンプル全4本のビルド (`sample1`, `sample2`, `tagsamp1`, `tagsamp2`)
- デバイスドライバサンプル (`rsdrv`) のビルド

## 必要なもの

- Podman または Docker が動作する Linux 環境
- インターネット接続（ビルド時に chokanji.com からアーカイブを取得）

## ビルド

```bash
podman build -t btron3sdk:latest .
# または
docker build -t btron3sdk:latest .
```

ビルドには数分かかります（アーカイブのダウンロード含む）。

## 使い方

### 開発シェルに入る

```bash
podman run --rm -it -v $PWD:/workspace:z btron3sdk:latest
```

コンテナ内に入ると環境変数が自動設定されます:

```bash
echo $BD        # /usr/local/brightv
echo $GNUi386   # /usr/local/brightv/tool/gnu/i386-unknown-gnu
```

### プロジェクトをビルドする

```bash
# コンテナ内で
cd /workspace/mydriver/pcat
make BD=$BD GNUs=$GNUs GNU_BD=$GNU_BD GNUi386=$GNUi386
```

### 起動スクリプトを使う

```bash
chmod +x run-btron3sdk.sh
./run-btron3sdk.sh            # 開発シェル
./run-btron3sdk.sh gterm      # gterm デバッグ接続（TCP 9999）
```

## ディレクトリ構成

| パス | 内容 |
|------|------|
| `Dockerfile` | クロス開発環境 (btron3sdk) のコンテナ定義 |
| `run-btron3sdk.sh` | 開発環境コンテナの起動スクリプト |
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
podman build -t btron3sdk:latest .                                   # 開発環境
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
このリポジトリのツールとドライバも、その方法で作られています。

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

## ビルド済みサンプル

コンテナ内の以下のパスにビルド済みバイナリがあります:

| パス | 内容 |
|------|------|
| `/usr/local/brightv/appl/sample1/pcat/sample.bz` | アプリサンプル1 |
| `/usr/local/brightv/appl/sample2/pcat/sched.bz` | アプリサンプル2 |
| `/usr/local/brightv/appl/tagsamp1/pcat/tagsamp1.bz` | TADタグサンプル1 |
| `/usr/local/brightv/appl/tagsamp2/pcat/tagsamp2.bz` | TADタグサンプル2 |
| `/usr/local/brightv/driver/sample/pcat/rsdrv` | RSドライバサンプル |

## 参考資料

- [超漢字 開発者向けページ](https://www.chokanji.com/developer/)
- [超漢字 PCI デバイス用デバイスドライバ説明書](http://www.chokanji.com/developer/info/pcidrv.html)
- [VirtualBox Guest Additions Mouse Driver for BTRON3](https://github.com/tadwg/virtualbox-additions)

## ライセンス

このリポジトリの Dockerfile および起動スクリプトは MIT ライセンスです。

ダウンロードされる brightv アーカイブは Personal Media Corporation の著作物であり、
使用条件については [超漢字開発者向けページ](https://www.chokanji.com/developer/) をご確認ください。
