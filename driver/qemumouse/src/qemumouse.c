/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * qemumouse.c
 * QEMU (vmmouse) Absolute Mouse Driver for BTRON3
 *
 * QEMU の PC マシンがエミュレートする VMware 互換の「バックドア」
 * (I/O ポート 0x5658) 上の vmmouse を絶対座標モードにして、
 * ホストのマウス座標・ボタン・ホイールを取得し、
 * BTRON3 の kbpd ドライバにポインタイベントとして渡す。
 *
 * PCI デバイスや DMA は使わない. I/O 命令 (inl) だけで動作する.
 * QEMU 側は既定 (-machine pc の vmport=on) のままでよい.
 *
 * 参考:
 *   tadwg/virtualbox-additions (vboxmousegpl, kbpd への送信部分)
 *   QEMU hw/i386/vmport.c, hw/i386/vmmouse.c
 *   xf86-input-vmmouse (vmmouse_proto.h)
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 2 of the License, or
 * (at your option) any later version.
 */

#include "qemumouse.h"

/* ================================================================
 * VMware バックドア / vmmouse 定数
 * ================================================================ */
#define VMPORT_MAGIC            0x564D5868  /* "VMXh"            */
#define VMPORT_PORT             0x5658      /* "VX"              */

#define VMPORT_CMD_GETVERSION       10
#define VMPORT_CMD_VMMOUSE_DATA     39
#define VMPORT_CMD_VMMOUSE_STATUS   40
#define VMPORT_CMD_VMMOUSE_COMMAND  41

#define VMMOUSE_READ_ID             0x45414552
#define VMMOUSE_DISABLE             0x000000f5
#define VMMOUSE_REQUEST_RELATIVE    0x4c455252
#define VMMOUSE_REQUEST_ABSOLUTE    0x53424152
#define VMMOUSE_VERSION             0x3442554a

#define VMMOUSE_ERROR               0xffff0000

/* 1 パケット = 4 語: ボタン, X, Y, Z(ホイール) */
#define VMMOUSE_LEFT_BUTTON         0x20
#define VMMOUSE_RIGHT_BUTTON        0x10
#define VMMOUSE_MIDDLE_BUTTON       0x08

/* 絶対座標の範囲 (vmmouse の仕様は 0〜0xFFFF) */
#define VMMOUSE_ABS_MAX             0xFFFF

/* ドライバ独自の詳細エラーコード (ER_NOSPT の下位16bit) */
#define ED_QM_KBPD_OPEN         0x0001  /* kbpd オープン失敗              */
#define ED_QM_KBPD_MAILBOX      0x0002  /* kbpd メールボックス ID 取得失敗 */
#define ED_QM_NO_BACKDOOR       0x0003  /* バックドアが応答しない         */
#define ED_QM_NO_VMMOUSE        0x0004  /* vmmouse が応答しない           */
#define ED_QM_VCRE_TSK          0x000A  /* vcre_tsk 失敗                  */
#define ED_QM_STA_TSK           0x000B  /* sta_tsk 失敗                   */

#define TASK_PRIORITY   30
#define TASK_STACKSIZE  2048
#define POLL_INTERVAL   10      /* ms */

/* ================================================================
 * 大域変数
 * ================================================================ */
static ID   gKbpdMailboxID          = -1;
static W    gKbpdDD                 = -1;
static volatile BOOL gLoopFlag       = TRUE;
static volatile BOOL gPollTaskActive = FALSE;
static W    gLastX = -1, gLastY = -1, gLastBtn = -1;

/* kbpd へ送信するパケット. kbpd が処理するまで保持が必要なので大域変数. */
static PdInput  gPdMsg;
static PdInput2 gPdMsg2;

/* ================================================================
 * バックドア呼び出し
 * ================================================================ */
typedef struct {
    UW  eax, ebx, ecx, edx;
} BdRegs;

static VOID backdoor(UW cmd, UW arg, BdRegs *r)
{
    UW a, b, c, d;
    __asm__ __volatile__ (
        "inl %%dx, %%eax"
        : "=a" (a), "=b" (b), "=c" (c), "=d" (d)
        : "0" (VMPORT_MAGIC), "1" (arg), "2" (cmd), "3" (VMPORT_PORT)
        : "memory");
    r->eax = a;  r->ebx = b;  r->ecx = c;  r->edx = d;
}

/* vmmouse を絶対座標モードにする. 戻り値: 0=OK, 負=詳細エラー */
static W vmmouse_enable(VOID)
{
    BdRegs r;

    /* バックドアの存在確認 */
    backdoor(VMPORT_CMD_GETVERSION, ~0U, &r);
    if (r.ebx != VMPORT_MAGIC) {
        DEBUG_PRINT(("qemumouse: no backdoor ebx=0x%08X\n", r.ebx));
        return -(ED_QM_NO_BACKDOOR);
    }

    /* vmmouse の ID を読む */
    backdoor(VMPORT_CMD_VMMOUSE_COMMAND, VMMOUSE_READ_ID, &r);
    backdoor(VMPORT_CMD_VMMOUSE_STATUS, 0, &r);
    if ((r.eax & 0x0000ffff) == 0 || (r.eax & VMMOUSE_ERROR) == VMMOUSE_ERROR) {
        DEBUG_PRINT(("qemumouse: vmmouse status=0x%08X\n", r.eax));
        return -(ED_QM_NO_VMMOUSE);
    }
    backdoor(VMPORT_CMD_VMMOUSE_DATA, 1, &r);
    if (r.eax != VMMOUSE_VERSION) {
        DEBUG_PRINT(("qemumouse: vmmouse id=0x%08X\n", r.eax));
        return -(ED_QM_NO_VMMOUSE);
    }

    /* 絶対座標モード */
    backdoor(VMPORT_CMD_VMMOUSE_COMMAND, VMMOUSE_REQUEST_ABSOLUTE, &r);
    DEBUG_PRINT(("qemumouse: vmmouse absolute mode\n"));
    return 0;
}

static VOID vmmouse_disable(VOID)
{
    BdRegs r;
    backdoor(VMPORT_CMD_VMMOUSE_COMMAND, VMMOUSE_REQUEST_RELATIVE, &r);
    backdoor(VMPORT_CMD_VMMOUSE_COMMAND, VMMOUSE_DISABLE, &r);
}

/* ================================================================
 * kbpd へのポインタイベント送信 (vboxmousegpl と同じ方式)
 * ================================================================ */
#define PDMSG_WAIT_MAX  50      /* 最大待ち (50 × 10ms = 500ms) */

static W pdmsg_wait_pd(PdInput *p)
{
    W wait;
    for (wait = 0; wait < PDMSG_WAIT_MAX; wait++) {
        if (p->stat.read != 0) return 0;
        dly_tsk(10);
    }
    return -1;
}

static W pdmsg_wait_pd2(PdInput2 *p)
{
    W wait;
    for (wait = 0; wait < PDMSG_WAIT_MAX; wait++) {
        if (p->stat.read != 0) return 0;
        dly_tsk(10);
    }
    return -1;
}

static VOID post_pointer_event(W vx, W vy, W main_btn, W sub_btn, W wheel, W mid_btn)
{
    W ox, oy;

    /* vmmouse 座標 (0〜0xFFFF) → BTRON3 論理座標 (0〜PDIN_XMAX-1 / PDIN_YMAX-1) */
    ox = (W)(((UW)vx * (UW)(PDIN_XMAX - 1)) / VMMOUSE_ABS_MAX);
    oy = (W)(((UW)vy * (UW)(PDIN_YMAX - 1)) / VMMOUSE_ABS_MAX);
    if (ox < 0) ox = 0;
    if (oy < 0) oy = 0;
    if (ox > PDIN_XMAX - 1) ox = PDIN_XMAX - 1;
    if (oy > PDIN_YMAX - 1) oy = PDIN_YMAX - 1;

    if (pdmsg_wait_pd(&gPdMsg) < 0) {
        DEBUG_PRINT(("qemumouse: post_event timeout, drop\n"));
    } else {
        memset(&gPdMsg, 0, sizeof(gPdMsg));
        gPdMsg.stat.read = 0;
        gPdMsg.stat.cmd  = INP_PD;
        gPdMsg.stat.err  = DEV_OK;
        gPdMsg.stat.abs  = 1;
        gPdMsg.stat.main = main_btn;
        gPdMsg.stat.sub  = sub_btn;
        if (mid_btn) {
            gPdMsg.stat.onebut = 1;
            gPdMsg.stat.qpress = 1;
        }
        gPdMsg.xpos = (H)ox;
        gPdMsg.ypos = (H)oy;
        DEBUG_PRINT(("qemumouse: post_event vx=%d vy=%d -> %d,%d main=%d sub=%d mid=%d\n",
                     vx, vy, ox, oy, main_btn, sub_btn, mid_btn));
        snd_msg(gKbpdMailboxID, (T_MSG *)&gPdMsg);
    }

    if (wheel != 0) {
        if (pdmsg_wait_pd2(&gPdMsg2) < 0) {
            DEBUG_PRINT(("qemumouse: post_wheel timeout, drop\n"));
        } else {
            memset(&gPdMsg2, 0, sizeof(gPdMsg2));
            gPdMsg2.stat.read = 0;
            gPdMsg2.stat.cmd  = INP_PD2;
            gPdMsg2.stat.err  = DEV_OK;
            gPdMsg2.wheel     = (H)wheel;
            DEBUG_PRINT(("qemumouse: post_wheel wheel=%d\n", wheel));
            snd_msg(gKbpdMailboxID, (T_MSG *)&gPdMsg2);
        }
    }
}

/* ================================================================
 * ポーリングタスク
 * ================================================================ */
static VOID poll_task(W *pParam)
{
    BdRegs r;
    UW     n;

    DEBUG_PRINT(("qemumouse: poll_task start\n"));
    gPollTaskActive = TRUE;

    while (gLoopFlag) {
        dly_tsk(POLL_INTERVAL);

        backdoor(VMPORT_CMD_VMMOUSE_STATUS, 0, &r);
        if ((r.eax & VMMOUSE_ERROR) == VMMOUSE_ERROR) {
            /* キュー溢れなど: 絶対座標モードに入り直す */
            DEBUG_PRINT(("qemumouse: status error, re-enable\n"));
            vmmouse_enable();
            continue;
        }
        n = r.eax & 0x0000ffff;

        while (n >= 4 && gLoopFlag) {
            W x, y, btn, main_btn, sub_btn, mid_btn, wheel;

            backdoor(VMPORT_CMD_VMMOUSE_DATA, 4, &r);
            n -= 4;

            btn      = (W)(r.eax & 0xffff);
            x        = (W)(r.ebx & 0xffff);
            y        = (W)(r.ecx & 0xffff);
            wheel    = (W)(B)(r.edx & 0xff);       /* 符号付き */
            main_btn = (btn & VMMOUSE_LEFT_BUTTON)   ? 1 : 0;
            sub_btn  = (btn & VMMOUSE_RIGHT_BUTTON)  ? 1 : 0;
            mid_btn  = (btn & VMMOUSE_MIDDLE_BUTTON) ? 1 : 0;

            /* 座標・いずれかのボタンの変化 (押下と解放), ホイール回転で送信 */
            if (x != gLastX || y != gLastY || btn != gLastBtn || wheel != 0) {
                post_pointer_event(x, y, main_btn, sub_btn, wheel, mid_btn);
                gLastX = x;  gLastY = y;  gLastBtn = btn;
            }
        }
    }

    DEBUG_PRINT(("qemumouse: poll_task end\n"));
    gPollTaskActive = FALSE;
    exd_tsk();
}

/* ================================================================
 * ドライバエントリポイント
 * ================================================================ */
ERR main(Bool StartUp, TC *arg)
{
    ERR     ret;
    TC      tcName[6];
    W       rsize;
    T_CTSK  ctsk;
    ID      idPollTask;
    W       param = 0;

    DEBUG_PRINT(("qemumouse: main(%d) build=" __DATE__ " " __TIME__ "\n", StartUp));

    /* ---- 終了処理 ---- */
    if (TRUE != StartUp) {
        gLoopFlag = FALSE;
        while (gPollTaskActive) { dly_tsk(POLL_INTERVAL); }
        vmmouse_disable();
        if (gKbpdDD > 0) b_cls_dev(gKbpdDD, 0, NULL);
        return ER_OK;
    }

    /* ---- 起動処理 ---- */
    gLoopFlag = TRUE;
    gPdMsg.stat.read  = 1;      /* 最初の送信を許可 */
    gPdMsg2.stat.read = 1;

    /* 1. kbpd オープンとメールボックス ID 取得 */
    eucstotcs(tcName, "kbpd");
    gKbpdDD = b_opn_dev(tcName, D_READ, NULL);
    if (!(gKbpdDD > 0)) {
        DEBUG_PRINT(("qemumouse: kbpd open failed (%d)\n", gKbpdDD));
        return ER_NOSPT | ED_QM_KBPD_OPEN;
    }
    ret = b_rea_dev(gKbpdDD, DN_KPINPUT, (B *)&gKbpdMailboxID, sizeof(ID), &rsize, NULL);
    if (ret != 0) {
        DEBUG_PRINT(("qemumouse: DN_KPINPUT failed (%d)\n", ret));
        b_cls_dev(gKbpdDD, 0, NULL);
        return ER_NOSPT | ED_QM_KBPD_MAILBOX;
    }

    /* 2. vmmouse を絶対座標モードに */
    {
        W r = vmmouse_enable();
        if (r < 0) {
            b_cls_dev(gKbpdDD, 0, NULL);
            return ER_NOSPT | (-r);
        }
    }

    /* 3. ポーリングタスク起動 */
    memset(&ctsk, 0, sizeof(ctsk));
    ctsk.exinf   = &param;
    ctsk.task    = (FP)poll_task;
    ctsk.itskpri = TASK_PRIORITY;
    ctsk.stksz   = TASK_STACKSIZE;
    ctsk.tskatr  = TA_HLNG | TA_RNG0;
    idPollTask = vcre_tsk(&ctsk);
    if (idPollTask < 0) {
        vmmouse_disable();
        b_cls_dev(gKbpdDD, 0, NULL);
        return ER_NOSPT | ED_QM_VCRE_TSK;
    }
    ret = sta_tsk(idPollTask, (INT)param);
    if (ret != 0) {
        del_tsk(idPollTask);
        vmmouse_disable();
        b_cls_dev(gKbpdDD, 0, NULL);
        return ER_NOSPT | ED_QM_STA_TSK;
    }

    DEBUG_PRINT(("qemumouse: startup OK\n"));
    return ER_OK;
}
