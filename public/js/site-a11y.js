/**
 * Accessibility helper: ensure <main> is the focusable skip-link target.
 */
document.addEventListener('DOMContentLoaded', () => {
  const main = document.querySelector('main');
  if (!main) return;
  if (!main.id) main.id = 'main-content';
  if (!main.hasAttribute('tabindex')) main.setAttribute('tabindex', '-1');
  const skipLink = document.querySelector('.skip-link');
  if (skipLink) skipLink.setAttribute('href', `#${main.id}`);
}, { once: true });
