#!/bin/bash
# scripts/reconcile.sh - Automated Reconciliation Script (Tianyue Li)
set -e

DATABASE_URL=${DATABASE_URL:-"postgresql://postgres:postgres@localhost:5432/inventory"}

echo "=================================================="
echo " [Reconciliation Audit] Run inventory general ledger integrity check"
echo "=================================================="

# Verification via psql: Total capacity must equal Available + Locked + Traded, and available tickets cannot be less than zero.
docker exec -i $(docker ps -qf "name=postgres") psql -U postgres -d inventory -c "
DO \$\$
DECLARE
    rec RECORD;
    diff_count INT := 0;
BEGIN
    FOR rec IN SELECT * FROM ticket_inventory LOOP
        IF (rec.available_tickets + rec.reserved_tickets + rec.confirmed_tickets) != rec.total_capacity THEN
            RAISE EXCEPTION '❌ [AUDIT FAILED] Data imbalance for tier %! Total % != Available % + Locked % + Confirmed %',
                rec.tier_id, rec.total_capacity, rec.available_tickets, rec.reserved_tickets, rec.confirmed_tickets;
        END IF;

        IF rec.available_tickets < 0 THEN
            RAISE EXCEPTION '❌ [OVERSOLD] Oversold condition in tier %! Available tickets: % (negative value)', rec.tier_id, rec.available_tickets;
        END IF;
    END LOOP;
    RAISE NOTICE '✅ [AUDIT PASSED] Accounts reconciled across all stalls; zero overselling; no missing tickets.';
END \$\$;
"