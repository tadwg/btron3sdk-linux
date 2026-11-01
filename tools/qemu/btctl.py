#!/usr/bin/env python3
"""QEMU 上の超漢字V を操作する (コンテナ内で実行. tools/qemu/btctl から呼ぶ).

  cli CMD...                シリアルの CLI でコマンドを実行し出力を表示 (DEBUGMODE 1 が必要)
  screen OUT.png            画面を PNG で保存 (IMAGE と同じディレクトリ)
  move X Y                  マウスを画面座標 (ピクセル) へ移動 (絶対座標: qemumouse が必要)
  click X Y [-b BTN] [-2]   移動してクリック (BTN: left/right/middle, -2 でダブルクリック)
  wheel up|down [N]         ホイール
  mice                      QEMU のマウスの状態 ("(absolute)" なら絶対座標モード)
"""
import argparse, json, socket, struct, sys, time

def qmp():
    s = socket.socket(socket.AF_UNIX); s.connect('/w/qmp.sock')
    f = s.makefile('rw'); f.readline()
    def cmd(c, **a):
        f.write(json.dumps({'execute': c, 'arguments': a} if a else {'execute': c}) + '\n'); f.flush()
        while True:
            r = json.loads(f.readline())
            if 'return' in r or 'error' in r:
                if 'error' in r:
                    raise SystemExit('qmp error: %s' % r['error'].get('desc'))
                return r['return']
    cmd('qmp_capabilities')
    return cmd

def screen_size(cmd):
    cmd('screendump', filename='/tmp/.size.png', format='png')
    return struct.unpack('>II', open('/tmp/.size.png', 'rb').read()[16:24])

def abs_move(cmd, x, y):
    w, h = screen_size(cmd)
    cmd('input-send-event', events=[
        {'type': 'abs', 'data': {'axis': 'x', 'value': x * 32767 // max(w - 1, 1)}},
        {'type': 'abs', 'data': {'axis': 'y', 'value': y * 32767 // max(h - 1, 1)}}])

def button(cmd, b, down):
    cmd('input-send-event', events=[{'type': 'btn', 'data': {'button': b, 'down': down}}])

def cli(cmds, wait):
    s = socket.socket(socket.AF_UNIX); s.connect('/w/serial.sock'); s.settimeout(0.3)
    try:
        while s.recv(65536):
            pass
    except socket.timeout:
        pass
    for c in cmds:
        s.sendall(c.encode('euc-jp') + b'\r')
        out = b''; t = time.time()
        while time.time() - t < wait:
            try:
                d = s.recv(65536)
                if d:
                    out += d
            except socket.timeout:
                pass
            if out.rstrip().endswith(b']%') and b'\n' in out:
                break
        sys.stdout.write(out.decode('euc-jp', 'replace').replace('\r', '') + '\n')

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('cli'); p.add_argument('-w', type=float, default=30.0); p.add_argument('commands', nargs='+')
    p = sub.add_parser('screen'); p.add_argument('out')
    p = sub.add_parser('move'); p.add_argument('x', type=int); p.add_argument('y', type=int)
    p = sub.add_parser('click'); p.add_argument('x', type=int); p.add_argument('y', type=int)
    p.add_argument('-b', default='left'); p.add_argument('-2', dest='double', action='store_true')
    p = sub.add_parser('wheel'); p.add_argument('dir', choices=['up', 'down']); p.add_argument('n', type=int, nargs='?', default=1)
    sub.add_parser('mice')
    a = ap.parse_args()
    if a.cmd == 'cli':
        return cli(a.commands, a.w)
    cmd = qmp()
    if a.cmd == 'screen':
        cmd('screendump', filename='/w/' + a.out, format='png')
    elif a.cmd == 'move':
        abs_move(cmd, a.x, a.y)
    elif a.cmd == 'click':
        abs_move(cmd, a.x, a.y); time.sleep(0.3)
        for _ in range(2 if a.double else 1):
            button(cmd, a.b, True); time.sleep(0.08); button(cmd, a.b, False); time.sleep(0.08)
    elif a.cmd == 'wheel':
        for _ in range(a.n):
            b = 'wheel-' + a.dir
            button(cmd, b, True); button(cmd, b, False); time.sleep(0.05)
    elif a.cmd == 'mice':
        print(cmd('human-monitor-command', **{'command-line': 'info mice'}).strip())

if __name__ == '__main__':
    main()
