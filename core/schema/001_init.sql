CREATE TABLE raw_inputs (
 input_id TEXT PRIMARY KEY, source TEXT NOT NULL, native_ref TEXT NOT NULL, received_at TEXT NOT NULL,
 body_text TEXT NOT NULL DEFAULT '', body_masked TEXT NOT NULL DEFAULT '', summary_masked TEXT NOT NULL DEFAULT '',
 media_ref TEXT, sender_role TEXT NOT NULL DEFAULT '不明',
 recv_state TEXT NOT NULL DEFAULT '受け取り済み' CHECK (recv_state IN ('受け取り済み','読み取り中','読み取り済み','読み取り不能','何もしない')),
 read_level TEXT, retention_until TEXT
);
CREATE TABLE people (person_id TEXT PRIMARY KEY, label TEXT NOT NULL, roles TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(roles)), contact_ref TEXT, share_grant_id TEXT, send_grant_id TEXT, notes TEXT NOT NULL DEFAULT '');
CREATE TABLE cases (
 case_id TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '', cause_input_id TEXT REFERENCES raw_inputs(input_id),
 status TEXT NOT NULL DEFAULT '提案前' CHECK(status IN ('提案前','未確定','保留','確認中','返事なし','承認待ち','承認済み','実行中','決まった','準備中','済んだ','見送り','取消','実行結果不明（要照合）')),
 certainty TEXT NOT NULL DEFAULT '推測', urgency TEXT NOT NULL DEFAULT '朝でよい' CHECK(urgency IN ('すぐ','今日中','朝でよい')),
 due_at TEXT, event_start TEXT, event_end TEXT, next_review_at TEXT, trigger_hint TEXT, resume_note TEXT, owner_role TEXT, version INTEGER NOT NULL DEFAULT 1 CHECK(version > 0), created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE case_links (from_case TEXT NOT NULL REFERENCES cases(case_id), to_case TEXT NOT NULL REFERENCES cases(case_id), kind TEXT NOT NULL, PRIMARY KEY(from_case,to_case,kind));
CREATE TABLE questions (q_id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(case_id), text TEXT NOT NULL, fill_method TEXT NOT NULL CHECK(fill_method IN ('台帳','調べる','相手に聞く','本人に聞く','待つ')), status TEXT NOT NULL DEFAULT '未' CHECK(status IN ('未','調べ中','聞き中','埋まった','埋められない')), answer TEXT, updated_at TEXT NOT NULL);
CREATE TABLE history (h_id TEXT PRIMARY KEY, case_id TEXT NOT NULL, at TEXT NOT NULL, actor TEXT NOT NULL, change TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(change)), reason TEXT NOT NULL DEFAULT '', basis TEXT NOT NULL DEFAULT '');
CREATE TABLE plans (plan_id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(case_id), body TEXT NOT NULL CHECK(json_valid(body)), status TEXT NOT NULL DEFAULT '作成中' CHECK(status IN ('作成中','提示','採用','却下','古い')), valid_until TEXT, created_at TEXT NOT NULL);
CREATE TABLE approvals (approval_id TEXT PRIMARY KEY, op_kind TEXT NOT NULL, payload_hash TEXT NOT NULL, granted_at TEXT NOT NULL, expires_at TEXT NOT NULL, state TEXT NOT NULL DEFAULT '有効' CHECK(state IN ('有効','使用済み','期限切れ','取消')), note TEXT NOT NULL DEFAULT '');
CREATE TABLE operations (op_id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('cal_personal','cal_draft','reminder','notion','send')), target_id TEXT NOT NULL, case_id TEXT NOT NULL REFERENCES cases(case_id), payload_hash TEXT NOT NULL, approval_id TEXT REFERENCES approvals(approval_id), linked_op_id TEXT REFERENCES operations(op_id), state TEXT NOT NULL DEFAULT '予約' CHECK(state IN ('予約','実行中','成功（読み戻し済み）','失敗','実行結果不明（要照合）','取消')), handoff_state TEXT CHECK(handoff_state IN ('渡し前（未送信）','渡し済み（未確認）','送信完了','返事あり')), result TEXT CHECK(result IS NULL OR json_valid(result)), created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE goal_nodes (node_id TEXT PRIMARY KEY, parent_id TEXT REFERENCES goal_nodes(node_id), kind TEXT NOT NULL, text TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('確定','推測','休み中','達成','やめた')), horizon TEXT, updated_at TEXT NOT NULL);
CREATE TABLE goal_progress (node_id TEXT NOT NULL REFERENCES goal_nodes(node_id), at TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', PRIMARY KEY(node_id,at));
CREATE TABLE outlook_days (date TEXT PRIMARY KEY, built_at TEXT NOT NULL, digest TEXT NOT NULL);
CREATE TABLE outlook_findings (key TEXT PRIMARY KEY, type TEXT NOT NULL, date TEXT, refs TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(refs)), urgency TEXT NOT NULL DEFAULT '朝でよい' CHECK(urgency IN ('すぐ','今日中','朝でよい')), text TEXT NOT NULL, status TEXT NOT NULL DEFAULT '未確定', first_seen TEXT NOT NULL, last_seen TEXT NOT NULL);
CREATE TABLE twin_prefs (pref_id TEXT PRIMARY KEY, topic TEXT NOT NULL, value TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('確定','推測','古い')));
CREATE TABLE twin_observations (id TEXT PRIMARY KEY, at TEXT NOT NULL, topic TEXT NOT NULL, observed TEXT NOT NULL);
CREATE TABLE twin_predictions (id TEXT PRIMARY KEY, at TEXT NOT NULL, topic TEXT NOT NULL, predicted TEXT NOT NULL, actual TEXT, hit INTEGER CHECK(hit IS NULL OR hit IN (0,1)));
CREATE TABLE delegations (del_id TEXT PRIMARY KEY, scope TEXT NOT NULL CHECK(json_valid(scope)), state TEXT NOT NULL CHECK(state IN ('下書き','影の運用中','本人の確認待ち','有効','期限切れ','取消')), approved_by_approval_id TEXT REFERENCES approvals(approval_id), created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE share_grants (grant_id TEXT PRIMARY KEY, recipient TEXT NOT NULL, info_kinds TEXT NOT NULL CHECK(json_valid(info_kinds)), purpose TEXT NOT NULL, period TEXT NOT NULL, state TEXT NOT NULL DEFAULT '有効');
CREATE TABLE templates (template_id TEXT PRIMARY KEY, name TEXT NOT NULL, body TEXT NOT NULL, slots TEXT NOT NULL CHECK(json_valid(slots)), state TEXT NOT NULL DEFAULT '有効');
CREATE TABLE shadow_records (id TEXT PRIMARY KEY, del_id TEXT NOT NULL REFERENCES delegations(del_id), at TEXT NOT NULL, proposed TEXT NOT NULL CHECK(json_valid(proposed)), user_final TEXT NOT NULL CHECK(json_valid(user_final)), match INTEGER NOT NULL CHECK(match IN (0,1)));
CREATE TABLE sources (source_id TEXT PRIMARY KEY, kind TEXT NOT NULL, reader TEXT NOT NULL, read_only INTEGER NOT NULL CHECK(read_only IN (0,1)), last_seen_at TEXT, status TEXT NOT NULL DEFAULT '未接続' CHECK(status IN ('動いている','止まっている','未接続')), note TEXT NOT NULL DEFAULT '');
CREATE TABLE latency (req_id TEXT NOT NULL, stage TEXT NOT NULL, started_at TEXT NOT NULL, ended_at TEXT, version TEXT, ai_kind TEXT, cost_yen REAL NOT NULL DEFAULT 0 CHECK(cost_yen >= 0), PRIMARY KEY(req_id,stage));
CREATE TABLE success_tech (at TEXT NOT NULL, metric TEXT NOT NULL, value REAL NOT NULL, PRIMARY KEY(at,metric));
CREATE TABLE success_fit (at TEXT NOT NULL, node_id TEXT NOT NULL REFERENCES goal_nodes(node_id), note TEXT NOT NULL DEFAULT '', PRIMARY KEY(at,node_id));
CREATE TABLE success_life (at TEXT NOT NULL, tag TEXT NOT NULL CHECK(tag IN ('役立った','うるさい','足りない')), text TEXT NOT NULL DEFAULT '', PRIMARY KEY(at,tag,text));
CREATE TABLE backups (at TEXT PRIMARY KEY, path_ref TEXT NOT NULL, bytes INTEGER NOT NULL CHECK(bytes >= 0), sha256 TEXT NOT NULL);
CREATE TABLE mirror_checks (at TEXT NOT NULL, target TEXT NOT NULL, diff TEXT NOT NULL CHECK(json_valid(diff)), PRIMARY KEY(at,target));
CREATE TABLE sent_log (key TEXT PRIMARY KEY, at TEXT NOT NULL, channel TEXT NOT NULL);
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL CHECK(json_valid(value)));
CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
CREATE INDEX idx_cases_status ON cases(status);
CREATE INDEX idx_cases_next_review_at ON cases(next_review_at);
CREATE INDEX idx_operations_state ON operations(state);
CREATE INDEX idx_raw_inputs_received_at ON raw_inputs(received_at);
CREATE VIRTUAL TABLE raw_inputs_fts USING fts5(body_text, summary_masked, content='raw_inputs', content_rowid='rowid', tokenize='trigram');
CREATE TRIGGER raw_inputs_ai AFTER INSERT ON raw_inputs BEGIN INSERT INTO raw_inputs_fts(rowid,body_text,summary_masked) VALUES(new.rowid,new.body_text,new.summary_masked); END;
CREATE TRIGGER raw_inputs_ad AFTER DELETE ON raw_inputs BEGIN INSERT INTO raw_inputs_fts(raw_inputs_fts,rowid,body_text,summary_masked) VALUES('delete',old.rowid,old.body_text,old.summary_masked); END;
CREATE TRIGGER raw_inputs_au AFTER UPDATE ON raw_inputs BEGIN INSERT INTO raw_inputs_fts(raw_inputs_fts,rowid,body_text,summary_masked) VALUES('delete',old.rowid,old.body_text,old.summary_masked); INSERT INTO raw_inputs_fts(rowid,body_text,summary_masked) VALUES(new.rowid,new.body_text,new.summary_masked); END;
