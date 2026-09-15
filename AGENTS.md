# Sleep Ledger — Arbeitsanweisungen

Home-Assistant-Custom-Integration, die aus Schlafdaten ein Langzeitkonto führt.
Wissenschaftlicher Hintergrund und Nutzersicht stehen im README; hier stehen die
Regeln für die Arbeit am Code.

## Architektur

```
custom_components/sleep_ledger/
  models.py      SleepNight, Provenance          ← rein
  debt.py        Schlafkonto                     ← rein
  regularity.py  SRI, Schlafmitte, Social Jetlag ← rein
  physiology.py  robuste Baselines, z-Scores     ← rein
  readiness.py   Bereitschaftsheuristik          ← rein
  sleep_need.py  Bedarfsschätzung, Weckererkennung ← rein
  timing.py      Aufwach-Anker, Rückrechnung     ← rein
  ingest.py      Entitäten/Recorder → SleepNight
  store.py       Persistenz der Nachthistorie
  coordinator.py Orchestrierung
  config_flow.py Einrichtung und Optionen
  entity.py      Basisklasse aller Entitäten
  sensor.py binary_sensor.py number.py button.py diagnostics.py
```

## Invarianten

Diese Regeln sind nicht verhandelbar. Jede von ihnen hat einen Grund, der
mindestens einmal Geld oder Vertrauen gekostet hätte.

1. **Die mit „rein" markierten Module importieren nichts aus `homeassistant`** —
   und auch nicht aus `const.py`, das seinerseits HA importiert. Sie nehmen
   `SleepNight`-Listen und geben Zahlen zurück.

   Geprüft wird das **statisch** in `tests/test_architecture.py`, nicht durch
   einen Importversuch: `sleep_ledger/__init__.py` ist der Einstiegspunkt von
   Home Assistant und zieht HA beim Paketimport zwangsläufig mit herein. Der Test
   schlägt auch an, wenn ein *neues* HA-freies Modul entsteht, das nicht in der
   Liste steht — damit die Trennung bewusst gezogen bleibt.

2. **Eine fehlende Nacht ist eine Lücke, keine Nacht mit null Stunden Schlaf.**
   Lücken dürfen nirgends als Nullwert in eine Summe, ein Mittel oder ein Fenster
   eingehen. Sie werden ausschließlich als Zerfall behandelt.

3. **Keine Zahl ohne Datenbasis.** Jede Kennzahl liefert `None` (→ `unknown`),
   solange ihre Mindestanforderung nicht erfüllt ist. Nie ein Ersatzwert, nie
   eine Extrapolation aus zu wenigen Nächten.

4. **Der Bereitschaftsindex bleibt als Heuristik gekennzeichnet.** `is_heuristic`
   im Ergebnisobjekt, im Entitätsattribut und in der Doku. Das Flag darf nicht
   stillschweigend verschwinden; `tests/test_readiness.py` sichert es ab.

5. **Modellparameter stehen in ihrem Modellmodul, nicht in `const.py`.**
   `const.py` ist die HA-Seite (Domain, Config-Keys, Speicher, Entitätsschlüssel)
   und importiert Home Assistant — die reinen Module dürfen es deshalb nicht
   importieren. Zeitkonstanten und Gewichte stehen direkt neben der Mathematik,
   die sie steuern. Magische Zahlen inline sind trotzdem verboten.

6. **Übergänge von und nach `unavailable` sind keine Zustandswechsel.** Ein
   Neustart der Companion-App erzeugt sie regelmäßig. Wer sie auswertet, bekommt
   Phantomnächte.

7. **Jede neue übersetzbare Zeichenkette landet in `translations/de.json` *und*
   `translations/en.json`.** `strings.json` ist die Kopie der englischen Fassung.

8. **Einschlafzeiten werden nicht gemessen, sondern zurückgerechnet.** Siehe
   `timing.py`. Wer eine aktivitätsbasierte Einschlaferkennung einbaut, macht das
   Ergebnis systematisch schlechter, nicht besser.

9. **Eine freie Nacht ist eine Nacht ohne Wecker, kein Wochentag.** Der Bedarf
   darf nie aus weckergekappten Nächten geschätzt werden — das unterschätzt ihn
   und beschönigt damit das Defizit, die gefährlichere Fehlerrichtung. Die
   Weckerprüfung erfolgt **je Nacht**, nicht über die Gruppe der freien Tage, und
   das Toleranzfenster ist nach oben gedeckelt. Drei Regressionen hingen daran:
   eine Urlaubswoche verdeckte die Weckerbindung aller Wochenenden; Urlaubstage
   unter der Woche wurden übersehen, solange nur früheres Erwachen zählte; und
   ein mitwachsendes Toleranzfenster bescheinigte jeder Streuung, eng gebündelt
   zu sein. Alle drei sind in `tests/test_sleep_need.py` festgehalten.

10. **Der angewandte Schlafbedarf springt nie.** Automatische Übernahme nur
    gedeckelt (rund fünf Minuten im Monat) und protokolliert. Ein Sprung im
    Nenner macht den Kontoverlauf über Jahre unvergleichbar.

## Bildmarke

Quelle ist die gestaltete Vorlage `brand/icon-source.png`. `brand/make_brand_assets.py`
leitet daraus alle von Home Assistant geforderten Fassungen ab. Die erzeugten
Dateien in `custom_components/sleep_ledger/` nie von Hand nachbearbeiten — wer die
Gestaltung ändern will, tauscht die Vorlage aus und lässt das Skript neu laufen.

```bash
uvx --with pillow --with numpy --with pyoxipng python brand/make_brand_assets.py
```

Drei Dinge, die das Skript erledigt und die man nicht weglassen darf:

- **Weiße Ecken freistellen.** Die Vorlage ist ein undurchsichtiges Quadrat; auf
  einem dunklen Theme sähe das aus wie ein kaputtes Bild.
- **Motivfarbe über die Kante hinaus fortsetzen**, bevor skaliert wird. Sonst
  zieht beim Verkleinern Weiß aus den Randpixeln in die Kante und es entsteht ein
  heller Saum.
- **Nur verlustfrei komprimieren.** Die Vorlage enthält feines Rauschen im
  Farbverlauf; eine Reduktion der Farbtiefe erzeugt dort sichtbare Streifen. Die
  rund 190 kB des großen Icons stammen aus der Vorlage und lassen sich ohne
  Eingriff in die Grafik nicht sinnvoll drücken.

Der Schriftzug wird mit einer Systemschrift gesetzt (macOS: Avenir Next Demi
Bold, sonst DejaVu Sans Bold). Auf einem anderen System erzeugt ein erneuter Lauf
daher einen anderen Schnitt — die eingecheckten PNGs sind maßgeblich.

## Werkzeuge

```bash
uv sync --group dev
uv run ruff format .
uv run ruff check .
uv run ty check custom_components/
uv run pytest
uv run python simulator/run_simulation.py
```

`uv.lock` wird eingecheckt.

## Tests

- `tests/real_history.py` enthält **aufgezeichnete Sensorverläufe aus einer echten
  Installation**, keine erdachten Randfälle. Die vier Ausfallarten des
  Fokus-Sensors (Tages-Fokus, 32-Stunden-Hänger, Totalausfall, `unavailable`-Blip)
  sind dort belegt und in `tests/test_timing.py` abgesichert. Diese Fixtures nicht
  „aufräumen".
- `tests/test_debt.py` prüft das Modell gegen publizierte Befunde. Wenn diese
  Tests fallen, ist das Modell kaputt — auch wenn der Code läuft.
- `tests/test_simulation.py` prüft die Rangfolge der Kennzahlen über synthetische
  Schläfertypen. Fängt Regressionen, die einzelne Formeln nicht bemerken.

## Release

1. `version` in `manifest.json` und `pyproject.toml` gleichziehen
2. `uv run ruff check . && uv run ty check custom_components/ && uv run pytest`
3. Auf `develop` committen, GitHub-Release mit passendem Tag anlegen
4. Betas als Pre-Release; nur die letzten beiden installierbar lassen
