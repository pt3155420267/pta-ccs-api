"""pta-ccs-api 命令行入口。

将 PTA (pintia.cn) 题目集(比赛)适配为 ICPC Contest API 2023-06 服务,
供 ICPC Tools CDS 等工具连接。
"""

import argparse
import os
import sys

from .adapter import (DEFAULT_PALETTE, COLORS_CSV_HEADER, load_colors_csv, log,
                      palette_hash)
from .assets import compute_assets_hash, ensure_assets_dir
from .pintia import PtaClient
from .server import make_server
from .store import ContestStore
from .sync import Syncer
from . import __version__


def build_parser():
    parser = argparse.ArgumentParser(
        prog='pta-ccs',
        description='将 PTA (pintia.cn) 题目集(比赛)适配为 CCS (ICPC Contest API 2023-06) 服务',
    )
    parser.add_argument('problem_set', nargs='?', default=None,
                        help='PTA 题目集(比赛) ID,即题目集页面 URL 中 problem-sets 后的数字')
    parser.add_argument('--problem-set', dest='problem_set_opt', default=None,
                        help='同上(可选写法)')
    parser.add_argument('--cookie', default=None, metavar='COOKIE',
                        help='PTA Cookie,如 "PTASession=xxxx";也可用环境变量 PTA_COOKIE 提供')
    parser.add_argument('--host', default='0.0.0.0', help='HTTP 监听地址(默认 0.0.0.0)')
    parser.add_argument('--port', type=int, default=8890, help='HTTP 监听端口(默认 8890)')
    parser.add_argument('--user', default='pintia', help='Basic 认证用户名(默认 pintia)')
    parser.add_argument('--password', default='pintia-pass', help='Basic 认证密码(默认 pintia-pass)')
    parser.add_argument('--rate', type=float, default=1.0,
                        help='访问 PTA 的最小请求间隔秒数(默认 1.0,请勿设置过小)')
    parser.add_argument('--limit', type=int, default=50, help='分页大小(默认 50)')
    parser.add_argument('--poll-interval', type=float, default=15,
                        help='比赛进行中的增量轮询间隔秒数(默认 15)')
    parser.add_argument('--poll-interval-ended', type=float, default=60,
                        help='比赛结束后的轮询间隔秒数(默认 60)')
    parser.add_argument('--data-dir', default='data', help='缓存目录(默认 ./data)')
    parser.add_argument('--proxy', default=None, metavar='URL',
                        help='访问 PTA 使用的代理(默认直连,忽略系统代理设置)')
    parser.add_argument('--colors', default=None, metavar='FILE',
                        help='题目配色 CSV(label,颜色名,RGB),默认 ./colors.csv,'
                             '不存在时自动生成默认配色(A~M 各不同颜色)')
    parser.add_argument('--assets-dir', default='assets', metavar='DIR',
                        help='本地图片资源目录(CDP 布局,见 assets/README.txt),默认 ./assets')
    parser.add_argument('--no-details', action='store_true',
                        help='不抓取提交详情(将没有 runs/逐测试点数据,大幅减少请求量)')
    parser.add_argument('--no-poll', action='store_true', help='不轮询 PTA,仅用本地缓存对外服务')
    parser.add_argument('--no-serve', action='store_true', help='仅同步数据后退出,不启动 HTTP 服务')
    parser.add_argument('--reset', action='store_true', help='删除本地缓存并重新全量同步')
    parser.add_argument('--verbose', action='store_true', help='输出更详细的日志')
    parser.add_argument('--version', action='version', version='pta-ccs-api %s' % __version__)
    return parser


def ensure_palette(args):
    """加载题目配色 CSV;默认文件不存在时生成默认配色(A~M)。返回 (palette, 颜色哈希)。"""
    explicit = args.colors is not None
    path = args.colors or 'colors.csv'
    if not os.path.exists(path):
        if explicit:
            build_parser().error('配色文件不存在: %s' % path)
        with open(path, 'w', encoding='utf-8', newline='\n') as fp:
            fp.write(COLORS_CSV_HEADER)
            for label, color, rgb in DEFAULT_PALETTE:
                fp.write('%s,%s,%s\n' % (label, color, rgb))
        log('已生成默认题目配色文件 %s(A~M 各不同颜色,可自行修改后重启)', path)
    palette = load_colors_csv(path)
    if not palette:
        log('警告: 配色文件 %s 为空,题目将使用默认白色', path)
    return palette, palette_hash(palette)


def main(argv=None):
    args = build_parser().parse_args(argv)
    problem_set_id = args.problem_set_opt or args.problem_set
    if not problem_set_id:
        build_parser().error('必须提供题目集 ID,例如: pta-ccs 2000172178531069952')

    cookie = args.cookie or os.environ.get('PTA_COOKIE')
    if not cookie and not args.no_poll:
        build_parser().error('必须通过 --cookie 或环境变量 PTA_COOKIE 提供 PTA Cookie '
                             '(登录 pintia.cn 后从浏览器 DevTools 复制 PTASession)')

    client = PtaClient(cookie or 'PTASession=none', min_interval=args.rate, page_limit=args.limit,
                       proxy=args.proxy)
    store = ContestStore(problem_set_id, args.data_dir)
    palette, colors_hash = ensure_palette(args)
    ensure_assets_dir(args.assets_dir)
    assets_hash = compute_assets_hash(args.assets_dir, colors_hash)
    syncer = Syncer(client, store, args, palette=palette, assets_dir=args.assets_dir)

    try:
        if args.reset:
            store.reset()
            log('已清除本地缓存,开始重新同步')

        status = store.load()
        if status == 'loaded' and store.assets_hash != assets_hash:
            # 配色或图片资源与缓存时不同:仅重建事件日志,不重新请求 PTA
            log('检测到配色/图片资源变化,重建事件日志 ...')
            status = 'rebuild'
        store.assets_hash = assets_hash

        if status == 'loaded':
            log('已从事件日志恢复 %s 的数据(%d 条事件, %d 条提交)',
                store.contest_id, len(store.events), len(store.submissions))
        elif status == 'resume':
            if not syncer.resume_sync():
                syncer.initial_sync()
        elif status == 'rebuild':
            if not syncer.rebuild_from_cache():
                syncer.initial_sync()
        else:
            try:
                syncer.initial_sync()
            except Exception as e:
                log('同步失败: %s', e)
                if store.events:
                    log('已保留部分数据;删除 %s 可重新全量同步' % store.base_dir)
                sys.exit(1)

        if not args.no_poll:
            syncer.start_poll_thread()

        if args.no_serve:
            log('同步完成(--no-serve,不启动 HTTP 服务)')
            return

        server = make_server(args.host, args.port, store, args.user, args.password, args.verbose,
                             assets_dir=args.assets_dir)
        log('ICPC Contest API 服务已启动: http://%s:%d/api/contests/%s', args.host, args.port, problem_set_id)
        log('ICPC Tools CDS 连接地址: http://<本机IP>:%d/api/contests/%s (用户 %s)', args.port, problem_set_id, args.user)
        server.serve_forever()
    except KeyboardInterrupt:
        log('收到中断,退出')
    finally:
        try:
            server.server_close()
        except Exception:
            pass


if __name__ == '__main__':
    main()
