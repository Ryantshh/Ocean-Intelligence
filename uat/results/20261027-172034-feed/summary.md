# Change Feed UAT results — 2026-10-27 17:21

Lists matching exactly: 16/20. Window bounds correct: 5/5.

| Scenario | Check | Expected | Dashboard | Result |
|---|---|---|---|---|
| 2025-06-17 dod | Window bounds | 2025-06-16 23:59:59.999999 to 2025-06-17 23:59:59.999999 | 2025-06-16T23:59:59.999999 to 2025-06-17T23:59:59.999999 | PASS |
| 2025-06-17 dod | Supply moves | 9 | 9 | PASS |
| 2025-06-17 dod | New vessels | 3 | 3 | PASS |
| 2025-06-17 dod | Field changes | 34 | 36 | FAIL |
| 2025-06-17 dod | New orders | 7 | 7 | PASS |
| 2025-06-17 wow | Window bounds | 2025-06-10 00:00:00 to 2025-06-17 00:00:00 | 2025-06-10T00:00:00 to 2025-06-17T00:00:00 | PASS |
| 2025-06-17 wow | Supply moves | 93 | 94 | FAIL |
| 2025-06-17 wow | New vessels | 55 | 55 | PASS |
| 2025-06-17 wow | Field changes | 212 | 212 | PASS |
| 2025-06-17 wow | New orders | 43 | 43 | PASS |
| 2025-06-17 24h | Window bounds | 2025-06-16 23:59:59.999999 to 2025-06-17 23:59:59.999999 | 2025-06-16T23:59:59.999999 to 2025-06-17T23:59:59.999999 | PASS |
| 2025-06-17 24h | Supply moves | 9 | 9 | PASS |
| 2025-06-17 24h | New vessels | 3 | 3 | PASS |
| 2025-06-17 24h | Field changes | 34 | 36 | FAIL |
| 2025-06-17 24h | New orders | 7 | 7 | PASS |
| 2025-06-16 dod | Window bounds | 2025-06-13 23:59:59.999999 to 2025-06-16 23:59:59.999999 | 2025-06-13T23:59:59.999999 to 2025-06-16T23:59:59.999999 | PASS |
| 2025-06-16 dod | Supply moves | 40 | 40 | PASS |
| 2025-06-16 dod | New vessels | 19 | 19 | PASS |
| 2025-06-16 dod | Field changes | 47 | 47 | PASS |
| 2025-06-16 dod | New orders | 8 | 8 | PASS |
| 2025-06-16 wow | Window bounds | 2025-06-09 00:00:00 to 2025-06-16 00:00:00 | 2025-06-09T00:00:00 to 2025-06-16T00:00:00 | PASS |
| 2025-06-16 wow | Supply moves | 87 | 93 | FAIL |
| 2025-06-16 wow | New vessels | 56 | 56 | PASS |
| 2025-06-16 wow | Field changes | 201 | 201 | PASS |
| 2025-06-16 wow | New orders | 43 | 43 | PASS |

## Differences

### 2025-06-17 dod — Field changes

- Shown but not expected (2): VESSEL 0301 | 2025-06-17 05:49:08; VESSEL 0780 | 2025-06-17 05:49:08

### 2025-06-17 wow — Supply moves

- Shown but not expected (1): VESSEL 0813 | 2025-06-14 | up

### 2025-06-17 24h — Field changes

- Shown but not expected (2): VESSEL 0301 | 2025-06-17 05:49:08; VESSEL 0780 | 2025-06-17 05:49:08

### 2025-06-16 wow — Supply moves

- Shown but not expected (6): VESSEL 0015 | 2025-06-10 | down; VESSEL 0416 | 2025-06-15 | down; VESSEL 0573 | 2025-06-14 | down; VESSEL 0597 | 2025-06-15 | up; VESSEL 0847 | 2025-06-14 | down; VESSEL 1008 | 2025-06-12 | down
