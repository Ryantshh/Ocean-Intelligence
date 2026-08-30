import { useState } from "react";

const LABELS = {
  load_port: "Load port",
  load_zone: "Load zone",
  discharge_port: "Discharge port",
  discharge_parent_zone: "Discharge zone",
  cargo_type: "Cargo type",
  cargo_description: "Cargo description",
  open_area: "Open area",
  parent_zone: "Open zone",
  vessel_status: "Vessel status",
  laycan_start_from: "Laycan from",
  laycan_start_to: "Laycan to",
  laycan_end_from: "Cancelling from",
  laycan_end_to: "Cancelling by",
  open_start_from: "Open from",
  open_start_to: "Open to",
  open_end_from: "Closes from",
  open_end_to: "Closes by",
  weight_min: "Cargo tonnes, min",
  weight_max: "Cargo tonnes, max",
  dwt_min: "Deadweight, min",
  dwt_max: "Deadweight, max",
  updated_from: "Updated since",
};

const ZONES = [
  "Arabian Gulf", "Asia", "Atlantic", "Australia", "Baltic", "Black Sea",
  "Caribs", "East Africa", "East Australia", "East Coast Canada",
  "East Coast India", "East Coast South America", "East Coast United States",
  "East Mediterranean", "Europe", "Europe Atlantic Coast", "Far East",
  "Great Lakes", "India", "Indian Ocean", "Mediterranean",
  "Micronesia & Melanesia", "North America - Arctic",
  "North Coast South America", "North Russia", "North West Africa", "Pacific",
  "Red Sea", "Scandinavia", "Singapore-Japan", "Skaw/Cape Passero",
  "South Africa", "South East Asia", "UK-IRE-CONT", "United States Gulf",
  "West Africa", "West Australia", "West Coast Canada",
  "West Coast Central America", "West Coast India", "West Coast South America",
  "West Coast United States", "West Mediterranean", "Worldwide",
];

const ZONE_FIELDS = new Set([
  "load_zone",
  "discharge_parent_zone",
  "parent_zone",
]);

export default function RefineSearch() {
  const initial = props.fields ?? {};
  const [values, setValues] = useState(initial);
  const [sent, setSent] = useState(false);

  const names = Object.keys(initial);
  const set = (field, value) => setValues({ ...values, [field]: value });

  const submit = () => {
    if (sent) return;
    setSent(true);
    submitElement(values);
  };

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-border bg-card p-4">
      {names.map((field) => (
        <label key={field} className="flex flex-col gap-1 text-sm">
          <span className="text-muted-foreground">{LABELS[field] ?? field}</span>
          <input
            className="rounded-md border border-input bg-background px-2 py-1
                       text-foreground outline-none focus:ring-1 focus:ring-ring"
            value={values[field] ?? ""}
            list={ZONE_FIELDS.has(field) ? "ocean-zones" : undefined}
            placeholder={ZONE_FIELDS.has(field) ? "pick a zone" : "type a value"}
            disabled={sent}
            onChange={(event) => set(field, event.target.value)}
            onKeyDown={(event) => event.key === "Enter" && submit()}
          />
        </label>
      ))}

      <datalist id="ocean-zones">
        {ZONES.map((zone) => (
          <option key={zone} value={zone} />
        ))}
      </datalist>

      <button
        className="mt-1 self-end rounded-md bg-primary px-3 py-1.5 text-sm
                   font-medium text-primary-foreground disabled:opacity-50"
        disabled={sent}
        onClick={submit}
      >
        {sent ? "Searching…" : "Search"}
      </button>
    </div>
  );
}
