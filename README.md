# Knowledge-Base-Curator-Agent

Agente de curaduría impulsado por IA para cursos o asignaturas universitarias dinámicas. Utiliza modelos de lenguaje extenso (LLMs) y generación aumentada por recuperación (RAG) para supervisar la documentación del curso, detectar inconsistencias, reducir la redundancia y sugerir mejoras entre distintas cohortes, manteniendo a los instructores al mando mediante un sistema de aprobación con intervención humana (human-in-the-loop).



## Descripción General

El **Asistente de Curaduría - Gestor de Documentos** permite:
- **Profesores/Admin**: Subir documentos al curso, ver comentarios de estudiantes y administrar documentos
- **Estudiantes**: Ver documentos del curso y comentar sobre los documentos disponibles

Los documentos son **globales por curso** (no dependen de la sesión), por lo que persisten incluso después de cerrar sesión.

## Características Principales

* Sistema de autenticación con 3 roles (Admin, Profesor, Estudiante) y página de registro (Signup).
* Crear/eliminar cursos y asignación de múltiples profesores por curso.
* Documentos globales y persistentes en la base de datos local SQLite.
* Subir documentos (PDF, MD, DOCX, TXT) mediante drag & drop.
* Sistema de versionado de documentos y visualización de diferencias (Diff) para documentos de texto/markdown.
* Generación local de embeddings por chunk con metadatos asociados y persistencia en ChromaDB por curso.
* Sincronización automática SQLite + ChromaDB al versionar o eliminar documentos (limpieza de chunks obsoletos).
* Búsqueda en lenguaje natural con estrategia configurable por curso: `semantic`, `keyword (BM25)` o `hybrid`.
* Descarga directa de archivos de las distintas versiones subidas.
* Comentarios de Estudiantes que son visibles solo para profesor/admin.
* Hash SHA256 para cada documento y cada subida.
* Historial de cambios con fechas.
* Interfaz diferenciada por rol de usuario.
* Confirmación antes de eliminar.
* Interfaz moderna y responsiva.
* Registro de métricas de recuperación por consulta (estrategia, Top-N, ids recuperados, scores y usuario).
* Workflow del agente con LangGraph para análisis y generación estructurada de sugerencias en estado `pendiente`.
* Tool-calling en chat del agente con recuperación semántica sobre documentos del curso.
* Observabilidad del agente con logs estructurados por nodo, prompt, respuesta LLM, tools y errores.
* Dashboard HITL para revisión humana de sugerencias (`aprobar` / `rechazar`) y trazabilidad por conversación.
* Interfaz de chat dedicada para profesores/admin con listado de fuentes consultadas por respuesta.
* Hook de pre-commit con `detect-secrets` para prevenir fuga accidental de secretos e información sensible.

## Estructura del Proyecto

```
Knowledge Base Curator Agent/
├── app.py                    # Servidor Flask con la lógica principal (orquestador)
├── agent_workflow.py         # Workflow base de LangGraph con OpenAI
├── agent_tools.py            # Herramientas del agente (tool-calling sobre documentos del curso)
├── document_processing.py    # Extracción/chunking de texto por archivo
├── embedding_processing.py   # Proveedores y payloads de embeddings
├── keyword_search.py         # Búsqueda BM25 y fusión híbrida (RRF)
├── observability.py          # Logging estructurado del agente y depuración
├── vector_store.py           # Persistencia y consultas en ChromaDB
├── .pre-commit-config.yaml   # Hook detect-secrets para validación previa a commits
├── requirements.txt          # Dependencias Python
├── .env.example              # Variables de entorno de ejemplo para LLM
├── database.db               # Base de datos SQLite
├── README.md                 # Documentación del proyecto
├── sql/
│   ├── 001_agent_traceability_schema.sql      # DDL de trazabilidad del agente
│   ├── 002_agent_traceability_operations.sql  # Operaciones SQL de referencia
│   ├── 003_agent_prompts_schema.sql           # DDL del catalogo dinamico de prompts
│   └── 004_agent_prompts_seed.sql             # Seed inicial de prompts del agente
├── templates/
│   ├── login.html            # Página de autenticación
│   ├── signup.html           # Página de registro de usuarios
│   ├── index.html            # Página de gestión de cursos
│   ├── upload.html           # Página de documentos, comentarios y búsqueda semántica
│   ├── chat.html             # Interfaz de chat con el agente por curso
│   └── review.html           # Dashboard HITL para revisar sugerencias del agente
├── static/
│   └── style.css            # Estilos CSS
├── tests/
│   ├── run_issue_suite.py   # Suite integrada de validación (Issues 11-15, 19, 20 y 21)
│   ├── test_issue_11.py
│   ├── test_issue_12.py
│   ├── test_issue_13.py
│   ├── test_issue_14.py
│   ├── test_issue_15.py
│   ├── test_issue_19.py
│   ├── test_issue_20.py
│   └── test_issue_21.py
├── run.bat                  # Script de ejecución automática (Windows)
├── run.ps1                  # Script PowerShell alternativo
├── .chroma/                 # Persistencia local de ChromaDB
└── venv_project/            # Entorno virtual local (opcional)
```

## Instalación y Ejecución

### Requisitos Previos
- **Python 3.7+** instalado
- **pip** (gestor de paquetes de Python)

### Opción 1: Ejecución Automática (Recomendado para Windows)

1. **Descargar el proyecto**
2. **Hacer doble clic en `run.bat`** o ejecutar `run.ps1` en PowerShell

El script automáticamente va a instalr las dependencias, iniciar el servidor y abrir la app en el navegador. 

### Opción 2: Ejecución Manual

1. **Abrir terminal en la carpeta del proyecto**

2. **Instalar dependencias**
```bash
pip install -r requirements.txt
```

   La primera ejecución que genere embeddings puede descargar el modelo local `sentence-transformers/all-MiniLM-L6-v2`.

3. **Ejecutar la aplicación**
```bash
python app.py
```

4. **Abrir en navegador**
```
http://localhost:5000
```

## Credenciales de Prueba (Genéricas)

| Usuario | Contraseña | Rol |
|---------|-----------|---------|
| `admin` | `admin123` | Admin | 
| `profesor` | `prof123` | Profesor| 
| `estudiante` | `est123` | Estudiante | 


## Flujo de la Aplicación

### **1. Página de Login**

- Ingresa con tus credenciales
- Selecciona tu rol 

### **2. Selección de Curso** (shelves)

- Ver todos los cursos disponibles
- Seleccionar un curso → Click "Aceptar"
- (Solo Admin/Profesor) Crear nuevos cursos
- (Solo Admin/Profesor) Eliminar cursos, para este se necesita una valdiación adcional (Y/N)


### **3. Gestión de Documentos**

#### Para el caso de Profesor/Admin:
```
┌─────────────────────────────────────┐
│  Subir Documentos del Curso         │
│  [Arrastra archivos aquí]           │
│  [Subir Documentos]                 │
└─────────────────────────────────────┘

┌─────────────────────────────────────┐
│  Documentos del Curso               │
│  ├─ Documento1.pdf                  │
│  │  Hash: a1b2c3d4                  │
│  │  Subido por: admin               │
│  │  Comentarios:                    │
│  │  ├─ Estudiante1: "Excelente" ... │
│  │  └─ Estudiante2: "Bueno" ...     │
│  └─ [Eliminar]                      │
└─────────────────────────────────────┘

┌─────────────────────────────────────┐
│  Comentarios de Estudiantes         │
│  (Vista completa de todos)          │
│  ├─ Documento1.pdf                  │
│  │  ├─ Estudiante1: "Comentario"... │
│  │  └─ Estudiante2: "Comentario"... │
│  └─ Documento2.pdf                  │
│     └─ Estudiante1: "Comentario"... │
└─────────────────────────────────────┘
```

#### Para el caso de los Estudiantes:
```
┌─────────────────────────────────────┐
│  Documentos del Curso               │
│  ├─ Documento1.pdf                  │
│  │  Hash: a1b2c3d4                  │
│  │  Fecha: 2026-03-05 10:30:00      │
│  └─ ──────────────────────          │
│  └─ Documento2.pdf                  │
│     Hash: e5f6g7h8                  │
│     Fecha: 2026-03-05 11:00:00      │
└─────────────────────────────────────┘

┌─────────────────────────────────────┐
│  Agregar Comentario                 │
│  [Selecciona un documento...]       │
│  [Tu comentario aquí]               │
│  [0/500]                            │
│  [Enviar Comentario]                │
└─────────────────────────────────────┘
```


## Estructura de Datos

### Hashes

- **Hash de Documento**: SHA256 de 8 caracteres que identifica cada archivo
  - Ejemplo: `a1b2c3d4`
- **Hash Global de Subida**: SHA256 de 8 caracteres para cada acción de  (snapshot)
  - Ejemplo: `8sds5fg5`

### Comentarios
Cada comentario incluye:
- Nombre del estudiante
- Texto del comentario (máx 500 caracteres)
- Fecha y hora exacta

### Documentos
- Persistentes en la base de datos
- No dependen de la sesión del usuario
- Disponibles para todos los usuarios del curso
- Solo eliminables por Profesor/Admin

### Trazabilidad del Agente

Se agregaron dos tablas para soportar auditoría y seguimiento Human-in-the-Loop:

- `agent_chat_history`
  - Registra mensajes entre profesor y agente por `course_id` y `conversation_id`.
  - `sender_type` solo permite `profesor` o `agente`.
  - `sender_username` es obligatorio para mensajes del profesor y debe quedar en `NULL` para mensajes del agente.

- `agent_suggestions`
  - Registra sugerencias asociadas al curso mediante `course_id`.
  - `tipo` solo permite `redundancia`, `deactualizacion` o `conflicto`.
  - `estado` solo permite `pendiente`, `aprobado` o `rechazado`.
  - `evidencia_ids` se guarda como un arreglo JSON serializado con ids de chunks relacionados.
  - `razonamiento` almacena una justificación explicable para revisión humana, no reasoning oculto del modelo.

Los scripts SQL equivalentes se encuentran en `sql/001_agent_traceability_schema.sql` y `sql/002_agent_traceability_operations.sql`.

### Catalogo de Prompts del Agente

Se agrego la tabla `agent_prompts` para versionar prompts sin dejarlos hardcodeados:

- `tipo_prompt` solo permite `analisis`, `chat` y `formateo`.
- `version` usa enteros incrementales por tipo de prompt.
- `is_active` indica cual version debe usarse en runtime para cada familia.
- `prompt_text` almacena el contenido completo del prompt.
- La funcion interna `get_active_prompt(tipo_prompt)` retorna el texto activo para futuras integraciones con LangGraph.

Los scripts asociados se encuentran en `sql/003_agent_prompts_schema.sql` y `sql/004_agent_prompts_seed.sql`.

### Workflow Base del Agente

Se agrego `agent_workflow.py` como base de integracion con LangGraph y OpenAI:

- Usa `OPENAI_API_KEY` y `OPENAI_MODEL` desde variables de entorno.
- Define `AgentState` con `messages`, `course_id`, `conversation_id`, `extracted_context`, `analysis_output` y `suggestions`.
- Implementa dos nodos en el grafo: `analyze_course` (análisis) y `generate_suggestions` (formateo y persistencia).
- Obtiene prompts activos desde `agent_prompts` para `analisis` y `formateo` vía `get_active_prompt(...)`.
- Normaliza y valida sugerencias (`tipo`, `input_context`, `razonamiento`, `evidencia_ids`) antes de persistirlas con `save_agent_suggestion(...)` en estado `pendiente`.
- Expone `run_agent_once(...)` para ejecutar el workflow compilado y retornar el estado actualizado.

La configuracion local esperada queda documentada en `.env.example`. Issue 45 usa OpenAI unicamente; Gemini queda fuera del alcance actual.

### Tools del Agente

Se agrego `agent_tools.py` para habilitar tool-calling sobre el contenido del curso:

- Define la tool `search_course_documents(query, course_id, top_n=5)` para recuperar evidencia semántica por `course_id`.
- Reutiliza un proveedor local de embeddings (`LocalSentenceTransformerProvider`) para reducir sobrecosto en inferencia.
- Restringe `top_n` al rango permitido y retorna resultados con fuente (`filename`) y `score`.
- Expone `AGENT_TOOLS` y `get_llm_with_tools()` para enlazar herramientas al modelo (`bind_tools`).

### Observabilidad del Agente

Se agrego `observability.py` para trazabilidad operativa del agente:

- Logs estructurados para entrada/salida de nodos (`log_node_input`, `log_node_output`).
- Registro de prompts enviados y respuestas crudas del LLM (`log_prompt`, `log_llm_response`).
- Registro de invocación y resultado de tools (`log_tool_invocation`, `log_tool_result`).
- Captura de errores por nodo o tool (`log_agent_error`).
- Modo de depuración detallada vía variable de entorno `DEBUG_AGENT=True`.

### Human-in-the-Loop (HITL)

El flujo HITL quedó integrado en `app.py` y en la interfaz:

- Persistencia de mensajes en `agent_chat_history` por `course_id` + `conversation_id`.
- Persistencia de sugerencias en `agent_suggestions` con estado inicial `pendiente`.
- Resolución humana de sugerencias con transición a `aprobado` o `rechazado` y registro de `reviewed_by` / `reviewed_at`.
- Rutas de vista dedicadas: `/chat/<course>` y `/review/<course>` para interacción y revisión.

## Formatos de Archivo Soportados

- **PDF** (.pdf)
- **Markdown** (.md)
- **Word** (.docx)
- **Plain Text** (.txt)

## Funcionalidades por Rol

### Admin
- Crear cursos y eliminar cursos (con confirmación)
- Asignar profesores a cursos
- Subir documentos al curso y eliminar documentos
- Consultar historial de documentos y comparar versiones (Diff)
- Descargar documentos
- Ver comentarios de estudiantes
- Chatear con el agente de curaduría por curso
- Revisar y resolver sugerencias del agente (HITL)
- Cambiar de curso y cerrar sesión

### Profesor
- Crear cursos y eliminar cursos propios (con confirmación)
- Subir documentos al curso y eliminar documentos
- Consultar historial de documentos y comparar versiones
- Descargar documentos
- Ver comentarios de estudiantes
- Chatear con el agente de curaduría por curso
- Revisar y resolver sugerencias del agente (HITL)
- Cambiar de curso y cerrar sesión

### Estudiante
- Registrarse en la plataforma
- Ver cursos disponibles, seleccionar e ingresar a un curso
- Ver documentos del curso y descargarlos
- Comentar sobre documentos (máx 500 caracteres)
- Ver historial básico o versiones
- Cambiar de curso y cerrar sesión
- NO puede: Crear/eliminar cursos, asignar profesores, subir/eliminar documentos, ver comentarios de otros estudiantes


## API Endpoints

### Autenticación
- `GET/POST /login` - Inicia sesión
- `GET/POST /signup` - Registro temporal/creación de cuentas
- `GET /logout` - Cierra sesión

### Cursos y Profesores
- `GET /api/courses` - Obtiene lista de cursos
- `POST /api/create-course` - Crea un nuevo curso (admin/profesor)
- `DELETE /api/delete-course/<course>` - Elimina un curso (admin/profesor)
- `GET /api/teachers` - Obtiene una lista de profesores registrados
- `GET /api/course-professors/<course>` - Obtiene los profesores asignados a un curso
- `POST /api/assign-professor` - Asigna un nuevo profesor a un curso

### Documentos
- `GET /api/documents/<course>` - Obtiene documentos del curso
- `POST /api/upload` - Sube documentos (admin/profesor)
- `DELETE /api/delete-document/<id>` - Elimina un documento (admin/profesor)
- `GET /api/download/<int:document_id>` - Descarga la última versión de un documento
- `GET /api/download-version/<int:version_id>` - Descarga una versión específica de un documento

### Historial y Versiones
- `GET /api/history` - Obtiene el historial global de todas las subidas
- `GET /api/document-history/<filename>` - Obtiene el historial de versiones para un documento específico
- `GET /api/document-diff/<document_id>/<version1>/<version2>` - Genera un diff entre dos versiones de un archivo de texto o markdown

### Comentarios
- `GET /api/comments/<document_id>` - Obtiene comentarios (admin/profesor)
- `POST /api/add-comment` - Agrega un comentario (estudiante)

### Consulta Semántica 
- `POST /api/query` - Ejecuta búsqueda semántica por curso y devuelve Top-N resultados con:
  - `chunk_text`
  - `score`
  - `source.filename`
  - `source.upload_date`

### Estrategia de Búsqueda por Curso
- `PUT /api/course-search-strategy/<course_id>` - Actualiza estrategia de recuperación del curso: `semantic`, `keyword` o `hybrid`.

### Métricas de Recuperación
- `GET /api/retrieval-metrics` - Consulta métricas históricas de recuperación (admin/profesor).
- Parámetros opcionales:
  - `course_code`: filtra por curso
  - `limit`: máximo de registros (tope 200)

### Vistas HITL
- `GET /chat/<course>` - Renderiza la interfaz de chat del agente para el curso (admin/profesor).
- `GET /review/<course>` - Renderiza el dashboard de revisión de sugerencias (admin/profesor).

### Agente de Curaduría (HITL)
- `GET /api/agent/suggestions?course_id=<id>` - Lista sugerencias pendientes del curso.
- `POST /api/agent/suggestions/<int:suggestion_id>/resolve` - Marca sugerencia como `aprobado` o `rechazado`.
- `POST /api/agent/chat` - Ejecuta chat contextual con tool-calling y retorna respuesta + fuentes.


### Sincronización de Vector Store en Eliminación y Versionado

- Al eliminar un documento (`DELETE /api/delete-document/<id>`), se eliminan también sus chunks en ChromaDB usando filtro por `doc_hash`.
- Al subir una nueva versión (`POST /api/upload` con mismo nombre en el curso), se eliminan primero los chunks de la versión anterior antes de indexar los nuevos.
- Si ocurre un error de vector store durante eliminación/versionado, SQLite hace rollback para mantener consistencia transaccional.

### Búsqueda y Recuperación en Lenguaje Natural

El endpoint `POST /api/query` soporta tres estrategias, definidas en `courses.search_strategy`:

- `semantic`
  - Usa embeddings del query con `all-MiniLM-L6-v2`.
  - Consulta la colección Chroma del curso y ordena por similitud vectorial.

Para `keyword` y `hybrid` se recuperan inicialmente candidatos pero no se muestra ni se despliega al usuario en la interfaz, se guarda solo para el proceso de ranking interno.

- `keyword` (BM25)
  - Recupera chunks candidatos del curso y los reordena con `BM25Okapi` (`rank_bm25`).
  - Prioriza coincidencias léxicas de términos del query.

- `hybrid`
  - Combina ranking semántico y ranking BM25.
  - Usa **Reciprocal Rank Fusion (RRF)** para fusionar listas.
  - Fórmula usada: `RRF score = 1 / (k + rank)` con `k=60`.

Notas de implementación:
- La estrategia activa se consulta por curso antes de ejecutar la búsqueda.
- Si la estrategia no es válida o está vacía, el sistema hace fallback a `semantic`.
- La respuesta mantiene un formato unificado (`chunk_text`, `score`, `source`, `metadata`) para las tres estrategias.

### Métricas de Recuperación

Cada consulta a `POST /api/query` guarda una traza en la tabla `retrieval_metrics` para análisis posterior.

Campos registrados por consulta:
- `timestamp`
- `course_name`
- `course_code`
- `query_text`
- `search_strategy`
- `top_n`
- `returned_doc_ids` (lista serializada en JSON)
- `scores` (lista serializada en JSON)
- `user`

Consulta de métricas:
- Endpoint: `GET /api/retrieval-metrics`
- Acceso: admin/profesor
- Filtros soportados: `course_code`, `limit`
- Respuesta: `{ total, metrics[] }`

### Interfaz de Usuario (UI)

- Se agregó una sección separada de "Consulta Semántica del Curso" para Admin, Profesor y Estudiante.
- La UI incluye selector Top-N con valores fijos: `1`, `3`, `5`, `10`.
- Cada resultado se muestra como tarjeta con:
  - Chunk de texto
  - Score de similitud
  - Nombre del archivo
  - Fecha de subida
- Para chunks largos se incorporó interacción "Ver más / Ver menos".
- En la vista del curso (`upload.html`) se añadió acceso directo a "Chatear con el Agente" para admin/profesor.
- La interfaz `chat.html` implementa conversación por curso con `conversation_id`, indicador de escritura, manejo de errores y despliegue colapsable de fuentes.
- La interfaz `review.html` implementa tarjetas de sugerencias pendientes con acciones de aprobación/rechazo en línea.

### Pre-commits y Protección de Información

- Se incorporó `.pre-commit-config.yaml` con el hook `detect-secrets` para bloquear commits con secretos detectables.
- Activación sugerida del hook local:
  - `pip install pre-commit`
  - `pre-commit install`
  - `pre-commit run --all-files`

### Pruebas

- Se añadieron pruebas de Issue 21 en `tests/test_issue_21.py` para validar `agent_workflow.py`, el cliente OpenAI y el workflow base de LangGraph.
- Se añadieron pruebas de Issue 20 en `tests/test_issue_20.py` para validar esquema, seed, helpers y scripts SQL del catalogo de prompts.
- Se añadieron pruebas de Issue 19 en `tests/test_issue_19.py` para validar esquema, helpers, constraints y script SQL.
- Se añadieron pruebas de Issue 14 en `tests/test_issue_14.py` (sincronización y rollback).
- Se añadieron pruebas de Issue 15 en `tests/test_issue_15.py` (respuesta, alcance por curso y validaciones).
- Se validó estrategia de búsqueda por curso (`semantic`/`keyword`/`hybrid`) y el registro/consulta de métricas de recuperación.
- Se consolidó la ejecución en `tests/run_issue_suite.py` para validar Issues 11-15, 19, 20 y 21 en conjunto.

## Stack

- **Base de datos**: SQLite3 (local, no requiere servidor externo)
- **Backend**: Flask 
- **Embeddings**: sentence-transformers (modelo local `all-MiniLM-L6-v2`)
- **Frontend**: HTML5, CSS3, JavaScript 
- `CHROMA_PERSIST_DIR` permite cambiar la ruta de persistencia local; por defecto se usa `./.chroma` y esa carpeta queda ignorada por Git
- **Vector Store**: ChromaDB persistente con una colección separada por curso
- **Hashing**: SHA256
- **Autenticación**: Sesiones de Flask
- **Validación**: Extensiones de archivo, duplicados, límites de caracteres



**Versión**: 1.0 
**Última actualización**: Abril, 2026 
**Desarrollado con**: Python, Flask, HTML5, CSS3, JavaScript