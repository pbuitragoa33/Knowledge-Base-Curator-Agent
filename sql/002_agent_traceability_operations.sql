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
     evidencia_ids, estado, created_at, reviewed_at, reviewed_by)
VALUES
    (:course_id, :conversation_id, :tipo, :input_context, :razonamiento,
     :evidencia_ids, :estado, :created_at, :reviewed_at, :reviewed_by);

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
    reviewed_by
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
    reviewed_by = :reviewed_by
WHERE id = :suggestion_id;
