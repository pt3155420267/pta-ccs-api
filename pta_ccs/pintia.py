"""PTA (pintia.cn) API 客户端:带全局限速、指数退避重试。"""

import json
import random
import threading
import time

import requests

from .utils import log

BASE_URL = 'https://pintia.cn'

DEFAULT_HEADERS = {
    'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                   '(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'),
    'Accept': 'application/json',
    'Referer': 'https://pintia.cn/',
}

# PTA 人员列表接口使用的过滤条件(与网页端一致)
MEMBERS_FILTER = '{"studentUserFilter":{"keyword":"","unbindOnly":false,"field":"STUDENT_NUMBER"}}'

RETRYABLE_STATUSES = (429, 500, 502, 503, 504)
MAX_ATTEMPTS = 6


class PtaError(Exception):
    """PTA 请求失败(非认证类)。"""


class PtaAuthError(Exception):
    """PTA 认证失败(cookie 过期或无效)。"""


class RateLimiter(object):
    """全局最小请求间隔限速器(带随机抖动,线程安全)。"""

    def __init__(self, min_interval, jitter=0.2):
        self.min_interval = max(0.0, min_interval)
        self.jitter = jitter
        self._next_time = 0.0
        self._lock = threading.Lock()

    def wait(self):
        with self._lock:
            now = time.monotonic()
            delay = self._next_time - now
            interval = self.min_interval * (1.0 + random.uniform(-self.jitter, self.jitter))
            self._next_time = max(now, self._next_time) + interval
        if delay > 0:
            time.sleep(delay)


class PtaClient(object):
    """对 PTA API 的只读访问。所有请求经过同一个限速器。"""

    def __init__(self, cookie, min_interval=1.0, page_limit=50, timeout=30, proxy=None):
        if '=' not in cookie:
            cookie = 'PTASession=' + cookie.strip()
        self.session = requests.Session()
        # 默认直连,忽略系统/环境代理设置(Windows 注册表代理常导致不可用连接);可用 --proxy 指定
        self.session.trust_env = False
        if proxy:
            self.session.proxies = {'http': proxy, 'https': proxy}
        self.session.headers.update(DEFAULT_HEADERS)
        self.session.headers['Cookie'] = cookie.strip()
        self.limiter = RateLimiter(min_interval)
        self.page_limit = page_limit
        self.timeout = timeout

    # ------------------------------------------------------------------ core

    def _get(self, path, params=None):
        url = BASE_URL + path
        attempt = 0
        while True:
            self.limiter.wait()
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
            except requests.RequestException as e:
                attempt += 1
                if attempt >= MAX_ATTEMPTS:
                    raise PtaError('网络请求失败: %s' % e)
                delay = min(30, 2 ** attempt)
                log('PTA 请求网络异常(%s),第 %d 次重试,%.0fs 后重试', e, attempt, delay)
                time.sleep(delay)
                continue
            if resp.status_code == 200:
                try:
                    return resp.json()
                except ValueError as e:
                    raise PtaError('PTA 返回非 JSON 响应: %s' % e)
            if resp.status_code in (401, 403):
                raise PtaAuthError('PTA 认证失败(HTTP %d),请检查 PTASession cookie 是否有效/过期'
                                   % resp.status_code)
            if resp.status_code in RETRYABLE_STATUSES:
                attempt += 1
                if attempt >= MAX_ATTEMPTS:
                    raise PtaError('PTA 持续返回 HTTP %d: %s' % (resp.status_code, url))
                retry_after = resp.headers.get('Retry-After')
                if retry_after and retry_after.strip().isdigit():
                    delay = min(120, float(retry_after))
                else:
                    delay = min(60, 2 ** attempt * 2)
                log('PTA 返回 HTTP %d,第 %d 次重试,%.0fs 后重试', resp.status_code, attempt, delay)
                time.sleep(delay)
                continue
            raise PtaError('PTA 请求失败 HTTP %d: %s' % (resp.status_code, url))

    # -------------------------------------------------------------- endpoints

    def get_problem_set(self, problem_set_id):
        """题目集(比赛)基本信息:名称、开始/结束时间、封榜配置、编译器、组织机构等。"""
        data = self._get('/api/problem-sets/%s' % problem_set_id)
        ps = data.get('problemSet')
        if not ps:
            raise PtaError('题目集 %s 不存在或无权访问' % problem_set_id)
        # 所属单位信息在响应顶层而非 problemSet 内,合并进来便于统一取用
        if data.get('organization'):
            ps['organization'] = data['organization']
        return ps

    def get_problems(self, problem_set_id):
        """题目列表(label、标题、时限、测试点配置)。"""
        data = self._get('/api/problem-sets/%s/problems' % problem_set_id)
        return data.get('problemSetProblems', [])

    def get_members_page(self, problem_set_id, page):
        """人员(报名名单)分页。"""
        params = {'page': page, 'limit': self.page_limit, 'filter': MEMBERS_FILTER}
        return self._get('/api/problem-sets/%s/members' % problem_set_id, params=params)

    def get_all_members(self, problem_set_id):
        result = {'total': 0, 'members': [], 'userGroups': {}, 'userById': {}, 'studentUserById': {}}
        page = 0
        while True:
            data = self.get_members_page(problem_set_id, page)
            result['total'] = data.get('total', 0)
            result['members'].extend(data.get('members', []))
            result['userGroups'].update(data.get('userGroups') or {})
            result['userById'].update(data.get('userById') or {})
            result['studentUserById'].update(data.get('studentUserById') or {})
            got = len(data.get('members', []))
            log('已获取报名成员 %d/%d', len(result['members']), result['total'])
            if got == 0 or len(result['members']) >= result['total']:
                break
            page += 1
        return result

    def get_submissions_page(self, problem_set_id, before=None):
        """提交列表(按时间倒序)。before 为上一页最后一条的提交 ID。"""
        params = {'limit': self.page_limit, 'filter': '{}'}
        if before:
            params['before'] = before
        return self._get('/api/problem-sets/%s/submissions' % problem_set_id, params=params)

    def iter_submissions(self, problem_set_id, stop_when=None):
        """按时间倒序遍历提交,产出 (page 数据, submissions)。

        stop_when(submissions) 返回 True 时停止继续翻页(用于增量同步时碰到已知提交)。
        """
        before = None
        while True:
            data = self.get_submissions_page(problem_set_id, before=before)
            subs = data.get('submissions', [])
            yield data, subs
            if not subs or not data.get('hasBefore'):
                return
            if stop_when is not None and stop_when(subs):
                return
            before = subs[-1]['id']

    def get_submission_detail(self, submission_id):
        """提交详情:编译结果与逐测试点判定结果(runs 数据来源)。"""
        return self._get('/api/submissions/%s' % submission_id)
