import hashlib
import ipaddress
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx

from boss_say_hello.ai.recovery import RequestRecovery
from boss_say_hello.ai.request_gate import RequestGate
from boss_say_hello.ai.request_trace import RequestTrace
from boss_say_hello.ai.schemas import AiFailure
from boss_say_hello.diagnostics import record
from boss_say_hello.storage.credentials import normalize_secret

OVERVIEW_QUERY = "公司全称 官网 主营业务 招聘 自研 外包 驻场 外派 劳务派遣 用工 经营风险"


def public_url(url):
    try:
        parts = urlsplit(url)
        if (
            parts.scheme != "https"
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.port not in (None, 443)
        ):
            return False
        host = parts.hostname.lower()
        if "." not in host or host.endswith((".local", ".localhost", ".internal")):
            return False
        try:
            return ipaddress.ip_address(host).is_global
        except ValueError:
            return True
    except ValueError:
        return False


class CompanySearch:
    def __init__(self, config, secret, transport=None):
        if config.base_url.rstrip("/") not in {"https://api.moonshot.cn/v1", "https://api.moonshot.ai/v1"}:
            raise ValueError("公司联网查询目前支持 Moonshot 官方 API 地址，请配置对应服务")
        self.client = httpx.AsyncClient(
            base_url=config.base_url.rstrip("/") + "/",
            headers={"Authorization": "Bearer " + normalize_secret(secret)},
            timeout=httpx.Timeout(65, connect=15),
            transport=transport,
            follow_redirects=False,
        )
        self.allowed_urls = set()
        self.recovery = RequestRecovery()
        self.request_gate = RequestGate()

    async def _post(self, operation, payload):
        record("company.search.started", operation=operation)
        stage = "company_" + operation
        trace = RequestTrace(
            stage,
            str(self.client.base_url.join("tools/" + operation)),
            payload,
            secret=self.client.headers["Authorization"].removeprefix("Bearer "),
        )

        async def request():
            response = await self.client.post("tools/" + operation, json=payload)
            response.raise_for_status()
            return response.json()

        try:
            with trace.bind():
                async with self.request_gate.slot(control=self.recovery.control):
                    return await self.recovery.call(lambda: trace.call(request), stage=stage)
        except AiFailure as exc:
            record(
                "ai.request.limited",
                level="WARNING",
                stage="company_" + operation,
                reason=exc.public_message,
                **exc.diagnostic_fields(),
            )
            raise
        except (httpx.HTTPError, ValueError) as exc:
            status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            record(
                "company.search.failed",
                level="ERROR",
                operation=operation,
                http_status=status,
                error_type=type(exc).__name__,
            )
            raise AiFailure(
                "search_failed", "公司联网查询失败，不能视为背景已通过；请检查搜索服务权限及网络"
            ) from None

    async def search(self, company):
        name = company.get("name", "").strip()
        if not name or len(name) > 200:
            raise ValueError("请先读取公司主体")
        query = f"{name} {company.get('city', '')} {OVERVIEW_QUERY}"
        data = await self._post("search_pro", {"text_query": query, "limit": 5, "timeout_seconds": 30})
        sources = []
        for item in data.get("search_results", [])[:5]:
            url = item.get("url", "")
            if not public_url(url):
                continue
            content = "\n".join(
                [item.get("snippet", "")] + [c.get("text", "") for c in item.get("chunks", [])]
            )[:3500]
            if not content.strip():
                continue
            sources.append(
                {
                    "id": hashlib.sha256((url + content).encode()).hexdigest()[:20],
                    "url": url,
                    "title": item.get("title", "")[:200],
                    "text": content,
                    "queried_at": datetime.now(timezone.utc).isoformat(),
                    "angle": "overview",
                }
            )
            self.allowed_urls.add(url)
        record("company.search.succeeded", count=len(sources))
        return sources

    async def fetch(self, url):
        if url not in self.allowed_urls or not public_url(url):
            raise ValueError("仅可读取本轮搜索已发现的公开 HTTPS 来源")
        data = await self._post("fetch", {"url": url})
        return {"url": url, "data": data}

    async def close(self):
        await self.client.aclose()
