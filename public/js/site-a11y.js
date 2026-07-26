/**
 * Accessibility helper: ensure <main> has id="main-content" for skip link.
 */
document.addEventListener('DOMContentLoaded', () => {
  const main = document.querySelector('main');
  if (main && !main.id) main.id = 'main-content';
}, { once: true });
