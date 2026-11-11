-- Coverage (C4-03, #79). Decisions C4-D003, C4-D004, C4-D014, C4-D017.
--
-- pms.coverage is one mirrored api_id that is still live, or deleted and still has scope.
-- A group that is entirely deleted and has no scope is not in the population.
-- pms.unmatched is the identity rows the API and the gauges share. A fully covered
-- identity is absent. kind_out_of_scope is listed and is not an unmatched identity.

CREATE VIEW pms.coverage AS
SELECT
    a.api_id,
    a.name,
    a.kind,
    a.exchange,
    a.exch_account_uid,
    a.deleted_at,
    a.first_seen_at,
    ea.id AS exchange_account_id,
    (k.in_scope IS TRUE) AS in_scope,
    (a.exchange IS NULL OR a.exch_account_uid IS NULL) AS missing_identity,
    (k.in_scope IS TRUE AND ea.id IS NOT NULL AND ek.kind IS NOT NULL) AS enabled,
    COALESCE(sc.n, 0) AS scope_count,
    sc.since_min AS since
FROM mirror.account a
LEFT JOIN LATERAL (
    SELECT count(*)::bigint AS n, min(s.since) AS since_min
    FROM mirror.scope s
    WHERE s.api_id = a.api_id
) sc ON true
LEFT JOIN pms.account_kind k ON k.kind = a.kind
LEFT JOIN pms.exchange_account ea
    ON ea.deleted_at IS NULL
   AND ea.exchange = a.exchange
   AND ea.exch_account_uid = a.exch_account_uid
LEFT JOIN pms.exchange_account_kind ek
    ON ek.exchange_account_id = ea.id
   AND ek.kind = a.kind
   AND ek.disabled_at IS NULL
WHERE a.deleted_at IS NULL OR COALESCE(sc.n, 0) > 0;

CREATE VIEW pms.unmatched AS
WITH grouped AS (
    SELECT
        CASE
            WHEN NOT c.in_scope AND c.missing_identity THEN 'id:' || c.api_id::text
            WHEN NOT c.in_scope THEN 'oos:' || c.exchange || chr(1) || c.exch_account_uid || chr(1) || c.kind
            WHEN c.missing_identity THEN 'id:' || c.api_id::text
            ELSE 'uid:' || c.exchange || chr(1) || c.exch_account_uid
        END AS grp,
        c.*
    FROM pms.coverage c
)
SELECT
    CASE
        WHEN bool_or(NOT in_scope) THEN 'kind_out_of_scope'
        WHEN bool_or(missing_identity) THEN 'missing_identity'
        WHEN max(exchange_account_id) IS NULL THEN 'no_exchange_account'
        ELSE 'kind_not_enabled'
    END AS reason,
    CASE WHEN bool_or(missing_identity) THEN NULL ELSE min(exchange) END AS exchange,
    CASE WHEN bool_or(missing_identity) THEN NULL ELSE min(exch_account_uid) END AS exch_account_uid,
    CASE WHEN bool_or(NOT in_scope) THEN min(kind) ELSE NULL END AS kind,
    CASE
        WHEN bool_or(NOT in_scope) OR bool_or(missing_identity) OR max(exchange_account_id) IS NULL THEN '{}'::text[]
        ELSE COALESCE(array_agg(DISTINCT kind ORDER BY kind) FILTER (WHERE NOT enabled), '{}')
    END AS missing_kinds,
    CASE
        WHEN bool_or(enabled) THEN COALESCE(bool_or(scope_count > 0 AND NOT enabled), false)
        ELSE COALESCE(bool_or(scope_count > 0), false)
    END AS traded,
    COALESCE(sum(scope_count), 0)::bigint AS scope_count,
    min(since) AS since,
    min(first_seen_at) AS first_seen_at,
    jsonb_agg(
        jsonb_build_object(
            'api_id', api_id,
            'name', name,
            'kind', kind,
            'deleted_at', deleted_at
        ) ORDER BY api_id
    ) AS api_ids
FROM grouped
GROUP BY grp
HAVING bool_or(NOT in_scope)
    OR bool_or(missing_identity)
    OR max(exchange_account_id) IS NULL
    OR bool_or(in_scope AND NOT enabled);
