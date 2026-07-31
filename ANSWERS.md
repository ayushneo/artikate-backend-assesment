# ANSWERS.md

Written answers for all four sections. The incident narrative and design
doc live in their own files, linked here rather than duplicated.

---

## Section 1: Diagnose a Broken System

Full investigation: **[INCIDENT_LOG.md](INCIDENT_LOG.md)**. This section
covers what's asked here specifically: why the fix works at the database
and ORM level.

**The bug** (`orders/buggy_queryset.py`): `OrderSummaryView.get_queryset()`
returns `Order.objects.all()`. `OrderSummarySerializer.get_customer_email()`
reads `obj.customer.email` for every order in the response.

**Why that costs one query per row:** `customer` is a `ForeignKey`, and
Django resolves it lazily, on first access, via `SELECT ... FROM
orders_customer WHERE id = %s` scoped to that one order. Correct in
general (you don't want every fetch to eagerly join every relation), but
it means a serializer touching a relation inside a loop over N objects
issues N round trips. Each one pays a fixed cost, a network round trip,
query planning, independent of how trivial the row lookup is, and paying
that 200 times sequentially is what turns 80ms into 30s+.

**The fix:** `Order.objects.select_related("customer").all()`
(`orders/views.py`). `select_related` resolves the FK at the SQL level,
as a JOIN, inside the original query: `SELECT orders.*, customers.* FROM
orders JOIN customers ON orders.customer_id = customers.id`. The customer
row comes back in the same result set, so `obj.customer.email` doesn't
trigger a new query, it's already populated from the JOIN. Query count
drops from `1 + N` to `1` and, more importantly, stops scaling with order
count, which is why the regression only showed up at 200+ orders.
`orders/tests/test_n_plus_one.py` asserts this directly.

`select_related` (JOIN) is right here because the relation is a single
FK, one related row per order. For a reverse FK or M2M (many related rows
per order), the JOIN would duplicate the order row per related row;
`prefetch_related` (a second `IN (...)` query, stitched together in
Python) is the correct tool for that shape. Not needed here, worth naming
since the task calls out both.

Evidence (django-silk at `/silk/`, captured for CI in
`docs/section1_query_evidence.txt` and `orders/tests/test_n_plus_one.py`):
**201 queries → 1 query**, 200 orders.

---

## Section 2: Rate-Limited Async Job Queue

Architecture and rate limiter: **[DESIGN.md](DESIGN.md)**.

**SIGKILL:** Celery's default acks a task before running it, so a SIGKILL
mid-task loses the job with no retry. This sets `CELERY_TASK_ACKS_LATE =
True` (ack only after the task finishes) and
`CELERY_TASK_REJECT_ON_WORKER_LOST = True` (a worker that disappears
holding an unacked task gets its task rejected back to the broker for
redelivery), both in `config/settings.py`. A SIGKILL mid-task now gets
redelivered instead of vanishing.

Consequence: this makes the system at-least-once, not exactly-once. A
worker could be killed after calling the provider but before the ack
reaches Redis, so the redelivered task runs again for an email that
already sent. Late-ack only prevents loss, not duplication.
`send_transactional_email` closes that gap itself: before doing anything,
it claims `SET emailqueue:sent:{email_id} NX EX <24h>` in Redis. A
redelivered execution finds the key already set, skips sending, and
returns `{"status": "duplicate-skipped"}`
(`test_duplicate_delivery_is_skipped_not_resent`). Late-ack makes
redelivery happen; the dedupe key makes it safe.

---

## Section 3: Multi-Tenant Data Isolation

**Implementation:** `tenants/managers.py::TenantManager` overrides
`get_queryset()` (and `TenantQuerySet.create()`) so every ORM entry
point, `.all()`, `.filter()`, `.get()`, `.count()`, `.create()`,
`.get_or_create()`, is scoped to a tenant id read from a `contextvars`
variable, and raises `TenantNotSet` instead of returning unscoped data if
no tenant is bound. `tenants/middleware.py::TenantMiddleware` resolves
the tenant per request (subdomain, falling back to `X-Tenant-ID`) and
binds/clears the context in `try`/`finally`. Negative-path tests:
`tenants/tests/test_isolation.py`, `orders/tests/test_summary_view.py`.

**Async and contextvars:** `threading.local()` keys state by OS thread,
and that breaks in async Django two ways:

1. **Interleaving on one thread.** An async view runs coroutines on the
   event loop; many requests can be in flight on the same OS thread,
   switching at every `await`. If tenant binding used
   `threading.local()`, request A sets it, awaits, and while suspended
   request B (same thread) sets it to something else. A resumes reading
   the wrong tenant, exactly the cross-tenant leak this mechanism exists
   to prevent.
2. **`sync_to_async` thread-hopping.** Django runs sync ORM code from
   async views via `sync_to_async`, on a worker thread pulled from a
   pool, not one dedicated to the request. Thread-local state set on the
   event-loop thread is invisible there, and state set inside the call is
   gone once it returns and the thread goes back to the pool.

Fix: `contextvars.ContextVar`, which is what `tenants/context.py`
actually uses. Each `asyncio.Task` gets its own copy of the context at
creation time, so concurrent requests stay isolated no matter how the
event loop interleaves them on a shared thread. `asgiref`'s
`sync_to_async` specifically propagates the calling context into the
worker thread it dispatches to, so a value set before the call is visible
correctly inside it. Django's own internals (async-safe DB connection
handling) made the same move for the same reason.

**Known limitation:** `TenantManager` scopes reads and
`.create()`/`.get_or_create()` writes, not an arbitrary `.update()` call
with an explicit `tenant_id=` kwarg, and it doesn't stop a different
tenant-owned model from simply not using `TenantManager` in the first
place. Scoping is only as strong as "every tenant-owned model uses this
manager," a review discipline, not something this mechanism enforces by
itself.

---

## Section 4: Written Architecture Review

*(Two of three, per the assignment. Chosen: B and C, the two with the
most concrete answers.)*

### Question B: Pagination trade-offs

Offset (`LIMIT n OFFSET m`) has two real costs. Scan behavior: neither
Postgres nor MySQL can seek to offset m, the engine walks and discards m
rows before returning the next n, so cost grows linearly with how deep
into the set you page. Mutation during pagination: offset defines a page
by position, not identity. A row inserted or deleted ahead of the current
offset between two requests (plausible during infinite scroll on a live
table) shifts everything after it by one, so the client sees a duplicate
or skips a row. That's the default behavior under any concurrent write,
not a rare race.

Cursor (keyset: `WHERE id > :last_id ORDER BY id LIMIT n`, on a unique,
ordered column) turns the scan into an index seek: jump straight to `id >
last_id`, nothing discarded, roughly constant cost regardless of scroll
depth. It's mutation-stable for the case that matters here: a row
inserted further down the ordering doesn't shift anything already
fetched, there's no position to perturb. Its real limitation: no jumping
to "page 400," only "the next page after here," and it needs a genuinely
unique, totally ordered column (a plain `created_at` with ties needs a
tiebreaker, typically `id`).

When I'd choose which: cursor for the infinite-scroll feed this question
describes, sequential consumption, needs mutation-stability, no
requirement to jump to an arbitrary page. Offset for admin UIs with
page-number links or a total-page display, where the table is admin-sized
and an occasional off-by-one from a concurrent edit is a rare cosmetic
issue, not a constant one.

### Question C: File upload security

Five vectors, each mitigated at the Django application layer:

1. **Content-type spoofing.** A client-supplied `Content-Type` or
   extension is just a claim; an attacker can upload a script named
   `photo.jpg` with `Content-Type: image/jpeg`. Mitigation: inspect the
   actual bytes with `python-magic` against an explicit MIME allow-list,
   server-side, before persisting.
2. **Path traversal via filename.** A filename like `../../settings.py`
   used unsanitized in a storage path can write outside the upload
   directory. Mitigation: never trust the client filename; run it through
   `django.utils.text.get_valid_filename()`, or generate the storage name
   server-side (e.g. a UUID).
3. **Oversized uploads / zip bombs.** An unbounded upload can exhaust
   memory (Django buffers uploads under `DATA_UPLOAD_MAX_MEMORY_SIZE`),
   or a small compressed file can decompress to gigabytes. Mitigation:
   set `DATA_UPLOAD_MAX_MEMORY_SIZE` and `FILE_UPLOAD_MAX_MEMORY_SIZE`
   explicitly, add an explicit size check before processing, never
   decompress user-supplied archives without a hard cap.
4. **Stored XSS via inline-rendered uploads.** An uploaded SVG or HTML
   can embed `<script>`; serving it inline with a browser-renderable
   `Content-Type` executes it in the victim's session. Mitigation: force
   `Content-Disposition: attachment` on user-uploaded responses, and
   don't allow SVG in an image-upload path that needs inline rendering,
   rasterize server-side or restrict to true raster formats.
5. **Executable / polyglot uploads.** A file valid as both an allowed
   type and an executable (a GIF/PHP polyglot) can pass a naive check and
   still execute if it lands somewhere invokable. Mitigation: require the
   extension allow-list AND the magic-byte signature to agree, and store
   uploads outside the served webroot under randomized names, so even a
   file that slips through has nowhere to run from.
