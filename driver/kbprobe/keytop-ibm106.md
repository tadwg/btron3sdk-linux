# lowkbpd が kbpd に送るキー (IBM 106 日本語キーボード, キーボード ID 0x41)

QEMU 9.0 上の超漢字V で、ダミーの kbpd (`driver/kbpdsniff`) を使って、lowkbpd が `INP_KEY` で送るメッセージを
そのまま記録した結果です。キーは QMP の `input-send-event` で 1 つずつ送りました。

## 規則

- キーボード ID は 0x41 (`KID_IBM_JP`)。lowkbpd は起動時に `INP_FLG` (kb=1, kbid=0x41) でイベントフラグを登録する
- **キートップコード = scan code set 1 の値** (E0 / E1 の付かないキー)
- E0 / E1 付きのキーは 0x80 以降の独自の番号 (下表)
- **NumLock** は lowkbpd 内で処理され、kbpd には送られない。押すたびにキーボードの LED (NumLock) を切り替え、
  テンキーの数字と小数点のコードが変わる。起動直後は NumLock OFF
  - NumLock ON: scan code そのまま (7 = 0x47 … 0 = 0x52, . = 0x53)
  - NumLock OFF: scan code + 0x48 (7 = 0x8F … 0 = 0x9A, . = 0x9B)
- テンキーのキー (数字、小数点、-、+) は `KeyInStat` の予約領域 `rsv2` の最上位ビット (bit 23) が 1 になる
  (テンキー *、/、Enter は 0)
- **Menu** (アプリケーションキー) は送られない
- PrintScreen と SysRq はどちらも 0x54
- キーリピートは lowkbpd からは送られない (キーを押し続けても押下 1 回・解放 1 回)。
  ただし QEMU は QMP の入力ではリピートを発生させないため、実キーボードのリピートの扱いは未確認

## E0 / E1 付きのキー

| QEMU qcode | キー | scan code | キートップ |
|---|---|---|---|
| `alt_r` | 右Alt | E0 38 | 0x80 |
| `ctrl_r` | 右Ctrl | E0 1D | 0x81 |
| `pause` | Pause | E1 1D 45 | 0x82 |
| `insert` | Insert | E0 52 | 0x83 |
| `delete` | Delete | E0 53 | 0x84 |
| `home` | Home | E0 47 | 0x85 |
| `end` | End | E0 4F | 0x86 |
| `pgup` | PageUp | E0 49 | 0x87 |
| `pgdn` | PageDown | E0 51 | 0x88 |
| `up` | ↑ | E0 48 | 0x89 |
| `down` | ↓ | E0 50 | 0x8A |
| `left` | ← | E0 4B | 0x8B |
| `right` | → | E0 4D | 0x8C |
| `kp_divide` | テンキー / | E0 35 | 0x8D |
| `kp_enter` | テンキー Enter | E0 1C | 0x8E |
| `meta_l` | 左Windows | E0 5B | 0x9C |
| `meta_r` | 右Windows | E0 5C | 0x9D |

## テンキー

| QEMU qcode | キー | NumLock ON | NumLock OFF |
|---|---|---|---|
| `kp_7` | 7 | 0x47 | 0x8F |
| `kp_8` | 8 | 0x48 | 0x90 |
| `kp_9` | 9 | 0x49 | 0x91 |
| `kp_4` | 4 | 0x4B | 0x93 |
| `kp_5` | 5 | 0x4C | 0x94 |
| `kp_6` | 6 | 0x4D | 0x95 |
| `kp_1` | 1 | 0x4F | 0x97 |
| `kp_2` | 2 | 0x50 | 0x98 |
| `kp_3` | 3 | 0x51 | 0x99 |
| `kp_0` | 0 | 0x52 | 0x9A |
| `kp_decimal` | . | 0x53 | 0x9B |
| `kp_subtract` | - | 0x4A | 0x4A |
| `kp_add` | + | 0x4E | 0x4E |

テンキー * は 0x37、/ は 0x8D、Enter は 0x8E (NumLock によらない)。

## その他のキー (scan code set 1 のまま)

| QEMU qcode | キー | キートップ |
|---|---|---|
| `esc` | Esc | 0x01 |
| `f1` | F1 | 0x3B |
| `f2` | F2 | 0x3C |
| `f3` | F3 | 0x3D |
| `f4` | F4 | 0x3E |
| `f5` | F5 | 0x3F |
| `f6` | F6 | 0x40 |
| `f7` | F7 | 0x41 |
| `f8` | F8 | 0x42 |
| `f9` | F9 | 0x43 |
| `f10` | F10 | 0x44 |
| `f11` | F11 | 0x57 |
| `f12` | F12 | 0x58 |
| `grave_accent` | 半角/全角 | 0x29 |
| `1〜0` | 1〜0 | 0x02〜0B |
| `minus` | - | 0x0C |
| `equal` | ^ | 0x0D |
| `yen` | ¥ | 0x7D |
| `backspace` | BackSpace | 0x0E |
| `tab` | Tab | 0x0F |
| `q〜p` | Q〜P | 0x10〜19 |
| `bracket_left` | @ | 0x1A |
| `bracket_right` | [ | 0x1B |
| `ret` | Enter | 0x1C |
| `caps_lock` | CapsLock | 0x3A |
| `a〜l` | A〜L | 0x1E〜26 |
| `semicolon` | ; | 0x27 |
| `apostrophe` | : | 0x28 |
| `backslash` | ] | 0x2B |
| `shift` | 左Shift | 0x2A |
| `z〜m` | Z〜M | 0x2C〜32 |
| `comma` | , | 0x33 |
| `dot` | . | 0x34 |
| `slash` | / | 0x35 |
| `ro` | \\ (ろ) | 0x73 |
| `shift_r` | 右Shift | 0x36 |
| `ctrl` | 左Ctrl | 0x1D |
| `alt` | 左Alt | 0x38 |
| `muhenkan` | 無変換 | 0x7B |
| `spc` | Space | 0x39 |
| `henkan` | 変換 | 0x79 |
| `katakanahiragana` | カタカナ/ひらがな | 0x70 |
| `hiragana` | ひらがな | 0x77 |
| `scroll_lock` | ScrollLock | 0x46 |
| `print / sysrq` | PrintScreen / SysRq | 0x54 |
| `kp_multiply` | テンキー * | 0x37 |
| `less` | < (102 キー) | 0x56 |

## kbpd のキーマップ (`DN_KEYMAP`) について

キーマップはキートップコードのビット列で、**各バイトの最上位ビットが先** (バイト 0 の bit 7 がキートップ 0x00) です。
`driver/kbprobe` の最初の版はこれを逆に読んでいたため、キートップコードが「scan code XOR 7」に見えていました (修正済み)。
