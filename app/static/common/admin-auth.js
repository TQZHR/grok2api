const APP_KEY_STORAGE = 'grok2api_app_key';
const APP_KEY_ENC_PREFIX = 'enc:v1:';
const APP_KEY_XOR_PREFIX = 'enc:xor:';
const APP_KEY_SECRET = 'grok2api-admin-key';
let cachedApiKey = null;

const textEncoder = new TextEncoder();
const textDecoder = new TextDecoder();

function toBase64(bytes) {
  let binary = '';
  bytes.forEach(b => { binary += String.fromCharCode(b); });
  return btoa(binary);
}

function fromBase64(base64) {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes;
}

function xorCipher(bytes, keyBytes) {
  const out = new Uint8Array(bytes.length);
  for (let i = 0; i < bytes.length; i++) {
    out[i] = bytes[i] ^ keyBytes[i % keyBytes.length];
  }
  return out;
}

function xorEncrypt(plain) {
  const data = textEncoder.encode(plain);
  const key = textEncoder.encode(APP_KEY_SECRET);
  const cipher = xorCipher(data, key);
  return `${APP_KEY_XOR_PREFIX}${toBase64(cipher)}`;
}

function xorDecrypt(stored) {
  if (!stored.startsWith(APP_KEY_XOR_PREFIX)) return stored;
  const payload = stored.slice(APP_KEY_XOR_PREFIX.length);
  const data = fromBase64(payload);
  const key = textEncoder.encode(APP_KEY_SECRET);
  const plain = xorCipher(data, key);
  return textDecoder.decode(plain);
}

async function deriveKey(salt) {
  const keyMaterial = await crypto.subtle.importKey(
    'raw',
    textEncoder.encode(APP_KEY_SECRET),
    'PBKDF2',
    false,
    ['deriveKey']
  );
  return crypto.subtle.deriveKey(
    {
      name: 'PBKDF2',
      salt,
      iterations: 100000,
      hash: 'SHA-256'
    },
    keyMaterial,
    { name: 'AES-GCM', length: 256 },
    false,
    ['encrypt', 'decrypt']
  );
}

async function encryptAppKey(plain) {
  if (!plain) return '';
  if (!crypto?.subtle) return xorEncrypt(plain);
  const salt = crypto.getRandomValues(new Uint8Array(16));
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const key = await deriveKey(salt);
  const cipher = await crypto.subtle.encrypt(
    { name: 'AES-GCM', iv },
    key,
    textEncoder.encode(plain)
  );
  return `${APP_KEY_ENC_PREFIX}${toBase64(salt)}:${toBase64(iv)}:${toBase64(new Uint8Array(cipher))}`;
}

async function decryptAppKey(stored) {
  if (!stored) return '';
  if (stored.startsWith(APP_KEY_XOR_PREFIX)) return xorDecrypt(stored);
  if (!stored.startsWith(APP_KEY_ENC_PREFIX)) return stored;
  if (!crypto?.subtle) return '';
  const parts = stored.split(':');
  if (parts.length !== 5) return '';
  const salt = fromBase64(parts[2]);
  const iv = fromBase64(parts[3]);
  const cipher = fromBase64(parts[4]);
  const key = await deriveKey(salt);
  const plain = await crypto.subtle.decrypt(
    { name: 'AES-GCM', iv },
    key,
    cipher
  );
  return textDecoder.decode(plain);
}

function parseStoredCreds(plain) {
  const raw = (plain || '').trim();
  if (!raw) return { username: '', password: '' };
  try {
    const obj = JSON.parse(raw);
    if (obj && typeof obj === 'object') {
      const username = typeof obj.username === 'string' ? obj.username.trim() : '';
      const password = typeof obj.password === 'string' ? obj.password.trim() : '';
      if (username && password) return { username, password };
    }
  } catch (e) { }
  // Legacy: raw is the password (username defaults to admin)
  return { username: 'admin', password: raw };
}

function serializeCreds(creds) {
  const username = typeof creds?.username === 'string' ? creds.username.trim() : '';
  const password = typeof creds?.password === 'string' ? creds.password.trim() : '';
  if (!username || !password) return '';
  return JSON.stringify({ username, password });
}

async function getStoredAppKey() {
  const stored = localStorage.getItem(APP_KEY_STORAGE) || '';
  if (!stored) return { username: '', password: '' };
  try {
    const plain = await decryptAppKey(stored);
    return parseStoredCreds(plain);
  } catch (e) {
    clearStoredAppKey();
    return { username: '', password: '' };
  }
}

async function storeAppKey(input) {
  if (!input) {
    clearStoredAppKey();
    return;
  }
  const creds = typeof input === 'string' ? { username: 'admin', password: input } : input;
  const serialized = serializeCreds(creds);
  if (!serialized) {
    clearStoredAppKey();
    return;
  }
  const encrypted = await encryptAppKey(serialized);
  localStorage.setItem(APP_KEY_STORAGE, encrypted || '');
}

function clearStoredAppKey() {
  localStorage.removeItem(APP_KEY_STORAGE);
  cachedApiKey = null;
}

async function requestApiKey(creds) {
  const body = serializeCreds(creds);
  const res = await fetch('/api/v1/admin/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body
  });
  if (!res.ok) {
    throw new Error('Unauthorized');
  }
  const data = await res.json();
  const rawApiKey = data.api_key || '';
  cachedApiKey = rawApiKey ? `Bearer ${rawApiKey}` : '';
  return cachedApiKey;
}

async function ensureApiKey() {
  const creds = await getStoredAppKey();
  if (!creds || !creds.password) {
    window.location.href = '/login';
    return null;
  }
  try {
    return await requestApiKey(creds);
  } catch (e) {
    clearStoredAppKey();
    window.location.href = '/login';
    return null;
  }
}

function buildAuthHeaders(apiKey) {
  return apiKey ? { 'Authorization': apiKey } : {};
}

function logout() {
  clearStoredAppKey();
  window.location.href = '/login';
}

async function fetchStorageType() {
  const apiKey = await ensureApiKey();
  if (apiKey === null) return null;
  try {
    const res = await fetch('/api/v1/admin/storage', {
      headers: buildAuthHeaders(apiKey)
    });
    if (!res.ok) return null;
    const data = await res.json();
    return (data && data.type) ? String(data.type) : null;
  } catch (e) {
    return null;
  }
}

function formatStorageLabel(type) {
  if (!type) return '-';
  const normalized = type.toLowerCase();
  const map = {
    local: 'local',
    mysql: 'mysql',
    pgsql: 'pgsql',
    postgres: 'pgsql',
    postgresql: 'pgsql',
    d1: 'd1',
    redis: 'redis'
  };
  return map[normalized] || '-';
}

async function updateStorageModeButton() {
  const buttons = Array.from(document.querySelectorAll('#storage-mode-btn, [data-storage-mode-btn]'));
  if (!buttons.length) return;
  buttons.forEach((btn) => {
    btn.textContent = '...';
    btn.title = '存储模式';
    btn.classList.remove('storage-ready');
  });
  const storageType = await fetchStorageType();
  const label = formatStorageLabel(storageType);
  buttons.forEach((btn) => {
    btn.textContent = label === '-' ? label : label.toUpperCase();
    btn.title = '存储模式';
    if (label !== '-') btn.classList.add('storage-ready');
  });
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', updateStorageModeButton);
} else {
  updateStorageModeButton();
}
