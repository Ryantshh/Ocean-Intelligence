# SMU 2025 Data Glossary

_Field definitions for SMU_Tonnage_data_2025.xlsx ("Tonnage Results") and SMU_Order_data_2025.xlsx ("Order Results") — dry-bulk vessel position and cargo order market data._

## Tonnage Results — Vessel Position Data

_One row = a reported vessel position/availability snapshot circulated by a broker._

| Field | Definition |
|---|---|
| Report Month | Calendar month (e.g. "2025-01 Jan") that this vessel position report belongs to. |
| Date Received | Timestamp when this specific version of the position report was received into the system. |
| First Date Received | Timestamp when this vessel position was first reported, before any later updates/revisions. |
| Vessel Name | Name/Identifier of the ship (anonymized in this dataset, e.g. "VESSEL 0663"). |
| DWT Summer | Deadweight tonnage at the summer load line — the vessel's maximum cargo + fuel + stores carrying capacity, in metric tons. |
| Draft Summer | Summer draft — the depth of the vessel's hull below the waterline when loaded to the summer DWT, in meters. |
| Build Year | Year the vessel was built/delivered from the shipyard. |
| Open Areas | Geographic area/port where the vessel becomes available ("open") for its next fixture. |
| Open Dates Start | Earliest date the vessel is expected to be open (available) for a new charter. |
| Open Dates End | Latest date in the vessel's open window. |
| Offer | Freight rate, hire rate, or terms being offered on this vessel by its owner/broker. |
| Bid | Freight rate, hire rate, or terms being bid by a prospective charterer for this vessel. |
| Update Date | Date/time this record was last refreshed or amended in the source system. |
| ETA Dates Start | Earliest estimated time of arrival at the vessel's next reported destination. |
| ETA Dates End | Latest estimated time of arrival in the reported ETA range. |
| ETA On Time | Flag indicating whether the vessel is tracking to arrive within its stated ETA window. |
| Ballast Duration (Min) | Minimum estimated sailing time (e.g. days) for the vessel's ballast (empty) leg to its next load port. |
| Ballast Duration (Max) | Maximum estimated sailing time for the ballast leg. |
| Ballast Distance (Min) | Minimum estimated distance (nautical miles) of the ballast leg. |
| Ballast Distance (Max) | Maximum estimated distance of the ballast leg. |
| Sender | Brokerage or firm that sent/circulated this position report. |
| Parent Zone | Broader geographic trading region containing the vessel's open area (e.g. "South East Asia"). |
| Pref Direction | Owner's/charterer's preferred trading direction for the vessel's next voyage. |
| Business Pref | Preferred business or cargo type the vessel is being marketed for. |
| Source Type | Channel through which the report was received (e.g. Email). |
| Commercial Status | Current fixture status of the vessel (e.g. FIXED — already chartered). |
| Off Market | Flag indicating the vessel has been withdrawn from active market circulation. |
| Last Known Cargoes | Most recent cargo(es) the vessel is known to have carried. |
| Edited | Flag indicating a person manually edited this record after it was received. |
| Edited By | Name/ID of the person who last edited the record. |
| Ship Comments | Free-text notes about the vessel itself (condition, restrictions, etc.). |
| Comments | Free-text notes about this specific position/fixture report. |
| Broker Name | Name of the broker(s) associated with this position. |
| Fresh | Flag indicating this is a newly circulated (not stale/repeated) position. |
| Ship Types | Vessel type classification (e.g. Bulk Carrier). |
| Call Sign | Vessel's international radio call sign. |
| MMSI Number | Maritime Mobile Service Identity — unique 9-digit number used for AIS/radio identification. |
| IMO Number | International Maritime Organization number — permanent unique vessel identifier. |
| Inmarsat ID | Vessel's Inmarsat satellite communications terminal identifier. |
| Hull Number | Shipyard's internal build/hull number for the vessel. |
| Official Number | Official registration number issued by the vessel's flag state. |
| Flag | Flag state (country of registration) of the vessel. |
| Port of Registry | Home port under which the vessel is registered. |
| Shipyard | Shipyard where the vessel was built. |
| Shipyard Country | Country in which the building shipyard is located. |
| Ship Sizes | Size/segment classification of the vessel (e.g. Capesize, Panamax). |
| Deletion Date | Date the vessel was removed from the fleet/register (e.g. scrapped or reflagged out of scope), if applicable. |
| Disponent Owner | Party currently controlling/operating the vessel commercially (may differ from the registered owner), typically the current charterer-operator. |
| Last Inspection Date | Date of the vessel's most recent vetting/condition inspection. |
| International Gross Tonnage | Gross tonnage (GT) — a dimensionless measure of the vessel's total enclosed internal volume, per international tonnage convention. |
| International Net Tonnage | Net tonnage (NT) — a dimensionless measure of the vessel's usable cargo-carrying volume, per international convention. |
| TPC Summer | Tonnes Per Centimetre immersion at the summer draft — tons needed to change draft by 1 cm at that loading condition. |
| Depth Moulded | Vertical distance from the keel to the main deck (moulded depth), in meters. |
| LOA | Length Overall — the vessel's total length end to end, in meters. |
| LPP | Length between Perpendiculars — length measured between the forward and aft perpendiculars, in meters. |
| Beam | Width of the vessel at its widest point, in meters. |
| Displacement | Total weight of water displaced by the loaded vessel (ship's light weight plus everything on board). |
| Classification Society | Society that certifies the vessel's structural and mechanical fitness (e.g. Lloyd's Register, DNV). |
| Ice Strengthened | Flag indicating the hull is reinforced for navigation in ice. |
| Strength Upper Deck | Rated structural strength of the vessel's upper (weather) deck, typically in tonnes/m². |
| Strength Hatchcover | Rated structural strength (load capacity) of the cargo hatch covers. |
| No. of Holds | Number of cargo holds on the vessel. |
| No. of Hatches | Number of cargo hatch openings on the vessel. |
| Open Hatch | Flag indicating the vessel has open-hatch (box-shaped, gantry-crane-friendly) hold design. |
| Box-Shaped | Flag indicating the cargo holds have a box-shaped (rather than tapered) profile, maximizing stowage of unitized/bulk cargo. |
| Largest Hatch Length | Length of the vessel's largest cargo hatch opening, in meters. |
| Grain Capacity | Maximum cargo volume for free-flowing (grain-type) bulk cargo, in cubic meters/feet. |
| Bale | Bale capacity — maximum cargo volume for packaged/bagged cargo, in cubic meters/feet (always ≤ grain capacity). |
| Australian Hold Ladders | Flag indicating the holds are fitted with permanent ladders meeting Australian stevedoring safety requirements. |
| Gearless | Flag indicating the vessel has no onboard cargo cranes/gear and relies on shore equipment. |
| Gear Type | Type of cargo-handling gear fitted (e.g. crane, derrick). |
| No. of Cranes | Number of onboard cargo cranes. |
| Crane Capacity | Maximum safe working load of the vessel's cranes, typically in tonnes. |
| Crane Outreach | Maximum horizontal reach of the cranes from the ship's side, in meters. |
| Crane Combi | Flag indicating cranes can be combined/coupled to lift heavier single loads together. |
| Grabber | Flag indicating the vessel is fitted with cargo grabs (clamshell buckets) for self-discharge. |
| No. of Grabs | Number of cargo grabs carried. |
| Grab Capacity | Cargo volume/weight capacity of a single grab, typically in cubic meters. |
| Grab Discharge Suitable | Flag indicating the vessel/holds are suitable for grab (mechanical) discharge without damage. |
| Propeller | Type of propeller fitted (e.g. Fixed Pitch, Controllable Pitch). |
| Max Speed | Vessel's maximum service speed, typically in knots. |
| AFF | Vessel equipment/fitting flag reported in the source feed (specific definition not documented by the data provider). |
| CO2 Fitted | Flag indicating the vessel has a CO2 fixed fire-extinguishing system. |
| A60 Steel Fitted Bulkhead | Flag indicating an A60 fire-rated steel bulkhead (60-minute fire/insulation rating) is fitted. |
| Logs Fitted | Flag indicating a data/voyage logging device is fitted onboard. |
| Scrubber Fitted | Flag indicating an exhaust gas scrubber is fitted, allowing use of higher-sulphur fuel under emissions regulations. |
| ITF Compliant | Flag indicating the vessel operates under an International Transport Workers' Federation-approved crew agreement. |
| Status | Current AIS navigational status of the vessel (e.g. Under way using engine, Anchored). |
| Ballast/Laden | Current voyage condition: Ballast (sailing empty) or Laden (sailing with cargo). |
| Destination | Port/place currently reported by the vessel (via AIS) as its destination. |
| Last Destination | Most recently reported prior destination. |
| ETA | Current estimated time of arrival at the reported Destination. |
| Live Bearing | Real-time compass heading/bearing of the vessel, from AIS tracking. |
| Live Speed | Real-time speed of the vessel over ground, in knots, from AIS tracking. |
| Draft (% Max) | Vessel's current draft expressed as a percentage of its maximum (summer) draft — an indicator of how loaded it is. |
| Draft (Meters) | Vessel's current draft, in meters, from AIS/reported data. |
| Assignment (T/F)| Flag indicating whether the vessel has been assigned to an order. |
| Order ID | Identifier of the order row this vessel has been assigned to, where applicable (matches Order ID in Order Results). |

## Order Results — Cargo / Fixture Order Data

_One row = a reported cargo enquiry or fixture order circulated by a broker._

| Field | Definition |
|---|---|
| Report Quarter | Calendar quarter (e.g. "2025-Q1") that this cargo/order report belongs to. |
| Date Received | Timestamp when this version of the order report was received into the system. |
| First Date Received | Timestamp when this order was first reported, before any later updates/revisions. |
| Account | Charterer or customer account associated with the order (anonymized, e.g. "ACCOUNT 142"). |
| DWT Min | Minimum vessel deadweight tonnage acceptable for this cargo. |
| DWT Max | Maximum vessel deadweight tonnage acceptable for this cargo. |
| Cargo Weight Min | Minimum cargo quantity offered, in metric tons. |
| Cargo Weight Max | Maximum cargo quantity offered, in metric tons. |
| Business Type | Type of charter/contract being sought (e.g. VOY = voyage charter, TCT = time charter trip). |
| Lay-Can Start | Laycan (laydays/cancelling) start — earliest date the vessel must be ready to load. |
| Lay-Can End | Laycan end — latest date by which loading must commence, after which the charterer may cancel. |
| Load / Deli | Load port (voyage charter) or delivery point (time charter) for the fixture. |
| Disc / Redel | Discharge port (voyage charter) or redelivery range (time charter) for the fixture. |
| Cargo Types | Standardized commodity classification of the cargo (e.g. IRON ORE, COAL). |
| Cargo Desc. | Free-text description of the cargo. |
| Commercial Status | Current status of the order/negotiation (e.g. open, fixed). |
| Sender | Party/broker who sent this order/cargo report. |
| Comments | Free-text notes about the order. |
| Load Parent Zone | Broader geographic trading region containing the load port. |
| Disc Parent Zone | Broader geographic trading region containing the discharge port. |
| Source Type | Channel through which the order was received (e.g. Email). |
| Off Market | Flag indicating the order has been withdrawn from active circulation. |
| Combi Cargo | Flag indicating this is a combination cargo (multiple commodity types in one shipment). |
| Charter Duration | Length of the time charter period, where applicable. |
| TCE | Time Charter Equivalent — the voyage's implied daily hire rate (revenue less voyage costs, divided by voyage days), used to compare voyage and time-charter economics. |
| Net Income | Estimated net earnings/profit associated with the fixture. |
| Edited | Flag indicating a person manually edited this record after it was received. |
| Edited By | Name/ID of the person who last edited the record. |
| Offer | Freight/hire rate or terms being offered by the charterer for this cargo. |
| Bid | Freight/hire rate or terms being bid by an owner/broker for this cargo. |
| Broker Name | Name of the broker(s) associated with this order. |
| Fresh | Flag indicating this is a newly circulated (not stale/repeated) order. |
| Max Ship Age | Maximum vessel age (years since build) the charterer will accept. |
| Ship Type | Vessel type required for the cargo (e.g. Bulk Carrier). |
| DWT Min.1 | Duplicate/secondary field for minimum acceptable vessel deadweight (later revision or alternate source of the same requirement). |
| DWT Max.1 | Duplicate/secondary field for maximum acceptable vessel deadweight. |
| Cargo Weight Min.1 | Duplicate/secondary field for minimum cargo quantity. |
| Cargo Weight Max.1 | Duplicate/secondary field for maximum cargo quantity. |
| Cargo Volume Min | Minimum cargo volume, for volume-rated (rather than weight-rated) cargoes, typically cubic meters. |
| Cargo Volume Max | Maximum cargo volume. |
| Cargo Unit | Unit of measure used for the cargo quantity (e.g. metric tons, cubic meters). |
| Cargo RT | Cargo Revenue Tons — the greater of weight or volume (in revenue ton terms) used to calculate freight for cargoes charged on a weight-or-measurement basis. |
| No. of Decks | Required/specified number of cargo decks on the vessel. |
| Max Ship Draft | Maximum vessel draft acceptable, constrained by port/channel depth. |
| Max Ship LOA | Maximum vessel Length Overall acceptable, constrained by port/berth length. |
| Max Ship Beam | Maximum vessel beam (width) acceptable, constrained by port/lock/canal width. |
| No. of Holds | Required/specified number of cargo holds. |
| Geared | Flag indicating a vessel with onboard cranes/gear is required (as opposed to gearless). |
| No. of Cranes | Required/specified number of onboard cranes. |
| Crane Capacity | Required/specified minimum crane lifting capacity. |
| Crane Outreach | Required/specified minimum crane outreach. |
| Grab | Flag/spec indicating the vessel must be fitted with or suitable for cargo grabs. |
| First Date Received.1 | Duplicate/secondary capture of the order's first-received timestamp. |
| Lead Time (Hours) | Hours elapsed between the order first being received and the point measured (e.g. fixture/update) — a measure of how quickly the order moved. |
| Update Date | Date/time this record was last refreshed or amended in the source system. |
| Order ID | Unique identifier for this order row. |
| Assigned (T/F) | Flag indicating whether this order row has been assigned to a vessel. |
| Assigned Vessel Name | Identifier of the vessel row assigned to this order, where applicable (matches Vessel Name in Tonnage Results). |

---

_Notes: Definitions reflect standard dry-bulk shipping/chartering terminology and the way each field is used in the source data. "Flag" fields are True/False indicators. A few fields (e.g. AFF) use an abbreviation not otherwise documented by the data provider; verify against the source system if precision matters for a specific use case._
