# -*- coding: utf-8 -*-
"""极简 QR Code 生成器。

Model 2 / Byte 模式 / ECC-L / 版本 1~5（均为单块结构），纯标准库。
只用于把点歌台局域网地址编码成二维码 SVG，供手机扫码，不依赖外部 CDN
或任何 pip 包。地址最长 106 字节，足够容纳 http://<IP>:<PORT>/ 形式。
"""
from __future__ import annotations

# version: (总码字数, 数据码字数, 纠错码字数) —— ECC-L，版本 1~5 均为单块
_CAP = {
    1: (26, 19, 7),
    2: (44, 34, 10),
    3: (70, 55, 15),
    4: (100, 80, 20),
    5: (134, 108, 26),
}
_ALIGN = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30]}

# GF(256)，本原多项式 0x11d
_EXP = [0] * 512
_LOG = [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _mul(a, b):
    if not a or not b:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _gen_poly(n):
    """生成 n 次 RS 生成多项式，系数按高次到低次排列。"""
    g = [1]
    for i in range(n):
        ng = [0] * (len(g) + 1)
        for j, c in enumerate(g):
            ng[j] ^= c
            ng[j + 1] ^= _mul(c, _EXP[i])
        g = ng
    return g


def _rs(data, n):
    """计算 n 个 Reed-Solomon 纠错码字。"""
    g = _gen_poly(n)
    res = [0] * n
    for b in data:
        f = b ^ res[0]
        res = res[1:] + [0]
        for j in range(n):
            res[j] ^= _mul(g[j + 1], f)
    return res


def _version(text):
    n = len(text.encode('utf-8'))
    for v in range(1, 6):
        if 4 + 8 + 8 * n <= _CAP[v][1] * 8:
            return v
    raise ValueError('地址过长（最多 106 字节）')


def _codewords(text, v):
    data = text.encode('utf-8')
    cap = _CAP[v][1] * 8
    bits = []

    def put(val, width):
        for i in range(width - 1, -1, -1):
            bits.append((val >> i) & 1)

    put(0b0100, 4)          # Byte 模式
    put(len(data), 8)       # 版本 1~9 的字符计数指示符为 8 bit
    for b in data:
        put(b, 8)
    bits += [0] * min(4, cap - len(bits))       # 终止符
    while len(bits) % 8:
        bits.append(0)
    pad, i = (0xEC, 0x11), 0
    while len(bits) < cap:
        put(pad[i % 2], 8)
        i += 1
    cw = [int(''.join(str(x) for x in bits[k:k + 8]), 2)
          for k in range(0, cap, 8)]
    return cw + _rs(cw, _CAP[v][2])


def _blank(v):
    n = 17 + 4 * v
    return [[None] * n for _ in range(n)]


def _setup(m, v):
    """布置功能图案；数据区保持 None。"""
    n = len(m)
    for r0, c0 in ((0, 0), (0, n - 7), (n - 7, 0)):
        for dr in range(-1, 8):
            for dc in range(-1, 8):
                r, c = r0 + dr, c0 + dc
                if 0 <= r < n and 0 <= c < n:
                    dark = (0 <= dr <= 6 and 0 <= dc <= 6 and
                            (dr in (0, 6) or dc in (0, 6) or
                             (2 <= dr <= 4 and 2 <= dc <= 4)))
                    m[r][c] = 1 if dark else 0
    for i in range(8, n - 8):
        b = 1 if i % 2 == 0 else 0
        if m[6][i] is None:
            m[6][i] = b
        if m[i][6] is None:
            m[i][6] = b
    pos = _ALIGN[v]
    for r in pos:
        for c in pos:
            if ((r <= 8 and c <= 8) or (r <= 8 and c >= n - 9) or
                    (r >= n - 9 and c <= 8)):
                continue
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    m[r + dr][c + dc] = (1 if (abs(dr) == 2 or abs(dc) == 2 or
                                              (dr == 0 and dc == 0)) else 0)
    m[n - 8][8] = 1                              # 固定黑模块
    for i in range(15):                           # 格式信息占位，数据放置时跳过
        r = i if i < 6 else (i + 1 if i < 8 else n - 15 + i)
        if m[r][8] is None:
            m[r][8] = 0
        c = n - 1 - i if i < 8 else (7 if i == 8 else 14 - i)
        if m[8][c] is None:
            m[8][c] = 0


def _mask(fn, r, c):
    if fn == 0:
        return (r + c) % 2 == 0
    if fn == 1:
        return r % 2 == 0
    if fn == 2:
        return c % 3 == 0
    if fn == 3:
        return (r + c) % 3 == 0
    if fn == 4:
        return (r // 2 + c // 3) % 2 == 0
    if fn == 5:
        return (r * c) % 2 + (r * c) % 3 == 0
    if fn == 6:
        return ((r * c) % 2 + (r * c) % 3) % 2 == 0
    return ((r + c) % 2 + (r * c) % 3) % 2 == 0


def _format(mask):
    """15 bit 格式信息：ECC-L(0b01) + 掩码号，BCH(15,5) + 0x5412 掩码。"""
    data = (1 << 3) | mask
    g15, gmask = 0x537, 0x5412
    d = data << 10
    while d.bit_length() - g15.bit_length() >= 0:
        d ^= g15 << (d.bit_length() - g15.bit_length())
    return ((data << 10) | d) ^ gmask


def _place(m, code, mask):
    n = len(m)
    inc, row, bit, byte = -1, n - 1, 7, 0
    col = n - 1
    while col > 0:
        if col == 6:                              # 跳过时序列
            col -= 1
        while True:
            for c in (col, col - 1):
                if m[row][c] is None:
                    dark = bool((code[byte] >> bit) & 1) if byte < len(code) else False
                    if _mask(mask, row, c):
                        dark = not dark
                    m[row][c] = 1 if dark else 0
                    bit -= 1
                    if bit < 0:
                        byte += 1
                        bit = 7
            row += inc
            if row < 0 or row >= n:
                row -= inc
                inc = -inc
                break
        col -= 2
    f = _format(mask)
    for i in range(15):
        b = (f >> i) & 1
        r = i if i < 6 else (i + 1 if i < 8 else n - 15 + i)
        m[r][8] = b
        c = n - 1 - i if i < 8 else (7 if i == 8 else 14 - i)
        m[8][c] = b
    m[n - 8][8] = 1


def _penalty(m):
    """ISO/IEC 18004 四条掩码惩罚规则。"""
    n, p = len(m), 0
    for i in range(n):
        for line in (m[i], [m[r][i] for r in range(n)]):
            run, prev = 0, None
            for v in line:
                if v == prev:
                    run += 1
                else:
                    if run >= 5:
                        p += 3 + run - 5
                    prev, run = v, 1
            if run >= 5:
                p += 3 + run - 5
    for r in range(n - 1):
        for c in range(n - 1):
            if m[r][c] == m[r][c + 1] == m[r + 1][c] == m[r + 1][c + 1]:
                p += 3
    pat1 = [1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0]
    pat2 = [0, 0, 0, 0, 1, 0, 1, 1, 1, 0, 1]
    for r in range(n):
        for c in range(n - 10):
            seg = [m[r][c + k] for k in range(11)]
            if seg == pat1 or seg == pat2:
                p += 40
    for c in range(n):
        for r in range(n - 10):
            seg = [m[r + k][c] for k in range(11)]
            if seg == pat1 or seg == pat2:
                p += 40
    dark = sum(sum(row) for row in m)
    p += 10 * (abs(dark * 100 // (n * n) - 50) // 5)
    return p


def encode(text, mask=None):
    """返回 0/1 模块矩阵。mask=None 时自动选择惩罚最低的掩码。"""
    v = _version(text)
    code = _codewords(text, v)
    if mask is not None:
        m = _blank(v)
        _setup(m, v)
        _place(m, code, mask)
        return m
    best = None
    for k in range(8):
        m = _blank(v)
        _setup(m, v)
        _place(m, code, k)
        score = _penalty(m)
        if best is None or score < best[0]:
            best = (score, m)
    return best[1]


def svg(text, scale=8, border=4):
    """输出白底深色模块的 SVG 二维码（含 4 模块静区）。"""
    m = encode(text)
    n = len(m)
    total = n + 2 * border
    path = []
    for r, row in enumerate(m):
        c = 0
        while c < n:
            if row[c]:
                start = c
                while c < n and row[c]:
                    c += 1
                w = c - start
                path.append('M%d %dh%dv1h-%dz' % (start + border, r + border, w, w))
            else:
                c += 1
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
            'viewBox="0 0 %d %d" shape-rendering="crispEdges">'
            '<rect width="%d" height="%d" fill="#ffffff"/>'
            '<path d="%s" fill="#0b1020"/></svg>') % (
                total * scale, total * scale, total, total,
                total, total, ''.join(path))
