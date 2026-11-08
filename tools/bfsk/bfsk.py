#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""bfsk - BTRON (超漢字V) ディスクイメージ操作ツール

対応イメージ形式 (内容から自動判別):
  - raw (ディスク全体 / パーティション単体)
  - VMDK monolithicSparse (非圧縮)
  - VDI (dynamic / fixed)

ファイルシステム (実イメージで確認した内容):
  - ディスク全体イメージ (MBR, パーティションタイプ 0x13) またはパーティション単体
  - 全フィールドがリトルエンディアン (仕様書 4章のフロッピー形式はビッグエンディアン)
  - 論理ブロック長はシステムヘッダから取得 (実例: 32768)
  - 準標準形式 (付録A: リトルエンディアン) のみ対応. 標準形式 (ビッグエンディアン) は非対応
  - ブロック n のオフセット = パーティション先頭 + ブート領域長 + (n-1) * ブロック長
    (付録B: 論理ブロック1 はブート領域の直後. ブート領域長は自動検出, 実例は 4096)
  - ディスク形式ID 0x6402 は「拡張形式2」(超漢字V R4 仕様差分, 4GB超対応):
    ファイルヘッダ +176 にレコードインデックス開始位置 (ridxofs) があり、
    フラグメントテーブルは 8 バイト (size, offset, blk) × 可変個

使い方 (詳細は README.md):
  bfsk.py [-p N | -n FSNAME] IMAGE COMMAND ...
    info                         イメージ / ファイルシステムの詳細
    df                           ディスク状態
    ls [-f][-F][-l|-t] [PATH..]  ファイル一覧
    fs [-l] [PATH..]             レコード構成
    stat [PATH..]                ファイルヘッダの詳細
    check [-v]                   整合性チェック (fsck 相当, 読み取りのみ)
  書き込み (ブロック割り当てあり, FileManager 層):
    mkf / rm / empf / ln / cp / rcp / apd / recv / cleanup   (詳細は README.md と -h)
  書き込み (ファイルヘッダのみ変更):
    ren PATH NAME                ファイル名の変更
    chmod [-o#][-g#][-p#][-a#] PATH..   アクセスモード / 属性
    chtime [-l..][-a..][-m..] PATH..    日時 (年.月.日.時.分.秒)
    touch PATH..                 更新日時・アクセス日時を現在に
    tp [-r#][-a|-x|-b][-e] PATH  レコード内容 (既定は生データを標準出力へ)
  PATH に "/SYS:ファイルID[/...]" と書くとファイル ID で指定できる (例: /SYS:83).
"""
import argparse
import datetime
import fnmatch
import os
import re
import struct
import sys
import time
import unicodedata

# ブート領域の大きさ (= パーティション先頭からシステムブロックまで) の候補.
# 付録B: 準標準 2HD は 2048, 実 HD イメージは 4096. 論理ブロック1 はブート領域の直後.
BOOT_SIZES = (0x400, 0x800, 0x1000, 0x2000, 0x4000, 0x8000)
DISK_FMT_IDS = (0x6400, 0x6401, 0x6402)
FH_START = 0x54726F6E
FH_END = 0x82DDE96B
TRON_DISK_IDS = (0x42FE, 0x52FE)
EPOCH_1985 = 473385600       # 1985-01-01 00:00:00 GMT の UNIX 時刻


def tron_str(b):
    """TRONコード(2バイトLE)の文字列をUnicodeに変換 (JIS X 0208 範囲のみ)."""
    out = []
    for i in range(0, len(b) - 1, 2):
        c = struct.unpack_from('<H', b, i)[0]
        if c == 0:
            break
        if c < 0x80:
            out.append(chr(c))
            continue
        try:
            out.append(bytes([(c >> 8) | 0x80, (c & 0xFF) | 0x80]).decode('euc-jp'))
        except UnicodeDecodeError:
            out.append('\\u{%04x}' % c)
    return ''.join(out)


def fmt_time(t):
    """BTRON 日時 (1985-01-01 GMT からの秒) をローカル時刻で表示 (TZ 環境変数に従う)."""
    if t in (0, 0xFFFFFFFF):
        return '-' if t == 0 else 'none'
    return datetime.datetime.fromtimestamp(t + EPOCH_1985).strftime('%Y-%m-%d %H:%M:%S')


def now_btron():
    return int(time.time()) - EPOCH_1985


def parse_btime(spec):
    """CLI の日時指定 "年.月.日.時.分.秒" (年は西暦下2桁または4桁, ローカル時刻) → BTRON 日時.
    "-1" は無効値 (保存期限なし)."""
    if spec.strip() == '-1':
        return 0xFFFFFFFF
    parts = spec.split('.')
    if len(parts) != 6 or not all(x.isdigit() for x in parts):
        raise FormatError('日時は 年.月.日.時.分.秒 で指定してください: %s' % spec)
    y, mo, d, h, mi, se = (int(x) for x in parts)
    if y < 100:
        y += 1900 if y >= 85 else 2000
    try:
        t = int(datetime.datetime(y, mo, d, h, mi, se).timestamp()) - EPOCH_1985
    except (ValueError, OverflowError) as e:
        raise FormatError('日時が不正です: %s (%s)' % (spec, e))
    if not 0 <= t < 0xFFFFFFFF:
        raise FormatError('日時が範囲外です: %s' % spec)
    return t


# 半角 ASCII → TRON コード (JIS X 0208) にない文字の置き換え
_TO_FULL = {' ': '\u3000', '-': '\u2212', '~': '\u301c', '\\': '\uff3c', '"': '\u201d', "'": '\u2019', '`': '\u2018'}


def tron_encode(name, maxchars=20):
    """文字列を TRON コード (2 バイト LE 固定長) の 40 バイトに変換.
    半角英数記号は全角にする (実イメージのファイル名はすべて全角)."""
    codes = []
    for ch in name:
        if ch in _TO_FULL:
            ch = _TO_FULL[ch]
        elif 0x21 <= ord(ch) <= 0x7E:
            ch = chr(ord(ch) + 0xFEE0)
        try:
            b = ch.encode('euc-jp')
        except UnicodeEncodeError:
            raise FormatError('TRON コードに変換できない文字があります: %r' % ch)
        if len(b) != 2 or b[0] < 0xA1:
            raise FormatError('TRON コード (JIS X 0208) にない文字です: %r' % ch)
        codes.append(((b[0] & 0x7F) << 8) | (b[1] & 0x7F))
    if not codes:
        raise FormatError('名前が空です')
    if len(codes) > maxchars:
        raise FormatError('名前は %d 文字までです (%d 文字)' % (maxchars, len(codes)))
    return struct.pack('<%dH' % len(codes), *codes).ljust(maxchars * 2, b'\0')


def short_name(name40):
    """ファイル短縮名 (4.2.4): 2 文字ずつ 32 ビットにまとめ、XOR と右 1 ビット回転."""
    ch = struct.unpack('<20H', name40)
    s = 0
    for k in range(10):
        s ^= (ch[2 * k] << 16) | ch[2 * k + 1]
        s = ((s >> 1) | (s << 31)) & 0xFFFFFFFF
    return s


def disp_name(name):
    """表示用の名前: CLI と同じく全角英数記号を半角にする (かな漢字はそのまま)."""
    out = []
    for ch in name:
        o = ord(ch)
        if 0xFF01 <= o <= 0xFF5E:
            ch = chr(o - 0xFEE0)
        elif ch == '\u3000':
            ch = ' '
        elif ch in '\u2212\u2015\u2010':
            ch = '-'
        elif ch == '\u301c':
            ch = '~'
        out.append(ch)
    return ''.join(out)


def dwidth(text):
    """表示幅 (EUC-JP のバイト数 = 全角 2, 半角 1)."""
    return len(text.encode('euc-jp', 'replace'))


def cli_time(t):
    """CLI 形式の日時 "YYMMDD hh:mm:ss" (ローカル時刻)."""
    if t in (0, 0xFFFFFFFF):
        return '-'.ljust(15)
    return datetime.datetime.fromtimestamp(t + EPOCH_1985).strftime('%y%m%d %H:%M:%S')


def norm_name(name):
    """名前比較用の正規化: CLI と同様に全角/半角を区別しない."""
    return unicodedata.normalize('NFKC', name).translate(_NAME_FOLD)


# NFKC で半角にならない記号 (TRON コード→EUC-JP 変換で出るもの) を補う
_NAME_FOLD = str.maketrans({'\u2212': '-', '\u301c': '~', '\uff5e': '~', '\u2015': '-', '\u2010': '-'})


class FormatError(Exception):
    pass


# ---------------------------------------------------------------------------
# イメージ形式層: どの形式も read(offset, n) で仮想ディスクのバイト列を返す
# ---------------------------------------------------------------------------
class RawImage:
    kind = 'raw'

    def __init__(self, f):
        self.f = f
        f.seek(0, 2)
        self.size = f.tell()

    def read(self, off, n):
        self.f.seek(off)
        d = self.f.read(n)
        return d + b'\0' * (n - len(d))      # 切り詰められたイメージ対策

    def write(self, off, data):
        if off + len(data) > self.size:
            raise FormatError('イメージの範囲外への書き込みです (0x%x)' % off)
        self.f.seek(off)
        self.f.write(data)


class _BlockMappedImage:
    """固定長ブロック単位でファイル内位置へ対応付ける形式の共通処理."""

    def read(self, off, n):
        out = bytearray()
        while n > 0:
            idx, inner = divmod(off, self.bsize)
            k = min(n, self.bsize - inner)
            pos = self._lookup(idx) if off < self.size else None
            if pos is None:
                out += b'\0' * k
            else:
                self.f.seek(pos + inner)
                d = self.f.read(k)
                out += d + b'\0' * (k - len(d))
            off += k
            n -= k
        return bytes(out)

    def write(self, off, data):
        """割り当て済みの領域にだけ上書きする (未割り当て領域への書き込みは未対応)."""
        pos = 0
        while pos < len(data):
            idx, inner = divmod(off + pos, self.bsize)
            k = min(len(data) - pos, self.bsize - inner)
            fpos = self._lookup(idx)
            if fpos is None:
                fpos = self._allocate(idx)
            self.f.seek(fpos + inner)
            self.f.write(data[pos:pos + k])
            pos += k


class VmdkSparseImage(_BlockMappedImage):
    """VMware monolithicSparse (ヘッダ 'KDMV', 非圧縮)."""
    kind = 'vmdk'

    def __init__(self, f):
        self.f = f
        f.seek(0)
        h = f.read(512)
        (_, ver, flags, cap, grain, _, _, ngte, rgd, gd) = struct.unpack_from('<4sIIQQQQIQQ', h, 0)
        comp = struct.unpack_from('<H', h, 77)[0]
        if flags & 0x10000 or comp:
            raise FormatError('圧縮 VMDK (streamOptimized) には未対応です')
        if gd == 0xFFFFFFFFFFFFFFFF:
            raise FormatError('グレインディレクトリがフッタにある VMDK には未対応です')
        if ver not in (1, 2, 3):
            raise FormatError('未対応の VMDK バージョン %d' % ver)
        self.size = cap * 512
        self.bsize = grain * 512
        self.ngte = ngte
        ngt = (cap // grain + ngte - 1) // ngte
        f.seek(gd * 512)
        self.gd = struct.unpack('<%dI' % ngt, f.read(ngt * 4))
        f.seek(rgd * 512)
        self.rgd = struct.unpack('<%dI' % ngt, f.read(ngt * 4)) if rgd else None
        self._gt = {}

    def _lookup(self, g):
        t, j = divmod(g, self.ngte)
        if t >= len(self.gd) or self.gd[t] == 0:
            return None
        gt = self._gt.get(t)
        if gt is None:
            self.f.seek(self.gd[t] * 512)
            gt = self._gt[t] = list(struct.unpack('<%dI' % self.ngte, self.f.read(self.ngte * 4)))
        s = gt[j]
        return None if s <= 1 else s * 512     # 0: 未割当, 1: ゼログレイン

    def _allocate(self, g):
        """グレインをファイル末尾に追加して割り当てる (グレインテーブルと冗長テーブルを更新)."""
        t, j = divmod(g, self.ngte)
        if t >= len(self.gd) or self.gd[t] == 0:
            raise FormatError('VMDK: グレインテーブルが無い領域への書き込みには未対応です')
        self._lookup(g)
        self.f.seek(0, 2)
        sec = (self.f.tell() + 511) // 512
        self.f.seek(sec * 512)
        self.f.write(b'\0' * self.bsize)
        for tab in (self.gd, self.rgd):
            if tab and tab[t]:
                self.f.seek(tab[t] * 512 + j * 4)
                self.f.write(struct.pack('<I', sec))
        self._gt[t][j] = sec
        return sec * 512


class VdiImage(_BlockMappedImage):
    """VirtualBox VDI (dynamic / fixed)."""
    kind = 'vdi'
    SIG = 0xBEDA107F

    def __init__(self, f):
        self.f = f
        f.seek(0)
        h = f.read(512)
        sig, ver = struct.unpack_from('<II', h, 0x40)
        if sig != self.SIG:
            raise FormatError('VDI シグネチャ不正')
        if ver >> 16 != 1:
            raise FormatError('未対応の VDI バージョン %#x' % ver)
        itype = struct.unpack_from('<I', h, 0x4C)[0]
        if itype not in (1, 2):                  # 1: dynamic, 2: fixed (3: undo, 4: diff)
            raise FormatError('差分/undo VDI (type %d) には未対応です' % itype)
        off_bmap, off_data = struct.unpack_from('<II', h, 0x154)
        self.size = struct.unpack_from('<Q', h, 0x170)[0]
        self.bsize = struct.unpack_from('<I', h, 0x178)[0]
        nblk = struct.unpack_from('<I', h, 0x180)[0]
        self.off_data = off_data
        self.off_bmap = off_bmap
        f.seek(off_bmap)
        self.bmap = list(struct.unpack('<%dI' % nblk, f.read(nblk * 4)))
        self.nalloc = struct.unpack_from('<I', h, 0x184)[0]

    def _lookup(self, b):
        if b >= len(self.bmap):
            return None
        v = self.bmap[b]
        if v >= 0xFFFFFFFE:                      # 未割当 / ゼロブロック
            return None
        return self.off_data + v * self.bsize

    def _allocate(self, b):
        """ブロックを末尾に追加して割り当てる (ブロックマップと割り当て数を更新)."""
        if b >= len(self.bmap):
            raise FormatError('VDI: 範囲外のブロックです')
        idx = self.nalloc
        pos = self.off_data + idx * self.bsize
        self.f.seek(pos)
        self.f.write(b'\0' * self.bsize)
        self.bmap[b] = idx
        self.f.seek(self.off_bmap + b * 4)
        self.f.write(struct.pack('<I', idx))
        self.nalloc += 1
        self.f.seek(0x184)
        self.f.write(struct.pack('<I', self.nalloc))
        return pos


def open_image(path, writable=False):
    """ファイル内容から形式を自動判別して開く."""
    f = open(path, 'r+b' if writable else 'rb')
    head = f.read(0x50)
    if head[:4] == b'KDMV':
        return VmdkSparseImage(f)
    if head.startswith(b'# Disk DescriptorFile'):
        raise FormatError('記述子ファイル形式の VMDK (flat/分割) には未対応です')
    if head[:4] == b'COWD':
        raise FormatError('旧形式 VMDK (COWD) には未対応です')
    if len(head) >= 0x44 and struct.unpack_from('<I', head, 0x40)[0] == VdiImage.SIG:
        return VdiImage(f)
    if head[:4] == b'QFI\xfb':
        raise FormatError('qcow2 には未対応です (qemu-img convert -O raw で変換してください)')
    raw = RawImage(f)
    if raw.size >= 512 and raw.read(raw.size - 512, 8) == b'conectix':
        raise FormatError('VHD には未対応です')
    return raw


class Disk:
    def __init__(self, path, part=None, fsname=None, writable=False):
        self.img = open_image(path, writable)
        self.writable = writable
        found = self._find_partitions(part)
        if not found:
            raise FormatError('BTRON ファイルシステムが見つかりません')
        for self.base, self.boot_size in found:
            self._load_header()
            if fsname is None or norm_name(self.fsname) == norm_name(fsname):
                break
        else:
            raise FormatError('ファイルシステム "%s" が見つかりません' % fsname)

    def _load_header(self):
        h = self._read(self.base + self.boot_size, 128)
        (self.disk_id, self.fmt_id, self.ssys, self.sfidt, self.sfnmt,
         self.nbmp) = struct.unpack_from('<6H', h, 0)
        if self.disk_id not in TRON_DISK_IDS:
            raise FormatError('TRON ディスクIDが見つかりません (0x%04x)' % self.disk_id)
        self.blksz, self.nfmax, self.lang, self.acl = struct.unpack_from('<4H', h, 24)
        self.nlb, self.nfree, self.t_upd, self.t_gen = struct.unpack_from('<4I', h, 32)
        self.fsname = tron_str(h[48:88])
        self.devname = tron_str(h[88:128])
        self.fidt_blk = 1 + self.ssys
        self.fnmt_blk = self.fidt_blk + self.sfidt

    # --- 低レベル ---
    def _read(self, off, n):
        return self.img.read(off, n)

    def _probe(self, off):
        """off をパーティション先頭とみなし、ブート領域の大きさを返す (見つからなければ None).
        準標準形式 (リトルエンディアン) のシステムヘッダ: ディスクID, 形式ID, ..., 論理ブロック長."""
        for bs in BOOT_SIZES:
            h = self._read(off + bs, 28)
            did, fid = struct.unpack_from('<2H', h, 0)
            blksz = struct.unpack_from('<H', h, 24)[0]
            if did in TRON_DISK_IDS and fid in DISK_FMT_IDS and blksz and blksz % 512 == 0:
                return bs
            bid, bfid = struct.unpack_from('>2H', h, 0)
            if bid in TRON_DISK_IDS and bfid in DISK_FMT_IDS:
                raise FormatError('TRON 標準形式 (ビッグエンディアン) には未対応です')
        return None

    def _find_partitions(self, part):
        """BTRON ファイルシステムを持つ (パーティション先頭, ブート領域長) の一覧."""
        mbr = self._read(0, 512)
        cands = []
        if mbr[510:512] == b'\x55\xaa':
            for i in range(4):
                e = mbr[0x1BE + 16 * i:0x1CE + 16 * i]
                lba = struct.unpack_from('<I', e, 8)[0]
                if e[4] and lba:
                    cands.append((i + 1, lba * 512))
        if part is not None:
            offs = [off for no, off in cands if no == part]
            if not offs:
                raise FormatError('パーティション %d がありません' % part)
        else:
            offs = [off for _, off in cands] + [0]     # 0: パーティション単体 / フロッピー
        out = []
        for off in offs:
            bs = self._probe(off)
            if bs is not None:
                out.append((off, bs))
                if off == 0:
                    break
        if part is not None and not out:
            raise FormatError('パーティション %d に BTRON ファイルシステムがありません' % part)
        return out

    def write(self, off, data):
        if not self.writable:
            raise FormatError('読み取り専用で開いています')
        self.img.write(off, data)

    def set_short_name(self, fid, value):
        self.write(self.block_offset(self.fnmt_blk) + fid * 4, struct.pack('<I', value))

    def block_offset(self, n):
        if n == 0:
            return self.base
        return self.base + self.boot_size + (n - 1) * self.blksz

    def read_block(self, n):
        return self._read(self.block_offset(n), self.blksz)

    # --- ファイル ---
    def fid_entry(self, fid):
        if not 0 <= fid < self.nfmax:
            raise FormatError('ファイルID %d は範囲外です (0..%d)' % (fid, self.nfmax - 1))
        v = struct.unpack('<I', self._read(self.block_offset(self.fidt_blk) + fid * 4, 4))[0]
        return v & 0xFFFFFF, v >> 24     # (開始ブロック, 参照カウント)

    def open_file(self, fid):
        blk, ref = self.fid_entry(fid)
        if blk == 0:
            raise FormatError('ファイルID %d は未使用です' % fid)
        return BFile(self, fid, blk, ref)


class BFile:
    def __init__(self, disk, fid, blk, ref):
        self.disk, self.fid, self.blk, self.ref = disk, fid, blk, ref
        self._recs = None
        self.hblock = bytearray(disk.read_block(blk))
        h = self.hblock[:192]
        if struct.unpack_from('<I', h, 0)[0] != FH_START or struct.unpack_from('<I', h, 188)[0] != FH_END:
            raise FormatError('ブロック %d にファイルヘッダがありません' % blk)
        ta = struct.unpack_from('<H', h, 4)[0]
        self.ftype, self.attr = ta >> 12, ta & 0xFFF
        self.apptype = struct.unpack_from('<H', h, 6)[0]
        self.nlink, self.level = struct.unpack_from('<2H', h, 68)
        self.size, self.nblk, self.nrec = struct.unpack_from('<3I', h, 72)
        self.t_keep, self.t_acc, self.t_upd, self.t_gen = struct.unpack_from('<4I', h, 92)
        self.name = tron_str(h[108:148])
        self.hdr_fid = struct.unpack_from('<H', h, 186)[0]
        # 拡張形式2 (0x6402) はヘッダ +176 にレコードインデックス開始位置 ridxofs を持つ
        # (超漢字V R4 仕様差分 4.3.3). それ以外は固定 384.
        self.ridxofs = 384
        if disk.fmt_id == 0x6402:
            self.ridxofs = struct.unpack_from('<H', h, 176)[0]
            if not (192 + 16 <= self.ridxofs <= disk.blksz - 16) or self.ridxofs % 16:
                raise FormatError('レコードインデックス開始位置が不正です (%d)' % self.ridxofs)

    @property
    def owner_mode(self):
        return self.attr & 0x7                     # RWE

    @property
    def acc_levels(self):
        return struct.unpack_from('<2H', self.hblock, 64)   # (グループ, 一般)

    def update_header(self, fields):
        """ファイルヘッダのフィールドを書き換えて保存する. fields: {offset: (fmt, value)}."""
        for off, (fmt, val) in fields.items():
            struct.pack_into(fmt, self.hblock, off, *(val if isinstance(val, tuple) else (val,)))
        self.disk.write(self.disk.block_offset(self.blk), bytes(self.hblock[:192]))
        self.__init__(self.disk, self.fid, self.blk, self.ref)   # 読み直し

    def fragments(self):
        """フラグメントテーブル (192 から ridxofs まで) を返す: [(size, block, offset)]."""
        out = []
        if self.disk.fmt_id == 0x6402:      # DfFragment2: UH size, UH offset, UW blk
            for off in range(192, self.ridxofs - 7, 8):
                size, boff, blk = struct.unpack_from('<HHI', self.hblock, off)
                if size == 0:
                    break
                out.append((size, blk, boff))
        else:                                # 6 バイト: size, ブロックアドレス*ブロック長+オフセット
            for off in range(192, 384 - 5, 6):
                size, pos = struct.unpack_from('<HI', self.hblock, off)
                if size == 0:
                    break
                out.append((size, pos // self.disk.blksz, pos % self.disk.blksz))
        return out

    @staticmethod
    def _slots(block, start):
        """インデックス領域を 16 バイト単位で前から列挙 (未使用エントリは呼び出し側で除外)."""
        for off in range(start, len(block) - 15, 16):
            yield block[off:off + 16]

    def _index_entries(self):
        """レコードインデックス (16バイト) を論理順に列挙."""
        if self.level == 0:
            yield from self._slots(self.hblock, self.ridxofs)
            return
        yield from self._walk_indirect(self.hblock, self.ridxofs, self.level)

    def _walk_indirect(self, block, start, level):
        # 間接インデックス (8バイト: 有効レコード数, 論理ブロックアドレス) を前から列挙
        # レベル 1 / 2 は超漢字V で作ったファイルで確認済み (README「インデックスレベル 1 / 2 の検証」)
        for off in range(start, len(block) - 7, 8):
            cnt, addr = struct.unpack_from('<2I', block, off)
            if addr == 0:
                continue
            child = self.disk.read_block(addr)
            if level == 1:
                yield from self._slots(child, 0)
            else:
                yield from self._walk_indirect(child, 0, level - 1)

    def index_blocks(self):
        """インデックスブロック / 間接ブロックの番号一覧 (インデックスレベル > 0 のとき)."""
        out = []

        def walk(block, start, level):
            for off in range(start, len(block) - 7, 8):
                cnt, addr = struct.unpack_from('<2I', block, off)
                if addr == 0:
                    continue
                out.append(addr)
                if level > 1:
                    walk(self.disk.read_block(addr), 0, level - 1)
        if self.level > 0:
            walk(self.hblock, self.ridxofs, self.level)
        return out

    def record_pieces(self, rec):
        """データレコードがブロック内に占める範囲: [(ブロック, 開始, 終了)]."""
        out = []
        remain, start = rec['size'], rec['offset']
        bs = self.disk.blksz
        for blk, cnt in rec['extents']:
            for k in range(cnt):
                if remain <= 0:
                    out.append((blk + k, 0, 0))      # 割り当てのみ (データなし)
                    continue
                n = min(remain, bs - start)
                out.append((blk + k, start, start + n))
                remain -= n
                start = 0
        return out, remain

    def read_record(self, rec):
        """データレコードの内容を返す. 複数ブロックにまたがる場合は連結する (4.3.7 図72)."""
        out = bytearray()
        remain = rec['size']
        start = rec['offset']
        for blk, cnt in rec['extents']:
            if remain <= 0:
                break
            n = min(remain, cnt * self.disk.blksz - start)
            out += self.disk._read(self.disk.block_offset(blk) + start, n)
            remain -= n
            start = 0
        if remain > 0:
            raise FormatError('レコードの割り当てブロックが不足しています')
        return bytes(out)

    @property
    def refcount(self):
        if self.ref == 255:              # 拡張形式: 255 以上はヘッダに実数
            rc = struct.unpack_from('<I', self.hblock, 172)[0]
            return rc or 255
        return self.ref

    def links(self):
        """リンクレコードのみ: [(レコード番号, リンク先 fid, 属性データ1〜5)]."""
        return [(i, r['link_fid'], r['attrs']) for i, r in enumerate(self.records()) if r['kind'] == 'link']

    def records(self):
        """レコードの一覧を返す (dict のリスト)."""
        if getattr(self, '_recs', None) is not None:
            return self._recs
        if self.ftype == 0:                  # リンクファイルはレコードを持たない
            self._recs = []
            return self._recs
        recs = []
        for e in self._index_entries():
            b0, b1 = e[0], e[1]
            if b0 != 0:                                   # 接続インデックス
                if not recs:
                    raise FormatError('先頭に接続インデックスがあります')
                for k in range(4):
                    v = struct.unpack_from('<I', e, k * 4)[0]
                    if v & 0xFF:
                        recs[-1]['extents'].append(((v >> 8) & 0xFFFFFF, v & 0xFF))
            elif b1 == 0x80:                              # リンクインデックス
                subtype, lfid = struct.unpack_from('<2H', e, 2)
                attrs = struct.unpack_from('<5H', e, 6)
                recs.append({'kind': 'link', 'type': 0, 'subtype': subtype,
                             'link_fid': lfid, 'attrs': attrs, 'size': 0, 'extents': []})
            elif b1 != 0:                                 # 通常インデックス
                subtype = struct.unpack_from('<H', e, 2)[0]
                start = struct.unpack_from('<H', e, 6)[0]
                size = struct.unpack_from('<I', e, 8)[0]
                v = struct.unpack_from('<I', e, 12)[0]
                ext = [((v >> 8) & 0xFFFFFF, v & 0xFF)] if v & 0xFF else []
                recs.append({'kind': 'data', 'type': b1 & 0x1F, 'subtype': subtype,
                             'offset': start, 'size': size, 'extents': ext})
            # b0 == b1 == 0: 未使用インデックス
        self._recs = recs
        return recs


def cmd_info(d, args):
    st = FileManager(d).fs_sts()
    print('image format     : %s' % d.img.kind)
    print('partition offset : 0x%x' % d.base)
    print('boot area size   : %d' % d.boot_size)
    print('disk id / format : 0x%04x / 0x%04x' % (d.disk_id, d.fmt_id))
    print('block size       : %d' % st['bsize'])
    print('blocks total/free: %d / %d' % (st['nblk'], st['nfree']))
    print('max files        : %d' % st['nfile'])
    print('fs name          : %s' % st['name'])
    print('device           : %s' % st['locat'])
    print('created / updated: %s / %s' % (fmt_time(st['ctime']), fmt_time(st['mtime'])))


# ---------------------------------------------------------------------------
# パス解決
#   /A/B/C     ルートからリンクをたどる (相対パスもルート起点. "." と ".." 可)
#   名前:N     同じ名前の N 番目 (0 起点, ":0" は省略形と同じ)
#   %c         次の 1 文字をそのまま扱う (%*, %:, %/, %% など)
#   *          ワイルドカード
#   全角/半角は区別しない
# ---------------------------------------------------------------------------
FID_PATH = re.compile(r'^/SYS:([0-9A-Za-z]+)(?:/|$)')


def split_path(path):
    comps, cur, esc = [], [], False
    for ch in path:
        if esc:
            cur.append(('lit', ch))
            esc = False
        elif ch == '%':
            esc = True
        elif ch == '/':
            comps.append(cur)
            cur = []
        else:
            cur.append(('lit' if ch not in '*:' else ch, ch))
    comps.append(cur)
    return [c for c in comps if c]


def parse_comp(toks):
    """トークン列を (fnmatch パターン, ワイルドカード有無, 出現番号 or None) に."""
    occ = None
    if ':' in [t for t, _ in toks]:
        i = max(k for k, (t, _) in enumerate(toks) if t == ':')
        num = ''.join(ch for _, ch in toks[i + 1:])
        if num.isdigit() and all(t == 'lit' for t, _ in toks[i + 1:]):
            occ = int(num)
            toks = toks[:i]
    pat, wild = [], False
    for t, ch in toks:
        if t == '*':
            pat.append('*')
            wild = True
        else:   # 正規化してから fnmatch の特殊文字をエスケープ (例: "［" → "[" → "[[]")
            pat.append(''.join('[%s]' % c if c in '[]?*' else c for c in norm_name(ch)))
    return ''.join(pat), wild, occ


class Resolver:
    """パス解決. ファイルの参照はすべてファイル管理層 (FileManager) を通す."""

    def __init__(self, fs):
        self.fs = fs if isinstance(fs, FileManager) else FileManager(fs)
        self.d = self.fs.d
        self.link_attrs = {}            # 解決したパス → そこへ至るリンクの属性データ1〜5
        self.link_pos = {}              # 解決したパス → (親ファイル fid, リンクレコード番号)

    def file(self, fid):
        return self.fs.fil_sts(fid)

    def children(self, f):
        """(表示名, 名前, 出現順, fid, リンク属性, レコード番号) の列. 同名 2 つ目以降は "名前:N"."""
        seen = {}
        out = []
        for recno, fid, attrs in self.fs.fnd_lnk(f.fid):
            try:
                name = self.file(fid).name
            except FormatError:
                name = '?'
            key = norm_name(name)
            n = seen.get(key, 0)
            seen[key] = n + 1
            dn = disp_name(name)
            out.append((dn if n == 0 else '%s:%d' % (dn, n), name, n, fid, attrs, recno))
        return out

    def resolve(self, path):
        """パスに一致する [(表示パス, BFile)] を返す (ワイルドカードで複数あり得る).
        "/SYS:ファイルID[/...]" はファイル ID のファイルを起点にする (bfsk 独自の拡張)."""
        results = [([], [0], None, None)]   # (表示名, fid スタック, 最後のリンク属性, (親fid, レコード番号))
        comps = split_path(path)
        if path.startswith('/') and comps and ''.join(ch for _, ch in comps[0]) == 'SYS' and \
                all(t == 'lit' for t, _ in comps[0]):
            comps = comps[1:]                   # "/SYS/..." はルートからの絶対パス (接続名 SYS)
        m = FID_PATH.match(path)
        if m:
            try:
                fid = cli_int(m.group(1))
            except ValueError:
                raise FormatError('%s: ファイル ID の形式が不正です' % path)
            self.file(fid)
            results = [(['SYS:%d' % fid], [fid], None, None)]
            comps = comps[1:]
        for toks in comps:
            word = ''.join(ch for _, ch in toks)
            nxt = []
            for names, stack, la, lp in results:
                if word == '.' and all(t == 'lit' for t, _ in toks):
                    nxt.append((names, stack, la, lp))
                    continue
                if word == '..' and all(t == 'lit' for t, _ in toks):
                    nxt.append((names[:-1], stack[:-1] or [0], None, None))
                    continue
                pat, wild, occ = parse_comp(toks)
                for disp, name, n, fid, attrs, recno in self.children(self.file(stack[-1])):
                    if not fnmatch.fnmatchcase(norm_name(name), pat):
                        continue
                    if occ is not None and n != occ:
                        continue
                    if occ is None and not wild and n != 0:
                        continue
                    nxt.append((names + [disp], stack + [fid], attrs, (stack[-1], recno)))
            results = nxt
        if not results:
            raise FormatError('%s: ファイルが見つかりません' % path)
        out = []
        for n, st, la, lp in results:
            p = '/' + '/'.join(n) if n and n[0].startswith('SYS:') else '/SYS' + ''.join('/' + x for x in n)
            self.link_attrs[p] = la
            self.link_pos[p] = lp
            out.append((p, self.file(st[-1])))
        return out

    def targets(self, args, default='/'):
        """コマンド引数のパスを [(表示パス, BFile)] に."""
        items = args.paths if getattr(args, 'paths', None) else [default]
        out = []
        for a in items:
            out.extend(self.resolve(a))
        return out


# ---------------------------------------------------------------------------
# コマンド (超漢字V CLI に準拠した出力)
# ---------------------------------------------------------------------------
def fmt_time_s(t):
    if t in (0, 0xFFFFFFFF):
        return '-'.ljust(19)
    return fmt_time(t)


V_HIDDEN = 0x8000       # 仮身属性 (リンク属性データ1) の隠蔽仮身ビット (3.8.3.1 図123)


def attr_str(f, link_attrs=None):
    """CLI ls -l の ATR: H 隠蔽仮身 (リンク側の属性), P 削除不可, R 書込不可, 1/2 アプリ属性."""
    h = 'H' if link_attrs and link_attrs[0] & V_HIDDEN else '-'
    a = f.attr
    return h + ''.join(c if a & bit else '-' for c, bit in
                       (('P', 0x20), ('R', 0x10), ('1', 0x40), ('2', 0x80)))


def cmd_df(d, args):
    st = FileManager(d).fs_sts()
    total = st['nblk'] * st['bsize'] // 1024
    free = st['nfree'] * st['bsize'] // 1024
    used = (100 * (st['nblk'] - st['nfree']) // st['nblk']) if st['nblk'] else 0
    print('PATH  DEV      TOTAL      FREE USED  UNIT MAXFILE NAME')
    print('%-4s  %-4s%10d%10d%4d%%%6d%7d  %s' % ('/SYS', 'hda0' if d.base or d.boot_size >= 0x1000 else 'fda', total, free, used,
                                                st['bsize'], st['nfile'], disp_name(st['name'])))


def cli_size(n):
    """ls -l の SIZE 欄: 7 桁を越えると K 単位 (CLI と同じ)."""
    return ('%7d ' % n) if n <= 9999999 else ('%7dK' % (n // 1024))


def cmd_ls(d, args):
    r = Resolver(d)
    targets = r.targets(args)
    for k, (tpath, tf) in enumerate(targets):
        if args.self_:
            entries = [(disp_name(tf.name), tf.fid, r.link_attrs.get(tpath))]
        else:
            entries = [(disp, fid, attrs) for disp, _, _, fid, attrs, _ in r.children(tf)]
            if len(targets) > 1:
                print('%s:' % tpath)
        if args.long and k == 0:               # 見出しは最初の 1 回だけ (CLI と同じ)
            print('ATYPE ATR   NREC NREF  SIZE   BLK  MTIME            NAME')
        elif args.time and k == 0:
            print('     CTIME            MTIME            ATIME       NAME')
        col = 0
        for disp, fid, lattrs in entries:
            try:
                f = r.file(fid)
            except FormatError as e:
                print('%s  (%s)' % (disp, e))
                continue
            name = disp
            if args.classify:
                if f.nlink:
                    name += '/'
                if f.refcount > 1:
                    name += '#'
            if args.long:
                print('%04x %s %2d %-2d %2d %s%5d  %s  %s' % (
                    f.apptype, attr_str(f, lattrs), f.nrec, f.nlink, f.refcount, cli_size(f.size),
                    f.nblk, cli_time(f.t_upd), name))
            elif args.time:
                print('%s  %s  %s  %s' % (cli_time(f.t_gen), cli_time(f.t_upd), cli_time(f.t_acc), name))
            else:                               # 20 桁 x 4 列 (CLI と同じ)
                if col == 3:
                    print(name)
                    col = 0
                else:
                    print(name + ' ' * max(1, 20 - dwidth(name)), end='')
                    col += 1
        if col:
            print()


def cli_subtype(st):
    return '0' if st == 0 else '0x%x' % st


def cmd_fs(d, args):
    r = Resolver(d)
    targets = r.targets(args)
    for tpath, f in targets:
        print('%s:' % tpath)
        recs = r.fs.rec_list(f.fid)
        for i, rec in enumerate(recs):
            if rec['kind'] == 'link':
                try:
                    name = disp_name(r.file(rec['link_fid']).name)
                except FormatError:
                    name = '*?'
                if args.long:
                    print('%2d: %3d %6s : %5d [%s] : %s' % (
                        i, 0, cli_subtype(rec['subtype']), rec['link_fid'],
                        ' '.join('%04x' % a for a in rec['attrs']), name))
                else:
                    print('%2d: %3d %6s : %s' % (i, 0, cli_subtype(rec['subtype']), name))
            else:
                line = '%2d: %3d %6s : %d' % (i, rec['type'], cli_subtype(rec['subtype']), rec['size'])
                if args.blocks:
                    line += '  [blk %s ofs %d]' % (' '.join('%d+%d' % (a, c) if c > 1 else '%d' % a
                                                           for a, c in rec['extents']) or '-', rec['offset'])
                print(line)
        print('--- End of Record ---')
        if len(recs) != f.nrec:
            print('warning: ヘッダの総レコード数 %d と一覧の件数 %d が一致しません'
                  % (f.nrec, len(recs)), file=sys.stderr)


def cmd_stat(d, args):
    r = Resolver(d)
    for tpath, f in r.targets(args):
        print('path      : %s' % tpath)
        print('file id   : %d (header block %d, ref %d)' % (f.fid, f.blk, f.refcount))
        print('name      : %s' % f.name)
        print('type/attr : %d / 0x%03x   apptype 0x%04x' % (f.ftype, f.attr, f.apptype))
        print('level     : %d   records %d   links %d   bytes %d   blocks %d'
              % (f.level, f.nrec, f.nlink, f.size, f.nblk))
        print('created   : %s' % fmt_time(f.t_gen))
        print('accessed  : %s' % fmt_time(f.t_acc))
        print('updated   : %s' % fmt_time(f.t_upd))
        g, p = f.acc_levels
        print('mode      : owner %d  group 0x%03x  public 0x%03x  attr 0x%03x' % (
            f.owner_mode, g & 0xFFF, p & 0xFFF, f.attr))
        print('limit     : %s' % fmt_time(f.t_keep))
        print('index ofs : %d' % f.ridxofs)
        print('fragments : %s' % (' '.join('%d@%d+%d' % fr for fr in f.fragments()) or '-'))
        if f.hdr_fid != f.fid:
            print('warning: ヘッダ内ファイルID %d が一致しません' % f.hdr_fid, file=sys.stderr)


# ---------------------------------------------------------------------------
# ファイル管理層: BTRON3 仕様 1.6.8 のシステムコールに相当する操作
#   cre_fil / del_fil / ins_rec / apd_rec / del_rec / chg_fnm など
#   ブロック割り当て方針: データレコードは新しいブロックの先頭から置く
#   (既存レコードと同じブロックは共有しない). 区切りコード (4.3.8) と
#   フラグメントテーブルはファイルのレコード配置から毎回作り直す.
# ---------------------------------------------------------------------------
REF_MAX = 255


class FileManager:
    """ファイル管理層. 読み取り専用で開いたディスクでも参照系の操作は使える."""

    def __init__(self, disk):
        d = self.d = disk
        self.modified = False
        self.nbytes = d.nbmp * (4 if d.fmt_id != 0x6400 else 1)
        self.bm_off = d.block_offset(1) + 128
        self.bm = bytearray(d._read(self.bm_off, self.nbytes))
        self.bad = d._read(self.bm_off + self.nbytes, self.nbytes)
        self.dirty = set()
        self.nfree = d.nfree
        self.first_data = 1 + d.ssys + d.sfidt + d.sfnmt
        self.hint = self.first_data
        self.cache = {}

    # --- 参照系 (fs_sts / fil_sts / rea_rec / fnd_lnk 相当) -------------------
    def fs_sts(self):
        """ファイルシステム管理情報 (FS_STATE 相当)."""
        d = self.d
        return {'bsize': d.blksz, 'nfile': d.nfmax, 'lang': d.lang, 'level': d.acl,
                'nblk': d.nlb, 'nfree': self.nfree, 'mtime': d.t_upd, 'ctime': d.t_gen,
                'name': d.fsname, 'locat': d.devname}

    def fil_sts(self, fid):
        """ファイル管理情報 (F_STATE 相当). ファイルヘッダを読んだ BFile を返す."""
        return self.file(fid)

    def rec_list(self, fid):
        """全レコードのインデックス情報 (see_rec / rea_rec で順にたどるのに相当)."""
        return self.file(fid).records()

    def rea_rec(self, fid, recno):
        """レコード読み込み. データレコードはバイト列、リンクレコードは (fid, 属性1〜5)."""
        f = self.file(fid)
        recs = f.records()
        if not 0 <= recno < len(recs):
            raise FormatError('レコード番号 %d は範囲外です (0..%d)' % (recno, len(recs) - 1))
        r = recs[recno]
        if r['kind'] == 'link':
            return r['link_fid'], r['attrs']
        return f.read_record(r)

    def fnd_lnk(self, fid):
        """リンクレコードの一覧 [(レコード番号, リンク先 fid, 属性1〜5)] (fnd_lnk の繰り返しに相当)."""
        return self.file(fid).links()

    # --- ファイル管理情報の変更 (chg_fnm / chg_fmd / chg_fat / chg_ftm) ---------
    def chg_fnm(self, fid, name):
        if fid == 0:
            raise FormatError('ルートファイル (ファイルシステム名) の変更には未対応です')
        self.set_name(fid, name)

    def chg_fmd(self, fid, owner=None, grpacc=None, pubacc=None):
        f = self.file(fid)
        ftype = (f.ftype << 12) | f.attr
        if owner is not None:
            if not 0 <= owner <= 7:
                raise FormatError('所有者アクセスモードは 0〜7 です')
            ftype = (ftype & ~0x7) | owner
        fields = {H_TYPE: ('<H', ftype), H_ATIME: ('<I', now_btron())}
        for v, off in ((grpacc, H_GRPACC), (pubacc, H_PUBACC)):
            if v is not None:
                if not 0 <= v <= 0xFFF:
                    raise FormatError('アクセスレベルは 0x000〜0xFFF です')
                fields[off] = ('<H', v)
        f.update_header(fields)

    def chg_fat(self, fid, op):
        """アクセス属性変更: 1/2 書込不可 セット/リセット, 3/4 削除不可 セット/リセット."""
        bit, on = {1: (0x10, True), 2: (0x10, False), 3: (0x20, True), 4: (0x20, False)}.get(op, (None, None))
        if bit is None:
            raise FormatError('アクセス属性の指定は 1〜4 です')
        f = self.file(fid)
        ftype = (f.ftype << 12) | f.attr
        ftype = (ftype | bit) if on else (ftype & ~bit)
        f.update_header({H_TYPE: ('<H', ftype), H_ATIME: ('<I', now_btron())})

    def chg_ftm(self, fid, ltime=None, atime=None, mtime=None):
        fields = {off: ('<I', v) for v, off in ((ltime, H_LTIME), (atime, H_ATIME), (mtime, H_MTIME))
                  if v is not None}
        if fields:
            self.file(fid).update_header(fields)

    # --- ファイル -------------------------------------------------------
    def file(self, fid):
        f = self.cache.get(fid)
        if f is None:
            blk, ref = self.d.fid_entry(fid)
            if blk == 0:
                raise FormatError('ファイルID %d は存在しません' % fid)
            f = self.cache[fid] = BFile(self.d, fid, blk, ref)
        return f

    def _fid_write(self, fid, blk, ref):
        self.d.write(self.d.block_offset(self.d.fidt_blk) + fid * 4,
                     struct.pack('<I', (min(ref, REF_MAX) << 24) | blk if blk else 0))

    def refcount(self, fid):
        return self.file(fid).refcount

    def _set_ref(self, fid, n):
        f = self.file(fid)
        if n > REF_MAX and self.d.fmt_id == 0x6400:
            raise FormatError('参照カウントが上限 (255) を越えます')
        self._fid_write(fid, f.blk, n)
        if self.d.fmt_id != 0x6400:
            struct.pack_into('<I', f.hblock, 172, n if n >= REF_MAX else 0)
            self.d.write(self.d.block_offset(f.blk), bytes(f.hblock[:192]))
        f.ref = min(n, REF_MAX)

    # --- ブロック ---------------------------------------------------------
    def _bit(self, n):
        return self.bm[n >> 3] >> (n & 7) & 1

    def _setbit(self, n, v):
        i = n >> 3
        if v:
            self.bm[i] |= 1 << (n & 7)
        else:
            self.bm[i] &= ~(1 << (n & 7)) & 0xFF
        self.dirty.add(i)

    def alloc(self, count):
        """count ブロックを確保して [(開始ブロック, 個数)] を返す. 連続領域を優先する."""
        if count == 0:
            return []
        if count > self.nfree:
            raise FormatError('ディスクの領域が不足しています (%d ブロック必要, 空き %d)' % (count, self.nfree))
        self.modified = True
        runs = []
        need = count
        n = self.hint
        nlb = self.d.nlb
        wrapped = False
        # まず count 個の連続領域を探す. 見つからなければ見つかった順に寄せ集める
        best = self._find_run(count)
        if best is not None:
            runs = [(best, count)]
        else:
            while need:
                if n >= nlb:
                    if wrapped:
                        raise FormatError('ディスクの領域が不足しています')
                    n, wrapped = self.first_data, True
                if self.bm[n >> 3] == 0xFF and n & 7 == 0:
                    n += 8
                    continue
                if not self._bit(n):
                    start = n
                    while n < nlb and need and not self._bit(n) and n - start < need:
                        n += 1
                    runs.append((start, n - start))
                    need -= n - start
                else:
                    n += 1
        out = []
        for start, cnt in runs:
            for b in range(start, start + cnt):
                self._setbit(b, 1)
            while cnt:
                k = min(cnt, REF_MAX)
                out.append((start, k))
                start += k
                cnt -= k
        self.nfree -= count
        self.hint = out[-1][0] + out[-1][1]
        return out

    def _find_run(self, count):
        nlb = self.d.nlb
        for base in (self.hint, self.first_data):
            n = base
            run = 0
            while n < nlb:
                if run == 0 and n & 7 == 0 and self.bm[n >> 3] == 0xFF:
                    n += 8
                    continue
                if self._bit(n):
                    run = 0
                else:
                    run += 1
                    if run == count:
                        return n - count + 1
                n += 1
        return None

    def free(self, blocks):
        self.modified = True
        for b in blocks:
            if not self._bit(b):
                raise FormatError('ブロック %d は既に未使用です' % b)
            if self.bad[b >> 3] >> (b & 7) & 1:
                continue
            self._setbit(b, 0)
            self.nfree += 1

    def sync(self):
        """ビットマップ・空きブロック数・システムブロック更新日時を書き込む (変更があったときだけ)."""
        if not self.modified:
            return
        if self.dirty:
            lo, hi = min(self.dirty), max(self.dirty) + 1
            self.d.write(self.bm_off + lo, bytes(self.bm[lo:hi]))
            self.dirty.clear()
        sys_off = self.d.block_offset(1)
        self.d.write(sys_off + 36, struct.pack('<II', self.nfree, now_btron()))
        self.d.nfree = self.nfree
        self.modified = False

    # --- ファイル生成 / 削除 (cre_fil / del_fil) ---------------------------
    def default_ridxofs(self):
        if self.d.fmt_id != 0x6402:
            return 384
        # 実イメージ (32KB ブロック) の新規ファイルは 6336 = 192 + ブロック長 * 3/16
        return min(192 + (self.d.blksz * 3 // 16 + 15) // 16 * 16, self.d.blksz - 16)

    def cre_fil(self, parent, name, atype=0, ftype=0x1000, grpacc=0, pubacc=0x0FFF):
        """ファイル生成. parent を指定すると末尾にリンクレコードを追加する (F_FIX)."""
        d = self.d
        name40 = tron_encode(name)
        fidt = d._read(d.block_offset(d.fidt_blk), d.nfmax * 4)
        fid = next((i for i in range(1, d.nfmax) if fidt[i * 4:i * 4 + 4] == b'\0\0\0\0'), None)
        if fid is None:
            raise FormatError('最大ファイル数を越えました')
        (blk, _), = self.alloc(1)
        t = now_btron()
        h = bytearray(d.blksz)
        struct.pack_into('<IHH', h, 0, FH_START, ftype, atype)
        struct.pack_into('<HHhh', h, 64, grpacc, pubacc, 0, 0)
        struct.pack_into('<III', h, 72, 0, 1, 0)
        struct.pack_into('<IIII', h, 92, 0xFFFFFFFF, t, t, t)
        h[108:148] = name40
        if d.fmt_id == 0x6402:
            struct.pack_into('<H', h, 176, self.default_ridxofs())
        struct.pack_into('<HI', h, 186, fid, FH_END)
        d.write(d.block_offset(blk), bytes(h))
        self._fid_write(fid, blk, 0)
        d.set_short_name(fid, short_name(name40))
        self.cache.pop(fid, None)
        if parent is not None:
            self.ins_rec(parent, None, 0, 0, link=(fid, (0, 0, 0, 0, 0)))
        return fid

    def copy_header(self, src, name=None):
        """ファイルヘッダ (属性・日時など) を引き継いだ空のファイルを作る (参照カウント 0)."""
        f = self.file(src)
        fid = self.cre_fil(None, name or f.name, f.apptype, (f.ftype << 12) | f.attr, *f.acc_levels)
        g = self.file(fid)
        if f.ftype == 0:      # リンクファイル: ファイル所在データ等をそのまま複製
            g.hblock[176:178] = f.hblock[176:178]
            g.hblock[192:] = f.hblock[192:]
            self.d.write(self.d.block_offset(g.blk), bytes(g.hblock))
            self.cache.pop(fid, None)
        return fid

    def _delete_file(self, fid, recursive):
        """参照カウント 0 のファイルを削除する. recursive なら含まれるリンク先も参照 0 で削除."""
        f = self.file(fid)
        if f.level:
            raise FormatError('%s: インデックスレベル 1 以上のファイルの削除には未対応です' % f.name)
        blocks = {f.blk}
        children = []
        for r in f.records():
            if r['kind'] == 'link':
                children.append(r['link_fid'])
            else:
                for b, c in r['extents']:
                    blocks.update(range(b, b + c))
        self.free(sorted(blocks))
        self._fid_write(fid, 0, 0)
        self.d.set_short_name(fid, 0)
        self.cache.pop(fid, None)
        removed = [fid]
        for c in children:
            if self.d.fid_entry(c)[0] == 0:      # 既に削除済み (循環参照)
                continue
            n = self.refcount(c) - 1
            self._set_ref(c, n)
            if n == 0 and recursive:
                removed += self._delete_file(c, True)
        return removed

    def del_fil(self, parent, recno, recursive=False):
        """parent のレコード recno (リンク) を削除し、参照 0 になればファイル本体を削除する."""
        pf = self.file(parent)
        rec = pf.records()[recno]
        if rec['kind'] != 'link':
            raise FormatError('レコード %d はリンクレコードではありません' % recno)
        fid = rec['link_fid']
        f = self.file(fid)
        if fid == 0:
            raise FormatError('ルートファイルは削除できません')
        if f.refcount <= 1:
            if f.attr & 0x20:
                raise FormatError('%s は削除不可属性です' % f.name)
            if f.nlink and not recursive:
                raise FormatError('%s はリンクレコードを含んでいます (-r で中も削除)' % f.name)
        self.del_rec(parent, recno, delete_unref=False)
        if self.refcount(fid) == 0:
            return self._delete_file(fid, recursive)
        return []

    # --- レコード操作 (ins_rec / apd_rec / del_rec) ------------------------
    def _place_data(self, data):
        """データを新しいブロックに書き込み (開始オフセット, extents) を返す."""
        bs = self.d.blksz
        size = len(data)
        if size == 0:
            return 0, []
        lead = 0
        nblk = (lead + size + bs - 1) // bs
        ext = self.alloc(nblk)
        buf = bytes(lead) + data
        pos = 0
        for b, c in ext:
            self.d.write(self.d.block_offset(b), buf[pos:pos + c * bs].ljust(c * bs, b'\0'))
            pos += c * bs
        return lead, ext

    def ins_rec(self, fid, recno, rtype, subtype, data=b'', link=None):
        """recno の直前 (None なら末尾 = apd_rec) にレコードを挿入する.
        rtype 0 はリンクレコードで link = (リンク先 fid, 属性データ1〜5)."""
        f = self.file(fid)
        self._writable(f)
        recs = list(f.records())
        if recno is None:
            recno = len(recs)
        if not 0 <= recno <= len(recs):
            raise FormatError('レコード番号 %d は範囲外です' % recno)
        if rtype == 0:
            lfid, attrs = link
            target = self.file(lfid)
            new = {'kind': 'link', 'type': 0, 'subtype': subtype, 'link_fid': lfid,
                   'attrs': tuple(attrs), 'size': 0, 'extents': []}
            self._set_ref(lfid, target.refcount + 1)
        else:
            if not 1 <= rtype <= 31:
                raise FormatError('レコードタイプは 1〜31 です')
            off, ext = self._place_data(bytes(data))
            new = {'kind': 'data', 'type': rtype, 'subtype': subtype, 'offset': off,
                   'size': len(data), 'extents': ext}
        recs.insert(recno, new)
        self._store(f, recs, touched={b for b0, c in new['extents'] for b in range(b0, b0 + c)})
        return recno

    def wri_rec(self, fid, recno, data, subtype=None):
        """データレコードの内容 (と必要ならサブタイプ) を置き換える. タイプは変えない."""
        r = self.rec_list(fid)[recno]
        if r['kind'] != 'data':
            raise FormatError('レコード %d はデータレコードではありません' % recno)
        st = r['subtype'] if subtype is None else subtype
        self.del_rec(fid, recno)
        self.ins_rec(fid, recno, r['type'], st, data)

    def apd_rec(self, fid, rtype, subtype, data=b'', link=None):
        return self.ins_rec(fid, None, rtype, subtype, data, link)

    def del_rec(self, fid, recno, delete_unref=False):
        """レコード削除. リンクレコードならリンク先の参照カウントを -1 する.
        戻り値: リンク先の参照カウントが 0 になったら 1 (del_rec と同じ)."""
        f = self.file(fid)
        self._writable(f)
        recs = list(f.records())
        if not 0 <= recno < len(recs):
            raise FormatError('レコード番号 %d は範囲外です' % recno)
        old = recs.pop(recno)
        ret = 0
        touched = set()
        if old['kind'] == 'link':
            n = self.refcount(old['link_fid']) - 1
            self._set_ref(old['link_fid'], n)
            ret = 1 if n == 0 else 0
        else:
            still = {b for r in recs if r['kind'] == 'data'
                     for b0, c in r['extents'] for b in range(b0, b0 + c)}
            mine = {b for b0, c in old['extents'] for b in range(b0, b0 + c)}
            self.free(sorted(mine - still))
            touched = mine & still
        self._store(f, recs, touched=touched)
        if ret and delete_unref:
            self._delete_file(old['link_fid'], True)
        return ret

    def _writable(self, f):
        if f.ftype == 0:
            raise FormatError('%s はリンクファイルです' % f.name)
        if f.level:
            raise FormatError('%s: インデックスレベル 1 以上のファイルの変更には未対応です' % f.name)

    # --- ファイルの書き戻し ------------------------------------------------
    @staticmethod
    def _index_entries(recs):
        out = []
        for r in recs:
            if r['kind'] == 'link':
                e = struct.pack('<BBHH5H', 0, 0x80, r['subtype'], r['link_fid'], *r['attrs'])
                out.append(e)
                continue
            ext = list(r['extents'])
            first = ext.pop(0) if ext else (0, 0)
            out.append(struct.pack('<BBHHHII', 0, 0x80 | r['type'], r['subtype'], 0, r['offset'], r['size'],
                                   (first[0] << 8) | first[1] if first[1] else 0))
            while ext:
                grp, ext = ext[:4], ext[4:]
                out.append(b''.join(struct.pack('<I', (b << 8) | c) for b, c in grp).ljust(16, b'\0'))
        return out

    def _layout(self, f, recs):
        """ブロックごとのレコード範囲: {blk: [(開始, 終了)]}, 使用ブロック集合."""
        bs = self.d.blksz
        pieces, blocks = {}, set()
        for r in recs:
            if r['kind'] != 'data':
                continue
            remain, start = r['size'], r['offset']
            for b0, c in r['extents']:
                for b in range(b0, b0 + c):
                    blocks.add(b)
                    if remain > 0:
                        n = min(remain, bs - start)
                        pieces.setdefault(b, []).append((start, start + n))
                        remain -= n
                        start = 0
        return pieces, blocks

    def _codes(self, ps):
        """ブロック内のレコード範囲から区切りコードと空き領域を求める (4.3.8 図73)."""
        bs = self.d.blksz
        codes, free = [], []
        pos = 0
        for a, b in sorted(ps):
            gap = a - pos
            if pos == 0 and gap:
                codes.append((a - 2, gap - 2))
                if gap - 2:
                    free.append((0, gap - 2))
            elif pos:
                codes += [(pos, gap - 4), (a - 2, gap - 4)]
                if gap - 4:
                    free.append((pos + 2, gap - 4))
            pos = b
        if 0 < pos < bs - 1:               # 末尾 1 バイトだけ余るときはコードを置かない (実機と同じ)
            codes.append((pos, bs - pos - 2))
            if bs - pos - 2:
                free.append((pos + 2, bs - pos - 2))
        return codes, free

    def _store(self, f, recs, touched=()):
        d = self.d
        bs = d.blksz
        entries = self._index_entries(recs)
        cap = (bs - f.ridxofs) // 16
        if len(entries) > cap:
            raise FormatError('%s: レコードインデックスが %d 個を越えます (インデックスレベル 1 以上は未対応)'
                              % (f.name, cap))
        pieces, blocks = self._layout(f, recs)
        frees = []
        for b in set(touched) | set(pieces):
            codes, free = self._codes(pieces.get(b, []))
            if b in touched:
                blk = bytearray(d.read_block(b))
                for pos, val in codes:
                    struct.pack_into('<H', blk, pos, val)
                d.write(d.block_offset(b), bytes(blk))
            frees += [(size, b, off) for off, size in free]
        frees.sort(key=lambda x: (-x[0], x[1], x[2]))
        h = f.hblock
        if d.fmt_id == 0x6402:
            nfr = (f.ridxofs - 192) // 8
            area = b''.join(struct.pack('<HHI', sz, off, b) for sz, b, off in frees[:nfr])
            h[192:f.ridxofs] = area.ljust(f.ridxofs - 192, b'\0')
        else:
            area = b''.join(struct.pack('<HI', sz, b * bs + off) for sz, b, off in frees[:32])
            h[192:384] = area.ljust(192, b'\0')
        idx = b''.join(entries)
        h[f.ridxofs:] = idx.rjust(bs - f.ridxofs, b'\0')      # 末尾に詰めて置く (実イメージと同じ)
        t = now_btron()
        nlink = sum(1 for r in recs if r['kind'] == 'link')
        struct.pack_into('<hh', h, 68, nlink, 0)
        struct.pack_into('<III', h, 72, sum(r['size'] for r in recs if r['kind'] == 'data'),
                         1 + len(blocks), len(recs))
        struct.pack_into('<II', h, 96, t, t)
        d.write(d.block_offset(f.blk), bytes(h))
        self.cache.pop(f.fid, None)

    def set_name(self, fid, name):
        f = self.file(fid)
        name40 = tron_encode(name)
        f.update_header({H_NAME: ('40s', name40), H_ATIME: ('<I', now_btron())})
        self.d.set_short_name(fid, short_name(name40))
        self.cache.pop(fid, None)


# ---------------------------------------------------------------------------
# 整合性チェック (読み取りのみ)
# ---------------------------------------------------------------------------
class Checker:
    def __init__(self, d, verbose=False):
        self.d = d
        self.verbose = verbose
        self.errors = []
        self.warnings = []

    def err(self, msg):
        self.errors.append(msg)

    def warn(self, msg):
        self.warnings.append(msg)

    def bitmap(self, which):
        d = self.d
        nbytes = d.nbmp * (4 if d.fmt_id != 0x6400 else 1)
        return d._read(d.block_offset(1) + 128 + which * nbytes, nbytes)

    def run(self):
        d = self.d
        bs = d.blksz
        # --- システムヘッダ
        if bs & (bs - 1) or bs < 512:
            self.err('論理ブロック長 %d が 2 のべき乗でない' % bs)
        nbits = d.nbmp * (32 if d.fmt_id != 0x6400 else 8)
        if nbits < d.nlb:
            self.err('ビットマップ (%d ビット) が論理ブロック数 %d より小さい' % (nbits, d.nlb))
        used_bm = self.bitmap(0)
        bad_bm = self.bitmap(1)

        def bit(bm, n):                       # 付録A: 各バイトの最下位ビットが先
            return bm[n >> 3] >> (n & 7) & 1

        full = d.nlb >> 3
        nset = sum(bin(x).count('1') for x in used_bm[:full]) + \
            sum(bit(used_bm, n) for n in range(full * 8, d.nlb))
        zeros = d.nlb - nset
        if zeros != d.nfree:
            self.err('空き論理ブロック数 %d がビットマップの未使用数 %d と一致しない' % (d.nfree, zeros))
        # --- ファイル ID テーブル / 各ファイル
        owner = {}                            # ブロック → 所有者 (fid or 'system')
        for n in range(1 + d.ssys + d.sfidt + d.sfnmt):
            owner[n] = 'system'
        fidt = d._read(d.block_offset(d.fidt_blk), d.nfmax * 4)
        fnmt = d._read(d.block_offset(d.fnmt_blk), d.nfmax * 4)
        files = {}
        links_to = {}
        for fid in range(d.nfmax):
            v = struct.unpack_from('<I', fidt, fid * 4)[0]
            if v == 0:
                continue
            blk, ref = v & 0xFFFFFF, v >> 24
            if not 0 < blk < d.nlb:
                self.err('fid %d: 開始ブロック %d が範囲外' % (fid, blk))
                continue
            try:
                f = BFile(d, fid, blk, ref)
            except FormatError as e:
                self.err('fid %d: %s' % (fid, e))
                continue
            files[fid] = f
            self.check_file(f, fnmt, owner, links_to)
        # --- 参照カウント
        for fid, f in files.items():
            n = links_to.get(fid, 0) + (1 if fid == 0 else 0)   # ルートは参照カウント 1 (4.2.5)
            if f.refcount != n:
                self.err('fid %d %s: 参照カウント %d と実際の固定リンク数 %d が一致しない' % (fid, f.name, f.refcount, n))
        for fid in links_to:
            if fid not in files:
                self.err('fid %d: 存在しないファイルへのリンクがある (%d 個)' % (fid, links_to[fid]))
        # --- ルートからの到達性
        if 0 not in files:
            self.err('ルートファイル (fid 0) がない')
        else:
            seen, stack = {0}, [0]
            while stack:
                for _, c, _ in files[stack.pop()].links():
                    if c in files and c not in seen:
                        seen.add(c)
                        stack.append(c)
            for fid in sorted(set(files) - seen):
                self.warn('fid %d %s: ルートから到達できない (参照カウント %d)' % (fid, files[fid].name, files[fid].refcount))
        # --- ビットマップと実際の使用ブロック
        for n in range(d.nlb):
            u = bit(used_bm, n)
            if bit(bad_bm, n):
                if not u:
                    self.err('ブロック %d: 不良ブロックが使用ブロックビットマップで未使用になっている' % n)
                continue
            if u and n not in owner:
                self.warn('ブロック %d: 使用中だがどのファイルにも属さない' % n)
            elif not u and n in owner:
                self.err('ブロック %d: %s が使用しているがビットマップでは未使用' % (n, owner[n]))
        self.nfiles = len(files)
        self.nblocks = len(owner)

    def claim(self, owner, blk, who, what):
        if not 0 < blk < self.d.nlb:
            self.err('%s: %s のブロック %d が範囲外' % (who, what, blk))
            return False
        prev = owner.get(blk)
        if prev is not None and prev != who:
            self.err('ブロック %d: %s と %s の両方が使用している' % (blk, prev, who))
            return False
        owner[blk] = who
        return True

    def check_file(self, f, fnmt, owner, links_to):
        d = self.d
        who = 'fid %d' % f.fid
        if f.hdr_fid != f.fid:
            self.err('%s: ヘッダ内のファイル ID %d が一致しない' % (who, f.hdr_fid))
        sn = struct.unpack_from('<I', fnmt, f.fid * 4)[0]
        if sn != short_name(bytes(f.hblock[108:148])):
            self.err('%s %s: 短縮名テーブルの値が一致しない' % (who, f.name))
        if f.ftype == 0:
            self.warn('%s: リンクファイル (未対応のため内容は検査しない)' % who)
            self.claim(owner, f.blk, who, 'ヘッダ')
            return
        blocks = {f.blk}
        self.claim(owner, f.blk, who, 'ヘッダ')
        for b in f.index_blocks():
            blocks.add(b)
            self.claim(owner, b, who, 'インデックス')
        try:
            recs = f.records()
        except FormatError as e:
            self.err('%s: %s' % (who, e))
            return
        size = nlink = 0
        pieces = {}
        for i, r in enumerate(recs):
            if r['kind'] == 'link':
                nlink += 1
                links_to[r['link_fid']] = links_to.get(r['link_fid'], 0) + 1
                continue
            size += r['size']
            ps, remain = f.record_pieces(r)
            if remain > 0:
                self.err('%s: レコード %d の割り当てブロックが %d バイト不足' % (who, i, remain))
            for blk, a, b in ps:
                if blk not in blocks:
                    blocks.add(blk)
                    self.claim(owner, blk, who, 'データ')
                if b > a:
                    pieces.setdefault(blk, []).append((a, b, i))
        if len(recs) != f.nrec:
            self.err('%s: 総レコード数 %d と実際 %d が一致しない' % (who, f.nrec, len(recs)))
        if nlink != f.nlink:
            self.err('%s: リンク数 %d と実際 %d が一致しない' % (who, f.nlink, nlink))
        if size != f.size:
            self.err('%s: 総バイト数 %d と実際 %d が一致しない' % (who, f.size, size))
        if len(blocks) != f.nblk:
            self.err('%s: 総使用ブロック数 %d と実際 %d が一致しない' % (who, f.nblk, len(blocks)))
        # --- 区切りコードと空き領域 (4.3.8)
        free = set()
        for blk, ps in pieces.items():
            data = d.read_block(blk)
            ps.sort()
            pos = 0
            for k, (a, b, i) in enumerate(ps):
                if a < pos:
                    self.err('%s: ブロック %d でレコードが重なっている (レコード %d)' % (who, blk, i))
                gap = a - pos
                if pos == 0 and gap:                     # 先頭の空き: [空き y][y][レコード]
                    y = gap - 2
                    if y < 0 or struct.unpack_from('<H', data, a - 2)[0] != y:
                        self.err('%s: ブロック %d 先頭の区切りコード不正' % (who, blk))
                    elif y:
                        free.add((blk, 0, y))
                elif pos:                                # レコード間: [x][空き x][x]
                    x = gap - 4
                    c1 = struct.unpack_from('<H', data, pos)[0] if gap >= 2 else None
                    c2 = struct.unpack_from('<H', data, a - 2)[0] if gap >= 2 else None
                    if x < 0 or c1 != x or c2 != x:
                        self.err('%s: ブロック %d オフセット %d の区切りコード不正' % (who, blk, pos))
                    elif x:
                        free.add((blk, pos + 2, x))
                pos = b
            if 0 < pos < d.blksz - 1:                    # 末尾の空き: [z][空き z]
                z = d.blksz - pos - 2
                if z < 0 or struct.unpack_from('<H', data, pos)[0] != z:
                    self.err('%s: ブロック %d 末尾の区切りコード不正' % (who, blk))
                elif z:
                    free.add((blk, pos + 2, z))
        for size_, blk, off in f.fragments():
            if (blk, off, size_) not in free:
                self.err('%s: フラグメント %d@%d+%d が実際の空き領域と一致しない' % (who, size_, blk, off))


def cmd_check(d, args):
    c = Checker(d, args.verbose)
    c.run()
    for m in c.errors:
        print('ERROR: ' + m)
    for m in c.warnings[:None if args.verbose else 50]:
        print('WARN : ' + m)
    if not args.verbose and len(c.warnings) > 50:
        print('WARN : ... ほか %d 件 (-v で全件)' % (len(c.warnings) - 50))
    print('files %d, blocks in use %d, errors %d, warnings %d' % (
        c.nfiles, c.nblocks, len(c.errors), len(c.warnings)))
    return 1 if c.errors else 0


# ---------------------------------------------------------------------------
# 書き込みコマンド (ブロック割り当てあり) — FileManager を使う
# ---------------------------------------------------------------------------
def split_parent(path):
    """パスを (親パス, 最後の名前) に分ける. 名前は % エスケープを解いた文字列."""
    i, last, esc = 0, -1, False
    for k, ch in enumerate(path.rstrip('/') if not path.endswith('%/') else path):
        if esc:
            esc = False
        elif ch == '%':
            esc = True
        elif ch == '/':
            last = k
    body = path.rstrip('/') if not path.endswith('%/') else path
    parent, name = (body[:last] or '/', body[last + 1:]) if last >= 0 else ('/', body)
    toks = split_path(name)
    if len(toks) != 1:
        raise FormatError('%s: 名前が空です' % path)
    return parent, ''.join(ch for _, ch in toks[0])


def resolve_one(r, spec, what):
    t = r.resolve(spec)
    if len(t) != 1:
        raise FormatError('%s: %s に %d 件一致しました (1 件だけ指定してください)' % (what, spec, len(t)))
    return t[0]


def cli_int(text):
    """CLI の数値形式 (4.1): NNNN 10進, 0NNN 8進, 0xNNNN 16進. 負号も可."""
    t = text.strip()
    neg = t.startswith('-')
    body = t[1:] if neg else t
    try:
        if body[:2].lower() == '0x':
            v = int(body[2:], 16)
        elif len(body) > 1 and body[0] == '0':
            v = int(body[1:], 8)
        else:
            v = int(body, 10)
    except ValueError:
        raise ValueError('数値の形式が不正です: %s' % text)
    return -v if neg else v


def parse_pair(spec, what, default2=None):
    parts = spec.split('.')
    try:
        a = cli_int(parts[0])
        b = cli_int(parts[1]) if len(parts) > 1 and parts[1] != '' else default2
    except ValueError:
        raise FormatError('%s の指定が不正です: %s' % (what, spec))
    return a, b


class Session:
    """コマンド 1 回分: FileManager とパス解決を持ち、最後に必ず sync する."""
    def __init__(self, d):
        self.d = d
        self.fm = FileManager(d)
        self.r = Resolver(self.fm)

    def fresh(self):
        self.r = Resolver(self.fm)         # レコード番号が変わるので解決結果を作り直す
        return self.r


def run_session(d, body):
    sess = Session(d)
    try:
        return body(sess)
    finally:
        sess.fm.sync()


def cmd_mkf(d, args):
    def body(s):
        for path in args.paths:
            parent, name = split_parent(path)
            _, pf = resolve_one(s.fresh(), parent, 'mkf')
            fid = s.fm.cre_fil(pf.fid, name, args.atype)
            if args.verbose:
                print('%s (fid %d)' % (path, fid))
    return run_session(d, body)


def _rm_targets(s, args):
    """削除対象: [(親 fid or None, fid, 表示名)]. 同じ親の中は後ろのレコードから."""
    out = []
    for spec in args.paths:
        for p, f in s.r.resolve(spec):
            parent, recno = s.r.link_pos[p] or (None, None)
            out.append((parent, f.fid, recno, p))
    out.sort(key=lambda x: (x[0] is None, x[0] or 0, -(x[2] or 0)))
    return out


def cmd_rm(d, args):
    def body(s):
        for parent, fid, recno, name in _rm_targets(s, args):
            if parent is None:
                if s.fm.refcount(fid):
                    raise FormatError('%s: 参照カウントが 0 でないため削除できません' % name)
                removed = s.fm._delete_file(fid, args.recursive)
            else:
                rec = s.fm.file(parent).records()[recno]
                if rec['kind'] != 'link' or rec['link_fid'] != fid:
                    raise FormatError('%s: リンクレコードの位置が変わりました' % name)
                removed = s.fm.del_fil(parent, recno, args.recursive)
            if args.verbose:
                print('%s: リンク削除%s' % (name, ', ファイル削除 %d 個' % len(removed) if removed else ''))
    return run_session(d, body)


def cmd_empf(d, args):
    def body(s):
        for _, f in s.r.targets(args):
            fid = f.fid
            while s.fm.file(fid).records():
                s.fm.del_rec(fid, len(s.fm.file(fid).records()) - 1, delete_unref=True)
    return run_session(d, body)


def cmd_ln(d, args):
    def body(s):
        *srcs, dest = args.paths
        if not srcs:
            raise FormatError('ln: リンク先パスとリンクするファイルを指定してください')
        _, df = resolve_one(s.r, dest, 'ln')
        attrs = tuple(cli_int(x) for x in args.attrs.split('.')) if args.attrs else None
        if attrs is not None and len(attrs) != 5:
            raise FormatError('ln: -a は 属性1.属性2.属性3.属性4.属性5 です')
        pos = args.pos
        for spec in srcs:
            for p, f in s.r.resolve(spec):
                # -a が無ければ、たどったリンクの属性を引き継ぐ (CLI と同じ). ファイル ID 指定なら 0
                la = attrs or s.r.link_attrs.get(p) or (0, 0, 0, 0, 0)
                s.fm.ins_rec(df.fid, pos, 0, 0, link=(f.fid, la))
                if pos is not None:
                    pos += 1
    return run_session(d, body)


def _copy_records(s, src_fid, dst_fid, pos, recs, override=None, recursive=False, mapping=None):
    """src の全レコード (recs = rec_list) を dst の pos から順に挿入する."""
    recs = [dict(r, _recno=i) for i, r in enumerate(recs)]
    items = []
    for r in recs:
        if r['kind'] == 'link':
            items.append(('link', r))
        else:
            items.append(('data', r, s.fm.rea_rec(src_fid, r['_recno'])))
    for it in items:
        r = it[1]
        if it[0] == 'link':
            target = r['link_fid']
            if recursive:
                target = _copy_tree(s, target, mapping)
            s.fm.ins_rec(dst_fid, pos, 0, r['subtype'], link=(target, r['attrs']))
        else:
            t, st = (r['type'], r['subtype'])
            if override:
                t = override[0]
                st = override[1] if override[1] is not None else st
            s.fm.ins_rec(dst_fid, pos, t, st, it[2])
        if pos is not None:
            pos += 1


def _copy_tree(s, fid, mapping):
    if fid in mapping:
        return mapping[fid]
    f = s.fm.file(fid)
    new = s.fm.copy_header(fid) if f.ftype == 0 else s.fm.cre_fil(None, f.name, f.apptype)
    mapping[fid] = new
    if f.ftype != 0:
        _copy_records(s, fid, new, None, s.fm.rec_list(fid), recursive=True, mapping=mapping)
    return new


def cmd_cp(d, args):
    def body(s):
        *srcs, dest = args.paths
        if not srcs:
            raise FormatError('cp: 複製するファイルと複製先を指定してください')
        _, df = resolve_one(s.r, dest, 'cp')
        mapping = {}
        for spec in srcs:
            for p, f in s.r.resolve(spec):
                la = s.r.link_attrs.get(p) or (0, 0, 0, 0, 0)
                if args.recursive:
                    new = _copy_tree(s, f.fid, mapping)
                else:
                    new = s.fm.copy_header(f.fid) if f.ftype == 0 else s.fm.cre_fil(None, f.name, f.apptype)
                    if f.ftype != 0:
                        _copy_records(s, f.fid, new, None, s.fm.rec_list(f.fid))
                s.fm.apd_rec(df.fid, 0, 0, link=(new, la))
                if args.verbose:
                    print('%s -> %s (fid %d)' % (p, dest, new))
    return run_session(d, body)


def cmd_rcp(d, args):
    def body(s):
        _, sf = resolve_one(s.r, args.src, 'rcp')
        try:
            _, df = resolve_one(s.r, args.dest, 'rcp')
        except FormatError:
            if FID_PATH.match(args.dest) and '/' not in args.dest[1:]:
                raise
            parent, name = split_parent(args.dest)
            _, pf = resolve_one(s.r, parent, 'rcp')
            df = s.fm.file(s.fm.cre_fil(pf.fid, name, sf.apptype))
        if df.fid == sf.fid:
            raise FormatError('rcp: 複製元と複製先が同じです')
        while s.fm.file(df.fid).records():
            s.fm.del_rec(df.fid, len(s.fm.file(df.fid).records()) - 1, delete_unref=True)
        _copy_records(s, sf.fid, df.fid, None, s.fm.rec_list(sf.fid),
                      recursive=args.recursive, mapping={})
    return run_session(d, body)


def read_stdin():
    """標準入力を最後まで読む. 端末・空・読み込みエラーは FormatError (何も変更しないため)."""
    if sys.stdin is None or sys.stdin.isatty():
        raise FormatError('標準入力が端末です. ファイルをリダイレクトするかパイプで渡してください')
    try:
        data = sys.stdin.buffer.read()
    except OSError as e:
        raise FormatError('標準入力を読めませんでした: %s' % e)
    if not data:
        raise FormatError('標準入力が空です (何も変更しません)')
    return data


def cmd_apd(d, args):
    def body(s):
        _, f1 = resolve_one(s.r, args.path1, 'apd')
        n1 = len(f1.records())
        pos = args.pos
        dstart = dcnt = None
        if args.delete:
            dstart, dcnt = parse_pair(args.delete, '-d', 1)
            if not 0 <= dstart < n1 or dcnt < 1 or dstart + dcnt > n1:
                raise FormatError('apd: 削除範囲が不正です')
            pos = dstart
        override = parse_pair(args.type, '-t') if args.type else None
        if override and not 1 <= override[0] <= 31:
            raise FormatError('apd: -t のレコードタイプは 1〜31 です')
        recs = []
        if args.path2 == '-':
            # 標準入力から 1 レコード (既定はタイプ 31・サブタイプ 0). 読み切るまで何も変更しない
            if override:
                t, st = override
            elif dcnt and f1.records()[dstart]['kind'] == 'data':     # 差し替え: 元のタイプを引き継ぐ
                t, st = f1.records()[dstart]['type'], f1.records()[dstart]['subtype']
            else:
                t, st = 31, 0
            recs = [{'kind': 'data', 'type': t, 'subtype': st if st is not None else 0,
                     '_data': read_stdin()}]
            override = None
        elif args.path2:
            _, f2 = resolve_one(s.r, args.path2, 'apd')
            recs = [dict(r, _recno=i) for i, r in enumerate(s.fm.rec_list(f2.fid))]
            if args.range:
                a, cnt = parse_pair(args.range, '-r', 1)
                if not 0 <= a < len(recs):
                    raise FormatError('apd: 複製開始レコード %d は範囲外です' % a)
                recs = recs[a:a + cnt]
            if override and any(r['kind'] == 'link' for r in recs):
                raise FormatError('apd: リンクレコードには -t を指定できません')
            # 複製元のデータを削除前に読んでおく (同じファイル同士でも安全)
            recs = [dict(r, _data=s.fm.rea_rec(f2.fid, r['_recno'])) if r['kind'] == 'data' else r
                    for r in recs]
        elif not args.delete:
            raise FormatError('apd: 複製元のファイル (PATH2、標準入力は -) または -d を指定してください')
        if pos is not None and not 0 <= pos <= n1 - (dcnt or 0):
            raise FormatError('apd: 挿入位置 %d は範囲外です' % pos)
        if dcnt:
            for _ in range(dcnt):
                s.fm.del_rec(f1.fid, dstart)
        for r in recs:
            if r['kind'] == 'link':
                s.fm.ins_rec(f1.fid, pos, 0, r['subtype'], link=(r['link_fid'], r['attrs']))
            else:
                t = override[0] if override else r['type']
                st = override[1] if override and override[1] is not None else r['subtype']
                s.fm.ins_rec(f1.fid, pos, t, st, r['_data'])
            if pos is not None:
                pos += 1
    return run_session(d, body)


def cmd_recv(d, args):
    def body(s):
        *hosts, dest = args.paths
        if not hosts:
            raise FormatError('recv: 取り込むファイルと取り込み先を指定してください')
        _, df = resolve_one(s.r, dest, 'recv')
        rtype, sub = parse_pair(args.type, '-t', 0) if args.type else (31, 0)
        for h in hosts:
            data = open(h, 'rb').read()
            if args.strip_eof and data.endswith(b'\x1a'):
                data = data[:-1]
            name = args.name or os.path.basename(h)
            for disp, n, _, fid, _, recno in s.fresh().children(s.fm.file(df.fid)):
                if norm_name(n) == norm_name(tron_str(tron_encode(name))) and args.replace:
                    s.fm.del_fil(df.fid, recno, True)
                    break
                if norm_name(n) == norm_name(tron_str(tron_encode(name))):
                    raise FormatError('recv: %s は既に存在します (-d で置き換え)' % disp)
            fid = s.fm.cre_fil(df.fid, name, args.atype)
            s.fm.apd_rec(fid, rtype, sub, data)
            if args.verbose:
                print('%s -> fid %d (%d バイト)' % (h, fid, len(data)))
    return run_session(d, body)


def find_garbage(d):
    """ルートから到達できないファイル: (到達可能集合, くずファイル一覧)."""
    fs = FileManager(d)
    seen, stack = {0}, [0]
    while stack:
        for _, c, _ in fs.fnd_lnk(stack.pop()):
            if c not in seen and d.fid_entry(c)[0]:
                seen.add(c)
                stack.append(c)
    fidt = d._read(d.block_offset(d.fidt_blk), d.nfmax * 4)
    garbage = [fid for fid in range(d.nfmax)
               if fidt[fid * 4:fid * 4 + 4] != b'\0\0\0\0' and fid not in seen]
    return seen, garbage


def cmd_cleanup(d, args):
    seen, garbage = find_garbage(d)
    if args.verbose or args.dry_run:
        for fid in garbage:
            print('fid %d %s' % (fid, FileManager(d).fil_sts(fid).name))
    if args.dry_run:
        print('くずファイル %d 個' % len(garbage))
        return

    def body(s):
        for fid in garbage:             # 到達可能なファイルへのリンクだけ参照カウントを戻す
            for _, c, _ in s.fm.file(fid).links():
                if c in seen:
                    s.fm._set_ref(c, s.fm.refcount(c) - 1)
        for fid in garbage:
            f = s.fm.file(fid)
            blocks = {f.blk} | {b for r in f.records() if r['kind'] == 'data'
                                for b0, c in r['extents'] for b in range(b0, b0 + c)}
            s.fm.free(sorted(blocks))
            s.fm._fid_write(fid, 0, 0)
            d.set_short_name(fid, 0)
            s.fm.cache.pop(fid, None)
        print('くずファイル %d 個を削除しました' % len(garbage))
    return run_session(d, body)


# ---------------------------------------------------------------------------
# アプリケーション登録 (CLI の apreg / apdel)
#   実機で確認した動作:
#   - 機能付箋 (タイプ 7) 1 つごとに /$$PROGRAM.BOX の末尾へリンクを追加する.
#     リンク属性は (0x8000, 0, 付箋 +0x18 のアプリケーション ID 3 語)
#   - $$PROGRAM.BOX のレコード 0 (タイプ 15) はリンク順に並んだ 32 バイトの分類名の表で、
#     付箋 +0x3E の分類名を書く. 削除時は後ろを 1 つ詰める (末尾の 1 件は残る)
#   - -t (小物) のときは付箋レコードを /SYS/USR/小物入れ (無ければ /小物入れ) の末尾にも追加する
#   - apdel は該当リンクと、同じアプリケーション ID の付箋を小物入れから削除し、
#     参照されなくなったファイルは削除する
# ---------------------------------------------------------------------------
FUSEN_APPL, FUSEN_CLASS = 0x18, 0x3E


def _root_child(s, name):
    try:
        return resolve_one(s.fresh(), '/' + name, 'apreg')[1]
    except FormatError:
        return None


def _kobako(s):
    for p in ('/USR/小物入れ', '/小物入れ'):
        try:
            return resolve_one(s.fresh(), p, 'apreg')[1]
        except FormatError:
            pass
    return None


def _class_table(s, pb):
    recs = s.fm.rec_list(pb.fid)
    if not recs or recs[0]['kind'] != 'data' or recs[0]['type'] != 15:
        raise FormatError('$$PROGRAM.BOX のレコード 0 が分類名の表 (タイプ 15) ではありません')
    return bytearray(s.fm.rea_rec(pb.fid, 0))


def cmd_apreg(d, args):
    def body(s):
        _, f = resolve_one(s.r, args.path, 'apreg')
        recs = s.fm.rec_list(f.fid)
        fusens = [s.fm.rea_rec(f.fid, i) for i, r in enumerate(recs) if r['kind'] == 'data' and r['type'] == 7]
        fsub = [r['subtype'] for r in recs if r['kind'] == 'data' and r['type'] == 7]
        if not f.apptype & 0x8000:
            raise FormatError('apreg: アプリケーションタイプが実行可能 (MSB=1) ではありません')
        if not recs or recs[0]['kind'] != 'data' or recs[0]['type'] != 9:
            raise FormatError('apreg: 先頭レコードが実行オブジェクトレコード (タイプ 9) ではありません')
        if not fusens:
            raise FormatError('apreg: 機能付箋レコード (タイプ 7) がありません')
        if any(len(x) < FUSEN_CLASS + 2 for x in fusens):
            raise FormatError('apreg: 機能付箋レコードが短すぎます')
        pb = _root_child(s, '$$PROGRAM.BOX')
        if pb is None:
            raise FormatError('apreg: /$$PROGRAM.BOX がありません')
        kb = _kobako(s) if args.accessory else None
        if args.accessory and kb is None:
            raise FormatError('apreg: 小物入れがありません')
        table = _class_table(s, pb)
        for fu in fusens:
            appl = struct.unpack_from('<3H', fu, FUSEN_APPL)
            idx = len(s.fm.fnd_lnk(pb.fid))
            s.fm.apd_rec(pb.fid, 0, 0, link=(f.fid, (0x8000, 0) + appl))
            cls = fu[FUSEN_CLASS:FUSEN_CLASS + 32]
            n = 0
            while n + 1 < len(cls) and cls[n:n + 2] != b'\0\0':
                n += 2
            if len(table) < (idx + 1) * 32:
                table += bytes((idx + 1) * 32 - len(table))
            table[idx * 32:idx * 32 + 32] = cls[:n].ljust(32, b'\0')
        s.fm.wri_rec(pb.fid, 0, bytes(table))
        if kb is not None:
            for fu, st in zip(fusens, fsub):
                s.fm.apd_rec(kb.fid, 7, st, fu)
    return run_session(d, body)


def cmd_apdel(d, args):
    def body(s):
        try:
            _, f = resolve_one(s.r, args.path, 'apdel')
        except FormatError:
            if args.force:
                return
            raise
        pb = _root_child(s, '$$PROGRAM.BOX')
        if pb is None:
            raise FormatError('apdel: /$$PROGRAM.BOX がありません')
        recs = s.fm.rec_list(f.fid)
        appls = {struct.unpack_from('<3H', s.fm.rea_rec(f.fid, i), FUSEN_APPL)
                 for i, r in enumerate(recs) if r['kind'] == 'data' and r['type'] == 7}
        links = s.fm.fnd_lnk(pb.fid)
        hits = [(k, recno) for k, (recno, lfid, _) in enumerate(links) if lfid == f.fid]
        if not hits:
            raise FormatError('apdel: %s はアプリケーションとして登録されていません' % args.path)
        table = _class_table(s, pb)
        for k, recno in reversed(hits):
            n = len(table) // 32
            if k < n - 1:
                table[k * 32:(n - 1) * 32] = table[(k + 1) * 32:n * 32]
            s.fm.del_rec(pb.fid, recno)
        s.fm.wri_rec(pb.fid, 0, bytes(table))
        kb = _kobako(s)
        if kb is not None:
            krecs = s.fm.rec_list(kb.fid)
            for i in reversed(range(len(krecs))):
                r = krecs[i]
                if r['kind'] == 'data' and r['type'] == 7 and r['size'] >= FUSEN_APPL + 6 and \
                        struct.unpack_from('<3H', s.fm.rea_rec(kb.fid, i), FUSEN_APPL) in appls:
                    s.fm.del_rec(kb.fid, i)
        if s.fm.refcount(f.fid) == 0:
            s.fm._delete_file(f.fid, False)
    return run_session(d, body)


# ---------------------------------------------------------------------------
# 書き込みコマンド (ファイルヘッダのみ変更)
# ---------------------------------------------------------------------------
H_TYPE, H_GRPACC, H_PUBACC, H_LTIME, H_ATIME, H_MTIME, H_NAME = 4, 64, 66, 92, 96, 100, 108


def one_target(r, args, what):
    t = r.targets(args)
    if len(t) != 1:
        raise FormatError('%s: ファイルを 1 つだけ指定してください (%d 件一致)' % (what, len(t)))
    return t[0]


def cmd_ren(d, args):
    r = Resolver(d)
    args.paths = [args.path]
    tpath, f = one_target(r, args, 'ren')
    r.fs.chg_fnm(f.fid, args.name)
    if args.verbose:
        print('%s -> %s' % (tpath, r.fs.fil_sts(f.fid).name))


def cmd_chmod(d, args):
    r = Resolver(d)
    targets = r.targets(args)
    if args.ngroup is not None:
        raise FormatError('chmod: -n (所有グループ) には未対応です')
    header_done = False
    for tpath, f in targets:
        if any(v is not None for v in (args.owner, args.group, args.public)):
            r.fs.chg_fmd(f.fid, args.owner, args.group, args.public)
        if args.attr is not None:
            r.fs.chg_fat(f.fid, args.attr)
        f = r.fs.fil_sts(f.fid)
        if not header_done:
            print('FNAME                  ATTR OWN GROUP        PUBLIC       GRPN')
            header_done = True
        g, p = f.acc_levels
        m = f.owner_mode
        lv = lambda v: 'R%2d W%2d E%2d' % (v >> 8 & 15, v >> 4 & 15, v & 15)
        attr = ''.join(c if f.attr & bit else '-' for c, bit in (('P', 0x20), ('R', 0x10), ('1', 0x40), ('2', 0x80)))
        own = ''.join(c if m & bit else '-' for c, bit in (('R', 4), ('W', 2), ('E', 1)))
        print('%-22s %s %s %s  %s  %-12s' % (disp_name(f.name), attr, own, lv(g), lv(p), ''))


def cmd_chtime(d, args):
    r = Resolver(d)
    times = [parse_btime(x) if x is not None else None for x in (args.ltime, args.atime, args.mtime)]
    for tpath, f in r.targets(args):
        r.fs.chg_ftm(f.fid, *times)
        f = r.fs.fil_sts(f.fid)
        print('%s  limit=%s access=%s modify=%s' % (tpath, fmt_time(f.t_keep), fmt_time(f.t_acc),
                                                    fmt_time(f.t_upd)))


def cmd_touch(d, args):
    r = Resolver(d)
    t = now_btron()
    for tpath, f in r.targets(args):
        r.fs.chg_ftm(f.fid, atime=t, mtime=t)


def hexdump(data, half, out):
    """CLI の tp -x (ハーフワード, 8 個/行) / tp -b (バイト, 16 個/行) と同じ形式."""
    for off in range(0, len(data), 16):
        chunk = data[off:off + 16]
        if half:
            chunk = chunk[:len(chunk) & ~1]      # 端数の 1 バイトは表示しない (CLI と同じ)
            if not chunk:
                break
            cells = ' '.join('%04x' % struct.unpack_from('<H', chunk, k)[0] for k in range(0, len(chunk), 2))
        else:
            cells = ' '.join('%02x' % b for b in chunk)
        out.write('%04x: %s\n' % (off, cells))


def cmd_tp(d, args):
    r = Resolver(d)
    targets = r.targets(args)
    if len(targets) != 1:
        raise FormatError('tp: ファイルを 1 つだけ指定してください (%d 件一致)' % len(targets))
    f = targets[0][1]
    data = r.fs.rea_rec(f.fid, args.record)
    if not isinstance(data, bytes):
        raise FormatError('tp: レコード %d はリンクレコードです' % args.record)
    if args.endian:
        b = bytearray(data)
        b[0:len(b) & ~1:2], b[1:len(b) & ~1:2] = b[1:len(b) & ~1:2], b[0:len(b) & ~1:2]
        data = bytes(b)
    out = sys.stdout
    if args.mode == 'x' or args.mode == 'b':
        hexdump(data, args.mode == 'x', out)
    elif args.mode == 'a':
        out.write(data.decode('euc-jp', 'replace'))
    else:
        if sys.stdout.isatty():
            raise FormatError('tp: 端末へは生データを出力しません. '
                              'リダイレクトするか -a / -x / -b を指定してください')
        sys.stdout.buffer.write(data)


def main():
    ap = argparse.ArgumentParser(prog='bfsk', description='BTRON ディスクイメージ操作ツール')
    g = ap.add_mutually_exclusive_group()
    g.add_argument('-p', '--partition', type=int, help='MBR パーティション番号 (1-4, 省略時は自動検出)')
    g.add_argument('-n', '--fsname', help='ファイルシステム名で選択 (例: "B-right/V")')
    ap.add_argument('image')
    sub = ap.add_subparsers(dest='cmd', required=True, metavar='COMMAND')

    def path_cmd(name, help_, nargs='*'):
        p = sub.add_parser(name, help=help_)
        p.add_argument('paths', nargs=nargs, metavar='PATH')
        return p

    sub.add_parser('info', help='イメージ / ファイルシステムの詳細')
    sub.add_parser('df', help='ディスク状態を表示')
    p = path_cmd('ls', 'ファイル一覧')
    p.add_argument('-f', dest='self_', action='store_true', help='PATH のファイル自体の情報を表示')
    p.add_argument('-F', dest='classify', action='store_true', help='リンクを含むと "/", 多重参照は "#" を付加')
    m = p.add_mutually_exclusive_group()
    m.add_argument('-l', dest='long', action='store_true', help='詳細情報')
    m.add_argument('-t', dest='time', action='store_true', help='日時情報')
    p = path_cmd('fs', 'レコード構成')
    p.add_argument('-l', dest='long', action='store_true', help='詳細表示 (リンクのファイル ID と属性)')
    p.add_argument('-b', dest='blocks', action='store_true', help='データの配置ブロックも表示 (bfsk 独自)')
    path_cmd('stat', 'ファイルヘッダの詳細')
    p = path_cmd('tp', 'レコード内容 (既定は生データを標準出力へ)', nargs=1)
    p.add_argument('-r', dest='record', type=cli_int, default=0, help='レコード番号 (既定 0)')
    p.add_argument('-e', dest='endian', action='store_true', help='エンディアン変換 (2 バイト単位で入れ替え)')
    m = p.add_mutually_exclusive_group()
    m.add_argument('-a', dest='mode', action='store_const', const='a', help='EUC 文字列として表示')
    m.add_argument('-x', dest='mode', action='store_const', const='x', help='16 進ダンプ (ハーフワード)')
    m.add_argument('-b', dest='mode', action='store_const', const='b', help='16 進ダンプ (バイト)')
    p = sub.add_parser('check', help='整合性チェック (読み取りのみ)')
    p.add_argument('-v', dest='verbose', action='store_true', help='警告を全件表示')
    p = sub.add_parser('ren', help='ファイル名の変更 (書き込み)')
    p.add_argument('-v', dest='verbose', action='store_true', help='変更結果を表示')
    p.add_argument('path', metavar='PATH')
    p.add_argument('name', metavar='NAME')
    num = cli_int
    p = path_cmd('chmod', 'アクセスモード / 属性の表示・変更 (書き込み)', nargs='+')
    p.add_argument('-o', dest='owner', type=num, help='所有者アクセスモード 0〜7 (RWE)')
    p.add_argument('-g', dest='group', type=num, help='グループアクセスレベル 0x0RWE')
    p.add_argument('-p', dest='public', type=num, help='一般アクセスレベル 0x0RWE')
    p.add_argument('-n', dest='ngroup', type=num, help='所有グループ (未対応)')
    p.add_argument('-a', dest='attr', type=num, help='1/2: 書込不可 セット/リセット, 3/4: 削除不可 セット/リセット')
    p = path_cmd('chtime', '日時の表示・設定 (書き込み)', nargs='+')
    p.add_argument('-l', dest='ltime', metavar='Y.M.D.h.m.s', help='保存期限 (-1 で無効)')
    p.add_argument('-a', dest='atime', metavar='Y.M.D.h.m.s', help='最新アクセス日時')
    p.add_argument('-m', dest='mtime', metavar='Y.M.D.h.m.s', help='最新更新日時')
    path_cmd('touch', '更新日時・アクセス日時を現在に (書き込み)', nargs='+')
    p = path_cmd('mkf', 'ファイル生成 (書き込み)', nargs='+')
    p.add_argument('-t', dest='atype', type=num, default=0, help='アプリケーションタイプ (既定 0)')
    p.add_argument('-v', dest='verbose', action='store_true')
    p = path_cmd('rm', 'ファイル (へのリンク) 削除 (書き込み)', nargs='+')
    p.add_argument('-r', dest='recursive', action='store_true', help='含まれるリンク先も参照 0 になれば削除')
    p.add_argument('-v', dest='verbose', action='store_true')
    path_cmd('empf', 'ファイル内のレコードをすべて削除 (書き込み)', nargs='+')
    p = path_cmd('ln', 'リンク追加: ln PATH.. DEST (書き込み)', nargs='+')
    p.add_argument('-p', dest='pos', type=num, help='挿入レコード位置 (既定: 最後)')
    p.add_argument('-a', dest='attrs', metavar='A1.A2.A3.A4.A5', help='リンク属性データ (既定 0)')
    p = path_cmd('cp', 'ファイル複製: cp PATH.. DEST (書き込み)', nargs='+')
    p.add_argument('-r', dest='recursive', action='store_true', help='リンク先のファイルも複製')
    p.add_argument('-v', dest='verbose', action='store_true')
    p = sub.add_parser('rcp', help='名前を付けて複製 / 内容の置き換え: rcp SRC NEWPATH (書き込み)')
    p.add_argument('-r', dest='recursive', action='store_true', help='リンク先のファイルも複製')
    p.add_argument('src', metavar='SRC')
    p.add_argument('dest', metavar='NEWPATH')
    p = sub.add_parser('apd', help='レコードの挿入 / 追加 / 削除 (書き込み)')
    p.add_argument('-p', dest='pos', type=num, help='PATH1 の挿入位置 (既定: 最後)')
    p.add_argument('-d', dest='delete', metavar='N.C', help='PATH1 のレコード N から C 個削除 (その位置に挿入)')
    p.add_argument('-r', dest='range', metavar='N.C', help='PATH2 のレコード N から C 個を複製 (既定: 全部)')
    p.add_argument('-t', dest='type', metavar='T.S', help='複製後のレコードタイプ.サブタイプ')
    p.add_argument('path1', metavar='PATH1')
    p.add_argument('path2', metavar='PATH2', nargs='?', help='複製元. "-" は標準入力 (既定タイプ 31.0)')
    p = path_cmd('recv', 'Linux のファイルを取り込む: recv HOSTFILE.. DEST (書き込み)', nargs='+')
    p.add_argument('-d', dest='replace', action='store_true', help='同名ファイルがあれば削除して置き換え')
    p.add_argument('-c', dest='strip_eof', action='store_true', help='最後の Ctrl-Z を削除')
    p.add_argument('-t', dest='type', metavar='T.S', help='レコードタイプ.サブタイプ (既定 31.0)')
    p.add_argument('-a', dest='atype', type=num, default=0, help='アプリケーションタイプ (既定 0)')
    p.add_argument('-N', dest='name', help='ファイル名 (既定: Linux 側のファイル名)')
    p.add_argument('-v', dest='verbose', action='store_true')
    p = sub.add_parser('apreg', help='アプリケーション登録 (書き込み)')
    p.add_argument('-t', dest='accessory', action='store_true', help='「小物」メニューへも登録')
    p.add_argument('path', metavar='PATH')
    p = sub.add_parser('apdel', help='アプリケーション登録の削除 (書き込み)')
    p.add_argument('-f', dest='force', action='store_true', help='PATH が無くてもエラーにしない')
    p.add_argument('path', metavar='PATH')
    p = sub.add_parser('cleanup', help='ルートから到達できないファイルを削除 (書き込み)')
    p.add_argument('-n', dest='dry_run', action='store_true', help='表示のみ')
    p.add_argument('-v', dest='verbose', action='store_true')
    args = ap.parse_args()
    writes = {'ren', 'touch', 'mkf', 'rm', 'empf', 'ln', 'cp', 'rcp', 'apd', 'recv', 'apreg', 'apdel'} | (
        {'cleanup'} if args.cmd == 'cleanup' and not args.dry_run else set()) | ({'chmod'} if args.cmd == 'chmod' and any(
        getattr(args, k) is not None for k in ('owner', 'group', 'public', 'attr')) else set()) | (
        {'chtime'} if args.cmd == 'chtime' and any(
            getattr(args, k) is not None for k in ('ltime', 'atime', 'mtime')) else set())
    try:
        d = Disk(args.image, args.partition, args.fsname, writable=args.cmd in writes)
        rc = {'info': cmd_info, 'df': cmd_df, 'check': cmd_check, 'ls': cmd_ls, 'fs': cmd_fs, 'stat': cmd_stat, 'tp': cmd_tp,
         'ren': cmd_ren, 'chmod': cmd_chmod, 'chtime': cmd_chtime, 'touch': cmd_touch,
         'mkf': cmd_mkf, 'rm': cmd_rm, 'empf': cmd_empf, 'ln': cmd_ln, 'cp': cmd_cp, 'rcp': cmd_rcp,
         'apd': cmd_apd, 'recv': cmd_recv, 'apreg': cmd_apreg, 'apdel': cmd_apdel, 'cleanup': cmd_cleanup}[args.cmd](d, args)
        if rc:
            return rc
    except FormatError as e:
        print('error: %s' % e, file=sys.stderr)
        return 1
    except BrokenPipeError:
        return 0
    return 0


if __name__ == '__main__':
    sys.exit(main())
