-- control: closed-world check of run.py
SELECT o.customer_id,
       my_udf(o.credit_limit) AS v
FROM crm.customer AS o
WHERE o.status = 'A';
