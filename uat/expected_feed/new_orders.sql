-- Change Feed "New orders".
-- Every order received in [since, until). Each order_seq is an order of its
-- own, so twins sharing an order_id are listed separately.
-- $1 since, $2 until ("now").
SELECT o.order_id::text AS order_id, o.order_seq
FROM order_test o
WHERE o.date_received >= $1
  AND o.date_received <  $2;
