/* Harbor support UI: streaming chat with sources, agent steps, voice and settings. No build step. */
(() => {
  const $ = (id) => document.getElementById(id);
  const app = $('app'), thread = $('thread'), welcome = $('welcome'), form = $('form'), q = $('q'), send = $('send');
  const baseEl = $('base'), keyEl = $('key'), emailEl = $('email'), connDot = $('connDot'), connText = $('connText'), modeBadge = $('modeBadge');
  const sourcesList = $('sourcesList'), sourcesCount = $('sourcesCount');
  let conversationId = null, busy = false, lastCitations = [];

  // ---------- settings ----------
  const store = { get: (k, d = '') => { try { return localStorage.getItem(k) ?? d; } catch { return d; } }, set: (k, v) => { try { localStorage.setItem(k, v); } catch {} } };
  baseEl.value = store.get('harbor.base', window.HARBOR_BACKEND_URL || '');
  keyEl.value = store.get('harbor.key', 'dev-key');
  emailEl.value = store.get('harbor.email', '');
  const base = () => baseEl.value.trim().replace(/\/+$/, '');
  baseEl.addEventListener('change', () => { store.set('harbor.base', base()); checkConnection(); });
  keyEl.addEventListener('change', () => store.set('harbor.key', keyEl.value.trim()));
  emailEl.addEventListener('change', () => store.set('harbor.email', emailEl.value.trim()));
  if (location.hostname.endsWith('netlify.app') && !base()) { $('settings').open = true; toast('Hosted page: enter the URL of your Harbor API under Connection.'); }

  const themeKey = 'harbor.theme';
  const applyTheme = (t) => { if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme; };
  applyTheme(store.get(themeKey, ''));
  $('themeToggle').addEventListener('click', () => {
    const dark = document.documentElement.dataset.theme === 'dark' || (!document.documentElement.dataset.theme && matchMedia('(prefers-color-scheme: dark)').matches);
    const next = dark ? 'light' : 'dark'; applyTheme(next); store.set(themeKey, next);
  });

  // ---------- connection ----------
  async function checkConnection() {
    connDot.className = 'dot'; connText.textContent = 'Checking'; modeBadge.textContent = 'connecting'; modeBadge.className = 'badge badge--muted';
    try {
      const r = await fetch(base() + '/readyz', { cache: 'no-store' });
      const d = await r.json();
      const ok = r.ok && d.status === 'ready';
      connDot.className = 'dot ' + (ok ? 'ok' : 'bad'); connText.textContent = ok ? 'Connected' : 'Degraded';
      modeBadge.textContent = d.mode === 'offline' ? 'offline demo' : 'live'; modeBadge.className = 'badge ' + (ok ? '' : 'badge--warn');
    } catch {
      connDot.className = 'dot bad'; connText.textContent = 'Unreachable'; modeBadge.textContent = 'no backend'; modeBadge.className = 'badge badge--bad';
    }
  }
  checkConnection();

  // ---------- layout toggles ----------
  const openMenu = (v) => app.classList.toggle('menu-open', v);
  $('menu').addEventListener('click', () => openMenu(true));
  $('closeSidebar').addEventListener('click', () => openMenu(false));
  $('scrim').addEventListener('click', () => openMenu(false));
  const openSources = (v) => { app.classList.toggle('sources-open', v); $('sourcesToggle').setAttribute('aria-expanded', String(v)); };
  $('sourcesToggle').addEventListener('click', () => openSources(!app.classList.contains('sources-open')));
  $('closeSources').addEventListener('click', () => openSources(false));
  $('newChat').addEventListener('click', () => { conversationId = null; thread.querySelectorAll('.msg').forEach((m) => m.remove()); welcome.hidden = false; renderSources([]); openMenu(false); q.focus(); });
  document.addEventListener('keydown', (e) => { if (e.key === '/' && document.activeElement !== q && !e.metaKey && !e.ctrlKey) { e.preventDefault(); q.focus(); } });

  // ---------- composer ----------
  const autosize = () => { q.style.height = 'auto'; q.style.height = Math.min(q.scrollHeight, 180) + 'px'; send.disabled = !q.value.trim() || busy; };
  q.addEventListener('input', autosize);
  q.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); if (!send.disabled) form.requestSubmit(); } });
  form.addEventListener('submit', (e) => { e.preventDefault(); ask(q.value.trim()); });
  document.querySelectorAll('[data-q]').forEach((b) => b.addEventListener('click', () => {
    if (b.dataset.email && !emailEl.value.trim()) { emailEl.value = b.dataset.email; store.set('harbor.email', b.dataset.email); }
    openMenu(false); ask(b.dataset.q);
  }));

  // ---------- rendering ----------
  const esc = (s) => s.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  function render(text) {
    const blocks = text.trim().split(/\n{2,}/);
    return blocks.map((block) => {
      const lines = block.split('\n');
      if (lines.every((l) => /^\s*([-*•]|\d+[.)])\s+/.test(l))) {
        return '<ul>' + lines.map((l) => '<li>' + inline(l.replace(/^\s*([-*•]|\d+[.)])\s+/, '')) + '</li>').join('') + '</ul>';
      }
      return '<p>' + lines.map(inline).join('<br>') + '</p>';
    }).join('');
  }
  function inline(s) {
    return esc(s)
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/\[c(\d{1,2})\]/g, (_, n) => `<button class="cite" data-id="c${n}" title="Source ${n}">${n}</button>`);
  }
  function addUser(text, voice) {
    welcome.hidden = true;
    const el = document.createElement('article'); el.className = 'msg msg--user';
    el.innerHTML = `<div class="msg__text${voice ? ' voice' : ''}"></div>`; el.querySelector('.msg__text').textContent = text;
    thread.appendChild(el); scroll(); return el;
  }
  function addAssistant() {
    welcome.hidden = true;
    const el = $('tpl-assistant').content.firstElementChild.cloneNode(true);
    thread.appendChild(el); scroll(); return el;
  }
  const scroll = () => { thread.scrollTop = thread.scrollHeight; };

  const LABELS = { guard: 'Checking the request', router: 'Working out what you need', knowledge: 'Searching the knowledge base', verifier: 'Verifying the answer', order: 'Looking up your order', escalate: 'Creating a ticket', smalltalk: 'Replying', finalize: 'Finishing up' };
  function addStep(stepsEl, label, cls = '') {
    stepsEl.hidden = false;
    stepsEl.querySelectorAll('.step:not(.done):not(.warn)').forEach((s) => s.classList.add('done'));
    const li = document.createElement('li'); li.className = 'step ' + cls; li.innerHTML = `<span class="step__i"></span>${esc(label)}`;
    stepsEl.appendChild(li); scroll();
  }
  function finish(el, d, extra = {}) {
    const text = el.querySelector('.msg__text'), steps = el.querySelector('.steps'), meta = el.querySelector('.msg__meta'), cites = el.querySelector('.msg__cites');
    steps.hidden = true; text.classList.remove('typing'); text.innerHTML = render(d.answer || '');
    const verdict = d.verdict || {}, guard = d.guard || {};
    let state = { cls: 'badge--ok', label: 'Verified' };
    if (d.intent === 'blocked') state = { cls: 'badge--bad', label: `Blocked · ${(guard.category || 'injection').replace(/_/g, ' ')}` };
    else if (d.intent === 'order') state = { cls: 'badge--muted', label: 'Order lookup' };
    else if (d.intent === 'escalate') state = { cls: 'badge--muted', label: 'Ticket created' };
    else if (d.intent !== 'knowledge') state = { cls: 'badge--muted', label: d.intent };
    else if (verdict.skipped) state = { cls: 'badge--muted', label: 'Not verified' };
    else if (verdict.grounded === false) state = { cls: 'badge--warn', label: 'Unverified claims' };
    meta.hidden = false;
    meta.innerHTML = `<span class="badge ${state.cls}">${esc(state.label)}</span>` +
      (d.attempts > 1 ? `<span class="badge badge--warn">rewritten ×${d.attempts - 1}</span>` : '') +
      `<span class="trace">${(d.trace || []).map(esc).join(' <i>›</i> ')}</span>` +
      `<span>${d.latency_ms} ms</span>`;
    lastCitations = d.citations || [];
    if (lastCitations.length) {
      cites.hidden = false;
      cites.innerHTML = lastCitations.map((c) => `<button class="chip" data-id="${c.id}"><b>${c.id.slice(1)}</b><span>${esc(c.title)}${c.section ? ' › ' + esc(c.section) : ''}</span></button>`).join('');
    }
    renderSources(lastCitations);
    el.querySelector('.msg__tools').hidden = false;
    el.querySelector('.msg__details').textContent = JSON.stringify({ conversation_id: d.conversation_id, intent: d.intent, attempts: d.attempts, guard, verdict, ticket_id: d.ticket_id, ...extra }, null, 2);
    el.dataset.answer = d.answer || ''; el.dataset.done = '1';
    scroll();
  }
  function renderSources(cites) {
    sourcesCount.textContent = cites.length;
    if (!cites.length) { sourcesList.innerHTML = '<p class="sources__empty">Citations from the latest answer appear here. Click a number in an answer to jump to its source.</p>'; return; }
    sourcesList.innerHTML = cites.map((c) => `
      <div class="src" id="src-${c.id}">
        <div class="src__top"><span class="src__n">${c.id.slice(1)}</span><span class="src__title">${esc(c.title)}</span></div>
        ${c.section ? `<div class="src__section">${esc(c.section)}</div>` : ''}
        <div class="src__snippet">${esc(c.snippet)}${c.snippet && c.snippet.length >= 240 ? '…' : ''}</div>
        <div class="src__bar" title="Reranker relevance ${Math.round(c.score * 100)}%"><i style="width:${Math.max(4, Math.round(c.score * 100))}%"></i></div>
        <div class="src__path">${esc(c.source)}</div>
      </div>`).join('');
  }
  thread.addEventListener('click', (e) => {
    const cite = e.target.closest('.cite, .chip');
    if (cite) {
      const msg = cite.closest('.msg'); if (msg && msg.dataset.done) { /* make sure the panel shows this message's citations */ }
      openSources(true);
      const card = $('src-' + cite.dataset.id);
      if (card) { card.scrollIntoView({ block: 'nearest', behavior: 'smooth' }); card.classList.add('hot'); setTimeout(() => card.classList.remove('hot'), 1400); }
      return;
    }
    const tool = e.target.closest('.tool');
    if (tool) {
      const msg = tool.closest('.msg');
      if (tool.dataset.act === 'copy') { navigator.clipboard?.writeText(msg.dataset.answer || '').then(() => toast('Answer copied')); }
      else if (tool.dataset.act === 'details') { const d = msg.querySelector('.msg__details'); d.hidden = !d.hidden; }
      else { msg.querySelectorAll('.tool[data-act="up"],.tool[data-act="down"]').forEach((t) => t.classList.toggle('active', t === tool && !t.classList.contains('active'))); toast('Thanks for the feedback'); }
    }
  });

  // ---------- chat turn (SSE) ----------
  async function ask(text) {
    if (!text || busy) return;
    busy = true; q.value = ''; autosize();
    addUser(text);
    const el = addAssistant(); const steps = el.querySelector('.steps'), out = el.querySelector('.msg__text');
    out.classList.add('typing');
    let buf = '', shown = '';
    try {
      const res = await fetch(base() + '/v1/chat/stream', {
        method: 'POST', headers: { 'content-type': 'application/json', 'x-api-key': keyEl.value.trim() },
        body: JSON.stringify({ message: text, conversation_id: conversationId, customer_email: emailEl.value.trim() || null }),
      });
      if (!res.ok) throw new Error(await errorText(res));
      const reader = res.body.getReader(), dec = new TextDecoder();
      const handle = (frame) => {
        let ev = 'message', data = '';
        frame.split('\n').forEach((l) => { if (l.startsWith('event:')) ev = l.slice(6).trim(); else if (l.startsWith('data:')) data += l.slice(5).trim(); });
        if (!data) return;
        const d = JSON.parse(data);
        if (ev === 'conversation') conversationId = d.conversation_id;
        else if (ev === 'status') addStep(steps, LABELS[d.node] || d.label);
        else if (ev === 'token') { shown += d.text; out.innerHTML = render(shown); out.classList.add('typing'); scroll(); }
        else if (ev === 'rewrite') { shown = ''; out.innerHTML = ''; addStep(steps, 'Verifier rejected the first draft, rewriting', 'warn'); }
        else if (ev === 'done') finish(el, d);
      };
      while (true) {
        const { value, done } = await reader.read(); if (done) break;
        buf += dec.decode(value, { stream: true }).replace(/\r\n/g, '\n');  // SSE servers may use CRLF
        let idx;
        while ((idx = buf.indexOf('\n\n')) >= 0) { const frame = buf.slice(0, idx); buf = buf.slice(idx + 2); handle(frame); }
      }
      if (buf.trim()) handle(buf);  // a final frame without a trailing blank line
      if (!el.dataset.done) throw new Error('The stream ended before the answer was complete.');
    } catch (err) {
      steps.hidden = true; out.classList.remove('typing');
      out.innerHTML = `<p>Something went wrong: ${esc(err.message)}</p>`;
      toast(err.message, true);
    } finally { busy = false; autosize(); q.focus(); }
  }
  async function errorText(res) {
    if (res.status === 401) return 'Invalid or missing API key (open Connection in the sidebar).';
    if (res.status === 429) return 'Rate limit reached, try again in a moment.';
    try { const j = await res.json(); return j.detail ? (typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail)) : `HTTP ${res.status}`; } catch { return `HTTP ${res.status}`; }
  }

  // ---------- voice: hold to talk ----------
  const mic = $('mic'), micTimer = $('micTimer');
  let rec = null, chunks = [], timer = null, started = 0;
  async function startRec() {
    if (busy || rec) return;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      rec = new MediaRecorder(stream); chunks = [];
      rec.ondataavailable = (e) => chunks.push(e.data);
      rec.onstop = () => { stream.getTracks().forEach((t) => t.stop()); sendVoice(); };
      rec.start(); started = Date.now(); mic.classList.add('rec');
      timer = setInterval(() => { const s = Math.floor((Date.now() - started) / 1000); micTimer.textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; }, 250);
    } catch (e) { toast('Microphone not available: ' + e.message, true); }
  }
  function stopRec() { if (rec && rec.state === 'recording') rec.stop(); mic.classList.remove('rec'); clearInterval(timer); micTimer.textContent = '0:00'; }
  async function sendVoice() {
    const blob = new Blob(chunks, { type: rec.mimeType || 'audio/webm' }); rec = null;
    if (blob.size < 2000) { toast('Recording too short, hold the button while you speak.'); return; }
    busy = true; autosize();
    const el = addAssistant(); const steps = el.querySelector('.steps'); addStep(steps, 'Transcribing');
    try {
      const fd = new FormData(); fd.append('audio', blob, 'clip.webm');
      if (conversationId) fd.append('conversation_id', conversationId);
      if (emailEl.value.trim()) fd.append('customer_email', emailEl.value.trim());
      const res = await fetch(base() + '/v1/voice/chat', { method: 'POST', headers: { 'x-api-key': keyEl.value.trim() }, body: fd });
      if (!res.ok) throw new Error(await errorText(res));
      const d = await res.json(); conversationId = d.conversation_id;
      el.before(addUser(d.transcript, true)); el.remove();
      const reply = addAssistant(); finish(reply, d, { transcript: d.transcript });
      const audio = document.createElement('audio'); audio.controls = true; audio.src = `data:${d.audio_mime};base64,${d.audio_base64}`; reply.querySelector('.msg__body').appendChild(audio); audio.play().catch(() => {});
    } catch (err) { el.querySelector('.steps').hidden = true; el.querySelector('.msg__text').innerHTML = `<p>Voice failed: ${esc(err.message)}</p>`; toast(err.message, true); }
    finally { busy = false; autosize(); }
  }
  mic.addEventListener('mousedown', startRec); mic.addEventListener('mouseup', stopRec); mic.addEventListener('mouseleave', stopRec);
  mic.addEventListener('touchstart', (e) => { e.preventDefault(); startRec(); }, { passive: false }); mic.addEventListener('touchend', (e) => { e.preventDefault(); stopRec(); });

  // ---------- toasts ----------
  function toast(msg, bad = false) {
    const t = document.createElement('div'); t.className = 'toast' + (bad ? ' toast--bad' : ''); t.textContent = msg;
    $('toasts').appendChild(t); setTimeout(() => t.remove(), bad ? 6000 : 2800);
  }
  autosize();
})();
