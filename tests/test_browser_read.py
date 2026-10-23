import pytest


class Page:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def evaluate(self, code):
        self.calls.append(code)
        for marker, value in self.responses.items():
            if marker in code:
                return value
        raise AssertionError("Unexpected evaluation")


async def test_account_id_required():
    from boss_say_hello.browser.nodriver_adapter import NodriverAdapter

    browser = NodriverAdapter()
    browser.list_tab = Page({"SESSION": {"logged_in": True, "account_id": None}})
    session = await browser.session()
    assert session.logged_in and session.account_id is None


def test_missing_job_id_pending():
    from boss_say_hello.browser.nodriver_adapter import parse_job

    assert parse_job("a1", {"url": "https://example.com/job_detail/x.html", "title": "test"}, {}).id == ""


@pytest.mark.parametrize("label,status", [("立即沟通", "new"), ("继续沟通", "contacted"), ("", "unknown")])
def test_contacted_and_unknown_status(label, status):
    from boss_say_hello.browser.nodriver_adapter import parse_job

    job = parse_job(
        "a1",
        {"url": "https://www.zhipin.com/job_detail/abcd.html", "title": "test"},
        {"contact": label, "text": "job", "salary": "20-30K", "city": "北京"},
    )
    assert job.id == "abcd" and job.contact_status == status


async def test_session_expiry_yields_error():
    from boss_say_hello.browser.nodriver_adapter import NodriverAdapter
    from boss_say_hello.browser.port import BrowserFailure

    browser = NodriverAdapter()
    browser.list_tab = Page({"SESSION": {"logged_in": False, "account_id": None}})
    with pytest.raises(BrowserFailure):
        _ = [j async for j in browser.iter_recommended()]


async def test_reading_never_clicks_contact():
    from boss_say_hello.browser.nodriver_adapter import NodriverAdapter

    browser = NodriverAdapter()
    browser.list_tab = Page(
        {"SESSION": {"logged_in": True, "account_id": "a1"}, "CARDS": {"cards": [], "ended": True}}
    )
    assert [j async for j in browser.iter_recommended()] == []
    assert not any(".click(" in code for code in browser.list_tab.calls)


async def test_recommend_list_end_differs_from_load_failure():
    from boss_say_hello.browser.nodriver_adapter import NodriverAdapter
    from boss_say_hello.browser.port import BrowserFailure

    browser = NodriverAdapter()
    browser.list_tab = Page(
        {"SESSION": {"logged_in": True, "account_id": "a1"}, "CARDS": {"cards": [], "ended": False}}
    )
    browser.poll_attempts = 1
    with pytest.raises(BrowserFailure):
        _ = [j async for j in browser.iter_recommended()]
