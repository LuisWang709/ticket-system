-- One Postgres server, one database per service.
CREATE DATABASE catalog;
CREATE DATABASE inventory;
CREATE DATABASE orders;
CREATE DATABASE payments;
CREATE DATABASE notifications;


-- Inventory Service Database & Tables (Tianyue Li)
\connect postgres;
\connect inventory;

\connect inventory;

CREATE TABLE IF NOT EXISTS ticket_inventory (
    tier_id VARCHAR(50) PRIMARY KEY,
    total_capacity INT NOT NULL,
    available_tickets INT NOT NULL,
    reserved_tickets INT NOT NULL DEFAULT 0,
    confirmed_tickets INT NOT NULL DEFAULT 0,
    version INT NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS reservations (
    reservation_id VARCHAR(64) PRIMARY KEY,
    order_id VARCHAR(64) NOT NULL,
    tier_id VARCHAR(50) NOT NULL REFERENCES ticket_inventory(tier_id),
    quantity INT NOT NULL,
    status VARCHAR(20) NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_reservations_order_id ON reservations(order_id);

INSERT INTO ticket_inventory (tier_id, total_capacity, available_tickets, reserved_tickets, confirmed_tickets)
VALUES
    ('1', 100, 100, 0, 0),
    ('2', 100, 100, 0, 0),
    ('3', 100, 100, 0, 0),
    ('VIP', 100, 100, 0, 0),
    ('tier-1', 100, 100, 0, 0)
ON CONFLICT (tier_id) DO UPDATE
SET total_capacity = EXCLUDED.total_capacity;