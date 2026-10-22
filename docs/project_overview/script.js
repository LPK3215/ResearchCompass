/* ResearchCompass project overview — navigation, theme, tabs, counters and copy actions. */

const $ = (selector, scope = document) => scope.querySelector(selector);
const $$ = (selector, scope = document) => Array.from(scope.querySelectorAll(selector));

const root = document.documentElement;
const themeButton = $('#theme-toggle');
const themeMeta = $('meta[name="theme-color"]');
const menuButton = $('#menu-toggle');
const mobileMenu = $('#mobile-menu');
const progressBar = $('#scroll-progress');
const backToTop = $('#back-to-top');
const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

const themeStorageKey = 'researchcompass-theme';
const allowedThemes = new Set(['dark', 'light']);

function systemTheme() {
  return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
}

function effectiveTheme() {
  return root.dataset.theme || systemTheme();
}

function setTheme(theme, persist = false) {
  const next = allowedThemes.has(theme) ? theme : systemTheme();
  root.dataset.theme = next;
  if (persist) {
    try { localStorage.setItem(themeStorageKey, next); } catch (error) { /* storage can be unavailable on file:// */ }
  }
  if (themeButton) {
    const isLight = next === 'light';
    themeButton.setAttribute('aria-label', isLight ? 'Switch to dark theme' : 'Switch to light theme');
    themeButton.setAttribute('title', isLight ? 'Switch to dark theme' : 'Switch to light theme');
    themeButton.setAttribute('aria-pressed', String(isLight));
  }
  if (themeMeta) {
    themeMeta.setAttribute('content', next === 'light' ? '#f4f9fa' : '#06121a');
  }
  document.dispatchEvent(new CustomEvent('themechange', { detail: { theme: next } }));
}

function initTheme() {
  let saved = null;
  try { saved = localStorage.getItem(themeStorageKey); } catch (error) { /* storage can be unavailable on file:// */ }
  setTheme(saved && allowedThemes.has(saved) ? saved : systemTheme(), false);
  themeButton?.addEventListener('click', () => {
    setTheme(effectiveTheme() === 'light' ? 'dark' : 'light', true);
  });
}

function initMobileMenu() {
  if (!menuButton || !mobileMenu) return;
  const close = () => {
    mobileMenu.classList.remove('is-open');
    menuButton.setAttribute('aria-expanded', 'false');
    menuButton.setAttribute('aria-label', 'Open navigation');
  };
  menuButton.addEventListener('click', () => {
    const open = menuButton.getAttribute('aria-expanded') !== 'true';
    menuButton.setAttribute('aria-expanded', String(open));
    menuButton.setAttribute('aria-label', open ? 'Close navigation' : 'Open navigation');
    mobileMenu.classList.toggle('is-open', open);
  });
  $$('a', mobileMenu).forEach((link) => link.addEventListener('click', close));
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') close();
  });
}

function updateScrollUi() {
  if (progressBar) {
    const scrollable = document.documentElement.scrollHeight - window.innerHeight;
    const ratio = scrollable > 0 ? Math.min(window.scrollY / scrollable, 1) : 0;
    progressBar.style.width = `${ratio * 100}%`;
  }
  backToTop?.classList.toggle('is-visible', window.scrollY > 600);
}

function initScrollUi() {
  let ticking = false;
  const onScroll = () => {
    if (ticking) return;
    ticking = true;
    window.requestAnimationFrame(() => {
      updateScrollUi();
      ticking = false;
    });
  };
  window.addEventListener('scroll', onScroll, { passive: true });
  updateScrollUi();
  backToTop?.addEventListener('click', () => window.scrollTo({ top: 0, behavior: prefersReducedMotion ? 'auto' : 'smooth' }));
}

function initActiveSection() {
  const sections = $$('main section[id]');
  if (!sections.length) return;
  const links = $$('.header-nav a[href^="#"], .sidebar-link[href^="#"]');
  const setActive = (id) => {
    links.forEach((link) => link.classList.toggle('is-active', link.getAttribute('href') === `#${id}`));
  };
  const observer = new IntersectionObserver((entries) => {
    const visible = entries
      .filter((entry) => entry.isIntersecting)
      .sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
    if (visible) setActive(visible.target.id);
  }, { rootMargin: '-20% 0px -65% 0px', threshold: [0, 0.15, 0.35] });
  sections.forEach((section) => observer.observe(section));
  setActive('overview');
}

function initTabs() {
  $$('[data-tab-group]').forEach((button) => {
    button.addEventListener('click', () => {
      const group = button.dataset.tabGroup;
      const target = button.dataset.tabTarget;
      $$(`[data-tab-group="${group}"]`).forEach((candidate) => {
        const active = candidate === button;
        candidate.classList.toggle('is-active', active);
        candidate.setAttribute('aria-selected', String(active));
      });
      $$(`[data-tab-panel="${group}"]`).forEach((panel) => {
        panel.classList.toggle('is-active', panel.dataset.tabId === target);
      });
      document.dispatchEvent(new CustomEvent('tabchange', { detail: { group, target } }));
    });
  });
}

function initViews() {
  $$('[data-view-group]').forEach((button) => {
    button.addEventListener('click', () => {
      const group = button.dataset.viewGroup;
      const target = button.dataset.viewTarget;
      $$(`[data-view-group="${group}"]`).forEach((candidate) => {
        candidate.classList.toggle('is-active', candidate === button);
        candidate.setAttribute('aria-pressed', String(candidate === button));
      });
      $$(`[data-view-panel="${group}"]`).forEach((panel) => {
        panel.classList.toggle('is-active', panel.dataset.viewId === target);
      });
      document.dispatchEvent(new CustomEvent('viewchange', { detail: { group, target } }));
    });
  });
}

function animateCounter(element) {
  if (element.dataset.counted === 'true') return;
  element.dataset.counted = 'true';
  const target = Number(element.dataset.target || 0);
  const decimals = Number(element.dataset.decimals || 0);
  if (!Number.isFinite(target)) return;
  if (prefersReducedMotion || target === 0) {
    element.textContent = target.toFixed(decimals);
    return;
  }
  const duration = 1100;
  const start = performance.now();
  const tick = (now) => {
    const progress = Math.min((now - start) / duration, 1);
    const eased = 1 - Math.pow(1 - progress, 3);
    element.textContent = (target * eased).toFixed(decimals);
    if (progress < 1) window.requestAnimationFrame(tick);
    else element.textContent = target.toFixed(decimals);
  };
  window.requestAnimationFrame(tick);
}

function initCounters() {
  const counters = $$('.counter');
  if (!counters.length) return;
  const observer = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) {
        animateCounter(entry.target);
        observer.unobserve(entry.target);
      }
    });
  }, { threshold: 0.45 });
  counters.forEach((counter) => observer.observe(counter));
}

async function copyText(text) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const area = document.createElement('textarea');
  area.value = text;
  area.setAttribute('readonly', '');
  area.style.position = 'fixed';
  area.style.opacity = '0';
  document.body.appendChild(area);
  area.select();
  document.execCommand('copy');
  area.remove();
}

function initCopyButtons() {
  $$('[data-copy]').forEach((button) => {
    button.addEventListener('click', async () => {
      const block = button.closest('.code-block');
      const code = block?.querySelector('code');
      if (!code) return;
      const label = $('.copy-label', button);
      const original = label?.textContent || 'Copy';
      try {
        await copyText(code.innerText);
        button.classList.add('is-copied');
        if (label) label.textContent = 'Copied';
      } catch (error) {
        if (label) label.textContent = 'Select & copy';
      }
      window.setTimeout(() => {
        button.classList.remove('is-copied');
        if (label) label.textContent = original;
      }, 1600);
    });
  });
}

function initTreeControls() {
  const allDetails = $$('.tree-wrap details');
  $('#expand-tree')?.addEventListener('click', () => allDetails.forEach((item) => { item.open = true; }));
  $('#collapse-tree')?.addEventListener('click', () => allDetails.forEach((item) => { item.open = false; }));
}

function init() {
  initTheme();
  initMobileMenu();
  initScrollUi();
  initActiveSection();
  initTabs();
  initViews();
  initCounters();
  initCopyButtons();
  initTreeControls();
}

init();
