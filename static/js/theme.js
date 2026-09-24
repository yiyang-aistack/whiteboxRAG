/**
 * static/index.html - theme (light / dark) initialization and toggling
 *
 * Loaded synchronously in <head>:
 *   1. the IIFE at the top adds the dark class to <html> before the first paint, avoiding a white
 *      flash on reload;
 *   2. toggleTheme() / syncThemeIcons() are called by the page buttons' onclick;
 *   3. the initial icon sync now runs once the DOM is ready (the inline script used to sit before
 *      </body>, which is equivalent).
 */

// Apply the theme as early as possible, avoiding a white flash on reload
(function () {
    var t = localStorage.getItem('theme') || (window.matchMedia && matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    if (t === 'dark') document.documentElement.classList.add('dark');
    window.__theme = t;
})();

// Theme toggling and icon sync
function toggleTheme() {
    var isDark = document.documentElement.classList.toggle('dark');
    var t = isDark ? 'dark' : 'light';
    localStorage.setItem('theme', t);
    syncThemeIcons(t);
}
function syncThemeIcons(t) {
    document.querySelectorAll('.theme-icon-sun').forEach(function (el) { el.classList.toggle('hidden', t !== 'dark'); });
    document.querySelectorAll('.theme-icon-moon').forEach(function (el) { el.classList.toggle('hidden', t === 'dark'); });
}

// Sync the icons with the already applied theme on first load (runs once the DOM is ready)
function syncInitialThemeIcons() {
    syncThemeIcons(window.__theme || (document.documentElement.classList.contains('dark') ? 'dark' : 'light'));
}
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', syncInitialThemeIcons);
} else {
    syncInitialThemeIcons();
}
