const STORAGE_KEY = 'grok2api_user_api_key';

let currentTab = 'chat';
let models = [];
let chatMessages = [];
let chatAttachments = []; // { file, previewUrl }
let videoAttachments = [];

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
  await refreshModels();

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
    div.innerHTML = `<img src="${it.previewUrl}" alt="img"><button title="移除">×</button>`;
    div.querySelector('button').addEventListener('click', () => {
      try { URL.revokeObjectURL(it.previewUrl); } catch (e) {}
      list.splice(idx, 1);
      renderAttachments(kind);
    });
    box.appendChild(div);
  });
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
      if (currentTab === 'image') return /imagine/i.test(id) && !/video/i.test(id);
      if (currentTab === 'video') return /video/i.test(id);
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
  localStorage.setItem(STORAGE_KEY, k);
  showToast('已保存', 'success');
  refreshModels();
}

function clearApiKey() {
  localStorage.removeItem(STORAGE_KEY);
  q('api-key-input').value = '';
  showToast('已清除', 'success');
}

function switchTab(tab) {
  currentTab = tab;
  ['chat', 'image', 'video'].forEach((t) => {
    q(`tab-${t}`).classList.toggle('active', t === tab);
    q(`panel-${t}`).classList.toggle('hidden', t !== tab);
  });
  refreshModels();
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

async function generateImage() {
  const prompt = String(q('image-prompt').value || '').trim();
  if (!prompt) return showToast('请输入 prompt', 'warning');
  const model = String(q('model-select').value || 'grok-imagine-1.0').trim();
  const n = Math.max(1, Math.min(10, Math.floor(Number(q('image-n').value || 1) || 1)));

  const headers = { ...buildApiHeaders(), 'Content-Type': 'application/json' };
  if (!headers.Authorization) return showToast('请先填写 API Key', 'warning');

  q('image-results').innerHTML = '';
  showToast('生成中...', 'info');
  try {
    const res = await fetch('/v1/images/generations', {
      method: 'POST',
      headers,
      body: JSON.stringify({ prompt, model, n, response_format: 'url' }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data?.error?.message || data?.detail || `HTTP ${res.status}`);

    const items = Array.isArray(data?.data) ? data.data : [];
    if (!items.length) throw new Error('没有生成结果');

    items.forEach((it) => {
      const url = it.url ? String(it.url) : it.b64_json ? `data:image/png;base64,${String(it.b64_json)}` : '';
      if (!url) return;
      const card = document.createElement('div');
      card.className = 'result-card';
      card.innerHTML = `<img src="${escapeHtml(url)}" alt="image" />`;
      q('image-results').appendChild(card);
    });
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
