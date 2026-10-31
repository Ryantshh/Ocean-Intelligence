# Chatbot UAT results — 2026-10-31 15:08

Working date: 2025-06-17. Runs per case: 3.

**Cases passed: 1/1** (a case passes only when every one of its runs passes). Runs passed: 3/3.

## By user story

| Story | Runs passed | Asked correctly | Right options | Real names | Rows exact | Count exact | No invention | Format |
|---|---|---|---|---|---|---|---|---|
| US-1.2 | 3/3 (100%) | 3/3 (100%) | n/a | 3/3 (100%) | 3/3 (100%) | 3/3 (100%) | 3/3 (100%) | 3/3 (100%) |

Median response time 4.8 s, slowest 6.5 s.

## By case

| Case | Prompt | Expected rows | Passed | Notes |
|---|---|---|---|---|
| v07 | Show me the position history of VESSEL 0743. | 20 | 3/3 |  |

## Replies

### v07 run 1 — PASS

> Show me the position history of VESSEL 0743.

- Tool `search_orders_and_tonnage`: `{"cargoes": null, "vessels": {"include_history": true, "vessel_ids": ["VESSEL 0743"]}}`

20 position reports found for VESSEL 0743.

- 10 Jun 2025 – anchored at **Port Said** (East Mediterranean); open window 14‑17 Jul 2025.  
- 6 Jun 2025 – anchored at **Port Said** (East Mediterranean); open window 14‑17 Jun 2025.  
- 12 May 2025 – anchored at **Port Said** (East Mediterranean); open on 12 May 2025.  
- 12 May 2025 – anchored at **Port Said** (East Mediterranean); open on 12 May 2025 (duplicate entry).  
- 15 Apr 2025 – anchored at **Lanshan** (Far East); open on 25 Mar 2025.

*Assumed: as of Tuesday 17 June 2025; each vessel shown at its latest reported position; history included per the request.*

### v07 run 2 — PASS

> Show me the position history of VESSEL 0743.

- Tool `search_orders_and_tonnage`: `{"cargoes": null, "vessels": {"include_history": true, "vessel_ids": ["VESSEL 0743"]}}`

20 position reports found for VESSEL 0743.

- **Jul 14‑17 2025** – Open in **Port Said** (East Mediterranean); anchored, LADEN.  
- **Jun 14‑17 2025** – Open in **Port Said** (East Mediterranean); anchored, LADEN.  
- **Apr 25 2025** – Open in **Port Said** (East Mediterranean); anchored, LADEN.  
- **May 12 2025** – Open in **Port Said** (East Mediterranean); anchored, LADEN.  
- **Mar 25 2025** – Open in **Lanshan** (Far East); anchored, LADEN.  

*Assumed: each row reflects the vessel’s reported position at the update time; history included as requested.*

### v07 run 3 — PASS

> Show me the position history of VESSEL 0743.

- Tool `search_orders_and_tonnage`: `{"cargoes": null, "vessels": {"include_history": true, "vessel_ids": ["VESSEL 0743"]}}`

20 position reports found for VESSEL 0743.

- **July 14‑17 2025** – anchored at **Port Said** (East Mediterranean).  
- **June 14‑17 2025** – anchored at **Port Said** (East Mediterranean).  
- **May 12 2025** – anchored at **Port Said** (East Mediterranean).  
- **May 4‑8 2025** – anchored at **Tubarao** (East Coast South America).  
- **April 25 2025** – anchored at **Port Said** (East Mediterranean).  

*Assumed: as of Tuesday 17 June 2025; each vessel shown at its latest reported position; history included as requested.*
