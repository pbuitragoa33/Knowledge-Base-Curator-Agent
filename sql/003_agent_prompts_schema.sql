CREATE TABLE IF NOT EXISTS agent_prompts
(
    id INTEGER PRIMARY KEY,
    tipo_prompt TEXT NOT NULL CHECK(tipo_prompt IN ('analisis', 'chat', 'formateo')),
    version INTEGER NOT NULL CHECK(version > 0),
    prompt_text TEXT NOT NULL CHECK(TRIM(prompt_text) <> ''),
    is_active INTEGER NOT NULL DEFAULT 0 CHECK(is_active IN (0, 1)),
    fecha_creacion TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_prompts_tipo_version
    ON agent_prompts(tipo_prompt, version);

CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_prompts_single_active_per_type
    ON agent_prompts(tipo_prompt)
    WHERE is_active = 1;

CREATE INDEX IF NOT EXISTS idx_agent_prompts_tipo_active
    ON agent_prompts(tipo_prompt, is_active);
