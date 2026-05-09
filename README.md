# BTRON3 (超漢字V) Cross Development Environment on Linux

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

## ファイル構成

```
Dockerfile          コンテナビルド定義
run-btron3sdk.sh    コンテナ起動スクリプト
```

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
- [超漢字クロス開発環境 (note)](https://note.com/madeli/n/ne89e0e63e7a6)
- [超漢字 PCI デバイス用デバイスドライバ説明書](http://www.chokanji.com/developer/info/pcidrv.html)
- [VirtualBox Guest Additions Mouse Driver for BTRON3](https://github.com/tadwg/virtualbox-additions)

## ライセンス

このリポジトリの Dockerfile および起動スクリプトは MIT ライセンスです。

ダウンロードされる brightv アーカイブは Personal Media Corporation の著作物であり、
使用条件については [超漢字開発者向けページ](https://www.chokanji.com/developer/) をご確認ください。
