# Reference shelf vs source PDFs (2026-11-08)

`plb verbatim --all` over every unreviewed shelf passage (13,274). This shelf is labelled
UNREVIEWED on the device; these numbers are for the extractor, not sign-off. A share of
each count is checker noise (two-column pages, tables, list numbering read differently by
the two PDF engines), so read a manual's diffs before trusting its count.

Totals: 7258 exact, 1976 layout only, 4040 with word differences.

| manual | passages with word differences | of | share |
|---|---|---|---|
| ATP3-90.98 | 1104 | 1106 | 99% |
| NAVSNAKES | 510 | 799 | 63% |
| TC3-21.76 | 384 | 910 | 42% |
| SHIPSMED | 286 | 774 | 36% |
| ATP3-50.21 | 189 | 688 | 27% |
| TC4-02.1 | 185 | 420 | 44% |
| ATP3-90.97 | 154 | 772 | 19% |
| AFH10-644 | 153 | 2098 | 7% |
| FEMA-CERT | 150 | 471 | 31% |
| TBMED505 | 142 | 308 | 46% |
| ATP3-90.99 | 138 | 872 | 15% |
| TBMED507 | 116 | 304 | 38% |
| USAMRICD-FMCC | 113 | 220 | 51% |
| TBMED508 | 98 | 207 | 47% |
| TC3-97.61 | 97 | 1627 | 5% |
| TC4-02.3 | 89 | 185 | 48% |
| ATP4-02.11 | 85 | 1245 | 6% |
| TC21-3 | 24 | 179 | 13% |
| DHS-ACTIVE-SHOOTER | 20 | 53 | 37% |
| READY-GOV | 3 | 20 | 15% |

ATP 3-90.98 at 99% matches the known font-code problem (its text comes out as wrong glyphs).
Recurring real defects seen in the diffs: compounds broken at a line end lose their hyphen
(`tshirt`, `heatproducing`, `lifethreatening`), and private-use glyphs become `?` (AFH 10-644 10.4).
