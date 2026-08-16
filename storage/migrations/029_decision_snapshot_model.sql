-- 029: DecisionSnapshot 强化（收尾文档 §38 / §39）：新增 model 段
ALTER TABLE decision_snapshots ADD COLUMN model JSON;
