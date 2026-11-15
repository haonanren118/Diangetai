# -*- coding: utf-8 -*-
"""点歌台 Web 服务。纯标准库。"""
import json
import mimetypes
import os
import re
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import about
import music
import pinyin
import qr

PORT = int(os.environ.get('KTV_PORT', '8089'))
SHARE = os.environ.get('KTV_SHARE', '/shares')
MUSIC = os.path.join(SHARE, 'music')
DATA = os.path.join(SHARE, 'data')
WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web')
SAVE = os.path.join(DATA, 'state.json')
AUDIO = ('.mp3', '.m4a', '.flac', '.wav', '.aac', '.ogg')
VIDEO = ('.mp4', '.mkv', '.avi', '.mov')
EXTS = AUDIO + VIDEO
IMGS = ('.jpg', '.jpeg', '.png', '.webp', '.bmp')
PLACEHOLDER = ('<svg xmlns="http://www.w3.org/2000/svg" width="400" height="400">'
               '<rect width="400" height="400" fill="#1b2340"/>'
               '<text x="200" y="275" font-size="180" text-anchor="middle" '
               'fill="#5a6b95">&#9835;</text></svg>')

os.makedirs(MUSIC, exist_ok=True)
os.makedirs(DATA, exist_ok=True)
lock = threading.Lock()
state = {'items': [], 'cur': -1, 'seq': 0}
try:
    with open(SAVE, encoding='utf-8') as f:
        saved = json.load(f)
    state.update({k: saved[k] for k in state if k in saved})
except Exception:
    pass


def persist():
    try:
        with open(SAVE, 'w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False)
    except Exception:
        pass


def _local_cover(path):
    """同名图片或目录下的 cover.jpg / folder.jpg 作为本地歌曲封面。"""
    base = os.path.splitext(path)[0]
    for ext in IMGS:
        f = base + ext
        if os.path.isfile(f):
            return '/cover?k=' + urllib.parse.quote(os.path.relpath(f, MUSIC))
    folder = os.path.dirname(path)
    for name in ('cover.jpg', 'folder.jpg', 'cover.png', 'cover.webp'):
        f = os.path.join(folder, name)
        if os.path.isfile(f):
            return '/cover?k=' + urllib.parse.quote(os.path.relpath(f, MUSIC))
    return ''


def local_songs():
    """扫描本地曲库；把 X-原唱.ext 与 X-伴奏.ext 配成同一首歌的两条音轨。"""
    groups = {}
    for root, _, files in os.walk(MUSIC):
        for fn in files:
            base, ext = os.path.splitext(fn)
            if ext.lower() not in EXTS:
                continue
            path = os.path.join(root, fn)
            key = os.path.relpath(path, MUSIC)
            title = re.sub(r'^\d{1,4}\s*[-.,、 ]+\s*', '', base)
            m = re.search(r'-(原唱|伴奏)$', title)
            if m:
                stem, role = title[:m.start()], ('orig' if m.group(1) == '原唱' else 'inst')
            else:
                stem, role = title, 'orig'
            parts = stem.split('-')
            name = parts[0].strip()
            artist = parts[1].strip() if len(parts) > 1 else ''
            rel_dir = os.path.relpath(root, MUSIC)
            gkey = os.path.join(rel_dir, stem) if rel_dir != '.' else stem
            g = groups.setdefault(gkey, {
                'k': gkey, 't': name + (' - ' + artist if artist else ''),
                'n': name, 'a': artist, 'tracks': {}, 'lrc': '', 'video': False,
            })
            g['tracks'][role] = '/media?k=' + urllib.parse.quote(key)
            if not g.get('cover'):
                g['cover'] = _local_cover(path)
            if role == 'orig' or 'orig' not in g['tracks']:
                lrc = os.path.splitext(path)[0] + '.lrc'
                if os.path.isfile(lrc):
                    g['lrc'] = '/lrc?k=' + urllib.parse.quote(key)
                g['video'] = ext.lower() in VIDEO
    out = []
    for g in groups.values():
        g['play'] = g['tracks'].get('orig') or g['tracks'].get('inst') or ''
        g.setdefault('cover', '')
        g['g'] = 'local'
        out.append(g)
    return sorted(out, key=lambda x: x['k'])


def online_songs(kw='', n=40):
    out = []
    for s in music.catalog(kw, n):
        out.append({
            'id': s['id'],
            't': s['name'] + (' - ' + s['artist'] if s['artist'] else ''),
            'n': s['name'], 'a': s['artist'], 'g': 'ne',
            'play': '/stream?id=' + urllib.parse.quote(s['id']),
            'lrc': '/lrc?id=' + urllib.parse.quote(s['id']),
            'video': False,
            'cover': '/cover?id=' + urllib.parse.quote(s['id']),
            'tracks': {'orig': '/stream?id=' + urllib.parse.quote(s['id'])},
        })
    return out


def search_songs(kw):
    """中文子串匹配；纯字母时按全拼 / 首字母 / 混合拼音匹配。"""
    py = pinyin.is_pinyin(kw)
    if py:
        lo = [x for x in local_songs() if pinyin.match(x['t'], kw)][:20]
    else:
        lo = [x for x in local_songs() if kw.lower() in x['t'].lower()][:20]
    on = online_songs(kw, 40)
    if py:
        on.sort(key=lambda x: pinyin.score(x['t'], kw))
        # 在线搜索对短拼音可能只返回英文名，用热歌榜里拼音命中的中文歌补上
        if on and not any(pinyin.match(x['t'], kw) for x in on):
            try:
                extra = [x for x in online_songs('', 80)
                         if pinyin.match(x['t'], kw)][:12]
                seen = {x['id'] for x in on}
                on = extra + [x for x in on if x['id'] not in {y['id'] for y in extra}]
            except Exception:
                pass
    return lo + on


def add_song(item):
    with lock:
        state['items'].append({
            't': item['t'], 'play': item['play'], 'lrc': item['lrc'],
            'video': item.get('video', False),
            'tracks': item.get('tracks') or {'orig': item['play']},
            'track': 'orig', 'n': item.get('n', ''), 'a': item.get('a', ''),
            'cover': item.get('cover', ''),
        })
        if state['cur'] < 0:
            state['cur'] = 0
            state['seq'] += 1
        persist()
        return '已点：' + item['t']


def move(delta=None, index=None, remove=False, clear=False):
    with lock:
        items = state['items']
        if clear:
            items.clear()
            state['cur'] = -1
        elif remove and 0 <= index < len(items):
            items.pop(index)
            if state['cur'] > index:
                state['cur'] -= 1
            if state['cur'] >= len(items):
                state['cur'] = len(items) - 1
        elif index is not None and 0 <= index < len(items):
            state['cur'] = index
            items[index]['track'] = 'orig'
            items[index]['karaoke'] = False
            items[index]['play'] = (items[index].get('tracks') or {}).get('orig') \
                or items[index]['play']
        elif delta is not None:
            nxt = state['cur'] + delta
            if delta < 0 and nxt < 0:
                nxt = 0 if items else -1
            state['cur'] = nxt if 0 <= nxt < len(items) else -1
            if 0 <= state['cur'] < len(items):
                it = items[state['cur']]
                it['track'] = 'orig'
                it['karaoke'] = False
                it['play'] = (it.get('tracks') or {}).get('orig') or it['play']
        else:
            return ''
        state['seq'] += 1
        persist()
        return 'ok'


def set_track(mode):
    """切换当前歌曲的原唱/伴奏。

    曲库里有配对的「歌名-伴奏.mp3」时直接用真实伴奏音轨；没有配对文件时
    保持同一音源并标记 karaoke，由大屏用 Web Audio 做立体声消人声。
    这样任何歌曲都能切换，歌词、封面、进度始终与当前歌曲一致。
    """
    with lock:
        items, cur = state['items'], state['cur']
        if not (0 <= cur < len(items)):
            return 'no song'
        it = items[cur]
        tracks = it.setdefault('tracks', {'orig': it['play']})
        if mode == 'orig':
            url = tracks.get('orig') or it['play']
            karaoke = False
        else:
            url = tracks.get('inst')
            karaoke = not url          # 没有真实伴奏音轨 → 浏览器端消人声
            if not url:
                url = tracks.get('orig') or it['play']
        if it.get('track', 'orig') == mode and it['play'] == url \
                and bool(it.get('karaoke')) == karaoke:
            return 'already'
        it['play'], it['track'], it['karaoke'] = url, mode, karaoke
        state['seq'] += 1
        persist()
        return 'ok'


def _cover_of(it):
    """兼容旧队列条目（无 cover 字段时从在线播放地址回推封面）。"""
    if not it:
        return ''
    if it.get('cover'):
        return it['cover']
    m = re.match(r'/stream\?id=(.+)$', it.get('play', ''))
    return '/cover?id=' + m.group(1) if m else ''


def now():
    with lock:
        items, cur, seq = state['items'], state['cur'], state['seq']
        it = items[cur] if 0 <= cur < len(items) else None
        return {
            'seq': seq,
            'cur': it['t'] if it else '暂无歌曲',
            'play': it['play'] if it else '',
            'lrc': it['lrc'] if it else '',
            'video': it.get('video', False) if it else False,
            'cover': _cover_of(it),
            'n': len(items),
            'cur_i': cur,
            'track': it.get('track', 'orig') if it else 'orig',
            'karaoke': bool(it.get('karaoke')) if it else False,
            'paired': len(it.get('tracks') or {}) > 1 if it else False,
            'next': items[cur + 1]['t'] if it and cur + 1 < len(items) else '',
        }


def queue():
    with lock:
        return [{'i': i, 't': x['t'], 'cur': i == state['cur'],
                 'cover': _cover_of(x)}
                for i, x in enumerate(state['items'])]


class H(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, ctype, body=b'', headers=None):
        if isinstance(body, str):
            body = body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _json(self, obj):
        self._send(200, 'application/json; charset=utf-8',
                   json.dumps(obj, ensure_ascii=False))

    def _page(self, name):
        try:
            with open(os.path.join(WEB, name), 'rb') as f:
                self._send(200, 'text/html; charset=utf-8', f.read())
        except Exception:
            self._send(404, 'text/plain; charset=utf-8', 'not found')

    def _bytes(self, path, rng=None):
        size = os.path.getsize(path)
        start, end = 0, size - 1
        if rng:
            m = re.match(r'bytes=(\d*)-(\d*)', rng)
            if m:
                if m.group(1):
                    start = int(m.group(1))
                if m.group(2):
                    end = min(int(m.group(2)), size - 1)
        if start > end or start >= size:
            self._send(416, 'text/plain', 'range not satisfiable',
                       {'Content-Range': 'bytes */%d' % size})
            return
        length = end - start + 1
        self.send_response(206 if rng else 200)
        self.send_header('Content-Type', mimetypes.guess_type(path)[0]
                         or 'application/octet-stream')
        self.send_header('Content-Length', str(length))
        self.send_header('Accept-Ranges', 'bytes')
        if rng:
            self.send_header('Content-Range', 'bytes %d-%d/%d' % (start, end, size))
        self.end_headers()
        try:
            with open(path, 'rb') as f:
                f.seek(start)
                left = length
                while left > 0:
                    b = f.read(min(65536, left))
                    if not b:
                        break
                    self.wfile.write(b)
                    left -= len(b)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _stream(self, sid):
        up = music.stream(sid, self.headers.get('Range'))
        if not up:
            self._send(404, 'text/plain; charset=utf-8', '不可播放或音源暂不可用')
            return
        self.send_response(up.status)
        for h in ('Content-Type', 'Content-Range'):
            v = up.headers.get(h)
            if v:
                self.send_header(h, v)
        self.send_header('Accept-Ranges', 'bytes')
        cl = up.headers.get('Content-Length')
        if cl:
            self.send_header('Content-Length', cl)
        else:
            self.send_header('Connection', 'close')
            self.close_connection = True
        self.end_headers()
        try:
            while True:
                b = up.read(65536)
                if not b:
                    break
                self.wfile.write(b)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            up.close()

    def _lrc_file(self, key):
        path = os.path.realpath(os.path.splitext(os.path.join(MUSIC, key))[0] + '.lrc')
        root = os.path.realpath(MUSIC)
        if not path.startswith(root + os.sep) or not os.path.isfile(path):
            return ''
        with open(path, encoding='utf-8', errors='replace') as f:
            return f.read()

    def do_GET(self):
        p = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(p.query)
        g = lambda k, d='': (q.get(k) or [d])[0]
        try:
            if p.path in ('/', '/index.html'):
                return self._page('index.html')
            if p.path in ('/tv', '/tv.html'):
                return self._page('tv.html')
            if about.handle_get(self, p.path, g):
                return
            if p.path == '/favicon.ico':
                return self._send(204, 'image/x-icon')
            if p.path == '/ping':
                return self._send(200, 'text/plain; charset=utf-8', 'ok')
            if p.path == '/api/lan':
                host = self.headers.get('Host') or ''
                ip = host.split(':')[0].strip('[]')
                return self._json({
                    'url': ('http://' + host + '/') if host else '',
                    'host': host,
                    'loopback': ip in ('', '127.0.0.1', 'localhost', '::1'),
                    'lan': os.environ.get('KTV_LAN_URL', ''),
                })
            if p.path == '/qr.svg':
                u = g('u') or ('http://' + (self.headers.get('Host') or '') + '/')
                if len(u) > 200 or not re.match(r'^https?://[\x21-\x7e]+$', u):
                    return self._send(400, 'text/plain; charset=utf-8', 'bad url')
                try:
                    body = qr.svg(u)
                except Exception:
                    return self._send(400, 'text/plain; charset=utf-8', 'url too long')
                return self._send(200, 'image/svg+xml; charset=utf-8', body,
                                  {'Cache-Control': 'no-store'})
            if p.path == '/cover':
                cache = {'Cache-Control': 'public, max-age=86400'}
                k = g('k')
                if k:
                    path = os.path.realpath(os.path.join(MUSIC, urllib.parse.unquote(k)))
                    root = os.path.realpath(MUSIC)
                    ok = (path.startswith(root + os.sep) and os.path.isfile(path)
                          and os.path.splitext(path)[1].lower() in IMGS)
                    if ok:
                        with open(path, 'rb') as f:
                            return self._send(200, mimetypes.guess_type(path)[0]
                                              or 'image/jpeg', f.read(), cache)
                    return self._send(200, 'image/svg+xml', PLACEHOLDER, cache)
                img = music.cover(g('id'))
                if not img:
                    return self._send(200, 'image/svg+xml', PLACEHOLDER, cache)
                return self._send(200, img[0], img[1], cache)
            if p.path == '/api/hot':
                return self._json(online_songs('', 80))
            if p.path == '/api/search':
                return self._json(search_songs(g('q').strip()))
            if p.path == '/api/local':
                return self._json(local_songs())
            if p.path == '/api/now':
                return self._json(now())
            if p.path == '/api/queue':
                return self._json(queue())
            if p.path == '/api/add':
                k = g('k')
                if k:
                    it = next((x for x in local_songs() if x['k'] == k), None)
                    if not it:
                        return self._send(404, 'text/plain; charset=utf-8', '歌曲不存在')
                else:
                    sid, n, a = g('id'), g('n'), g('a')
                    if not sid:
                        return self._send(400, 'text/plain; charset=utf-8', 'bad id')
                    it = {'t': n + (' - ' + a if a else ''),
                          'play': '/stream?id=' + urllib.parse.quote(sid),
                          'tracks': {'orig': '/stream?id=' + urllib.parse.quote(sid)},
                          'n': n, 'a': a, 'cover': '/cover?id=' + urllib.parse.quote(sid),
                          'lrc': '/lrc?id=' + urllib.parse.quote(sid), 'video': False}
                return self._send(200, 'text/plain; charset=utf-8', add_song(it))
            if p.path == '/api/track':
                return self._send(200, 'text/plain; charset=utf-8',
                                  set_track(g('mode', 'inst')))
            if p.path == '/api/prev':
                return self._send(200, 'text/plain; charset=utf-8', move(delta=-1))
            if p.path == '/api/next':
                return self._send(200, 'text/plain; charset=utf-8', move(delta=1))
            if p.path == '/api/ended':
                cur = now()
                if g('seq') == str(cur['seq']) and cur['play']:
                    return self._send(200, 'text/plain; charset=utf-8', move(delta=1))
                return self._send(200, 'text/plain; charset=utf-8', 'skip')
            if p.path == '/api/jump':
                return self._send(200, 'text/plain; charset=utf-8',
                                  move(index=int(g('i', '-1'))))
            if p.path == '/api/del':
                return self._send(200, 'text/plain; charset=utf-8',
                                  move(index=int(g('i', '-1')), remove=True))
            if p.path == '/api/clear':
                return self._send(200, 'text/plain; charset=utf-8', move(clear=True))
            if p.path == '/media':
                key = urllib.parse.unquote(g('k'))
                path = os.path.realpath(os.path.join(MUSIC, key))
                root = os.path.realpath(MUSIC)
                if not path.startswith(root + os.sep) or not os.path.isfile(path):
                    return self._send(404, 'text/plain; charset=utf-8', 'not found')
                return self._bytes(path, self.headers.get('Range'))
            if p.path == '/stream':
                return self._stream(g('id'))
            if p.path == '/lrc':
                if g('k'):
                    return self._send(200, 'text/plain; charset=utf-8',
                                      self._lrc_file(urllib.parse.unquote(g('k'))))
                return self._send(200, 'text/plain; charset=utf-8', music.lyric(g('id')))
            return self._send(404, 'text/plain; charset=utf-8', 'not found')
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            try:
                self._send(500, 'text/plain; charset=utf-8', 'err: %s' % e)
            except Exception:
                pass

    def do_POST(self):
        p = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(p.query)
        g = lambda k, d='': (q.get(k) or [d])[0]
        try:
            if about.handle_post(self, p.path, g):
                return
            return self._send(404, 'text/plain; charset=utf-8', 'not found')
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            try:
                self._send(500, 'text/plain; charset=utf-8', 'err: %s' % e)
            except Exception:
                pass


if __name__ == '__main__':
    print('diange listening on 0.0.0.0:%d  music=%s' % (PORT, MUSIC), flush=True)
    ThreadingHTTPServer(('0.0.0.0', PORT), H).serve_forever()
