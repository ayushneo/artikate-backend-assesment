# Artikate Studio: Backend Technical Assessment

Django backend covering the four required sections: diagnosing/fixing an
N+1 regression, a rate-limited Celery+Redis job queue, automatic
multi-tenant ORM scoping, and written architecture answers.

- **[ANSWERS.md](ANSWERS.md)**: written answers, per section.
- **[DESIGN.md](DESIGN.md)**: Section 2 architecture decisions.
- **[INCIDENT_LOG.md](INCIDENT_LOG.md)**: Section 1 investigation.
- **[docs/section1_query_evidence.txt](docs/section1_query_evidence.txt)**: before/after query counts.

## Setup and run (under 5 minutes)

Requires Docker Desktop, nothing else, no local Python/Postgres/Redis.

```bash
docker compose up --build
```

Builds the image, starts Redis, runs migrations, and starts the Django
dev server on **http://localhost:8080** plus a Celery worker, both in the
foreground so you can watch both logs. First run pulls/builds images
(~1 minute); later runs are seconds.

In a second terminal, run the test suite (26 tests, real Redis, no mocks,
~2 seconds):

```bash
docker compose run --rm web pytest -v
```

Regenerate the Section 1 query-count evidence yourself:

```bash
docker compose run --rm web python manage.py query_count_demo
```

Live profiler view for Section 1: open **http://localhost:8080/silk/**
while the stack runs, then hit the endpoint below and check the
request's query count in Silk's UI.

Try the fixed endpoint directly:

```bash
curl -H "X-Tenant-ID: 1" http://localhost:8080/api/orders/summary/
```

(`X-Tenant-ID` stands in for a tenant claim already verified by upstream
JWT auth, see "Interpretations" below. Without a valid header the
endpoint returns `400`, not data, that's Section 3's fail-closed tenant
scoping, exercised through the real endpoint, not just at the ORM layer.)

Stop everything with `Ctrl+C`, then `docker compose down`.

### Running without Docker

Works too (targets Django 4.2 LTS, Python 3.8–3.12 all fine), but you
need Redis running locally yourself:

```bash
python -m venv .venv && source .venv/bin/activate  # .venv\Scripts\activate on Windows
pip install -r requirements.txt
redis-server &                      # however you normally run Redis locally
python manage.py migrate
python manage.py runserver           # terminal 1
celery -A config worker -l info      # terminal 2
pytest                               # terminal 3
```

`REDIS_URL` / `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` default to
`localhost:6379` (db 0/0/1) when unset, see `.env.example`.

## Project layout

```
config/       Django project: settings, urls, celery app, DRF exception handler
orders/       Section 1, Order/Customer models, the N+1 bug + fix, tests, evidence command
tenants/      Section 3, Tenant model, contextvar-based TenantManager, TenantMiddleware, tests
emailqueue/   Section 2, Celery task, Redis sliding-window rate limiter, dead-letter, tests
docs/         Generated evidence (query counts)
```

`orders` and `tenants` are connected on purpose, not two disconnected
demos: `Order`/`Customer` use `TenantManager` from `tenants/`, so
`/api/orders/summary/` is a real tenant-scoped, N+1-prone endpoint, both
sections exercise the same models the way they would in one real app.

## Interpretations and assumptions

Stated here per the assessment's own instruction to note ambiguity and
proceed:

- **SQLite, not Postgres, for local dev.** The N+1 bug (Section 1) and
  the tenant-scoping bug (Section 3) are ORM query-shape problems, not
  database-engine problems, and reproduce identically on SQLite. Keeps
  `docker compose up` from needing a Postgres container just to prove the
  point, which matters for the "under 5 minutes, clean environment"
  requirement. `INCIDENT_LOG.md` §3 covers the same investigation against
  a real Postgres instance (`pg_stat_statements`, `EXPLAIN ANALYZE`),
  since that's where this scenario would actually occur.
- **`X-Tenant-ID` header instead of full JWT verification.**
  `TenantMiddleware` resolves the tenant from the subdomain first,
  falling back to an `X-Tenant-ID` header. The header stands in for a
  tenant claim that, in a real deployment, would already be extracted and
  cryptographically verified by upstream JWT auth middleware. Full JWT
  verification is a separate concern from the ORM-level isolation this
  section actually tests, so it wasn't built out.
- **Rate-limiter and burst test scale.** The assignment's literal numbers
  (500 jobs, 200/minute) need ~2.5 real minutes to observe end to end,
  which doesn't belong in a suite expected to run in seconds.
  `emailqueue/tests/test_ratelimiter.py` proves the required 500-submission
  burst property at the assignment's exact count against the real Lua
  script, scaled to `limit=20/window=1s`; `emailqueue/tests/test_tasks.py`
  proves "no job lost" and "a failure is retried" at the Celery-task
  level with `Task.apply()` (synchronous, no real sleep between
  retries). `DESIGN.md` §5 explains why the algorithm's correctness
  doesn't depend on the specific numbers.
- **No `OrderItem` line-items model.** The N+1 hazard is demonstrated
  with a single `ForeignKey` (`Order → Customer`) rather than adding a
  second model purely to also show `prefetch_related`. `ANSWERS.md` §1
  explains why `prefetch_related` would be the right tool for a
  reverse-FK/M2M shape, and why it isn't needed for this fix.

## Section 4: which two questions

Answered **B (pagination trade-offs)** and **C (file upload security)**
in `ANSWERS.md`, the two with the most concrete "name the exact
mechanism" answers.

## Section 5: Loom recording

Optional, not included. Sections 1–4 (the required, graded portion) are
complete and independently verifiable via the test suite and the
commands above.

## Submission checklist

- [x] README.md with setup/run instructions (this file)
- [x] DESIGN.md covering Section 2 architecture
- [x] Written answers labelled per section (ANSWERS.md), plus a
      standalone incident log for Section 1 (INCIDENT_LOG.md)
- [x] All tests pass from a clean environment (`docker compose run --rm web pytest`)
- [x] django-silk wired in + query-count evidence for Section 1
      (`docs/section1_query_evidence.txt`, `/silk/`)
- [x] No `.env` files or secrets committed (`.env.example` only; `.gitignore` excludes `.env`, `db.sqlite3`)
- [ ] Loom recording (optional, not included)
