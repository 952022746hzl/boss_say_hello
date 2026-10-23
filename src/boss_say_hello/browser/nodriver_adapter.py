import asyncio
import json
import re
from urllib.parse import urlparse
from boss_say_hello.browser import selectors as dom
from boss_say_hello.browser.port import SessionInfo, BrowserCapabilities, AttachmentInfo, BrowserFailure, ActionResult, ActionBlocked
from boss_say_hello.browser.actions import script
from boss_say_hello.domain import Job


def job_id(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != 'https' or parsed.hostname not in ('www.zhipin.com', 'zhipin.com'):
        return ''
    match = re.fullmatch(r'/job_detail/([A-Za-z0-9_-]+)\.html', parsed.path)
    return match[1] if match else ''


def parse_job(account_id, card, detail):
    label = detail.get('contact', '')
    city = detail.get('city') or card.get('city') or None
    if city:
        city = re.split(r'[·•\s]', city)[0]
    return Job(account_id, job_id(card.get('url', '')), detail.get('recruiter_id') or card.get('recruiter_id'),
               detail.get('title') or card.get('title', ''), detail.get('company') or card.get('company', ''),
               city, detail.get('salary') or card.get('salary', ''), detail.get('text', ''), card.get('url', ''),
               'new' if label == '立即沟通' else 'contacted' if label == '继续沟通' else 'unknown')


class NodriverAdapter:
    poll_attempts = 5

    def __init__(self):
        self.browser = None
        self.list_tab = None
        self.detail_tab = None
        self.active_job = None
        self.control = None

    def bind_control(self, control):
        self.control = control

    def guard(self):
        if self.control is None or self.control.pause_requested.is_set() or self.control.closing.is_set():
            raise ActionBlocked('任务暂停或未绑定运行控制')

    async def refresh_job(self, job):
        session = await self.session()
        raw = await self.read_detail(job.url)
        return parse_job(session.account_id, {'url':job.url}, raw)

    async def _action(self, job, action, value=''):
        self.guard()
        session = await self.session()
        if not session.logged_in or session.account_id != job.account_id:
            return ActionResult('failed', '当前账号不匹配，未执行')
        observe = 'observe_attachment' if action == 'attachment' else 'observe'
        before = {'count': 0}
        if action != 'contact':
            before = await self.evaluate(self.detail_tab, script(observe, job, value))
            if not before.get('matched'):
                return ActionResult('failed', '无法核实目标会话，未执行')
        self.guard()
        clicked = await self.evaluate(self.detail_tab, script(action, job, value))
        if not clicked.get('clicked'):
            return ActionResult('failed', '页面控件或目标身份无法核实，未执行')
        for index in range(self.poll_attempts):
            evidence = await self.evaluate(self.detail_tab, script(observe, job, value))
            if evidence.get('matched') and (action == 'contact' or evidence.get('count', 0) > before.get('count', 0)):
                return ActionResult('succeeded', '已核实目标会话' if action == 'contact' else '已观察到新增的己方消息')
            if index + 1 < self.poll_attempts:
                await asyncio.sleep(.5)
        return ActionResult('unknown', '已操作，但送达证据不足；不会自动重试')

    async def open_contact(self, job):
        return await self._action(job, 'contact')

    async def send_text(self, job, text):
        return await self._action(job, 'message', text)

    async def send_attachment(self, job, attachment_id):
        return await self._action(job, 'attachment', attachment_id)

    async def return_to_list(self):
        if self.list_tab is None:
            return False
        await self.list_tab.activate()
        return True

    async def evaluate(self, tab, script):
        if tab is None:
            raise BrowserFailure('请先打开浏览器并手动登录')
        try:
            value = await asyncio.wait_for(tab.evaluate(script), 12)
            return json.loads(value) if isinstance(value, str) else value
        except asyncio.CancelledError:
            raise
        except Exception:
            raise BrowserFailure('页面读取失败，请检查浏览器或重新登录') from None

    async def open(self, profile):
        if self.browser is None:
            import nodriver
            profile.mkdir(parents=True, exist_ok=True)
            self.browser = await nodriver.start(user_data_dir=str(profile), headless=False)
            self.list_tab = await self.browser.get(dom.RECOMMEND_URL)
        else:
            await self.list_tab.get(dom.RECOMMEND_URL)

    async def session(self):
        if self.list_tab is None:
            return SessionInfo(None, False)
        raw = await self.evaluate(self.list_tab, dom.SESSION)
        return SessionInfo(raw.get('account_id'), bool(raw.get('logged_in')))

    async def iter_recommended(self):
        seen = set()
        misses = 0
        while True:
            session = await self.session()
            if not session.logged_in or not session.account_id:
                raise BrowserFailure('登录失效或无法核实当前账号标识，请重新检查登录')
            snapshot = await self.evaluate(self.list_tab, dom.CARDS)
            fresh = [card for card in snapshot.get('cards', []) if card.get('url') not in seen]
            for card in fresh:
                seen.add(card.get('url'))
                if not job_id(card.get('url', '')):
                    yield parse_job(session.account_id, card, {})
                    continue
                detail = await self.read_detail(card['url'])
                yield parse_job(session.account_id, card, detail)
            if snapshot.get('ended'):
                return
            misses = 0 if fresh else misses + 1
            if misses >= self.poll_attempts:
                raise BrowserFailure('推荐列表未继续加载，无法确认是否到底；本轮已停止')
            await self.evaluate(self.list_tab, dom.SCROLL)
            await asyncio.sleep(1)

    async def read_detail(self, url):
        if not job_id(url):
            raise BrowserFailure('岗位链接无法核实')
        if self.detail_tab is None:
            self.detail_tab = await self.browser.get(url, new_tab=True)
        else:
            await self.detail_tab.get(url)
        for _ in range(20):
            raw = await self.evaluate(self.detail_tab, dom.DETAIL)
            if job_id(raw.get('url', '')) == job_id(url) and raw.get('text'):
                return raw
            await asyncio.sleep(.5)
        raise BrowserFailure('岗位详情未能正确加载')

    async def attachments(self):
        raw = await self.evaluate(self.detail_tab or self.list_tab, dom.ATTACHMENTS)
        return list({item['id']: AttachmentInfo(item['id'], item['name']) for item in raw if item.get('id') and item.get('name')}.values())

    async def capabilities(self):
        if self.detail_tab is None and self.list_tab is None:
            return BrowserCapabilities(None, 'unknown')
        raw = await self.evaluate(self.detail_tab or self.list_tab, dom.CAPABILITIES)
        return BrowserCapabilities(raw.get('greeting_limit'), raw.get('initial_contact_behavior', 'unknown'))

    async def close(self):
        if self.browser:
            self.browser.stop()
        self.browser = self.list_tab = self.detail_tab = None
