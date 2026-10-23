import json

import httpx
import pytest


async def test_search_uses_same_provider_and_public_company_terms(config):
    from boss_say_hello.agent.search import CompanySearch

    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "search_results": [
                    {
                        "title": "公司官网",
                        "url": "https://company.example/about",
                        "snippet": "产品研发",
                        "chunks": [{"text": "提供客户驻场服务"}],
                    }
                ]
            },
        )

    search = CompanySearch(
        config(base_url="https://api.moonshot.cn/v1"), "test-key", httpx.MockTransport(respond)
    )
    try:
        sources = await search.search({"name": "测试公司", "city": "深圳"}, "employment")
        assert calls[0].url.path == "/v1/tools/search_pro"
        body = json.loads(calls[0].content)
        assert "测试公司" in body["text_query"] and body["limit"] == 5
        assert sources[0]["id"] and "驻场" in sources[0]["text"]
        with pytest.raises(ValueError):
            await search.fetch("http://127.0.0.1/secrets")
    finally:
        await search.close()


def test_unsupported_search_endpoint_rejected(config):
    from boss_say_hello.agent.search import CompanySearch

    with pytest.raises(ValueError):
        CompanySearch(config(), "test-key")


def test_company_cache_separates_identity_and_expiry(tmp_path):
    from boss_say_hello.storage.companies import CompanyCache

    now = [1000000.0]
    cache = CompanyCache(tmp_path / "companies.json", clock=lambda: now[0])
    identity = {"id": "a", "name": "同名公司", "city": "深圳"}
    cache.put(identity, [{"id": "e", "url": "https://example.com", "text": "实际资料"}])
    assert CompanyCache(cache.path, clock=lambda: now[0]).get(identity, 7)
    assert cache.get({**identity, "id": "b"}, 7) is None
    now[0] += 8 * 86400
    assert cache.get(identity, 7) is None


def test_adding_new_source_does_not_extend_old_source_lifetime(tmp_path):
    from boss_say_hello.storage.companies import CompanyCache

    now = [1000000.0]
    cache = CompanyCache(tmp_path / "companies.json", clock=lambda: now[0])
    identity = {"id": "company"}
    cache.put(identity, [{"id": "old", "text": "old"}])
    now[0] += 6 * 86400
    cache.put(identity, cache.get(identity, 7) + [{"id": "new", "text": "new"}])
    now[0] += 2 * 86400
    assert [s["id"] for s in cache.get(identity, 7)] == ["new"]
