let apiKey = '';
let cachedRows = [];
let editingKey = null; // full key string

function q(id) {
  return document.getElementById(id);
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

function copyToClipboard(text) {
  try {
    navigator.clipboard.writeText(text);
    showToast('已复制', 'success');
  } catch (e) {
    showToast('复制失败', 'error');
  }
}

async function init() {
  apiKey = await ensureApiKey();
  if (apiKey === null) return;
  await loadKeys();
}

async function loadKeys() {
  q('loading').classList.remove('hidden');
  q('empty-state').classList.add('hidden');
  q('keys-table-body').innerHTML = '';
  try {
    const res = await fetch('/api/v1/admin/keys', { headers: buildAuthHeaders(apiKey) });
    if (res.status === 401) return logout();
    const data = await res.json();
    if (!data || data.success !== true) throw new Error(data?.error || '加载失败');
    cachedRows = Array.isArray(data.data) ? data.data : [];
    renderTable();
  } catch (e) {
    showToast('加载失败: ' + (e?.message || e), 'error');
  } finally {
    q('loading').classList.add('hidden');
  }
}

function renderTable() {
  const body = q('keys-table-body');
  body.innerHTML = '';

  if (!cachedRows.length) {
    q('empty-state').classList.remove('hidden');
    return;
  }

  cachedRows.forEach((row) => {
    const tr = document.createElement('tr');

    const limits = row;
    const used = row.usage_today || {};

    const statusPill = row.is_active
      ? '<span class="pill">启用</span>'
      : '<span class="pill pill-muted">禁用</span>';

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

    tr.querySelector('[data-action="copy"]').addEventListener('click', () => copyToClipboard(String(row.key || '')));
    tr.querySelector('[data-action="edit"]').addEventListener('click', () => openEditModal(row));
    tr.querySelector('[data-action="delete"]').addEventListener('click', () => deleteKey(row));

    body.appendChild(tr);
  });
}

function escapeHtml(s) {
  return String(s)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

function openCreateModal() {
  editingKey = null;
  q('modal-title').textContent = '新增 API Key';
  q('key-name').value = '';
  q('key-value').value = '';
  q('limit-chat').value = '';
  q('limit-heavy').value = '';
  q('limit-image').value = '';
  q('limit-video').value = '';
  q('key-active').checked = true;
  q('key-value').disabled = false;
  q('key-modal').classList.remove('hidden');
}

function openEditModal(row) {
  editingKey = String(row.key || '');
  q('modal-title').textContent = '编辑 API Key';
  q('key-name').value = String(row.name || '');
  q('key-value').value = String(row.key || '');
  q('key-value').disabled = true;
  q('limit-chat').value = Number(row.chat_limit) >= 0 ? String(row.chat_limit) : '';
  q('limit-heavy').value = Number(row.heavy_limit) >= 0 ? String(row.heavy_limit) : '';
  q('limit-image').value = Number(row.image_limit) >= 0 ? String(row.image_limit) : '';
  q('limit-video').value = Number(row.video_limit) >= 0 ? String(row.video_limit) : '';
  q('key-active').checked = Boolean(row.is_active);
  q('key-modal').classList.remove('hidden');
}

function closeKeyModal() {
  q('key-modal').classList.add('hidden');
}

async function submitKeyModal() {
  const name = q('key-name').value.trim();
  const keyVal = q('key-value').value.trim();
  const limits = {
    chat_per_day: q('limit-chat').value.trim(),
    heavy_per_day: q('limit-heavy').value.trim(),
    image_per_day: q('limit-image').value.trim(),
    video_per_day: q('limit-video').value.trim(),
  };
  const isActive = q('key-active').checked;

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
      const data = await res.json();
      if (!data?.success) throw new Error(data?.error?.message || data?.error || '创建失败');
      showToast('创建成功', 'success');
    } else {
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
      const data = await res.json();
      if (!data?.success) throw new Error(data?.error?.message || data?.error || '更新失败');
      showToast('更新成功', 'success');
    }

    closeKeyModal();
    await loadKeys();
  } catch (e) {
    showToast('操作失败: ' + (e?.message || e), 'error');
  }
}

async function deleteKey(row) {
  const key = String(row.key || '');
  if (!key) return;
  if (!confirm('确定删除该 API Key 吗？此操作不可恢复。')) return;
  try {
    const res = await fetch('/api/v1/admin/keys/delete', {
      method: 'POST',
      headers: { ...buildAuthHeaders(apiKey), 'Content-Type': 'application/json' },
      body: JSON.stringify({ key }),
    });
    if (res.status === 401) return logout();
    const data = await res.json();
    if (!data?.success) throw new Error(data?.error?.message || data?.error || '删除失败');
    showToast('删除成功', 'success');
    await loadKeys();
  } catch (e) {
    showToast('删除失败: ' + (e?.message || e), 'error');
  }
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}

