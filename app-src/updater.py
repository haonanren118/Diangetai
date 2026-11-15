# -*- coding: utf-8 -*-
"""独立更新进程：走飞牛正规安装链路 install-local，让应用中心版本号跟随。

由服务进程以 start_new_session 启动，脱离服务生命周期；更新期间服务会被
appcenter-cli 停止再拉起，脚本从共享目录里继续运行并把进度写入 status 文件。
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request

APPROOT = os.environ.get('KTV_APPROOT', '')


def status(path, phase, version, detail='', error=''):
    data = {'phase': phase, 'version': version, 'detail': detail,
            'error': error, 'ts': int(time.time())}
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


def cli():
    return shutil.which('appcenter-cli') or '/usr/local/bin/appcenter-cli'


def run_install(src, log):
    try:
        r = subprocess.run([cli(), 'install-local', '-d', src],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True, timeout=420)
        out = r.stdout or ''
        # appcenter-cli 安装失败时仍可能返回 0，必须同时检查输出里的错误标记，
        # 否则会把失败误判成成功、把「回滚失败」误写成「已回滚」。
        failed = (r.returncode != 0 or '[Error]' in out or 'Install Failed' in out)
        with open(log, 'a', encoding='utf-8') as f:
            f.write('\n===== install-local %s rc=%s failed=%s =====\n%s\n' %
                    (src, r.returncode, failed, out[-4000:]))
        return not failed, out[-500:]
    except Exception as e:
        with open(log, 'a', encoding='utf-8') as f:
            f.write('\n===== install-local exception =====\n%s\n' % e)
        return False, str(e)


def manifest_version(path):
    try:
        with open(path, encoding='utf-8') as f:
            for line in f:
                m = re.match(r'\s*version\s*=\s*(\S+)', line)
                if m:
                    return m.group(1)
    except Exception:
        pass
    return ''


def ac_version(appname):
    root = APPROOT or os.path.join('/var/apps', appname)
    return manifest_version(os.path.join(root, 'manifest'))


def wait_service(port, version, log, timeout=120):
    """等服务回来并确认代码版本；旧版没有 /api/about 时用 /ping 兜底。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen('http://127.0.0.1:%s/api/about' % port,
                                        timeout=3) as r:
                d = json.loads(r.read().decode('utf-8'))
                if d.get('version') == version:
                    return True
        except Exception:
            pass
        try:
            with urllib.request.urlopen('http://127.0.0.1:%s/ping' % port,
                                        timeout=3) as r:
                if r.status == 200 and ac_version(APPNAME) == version:
                    return True
        except Exception:
            pass
        time.sleep(2)
    with open(log, 'a', encoding='utf-8') as f:
        f.write('\n服务在 %ss 内未恢复到版本 %s\n' % (timeout, version))
    return False


def main():
    stage, backup, statfile, appname, version, port, log = sys.argv[1:8]
    global APPNAME
    APPNAME = appname
    status(statfile, 'installing', version, '正在安装新版本，服务会短暂中断…')
    ok, tail = run_install(stage, log)
    if ok:
        ok = wait_service(port, version, log)
        if ok and ac_version(appname) != version:
            ok = False
            tail = '应用中心版本号未跟随（%s）' % ac_version(appname)
    if ok:
        status(statfile, 'done', version, '已更新到 %s，应用中心版本号已同步' % version)
        return 0

    # 失败回滚：用更新前备份的工程目录再走一次正规安装链路
    status(statfile, 'rollingback', version, '更新失败，正在回滚…', tail)
    rok, _ = run_install(backup, log)
    bk_ver = manifest_version(os.path.join(backup, 'manifest'))
    if rok:
        wait_service(port, bk_ver, log, timeout=90)
        status(statfile, 'rolledback', version,
               '已回滚到更新前版本', '更新失败：' + tail)
    else:
        status(statfile, 'failed', version, '更新与回滚均失败，请检查日志', tail)
    return 1


if __name__ == '__main__':
    sys.exit(main())
