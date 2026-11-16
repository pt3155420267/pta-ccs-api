"""本地图片资源(CDP 目录布局)读取:
    <assets-dir>/contest/logo.<ext>              比赛图标(Resolver 主图/无照片队伍的默认照片)
    <assets-dir>/organizations/<orgId>/logo.<ext>  机构图标(同机构队伍共享)
    <assets-dir>/teams/<teamId>/photo.<ext>       队伍照片(teamId 支持 team-<uid> 与 <uid> 两种命名)
"""

import hashlib
import os

IMAGE_EXTS = ('.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.bmp')

MIME_TYPES = {
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.gif': 'image/gif',
    '.svg': 'image/svg+xml',
    '.webp': 'image/webp',
    '.bmp': 'image/bmp',
}

ASSETS_README = """本地图片资源目录(CDP 布局),由 pta-ccs-api 对外提供下载:

  contest/logo.<ext>                  比赛图标(Resolver 主页面图片,也是无照片队伍的默认照片)
  organizations/<orgId>/logo.<ext>    机构图标(该机构所有队伍共享)
  teams/<teamId>/photo.<ext>          队伍照片(teamId 为队伍 API id,可简写为数字 uid)

修改/新增图片后重启本程序即可生效(仅重建本地事件日志,不会重新请求 PTA)。
"""


def find_image(assets_dir, segments, name):
    """在 assets_dir/segments/ 下查找 name.<ext>,返回 (文件名, 绝对路径) 或 None。"""
    folder = os.path.join(assets_dir, *segments)
    if not os.path.isdir(folder):
        return None
    for ext in IMAGE_EXTS:
        filename = name + ext
        path = os.path.join(folder, filename)
        if os.path.isfile(path):
            return filename, path
    return None


def file_reference(href_prefix, found):
    """把查到的图片转为 ICPC Contest API 的文件引用对象。"""
    filename, _ = found
    ext = os.path.splitext(filename)[1].lower()
    return {
        'href': '%s/%s' % (href_prefix, filename),
        'mime': MIME_TYPES[ext],
        'filename': filename,
    }


def compute_assets_hash(assets_dir, colors_hash):
    """静态资源指纹:配色哈希 + assets 目录树(相对路径+大小)。内容变化时触发事件重建。"""
    sha = hashlib.sha1()
    sha.update(('colors:' + colors_hash).encode('utf-8'))
    if assets_dir and os.path.isdir(assets_dir):
        for root, dirs, files in os.walk(assets_dir):
            dirs.sort()
            for filename in sorted(files):
                path = os.path.join(root, filename)
                try:
                    size = os.path.getsize(path)
                except OSError:
                    continue
                rel = os.path.relpath(path, assets_dir).replace('\\', '/')
                sha.update(('%s:%d\n' % (rel, size)).encode('utf-8'))
    return sha.hexdigest()


def ensure_assets_dir(assets_dir):
    """首次运行时创建目录骨架与说明文件。"""
    if not os.path.isdir(assets_dir):
        os.makedirs(assets_dir, exist_ok=True)
        for sub in ('contest', 'organizations', 'teams'):
            os.makedirs(os.path.join(assets_dir, sub), exist_ok=True)
        with open(os.path.join(assets_dir, 'README.txt'), 'w', encoding='utf-8') as fp:
            fp.write(ASSETS_README)
