# Discord-Helper

Bot für den Discord der Fachschaft IT: Regelannahme, Rollenmenüs für Studiengang, Semester, Fächer und Events, Honeypot gegen Spam-Bots, Join-to-Create-Sprachkanäle und geplante Nachrichten.

Der Aufbau des Servers steht in [server_layout.toml](server_layout.toml). [provision.py](provision.py) legt daraus Rollen, Kanäle, Rechte und Menüs an. Es löscht nichts und darf nach Änderungen erneut laufen.

## Rollenreihenfolge

Von oben nach unten in den Servereinstellungen:

1. `admin`
2. `Fachschaftsvertreter`
3. `Aktives Fachschaftsmitglied`
4. Bot-Rolle
5. alle übrigen Rollen

Der Bot kann nur Rollen unter seiner eigenen vergeben. Deshalb steht er unter den Fachschaftsrollen und kann sie auch nicht versehentlich über ein Menü vergeben.

Dadurch kann der Bot Fachschaftsmitglieder nicht bannen. Schreibt ein gehackter Fachschafts-Account in den Honeypot, wird die Nachricht gelöscht, und der fehlgeschlagene Bann erscheint in `#bot-log`.

`admin` steht ganz oben und sieht die Fachschaftskanäle trotzdem nicht, solange die Rolle kein Administrator-Recht hat. Mit „Rollen verwalten“ können sich Admins die Fachschaftsrollen aber selbst geben. Das ist technisch nicht verhindert, aber im Audit-Log sichtbar.

`Fachschaftsvertreter` und `Aktives Fachschaftsmitglied` dürfen Leute stummschalten (Rechte „Timeout“ und „Mitglieder stummschalten“). Kicken und Bannen bleibt bei `admin`.

## Ersteinrichtung

### 1. Bot anlegen

Ein normaler Discord-Account kann kein Bot werden, der Bot ist immer ein eigener Account. Am besten meldet ihr euch mit dem Fachschafts-Account im [Discord Developer Portal](https://discord.com/developers/applications) an, damit die Anwendung der Fachschaft gehört und nicht einer Einzelperson. Der Account sollte Zwei-Faktor-Authentifizierung nutzen: Verlangt der Server 2FA für Moderation, kann der Bot sonst nicht bannen.

Dort eine Anwendung mit Bot anlegen und die Privileged Intents **Server Members** und **Message Content** aktivieren. Den Bot mit diesem Link einladen (`CLIENT_ID` ersetzen):

```text
https://discord.com/oauth2/authorize?client_id=CLIENT_ID&scope=bot+applications.commands&permissions=2252111588224630
```

### 2. Auf dem Fachschaftsserver vorbereiten

```bash
cp example.config.json config.json
cp .env.example .env              # DISCORD_TOKEN eintragen
```

Optional: `BOT_STATUS` setzt den Status-Text, `PUID` und `PGID` müssen zum Besitzer von `config.json` passen (Standard 1000).

Danach `server_layout.toml` prüfen. Ist der Bot auf mehreren Servern, muss dort `guild_id` gesetzt sein.

### 3. Server aufbauen

```bash
docker compose build
docker compose stop bot
docker compose run --rm bot python provision.py           # zeigt nur die Änderungen
docker compose run --rm bot python provision.py --apply   # führt sie aus
docker compose up -d
```

Der Bot muss beim Einrichten gestoppt sein, sonst überschreibt er das Ergebnis in `config.json`.

### 4. Manuelle Schritte

Diese Schritte bleiben bewusst Handarbeit, weil sie den Zugang zum Server ändern. Reihenfolge einhalten:

1. Rollen wie oben beschrieben sortieren und den beiden Fachschaftsrollen die Rechte „Timeout“ und „Mitglieder stummschalten“ geben.
2. In `/settings` > Role menus den Button **Repost / refresh all** drücken.
3. Bei `admin` das Recht **Administrator** entfernen. Stattdessen aktivieren: Kanäle verwalten, Rollen verwalten, Server verwalten, Audit-Log ansehen, Kicken, Bannen, Timeout, Nachrichten verwalten, Nachrichten anpinnen, Mitglieder stummschalten, verschieben und taub schalten sowie die normalen Rechte aus `Mitglied`. Vorher prüfen, dass der Server-Eigentümer erreichbar ist.
4. Bei `@everyone` alle Serverrechte entfernen. Ab jetzt sieht man ohne Regelannahme nur noch `#regeln` und `#exmatrikulation`. Das gilt auch für bestehende Mitglieder, die den Button noch einmal drücken müssen.
5. `python provision.py` erneut ohne `--apply` ausführen. Es sollte keine `MANUAL`-Warnung mehr erscheinen.

Der Server-Eigentümer sieht immer alle Kanäle, das lässt sich in Discord nicht ändern.

### 5. Abnahme mit einem normalen Zweit-Account

Admins werden vom Honeypot nicht gebannt, deshalb einen Account ohne besondere Rechte verwenden.

- [ ] Vor der Regelannahme sind nur `#regeln` und `#exmatrikulation` sichtbar
- [ ] Der Regel-Button schaltet den Server frei, ein zweiter Klick nimmt nichts weg
- [ ] Eine SPO-Rolle schaltet den passenden `fächerwahl`-Kanal frei
- [ ] Eine Fach-Rolle schaltet den Fach-Chat frei
- [ ] Event-Kanäle sind lesbar, Schreiben geht erst mit Event-Rolle
- [ ] `➕ Raum erstellen` erzeugt einen Raum, der leer wieder verschwindet
- [ ] Die Rolle `International Student` stellt die Bot-Antworten auf Englisch um
- [ ] `admin` sieht die Kategorie `Fachschaft` nicht, `Fachschaftsvertreter` sieht `fsv` und `allgemeines`, `Aktives Fachschaftsmitglied` nur `allgemeines`
- [ ] Eine Nachricht in `#exmatrikulation` führt zum Bann und erscheint in `#bot-log`, danach den Testaccount wieder entbannen

## Betrieb

| Aufgabe | Vorgehen |
|---|---|
| Update einspielen | `git pull && docker compose up -d --build` |
| Logs ansehen | `docker compose logs -f bot` |
| Fach, SPO oder Studiengang ändern | `server_layout.toml` anpassen, Abschnitt 3 ausführen, dann **Repost / refresh all**. Quelle sind die [Modulhandbücher](https://www.hs-esslingen.de/informatik-und-informationstechnik/fakultaet/service/it/sammlung-modulhandbuecher-fakultaet-informationstechnik). |
| Sichern | `config.json` und `.env` |
| Einstellungen ändern | `/settings` auf Discord |

Rollen und Kanäle, die aus dem Layout entfernt werden, bleiben auf Discord bestehen und müssen von Hand gelöscht werden. Dasselbe gilt für das zugehörige Menü.
