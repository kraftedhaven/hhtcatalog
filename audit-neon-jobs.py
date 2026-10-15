import os
import psycopg

url = os.environ.get("NEON_DATABASE_URL") or os.environ.get("DATABASE_URL")
if not url:
    raise SystemExit(
        "No database URL is loaded. Load the rotated Heroku DATABASE_URL first."
    )

with psycopg.connect(url, connect_timeout=15) as conn:
    with conn.cursor() as cur:
        print("-- job counts by kind/status --")
        cur.execute("""
            SELECT kind, status, COUNT(*)
            FROM public.commerce_jobs
            GROUP BY kind, status
            ORDER BY kind, status;
        """)
        for row in cur.fetchall():
            print(row)

        print("\n-- queued/running jobs --")
        cur.execute("""
            SELECT id, kind, status, progress, created_at, updated_at,
                   LEFT(COALESCE(error, ''), 240)
            FROM public.commerce_jobs
            WHERE status IN ('queued', 'running')
            ORDER BY created_at ASC;
        """)
        rows = cur.fetchall()
        for row in rows:
            print(row)

        if not rows:
            print("(none)")

        print("\n-- analysis job totals --")
        cur.execute("""
            SELECT status, COUNT(*)
            FROM public.commerce_jobs
            WHERE kind = 'listing_analysis'
            GROUP BY status
            ORDER BY status;
        """)
        for row in cur.fetchall():
            print(row)
