const STORAGE_KEY = 'grok2api_user_api_key';

let currentTab = 'chat';
let models = [];
let chatMessages = [];
let chatAttachments = []; // { file, previewUrl }
let videoAttachments = [];
let imageGenerationMethod = 'legacy';
let imageGenerationExperimental = false;
let imageContinuousSockets = [];
let imageContinuousRunning = false;
let imageContinuousCount = 0;
let imageContinuousLatencyTotal = 0;
let imageContinuousLatencyCount = 0;
let imageContinuousActive = 0;
let imageContinuousLastError = '';
let imageContinuousRunToken = 0;
let imageContinuousDesiredConcurrency = 1;

function q(id) {
  return document.getElementById(id);
}

function isAdminChat() {
  return Boolean(window.__CHAT_ADMIN__);
}

function getUserApiKey() {
  return String(q('api-key-input').value || '').trim();
}

function buildApiHeaders() {
  const k = getUserApiKey();
  return k ? { Authorization: `Bearer ${k}` } : {};
}

function escapeHtml(s) {
  return String(s)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

function toAbsoluteUrl(url) {
  const u = String(url || '').trim();
  if (!u) return '';
  if (u.startsWith('http://') || u.startsWith('https://') || u.startsWith('data:')) return u;
  try {
    return new URL(u, window.location.href).toString();
  } catch (e) {
    return u;
  }
}

function detectBase64ImageMime(base64Text) {
  const s = String(base64Text || '').trim().replace(/\s+/g, '');
  if (!s) return 'image/png';
  if (s.startsWith('/9j/')) return 'image/jpeg';
  if (s.startsWith('iVBORw0KGgo')) return 'image/png';
  if (s.startsWith('UklGR')) return 'image/webp';
  if (s.startsWith('R0lGOD')) return 'image/gif';
  if (s.startsWith('Qk')) return 'image/bmp';
  return 'image/png';
}

function toImageDataUrl(raw) {
  const value = String(raw || '').trim();
  if (!value) return '';
  const lowered = value.toLowerCase();
  if (['error', 'null', 'none', 'undefined'].includes(lowered)) return '';
  if (value.startsWith('data:image/')) return value;
  const mime = detectBase64ImageMime(value);
  return `data:${mime};base64,${value}`;
}

function pickImageSrc(item) {
  const rawUrl = String(item?.url || '').trim();
  const rawUrlLower = rawUrl.toLowerCase();
  if (
    rawUrl &&
    rawUrl !== 'https://assets.grok.com/' &&
    rawUrl !== 'https://assets.grok.com' &&
    rawUrlLower !== 'error' &&
    rawUrlLower !== 'null' &&
    rawUrlLower !== 'undefined'
  ) {
    return toAbsoluteUrl(rawUrl);
  }
  const b64json = String(item?.b64_json || '').trim();
  if (b64json) return toImageDataUrl(b64json);
  const base64 = String(item?.base64 || '').trim();
  if (base64) return toImageDataUrl(base64);
  return '';
}

function showUserMsg(role, content) {
  const wrap = document.createElement('div');
  wrap.className = 'msg';
  wrap.innerHTML = `
    <div class="msg-role">${escapeHtml(role)}</div>
    <div class="msg-bubble"></div>
  `;
  const bubble = wrap.querySelector('.msg-bubble');
  renderContent(bubble, content, role !== 'assistant');
  q('chat-messages').appendChild(wrap);
  q('chat-messages').scrollTop = q('chat-messages').scrollHeight;
  return bubble;
}

function mdImagesToHtml(text) {
  return String(text).replace(/!\[[^\]]*]\(([^)]+)\)/g, (m, url) => {
    const safe = escapeHtml(String(url || '').trim());
    return safe ? `<img src="${safe}" alt="image" />` : '';
  });
}

function sanitizeHtml(html) {
  const allowedTags = new Set(['A', 'IMG', 'VIDEO', 'SOURCE', 'BR', 'P', 'PRE', 'CODE', 'DIV', 'SPAN']);
  const allowedAttrs = {
    A: new Set(['href', 'target', 'rel']),
    IMG: new Set(['src', 'alt']),
    VIDEO: new Set(['src', 'controls', 'preload', 'poster']),
    SOURCE: new Set(['src', 'type']),
    P: new Set([]),
    PRE: new Set([]),
    CODE: new Set([]),
    DIV: new Set([]),
    SPAN: new Set([]),
  };

  const doc = new DOMParser().parseFromString(`<div>${html}</div>`, 'text/html');
  const root = doc.body.firstElementChild;
  if (!root) return '';

  function cleanNode(node) {
    if (node.nodeType === Node.TEXT_NODE) return document.createTextNode(node.textContent || '');
    if (node.nodeType !== Node.ELEMENT_NODE) return document.createTextNode('');

    const el = node;
    const tag = el.tagName.toUpperCase();
    if (!allowedTags.has(tag)) {
      const frag = document.createDocumentFragment();
      Array.from(el.childNodes).forEach((c) => frag.appendChild(cleanNode(c)));
      return frag;
    }

    const out = document.createElement(tag.toLowerCase());
    const okAttrs = allowedAttrs[tag] || new Set();
    Array.from(el.attributes || []).forEach((attr) => {
      const name = attr.name.toLowerCase();
      if (!okAttrs.has(name)) return;
      const val = String(attr.value || '');
      if (tag === 'A' && name === 'href') {
        if (!(val.startsWith('http://') || val.startsWith('https://') || val.startsWith('/'))) return;
      }
      if ((tag === 'IMG' || tag === 'VIDEO' || tag === 'SOURCE') && name === 'src') {
        if (!(val.startsWith('http://') || val.startsWith('https://') || val.startsWith('/') || val.startsWith('data:')))
          return;
      }
      out.setAttribute(name, val);
    });

    Array.from(el.childNodes).forEach((c) => out.appendChild(cleanNode(c)));
    return out;
  }

  const cleaned = cleanNode(root);
  const container = document.createElement('div');
  container.appendChild(cleaned);
  return container.innerHTML;
}

function renderContent(container, content, forceText) {
  container.innerHTML = '';
  const text = String(content || '');

  if (forceText) {
    const pre = document.createElement('pre');
    pre.textContent = text;
    container.appendChild(pre);
    return;
  }

  const html = sanitizeHtml(mdImagesToHtml(text).replace(/\n/g, '<br/>'));
  if (!html.trim()) {
    const pre = document.createElement('pre');
    pre.textContent = text;
    container.appendChild(pre);
    return;
  }
  container.innerHTML = html;
}

async function init() {
  if (isAdminChat()) {
    const adminSession = await ensureApiKey();
    if (adminSession === null) return;
    try {
      const res = await fetch('/api/v1/admin/config', { headers: buildAuthHeaders(adminSession) });
      if (res.status === 401) return logout();
      if (res.ok) {
        const cfg = await res.json();
        const k = String(cfg?.app?.api_key || '').trim();
        if (k) {
          q('api-key-input').value = k;
          localStorage.setItem(STORAGE_KEY, k);
        }
      }
    } catch (e) {}
  }

  const saved = localStorage.getItem(STORAGE_KEY) || '';
  if (!q('api-key-input').value) q('api-key-input').value = saved;

  bindFileInputs();
  q('image-run-mode')?.addEventListener('change', () => {
    if (getImageRunMode() !== 'continuous') {
      stopImageContinuous();
    }
    updateImageModeUI();
  });
  window.addEventListener('beforeunload', () => {
    stopImageContinuous();
  });
  await refreshModels();
  await refreshImageGenerationMethod();

  chatMessages = [];
  q('chat-messages').innerHTML = '';
  showUserMsg('system', '提示：选择模型后即可开始聊天；生图/视频请切换到对应 Tab。');
}

function bindFileInputs() {
  q('chat-file').addEventListener('change', () => {
    const files = Array.from(q('chat-file').files || []);
    if (!files.length) return;
    addAttachments('chat', files);
    q('chat-file').value = '';
  });

  q('video-file').addEventListener('change', () => {
    const files = Array.from(q('video-file').files || []);
    if (!files.length) return;
    addAttachments('video', files);
    q('video-file').value = '';
  });
}

function addAttachments(kind, files) {
  const list = kind === 'video' ? videoAttachments : chatAttachments;
  files.forEach((f) => {
    if (!String(f.type || '').toLowerCase().startsWith('image/')) return;
    const url = URL.createObjectURL(f);
    list.push({ file: f, previewUrl: url });
  });
  renderAttachments(kind);
}

function renderAttachments(kind) {
  const list = kind === 'video' ? videoAttachments : chatAttachments;
  const info = kind === 'video' ? q('video-attach-info') : q('chat-attach-info');
  const box = kind === 'video' ? q('video-attach-preview') : q('chat-attach-preview');
  info.textContent = list.length ? `已选择 ${list.length} 张图片` : '';
  box.innerHTML = '';
  if (!list.length) {
    box.classList.add('hidden');
    return;
  }
  box.classList.remove('hidden');
  list.forEach((it, idx) => {
    const div = document.createElement('div');
    div.className = 'attach-item';
    div.innerHTML = `<img src="${it.previewUrl}" alt="img"><button title="绉婚櫎">脳</button>`;
    div.querySelector('button').addEventListener('click', () => {
      try { URL.revokeObjectURL(it.previewUrl); } catch (e) {}
      list.splice(idx, 1);
      renderAttachments(kind);
    });
    box.appendChild(div);
  });
}

function getImageRunMode() {
  const value = String(q('image-run-mode')?.value || 'single').trim().toLowerCase();
  return value === 'continuous' ? 'continuous' : 'single';
}

function getImageContinuousConcurrency() {
  return Math.max(1, Math.min(3, Math.floor(Number(q('image-concurrency')?.value || 1) || 1)));
}

function getImageContinuousActiveCount() {
  return imageContinuousSockets.filter((it) => it && it.active && !it.closed).length;
}

function getImageContinuousOpenCount() {
  return imageContinuousSockets.filter((it) => {
    const ws = it?.ws;
    if (!ws || it?.closed) return false;
    return ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING;
  }).length;
}

function setImageStatusText(text) {
  const el = q('image-status-text');
  if (el) el.textContent = String(text || '-');
}

function updateImageContinuousStats() {
  imageContinuousActive = getImageContinuousActiveCount();
  const countEl = q('image-count-value');
  const activeEl = q('image-active-value');
  const latencyEl = q('image-latency-value');
  const errorEl = q('image-error-value');
  if (countEl) countEl.textContent = String(imageContinuousCount);
  if (activeEl) activeEl.textContent = String(imageContinuousActive);
  if (latencyEl) {
    if (imageContinuousLatencyCount > 0) {
      latencyEl.textContent = `${Math.round(imageContinuousLatencyTotal / imageContinuousLatencyCount)}ms`;
    } else {
      latencyEl.textContent = '-';
    }
  }
  if (errorEl) errorEl.textContent = imageContinuousLastError || '-';
}

function updateImageContinuousButtons() {
  const isContinuous = imageGenerationExperimental && getImageRunMode() === 'continuous';
  const startBtn = q('image-start-btn');
  const stopBtn = q('image-stop-btn');
  if (startBtn) startBtn.disabled = !isContinuous || imageContinuousRunning;
  if (stopBtn) stopBtn.disabled = !isContinuous || !imageContinuousRunning;
}

function updateImageRunModeUI() {
  const isContinuous = imageGenerationExperimental && getImageRunMode() === 'continuous';
  const nWrap = q('image-n-wrap');
  const generateWrap = q('image-generate-wrap');
  const resultBox = q('image-results');
  const continuousWrap = q('image-continuous-wrap');
  const emptyState = q('image-empty-state');
  const waterfall = q('image-waterfall');

  if (nWrap) nWrap.classList.toggle('hidden', isContinuous);
  if (generateWrap) generateWrap.classList.toggle('hidden', isContinuous);
  if (resultBox) resultBox.classList.toggle('hidden', isContinuous);
  if (continuousWrap) continuousWrap.classList.toggle('hidden', !isContinuous);
  if (resultBox) resultBox.classList.remove('waterfall-layout');

  if (emptyState && waterfall) {
    emptyState.classList.toggle('hidden', waterfall.children.length > 0);
  }

  if (isContinuous && imageContinuousRunning) {
    setImageStatusText(imageContinuousActive > 0 ? 'Running' : 'Connecting');
  } else if (isContinuous) {
    setImageStatusText('Idle');
  }

  updateImageContinuousButtons();
  updateImageContinuousStats();
}

function updateImageModeUI() {
  const isExperimental = imageGenerationExperimental;
  const hint = q('image-mode-hint');
  const aspectWrap = q('image-aspect-wrap');
  const concurrencyWrap = q('image-concurrency-wrap');
  const runModeWrap = q('image-run-mode-wrap');
  const runMode = q('image-run-mode');

  if (!isExperimental && imageContinuousRunning) {
    stopImageContinuous();
  }

  if (hint) hint.classList.toggle('hidden', !isExperimental);
  if (aspectWrap) aspectWrap.classList.toggle('hidden', !isExperimental);
  if (concurrencyWrap) concurrencyWrap.classList.toggle('hidden', !isExperimental);
  if (runModeWrap) runModeWrap.classList.toggle('hidden', !isExperimental);
  if (runMode && !isExperimental) runMode.value = 'single';

  updateImageRunModeUI();
}

function clearImageContinuousError() {
  imageContinuousLastError = '';
  updateImageContinuousStats();
}

function setImageContinuousError(message) {
  imageContinuousLastError = String(message || '').trim() || 'unknown';
  updateImageContinuousStats();
}

function resetImageContinuousMetrics(resetCount) {
  if (resetCount) imageContinuousCount = 0;
  imageContinuousLatencyTotal = 0;
  imageContinuousLatencyCount = 0;
  imageContinuousActive = 0;
  clearImageContinuousError();
  updateImageContinuousStats();
}

function appendWaterfallImage(item, connectionIndex) {
  const src = pickImageSrc(item);
  if (!src) return;

  const waterfall = q('image-waterfall');
  if (!waterfall) return;

  const seq = Number(item?.sequence) || waterfall.children.length + 1;
  const elapsed = Math.max(0, Number(item?.elapsed_ms) || 0);
  const ratio = String(item?.aspect_ratio || '').trim();

  const card = document.createElement('div');
  card.className = 'waterfall-item';
  card.innerHTML = `
    <img alt="image" src="${src}" />
    <div class="waterfall-meta">
      <span>#${seq} 路 WS${connectionIndex + 1}</span>
      <span>${ratio || '-'} 路 ${elapsed > 0 ? `${elapsed}ms` : '-'}</span>
    </div>
  `;
  waterfall.prepend(card);

  imageContinuousCount += 1;
  if (elapsed > 0) {
    imageContinuousLatencyTotal += elapsed;
    imageContinuousLatencyCount += 1;
  }

  const emptyState = q('image-empty-state');
  if (emptyState) emptyState.classList.add('hidden');
  updateImageContinuousStats();
}

function clearImageWaterfall() {
  const waterfall = q('image-waterfall');
  const emptyState = q('image-empty-state');
  if (waterfall) waterfall.innerHTML = '';
  if (emptyState) emptyState.classList.remove('hidden');
  resetImageContinuousMetrics(true);
}

function buildImagineWsUrl() {
  const key = getUserApiKey();
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const url = new URL('/api/v1/admin/imagine/ws', `${proto}//${window.location.host}`);
  if (key) url.searchParams.set('api_key', key);
  return url.toString();
}

function parseWsMessage(raw) {
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch (e) {
    return null;
  }
}

function openImageContinuousSocket(socketIndex, runToken, prompt, aspectRatio, attempt = 0) {
  const wsUrl = buildImagineWsUrl();
  const ws = new WebSocket(wsUrl);
  const socketState = {
    index: socketIndex,
    ws,
    runToken,
    attempt,
    active: false,
    closed: false,
    hadError: false,
    lastError: '',
    runId: '',
  };
  imageContinuousSockets.push(socketState);
  updateImageContinuousStats();

  ws.onopen = () => {
    if (!imageContinuousRunning || runToken !== imageContinuousRunToken || getImageRunMode() !== 'continuous') {
      try { ws.close(1000, 'stale'); } catch (e) {}
      return;
    }
    clearImageContinuousError();
    ws.send(JSON.stringify({ type: 'start', prompt, aspect_ratio: aspectRatio }));
  };

  ws.onmessage = (event) => {
    const data = parseWsMessage(event?.data);
    if (!data || runToken !== imageContinuousRunToken) return;
    const msgType = String(data?.type || '').trim();

    if (msgType === 'status') {
      const status = String(data?.status || '').trim().toLowerCase();
      socketState.runId = String(data?.run_id || socketState.runId || '');
      socketState.active = status === 'running';
      updateImageContinuousStats();
      if (imageContinuousRunning) {
        if (status === 'running') {
          clearImageContinuousError();
          setImageStatusText('Running');
        }
        if (status === 'stopped') setImageStatusText('Stopped');
      }
      updateImageContinuousButtons();
      return;
    }

    if (msgType === 'image') {
      socketState.active = true;
      clearImageContinuousError();
      appendWaterfallImage(data, socketIndex);
      if (imageContinuousRunning) setImageStatusText('Running');
      updateImageContinuousButtons();
      return;
    }

    if (msgType === 'error') {
      const message = String(data?.message || 'unknown error').trim() || 'unknown error';
      setImageContinuousError(message);
      if (imageContinuousRunning) setImageStatusText('Running (with errors)');
      return;
    }

    if (msgType === 'pong') {
      if (imageContinuousRunning && imageContinuousActive <= 0) setImageStatusText('Connected');
    }
  };

  ws.onerror = () => {
    if (runToken !== imageContinuousRunToken) return;
    socketState.hadError = true;
    socketState.lastError = `WS${socketIndex + 1} connection error`;
  };

  ws.onclose = (event) => {
    socketState.closed = true;
    socketState.active = false;
    updateImageContinuousStats();

    if (runToken !== imageContinuousRunToken) return;
    if (imageContinuousRunning) {
      const stillActive = getImageContinuousActiveCount();
      const stillOpen = getImageContinuousOpenCount();

      if (event?.code === 1008 && stillActive <= 0 && stillOpen <= 0) {
        setImageContinuousError('WebSocket auth rejected. Check API key.');
        setImageStatusText('Auth failed');
      } else if (socketState.hadError && stillActive <= 0 && stillOpen <= 0) {
        const closeCode = Number(event?.code || 0);
        const closeReason = String(event?.reason || '').trim();
        if (closeCode > 0) {
          const suffix = closeReason ? `: ${closeReason}` : '';
          setImageContinuousError(`WebSocket closed (${closeCode})${suffix}`);
        } else {
          setImageContinuousError(socketState.lastError || `WS${socketIndex + 1} connection error`);
        }
        setImageStatusText('Disconnected');
      }

      if (
        event?.code !== 1000 &&
        event?.code !== 1008 &&
        socketState.attempt < 1 &&
        getImageRunMode() === 'continuous' &&
        imageGenerationExperimental
      ) {
        setTimeout(() => {
          if (!imageContinuousRunning || runToken !== imageContinuousRunToken) return;
          if (getImageContinuousOpenCount() >= imageContinuousDesiredConcurrency) return;
          openImageContinuousSocket(socketIndex, runToken, prompt, aspectRatio, socketState.attempt + 1);
        }, 1200);
      }

      if (stillActive <= 0 && stillOpen <= 0 && event?.code === 1000) {
        setImageStatusText('Stopped');
      }
      updateImageContinuousButtons();
    }
  };
}

function stopImageContinuous() {
  imageContinuousRunToken += 1;
  imageContinuousRunning = false;
  imageContinuousActive = 0;

  imageContinuousSockets.forEach((state) => {
    const ws = state?.ws;
    if (!ws) return;
    try {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'stop' }));
      }
    } catch (e) {}
    try {
      if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
        ws.close(1000, 'client stop');
      }
    } catch (e) {}
    state.closed = true;
    state.active = false;
  });
  imageContinuousSockets = [];

  if (imageGenerationExperimental && getImageRunMode() === 'continuous') {
    setImageStatusText('Stopped');
  } else {
    setImageStatusText('Idle');
  }
  updateImageContinuousButtons();
  updateImageContinuousStats();
}

function startImageContinuous() {
  if (!imageGenerationExperimental || getImageRunMode() !== 'continuous') {
    return;
  }
  const prompt = String(q('image-prompt')?.value || '').trim();
  if (!prompt) {
    showToast('Please input prompt', 'warning');
    return;
  }
  if (!getUserApiKey()) {
    showToast('Please input API key first', 'warning');
    return;
  }

  const aspectRatio = String(q('image-aspect')?.value || '2:3').trim() || '2:3';
  const concurrency = getImageContinuousConcurrency();
  const token = imageContinuousRunToken + 1;
  imageContinuousDesiredConcurrency = concurrency;

  stopImageContinuous();
  imageContinuousRunToken = token;
  imageContinuousRunning = true;
  clearImageContinuousError();
  imageContinuousActive = 0;
  if (!q('image-waterfall')?.children?.length) resetImageContinuousMetrics(true);

  setImageStatusText('Connecting');
  updateImageContinuousButtons();
  updateImageContinuousStats();

  for (let i = 0; i < concurrency; i += 1) {
    openImageContinuousSocket(i, token, prompt, aspectRatio);
  }
}

function isExperimentalImageMethod(method) {
  const value = String(method || '').trim().toLowerCase();
  return (
    value === 'imagine_ws_experimental' ||
    value === 'imagine_ws' ||
    value === 'experimental' ||
    value === 'new' ||
    value === 'new_method'
  );
}

async function refreshImageGenerationMethod() {
  const headers = buildApiHeaders();
  imageGenerationMethod = 'legacy';
  imageGenerationExperimental = false;

  if (!headers.Authorization) {
    stopImageContinuous();
    updateImageModeUI();
    return;
  }

  try {
    const res = await fetch(`/v1/images/method?t=${Date.now()}`, {
      headers,
      cache: 'no-store',
    });
    if (res.ok) {
      const data = await res.json().catch(() => ({}));
      const method = String(data?.image_generation_method || data?.method || '').trim().toLowerCase();
      imageGenerationMethod = method || 'legacy';
      imageGenerationExperimental = isExperimentalImageMethod(imageGenerationMethod);
    }
  } catch (e) {}

  if (!imageGenerationExperimental) {
    stopImageContinuous();
  }

  updateImageModeUI();
}

async function refreshModels() {
  const sel = q('model-select');
  sel.innerHTML = '';

  const headers = buildApiHeaders();
  if (!headers.Authorization) {
    showToast('请先填写 API Key', 'warning');
    return;
  }

  try {
    const res = await fetch('/v1/models', { headers });
    if (res.status === 401) {
      showToast('API Key 无效或未授权', 'error');
      return;
    }
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    models = Array.isArray(data?.data) ? data.data : [];

    const filtered = models.filter((m) => {
      const id = String(m.id || '');
      if (currentTab === 'image') return id === 'grok-imagine-1.0';
      if (currentTab === 'video') return id === 'grok-imagine-1.0-video';
      return !/imagine/i.test(id) || id === 'grok-4-heavy';
    });

    filtered.forEach((m) => {
      const opt = document.createElement('option');
      const id = String(m.id || '');
      const label = String(m.display_name || id);
      opt.value = id;
      opt.textContent = `${label} (${id})`;
      sel.appendChild(opt);
    });

    if (currentTab === 'image') sel.value = 'grok-imagine-1.0';
    else if (currentTab === 'video') sel.value = 'grok-imagine-1.0-video';
    else sel.value = sel.value || 'grok-4-fast';
  } catch (e) {
    showToast('加载模型失败: ' + (e?.message || e), 'error');
  }
}

function saveApiKey() {
  const k = getUserApiKey();
  if (!k) return showToast('请输入 API Key', 'warning');
  stopImageContinuous();
  localStorage.setItem(STORAGE_KEY, k);
  showToast('已保存', 'success');
  refreshModels();
  refreshImageGenerationMethod();
}

function clearApiKey() {
  stopImageContinuous();
  localStorage.removeItem(STORAGE_KEY);
  q('api-key-input').value = '';
  imageGenerationMethod = 'legacy';
  imageGenerationExperimental = false;
  updateImageModeUI();
  showToast('已清除', 'success');
}

function switchTab(tab) {
  if (currentTab === 'image' && tab !== 'image') {
    stopImageContinuous();
  }
  currentTab = tab;
  ['chat', 'image', 'video'].forEach((t) => {
    q(`tab-${t}`).classList.toggle('active', t === tab);
    q(`panel-${t}`).classList.toggle('hidden', t !== tab);
  });
  refreshModels();
  if (tab === 'image') refreshImageGenerationMethod();
}

function pickChatImage() {
  q('chat-file').click();
}

function pickVideoImage() {
  q('video-file').click();
}

async function uploadImages(files) {
  const headers = buildApiHeaders();
  if (!headers.Authorization) throw new Error('Missing API Key');

  const uploaded = [];
  for (const f of files) {
    const fd = new FormData();
    fd.append('file', f);
    const res = await fetch('/v1/uploads/image', { method: 'POST', headers, body: fd });
    if (res.status === 401) throw new Error('Unauthorized');
    const data = await res.json();
    if (!res.ok) throw new Error(data?.error?.message || `Upload failed (${res.status})`);
    uploaded.push(toAbsoluteUrl(String(data.url || '')));
  }
  return uploaded.filter(Boolean);
}

async function sendChat() {
  const prompt = String(q('chat-input').value || '').trim();
  if (!prompt && !chatAttachments.length) return showToast('请输入内容或上传图片', 'warning');

  const model = String(q('model-select').value || '').trim();
  const stream = Boolean(q('stream-toggle').checked);

  const headers = { ...buildApiHeaders(), 'Content-Type': 'application/json' };
  if (!headers.Authorization) return showToast('请先填写 API Key', 'warning');

  try {
    let imgUrls = [];
    if (chatAttachments.length) {
      showToast('上传图片中...', 'info');
      imgUrls = await uploadImages(chatAttachments.map((x) => x.file));
    }

    const userContent = imgUrls.length
      ? [{ type: 'text', text: prompt || ' ' }, ...imgUrls.map((u) => ({ type: 'image_url', image_url: { url: u } }))]
      : prompt;

    chatMessages.push({ role: 'user', content: userContent });

    showUserMsg('user', prompt || '[图片]');
    q('chat-input').value = '';
    chatAttachments.forEach((a) => {
      try { URL.revokeObjectURL(a.previewUrl); } catch (e) {}
    });
    chatAttachments = [];
    renderAttachments('chat');

    const body = { model, messages: chatMessages, stream };

    if (stream) {
      const assistantBubble = showUserMsg('assistant', '');
      await streamChat(body, assistantBubble);
    } else {
      const res = await fetch('/v1/chat/completions', { method: 'POST', headers, body: JSON.stringify(body) });
      if (res.status === 401) return showToast('API Key 无效或未授权', 'error');
      const data = await res.json();
      const content = data?.choices?.[0]?.message?.content || '';
      chatMessages.push({ role: 'assistant', content });
      showUserMsg('assistant', content);
    }
  } catch (e) {
    showToast('发送失败: ' + (e?.message || e), 'error');
  }
}

async function streamChat(body, bubbleEl) {
  const headers = { ...buildApiHeaders(), 'Content-Type': 'application/json' };
  const res = await fetch('/v1/chat/completions', { method: 'POST', headers, body: JSON.stringify(body) });
  if (!res.ok || !res.body) {
    const t = await res.text().catch(() => '');
    throw new Error(`HTTP ${res.status}: ${t.slice(0, 200)}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  let acc = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const parts = buf.split('\n\n');
    buf = parts.pop() || '';
    for (const part of parts) {
      const line = part.trim();
      if (!line.startsWith('data:')) continue;
      const payload = line.slice(5).trim();
      if (payload === '[DONE]') {
        chatMessages.push({ role: 'assistant', content: acc });
        return;
      }
      try {
        const obj = JSON.parse(payload);
        const delta = obj?.choices?.[0]?.delta?.content;
        if (typeof delta === 'string' && delta) {
          acc += delta;
          renderContent(bubbleEl, acc, false);
          q('chat-messages').scrollTop = q('chat-messages').scrollHeight;
        }
      } catch (e) {}
    }
  }

  chatMessages.push({ role: 'assistant', content: acc });
}

function createImageCard(index) {
  const card = document.createElement('div');
  card.className = 'result-card';
  card.dataset.index = String(index);
  card.innerHTML = `
    <div class="result-placeholder">等待生成...</div>
    <div class="result-progress"><div class="result-progress-bar"></div></div>
    <div class="result-meta"><span>#${index + 1}</span><span class="result-status">0%</span></div>
  `;
  return card;
}

function ensureImageCard(cardMap, index) {
  const key = Number(index) || 0;
  if (cardMap.has(key)) return cardMap.get(key);
  const card = createImageCard(key);
  q('image-results').appendChild(card);
  cardMap.set(key, card);
  return card;
}

function updateImageCardProgress(card, progress) {
  const pct = Math.max(0, Math.min(100, Number(progress) || 0));
  const bar = card.querySelector('.result-progress-bar');
  const status = card.querySelector('.result-status');
  if (bar) bar.style.width = `${pct}%`;
  if (status) status.textContent = `${pct}%`;
}

function updateImageCardCompleted(card, src, failed) {
  const placeholder = card.querySelector('.result-placeholder');
  const progress = card.querySelector('.result-progress');
  const status = card.querySelector('.result-status');

  if (progress) progress.remove();

  if (failed) {
    card.classList.add('is-error');
    if (placeholder) placeholder.textContent = '生成失败';
    if (status) status.textContent = '失败';
    return;
  }

  card.classList.remove('is-error');
  if (placeholder) placeholder.remove();

  const img = document.createElement('img');
  img.alt = 'image';
  img.src = src;
  card.insertBefore(img, card.firstChild);

  if (status) status.textContent = '完成';
}

function buildImageRequestConfig() {
  const ratio = String(q('image-aspect')?.value || '2:3');
  const concurrency = Math.max(1, Math.min(3, Math.floor(Number(q('image-concurrency')?.value || 1) || 1)));
  if (!imageGenerationExperimental) {
    return { size: '1024x1024', concurrency: 1 };
  }
  return { size: ratio, concurrency };
}

async function streamImage(body, headers) {
  const res = await fetch('/v1/images/generations', {
    method: 'POST',
    headers,
    body: JSON.stringify({ ...body, stream: true }),
  });

  if (!res.ok || !res.body) {
    const t = await res.text().catch(() => '');
    throw new Error(`HTTP ${res.status}: ${t.slice(0, 200)}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  const cardMap = new Map();
  const completedSet = new Set();
  let rendered = 0;
  let buf = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buf += decoder.decode(value, { stream: true });
    const blocks = buf.split('\n\n');
    buf = blocks.pop() || '';

    for (const block of blocks) {
      const lines = block.split('\n').map((line) => line.trim()).filter(Boolean);
      if (!lines.length) continue;

      let event = '';
      const dataLines = [];
      lines.forEach((line) => {
        if (line.startsWith('event:')) event = line.slice(6).trim();
        if (line.startsWith('data:')) dataLines.push(line.slice(5).trim());
      });

      const payload = dataLines.join('\n').trim();
      if (!payload) continue;
      if (payload === '[DONE]') return rendered;

      let obj = null;
      try {
        obj = JSON.parse(payload);
      } catch (e) {
        continue;
      }

      const type = String(obj?.type || event || '').trim();
      const idx = Math.max(0, Number(obj?.index) || 0);
      const card = ensureImageCard(cardMap, idx);

      if (type === 'image_generation.partial_image') {
        updateImageCardProgress(card, obj?.progress ?? 0);
        continue;
      }

      if (type === 'image_generation.completed') {
        const src = pickImageSrc(obj);
        const failed = !src;
        updateImageCardCompleted(card, src, failed);
        if (!failed && !completedSet.has(idx)) {
          completedSet.add(idx);
          rendered += 1;
        }
      }
    }
  }

  return rendered;
}

async function generateImage() {
  const prompt = String(q('image-prompt').value || '').trim();
  if (!prompt) return showToast('请输入 prompt', 'warning');

  const headers = { ...buildApiHeaders(), 'Content-Type': 'application/json' };
  if (!headers.Authorization) return showToast('请先填写 API Key', 'warning');

  if (imageGenerationExperimental && getImageRunMode() === 'continuous') {
    startImageContinuous();
    return;
  }

  stopImageContinuous();

  const model = String(q('model-select').value || 'grok-imagine-1.0').trim();
  const n = Math.max(1, Math.min(10, Math.floor(Number(q('image-n').value || 1) || 1)));
  const stream = Boolean(q('stream-toggle').checked);
  const useStream = stream && n <= 2;
  const { size, concurrency } = buildImageRequestConfig();

  q('image-results').innerHTML = '';
  showToast('生成中...', 'info');

  const reqBody = { prompt, model, n, size, concurrency };
  try {
    if (stream && !useStream) {
      showToast('n > 2 disables stream and falls back to non-stream mode.', 'warning');
    }

    if (useStream) {
      const rendered = await streamImage(reqBody, headers);
      if (!rendered) throw new Error('No image generated');
      return;
    }

    const res = await fetch('/v1/images/generations', {
      method: 'POST',
      headers,
      body: JSON.stringify({ ...reqBody, stream: false }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data?.error?.message || data?.detail || `HTTP ${res.status}`);

    const items = Array.isArray(data?.data) ? data.data : [];
    if (!items.length) throw new Error('No image generated');

    let rendered = 0;
    items.forEach((it, idx) => {
      const src = pickImageSrc(it);
      const card = createImageCard(idx);
      q('image-results').appendChild(card);
      if (!src) {
        updateImageCardCompleted(card, '', true);
        return;
      }
      rendered += 1;
      updateImageCardCompleted(card, src, false);
    });

    if (!rendered) throw new Error('Image data is empty or unsupported');
  } catch (e) {
    showToast('生图失败: ' + (e?.message || e), 'error');
  }
}

async function generateVideo() {
  const prompt = String(q('video-prompt').value || '').trim();
  if (!prompt) return showToast('请输入 prompt', 'warning');

  const model = String(q('model-select').value || 'grok-imagine-1.0-video').trim();
  const stream = Boolean(q('stream-toggle').checked);
  const headers = { ...buildApiHeaders(), 'Content-Type': 'application/json' };
  if (!headers.Authorization) return showToast('请先填写 API Key', 'warning');

  const videoConfig = {
    aspect_ratio: String(q('video-aspect').value || '3:2'),
    video_length: Number(q('video-length').value || 6),
    resolution: String(q('video-resolution').value || 'SD'),
    preset: String(q('video-preset').value || 'custom'),
  };

  try {
    let imgUrls = [];
    if (videoAttachments.length) {
      showToast('上传图片中...', 'info');
      imgUrls = await uploadImages(videoAttachments.slice(0, 1).map((x) => x.file));
    }

    const userContent = imgUrls.length
      ? [{ type: 'text', text: prompt }, ...imgUrls.map((u) => ({ type: 'image_url', image_url: { url: u } }))]
      : prompt;

    const reqBody = { model, messages: [{ role: 'user', content: userContent }], stream, video_config: videoConfig };

    q('video-results').innerHTML = '';
    const bubble = document.createElement('div');
    bubble.className = 'msg-bubble';
    q('video-results').appendChild(bubble);

    if (stream) {
      await streamVideo(reqBody, bubble);
    } else {
      const res = await fetch('/v1/chat/completions', { method: 'POST', headers, body: JSON.stringify(reqBody) });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data?.error?.message || data?.detail || `HTTP ${res.status}`);
      const content = data?.choices?.[0]?.message?.content || '';
      renderContent(bubble, content, false);
    }

    videoAttachments.forEach((a) => {
      try { URL.revokeObjectURL(a.previewUrl); } catch (e) {}
    });
    videoAttachments = [];
    renderAttachments('video');
  } catch (e) {
    showToast('生成视频失败: ' + (e?.message || e), 'error');
  }
}

async function streamVideo(body, bubbleEl) {
  const headers = { ...buildApiHeaders(), 'Content-Type': 'application/json' };
  const res = await fetch('/v1/chat/completions', { method: 'POST', headers, body: JSON.stringify(body) });
  if (!res.ok || !res.body) {
    const t = await res.text().catch(() => '');
    throw new Error(`HTTP ${res.status}: ${t.slice(0, 200)}`);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  let acc = '';
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const parts = buf.split('\n\n');
    buf = parts.pop() || '';
    for (const part of parts) {
      const line = part.trim();
      if (!line.startsWith('data:')) continue;
      const payload = line.slice(5).trim();
      if (payload === '[DONE]') return;
      try {
        const obj = JSON.parse(payload);
        const delta = obj?.choices?.[0]?.delta?.content;
        if (typeof delta === 'string' && delta) {
          acc += delta;
          renderContent(bubbleEl, acc, false);
        }
      } catch (e) {}
    }
  }
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}

