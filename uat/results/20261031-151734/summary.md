# Chatbot UAT results — 2026-10-31 15:18

Working date: 2025-06-17. Runs per case: 3.

**Cases passed: 2/2** (a case passes only when every one of its runs passes). Runs passed: 6/6.

## By user story

| Story | Runs passed | Asked correctly | Right options | Real names | Rows exact | Count exact | No invention | Format |
|---|---|---|---|---|---|---|---|---|
| US-1.2 | 6/6 (100%) | 6/6 (100%) | n/a | 6/6 (100%) | 6/6 (100%) | 6/6 (100%) | 6/6 (100%) | 6/6 (100%) |

Median response time 5.3 s, slowest 6.9 s.

## By case

| Case | Prompt | Expected rows | Passed | Notes |
|---|---|---|---|---|
| v03 | Show me the WAF openers. | 18 | 3/3 | Expected answer reads 'openers' as where the vessel opens, with no fixture-status filter. Confirm with the sponsor. |
| v04 | Show me open vessels in the Far East of at least 180,000 dwt. | 131 | 3/3 |  |

## Replies

### v03 run 1 — PASS

> Show me the WAF openers.

- Tool `search_orders_and_tonnage`: `{"cargoes": null, "vessels": {"commercial_status": "OPEN", "parent_zone": ["West Africa"]}}`
- Tool `search_orders_and_tonnage`: `{"cargoes": null, "vessels": {"parent_zone": ["West Africa"]}}`

18 vessels open in West Africa (commercial status OPEN, parent zone West Africa).

- VESSEL 0295 – open 05‑07 to 14‑07 at Abidjan (LADEN)  
- VESSEL 0791 – open 29‑06 to 29‑06 at Boffa (LADEN)  
- VESSEL 1000 – open 25‑05 to 30‑05 at Lagos (LADEN)  
- VESSEL 0538 – open 05‑05 to 06‑05 at Freetown (BALLAST)  
- VESSEL 0154 – open 02‑05 to 03‑05 at Port Kamsar (BALLAST)

*Assumed: as of Tuesday 17 June 2025; each vessel shown at its latest position report; commercial status OPEN as of today.*

### v03 run 2 — PASS

> Show me the WAF openers.

- Tool `search_orders_and_tonnage`: `{"cargoes": null, "vessels": {"commercial_status": "OPEN", "parent_zone": ["West Africa"]}}`
- Tool `search_orders_and_tonnage`: `{"cargoes": null, "vessels": {"parent_zone": ["West Africa"]}}`

18 vessels open in West Africa (WAF)  

- VESSEL 0295 – LADEN, open 5‑14 Jun 2025, open area Abidjan  
- VESSEL 0725 – BALLAST, open 13 Jun 2025 (single‑day), open area Brazil / Guinea  
- VESSEL 0886 – BALLAST, open 2‑5 Aug 2025, open area Brazil / West Africa  
- VESSEL 0791 – LADEN, open 29 Jun 2025 (single‑day), open area Boffa  
- VESSEL 0716 – LADEN, open 11 Jun 2025 (single‑day), open area Tubarao / Port Kamsar  

*Assumed: as of Tuesday 17 June 2025; each vessel at its latest position; commercial status OPEN as of today.*

### v03 run 3 — PASS

> Show me the WAF openers.

- Tool `search_orders_and_tonnage`: `{"cargoes": null, "vessels": {"commercial_status": "OPEN", "parent_zone": ["West Africa"]}}`
- Tool `search_orders_and_tonnage`: `{"cargoes": null, "vessels": {"parent_zone": ["West Africa"]}}`

18 vessels open in West Africa (WAF)  

- VESSEL 0295 – open 5‑14 Jun 2025, open area Abidjan, LADEN  
- VESSEL 0725 – open 13 Jun 2025, open area Brazil & Guinea, BALLAST  
- VESSEL 0791 – open 29 Jun 2025, open area Boffa, LADEN  
- VESSEL 1000 – open 25‑30 May 2025, open area Lagos, LADEN  
- VESSEL 0052 – open 14‑18 May 2025, open area Boffa, BALLAST  

*Assumed: as of Tuesday 17 June 2025; each vessel at its latest position; OPEN as of today.*

### v04 run 1 — PASS

> Show me open vessels in the Far East of at least 180,000 dwt.

- Tool `search_orders_and_tonnage`: `{"cargoes": null, "vessels": {"commercial_status": "OPEN", "dwt_min": 180000, "parent_zone": ["Far East"]}}`

**Open vessels in the Far East ≥ 180 kt dwt:** 131 matches.

- VESSEL 0049 – Anchored, BALLAST, open area Jiangyin / Qidong (Jiangsu)  
- VESSEL 0143 – Moored (tied), LADEN, open area Jingtang (Tangshan)  
- VESSEL 0401 – Moored (tied), LADEN, open area Changjiangkou  
- VESSEL 0400 – Under way using its engine, BALLAST, open area Zhuhai  
- VESSEL 0171 – Under way using its engine, LADEN, open area Jingtang (Tangshan)  

*Assumed: as of Tuesday 17 June 2025; each vessel at its latest position; commercial status OPEN.*

### v04 run 2 — PASS

> Show me open vessels in the Far East of at least 180,000 dwt.

- Tool `search_orders_and_tonnage`: `{"cargoes": null, "vessels": {"commercial_status": "OPEN", "dwt_min": 180000, "parent_zone": ["Far East"]}}`

131 open vessels match Far East ≥ 180 kt DWT.

- VESSEL 0049 – 180 544 dwt – Anchored – open area Jiangyin, Qidong (Jiangsu)  
- VESSEL 0143 – 187 888 dwt – Moored (tied) – open area Jingtang (Tangshan)  
- VESSEL 0401 – 181 146 dwt – Moored (tied) – open area Changjiangkou  
- VESSEL 0400 – 180 882 dwt – Under way using its engine – open area Zhuhai  
- VESSEL 0171 – 181 725 dwt – Under way using its engine – open area Jingtang (Tangshan)  

*Assumed: as of Tuesday 17 June 2025; each vessel at its latest position; commercial status OPEN.*

### v04 run 3 — PASS

> Show me open vessels in the Far East of at least 180,000 dwt.

- Tool `search_orders_and_tonnage`: `{"cargoes": null, "vessels": {"commercial_status": "OPEN", "dwt_min": 180000, "parent_zone": ["Far East"]}}`

**Open vessels in the Far East ≥ 180 kt dwt – 131 matches**  

- VESSEL 0049 – 180.5 kt, anchored, BALLAST, open area Jiangyin / Qidong (Jiangsu)  
- VESSEL 0143 – 187.9 kt, moored (tied), LADEN, open area Jingtang (Tangshan)  
- VESSEL 0401 – 181.1 kt, moored (tied), LADEN, open area Changjiangkou  
- VESSEL 0400 – 180.9 kt, under way (engine), BALLAST, open area Zhuhai  
- VESSEL 0171 – 181.7 kt, under way (engine), LADEN, open area Jingtang (Tangshan)  

*Assumed: as of Tuesday 17 June 2025; each vessel at its latest position; commercial status OPEN.*
