# Sicherheit

## Zugangsdaten

`paperless-ngx-mcp setup` speichert URL und API-Token in einer lokalen,
benutzerspezifischen Konfigurationsdatei. Auf POSIX-Systemen erzwingt der Server
für diese Datei Rechte `0600` und für ihr Verzeichnis `0700`. Die Datei ist nicht
verschlüsselt. Verwende daher ausschließlich ein geschütztes Benutzerkonto und
teile die Datei nicht.

Der Server liest keine `.env`-Datei automatisch. Prozessvariablen sind für
automatisierte und headless Einsätze weiterhin möglich; `PAPERLESS_URL` und
`PAPERLESS_TOKEN` müssen dabei gemeinsam gesetzt werden.

Der API-Token wird weder in Toolantworten noch in CLI-Ausgaben angezeigt.

## Dokumente

Der Server stellt kein Werkzeug zum endgültigen Löschen von Dokumenten bereit,
sendet für Dokumente keine HTTP-`DELETE`-Anfragen und leert den Paperless-
Papierkorb nicht.

## Sicherheitslücken melden

Bitte keine Zugangsdaten oder Sicherheitslücken in öffentlichen Issues
veröffentlichen. Melde sie stattdessen vertraulich an den Repository-Eigentümer.
