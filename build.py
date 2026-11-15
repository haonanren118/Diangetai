# -*- coding: utf-8 -*-
"""构建布局：生成 native fpk 工程（宿主直接跑 Python，不依赖 Docker）与独立 Docker 部署包。

元数据集中在本段常量，切换「并存 / 替换已装应用」时只需改 APPNAME 与 PORT。
"""
import os
import json
import shutil
import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / 'app-src'

# ---- 应用元数据（用户指定） ----
APPNAME = 'familyktv'          # 内部标识，全小写；新应用，不覆盖已发布的 openhwlab-ktv 2.0.0-2
DISPLAY = '点歌台'
VERSION = '0.0.8'          # 全新版本标识：IPv4 连接层修复，不覆盖已发布版本
AUTHOR = 'haonanren118'        # 作者署名（GitHub / Gitee 身份）
PORT = 18090                   # 宿主机端口；与已装的 Docker 版 18089 并存，互不冲突
INNER_PORT = 8089              # 仅独立 Docker 部署包的容器内端口
SHARE_NAME = 'diangetai'       # 用户可见的共享文件夹名，不含内部应用标识
CHANGELOG = ('%s 修复部分 NAS 上歌源与热歌榜一直加载：外网连接强制走 IPv4，'
             '避免机器有 IPv6 地址但出口不通时每个请求先等超时再回退；'
             '更新源检查与下载同样受益；纯 IPv6 环境可设 KTV_IPV4_ONLY=0 关闭'
             % VERSION)

FPK = ROOT / APPNAME
DOCKER = ROOT / (APPNAME + '-docker')
ZIPNAME = APPNAME + '-docker-' + VERSION
TRASH = ROOT / '_test' / '_discard'
TRASH.mkdir(parents=True, exist_ok=True)


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.strip('\n').encode('utf-8') + b'\n')


def copy_app(dest):
    shutil.copytree(SRC, dest, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))


# 版本文件随代码一起分发：关于页读它，热更新后由新包覆盖，应用中心版本同步 manifest。
write(SRC / 'version.json', json.dumps(
    {'app': DISPLAY, 'version': VERSION, 'author': AUTHOR, 'changelog': CHANGELOG},
    ensure_ascii=False))


def discard_ds_store():
    n = 0
    for p in list(FPK.rglob('.DS_Store')):
        os.replace(str(p), str(TRASH / ('ds_%d' % n)))
        n += 1
    return n


# ---------- RGBA PNG 图标（纯标准库绘制，3x 超采样抗锯齿） ----------
def shade(px, py):
    r = 0.19
    cx, cy = min(max(px, r), 1 - r), min(max(py, r), 1 - r)
    if (px - cx) ** 2 + (py - cy) ** 2 > r * r:
        return (0, 0, 0, 0)
    t = (px * 0.55 + py * 0.45)
    bg = (int(43 - 30 * t), int(35 + 39 * t), int(82 - 9 * t), 255)
    hx, hy = (px - 0.41) / 0.135, (py - 0.69) / 0.10
    if hx * hx + hy * hy <= 1:
        return (46, 224, 182, 255)
    if 0.515 <= px <= 0.555 and 0.25 <= py <= 0.69:
        return (238, 246, 255, 255)
    if 0.535 <= px <= 0.75 and 0.25 <= py <= 0.44:
        top = 0.25 + (px - 0.535) * 0.75
        if top <= py <= top + 0.075 or (py >= top and py <= 0.44 and px >= 0.70):
            return (238, 246, 255, 255)
    if (px - 0.78) ** 2 + (py - 0.24) ** 2 < 0.0022:
        return (232, 81, 122, 255)
    return bg


def png(size, path):
    ss = 3
    raw = bytearray()
    for y in range(size):
        row = bytearray(b'\x00')
        for x in range(size):
            acc = [0, 0, 0, 0]
            for sy in range(ss):
                for sx in range(ss):
                    c = shade((x + (sx + .5) / ss) / size,
                              (y + (sy + .5) / ss) / size)
                    for i in range(4):
                        acc[i] += c[i]
            n = ss * ss
            row += bytes(v // n for v in acc)
        raw += row

    def chunk(tag, data):
        return (struct.pack('>I', len(data)) + tag + data +
                struct.pack('>I', zlib.crc32(tag + data) & 0xffffffff))

    data = (b'\x89PNG\r\n\x1a\n' +
            chunk(b'IHDR', struct.pack('>IIBBBBB', size, size, 8, 6, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(bytes(raw), 9)) +
            chunk(b'IEND', b''))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


# ---------- native fpk 工程（全新目录，不复用旧的 docker 工程） ----------
shutil.rmtree(FPK, ignore_errors=True)
FPK.mkdir(parents=True)
(FPK / 'wizard').mkdir(exist_ok=True)
copy_app(FPK / 'app' / 'bin')
png(64, FPK / 'ICON.PNG')
png(256, FPK / 'ICON_256.PNG')
png(64, FPK / 'app' / 'ui' / 'images' / 'icon_64.png')
png(256, FPK / 'app' / 'ui' / 'images' / 'icon_256.png')
print('discarded .DS_Store:', discard_ds_store())

write(FPK / 'manifest', '''
appname               = {app}
version               = {ver}
display_name          = {name}
desc                  = 点歌台：逐字歌词染色、粒子大屏、进度拖动、拼音搜歌、原唱/伴奏切换、扫码与遥控器点歌；原生运行，不依赖 Docker
platform              = x86
source                = thirdparty
maintainer            = {author}
distributor           = {author}
desktop_uidir         = ui
desktop_applaunchname = {app}.Application
service_port          = {port}
checkport             = true
changelog             = {chg}
'''.format(app=APPNAME, ver=VERSION, name=DISPLAY, author=AUTHOR, port=PORT, chg=CHANGELOG))

write(FPK / 'app' / 'ui' / 'config', '''
{{
  ".url": {{
    "{app}.Application": {{
      "title": "{name}",
      "icon": "images/icon_{{0}}.png",
      "type": "url",
      "protocol": "http",
      "port": "{port}",
      "url": "/",
      "allUsers": true
    }}
  }}
}}
'''.format(app=APPNAME, name=DISPLAY, port=PORT))

# native 没有 docker-project：appcenter 不托管 compose，进程由 cmd/main 管理
# 共享文件夹名独立出来：用户可见路径里不出现内部应用标识。
write(FPK / 'config' / 'resource', '''
{{
  "data-share": {{
    "shares": [
      {{ "name": "{share}" }}
    ]
  }}
}}
'''.format(share=SHARE_NAME))

write(FPK / 'config' / 'privilege', '''
{
  "defaults": { "run-as": "root" }
}
''')

# 飞牛把 data-share 做成符号链接：/var/apps/<app>/shares/<share> -> /vol1/@appshare/<share>；
# 而 TRIM_APPDEST 指向代码目录 /vol1/@appcenter/<app>，两者不是同一处。
# 必须用规范规定的固定路径，否则用户放进共享目录的歌曲服务读不到。
SHARE_EXPR = '"/var/apps/%s/shares/%s"' % (APPNAME, SHARE_NAME)
write(FPK / 'cmd' / 'main', '''
#!/bin/bash
# 原生服务：直接用飞牛宿主 Python 3 运行，无需 Docker。
LOG_FILE="${{TRIM_PKGVAR}}/info.log"
PID_FILE="${{TRIM_PKGVAR}}/app.pid"
APP_BIN="${{TRIM_APPDEST}}/bin/server.py"
SHARE_DIR={share}
PYTHON="/usr/bin/python3"
[ -x "$PYTHON" ] || PYTHON="$(command -v python3)"

log_msg() {{ echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" >> "$LOG_FILE"; }}

check_process() {{
    local pid=$1
    kill -0 "$pid" 2>/dev/null
}}

status() {{
    if [ -f "$PID_FILE" ]; then
        pid=$(head -n 1 "$PID_FILE" | tr -d '[:space:]')
        if check_process "$pid"; then return 0; fi
        rm -f "$PID_FILE"
    fi
    return 1
}}

start_process() {{
    if status; then return 0; fi
    mkdir -p "$SHARE_DIR/music" "$SHARE_DIR/data" "$TRIM_PKGVAR"
    export KTV_PORT="${{TRIM_SERVICE_PORT:-{port}}}"
    export KTV_SHARE="$SHARE_DIR"
    export PYTHONUNBUFFERED=1
    log_msg "start $APP_BIN port=$KTV_PORT share=$KTV_SHARE python=$PYTHON"
    nohup "$PYTHON" "$APP_BIN" >> "$LOG_FILE" 2>&1 &
    printf "%s" "$!" > "$PID_FILE"
    return 0
}}

stop_process() {{
    log_msg "stop"
    if [ -r "$PID_FILE" ]; then
        pid=$(head -n 1 "$PID_FILE" | tr -d '[:space:]')
        if ! check_process "$pid"; then rm -f "$PID_FILE"; return 0; fi
        kill -TERM "$pid" >> "$LOG_FILE" 2>&1
        local count=0
        while check_process "$pid" && [ $count -lt 10 ]; do
            sleep 1
            count=$((count + 1))
        done
        if check_process "$pid"; then kill -KILL "$pid"; fi
        rm -f "$PID_FILE"
    fi
    return 0
}}

case $1 in
start)  start_process ;;
stop)   stop_process ;;
status) if status; then exit 0; else exit 3; fi ;;
*)      exit 1 ;;
esac
'''.format(share=SHARE_EXPR, port=PORT))

for name in ('install_init', 'install_callback', 'upgrade_init', 'upgrade_callback',
             'uninstall_init', 'uninstall_callback', 'config_init', 'config_callback'):
    body = '#!/bin/bash\nexit 0\n'
    if name in ('install_callback', 'upgrade_callback'):
        body = '''#!/bin/bash
# 预建共享目录并补执行位（fpk 在 Windows 打包时脚本权限位为 666）
mkdir -p "/var/apps/{app}/shares/{share}/music" "/var/apps/{app}/shares/{share}/data"
chmod +x "${{TRIM_APPDEST}}/../cmd/main" 2>/dev/null || true
exit 0
'''.format(app=APPNAME, share=SHARE_NAME)
    if name == 'uninstall_callback':
        body = '#!/bin/bash\n# 保留共享目录与已点队列，不删用户数据\nexit 0\n'
    write(FPK / 'cmd' / name, body)

# ---------- 独立 Docker 部署包（可选，给任意 Docker 主机用） ----------
shutil.rmtree(DOCKER, ignore_errors=True)
copy_app(DOCKER / 'app')
(DOCKER / 'music').mkdir(parents=True)
(DOCKER / 'data').mkdir(parents=True)
write(DOCKER / 'music' / '.gitkeep', '')
write(DOCKER / 'data' / '.gitkeep', '')

write(DOCKER / 'Dockerfile', '''
FROM python:3.11-alpine
ENV PYTHONUNBUFFERED=1 KTV_PORT={inner} KTV_SHARE=/shares
WORKDIR /app
COPY app/ /app/
RUN mkdir -p /shares/music /shares/data
EXPOSE {inner}
CMD ["python3", "server.py"]
'''.format(inner=INNER_PORT))

write(DOCKER / 'docker-compose.yaml', '''
services:
  ktv:
    build: .
    image: {app}:{ver}
    container_name: {app}
    restart: unless-stopped
    ports:
      - "{port}:{inner}"
    volumes:
      - ./music:/shares/music
      - ./data:/shares/data
'''.format(app=APPNAME, ver=VERSION, port=PORT, inner=INNER_PORT))

write(DOCKER / 'README.md', '''
# {name} · 独立 Docker 部署包（可选）

飞牛 fpk 已是**原生版**，不需要 Docker；本包给任意 Docker 主机使用。

## 部署

在解压后的目录执行：

    docker compose up -d --build

## 访问

- 点歌台（手机）：http://<NAS_IP>:{port}/
- 大屏播放端（电视 / 平板）：http://<NAS_IP>:{port}/tv

首次打开大屏需点击一次「开启大屏」，浏览器才允许自动播放。
大屏右上角有二维码，手机扫码即可进入点歌台；手机与电视需在同一局域网。
若大屏用 localhost 打开导致二维码不可用，在「点歌」面板底部把地址改成本机局域网 IP 再点「应用并换码」。

## 四种点歌方式

- 手机扫码：扫大屏右上角二维码
- 鼠标：点歌台 / 大屏「点歌」面板直接点
- 遥控器：方向键移动焦点、OK 确认、返回键关闭面板、数字键 1~9 点第 N 首
- USB 键盘：S 搜歌、Q 已点队列、N 切歌、P 上一首、T 原唱/伴奏、空格播放暂停、左右键快进退 5 秒

## 已点队列

点歌台切到「已点」标签：可看完整队列、当前播放高亮、跳转播放、删除、清空；2 秒自动刷新。
大屏「点歌」面板里的「已点队列」同样可切歌 / 删除。

## 逐字歌词与画面

逐字歌词染色（普通 LRC 按行内进度渐进，带 `<mm:ss.xx>` 时间戳的 LRC 按字精确染色），
显示上一行 / 当前行 / 下一行。大屏是透明 canvas 粒子层，音频、视频、无歌曲时都持续绘制，不会黑屏。
进度条支持鼠标 / 触摸 / 遥控器方向键 / USB 键盘拖动，拖动时显示目标时间。

## 本地曲库

把歌曲放进 music/ 目录，命名 `001-歌名-歌手.mp4`（或 .mp3），同名 .lrc 为歌词；
同名 .jpg/.png 或目录下的 cover.jpg 会作为歌曲封面，在线歌曲自动取专辑封面。
支持 mp3 / mp4 / m4a / flac / wav / aac / ogg / mkv / avi / mov。
已点队列保存在 data/state.json，重启后自动恢复。

## 原唱 / 伴奏切换

在大屏或手机点「切伴奏」即可切换，切换时歌词、封面与播放进度都不中断。
- 曲库里同时放 `001-歌名-原唱.mp3` 与 `001-歌名-伴奏.mp3` 时，自动使用真实伴奏音轨（效果最好）。
- 没有配对伴奏文件时，大屏用浏览器 Web Audio 对左右声道做反相抵消，实时生成消人声伴奏，本地与在线歌曲都可用；按钮悬停提示会标注当前模式。
- 再点「切回原唱」立即恢复；切歌 / 跳转 / 清空队列后自动回到原唱。

## 关于、曲库文件夹与版本管理

- 关于页：http://<NAS_IP>:{port}/about —— 查看代码版本、应用中心版本、作者、端口、共享目录与更新日志；可配置更新源、上传 fpk 一键升级、失败自动回滚。
- 曲库文件夹：http://<NAS_IP>:{port}/files —— 查看共享目录路径、复制路径、直接从手机上传歌曲 / 封面 / 歌词；点歌台「本地曲库」标签顶部也有入口，大屏「点歌」面板底部同样有链接。
- 飞牛 fpk 版支持应用内热更新：上传新版本 fpk 后点「立即更新」，应用会走 appcenter-cli install-local 正规链路自动完成停止→安装→启动，应用中心版本号与代码版本保持一致；期间已点队列、曲库与封面保留，失败自动回滚。
- 独立 Docker 部署不支持应用内热更新（关于页会标注），升级请替换部署包后重新 `docker compose up -d --build`。

## 说明

在线曲库使用网易云公开接口，只保留实测可播放的曲目；
仅供个人家庭使用，音源版权归各平台所有。
'''.format(name=DISPLAY, port=PORT))

zip_path = shutil.make_archive(str(ROOT / ZIPNAME), 'zip',
                               root_dir=str(ROOT), base_dir=DOCKER.name)
print('fpk files:', sorted(str(p.relative_to(FPK)) for p in FPK.rglob('*') if p.is_file()))
print('docker files:', sorted(str(p.relative_to(DOCKER)) for p in DOCKER.rglob('*') if p.is_file()))
print('zip:', zip_path, os.path.getsize(zip_path), 'bytes')
print('meta: appname=%s version=%s display=%s author=%s port=%s' %
      (APPNAME, VERSION, DISPLAY, AUTHOR, PORT))
