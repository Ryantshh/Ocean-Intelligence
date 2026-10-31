# Chatbot UAT results — 2026-10-27 17:06

Working date: 2025-06-17. Runs per case: 1.

## By user story

| Story | Runs passed | Asked correctly | Rows exact | Count exact | No invention | Format |
|---|---|---|---|---|---|---|
| US-1.1 | 0/1 (0%) | 1/1 (100%) | 0/1 (0%) | 0/1 (0%) | 1/1 (100%) | 1/1 (100%) |
| US-1.2 | 0/1 (0%) | 1/1 (100%) | 0/1 (0%) | 0/1 (0%) | 1/1 (100%) | 1/1 (100%) |

Median response time 8.7 s, slowest 9.5 s.

## By case

| Case | Prompt | Expected rows | Passed | Notes |
|---|---|---|---|---|
| c05 | Show me today's cargo list. | 28 | 0/1 | run 1: rows_ok, count_ok |
| v02 | Show me the ECSA ballasters. | 85 | 0/1 | run 1: option_ok, rows_ok, count_ok |

## Replies

### c05 run 1 — FAIL

> Show me today's cargo list.

- Tool `search_orders_and_tonnage`: `{"cargoes": {"laycan_end_from": "2025-06-17", "laycan_start_to": "2025-07-17", "updated_from": "2025-06-12"}, "vessels": null}`
- Missing vs expected: []; extra: ['100732926791440709', '103890718298647154', '119703167752801768', '285706810420592088', '313174108092414313', '331519099919830518', '371395245880992145', '3872431200759984', '418386711500631548', '456000061016644374', '475844583210441342', '526545912022994435', '559622898267766515', '632578143262134108', '662970406363730276', '687295348107690718', '700116495699637602', '739320110791643063', '75302271507637965', '755871880273236329']

57 cargoes match today’s list.

- **371395245880992145** – Iron ore (170 kt) loading at Itaqui (East Coast South America), lay‑can 26 Jun – 06 Jul.  
- **700116495699637602** – Coal loading at Hay Point (East Australia), lay‑can 16 Jun – 19 Jun.  
- **559622898267766515** – Iron‑ore pellets loading at Tubarao (East Coast South America), lay‑can 13 Jun – 27 Jun.  
- **418386711500631548** – Pig iron (200 kt) loading at Riga (Baltic), lay‑can 25 Jun – 27 Jun.  
- **995758371143559451** – Iron ore loading at Acu Superport (East Coast South America), lay‑can 23 Jun – 27 Jun.  

*Assumed: as of Tuesday 17 June 2025; each cargo at its latest update; live‑book windows still open today, opening within 30 days, updated in the last 5 days.*

### v02 run 1 — FAIL

> Show me the ECSA ballasters.

- Asked: *Which region does “ECSA” refer to?* — offered: East Coast South Africa, West Africa, South Africa — answered: East Coast South America
- Asked: *Which zone should be used for “ECSA”?* — offered: South Africa, East Coast South Africa, West Africa — answered: East Coast South America
- Tool `ask_user`: `{"questions": [{"header": "Region", "options": [{"description": "The East Coast of South Africa zone", "label": "East Coast South Africa"}, {"description": "West African zone", "label": "West Africa"}, {"description": "South Africa zone (overall)", "label": "South Africa"}], "question": "Which region does \u201cECSA\u201d refer to?"}]}`
- Tool `ask_user`: `{"questions": [{"header": "Zone", "options": [{"description": "South Africa zone (overall)", "label": "South Africa"}, {"description": "East Coast of South Africa (if available)", "label": "East Coast South Africa"}, {"description": "West Africa zone", "label": "West Africa"}], "question": "Which zone should be used for \u201cECSA\u201d?"}]}`
- Tool `search_orders_and_tonnage`: `{"vessels": {"ballast_laden": "BALLAST", "commercial_status": "OPEN", "parent_zone": ["East Coast South Africa"]}}`
- Missing vs expected: ['VESSEL 0007', 'VESSEL 0019', 'VESSEL 0044', 'VESSEL 0057', 'VESSEL 0063', 'VESSEL 0065', 'VESSEL 0106', 'VESSEL 0127', 'VESSEL 0166', 'VESSEL 0170', 'VESSEL 0173', 'VESSEL 0179', 'VESSEL 0208', 'VESSEL 0210', 'VESSEL 0215', 'VESSEL 0232', 'VESSEL 0244', 'VESSEL 0246', 'VESSEL 0248', 'VESSEL 0253']; extra: []

No open ballasted vessels are listed for the East Coast South Africa zone.

*Assumed: as of Tuesday 17 June 2025; each vessel at its latest position; OPEN as of today.*
