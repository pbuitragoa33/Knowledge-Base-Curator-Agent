INSERT INTO agent_prompts
    (tipo_prompt, version, prompt_text, is_active, fecha_creacion)
SELECT
    'analisis',
    1,
    'Eres un analista de curaduria academica. Revisa el material del curso {{course_name}} usando solo {{contexto_recuperado}}. Detecta redundancia, deactualizacion y conflictos. Si la evidencia es insuficiente, dilo explicitamente. No inventes fuentes ni hechos.',
    CASE
        WHEN EXISTS(
            SELECT 1
            FROM agent_prompts
            WHERE tipo_prompt = 'analisis' AND is_active = 1
        ) THEN 0
        ELSE 1
    END,
    CURRENT_TIMESTAMP
WHERE NOT EXISTS(
    SELECT 1
    FROM agent_prompts
    WHERE tipo_prompt = 'analisis' AND version = 1
);

INSERT INTO agent_prompts
    (tipo_prompt, version, prompt_text, is_active, fecha_creacion)
SELECT
    'chat',
    1,
    'Eres un asistente de curaduria para docentes del curso {{course_name}}. Responde en espanol usando {{historial_chat}} y {{contexto_recuperado}}. Prioriza claridad, trazabilidad y evidencia disponible. Diferencia hechos observados de sugerencias.',
    CASE
        WHEN EXISTS(
            SELECT 1
            FROM agent_prompts
            WHERE tipo_prompt = 'chat' AND is_active = 1
        ) THEN 0
        ELSE 1
    END,
    CURRENT_TIMESTAMP
WHERE NOT EXISTS(
    SELECT 1
    FROM agent_prompts
    WHERE tipo_prompt = 'chat' AND version = 1
);

INSERT INTO agent_prompts
    (tipo_prompt, version, prompt_text, is_active, fecha_creacion)
SELECT
    'formateo',
    1,
    'Transforma {{hallazgos}} en sugerencias listas para revision humana. Para cada sugerencia entrega exactamente: tipo, input_context, razonamiento y evidencia_ids. No agregues campos extra y no inventes evidencia.',
    CASE
        WHEN EXISTS(
            SELECT 1
            FROM agent_prompts
            WHERE tipo_prompt = 'formateo' AND is_active = 1
        ) THEN 0
        ELSE 1
    END,
    CURRENT_TIMESTAMP
WHERE NOT EXISTS(
    SELECT 1
    FROM agent_prompts
    WHERE tipo_prompt = 'formateo' AND version = 1
);
