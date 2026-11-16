"""时间工具与 PTA -> CCS(ICPC Contest API)格式映射。"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple, List

from .utils import log

# PTA compiler 代码 -> CCS language 展示名
LANGUAGE_NAMES = {
    'GCC': 'C',
    'GXX': 'C++',
    'CLANG': 'C (Clang)',
    'CLANGXX': 'C++ (Clang)',
    'JAVAC': 'Java',
    'PYTHON': 'Python 3',
    'PYPY': 'PyPy 2',
    'PYPY3': 'PyPy 3',
}

# PTA 判题状态 -> CCS judgement-type id
STATUS_MAP = {
    'ACCEPTED': 'ac',
    'PARTIAL_ACCEPTED': 'pa',
    'WRONG_ANSWER': 'wa',
    'PRESENTATION_ERROR': 'pe',
    'COMPILE_ERROR': 'ce',
    'TIME_LIMIT_EXCEEDED': 'tle',
    'MEMORY_LIMIT_EXCEEDED': 'mle',
    'OUTPUT_LIMIT_EXCEEDED': 'ole',
    'RUNTIME_ERROR': 'rte',
    'SEGMENTATION_FAULT': 'rte',
    'INTERNAL_ERROR': 'ice',
    'SKIPPED': 'skipped',
}

# 视为"尚未出结果"的状态(判题队列中),此时 judgement 的 type 置为 null
PENDING_STATUSES = {'QUEUED', 'JUDGING', 'PENDING', 'SUBMITTED', 'UPLOADING', 'COMPILING'}

# 默认题目配色(A~M 各不同颜色,类似气球色),写入默认 colors.csv
DEFAULT_PALETTE = [
    ('A', 'Red', '#E53935'),
    ('B', 'Orange', '#FB8C00'),
    ('C', 'Yellow', '#FDD835'),
    ('D', 'Lime', '#7CB342'),
    ('E', 'Green', '#43A047'),
    ('F', 'Teal', '#00897B'),
    ('G', 'Cyan', '#00ACC1'),
    ('H', 'Blue', '#1E88E5'),
    ('I', 'Indigo', '#3949AB'),
    ('J', 'Purple', '#8E24AA'),
    ('K', 'Magenta', '#D81B60'),
    ('L', 'Brown', '#8D6E63'),
    ('M', 'Gray', '#546E7A'),
]

COLORS_CSV_HEADER = (
    '# 题目配色文件:每行 label,颜色名,RGB(如 #1E88E5);# 开头为注释\n'
    '# 修改后重启本程序生效(仅重建本地事件日志,不会重新请求 PTA)\n'
    'label,color,rgb\n'
)


def palette_from_rows(rows):
    """[(label, color, rgb)] -> {label: {color, rgb}}。"""
    palette = {}
    for label, color, rgb in rows:
        palette[str(label).strip().upper()] = {'color': color, 'rgb': rgb}
    return palette


def palette_hash(palette):
    """配色内容的稳定哈希(用于检测 colors.csv 变化)。"""
    import hashlib
    canonical = '\n'.join('%s,%s,%s' % (label, palette[label]['color'], palette[label]['rgb'])
                          for label in sorted(palette))
    return hashlib.sha1(canonical.encode('utf-8')).hexdigest()


def load_colors_csv(path):
    """读取题目配色 CSV(label,color,rgb),返回 palette dict;格式非法的行跳过并告警。"""
    palette = {}
    with open(path, 'r', encoding='utf-8-sig') as fp:
        for line_no, raw in enumerate(fp, 1):
            line = raw.strip()
            if not line or line.startswith('#'):
                continue
            parts = [p.strip() for p in line.split(',')]
            if parts and parts[0].lower() == 'label':
                continue  # 表头
            if len(parts) == 3:
                label, color, rgb = parts
            elif len(parts) == 2:
                label, color, rgb = parts[0], parts[1], parts[1]
            else:
                log('colors.csv 第 %d 行格式非法,已跳过: %s', line_no, line)
                continue
            rgb = rgb.upper()
            if not rgb.startswith('#'):
                rgb = '#' + rgb
            import re
            if not re.match(r'^#[0-9A-F]{6}$', rgb):
                log('colors.csv 第 %d 行 RGB 值非法,已跳过: %s', line_no, line)
                continue
            palette[label.upper()] = {'color': color, 'rgb': rgb}
    return palette

# 固定 judgement-types 表: (id, name, penalty, solved)
JUDGEMENT_TYPES = [
    ('ac', 'Accepted', False, True),
    ('pa', 'Partially Accepted', True, False),
    ('wa', 'Wrong Answer', True, False),
    ('pe', 'Presentation Error', True, False),
    ('ce', 'Compile Error', False, False),
    ('tle', 'Time Limit Exceeded', True, False),
    ('mle', 'Memory Limit Exceeded', True, False),
    ('ole', 'Output Limit Exceeded', True, False),
    ('rte', 'Runtime Error', True, False),
    ('ice', 'Internal Error', False, False),
    ('skipped', 'Skipped', False, False),
]


# --------------------------------------------------------------------- 时间

def parse_time(s):
    """解析 PTA 的 ISO8601 时间(如 2025-12-18T06:30:00Z),失败返回 None。"""
    if not s:
        return None
    try:
        return datetime.strptime(s, '%Y-%m-%dT%H:%M:%S.%fZ').replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    try:
        return datetime.strptime(s, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(s.replace('Z', '+00:00'))
    except ValueError:
        return None


def format_time(dt):
    """输出带本地时区偏移的 ISO8601 时间,形如 2025-12-18T14:30:00.000+08:00。"""
    if dt is None:
        return None
    dt = dt.astimezone()
    offset = dt.utcoffset() or timedelta(0)
    total = int(offset.total_seconds())
    sign = '+' if total >= 0 else '-'
    total = abs(total)
    hours, minutes = total // 3600, total % 3600 // 60
    return '%04d-%02d-%02dT%02d:%02d:%02d.%03d%s%02d:%02d' % (
        dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second,
        dt.microsecond // 1000, sign, hours, minutes)


def format_duration_ms(ms):
    """毫秒 -> H:MM:SS.mmm(ICPC Contest API 时长格式)。"""
    ms = max(0, int(ms))
    hours = ms // 3600000
    minutes = ms % 3600000 // 60000
    seconds = ms % 60000 // 1000
    milli = ms % 1000
    return '%d:%02d:%02d.%03d' % (hours, minutes, seconds, milli)


def contest_time_str(start_dt, at_dt):
    """比赛相对时间(开始时刻起算,负值截断为 0:00:00.000)。"""
    if start_dt is None or at_dt is None:
        return None
    delta_ms = int((at_dt - start_dt).total_seconds() * 1000)
    return format_duration_ms(delta_ms)


# --------------------------------------------------------------- CCS 适配

def to_contest(ps):
    """problemSet -> CCS contest 对象。"""
    start = parse_time(ps.get('startAt'))
    end = parse_time(ps.get('endAt'))
    duration_s = ps.get('duration')
    if duration_s is None and start and end:
        duration_s = (end - start).total_seconds()
    freeze_s = freeze_duration_seconds(ps, duration_s)
    return {
        'id': str(ps.get('id')),
        'name': ps.get('name') or ps.get('id'),
        'formal_name': ps.get('name') or ps.get('id'),
        'start_time': format_time(start),
        'duration': format_duration_ms(int(round(duration_s * 1000))) if duration_s else '0:00:00.000',
        'scoreboard_type': 'pass-fail',
        'scoreboard_freeze_duration': format_duration_ms(freeze_s * 1000) if freeze_s else None,
        'penalty_time': 20,
    }


def hiding_minutes(ps):
    """problemSetConfig.hidingTime:开赛后多少分钟封榜(0 表示不封榜)。"""
    return (ps.get('problemSetConfig') or {}).get('hidingTime') or 0


def freeze_duration_seconds(ps, duration_s):
    """根据 hidingTime(开赛后多少分钟封榜)计算封榜时长(秒);无封榜返回 None。

    例:时长 150 分钟、hidingTime=120 -> 第 120 分钟封榜,封榜时长 30 分钟。
    (对应比赛公告"比赛进行第 120 分钟(结束前 30 分钟)封榜")
    """
    duration_s = float(duration_s or 0)
    hiding_min = hiding_minutes(ps)
    if hiding_min <= 0:
        return None
    freeze_s = duration_s - hiding_min * 60
    return freeze_s if freeze_s > 0 else None


def to_state(ps, now=None):
    """problemSet -> CCS state 对象(开始/封榜/结束/解封/终局化时间)。"""
    now = now or datetime.now(timezone.utc)
    start = parse_time(ps.get('startAt'))
    end = parse_time(ps.get('endAt'))
    duration_s = float(ps.get('duration') or 0)
    if end is None and start:
        end = start + timedelta(seconds=duration_s)
    hiding_min = hiding_minutes(ps)
    frozen_at = None
    if hiding_min > 0 and start:
        frozen_at = start + timedelta(minutes=hiding_min)
        if frozen_at >= end:
            frozen_at = None

    started = start if (start and now >= start) else None
    ended = end if (end and now >= end) else None
    frozen = frozen_at if (frozen_at and now >= frozen_at) else None
    thawed = end if (end and now >= end) else None
    finalized = end + timedelta(seconds=120) if (end and now >= end + timedelta(seconds=120)) else None
    end_of_updates = end + timedelta(seconds=150) if (end and now >= end + timedelta(seconds=150)) else None
    return {
        'started': format_time(started),
        'ended': format_time(ended),
        'frozen': format_time(frozen),
        'thawed': format_time(thawed),
        'finalized': format_time(finalized),
        'end_of_updates': format_time(end_of_updates),
    }


def to_problem(psp, index, palette=None):
    """problemSetProblem -> CCS problem 对象(palette: label -> {color, rgb})。"""
    config = ((psp.get('problemConfig') or {}).get('programmingProblemConfig') or {})
    time_limit_ms = config.get('timeLimit')
    cases = config.get('cases') or {}
    label = psp.get('label') or chr(ord('A') + index)
    entry = (palette or {}).get(str(label).upper())
    return {
        'id': str(psp.get('id')),
        'label': label,
        'name': psp.get('title') or label or '',
        'ordinal': index,
        'color': (entry or {}).get('color') or 'white',
        'rgb': (entry or {}).get('rgb') or '#ffffff',
        'time_limit': (time_limit_ms / 1000.0) if time_limit_ms else None,
        'test_data_count': len(cases),
    }


def to_language(code):
    """PTA compiler 代码 -> CCS language 对象。"""
    return {'id': code, 'name': LANGUAGE_NAMES.get(code, code)}


def to_group(group):
    """PTA userGroup -> CCS group 对象。"""
    return {'id': str(group.get('id')), 'name': group.get('name') or str(group.get('id'))}


def to_organization(org):
    """题目集所属单位 -> CCS organization 对象(单一机构)。"""
    return {
        'id': str(org.get('id')),
        'name': org.get('name') or str(org.get('id')),
        'formal_name': org.get('name') or str(org.get('id')),
    }


def to_team(user_id, member, user, student_user, organization_id):
    """(报名成员 + 用户信息) -> CCS team 对象。

    member:  members[] 条目 {userGroupId, userId, studentUserId}
    user:    userById[userId] {nickname, ...}
    student_user: studentUserById[studentUserId] {name, studentNumber}
    """
    uid = str(user_id)
    real_name = (student_user or {}).get('name')
    nickname = (user or {}).get('nickname')
    name = real_name or nickname or ('team-%s' % uid)
    student_number = (student_user or {}).get('studentNumber')
    group_id = str((member or {}).get('userGroupId') or 'participants')
    if group_id in ('0', 'None'):
        group_id = 'participants'
    return {
        'id': 'team-%s' % uid,
        'label': student_number or 'team-%s' % uid,
        'name': name,
        'display_name': name,
        'organization_id': organization_id,
        'group_ids': [group_id],
    }


def judgement_type_id(status):
    """PTA 状态 -> CCS judgement-type id;判题中/未知返回 None。"""
    if not status:
        return None
    if status in PENDING_STATUSES:
        return None
    return STATUS_MAP.get(status)


def to_judgement(sub, start_dt, contest_start):
    """PTA submission -> CCS judgement 对象。"""
    sid = str(sub.get('id'))
    judge_at = parse_time(sub.get('judgeAt'))
    submit_at = parse_time(sub.get('submitAt')) or start_dt
    return {
        'id': 'j-%s' % sid,
        'submission_id': sid,
        'judgement_type_id': judgement_type_id(sub.get('status')),
        'start_time': format_time(submit_at),
        'start_contest_time': contest_time_str(contest_start, submit_at),
        'end_time': format_time(judge_at),
        'end_contest_time': contest_time_str(contest_start, judge_at),
    }


def to_submission(sub, contest_start):
    """PTA submission -> CCS submission 对象。"""
    submit_at = parse_time(sub.get('submitAt'))
    sid = str(sub.get('id'))
    return {
        'id': sid,
        'language_id': sub.get('compiler'),
        'problem_id': str(sub.get('problemSetProblemId')),
        'team_id': 'team-%s' % sub.get('userId'),
        'time': format_time(submit_at),
        'contest_time': contest_time_str(contest_start, submit_at),
    }


def to_run(submission_id, case_id, ordinal, case_result, judge_at, contest_start):
    """单个测试点判定结果 -> CCS run 对象。"""
    run_time = case_result.get('time')
    return {
        'id': 'r-%s-%s' % (submission_id, case_id),
        'judgement_id': 'j-%s' % submission_id,
        'ordinal': ordinal,
        'judgement_type_id': judgement_type_id(case_result.get('result')),
        'time': format_time(judge_at),
        'contest_time': contest_time_str(contest_start, judge_at),
        'run_time': float(run_time) if run_time is not None else None,
    }


def testcase_results(detail):
    """提交详情 -> (编译是否通过, {测试点ID: 判定结果dict})。"""
    jrcs = (detail.get('submission') or {}).get('judgeResponseContents') or []
    compile_ok = None
    results = {}
    for jrc in jrcs:
        prog = jrc.get('programmingJudgeResponseContent') or {}
        compilation = prog.get('compilationResult') or {}
        if compilation:
            compile_ok = bool(compilation.get('success'))
        for case_id, res in sorted((prog.get('testcaseJudgeResults') or {}).items()):
            results[str(case_id)] = res
    return compile_ok, results
