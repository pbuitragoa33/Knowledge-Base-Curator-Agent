-- Insertar un mensaje entre profesor y agente
INSERT INTO agent_chat_history
    (course_id, conversation_id, sender_type, sender_username, message_text, created_at)
VALUES
    (:course_id, :conversation_id, :sender_type, :sender_username, :message_text, :created_at);

-- Listar historial cronológico del chat de un curso
SELECT
    id,
    course_id,
    conversation_id,
    sender_type,
    sender_username,
    message_text,
    created_at
FROM agent_chat_history
WHERE course_id = :course_id
  AND (:conversation_id IS NULL OR conversation_id = :conversation_id)
ORDER BY created_at ASC, id ASC
LIMIT :limit;

-- Insertar una sugerencia del agente
INSERT INTO agent_suggestions
    (course_id, conversation_id, tipo, input_context, razonamiento,
    evidencia_ids, estado, created_at, reviewed_at, reviewed_by, feedback_text, score_manual)
VALUES
    (:course_id, :conversation_id, :tipo, :input_context, :razonamiento,
    :evidencia_ids, :estado, :created_at, :reviewed_at, :reviewed_by, :feedback_text, :score_manual);

-- Listar sugerencias de un curso con filtros opcionales
SELECT
    id,
    course_id,
    conversation_id,
    tipo,
    input_context,
    razonamiento,
    evidencia_ids,
    estado,
    created_at,
    reviewed_at,
        reviewed_by,
        feedback_text,
        score_manual
FROM agent_suggestions
WHERE course_id = :course_id
  AND (:estado IS NULL OR estado = :estado)
  AND (:tipo IS NULL OR tipo = :tipo)
ORDER BY created_at DESC, id DESC
LIMIT :limit;

-- Actualizar estado final de revisión humana
UPDATE agent_suggestions
SET estado = :estado,
    reviewed_at = :reviewed_at,
    reviewed_by = :reviewed_by,
    feedback_text = :feedback_text,
    score_manual = :score_manual
WHERE id = :suggestion_id;

-- Guardar o actualizar feedback de respuestas del agente
INSERT INTO agent_chat_feedback
    (message_id, course_id, conversation_id, feedback_value, feedback_by, created_at, updated_at)
VALUES
    (:message_id, :course_id, :conversation_id, :feedback_value, :feedback_by, :created_at, :updated_at);

UPDATE agent_chat_feedback
SET feedback_value = :feedback_value,
    updated_at = :updated_at
WHERE message_id = :message_id
  AND feedback_by = :feedback_by;

-- Guardar o actualizar calificacion (1-5) general de una conversacion
INSERT INTO agent_chat_session_ratings
    (course_id, conversation_id, rating_score, rated_by, created_at, updated_at)
VALUES
    (:course_id, :conversation_id, :rating_score, :rated_by, :created_at, :updated_at);

UPDATE agent_chat_session_ratings
SET rating_score = :rating_score,
    updated_at = :updated_at
WHERE course_id = :course_id
  AND conversation_id = :conversation_id
  AND rated_by = :rated_by;
