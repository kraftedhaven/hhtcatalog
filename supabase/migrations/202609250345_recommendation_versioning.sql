-- HHT Commerce Agent: preserve recommendation history and expose one current row per listing.
BEGIN;

ALTER TABLE public.recommendations
    ADD COLUMN IF NOT EXISTS version_number integer NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS is_current boolean NOT NULL DEFAULT true;

WITH ranked AS (
    SELECT id,
           row_number() OVER (
             PARTITION BY listing_row_id
             ORDER BY updated_at DESC, created_at DESC, id DESC
           ) AS current_rank,
           row_number() OVER (
             PARTITION BY listing_row_id
             ORDER BY updated_at ASC, created_at ASC, id ASC
           ) AS version_rank
    FROM public.recommendations
)
UPDATE public.recommendations r
SET is_current = (ranked.current_rank = 1),
    version_number = ranked.version_rank,
    status = CASE
      WHEN ranked.current_rank <> 1 AND r.status = 'Pending' THEN 'Superseded'
      ELSE r.status
    END
FROM ranked
WHERE r.id = ranked.id;

CREATE UNIQUE INDEX IF NOT EXISTS recommendations_one_current_per_listing
    ON public.recommendations(listing_row_id)
    WHERE is_current = true;

COMMIT;
