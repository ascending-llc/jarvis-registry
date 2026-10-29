"""AS-1962 spike driver: one v2 Client connects to the same server three ways.

  * mode="2026-07-28", elicitation supported -> new mechanism (InputRequiredResult) delivers.
  * mode="2026-07-28", elicitation NOT supported -> plain CallToolResult fallback (the URL as text).
  * mode="legacy" (negotiates 2025-11-25) -> back-channel ctx.elicit_url(), backward compatible.

A client declares URL-elicitation support simply by passing an elicitation_callback.
In-process connection, real protocol logic. Prints the wire shape + self-checks.
Run: uv run run_spike.py
"""

from __future__ import annotations

import json

import anyio
from mcp import Client
from mcp_types import ElicitRequestURLParams, ElicitResult
from server import AUTH_URL, mcp


def _dump(result) -> str:
    return json.dumps(result.model_dump(by_alias=True), indent=2, default=str)


async def _on_elicitation(context, params):
    """Stand in for the user: 'open' the URL and accept."""
    kind = type(params).__name__
    if isinstance(params, ElicitRequestURLParams):
        print(f"    [client callback] {kind}: url={params.url!r} elicitation_id={params.elicitation_id!r}")
    else:
        print(f"    [client callback] {kind} (non-URL): {params.model_dump(by_alias=True)}")
    return ElicitResult(action="accept")


async def _exercise(mode: str, *, supports_elicitation: bool = True) -> dict:
    """Connect once, run hello + authorize, collect results for the self-checks.

    supports_elicitation=False omits the elicitation_callback, so the client does
    not declare URL-elicitation support and the server takes the fallback path.
    """
    label = f"{mode}, elicitation={'yes' if supports_elicitation else 'no'}"
    print(f"\n{'=' * 70}\nCLIENT: {label}\n{'=' * 70}")
    facts: dict = {"mode": mode}
    callback = _on_elicitation if supports_elicitation else None
    async with Client(mcp, mode=mode, elicitation_callback=callback) as client:
        negotiated = client.protocol_version
        facts["negotiated"] = negotiated
        print(f"negotiated protocol version: {negotiated}")

        hello_result = await client.call_tool("hello", {"name": "AS-1962"})
        hello_text = [getattr(c, "text", None) for c in hello_result.content]
        facts["hello"] = hello_text
        print(f"\n[hello] -> {hello_text}")

        # Don't auto-answer: grab the raw first authorize result to inspect the wire shape.
        raw = await client.session.call_tool("authorize", {}, allow_input_required=True)
        facts["raw_type"] = type(raw).__name__
        facts["raw_wire"] = raw.model_dump(by_alias=True)
        print(f"\n[authorize RAW] python type: {type(raw).__name__}")
        print(f"[authorize RAW] wire JSON:\n{_dump(raw)}")

        # Full round-trip: the high-level client runs the callback + retries, returns the final result.
        final = await client.call_tool("authorize", {})
        final_text = [getattr(c, "text", None) for c in final.content]
        facts["final"] = final_text
        print(f"\n[authorize FINAL] -> {final_text}")
    return facts


async def main() -> None:
    modern = await _exercise("2026-07-28")
    fallback = await _exercise("2026-07-28", supports_elicitation=False)
    legacy = await _exercise("legacy")

    # self-checks
    # 1) modern + elicitation support -> InputRequiredResult / URL mode
    assert modern["negotiated"] == "2026-07-28"
    assert modern["hello"] == ["hello AS-1962"]
    assert modern["raw_type"] == "InputRequiredResult"
    assert modern["raw_wire"]["resultType"] == "input_required"
    url_params = modern["raw_wire"]["inputRequests"]["authorize"]["params"]
    assert url_params["mode"] == "url"
    assert url_params["url"] == AUTH_URL
    assert url_params.get("elicitationId") is None  # modern strips it; continuation via request_state
    assert modern["raw_wire"].get("requestState")
    assert modern["final"] == ["Authorization confirmed; proceeding with the real work."]

    # 2) modern WITHOUT elicitation support -> CallToolResult fallback carrying the URL
    assert fallback["raw_type"] == "CallToolResult"
    assert fallback["raw_wire"]["isError"] is True
    assert AUTH_URL in fallback["raw_wire"]["content"][0]["text"]

    # 3) legacy -> back-channel elicit_url, carries elicitationId through
    assert legacy["negotiated"] == "2025-11-25"
    assert "accept" in (legacy["final"][0] or "")

    print(f"\n{'=' * 70}\nALL SELF-CHECKS PASSED\n{'=' * 70}")
    print("Key wire-shape facts (2026-07-28):")
    print(f"  resultType   = {modern['raw_wire']['resultType']!r}")
    print(f"  params.mode  = {url_params['mode']!r}")
    print(f"  elicitationId= {url_params.get('elicitationId')!r}   (stripped on modern wire)")
    print(f"  requestState = {modern['raw_wire']['requestState'][:24]!r}...  (opaque, encrypted)")
    print(f"  fallback     = {fallback['raw_type']} (no elicitation capability -> URL as text)")


if __name__ == "__main__":
    anyio.run(main)
