#!/usr/bin/env python3
"""serve_live — движок окна дашборда: ⌘R = свежая пересборка.

Крошечный HTTP-сервер на 127.0.0.1:<эфемерный порт>, который на КАЖДЫЙ GET
прогоняет рендерер (live_projection.py) и отдаёт свежий html. Живёт ровно
столько, сколько открыто окно (Dashboard.app запускает его перед Chrome и
убивает после закрытия) — это НЕ демон, а часть окна. Порт пишется в файл
из --port-file, чтобы обёртка знала адрес.
"""

import argparse
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).parent
RENDER = HERE / "live_projection.py"
# Собранная страница живёт там, где её кладёт РЕНДЕР (`live_projection.OUT`):
# рядом с кодом, пока доска в репозитории, и в доме человека, когда она уехала
# в пакет и соседом кода стал site-packages. Адрес поэтому импортируется ниже
# вместе с домом, а не считается тут второй раз: разъедься эти два ответа,
# сервер отдавал бы файл, которого рендер не пишет.
import os as _os

# ДОМ, ПАПКА РАЗБОРОВ И НАСТРОЙКИ — одни с рендером. Берём их оттуда, а не
# заводим вторую копию правила: разъедься они, кнопка правила бы файл в одном
# доме, а доска показывала другой.
# Импорт мягкий нарочно: рендер могли только что сломать правкой, а сервер обязан
# подняться и отдавать последний удачный билд (урок 12.08 — сутки старой доски).
sys.path.insert(0, str(HERE))
try:
    from live_projection import (HOME as CONTROL_HOME, NEWS_DIR as NEWS_ROOT,
                                 OUT, _conf, works_sources)
except Exception as _exc:  # noqa: BLE001 — сервер важнее причины
    CONTROL_HOME = Path(_os.environ.get("TIDE_HOME")
                        or Path.home() / "tide-home")
    NEWS_ROOT = CONTROL_HOME / ".tide" / "news"
    OUT = HERE / "build" / "board.html"

    def _conf(key, default=""):
        """Запасной ответ: только окружение — файла настроек без рендера нет."""
        return (_os.environ.get(key) or "").strip() or default

    def works_sources():
        """Запасной ответ: только общая папка рядом с кодом — как было всегда."""
        return [("", HERE.parent / ".tide" / "arcs" / "works")]

    print("board: рендер не импортируется ({0}) — дом взят из окружения: {1}"
          .format(str(_exc)[:120], CONTROL_HOME), flush=True)

# транслит кириллицы → латиница для слага новой нити: tide slugify рубит
# кириллицу в пустоту, а имя нити человек часто пишет по-русски (решение 13.07).
_CYR2LAT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "",
    "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def _slugify(name):
    """Имя нити → безопасный слаг: транслит кириллицы, только [a-z0-9-], ≤48."""
    s = "".join(_CYR2LAT.get(ch, ch) for ch in (name or "").lower())
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:48]


# Окружение для вызовов `tide` (единая дверь в механику: return/spark/take и далее).
# Реестр терминалов, фокус, resume — всё живёт в tide; доска — тонкий адаптер (клик → verb).
_TIDE_ENV = {"PATH": "{home}/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin".format(home=str(Path.home())),
             "HOME": str(Path.home()),
             "TIDE_HOME": str(CONTROL_HOME)}

# Рендер — самая дорогая вещь доски: полный прогон live_projection ≈4с, и раньше он шёл
# на КАЖДЫЙ GET /, а вкладка поллит / каждые 5с. Пока доска была частью окна, это сходилось
# (один зритель, сервер умирал с окном). Как только доска стала службой launchd и уехала в
# tailscale, зрителей стало двое (окно + телефон) — прогоны наложились, однопоточный сервер
# перестал успевать, очередь принятых соединений (backlog 5) переполнилась, и клик ⟳ либо
# ждал десятки секунд, либо вовсе не доезжал (connect timeout). Человек читал это как
# «кнопка не работает», хотя `tide return` был жив (29.07).
# Лечим двумя вещами: сервер многопоточный (клик не стоит в очереди за рендером соседней
# вкладки) + один прогон на всех, кто попросил разом — рендерер пишет ОДИН общий .tmp,
# поэтому параллельно его звать нельзя, только под локом.
_RENDER_LOCK = threading.Lock()
_RENDER_FRESH_SEC = 3.0   # билд моложе этого отдаём как есть, без нового прогона
_render_done_at = 0.0     # monotonic конца последнего прогона



class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 (BaseHTTPRequestHandler API)
        if self.path.startswith("/resume"):
            return self._resume()
        if self.path.startswith("/open"):
            return self._open()
        if self.path.startswith("/take"):
            return self._take()
        if self.path.startswith("/spark"):
            return self._spark()
        if self.path.startswith("/drop-cand"):
            return self._drop_cand()
        if self.path.startswith("/drop-thread"):
            return self._drop_thread()
        if self.path.startswith("/new-thread"):
            return self._new_thread()
        if self.path.startswith("/add-cand"):
            return self._add_cand()
        if self.path.startswith("/note-add"):
            return self._note_add()
        if self.path.startswith("/note-edit"):
            return self._note_edit()
        if self.path.startswith("/note-drop"):
            return self._note_drop()
        if self.path.startswith("/news-add"):
            return self._news_add()
        if self.path.startswith("/article"):
            return self._article()
        if self.path.startswith("/news-fav"):
            return self._news_fav()
        if self.path.startswith("/news-process"):
            return self._news_process()
        if self.path.startswith("/news-sync"):
            return self._news_sync()
        if self.path.startswith("/ext"):
            return self._ext()
        if self.path.startswith("/page?"):
            return self._page_get()
        if self.path.startswith("/page-del"):
            return self._page_del()
        if self.path.startswith("/page-asset"):
            return self._page_asset()
        if self.path.startswith("/pg-assets"):
            return self._pg_assets()
        if self.path.startswith("/work-add"):
            return self._work_add()
        if self.path.startswith("/work-check"):
            return self._work_check()
        if self.path.startswith("/work-close"):
            return self._work_close()
        if self.path.startswith("/work-title"):
            return self._work_title()
        if self.path.startswith("/work-deadline"):
            return self._work_deadline()
        if self.path.startswith("/work-reopen"):
            return self._work_reopen()
        if self.path.startswith("/work-desc"):
            return self._work_desc()
        if self.path.startswith("/work-fav"):
            return self._work_fav()
        if self.path.startswith("/work-item-add"):
            return self._work_item_add()
        if self.path.startswith("/work-item-edit"):
            return self._work_item_edit()
        if self.path.startswith("/work-item-del"):
            return self._work_item_del()
        # гейт согласования плана: «да» на работу целиком и «нет» одному
        # предложению. Обе двери доски (карточка работы и стол) зовут ИХ
        if self.path.startswith("/work-from-cand"):
            return self._work_from_cand()
        if self.path.startswith("/work-send"):
            return self._work_send()
        if self.path.startswith("/work-agree"):
            return self._work_agree()
        if self.path.startswith("/work-prop-drop"):
            return self._work_prop_drop()
        # артефакты стола. Проверяются ПОСЛЕ /article, но пересечься с ним не
        # могут: '/artif…' расходится с '/articl…' на пятой букве
        if self.path.startswith("/artifact-take"):
            return self._artifact_take()
        if self.path.startswith("/artifact-fav"):
            return self._artifact_fav()
        if self.path.startswith("/validate"):
            return self._validate()
        if self.path.startswith("/fav"):
            return self._fav()
        if self.path.startswith("/dismiss"):
            return self._dismiss()
        if self.path.startswith("/close"):
            return self._close()
        if self.path.startswith("/reopen"):
            return self._reopen()
        try:
            body = self._render_body()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")  # ⌘R всегда мимо кэша
            self.end_headers()
            self.wfile.write(body)
        except Exception as exc:  # окно не должно видеть трейс — короткая честная страница
            self.send_response(500)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write("доска не собралась: {0}".format(exc).encode())

    def _render_body(self):
        """Свежий board.html — ОДИН прогон рендерера на всех, кто попросил разом.

        Под локом (см. _RENDER_LOCK): билд моложе _RENDER_FRESH_SEC отдаём как есть,
        иначе гоним проекцию. Второй запрос, пришедший во время прогона, дождётся его
        и отдаст тот же результат — вместо второго прогона поверх общего .tmp.
        """
        global _render_done_at
        with _RENDER_LOCK:
            if time.monotonic() - _render_done_at < _RENDER_FRESH_SEC and OUT.is_file():
                return OUT.read_bytes()
            # рендеру нужен PATH единой двери (_TIDE_ENV): внутри он зовёт
            # `tide doctor --line`, и с голым /usr/bin:/bin строка здоровья
            # молча пропадала на КАЖДОЙ отдаче сервера
            try:
                r = subprocess.run(
                    [sys.executable, str(RENDER)],
                    env=dict(_TIDE_ENV, LIVE_OUT=str(OUT)),
                    capture_output=True, timeout=30,
                )
            except subprocess.TimeoutExpired:
                # НЕ УСПЕЛ — это тот же случай, что «упал»: прошлый билд лежит на
                # диске и вполне годен. Раньше таймаут летел наружу и человек
                # видел «доска не собралась» (кандидат 217): проекция берёт ~7 с
                # процессорного времени, и когда мак занят чем-то тяжёлым,
                # тридцати секунд СТЕНЫ ей не хватает — доска переставала
                # открываться, пока машина занята. Устаревшая страница со
                # штампом свежести в углу честнее пустого экрана; штамп времени
                # ставим тоже, иначе каждый полл заводил бы новый прогон поверх
                # занятого процессора и разгонял ту же беду
                print("board: ⚠ рендер не уложился в 30 с — отдаю прошлый билд",
                      file=sys.stderr)
                _render_done_at = time.monotonic()
                return OUT.read_bytes()
            if r.returncode != 0:
                # рендер упал → отдаём ПРОШЛЫЙ билд, но не молча (14.07: крэш
                # проекции маскировался устаревшей страницей — дебаг вслепую)
                print("board: ⚠ рендер упал (rc={0}): {1}".format(
                    r.returncode, (r.stderr or b"")[-300:].decode("utf-8", "ignore")),
                    file=sys.stderr)
            # штамп ставим и на упавшем прогоне: падающий рендерер не надо звать
            # по разу на каждый полл — окно свежести даёт ему передышку
            _render_done_at = time.monotonic()
            return OUT.read_bytes()

    def _resume(self):
        """⟳ возврат в сессию — тонкий адаптер над `tide return --json` (одна дверь).

        Pull-модель сохранена: запрос рождается ТОЛЬКО кликом человека в окне
        (localhost). Валидация жёсткая: sid — uuid-форма, dir — существующий
        каталог строго под ~/Documents/projects/. Вся механика (реестр, focus-проба
        живости — cand 101, respawn `claude --resume` под тем же sid со scoped-MCP)
        живёт в tide; доски-копия `_reg_*`/`_orca_create` умерла против этой двери.

        Гейт каталога — по РЕЕСТРУ (roster.md), как у ▶ spark и /take. Раньше
        тут стоял префикс одной папки, а проекты живут и вне неё — такой проект
        молча получал 400, и человек видел «жму, и ничего не происходит»
        (24.08). Реестр гейт только РАСШИРЯЕТ — старый префикс остался
        запасным, чтобы пропавший roster.md не
        закрыл дверь всем.
        """
        import json as _json
        import re as _re
        import urllib.parse as _up
        q = _up.parse_qs(_up.urlparse(self.path).query)
        sid = (q.get("sid") or [""])[0]
        d = (q.get("dir") or [""])[0]
        arc = (q.get("arc") or [""])[0]  # арка сессии — паспорт для гейта + legacy-ключ
        title = (q.get("title") or [""])[0]  # человеческое имя нити для таба Orca
        plain = (q.get("plain") or [""])[0] == "1"  # fetch-режим: вердикт текстом в тост
        force = (q.get("force") or [""])[0] == "1"  # рука человека: войти в растворённую
        ok_sid = bool(_re.fullmatch(r"[0-9a-fA-F-]{8,64}", sid))
        ok_dir = (Path(d).is_dir() and ".." not in d
                  and (d in self._roster_dirs() or self._under_roster(d)))
        if not (ok_sid and ok_dir):
            self.send_response(400); self.end_headers()
            self.wfile.write("resume: плохие параметры".encode()); return
        arc_ok = self._under_roster(arc) and "/.tide/arcs/" in arc
        tab = _re.sub(r"\s+", " ", title).strip()[:48]
        cmd = ["tide", "return", "--sid", sid, "--dir", d, "--json"]
        if arc_ok:
            cmd += ["--arc", arc]
        if tab:
            cmd += ["--title", tab]
        if force:
            # только из confirm-модалки доски (клик человека): перезайти в
            # растворённую — «мало ли что, достать из старой» (решение 14.07)
            cmd += ["--force"]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=45,
                               env=_TIDE_ENV)
            out = _json.loads(r.stdout or "{}") if r.returncode in (0, 1) else {}
        except Exception:
            out = {}
        action = out.get("action") or ""
        if action == "focused":
            msg = "переключил на сессию → смотри Orca"
        elif action == "resumed":
            msg = "сессия поднимается — смотри терминал Orca"
        elif action == "gone":
            # растворённая голова: её вкладка умерла, воскрешение запрещено (I6) —
            # вердикт ЧЕЛОВЕКУ, не глотать (решение 14.07: молча «не перешло»)
            msg = "сессия растворилась — нить у преемника (⟳ на его строке)"
        else:
            # деградация (нет верба/старый tide/сбой) — подсказка, не 500
            msg = ("не вышло поднять ({0}) — обнови tide (tide self-update) или "
                   "открой руками: cd {1} && claude --resume {2}").format(
                       (out.get("detail") or "tide return недоступен")[:120], d, sid)
        if action in ("focused", "resumed") and out.get("screen_locked"):
            # запертый мак не выводит вперёд НИ ОДНО приложение: вкладка переключена,
            # но окна человек не увидит. «Смотри Orca» тут — враньё, а по канону
            # вердикт не глотается и не врёт (решение 14.07). Замок видит tide (поле
            # screen_locked в его json), доска только доносит — своей копии механики
            # не держит. Особенно нужно, когда тычут с телефона по тайлнету: там мак
            # вообще вне поля зрения.
            msg = "вкладка переключена, но мак заперт — окно поднимется, как разблокируешь"
        if plain:
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(msg.encode())
            return
        return self._resume_page(msg)

    def _resume_page(self, msg):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(("<body style='background:#0b0f14;color:#a3b4c3;font:14px "
                          "ui-monospace,monospace;padding:40px'>{0}<br><br>"
                          "<a href='/' style='color:#4f93e0'>← к доске</a></body>"
                          .format(msg)).encode())

    def _tide(self, args, cwd=None, timeout=30):
        """Прогнать ``tide <args>`` через дверь-сабпроцесс → (ok, вывод). Тонкий
        адаптер: доска не несёт своей копии механики (правки паспортов/planов —
        только вербами tide)."""
        try:
            r = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                               cwd=cwd, env=_TIDE_ENV)
            return r.returncode == 0, (r.stdout + r.stderr).strip()
        except Exception as exc:
            return False, str(exc)

    # ── жест, ничего не изменивший, следа не оставляет (кандидат 164) ────────
    # Журнал паспорта читают ГЛАЗАМИ: по нему считают, сколько раз человек
    # трогал работу и когда. Строка «в избранных» на клике по уже избранному
    # (доска не успела обновиться, палец дрогнул, ручку дёрнули напрямую) врёт
    # про число жестов ровно так же, как врали четыре «закрыта» подряд 30.07.
    # Поэтому у каждого журналящего жеста первым делом — сверка с текущим
    # состоянием: совпало, значит менять нечего, отвечаем словом и уходим БЕЗ
    # записи. Это не ошибка: 200 и «уже так» — повторный жест идемпотентен.

    def _same(self, msg):
        """Ответить «менять нечего» — 200, без правки файла и без журнала."""
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(msg.encode())

    def _close(self):
        """✓ закрыть нить С ДОСКИ рукой — тонкий адаптер над ``tide arc close -f
        --retire-head [--result …]`` (итог, ретайр головы и печать — одним вербом
        в домене). Pull: клик = рука человека; валидация пути жёсткая."""
        import re as _re
        import urllib.parse as _up
        q = _up.parse_qs(_up.urlparse(self.path).query)
        d = (q.get("d") or [""])[0]
        result = (q.get("result") or [""])[0].strip()
        tdir = Path(d)
        ok = (self._under_roster(d) and "/.tide/arcs/" in d
              and tdir.is_dir() and not tdir.name.startswith("__"))
        if not ok:
            self.send_response(400); self.end_headers()
            self.wfile.write("close: плохой путь".encode()); return
        proot = tdir.parents[2]  # <proot>/.tide/arcs/<thread>
        slug = _re.sub(r"^\d+-@?", "", tdir.name)
        args = ["tide", "arc", "close", "-f", "--retire-head", slug]
        if result:
            args += ["--result", result]
        ok, out = self._tide(args, cwd=str(proot))
        self.send_response(200 if ok else 500)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write((("нить закрыта — трофеем на полку" if ok
                           else "закрыть не вышло: {0}".format(out))).encode())

    def _reopen(self):
        """⟲ вернуть закрытую нить в работу С ДОСКИ (симметрия закрытию; решение 14.07).
        Честный ``tide arc reopen`` (strip __…__ + status:active). Путь — закрытая (__)
        нить. Голова остаётся отпущенной — свежую поднимаешь ▶ (пустого пере-захода нет).
        """
        import re as _re
        import urllib.parse as _up
        q = _up.parse_qs(_up.urlparse(self.path).query)
        d = (q.get("d") or [""])[0]
        tdir = Path(d)
        ok = (self._under_roster(d) and "/.tide/arcs/" in d
              and tdir.is_dir() and tdir.name.startswith("__"))
        if not ok:
            self.send_response(400); self.end_headers()
            self.wfile.write("reopen: плохой путь".encode()); return
        slug = _re.sub(r"^\d+-@?", "", _re.sub(r"^__|__$", "", tdir.name))
        proot = tdir.parents[2]
        try:
            r = subprocess.run(
                ["tide", "arc", "reopen", slug],
                capture_output=True, text=True, timeout=30, cwd=str(proot),
                env={"PATH": "{0}/.local/bin:/opt/homebrew/bin:/usr/local/bin:"
                             "/usr/bin:/bin".format(Path.home()), "HOME": str(Path.home())})
            ok, out = r.returncode == 0, (r.stdout + r.stderr).strip()
        except Exception as exc:
            ok, out = False, str(exc)
        self.send_response(200 if ok else 500)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write((("нить вернулась в работу" if ok
                           else "reopen не вышло: {0}".format(out))).encode())

    def _fav(self):
        """★ добавить / убрать нить из избранных (решение 16.07): правит одну
        строку в контрол-хоуме (state/favorites) — единственном носителе списка.
        Порядок файла = порядок на столе, новая встаёт в конец; комментарии и
        чужие строки не трогаются. Несуществующую нить рендер и так молчит."""
        import urllib.parse as _up
        q = _up.parse_qs(_up.urlparse(self.path).query)
        proj = (q.get("proj") or [""])[0]
        tdir = (q.get("dir") or [""])[0]
        on = (q.get("on") or ["1"])[0] == "1"
        ok = (re.fullmatch(r"[A-Za-z0-9._-]{1,64}", proj)
              and re.fullmatch(r"[^|/\\\n\r]{1,128}", tdir))
        if not ok:
            self.send_response(400); self.end_headers()
            self.wfile.write("fav: плохие параметры".encode()); return
        f = CONTROL_HOME / ".tide" / "state" / "favorites"
        lines = f.read_text(encoding="utf-8").splitlines() if f.is_file() else []

        def _key(ln):
            if ln.strip().startswith("#") or "|" not in ln:
                return None
            parts = [p.strip() for p in ln.split("|")]
            return (parts[0], parts[1]) if len(parts) >= 2 else None

        kept = [ln for ln in lines if _key(ln) != (proj, tdir)]
        if on:
            kept.append("{0} | {1}".format(proj, tdir))
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("\n".join(kept) + "\n", encoding="utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(("★ в избранных" if on else "убрана из избранных").encode())

    def _dismiss(self):
        """✕ отпустить голову С ДОСКИ (pull: клик = рука человека; закон 12.07:
        смерть внимания — ТОЛЬКО руками, агент себя не хоронит).

        Пишет ``dismissed: <ts>`` в паспорт сессии — сессия остаётся в журнале
        визитов нити (⟳ живёт, структура не тронута), но головой в фокусе
        больше не считается. Идемпотентно. Валидация: путь строго под
        ~/Documents, внутри .tide/arcs, с паспортом arc.md.
        """
        import urllib.parse as _up
        q = _up.parse_qs(_up.urlparse(self.path).query)
        d = (q.get("d") or [""])[0]
        dp = Path(d)
        ok = (self._under_roster(d)
              and "/.tide/arcs/" in d and (dp / "arc.md").is_file())
        if not ok:
            self.send_response(400); self.end_headers()
            self.wfile.write("dismiss: плохой путь".encode()); return
        # Семантика в домене (tide arc dismiss): одна сессия; у ЗАКРЫТОЙ (__) нити —
        # освобождение всей головы-цепочки разом (решение 13.07).
        ok, out = self._tide(["tide", "arc", "dismiss", "--dir", d])
        many = "session(s)" in out and not out.strip().endswith("1 session(s)")
        self.send_response(200 if ok else 500)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        msg = (("нить отпущена — головы освобождены, ушла из фокуса" if many
                else "голова отпущена — след остался") if ok
               else "dismiss не вышло: {0}".format(out[:200]))
        self.wfile.write(msg.encode())

    def _drop_cand(self):
        """✕ выбросить кандидата С ДОСКИ (pull: клик = рука человека). Мягкое
        удаление по закону «ничего не тонет»: файл идеи ПЕРЕЕЗЖАЕТ в
        ``candidates/__dropped__/`` — уходит с полки И из ``tide candidate list``
        (оба читают только ``*.md`` верхнего уровня), но остаётся на диске и
        восстановим. Валидация — как в ``_spark_from_cand``: проект из ростера,
        ключ строгий, файл строго ВНУТРИ ``candidates/`` (без обхода пути)."""
        import re as _re
        import urllib.parse as _up
        q = _up.parse_qs(_up.urlparse(self.path).query)
        proj = (q.get("proj") or [""])[0]
        cand = (q.get("cand") or [""])[0]
        if not _re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z@._-]{0,79}", proj):
            self.send_response(400); self.end_headers()
            self.wfile.write("drop: плохой проект".encode()); return
        proot = self._roster_root(proj)
        if not proot:
            self.send_response(400); self.end_headers()
            self.wfile.write("drop: нет такого проекта".encode()); return
        if not (_re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z._-]{0,120}", cand)
                and ".." not in cand):
            self.send_response(400); self.end_headers()
            self.wfile.write("drop: плохой кандидат".encode()); return
        cdir = (Path(proot) / ".tide" / "arcs" / "candidates").resolve()
        cfile = (cdir / "{0}.md".format(cand)).resolve()
        if not (cfile.is_file() and cfile.parent == cdir):
            self.send_response(400); self.end_headers()
            self.wfile.write("drop: нет такого кандидата".encode()); return
        ok, err = self._tide(["tide", "candidate", "drop", cand], cwd=str(proot))
        self.send_response(200 if ok else 500)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write((("кандидат выброшен — в __dropped__, восстановим" if ok
                           else "выбросить не вышло: {0}".format(err[:200]))).encode())

    def _add_cand(self):
        """＋ добавить идею-кандидата прямо с полки (решение 13.07: вписать идею —
        ложится в бэклог проекта). Слаг — короткий транслит первых слов; тело —
        полный текст; ``from:`` пустой (=рукой). Через ``tide candidate add``."""
        import urllib.parse as _up
        q = _up.parse_qs(_up.urlparse(self.path).query)
        proj = (q.get("proj") or [""])[0]
        text = (q.get("text") or [""])[0].strip()
        if not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z@._-]{0,79}", proj):
            self.send_response(400); self.end_headers()
            self.wfile.write("add-cand: плохой проект".encode()); return
        proot = self._roster_root(proj)
        if not proot:
            self.send_response(400); self.end_headers()
            self.wfile.write("add-cand: нет такого проекта".encode()); return
        if not text:
            self.send_response(400); self.end_headers()
            self.wfile.write("add-cand: пустой кандидат".encode()); return
        text = text[:2000]
        # слаг = короткий хэндл из первых слов (не вся идея простынёй в имя ряда)
        slug = _slugify(" ".join(text.split()[:5])) or "idea"
        env = {"PATH": "{0}/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:"
                       "/bin".format(Path.home()), "HOME": str(Path.home())}
        try:
            r = subprocess.run(
                ["tide", "candidate", "add", slug, text],
                capture_output=True, text=True, timeout=30, cwd=proot, env=env)
            ok, out = r.returncode == 0, (r.stdout + r.stderr).strip()
        except Exception as exc:
            ok, out = False, str(exc)
        self.send_response(200 if ok else 500)
        self.send_header("Content-Type", "text/plain; charset=utf-8"); self.end_headers()
        self.wfile.write(("кандидат добавлен в бэклог" if ok
                          else "add-cand: не вышло — {0}".format(out)).encode())

    def _work_from_cand(self):
        """⚒ завести работу ИЗ ИДЕИ одним жестом (разрыв потока, 01.09).

        До этого у кандидата было три кнопки — скопировать, родить нить,
        выбросить, — и ни одной, которая превращает идею в работу. Двести идей
        лежали на полках без хода, хотя движок умеет это одним вербом:
        `tide work add <имя> --cand NN` кладёт текст идеи черновиком в `## план`
        и уводит саму идею с полки в `__dropped__`, откуда её можно вернуть.

        Имя работы берём ЗАГОЛОВКОМ кандидата, а не первой строкой его тела:
        заголовок человек уже написал руками, и это ровно то имя, которое он
        ждёт увидеть на карточке.
        """
        import urllib.parse as _up
        q = _up.parse_qs(_up.urlparse(self.path).query)
        proj = (q.get("proj") or [""])[0]
        cand = (q.get("cand") or [""])[0]
        if not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z@._-]{0,79}", proj):
            return self._say_plain(400, "работа: плохой проект")
        if not (re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z._-]{0,120}", cand)
                and ".." not in cand):
            return self._say_plain(400, "работа: плохой кандидат")
        proot = self._roster_root(proj)
        if not proot:
            return self._say_plain(400, "работа: нет такого проекта")
        f = Path(proot) / ".tide" / "arcs" / "candidates" / (cand + ".md")
        if not f.is_file():
            return self._say_plain(404, "работа: идея не нашлась")
        title = ""
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
                break
        # заголовка нет — берём слаг: имя всё равно правится кликом на карточке
        title = (title or re.sub(r"^\d+-", "", cand).replace("-", " "))[:200]
        # `--for` заполняет поле `project:` — без него паспорт рождается с пустым
        # домом. Доске это не мешает (работа в своём проекте адресуется местом,
        # см. _work_home), но пустая строка в паспорте — вранье глазам человека,
        # который открыл файл.
        ok, out = self._tide(["tide", "work", "add", title,
                              "--cand", cand, "--for", proj],
                             cwd=str(proot), timeout=30)
        return self._say_plain(
            200 if ok else 500,
            "работа заведена — идея ушла с полки" if ok
            else "не вышло завести: {0}".format(out[:200]))

    def _work_send(self):
        """⇥ отправить строителя на СОГЛАСОВАННУЮ работу (разрыв потока, 01.09).

        Доска давно умеет сказать «взята, строитель не отправлен — тихо два
        дня», но сделать с этим ничего не могла: отправка жила только внутри
        гейта согласования, и работа, у которой план согласовали словом в чате
        или которую завели формой прямо тут, лежала, пока про неё не вспомнят.

        Жест ровно тот же, что у «да» после согласования (`_work_go`): незанятую
        взять, потом увести в сессию нити с живой репликой. Плюс строка
        `tide work dispatch` — отправка обязана быть видна в журнале, иначе
        доска через час снова скажет «строитель не отправлен».

        Чекать пункты и закрывать работу этот жест НЕ умеет: done — рука
        человека, и кнопка «поехали» его гейта не трогает.
        """
        import urllib.parse as _up
        q = _up.parse_qs(_up.urlparse(self.path).query)
        f = self._work_file(q)
        if not f:
            return self._say_plain(404, "work: не нашёл")
        lines = f.read_text(encoding="utf-8").splitlines()
        st = self._work_status_of(lines)
        if st == "done":
            return self._same("работа закрыта — сначала открой её обратно")
        if self._work_props(lines):
            return self._same("сначала гейт: есть непринятые предложения")
        slug = f.parent.name
        root = self._work_root(f)
        thread = self._work_meta(lines, "thread")
        by = "нить " + re.sub(r"^\d+-@?", "", thread) if thread else "агент"
        self._tide(["tide", "work", "dispatch", slug, "--to", by],
                   cwd=str(root), timeout=30)
        msg = "строитель отправлен"
        msg += self._work_go(f, st)
        return self._say_plain(200, msg)

    def _say_plain(self, code, msg):
        """Ответ человеку одной строкой — общая дверь коротких вердиктов."""
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(msg.encode())

    def _note_add(self):
        """＋ заметка проекта с полки (решение 17.07): «заголовок | тело» одной
        строкой → файл <proj>/.tide/notes/NN-slug.md. Карточка-справка «быстро
        достать команду»; теги — строкой `tags: …` в теле."""
        import urllib.parse as _up
        q = _up.parse_qs(_up.urlparse(self.path).query)
        proj = (q.get("proj") or [""])[0]
        text = (q.get("text") or [""])[0].strip()
        if not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z@._-]{0,79}", proj):
            self.send_response(400); self.end_headers()
            self.wfile.write("note-add: плохой проект".encode()); return
        proot = self._roster_root(proj)
        if not proot:
            self.send_response(400); self.end_headers()
            self.wfile.write("note-add: нет такого проекта".encode()); return
        if not text:
            self.send_response(400); self.end_headers()
            self.wfile.write("note-add: пустая заметка".encode()); return
        title, _, body = text.partition("|")
        title, body = title.strip()[:120], body.strip()[:4000]
        d = Path(proot) / ".tide" / "notes"
        d.mkdir(parents=True, exist_ok=True)
        nn = 1 + max((int(m.group(1)) for f in d.glob("*.md")
                      if (m := re.match(r"(\d+)-", f.name))), default=0)
        slug = _slugify(" ".join(title.split()[:5])) or "note"
        stamp = "- {0} — создана (рука, с доски)".format(
            __import__("datetime").datetime.now().isoformat(timespec="minutes"))
        (d / "{0:02d}-{1}.md".format(nn, slug)).write_text(
            "# {0}\n\n{1}\n\n## журнал\n{2}\n".format(title or slug, body, stamp),
            encoding="utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write("заметка записана".encode())

    def _note_edit(self):
        """Правка заметки с полки (решение 17.07, как у работ): what=title —
        переписать первую строку `# …`; what=rest — переписать ВСЁ после
        заголовка (строка tags: + тело) сырым текстом из редактора."""
        import urllib.parse as _up
        q = _up.parse_qs(_up.urlparse(self.path).query)
        proj = (q.get("proj") or [""])[0]
        slug_ = (q.get("f") or [""])[0]
        what = (q.get("what") or [""])[0]
        text = (q.get("t") or [""])[0].strip()
        proot = self._roster_root(proj) if re.fullmatch(
            r"[0-9A-Za-z][0-9A-Za-z@._-]{0,79}", proj) else None
        ok_slug = re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z_-]{0,99}", slug_)
        f = (Path(proot) / ".tide" / "notes" / (slug_ + ".md")) if (proot and ok_slug) else None
        if not f or not f.is_file() or what not in ("title", "rest"):
            self.send_response(400); self.end_headers()
            self.wfile.write("note-edit: не нашёл заметку".encode()); return
        full = f.read_text(encoding="utf-8")
        # журнал (принцип №3: кто когда правил) живёт хвостом файла и ПЕРЕЖИВАЕТ
        # любую правку; каждый жест с доски — своя строка
        head, _, jraw = full.partition("## журнал")
        journal = jraw.strip()
        lines = head.splitlines()
        title = lines[0] if lines and lines[0].startswith("#") else "# " + f.stem
        was = "\n".join(lines[1:]).strip("\n")
        if what == "title":
            if not text:
                self.send_response(400); self.end_headers()
                self.wfile.write("note-edit: пустой заголовок".encode()); return
            body = was
            new_title = "# " + text[:200].lstrip("# ")
        else:
            body, new_title = text[:8000], title
        # редактор шлёт и нетронутый текст (открыл, закрыл) — такая правка не
        # правка, и «переписан» в журнале про неё врал бы
        if (new_title, body) == (title, was):
            return self._same("без изменений")
        stamp = "- {0} — {1} (рука, с доски)".format(
            __import__("datetime").datetime.now().isoformat(timespec="minutes"),
            "заголовок переписан" if what == "title" else "текст переписан")
        journal = (journal + "\n" if journal else "") + stamp
        f.write_text("{0}\n\n{1}\n\n## журнал\n{2}\n".format(
            new_title, body, journal), encoding="utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write("ok".encode())

    def _note_drop(self):
        """✕ мягкое удаление заметки (решение 17.07: «удалённые пускай будут,
        чтобы потом разобраться»): файл уезжает в notes/__dropped__/ со строкой
        в журнале — никакого hard delete."""
        import urllib.parse as _up
        q = _up.parse_qs(_up.urlparse(self.path).query)
        proj = (q.get("proj") or [""])[0]
        slug_ = (q.get("f") or [""])[0]
        proot = self._roster_root(proj) if re.fullmatch(
            r"[0-9A-Za-z][0-9A-Za-z@._-]{0,79}", proj) else None
        ok_slug = re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z_-]{0,99}", slug_)
        f = (Path(proot) / ".tide" / "notes" / (slug_ + ".md")) if (proot and ok_slug) else None
        if not f or not f.is_file():
            self.send_response(400); self.end_headers()
            self.wfile.write("note-drop: не нашёл заметку".encode()); return
        stamp = "- {0} — убрана (рука, с доски)".format(
            __import__("datetime").datetime.now().isoformat(timespec="minutes"))
        text = f.read_text(encoding="utf-8").rstrip("\n")
        if "## журнал" not in text:
            text += "\n\n## журнал"
        f.write_text(text + "\n" + stamp + "\n", encoding="utf-8")
        grave = f.parent / "__dropped__"
        grave.mkdir(exist_ok=True)
        dest = grave / f.name
        n = 2
        while dest.exists():
            dest = grave / "{0}-{1}.md".format(f.stem, n)
            n += 1
        f.rename(dest)
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write("заметка убрана в __dropped__ (вернуть можно рукой)".encode())

    def _news_add(self):
        """Форма вкладки «новости» (заготовка, решение 16.07): ссылка падает в
        очередь news/inbox.urls — русло; разбор в статью — конвейер следующего
        шага нити news-and-threads. Дубли в очереди не плодим."""
        import urllib.parse as _up
        q = _up.parse_qs(_up.urlparse(self.path).query)
        url = (q.get("u") or [""])[0].strip()[:2048]
        parts = _up.urlparse(url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            self.send_response(400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write("news: это не похоже на ссылку".encode()); return
        inbox = NEWS_ROOT / "inbox.urls"
        inbox.parent.mkdir(parents=True, exist_ok=True)
        known = (inbox.read_text(encoding="utf-8").splitlines()
                 if inbox.exists() else [])
        if url in known:
            msg = "уже в очереди"
        else:
            with inbox.open("a", encoding="utf-8") as f:
                f.write(url + "\n")
            msg = "в очереди на разбор"
            # фоном дотянуть «что за видео» (название/длительность) в
            # inbox.meta.json — очередь показывает не голый url (решение 16.07)
            subprocess.Popen(
                [sys.executable, str(inbox.parent / "process.py"), "enrich", url],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                env=_TIDE_ENV)
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(msg.encode())

    def _news_process(self):
        """Кнопка «разобрать» (решение 16.07): запустить конвейер
        news/process.py фоном на компе — очередь → транскрипт → статья.
        Стадии конвейер пишет в process.status.json, их показывает вкладка;
        от двойного запуска — process.lock (проверяет сам конвейер)."""
        import os as _os
        news = NEWS_ROOT
        lock = news / "process.lock"
        try:
            _os.kill(int(lock.read_text().strip()), 0)
            running = True
        except Exception:
            running = False
        if running:
            msg = "уже разбирается — стадия на вкладке"
        else:
            log = (news / "process.log").open("a", encoding="utf-8")
            subprocess.Popen(
                [sys.executable, str(news / "process.py")],
                stdout=log, stderr=log, env=_TIDE_ENV,
                start_new_session=True)
            msg = "разбор запущен — стадии появятся у очереди"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(msg.encode())

    def _news_sync(self):
        """Очередь инбокс-бота → вкладка (решение 16.07: «кинул в телегу —
        ожидаю увидеть в очереди на доске»): новые ссылки доливаются в
        inbox.urls, названия дотягивает enrich фоном. Ответ — число новых."""
        import json as _json
        import urllib.request as _ur
        news = NEWS_ROOT
        inbox = news / "inbox.urls"
        # Адрес инбокс-бота — приватный деплой конкретного человека, а не часть
        # стека: живёт в `TIDE_INBOX_URL` (окружение или instance.env). Не
        # назван — запрос НЕ СТРОИТСЯ вовсе: ходить некуда, и молчаливый стук в
        # чужой хост из коробки был бы хуже пустой очереди.
        # Токен лежит в связке ключей, и ИМЯ записи — тоже частность установки
        # (`TIDE_INBOX_TOKEN_KEY`), а не константа стека. Нет адреса или нет
        # имени ключа — очередь просто пуста.
        url = _conf("TIDE_INBOX_URL")
        key = _conf("TIDE_INBOX_TOKEN_KEY")
        items = []
        if url and key:
            try:
                br = subprocess.run(
                    ["security", "find-generic-password", "-s", key, "-w"],
                    capture_output=True, text=True, timeout=10).stdout.strip()
                req = _ur.Request(url, headers={"Authorization": "Bearer " + br})
                items = _json.load(_ur.urlopen(req, timeout=8)) if br else []
            except Exception:
                items = []
        known = (inbox.read_text(encoding="utf-8").splitlines()
                 if inbox.exists() else [])
        added = 0
        for it in items:
            u = (it.get("url") or "").strip()
            if u and u not in known:
                with inbox.open("a", encoding="utf-8") as f:
                    f.write(u + "\n")
                known.append(u)
                subprocess.Popen(
                    [sys.executable, str(news / "process.py"), "enrich", u],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    env=_TIDE_ENV)
                added += 1
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(str(added).encode())

    def _ext(self):
        """Внешняя ссылка с доски/читалки → системный `open` (решение 16.07):
        срабатывает тот же выбор браузера, что у ссылок из терминала, а
        вебвью борда не угоняется на чужую страницу без пути назад."""
        import urllib.parse as _up
        q = _up.parse_qs(_up.urlparse(self.path).query)
        url = (q.get("u") or [""])[0].strip()[:2048]
        parts = _up.urlparse(url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            self.send_response(400); self.end_headers()
            self.wfile.write("ext: это не ссылка".encode()); return
        try:
            subprocess.run(["open", url], timeout=10)
        except Exception:
            pass
        self.send_response(204); self.end_headers()

    # ── вкладка «работа»: работа = арка .tide/arcs/works/NN-slug/work.md ──

    def _work_add(self):
        """Завести работу с доски (решение 16.07): заголовок = первый пункт
        чеклиста, дедлайн опционален. Работа рождается аркой — папкой с
        паспортом; агент дальше согласует и дополняет чеклист.

        `proj` — дом работы (решение 30.07): форма живёт и на вкладке «работы»
        страницы нити, и заведённая оттуда работа должна сразу лечь в СВОЙ дом,
        иначе она не покажется в том самом списке, откуда её завели. С общей
        вкладки параметр пуст — там дом работе назначит агент. Имя дома идёт в
        тело паспорта, а не в путь: проверяем не от обхода каталогов, а чтобы
        перевод строки или двоеточие не разорвали шапку work.md.

        `thread` — ответственная нить тем же адресом, что пишет `tide work take`
        (голый слаг своего дома или «дом/NN-@нить»). Со страницы нити список сит
        по НИТИ (работа 24), и без этого поля новая работа исчезала бы из того
        самого списка, откуда её завели."""
        import urllib.parse as _up
        from datetime import date
        q = _up.parse_qs(_up.urlparse(self.path).query)
        text = (q.get("t") or [""])[0].strip()[:300]
        dl = (q.get("d") or [""])[0].strip()
        proj = (q.get("proj") or [""])[0].strip()
        thread = (q.get("thread") or [""])[0].strip()
        if not text:
            self.send_response(400); self.end_headers()
            self.wfile.write("work: пустая работа".encode()); return
        if dl and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", dl):
            self.send_response(400); self.end_headers()
            self.wfile.write("work: кривой дедлайн".encode()); return
        if proj and not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z._-]{0,60}", proj):
            self.send_response(400); self.end_headers()
            self.wfile.write("work: кривое имя проекта".encode()); return
        if thread and not re.fullmatch(r"[0-9A-Za-z@/._-]{1,120}", thread):
            self.send_response(400); self.end_headers()
            self.wfile.write("work: кривой адрес нити".encode()); return
        works = HERE.parent / ".tide" / "arcs" / "works"
        works.mkdir(parents=True, exist_ok=True)
        nums = [int(m.group(1)) for p in works.iterdir()
                if (m := re.match(r"(\d+)-", p.name))]
        slug = "{0:02d}-{1}".format(max(nums, default=0) + 1,
                                    _slugify(" ".join(text.split()[:5])) or "work")
        d = works / slug
        d.mkdir()
        (d / "work.md").write_text(
            "# {t}\n\nkind: work\nproject: {p}\nstatus: open\ncreated: {c}\n"
            "{th}{dl}\n## чеклист\n- [ ] {t}\n".format(
                t=text, p=proj, c=date.today().isoformat(),
                th="thread: {0}\n".format(thread) if thread else "",
                dl="deadline: {0}\n".format(dl) if dl else ""),
            encoding="utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write("работа заведена".encode())

    def _work_close(self):
        """Закрыть работу рукой человека (гейт по work-cycle.md): status → done
        + строка в ## журнал. Кнопка живёт на карточке в состоянии review."""
        import urllib.parse as _up
        from datetime import datetime
        q = _up.parse_qs(_up.urlparse(self.path).query)
        f = self._work_file(q)
        if not f:
            self.send_response(404); self.end_headers()
            self.wfile.write("work: не нашёл".encode()); return
        slug = f.parent.name
        text = f.read_text(encoding="utf-8")
        if not re.search(r"^status: ", text, flags=re.M):
            self.send_response(400); self.end_headers()
            self.wfile.write("work: паспорт без status".encode()); return
        if re.search(r"^status: done\s*$", text, flags=re.M):
            return self._same("работа уже закрыта")
        text = re.sub(r"^status: .*$", "status: done", text, count=1,
                      flags=re.M)
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        line = "- {0} — закрыта владельцем (кнопка на доске)".format(stamp)
        if re.search(r"^## журнал", text, flags=re.M):
            text = text.rstrip("\n") + "\n" + line + "\n"
        else:
            text = text.rstrip("\n") + "\n\n## журнал\n" + line + "\n"
        f.write_text(text, encoding="utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write("работа закрыта".encode())

    def _work_deadline(self):
        """Поставить/сменить/снять дедлайн кликом по чипу (решение 16.07):
        d=YYYY-MM-DD — ставит/меняет, пустой d — снимает. Жест — в журнал."""
        import urllib.parse as _up
        from datetime import datetime
        q = _up.parse_qs(_up.urlparse(self.path).query)
        f = self._work_file(q)
        d = (q.get("d") or [""])[0].strip()
        if not f:
            self.send_response(404); self.end_headers()
            self.wfile.write("work: не нашёл".encode()); return
        if d and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
            self.send_response(400); self.end_headers()
            self.wfile.write("work: кривой дедлайн".encode()); return
        text = f.read_text(encoding="utf-8")
        had = re.search(r"^deadline: (.*)$", text, flags=re.M)
        if (had.group(1).strip() if had else "") == d:
            return self._same("дедлайн уже {0}".format(d) if d
                              else "дедлайна и не было")
        if d and had:
            text = re.sub(r"^deadline: .*$", "deadline: " + d, text,
                          count=1, flags=re.M)
        elif d:
            text = re.sub(r"^created: .*$", "\\g<0>\ndeadline: " + d, text,
                          count=1, flags=re.M)
        elif had:
            text = re.sub(r"^deadline: .*\n?", "", text, count=1, flags=re.M)
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        line = ("- {0} — дедлайн → {1} (рука человека, доска)".format(stamp, d)
                if d else
                "- {0} — дедлайн снят (рука человека, доска)".format(stamp))
        if re.search(r"^## журнал", text, flags=re.M):
            text = text.rstrip("\n") + "\n" + line + "\n"
        else:
            text = text.rstrip("\n") + "\n\n## журнал\n" + line + "\n"
        f.write_text(text, encoding="utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(("дедлайн: " + (d or "снят")).encode())

    def _work_title(self):
        """Переписать заголовок работы кликом по тексту (решение 16.07)."""
        import urllib.parse as _up
        q = _up.parse_qs(_up.urlparse(self.path).query)
        f = self._work_file(q)
        text = (q.get("t") or [""])[0].strip()[:200]
        if not f or not text:
            self.send_response(400); self.end_headers()
            self.wfile.write("work: не нашёл / пустой заголовок".encode())
            return
        lines = f.read_text(encoding="utf-8").splitlines()
        for j, l in enumerate(lines):
            if l.startswith("# "):
                lines[j] = "# " + text
                f.write_text("\n".join(lines) + "\n", encoding="utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write("ok".encode()); return
        self.send_response(400); self.end_headers()
        self.wfile.write("work: паспорт без заголовка".encode())

    def _work_review_ready(self, lines):
        """Чеклист закрыт: все согласованные пункты чекнуты и ни одного
        предложения не висит. Тот же гейт, что у движка (work._review_ready):
        считаем обе секции — фикс держит приёмку наравне с шагом плана, а
        стоящее `- [?]` значит, что не закрыт сам разговор о плане."""
        seen = done = 0
        section = ""
        for l in lines:
            if l.startswith("## "):
                section = l[3:].strip(); continue
            if section not in ("чеклист", "фиксы"):
                continue
            m = re.match(r"- \[( |x|\?)\] ", l)
            if not m:
                continue
            if m.group(1) == "?":
                return False
            seen += 1
            done += m.group(1) == "x"
        return bool(seen) and seen == done

    def _work_reopen(self):
        """Открыть закрытую работу обратно (решение 16.07) — в тот живой статус,
        который ЧИТАЕТСЯ ИЗ ПАСПОРТА, а не всегда в open.

        Передумал закрывать — это про статус done, и только про него: исполнитель
        как числился в `taken-by`, так и числится, чеки как стояли, так и стоят.
        Штамповать «open» поверх всего этого — сочинять состояние, которого не
        бывает (кандидат 168: работа 31 висела open, за 04-pult и 1/1 сделано, и
        записка передачи звала следующую сессию делать сделанное). Поэтому:
        есть исполнитель и чеклист закрыт — review (работа готова, человек просто
        не дозакрыл); есть исполнитель, работа не доделана — taken; никто не
        брал — open."""
        import urllib.parse as _up
        q = _up.parse_qs(_up.urlparse(self.path).query)
        f = self._work_file(q)
        if not f:
            self.send_response(404); self.end_headers()
            self.wfile.write("work: не нашёл".encode()); return
        lines = f.read_text(encoding="utf-8").splitlines()
        st = self._work_status_of(lines)
        if not st:
            self.send_response(400); self.end_headers()
            self.wfile.write("work: паспорт без status".encode()); return
        if st != "done":
            return self._same("работа уже открыта")
        who = ""
        for l in lines:
            m = re.match(r"taken-by:\s*(\S.*)$", l)
            if m:
                who = m.group(1).strip(); break
        if not who:
            new, notes = "open", ["открыта заново (рука человека, доска)"]
        elif self._work_review_ready(lines):
            new = "review"
            notes = ["открыта заново → числится за {0} (рука человека, доска)".format(who),
                     "все пункты чекнуты → review, ждёт закрытия человеком"]
        else:
            new = "taken"
            notes = ["открыта заново → taken, числится за {0} "
                     "(рука человека, доска)".format(who)]
        for j, l in enumerate(lines):
            if l.startswith("status:"):
                lines[j] = "status: " + new; break
        self._work_journal(f, lines, *notes)
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write("открыта заново ({0})".format(new).encode())

    def _work_desc(self):
        """Переписать описание (свободный текст между паспортом и чеклистом)
        рукой человека с доски; пустой t — снести описание."""
        import urllib.parse as _up
        q = _up.parse_qs(_up.urlparse(self.path).query)
        f = self._work_file(q)
        text = (q.get("t") or [""])[0].strip()[:2000]
        if not f:
            self.send_response(404); self.end_headers()
            self.wfile.write("work: не нашёл".encode()); return
        lines = f.read_text(encoding="utf-8").splitlines()
        try:
            cut = next(i for i, l in enumerate(lines) if l.startswith("## "))
        except StopIteration:
            cut = len(lines)
        # правка описания ПЕРЕСОБИРАЕТ шапку — поля, которых нет в этом списке,
        # она молча съедает. thread: и fav: сюда дописаны вместе со звездой
        # (фикс 7 работы 21): без них правка описания снимала бы работе и
        # ведущую нить, и избранность
        head = [l for l in lines[:cut]
                if l.startswith("# ") or re.match(
                    r"(kind|project|deadline|status|created"
                    r"|taken-by|taken-at|thread|fav):\s*", l)]
        title, meta = head[0:1], head[1:]
        out = title + [""] + meta + [""]
        if text:
            out += [text, ""]
        out += lines[cut:]
        f.write_text("\n".join(out).rstrip("\n") + "\n", encoding="utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write("описание обновлено".encode())

    def _work_fav(self):
        """★ на работе (решение 30.07, фикс 7 работы 21): строка `fav: yes` в
        шапке паспорта — сразу после status, тем же руслом, что у артефакта
        (`_artifact_fav`). on=0 строку убирает. Отсутствие строки и есть «не
        избранная», старые паспорта живут как жили. Доска ставит избранные
        первыми — и на общей вкладке, и в нити.

        Жест пишет строку в ## журнал, как все руки человека: в tide ничего не
        тонет молча. Замок от даблклика стоит на кнопке (dataset.busy), но и
        сюда повторный клик приходит безобидным — «уже в избранных» отвечаем
        без второй записи в журнал."""
        import urllib.parse as _up
        from datetime import datetime
        q = _up.parse_qs(_up.urlparse(self.path).query)
        f = self._work_file(q)
        on = (q.get("on") or ["1"])[0] == "1"
        if not f:
            self.send_response(404); self.end_headers()
            self.wfile.write("work: не нашёл".encode()); return
        text = f.read_text(encoding="utf-8")
        if not re.search(r"^status: ", text, flags=re.M):
            self.send_response(400); self.end_headers()
            self.wfile.write("work: паспорт без status".encode()); return
        had = re.search(r"^fav: (.*)$", text, flags=re.M)
        is_on = bool(had) and had.group(1).strip().lower() in ("yes", "true", "1")
        if is_on == on:  # доска могла не успеть обновиться — молча соглашаемся
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(("★ уже в избранных" if on
                              else "уже не в избранных").encode()); return
        if on and had:
            text = re.sub(r"^fav: .*$", "fav: yes", text, count=1, flags=re.M)
        elif on:
            text = re.sub(r"^status: .*$", "\\g<0>\nfav: yes", text, count=1,
                          flags=re.M)
        else:
            text = re.sub(r"^fav: .*\n?", "", text, count=1, flags=re.M)
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        line = "- {0} — {1} (рука человека, доска)".format(
            stamp, "в избранных" if on else "убрана из избранных")
        if re.search(r"^## журнал", text, flags=re.M):
            text = text.rstrip("\n") + "\n" + line + "\n"
        else:
            text = text.rstrip("\n") + "\n\n## журнал\n" + line + "\n"
        f.write_text(text, encoding="utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(("★ в избранных" if on
                          else "убрана из избранных").encode())

    def _work_file(self, q):
        """Валидный work.md по ?f=<ключ> или None (общая дверь ручек работ).

        Ключ — голый слаг у работы из общей папки (как было всегда) либо
        «дом/слаг» у работы, лежащей в своём проекте. Дом резолвим ТЕМИ ЖЕ
        источниками, которыми доска её нарисовала (`works_sources`): разъедься
        эти два списка, кнопка правила бы не тот файл, который человек видит.
        """
        key = (q.get("f") or [""])[0]
        home, _, slug = key.rpartition("/")
        if not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z._-]{0,80}", slug):
            return None
        if home and not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z@._-]{0,79}", home):
            return None
        for hint, d in works_sources():
            if (hint or "") != home:
                continue
            f = Path(d) / slug / "work.md"
            if f.is_file():
                return f
        return None

    @staticmethod
    def _work_root(f):
        """Корень проекта, которому принадлежит work.md *f*.

        Вербы `tide work …` зовут ВНУТРИ проекта работы: путь у них
        `<корень>/.tide/arcs/works/<слаг>/work.md`, отсюда и четыре шага вверх.
        Пока все работы лежали в одной папке, корнем всегда был дом-верфь и его
        зашивали константой; теперь работа может лежать в соседнем проекте, и верб,
        запущенный из верфи, её просто не нашёл бы.
        """
        return f.parents[4]

    def _work_item_add(self):
        """Дописать пункт чеклиста рукой человека с доски (слово 16.07):
        строка `- [ ] <текст>` в конец секции ## чеклист.

        РАБОТА НА ПРИЁМКЕ — другой случай (фикс 6 работы 22): то, что человек
        дописал, глядя на сделанное, это не тихий пункт плана, а ФИКС. Ложится
        в `## фиксы` тем же руслом, что и верб `tide work fix`, и возвращает
        работу в taken — на столе снова работа, а не вердикт. Иначе накидка у
        гейта пряталась бы среди плановых шагов, а работа так и висела бы
        «готова к закрытию» с невыполненным пунктом внутри."""
        import urllib.parse as _up
        q = _up.parse_qs(_up.urlparse(self.path).query)
        f = self._work_file(q)
        text = (q.get("t") or [""])[0].strip()[:300]
        if not f or not text:
            self.send_response(400); self.end_headers()
            self.wfile.write("work: не нашёл / пустой пункт".encode()); return
        lines = f.read_text(encoding="utf-8").splitlines()
        if self._work_status_of(lines) == "review":
            return self._work_fix_add(f, lines, text)
        try:
            end = next(i for i, l in enumerate(lines)
                       if l.startswith("## чеклист"))
        except StopIteration:
            self.send_response(400); self.end_headers()
            self.wfile.write("work: паспорт без чеклиста".encode()); return
        for j in range(end + 1, len(lines)):
            if lines[j].startswith("## "):
                break
            if lines[j].strip():
                end = j
        lines.insert(end + 1, "- [ ] " + text)
        f.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write("пункт добавлен".encode())

    def _work_status_of(self, lines):
        """`status:` из шапки паспорта работы («» — поля нет)."""
        for l in lines:
            m = re.match(r"status:\s*(\S*)", l)
            if m:
                return m.group(1)
        return ""

    def _work_section(self, lines, title):
        """(строка заголовка, строка ЗА последней строкой секции) или None."""
        head = None
        for j, l in enumerate(lines):
            if not l.startswith("## "):
                continue
            if head is not None:
                return head, j
            if l[3:].strip() == title:
                head = j
        return (head, len(lines)) if head is not None else None

    def _work_count_items(self, lines):
        """Сколько пунктов работы лежит в этих строках. Считаем обе секции —
        нумерация на доске сквозная через чеклист и фиксы, и номер нового
        фикса продолжает её (тот же счёт, что у `_work_item_line`)."""
        n, section = 0, ""
        for l in lines:
            if l.startswith("## "):
                section = l[3:].strip(); continue
            if section in ("чеклист", "фиксы") and re.match(r"- \[( |x|\?)\] ", l):
                n += 1
        return n

    def _work_fix_add(self, f, lines, text):
        """Накидка у приёмки → пункт в `## фиксы`, работа обратно в taken.

        Пункт ложится сразу согласованным (`- [ ]`, не `- [?]`): его написала
        рука человека, и просить у него «да» на своё же слово незачем. Секция
        родится, если её ещё нет, — строго между чеклистом и журналом: номера
        фиксов продолжают чеклист, а журнал по контракту work.md остаётся
        последним."""
        found = self._work_section(lines, "фиксы")
        if found:
            head, end = found
            at = end
            while at > head + 1 and not lines[at - 1].strip():
                at -= 1  # за последним фиксом, до пустого хвоста секции
            body = ["- [ ] " + text]
        else:
            found = self._work_section(lines, "чеклист")
            if found:
                at = found[1]
            else:
                jr = self._work_section(lines, "журнал")
                at = jr[0] if jr else len(lines)
            gap = [""] if at and lines[at - 1].strip() else []
            body = gap + ["## фиксы", "- [ ] " + text, ""]
        n = self._work_count_items(lines[:at]) + 1
        lines = lines[:at] + body + lines[at:]
        for j, l in enumerate(lines):
            if l.startswith("status:"):
                lines[j] = "status: taken"; break
        self._work_journal(
            f, lines,
            "фикс {0} добавлен рукой с доски".format(n),
            "фикс вернул в работу (review → taken)")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(
            "фикс {0} добавлен · работа вернулась в работу".format(n).encode())

    # ── сделанное неприкосновенно (решение 30.07, фикс 5 работы 22) ───────────
    # Пункт с галочкой — уже история: на него сослался журнал («пункт N ✓ …»
    # с пруфом), по нему могла пройти приёмка («пункт N принят рукой»), и по
    # нему считается прогресс. Правка текста сделала бы пруф в журнале ложью,
    # удаление сдвинуло бы нумерацию — все прошлые строки журнала стали бы
    # указывать не на те пункты. Поэтому запрет НЕ зависит от статуса работы:
    # он про сам пункт. Откатить «сделано» может только исполнитель, вербом
    # `tide work uncheck --reason`, — и тогда пункт снова правится.
    DONE_LOCK = "work: сделанное — история, {0} только верб исполнителя"

    def _work_item_locked(self, state, verb):
        """Сделанный пункт руками с доски не трогаем — 400 и словами почему.
        Рендер прячет крестик и правку, но ручку зовут и напрямую: интерфейс
        обойти можно, сервер — нет."""
        if state != "x":
            return False
        self.send_response(400); self.end_headers()
        self.wfile.write(self.DONE_LOCK.format(verb).encode())
        return True

    def _work_item_edit(self):
        """Править текст пункта i рукой человека: галочка сохраняется."""
        import urllib.parse as _up
        q = _up.parse_qs(_up.urlparse(self.path).query)
        f = self._work_file(q)
        idx = (q.get("i") or [""])[0]
        text = (q.get("t") or [""])[0].strip()[:300]
        if not f or not idx.isdigit() or not text:
            self.send_response(400); self.end_headers()
            self.wfile.write("work: не нашёл / пустой пункт".encode()); return
        lines = f.read_text(encoding="utf-8").splitlines()
        hit = self._work_item_line(lines, int(idx))
        if hit is None:
            self.send_response(404); self.end_headers()
            self.wfile.write("work: нет такого пункта".encode()); return
        j, state = hit
        if self._work_item_locked(state, "перепишет"):
            return
        lines[j] = lines[j][:6] + text
        f.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write("ok".encode())

    def _work_item_del(self):
        """Удалить пункт i рукой человека с доски."""
        import urllib.parse as _up
        q = _up.parse_qs(_up.urlparse(self.path).query)
        f = self._work_file(q)
        idx = (q.get("i") or [""])[0]
        if not f or not idx.isdigit():
            self.send_response(400); self.end_headers()
            self.wfile.write("work: не нашёл".encode()); return
        lines = f.read_text(encoding="utf-8").splitlines()
        hit = self._work_item_line(lines, int(idx))
        if hit is None:
            self.send_response(404); self.end_headers()
            self.wfile.write("work: нет такого пункта".encode()); return
        if self._work_item_locked(hit[1], "снимет"):
            return
        del lines[hit[0]:self._work_item_end(lines, hit[0])]
        f.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write("пункт удалён".encode())

    def _work_item_end(self, lines, j):
        """Где кончается пункт, начавшийся строкой j: сама `- [ ] тайтл` плюс
        описание — строки под ней с отступом в два пробела (контракт work.md).
        Удаление уносит пункт ЦЕЛИКОМ: осиротевшее описание прилипло бы к
        соседнему пункту и поехало бы вместе с ним."""
        end = j + 1
        while (end < len(lines) and lines[end].startswith("  ")
               and lines[end].strip()):
            end += 1
        return end

    def _work_item_line(self, lines, i):
        """Строка i-го пункта работы: (номер_строки, состояние) или None.
        Ищем СТРОГО внутри `## чеклист` и `## фиксы` — план тоже свободный
        текст и вполне может нести свои «- [ ] …», а нумерация на доске идёт
        по пунктам (решение 30.07). Нумерация СКВОЗНАЯ через обе секции: доска
        рисует их одним списком и шлёт сюда его индекс, а `## фиксы` по
        контракту work.md стоит сразу за чеклистом — значит порядок строк в
        файле и есть порядок на доске (работа 19). Читать одну секцию нельзя:
        чек по фиксу свалился бы в чужой пункт или в 404.
        Состояние: «x» · « » · «?» (предложен агентом)."""
        n, section = -1, ""
        for j, l in enumerate(lines):
            if l.startswith("## "):
                section = l[3:].strip(); continue
            if section not in ("чеклист", "фиксы"):
                continue
            m = re.match(r"- \[( |x|\?)\] ", l)
            if not m:
                continue
            n += 1
            if n == i:
                return j, m.group(1)
        return None

    def _work_check(self):
        """Тоггл пункта чеклиста работы: переписывает `- [ ]`↔`- [x]` строки i."""
        import urllib.parse as _up
        q = _up.parse_qs(_up.urlparse(self.path).query)
        idx = (q.get("i") or [""])[0]
        f = self._work_file(q)
        if not f or not idx.isdigit():
            self.send_response(404); self.end_headers()
            self.wfile.write("work: не нашёл".encode()); return
        i = int(idx)
        lines = f.read_text(encoding="utf-8").splitlines()
        hit = self._work_item_line(lines, i)
        if hit is None:
            self.send_response(404); self.end_headers()
            self.wfile.write("work: нет такого пункта".encode()); return
        j, state = hit
        if state == "?":
            # предложенный агентом пункт чекать нечем: сперва «да» человека
            self.send_response(400); self.end_headers()
            self.wfile.write(
                "work: пункт ещё не согласован — сначала «да»".encode()); return
        was_done = state == "x"
        lines[j] = ("- [ ] " if was_done else "- [x] ") + lines[j][6:]
        # ченджлог работы (решение 16.07): и жест руки — строка в ## журнал,
        # чтобы видно было, кто когда что сделал
        self._work_journal(f, lines, "пункт {0} {1} (рука человека, доска)".format(
            i + 1, "расчекнут" if was_done else "чекнут"))
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write("ok".encode())

    def _work_journal(self, f, lines, *notes):
        """Записать паспорт работы и дописать *notes* строками в ## журнал
        (секция заводится, если её ещё нет) — общая дверь жестов доски,
        меняющих чеклист: ничего не тонет молча. Нот бывает несколько: один
        жест несёт два факта (фикс лёг И вернул работу в taken) — и читаться
        они должны отдельными строками, как их пишет верб."""
        from datetime import datetime
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        text = "\n".join(lines) + "\n"
        block = "".join("- {0} — {1}\n".format(stamp, n) for n in notes)
        if re.search(r"^## журнал", text, flags=re.M):
            text = text.rstrip("\n") + "\n" + block
        else:
            text = text.rstrip("\n") + "\n\n## журнал\n" + block
        f.write_text(text, encoding="utf-8")

    # ── ГЕЙТ СОГЛАСОВАНИЯ ПЛАНА: «да» кнопкой (решение 07.08, работа 44) ──────
    # Гейт висел словом в чат: человек видел карточку на доске, шёл в терминал,
    # диктовал «да», агент звал `tide work agree`, потом `take`, потом строил.
    # Три перехода на ровном месте — а смысл tide в том, чтобы работа была
    # легче, а не чтобы человек носил слова между окнами. Теперь это ОДНА
    # кнопка, и она делает весь гейт целиком: согласовать → взять → увести к
    # агенту (пункты 7 и 8 работы 44).
    #
    # ПРО СЛОВО. Движок требует `--word` не из вредности: слово — подпись
    # человека, оно ложится в журнал, и по нему потом читают, чем гейт закрыт.
    # Кнопка — тоже слово, но врать им нельзя: подставить голое «да» так, будто
    # человек произнёс его голосом, значит подделать подпись. Поэтому слово
    # честно называет свой канал — в журнале читается «предложения подтверждены
    # словом: „да — кнопкой с доски"». Видно и ЧТО согласовано, и ЧЕМ.
    #
    # ПРО ФАЙЛ. Ручка не правит work.md сама (тут раньше жили `_work_item_yes`
    # и `_work_item_yes_all`, писавшие `- [?]` → `- [ ]` руками мимо вербов и
    # ни к чему на доске не подключённые). Согласование двигает не только
    # символ в строке: журнал, статус, нить, курсор, сдвиг ссылок. Копия этой
    # механики на доске разъехалась бы с движком молча — доска зовёт `tide work
    # agree`, и это единственный путь.
    GATE_YES = "да — кнопкой с доски"
    GATE_NO = "нет — кнопкой с доски"

    def _work_props(self, lines):
        """Номера ПРЕДЛОЖЕННЫХ пунктов работы (1-based, как их зовут вербы)."""
        out, i = [], 0
        while True:
            hit = self._work_item_line(lines, i)
            if hit is None:
                return out
            if hit[1] == "?":
                out.append(i + 1)
            i += 1

    def _work_meta(self, lines, key):
        """Поле шапки паспорта работы («» — поля нет)."""
        for l in lines:
            if l.startswith("## "):
                break
            m = re.match(re.escape(key) + r":\s*(.*)", l)
            if m:
                return m.group(1).strip()
        return ""

    def _work_agree(self):
        """«да» человека на гейте согласования — одной кнопкой с ЛЮБОЙ площадки.

        Один адрес на обе двери (карточка работы и стол issues): разъедься они,
        гейт вёл бы себя по-разному в двух местах, а это один и тот же гейт.
        Порядок жестов — `tide work agree` → снять со стола вопросы этой работы
        → `tide work take` → увести человека к агенту (см. `_work_go`).
        Чекать пункты и закрывать работу кнопка НЕ умеет: `done` ставит только
        рука человека у приёмки — это канон, и гейт согласования его не трогает.
        """
        import urllib.parse as _up
        q = _up.parse_qs(_up.urlparse(self.path).query)
        f = self._work_file(q)
        if not f:
            self.send_response(400); self.end_headers()
            self.wfile.write("work: не нашёл".encode()); return
        slug = f.parent.name
        lines = f.read_text(encoding="utf-8").splitlines()
        props = self._work_props(lines)
        if not props:
            # кнопка рисуется только при висящих предложениях, но ручку зовут и
            # напрямую (и доска могла отстать на своп) — 200 и «менять нечего»:
            # жест, ничего не изменивший, следа в журнале не оставляет
            return self._same("согласовывать нечего — предложений нет")
        st = self._work_status_of(lines)
        ok, out = self._tide(["tide", "work", "agree", slug,
                              "--word", self.GATE_YES],
                             cwd=str(self._work_root(f)), timeout=30)
        if not ok:
            self.send_response(500)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(("не согласовалось: " + out[:200]).encode())
            return
        msg = "согласовано · {0}".format(len(props))
        msg += self._gate_asks_taken(slug)
        msg += self._work_go(f, st)
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(msg.encode())

    def _work_prop_drop(self):
        """«нет» отдельному предложению — путь отказа рядом с «да».

        Согласиться со всем одной кнопкой легко, а «эти два убери» до сих пор
        делалось только словом в чат. Верб тот же (`tide work agree --drop N`),
        слово — тоже кнопочное, и оно тоже ложится в журнал: снятое предложение
        должно быть видно, иначе план молча меняется задним числом.
        Согласованный пункт этой ручкой не снять — у него свой верб (`tide work
        drop … --word`) и своя цена: на него уже сослался журнал."""
        import urllib.parse as _up
        q = _up.parse_qs(_up.urlparse(self.path).query)
        f = self._work_file(q)
        idx = (q.get("i") or [""])[0]
        if not f or not idx.isdigit():
            self.send_response(400); self.end_headers()
            self.wfile.write("work: не нашёл".encode()); return
        lines = f.read_text(encoding="utf-8").splitlines()
        hit = self._work_item_line(lines, int(idx))
        if hit is None:
            self.send_response(404); self.end_headers()
            self.wfile.write("work: нет такого пункта".encode()); return
        if hit[1] != "?":
            self.send_response(400); self.end_headers()
            self.wfile.write(("этот пункт уже согласован — снимает его "
                              "исполнитель вербом со словом").encode())
            return
        ok, out = self._tide(["tide", "work", "agree", f.parent.name,
                              "--drop", str(int(idx) + 1),
                              "--word", self.GATE_NO],
                             cwd=str(self._work_root(f)), timeout=30)
        self.send_response(200 if ok else 500)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(("предложение снято" if ok
                          else "не снялось: " + out[:200]).encode())

    def _gate_asks_taken(self, work_slug):
        """Убрать со стола ВОПРОСЫ этой работы — тем же словом, что и гейт.

        Вторая площадка гейта — стол: агент кладёт туда `tide artifact add
        --ask … --work NN`. Согласовать план и оставить вопрос висеть значит
        спросить дважды об одном. Снимаем вербом (`tide artifact taken --word`),
        и только `status: new` + `kind: question` + свой номер работы — чужую
        подачу гейт не трогает. Отдаёт приписку к вердикту (или «»)."""
        num = re.match(r"(\d+)", work_slug)
        if not num:
            return ""
        adir = HERE.parent / ".tide" / "arcs" / "artifacts"
        hit = 0
        for af in (sorted(adir.glob("*/artifact.md")) if adir.is_dir() else []):
            try:
                head = af.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            head = head.split("\n## ", 1)[0]
            if not re.search(r"^kind:\s*question\s*$", head, re.M):
                continue
            if not re.search(r"^status:\s*new\s*$", head, re.M):
                continue
            m = re.search(r"^work:\s*(\d+)\s*$", head, re.M)
            if not m or int(m.group(1)) != int(num.group(1)):
                continue
            ok, _out = self._tide(["tide", "artifact", "taken", af.parent.name,
                                   "--word", self.GATE_YES],
                                  cwd=str(HERE.parent), timeout=30)
            hit += 1 if ok else 0
        return " · вопрос убран со стола" if hit else ""

    def _work_go(self, f, st_before):
        """ПОСЛЕ «ДА» РАБОТА ИДЁТ САМА (пункт 8 работы 44): взять и увести.

        Апрув не просто метит план согласованным — он запускает работу. Взять
        может только незанятую (`open`): у взятой уже есть исполнитель, и второй
        `take` соврал бы полю `taken-by`. Дальше — дорога к агенту, см.
        `_work_route`. Отдаёт приписку к вердикту кнопки."""
        slug = f.parent.name
        lines = f.read_text(encoding="utf-8").splitlines()
        thread = self._work_meta(lines, "thread")
        tail = ""
        took = False
        if st_before == "open":
            by = "нить " + re.sub(r"^\d+-@?", "", thread) if thread else "агент"
            ok, out = self._tide(["tide", "work", "take", slug,
                                  "--by", by, "--word", self.GATE_YES],
                                 cwd=str(self._work_root(f)), timeout=30)
            tail = " · взята" if ok else " · не взялась ({0})".format(out[:80])
            if not ok:
                return tail
            took = True
        return tail + self._work_route(thread, self._work_say(f, took),
                                       self._work_root(f))

    def _work_say(self, f, took):
        """Что кнопка ГОВОРИТ агенту после «да» — одной живой фразой.

        Апрув запускает работу, а запустить её можно только словами: агенту надо
        сказать, какую работу взяли и что вести её до приёмки. Служебный код
        («work-agree 44», json) сюда не годится — на том конце читает такой же
        собеседник, как человек, и понимает он то же самое, что понял бы человек.

        Одна строка без переносов: реплика уезжает в промпт живой сессии, а
        перевод строки отправил бы её на полпути (см. `tide return --say`).
        Про приёмку в конце — не вежливость, а граница: `done` ставит только
        рука человека, и агент должен знать это с первого хода, а не узнать на
        попытке закрыть работу самому."""
        slug = f.parent.name
        lines = f.read_text(encoding="utf-8").splitlines()
        title = next((l[2:].strip() for l in lines if l.startswith("# ")), slug)
        if len(title) > 90:
            title = title[:87].rstrip() + "…"
        num = re.match(r"(\d+)", slug)
        num = num.group(1) if num else slug
        return (
            "Человек нажал «да» на доске: план работы {0} «{1}» согласован{2}. "
            "Веди её по согласованным пунктам — посмотри tide work show {3}, "
            "иди по чеклисту сверху вниз, каждый пункт закрывай только с пруфом "
            "(tide work check). План заново не строй и своего в него не "
            "дописывай. Сделаешь все пункты — доведи работу до review и позови "
            "владельца на приёмку: done ставит только его рука."
        ).format(num, title, " и она взята на тебя" if took else "", slug)

    def _work_route(self, thread, say="", root=None):
        """Куда уводит «да»: в ЖИВУЮ сессию нити — или в новую, если её нет.

        Развилка тут одна и она канонная: у работы есть ведущая нить, у нити —
        живая сессия. Жива — надо ВЕРНУТЬСЯ в неё; две работающие разом по одной
        нити — тревога по принципу №1 («хендофф не ломается»), и плодить их
        кнопкой было бы прямым вредом. `tide return` дубля родить не может: он
        фокусит записанную вкладку по sid, а не найдя её — поднимает
        `claude --resume <sid>`, ту же самую сессию под тем же id.
        Живой сессии нет — `tide spark <проект> --thread <нить>`: tide создаёт
        арку, пинит id, регистрирует и спавнит (та же дверь, что у ▶ на доске).

        Ведущую сессию ищем ТЕМ ЖЕ резолвом, которым доска рисует ↗ на карточке
        работы (`live_projection._head_session`) — иначе кнопка уводила бы не
        туда, куда показывает стрелка рядом с ней.

        *say* — реплика агенту (`_work_say`), и она едет ОБЕИМИ ветками: живой
        сессии её отдаёт `tide return --say` (набирает в её терминал и жмёт
        Enter), поднятой — `tide spark --say` первым ходом. Без неё «да» человека
        доезжало до агента только случайно, с его следующего хода, по журналу
        работы: окно поднялось, а зачем — сессия не знала. Ход даётся ровно на
        жест человека: сама доска сессии не будит.

        *root* — проект САМОЙ РАБОТЫ. Раньше тут стоял дом-верфь константой, и
        «да» на работе, чья нить живёт в соседнем проекте, отвечало «нить не в
        этом проекте — открой сессию сам»: нить искали не в том доме. На живых
        работах это ломалось у трёх из них, и все три смотрели в нити соседнего проекта —
        то есть ровно в тот проект, в котором владелец работает каждый день.
        Адрес вида «дом/нить» по-прежнему сильнее: если работа явно назвала
        чужой дом, идём туда, а не в тот, где лежит сама."""
        if not thread:
            return " · нить не указана — открой сессию сам"
        import json as _json
        import sys as _sys
        if str(HERE) not in _sys.path:
            _sys.path.insert(0, str(HERE))
        proot = Path(root) if root else HERE.parent
        home, _, bare = thread.strip().rpartition("/")
        if home:
            named = self._roster_root(home)
            if named:
                proot, thread = Path(named), bare
        try:
            import live_projection as _lp
            by_dir = {t["dir"]: t for t in _lp.read_threads(proot.name, proot)}
        except Exception as exc:  # рендер жив и без этого — вердикт не глотаем
            return " · нить не прочиталась ({0})".format(str(exc)[:60])
        t = by_dir.get(thread) or by_dir.get(thread.rsplit("/", 1)[-1])
        if t is None:
            return " · нить {0} не в этом проекте — открой сессию сам".format(
                thread)
        head = _lp._head_session(t)
        if head and head.get("claude"):
            args = ["tide", "return", "--sid", head["claude"], "--dir", str(proot),
                    "--arc", str(t["path"] / "arcs" / head["dir"]), "--json"]
            if say:
                args += ["--say", say]
            ok, out = self._tide(args, cwd=str(proot), timeout=45)
            # json ищем строкой, а не splitlines()[0]: рядом может лечь
            # предупреждение самого tide, и первая строка тогда не json
            act, said = "", None
            for ln in out.splitlines():
                if ln.startswith("{"):
                    try:
                        v = _json.loads(ln) or {}
                    except ValueError:
                        v = {}
                    act, said = v.get("action") or "", v.get("said")
                    break
            # Реплика не доехала — так и говорим. Обещать «агент услышал», когда
            # ход в сессию не попал, хуже, чем честная дыра: человек уйдёт от
            # доски уверенным, что работа пошла, а она стоит.
            miss = "" if said is not False else " (реплика не дошла — скажи сам)"
            if act == "focused":
                return " · агент ведёт её — переключил на сессию" + miss
            if act == "resumed":
                return " · агент ведёт её — сессия поднимается" + miss
            if act == "gone":
                return " · сессия нити растворилась — подними её с карточки нити"
            return " · работа у нити, но в сессию не перешло ({0})".format(
                out[:80])
        args = ["tide", "spark", proot.name, "--thread",
                re.sub(r"^\d+-@?", "", t["dir"])]
        if say:
            # Свежая сессия ещё и стартует: сначала старт-гейт, потом работа —
            # иначе первый пульс нити никто не поставит, и доска ослепнет.
            args += ["--say", "Тебя подняли кнопкой с доски. Первым ходом закрой "
                             "старт-гейт (живая цель + первый tide offload), "
                             "дальше — дело. " + say]
        ok, out = self._tide(args, cwd=str(proot), timeout=60)
        return (" · живой сессии не было — поднимаю новую"
                if ok else " · сессию поднять не вышло ({0})".format(out[:100]))

    # ── стол: артефакт = арка .tide/arcs/artifacts/NN-slug/artifact.md ──
    # Ручные жесты человека над подачей — ровно две, и обе законные кнопки руки,
    # как «закрыть» у работы: «забрал ✓» уводит подачу со стола (status → taken),
    # ★ оставляет её себе на полку (fav: yes). Обе правят паспорт и обе пишут
    # строку в ## журнал: в tide ничего не тонет молча, а подача, исчезнувшая со
    # стола без следа, читалась бы как потерянная. Ответы агенту сюда НЕ ходят —
    # их диктуют словами в сессии.

    ART_SLUG = r"[0-9A-Za-z][0-9A-Za-z._-]{0,80}"

    def _artifact_file(self, q):
        """Валидный artifact.md по ?f=<slug> или None (общая дверь жестов)."""
        slug = (q.get("f") or [""])[0]
        f = (HERE.parent / ".tide" / "arcs" / "artifacts" / slug
             / "artifact.md")
        if not re.fullmatch(self.ART_SLUG, slug) or not f.is_file():
            return None
        return f

    def _artifact_write(self, f, text, note, msg):
        """Записать паспорт артефакта, дописав *note* строкой в ## журнал
        (секция заводится, если её ещё нет), и ответить человеку *msg*."""
        from datetime import datetime
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        line = "- {0} — {1}".format(stamp, note)
        if re.search(r"^## журнал", text, flags=re.M):
            text = text.rstrip("\n") + "\n" + line + "\n"
        else:
            text = text.rstrip("\n") + "\n\n## журнал\n" + line + "\n"
        f.write_text(text, encoding="utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(msg.encode())

    def _artifact_take(self):
        """«Забрал ✓» рукой человека: status → taken. Стол рисует только new —
        значит после этого жеста подача с него уходит; со звездой она не
        пропадает совсем, а ложится на полку «сохранённые»."""
        import urllib.parse as _up
        q = _up.parse_qs(_up.urlparse(self.path).query)
        f = self._artifact_file(q)
        if not f:
            self.send_response(404); self.end_headers()
            self.wfile.write("артефакт: не нашёл".encode()); return
        text = f.read_text(encoding="utf-8")
        if not re.search(r"^status: ", text, flags=re.M):
            self.send_response(400); self.end_headers()
            self.wfile.write("артефакт: паспорт без status".encode()); return
        # повторный клик — не ошибка: доска могла не успеть обновиться, и
        # второй «забрал» должен молча согласиться, а не ругаться
        if re.search(r"^status: taken\s*$", text, flags=re.M):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write("уже забран".encode()); return
        text = re.sub(r"^status: .*$", "status: taken", text, count=1,
                      flags=re.M)
        self._artifact_write(f, text, "забран рукой с доски", "забрал")

    def _artifact_fav(self):
        """★ на артефакте: строка `fav: yes` в шапке паспорта — сразу после
        status, чтобы паспорт читался сверху вниз («что это · где это · моё
        ли»). on=0 строку убирает. Старые паспорта без fav живут как были:
        отсутствие строки и есть «не сохранён».

        Повторный клик (★ по уже сохранённому, ✕ по несохранённому) ничего не
        меняет — отвечаем «уже так» без строки в журнал, тем же руслом, что у
        работы (`_work_fav`)."""
        import urllib.parse as _up
        q = _up.parse_qs(_up.urlparse(self.path).query)
        f = self._artifact_file(q)
        on = (q.get("on") or ["1"])[0] == "1"
        if not f:
            self.send_response(404); self.end_headers()
            self.wfile.write("артефакт: не нашёл".encode()); return
        text = f.read_text(encoding="utf-8")
        if not re.search(r"^status: ", text, flags=re.M):
            self.send_response(400); self.end_headers()
            self.wfile.write("артефакт: паспорт без status".encode()); return
        had = re.search(r"^fav: (.*)$", text, flags=re.M)
        is_on = bool(had) and had.group(1).strip().lower() in ("yes", "true", "1")
        if is_on == on:
            return self._same("★ уже сохранён" if on else "уже не сохранён")
        if on and had:
            text = re.sub(r"^fav: .*$", "fav: yes", text, count=1, flags=re.M)
        elif on:
            text = re.sub(r"^status: .*$", "\\g<0>\nfav: yes", text, count=1,
                          flags=re.M)
        else:
            text = re.sub(r"^fav: .*\n?", "", text, count=1, flags=re.M)
        self._artifact_write(f, text,
                             "в избранных" if on else "из избранных",
                             "★ сохранён" if on else "убран из сохранённых")

    # ── «доска страниц»: страница-рисунок = pages/<имя>.png (решение 16.07) ──

    PG_NAME = r"[0-9A-Za-z][0-9A-Za-z._-]{0,80}"

    def _page_del(self):
        """Крестик на карточке: страница не удаляется, а уезжает в
        pages/.trash (png+json) — ничего не тонет молча."""
        import urllib.parse as _up
        from datetime import datetime
        q = _up.parse_qs(_up.urlparse(self.path).query)
        name = (q.get("f") or [""])[0]
        d = HERE.parent / "pages"
        if not re.fullmatch(self.PG_NAME, name):
            self.send_response(400); self.end_headers()
            self.wfile.write("page: плохое имя".encode()); return
        trash = d / ".trash"
        trash.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%H%M%S")
        moved = 0
        for ext in (".png", ".json"):
            p = d / (name + ext)
            if p.is_file():
                dst = trash / (name + ext)
                if dst.exists():
                    dst = trash / (name + "-" + stamp + ext)
                p.rename(dst); moved += 1
        if not moved:
            self.send_response(404); self.end_headers()
            self.wfile.write("page: не нашёл".encode()); return
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write("страница в корзине".encode())

    def _page_asset(self):
        """Ассет рисовалки (генерённая картинка): pages/.assets/<имя>.png."""
        import urllib.parse as _up
        q = _up.parse_qs(_up.urlparse(self.path).query)
        name = (q.get("f") or [""])[0]
        f = HERE.parent / "pages" / ".assets" / name
        if not re.fullmatch(self.PG_NAME, name) or not f.is_file():
            self.send_response(404); self.end_headers(); return
        body = f.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _pg_assets(self):
        """Галерея ассетов рисовалки: имена *.png из pages/.assets,
        свежие первыми (mtime), максимум 40 — для пикера картинок."""
        import json as _json
        d = HERE.parent / "pages" / ".assets"
        names = []
        if d.is_dir():
            files = sorted(d.glob("*.png"),
                           key=lambda p: p.stat().st_mtime, reverse=True)
            names = [p.name for p in files[:40]]
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(_json.dumps(names).encode())

    def _pg_gen(self):
        """Генерация картинки для рисовалки через Replicate (скилл
        replicate-gen, токен в Keychain). Сервер однопоточный — работа
        уходит в фоновый тред, ответ сразу: {"f": "<имя ассета>"}."""
        import json as _json
        import random
        import threading
        import time
        import urllib.parse as _up
        n = int(self.headers.get("Content-Length") or 0)
        if not 0 < n <= 4096:
            self.send_response(400); self.end_headers()
            self.wfile.write("pg-gen: плохой запрос".encode()); return
        q = _up.parse_qs(self.rfile.read(n).decode())
        prompt = (q.get("p") or [""])[0].strip()
        if not prompt:
            self.send_response(400); self.end_headers()
            self.wfile.write("pg-gen: пустой промпт".encode()); return
        transparent = (q.get("t") or [""])[0] == "1"
        try:
            ar = float((q.get("ar") or ["1"])[0])
        except ValueError:
            ar = 1.0
        aspects = {"1:1": 1.0, "16:9": 16 / 9, "9:16": 9 / 16,
                   "4:3": 4 / 3, "3:4": 3 / 4, "3:2": 1.5, "2:3": 2 / 3}
        aspect = min(aspects, key=lambda a: abs(aspects[a] - ar))
        name = "img-{0}-{1}.png".format(time.strftime("%H%M%S"),
                                        random.randint(100, 999))
        d = HERE.parent / "pages" / ".assets"
        d.mkdir(parents=True, exist_ok=True)
        script = (Path.home() / ".claude" / "skills" / "replicate-gen"
                  / "replicate_gen.py")
        # replicate_gen.py ищет токен в Keychain по os.environ["USER"] —
        # в _TIDE_ENV его нет, докладываем только для этого вызова
        env = dict(_TIDE_ENV, USER=_os.environ.get("USER", Path.home().name))

        def run():
            try:
                if not transparent:
                    subprocess.run(
                        [sys.executable, str(script), "image", prompt,
                         str(d / name), "--model",
                         "black-forest-labs/flux-schnell",
                         "--aspect", aspect],
                        capture_output=True, timeout=240, env=env)
                    return
                # прозрачный фон: генерим во временный raw, фон снимает
                # Replicate; d/name появляется ТОЛЬКО готовым (поллер клиента
                # хватает первый успешный GET). Убирание сорвалось — фолбэк:
                # raw переименовывается в d/name (белый фон лучше пустоты).
                raw = d / (name + ".raw")
                subprocess.run(
                    [sys.executable, str(script), "image", prompt,
                     str(raw), "--model", "black-forest-labs/flux-schnell",
                     "--aspect", aspect],
                    capture_output=True, timeout=240, env=env)
                if not (raw.is_file() and raw.stat().st_size > 1000):
                    return
                try:
                    Handler._rmbg(raw, d / name)
                except Exception:
                    try:
                        raw.rename(d / name)
                    except Exception:
                        pass
                finally:
                    if raw.exists() and (d / name).exists():
                        try:
                            raw.unlink()
                        except Exception:
                            pass
            except Exception:
                pass

        threading.Thread(target=run, daemon=True).start()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(_json.dumps({"f": name}).encode())

    @staticmethod
    def _replicate_token():
        r = subprocess.run(["security", "find-generic-password",
                            "-s", "replicate-api-token", "-w"],
                           capture_output=True, text=True, timeout=10)
        return (r.stdout or "").strip()

    @staticmethod
    def _rmbg(src, dst):
        """Убрать фон: Replicate 851-labs/background-remover, синхронно
        (Prefer: wait). src → dst; кидает исключение при любой беде.
        Модель комьюнити — эндпоинт models/{...}/predictions ей не положен
        (404), ходим через /v1/predictions с version; User-Agent обязателен —
        дефолтный Python-urllib режет Cloudflare (error 1010)."""
        import base64
        import json as _json
        import time as _time
        import urllib.request as _ur
        token = Handler._replicate_token()
        if not token:
            raise RuntimeError("no token")
        hdrs = {"Authorization": "Bearer " + token,
                "User-Agent": "tide-board/1.0"}

        def _get(url):
            with _ur.urlopen(_ur.Request(url, headers=hdrs), timeout=30) as r:
                return _json.loads(r.read().decode())

        # версия модели: свежая с API; осечка — пин (рабочий на 22.07.2026)
        version = ("a029dff38972b5fda4ec5d75d7d1cd25"
                   "aeff621d2cf4946a41055d7db66b80bc")
        try:
            version = _get("https://api.replicate.com/v1/models/"
                           "851-labs/background-remover")["latest_version"]["id"]
        except Exception:
            pass
        raw = Path(src).read_bytes()
        # файл уходит data-URI (flux-ассеты ~0.2–2МБ; Replicate такое ест)
        payload = _json.dumps({"version": version, "input": {"image":
            "data:image/png;base64," + base64.b64encode(raw).decode()}}).encode()
        req = _ur.Request(
            "https://api.replicate.com/v1/predictions",
            data=payload, method="POST",
            headers=dict(hdrs, **{"Content-Type": "application/json",
                                  "Prefer": "wait=60"}))
        with _ur.urlopen(req, timeout=120) as resp:
            out = _json.loads(resp.read().decode())
        # Prefer: wait=60 может вернуть "processing" — доводим поллингом
        # (GET раз в 3с, суммарно до 120с, тот же Authorization)
        deadline = _time.time() + 120
        while (out.get("status") not in ("succeeded", "failed", "canceled")
               and (out.get("urls") or {}).get("get")
               and _time.time() < deadline):
            _time.sleep(3)
            out = _get(out["urls"]["get"])
        url = out.get("output")
        if isinstance(url, list):
            url = url[0] if url else None
        if not (out.get("status") == "succeeded" and url):
            raise RuntimeError("rmbg failed: " + str(out.get("status")))
        dreq = _ur.Request(url, headers={"User-Agent": "tide-board/1.0"})
        with _ur.urlopen(dreq, timeout=120) as resp:
            Path(dst).write_bytes(resp.read())

    def _page_get(self):
        import urllib.parse as _up
        q = _up.parse_qs(_up.urlparse(self.path).query)
        name = (q.get("f") or [""])[0]
        # правда рисунка — штрихи: /page?f=<имя>.json (paint, шаг 1);
        # png остаётся превью и выгрузкой
        is_json = name.endswith(".json")
        f = HERE.parent / "pages" / (name if is_json else name + ".png")
        if not re.fullmatch(self.PG_NAME, name) or not f.is_file():
            self.send_response(404); self.end_headers(); return
        body = f.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type",
                         "application/json" if is_json else "image/png")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path.startswith("/page-save"):
            return self._page_save()
        if self.path.startswith("/pg-beacon"):
            return self._pg_beacon()
        if self.path.startswith("/pg-gen"):
            return self._pg_gen()
        self.send_response(404); self.end_headers()

    def _pg_beacon(self):
        """ВРЕМЕННЫЙ сток маяка рисовалки (paint, диагноз «мимо курсора»):
        строка-jsonl на каждый штрих. Снять вместе с маяком после диагноза."""
        n = int(self.headers.get("Content-Length") or 0)
        if not 0 < n <= 4096:
            self.send_response(204); self.end_headers(); return
        raw = self.rfile.read(n).decode("utf-8", "replace")
        f = HERE.parent / "pages" / ".pg-beacon.jsonl"
        with f.open("a", encoding="utf-8") as fh:
            fh.write(raw.replace("\n", " ") + "\n")
        self.send_response(204); self.end_headers()

    def _page_save(self):
        import base64
        import urllib.parse as _up
        n = int(self.headers.get("Content-Length") or 0)
        if not 0 < n <= 12_000_000:  # ~12МБ хватает канвасу с запасом
            self.send_response(413); self.end_headers()
            self.wfile.write("page: слишком большой рисунок".encode()); return
        q = _up.parse_qs(self.rfile.read(n).decode())
        name = (q.get("f") or [""])[0]
        data = (q.get("d") or [""])[0]
        prefix = "data:image/png;base64,"
        if not re.fullmatch(self.PG_NAME, name) or not data.startswith(prefix):
            self.send_response(400); self.end_headers()
            self.wfile.write("page: плохие данные".encode()); return
        try:
            raw = base64.b64decode(data[len(prefix):])
        except Exception:
            self.send_response(400); self.end_headers()
            self.wfile.write("page: битый base64".encode()); return
        d = HERE.parent / "pages"
        d.mkdir(parents=True, exist_ok=True)
        # fresh=1 — имя только что родилось у клиента (метка до минуты):
        # два сейва в одну минуту не должны молча писаться поверх (решение владельца
        # 16.07: «вторую сохраняю — не сохраняется») — разводим суффиксом
        if (q.get("fresh") or [""])[0] == "1":
            base, i = name, 2
            while (d / (name + ".png")).exists() \
                    or (d / (name + ".json")).exists():
                name = "{0}-{1}".format(base, i); i += 1
        # перед перезаписью прежняя версия уезжает в корзину со штампом —
        # пересейв не съедает историю (урок 16.07: тест затёр страницу 1944)
        from datetime import datetime
        stamp = datetime.now().strftime("%H%M%S")
        trash = d / ".trash"
        for ext in (".png", ".json"):
            old = d / (name + ext)
            if old.is_file():
                trash.mkdir(parents=True, exist_ok=True)
                old.rename(trash / (name + "-" + stamp + ext))
        (d / (name + ".png")).write_bytes(raw)
        # штрихи (s=) — правда рисунка и его история: <имя>.json рядом с png
        strokes = (q.get("s") or [""])[0]
        if strokes:
            import json as _json
            try:
                parsed = _json.loads(strokes)
                assert isinstance(parsed, dict) and "strokes" in parsed
            except Exception:
                self.send_response(400); self.end_headers()
                self.wfile.write("page: битые штрихи".encode()); return
            (d / (name + ".json")).write_text(strokes, encoding="utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("X-Page-Name", name)
        self.end_headers()
        self.wfile.write("страница сохранена · {0}".format(name).encode())

    def _news_fav(self):
        """★ на карточке новости — тоггл слага в news/favorites.txt (русло
        избранного; решение 16.07: «избранные добавлять, те, что очень нравятся»)."""
        import urllib.parse as _up
        q = _up.parse_qs(_up.urlparse(self.path).query)
        name = (q.get("f") or [""])[0]
        if not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z._-]{0,120}", name):
            self.send_response(400); self.end_headers()
            self.wfile.write("fav: плохое имя".encode()); return
        if not (NEWS_ROOT / (name + ".md")).is_file():
            self.send_response(404); self.end_headers()
            self.wfile.write("fav: нет такой статьи".encode()); return
        favp = NEWS_ROOT / "favorites.txt"
        favs = ([l.strip() for l in favp.read_text(encoding="utf-8").splitlines()
                 if l.strip()] if favp.exists() else [])
        if name in favs:
            favs = [x for x in favs if x != name]; msg = "убрано из избранного"
        else:
            favs = [name] + favs; msg = "в избранном"
        favp.write_text("\n".join(favs) + ("\n" if favs else ""),
                        encoding="utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(msg.encode())

    ARTICLE_CSS = """
    :root{--bg:#14181f;--panel:#1b222c;--line:#2a323e;--ink:#e6e9ee;
      --dim:#8b94a3;--faint:#5c6572;--c1:#e0603f;--c2:#e0a63f;
      --mono:'SF Mono',ui-monospace,Menlo,monospace}
    *{box-sizing:border-box;margin:0;padding:0}
    body{background:var(--bg);color:var(--ink);font:16px/1.65 -apple-system,
      'Helvetica Neue',sans-serif;padding:48px 20px 90px}
    .art{max-width:680px;margin:0 auto}
    .back{display:inline-block;margin-bottom:34px;color:var(--dim);
      text-decoration:none;font-family:var(--mono);font-size:12.5px}
    .back:hover{color:var(--ink)}
    h1{font-size:27px;line-height:1.2;letter-spacing:-.5px;margin:0 0 14px}
    h2{font-size:19px;margin:36px 0 12px;letter-spacing:-.2px}
    .meta{font-family:var(--mono);font-size:12px;color:var(--faint);
      margin-bottom:34px;padding-bottom:20px;border-bottom:1px solid var(--line)}
    .meta a{color:var(--dim)}
    p{margin:0 0 16px}
    ul,ol{margin:0 0 16px 22px}
    li{margin:0 0 6px}
    strong{color:var(--ink)}
    a{color:var(--c2)}
    """

    def _article(self):
        """Читалка статьи news/*.md — клик по карточке вкладки «новости»
        (решение 16.07: «нажимаю — ничего не открывается»). Русло — md-файл,
        эта страница — его проекция; конвертер крошечный, по нашему же
        формату статей (# ## - ** и абзацы), не общий markdown."""
        import html as _h
        import urllib.parse as _up
        q = _up.parse_qs(_up.urlparse(self.path).query)
        name = (q.get("f") or [""])[0]
        if not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z._-]{0,120}", name):
            self.send_response(400); self.end_headers()
            self.wfile.write("article: плохое имя".encode()); return
        f = NEWS_ROOT / (name + ".md")
        if not f.is_file():
            self.send_response(404); self.end_headers()
            self.wfile.write("article: нет такой статьи".encode()); return
        lines = f.read_text(encoding="utf-8").splitlines()
        title, meta, body, in_list = name, [], [], False
        pbuf = []

        def _inline(s):
            s = _h.escape(s)
            s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
            s = re.sub(r"(https?://[^\s)]+)", r'<a href="\1">\1</a>', s)
            return s

        def _flush():
            if pbuf:
                body.append("<p>{0}</p>".format(_inline(" ".join(pbuf))))
                pbuf.clear()

        for ln in lines:
            s = ln.rstrip()
            if s.startswith("# ") and title == name:
                title = s[2:].strip(); continue
            m = re.match(r"(source|date|speaker|telegraph|site):\s*(.+)", s)
            if m and not body and not pbuf:
                meta.append(m.group(2).strip()); continue
            if s.startswith("## "):
                _flush()
                if in_list: body.append("</ul>"); in_list = False
                body.append("<h2>{0}</h2>".format(_inline(s[3:]))); continue
            if re.match(r"[-*] |\d+\. ", s):
                _flush()
                if not in_list: body.append("<ul>"); in_list = True
                body.append("<li>{0}</li>".format(
                    _inline(re.sub(r"^([-*]|\d+\.) ", "", s)))); continue
            if not s:
                _flush()
                if in_list: body.append("</ul>"); in_list = False
                continue
            if in_list:  # перенос внутри пункта списка
                body[-1] = body[-1][:-5] + " " + _inline(s) + "</li>"; continue
            pbuf.append(s)
        _flush()
        if in_list: body.append("</ul>")
        page = ("<!doctype html><html lang='ru'><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,initial-scale=1'>"
                "<title>{t}</title><style>{css}</style><body><div class='art'>"
                "<a class='back' href='/'>← доска</a>"
                "<h1>{t}</h1><div class='meta'>{m}</div>{b}</div>"
                "<script>document.addEventListener('keydown',e=>{{"
                "if(e.key==='Escape')location.href='/';}});"
                # внешние ссылки — через /ext (open → выбор браузера),
                # не угоняя вебвью; «← доска» своя, её не трогаем
                "document.addEventListener('click',e=>{{"
                "const a=e.target.closest&&e.target.closest('a[href]');"
                "if(!a)return;"
                "if(!/^https?:/.test(a.href)||a.host===location.host)return;"
                "e.preventDefault();"
                "fetch('/ext?u='+encodeURIComponent(a.href)).catch(()=>{{}});}});"
                # зум Cmd+=/-/0 — общий множитель с доской (board-zoom)
                "(function(){{let z=1;"
                "try{{z=parseFloat(localStorage.getItem('board-zoom'))||1;}}catch(e){{}}"
                "function ap(){{document.body.style.zoom=z;}}"
                "function st(n){{z=Math.min(2,Math.max(.5,Math.round(n*10)/10));"
                "try{{localStorage.setItem('board-zoom',z);}}catch(e){{}}ap();}}"
                "document.addEventListener('keydown',e=>{{"
                "if(!(e.metaKey||e.ctrlKey))return;"
                "if(e.key==='='||e.key==='+'){{e.preventDefault();st(z+0.1);}}"
                "else if(e.key==='-'){{e.preventDefault();st(z-0.1);}}"
                "else if(e.key==='0'){{e.preventDefault();st(1);}}}});"
                "ap();}})();</script></html>").format(
            t=_h.escape(title), css=self.ARTICLE_CSS,
            m=_inline(" · ".join(meta)), b="".join(body))
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(page.encode())

    def _new_thread(self):
        """＋ старт НОВОЙ нити прямо с доски (решение 13.07: вписать имя, нажать
        старт — нить заводится И поднимается оркестрирующей сессией, без CLI).
        Создаёт thread ``NN-@slug`` с goal=именем (``tide arc new-thread``), затем
        спаркует в неё сессию тем же путём, что ▶ у пустой нити. Слаг — транслит
        имени (кириллица→латиница): tide slugify рубит кириллицу в пустоту."""
        import urllib.parse as _up
        q = _up.parse_qs(_up.urlparse(self.path).query)
        proj = (q.get("proj") or [""])[0]
        name = (q.get("name") or [""])[0].strip()
        if not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z@._-]{0,79}", proj):
            self.send_response(400); self.end_headers()
            self.wfile.write("new-thread: плохой проект".encode()); return
        proot = self._roster_root(proj)
        if not proot:
            self.send_response(400); self.end_headers()
            self.wfile.write("new-thread: нет такого проекта".encode()); return
        if not name:
            self.send_response(400); self.end_headers()
            self.wfile.write("new-thread: пустое имя".encode()); return
        name = name[:120]
        slug = _slugify(name)
        if not slug:
            self.send_response(400); self.end_headers()
            self.wfile.write("new-thread: имя не даёт слаг — впиши пару латинских/цифр".encode())
            return
        env = {"PATH": "{0}/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:"
                       "/bin".format(Path.home()), "HOME": str(Path.home())}
        try:
            # НЕ ставим goal=имя (решение 14.07, cand 104): имя — это ТЭГ нити (slug),
            # а цель/суть при рождении НЕ определена — нить заводится draft'ом, суть
            # добирается отдельно/агентом. Раньше --goal name делал цель = набранным
            # именем, чего человек не просил.
            r = subprocess.run(
                ["tide", "arc", "new-thread", slug],
                capture_output=True, text=True, timeout=30, cwd=proot, env=env)
            if r.returncode != 0:
                self.send_response(500); self.end_headers()
                self.wfile.write(("new-thread: не завелась — "
                                  + (r.stdout + r.stderr).strip()).encode()); return
        except Exception as exc:
            self.send_response(500); self.end_headers()
            self.wfile.write(("new-thread: " + str(exc)).encode()); return
        # найти созданную нить NN-@slug (новейшую по номеру)
        arcs = Path(proot) / ".tide" / "arcs"
        pat = re.compile(r"^\d+-@{0}$".format(re.escape(slug)))
        dirs = sorted((d for d in arcs.iterdir() if d.is_dir() and pat.match(d.name)),
                      key=lambda d: d.name)
        if not dirs:
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8"); self.end_headers()
            self.wfile.write("нить заведена — подними её ▶ на полке".encode()); return
        # спаркнуть первую сессию в созданную нить — ЕДИНЫМ путём через `tide spark`
        # (cand 94): tide создаёт сессию, пинит id, регает по sid, спавнит.
        return self._tide_spark(proj, ["--thread", slug])

    def _drop_thread(self):
        """✕ убрать ПУСТУЮ планлесс-нить С ДОСКИ (pull: клик = рука человека).
        Мягко по закону «ничего не тонет»: папка-нить ПЕРЕЕЗЖАЕТ в
        ``.tide/arcs/__dropped__/`` — уходит с полки, но остаётся на диске и
        восстановима. НЕ ``__NN@__`` (это трофей в «закрыто»): убранная пустышка
        трофеем не была. ГЕЙТ БЕЗОПАСНОСТИ: убрать можно ТОЛЬКО реально пустую
        нить (ноль открытых сессий И нет plan.md) — живую работу не тронуть даже
        подделанным URL. Валидация пути — как в ``_dismiss``/``_close``."""
        import urllib.parse as _up
        q = _up.parse_qs(_up.urlparse(self.path).query)
        d = (q.get("d") or [""])[0]
        tdir = Path(d)
        ok = (self._under_roster(d) and "/.tide/arcs/" in d
              and tdir.is_dir() and not tdir.name.startswith("__")
              and tdir.parent.name == "arcs")
        if not ok:
            self.send_response(400); self.end_headers()
            self.wfile.write("drop-thread: плохой путь".encode()); return
        # ГЕЙТ пустоты живёт в домене: tide arc drop отказывает на живой работе
        ok, err = self._tide(["tide", "arc", "drop", "--dir", d])
        empty_gate = (not ok) and "not empty" in err
        self.send_response(200 if ok else (400 if empty_gate else 500))
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write((("пустая нить убрана — в __dropped__, восстановима" if ok
                           else ("drop-thread: нить не пустая — руками" if empty_gate
                                 else "убрать не вышло: {0}".format(err[:200])))).encode())

    def _take(self):
        """▶ запуск висящего хендоффа С ДОСКИ (pull: клик = рука человека).
        Поднимает засеянную сессию как tide menu: claude с @seed в корне
        проекта. Оффер НЕ помечается taken — приём настоящий: свежая сессия
        подтверждает сама (закон №2)."""
        import re as _re
        import urllib.parse as _up
        q = _up.parse_qs(_up.urlparse(self.path).query)
        key = (q.get("key") or [""])[0]
        home = CONTROL_HOME
        ok_key = bool(_re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z_-]{0,79}", key))
        rec = home / ".tide" / "handoffs" / "{0}.md".format(key)
        if not (ok_key and rec.is_file()):
            self.send_response(400); self.end_headers()
            self.wfile.write("take: нет такого оффера".encode()); return
        text = rec.read_text(encoding="utf-8", errors="ignore")

        def fld(k):
            import re as _r
            m = _r.search(r"^{0}:\s*(.+)$".format(k), text, _r.M)
            return m.group(1).strip() if m else ""

        status, seed, project = fld("status"), fld("seed"), fld("project")
        # ГЕЙТ СВЕЖЕСТИ (решение 08.07): источник ушёл вперёд после оффера →
        # сид устарел, запуск родил бы дубль (Микки 17). Меряем рост
        # транскрипта сессии-источника; порог — пара прощальных реплик.
        from_sid = fld("from-session")
        base = fld("origin-size")
        if from_sid:
            hits = list((Path.home() / ".claude" / "projects").glob(
                "*/{0}.jsonl".format(from_sid)))
            if hits and base.isdigit():
                grown = hits[0].stat().st_size - int(base)
                if grown > 150_000:
                    self.send_response(409)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(("⚠ сид устарел: источник ушёл вперёд на {0} КБ — "
                                      "попроси его пере-хендоффнуть или дропни оффер"
                                      .format(grown // 1000)).encode())
                    return
        proot = ""
        rf = home / "roster.md"
        if rf.is_file():
            for line in rf.read_text(encoding="utf-8").splitlines():
                parts = [p.strip() for p in line.split("|")]
                if len(parts) >= 2 and parts[0] == project:
                    proot = parts[1]; break
        ok = (status == "offered" and Path(seed).is_file()
              and proot and Path(proot).expanduser().is_dir())
        if not ok:
            self.send_response(400); self.end_headers()
            self.wfile.write("take: оффер не готов (status/seed/project)".encode()); return
        proot = str(Path(proot).expanduser())
        # ЕДИНЫЙ путь подъёма (волна 3): `tide pickup` = тот же launch_session, что и
        # у меню — sid до спавна, резерв оффера за sid (флип на первом ходе сессии,
        # подпись A), scoped-MCP команда, запись реестра. Крупнейший дубль доски
        # (инлайновый orca create + ручной claude + venv-мост _confirm_pickup) умер.
        ok, out = self._tide(["tide", "pickup", key, "--json"], timeout=60)
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(("поднимается" if ok else
                          "не вышло ({0}) — руками: cd {1} && claude "
                          "--append-system-prompt @{2}"
                          .format(out[:120], proot, seed)).encode())

    def _spark(self):
        """▶ старт БЕСХОЗНОЙ работы с полки (pull: клик = рука человека).
        Одна кнопка — два входа под общей онтологией «бесхозная работа без
        сессии»:
          cand=<key>   — идея-файл в candidates/ → рождает НОВУЮ нить из
                         постановки;
          thread=<dir> — заведённая-но-пустая нить → поднимает свежую
                         оркестрирующую сессию ВНУТРЬ неё (дубля не плодим).
        По образцу _take: только localhost, валидация жёсткая. Приём
        подтверждает сама сессия (закон №2) — заранее ничего не помечаем."""
        import re as _re
        import urllib.parse as _up
        q = _up.parse_qs(_up.urlparse(self.path).query)
        proj = (q.get("proj") or [""])[0]
        cand = (q.get("cand") or [""])[0]
        thread = (q.get("thread") or [""])[0]
        if not _re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z@._-]{0,79}", proj):
            self.send_response(400); self.end_headers()
            self.wfile.write("spark: плохой проект".encode()); return
        proot = self._roster_root(proj)
        if not proot:
            self.send_response(400); self.end_headers()
            self.wfile.write("spark: нет такого проекта".encode()); return
        # ЕДИНЫЙ путь — `tide spark` (cand 94): tide сам создаёт арку ДО старта, пинит
        # --session-id, спавнит и пишет sid-реестр. Голова видна доске сразу (костыль
        # «запускается» больше не нужен). Доска — тонкий вызов.
        if thread:
            if not (_re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z@._-]{0,120}", thread) and ".." not in thread):
                return self._spark_err("spark: плохая нить")
            args = ["--thread", _re.sub(r"^\d+-@?", "", thread)]
        elif cand:
            args = self._cand_new_thread_args(proot, cand)
            if not args:
                return self._spark_err("spark: нет такого кандидата")
        else:
            return self._spark_err("spark: нет ни кандидата, ни нити")
        self._tide_spark(proj, args)

    def _spark_err(self, msg):
        self.send_response(400); self.end_headers(); self.wfile.write(msg.encode())

    def _tide_spark(self, proj, args):
        """Тонкий вызов `tide spark <proj> …` — tide владеет запуском (cand 94)."""
        try:
            r = subprocess.run(
                ["tide", "spark", proj] + args,
                capture_output=True, text=True, timeout=30,
                env={"PATH": "{0}/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin".format(Path.home()),
                     "HOME": str(Path.home()),
                     "TIDE_HOME": str(CONTROL_HOME)})
            ok = r.returncode == 0
            msg = "сессия поднимается — смотри терминал Orca" if ok else \
                  "не вышло поднять: {0}".format((r.stderr or r.stdout or "").strip()[:200])
        except Exception as exc:
            msg = "не вышло поднять: {0}".format(exc)
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(msg.encode())

    def _cand_new_thread_args(self, proot, cand):
        """Идея candidates/<cand>.md → аргументы `tide spark --new-thread <slug> --goal <суть>`.
        Кандидат — существующий .md строго внутри candidates/ (без обхода пути). None, если нет."""
        import re as _re
        if not (_re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z._-]{0,120}", cand) and ".." not in cand):
            return None
        cdir = (Path(proot) / ".tide" / "arcs" / "candidates").resolve()
        cfile = (cdir / "{0}.md".format(cand)).resolve()
        if not (cfile.is_file() and cfile.parent == cdir):
            return None
        raw = cfile.read_text(encoding="utf-8", errors="ignore")
        gist = " ".join(ln.strip() for ln in raw.splitlines()
                        if ln.strip() and not ln.startswith(("#", "from:", "dropped:")))[:120]
        slug = _re.sub(r"^\d+-", "", cand)
        return ["--new-thread", slug, "--goal", gist or slug]

    def _roster_dirs(self):
        """Корни ВСЕХ проектов реестра — множество для гейтов пути (⟳ resume).
        Пустое, если roster.md пропал: гейт тогда падает на запасной префикс,
        а не закрывается наглухо."""
        rf = CONTROL_HOME / "roster.md"
        out = set()
        if not rf.is_file():
            return out
        for line in rf.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 2 and parts[0] and parts[1]:
                out.add(str(Path(parts[1]).expanduser()))
        return out

    def _under_roster(self, p):
        """Путь *p* лежит внутри проекта из ростера (или самого дома)?

        ОДИН гейт на все ручки, которые принимают путь из URL. Раньше у каждой
        стоял свой префикс `~/Documents`, и он врал в обе стороны: пускал всё
        подряд из домашней папки и НЕ пускал проект, живущий вне неё, — такой
        молча получал 400, а человек видел «жму, и ничего не происходит»
        (24.08). Ростер — честная граница: доска работает ровно с тем, что
        человек в неё завёл, где бы оно ни лежало.

        Ростер пропал — не закрываемся наглухо: остаётся дом, под ним лежат
        хендоффы и состояние, без них доска бесполезна.
        """
        if not p or ".." in str(p):
            return False
        try:
            target = Path(p).resolve()
        except OSError:
            return False
        roots = {Path(r) for r in self._roster_dirs()} | {CONTROL_HOME}
        for r in roots:
            try:
                target.relative_to(Path(r).resolve())
            except ValueError:
                continue
            return True
        return False

    def _roster_root(self, proj):
        """Резолв корня проекта через roster.md — как в _take. Пусто, если
        проекта нет или каталог не существует."""
        rf = CONTROL_HOME / "roster.md"
        if rf.is_file():
            for line in rf.read_text(encoding="utf-8").splitlines():
                parts = [p.strip() for p in line.split("|")]
                if len(parts) >= 2 and parts[0] == proj:
                    root = Path(parts[1]).expanduser()
                    return str(root) if root.is_dir() else ""
        return ""

    def _validate(self):
        """✓ человек валидирует гейт шага С ДОСКИ (pull: клик = рука человека).
        Пишет `гейт-пройден` в plan.md нити и метит шаг done ([x]); следующий
        todo-шаг становится текущим ([>]), чтобы у доски был «сейчас». Пишет
        ТОЛЬКО гейт — задачи tasks.md не трогает (их заполняет агент). Только
        localhost, валидация жёсткая."""
        import re as _re
        import urllib.parse as _up
        q = _up.parse_qs(_up.urlparse(self.path).query)
        proj = (q.get("proj") or [""])[0]
        thread = (q.get("thread") or [""])[0]
        wave = (q.get("wave") or [""])[0]
        if not (_re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z@._-]{0,79}", proj)
                and _re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z@._-]{0,120}", thread)
                and ".." not in thread and _re.fullmatch(r"\d{1,3}", wave)):
            self.send_response(400); self.end_headers()
            self.wfile.write("validate: плохие параметры".encode()); return
        proot = self._roster_root(proj)
        if not proot:
            self.send_response(400); self.end_headers()
            self.wfile.write("validate: нет такого проекта".encode()); return
        arcs = (Path(proot) / ".tide" / "arcs").resolve()
        tdir = (arcs / thread).resolve()
        plan = tdir / "plan.md"
        if not (tdir.parent == arcs and tdir.is_dir() and plan.is_file()):
            self.send_response(400); self.end_headers()
            self.wfile.write("validate: нет плана нити".encode()); return
        # Структурная правка plan.md живёт в домене (tide arc validate): [x] на шаге,
        # гейт-пройден в под-блоке, промоут следующего [ ] в [>].
        ok, out = self._tide(["tide", "arc", "validate", "--dir", str(tdir),
                              "--step", wave, "--who", "человек (с доски)"])
        known_bad = (not ok) and ("no step" in out or "bad step" in out)
        self.send_response(200 if ok else (400 if known_bad else 500))
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(("шаг {0} завалидирован".format(wave) if ok
                          else ("validate: нет такого шага" if known_bad
                                else "не вышло записать план: {0}".format(out[:200]))).encode())

    def _open(self):
        """Открыть артефакт workspace дефолтным приложением (pull-модель:
        запрос рождается только кликом человека в окне, localhost).
        Валидация как у /resume: существующий файл внутри проекта из ростера."""
        import urllib.parse as _up
        q = _up.parse_qs(_up.urlparse(self.path).query)
        f = (q.get("f") or [""])[0]
        ok = self._under_roster(f) and Path(f).is_file()
        if not ok:
            self.send_response(400); self.end_headers()
            self.wfile.write("open: плохой путь".encode()); return
        try:
            subprocess.run(["/usr/bin/open", f], timeout=10)
        except Exception:
            pass
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write("ok".encode())

    def log_message(self, *a):  # тихо: это окно, не сервер логов
        pass


def _probe_tide() -> None:
    """Capability-probe при старте: доске нужны вербы return/pickup/arc hold и т.д.

    Разъезд версий (доска новее tide) не должен встречаться молча на клике: одна
    честная строка в лог при старте. Кнопки при недостаче и так деградируют в
    человеко-читаемую подсказку (не 500) — это второй эшелон, не замена пробе."""
    try:
        r = subprocess.run(["tide", "help"], capture_output=True, text=True,
                           timeout=10, env=_TIDE_ENV)
        blob = r.stdout + r.stderr
        missing = [v for v in ("return", "pickup") if v not in blob]
        if r.returncode != 0:
            print("board: ⚠ tide не отвечает — кнопки будут деградировать в подсказки",
                  file=sys.stderr)
        elif missing:
            print("board: ⚠ tide без вербов {0} — обнови tide (tide self-update)".format(
                ", ".join(missing)), file=sys.stderr)
    except Exception as exc:
        print("board: ⚠ проба tide не удалась: {0}".format(exc), file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port-file")
    ap.add_argument("--port", type=int, default=0)
    args = ap.parse_args()
    _probe_tide()
    # многопоточный: клик (⟳/▶/take/✓) обслуживается СРАЗУ, а не в очереди за рендером
    # соседней вкладки; заодно висящий preconnect браузера больше не запирает accept-цикл
    # (у BaseHTTPRequestHandler нет таймаута чтения — принятое молчащее соединение
    # держало однопоточный сервер, пока клиент не заговорит)
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)  # только localhost
    if args.port_file:
        Path(args.port_file).write_text(str(srv.server_address[1]), encoding="utf-8")
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
