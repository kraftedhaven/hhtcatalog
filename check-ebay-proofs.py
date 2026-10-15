import os
import psycopg

url = os.environ.get("NEON_DATABASE_URL") or os.environ.get("DATABASE_URL")
if not url:
    raise SystemExit("No Neon database environment variable is available.")

with psycopg.connect(url, connect_timeout=15) as conn:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT scope, status, probe, http_status, checked_at, error_category
            FROM public.ebay_scope_proofs
            ORDER BY scope;
        """ )
        rows = cur.fetchall()

print("eBay Scope Proofs in Neon PostgreSQL:")
for r in rows:
    print(
        f"  Scope: {r[0]} | Status: {r[1]} | "
        f"HTTP: {r[3]} | Checked: {r[4]} | "
        f"Error: {r[5] or 'None'}"
    )

if not rows:
    print("  (No scope proof rows found yet)")
