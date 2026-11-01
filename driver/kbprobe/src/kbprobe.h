/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * kbprobe.h
 * kbpd key map probe (調査用)
 *
 * tadwg/virtualbox-additions の vboxmousegpl.h をもとに作成.
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 2 of the License, or
 * (at your option) any later version.
 */

#ifndef __KBPROBE_H__
#define __KBPROBE_H__

/* ---- 基本型 ---- */
#include <basic.h>              /* __TYPEDEF_H__ 定義 → 後続の重複防止 */

/* ---- itron カーネル API ---- */
#include <itron/itron.h>
#include <itron/ierrno.h>
#include <itron/ifncode.h>
#include <itron/idebug.h>
#include <itron/istddef.h>
#include <itron/isyscall.h>     /* snd_msg(ID, T_MSG*) */

/* ---- ドライバ用ヘッダ ---- */
#include <driver/kbpd.h>        /* PdInput, PdInput2, DN_KPINPUT, PDIN_XMAX, PDIN_YMAX */
#include <driver/pcat/sys.h>    /* in_w, out_w                         */
#include <kernel/segment.h>     /* ER_OK, ER_NOSPT などの定義を含む      */
#include <driver/hwres.h>
#include <bsys/cons_io.h>
#include <util/debug.h>         /* DEBUG_PRINT */
#include <tstring.h>            /* eucstotcs   */
#include <kernel/util.h>

/* ---- btron/device.h の必要分 ---- */
#ifndef D_READ
#define D_READ  0x01
#endif
extern W  b_opn_dev(TC *name, UINT mode, void *arg);
extern ER b_cls_dev(W dd, UINT mode, void *arg);
extern ER b_rea_dev(W dd, W start, void *buf, W size, W *asize, void *arg);

#endif /* __KBPROBE_H__ */
