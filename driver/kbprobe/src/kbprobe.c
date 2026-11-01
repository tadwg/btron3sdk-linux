/*
 * kbprobe.c  調査用ドライバ: kbpd のキーマップの変化をシリアルに出力する
 *
 * 実 IO ドライバ (lowkbpd など) がどのキートップコードを送っているかを調べるため、
 * kbpd の DN_KEYMAP (押されているキートップコードのビット列) を 10ms ごとに読み、
 * 変化したビットを "kbprobe: DOWN 0xNN" / "kbprobe: UP 0xNN" として出力する.
 * 起動時に DN_KEYID と、そのキーボードの DN_KEYDEF (keytopofs, keymax, kctmax) も出力する.
 *
 * 出力は DEBUG_PRINT を使うのでデバッグビルド (bin/kbprobe.debug) を使う.
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 2 of the License, or
 * (at your option) any later version.
 */
#include "kbprobe.h"

#define TASK_PRIORITY   30
#define TASK_STACKSIZE  4096
#define POLL_INTERVAL   10

static W    gKbpdDD = -1;
static volatile BOOL gLoop = TRUE;
static volatile BOOL gActive = FALSE;
static KeyMap gLast;
static KeyDef gDef;             /* 可変長だが最大サイズで確保 */

static VOID probe_task(W *p)
{
    KeyMap km;
    W      rsize, i, b;

    gActive = TRUE;
    memset(gLast, 0, sizeof(gLast));
    while (gLoop) {
        dly_tsk(POLL_INTERVAL);
        if (b_rea_dev(gKbpdDD, DN_KEYMAP, (B *)km, sizeof(km), &rsize, NULL) != 0) continue;
        for (i = 0; i < (W)sizeof(km); i++) {
            UB diff = km[i] ^ gLast[i];
            if (!diff) continue;
            for (b = 0; b < 8; b++) {
                if (diff & (1 << b)) {
                    /* キーマップは各バイトの最上位ビットが先 (kbpdsniff で確認) */
                    DEBUG_PRINT(("kbprobe: %s 0x%02X\n",
                                 (km[i] & (1 << b)) ? "DOWN" : "UP", i * 8 + (7 - b)));
                }
            }
            gLast[i] = km[i];
        }
    }
    gActive = FALSE;
    exd_tsk();
}

ERR main(Bool StartUp, TC *arg)
{
    TC      tcName[6];
    W       rsize, kid = -1;
    T_CTSK  ctsk;
    ID      tid;
    W       param = 0;
    ERR     er;

    if (TRUE != StartUp) {
        gLoop = FALSE;
        while (gActive) dly_tsk(POLL_INTERVAL);
        if (gKbpdDD > 0) b_cls_dev(gKbpdDD, 0, NULL);
        return ER_OK;
    }
    eucstotcs(tcName, "kbpd");
    gKbpdDD = b_opn_dev(tcName, D_READ, NULL);
    if (!(gKbpdDD > 0)) return ER_NOSPT | 1;

    er = b_rea_dev(gKbpdDD, DN_KEYID, (B *)&kid, sizeof(kid), &rsize, NULL);
    DEBUG_PRINT(("kbprobe: DN_KEYID er=%d kid=0x%X\n", er, kid));
    if (er == 0 && kid >= 0) {
        er = b_rea_dev(gKbpdDD, DN_KEYDEF(kid), (B *)&gDef, sizeof(gDef), &rsize, NULL);
        DEBUG_PRINT(("kbprobe: DN_KEYDEF er=%d size=%d keytopofs=%d keymax=%d kctmax=%d\n",
                     er, rsize, gDef.keytopofs, gDef.keytab.keymax, gDef.keytab.kctmax));
        er = b_rea_dev(gKbpdDD, DN_KEYDEF2(kid), (B *)&gDef, sizeof(gDef), &rsize, NULL);
        DEBUG_PRINT(("kbprobe: DN_KEYDEF2 er=%d size=%d keytopofs=%d keymax=%d kctmax=%d\n",
                     er, rsize, gDef.keytopofs, gDef.keytab.keymax, gDef.keytab.kctmax));
    }

    memset(&ctsk, 0, sizeof(ctsk));
    ctsk.exinf   = &param;
    ctsk.task    = (FP)probe_task;
    ctsk.itskpri = TASK_PRIORITY;
    ctsk.stksz   = TASK_STACKSIZE;
    ctsk.tskatr  = TA_HLNG | TA_RNG0;
    tid = vcre_tsk(&ctsk);
    if (tid < 0) { b_cls_dev(gKbpdDD, 0, NULL); return ER_NOSPT | 2; }
    sta_tsk(tid, (INT)param);
    DEBUG_PRINT(("kbprobe: started\n"));
    return ER_OK;
}
