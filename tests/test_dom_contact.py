"""Real Chromium against a local reproduction of observed BOSS DOM, never sends externally."""

import json
import os

import pytest


@pytest.mark.skipif(os.environ.get("BOSS_TEST_LOCAL_CHROME") != "1", reason="Explicit local Chrome test")
async def test_contact_dialog_editor_identity_and_acknowledgement(tmp_path, job):
    import nodriver

    from boss_say_hello.browser.actions import script
    from boss_say_hello.browser.nodriver_adapter import NodriverAdapter
    from boss_say_hello.browser.selectors import CAPABILITIES

    html = """<!doctype html><meta charset="utf-8">
    <div class="job-detail-box"><a class="op-btn-chat" onclick="contact()">立即沟通</a></div>
    <script>
    window.contactCount=0;window.sendCount=0;
    document.querySelector('.job-detail-box').__vue__={$props:{data:{jobInfo:{encryptId:'j1',encryptUserId:'hr1'}}}};
    function contact(){window.contactCount++;document.body.insertAdjacentHTML('beforeend',`<div class="dialog-wrap">已向BOSS发送消息<button onclick="openChat();this.parentElement.remove()">继续沟通</button></div>`)}
    function openChat(){document.body.insertAdjacentHTML('beforeend',`<div class="chat-container"><div class="chat-conversation"><div class="chat-message"></div><div class="editor-container"><div id="chat-input" contenteditable="true"></div><button class="btn-send disabled" onclick="send()">发送</button></div></div></div>`);
      document.querySelector('.chat-conversation').__vue__={'selectedFriend$':{encryptJobId:'j1',encryptBossId:'hr1'}};
      document.querySelector('.editor-container').__vue__={$data:{MAX_PASTE_LENGTH:1000}};
      document.querySelector('#chat-input').addEventListener('input',()=>queueMicrotask(()=>document.querySelector('.btn-send').classList.remove('disabled')));
    }
    function send(){window.sendCount++;const value=document.querySelector('#chat-input').innerText;
      const e=document.createElement('div');e.className='message-item item-myself';e.dataset.mid='new-message-'+window.sendCount;
      e.innerHTML='<div class="text"><span class="message-status status-loading"></span><span class="text-content"></span></div>';
      e.querySelector('.text-content').innerText=value;e.__vue__={$props:{message:{text:value,status:0,isSelf:true}}};
      document.querySelector('.chat-message').append(e);document.querySelector('#chat-input').textContent='';
    }
    </script>"""
    path = tmp_path / "contact.html"
    path.write_text(html, encoding="utf-8")
    browser = await nodriver.start(
        headless=True,
        user_data_dir=str(tmp_path / "profile"),
        browser_executable_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    )
    try:
        tab = await browser.get(path.as_uri())

        async def run(action, value="", target=None):
            return json.loads(await tab.evaluate(script(action, target or job(), value)))

        assert not (await run("contact", target=job(recruiter_id="wrong")))["clicked"]
        assert (await run("contact"))["clicked"]
        assert (await run("observe"))["can_continue"]
        assert (await run("continue_contact"))["clicked"]
        assert not (await run("continue_contact"))["clicked"]
        assert (await run("observe"))["matched"]
        caps = json.loads(await tab.evaluate(CAPABILITIES))
        assert caps["greeting_limit"] == 200 and caps["limit_source"] == "editor"
        assert not (await run("fill_message", "x" * 201)).get("filled")
        assert (await run("fill_message", "您好，我关注这个岗位。"))["filled"]
        assert not (await run("send_message", "您好，我关注这个岗位。", job(recruiter_id="wrong")))["clicked"]
        assert (await run("send_message", "您好，我关注这个岗位。"))["clicked"]
        assert (await run("observe", "您好，我关注这个岗位。"))["count"] == 0
        await tab.evaluate(
            "document.querySelector('.message-status').className='message-status status-delivery'"
        )
        assert (await run("observe", "您好，我关注这个岗位。"))["count"] == 1
        assert await tab.evaluate("window.sendCount") == 1
        assert await tab.evaluate("window.contactCount") == 1
        await tab.evaluate("document.querySelector('#chat-input').textContent='用户自己的草稿'")
        assert (await run("fill_message", "新招呼语"))["code"] == "existing_draft"
        assert (await run("message_ready", "新招呼语"))["code"] == "draft_changed"
        from unittest.mock import AsyncMock

        from boss_say_hello.browser.port import SessionInfo
        from boss_say_hello.runtime.control import RunControl

        # Reproduce delayed chat initialization clearing the first filled draft.
        await tab.evaluate("""(()=>{
          const input=document.querySelector('#chat-input');input.textContent='';
          window.refillCount=0;input.addEventListener('input',()=>window.refillCount++);
          input.addEventListener('input',()=>setTimeout(()=>{input.textContent='';document.querySelector('.btn-send').classList.add('disabled')},100),{once:true});
          const originalSend=window.send;window.send=()=>{originalSend();setTimeout(()=>document.querySelector('.message-item:last-child .message-status').className='message-status status-delivery',100)};
        })()""")
        adapter = NodriverAdapter()
        adapter.detail_tab = tab
        adapter.session = AsyncMock(return_value=SessionInfo("a1", True))
        adapter.bind_control(RunControl())
        result = await adapter.send_text(job(), "您好，希望沟通合成测试岗位。")
        assert result.outcome == "succeeded"
        assert await tab.evaluate("window.refillCount") == 2
        assert await tab.evaluate("window.sendCount") == 2
        # A manual send also clears the editor. Never refill or send that text again.
        await tab.evaluate(
            "document.querySelector('#chat-input').addEventListener('input',()=>setTimeout(()=>window.send(),100),{once:true})"
        )
        manual_text = "您好，这是手动发送并发合成测试。"
        result = await adapter.send_text(job(), manual_text)
        assert result.outcome == "unknown"
        assert await tab.evaluate("window.sendCount") == 3
        assert await tab.evaluate("window.refillCount") == 3
        for action in ("fill_message", "message_ready", "send_message"):
            blocked = json.loads(await tab.evaluate(script(action, job(), manual_text, baseline_ids=[])))
            assert blocked["code"] == "matching_message_appeared"
        assert await tab.evaluate("window.sendCount") == 3
    finally:
        owner = NodriverAdapter()
        owner.browser = browser
        await owner.close()
