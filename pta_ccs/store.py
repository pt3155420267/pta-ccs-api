"""比赛数据存储:实体表 + 事件日志(events),支持落盘缓存与事件回放。

事件格式与 ICPC Contest API event-feed 一致:
    {"type": "submissions", "id": "...", "data": {...}, "token": "..."}

文件布局(<data_dir>/<problem_set_id>/):
    events.jsonl  事件日志(逐行 JSON),重启后回放即可恢复全部对外数据与 token
    cache.json    PTA 原始数据缓存,用于增量轮询比对与断点续传
"""

import json
import os
import threading

from .adapter import JUDGEMENT_TYPES, log

EVENT_TYPES_ENTITY = ('languages', 'problems', 'groups', 'organizations', 'teams',
                      'judgement-types', 'submissions', 'judgements', 'runs')


class ContestStore(object):
    def __init__(self, contest_id, data_dir):
        self.contest_id = str(contest_id)
        self.base_dir = os.path.join(data_dir, self.contest_id)
        self.events_path = os.path.join(self.base_dir, 'events.jsonl')
        self.cache_path = os.path.join(self.base_dir, 'cache.json')

        self.lock = threading.RLock()
        self.events = []            # 全量事件列表
        self._token_index = {}      # token -> 下标
        self._events_fp = None

        # 对外服务的实体表(CCS 格式)
        self.contest = None
        self.state = None
        self.languages = {}
        self.language_order = []
        self.problems = {}          # id -> problem(按 ordinal 排序输出)
        self.problem_order = []
        self.groups = {}
        self.group_order = []
        self.organizations = {}
        self.org_order = []
        self.teams = {}
        self.team_order = []
        self.judgement_types = {}
        self.judgement_type_order = []
        self.submissions = {}       # id -> submission
        self.submission_order = []
        self.judgements = {}        # id -> judgement
        self.judgement_order = []
        self.runs = {}              # id -> run
        self.run_order = []

        # PTA 原始数据(轮询比对用)
        self.raw_submissions = {}   # submissionId -> PTA 原始 submission
        self.details = {}           # submissionId -> 精简提交详情(测试点结果),用于事件日志缺失时重建 runs
        self.problem_set = None     # PTA 原始 problemSet
        self.raw_problems = []
        self.raw_members = None
        self.initial_done = False   # 全量同步是否已完成(用于中断续传)
        self.assets_hash = None     # 配色+图片资源指纹(变化时重建事件)

    # ------------------------------------------------------------ 路径/持久化

    def reset(self):
        with self.lock:
            self._close_events_fp()
            for path in (self.events_path, self.cache_path):
                if os.path.exists(path):
                    os.remove(path)
            self.events = []
            self._token_index = {}
            self._clear_entities()

    def load(self):
        """启动时加载,判定恢复方式。

        返回:
          'loaded'  事件日志完整(全量同步已完成),回放即可对外服务
          'resume'  全量同步中途被中断,依据缓存续传(补齐缺失详情后重建事件)
          'rebuild' 全量同步已完成但事件文件丢失,依据缓存重建事件
          'fresh'   无可用缓存,需要全量同步
        """
        with self.lock:
            has_events = os.path.exists(self.events_path) and os.path.getsize(self.events_path) > 0
            cache = self._load_cache()
            if cache and cache.get('problemSet'):
                self._load_raw(cache)
                if not cache.get('initialDone'):
                    return 'resume'
                if has_events:
                    count = self._replay_events()
                    log('已回放 %d 条事件', count)
                    # 继续以追加模式写入后续事件
                    self.start_event_persist(truncate=False)
                    return 'loaded'
                return 'rebuild'
            if has_events:
                # 有事件无缓存:无法做增量比对,只能全量重建
                log('检测到事件日志但缺少有效缓存,将重新全量同步')
                self.reset()
            return 'fresh'

    def start_event_persist(self, truncate=False):
        os.makedirs(self.base_dir, exist_ok=True)
        mode = 'w' if truncate else 'a'
        self._events_fp = open(self.events_path, mode, encoding='utf-8', newline='\n')

    def _close_events_fp(self):
        if self._events_fp:
            try:
                self._events_fp.close()
            except OSError:
                pass
            self._events_fp = None

    # ------------------------------------------------------------------ 事件

    def add_event(self, event_type, data):
        with self.lock:
            token = str(len(self.events) + 1)
            # 事件信封 id:state 事件按规范固定为 "state",其余取数据自身的 id
            event_id = 'state' if event_type == 'state' else data.get('id')
            event = {'type': event_type, 'id': event_id, 'data': data, 'token': token}
            self.events.append(event)
            self._token_index[token] = len(self.events) - 1
            self._apply_event(event)
            if self._events_fp:
                self._events_fp.write(json.dumps(event, ensure_ascii=False) + '\n')
                self._events_fp.flush()
            return event

    def events_since(self, token):
        with self.lock:
            if token is None:
                return list(self.events), None
            index = self._token_index.get(token)
            if index is None:
                return None, None
            return list(self.events[index + 1:]), token

    def _replay_events(self):
        count = 0
        with open(self.events_path, 'r', encoding='utf-8') as fp:
            for line in fp:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    log('事件日志存在损坏行,已跳过')
                    continue
                self.events.append(event)
                self._token_index[event['token']] = len(self.events) - 1
                self._apply_event(event)
                count += 1
        return count

    def _apply_event(self, event):
        event_type, data = event['type'], event['data']
        if event_type == 'contests':
            self.contest = data
        elif event_type == 'state':
            self.state = data
        elif event_type in EVENT_TYPES_ENTITY:
            table, key = self._table_for(event_type)
            table[data['id']] = data
            order = self._order_for(event_type)
            if data['id'] not in order:
                order.append(data['id'])

    def _table_for(self, event_type):
        return {
            'languages': (self.languages, 'id'),
            'problems': (self.problems, 'id'),
            'groups': (self.groups, 'id'),
            'organizations': (self.organizations, 'id'),
            'teams': (self.teams, 'id'),
            'judgement-types': (self.judgement_types, 'id'),
            'submissions': (self.submissions, 'id'),
            'judgements': (self.judgements, 'id'),
            'runs': (self.runs, 'id'),
        }[event_type]

    def _order_for(self, event_type):
        return {
            'languages': self.language_order,
            'problems': self.problem_order,
            'groups': self.group_order,
            'organizations': self.org_order,
            'teams': self.team_order,
            'judgement-types': self.judgement_type_order,
            'submissions': self.submission_order,
            'judgements': self.judgement_order,
            'runs': self.run_order,
        }[event_type]

    def _clear_entities(self):
        self._clear_served()
        self.raw_submissions = {}
        self.details = {}
        self.problem_set = None
        self.raw_problems = []
        self.raw_members = None
        self.initial_done = False
        self.assets_hash = None

    def _clear_served(self):
        """仅清空对外服务的事件与实体表(保留 PTA 原始数据,供重建)。"""
        self.contest = None
        self.state = None
        self.languages = {}
        self.language_order = []
        self.problems = {}
        self.problem_order = []
        self.groups = {}
        self.group_order = []
        self.organizations = {}
        self.org_order = []
        self.teams = {}
        self.team_order = []
        self.judgement_types = {}
        self.judgement_type_order = []
        self.submissions = {}
        self.submission_order = []
        self.judgements = {}
        self.judgement_order = []
        self.runs = {}
        self.run_order = []
        self.events = []
        self._token_index = {}

    # ------------------------------------------------------------------ 缓存

    def _load_cache(self):
        try:
            with open(self.cache_path, 'r', encoding='utf-8') as fp:
                return json.load(fp)
        except (OSError, ValueError) as e:
            log('缓存读取失败: %s', e)
            return None

    def _load_raw(self, cache):
        with self.lock:
            self.problem_set = cache.get('problemSet')
            self.raw_problems = cache.get('problems', [])
            self.raw_members = cache.get('members')
            for sub in cache.get('submissions', []):
                self.raw_submissions[str(sub['id'])] = sub
            for sid, detail in (cache.get('details') or {}).items():
                self.details[str(sid)] = detail
            self.initial_done = bool(cache.get('initialDone'))
            self.assets_hash = cache.get('assetsHash')

    def save_cache(self):
        """将 PTA 原始数据原子写入 cache.json(全程持锁,避免并发写临时文件)。"""
        with self.lock:
            cache = {
                'version': 1,
                'contestId': self.contest_id,
                'initialDone': self.initial_done,
                'assetsHash': self.assets_hash,
                'problemSet': self.problem_set,
                'problems': self.raw_problems,
                'members': self.raw_members,
                'submissions': list(self.raw_submissions.values()),
                'details': self.details,
            }
            tmp_path = self.cache_path + '.tmp'
            os.makedirs(self.base_dir, exist_ok=True)
            with open(tmp_path, 'w', encoding='utf-8', newline='\n') as fp:
                json.dump(cache, fp, ensure_ascii=False)
            os.replace(tmp_path, self.cache_path)
