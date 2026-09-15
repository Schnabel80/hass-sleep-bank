# Sleep Ledger

Eine Home-Assistant-Integration, die aus täglichen Schlafdaten ein **Langzeitkonto**
führt — statt nur die letzte Nacht zu bewerten.

## Warum

Apple Watch, Oura und Co. liefern einen Score für die vergangene Nacht. Die
Schlafforschung sagt aber klar, dass das die falsche Zeitskala ist:

> Bei chronischer Teilrestriktion (6 h/Nacht über zwei Wochen) sinkt die kognitive
> Leistung **linear weiter**, während die *subjektive* Müdigkeit nach wenigen Tagen
> auf einem Plateau stehenbleibt.

Nach zehn Tagen à 6 h ist man objektiv auf dem Niveau von ein bis zwei durchwachten
Nächten — und merkt es nicht. Der Score der letzten Nacht kann dabei ausgezeichnet
aussehen. Genau diese Lücke schließt diese Integration.

## Was gerechnet wird

| Kennzahl | Grundlage |
|---|---|
| **Akute Schlafschuld** (kumuliert, Minuten) | Leaky-Integrator mit τ ≈ 3 Nächten. Diese Zeitkonstante bildet die gemessene Erholungsdynamik ab: Nach 10 Nächten à 6 h ist das Konto mit rund sieben bis acht Nächten à 8 h ausgeglichen. |
| **Chronisches Defizit** (Minuten pro Nacht) | Derselbe Mechanismus mit τ ≈ 21 Nächten, als gewichtetes Mittel ausgegeben: „ich liege seit Wochen im Schnitt X Minuten unter meinem Bedarf". |
| **Nächte bis Erholung** | Vorwärtssimulation des Kontos für 7 h, 8 h und 9 h Zielschlaf. |
| **Sleep Regularity Index** | Wahrscheinlichkeit, an zwei aufeinanderfolgenden Tagen zur selben Uhrzeit im selben Zustand zu sein (−100 … +100). In der UK-Biobank-Kohorte (n = 60.977) sagte die Regelmäßigkeit die Gesamtmortalität **besser voraus als die Schlafdauer**. |
| **Social Jetlag** | Differenz der Schlafmitte zwischen freien und Arbeitstagen. |
| **Physiologische Erholung** | Ruhepuls, HRV und Atemfrequenz als robuste z-Scores gegen die eigene rollierende Baseline (Median und MAD, 28 Nächte). |
| **Bereitschaftsindex** | Gewichtete Zusammenfassung der drei Bereiche, 0–100. |

### Warum Konto *und* Physiologie

Sie decken verschiedene blinde Flecken ab, und das lässt sich im mitgelieferten
Simulator direkt zeigen: Ein „Wochenend-Nachholer" steht chronisch bei rund
57 Minuten Defizit pro Nacht — seine physiologischen z-Scores sind trotzdem
unauffällig. Die rollierende Baseline hat sich längst an den Dauerzustand
gewöhnt. Ein reines Erholungsmaß übersieht dieses Muster; das Konto sieht es.
Umgekehrt bemerkt das Konto einen Infekt nicht, die Physiologie schon.

## Was das *nicht* ist

Ehrlichkeit gehört zur Sache:

- **Es gibt keinen validierten Consumer-Schlafschuld-Score.** Dies ist ein
  wissenschaftlich motiviertes n=1-Modell, kein Messinstrument und kein
  Medizinprodukt.
- **Der Bereitschaftsindex ist eine Heuristik.** Schlafkonto, Regularität und die
  z-Scores stehen jeweils auf publizierten Befunden — die *Gewichtung* dieser drei
  zu einer Zahl ist frei gewählt. Es gibt keine Studie, die sagt, Schlafschuld
  erkläre 45 % der Leistungsfähigkeit. Der Wert trägt deshalb ein
  `is_heuristic: true` in seinen Attributen und lässt sich umgewichten.
- **Der individuelle Schlafbedarf schwankt stark** (etwa 6–9 h). Die Vorgabe von
  8 h ist ein Populationsmittel. Stell ihn ein oder lass ihn schätzen.
- **Stadienerkennung aus Beschleunigungsdaten ist nur mäßig genau.** Dauer und
  Timing sind die robusten Signale; Tief- und REM-Minuten fließen deshalb nicht
  ins Modell ein, sondern werden nur mitgeführt.

## Der Schlafbedarf — die einflussreichste Zahl

Das chronische Defizit folgt dem angenommenen Bedarf **exakt 1:1**; das steckt in
der Definition `Defizit = Bedarf − Schlaf`. Dieselben Schlafdaten ergeben:

| angenommener Bedarf | akute Schuld | chron. Defizit | Warnung |
|---|---|---|---|
| 420 min | 13 min | −2 min | nein |
| **480 min (Vorgabe)** | 204 min | 48 min | **ja** |
| 540 min | 410 min | 105 min | ja |

Eine Fehlannahme von 30 Minuten erzeugt also 30 Minuten Phantomdefizit pro Nacht
— mehr als die Warnschwelle. Und der wahre Bedarf streut individuell weit:
Kitamura et al. maßen bei 15 gesunden jungen Männern unter neun Tagen erweiterter
Schlafgelegenheit eine optimale Dauer von im Mittel 8,41 h, individuell zwischen
**7,29 und 9,26 h**. Der gewohnte Schlaf zu Hause lag bei 7,37 h — der
Gewohnheitswert **unterschätzt den Bedarf also um rund eine Stunde**.

Deshalb bringt die Integration eine eigene Bedarfsschätzung mit
(`sensor.*_estimated_sleep_need`), getrennt vom angewandten Wert.

### Was „freie Nacht" heißt — und warum der Wochentag nicht reicht

Entscheidend ist nicht der Kalender, sondern ob der Schlaf **von selbst endete**.
Wer auch samstags einen Wecker stellt, hat keine freie Nacht. Genau hier lauert
ein stiller Fehler: Weckergekappte Nächte messen die Weckzeit, nicht den Bedarf,
und würden ihn zu niedrig schätzen — mit einem zu kleinen Defizit als Folge. Eine
Unterschätzung ist gefährlicher als gar keine Schätzung, weil sie beruhigt.

Die Integration erkennt das deshalb aktiv: Extern auferlegte Weckzeiten bündeln
sich eng. Jede Nacht wird **einzeln** danach eingeteilt, ob ihre Aufwachzeit
deutlich vom gewohnten Weckzeitpunkt abweicht — in welche Richtung, ist
gleichgültig. Wer dienstags um 08:00 statt 06:15 aufwacht, hat Urlaub; wer eine
Stunde vor dem Wecker aufwacht, hat ausgeschlafen.

Findet sich keine einzige ungestörte Nacht, meldet der Sensor offen
`all_nights_alarm_constrained` statt einer Zahl.

### Vier Güteklassen

| Verfahren | Grundlage | Konfidenz |
|---|---|---|
| `calibration` | ein markierter Zeitraum ohne Wecker | 0,9 |
| `pooled_runs` | zusammenhängende ungestörte Nächte über Monate | 0,65 |
| `last_night_of_run` | letzte Nacht je Lauf, wenn es für einen Fit nicht reicht | 0,4 |
| `none` | keine Schätzung, mit Begründung am Sensor | — |

Die Schätzung wird **nie** sprunghaft übernommen. Ist „Schlafbedarf automatisch
schätzen" aktiv, zieht der angewandte Wert höchstens rund fünf Minuten pro Monat
nach und protokolliert jede Änderung. Ein Sprung im Nenner sähe im Verlauf des
Kontos aus wie eine plötzliche Schlafkrise und machte ihn über Jahre
unvergleichbar.

### Kalibrierlauf — der Weg, wenn der Wecker immer klingelt

`switch.*_sleep_need_calibration` einschalten, sobald eine Phase ohne Wecker
beginnt (Urlaub, freie Woche), und danach wieder ausschalten. Über genau diese
Nächte wird die Abklingkurve gelegt und ihre Asymptote als Bedarf genommen — die
Heimnachbildung des publizierten Laborprotokolls.

Zwei Dinge dazu:

- **Mindestens vier Nächte, besser sechs.** Bei Kitamura war der Wert erst ab der
  vierten Nacht stabil; die erste lag 3,22 h über dem Gewohnheitswert und misst
  vor allem die *Schuld*. Kürzere Läufe werden abgelehnt oder abgewertet.
- **Der Lauf braucht keine Zeitstempel.** Mit dem Markieren versichert man, dass
  kein Wecker lief. Das ist der einzige Weg, der auch ohne Fokus-Sensor oder
  Schrittzähler trägt.

Der Schalter lässt sich automatisieren, etwa über einen Urlaubskalender.

Die Integration findet einen längeren ungestörten Lauf übrigens auch **ohne**
Markierung — dann als `pooled_runs` mit geringerer Konfidenz, weil niemand
bestätigt hat, dass wirklich kein Wecker lief.

### Ändert sich der Bedarf über die Jahre?

Ja, auf zwei Zeitskalen — und die schnellere ist die wichtigere.

**Langsam, über Jahrzehnte.** Klerman und Dijk maßen unter 16 h Schlafgelegenheit
einen Asymptotenwert von 8,9 h bei jungen und 7,4 h bei älteren Erwachsenen, grob
20 Minuten pro Jahrzehnt. Über fünf Jahre Nutzung sind das ~10 Minuten, deutlich
unter der Unsicherheit jeder Heimschätzung. Ein explizites Altersmodell wäre
Scheingenauigkeit; die laufende Neuschätzung fängt das von selbst ab. Ob es
überhaupt sinkender *Bedarf* ist, ist strittig — das NIA hält dagegen, dass
Ältere dieselben 7–9 h brauchen und nur die *Fähigkeit* zu schlafen abnimmt.

**Schnell, über Wochen.** Das ist die relevantere Änderung: Infekte, Impfungen,
körperliche Belastung und kognitive Last erhöhen den Bedarf, dazu Lebensphasen
wie ein Kind, ein neues Trainingspensum oder Schichtbeginn. Deshalb wird laufend
neu geschätzt statt einmal eingestellt.

### Wenn der Bedarf unsicher bleibt

Das Attribut `change_vs_4_weeks_min` am chronischen Defizit ist
**bedarfsunabhängig**: Eine Fehlannahme verschiebt beide Zeitpunkte um denselben
Betrag und fällt in der Differenz heraus. Der absolute Wert mag daneben liegen —
die Richtung stimmt.

## Grundsatz: keine Zahl ohne Datenbasis

Jede Kennzahl meldet `unknown`, solange ihre Datengrundlage nicht trägt — nie
einen Platzhalter. Ein Schlafkonto aus drei Nächten wäre eine Zahl ohne Aussage,
und im Diagramm sähe sie genauso echt aus wie eine belastbare.

Konkret:

- Die akute Schuld braucht 3 Nächte, das chronische Defizit 14.
- Der SRI braucht 10 auswertbare Tagespaare, die z-Scores 14 Baseline-Nächte.
- Ohne Einschlaf-/Aufwachzeiten bleiben SRI, Schlafmitte und Social Jetlag
  dauerhaft `unknown`.
- **Eine fehlende Nacht ist eine Lücke, keine Nacht mit null Stunden Schlaf.**
  Lud die Uhr nachts oder fiel ein Sync aus, wird das Konto nicht belastet.
- `sensor.*_data_coverage` und `binary_sensor.*_insufficient_data` zeigen
  jederzeit, wie belastbar die aktuelle Grundlage ist.

## Installation

### Über HACS

1. HACS → Integrationen → Menü → Benutzerdefinierte Repositories
2. `https://github.com/Schnabel80/hass-sleep-ledger` als Kategorie *Integration*
   hinzufügen
3. „Sleep Ledger" installieren, Home Assistant neu starten
4. Einstellungen → Geräte & Dienste → Integration hinzufügen → „Sleep Ledger"

### Manuell

Ordner `custom_components/sleep_ledger` nach `<config>/custom_components/`
kopieren und Home Assistant neu starten.

## Einrichtung

Drei Schritte:

**1. Quellentitäten.** Zwingend ist nur die Schlafdauer. Sensoren der iOS-
Companion-App werden automatisch vorgeschlagen. Jede Quelle funktioniert —
Companion-App, Health Auto Export, Oura, Withings, Sleep as Android, eigene
Template-Sensoren.

**2. Zeiterfassung.** Liegen echte Zeitstempel vor, werden sie verwendet. Sonst
siehe unten.

**3. Schlafbedarf und freie Tage.**

Alles lässt sich später über den Optionsdialog ändern.

## Wie die Schlafzeiten ermittelt werden

**Aufwachen ist messbar, Einschlafen nicht.** Wer im Bett liest, erzeugt keine
Schritte und hat den Schlafmodus längst aktiv — jede aktivitätsbasierte
Einschlaferkennung setzt den Schlafbeginn systematisch zu früh an. Die Uhr weiß
dagegen, wann tatsächlich Schlaf einsetzte, und meldet das als Dauer. Also wird
genau ein Anker am Morgen bestimmt und der Rest zurückgerechnet:

```
Einschlafzeit = Aufwachzeit − (Schlafdauer + nächtliche Wachzeit)
```

Der Aufwach-Anker entsteht aus bis zu vier Kandidaten, die sich gegenseitig
stützen:

| Kandidat | Konfidenz |
|---|---|
| Explizite Aufwachzeit-Entität | 1,0 |
| Fokus-Sensor schaltet im Morgenfenster ab | 0,8 |
| Beginn anhaltender Schrittaktivität | 0,7 |
| Ankunft der Nachtdaten in HA | 0,5 (gilt als Obergrenze) |

Stimmen mehrere Kandidaten auf ±45 Minuten überein, steigt die Konfidenz.

### Warum mehrere Kandidaten nötig sind

Der Fokus-Sensor allein trägt nicht. In sieben Tagen eines realen Verlaufs traten
drei verschiedene Ausfallarten auf — alle drei sind als Testfälle hinterlegt:

- **Tages-Fokus:** 13:16 → 14:29 Uhr „on", kein Schlaf. Abgefangen über eine
  Mindestdauer von 3 Stunden.
- **Verlorener Übergang:** 32 Stunden am Stück „on". Abgefangen über eine
  Höchstdauer von 16 Stunden.
- **Totalausfall:** eine ganze Nacht ohne jeden Übergang, obwohl morgens saubere
  Schlafdaten eintrafen. Genau dafür gibt es den Datenankunfts-Anker.
- **`unavailable`-Blips** bei App-Neustarts werden nie als Zustandswechsel
  gewertet.

Ein nächtlicher Toilettengang beendet den Schlaf übrigens nicht: Ein
Aktivitätsausbruch zählt nur, wenn er anhaltend ist. Kurze Ausbrüche werden als
Unterbrechung gezählt und fließen in die Fragmentierung ein.

## Entitäten

| Entität | Einheit | Inhalt |
|---|---|---|
| `sensor.*_acute_sleep_debt` | min | Kumulierte akute Schuld (negativ = Guthaben) |
| `sensor.*_chronic_deficit_per_night` | min | Mittleres Defizit pro Nacht |
| `sensor.*_nights_to_recovery` | — | Nächte bis zum Ausgleich; Szenarien in den Attributen |
| `sensor.*_sleep_regularity_index` | — | SRI, −100 … +100 |
| `sensor.*_social_jetlag` | h | Differenz der Schlafmitte frei/Arbeitstag |
| `sensor.*_sleep_midpoint` | — | Mittlere Schlafmitte als Uhrzeit |
| `sensor.*_midpoint_variability` | min | Streuung der Schlafmitte |
| `sensor.*_physiological_recovery` | — | Erholungs-z-Score; Einzelwerte in den Attributen |
| `sensor.*_readiness` | — | Bereitschaft 0–100 (**Heuristik**) |
| `sensor.*_data_coverage` | % | Erfasste Nächte im Auswertungsfenster |
| `sensor.*_last_night_confidence` | % | Verlässlichkeit der letzten Nacht |
| `sensor.*_last_night_duration` | min | Dauer der letzten Nacht |
| `binary_sensor.*_sleep_debt_warning` | — | Chronisches Defizit über Schwelle |
| `binary_sensor.*_insufficient_data` | — | Datenlage trägt die Langzeitmetriken nicht |
| `sensor.*_estimated_sleep_need` | min | Geschätzter Bedarf; Verfahren, Grund und Konfidenz in den Attributen |
| `switch.*_sleep_need_calibration` | — | Kalibrierlauf ohne Wecker markieren |
| `number.*` | — | Schlafbedarf, Warnschwelle, Gewichte der Bereitschaft |
| `button.*_recalculate` | — | Historie neu auswerten |

## Beispiele

**Schonmodus nach schlechter Woche** — beachte die Absicherung gegen dünne Daten:

```yaml
automation:
  - alias: Schonmodus bei chronischem Defizit
    triggers:
      - trigger: state
        entity_id: binary_sensor.sleep_ledger_sleep_debt_warning
        to: "on"
    conditions:
      - condition: state
        entity_id: binary_sensor.sleep_ledger_insufficient_data
        state: "off"
    actions:
      - action: notify.mobile_app
        data:
          message: >
            Chronisches Defizit von
            {{ states('sensor.sleep_ledger_chronic_deficit_per_night') }} min/Nacht.
            Ausgleich in {{ states('sensor.sleep_ledger_nights_to_recovery') }} Nächten.
```

**Weckzeit an die Schuld koppeln:**

```yaml
  - alias: Später wecken bei hoher Schlafschuld
    triggers:
      - trigger: numeric_state
        entity_id: sensor.sleep_ledger_acute_sleep_debt
        above: 240
    actions:
      - action: input_datetime.set_datetime
        target:
          entity_id: input_datetime.wecker
        data:
          time: "07:00:00"
```

## Fehlersuche

**Alle Langzeitwerte sind `unknown`.** Normal am Anfang — das Modell braucht
14 erfasste Nächte. `sensor.*_data_coverage` zeigt den Fortschritt. Beim ersten
Start werden Nächte aus der Langzeitstatistik des Quellsensors rekonstruiert
(Tagesmaximum), sofern vorhanden; diese tragen eine reduzierte Konfidenz.

**Der geschätzte Bedarf bleibt leer.** Sieh dir das Attribut `reason` an.
`all_nights_alarm_constrained` heißt: Jede Nacht endet am Wecker, es gibt nichts
zu messen — dann hilft nur ein Kalibrierlauf. `no_timing_data` heißt: Es fehlen
Aufwachzeiten, ohne die sich die Weckerbindung nicht prüfen lässt; auch hier
funktioniert der Kalibrierlauf trotzdem.

**SRI und Social Jetlag bleiben `unknown`.** Es fehlen Zeitstempel. Prüfe, ob
Fokus-Sensor oder Schrittzähler konfiguriert sind und die
`anchor_provenance`-Attribute von `sensor.*_last_night_confidence` plausibel
aussehen.

**Eine Nacht fehlt.** Die Uhr hat nicht gemessen oder nicht synchronisiert. Das
ist gewollt folgenlos: Lücken belasten das Konto nicht. Der Diagnose-Download
(Gerät → Diagnose herunterladen) enthält die vollständige Historie mit Herkunft
und Konfidenz je Nacht.

## Entfernen

Einstellungen → Geräte & Dienste → Sleep Ledger → Menü → Löschen. Die gespeicherte
Nachthistorie wird dabei mitgelöscht. Bei Installation über HACS anschließend dort
deinstallieren.

## Bildmarke

Icon und Logo liegen in `custom_components/sleep_ledger/brand/` (`icon.png`,
`icon@2x.png`, `logo.png`, `logo@2x.png`, `dark_logo*.png`) in den von Home
Assistant geforderten Größen.

Die Gestaltung: eine **Mondsichel** für den Schlaf über einem **Speicher mit
Füllstand und Ziellinie** — der Füllstand bleibt unter der Marke, also genau das
Bild eines Schlafkontos im Minus.

Quelle ist `artwork/icon-source.png`. `artwork/make_brand_assets.py` stellt die weißen
Ecken frei, setzt die Motivfarbe über die Kante hinaus fort (sonst entsteht beim
Verkleinern ein heller Saum) und schreibt alle Ausgabeformate:

```bash
uvx --with pillow --with numpy --with pyoxipng python artwork/make_brand_assets.py
```

## Entwicklung

```bash
uv sync --group dev
uv run ruff format . && uv run ruff check .
uv run ty check custom_components/
uv run pytest
uv run python simulator/run_simulation.py
```

Die Modellmodule (`debt`, `regularity`, `physiology`, `readiness`, `timing`,
`models`) importieren **nichts** aus Home Assistant. Die Mathematik bleibt damit
unabhängig überprüfbar und frei von versteckten Abhängigkeiten auf Zustand,
Zeitzone oder Konfiguration der Instanz; der Simulator verwendet sie direkt.
`tests/test_architecture.py` sichert die Trennung statisch ab.

## Offene Punkte

- **Nickerchen:** Ob die Quelle sie in die Tagessumme einrechnet, ist
  geräteabhängig. Derzeit wird nur ein Zuwachs außerhalb des Morgenfensters als
  Nickerchen gewertet. Die Annahme gehört an echten Daten überprüft.
- **Health-Auto-Export-Webhook** für echte Zeitstempel und historischen Backfill
  ist vorgesehen; das Datenmodell ist darauf vorbereitet.

## Quellen

- [Dynamics of recovery sleep from chronic sleep restriction — SLEEP Advances](https://academic.oup.com/sleepadvances/article/4/1/zpac044/6854927)
- [Sleep regularity is a stronger predictor of mortality risk than sleep duration — SLEEP](https://academic.oup.com/sleep/article/47/1/zsad253/7280269)
- [The two-process model from a mathematical perspective — npj Biological Timing and Sleep](https://www.nature.com/articles/s44323-025-00039-z)
- [The Role of Sleep Banking in Reducing Cognitive and Motor Impairments — MDPI](https://www.mdpi.com/2624-5175/8/1/8)
- [Social jetlag, sleep, and metabolic syndrome — Sleep Science and Practice](https://link.springer.com/article/10.1186/s41606-025-00158-3)
- [Estimating Sleep Stages from Apple Watch (PDF)](https://www.apple.com/health/pdf/Estimating_Sleep_Stages_from_Apple_Watch_Oct_2025.pdf)
- [Kitamura et al.: Estimating individual optimal sleep duration and potential sleep debt — Scientific Reports](https://pmc.ncbi.nlm.nih.gov/articles/PMC5075948/)
- [SONA: The Sleep Opportunity, Need and Ability Theory — Journal of Sleep Research](https://pmc.ncbi.nlm.nih.gov/articles/PMC12592830/)
- [NIA: Sleep and Older Adults](https://www.nia.nih.gov/health/sleep/sleep-and-older-adults)

## Lizenz

Apache-2.0
