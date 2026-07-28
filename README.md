# Local Paperless-ngx MCP

Ein lokaler [FastMCP](https://gofastmcp.com/)-Server für die
[Paperless-ngx REST API](https://docs.paperless-ngx.com/api/). Er verbindet
MCP-fähige Clients über `stdio` mit deiner eigenen Paperless-ngx-Instanz.

## Funktionen

- Verbindung und API-Version prüfen
- Dokumente einfach, nach Titel, mit erweiterter Paperless-Syntax oder nach
  Ähnlichkeit suchen
- Dokument-Metadaten und OCR-Text abrufen
- Tags, Korrespondenten, Dokumenttypen, Speicherpfade, Custom Fields und
  gespeicherte Ansichten vollständig und paginiert auflisten
- Organisationsqualität über das gesamte Archiv zusammenfassen
- Dokumente ohne ausgewählte Metadaten finden
- Organisationsobjekte erstellen, umbenennen und konfigurieren
- Korrespondenten, Dokumenttypen und Speicherpfade in Batches zuweisen
- Tags additiv hinzufügen oder entfernen
- Dokumente ausschließlich in den wiederherstellbaren Papierkorb verschieben
- Ausgewählte Dokument-Metadaten aktualisieren
- Schreibzugriffe standardmäßig blockieren
- Endgültiges Löschen und Leeren des Papierkorbs technisch blockieren

## Voraussetzungen

- Python 3.11 oder neuer
- [`uv`](https://docs.astral.sh/uv/)
- Eine erreichbare Paperless-ngx-Instanz mit API-Token

Das Token erzeugst du in Paperless-ngx unter **Profil → API-Token**.

## Installation

```bash
git clone git@github.com:frankherchet/local-paperless-ngx-mcp.git
cd local-paperless-ngx-mcp
cp .env.example .env
uv sync --extra dev
```

Trage anschließend URL und Token in `.env` ein:

```dotenv
PAPERLESS_URL=http://localhost:8000
PAPERLESS_TOKEN=dein-api-token
PAPERLESS_READ_ONLY=true
```

Die `.env`-Datei wird nicht von Git erfasst.

## Starten

Direkt über den Projekteinstieg:

```bash
uv run paperless-ngx-mcp
```

Oder über die FastMCP-Projektkonfiguration:

```bash
uv run fastmcp run fastmcp.json
```

## MCP-Client konfigurieren

Für einen lokalen MCP-Client kannst du folgende `stdio`-Konfiguration verwenden.
Ersetze `<REPO>` durch den absoluten Pfad zu diesem Repository.

```json
{
  "mcpServers": {
    "paperless-ngx": {
      "command": "uv",
      "args": [
        "--directory",
        "<REPO>",
        "run",
        "paperless-ngx-mcp"
      ]
    }
  }
}
```

FastMCP lädt die Zugangsdaten aus der `.env` im Projektverzeichnis. Alternativ
kann der Client `PAPERLESS_URL` und `PAPERLESS_TOKEN` direkt als
Umgebungsvariablen an den Prozess übergeben.

## Verfügbare Tools

| Tool | Zweck | Schreibzugriff |
| --- | --- | --- |
| `paperless_status` | Verbindung, Versionen und Dokumentanzahl prüfen | Nein |
| `search_documents` | Dokumente in vier Suchmodi finden | Nein |
| `get_document` | Dokument, OCR-Text und optional Datei-Prüfsummen laden | Nein |
| `list_metadata` | Organisationsobjekte und Workflows listen | Nein |
| `list_workflows` / `get_workflow` | Workflows paginieren oder einzeln lesen | Nein |
| `create_workflow` / `update_workflow` | Verschachtelte Paperless-Workflows anlegen oder patchen | Ja |
| `delete_workflow` | Workflow als Dry-run prüfen oder nach Freigabe löschen | Optional |
| `configure_default_intake` | Standard-Eingang für ausschließlich neue Dokumente konfigurieren | Optional |
| `verify_default_intake` | Standard-Eingang lesend prüfen | Nein |
| `get_organization_overview` | Nutzung, Dubletten und Zuordnungslücken zusammenfassen | Nein |
| `find_documents_missing_metadata` | Dokumente ohne ausgewählte Metadaten finden | Nein |
| `find_documents_by_metadata` | Dokumente zu einem Organisationsobjekt finden | Nein |
| `create_organization_item` | Organisationsobjekte erstellen | Ja |
| `update_organization_item` | Organisationsobjekte umbenennen oder konfigurieren | Ja |
| `bulk_edit_objects` | `/api/bulk_edit_objects/` mit Vorschau und Sicherheitsprüfung | Optional |
| `set_document_metadata_field` | Korrespondent, Typ oder Speicherpfad setzen/leeren | Ja |
| `modify_document_tags` | Tags additiv hinzufügen oder entfernen | Ja |
| `list_trashed_documents` | Inhalt des Papierkorbs auflisten | Nein |
| `move_documents_to_trash` | Dokumente in den wiederherstellbaren Papierkorb verschieben | Ja |
| `restore_documents_from_trash` | Dokumente aus dem Papierkorb wiederherstellen | Ja |
| `update_document` | Dokumentfelder per REST-PATCH ändern oder leeren | Optional |
| `document_notes` | Dokumentnotizen auflisten oder anlegen | Optional |

`update_document` funktioniert erst nach:

```dotenv
PAPERLESS_READ_ONLY=false
```

Auch bei deaktiviertem Read-only-Modus kann der MCP keine Dokumente endgültig
löschen:

- Es wird kein Dokument-Löschtool registriert.
- HTTP-`DELETE` ist im API-Client vollständig gesperrt.
- `trash empty` wird explizit blockiert.
- Nicht freigegebene Bulk-Methoden wie `merge` oder `delete_pages` werden
  abgewiesen.
- `move_documents_to_trash` nutzt ausschließlich Paperless' wiederherstellbaren
  Papierkorb.

Workflow-Objekte können ausschließlich über `delete_workflow` gelöscht werden;
der Standard ist immer `dry_run=true`. Dies berührt weder bestehende Dokumente
noch den Papierkorb.

### Standard-Eingang für neue Dokumente

`configure_default_intake(storage_path_id=18, dry_run=true)` plant einen eigenen
Workflow mit dem festen Namen `Standard-Eingang – neue Dokumente`. Er greift auf
jedes neu hinzugefügte Dokument (`Document Added`) zu und weist den gewählten
Speicherpfad zu. Bestehende Dokumente werden nie verändert.

Die Fachfunktion ist idempotent: Sie erstellt ausschließlich diesen einen
Workflow oder aktualisiert ausschließlich diesen, falls seine Definition von der
Soll-Konfiguration abweicht. Sie lässt alle anderen Workflows unverändert und
wählt eine Reihenfolge nach vorhandenen Speicherpfad-Zuweisungen. Vor einer
Änderung zuerst den Dry-run prüfen; danach mit `dry_run=false` erneut ausführen.
`verify_default_intake()` bestätigt lesend Name, Aktivierung, Trigger,
Speicherpfad und fehlende Filter.

Unbenutzte Tags, Korrespondenten, Dokumenttypen und Speicherpfade können dagegen
über das REST-orientierte Tool `bulk_edit_objects` gezielt entfernt werden. Das
Tool bildet `POST /api/bulk_edit_objects/` mit den API-Feldern `objects`,
`object_type` und `operation` ab. Sicherheitsvorkehrungen werden zentral
angewendet:

1. Der Standard `dry_run=true` prüft `document_count`, Workflow-Trigger und
   Workflow-Aktionen; bei Tags werden zusätzlich untergeordnete Tags als
   Referenzen berücksichtigt.
2. Das Ergebnis wird dem Benutzer gezeigt und die ausdrückliche Freigabe
   eingeholt.
3. Derselbe Aufruf mit `dry_run=false` wiederholt die Referenzprüfung unmittelbar
   vor dem Löschen und bricht die gesamte Anfrage ab, wenn ein Eintrag verwendet
   wird oder nicht mehr existiert.

Das Bulk-Tool ist im MCP als destruktiv markiert. Für `operation=delete` sind nur
die vier genannten Organisationstypen freigegeben. Dokumente, Custom Fields,
Saved Views und andere Objekte sind darüber nicht löschbar.

`list_metadata(object_type="workflows")` liefert die vollständigen Trigger und
Aktionen für eine manuelle Prüfung. Die Löschsperre führt dieselbe
Workflow-Referenzprüfung serverseitig erneut aus. Wenn Workflows nicht gelesen
werden können, schlägt die Prüfung fehl und es wird nichts gelöscht.

Für eine sichere Dublettenprüfung kann `get_document` mit
`include_file_metadata=true` aufgerufen werden. Die Antwort enthält dann unter
`file_metadata` insbesondere `original_checksum` und `archive_checksum`.
`document_notes` bildet die GET- und POST-Operationen von
`/api/documents/{id}/notes/` ab; das Löschen von Notizen wird wegen der globalen
HTTP-`DELETE`-Sperre nicht angeboten.

### Organisationsprüfung mit AI

Die Analyse-Tools laden keine OCR-Inhalte. Ein sinnvoller Einstieg in einem
MCP-fähigen Chat ist:

> Prüfe mit `get_organization_overview`, ob meine Paperless-Organisation
> schlüssig ist. Bewerte Tags, Korrespondenten, Dokumenttypen, Speicherpfade,
> Custom Fields und Saved Views. Behandle ungenutzte Einträge nur als
> Prüfkandidaten und schlage noch keine Löschungen vor.

Für Detailprüfungen kann das Modell anschließend mit `list_metadata` durch die
jeweilige Objektart paginieren. `find_documents_missing_metadata` liefert
kompakte Dokument-Metadaten zu fehlenden Zuordnungen, ohne den OCR-Text zu
übertragen.

Empfohlener Ablauf:

1. Analyse und Bereinigungsplan im Read-only-Modus erstellen.
2. Vorgeschlagene Zielstruktur durch den Benutzer bestätigen lassen.
3. `PAPERLESS_READ_ONLY=false` setzen und den MCP neu starten.
4. Änderungen in kleinen Batches durchführen und jeweils kontrollieren.
5. Unbenutzte Organisationsobjekte mit `bulk_edit_objects` und `dry_run=true`
   prüfen, die Vorschau bestätigen lassen und denselben Aufruf anschließend mit
   `dry_run=false` ausführen.

## Entwicklung

```bash
uv run ruff format .
uv run ruff check .
uv run mypy
uv run pytest
```

Alles in einem Durchlauf:

```bash
uv run ruff format --check . \
  && uv run ruff check . \
  && uv run mypy \
  && uv run pytest
```

## Sicherheit

- Der Server lauscht standardmäßig nicht auf einem Netzwerkport, sondern nutzt
  ausschließlich `stdio`.
- API-Token gehören nur in `.env` oder in die Prozessumgebung.
- Schreibzugriffe sind standardmäßig deaktiviert.
- Endgültiges Löschen von Dokumenten sowie das Leeren des Papierkorbs sind auch
  bei aktivierten Schreibzugriffen nicht möglich.
- Suchergebnisse enthalten keinen vollständigen OCR-Text; dieser wird nur über
  `get_document` und mit einem konfigurierbaren Größenlimit geliefert.
