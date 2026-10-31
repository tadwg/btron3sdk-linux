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
  - ブロック n のオフセット = パーティション先頭 + 0x1000 + (n-1) * ブロック長
    (ブロック0 = BOOT 領域のみ 4KB)
  - ディスク形式ID 0x6402 は「拡張形式2」(超漢字V R4 仕様差分, 4GB超対応):
    ファイルヘッダ +176 にレコードインデックス開始位置 (ridxofs) があり、
    フラグメントテーブルは 8 バイト (size, offset, blk) × 可変個

使い方:
  bfsk.py [-p N] IMAGE info
  bfsk.py [-p N] IMAGE records FILEID
"""
import argparse
import struct
import sys

SYS_OFFSET = 0x1000          # パーティション先頭からシステムブロックまで
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
    import datetime
    if t in (0, 0xFFFFFFFF):
        return '-' if t == 0 else 'none'
    return datetime.datetime.fromtimestamp(t + EPOCH_1985, datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')


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


def open_image(path):
    """ファイル内容から形式を自動判別して開く."""
    f = open(path, 'rb')
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
    def __init__(self, path, part=None):
        self.img = open_image(path)
        self.base = self._find_partition(part)
        h = self._read(self.base + SYS_OFFSET, 128)
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

    def _find_partition(self, part):
        mbr = self._read(0, 512)
        cands = []
        if mbr[510:512] == b'\x55\xaa':
            for i in range(4):
                e = mbr[0x1BE + 16 * i:0x1CE + 16 * i]
                ptype = e[4]
                lba = struct.unpack_from('<I', e, 8)[0]
                if ptype and lba:
                    cands.append((i + 1, ptype, lba * 512))
        if part is not None:
            for no, _, off in cands:
                if no == part:
                    return off
            raise FormatError('パーティション %d がありません' % part)
        for _, _, off in cands:
            if struct.unpack('<H', self._read(off + SYS_OFFSET, 2))[0] in TRON_DISK_IDS:
                return off
        if struct.unpack('<H', self._read(SYS_OFFSET, 2))[0] in TRON_DISK_IDS:
            return 0                     # パーティション単体イメージ
        raise FormatError('BTRON ファイルシステムが見つかりません')

    def block_offset(self, n):
        if n == 0:
            return self.base
        return self.base + SYS_OFFSET + (n - 1) * self.blksz

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
        self.hblock = disk.read_block(blk)
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

    def records(self):
        """レコードの一覧を返す (dict のリスト)."""
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
        return recs


def cmd_info(d, args):
    print('image format     : %s' % d.img.kind)
    print('partition offset : 0x%x' % d.base)
    print('disk id / format : 0x%04x / 0x%04x' % (d.disk_id, d.fmt_id))
    print('block size       : %d' % d.blksz)
    print('blocks total/free: %d / %d' % (d.nlb, d.nfree))
    print('max files        : %d' % d.nfmax)
    print('fs name          : %s' % d.fsname)
    print('device           : %s' % d.devname)
    print('created / updated: %s / %s' % (fmt_time(d.t_gen), fmt_time(d.t_upd)))


def cmd_records(d, args):
    f = d.open_file(args.fileid)
    recs = f.records()
    print('file id   : %d (header block %d, ref %d)' % (f.fid, f.blk, f.ref))
    print('name      : %s' % f.name)
    print('type/attr : %d / 0x%03x   apptype 0x%04x' % (f.ftype, f.attr, f.apptype))
    print('level     : %d   records %d   links %d   bytes %d   blocks %d'
          % (f.level, f.nrec, f.nlink, f.size, f.nblk))
    print('created   : %s   updated %s' % (fmt_time(f.t_gen), fmt_time(f.t_upd)))
    print('index ofs : %d   fragments: %s' % (f.ridxofs, ' '.join(
        '%d@%d+%d' % fr for fr in f.fragments()) or '-'))
    if f.hdr_fid != f.fid:
        print('warning: ヘッダ内ファイルID %d が一致しません' % f.hdr_fid, file=sys.stderr)
    print()
    print('%4s %-4s %4s %7s %10s %6s  %s' % ('no', 'kind', 'type', 'subtype', 'size', 'offset', 'blocks / link'))
    for i, r in enumerate(recs):
        if r['kind'] == 'link':
            try:
                tgt = d.open_file(r['link_fid']).name
            except FormatError:
                tgt = '?'
            extra = '-> fid %d %s  attr=%s' % (r['link_fid'], tgt, ','.join('%04x' % a for a in r['attrs']))
            print('%4d %-4s %4d %#7x %10s %6s  %s' % (i, 'link', 0, r['subtype'], '-', '-', extra))
        else:
            ext = ' '.join('%d+%d' % (a, c) if c > 1 else '%d' % a for a, c in r['extents']) or '-'
            print('%4d %-4s %4d %#7x %10d %6d  %s' % (i, 'data', r['type'], r['subtype'],
                                                     r['size'], r['offset'], ext))
    if len(recs) != f.nrec:
        print('warning: ヘッダの総レコード数 %d と一覧の件数 %d が一致しません' % (f.nrec, len(recs)),
              file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(prog='bfsk', description='BTRON ディスクイメージ操作ツール')
    ap.add_argument('-p', '--partition', type=int, help='MBR パーティション番号 (1-4, 省略時は自動検出)')
    ap.add_argument('image')
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('info', help='ファイルシステム情報を表示')
    p = sub.add_parser('records', help='ファイルのレコード一覧を表示')
    p.add_argument('fileid', type=int)
    args = ap.parse_args()
    try:
        d = Disk(args.image, args.partition)
        {'info': cmd_info, 'records': cmd_records}[args.cmd](d, args)
    except FormatError as e:
        print('error: %s' % e, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
