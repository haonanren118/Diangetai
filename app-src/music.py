# -*- coding: utf-8 -*-
"""点歌台在线曲库。纯标准库，无第三方依赖。"""
import html
import json
import re
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


def _req(url, method='GET', ref=None, extra=None):
    h = {'User-Agent': UA}
    if ref:
        h['Referer'] = ref
    if extra:
        h.update(extra)
    return urllib.request.urlopen(
        urllib.request.Request(url, headers=h, method=method),
        context=CTX, timeout=12)


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
