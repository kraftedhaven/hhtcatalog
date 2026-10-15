import pathlib
import psycopg

url = pathlib.Path(
    r"C:\Users\korin\hhtcatalog-clean\.neon-url.tmp"
).read_text().strip()

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
    err = r[5] if r[5] else "None"
    print(
        f"  Scope: {r[0]} | Status: {r[1]} | "
        f"HTTP: {r[3]} | Checked: {r[4]} | Error: {err}"
    )

if not rows:
    print("  (No scope proof rows found yet)")
