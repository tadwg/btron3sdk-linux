# kbpdsniff (調査用)

本物の kbpd の代わりにデバイス `kbpd` として登録するダミーのドライバです。
lowkbpd などの実 IO ドライバが入力メイルボックスに送るメッセージ (`INP_KEY` / `INP_PD` / `INP_PD2` / `INP_FLG`)
と、属性の読み書き要求を、そのままシリアルに出力します。
`INP_FLG` でイベントフラグが登録されると、本物の kbpd と同じくスキャン速度・感度・入力モードのコマンドを送ります。

`STARTUP.CMD` の `kerext kbpd` をこれ (デバッグビルド) に置き換えて、`DEBUGMODE 1` のイメージで使います。
本物の kbpd が無いので、画面での操作はできません。

## 確認できたこと (QEMU 9.0 + 超漢字V)

- 起動時の流れ: lowkbpd と qemumouse が `DN_KPINPUT` を読み、lowkbpd は `INP_FLG` (kb=1, kbid=0x41) で
  イベントフラグを登録する。kbpd からのコマンド (スキャン速度・感度・入力モード) を受け付ける。
  その後、システム側のタスクがキーボード ID 0x41 / 0x40 / 0x01 / 0x02 のキーボード定義
  (`DN_KEYDEF` / `DN_KEYDEF2`) を書き込み、`DN_KPSTAT`、`DN_KPMETABUT`、`DN_PDMODE`、`DN_PDSIM`、`DN_KEYMODE`
  の読み書きと、`DN_PDRANGE` の設定を行う
- キー: lowkbpd は scan code set 1 のコードを送る。対応表は `driver/kbprobe/keytop-ibm106.md`
- マウス: qemumouse を使っているとき、vmmouse のイベントごとに lowkbpd から PS/2 由来の
  `INP_PD` (abs=0, x=+3, y=0, ボタンすべて OFF, onebut=1) が qemumouse の `INP_PD` (abs=1) の直前に届く。
  ボタンを押したまま移動すると「押下」と「解放」が交互に届くため、ドラッグができない
