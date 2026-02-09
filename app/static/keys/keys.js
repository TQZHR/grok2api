let apiKey = '';
let cachedRows = [];
let displayRows = [];
let editingKey = null;
let isSubmitting = false;
let keyModalEscBound = false;
const MODAL_TRANSITION_MS = 200;
let keyModalHideTimer = null;

const keyFilterState = {
  search: '',
  status: 'all',
};

function q(id) {
  return document.getElementById(id);
}

function escapeHtml(s) {
  return String(s)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

function fmtLimit(v) {
  const n = Number(v);
  if (!Number.isFinite(n) || n < 0) return '不限';
  return String(Math.floor(n));
}

function fmtDate(tsSec) {
  const n = Number(tsSec);
  if (!Number.isFinite(n) || n <= 0) return '-';
  const d = new Date(Math.floor(n) * 1000);
  return d.toLocaleString();
}

function parseRemainingValue(v) {
  if (v === null || v === undefined || v === '') return null;
  const n = Number(v);
  if (!Number.isFinite(n)) return null;
  return Math.max(0, Math.floor(n));
}

function normalizeUsageValue(v) {
  const n = Number(v);
  if (!Number.isFinite(n) || n < 0) return 0;
  return Math.floor(n);
}

function normalizeKeyRow(row) {
  const usage = row && typeof row === 'object' ? (row.usage_today || {}) : {};
  const remaining = row && typeof row === 'object' ? (row.remaining_today || {}) : {};
  return {
    ...(row || {}),
    key: String(row?.key || ''),
    name: String(row?.name || ''),
    display_key: String(row?.display_key || row?.key || ''),
    created_at: Number(row?.created_at || 0),
    is_active: Boolean(row?.is_active),
    chat_limit: Number(row?.chat_limit ?? -1),
    heavy_limit: Number(row?.heavy_limit ?? -1),
    image_limit: Number(row?.image_limit ?? -1),
    video_limit: Number(row?.video_limit ?? -1),
    usage_today: {
      chat_used: normalizeUsageValue(usage.chat_used),
      heavy_used: normalizeUsageValue(usage.heavy_used),
      image_used: normalizeUsageValue(usage.image_used),
      video_used: normalizeUsageValue(usage.video_used),
    },
    remaining_today: {
      chat: parseRemainingValue(remaining.chat),
      heavy: parseRemainingValue(remaining.heavy),
      image: parseRemainingValue(remaining.image),
      video: parseRemainingValue(remaining.video),
    },
  };
}

function deriveKeyState(row) {
  const isActive = Boolean(row.is_active);
  const remaining = row.remaining_today || {};
  const values = [remaining.chat, remaining.heavy, remaining.image, remaining.video].filter((v) => v !== null);
  const isExhausted = values.length > 0 && values.some((v) => Number(v) <= 0);
  return {
    isActive,
    isInactive: !isActive,
    isExhausted,
  };
}

function extractErrorMessage(payload, fallback = '请求失败') {
  if (!payload) return fallback;
  if (typeof payload === 'string' && payload.trim()) return payload.trim();
  if (typeof payload.detail === 'string' && payload.detail.trim()) return payload.detail.trim();
  if (typeof payload.error === 'string' && payload.error.trim()) return payload.error.trim();
  if (typeof payload.message === 'string' && payload.message.trim()) return payload.message.trim();
  if (payload.error && typeof payload.error.message === 'string' && payload.error.message.trim()) return payload.error.message.trim();
  if (payload.data && typeof payload.data.message === 'string' && payload.data.message.trim()) return payload.data.message.trim();
  return fallback;
}

async function parseJsonSafely(response) {
  try {
    return await response.json();
  } catch (e) {
    return null;
  }
}

function setLoading(loading) {
  const el = q('loading');
  if (!el) return;
  el.classList.toggle('hidden', !loading);
}

function setEmptyState(visible, text = '') {
  const el = q('empty-state');
  if (!el) return;
  if (text) el.textContent = text;
  el.classList.toggle('hidden', !visible);
}

function setText(id, value) {
  const el = q(id);
  if (el) el.textContent = String(value);
}

function refreshKeyFilterStateFromDom() {
  keyFilterState.search = String(q('keys-search')?.value || '').trim().toLowerCase();
  keyFilterState.status = String(q('keys-status-filter')?.value || 'all');
}

function applyKeyFilters() {
  refreshKeyFilterStateFromDom();
  const { search, status } = keyFilterState;

  displayRows = cachedRows.filter((row) => {
    const haystack = `${row.name} ${row.key} ${row.display_key}`.toLowerCase();
    const matchSearch = !search || haystack.includes(search);
    if (!matchSearch) return false;

    const state = deriveKeyState(row);
    if (status === 'active') return state.isActive;
    if (status === 'inactive') return state.isInactive;
    if (status === 'exhausted') return state.isExhausted;
    return true;
  });

  setText('keys-filter-count', displayRows.length);
}

function updateStats() {
  const total = cachedRows.length;
  let active = 0;
  let inactive = 0;
  let exhausted = 0;

  cachedRows.forEach((row) => {
    const state = deriveKeyState(row);
    if (state.isActive) active += 1;
    if (state.isInactive) inactive += 1;
    if (state.isExhausted) exhausted += 1;
  });

  setText('keys-stat-total', total);
  setText('keys-stat-active', active);
  setText('keys-stat-inactive', inactive);
  setText('keys-stat-exhausted', exhausted);
}

function renderTable() {
  const body = q('keys-table-body');
  if (!body) return;
  body.innerHTML = '';

  if (!cachedRows.length) {
    setEmptyState(true, '暂无 API Key，请点击右上角新增。');
    return;
  }

  if (!displayRows.length) {
    setEmptyState(true, '没有符合筛选条件的 API Key。');
    return;
  }

  setEmptyState(false);

  displayRows.forEach((row) => {
    const tr = document.createElement('tr');

    const used = row.usage_today || {};
    const limits = row;
    const state = deriveKeyState(row);
    let statusPill = '<span class="pill pill-muted">禁用</span>';
    if (state.isExhausted) {
      statusPill = '<span class="pill" style="background:#fff7ed;color:#c2410c;border-color:#fed7aa;">额度用尽</span>';
    } else if (state.isActive) {
      statusPill = '<span class="pill" style="background:#ecfdf5;color:#047857;border-color:#bbf7d0;">启用</span>';
    }

    const limitText = `${fmtLimit(limits.chat_limit)} / ${fmtLimit(limits.heavy_limit)} / ${fmtLimit(limits.image_limit)} / ${fmtLimit(limits.video_limit)}`;
    const usedText = `${Number(used.chat_used || 0)} / ${Number(used.heavy_used || 0)} / ${Number(used.image_used || 0)} / ${Number(used.video_used || 0)}`;

    tr.innerHTML = `
      <td class="text-left">
        <div class="font-medium">${escapeHtml(String(row.name || ''))}</div>
        <div class="text-xs text-[var(--accents-5)] mono">${escapeHtml(String(row.key || ''))}</div>
      </td>
      <td class="text-left">
        <div class="mono">${escapeHtml(String(row.display_key || row.key || ''))}</div>
        <button class="btn-link mt-1" data-action="copy">复制</button>
      </td>
      <td class="text-center">${statusPill}</td>
      <td class="text-left mono">${escapeHtml(limitText)}</td>
      <td class="text-left mono">${escapeHtml(usedText)}</td>
      <td class="text-center text-sm">${escapeHtml(fmtDate(row.created_at))}</td>
      <td class="text-center">
        <button class="geist-button-outline text-xs px-3 py-1" data-action="edit">编辑</button>
        <button class="geist-button-danger text-xs px-3 py-1 ml-2" data-action="delete">删除</button>
      </td>
    `;

    tr.querySelector('[data-action="copy"]')?.addEventListener('click', () => {
      copyToClipboard(String(row.key || ''));
    });
    tr.querySelector('[data-action="edit"]')?.addEventListener('click', () => {
      openEditModal(row);
    });
    tr.querySelector('[data-action="delete"]')?.addEventListener('click', () => {
      deleteKey(row);
    });

    body.appendChild(tr);
  });
}

function randomSegment(length) {
  const chars = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789';
  const charsLen = chars.length;
  const bytes = new Uint8Array(length);
  if (window.crypto?.getRandomValues) {
    window.crypto.getRandomValues(bytes);
  } else {
    for (let i = 0; i < length; i += 1) bytes[i] = Math.floor(Math.random() * 256);
  }
  let out = '';
  for (let i = 0; i < length; i += 1) out += chars[bytes[i] % charsLen];
  return out;
}

function generateRandomApiKey() {
  return `sk-${randomSegment(24)}`;
}

async function copyToClipboard(text, silent = false) {
  try {
    await navigator.clipboard.writeText(text);
    if (!silent) showToast('已复制', 'success');
    return true;
  } catch (e) {
    if (!silent) showToast('复制失败', 'error');
    return false;
  }
}

function setSubmitState(submitting) {
  isSubmitting = submitting;
  const btn = q('submit-btn');
  if (!btn) return;
  const idleLabel = btn.dataset.idleLabel || '保存';
  btn.disabled = submitting;
  btn.textContent = submitting ? '提交中...' : idleLabel;
}

function normalizeLimitInput(raw) {
  const v = String(raw || '').trim();
  if (!v) return '';
  const n = Number(v);
  if (!Number.isFinite(n) || n < 0) return '';
  return String(Math.floor(n));
}

function buildLimitsPayload() {
  return {
    chat_per_day: normalizeLimitInput(q('limit-chat')?.value),
    heavy_per_day: normalizeLimitInput(q('limit-heavy')?.value),
    image_per_day: normalizeLimitInput(q('limit-image')?.value),
    video_per_day: normalizeLimitInput(q('limit-video')?.value),
  };
}

function openCreateModal() {
  editingKey = null;
  q('key-modal-title').textContent = '新增 API Key';
  q('key-name').value = '';
  q('key-value').value = '';
  q('key-value').disabled = false;
  q('limit-chat').value = '';
  q('limit-heavy').value = '';
  q('limit-image').value = '';
  q('limit-video').value = '';
  q('key-active').checked = true;
  const btn = q('submit-btn');
  if (btn) btn.dataset.idleLabel = '创建';
  setSubmitState(false);
  const modal = q('key-modal');
  if (!modal) return;
  if (keyModalHideTimer) {
    clearTimeout(keyModalHideTimer);
    keyModalHideTimer = null;
  }
  modal.classList.remove('hidden');
  requestAnimationFrame(() => {
    modal.classList.add('is-open');
  });
}

function openEditModal(row) {
  editingKey = String(row.key || '');
  q('key-modal-title').textContent = '编辑 API Key';
  q('key-name').value = String(row.name || '');
  q('key-value').value = String(row.key || '');
  q('key-value').disabled = true;
  q('limit-chat').value = Number(row.chat_limit) >= 0 ? String(row.chat_limit) : '';
  q('limit-heavy').value = Number(row.heavy_limit) >= 0 ? String(row.heavy_limit) : '';
  q('limit-image').value = Number(row.image_limit) >= 0 ? String(row.image_limit) : '';
  q('limit-video').value = Number(row.video_limit) >= 0 ? String(row.video_limit) : '';
  q('key-active').checked = Boolean(row.is_active);
  const btn = q('submit-btn');
  if (btn) btn.dataset.idleLabel = '保存';
  setSubmitState(false);
  const modal = q('key-modal');
  if (!modal) return;
  if (keyModalHideTimer) {
    clearTimeout(keyModalHideTimer);
    keyModalHideTimer = null;
  }
  modal.classList.remove('hidden');
  requestAnimationFrame(() => {
    modal.classList.add('is-open');
  });
}

function closeKeyModal() {
  const modal = q('key-modal');
  if (!modal) return;
  modal.classList.remove('is-open');
  if (keyModalHideTimer) clearTimeout(keyModalHideTimer);
  keyModalHideTimer = window.setTimeout(() => {
    modal.classList.add('hidden');
    keyModalHideTimer = null;
  }, MODAL_TRANSITION_MS);
}

function generateKeyValue() {
  const input = q('key-value');
  if (!input || input.disabled) return;
  input.value = generateRandomApiKey();
  showToast('已生成 API Key', 'success');
}

function applyKeyLimitPreset(mode) {
  const recommended = {
    chat: '300',
    heavy: '100',
    image: '100',
    video: '20',
  };
  if (mode === 'unlimited') {
    q('limit-chat').value = '';
    q('limit-heavy').value = '';
    q('limit-image').value = '';
    q('limit-video').value = '';
    return;
  }
  q('limit-chat').value = recommended.chat;
  q('limit-heavy').value = recommended.heavy;
  q('limit-image').value = recommended.image;
  q('limit-video').value = recommended.video;
}

async function loadKeys() {
  const body = q('keys-table-body');
  if (body) body.innerHTML = '';
  setLoading(true);
  setEmptyState(false);
  try {
    const res = await fetch('/api/v1/admin/keys', { headers: buildAuthHeaders(apiKey) });
    if (res.status === 401) return logout();
    const payload = await parseJsonSafely(res);
    if (!res.ok || payload?.success !== true) {
      throw new Error(extractErrorMessage(payload, '加载失败'));
    }
    const rows = Array.isArray(payload.data) ? payload.data : [];
    cachedRows = rows.map((row) => normalizeKeyRow(row));
    updateStats();
    applyKeyFilters();
    renderTable();
  } catch (e) {
    showToast(`加载失败: ${e?.message || e}`, 'error');
  } finally {
    setLoading(false);
  }
}

async function submitKeyModal() {
  if (isSubmitting) return;

  const name = String(q('key-name')?.value || '').trim();
  const keyVal = String(q('key-value')?.value || '').trim();
  const limits = buildLimitsPayload();
  const isActive = Boolean(q('key-active')?.checked);

  setSubmitState(true);
  try {
    if (!editingKey) {
      const res = await fetch('/api/v1/admin/keys', {
        method: 'POST',
        headers: { ...buildAuthHeaders(apiKey), 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: name || '',
          key: keyVal || '',
          limits,
          is_active: isActive,
        }),
      });
      if (res.status === 401) return logout();
      const payload = await parseJsonSafely(res);
      if (!res.ok || payload?.success !== true) {
        throw new Error(extractErrorMessage(payload, '创建失败'));
      }

      closeKeyModal();
      await loadKeys();

      const createdKey = String(payload?.data?.key || keyVal || '');
      if (createdKey) {
        const copied = await copyToClipboard(createdKey, true);
        showToast(copied ? '创建成功，已复制 Key' : '创建成功', 'success');
      } else {
        showToast('创建成功', 'success');
      }
      return;
    }

    const res = await fetch('/api/v1/admin/keys/update', {
      method: 'POST',
      headers: { ...buildAuthHeaders(apiKey), 'Content-Type': 'application/json' },
      body: JSON.stringify({
        key: editingKey,
        name: name || undefined,
        is_active: isActive,
        limits,
      }),
    });
    if (res.status === 401) return logout();
    const payload = await parseJsonSafely(res);
    if (!res.ok || payload?.success !== true) {
      throw new Error(extractErrorMessage(payload, '更新失败'));
    }

    closeKeyModal();
    await loadKeys();
    showToast('更新成功', 'success');
  } catch (e) {
    showToast(`操作失败: ${e?.message || e}`, 'error');
  } finally {
    setSubmitState(false);
  }
}

async function deleteKey(row) {
  const key = String(row.key || '').trim();
  if (!key) return;
  if (!confirm('确定删除该 API Key 吗？此操作不可恢复。')) return;

  try {
    const res = await fetch('/api/v1/admin/keys/delete', {
      method: 'POST',
      headers: { ...buildAuthHeaders(apiKey), 'Content-Type': 'application/json' },
      body: JSON.stringify({ key }),
    });
    if (res.status === 401) return logout();
    const payload = await parseJsonSafely(res);
    if (!res.ok || payload?.success !== true) {
      throw new Error(extractErrorMessage(payload, '删除失败'));
    }
    await loadKeys();
    showToast('删除成功', 'success');
  } catch (e) {
    showToast(`删除失败: ${e?.message || e}`, 'error');
  }
}

function onKeyFilterChange() {
  applyKeyFilters();
  renderTable();
}

function resetKeyFilters() {
  const search = q('keys-search');
  const status = q('keys-status-filter');
  if (search) search.value = '';
  if (status) status.value = 'all';
  applyKeyFilters();
  renderTable();
}

async function init() {
  apiKey = await ensureApiKey();
  if (apiKey === null) return;
  const modal = q('key-modal');
  if (modal && !keyModalEscBound) {
    modal.addEventListener('click', (event) => {
      if (event.target === modal) closeKeyModal();
    });
    document.addEventListener('keydown', (event) => {
      if (event.key !== 'Escape') return;
      if (modal.classList.contains('hidden')) return;
      closeKeyModal();
    });
    keyModalEscBound = true;
  }
  await loadKeys();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
