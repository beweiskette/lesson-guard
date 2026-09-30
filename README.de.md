# lesson-guard

[English](README.md)

KI-Programmieragenten sammeln Lektionen als Text: Notizen im Gedächtnis, Regeln in `CLAUDE.md` und
`AGENTS.md`, Protokolle von Pannen. Solche Regeln werden überlesen, vergessen oder von der nächsten
Anweisung verdrängt, und derselbe Fehler passiert wieder. lesson-guard macht aus diesen Notizen kleine
ausführbare Wächter («Guards»). Sie laufen als Hook im Agenten und als git-pre-commit-Hook, halten den
Fehler auf, bevor er passiert, und sagen dem Agenten, was er stattdessen tun soll.

Jeder Guard bringt eigene Testfälle mit: Beispiele, die er abfangen muss, und Beispiele, die er
durchlassen muss. Ein Guard, dessen Tests scheitern, wird nie aktiv.

Andere Projekte schreiben solche Wächter von Hand oder halten nur neue Regeln als Text fest.
lesson-guard übersetzt die Notizen, die schon da sind, in Guards, prüft sie mit ihren Tests und liefert
denselben Satz an Claude Code, Codex und git.

## Ablauf

```
notes/*.md  --compile-->  guards/proposed/*.yaml  --enable-->  guards/*.yaml  --check-->  Claude-Code-Hook
                          guards/rejected/*.yaml  (Tests gescheitert)                    Codex-Hook
                          guards/drafts/*.yaml    (--llm none)                           git pre-commit
```

1. `lesson-guard compile NOTIZORDNER` liest Markdown-Notizen (Front Matter mit `name`, `description`
   und `type` wird ausgewertet) und schlägt Guards vor. Mit `--llm claude` schreibt ein Modell die
   Vorschläge, mit `--llm none` entsteht pro Notiz ein Gerüst zum Ausfüllen von Hand.
2. Jeder Vorschlag wird geprüft und seine eigenen Tests laufen. Wer besteht, landet in
   `guards/proposed/`, wer scheitert, in `guards/rejected/` samt Begründung.
3. Aktiv wird nichts, bis du `lesson-guard enable ID` aufrufst. Dabei laufen die Tests noch einmal.
4. `lesson-guard check` prüft die aktiven Guards gegen die Daten eines Hooks oder gegen die
   vorgemerkten Dateien eines Commits.

## Installation

Python 3.11 oder neuer. Einzige Laufzeitabhängigkeit ist PyYAML.

```
git clone https://github.com/beweiskette/lesson-guard.git
cd lesson-guard
python -m pip install .
```

`pipx install .` geht auch und hält das Werkzeug aus deinen Projektumgebungen heraus. Die Hooks rufen
`lesson-guard` auf, der Befehl muss also im `PATH` des Agenten liegen. Sonst gib den vollen Pfad an:
`lesson-guard install ... --command /pfad/zu/lesson-guard`.

## Schnellstart

Im Ordner `examples/` liegen sieben erfundene Notizen, vorbereitete Modellantworten und die daraus
entstandenen Guards.

```
lesson-guard test --guards examples/guards
lesson-guard compile examples/notes --out /tmp/guards --llm fake:examples/fake-llm/responses.json
```

Ausgabe des zweiten Befehls:

```
proposed  no-class-files-in-backups        /tmp/guards/proposed/no-class-files-in-backups.yaml
proposed  no-double-encoded-utf8           /tmp/guards/proposed/no-double-encoded-utf8.yaml
proposed  no-direct-edit-generated         /tmp/guards/proposed/no-direct-edit-generated.yaml
proposed  image-server-no-free             /tmp/guards/proposed/image-server-no-free.yaml
proposed  keep-mixed-line-endings          /tmp/guards/proposed/keep-mixed-line-endings.yaml
proposed  no-secrets-in-files              /tmp/guards/proposed/no-secrets-in-files.yaml
proposed  no-env-in-archives               /tmp/guards/proposed/no-env-in-archives.yaml
rejected  image-server-no-free-loose       /tmp/guards/rejected/image-server-no-free-loose.yaml
            - tests.pass[0] should pass but was caught: 'curl https://example.org/freedom'
skipped   examples/notes/answer-style.md: note type 'user' rarely describes a checkable mistake

7 proposed, 1 rejected, 0 drafts, 1 skipped, 0 errors. Nothing was enabled; use 'lesson-guard enable ID'.
```

Der abgelehnte Guard reagierte auf `/free` an jeder Stelle, deshalb scheiterte sein eigener
Durchlass-Test mit `/freedom`. Genau dafür tragen Guards Tests mit sich: Ein zu grober Wächter wird
von genervten Nutzern abgeschaltet, also wird er gestoppt, bevor er je aktiv ist.

Einen Hook-Aufruf kannst du von Hand nachstellen:

```
echo '{"tool_name":"Bash","tool_input":{"command":"curl -X POST http://localhost:9000/free"}}' \
  | lesson-guard check --json-stdin --format claude --guards examples/guards
```

```json
{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "lesson-guard blocked this action:\n- [image-server-no-free] Calling /free unloads every model on the shared image server for all users. Wait for the queue to drain or ask the owner instead. (lesson: examples/notes/image-server-free.md)"}}
```

## Eigene Notizen

```
lesson-guard compile pfad/zu/notizen --out guards --llm claude   # Modell schlägt Guards vor (kostet Tokens)
lesson-guard compile pfad/zu/notizen --out guards --llm none     # Gerüste für Handarbeit
lesson-guard list --all
lesson-guard test guards/proposed
lesson-guard enable keep-mixed-line-endings
```

`--llm claude` ruft pro Notiz, die nach einer Regel aussieht, einmal
`claude -p --output-format json --json-schema ... --tools ""` auf. Das Modell hat dabei keine
Werkzeuge, und die Antwort muss dem Schema entsprechen. Der Text der Notiz geht an das Modell, also
kompiliere nur Notizen, die du auch in einen Chat kopieren würdest. Das Feld `source` setzt der
Compiler selbst, nicht das Modell.

Übersprungen werden Notizen mit `type: user` oder `type: reference` im Front Matter und Notizen ohne
regelartige Wörter (never, always, do not, nie, immer, nicht, statt und ähnliche). `--all-notes`
schaltet diesen Filter ab, `--exclude GLOB` lässt Dateien wie ein Inhaltsverzeichnis aus.

## Aufbau eines Guards

```yaml
id: keep-mixed-line-endings
source: examples/notes/line-endings.md
description: Files that mix CRLF and LF must keep their line endings.
severity: block                  # block oder warn
event: [pre_edit, pre_commit]    # pre_tool, pre_edit, pre_commit
match:
  predicates:
  - line_endings_changed
  - name: mixed_line_endings
    on: original
message: This file mixes CRLF and LF on purpose and the change normalizes that. Keep the
  original line endings and change only the lines you mean to change.
tests:
  block:
  - path: tools/runner.py
    original: "a = 1\r\nb = 2\nc = 3\r\n"
    content: "a = 1\nb = 2\nc = 4\n"
  pass:
  - path: tools/runner.py
    original: "a = 1\r\nb = 2\nc = 3\r\n"
    content: "a = 1\r\nb = 2\nc = 4\r\n"
```

Ereignisse und die Felder, die ein Guard sieht:

| Ereignis | wann | Felder |
|---|---|---|
| `pre_tool` | vor einem Shell-Befehl oder einem anderen Werkzeugaufruf ohne Dateiänderung | `tool`, `command` (bei anderen Werkzeugen: die Eingabe als JSON) |
| `pre_edit` | bevor der Agent eine Datei schreibt | `tool`, `path`, `content` (Datei nach der Änderung, falls bekannt), `original` (Datei vorher), `added` (neu hinzukommender Text) |
| `pre_commit` | für jede vorgemerkte Datei bei `git commit` | `path`, `content` (Stand im Index), `original` (Stand in HEAD), `added` (neue Zeilen) |

Bedingungen in `match`. Alle angegebenen Bedingungen müssen zutreffen, eine Liste heisst «eine davon
genügt».

| Schlüssel | Bedeutung |
|---|---|
| `tool` | regulärer Ausdruck, muss den ganzen Werkzeugnamen treffen (`Bash`, `PowerShell`, `Write`, `Edit`, `apply_patch` und weitere) |
| `command`, `command_not` | regulärer Ausdruck, gesucht im Befehl |
| `path`, `path_not` | Glob; `**` geht über Ordnergrenzen, ein Glob ohne `/` prüft nur den Dateinamen |
| `content`, `content_not`, `added`, `added_not` | regulärer Ausdruck (mehrzeilig), gesucht im Text |
| `predicates` | Liste von Prädikaten, `!name` verneint, oder `{name, on, negate}` |

Prädikate:

| Name | Standardziel | trifft zu, wenn |
|---|---|---|
| `mixed_line_endings` | `content` | der Text mehr als eine Art von Zeilenende enthält |
| `line_endings_changed` | original und content | sich die Arten der Zeilenenden vorher und nachher unterscheiden |
| `utf8_double_encoded` | `added` | der Text Zeichensalat wie `Ã¤` oder `â€™` enthält |
| `contains_secret_like` | `added` | bekannte Schlüsselformate, Kopfzeilen privater Schlüssel oder zufällig wirkende Werte vorkommen, die Namen wie `api_key`, `token` oder `password` zugewiesen sind |
| `file_is_generated_marker` | `original` | die ersten 30 Zeilen `@generated`, `DO NOT EDIT`, `auto-generated` oder Ähnliches enthalten |

`on` kann `content`, `original`, `added`, `command` oder `any` sein.

Testfälle verwenden dieselben Felder. Das Ereignis ist ohne Angabe das erste des Guards, das Werkzeug
`Bash`, `Write` oder `git`. Bei Dateiänderungen gilt `added` ohne Angabe als gleich `content`, wie bei
einem Schreibvorgang über die ganze Datei. Jeder Guard braucht mindestens einen `block`-Fall und einen
`pass`-Fall.

## Anbindung an die Agenten

`lesson-guard install claude|codex|git` gibt die Konfiguration aus. Mit `--write PFAD` wird der Eintrag
in diese Datei eingefügt. Bestehende Hooks bleiben erhalten, ein zweiter Aufruf ändert nichts. Der
Guard-Ordner wird als absoluter Pfad eingetragen.

### Claude Code

```
lesson-guard install claude --guards guards --write .claude/settings.json
```

Das trägt einen `PreToolUse`-Hook für `Bash|PowerShell|Write|Edit|MultiEdit|NotebookEdit` ein, der
`lesson-guard check --format claude --json-stdin` aufruft. Bei einem blockierenden Treffer kommt
`permissionDecision: "deny"` zurück, als Begründung die Meldungen der Guards, die der Agent liest. Eine
Warnung liefert `additionalContext` für das Modell und eine `systemMessage` für dich, aber keine
Entscheidung. Die übliche Rückfrage nach der Berechtigung bleibt damit bestehen.

### Codex CLI

```
lesson-guard install codex --guards guards --write .codex/hooks.json
```

Das trägt einen `PreToolUse`-Hook für `Bash|apply_patch` ein, der
`lesson-guard check --format codex --json-stdin` aufruft. Ein Patch wird in eine `pre_edit`-Aktion pro
Datei zerlegt. Codex behandelt `permissionDecision: "ask"` und einige andere Felder als nicht
unterstützt und lässt den Aufruf dann durch. Der Codex-Adapter gibt deshalb nur `deny`,
`additionalContext` und `systemMessage` aus.

### git

```
lesson-guard install git --guards guards --write .git/hooks/pre-commit
```

Der Hook ruft `lesson-guard check --event pre_commit` auf und endet mit 1, wenn ein Guard blockiert.
Einen vorhandenen pre-commit-Hook, der nicht von lesson-guard stammt, überschreibt er nicht.

### Verhalten bei Fehlern

Scheitert lesson-guard selbst (kaputte Hook-Daten, unlesbarer Guard), meldet `check` den Grund und
endet mit 1. Beide Agenten werten das als nicht blockierenden Fehler: Der Aufruf läuft weiter, und du
siehst die Meldung. Mit `--fail-closed` endet der Befehl mit 2 und blockiert. Ungültige Guard-Dateien
werden mit einer Meldung übersprungen, die gültigen laufen trotzdem.

## Befehle

| Befehl | Zweck |
|---|---|
| `check` | aktive Guards prüfen; `--json-stdin` für Agenten-Hooks, `--event pre_commit` für git |
| `test [PFAD...]` | die Tests in Guard-Dateien ausführen |
| `list [--all]` | aktive Guards auflisten, mit `--all` auch Vorschläge, Entwürfe und abgelehnte |
| `compile NOTIZORDNER` | Guards vorschlagen; `--llm claude`, `none` oder `fake:DATEI` |
| `enable ID` / `disable ID` | einen Guard aktivieren oder zurück zu den Vorschlägen legen |
| `install claude/codex/git` | Hook-Konfiguration ausgeben oder einfügen |

`--guards ORDNER` wählt überall den Guard-Ordner (Standard `guards` oder `$LESSON_GUARD_DIR`).

## Grenzen

- Guards bestehen aus regulären Ausdrücken, Globs und ein paar Prädikaten. Sie erkennen die Form eines
  Fehlers, nicht die Absicht dahinter. Ein Befehl, der dasselbe auf anderem Weg tut, kommt durch.
- Hooks sehen Werkzeugaufrufe. Ein Skript, das eine Datei schreibt, ist ein `pre_tool`-Befehl und
  kein `pre_edit`.
- Bei `apply_patch`-Änderungen von Codex wird der Dateiinhalt nach dem Patch nicht rekonstruiert.
  `line_endings_changed` kann dort nicht greifen; der git-pre-commit-Hook fängt es trotzdem ab. Bei
  `Edit` von Claude wird der Inhalt nur rekonstruiert, wenn `old_string` wörtlich in der Datei steht.
- `contains_secret_like` ist eine Heuristik. Geheimnisse in ungewohnten Formaten rutschen durch, und
  zufällige Testdaten können anschlagen.
- Globs unterscheiden Gross- und Kleinschreibung. Dateien über 2 MB und Binärdateien werden nicht
  geprüft.
- Reguläre Ausdrücke aus Modellvorschlägen werden nicht auf katastrophales Backtracking geprüft. Lies
  vorgeschlagene Guards, bevor du sie aktivierst.
- Die Hook-Adapter folgen den veröffentlichten Hook-Protokollen von Claude Code und Codex, und
  Unit-Tests prüfen das ausgegebene JSON. In einer laufenden Agentensitzung wurden sie während der
  Entwicklung nicht ausprobiert, und das Backend `--llm claude` wurde nur mit einem nachgebauten
  Prozessaufruf getestet, weil die Tests nie ein kostenpflichtiges Modell aufrufen.

## Lizenz

MIT, siehe [LICENSE](LICENSE).
