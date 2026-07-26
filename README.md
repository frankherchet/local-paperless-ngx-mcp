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
- Ausgewählte Dokument-Metadaten aktualisieren
- Schreibzugriffe standardmäßig blockieren

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
| `get_document` | Metadaten und begrenzten OCR-Text laden | Nein |
| `list_metadata` | Organisationsobjekte mit Details und Matching-Modus listen | Nein |
| `get_organization_overview` | Nutzung, Dubletten und Zuordnungslücken zusammenfassen | Nein |
| `find_documents_missing_metadata` | Dokumente ohne ausgewählte Metadaten finden | Nein |
| `update_document` | Titel, Datum, Zuordnungen, Tags oder ASN ändern | Optional |

`update_document` funktioniert erst nach:

```dotenv
PAPERLESS_READ_ONLY=false
```

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
- Suchergebnisse enthalten keinen vollständigen OCR-Text; dieser wird nur über
  `get_document` und mit einem konfigurierbaren Größenlimit geliefert.
