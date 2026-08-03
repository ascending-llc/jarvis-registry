"""Unit tests for auth-server consent HTML rendering."""

from auth_server.routes.consent_templates import render_consent_page


def test_consent_page_escapes_attacker_controlled_client_metadata() -> None:
    html = render_consent_page(
        client_name='<script>alert("name")</script>',
        client_uri='javascript:alert("uri")',
        redirect_uri='https://evil.example/"><script>alert(1)</script>',
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
    assert "Redirects to" in html
    assert "https://evil.example/&quot;&gt;&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert '<a href="https://evil.example' not in html


def test_consent_page_omits_redirect_uri_when_none() -> None:
    html = render_consent_page(
        client_name="Test App",
        client_uri=None,
        redirect_uri=None,
        ip_address=None,
        registered_at=None,
        nonce="test-nonce",
        approve_action="/auth/oauth2/consent/approve",
        deny_action="/auth/oauth2/consent/deny",
    )

    assert "Redirects to" not in html
