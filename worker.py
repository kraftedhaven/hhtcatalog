"""Background worker for long-running catalog analysis jobs.

It performs no eBay mutation. Mutations remain behind recommendation approval and
an explicit apply request from the application.
"""
from __future__ import annotations
import os, time, logging
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from hht_app import commerce_agent

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))

def run() -> None:
    interval = max(2, int(os.environ.get("WORKER_POLL_SECONDS", "10")))
    concurrency = min(8, max(1, int(os.environ.get("WORKER_CONCURRENCY", "4"))))
    logging.info("HHT catalog worker started; approval-only mode enabled; concurrency=%s", concurrency)
    with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="catalog-job") as executor:
        running = {}
        while True:
            try:
                commerce_agent.init_db()
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
