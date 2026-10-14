# Instagram Reels и TikTok напрямую

Решение и его причины описаны в [ADR-0099](docs/adr/0099-direct-instagram-and-tiktok-publishers.md).
Всё устроено как у YouTube ([YOUTUBE_SETUP.md](YOUTUBE_SETUP.md)): подключение аккаунта
само ничего не публикует, `post_clip.py` без `--approve` только показывает предпросмотр,
а квитанции в `.omemo/` не дают отправить один и тот же клип дважды.

**Состояние на 2026-10-14:** код и тесты готовы (`tests/test_social_publishers.py`,
локальный сервер изображает Graph API и TikTok). С реальными Instagram и TikTok
пока не проверялось: первый настоящий запуск и будет проверкой.

## Instagram (через Facebook-страницу)

Публикация с компьютера возможна только через вход Facebook: аккаунт Instagram должен быть
**профессиональным** (Автор или Бизнес) и **привязан к Facebook-странице**.

1. В приложении Instagram: Настройки → Тип аккаунта → переключиться на профессиональный.
   Привязать его к своей Facebook-странице (Центр аккаунтов или настройки страницы).
2. На [developers.facebook.com](https://developers.facebook.com/apps) создайте приложение
   типа **Business**. Добавьте продукт **Instagram** → вариант *API setup with Facebook Login*.
   Пока приложение в режиме разработки, публиковать можно в аккаунты, где вы администратор,
   без проверки Meta (App Review).
3. В **Graph API Explorer** выберите своё приложение, «User Token» и отметьте разрешения:
   `instagram_basic`, `instagram_content_publish`, `pages_show_list`, `pages_read_engagement`.
   Нажмите *Generate Access Token* и разрешите доступ к своей странице и Instagram.
4. В каталоге проекта (App ID есть в настройках приложения; App secret и токен вводятся
   скрыто, в чат их не вставлять):

   ```sh
   .venv/bin/python configure_instagram.py --app-id 1234567890 --handle @мой_инстаграм
   ```

   Скрипт обменяет токен на долгоживущий, найдёт страницу с этим Instagram и сохранит
   её токен в `.omemo/instagram-token.json` (права 0600). Токен страницы, полученный
   так, не истекает, пока вы не смените пароль и не отзовёте доступ.

Ограничения Instagram для Reels: до 300 МБ, ширина до 1920 px, 3 с – 15 мин, подпись до
2200 символов и 30 хэштегов. Клипы `factory.py` (720×1280) подходят; клипы основного
конвейера на холсте 2160×3840 для Instagram слишком широкие. Задайте
`OMEMO_CLIP_CANVAS=1080x1920`, если они идут в Instagram.

## TikTok (черновики, после аудита — прямая публикация)

Пока TikTok не проверил приложение (аудит), всё, что оно публикует напрямую, видно только
вам. Поэтому стартовый режим — **черновики**: ролик падает во «Входящие» в приложении
TikTok, вы вставляете подпись и публикуете сами. В конвейере такой клип остаётся
«в ожидании», пока вы его не опубликуете. Не больше 5 черновиков за сутки.

1. На [developers.tiktok.com](https://developers.tiktok.com/apps) создайте приложение,
   платформа **Desktop**. Добавьте продукты **Login Kit** и **Content Posting API**
   (в нём включите *Direct Post*). Scopes: `user.info.basic`, `video.upload`,
   `video.publish`.
2. В Login Kit добавьте redirect URI: `http://127.0.0.1:*/callback/`.
3. Подключите аккаунт (Client key виден в приложении, Client secret вводится скрыто):

   ```sh
   .venv/bin/python configure_tiktok.py --client-key aw123... --handle @мой_тикток
   ```

   Откроется браузер TikTok, разрешите доступ. Токен сохранится в
   `.omemo/tiktok-token.json`, обновляется сам (действует год, потом подключить заново).
4. **Аудит.** В разделе Content Posting API подайте приложение на проверку
   («Apply for audit»): нужны описание, как работает публикация, и видео с демонстрацией.
   По отзывам проверка идёт 2–6 недель.
5. После одобрения поменяйте в `.env`:

   ```sh
   OMEMO_TIKTOK_MODE=direct
   OMEMO_TIKTOK_PRIVACY=PUBLIC_TO_EVERYONE
   ```

   Перед каждой прямой публикацией код спрашивает у TikTok, какие варианты видимости
   разрешены аккаунту, и останавливается, если выбранного нет.

## Публикация

Рядом с MP4 должен лежать TXT с разделами `Название:` и `Описание:`, как для YouTube.
Подпись на обеих площадках: название, пустая строка, описание.

```sh
.venv/bin/python post_clip.py '/полный/путь/clip.mp4' --to instagram
.venv/bin/python post_clip.py '/полный/путь/clip.mp4' --to tiktok
```

Это предпросмотр без сети. После апрува повторить с `--approve ОТПЕЧАТОК`. Изменение
файла, текста, аккаунта или режима меняет отпечаток. Отдельные аккаунты подключаются
с `--profile имя` (файлы `.env.instagram-имя`, `.env.tiktok-имя`).

**Локальная фабрика:** `factory.py ... --also instagram,tiktok` показывает предпросмотры
всех площадок, и одно `publish` отправляет клип в YouTube и туда.

**Основной конвейер:** `OMEMO_POSTING_PROVIDER=youtube,instagram,tiktok` (или любая часть
списка). Клип становится опубликованным, когда его приняли все перечисленные площадки.

## Повторы и ошибки

- **Instagram:** до вызова `media_publish` ничего не видно публично, поэтому прерванная
  загрузка просто начинается заново. Если обработка видео заняла дольше 10 минут, ролик
  опубликует следующая проверка статуса. Если процесс упал во время самой публикации,
  повтор заблокирован: проверьте профиль в Instagram. Квитанцию не удаляйте вслепую.
- **TikTok:** квитанция пишется до первого запроса. Если `publish_id` не успел
  сохраниться, повтор заблокирован: проверьте TikTok («Входящие» и профиль).
- Ошибки показывают код площадки (например, `spam_risk_too_many_pending_share` —
  лимит черновиков) и никогда не содержат токенов.

## Официальная документация

- [Instagram: Content Publishing](https://developers.facebook.com/docs/instagram-platform/content-publishing)
- [Instagram: Resumable uploads](https://developers.facebook.com/docs/instagram-platform/content-publishing/resumable-uploads)
- [TikTok: Upload (черновики)](https://developers.tiktok.com/doc/content-posting-api-reference-upload-video)
- [TikTok: Direct Post](https://developers.tiktok.com/doc/content-posting-api-reference-direct-post)
- [TikTok: Login Kit for Desktop](https://developers.tiktok.com/doc/login-kit-desktop)
