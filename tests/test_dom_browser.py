"""Real JavaScript against a synthetic local page; never accesses BOSS accounts."""

import json
import os

import pytest

from boss_say_hello.browser import selectors as dom


@pytest.mark.skipif(
    os.environ.get("BOSS_TEST_LOCAL_CHROME") != "1", reason="Explicit local headless Chrome DOM test"
)
async def test_current_recommendation_dom(tmp_path):
    import nodriver

    html = """<!doctype html><meta charset="utf-8"><div id="wrap">
    <div class="c-expect-select"><div class="expect-item" onclick="this.classList.add('active')">Java(深圳)</div><div class="add-expect-btn">添加求职期望</div><button style="display:none">隐藏项</button></div>
    <div class="c-filter-condition"><div class="city-label"><span class="cur-city-label">深圳</span></div><div class="condition-filter-select"><ul><li ka="sel-job-rec-salary-406" class="selected">20-50K</li></ul></div></div>
    <div class="user-nav"><a ka="header-username">synthetic user</a></div>
    <div class="job-card-box"><a class="job-name" href="https://www.zhipin.com/job_detail/synthetic.html">Python</a><span class="job-salary">20-30K</span><span class="boss-name">合成公司</span><span class="company-location">北京</span></div>
    <div class="job-detail-box"><div class="job-detail-header"><span class="job-name">Python</span><a class="op-btn-chat">立即沟通</a></div><div class="job-detail-body">Python 接口开发</div></div></div>
    <script>
    document.querySelector('#wrap').__vue__={$store:{state:{hasLoadedUser:true,userInfo:{encryptUserId:'synthetic-account'}}}};
    document.querySelector('.job-detail-box').__vue__={$props:{data:{jobInfo:{encryptId:'synthetic',encryptUserId:'synthetic-hr',jobName:'Python',locationName:'北京',salaryDesc:'20-30K',postDescription:'Python 接口开发'},brandComInfo:{brandName:'合成公司'},bossInfo:{name:'王先生'}}}};
    </script>"""
    path = tmp_path / "synthetic.html"
    path.write_text(html, encoding="utf-8")
    browser = await nodriver.start(
        headless=True,
        user_data_dir=str(tmp_path / "profile"),
        browser_executable_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    )
    try:
        page = await browser.get(path.as_uri())
        session = json.loads(await page.evaluate(dom.SESSION))
        assert session == {"logged_in": True, "account_id": "synthetic-account"}
        cards = json.loads(await page.evaluate(dom.CARDS))["cards"]
        assert cards[0]["salary"] == "20-30K" and cards[0]["company"] == "合成公司"
        detail = json.loads(await page.evaluate(dom.DETAIL))
        assert detail["recruiter_id"] == "synthetic-hr" and detail["city"] == "北京"
        assert detail["recruiter_name"] == "王先生"
        await page.evaluate(
            "delete document.querySelector('.job-detail-box').__vue__.$props.data.bossInfo; document.querySelector('.job-detail-box').insertAdjacentHTML('beforeend', '<div class=\"boss-info\"><span class=\"name\">张女士</span></div>')"
        )
        assert json.loads(await page.evaluate(dom.DETAIL))["recruiter_name"] == "张女士"
        from boss_say_hello.browser.nodriver_adapter import NodriverAdapter

        adapter = NodriverAdapter()
        adapter.list_tab = page
        assert await adapter.existing_expectations() == [
            {"label": "Java(深圳)", "title": "Java", "city": "深圳"}
        ]
        await page.evaluate(
            "window.contactClicks=0; document.querySelector('.op-btn-chat').onclick=()=>window.contactClicks++"
        )
        detail = await adapter.read_detail("https://www.zhipin.com/job_detail/synthetic.html")
        assert detail["recruiter_id"] == "synthetic-hr"
        assert await page.evaluate("window.contactClicks") == 0
        from boss_say_hello.browser.port import JobUnavailable
        from boss_say_hello.runtime.control import RunControl

        adapter.bind_control(RunControl())
        assert await adapter.current_candidate_ids() == {"synthetic"}
        with pytest.raises(JobUnavailable):
            await adapter.read_detail("https://www.zhipin.com/job_detail/missing.html")
        from boss_say_hello.browser.preferences import SNAPSHOT, action_script

        state = json.loads(await page.evaluate(SNAPSHOT))
        labels = [c["label"] for c in state["controls"]]
        assert "20-50K" not in labels and "深圳" not in labels
        assert "立即沟通" not in labels and "隐藏项" not in labels
        control = next(c for c in state["controls"] if c["label"] == "Java(深圳)")
        assert json.loads(await page.evaluate(action_script(control["id"])))["acted"]
        current = json.loads(await page.evaluate(SNAPSHOT))
        assert current["expectations"][0]["selected"]
        assert not json.loads(await page.evaluate(action_script(control["id"])))["acted"]
        assert not any(c["label"] == "添加求职期望" for c in current["controls"])
        await page.evaluate(
            """document.body.insertAdjacentHTML('beforeend', `<div class="expectation-form"><h3>添加求职期望</h3><div class="form-item"><span class="item-label">期望职位</span><input type="text" value="Java"></div><button onclick="window.saved=true">完成</button></div>`)"""
        )
        observed = json.loads(await page.evaluate(SNAPSHOT))
        assert not any(c["label"] == "完成" or c["kind"] == "input" for c in observed["controls"])
        # An existing control must also be rejected if its DOM is repurposed.
        selected = next(c for c in observed["controls"] if c["label"] == "Java(深圳)")
        await page.evaluate(
            "document.querySelector('.expectation-form').append(document.querySelector('.expect-item'))"
        )
        assert not json.loads(await page.evaluate(action_script(selected["id"])))["acted"]
        assert not await page.evaluate("!!window.saved")
    finally:
        from boss_say_hello.browser.nodriver_adapter import NodriverAdapter

        owner = NodriverAdapter()
        owner.browser = browser
        await owner.close()
