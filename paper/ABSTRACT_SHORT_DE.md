# Kurzfassung

*Zur Vorstellung der Arbeit bei einer möglichen akademischen Kooperation.*

---

DNA-Datenspeicher kodieren unter Sequenzregeln, etwa kein Homopolymer länger als drei und ein
GC-Gehalt zwischen 40 und 60 Prozent. Diese Regeln werden als Konstanten der Chemie behandelt. Wir
finden, dass sie Eigenschaften der Chemie **und des Decoders** sind. Unter algebraischer
Fehlerkorrektur ist ein Strang riskant im Verhältnis dazu, wie viele Fehler er ansammelt. Unter
Rekonstruktion aus mehreren Reads ist er riskant im Verhältnis dazu, wie viele seiner Fehler von
allen Reads geteilt werden, denn den Rest repariert die Abstimmung kostenlos.

Ein kleines Modell, das auf nichts als den Fehlern eines eingefrorenen Rekonstruktions-Decoders
trainiert wurde, bestätigt das, ohne dass man es ihm sagt: es bewertet Deletions-Kontexte mit
0,934 und Substitutions-Kontexte vergleichbarer gemessener Fehlerrate mit 0,444. Als
Bewertungsmodell für Kandidatenstränge eingesetzt, ohne Kosten an Dichte, hebt es die
Dateiwiederherstellung bei 4,5 Reads pro Strang von 27 auf 98,3 Prozent, bei sonst identischem
Codec und über 300 Held-out-Durchläufe je Messpunkt. Der Gewinn übersteht einen Simulator, der nie
zum Tuning benutzt wurde, und er schrumpft, wenn der Decoder stärker wird. Das ist der erste
Beleg dafür, dass das Optimum des Encoders dem Decoder folgt.

Die Kanäle sind simuliert und auf echten veröffentlichten Reads kalibriert. Das Ranking überträgt
sich auf echte Held-out-Nanopore-Cluster. Wir haben nicht gezeigt, dass es die Fehlerrate auf
synthetisierter DNA senkt.

---

**Unsere Bitte:** wir halten das für ein Workshop- oder Methodenpaper und hätten gern jemanden aus
dem Feld, der uns sagt, ob diese Einschätzung stimmt und was dafür nötig wäre.
