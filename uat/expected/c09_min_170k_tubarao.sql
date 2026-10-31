-- c09  "Cargoes of at least 170,000 tonnes loading at Tubarao"
-- The whole stem must be at least 170,000 t: a 153,000-187,000 stem does not
-- qualify, so the minimum is what is compared.
SELECT o.order_id::text AS id
FROM order_test o
WHERE o.date_received < $1::date + 1
  AND o.load_port ILIKE '%Tubarao%'
  AND o.cargo_weight_min >= 170000;
