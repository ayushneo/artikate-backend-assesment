# Incident Log: `/api/orders/summary/` timeouts after deployment

Symptom: endpoint served the dashboard in ~80ms normally. After a deploy,
users with 200+ orders started timing out at 30s+. No code change was
made to the view itself.

## 1. Scope the symptom first

Only users with 200+ orders are affected, not everyone. That rules out
anything uniformly slow (a bad index on a small table, a broken cache
backend, a dead external call), since those hurt everyone equally. It
points at something whose cost scales with N (order count) on an
otherwise simple listing endpoint. Cost growing linearly with row count
usually means a missing index or a query-count problem: O(N) round trips
instead of O(1). I treated N+1 as the leading hypothesis here, not the
conclusion yet.

## 2. Check what actually shipped

"No code change to the view" doesn't mean nothing relevant changed, it
means the regression is adjacent: the serializer the view delegates to, a
shared manager/queryset, a signal on the model. The real check is diffing
everything the view depends on that shipped in this deploy, not the
view's own diff.

## 3. What I'd check against a real Postgres instance

(No production DB in this exercise, this is what the same investigation
looks like against one.)

- `pg_stat_statements`, grouped by normalized query, sorted by `calls *
  mean_exec_time`. Looking for one query template whose call count is
  suspiciously proportional to something request-shaped, not one
  individually slow query, since an N+1'd query is usually fast on its
  own.
- `EXPLAIN ANALYZE` on the endpoint's main query, to rule out "missing
  index" as the sole cause. If the plan is fine (index scan) but the
  endpoint is still slow, the cost is in how many queries run, not how
  each one runs.

## 4. Reproduce locally, count queries

Built the view the way this bug most plausibly happens:
`OrderSummarySerializer` gained a `customer_email` field
(`SerializerMethodField` reading `obj.customer.email`) in the same deploy
that "touched nothing." The view's `get_queryset()` still returns a bare
`Order.objects.all()`, no `select_related`.

Wired up `django-silk` (`config/settings.py`, `/silk/`) and also captured
the same evidence with `CaptureQueriesContext` so it's provable in CI,
not just eyeballed (`orders/tests/test_n_plus_one.py`,
`orders/management/commands/query_count_demo.py`).

Result, 200 orders for one tenant:

```
BEFORE fix, Order.objects.all(), no select_related: 201 queries
AFTER fix,  select_related('customer'):              1 query
```
(Full output: `docs/section1_query_evidence.txt`.)

201 = 1 query for the order list + 1 per order for `customer_email`.
Confirms the hypothesis: query count scales 1:1 with order count. At 200
orders that's 201 sequential round trips, each paying connection and
planning overhead even though the row lookup itself is trivial, which is
exactly how 80ms becomes 30s+ only for the users the report names.

## 5. Root cause

N+1 query, from a serializer field crossing a `ForeignKey` without
`select_related`. Not a missing index: `EXPLAIN` on the per-row lookup
shows an index scan, the plan is fine, it's just issued 200 extra times.
Not generic serializer overhead, it's specifically the FK traversal. Not
cache invalidation, nothing here is cached.

## 6. Fix and verify

`OrderSummaryView.get_queryset()` → `Order.objects.select_related("customer").all()`
(`orders/views.py`). `ANSWERS.md` Section 1 has the JOIN-level
explanation. `orders/tests/test_n_plus_one.py` pins both the before-fix
count (proving the diagnosis) and the after-fix count staying constant
regardless of order volume (proving the fix generalizes, not just for
this one dataset size).
