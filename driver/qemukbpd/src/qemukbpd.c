/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * qemukbpd.c
 * QEMU 用 キーボード + 絶対座標マウス 実 IO ドライバ (lowkbpd の代わり) for BTRON3
 *
 * - キーボード: QEMU の i8042 から scan code set 1 を読み、lowkbpd と同じキートップコードに変換して
 *   kbpd に INP_KEY (キーボード ID 0x41: IBM 106) で送る
 *     ・E0/E1 付きのキーは 0x80 以降の独自コード (Pause = 0x82)
 *     ・NumLock は内部で処理 (LED 切り替え). OFF のときテンキーの数字・小数点は scan code + 0x48
 *     ・テンキーのキーは KeyInStat.tenkey = 1
 *     ・キーリピート (押しっぱなしで繰り返される make code) は送らない
 * - マウス: vmmouse (VMware バックドア) を絶対座標モードにして INP_PD / INP_PD2 で送る
 * - 割り込み待ち: キーボード (IRQ1) と PS/2 マウス (IRQ12) の割り込みでタスクを起こす.
 *   QEMU は vmmouse のイベントごとにダミーの PS/2 マウスのパケットを作るので、その IRQ12 を
 *   「vmmouse にデータがある」合図に使い、パケット自体は捨てる (kbpd には送らない. 二重のイベントを防ぐ).
 *   取りこぼしに備えて FALLBACK_MS ごとにも確認する. 引数 poll で従来の 10ms ポーリングにする
 * - 引数 log / log2 / log3 で動作ログをコンソール (シリアル) に出す (下記 LOG)
 * - INP_FLG でコマンド用イベントフラグを登録し、入力モード (LED) とサスペンド / リジュームに対応する
 *
 * 前提: STARTUP.CMD で lowkbpd (と qemumouse, vmwdrv) を読み込まないこと.
 *
 * 参考: tadwg/virtualbox-additions (kbpd への送信部分), 超漢字V 開発者向け資料 第6章 KB/PD,
 *       driver/kbprobe/keytop-ibm106.md (lowkbpd の調査結果)
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 2 of the License, or
 * (at your option) any later version.
 */
#include "qemukbpd.h"

/* ================================================================ 定数 */
#define KBID            0x41        /* KID_IBM_JP */

#define I8042_DATA      0x60
#define I8042_STAT      0x64
#define I8042_CMD       0x64
#define ST_OBF          0x01        /* 出力バッファにデータあり */
#define ST_IBF          0x02        /* 入力バッファが一杯       */
#define ST_AUX          0x20        /* データは補助ポート (マウス) から */
/* コマンドバイト */
#define I8042_CMDBYTE_POLL  0x64    /* 変換 (set1) 有効, 補助ポート無効, 割り込み無効, SYS */
#define I8042_CMDBYTE_IRQ   0x47    /* 変換 (set1) 有効, 両ポート有効, 両方の割り込み有効, SYS */
#define IV_AUX              INTVEC(IRQ12)

#define KB_LED_SCROLL   0x01
#define KB_LED_NUM      0x02
#define KB_LED_CAPS     0x04

#define VMPORT_MAGIC                0x564D5868
#define VMPORT_PORT                 0x5658
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
#define VMMOUSE_LEFT_BUTTON         0x20
#define VMMOUSE_RIGHT_BUTTON        0x10
#define VMMOUSE_MIDDLE_BUTTON       0x08
#define VMMOUSE_ABS_MAX             0xFFFF

/* 詳細エラーコード (ER_NOSPT の下位16bit) */
#define ED_KBPD_OPEN    0x0001
#define ED_KBPD_MAILBOX 0x0002
#define ED_I8042        0x0003      /* i8042 が応答しない */
#define ED_CRE_FLG      0x0004
#define ED_VCRE_TSK     0x000A
#define ED_STA_TSK      0x000B

#define TASK_PRIORITY   30
#define TASK_STACKSIZE  2048
#define POLL_INTERVAL   10          /* ms (poll 指定時) */
#define FALLBACK_MS     500         /* 割り込み待ちのときの確認間隔 */
#define NKEYMSG         32          /* キー送信バッファ数 */

/* ================================================================ 大域変数 */
static ID   gMbx = -1;              /* kbpd の入力メイルボックス */
static W    gKbpdDD = -1;
static ID   gFlg = -1;              /* コマンド用イベントフラグ */
static volatile BOOL gLoop = TRUE;
static volatile BOOL gSuspended = FALSE;
static volatile W    gActive = 0;   /* 動作中のタスク数 */
static BOOL gMouse = FALSE;         /* vmmouse を使えるか */
static ID   gIrqFlg = -1;           /* 割り込みハンドラ → io_task */
static BOOL gUseIrq = FALSE;        /* 割り込み待ちで動いているか */
static BOOL gIntKbDef = FALSE, gIntAuxDef = FALSE;
static volatile UW gIntKb = 0, gIntAux = 0;     /* 割り込み回数 */
#ifdef DEBUG
static W    gLog = 2;               /* デバッグ版は既定で log2 */
#else
static W    gLog = 0;               /* 0: なし, 1: キー・ボタン・コマンド・異常, 2: +移動・統計, 3: +全バイト */
#endif

static KeyInput gKeyMsg[NKEYMSG];
static W        gKeyIdx = 0;
static PdInput  gPdMsg;
static PdInput2 gPdMsg2;
static FlgInput gFlgMsg;

static UB   gDown[32];              /* 押下中のキートップ (リピート除去用) */
static BOOL gNumLock = FALSE;       /* 起動直後は OFF (lowkbpd と同じ) */
static W    gMode = AlphaMode;      /* kbpd の入力モード */
static BOOL gE0 = FALSE;
static W    gE1 = 0;                /* E1 シーケンスの残りバイト数 */
static W    gLastX = -1, gLastY = -1, gLastBtn = -1;

/* ================================================================ ログ */
static VOID log_head(VOID)
{
    SYSTIME t;
    get_tim(&t);
    printf("qemukbpd %u.%03u: ", (UW)((t / 1000) % 100000), (UW)(t % 1000));
}
#define LOG(lv, args)   do { if (gLog >= (lv)) { log_head(); printf args; } } while (0)

/* ================================================================ 共通 */
static W wait_read(volatile UW *stat)   /* read フラグ (先頭ワードの bit0) を待つ */
{
    W i;
    for (i = 0; i < 50; i++) {
        if (*stat & 1) return 0;
        dly_tsk(10);
    }
    return -1;
}

/* ================================================================ i8042 */
static BOOL kbc_wait_in(VOID)
{
    W i;
    for (i = 0; i < 1000; i++) {
        if (!(in_b(I8042_STAT) & ST_IBF)) return TRUE;
        dly_tsk(1);
    }
    return FALSE;
}

static VOID kbc_cmd(UB c)
{
    if (kbc_wait_in()) out_b(I8042_CMD, c);
}

static VOID kbc_data(UB d)
{
    if (kbc_wait_in()) out_b(I8042_DATA, d);
}

static VOID kbc_flush(VOID)
{
    W i;
    for (i = 0; i < 64 && (in_b(I8042_STAT) & ST_OBF); i++) (VOID)in_b(I8042_DATA);
}

static VOID kb_set_led(VOID)
{
    UB led = 0;
    if (gNumLock) led |= KB_LED_NUM;
    if (gMode == CapsMode) led |= KB_LED_CAPS;
    kbc_data(0xED);         /* LED 設定. ACK (0xFA) はポーリングで捨てる */
    kbc_data(led);
}

static W kbc_init(BOOL irq)
{
    kbc_flush();
    kbc_cmd(0x60);                  /* コマンドバイト書き込み */
    kbc_data(irq ? I8042_CMDBYTE_IRQ : I8042_CMDBYTE_POLL);
    kbc_cmd(irq ? 0xA8 : 0xA7);     /* 補助ポート (マウス) 有効 / 無効 */
    kbc_cmd(0xAE);                  /* キーボード有効 */
    kbc_flush();
    kbc_data(0xF4);                 /* キーボード: スキャン有効 */
    if (irq) {                      /* PS/2 マウス: データ送信開始 (割り込みの合図用) */
        kbc_cmd(0xD4);
        kbc_data(0xF4);
    }
    kb_set_led();
    return kbc_wait_in() ? 0 : -(ED_I8042);
}

/* ================================================================ キー */
/* E0 付きのキー → キートップ (0 は送らない) */
static UB e0_keytop(UB c)
{
    switch (c) {
    case 0x38: return 0x80;     /* 右 Alt   */
    case 0x1D: return 0x81;     /* 右 Ctrl  */
    case 0x52: return 0x83;     /* Insert   */
    case 0x53: return 0x84;     /* Delete   */
    case 0x47: return 0x85;     /* Home     */
    case 0x4F: return 0x86;     /* End      */
    case 0x49: return 0x87;     /* PageUp   */
    case 0x51: return 0x88;     /* PageDown */
    case 0x48: return 0x89;     /* ↑ */
    case 0x50: return 0x8A;     /* ↓ */
    case 0x4B: return 0x8B;     /* ← */
    case 0x4D: return 0x8C;     /* → */
    case 0x35: return 0x8D;     /* テンキー / */
    case 0x1C: return 0x8E;     /* テンキー Enter */
    case 0x5B: return 0x9C;     /* 左 Windows */
    case 0x5C: return 0x9D;     /* 右 Windows */
    case 0x37: return 0x54;     /* PrintScreen (SysRq と同じ) */
    case 0x46: return 0x82;     /* Ctrl + Break → Pause と同じ扱い */
    default:   return 0;        /* Menu (0x5D), 偽 Shift (0x2A/0x36) など */
    }
}

static VOID send_key(W keytop, BOOL press, BOOL tenkey)
{
    KeyInput *m;
    UB bit = 0x80 >> (keytop & 7);

    if (gSuspended) return;
    /* リピートの除去と、押していないキーの解放の除去 */
    if (press) {
        if (gDown[keytop >> 3] & bit) return;
        gDown[keytop >> 3] |= bit;
    } else {
        if (!(gDown[keytop >> 3] & bit)) return;
        gDown[keytop >> 3] &= ~bit;
    }
    m = &gKeyMsg[gKeyIdx];
    if (wait_read((volatile UW *)&m->stat) < 0) {
        LOG(1, ("key buffer busy, drop 0x%02X\n", keytop));
        return;
    }
    gKeyIdx = (gKeyIdx + 1) % NKEYMSG;
    memset(m, 0, sizeof(*m));
    m->stat.cmd    = INP_KEY;
    m->stat.err    = DEV_OK;
    m->stat.kbid   = KBID;
    m->stat.press  = press ? 1 : 0;
    m->stat.tenkey = tenkey ? 1 : 0;
    m->keytop      = keytop;
    LOG(1, ("key 0x%02X %s%s\n", keytop, press ? "down" : "up", tenkey ? " (tenkey)" : ""));
    snd_msg(gMbx, (T_MSG *)m);
}

static VOID kb_byte(UB b)
{
    UB   code = b & 0x7F;
    BOOL press = !(b & 0x80);

    /* キーボードの応答 (ACK / 再送 / エコー / エラー). 0xAA は自己診断の完了応答と同じ値だが
       左 Shift の解放でもあるので捨てない (このドライバはキーボードをリセットしない) */
    if (b == 0xFA || b == 0xFE || b == 0xEE || b == 0x00 || b == 0xFF) return;
    if (gE1 > 0) {                          /* Pause: E1 1D 45 E1 9D C5 */
        if (--gE1 == 0 && code == 0x45) {
            send_key(0x82, press, FALSE);
        }
        return;
    }
    if (b == 0xE1) { gE1 = 2; return; }
    if (b == 0xE0) { gE0 = TRUE; return; }
    if (gE0) {
        UB kt;
        gE0 = FALSE;
        kt = e0_keytop(code);
        if (kt) send_key(kt, press, FALSE);
        return;
    }
    if (code == 0x45) {                     /* NumLock: 内部で処理 */
        if (press && !(gDown[0x45 >> 3] & (0x80 >> (0x45 & 7)))) {
            gNumLock = !gNumLock;
            kb_set_led();
        }
        if (press) gDown[0x45 >> 3] |= 0x80 >> (0x45 & 7);
        else       gDown[0x45 >> 3] &= ~(0x80 >> (0x45 & 7));
        return;
    }
    if (code >= 0x47 && code <= 0x53) {    /* テンキー */
        W kt = code;
        if (!gNumLock && code != 0x4A && code != 0x4E) kt = code + 0x48;
        send_key(kt, press, TRUE);
        return;
    }
    send_key(code, press, FALSE);
}

/* ================================================================ マウス (vmmouse) */
typedef struct { UW eax, ebx, ecx, edx; } BdRegs;

static VOID backdoor(UW cmd, UW arg, BdRegs *r)
{
    UW a, b, c, d;
    __asm__ __volatile__ ("inl %%dx, %%eax"
        : "=a" (a), "=b" (b), "=c" (c), "=d" (d)
        : "0" (VMPORT_MAGIC), "1" (arg), "2" (cmd), "3" (VMPORT_PORT) : "memory");
    r->eax = a;  r->ebx = b;  r->ecx = c;  r->edx = d;
}

static BOOL vmmouse_enable(VOID)
{
    BdRegs r;
    backdoor(VMPORT_CMD_GETVERSION, ~0U, &r);
    if (r.ebx != VMPORT_MAGIC) return FALSE;
    backdoor(VMPORT_CMD_VMMOUSE_COMMAND, VMMOUSE_READ_ID, &r);
    backdoor(VMPORT_CMD_VMMOUSE_STATUS, 0, &r);
    if ((r.eax & 0xffff) == 0 || (r.eax & VMMOUSE_ERROR) == VMMOUSE_ERROR) return FALSE;
    backdoor(VMPORT_CMD_VMMOUSE_DATA, 1, &r);
    if (r.eax != VMMOUSE_VERSION) return FALSE;
    backdoor(VMPORT_CMD_VMMOUSE_COMMAND, VMMOUSE_REQUEST_ABSOLUTE, &r);
    return TRUE;
}

static VOID vmmouse_disable(VOID)
{
    BdRegs r;
    backdoor(VMPORT_CMD_VMMOUSE_COMMAND, VMMOUSE_REQUEST_RELATIVE, &r);
    backdoor(VMPORT_CMD_VMMOUSE_COMMAND, VMMOUSE_DISABLE, &r);
}

static VOID post_pointer(W vx, W vy, W main_btn, W sub_btn, W wheel, W mid_btn)
{
    W ox = (W)(((UW)vx * (UW)(PDIN_XMAX - 1)) / VMMOUSE_ABS_MAX);
    W oy = (W)(((UW)vy * (UW)(PDIN_YMAX - 1)) / VMMOUSE_ABS_MAX);
    if (ox > PDIN_XMAX - 1) ox = PDIN_XMAX - 1;
    if (oy > PDIN_YMAX - 1) oy = PDIN_YMAX - 1;

    if (gSuspended) return;
    if (wait_read((volatile UW *)&gPdMsg.stat) == 0) {
        memset(&gPdMsg, 0, sizeof(gPdMsg));
        gPdMsg.stat.cmd  = INP_PD;
        gPdMsg.stat.err  = DEV_OK;
        gPdMsg.stat.abs  = 1;
        gPdMsg.stat.main = main_btn;
        gPdMsg.stat.sub  = sub_btn;
        if (mid_btn) { gPdMsg.stat.onebut = 1; gPdMsg.stat.qpress = 1; }
        gPdMsg.xpos = (H)ox;
        gPdMsg.ypos = (H)oy;
        snd_msg(gMbx, (T_MSG *)&gPdMsg);
    } else {
        LOG(1, ("pd buffer busy, drop\n"));
    }
    if (wheel != 0 && wait_read((volatile UW *)&gPdMsg2.stat) == 0) {
        memset(&gPdMsg2, 0, sizeof(gPdMsg2));
        gPdMsg2.stat.cmd = INP_PD2;
        gPdMsg2.stat.err = DEV_OK;
        gPdMsg2.wheel    = (H)wheel;
        snd_msg(gMbx, (T_MSG *)&gPdMsg2);
    }
}

static W mouse_poll(VOID)       /* 読んだパケット数を返す */
{
    BdRegs r;
    UW     n;
    W      cnt = 0;

    backdoor(VMPORT_CMD_VMMOUSE_STATUS, 0, &r);
    if ((r.eax & VMMOUSE_ERROR) == VMMOUSE_ERROR) {
        LOG(1, ("vmmouse error status 0x%08X, re-enable\n", r.eax));
        vmmouse_enable();
        return 0;
    }
    n = r.eax & 0xffff;
    while (n >= 4 && gLoop) {
        W x, y, btn, wheel;
        backdoor(VMPORT_CMD_VMMOUSE_DATA, 4, &r);
        n -= 4;
        btn   = (W)(r.eax & 0xffff);
        x     = (W)(r.ebx & 0xffff);
        y     = (W)(r.ecx & 0xffff);
        wheel = (W)(B)(r.edx & 0xff);
        cnt++;
        if (btn != gLastBtn || wheel != 0) {
            LOG(1, ("pd %u,%u btn 0x%02X wheel %d\n", (UW)x, (UW)y, (UW)btn, wheel));
        } else if (x != gLastX || y != gLastY) {
            LOG(2, ("pd %u,%u\n", (UW)x, (UW)y));
        }
        if (x != gLastX || y != gLastY || btn != gLastBtn || wheel != 0) {
            post_pointer(x, y, (btn & VMMOUSE_LEFT_BUTTON) ? 1 : 0, (btn & VMMOUSE_RIGHT_BUTTON) ? 1 : 0,
                         wheel, (btn & VMMOUSE_MIDDLE_BUTTON) ? 1 : 0);
            gLastX = x;  gLastY = y;  gLastBtn = btn;
        }
    }
    return cnt;
}

/* ================================================================ 割り込み */
static VOID kb_inthdr(VOID)
{
    gIntKb++;
    set_flg(gIrqFlg, 1);
    EndOfInt(IV_KB);
}

static VOID aux_inthdr(VOID)
{
    gIntAux++;
    set_flg(gIrqFlg, 1);
    EndOfInt(IV_AUX);
}

static BOOL def_handler(IntVector vec, FP hdr)
{
    T_DINT dint;
    ER     er;
    memset(&dint, 0, sizeof(dint));
    dint.intatr = TA_HLNG;
    dint.inthdr = hdr;
    er = def_int(vec, &dint);
    if (er != E_OK) LOG(1, ("def_int(0x%02X) failed %d\n", (UW)vec, er));
    return er == E_OK;
}

static VOID irq_setup(VOID)
{
    T_CFLG cflg;
    memset(&cflg, 0, sizeof(cflg));
    cflg.flgatr = TA_WSGL;
    gIrqFlg = vcre_flg(&cflg);
    if (gIrqFlg < 0) { LOG(1, ("vcre_flg failed %d\n", gIrqFlg)); return; }
    gIntKbDef  = def_handler(IV_KB,  (FP)kb_inthdr);
    gIntAuxDef = gIntKbDef && def_handler(IV_AUX, (FP)aux_inthdr);
    gUseIrq = gIntKbDef && gIntAuxDef;
}

static VOID irq_release(VOID)
{
    if (gUseIrq) { DisableInt(IV_KB); DisableInt(IV_AUX); }
    if (gIntKbDef)  def_int(IV_KB, NULL);
    if (gIntAuxDef) def_int(IV_AUX, NULL);
    gIntKbDef = gIntAuxDef = FALSE;
}

/* ================================================================ タスク */
static VOID io_task(W *p)
{
    W    i, aux, nto = 0;
    BOOL timeout;
    gActive++;
    while (gLoop) {
        if (gUseIrq) {
            UINT ptn;
            timeout = twai_flg(&ptn, gIrqFlg, 1, TWF_ORW | TWF_CLR, FALLBACK_MS) != E_OK;
        } else {
            dly_tsk(POLL_INTERVAL);
            timeout = TRUE;
        }
        aux = 0;
        for (i = 0; i < 64; i++) {          /* キーボード (補助ポートのデータは合図として捨てる) */
            UB st = in_b(I8042_STAT);
            UB b;
            if (!(st & ST_OBF)) break;
            b = in_b(I8042_DATA);
            if (st & ST_AUX) {
                aux++;
                LOG(3, ("aux 0x%02X\n", b));
            } else {
                LOG(3, ("kbd 0x%02X\n", b));
                if (gUseIrq && timeout) LOG(1, ("key data found without interrupt\n"));
                kb_byte(b);
            }
        }
        if (gMouse && (aux > 0 || timeout)) {
            W n = mouse_poll();
            if (gUseIrq && timeout && n > 0) LOG(1, ("vmmouse data found without interrupt (%d)\n", n));
        }
        if (gUseIrq && timeout && ++nto >= 120) {   /* 約 1 分ごとの統計 */
            nto = 0;
            LOG(2, ("irq kbd %u aux %u\n", gIntKb, gIntAux));
        }
    }
    gActive--;
    exd_tsk();
}

/* kbpd からのコマンド (6.10) */
static VOID cmd_task(W *p)
{
    UINT cmd;
    gActive++;
    while (gLoop) {
        set_flg(gFlg, 0x80000000);                         /* コマンド入力 READY */
        if (twai_flg(&cmd, gFlg, 0x7fffffff, TWF_ORW | TWF_CLR, 200) != E_OK) continue;
        cmd &= 0x7fffffff;
        LOG(1, ("command 0x%08X\n", cmd));
        if (cmd == SuspendKBPD) {
            gSuspended = TRUE;
        } else if (cmd == ResumeKBPD) {
            gSuspended = FALSE;
            if (gMouse) vmmouse_enable();
            kb_set_led();
        } else if ((cmd & 0xff000000) == 0x03000000) {       /* InputModeCmd */
            gMode = (W)(cmd & 0xff);
            kb_set_led();
        }
        /* ScanRateCmd / SenseCmd は vmmouse に関係しないので無視する */
    }
    gActive--;
    exd_tsk();
}

static ERR start_task(FP fn)
{
    T_CTSK ctsk;
    ID     tid;
    memset(&ctsk, 0, sizeof(ctsk));
    ctsk.task    = fn;
    ctsk.itskpri = TASK_PRIORITY;
    ctsk.stksz   = TASK_STACKSIZE;
    ctsk.tskatr  = TA_HLNG | TA_RNG0;
    tid = vcre_tsk(&ctsk);
    if (tid < 0) return ER_NOSPT | ED_VCRE_TSK;
    if (sta_tsk(tid, 0) != 0) { del_tsk(tid); return ER_NOSPT | ED_STA_TSK; }
    return ER_OK;
}

static VOID send_flg(BOOL reg)
{
    if (wait_read((volatile UW *)&gFlgMsg.stat) < 0) return;
    memset(&gFlgMsg, 0, sizeof(gFlgMsg));
    gFlgMsg.stat.cmd  = INP_FLG;
    gFlgMsg.stat.err  = DEV_OK;
    gFlgMsg.stat.kb   = 1;
    gFlgMsg.stat.kbid = KBID;
    gFlgMsg.stat.reg  = reg ? 1 : 0;
    gFlgMsg.flgid     = gFlg;
    snd_msg(gMbx, (T_MSG *)&gFlgMsg);
}

/* ================================================================ 引数 */
static BOOL has_word(const B *s, const B *w)
{
    W n = strlen(w);
    while (*s) {
        if (strncmp(s, w, n) == 0 && (s[n] == 0 || s[n] == ' ' || s[n] == '\t' || s[n] == ',')) return TRUE;
        while (*s && *s != ' ' && *s != '\t' && *s != ',') s++;
        while (*s == ' ' || *s == '\t' || *s == ',') s++;
    }
    return FALSE;
}

static BOOL parse_args(TC *arg)     /* 戻り値: poll 指定 */
{
    B buf[128];
    buf[0] = 0;
    if (arg != NULL && tc_strlen(arg) < 40) tcstoeucs(buf, arg);
    if (has_word(buf, "log"))  gLog = 1;
    if (has_word(buf, "log2")) gLog = 2;
    if (has_word(buf, "log3")) gLog = 3;
    LOG(1, ("args \"%s\"\n", buf));
    return has_word(buf, "poll");
}

/* ================================================================ エントリ */
ERR main(Bool StartUp, TC *arg)
{
    TC     tcName[6];
    W      rsize, i;
    ERR    er;
    T_CFLG cflg;
    BOOL   poll;

    if (TRUE != StartUp) {                  /* 終了処理 */
        LOG(1, ("stop\n"));
        gLoop = FALSE;
        if (gUseIrq) set_flg(gIrqFlg, 1);
        while (gActive > 0) dly_tsk(POLL_INTERVAL);
        irq_release();
        send_flg(FALSE);
        if (gMouse) vmmouse_disable();
        if (gKbpdDD > 0) b_cls_dev(gKbpdDD, 0, NULL);
        return ER_OK;
    }

    poll = parse_args(arg);
    LOG(1, ("start, build " __DATE__ " " __TIME__ "\n"));
    for (i = 0; i < NKEYMSG; i++) gKeyMsg[i].stat.read = 1;
    gPdMsg.stat.read = 1;  gPdMsg2.stat.read = 1;  gFlgMsg.stat.read = 1;
    gLoop = TRUE;

    eucstotcs(tcName, "kbpd");
    gKbpdDD = b_opn_dev(tcName, D_READ, NULL);
    if (!(gKbpdDD > 0)) return ER_NOSPT | ED_KBPD_OPEN;
    er = b_rea_dev(gKbpdDD, DN_KPINPUT, (B *)&gMbx, sizeof(ID), &rsize, NULL);
    if (er != 0) { b_cls_dev(gKbpdDD, 0, NULL); return ER_NOSPT | ED_KBPD_MAILBOX; }

    if (!poll) irq_setup();
    if (kbc_init(gUseIrq) < 0) {
        irq_release();
        b_cls_dev(gKbpdDD, 0, NULL);
        return ER_NOSPT | ED_I8042;
    }
    if (gUseIrq) { EnableInt(IV_KB); EnableInt(IV_AUX); }
    gMouse = vmmouse_enable();
    LOG(1, ("kbpd mbx %d, vmmouse %s, %s\n", gMbx, gMouse ? "absolute" : "not found",
            gUseIrq ? "interrupt (IRQ1/IRQ12)" : "polling 10ms"));

    memset(&cflg, 0, sizeof(cflg));
    cflg.flgatr = TA_WMUL;          /* kbpd と qemukbpd の両方が待つ */
    gFlg = vcre_flg(&cflg);
    if (gFlg < 0) { b_cls_dev(gKbpdDD, 0, NULL); return ER_NOSPT | ED_CRE_FLG; }

    if ((er = start_task((FP)io_task)) != ER_OK) { irq_release(); b_cls_dev(gKbpdDD, 0, NULL); return er; }
    if ((er = start_task((FP)cmd_task)) != ER_OK) { gLoop = FALSE; return er; }
    send_flg(TRUE);

    LOG(1, ("startup OK\n"));
    return ER_OK;
}
