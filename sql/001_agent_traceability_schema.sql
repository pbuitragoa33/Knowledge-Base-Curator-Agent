PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS agent_chat_history
(
    id INTEGER PRIMARY KEY,
    course_id INTEGER NOT NULL,
    conversation_id TEXT NOT NULL,
    sender_type TEXT NOT NULL CHECK(sender_type IN ('profesor', 'agente')),
    sender_username TEXT,
    message_text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(course_id) REFERENCES courses(id),
    CHECK(
        (sender_type = 'profesor' AND sender_username IS NOT NULL AND TRIM(sender_username) <> '')
        OR
        (sender_type = 'agente' AND sender_username IS NULL)
    )
);

CREATE TABLE IF NOT EXISTS agent_suggestions
(
    id INTEGER PRIMARY KEY,
    course_id INTEGER NOT NULL,
    conversation_id TEXT,
    tipo TEXT NOT NULL CHECK(tipo IN ('redundancia', 'deactualizacion', 'conflicto')),
    input_context TEXT NOT NULL,
    razonamiento TEXT NOT NULL,
    evidencia_ids TEXT NOT NULL,
    estado TEXT NOT NULL DEFAULT 'pendiente' CHECK(estado IN ('pendiente', 'aprobado', 'rechazado')),
    created_at TEXT NOT NULL,
    reviewed_at TEXT,
    reviewed_by TEXT,
    feedback_text TEXT,
    score_manual INTEGER,
    FOREIGN KEY(course_id) REFERENCES courses(id),
    CHECK(
        (estado = 'pendiente' AND reviewed_at IS NULL AND reviewed_by IS NULL AND score_manual IS NULL)
        OR
        (
            estado IN ('aprobado', 'rechazado')
            AND reviewed_at IS NOT NULL
            AND reviewed_by IS NOT NULL
            AND TRIM(reviewed_by) <> ''
            AND score_manual BETWEEN 1 AND 5
        )
    )
);

CREATE TABLE IF NOT EXISTS agent_chat_feedback
(
    id INTEGER PRIMARY KEY,
    message_id INTEGER NOT NULL,
    course_id INTEGER NOT NULL,
    conversation_id TEXT NOT NULL,
    feedback_value TEXT NOT NULL CHECK(feedback_value IN ('up', 'down')),
    feedback_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(message_id) REFERENCES agent_chat_history(id),
    FOREIGN KEY(course_id) REFERENCES courses(id),
    UNIQUE(message_id, feedback_by)
);

CREATE TABLE IF NOT EXISTS agent_chat_session_ratings
(
    id INTEGER PRIMARY KEY,
    course_id INTEGER NOT NULL,
    conversation_id TEXT NOT NULL,
    rating_score INTEGER NOT NULL CHECK(rating_score BETWEEN 1 AND 5),
    rated_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(course_id) REFERENCES courses(id),
    UNIQUE(course_id, conversation_id, rated_by)
);

CREATE INDEX IF NOT EXISTS idx_agent_chat_history_course_conversation_created_at
    ON agent_chat_history(course_id, conversation_id, created_at);

CREATE INDEX IF NOT EXISTS idx_agent_chat_history_course_created_at
    ON agent_chat_history(course_id, created_at);

CREATE INDEX IF NOT EXISTS idx_agent_suggestions_course_created_at
    ON agent_suggestions(course_id, created_at);

CREATE INDEX IF NOT EXISTS idx_agent_suggestions_course_estado_created_at
    ON agent_suggestions(course_id, estado, created_at);

CREATE INDEX IF NOT EXISTS idx_agent_chat_feedback_course_created_at
    ON agent_chat_feedback(course_id, created_at);

CREATE INDEX IF NOT EXISTS idx_agent_chat_feedback_message_id
    ON agent_chat_feedback(message_id);

CREATE INDEX IF NOT EXISTS idx_agent_chat_session_ratings_course_created_at
    ON agent_chat_session_ratings(course_id, created_at);

CREATE INDEX IF NOT EXISTS idx_agent_chat_session_ratings_conversation_id
    ON agent_chat_session_ratings(conversation_id);
