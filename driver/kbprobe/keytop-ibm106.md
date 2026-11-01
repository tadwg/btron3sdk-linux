# lowkbpd のキートップコード (IBM 106 日本語キーボード, キーボード ID 0x41)

QEMU 9.0 上の超漢字V で、`kbprobe` を使って調べた対応表です。QEMU から QMP の `input-send-event` でキーを 1 つずつ送り、
kbpd のキーマップ (`DN_KEYMAP`) で ON になったビット (キートップコード) を記録しました。
kbpd へは `INP_KEY` でキーボード ID 0x41 として送られています (`DN_KEYID` = 0x41)。

## 規則

- 拡張キー (E0 付き) 以外は **キートップコード = scan code set 1 の値 XOR 7** (下位 3 ビットを反転) になっている
- 拡張キーは 0x80 以降の独自の番号 (下の表のとおり)
- PrintScreen は SysRq と同じ 0x53
- Pause、Menu (アプリケーションキー)、NumLock はキーマップに現れない (lowkbpd 内で処理されている可能性がある)
- QEMU の `katakanahiragana` (106 キーボードのカタカナ/ひらがなキー) はスキャンコード 0x70 でキートップ 0x77、
  `hiragana` はスキャンコード 0x77 でキートップ 0x70 (どちらも XOR 7 の規則どおり)

## 対応表

| QEMU qcode | キー | set 1 | キートップ |
|---|---|---|---|
| `esc` | Esc | 01 | 0x06 |
| `f1` | F1 | 3B | 0x3C |
| `f2` | F2 | 3C | 0x3B |
| `f3` | F3 | 3D | 0x3A |
| `f4` | F4 | 3E | 0x39 |
| `f5` | F5 | 3F | 0x38 |
| `f6` | F6 | 40 | 0x47 |
| `f7` | F7 | 41 | 0x46 |
| `f8` | F8 | 42 | 0x45 |
| `f9` | F9 | 43 | 0x44 |
| `f10` | F10 | 44 | 0x43 |
| `f11` | F11 | 57 | 0x50 |
| `f12` | F12 | 58 | 0x5F |
| `print` | PrintScreen | E0 37 | 0x53 |
| `sysrq` | SysRq | 54 | 0x53 |
| `scroll_lock` | ScrollLock | 46 | 0x41 |
| `grave_accent` | 半角/全角 | 29 | 0x2E |
| `1` | 1 | 02 | 0x05 |
| `2` | 2 | 03 | 0x04 |
| `3` | 3 | 04 | 0x03 |
| `4` | 4 | 05 | 0x02 |
| `5` | 5 | 06 | 0x01 |
| `6` | 6 | 07 | 0x00 |
| `7` | 7 | 08 | 0x0F |
| `8` | 8 | 09 | 0x0E |
| `9` | 9 | 0A | 0x0D |
| `0` | 0 | 0B | 0x0C |
| `minus` | - | 0C | 0x0B |
| `equal` | ^ | 0D | 0x0A |
| `yen` | ¥ | 7D | 0x7A |
| `backspace` | BackSpace | 0E | 0x09 |
| `tab` | Tab | 0F | 0x08 |
| `q` | Q | 10 | 0x17 |
| `w` | W | 11 | 0x16 |
| `e` | E | 12 | 0x15 |
| `r` | R | 13 | 0x14 |
| `t` | T | 14 | 0x13 |
| `y` | Y | 15 | 0x12 |
| `u` | U | 16 | 0x11 |
| `i` | I | 17 | 0x10 |
| `o` | O | 18 | 0x1F |
| `p` | P | 19 | 0x1E |
| `bracket_left` | @ | 1A | 0x1D |
| `bracket_right` | [ | 1B | 0x1C |
| `ret` | Enter | 1C | 0x1B |
| `caps_lock` | CapsLock | 3A | 0x3D |
| `a` | A | 1E | 0x19 |
| `s` | S | 1F | 0x18 |
| `d` | D | 20 | 0x27 |
| `f` | F | 21 | 0x26 |
| `g` | G | 22 | 0x25 |
| `h` | H | 23 | 0x24 |
| `j` | J | 24 | 0x23 |
| `k` | K | 25 | 0x22 |
| `l` | L | 26 | 0x21 |
| `semicolon` | ; | 27 | 0x20 |
| `apostrophe` | : | 28 | 0x2F |
| `backslash` | ] | 2B | 0x2C |
| `shift` | 左Shift | 2A | 0x2D |
| `z` | Z | 2C | 0x2B |
| `x` | X | 2D | 0x2A |
| `c` | C | 2E | 0x29 |
| `v` | V | 2F | 0x28 |
| `b` | B | 30 | 0x37 |
| `n` | N | 31 | 0x36 |
| `m` | M | 32 | 0x35 |
| `comma` | , | 33 | 0x34 |
| `dot` | . | 34 | 0x33 |
| `slash` | / | 35 | 0x32 |
| `ro` | \ (ろ) | 73 | 0x74 |
| `shift_r` | 右Shift | 36 | 0x31 |
| `less` | < (102 キー) | 56 | 0x51 |
| `ctrl` | 左Ctrl | 1D | 0x1A |
| `meta_l` | 左Windows | E0 5B | 0x9B |
| `alt` | 左Alt | 38 | 0x3F |
| `muhenkan` | 無変換 | 7B | 0x7C |
| `spc` | Space | 39 | 0x3E |
| `henkan` | 変換 | 79 | 0x7E |
| `hiragana` | ひらがな (hiragana) | 77 | 0x70 |
| `katakanahiragana` | カタカナ/ひらがな (katakanahiragana) | 70 | 0x77 |
| `alt_r` | 右Alt | E0 38 | 0x87 |
| `meta_r` | 右Windows | E0 5C | 0x9A |
| `ctrl_r` | 右Ctrl | E0 1D | 0x86 |
| `insert` | Insert | E0 52 | 0x84 |
| `delete` | Delete | E0 53 | 0x83 |
| `home` | Home | E0 47 | 0x82 |
| `end` | End | E0 4F | 0x81 |
| `pgup` | PageUp | E0 49 | 0x80 |
| `pgdn` | PageDown | E0 51 | 0x8F |
| `up` | ↑ | E0 48 | 0x8E |
| `left` | ← | E0 4B | 0x8C |
| `down` | ↓ | E0 50 | 0x8D |
| `right` | → | E0 4D | 0x8B |
| `kp_divide` | テンキー / | E0 35 | 0x8A |
| `kp_multiply` | テンキー * | 37 | 0x30 |
| `kp_subtract` | テンキー - | 4A | 0x4D |
| `kp_add` | テンキー + | 4E | 0x49 |
| `kp_enter` | テンキー Enter | E0 1C | 0x89 |
| `kp_decimal` | テンキー . | 53 | 0x54 |
| `kp_0` | テンキー 0 | 52 | 0x55 |
| `kp_1` | テンキー 1 | 4F | 0x48 |
| `kp_2` | テンキー 2 | 50 | 0x57 |
| `kp_3` | テンキー 3 | 51 | 0x56 |
| `kp_4` | テンキー 4 | 4B | 0x4C |
| `kp_5` | テンキー 5 | 4C | 0x4B |
| `kp_6` | テンキー 6 | 4D | 0x4A |
| `kp_7` | テンキー 7 | 47 | 0x40 |
| `kp_8` | テンキー 8 | 48 | 0x4F |
| `kp_9` | テンキー 9 | 49 | 0x4E |
