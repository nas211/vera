/* WHY × Yellow customer app. Auth = Supabase Auth (supabase-js, anon key). Everything else
   goes through the WHY backend at /yellow/api with the user's access token. The browser
   never receives, stores or redisplays the API secret: it is posted once and the inputs are
   cleared immediately. */
(() => {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const API = "/yellow/api";
  const state = { cfg: null, sb: null, session: null, me: null, mode: "signin", poll: null };

  // ---------------------------------------------------------------- helpers
  const toast = (msg) => { const t = document.createElement("div"); t.className = "toast"; t.textContent = msg; $("#toasts").appendChild(t); setTimeout(() => t.remove(), 4500); };
  const showPane = (p) => document.querySelectorAll(".pane").forEach((el) => el.classList.toggle("is-active", el.dataset.pane === p));
  const usd = (v) => v == null ? "—" : "$" + Number(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const usd0 = (v) => "$" + Number(v).toLocaleString("en-US");
  const busy = (btn, on, label) => { if (!btn) return; if (on) { btn.dataset.label = btn.textContent; btn.textContent = label || "Working…"; btn.disabled = true; } else { btn.textContent = btn.dataset.label || btn.textContent; btn.disabled = false; } };
  const showError = (msg) => { $("#globalErrorText").textContent = msg; $("#globalError").classList.remove("hidden"); };
  const hideError = () => $("#globalError").classList.add("hidden");

  async function api(method, path, body) {
    const headers = { "Content-Type": "application/json" };
    if (state.session) headers.Authorization = "Bearer " + state.session.access_token;
    const r = await fetch(API + path, { method, headers, body: body ? JSON.stringify(body) : undefined, cache: "no-store" });
    let j = null; try { j = await r.json(); } catch (e) { j = null; }
    if (!r.ok) { const err = new Error((j && (j.error + (j.code ? ":" + j.code : ""))) || ("http_" + r.status)); err.status = r.status; err.body = j; throw err; }
    return j;
  }

  const ERRORS = {
    invalid_api_key_format: "That does not look like a Yellow API key.",
    invalid_api_secret_format: "That does not look like a Yellow API secret.",
    key_equals_secret: "API key and secret must be different.",
    credential_already_bound: "This Yellow API key is already connected to another WHY account.",
    pause_before_reconnect: "Pause trading before replacing the credential.",
    open_positions_managed: "Wait until WHY has no open positions for this account before replacing the credential.",
    not_verified: "The Yellow connection is not verified yet.",
    activation_not_allowed_from_state: "Trading cannot be activated from the current state.",
    size_not_allowed: "Choose one of the listed position sizes.",
    rate_limited: "Too many attempts. Try again in a minute.",
    vault_unavailable: "The credential vault is unavailable. Nothing was stored. Try again later.",
    supabase_not_configured: "Sign-in is not configured on this server yet.",
    "unauthorized:token_expired": "Your session expired. Please sign in again.",
  };
  const friendly = (e) => ERRORS[e.message] || ERRORS[(e.message || "").split(":")[0]] || ("Request failed (" + e.message + ").");

  // ---------------------------------------------------------------- partner attribution
  // A partner link (e.g. /yellow/app/?ref=yellow or /r/yellow) tags the signup with the partner
  // code. First touch wins: the code is kept in localStorage until the account is created and
  // travels in Supabase's signUp user metadata, where the team dashboard reads it.
  const REF_KEY = "why_ref";
  function captureRef() {
    try {
      const q = new URLSearchParams(location.search), ref = (q.get("ref") || "").trim().toLowerCase();
      if (ref && /^[a-z0-9][a-z0-9_-]{0,31}$/.test(ref) && !localStorage.getItem(REF_KEY)) {
        localStorage.setItem(REF_KEY, JSON.stringify({ partner: ref, at: new Date().toISOString(), landing: location.pathname, referrer: (document.referrer || "").slice(0, 200) }));
      }
      if (q.has("ref")) { q.delete("ref"); history.replaceState(null, "", location.pathname + (q.toString() ? "?" + q : "") + location.hash); }
    } catch (e) {}
  }
  function signupMeta() {
    try { const r = JSON.parse(localStorage.getItem(REF_KEY) || "null"); return r && r.partner ? { partner: r.partner, partner_at: r.at, landing: r.landing, referrer: r.referrer } : {}; } catch (e) { return {}; }
  }
  captureRef();

  // ---------------------------------------------------------------- boot
  async function boot() {
    try { state.cfg = await api("GET", "/public-config"); } catch (e) { showError("The WHY backend is unreachable."); return; }
    if (!state.cfg.configured || !window.supabase) { $("#configNotice").classList.remove("hidden"); showPane("auth"); $("#authSubmit").disabled = true; return; }
    state.sb = window.supabase.createClient(state.cfg.supabase_url, state.cfg.supabase_anon_key, { auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true } });
    state.sb.auth.onAuthStateChange((ev, session) => {
      state.session = session; renderWho();
      if (ev === "PASSWORD_RECOVERY") { state.recovering = true; showPane("auth"); showAuthCard("recover"); }
    });
    if (/type=recovery/.test(location.hash)) { state.recovering = true; }
    const { data } = await state.sb.auth.getSession();
    state.session = data.session;
    renderWho();
    if (state.recovering) { showPane("auth"); showAuthCard("recover"); return; }
    if (state.session) await loadMe(); else showPane("auth");
  }

  function renderWho() {
    const email = state.session && state.session.user ? state.session.user.email : "";
    $("#whoEmail").textContent = email || "";
    $("#signOutBtn").classList.toggle("hidden", !state.session);
  }

  async function loadMe() {
    try {
      hideError();
      state.me = await api("GET", "/me");
      render();
    } catch (e) {
      if (e.status === 401) { await signOut(); return; }
      showError(friendly(e));
    }
  }

  // ---------------------------------------------------------------- auth
  function setMode(m) {
    state.mode = m;
    $("#authTitle").textContent = m === "signup" ? "Create your WHY account" : "Sign in to WHY";
    $("#authSubmit").textContent = m === "signup" ? "Create Account" : "Sign In";
    $("#confirmField").classList.toggle("hidden", m !== "signup");
    $("#termsField").classList.toggle("hidden", m !== "signup");
    $("#authPass").autocomplete = m === "signup" ? "new-password" : "current-password";
    $("#authSwitch").innerHTML = m === "signup" ? 'Already have an account? <a href="#" class="link" id="toSignin">Sign in</a>' : 'New to WHY? <a href="#" class="link" id="toSignup">Create an account</a>';
    const a = $("#toSignin") || $("#toSignup"); a.addEventListener("click", (e) => { e.preventDefault(); setMode(m === "signup" ? "signin" : "signup"); });
  }
  $("#toSignup").addEventListener("click", (e) => { e.preventDefault(); setMode("signup"); });

  $("#authForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const email = $("#authEmail").value.trim(), pw = $("#authPass").value, err = $("#authError");
    err.classList.add("hidden");
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(email) || pw.length < 8) { err.textContent = "Enter a valid email and a password of at least 8 characters."; err.classList.remove("hidden"); return; }
    if (state.mode === "signup") {
      if (pw !== $("#authPass2").value) { err.textContent = "Passwords don't match."; err.classList.remove("hidden"); return; }
      if (!$("#authTerms").checked) { err.textContent = "Please accept the terms to continue."; err.classList.remove("hidden"); return; }
    }
    busy($("#authSubmit"), true, state.mode === "signup" ? "Creating account…" : "Signing in…");
    try {
      const res = state.mode === "signup" ? await state.sb.auth.signUp({ email, password: pw, options: { data: signupMeta() } }) : await state.sb.auth.signInWithPassword({ email, password: pw });
      if (res.error) throw res.error;
      $("#authPass").value = ""; $("#authPass2").value = "";
      if (!res.data.session) { err.textContent = "Check your email to confirm your account, then sign in."; err.classList.remove("hidden"); return; }
      state.session = res.data.session; renderWho();
      await loadMe();
    } catch (ex) { err.textContent = ex.message || "Sign-in failed."; err.classList.remove("hidden"); }
    finally { busy($("#authSubmit"), false); }
  });

  // ---------------------------------------------------------------- password reset (Supabase recovery mail -> back here)
  const authCard = () => $("section[data-pane=auth] .onb-card:not(#forgotCard):not(#recoverCard)");
  function showAuthCard(which) { authCard().classList.toggle("hidden", which !== "auth"); $("#forgotCard").classList.toggle("hidden", which !== "forgot"); $("#recoverCard").classList.toggle("hidden", which !== "recover"); }
  $("#toForgot").addEventListener("click", (e) => { e.preventDefault(); $("#forgotEmail").value = $("#authEmail").value; showAuthCard("forgot"); });
  $("#forgotBack").addEventListener("click", (e) => { e.preventDefault(); showAuthCard("auth"); });
  $("#forgotForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const email = $("#forgotEmail").value.trim(), err = $("#forgotError"), info = $("#forgotInfo");
    err.classList.add("hidden"); info.classList.add("hidden");
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(email)) { err.textContent = "Enter a valid email address."; err.classList.remove("hidden"); return; }
    busy($("#forgotSubmit"), true, "Sending…");
    try {
      const { error } = await state.sb.auth.resetPasswordForEmail(email, { redirectTo: location.origin + "/yellow/app/" });
      if (error) throw error;
      info.textContent = "If an account exists for that email, a reset link is on its way. Check spam too."; info.classList.remove("hidden");
    } catch (ex) { err.textContent = ex.message || "Could not send the reset email."; err.classList.remove("hidden"); }
    finally { busy($("#forgotSubmit"), false); }
  });
  $("#recoverForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const pw = $("#recoverPass").value, err = $("#recoverError"); err.classList.add("hidden");
    if (pw.length < 8) { err.textContent = "Use at least 8 characters."; err.classList.remove("hidden"); return; }
    if (pw !== $("#recoverPass2").value) { err.textContent = "Passwords don't match."; err.classList.remove("hidden"); return; }
    busy($("#recoverSubmit"), true, "Saving…");
    try {
      const { error } = await state.sb.auth.updateUser({ password: pw });
      if (error) throw error;
      $("#recoverPass").value = $("#recoverPass2").value = "";
      state.recovering = false; showAuthCard("auth"); toast("Password updated.");
      await loadMe();
    } catch (ex) { err.textContent = ex.message || "Could not save the password."; err.classList.remove("hidden"); }
    finally { busy($("#recoverSubmit"), false); }
  });

  async function signOut() { try { if (state.sb) await state.sb.auth.signOut(); } catch (e) {} state.session = null; state.me = null; renderWho(); stopPoll(); showPane("auth"); }
  $("#signOutBtn").addEventListener("click", signOut);

  // ---------------------------------------------------------------- connect
  $("#revealSecret").addEventListener("click", () => { const i = $("#apiSecret"); i.type = i.type === "password" ? "text" : "password"; });
  $("#connectForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const key = $("#apiKey").value.trim(), secret = $("#apiSecret").value.trim(), err = $("#connectError");
    err.classList.add("hidden");
    if (key.length < 16 || secret.length < 16) { err.textContent = "Paste both the API key and the API secret."; err.classList.remove("hidden"); return; }
    busy($("#connectSubmit"), true, "Verifying with Yellow…");
    try {
      const me = await api("POST", "/me/yellow/connect", { api_key: key, api_secret: secret });
      $("#apiKey").value = ""; $("#apiSecret").value = ""; $("#apiSecret").type = "password";   // never keep the secret around
      state.me = me; render();
      toast(me.account.state === "VERIFIED" ? "Yellow connected and verified." : "Credential stored. " + stateText(me.account).title);
    } catch (ex) { $("#apiSecret").value = ""; err.textContent = friendly(ex); err.classList.remove("hidden"); }
    finally { busy($("#connectSubmit"), false); }
  });

  // ---------------------------------------------------------------- dashboard
  const PILL = { DISCONNECTED: "pill-muted", PENDING_YELLOW_VERIFICATION: "pill-amber", VERIFIED: "pill-green", ACTIVE: "pill-green", PAUSED: "pill-amber", DRAINING: "pill-amber", ERROR: "pill-red" };
  function stateText(a) {
    switch (a.state) {
      case "DISCONNECTED": return { title: "Not connected", text: a.state_reason === "invalid_credential" ? "Yellow rejected the last credential. Nothing was kept — connect again with a valid key." : a.state_reason === "forbidden_permission" ? "The last key had withdrawal or transfer permission and was rejected. Create a trading-only key." : "" };
      case "PENDING_YELLOW_VERIFICATION": return { title: "Pending Yellow verification", text: a.verification_reason === "permission_metadata_unavailable" ? "Your credential works (account read OK), but Yellow does not expose its permission scopes to API keys, so WHY cannot confirm withdrawal is disabled. Trading stays locked until verification completes." : a.verification_reason && a.verification_reason.startsWith("yellow_unreachable") ? "Yellow could not be reached. Your credential is stored securely; re-check verification later." : "Verification in progress." };
      case "VERIFIED": return { title: "Verified — ready to activate", text: "Activate trading when you are ready." };
      case "ACTIVE": return { title: "Trading active", text: "WHY opens coin-minimum positions when your Yellow balance has available margin." };
      case "PAUSED": return { title: "Paused", text: "No new entries. Open positions keep being managed." };
      case "DRAINING": return { title: "Size change in progress", text: "New entries are paused until every open position at the old size has closed. Existing positions are never resized." };
      case "ERROR": return { title: "Needs attention", text: a.state_reason || "Trading paused." };
      default: return { title: a.state, text: "" };
    }
  }
  function render() {
    const me = state.me; if (!me) return;
    const a = me.account, c = me.config, rt = me.runtime || {};
    if (a.state === "DISCONNECTED") { showPane("connect"); startPoll(false); renderConnectExtras(a); return; }
    showPane("dash"); startPoll(true);
    const pill = $("#statePill"); pill.className = "pill " + (PILL[a.state] || "pill-muted"); pill.textContent = a.state.replace(/_/g, " ");
    $("#kKey").textContent = a.api_key_masked || "—";
    $("#kVerify").textContent = a.verification_state === "VERIFIED" ? (a.permission_source === "yellow_api" ? "Verified (Yellow)" : "Verified (owner attestation)") : a.verification_state === "PENDING" ? "Pending" : a.verification_state.replace(/_/g, " ");
    $("#kAsid").textContent = a.app_session_id || "—";
    $("#kEquity").textContent = usd(a.balance_equity); $("#kAvail").textContent = usd(a.balance_available);
    $("#kOpen").textContent = (rt.open_positions ?? 0) + " / " + c.max_open_positions;
    const st = stateText(a); $("#stateNotice").classList.toggle("hidden", !st.text); $("#stateNoticeTitle").textContent = st.title; $("#stateNoticeText").textContent = st.text;
    $("#sizeHint").textContent = `10× leverage · ${usd(a.balance_available)} available at Yellow · each trade uses that coin's minimum valid order · up to ${c.max_open_positions} open positions.`;
    const pend = c.pending_position_size_usd; $("#pendingHint").classList.toggle("hidden", !pend); $("#sizeCancel").classList.toggle("hidden", !pend);
    if (pend) $("#pendingHint").textContent = `Scheduled: ${usd0(pend)} applies once the ${rt.open_positions ?? 0} open position(s) at ${usd0(c.position_size_usd)} have closed.`;
    const canActivate = (a.state === "VERIFIED" || a.state === "PAUSED") && a.verification_state === "VERIFIED";
    $("#activateBtn").disabled = !canActivate; $("#pauseBtn").disabled = !(a.state === "ACTIVE" || a.state === "DRAINING");
    $("#verifyBtn").disabled = !(a.state === "PENDING_YELLOW_VERIFICATION" || a.state === "ERROR" || a.state === "VERIFIED");
    const tb = $("#posTable tbody"); tb.innerHTML = (rt.positions || []).map((p) => `<tr><td>${p.market}</td><td>${p.direction}</td><td class="mono">${p.qty}</td><td class="mono">${p.entry_price}</td><td>${p.state}</td><td>${p.strategy}</td></tr>`).join("") || `<tr><td colspan="6" class="dim">No open positions.</td></tr>`;
    $("#events").innerHTML = (me.events || []).map((e) => `<li><span class="mono">${(e.at || "").replace("T", " ").slice(0, 19)}</span><span>${e.kind.replace(/_/g, " ")}</span></li>`).join("") || "<li class='dim'>No activity yet.</li>";
    renderHealth();
  }
  function renderConnectExtras(a) { const err = $("#connectError"); const st = stateText(a); if (st.text) { err.textContent = st.text; err.classList.remove("hidden"); } }
  async function renderHealth() {
    try {
      const h = await api("GET", "/health");
      const keys = ["APP_HEALTHY", "SUPABASE_HEALTHY", "VAULT_HEALTHY", "YELLOW_CONNECTED", "YELLOW_VERIFIED", "EXECUTION_READY"];
      $("#health").innerHTML = keys.map((k) => `<span class="pill ${h[k] ? "pill-green" : "pill-muted"}">${k}: ${h[k] ? "yes" : "no"}</span>`).join("");
    } catch (e) { $("#health").textContent = "health unavailable"; }
  }

  // ---------------------------------------------------------------- actions
  async function act(btn, fn, okMsg) {
    busy(btn, true); hideError();
    try { state.me = await fn(); render(); if (okMsg) toast(okMsg); }
    catch (e) { showError(friendly(e)); }
    finally { busy(btn, false); }
  }
  $("#sizeCancel").addEventListener("click", (e) => act(e.currentTarget, () => api("POST", "/me/bot/size/cancel"), "Scheduled size change cancelled."));
  $("#activateBtn").addEventListener("click", (e) => confirmModal("Activate automated trading?", "WHY will open coin-minimum positions when your Yellow account has available margin. At most eight positions can be open. Derivatives trading involves substantial risk; losses are possible.", () => act(e.currentTarget, () => api("POST", "/me/bot/activate"), "Trading activated.")));
  $("#pauseBtn").addEventListener("click", (e) => act(e.currentTarget, () => api("POST", "/me/bot/pause"), "New entries paused."));
  $("#verifyBtn").addEventListener("click", (e) => act(e.currentTarget, () => api("POST", "/me/yellow/verify"), "Verification re-checked."));
  $("#disconnectBtn").addEventListener("click", (e) => {
    const n = state.me && state.me.runtime ? state.me.runtime.open_positions : 0;
    confirmModal("Disconnect Yellow?", n > 0 ? `You have ${n} open position(s). They will stay open in your Yellow account and will NO LONGER be managed by WHY (no target, no stop). The stored credential will be deleted.` : "The stored credential will be deleted. You can reconnect at any time.",
      () => act(e.currentTarget, () => api("POST", "/me/yellow/disconnect", { confirm_unmanaged: n > 0 }), "Yellow disconnected."));
  });

  function confirmModal(title, text, onOk) {
    $("#modalTitle").textContent = title; $("#modalText").textContent = text; $("#modal").classList.remove("hidden");
    const ok = $("#modalOk"), cancel = $("#modalCancel");
    const close = () => { $("#modal").classList.add("hidden"); ok.onclick = null; cancel.onclick = null; };
    ok.onclick = () => { close(); onOk(); }; cancel.onclick = close;
  }

  function startPoll(on) { stopPoll(); if (on) state.poll = setInterval(() => { if (document.visibilityState === "visible") loadMe(); }, 20000); }
  function stopPoll() { if (state.poll) clearInterval(state.poll); state.poll = null; }

  boot();
})();
