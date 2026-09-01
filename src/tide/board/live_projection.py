#!/usr/bin/env python3
"""live_projection — доска по закону (канд. 46): нити свежие сверху, проекты как
группировка, тексты из ПАСПОРТОВ (никакого рукописного FOCUS-словаря).

Проекция живых файлов tide, свежая на каждый рендер:
  ростер (архивные проекты скрыты) → нити каждого проекта (цель/статус) →
  их сессии (title/goal/cursor/context/offloaded-at) → очередь передач.
Свежесть нити = новейший паспорт внутри; сортировка проектов — по свежести
их самой живой нити. Дизайн — шаблон Current Scale (deck/desktop/scope).
"""

import html as _html
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

def _control_home():
    """Дом, из которого доска читает ростер, хендоффы и избранное.

    Правило ОДНО с движком (`tide.paths.control_home`): сначала `$TIDE_HOME`,
    иначе подъём вверх за каталогом, у которого лежит `roster.md`. Читаем его
    тут своими десятью строками, а не импортом, по той же причине, что и
    `_plugins_off` ниже: `import tide` стоит ~0.25 с на каждый рендер, а доска
    обязана открыться и в питоне, который tide не видит.

    ПОСЛЕДНЕЙ стоит ветка на `~/Documents/tide-home`, и это не личная
    константа, забытая в общем коде, а переходник ровно на одну машину:
    доску владельца поднимает служба launchd, у которой в plist нет `TIDE_HOME`,
    а подъём от файла доски упирается в `~/Documents`, где ростера нет. Путь
    берётся ТОЛЬКО если там правда лежит `roster.md`, поэтому у любого другого
    человека этой ветки просто нет. Умрёт вместе с переводом службы на
    `tide board` — тогда дом приедет из окружения, как у всех.
    """
    env = (os.environ.get("TIDE_HOME") or "").strip()
    if env:
        p = Path(env).expanduser()
        if p.is_dir():
            return p.resolve()
    for c in Path(__file__).resolve().parents:
        if (c / "roster.md").is_file():
            return c
    return Path.home() / "Documents" / "tide-home"


HOME = _control_home()
TEMPLATE = next(pp / "scope" / "index.html"
                for pp in Path(__file__).resolve().parents
                if (pp / "scope" / "index.html").is_file())  # ищем шаблон вверх — переживает симлинки
def _cache_dir():
    """Куда доска складывает своё рабочее: собранную страницу и счётчики.

    Рядом с кодом — только пока код лежит в репозитории и папка `build` там
    уже есть. У установленного пакета сосед кода — site-packages, и он бывает
    доступен только на чтение: доска, пишущая себе под ноги, там просто не
    поднимется. Поэтому по умолчанию пишем в дом человека, а старое место
    оставляем, пока оно живо, — чтобы у владельца ничего не переехало молча.
    """
    near = Path(__file__).parent / "build"
    if near.is_dir():
        return near
    d = HOME / ".tide" / "board-build"
    d.mkdir(parents=True, exist_ok=True)
    return d


OUT = Path(os.environ.get("LIVE_OUT") or _cache_dir() / "board.html")
# транскрипты claude-сессий: их mtime — честный пульс головы (дописываются
# на каждом ходу), в отличие от arc.md (пишется только при offload/handoff)
CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"
# сторона контента shell-kit: копия продукта, фолбэк — эталон кита
SHELL_CLIENT = next((pp / rel
                     for pp in Path(__file__).resolve().parents
                     for rel in ("board/src/shell/shell-client.js",
                                 "shell-kit/kit/shell-client.js",
                                 # то же самое, когда доска уехала в пакет:
                                 # tide/board/shell/shell-client.js рядом с кодом
                                 "shell/shell-client.js")
                     if (pp / rel).is_file()), None)


def _plugins_off():
    """Имена съёмных частей, ВЫКЛЮЧЕННЫХ у этого человека (работа 48).

    Что кор, что плагин и что вообще бывает — знает движок (`tide.plugins`,
    там же вербы `tide plugins on|off`). Общий у нас ФАЙЛ:
    `<control-home>/.tide/plugins`, строки вида `имя = on|off`.

    Читаем его тут своими двенадцатью строками, а не импортом `tide.plugins`,
    СОЗНАТЕЛЬНО: `import tide` тянет за собой importlib.metadata и стоит ~0.25 с
    — на каждый запрос доски (проекция пересобирается всегда). Плюс доска
    обязана открыться даже в питоне, который tide не видит. Правило разбора
    ровно одно и здесь, и в движке: ВЫКЛЮЧАЕТ только явное `= off`.

    Всё остальное — нет файла, кривая строка, незнакомое имя, нечитаемый
    каталог — значит «включено». Ни одно из этих состояний не имеет права
    отнять у человека вкладку; поэтому и доска владельца, у которой файла может
    не быть вовсе, не меняется ни на пиксель."""
    off = set()
    try:
        home = Path(os.environ.get("TIDE_HOME") or HOME)
        f = home / ".tide" / "plugins"
        if not f.is_file():
            return off
        for raw in f.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.split("#", 1)[0].strip()
            name, _, value = line.partition("=")
            if value.strip().lower() in ("off", "0", "false", "no"):
                off.add(name.strip().lower())
    except Exception:
        return set()
    return off


S = 'fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"'

# СЕССИЯ-РЯД по UI-киту фабрики (design/system/ui-kit.html, аккордеон .acc-*
# + .btn-icon; DESIGN-LANGUAGE: mono, hairline-борders, углы 2-4px, без пилюль).
# Один связный блок классов вместо инлайн-каши (урок владельца 09.07).
SESS_CSS = """
/* коннектор таймлайна у богатого узла стартует НИЖЕ кружка (крупнее обычного),
   иначе полоска налезает на круг сверху */
.step.rich .mc::after{ top:26px; }
/* записи контекста КАРТОЧКАМИ (решение 13.07: «время как заголовок, не сплошной
   текст, сделай красиво»): время — тусклым mono-ЗАГОЛОВКОМ сверху (только если
   есть); тело — читаемым абзацем под ним; между записями тонкая линия; много-
   пунктовое — спойлером с бейджем «+N» и пунктами на рельсе */
.ctx{ padding:11px 0; border-top:1px solid var(--line); }
.substeps .ctx:first-child{ border-top:none; padding-top:2px; }
.ctx .ct{ color:var(--ink-faint); font-family:var(--mono); font-size:10.5px;
  letter-spacing:.09em; margin-bottom:5px; }
.ctx .cx{ color:var(--ink-dim); font-size:12.5px; line-height:1.62; }
.ctx .cx>div{ padding:2.5px 0; }
/* код-якоря: ярче прозы (контраст против «единого серого») */
.ctx .k{ color:var(--ink); font-weight:600; }
.ctx code.k{ color:var(--c2); font-weight:400; }
details.ctx{ display:block; }
details.ctx>summary{ list-style:none; cursor:pointer; }
details.ctx>summary::-webkit-details-marker{ display:none; }
details.ctx>summary:hover .cx{ color:var(--ink); }
.ctx .cxmore{ margin-left:8px; color:var(--ink-faint); font-family:var(--mono);
  font-size:10px; border:1px solid var(--line-2); border-radius:3px; padding:0 5px;
  white-space:nowrap; }
details.ctx[open] .cxmore{ display:none; }
.ctx .cxd{ margin:7px 0 1px; padding-left:12px; border-left:1px solid var(--line-2);
  color:var(--ink-mute); font-size:12px; line-height:1.6; }
.ctx .cxd>div{ padding:2px 0; }
/* пункт спойлера: номер тусклым амбер-индексом в колонке, текст рядом (решение владельца
   13.07: без «(N)» в скобках) */
.ctx .cxd .cxi{ display:flex; gap:11px; padding:3px 0; align-items:baseline; }
.ctx .cxd .cxn{ flex:none; min-width:15px; text-align:right; color:var(--c2);
  font-family:var(--mono); font-size:11px; opacity:.8; }
.ctx .cxd .cxt{ flex:1; min-width:0; }
/* пункт без номера (клауза от «; »-реза): просто строка вплотную к рельсу,
   без буллета-точки и без колонки-индекса (решение 13.07) */
.ctx .cxd .cxp{ padding:3px 0; }
/* тост-подтверждение (deckCopy и др.): всплывает снизу по центру, гаснет сам */
#toast{ position:fixed; left:50%; bottom:26px; transform:translateX(-50%) translateY(16px);
  background:var(--bg-2); border:1px solid var(--line-2); color:var(--ink);
  font-family:var(--mono); font-size:12px; padding:8px 16px; border-radius:6px;
  opacity:0; pointer-events:none; z-index:80;
  transition:opacity .18s ease, transform .18s ease; }
#toast.show{ opacity:1; transform:translateX(-50%) translateY(0); }
/* ＋ старт новой нити с доски (решение 13.07): поле имени + кнопка старт, вверху полки */
.ntf{ display:flex; gap:8px; align-items:stretch; margin:2px 0 20px; }
.nti{ flex:1; min-width:0; background:var(--inset); border:1px solid var(--line-2);
  border-radius:8px; color:var(--ink); font-family:var(--mono); font-size:12.5px;
  padding:9px 14px; outline:none; transition:border-color .12s ease; }
.nti:focus{ border-color:var(--c2); }
.nti::placeholder{ color:var(--ink-faint); }
.ntb{ flex:none; border-radius:8px; }
.sess{ border-bottom:1px solid var(--line-2); }
.sess>summary{ list-style:none; display:flex; align-items:center; gap:12px;
  padding:9px 2px; cursor:pointer; min-width:0; }
.sess>summary::-webkit-details-marker{ display:none; }
.sess>summary:hover .stitle{ color:var(--ink); }
.sess .sid{ color:var(--ink-faint); font-family:var(--mono); font-size:11px;
  white-space:nowrap; width:148px; flex:none; overflow:hidden; text-overflow:ellipsis; }
.sess .stitle{ flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis;
  white-space:nowrap; color:var(--ink-dim); }
.sess .sdate{ color:var(--ink-faint); font-family:var(--mono); font-size:11px;
  white-space:nowrap; }
.sbtn{ display:inline-flex; align-items:center; justify-content:center;
  width:28px; height:28px; flex:none; border:1px solid var(--line-2);
  border-radius:4px; color:var(--ink-dim); text-decoration:none; font-size:13px; }
.sbtn:hover{ border-color:var(--ink-faint); color:var(--ink); }
.sbtn.xbtn:hover{ border-color:var(--c2); color:var(--c2); }
.sbtn.xbtn.armed{ border-color:var(--c2); color:var(--c2); }
.sspace{ width:28px; flex:none; }
.schev{ width:14px; height:14px; flex:none; color:var(--ink-mute);
  transition:transform .15s ease; }
.sess[open] .schev{ transform:rotate(180deg); }
.sess .sbody{ padding:2px 2px 12px 160px; color:var(--ink-dim);
  font-size:12px; line-height:1.6; }
.sess .sbody .srow{ display:flex; gap:12px; padding:1px 0; }
.sess .sbody .stime{ color:var(--ink-faint); font-family:var(--mono);
  font-size:11px; min-width:38px; flex:none; padding-top:2px; }
/* строка прошлой сессии на таймлайне (решение 17.07: «вёрстка кривая, нормальная
   кнопка вернуться, внутри навехерня»): узел-маркер по ЦЕНТРУ строки, а не висит
   сверху; возврат — ОДНА явная пилюля .sret, без второго кликабельного кружка */
.step.sln .m{ margin-top:14px; }
.sret{ flex:none; padding:4px 13px; font-size:11px; }
/* единый компонент кнопки — ПИЛЮЛЯ (форма ▶ запустить); ⟳ вернуться носит её же,
   разница primary/secondary только в амбер-акценте */
.abtn{ display:inline-flex; align-items:center; gap:7px; font-family:var(--mono);
  font-size:11.5px; font-weight:600; padding:6px 18px; border:1px solid var(--line-2);
  border-radius:999px; background:transparent; color:var(--ink-dim); text-decoration:none;
  white-space:nowrap; }
.abtn:hover{ border-color:var(--ink-faint); color:var(--ink); }
.abtn.primary{ border-color:var(--c2); color:var(--c2); }
.abtn.primary:hover{ border-color:var(--c2); background:rgba(200,120,10,.10); }
.abtn.danger{ border-color:var(--bad); color:var(--bad); }
.abtn.danger:hover{ border-color:var(--bad); background:rgba(224,101,90,.12); }
/* спойлеры дальше/пройдено — в колонну таймлайна, не к левому краю */
.road details.past>summary{ padding-left:76px; }
/* «подробнее»: плюс прижат к слову, не улетает вправо */
.tld.inl>summary{ display:inline-flex; align-items:center; gap:6px; }
.tld.inl>summary::after{ margin-left:0; }
/* УРОВЕНЬ ПРОЕКТОВ и ПОЛКА (канд. 75, нить «полка»): карточка дома компактной
   строкой (по образцу .bcard кита), ряды полки — по аккордеону .sess */
.pjgrid{ display:grid; grid-template-columns:repeat(2,1fr); gap:8px; }
.pjcard{ display:flex; align-items:center; gap:10px; border:1px solid var(--line);
  border-radius:3px; background:var(--bg-1); padding:10px 12px; cursor:pointer;
  transition:border-color .12s ease, background .12s ease;
  -webkit-tap-highlight-color:transparent; }
.pjcard:hover, .pjcard:focus-visible{ border-color:var(--line-2);
  background:var(--bg-2); outline:none; }
.pjcard .ic{ color:var(--ink-mute); flex:none; }
.pjcard .ic svg{ width:16px; height:16px; display:block; }
.pjcard .pjnm{ font-size:12px; color:var(--ink); overflow:hidden;
  text-overflow:ellipsis; white-space:nowrap; }
.pjcard .pjmeta{ margin-left:auto; color:var(--ink-mute); font-size:10px;
  letter-spacing:.06em; white-space:nowrap; flex:none; }
.pjwait{ color:var(--c2); }
/* МЕРЫ СИДА (решение 05): счёт у бейджа передачи на карте и полная строка блоков
   под узлом передачи в фокус-виде. Дырка — полый маркер в ink-faint: недостача
   должна ЧИТАТЬСЯ, а не считаться, поэтому цветом отделяем пустое от набранного. */
.seedn{ color:var(--ink-faint); }
.seedm{ display:block; margin-top:5px; font-family:var(--mono); font-size:10px;
  letter-spacing:.04em; color:var(--ink-mute); }
.seedm .sb{ white-space:nowrap; }
.seedm .sb.hole{ color:var(--ink-faint); }
.seedm .sb.free{ color:var(--ink-faint); font-style:italic; }
/* ВРЕМЕННАЯ плашка статуса нити снизу карты (решение 13.07) — только визуал:
   ждёт твоё решение (амбер, зовёт) · агент работает (зелёный, спокойно) ·
   отложена (приглушённая, дремлет). Карта нити становится колонкой: шапка + полоса. */
.pjcard.thr{ flex-direction:column; align-items:stretch; gap:9px; }
.pjcard.thr .pjtop{ display:flex; align-items:center; gap:10px; }
/* переносим ряд: у нити с висящей передачей кнопок ДВЕ (запустить + резюм), и на
   узкой карте они не влезали в строку — вторая уходит под первую, а у карт с одной
   кнопкой перенос не срабатывает (подпись .stlbl жмётся, места хватает) */
.pjstatus{ display:flex; align-items:center; flex-wrap:wrap; gap:7px; row-gap:8px;
  padding-top:9px;
  border-top:1px solid var(--line); font-family:var(--mono); font-size:10px;
  letter-spacing:.1em; text-transform:uppercase; color:var(--ink-mute); }
.pjstatus .dot{ width:6px; height:6px; border-radius:50%;
  background:var(--ink-faint); flex:none; }
.pjstatus.st-wait{ color:var(--c2); } .pjstatus.st-wait .dot{ background:var(--c2); }
.pjstatus.st-work .dot{ background:var(--ok); }
/* спит — дремлет сама (головы нет/остыла): полый кружок, глубже отступает */
.pjstatus.st-sleep{ color:var(--ink-faint); }
.pjstatus.st-sleep .dot{ background:transparent; border:1px solid var(--ink-faint); }
/* закрыта (loose-end: нить закрыта, чат ещё жив) — приглушённо, не демандит */
.pjstatus.st-done{ color:var(--ink-mute); }
.pjstatus.st-done .dot{ background:var(--ink-mute); }
/* запускается (слепая-но-живая: терминал поднят, claude-id ещё не связан) — кобальт,
   точка пульсирует, чтобы читалось «идёт подъём», не статичный статус */
.pjstatus.st-starting{ color:var(--c1); }
.pjstatus.st-starting .dot{ background:var(--c1); animation:stpulse 1.4s ease-in-out infinite; }
@keyframes stpulse{ 0%,100%{ opacity:1; } 50%{ opacity:.3; } }
/* полоса статуса: подпись растёт и жмёт кнопки К ПРАВОМУ КРАЮ единой группой
   (решение 13.07: раньше у обеих был margin-left:auto → свободное место делилось,
   ☾ висла посередине). Кнопки — БЕЗ auto-margin, их толкает вправо .stlbl. */
.pjstatus .stlbl{ flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis;
  white-space:nowrap; }
/* круглые кнопки полосы — ВТОРИЧНЫЕ жесты: ✕ закрыть/отпустить · ★ избранное.
   Вход в сессию из этого ряда ушёл: он стал кнопкой .pjresume ниже (решение 29.07). */
.pjhold{ width:22px; height:22px; border-radius:50%; flex:none;
  border:1px solid var(--line-2); display:inline-flex; align-items:center;
  justify-content:center; color:var(--ink-mute); text-decoration:none;
  transition:border-color .12s ease, color .12s ease, background .12s ease; }
.pjhold svg{ display:block; }
.pjhold:hover{ border-color:var(--ink-faint); color:var(--ink-dim); background:var(--bg-2); }
/* «резюм» с карточки нити (решение 29.07: «кнопка перехода слишком маленькая, там
   где спит — сделать побольше, иконка + надпись, пусть будет нормальная»). Это
   ГЛАВНОЕ действие карты, поэтому не свой значок, а .abtn — единый компонент
   доски «форма ▶ запустить»; тело взято у кита (.btn-secondary: заливка --bg-2,
   чернила --ink), чтобы кнопка читалась предметом, а не контуром.
   Амбер НЕ по умолчанию: c2 = «нужен ты сейчас», один hot-кластер на вид (закон
   цвета в шапке шаблона) — .primary достаётся только нити, которая ждёт слова,
   и висящей передаче. Иначе 30 карточек кричали бы разом.
   Полоса статуса — капс с трекингом, и кнопка наследовала его молча: выходил
   не жест, а разросшаяся подпись. Возвращаем букву кнопки (строчная, трекинг
   кита .btn) — иерархия читается: подпись тихая капсом, действие живое словом. */
.pjresume{ flex:none; padding:7px 15px; background:var(--bg-2); color:var(--ink);
  text-transform:none; letter-spacing:.01em; }
.pjresume svg{ width:15px; height:15px; }
.pjrow{ display:flex; align-items:center; gap:12px; padding:9px 2px;
  border-bottom:1px solid var(--line-2); cursor:pointer; }
.pjrow:hover .pjgoal{ color:var(--ink); }
.pjrow .pjtag{ color:var(--ink); font-size:12px; white-space:nowrap;
  flex:none; min-width:64px; }
.pjrow .pjgoal{ flex:1; min-width:0; color:var(--ink-dim); font-size:12px;
  overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.pjrow .pjage{ color:var(--ink-faint); font-family:var(--mono); font-size:11px;
  white-space:nowrap; flex:none; }
/* бейдж происхождения кандидата (решение 13.07): ← из нити · ↗ из проекта · рукой */
.csrc{ color:var(--ink-faint); font-family:var(--mono); font-size:10px;
  letter-spacing:.04em; white-space:nowrap; flex:0 1 auto; min-width:0;
  max-width:220px; overflow:hidden; text-overflow:ellipsis; }
.csrc.cross{ color:var(--c2); opacity:.85; }
/* «когда кинули» кандидата — в размер бейджа происхождения, рядом с ним, тише */
.pjage.cwhen{ font-size:10px; }
.pjrow .pjage.hot{ color:var(--c2); }
.pjcard.move{ border-color:var(--c2-ring); }
.pjcard .pjmeta.hot{ color:var(--c2); }
.pjbadge{ color:var(--ink-mute); font-size:10px; flex:none; }
.pjproj{ color:var(--ink-mute); font-size:10px; letter-spacing:.06em;
  white-space:nowrap; flex:none; }
/* чипы вида «всё/сессии» — по киту: углы 4px, mono-капс, без пилюль (12.07) */
.vbtns{ display:flex; gap:8px; margin:0 0 14px; }
.vbtn{ background:none; border:1px solid var(--line-2); border-radius:4px;
  color:var(--ink-mute); font-family:var(--mono); font-size:10px;
  letter-spacing:.14em; text-transform:uppercase; padding:5px 12px; cursor:pointer; }
.vbtn:hover{ color:var(--ink-dim); border-color:var(--ink-faint); }
.vbtn.on{ color:var(--ink); border-color:var(--ink-faint); background:var(--bg-2); }
/* строка живой сессии у курсора: ⟳ прижата к правому краю, не за текстом */
.sessrow{ display:flex; align-items:center; gap:10px; margin-top:6px;
  color:var(--ink-mute); }
.sessrow .sbtn{ margin-left:auto; }
.pjsec{ margin:0 0 20px; }
summary.pjrow{ align-items:center; }
/* заголовок кандидата стоит в позиции тега и бывает длинным слаг-именем у легаси-
   кандидатов (полка tide-stack) — не даём ему раздувать ряд в горизонт: кап +
   многоточие, полное имя по hover-title и в раскрытой сути (решение 13.07) */
summary.pjrow .pjtag{ flex:0 1 auto; min-width:0; overflow:hidden; text-overflow:ellipsis; }
/* табы вида сверху (решение 12.07): стол ⇄ проекты, переключение и клавишей M */
.vtabs{ display:flex; gap:18px; margin:18px 0 6px; border-bottom:1px solid var(--line); }
.vtab{ background:none; border:none; border-bottom:2px solid transparent;
  color:var(--ink-mute); font-family:var(--mono); font-size:11px;
  letter-spacing:.14em; text-transform:uppercase; padding:6px 2px 8px;
  margin-bottom:-1px; cursor:pointer; }
.vtab:hover{ color:var(--ink-dim); }
.vtab.on{ color:var(--ink); border-bottom-color:var(--c1); }
/* табы-фильтр потока нитей по проекту (решение 14.07: «прям здесь посмотреть нити
   только по конкретным проектам») — тот же язык, что .vtabs, но компактнее:
   секционный фильтр под slabel, не переключатель экранов */
.ptabs{ display:flex; gap:10px; margin:12px 0 10px; flex-wrap:wrap; }
.ptab{ background:none; border:none; border-bottom:1px solid transparent;
  color:var(--ink-mute); font-family:var(--mono); font-size:9.5px;
  letter-spacing:.1em; text-transform:uppercase; padding:1px 1px 3px; cursor:pointer; }
.ptab:hover{ color:var(--ink-dim); }
.ptab.on{ color:var(--ink); border-bottom-color:var(--c1); }
.ptab .n{ color:var(--ink-faint); letter-spacing:0; }
/* фильтр прячет карточку/секцию атрибутом hidden — но их собственный display
   (flex/grid) перебивает браузерное display:none от hidden (решение 14.07:
   «нажимаю — не фильтрует»); класс+атрибут специфичнее и возвращает hidden смысл */
.pjcard[hidden], .pjgrid[hidden], .slabel[hidden], .ptabs[hidden]{ display:none; }
/* фильтр стола по проекту — селект в шапке слева от шестерёнки (решение 17.07) */
.tbctl{ display:flex; align-items:center; gap:10px; }
.pfilter{ background:none; border:1px solid var(--line); border-radius:6px;
  padding:4px 10px; font-family:var(--mono); font-size:11.5px;
  letter-spacing:.04em; color:var(--ink-dim); cursor:pointer; outline:none;
  -webkit-appearance:none; appearance:none; max-width:180px; }
.pfilter:hover{ color:var(--ink); border-color:var(--line-2); }
.pfilter option{ background:var(--bg-1); color:var(--ink); letter-spacing:0; }
/* шестерёнка настроек в шапке — сосед палитры, тот же тихий язык (решение 14.07) */
.setbtn{ background:none; border:1px solid var(--line); border-radius:6px;
  width:26px; height:26px; display:inline-flex; align-items:center;
  justify-content:center; color:var(--ink-mute); cursor:pointer; margin-left:0; }
.setbtn:hover{ color:var(--ink-dim); border-color:var(--line-2); }
.setbtn svg{ width:14px; height:14px; display:block; }
/* строка настройки в модалке: текст слева, тумблер справа */
.setrow{ display:flex; align-items:center; justify-content:space-between; gap:16px;
  padding:12px 0; border-top:1px solid var(--line); }
.setrow:first-of-type{ border-top:none; }
.setrow .t{ color:var(--ink); font-size:13px; }
.setrow .d{ color:var(--ink-mute); font-size:11.5px; margin-top:3px; line-height:1.5; }
.setrow input[type=checkbox]{ accent-color:var(--c1); width:16px; height:16px;
  flex:none; cursor:pointer; }
/* openspec-чеклист ВНУТРИ таймлайна (шаг 4, решение 12.07): задачи шага под его
   волной — mono-капс подпись, галки хайрлайн, done приглушён и зачёркнут */
.cksec{ margin:8px 0 2px; }
.cksec .cklbl{ color:var(--ink-mute); font-family:var(--mono); font-size:10px;
  letter-spacing:.12em; text-transform:uppercase; margin:2px 0 6px; }
.ckrow{ display:flex; gap:10px; align-items:baseline; padding:3px 0;
  color:var(--ink-dim); font-size:12px; line-height:1.5; }
.ckrow .ckmark{ flex:none; width:14px; text-align:center; color:var(--ink-mute);
  font-size:11px; }
.ckrow.ckdone .ckmark{ color:var(--c1); }
.ckrow.ckdone .cktext{ color:var(--ink-mute); text-decoration:line-through; }
.ckrow .cktext{ min-width:0; }
/* гейт-подтверждение (шаг 4): критерий «на что штамп» + кнопка в одном блоке,
   левая полоса в акценте гейта — видно, что именно подтверждаешь */
.gateconfirm{ margin-top:12px; padding:9px 12px; border-left:2px solid var(--c1);
  background:var(--bg-2); border-radius:0 4px 4px 0; }
.gateconfirm .gatecrit{ display:block; color:var(--ink-dim); font-size:12px;
  line-height:1.55; margin:0 0 9px; }
.gateconfirm .gatecrit b{ color:var(--ink); font-weight:600; }
.gateconfirm .gatebtn{ margin-top:0; }
/* «✓ завалидировать» — валидация гейта с доски (шаг 4): тихая пилюля в
   акценте гейта (--c1), тон как «закроется гейтом» */
.gatebtn{ display:inline-block; margin-top:10px; padding:4px 14px;
  border:1px solid var(--c1); border-radius:999px; color:var(--c1);
  font-family:var(--mono); font-size:11px; letter-spacing:.04em;
  text-decoration:none; cursor:pointer; }
.gatebtn:hover{ background:var(--c1); color:var(--bg-1); }
"""

CHEVRON = ('<svg class="schev" viewBox="0 0 24 24" fill="none" '
           'stroke="currentColor" stroke-width="2" stroke-linecap="round" '
           'stroke-linejoin="round"><path d="m6 9 6 6 6-6"/></svg>')
ICON_THREAD = f'<svg viewBox="0 0 24 24" {S}><path d="M12 3 21 8 12 13 3 8Z"/><path d="M3 12l9 5 9-5"/></svg>'
ICON_ROUTINE = f'<svg viewBox="0 0 24 24" {S}><circle cx="12" cy="12" r="7"/><path d="M12 8v4l3 2"/></svg>'
ICON_PROJECT = f'<svg viewBox="0 0 24 24" {S}><path d="M3 6h6l2 2h10v11H3Z"/></svg>'
# иконки кнопок полосы статуса (решение 13.07): SVG вместо текст-глифов — чёткие,
# центрируются ровно. play — залитый треугольник (центроид ~центр + оптический сдвиг
# в CSS). ☾/↑ умерли вместе с фичей «отложенные» (решение 16.07); их слот занял ★.
ICON_PLAY = ('<svg viewBox="0 0 24 24" width="10" height="10" fill="currentColor">'
             '<path d="M9 7v10l9-5z"/></svg>')  # центроид (9,7)(9,17)(18,12) = (12,12)
_STAR_PATH = ('M12 2l2.9 6.26 6.6.71-4.9 4.47 1.35 6.5L12 16.67 6.05 19.94 '
              '7.4 13.44 2.5 8.97l6.6-.71Z')
ICON_STAR = ('<svg viewBox="0 0 24 24" width="13" height="13" fill="none" '
             'stroke="currentColor" stroke-width="1.6" stroke-linecap="round" '
             'stroke-linejoin="round"><path d="{0}"/></svg>').format(_STAR_PATH)
ICON_STAR_ON = ('<svg viewBox="0 0 24 24" width="13" height="13" '
                'fill="currentColor"><path d="{0}"/></svg>').format(_STAR_PATH)
# ⟳ вернуться в сессию — крепкий значок в кит, а не тонкий символ (lucide
# rotate-ccw): та же весовая линия, что у ▶ у кандидата
ICON_RESUME = ('<svg viewBox="0 0 24 24" width="15" height="15" fill="none" '
               'stroke="currentColor" stroke-width="2" stroke-linecap="round" '
               'stroke-linejoin="round"><polyline points="1 4 1 10 7 10"/>'
               '<path d="M3.5 15a9 9 0 1 0 2.1-9.4L1 10"/></svg>')
# ↗ провалиться к ведущему агенту работы — та же весовая линия, что у ★ и ⟳
ICON_DIVE = ('<svg viewBox="0 0 24 24" width="12" height="12" fill="none" '
             'stroke="currentColor" stroke-width="2.2" stroke-linecap="round" '
             'stroke-linejoin="round"><line x1="7" y1="17" x2="17" y2="7"/>'
             '<polyline points="8 7 17 7 17 16"/></svg>')
# ── иконки ряда вкладок (решение 01.08, работа 36) ─────────────────────────────
# «Каждой вкладке иконку маленькую слева тематическую; у нас в дизайне какие-то
# иконки были, нужны хорошие». Иконки в доме ЕСТЬ — ICON_THREAD и ICON_PROJECT
# берём как есть (нити и проекты уже помечены ими на карточках, и второй значок
# для той же вещи развёл бы язык надвое). Недостающие пять рисованы по тому же
# киту и по DESIGN-LANGUAGE фабрики: сетка 24, штрих `S` (1.4, currentColor, без
# заливок), схема, а не картинка. Размер и цвет иконка берёт у своей вкладки —
# currentColor, значит на активной она загорается вместе с её словом.
# Мотив у каждой — то, ЧТО на вкладке лежит, а не абстракция: стол входящих —
# лоток, работа — лист с галочкой, новости — лента с кадром, доска — карандаш,
# навыки — раскрытая методичка.
ICON_INBOX = (f'<svg viewBox="0 0 24 24" {S}><path d="M4 5h16v14H4Z"/>'
              '<path d="M4 13h4.5l1.5 2.5h4l1.5-2.5H20"/></svg>')
ICON_WORKSHEET = (f'<svg viewBox="0 0 24 24" {S}><path d="M5 3h9l5 5v13H5Z"/>'
                  '<path d="M14 3v5h5"/><path d="m9 13.5 2 2 4-4"/></svg>')
ICON_FEED = (f'<svg viewBox="0 0 24 24" {S}><path d="M3 6h13v12H3Z"/>'
             '<path d="m16 11 5-3v8l-5-3Z"/></svg>')
ICON_PENCIL = (f'<svg viewBox="0 0 24 24" {S}><path d="M4 20v-4L16 4l4 4L8 20Z"/>'
               '<path d="m14 6 4 4"/></svg>')
ICON_SKILL = (f'<svg viewBox="0 0 24 24" {S}>'
              '<path d="M12 7C10.4 5.6 8.3 5.2 6 5.2v12c2.3 0 4.4.4 6 1.8"/>'
              '<path d="M12 7c1.6-1.4 3.7-1.8 6-1.8v12c-2.3 0-4.4.4-6 1.8"/>'
              '</svg>')


def esc(s):  # html-escape для вклейки текстов паспортов
    return _html.escape(str(s or ""), quote=True)


# ── человеческий слой (change human-board) ──────────────────────────────────
# Доска отвечает ЧЕЛОВЕКУ, не агенту: служебные записи агентов (пульсы, роли,
# «чей ход») переводятся в язык владельца, а жаргон не светится в свёрнутом виде.

# запретный список видимого слоя (спека board-view). Хранятся ОСНОВЫ слов —
# ловим падежи: «экзекьюци(я/и)», «оркестратор(а/ы)», «оффер(ы/а)»…
JARGON = ("ярус", "отлив", "закрома", "экзекьюци", "оркестратор", "бинарник",
          "оффер", "слаг", "канон-долг", "ростер", "дистилл", "воркер")


def _has_jargon(text):
    low = (text or "").lower()
    return any(w in low for w in JARGON)


def _human(text):
    """Видимый слой без служебного жаргона (шаг 3): жаргонная строка НЕ светится
    свёрнуто — возвращаем пусто (место вызова уводит её под «подробнее» или
    показывает очищенную строку «ход»). Чистый текст проходит как есть."""
    return "" if _has_jargon(text) else (text or "")


def _role_word(role):
    """Роль сессии словами человека (шаг 4): планирование→планирует,
    экзекьюция→делает. Полное слово роли остаётся тултипом на месте вызова."""
    r = (role or "").lower()
    if r.startswith("план"):
        return "планирует"
    if r.startswith("экзек"):
        return "делает"
    return role or ""


def _owner_tokens():
    """Как ЭТОТ человек называет себя в своих же паспортах — стеблями.

    Люди пишут в планах «ждём хозяина», «гейт такого-то» — своим именем, — и
    доске это надо узнавать: по имени владельца видно, что ход человеческий, а
    в подписи шага само имя светиться не должно (решение 12.07: «личность не
    важна»).

    Имя берём из ФАЙЛА, а не из кода: `<дом>/.tide/instance-tokens` — тот же
    список, по которому `tide verify --portable` проверяет, что в пакет не
    уехало личное. Один список на обе задачи: пропиши себя один раз, и доска
    узнаёт тебя, а ворота ловят твоё имя в исходниках. В коде имени нет вовсе,
    поэтому доска уезжает в коробку как есть, а у нового человека, который себя
    не вписал, просто нет этой эвристики — и ничего не ломается.

    Берём ТОЛЬКО человеческую половину файла — до первого комментария, где
    хозяин заводит раздел про проекты. Имена проектов там тоже есть, и они
    воротам нужны, а доске вредны в обе стороны: шаг «выкатить <проект>» стал
    бы «ходом человека», а из подписи шага пропало бы название. Не нашли такого
    комментария — считаем человеческим весь файл; ошиблись в эту сторону —
    доска чуть охотнее зовёт человека, ошиблись бы в другую — молча съедала бы
    слова из подписей.

    Ничего не получилось (нет файла, нечитаемо) — пустой ответ: эвристика
    просто не работает, и это нормально. У нового человека, который себя не
    вписывал, так и будет.
    """
    out = []
    try:
        f = HOME / ".tide" / "instance-tokens"
        if not f.is_file():
            return ()
        for raw in f.read_text(encoding="utf-8", errors="replace").splitlines():
            head = raw.strip()
            if head.startswith("#") and "проект" in head.lower():
                break
            s = raw.split("#", 1)[0].strip().lower()
            if s and len(s) >= 4:
                out.append(s)
    except OSError:
        return ()
    return tuple(out)


_OWNER = _owner_tokens()

# слова, по которым ход считается ЧЕЛОВЕЧЕСКИМ (эвристика шага 1): нужен его
# вердикт/подпись/взгляд, либо шаг стоит на гейте и ждёт. Имя самого человека
# приезжает из его файла маркеров (_owner_tokens), а не лежит в коде.
_MOVE_YOU = _OWNER + ("вердикт", "подпис", "реши", "жд", "гейт", "холодн",
                      "взгляд", "апрув", "прими", "ответ", "согласу")


def _whose_move(t, live):
    """Чей сейчас ход (шаг 1): эвристика по `next` живой сессии (фолбэк — курсор)
    + состояние шага. Возврат ('твой'|'агента', короткий текст без жаргона) либо
    None, если у нити нет живой сессии/пульса."""
    if not live:
        return None
    nxt = live.get("next") or ""
    src = nxt or live.get("cursor") or ""
    if not src:
        return None
    turn = "твой" if any(w in src.lower() for w in _MOVE_YOU) else "агента"
    # текст-подсказку берём ТОЛЬКО из next: курсор показывается отдельной строкой
    # (road) или карточкой (plan), дублировать его в «ход» незачем. Нет next —
    # одна честная метка хода, детали несёт курсор/карточка.
    text = ""
    if nxt:
        parts = [p.strip(" .·—") for p in re.split(r"\s*(?:→|;|·|\|)\s*", nxt)
                 if p.strip(" .·—")]
        if turn == "твой":  # кусок, где реально ждут человека
            seg = next((p for p in parts if any(w in p.lower() for w in _MOVE_YOU)),
                       parts[-1] if parts else nxt)
        else:               # ход агента — первое, за что он берётся
            seg = parts[0] if parts else nxt
        seg = re.sub(r"^(утром|днём|вечером|ночью|потом|затем|далее|сейчас)\s*:?\s*",
                     "", seg, flags=re.I)
        text = _human(seg).strip()  # жаргонный кусок гаснет → метка без хвоста
        # личность не светится (решение 12.07: «личность не важна»): «гейт владельца»
        # → «гейт», одиночное имя вычищается
        for _tok in _OWNER:
            text = re.sub(r"гейт\s+" + _tok + r"\w*", "гейт", text, flags=re.I)
            text = re.sub(r"\s*\b" + _tok + r"\w*\b", "", text, flags=re.I)
        text = text.strip(" :·—,")
    # обрезка ПО ГРАНИЦЕ СЛОВА с многоточием — не «ясно «что происходит и ч»
    if len(text) > 64:
        text = text[:64].rsplit(" ", 1)[0].rstrip(",:;—- ") + "…"
    return turn, text


def _move_line(t, live):
    """Видимая строка «ход: …» для блока «сейчас» (шаг 2): второй строкой после
    итога. Ход человека — единственный амбер вью (то, что ждёт именно тебя)."""
    mv = _whose_move(t, live)
    if not mv:
        return ""
    turn, text = mv
    col = "var(--c2)" if turn == "твой" else "var(--ink-mute)"
    tail = " — {0}".format(esc(text)) if text else ""
    return ('<span class="gs" style="margin-top:6px">'
            '<span style="color:{0};text-transform:uppercase;letter-spacing:.12em;'
            'font-size:10px">ход · {1}</span>{2}</span>'.format(col, turn, tail))


def read_field(text, key):
    m = re.search(r"^{0}:\s*(.+)$".format(re.escape(key)), text, re.M)
    v = (m.group(1).strip() if m else "")
    return "" if v.startswith("<") else v


def section(text, title):
    m = re.search(r"^##\s+{0}.*?\n(.*?)(?=^##\s|\Z)".format(re.escape(title)), text, re.M | re.S)
    body = (m.group(1).strip() if m else "")
    return "" if body.startswith("<") else body


def roster_projects():
    out = []
    f = HOME / "roster.md"
    if not f.is_file():
        return out
    for line in f.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "|" not in s:
            continue
        parts = [p.strip() for p in s.split("|")]
        name, path = parts[0], Path(parts[1]).expanduser()
        status = parts[2] if len(parts) > 2 else ""
        if "archived" in status or not path.is_dir():
            continue  # архив = единственная операция скрытия (закон 46)
        out.append((name, path))
    return out


def sess_label(dirname):
    """Стандарт айдишника сессии (решение 09.07: «правки-6, правки-7 — у каждого
    чата номер и чёткое название»): №NN · слово. Номер — порядок в нити (даёт
    станок), слово — короткое имя. Нормализует и кривые имена CLI-эпохи:
    12-12-pravki-12 → (№12, pravki)."""
    m = re.match(r"^(\d+)-(.*)$", dirname.strip("_"))
    if not m:
        return "", dirname.strip("_")
    num, rest = m.group(1), m.group(2)
    rest = re.sub(r"^{0}-".format(num), "", rest)   # 12-12-pravki → 12-pravki убрал дубль
    rest = re.sub(r"-?{0}$".format(num), "", rest)  # pravki-12 → pravki
    return num, (rest or dirname.strip("_"))


def _sess_name(slug, dirname):
    """Отображаемое имя сессии — НОМЕР + ЕЁ СОБСТВЕННЫЙ слаг из паспорта-арки
    (01-build, 02-priem), правда арки, не вывод от нити (cand 116: '01-build'
    рисовался '01-payouts', цель терялась — решение 16.07). Слаг нити — фолбэк
    для дир-имён без собственного слова (голый номер)."""
    d = (dirname or "").strip("_")
    m = re.match(r"(\d+)-?(.*)", d)
    if not m:
        return d
    n, own = m.group(1), (m.group(2) or "").strip("-")
    word = own or slug
    return "{0}-{1}".format(n, word) if word else d


def _offloaded_ts(text):
    """Час последнего offload из паспорта (ISO-стамп поля `offloaded-at`) → ts, иначе 0.

    Честный пульс-фолбэк, когда живого транскрипта нет: `offloaded-at` пишется в момент
    реального offload и не дёргается массовым touch — в отличие от mtime файла, из-за
    которого мёртвый тред-призрак светился «26 мин» (cand 09)."""
    raw = (read_field(text, "offloaded-at") or "").strip()
    if not raw or raw == "0":
        return 0.0
    try:
        return datetime.fromisoformat(raw).timestamp()
    except (ValueError, TypeError):
        return 0.0


def _birth_ts(path):
    """Время СОЗДАНИЯ файла (st_birthtime; фолбэк mtime) — стабильный «возраст с рождения»
    для нити/сессии без пульса (свежесозданная нить ≈ «только что», не epoch)."""
    try:
        st = path.stat()
        return getattr(st, "st_birthtime", None) or st.st_mtime
    except OSError:
        return 0.0


def read_sessions(tdir):
    sess = []
    sub = tdir / "arcs"
    if not sub.is_dir():
        return sess
    for d in sorted(sub.iterdir()):
        pp = d / "arc.md"
        if not d.is_dir() or not pp.is_file():
            continue
        text = pp.read_text(encoding="utf-8", errors="ignore")
        closed = d.name.startswith("__")
        num, short = sess_label(d.name)
        sess.append({
            # имя без __закрывающей__ обёртки — совпадает с записями швов
            "name": d.name.strip("_"), "dir": d.name, "closed": closed,
            # айдишник для глаза: «слово номер» — как человек сам называл чаты
            # («правки 6, правки 7»), номер без нулей, без канцелярского №
            "label": ("{0} {1}".format(short, int(num)) if num else short),
            "title": read_field(text, "title") or read_field(text, "goal"),
            "cursor": " ".join(section(text, "cursor").split())[:300],
            # что сделано в сессии — для спойлера прошлого (решение 09.07):
            # вкладку можно закрыть, когда содержимое видно с доски
            "summary": " ".join(section(text, "summary").split())[:300],
            "next": " ".join(section(text, "next").split())[:300],
            "context": [ln.strip("- ").strip() for ln in section(text, "context").splitlines() if ln.strip()][-3:],
            "mtime": pp.stat().st_mtime,
            # honest activity fallbacks (cand 09): offload stamp + creation time, NOT mtime
            "offloaded": _offloaded_ts(text),
            "birthtime": _birth_ts(pp),
            "from": read_field(text, "from"),
            "claude": read_field(text, "claude-session"),
            # голова отпущена рукой (✕ с доски): сессия остаётся в журнале
            # визитов (⟳ живёт), но головой больше не считается — структура
            # не тронута, убито только внимание (закон: смерть — руками)
            "dismissed": read_field(text, "dismissed"),
            # растворена механикой (I6): отдала нить по хендоффу, держит преемник;
            # ⟳ остаётся (живую вкладку фокусит), respawn запрещён на стороне tide
            "dissolved": read_field(text, "dissolved"),
            # роль сессии (правка владельца 08.07): планирование / экзекьюция —
            # чип на карточке, чтобы в двух окнах не путаться, кто есть кто
            "role": read_field(text, "role"),
        })
    sess.reverse()  # новейшие сверху — везде (закон дома)
    return sess


def read_plan(tdir):
    """План нити волнами (<thread>/plan.md, закон 47): final + волны + патчи.

    Формат волны: ``- [x|>| ] N. имя | что делается | результат волны``.
    План иммутабелен: правки копятся версиями в ## патчи — тут только чтение.
    """
    f = tdir / "plan.md"
    if not f.is_file():
        return None
    text = f.read_text(encoding="utf-8", errors="ignore")
    m = re.search(r"^final:\s*(.+?)(?=^\s*$|^##)", text, re.M | re.S)
    final = " ".join(m.group(1).split()) if m else ""
    waves = []
    for ln in text.splitlines():
        mm = re.match(r"^- \[([x> ])\]\s*(?:(\d+)\.\s*)?([^|]+)\|([^|]+)\|(.+)$", ln)
        if mm:
            name = mm.group(3).strip()
            waves.append({"state": {"x": "done", ">": "now"}.get(mm.group(1), "todo"),
                          # номер волны и ∥-связки — НЕСУЩИЕ (правило владельца 08.07):
                          # развилка существует, только если оформлена в плане
                          "num": mm.group(2) or "",
                          "par": re.findall(r"∥(\d+)", name),
                          "name": name, "doing": mm.group(4).strip(),
                          "result": mm.group(5).strip(), "desc": "", "check": "", "passed": ""})
            continue
        dm = re.match(r"^\s+(описание|проверка|гейт|гейт-пройден):\s*(.+)$", ln)
        if dm and waves:
            key = {"описание": "desc", "проверка": "check",
                   "гейт": "check", "гейт-пройден": "passed"}[dm.group(1)]
            waves[-1][key] = dm.group(2).strip()
    patches = [ln.strip("- ").strip() for ln in
               text.partition("## патчи")[2].splitlines() if ln.strip().startswith("-")]
    version = (re.search(r"·\s*(v\d+)", text) or [None, ""])[1]
    return {"final": final, "waves": waves, "patches": patches, "version": version} if waves else None


def read_plan_steps(tdir):
    """Шаги плана нити для РАБОТ (работа 44, слово человека 07.08: «хочется чтобы
    работа что сейчас делается бралась в рамках куда идём»).

    Отдельный читатель, а не read_plan, — и вот почему. read_plan разбирает
    ОДНОСТРОЧНУЮ волну закона 47 (`- [>] N. имя | что делается | результат`) и на
    живых планах молчит: их пишет рука, строки в них перенесены по ширине окна,
    и первая же строка шага обрывается на середине — регулярка не сходится, и
    read_plan честно отдаёт None. Чинить её тут нельзя: на ней стоит таймлайн со
    своими развилками, гейтами и ∥-связками, а вкладке «работы» из всего плана
    нужны три вещи — номер, имя и «результат». Поэтому свой разбор, терпимый к
    переносам, и общий с read_plan только формат самой строки.

    Склейка — по пустой строке, а не по отступу: два живых плана (release и
    paint) заворачивают продолжение по-своему, и держаться отступа значило бы
    работать ровно на один из них. Пункт начинается с `- [ ]` и тянется, пока
    строки не кончатся пустой; всё, что за `## …`, — уже не шаги.

    Наружу: {"final": чем всё кончится, "items": [{num, state, name, result}]}.
    None — плана нет вовсе (вкладка тогда остаётся плоским списком, как была).
    Пустой items при живом final — план есть, но шаги в нём не читаются: группы
    не рисуем, а «к чему ведёт» у работы всё равно есть чем ответить."""
    f = tdir / "plan.md"
    if not f.is_file():
        return None
    text = f.read_text(encoding="utf-8", errors="ignore")
    m = re.search(r"^final:\s*(.+?)(?=^\s*$|^##)", text, re.M | re.S)
    final = " ".join(m.group(1).split()) if m else ""
    sec = re.search(r"^##\s*шаги\s*$(.*?)(?=^##\s|\Z)", text, re.M | re.S)
    body = sec.group(1) if sec else text
    raw, live = [], False
    for ln in body.splitlines():
        s = ln.strip()
        if re.match(r"^-\s*\[[x>\s]\]", s):
            raw.append(s); live = True; continue
        # `описание:`/`проверка:`/`гейт:` — не перенос строки, а СОБСТВЕННЫЕ поля
        # шага (их читает read_plan для таймлайна). Приклей их к «результату», и
        # человек прочёл бы под работой абзац разведки вместо одной фразы, ради
        # которой строка и заведена
        if re.match(r"^(описание|проверка|гейт|гейт-пройден)\s*:", s):
            live = False; continue
        if live and s:
            raw[-1] += " " + s; continue
        live = False
    items = []
    for s in raw:
        mm = re.match(r"^-\s*\[([x>\s])\]\s*(?:(\d+)[.)]\s*)?(.*)$", s)
        if not mm:
            continue
        parts = [p.strip() for p in mm.group(3).split("|")]
        # «результат: «…»» — служебное слово и кавычки говорят про формат файла,
        # человеку на доске нужен сам текст
        res = re.sub(r"^результат\s*:\s*", "", parts[-1]) if len(parts) > 2 else ""
        items.append({
            "num": mm.group(2) or "",
            "state": {"x": "done", ">": "now"}.get(mm.group(1).strip(), "todo"),
            "name": " ".join(parts[0].split()),
            "result": " ".join(res.strip().strip("«»").split()),
        })
    if not final and not items:
        return None
    return {"final": final, "items": items}


def read_spec_checklist(t):
    """Чеклист linked openspec change (шаг 4): поле `spec: <codebase-rel>:
    <change>` в паспорте нити → задачи tasks.md, разложенные ПО ШАГАМ (секции
    `## Шаг N` → волна N; секция без номера цепляется к последнему номерному
    шагу). None, если линка нет, путь битый или change не найден — доска не
    падает. Живёт в таймлайне: задачи вкладываются в свою волну (решение 12.07)."""
    spec = (t.get("spec") or "").strip()
    if ":" not in spec:
        return None
    rel, _, change = spec.partition(":")
    rel, change = rel.strip(), change.strip()
    if not rel or not change or ".." in rel or ".." in change or "/" in change:
        return None
    proot = t["path"].parents[2]
    base = (proot / rel / "openspec" / "changes").resolve()
    cdir = (base / change).resolve()
    tasks = cdir / "tasks.md"
    try:
        if cdir.parent != base or not tasks.is_file():
            return None
        text = tasks.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    by_wave, cur, done, total = {}, None, 0, 0
    for line in text.splitlines():
        h = re.match(r"^##\s+(.+)$", line)
        if h:
            wm = re.search(r"Шаг\s+(\d+)", h.group(1))
            if wm:
                cur = wm.group(1)  # номерная секция → её волна; иначе держим прежнюю
            continue
        m = re.match(r"^\s*-\s*\[([ xX])\]\s+(.+)$", line)
        if m:
            d = m.group(1).lower() == "x"
            total += 1
            done += 1 if d else 0
            if cur is not None:
                by_wave.setdefault(cur, []).append(
                    {"text": m.group(2).strip(), "done": d})
    if not total:
        return None
    return {"change": change, "done": done, "total": total, "by_wave": by_wave}


def _wave_tasks(tasks):
    """Задачи openspec-шага ВНУТРИ его волны в таймлайне (решение 12.07: чеклист
    живёт в дороге, не отдельным блоком снизу). Галки + прогресс шага; текст
    задачи — сквозь жаргон-фильтр (служебное не светится)."""
    if not tasks:
        return ""
    d = sum(1 for x in tasks if x["done"])
    rows = []
    for x in tasks:
        disp = _human(x["text"])
        rows.append('<div class="ckrow {c}"><span class="ckmark">{b}</span>'
                    '<span class="cktext">{t}</span></div>'.format(
                        c="ckdone" if x["done"] else "ckopen",
                        b="✓" if x["done"] else "○",
                        t=esc(disp) if disp else "—"))
    return ('<div class="cksec"><div class="cklbl">чеклист · {d}/{n}</div>{r}</div>'
            .format(d=d, n=len(tasks), r="".join(rows)))


def read_threads(pname, proot):
    out = []
    arcs = proot / ".tide" / "arcs"
    if not arcs.is_dir():
        return out
    for d in sorted(arcs.iterdir()):
        if not d.is_dir() or d.name == "candidates" or d.name.startswith("__"):
            continue
        goals = sorted(d.glob("*-goal.md"))
        pp = goals[-1] if goals else d / "arc.md"
        if not pp.is_file():
            continue
        text = pp.read_text(encoding="utf-8", errors="ignore")
        goal = read_field(text, "goal")
        # тег нити (решение 12.07): короткий позывной латиницей — DECK, MITE…
        tag = read_field(text, "tag").upper()
        kind = read_field(text, "kind") or "arc"
        sessions = read_sessions(d)
        if not goal and not sessions:
            continue  # болванка без жизни — доске не место
        # honest freshness = newest real session pulse, thread base = its offload/birth,
        # never file mtime (cand 09)
        fresh = max([_offloaded_ts(text) or _birth_ts(pp)] + [_session_pulse(s) for s in sessions])
        out.append({
            "id": re.sub(r"[^a-z0-9]", "", (pname + d.name).lower()),
            "proj": pname, "dir": d.name,
            "slug": re.sub(r"^\d+-@?", "", d.name),
            "goal": goal, "tag": tag, "kind": kind, "sessions": sessions, "fresh": fresh,
            # шаги плана — отдельным читателем (работа 44): их спрашивает
            # вкладка «работы», а не таймлайн, и живой формат другой
            "plan": read_plan(d), "steps": read_plan_steps(d),
            "path": d, "spec": read_field(text, "spec"),
        })
    return out


def _real_goal(t):
    """Человеческая цель нити или '' если её по сути нет. Авто-имя целью НЕ
    считается: goal, совпавший со слагом/тегом самой нити (напр. 'debug_deck'
    у нити debug-deck), — это её имя, а не цель, и доске его как цель показывать
    нечего. Плейсхолдеры '<...>' read_field уже отсёк. Пустой результат →
    вызывающий просто не рисует узел/подзаголовок цели."""
    g = (t.get("goal") or "").strip()
    if not g:
        return ""
    # нормализуем ТОЛЬКО разделители (пробел/_/-), НЕ буквы — иначе кириллица
    # (реальные цели по-русски) вырезается в '' и ложно совпадает с пустым тегом
    norm = lambda x: re.sub(r"[\s_\-]+", "", (x or "").strip().lower())
    ng, nslug, ntag = norm(g), norm(t.get("slug")), norm(t.get("tag"))
    if ng and (ng == nslug or (ntag and ng == ntag)):
        return ""
    return g


def _offer_staleness(text):
    """Насколько сессия-источник ушла вперёд после оффера, в КБ (0 = свежо).
    Пара прощальных реплик — норма; реальная работа после передачи = устарел."""
    from_sid = read_field(text, "from-session")
    base = read_field(text, "origin-size")
    if not (from_sid and base.isdigit()):
        return 0
    hits = list((Path.home() / ".claude" / "projects").glob("*/{0}.jsonl".format(from_sid)))
    if not hits:
        return 0
    grown = hits[0].stat().st_size - int(base)
    return grown // 1000 if grown > 150_000 else 0


# КОНТРАКТ СИДА — семь типовых блоков (решение 05 нити edinyy-sloy, подпись владельца
# 30.07: «хендофф — слабейший шов, валидация глазами; свободный текст сида не
# измерим»). Доска их МЕРИТ: прозовый блок — есть/нет, списочный — счётом пунктов.
# Ключ = имя блока в контракте, оно же подпись на доске. Синонимы — как блок
# назывался в сидах ДО контракта: их писали от руки и формулировки разошлись
# («## где мы (минимум для шага)», «## Состояние машины — ПРОЧТИ ПЕРВЫМ»), а
# старый сид не должен читаться как пустой. Матчим ПОДСТРОКОЙ по нормализованному
# заголовку (без хвоста в скобках) — заголовки живые, точной строки в каноне нет.
SEED_PROSE, SEED_LIST = "проза", "список"
SEED_CONTRACT = (
    ("финал", SEED_PROSE, ("финал", "от а к б", "что выкачено", "итог")),
    ("курсор", SEED_PROSE, ("курсор", "где мы", "состояние")),
    ("дальше", SEED_LIST, ("дальше", "следующий шаг")),
    ("решения", SEED_LIST, ("решения",)),
    ("опыт", SEED_LIST, ("опыт", "отвергнуто", "петли", "грабли")),
    ("карта", SEED_LIST, ("карта входа", "карта", "reading-path")),
    ("окружение", SEED_PROSE, ("окружение", "среда", "как работать",
                               "как просил")),
)
_SEED_CACHE = {}


def _seed_norm(head):
    """Заголовок → сравнимое имя: без регистра, без хвоста в скобках, тире в
    пробел. Дефис НЕ трогаем — им живёт «reading-path»."""
    h = re.sub(r"\s*\([^)]*\)\s*$", "", head.strip().lower())
    h = h.replace("—", " ").replace("–", " ")
    return re.sub(r"\s+", " ", h).strip()


def _seed_sections(path):
    """Секции `## …` сида → {нормализованное имя: строки тела}. None — не читается."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="ignore")
    except (OSError, TypeError):
        return None
    out, name = {}, None
    for line in text.splitlines():
        m = re.match(r"##\s+(.+?)\s*$", line)
        if m:
            name = _seed_norm(m.group(1))
            out.setdefault(name, [])
        elif name is not None:
            out[name].append(line)
    return out


def _seed_measure(path):
    """Меры сида: marks = [(подпись, знак, дырка?)], filled = сколько блоков есть,
    matched = сколько заголовков вообще опознано контрактом (0 → свободная форма,
    такой сид НЕ ругаем нулём). None — сида нет / не прочитать.
    Один заголовок закрывает ОДИН блок: у «опыта» синонимов несколько, но два
    блока не должны делить одну секцию."""
    if not path:
        return None
    try:
        stamp = Path(path).stat().st_mtime_ns
    except (OSError, TypeError):
        return None
    hit = _SEED_CACHE.get(path)
    if hit and hit[0] == stamp:
        return hit[1]
    secs = _seed_sections(path)
    if secs is None:
        return None
    marks, filled, matched, used = [], 0, 0, set()
    for label, kind, aliases in SEED_CONTRACT:
        body = None
        for name, lines in secs.items():
            if name not in used and any(a in name for a in aliases):
                body = lines
                used.add(name)
                matched += 1
                break
        items = [ln for ln in (body or []) if ln.strip()]
        if not items:
            marks.append((label, "○", True))
            continue
        filled += 1
        if kind == SEED_PROSE:
            marks.append((label, "✓", False))
        else:
            # пункты списка; блок написан прозой — считаем непустые строки, чтобы
            # заполненный блок не выглядел нулём
            bullets = [ln for ln in items if re.match(r"\s*(?:[-*+]|\d+[.)])\s+", ln)]
            marks.append((label, str(len(bullets) or len(items)), False))
    out = {"marks": marks, "filled": filled, "matched": matched}
    _SEED_CACHE[path] = (stamp, out)
    return out


def _seed_title(rec):
    """Полная строка мер — в title (карточке и узлу передачи)."""
    m = _seed_measure((rec or {}).get("seed"))
    if not m:
        return ""
    if not m["matched"]:
        return "сид свободной формы — контрактных блоков не найдено"
    return "сид {0}/7 · ".format(m["filled"]) + " · ".join(
        "{0} {1}".format(lb, sg) for lb, sg, _ in m["marks"])


def _seed_line(rec):
    """Строка мер под узлом висящей передачи в фокус-виде (моно, дырки — полым
    маркером в ink-faint, чтобы недостача читалась глазом, а не считалась)."""
    m = _seed_measure((rec or {}).get("seed"))
    if not m:
        return ""
    if not m["matched"]:
        return '<span class="seedm"><span class="sb free">сид свободной формы</span></span>'
    return '<span class="seedm">{0}</span>'.format(" · ".join(
        '<span class="sb{0}">{1} {2}</span>'.format(
            " hole" if hole else "", lb, sg) for lb, sg, hole in m["marks"]))


def read_offers():
    """Очередь передач: waiting-счётчики + ПОЛНЫЕ записи для таймлайна нити."""
    q = HOME / ".tide" / "handoffs"
    waiting, records = {}, []
    if not q.is_dir():
        return waiting, records
    for p in sorted(q.glob("*.md")):
        t = p.read_text(encoding="utf-8", errors="ignore")
        status = read_field(t, "status")
        arc = read_field(t, "arc")
        proj = read_field(t, "project")
        top_slug = re.sub(r"^\d+-@?", "", arc.split("/", 1)[0])
        rec = {"status": status, "proj": proj, "thread": top_slug,
               "key": p.stem,
               "mode": read_field(t, "mode"),
               "created": read_field(t, "created"),
               "taken_at": read_field(t, "taken-at"),
               "session": arc.split("/", 1)[1] if "/" in arc else "",
               # путь к сиду — доска меряет его состав по контракту (решение 05)
               "seed": read_field(t, "seed"),
               # оффер ЗАРЕЗЕРВИРОВАН (▶ уже нажат, сессия поднимается; флип —
               # первым ходом): кнопка мутирует ▶→⟳, повторный клик фокусит
               "pickup": read_field(t, "pickup-session"),
               "reserved_at": read_field(t, "reserved-at"),
               # свежесть сида (решение 08.07): источник растёт → кнопка гаснет
               # прямо на доске (рендер каждые 5с), не дожидаясь клика
               "stale_kb": _offer_staleness(t) if status == "offered" else 0}
        records.append(rec)
        if status == "offered":
            waiting[(proj, top_slug)] = waiting.get((proj, top_slug), 0) + 1
    return waiting, records


def age(ts):
    d = datetime.now() - datetime.fromtimestamp(ts)
    if d.days > 0:
        return "{0} дн".format(d.days)
    h = d.seconds // 3600
    return "{0} ч".format(h) if h else "{0} мин".format(max(1, d.seconds // 60))


def read_favorites():
    """Избранные нити (решение 16.07): [(proj, dir)] в порядке файла — руками
    закреплённый список, ЕДИНСТВЕННОЕ что висит над потоком стола.

    Живут ФАЙЛОМ в контрол-хоуме (не в коде — закон): правятся словами через
    агента. Сменили собой F-слоты read_focus (рамка не звалась с 12.07)."""
    f = HOME / ".tide" / "state" / "favorites"
    out = []
    if not f.is_file():
        return out
    for line in f.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "|" not in s:
            continue
        parts = [p.strip() for p in s.split("|")]
        if len(parts) >= 2:
            out.append((parts[0], parts[1]))
    return out


def read_desk_waits():
    """{(дом, каталог нити): сколько её вещей ждёт РУКИ ЧЕЛОВЕКА} — источник
    оранжевого статуса (решение 31.07, шаг 3 работы 25: «оранжевый — только из
    стола»).

    Считаем ровно то, что стол показывает карточками, и ничего сверх:
      1. работа нити с шагами `[?]`  — «подтверди шаги», ждут слова;
      2. работа нити в `status: review` — «прими работу», ждёт приёмки;
      3. артефакт со `status: new`, принадлежащий нити (назвал её работу полем
         `work: NN` либо родился в ней — `from-arc`) — «забери».
    Передача, ждущая приёма, сюда НЕ идёт: у неё свой счётчик (read_offers), он
    и так доезжает до карточки отдельным ⌛.

    ГЕЙТА ПЛАНА В СПИСКЕ НЕТ намеренно. Наблюдаемо только «волна идёт, гейт не
    пройден» — а это норма всю дорогу, пока волна строится; честного «волна
    доделана, гейт ждёт руки» в plan.md нет. Считать его ждущим значило бы
    вернуть ложный оранжевый ровно того рода, от которого уходим: упрётся агент
    в гейт — положит карточку на стол, и её накроет пункт 3.

    Адрес нити берём из поля `thread:` тем же чтением, что и вкладка работ
    (_thread_addr): голый слаг — нить дома работ, `дом/NN-@нить` — чужого."""
    out = {}
    def bump(addr, own=""):
        """*own* — дом самой работы: голый слаг в её паспорте читается ОТ НЕГО.

        Движок пишет `thread: 07-@нить` без дома, когда работа и нить в одном
        проекте. Раньше такой адрес всегда читался от дома-верфи, потому что
        других источников не было; теперь работа может лежать в соседнем проекте, и её
        голый слаг — нить соседнего проекта, а не одноимённая нить верфи.
        """
        if not addr:
            return
        home, _, slug = addr.strip().rpartition("/")
        key = (home or own or _works_home(), slug.strip("_"))
        if key[1]:
            out[key] = out.get(key, 0) + 1
    work_thread = {}                      # номер работы → (адрес нити, её дом)
    for hint, f in work_files():
        try:
            _title, meta, _d, items, _j, _p = _work_read(f)
        except OSError:
            continue
        own = _work_home(hint, meta)
        addr = meta.get("thread", "").strip()
        work_thread[_issue_num(f.parent.name)] = (addr, own)
        if not addr or meta.get("status") == "done":
            continue
        if any(it[0] == "?" for it in items):
            bump(addr, own)
        if meta.get("status") == "review":
            bump(addr, own)
    for f in (sorted(ARTIFACTS_DIR.glob("*/artifact.md"))
              if ARTIFACTS_DIR.is_dir() else []):
        try:
            _t, meta, _c = _artifact_read(f)
        except OSError:
            continue
        if meta.get("status", "new") != "new":
            continue
        wn = meta.get("work", "").strip()
        named = work_thread.get(int(wn)) if wn.isdigit() else None
        if named:
            bump(named[0], named[1])
        else:
            bump(meta.get("from-arc", ""))
    return out


_STATUS_QUIET = 30    # сек тишины после ЧИСТОГО финала агента → уже «ждёт твоё решение»
_WORK_FRESH = 180     # транскрипт молчит дольше → голова НЕ в цикле (живой агент пишет
#                       каждые несколько секунд); работой это уже не считаем


def _thread_status(t, now_ts, waits=0):
    """Статус нити для плашки. ('work'|'wait'|'sleep', подпись).

    ПЕРЕСОБРАН 31.07 (работа 25, шаги 3–4): раньше и «зелёный», и «оранжевый»
    выводились из одного источника — пульса ГОЛОВЫ, — и оба врали.
      · Пока сабагент строит, транскрипт головы молчит: она ждёт воркера и в
        свой чат ничего не пишет. Тишина читалась как «агент встал» → оранжевый
        горел над кипящей работой (payouts 31.07).
      · Молчание головы вообще не значит, что человек нужен: у нити может не
        быть ни одного вопроса к нему (gitlab-migration 31.07 — решать нечего,
        а доска звала).
    Теперь у каждого цвета СВОЙ источник:
      work  — жив ХОТЬ КТО-ТО из агентов нити: голова (пульс транскрипта) или её
              воркер (пульс чатов сабагентов, см. _worker_pulse);
      wait  — на столе есть РЕАЛЬНАЯ вещь, ждущая руки: *waits* > 0 (список
              собирает read_desk_waits — шаги [?], review, артефакт, передача);
      sleep — никто не строит и ничего не ждёт.
    Порядок: живой агент важнее ждущего. Нить, в которой кто-то строит, не
    заблокирована на человеке — решение подождёт до остановки, а карточка
    ожидания всё это время видна на столе, куда человек и ходит решать; встали
    агенты — оранжевый загорается сразу.

    Молчание и вопрос головы сами по себе оранжевого больше НЕ дают: у доски
    нет способа отличить «агент спросил» от «агент упал на tool-call», а звать
    человека к обеим одинаково — это и был ложный вызов."""
    if t.get("closed"):                           # закрытая НЕ демандит (решение 13.07):
        # tide 1.0.34+ (cand 79): close СПАРИВАЕТ живую голову — она остаётся открытой
        # ВНУТРИ закрытой нити и переживает её. Покажем это, а не «всё запечатано» (cand 10).
        hs = _head_session(t) if t.get("kind") != "routine" else None
        if hs and (now_ts - _session_pulse(hs)) < HEAD_IN_HAND_SEC:
            return ("done", "✓ закрыта · голова жива")
        return ("done", "✓ закрыта")              # чат ещё жив — но это loose-end, не «ждёт»
    # ТРЕВОГА двойного пульса (механика №1 упрощена 16.07): две открытые сессии
    # нити работают ОДНОВРЕМЕННО — настоящий «Мики 17». Ловится по пульсу
    # транскриптов, а не по штампам-бухгалтерии; тревога, не запрет. Стоит выше
    # всех: это не «ждёт решение», а поломка нити, и молчать о ней нельзя.
    if t.get("kind") != "routine":
        hot = [s for s in t["sessions"]
               if not s["closed"] and not s.get("dismissed") and s.get("claude")
               and (now_ts - _session_pulse(s)) < _WORK_FRESH]
        if len(hot) >= 2:
            return ("wait", "⚠ две сессии работают разом")
    hs = _head_session(t) if t.get("kind") != "routine" else None
    if hs:
        # голова в цикле — дальше не смотрим: за воркерами на диск ходить незачем
        if (now_ts - _session_pulse(hs)) < _WORK_FRESH:
            return ("work", "агент работает")
        # голова молчит — спрашиваем её воркеров (ленивый glob, шаг 4)
        if (now_ts - _worker_pulse(hs)) < _WORK_FRESH:
            return ("work", "агент работает")
    if waits:
        return ("wait", "ждёт твоё решение")
    return ("sleep", "спит")


def _resume_oc(sid, proot, arc="", force=False):
    """ЕДИНОЕ действие возврата в сессию (унификация 14.07): fetch-фокус НА МЕСТЕ,
    не href-навигация → доска остаётся, новой вкладки нет. Несёт arc СЕССИИ, чтобы
    tide читал её паспорт. ВЕРДИКТ сервера показывается тостом (решение 14.07:
    «gone» глотался — тост стрелял до fetch, человек читал чужую активную
    вкладку как «перешло не туда»)."""
    extra = "&force=1" if force else ""
    return ("event.preventDefault();event.stopPropagation();deckToast('→ в сессию…');"
            "fetch('/resume?plain=1" + extra + "&sid=" + esc(sid) + "&dir="
            + esc(str(proot)) + "&arc=" + esc(str(arc)) + "')"
            ".then(function(r){return r.text()})"
            ".then(function(x){deckToast(x.slice(0,120))})")


def _resume_action(t, s):
    """Значение onclick для ⟳ сессии *s*: прямой фокус/подъём, БЕЗ модалок —
    по новой механике №1 (решение 16.07) прошлые сессии — открытая история,
    вернуться можно в любую. force=1 всегда: старый станок гейтил сессии со
    штампом dissolved — рука человека (клик) этот гейт перекрывает; новый
    станок без гейта флаг просто игнорирует."""
    proot = t["path"].parents[2]
    arc = t["path"] / "arcs" / s["dir"]
    return _resume_oc(s["claude"], proot, arc, force=True)


def _spark_oc(proj, thread):
    """▶ поднять сессию — ИДЕМПОТЕНТНО (фикс Дефекта 1, принцип №1): гард re-entry
    (this.dataset.busy) + гашение кнопки не дают повторному жмаку в окне запуска
    поднять ВТОРУЮ сессию; после старта обновляем доску → кнопка станет ⟳."""
    return ("event.preventDefault();event.stopPropagation();"
            "if(this.dataset.busy)return;this.dataset.busy='1';"
            "this.style.pointerEvents='none';this.style.opacity='.4';"
            "deckToast('поднимаю сессию');"
            "fetch('/spark?proj=" + esc(proj) + "&thread=" + esc(thread) + "')"
            ".then(function(){setTimeout(function(){"
            "if(typeof boardRefresh==='function')boardRefresh();},1500);});")


def _resume_btn(oc, icon, title, hot=False, word="резюм"):
    """Кнопка «резюм» в полосе статуса — иконка + слово (решение 29.07: «кнопка
    слишком маленькая, пусть будет нормальная»). Вид несёт .abtn (единый компонент
    доски «форма ▶ запустить»), размер и заливку — .pjresume; амбер (.primary)
    только когда нить ждёт человека. Слово ОДНО на все три случая возврата
    (вернуться в живую · войти · поднять): с места человека это один жест «верни
    меня в нить», а чем именно движок его исполнит — в тултипе. Другое слово
    берёт только передача (запустить) — там это ДРУГОЙ жест, не возврат."""
    return ('<a class="abtn pjresume{p}" href="#" title="{t}" onclick="{o}">'
            '{i}<span>{w}</span></a>').format(
                p=" primary" if hot else "", t=title, o=oc, i=icon, w=word)


def _take_oc(key):
    """Приём висящей передачи С КАРТОЧКИ — тот же движок /take, что у пилюли в
    панели нити (_take_btn), гейты тоже её. Разница только в обратной связи по
    грамматике карточки: stopPropagation (клик не проваливает в панель), гвоздь
    от двойного тапа и refresh доски — вместо подмены текста, которая на карточке
    съела бы иконку со словом."""
    return ("event.preventDefault();event.stopPropagation();"
            "if(this.dataset.busy)return;this.dataset.busy='1';"
            "this.style.pointerEvents='none';this.style.opacity='.4';"
            "deckToast('принимаю передачу');"
            "fetch('/take?key=" + esc(key) + "')"
            ".then(function(){setTimeout(function(){"
            "if(typeof boardRefresh==='function')boardRefresh();},1500);});")


def _offer_of(hos):
    """Висящий оффер нити из её записей передач: карточке нужен сам rec (key,
    свежесть сида, резерв), а не только счётчик wait, которым она красит бейдж."""
    return next((r for r in (hos or []) if r["status"] == "offered"), None)


def _card_take_btn(offer):
    """«▶ запустить» на карточке нити с висящей передачей (решение 29.07:
    «логичнее в статусе передачи на карточке показывать ОБЕ кнопки — и последняя
    сессия, и хендофф-кнопка, как внутри нити»). Гейты — те же, что у пилюли в
    панели: без key звать некого, устаревший сид не берём (абзац-объяснение живёт
    в панели, на карточке ему не место), а уже нажатый оффер честно говорит
    «поднимается» и отдаёт амбер обратно."""
    if not offer or not offer.get("key") or offer.get("stale_kb"):
        return ""
    reserved = ((offer.get("pickup") or "").strip() not in ("", "-")
                and not _reserve_stale(offer))
    if reserved:
        return _resume_btn(_take_oc(offer["key"]), ICON_RESUME,
                           "передача уже поднимается — открыть терминал",
                           word="поднимается")
    return _resume_btn(_take_oc(offer["key"]), ICON_PLAY,
                       "принять передачу свежей сессией", hot=True,
                       word="запустить")


def _card_play(t, wait=0, hot=False, offer=None):
    """Кнопка «резюм» справа в полосе статуса (решение 13.07, вид переделан 29.07):
    вход в оркестрирующую сессию нити. Живая голова с claude-айди → в её терминал
    (/resume фокусит уже поднятый, не плодя дубль; нет — поднимает). Головы нет →
    поднять свежую сессию по нити (/spark). Оба через fetch — доска на месте, Orca
    всплывает. Кнопка НЕ проваливает в панель (stopPropagation). Закрытой/дежурке
    без dir — spark некуда."""
    # ВИСЯЩАЯ передача (⌛) — ДВЕ кнопки, как внутри нити (решение 29.07). Раньше ▶
    # тут пряталась целиком (13.07), чтобы не было шортката мимо пайплайна приёма,
    # и оставался только ⟳ возврат в держащую сессию (19.07: «нету кнопки — если
    # закрою, потеряю»). Шортката нет и теперь: карточка зовёт ТОТ ЖЕ /take, что
    # пилюля в панели, с теми же гейтами свежести — то есть это и есть пайплайн,
    # просто без лишнего проваливания внутрь. Порядок: запустить первым — это
    # ожидаемый следующий шаг передачи; амбер достаётся ему одному.
    if wait:
        take = _card_take_btn(offer)
        s = _live_session(t)
        if not (s and s.get("claude")):
            return take
        title = ("вернуться в сессию, которая пока держит нить" if take else
                 "вернуться в текущую сессию "
                 "(передача принимается из панели нити)")
        return take + _resume_btn(_resume_action(t, s), ICON_RESUME, title,
                                  hot and not take)
    s = _live_session(t)
    if s and s.get("claude"):
        proot = t["path"].parents[2]
        # title таба в Orca = ИМЯ НИТИ с карточки (решение 13.07: не «resume-<sid>»,
        # а «debug-deck») — реестр по пути арки надёжен, title теперь человеческий
        name = re.sub(r"[\"'\\\n\r]", "", (t.get("tag") or t.get("slug") or ""))
        oc = ("event.preventDefault();event.stopPropagation();deckToast('→ в сессию');"
              "fetch('/resume?sid={0}&dir={1}&arc={2}&title='+encodeURIComponent('{3}'))".format(
                  esc(s["claude"]), esc(str(proot)), esc(str(t["path"])), name))
        return _resume_btn(oc, ICON_PLAY, "войти в сессию", hot)
    if t.get("kind") == "routine" or not t.get("dir"):
        return ""
    oc = _spark_oc(t["proj"], t["dir"])
    return _resume_btn(oc, ICON_PLAY, "поднять сессию", hot)


def _card_close(t):
    """✕ закрыть нить рукой С КАРТОЧКИ (решение 14.07: «на карточке должен быть
    крестик»). Вариант A: любая открытая нить, за confirm-модалкой с полем «итог
    одной строкой» → /close (итог в output/ + ретайр головы + tide arc close -f).
    Дежурка закрывается тем же ✕ — домен каскадит рутины с 14.07 (решение владельца: «нет
    кнопочек, чтобы закрыть»). Без-паспорта/уже-закрытой — нечего."""
    import urllib.parse as _up
    if not t.get("path") or t.get("closed"):
        return ""
    name = t.get("tag") or t.get("slug") or "нить"
    oc = _confirm_onclick(
        url="/close?d=" + _up.quote(str(t["path"])),
        title="Закрыть нить «{0}»?".format(name),
        body="Уйдёт в закрытые (⟲ reopen вернёт). Живая голова ретайрится.",
        ok="закрыть", danger=True,
        input="итог одной строкой (в output) — можно пусто")
    return '<a class="pjhold" href="#" title="закрыть нить" onclick="{0}">✕</a>'.format(oc)


def _card_release(t):
    """✕ на карточке ЗАКРЫТОЙ нити с живой головой: отпустить голову (движок
    /dismiss). решение 14.07: «закрыл нить, а она зависла» — карточка «чат жив ·
    нить закрыта» честно ждёт 6ч остывания транскрипта, но когда чат уже не
    нужен, человеку нужен жест здесь и сейчас. Смерть внимания — только руками
    (закон 12.07), поэтому кнопка за confirm, не автоматика. У закрытой нити
    dismiss освобождает всю цепочку голов разом (домен)."""
    import urllib.parse as _up
    if not t.get("closed") or not t.get("path"):
        return ""
    s = _live_session(t)
    if not s:
        return ""
    arc = t["path"] / "arcs" / s["dir"]
    name = t.get("tag") or t.get("slug") or "нить"
    oc = _confirm_onclick(
        url="/dismiss?d=" + _up.quote(str(arc)),
        title="Отпустить голову «{0}»?".format(name),
        body="Нить закрыта, чат ещё жив — карточка держится за его тепло. "
             "Отпустить — уйдёт из фокуса сразу; сам чат в терминале живёт, "
             "след остаётся в журнале нити.",
        ok="отпустить", danger=False)
    return '<a class="pjhold" href="#" title="отпустить голову" onclick="{0}">✕</a>'.format(oc)


_FAV_SET = None


def _card_fav(t):
    """★ добавить / убрать из избранных прямо с карточки (решение 16.07):
    залитая звезда = уже в избранных, клик снимает; контурная — добавляет в
    конец списка. Пишет движок /fav (одна строка в state/favorites контрол-
    хоума), доска обновляется на месте — карточка переезжает между секциями.
    Слот и повадки — бывшей ☾: сосед ▶, stopPropagation, закрытой — нечего."""
    global _FAV_SET
    if not t.get("path") or t.get("closed") or not t.get("dir"):
        return ""
    if _FAV_SET is None:  # процесс рендера живёт один запрос — кэш честный
        _FAV_SET = set(read_favorites())
    fav = (t.get("proj"), t["dir"]) in _FAV_SET
    on, glyph = ("0", ICON_STAR_ON) if fav else ("1", ICON_STAR)
    title = "убрать из избранных" if fav else "в избранные"
    proj = re.sub(r"[\"'\\\n\r]", "", t.get("proj") or "")
    d = re.sub(r"[\"'\\\n\r]", "", t["dir"])
    oc = ("event.preventDefault();event.stopPropagation();"
          "fetch('/fav?on={on}&proj={p}&dir='+encodeURIComponent('{d}'))"
          ".then(function(r){{return r.text()}}).then(function(x){{deckToast(x);"
          "if(typeof boardRefresh==='function')boardRefresh();}})").format(
              on=on, p=proj, d=d)
    return '<a class="pjhold" href="#" title="{0}" onclick="{1}">{2}</a>'.format(
        title, oc, glyph)


def _sleep_days():
    """Со скольких дней тишины нить считается спящей.

    Переменная, а не константа: тридцать дней — это цифра из разбора стола на
    01.09 (тридцать нитей из сорока трёх не двигались месяц), а не закон
    природы. Кому нужен другой ритм — `$TIDE_SLEEP_DAYS`; ноль или мусор в
    переменной ничего не прячет, потому что прятать по ошибке хуже, чем не
    прятать вовсе.
    """
    raw = (os.environ.get("TIDE_SLEEP_DAYS") or "").strip()
    try:
        days = int(raw) if raw else SLEEP_DAYS_DEFAULT
    except ValueError:
        return SLEEP_DAYS_DEFAULT
    return days if days > 0 else 0


SLEEP_DAYS_DEFAULT = 30


def _sleeping(t, wait=0, waits=0):
    """Нить спит: давно не двигалась И ничего от человека не ждёт.

    Второе условие важнее первого. Нить, которая держит на столе вопрос или
    приёмку, не спящая, сколько бы она ни молчала, — иначе фильтр спрятал бы
    ровно то, ради чего человек на доску и заходит. Висящая передача — то же
    самое: она ждёт руки.
    """
    days = _sleep_days()
    if not days or wait or waits:
        return False
    fresh = t.get("fresh") or 0
    if not fresh:
        return False
    return (datetime.now().timestamp() - fresh) > days * 86400


def _card(t, wait, label=None, badge="", show_proj=False, note="", offer=None,
          waits=0):
    """Карта нити на столе — компактная строка, ровно как карточка проекта
    (решение 12.07: «сделать такими же аккуратными»). В плоском потоке
    по свежести проект подписан прямо на карте. Действий на превью НЕТ
    (решение 12.07: ✕ отпустить — внутри панели, не на карте).

    *waits* — сколько вещей нити ждёт руки (read_desk_waits): единственный
    источник оранжевого статуса с 31.07, см. _thread_status. Висящая передача
    (*wait*) считается ждущей наравне с ними.

    `data-sleep` — метка спящей (см. `_sleeping`): по ней фильтр в шапке решает,
    показывать карточку или нет. Метка, а не отсутствие карточки: нить никуда не
    девается, её просто не видно, пока не попросишь."""
    mtext = (note + ' · ' if note else '') + age(t["fresh"])
    if wait:
        # у бейджа — краткий счёт «сид N/7» (сколько контрактных блоков есть),
        # полная строка мер в title. Сид свободной формы счёта НЕ получает: он не
        # дырявый, он просто до контракта — пугать нулём нечестно (решение 05)
        sm = _seed_measure((offer or {}).get("seed"))
        score = ('<span class="seedn">сид {0}/7</span>'.format(sm["filled"])
                 if sm and sm["matched"] else "")
        meta = '<span class="pjmeta hot" title="{0}">⌛ передача {1}</span>'.format(
            esc(_seed_title(offer)), score)
    else:
        meta = '<span class="pjmeta">{0}</span>'.format(esc(mtext))
    icon = ICON_ROUTINE if t["kind"] == "routine" else ICON_THREAD
    b = '<span class="pjbadge">{0}</span>'.format(badge) if badge else ""
    pj = ('<span class="pjproj">{0}</span>'.format(esc(t["proj"]))
          if show_proj else "")
    # плашка статуса нити (решение 13.07; пересобрана 31.07): зелёный — от любого
    # живого агента нити, оранжевый — только от ждущей вещи на столе, включая
    # висящую передачу. Справа кнопка «резюм» — вход в оркестрирующую сессию
    # (фокус/поднять), а у нити с висящей передачей ДВЕ кнопки: запустить
    # преемника + вернуться в держащую сессию (29.07). Амбер кнопке достаётся
    # ровно там, где нить демандит человека — hot остаётся редким
    scls, slbl = _thread_status(t, datetime.now().timestamp(),
                                waits=waits + (1 if wait else 0))
    status = ('<div class="pjstatus st-{c}"><span class="dot"></span>'
              '<span class="stlbl">{l}</span>{x}{f}{p}</div>'.format(
                  c=scls, l=slbl, x=_card_close(t) + _card_release(t),
                  f=_card_fav(t), p=_card_play(t, wait, hot=bool(wait) or scls == "wait",
                                               offer=offer)))
    return ('<div class="pjcard thr{m}" role="button" tabindex="0" data-id="{i}" '
            'data-proj="{pr}"{sl}>'
            '<div class="pjtop"><span class="ic">{ic}</span>{b}'
            '<span class="pjnm">{n}</span>{pj}{meta}</div>{status}</div>'.format(
                m=" move" if wait else "", i=t["id"], ic=icon, b=b, pr=esc(t.get("proj") or ""),
                sl=' data-sleep="1"' if _sleeping(t, wait, waits) else "",
                n=esc(t["tag"] or label or t["slug"]), pj=pj, meta=meta, status=status))


# ── уровень проектов (канд. 75, нить «полка», шаг 1) ────────────────────────
# Доска получает уровень всех домов: карточка = имя + сколько живого +
# давность движения + ⌛ если ждёт передача; заход в дом = полка.


def _proj_card(pid, name, threads, wait, pfresh=0):
    """Карточка проекта на уровне «проекты» — компактная строка по киту.
    Давность = pfresh (учитывает и закрытие нити), а не только открытые."""
    if threads:
        n = len(threads)
        meta = "{0} {1} · {2}".format(
            n, _plural(n, "нить", "нити", "нитей"), age(pfresh or threads[0]["fresh"]))
    else:
        meta = "тихо"
    if wait:
        meta = '<span class="pjwait">⌛</span> ' + meta
    return ('<div class="pjcard" role="button" tabindex="0" data-id="{i}">'
            '<span class="ic">{ic}</span><span class="pjnm">{n}</span>'
            '<span class="pjmeta">{m}</span></div>'.format(
                i=pid, ic=ICON_PROJECT, n=esc(name), m=meta))


def _lead_btn(t, offer=None):
    """Жест живой нити (симметрия ▶ у кандидата). ВИСЯЩАЯ ПЕРЕДАЧА бьёт всё
    (решение 17.07): оффер на нить → ▶ ПОДНЯТЬ ПРЕЕМНИКА (движок /take), не ⟳ в
    предшественника — иначе «принять» кидает назад в старую сессию. Иначе:
    живая сессия с claude-айди → ⟳ вернуться (движок /resume); нет айди → ▶
    поднять новую сессию по нити (/spark). Клик не проваливает в нить."""
    if offer and offer.get("key") and not offer.get("stale_kb"):
        return ('<a class="sbtn" href="#" data-u="/take?key={0}" '
                'title="▶ поднять сессию-преемника (принять передачу)" '
                'onclick="event.preventDefault();event.stopPropagation();'
                "this.style.opacity='.4';fetch(this.dataset.u)\">▶</a>".format(
                    esc(offer["key"])))
    s = _live_session(t)
    if s and s.get("claude"):
        proot = t["path"].parents[2]
        return ('<a class="sbtn" href="#" title="вернуться в ведущую сессию нити" '
                'onclick="{0}">{1}</a>'.format(
                    _resume_action(t, s), ICON_RESUME))
    return ('<a class="sbtn" href="#" title="поднять сессию по нити" '
            'onclick="{0}">▶</a>'.format(_spark_oc(t["proj"], t["dir"])))


def _shelf_row(t, wait, offer=None):
    """Ряд полки: тег нити · суть · давность · кнопка-жест; клик по ряду
    проваливает в нить, клик по кнопке — нет (stopPropagation). Висящая
    передача → «⌛ передача» + ▶ поднять преемника (offer), иначе ⟳ в ведущую."""
    flag = ('<span class="pjage hot">⌛ передача</span>' if wait else
            '<span class="pjage">{0}</span>'.format(age(t["fresh"])))
    return ('<div class="pjrow" role="button" tabindex="0" '
            'onclick="openT(\'{i}\')"><span class="pjtag">{tag}</span>'
            '<span class="pjgoal">{goal}</span>{flag}{btn}</div>'.format(
                i=t["id"], tag=esc(t["tag"] or t["slug"]),
                goal=esc(_human(_real_goal(t))), flag=flag,
                btn=_lead_btn(t, offer)))


def _drop_ts(text, st):
    """Когда кандидата кинули на полку (решение 13.07). Приоритет — явный стамп в
    файле (`dropped:`/`added:`): он переживает правки. Иначе — создание файла
    (birthtime, mtime-фолбэк). ВАЖНО: правка кандидата в rename-редакторе сбрасывает
    и mtime, и birthtime — поэтому честный источник это стамп; FS остаётся лучшим-
    доступным для старых несштампованных кандидатов (для них birth==drop)."""
    raw = (read_field(text, "dropped") or read_field(text, "added")).strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(raw, fmt).timestamp()
        except ValueError:
            continue
    return getattr(st, "st_birthtime", None) or st.st_mtime


def read_candidates(proot):
    """Кандидаты-идеи дома (`.tide/arcs/candidates/*.md`): номер · имя · суть.
    Имя — одно человеческое слово из поля `title:` (правило владельца 12.07: кандидат
    зовётся одним словом, чтобы ориентироваться), фолбэк — слаг файла. Свежие
    (старшие номера) сверху — как всё на доске."""
    out = []
    cdir = proot / ".tide" / "arcs" / "candidates"
    if not cdir.is_dir():
        return out
    for f in sorted(cdir.iterdir()):
        if f.suffix != ".md" or not f.is_file():
            continue
        m = re.match(r"^(\d+)-(.+)$", f.stem)
        num, slug = (m.group(1), m.group(2)) if m else ("", f.stem)
        text = f.read_text(encoding="utf-8", errors="ignore")
        title = read_field(text, "title") or slug.replace("-", " ")
        # суть — без служебных полей (title/from) и заголовков. ПУСТЫЕ СТРОКИ
        # держим (фикс 4 работы 28): по ним раскрытие режется на абзацы, а
        # свёрнутый ряд и буфер и так берут склеенный `full`
        skip = ("#", "from:", "title:", "dropped:", "added:")
        body = "\n".join(ln.rstrip() for ln in text.splitlines()
                         if not ln.startswith(skip)).strip()
        full = " ".join(body.split())
        out.append({"num": num.lstrip("0") or num, "key": f.stem,
                    "title": title,
                    "body": body,              # с абзацами — для раскрытия
                    "gist": full[:500],        # для показа на полке — с капом
                    "full": full,              # для копирования — целиком
                    "from": read_field(text, "from"),   # происхождение (решение 13.07)
                    "dropped": _drop_ts(text, f.stat())})  # когда кинули (решение 13.07)
    out.reverse()
    return out


def _cand_origin(frm):
    """Происхождение кандидата → (метка, css-класс) для бейджа на ряду (решение владельца
    13.07): ↗ из другого проекта · ← из нити этого проекта · рукой (добавлен
    человеком). Формы from: «↗ имя-проекта» / «06-@operator/01-frame» / «-»/пусто, но
    БЫВАЕТ длинный freeform («сессия 08-sign-take2 (решение 09.07: …)») — режем до
    опознавательного токена, иначе бейдж (.csrc, nowrap) раздувал ряд полки в
    горизонтальный скролл (решение 13.07, полка tide-stack)."""
    frm = (frm or "").strip()
    if not frm or frm == "-":
        return ("рукой", "csrc")
    def _short(s):  # без пояснения-в-скобках и ведущих стрелок, с капом длины
        s = re.sub(r"^[←↗→\s]+", "", s.split("(")[0]).strip()
        return (s[:22].rstrip() + "…") if len(s) > 23 else s
    if frm.startswith("↗"):
        return ("↗ {0}".format(_short(frm)), "csrc cross")
    seg = re.sub(r"^\d+-@?", "", frm.split("/")[0])
    return ("← {0}".format(_short(seg)), "csrc")


def read_closed(pname, proot):
    """Закрытые нити дома (`__NN-@slug__`) — трофеи полки: тег, дата закрытия,
    итог финал-гейта из plan.md и пройденные гейты. Несёт полный объект нити
    (id/sessions/plan/path) — чтобы В ЗАКРЫТУЮ ТОЖЕ можно было провалиться в
    полный контекст (решение 12.07), не только прочитать итог. Новейшие сверху."""
    out = []
    arcs = proot / ".tide" / "arcs"
    if not arcs.is_dir():
        return out
    for d in sorted(arcs.iterdir()):
        if not (d.is_dir() and d.name.startswith("__")):
            continue
        # __dropped__ — могила убранных пустых нитей (не трофей): контейнер без
        # своего паспорта, читался бы как пустой закрытый трофей — пропускаем.
        if d.name == "__dropped__":
            continue
        goals = sorted(d.glob("*-goal.md"))
        pp = goals[-1] if goals else d / "arc.md"
        text = pp.read_text(encoding="utf-8", errors="ignore") if pp.is_file() else ""
        tag = read_field(text, "tag").upper()
        slug = re.sub(r"^\d+-@?", "", d.name.strip("_"))
        goal = read_field(text, "goal")
        plan = read_plan(d)
        final = (plan["final"] if plan else "") or goal
        passed = [w for w in (plan["waves"] if plan else []) if w.get("passed")]
        date = ""
        for w in reversed(passed):
            m = re.search(r"(\d{2}\.\d{2})", w["passed"])
            if m:
                date = m.group(1)
                break
        mtime = d.stat().st_mtime
        sessions = read_sessions(d)
        out.append({"tag": tag or slug, "final": final,
                    "date": date or datetime.fromtimestamp(mtime).strftime("%d.%m"),
                    "gates": ["✓ {0} — {1}".format(_wave_title(w["name"]), w["passed"])
                              for w in passed],
                    "mtime": mtime,
                    # полный объект нити для провала в закрытую
                    "id": re.sub(r"[^a-z0-9]", "", (pname + d.name).lower()),
                    # форма закрытой нити = форме живой (read_threads): "dir"
                    # был только у живой, и общий потребитель падал на закрытой
                    "proj": pname, "dir": d.name, "slug": slug, "goal": goal,
                    "kind": read_field(text, "kind") or "arc", "closed": True,
                    "sessions": sessions, "plan": plan, "path": d,
                    # a live head under a closed thread surfaces by its real pulse (cand 09/10)
                    "fresh": max([mtime] + [_session_pulse(s) for s in sessions])})
    out.sort(key=lambda c: -c["mtime"])
    return out


def _cand_body_html(c):
    """Раскрытый кандидат (фикс 4 работы 28). До фикса тут лежала простыня: всё
    тело файла склеивалось в одну строку с капом 500 знаков, а происхождение и
    дата жили только в свёрнутом ряду тихими бейджами — раскрыл и потерял.
    Теперь сверху строка-мета (номер · откуда пришёл · дата), под ней текст
    АБЗАЦАМИ и целиком. Абзацы и код — общим движком заметок (_pnote_body_html),
    чтобы подача была одна. Происхождение на строке-мете стоит той же короткой
    меткой, что и бейдж ряда, но у 13 из 74 кандидатов `from:` — freeform на
    сотни знаков; полный текст не теряем, он идёт отдельной тихой строкой
    «откуда» под метой (в одну строку он превращал бы шапку в стену)."""
    frm = (c.get("from") or "").strip()
    src_label, _cls = _cand_origin(frm)
    bits = (["№ " + c["num"]] if c["num"] else []) \
        + [src_label, datetime.fromtimestamp(c["dropped"]).strftime("%d.%m.%Y")]
    out = ['<div class="cmeta" title="{f}">{b}</div>'.format(
        f=esc(c["key"] + ".md"), b=esc(" · ".join(bits)))]
    # полную строку показываем ТОЛЬКО когда бейдж что-то срезал: многоточие в
    # метке или пояснение-в-скобках, которое _cand_origin отбрасывает
    if frm and frm != "-" and (src_label.endswith("…") or "(" in frm):
        out.append('<div class="cfrom">откуда: {0}</div>'.format(esc(frm)))
    return "".join(out) + _pnote_body_html(c.get("body") or c["gist"])


def _cand_row(c, proj):
    """Кандидат на полке — ЕДИНООБРАЗНО с рядами нитей (решение 12.07: не выбивался
    номером): одно слово-имя · суть · ⧉ · ▶ · ✕, как имя-цель у нити. Полный
    кандидат с мета-строкой — по клику (_cand_body_html); служебный жаргон в
    свёрнутом виде не светится (через _human).
    ⧉ копирует описание идеи в буфер (решение 13.07: вставить в оркестр-сессию и
    сказать «забери в работу», не копируя руками); ▶ поднимает свежую сессию из
    идеи (движок /spark); ✕ выбрасывает идею (движок /drop-cand, мягко в
    __dropped__). Клик по кнопкам не разворачивает суть (stopPropagation)."""
    copy_text = "Кандидат {p}/{k} — «{t}»\n\n{full}".format(
        p=proj, k=c["key"], t=c["title"], full=c.get("full") or c["gist"])
    copy = ('<a class="sbtn" href="#" title="⧉ скопировать описание кандидата" '
            'onclick="{oc}">⧉</a>'.format(oc=_copy_onclick(copy_text)))
    spark = ('<a class="sbtn" href="#" title="▶ родить нить из кандидата" '
             'onclick="event.preventDefault();event.stopPropagation();'
             "this.style.opacity='.4';"
             "fetch('/spark?proj={p}&cand={k}')\">▶</a>".format(
                 p=esc(proj), k=esc(c["key"])))
    # ⚒ идея → работа одним жестом (разрыв потока, 01.09): у кандидата были
    # только «скопировать», «родить нить» и «выбросить», и превратить идею в
    # работу можно было лишь руками в чате. Движок это умеет вербом, доске
    # оставалось позвать. Тост вместо перезагрузки — идея уезжает с полки, и
    # доска подберёт это следующим свопом сама.
    towork = ('<a class="sbtn" href="#" title="⚒ завести работу из идеи" '
              'onclick="event.preventDefault();event.stopPropagation();'
              "this.style.opacity='.4';"
              "fetch('/work-from-cand?proj={p}&cand={k}')"
              ".then(function(r){{return r.text()}})"
              ".then(function(x){{(window.deckToast||alert)(x)}});\">⚒</a>".format(
                  p=esc(proj), k=esc(c["key"])))
    # ✕ выброс — опасное действие через ТИПОВУЮ модалку (deckConfirm): клик
    # открывает подтверждение, оно делает fetch и обновляет доску на месте
    # (не роняя из полки на главную). Мягко: файл едет в __dropped__, восстановим.
    drop = ('<a class="sbtn xbtn" href="#" title="✕ выбросить кандидата с полки" '
            'onclick="{oc}">✕</a>'.format(oc=_confirm_onclick(
                url="/drop-cand?proj={0}&cand={1}".format(proj, c["key"]),
                title="Выбросить кандидата с полки?",
                body="«{0}» уедет в __dropped__ — исчезнет с полки и из бэклога, "
                     "но останется на диске. Восстановимо.".format(c["title"]),
                ok="Выбросить")))
    # суть в свёрнутом — через жаргон-фильтр; если он съел всё под ноль (жаргон-
    # ёмкая идея, ловлено на absorb 13.07) — показываем сырую, не голый ряд.
    preview = _human(c["gist"]) or c["gist"]
    src_label, src_cls = _cand_origin(c.get("from"))
    src = '<span class="{0}">{1}</span>'.format(src_cls, esc(src_label))
    # когда кинули — тихой строкой возраста, тот же идиом что у нитей (решение 13.07)
    when = '<span class="pjage cwhen" title="кинут на полку">{0}</span>'.format(age(c["dropped"]))
    return ('<details class="tld candr"><summary class="pjrow">'
            '<span class="pjtag" title="{tt}">{n}</span><span class="pjgoal">{g}</span>'
            '{src}{when}{cp}{w}{s}{x}'
            '</summary><div class="tldd cbody">{full}</div></details>'.format(
                n=esc(c["title"]), tt=esc(c["title"]), g=esc(preview),
                full=_cand_body_html(c),
                src=src, when=when, cp=copy, w=towork, s=spark, x=drop))


def _stub_row(t, proj):
    """Пустая планлесс-нить в группе «не начатые» (решение 13.07: это брак, не
    кандидат — папка-нить без сессий и плана). Два жеста: ▶ поднять
    оркестрирующую сессию ВНУТРЬ неё (движок /spark?thread, дубля не плодит),
    ✕ убрать в __dropped__ (движок /drop-thread, мягко — работы там ноль).
    Клик по строке проваливает в паспорт; клик по кнопкам — нет."""
    import urllib.parse as _up
    spark = ('<a class="sbtn" href="#" title="▶ подхватить нить оркестрирующей сессией" '
             'onclick="event.preventDefault();event.stopPropagation();'
             "this.style.opacity='.4';"
             "fetch('/spark?proj={p}&thread={d}')\">▶</a>".format(
                 p=esc(proj), d=esc(t["dir"])))
    drop = ('<a class="sbtn xbtn" href="#" title="✕ убрать пустую нить" '
            'onclick="{oc}">✕</a>'.format(oc=_confirm_onclick(
                url="/drop-thread?d=" + _up.quote(str(t["path"])),
                title="Убрать пустую нить?",
                body="«{0}» уедет в __dropped__ — в ней ноль сессий и нет плана, "
                     "работы не теряем. Восстановимо.".format(t["tag"] or t["slug"]),
                ok="Убрать")))
    return ('<div class="pjrow" role="button" tabindex="0" onclick="openT(\'{i}\')">'
            '<span class="pjtag">{tag}</span><span class="pjgoal">{goal}</span>{s}{x}'
            '</div>'.format(
                i=t["id"], tag=esc(t["tag"] or t["slug"]),
                goal=esc(_human(_real_goal(t))), s=spark, x=drop))


def _closed_row(c):
    """Трофей на полке: ✓ тег · итог · дата закрытия. Клик ПРОВАЛИВАЕТ в полный
    контекст закрытой нити (план, гейты, сессии) — как у живой (решение 12.07:
    закрытую тоже надо провалить, не только прочитать итог)."""
    import urllib.parse as _up
    head = _human(c["final"])
    head = (head[:150].rsplit(" ", 1)[0] + "…") if len(head) > 150 else head
    # ⟲ вернуть закрытую нить в работу (симметрия ✕ закрытию, решение 14.07) — за
    # confirm-модалкой; stopPropagation, чтобы не провалиться в трофей
    reopen = ('<a class="sbtn" href="#" title="⟲ вернуть нить в работу" '
              'onclick="{oc}">⟲</a>'.format(oc=_confirm_onclick(
                  url="/reopen?d=" + _up.quote(str(c["path"])),
                  title="Вернуть нить «{0}» в работу?".format(c["tag"]),
                  body="Снова откроется на полке. Свежую сессию поднимешь ▶.",
                  ok="вернуть", danger=False)))
    return ('<div class="pjrow" role="button" tabindex="0" onclick="openT(\'{i}\')">'
            '<span class="pjtag">✓ {tag}</span><span class="pjgoal">{f}</span>'
            '<span class="pjage">{d}</span>{r}</div>'.format(
                i=c["id"], tag=esc(c["tag"]), f=esc(head) or "итог — по клику",
                d=esc(c["date"]), r=reopen))


def _shelf_sec(label, rows, top="", count=None):
    """Секция полки: заголовок · N, необязательный `top`-элемент (форма ввода,
    не в счёте), затем ряды. *count* переопределяет N, когда ряды обёрнуты в
    один контейнер (напр. грид работ) — иначе счёт был бы 1."""
    n = len(rows) if count is None else count
    return '<div class="pjsec"><div class="lbl">{0} · {1}</div>{2}{3}</div>'.format(
        label, n, top, "".join(rows))


def _new_thread_form(proj):
    """＋ старт новой нити прямо с доски (решение 13.07): вписать имя → старт → нить
    заводится и поднимается оркестрирующей сессией (движок /new-thread). Enter в
    поле = старт. Стоит вверху полки, над «живыми нитями»."""
    return ('<div class="ntf">'
            '<input class="nti" type="text" maxlength="120" '
            'placeholder="＋ новая нить — впиши название и жми старт…" '
            'onkeydown="if(event.key===\'Enter\'){{deckNewThread(\'{p}\',this)}}">'
            '<a class="abtn primary ntb" href="#" onclick="event.preventDefault();'
            'deckNewThread(\'{p}\',this.previousElementSibling)">старт</a></div>'.format(
                p=esc(proj)))


def _add_cand_form(proj):
    """＋ добавить идею-кандидата прямо с полки (решение 13.07): вписать идею →
    добавить → ложится в бэклог как candidate (движок /add-cand, from: рукой).
    Как форма нити, но вторичной кнопкой (не амбер — идея легче, чем старт нити).
    Стоит вверху секции «идеи»."""
    return ('<div class="ntf acf">'
            '<input class="nti" type="text" maxlength="500" '
            'placeholder="＋ новый кандидат — впиши и жми добавить…" '
            'onkeydown="if(event.key===\'Enter\'){{deckAddCand(\'{p}\',this)}}">'
            '<a class="abtn ntb" href="#" onclick="event.preventDefault();'
            'deckAddCand(\'{p}\',this.previousElementSibling)">добавить</a></div>'.format(
                p=esc(proj)))


# ── заметки проекта (решение 17.07): карточки-справки «просто помни» ──────────
# Русло — файлы: <proj>/.tide/notes/NN-slug.md; первая строка `# заголовок`,
# опц. `tags: деплой, прод`, дальше свободное тело (команды в ``` блоках).
# Третья вещь рядом с нитями и кандидатами: кандидат — идея на потом, работа —
# делаем и чекаем, заметка — быстро достать («команда снятия заглушки»).


def read_notes(proot):
    """Заметки проекта: [{slug, title, tags, body, journal, mtime}], новейшие
    сверху. ## журнал — история жестов (кто когда правил, принцип №3), в тело
    не входит; удалённые лежат в notes/__dropped__/ и сюда не читаются."""
    d = Path(proot) / ".tide" / "notes"
    out = []
    if not d.is_dir():
        return out
    for f in sorted(d.glob("*.md"), reverse=True):
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        text, _, jraw = text.partition("## журнал")
        journal = [l.strip("- ").strip() for l in jraw.splitlines()
                   if l.strip().startswith("-")]
        lines = text.splitlines()
        title = lines[0].lstrip("# ").strip() if lines else f.stem
        m = re.search(r"^tags:\s*(.+)$", text, re.M)
        tags = [t.strip() for t in m.group(1).split(",") if t.strip()] if m else []
        body = re.sub(r"^#.*\n|^tags:.*\n", "", text, count=2, flags=re.M).strip()
        out.append({"slug": f.stem, "title": title or f.stem, "tags": tags,
                    "body": body, "journal": journal, "mtime": f.stat().st_mtime})
    return out


def _pnote_body_html(body):
    """Тело заметки → блоки (решение 17.07: «нормальное форматирование, код
    отдельно скопировать»): проза — абзацами (строки внутри абзаца склеены),
    код (4-пробельный отступ или ```-забор) — своим блоком с СОБСТВЕННОЙ ⧉.
    Комментарные строки # внутри кода остаются в блоке, но в буфер ⧉ кладёт
    только команды — их и достают."""
    blocks, para, code, fenced = [], [], [], False

    def flush_para():
        if para:
            blocks.append(("p", " ".join(" ".join(para).split())))
            para.clear()

    def flush_code():
        if code:
            blocks.append(("code", "\n".join(code)))
            code.clear()

    for ln in body.splitlines():
        if ln.strip().startswith("```"):
            flush_para() if not fenced else flush_code()
            fenced = not fenced
            continue
        if fenced or ln.startswith(("    ", "\t")):
            flush_para()
            code.append(ln[4:] if ln.startswith("    ") else ln.lstrip("\t"))
            continue
        if not ln.strip():
            flush_para(); flush_code()
            continue
        flush_code()
        para.append(ln.strip())
    flush_para(); flush_code()

    out = []
    for kind, text in blocks:
        if kind == "p":
            out.append('<p class="ntp">{0}</p>'.format(esc(text)))
        else:
            cmds = "\n".join(l for l in text.splitlines()
                             if l.strip() and not l.strip().startswith("#"))
            # показываем код целиком, копируем только команды — строки-
            # комментарии в терминале не нужны
            out.append(_copy_block(text, "code", "команду", copy_text=cmds or text))
    return "".join(out)


def _note_row(n, proj):
    """Ряд заметки на полке: [заголовок · теги · ⧉ всё · шеврон] — тело в
    развороте блоками (_note_body_html), у каждого кода своя ⧉. Правка как у
    работ (решение 17.07): клик по заголовку — на месте; клик по абзацу тела —
    редактор сырого текста (теги там же строкой tags:). Сырьё для редактора —
    в <template>, чтобы не экранировать в атрибут."""
    import urllib.parse as _up
    chips = "".join('<span class="skchip">{0}</span>'.format(esc(t)) for t in n["tags"])
    raw = ("tags: {0}\n\n".format(", ".join(n["tags"])) if n["tags"] else "") + n["body"]
    # журнал — тихой историей под телом (кто когда правил, новейшее сверху);
    # ✕ — мягкое удаление в __dropped__ за confirm-модалкой (разобраться можно)
    jrows = "".join('<div class="ntj">{0}</div>'.format(esc(j))
                    for j in reversed(n["journal"][-4:]))
    drop = _confirm_onclick(
        "/note-drop?proj={0}&f={1}".format(_up.quote(proj), _up.quote(n["slug"])),
        "Убрать заметку «{0}»?".format(n["title"][:60]),
        "Уйдёт в notes/__dropped__/ с записью в журнале — вернуть можно рукой.",
        ok="убрать", danger=True)
    return ('<details class="skrow ntrow" data-nt="{s}" data-ntp="{p}"><summary>'
            '<span class="sknm" data-ntitle="1">{t}</span>{c}<span class="sksub"></span>'
            '<a class="sbtn" href="#" title="скопировать заметку целиком" '
            'onclick="{cp}">⧉</a>'
            '<a class="sbtn" href="#" title="убрать заметку" onclick="{dr}">✕</a>'
            '{ch}</summary>'
            '<div class="skbody ntbody">{b}{j}</div>'
            '<template class="ntraw">{raw}</template>'
            '</details>').format(
                s=esc(n["slug"]), p=esc(proj), t=esc(n["title"]), c=chips,
                cp=_copy_onclick(n["body"]), dr=drop, ch=CHEVRON,
                b=_pnote_body_html(n["body"]), raw=esc(raw),
                j=('<div class="ntjs">{0}</div>'.format(jrows) if jrows else ""))


def _add_note_form(proj):
    """＋ заметка с полки: «заголовок | тело» одной строкой (движок /note-add);
    теги — словом в теле `tags: …`, или дописать в файл потом."""
    return ('<div class="ntf acf">'
            '<input class="nti" type="text" maxlength="900" '
            'placeholder="＋ заметка: заголовок | текст (tags: деплой)…" '
            'onkeydown="if(event.key===\'Enter\'){{deckAddNote(\'{p}\',this)}}">'
            '<a class="abtn ntb" href="#" onclick="event.preventDefault();'
            'deckAddNote(\'{p}\',this.previousElementSibling)">записать</a></div>'.format(
                p=esc(proj)))


def _work_num(slug):
    """Номер работы из слага NN-… — голой строкой: он стоит ПЕРВЫМ в самом
    заголовке, «22 · Название», тем же кеглем и инком, что имя (фикс 10 работы
    22: тихий чип «работа NN» в мете тонул, а человек диктует решения именно по
    номерам — глаз должен цеплять номер рядом с названием, не искать в мелкой
    строке). Тот же якорь, что на столе ISSUES (_issue_kind_html), и номер оба
    берут одним регексом (_ISSUE_NUM_RE — живёт у стола, резолвится при
    вызове). Слаг без номера — заголовок как есть, а не «0 ·» из воздуха."""
    m = _ISSUE_NUM_RE.match(slug)
    return str(int(m.group(1))) if m else ""


def _work_step_num(meta):
    """Номер шага плана, к которому работа привязана, — строкой ('' если нет).
    Поле `step:` в паспорте работы (работа 44). Пишут его станок и рука, поэтому
    берём ЧИСЛО из чего угодно («3», «шаг 3», «3.») и молчим, если числа нет:
    кривой адрес — не повод ронять вкладку, работа просто уйдёт «без шага»."""
    m = re.search(r"\d+", (meta.get("step") or ""))
    return str(int(m.group(0))) if m else ""


def _step_of(steps, meta):
    """Запись шага плана, под которым стоит работа, или None. None — и когда
    шага нет в паспорте, и когда он есть, но такого шага в плане уже не осталось
    (план правится версиями, работа могла остаться со старым номером): такая
    работа честнее лежит «без шага», чем под выдуманным заголовком."""
    num = _work_step_num(meta)
    if not num or not steps:
        return None
    return next((s for s in steps["items"] if s["num"] == num), None)


# ── лицо карточки работы = её состояние (решение 30.07, работа 21) ─────────────
# «Агент общается со мной через карточку: её вид показывает текущее состояние;
# хочу подробности — проваливаюсь». Поэтому лицо несёт ОДНУ строку человеческим
# языком — чей ход и что происходит сейчас, — а не развёрнутый чеклист логом.
# Строка одна на все площадки (карточка вкладки «работа» и компактный блок
# полки/нити): разъедься они, работа говорила бы о себе в двух местах разное.
def _ru_plural(n, one, few, many):
    """Русское число словом: 1 шаг · 2 шага · 5 шагов. Строку состояния читают
    как речь, и «1 шагов» в ней сразу выдаёт машину."""
    n10, n100 = n % 10, n % 100
    if n10 == 1 and n100 != 11:
        return one
    if 2 <= n10 <= 4 and not 12 <= n100 <= 14:
        return few
    return many


def _work_state(st, items, journal, title="", now=None, cursor=0):
    """Состояние работы одной строкой → (класс, текст, пункт-курсор).

    Порядок веток = порядок важности для человека: сперва то, что ждёт ЕГО
    (предложенные шаги, приёмка), потом то, что делает агент. Прогресс и фиксы
    идут припиской к главному, а не отдельными счётчиками: «агент делает: X ·
    3/7 · ещё 2 фикса» читается вслух, ряд из трёх пилюль — нет.

    «АГЕНТ ДЕЛАЕТ» КОРМИТСЯ ТОЛЬКО КУРСОРОМ (решение 31.07, фикс 4 работы 26).
    Раньше строка брала первый несделанный пункт — та самая эвристика, что уже
    соврала человеку: пункты делаются не по порядку, и лицо уверенно называло
    не то, чем агент занят. Теперь имя шага говорит сам агент вербом
    `tide work at` (движок 1.0.44); *cursor* — номер оттуда, уже проверенный на
    свежесть вызывающим. Курсора нет — «агента делает» НЕТ ВОВСЕ: остаётся
    честный счёт. Молчать тут лучше, чем гадать.

    Третьим отдаём текст пункта-курсора: разметку (кобальтовый маркер — один
    язык с подчёркиванием «здесь агент») клеит _work_state_html, а тут живёт
    только смысл."""
    agreed = [it for it in items if it[0] != "?"]
    n, nd = len(agreed), sum(1 for it in agreed if it[0] == "x")
    n_prop = sum(1 for it in items if it[0] == "?")
    fx_all = sum(1 for it in items if it[3])
    fx_left = sum(1 for it in items if it[3] and it[0] == " ")
    fxw = lambda k: "{0} {1}".format(k, _ru_plural(k, "фикс", "фикса", "фиксов"))
    # предложенное перебивает всё: пока человек не кивнул, «работа идёт» — враньё
    if n_prop:
        return ("prop", "ждёт твоего «да»: {0} {1} {2}".format(
            _ru_plural(n_prop, "предложен", "предложено", "предложено"),
            n_prop, _ru_plural(n_prop, "шаг", "шага", "шагов")), "")
    if st == "done":
        return ("done", "закрыта · {0}/{1}".format(nd, n) if n else "закрыта", "")
    if st == "review":
        # «с пруфами» — не украшение: пруф пишет только `tide work check
        # --proof`, и сказать про него, когда журнал пуст, значит соврать
        cm = _work_checkmap(journal)
        proofs = sum(1 for i, it in enumerate(items)
                     if it[0] == "x" and cm.get(i + 1, ("", ""))[1])
        # рука уже прошлась по части пунктов (работа 22) — счётчик говорит про
        # ПРИЁМКУ, а не про чеки исполнителя: «X/X» звало бы принимать то, что
        # человек принял вчера, и он не понимал бы, где остановился
        acc = _work_accepted(items, journal)
        n_acc = sum(1 for i, it in enumerate(items)
                    if it[0] == "x" and i in acc)
        txt = ("ход твой: прими работу — принято {0} из {1}".format(n_acc, n)
               if n_acc else "ход твой: прими работу — {0}/{1}{2}".format(
                   nd, n, " с пруфами" if proofs else ""))
        if fx_all:
            txt += " · " + fxw(fx_all)
        return ("rev", txt, "")
    if st == "taken":
        # ТИШИНА ПЕРЕБИВАЕТ ВСЁ (работа 39): пока по работе идут жесты, строка
        # рассказывает про стройку; замолчала дольше порога — рассказывать
        # больше не о чем, и единственная честная новость это само молчание.
        # Счёт остаётся припиской: он не врёт ни при какой тишине.
        quiet = _work_quiet(journal, now)
        if quiet:
            return ("quiet", "{0} · {1}/{2}".format(quiet, nd, n) if n
                    else quiet, "")
        if not n:
            return ("live", "агент взял работу — плана пока нет", "")
        # ИМЯ ШАГА — ТОЛЬКО ИЗ КУРСОРА (фикс 4 работы 26). Пункт, названный
        # ИМЕНЕМ РАБОТЫ (агенты часто так пишут первый), не называем даже под
        # курсором: лицо сказало бы дважды одно, а строка состояния должна
        # добавлять к шапке, а не повторять её
        cur = ""
        if 1 <= cursor <= len(items):
            cur = items[cursor - 1][1]
            if cur.strip().lower() == title.strip().lower():
                cur = ""
        if cur and len(cur) > 72:
            cur = cur[:71].rstrip() + "…"
        if nd >= n:
            txt = "шаги закрыты — агент ещё не сдал · {0}/{1}".format(nd, n)
        elif cur:
            # НОМЕР ПЕРВЫМ, как в заголовке работы и в чеклисте: им человек
            # диктует («доделай третий»), и он же связывает строку лица с
            # подчёркнутым пунктом ниже
            txt = "агент делает: {0} · {1} · {2}/{3}".format(
                cursor, cur, nd, n)
        else:
            # курсора нет — счёт и есть весь честный ответ
            txt = "{0}/{1}".format(nd, n)
        if fx_left:
            txt += " · ещё " + fxw(fx_left)
        return ("live", txt, cur if nd < n else "")
    return ("open", "не взята — возьмёт агент", "")


def _work_fresh(items, checkmap, now):
    """Свежий пруф-чек работы → (свежо?, пруф последнего). Чеклист с лица ушёл,
    а вместе с ним ушла бы и пульсирующая галочка (шов 3) — «результат воркера
    виден прилетающим». Здесь тот же сигнал сворачивается в одну точку у строки
    состояния: пульсит, пока чек моложе FRESH_CHECK_MIN."""
    best_dt, best_proof = None, ""
    for i, it in enumerate(items):
        rec = checkmap.get(i + 1) if it[0] == "x" else None
        if not rec:
            continue
        try:
            dt = datetime.strptime(rec[0], "%Y-%m-%d %H:%M")
        except ValueError:
            continue  # кривая дата в журнале — просто не подсвечиваем
        if (now - dt).total_seconds() > FRESH_CHECK_MIN * 60:
            continue
        if best_dt is None or dt >= best_dt:
            best_dt, best_proof = dt, rec[1]
    return best_dt is not None, best_proof


# отметка времени в строке журнала работы: «- 2026-07-30 14:54 — …»
_WORK_JRN_TS = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2})")
# сколько работа считается живой после последнего жеста. 30 минут — цена
# честности: агент, реально пашущий по работе, пишет в журнал чаще (взял, чек,
# фикс, пруф); тишина дольше получаса значит «отошёл», а не «делает».
WORK_LIVE_MIN = 30


def _work_last_move(journal):
    """Когда по работе последний раз ДВИГАЛИ — datetime последней строки журнала
    (или None у пустого/кривого). Одна точка правды на «N мин» под шапкой и на
    зелёную точку: разъедься они, карточка сказала бы «агент работает» рядом с
    «18 ч». Читаем журнал, а не mtime файла: mtime двигает любой чужой touch,
    журнал пишут только настоящие жесты."""
    for line in reversed(journal):
        m = _WORK_JRN_TS.match(line)
        if not m:
            continue
        try:
            return datetime.strptime(m.group(1), "%Y-%m-%d %H:%M")
        except ValueError:
            continue  # кривая дата — ищем строку глубже
    return None


# след отправки строителя (верб `tide work dispatch`, движок 1.0.45): строка
# журнала «строитель отправлен: имя». Для доски это ОБЫЧНЫЙ жест журнала — он
# двигает свежесть работы, как чек или курсор; особенным его делает только то,
# что после него ждут строителя, и молчание сразу за ним значит другое
_DISPATCH_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}) — строитель отправлен: (.+)$")


def _work_dispatch(journal):
    """(индекс в журнале, когда, кто) последней отправки строителя — или None."""
    out = None
    for i, j in enumerate(journal):
        m = _DISPATCH_RE.match(j)
        if m:
            out = (i, m.group(1), m.group(2).strip())
    return out


def _work_quiet(journal, now=None):
    """Взятая работа, по которой ТИХО, — одной честной строкой (или '').

    Работа 39, случай 35: карточка три часа выглядела живой, а строителя на ней
    не было. `status: taken` — это про паспорт, а не про то, что кто-то работает;
    правду знает только журнал, и меряем мы её тем же порогом, каким доска судит
    сессию без пульса (_WORK_FRESH): молчит дольше — из цикла выпал.

    МОЛЧАНИЙ ДВА, И ЭТО РАЗНЫЕ БЕДЫ (уточнение 01.08):
      · строителя не отправляли — работа просто лежит взятой, никто не звал;
      · строителя отправили, а жестов после отправки нет — самый тревожный
        случай: человек ждёт, оркестратор думает, что послал, а стройки нет.
    Третий вид тишины — строитель работал и замолк — называем его же именем.
    Гадать, какая из трёх, человек не должен: и имя, и время есть в журнале."""
    last = _work_last_move(journal)
    if last is None:
        return ""
    if ((now or datetime.now()) - last).total_seconds() <= _WORK_FRESH * 60:
        return ""
    ago = age(last.timestamp())
    disp = _work_dispatch(journal)
    if disp is None:
        return "взята, строитель не отправлен — тихо {0}".format(ago)
    i, _when, who = disp
    if len(who) > 28:
        who = who[:27].rstrip() + "…"
    if i == len(journal) - 1:  # отправка — ПОСЛЕДНЕЕ, что было
        return "строитель {0} отправлен {1} назад, жестов нет".format(who, ago)
    return "строитель {0} молчит {1}".format(who, ago)


def _work_live(journal, now=None):
    """Пашет ли по работе агент ПРЯМО СЕЙЧАС.

    Спрашиваем ЖУРНАЛ САМОЙ РАБОТЫ (решение 30.07, фикс 9 работы 22: зелёная точка
    горела на работе, по которой никто не двигался 18 часов). До этого сигнал
    брался с пульса ВЕДУЩЕЙ НИТИ — а пульс нити ≠ пульс работы: нить живёт
    своей жизнью (человек в ней разговаривает, агент делает соседнюю работу), и
    точка на этой карточке была враньём. Паспорт тоже не годится: `status:
    taken` остаётся в файле и после того, как сессия остыла.

    Настоящее движение по работе оставляет строку в её журнале — по ней и
    судим. Тишина дольше WORK_LIVE_MIN — работа остыла, точки нет."""
    last = _work_last_move(journal)
    if last is None:
        return False
    return ((now or datetime.now()) - last).total_seconds() <= WORK_LIVE_MIN * 60


def _work_age(journal):
    """«N мин» с последнего движения работы (решение 30.07, фикс 6 работы 21:
    «видно, живёт работа или остыла»). Пустой журнал — честно ничего."""
    last = _work_last_move(journal)
    return age(last.timestamp()) if last else ""


def _work_is_fav(meta):
    """Работа в избранных? Строка `fav: yes` в шапке паспорта — то же русло,
    что у артефакта (у нити список лежит в контрол-хоуме, у работы и артефакта
    — в их собственных паспортах: это вещи, а не адреса)."""
    return meta.get("fav", "").strip().lower() in ("yes", "true", "1")


def _work_star(slug, fav):
    """★ на работе — ровно звезда нити (_card_fav) и артефакта (_artifact_star):
    залитая значит «моё, держу сверху», клик снимает. Избранные работы идут
    первыми в списке — и на общей вкладке, и в нити.

    Жест ЗАПЕРТ на один запрос (болячка 30.07, артефакт 100: повторный клик до
    свопа доски дописывал в журнал лишние строки): stopPropagation не пускает
    клик ни в проваливание карточки, ни в делегаты вкладки, а замок busy глушит
    второй клик, пока первый едет, — узел со свопом уезжает, снимать нечего."""
    on, glyph = ("0", ICON_STAR_ON) if fav else ("1", ICON_STAR)
    title = "убрать из избранных" if fav else "в избранные"
    s = re.sub(r"[\"'\\\n\r]", "", slug)
    oc = ("event.preventDefault();event.stopPropagation();"
          "if(this.dataset.busy)return false;this.dataset.busy='1';"
          "fetch('/work-fav?on={on}&f='+encodeURIComponent('{s}'))"
          ".then(function(r){{return r.text()}}).then(function(x){{deckToast(x);"
          "if(typeof boardRefresh==='function')boardRefresh();}})").format(
              on=on, s=s)
    return '<a class="pjhold wkfav" href="#" title="{0}" onclick="{1}">{2}</a>'.format(
        title, oc, glyph)


def _dive_btn(lead, oc):
    """Провал к ведущему агенту — круглая кнопка-иконка ↗ того же кита, что ✕ и
    ★ у нитей (.pjhold). Раньше тут висела пилюля «провалиться →» (фикс 8 работы
    21: с телефона подпись съедала строку меты, а жест за ней ровно один). Имя
    ведущей нити ушло в подсказку: на лице оно дублировало контекст страницы.
    Прыгать некуда (нить не привязана или агент спит) — кнопки честно нет."""
    if not oc:
        return ""
    return ('<a class="pjhold wkdive" href="#" title="провалиться к ведущему '
            'агенту{0}" onclick="event.stopPropagation();{1}">{2}</a>'.format(
                " — " + esc(lead) if lead else "", oc, ICON_DIVE))


def _work_badge(st, live, quiet=False):
    """Бейдж состояния в шапке лица. Живой («агент работает», зелёная точка) —
    ровно плашка нити st-work: он говорит про РЕАЛЬНОЕ движение по работе
    (_work_live — журнал моложе получаса). «в работе» — про паспорт: работа
    взята, но по ней сейчас тихо.

    У остывшей ТОЧКИ НЕТ ВООБЩЕ (решение 30.07, фикс 9 работы 22). Раньше она
    носила кобальтовую с пульсом — и на карточке, где никто не двигался сутки,
    пульсирующий кружок читался как «прямо сейчас идёт». Точка на доске значит
    живое движение; нет движения — нет и точки, остаётся тихий капс."""
    if st == "taken":
        # «в работе» на молчащей работе — та же ложь, что зелёная точка на
        # остывшей (фикс 9 работы 22), только тише: паспорт говорит про запись
        # в файле, а человек читает её как «идёт стройка». Молчит дольше порога
        # — бейдж честно говорит «тихо», подробности несёт строка состояния
        return ('<span class="wkstat work"><span class="dot"></span>'
                'агент работает</span>' if live else
                '<span class="wkstat quiet">тихо</span>' if quiet else
                '<span class="wkstat">в работе</span>')
    if st == "review":
        return ('<span class="wkstat rev"><span class="dot"></span>'
                'на проверке</span>')
    return ""


def _work_cursor(meta, journal, now=None):
    """Номер пункта, на котором агент СЕЙЧАС (`at:`, верб `tide work at`) — или
    0. Курсору верим, ПОКА РАБОТА ЖИВА по своему журналу (то же окно, что у
    зелёной точки): агент упал, не сняв метку, — она гаснет сама, а не врёт
    сутками, что кто-то тут сидит. Один ответ на обоих читателей — подчёркивание
    пункта и строку «агент делает», чтобы они не разъехались."""
    num = meta.get("at", "").strip()
    return int(num) if num.isdigit() and _work_live(journal, now) else 0


def _work_state_html(st, items, journal, now=None, title="", cursor=0):
    """Строка состояния готовой разметкой — ОДИН вызов на лицо карточки и на
    компактный блок полки/нити, чтобы работа везде говорила о себе одно.

    Имя пункта под курсором несёт кобальтовый маркер — ту же линию, которой
    подчёркнут сам пункт в чеклисте (фикс 4 работы 26): человек должен видеть
    одним взглядом, что строка лица и подчёркнутый шаг говорят про одно место.
    Метку клеим по УЖЕ ЭКРАНИРОВАННОМУ тексту: и строка, и кусок под маркером
    проходят esc() порознь, поэтому подмена не может внести разметку."""
    now = now or datetime.now()
    cls, txt, cur = _work_state(st, items, journal, title, now, cursor)
    fresh, proof = _work_fresh(items, _work_checkmap(journal), now)
    dot = ('<span class="wkfrdot"{0}></span>'.format(
        ' title="{0}"'.format(esc(proof[:240])) if proof else "")
        if fresh else "")
    body = esc(txt)
    if cur:
        body = body.replace(esc(cur),
                            '<span class="wkcur">{0}</span>'.format(esc(cur)), 1)
    return '<div class="wkstate {0}">{1}{2}</div>'.format(cls, dot, body)


def _work_block(slug, title, meta, items, dl, by_dir, today, done=False,
                journal=(), desc="", plan=(), expand=False, steps=None):
    """Блок работы для ленты полки и вкладки «работы» нити (решение 17.07: «видно,
    как работа идёт»): НАЗВАНИЕ во всю ширину · одна тихая мета-строка · СТРОКА
    СОСТОЯНИЯ. Клик по блоку → модалка (полный вход).

    *expand* — блок несёт ВНУТРИ себя полный вид (`.wkfull`: паспорт · план ·
    описание · чеклист · журнал), и площадка нити разворачивает его сразу
    (решение 07.08: «работа нити сразу видна развёрнутой» — план и предложенные
    шаги читаются, не проваливаясь). Разметку строит та же _work_full, что и на
    большой карточке вкладки «работа»: своей копии чеклиста тут нет. Вместе с
    ней приезжает `data-wk` — жесты пунктов адресуются по нему и по data-i.
    Ленте полки полный вид не нужен: там правило «лицо = состояние» осталось
    как было (решение 30.07, работа 21), и лишний скрытый чеклист в каждом блоке
    был бы просто весом страницы.

    Превью первых трёх пунктов тут стояло до работы 21 — и врало тем же, чем
    чеклист на лице: показывало первые строки списка, а не то, что происходит.
    Состояние компакт берёт той же функцией, что большая карточка.

    Разложен так же, как лицо большой карточки (фикс 10 работы 21): раскладка у
    работы одна на все площадки. Подвал с «ведёт: <нить>» отсюда ушёл — компакт
    и так стоит ВНУТРИ нити, и строка повторяла человеку заголовок страницы."""
    st = meta.get("status", "open")
    # номер — ПЕРВЫМ в самом заголовке, «22 · Название» (фикс 10 работы 22):
    # по нему работу зовут словами — и у закрытых; в мете он больше не живёт.
    # Номер — отдельной переменной disp: title дальше уходит в строку
    # состояния (_work_state_html), и номер туда просачиваться не должен
    num = _work_num(slug)
    disp = "{0} · {1}".format(num, title) if num else title
    # мета-строка компакта — та же, что на большой карточке: живой статус
    # (движение по журналу самой работы), дедлайн, свежесть
    meta_chips = [_work_badge(st, not done and _work_live(journal),
                              quiet=bool(not done and _work_quiet(journal)))]
    if dl and not done:
        cls = ("bad" if dl < today else "warn" if dl == today else "")
        meta_chips.append('<span class="wkdl {0}">до {1}</span>'.format(
            cls, dl.strftime("%d.%m")))
    ago = _work_age(journal)
    if ago:
        meta_chips.append('<span class="wkage">{0}</span>'.format(esc(ago)))
    # РУК НА КОМПАКТЕ НЕТ (решение 31.07, работа 26: «с этой карточкой фейвериты
    # и переход на сессию можно убрать»). Тут стояли ★ и ↗ — правым краем меты,
    # как у большой карточки; ими с этой поверхности не пользовались, а лицо
    # они шумели: компакт читают глазами по ленте, а не тыкают.
    # Жесты никуда не делись, у них просто другие площадки: ★ живёт на большой
    # карточке вкладки «работа» и в модалке (и в самом файле — сортировка
    # избранных по `fav:` работает как работала), вход в ведущую сессию — со
    # стола ISSUES и с карточки нити. Обработчики и ручки сервера не тронуты:
    # тут перестала рисоваться разметка, а не пропал жест.
    state = _work_state_html(st, items, journal, title=title,
                             cursor=_work_cursor(meta, journal))
    # полный вид — общей сборкой (см. докстринг): развернёт его CSS площадки.
    # *steps* и in_thread — про строку «куда ведёт» (работа 44): площадка нити
    # уже сказала человеку и нить (заголовок страницы), и шаг (заголовок
    # группы), поэтому строка тут договаривает только результат
    full = (_work_full(_work_parts(slug, meta, desc, items, journal, plan,
                                   by_dir, dl, today, plan_open=True,
                                   steps=steps, in_thread=True))
            if expand else "")
    return ('<div class="wkblk{cl}"{wk} data-wkopen="{s}" tabindex="0">'
            '<div class="wkblktop"><span class="wkblknm">{t}</span></div>'
            '<div class="wkmeta">{c}</div>{st}{full}</div>'.format(
                cl=" done" if done else "", s=esc(slug), t=esc(disp),
                wk=' data-wk="{0}"'.format(esc(slug)) if expand else "",
                c="".join(meta_chips), st=state, full=full))


def _proj_match(work_proj, name):
    """Работа *work_proj* живёт в доме *name*? Матч по полю project: с допуском
    на алиас (короткое имя — префикс длинного, ≥4 симв.). Правило одно
    на всех, кто фильтрует по дому — полка проекта, вкладка работ нити и её же
    стол issues: разъедься они, человек видел бы на одной странице разный набор
    работ одного проекта."""
    norm = lambda s: re.sub(r"[^a-z0-9]", "", (s or "").lower())
    wp, want = norm(work_proj), norm(name)
    if not wp or not want:
        return False
    pref = min(len(wp), len(want)) >= 4 and (
        wp.startswith(want) or want.startswith(wp))
    return wp == want or pref


def _works_home():
    """Дом самих работ (tide-stack) — тот проект, от которого читается ГОЛЫЙ
    адрес нити в паспорте: `tide work take` пишет «NN-@нить» без дома, только
    когда сессия сидит в этом же доме (resolve_caller_thread)."""
    return WORKS_DIR.parents[2].name


def _thread_addr(proj, tdir):
    """Каталог нити → адрес для поля thread: голый слаг у дома работ, «дом/нить»
    у соседнего — ровно как его пишет сам станок. Подчёркивания закрытой нити
    (`__NN-@нить__`) в адрес не идут: адрес должен пережить закрытие."""
    slug = (tdir or "").strip("_")
    return slug if proj == _works_home() else "{0}/{1}".format(proj, slug)


def _thread_match(work_thread, proj, tdir, own=""):
    """Ответственная нить работы — это нить *tdir* дома *proj*? Сравниваем
    адресами (см. _thread_addr): голый слаг читается от дома работ, «дом/нить» —
    от названного дома (там допуск на алиас, как везде). Работа без thread:
    ничья — внутри нити её нет, но с полки дома и с общей вкладки «работа» она
    никуда не делась.

    *own* — дом самой работы. Голый слаг движок пишет, когда работа и нить
    живут в одном проекте, — значит и читать его надо от дома работы, а не от
    дома-верфи. Пусто (работа из общей папки) — по-старому, от верфи."""
    addr = (work_thread or "").strip()
    if not addr or not tdir:
        return False
    home, _, slug = addr.rpartition("/")
    if slug.strip("_") != tdir.strip("_"):
        return False
    return _proj_match(home, proj) if home else _proj_match(own or _works_home(),
                                                            proj)


def _tdir(t):
    """Каталог нити: у живой он в паспорте, у закрытой — имя её папки."""
    return t.get("dir") or t["path"].name


def _step_head(step, n_works, n_done, n_items):
    """Заголовок группы шага на вкладке «работы» (работа 44). Читается как СТРОКА
    ПЛАНА, а не как техническая метка: номер и имя шага теми же словами, что в
    plan.md, и прогресс тихой припиской справа — сколько работ и сколько пунктов
    в них закрыто.

    Знак состояния берём готовым — это .step с её кружком (закон 47, таймлайн
    нити): сделанный шаг тускнеет и получает залитый квадрат, текущий — кобальт
    в кольце, будущий стоит пустым. Заводить второй язык для того же смысла
    нельзя: человек ходит между таймлайном и работами одной нити, и «сейчас» в
    двух местах обязано выглядеть одинаково. Соединительной линии между шагами
    тут нет (гасим .mc::after): в таймлайне шаги идут подряд, а здесь между ними
    стоят карточки работ, и нитка тянулась бы сквозь них в пустоту."""
    st = (step or {}).get("state", "")
    num = (step or {}).get("num", "")
    nm = (step or {}).get("name", "") or "без шага"
    head = "{0} · {1}".format(num, nm) if num else nm
    prog = "{0} {1}".format(n_works, _ru_plural(n_works, "работа", "работы",
                                                "работ"))
    if n_items:
        prog += " · {0}/{1} {2}".format(
            n_done, n_items, _ru_plural(n_items, "пункт", "пункта", "пунктов"))
    return ('<div class="step wkstep {c}"><span class="mc"><span class="m">'
            '</span></span><div class="body"><span class="gt">{h}</span>'
            '<span class="wkstepp">{p}</span></div></div>'.format(
                c=st, h=esc(head), p=esc(prog)))


def _step_groups(rows, steps):
    """Карточки работ, разложенные ПОД ШАГАМИ плана (работа 44, слово человека
    07.08: «хочется чтобы работа что сейчас делается бралась в рамках куда
    идём»). *rows* — [(шаг|None, карточка, пункты)], наружу — плоский список
    html: заголовок группы и её карточки идут подряд, столбик .wkblkcol и так
    ставит их друг под другом.

    Порядок — порядок ПЛАНА, а не свежести: список отвечает на «куда идём», и
    переставлять шаги местами значило бы врать про дорогу. Пустых шагов нет:
    вкладка «работы» — не вторая копия плана, шаг появляется здесь ровно тогда,
    когда им кто-то занят. Работы без шага (и со ссылкой на шаг, которого в
    плане уже нет) идут последней группой «без шага» — они не пропадают и не
    притворяются частью дороги."""
    by_num, off = {}, []
    for step, card, items in rows:
        (by_num.setdefault(step["num"], []) if step else off).append(
            (card, items))
    out = []
    for step in steps["items"] + [None]:
        grp = off if step is None else by_num.get(step["num"], [])
        if not grp:
            continue
        # пункты считаем как строка состояния работы: согласованные ([ ] и [x]),
        # предложенные агентом ещё не работа и в знаменатель не идут
        agreed = [it for _c, its in grp for it in its if it[0] != "?"]
        out.append(_step_head(step, len(grp),
                              sum(1 for it in agreed if it[0] == "x"),
                              len(agreed)))
        out.extend(c for c, _its in grp)
    return out


class _WorkCol(NamedTuple):
    """Колонка работ: готовое тело и число РАБОТ в нём. Двумя полями одной записи
    — чтобы счётчик вкладки и нарисованный список считались из одного места.

    Раньше наружу шёл просто список html-кусков, а звавшие мерили его len(). Пока
    кусок = карточка, это сходилось; как только между карточками встали заголовки
    групп-шагов (работа 44), len() начал считать заголовки работами — вкладка
    печатала «работы · 2» там, где карточка одна, и спойлер «закрытые · 2» на
    одну закрытую. Счёт и список обязаны иметь ОДИН источник, иначе доска врёт
    ровно в тот момент, когда вид усложняется."""
    html: str
    n: int


def _project_works(name, by_dir, tdir=None, steps=None):
    """(открытые, закрытые) работы проекта *name* двумя _WorkCol (решение 17.07:
    «не вижу работ на полке», «закрытые тоже снизу», «во всю строку»). Работы
    живут в tide-stack, у каждой поле project: — её дом (см. _proj_match).

    *tdir* — сито не по дому, а по НИТИ (работа 24): вкладка «работы» страницы
    нити показывает только работы этой нити. Ничьи и работы соседних нитей того
    же дома оттуда уходят — их место на полке дома и на общей вкладке «работа»,
    где по-прежнему видно всё. Сито ОДНО и стоит здесь, до всякой раскладки:
    группировка по шагам получает уже просеянные работы и своего сита не заводит.

    *steps* — шаги плана ЭТОЙ нити (работа 44): с ними обе колонки едут группами
    под шагами, без них — плоским списком, как было. У нити без plan.md вкладка
    поэтому выглядит ровно как раньше: пустых заголовков групп не бывает.
    Счётчик колонки при этом не зависит от раскладки — это всегда число работ."""
    today = datetime.now().date()
    open_c, closed_c = [], []
    rows = []
    for hint, f in work_files():
        title, meta, desc, items, journal, plan = _work_read(f)
        own = _work_home(hint, meta)
        if not (_thread_match(meta.get("thread"), name, tdir, own) if tdir
                else _proj_match(own, name)):
            continue
        dl = None
        try:
            dl = datetime.strptime(meta.get("deadline", ""), "%Y-%m-%d").date()
        except ValueError:
            pass
        rows.append((0 if _work_is_fav(meta) else 1, dl or datetime.max.date(),
                     f.parent.name, title, meta, items, dl, journal, desc,
                     plan))
    # избранные — первыми, как избранные нити сверху потока (решение 30.07, фикс
    # 7 работы 21); внутри — прежний порядок по дедлайну и номеру
    rows.sort(key=lambda r: (r[0], r[1], r[2]))
    for _fv, _dt, slug, title, meta, items, dl, journal, desc, plan in rows:
        done = meta.get("status") == "done"
        # журнал компакту нужен ради состояния: свежесть пруф-чека знает только
        # он (см. _work_fresh). ВНУТРИ НИТИ (tdir) блок ещё и разворачивается:
        # план и предложенные шаги видны сразу, без проваливания (решение 07.08).
        # На полке дома (без tdir) блок остаётся свёрнутым лицом-состоянием.
        card = _work_block(slug, title, meta, items, dl, by_dir, today, done,
                           journal, desc=desc, plan=plan,
                           expand=bool(tdir), steps=steps)
        (closed_c if done else open_c).append(
            (slug, card, _step_of(steps, meta), items))
    # закрытые — свежие сверху (как на вкладке работ)
    closed_c.sort(key=lambda r: r[0], reverse=True)
    # группы считаются в каждой колонке СВОИ: открытые отвечают на «чем шаг
    # делается сейчас», закрытые — память, и мешать их прогресс в одну цифру
    # значило бы показывать шаг сделанным ровно тем, что с него уже сняли.
    # Число работ в колонке берётся ДО раскладки — оно про работы, не про куски
    # html, и потому одинаково у плоского списка и у групп
    def col(rows_):
        if steps and steps["items"]:
            body = _step_groups([(s, c, i) for _sl, c, s, i in rows_], steps)
        else:
            body = [r[1] for r in rows_]
        return _WorkCol("".join(body), len(rows_))
    return col(open_c), col(closed_c)


def shelf_detail(name, proot, threads, offers, ho_records=None):
    """ПОЛКА проекта — всё состояние дома одним видом: живые нити · работы ·
    кандидаты (бесхозная работа без сессии) · заметки · дежурки · закрытые ✓."""
    # оффер по нити (решение 17.07): на нити с висящей передачей кнопка ряда должна
    # ПОДНЯТЬ ПРЕЕМНИКА (take), а не ⟳ в предшественника — иначе «принять» кидает
    # назад в старую сессию. Ключ оффера ищем по проекту+нити.
    offer_by_slug = {}
    for r in (ho_records or []):
        if r["proj"] == name and r["status"] == "offered":
            offer_by_slug.setdefault(r["thread"], r)
    # онтология (решение 12.07): живая нить = цепочка оркестрирующих сессий;
    # кандидат = бесхозная работа БЕЗ сессии. Нить без сессий — не отдельный
    # род «заявка», а тот же кандидат (просто с паспортом): её ▶ поднимает
    # оркестрирующую сессию ВНУТРЬ неё, идею-файл ▶ рождает новую нить.
    # живая = есть сессии ИЛИ есть план (решение 12.07: ▶ родила нить с планом
    # закон-47, ждущим подписи — это уже работа, а не бесхозная идея). Без
    # сессий И без плана — заведённая-но-пустая нить = кандидат.
    live = [t for t in threads if t["kind"] != "routine" and (t["sessions"] or t.get("plan"))]
    stubs = [t for t in threads if t["kind"] != "routine" and not t["sessions"] and not t.get("plan")]
    duty = [t for t in threads if t["kind"] == "routine"]
    cands = read_candidates(proot)
    closed = read_closed(name, proot)
    by_dir = {t["dir"]: t for t in threads}
    work_open, work_closed = _project_works(name, by_dir)

    # ЖИВЫЕ НИТИ — цепочки оркестрирующих сессий; сюда же не начатые (пустые
    # планлесс-нити) и дежурки-рутины, чтобы вкладка была одна «нити»
    live_body = [_new_thread_form(name)]
    if live:
        live_body.append(_shelf_sec("живые нити",
            [_shelf_row(t, offers.get((name, t["slug"]), 0),
                        offer_by_slug.get(t["slug"])) for t in live]))
    if stubs:
        live_body.append(_shelf_sec("не начатые", [_stub_row(t, name) for t in stubs]))
    if duty:
        live_body.append(_shelf_sec("дежурки",
            [_shelf_row(t, offers.get((name, t["slug"]), 0)) for t in duty]))

    # РАБОТЫ — горизонтальная лента блоков (решение 17.07): на блоке видно первые
    # 3 пункта — «как работа идёт» с одного взгляда; закрытые снизу под спойлером
    work_body = []
    if work_open.n:
        work_body.append('<div class="wkblkrow">{0}</div>'.format(work_open.html))
    else:
        work_body.append('<p class="shempty">работ у дома пока нет.</p>')
    if work_closed.n:
        work_body.append(
            '<details class="wkclosed"><summary>закрытые · {0}</summary>'
            '<div class="wkblkrow">{1}</div></details>'.format(
                work_closed.n, work_closed.html))

    panes = [
        ("live", "нити", len(live), "".join(live_body)),
        ("works", "работы", work_open.n, "".join(work_body)),
        # ключ вкладки остаётся "ideas" — на нём завязана память активной вкладки
        # (window.__stab) и селекторы; человеку видно только имя (работа 28)
        ("ideas", "кандидаты", len(cands),
         _shelf_sec("кандидаты", [_cand_row(c, name) for c in cands],
                    top=_add_cand_form(name))),
        ("notes", "заметки", len(read_notes(proot)),
         _shelf_sec("заметки", [_note_row(n, name) for n in read_notes(proot)],
                    top=_add_note_form(name))),
        ("closed", "закрыто", len(closed),
         _shelf_sec("закрыто ✓", [_closed_row(c) for c in closed]) if closed
         else '<p class="shempty">закрытых нитей нет.</p>'),
    ]
    bar = "".join(
        '<button class="stab{on}" data-stab="{k}">{lbl} · {n}</button>'.format(
            on=" on" if i == 0 else "", k=k, lbl=lbl, n=n)
        for i, (k, lbl, n, _b) in enumerate(panes))
    body = "".join(
        '<div class="spane" data-spane="{k}"{h}>{b}</div>'.format(
            k=k, h="" if i == 0 else " hidden", b=b)
        for i, (k, _l, _n, b) in enumerate(panes))
    struct = '<div class="stabs">{0}</div>{1}'.format(bar, body)
    return {"name": name, "icon": ICON_PROJECT, "proj": "проект",
            "why": "", "move": "", "struct": struct,
            "now": None, "foldmove": False}


# «в руках» = голова пульсила недавно; остывшие головы падают из фокуса в поток
# (решение 12.07: фокус самоочищается по пульсу). Одна ручка — можно крутить.
HEAD_IN_HAND_SEC = 6 * 3600


_REG_FILE = HOME / "terminals.json"  # реестр запусков: арка→{handle,ts} (общий с serve_live)


def _reg_read():
    try:
        return json.loads(_REG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _orca_live_handles():
    """Множество handle живых терминалов Orca. Тихо → пусто. Зовём ТОЛЬКО когда
    есть слепые кандидаты (не на каждый рендер зря — orca-запрос не бесплатный)."""
    import subprocess
    try:
        r = subprocess.run(["orca", "terminal", "list", "--json"],
                           capture_output=True, text=True, timeout=8,
                           env={"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"})
        data = json.loads(r.stdout or "{}")
        return {t.get("handle") for t in data.get("result", {}).get("terminals", [])
                if t.get("handle")}
    except Exception:
        return set()


def _fresh_key(t, wait_ts):
    """Ключ порядка нитей на столе: свежайшая сверху (правило «новейшее сверху» —
    везде). Считаем по пульсу нити, но ВИСЯЩАЯ передача поднимает её к моменту
    хендоффа (решение 12.07: «сделали хендофф — нить обновилась точно в этот
    момент»). Ключ ОДИН на обе секции стола — избранные и поток: иначе порядок
    расходится, и ждущая нить оказывается под спящими (решение 29.07)."""
    return -max(t["fresh"], wait_ts.get((t["proj"], t["slug"]), 0))


def _proj_panes(roster_all, closed_by=None):
    """{адрес: панель} — тела вкладок «работы», «issues» и «кандидаты» страницы
    нити (работа 20, работа 28). Стол issues и полка кандидатов одинаковы у всех
    нитей дома и лежат ОДНОЙ копией на дом (ключ — имя дома); работы у каждой
    нити свои (работа 24), их ключ —
    «дом|каталог нити». Класть панели в паспорт самой нити нельзя: нитей на
    доске под сотню, и общий стол возился бы десятками копий — страница нити
    берёт своё из этой карты по адресу (window.TP), а в JSON нити лежит только
    он.

    Панель строим КАЖДОЙ нити ростера, включая закрытые (в закрытую с доски
    проваливаются так же) и дома, где живых нитей не осталось: во вкладке живёт
    форма «завести работу» — пустой нити она нужна больше всех. Весь проход
    стоит десятые доли секунды, экономить тут нечего. Рендер — тот же, что на
    полке дома (_project_works) и на столе (_issues_panel): человек должен
    видеть ту же работу теми же карточками. Раскладка своя (.wkblkcol —
    столбик во всю ширину против ленты полки): страницу нити читают с телефона,
    и листать карточки вбок там нечем. И блок тут РАЗВЁРНУТ (решение 07.08):
    внутри нити работ единицы, и они — её содержание, поэтому план и шаги
    читаются с места; на полке дома лента осталась свёрнутыми лицами."""
    panes = {}
    for name, path, threads, _pf in roster_all:
        by_dir = {t["dir"]: t for t in threads}
        ihtml, ni = _issues_panel(threads, proj=name)
        # кандидаты дома — тем же рядом и в том же порядке, что на полке
        # (свежие сверху): человек внутри нити не должен ходить за ними наружу
        cands = read_candidates(path)
        panes[name] = {"issues": ihtml, "ni": ni,
                       "cands": _shelf_sec("кандидаты",
                                           [_cand_row(c, name) for c in cands],
                                           top=_add_cand_form(name)),
                       "nc": len(cands)}
        dirs = ([t["dir"] for t in threads]
                + [c["path"].name for c in (closed_by or {}).get(name, [])])
        for d in dirs:
            # шаги плана нити (работа 44) — рамка, в которой стоят её работы.
            # У живой они уже прочитаны в паспорте; у закрытой паспорта в этом
            # виде нет, и читаем прямо с диска — провал в закрытую нить должен
            # показывать ту же раскладку, а не молча разъезжаться с живой
            t = by_dir.get(d)
            steps = (t.get("steps") if t
                     else read_plan_steps(path / ".tide" / "arcs" / d))
            work_open, work_closed = _project_works(name, by_dir, tdir=d,
                                                    steps=steps)
            # завести работу можно прямо отсюда — на общую вкладку за этим не
            # ходят; заведённая тут сразу приписана этой нити
            body = _work_form(proj=name, thread=_thread_addr(name, d))
            body += ('<div class="wkblkcol">{0}</div>'.format(work_open.html)
                     if work_open.n else
                     '<p class="shempty">живых работ у нити нет.</p>')
            if work_closed.n:
                body += ('<details class="wkclosed"><summary>закрытые · {0}'
                         '</summary><div class="wkblkcol">{1}</div></details>'
                         .format(work_closed.n, work_closed.html))
            # число на ярлыке вкладки — то же поле, из которого нарисован список
            # (см. _WorkCol): счёт и вид не имеют права разойтись
            panes["{0}|{1}".format(name, d)] = {"works": body,
                                                "nw": work_open.n}
    return panes


def build():
    offers, ho_records = read_offers()
    roster_all, projects, closed_by = [], [], {}
    for name, path in roster_projects():
        threads = read_threads(name, path)
        threads.sort(key=lambda t: -t["fresh"])
        closed_by[name] = read_closed(name, path)
        # свежесть ПРОЕКТА считает и закрытие нити (решение 12.07: закрыл дек —
        # он должен всплыть, а не утонуть на «4 дня» по старым открытым нитям)
        pfresh = max([t["fresh"] for t in threads]
                     + [c["fresh"] for c in closed_by[name]] + [0])
        roster_all.append((name, path, threads, pfresh))
        if not threads:
            continue
        projects.append({"name": name, "threads": threads, "fresh": threads[0]["fresh"]})
    projects.sort(key=lambda p: -p["fresh"])  # самый живой проект сверху
    # вкладки страницы нити: issues — копией на дом, работы — своей у каждой
    # нити (см. _proj_panes); счётчик ярлыка берём отсюда же, чтобы число на
    # вкладке и её содержимое считались одним проходом и не разъезжались
    tp = _proj_panes(roster_all, closed_by)
    cnt = lambda p, d: (tp.get("{0}|{1}".format(p, d), {}).get("nw", 0),
                        tp.get(p, {}).get("ni", 0),
                        tp.get(p, {}).get("nc", 0))
    # что ждёт руки, по нитям — единственный источник оранжевого (шаг 3 работы
    # 25). Собираем ОДИН раз на сборку: работ и артефактов десятки, а карточек
    # нитей под сотню, и читать те же файлы на каждую значило бы возить диск зря
    waits = read_desk_waits()
    wof = lambda t: waits.get((t.get("proj") or "", _tdir(t)), 0)

    # «ИЗБРАННЫЕ» (решение 16.07): единственное, что висит над потоком, — руками
    # закреплённый список из контрол-хоума (state/favorites). ЧТО в секции —
    # выбор руками, а ПОРЯДОК внутри — по свежести, как во всём потоке (решение владельца
    # 29.07: «ждущий сейчас должен быть наверху» — сверху лежали спящие, а paint
    # и edinyy-sloy со «ждёт твоё решение» падали вниз, потому что порядок был
    # файловый, т.е. порядок нажатия звёздочки). Ключ общий — _fresh_key.
    # Тем же решением сняты фокус-бэнд по пульсу и секция «отложенные» с её
    # фичей hold: живое само всплывает в потоке свежестью, отложка умерла —
    # held: в паспортах больше никто не читает. Хоткеи 1..7 открывают избранные
    # в том же порядке, что видит глаз (1 — самая верхняя).
    focus_ids, favored = [], set()
    cards, T = [], {}
    now_ts = datetime.now().timestamp()
    by_key = {(p["name"], t["dir"]): t for p in projects for t in p["threads"]}
    # висящие офферы датируют нить моментом хендоффа — нужно ДО обеих сортировок
    wait_ts = {}
    for r in ho_records:
        if r["status"] == "offered" and r["created"]:
            try:
                ts = datetime.fromisoformat(r["created"]).timestamp()
            except ValueError:
                continue
            k = (r["proj"], r["thread"])
            wait_ts[k] = max(wait_ts.get(k, 0), ts)
    frow, fav_projs = [], []
    # закрылась/переехала — строка файла молча не рендерится
    favs = [(proj, by_key[(proj, tdir)]) for proj, tdir in read_favorites()
            if (proj, tdir) in by_key]
    favs.sort(key=lambda pt: _fresh_key(pt[1], wait_ts))
    for proj, t in favs:
        wait = offers.get((proj, t["slug"]), 0)
        hos = [r for r in ho_records if r["proj"] == proj and r["thread"] == t["slug"]]
        frow.append(_card(t, wait, show_proj=True, offer=_offer_of(hos),
                          waits=waits.get((proj, _tdir(t)), 0)))
        T[t["id"]] = detail(proj, t, wait, handoffs=hos,
                            counts=cnt(proj, _tdir(t)))
        focus_ids.append(t["id"])
        favored.add((proj, t["dir"]))
        fav_projs.append(proj)
    if frow:
        cards.append('<div class="slabel">избранные · {0}</div><div class="pjgrid">{1}</div>'.format(
            len(frow), "".join(frow)))

    # поток стола ПЛОСКИЙ по свежести (решение 12.07: «не сгруппированно по
    # проектам — по свежести»); проект подписан на самой карте. Что раньше
    # звалось «в фокусе», теперь просто свежее и потому сверху потока.
    # wait_ts считается выше — тем же ключом сортируются избранные
    stream = [t for p in projects for t in p["threads"]
              if (p["name"], t["dir"]) not in favored]
    # СЕРДЦЕ ОСИ (решение 12.07): голова ПЕРЕЖИВАЕТ нить — закрытая нить с ещё
    # живым чатом не хоронится, а всплывает в потоке с ✓, пока транскрипт дышит
    # (пульс = mtime транскрипта claude-чата, не arc.md)
    for name, _path, _threads, _pf in roster_all:
        for c in closed_by.get(name, []):
            hs = _head_session(c)
            if hs and _session_pulse(hs) >= now_ts - HEAD_IN_HAND_SEC:
                c["proj"] = c.get("proj") or name
                c["fresh"] = max(c["fresh"], _session_pulse(hs))
                stream.append(c)
    stream.sort(key=lambda t: _fresh_key(t, wait_ts))
    if stream:
        cards.append('<div class="slabel">нити · {0}</div><div class="pjgrid">'.format(len(stream)))
        for t in stream:
            wait = offers.get((t["proj"], t["slug"]), 0)
            hos = [r for r in ho_records if r["proj"] == t["proj"] and r["thread"] == t["slug"]]
            was_closed = bool(t.get("closed"))
            cards.append(_card(t, wait, show_proj=True, badge="✓" if was_closed else "",
                               note="чат жив · нить закрыта" if was_closed else "",
                               offer=_offer_of(hos), waits=wof(t)))
            T[t["id"]] = detail(t["proj"], t, wait, handoffs=hos,
                                counts=cnt(t["proj"], _tdir(t)))
        cards.append("</div>")

    # ГЛОБАЛЬНЫЙ фильтр стола по проекту (решение 14.07: «пусть фильтры будут не в
    # нитях, а вообще по всем — и в фокусе, и по отложенным, и по нитям, сверху»):
    # один ряд табов НАД всеми секциями; фильтрует каждую карточку стола
    # (PTAB_JS по data-proj), опустевшая секция прячется целиком со своей подписью.
    # Порядок табов = порядок появления проекта на столе (избранные → поток).
    # фильтр по проекту переехал в ШАПКУ селектом (решение 17.07: ряд чипов
    # съедал три строки) — «все» первой опцией, слева от шестерёнки. Строим
    # опции здесь (знаем счётчики), инжектим в топбар в main().
    desk_projs = fav_projs + [t["proj"] for t in stream]
    pfilter_html = ""
    if desk_projs:
        counts, order = {}, []
        for p in desk_projs:
            if p not in counts:
                order.append(p)
            counts[p] = counts.get(p, 0) + 1
        opts = ['<option value="*">все · {0}</option>'.format(len(desk_projs))]
        opts += ['<option value="{0}">{0} · {1}</option>'.format(esc(p), counts[p])
                 for p in order]
        pfilter_html = ('<select id="pfilter" class="pfilter" '
                        'title="фильтр стола по проекту">{0}</select>'.format(
                            "".join(opts)))

    # ФИЛЬТР СНА — второй селект той же породы, рядом с проектным (решение 01.09:
    # «спящие скрыть, показывать фильтром; фильтр — в том же языке, что „все ·
    # 43“»). Отдельным селектом, а не опциями в проектном: это вторая ось, и
    # смешать их в одном списке значило бы заставить человека выбирать между
    # «показать один дом» и «показать спящие».
    # Считаем по тем же картам, что нарисовали: предикат один (_sleeping), и
    # число в опции обязано сойтись с тем, что скроется по выбору.
    n_sleep = sum(1 for proj, t in favs
                  if _sleeping(t, offers.get((proj, t["slug"]), 0),
                               waits.get((proj, _tdir(t)), 0)))
    n_sleep += sum(1 for t in stream
                   if _sleeping(t, offers.get((t["proj"], t["slug"]), 0), wof(t)))
    n_all = len(favs) + len(stream)
    if n_sleep:
        pfilter_html += (
            '<select id="sfilter" class="pfilter" '
            'title="спящие — те, что молчат больше {d} дней и ничего не ждут">'
            '<option value="live">живые · {live}</option>'
            '<option value="*">со спящими · {all}</option>'
            '<option value="sleep">спящие · {sleep}</option>'
            '</select>'.format(d=_sleep_days(), live=n_all - n_sleep,
                               all=n_all, sleep=n_sleep))

    # уровень проектов — ВСЕ дома (и тихие тоже), живые по свежести сверху;
    # заход в дом = полка (та же механика провала). Живёт своим ТАБОМ
    # «проекты» (решение 12.07), не хвостом стола
    pwait = {}
    for (pn, _slug), n in offers.items():
        pwait[pn] = pwait.get(pn, 0) + n
    prow = []
    for name, path, threads, pfresh in sorted(roster_all,
                                              key=lambda x: (-x[3], x[0])):
        pid = "proj" + re.sub(r"[^a-z0-9]", "", name.lower())
        prow.append(_proj_card(pid, name, threads, pwait.get(name, 0), pfresh))
        T[pid] = shelf_detail(name, path, threads, offers, ho_records)
        # закрытые нити тоже проваливаются в полный контекст (решение 12.07):
        # регистрируем их детальные панели, чтобы клик по трофею открыл нить;
        # если голова закрытой нити уже в фокусе — её панель (с хендоффами) не топчем
        for c in closed_by.get(name, []):
            if c["id"] not in T:
                T[c["id"]] = detail(name, c, 0, counts=cnt(name, _tdir(c)))
    proj_html = '<div class="slabel">проекты · {0}</div><div class="pjgrid">{1}</div>'.format(
        len(prow), "".join(prow))
    return "".join(cards), proj_html, T, focus_ids, pfilter_html, tp


def _mark_resume(t, s):
    """УЗЕЛ-действие (правка владельца 09.07): кружок сессии на линии кликается
    и возвращает в неё (⟳ resume) — отдельная кнопка не нужна."""
    if not s.get("claude"):
        return '<span class="mc"><span class="m"></span></span>'
    proot = t["path"].parents[2]
    # ссылка НЕ оборачивает кружок, а САМА кружок (class="m") — иначе лишний
    # флекс-элемент в .mc плющит маркер (поймано владельцем 09.07)
    return ('<span class="mc"><a class="m mact" href="#" '
            'title="⟳ вернуться в сессию" onclick="{0}"></a></span>'.format(
                _resume_action(t, s)))


def _mark_take(rec):
    """Узел передачи ⇄ кликается и ЗАПУСКАЕТ сессию (движок /take)."""
    if not (rec.get("key") and not rec.get("stale_kb")):
        return '<span class="mc"><span class="tlm">⇄</span></span>'
    return ('<span class="mc"><a class="tlm mact" href="#" data-u="/take?key={0}" '
            'title="▶ запустить" style="color:inherit;text-decoration:none" '
            'onclick="event.preventDefault();'
            "this.style.opacity='.35';fetch(this.dataset.u)\">⇄</a></span>".format(
                esc(rec["key"])))


def _take_btn(rec):
    """Кнопка-пилюля висящего оффера: ▶ /take с гейтом свежести сида."""
    if rec.get("stale_kb"):
        return ('<span style="display:inline-block;padding:3px 16px;'
                'border:1px solid var(--line-2);border-radius:999px;'
                'color:var(--ink-mute)">сид устарел · источник ушёл на {0} КБ · '
                'нужен свежий хендофф</span>'.format(rec["stale_kb"]))
    if rec.get("key"):
        # единый компонент кнопки .abtn (primary — амбер): ту же форму носит
        # ⟳ вернуться в сессию, чтобы действия читались как один набор.
        # Зарезервированный оффер (▶ уже нажат, ждём первый ход сессии) — кнопка
        # честно говорит «поднимается»; клик безопасен: /take идемпотентен
        # (tide pickup фокусит живую поднятую вкладку, дубля не чеканит).
        # ПРОТУХШИЙ резерв (первый ход так и не пришёл за TTL станка, cand 116
        # п.4) — снова «▶ запустить», не вечное «поднимается»: свежий take
        # просто перезапишет резерв.
        reserved = ((rec.get("pickup") or "").strip() not in ("", "-")
                    and not _reserve_stale(rec))
        label = "⟳ поднимается — открыть" if reserved else "▶ запустить"
        cls = "abtn" if reserved else "abtn primary"
        return ('<a class="{2}" href="#" data-u="/take?key={0}" '
                "onclick=\"event.preventDefault();var el=this;el.textContent='открываю…';"
                "fetch(el.dataset.u).then(function(r){{return r.text()}})"
                ".then(function(x){{el.textContent=x.slice(0,90);"
                "el.classList.remove('primary')}})\">"
                '{1}</a>'.format(esc(rec["key"]), label, cls))
    return ""


_RESERVE_TTL_SEC = 2 * 3600  # зеркало станочного RESERVE_TTL_HOURS (cand 116 п.4)


def _reserve_stale(rec):
    """Резерв без первого хода старше TTL — оффер снова читается берущимся."""
    raw = (rec.get("reserved_at") or "").strip()
    if not raw or raw == "-":
        return True  # легаси-резерв без штампа — не держим кнопку вечно
    try:
        ts = datetime.fromisoformat(raw).timestamp()
    except ValueError:
        return True
    return (datetime.now().timestamp() - ts) > _RESERVE_TTL_SEC


def _ho_row(rec):
    """Шов ⇄ на таймлайне: КОГДА передача случилась/висит (закон: таймлайн).
    Висящую можно ▶ запустить прямо с доски (движок /take, pull-модель)."""
    when = (rec["taken_at"] if rec["status"] == "taken" else rec["created"]) or ""
    when = when.replace("T", " ")[:16]
    tail = (" → {0}".format(esc(_sess_name(rec.get("thread", ""), rec["session"])))
            if rec["session"] else "")
    if rec["status"] == "offered":
        # висящая передача = ГЛАВНОЕ действие доски: шапкой — ИМЯ следующей
        # сессии (02-debug-deck), не «передача ждёт · дата»; под ним одна
        # кнопка ▶ запустить, без «continue → …» (имя уже наверху)
        btn = _take_btn(rec)
        nm = (_sess_name(rec.get("thread", ""), rec["session"])
              if rec["session"] else "следующая сессия")
        # под именем — МЕРЫ СИДА по контракту семи блоков (решение 05): шов
        # валидируется глазами, поэтому состав видно до приёма, а не после
        return ('<div class="step tl ho"><span class="sg"></span>'
                + _mark_take(rec) +
                '<div class="body"><span class="gt" style="color:var(--c2)">⌛ {0}</span>'
                '{2}<span class="gs" style="margin-top:6px">{1}</span>'
                '</div></div>'.format(esc(nm), btn, _seed_line(rec)))
    head = ('<div style="display:grid;grid-template-columns:150px 1fr auto;'
            'gap:12px;align-items:baseline;flex:1;min-width:0">'
            '<span style="color:var(--ink-faint);font-family:var(--mono);font-size:11px;'
            'white-space:nowrap">⇄ шов</span>'
            '<span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;'
            'color:var(--ink-faint)">передача принята · {0}</span><span></span>'
            '</div>'.format(esc(when)))
    return ('<div class="step tl ho"><span class="sg"></span>'
            '<span class="mc"><span class="tlm">⇄</span></span>'
            '<div class="body"><details class="tld"><summary>{0}</summary>'
            '<div class="tldd">{1}{2}</div></details></div></div>'.format(
                head, esc(rec["mode"] or "continue"), tail))


# ── события работ на таймлайне (решение 01.08, работа 40) ─────────────────────
# «Кучу времени делаем работу, а таймлайн тухнет, ничего нового не появляется».
# И правда: лента копила только выгрузки головы, а всё, что случилось С РАБОТАМИ
# нити — за два дня одиннадцать закрытий, — на ней не появлялось вовсе. Данные
# лежали рядом: у каждой работы поле thread и журнал со временем.
_WEV = (
    (re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}) — (?:рождена|заведена)"),
     "born", "заведена"),
    (re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}) — взята в работу"),
     "taken", "взята"),
    (re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}) — все пункты чекнуты → review"),
     "review", "сдана на проверку"),
    (re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}) — закрыта"),
     "closed", "закрыта"),
)
# что видно всегда, а что уезжает в свёртку: ЗАКРЫТИЕ — веха (ими человек и
# меряет день: «за два дня одиннадцать закрытий»), остальное — сопровождение.
# Держать в ленте ещё и сдачи со взятиями значило бы утопить рассказ головы:
# у одной нити их за те же два дня набралось под сотню
_WEV_LOUD = ("closed",)
_WEV_INLINE_MAX = 4


def _thread_work_events(proj, tdir):
    """События работ ЭТОЙ нити → [(когда, вид, подпись)], старое первым.

    Читаем те же work.md, что и вкладка «работы», и тем же ситом по нити
    (_thread_match) — доска не должна знать двух разных ответов на вопрос
    «чьи это работы». Рождения отдельной строкой у старых работ нет: их первый
    след — первая строка журнала, её и берём, но только если явной строки
    рождения не нашлось."""
    out = []
    for hint, f in work_files():
        try:
            title, meta, _d, _items, journal, _p = _work_read(f)
        except OSError:
            continue
        if not _thread_match(meta.get("thread"), proj, tdir,
                             _work_home(hint, meta)):
            continue
        num = _work_num(f.parent.name)
        name = "работа {0} · {1}".format(num, title) if num else title
        seen_born = False
        for j in journal:
            for rx, kind, word in _WEV:
                m = rx.match(j)
                if not m:
                    continue
                try:
                    when = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M")
                except ValueError:
                    break
                if kind == "born":
                    seen_born = True
                out.append((when, kind, "{0} — {1}".format(name, word)))
                break
        if not seen_born and journal:
            m = _WORK_JRN_TS.match(journal[0])
            if m:
                try:
                    out.append((datetime.strptime(m.group(1), "%Y-%m-%d %H:%M"),
                                "born", "{0} — заведена".format(name)))
                except ValueError:
                    pass
    out.sort(key=lambda e: e[0])
    return out


def _wev_row(ev):
    """Событие работы — подшагом таймлайна: время и одна строка. Язык тот же,
    что у выгрузок головы (.sub с квадратиком-маркером), но маркер свой и тон
    тише: глаз должен отличать «агент рассказал» от «с работой случилось»."""
    when, kind, text = ev
    return ('<div class="sub wev {k}"><span class="sm"></span>'
            '<span class="wevt">{w}</span><span>{x}</span></div>'.format(
                k=kind, w=when.strftime("%H:%M"), x=esc(text)))


def _mix_steps(context, events):
    """Подшаги узла сессии: выгрузки головы и события её работ ОДНОЙ лентой по
    времени, новейшее сверху (закон дома).

    Плотность: одиннадцать закрытий за два дня не должны утопить рассказ головы,
    поэтому вехи (сдана/закрыта) стоят в ленте всегда, а сопровождение
    (заведена/взята) уезжает в тихую свёртку, как только событий становится
    больше горстки."""
    rows = []
    for c in context:
        m = re.match(r"(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})", c)
        when = None
        if m:
            try:
                when = datetime.strptime(
                    m.group(1) + " " + m.group(2), "%Y-%m-%d %H:%M")
            except ValueError:
                when = None
        rows.append((when, _ctx_item(c)))
    quiet = []
    if len(events) > _WEV_INLINE_MAX:
        loud = [e for e in events if e[1] in _WEV_LOUD]
        quiet = [e for e in events if e[1] not in _WEV_LOUD]
        events = loud
    rows += [(e[0], _wev_row(e)) for e in events]
    rows.sort(key=lambda r: (r[0] is not None, r[0] or datetime.min),
              reverse=True)
    html = "".join(h for _w, h in rows)
    if quiet:
        html += ('<details class="sub wevmore"><summary><span class="sm"></span>'
                 '<span>ещё {0} · заведены, взяты, сданы</span></summary>'
                 '<div class="wevd">{1}</div></details>'.format(
                     len(quiet), "".join(_wev_row(e) for e in reversed(quiet))))
    return html


def _session_node(t, s, now=False, suppress_move=False, passed_to="",
                  events=()):
    """Богатый узел сессии на дороге: кружок-возврат + имя NN-нить + курсор +
    записи списком + ⟳ вернуться. ОДИН дизайн и для «сейчас», и для прошлых —
    прошлые НЕ сворачиваются в «путь досюда» (правка владельца 13.07: свёрнутая
    компактная строка читалась как поломанный дизайн), просто уходят вниз.
    ОТПУЩЕННАЯ голова (dismissed) — мёртвый след ОБОРВАННОЙ ветки: тускло,
    серый кружок, «отпущена ✕», без богатого тела — чтобы не читалась как
    живой шаг, «от которого пришли» (решение 13.07: 01 её породил, а она сдохла).
    *passed_to* — имя текущей сессии, если ЭТА уже передала нить дальше
    (выводится из цепочки, решение 16.07): тускло + пилюля простого возврата."""
    if s.get("dismissed"):
        # круглый тусклый маркер (не квадрат, как у живых), без иконки-кнопки —
        # мёртвой ветке возврат не нужен; только имя + «отпущена»
        return ('<div class="step done"><span class="sg"></span>'
                '<span class="mc"><span style="width:8px;height:8px;border-radius:50%;'
                'background:var(--ink-mute);margin-top:5px;flex:none"></span></span>'
                '<div class="body" style="display:flex;align-items:center;gap:10px;'
                'flex-wrap:wrap">'
                '<span class="gt" style="color:var(--ink-mute)">{0}</span>'
                '<span style="font-size:9px;letter-spacing:.14em;text-transform:uppercase;'
                'color:var(--ink-faint)">отпущена · ветка оборвалась</span>'
                '</div></div>'.format(esc(_sess_name(t["slug"], s["dir"]))))
    if passed_to:
        # передала нить дальше — история открыта: тускло, с ЯВНОЙ пилюлей
        # возврата без допросов (решение 16.07: «к старым тоже должна быть
        # возможность вернуться»); слово «растворилась» из интерфейса ушло.
        # ПРОШЛОЕ РАСКРЫВАЕТСЯ (решение 01.08, фикс 2 работы 40: «по старым
        # сессиям можно было бы видеть, что они делали — а мы это прячем»).
        # Раньше узел был одной строкой и вся её история умирала молча. Теперь
        # то же тело, что у живой (выгрузки головы + события работ её времени),
        # живёт под тапом: свёрнуто по умолчанию — прошлое не должно топить
        # «сейчас», это закон таймлайна и он не меняется. Заголовок и кнопка
        # возврата остались ровно теми же.
        past = _mix_steps(s["context"], events)
        head = ('<div style="display:flex;align-items:center;gap:10px;'
                'flex-wrap:wrap">'
                '<span class="gt" style="color:var(--ink-mute)">{0}</span>'
                '<span style="font-size:9px;letter-spacing:.14em;'
                'text-transform:uppercase;color:var(--ink-faint)">'
                'нить ушла дальше ⇄ {1}</span></div>'.format(
                    esc(_sess_name(t["slug"], s["dir"])), esc(passed_to)))
        body = (('<details class="pastwork"><summary>{h}</summary>'
                 '<div class="substeps">{s}</div></details>'.format(
                     h=head, s=past)) if past else head)
        return ('<div class="step done"><span class="sg"></span>'
                + _mark_resume(t, s) +
                '<div class="body">{0}{1}</div></div>'.format(
                    body, _sess_acts(t, s)))
    subs = _mix_steps(s["context"], events)
    # нет курсора (сессия ещё не пульсовала) → строка говорит ЦЕЛЬ из паспорта,
    # а не молчит пустотой (cand 116 п.1: goal-строка на борде была пуста)
    cur_txt = _human(s["cursor"]) or _human(s.get("title") or "")
    cur = '<span class="gs">{0}</span>'.format(esc(cur_txt)) if cur_txt else ""
    mv = "" if suppress_move else _move_line(t, s)
    body = ('<div class="body"><span class="gt">{t}</span>{mv}{c}{sess}{subs}{a}</div>'.format(
        t=esc(_sess_name(t["slug"], s["dir"])), mv=mv, c=cur,
        sess=_session_now_row(t, s),
        subs='<div class="substeps">{0}</div>'.format(subs) if subs else "",
        a=_sess_acts(t, s)))
    lab = '<span class="sn">сейчас</span>' if now else ""
    return '<div class="step now rich"><span class="sg">{0}</span>{1}{2}</div>'.format(
        lab, _mark_resume(t, s), body)


def _road(t, wait, handoffs=None):
    """Нить как ДОРОГА (полоска из шаблона): сессии = шаги во времени.

    Закрытые — пройденные (старые сворачиваются), живая — курсор с
    context-подшагами, ПЕРЕДАЧИ — швы ⇄ с временем (таймлайн хендоффов),
    цель нити — в конце. Маркап дизайна scope, данные — паспорта.
    """
    # сессия, ждущая подхвата, представлена своей ⌛-строкой — её НЕ дублируем
    # узлом (иначе пустой пикап-стаб висит дважды). Оффер несёт ГОЛЫЙ слаг
    # ('build'), сессия — дир-имя ('01-build'): сравниваем нормализованно, плюс
    # ловим reserved-сессию по запиненному sid (cand 116 п.2: payouts показывал
    # одну работу тремя строками — дедуп по сырым именам молча промахивался)
    def _bare(x):
        return re.sub(r"^_*\d+-?", "", x or "").strip("-_")
    offers_open = [rec for rec in (handoffs or []) if rec["status"] == "offered"]
    offered = ({_bare(rec["session"]) for rec in offers_open if rec["session"]}
               | {rec["session"] for rec in offers_open if rec["session"]})
    offered_sids = {rec["pickup"] for rec in offers_open if rec.get("pickup")}
    _repr_by_offer = lambda s: (_bare(s["dir"]) in offered or s["name"] in offered
                                or (s.get("claude") or "-") in offered_sids)
    # порядок дороги = ЛИНЕАЖ ХЕНДОФФОВ (по номеру сессии, новейшая сверху),
    # НЕ по пульсу/времени: 02 родилась ПОСЛЕ 01 и должна стоять выше, даже
    # если сдохла, — иначе снизу читается как предок, «откуда пришли»
    seq = [s for s in t["sessions"] if not _repr_by_offer(s)]  # новейший № сверху
    # «сейчас» = ТЕКУЩАЯ сессия нити (решение 16.07: «текущая — последняя, в
    # которую хэндофнуто»): новейшая открытая ПРИНЯТАЯ (sid запинен, чат не
    # ended) — выводится из цепочки, не штампуется. Пульс решает только
    # фолбэк, когда принятых нет. Метка не меняет позицию (линеаж).
    live = [s for s in seq
            if not s["closed"] and not s.get("dismissed")]
    taken = [s for s in live if s.get("claude") and not s.get("ended")]
    cursor_live = (taken[0] if taken
                   else (max(live, key=_session_pulse) if live else None))
    waiting_rows = [_ho_row(rec) for rec in (handoffs or [])
                    if rec["status"] == "offered"]

    rows = ['<div class="road">']  # «нить · один поток» — визуальный шум, убран
    # узел цели — только если у нити есть РЕАЛЬНАЯ цель; авто-имя (goal==слаг)
    # или пустышку не рисуем вовсе, чтобы не плодить бесполезную строку-цель
    goal_txt = _real_goal(t)
    if goal_txt:
        rows.append('<div class="step goal rich"><span class="sg"></span>'
                    '<span class="mc"><span class="m"></span></span>'
                    '<div class="body"><span class="gt">цель</span>'
                    '<span class="gs">{0}</span></div></div>'.format(esc(goal_txt)))
    rows += waiting_rows
    # все сессии В ПОРЯДКЕ ЛИНЕАЖА (новейшая сверху). Открытая НЕ-текущая =
    # передала нить дальше (выводится из цепочки, штамп dissolved больше не
    # нужен) — тускнеет с подписью «нить ушла дальше». «ход · твой» гасим у
    # всех, кроме курсора, и если висит оффер
    tip_name = _sess_name(t["slug"], cursor_live["dir"]) if cursor_live else ""
    # СОБЫТИЯ РАБОТ — ПО СЕССИЯМ ИХ ВРЕМЕНИ (работа 40). Окно сессии выводим из
    # рождений: seq идёт новейшей первой, значит сессия живёт от своего рождения
    # до рождения следующей, а самая новая — до сих пор. Событие старше самой
    # старой сессии не выбрасываем — отдаём ей: потерять его хуже, чем показать
    # чуть выше по ленте, а другого владельца у него всё равно нет.
    wev = _thread_work_events(t.get("proj", ""), _tdir(t))
    births = [s.get("birthtime") or 0 for s in seq]
    by_sess = [[] for _ in seq]
    for ev in wev:
        ts = ev[0].timestamp()
        idx = len(seq) - 1
        for i, b in enumerate(births):
            if ts >= b:
                idx = i
                break
        if by_sess:
            by_sess[idx].append(ev)
    for i, s in enumerate(seq):
        is_now = s is cursor_live
        passed = (not is_now and not s["closed"] and not s.get("dismissed")
                  and (s.get("dissolved") or cursor_live is not None))
        rows.append(_session_node(t, s, now=is_now,
                                  suppress_move=(not is_now) or bool(waiting_rows),
                                  passed_to=tip_name if passed else "",
                                  events=by_sess[i] if by_sess else []))
    rows.append("</div>")
    return "".join(rows)


def _human_note(c):
    """context-строка без ISO-шума: «2026-07-07T21:12 — текст» → «21:12 текст»."""
    m = re.match(r"\d{4}-\d{2}-\d{2}T(\d{2}:\d{2})(?::\d{2})?\s*—?\s*(.*)", c)
    return "{0} {1}".format(m.group(1), m.group(2)) if m else c


def _note_body_html(c):
    """Длинную запись контекста режем на читаемые строки — по нумерации «(N)»
    и по «; »: стена текста → список. ISO-префикс снимаем. Одна строка —
    отдаём как есть."""
    m = re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?\s*—?\s*(.*)", c, re.S)
    body = (m.group(1) if m else c).strip()
    marked = re.sub(r"\s*\((\d+)\)\s*", r"\n(\1) ", body).strip()
    lines = [seg.strip(" ;") for chunk in marked.split("\n")
             for seg in re.split(r";\s+", chunk) if seg.strip(" ;")]
    if len(lines) <= 1:
        return esc(body)
    return "".join('<div style="padding:2px 0">{0}</div>'.format(esc(ln))
                   for ln in lines)


def _note_clauses(c):
    """context-строку (без ISO-префикса) → список пунктов: режем по нумерации
    «(N)» и по «; »."""
    m = re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?\s*—?\s*(.*)", c, re.S)
    body = (m.group(1) if m else c).strip()
    marked = re.sub(r"\s*\((\d+)\)\s*", r"\n(\1) ", body).strip()
    return [seg.strip(" ;") for chunk in marked.split("\n")
            for seg in re.split(r";\s+", chunk) if seg.strip(" ;")]


# код-якоря в записи (решение 13.07: «не единый серый текст» — дать глазу за что
# зацепиться): бэктики · **жирное** · тикет-рефы MIT-71 · хеши коммитов ·
# латинские identifier'ы (dotted / camelCase). Один проход с альтернацией —
# вставленная разметка не пере-сканируется (иначе dotted+camel давали вложенные
# <b>). Кириллица-проза не тронута — подсвечиваем только латиницу/цифры.
_EMPH_RE = re.compile(
    r"\*\*(?P<bold>.+?)\*\*"
    r"|`(?P<code>[^`]+)`"
    r"|(?P<tok>"
    r"\b[A-Z]{2,}-\d+\b"                               # тикет-реф MIT-71
    r"|\b(?=[0-9a-f]*[0-9])[0-9a-f]{7,40}\b"           # хеш коммита (с цифрой)
    r"|\b[A-Za-z][A-Za-z0-9]*(?:\.[A-Za-z0-9]+)+\b"    # dotted ident
    r"|\b[a-z]+[A-Z][A-Za-z0-9]*\b"                    # camelCase
    r")")


def _emph(text):
    """Экранировать + подсветить код-якоря классом .k (яркий --ink)."""
    def repl(m):
        if m.group("bold") is not None:
            return '<b class="k">{0}</b>'.format(m.group("bold"))
        if m.group("code") is not None:
            return '<code class="k">{0}</code>'.format(m.group("code"))
        return '<b class="k">{0}</b>'.format(m.group("tok"))
    return _EMPH_RE.sub(repl, esc(text))


def _ctx_sentences(text):
    """Клаузу → предложения: режем по «. »/«; » перед заглавной/«(»/««» — даёт
    ритм вместо стены. Десятичные/URL/«min(6ч» не рвём (нужен пробел+заглавная)."""
    parts = re.split(r"(?<=[.;])\s+(?=[А-ЯA-Z(«])", text)
    return [p.strip() for p in parts if p.strip()]


def _clause_li(cl):
    """Пункт спойлера (решение 13.07). Нумерованный «(N) …» — амбер-индексом в
    колонке (аккуратный список). БЕЗ номера (клауза от «; »-реза) — просто
    строкой текста вплотную к рельсу, БЕЗ буллета-точки: точки читались как мусор,
    а колонка отбрасывала текст далеко вправо от линии."""
    m = re.match(r"^\(?(\d+)[).]\s+(.*)$", cl)
    if m:
        return ('<div class="cxi"><span class="cxn">{0}</span>'
                '<span class="cxt">{1}</span></div>'.format(
                    esc(m.group(1)), _emph(m.group(2))))
    return '<div class="cxp">{0}</div>'.format(_emph(cl))


def _ctx_item(c):
    """Запись контекста — КАРТОЧКОЙ (решение 13.07): время тусклым mono-ЗАГОЛОВКОМ
    сверху (если есть); тело — предложениями-строками (ритм, не стена), с
    подсветкой код-якорей (.k, контраст против серой прозы); записи разделены
    тонкой линией. Многопунктовую (лид-ин + (1)(2)(3)…) — спойлером с бейджем «+N»;
    пункты — аккуратным номером-индексом, не «(N)» в скобках."""
    m = re.match(r"\d{4}-\d{2}-\d{2}T(\d{2}:\d{2})", c)
    hdr = '<div class="ct">{0}</div>'.format(esc(m.group(1))) if m else ""
    clauses = _note_clauses(c)

    def lines(clause):
        return "".join("<div>{0}</div>".format(_emph(s))
                       for s in _ctx_sentences(clause))
    if len(clauses) <= 2:  # короткая — целиком, раскрывать нечего
        txt = "".join(lines(cl) for cl in clauses)
        return '<div class="ctx">{0}<div class="cx">{1}</div></div>'.format(hdr, txt)
    lead = '{0}<span class="cxmore">+{1}</span>'.format(
        _emph(clauses[0]), len(clauses) - 1)
    rest = "".join(_clause_li(cl) for cl in clauses[1:])
    return ('<details class="ctx"><summary>{0}<div class="cx">{1}</div></summary>'
            '<div class="cxd">{2}</div></details>'.format(hdr, lead, rest))


def _artifacts(t):
    """Артефакты живой сессии — копнуть и ОТКРЫТЬ по клику (pull: клик = рука
    человека, движок открывает файл дефолтным приложением через /open)."""
    import urllib.parse as _up
    live_dirs = [t["path"] / "arcs" / s["dir"] / "workspace"
                 for s in t["sessions"] if not s["closed"]]
    files = []
    for d in live_dirs:
        if d.is_dir():
            files += sorted((p for p in d.rglob("*") if p.is_file()),
                            key=lambda p: -p.stat().st_mtime)[:8]
    if not files:
        return ""
    rows = "".join(
        "<div style='padding:2px 0'><a href='#' data-u='/open?f={u}' "
        "onclick='fetch(this.dataset.u);return false' "
        "style='color:var(--ink);text-decoration:none;border-bottom:1px dotted var(--ink-mute)'>{n}</a></div>".format(
            u=_up.quote(str(p)), n=esc(p.name)) for p in files[:8])
    return ('<details class="tld" style="margin-top:10px"><summary>артефакты · {0} файлов '
            'в workspace</summary><div class="tldd">{1}</div></details>'.format(len(files), rows))


def _resume_link(t, s):
    """⟳ возврат в ЛЮБУЮ сессию с claude-айди, закрытую тоже (правка владельца
    08.07). dir = КОРЕНЬ проекта — claude --resume ищет сессию по нему."""
    if not s.get("claude"):
        return ""
    proot = t["path"].parents[2]
    return (' <a href="#" onclick="{0}" style="color:var(--c1);'
            'text-decoration:none">⟳ вернуться</a>'.format(
                _resume_action(t, s)))


def _session_now_row(t, s):
    """Тихая подпись живой сессии у курсора: «сессия · роль · метка». ЧИСТЫЙ
    ТЕКСТ, никаких действий (решение 12.07: коробки у текста — кривь). Возврат
    (⟳) — на кружке-узле шага (правило 09.07); отпустить (✕) — пилюлей в
    подвале карточки сессии, рядом с ⟳ (единственный дом действий)."""
    # имя сессии теперь в ЗАГОЛОВКЕ узла (01-debug-deck) — под-строкой его не
    # дублируем; оставляем только роль (планирование/экзекьюция). Нет роли —
    # строки нет вовсе (пустая «сессия» — шум)
    rw = _role_word(s.get("role"))
    if not rw:
        return ""
    return ('<span class="gs sessrow">сессия · '
            '<span title="{0}">{1}</span></span>'.format(esc(s["role"]), esc(rw)))


def _is_stub(s):
    """Засеянная, но не подхваченная сессия: открыта и без пульса (нет курсора
    и контекста). На доске её представляет её же ⌛-оффер, не пустой шаг."""
    return (not s["closed"]) and not s["cursor"] and not s["context"]


def _live_session(t):
    """Актуальная сессия нити ДЛЯ ВОЗВРАТА (▶ на карточке). Новая механика №1
    (решение 16.07): ТЕКУЩАЯ = хвост цепочки передач — новейшая открытая
    принятая (sid запинен, чат не ended); выводится, не штампуется. Порядок:
    (1) новейшая открытая с живым sid; (2) тёплый чат где угодно — живой чат
    под ЗАКРЫТОЙ аркой бьёт заглушки (решение 14.07, office: иначе ▶ спавнит
    дубль вместо фокуса работающего терминала); (3) новейшая открытая
    непустышка; (4) новейшая открытая (пустышки-сиды не вытесняют живую)."""
    now_ts = datetime.now().timestamp()
    cands = [s for s in t["sessions"] if not s.get("dismissed")]
    # «живой sid» проверяем ДИСКОМ, а не паспортом: сессия-приёмник оффера рождается
    # со штампом sid ДО первого хода, и сорвавшийся подъём (19.07: Orca падала в
    # `login:`) оставляет в паспорте айди чата, которого нет. Такой призрак — новейший
    # в цепочке — забирал возврат у настоящей живой сессии. Штамп остаётся фолбэком:
    # у старых нитей транскрипты подчищены, без него они потеряли бы хвост цепочки.
    open_tips = [s for s in cands
                 if not s["closed"] and s.get("claude") and not s.get("ended")]
    tip = (next((s for s in open_tips if _has_transcript(s)), None)
           or (open_tips[0] if open_tips else None))
    if tip is not None:
        return tip
    warm = [s for s in cands
            if s.get("claude") and not s.get("ended")
            and _session_pulse(s) >= now_ts - HEAD_IN_HAND_SEC]
    if warm:
        return max(warm, key=_session_pulse)
    open_s = [s for s in cands if not s["closed"]]
    return next((s for s in open_s if not _is_stub(s)), open_s[0] if open_s else None)


_PULSE_CACHE = {}  # str(path) -> (file_mtime, last_entry_ts): не тейл-ридить
# один и тот же транскрипт на каждый автообновляемый рендер


def _last_entry_ts(path):
    """Истинный пульс + ЧЕЙ ХОД завершает транскрипт jsonl — (ts, kind). НЕ mtime
    файла (его сбивает массовый touch: sync/copy/git/tick — мёртвая неделю сессия
    притворялась бы живой). Читаем только хвост (файл бывает на мегабайты), берём
    новейшую запись с timestamp. kind: 'agent_done' — последняя запись это финал
    ассистента (текст без pending tool_use → ход отдан человеку) · 'working' —
    tool_use / tool_result / реплика юзера (агент в цикле). Кэш по mtime файла —
    реальный ход двигает mtime, кэш не протухает. (None, None) — хвоста нет."""
    key = str(path)
    try:
        fmtime = path.stat().st_mtime
    except OSError:
        return (None, None)
    cached = _PULSE_CACHE.get(key)
    if cached and cached[0] == fmtime:
        return cached[1]
    result = _tail_scan(path)
    _PULSE_CACHE[key] = (fmtime, result)
    return result


def _tail_scan(path):
    """(ts, kind) новейшей timestamped-записи хвоста. kind см. _last_entry_ts."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 65536))
            tail = f.read().decode("utf-8", "ignore")
    except OSError:
        return (None, None)
    for ln in reversed(tail.splitlines()):
        if '"timestamp"' not in ln:
            continue
        try:
            o = json.loads(ln)
        except ValueError:
            continue
        ts = o.get("timestamp")
        if not ts:
            continue
        try:
            tsf = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return (None, None)
        # финал ассистента = запись типа assistant с контентом БЕЗ tool_use (агент
        # дописал текст и отдал ход). Всё остальное (tool_use в ассистенте, реплика
        # юзера, tool_result) — агент ещё в цикле.
        role = o.get("type") or (o.get("message") or {}).get("role")
        kind = "working"
        if role == "assistant":
            content = (o.get("message") or {}).get("content")
            has_tool = isinstance(content, list) and any(
                isinstance(b, dict) and b.get("type") == "tool_use" for b in content)
            kind = "working" if has_tool else "agent_done"
        return (tsf, kind)
    return (None, None)


def _session_state(s):
    """(ts, kind) сессии по её claude-транскрипту (пульс + чей ход). Нет claude-
    айди / транскрипта / timestamp — фолбэк (mtime паспорта, kind None)."""
    sid = s.get("claude")
    if sid:
        try:
            hits = list(CLAUDE_PROJECTS.glob("*/{0}.jsonl".format(sid)))
        except OSError:
            hits = []
        if hits:
            ts, kind = _last_entry_ts(hits[0])
            if ts:
                return ts, kind
    # no live transcript → honest fallback: last offload, else creation time — NEVER the
    # passport mtime, which a mass touch bumps and made a dead ghost glow "26 мин" (cand 09).
    return (s.get("offloaded") or s.get("birthtime") or s.get("mtime") or 0.0), None


def _session_pulse(s):
    """Честный пульс сессии — время последнего хода её claude-чата (см.
    _session_state). Иммунно к массовому touch. Фолбэк — mtime паспорта."""
    return _session_state(s)[0]


def _worker_pulse(s):
    """Пульс ВОРКЕРОВ сессии *s* — 0.0, если их нет (разведка 31.07, шаг 2
    работы 25).

    Пока сабагент строит, транскрипт РОДИТЕЛЯ молчит: голова ждёт воркера и в
    свой чат ничего не пишет. Доска мерила только голову, ловила тишину и звала
    человека к работе, которая кипит (payouts 31.07: голова тиха 4 минуты, два
    воркера дописывали свои чаты прямо в эту секунду).

    Воркеры видны на диске без всякого реестра: их чаты лежат в
    `<проект>/<sid-родителя>/subagents/*.jsonl` — каталог назван сидом сессии,
    и привязка воркер→сессия→нить уже есть. Содержимое НЕ парсим: голове нужен
    «чей ход» (_last_entry_ts), воркеру — только «дышит ли», а это mtime.

    Дороже одного glob не стоит и зовётся лениво — только когда голова уже не
    свежая (см. _thread_status): пока голова в цикле, нить и так зелёная."""
    sid = s.get("claude")
    if not sid:
        return 0.0
    try:
        dirs = list(CLAUDE_PROJECTS.glob("*/{0}/subagents".format(sid)))
    except OSError:
        return 0.0
    best = 0.0
    for d in dirs:
        try:
            for f in d.glob("*.jsonl"):
                best = max(best, f.stat().st_mtime)
        except OSError:
            continue
    return best


def _has_transcript(s):
    """Есть ли у сессии РЕАЛЬНЫЙ чат на диске. Штамп `claude-session:` в паспорте
    этого не доказывает: подъём оффера чеканит sid и штампует его ДО того, как
    сессия заговорит, — сорвался спавн, и в паспорте остаётся айди чата, которого
    никогда не было."""
    sid = s.get("claude")
    if not sid:
        return False
    try:
        return any(CLAUDE_PROJECTS.glob("*/{0}.jsonl".format(sid)))
    except OSError:
        return False


def _head_session(t):
    """ГОЛОВА нити: живая сессия, привязанная к claude-чату (есть источник
    пульса), не отпущенная рукой (✕ → dismissed). Это то, что band «в фокусе»
    показывает — внимание, а не структуру. «Живость» решает пульс транскрипта
    (см. _session_pulse) в build(), а НЕ содержимое паспорта — поэтому свежая,
    ещё не заполненная сессия тоже голова (родил → в фокусе), а заглушка без
    claude-айди головой не считается. None, если головы нет (нить без живой
    сессии, дежурка, только план, голова отпущена).

    Сперва спрашиваем ДИСК, а не паспорт (канон №1: нить ведёт новейшая принятая
    сессия — выводится, не штампуется). Сессия-приёмник оффера рождается уже со
    штампом sid; если её подъём сорвался (19.07: Orca падала в `login:`), паспорт
    несёт айди чата, которого нет. Такой призрак становился головой и забирал у
    настоящей живой сессии кнопку возврата — на странице нити возвращаться было
    некуда. Фолбэк на штамп сохранён: у старых нитей транскрипты подчищены, и без
    него они разом остались бы без головы."""
    if t["kind"] == "routine":
        return None
    open_s = [s for s in t["sessions"]
              if not s["closed"] and not s.get("dismissed")]
    return (next((s for s in open_s if _has_transcript(s)), None)
            or next((s for s in open_s if s.get("claude")), None))


def _note_parts(c):
    """context-строка → (ЧЧ:ММ, короткий текст) для карточки сессии."""
    m = re.match(r"\d{4}-\d{2}-\d{2}T(\d{2}:\d{2})(?::\d{2})?\s*—?\s*(.*)", c)
    time, text = (m.group(1), m.group(2)) if m else ("", c)
    return time, (text[:110] + "…" if len(text) > 110 else text)


def _session_card(t, s, acts=""):
    """КАРТОЧКА живой сессии (правки владельца 08.07): по умолчанию СЛОЖЕНА —
    айди · заголовок · время последней записи; развернул — подробности
    (сделанное со временем-колонкой, «дальше», артефакты). *acts* — ряд
    действий (⟳/✕), живёт ВНУТРИ рамки и виден без разворота (решение 14.07)."""
    def _short(v, n=110):
        return v[:n] + "…" if len(v) > n else v

    title = _short(s["title"] or s["name"], 90)
    notes = [_note_parts(c) for c in s["context"]]
    last_time = next((tm for tm, _tx in reversed(notes) if tm), "")
    # сложенная шапка: подпись в языке доски + курсор обычной краской, без цветов;
    # роль (планирование/экзекьюция) — чипом, чтобы окна не путались (решение 08.07);
    # оркестратор помечен особо — «мы здесь»: якорь глаза при открытой развилке
    role = ""
    if s.get("role"):
        # словом человека (шаг 4), полная роль — тултипом
        role = ' · <span style="color:var(--c1)" title="{0}">{1}</span>'.format(
            esc(s["role"]), esc(_role_word(s["role"])))
        if s["role"].startswith("план"):
            # оркестратор помечен иконкой-поинтером «ты здесь» (правка владельца
            # 09.07: слова распирали подпись), смысл — в тултипе
            role += (' · <span title="мы здесь — оркестратор нити" '
                     'style="color:var(--c2)">'
                     '<svg viewBox="0 0 24 24" width="12" height="12" fill="none" '
                     'stroke="currentColor" stroke-width="2" stroke-linejoin="round" '
                     'style="vertical-align:-1px"><path d="M12 2 19 21 12 17 5 21Z"/>'
                     '</svg></span>')
    # шапка — ОДНА колонка во всю ширину (summary — flex: без обёртки подпись
    # и курсор встают бок-о-бок и длинный чип складывает подпись в столбик)
    head = ('<div style="flex:1;min-width:0">'
            '<div class="lbl" style="margin:0 0 6px">сессия {0}{3}{1}</div>'
            '<span style="color:var(--ink);line-height:1.55">{2}</span></div>'.format(
                esc(_sess_name(t["slug"], s["dir"])),
                " · {0}".format(esc(last_time)) if last_time else "",
                esc(_human(_short(s["cursor"])) or title), role))
    body = ['<div style="border-top:1px solid var(--line-2);margin-top:10px;padding-top:10px">'
            '<span style="color:var(--ink-dim)">{0}</span></div>'.format(esc(title))]
    if notes:
        body.append('<div style="margin-top:8px">')
        for time, text in notes:
            body.append('<div style="display:flex;gap:12px;padding:2px 0">'
                        '<span style="color:var(--ink-faint);font-family:var(--mono);'
                        'font-size:11px;min-width:38px;padding-top:2px">{0}</span>'
                        '<span style="color:var(--ink-dim)">{1}</span></div>'.format(
                            esc(time), esc(text)))
        body.append('</div>')
    if s.get("next"):
        body.append('<div style="display:flex;gap:12px;padding:2px 0">'
                    '<span style="color:var(--ink-faint);font-family:var(--mono);'
                    'font-size:11px;min-width:38px;padding-top:2px">дальше</span>'
                    '<span style="color:var(--ink-dim)">{0}</span></div>'.format(esc(_short(s["next"]))))
    # подвал — СТРОКАМИ, не флексом: раскрытые артефакты роняли кнопку ⟳
    # в вертикальный центр своего списка (кривая вёрстка, решение 08.07)
    foot = []
    art = _artifacts(t)
    if art:
        foot.append(art)
    # рамка — СНАРУЖИ details, действия (*acts*) — ВНУТРИ рамки и видимы в
    # сложенном виде (решение 14.07: «кнопки прям внутри карточки»); разворот
    # прячет только подробности, не действия
    return ('<div style="border:1px solid var(--line-2);border-radius:10px;'
            'padding:12px 16px;margin:2px 0 6px">'
            '<details class="tld"><summary>{0}</summary>{1}{2}</details>'
            '{3}</div>'.format(head, "".join(body), "".join(foot), acts))


def _session_done_row(t, s, when="", live=False):
    """Сессия одной СТРОКОЙ-СЕТКОЙ аккордеона (правка владельца 09.07: чёткость
    вида, ничего не скачет): одна линия [айди · заголовок… · дата · ⟳],
    перенос запрещён. Швы НЕ строки (неинформативный мусор) — время приёма
    сидит датой в строке сессии, «из кого» и что сделано — в спойлере.
    live=True — та же строка одной высоты, но с меткой «сейчас» и амбер-узлом
    (Addendum 12.07, вердикт 1: ряд ровный по киту, действия тихие — без
    гигантской кнопки; актуальная не выламывается из ряда)."""
    title = s["title"] if s["title"] and s["title"] != s["name"] else ""
    mark = " ✓" if s["closed"] else ""
    # заголовок в СТРОКЕ (её видно свёрнутой) — пульс агента: жаргонный не
    # светится (шаг 3), полный остаётся первой строкой панели ниже
    row_title = _human(title)
    if not row_title:
        # нет заголовка → свёрнутая строка всё равно должна сказать ЧТО СДЕЛАНО,
        # а не пустой «—»: сводка → последняя запись контекста → курсор
        alt = (s.get("summary") or
               (_note_parts(s["context"][-1])[1] if s["context"] else "") or
               s.get("cursor") or "")
        row_title = _human(alt.strip())
    # строка аккордеона по UI-киту: [айди · заголовок… · дата · ⟳ · шеврон],
    # один взгляд = одна строка; всё содержимое — в панели по клику.
    # Растворённая говорит об этом ПРЯМО В СТРОКЕ (решение 14.07: клик по ⟳ мёртвой
    # растворённой — тупик «gone», строка должна предупреждать заранее)
    diss = (' <span style="font-size:9px;letter-spacing:.12em;color:var(--ink-faint)">'
            'растворилась ⇄</span>' if s.get("dissolved") else "")
    # ОДНА явная кнопка возврата — пилюля как в богатой карточке (решение 17.07:
    # «нормальная кнопка вернуться, как у других мест»); кружок-узел больше НЕ
    # кликаемый, чтобы не двоить действие. Нет записанного чата → тихий спейсер.
    # onclick сам гасит всплытие (_resume_oc), клик по пилюле не сворачивает ряд.
    ret = ('<a class="abtn sret" href="#" title="вернуться в сессию" '
           'onclick="{0}">⟳ вернуться</a>'.format(_resume_action(t, s))
           if s.get("claude") else '<span class="sspace"></span>')
    head = ('<span class="sid">{0}{1}</span>'
            '<span class="stitle">{2}{5}</span>'
            '<span class="sdate">{3}</span>{4}{6}'.format(
                esc(_sess_name(t["slug"], s["dir"])), mark, esc(row_title) or "—",
                esc(when), ret, diss, CHEVRON))
    body = []
    # заголовок целиком уже виден в строке — в теле НЕ повторяем (решение 17.07:
    # «внутри навехерня»); тело = откуда пришла нить + собственные пульсы сессии
    if s.get("from"):
        body.append('<div class="srow" style="color:var(--ink-faint)">из {0}{1}</div>'.format(
            esc(s["from"]), " · принята {0}".format(esc(when)) if when else ""))
    if s.get("summary"):
        body.append('<div class="srow">{0}</div>'.format(esc(s["summary"])))
    for c in reversed(s["context"]):  # новейшее сверху (закон дома)
        tm, tx = _note_parts(c)
        body.append('<div class="srow"><span class="stime">{0}</span>'
                    '<span>{1}</span></div>'.format(esc(tm), esc(tx)))
    if not body:
        body = ['<div class="srow" style="color:var(--ink-faint)">паспорт пуст — '
                'сессия ничего о себе не записала</div>']
    sg = ('<span class="sg"><span class="sn">сейчас</span></span>' if live
          else '<span class="sg"></span>')
    return ('<div class="step {cls} sln">{sg}{mc}'
            '<div class="body"><details class="sess"><summary>{head}</summary>'
            '<div class="sbody">{body}</div></details></div></div>'.format(
                cls="now" if live else "done", sg=sg,
                mc='<span class="mc"><span class="m"></span></span>',
                head=head, body="".join(body)))


def _past_events(t, waves_done_rows, handoffs=None, newest_first=False):
    """ОДИН таймлайн прошлого (правка владельца 08.07): волны, сессии и передачи
    вперемешку по времени. Ключи: волна — дата гейта (конец дня), сессия —
    время шва, который её ПРИНЁС (нет шва — номер сессии = хронология,
    время синтезируется от соседки), шов — taken-at.
    waves_done_rows=[] даёт ленту только из сессий и швов."""
    from datetime import datetime as _dt, timedelta as _td

    def _parse(k):
        try:
            return _dt.strptime(str(k).replace("T", " ")[:16], "%Y-%m-%d %H:%M")
        except ValueError:
            return None

    floor = _dt(2000, 1, 1)
    taken = [r for r in (handoffs or []) if r["status"] == "taken"]
    actual = _live_session(t)
    started = {}  # имя сессии → когда её принесло швом
    for r in taken:
        if r["session"]:
            started[r["session"]] = _parse(r["taken_at"] or r["created"] or "")
    events = []  # (время, приоритет-при-равенстве, html): шов → сессия → волна
    for key, html in waves_done_rows:  # (дата гейта, html)
        events.append((_parse(key) or floor, 2, html))
    sess_past = sorted((s for s in t["sessions"] if s is not actual and not _is_stub(s)),
                       key=lambda s: s["name"])  # номер = хронология; пустышки-сиды не шаги
    last = floor
    for s in sess_past:
        k = started.get(s["name"]) or last + _td(minutes=1)
        real = s["name"] in started and started[s["name"]] is not None
        last = k
        # швы НЕ отдельные строки (правка владельца 09.07 — мусор): время приёма
        # сидит датой прямо в строке сессии, которую шов принёс
        events.append((k, 1, _session_done_row(
            t, s, when=k.strftime("%d.%m %H:%M") if real else "")))
    events.sort(key=lambda e: (e[0], e[1]), reverse=newest_first)
    return [h for _k, _p, h in events]


def _sessions_only(t, handoffs=None):
    """Режим «сессии»: только сессии и швы, новейшие сверху — быстрый пробег
    глазами «какие сессии были». Актуальная светится, к любой ⟳ возврат."""
    waiting = [r for r in (handoffs or []) if r["status"] == "offered"]
    actual = _live_session(t)
    rows = ['<div class="road"><div class="lbl">сессии · новейшие сверху</div>']
    rows += [_ho_row(r) for r in waiting]
    # актуальная сессия — та же строка одной высоты по киту, лишь с меткой
    # «сейчас» и амбер-узлом (Addendum вердикт 1: без гигантской кнопки)
    if actual:
        rows.append(_session_done_row(t, actual, live=True))
    rows += _past_events(t, [], handoffs, newest_first=True)
    rows.append("</div>")
    return "".join(rows)


def _view_chips(full, sess):
    """Фильтр вида (правка владельца 08.07): «всё» — один таймлайн работы;
    «сессии» — быстрый пробег только по сессиям. Чипы по киту (12.07):
    углы 4px, mono-капс, без пилюль."""
    return ('<div class="vroot">'
            '<div class="vbtns">'
            '<button class="vbtn on" onclick="var p=this.parentNode.parentNode;'
            "p.querySelector('.vw').style.display='';"
            "p.querySelector('.vs').style.display='none';"
            "this.classList.add('on');this.nextElementSibling.classList.remove('on')\""
            '>всё</button>'
            '<button class="vbtn" onclick="var p=this.parentNode.parentNode;'
            "p.querySelector('.vw').style.display='none';"
            "p.querySelector('.vs').style.display='';"
            "this.classList.add('on');this.previousElementSibling.classList.remove('on')\""
            '>сессии</button></div>'
            '<div class="vw">{w}</div>'
            '<div class="vs" style="display:none">{s}</div></div>').format(
                w=full, s=sess)


def _plural(n, one, few, many):
    """1 шаг · 2 шага · 5 шагов — без уродца «шаг(ов)»."""
    if 11 <= n % 100 <= 14:
        return many
    return {1: one, 2: few, 3: few, 4: few}.get(n % 10, many)


def _wave_title(name):
    """Имя волны без служебного маркера параллельности «∥N» из plan.md."""
    return re.sub(r"\s*∥\S*", "", name).strip()


def _fork_block(waves, recs, extra_fn, merge_w):
    """Развилка (правка владельца 08.07): параллельные волны рисуются РЯДОМ,
    не столбиком — каждая ветка своя колонка со своей кнопкой ▶. Открыл два
    окна — у каждого своя полоса, не теряешься."""
    cols = []
    for w, rec in zip(waves, recs):
        # колонка с оффером = ДРАФТ сессии (решение 08.07): подписываем честно —
        # это ещё не живая работа, а заготовка, которую рождает кнопка ▶
        bits = ['<div class="lbl" style="margin:0 0 8px">ветка · драфт сессии · '
                'оживает кнопкой ▶</div>' if rec else
                '<div class="lbl" style="margin:0 0 8px">ветка</div>',
                '<span class="gt">{0}</span>'.format(esc(_wave_title(w["name"]))),
                '<span class="gs">делается: {0}</span>'.format(esc(w["doing"])),
                '<span class="gs">результат: <b style="color:var(--ink)">{0}</b></span>'.format(
                    esc(w["result"]))]
        if w.get("check"):
            bits.append('<span class="gs" style="border-left:2px solid var(--c1);'
                        'padding-left:8px">закроется гейтом: {0}</span>'.format(esc(w["check"])))
        bits.append(extra_fn(w))
        if rec:
            tail = " → {0}".format(esc(rec["session"])) if rec["session"] else ""
            bits.append('<div style="margin-top:10px">{0}'
                        '<span style="color:var(--ink-faint);margin-left:12px">{1}{2}</span></div>'.format(
                            _take_btn(rec), esc(rec["mode"] or "continue"), tail))
        cols.append('<div style="flex:1;min-width:260px;border:1px solid var(--line-2);'
                    'border-radius:10px;padding:14px 16px">{0}</div>'.format("".join(bits)))
    fork = ('<div class="step now rich">'
            '<span class="sg"><span class="sn" style="color:var(--ink-mute)">развилка</span></span>'
            '<span class="mc"><span class="m"></span></span>'
            '<div class="body"><span class="gs" style="color:var(--ink-mute)">'
            '{0} шага идут параллельно — у каждой ветки своё окно и своя кнопка; '
            'оркестратор ◈ остаётся в своём окне и держит нить</span>'
            '<div style="display:flex;gap:12px;margin-top:8px;flex-wrap:wrap">{1}</div>'
            '</div></div>'.format(len(waves), "".join(cols)))
    # пара развилки — СЛИЯНИЕ: не выдумка вида, а ШАГ ИЗ ПЛАНА (правило владельца
    # 08.07: развилка легальна только с оформленным местом схождения)
    jbits = ['<span class="gt" style="color:var(--ink-dim)">{0}</span>'.format(
                 esc(_wave_title(merge_w["name"]))),
             '<span class="gs">{0}</span>'.format(esc(merge_w["doing"])),
             '<span class="gs">результат: <b style="color:var(--ink)">{0}</b></span>'.format(
                 esc(merge_w["result"]))]
    if merge_w.get("check"):
        jbits.append('<span class="gs" style="border-left:2px solid var(--c1);'
                     'padding-left:8px">закроется гейтом: {0}</span>'.format(esc(merge_w["check"])))
    join = ('<div class="step next rich">'
            '<span class="sg"><span class="sn" style="color:var(--ink-mute)">слияние</span></span>'
            '<span class="mc"><span class="m"></span></span>'
            '<div class="body">{0}</div></div>'.format("".join(jbits)))
    return fork + join


def _sess_acts(t, s):
    """ЕДИНЫЙ ряд действий сессии — пилюлями, видимыми сразу (решение 13.07:
    «нет кнопки вернуться» — кружок-узел и подвал свёрнутой карточки были
    прятками; 16.07: «возвращаться к ЛЮБОЙ сессии таймлайна, не только к
    последней»). ⟳ — всегда при claude-айди: живой фокус, растворённой —
    confirm-модалка с force-входом. Без записанного айди пустота говорит
    ПОЧЕМУ (ничего не тонет молча): у старых сессий сид в паспорт не писался.
    ✕ отпустить — только у свободной головы (нить закрыта)."""
    import urllib.parse as _up
    acts = []
    if s.get("claude"):
        lbl = ("⟳ зайти в прошлую сессию" if s.get("dissolved")
               else "⟳ вернуться в сессию")
        acts.append('<a class="abtn" href="#" onclick="{0}">{1}</a>'.format(
            _resume_action(t, s), lbl))
    else:
        acts.append('<span style="font-size:10px;color:var(--ink-faint);'
                    'align-self:center">чат сессии не записан — вернуться '
                    'некуда</span>')
    if (t.get("closed") and s.get("dir")
            and not s.get("closed") and not s.get("dismissed")):
        spath = str(t["path"] / "arcs" / s["dir"])
        acts.append(_two_step_btn(
            "✕ отпустить голову",
            "/dismiss?d=" + _up.quote(spath),
            "Голова выйдет из фокуса нити. След визита и кнопка ⟳ вернуться "
            "останутся — структура не тронута.",
            title="Отпустить голову?"))
    if not acts:
        return ""
    return ('<div style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap">'
            '{0}</div>'.format("".join(acts)))


def _confirm_onclick(url, title, body, ok="подтвердить", danger=True, stop=True, input=""):
    """Значение onclick, открывающее ТИПОВУЮ модалку подтверждения (deckConfirm)
    — ЕДИНЫЙ путь для всех опасных действий доски (выброс идеи · отпустить
    голову · закрыть нить). Параметры несём как JSON через html-escape: атрибут
    в двойных кавычках, `"` внутри → `&quot;`, браузер декодирует обратно — без
    ада экранирования и удвоения фигурных скобок в инлайн-JS. Саму работу
    (fetch + обновление доски на месте) делает модалка, не кнопка."""
    import json as _json
    fields = {"url": url, "title": title, "body": body, "ok": ok, "danger": bool(danger)}
    if input:
        fields["input"] = input  # текстовое поле (напр. итог одной строкой при закрытии)
    payload = esc(_json.dumps(fields, ensure_ascii=False, separators=(",", ":")))
    pre = ("event.preventDefault();event.stopPropagation();" if stop
           else "event.preventDefault();")
    return pre + "deckConfirm(" + payload + ")"


def _copy_onclick(text):
    """onclick, копирующий *text* в буфер через deckCopy (клипборд + тост). Текст
    несём как JSON через html-escape — переносы/кавычки целы, атрибут не рвётся
    (тот же приём, что _confirm_onclick)."""
    import json as _json
    payload = esc(_json.dumps(text, ensure_ascii=False))
    return ("event.preventDefault();event.stopPropagation();deckCopy(" + payload + ")")


# ── типовой блок «содержимое + ⧉ скопировать» ────────────────────────────────
# ОДИН элемент на всю доску (решение 30.07: «это типовой элемент должен быть»):
# команда артефакта на столе, код в теле заметки — и всё, что заведут дальше.
# Рендер один, здесь; стили одни, в CODE_CSS. Раньше стол и заметки держали по
# своей копии (.isblk/.ispre/.iscp против .ntcode/.ntpre/.ntcp) — и один и тот
# же баг починили бы дважды или, как вышло, ни разу.
# Как читается содержимое: (тег, класс). Команду и путь сверяют по символам —
# им моноширинка кита; сообщение читают прозой, как текст, которым оно станет.
_COPY_SKINS = {
    "code": ("pre", "cbpre"),
    "path": ("div", "cbpath"),
    "text": ("div", "cbtext"),
}


def _copy_block(content, skin="code", what="команду", copy_text=None):
    """Содержимое + ⧉ рядом. *skin* — как его читают (см. _COPY_SKINS), *what* —
    чем кнопка зовёт себя в тултипе, *copy_text* — что реально уедет в буфер,
    когда это не сам показанный текст (заметка копирует команды без строк-
    комментариев). Кнопка идёт ПОСЛЕ содержимого: она вторая колонка ряда, а не
    наложение на него, и порядок в разметке — тот же, что на экране."""
    tag, cls = _COPY_SKINS.get(skin, _COPY_SKINS["text"])
    return ('<div class="cblk"><{t} class="cbin {c}">{b}</{t}>'
            '<a class="sbtn cbcp" href="#" title="скопировать {w}" '
            'onclick="{o}">⧉</a></div>').format(
                t=tag, c=cls, b=esc(content), w=esc(what),
                o=_copy_onclick(content if copy_text is None else copy_text))


CODE_CSS = """
/* ТИПОВОЙ блок «содержимое + ⧉ скопировать» (рендер — _copy_block).
   Собран РЯДОМ, а не наложением, и это не вкусовщина: абсолютная кнопка поверх
   коробки кода врёт двумя способами. Первый — у коробки свой горизонтальный
   скролл (overflow-x:auto при white-space:pre), а end-padding в скролл-
   контейнере не входит в прокручиваемую ширину: «поле», оставленное кнопке
   паддингом, не держится, и длинная строка уезжает ПОД квадратик. Второй —
   кнопка привязана к углу блока, но растёт независимо от него (на телефоне до
   40px, пальцем): на однострочном содержимом она вываливалась вниз за кромку и
   читалась обрезанной. В ряду ширина кнопки вычитается ДО того, как содержимому
   раздали остаток, — пересечься им нечем ни при какой ширине.
   Отступов у блока СВОИХ нет: их держит поверхность, которая его позвала. */
.cblk{display:flex;align-items:flex-start;gap:8px}
.cbin{flex:1 1 auto;min-width:0}
.cblk .cbcp{flex:none;opacity:.55}
.cblk:hover .cbcp{opacity:1}
/* коробка кода — одна на стол и на заметки */
.cbpre{white-space:pre;overflow-x:auto;font-family:var(--mono);font-size:12px;
  line-height:1.6;color:var(--ink);background:var(--bg-1);
  border:1px solid var(--line);border-radius:6px;padding:11px 13px;margin:0}
/* путь бывает длиннее экрана телефона — рвём где угодно */
.cbpath{font-family:var(--mono);font-size:12px;line-height:1.6;color:var(--ink);
  word-break:break-all}
.cbtext{font-size:13.5px;line-height:1.6;color:var(--ink);white-space:pre-line;
  overflow-wrap:anywhere}
@media (max-width:700px){
  /* ⧉ пальцем: 40px в квадрате и всегда видима — hover'а на тапе нет, а
     полупрозрачный квадратик читался бы как «пока нельзя» */
  .cblk .cbcp{width:40px;height:40px;opacity:1}
  .cbpre,.cbpath{font-size:12.5px}
}
"""


def _two_step_btn(label, url, ask, title="Подтвердить действие?"):
    """Опасное действие через ТИПОВУЮ модалку (deckConfirm): клик открывает
    модалку (заголовок + текст + отмена/подтвердить), она делает fetch и
    обновляет доску НА МЕСТЕ. Раньше здесь был инлайн-взвод «точно? · ещё клик»
    прямо в кнопке (браузерный confirm() Chrome глушит молча, решение 12.07) —
    теперь один модальный путь на всё приложение."""
    return '<a class="abtn" href="#" onclick="{oc}">{l}</a>'.format(
        oc=_confirm_onclick(url=url, title=title, body=ask, ok=label), l=esc(label))


def _validate_btn(t, w):
    """✓ завалидировать гейт текущего шага С ДОСКИ (движок /validate): человек
    метит шаг пройденным глазами, план пишет гейт-пройден. Нет номера шага —
    нет кнопки. Пишет ТОЛЬКО гейт, не тикает задачи чеклиста."""
    # у закрытой нити гейт валидировать нечего — кнопки тут быть не должно.
    # Не «лишняя» проверка: сюда доходит нить, закрытая с НЕдоигранной волной
    # (у волны есть num и check, passed пустой) — план оборвали, а _plan_road
    # всё равно зовёт гейт. Так доска встала 12.08: закрытая нить приходит из
    # read_closed, где "dir" не было, и KeyError ронял ВЕСЬ прогон — сервер
    # сутки отдавал последний удачный билд
    if t.get("closed"):
        return ""
    if not w.get("num"):
        return ""
    return ('<a class="gatebtn" href="#" '
            'data-u="/validate?proj={p}&thread={d}&wave={n}" '
            "onclick=\"event.preventDefault();var el=this;el.textContent='…';"
            "fetch(el.dataset.u).then(function(r){{return r.text()}})"
            ".then(function(x){{el.textContent='✓ '+x;el.style.opacity='.6';"
            "el.style.pointerEvents='none'}})\">✓ завалидировать</a>".format(
                p=esc(t["proj"]), d=esc(t["dir"]), n=esc(w["num"])))


def _gate_confirm(t, w):
    """Гейт текущего шага С ЕГО КРИТЕРИЕМ прямо у кнопки (решение 12.07: «вижу
    кнопку завалидировать, но не понимаю что валидирую»). Видно НА ЧТО ставишь
    штамп — критерий гейта, затем кнопка. Нет критерия — валидировать нечего."""
    crit = _human(w.get("check") or "")
    if not w.get("num") or not crit:
        return ""
    return ('<div class="gateconfirm">'
            '<span class="gatecrit">на проверку: <b>{0}</b></span>{1}</div>'.format(
                esc(crit), _validate_btn(t, w)))


def _plan_road(t, wait, handoffs=None):
    """Хребет нити по закону 47: волны плана; текущая — развёрнута (что делается /
    результат / курсор сессии / швы / артефакты); журнал сессий — копнуть."""
    plan = t["plan"]
    live = _live_session(t)
    # openspec-чеклист живёт В таймлайне (решение 12.07): задачи каждого шага
    # вкладываются в его волну; общий прогресс N/M — в шапке дороги
    cl = read_spec_checklist(t)
    by_wave = cl["by_wave"] if cl else {}
    # версия плана и её история — одно целое (правка владельца 08.07): патчи
    # живут у заголовка «план vN», а не болтаются под финальным результатом
    # история версий плана (Addendum 12.07, вердикт 3): каждая версия — ОДНА
    # строка «vN · дата · суть до 80 знаков», разворот по клику НА СВОЮ версию;
    # никогда не вывалить все развёрнутыми разом (была стена текста)
    patches = ""
    if plan["patches"]:
        items = []
        for p in plan["patches"]:
            m = re.match(r"^(v\d+)\s*·\s*([^·]+?)\s*·\s*(.+)$", p)
            ver, date, rest = (m.group(1), m.group(2).strip(), m.group(3).strip()) if m else ("", "", p)
            head = " · ".join(x for x in (ver, date) if x)
            gist = (rest[:80] + "…") if len(rest) > 80 else rest
            items.append(
                '<details class="tld"><summary><span style="color:var(--ink-dim)">{0}</span>'
                '{1}{2}</summary><div class="tldd">{3}</div></details>'.format(
                    esc(head), " · " if head else "", esc(gist), esc(rest)))
        patches = (' · <details class="tld" style="display:inline-block;vertical-align:baseline">'
                   '<summary style="display:inline;cursor:pointer;font:inherit;letter-spacing:inherit;color:inherit">развитие плана · {0} версий</summary>'
                   '<div class="tldd" style="text-transform:none;letter-spacing:normal;'
                   'font-size:11.5px;line-height:1.7;max-width:80ch;color:var(--ink-dim)">'
                   '{1}</div></details>'.format(len(plan["patches"]), "".join(items)))
    prog = ' · {0}/{1}'.format(cl["done"], cl["total"]) if cl else ""
    rows = ['<div class="road"><div class="lbl">шаги · план {0}{1}{2}</div>'.format(
        esc(plan["version"]), prog, patches)]
    def _wave_extra(w):
        """Развёрнутое описание + «как проверять» — почитать по клику (закон 47)."""
        out = ""
        if w.get("desc"):
            out += ('<details class="tld"><summary>описание</summary>'
                    '<div class="tldd">{0}</div></details>'.format(esc(w["desc"])))
        if w.get("check"):
            out += ('<details class="tld"><summary>как проверять</summary>'
                    '<div class="tldd">{0}</div></details>'.format(esc(w["check"])))
        return out

    # порядок владельца (09.07): финал сверху → будущее (свёрнуто, позднее выше) →
    # текущее → история ВНИЗУ, новейшее сверху; ничего не пишется дважды
    done_rows, future_rows, now_rows = [], [], []
    now_waves = [w for w in plan["waves"] if w["state"] == "now"]
    pending = [r for r in (handoffs or []) if r["status"] == "offered"]
    # развилка — ФУНКЦИЯ ПЛАНА, не вывод доски (правило владельца 08.07): рисуем её
    # только когда она оформлена — активные волны связаны ∥-метками ДРУГ НА ДРУГА
    # и в плане есть явный шаг схождения («слияние»). Просто две активные волны
    # без оформления — обычные шаги столбиком, никакой развилки
    linked = (len(now_waves) > 1 and
              all(w["par"] and set(w["par"]) <= {v["num"] for v in now_waves if v is not w}
                  for w in now_waves))
    merge_w = next((w for w in plan["waves"]
                    if w["state"] != "done" and re.search(r"слияни", w["name"], re.I)), None)
    fork = linked and merge_w is not None
    branch_recs = (pending if fork and len(pending) == len(now_waves)
                   else [None] * len(now_waves))
    seen_now = False
    for w in plan["waves"]:
        if w["state"] == "done":
            # завершённое отвечает «ЧТО СДЕЛАНО»: дело + результат словами;
            # ключ времени — дата гейта (конец дня), чтобы встать среди сессий
            gm = re.search(r"(\d{2})\.(\d{2})", w.get("passed") or "")
            gkey = "2026-{0}-{1} 23:59".format(gm.group(2), gm.group(1)) if gm else ""
            done_rows.append((gkey,
                             '<div class="step done rich"><span class="sg"></span>'
                             '<span class="mc"><span class="m"></span></span>'
                             '<div class="body"><span class="gt">{0}</span>'
                             '<span class="gs">сделано: {1}</span>'
                             '<span class="gs">итог: <b style="color:var(--ink)">{2}</b></span>{3}{4}'
                             '</div></div>'.format(
                                 esc(w["name"]), esc(w["doing"]), esc(w["result"]),
                                 ('<span class="gs" style="color:var(--ink-mute)">гейт ✓ {0}</span>'
                                  .format(esc(w["passed"]))) if w.get("passed") else "",
                                 _wave_tasks(by_wave.get(w["num"], [])))))
        elif w["state"] == "now":
            first, seen_now = not seen_now, True
            if first:
                # СНАЧАЛА блок СЕССИИ — слой сессий параллелен волнам (правка
                # владельца 08.07): КАРТОЧКА при развилке; в обычном режиме
                # сессия уезжает в «подробнее» единого блока (решение 11.07)
                if fork:
                    if live:
                        now_rows.append('<div class="step now rich">'
                                        '<span class="sg"><span class="sn">сейчас</span></span>'
                                        + _mark_resume(t, live) +
                                        '<div class="body">{0}</div></div>'.format(_session_card(t, live, acts=_sess_acts(t, live))))
                    # офферы, не разобранные по веткам, остаются швами над развилкой
                    if branch_recs[0] is None:
                        now_rows += [_ho_row(rec) for rec in pending]
                    now_rows.append(_fork_block(now_waves, branch_recs, _wave_extra, merge_w))
                else:
                    now_rows += [_ho_row(rec) for rec in pending]
            if fork:
                continue  # ветки уже нарисованы развилкой выше
            # ОДИН блок «сейчас» = текущий шаг (решение 11.07: «сейчас и шаг —
            # их два, очень много текста»): видны только имя и итог; делается,
            # гейт, описание и карточка сессии — под «подробнее»
            # гейт-критерий больше НЕ прячется в «подробнее»: он у кнопки
            # (см. _gate_confirm) — видно, на что ставишь штамп (решение 12.07)
            det = ['<span class="gs">делается: {0}</span>'.format(esc(w["doing"]))]
            det.append(_wave_extra(w))
            mark = _mark_resume(t, live) if (live and first) else \
                   '<span class="mc"><span class="m"></span></span>'
            # ТЕКУЩАЯ сессия — КАРТОЧКОЙ, ВСЕГДА видимой (решение 14.07: «пускай
            # всегда сессия отображается, кнопки прям внутри карточки»); раньше
            # карточка пряталась под «подробнее», а кружок-⟳ кнопкой не читался
            sess_line = ""
            if live and first:
                sess_line = '<div style="margin-top:10px">{0}</div>'.format(
                    _session_card(t, live, acts=_sess_acts(t, live)))
            now_rows.append('<div class="step now rich">'
                            '<span class="sg"><span class="sn">сейчас</span></span>'
                            + mark +
                            '<div class="body"><span class="gt">{0}</span>'
                            '<span class="gs">итог: <b style="color:var(--ink)">{1}</b></span>'
                            '{6}{5}{3}{4}'
                            '<details class="tld inl"><summary>подробнее</summary>'
                            '<div class="tldd">{2}</div></details></div></div>'.format(
                                esc(_wave_title(w["name"])), esc(w["result"]),
                                "".join(det), _move_line(t, live), _gate_confirm(t, w),
                                _wave_tasks(by_wave.get(w["num"], [])), sess_line))
        else:
            if fork and w is merge_w:
                continue  # шаг слияния нарисован парой к развилке, не дублируем
            # будущее — одной строкой: имя + результат; ВСЕ детали в одном
            # «подробнее» (не три плюсика, решение 11.07)
            det = ['<div style="padding:2px 0">делается: {0}</div>'.format(esc(w["doing"]))]
            if w.get("desc"):
                det.append('<div style="padding:2px 0">{0}</div>'.format(esc(w["desc"])))
            if w.get("check"):
                det.append('<div style="padding:2px 0">гейт: {0}</div>'.format(esc(w["check"])))
            det.append(_wave_tasks(by_wave.get(w["num"], [])))
            future_rows.append('<div class="step next rich"><span class="sg"></span>'
                               '<span class="mc"><span class="m"></span></span>'
                               '<div class="body"><span class="gt" style="color:var(--ink-dim)">{0}</span>'
                               '<span class="gs"><i>{1}</i></span>'
                               '<details class="tld inl"><summary>подробнее</summary>'
                               '<div class="tldd">{2}</div></details>'
                               '</div></div>'.format(esc(_wave_title(w["name"])), esc(w["result"]),
                                                     "".join(det)))
    # сборка сверху вниз (решение 09.07): ФИНАЛ → будущее (позднее выше — время
    # течёт вверх, ближайший шаг у текущего) → ТЕКУЩЕЕ → история спойлером
    # внизу, новейшее сверху; цель и шаги не пишутся дважды.
    # «Закрыть нить» — ВЕРХНЕУРОВНЕВОЕ конечное действие (решение 14.07): живёт у
    # финального результата, когда все гейты подписаны, — не посреди таймлайна
    all_done = plan["waves"] and all(w["state"] == "done" for w in plan["waves"])
    close_pill = ""
    if all_done and not t.get("closed"):
        import urllib.parse as _up
        close_pill = '<div style="margin-top:10px">{0}</div>'.format(
            _two_step_btn(
                "✓ закрыть нить — на полку",
                "/close?d=" + _up.quote(str(t["path"])),
                "Нить встанет трофеем на полку, итог останется читаемым; "
                "живая сессия станет свободной головой.",
                title="Закрыть нить?"))
    rows.append('<div class="step goal rich"><span class="sg"></span>'
                '<span class="mc"><span class="m"></span></span>'
                '<div class="body"><span class="gt">финальный результат</span>'
                '<span class="gs">{0}</span>{1}</div></div>'.format(
                    esc(plan["final"]), close_pill))
    # будущее СВЁРНУТО как прошлое (решение 11.07): одна строка-спойлер
    if future_rows:
        rows.append('<details class="past"><summary>дальше · {0} {1}</summary>'
                    '<div class="paststeps">{2}</div></details>'.format(
                        len(future_rows), _plural(len(future_rows), "шаг", "шага", "шагов"),
                        "".join(reversed(future_rows))))
    # ЗАКРЫТИЕ РУКАМИ С ДОСКИ (решение 12.07: «гейт на шаг → разблокируется
    # закрытие цели»): все волны [x] (гейты подписаны), нить ещё открыта →
    # блок «сейчас» с пилюлей «закрыть нить» (/close → tide arc close).
    # Смерть структуры — только рука человека, кликом отсюда.
    if not now_rows and not t.get("closed") and all_done:
        # живая голова видна и ДОСТИЖИМА и здесь; сама кнопка закрытия уехала
        # наверх к финальному результату (решение 14.07: верхнеуровневое действие)
        head_bits = ""
        if live and live.get("claude"):
            head_bits = '<div style="margin-top:10px">{0}</div>'.format(
                _session_card(t, live, acts=_sess_acts(t, live)))
        mark = _mark_resume(t, live) if (live and live.get("claude")) else \
               '<span class="mc"><span class="m"></span></span>'
        now_rows.append(
            '<div class="step now rich">'
            '<span class="sg"><span class="sn">сейчас</span></span>'
            + mark +
            '<div class="body"><span class="gt">все гейты подписаны — нить готова '
            'закрыться</span>'
            '<span class="gs">закрытие — твоя рука, кнопка у финального результата '
            'сверху; живая сессия станет свободной головой</span>'
            '{0}</div></div>'.format(head_bits))
    # СВОБОДНАЯ ГОЛОВА (решение 12.07): нить закрыта (волн «сейчас» нет), а живая
    # сессия осталась — видна блоком «сейчас» со своей карточкой (⟳/✕ пилюли
    # в подвале), не хоронится под волнами. Отпустить можно только её.
    if not now_rows and t.get("closed"):
        hs = _head_session(t)
        if hs:
            now_rows.append('<div class="step now rich">'
                            '<span class="sg"><span class="sn">сейчас</span></span>'
                            + _mark_resume(t, hs) +
                            '<div class="body"><span class="gt">голова свободна — '
                            'нить закрыта</span>{0}</div></div>'.format(
                                _session_card(t, hs, acts=_sess_acts(t, hs))))
    # ВИСЯЩАЯ ПЕРЕДАЧА ВНЕ активной волны (решение 17.07, кейс threads на гейте):
    # передача случается МЕЖДУ шагами — когда ни одна волна не «сейчас», оффер
    # рисовался только внутри now-ветки и потому пропадал вовсе («0 передач»,
    # кнопки нет). Показываем ⌛-строку с ▶ запустить всегда, если её ещё не
    # отрисовала now-волна (seen_now) и это не форк.
    # ЖИВАЯ ГОЛОВА НА ГЕЙТЕ + ВИСЯЩИЙ ОФФЕР (решение 17.07, кейс threads): нить
    # между шагами (ни одной волны «сейчас»). Пока оффер offered (не taken),
    # ТЕКУЩАЯ сессия ещё держит нить — показываем ОБЕ строки (выбор владельца):
    # сверху ⟳ вернуться в текущую, под ней ⌛ ▶ запустить преемника. Раньше
    # карточка живой головы жила ТОЛЬКО внутри активной волны, а на гейте оффер
    # (или пустота) забирал весь слот «сейчас» — кнопка возврата в живую
    # пропадала (боль v6: «тут вообще кнопок нет»). Не дублируем, когда голову
    # уже нарисовала активная волна (seen_now) или ветка all_done (см. выше).
    head_row = ""
    if (not seen_now and not all_done and live and live.get("claude")
            and not t.get("closed")):
        cap = ("текущая сессия держит нить — вернуться сюда"
               if pending else "нить на гейте — выбираем следующий шаг")
        head_row = ('<div class="step now rich">'
                    '<span class="sg"><span class="sn">сейчас</span></span>'
                    + _mark_resume(t, live) +
                    '<div class="body"><span class="gt">{0}</span>{1}</div></div>'
                    .format(cap, _session_card(t, live, acts=_sess_acts(t, live))))
    if pending and not seen_now:
        now_rows = [_ho_row(rec) for rec in pending] + now_rows
    if head_row:  # ⟳ текущая — над ⌛ преемником (порядок из превью владельца)
        now_rows = [head_row] + now_rows
    rows += now_rows
    if done_rows or t["sessions"]:
        past = _past_events(t, done_rows, handoffs, newest_first=True)
        n_s = sum(1 for s in t["sessions"] if s is not live and not _is_stub(s))
        n_h = sum(1 for r in (handoffs or []) if r["status"] == "taken")
        # разворачиваем «пройдено» по умолчанию, когда есть РОДОСЛОВНАЯ — прошлые сессии
        # или швы (решение 14.07: связь сессий 1→2 и ⟳ к прошлой должны быть ВИДНЫ в
        # общем таймлайне, а не прятаться в подвале). Чистая история шагов без сессий —
        # остаётся свёрнутой (шум не разворачиваем).
        openattr = " open" if (n_s > 0 or n_h > 0) else ""
        rows.append('<details class="past"{5}><summary>пройдено · {0} {1} ✓ · {2} сессий · '
                    '{3} передач</summary><div class="paststeps">{4}</div></details>'.format(
                        len(done_rows), _plural(len(done_rows), "шаг", "шага", "шагов"),
                        n_s, n_h, "".join(past), openattr))
    rows.append("</div>")
    return "".join(rows)


# полоса «работы нити · N» над целью (решение 17.07) СНЯТА 30.07: с появлением
# вкладки «работы» она стала вторым ответом на тот же вопрос — и отвечала другим
# числом (работы ЭТОЙ нити против работ дома), из-за чего человек видел на одной
# странице «· 2» сверху и «· 3» на вкладке. Правда одна — вкладка со счётом по
# дому; работа своей нити и так узнаётся на карточке по ведущему агенту.


def _thread_about(pname, t):
    """Тело вкладки «суть» (решение 01.08, фикс 7 работы 28): зачем нить и её
    паспорт.

    Цель жила в ШАПКЕ страницы и занимала на телефоне пол-экрана над всем
    остальным («описание очень много места занимает; правильнее первую вкладку
    сделать, как паспорт»). Читают её редко — один раз, разбираясь, что это за
    нить, — а платили за неё каждым заходом. Теперь она первая вкладка: под
    рукой, но не на глазах; в шапке остались имя и дом.

    Паспорт — ТОЛЬКО то, что есть в данных нити: дом, позывной, вид, адрес
    папки, сколько сессий, когда последний пульс. Новых полей не выдумываем:
    пустое поле строки не рождает. Табличка — та же, что в паспорте работы
    (.wkprow/.wkpk/.wkpv): один рисунок на два паспорта, глаз читает их
    одинаково."""
    goal = _human(_real_goal(t))
    rows = [("дом", pname)]
    if t.get("tag"):
        rows.append(("позывной", t["tag"]))
    rows.append(("вид", "дежурка" if t.get("kind") == "routine" else "нить"))
    rows.append(("адрес", t.get("dir", "")))
    n_sess = len(t.get("sessions") or [])
    if n_sess:
        rows.append(("сессий", "{0}".format(n_sess)))
    if t.get("fresh"):
        # метка короткая намеренно: колонка меток узкая, «последний пульс»
        # ломался на две строки и уводил значение от своей подписи
        rows.append(("пульс", age(t["fresh"]) + " назад"))
    if t.get("spec"):
        rows.append(("спека", t["spec"]))
    table = "".join(
        '<div class="wkprow"><span class="wkpk">{0}</span>'
        '<span class="wkpv">{1}</span></div>'.format(esc(k), esc(v))
        for k, v in rows if str(v).strip())
    # ГЛАВНОЕ — ПЕРВЫМ, над целью (решение 33): у самого важного особое место и
    # особая процедура чтения. Читатель тот же, что у `tide thread`
    # (thread_screen.thread_main) — доска не разбирает plan.md сама.
    main = ""
    try:
        from tide.arc import thread_screen

        raw = thread_screen.thread_main(Path(t["path"])) if t.get("path") else ""
        if raw:
            # Пункт списка переносится через несколько строк файла — каждую
            # строку абзацем значило бы разорвать семь мыслей на двадцать девять
            # обрывков. Новый пункт начинает «- », остальное — продолжение.
            items = []
            for ln in raw.splitlines():
                if not ln.strip():
                    continue
                if ln.lstrip().startswith("- ") or not items:
                    items.append(ln.strip().lstrip("- ").strip())
                else:
                    items[-1] += " " + ln.strip()
            main = '<div class="dmain">{0}</div>'.format(
                "".join('<p>{0}</p>'.format(esc(i)) for i in items))
    except Exception:                       # noqa: BLE001 — доска не падает никогда
        main = ""
    return ('<div class="dabout">{m}{g}<div class="dpass">{t}</div></div>'.format(
        m=main,
        g=('<p class="dgoal">{0}</p>'.format(esc(goal)) if goal else
           '<p class="dgoal none">цель нити не записана — её знает агент</p>'),
        t=table))


def _thread_decisions(t):
    """Решения нити, посчитанные ДВИЖКОМ (tide.arc.thread_screen), или None.

    Доска не парсит decisions.md сама — она рендерит ровно тот словарь, который
    печатает `tide thread`. Иначе две поверхности начнут по-разному отвечать на
    «выполнено ли», и человек, пришедший проверить одну другой, останется без
    ответа вовсе. Импорт защищённый: доска обязана подниматься и там, где движок
    не импортируется (вкладка тогда просто не появится).
    """
    try:
        from tide.arc import thread_screen

        tdir = t.get("path")
        if tdir is None:
            return None
        tdir = Path(tdir)
        return thread_screen.decisions_state(tdir.parents[2], tdir)
    except Exception:                       # noqa: BLE001 — доска не падает никогда
        return None


def _dec_rows(recs, chip="owing"):
    """Строки решений: номер в колонке ключа, текст и чип в значении.

    Чип зависит от того, ЧТО строка спрашивает, а не от того, есть ли поле.
    `owing` — обещание ждёт исполнителя, и его отсутствие красится c2 («твой
    ход»). `done` — работа сделана, и чип говорит чем: работой или пруфом;
    пустоты тут не бывает, проверка её не пропустит. `none` — стоячее правило,
    исполнителя у него нет и не должно быть, чип был бы враньём.

    Так «нет работы» горит ровно там, где это проблема. Первый вид красил тем же
    тревожным чипом одиннадцать ВЫПОЛНЕННЫХ решений, доказанных коммитом, —
    девятнадцать алармов вместо восьми, и глаз перестал бы им верить сразу.
    """
    from tide.arc import decision

    out = []
    for d in recs:
        who, tag = decision.owner(d), ""
        if chip == "owing":
            tag = ('<span class="dwho">работа {0}</span>'.format(esc(who)) if who
                   else '<span class="dwho none">нет работы</span>')
        elif chip == "done":
            tag = '<span class="dwho">{0}</span>'.format(
                esc("работа {0}".format(who) if who else "по пруфу"))
        out.append('<div class="wkprow"><span class="wkpk">{0}</span>'
                   '<span class="wkpv">{1}{2}</span></div>'.format(
                       esc(str(d["num"])), esc(str(d.get("what") or d["slug"])), tag))
    return "".join(out)


def _decisions_pane(t):
    """Тело вкладки «решения» и число для ярлыка (работа 64, пункт 7).

    Возвращает пару (html, сколько обещаний ждёт дела) — число нужно ряду
    вкладок, и считать его повторным разбором собственного HTML было бы ровно
    тем вторым вычислением, которого мы избегаем.

    Первым — то, что горит: решения В СИЛЕ и НЕ выполненные, у каждого видно,
    несёт ли его работа. Это и есть болезнь, ради которой всё затевалось: девять
    решений были подписаны и не сделаны, три из них годами, и спросить об этом
    было нечем. Дальше — стоячие правила (они не «невыполненные», они критерии),
    выполненное числом, и снятое с истории. Порядок = порядок вопросов человека.
    """
    st = _thread_decisions(t)
    if not st or not st["all"]:
        return "", 0
    live, owing = st["live"], st["owing"]
    rules, done, retired = st["rules"], st["done"], st["retired"]
    lead = "{0} в силе · {1} выполнено · {2} нет · {3} {4}".format(
        len(live), len(done), len(owing), len(rules),
        _plural(len(rules), "стоячее правило", "стоячих правила", "стоячих правил"))
    if retired:
        lead += " · {0} снято или заменено".format(len(retired))
    body = []
    if owing:
        body.append('<div class="dsec">в силе, НЕ выполнено · {0}</div>{1}'.format(
            len(owing), _dec_rows(owing)))
    if rules:
        body.append('<div class="dsec">стоячие правила · {0}</div>{1}'.format(
            len(rules), _dec_rows(rules, chip="none")))
    # ВЫПОЛНЕННОЕ И СНЯТОЕ — СПОЙЛЕРОМ, языком доски (та же <details class="past">,
    # которой свёрнуто прошлое таймлайна). Это история: она нужна, чтобы не
    # перерешивать, но открывают её редко, а развёрнутой она топила бы восемь
    # горящих строк под тремя десятками прочитанных.
    if done:
        body.append('<details class="past"><summary>выполнено · {0}</summary>'
                    '<div class="paststeps">{1}</div></details>'.format(
                        len(done), _dec_rows(done, chip="done")))
    if retired:
        body.append('<details class="past"><summary>снято или заменено · {0}'
                    '</summary><div class="paststeps">{1}</div></details>'.format(
                        len(retired), _dec_rows(retired, chip="done")))
    return ('<div class="dabout"><p class="dgoal">{0}</p>'
            '<div class="dpass">{1}</div></div>').format(
                esc(lead), "".join(body)), len(owing)


def _detail_tabs(pname, tdir, timeline, counts, about="", decisions=("", 0)):
    """Вкладки страницы нити (решение 30.07, работа 20; кандидаты — работа 28):
    таймлайн · работы · issues · кандидаты. Таймлайн — прежний вид нити как есть
    и первый по умолчанию; работы — работы ЭТОЙ нити (работа 24), issues и
    кандидаты — всего дома, чтобы за ними человек не ходил на общую вкладку.

    Тела «работы», «issues» и «кандидаты» СЮДА не кладём: стол дома одинаков у
    всех его нитей, а нитей в JSON под сотню — каждая копия множилась бы на все.
    Панели лежат в window.TP (см. _proj_panes), вкладка держит только адрес
    (`data-tp="работы|дом|нить"`) и вклеивается при первом показе
    (SHELF_TABS_JS). Ярлык без числа, когда пусто, — как в ряду вкладок доски:
    ноль числом мозолит глаз."""
    nw, ni, nc = counts
    # число на ярлыке = сколько решений ЖДЁТ дела: в силе и не выполнено. Не
    # всего решений — всего их три десятка и число это ничего не говорит; ярлык
    # должен ловить глаз ровно тогда, когда нить кому-то что-то должна.
    decisions, nd = decisions
    # «СУТЬ» ПЕРВОЙ, ОТКРЫТ ТАЙМЛАЙН (фикс 7 работы 28). Порядок и активность —
    # разные вещи: паспорт стоит первым, потому что это начало разговора о нити,
    # но открывать его каждый раз незачем — человек приходит смотреть, что
    # происходит СЕЙЧАС. Поэтому `.on` метится по ключу, а не по месту в ряду.
    tabs = [("tab", "суть", 0, ""),
            ("tl", "таймлайн", 0, ""),
            # «решения» стоят СРАЗУ за таймлайном, до работ: работа отвечает
            # «что сейчас делается», решение — «на чём мы вообще стоим и что из
            # обещанного не сделано». Второе объясняет первое, и его ищут раньше.
            ("tdc", "решения", nd, ""),
            ("twk", "работы", nw, "works|{0}|{1}".format(pname, tdir)),
            ("tis", "issues", ni, "issues|" + pname),
            ("tcd", "кандидаты", nc, "cands|" + pname)]
    bar = "".join(
        '<button class="stab{on}" data-stab="{k}">{l}{n}</button>'.format(
            on=" on" if k == "tl" else "", k=k, l=lbl,
            n=" · {0}".format(n) if n else "")
        for k, lbl, n, _tp in tabs)
    # тело «сути» вклеено прямо тут, а не через window.TP: оно СВОЁ у каждой
    # нити (в отличие от стола и кандидатов дома) и весит пару строк — гонять
    # его через общий словарь панелей значило бы городить дорогу ради ничего
    panes = ['<div class="spane" data-spane="tab" hidden>{0}</div>'.format(about),
             '<div class="spane" data-spane="tl">{0}</div>'.format(timeline),
             # решения — СВОИ у каждой нити, как и «суть»: тело вклеено прямо
             # тут, а не гоняется через общий window.TP словарь панелей дома
             '<div class="spane" data-spane="tdc" hidden>{0}</div>'.format(decisions)]
    panes += ['<div class="spane" data-spane="{k}" data-tp="{tp}" hidden>'
              '</div>'.format(k=k, tp=esc(tp))
              for k, _l, _n, tp in tabs[3:]]
    return '<div class="stabs dtabs">{0}</div>{1}'.format(bar, "".join(panes))


def detail(pname, t, wait, label=None, handoffs=None, counts=(0, 0, 0)):
    # ЕДИНЫЙ вид (правка владельца 08.07): работа и сессии в одном потоке —
    # прошлое и родословная свёрнуты, развёрнуто только «сейчас» и дорога вперёд
    if t.get("plan"):
        struct = _plan_road(t, wait, handoffs=handoffs)
    elif t["sessions"]:
        struct = _road(t, wait, handoffs=handoffs)
    else:
        struct = ('<div class="dmore"><div class="lbl">подробнее</div>'
                  '<p>заявка: нить заведена с целью, но работа ещё не начиналась — '
                  'ни одной сессии. Стартовать — скажи агенту.</p></div>')
    struct = _detail_tabs(pname, _tdir(t), struct, counts,
                          about=_thread_about(pname, t),
                          decisions=_decisions_pane(t))
    # табы ВСЁ/СЕССИИ убраны (визуальный шум): сессии и так в основном потоке
    # дороги, отдельный вид-фильтр не нужен
    # now-плашка упразднена (правка владельца 08.07): она дублировала курсор
    # развёрнутой волны — внимание держит сама волна «сейчас» в потоке
    now = None
    return {"name": t["tag"] or label or t["slug"], "icon": ICON_ROUTINE if t["kind"] == "routine" else ICON_THREAD,
            # подпись = только проект (решение 12.07: слаг-имена «handoff-hygiene»
            # непонятны человеку — техническое живёт в файлах, не в шапке)
            "proj": pname,
            # ЦЕЛЬ УЕХАЛА С ШАПКИ ВО ВКЛАДКУ «СУТЬ» (решение 01.08, фикс 7 работы
            # 28): она стояла тут с 12.07 и на телефоне занимала пол-экрана над
            # всем, что человек пришёл смотреть. Читают её раз, платили за неё
            # каждым заходом. Шапке остались имя и дом; текст цели целиком — в
            # первой вкладке (_thread_about), пустое поле `.dwhy` прячет CSS.
            "why": "", "move": "", "struct": struct,
            "now": now, "foldmove": False}


HELP_MODAL = """
<div id="help" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:60;align-items:center;justify-content:center">
  <div style="position:relative;max-width:520px;background:var(--bg-1);border:1px solid var(--line-2);border-radius:12px;padding:28px 30px;line-height:1.65;color:var(--ink-dim)">
    <button id="helpx" aria-label="закрыть" style="position:absolute;top:10px;right:12px;background:none;border:none;color:var(--ink-mute);font:inherit;font-size:18px;cursor:pointer;padding:6px">✕</button>
    <div class="slabel" style="margin:0 0 12px">справка · H или ✕ закрывает</div>
    <p style="margin:0 0 10px"><b style="color:var(--ink)">Только смотреть.</b> Тапни карту — провалишься в нить. Управляешь через агента, не тут.</p>
    <p style="margin:0 0 10px">Активные нити, свежие сверху. Проекты — группировка, самый живой выше. Архив прячет проект целиком.</p>
    <p style="margin:0 0 10px"><b style="color:var(--ink)">⌛ передача</b> — нить ждёт подхвата (tide menu). Возраст на карте — давность последнего движения.</p>
    <p style="margin:0 0 10px"><b style="color:var(--ink)">M</b> — стол ⇄ проекты. В проекте — полка: живое, кандидаты, закрытое ✓.</p>
    <p style="margin:0"><b style="color:var(--ink)">1–7</b> — вкладки по порядку слева направо. <b style="color:var(--ink)">F1–F7</b> — открыть нить из фокуса по номеру.</p>
  </div>
</div>"""

HELP_JS = """
const help=document.getElementById('help');
function helpOpen(){ help.style.display='flex'; }
function helpClose(){ help.style.display='none'; }
function helpToggle(){ (help.style.display==='flex') ? helpClose() : helpOpen(); }
document.getElementById('helpx').addEventListener('click',helpClose);
help.addEventListener('click',e=>{ if(e.target===help) helpClose(); });
document.addEventListener('keydown',e=>{
  // справка только по Cmd+H (решение 17.07): голая H стреляла при наборе
  if(e.metaKey&&!e.shiftKey&&!e.altKey
     &&(e.key==='h'||e.key==='H'||e.key==='р'||e.key==='Р')){
    e.preventDefault(); helpToggle(); return; }
  if(e.key==='Escape'&&help.style.display==='flex') helpClose();
});"""


SETTINGS_MODAL = """
<div id="settings" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:65;align-items:center;justify-content:center">
  <div style="position:relative;max-width:460px;width:calc(100% - 48px);background:var(--bg-1);border:1px solid var(--line-2);border-radius:12px;padding:26px 30px;color:var(--ink-dim)">
    <button id="setx" aria-label="закрыть" style="position:absolute;top:10px;right:12px;background:none;border:none;color:var(--ink-mute);font:inherit;font-size:18px;cursor:pointer;padding:6px">✕</button>
    <div class="slabel" style="margin:0 0 10px">настройки доски · ✕ закрывает</div>
    <div class="setrow">
      <div><div class="t">Табы-фильтр по проектам</div>
        <div class="d">ряд табов над столом: «все» + таб на проект; фильтрует фокус, отложенные и нити</div></div>
      <input type="checkbox" id="set-ptabs">
    </div>
    <div class="setrow">
      <div><div class="t">Палитра</div>
        <div class="d">цветовая тема доски</div></div>
      __PALSEL__
    </div>
  </div>
</div>"""

SETTINGS_JS = """
// настройки доски (решение 14.07): шестерёнка справа от палитры. Живут в
// localStorage (как палитра и вид), применяются на месте без перезагрузки.
const setm=document.getElementById('settings');
const setPt=document.getElementById('set-ptabs');
function setOpen(){ setPt.checked=(localStorage.getItem('board-ptabs-on')||'1')==='1'; setm.style.display='flex'; }
function setClose(){ setm.style.display='none'; }
document.getElementById('setbtn').addEventListener('click',setOpen);
document.getElementById('setx').addEventListener('click',setClose);
setm.addEventListener('click',e=>{ if(e.target===setm) setClose(); });
document.addEventListener('keydown',e=>{ if(e.key==='Escape'&&setm.style.display==='flex') setClose(); });
setPt.addEventListener('change',()=>{
  try{ localStorage.setItem('board-ptabs-on', setPt.checked?'1':'0'); }catch(e){}
  applyPtab();
});"""


SETTINGS_GEAR = (
    '<button id="setbtn" class="setbtn" title="настройки доски" aria-label="настройки доски">'
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" '
    'stroke-linecap="round"><path d="M3 5h7M12 3v4M14 5h7'
    'M3 12h3M8 10v4M10 12h11M3 19h11M16 17v4M18 19h3"/></svg></button>')


CONFIRM_MODAL = """
<div id="confirm" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:70;align-items:center;justify-content:center">
  <div style="position:relative;max-width:440px;width:calc(100% - 48px);background:var(--bg-1);border:1px solid var(--line-2);border-radius:12px;padding:26px 28px;color:var(--ink-dim)">
    <div id="cfm-title" style="margin:0 0 12px;color:var(--ink);font-size:15px;font-weight:600"></div>
    <p id="cfm-body" style="margin:0 0 14px;line-height:1.6;font-size:13px"></p>
    <input id="cfm-input" type="text" style="display:none;width:100%;box-sizing:border-box;margin:0 0 18px;padding:8px 10px;background:var(--bg-1);border:1px solid var(--line);border-radius:6px;color:var(--ink);font:13px ui-monospace,monospace">

    <div style="display:flex;gap:10px;justify-content:flex-end">
      <a id="cfm-cancel" class="abtn" href="#">отмена</a>
      <a id="cfm-ok" class="abtn primary" href="#">подтвердить</a>
    </div>
  </div>
</div>"""

# Инлайн-стилей тут нет (решение 30.07, фикс 7 работы 22): вид модалки — часть
# кита и живёт в WORK_CSS рядом с видом карточки, иначе мобильные поля и рамку
# правкой не достанешь (инлайн бьёт любое правило без !important).
WORK_MODAL = """
<div id="wkmodal">
  <div class="wkmpane">
    <div class="wkmtop">__LEGEND__<button id="wkmx" aria-label="закрыть">✕</button></div>
    <div class="wkmbody"></div>
  </div>
</div>"""

WORK_MODAL_JS = """
// ПРОВАЛИВАНИЕ в работу (решение 17.07, переосмыслено 30.07 работой 21): лицо
// карточки говорит одну строку состояния, а полный вид — план, чеклист, фиксы,
// журнал и все жесты — открывается тапом. Модалка клонирует ЖИВУЮ карточку из
// вкладки «работа» (CSS разворачивает в ней скрытый .wkfull), жесты работают
// через делегирование. ✕/клик-мимо/Esc закрывают.
(function(){
  const m=document.getElementById('wkmodal');
  if(!m) return;
  const body=m.querySelector('.wkmbody');
  const sel=s=>(window.CSS&&CSS.escape?CSS.escape(s):s);
  // ЗАМОК ФОНА (решение 31.07, работа 25: «под ней можно страницу проскролить»).
  // Скроллящих контейнеров на доске два — сама страница (html держит скролл,
  // см. scope/index.html) и страница нити (.detail, свой fixed-слой со своим
  // скроллом). Замок — один класс на <html>, правила гасят оба разом; сам
  // модал скроллит внутри себя и цепочку наружу не пускает
  // (overscroll-behavior). Позицию браузер держит сам: вернули overflow —
  // человек стоит там же, откуда провалился, без прыжка наверх.
  const lock=on=>document.documentElement.classList.toggle('wkmlock', !!on);
  function wkmClose(){ m.style.display='none'; body.innerHTML='';
    window.__wkmSlug=''; lock(false); }
  function wkmFill(keep){
    const src=document.querySelector('#work .wkcard[data-wk="'+
      sel(window.__wkmSlug)+'"]');
    if(!src){ if(keep) wkmClose(); else (window.deckToast||alert)('работа не найдена');
      return false; }
    // развороты спойлеров (план/журнал) переживают пересборку: иначе каждый
    // чек схлопывал бы то, что человек только что открыл читать
    const open=keep?[...body.querySelectorAll('details')].map(x=>x.open):[];
    body.innerHTML=''; body.appendChild(src.cloneNode(true));
    if(keep) [...body.querySelectorAll('details')].forEach((x,i)=>{
      if(open[i]!==undefined) x.open=open[i]; });
    return true;
  }
  function wkmOpen(slug){
    window.__wkmSlug=slug;
    if(wkmFill(false)){
      m.style.display='flex'; body.scrollTop=0; lock(true);
      // легенда живёт в оболочке модалки (фикс 7 работы 26) и потому переживает
      // её закрытие — свернём, иначе следующая работа открылась бы со справкой
      // поверх своего же заголовка
      const lg=m.querySelector('.wkleg'); if(lg) lg.open=false;
    } else window.__wkmSlug='';
  }
  // чек и правка теперь живут ТОЛЬКО тут, а boardRefresh свопает #work целиком —
  // без пересборки клона галочка в модалке не шевелилась бы до её закрытия
  const wk=document.getElementById('work');
  if(wk) new MutationObserver(()=>{
    if(m.style.display==='flex'&&window.__wkmSlug&&!window.__wkEditing)
      wkmFill(true);
  }).observe(wk,{childList:true,subtree:true});
  document.addEventListener('click',e=>{
    const t=e.target;
    if(t===m||t.id==='wkmx'){ wkmClose(); return; }
    // внутри модалки карточка уже развёрнута: там клик принадлежит жестам
    // (квадратик, текст, крестик), а не второму проваливанию
    if(t.closest&&t.closest('#wkmodal')) return;
    // блок нити развёрнут прямо на площадке (решение 07.08): внутри полного вида
    // клик принадлежит его жестам и чтению (свёртки, правка пункта, крестик),
    // а не проваливанию — иначе поверх развёрнутой работы вставала бы модалка
    // с ней же. Проваливаются по ЛИЦУ блока: имя, мета, строка состояния
    if(t.closest&&t.closest('.wkblkcol .wkfull')) return;
    const dw=t.closest&&t.closest('[data-wkopen]');
    if(!dw) return;
    // руки, живущие на самом лице (имя, чип дедлайна, «закрыть», прыжок в
    // ведущего), остаются собой — по ним карточка не проваливается
    if(t.closest('button,a,input,select,summary,[contenteditable="true"],'
      +'.wkdl[data-dl],.wknm[data-title]')) return;
    e.preventDefault(); wkmOpen(dw.dataset.wkopen);
  });
  document.addEventListener('keydown',e=>{
    if(e.key==='Escape'&&m.style.display==='flex') wkmClose(); });
})();"""

SHELF_TABS_JS = """
// вкладки полки проекта (решение 17.07): нити · работы · идеи · заметки · закрыто.
// ОН ЖЕ — вкладки страницы нити (решение 30.07, работа 20): таймлайн · работы ·
// issues, ряд с классом .dtabs. Механика одна, разъезжаться ей незачем.
// Активная вкладка запоминается и переживает пересборку .dstruct (autorefresh
// свопает innerHTML) — MutationObserver переприменяет её. Нет такой вкладки в
// этом виде (напр. другой проект) → падаем на первую. Память РАЗДЕЛЬНАЯ: у
// полки и у нити свои наборы ключей, и одна переменная роняла бы соседа на
// первую вкладку каждый раз, когда человек ходит полка↔нить.
(function(){
  window.__stab = window.__stab || 'live';
  window.__dtab = window.__dtab || 'tl';
  const mem = bar => (bar && bar.classList.contains('dtabs')) ? '__dtab' : '__stab';
  // тела «работы»/«issues» одинаковы для всех нитей дома и лежат одной копией
  // в window.TP (см. _proj_panes) — вкладка знает лишь адрес и вклеивает своё
  // при первом показе. data-done — замок от повторной вклейки; своп
  // автообновления приносит панель заново, и замок уезжает вместе с ней
  function fill(p){
    const src=p.dataset.tp;
    if(!src || p.dataset.done) return;
    const cut=src.indexOf('|');
    const rec=(window.TP||{})[src.slice(cut+1)];
    const html=rec && rec[src.slice(0,cut)];
    p.innerHTML = html || '<p class="shempty">у дома тут пусто.</p>';
    p.dataset.done='1';
  }
  function apply(root){
    if(!root) return;
    const tabs=[...root.querySelectorAll('.stab')];
    if(!tabs.length) return;
    const bar=root.querySelector('.stabs');
    const mk=mem(bar);
    let key=window[mk];
    if(!tabs.some(b=>b.dataset.stab===key)) key=tabs[0].dataset.stab;
    tabs.forEach(b=>b.classList.toggle('on', b.dataset.stab===key));
    root.querySelectorAll('.spane').forEach(p=>{
      p.hidden = p.dataset.spane!==key;
      if(!p.hidden) fill(p);
    });
  }
  document.addEventListener('click',e=>{
    const b=e.target.closest && e.target.closest('.stab');
    if(!b) return;
    window[mem(b.closest('.stabs'))]=b.dataset.stab;
    apply(b.closest('.dstruct')||document.querySelector('.dstruct'));
  });
  const ds=document.querySelector('.dstruct');
  if(ds){ new MutationObserver(()=>apply(ds)).observe(ds,{childList:true}); apply(ds); }
})();"""

CONFIRM_JS = """
// ТИПОВАЯ модалка подтверждения — ОДИН путь для всех опасных действий доски
// (выброс идеи · отпустить голову · закрыть нить). deckConfirm(o) показывает её;
// ОК делает fetch(o.url) и обновляет доску НА МЕСТЕ (boardRefresh), а НЕ
// location.reload() — чтобы не ронять тебя из открытой полки на главную (решение владельца
// 13.07). Отмена/клик-мимо/Esc — закрывают. Ошибка сервера — словами в тело.
const cfm=document.getElementById('confirm');
const cfmT=document.getElementById('cfm-title');
const cfmB=document.getElementById('cfm-body');
const cfmOk=document.getElementById('cfm-ok');
const cfmNo=document.getElementById('cfm-cancel');
const cfmI=document.getElementById('cfm-input');
let cfmCur=null;
function cfmClose(){ cfm.style.display='none'; cfmCur=null; }
function deckConfirm(o){
  cfmCur=o;
  cfmT.textContent=o.title||'Подтвердить действие?';
  cfmB.textContent=o.body||'';
  cfmOk.textContent=o.ok||'подтвердить';
  cfmOk.classList.toggle('danger', !!o.danger);
  cfmOk.classList.toggle('primary', !o.danger);
  if(o.input){ cfmI.style.display='block'; cfmI.placeholder=o.input; cfmI.value=''; }
  else { cfmI.style.display='none'; }
  cfm.style.display='flex';
  if(o.input){ setTimeout(function(){ cfmI.focus(); }, 30); }
}
async function cfmGo(){
  if(!cfmCur) return;
  const o=cfmCur, lbl=o.ok||'подтвердить';
  cfmOk.textContent='…';
  try{
    var url=o.url;
    if(cfmI.style.display!=='none'&&cfmI.value.trim()){
      url+=(url.indexOf('?')<0?'?':'&')+'result='+encodeURIComponent(cfmI.value.trim()); }
    const r=await fetch(url);
    const txt=await r.text().catch(function(){ return ''; });
    if(r.ok){ cfmClose(); if(typeof boardRefresh==='function'){ boardRefresh(); } else { location.reload(); } }
    else{ cfmB.textContent='✗ '+(txt||('ошибка '+r.status)); cfmOk.textContent=lbl; }
  }catch(e){ cfmB.textContent='✗ '+e; cfmOk.textContent=lbl; }
}
cfmOk.addEventListener('click',function(e){ e.preventDefault(); cfmGo(); });
cfmNo.addEventListener('click',function(e){ e.preventDefault(); cfmClose(); });
cfm.addEventListener('click',function(e){ if(e.target===cfm) cfmClose(); });
document.addEventListener('keydown',function(e){ if(e.key==='Escape'&&cfm.style.display==='flex') cfmClose(); });"""


COPY_JS = """
// deckCopy — копировать текст в буфер (решение 13.07: скопировать описание идеи,
// вставить в оркестр-сессию, сказать «забери в работу»). Clipboard API + фолбэк
// на скрытый textarea (Tauri-webview/старый контекст), + тост-подтверждение.
function deckToast(msg){
  var t=document.getElementById('toast');
  if(!t){ t=document.createElement('div'); t.id='toast'; document.body.appendChild(t); }
  t.textContent=msg; t.classList.add('show');
  clearTimeout(window.__toastT);
  window.__toastT=setTimeout(function(){ t.classList.remove('show'); }, 1600);
}
function deckCopy(text){
  function ok(v){ deckToast(v ? 'скопировано' : 'не вышло скопировать'); }
  function fallback(){
    try{
      var ta=document.createElement('textarea'); ta.value=text;
      ta.style.position='fixed'; ta.style.top='-1000px'; ta.style.opacity='0';
      document.body.appendChild(ta); ta.focus(); ta.select();
      var done=document.execCommand('copy'); document.body.removeChild(ta); ok(done);
    }catch(e){ ok(false); }
  }
  if(navigator.clipboard && navigator.clipboard.writeText){
    navigator.clipboard.writeText(text).then(function(){ ok(true); }, fallback);
  } else { fallback(); }
}
// deckNewThread — старт новой нити с доски (решение 13.07): имя → /new-thread →
// нить заводится + поднимается сессией; обновляем доску на месте (boardRefresh).
function deckNewThread(proj, el){
  var name=(el.value||'').trim();
  if(!name){ el.focus(); return; }
  el.disabled=true;
  fetch('/new-thread?proj='+encodeURIComponent(proj)+'&name='+encodeURIComponent(name))
    .then(function(r){ return r.text().then(function(t){
      el.disabled=false;
      if(r.ok){ el.value=''; deckToast(t||'нить заведена');
        if(typeof boardRefresh==='function'){ boardRefresh(); } }
      else { deckToast('✗ '+t); el.focus(); }
    }); })
    .catch(function(e){ el.disabled=false; deckToast('✗ '+e); });
}
// deckAddCand — добавить идею-кандидата с полки (решение 13.07): текст → /add-cand
// → ложится в бэклог; обновляем доску на месте.
function deckAddCand(proj, el){
  var text=(el.value||'').trim();
  if(!text){ el.focus(); return; }
  el.disabled=true;
  fetch('/add-cand?proj='+encodeURIComponent(proj)+'&text='+encodeURIComponent(text))
    .then(function(r){ return r.text().then(function(t){
      el.disabled=false;
      if(r.ok){ el.value=''; deckToast(t||'кандидат добавлен');
        if(typeof boardRefresh==='function'){ boardRefresh(); } }
      else { deckToast('✗ '+t); el.focus(); }
    }); })
    .catch(function(e){ el.disabled=false; deckToast('✗ '+e); });
}
// правка заметки как у работ (решение 17.07): заголовок — wkEdit на месте;
// клик по абзацу тела — textarea с сырым текстом (tags: правится там же),
// Esc — отмена, blur/Cmd+Enter — сохранить
document.addEventListener('click',function(e){
  var row=e.target.closest&&e.target.closest('.ntrow'); if(!row) return;
  var slug=row.dataset.nt, proj=row.dataset.ntp;
  var ti=e.target.closest('.sknm');
  if(ti&&ti.dataset.ntitle&&!window.__wkEditing){
    e.preventDefault(); e.stopPropagation();
    wkEdit(ti,function(t){ if(t) return wkCall('/note-edit?proj='+encodeURIComponent(proj)
      +'&f='+encodeURIComponent(slug)+'&what=title&t='+encodeURIComponent(t)); });
    return; }
  if(e.target.closest('.ntp')&&!row.querySelector('.ntta')){
    var body=row.querySelector('.ntbody'), tpl=row.querySelector('.ntraw');
    if(!body||!tpl) return;
    var ta=document.createElement('textarea'); ta.className='ntta';
    ta.value=tpl.content.textContent;
    var hint=document.createElement('div'); hint.className='nthint';
    hint.textContent='Esc — отмена · клик мимо/⌘Enter — сохранить · теги строкой tags:';
    body.replaceChildren(ta,hint); ta.focus();
    var fin=function(ok){
      if(ok) wkCall('/note-edit?proj='+encodeURIComponent(proj)
        +'&f='+encodeURIComponent(slug)+'&what=rest&t='
        +encodeURIComponent(ta.value)).then(function(){ boardRefresh(); });
      else boardRefresh(); };
    ta.onkeydown=function(ev){
      if(ev.key==='Escape'){ ta.onblur=null; fin(false); }
      if(ev.key==='Enter'&&(ev.metaKey||ev.ctrlKey)){ ta.onblur=null; fin(true); } };
    ta.onblur=function(){ fin(true); };
  }
});
function deckAddNote(proj, el){
  var text=(el.value||'').trim();
  if(!text){ el.focus(); return; }
  el.disabled=true;
  fetch('/note-add?proj='+encodeURIComponent(proj)+'&text='+encodeURIComponent(text))
    .then(function(r){ return r.text().then(function(t){
      el.disabled=false;
      if(r.ok){ el.value=''; deckToast(t||'заметка записана');
        if(typeof boardRefresh==='function'){ boardRefresh(); } }
      else { deckToast('✗ '+t); el.focus(); }
    }); })
    .catch(function(e){ el.disabled=false; deckToast('✗ '+e); });
}"""


AUTOREFRESH_JS = """
// живой рефреш (правка владельца 08.07): раз в 5 с тихо тянем свежий рендер БЕЗ
// перезагрузки страницы. Карточки и шапка меняются на месте; открытая деталь
// перерисовывается только если её данные реально изменились — со скроллом и
// раскрытыми details как были. Вкладка не видна — не дёргаемся.
const _openT = openT;
openT = function(id){ window.__openId = id; _openT(id); };
async function boardRefresh(){
  if (document.hidden) return;
  try {
    const r = await fetch('/', {cache:'no-store'});
    const txt = await r.text();
    const doc = new DOMParser().parseFromString(txt, 'text/html');
    // код доски сменился (CSS/JS) → innerHTML-своп доставил бы разметку БЕЗ её
    // стилей и обработчиков (решение 14.07: свежие табы отрисовались голыми
    // кнопками в давно открытой вкладке) — единственный честный путь: один
    // полный reload на смену ревизии. Проверка стоит ДО разбора T: старая
    // вкладка должна перезагрузиться, даже если новый код сменил форму T
    // (paint 16.07: вкладка с добитым кодом рисовала мимо курсора).
    // штамп «данные от HH:MM:SS» — время ЭТОГО ответа сервера, то есть момент,
    // когда стол последний раз реально освежился. Ставим его до всех разборов
    // ниже: даже если своп не случится, цифра в углу останется честной
    const nst = doc.getElementById('rstamp'), ost = document.getElementById('rstamp');
    if (nst && ost) ost.textContent = nst.textContent;
    const nrev = doc.querySelector('meta[name="board-rev"]');
    const orev = document.querySelector('meta[name="board-rev"]');
    if (nrev && orev && nrev.content !== orev.content) {
      // недорисованное не тонет: штрихи + голова undo + имя страницы
      // уезжают в sessionStorage и оживают после перезагрузки (pagesInit)
      if (window.__pgDirty && window.pgStash) {
        try{ sessionStorage.setItem('pg-stash', pgStash()); }catch(e){}
      }
      location.reload(); return;
    }
    const m = txt.match(new RegExp('const T = (\\\\{[\\\\s\\\\S]*?\\\\});\\n'));
    if (!m) return;
    const nt = JSON.parse(m[1]);
    // TP — панели «работы» (своя у нити) и «issues» (копия на дом). Их
    // содержимое в паспорт нити не входит, поэтому сравнение struct ниже про
    // них ничего не знает: закрытый пункт работы не долетал бы до открытой
    // страницы до перезагрузки. Сменилась карта — перерисовываем деталь.
    // Сравниваем СЫРОЙ текст с сырым (window.__tpRaw), а не с JSON.stringify
    // живого объекта: пробелы Python и JS в сериализации разные, и такое
    // сравнение всегда врало бы «изменилось» — деталь мигала бы каждые 5 с.
    const mp = txt.match(new RegExp('const TP = (\\\\{[\\\\s\\\\S]*?\\\\});\\n'));
    let tpMoved = false;
    if (mp && mp[1] !== window.__tpRaw) {
      // первый опрос только запоминает слепок: страница уже отрисована этой же
      // картой, и перерисовывать деталь на ровном месте не за чем
      tpMoved = window.__tpRaw !== undefined;
      window.__tpRaw = mp[1]; window.TP = JSON.parse(mp[1]);
    }
    const nf = doc.getElementById('focus'), f = document.getElementById('focus');
    if (nf && f && nf.innerHTML !== f.innerHTML) { f.innerHTML = nf.innerHTML; bind(); }
    const na = doc.getElementById('all'), a = document.getElementById('all');
    if (na && a && na.innerHTML !== a.innerHTML) { a.innerHTML = na.innerHTML; bind(); }
    // новости: не свопаем, пока человек печатает в форме — иначе ввод пропадёт
    const nw = doc.getElementById('news'), w = document.getElementById('news');
    const wi = w && w.querySelector('#nurl');
    const typing = wi && (wi.value || document.activeElement === wi);
    if (nw && w && !typing && nw.innerHTML !== w.innerHTML) { w.innerHTML = nw.innerHTML; }
    // работа: не свопаем, пока в форме печатают
    const nwk = doc.getElementById('work'), wk = document.getElementById('work');
    const wt = wk && wk.querySelector('#wk-t');
    const wkTyping = wt && (wt.value || document.activeElement === wt);
    if (nwk && wk && !wkTyping && !window.__wkEditing && nwk.innerHTML !== wk.innerHTML) { wk.innerHTML = nwk.innerHTML; }
    // стол ISSUES: свопается так же, как работа; счётчик живёт в заголовке
    // вкладки (вне панели) — освежаем его отдельно, иначе врёт до перезагрузки
    const nis = doc.getElementById('issues'), is = document.getElementById('issues');
    // раскрытая строка очереди пишет в разметку open="" — сравнение его не
    // видит (иначе своп шёл бы каждые 5 с просто потому, что человек развернул
    // строку), а после свопа раскрытые возвращаем на место по data-row
    const isn = h => h.replace(/ open=""/g, '');
    if (nis && is && !window.__wkEditing && isn(nis.innerHTML) !== isn(is.innerHTML)) {
      const op = new Set([...is.querySelectorAll('.isrow[open]')].map(x=>x.dataset.row));
      is.innerHTML = nis.innerHTML;
      if (op.size) is.querySelectorAll('.isrow').forEach(x=>{ if(op.has(x.dataset.row)) x.open = true; });
    }
    // СЧЁТЧИК ПИШЕМ В ПОДПИСЬ, А НЕ В КНОПКУ (работа 36): в кнопке рядом со
    // словом живёт иконка вкладки, и присваивание textContent всей кнопке
    // стирало бы её при каждом изменении числа. Фолбэк на кнопку — для
    // страницы, отданной старой сборкой, где узла .vtl ещё нет
    const q = r => r.querySelector('.vtab[data-v="issues"] .vtl')
                || r.querySelector('.vtab[data-v="issues"]');
    const nit = q(doc), oit = q(document);
    if (nit && oit && nit.textContent !== oit.textContent) oit.textContent = nit.textContent;
    // доска: холст — проекция штрихов в памяти, своп ему не страшен;
    // держим только живое касание (__pgTouching), чтобы не рвать штрих —
    // иначе сетка карточек «замерзала», пока рисунок не сохранён
    const npg = doc.getElementById('pages'), pg = document.getElementById('pages');
    if (npg && pg && !window.__pgTouching && npg.innerHTML !== pg.innerHTML) {
      pg.innerHTML = npg.innerHTML;
      if (!pg.hidden && window.pagesInit) setTimeout(pagesInit, 0);
    }
    // фильтр-селект живёт в шапке (вне #focus) — освежаем опции при обновлении,
    // выбор сохраняем (applyPtab выставит value=ptabSel); затем перефильтруем
    const npf = doc.getElementById('pfilter'), opf = document.getElementById('pfilter');
    if (npf && opf && npf.innerHTML !== opf.innerHTML) { opf.innerHTML = npf.innerHTML; }
    if (typeof applyPtab === 'function') applyPtab();
    const oid = window.__openId;
    // на вкладке «работы» страницы нити стоит форма «завести работу»: пока в
    // неё печатают, деталь не перерисовываем — своп унёс бы набранное (тот же
    // сторож, что у форм на вкладках «работа» и «новости» выше)
    const dnw = document.querySelector('.detail .wkform .wknew');
    const dTyping = dnw && (dnw.value || document.activeElement === dnw);
    // долг на перерисовку копим флагом: смену карты нельзя просто «пропустить»
    // из-за печати, иначе панель останется старой до следующей смены
    if (tpMoved) window.__tpDirty = true;
    const changed = oid && nt[oid] && T[oid] && !dTyping
      && (T[oid].struct !== nt[oid].struct || window.__tpDirty);
    if (changed) window.__tpDirty = false;   // долг закрыт (или закроется ниже)
    Object.keys(T).forEach(k=>delete T[k]); Object.assign(T, nt);
    const d = document.getElementById('detail');
    if (changed && d.classList.contains('open')) {
      const st = d.scrollTop;
      const open = [...d.querySelectorAll('details')].map(x=>x.open);
      openT(oid);
      [...d.querySelectorAll('details')].forEach((x,i)=>{ if(open[i]!==undefined) x.open = open[i]; });
      d.scrollTop = st;
    }
  } catch(e){}
}
setInterval(boardRefresh, 5000);
// ВОЗВРАТ К ВКЛАДКЕ — отдельный случай, и главный телефонный (кандидат 165:
// 31.07 стол на телефоне звал принять работу, закрытую минутой раньше). Пока
// страница в фоне, интервал выше не работает дважды: сам он молчит по
// document.hidden, а телефон вдобавок замораживает таймеры целиком — и человек
// возвращается к столу, который не дышал час. Поэтому свежее спрашиваем СРАЗУ
// на возврат, не дожидаясь тика: visibilitychange — переключение вкладки,
// pageshow — вкладку могли достать из bfcache готовой страницей, focus — окно
// подняли поверх. Все три прилетают на один жест, поэтому один порог в секунду
// на всех; он висит ЗДЕСЬ, а не внутри boardRefresh — тот зовут после кликов,
// и глотать обновление после жеста человека нельзя.
let _wakeAt = 0;
function boardWake(){
  const t = Date.now();
  if (t - _wakeAt < 1000) return;
  _wakeAt = t;
  boardRefresh();
}
document.addEventListener('visibilitychange', function(){
  if (!document.hidden) boardWake();
});
window.addEventListener('pageshow', boardWake);
window.addEventListener('focus', boardWake);"""


RSTAMP_CSS = """
/* «данные от HH:MM:SS» в углу (кандидат 165). Автообновление бывает мёртвым —
   заснул телефон, упала служба, порвалась сеть, — и молчащий стол выглядит
   ровно как живой. Одна цифра делает протухшее видимым глазом: разошлась с
   часами на телефоне — значит стол врёт, обнови руками. Инфостроку с экрана
   сняли 30.07 («убрать вообще»), и это не она: не в потоке, строки не ест,
   pointer-events:none — палец её не поймает. */
#rstamp{ position:fixed; right:9px; bottom:6px; z-index:40; pointer-events:none;
  font-family:var(--mono); font-size:10px; letter-spacing:.04em;
  color:var(--ink-faint); opacity:.7; }
@media (max-width:700px){
  #rstamp{ right:6px; bottom:4px; font-size:9px; }
  /* низ телефона на вкладке страниц занят панелью редактора — она фиксированная
     и в зоне большого пальца; штамп сел бы поверх инструментов, поэтому там он
     молчит. Стол и работы, ради которых он и заведён, угол не занимают. */
  body:has(#pages:not([hidden])) #rstamp{ display:none; }
}
"""


PALETTE_JS = """
// палитра переживает перезагрузку (решение 11.07): выбор — в localStorage
(function(){
  const pal=document.getElementById('pal'); if(!pal) return;
  const saved=localStorage.getItem('board-palette');
  if(saved){ document.documentElement.dataset.palette=saved; pal.value=saved; }
  pal.addEventListener('change',e=>localStorage.setItem('board-palette',e.target.value));
})();"""


ZOOM_JS = """
// масштаб Cmd+= / Cmd+- / Cmd+0 — как в браузере (решение 16.07): оболочка
// Board.app браузерных хоткеев не даёт; множитель живёт в localStorage
// (board-zoom) и общий у доски с читалкой статей
(function(){
  let z=1;
  try{ z=parseFloat(localStorage.getItem('board-zoom'))||1; }catch(e){}
  function apply(){ document.body.style.zoom=z; }
  function set(nz){ z=Math.min(2,Math.max(.5,Math.round(nz*10)/10));
    try{ localStorage.setItem('board-zoom',z); }catch(e){} apply(); }
  document.addEventListener('keydown',e=>{
    if(!(e.metaKey||e.ctrlKey)) return;
    if(e.key==='='||e.key==='+'){ e.preventDefault(); set(z+0.1); }
    else if(e.key==='-'){ e.preventDefault(); set(z-0.1); }
    else if(e.key==='0'){ e.preventDefault(); set(1); }
  });
  apply();
})();"""


SHELL_HOTKEYS_JS = """
// хоткеи оболочки работают и ИЗ iframe: контент просит через ShellKit (deck-v1)
document.addEventListener('keydown',e=>{
  if(!(e.metaKey||e.ctrlKey)) return;
  if(e.code==='KeyR'){ e.preventDefault(); window.ShellKit?ShellKit.reload():location.reload(); }
  if(e.code==='KeyF'){ e.preventDefault(); if(window.ShellKit) ShellKit.fullscreen(); }
});"""


class BoardTab(NamedTuple):
    """Одна вкладка доски — строка реестра, а не гвоздь в разметке (работа 48).

    ``key``     значение data-v: и кнопка, и id панели, и ключ в localStorage
    ``plugin``  имя части в `tide.plugins`; "" — кор, выключить нельзя
    ``label``   слово на кнопке
    ``icon``    иконка из кита дома (ICON_*)
    ``attrs``   доп. атрибуты кнопки (подсказка про хоткей)
    ``panel``   функция-сборщик панели, или None — панель приезжает из
                ШАБЛОНА (#focus и #all живут в scope/index.html и заполняются
                регуляркой в main(); это кор и вынимать их некуда)
    """

    key: str
    plugin: str
    label: str
    icon: str
    attrs: str = ""
    panel: object = None


def board_tabs():
    """РЕЕСТР ВКЛАДОК — одно место, где сказано, из чего состоит ряд.

    Порядок списка = порядок ряда на экране (решение 01.08, работа 36: нити
    первыми). Кор и плагины идут одним и тем же путём НАРОЧНО: заведи вкладке
    плагина отдельную дорожку — и через месяц у доски будет два способа
    нарисовать вкладку, которые разъедутся на первой же правке шапки.

    Код панелей при этом никуда не переезжал: `panel` — это та самая функция,
    которая как лежала в этом файле, так и лежит (список собирается на рендере,
    поэтому имена ниже по файлу к тому моменту уже есть). Плагинность тут ровно
    одна — вкладка ПОПАДАЕТ на доску из списка, а не из жёсткой разметки."""
    return (
        BoardTab("focus", "", "нити", ICON_THREAD,
                 " title=\"M — переключить\"", None),
        BoardTab("issues", "issues", "issues", ICON_INBOX, "", _issues_panel),
        BoardTab("work", "work", "работа", ICON_WORKSHEET, "", _works_panel),
        BoardTab("all", "", "проекты", ICON_PROJECT,
                 " title=\"M — переключить\"", None),
        BoardTab("news", "news", "новости", ICON_FEED, "", _news_panel),
        BoardTab("pages", "pages", "доска", ICON_PENCIL, "", _pages_panel),
        BoardTab("skills", "skills", "навыки", ICON_SKILL, "", _skills_panel),
    )


# Порядок ЦИФР 1–7 — не порядок ряда (сложилось так: ряд переехал на «нити
# первыми», цифры остались). Трогать его нельзя — это пальцы человека; но
# отфильтровать по включённому нужно, иначе цифра вела бы на пустоту.
KEY_ORDER = ("issues", "work", "focus", "all", "news", "pages", "skills")
# Порядок восстановления вкладки из localStorage — тоже свой, историчный.
VIEW_ORDER = ("issues", "focus", "all", "news", "pages", "work", "skills")


def enabled_tabs():
    """Вкладки, которые этот человек видит: кор + невыключенные плагины.

    Кор (`plugin == ""`) не спрашивают вовсе: строка `board = off` в реестре
    не имеет права оставить человека с пустым экраном."""
    off = _plugins_off()
    return [t for t in board_tabs() if not t.plugin or t.plugin not in off]


def _tabs_html(tabs, n_issues=0):
    """Ряд вкладок. ISSUES — первая и открытая по умолчанию (решение 30.07):
    вход на доску = свой стол, кухня работ рядом соседней вкладкой. Счётчик
    в заголовке говорит, сколько ждёт руки; ноль числа не рисует — пустой
    стол не должен мозолить глаз нулём.

    Обёртка `.vnav` и кнопка ☰ (решение 30.07: «на мобилке — в бургер») нужны
    только узкому экрану: на десктопе бургер display:none и ряд остаётся тем
    же флексом, что был. Список вкладок ОДИН — на ≤700px он же и становится
    выпадающим меню (HEADNAV_CSS), поэтому текущая помечена одним классом
    `.on` и там и там, а рассинхрону двух копий взяться неоткуда.

    ИКОНКА СЛЕВА У КАЖДОЙ (решение 01.08, работа 36) — из кита дома, см. ICON_*
    выше. Слово вкладки живёт ОТДЕЛЬНЫМ узлом `.vtl`, и это не украшение
    разметки: счётчик issues автообновление вписывает прямо в кнопку, и по
    старому `textContent = …` иконка стиралась бы при каждом изменении числа
    (см. AUTOREFRESH_JS). Подпись бургера берётся с активной вкладки
    textContent'ом и остаётся словом: у svg текстовых узлов нет."""
    n = " · {0}".format(n_issues) if n_issues else ""
    # НИТИ ПЕРВЫМИ (решение 01.08, работа 36): порядок ряда — его, а не наш; он
    # теперь задан порядком реестра (board_tabs), а не этим литералом.
    # Вход на доску при этом НЕ переехал: открывается по-прежнему issues (её
    # панель единственная не hidden в разметке), и потому `.on` метится ПО КЛЮЧУ,
    # а не по первому месту в списке. Разъедься эти две вещи — ряд подсветил бы
    # одну вкладку, а показал другую. Ключ берём у default_view(): выключи
    # человек issues — открытой станет первая, что осталась.
    dv = default_view(tabs)
    return ('<div class="vnav">'
            '<button class="vburger" type="button" aria-expanded="false"'
            ' aria-label="вкладки">☰<span class="vbcur"></span></button>'
            '<div class="vtabs" role="tablist">{0}</div></div>'.format(
                "".join(
                    '<button class="vtab{on}" data-v="{v}"{t}>'
                    '<span class="vic" aria-hidden="true">{i}</span>'
                    '<span class="vtl">{l}</span></button>'.format(
                        on=" on" if t.key == dv else "", v=t.key, t=t.attrs,
                        i=t.icon,
                        l=(t.label + n if t.key == "issues" else t.label))
                    for t in tabs)))


def default_view(tabs):
    """Какая вкладка открыта при входе, если человек ещё ничего не выбирал.

    Обычно issues (решение 30.07: вход на доску = свой стол). Выключен issues —
    первая оставшаяся: экран без единой видимой панели был бы хуже любого
    «неправильного» вида."""
    keys = [t.key for t in tabs]
    return "issues" if "issues" in keys else (keys[0] if keys else "focus")


# ── вкладка «навыки» (решение 17.07): все скиллы — глобальные и по проектам ──
# Русло — файлы: скилл = <dir>/SKILL.md с YAML-шапкой (name/description).
# Пока ТОЛЬКО отображение (read-only): никакой правки/установки с доски.


def _skill_meta(skill_md):
    """(имя, описание, tagline, summary) из YAML-шапки SKILL.md.

    tagline/summary — витринные EN-поля единого формата (решение 17.07: «кратко и
    понятно, заголовок на английском»); description — рабочее поле триггеров,
    остаётся фолбэком для скиллов, ещё не описанных по формату."""
    try:
        text = skill_md.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return "", "", "", ""
    name = desc = tagline = summary = ""
    m = re.match(r"\s*---\s*\n(.*?)\n---\s*\n?", text, re.S)
    if m:
        fm = m.group(1)

        def _field(key, multiline=False):
            flags = re.M | (re.S if multiline else 0)
            pat = (r"^{0}:\s*(.+?)(?=^\w[\w-]*:|\Z)" if multiline
                   else r"^{0}:\s*(.+)$").format(key)
            fmatch = re.search(pat, fm, flags)
            return " ".join(fmatch.group(1).split()).strip().strip("'\"") if fmatch else ""

        name = _field("name")
        desc = _field("description", multiline=True)
        tagline = _field("tagline")
        summary = _field("summary")
        text = text[m.end():]
    if not name:
        hm = re.search(r"^#\s+(.+)$", text, re.M)
        name = hm.group(1).strip() if hm else ""
    if not desc:
        para = next((p.strip() for p in text.split("\n\n")
                     if p.strip() and not p.strip().startswith("#")), "")
        desc = " ".join(para.split())
    return name, desc, tagline, summary


def _read_skill_dir(base):
    """Скиллы каталога *base*: [{slug, name, desc, linked}] по алфавиту."""
    out = []
    if not base.is_dir():
        return out
    for d in sorted(base.iterdir()):
        if not (d / "SKILL.md").is_file():
            continue
        name, desc, tagline, summary = _skill_meta(d / "SKILL.md")
        out.append({"slug": d.name, "name": name or d.name, "desc": desc,
                    "tagline": tagline, "summary": summary,
                    "linked": d.is_symlink()})
    return out


# «наш стек» среди глобальных (решение 17.07: «разделить — стек и остальные»).
# Автоматом: симлинк в чекаут tide + слаг tide-*; остальное — руками, список
# правится словами через агента (текстовая эвристика тащила figma по слову
# «доска» — грязно).
STACK_SKILL_SLUGS = {
    "offload", "handoff", "canon", "canon-deck", "make-contract",
    "plan-approve", "post-to-board", "tick", "regatta", "reflect", "orca-ops",
}


def _is_stack_skill(base, s):
    d = base / s["slug"]
    if s["slug"].startswith("tide"):
        return True
    if s["slug"] in STACK_SKILL_SLUGS:
        return True
    try:
        return d.is_symlink() and "/projects/tide/" in str(d.resolve())
    except OSError:
        return False


def read_skills():
    """[(scope, skills)] — глобальные, разрезанные на «стек tide» и «остальные»
    (решение 17.07), + каждый проект ростера с .claude/skills. Пустые скоупы
    не показываем."""
    out = []
    glob_dir = Path.home() / ".claude" / "skills"
    g = _read_skill_dir(glob_dir)
    stack = [s for s in g if _is_stack_skill(glob_dir, s)]
    rest = [s for s in g if s not in stack]
    if stack:
        out.append(("стек tide", stack))
    if rest:
        out.append(("остальные", rest))
    for name, path in roster_projects():
        root = Path(path)
        s = _read_skill_dir(root / ".claude" / "skills")
        if s:
            out.append((name, s))
        # скиллы вложенных репо (решение 17.07: «почему не видно скиллов вложенного репо?» —
        # они в <проект>/<вложенное-репо>/.claude/skills): один уровень вглубь, свой скоуп
        try:
            subs = sorted(d for d in root.iterdir()
                          if d.is_dir() and not d.name.startswith("."))
        except OSError:
            subs = []
        for sub in subs:
            nested = _read_skill_dir(sub / ".claude" / "skills")
            if nested:
                out.append(("{0}/{1}".format(name, sub.name), nested))
    return out


# счётчик вызовов скиллов (решение 17.07: «нет уверенности, что агенты
# правильно используют») — вызовы Skill-тула из транскриптов за 14 дней.
# Скан тяжёлый (тысячи jsonl) → кэш в build/, пересчёт ФОНОМ раз в 6 часов;
# рендер никогда не ждёт. Замер 17.07 уже похоронил четырёх нулевиков
# (plan-approve/tick/reflect/regatta → .skills-archive).
_SKILL_USAGE_CACHE = _cache_dir() / "skill-usage.json"
_SKILL_USAGE_TTL = 6 * 3600
_SKILL_USAGE_DAYS = 14


def _count_skill_usage_main():
    """--count-skill-usage: пересчитать кэш (зовётся детачнутым сабпроцессом)."""
    import glob as _glob
    import time as _time
    cutoff = _time.time() - _SKILL_USAGE_DAYS * 86400
    counts = {}
    pat = re.compile(r'"skill":\s*"([a-z0-9_-]+)"')
    for f in _glob.glob(str(Path.home() / ".claude" / "projects" / "*" / "*.jsonl")):
        try:
            if os.path.getmtime(f) < cutoff:
                continue
            with open(f, encoding="utf-8", errors="ignore") as fh:
                for ln in fh:
                    if '"Skill"' not in ln or '"skill"' not in ln:
                        continue
                    for m in pat.finditer(ln):
                        counts[m.group(1)] = counts.get(m.group(1), 0) + 1
        except OSError:
            continue
    _SKILL_USAGE_CACHE.parent.mkdir(parents=True, exist_ok=True)
    _SKILL_USAGE_CACHE.write_text(
        json.dumps({"ts": _time.time(), "counts": counts}), encoding="utf-8")


def _skill_usage():
    """{slug: вызовы за 14 дней} из кэша; протух → фоновый пересчёт, отдаём старое."""
    import subprocess as _sp
    import sys as _sys
    import time as _time
    data = {}
    try:
        data = json.loads(_SKILL_USAGE_CACHE.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    stale = (_time.time() - data.get("ts", 0)) > _SKILL_USAGE_TTL
    if stale:
        lock = _SKILL_USAGE_CACHE.with_suffix(".lock")
        try:
            fresh_lock = lock.exists() and (_time.time() - lock.stat().st_mtime) < 600
            if not fresh_lock:
                lock.parent.mkdir(parents=True, exist_ok=True)
                lock.write_text("counting", encoding="utf-8")
                _sp.Popen([_sys.executable, str(Path(__file__)), "--count-skill-usage"],
                          start_new_session=True,
                          stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
        except Exception:
            pass
    return data.get("counts", {})


def _skills_panel():
    """Вкладка «навыки»: секция на скоуп, строка-аккордеон на скилл —
    [имя · чип «станок» · tagline · вызовы за 14д · шеврон], описание в панели."""
    usage = _skill_usage()
    sections = []
    total = 0
    for scope, skills in read_skills():
        rows = []
        for s in skills:
            total += 1
            chip = ('<span class="skchip" title="симлинк — доезжает со станка '
                    '(tide install-skills)">станок</span>' if s["linked"] else "")
            # строка = EN-tagline единого формата; разворот = короткий EN-summary.
            # Скилл без витринных полей честно падает на description (и виден
            # как неописанный — кандидат на прогон формата)
            line = s["tagline"] or (s["desc"][:160] + ("…" if len(s["desc"]) > 160 else ""))
            body = s["summary"] or s["desc"] or "описания нет — SKILL.md без шапки"
            n = usage.get(s["slug"], 0)
            use = ('<span class="skuse{0}" title="вызовы Skill-тулом за {1} дней">'
                   '{2}</span>'.format(" zero" if not n else "",
                                       _SKILL_USAGE_DAYS, n))
            rows.append(
                '<details class="skrow"><summary><span class="sknm">{0}</span>{1}'
                '<span class="sksub">{2}</span>{5}{3}</summary>'
                '<div class="skbody">{4}</div></details>'.format(
                    esc(s["slug"]), chip, esc(line), CHEVRON, esc(body), use))
        sections.append('<div class="slabel">{0} · {1}</div>{2}'.format(
            esc(scope), len(skills), "".join(rows)))
    body = ("".join(sections) if sections else
            '<p style="color:var(--ink-faint)">скиллов не найдено</p>')
    return '<div id="skills" hidden>{0}</div>'.format(body)


SKILLS_CSS = """
#skills{margin-top:22px}
#skills .slabel{margin-top:26px}
.skrow{border-bottom:1px solid var(--line)}
.skrow summary{display:flex;align-items:baseline;gap:10px;list-style:none;
  cursor:pointer;padding:9px 2px;min-width:0}
.skrow summary::-webkit-details-marker{display:none}
.sknm{font-family:var(--mono);font-size:12.5px;color:var(--ink);flex:none}
.skchip{font-family:var(--mono);font-size:9.5px;letter-spacing:.08em;
  padding:1px 7px;border:1px solid var(--line-2);border-radius:10px;
  color:var(--ink-faint);flex:none}
.sksub{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
  font-size:12px;color:var(--ink-faint);min-width:0}
.skrow[open] .sksub{visibility:hidden}
.skbody{padding:2px 2px 12px;font-size:12.5px;color:var(--ink-dim);
  max-width:72ch;line-height:1.55}
/* вызовы за 14 дней — тихая цифра справа; ноль виден (мёртвое не прячется) */
.skuse{flex:none;font-family:var(--mono);font-size:10px;color:var(--ink-faint);
  min-width:22px;text-align:right}
.skuse.zero{color:var(--ink-mute)}
/* заметки проекта на полке — тело блоками: проза абзацами, код отдельными
   карточками со своей ⧉ (решение 17.07) */
.ntrow summary .sbtn{flex:none}
/* длинный заголовок УЖИМАЕТСЯ с эллипсисом, а не выталкивает чипы и кнопки
   за край (решение 17.07: «по горизонтали поехала вёрстка») */
.ntrow .sknm{flex:0 1 auto;min-width:0;overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap}
.ntrow .sksub{flex:1 1 0}
/* правка как у работ: клик по тексту, подсказка пунктиром (без карандашей) */
.ntrow .sknm,.ntbody .ntp{cursor:text}
.ntrow .sknm:hover,.ntbody .ntp:hover{
  text-decoration:underline dashed var(--ink-mute);text-underline-offset:3px}
.ntta{width:100%;min-height:180px;resize:vertical;font-family:var(--mono);
  font-size:11.5px;line-height:1.55;color:var(--ink);background:var(--bg-1);
  border:1px dashed var(--ink-mute);border-radius:6px;padding:11px 13px}
.nthint{font-family:var(--mono);font-size:10px;color:var(--ink-faint);
  padding:4px 2px 10px}
/* журнал заметки — тихая история под телом (кто когда правил) */
.ntjs{margin-top:2px;padding-top:8px;border-top:1px solid var(--line)}
.ntj{font-family:var(--mono);font-size:10px;color:var(--ink-faint);
  padding:1px 0}
.ntbody{max-width:76ch}
.ntp{margin:0 0 10px;line-height:1.6}
/* код в теле заметки — ТИПОВОЙ блок (.cblk, CODE_CSS); заметке принадлежит
   только отступ между блоками, вид кода общий с артефактом на столе */
.ntbody .cblk{margin:0 0 12px}
"""


# ── вкладка «новости» (заготовка, решение 16.07, нить news-and-threads) ──
# Русло — файлы: статья = news/*.md (первая строка `# заголовок`, мета —
# строки `source:`/`date:`), очередь на разбор = news/inbox.urls (пишет
# форма через serve_live /news-add). Вода — эта проекция; конвейер
# ссылка → транскрипт → статья приедет следующим шагом нити.
# press — отдельный проект (решение 17.07): разборы + talks выехали из
# tide-stack в свой дом; доска их лишь проецирует. Путь переопределяется
# env NEWS_ROOT (на случай переезда проекта).
NEWS_DIR = Path(os.environ.get(
    "NEWS_ROOT", str(Path.home() / "Documents" / "projects" / "press")))
NEWS_SITE_URL = "https://tide-news.vercel.app"  # витрина базы (шаг 7 нити 17)

def _news_favs():
    """Избранное — слаги в news/favorites.txt (пишет ★ через /news-fav)."""
    p = NEWS_DIR / "favorites.txt"
    if not p.exists():
        return set()
    return {l.strip() for l in p.read_text(encoding="utf-8").splitlines()
            if l.strip()}


def _news_fresh_key(f):
    """Свежие сверху (решение 16.07): дата из имени, внутри дня — mtime;
    голый sort по имени тасовал одноднёвки по алфавиту слага."""
    m = re.match(r"(\d{4}-\d{2}-\d{2})", f.name)
    return ((m.group(1) if m else ""), f.stat().st_mtime)


def _news_cards():
    """Карточки статей из news/*.md.

    Статей нет — вкладка пуста, и это правда. Раньше пустоту закрывали двумя
    выдуманными карточками-образцами: они показывали форму будущего вида, но
    человеку, открывшему вкладку впервые, врали, что у него что-то есть.
    Возвращает (карточки-избранные, карточки-остальные) готовым html."""
    favs = _news_favs()
    fav_cards, cards = [], []
    for f in (sorted(NEWS_DIR.glob("*.md"), key=_news_fresh_key, reverse=True)
              if NEWS_DIR.is_dir() else []):
        if f.name.lower() == "readme.md":
            continue
        head = f.read_text(encoding="utf-8")[:2000].splitlines()
        title = next((l[2:].strip() for l in head if l.startswith("# ")), f.stem)
        meta = dict(l.split(":", 1) for l in head
                    if re.match(r"(source|date|site):", l))
        src = (meta.get("source") or "").strip()
        host = re.sub(r"^www\.", "", re.sub(r"^https?://", "", src).split("/")[0])
        sub = " · ".join(x for x in (host, (meta.get("date") or "").strip()) if x)
        on = f.stem in favs
        site = (meta.get("site") or "").strip()
        lnk = ('<span class="nlnk" data-u="{0}" '
               'title="скопировать ссылку на сайт">⧉</span>'
               .format(esc(site)) if site else "")
        card = ('<a class="newscard" href="/article?f={2}">'
                '<span class="nfav{3}" data-f="{2}" title="в избранное">★</span>'
                '{4}<span class="nm">{0}</span>'
                '<span class="nsub">{1}</span></a>'
                .format(esc(title), esc(sub), esc(f.stem),
                        " on" if on else "", lnk))
        (fav_cards if on else cards).append(card)
    return "".join(fav_cards), "".join(cards), len(fav_cards), len(cards)


def _news_processing():
    """Живая стадия конвейера process.py: status-файл честен, только пока
    жив process.lock (pid); лок мёртв — статус пуст, кнопка возвращается."""
    try:
        os.kill(int((NEWS_DIR / "process.lock").read_text().strip()), 0)
    except Exception:
        return {}
    try:
        return json.loads((NEWS_DIR / "process.status.json")
                          .read_text(encoding="utf-8"))
    except Exception:
        return {}


def _news_queue():
    """Очередь inbox.urls — свежие сверху; у строки — что за видео
    (название/канал/длительность из inbox.meta.json, тянет process.py
    enrich), во время разбора — живая стадия. Пусто → секции нет."""
    inbox = NEWS_DIR / "inbox.urls"
    if not inbox.exists():
        return 0, "", {}
    urls = [u.strip() for u in inbox.read_text(encoding="utf-8").splitlines()
            if u.strip()]
    try:
        meta = json.loads((NEWS_DIR / "inbox.meta.json")
                          .read_text(encoding="utf-8"))
    except Exception:
        meta = {}
    st = _news_processing()
    rows = []
    for u in reversed(urls):
        m = meta.get(u) or {}
        host = re.sub(r"^www\.", "", re.sub(r"^https?://", "", u).split("/")[0])
        mins = int(m.get("duration") or 0) // 60
        sub = " · ".join(x for x in
                         (host, "{0} мин".format(mins) if mins else "",
                          m.get("channel", "")) if x)
        line = ('<span class="nqt">{0}</span><span class="nqs">{1}</span>'
                '<span class="nqu">{2}</span>'
                .format(esc(m["title"]), esc(sub), esc(u))
                if m.get("title") else esc(u))
        if st.get("url") == u:
            line += ('<span class="nqst">⏳ {0}…</span>'
                     .format(esc(st.get("stage", "разбирается"))))
        elif m.get("error"):
            line += '<span class="nqerr">✗ {0}</span>'.format(esc(m["error"]))
        rows.append('<div class="newsq">{0}</div>'.format(line))
    return len(urls), "".join(rows), st


def _news_panel():
    fav_cards, cards, nfav, n = _news_cards()
    qn, queue, st = _news_queue()
    parts = [
        '<div id="news" hidden>',
        '<div class="nform">'
        '<input class="nurl" id="nurl" type="url" spellcheck="false" '
        'placeholder="youtube или github — вкинуть ссылку">'
        '<button class="nbtn" id="nadd">в очередь</button>'
        '<a class="nsite" href="{0}/" title="открыть сайт новостей">'
        'сайт ↗</a></div>'.format(esc(NEWS_SITE_URL)),
    ]
    if qn:
        act = ('<span class="nqst">⏳ разбирается…</span>' if st else
               '<button class="nbtn nproc" id="nproc">разобрать</button>')
        parts += ['<div class="slabel qrow">в очереди на разбор · {0}{1}</div>'
                  .format(qn, act),
                  queue]
    if nfav:
        parts += ['<div class="slabel">избранное · {0}</div>'.format(nfav),
                  '<div class="newsgrid">{0}</div>'.format(fav_cards)]
    label = ("статьи · {0}".format(n) if n
             else ("" if nfav else "статей пока нет — образцы вида"))
    if cards:
        parts += ['<div class="slabel">{0}</div>'.format(label) if label else "",
                  '<div class="newsgrid">{0}</div>'.format(cards)]
    parts += ['</div>']
    return "".join(parts)


# ── вкладка «работа» (решение 16.07): работа = АРКА (.tide/arcs/works/NN-slug/
# work.md) — сущность, которую человек согласует с агентом. Карточка =
# чеклист (мин. 1 пункт) + дедлайн + проект; тело — свободный текст
# (задача или проблема, без типов). Заготовка: показывается только тут.
def _legacy_works_dir():
    """Общая папка работ дома-верфи — та, что лежит рядом с кодом доски.

    Историческое место. Пока доска жила внутри репозитория, работы ВСЕХ
    проектов складывались сюда, а к какому дому относится работа, говорило её
    поле `project:`. Шестьдесят пять владельца работ лежат так до сих пор, и
    ломать их адресацию нельзя. У кода, переехавшего в пакет, такой папки
    рядом нет — и общей свалки не существует вовсе: каждая работа живёт в
    своём проекте, как её и кладёт движок.
    """
    d = Path(__file__).resolve().parent.parent / ".tide" / "arcs" / "works"
    return d if d.is_dir() else None


def works_sources():
    """Откуда доска берёт работы: [(дом, папка работ)] по всему ростеру.

    Движок кладёт работу туда, где сидела сессия (`<проект>/.tide/arcs/works`),
    а доска смотрела в ОДНУ папку — ту, что рядом с её кодом. Пока работы
    заводились из дома-верфи, это совпадало; как только они пошли из соседнего проекта,
    одиннадцать штук стали невидимы (кандидат 187: на доске восемь работ
    одного дома, `tide work list` из самого этого дома даёт больше). Ходим по
    ростеру — тому же списку, по которому собираются нити, чтобы у доски был
    один ответ на вопрос «какие проекты я показываю».

    Дом-верфь идёт ПЕРВЫМ и с пустым именем: работы в нём адресуются полем
    `project:`, а не местом, и это единственный источник, где так. Остальные
    отвечают за себя сами — работа в `<проект>/.tide/arcs/works` принадлежит
    этому проекту, что бы ни было написано у неё в паспорте.

    `$TIDE_WORKS` — аварийный съезд: назови одну папку, и доска будет смотреть
    только в неё.
    """
    env = (os.environ.get("TIDE_WORKS") or "").strip()
    if env:
        return [("", Path(env).expanduser())]
    out, seen = [], set()
    legacy = _legacy_works_dir()
    if legacy:
        out.append(("", legacy))
        seen.add(str(legacy.resolve()))
    for name, path in roster_projects():
        d = Path(path) / ".tide" / "arcs" / "works"
        key = str(d.resolve()) if d.exists() else str(d)
        if key in seen:
            continue
        seen.add(key)
        out.append((name, d))
    return out


def work_files():
    """[(дом-подсказка, файл work.md)] со всех источников.

    Подсказка пустая — работа из общей папки, её дом сказан полем `project:`
    (см. `_work_home`). Иначе дом — имя проекта, в котором она лежит.
    """
    out = []
    for hint, d in works_sources():
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*/work.md")):
            out.append((hint, f))
    return out


def _work_home(hint, meta):
    """Чей это дом: МЕСТО работы, а для общей папки — её поле `project:`."""
    return hint or (meta.get("project") or "").strip()


def _work_key(hint, slug):
    """Ключ работы — им адресуются все ручки доски и метится карточка.

    У работ общей папки ключ ГОЛЫЙ, ровно как был: у владельца их шестьдесят пять,
    и смена ключа увела бы и адреса кнопок, и раскрытое состояние карточек.
    У работы, лежащей в своём проекте, ключ — «дом/слаг»: номера у проектов
    свои, `01-…` есть у каждого, и без дома доска правила бы одну работу, а
    показывала другую.
    """
    return "{0}/{1}".format(hint, slug) if hint else slug


WORKS_DIR = _legacy_works_dir() or (HOME / ".tide" / "arcs" / "works")
# окно «свежести» пруф-чека (единый слой, шов 3): пункт, чекнутый в пределах
# стольких минут, подсвечивается пульсом — результат воркера виден прилетающим
FRESH_CHECK_MIN = 180
# журнальная строка чека (её пишет `tide work check`): «{дата} — пункт N ✓
# «{текст}»: {пруф}» — вытаскиваем время и номер пункта, чтобы знать свежесть
_CHECK_RE = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}) — пункт (\d+) ✓")
# приёмка рукой (работа 22) — контракт с движком, строки журнала дословны.
# Попунктную писала доска тапом по кружку; с работы 27 этого жеста НЕТ (ручка
# /work-item-accept осталась вербам), но старые записи в журналах живы и
# читаются по-прежнему. Массовую пишет `tide work close` — слово человека
_ACCEPT_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}) — пункт (\d+) принят рукой$")
_ACCEPT_ALL_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}) — все сделанные пункты приняты словом")
# чек РУКОЙ с доски: `- {дата} — пункт N чекнут (рука человека, доска)`, пишет
# serve_live._work_check. Пруфа у него нет и быть не может — человек ставит
# галочку сам. Строку «расчекнут» этот шаблон не ловит: после номера он ждёт
# ровно « чекнут», а там стоит « расчекнут» — и сдвинуться `\d+` некуда
_HAND_CHECK_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}) — пункт (\d+) чекнут(?: \(([^)]*)\))?")


def _work_read(f):
    lines = f.read_text(encoding="utf-8").splitlines()
    title = next((l[2:].strip() for l in lines if l.startswith("# ")),
                 f.parent.name)
    meta, desc, items, journal, plan, section = {}, [], [], [], [], ""
    for l in lines:
        # `at` — курсор работы (верб `tide work at`, tide 1.0.44): на каком
        # пункте исполнитель сейчас. С «taken-at» не путается: match() держит
        # начало строки, и `at:` ловит только строку, которая с него и начинается
        # `step` — адрес работы В ПЛАНЕ нити (работа 44): номер шага, под
        # которым она стоит на вкладке «работы». Поле ставит станок; работа без
        # него не ломается, а просто ложится в группу «без шага»
        m = re.match(r"(kind|project|deadline|status|created"
                     r"|taken-by|taken-at|thread|fav|at|step):\s*(.*)", l)
        if m:
            meta[m.group(1)] = m.group(2).strip(); continue
        if l.startswith("## "):
            section = l[3:].strip(); continue
        # состояние пункта (согласование плана, решение 30.07): «x» сделан ·
        # « » согласован и ждёт · «?» ПРЕДЛОЖЕН агентом и ждёт «да» руки
        mi = re.match(r"- \[( |x|\?)\] (.*)", l)
        # фиксы (решение 30.07, работа 19) — накидки руки у приёмки: живут своей
        # секцией СРАЗУ ПОСЛЕ чеклиста, но пункт у них тот же и нумерация
        # СКВОЗНАЯ (сперва чеклист, следом фиксы) — жесты доски флипают по
        # индексу в этом общем списке, разорви его, и чек уйдёт не в тот пункт
        fx = section == "фиксы"
        if section in ("чеклист", "фиксы") and mi:
            items.append([mi.group(1), mi.group(2), [], fx]); continue
        # описание пункта (решение 30.07, его формат из тетради): короткий
        # заголовок-действие в строке `- [ ]`, подробности — строками СРАЗУ
        # ПОД ним с отступом в два пробела. Пункты нумеруются ТОЛЬКО по
        # строкам «- [» (жесты доски флипают по индексу) — продолжения в счёт
        # не идут, поэтому ловим их отдельной веткой ПОСЛЕ mi. Продолжение
        # принимает только пункт СВОЕЙ секции: отступ, забредший под «## фиксы»
        # до первого фикса, иначе прилип бы к последнему пункту чеклиста.
        if (section in ("чеклист", "фиксы") and items and items[-1][3] == fx
                and l.startswith("  ") and l.strip()):
            items[-1][2].append(l.strip()); continue
        # ченджлог работы (решение 16.07): кто когда что сделал — на карточку
        if section == "журнал" and l.startswith("- "):
            journal.append(l[2:].strip()); continue
        # ЗАПИСЬ ЖУРНАЛА БЫВАЕТ МНОГОСТРОЧНОЙ (решение 31.07, работа 33, фикс 3):
        # по новой норме пруф начинается короткой строкой для человека, дальше
        # пустая строка — и техника для протокола. Верб пишет это как есть, и
        # без этой ветки хвост терялся молча: строка без «- » не подходила ни
        # под одну и просто исчезала. Пустые строки держим — на них и режется
        # короткая часть от техники (см. _proof_split).
        if section == "журнал" and journal:
            journal[-1] += "\n" + l.rstrip(); continue
        # план работы (решение 30.07): свободный текст, переводы строк несут
        # структуру — отдаём сырьём, разметку делает карточка
        if section == "план":
            plan.append(l); continue
        if not section and l.strip() and not l.startswith("# "):
            desc.append(l.strip())
    # пункт наружу — четвёркой (состояние, тайтл, описание, фикс?); переводы
    # строк в описании несут структуру, разметку делает карточка
    # (white-space:pre-line). Список ОДИН на обе секции — по нему и считаются
    # сквозные data-i, которыми доска чекает.
    return (title, meta, " ".join(desc),
            [(s, t, "\n".join(d), fx) for s, t, d, fx in items],
            journal, "\n".join(plan).strip())


def _work_checkmap(journal):
    """номер пункта → (когда чекнули, пруф) по журналу работы.
    Пруф пишет `tide work check --proof`; больше его нигде нет — mtime и
    паспорт молчат о том, ЧЕМ пункт закрыт. Читает подсветка свежего чека на
    карточке работы; сам ТЕКСТ пруфа под пунктом собирает _work_proofs — там
    же ловится и чек рукой, которому свежесть воркера ни к чему."""
    out = {}
    for j in journal:
        m = _CHECK_RE.match(j)
        if m:
            proof = j.split("»: ", 1)[1] if "»: " in j else ""
            out[int(m.group(2))] = (m.group(1), proof)
    return out


def _work_proofs(journal):
    """номер пункта → (когда, чем закрыт, рукой?) — ЧТО ПОКАЗАТЬ под пунктом.

    Работа 33 (решение 31.07): «хочу на этих карточках видеть пруфы, если они
    есть». Пруф живёт только строкой журнала, и закрыть пункт могли двумя
    руками: вербом агента (`✓ «имя»: текст пруфа`) и человеком с доски
    (`чекнут (рука человека, доска)`) — у второго пруфа нет и не будет.

    Пункт могли чекать дважды (после uncheck) — берём СВЕЖАЙШЕЕ: журнал идёт
    по времени, и поздняя запись просто перекрывает раннюю. Она же решает спор
    двух рук: если последним пункт закрыл человек, старый пруф описывает чек,
    который уже отменили, и показывать его было бы враньём."""
    out = {}
    for j in journal:
        m = _CHECK_RE.match(j)
        if m:
            proof = j.split("»: ", 1)[1].strip() if "»: " in j else ""
            out[int(m.group(2))] = (m.group(1), proof, False)
            continue
        m = _HAND_CHECK_RE.match(j)
        if m:
            out[int(m.group(2))] = (m.group(1), (m.group(3) or "").strip(),
                                    True)
    return out


# сколько знаков человеческой части пруфа карточка готова показать сразу.
# Норма скилла — до ~120; кап выше нормы, потому что режет он не норму, а
# старые пруфы-простыни, у которых первое предложение бывает и на четверть
# тысячи знаков
PROOF_HEAD_MAX = 200


def _proof_split(text):
    """(короткая строка, техника) — что показать сразу, что спрятать глубже.

    Норма (решение 31.07, работа 33, фикс 2): пруф начинается человеческой
    строкой — что сделано и чем проверено, — дальше пустая строка и техника
    для протокола. Есть перевод строки — верим ему: агент сам сказал, где
    кончается человеческое.

    Старые пруфы писались одной простынёй, и им режем по первому предложению:
    это лучшее, что можно узнать о тексте, не выдумывая. Предложение длиннее
    капа (у сегодняшних бывает) — обрываем по слову; ничего не теряется,
    хвост целиком лежит в «деталях»."""
    text = (text or "").strip()
    if "\n" in text:
        head, tail = text.split("\n", 1)
        return head.strip(), tail.strip()
    m = re.search(r"[.!?…](?=\s)", text[:PROOF_HEAD_MAX + 1])
    if m:
        return text[:m.end()].strip(), text[m.end():].strip()
    if len(text) <= PROOF_HEAD_MAX:
        return text, ""
    cut = text.rfind(" ", 0, PROOF_HEAD_MAX)
    if cut <= 0:  # слова длиннее капа не бывает, но пусть режет честно
        cut = PROOF_HEAD_MAX
    return text[:cut].strip() + "…", text[cut:].strip()


def _work_proof_html(rec):
    """Что сделано по пункту — ВИДНОЙ СТРОКОЙ под ним (работа 33).

    Одна разметка на две площадки: чеклист карточки работы и карточка стола
    «прими работу».

    СВЁРТКИ И ЯРЛЫКА «ПРУФ» ЗДЕСЬ БОЛЬШЕ НЕТ (решение 01.08, фикс 6: «ты
    называешь пруфом, а пишешь не пруф, а „что сделано"; спойлер вообще не
    нужен — просто нормальное описание нужно»). Он прав дважды. Слово «пруф» —
    наше, цеховое: человеку, который решает, принимать ли работу, оно ничего не
    сообщает, а место на карточке занимает. И прятать за клик ОДНУ тихую
    строку — прятать ровно то, ради чего в карточку и смотрят: две ступени
    (фикс 3) читались как «сначала догадайся, что тут есть, потом читай».

    Ступень осталась одна и настоящая: человеческая строка видна сразу, за
    клик уходит только техника — файлы, функции, цифры прогонов. Это протокол,
    его читают, когда уже полезли разбираться. «Карточка тише» (работа 26) не
    нарушена: строка мелкая и тихая, громкости ей добавляет не кегль, а то, что
    её не надо искать.

    Чек рукой раскрывать нечего — тихая строка о том, чья рука; журнал ничего
    другого о нём не знает, и придумывать нам нечего. Молчит журнал совсем —
    молчим и мы."""
    if not rec:
        return ""
    when, text, hand = rec
    if hand:
        return '<div class="wkpfh"{0}>{1}</div>'.format(
            ' title="{0}"'.format(esc(when)) if when else "",
            esc(text or "рукой, без пруфа"))
    if not text:
        return ""
    head, tail = _proof_split(text)
    return ('<div class="wkpf"><div class="wkpfb"{0}>{1}</div>{2}</div>'.format(
        ' title="{0}"'.format(esc(when)) if when else "", esc(head),
        ('<details class="wkpfx"><summary>детали</summary>'
         '<div class="wkpfxb">{0}</div></details>'.format(esc(tail))
         if tail else "")))


def _work_accepted(items, journal):
    """индекс пункта → когда его приняла рука человека (работа 22).

    «Сделано» и «принято» — разные факты: первый ставит исполнитель галочкой в
    чеклисте, второй — человек, и след второго живёт СТРОКОЙ ЖУРНАЛА (формат
    `- [x] …` трогать нельзя, на нём держатся вербы tide). Форм две: попунктная
    (кнопка доски) и массовая — её пишет `tide work close`, когда человек
    принимает работу целиком одним словом.

    Массовая накрывает все сделанные пункты, но только чекнутые ДО неё: работу
    можно открыть заново и дочекать, и старое слово человека про новый пункт
    ничего не говорило. Время чека знает журнал; чек без журнальной строки
    считаем накрытым — раньше слова мы о нём всё равно ничего не знаем."""
    out, mass = {}, ""
    for j in journal:
        m = _ACCEPT_RE.match(j)
        if m:
            out[int(m.group(2)) - 1] = m.group(1); continue
        m = _ACCEPT_ALL_RE.match(j)
        if m:
            mass = m.group(1)
    if not mass:
        return out
    cm = _work_checkmap(journal)
    for i, it in enumerate(items):
        if it[0] != "x" or i in out:
            continue
        when = cm.get(i + 1, ("", ""))[0]
        if not when or when <= mass:  # «ГГГГ-ММ-ДД ЧЧ:ММ» сравнивается строкой
            out[i] = mass
    return out


def _work_lead_jump(meta, by_dir):
    """Прыжок в ведущего агента работы → (нить, её короткое имя, onclick).
    Один резолв на двоих: карточка работы (кнопка-иконка ↗) и стол ISSUES
    («надиктовать →») ведут человека в ОДНУ И ТУ ЖЕ сессию, поэтому и логика
    одна. Нить None — адрес есть, но в этом виде её нет (закрытая/иной проект);
    onclick пустой — прыгать некуда (нить не привязана или агент спит)."""
    thr = meta.get("thread", "").strip()
    if not thr:
        return None, "", ""
    # адрес может быть голым слагом (свой проект) или proj/NN-@нить (кросс-
    # проект): пробуем и хвост после «/», чтобы прыжок работал на полке проекта
    t = by_dir.get(thr) or by_dir.get(thr.rsplit("/", 1)[-1])
    if not t:
        return None, thr, ""
    lead = _real_goal(t) or t["dir"]
    if len(lead) > 56:
        lead = lead[:55].rstrip() + "…"
    head = _head_session(t)
    return t, lead, (_resume_action(t, head) if head else "")


def _work_lead(meta, by_dir):
    """Ведущий агент работы (решение 17.07): какая нить ведёт + провал в её ведущую
    сессию. Привязку создаёт САМ агент (авто на take) — селекта нет, человек нить
    не переключает.

    С ЛИЦА строка ушла в полный вид (фикс 10 работы 21): на карточке она ела две
    строки и повторяла контекст страницы — человек и так стоит внутри нити. Сам
    прыжок с лица не пропал, он стал круглой иконкой ↗ в мета-строке (_dive_btn).

    ИКОНКИ ЗДЕСЬ НЕТ (решение 30.07, фикс 7 работы 22, смотрел с телефона): в
    модалке ↗ стояла дважды — в мета-строке шапки и тут же под ней у «ведёт», —
    и обе вели в одну сессию. Провал остаётся ОДИН, в мете; эта строка просто
    называет ведущую нить."""
    t, lead, oc = _work_lead_jump(meta, by_dir)
    if not lead:
        # непривязанную работу озвучивает строка состояния («не взята — возьмёт
        # агент», работа 21); своя строка тут говорила бы то же самое вторым
        # шрифтом — на лице карточки две одинаковые фразы подряд
        return ""
    if t is None:  # нить не в этом виде (закрытая/иной проект) — просто адрес
        return '<div class="wklead">ведёт нить {0}</div>'.format(esc(lead))
    # (значение той же строки без слова «ведёт» — для паспорта-таблички,
    #  см. _work_lead_val)
    if oc:
        return ('<div class="wklead"><span class="wkleadn">ведёт: {0}</span>'
                '</div>'.format(esc(lead)))
    return '<div class="wklead">нить {0} · агент спит</div>'.format(esc(lead))


# ЛЕГЕНДА ЗНАЧКОВ (решение 31.07, фикс 6 работы 26) — тихая «?» у чеклиста
# модалки, по тапу разворачивается как FAQ. Язык пункта копился фиксами (цифра ·
# кружок · зелёный кружок · подчёркивание · пунктир), и человеку негде было
# спросить, что они значат: подписей у знаков нет и быть не должно — они на то и
# знаки. Легенда ПОКАЗЫВАЕТ их, а не описывает словами: образцы носят те же
# правила, что живые пункты (см. WORK_CSS, селекторы сгруппированы), поэтому
# язык не может разъехаться с легендой — поменяли вид, легенда поменялась сама.
# Классы у образцов СВОИ (.wlmark/.wltx, не .wki/.wkn): на живых узлах висят
# жесты чека и приёмки, и картинка в справке не должна их ловить.
_WORK_LEGEND = (
    '<details class="wkleg"><summary title="что значат значки">?</summary>'
    '<div class="wklegb">' + "".join(
        '<div class="wlrow"><span class="wlm">{0}</span>'
        '<span class="wld">{1}</span></div>'.format(mark, what)
        for mark, what in (
            ('<span class="wlmark">3</span>', "не сделан"),
            ('<span class="wlmark don">3</span>',
             "сделан, ждёт приёмки словом"),
            ('<span class="wlmark don acc">3</span>'
             '<span class="wltx acc">принят</span>', "принят рукой"),
            ('<span class="wltx cur">здесь агент</span>',
             "агент на этом пункте сейчас"),
            ('<span class="wlmark prop">3</span>'
             '<span class="wltx prop">ждёт «да»</span>',
             "предложено агентом, ждёт твоего слова"),
        )) + '</div></details>')


def _work_lead_val(meta, by_dir):
    """Ведущая нить ЗНАЧЕНИЕМ для строки паспорта (фикс 5 работы 26) — без
    слова «ведёт»: его говорит метка слева. Спящего агента не прячем — это
    часть правды о том, кто работу держит."""
    t, lead, oc = _work_lead_jump(meta, by_dir)
    if not lead:
        return ""
    if t is None:
        return esc(lead) + " · не в этом проекте"
    return esc(lead) if oc else esc(lead) + " · агент спит"


def _work_goes(meta, by_dir, steps=None, in_thread=False):
    """КУДА РАБОТА ВЕДЁТ — одной строкой сверху полного вида (работа 44, слово
    владельца 07.08: «хочется чтобы работа что сейчас делается бралась в рамках куда
    идём»). Открыл работу — понял смысл, не поднимаясь в план нити.

    «К чему в итоге» — это результат ЕЁ шага, слово в слово из plan.md. Шага
    нет — отвечает `final:` нити, чем всё кончится: у работы вне шагов
    направление всё равно есть. Нет и его — строки нет вовсе; пустая заглушка
    «результат не задан» сообщала бы только о том, что доска умеет рисовать
    строку.

    СТРОКА ЗНАЕТ, ГДЕ СТОИТ. На вкладке «работа» и в модалке контекста нет —
    там она говорит полностью: нить · шаг · к чему. На площадке нити
    (*in_thread*) обе первые части уже сказаны: нить — заголовком страницы, шаг
    — заголовком группы, под которой работа и стоит. Дублировать их значит
    ровно то, за что 07.08 сняли подвал «ведёт: <нить>» с компакта: строка
    пересказывала человеку то, на что он смотрит. Поэтому внутри нити остаётся
    договорка — сам результат.

    *steps* приходят с площадки (у страницы нити они есть и для закрытой нити);
    без них шаги берём у ведущей нити работы, как их видит этот вид."""
    t, _lead, _oc = _work_lead_jump(meta, by_dir)
    if steps is None:
        steps = (t or {}).get("steps")
    step = _step_of(steps, meta)
    goal = (step or {}).get("result") or (steps or {}).get("final") or ""
    if not goal:
        return ""
    addr = ""
    if not in_thread:
        # нить тут зовётся ПОЗЫВНЫМ (release, paint), а не своей целью: цель —
        # абзац, и в строке-адресе она съедала бы и шаг, и сам результат. Целью
        # нить называется в паспорте («ведёт»), где на неё отведена своя строка
        nit = (t or {}).get("slug") or (meta.get("thread") or "").strip()
        bits = []
        if nit:
            bits.append("нить " + nit)
        if step:
            bits.append("шаг {0} · {1}".format(step["num"], step["name"])
                        if step["num"] else "шаг " + step["name"])
        addr = ('<span class="wkgoa">{0}</span>'.format(esc(" · ".join(bits)))
                if bits else "")
    return ('<div class="wkgoes">{0}<span class="wkgor">'
            '<span class="wkgok">к чему</span>{1}</span></div>'.format(
                addr, esc(goal)))


def _work_plan_html(plan):
    """Тело блока «план» на карточке (решение 30.07). Текст плана свободный:
    строки-буллеты «- …» собираем списком, остальное — абзацами, чтобы
    переводы строк из work.md дожили до глаза, а не схлопнулись в простыню."""
    out, bullets = [], []
    for raw in plan.split("\n"):
        line = raw.strip()
        if line.startswith("- "):
            bullets.append(line[2:].strip()); continue
        if bullets:
            out.append('<ul class="wkpul">{0}</ul>'.format(
                "".join('<li>{0}</li>'.format(esc(b)) for b in bullets)))
            bullets = []
        if line:
            out.append('<p class="wkpp">{0}</p>'.format(esc(line)))
    if bullets:
        out.append('<ul class="wkpul">{0}</ul>'.format(
            "".join('<li>{0}</li>'.format(esc(b)) for b in bullets)))
    return "".join(out)


def _work_form(main=False, proj="", thread=""):
    """Форма «завести работу» — ОДНА на две площадки (решение 30.07): общая
    вкладка «работа» и вкладка «работы» страницы нити. Вид, поля и эндпоинт
    те же; со страницы нити работа рождается сразу в СВОЁМ доме (`proj`) и с
    ответственной нитью страницы (`thread`, работа 24) — иначе она не
    показалась бы в том самом списке, откуда её завели: список нити сит по
    нити, а не по дому.

    Ручки для обработчика — классы (.wknew/.wkgo), а не id: форм на
    странице теперь две, и `getElementById` нашёл бы всегда первую. Общей форме
    id оставлены: на них висят её собственные правила (в т.ч. мобильные) и
    сторож автообновления «не свопать, пока человек печатает».

    Дедлайна в форме НЕТ (решение владельца, фикс 7 работы 20): одна строка — инпут +
    «завести»; срок человек ставит потом чипом «+ дедлайн» на карточке."""
    ids = (' id="wk-t"', ' id="wk-add"') if main else ("", "")
    tags = (' data-proj="{0}"'.format(esc(proj)) if proj else "")
    tags += (' data-thread="{0}"'.format(esc(thread)) if thread else "")
    return (
        '<div class="wkform"{p}>'
        '<input class="nurl wknew"{0} spellcheck="false" '
        'placeholder="что сделать — одной строкой">'
        '<button class="nbtn wkgo"{1}>завести</button></div>'.format(
            *ids, p=tags))


# ── ГЕЙТ СОГЛАСОВАНИЯ ПЛАНА: «да» — кнопка, а не слово в чат ────────────────
# Работа 44, слово человека 07.08: «план сейчас висел на апрув — это конкретный
# типовый гейт, и по идее после апрува агент должен идти по согласованной работе
# до след гейта». До этого человек видел карточку, шёл в чат, писал «да», агент
# вручную звал `tide work agree`, потом `take`, потом строил — три перехода на
# ровном месте. Решение 06 нити release отменяет прежнее «да только словом»
# (30.07): то правило снимало жесты ПО ПУНКТАМ, а гейт — один на работу, это
# другое. Пунктовые жесты руки на доске так и не вернулись.
#
# ДВЕ ПЛОЩАДКИ, ОДНА КНОПКА. Гейт стоит и на карточке работы, и на столе
# (решение 07: «согласования не в планах, а в issue»). Разметка кнопки одна —
# _gate_block, — и адрес у неё один: /work-agree. Разъедься они, гейт вёл бы
# себя по-разному в двух местах, а это ОДИН гейт.
#
# СЛОВО. Кнопка — тоже подпись человека, и она честно называет свой канал:
# «да — кнопкой с доски». Подставить голое «да» так, будто человек произнёс его
# голосом, значит подделать подпись; строку в журнале потом читают глазами и по
# ней решают, чем гейт закрыт. Слово-близнец у отказа: «нет — кнопкой с доски».
# Оба живут на сервере (Handler.GATE_YES/GATE_NO) — тут только то, что показано
# человеку ДО нажатия: доска не обещает словами того, чего не сделает.
GATE_WORD = "да — кнопкой с доски"
GATE_NO_WORD = "нет — кнопкой с доски"


def _gate_block(slug, n):
    """«Да» гейта — одной разметкой на карточку работы и на стол.

    Кнопка появляется ТОЛЬКО когда есть что согласовывать: нет висящих «- [?]»
    — нет и блока. Пункты она не чекает и работу не закрывает — `done` ставит
    только рука человека у приёмки. Что кнопка делает на самом деле, написано
    тут же тихой строкой: обещание и жест должны совпадать."""
    return ('<div class="wkgate">'
            '<button type="button" class="abtn primary wkyes" data-yes="{s}">'
            'да, согласовано · {n}</button>'
            '<span class="wkgatew">в журнал ляжет «{w}» · агент возьмёт работу '
            'и поведёт её до приёмки</span></div>'.format(
                s=esc(slug), n=n, w=esc(GATE_WORD)))


def _send_block(slug, quiet):
    """«Поехали» на работе, по которой тихо — жест рядом с диагнозом (01.09).

    Доска давно умела СКАЗАТЬ «взята, строитель не отправлен — тихо два дня»,
    но сделать с этим ничего не могла: отправка жила только внутри гейта
    согласования, и работа, чей план согласовали словом в чате, лежала, пока про
    неё не вспомнят. Диагноз без жеста — это упрёк, а не инструмент.

    Кнопка появляется ТОЛЬКО когда доска и так жалуется (*quiet* непустой) и
    согласовывать нечего: висят предложения — человеку нужен гейт, а не гонец.
    Пункты она не чекает и работу не закрывает: `done` — рука человека.
    """
    return ('<div class="wkgate">'
            '<button type="button" class="abtn wksend" data-send="{s}">'
            'отправить строителя</button>'
            '<span class="wkgatew">{q} · в журнал ляжет отправка, работа уйдёт '
            'в сессию своей нити с живой репликой</span></div>'.format(
                s=esc(slug), q=esc(quiet)))


def _gate_no(slug, i):
    """«Нет» одному предложению — путь отказа рядом с «да».

    Согласиться со всем одной кнопкой легко, а «эти два убери» до работы 44
    делалось только словом в чат. Пилюля со словом, а не крестик: крестик рядом
    с «да» читался бы как «удалить пункт», тогда как это тоже ответ человека и
    он тоже ложится в журнал. Стоит только у предложенного пункта — у
    согласованного своя цена и свой верб."""
    return ('<span class="wkno" data-no="{0}" data-i="{1}" title="снять '
            'предложение — в журнал ляжет «{2}»">нет</span>'.format(
                esc(slug), i, esc(GATE_NO_WORD)))


def _work_parts(slug, meta, desc, items, journal, plan, by_dir, dl, today,
                now=None, plan_open=False, steps=None, in_thread=False):
    """Куски одной работы, из которых собраны ОБА её вида: лицо (чипы меты) и
    полный — паспорт · план · описание · чеклист · журнал.

    Вынесено из карточки вкладки «работа» словом человека 07.08: «работа нити сразу
    видна развёрнутой». Тот же полный вид рисует теперь и блок вкладки «работы»
    страницы нити (_work_block) — а собирать чеклист вторым куском кода нельзя:
    жесты доски адресуются по data-wk + data-i, и разъехавшаяся разметка увела бы
    чек не в тот пункт. Функция только СТРОИТ строки и ничего не решает про
    раскладку: что из кусков показать и где, каждая площадка выбирает сама."""
    now = now or datetime.now()
    st = meta.get("status", "open")
    proj = ('<span class="wkproj">{0}</span>'.format(esc(meta["project"]))
            if meta.get("project") else "")
    # дедлайн переключается кликом по чипу (решение 16.07); без дедлайна —
    # пунктирный «+ дедлайн»; закрытые — просто текст. Поставленный срок —
    # на лице (это ограничение, его смотрят), а пустая рука «+ дедлайн» — в
    # полном виде: ставят срок редко и уже вдумавшись в работу
    dl_chip = dl_add = ""
    if dl:
        cls = ("bad" if dl < today else "warn" if dl == today else "")
        dl_chip = '<span class="wkdl {0}"{1}>до {2}</span>'.format(
            cls,
            (' data-dl="{0}" data-iso="{1}"'.format(
                esc(slug), dl.isoformat()) if st != "done" else ""),
            dl.strftime("%d.%m"))
    elif st != "done":
        # «поставить», а не «+ дедлайн» (фикс 5 работы 26): чип живёт ТОЛЬКО
        # в паспорте-табличке, где слово «дедлайн» уже сказано меткой слева;
        # плюс перед ним читался бы вторым названием строки. Жест тот же —
        # класс, data-dl и обработчик не менялись
        dl_add = ('<span class="wkdl add" data-dl="{0}">поставить</span>'
                  .format(esc(slug)))
    # кто взял работу и когда (единый слой, шов 1): чип рисуем только когда
    # паспорт реально знает исполнителя (статус taken/review) — у open его
    # нет. Имя может быть длинным (адрес сессии) — режем, полное в title;
    # дату показываем компактно, без секунд.
    taken, taken_val = "", ""
    tk_by = meta.get("taken-by", "").strip()
    if tk_by:
        tk_at = meta.get("taken-at", "").strip()
        try:
            tk_at = datetime.strptime(
                tk_at, "%Y-%m-%dT%H:%M").strftime("%d.%m %H:%M")
        except ValueError:
            pass  # незнакомый формат — показываем как есть
        short = tk_by if len(tk_by) <= 32 else tk_by[:31].rstrip() + "…"
        label_full = "взял: " + tk_by + (" · " + tk_at if tk_at else "")
        chip = "взял: " + short + (" · " + tk_at if tk_at else "")
        taken = '<span class="wktaken" title="{0}">{1}</span>'.format(
            esc(label_full), esc(chip))
        # в паспорте-табличке слово «взял» стоит меткой слева, поэтому в
        # значении его нет — только кто и когда (фикс 5 работы 26)
        taken_val = '<span title="{0}">{1}</span>'.format(
            esc(label_full),
            esc(short + (" · " + tk_at if tk_at else "")))
    # свежесть работы (фикс 6 работы 21): сколько прошло с последнего жеста
    # по журналу — тихо, как «5 мин» на карточке нити
    ago = _work_age(journal)
    age_chip = ('<span class="wkage">{0}</span>'.format(esc(ago))
                if ago else "")
    # свежий пруф-чек (единый слой, шов 3): галочка только что чекнутого
    # пункта пульсит — результат воркера виден прилетающим. «Когда чекнули»
    # знает только журнал: собираем номер_пункта → (время, пруф) последнего
    # чека; пункт «свежий», если чек в пределах FRESH_CHECK_MIN.
    checkmap = _work_checkmap(journal)
    # чем закрыт каждый сделанный пункт (работа 33): «что сделано» строкой
    # под пунктом или чья рука, см. _work_proof_html
    proofs = _work_proofs(journal)
    # кто из сделанных пунктов уже принят рукой (работа 22) — вторая отметка
    accmap = _work_accepted(items, journal)
    # КУРСОР РАБОТЫ (решение 31.07, фикс 15 работы 25): на каком пункте
    # исполнитель СЕЙЧАС. Число ставит сам агент вербом `tide work at`; чем
    # он живёт и почему протухает — см. _work_cursor. Его читают двое —
    # подчёркивание пункта тут и строка «агент делает» (фикс 4 работы 26), —
    # и ответ у них ОДИН, иначе лицо и чеклист покажут разные места.
    cursor = _work_cursor(meta, journal, now)
    # без иконок-карандашей (решение 16.07): клик по тексту = правка,
    # квадратик = чек, крестик крупный = удалить
    rows_html, fix_html = [], []
    for i, (state, txt, idesc, is_fix) in enumerate(items):
        done, prop = state == "x", state == "?"
        cls = " don" if done else " prop" if prop else ""
        if i + 1 == cursor and not done:
            cls += " cur"
        tattr = ""  # НЕ title: имя работы живёт снаружи, на лице карточки
        when, proof = checkmap.get(i + 1, ("", "")) if done else ("", "")
        if when:
            try:
                dt = datetime.strptime(when, "%Y-%m-%d %H:%M")
                if (now - dt).total_seconds() <= FRESH_CHECK_MIN * 60:
                    cls += " fresh"
                    if proof:  # пруф последнего чека — в подсказку галочки
                        tattr = ' title="{0}"'.format(esc(proof[:240]))
            except ValueError:
                pass  # кривая дата в журнале — просто не подсвечиваем
        # ПОПУНКТОВОГО «да» тут нет и не будет (решение 06, 30.07): пункт —
        # мелкая цель посреди читаемого текста, промах по ней стоит записи в
        # файл. Гейт согласования — не попунктовый: он про план ЦЕЛИКОМ и живёт
        # одной кнопкой под чеклистом (работа 44, см. _gate_block)
        # описание пункта — ОТДЕЛЬНЫМ узлом, не внутри .wkt: правка текста
        # берёт textContent именно .wkt, и подробности не должны в неё
        # попасть (жест переписал бы только строку тайтла и съел описание)
        dhtml = ('<div class="wkd">{0}</div>'.format(esc(idesc))
                 if idesc else "")
        # ПРУФ ПОД ПУНКТОМ (решение 31.07, работа 33): чем именно закрыт
        # пункт — свёрткой в том же столбике, что описание. Голая галочка
        # проверить сделанное не давала: текст пруфа лежал только в
        # журнале, и человек читал его простынёй, разворачивая весь лог
        pfh = _work_proof_html(proofs.get(i + 1)) if done else ""
        # ДВЕ ОТМЕТКИ (работа 22): квадратик — «сделано», чек исполнителя;
        # вторая галочка — «принято», рука человека. Вторая появляется
        # только у сделанного пункта: принимать нечего, пока исполнитель не
        # отчитался.
        # У СДЕЛАННОГО КВАДРАТИК МЁРТВ (решение 30.07, фикс 8 работы 22:
        # «галочки всё ещё кликаются»): он читается как переключатель, и
        # приёмка, висевшая на нём, срабатывала от промаха по зелёной
        # галочке. Приёмка теперь ТОЛЬКО явным кликом по второй отметке —
        # у неё и курсор, и подсказка; квадратик же просто говорит факт.
        a_when = accmap.get(i, "") if done else ""
        if a_when:
            cls += " acc"
        # УЗЛЫ ОТМЕТОК ОСТАЮТСЯ, НО ПУСТЫ (решение 31.07, фикс 14 работы 25):
        # рисунок состояния переехал на цифру (кружок), галочки скрыты
        # правилом, а разметка цела — на ней держится контракт с сервером и
        # чужой разбор. Подсказка ушла ВМЕСТЕ с рисунком: на скрытом узле
        # она не всплыла бы, наводить не на что. Всё, что о состоянии можно
        # сказать словами — пруф последнего чека, время приёмки,
        # приглашение к жесту, — висит теперь на самой цифре.
        mark = '<span class="wkacc"></span>'
        if done and a_when:
            ntit = "принято рукой · " + a_when
        elif done:
            # «тап — принять» из подсказки ушло (работа 27): жеста больше
            # нет, и звать к нему пальцем нечестно. Остаётся факт и пруф
            ntit = "сделано · чек исполнителя"
            if proof:
                ntit += " · " + proof[:240]
        else:
            ntit = "ждёт твоего «да»" if prop else ""
        # СДЕЛАННОЕ НЕПРИКОСНОВЕННО (решение 30.07, фикс 5 работы 22): у
        # пункта с галочкой нет ни крестика, ни правки текста — на любом
        # статусе работы, не только у приёмки. На него уже сослался журнал
        # (пруф чека, строка «пункт N принят рукой») и по нему считается
        # прогресс: правка сделала бы пруф ложью, а удаление сдвинуло бы
        # нумерацию — все прошлые строки журнала стали бы указывать не на
        # те пункты. Снять «сделано» может только исполнитель вербом
        # `tide work uncheck --reason`, и тогда пункт снова правится.
        # Тот же запрет стоит на сервере: рендер обойти можно, ручку — нет.
        # у ПРЕДЛОЖЕННОГО пункта вместо крестика — «нет» (работа 44): это не
        # удаление строки, а ответ человека, и он идёт вербом со словом
        xbtn = ("" if done else _gate_no(slug, i) if prop else
                '<span class="wkx" data-act="del" title="удалить">×</span>')
        ttit = (' title="сделано — история, правится только вербом '
                'исполнителя"' if done else "")
        # НОМЕР ПУНКТА ГЛАЗАМИ (решение 31.07, работа 25, шаг 8): «а ещё
        # пункты я хочу цифрами увидеть». Цифра та же, которой пункт зовут
        # вербы (`tide work check <работа> 3`) и которой его называет
        # журнал, — счёт с единицы по общему списку, поэтому у фиксов
        # нумерация продолжает чеклист, а не начинается заново.
        (fix_html if is_fix else rows_html).append(
            '<div class="wki{c}" data-i="{i}"{ta}>'
            '<span class="wkn"{nt}>{n}</span>'
            '<span class="wkbox"></span>{mk}'
            '<div class="wktx"><span class="wkt"{tt}>{tx}</span>{d}{pf}'
            '</div>{x}</div>'.format(
                c=cls, i=i, n=i + 1, ta=tattr, mk=mark, tt=ttit,
                nt=' title="{0}"'.format(esc(ntit)) if ntit else "",
                tx=esc(txt), d=dhtml, pf=pfh, x=xbtn))
    rows = "".join(rows_html)
    # рукой человека с доски (16.07): дописать пункт — только у живых работ.
    # Стоит ВПРИТЫК к чеклисту, до блока фиксов: жест пишет именно в
    # `## чеклист`, и разной формой про это врать нельзя
    if st != "done":
        rows += ('<div class="wkadd" data-add="{0}">+ пункт</div>'
                 .format(esc(slug)))
    # фиксы (решение 30.07, работа 19) — то, что человек накидал у приёмки:
    # своим блоком под плановыми шагами, отделён тихим заголовком с
    # волосяной линией. Пункт визуально тот же (квадратик, тайтл,
    # описание) и жесты те же — индекс data-i сквозной, чек работает.
    if fix_html:
        rows += ('<div class="wkfixlbl">фиксы · {0}</div>'
                 '<div class="wkfixitems">{1}</div>'.format(
                     len(fix_html), "".join(fix_html)))
    # ГЕЙТ (работа 44) — последним, под всем чеклистом: он про план ЦЕЛИКОМ, а
    # не про строку. Нет висящих предложений — нет и блока
    n_props = sum(1 for it in items if it[0] == "?")
    if n_props and st != "done":
        rows += _gate_block(slug, n_props)
    elif st != "done":
        # согласовывать нечего, а доска жалуется на тишину — значит нужен не
        # гейт, а гонец (см. _send_block)
        quiet = _work_quiet(journal, now)
        if quiet:
            rows += _send_block(slug, quiet)
    # прогресс — по СОГЛАСОВАННЫМ пунктам ([ ]+[x]); предложенные висят
    # отдельной припиской, они ещё не работа
    agreed = [it for it in items if it[0] != "?"]
    n_done = sum(1 for it in agreed if it[0] == "x")
    # описание и заголовок правятся кликом по тексту (решение 16.07).
    # ПУСТОГО «+ ОПИСАНИЕ» БОЛЬШЕ НЕТ (решение 31.07, фикс 11 работы 25):
    # приглашение занимало строку в самой плотной части модалки и не
    # сообщало ничего. Есть описание — тихая строка, нет — нет и строки;
    # завести его агент по-прежнему может вербом, а живое правится кликом.
    if not desc:
        dsc = ""
    elif st != "done":
        dsc = ('<div class="wkdesc" data-desc="{0}"><span class="wkdt">{1}'
               '</span></div>'.format(esc(slug), esc(desc)))
    else:
        dsc = ('<div class="wkdesc"><span class="wkdt">{0}</span></div>'
               .format(esc(desc)))
    # план работы (решение 30.07): свёрнутый блок сразу под ведущей нитью —
    # читаешь, что агент собрался делать, и киваешь пунктам ниже. Секции
    # `## план` в паспорте может не быть — тогда блока просто нет.
    # *plan_open* — во вкладке «работы» нити план стоит РАЗВЁРНУТЫМ (решение владельца
    # 07.08): туда приходят читать, куда работа идёт, и прятать это за словом
    # «план» значит не показать. На кухне работ спойлер остаётся спойлером —
    # там карточек полторы сотни, и развёрнутые планы съели бы страницу.
    pln = ('<details class="wkplan"{1}><summary>план</summary>'
           '<div class="wkplines">{0}</div></details>'.format(
               _work_plan_html(plan), " open" if plan_open else "")
           if plan else "")
    # ченджлог работы (решение 16.07): свёрнутый журнал на карточке,
    # свежие сверху — видно, кто когда что сделал (рука или агент)
    jrn = ('<details class="wkjrn"><summary>журнал · {0}</summary>'
           '<div class="wkjlines">{1}</div></details>'.format(
               len(journal),
               "".join('<div class="wkjl">{0}</div>'.format(esc(j))
                       for j in reversed(journal)))
           if journal else "")
    # ПАСПОРТ — В СВЁРТКУ (решение 31.07, фикс 3 работы 26): дом · срок · кто
    # взял · ведущая нить съезжали на лицо модалки третьим и четвёртым
    # этажом и топили то, ради чего её открыли, — что происходит и какие
    # шаги. Это справка: её читают раз, когда провалились разбираться, а не
    # каждый раз. Уезжает тем же тихим спойлером, что план и журнал, — на
    # лице остаются имя, статус, «агент делает» и пункты.
    # Жест «+ дедлайн» уехал ВМЕСТЕ с паспортом и цел: он живёт на своём
    # чипе, а не на месте — развернул справку и поставил срок.
    # ТАБЛИЧКОЙ, А НЕ СТРОКОЙ (решение 31.07, фикс 5 работы 26): внутри
    # свёртки куски паспорта стояли в один ряд через точку — «tide-stack ·
    # поставить · взял: … · ведёт: …», — и читались как одно слипшееся
    # предложение, в котором глаз не находит нужное поле. Теперь строки
    # «метка → значение»: метку берёт тихий моно-капс, значение — обычный
    # инк. Ищется взглядом по левой колонке, а не перечитыванием.
    # Значения — те же узлы, что были: чип дедлайна несёт свой жест, у
    # «взял» тот же title с полным именем. Пустое поле строки не рождает.
    pass_rows = []
    if meta.get("project"):
        pass_rows.append(("дом", esc(meta["project"])))
    if dl_chip or dl_add:
        pass_rows.append(("дедлайн", dl_chip or dl_add))
    if taken_val:
        pass_rows.append(("взял", taken_val))
    lead_val = _work_lead_val(meta, by_dir)
    if lead_val:
        pass_rows.append(("ведёт", lead_val))
    passport = "".join(
        '<div class="wkprow"><span class="wkpk">{0}</span>'
        '<span class="wkpv">{1}</span></div>'.format(k, v)
        for k, v in pass_rows)
    # куда работа ведёт (работа 44) — первой строкой полного вида, до паспорта:
    # это не справка, а рамка, в которой всё остальное читается
    goes = _work_goes(meta, by_dir, steps, in_thread)
    return {"st": st, "proj": proj, "dl_chip": dl_chip, "dl_add": dl_add,
            "taken": taken, "age_chip": age_chip, "cursor": cursor,
            "rows": rows, "n_done": n_done, "n_agreed": len(agreed),
            "dsc": dsc, "pln": pln, "jrn": jrn, "passport": passport,
            "goes": goes}


def _work_full(p):
    """ПОЛНЫЙ ВИД работы одной разметкой на все площадки: куда ведёт · паспорт ·
    план · описание · чеклист · журнал. Куски приходят из _work_parts.

    Живёт ВНУТРИ карточки и на вкладке «работа» скрыт CSS — там лицо говорит
    состояние, а подробности берут проваливанием в модалку (решение 30.07, работа
    21). Во вкладке «работы» страницы НИТИ он же стоит развёрнутым (решение 07.08:
    «работа нити сразу видна развёрнутой») — та же разметка, другая площадка.

    «КУДА ВЕДЁТ» — ПЕРВОЙ (работа 44): «открыл карточку — понял смысл». Она
    рамка, а не справка, и потому стоит ДО паспорта — за спойлером ей делать
    нечего. Лица вкладки «работа» это не касается: полный вид там по-прежнему
    скрыт и всплывает только в модалке, правило «лицо = состояние» цело."""
    return ('<div class="wkfull">{g}{ps}{p}{d}'
            '<div class="wkitems">{r}</div>{j}</div>').format(
                g=p.get("goes", ""),
                ps=('<details class="wkpass"><summary>паспорт</summary>'
                    '<div class="wkpassb">{0}</div></details>'.format(
                        p["passport"]) if p["passport"] else ""),
                d=p["dsc"], p=p["pln"], r=p["rows"], j=p["jrn"])


def _works_panel(threads=None):
    today = datetime.now().date()
    # нити СВОЕГО проекта (tide-stack) — для ответственной нити и перехода в
    # её голову; читаем сами, вызов из main() ничего не пробрасывает
    if threads is None:
        sroot = WORKS_DIR.parents[2]
        threads = read_threads(sroot.name, sroot)
    by_dir = {t["dir"]: t for t in threads}  # ответственная нить = её dir
    works = []
    for hint, f in work_files():
        title, meta, desc, items, journal, plan = _work_read(f)
        dl = None
        try:
            dl = datetime.strptime(meta.get("deadline", ""), "%Y-%m-%d").date()
        except ValueError:
            pass
        works.append((0 if _work_is_fav(meta) else 1, dl or datetime.max.date(),
                      _work_key(hint, f.parent.name), title, meta, desc, items,
                      dl, journal, plan, f.parent.name))
    # избранные — первыми, ровно как избранные нити над потоком (решение 30.07,
    # фикс 7 работы 21); внутри группы порядок прежний: дедлайн, потом номер
    works.sort(key=lambda w: (w[0], w[1], w[2]))
    cards, closed = [], []
    # slug тут — КЛЮЧ работы (голый у общей папки, «дом/слаг» у своей): им
    # адресуются все ручки и им же метится карточка. Номер в заголовке читается
    # с имени КАТАЛОГА: у «<проект>/07-…» первый символ не цифра, и общий ключ
    # оставил бы работы соседних проектов без номера.
    for (_fv, _dt, slug, title, meta, desc, items, dl, journal, plan,
         dirname) in works:
        st = meta.get("status", "open")
        # ЛИЦО РАЗГРУЖЕНО (решение 30.07, фикс 10 работы 21, смотрел с телефона:
        # «карточки перегруженные, много информации»). Мета работы делится
        # надвое: то, что нужно НА ВЗГЛЯД — статус · номер · срок · свежесть —
        # остаётся на лице ОДНОЙ тихой строкой под названием; паспортное (дом,
        # кто взял, постановка срока) уезжает в полный вид, его читают, когда в
        # работу уже провалились. Раньше всё это толкалось в одной строке с
        # названием, и на 390px название сжималось в «Чернови передач собира…».
        # номер работы — ПЕРВЫМ в заголовке, «22 · Название», а не тихим чипом
        # в мете (фикс 10 работы 22): по нему человек сверяет карточку со
        # словом оркестратора, и в мете он тонул
        num = _work_num(dirname)
        # ВСЁ, ЧТО ОБЩЕЕ С БЛОКОМ НИТИ, СОБИРАЕТ _work_parts (решение 07.08:
        # «работа нити сразу видна развёрнутой»): чипы паспорта, план, описание,
        # чеклист с фиксами и журнал. Раньше это лежало прямо тут — и полный вид
        # был только у этой карточки; теперь его же рисует блок вкладки «работы»
        # страницы нити (_work_block). Двум сборкам разъезжаться нельзя: жесты
        # доски адресуются по data-wk + data-i, и второй, чуть другой чеклист
        # увёл бы чек не в тот пункт.
        now = datetime.now()
        p = _work_parts(slug, meta, desc, items, journal, plan, by_dir, dl,
                        today, now)
        dl_chip, age_chip, cursor = p["dl_chip"], p["age_chip"], p["cursor"]
        rows, dsc, pln, jrn = p["rows"], p["dsc"], p["pln"], p["jrn"]
        n_done, n_agreed = p["n_done"], p["n_agreed"]
        # ★ избранное (фикс 7 работы 21) — правым краем меты, как у нити в
        # полосе статуса; у закрытых руки нет, они уже в памяти
        star = _work_star(slug, _work_is_fav(meta)) if st != "done" else ""
        # ряд чипов закрытой карточки — прежний, минус номер: он и у закрытых
        # теперь живёт в заголовке (фикс 10 работы 22)
        chips = [p["proj"], dl_chip, p["dl_add"], p["taken"], age_chip]
        # статусы в файле — жесты агента. РАЗВОРОТ решения 16.07 «„в работе"
        # убрать вообще» единым слоем: воркер теперь виден живьём — taken/review
        # рисуются бейджем в шапке (шов 2 ниже); кнопка же знает лишь
        # закрыть/открыть — закрыть можно всегда, закрытую открыть обратно
        # (решение 16.07), done — рука человека
        btn = ('<button class="nbtn wkdone" data-close="{0}">закрыть</button>'
               .format(esc(slug)) if st != "done" else
               '<button class="nbtn wkdone" data-reopen="{0}">открыть'
               '</button>'.format(esc(slug)))
        # «22 · Название»: номер СВОИМ span-ом ВНЕ редактируемой зоны — жест
        # правки (wkEdit) берёт textContent именно с .wknm[data-title], и
        # номер не должен уезжать в текст при сохранении. Обёртка .wknmw несёт
        # кегль и кламп на обоих разом — номер читается тем же шрифтом, что имя
        npre = ('<span class="wknmn">{0} · </span>'.format(num) if num else "")
        nm = ('<span class="wknmw">{0}<span class="wknm" data-title="{1}">{2}'
              '</span></span>'.format(npre, esc(slug), esc(title))
              if st != "done" else
              '<span class="wknmw">{0}<span class="wknm">{1}</span></span>'
              .format(npre, esc(title)))
        # ведущий агент (решение 17.07): привязку создаёт агент сам, селекта нет.
        # У живой работы строка живёт в ПОЛНОМ виде (фикс 10 работы 21) — с лица
        # прыжок остался иконкой ↗ в мете; у закрытой это просто память
        thr_val = meta.get("thread", "").strip()
        thr = (_work_lead(meta, by_dir) if st != "done" else
               ('<div class="wklead done">вела нить {0}</div>'.format(esc(thr_val))
                if thr_val else ""))
        # статус живьём (единый слой, шов 2; правда пульса — фикс 9 работы 22):
        # «агент работает» с зелёной точкой, пока по САМОЙ работе идут жесты
        # (журнал моложе получаса); «в работе» без точки — взята, но остыла;
        # «на проверке» — ждёт руки. open — без бейджа, done — в спойлер как есть
        badge = _work_badge(st, st == "taken" and _work_live(journal, now),
                            quiet=bool(_work_quiet(journal, now)))
        if st == "done":
            # закрытая карточка остаётся прежней (решение 30.07, работа 21:
            # «done — как сейчас в спойлере закрытых, лицо не трогать»): она
            # уже не разговор, а память, и читают её именно списком сделанного
            closed.append((slug, (
                '<div class="wkcard closed" data-wk="{s}">'
                '<div class="wkhead">{t}'
                '<span class="wkchips">{g}{c}</span></div>{d}{th}{p}'
                '<div class="wkitems">{r}</div>{j}'
                '<div class="wkfoot"><span class="wkprog">{nd}/{n}</span>'
                '{b}</div></div>'.format(
                    s=esc(slug), t=nm, g=badge, c="".join(chips),
                    d=dsc, th=thr, p=pln, r=rows, j=jrn,
                    nd=n_done, n=n_agreed, b=btn))))
            continue
        # ЛИЦО ≠ ПОЛНЫЙ ВИД (решение 30.07, работа 21). На лице — НАЗВАНИЕ во всю
        # ширину, под ним ОДНА тихая мета-строка и СТРОКА СОСТОЯНИЯ (главный
        # текст: чей ход и что происходит), в подвале «закрыть». Всё остальное —
        # паспортные чипы, ведущая нить, описание, план, чеклист с фиксами,
        # журнал и жесты по ним — лежит тут же в `.wkfull`, скрыто CSS и
        # всплывает ТОЛЬКО в модалке: #wkmodal клонирует карточку целиком.
        # Вторым КОДОМ полный вид не собираем: разметку строит _work_full, и она
        # же идёт в блок вкладки «работы» нити (решение 07.08) — жесты доски
        # адресуются по data-wk + data-i, и разъехавшаяся сборка увела бы чек не
        # в тот пункт.
        _t, lead_nm, lead_oc = _work_lead_jump(meta, by_dir)
        # правый край меты — руки: ★ и провал к ведущему агенту одной иконкой
        hands = '<span class="wkmr">{0}{1}</span>'.format(
            star, _dive_btn(lead_nm, lead_oc))
        # ЛЕГЕНДА ЗДЕСЬ БОЛЬШЕ НЕ ЖИВЁТ (решение 31.07, фикс 7 работы 26): она
        # уехала в полоску модалки к крестику. Справка одна на все работы и не
        # меняется — возить её копией в каждой из полутора сотен карточек было
        # и лишним весом, и лишним ярусом в шапке.
        full = _work_full(p)
        cards.append(
            '<div class="wkcard" data-wk="{s}" data-wkopen="{s}">'
            '<div class="wkhead">{t}</div>'
            '<div class="wkmeta">{g}{dl}{ag}{h}</div>'
            '{stl}{full}'
            '<div class="wkfoot one">{b}</div></div>'.format(
                s=esc(slug), t=nm, g=badge, dl=dl_chip, ag=age_chip,
                h=hands, stl=_work_state_html(st, items, journal, now, title,
                                              cursor=cursor),
                full=full, b=btn))
    label = ('<div class="slabel">работы · {0}</div>'.format(len(cards))
             if cards else '<div class="slabel">работ пока нет</div>')
    # закрытые — внизу под спойлером, свежие сверху (кандидат 121, решение 16.07)
    spoiler = (
        '<details class="wkclosed"><summary>закрытые · {0}</summary>'
        '<div class="wkgrid">{1}</div></details>'.format(
            len(closed),
            "".join(c for _, c in sorted(closed, reverse=True)))
        if closed else "")
    return (
        '<div id="work" hidden>' + _work_form(main=True)
        + label + '<div class="wkgrid">{0}</div>'.format("".join(cards))
        + spoiler + '</div>')


WORK_CSS = """
#work .wkform{display:flex;gap:8px;margin:22px 0 26px}
#work .wkform #wk-t{flex:1;max-width:520px}
/* та же форма — вкладкой «работы» страницы нити (решение 30.07). Правила общей
   висят на её id и остаются за ней (два id перебьют любой класс), поэтому две
   формы не спорят; здесь те же размеры через классы. Отступ меньше: форма
   стоит сразу под рядом вкладок, а не под шапкой экрана */
.wkform{display:flex;gap:8px}
.spane .wkform{margin:2px 0 16px}
.wkform .wknew{flex:1;min-width:0;max-width:520px}
@media (max-width:700px){
  /* одна строка и на телефоне (фикс 7 работы 20): полей два — инпут тянется,
     кнопка справа честным тап-таргетом (44px, HIG), без переноса */
  .spane .wkform .wknew{max-width:none}
  .spane .wkform .wkgo{min-height:44px}
}
.wkgrid{display:grid;grid-template-columns:1fr;gap:10px}
/* большой экран (решение 16.07): вкладка работ раздвигает контейнер,
   карточки — всегда в две колонки; на узком — всегда одна */
@media (min-width:1100px){
  .wrap:has(#work:not([hidden])){max-width:1080px}
  .wkgrid{grid-template-columns:repeat(2,minmax(0,1fr))}
}
.wkcard{border:1px solid var(--line);border-radius:6px;padding:14px 16px;
  display:flex;flex-direction:column}
/* ── лицо = состояние, подробности — проваливанием (решение 30.07, работа 21) ──
   Полный вид живёт ВНУТРИ карточки и скрыт: модалка клонирует карточку целиком
   и там разворачивает его. Одна разметка на оба вида — двух копий чеклиста на
   странице быть не должно, жесты адресуются по data-wk + data-i. */
.wkfull{display:none}
#wkmodal .wkfull{display:block}
/* ── сама модалка (решение 30.07, фикс 7 работы 22; переделана 31.07, шаг 7
   работы 25: «края-рамки непонятные, под ней можно страницу проскролить») ────
   РАМКА ОДНА. Раньше их было две: своя у панели модалки и своя у клонированной
   карточки внутри — на 390px это читалось как окно в окне, и текст жался к
   середине двойными полями. Панель держит рамку и отступы (16–18px, ровно как
   у карточек), карточка внутри их складывает.
   СКРОЛЛ ВНУТРИ ПАНЕЛИ, а не под ней. Раньше длинная работа растягивала
   панель, а ездил ею весь оверлей — из-за чего снизу вылезала доска, край
   панели уходил за экран и читался как обрыв. Теперь панель — поверхность
   фиксированной высоты (как выпадашка бургера: тот же --bg-1, кант --line-2 и
   тень), а едет только её тело; цепочку скролла наружу гасит
   overscroll-behavior, фон под модалкой держит замок html.wkmlock. */
#wkmodal{display:none;position:fixed;inset:0;background:rgba(0,0,0,.55);
  backdrop-filter:blur(3px);z-index:75;align-items:center;
  justify-content:center;overflow:hidden;overscroll-behavior:contain;
  padding:40px 16px}
#wkmodal .wkmpane{position:relative;max-width:640px;width:100%;max-height:100%;
  display:flex;flex-direction:column;overflow:hidden;
  background:var(--bg-1);border:1px solid var(--line-2);border-radius:12px;
  box-shadow:0 12px 28px rgba(0,0,0,.28)}
/* ✕ живёт своей полоской НАД телом, а не поверх текста: content-strip модалки
   не должен подлезать под кнопку, когда его листают */
/* полоска модалки: слева «?» (легенда значков), справа ✕. Две тихие круглые
   ручки одного роста по краям — ряд читается рамкой окна, а не этажом кнопок */
#wkmodal .wkmtop{flex:none;display:flex;align-items:center;
  justify-content:space-between;padding:6px 10px 0}
#wkmodal .wkmbody{overflow-y:auto;overscroll-behavior:contain;
  -webkit-overflow-scrolling:touch;padding:2px 18px 18px}
#wkmx{background:none;border:none;color:var(--ink-mute);font:inherit;
  font-size:18px;cursor:pointer;padding:6px 8px;line-height:1}
#wkmx:hover{color:var(--ink)}
/* фон под модалкой не едет: гасим оба скроллящих контейнера доски — саму
   страницу и открытую страницу нити (см. lock() в WORK_MODAL_JS) */
html.wkmlock,html.wkmlock body,html.wkmlock .detail{overflow:hidden}
@media (max-width:700px){
  /* на телефоне модалка почти во весь экран: поля по краям — только чтобы было
     видно, что это слой поверх доски, и чтобы читался тап «мимо» */
  #wkmodal{padding:14px 8px}
  #wkmodal .wkmpane{max-width:none;border-radius:10px}
  #wkmodal .wkmbody{padding:2px 14px 16px}
  #wkmx{padding:8px 10px;font-size:19px}
}
/* тап по лицу = проваливание; в модалке карточка уже развёрнута, и палец там
   ищет квадратики, а не «открой ещё раз» */
.wkcard[data-wkopen]{cursor:pointer}
.wkcard[data-wkopen]:hover{border-color:var(--line-2)}
#wkmodal .wkcard[data-wkopen],#wkmodal .wkcard[data-wkopen]:hover{
  cursor:default;border:none;border-radius:0;padding:0}
/* строка состояния — ГЛАВНЫЙ текст лица: чей ход и что происходит сейчас.
   Крупнее и ярче всего вокруг (чипы 10.5px тихие, описание 12.5px dim), потому
   что читают карточку ради неё. Цвет — по роли кита: «твой ход» тёплым c2
   (согласование), приёмка зелёным ok (сделанное), живая работа полным инком,
   невзятая — тихо. Точка перед текстом = свежий пруф-чек (шов 3). */
.wkstate{font-size:13.5px;line-height:1.5;color:var(--ink);margin:9px 0 0;
  overflow-wrap:anywhere}
.wkstate.prop{color:var(--c2)}
.wkstate.rev{color:var(--ok)}
.wkstate.open,.wkstate.done{color:var(--ink-faint)}
/* ТИШИНА ВЗЯТОЙ (работа 39) — тревожным warn, тем же, каким доска метит
   сегодняшний дедлайн: это не ошибка (краснить нечего) и не новость, а вещь,
   на которую человеку надо посмотреть. Ярче остывшего инка намеренно: карточка
   молчащей работы обязана цеплять глаз в ленте живых */
.wkstate.quiet{color:var(--warn)}
.wkstat.quiet{color:var(--warn)}
.wkfrdot{display:inline-block;width:6px;height:6px;border-radius:50%;
  margin-right:7px;vertical-align:middle;background:var(--c1);flex:none;
  box-shadow:0 0 0 1px var(--c1),0 0 7px 1px var(--c1-soft);
  animation:stpulse 1.4s ease-in-out infinite}
.wkhead{display:flex;justify-content:space-between;align-items:baseline;
  gap:4px 10px;flex-wrap:wrap}
/* обёртка имени .wknmw держит кегль/кламп на номере И названии разом (фикс 10
   работы 22: «22 · Название» — номер тем же кеглем и инком, что заголовок);
   внутри номер .wknmn — свой span ВНЕ редактируемого .wknm, чтобы правка
   имени не утащила его в текст */
.wknmw{font-weight:600}
/* ЛИЦО: название ПЕРВОЙ СТРОКОЙ ВО ВСЮ ШИРИНУ (решение 30.07, фикс 10 работы 21,
   смотрел с телефона: чипы делили шапку пополам, и на 390px имя ужималось в
   «Чернови передач собира…»). Максимум две строки, дальше многоточие. В полном
   виде клампа нет: там имя читают целиком и там же правят кликом */
.wkcard:not(.closed) .wkhead{display:block}
.wkcard:not(.closed) .wknmw{display:-webkit-box;-webkit-line-clamp:2;
  -webkit-box-orient:vertical;overflow:hidden;font-size:14px;line-height:1.35}
/* разворот клампа — и когда имя правится прямо на лице: кламп теперь на
   обёртке, contenteditable-потомок сам её не разожмёт */
#wkmodal .wknmw,.wknmw:has(>.wknm[contenteditable="true"]){display:block;
  overflow:visible;-webkit-line-clamp:none}
/* чипы шапки переносятся на новую строку, а не вылезают вбок (единый слой:
   статус-бейдж + «взял:» удлинили ряд — в две колонки он тёк в соседа) */
.wkchips{display:inline-flex;flex-wrap:wrap;justify-content:flex-end;gap:6px;
  flex-shrink:1;min-width:0}
/* ОДНА тихая мета-строка под названием — и на большой карточке, и на компакте
   нити: статус · срок · свежесть, руки (★ и ↗) правым краем; номер из меты
   ушёл в заголовок (фикс 10 работы 22).
   Цель — влезть в строку на 390px, поэтому внутри неё всё без пилюль */
.wkmeta{display:flex;align-items:center;flex-wrap:wrap;gap:6px 9px;
  margin:7px 0 0;font-family:var(--mono);font-size:10px;color:var(--ink-faint)}
.wkmr{display:inline-flex;align-items:center;gap:6px;margin-left:auto;flex:none}
/* поставленный срок на лице — не пилюля, а такая же тихая мета; рука цела
   (клик открывает календарь), подсказка о ней — пунктир на ховере */
.wkmeta .wkdl{border:none;padding:0;font-size:10px}
.wkmeta .wkdl[data-dl]:hover{border:none;
  text-decoration:underline dashed var(--ink-mute);text-underline-offset:3px}
.wkmeta .wkdl input{font-size:10px;width:104px}
/* паспортные чипы (дом · «взял» · постановка срока) уехали в полный вид —
   по левому краю, как всё в модалке */
.wkfchips{justify-content:flex-start;margin:0 0 6px}
/* …и там они НЕ пилюли, а продолжение шапки (решение 30.07, фикс 7 работы 22):
   ОДНА тихая строка тем же мелким моно, что мета над ней, куски разделены
   точкой. Овалами этот ряд читался вторым этажом кнопок — особенно «взял:
   имя · время», у которого рамка растягивалась на пол-экрана. */
#wkmodal .wkfchips{gap:0 7px;margin:8px 0 0;font-family:var(--mono);
  font-size:10.5px;color:var(--ink-faint)}
#wkmodal .wkfchips>*{border:none;border-radius:0;padding:0;font-size:10.5px;
  color:var(--ink-faint);white-space:normal}
#wkmodal .wkfchips>*+*::before{content:'·';margin-right:7px;
  color:var(--ink-mute)}
/* рука «+ дедлайн» цела — про неё говорит пунктир по наведению, как у всякого
   правимого текста доски, а не рамка */
#wkmodal .wkfchips .wkdl[data-dl]:hover{border:none;color:var(--ink);
  text-decoration:underline dashed var(--ink-mute);text-underline-offset:3px}
/* «ведёт: …» — вторая строка того же тихого блока, вплотную к чипам */
#wkmodal .wklead{margin:4px 0 0;font-size:10.5px;color:var(--ink-faint)}
/* ── ШАПКА МОДАЛКИ ДЫШИТ (решение 31.07, фикс 11 работы 25: «сверху кучно») ────
   На лице карточки эти строки стоят тесно намеренно — там верх читают одним
   взглядом. В модалке под ними живут ещё паспорт, «ведёт» и описание, и весь
   верх слипался в одно пятно. Даём ярусы и разную громкость: ИМЯ · что
   происходит сейчас · паспортное. Режем воздухом, а не линиями — линий в
   карточке и так довольно. Паспортное (дом · срок · кто взял · ведущая нить)
   уходит на шаг тише инком: его читают, когда уже провалились в работу, а не
   на входе. */
#wkmodal .wkmeta{margin-top:10px}
#wkmodal .wkstate{margin-top:15px}
/* воздух, который держали чипы, достался свёртке паспорта (фикс 3 работы 26);
   внутри неё чипы жмутся к своей подписи, как строки плана к «план» */
/* ПАСПОРТ И ПЛАН — ОДНОЙ ГРУППОЙ (фикс 7 работы 26): два однострочных
   спойлера стояли через два больших яруса и ели вертикаль пустотой. Воздух
   остался ТОЛЬКО перед группой, внутри неё строки идут подряд — читается как
   две тихие подписи, а не как два этажа */
#wkmodal .wkpass{margin-top:14px}
#wkmodal .wkplan{margin-top:0}
#wkmodal .wkpass summary,#wkmodal .wkplan summary{padding:1px 0}
/* ПАСПОРТ-ТАБЛИЧКА (фикс 5 работы 26): строки «метка → значение». Метка —
   тихий моно-капс кита (мотив .islbl/.wkfixlbl) в колонке одной ширины, чтобы
   значения стояли по одной вертикали и глаз шёл вниз по левому краю; значение —
   обычный инк, оно тут главное. Ряд не сетка: на 390px длинное «взял» должно
   переноситься внутри своей колонки, а не ломать таблицу. */
.wkpassb{margin:6px 0 0}
.wkprow{display:flex;gap:10px;align-items:baseline;padding:2px 0}
.wkpk{flex:none;width:58px;font-family:var(--mono);font-size:9px;
  letter-spacing:.18em;text-transform:uppercase;color:var(--ink-mute)}
.wkpv{flex:1;min-width:0;font-size:12px;color:var(--ink-dim);
  overflow-wrap:anywhere}
/* чип дедлайна внутри таблички — не пилюля, а такой же текст со своей рукой:
   пунктир по наведению говорит, что он правится (мотив всего правимого доски) */
/* border-WIDTH, а не border:none: ниже `.wkdl.add` возвращает пунктир стилем,
   и обнулённая одним словом рамка вернулась бы к нему толщиной по умолчанию —
   «поставить» снова читалось бы пилюлей в таблице */
.wkpv .wkdl{border-width:0;padding:0;font-size:12px;color:var(--ink-dim)}
.wkpv .wkdl[data-dl]:hover{border:none;color:var(--ink);
  text-decoration:underline dashed var(--ink-mute);text-underline-offset:3px}
/* описание — тихая строка, не второй заголовок (фикс 11) */
#wkmodal .wkdesc{margin-top:16px;font-size:12px;color:var(--ink-mute)}
/* ── МОДАЛКА БЕЗ КРЕСТИКОВ (решение 31.07, фикс 13 работы 25) ─────────────────
   Органы правки плана из модалки убраны: крестик пункта и «+ пункт» не
   рисуются вовсе, правимый текст перестаёт обещать правку пунктиром и
   курсором. Жест закрыт и в обработчике — рисунок и поведение должны говорить
   одно (см. WORK_JS). Карточки вкладки «работа» это не касается: там правка
   рукой осталась, и правило живёт под #wkmodal. */
#wkmodal .wkx,#wkmodal .wkadd{display:none}
#wkmodal .wki .wkt,#wkmodal .wknm,#wkmodal .wkdt{cursor:default}
#wkmodal .wki .wkt:hover,#wkmodal .wknm[data-title]:hover,
#wkmodal .wkdt:hover{text-decoration:none}
#wkmodal .wkitems{margin-top:15px}
.wkproj,.wkdl,.wktaken{font-family:var(--mono);font-size:10.5px;
  padding:2px 8px;border:1px solid var(--line);border-radius:10px;
  color:var(--ink-dim);white-space:nowrap}
/* чип «кто взял» (единый слой, шов 1): тот же пилюль, но тёплый акцент —
   исполнитель читается отдельно от проекта/дедлайна */
.wktaken{border-color:var(--c1-ring);color:var(--ink);cursor:default}
/* статус работы (единый слой шов 2; разгружен фиксом 10 работы 21) — НЕ пилюля,
   а ровно та же тихая строка, что под карточкой нити (.pjstatus): цветная точка
   плюс слово мелким капсом, без рамки и фона. Рамка спорила с названием за
   внимание и на 390px одна выталкивала мету во вторую строку. Цвет несёт точка,
   подпись у всех состояний одинаково тихая */
.wkstat{font-family:var(--mono);font-size:9.5px;letter-spacing:.1em;
  text-transform:uppercase;display:inline-flex;align-items:center;gap:6px;
  color:var(--ink-mute);white-space:nowrap;flex:none}
.wkstat .dot{width:6px;height:6px;border-radius:50%;flex:none;
  background:var(--ink-faint)}
/* «в работе» — взята, но по ней тихо: ТОЧКИ НЕТ (фикс 9 работы 22). Раньше тут
   пульсировал кобальт, и остывшая сутки работа выглядела живее некуда. Точка на
   доске = живое движение, и другого значения у неё быть не должно */
/* «агент работает» (фикс 5 работы 21) — ровно плашка нити st-work: зелёная
   точка и есть весь сигнал. Пульса ей не даём: у нитей его нет, а два разных
   пульса в одном ряду читались бы как два разных события */
.wkstat.work .dot{background:var(--ok)}
/* «на проверке» — ждёт руки человека, нейтрально */
.wkstat.rev .dot{background:var(--ink-mute)}
/* свежесть работы (фикс 6): «5 мин» с последнего жеста — тише всех чипов,
   это фон, а не свойство; тот же кегль, что у номера работы */
.wkage{font-family:var(--mono);font-size:10px;color:var(--ink-faint);
  white-space:nowrap;align-self:center}
/* ★ работы (фикс 7) — та же круглая кнопка, что у нити и артефакта; в ряду
   чипов ей нужен только вертикальный центр */
.wkfav{align-self:center}
.wkdl.warn{border-color:var(--warn);color:var(--warn)}
.wkdl.bad{border-color:var(--bad);color:var(--bad)}
.wkdl[data-dl]{cursor:pointer}
.wkdl[data-dl]:hover{border-style:dashed;color:var(--ink)}
.wkdl.add{border-style:dashed;color:var(--ink-faint)}
.wkdl input{background:none;border:none;outline:none;color:inherit;
  font-family:var(--mono);font-size:10.5px;width:112px;padding:0;
  color-scheme:dark;cursor:pointer}
html[data-palette="sand"] .wkdl input{color-scheme:light}
.wkdesc{display:flex;gap:8px;align-items:flex-start;
  font-size:12.5px;color:var(--ink-dim);margin:8px 0 0}
.wkdt{flex:1;outline:none;min-height:1em}
.wkdt:empty::before{content:'+ описание';color:var(--ink-faint);
  font-family:var(--mono);font-size:11px}
.wkitems{margin:10px 0 0}
.wki{display:flex;gap:8px;padding:5px 0;font-size:13px;
  align-items:flex-start}
/* ── СОСТОЯНИЕ НЕСЁТ САМА ЦИФРА (решение 31.07, фикс 14 работы 25) ────────────
   Модель упрощена и ЗАМЕНЯЕТ галочки фиксов 10/12: номер пункта — и якорь
   диктовки («чекни третий»), и его состояние. Одна вещь вместо трёх:
     голая цифра   — согласован, ещё не сделан;
     цифра в канте — сделал исполнитель, ждёт руки (нейтральный кружок);
     цифра в зелёном кружке — принято рукой, текст гаснет и черкается.
   Кружок — КАНТ, а не заливка: цифру должно быть видно, ею человек диктует.
   Ширина фиксированная (фикс 12): колонка одна на все пункты, иначе двузначный
   номер двигает тайтл и список идёт лесенкой. */
.wkn,.wlmark{flex:none;width:18px;height:18px;margin-top:1px;
  display:inline-flex;align-items:center;justify-content:center;
  border:1px solid transparent;border-radius:50%;
  font-family:var(--mono);font-size:10px;line-height:1;
  color:var(--ink-faint);user-select:none}
.wki.don .wkn,.wlmark.don{border-color:var(--line-2);color:var(--ink-dim)}
.wki.don.acc .wkn,.wlmark.acc{border-color:var(--ok);color:var(--ok)}
/* КРУЖОК — ЧИСТЫЙ ИНДИКАТОР (решение 31.07, работа 27). Тап по кружку сделанного
   пункта был последним пунктовым жестом руки на доске — и подвёл: три пункта
   приняты случайными нажатиями. Ни курсора, ни подсветки по наведению, ни
   тап-накладки под палец у него больше нет: обещать жест, которого не должно
   быть, — врать пальцу. Приёмка идёт словом в сессию (см. WORK_JS). */
/* СДЕЛАННОЕ ГАСНЕТ НЕ СРАЗУ (решение 31.07, фикс 10 работы 25): пункт в канте
   ещё ждёт руки — это живое, и выцветать ему рано. Гаснет и перечёркивается
   ровно принятое и всё в закрытой работе, где ждать уже нечего. */
.wki.don.acc,.wkcard.closed .wki.don{color:var(--ink-faint)}
.wki.don.acc .wkt,.wkcard.closed .wki.don .wkt{text-decoration:line-through}
/* сделанный пункт правке не поддаётся (фикс 5 работы 22) — курсор не обещает */
.wki.don .wkt{cursor:default}
.wkt{flex:1;outline:none}
/* тайтл + описание пункта (решение 30.07): столбик занимает место, где раньше
   тянулся сам .wkt — ряд не меняет геометрию; описание тише и мельче тайтла,
   переводы строк из work.md доживают до глаза через pre-line */
.wktx{flex:1;min-width:0}
.wktx .wkt{display:block}
.wkd{font-size:11.5px;line-height:1.5;color:var(--ink-dim);margin:3px 0 0;
  white-space:pre-line;overflow-wrap:anywhere}
/* принятый пункт выцветает целиком: иначе описание светило бы ярче
   зачёркнутого тайтла над ним */
.wki.don.acc .wkd,.wkcard.closed .wki.don .wkd{color:var(--ink-faint)}
.wknm{outline:none}
/* без карандашей (решение 16.07): текст правится кликом — подсказка пунктиром.
   Сделанные пункты из этого выключены (фикс 5 работы 22): подчёркивание по
   наведению обещало бы правку, которой у них больше нет */
.wkcard:not(.closed) .wki:not(.don) .wkt,.wkcard:not(.closed) .wkdt,
.wknm[data-title]{cursor:text}
.wkcard:not(.closed) .wki:not(.don) .wkt:hover,
.wkcard:not(.closed) .wkdt:hover,
.wknm[data-title]:hover{
  text-decoration:underline dashed var(--ink-mute);text-underline-offset:3px}
.wkt[contenteditable="true"],.wkdt[contenteditable="true"],
.wknm[contenteditable="true"]{color:var(--ink);text-decoration:none;
  border-bottom:1px dashed var(--ink-mute)}
.wkx{color:var(--ink-faint);cursor:pointer;font-size:18px;line-height:1;
  padding:0 6px;flex:none}
.wkx:hover{color:var(--bad)}
.wkadd{font-family:var(--mono);font-size:12px;color:var(--ink-faint);
  cursor:pointer;padding:8px 0 0;user-select:none}
.wkadd:hover{color:var(--ink)}
/* ГЕЙТ СОГЛАСОВАНИЯ (работа 44) — «да» кнопкой. Своей краски у блока нет: он
   берёт .abtn.primary, ту самую амберную пилюлю, которой кит метит «твой ход»
   на карточке нити. Разметка одна на карточку работы и на стол, значит и
   правила одни — иначе гейт выглядел бы в двух местах как два разных жеста.
   Волосяная линия сверху отделяет решение от чтения: выше — что согласовывать,
   ниже — чем. Подпись рядом с кнопкой — не украшение, а обещание: что именно
   ляжет в журнал и что случится с работой после нажатия. */
.wkgate{display:flex;flex-wrap:wrap;align-items:center;gap:10px 14px;
  margin-top:13px;padding-top:12px;border-top:1px solid var(--line)}
button.wkyes{-webkit-appearance:none;appearance:none;cursor:pointer;
  padding:8px 20px;font-size:12px}
button.wkyes[disabled]{opacity:.45;pointer-events:none}
/* «отправить строителя» — та же кнопка, но не primary: гейт подписывают, а
   гонца просто посылают, и амбер тут был бы вторым «главным» в одной строке */
button.wksend{-webkit-appearance:none;appearance:none;cursor:pointer;
  padding:8px 20px;font-size:12px}
button.wksend[disabled]{opacity:.45;pointer-events:none}
.wkgatew{flex:1;min-width:170px;font-family:var(--mono);font-size:10.5px;
  line-height:1.55;color:var(--ink-faint)}
/* «нет» у предложенного пункта — пилюля со СЛОВОМ, не крестик: крестик рядом с
   «да» читался бы как «удалить строку», а это ответ человека, и он тоже идёт в
   журнал. Пунктир — тот же язык, что у кружка предложенного пункта. */
.wkno{flex:none;align-self:flex-start;margin-top:1px;padding:1px 9px;
  font-family:var(--mono);font-size:10.5px;line-height:1.6;
  color:var(--ink-faint);border:1px dashed var(--line-2);border-radius:999px;
  cursor:pointer;user-select:none}
.wkno:hover{color:var(--bad);border-color:var(--bad)}
.wkno[data-busy]{opacity:.4;pointer-events:none}
.wkadd .nurl{width:100%;padding:5px 9px;font-size:12px}
.wkcard.closed .wkx,.wkcard.closed .wkadd{display:none!important}
/* ── ГАЛОЧЕК В РИСУНКЕ НЕТ (решение 31.07, фикс 14 работы 25) ─────────────────
   Две отметки (✓ исполнителя и ✓✓ руки, работа 22) сказали своё и уступили
   место кружку вокруг цифры: одна вещь на ряд вместо трёх, и колонка состояния
   схлопнулась — тайтлы встали по одной вертикали сами, без подпорок.
   УЗЛЫ ОСТАЮТСЯ В РАЗМЕТКЕ и не удалены: на них держится контракт с сервером
   (индекс пункта, ручки /work-check и /work-item-accept) и разбор чужого кода,
   который их ищет. Прятать дешевле, чем вырезать и потом искать, где отвалилось;
   жест переехал на .wkn явным селектором (см. WORK_JS).
   Маркера «здесь сейчас воркер» тут по-прежнему НЕТ: жеста-источника у доски
   не существует, рисовать его значило бы гадать. */
.wkbox,.wkacc{display:none}
/* свежий пруф-чек (единый слой, шов 3, финал): кружок только что чекнутого
   пункта обводит кобальтовое свечение и пульсит домашним мотивом stpulse —
   результат воркера виден ПРИЛЕТАЮЩИМ; старые чек-пункты спокойны. Свечение
   переехало с галочки на цифру вместе с самим состоянием (фикс 14). */
.wki.fresh .wkn{position:relative}
.wki.fresh .wkn::before{content:'';position:absolute;inset:-3px;
  border-radius:50%;box-shadow:0 0 0 1px var(--c1),0 0 7px 1px var(--c1-soft);
  animation:stpulse 1.4s ease-in-out infinite}
/* ── согласование плана на карточке (решение 30.07) ───────────────────────────
   Блок «план» — тот же спойлер, что журнал (тихая строка, разворот по клику):
   план не кричит, он лежит под рукой. Пункт, ПРЕДЛОЖЕННЫЙ агентом (`- [?]`),
   ещё не работа — пунктирный квадратик и тёплый c2 (роль кита «твой ход»),
   рядом кнопка «да». Клик по самому квадратику не чекает — сначала кивок. */
.wkplan{margin:10px 0 0}
/* ПАСПОРТ (фикс 3 работы 26) — тот же тихий спойлер, что план и журнал: три
   свёртки одного роста, ни одна не громче другой. Своих правил у него ровно
   два — отступ сверху и то, что чипы внутри уже уложены #wkmodal .wkfchips */
.wkpass{margin:10px 0 0}
.wkplan summary,.wkpass summary{font-family:var(--mono);font-size:10.5px;
  color:var(--ink-faint);cursor:pointer;user-select:none;padding:2px 0}
.wkplan summary:hover,.wkpass summary:hover{color:var(--ink-dim)}
.wkplines{font-size:12.5px;color:var(--ink-dim);line-height:1.55;
  padding:2px 0 2px 2px;border-left:1px solid var(--line);padding-left:10px;
  margin-top:4px}
.wkpp{margin:6px 0}
.wkpul{margin:6px 0;padding-left:17px}
.wkpul li{margin:2px 0}
/* ── «ПРЕДЛОЖЕНО» — ПРИЗНАК ПУНКТА, А НЕ ЛИНИЯ ПОД НИМ (решение 07.08, смотрел
   развёрнутую работу с телефона) ────────────────────────────────────────────
   Пунктир жил border-bottom'ом ПОД ТАЙТЛОМ и тянулся во всю ширину текстовой
   колонки. Пока чеклист читали только в модалке, это сходило; на узком экране
   он стал читаться РАЗДЕЛИТЕЛЕМ — глаз видел черту между заголовком и его
   описанием и решал, что пункт кончился. Знак врал про устройство списка и
   ничего не говорил про состояние.
   Переехал туда, где у пункта уже живёт ВСЁ состояние, — на цифру (фикс 14
   работы 25: «состояние несёт сама цифра»): пунктирный кант в c2, роль кита
   «твой ход». Квадратика по-прежнему нет, и рамка ничего пальцу не обещает —
   кружок к работе 27 стал чистым индикатором без жеста. Язык пункта от этого
   не разросся: у цифры было три вида (голая · в канте · в зелёном), стало
   четыре, и все четыре — одна и та же вещь.
   Тихий инк текста остаётся: предложенное — ещё не работа.
   Легенда носит те же классы (см. _WORK_LEGEND) и меняется вместе с этим. */
.wki.prop{color:var(--ink-dim)}
.wltx.prop{color:var(--ink-dim)}
.wki.prop .wkn,.wlmark.prop{border-style:dashed;border-color:var(--c2-ring);
  color:var(--c2)}
/* ТЕКУЩИЙ ПУНКТ (решение 31.07, фикс 15 работы 25): где агент прямо сейчас —
   сплошное подчёркивание кобальтом, домашним цветом «идёт работа». Три знака
   пункта не спорят: пунктир в c2 — «ждёт твоего да», сплошная кобальтовая —
   «здесь агент», кружок — «сделано/принято»; у каждого своя роль и свой цвет.
   Ставится только на НЕсделанный пункт живой работы (см. cursor в рендере) */
.wki.cur .wkt,.wltx.cur{border-bottom:1.5px solid var(--c1)}
.wki.cur{color:var(--ink)}
/* ТОТ ЖЕ МАРКЕР В СТРОКЕ ЛИЦА (фикс 4 работы 26): имя шага в «агент делает»
   подчёркнуто ровно так же, как сам пункт в чеклисте. Один язык на две
   поверхности — глаз связывает строку лица с местом в списке без слов */
.wkstate .wkcur{border-bottom:1.5px solid var(--c1)}
/* ── ЛЕГЕНДА ЗНАЧКОВ (решение 31.07, фикс 6 работы 26) ────────────────────────
   «?» — круг ровно того же роста и кегля, что кружок пункта: справка о языке
   написана этим же языком. Тихая до последнего: инк faint, никакого фона и
   никакой анимации — она ждёт, когда её спросят, а не зовёт. Раскрытая —
   строки «образец → что значит», образцы носят живые правила выше. */
/* «?» стоит В ПОЛОСКЕ модалки (фикс 7 работы 26), поэтому раскрытая легенда
   НЕ раздвигает ряд: панель уходит поверх содержимого плавающей поверхностью —
   той же, что панель модалки и выпадашка бургера (--bg-1, кант --line-2,
   радиус, та же тень). Ряд остаётся в одну строку, тело под ним не прыгает. */
.wkleg{position:relative}
.wkleg summary{width:18px;height:18px;display:inline-flex;align-items:center;
  justify-content:center;border:1px solid var(--line);border-radius:50%;
  font-family:var(--mono);font-size:10px;line-height:1;color:var(--ink-faint);
  cursor:pointer;user-select:none;list-style:none}
.wkleg summary::-webkit-details-marker{display:none}
.wkleg summary:hover{border-color:var(--line-2);color:var(--ink-dim)}
.wkleg[open] summary{border-color:var(--line-2);color:var(--ink-dim)}
.wklegb{position:absolute;top:calc(100% + 6px);left:0;z-index:3;
  width:max-content;max-width:min(340px,72vw);padding:10px 12px;
  border:1px solid var(--line-2);border-radius:8px;background:var(--bg-1);
  box-shadow:0 12px 28px rgba(0,0,0,.28)}
.wlrow{display:flex;align-items:baseline;gap:10px;padding:3px 0}
.wlm{flex:none;display:inline-flex;align-items:baseline;gap:7px;min-width:96px}
.wld{flex:1;min-width:0;font-size:12px;color:var(--ink-dim);
  overflow-wrap:anywhere}
.wltx{font-size:12.5px;color:var(--ink)}
.wltx.acc{color:var(--ink-faint);text-decoration:line-through}
/* ── фиксы на карточке (решение 30.07, работа 19) ─────────────────────────────
   Накидка руки у приёмки — не плановый шаг, и путать их нельзя. Заголовок —
   тихий капс кита (мотив .slabel/.islbl) с волосяной линией: она и режет
   карточку надвое, отделяя докинутое от согласованного. Сами пункты — ровно
   те же .wki: у фикса та же жизнь (чек с пруфом, правка, удаление) и та же
   сквозная нумерация, менять ему вид значило бы врать про это. */
.wkfixlbl{display:flex;align-items:center;gap:10px;font-family:var(--mono);
  font-size:9px;letter-spacing:.22em;text-transform:uppercase;
  color:var(--ink-mute);margin:12px 0 2px}
.wkfixlbl::after{content:'';flex:1;height:1px;background:var(--line)}
/* вкладки полки проекта (решение 17.07): нити · работы · идеи · заметки · закрыто */
.stabs{display:flex;gap:16px;flex-wrap:wrap;margin:16px 0 14px;
  border-bottom:1px solid var(--line)}
.stab{background:none;border:none;border-bottom:2px solid transparent;
  color:var(--ink-mute);font-family:var(--mono);font-size:12px;cursor:pointer;
  padding:4px 0 8px;letter-spacing:.02em}
.stab:hover{color:var(--ink-dim)}
.stab.on{color:var(--ink);border-bottom-color:var(--c1)}
.spane[hidden]{display:none}
/* тот же ряд — вкладками страницы нити (решение 30.07, работа 20): таймлайн ·
   работы · issues · кандидаты. С телефона ряд первым делом тыкают пальцем —
   даём честные 44px (HIG) и больше воздуха между целями, чем нужно мыши.
   ОДНОЙ СТРОКОЙ, ЛИСТАЕТСЯ ПАЛЬЦЕМ (решение 01.08, фикс 6 работы 28, скрин с
   телефона: «хочу таймлайн, работы, issues с кандидатами в одну строчку — и
   чтобы этот блок скроллился»). Четвёртая вкладка перевесила строку: ряду надо
   387px, а на 390px экране под ним 350 (у .dwrap свои поля по 20), — и
   «кандидаты · 75» уезжали второй строкой, отрывая ярлык от своего ряда.
   Лечение уже есть в доме: ровно так живут табы вида (.vtabs, MOBILE_CSS) —
   nowrap, горизонтальный скролл, полоса скрыта. Повторяем его буква в букву,
   чтобы два ряда вкладок листались одинаково. Воздух между целями ужат с 20 до
   14: на 430px этого хватает, чтобы все четыре стояли видимыми без свайпа, а
   44px под палец не тронуты — режем промежутки, не цели. */
@media (max-width:700px){
  .dtabs{gap:14px;flex-wrap:nowrap;overflow-x:auto;
    -webkit-overflow-scrolling:touch;scrollbar-width:none}
  .dtabs::-webkit-scrollbar{display:none}
  .dtabs .stab{white-space:nowrap;flex:none;
    min-height:44px;padding:4px 0 6px;font-size:12.5px}
}
.shempty{color:var(--ink-faint);font-size:12.5px;padding:8px 0}
/* работы горизонтальной лентой блоков на полке (решение 17.07): видно прогресс.
   align-items:flex-start — блоки по своему контенту, не тянутся к самому
   высокому (иначе футер улетал в низ гигантской пустотой) */
.wkblkrow{display:flex;align-items:flex-start;gap:10px;overflow-x:auto;
  padding:2px 2px 10px;scrollbar-width:thin}
.wkblkrow::-webkit-scrollbar{height:7px}
.wkblkrow::-webkit-scrollbar-thumb{background:var(--line-2);border-radius:4px}
.wkblk{flex:0 0 250px;display:flex;flex-direction:column;
  border:1px solid var(--line);border-radius:8px;padding:12px 14px;
  cursor:pointer;outline:none}
.wkblk:hover,.wkblk:focus{border-color:var(--c1);background:var(--c1-soft)}
.wkblk.done{opacity:.5}
/* компакт разложен как лицо большой карточки (фикс 10 работы 21): название во
   всю ширину блока, под ним та же .wkmeta одной строкой, под ней состояние.
   Подвал со строкой ведущей нити отсюда ушёл — компакт и так стоит В нити */
.wkblktop{margin-bottom:2px}
.wkblknm{color:var(--ink);font-weight:600;font-size:13px;line-height:1.35;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;
  overflow:hidden}
/* то же состояние, что на большой карточке, только тише кеглем: блок узкий,
   и строка тут переносится на две-три (работа 21 — превью первых трёх пунктов
   отсюда ушло вместе с чеклистом с лица) */
.wkblk .wkstate{font-size:12.5px;line-height:1.45;margin:8px 0 0}
.wkblk .wkmeta{margin-top:6px}
/* те же блоки СТОЛБИКОМ во всю ширину — вкладка «работы» страницы нити (решение владельца
   30.07: с телефона горизонтальный слайдер неудобен, карточка обрезалась
   справа). Лента (.wkblkrow) осталась полке дома, где она стоит внутри
   широкого вида; здесь читают сверху вниз, как на общей вкладке работ.
   Сетка, а не флекс: у .wkblk своя flex-база 250px, и в колонке-флексе она
   стала бы ВЫСОТОЙ — grid этих свойств не читает, карточка растёт по тексту */
.wkblkcol{display:grid;grid-template-columns:minmax(0,1fr);gap:10px;
  padding:2px 0}
.wkblkcol .wkblk{width:100%}
.wkblkcol .wkblknm{-webkit-line-clamp:3}
/* ── РАБОТА НИТИ — СРАЗУ РАЗВЁРНУТАЯ (решение 07.08: «работа нити сразу видна
   развёрнутой») ─────────────────────────────────────────────────────────────
   Во вкладке «работы» страницы нити план и предложенные шаги надо ЧИТАТЬ, а не
   доставать проваливанием: работ у нити единицы, и они и есть её содержание.
   Полный вид тут не второй копией — это тот же .wkfull, что лежит внутри
   карточки вкладки «работа» (одна сборка, _work_full), только раскрытый.
   Площадка адресуется столбиком .wkblkcol, а НЕ .spane: полка дома живёт в
   таких же .spane, и правило «лицо = состояние, подробности проваливанием»
   (решение 30.07, работа 21) там остаётся в силе. */
.wkblkcol .wkfull{display:block}
/* палец — только на лице (название · мета · строка состояния): им целятся в
   модалку. Под развёрнутым текстом курсор-рука обещал бы жест, которого нет */
.wkblkcol .wkblk{cursor:default}
.wkblkcol .wkblktop,.wkblkcol .wkblk>.wkmeta,
.wkblkcol .wkblk>.wkstate{cursor:pointer}
/* наведение — только рамкой: заливка целого развёрнутого блока читалась бы
   выделением, а не приглашением ткнуть */
.wkblkcol .wkblk:hover,.wkblkcol .wkblk:focus{background:none}
/* правка текста внутри развёрнутого блока — те же подсказки, что на большой
   карточке (курсор-текст и пунктир по наведению); у закрытой работы жестов нет */
.wkblkcol .wkblk:not(.done) .wki:not(.don) .wkt,
.wkblkcol .wkblk:not(.done) .wkdt{cursor:text}
.wkblkcol .wkblk:not(.done) .wki:not(.don) .wkt:hover,
.wkblkcol .wkblk:not(.done) .wkdt:hover{
  text-decoration:underline dashed var(--ink-mute);text-underline-offset:3px}
.wkblkcol .wkblk.done .wkx,.wkblkcol .wkblk.done .wkadd{display:none!important}
/* ── ЧЕКЛИСТ ТУТ ЧИТАЮТ, А НЕ ПРОСМАТРИВАЮТ (решение 07.08, iPhone: «вёрстка ещё
   не супер удачная») ────────────────────────────────────────────────────────
   Развёрнутый вид приехал сюда с плотностью списка ВНУТРИ модалки — там его
   пробегают глазами, зная, что искать. На странице нити работ единицы, и их
   читают подряд сверху вниз с телефона: та же плотность стала кашей. Пункты
   шли сплошняком, описание одного слипалось с заголовком следующего, и границы
   пункта глаз не находил.
   Три правки, все — уже живущие на доске мотивы, ничего нового:
     · ВОЗДУХ И ВОЛОСЯНАЯ ЛИНИЯ МЕЖДУ пунктами — тот же --line, которым доска
       режет строки журнала (.wkjl). Именно между: над первым её нет, там уже
       кончился план, и лишняя черта читалась бы вторым дном блока;
     · ОПИСАНИЕ ТИШЕ ЗАГОЛОВКА. Раньше оба стояли ink-dim и звучали одним
       голосом; теперь пара «значение → подпись», как в паспорте (.wkpv/.wkpk):
       заголовок берёт полный инк, описание уходит в ink-mute;
     · ЦИФРА КРУПНЕЕ И БЛИЖЕ К ТЕКСТУ — ею диктуют («доделай третий»), и в
       кегле лица она обязана читаться, а не быть сноской.
   Правила висят на .wkblkcol — это площадка нити. Кухня работ и модалка со
   своей плотностью остаются как были: там правило «лицо = состояние» и другой
   способ чтения. */
.wkblkcol .wki{padding:9px 0;font-size:13.5px;line-height:1.5}
.wkblkcol .wki+.wki{border-top:1px solid var(--line)}
.wkblkcol .wki:not(.don) .wkt{color:var(--ink)}
.wkblkcol .wkd{color:var(--ink-mute);margin-top:5px}
.wkblkcol .wki>.wkn{width:20px;height:20px;font-size:11.5px}
/* ярче только у голой цифры (согласован, не сделан): у сделанного, принятого и
   предложенного цвет — часть их знака, и трогать его тут нельзя */
.wkblkcol .wki:not(.prop):not(.don)>.wkn{color:var(--ink-dim)}
@media (max-width:700px){
  /* ── КРЕСТИКОВ НА ТЕЛЕФОНЕ НЕТ (решение 07.08) ──────────────────────────────
     На узкой площадке «×» висел у самого края каждого пункта: тап-таргет
     мельче честных 44px (HIG), зато шума — по одному значку на строку, и всё
     это на поверхности, куда приходят ЧИТАТЬ план и соглашаться с ним, а не
     ковырять его. Прячем ровно как в модалке (#wkmodal .wkx) — жест никуда не
     делся: на десктопе этой же карточки он на месте. */
  .wkblkcol .wkx{display:none}
}
/* ── РАБОТЫ СТОЯТ ПОД ШАГАМИ ПЛАНА (работа 44, решение 07.08: «хочется чтобы
   работа что сейчас делается бралась в рамках куда идём») ───────────────────
   Заголовок группы обязан читаться как СТРОКА ПЛАНА, а не как ярлык списка,
   поэтому он и есть строка плана: тот же .step с тем же кружком, которым нить
   рисует свои шаги в таймлайне (закон 47). Второй язык для «сейчас» тут завести
   нельзя — человек ходит между таймлайном и работами одной нити, и знак должен
   быть один. Своего у группы ровно два правила: убитая соединительная линия (в
   таймлайне шаги идут подряд, здесь между ними стоят карточки — нитка тянулась
   бы сквозь них в пустоту) и тихая приписка прогресса под именем.
   Закрытый шаг гасим по имени — тем же приёмом, что закрытая карточка работы
   (.wkcard.closed): сделанное остаётся видимым, но перестаёт спорить за глаз с
   тем, чем нить занята сейчас. */
.wkblkcol .wkstep{padding:6px 0 2px}
/* воздух — только МЕЖДУ группами: заголовок стоит вплотную к своим карточкам и
   отбивается от чужих, иначе шаг читался бы подписью к тому, что над ним */
.wkblkcol .wkblk+.wkstep{margin-top:16px}
.wkstep .mc::after{display:none}
.wkstep.done .gt{color:var(--ink-mute)}
.wkstepp{display:block;font-family:var(--mono);font-size:10px;
  letter-spacing:.06em;color:var(--ink-faint);margin-top:4px}
/* ── КУДА РАБОТА ВЕДЁТ (работа 44) ──────────────────────────────────────────
   Первая строка полного вида: адрес «нить · шаг» тихим моно, как метка
   паспорта, и сам результат обычным инком — его читают. Слева волосяная
   отбивка кантом (--line-2, тот же кант, которым доска отделяет цитату пруфа):
   строка не спорит с заголовком работы и не читается её описанием.
   Метка «к чему» — моно-капс ключей паспорта (.wkpk): без неё цитата из плана
   висела бы неизвестно чем. Внутри нити адреса в строке нет — его уже сказали
   заголовок страницы и заголовок группы шага (см. _work_goes). */
.wkgoes{margin:4px 0 10px;padding-left:10px;
  border-left:2px solid var(--line-2)}
.wkgoa{display:block;font-family:var(--mono);font-size:10px;
  letter-spacing:.06em;color:var(--ink-faint);overflow-wrap:anywhere}
.wkgor{display:block;margin-top:3px;font-size:12.5px;line-height:1.5;
  color:var(--ink-dim);overflow-wrap:anywhere}
.wkgok{font-family:var(--mono);font-size:9px;letter-spacing:.22em;
  text-transform:uppercase;color:var(--ink-mute);margin-right:8px}
/* ведущая нить — только в полном виде и у закрытых (фикс 10 работы 21) */
.wklead{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:8px 0 0;
  font-family:var(--mono);font-size:11px;color:var(--ink-dim)}
.wklead.done{color:var(--ink-faint)}
.wkleadn{overflow:hidden;text-overflow:ellipsis;flex:1}
/* провал к ведущему агенту — круглая кнопка кита (.pjhold ✕/★ у нитей), но
   кобальтом: это вход в работу, а не второстепенный жест. Заменила длинную
   пилюлю с подписью (фикс 8 работы 21): та съедала строку меты на телефоне */
.wkdive{color:var(--c1);border-color:var(--c1-ring)}
.wkdive:hover{color:var(--c1);border-color:var(--c1);background:var(--c1-soft)}
.wkfoot{margin-top:auto;padding-top:10px;display:flex;
  justify-content:space-between;align-items:center}
/* на лице в подвале осталась одна кнопка «закрыть» — прогресс уехал в строку
   состояния; жмём её вправо, чтобы подвал не выглядел оборванным слева */
.wkfoot.one{justify-content:flex-end}
.wkprog{font-family:var(--mono);font-size:10.5px;color:var(--ink-faint)}
.wkdone{padding:4px 12px;font-size:11px}
.wkjrn{margin-top:10px}
.wkjrn summary{font-family:var(--mono);font-size:10.5px;
  color:var(--ink-faint);cursor:pointer;user-select:none;padding:2px 0}
.wkjrn summary:hover{color:var(--ink-dim)}
/* запись журнала бывает многострочной (работа 33: пруф = короткая строка,
   пустая строка, техника) — переводы строк доживают до глаза, иначе лог
   склеивался бы в одну кашу там, где агент писал абзацами */
.wkjl{font-family:var(--mono);font-size:10.5px;color:var(--ink-dim);
  padding:2px 0;border-bottom:1px solid var(--line);white-space:pre-line;
  overflow-wrap:anywhere}
.wkjl:last-child{border-bottom:none}
.wkclosed{margin-top:28px}
.wkclosed summary{font-family:var(--mono);font-size:11px;letter-spacing:.08em;
  text-transform:uppercase;color:var(--ink-faint);cursor:pointer;
  padding:4px 0;user-select:none}
.wkclosed summary:hover{color:var(--ink-dim)}
.wkclosed .wkgrid{margin-top:10px}
.wkclosed .wkblkcol{margin-top:10px}
.wkcard.closed{opacity:.55}
"""

WORK_JS = """
// вкладка «работа» (решение 16.07): квадратик = чек, клик по тексту = правка
// (заголовок/описание/пункт), крестик = удалить, закрыть/открыть — кнопкой.
function wkEdit(el, save){
  window.__wkEditing=true;
  el.contentEditable='true'; el.focus();
  document.getSelection().selectAllChildren(el);
  const fin=async ok=>{ el.contentEditable='false'; window.__wkEditing=false;
    if(ok) await save(el.textContent.trim());
    boardRefresh(); };
  el.onkeydown=ev=>{
    if(ev.key==='Enter'){ ev.preventDefault(); el.onblur=null; fin(true); }
    if(ev.key==='Escape'){ el.onblur=null; fin(false); } };
  el.onblur=()=>fin(true);
}
async function wkCall(url){
  try{
    const r=await fetch(url); const msg=await r.text();
    if(!r.ok) (window.deckToast||alert)(msg);
    return r.ok;
  }catch(err){ (window.deckToast||alert)('не вышло: '+err); return false; }
}
// как wkCall, но вердикт слышно ВСЕГДА: гейт согласования не просто правит
// файл — он уводит человека в сессию, и куда именно, знает только ответ
// сервера («переключил на сессию» · «поднимаю новую» · «нить не в этом
// проекте»). Проглотить его значило бы оставить человека гадать, где агент
async function wkSay(url){
  try{
    const r=await fetch(url); const msg=await r.text();
    (window.deckToast||alert)(msg);
    return r.ok;
  }catch(err){ (window.deckToast||alert)('не вышло: '+err); return false; }
}
document.addEventListener('click',async e=>{
  const t=e.target;
  // «завести»: форм на странице две (общая вкладка и вкладка нити) — поля
  // берём из СВОЕЙ, а не по id, иначе кнопка нити читала бы чужой инпут.
  // data-proj/data-thread есть только у формы нити: заведённая оттуда работа
  // сразу приписана дому И нити страницы и потому видна в том же списке,
  // откуда её завели (список нити сит по нити).
  // Даты в форме нет — дедлайн ставится чипом на карточке (/work-deadline)
  const go=t&&t.closest&&t.closest('.wkgo');
  if(go){
    const form=go.closest('.wkform');
    const ti=form.querySelector('.wknew');
    const txt=(ti.value||'').trim(); if(!txt) return;
    if(await wkCall('/work-add?t='+encodeURIComponent(txt)
                    +'&proj='+encodeURIComponent(form.dataset.proj||'')
                    +'&thread='+encodeURIComponent(form.dataset.thread||''))){
      ti.value='';
      (window.deckToast||alert)('работа заведена'); boardRefresh(); }
    return; }
  // дедлайн на карточке: клик по чипу → инлайн-календарь → /work-deadline
  const dlc=t.closest&&t.closest('.wkdl[data-dl]');
  if(dlc&&!dlc.querySelector('input')&&!window.__wkEditing){
    window.__wkEditing=true;
    const slug=dlc.dataset.dl, iso=dlc.dataset.iso||'';
    dlc.innerHTML='<input type="date" value="'+iso+'">';
    const inp=dlc.querySelector('input');
    const fin=async save=>{ inp.onchange=null; inp.onblur=null;
      window.__wkEditing=false;
      if(save&&inp.value!==iso){
        await wkCall('/work-deadline?f='+encodeURIComponent(slug)
                     +'&d='+encodeURIComponent(inp.value||'')); }
      boardRefresh(); };
    inp.onchange=()=>fin(true);
    inp.onblur=()=>fin(inp.value!==iso);
    inp.onkeydown=ev=>{ if(ev.key==='Escape'){ inp.onblur=null; fin(false); } };
    try{ if(inp.showPicker) inp.showPicker(); }catch(err){}
    return; }
  if(t&&t.classList&&t.classList.contains('wkdone')){
    const url=t.dataset.close
      ?'/work-close?f='+encodeURIComponent(t.dataset.close)
      :'/work-reopen?f='+encodeURIComponent(t.dataset.reopen);
    if(await wkCall(url)) boardRefresh();
    return; }
  if(window.__wkEditing) return;
  // МОДАЛКА — ЧТЕНИЕ И РУКА, НЕ РЕДАКТОР (решение 31.07, фикс 13 работы 25).
  // Планом управляют голосом в сессию: пункт кладёт агент вербом, «да» ему
  // говорит человек словом. Немой второй путь из модалки убран целиком, и
  // опаснее всех был крестик: удалённый пункт сдвинул бы сквозную нумерацию,
  // на которую уже сослался журнал («пункт 5 ✓») и по которой человек диктует.
  // Остаются чтение и рука: отметить сделанным, принять, поставить срок,
  // закрыть работу. На карточке вкладки «работа» все жесты живут как жили —
  // правило скоупнуто модалкой, а не вырезано из движка.
  if(t.closest&&t.closest('#wkmodal')&&t.closest(
      '.wkx,.wkadd,.wknm[data-title],.wkdesc[data-desc] .wkdt,'
      +'.wki:not(.don) .wkt')) return;
  // ГЕЙТ СОГЛАСОВАНИЯ (работа 44): «да» на план целиком и «нет» одному
  // предложению. Обработчик ОДИН на все площадки — карточка работы, блок нити,
  // модалка и стол зовут ту же ручку с тем же адресом; гейт один, дверей у него
  // несколько, и вести себя в них по-разному он не должен. В список запретов
  // модалки эти жесты не входят намеренно: она «чтение и рука», а это рука —
  // ровно того же рода, что «закрыть» работу.
  const yes=t.closest&&t.closest('.wkyes');
  if(yes){
    // гвоздь от второго нажатия: за кликом едут ЧЕТЫРЕ верба (agree · artifact
    // taken · take · return/spark), они не мгновенны, и повторный жмак успел бы
    // послать вторую цепочку — а в ней подъём ВТОРОЙ сессии по одной нити
    e.preventDefault(); e.stopPropagation();
    if(yes.disabled) return;
    yes.disabled=true; const was=yes.textContent; yes.textContent='согласую…';
    if(await wkSay('/work-agree?f='+encodeURIComponent(yes.dataset.yes)))
      boardRefresh();
    else { yes.disabled=false; yes.textContent=was; }
    return; }
  // «отправить строителя» — тот же гвоздь от второго нажатия, что у гейта: за
  // кликом едут dispatch · take · return/spark, и повторный жмак поднял бы
  // ВТОРУЮ сессию по одной нити (принцип №1)
  const snd=t.closest&&t.closest('.wksend');
  if(snd){
    e.preventDefault(); e.stopPropagation();
    if(snd.disabled) return;
    snd.disabled=true; const wasS=snd.textContent; snd.textContent='отправляю…';
    if(await wkSay('/work-send?f='+encodeURIComponent(snd.dataset.send)))
      boardRefresh();
    else { snd.disabled=false; snd.textContent=wasS; }
    return; }
  const no=t.closest&&t.closest('.wkno');
  if(no){
    e.preventDefault(); e.stopPropagation();
    if(no.dataset.busy) return; no.dataset.busy='1';
    if(await wkCall('/work-prop-drop?f='+encodeURIComponent(no.dataset.no)
                    +'&i='+encodeURIComponent(no.dataset.i))) boardRefresh();
    else delete no.dataset.busy;
    return; }
  // карточку ищем и среди блоков нити (.wkblk с data-wk): там полный вид
  // развёрнут прямо на площадке (решение 07.08), и жесты пунктов обязаны
  // работать с места — адрес у них тот же, data-wk + data-i
  const x=t.closest&&t.closest('.wkx');
  if(x){ const row=x.closest('.wki'), card=x.closest('.wkcard,.wkblk[data-wk]');
    if(await wkCall('/work-item-del?f='+encodeURIComponent(card.dataset.wk)
                    +'&i='+row.dataset.i)) boardRefresh();
    return; }
  const nm=t.closest&&t.closest('.wknm[data-title]');
  if(nm){
    wkEdit(nm, async v=>{
      if(v) await wkCall('/work-title?f='
        +encodeURIComponent(nm.dataset.title)+'&t='+encodeURIComponent(v)); });
    return; }
  const dt=t.closest&&t.closest('.wkdesc[data-desc] .wkdt');
  if(dt){ const box=dt.closest('.wkdesc');
    wkEdit(dt, async v=>{
      await wkCall('/work-desc?f='+encodeURIComponent(box.dataset.desc)
                   +'&t='+encodeURIComponent(v)); });
    return; }
  // :not(.don) — сделанный пункт правке не поддаётся (фикс 5 работы 22):
  // он уже история, на него сослался журнал; откат — верб исполнителя
  const wt=t.closest&&t.closest('.wkcard:not(.closed) .wki:not(.don) .wkt,'
    +'.wkblk[data-wk]:not(.done) .wki:not(.don) .wkt');
  if(wt){ const row=wt.closest('.wki'), card=wt.closest('.wkcard,.wkblk[data-wk]');
    wkEdit(wt, async v=>{
      if(v) await wkCall('/work-item-edit?f='
        +encodeURIComponent(card.dataset.wk)+'&i='+row.dataset.i
        +'&t='+encodeURIComponent(v)); });
    return; }
  const ad=t.closest&&t.closest('.wkadd');
  if(ad&&!ad.querySelector('input')&&!ad.dataset.busy){
    window.__wkEditing=true;
    ad.innerHTML='<input class="nurl" placeholder="новый пункт">';
    const inp=ad.querySelector('input'); inp.focus();
    // замок от даблклика: Enter и blur гонятся за один и тот же текст, а на
    // работе в review жест дописывает ФИКС — второй заход положил бы пункт
    // дважды и дважды соврал бы журналу (болячка 30.07: четыре «закрыта»)
    const done=async ok=>{ const v=inp.value.trim();
      if(ad.dataset.busy) return;
      ad.dataset.busy='1';
      window.__wkEditing=false; ad.textContent='+ пункт';
      if(ok&&v) await wkCall('/work-item-add?f='
        +encodeURIComponent(ad.dataset.add)+'&t='+encodeURIComponent(v));
      delete ad.dataset.busy;
      boardRefresh(); };
    inp.onkeydown=ev=>{
      if(ev.key==='Enter'){ inp.onblur=null; done(true); }
      if(ev.key==='Escape'){ inp.onblur=null; done(false); } };
    inp.onblur=()=>done(true);
    return; }
  // ПУНКТОВЫХ ЖЕСТОВ РУКИ НА ДОСКЕ БОЛЬШЕ НЕТ (решение 31.07, работа 27).
  // Тут стоял последний — тап по кружку сделанного писал «принято рукой». Он и
  // подвёл: три пункта приняты СЛУЧАЙНЫМИ нажатиями, и в журнале осталась
  // приёмка, которой по смыслу не было. Пункт — мелкая цель посреди читаемого
  // текста, промах по ней стоит записи в файл, а откатить приёмку с доски
  // нечем: цена ошибки и цена жеста разошлись.
  // Приёмка и «да» идут словом в сессию (решение 06), «сделано» — вербом
  // исполнителя с пруфом. Кружок остался ЧИСТЫМ ИНДИКАТОРОМ: ни курсора, ни
  // подсветки, ни обработчика. Ручка /work-item-accept на сервере жива и
  // принадлежит вербам — сервер не трогали, снялась только рука с доски.
  // Властные кнопки человека на месте: «закрыть» работы и «да» на плане.
});
document.addEventListener('keydown',e=>{
  // Enter в поле имени = «завести», но кнопку жмём СВОЕЙ формы
  const ti=e.target;
  if(e.key==='Enter'&&ti&&ti.classList&&ti.classList.contains('wknew'))
    ti.closest('.wkform').querySelector('.wkgo').click(); });
"""


# ── вкладка ISSUES (решение 30.07, работа 17): стол входящих человека ──────────
# Вкладка «работа» — кухня агентов: все работы, все списки, вся техничка. Стол —
# другое: ТОЛЬКО то, что ждёт руки человека, крупно и человеческим языком. Своих
# данных у стола нет, он вытаскивает из тех же work.md:
#   согласование — у работы есть шаги «- [?]»: агент предложил, ждёт «да»
#   верификация  — работа в status: review: сделана, ждёт приёма
# На согласовании план РАЗВЁРНУТ (не спойлер, как на карточке работы): прятать
# то, ради чего человек пришёл, — значит не показать. Приёмке же нужен не план,
# а дельта (фикс 11 работы 22): её карточка показывает ровно ЧТО ПРИНЯТЬ —
# счёт и тайтлы непринятого, без плана и пруфов.
# КНОПОК ПРИЁМА на столе по-прежнему нет (решение 30.07): приёмка — разговор, её
# удобнее надиктовать агенту словами, чем набрать кликами, и `done` ставит
# только рука человека у работы. А вот СОГЛАСОВАНИЕ кнопку получило (работа 44,
# решение 06): это типовой гейт с ответом «да/нет», а не разговор, и походом в
# чат ради одного слова он стоил трёх лишних переходов. Стол читают — и либо
# жмут «да», либо одним жестом проваливаются в ведущую сессию нити.
# ОДНО целевое действие (решение 30.07: «меня грузят кучей инфы, я плыву»): стол
# показывает крупно ПЕРВОЕ ожидание очереди и только его; остальные ждут
# строкой — тип и имя, — и разворачиваются в свою карточку по клику.
_ISSUE_NUM_RE = re.compile(r"^(\d+)")


def _issue_num(slug):
    """Номер работы из слага — очередь стола идёт по нему, от меньшего:
    первым делают то, что заведено раньше, и порядок не пляшет от пульса."""
    m = _ISSUE_NUM_RE.match(slug)
    return int(m.group(1)) if m else 10 ** 6


# Очередь стола (решение 30.07, работа 17 шаг 4) — сперва по роду дела, и только
# внутри рода по номеру: артефакты закрываются одним движением (скопировал —
# отправил или запустил), и держать их за чужой приёмкой значит держать
# человека; приёмка следом — работа уже сделана, ждёт подписи; согласование
# последним — оно самое долгое, там читают план.
_RANK_ARTIFACT, _RANK_VERIFY, _RANK_AGREE = 0, 1, 2


def _issue_foot_row(oc, note, go="", hand=""):
    """Подвал карточки стола: два перехода одним рядом, ОДИНАКОВЫМИ пилюлями —
    ровно «резюм» с карточки нити (.abtn.pjresume, решение 30.07). «Надиктовать →»
    ведёт в ведущую сессию нити (та же механика, что у ↗ на карточке
    работы; общий резолв — _work_lead_jump), «к работе →» — на кухню, к самой
    работе. Своей краски ни у одной нет: какой жест главный, говорит порядок, а
    не цвет. Прыгать в сессию некуда — вместо кнопки тихая подпись *note*:
    молчащая кнопка хуже отсутствующей, а почему некуда — в каждом случае своё,
    и врать одним текстом на всех нельзя. Работы может не быть вовсе (артефакт
    живёт и без неё) — тогда «надиктовать» занимает ряд целиком.
    .isgo на второй — не вид, а ручка для делегата в ISSUES_JS.
    *hand* — жесты РУКИ человека (★ и «забрал ✓» у артефакта): они правят
    паспорт, а не переносят взгляд, поэтому идут своей группой в хвосте ряда и
    не растягиваются наравне с переходами."""
    jump = ('<button type="button" class="abtn pjresume" onclick="{0}">'
            'к сессии →</button>'.format(oc) if oc else
            '<span class="isasleep">{0}</span>'.format(esc(note)))
    tail = ('<button type="button" class="abtn pjresume isgo" data-go="{0}">'
            'к работе →</button>'.format(esc(go)) if go else "")
    return '<div class="isfoot">{0}{1}{2}</div>'.format(jump, tail, hand)


def _issue_foot(slug, oc, bound):
    """Подвал карточки работы: подпись честная — у работы с нитью агент спит,
    у работы без нити его ещё и не было."""
    return _issue_foot_row(oc, "агент спит — открой сессию нити" if bound
                           else "работу ещё никто не взял", slug)


# имя жеста одно на карточку и на свёрнутую строку: разъедься они словами —
# человек читал бы про два разных дела
_KIND_VERIFY = "прими работу"
_KIND_TAKE = "забери"
_KIND_ANSWER = "ответь"      # вопрос агента: рука не забирает, а отвечает


def _kind_agree(n):
    """Имя жеста согласования: «подтверди шаги · N» (решение 31.07, фикс 9 работы
    25). Раньше звалось «подтверди план» — и врало объёмом: план работы человек
    уже подтвердил, на «да» ждут отдельные ШАГИ, и сколько их, со свёрнутой
    строки видно не было. Число тут — чтобы решение мерилось до раскрытия
    карточки: один шаг и семь шагов — разный заход."""
    return "подтверди шаги · {0}".format(n)


def _issue_kind_html(slug, kind, word="работа"):
    """Шапка-тип с номером: человек диктует решения по номерам («в пятнадцатой
    первый шаг ок»), а номера на карточках не было — глазу не к чему привязать
    слово. «работа 15» идёт первой и ярче (тот же кегль, полный инк
    полужирно), тип жеста — следом. Слуг без номера — редкость, но шапка тогда
    честно молчит про номер, а не врёт «работа 1000000». *word* — что за вещь
    лежит на столе: работа или артефакт; нумерация у них своя, и путать их
    словом «работа» нельзя."""
    m = _ISSUE_NUM_RE.match(slug)
    if not m:
        return kind
    return '<span class="isknum">{0} {1}</span> · {2}'.format(
        word, int(m.group(1)), kind)


# рамка карточки, подписанная владельцем (30.07): короткий заголовок → проблема →
# где мы → шаги → где будем. План работы пишется теми же словами строками
# «Проблема: …» / «Где мы: …» / «Результат: …», и метка до двоеточия — не текст,
# а указатель: карточка отдаёт её тихому капсу, а инк оставляет самой фразе.
_PLAN_LINE_RE = re.compile(
    r"^(проблема|где мы|результат|где будем)\s*:\s*(.*)$", re.I)
# «результат» говорит про мир ПОСЛЕ шагов — на карточке он и стоит после них
_PLAN_TAIL = ("результат", "где будем")


def _issue_plan_split(plan):
    """План работы → (что читается до шагов, что закрывает карточку). Строку
    без метки рамки держим наверху: непонятое лучше показать раньше, чем
    спрятать в хвост."""
    head, tail = [], []
    for raw in plan.split("\n"):
        m = _PLAN_LINE_RE.match(raw.strip())
        is_tail = bool(m) and m.group(1).lower() in _PLAN_TAIL
        (tail if is_tail else head).append(raw)
    return head, tail


def _issue_plan_html(lines):
    """Тело блока плана на столе: метка рамки уходит в тихий капс, текст —
    обычным инком; буллеты собираются списком, прочее — абзацами, чтобы
    переводы строк из work.md дожили до глаза. Отдельно от _work_plan_html:
    рамка живёт на столе, и общий рендер карточки работы ей не нужен."""
    out, bullets = [], []
    for raw in lines:
        line = raw.strip()
        if line.startswith("- "):
            bullets.append(line[2:].strip()); continue
        if bullets:
            out.append('<ul class="wkpul">{0}</ul>'.format(
                "".join('<li>{0}</li>'.format(esc(b)) for b in bullets)))
            bullets = []
        if not line:
            continue
        m = _PLAN_LINE_RE.match(line)
        if m:
            out.append('<p class="wkpp"><span class="isplbl">{0}</span>{1}'
                       '</p>'.format(esc(m.group(1)), esc(m.group(2))))
            continue
        out.append('<p class="wkpp">{0}</p>'.format(esc(line)))
    if bullets:
        out.append('<ul class="wkpul">{0}</ul>'.format(
            "".join('<li>{0}</li>'.format(esc(b)) for b in bullets)))
    return "".join(out)


def _issue_agree(slug, title, plan, props, foot, quiet=""):
    """Карточка «подтверди план»: что агент собрался делать + шаги на кивок.
    Порядок — рамка владельца (30.07): заголовок, проблема и «где мы», шаги, и
    закрывающим блоком «где будем» — ради чего кивок и нужен.
    Шаг подписан тем же форматом: короткий заголовок-действие обычным инком,
    подробности под ним тише и мельче — читаются, когда захочется.
    Шаг остаётся видимо предложенным (.prop, пунктирный квадрат) — состояние
    его от кивка и не меняется до нажатия.

    КИВОК ТЕПЕРЬ КНОПКА (работа 44, пункт 7): «да» стоит прямо тут, ПОСЛЕ «где
    будем» — человек дочитывает, ради чего кивок, и жмёт. Разметка кнопки та
    же, что на карточке работы (_gate_block), и адрес тот же: гейт один, дверей
    у него две. У каждого шага рядом «нет» — отказ не должен требовать похода в
    чат, когда «да» делается пальцем.

    *quiet* — работа взята, но по ней тихо (работа 39). Тогда над шагами стоит
    предупреждение: человек собирается кивнуть агенту, которого, возможно, уже
    нет, — и знать это он должен ДО кивка, а не после."""
    steps = "".join(
        '<div class="wki prop" data-i="{0}"><span class="wkn">{1}</span>'
        '<span class="wkbox"></span>'
        '<div class="istx"><span class="wkt">{2}</span>{3}</div>{4}</div>'.format(
            i, i + 1, esc(txt),
            '<div class="isdesc">{0}</div>'.format(esc(d)) if d else "",
            _gate_no(slug, i))
        for i, txt, d in props)
    head, tail = _issue_plan_split(plan)
    pln = ('<div class="isplan">{0}</div>'.format(_issue_plan_html(head))
           if any(l.strip() for l in head) else "")
    res = ('<div class="isplan isres">{0}</div>'.format(_issue_plan_html(tail))
           if tail else "")
    # счёт шагов теперь несёт сама шапка («подтверди шаги · N»), и подпись над
    # списком его не повторяет: одно число, сказанное на карточке дважды, доска
    # уже проходила полосой «работы нити» — глаз ищет разницу там, где её нет
    return (
        '<div class="iscard" data-wk="{s}">'
        '<div class="iskind">{k}</div>'
        '<div class="isnm">{t}</div>{q}{p}'
        '<div class="islbl">шаги ждут твоего «да»</div>'
        '<div class="isitems">{r}</div>{e}{g}{f}</div>'.format(
            s=esc(slug), k=_issue_kind_html(slug, _kind_agree(len(props))),
            t=esc(title), p=pln, r=steps, e=res, f=foot,
            g=_gate_block(slug, len(props)),
            q=('<div class="isquiet">{0}</div>'.format(esc(quiet))
               if quiet else "")))


def _issue_verify(slug, title, items, journal, foot):
    """Карточка «прими работу»: ровно ЧТО ПРИНЯТЬ (фикс 11 работы 22).
    Описания и план сюда не идут — простыня топила само решение; они ждут в
    модалке работы по «к работе →». А вот «ЧТО СДЕЛАНО» вернулось (работа 33):
    подписывать сделанное, не видя, что именно сделано, человеку было нечем.
    Простыней, от которой уходил фикс 11, это не стало — на карточке стоит одна
    человеческая строка на пункт, техника ушла в «детали» (_work_proof_html).
    Принятые пункты не перечисляем (они уже приняты) — счёт-строка говорит, где
    рука остановилась. Счёт «из X» считает по договорённым, тем же числом, что
    лицо работы (_work_state), — карточка и лицо не должны спорить цифрами.
    Зонтичный пункт — текст совпал с названием работы (его штампует
    `tide work add`) — не перечисляем: заголовок карточки уже сказал то же
    самое, дубль путал глаз (фикс 12); счёт его по-прежнему учитывает.

    ФИКСЫ ПЕРЕЧИСЛЯЕМ НАРАВНЕ С ШАГАМИ (решение 01.08, фикс 4 работы 39: «что
    именно ты мне даёшь на приёмку? если ты даёшь мне фиксы, то я их не вижу»).
    До этого они шли сухим счётом «докинуто фиксов · N» — правило родилось, когда
    на карточке у всех пунктов были голые галочки и читать в них было нечего.
    С работы 33 у каждого пункта есть человеческая строка «что сделано», и счёт
    вместо неё стал прятать РОВНО ТО, что человек и просил сделать: фикс — это
    его собственная накидка, и подписывать её вслепую он не должен. Строка счёта
    осталась, но стала тем, чем и была на кухне, — тихим разделителем перед
    блоком фиксов."""
    acc = _work_accepted(items, journal)
    n_agreed = sum(1 for it in items if it[0] != "?")
    done = [(i, it) for i, it in enumerate(items) if it[0] == "x"]
    n_acc = sum(1 for i, _it in done if i in acc)
    cnt = ("принято {0} из {1}".format(n_acc, n_agreed) if n_acc
           else "сделано {0} — жду твоей руки".format(len(done)))
    umb = title.strip().casefold()
    todo = [(i, it) for i, it in done if i not in acc]
    # чем закрыт пункт (работа 33) — та же свёртка, что в чеклисте работы:
    # разметка одна, поэтому «что сделано» на столе и на кухне выглядит одинаково
    proofs = _work_proofs(journal)

    def _rows(pairs):
        """Пункты списком. Нумерация — сквозная по файлу (i+1): ею человек
        диктует и ею же его зовёт журнал, у фиксов она продолжает чеклист."""
        return "".join(
            '<div class="wki don"><span class="wkn">{0}</span>'
            '<span class="wkbox"></span>'
            '<div class="istx"><span class="wkt">{1}</span>{2}</div></div>'
            .format(i + 1, esc(it[1]), _work_proof_html(proofs.get(i + 1)))
            for i, it in pairs if it[1].strip().casefold() != umb)

    rows = _rows([(i, it) for i, it in todo if not it[3]])
    fxpairs = [(i, it) for i, it in todo if it[3]]
    fxrows = _rows(fxpairs)
    # спрятали зонтик — и показывать стало нечего, хотя счёт неполный: пустота
    # под счётом читалась бы как «принимать нечего». Тихая строка держит правду
    hush = ('<div class="islbl">остался итоговый чек</div>'
            if todo and not rows and not fxrows and n_acc < n_agreed else "")
    # разделитель — тот же, что в чеклисте работы (.wkfixlbl, волосяная линия и
    # тихий капс): один рисунок «дальше пошли накидки» на кухне и на столе.
    # Число — сколько показано ниже, а не сколько фиксов у работы вообще: цифра
    # обязана сходиться с тем, что человек видит под ней
    fx = ('<div class="wkfixlbl">фиксы · {0}</div>'
          '<div class="isitems">{1}</div>'.format(len(fxpairs), fxrows)
          if fxrows else "")
    # несделанное не прячем: человек подписывает работу целиком, и если
    # согласованный пункт остался пустым — он должен это увидеть до кнопки
    left = sum(1 for it in items if it[0] == " ")
    tail = ('<div class="isleft">ещё не сделано · {0}</div>'.format(left)
            if left else "")
    return (
        '<div class="iscard verify" data-wk="{s}">'
        '<div class="iskind">{k}</div>'
        '<div class="isnm">{t}</div>'
        '<div class="islbl">{c}</div>{r}{x}{l}{f}</div>'.format(
            s=esc(slug), k=_issue_kind_html(slug, _KIND_VERIFY), t=esc(title),
            c=esc(cnt),
            r=('<div class="isitems">{0}</div>'.format(rows) if rows
               else hush),
            x=fx, l=tail, f=foot))


# ── артефакты (решение 30.07, работа 17, шаг 4) ───────────────────────────────
# Артефакт — вещь, которую агент ПОДАЁТ человеку в руки: сообщение на
# отправку, команда на запуск, файл. Своей вкладки у него нет и не надо:
# это то же ожидание руки, что согласование и приёмка, и лежит оно на том же
# столе. Русло — файлы, как у работ: .tide/arcs/artifacts/NN-slug/artifact.md
# (формат согласован с вербами tide: движок пишет — доска читает). Каталога
# может не быть вовсе — стол это переживает молча.
# Взятое (status: taken) со стола уходит: стол показывает то, что ЖДЁТ, а не то,
# что было. Кроме одного случая — человек пометил подачу звездой (fav: yes):
# такое забранное ложится на полку «сохранённые» под столом (решение 30.07).
# Команду, которую запускаешь раз в месяц, забыть проще, чем найти заново.
ARTIFACTS_DIR = WORKS_DIR.parent / "artifacts"
_ART_META_RE = re.compile(r"(kind|status|fav|created|from-arc|work):\s*(.*)")
_ART_YES = ("yes", "y", "1", "true", "да")


def _artifact_read(f):
    """(подпись, мета, содержимое) из artifact.md. Подпись — H1: она говорит,
    что это и что с ним сделать. Мету берём только до первой секции, иначе
    строка «kind: …» внутри содержимого перебила бы паспорт. Журнал доска не
    читает — на столе он не нужен."""
    lines = f.read_text(encoding="utf-8").splitlines()
    title = next((l[2:].strip() for l in lines if l.startswith("# ")),
                 f.parent.name)
    meta, body, section = {}, [], ""
    for l in lines:
        if l.startswith("## "):
            section = l[3:].strip(); continue
        if not section:
            m = _ART_META_RE.match(l)
            if m:
                meta[m.group(1)] = m.group(2).strip()
            continue
        if section == "содержимое":
            body.append(l)
    return title, meta, "\n".join(body).strip()


# вид артефакта → (метка блока, скин типового блока, что копируем словами)
_ART_KINDS = {
    "command": ("команда", "code", "команду"),
    "message": ("сообщение", "text", "сообщение"),
    "file": ("файл", "path", "путь"),
    # ВОПРОС (шаг 6 работы 25, tide 1.0.45): агент встал и ждёт слова. Раньше
    # такое ждание жило только в чате — человек узнавал о нём, зайдя в сессию,
    # а доска не знала вовсе. Теперь это вещь на столе: подпись зовёт, кнопка
    # ведёт в сессию, а ОТВЕТ по-прежнему словом там (решение 06) — поля ввода
    # у карточки нет и быть не должно.
    "question": ("вопрос — ответь словом в сессии", "text", "вопрос"),
}
# что делает рука с этой вещью: забирает — или отвечает. Слово одно на шапку
# карточки, строку очереди и кнопку подвала, чтобы человек читал про одно дело
_ART_ASK = "question"


def _artifact_star(slug, fav):
    """★ на артефакте — ровно звезда карточки нити (_card_fav): залитая значит
    «оставлено себе», клик снимает; контурная — сохраняет. Разница только в
    русле: у нити список лежит в контрол-хоуме, у артефакта — строкой `fav:` в
    его же паспорте, потому что артефакт — вещь, а не адрес.
    Жест ЗАПЕРТ на один запрос (болячка 30.07, артефакт 100: повторный клик до
    свопа доски дописывал в журнал лишние строки): stopPropagation не пускает
    клик в делегаты стола, а замок busy глушит второй клик, пока первый едет, —
    замок живёт на узле и умирает вместе с ним при свопе boardRefresh, снимать
    руками нечего."""
    on, glyph = ("0", ICON_STAR_ON) if fav else ("1", ICON_STAR)
    title = "убрать из сохранённых" if fav else "сохранить у себя"
    s = re.sub(r"[\"'\\\n\r]", "", slug)
    oc = ("event.preventDefault();event.stopPropagation();"
          "if(this.dataset.busy)return false;this.dataset.busy='1';"
          "fetch('/artifact-fav?on={on}&f='+encodeURIComponent('{s}'))"
          ".then(function(r){{return r.text()}}).then(function(x){{deckToast(x);"
          "if(typeof boardRefresh==='function')boardRefresh();}})").format(
              on=on, s=s)
    return '<a class="pjhold" href="#" title="{0}" onclick="{1}">{2}</a>'.format(
        title, oc, glyph)


def _issue_artifact(slug, title, akind, content, foot, gate=""):
    """Карточка «забери»: содержимое видно сразу — разворачивать нечего,
    артефакт И ЕСТЬ содержимое. Копирование живёт ТАМ ЖЕ, где везде на доске, —
    ТИПОВЫМ блоком _copy_block, одним на стол и на заметки.
    Вид неизвестен — читаем прозой и всё равно показываем: подача не должна
    пропасть из-за опечатки в kind.
    Жесты руки — в подвале (см. _issue_foot_row): «забрал ✓» уводит подачу со
    стола, ★ оставляет её себе. Это не ответы агенту (те диктуют словами) —
    это законные кнопки человека, как «закрыть» у работы, и кит у них тот же.

    ВОПРОС (шаг 6 работы 25) — тот же артефакт, но рука с ним делает другое:
    шапка зовёт «ответь», а не «забери», и содержимое — не вещь на вынос, а сам
    вопрос. Поля ввода тут нет намеренно (решение 06): отвечают словом в
    сессию, дорогу туда даёт «к сессии →» в подвале.

    И БОЛЬШЕ У ВОПРОСА НЕТ НИЧЕГО (решение 01.08, фикс 7 работы 33: «вижу
    „добавить в избранное" зачем-то — не надо, я отвечаю через чат отсюда»).
    Ни звезды, ни «ответил ✓», ни ⧉ — вопрос не вещь: его не уносят к себе, не
    забирают и не копируют, на него отвечают. Каждая лишняя кнопка тут звала
    сделать руками то, что делается словом, и путала, чем вопрос закрывается.
    Остаются ровно текст и дорога в сессию. Артефакты ОСТАЛЬНЫХ видов (команда,
    файл, сообщение) руки не теряют: их забирают на самом деле.

    *gate* — исключение из «ни одной кнопки», и оно ровно одно (работа 44):
    вопрос-СОГЛАСОВАНИЕ, привязанный к работе с висящими «- [?]». Это тот же
    типовой гейт, что стоит на карточке работы, только спрошенный на столе
    (решение 07: «согласования не в планах, а в issue»), и отвечать на него
    походом в чат — ровно то трение, ради снятия которого кнопку и завели.
    Вопросы без такой привязки кнопки не получают: на них правда отвечают
    словом, и обещать им закрытие пальцем было бы враньём."""
    lbl, skin, what = _ART_KINDS.get(akind, ("подача", "text", "содержимое"))
    ask = akind == _ART_ASK
    if ask and gate:
        # подпись обязана совпадать с тем, что на карточке ЕСТЬ: «ответь словом
        # в сессии» над кнопкой «да» отправляло бы человека в чат мимо неё
        lbl = "вопрос-согласование — ответь кнопкой или словом в сессии"
    # у вопроса тот же типовой блок и та же типографика, но без ⧉: обёртка
    # .cblk оставлена — на ней висит отступ от подписи (.isgrid .cblk)
    body = ('<div class="cblk"><div class="cbin cbtext">{0}</div></div>'.format(
        esc(content)) if ask and content else
        _copy_block(content, skin, what) if content else
        '<div class="isnone">содержимое пустое</div>')
    return (
        '<div class="iscard artf{q}" data-af="{s}">'
        '<div class="iskind">{k}</div>'
        '<div class="isnm">{t}</div>'
        '<div class="islbl">{l}</div>{b}{g}{f}</div>'.format(
            q=" ask" if ask else "", s=esc(slug),
            k=_issue_kind_html(slug, _KIND_ANSWER if ask else _KIND_TAKE,
                               "вопрос" if ask else "артефакт"),
            t=esc(title), l=esc(lbl), b=body, f=foot, g=gate))


def _artifact_saved(slug, title, akind, content, star):
    """Забранный, но оставленный звездой — карточка полки, не стола. Ожидания в
    ней нет, значит нет и подвала переходов: сессия, которая её подала, давно
    ушла, а работа закрыта. Остаётся ровно то, ради чего человек нажал звезду, —
    подпись и содержимое с копиром. Звезда остаётся: полка должна отпускать."""
    _lbl, skin, what = _ART_KINDS.get(akind, ("подача", "text", "содержимое"))
    body = (_copy_block(content, skin, what) if content else
            '<div class="isnone">содержимое пустое</div>')
    return ('<div class="issv" data-af="{s}"><div class="issvtop">'
            '<span class="issvnm">{t}</span>{r}</div>{b}</div>'.format(
                s=esc(slug), t=esc(title), r=star, b=body))


def _artifact_rows(by_dir, work_by_num, keep=None, gates=None):
    """Ожидающие артефакты → строки очереди стола. Прыжок «надиктовать →» —
    по from-arc, тем же резолвом, что у работ (_work_lead_jump ждёт поле
    thread). Подпись, когда прыгать некуда, честная и разная: адреса нет ·
    нить не этого проекта · агент спит. «К работе →» появляется, только если
    артефакт назвал работу И она на кухне есть: ссылка в пустоту хуже, чем
    её отсутствие.
    Отдаёт ДВА списка: очередь стола и карточки полки «сохранённые» — забранное
    со звездой. Один проход по папке: и то и другое читается из одних файлов, и
    разводить их по двум обходам значило бы читать диск дважды. Полка идёт новым
    сверху — как весь поток доски.
    *keep* — сито дома (стол внутри нити показывает ожидания ТОЛЬКО своего
    проекта): предикат по мете артефакта, None — берём все.
    *gates* — номер работы → (слаг, сколько «- [?]» висит): вопрос, привязанный
    к такой работе, получает кнопку гейта (работа 44, см. _issue_artifact)."""
    out, saved = [], []
    for f in (sorted(ARTIFACTS_DIR.glob("*/artifact.md"))
              if ARTIFACTS_DIR.is_dir() else []):
        try:
            title, meta, content = _artifact_read(f)
        except OSError:
            continue
        if keep and not keep(meta):
            continue
        slug = f.parent.name
        fav = meta.get("fav", "").strip().lower() in _ART_YES
        akind = meta.get("kind", "")
        if meta.get("status", "new") != "new":
            # полка — РОВНО taken со звездой; new живёт на столе всегда
            # (звезда лишь заливается), забранное без звезды не рисуем
            # вовсе: стол не архив подач
            if fav and meta.get("status") == "taken":
                saved.append((_issue_num(slug), _artifact_saved(
                    slug, title, akind, content, _artifact_star(slug, True))))
            continue
        t, lead, oc = _work_lead_jump(
            {"thread": meta.get("from-arc", "")}, by_dir)
        if oc:
            note = ""
        elif not lead:
            note = "нить не указана — некуда прыгать"
        elif t is None:
            note = "нить {0} — не в этом проекте".format(lead)
        else:
            note = "агент спит — открой сессию нити"
        wnum = meta.get("work", "").strip()
        go = work_by_num.get(int(wnum)) if wnum.isdigit() else ""
        # У ВОПРОСА РУК В ПОДВАЛЕ НЕТ (решение 01.08, фикс 7 работы 33). Были
        # звезда и «ответил ✓» — обе лишние: избранное копит вещи, а вопрос не
        # вещь; «ответил ✓» же обещал, что вопрос закрывается кнопкой, тогда
        # как закрывает его РАЗГОВОР. Вопрос уводит со стола агент вербом,
        # увидев ответ в сессии, — там же, где ответ и прозвучал. Остальным
        # видам подачи руки оставлены: команду и файл человек правда забирает.
        ask = akind == _ART_ASK
        hand = ("" if ask else
                '<div class="ishand">{0}'
                '<button type="button" class="nbtn istake" data-take="{1}">'
                'забрал ✓</button></div>'.format(
                    _artifact_star(slug, fav), esc(slug)))
        # гейт на вопросе — только когда за ним стоит работа с висящими
        # предложениями: иначе кнопке нечего согласовывать
        gt = (gates or {}).get(int(wnum)) if wnum.isdigit() else None
        card = _issue_artifact(slug, title, akind, content,
                               _issue_foot_row(oc, note, go or "", hand),
                               gate=(_gate_block(*gt) if ask and gt else ""))
        out.append((_RANK_ARTIFACT, _issue_num(slug), "af|" + slug,
                    _issue_kind_html(slug, _KIND_ANSWER if ask else _KIND_TAKE,
                                     "вопрос" if ask else "артефакт"), title,
                    card, "artf ask" if ask else "artf"))
    saved.sort(key=lambda s: -s[0])
    return out, [html for _n, html in saved]


def _issue_row(key, kind, title, card, cls):
    """Ожидание из очереди — одной тихой строкой: тип жеста и имя работы.
    <details> вместо своего JS: раскрытие живёт в разметке, работает с
    клавиатуры и переживает своп автообновления (см. AUTOREFRESH_JS)."""
    return (
        '<details class="isrow{v}" data-row="{k}">'
        '<summary class="issum"><span class="iskind2">{d}</span>'
        '<span class="isnm2">{t}</span></summary>{c}</details>'.format(
            v=" " + cls if cls else "", k=esc(key), d=kind, t=esc(title),
            c=card))


def _issues_panel(threads=None, proj=None):
    """(html вкладки, сколько ждёт руки). Счётчик нужен ряду вкладок — читаем
    работы ОДИН раз и отдаём оба, чтобы заголовок и стол не разъехались.
    Нити своего проекта нужны за тем же, зачем панели работ: работа привязана
    к нити, и карточка прыгает в её ведущую сессию.
    На столе три рода ожиданий — артефакт «забери», приёмка, согласование
    (порядок очереди — _RANK_*). Наверх идёт РОВНО ОДНО, первое в очереди;
    остальные лежат списком строк под «ещё на столе». Счётчик вкладки считает
    всё, что ждёт руки, — свёрнутое тоже ждёт; полка «сохранённые» в счёт НЕ
    входит: забранное уже не ждёт, оно просто лежит под рукой.

    *proj* — тот же стол, но одним домом: вкладка issues внутри нити (решение владельца
    30.07, работа 20). Работу к дому вяжет её поле project: (_proj_match),
    артефакт — работа, которую он назвал (`work: NN`), а если не назвал —
    нить-родитель (`from-arc`) из нитей этого дома. Артефакт, не сказавший ни
    того, ни другого, ничьим домом не считается: приписать его наугад значит
    показать человеку в проекте чужую вещь. Панель дома идёт БЕЗ id="issues" —
    он принадлежит главной вкладке, и второй такой же в странице нити ломал бы
    и своп автообновления, и CSS."""
    if threads is None:
        sroot = WORKS_DIR.parents[2]
        threads = read_threads(sroot.name, sroot)
    by_dir = {t["dir"]: t for t in threads}
    rows, work_by_num, mine, gates = [], {}, set(), {}
    for hint, f in work_files():
        title, meta, _desc, items, journal, plan = _work_read(f)
        dirname = f.parent.name
        slug = _work_key(hint, dirname)
        own = _work_home(hint, meta)
        num = _issue_num(dirname)
        # КАРТА НОМЕРОВ — только по общей папке. Артефакт называет работу
        # числом (`work: 44`), а числа у проектов свои: `07` есть почти в каждом, и
        # на верфи. Пускать сюда работы соседних проектов значило бы иногда
        # уводить подачу к однономернóй чужой работе. Артефакты пока живут
        # только в общей папке, поэтому ничего и не теряется.
        if not hint:
            work_by_num[num] = slug
        st = meta.get("status", "open")
        # «к работе →» ведёт на кухню, где лежат ВСЕ работы — карту номеров
        # собираем до сита дома, иначе прыжок с чужой подачи упёрся бы в пустоту
        if proj and not _proj_match(own, proj):
            continue
        if not hint:
            mine.add(num)
        if st == "done":
            continue
        _t, lead, oc = _work_lead_jump(meta, by_dir)
        foot = _issue_foot(slug, oc, bool(lead))
        props = [(i, txt, d)
                 for i, (s, txt, d, _fx) in enumerate(items) if s == "?"]
        # одна работа может ждать дважды — и шагами на «да», и приёмкой:
        # это две разные карточки, каждая со своим местом в очереди
        if props:
            # гейт этой работы знают обе карточки стола — своя и вопрос,
            # который её назвал: кнопка у них общая (работа 44). Ключом опять
            # же только общая папка: артефакт-вопрос зовёт работу числом
            if not hint:
                gates[num] = (slug, len(props))
            rows.append((_RANK_AGREE, num, slug + "|0",
                         _issue_kind_html(slug, _kind_agree(len(props))), title,
                         _issue_agree(slug, title, plan, props, foot,
                                      quiet=_work_quiet(journal)), ""))
        if st == "review":
            rows.append((_RANK_VERIFY, num, slug + "|1",
                         _issue_kind_html(slug, _KIND_VERIFY), title,
                         _issue_verify(slug, title, items, journal, foot),
                         "verify"))
    keep = None
    if proj:
        dirs = set(by_dir)
        def keep(meta):
            wn = meta.get("work", "").strip()
            if wn.isdigit():
                return int(wn) in mine
            fa = meta.get("from-arc", "").strip()
            return bool(fa) and (fa in dirs or fa.rsplit("/", 1)[-1] in dirs)
    arows, saved = _artifact_rows(by_dir, work_by_num, keep, gates)
    rows += arows
    rows.sort(key=lambda r: (r[0], r[1]))
    if not rows:
        body = '<div class="isempty">ничего не ждёт твоей руки</div>'
    else:
        body = ('<div class="ismain"><div class="ishead">сейчас от тебя</div>'
                '{0}</div>'.format(rows[0][5]))
        rest = rows[1:]
        if rest:
            body += (
                '<div class="isrest">'
                '<div class="islbl isrestlbl">ещё на столе · {0}</div>{1}'
                '</div>'.format(
                    len(rest),
                    "".join(_issue_row(key, kind, title, card, cls)
                            for _r, _n, key, kind, title, card, cls in rest)))
    # полка под столом: забранное, помеченное звездой. Свёрнута — она не
    # ожидание, а память; пустой её не бывает вовсе (нет звёзд — нет блока)
    if saved:
        body += ('<details class="issaved">'
                 '<summary>сохранённые · {0}</summary>'
                 '<div class="issvgrid">{1}</div></details>'.format(
                     len(saved), "".join(saved)))
    if proj:
        return '<div class="isgrid">{0}</div>'.format(body), len(rows)
    return ('<div id="issues"><div class="isgrid">{0}</div></div>'.format(body),
            len(rows))


ISSUES_CSS = """
#issues{margin-top:22px}
.isgrid{display:flex;flex-direction:column;gap:14px}
/* карточка стола — тот же мотив, что wkcard, но с тёплой кромкой c2 («твой
   ход» по киту); приёмка ведётся зелёным ok — тем же, которым кит метит
   сделанное. Одна колонка на любой ширине: стол читают сверху вниз */
.iscard{border:1px solid var(--line);border-left:2px solid var(--c2);
  border-radius:6px;padding:16px 18px}
.iscard.verify{border-left-color:var(--ok)}
/* артефакт — третий цвет кита (c3): он не «твой ход» (c2, там решение) и не
   «принято» (ok), а вынос вещи из доски в руки */
.iscard.artf{border-left-color:var(--c3)}
/* ВОПРОС — обратно в c2 (шаг 6 работы 25): вынос вещи тут ни при чём, это
   ровно «твой ход» — агент встал и ждёт слова, как на согласовании шагов.
   Цвет кита несёт роль, а не сущность: одна и та же папка артефакта читается
   по-разному, потому что рука с ней делает разное */
.iscard.artf.ask{border-left-color:var(--c2)}
.iscard.artf.ask .iskind{color:var(--c2)}
.isrow.ask .iskind2{color:var(--c2)}
.isrow .iscard.artf.ask{border-left-color:var(--c2)}
.iskind{font-family:var(--mono);font-size:9.5px;letter-spacing:.18em;
  text-transform:uppercase;color:var(--c2)}
.iscard.verify .iskind{color:var(--ok)}
.iscard.artf .iskind{color:var(--c3)}
/* номер работы — якорь для голоса («в пятнадцатой первый шаг ок»): тот же
   кегль, что тип жеста, но полный инк и полужирно — глаз цепляет число.
   Одно правило на карточку (.iskind) и строку очереди (.iskind2) */
.isknum{color:var(--ink);font-weight:700}
/* подпись артефакта пишет агент, и в ней бывает путь или ссылка одним словом:
   рвём где угодно — иначе на телефоне такое слово уводит страницу вбок */
.isnm{font-size:15px;line-height:1.45;color:var(--ink);margin:7px 0 0;
  overflow-wrap:anywhere}
/* план развёрнут: волосяная линия слева вместо спойлера — видно, что это
   речь агента, но читать её не надо разворачивать */
.isplan{font-size:13px;color:var(--ink-dim);line-height:1.6;margin:11px 0 0;
  padding-left:12px;border-left:1px solid var(--line)}
/* метка рамки («проблема» · «где мы» · «результат») — тихий капс кита, тот же
   мотив, что .islbl; цвета своего у неё нет, инк принадлежит тексту */
.isplbl{display:block;font-family:var(--mono);font-size:9px;letter-spacing:.22em;
  text-transform:uppercase;color:var(--ink-mute);margin:0 0 2px}
/* «где будем» закрывает карточку — стоит после шагов и дышит от них */
.isres{margin:16px 0 0}
.islbl{font-family:var(--mono);font-size:9px;letter-spacing:.22em;
  text-transform:uppercase;color:var(--ink-mute);margin:17px 0 4px}
.isitems .wki{padding:7px 0;font-size:13.5px}
/* шаг на столе = тайтл + описание (решение 30.07): столбик держит место
   бывшего .wkt, «да» остаётся справа. Тайтл — обычный инк, размер ряда;
   описание тише и мельче, переводы строк из work.md живут через pre-line */
.istx{flex:1;min-width:0}
.istx .wkt{display:block}
.isdesc{font-size:12px;line-height:1.55;color:var(--ink-dim);margin:4px 0 0;
  white-space:pre-line;overflow-wrap:anywhere}
/* сделанный пункт на приёмке — не архив, а предмет подписи: зачёркнутый и
   выцветший (мотив карточки работы) тут читался бы как «уже неважно» */
.isgrid .wki.don{color:var(--ink)}
.isgrid .wki.don .wkt{text-decoration:none}
/* .isproof отсюда убран (работа 33): пруф на столе жил с фикса 11 сиротой —
   правила были, а разметки не осталось. Вернулся он ОБЩИМИ правилами (.wkpf,
   PROOF_CSS) — одними на стол и на чеклист работы, чтобы у стола не завелась
   вторая правда о том, как выглядит «что сделано». */
/* тишина взятой работы на столе (работа 39) — тем же warn, что «ещё не
   сделано» ниже и что строка тишины на карточке работы: один цвет на один
   смысл «посмотри на это до того, как решишь» */
.isquiet{font-family:var(--mono);font-size:11px;color:var(--warn);
  margin:11px 0 0;line-height:1.5}
.isleft{font-family:var(--mono);font-size:11px;color:var(--warn);margin:10px 0 0}
/* содержимое артефакта — ТИПОВОЙ блок (.cblk, CODE_CSS), общий с кодом в теле
   заметки; столу принадлежит только отступ от подписи над ним */
.isgrid .cblk{margin:11px 0 0}
.isnone{font-family:var(--mono);font-size:11px;color:var(--ink-faint)}
/* подвал карточки: два перехода ОДНИМ рядом и поровну, и обе кнопки — ровно
   «резюм» с карточки нити (.abtn + .pjresume), без единой своей краски
   (решение 30.07: «две одинаковые пилюли»). Тихой ссылкой «к работе» сбоку
   больше не живёт: это такой же переход, как «надиктовать», и врать разной
   формой про равные жесты нельзя. Кто из них главный, говорит порядок —
   слева тот, ради которого стол и открыли. Работы у артефакта может не быть —
   тогда «надиктовать» занимает ряд целиком (flex:1 1 0). */
.isfoot{display:flex;align-items:stretch;gap:10px;flex-wrap:wrap;margin:18px 0 0}
.isfoot .abtn{flex:1 1 0;min-width:0;min-height:40px;justify-content:center;
  cursor:pointer;-webkit-appearance:none;appearance:none}
/* прыгать некуда — вместо пилюли подпись, и она забирает строку себе: сосед
   встаёт под ней целой пилюлей, а не огрызком в остатке ряда */
.isasleep{flex:1 1 100%;font-family:var(--mono);font-size:11px;
  color:var(--ink-faint)}
/* рука человека на артефакте (решение 30.07): ★ сохранить себе и «забрал ✓».
   Они правят паспорт, а переходы рядом только уносят взгляд, — поэтому своей
   группой в хвосте ряда и БЕЗ растяжки: пилюля перехода живёт половиной ряда,
   жест руки — ровно по себе. Кнопка взята у «закрыть» на кухне (.nbtn.wkdone,
   тот же кит): рука человека везде на доске выглядит одинаково, и «забрал»
   отличается от «закрыть» только словом. Звезда — ровно ★ карточки нити */
.ishand{flex:none;display:flex;align-items:center;gap:9px;margin-left:auto}
.istake{padding:4px 12px;font-size:11px;white-space:nowrap}
/* полка «сохранённые» — забранное, оставленное себе звездой. Свёрнута и тиха:
   это не ожидание, а память под столом. Повадки — «закрытых» на кухне
   (.wkclosed), кегль — тихого капса стола (.islbl) */
.issaved{margin-top:4px}
.issaved>summary{font-family:var(--mono);font-size:9px;letter-spacing:.22em;
  text-transform:uppercase;color:var(--ink-mute);cursor:pointer;padding:6px 2px;
  min-height:34px;display:flex;align-items:center;user-select:none;
  list-style:none}
.issaved>summary::-webkit-details-marker{display:none}
.issaved>summary::after{content:'▾';margin-left:8px;font-size:10px}
.issaved[open]>summary::after{content:'▴'}
.issaved>summary:hover{color:var(--ink-dim)}
.issvgrid{display:flex;flex-direction:column;gap:14px;margin:6px 0 0}
/* карточка полки — без коробки и без подвала: у вещи, которую уже забрали, нет
   ни ожидания, ни куда прыгать. Кромка c3 остаётся — по ней глаз узнаёт
   артефакт и на столе, и под ним */
.issv{border-left:2px solid var(--c3);padding:1px 0 2px 12px}
.issvtop{display:flex;align-items:flex-start;gap:10px}
.issvnm{flex:1;min-width:0;font-size:13.5px;line-height:1.45;
  color:var(--ink-dim);overflow-wrap:anywhere}
.isgrid .issv .cblk{margin:8px 0 0}
.isempty{color:var(--ink-faint);font-size:13px;padding:26px 2px}
/* ОДНО целевое действие: главная карточка названа словами и стоит одна —
   ниже уже не карточки, а очередь строками */
.ishead{font-family:var(--mono);font-size:9.5px;letter-spacing:.22em;
  text-transform:uppercase;color:var(--c2);margin:0 0 9px}
.ismain .iscard{border-left-width:3px}
.isrest{margin:10px 0 0}
.isrestlbl{margin:0 0 2px}
/* строка очереди: тип + имя, волосяными линиями в столбик; раскрытая
   отдаёт место своей карточке, свёрнутая молчит */
.isrow{border-top:1px solid var(--line)}
.isrow:last-child{border-bottom:1px solid var(--line)}
/* ШАПКА ОТДЕЛЬНО, ИМЯ ПОД НЕЙ (решение 31.07, работа 25, скрин с телефона).
   Раньше строка была одним флекс-рядом: тип жеста стоял без сжатия, имя —
   впритык справа, — и на 390px «РАБОТА 25 · ПОДТВЕРДИ ПЛАН» съедала строку
   целиком, а имя уезжало вбок и ломалось по слогам. Сетка ставит тип и имя в
   ОДНУ колонку друг под другом (тот же порядок, что в развёрнутой карточке:
   .iskind над .isnm), стрелка держит вторую и стоит по центру обеих строк. */
.issum{display:grid;grid-template-columns:minmax(0,1fr) auto;
  column-gap:10px;row-gap:3px;padding:13px 2px;
  min-height:44px;cursor:pointer;list-style:none}
.issum::-webkit-details-marker{display:none}
.iskind2,.isnm2{grid-column:1}
.issum::after{content:'▾';grid-column:2;grid-row:1 / span 2;align-self:center;
  padding-left:10px;font-size:10px;color:var(--ink-faint)}
.isrow[open] .issum::after{content:'▴'}
.iskind2{flex:none;font-family:var(--mono);font-size:9.5px;letter-spacing:.16em;
  text-transform:uppercase;color:var(--c2)}
.isrow.verify .iskind2{color:var(--ok)}
.isrow.artf .iskind2{color:var(--c3)}
.isnm2{min-width:0;font-size:13.5px;line-height:1.45;color:var(--ink-dim);
  overflow-wrap:anywhere}
.isrow:hover .isnm2,.isrow[open] .isnm2{color:var(--ink)}
/* карточка внутри строки — без своей коробки: рамку уже держит строка,
   двойной кант читался бы как вторая карточка */
.isrow .iscard{border:0;border-left:2px solid var(--c2);border-radius:0;
  padding:2px 0 20px 14px}
.isrow .iscard.verify{border-left-color:var(--ok)}
.isrow .iscard.artf{border-left-color:var(--c3)}
.isrow .iskind{display:none}  /* тип уже сказан строкой выше */
/* прыжок «к работе →»: карточка на кухне подсвечивается кольцом, чтобы глаз
   нашёл её в общем списке; гаснет само */
.wkcard.wkjump{border-color:var(--c1);box-shadow:0 0 0 3px var(--c1-soft)}
@media (max-width:700px){
  .iscard{padding:14px}
  .isnm{font-size:14.5px}
  /* подробности шага с телефона читают глазами, а не лупой */
  .isdesc{font-size:12.5px}
  /* пальцем, не мышью: пилюли подвала в размер пальца (44px, HIG) и в тот же
     кегль, что «резюм» на карточке нити на телефоне */
  .isfoot .abtn{min-height:44px;font-size:12.5px}
  /* с телефона рука занимает свою строку целиком и жмётся вправо: втиснутая
     третьей в ряд пилюль, она осталась бы огрызком под пальцем */
  .ishand{flex:1 1 100%;justify-content:flex-end}
  .istake{min-height:40px;padding:4px 16px;font-size:12.5px}
  /* строка очереди — цель для пальца: 48px в высоту; имя жмётся и переносится
     внутри своей колонки (min-width:0 + overflow-wrap), за экран не лезет */
  .issum{column-gap:10px;padding:14px 2px;min-height:48px}
  .isnm2{font-size:14px}
  .isrow .iscard{padding:2px 0 18px 12px}
}
"""

ISSUES_JS = """
// стол ISSUES (решение 30.07): жестов ОТВЕТА агенту тут нет — на «да» отвечают
// словами в сессии. Свой обработчик держат ровно две вещи: переход «к работе →»
// внутри доски и «забрал ✓» у артефакта — законная кнопка руки, как «закрыть» у
// работы (★ сидит на своём onclick, ровно как на карточке нити).
// Делегирование на document — переживает innerHTML-своп автообновления.
document.addEventListener('click',async e=>{
  const t=e.target;
  // «забрал ✓»: подача уходит со стола (status → taken), след — в её журнал.
  // Жест заперт на один запрос (болячка 30.07, артефакт 100: жесты руки
  // множились до свопа доски): preventDefault+stopPropagation — чтобы клик не
  // дошёл ни до дефолтов разметки (разворот <details>, якорь), ни до соседних
  // делегатов; disabled — замок от повторного клика, пока запрос едет. Успех
  // уносит кнопку свопом boardRefresh, живой её возвращает только отказ
  const tk=t.closest&&t.closest('.istake');
  if(tk){
    e.preventDefault(); e.stopPropagation();
    if(tk.disabled) return;
    tk.disabled=true;
    if(await wkCall('/artifact-take?f='+encodeURIComponent(tk.dataset.take)))
      boardRefresh();
    else tk.disabled=false;
    return; }
  // «к работе →»: та же работа на кухне — переключаем вид и подсвечиваем
  const go=t.closest&&t.closest('.isgo');
  if(go){
    const slug=go.dataset.go;
    if(typeof setView==='function') setView('work');
    const sel=(window.CSS&&CSS.escape)?CSS.escape(slug):slug;
    const card=document.querySelector('.wkcard[data-wk="'+sel+'"]');
    if(card){ card.scrollIntoView({block:'center'});
      card.classList.add('wkjump');
      setTimeout(()=>card.classList.remove('wkjump'),1800); }
  }
});"""


# ── «доска страниц» (решение 16.07, кандидат 112): рисование рукой на canvas ──
# Русло — pages/*.png (страница = файл); вкладка и превью — проекция папки.
def _pages_dir():
    """Где лежат нарисованные страницы.

    Три адреса по порядку: `$TIDE_PAGES`, потом папка рядом с кодом (там они у
    владельца — `tide-stack/pages`, четырнадцать штук), потом `pages/` в доме.
    Средняя ветка нужна ровно для того, чтобы его рисунки остались на месте
    после переезда кода в пакет; у нового человека рядом с кодом лежит
    site-packages, никакого `pages` там нет, и он получает папку в своём доме.
    """
    env = (os.environ.get("TIDE_PAGES") or "").strip()
    if env:
        return Path(env).expanduser()
    near = Path(__file__).resolve().parent.parent / "pages"
    return near if near.is_dir() else HOME / "pages"


PAGES_DIR = _pages_dir()


def _pages_panel():
    # v=mtime в src превью: пересейв меняет файл → меняется HTML сетки →
    # своп доски перекачивает картинку (иначе превью замирало навсегда)
    cards = "".join(
        '<div class="pgcard" data-pg="{0}">'
        '<span class="pgdel" data-del="{0}" title="удалить">✕</span>'
        '<img src="/page?f={0}&v={1}" alt="">'
        '<span class="nsub">{0}</span></div>'.format(
            esc(f.stem), int(f.stat().st_mtime))
        for f in (sorted(PAGES_DIR.glob("*.png"), reverse=True)
                  if PAGES_DIR.is_dir() else []))
    saved = ('<div class="slabel">страницы · {0}</div>'
             '<div class="pggrid">{1}</div>'.format(cards.count("pgcard"), cards)
             if cards else "")
    # история — иконками (решение 16.07: «назад-вперёд точно можно иконками»);
    # панель группами через волосяные разделители: инструмент · цвет ·
    # история ——— страница
    undo_svg = (f'<svg viewBox="0 0 24 24" {S}><path d="M9 14 4 9l5-5"/>'
                '<path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/></svg>')
    redo_svg = (f'<svg viewBox="0 0 24 24" {S}><path d="m15 14 5-5-5-5"/>'
                '<path d="M20 9H9.5a5.5 5.5 0 0 0 0 11H13"/></svg>')
    pen_svg = (f'<svg viewBox="0 0 24 24" {S}>'
               '<path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/>'
               '</svg>')
    er_svg = (f'<svg viewBox="0 0 24 24" {S}>'
              '<path d="m7 21-4.3-4.3c-1-1-1-2.5 0-3.4l9.6-9.6c1-1 2.5-1 3.4 0'
              'l5.6 5.6c1 1 1 2.5 0 3.4L13 21H7Z"/><path d="M22 21H7"/>'
              '<path d="m5 11 9 9"/></svg>')
    # фигуры как в пейнте (решение 16.07): линия · прямоугольник · круг
    line_svg = f'<svg viewBox="0 0 24 24" {S}><path d="M5 19 19 5"/></svg>'
    rect_svg = (f'<svg viewBox="0 0 24 24" {S}>'
                '<rect x="4" y="6" width="16" height="12" rx="1"/></svg>')
    ell_svg = f'<svg viewBox="0 0 24 24" {S}><circle cx="12" cy="12" r="8"/></svg>'
    # рука: таскать нарисованное (решение 16.07)
    move_svg = (f'<svg viewBox="0 0 24 24" {S}>'
                '<path d="M12 2v20M2 12h20"/>'
                '<path d="m9 5 3-3 3 3M9 19l3 3 3-3M5 9 2 12l3 3M19 9l3 3-3 3"/>'
                '</svg>')
    # сетка-тумблер и текст по клеткам (решение 16.07)
    text_svg = (f'<svg viewBox="0 0 24 24" {S}>'
                '<path d="M5 7V5h14v2"/><path d="M9 19h6"/>'
                '<path d="M12 5v14"/></svg>')
    grid_svg = (f'<svg viewBox="0 0 24 24" {S}>'
                '<rect x="4" y="4" width="16" height="16" rx="1"/>'
                '<path d="M4 10h16M4 16h16M10 4v16M16 4v16"/></svg>')
    # картинка через Replicate (решение 16.07): рамка → промпт → объект русла
    img_svg = (f'<svg viewBox="0 0 24 24" {S}>'
               '<rect x="3" y="4" width="18" height="16" rx="1.5"/>'
               '<circle cx="9" cy="9" r="1.6"/>'
               '<path d="m21 15-3.1-3.1a2 2 0 0 0-2.8 0L7 20"/></svg>')
    # модификаторы фигур как в пейнте (решение 16.07): заливка · скругление
    fill_svg = ('<svg viewBox="0 0 24 24" fill="currentColor">'
                '<rect x="5" y="5" width="14" height="14" rx="1.5"/></svg>')
    round_svg = (f'<svg viewBox="0 0 24 24" {S}>'
                 '<rect x="4" y="4" width="16" height="16" rx="4.5"/></svg>')
    return (
        '<div id="pages" hidden>'
        # панель по киту (сабагент-дизайнер 16.07, DESIGN-LANGUAGE +
        # ui-kit): две смысловые строки без переноса — «чем рисую»
        # (инструмент ↔ вид) и «что со страницей» (история · цвет ·
        # действия); кромки строк совпадают на любой ширине.
        # на мобиле (≤700px) панель прибита к низу экрана: ряд 1 —
        # скролл-лента инструментов, ряд 2 — цвета и действия без
        # скролла (мобильная схема, нить paint 29.07)
        '<div class="pgbar">'
        '<div class="pgrow">'
        '<span class="pggrp" role="group" aria-label="инструмент">'
        '<button class="nbtn pgtool pgico" id="pg-move" title="таскать">'
        + move_svg + '</button>'
        '<button class="nbtn pgtool pgico on" id="pg-pen" title="карандаш">'
        + pen_svg + '</button>'
        '<button class="nbtn pgtool pgico" id="pg-er" title="ластик">'
        + er_svg + '</button>'
        '<button class="nbtn pgtool pgico" id="pg-line" title="линия">'
        + line_svg + '</button>'
        '<button class="nbtn pgtool pgico" id="pg-rect" title="прямоугольник">'
        + rect_svg + '</button>'
        '<button class="nbtn pgtool pgico" id="pg-ell" title="круг">'
        + ell_svg + '</button>'
        '<button class="nbtn pgtool pgico" id="pg-text" title="текст">'
        + text_svg + '</button>'
        '<button class="nbtn pgtool pgico" id="pg-img" title="картинка (replicate)">'
        + img_svg + '</button></span>'
        '<span class="pggrp" role="group" aria-label="вид фигуры">'
        '<button class="nbtn pgico" id="pg-fill" title="заливка фигур">'
        + fill_svg + '</button>'
        '<button class="nbtn pgico" id="pg-round" title="скруглённые углы">'
        + round_svg + '</button>'
        # сетка — тематически «вид», рядом с заливкой/скруглением;
        # на мобиле весь ряд 1 — скролл-лента инструментов
        '<button class="nbtn pgico" id="pg-grid" title="сетка">'
        + grid_svg + '</button></span></div>'
        '<div class="pgrow">'
        '<span class="pggrp" role="group" aria-label="история">'
        '<button class="nbtn pgico" id="pg-undo" title="отменить · Cmd+Z">'
        + undo_svg + '</button>'
        '<button class="nbtn pgico" id="pg-redo" title="вернуть · Shift+Cmd+Z">'
        + redo_svg + '</button></span>'
        # цвет во 2-м ряду: на мобиле кружки должны быть видны без
        # скролла ленты (целевая схема ряда 2, нить paint 29.07)
        '<span class="pgdots" role="group" aria-label="цвет">'
        '<span class="pgdot on" data-c="--ink" title="чернила"></span>'
        '<span class="pgdot" data-c="--c1"></span>'
        '<span class="pgdot" data-c="--c2"></span>'
        '<span class="pgdot" data-c="--c3"></span>'
        '<span class="pgdot" data-c="--ok"></span>'
        '<span class="pgdot" data-c="--bad"></span></span>'
        '<span class="pgacts">'
        '<button class="nbtn" id="pg-new">'
        '<span class="pgword">новая</span><span class="pgplus">+</span></button>'
        '<button class="nbtn" id="pg-save">сохранить</button></span></div></div>'
        '<div class="pgwrap"><canvas id="pgc"></canvas></div>'
        + saved + '</div>')


PAGES_CSS = """
/* ── панель рисовалки по киту: две смысловые строки, перенос запрещён;
   ячейка 32px (кит .btn-icon), актив — hairline-чернилами, без заливок
   и теней (сабагент-дизайнер 16.07, DESIGN-LANGUAGE + ui-kit) ── */
#pages .pgbar{display:flex;flex-direction:column;gap:8px;margin:22px 0 12px}
#pages .pgrow{display:flex;align-items:center;justify-content:space-between;
  gap:12px;flex-wrap:nowrap;min-width:0}
#pages .pggrp{display:flex;gap:4px;flex:none}
#pages .pgbar .nbtn{flex:none;box-sizing:border-box;height:32px;
  background:none;border:1px solid var(--line);border-radius:4px;
  font-family:var(--mono);font-size:12px;color:var(--ink-dim);
  cursor:pointer;-webkit-appearance:none;appearance:none;
  transition:color .12s ease,border-color .12s ease}
#pages .pgico{width:32px;padding:0;display:inline-flex;
  align-items:center;justify-content:center}
#pages .pgico svg{width:15px;height:15px;display:block}
#pages .pgbar .nbtn:hover{color:var(--ink);border-color:var(--ink-dim)}
#pages .pgbar .nbtn:focus-visible{outline:1px solid var(--ink-dim);
  outline-offset:1px}
#pages .pgtool.on,#pages .pgtool.on:hover{color:var(--ink);
  border-color:var(--ink)}
#pages .pgdots{display:flex;gap:8px;align-items:center;flex:none;height:32px}
#pages .pgdot{width:20px;height:20px;flex:none;box-sizing:border-box;
  border-radius:50%;border:1px solid var(--line);padding:3px;
  background-clip:content-box;cursor:pointer;
  transition:border-color .12s ease}
#pages .pgdot:hover{border-color:var(--ink-dim)}
#pages .pgdot.on{border-color:var(--ink)}
#pages .pgdot[data-c="--ink"]{background-color:var(--ink)}
#pages .pgdot[data-c="--c1"]{background-color:var(--c1)}
#pages .pgdot[data-c="--c2"]{background-color:var(--c2)}
#pages .pgdot[data-c="--c3"]{background-color:var(--c3)}
#pages .pgdot[data-c="--ok"]{background-color:var(--ok)}
#pages .pgdot[data-c="--bad"]{background-color:var(--bad)}
#pages .pgacts{display:flex;gap:8px;flex:none}
#pages .pgacts .nbtn{padding:0 14px;display:inline-flex;
  align-items:center;white-space:nowrap}
#pages .pgplus{display:none} /* плюс вместо слова «новая» — только мобайл */
#pages #pg-save{color:var(--ink);border-color:var(--ink-dim)}
#pages #pg-save:hover{border-color:var(--ink)}
.pgwrap{border:1px solid var(--line);border-radius:6px;overflow:hidden;
  position:relative}
#pgc{display:block;width:100%;height:62vh;touch-action:none;cursor:crosshair}
#pages.txt #pgc{cursor:text}
#pages.mv #pgc{cursor:grab}
/* оверлей ввода текста по клеткам: моно, волосяная рамка, без заливки */
.pgtxt{position:absolute;box-sizing:border-box;
  background:rgba(11,11,15,.85); /* фолбэк, если color-mix не умеет */
  background:color-mix(in srgb,var(--bg) 85%,transparent);
  border:1px dashed var(--line);border-radius:2px;outline:none;
  font-family:var(--mono);font-weight:700;padding:0 6px;resize:none;
  overflow:hidden;text-align:center}
/* ластик виден в руке: курсор-кольцо диаметром в след ластика */
#pages.er #pgc{cursor:url('data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" width="26" height="26" viewBox="0 0 26 26"><circle cx="13" cy="13" r="11.5" fill="none" stroke="%23000" stroke-width="3" opacity="0.55"/><circle cx="13" cy="13" r="11.5" fill="none" stroke="%23fff" stroke-width="1.4"/></svg>') 13 13, cell}
.pggrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));
  gap:10px}
.pgcard{border:1px solid var(--line);border-radius:6px;padding:8px;
  cursor:pointer;display:flex;flex-direction:column;gap:6px;position:relative}
.pgcard:hover{border-color:var(--ink-dim)}
.pgdel{position:absolute;top:6px;right:9px;font-size:12px;line-height:1;
  color:var(--ink-faint);cursor:pointer;padding:3px;opacity:0}
.pgcard:hover .pgdel{opacity:1}
.pgdel:hover{color:var(--bad)}
.pgcard img{width:100%;border-radius:3px;background:var(--bg)}
.pgcard .nsub{font-family:var(--mono);font-size:10.5px;color:var(--ink-faint)}
/* чипы стилей генерации: ряд над рамкой промпта */
.pgstyles{position:absolute;display:flex;gap:4px;flex-wrap:wrap;z-index:3}
.pgstyles .nbtn{padding:2px 8px;font-size:10.5px;height:auto}
.pgstyles .nbtn.on{color:var(--ink);border-color:var(--ink)}
.pgstyles .pgtrch{margin-left:8px}
/* пикер картинки: галерея ассетов + чипы + промпт одной панелью */
.pgpick{position:absolute;box-sizing:border-box;z-index:4;padding:8px;
  background:rgba(11,11,15,.92);
  background:color-mix(in srgb,var(--bg) 92%,transparent);
  border:1px solid var(--line);border-radius:4px;
  display:flex;flex-direction:column;gap:8px}
.pgpgal{display:grid;grid-template-columns:repeat(auto-fill,minmax(52px,1fr));
  gap:6px;max-height:126px;overflow-y:auto}
.pgpgal img{width:100%;aspect-ratio:1;object-fit:cover;border-radius:3px;
  border:1px solid var(--line);cursor:pointer;background:var(--bg)}
.pgpgal img:hover{border-color:var(--ink-dim)}
.pgpgal-empty{font-family:var(--mono);font-size:11px;color:var(--ink-faint)}
.pgpick .pgstyles{position:static}
.pgpick .pgtxt{position:static;width:100%;min-height:52px}
/* ── мобильный редактор (решение 29.07, нить paint): как в Markup/Procreate —
   холст доминирует, панель прибита к низу экрана в зоне большого пальца.
   Ряд 1 — лента инструментов с горизонтальным скроллом (скроллбар спрятан),
   ряд 2 — цвета и действия БЕЗ скролла, влезает в 390px. Сетке страниц —
   отступ снизу, чтобы не пряталась под фикс-панелью ── */
@media (max-width:700px){
  #pages{padding-bottom:140px}
  #pages .pgbar{position:fixed;left:0;right:0;bottom:0;z-index:6;margin:0;
    padding:8px 10px max(18px, calc(8px + env(safe-area-inset-bottom)));
    background:rgba(11,11,15,.96); /* фолбэк, если color-mix не умеет */
    background:color-mix(in srgb,var(--bg) 96%,transparent);
    border-top:1px solid var(--line)}
  #pages .pgrow{flex-wrap:nowrap;justify-content:flex-start;gap:8px;
    overflow-x:auto;-webkit-overflow-scrolling:touch;scrollbar-width:none}
  #pages .pgrow::-webkit-scrollbar{display:none}
  #pages .pgbar .nbtn{height:42px}
  #pages .pgico{width:42px}
  #pages .pgico svg{width:18px;height:18px}
  /* ряд 2: undo/redo · кружки · спейсер · плюс · сохранить; ячейка 38px,
     точки gap 4 и save 12px — иначе в 390px не влезает без скролла */
  #pages .pgrow:last-child{gap:6px}
  #pages .pgrow:last-child .nbtn{height:38px}
  #pages .pgrow:last-child .pgico{width:38px}
  #pages .pgdots{gap:4px;height:38px}
  #pages .pgdot{width:20px;height:20px;padding:3px}
  #pages .pgacts{margin-left:auto;gap:6px}
  #pages .pgword{display:none}
  #pages .pgplus{display:inline}
  #pages #pg-new{width:42px;padding:0;display:inline-flex;align-items:center;
    justify-content:center;font-size:18px}
  #pages #pg-save{padding:0 12px;font-size:12px}
  /* холст — почти весь экран, почти в край по бокам */
  #pages .pgwrap{margin:8px -6px 0}
  #pgc{height:calc(100dvh - 320px);min-height:340px}
  /* пикер картинок — над панелью во всю ширину; left/top/width приходят
     инлайном из JS — перебиваем через !important */
  .pgpick{position:fixed;left:8px!important;right:8px;top:auto!important;
    width:auto!important;max-width:none;
    bottom:calc(120px + env(safe-area-inset-bottom))}
  .pgpgal{grid-template-columns:repeat(auto-fill,minmax(64px,1fr));
    max-height:160px}
}
"""

PAGES_JS = """
// «доска»: русло — штрихи (paint, шаг 1). Правда рисунка = список штрихов
// {t:pen|er, c:'--вар', p:[[0..1,0..1],…]} + голова undo; холст — проекция:
// любой показ = полный перерисунок штрихов. PNG остаётся превью и выгрузкой.
// __pgDirty держит авторефреш от свопа панели посреди рисунка.
window.__pgDirty=false;
let __pgName=null,__pgTool='pen',__pgColor='--ink',__pgDown=false;
let __pgS=[],__pgHead=0,__pgCur=null,__pgBaseImg=null;
// сетка: тумблер (localStorage), клетка 24 layout-px; фигуры и текст
// прилипают к узлам, перо остаётся свободным
let __pgGrid=false; try{ __pgGrid=localStorage.getItem('board-grid')==='1'; }catch(e){}
let __pgFill=false,__pgRound=false; // модификаторы фигур (как в пейнте)
let __pgMv=null; // живой перенос: {ref, sx, sy, dx, dy} в битмап-пикселях
let __pgRz=null; // живой ресайз за уголок: {ref, p0(норм.), x, y(битмап)}
let __pgWaitRef=null; // рамка, ждущая перегенерацию (Enter уже нажат)
const PG_CELL=24;
// стили генерации картинок: ключ → русская подпись → английский хвост промпта
const PG_ISTYLES=[
  ['','без стиля',''],
  ['line','линии','minimal line art, monochrome, clean'],
  ['flat','флэт','flat vector illustration, simple shapes'],
  ['photo','фото','photorealistic, natural light'],
  ['aqua','акварель','soft watercolor illustration'],
  ['pixel','пиксель','pixel art, 8-bit, limited palette'],
  ['scheme','схема','clean schematic diagram, flat, labeled']];
// клетка в пикселях битмапа: колонки делят ширину НАЦЕЛО — сетка всегда
// цельными квадратами, без половинок по краям (решение 16.07)
function _pgCell(){ const c=_pgCanvas(), cw=c.clientWidth||c.width;
  const n=Math.max(1,Math.round(cw/PG_CELL));
  return c.width/n; }
function _pgSnap(v){ const g=_pgCell(); return Math.round(v/g)*g; }
function _pgv(n){return getComputedStyle(document.documentElement)
  .getPropertyValue(n).trim();}
function _pgCanvas(){return document.getElementById('pgc');}
function _pgStrokeStyle(x,t,k){
  if(t==='er'){ x.globalCompositeOperation='destination-out'; x.lineWidth=26*k; }
  else{ x.globalCompositeOperation='source-over'; x.lineWidth=2.5*k; } }
function _pgDrawS(x,s,k,i){
  // один штрих любого типа: перо/ластик — ломаная; line/rect/ell — фигура;
  // text — моноширинный текст в прямоугольнике клеток (перенос по словам,
  // клип по рамке; размер строки хранится в штрихе — масштабируется с ним)
  const c=_pgCanvas(), W=c.width, H=c.height, p=s.p;
  if(p.length<2) return;
  if(s.t==='img'){
    const x0=Math.min(p[0][0],p[1][0])*W, y0=Math.min(p[0][1],p[1][1])*H,
          w=Math.abs(p[1][0]-p[0][0])*W, h=Math.abs(p[1][1]-p[0][1])*H;
    x.globalCompositeOperation='source-over';
    const ent=_pgImg(s.u);
    // лоадер-надпись: по центру рамки, живые точки (тикер внизу дёргает
    // перерисовку раз в полсекунды, пока есть ожидающие рамки)
    const drawWait=()=>{
      const dots='.'.repeat(1+Math.floor(Date.now()/500)%3);
      x.fillStyle=_pgv('--ink');
      x.font='700 '+(13*k)+'px '+(_pgv('--mono')||'monospace');
      x.textAlign='center'; x.textBaseline='middle';
      x.fillText('генерится'+dots,x0+w/2,y0+h/2);
      x.textAlign='start'; };
    if(ent.ready){
      x.drawImage(ent.img,x0,y0,w,h);
      if(i===__pgWaitRef){ // regen: старая картинка гаснет под скримом
        x.save(); x.globalAlpha=0.55; x.fillStyle=_pgv('--bg');
        x.fillRect(x0,y0,w,h); x.restore();
        drawWait(); } }
    else{
      x.save();
      x.globalAlpha=0.4; x.fillStyle=_pgv('--bg'); x.fillRect(x0,y0,w,h);
      x.setLineDash([6*k,4*k]); x.strokeStyle=_pgv('--ink-dim');
      x.lineWidth=1.5; x.globalAlpha=0.9; x.strokeRect(x0,y0,w,h);
      x.restore();
      drawWait(); }
    return; }
  if(s.t==='text'){
    const x0=Math.min(p[0][0],p[1][0])*W, y0=Math.min(p[0][1],p[1][1])*H,
          w=Math.abs(p[1][0]-p[0][0])*W, h=Math.abs(p[1][1]-p[0][1])*H,
          mono=_pgv('--mono')||'Menlo,monospace';
    const fit=_pgFitText(x,s,w,h,mono);
    // цвет не выбран (чернила по умолчанию) → контраст сам: тёмный на
    // светлой подложке, светлый на тёмной — текст не пропадает на заливке.
    // Подложка на этот момент уже отрисована (реплей по порядку) —
    // выбор детерминирован в любом окне.
    const ink=(s.c==='--ink')?_pgInkFor(x,x0,y0,w,h):s.c;
    x.save(); x.beginPath(); x.rect(x0,y0,w,h); x.clip();
    x.globalCompositeOperation='source-over';
    x.fillStyle=_pgv(ink); x.font='700 '+(fit.lh*0.72)+'px '+mono;
    x.textBaseline='middle'; x.textAlign='center';
    // по центру блока — и по вертикали, и по горизонтали (решение 16.07)
    let ly=y0+Math.max(0,(h-fit.lines.length*fit.lh)/2)+fit.lh/2;
    for(const ln of fit.lines){ x.fillText(ln,x0+w/2,ly); ly+=fit.lh; }
    x.textAlign='start';
    x.restore(); return; }
  _pgStrokeStyle(x,s.t==='er'?'er':'pen',k);
  if(s.t!=='er') x.strokeStyle=_pgv(s.c);
  x.beginPath();
  if(s.t==='rect'){
    const rx=Math.min(p[0][0],p[1][0])*W, ry=Math.min(p[0][1],p[1][1])*H,
          rw=Math.abs(p[1][0]-p[0][0])*W, rh=Math.abs(p[1][1]-p[0][1])*H;
    if(s.r&&x.roundRect) x.roundRect(rx,ry,rw,rh,Math.min(12*k,rw/2,rh/2));
    else x.rect(rx,ry,rw,rh); }
  else if(s.t==='ell'){
    x.ellipse((p[0][0]+p[1][0])/2*W, (p[0][1]+p[1][1])/2*H,
              Math.abs(p[1][0]-p[0][0])/2*W, Math.abs(p[1][1]-p[0][1])/2*H,
              0, 0, Math.PI*2); }
  else if(s.t==='line'){
    x.moveTo(p[0][0]*W,p[0][1]*H); x.lineTo(p[1][0]*W,p[1][1]*H); }
  else{
    x.moveTo(p[0][0]*W,p[0][1]*H);
    for(const pt of p.slice(1)) x.lineTo(pt[0]*W,pt[1]*H); }
  if((s.t==='rect'||s.t==='ell')&&s.f){ x.fillStyle=_pgv(s.c); x.fill(); }
  else x.stroke();
  x.globalCompositeOperation='source-over'; }
// светлота подложки под рамкой → '--ink' на тёмном, '--bg' на светлом
function _pgInkFor(x,x0,y0,w,h){
  const c=_pgCanvas();
  const sx=Math.max(0,Math.floor(x0)), sy=Math.max(0,Math.floor(y0)),
        sw=Math.min(c.width-sx,Math.ceil(w)),
        sh=Math.min(c.height-sy,Math.ceil(h));
  if(sw<1||sh<1) return '--ink';
  try{
    const d=x.getImageData(sx,sy,sw,sh).data; let sum=0,n=0;
    for(let i=0;i<d.length;i+=16){
      const a=d[i+3]/255;
      sum+=(0.299*d[i]+0.587*d[i+1]+0.114*d[i+2])*a+12*(1-a); n++; }
    return (n&&sum/n>140)?'--bg':'--ink';
  }catch(e){ return '--ink'; } }
// подгон текста в рамку: строка стартует с высоты клетки на момент ввода
// и уменьшается, пока текст не влезет целиком (решение 16.07: «если не
// влезает — уменьшается, чтобы всегда влезал»). Детерминирован: реплей
// в любом окне даёт тот же размер.
function _pgFitText(x,s,w,h,mono){
  const H=_pgCanvas().height;
  let lh=(s.fs||0.028)*H, lines=[];
  for(let i=0;i<24;i++){
    x.font='700 '+(lh*0.72)+'px '+mono; // жирный — тем же и меряем
    const pad=lh*0.25; lines=[]; let wide=false;
    for(const para of String(s.s||'').split('\\n')){
      let line='';
      for(const word of para.split(' ')){
        const probe=line?line+' '+word:word;
        if(line&&x.measureText(probe).width>w-2*pad){
          lines.push(line); line=word; }
        else line=probe; }
      lines.push(line); }
    for(const ln of lines)
      if(x.measureText(ln).width>w-2*pad){ wide=true; break; }
    if(!wide&&lines.length*lh<=h+1) break;
    lh*=0.9; }
  return {lh:lh,lines:lines}; }
function _pgRender(v){
  // v=false — чистый экспорт (без сетки и ручек); по умолчанию — живой вид
  const view=(v===undefined)?true:v;
  const c=_pgCanvas(); if(!c||!c.__sized) return;
  const x=c.getContext('2d'), k=_pgK();
  x.globalCompositeOperation='source-over';
  x.fillStyle=_pgv('--bg'); x.fillRect(0,0,c.width,c.height);
  if(view&&__pgGrid){
    // сетка — слой вида, в штрихи и png-превью не попадает; рисуются
    // только ПОЛНЫЕ ряды — ни одной обрезанной клетки, остаток снизу чист
    const g=_pgCell(), yg=Math.floor(c.height/g)*g;
    x.save(); x.strokeStyle=_pgv('--line');
    x.globalAlpha=0.5; x.lineWidth=1; x.beginPath();
    for(let gx=g;gx<c.width-0.5;gx+=g){ x.moveTo(gx,0); x.lineTo(gx,yg); }
    for(let gy=g;gy<=yg+0.5;gy+=g){ x.moveTo(0,gy); x.lineTo(c.width,gy); }
    x.stroke(); x.restore(); }
  if(__pgBaseImg) x.drawImage(__pgBaseImg,0,0,c.width,c.height);
  // переносы: {t:'move',ref,dx,dy} копятся в смещения; объект рисуется
  // сдвинутым, но остаётся на своём месте истории (undo честный)
  const effP=_pgEffP(), edits=_pgEdits(), dels=_pgDels();
  const S0=__pgS.slice(0,__pgHead);
  for(let i=0;i<S0.length;i++){
    let s=S0[i];
    if(s.t==='move'||s.t==='edit'||s.t==='del'||s.t==='size'||dels[i])
      continue;
    if(edits[i]!==undefined){ const ed=edits[i];
      s=Object.assign({},s,(typeof ed==='string')?{s:ed}:ed); }
    if(effP[i]) s=Object.assign({},s,{p:effP[i]});
    _pgDrawS(x,s,k,i); }
  // слой вида при выбранной «руке» (в png не идёт): у текст-блоков —
  // пунктирная рамка границ (решение 16.07: «не видно, как блок выглядит»),
  // у всех рамочных — уголок-ручка ресайза
  if(view&&__pgTool==='move'){
    const hs=6*k; x.save(); x.strokeStyle=_pgv('--ink-dim'); x.lineWidth=1;
    for(let i=0;i<S0.length;i++){ const s=S0[i];
      if(dels[i]||!(s.t==='text'||s.t==='rect'||s.t==='ell'||s.t==='img')) continue;
      const P=effP[i]||s.p;
      const x0=Math.min(P[0][0],P[1][0])*c.width,
            y0=Math.min(P[0][1],P[1][1])*c.height,
            mx=Math.max(P[0][0],P[1][0])*c.width,
            my=Math.max(P[0][1],P[1][1])*c.height;
      if(s.t==='text'){
        x.setLineDash([5*k,4*k]); x.globalAlpha=0.7;
        x.strokeRect(x0,y0,mx-x0,my-y0);
        x.setLineDash([]); x.globalAlpha=1; }
      x.strokeRect(mx-hs/2,my-hs/2,hs,hs); }
    x.restore(); } }
function _pgEdits(){
  // правки: {t:'edit',ref,s} — текст (строка); {t:'edit',ref,u,pr,st[,tr]} —
  // перегенерация картинки (объект); последняя ≤ головы побеждает
  const m={};
  for(let i=0;i<__pgHead;i++){ const s=__pgS[i];
    if(s.t==='edit')
      m[s.ref]=(s.u!==undefined?{u:s.u,pr:s.pr,st:s.st,tr:s.tr}:s.s); }
  return m; }
function _pgDels(){
  // выброшенные: {t:'del',ref} — вынес за край, объект пропал (undo вернёт)
  const m={};
  for(let i=0;i<__pgHead;i++){ const s=__pgS[i];
    if(s.t==='del') m[s.ref]=1; }
  return m; }
// эффективная геометрия: move (дельта) и size (абсолютная рамка)
// применяются В ПОРЯДКЕ истории; сверху — живые перетаскивание/ресайз
function _pgEffP(){
  const m={};
  for(let i=0;i<__pgHead;i++){ const s=__pgS[i];
    if(s.t==='move'){
      const base=m[s.ref]||(__pgS[s.ref]&&__pgS[s.ref].p);
      if(base) m[s.ref]=base.map(q=>[q[0]+s.dx,q[1]+s.dy]); }
    else if(s.t==='size') m[s.ref]=s.p; }
  const c=_pgCanvas();
  if(__pgMv&&(__pgMv.dx||__pgMv.dy)){
    const base=m[__pgMv.ref]||__pgS[__pgMv.ref].p;
    m[__pgMv.ref]=base.map(q=>[q[0]+__pgMv.dx/c.width,
                               q[1]+__pgMv.dy/c.height]); }
  if(__pgRz) m[__pgRz.ref]=[__pgRz.p0,[__pgRz.x/c.width,__pgRz.y/c.height]];
  return m; }
// хит-тест для руки: сверху вниз, с учётом смещений; ластик не таскается
function _pgHit(bx,by){
  const c=_pgCanvas(), W=c.width, H=c.height, effP=_pgEffP(),
        dels=_pgDels(), th=10*_pgK();
  for(let i=__pgHead-1;i>=0;i--){
    const s=__pgS[i];
    if(s.t==='move'||s.t==='edit'||s.t==='del'||s.t==='size'||s.t==='er'
       ||dels[i]||s.p.length<2) continue;
    const P=(effP[i]||s.p).map(q=>[q[0]*W,q[1]*H]);
    if(s.t==='rect'||s.t==='ell'||s.t==='text'||s.t==='img'){
      const x0=Math.min(P[0][0],P[1][0])-th, x1=Math.max(P[0][0],P[1][0])+th,
            y0=Math.min(P[0][1],P[1][1])-th, y1=Math.max(P[0][1],P[1][1])+th;
      if(bx>=x0&&bx<=x1&&by>=y0&&by<=y1) return i; }
    else{
      for(let j=1;j<P.length;j++){
        const ax=P[j-1][0],ay=P[j-1][1],ex=P[j][0],ey=P[j][1];
        const L2=(ex-ax)*(ex-ax)+(ey-ay)*(ey-ay);
        let t=L2?((bx-ax)*(ex-ax)+(by-ay)*(ey-ay))/L2:0;
        t=Math.max(0,Math.min(1,t));
        const qx=ax+t*(ex-ax)-bx, qy=ay+t*(ey-ay)-by;
        if(qx*qx+qy*qy<=th*th) return i; } } }
  return -1; }
// заначка на reload по смене ревизии: штрихи + голова + имя (+ подложка)
window.pgStash=function(){ return JSON.stringify({n:__pgName,s:__pgS,
  h:__pgHead,b:__pgBaseImg?__pgBaseImg.__src||null:null}); };
window.pagesInit=function(){
  const gb=document.getElementById('pg-grid');
  if(gb) gb.classList.toggle('on',__pgGrid); // кнопка сетки помнит состояние
  const fb=document.getElementById('pg-fill'),
        rb=document.getElementById('pg-round');
  if(fb) fb.classList.toggle('on',__pgFill);
  if(rb) rb.classList.toggle('on',__pgRound);
  const c=_pgCanvas(); if(!c||c.__sized) return;
  const r=c.getBoundingClientRect(), d=window.devicePixelRatio||1;
  if(!r.width) return; // панель спрятана — меряться нечем, придём после показа
  c.width=r.width*d; c.height=r.height*d;
  const x=c.getContext('2d');
  x.lineCap='round'; x.lineJoin='round';
  c.__sized=true;
  let st=null; try{ st=sessionStorage.getItem('pg-stash'); }catch(e){}
  if(st){
    try{ sessionStorage.removeItem('pg-stash'); }catch(e){}
    try{
      if(st[0]==='{'){ const o=JSON.parse(st);
        __pgName=o.n; __pgS=o.s||[]; __pgHead=o.h||0;
        if(o.b) _pgLoadBase(o.b); window.__pgDirty=true; }
      else _pgLoadBase(st); // старый формат заначки — голый dataURL
    }catch(e){} }
  _pgRender(); // холст = проекция штрихов: своп/сейв/реролл ничего не теряют
};
function _pgLoadBase(src){ const img=new Image();
  img.onload=()=>{ img.__src=src; __pgBaseImg=img; _pgRender(); };
  img.src=src; }
// координаты указателя → битмап. Больное место — зум доски (body.zoom):
// offsetX в ОБОИХ движках приходит зумленным (замер 16.07: Chrome CDP и
// штрихи владельца в WebKit), а clientWidth — незумлен; движки различаются
// только rect'ом, который здесь не нужен. Делитель — наш же зум Z.
// Синтетика (тесты) offsetX не несёт — для неё путь через rect (зум 1).
function _pgZ(){ return parseFloat(document.body.style.zoom)||1; }
function _pgK(){ const c=_pgCanvas();
  return c.width/(c.clientWidth||c.width); }
function _pgPos(e){ const c=_pgCanvas();
  if(e.isTrusted){
    const z=_pgZ(), cw=c.clientWidth||c.width, ch=c.clientHeight||c.height;
    return [e.offsetX/z*c.width/cw, e.offsetY/z*c.height/ch]; }
  const r=c.getBoundingClientRect();
  return [(e.clientX-r.left)*(c.width/r.width),
          (e.clientY-r.top)*(c.height/r.height)]; }
// ВРЕМЕННЫЙ маяк (paint, 16.07): пишет каждый штрих в localStorage —
// соседняя сессия читает цифры, чтобы поймать «рисует мимо курсора»
// на живой руке. Снять после диагноза.
function _pgBeacon(e){try{
  const c=_pgCanvas(), r=c.getBoundingClientRect();
  const rec={ts:Date.now(),cx:e.clientX,cy:e.clientY,ox:e.offsetX,oy:e.offsetY,
    l:r.left,t:r.top,w:r.width,h:r.height,bw:c.width,bh:c.height,
    cw:c.clientWidth,ch:c.clientHeight,
    sized:!!c.__sized,dpr:devicePixelRatio,zoom:document.body.style.zoom||'',
    iw:innerWidth,ih:innerHeight,ua:navigator.userAgent.slice(-60)};
  // на сервер: у вкладок владельца и сессии-диагноста разные хранилища,
  // sendBeacon долетает из любого движка и не троттлится
  if(navigator.sendBeacon) navigator.sendBeacon('/pg-beacon',JSON.stringify(rec));
  const a=JSON.parse(localStorage.getItem('pg-debug')||'[]');
  a.push(rec);
  localStorage.setItem('pg-debug',JSON.stringify(a.slice(-40)));
}catch(err){}}
document.addEventListener('pointerdown',e=>{
  if(!e.target||e.target.id!=='pgc') return;
  // открыт пикер картинки → клик мимо закрывает его БЕЗ генерации
  // (семантика пикера: выбор — только миниатюрой или Enter)
  const openPick=document.querySelector('.pgpick');
  if(openPick){
    if(window.__pgPickClose) window.__pgPickClose();
    else openPick.remove();
    e.preventDefault(); return; }
  // открыт ввод текста → клик мимо просто закрывает его (как в редакторе),
  // штрих этим кликом не начинается
  const openTa=document.querySelector('.pgtxt');
  if(openTa){ openTa.blur();
    openTa.dispatchEvent(new Event('blur')); // фокус мог и не стоять
    e.preventDefault(); return; }
  _pgBeacon(e);
  pagesInit(); __pgDown=true; window.__pgDirty=true;
  window.__pgTouching=true; // держит своп панели ровно на время касания
  const c=_pgCanvas(), x=c.getContext('2d');
  try{ c.setPointerCapture(e.pointerId); }catch(err){} // штрих не рвётся за краем
  let [bx,by]=_pgPos(e);
  if(__pgTool==='move'){
    // сперва уголок-ручка (ресайз рамки), потом тело объекта (перенос)
    const effP=_pgEffP(), dels=_pgDels(), hth=9*_pgK();
    let rz=-1;
    for(let i=__pgHead-1;i>=0;i--){ const s=__pgS[i];
      if(dels[i]||!(s.t==='text'||s.t==='rect'||s.t==='ell'||s.t==='img')) continue;
      const P=effP[i]||s.p;
      const mx=Math.max(P[0][0],P[1][0])*c.width,
            my=Math.max(P[0][1],P[1][1])*c.height;
      if(Math.abs(bx-mx)<=hth&&Math.abs(by-my)<=hth){ rz=i; break; } }
    if(rz>=0){
      const P=effP[rz]||__pgS[rz].p;
      __pgRz={ref:rz,
        p0:[Math.min(P[0][0],P[1][0]),Math.min(P[0][1],P[1][1])],
        x:bx,y:by};
      // пропорции рамки на момент захвата: Shift на картинке держит их
      const c0=_pgCanvas();
      const arW=(Math.max(P[0][0],P[1][0])-Math.min(P[0][0],P[1][0]))*c0.width,
            arH=(Math.max(P[0][1],P[1][1])-Math.min(P[0][1],P[1][1]))*c0.height;
      __pgRz.ar=(arH>0)?arW/arH:1;
      __pgRz.isImg=(__pgS[rz].t==='img'); }
    else{
      const hit=_pgHit(bx,by);
      if(hit>=0) __pgMv={ref:hit,sx:bx,sy:by,dx:0,dy:0};
      else { __pgDown=false; window.__pgTouching=false; } }
    e.preventDefault(); return; }
  const shaped=(__pgTool==='line'||__pgTool==='rect'||__pgTool==='ell'
                ||__pgTool==='text'||__pgTool==='img');
  if(__pgGrid&&shaped){ bx=_pgSnap(bx); by=_pgSnap(by); }
  __pgCur={t:__pgTool,c:__pgColor,p:[[bx/c.width,by/c.height]]};
  if(__pgTool==='rect'||__pgTool==='ell'){
    if(__pgFill) __pgCur.f=1;
    if(__pgRound&&__pgTool==='rect') __pgCur.r=1; }
  x.beginPath(); x.moveTo(bx,by); e.preventDefault();
});
document.addEventListener('pointermove',e=>{
  if(__pgDown&&__pgRz){
    const c=_pgCanvas(); let [bx,by]=_pgPos(e);
    const propor=e.shiftKey&&__pgRz.isImg; // картинка: Shift держит пропорции
    if(!propor&&(__pgGrid||e.shiftKey)){ bx=_pgSnap(bx); by=_pgSnap(by); }
    // рамка не схлопывается меньше клетки
    const g=_pgCell();
    bx=Math.max(__pgRz.p0[0]*c.width+g,bx);
    by=Math.max(__pgRz.p0[1]*c.height+g,by);
    if(propor){
      const w=bx-__pgRz.p0[0]*c.width;
      by=__pgRz.p0[1]*c.height+w/(__pgRz.ar||1); }
    __pgRz.x=bx; __pgRz.y=by;
    window.__pgDirty=true; _pgRender(); return; }
  if(__pgDown&&__pgMv){
    const c=_pgCanvas(), [bx,by]=_pgPos(e);
    let dx=bx-__pgMv.sx, dy=by-__pgMv.sy;
    // целыми клетками: при включённой сетке — всегда, с Shift — даже без неё
    if(__pgGrid||e.shiftKey){ dx=_pgSnap(dx); dy=_pgSnap(dy); }
    __pgMv.dx=dx; __pgMv.dy=dy; __pgMv.lx=bx; __pgMv.ly=by;
    window.__pgDirty=true;
    _pgRender(); return; }
  if(!__pgDown||!__pgCur) return;
  const c=_pgCanvas(), x=c.getContext('2d'), k=_pgK(), [bx,by]=_pgPos(e);
  if(__pgCur.t==='text'||__pgCur.t==='img'){
    // выделение клеток под текст: тонкая тусклая резинка (не пером!)
    let tx=bx, ty=by;
    if(__pgGrid){ tx=_pgSnap(tx); ty=_pgSnap(ty); }
    __pgCur.p[1]=[tx/c.width,ty/c.height];
    _pgRender();
    const P=__pgCur.p;
    x.save(); x.setLineDash([5*k,4*k]);
    x.strokeStyle=_pgv('--ink-dim'); x.lineWidth=1; x.globalAlpha=0.7;
    x.strokeRect(Math.min(P[0][0],P[1][0])*c.width,
                 Math.min(P[0][1],P[1][1])*c.height,
                 Math.abs(P[1][0]-P[0][0])*c.width,
                 Math.abs(P[1][1]-P[0][1])*c.height);
    x.restore(); return; }
  if(__pgCur.t==='line'||__pgCur.t==='rect'||__pgCur.t==='ell'){
    // фигура тянется резинкой: перерисовать штрихи + предпросмотр.
    // Сетка (если включена) прилипает конец к узлу; Shift прижимает:
    // линию — к осям/диагонали (шаг 45°), прямоугольник — к квадрату,
    // эллипс — к кругу; считаем в пикселях холста, не в долях
    let ex=bx, ey=by;
    if(__pgGrid){ ex=_pgSnap(ex); ey=_pgSnap(ey); }
    if(e.shiftKey){
      const ax=__pgCur.p[0][0]*c.width, ay=__pgCur.p[0][1]*c.height;
      let dx=ex-ax, dy=ey-ay;
      if(__pgCur.t==='line'){
        const st=Math.PI/4, sa=Math.round(Math.atan2(dy,dx)/st)*st,
              len=Math.hypot(dx,dy);
        dx=Math.cos(sa)*len; dy=Math.sin(sa)*len;
        if(Math.abs(Math.cos(sa))<1e-9) dx=0;
        if(Math.abs(Math.sin(sa))<1e-9) dy=0;
      }else{
        const side=Math.max(Math.abs(dx),Math.abs(dy));
        dx=Math.sign(dx||1)*side; dy=Math.sign(dy||1)*side;
      }
      ex=ax+dx; ey=ay+dy;
    }
    __pgCur.p[1]=[ex/c.width,ey/c.height];
    _pgRender(); _pgDrawS(x,__pgCur,k); return; }
  __pgCur.p.push([bx/c.width,by/c.height]);
  _pgStrokeStyle(x,__pgCur.t,k);
  if(__pgCur.t!=='er') x.strokeStyle=_pgv(__pgCur.c);
  x.lineTo(bx,by); x.stroke();
});
document.addEventListener('pointerup',()=>{
  if(__pgDown&&__pgRz){
    const c=_pgCanvas();
    __pgS=__pgS.slice(0,__pgHead);
    __pgS.push({t:'size',ref:__pgRz.ref,
      p:[__pgRz.p0,[__pgRz.x/c.width,__pgRz.y/c.height]]});
    __pgHead=__pgS.length; window.__pgDirty=true;
    __pgRz=null; __pgDown=false; window.__pgTouching=false;
    _pgRender(); return; }
  if(__pgDown&&__pgMv){
    const c=_pgCanvas();
    const out=(__pgMv.lx!==undefined)&&(__pgMv.lx<0||__pgMv.lx>c.width
              ||__pgMv.ly<0||__pgMv.ly>c.height);
    if(out){ // вынес за край — объект выброшен (undo вернёт)
      __pgS=__pgS.slice(0,__pgHead);
      __pgS.push({t:'del',ref:__pgMv.ref});
      __pgHead=__pgS.length; window.__pgDirty=true; }
    else if(__pgMv.dx||__pgMv.dy){
      __pgS=__pgS.slice(0,__pgHead);
      __pgS.push({t:'move',ref:__pgMv.ref,
        dx:__pgMv.dx/c.width,dy:__pgMv.dy/c.height});
      __pgHead=__pgS.length; window.__pgDirty=true; }
    __pgMv=null; __pgDown=false; window.__pgTouching=false;
    _pgRender(); return; }
  if(__pgDown&&__pgCur&&__pgCur.t==='img'){
    const c=_pgCanvas(), g=_pgCell();
    let p=__pgCur.p;
    if(p.length<2){
      const bx=p[0][0]*c.width, by=p[0][1]*c.height;
      p=[p[0],[(bx+8*g)/c.width,(by+3*g)/c.height]]; }
    _pgRender();
    _pgGenOpen(p);
    __pgDown=false; __pgCur=null; return; }
  if(__pgDown&&__pgCur&&__pgCur.t==='text'){
    // выделение клеток закончено → оверлей ввода; клик без движения
    // даёт одну клетку 8×1 (строка) от точки
    const c=_pgCanvas(), g=_pgCell();
    let p=__pgCur.p;
    if(p.length<2){
      const bx=p[0][0]*c.width, by=p[0][1]*c.height;
      p=[p[0],[(bx+8*g)/c.width,(by+g)/c.height]]; }
    _pgRender(); // стереть резинку — рамку рисует само поле ввода
    _pgTxtOpen({t:'text',c:__pgCur.c,p:p});
    __pgDown=false; __pgCur=null; return; }
  if(__pgDown&&__pgCur&&__pgCur.p.length>1){
    __pgS=__pgS.slice(0,__pgHead); // новый штрих съедает хвост «вернуть»
    __pgS.push(__pgCur); __pgHead=__pgS.length; }
  __pgDown=false; __pgCur=null; window.__pgTouching=false;
});
// ── текст по клеткам: оверлей-ввод поверх холста ──
// строка = высота клетки; Enter — готово, Shift+Enter — новая строка,
// Esc — отмена. Пока оверлей открыт, своп панели держится (__pgTouching).
function _pgTxtOpen(st,editRef){
  const c=_pgCanvas(), wrap=c.parentElement, k=_pgK();
  const x0=Math.min(st.p[0][0],st.p[1][0])*c.width,
        y0=Math.min(st.p[0][1],st.p[1][1])*c.height,
        w=Math.abs(st.p[1][0]-st.p[0][0])*c.width,
        h=Math.abs(st.p[1][1]-st.p[0][1])*c.height;
  if(st.fs===undefined) st.fs=_pgCell()/c.height; // строка = клетка при вводе
  const ta=document.createElement('textarea');
  ta.className='pgtxt'; ta.spellcheck=false;
  ta.style.left=(x0/k)+'px'; ta.style.top=(y0/k)+'px';
  ta.style.width=(w/k)+'px'; ta.style.height=(h/k)+'px';
  // цвет как в рендере: не выбран — контраст к подложке
  ta.style.color=_pgv(st.c==='--ink'?_pgInkFor(c.getContext('2d'),x0,y0,w,h)
                                    :st.c);
  ta.style.fontSize=(st.fs*c.height*0.72/k)+'px';
  ta.style.lineHeight=(st.fs*c.height/k)+'px';
  if(st.s) ta.value=st.s; // правка существующего блока
  wrap.appendChild(ta); window.__pgTouching=true;
  setTimeout(()=>{ ta.focus(); ta.dispatchEvent(new Event('input')); },0);
  let closed=false;
  function done(commit){
    if(closed) return; closed=true; // blur может прилететь дважды
    const val=ta.value; ta.remove(); window.__pgTouching=false;
    _pgRender();
    if(!commit) return;
    if(editRef!=null){
      if(val===st.s) return; // не менял — не история
      __pgS=__pgS.slice(0,__pgHead);
      __pgS.push({t:'edit',ref:editRef,s:val});
      __pgHead=__pgS.length; window.__pgDirty=true; _pgRender(); return; }
    if(!val.trim()) return;
    st.s=val;
    __pgS=__pgS.slice(0,__pgHead); __pgS.push(st); __pgHead=__pgS.length;
    window.__pgDirty=true; _pgRender(); }
  ta.addEventListener('keydown',ev=>{
    ev.stopPropagation();
    if(ev.key==='Escape'){ ev.preventDefault(); done(false); }
    else if(ev.key==='Enter'&&!ev.shiftKey){ ev.preventDefault(); done(true); }});
  ta.addEventListener('input',()=>{
    // набираешь — шрифт сам ужимается, чтобы текст всегда влезал в рамку;
    // и держится по центру блока, как в рендере
    const fit=_pgFitText(c.getContext('2d'),{fs:st.fs,s:ta.value},w,h,
                         _pgv('--mono')||'Menlo,monospace');
    ta.style.fontSize=(fit.lh*0.72/k)+'px';
    ta.style.lineHeight=(fit.lh/k)+'px';
    ta.style.paddingTop=
      Math.max(0,(h-fit.lines.length*fit.lh)/2/k)+'px'; });
  ta.addEventListener('blur',()=>done(true));
}
// ── картинка: ПИКЕР — галерея готовых ассетов + промпт генерации ──
// сгенерённая картинка ложится в галерею, потом её можно просто выбрать;
// генерация — штатная функция внутри вставки картинки (решение владельца, нить paint).
// editRef=null — новый штрих; иначе замена: запись-правка {t:'edit',ref,…}.
// pre — префилл {pr,st,tr} для режима regen. Клик по миниатюре — вставка
// как есть ({u} без pr/st/tr); Enter в промпте — генерация через /pg-gen.
function _pgGenOpen(p,editRef,pre){
  const c=_pgCanvas(), wrap=c.parentElement, k=_pgK();
  const x0=Math.min(p[0][0],p[1][0])*c.width,
        y0=Math.min(p[0][1],p[1][1])*c.height,
        w=Math.abs(p[1][0]-p[0][0])*c.width,
        h=Math.abs(p[1][1]-p[0][1])*c.height;
  const pick=document.createElement('div');
  pick.className='pgpick';
  pick.style.left=(x0/k)+'px'; pick.style.top=(y0/k)+'px';
  pick.style.width=Math.max(w/k,280)+'px';
  // галерея: уже сгенерённые ассеты, свежие первыми
  const gal=document.createElement('div');
  gal.className='pgpgal';
  fetch('/pg-assets').then(r=>{ if(!r.ok) throw 0; return r.json(); })
    .then(list=>{
      if(!Array.isArray(list)||!list.length){
        const em=document.createElement('div');
        em.className='pgpgal-empty';
        em.textContent='галерея пуста — сгенери первую';
        gal.appendChild(em); return; }
      list.slice(0,24).forEach(nm=>{
        const im=document.createElement('img');
        im.loading='lazy';
        im.src='/page-asset?f='+encodeURIComponent(nm);
        im.dataset.a=nm;
        im.addEventListener('click',()=>pickAsset(nm));
        gal.appendChild(im); }); })
    .catch(()=>{});
  const ta=document.createElement('textarea');
  ta.className='pgtxt'; ta.spellcheck=false;
  ta.placeholder='что нарисовать? · Enter';
  ta.style.color=_pgv('--ink-dim');
  ta.style.fontSize=(PG_CELL*0.6)+'px'; ta.style.lineHeight=(PG_CELL*0.9)+'px';
  if(pre&&pre.pr) ta.value=pre.pr;
  // выбранный стиль: при regen — из штриха, иначе липнет в localStorage
  let style='';
  try{ style=localStorage.getItem('board-imgstyle')||''; }catch(err){}
  if(pre&&pre.st!==undefined) style=pre.st;
  if(!PG_ISTYLES.some(s=>s[0]===style)) style='';
  // прозрачный фон: тумблер; при regen — из штриха, иначе липнет в localStorage
  let transp=false;
  try{ transp=localStorage.getItem('board-imgtransp')==='1'; }catch(err){}
  if(pre&&pre.tr!==undefined) transp=!!pre.tr;
  const chips=document.createElement('div');
  chips.className='pgstyles'; // внутри панели обычным потоком: без left/top
  PG_ISTYLES.forEach(st=>{
    const b=document.createElement('button');
    b.type='button'; b.className='nbtn'+(st[0]===style?' on':'');
    b.textContent=st[1];
    b.addEventListener('click',()=>{ style=st[0];
      try{ localStorage.setItem('board-imgstyle',style); }catch(err){}
      chips.querySelectorAll('.nbtn:not(.pgtrch)').forEach(n=>
        n.classList.toggle('on',n===b));
      ta.focus(); });
    chips.appendChild(b); });
  const trb=document.createElement('button');
  trb.type='button'; trb.className='nbtn pgtrch'+(transp?' on':'');
  trb.textContent='прозрачный';
  trb.addEventListener('click',()=>{ transp=!transp;
    trb.classList.toggle('on',transp);
    try{ localStorage.setItem('board-imgtransp',transp?'1':'0'); }catch(err){}
    ta.focus(); });
  chips.appendChild(trb);
  pick.appendChild(gal); pick.appendChild(chips); pick.appendChild(ta);
  wrap.appendChild(pick); window.__pgTouching=true;
  setTimeout(()=>ta.focus(),0);
  let closed=false;
  // закрыть = убрать панель целиком; blur НЕ коммитит — закрытие только
  // Enter/Escape/клик-мимо (canvas-гард зовёт __pgPickClose)/миниатюра
  function close(){
    if(closed) return false; closed=true;
    window.__pgPickClose=null;
    pick.remove(); window.__pgTouching=false; _pgRender();
    return true; }
  window.__pgPickClose=close;
  // выбор из галереи: немедленно вставить как есть — картинка «из галереи»
  function pickAsset(nm){
    if(!close()) return;
    __pgS=__pgS.slice(0,__pgHead);
    __pgS.push((editRef!=null)?{t:'edit',ref:editRef,u:nm}
                              :{t:'img',c:'--ink',p:p,u:nm});
    __pgHead=__pgS.length; window.__pgDirty=true; _pgRender(); }
  function done(commit){
    const val=(ta.value||'').trim();
    if(!close()) return;
    if(!commit||!val) return;
    const tail=(PG_ISTYLES.find(s=>s[0]===style)||['','',''])[2];
    const full=tail?val+', '+tail:val; // стилевой хвост не хранится в pr
    // regen: рамка сразу переходит в «генерится…» — Enter не глотается молча
    if(editRef!=null){ __pgWaitRef=editRef; _pgRender(); }
    fetch('/pg-gen',{method:'POST',
      headers:{'Content-Type':'application/x-www-form-urlencoded'},
      body:'p='+encodeURIComponent(full)
           +'&ar='+encodeURIComponent((w/h).toFixed(3))
           +'&t='+(transp?'1':'0')})
      .then(r=>{ if(!r.ok) throw 0; return r.json(); })
      .then(o=>{ __pgS=__pgS.slice(0,__pgHead);
        const rec=(editRef!=null)
          ?{t:'edit',ref:editRef,u:o.f,pr:val,st:style}
          :{t:'img',c:'--ink',p:p,u:o.f,pr:val,st:style};
        if(transp) rec.tr=1;
        __pgS.push(rec);
        __pgHead=__pgS.length; window.__pgDirty=true;
        __pgWaitRef=null; _pgRender(); })
      .catch(()=>{ __pgWaitRef=null; _pgRender();
        (window.deckToast||alert)('генерация не пошла'); }); }
  ta.addEventListener('keydown',ev=>{
    ev.stopPropagation();
    if(ev.key==='Escape'){ ev.preventDefault(); done(false); }
    else if(ev.key==='Enter'&&!ev.shiftKey){ ev.preventDefault(); done(true); }});
}
// кэш ассетов: пока файла нет (генерится) — ретраи раз в 2.5с
const _pgImgs={};
function _pgImg(u){
  let e=_pgImgs[u];
  if(e) return e;
  e=_pgImgs[u]={img:null,ready:false,tries:0};
  const load=()=>{ const im=new Image();
    im.onload=()=>{ e.img=im; e.ready=true; _pgRender(); };
    im.onerror=()=>{ if(++e.tries<60) setTimeout(load,2500); };
    im.src='/page-asset?f='+encodeURIComponent(u)+'&r='+e.tries; };
  load(); return e; }
document.addEventListener('pointercancel',()=>{
  __pgDown=false; __pgCur=null; __pgMv=null; __pgRz=null;
  window.__pgTouching=false; });
// двойной клик по текстовому блоку — подредактировать (решение 16.07);
// правка = запись истории {t:'edit'}, отмена возвращает старый текст
document.addEventListener('dblclick',e=>{
  if(!e.target||e.target.id!=='pgc') return;
  const c=_pgCanvas(), [bx,by]=_pgPos(e), i=_pgHit(bx,by);
  if(i<0) return;
  const effP=_pgEffP(), edits=_pgEdits();
  if(__pgS[i].t==='img'){
    // дабл-клик по картинке — перегенерация: тот же оверлей, префилл
    // промпта/стиля из последней правки или из самого штриха
    const ed=(typeof edits[i]==='object'&&edits[i]!==null)?edits[i]:null;
    const pre={pr:(ed&&ed.pr)||__pgS[i].pr||'',
               st:(ed&&ed.st!==undefined?ed.st:__pgS[i].st)||'',
               tr:(ed?ed.tr:__pgS[i].tr)?1:0};
    _pgGenOpen(effP[i]||__pgS[i].p,i,pre); e.preventDefault(); return; }
  if(__pgS[i].t!=='text') return;
  const eff=Object.assign({},__pgS[i],
    {p:effP[i]||__pgS[i].p,
     s:(edits[i]!==undefined?edits[i]:__pgS[i].s)});
  _pgTxtOpen(eff,i); e.preventDefault();
});
function _pgUndo(){ if(__pgHead>0){ __pgHead--; window.__pgDirty=true; _pgRender(); } }
function _pgRedo(){ if(__pgHead<__pgS.length){ __pgHead++; window.__pgDirty=true; _pgRender(); } }
document.addEventListener('keydown',e=>{
  // панели может не быть вовсе (плагин «страницы» выключен, работа 48) —
  // без этой проверки КАЖДОЕ нажатие клавиши на доске падало бы здесь
  const _pgv=document.getElementById('pages');
  if(!_pgv||_pgv.hidden) return;
  const tag=e.target&&e.target.tagName;
  if(tag==='INPUT'||tag==='TEXTAREA') return;
  // e.code — физическая клавиша: Cmd+Z работает и на русской раскладке
  if((e.metaKey||e.ctrlKey)&&e.code==='KeyZ'){
    e.preventDefault(); e.shiftKey?_pgRedo():_pgUndo(); }
});
document.addEventListener('click',async e=>{
  const t=e.target;
  if(t.closest&&t.closest('.pgdot')){ const d=t.closest('.pgdot');
    __pgColor=d.dataset.c;
    // выбор цвета не сбрасывает фигуру/перо; ластик — отпускает
    if(__pgTool==='er'){ __pgTool='pen';
      document.querySelectorAll('.pgtool').forEach(b=>
        b.classList.toggle('on',b.id==='pg-pen'));
      document.getElementById('pages').classList.remove('er'); }
    document.querySelectorAll('.pgdot').forEach(o=>o.classList.toggle('on',o===d));
    return; }
  const tb=t.closest&&t.closest('.pgtool');
  if(tb){
    const m={'pg-pen':'pen','pg-er':'er','pg-line':'line',
             'pg-rect':'rect','pg-ell':'ell','pg-text':'text',
             'pg-img':'img','pg-move':'move'};
    __pgTool=m[tb.id]||'pen';
    document.querySelectorAll('.pgtool').forEach(b=>b.classList.toggle('on',b===tb));
    const pg=document.getElementById('pages');
    pg.classList.toggle('er',__pgTool==='er');
    pg.classList.toggle('txt',__pgTool==='text');
    pg.classList.toggle('mv',__pgTool==='move');
    return; }
  if(t.closest&&t.closest('#pg-fill')){
    __pgFill=!__pgFill;
    document.getElementById('pg-fill').classList.toggle('on',__pgFill);
    return; }
  if(t.closest&&t.closest('#pg-round')){
    __pgRound=!__pgRound;
    document.getElementById('pg-round').classList.toggle('on',__pgRound);
    return; }
  if(t.closest&&t.closest('#pg-grid')){
    __pgGrid=!__pgGrid;
    try{ localStorage.setItem('board-grid',__pgGrid?'1':'0'); }catch(err){}
    document.getElementById('pg-grid').classList.toggle('on',__pgGrid);
    _pgRender(); return; }
  if(t.closest&&t.closest('#pg-undo')){ _pgUndo(); return; }
  if(t.closest&&t.closest('#pg-redo')){ _pgRedo(); return; }
  if(t.closest&&t.closest('#pg-new')){ __pgS=[]; __pgHead=0; __pgBaseImg=null; __pgName=null;
    window.__pgDirty=false; const c=_pgCanvas(); c.__sized=false; pagesInit();
    return; }
  if(t.closest&&t.closest('#pg-save')){
    const c=_pgCanvas(); if(!c.__sized) return;
    let fresh=false;
    if(!__pgName){ const n=new Date(); fresh=true;
      __pgName=n.getFullYear()+('0'+(n.getMonth()+1)).slice(-2)
        +('0'+n.getDate()).slice(-2)+'-'+('0'+n.getHours()).slice(-2)
        +('0'+n.getMinutes()).slice(-2); }
    try{
      // png — превью и выгрузка; json — правда: полная история штрихов;
      // fresh=1 → сервер разведёт имя при коллизии и вернёт финальное.
      // png снимается без сетки (сетка — слой вида, не рисунка)
      _pgRender(false);
      const png=c.toDataURL('image/png');
      _pgRender();
      const r=await fetch('/page-save',{method:'POST',
        headers:{'Content-Type':'application/x-www-form-urlencoded'},
        body:'f='+encodeURIComponent(__pgName)
             +'&d='+encodeURIComponent(png)
             +'&s='+encodeURIComponent(JSON.stringify(
                 {v:1,strokes:__pgS,head:__pgHead}))
             +(fresh?'&fresh=1':'')});
      const nm=r.headers.get('X-Page-Name'); if(r.ok&&nm) __pgName=nm;
      (window.deckToast||alert)(await r.text());
      if(r.ok){ window.__pgDirty=false; boardRefresh(); }
    }catch(err){ (window.deckToast||alert)('не вышло: '+err); }
    return; }
  if(t.classList&&t.classList.contains('pgdel')){
    // крестик — через подтверждение доски; файл уезжает в pages/.trash
    if(t.dataset.del===__pgName) __pgName=null; // не воскрешать сейвом
    deckConfirm({title:'Удалить страницу?', body:t.dataset.del,
      ok:'удалить', danger:true,
      url:'/page-del?f='+encodeURIComponent(t.dataset.del)});
    return; }
  const pc=t.closest&&t.closest('.pgcard');
  if(pc){ const c=_pgCanvas(); c.__sized=false; pagesInit();
    const name=pc.dataset.pg;
    __pgName=name; window.__pgDirty=false;
    __pgS=[]; __pgHead=0; __pgBaseImg=null;
    try{ // сперва штрихи; старые png-страницы живут подложкой
      const rj=await fetch('/page?f='+encodeURIComponent(name)+'.json');
      if(rj.ok){ const o=await rj.json();
        __pgS=o.strokes||[]; __pgHead=(o.head==null)?__pgS.length:o.head;
        _pgRender(); return; }
    }catch(err){}
    _pgLoadBase('/page?f='+encodeURIComponent(name)); }
});
// пульс лоадера: пока есть недогенерённые картинки — перерисовка
setInterval(()=>{
  const pg=document.getElementById('pages');
  if(!pg||pg.hidden) return;
  const busy=(__pgWaitRef!=null)
    ||Object.values(_pgImgs).some(e=>!e.ready&&e.tries<60);
  if(busy) _pgRender();
},500);
pagesInit&&setTimeout(pagesInit,50);"""


NEWS_CSS = """
#news .nform{display:flex;gap:8px;margin:22px 0 26px}
#news .nsite{margin-left:auto;align-self:center;font-family:var(--mono);
  font-size:12px;color:var(--ink-faint);text-decoration:none;
  white-space:nowrap;padding:9px 2px}
#news .nsite:hover{color:var(--ink)}
.nurl{background:none;border:1px solid var(--line);border-radius:4px;
  padding:9px 12px;font-family:var(--mono);font-size:12.5px;
  color:var(--ink);outline:none}
.nurl:focus{border-color:var(--ink-dim)}
.nurl::placeholder{color:var(--ink-faint)}
#news .nurl{flex:1;max-width:560px}
.nbtn{background:none;border:1px solid var(--line);border-radius:4px;
  padding:9px 16px;font-family:var(--mono);font-size:12.5px;color:var(--ink-dim);
  cursor:pointer;-webkit-appearance:none;appearance:none}
.nbtn:hover{color:var(--ink);border-color:var(--ink-dim)}
.newsgrid{display:flex;flex-direction:column;gap:8px}
.newscard{border:1px solid var(--line);border-radius:6px;padding:12px 16px;
  display:flex;flex-direction:row;align-items:baseline;gap:14px;color:inherit;
  text-decoration:none;cursor:pointer}
a.newscard:hover{border-color:var(--ink-dim)}
.newscard .nm{font-weight:600;flex:1;order:1;padding:0}
.newscard .nsub{font-family:var(--mono);font-size:11px;color:var(--ink-faint);
  order:2;white-space:nowrap}
.newscard .nlnk{order:3;font-size:13px;color:var(--ink-faint);cursor:pointer}
.newscard .nlnk:hover{color:var(--ink)}
.newscard .nfav{order:4;font-size:14px;color:var(--ink-faint);cursor:pointer}
.newscard .nfav:hover{color:var(--c2)}
.newscard .nfav.on{color:var(--c2)}
.newscard.sample{border-style:dashed;opacity:.5}
.newsq{font-family:var(--mono);font-size:12px;color:var(--ink-dim);
  padding:6px 0;border-bottom:1px solid var(--line);word-break:break-all;
  display:flex;flex-direction:column;gap:2px}
.newsq .nqt{color:var(--ink);font-weight:600}
.newsq .nqs{color:var(--ink-faint)}
.newsq .nqu{color:var(--ink-faint);font-size:11px}
.newsq .nqst{color:var(--warn)}
.newsq .nqerr{color:var(--bad);font-size:11px}
.slabel.qrow{display:flex;align-items:center;gap:14px}
.slabel.qrow .nqst{color:var(--warn);text-transform:none;letter-spacing:0}
.nbtn.nproc{padding:4px 12px;font-size:11.5px}
"""

# ── зум выключен (решение 29.07: «Zoom отключим») ───────────────────────────────
# Метатег живёт в шаблоне (scope/index.html) — перебиваем его здесь, при сборке
# head, чтобы вся мобильная правка лежала одним куском рядом с MOBILE_CSS.
# ВАЖНО про честность: Safari на iOS с 10-й версии НАМЕРЕННО игнорирует
# user-scalable/maximum-scale — пинч там останется живым (это их решение по
# доступности, не наш баг). Тег снимает двойной-тап-зум и работает в
# Chrome/Android; настоящее лекарство от «тапнул в поле — страница уехала» —
# 16px полям ввода ниже. Если пинч надо убить совсем — только обёрткой
# (Board.app/WKWebView), из веба не выключается.
VIEWPORT_META = ('<meta name="viewport" content="width=device-width, '
                 'initial-scale=1, maximum-scale=1, user-scalable=no, '
                 'viewport-fit=cover">')

# ── узкий экран (решение 29.07, смотрит доску с айфона) ─────────────────────────
# На ~390px CSS сетка в две колонки давала карточку ~170px: имя нити срезалось
# до «payo…», «t…», «m.», а вторая колонка вовсе уезжала за край (min-content
# карточки шире трека 1fr). Правило одно: на узком всё карточное — в столбец.
# Образец уже стоял на вкладке «работа» (.wkgrid: 1fr по умолчанию, две колонки
# только от 1100px) — здесь то же, но брейкпоинтом сверху, десктоп не тронут.
# Сетки на auto-fill (.pggrid, .dworkgrid) сами дают одну колонку на узком —
# им правило не нужно.
MOBILE_CSS = """
@media (max-width:700px){
  /* избранные · нити · проекты — одна колонка; minmax(0,…) не даёт карточке
     распирать трек, если внутри что-то nowrap */
  .pjgrid{ grid-template-columns:minmax(0,1fr); }
  /* табы вида переносились на две строки (решение 29.07, скриншот с айфона) —
     вместо переноса один ряд с горизонтальным скроллом: полоса скрыта,
     ряд листается пальцем, «навыки» достижимы свайпом */
  .vtabs{ gap:12px; flex-wrap:nowrap; overflow-x:auto;
    -webkit-overflow-scrolling:touch; scrollbar-width:none; }
  .vtabs::-webkit-scrollbar{ display:none; }
  .vtab{ white-space:nowrap; flex:none; }
  /* шапка/контейнер: боковые поля 20px на ~390px съедали десятую часть
     экрана — ужимаем, ритм остальной вёрстки не трогаем */
  .wrap{ padding:16px 14px 44px; }
  .tbrow{ padding:9px 14px; }
  /* панель рисовалки: мобильные правки живут медиа-блоком в конце PAGES_CSS
     (рядом со своей базой) — там панель прибита к низу, лента и 42px-ячейки */
  /* карточка нити: «резюм» — главное действие, на телефоне во всю ширину
     футера и с честным тап-таргетом (44px, HIG). Статус не теряется — он
     остаётся строкой над кнопкой, ✕ и ★ рядом с ним вторичными кружками,
     но уже не микроскопическими. */
  .pjstatus{ flex-wrap:wrap; row-gap:10px; }
  .pjstatus .pjresume{ flex:1 1 100%; justify-content:center; min-height:44px;
    font-size:12.5px; }
  .pjhold{ width:36px; height:36px; }
  /* iOS сам зумит страницу при тапе в поле мельче 16px и обратно НЕ откатывает
     (то самое «доска уехала»); лечится только размером шрифта поля. Целим
     классы, а не голый input{}: у всех полей класс (.nti/.nurl), его
     специфичность выше — правило по элементу до них бы не достало. Чип
     дедлайна (.wkdl input) и текст рисовалки (.pgtxt) НЕ трогаем: там размер
     несёт вёрстку — 16px разорвал бы пилюлю и сбил привязку к клеткам. */
  .nti, .nurl{ font-size:16px; }
  /* форма работы — одна строка и тут (фикс 7 работы 20): даты в форме больше
     нет, инпут тянется на всё свободное, кнопка справа тап-таргетом 44px */
  #work .wkform #wk-t{ min-width:0; max-width:none; }
  #work .wkform #wk-add{ min-height:44px; }
}
"""

# ── вкладки на узком экране: ряд сворачивается в ☰ (решение 30.07) ─────────────
# Ряд даже с горизонтальным скроллом на телефоне читается как «что-то съехало»:
# видно две с половиной вкладки, остальные — свайпом вслепую. На ≤700px ряд
# прячется целиком, на его месте кнопка ☰ с именем текущей вкладки; тап роняет
# тот же список вниз выпадашкой, выбор её закрывает (BURGER_JS).
# Меню — это и есть `.vtabs` в другой раскладке, дубля разметки нет.
# Все правила целим через `.vnav` (0,2,0): так они перебивают мобильные
# правила по голому `.vtabs` специфичностью, а не порядком блоков — соседний
# мобильный блок можно двигать и переписывать, бургер этого не заметит.
# Выпадашка под шапкой (z-index 9 против её 10) — липкая шапка остаётся сверху.
HEADNAV_CSS = """
.vburger{ display:none; }
@media (max-width:700px){
  .vnav{ position:relative; margin:14px 0 10px; }
  .vburger{ display:flex; align-items:center; gap:10px; width:100%;
    min-height:44px; padding:0 14px; background:none; cursor:pointer;
    border:1px solid var(--line); border-radius:8px; color:var(--ink-mute);
    font-family:var(--mono); font-size:13px; }
  .vburger .vbcur{ color:var(--ink); font-size:11px; letter-spacing:.14em;
    text-transform:uppercase; }
  .vnav .vtabs{ display:none; }
  .vnav.vopen .vtabs{ display:flex; flex-direction:column; gap:0;
    position:absolute; top:calc(100% + 6px); left:0; right:0; z-index:9;
    margin:0; padding:4px 0; overflow:visible;
    border:1px solid var(--line-2); border-radius:8px; background:var(--bg-1);
    box-shadow:0 12px 28px rgba(0,0,0,.28); }
  .vnav.vopen .vtab{ display:flex; align-items:center; width:100%;
    min-height:44px; margin:0; padding:0 14px; text-align:left;
    border-bottom:none; }
  .vnav.vopen .vtab.on{ color:var(--ink); background:var(--bg-2);
    box-shadow:inset 2px 0 0 var(--c1); }
}
"""

# ── иконка у вкладки (решение 01.08, работа 36) ───────────────────────────────
# Свой блок, а не правка соседних: ряд вкладок держат три чужих блока разом
# (база, мобильный, бургер), и вклиниваться в каждый ради одного отступа значит
# ронять их порядок. Здесь ровно две вещи — кнопка становится флексом с
# промежутком, и картинке задаётся рост. Цвет НЕ задаём вовсе: currentColor
# уже наследует состояние вкладки — тихий ink-mute в покое, ink-dim под мышью,
# полный ink у активной. Значок 13px при слове в 11px: штрих 1.4 в мелкой сетке
# читается легче слова, и равный рост сделал бы его громче подписи.
# `.vnav.vopen .vtab` (0,3,0) перебивает наш display в выпадашке и оставляет
# свой — промежуток при этом наследуется, поэтому и там иконка стоит слева.
# ── вкладка «суть» страницы нити (решение 01.08, фикс 7 работы 28) ────────────
# Цель — единственное, ради чего сюда заходят читать, и потому она набрана в
# рост абзаца, а не подписи; паспорт под ней — тем же рисунком «метка →
# значение», что паспорт работы (.wkprow), чтобы два паспорта доски читались
# одним движением глаза. Пустая `.dwhy` в шапке (цель уехала сюда) прячется:
# пустой абзац оставлял бы над лентой полосу воздуха ни за что.
ABOUT_CSS = """
.dwhy:empty{ display:none; }
.dabout{ padding:2px 0 4px; }
.dgoal{ font-size:14px; line-height:1.7; color:var(--ink-dim); margin:0 0 18px;
  max-width:68ch; overflow-wrap:anywhere; }
.dgoal.none{ font-family:var(--mono); font-size:11.5px; color:var(--ink-faint); }
.dpass{ border-top:1px solid var(--line); padding-top:12px; }
/* ── решения нити (работа 64, пункт 7) ────────────────────────────────────
   Вкладка «решения» — тот же паспорт-язык, что «суть»: заголовок раздела,
   строки .wkprow с номером слева. Своего рисунка не заводим, кроме двух вещей,
   которых в ките не было: подпись раздела внутри паспорта (.dsec) и чип
   исполнителя (.dwho). Чип без исполнителя красится c2 — цветом «твой ход»:
   обещание в силе, никто его не несёт, и решить это может только человек. */
/* «главное» — первый блок вкладки «суть» (решение 33). Читается как врезка, а
   не как ещё один абзац: линия слева даёт ему вес, которого нет у цели, и глаз
   находит его раньше всего остального, не читая. */
.dmain{ border-left:2px solid var(--c1); padding:2px 0 2px 14px; margin:0 0 18px; }
.dmain p{ font-size:13px; line-height:1.65; color:var(--ink-dim); margin:0 0 7px;
  max-width:68ch; overflow-wrap:anywhere; }
.dmain p:last-child{ margin-bottom:0; }
.dsec{ margin:16px 0 6px; font-family:var(--mono); font-size:9px;
  letter-spacing:.18em; text-transform:uppercase; color:var(--ink-mute); }
.dsec:first-child{ margin-top:0; }
.dwho{ font-family:var(--mono); font-size:9.5px; letter-spacing:.08em;
  padding:1px 7px; margin-left:6px; border:1px solid var(--line-2);
  border-radius:10px; color:var(--ink-faint); white-space:nowrap; }
.dwho.none{ color:var(--c2); border-color:var(--c2); border-style:dashed; }
.dnums{ font-family:var(--mono); font-size:11.5px; color:var(--ink-faint);
  line-height:1.7; overflow-wrap:anywhere; }
@media (max-width:700px){
  .dgoal{ font-size:14.5px; max-width:none; }
}
"""

# ── события работ и раскрытие прошлого в таймлайне (работа 40) ──────────────
# Язык подшага не изобретаем: та же строка .sub с квадратиком-маркером, что у
# выгрузок головы. Отличают событие работы две вещи — КРУГЛЫЙ маркер вместо
# квадратного и тон тише: глаз с одного взгляда делит «агент рассказал» и «с
# работой случилось». Закрытие — единственное, что светится: маркер заливается
# зелёным ok, тем же, которым доска метит сделанное везде.
TLWORK_CSS = """
.sub.wev{ font-size:11.5px; color:var(--ink-mute); align-items:flex-start; }
.sub.wev .sm{ border-radius:50%; border-color:var(--line-2); }
.sub.wev.closed{ color:var(--ink-dim); }
.sub.wev.closed .sm{ background:var(--ok); border-color:var(--ok); }
.sub.wev .wevt{ flex:none; font-family:var(--mono); font-size:10px;
  color:var(--ink-faint); padding-top:1px; }
/* свёртка сопровождения: маркер «+» ей достаётся от общего details.sub */
details.sub.wevmore{ color:var(--ink-faint); font-size:11px; }
.wevd{ margin:2px 0 2px 16px; }
/* ПРОШЛАЯ СЕССИЯ РАСКРЫВАЕТСЯ (фикс 2): заголовок остаётся заголовком, тело
   приходит по тапу. Стрелка — единственное, что добавилось к прежней строке */
.pastwork > summary{ list-style:none; cursor:pointer; display:flex;
  align-items:center; gap:10px; }
.pastwork > summary::-webkit-details-marker{ display:none; }
.pastwork > summary::after{ content:'▾'; margin-left:auto; padding-left:10px;
  font-size:10px; color:var(--ink-faint); }
.pastwork[open] > summary::after{ content:'▴'; }
.pastwork > summary:hover .gt{ color:var(--ink-dim); }
.pastwork .substeps{ margin-top:8px; }
@media (max-width:700px){
  .pastwork > summary{ min-height:38px; }
  .sub.wev{ font-size:12px; }
}
"""

NAVICON_CSS = """
.vtab{ display:inline-flex; align-items:center; gap:6px; flex:none; }
.vtab .vic{ flex:none; width:13px; height:13px; }
.vtab .vic svg{ display:block; width:100%; height:100%; }
/* ПОДПИСЬ НЕ ЛОМАЕТСЯ (замер 01.08): колонка доски — 620px под ряд, семь
   значков со своими отступами прибавили к нему полтора десятка, и флекс
   отыгрывал их единственным способом, каким умеет, — сжимал первую вкладку,
   пока «ISSUES · 3» не разваливалось на две строки. Ряду тесно — пусть едет
   вбок, а не ломает слова: тот же приём, которым живут вкладки нити и мобильный
   ряд (nowrap + скрытый горизонтальный скролл). Промежутки при этом ужаты по
   замеру — между вкладками 18→11, между значком и словом 6, — и семь вкладок
   со значками встают в колонку целиком: ряду нужно 594 при месте 600. Вырастет
   счётчик до двузначного — ряд поедет вбок, и это честная сдача: слово важнее
   свайпа. Только для широкого: на ≤700px ряд прячется под ☰, правила там свои. */
.vtl{ white-space:nowrap; }
@media (min-width:701px){
  .vtabs{ gap:11px; flex-wrap:nowrap; overflow-x:auto;
    -webkit-overflow-scrolling:touch; scrollbar-width:none; }
  .vtabs::-webkit-scrollbar{ display:none; }
}
@media (max-width:700px){
  /* в выпадашке цели крупнее и воздуха между значком и словом больше —
     список читают сверху вниз, а не сканируют строкой */
  .vnav.vopen .vtab{ gap:11px; }
  .vnav.vopen .vtab .vic{ width:15px; height:15px; }
}
"""

# ── кандидаты: ряд, раскрытие, форма (фиксы 3–5 работы 28) ───────────────────
# Ряд кандидата на телефоне ломался: имя, суть, бейдж «откуда», возраст и три
# кнопки стояли ОДНОЙ строкой — на ~430px имени доставалось три слова с
# многоточием («thread completio…»), а бейджу и вовсе «р…». Лечение то же, что
# у карточки нити на узком: не ужимать, а перекладывать. Имя занимает первую
# строку целиком и переносится, мета и кнопки идут второй; суть в свёрнутом
# ряду на телефоне снимаем совсем (строка-огрызок ничего не сообщала, полный
# текст — в тапе). Маркер +/− уходит из потока в угол, иначе перенос уносил
# его на третью строку. Блок стоит ПОСЛЕ MOBILE_CSS/HEADNAV_CSS: правила
# метят те же классы, и порядок решает спор без гонки специфичностей.
CAND_CSS = """
.candr .cmeta{ color:var(--ink-faint); font-family:var(--mono); font-size:10px;
  letter-spacing:.04em; margin:2px 0 10px; }
/* длинный freeform-`from:` — тихой строкой под метой, не в шапку одной лентой */
.candr .cfrom{ color:var(--ink-mute); font-size:11px; line-height:1.6;
  margin:-4px 0 12px; padding-left:10px; border-left:1px solid var(--line-2); }
/* раскрытый кандидат — читаемый текст, а не сводка мелким: строка длиннее
   ~72 знаков утомляет глаз, межстрочка как у заметок */
.tldd.cbody{ white-space:normal; font-size:12px; line-height:1.7;
  color:var(--ink-dim); max-width:72ch; padding:2px 0 6px; }
.tldd.cbody .ntp{ margin:0 0 10px; }
.tldd.cbody .ntp:last-child{ margin-bottom:0; }
/* подсказка формы добавления не влезала в поле (фикс 5): «＋ новый кандидат —
   впиши и жми добавить…» обрезалось. Шрифт САМОГО поля на узком трогать
   нельзя — 16px единственное, что держит iOS от зума при тапе, — поэтому
   мельче делаем только подсказку: она про то, что сюда писать, а не про ввод. */
.nti::placeholder{ font-size:11px; }
@media (max-width:700px){
  .candr>summary.pjrow{ position:relative; flex-wrap:wrap; row-gap:9px;
    gap:10px; padding:11px 2px; }
  .candr>summary.pjrow .pjtag{ flex:1 0 100%; min-width:0; white-space:normal;
    overflow:visible; text-overflow:clip; font-size:13px; line-height:1.45;
    padding-right:20px; }
  .candr>summary.pjrow .pjgoal{ display:none; }
  .candr>summary.pjrow .csrc{ max-width:none; }
  .candr>summary.pjrow .cwhen{ margin-right:auto; }
  .candr>summary.pjrow .sbtn{ width:36px; height:36px; font-size:15px; }
  /* селектор с `details` намеренно: у базового правила шаблона три элемента,
     и без них наш `margin`/`padding` проиграл бы ему специфичностью */
  details.candr>summary::after{ position:absolute; top:11px; right:2px;
    margin:0; padding:0; }
  .tldd.cbody{ font-size:12.5px; max-width:none; }
  .nti::placeholder{ font-size:12px; }
  /* поле и кнопка в столбик: подсказке достаётся вся ширина, кнопке — честный
     тап-таргет 44px (HIG), как «резюм» на карточке нити */
  .ntf{ flex-wrap:wrap; }
  .ntf .nti{ flex:1 1 100%; }
  .ntf .ntb{ flex:1 1 100%; justify-content:center; min-height:44px; }
}
/* на самых узких (SE/mini, ~375px) даже 12px не спасает длинную подсказку
   заметки — ещё ступень вниз, дальше резать нечего */
@media (max-width:400px){
  .nti::placeholder{ font-size:11px; }
}
"""

# ── «что сделано» под пунктом (работа 33, вид финальный — фикс 6) ───────────
# Живёт в двух местах разом — в чеклисте карточки работы (.wktx) и на карточке
# стола «прими работу» (.istx), — и выглядеть должно одинаково: это одна и та
# же вещь, читают её одним движением. Своим блоком после MOBILE_CSS, как
# CAND_CSS: правила метят и базу, и телефон, и спорить с брейкпоинтом им
# незачем. Кегль — читаемого раскрытия кандидата (.tldd.cbody): строку читают
# глазами, а не сканируют, и мельче 12px её не прочесть.
PROOF_CSS = """
.wkpf{ margin:4px 0 2px; }
/* «ЧТО СДЕЛАНО» — ВИДНО СРАЗУ (решение 01.08, фикс 6): ни свёртки, ни ярлыка
   «пруф». Волосяная линия слева — единственное, чем строка отличается от
   описания пункта над ней, и этого хватает: описание говорит, что СОБИРАЛИСЬ
   сделать, эта строка — что СДЕЛАНО. Кегль и тон тихие, громкости ей добавляет
   не размер, а то, что её не надо искать. anywhere — в строке бывает путь или
   команда одним словом, на телефоне такое уводит страницу вбок */
.wkpfb{ font-size:12px; line-height:1.65; color:var(--ink-dim);
  max-width:74ch; margin:3px 0 2px; padding-left:10px;
  border-left:1px solid var(--line); overflow-wrap:anywhere; }
/* принятое и закрытое гаснет целиком (норма .wkd): иначе строка светила бы
   ярче зачёркнутого пункта над ней */
.wki.don.acc .wkpfb,.wkcard.closed .wki.don .wkpfb{ color:var(--ink-faint); }
/* ТЕХНИКА — ЕДИНСТВЕННОЕ, ЧТО УХОДИТ ЗА КЛИК: файлы, функции, цифры прогонов.
   Это протокол, его читают, уже полезши разбираться. Стоит в колонке текста, а
   не пункта — принадлежит строке над собой, и левая линия у них общая. Тише
   её: строчными и без разрядки. Переводы строк из журнала доживают до глаза */
.wkpfx{ margin:2px 0 2px 10px; }
.wkpfx>summary{ display:inline-flex; align-items:center; gap:5px;
  font-family:var(--mono); font-size:10px; color:var(--ink-faint);
  cursor:pointer; user-select:none; padding:2px 0; list-style:none; }
.wkpfx>summary::-webkit-details-marker{ display:none; }
.wkpfx>summary::after{ content:'▾'; font-size:9px; }
.wkpfx[open]>summary::after{ content:'▴'; }
.wkpfx>summary:hover{ color:var(--ink-dim); }
.wkpfxb{ font-size:11.5px; line-height:1.6; color:var(--ink-mute);
  max-width:74ch; margin:2px 0 4px; padding-left:10px;
  border-left:1px solid var(--line); white-space:pre-line;
  overflow-wrap:anywhere; }
/* чек рукой: разворачивать нечего, пруфа у него нет — тихая строка вместо
   свёртки, чтобы человек не тыкал в стрелку за текстом, которого не будет */
.wkpfh{ font-family:var(--mono); font-size:10px; letter-spacing:.04em;
  color:var(--ink-faint); margin:4px 0 2px; }
@media (max-width:700px){
  /* с телефона в свёртку целятся пальцем — рост как у полки «сохранённые» */
  .wkpfx>summary{ min-height:34px; padding:6px 0; }
  .wkpfb{ font-size:12.5px; max-width:none; }
  .wkpfxb{ font-size:12px; max-width:none; }
}
"""

BURGER_JS = """
// ☰ на узком экране: ряд вкладок свёрнут в кнопку (решение 30.07). Меню — тот же
// `.vtabs`, поэтому переключение вида делает штатный обработчик VTABS_JS, а
// здесь только открыть/закрыть и подписать кнопку текущей вкладкой.
const vnav=document.querySelector('.vnav');
const vburg=vnav&&vnav.querySelector('.vburger');
function vnavLabel(){
  const on=document.querySelector('.vtab.on'), l=vnav&&vnav.querySelector('.vbcur');
  // счётчик issues освежается автообновлением прямо в кнопке вкладки —
  // подпись берём с неё в момент показа, иначе она врёт до перезагрузки
  if(on&&l) l.textContent=on.textContent;
}
function vnavOpen(on){
  if(!vnav) return;
  vnav.classList.toggle('vopen',on);
  if(vburg) vburg.setAttribute('aria-expanded',on?'true':'false');
}
// вкладку сменили не бургером (цифрой, M) — свернуть список и переподписать
function vnavSync(){ vnavLabel(); vnavOpen(false); }
if(vburg) vburg.addEventListener('click',e=>{
  e.stopPropagation(); vnavLabel(); vnavOpen(!vnav.classList.contains('vopen'));
});
document.addEventListener('click',e=>{
  if(!vnav||!vnav.classList.contains('vopen')) return;
  const t=e.target;
  if(t.closest&&t.closest('.vtab')){ vnavSync(); return; }
  if(!(t.closest&&t.closest('.vnav'))) vnavOpen(false);
});
document.addEventListener('keydown',e=>{ if(e.key==='Escape') vnavOpen(false); });
vnavLabel();"""

EXT_JS = """
// внешние ссылки НЕ угоняют вебвью борда (из чужой страницы нет пути назад,
// решение 16.07) — уходят в систему через /ext → open: срабатывает тот же
// выбор браузера, что и у ссылок из терминала
document.addEventListener('click',e=>{
  const a=e.target.closest&&e.target.closest('a[href]'); if(!a) return;
  if(!/^https?:/.test(a.href)||a.host===location.host) return;
  e.preventDefault(); e.stopPropagation();
  fetch('/ext?u='+encodeURIComponent(a.href)).catch(()=>{});
});"""

NEWS_JS = """
// форма «вкинуть видео»: делегирование — переживает innerHTML-своп рефреша
async function newsAdd(){
  const u=document.getElementById('nurl'); if(!u) return;
  const url=(u.value||'').trim(); if(!url) return;
  try{
    const r=await fetch('/news-add?u='+encodeURIComponent(url));
    const t=await r.text();
    if(r.ok) u.value='';
    (window.deckToast||alert)(t);
    if(r.ok) boardRefresh();
  }catch(e){ (window.deckToast||alert)('не вышло: '+e); }
}
document.addEventListener('click',e=>{
  if(e.target&&e.target.id==='nadd') newsAdd(); });
// ★ избранное: клик по звезде не проваливается в статью
document.addEventListener('click',async e=>{
  const st=e.target.closest&&e.target.closest('.nfav'); if(!st) return;
  e.preventDefault(); e.stopPropagation();
  try{
    const r=await fetch('/news-fav?f='+encodeURIComponent(st.dataset.f));
    if(r.ok){ st.classList.toggle('on'); boardRefresh(); }
    else (window.deckToast||alert)(await r.text());
  }catch(err){ (window.deckToast||alert)('не вышло: '+err); }
});
document.addEventListener('keydown',e=>{
  if(e.key==='Enter'&&e.target&&e.target.id==='nurl') newsAdd(); });
// ⧉ на карточке: ссылка на статью на сайте — в буфер, шерь куда хочешь
document.addEventListener('click',async e=>{
  const ln=e.target.closest&&e.target.closest('.nlnk'); if(!ln) return;
  e.preventDefault(); e.stopPropagation();
  try{
    await navigator.clipboard.writeText(ln.dataset.u);
    (window.deckToast||alert)('ссылка скопирована — '+ln.dataset.u);
  }catch(err){ (window.deckToast||alert)(ln.dataset.u); }
});
// очередь инбокс-бота доливается на вкладку: при заходе на «новости»
// и раз в минуту, пока вкладка видна
async function newsSync(){
  try{
    const r=await fetch('/news-sync');
    if(parseInt(await r.text(),10)>0) boardRefresh();
  }catch(e){}
}
document.addEventListener('click',e=>{
  const b=e.target.closest&&e.target.closest('.vtab');
  if(b&&b.dataset.v==='news') newsSync(); });
setInterval(()=>{
  const v=document.getElementById('news');
  if(v&&!v.hidden) newsSync(); },60000);
try{ if(localStorage.getItem('board-view')==='news') newsSync(); }catch(e){}
// кнопка «разобрать»: конвейер на компе, стадии приезжают рефрешем
document.addEventListener('click',async e=>{
  if(!e.target||e.target.id!=='nproc') return;
  e.target.disabled=true;
  try{
    const r=await fetch('/news-process');
    (window.deckToast||alert)(await r.text());
    boardRefresh();
  }catch(err){ (window.deckToast||alert)('не вышло: '+err); e.target.disabled=false; }
});"""

VTABS_JS = """
// табы «стол ⇄ проекты ⇄ новости» (решение 12.07 и 16.07): сверху кнопками,
// M гоняет стол⇄проекты; выбор переживает перезагрузку (как палитра)
const vtabs=document.querySelectorAll('.vtab');
const newsV=document.getElementById('news');
const pagesV=document.getElementById('pages');
const workV=document.getElementById('work');
const skillsV=document.getElementById('skills');
const issuesV=document.getElementById('issues');
function setView(mode){ allMode=(mode==='all');
  focusV.hidden=(mode!=='focus'); allV.hidden=(mode!=='all');
  if(newsV) newsV.hidden=(mode!=='news');
  if(pagesV) pagesV.hidden=(mode!=='pages');
  if(workV) workV.hidden=(mode!=='work');
  if(skillsV) skillsV.hidden=(mode!=='skills');
  if(issuesV) issuesV.hidden=(mode!=='issues');
  vtabs.forEach(b=>b.classList.toggle('on',b.dataset.v===mode));
  try{ localStorage.setItem('board-view',mode); }catch(e){}
  // канвас меряется только видимым — инициализация после показа панели
  if(mode==='pages') setTimeout(()=>window.pagesInit&&pagesInit(),0);
  // селект проекта в шапке живёт при любой вкладке (решение 30.07) — зовём
  // только чтобы пере-применить фильтр к столу после смены вида
  if(typeof applyPtab==='function') applyPtab();
  window.scrollTo(0,0); }
vtabs.forEach(b=>b.addEventListener('click',()=>setView(b.dataset.v)));
toggle=function(){ setView(allMode?'focus':'all'); };
// разметка открывает ISSUES — значит в список восстановления входит и 'focus':
// раньше он был «состоянием по умолчанию» и своей ветки не требовал.
// Список приезжает из РЕЕСТРА вкладок (работа 48): выключенная часть из него
// выпадает, и сохранённый вид «news» у человека без новостей просто не
// восстановится — вместо ошибки он увидит вкладку по умолчанию.
const _VIEWS=__VIEWS__;
const _DEFV=__DEFAULT_VIEW__;
const _sv=localStorage.getItem('board-view');
if(_VIEWS.indexOf(_sv)>=0) setView(_sv);
// setView при живом issues НЕ зовём: его панель и так приезжает видимой из
// разметки, а лишний вызов писал бы localStorage и прокручивал экран
else if(_DEFV!=='issues') setView(_DEFV);"""


PTAB_JS = """
// ГЛОБАЛЬНЫЙ фильтр стола по проекту (решение 14.07; в шапку селектом 17.07):
// #pfilter в шапке фильтрует #focus по data-proj; опустевшая секция прячется
// целиком (slabel + grid). Виден на ЛЮБОЙ вкладке (решение 30.07: «выбор проекта
// — на любой странице»): раньше пропадал везде, кроме «нитей», и человек искал
// его глазами по шапке. Прячет его только тумблер в настройках; выбор — в
// localStorage, исчезнувший проект тихо падает на «все».
// Селект живёт в ШАПКЕ (не в #focus), boardRefresh его не сносит — только
// освежает опции (см. AUTOREFRESH); пере-применяем после bind.
// var (не let): applyPtab зовётся из VTABS setView ещё до этой строки при
// восстановлении не-focus вкладки — let дал бы TDZ ReferenceError и обрушил
// скрипт. var поднят и = undefined, applyPtab это переживает (падает на «все»).
var ptabSel='*';
try{ ptabSel=localStorage.getItem('board-ptab')||'*'; }catch(e){}
// ФИЛЬТР СНА (решение 01.09): по умолчанию 'live' — спящие не показываются, пока
// не попросишь. Живут обе оси в одном проходе: карточка видна, только если её
// пропустили И проект, И сон. Разведи их по двум обработчикам — второй стирал
// бы решение первого.
var sleepSel='live';
try{ sleepSel=localStorage.getItem('board-sleep')||'live'; }catch(e){}
function applyPtab(){
  const sel=document.getElementById('pfilter');
  const ptOn=(localStorage.getItem('board-ptabs-on')||'1')==='1';
  if(sel){
    // выбранный проект исчез из опций → «все»
    if(![...sel.options].some(o=>o.value===ptabSel)) ptabSel='*';
    sel.value = (ptOn ? ptabSel : '*');
    sel.style.display = ptOn ? '' : 'none';
  }
  const ssel=document.getElementById('sfilter');
  // спящих не осталось — селекта нет; тогда и прятать нечего
  if(ssel){ if(![...ssel.options].some(o=>o.value===sleepSel)) sleepSel='live';
            ssel.value=sleepSel; }
  const active = ptOn ? ptabSel : '*';
  document.querySelectorAll('#focus .pjcard[data-proj]').forEach(c=>{
    const byProj = active==='*' || c.dataset.proj===active;
    const asleep = c.dataset.sleep==='1';
    const bySleep = !ssel ? true
                  : sleepSel==='*' ? true
                  : sleepSel==='sleep' ? asleep
                  : !asleep;
    c.hidden = !(byProj && bySleep); });
  document.querySelectorAll('#focus .pjgrid').forEach(g=>{
    const any=[...g.children].some(c=>!c.hidden);
    g.hidden=!any;
    const l=g.previousElementSibling;
    if(l&&l.classList.contains('slabel')) l.hidden=!any;
  });
}
document.addEventListener('change',e=>{
  if(!e.target) return;
  if(e.target.id==='pfilter'){
    ptabSel=e.target.value;
    try{ localStorage.setItem('board-ptab',ptabSel); }catch(err){}
  } else if(e.target.id==='sfilter'){
    sleepSel=e.target.value;
    try{ localStorage.setItem('board-sleep',sleepSel); }catch(err){}
  } else return;
  applyPtab();
});
if(typeof bind==='function'){ const _bindP=bind; bind=function(){ _bindP(); applyPtab(); }; }
applyPtab();"""


FOCUS_JS = """
const FOCUS_IDS=__FOCUS_IDS__;
// Цифры 1–7 — ВКЛАДКИ (решение 30.07: «жму цифру — открываются треды», ждал не
// того). Порядок цифр = порядок вкладок в шапке, объяснять нечего: 1 — самая
// левая. Открытие нити по номеру с цифр СНЯТО; на F1–F7 оно осталось (эти
// клавиши случайно не нажмёшь), ради них и живёт FOCUS_IDS.
// Cmd/Ctrl+цифра не трогаем — это вкладки браузера, не наши.
// Список — из РЕЕСТРА вкладок (работа 48), в историческом порядке цифр:
// выключенная часть выпадает, и цифры сдвигаются на то, что осталось.
const KEY_VIEWS=__KEY_VIEWS__;
document.addEventListener('keydown',e=>{
  if(window.inEdit&&inEdit(e)) return;  // печать важнее хоткеев (17.07)
  if(e.metaKey||e.ctrlKey||e.altKey) return;
  if(e.key>='1'&&e.key<='7'){
    const v=KEY_VIEWS[+e.key-1];
    if(!v||typeof setView!=='function') return;
    e.preventDefault(); setView(v);
    // на узком экране список вкладок мог быть раскрыт — цифра его закрывает
    // и переподписывает ☰, иначе кнопка врёт про текущую вкладку
    if(typeof vnavSync==='function') vnavSync();
    return;
  }
  const f=/^F([1-7])$/.exec(e.key);
  if(f&&FOCUS_IDS[+f[1]-1]){ e.preventDefault(); openT(FOCUS_IDS[+f[1]-1]); }
});"""


def main():
    body, proj_html, T, focus_ids, pfilter_html, tp = build()
    tpl = TEMPLATE.read_text(encoding="utf-8")
    # ревизия кода доски = хэш этого файла: долгоживущая вкладка сравнивает её
    # при автообновлении и перезагружается ОДИН раз, когда доску обновили —
    # иначе новая разметка приезжает в старую страницу без своих CSS/JS
    import hashlib
    rev = hashlib.sha1(Path(__file__).read_bytes()).hexdigest()[:10]
    tpl = tpl.replace("</title>",
                      '</title>\n<meta name="board-rev" content="{0}">'.format(rev), 1)
    # зум с телефона выключен (см. VIEWPORT_META): шаблонный тег перебиваем,
    # а если шаблон его потеряет — ставим свой, чтобы правка не пропала молча
    tpl, hit = re.subn(r'<meta name="viewport"[^>]*>', VIEWPORT_META, tpl, count=1)
    if not hit:
        tpl = tpl.replace("</title>", "</title>\n" + VIEWPORT_META, 1)
    html = re.sub(r'(<div id="focus">).*?(</div>\s*<div id="all")',
                  lambda m: m.group(1) + body + m.group(2), tpl, count=1, flags=re.S)
    # таб «проекты» живёт в бывшем M-экране: уровень домов, заход = полка
    html = re.sub(r'(<div id="all" hidden>).*?(</div>\s*<div class="foot")',
                  lambda m: m.group(1) + proj_html + m.group(2), html, count=1, flags=re.S)
    # ВКЛАДКИ ИЗ РЕЕСТРА (работа 48): и ряд кнопок, и панели собираются по
    # одному списку `board_tabs()`, отфильтрованному по включённым частям.
    # Ни одна вкладка — ни кор, ни плагин — не попадает на доску иначе.
    tabs = enabled_tabs()
    # вкладка по умолчанию — ISSUES (решение 30.07): её панель приезжает видимой,
    # а #focus, бывший экран по умолчанию, — скрытым. Дальше вид решает
    # localStorage через VTABS_JS, как и у остальных вкладок.
    n_issues = 0
    panels = {}
    for t in tabs:
        if not t.panel:
            continue  # #focus и #all приезжают из шаблона, их уже вставили выше
        if t.key == "issues":
            # единственная панель, которая отдаёт ещё и число: счётчик на её
            # кнопке в ряду вкладок берётся отсюда, не пересчитывается заново
            panels[t.key], n_issues = t.panel()
        else:
            panels[t.key] = t.panel()
    html = html.replace('<div id="focus">',
                        _tabs_html(tabs, n_issues) + '\n<div id="focus" hidden>', 1)
    # панели «стол», «работа», «новости», «доска страниц», «навыки» — экраны
    # рядом с #focus/#all, в том же порядке, что кнопки в ряду
    html = html.replace('<div class="foot"',
                        "".join(panels[t.key] for t in tabs if t.key in panels)
                        + '\n<div class="foot"', 1)
    # TP — панели страницы нити: работы у каждой нити свои, issues — на дом (см.
    # _proj_panes). Ставим ОТДЕЛЬНОЙ строкой после T и ровно в том же виде:
    # автообновление вытягивает обе регуляркой «const X = {…};\n», и своя
    # строка у каждой — условие того, что жадность одной не съест другую.
    html = re.sub(
        r"const T = \{.*?\n\};",
        lambda m: ("const T = " + json.dumps(T, ensure_ascii=False) + ";\n"
                   + "const TP = " + json.dumps(tp, ensure_ascii=False)
                   + ";\nwindow.TP = TP;"),
        html, count=1, flags=re.S)
    # инфостроку «CURRENT SCALE · ТВОИ НИТИ · СОБРАНО …» с экрана СНЯЛИ
    # (решение 30.07: «убрать вообще»): она ничего не решала и на телефоне
    # съедала строку первого экрана. Штамп сборки полезен только при разборе
    # «а свежий ли HTML мне отдали» — остаётся комментарием в исходнике
    # страницы, где его ищет глаз отладчика, а не глаз человека.
    stamp = datetime.now().strftime("%d.%m %H:%M:%S")
    html = re.sub(r'<div class="eyebrow">.*?</div>\s*',
                  "<!-- board build: {0} -->\n".format(stamp),
                  html, count=1, flags=re.S)
    # тот же штамп — ещё и в угол экрана: комментарий в исходнике видит отладчик,
    # а «свежее ли то, на что я смотрю» спрашивает человек, и чаще всего с
    # телефона. Живым его держит автообновление (AUTOREFRESH_JS переписывает
    # текст с каждого удачного ответа) — застывшая цифра и значит «протухло»
    html = html.replace(
        "</html>",
        '<div id="rstamp">данные от {0}</div>\n</html>'.format(
            stamp.split(" ", 1)[-1]), 1)
    # светофор здоровья с шапки СНЯТ (решение 16.07, скриншотом: «мешает,
    # убери нафиг»); цифры остаются в `tide doctor --line` и хуке входа
    # сессии, поиск тихого места — кандидат 111-health-quiet-home
    # пустой чип «ход» в шапке «сейчас» рисовался пустой пилюлей — прячем;
    # плюсик у карточек-рёбер — мусор (решение 09.07): карточка кликабельна целиком
    html = html.replace("</style>",
                        ".nt:empty{display:none}\n"
                        # узел-действие: кружок/⇄ кликается (resume/take)
                        ".mact{transition:transform .12s;cursor:pointer}\n"
                        ".mact:hover{transform:scale(1.45)}\n"
                        + CODE_CSS  # типовой блок «код + ⧉» — базой под панели
                        + SESS_CSS + NEWS_CSS + PAGES_CSS + WORK_CSS + SKILLS_CSS
                        + ISSUES_CSS
                        + MOBILE_CSS  # брейкпоинт последним — перебивает базу
                        + HEADNAV_CSS  # бургер: правила через .vnav, порядок ему не нужен
                        + CAND_CSS  # ряд/раскрытие кандидата и форма — последними
                        + PROOF_CSS  # пруф под пунктом: и кухня, и стол
                        + NAVICON_CSS  # после HEADNAV_CSS: иконка в обеих раскладках
                        + ABOUT_CSS  # вкладка «суть» + пустая цель в шапке
                        + TLWORK_CSS  # события работ и раскрытие прошлого
                        + RSTAMP_CSS  # штамп свежести — поверх всего, в углу
                        + "</style>", 1)
    # справка НЕ живёт на экране (правка владельца 07.07): интро и подвал — в модалку по H
    html = re.sub(r'<p class="intro">.*?</p>', "", html, count=1, flags=re.S)
    html = re.sub(r'<div class="foot">.*?</div>', "", html, count=1, flags=re.S)
    # ТОТ ЖЕ реестр — в JS: список восстановления вида и раскладка цифр 1–7.
    # Порядки исторические (KEY_ORDER/VIEW_ORDER), фильтр — по включённому.
    _keys = {t.key for t in tabs}
    vtabs_js = (VTABS_JS
                .replace("__VIEWS__",
                         json.dumps([k for k in VIEW_ORDER if k in _keys]))
                .replace("__DEFAULT_VIEW__", json.dumps(default_view(tabs))))
    focus_js = (FOCUS_JS
                .replace("__FOCUS_IDS__", json.dumps(focus_ids))
                .replace("__KEY_VIEWS__",
                         json.dumps([k for k in KEY_ORDER if k in _keys])))
    html = html.replace("</script>", PALETTE_JS + HELP_JS + vtabs_js + focus_js
        + AUTOREFRESH_JS + PTAB_JS + SETTINGS_JS
        + CONFIRM_JS + COPY_JS + EXT_JS + NEWS_JS + PAGES_JS + WORK_JS
        + ISSUES_JS  # после WORK_JS: стол зовёт его wkCall
        + WORK_MODAL_JS + SHELF_TABS_JS + ZOOM_JS
        + BURGER_JS  # после VTABS_JS: подпись кнопки берётся с активной вкладки
        + "\n</script>")
    # настройки (решение 14.07): в шапке — только шестерёнка; селект палитры
    # переезжает из шапки внутрь модалки настроек (его id/JS не меняются)
    pal_m = re.search(r'<label class="palsel">.*?</select></label>', html, flags=re.S)
    palsel = ""
    if pal_m:
        palsel = pal_m.group(0).replace('<span class="pl">палитра</span>', "")
        # фильтр стола по проекту (решение 17.07) — селектом слева от шестерёнки
        html = html.replace(
            pal_m.group(0),
            '<div class="tbctl">{0}{1}</div>'.format(pfilter_html, SETTINGS_GEAR), 1)
    html = html.replace('<div class="detail" id="detail"',
                        HELP_MODAL + SETTINGS_MODAL.replace("__PALSEL__", palsel)
                        + CONFIRM_MODAL
                        + WORK_MODAL.replace("__LEGEND__", _WORK_LEGEND)
                        + '\n<div class="detail" id="detail"')
    if SHELL_CLIENT:  # мост к оболочке Board.app (без неё — веб-фолбэк)
        # </script> в тексте кита (пример в шапке-комментарии) оборвал бы
        # инлайн-блок посреди файла — экранируем для HTML-парсера
        kit_js = SHELL_CLIENT.read_text(encoding="utf-8").replace("</script>", "<\\/script>")
        html = html.replace("</html>", "<script>{0}{1}</script></html>".format(
            kit_js, SHELL_HOTKEYS_JS), 1)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    # АТОМАРНАЯ запись (решение 13.07): temp + rename — читатель (serve_live) никогда
    # не поймает полу-записанный board.html (это давало транзиентные 599-байт отдачи)
    tmp = OUT.with_name(OUT.name + ".tmp")
    tmp.write_text(html, encoding="utf-8")
    os.replace(str(tmp), str(OUT))
    print("live_projection: {0} — {1} нитей".format(OUT, len(T)))
    return 0


if __name__ == "__main__":
    import sys as _sys
    if "--count-skill-usage" in _sys.argv:
        _count_skill_usage_main()
        raise SystemExit(0)
    raise SystemExit(main())
