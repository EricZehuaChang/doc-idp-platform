"""Celery application — resource-pooled queues (design v0.2 §9: Celery 5 +
Redis, queues split by resource so heavy parsing never starves extraction).

Queues:
  orchestrate  cheap coordination (plan/finalize)
  parse_cpu    pdfplumber/markitdown/cloud-API parsing
  parse_gpu    parsers backed by a local GPU model (monkeyocr, glm-ocr-local)
  extract      LLM extraction (IO-bound; scale its concurrency independently)

Workers:
  Windows dev :  celery -A app.tasks.celery_app worker -P threads -c 8 \
                   -Q orchestrate,parse_cpu,parse_gpu,extract
  production  :  one prefork worker per queue (container images pin -Q), e.g.
                 celery -A app.tasks.celery_app worker -Q parse_gpu -c 1

DLQ stance (HA v2.0 §2.3): acks_late redelivers on worker loss; tasks catch
their own business failures into the DB error state (the DB is the DLQ of
record — rows in status=error carry the message and are re-submittable).
"""
from celery import Celery

from app.config import get_settings

QUEUES = ("orchestrate", "parse_cpu", "parse_gpu", "extract")

# parsers that occupy a local GPU; everything else parses on the CPU pool
GPU_PARSERS = {"monkeyocr", "glm-ocr-local"}


def parse_queue_for(parser_pin: str | None) -> str:
    return "parse_gpu" if parser_pin in GPU_PARSERS else "parse_cpu"


settings = get_settings()
celery_app = Celery("idp", broker=settings.redis_url, backend=settings.redis_url,
                    include=["app.tasks.celery_tasks"])
celery_app.conf.update(
    task_acks_late=True,                    # worker crash -> broker redelivers
    worker_prefetch_multiplier=1,           # long tasks: no hoarding
    task_default_queue="parse_cpu",
    result_expires=3600,
    broker_connection_retry_on_startup=True,
    task_routes={
        "idp.run_transaction": {"queue": "orchestrate"},
        "idp.finalize_transaction": {"queue": "orchestrate"},
        "idp.parse_file": {"queue": "parse_cpu"},   # per-dispatch .set() moves GPU jobs
        "idp.extract_file": {"queue": "extract"},
        "idp.ping": {"queue": "orchestrate"},
    },
)
