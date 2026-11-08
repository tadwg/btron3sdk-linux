/* SPDX-License-Identifier: MIT */
/*
 * mkrecs.c  bfsk の検証用: ファイルにレコードを大量に追加する CLI コマンド
 *
 *   mkrecs FILE COUNT [TYPE [REOPEN]]
 *     FILE の末尾に、レコード番号 (0 から始まる通し番号, 4 バイト, リトルエンディアン) を内容とする
 *     レコードを COUNT 個追加する. レコードタイプは TYPE (既定 16), サブタイプは 0.
 *     既存のレコードがあれば、その個数を初期値として番号を続ける.
 *     REOPEN 個ごとにファイルを閉じて開き直す (既定 100000. 開いたまま数百万個追加すると
 *     システムメモリ不足 (EC_NOSPC) になったため).
 *     インデックスレベル 1 / 2 のファイルを作って bfsk の読み込みを確かめるために使う.
 */
#include <basic.h>
#include <bstdio.h>
#include <tstring.h>
#include <btron/file.h>
#include <btron/event.h>

#define PROGRESS 100000

static W tc2w(TC *s)
{
	W v = 0;
	for (; *s; s++) {
		W c = *s;
		if (c >= 0x2330 && c <= 0x2339) c -= 0x2330 - '0';   /* 全角数字 */
		if (c < '0' || c > '9') return -1;
		v = v * 10 + (c - '0');
	}
	return v;
}

static UW msec(VOID)		/* 経過時間 (ms) */
{
	UW t = 0;
	get_etm(&t);
	return t;
}

EXPORT W main(W argc, TC **argv)
{
	LINK lnk;
	W fd, er, i, count, type, base, reopen;
	UW buf, t0;
	F_STATE st;

	if (argc < 3) {
		printf("usage: mkrecs FILE COUNT [TYPE]\n");
		return 1;
	}
	count = tc2w(argv[2]);
	type = (argc > 3) ? tc2w(argv[3]) : 16;
	reopen = (argc > 4) ? tc2w(argv[4]) : 100000;
	if (count < 0 || type < 1 || type > 31 || reopen < 1) {
		printf("mkrecs: bad COUNT or TYPE\n");
		return 1;
	}
	if ((er = get_lnk(argv[1], &lnk, F_NORM)) < 0) {
		printf("mkrecs: get_lnk %S: %08x\n", argv[1], (UW)er);
		return 1;
	}
	if ((fd = opn_fil(&lnk, F_UPDATE | F_EXCL, NULL)) < 0) {
		printf("mkrecs: opn_fil: %08x\n", (UW)fd);
		return 1;
	}
	base = 0;
	if (ofl_sts(fd, NULL, &st, NULL) >= 0) base = st.f_nrec;
	printf("mkrecs: %S: %d records, append %d (type %d)\n", argv[1], base, count, type);

	t0 = msec();
	for (i = 0; i < count; i++) {
		buf = (UW)(base + i);
		if ((er = apd_rec(fd, (B *)&buf, sizeof(buf), type, 0, 0)) < 0) {
			printf("mkrecs: apd_rec #%d: %08x\n", base + i, (UW)er);
			break;
		}
		if ((i + 1) % reopen == 0) {		/* 閉じて開き直す */
			cls_fil(fd);
			if ((fd = opn_fil(&lnk, F_UPDATE | F_EXCL, NULL)) < 0) {
				printf("mkrecs: reopen: %08x\n", (UW)fd);
				return 1;
			}
		}
		if ((i + 1) % PROGRESS == 0) {
			printf("mkrecs: %d records (%u ms)\n", base + i + 1, msec() - t0);
		}
	}
	if (ofl_sts(fd, NULL, &st, NULL) >= 0) {
		printf("mkrecs: done, %d records in file, %u ms\n", st.f_nrec, msec() - t0);
	}
	cls_fil(fd);
	return (i == count) ? 0 : 1;
}
