CREATE TABLE card_answers (
 card_key TEXT NOT NULL,
 answer TEXT NOT NULL CHECK(answer IN ('出して','いらない','済んだ','まだ','やめた','当たり','外れ','まだ分からない')),
 answered_at TEXT NOT NULL,
 case_id TEXT REFERENCES cases(case_id),
 question_id TEXT REFERENCES questions(q_id),
 PRIMARY KEY(card_key)
);
CREATE INDEX idx_card_answers_key ON card_answers(card_key,answered_at);
CREATE TABLE card_presentations (
 card_key TEXT NOT NULL,
 shown_on TEXT NOT NULL,
 PRIMARY KEY(card_key,shown_on)
);
