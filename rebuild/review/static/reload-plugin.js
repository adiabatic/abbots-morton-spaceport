// A livereload client plugin, loaded as a classic script before livereload's injected loader, because the client adds every `LiveReloadPlugin*` constructor on window only when it loads. The client asks each plugin's reload(path) before reloading the page, and a true answer stops it. This plugin claims the `ams:` paths the cycle sends (reload.js names them) and passes each to the app as an `ams-reload` event, or keeps the latest in `window.__amsPendingReload` until the app sets `window.__amsReloadReady`, so the app decides when the page reloads.
(() => {
  class LiveReloadPluginAms {
    static identifier = 'ams';
    static version = '1';

    reload(path) {
      if (typeof path !== 'string' || !path.startsWith('ams:')) return false;
      if (window.__amsReloadReady) window.dispatchEvent(new CustomEvent('ams-reload', { detail: { path } }));
      else window.__amsPendingReload = path;
      return true;
    }
  }
  window.LiveReloadPluginAms = LiveReloadPluginAms;
})();
