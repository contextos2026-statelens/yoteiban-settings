-- 候補箱などからの引っ越し用に、案件と入力へ列を足す。既存行は既定値のまま。
ALTER TABLE cases ADD COLUMN location TEXT;
ALTER TABLE cases ADD COLUMN duration_min INTEGER CHECK(duration_min IS NULL OR duration_min >= 0);
ALTER TABLE cases ADD COLUMN counterpart TEXT;
ALTER TABLE cases ADD COLUMN inferred_fields TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(inferred_fields));
ALTER TABLE cases ADD COLUMN tentative INTEGER NOT NULL DEFAULT 0 CHECK(tentative IN (0, 1));
ALTER TABLE cases ADD COLUMN reply_by TEXT;

ALTER TABLE raw_inputs ADD COLUMN addressed_to TEXT;
ALTER TABLE raw_inputs ADD COLUMN is_direct TEXT CHECK(is_direct IS NULL OR is_direct IN ('はい', 'いいえ', '不明'));
ALTER TABLE raw_inputs ADD COLUMN named TEXT CHECK(named IS NULL OR named IN ('はい', 'いいえ'));
ALTER TABLE raw_inputs ADD COLUMN schedule_related TEXT CHECK(schedule_related IS NULL OR schedule_related IN ('はい', '迷い', 'いいえ'));
ALTER TABLE raw_inputs ADD COLUMN mute_state TEXT CHECK(mute_state IS NULL OR mute_state IN ('なし', '自動（印のみ）', '本人が明示'));

-- 重要度の補正表（空。行は入れない）。条件は出どころ・種類・語のうち少なくとも1つ。
CREATE TABLE IF NOT EXISTS urgency_corrections (
    corr_id TEXT PRIMARY KEY,
    source TEXT,
    kind TEXT,
    word TEXT,
    adj_type TEXT NOT NULL CHECK(adj_type IN ('点の加減', '固定レベル', '夜の即時')),
    adj_value TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('本人の指示', '学習の提案を承認')),
    review_at TEXT NOT NULL,
    CHECK(coalesce(source, '') <> '' OR coalesce(kind, '') <> '' OR coalesce(word, '') <> ''),
    CHECK(adj_type <> '夜の即時' OR adj_value IN ('する', 'しない'))
);
