// Passkeys (WebAuthn) for two-factor sign in. Two forms use it:
// - Settings, form[data-passkey-add]: the server checks the password and sends the options
//   (data-options-url), the browser makes a passkey, and the form is sent with it.
// - The second sign in step, form[data-passkey-signin]: the server sends a challenge, the
//   browser signs it with one of the account's passkeys, and the form is sent with that.
// The server checks everything again; this file only carries the browser's answers.
(function () {
  function toBytes(b64url) {
    var b64 = b64url.replace(/-/g, "+").replace(/_/g, "/");
    while (b64.length % 4) b64 += "=";
    var raw = atob(b64), out = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
    return out.buffer;
  }
  function toText(buffer) {
    if (!buffer) return null;
    var bytes = new Uint8Array(buffer), raw = "";
    for (var i = 0; i < bytes.length; i++) raw += String.fromCharCode(bytes[i]);
    return btoa(raw).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }
  function showError(form, text) {
    var box = form.querySelector("[data-passkey-error]");
    if (box) { box.textContent = text; box.hidden = false; }
    form.querySelectorAll("[type=submit]").forEach(function (b) { b.disabled = false; });
  }
  function clearError(form) {
    var box = form.querySelector("[data-passkey-error]");
    if (box) { box.textContent = ""; box.hidden = true; }
  }
  // Why the browser's prompt ended without a passkey, in words people can act on.
  function promptError(err, adding) {
    var name = err && err.name;
    if (name === "NotAllowedError" || name === "AbortError") {
      return adding ? "No passkey was added: the prompt was closed or timed out. Please try again."
                    : "The passkey prompt was closed or timed out. Please try again.";
    }
    if (name === "InvalidStateError" && adding) {
      return "This device or password manager already has a passkey for your account.";
    }
    if (name === "SecurityError") {
      return "Passkeys only work on the site's own address. Open Nomad Life from its usual address and try again.";
    }
    return adding ? "The passkey could not be added. Please try again."
                  : "The passkey could not be used. Please try again.";
  }
  // The server's JSON, or an error with its message (or one for a page left open too long).
  function askServer(form) {
    return fetch(form.getAttribute("data-options-url"), {
      method: "POST", body: new FormData(form), credentials: "same-origin",
      headers: { "Accept": "application/json" }
    }).then(function (resp) {
      return resp.json().catch(function () { return { error: "Please reload the page and try again." }; })
        .then(function (data) {
          if (!resp.ok || data.error) {
            if (data.reload) { window.location.reload(); }
            throw { server: data.error || "Please reload the page and try again." };
          }
          return data;
        });
    });
  }
  function send(form, credential) {
    form.querySelector('input[name="credential"]').value = JSON.stringify(credential);
    // A passkey is already made or signed: the password is not needed on the way back.
    form.setAttribute("data-passkey-ready", "1");
    form.submit();
  }
  var supported = !!(window.PublicKeyCredential && navigator.credentials && window.fetch);

  document.querySelectorAll("form[data-passkey-add]").forEach(function (form) {
    form.addEventListener("submit", function (e) {
      if (form.getAttribute("data-passkey-ready")) return;
      e.preventDefault();
      clearError(form);
      if (!supported) {
        showError(form, "This browser cannot make passkeys. Try a current version of Safari, Chrome, Edge or Firefox.");
        return;
      }
      form.querySelectorAll("[type=submit]").forEach(function (b) { b.disabled = true; });
      askServer(form).then(function (o) {
        o.challenge = toBytes(o.challenge);
        o.user.id = toBytes(o.user.id);
        (o.excludeCredentials || []).forEach(function (c) { c.id = toBytes(c.id); });
        return navigator.credentials.create({ publicKey: o });
      }).then(function (cred) {
        var r = cred.response;
        send(form, {
          id: cred.id, rawId: toText(cred.rawId), type: cred.type,
          authenticatorAttachment: cred.authenticatorAttachment || null,
          clientExtensionResults: cred.getClientExtensionResults ? cred.getClientExtensionResults() : {},
          response: {
            clientDataJSON: toText(r.clientDataJSON),
            attestationObject: toText(r.attestationObject),
            transports: r.getTransports ? r.getTransports() : []
          }
        });
      }).catch(function (err) {
        showError(form, err && err.server ? err.server : promptError(err, true));
      });
    });
  });

  document.querySelectorAll("form[data-passkey-signin]").forEach(function (form) {
    form.addEventListener("submit", function (e) {
      if (form.getAttribute("data-passkey-ready")) return;
      e.preventDefault();
      clearError(form);
      if (!supported) {
        showError(form, "This browser cannot use passkeys. Try a current version of Safari, Chrome, Edge or Firefox.");
        return;
      }
      form.querySelectorAll("[type=submit]").forEach(function (b) { b.disabled = true; });
      askServer(form).then(function (o) {
        o.challenge = toBytes(o.challenge);
        (o.allowCredentials || []).forEach(function (c) { c.id = toBytes(c.id); });
        return navigator.credentials.get({ publicKey: o });
      }).then(function (cred) {
        var r = cred.response;
        send(form, {
          id: cred.id, rawId: toText(cred.rawId), type: cred.type,
          authenticatorAttachment: cred.authenticatorAttachment || null,
          clientExtensionResults: cred.getClientExtensionResults ? cred.getClientExtensionResults() : {},
          response: {
            clientDataJSON: toText(r.clientDataJSON),
            authenticatorData: toText(r.authenticatorData),
            signature: toText(r.signature),
            userHandle: toText(r.userHandle)
          }
        });
      }).catch(function (err) {
        showError(form, err && err.server ? err.server : promptError(err, false));
      });
    });
  });
})();
