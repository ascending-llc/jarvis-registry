"""Unit tests for auth-server consent HTML rendering."""

from auth_server.routes.consent_templates import render_consent_page


def test_consent_page_escapes_attacker_controlled_client_metadata() -> None:
    html = render_consent_page(
        client_name='<script>alert("name")</script>',
        client_uri='javascript:alert("uri")',
        ip_address="<img src=x onerror=alert(1)>",
        registered_at=1_700_000_000,
        nonce='nonce"><script>alert(1)</script>',
        approve_action="/auth/oauth2/consent/approve",
        deny_action="/auth/oauth2/consent/deny",
    )

    assert "<script>" not in html
    assert "&lt;script&gt;alert(&quot;name&quot;)&lt;/script&gt;" in html
    assert "javascript:alert(&quot;uri&quot;)" in html
    assert 'href="javascript:' not in html
    assert "<img" not in html
    assert 'method="POST"' in html
    assert 'action="/auth/oauth2/consent/approve"' in html
    assert 'action="/auth/oauth2/consent/deny"' in html
