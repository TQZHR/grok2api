const usernameInput = document.getElementById('username-input');
const passwordInput = document.getElementById('password-input');

usernameInput.addEventListener('keypress', function (e) {
  if (e.key === 'Enter') passwordInput.focus();
});

passwordInput.addEventListener('keypress', function (e) {
  if (e.key === 'Enter') login();
});

async function login() {
  const username = (usernameInput.value || '').trim();
  const password = (passwordInput.value || '').trim();
  if (!username || !password) return;

  try {
    const res = await fetch('/api/v1/admin/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password })
    });

    if (res.ok) {
      await storeAppKey({ username, password });
      window.location.href = '/admin/token';
    } else {
      showToast('用户名或密码错误', 'error');
    }
  } catch (e) {
    showToast('连接失败', 'error');
  }
}

// Auto-redirect checks
(async () => {
  const existing = await getStoredAppKey();
  const existingUsername = (existing && existing.username) ? String(existing.username) : '';
  const existingPassword = (existing && existing.password) ? String(existing.password) : '';

  usernameInput.value = existingUsername || 'admin';
  passwordInput.focus();

  if (!existingPassword) return;

  fetch('/api/v1/admin/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: usernameInput.value.trim(), password: existingPassword })
  }).then(res => {
    if (res.ok) window.location.href = '/admin/token';
  });
})();
