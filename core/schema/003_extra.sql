-- 引っ越しで受け皿の無い列を捨てない「その他の箱」。既存行は空の JSON。
ALTER TABLE cases ADD COLUMN extra TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(extra));
ALTER TABLE raw_inputs ADD COLUMN extra TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(extra));
