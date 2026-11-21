-- Migration 0025: the part of a sale return refunded on the customer's account (credit-only or mixed invoices).
-- Old rows keep 0; readers treat refund_method 'credit' as "all of total" so history stays correct.

ALTER TABLE sale_returns ADD COLUMN credit_part INTEGER NOT NULL DEFAULT 0 CHECK (credit_part >= 0);
