/* ============================================================================
   shell-client.js — сторона КОНТЕНТА (любая страница: студия, дашборд, игра).
   Пара к shell.ts (протокол deck-v1). Подключается обычным <script> без сборки:
     <script src="shell-client.js?b=1"></script>
   Контент НЕ знает, где он запущен:
     · внутри Tauri-оболочки (iframe) → просьбы уходят postMessage'ем наверх
     · сам по себе в браузере       → веб-фолбэк (Fullscreen API / reload)
   window.ShellKit: .framed · .fullscreen(on?) · .reload() · .title(str)
   Правь ЭТАЛОН в deck/desktop/shell-kit, в продукте — только синк.
   ========================================================================== */
(function () {
  if (window.ShellKit) return;
  var framed = (function () {
    try { return !!window.parent && window.parent !== window; } catch (_) { return true; }
  })();

  function post(msg) {
    try { window.parent.postMessage(Object.assign({ shell: 'deck-v1' }, msg), '*'); return true; }
    catch (_) { return false; }
  }

  function webFullscreen(on) {
    var d = document, el = d.documentElement;
    var isOn = !!(d.fullscreenElement || d.webkitFullscreenElement);
    var want = on === undefined ? !isOn : on;
    if (want === isOn) return;
    if (want) (el.requestFullscreen || el.webkitRequestFullscreen || function () {}).call(el);
    else (d.exitFullscreen || d.webkitExitFullscreen || function () {}).call(d);
  }

  window.ShellKit = {
    version: 1,
    framed: framed,
    fullscreen: function (on) { if (framed) post({ cmd: 'fullscreen', on: on }); else webFullscreen(on); },
    reload: function () { if (framed) post({ cmd: 'reload' }); else location.reload(); },
    title: function (v) { if (framed) post({ cmd: 'title', value: String(v) }); else document.title = String(v); }
  };
})();
