-- Migration 0019: the safe (1130), where surplus till cash is kept; reached only by manual treasury movements.
INSERT INTO accounts (code, name, parent_code, kind, normal_side, postable) VALUES
  ('1130', 'الخزنة', '11', 'asset', 'debit', 1);
