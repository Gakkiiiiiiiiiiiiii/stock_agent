-- 详细修改方案 §5：DecisionSnapshot v2（runtime/proposal/policy/tools/inputs/output + schema_version）
ALTER TABLE decision_snapshots ADD COLUMN schema_version VARCHAR(40);
ALTER TABLE decision_snapshots ADD COLUMN runtime JSON;
ALTER TABLE decision_snapshots ADD COLUMN tools JSON;
ALTER TABLE decision_snapshots ADD COLUMN inputs JSON;
ALTER TABLE decision_snapshots ADD COLUMN proposal JSON;
ALTER TABLE decision_snapshots ADD COLUMN policy JSON;
ALTER TABLE decision_snapshots ADD COLUMN output JSON;
