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

CREATE INDEX IF NOT EXISTS idx_agent_chat_history_course_conversation_created_at
    ON agent_chat_history(course_id, conversation_id, created_at);

CREATE INDEX IF NOT EXISTS idx_agent_chat_history_course_created_at
    ON agent_chat_history(course_id, created_at);

CREATE INDEX IF NOT EXISTS idx_agent_suggestions_course_created_at
    ON agent_suggestions(course_id, created_at);

CREATE INDEX IF NOT EXISTS idx_agent_suggestions_course_estado_created_at
    ON agent_suggestions(course_id, estado, created_at);
