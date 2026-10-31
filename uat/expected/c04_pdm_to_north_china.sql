-- c04  "Show me orders from PDM to North China."  (answered: Madeira)
-- PDM is Ponta da Madeira, stored as Madeira. North China is a stored
-- discharge label. No dates asked for.
SELECT o.order_id::text AS id
FROM order_test o
WHERE o.date_received < $1::date + 1
  AND o.load_port      ILIKE '%Madeira%'
  AND o.discharge_port ILIKE '%North China%';
