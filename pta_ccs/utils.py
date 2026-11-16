"""通用小工具。"""

import sys
from datetime import datetime

# Linux 下若终端 locale 非 UTF-8,中文日志会编码失败;统一强制 UTF-8(Windows 下同样安全)
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass


def log(msg, *args):
    """带时间戳的行式日志输出。"""
    stamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    if args:
        msg = msg % args
    print('[%s] %s' % (stamp, msg), flush=True)
