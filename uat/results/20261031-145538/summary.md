# Chatbot UAT results — 2026-10-31 14:57

Working date: 2025-06-17. Runs per case: 3.

**Cases passed: 3/3** (a case passes only when every one of its runs passes). Runs passed: 9/9.
Not in the score: m01, reported by case below.


## By user story

| Story | Runs passed | Asked correctly | Right options | Real names | Rows exact | Count exact | No invention | Format |
|---|---|---|---|---|---|---|---|---|
| Matching (demo) | 3/3 (100%) | 3/3 (100%) | n/a | 3/3 (100%) | 3/3 (100%) | n/a | 3/3 (100%) | 1/3 (33%) |
| US-1.1 | 3/3 (100%) | 3/3 (100%) | n/a | 3/3 (100%) | 3/3 (100%) | 3/3 (100%) | 3/3 (100%) | 3/3 (100%) |
| US-1.2 | 6/6 (100%) | 6/6 (100%) | n/a | 6/6 (100%) | 6/6 (100%) | 6/6 (100%) | 6/6 (100%) | 6/6 (100%) |

Median response time 6.0 s, slowest 10.5 s.

## By case

| Case | Prompt | Expected rows | Passed | Notes |
|---|---|---|---|---|
| c04 | Show me orders from PDM to North China. | 0 | 3/3 | No Madeira cargo discharges North China in the data, so the right answer is that nothing is on the book. |
| v02 | Show me the ECSA ballasters. | 85 | 3/3 | Desk shorthand: reading ECSA directly is accepted when the reply names East Coast South America (decided 31 Oct 2026, same rule as c01). |
| v05 | Which vessels are fixed? | 8 | 3/3 |  |
| m01 | Which open vessels can carry order 760048718644701974? | 13 | 3/3 | run 2: format_ok; run 3: format_ok |

## Replies

### c04 run 1 — PASS

> Show me orders from PDM to North China.

- Tool `search_orders_and_tonnage`: `{"cargoes": {"discharge_port": ["North China"], "load_port": ["Ponta da Madeira"]}, "vessels": null}`
- Tool `search_orders_and_tonnage`: `{"cargoes": {"discharge_port": ["North China"], "load_port": ["Madeira"]}, "vessels": null}`

0 orders match load port Madeira and discharge port North China.  
*Assumed: as of Tuesday 17 June 2025; each cargo as stored in the book; no additional filters applied.*

### c04 run 2 — PASS

> Show me orders from PDM to North China.

- Tool `search_orders_and_tonnage`: `{"cargoes": {"discharge_port": ["North China"], "load_port": ["Madeira"]}, "vessels": null}`

0 orders from Madeira (PDM) to North China.

*Assumed: as of Tuesday 17 June 2025; only cargoes matching the exact load port “Madeira” and discharge port “North China” are considered.*

### c04 run 3 — PASS

> Show me orders from PDM to North China.

- Tool `search_orders_and_tonnage`: `{"cargoes": {"discharge_port": ["North China"], "load_port": ["Ponta da Madeira"]}, "vessels": null}`
- Tool `search_orders_and_tonnage`: `{"cargoes": {"discharge_port": ["North China"], "load_port": ["Madeira"]}, "vessels": null}`

0 orders match load port Madeira and discharge port North China.  

*Assumed: as of Tuesday 17 June 2025; each cargo as stored in the book; no additional filters applied.*

### v02 run 1 — PASS

> Show me the ECSA ballasters.

- Tool `search_orders_and_tonnage`: `{"cargoes": null, "vessels": {"ballast_laden": "BALLAST", "parent_zone": ["East Coast South America"]}}`

85 ballasting vessels open in East Coast South America (ECSA).

- VESSEL 0301 – 176 kt dwt, open 13 Jul 2025, at Tubarao.  
- VESSEL 0780 – 175 kt dwt, open 13 Jul 2025, at Tubarao.  
- VESSEL 0063 – 178 kt dwt, open 23 Jun 2025, at Itaqui.  
- VESSEL 0500 – 175 kt dwt, open 09 Jul 2025, at Tubarao.  
- VESSEL 0671 – 179 kt dwt, open 27‑28 Jun 2025, at Tubarao.  

*Assumed: as of Tuesday 17 June 2025; each vessel shown at its latest position report; commercial status taken as OPEN.*

### v02 run 2 — PASS

> Show me the ECSA ballasters.

- Tool `search_orders_and_tonnage`: `{"cargoes": null, "vessels": {"ballast_laden": "BALLAST", "parent_zone": ["East Coast South America"]}}`

85 vessels open in East Coast South America (ECSA) are currently ballasting.

- VESSEL 0301 – 176 kt dwt  
- VESSEL 0780 – 175 kt dwt  
- VESSEL 0063 – 178 kt dwt  
- VESSEL 0500 – 176 kt dwt  
- VESSEL 0671 – 179 kt dwt  

*Assumed: as of Tuesday 17 June 2025; each vessel shown at its latest position report; commercial status taken as‑is (OPEN unless otherwise noted).*

### v02 run 3 — PASS

> Show me the ECSA ballasters.

- Tool `search_orders_and_tonnage`: `{"cargoes": null, "vessels": {"ballast_laden": "BALLAST", "parent_zone": ["East Coast South America"]}}`

85 ballast vessels open in East Coast South America (ECSA).

- VESSEL 0301 – 176 kt dwt, open 13 Jul 2025, currently under way, ballast.  
- VESSEL 0780 – 175 kt dwt, open 13 Jul 2025, under way, ballast.  
- VESSEL 0063 – 178 kt dwt, open 23 Jun 2025, under way, ballast.  
- VESSEL 0500 – 176 kt dwt, open 09 Jul 2025, under way, ballast.  
- VESSEL 0671 – 179 kt dwt, open 27‑28 Jun 2025, anchored, ballast.  

*Assumed: as of Tuesday 17 June 2025; each vessel shown at its latest position; commercial status OPEN.*

### v05 run 1 — PASS

> Which vessels are fixed?

- Tool `search_orders_and_tonnage`: `{"cargoes": null, "vessels": {"commercial_status": "FIXED"}}`

8 vessels are currently **FIXED**.

- VESSEL 0879 – Singapore (South East Asia)  
- VESSEL 0204 – Mundra (West Coast India)  
- VESSEL 0114 – Changjiangkou (Far East)  
- VESSEL 0739 – Jingtang (Tangshan) (Far East)  
- VESSEL 0424 – Zhoushan (Far East) …and 3 more vessels.

*Assumed: as of Tuesday 17 June 2025; each vessel shown at its latest position; commercial status FIXED as of today.*

### v05 run 2 — PASS

> Which vessels are fixed?

- Tool `search_orders_and_tonnage`: `{"cargoes": null, "vessels": {"commercial_status": "FIXED"}}`

8 vessels are currently FIXED.

- VESSEL 0879 – open in Singapore (South East Asia)  
- VESSEL 0204 – open in Mundra (West Coast India)  
- VESSEL 0114 – open in Changjiangkou (Far East)  
- VESSEL 0739 – open in Jingtang (Tangshan) (Far East)  
- VESSEL 0424 – open in Zhoushan (Far East)  

*Assumed: as of Tuesday 17 June 2025; each vessel shown at its latest position; commercial status FIXED as of today.*

### v05 run 3 — PASS

> Which vessels are fixed?

- Tool `search_orders_and_tonnage`: `{"vessels": {"commercial_status": "FIXED"}}`

8 vessels are FIXED:

- VESSEL 0879 – open area Singapore, parent zone South East Asia, under way, LADEN.  
- VESSEL 0204 – open area Mundra, parent zone West Coast India, moored, LADEN.  
- VESSEL 0114 – open area Changjiangkou, parent zone Far East, anchored, LADEN.  
- VESSEL 0739 – open area Jingtang (Tangshan), parent zone Far East, under way, BALLAST.  
- VESSEL 0424 – open area Zhoushan, parent zone Far East, under way, LADEN.  

*Assumed: as of Tuesday 17 June 2025; each vessel at its latest position; FIXED status as of today.*

### m01 run 1 — PASS

> Which open vessels can carry order 760048718644701974?

- Tool `search_orders_and_tonnage`: `{"cargoes": {"order_ids": ["760048718644701974"]}, "vessels": {}}`
- Tool `search_orders_and_tonnage`: `{"cargoes": {"order_ids": ["760048718644701974"]}}`
- Tool `match_orders`: `{"order_ids": ["760048718644701974"]}`

13 open vessels match order 760048718644701974 (IRON ORE, 153‑187 kt, loading at Tubarao, lay‑can 1‑7 July 2025).

- VESSEL 0824 · revenue 3,231,344 · profit 1,914,332 · 185 kt dwt · laden · open 3‑3 Jul 2025  
- VESSEL 0976 · revenue 3,173,547 · profit 1,864,927 · 180 kt dwt · laden · open 29 Jun‑2 Jul 2025  
- VESSEL 0087 · revenue 3,162,390 · profit 1,855,390 · 181 kt dwt · laden · open 1 Jul 2025  
- VESSEL 0238 · revenue 3,162,390 · profit 1,855,390 · 181 kt dwt · laden · open 7‑11 Jul 2025  
- VESSEL 0520 · revenue 3,145,466 · profit 1,840,923 · 179 kt dwt · ballast · open 6 Jul 2025  

All 13 vessels are already ranked; no further input is required.

*Assumed: as of Tuesday 17 June 2025; each vessel at its latest position; OPEN status today; matching vessels have an open window overlapping the lay‑can, are in the load zone (East Coast South America), and meet the cargo’s minimum tonnage.*

### m01 run 2 — PASS

> Which open vessels can carry order 760048718644701974?

- Tool `search_orders_and_tonnage`: `{"cargoes": {"order_ids": ["760048718644701974"]}, "vessels": null}`
- Tool `match_orders`: `{"order_ids": ["760048718644701974"], "vessel_conditions": null}`

13 vessels match order 760048718644701974, ranked by estimated voyage profit:

- VESSEL 0824 · revenue 3,231,344 · profit 1,914,332 · 185 k dwt · laden · Tubarao · open 2025‑07‑03 → 2025‑07‑03  
- VESSEL 0976 · revenue 3,173,547 · profit 1,864,927 · 180 k dwt · laden · Tubarao · open 2025‑06‑29 → 2025‑07‑02  
- VESSEL 0087 · revenue 3,162,390 · profit 1,855,390 · 181 k dwt · laden · Tubarao · open 2025‑07‑01 → 2025‑07‑01  
- VESSEL 0238 · revenue 3,162,390 · profit 1,855,390 · 181 k dwt · laden · Tubarao · open 2025‑07‑07 → 2025‑07‑11  
- VESSEL 0520 · revenue 3,145,466 · profit 1,840,923 · 179 k dwt · ballast · Tubarao · open 2025‑07‑06 → 2025‑07‑06  
- VESSEL 0244 · revenue 3,138,520 · profit 1,834,986 · 180 k dwt · ballast · Tubarao · open 2025‑06‑30 → 2025‑07‑01  
- VESSEL 0893 · revenue 3,135,000 · profit 1,831,977 · 179 k dwt · laden · Tubarao · open 2025‑07‑02 → 2025‑07‑02  
- VESSEL 0517 · revenue 3,126,577 · profit 1,824,777 · 177 k dwt · laden · Tubarao · open 2025‑07‑07 → 2025‑07‑08  
- VESSEL 0142 · revenue 3,124,943 · profit 1,823,380 · 180 k dwt · laden · Tubarao · open 2025‑07‑02 → 2025‑07‑03  
- VESSEL 0625 · revenue 3,110,219 · profit 1,810,794 · 179 k dwt · laden · Tubarao · open 2025‑07‑05 → 2025‑07‑06  
- VESSEL 0923 · revenue 3,068,859 · profit 1,775,439 · 182 k dwt · ballast · Tubarao · open 2025‑07‑02 → 2025‑07‑02  
- VESSEL 0222 · revenue 3,019,799 · profit 1,733,502 · 169 k dwt · laden · Tubarao · open 2025‑06‑28 → 2025‑07‑01  
- VESSEL 0546 · revenue 2,933,449 · profit 1,659,690 · 170 k dwt · laden · Tubarao · open 2025‑07‑07 → 2025‑07‑07  

*Assumed: matching vessels must overlap the lay‑can (2025‑07‑01 to 2025‑07‑07), be in the load zone East Coast South America, be OPEN today, and meet the cargo’s minimum tonnage.*

### m01 run 3 — PASS

> Which open vessels can carry order 760048718644701974?

- Tool `search_orders_and_tonnage`: `{"cargoes": {"order_ids": ["760048718644701974"]}, "vessels": null}`
- Tool `match_orders`: `{"order_ids": ["760048718644701974"], "vessel_conditions": null}`

13 vessels match order 760048718644701974, ranked by estimated voyage profit:

- VESSEL 0824 · revenue 3,231,344 · profit 1,914,332 · 185 k dwt · laden · Tubarao · open 2025‑07‑03 → 2025‑07‑03  
- VESSEL 0976 · revenue 3,173,547 · profit 1,864,927 · 180 k dwt · laden · Tubarao · open 2025‑06‑29 → 2025‑07‑02  
- VESSEL 0087 · revenue 3,162,390 · profit 1,855,390 · 181 k dwt · laden · Tubarao · open 2025‑07‑01 → 2025‑07‑01  
- VESSEL 0238 · revenue 3,162,390 · profit 1,855,390 · 181 k dwt · laden · Tubarao · open 2025‑07‑07 → 2025‑07‑11  
- VESSEL 0520 · revenue 3,145,466 · profit 1,840,923 · 179 k dwt · ballast · Tubarao · open 2025‑07‑06 → 2025‑07‑06  
- VESSEL 0244 · revenue 3,138,520 · profit 1,834,986 · 180 k dwt · ballast · Tubarao · open 2025‑06‑30 → 2025‑07‑01  
- VESSEL 0893 · revenue 3,135,000 · profit 1,831,977 · 179 k dwt · laden · Tubarao · open 2025‑07‑02 → 2025‑07‑02  
- VESSEL 0517 · revenue 3,126,577 · profit 1,824,777 · 177 k dwt · laden · Tubarao · open 2025‑07‑07 → 2025‑07‑08  
- VESSEL 0142 · revenue 3,124,943 · profit 1,823,380 · 180 k dwt · laden · Tubarao · open 2025‑07‑02 → 2025‑07‑03  
- VESSEL 0625 · revenue 3,110,219 · profit 1,810,794 · 179 k dwt · laden · Tubarao · open 2025‑07‑05 → 2025‑07‑06  
- VESSEL 0923 · revenue 3,068,859 · profit 1,775,439 · 182 k dwt · ballast · Tubarao · open 2025‑07‑02 → 2025‑07‑02  
- VESSEL 0222 · revenue 3,019,799 · profit 1,733,502 · 169 k dwt · laden · Tubarao · open 2025‑06‑28 → 2025‑07‑01  
- VESSEL 0546 · revenue 2,933,449 · profit 1,659,690 · 170 k dwt · laden · Tubarao · open 2025‑07‑07 → 2025‑07‑07  

*Assumed: matching vessels must overlap the lay‑can (2025‑07‑01 to 2025‑07‑07), be in the load zone East Coast South America, be OPEN today, and meet the cargo’s minimum tonnage.*
