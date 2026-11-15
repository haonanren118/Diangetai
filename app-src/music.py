# -*- coding: utf-8 -*-
"""点歌台在线曲库。纯标准库，无第三方依赖。"""
import html
import http.client
import json
import os
import re
import socket
import ssl
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
REF = 'https://music.163.com/'
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
PLAYLISTS = (3778678, 3779629, 19723756, 2884035)
_cache = {}
_url_cache = {}
_song_pic = {}
_cover_cache = {}

# 有些 NAS 拿到了 IPv6 地址与默认路由，但 IPv6 出口实际不通；glibc 默认把
# AAAA 排在 A 前面，socket.create_connection 会先连 IPv6 并一直等到超时
# （下面的请求超时是 12s）才回退 IPv4。一个榜单要问 4 个歌单，排队下来
# 就是 40s+ 的「一直加载中」。这里在连接层只解析 A 记录，不再依赖
# /etc/gai.conf 这类机器配置；纯 IPv6 环境可设 KTV_IPV4_ONLY=0 关闭。
IPV4_ONLY = os.environ.get('KTV_IPV4_ONLY', '1') != '0'


def _connect(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT,
            source_address=None):
    """只走 AF_INET 的 create_connection，签名与标准库一致。"""
    if not IPV4_ONLY:
        return socket.create_connection(address, timeout, source_address)
    host, port = address
    err = None
    for res in socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM):
        af, socktype, proto, _, sa = res
        sock = None
        try:
            sock = socket.socket(af, socktype, proto)
            if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                sock.settimeout(timeout)
            if source_address:
                sock.bind(source_address)
            sock.connect(sa)
            return sock
        except OSError as e:
            err = e
            if sock is not None:
                sock.close()
    if err is not None:
        raise err
    raise OSError('getaddrinfo returns an empty list')


class _V4HTTPConnection(http.client.HTTPConnection):
    def __init__(self, *args, **kwargs):
        http.client.HTTPConnection.__init__(self, *args, **kwargs)
        self._create_connection = _connect


class _V4HTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, *args, **kwargs):
        http.client.HTTPSConnection.__init__(self, *args, **kwargs)
        self._create_connection = _connect


class _V4HTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_V4HTTPConnection, req)


class _V4HTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_V4HTTPSConnection, req, context=self._context)


OPENER = urllib.request.build_opener(
    _V4HTTPHandler(), _V4HTTPSHandler(context=CTX))


def urlopen(req, timeout=12):
    """统一外网请求入口：更新源检查/下载也复用，避免同一类超时。"""
    return OPENER.open(req, timeout=timeout)


def _req(url, method='GET', ref=None, extra=None):
    h = {'User-Agent': UA}
    if ref:
        h['Referer'] = ref
    if extra:
        h.update(extra)
    return urlopen(urllib.request.Request(url, headers=h, method=method),
                   timeout=12)


def _json(url):
    return json.loads(_req(url, ref=REF).read().decode('utf-8', 'replace'))


def _clean(s):
    s = html.unescape(s or '').replace('\\u0026', '&').replace('\xa0', ' ')
    return re.sub(r'\s+', ' ', s).strip()


def _song(t):
    return {
        'src': 'ne',
        'id': str(t.get('id')),
        'name': _clean(t.get('name')),
        'artist': _clean('/'.join((a.get('name') or '') for a in (t.get('artists') or []))),
        'dur': t.get('duration', 0),
    }


def outer(sid):
    return 'https://music.163.com/song/media/outer/url?id=%s.mp3' % sid


def search(kw, n=30):
    try:
        d = _json('https://music.163.com/api/search/get/web?csrf_token=&type=1'
                  '&offset=0&limit=%d&s=%s' % (n, urllib.parse.quote(kw)))
        return [_song(t) for t in ((d.get('result') or {}).get('songs') or [])[:n]]
    except Exception:
        return []


def hot(n=120):
    out = []
    for pid in PLAYLISTS:
        try:
            d = _json('https://music.163.com/api/playlist/detail?id=%d' % pid)
            out += [_song(t) for t in ((d.get('result') or {}).get('tracks') or [])]
        except Exception:
            pass
    return out[:n]


def _probe(it):
    try:
        r = _req(outer(it['id']), 'HEAD', REF)
        it['playable'] = 'audio' in (r.headers.get('Content-Type') or '')
        r.close()
    except Exception:
        it['playable'] = False
    return it


def _dedupe(items):
    seen, out = set(), []
    for it in items:
        k = (it['name'], it['artist'])
        if k not in seen:
            seen.add(k)
            out.append(it)
    return out


def prefetch_covers(sids):
    """批量取专辑封面地址（搜索/榜单结果不含 picUrl，需查歌曲详情）。"""
    need = [s for s in sids if s not in _song_pic]
    for i in range(0, len(need), 30):
        chunk = need[i:i + 30]
        try:
            d = _json('https://music.163.com/api/song/detail?ids=%s'
                      % urllib.parse.quote('[' + ','.join(chunk) + ']'))
            for so in (d.get('songs') or []):
                pic = (so.get('album') or {}).get('picUrl')
                if pic:
                    _song_pic[str(so.get('id'))] = pic
        except Exception:
            pass


def cover(sid, size=400):
    """取封面图片字节；图片与地址均缓存，避免重复请求上游。"""
    hit = _cover_cache.get(sid)
    if hit and time.time() - hit[0] < 86400:
        return hit[1], hit[2]
    url = _song_pic.get(sid)
    if not url:
        prefetch_covers([sid])
        url = _song_pic.get(sid)
    if not url:
        return None
    u = url + ('&' if '?' in url else '?') + 'param=%dy%d' % (size, size)
    try:
        r = _req(u, ref=REF)
        ct = r.headers.get('Content-Type') or 'image/jpeg'
        b = r.read()
        r.close()
    except Exception:
        return None
    if 'image' not in ct or not b:
        return None
    _cover_cache[sid] = (time.time(), ct, b)
    return ct, b


def catalog(kw='', n=40):
    """返回已探测为可播的曲目；结果缓存 10 分钟。"""
    key = (kw, n)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    src = search(kw, min(n, 30)) if kw else hot(max(n, 120))
    with ThreadPoolExecutor(max_workers=8) as ex:
        ok = [x for x in ex.map(_probe, src) if x['playable']]
    out = _dedupe(ok)[:n]
    prefetch_covers([x['id'] for x in out])
    _cache[key] = (time.time(), out)
    return out


def resolve(sid):
    """取最终直链；缓存 5 分钟，便于拖动进度条后快速重连。"""
    hit = _url_cache.get(sid)
    if hit and time.time() - hit[0] < 300:
        return hit[1]
    try:
        r = _req(outer(sid), ref=REF)
        ct = r.headers.get('Content-Type') or ''
        url = r.geturl()
        r.close()
    except Exception:
        return None
    if 'audio' not in ct:
        return None
    _url_cache[sid] = (time.time(), url)
    return url


def find_inst(name, hint=''):
    """搜索可播放的伴奏版本，返回歌曲 id；找不到返回空串。"""
    try:
        src = search((name + ' 伴奏').strip(), 12)
    except Exception:
        return ''
    if not src:
        return ''
    with ThreadPoolExecutor(max_workers=6) as ex:
        ok = [x for x in ex.map(_probe, src) if x['playable']]
    key = re.sub(r'\s+', '', (hint or name))[:3]
    for x in ok:
        nm = x['name']
        if ('伴奏' in nm or 'instrumental' in nm.lower() or '纯音乐' in nm) \
                and (not key or key in nm):
            return x['id']
    for x in ok:
        if key and key in x['name']:
            return x['id']
    return ''


def lyric(sid):
    try:
        d = _json('https://music.163.com/api/song/lyric?os=pc&id=%s&lv=1&kv=1&tv=-1' % sid)
        return (d.get('lrc') or {}).get('lyric') or ''
    except Exception:
        return ''


def stream(sid, range_header=None):
    """打开上游音频流并透传 Range，供 HTTP 层转发。"""
    extra = {'Range': range_header} if range_header else None
    for _ in range(2):
        url = resolve(sid)
        if not url:
            return None
        try:
            return _req(url, ref=REF, extra=extra)
        except Exception:
            _url_cache.pop(sid, None)
    return None
