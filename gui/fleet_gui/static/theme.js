/**
 * @fileoverview Applies the saved or requested theme before first paint.
 * Precedence: ?theme=light|dark, then the saved choice, then the OS setting.
 */

(() => {
  const THEMES = ['light', 'dark'];
  try {
    const theme = new URLSearchParams(location.search).get('theme') ||
        localStorage.getItem('fleet-theme');
    if (THEMES.includes(theme)) {
      document.documentElement.dataset.theme = theme;
    }
  } catch (e) {
    // Storage can be blocked (private windows); the OS theme still applies.
  }
})();
