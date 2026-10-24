# Voyage Calculator Field Sources

| Group | Field | Prefilled from available data | Prefilled with computed value | Prefilled with calculator default (trader can edit) | Calculator default value |
|---|---|---|---|---|---|
| Vessel | Deadweight | ✅ | N/A | N/A | 62,000 |
| | Grain capacity | ✅ | N/A | N/A | 70,000 |
| | Speed when sailing empty | ❌ | ❌ | ✅ | 14 |
| | Speed when sailing loaded | ❌ | ❌ | ✅ | 12 |
| | Intermediate fuel oil burned per day at sea, empty | ❌ | ❌ | ✅ | 23 |
| | Intermediate fuel oil burned per day at sea, loaded | ❌ | ❌ | ✅ | 18.5 |
| | Marine diesel oil burned per day at sea, empty | ❌ | ❌ | ✅ | 0.1 |
| | Marine diesel oil burned per day at sea, loaded | ❌ | ❌ | ✅ | 0.1 |
| | Intermediate fuel oil burned per day in port, working | ❌ | ❌ | ✅ | 5.5 |
| | Intermediate fuel oil burned per day in port, idle | ❌ | ❌ | ✅ | 5.5 |
| | Marine diesel oil burned per day in port, working | ❌ | ❌ | ✅ | 0.1 |
| | Marine diesel oil burned per day in port, idle | ❌ | ❌ | ✅ | 0.1 |
| Voyage | Distance sailing empty to the load port | ❌ | ✅ Sea distance from open area to load port, using the World Port Index + searoute (distance algorithm) | ❌ Ask the trader for port name if the open area or load port data field is not a port and then compute | 3,000 |
| | Distance sailing loaded to the discharge port | ❌ | ✅ Sea distance from load port to discharge port, using the World Port Index + searoute (distance algorithm) | ❌ Ask the trader for port name if the load port or discharge port data field is not a port and then compute | 3,000 |
| | Days spent taking on fuel | ❌ | ❌ | ✅ | 1 |
| Cargo | Cargo quantity | ✅ | N/A | N/A | 60,500 |
| | Stowage factor | ❌ | ❌ | ✅ | 1.33 |
| | Freight rate | ❌ | ❌ | ✅ | 22 |
| | Address commission on freight | ❌ | ❌ | ✅ | 3.75% |
| | Broker commission | ❌ | ❌ | ✅ | 1.25% |
| Port | Loading rate | ❌ | ❌ | ✅ | 8,000 |
| | Discharging rate | ❌ | ❌ | ✅ | 11,000 |
| | Turn time at the load port | ❌ | ❌ | ✅ | 0.5 days |
| | Turn time at the discharge port | ❌ | ❌ | ✅ | 0.5 days |
| | Idle days in port | ❌ | ❌ | ✅ | 0.5 days |
| Prices and costs | Daily hire rate | ❌ | ❌ | ✅ | 12,000 |
| | Address commission on hire | ❌ | ❌ | ✅ | 3.75% |
| | Intermediate fuel oil price | ❌ | ❌ | ✅ | 440 |
| | Marine diesel oil price | ❌ | ❌ | ✅ | 850 |
| | Load port charges | ❌ | ❌ | ✅ | 20,000 |
| | Discharge port charges | ❌ | ❌ | ✅ | 20,000 |
| | Cables, entertainment and victualling | ❌ | ❌ | ✅ | 1,500 |
| | Payment in lieu of hold cleaning | ❌ | ❌ | ✅ | 5,000 |
| | Agency fee for taking on fuel | ❌ | ❌ | ✅ | 1,500 |

## Calculation steps

Worked example using the calculator default values.

### Step 1: Quantity loaded

The vessel carries the smallest of the cargo offered, what fits in its holds, and what its deadweight allows.

```
Space limit     = grain capacity ÷ stowage factor
                = 70,000 ÷ 1.33                          = 52,631.58 t
Quantity loaded = smallest of (cargo quantity, space limit, deadweight)
                = smallest of (60,500, 52,631.58, 62,000) = 52,631.58 t
```

### Step 2: Days at sea

```
Empty leg days  = empty leg distance ÷ (empty speed × 24)
                = 3,000 ÷ (14 × 24)                       = 8.9286 days
Loaded leg days = loaded leg distance ÷ (loaded speed × 24)
                = 3,000 ÷ (12 × 24)                       = 10.4167 days
Days at sea     = 8.9286 + 10.4167                        = 19.3452 days
```

### Step 3: Days in port

```
Load port working days   = quantity loaded ÷ loading rate + load port turn time
                         = 52,631.58 ÷ 8,000 + 0.5        = 7.0789 days
Load port days           = working days + idle days
                         = 7.0789 + 0.5                   = 7.5789 days
Discharge port days      = quantity loaded ÷ discharging rate + discharge port turn time
                         = 52,631.58 ÷ 11,000 + 0.5       = 5.2847 days
```

### Step 4: Total voyage days

```
Total days = days at sea + days taking on fuel + load port days + discharge port days
           = 19.3452 + 1 + 7.5789 + 5.2847                = 33.2089 days
```

### Step 5: Hire cost

```
Gross hire = daily hire × total days
           = 12,000 × 33.2089                             = 398,506.49
Net hire   = gross hire × (1 − address commission on hire)
           = 398,506.49 × (1 − 0.0375)                    = 383,562.50
```

### Step 6: Fuel burned

Intermediate fuel oil:

```
At sea              = empty leg days × empty burn + loaded leg days × loaded burn
                    = 8.9286 × 23 + 10.4167 × 18.5         = 398.0655 t
In port             = (load port working days + discharge port days) × working burn
                      + idle days × idle burn
                    = (7.0789 + 5.2847) × 5.5 + 0.5 × 5.5  = 70.7500 t
Taking on fuel      = days taking on fuel × idle burn
                    = 1 × 5.5                              = 5.5000 t
Total               = 398.0655 + 70.7500 + 5.5000          = 474.3155 t
```

Marine diesel oil:

```
At sea              = 8.9286 × 0.1 + 10.4167 × 0.1         = 1.9345 t
In port             = (7.0789 + 5.2847) × 0.1 + 0.5 × 0.1  = 1.2864 t
Taking on fuel      = 1 × 0.1                              = 0.1000 t
Total               = 1.9345 + 1.2864 + 0.1000             = 3.3209 t
```

### Step 7: Fuel cost

```
Intermediate fuel oil cost = 474.3155 × 440               = 208,698.81
Marine diesel oil cost     = 3.3209 × 850                 = 2,822.75
Fuel cost                  = 208,698.81 + 2,822.75        = 211,521.56
```

### Step 8: Other costs

```
Other costs = cables, entertainment and victualling
              + payment in lieu of hold cleaning + agency fee for taking on fuel
              + load port charges + discharge port charges
            = 1,500 + 5,000 + 1,500 + 20,000 + 20,000      = 48,000
```

### Step 9: Revenue

```
Gross freight = quantity loaded × freight rate
              = 52,631.58 × 22                             = 1,157,894.74
Commissions   = gross freight × (address commission on freight + broker commission)
              = 1,157,894.74 × (0.0375 + 0.0125)           = 57,894.74
Revenue       = 1,157,894.74 − 57,894.74                   = 1,100,000.00
```

### Step 10: Profit

```
Profit = revenue − net hire − fuel cost − other costs
       = 1,100,000.00 − 383,562.50 − 211,521.56 − 48,000.00 = 456,915.94
```

