SELECT id, email, first_name, last_name
FROM users
WHERE LOWER(first_name) IN ('sandeep','yashveer','bhavya','dyuthith','annie');
