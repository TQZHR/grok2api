let mobileNavKeydownHandler = null;

function setupMobileDrawer(container) {
  const toggleBtn = container.querySelector('#mobile-nav-toggle');
  const closeBtn = container.querySelector('#mobile-nav-close');
  const overlay = container.querySelector('#mobile-nav-overlay');
  const drawer = container.querySelector('#mobile-nav-drawer');
  if (!toggleBtn || !overlay || !drawer) return;

  let isOpen = false;
  let hideTimer = null;

  const closeDrawer = () => {
    if (!isOpen) return;
    isOpen = false;
    toggleBtn.setAttribute('aria-expanded', 'false');
    overlay.classList.remove('is-open');
    drawer.classList.remove('is-open');
    if (hideTimer) clearTimeout(hideTimer);
    hideTimer = window.setTimeout(() => {
      overlay.classList.add('hidden');
      drawer.classList.add('hidden');
      drawer.setAttribute('aria-hidden', 'true');
      overlay.setAttribute('aria-hidden', 'true');
      hideTimer = null;
    }, 180);
  };

  const openDrawer = () => {
    if (isOpen) return;
    isOpen = true;
    if (hideTimer) {
      clearTimeout(hideTimer);
      hideTimer = null;
    }
    toggleBtn.setAttribute('aria-expanded', 'true');
    overlay.classList.remove('hidden');
    drawer.classList.remove('hidden');
    drawer.setAttribute('aria-hidden', 'false');
    overlay.setAttribute('aria-hidden', 'false');
    requestAnimationFrame(() => {
      overlay.classList.add('is-open');
      drawer.classList.add('is-open');
    });
  };

  const toggleDrawer = () => {
    if (isOpen) closeDrawer();
    else openDrawer();
  };

  toggleBtn.addEventListener('click', toggleDrawer);
  closeBtn?.addEventListener('click', closeDrawer);
  overlay.addEventListener('click', closeDrawer);
  drawer.querySelectorAll('a[data-nav]').forEach((link) => {
    link.addEventListener('click', closeDrawer);
  });

  if (mobileNavKeydownHandler) {
    document.removeEventListener('keydown', mobileNavKeydownHandler);
  }
  mobileNavKeydownHandler = (event) => {
    if (event.key === 'Escape') closeDrawer();
  };
  document.addEventListener('keydown', mobileNavKeydownHandler);
  window.addEventListener('resize', () => {
    if (window.innerWidth > 768) closeDrawer();
  });
}

async function loadAdminHeader() {
  const container = document.getElementById('app-header');
  if (!container) return;
  try {
    const res = await fetch('/static/common/header.html?v=3', { cache: 'no-store' });
    if (!res.ok) return;
    container.innerHTML = await res.text();
    const path = window.location.pathname;
    const links = container.querySelectorAll('a[data-nav]');
    links.forEach((link) => {
      const target = link.getAttribute('data-nav') || '';
      if (target && path.startsWith(target)) {
        link.classList.add('active');
      }
    });
    setupMobileDrawer(container);
    if (typeof updateStorageModeButton === 'function') {
      updateStorageModeButton();
    }
  } catch (e) {
    // Fail silently to avoid breaking page load
  }
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', loadAdminHeader);
} else {
  loadAdminHeader();
}
