"""Persistent read-only worker for HHT Commerce Agent jobs.

The worker never publishes or mutates eBay listings. It claims seller-scoped
jobs from Supabase/PostgreSQL, processes independent jobs concurrently, and
requeues jobs interrupted by a process or VM restart.
"""
from __future__ import annotations

import logging
import os
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from hht_app import commerce_agent

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))


def run() -> None:
    interval = max(2, int(os.environ.get("WORKER_POLL_SECONDS", "5")))
    # Hard ceiling protects provider quotas and Supabase from accidental fan-out.
    concurrency = min(16, max(1, int(os.environ.get("WORKER_CONCURRENCY", "8"))))
    stale_after = max(300, int(os.environ.get("WORKER_STALE_AFTER_SECONDS", "900")))
    logging.info(
        "HHT catalog worker started; approval-only mode enabled; concurrency=%s stale_after=%ss",
        concurrency,
        stale_after,
    )
    with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="catalog-job") as executor:
        running = {}
        while True:
            try:
                commerce_agent.init_db()
                recovered = commerce_agent.requeue_stale_jobs(stale_after)
                if recovered:
                    logging.warning("Requeued %s stale Commerce Agent job(s)", recovered)

                completed = [job_id for job_id, future in running.items() if future.done()]
                for job_id in completed:
                    try:
                        job = running.pop(job_id).result()
                        logging.info("Completed Commerce Agent job id=%s status=%s", job_id, (job or {}).get("status"))
                    except Exception:
                        logging.exception("Commerce Agent job failed id=%s", job_id)

                slots = concurrency - len(running)
                if slots > 0:
                    for job_id in commerce_agent.queued_job_ids(slots, list(running)):
                        running[job_id] = executor.submit(commerce_agent.run_job, job_id)
                if running:
                    wait(list(running.values()), timeout=interval, return_when=FIRST_COMPLETED)
                else:
                    time.sleep(interval)
            except KeyboardInterrupt:
                return
            except Exception:
                logging.exception("Worker loop failed; retrying")
                time.sleep(interval)


if __name__ == "__main__":
    run()
