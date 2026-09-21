"""Background worker for long-running catalog analysis jobs.

It performs no eBay mutation. Mutations remain behind recommendation approval and
an explicit apply request from the application.
"""
from __future__ import annotations
import os, time, logging
from hht_app import commerce_agent

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))

def run() -> None:
    interval = max(2, int(os.environ.get("WORKER_POLL_SECONDS", "10")))
    logging.info("HHT catalog worker started; approval-only mode enabled")
    while True:
        try:
            commerce_agent.init_db()
            job = commerce_agent.run_next_queued_job()
            if job:
                logging.info("Completed Commerce Agent job id=%s status=%s", job.get("id"), job.get("status"))
                continue
            time.sleep(interval)
        except KeyboardInterrupt:
            return
        except Exception:
            logging.exception("Worker loop failed; retrying")
            time.sleep(interval)

if __name__ == "__main__":
    run()
