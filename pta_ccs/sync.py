"""数据同步:全量初始化 + 中断续传 + 增量轮询,把 PTA 数据转换为 CCS 事件写入 store。"""

import threading
import time
from datetime import datetime, timezone

from .adapter import (JUDGEMENT_TYPES, PENDING_STATUSES, STATUS_MAP, log, parse_time,
                      to_contest, to_group, to_judgement, to_language, to_organization,
                      to_problem, to_run, to_state, to_submission, to_team, testcase_results)
from .assets import file_reference, find_image
from .pintia import PtaAuthError, PtaError

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


class Syncer(object):
    def __init__(self, client, store, opts, palette=None, assets_dir=None):
        self.client = client
        self.store = store
        self.opts = opts
        self.palette = palette or {}
        self.assets_dir = assets_dir
        self.fetch_details = not opts.no_details
        self.poll_enabled = not opts.no_poll
        self._warned_users = set()
        self._warned_statuses = set()

    def configure(self, palette, assets_dir):
        self.palette = palette or {}
        self.assets_dir = assets_dir

    # ------------------------------------------------------------- 图片资源

    def _image_ref(self, segments, name, href_prefix, alt_ids=None):
        """查找图片并生成文件引用;找不到返回 None。alt_ids 为备选目录名(如去掉 team- 前缀)。"""
        if not self.assets_dir:
            return None
        found = find_image(self.assets_dir, segments, name)
        if found is None and alt_ids:
            for alt in alt_ids:
                found = find_image(self.assets_dir, (segments[0], alt), name)
                if found is not None:
                    break
        if found is None:
            return None
        return file_reference(href_prefix, found)

    # --------------------------------------------------------- 实体转换辅助

    def contest_start(self):
        return parse_time((self.store.problem_set or {}).get('startAt'))

    def emit_contest_and_static(self):
        """比赛、state、语言、题目、分组、机构、队伍、judgement-types 事件。"""
        store = self.store
        ps = store.problem_set
        psid = store.contest_id
        organization = to_organization(ps.get('organization') or {'id': ps.get('ownerOrganizationId'), 'name': ps.get('name')})
        org_id = organization['id']

        contest_data = to_contest(ps)
        ref = self._image_ref(('contest',), 'logo', 'contests/%s' % psid)
        if ref:
            contest_data['logo'] = [ref]
        store.add_event('contests', contest_data)
        store.add_event('state', to_state(ps))

        for code in (ps.get('problemSetConfig') or {}).get('compilers') or []:
            store.add_event('languages', to_language(code))

        problems = sorted(store.raw_problems,
                          key=lambda p: (p.get('label') or '', p.get('id') or ''))
        for index, psp in enumerate(problems):
            store.add_event('problems', to_problem(psp, index, self.palette))

        groups = (store.raw_members or {}).get('userGroups') or {}
        if not groups:
            groups = {'participants': {'id': 'participants', 'name': 'Participants'}}
        for gid, group in groups.items():
            store.add_event('groups', to_group(group))

        ref = self._image_ref(('organizations', org_id), 'logo',
                              'contests/%s/organizations/%s' % (psid, org_id))
        if ref:
            organization['logo'] = [ref]
        store.add_event('organizations', organization)

        members = (store.raw_members or {}).get('members') or []
        users = (store.raw_members or {}).get('userById') or {}
        students = (store.raw_members or {}).get('studentUserById') or {}
        for member in members:
            uid = member.get('userId')
            student = students.get(member.get('studentUserId'))
            team = to_team(uid, member, users.get(uid), student, org_id)
            ref = self._image_ref(('teams', team['id']), 'photo',
                                  'contests/%s/teams/%s' % (psid, team['id']),
                                  alt_ids=(str(uid),))
            if ref:
                team['photo'] = [ref]
            store.add_event('teams', team)

        for jt_id, jt_name, penalty, solved in JUDGEMENT_TYPES:
            store.add_event('judgement-types', {
                'id': jt_id, 'name': jt_name, 'penalty': penalty, 'solved': solved,
            })

    def ensure_team(self, user_id, user=None, student_user=None, member=None):
        """提交来自不在报名名单中的用户时,补建队伍(基于提交接口附带的用户信息)。"""
        uid = str(user_id)
        team_id = 'team-%s' % uid
        if team_id in self.store.teams:
            return
        if uid in self._warned_users:
            return
        self._warned_users.add(uid)
        log('警告: 用户 %s 不在报名名单中,已根据提交数据补建队伍', uid)
        ps = self.store.problem_set
        organization = to_organization(ps.get('organization') or {'id': ps.get('ownerOrganizationId'), 'name': ps.get('name')})
        team = to_team(uid, member, user, student_user, organization['id'])
        ref = self._image_ref(('teams', team['id']), 'photo',
                              'contests/%s/teams/%s' % (self.store.contest_id, team['id']),
                              alt_ids=(uid,))
        if ref:
            team['photo'] = [ref]
        self.store.add_event('teams', team)
    def upsert_submission(self, sub):
        """发出 submissions + judgements 事件。"""
        store = self.store
        sid = str(sub['id'])
        uid = str(sub.get('userId'))

        members = (store.raw_members or {})
        member_of = {str(m.get('userId')): m for m in members.get('members') or []}
        users = members.get('userById') or {}
        students = members.get('studentUserById') or {}
        if uid not in member_of:
            self.ensure_team(uid, user=sub.get('user') or users.get(uid),
                             student_user=students.get(str(sub.get('studentUserId'))))
        status = sub.get('status')
        if (sub.get('judgeAt') and status
                and status not in STATUS_MAP and status not in PENDING_STATUSES
                and status not in self._warned_statuses):
            self._warned_statuses.add(status)
            log('警告: 未知的 PTA 判题状态 %s(提交 %s),判定结果将暂缺', status, sid)
        start = self.contest_start()
        store.add_event('submissions', to_submission(sub, start))
        store.add_event('judgements', to_judgement(sub, parse_time(sub.get('submitAt')), start))

    def apply_detail(self, sid, detail):
        """提交详情 -> runs 事件(编译失败/无测试点时为空)。同时保存精简详情供重建使用。"""
        store = self.store
        sub = store.raw_submissions.get(sid) or {}
        judge_at = parse_time(sub.get('judgeAt')) or parse_time((detail.get('submission') or {}).get('judgeAt'))
        start = self.contest_start()
        _, results = testcase_results(detail)
        store.details[sid] = {'judgeAt': sub.get('judgeAt'), 'results': {
            case_id: {'result': res.get('result'), 'time': res.get('time')}
            for case_id, res in results.items()
        }}
        for ordinal, case_id in enumerate(sorted(results.keys())):
            store.add_event('runs', to_run(sid, case_id, ordinal + 1, results[case_id], judge_at, start))

    # ------------------------------------------------------------ 全量初始化

    def initial_sync(self):
        store = self.store
        psid = store.contest_id
        log('开始全量同步题目集 %s ...', psid)

        ps = self.client.get_problem_set(psid)
        store.problem_set = ps
        log('比赛: %s (%s ~ %s)', ps.get('name'), ps.get('startAt'), ps.get('endAt'))

        store.raw_problems = self.client.get_problems(psid)
        log('题目: %d 道', len(store.raw_problems))

        store.raw_members = self.client.get_all_members(psid)
        log('报名成员: %d 人,%d 个分组', len(store.raw_members['members']), len(store.raw_members['userGroups']))

        subs, users, students = self._crawl_submissions(psid)
        # 合并提交接口附带的用户信息,作为不在报名名单中的用户的兜底数据
        store.raw_members.setdefault('userById', {}).update(users)
        store.raw_members.setdefault('studentUserById', {}).update(students)
        for sub in subs:
            store.raw_submissions[str(sub['id'])] = sub
        log('提交合计: %d 条', len(subs))

        # 检查点:先落盘基础数据,此后中断可续传(不必重新翻页抓取提交)
        store.initial_done = False
        store.save_cache()

        # 提交详情 -> runs(最耗时的阶段,每 50 条保存一次进度)
        if self.fetch_details:
            self._fetch_details(psid, [s for s in subs if self._need_detail(s)])
        else:
            log('已按 --no-details 跳过提交详情,将不产生 runs 数据')

        self._reemit_all_events()
        store.initial_done = True
        store.save_cache()
        log('全量同步完成,%d 条事件已写入 %s', len(store.events), store.events_path)

    def _crawl_submissions(self, psid):
        """倒序翻页抓取全部提交,返回(时间正序列表, userById, studentUserById)。"""
        all_subs = []
        users, students = {}, {}
        pages = self.client.iter_submissions(psid)
        for data, subs in pages:
            all_subs.extend(subs)
            users.update(data.get('userById') or {})
            students.update(data.get('studentUserById') or {})
            log('已获取提交 %d 条 ...', len(all_subs))
        all_subs.reverse()  # 时间正序
        return all_subs, users, students

    def _need_detail(self, sub):
        """是否需要拉取详情:已出结果且不是编译错误(CE 没有测试点结果)。"""
        if not self.fetch_details:
            return False
        if not sub.get('judgeAt'):
            return False
        return sub.get('status') != 'COMPILE_ERROR'

    def _fetch_details(self, psid, subs):
        total = len(subs)
        if not total:
            return
        log('开始抓取 %d 条提交详情(用于生成 runs)...', total)
        done = 0
        try:
            for sub in subs:
                sid = str(sub['id'])
                try:
                    detail = self.client.get_submission_detail(sid)
                except PtaError as e:
                    log('抓取提交 %s 详情失败: %s', sid, e)
                    continue
                self.apply_detail(sid, detail)
                done += 1
                if done % 50 == 0:
                    log('提交详情进度 %d/%d', done, total)
                    self.store.save_cache()  # 检查点:中断后可续传
        except PtaAuthError as e:
            # cookie 失效时继续逐条重试毫无意义,直接中止
            log('抓取中止: %s(请更新 cookie 后重启)', e)
        log('提交详情抓取完成 %d/%d', done, total)
        if done:
            self.store.save_cache()

    def _sorted_submissions(self):
        """按提交时间正序返回全部原始提交(重建事件日志的固定顺序)。"""
        def sort_key(sub):
            return (parse_time(sub.get('submitAt')) or _EPOCH, str(sub.get('id')))

        return sorted(self.store.raw_submissions.values(), key=sort_key)

    def _reemit_all_events(self):
        """依据当前 store 数据确定性地重建全部事件日志(各恢复路径共用)。"""
        store = self.store
        # 清空内存中的事件与实体(保留原始数据),避免此前部分产出的事件残留
        with store.lock:
            store._clear_served()
        store.start_event_persist(truncate=True)
        self.emit_contest_and_static()
        subs = self._sorted_submissions()
        for sub in subs:
            self.upsert_submission(sub)
        # runs 按提交时间顺序重建
        for sub in subs:
            sid = str(sub.get('id'))
            distilled = store.details.get(sid)
            if distilled:
                self.apply_distilled_detail(sid, distilled)
        log('事件日志重建完成,%d 条事件', len(store.events))

    # -------------------------------------------------------------- 中断续传

    def resume_sync(self):
        """全量同步中断后的续传:补齐缺失的提交详情,再重建事件日志。"""
        store = self.store
        if not store.problem_set:
            return False
        log('检测到上次全量同步未完成,开始续传 ...')

        # 基础数据可能在极早期被中断而缺失,补齐
        if not store.raw_problems:
            store.raw_problems = self.client.get_problems(store.contest_id)
            log('题目: %d 道', len(store.raw_problems))
        members = store.raw_members or {}
        if not members.get('members'):
            store.raw_members = self.client.get_all_members(store.contest_id)
            log('报名成员: %d 人', len(store.raw_members['members']))

        # 补齐缺失的提交详情
        missing = [s for s in store.raw_submissions.values()
                   if self._need_detail(s) and str(s['id']) not in store.details]
        if missing:
            log('尚有 %d 条提交详情未抓取', len(missing))
            self._fetch_details(store.contest_id, missing)

        self._reemit_all_events()
        store.initial_done = True
        store.save_cache()
        log('续传完成,%d 条事件已写入 %s', len(store.events), store.events_path)
        return True

    # -------------------------------------------------------------- 恢复重建

    def rebuild_from_cache(self):
        """全量同步已完成但事件文件丢失时,依据缓存重建事件日志。"""
        store = self.store
        if not store.problem_set:
            return False
        log('依据本地缓存重建事件日志 ...')
        self._reemit_all_events()
        store.save_cache()
        return True

    def apply_distilled_detail(self, sid, distilled):
        """缓存的精简详情 -> runs 事件(重建路径)。"""
        store = self.store
        sub = store.raw_submissions.get(sid) or {}
        judge_at = parse_time(sub.get('judgeAt')) or parse_time(distilled.get('judgeAt'))
        start = self.contest_start()
        results = distilled.get('results') or {}
        for ordinal, case_id in enumerate(sorted(results.keys())):
            store.add_event('runs', to_run(sid, case_id, ordinal + 1, results[case_id], judge_at, start))

    # -------------------------------------------------------------- 增量轮询

    def poll_once(self):
        """单次增量轮询:检测新提交与状态变化,更新事件。"""
        store = self.store
        psid = store.contest_id

        new_subs = []
        for data, subs in self.client.iter_submissions(
                psid, stop_when=lambda page: all(str(s['id']) in store.raw_submissions for s in page)):
            members = store.raw_members or {}
            members.setdefault('userById', {}).update(data.get('userById') or {})
            members.setdefault('studentUserById', {}).update(data.get('studentUserById') or {})
            for sub in subs:
                sid = str(sub['id'])
                prev = store.raw_submissions.get(sid)
                if prev is None:
                    new_subs.append(sub)
                elif self._sub_changed(prev, sub):
                    new_subs.append(sub)
            if not data.get('hasBefore'):
                break

        if new_subs:
            new_subs.sort(key=lambda s: (parse_time(s.get('submitAt')) or _EPOCH, str(s.get('id'))))
            log('发现 %d 条新增/变化的提交', len(new_subs))
            need_details = []
            for sub in new_subs:
                sid = str(sub['id'])
                store.raw_submissions[sid] = sub
                self.upsert_submission(sub)
                if self._need_detail(sub):
                    need_details.append(sub)
            if need_details:
                self._fetch_details(psid, need_details)

        self.refresh_state()
        store.save_cache()

    @staticmethod
    def _sub_changed(prev, cur):
        keys = ('status', 'judgeAt', 'time', 'memory')
        return any(prev.get(k) != cur.get(k) for k in keys)

    def refresh_state(self):
        """比赛状态(开始/封榜/结束等)发生变化时补发 state 事件。"""
        store = self.store
        state = to_state(store.problem_set)
        if state != store.state:
            store.add_event('state', state)

    def poll_forever(self):
        interval = self.opts.poll_interval
        while True:
            time.sleep(interval)
            try:
                self.poll_once()
                ended = (self.store.state or {}).get('ended')
                interval = self.opts.poll_interval_ended if ended else self.opts.poll_interval
            except PtaAuthError as e:
                log('轮询失败: %s(请更新 cookie 后重启)', e)
            except Exception as e:  # noqa: B001  轮询循环不允许中断
                log('轮询失败: %s', e)

    def start_poll_thread(self):
        if not self.poll_enabled:
            return
        thread = threading.Thread(target=self.poll_forever, name='pta-poll', daemon=True)
        thread.start()
        log('增量轮询已启动(间隔 %.0fs,结束后 %.0fs)', self.opts.poll_interval, self.opts.poll_interval_ended)
