# -*- coding: utf-8 -*-
"""流式 multipart/form-data 解析：分块写临时文件、分块复制，不把大文件读进内存。"""
import os
import re
import tempfile
import urllib.parse


def _find_all(path, needle):
    """分块扫描文件，返回 needle 全部出现位置（处理跨块边界）。"""
    out = []
    overlap = len(needle) - 1
    with open(path, 'rb') as f:
        pos = 0
        prev = b''
        while True:
            chunk = f.read(1 << 20)
            if not chunk:
                break
            data = prev + chunk
            base = pos - len(prev)
            start = 0
            while True:
                i = data.find(needle, start)
                if i < 0:
                    break
                out.append(base + i)
                start = i + 1
            prev = data[-overlap:] if overlap else b''
            pos += len(chunk)
    return out


def save(handler, dest_dir, allowed, max_size=1 << 30):
    """读取请求体并保存所有文件字段，返回 [(文件名, 字节数), ...]。"""
    ctype = handler.headers.get('Content-Type', '')
    m = re.search(r'boundary=(?:"([^"]+)"|([^;\s]+))', ctype, re.I)
    if not m:
        raise ValueError('缺少 multipart boundary')
    boundary = b'--' + (m.group(1) or m.group(2)).encode('ascii', 'replace')
    try:
        length = int(handler.headers.get('Content-Length', '0'))
    except ValueError:
        length = 0
    if length <= 0 or length > max_size:
        raise ValueError('上传大小不合法（上限 %d MB）' % (max_size >> 20))

    os.makedirs(dest_dir, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.upload-', suffix='.part', dir=dest_dir)
    try:
        with os.fdopen(fd, 'wb') as f:
            left = length
            while left > 0:
                b = handler.rfile.read(min(65536, left))
                if not b:
                    break
                f.write(b)
                left -= len(b)
        marks = _find_all(tmp, boundary)
        if len(marks) < 2:
            raise ValueError('上传内容格式不正确')
        saved = []
        with open(tmp, 'rb') as f:
            for i in range(len(marks) - 1):
                start = marks[i] + len(boundary)
                end = marks[i + 1]
                f.seek(start)
                head = f.read(1024)
                j = head.find(b'\r\n\r\n')
                if j < 0:
                    continue
                hdr = head[:j].decode('utf-8', 'replace')
                fm = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";\r\n]+)', hdr, re.I)
                if not fm:
                    continue
                raw = urllib.parse.unquote(fm.group(1).strip().strip('"'))
                name = os.path.basename(raw.replace('\\', '/')).strip()
                if not name or name in ('.', '..'):
                    continue
                ext = os.path.splitext(name)[1].lower()
                if allowed and ext not in allowed:
                    raise ValueError('不允许的文件类型：' + ext)
                data_start = start + j + 4
                data_end = end - 2          # part 结尾的 CRLF 属于分隔符
                if data_end <= data_start:
                    continue
                dest = os.path.join(dest_dir, name)
                with open(dest, 'wb') as out:
                    f.seek(data_start)
                    left = data_end - data_start
                    while left > 0:
                        b = f.read(min(65536, left))
                        if not b:
                            break
                        out.write(b)
                        left -= len(b)
                saved.append((name, os.path.getsize(dest)))
        if not saved:
            raise ValueError('没有找到上传文件')
        return saved
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
