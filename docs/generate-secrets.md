# Generate Secret Keys

All keys are generated **locally in your browser** using the [Web Crypto API](https://developer.mozilla.org/en-US/docs/Web/API/Web_Crypto_API). Nothing is sent to any server.

| Variable | Purpose |
|---|---|
| `SECRET_KEY` | Signs sessions and internal tokens |
| `CREDS_KEY` | AES-256 key for encrypting stored MCP server credentials |
| `JWT_PRIVATE_KEY` | RSA-2048 private key — signs JWTs issued by the auth server |
| `JWT_PUBLIC_KEY` | RSA-2048 public key — verifies JWTs across all services |

---

<style>
#key-generator { font-family: inherit; margin: 1.5rem 0; }

.keygen-notice {
  background: var(--md-code-bg-color);
  border-left: 4px solid var(--md-accent-fg-color);
  padding: 0.75rem 1rem;
  border-radius: 0 4px 4px 0;
  margin-bottom: 1.5rem;
  font-size: 0.85rem;
  color: var(--md-typeset-color);
}

.keygen-actions-top { text-align: center; margin-bottom: 1.5rem; }

.keygen-btn {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.5rem 1.2rem;
  border: none;
  border-radius: 4px;
  cursor: pointer;
  font-family: inherit;
  font-size: 0.85rem;
  font-weight: 500;
  transition: opacity 0.15s;
  text-decoration: none !important;
}
.keygen-btn--primary {
  background: var(--md-accent-fg-color);
  color: #fff !important;
  padding: 0.75rem 2.5rem;
  font-size: 1rem;
}
.keygen-btn--sm {
  background: var(--md-accent-fg-color);
  color: #fff !important;
  padding: 0.35rem 0.85rem;
}
.keygen-btn--ghost {
  background: transparent;
  color: var(--md-accent-fg-color) !important;
  border: 1px solid var(--md-accent-fg-color);
  padding: 0.35rem 0.85rem;
}
.keygen-btn:hover { opacity: 0.8; }
.keygen-btn:disabled { opacity: 0.45; cursor: not-allowed; }

.keygen-fields { display: flex; flex-direction: column; gap: 1rem; }

.keygen-field {
  background: var(--md-code-bg-color);
  border: 1px solid var(--md-default-fg-color--lightest);
  border-radius: 6px;
  padding: 1rem;
}

.keygen-field-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
  margin-bottom: 0.6rem;
  flex-wrap: wrap;
}

.keygen-field-label {
  font-family: var(--md-code-font-family);
  font-weight: 700;
  font-size: 0.88rem;
  color: var(--md-typeset-color);
}

.keygen-field-desc {
  font-size: 0.78rem;
  color: var(--md-default-fg-color--light);
  margin-top: 0.15rem;
}

.keygen-field-buttons { display: flex; gap: 0.45rem; flex-shrink: 0; align-items: center; }

.keygen-input, .keygen-textarea {
  width: 100%;
  font-family: var(--md-code-font-family);
  font-size: 0.78rem;
  background: var(--md-default-bg-color);
  border: 1px solid var(--md-default-fg-color--lightest);
  border-radius: 4px;
  padding: 0.4rem 0.6rem;
  color: var(--md-typeset-color);
  box-sizing: border-box;
  outline: none;
}
.keygen-input:focus, .keygen-textarea:focus {
  border-color: var(--md-accent-fg-color);
}
.keygen-textarea {
  height: 130px;
  resize: vertical;
}

.keygen-env-block {
  margin-top: 1.5rem;
  border: 1px solid var(--md-default-fg-color--lightest);
  border-radius: 6px;
  overflow: hidden;
}

.keygen-env-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.55rem 1rem;
  background: var(--md-code-bg-color);
  border-bottom: 1px solid var(--md-default-fg-color--lightest);
}

.keygen-env-pre {
  margin: 0 !important;
  padding: 1rem !important;
  font-size: 0.75rem !important;
  white-space: pre-wrap !important;
  word-break: break-all !important;
  background: var(--md-default-bg-color) !important;
  color: var(--md-typeset-color) !important;
  border: none !important;
  box-shadow: none !important;
  border-radius: 0 !important;
}

.keygen-copied {
  font-size: 0.75rem;
  color: #2e7d32;
  opacity: 0;
  transition: opacity 0.3s;
  align-self: center;
}
.keygen-copied.show { opacity: 1; }
</style>

<div id="key-generator">

  <div class="keygen-notice">
    🔒 Keys are generated entirely in your browser. Refresh the page to clear them.
  </div>

  <div class="keygen-actions-top">
    <button class="keygen-btn keygen-btn--primary" onclick="kgGenerateAll(this)">
      ⚡ Generate All Keys
    </button>
  </div>

  <div class="keygen-fields">

    <div class="keygen-field">
      <div class="keygen-field-header">
        <div>
          <div class="keygen-field-label">SECRET_KEY</div>
          <div class="keygen-field-desc">64 random bytes, base64url-encoded — used for session signing</div>
        </div>
        <div class="keygen-field-buttons">
          <button class="keygen-btn keygen-btn--sm" onclick="kgGenerateSecretKey()">Generate</button>
          <button class="keygen-btn keygen-btn--ghost" onclick="kgCopy('kg-secret-key', this)">Copy</button>
        </div>
      </div>
      <input type="text" id="kg-secret-key" class="keygen-input" readonly placeholder="Click Generate…">
    </div>

    <div class="keygen-field">
      <div class="keygen-field-header">
        <div>
          <div class="keygen-field-label">CREDS_KEY</div>
          <div class="keygen-field-desc">32 random bytes, hex-encoded (64 chars) — AES-256 key for encrypting stored credentials</div>
        </div>
        <div class="keygen-field-buttons">
          <button class="keygen-btn keygen-btn--sm" onclick="kgGenerateCredsKey()">Generate</button>
          <button class="keygen-btn keygen-btn--ghost" onclick="kgCopy('kg-creds-key', this)">Copy</button>
        </div>
      </div>
      <input type="text" id="kg-creds-key" class="keygen-input" readonly placeholder="Click Generate…">
    </div>

    <div class="keygen-field">
      <div class="keygen-field-header">
        <div>
          <div class="keygen-field-label">JWT_PRIVATE_KEY</div>
          <div class="keygen-field-desc">RSA-2048 private key (PKCS8 PEM) — signs JWTs issued by the auth server</div>
        </div>
        <div class="keygen-field-buttons">
          <button class="keygen-btn keygen-btn--sm" id="kg-jwt-btn" onclick="kgGenerateJwtKeys(this)">Generate Pair</button>
          <button class="keygen-btn keygen-btn--ghost" onclick="kgCopy('kg-jwt-private', this)">Copy</button>
        </div>
      </div>
      <textarea id="kg-jwt-private" class="keygen-textarea" readonly placeholder="Click Generate Pair…"></textarea>
    </div>

    <div class="keygen-field">
      <div class="keygen-field-header">
        <div>
          <div class="keygen-field-label">JWT_PUBLIC_KEY</div>
          <div class="keygen-field-desc">RSA-2048 public key (SPKI PEM) — verifies JWTs across all services</div>
        </div>
        <div class="keygen-field-buttons">
          <button class="keygen-btn keygen-btn--ghost" onclick="kgCopy('kg-jwt-public', this)">Copy</button>
        </div>
      </div>
      <textarea id="kg-jwt-public" class="keygen-textarea" readonly placeholder="Generated together with private key…"></textarea>
    </div>

  </div>

  <div class="keygen-env-block">
    <div class="keygen-env-header">
      <span class="keygen-field-label">.env output</span>
      <button class="keygen-btn keygen-btn--ghost" onclick="kgCopyEnv(this)">Copy All</button>
    </div>
    <pre id="kg-env-output" class="keygen-env-pre">Generate keys above, then paste this block into your .env file.</pre>
  </div>

</div>

<script>
(function () {
  function bufferToPem(buffer, type) {
    const b64 = btoa(String.fromCharCode(...new Uint8Array(buffer)));
    const lines = b64.match(/.{1,64}/g).join('\n');
    return '-----BEGIN ' + type + '-----\n' + lines + '\n-----END ' + type + '-----\n';
  }

  function toBase64Url(buf) {
    return btoa(String.fromCharCode(...new Uint8Array(buf)))
      .replace(/\+/g, '-').replace(/\//g, '_').replace(/=/g, '');
  }

  function toHex(buf) {
    return Array.from(new Uint8Array(buf))
      .map(function (b) { return b.toString(16).padStart(2, '0'); }).join('');
  }

  function updateEnv() {
    var sk   = document.getElementById('kg-secret-key').value;
    var ck   = document.getElementById('kg-creds-key').value;
    var priv = document.getElementById('kg-jwt-private').value;
    var pub  = document.getElementById('kg-jwt-public').value;
    if (!sk && !ck && !priv) return;
    var out = [];
    if (sk)   out.push('SECRET_KEY=' + sk);
    if (ck)   out.push('CREDS_KEY=' + ck);
    if (priv) out.push('JWT_PRIVATE_KEY="' + priv + '"');
    if (pub)  out.push('JWT_PUBLIC_KEY="' + pub + '"');
    document.getElementById('kg-env-output').textContent = out.join('\n');
  }

  window.kgGenerateSecretKey = function () {
    var buf = crypto.getRandomValues(new Uint8Array(64));
    document.getElementById('kg-secret-key').value = toBase64Url(buf);
    updateEnv();
  };

  window.kgGenerateCredsKey = function () {
    var buf = crypto.getRandomValues(new Uint8Array(32));
    document.getElementById('kg-creds-key').value = toHex(buf);
    updateEnv();
  };

  window.kgGenerateJwtKeys = async function (btn) {
    var orig = btn ? btn.textContent : null;
    if (btn) { btn.textContent = 'Generating…'; btn.disabled = true; }
    try {
      var kp = await crypto.subtle.generateKey(
        { name: 'RSASSA-PKCS1-v1_5', modulusLength: 2048,
          publicExponent: new Uint8Array([1, 0, 1]), hash: 'SHA-256' },
        true, ['sign', 'verify']
      );
      var privBuf = await crypto.subtle.exportKey('pkcs8', kp.privateKey);
      var pubBuf  = await crypto.subtle.exportKey('spki', kp.publicKey);
      document.getElementById('kg-jwt-private').value = bufferToPem(privBuf, 'PRIVATE KEY');
      document.getElementById('kg-jwt-public').value  = bufferToPem(pubBuf,  'PUBLIC KEY');
      updateEnv();
    } catch (e) {
      console.error('JWT key generation failed:', e);
    } finally {
      if (btn) { btn.textContent = orig; btn.disabled = false; }
    }
  };

  window.kgGenerateAll = async function (btn) {
    var orig = btn ? btn.textContent : null;
    if (btn) { btn.textContent = 'Generating…'; btn.disabled = true; }
    kgGenerateSecretKey();
    kgGenerateCredsKey();
    await kgGenerateJwtKeys(document.getElementById('kg-jwt-btn'));
    if (btn) { btn.textContent = orig; btn.disabled = false; }
  };

  window.kgCopy = function (id, btn) {
    var val = document.getElementById(id).value;
    if (!val) return;
    navigator.clipboard.writeText(val).then(function () {
      if (!btn) return;
      var orig = btn.textContent;
      btn.textContent = 'Copied!';
      setTimeout(function () { btn.textContent = orig; }, 1500);
    });
  };

  window.kgCopyEnv = function (btn) {
    var text = document.getElementById('kg-env-output').textContent;
    navigator.clipboard.writeText(text).then(function () {
      if (!btn) return;
      var orig = btn.textContent;
      btn.textContent = 'Copied!';
      setTimeout(function () { btn.textContent = orig; }, 1500);
    });
  };
})();
</script>

---

## Storing Your Keys

**Local development** — paste the `.env` output directly into your `.env` file.

**Production** — store `JWT_PRIVATE_KEY` and `JWT_PUBLIC_KEY` in a secrets manager:

- AWS: Secrets Manager or Parameter Store (SecureString)
- Azure: Key Vault
- GCP: Secret Manager

!!! warning "Keep your private key secret"
    `JWT_PRIVATE_KEY` is a signing key. Anyone who holds it can issue valid JWTs for
    your deployment. Never commit it to version control or log it.

Once your `.env` is populated, continue to [Step 4: Deploy Services](quick-start.md#step-4-deploy-services).
