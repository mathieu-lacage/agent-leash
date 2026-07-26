"""
mitmproxy addon that gates outbound requests through the sandbox server.

Run as:
    mitmdump --listen-host 127.0.0.1 --listen-port PORT \
             -s /path/to/proxy_addon.py \
             --set sandbox_id=ID --set server_url=http://localhost:7612
"""

import httpx
from mitmproxy import http, ctx


class DomainGatekeeper:
    sandbox_id: str = ""
    server_url: str = "http://localhost:7612"

    def load(self, loader):
        loader.add_option("sandbox_id", str, "", "Sandbox ID")
        loader.add_option(
            "server_url", str, "http://localhost:7612", "Sandbox server URL"
        )

    def configure(self, updates):
        if "sandbox_id" in updates:
            self.sandbox_id = ctx.options.sandbox_id
        if "server_url" in updates:
            self.server_url = ctx.options.server_url

    async def request(self, flow: http.HTTPFlow) -> None:
        domain = flow.request.pretty_host
        # skip localhost (the proxy control channel itself)
        if domain in ("localhost", "127.0.0.1", "::1"):
            return

        ctx.log.info(
            f"[{self.sandbox_id}] {flow.request.method} {flow.request.pretty_url}"
        )

        try:
            async with httpx.AsyncClient(timeout=70.0, trust_env=False) as client:
                r = await client.post(
                    f"{self.server_url}/api/proxy/request-approval",
                    json={"sandbox_id": self.sandbox_id, "domain": domain},
                )
            data = r.json()
            if data.get("action") != "allow":
                flow.kill()
        except Exception as e:
            ctx.log.warn(f"sandbox proxy: error checking {domain}: {e} — blocking")
            flow.kill()


addons = [DomainGatekeeper()]
