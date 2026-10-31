#!/usr/bin/env python3
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
  書き込み (ファイルヘッダのみ変更):
    ren PATH NAME                ファイル名の変更
    chmod [-o#][-g#][-p#][-a#] PATH..   アクセスモード / 属性
    chtime [-l..][-a..][-m..] PATH..    日時 (年.月.日.時.分.秒)
    touch PATH..                 更新日時・アクセス日時を現在に
    tp [-r#][-a|-x|-b][-e] PATH  レコード内容 (既定は生データを標準出力へ)
  PATH の各コマンドで -i を付けると PATH の代わりにファイルID を指定できる.
"""
import argparse
import datetime
import fnmatch
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
                raise FormatError('%s の未割り当て領域への書き込みには未対応です (0x%x)' % (self.kind, off + pos))
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
        self._gt = {}

    def _lookup(self, g):
        t, j = divmod(g, self.ngte)
        if t >= len(self.gd) or self.gd[t] == 0:
            return None
        gt = self._gt.get(t)
        if gt is None:
            self.f.seek(self.gd[t] * 512)
            gt = self._gt[t] = struct.unpack('<%dI' % self.ngte, self.f.read(self.ngte * 4))
        s = gt[j]
        return None if s <= 1 else s * 512     # 0: 未割当, 1: ゼログレイン


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
        f.seek(off_bmap)
        self.bmap = struct.unpack('<%dI' % nblk, f.read(nblk * 4))

    def _lookup(self, b):
        if b >= len(self.bmap):
            return None
        v = self.bmap[b]
        if v >= 0xFFFFFFFE:                      # 未割当 / ゼロブロック
            return None
        return self.off_data + v * self.bsize


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
        # ※ level>0 のファイルは手元のサンプルに無く未検証
        for off in range(start, len(block) - 7, 8):
            cnt, addr = struct.unpack_from('<2I', block, off)
            if addr == 0:
                continue
            child = self.disk.read_block(addr)
            if level == 1:
                yield from self._slots(child, 0)
            else:
                yield from self._walk_indirect(child, 0, level - 1)

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
    print('image format     : %s' % d.img.kind)
    print('partition offset : 0x%x' % d.base)
    print('boot area size   : %d' % d.boot_size)
    print('disk id / format : 0x%04x / 0x%04x' % (d.disk_id, d.fmt_id))
    print('block size       : %d' % d.blksz)
    print('blocks total/free: %d / %d' % (d.nlb, d.nfree))
    print('max files        : %d' % d.nfmax)
    print('fs name          : %s' % d.fsname)
    print('device           : %s' % d.devname)
    print('created / updated: %s / %s' % (fmt_time(d.t_gen), fmt_time(d.t_upd)))


# ---------------------------------------------------------------------------
# パス解決
#   /A/B/C     ルートからリンクをたどる (相対パスもルート起点. "." と ".." 可)
#   名前:N     同じ名前の N 番目 (0 起点, ":0" は省略形と同じ)
#   %c         次の 1 文字をそのまま扱う (%*, %:, %/, %% など)
#   *          ワイルドカード
#   全角/半角は区別しない
# ---------------------------------------------------------------------------
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
    def __init__(self, disk):
        self.d = disk
        self._cache = {}
        self.link_attrs = {}            # 解決したパス → そこへ至るリンクの属性データ1〜5

    def file(self, fid):
        f = self._cache.get(fid)
        if f is None:
            f = self._cache[fid] = self.d.open_file(fid)
        return f

    def children(self, f):
        """(表示名, 名前, 出現順, fid, リンク属性) の列. 同名 2 つ目以降は "名前:N"."""
        seen = {}
        out = []
        for _, fid, attrs in f.links():
            try:
                name = self.file(fid).name
            except FormatError:
                name = '?'
            key = norm_name(name)
            n = seen.get(key, 0)
            seen[key] = n + 1
            out.append((name if n == 0 else '%s:%d' % (name, n), name, n, fid, attrs))
        return out

    def resolve(self, path):
        """パスに一致する [(表示パス, BFile)] を返す (ワイルドカードで複数あり得る)."""
        results = [([], [0], None)]     # (表示名リスト, fid スタック, 最後にたどったリンクの属性)
        for toks in split_path(path):
            word = ''.join(ch for _, ch in toks)
            nxt = []
            for names, stack, la in results:
                if word == '.' and all(t == 'lit' for t, _ in toks):
                    nxt.append((names, stack, la))
                    continue
                if word == '..' and all(t == 'lit' for t, _ in toks):
                    nxt.append((names[:-1], stack[:-1] or [0], None))
                    continue
                pat, wild, occ = parse_comp(toks)
                for disp, name, n, fid, attrs in self.children(self.file(stack[-1])):
                    if not fnmatch.fnmatchcase(norm_name(name), pat):
                        continue
                    if occ is not None and n != occ:
                        continue
                    if occ is None and not wild and n != 0:
                        continue
                    nxt.append((names + [disp], stack + [fid], attrs))
            results = nxt
        if not results:
            raise FormatError('%s: ファイルが見つかりません' % path)
        out = []
        for n, st, la in results:
            p = '/' + '/'.join(n)
            self.link_attrs[p] = la
            out.append((p, self.file(st[-1])))
        return out

    def targets(self, args, default='/'):
        """コマンド引数 (パス or -i 指定時はファイルID) を [(表示名, BFile)] に."""
        items = args.paths if getattr(args, 'paths', None) else [default]
        out = []
        for a in items:
            if args.fid:
                try:
                    fid = int(a, 0)
                except ValueError:
                    raise FormatError('%s: ファイルID は数値で指定してください' % a)
                out.append(('#%d' % fid, self.file(fid)))
            else:
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
    total = d.nlb * d.blksz // 1024
    free = d.nfree * d.blksz // 1024
    used = (100 * (d.nlb - d.nfree) // d.nlb) if d.nlb else 0
    print('%12s %12s %4s %6s %7s  %s' % ('TOTAL', 'FREE', 'USED', 'UNIT', 'MAXFILE', 'NAME'))
    print('%12d %12d %3d%% %6d %7d  %s' % (total, free, used, d.blksz, d.nfmax, d.fsname))


def cmd_ls(d, args):
    r = Resolver(d)
    if args.long:
        print('ATYPE ATR   NREC  NLNK  NREF       SIZE    BLK  MTIME                NAME')
    elif args.time:
        print('CTIME                ATIME                MTIME                NAME')
    targets = r.targets(args)
    for tpath, tf in targets:
        if args.self_:
            entries = [(tpath, tf.fid, r.link_attrs.get(tpath))]
        else:
            entries = [(disp, fid, attrs) for disp, _, _, fid, attrs in r.children(tf)]
            if len(targets) > 1:
                print('%s:' % tpath)
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
                print('%04x  %s %5d %5d %5d %10d %6d  %s  %s' % (
                    f.apptype, attr_str(f, lattrs), f.nrec, f.nlink, f.refcount, f.size, f.nblk,
                    fmt_time_s(f.t_upd), name))
            elif args.time:
                print('%s  %s  %s  %s' % (fmt_time_s(f.t_gen), fmt_time_s(f.t_acc),
                                          fmt_time_s(f.t_upd), name))
            else:
                print(name)


def cmd_fs(d, args):
    r = Resolver(d)
    targets = r.targets(args)
    for tpath, f in targets:
        if len(targets) > 1:
            print('%s:' % tpath)
        recs = f.records()
        for i, rec in enumerate(recs):
            if rec['kind'] == 'link':
                try:
                    name = r.file(rec['link_fid']).name
                except FormatError:
                    name = '*?'
                if args.long:
                    print('%3d: 0 %04x : %5d [%s] : %s' % (
                        i, rec['subtype'], rec['link_fid'],
                        ' '.join('%04x' % a for a in rec['attrs']), name))
                else:
                    print('%3d: 0 %04x : %s' % (i, rec['subtype'], name))
            else:
                line = '%3d: %2d %04x : %d' % (i, rec['type'], rec['subtype'], rec['size'])
                if args.long:
                    line += '  [blk %s ofs %d]' % (' '.join('%d+%d' % (a, c) if c > 1 else '%d' % a
                                                           for a, c in rec['extents']) or '-', rec['offset'])
                print(line)
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
    if f.fid == 0:
        raise FormatError('ren: ルートファイル (ファイルシステム名) の変更には未対応です')
    name40 = tron_encode(args.name)
    f.update_header({H_NAME: ('40s', name40), H_ATIME: ('<I', now_btron())})
    d.set_short_name(f.fid, short_name(name40))
    if args.verbose:
        print('%s -> %s' % (tpath, f.name))


def cmd_chmod(d, args):
    r = Resolver(d)
    targets = r.targets(args)
    change = any(v is not None for v in (args.owner, args.group, args.public, args.attr))
    if args.ngroup is not None:
        raise FormatError('chmod: -n (所有グループ) には未対応です')
    for tpath, f in targets:
        if change:
            ftype = (f.ftype << 12) | f.attr
            if args.owner is not None:
                if not 0 <= args.owner <= 7:
                    raise FormatError('chmod: -o は 0〜7 です')
                ftype = (ftype & ~0x7) | args.owner
            if args.attr is not None:
                bit, on = {1: (0x10, True), 2: (0x10, False), 3: (0x20, True), 4: (0x20, False)}.get(args.attr, (None, None))
                if bit is None:
                    raise FormatError('chmod: -a は 1〜4 です')
                ftype = (ftype | bit) if on else (ftype & ~bit)
            fields = {H_TYPE: ('<H', ftype), H_ATIME: ('<I', now_btron())}
            for opt, off in ((args.group, H_GRPACC), (args.public, H_PUBACC)):
                if opt is not None:
                    if not 0 <= opt <= 0xFFF:
                        raise FormatError('chmod: -g / -p は 0x000〜0xFFF です')
                    fields[off] = ('<H', opt)
            f.update_header(fields)
        g, p = f.acc_levels
        m = f.owner_mode
        print('%s  owner=%s%s%s(%d) group=0x%03x public=0x%03x attr=%s%s' % (
            tpath, 'R' if m & 4 else '-', 'W' if m & 2 else '-', 'E' if m & 1 else '-', m, g & 0xFFF,
            p & 0xFFF, 'R' if f.attr & 0x10 else '-', 'P' if f.attr & 0x20 else '-'))


def cmd_chtime(d, args):
    r = Resolver(d)
    fields = {}
    for spec, off in ((args.ltime, H_LTIME), (args.atime, H_ATIME), (args.mtime, H_MTIME)):
        if spec is not None:
            fields[off] = ('<I', parse_btime(spec))
    for tpath, f in r.targets(args):
        if fields:
            f.update_header(fields)
        print('%s  limit=%s access=%s modify=%s' % (tpath, fmt_time(f.t_keep), fmt_time(f.t_acc),
                                                    fmt_time(f.t_upd)))


def cmd_touch(d, args):
    r = Resolver(d)
    t = now_btron()
    for tpath, f in r.targets(args):
        f.update_header({H_ATIME: ('<I', t), H_MTIME: ('<I', t)})


def hexdump(data, half, out):
    for off in range(0, len(data), 16):
        chunk = data[off:off + 16]
        if half:
            cells = ' '.join('%04x' % struct.unpack_from('<H', chunk.ljust(len(chunk) + len(chunk) % 2, b'\0'), k)[0]
                             for k in range(0, len(chunk), 2))
            width = 39
        else:
            cells = ' '.join('%02x' % b for b in chunk)
            width = 47
        txt = ''.join(chr(b) if 0x20 <= b < 0x7F else '.' for b in chunk)
        out.write('%08x  %-*s  %s\n' % (off, width, cells, txt))


def cmd_tp(d, args):
    r = Resolver(d)
    targets = r.targets(args)
    if len(targets) != 1:
        raise FormatError('tp: ファイルを 1 つだけ指定してください (%d 件一致)' % len(targets))
    f = targets[0][1]
    recs = f.records()
    if not 0 <= args.record < len(recs):
        raise FormatError('tp: レコード番号 %d は範囲外です (0..%d)' % (args.record, len(recs) - 1))
    rec = recs[args.record]
    if rec['kind'] == 'link':
        raise FormatError('tp: レコード %d はリンクレコードです' % args.record)
    data = f.read_record(rec)
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
        p.add_argument('-i', '--fid', action='store_true', help='PATH の代わりにファイルID で指定')
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
    p.add_argument('-l', dest='long', action='store_true', help='詳細表示')
    path_cmd('stat', 'ファイルヘッダの詳細')
    p = path_cmd('tp', 'レコード内容 (既定は生データを標準出力へ)', nargs=1)
    p.add_argument('-r', dest='record', type=lambda x: int(x, 0), default=0, help='レコード番号 (既定 0)')
    p.add_argument('-e', dest='endian', action='store_true', help='エンディアン変換 (2 バイト単位で入れ替え)')
    m = p.add_mutually_exclusive_group()
    m.add_argument('-a', dest='mode', action='store_const', const='a', help='EUC 文字列として表示')
    m.add_argument('-x', dest='mode', action='store_const', const='x', help='16 進ダンプ (ハーフワード)')
    m.add_argument('-b', dest='mode', action='store_const', const='b', help='16 進ダンプ (バイト)')
    p = sub.add_parser('ren', help='ファイル名の変更 (書き込み)')
    p.add_argument('-i', '--fid', action='store_true', help='PATH の代わりにファイルID で指定')
    p.add_argument('-v', dest='verbose', action='store_true', help='変更結果を表示')
    p.add_argument('path', metavar='PATH')
    p.add_argument('name', metavar='NAME')
    num = lambda x: int(x, 0)
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
    args = ap.parse_args()
    writes = {'ren', 'touch'} | ({'chmod'} if args.cmd == 'chmod' and any(
        getattr(args, k) is not None for k in ('owner', 'group', 'public', 'attr')) else set()) | (
        {'chtime'} if args.cmd == 'chtime' and any(
            getattr(args, k) is not None for k in ('ltime', 'atime', 'mtime')) else set())
    try:
        d = Disk(args.image, args.partition, args.fsname, writable=args.cmd in writes)
        {'info': cmd_info, 'df': cmd_df, 'ls': cmd_ls, 'fs': cmd_fs, 'stat': cmd_stat, 'tp': cmd_tp,
         'ren': cmd_ren, 'chmod': cmd_chmod, 'chtime': cmd_chtime, 'touch': cmd_touch}[args.cmd](d, args)
    except FormatError as e:
        print('error: %s' % e, file=sys.stderr)
        return 1
    except BrokenPipeError:
        return 0
    return 0


if __name__ == '__main__':
    sys.exit(main())
