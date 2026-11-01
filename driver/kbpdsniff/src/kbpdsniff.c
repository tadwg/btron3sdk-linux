/*
 * kbpdsniff.c  調査用: ダミーの kbpd ドライバ
 *
 * 本物の kbpd の代わりにデバイス "kbpd" として登録し、
 * 実 IO ドライバ (lowkbpd など) から入力メイルボックスに届くメッセージ
 * (INP_KEY / INP_PD / INP_PD2 / INP_FLG) を、そのままシリアルに出力する.
 * 属性の読み書き要求 (DC_READ / DC_WRITE) もデータ番号を出力する.
 * DN_KPINPUT の読み出しにはメイルボックス ID を返し、それ以外の読み出しは 0 で埋めて返す.
 * INP_FLG でイベントフラグが登録されたら、本物の kbpd と同じく
 * スキャン速度・感度・入力モードのコマンドを送る.
 *
 * 出力は DEBUG_PRINT なのでデバッグビルドを使う. 本物の kbpd が無いので画面での操作はできない.
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 2 of the License, or
 * (at your option) any later version.
 */
#include "kbpdsniff.h"

#define TASK_PRIORITY   30
#define MAXFLG          4

static ID   gMbx  = -1;
static ID   gPort = -1;
static ID   gFlg[MAXFLG];
static W    gNflg = 0;
static UB   gZero[512];

/* ---- 実 IO ドライバへのコマンド (6.10) ---- */
static VOID send_cmd(ID flg, UW cmd)
{
    UINT ptn;
    W    i;
    for (i = 0; i < 100; i++) {         /* コマンド入力 READY (0x80000000) を最大 1 秒待つ */
        if (pol_flg(&ptn, flg, 0x80000000, TWF_ORW | TWF_CLR) == E_OK) {
            set_flg(flg, cmd);
            DEBUG_PRINT(("kbpdsniff: CMD flg=%d cmd=0x%08X\n", flg, cmd));
            return;
        }
        dly_tsk(10);
    }
    DEBUG_PRINT(("kbpdsniff: CMD flg=%d cmd=0x%08X not ready\n", flg, cmd));
}

/* ---- 入力メイルボックスの受信 ---- */
static VOID mbx_task(W *p)
{
    T_MSG *msg;
    for (;;) {
        if (rcv_msg(&msg, gMbx) != E_OK) { dly_tsk(10); continue; }
        {
            UW  raw = *(UW *)((B *)msg + sizeof(T_MSG));
            PdInStat *st = (PdInStat *)((B *)msg + sizeof(T_MSG));
            switch (st->cmd) {
            case INP_KEY: {
                KeyInput *k = (KeyInput *)msg;
                DEBUG_PRINT(("kbpdsniff: KEY kbid=0x%02X press=%d keytop=0x%02X err=%d raw=0x%08X\n",
                             k->stat.kbid, k->stat.press, k->keytop, k->stat.err, raw));
                break; }
            case INP_PD: {
                PdInput *d = (PdInput *)msg;
                DEBUG_PRINT(("kbpdsniff: PD abs=%d x=%d y=%d main=%d sub=%d qpress=%d onebut=%d "
                             "nodsp=%d inv=%d vst=%d err=%d raw=0x%08X\n",
                             d->stat.abs, d->xpos, d->ypos, d->stat.main, d->stat.sub,
                             d->stat.qpress, d->stat.onebut, d->stat.nodsp, d->stat.inv,
                             d->stat.vst, d->stat.err, raw));
                break; }
            case INP_PD2: {
                PdInput2 *w = (PdInput2 *)msg;
                DEBUG_PRINT(("kbpdsniff: PD2 wheel=%d raw=0x%08X\n", w->wheel, raw));
                break; }
            case INP_FLG: {
                FlgInput *g = (FlgInput *)msg;
                DEBUG_PRINT(("kbpdsniff: FLG reg=%d kb=%d kbid=0x%02X flgid=%d raw=0x%08X\n",
                             g->stat.reg, g->stat.kb, g->stat.kbid, g->flgid, raw));
                if (g->stat.reg && gNflg < MAXFLG) {
                    ID f = g->flgid;
                    gFlg[gNflg++] = f;
                    g->stat.read = 1;
                    send_cmd(f, ScanRateCmd(8));
                    send_cmd(f, SenseCmd(8));
                    send_cmd(f, InputModeCmd(AlphaMode));
                    continue;
                }
                break; }
            default:
                DEBUG_PRINT(("kbpdsniff: UNKNOWN cmd=%d raw=0x%08X\n", st->cmd, raw));
                break;
            }
            st->read = 1;
        }
    }
}

/* ---- デバイス要求 (DC_OPEN / DC_READ など) ---- */
static ERR copy_out(DevReq *req, VP src, W size, W *alen)
{
    ERR er;
    *alen = size;
    if (req->datacnt == 0) return ER_OK;           /* サイズの問い合わせ */
    if (req->datacnt < size) return ER_PAR;
    if (req->cmd.adcnv) {
        er = SetTaskSpace(req->taskid);
        if (er != ER_OK) return er;
        er = CheckSpaceRW(req->memptr, size);
        if (er != ER_OK) return er;
    }
    memcpy(req->memptr, src, size);
    return ER_OK;
}

static VOID port_task(W *p)
{
    DevReq req;
    DevRsp rsp;
    RNO    rdvno;
    INT    size;
    ERR    er;

    for (;;) {
        er = acp_por(&rdvno, (VP)&req, &size, gPort, D_NORM_PTN | D_ABORT_PTN);
        if (er < E_OK) { dly_tsk(10); continue; }
        rsp.devid = req.devid;  rsp.cmd = req.cmd;  rsp.datano = req.datano;  rsp.datacnt = 0;
        er = ER_OK;
        switch (req.cmd.cmd) {
        case DC_OPEN:
        case DC_CLOSE:
        case DC_CLOSEALL:
            DEBUG_PRINT(("kbpdsniff: DC %d task=%d\n", req.cmd.cmd, req.taskid));
            break;
        case DC_READ:
            DEBUG_PRINT(("kbpdsniff: READ datano=%d cnt=%d task=%d\n", req.datano, req.datacnt, req.taskid));
            if (req.datano == DN_KPINPUT) {
                er = copy_out(&req, &gMbx, sizeof(ID), &rsp.datacnt);
            } else {
                W n = req.datacnt > (W)sizeof(gZero) ? (W)sizeof(gZero) : req.datacnt;
                er = copy_out(&req, gZero, n, &rsp.datacnt);
            }
            break;
        case DC_WRITE:
            DEBUG_PRINT(("kbpdsniff: WRITE datano=%d cnt=%d task=%d\n", req.datano, req.datacnt, req.taskid));
            rsp.datacnt = req.datacnt;
            break;
        default:
            DEBUG_PRINT(("kbpdsniff: DC %d\n", req.cmd.cmd));
            break;
        }
        rsp.error.err = er;
        rpl_rdv(rdvno, (VP)&rsp, sizeof(rsp));
    }
}

static ID start_task(FP fn, W stk)
{
    T_CTSK ctsk;
    ID     tid;
    memset(&ctsk, 0, sizeof(ctsk));
    ctsk.task    = fn;
    ctsk.itskpri = TASK_PRIORITY;
    ctsk.stksz   = stk;
    ctsk.tskatr  = TA_HLNG | TA_RNG0;
    tid = vcre_tsk(&ctsk);
    if (tid >= 0) sta_tsk(tid, 0);
    return tid;
}

ERR main(Bool StartUp, TC *arg)
{
    T_CMBX cmbx;
    T_CPOR cpor;
    DevDef def;
    ERR    er;

    if (TRUE != StartUp) return ER_OK;

    memset(&cmbx, 0, sizeof(cmbx));
    cmbx.mbxatr = TA_TFIFO;
    gMbx = vcre_mbx(&cmbx);
    if (gMbx < 0) return ER_NOSPT | 1;

    memset(&cpor, 0, sizeof(cpor));
    strncpy((B *)&cpor.exinf, "kbpd", 4);
    cpor.poratr  = TA_NULL;
    cpor.maxcmsz = sizeof(DevReq);
    cpor.maxrmsz = sizeof(DevRsp);
    gPort = vcre_por(&cpor);
    if (gPort < 0) return ER_NOSPT | 2;

    if (start_task((FP)mbx_task, 4096) < 0) return ER_NOSPT | 3;
    if (start_task((FP)port_task, 4096) < 0) return ER_NOSPT | 4;

    memset(&def, 0, sizeof(def));
    def.attr.chardev = 1;
    def.subunits = 1;
    eucstotcs(def.name, "kbpd");
    def.portid = gPort;
    er = b_DefDevice(&def, NULL);
    DEBUG_PRINT(("kbpdsniff: DefDevice er=%d mbx=%d port=%d\n", er, gMbx, gPort));
    if (er < ER_OK) return er;
    return ER_OK;
}
