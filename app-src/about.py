# -*- coding: utf-8 -*-
"""关于 / 曲库文件夹 / 在线升级。

飞牛 fpk 版的升级走 appcenter-cli install-local 正规安装链路：应用内上传
新版本 fpk 后，由独立更新进程完成「停止→安装→启动」，应用中心版本号、
manifest 与代码版本三者一致；共享目录与已点队列不参与安装，保持不动。
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.parse
import urllib.request

import multipart
import music

APPNAME = 'familyktv'
BASE = os.path.dirname(os.path.abspath(__file__))
VERFILE = os.path.join(BASE, 'version.json')
APPROOT = os.environ.get('KTV_APPROOT', '/var/apps/' + APPNAME)
MANIFEST = APPROOT + '/manifest'
APPDEST = os.environ.get('KTV_APPDEST', '/vol1/@appcenter/' + APPNAME)
SHARE = os.environ.get('KTV_SHARE', '/shares')
MUSIC = os.path.join(SHARE, 'music')
DATA = os.path.join(SHARE, 'data')
# 用户可见的目录文案：只显示共享文件夹名，不暴露内部应用标识。
FOLDER = os.path.basename(os.path.normpath(SHARE)) or 'diangetai'
LABEL = '共享空间 / %s / music' % FOLDER
UPDATES = os.path.join(DATA, 'updates')
BACKUPS = os.path.join(DATA, 'backups')
STATUS = os.path.join(DATA, 'update_status.json')
SOURCE = os.path.join(DATA, 'update_source.txt')
PENDING = os.path.join(UPDATES, 'pending.fpk')
PENDING_META = os.path.join(UPDATES, 'pending.json')
LOG = os.path.join(DATA, 'update.log')
PORT = int(os.environ.get('KTV_PORT', '8089'))
MEDIA_EXT = ('.mp3', '.m4a', '.flac', '.wav', '.aac', '.ogg', '.mp4', '.mkv',
             '.avi', '.mov', '.jpg', '.jpeg', '.png', '.webp', '.bmp', '.lrc')
PKG_EXT = ('.fpk', '.tgz', '.gz')
SKELETON = ('manifest', 'ICON.PNG', 'ICON_256.PNG', 'cmd', 'config', 'wizard')

for d in (DATA, UPDATES, BACKUPS, MUSIC):
    os.makedirs(d, exist_ok=True)


def _kv(text, key):
    for line in text.splitlines():
        m = re.match(r'\s*%s\s*=\s*(.*)$' % re.escape(key), line)
        if m:
            return m.group(1).strip()
    return ''


def _manifest(path):
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            return f.read()
    except Exception:
        return ''


def code_version():
    try:
        with open(VERFILE, encoding='utf-8') as f:
            d = json.load(f)
        if d.get('version'):
            return d
    except Exception:
        pass
    return {'version': '0.0.0', 'changelog': '', 'app': '点歌台',
            'author': 'haonanren118'}


def vtuple(v):
    try:
        return tuple(int(x) for x in re.findall(r'\d+', v)[:3])
    except Exception:
        return (0, 0, 0)


def newer(a, b):
    return vtuple(a) > vtuple(b)


def info():
    cv = code_version()
    acm = _manifest(MANIFEST)
    acv = _kv(acm, 'version')
    return {
        'app': cv.get('app', '点歌台'),
        'version': cv['version'],
        'changelog': cv.get('changelog', ''),
        'acVersion': acv,
        'acChangelog': _kv(acm, 'changelog'),
        'sync': (not acv) or acv == cv['version'],
        'fpk': bool(acv),
        'author': cv.get('author', 'haonanren118'),
        'port': PORT,
        'python': sys.version.split()[0],
        'share': SHARE,
        'shareLabel': LABEL,
        'folder': FOLDER,
        'music': MUSIC,
        'source': _read(SOURCE).strip(),
    }


def _read(path, default=''):
    try:
        with open(path, encoding='utf-8') as f:
            return f.read()
    except Exception:
        return default


def _write(path, text):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(text)
    os.replace(tmp, path)


def _jsonfile(path, default=None):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {} if default is None else default


def _safe_extract(t, dest):
    for m in t.getmembers():
        n = m.name.replace('\\', '/')
        if n.startswith('/') or '..' in n.split('/'):
            continue
        try:
            t.extract(m, dest)
        except Exception:
            pass


def fpk_meta(path):
    """读取 fpk 内的 manifest（appname / version / changelog）。"""
    with tarfile.open(path) as t:
        try:
            f = t.extractfile('manifest')
            text = f.read().decode('utf-8', 'replace') if f else ''
        except KeyError:
            text = ''
        has_app = any(n == 'app.tgz' for n in t.getnames())
    return {'appname': _kv(text, 'appname'), 'version': _kv(text, 'version'),
            'changelog': _kv(text, 'changelog'), 'hasApp': has_app}


def accept_package(src):
    """校验并登记上传/下载的更新包。"""
    meta = fpk_meta(src)
    if meta['appname'] != APPNAME or not meta['hasApp']:
        raise ValueError('不是有效的点歌台 fpk 更新包')
    if not meta['version']:
        raise ValueError('更新包缺少版本号')
    if src != PENDING:
        shutil.move(src, PENDING)
    data = dict(meta, size=os.path.getsize(PENDING),
                current=info()['version'], newer=newer(meta['version'], info()['version']),
                ts=int(time.time()))
    _jsonwrite(PENDING_META, data)
    return data


def _jsonwrite(path, obj):
    _write(path, json.dumps(obj, ensure_ascii=False))


def _stage(fpksrc, version):
    """把 fpk 展开成 install-local 可识别的工程目录。"""
    work = os.path.join(UPDATES, 'stage-' + version)
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    for item in SKELETON:
        src = os.path.join(APPROOT, item)
        dst = os.path.join(work, item)
        if os.path.isdir(src):
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        elif os.path.isfile(src):
            shutil.copy2(src, dst)
    with tarfile.open(fpksrc) as t:
        _safe_extract(t, work)          # fpk 自带新版本 manifest，会覆盖骨架
    tgz = os.path.join(work, 'app.tgz')
    os.makedirs(os.path.join(work, 'app'), exist_ok=True)
    with tarfile.open(tgz) as t:
        _safe_extract(t, os.path.join(work, 'app'))
    os.remove(tgz)
    return work


def _backup(version):
    """备份当前运行版本，供失败回滚或手动回滚使用。"""
    bk = os.path.join(BACKUPS, version)
    shutil.rmtree(bk, ignore_errors=True)
    os.makedirs(bk)
    for item in SKELETON:
        src = os.path.join(APPROOT, item)
        dst = os.path.join(bk, item)
        if os.path.isdir(src):
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        elif os.path.isfile(src):
            shutil.copy2(src, dst)
    # install-local 打包 app.tgz 时要求 app/ui 存在（manifest 的 desktop_uidir）。
    # 除 bin/ 外把部署目录的 ui/、config/ 等原样带进备份，否则回滚安装会失败。
    os.makedirs(os.path.join(bk, 'app', 'ui'), exist_ok=True)
    try:
        for item in os.listdir(APPDEST):
            if item == 'bin' or item.startswith('__'):
                continue
            src = os.path.join(APPDEST, item)
            if os.path.isdir(src):
                shutil.copytree(src, os.path.join(bk, 'app', item), dirs_exist_ok=True,
                                ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    except OSError:
        pass
    shutil.copytree(BASE, os.path.join(bk, 'app', 'bin'),
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    return bk


def _launch(stage, backup, version):
    """启动独立更新进程（服务会被 install-local 停止，进程必须脱离服务）。"""
    if not os.path.isfile(MANIFEST):
        raise RuntimeError('当前为独立部署，热更新仅飞牛 fpk 版可用')
    updater = os.path.join(DATA, 'updater.py')
    shutil.copy2(os.path.join(BASE, 'updater.py'), updater)
    _jsonwrite(STATUS, {'phase': 'starting', 'version': version,
                        'detail': '更新进程已启动…', 'error': '', 'ts': int(time.time())})
    cmd = [sys.executable, updater, stage, backup, STATUS, APPNAME,
           version, str(PORT), LOG]
    with open(LOG, 'ab') as out:
        subprocess.Popen(cmd, cwd=DATA, stdin=subprocess.DEVNULL,
                         stdout=out, stderr=subprocess.STDOUT,
                         start_new_session=True, close_fds=True)
    time.sleep(0.4)


def _status():
    d = _jsonfile(STATUS, {'phase': 'idle', 'version': '', 'detail': '',
                           'error': '', 'ts': 0})
    d['pending'] = _jsonfile(PENDING_META, None)
    d['current'] = info()['version']
    d['acVersion'] = _kv(_manifest(MANIFEST), 'version')
    d['sync'] = info()['sync']
    d['backups'] = sorted((x for x in os.listdir(BACKUPS)
                           if os.path.isdir(os.path.join(BACKUPS, x))), reverse=True)[:5]
    return d


def _url_ok(u):
    return bool(re.match(r'^https?://[\x21-\x7e]{1,400}$', u or ''))


def _check(url):
    if url and url != _read(SOURCE).strip():
        _write(SOURCE, url)
    src = _read(SOURCE).strip()
    if not src:
        return {'configured': False, 'hasUpdate': False,
                'tip': '未配置更新源，可直接在下方上传 fpk 更新包'}
    if not _url_ok(src):
        return {'configured': True, 'hasUpdate': False, 'error': '更新源地址不合法'}
    try:
        with music.urlopen(src, timeout=10) as r:
            d = json.loads(r.read(200000).decode('utf-8', 'replace'))
        latest = str(d.get('version', ''))
        cur = info()['version']
        return {'configured': True, 'hasUpdate': newer(latest, cur),
                'current': cur, 'latest': latest,
                'changelog': str(d.get('changelog', '')),
                'url': str(d.get('url', '')), 'source': src}
    except Exception as e:
        return {'configured': True, 'hasUpdate': False, 'error': '检查失败：%s' % e,
                'source': src}


def _fetch(url):
    if not _url_ok(url):
        raise ValueError('下载地址不合法')
    req = urllib.request.Request(url, headers={'User-Agent': 'diangetai-updater'})
    with music.urlopen(req, timeout=60) as r, open(PENDING + '.dl', 'wb') as f:
        left = 200 << 20
        while left > 0:
            b = r.read(65536)
            if not b:
                break
            f.write(b)
            left -= len(b)
    return accept_package(PENDING + '.dl')


def _file_list():
    out = []
    for root, _, files in os.walk(MUSIC):
        for fn in files:
            p = os.path.join(root, fn)
            try:
                st = os.stat(p)
            except OSError:
                continue
            ext = os.path.splitext(fn)[1].lower()
            if ext in ('.mp3', '.m4a', '.flac', '.wav', '.aac', '.ogg', '.mp4',
                       '.mkv', '.avi', '.mov'):
                kind = '歌曲'
            elif ext in ('.jpg', '.jpeg', '.png', '.webp', '.bmp'):
                kind = '封面'
            elif ext == '.lrc':
                kind = '歌词'
            else:
                kind = '其它'
            out.append({'name': os.path.relpath(p, MUSIC).replace('\\', '/'),
                        'size': st.st_size, 'kind': kind,
                        'mtime': int(st.st_mtime)})
    return sorted(out, key=lambda x: x['name'])


def handle_get(h, path, g):
    if path in ('/about', '/about.html'):
        h._page('about.html')
        return True
    if path in ('/files', '/files.html'):
        h._page('files.html')
        return True
    if path == '/api/about':
        h._json(info())
        return True
    if path == '/api/files':
        d = info()
        h._json({'path': MUSIC, 'share': SHARE, 'label': LABEL, 'folder': FOLDER,
                 'files': _file_list(), 'app': d['app'], 'fpk': d['fpk']})
        return True
    if path == '/api/update/status':
        h._json(_status())
        return True
    if path == '/api/update/check':
        h._json(_check(g('url').strip()))
        return True
    if path == '/api/update/source':
        h._json({'source': _read(SOURCE).strip()})
        return True
    return False


def handle_post(h, path, g):
    if path == '/api/files/upload':
        try:
            saved = multipart.save(h, MUSIC, MEDIA_EXT)
            h._json({'ok': True, 'files': [{'name': n, 'size': s} for n, s in saved],
                     'count': len(_file_list())})
        except Exception as e:
            h._json({'ok': False, 'error': str(e)})
        return True
    if path == '/api/update/upload':
        try:
            saved = multipart.save(h, UPDATES, PKG_EXT, max_size=200 << 20)
            h._json(dict({'ok': True}, **accept_package(os.path.join(UPDATES, saved[0][0]))))
        except Exception as e:
            h._json({'ok': False, 'error': '更新包无效：%s' % e})
        return True
    if path == '/api/update/fetch':
        try:
            h._json(dict({'ok': True}, **_fetch(g('url').strip())))
        except Exception as e:
            h._json({'ok': False, 'error': '下载失败：%s' % e})
        return True
    if path == '/api/update/apply':
        try:
            meta = _jsonfile(PENDING_META, None)
            if not meta or not os.path.isfile(PENDING):
                raise ValueError('请先上传更新包')
            if not newer(meta['version'], info()['version']):
                raise ValueError('更新包版本 %s 不高于当前 %s' %
                                 (meta['version'], info()['version']))
            stage = _stage(PENDING, meta['version'])
            bk = _backup(info()['version'])
            _launch(stage, bk, meta['version'])
            h._json({'ok': True, 'phase': 'installing', 'version': meta['version']})
        except Exception as e:
            h._json({'ok': False, 'error': str(e)})
        return True
    if path == '/api/update/rollback':
        try:
            bks = sorted((x for x in os.listdir(BACKUPS)
                          if os.path.isdir(os.path.join(BACKUPS, x))),
                         key=lambda x: os.path.getmtime(os.path.join(BACKUPS, x)),
                         reverse=True)
            if not bks:
                raise ValueError('没有可回滚的备份')
            bk = os.path.join(BACKUPS, bks[0])
            ver = _kv(_manifest(os.path.join(bk, 'manifest')), 'version') or bks[0]
            _launch(bk, bk, ver)
            h._json({'ok': True, 'phase': 'installing', 'version': ver})
        except Exception as e:
            h._json({'ok': False, 'error': str(e)})
        return True
    if path == '/api/update/source':
        u = g('url').strip()
        if u and not _url_ok(u):
            h._json({'ok': False, 'error': '地址不合法'})
        else:
            _write(SOURCE, u)
            h._json({'ok': True, 'source': u})
        return True
    return False
