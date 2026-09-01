# Доска

`tide board` поднимает доску из пакета: маленький сервер на localhost, который
на каждый запрос заново собирает проекцию твоего контрол-дома — стрим самого
дома, стрим каждого проекта из ростера и карточки работ (когда включён плагин
`work`). Страница сама перезагружается раз в 30 секунд; никакой базы и
никакого кэша — что в `.tide/`, то и на экране.

```bash
tide board                # http://127.0.0.1:8765
tide board --open         # то же + сразу открыть браузер
tide board --port 9000    # порт не зашит — любой свободный
```

Сервер слушает только localhost (это осознанное решение, не ограничение):
наружу доску выносит tailscale, не открытый порт.

## С телефона

Сервер остаётся на localhost; до телефона доску доносит [tailscale](https://tailscale.com)
(бесплатного плана хватает):

1. Поставь Tailscale на мак и на телефон, войди в один tailnet.
2. На маке, при работающем `tide board`:

   ```bash
   tailscale serve --bg 8765
   tailscale serve status       # покажет https://<имя-мака>.<tailnet>.ts.net
   ```

3. Открой этот адрес в браузере телефона. HTTPS и доступ только для твоих
   устройств tailscale даёт сам.

Снять: `tailscale serve reset`.

## Как служба (macOS, чтобы доска жила всегда)

`tide board` — обычный процесс: закрыл терминал — доска погасла. Чтобы она
поднималась сама при входе в систему, поставь launchd-агент. Файл
`~/Library/LaunchAgents/com.tide.board.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.tide.board</string>
  <key>ProgramArguments</key><array>
    <string>/Users/ТЫ/.local/bin/tide</string>
    <string>board</string>
    <string>--port</string><string>8765</string>
  </array>
  <key>EnvironmentVariables</key><dict>
    <key>TIDE_HOME</key><string>/Users/ТЫ/tide-home</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/tmp/tide-board.log</string>
  <key>StandardErrorPath</key><string>/tmp/tide-board.log</string>
</dict></plist>
```

Два пути подставь свои: `which tide` скажет первый, твой контрол-дом — второй.
Дальше:

```bash
launchctl load ~/Library/LaunchAgents/com.tide.board.plist    # поставить
launchctl unload ~/Library/LaunchAgents/com.tide.board.plist  # снять
```

## Стык с живой доской

У этой доски есть старшая сестра — живая интерактивная доска верфи
(`tide-stack/board`: вкладки, кнопки работ, возврат в сессии, ~12 тысяч строк).
Она в пакете НЕ едет: сегодня она прибита к одной машине абсолютными путями
(кандидат 187) и несёт поверхности, которых у свежей установки нет. Стык
намечен так: когда её отвяжут от домашних путей, верб `tide board` научится
запускать её вместо встроенной страницы — тот же адрес, та же команда, богаче
страница. До тех пор встроенная проекция — честный минимум: видно всё, нажимать
пока нечего.
