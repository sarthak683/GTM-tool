#!/usr/bin/env bash
# One-time prod data repair for the sourcing/prospecting/assignment audit
# (2026-08-16). SAFE BY DEFAULT: running it plain only REVIEWS (read-only
# counts + CSV exports). Nothing is written until you run it again with
# APPLY=yes.
#
#   bash scripts/prod-repair/sourcing-repair-2026-08-16.sh          # review
#   APPLY=yes bash scripts/prod-repair/sourcing-repair-2026-08-16.sh # repair
#
# What APPLY does (single transaction, in this order):
#   P1  Dismiss open ghost tasks pointing at deleted entities   (expect ~13)
#   P2  Adopt real domains for ".unknown" placeholder companies
#       — ONLY with unanimous contact-email evidence (>=2 contacts, exactly
#       one corporate domain among them) and no collision with an existing
#       company. Real->real domain changes are NEVER auto-applied.
#   P3  Re-link orphan prospects (company_id IS NULL) to the company whose
#       domain exactly matches their email domain               (expect ~74+)
#   P4  Give prospects missing an SDR their ACCOUNT's SDR       (expect ~95+)
#       (gap-fill, not a handoff: no watermark reset — history attribution
#       stays with the account SDR who actually worked them)
#   P5  Same for the AE slot
#   P6  Backfill ownerless companies whose contacts unanimously name one SDR
#                                                               (expect ~25)
# What stays MANUAL (review CSVs written next to this script):
#   - contact-vs-account SDR conflicts (both set, different) — ~206 rows;
#     decide per pair, then reassign from the UI (cascade now reports splits)
#   - misattached-contact candidates (~193) — email domain matches neither
#     the account domain nor the account's dominant contact domain
#   - real-domain corrections + same-name/different-domain company pairs
#     (e.g. the two different IRIS companies) — merge/fix by hand in the UI
set -euo pipefail
export KUBECONFIG=/Users/sarthak/gtm-secrets/beacon-test-kubeconfig.yaml
NS=gtm-prod
POD=gtm-postgresql-0
OUT_DIR="$(cd "$(dirname "$0")" && pwd)/review-2026-08-16"
mkdir -p "$OUT_DIR"

PSQL='PGPASSWORD="$(cat $POSTGRES_PASSWORD_FILE)" psql -U "$POSTGRES_USER" -d "$POSTGRES_DATABASE"'
RO_PSQL="PGOPTIONS=\"-c default_transaction_read_only=on\" $PSQL"

FREEMAIL="('gmail.com','yahoo.com','outlook.com','hotmail.com','icloud.com','aol.com','proton.me','protonmail.com','live.com','msn.com','googlemail.com','rediffmail.com','qq.com','yandex.com','ymail.com','me.com')"

echo "=== REVIEW: current damage counts (read-only) ==="
kubectl -n "$NS" exec -i "$POD" -- bash -c "$RO_PSQL -At -f -" <<SQL
SELECT 'open_ghost_tasks='||count(*) FROM tasks t
  LEFT JOIN deals d ON d.id=t.entity_id AND t.entity_type='deal'
  LEFT JOIN contacts c ON c.id=t.entity_id AND t.entity_type='contact'
  LEFT JOIN companies co ON co.id=t.entity_id AND t.entity_type='company'
  WHERE t.status='open' AND ((t.entity_type='deal' AND d.id IS NULL)
    OR (t.entity_type='contact' AND c.id IS NULL)
    OR (t.entity_type='company' AND co.id IS NULL));
WITH cd AS (
  SELECT c.company_id, lower(split_part(c.email,'@',2)) AS edom, count(*) n
  FROM contacts c WHERE c.email LIKE '%@%' AND c.company_id IS NOT NULL
    AND lower(split_part(c.email,'@',2)) NOT IN $FREEMAIL
  GROUP BY 1,2
), stats AS (
  SELECT company_id, count(*) AS domains, sum(n) AS contacts,
         (array_agg(edom ORDER BY n DESC))[1] AS dominant
  FROM cd GROUP BY company_id
)
SELECT 'placeholder_domain_adoptions='||count(*) FROM companies co JOIN stats s ON s.company_id=co.id
  WHERE co.domain LIKE '%.unknown' AND s.domains=1 AND s.contacts>=2
    AND NOT EXISTS (SELECT 1 FROM companies c2 WHERE lower(c2.domain)=s.dominant AND c2.id<>co.id);
SELECT 'orphans_relinkable='||count(*) FROM contacts c JOIN companies co
  ON lower(co.domain)=lower(split_part(c.email,'@',2))
  WHERE c.company_id IS NULL AND c.email LIKE '%@%';
SELECT 'sdr_gap_fills='||count(*) FROM contacts c JOIN companies co ON co.id=c.company_id
  WHERE c.sdr_id IS NULL AND co.sdr_id IS NOT NULL;
SELECT 'ae_gap_fills='||count(*) FROM contacts c JOIN companies co ON co.id=c.company_id
  WHERE c.assigned_to_id IS NULL AND co.assigned_to_id IS NOT NULL;
WITH consensus AS (
  SELECT company_id, min(sdr_id::text)::uuid AS sdr_id FROM contacts
  WHERE sdr_id IS NOT NULL AND company_id IS NOT NULL
  GROUP BY company_id HAVING count(DISTINCT sdr_id)=1
)
SELECT 'company_sdr_backfills='||count(*) FROM companies co JOIN consensus x ON x.company_id=co.id
  WHERE co.sdr_id IS NULL;
SELECT 'manual_sdr_conflicts='||count(*) FROM contacts c JOIN companies co ON co.id=c.company_id
  WHERE c.sdr_id IS NOT NULL AND co.sdr_id IS NOT NULL AND c.sdr_id<>co.sdr_id;
SQL

echo
echo "=== REVIEW: exporting manual-decision CSVs to $OUT_DIR ==="
kubectl -n "$NS" exec -i "$POD" -- bash -c "$RO_PSQL --csv -f -" > "$OUT_DIR/sdr-conflicts.csv" <<'SQL'
SELECT co.name AS company, co.sdr_name AS company_sdr, c.sdr_name AS contact_sdr,
       c.first_name||' '||coalesce(c.last_name,'') AS contact, c.email,
       c.sequence_status, c.call_last_at
FROM contacts c JOIN companies co ON co.id=c.company_id
WHERE c.sdr_id IS NOT NULL AND co.sdr_id IS NOT NULL AND c.sdr_id<>co.sdr_id
ORDER BY co.name, c.email;
SQL

kubectl -n "$NS" exec -i "$POD" -- bash -c "$RO_PSQL --csv -f -" > "$OUT_DIR/misattached-candidates.csv" <<SQL
WITH cd AS (
  SELECT c.company_id, lower(split_part(c.email,'@',2)) AS edom, count(*) n,
         row_number() OVER (PARTITION BY c.company_id ORDER BY count(*) DESC) rn
  FROM contacts c WHERE c.email LIKE '%@%' AND c.company_id IS NOT NULL
  GROUP BY 1,2
), dom AS (SELECT company_id, edom AS dominant, n FROM cd WHERE rn=1)
SELECT co.name AS company, co.domain AS company_domain, d.dominant AS company_dominant_email_domain,
       c.first_name||' '||coalesce(c.last_name,'') AS contact, c.email,
       lower(split_part(c.email,'@',2)) AS contact_domain, c.sdr_name
FROM contacts c JOIN companies co ON co.id=c.company_id JOIN dom d ON d.company_id=co.id
WHERE c.email LIKE '%@%' AND d.n>=3
  AND lower(split_part(c.email,'@',2)) <> d.dominant
  AND lower(split_part(c.email,'@',2)) NOT LIKE '%.'||d.dominant
  AND lower(split_part(c.email,'@',2)) <> regexp_replace(split_part(regexp_replace(lower(coalesce(co.domain,'')),'^https?://',''),'/',1),'^www.','')
  AND lower(split_part(c.email,'@',2)) NOT IN $FREEMAIL
ORDER BY co.name, c.email;
SQL

kubectl -n "$NS" exec -i "$POD" -- bash -c "$RO_PSQL --csv -f -" > "$OUT_DIR/domain-corrections-review.csv" <<SQL
WITH cd AS (
  SELECT c.company_id, lower(split_part(c.email,'@',2)) AS edom, count(*) n
  FROM contacts c WHERE c.email LIKE '%@%' AND c.company_id IS NOT NULL
    AND lower(split_part(c.email,'@',2)) NOT IN $FREEMAIL
  GROUP BY 1,2
), stats AS (
  SELECT company_id, count(*) AS domains, sum(n) AS contacts,
         (array_agg(edom ORDER BY n DESC))[1] AS dominant
  FROM cd GROUP BY company_id
)
SELECT co.name AS company, co.domain AS current_domain, s.dominant AS suggested_domain,
       s.contacts AS evidence_contacts,
       EXISTS (SELECT 1 FROM companies c2 WHERE lower(c2.domain)=s.dominant AND c2.id<>co.id) AS suggested_domain_taken_by_another_company
FROM companies co JOIN stats s ON s.company_id=co.id
WHERE co.domain NOT LIKE '%.unknown'
  AND s.dominant <> regexp_replace(split_part(regexp_replace(lower(coalesce(co.domain,'')),'^https?://',''),'/',1),'^www.','')
  AND s.dominant NOT LIKE '%.'||regexp_replace(split_part(regexp_replace(lower(coalesce(co.domain,'')),'^https?://',''),'/',1),'^www.','')
ORDER BY s.contacts DESC;
SQL

echo "Wrote: sdr-conflicts.csv, misattached-candidates.csv, domain-corrections-review.csv"

if [ "${APPLY:-no}" != "yes" ]; then
  echo
  echo "REVIEW ONLY — nothing was changed. Re-run with APPLY=yes to repair."
  exit 0
fi

echo
echo "=== APPLY: running repairs in ONE transaction ==="
kubectl -n "$NS" exec -i "$POD" -- bash -c "$PSQL -a -f -" <<SQL
BEGIN;

-- P1: dismiss open ghost tasks (keeps audit history; no deletes)
UPDATE tasks t SET status='dismissed', updated_at=NOW()
FROM (
  SELECT t2.id FROM tasks t2
  LEFT JOIN deals d ON d.id=t2.entity_id AND t2.entity_type='deal'
  LEFT JOIN contacts c ON c.id=t2.entity_id AND t2.entity_type='contact'
  LEFT JOIN companies co ON co.id=t2.entity_id AND t2.entity_type='company'
  WHERE t2.status='open' AND ((t2.entity_type='deal' AND d.id IS NULL)
    OR (t2.entity_type='contact' AND c.id IS NULL)
    OR (t2.entity_type='company' AND co.id IS NULL))
) ghosts WHERE t.id=ghosts.id;

-- P2: placeholder-domain adoption (unanimous evidence, collision-free)
WITH cd AS (
  SELECT c.company_id, lower(split_part(c.email,'@',2)) AS edom, count(*) n
  FROM contacts c WHERE c.email LIKE '%@%' AND c.company_id IS NOT NULL
    AND lower(split_part(c.email,'@',2)) NOT IN $FREEMAIL
  GROUP BY 1,2
), stats AS (
  SELECT company_id, count(*) AS domains, sum(n) AS contacts,
         (array_agg(edom ORDER BY n DESC))[1] AS dominant
  FROM cd GROUP BY company_id
)
UPDATE companies co SET domain=s.dominant, updated_at=NOW()
FROM stats s
WHERE co.id=s.company_id AND co.domain LIKE '%.unknown'
  AND s.domains=1 AND s.contacts>=2
  AND NOT EXISTS (SELECT 1 FROM companies c2 WHERE lower(c2.domain)=s.dominant AND c2.id<>co.id);

-- P3: orphan relink by exact domain (lower(domain) is unique => deterministic)
UPDATE contacts c SET company_id=co.id, updated_at=NOW()
FROM companies co
WHERE c.company_id IS NULL AND c.email LIKE '%@%'
  AND lower(co.domain)=lower(split_part(c.email,'@',2));

-- P4: ownerless company backfill from unanimous contact SDR. Runs BEFORE the
-- contact gap-fills: the first APPLY ran it last, so contacts of the 24
-- freshly-backfilled companies were left as 49 new "gaps" — filling company
-- slots first lets one pass converge.
WITH consensus AS (
  SELECT company_id, min(sdr_id::text)::uuid AS sdr_id FROM contacts
  WHERE sdr_id IS NOT NULL AND company_id IS NOT NULL
  GROUP BY company_id HAVING count(DISTINCT sdr_id)=1
)
UPDATE companies co
SET sdr_id=u.id, sdr_name=u.name, sdr_email=u.email, sdr_assigned_at=NOW(), updated_at=NOW()
FROM consensus x JOIN users u ON u.id=x.sdr_id
WHERE co.id=x.company_id AND co.sdr_id IS NULL;

-- P5: SDR gap-fill from the account (no watermark reset: gap-fill, not handoff)
UPDATE contacts c SET sdr_id=co.sdr_id, sdr_name=co.sdr_name, updated_at=NOW()
FROM companies co
WHERE co.id=c.company_id AND c.sdr_id IS NULL AND co.sdr_id IS NOT NULL;

-- P6: AE gap-fill from the account
UPDATE contacts c SET assigned_to_id=co.assigned_to_id, assigned_rep_email=co.assigned_rep_email, updated_at=NOW()
FROM companies co
WHERE co.id=c.company_id AND c.assigned_to_id IS NULL AND co.assigned_to_id IS NOT NULL;

COMMIT;

-- Post-repair verification (should all be small/zero)
SELECT 'post_open_ghost_tasks='||count(*) FROM tasks t
  LEFT JOIN deals d ON d.id=t.entity_id AND t.entity_type='deal'
  LEFT JOIN contacts c ON c.id=t.entity_id AND t.entity_type='contact'
  LEFT JOIN companies co ON co.id=t.entity_id AND t.entity_type='company'
  WHERE t.status='open' AND ((t.entity_type='deal' AND d.id IS NULL)
    OR (t.entity_type='contact' AND c.id IS NULL)
    OR (t.entity_type='company' AND co.id IS NULL));
SELECT 'post_orphans='||count(*) FROM contacts WHERE company_id IS NULL;
SELECT 'post_sdr_gaps='||count(*) FROM contacts c JOIN companies co ON co.id=c.company_id
  WHERE c.sdr_id IS NULL AND co.sdr_id IS NOT NULL;
SELECT 'post_placeholder_domains='||count(*) FROM companies WHERE domain LIKE '%.unknown';
SQL

echo
echo "APPLY DONE. Review the UPDATE counts above; the CSVs in $OUT_DIR list the"
echo "manual decisions that remain (SDR conflicts, misattached prospects,"
echo "real-domain corrections, company merges)."
