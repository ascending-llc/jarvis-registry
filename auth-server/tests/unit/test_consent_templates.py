"""Unit tests for auth-server consent HTML rendering."""

from auth_server.routes.consent_templates import render_consent_page, render_redirect_error_consent_page


def test_consent_page_escapes_attacker_controlled_client_metadata() -> None:
    html = render_consent_page(
        client_name='<script>alert("name")</script>',
        client_uri='javascript:alert("uri")',
        redirect_uri='https://evil.example/"><script>alert(1)</script>',
        ip_address="<img src=x onerror=alert(1)>",
        registered_at=1_700_000_000,
        scopes=[],
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
        scopes=[],
        nonce="test-nonce",
        approve_action="/auth/oauth2/consent/approve",
        deny_action="/auth/oauth2/consent/deny",
    )

    assert "Redirects to" not in html
    assert '<table class="scopes">' not in html


def test_consent_page_renders_escaped_scope_table() -> None:
    html = render_consent_page(
        client_name="Test App",
        client_uri=None,
        redirect_uri=None,
        ip_address=None,
        registered_at=None,
        scopes=[("scope<script>", "Allow <b>everything</b>"), ("scope-without-description", None)],
        nonce="test-nonce",
        approve_action="/auth/oauth2/consent/approve",
        deny_action="/auth/oauth2/consent/deny",
    )

    assert '<table class="scopes">' in html
    assert "<th>Permission</th><th>What it allows</th>" in html
    assert "<code>scope&lt;script&gt;</code>" in html
    assert "Allow &lt;b&gt;everything&lt;/b&gt;" in html
    assert "<script>" not in html
    assert ">None<" not in html


def test_redirect_error_consent_page_escapes_attacker_controlled_values() -> None:
    html = render_redirect_error_consent_page(
        redirect_uri='http://localhost/callback"><script>alert("redirect")</script>',
        error="invalid_client",
        error_description='<script>alert("description")</script>',
        nonce='nonce"><script>alert("nonce")</script>',
        approve_action='/auth/oauth2/redirect-error-consent/approve?x="bad"',
        deny_action='/auth/oauth2/redirect-error-consent/deny?x="bad"',
    )

    assert "<script>" not in html
    assert "&lt;script&gt;alert(&quot;description&quot;)&lt;/script&gt;" in html
    assert "http://localhost/callback&quot;&gt;&lt;script&gt;alert(&quot;redirect&quot;)&lt;/script&gt;" in html
    assert '<a href="' not in html
    assert 'method="POST"' in html
    assert 'action="/auth/oauth2/redirect-error-consent/approve?x=&quot;bad&quot;"' in html
    assert 'action="/auth/oauth2/redirect-error-consent/deny?x=&quot;bad&quot;"' in html
