# Orquestador de la Aplicación


# ------------
# Librerias 
# ------------

from flask import Flask, render_template, request, jsonify, session, redirect, url_for, send_file
from werkzeug.utils import secure_filename
import os
import sqlite3
import hashlib
import json
from datetime import datetime
from functools import wraps
import shutil
import re

from embedding_processing import (
    DEFAULT_EMBEDDING_BATCH_SIZE,
    DEFAULT_EMBEDDING_DEVICE,
    DEFAULT_EMBEDDING_DIMENSION,
    DEFAULT_EMBEDDING_MODEL_NAME,
    DeterministicEmbeddingProvider,
    EmbeddingGenerationError,
    LocalSentenceTransformerProvider,
    build_embedding_payloads,
)
from document_processing import process_uploaded_file
from vector_store import (
    VectorStoreError,
    delete_course_embeddings_by_metadata,
    delete_course_embeddings,
    query_course_embeddings,
    get_course_embeddings_by_metadata,
    upsert_course_embeddings,
)
from keyword_search import bm25_search, reciprocal_rank_fusion

# ----------
# En Flask
# ----------

app = Flask(__name__)
app.secret_key = 'tu_clave_secreta_segura_cambiar'


# -----------------------------
# Helpers de Configuración
# -----------------------------

def _read_positive_int_env(variable_name, default_value):

    """Lee un entero positivo desde variables de entorno."""

    raw_value = os.environ.get(variable_name, default_value)

    try:

        return max(1, int(raw_value))

    except (TypeError, ValueError):

        return default_value


# ----------------------------------------------------
# Configuración --> extensiones, directorios y rutas
# ----------------------------------------------------

DOWNLOAD_DIR = os.environ.get('DOWNLOAD_DIR', os.path.expanduser('~/Downloads/UploadedFiles'))
ALLOWED_EXTENSIONS = {'pdf', 'md', 'docx', 'txt'}
DATABASE = os.environ.get('DATABASE_PATH', 'database.db')
VALID_SHELF_STATUS = {'borrador', 'publicado'}
EMBEDDING_MODEL_NAME = os.environ.get('EMBEDDING_MODEL_NAME', DEFAULT_EMBEDDING_MODEL_NAME)
EMBEDDING_BATCH_SIZE = _read_positive_int_env('EMBEDDING_BATCH_SIZE', DEFAULT_EMBEDDING_BATCH_SIZE)
EMBEDDING_DEVICE = os.environ.get('EMBEDDING_DEVICE', DEFAULT_EMBEDDING_DEVICE)
EMBEDDING_DIMENSION = DEFAULT_EMBEDDING_DIMENSION
QUERY_TOP_N_DEFAULT = _read_positive_int_env('QUERY_TOP_N_DEFAULT', 5)
QUERY_TOP_N_MAX = _read_positive_int_env('QUERY_TOP_N_MAX', 20)
PROMPT_TYPES = ('analisis', 'chat', 'formateo')
DEFAULT_AGENT_PROMPTS = {
    'analisis': (
        'Eres un analista de curaduria academica. Revisa el material del curso {{course_name}} '
        'usando solo {{contexto_recuperado}}. Detecta redundancia, deactualizacion y conflictos. '
        'Si la evidencia es insuficiente, dilo explicitamente. No inventes fuentes ni hechos.'
    ),
    'chat': (
        'Eres un asistente de curaduria para docentes del curso {{course_name}}. Responde en '
        'espanol usando {{historial_chat}} y {{contexto_recuperado}}. Prioriza claridad, '
        'trazabilidad y evidencia disponible. Diferencia hechos observados de sugerencias.'
    ),
    'formateo': (
        'Transforma {{hallazgos}} en sugerencias listas para revision humana. Para cada '
        'sugerencia entrega exactamente: tipo, input_context, razonamiento y evidencia_ids. '
        'No agregues campos extra y no inventes evidencia.'
    ),
}
CHUNK_REGISTRY = {}
UPLOAD_CHUNK_INDEX = {}
EMBEDDING_REGISTRY = {}
UPLOAD_EMBEDDING_INDEX = {}
EMBEDDING_PROVIDER = None


# -----------------------------------------
# Crear carpeta de descargas si no existe
# -----------------------------------------

# Descarga lo que carga el usuario

os.makedirs(DOWNLOAD_DIR, exist_ok = True)


# -------------------------------
# Funciones de Segurtidad (Hash)
# -------------------------------

# Contraseña como hash  con el algoritmo SHA256

def hash_password(password):

    """Hash simple para contraseña (en producción usar bcrypt)"""

    return hashlib.sha256(password.encode()).hexdigest()

# Verificación de contraseña

def verify_password(password, hashed):

    """Verifica contraseña"""

    return hash_password(password) == hashed

# Verificación de formato de email

def validate_email(email):

    """Valida email con expresión regular"""

    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    
    return re.match(pattern, email) is not None

# Normalización de código de curso para búsqueda y comparación

def normalize_course_code(course_code):

    """Normaliza el código del curso."""

    return str(course_code or '').strip().upper()

# Funciones Auxiliares para Cursos

def professor_can_manage_course(cursor, course_name, professor_username):

    """Valida si el profesor puede gestionar estudiantes del curso."""

    cursor.execute(
        """SELECT 1 FROM courses
           WHERE name = ? AND responsible_teacher = ?""",
        (course_name, professor_username)
    )

    if cursor.fetchone():

        return True

    # Compatibilidad con datos antiguos que usaban tabla de asignaciones.
    cursor.execute(
        """SELECT 1 FROM course_professors
           WHERE course_name = ? AND professor_username = ?""",
        (course_name, professor_username)
    )

    return cursor.fetchone() is not None


# Resolver Contexxto del Curso a partir de id, nombre, código o sesión

def resolve_course_context(cursor, *, course_id = None, course_name = None, course_code = None):

    """Resuelve name + code de curso usando id, nombre, código o sesión."""

    if course_id is not None:

        cursor.execute("SELECT id, name, course_code FROM courses WHERE id = ?", (course_id,))
        row = cursor.fetchone()

        if row:

            return row

    if course_name:

        cursor.execute("SELECT id, name, course_code FROM courses WHERE name = ?", (course_name,))
        row = cursor.fetchone()

        if row:

            return row

    normalized_code = normalize_course_code(course_code)

    if normalized_code:

        cursor.execute("SELECT id, name, course_code FROM courses WHERE course_code = ? COLLATE NOCASE", (normalized_code,))
        row = cursor.fetchone()

        if row:

            return row

    selected_course = session.get('selected_course', '').strip()

    if selected_course:

        cursor.execute("SELECT id, name, course_code FROM courses WHERE name = ?", (selected_course,))
        row = cursor.fetchone()

        if row:

            return row

    return None


# Crear tabla de cursos
def _create_courses_table(cursor, table_name = 'courses'):

    cursor.execute(f'''CREATE TABLE IF NOT EXISTS {table_name}
                 (id INTEGER PRIMARY KEY,
                  name TEXT UNIQUE NOT NULL,
                  course_code TEXT NOT NULL,
                  responsible_teacher TEXT NOT NULL,
                  status TEXT NOT NULL CHECK(status IN ('borrador', 'publicado')),
                  search_strategy TEXT NOT NULL DEFAULT 'semantic',
                  created_by TEXT,
                  created_date TEXT)''')

# Crear índices para la tabla de cursos

def _create_courses_indexes(cursor):

    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_courses_code_nocase ON courses(course_code COLLATE NOCASE)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_courses_responsible_teacher ON courses(responsible_teacher)")

# Obtener el primer profesor registrado para asignar cursos sin responsable

def _get_first_teacher(cursor):

    cursor.execute("SELECT username FROM users WHERE role = 'profesor' ORDER BY id LIMIT 1")
    result = cursor.fetchone()

    return result[0] if result else None

# Codigo unico del curso basado en el nombre o código original, con sufijo incremental si hay colisiones

def _next_unique_course_code(base_code, used_codes):

    normalized_base = normalize_course_code(base_code)[:30] if base_code else 'LEGACY'
    candidate = normalized_base
    suffix = 2

    while candidate.lower() in used_codes:

        suffix_text = f"-{suffix}"
        candidate = f"{normalized_base[:30 - len(suffix_text)]}{suffix_text}"
        suffix += 1

    used_codes.add(candidate.lower())

    return candidate

# Cursos con esquema garantizada y migración de datos si es necesario

def _ensure_courses_schema(con, cursor):

    expected_columns = ['id', 'name', 'course_code', 'responsible_teacher', 'status', 'search_strategy', 'created_by', 'created_date']
    cursor.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'courses'")
    table_exists = cursor.fetchone() is not None

    if not table_exists:

        _create_courses_table(cursor)
        _create_courses_indexes(cursor)
        return

    cursor.execute("PRAGMA table_info(courses)")
    current_columns = [row[1] for row in cursor.fetchall()]

    if current_columns == expected_columns:

        _create_courses_indexes(cursor)
        return

    cursor.execute("SELECT * FROM courses ORDER BY id")

    existing_rows = cursor.fetchall()
    first_teacher = _get_first_teacher(cursor)
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    used_codes = set()
    migrated_rows = []

    for old_row in existing_rows:

        row = dict(zip(current_columns, old_row))
        course_id = row.get('id')
        course_name = (row.get('name') or '').strip()

        if not course_name:

            continue

        created_by = (row.get('created_by') or '').strip() or None
        created_date = row.get('created_date') or now

        course_code = row.get('course_code')

        if course_code:

            course_code = str(course_code).strip()

        if not course_code:

            course_code = f"LEGACY-{course_id if course_id is not None else len(migrated_rows) + 1}"

        unique_code = _next_unique_course_code(course_code, used_codes)
        responsible_teacher = (row.get('responsible_teacher') or '').strip()

        if responsible_teacher:

            cursor.execute("SELECT role FROM users WHERE username = ?", (responsible_teacher,))
            teacher_role = cursor.fetchone()

            if not teacher_role or teacher_role[0] != 'profesor':
                responsible_teacher = ''

        if not responsible_teacher and created_by:

            cursor.execute("SELECT role FROM users WHERE username = ?", (created_by,))
            
            creator_role = cursor.fetchone()

            if creator_role and creator_role[0] == 'profesor':
                
                responsible_teacher = created_by

        if not responsible_teacher:
            responsible_teacher = first_teacher or created_by or 'profesor'

        status = str(row.get('status') or 'borrador').strip().lower()
        
        if status not in VALID_SHELF_STATUS:

            status = 'borrador'

        search_strategy = str(row.get('search_strategy') or 'semantic').strip().lower()

        if search_strategy not in ('semantic', 'keyword', 'hybrid'):

            search_strategy = 'semantic'

        migrated_rows.append(
            (course_id, course_name, unique_code, responsible_teacher, status, search_strategy, created_by, created_date)
        )

    con.execute("PRAGMA foreign_keys = OFF")

    cursor.execute("DROP TABLE IF EXISTS courses_new")

    _create_courses_table(cursor, table_name = 'courses_new')

    cursor.executemany('''INSERT INTO courses_new
                          (id, name, course_code, responsible_teacher, status, search_strategy, created_by, created_date)
                          VALUES (?, ?, ?, ?, ?, ?, ?, ?)''', migrated_rows)
    cursor.execute("DROP TABLE courses")
    cursor.execute("ALTER TABLE courses_new RENAME TO courses")
    _create_courses_indexes(cursor)
    con.execute("PRAGMA foreign_keys = ON")


def get_db_connection():

    """Crea una conexión SQLite con llaves foráneas habilitadas."""

    con = sqlite3.connect(DATABASE)
    con.execute("PRAGMA foreign_keys = ON")
    return con


def _normalize_prompt_type(tipo_prompt):

    """Normaliza el tipo de prompt para consultas internas."""

    return str(tipo_prompt or '').strip().lower()


def _create_agent_traceability_tables(cursor):

    cursor.execute('''CREATE TABLE IF NOT EXISTS agent_chat_history
                 (id INTEGER PRIMARY KEY,
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
                  ))''')

    cursor.execute('''CREATE TABLE IF NOT EXISTS agent_suggestions
                 (id INTEGER PRIMARY KEY,
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
                  FOREIGN KEY(course_id) REFERENCES courses(id),
                  CHECK(
                      (estado = 'pendiente' AND reviewed_at IS NULL AND reviewed_by IS NULL)
                      OR
                      (estado IN ('aprobado', 'rechazado') AND reviewed_at IS NOT NULL AND reviewed_by IS NOT NULL AND TRIM(reviewed_by) <> '')
                  ))''')


def _create_agent_prompts_table(cursor):

    cursor.execute('''CREATE TABLE IF NOT EXISTS agent_prompts
                 (id INTEGER PRIMARY KEY,
                  tipo_prompt TEXT NOT NULL CHECK(tipo_prompt IN ('analisis', 'chat', 'formateo')),
                  version INTEGER NOT NULL CHECK(version > 0),
                  prompt_text TEXT NOT NULL CHECK(TRIM(prompt_text) <> ''),
                  is_active INTEGER NOT NULL DEFAULT 0 CHECK(is_active IN (0, 1)),
                  fecha_creacion TEXT NOT NULL)''')


def _create_agent_traceability_indexes(cursor):

    cursor.execute(
        '''CREATE INDEX IF NOT EXISTS idx_agent_chat_history_course_conversation_created_at
           ON agent_chat_history(course_id, conversation_id, created_at)'''
    )
    cursor.execute(
        '''CREATE INDEX IF NOT EXISTS idx_agent_chat_history_course_created_at
           ON agent_chat_history(course_id, created_at)'''
    )
    cursor.execute(
        '''CREATE INDEX IF NOT EXISTS idx_agent_suggestions_course_created_at
           ON agent_suggestions(course_id, created_at)'''
    )
    cursor.execute(
        '''CREATE INDEX IF NOT EXISTS idx_agent_suggestions_course_estado_created_at
           ON agent_suggestions(course_id, estado, created_at)'''
    )


def _create_agent_prompt_indexes(cursor):

    cursor.execute(
        '''CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_prompts_tipo_version
           ON agent_prompts(tipo_prompt, version)'''
    )
    cursor.execute(
        '''CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_prompts_single_active_per_type
           ON agent_prompts(tipo_prompt)
           WHERE is_active = 1'''
    )
    cursor.execute(
        '''CREATE INDEX IF NOT EXISTS idx_agent_prompts_tipo_active
           ON agent_prompts(tipo_prompt, is_active)'''
    )


def _seed_agent_prompts(cursor):

    """Inserta prompts base sin sobrescribir datos existentes."""

    for tipo_prompt, prompt_text in DEFAULT_AGENT_PROMPTS.items():

        cursor.execute(
            '''SELECT 1 FROM agent_prompts
               WHERE tipo_prompt = ? AND version = 1''',
            (tipo_prompt,)
        )

        if cursor.fetchone():

            continue

        cursor.execute(
            '''SELECT 1 FROM agent_prompts
               WHERE tipo_prompt = ? AND is_active = 1''',
            (tipo_prompt,)
        )
        has_active_version = cursor.fetchone() is not None
        created_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        cursor.execute(
            '''INSERT INTO agent_prompts
               (tipo_prompt, version, prompt_text, is_active, fecha_creacion)
               VALUES (?, ?, ?, ?, ?)''',
            (
                tipo_prompt,
                1,
                prompt_text,
                0 if has_active_version else 1,
                created_at,
            )
        )


# --------------------------------------------------
# Acciones relacinadas a la Base de Datos (SQLite3)
# --------------------------------------------------

# Inicialización y Conexión
# Creación de tablas (en caso de que exista se hace Ingesta)
# Creación de Usuarios y conexiones entre los roles y acciones


def init_db():

    con = get_db_connection()
    c = con.cursor()

    # Tabla de Usuario

    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (id INTEGER PRIMARY KEY,
                  username TEXT UNIQUE NOT NULL,
                  email TEXT UNIQUE NOT NULL,
                  password TEXT NOT NULL,
                  role TEXT NOT NULL)''')

    # Crear usuarios por defecto si no

    c.execute("SELECT COUNT(*) FROM users")

    if c.fetchone()[0] == 0:

        users = [
            ('admin', 'admin@example.com', hash_password('admin123'), 'admin'),
            ('profesor', 'profesor@example.com', hash_password('prof123'), 'profesor'),
            ('estudiante', 'estudiante@example.com', hash_password('est123'), 'estudiante')
        ]

        c.executemany('INSERT INTO users VALUES (NULL, ?, ?, ?, ?)', users)

    # Tabla de Cursos o Shelves + migracion de esquema

    _ensure_courses_schema(con, c)

    # Tabla de Documentos (son globales por curso)

    c.execute('''CREATE TABLE IF NOT EXISTS documents
                 (id INTEGER PRIMARY KEY,
                  course TEXT NOT NULL,
                  doc_hash TEXT UNIQUE NOT NULL,
                  filename TEXT NOT NULL,
                  file_hash TEXT NOT NULL,
                  upload_date TEXT NOT NULL,
                  filepath TEXT NOT NULL,
                  uploaded_by TEXT NOT NULL,
                  FOREIGN KEY(course) REFERENCES courses(name))''')

    # Tabla de Comentarios de Estudiantes

    c.execute('''CREATE TABLE IF NOT EXISTS comments
                 (id INTEGER PRIMARY KEY,
                  document_id INTEGER NOT NULL,
                  student_name TEXT NOT NULL,
                  comment_text TEXT NOT NULL,
                  comment_date TEXT NOT NULL,
                  FOREIGN KEY(document_id) REFERENCES documents(id))''')

    # Tabla Antigua de Uploads (para compatibilidad)

    c.execute('''CREATE TABLE IF NOT EXISTS uploads
                 (id INTEGER PRIMARY KEY,
                  session_id TEXT,
                  upload_hash TEXT UNIQUE,
                  course TEXT,
                  upload_date TEXT,
                  files_json TEXT)''')

    c.execute('''CREATE TABLE IF NOT EXISTS files
                 (id INTEGER PRIMARY KEY,
                  upload_hash TEXT,
                  filename TEXT,
                  file_hash TEXT,
                  upload_date TEXT,
                  filepath TEXT,
                  FOREIGN KEY(upload_hash) REFERENCES uploads(upload_hash))''')

    # Tabla de Profesores Asignados a Cursos (para ADMIN)

    c.execute('''CREATE TABLE IF NOT EXISTS course_professors
                 (id INTEGER PRIMARY KEY,
                  course_name TEXT NOT NULL,
                  professor_username TEXT NOT NULL,
                  assigned_date TEXT NOT NULL,
                  UNIQUE(course_name, professor_username),
                  FOREIGN KEY(course_name) REFERENCES courses(name),
                  FOREIGN KEY(professor_username) REFERENCES users(username))''')

    # Tabla de Estudiantes Asignados a Cursos (para Profesores)

    c.execute('''CREATE TABLE IF NOT EXISTS course_students
                 (id INTEGER PRIMARY KEY,
                  course_name TEXT NOT NULL,
                  student_username TEXT NOT NULL,
                  added_date TEXT NOT NULL,
                  UNIQUE(course_name, student_username),
                  FOREIGN KEY(course_name) REFERENCES courses(name),
                  FOREIGN KEY(student_username) REFERENCES users(username))''')

    # Tabla de Documentos Versionados

    c.execute('''CREATE TABLE IF NOT EXISTS document_versions
                 (id INTEGER PRIMARY KEY,
                  document_id INTEGER NOT NULL,
                  version_number INTEGER NOT NULL,
                  filename TEXT NOT NULL,
                  file_hash TEXT NOT NULL,
                  upload_date TEXT NOT NULL,
                  filepath TEXT NOT NULL,
                  uploaded_by TEXT NOT NULL,
                  UNIQUE(document_id, version_number),
                  FOREIGN KEY(document_id) REFERENCES documents(id))''')

    # Crear cursos por defecto si no existen

    c.execute("SELECT COUNT(*) FROM courses")

    if c.fetchone()[0] == 0:

        default_teacher = _get_first_teacher(c) or 'profesor'
        default_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        default_courses = [
            ('Ingenieria de Software', 'ISW-101', default_teacher, 'publicado', 'admin', default_date),
            ('Ingenieria de Sistemas', 'ISI-102', default_teacher, 'publicado', 'admin', default_date),
            ('Ingenieria en Redes', 'RED-103', default_teacher, 'publicado', 'admin', default_date)
        ]

        c.executemany('''INSERT INTO courses
                         (name, course_code, responsible_teacher, status, created_by, created_date)
                         VALUES (?, ?, ?, ?, ?, ?)''', default_courses)
    
    # Tabla de Métricas de Recuperación

    c.execute('''CREATE TABLE IF NOT EXISTS retrieval_metrics
             (id INTEGER PRIMARY KEY,
              timestamp TEXT NOT NULL,
              course_name TEXT NOT NULL,
              course_code TEXT NOT NULL,
              query_text TEXT NOT NULL,
              search_strategy TEXT NOT NULL,
              top_n INTEGER NOT NULL,
              returned_doc_ids TEXT NOT NULL,
              scores TEXT NOT NULL,
              user TEXT NOT NULL)''')

    _create_agent_traceability_tables(c)
    _create_agent_traceability_indexes(c)
    _create_agent_prompts_table(c)
    _create_agent_prompt_indexes(c)
    _seed_agent_prompts(c)

    con.commit()
    con.close()


init_db()


def seed_agent_prompts():

    """Ejecuta la siembra idempotente de prompts base."""

    con = get_db_connection()
    c = con.cursor()

    try:

        _seed_agent_prompts(c)
        con.commit()

    finally:

        con.close()


def _serialize_evidence_ids(evidence_ids):

    """Serializa ids de evidencia para almacenamiento en SQLite."""

    if evidence_ids is None:

        evidence_ids = []

    return json.dumps([str(evidence_id) for evidence_id in evidence_ids])


def _deserialize_evidence_ids(raw_value):

    """Convierte el campo serializado de evidencia a una lista Python."""

    if not raw_value:

        return []

    try:

        decoded = json.loads(raw_value)

    except (TypeError, ValueError):

        return []

    if not isinstance(decoded, list):

        return []

    return [str(item) for item in decoded]


def save_agent_chat_message(course_id, conversation_id, sender_type, message_text, sender_username = None):

    """Guarda un mensaje de trazabilidad entre profesor y agente."""

    created_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    normalized_sender_username = sender_username.strip() if isinstance(sender_username, str) else None
    normalized_conversation_id = str(conversation_id or '').strip()
    normalized_message_text = str(message_text or '').strip()
    normalized_sender_type = str(sender_type or '').strip()
    con = get_db_connection()
    c = con.cursor()

    try:

        c.execute(
            '''INSERT INTO agent_chat_history
               (course_id, conversation_id, sender_type, sender_username, message_text, created_at)
               VALUES (?, ?, ?, ?, ?, ?)''',
            (
                int(course_id),
                normalized_conversation_id,
                normalized_sender_type,
                normalized_sender_username,
                normalized_message_text,
                created_at,
            )
        )
        con.commit()
        return c.lastrowid

    finally:

        con.close()


def save_agent_suggestion(
    course_id,
    tipo,
    input_context,
    razonamiento,
    evidencia_ids,
    estado = 'pendiente',
    conversation_id = None,
    reviewed_by = None,
):

    """Guarda una sugerencia generada por el agente para auditoría futura."""

    created_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    normalized_estado = str(estado or 'pendiente').strip()
    normalized_reviewed_by = reviewed_by.strip() if isinstance(reviewed_by, str) else None
    normalized_reviewed_at = created_at if normalized_estado in ('aprobado', 'rechazado') and normalized_reviewed_by else None
    con = get_db_connection()
    c = con.cursor()

    try:

        c.execute(
            '''INSERT INTO agent_suggestions
               (course_id, conversation_id, tipo, input_context, razonamiento,
                evidencia_ids, estado, created_at, reviewed_at, reviewed_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (
                int(course_id),
                str(conversation_id or '').strip() or None,
                str(tipo or '').strip(),
                str(input_context or '').strip(),
                str(razonamiento or '').strip(),
                _serialize_evidence_ids(evidencia_ids),
                normalized_estado,
                created_at,
                normalized_reviewed_at,
                normalized_reviewed_by,
            )
        )
        con.commit()
        return c.lastrowid

    finally:

        con.close()


def list_agent_chat_history(course_id, conversation_id = None, limit = 100):

    """Lista mensajes del historial del agente para un curso."""

    normalized_limit = max(1, int(limit))
    con = get_db_connection()
    c = con.cursor()

    try:

        if conversation_id:

            c.execute(
                '''SELECT id, course_id, conversation_id, sender_type, sender_username, message_text, created_at
                   FROM agent_chat_history
                   WHERE course_id = ? AND conversation_id = ?
                   ORDER BY created_at ASC, id ASC
                   LIMIT ?''',
                (int(course_id), str(conversation_id).strip(), normalized_limit)
            )

        else:

            c.execute(
                '''SELECT id, course_id, conversation_id, sender_type, sender_username, message_text, created_at
                   FROM agent_chat_history
                   WHERE course_id = ?
                   ORDER BY created_at ASC, id ASC
                   LIMIT ?''',
                (int(course_id), normalized_limit)
            )

        rows = c.fetchall()

    finally:

        con.close()

    history = []

    for row in rows:

        history.append({
            'id': row[0],
            'course_id': row[1],
            'conversation_id': row[2],
            'sender_type': row[3],
            'sender_username': row[4],
            'message_text': row[5],
            'created_at': row[6],
        })

    return history


def list_agent_suggestions(course_id, estado = None, tipo = None, limit = 100):

    """Lista sugerencias de un curso con filtros opcionales."""

    normalized_limit = max(1, int(limit))
    where_clauses = ['course_id = ?']
    parameters = [int(course_id)]

    if estado:

        where_clauses.append('estado = ?')
        parameters.append(str(estado).strip())

    if tipo:

        where_clauses.append('tipo = ?')
        parameters.append(str(tipo).strip())

    parameters.append(normalized_limit)
    con = get_db_connection()
    c = con.cursor()

    try:

        c.execute(
            f'''SELECT id, course_id, conversation_id, tipo, input_context, razonamiento,
                       evidencia_ids, estado, created_at, reviewed_at, reviewed_by
                FROM agent_suggestions
                WHERE {' AND '.join(where_clauses)}
                ORDER BY created_at DESC, id DESC
                LIMIT ?'''
            ,
            tuple(parameters)
        )
        rows = c.fetchall()

    finally:

        con.close()

    suggestions = []

    for row in rows:

        suggestions.append({
            'id': row[0],
            'course_id': row[1],
            'conversation_id': row[2],
            'tipo': row[3],
            'input_context': row[4],
            'razonamiento': row[5],
            'evidencia_ids': _deserialize_evidence_ids(row[6]),
            'estado': row[7],
            'created_at': row[8],
            'reviewed_at': row[9],
            'reviewed_by': row[10],
        })

    return suggestions


def update_agent_suggestion_status(suggestion_id, estado, reviewed_by):

    """Actualiza el estado final de una sugerencia revisada por una persona."""

    reviewed_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    normalized_reviewed_by = reviewed_by.strip() if isinstance(reviewed_by, str) else None
    con = get_db_connection()
    c = con.cursor()

    try:

        c.execute(
            '''UPDATE agent_suggestions
               SET estado = ?, reviewed_at = ?, reviewed_by = ?
               WHERE id = ?''',
            (
                str(estado or '').strip(),
                reviewed_at,
                normalized_reviewed_by,
                int(suggestion_id),
            )
        )
        con.commit()
        return c.rowcount > 0

    finally:

        con.close()


def get_active_prompt(tipo_prompt):

    """Retorna el prompt activo para un tipo dado, si existe."""

    normalized_tipo_prompt = _normalize_prompt_type(tipo_prompt)

    if not normalized_tipo_prompt:

        return None

    con = get_db_connection()
    c = con.cursor()

    try:

        c.execute(
            '''SELECT prompt_text
               FROM agent_prompts
               WHERE tipo_prompt = ? AND is_active = 1
               ORDER BY version DESC
               LIMIT 1''',
            (normalized_tipo_prompt,)
        )
        row = c.fetchone()

    finally:

        con.close()

    return row[0] if row else None


def list_agent_prompts(tipo_prompt = None, include_inactive = True):

    """Lista versiones de prompts para soporte interno y pruebas."""

    where_clauses = []
    parameters = []
    normalized_tipo_prompt = _normalize_prompt_type(tipo_prompt)

    if normalized_tipo_prompt:

        where_clauses.append('tipo_prompt = ?')
        parameters.append(normalized_tipo_prompt)

    if not include_inactive:

        where_clauses.append('is_active = 1')

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ''
    con = get_db_connection()
    c = con.cursor()

    try:

        c.execute(
            f'''SELECT id, tipo_prompt, version, prompt_text, is_active, fecha_creacion
                FROM agent_prompts
                {where_sql}
                ORDER BY tipo_prompt ASC, version DESC, id DESC''',
            tuple(parameters)
        )
        rows = c.fetchall()

    finally:

        con.close()

    prompts = []

    for row in rows:

        prompts.append({
            'id': row[0],
            'tipo_prompt': row[1],
            'version': row[2],
            'prompt_text': row[3],
            'is_active': bool(row[4]),
            'fecha_creacion': row[5],
        })

    return prompts


def create_agent_prompt_version(tipo_prompt, prompt_text, is_active = False):

    """Crea una nueva version de prompt dentro de su familia."""

    normalized_tipo_prompt = _normalize_prompt_type(tipo_prompt)
    normalized_prompt_text = str(prompt_text or '').strip()
    normalized_is_active = bool(is_active)
    created_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    con = get_db_connection()
    c = con.cursor()

    try:

        c.execute(
            '''SELECT COALESCE(MAX(version), 0)
               FROM agent_prompts
               WHERE tipo_prompt = ?''',
            (normalized_tipo_prompt,)
        )
        next_version = int(c.fetchone()[0]) + 1

        if normalized_is_active:

            c.execute(
                '''UPDATE agent_prompts
                   SET is_active = 0
                   WHERE tipo_prompt = ?''',
                (normalized_tipo_prompt,)
            )

        c.execute(
            '''INSERT INTO agent_prompts
               (tipo_prompt, version, prompt_text, is_active, fecha_creacion)
               VALUES (?, ?, ?, ?, ?)''',
            (
                normalized_tipo_prompt,
                next_version,
                normalized_prompt_text,
                1 if normalized_is_active else 0,
                created_at,
            )
        )
        con.commit()
        return c.lastrowid

    finally:

        con.close()


def activate_agent_prompt_version(tipo_prompt, version):

    """Activa una version existente y desactiva las demas del mismo tipo."""

    normalized_tipo_prompt = _normalize_prompt_type(tipo_prompt)
    con = get_db_connection()
    c = con.cursor()

    try:

        c.execute(
            '''SELECT 1
               FROM agent_prompts
               WHERE tipo_prompt = ? AND version = ?''',
            (normalized_tipo_prompt, int(version))
        )

        if c.fetchone() is None:

            return False

        c.execute(
            '''UPDATE agent_prompts
               SET is_active = 0
               WHERE tipo_prompt = ?''',
            (normalized_tipo_prompt,)
        )
        c.execute(
            '''UPDATE agent_prompts
               SET is_active = 1
               WHERE tipo_prompt = ? AND version = ?''',
            (normalized_tipo_prompt, int(version))
        )
        con.commit()
        return c.rowcount > 0

    finally:

        con.close()


# ---------------------------------
# Decorador para requerir el login
# ---------------------------------

def login_required(f):

    """El login es requerido"""

    @wraps(f)

    def decorated_function(*args, **kwargs):

        if 'user' not in session:

            return redirect(url_for('login'))
        
        return f(*args, **kwargs)
    
    return decorated_function


# -------------------------------------------------
# Decorador para requerir el rol admin o profesor
# -------------------------------------------------

def admin_required(f):

    """Para admin o profesor"""

    @wraps(f)

    def decorated_function(*args, **kwargs):

        if 'user' not in session:

            return redirect(url_for('login'))
        
        # Conexión con la base de datos
        
        con = sqlite3.connect(DATABASE)
        c = con.cursor()
        c.execute("SELECT role FROM users WHERE username = ?", (session['user'],))
        result = c.fetchone()
        con.close()
        
        if not result or result[0] not in ['admin', 'profesor']:

            return jsonify({'error': 'Acceso denegado'}), 403
        
        return f(*args, **kwargs)
    
    return decorated_function


# --------------------------------------------------------
# Permitir las tipos de archivos (.txt, .md, .docx, .pdf)
# --------------------------------------------------------

def allowed_file(filename):

    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


# Inicializar proveedor de embeddings cuando es necesatio

def get_embedding_provider():

    """Inicializa el proveedor de embeddings solo cuando es necesario."""

    global EMBEDDING_PROVIDER

    if EMBEDDING_PROVIDER is None:

        if app.config.get('TESTING'):

            EMBEDDING_PROVIDER = DeterministicEmbeddingProvider(
                embedding_dimension = EMBEDDING_DIMENSION,
            )

        else:

            EMBEDDING_PROVIDER = LocalSentenceTransformerProvider(
                model_name=EMBEDDING_MODEL_NAME,
                batch_size=EMBEDDING_BATCH_SIZE,
                device=EMBEDDING_DEVICE,
                embedding_dimension=EMBEDDING_DIMENSION,
            )

    return EMBEDDING_PROVIDER


# Registrar chunks y embeddings generados durante el proceso de subida para su posterior limpieza o rollback

def register_chunk_records(upload_hash, chunk_records):

    """Mantiene el registro temporal chunk -> snapshot de subida."""

    upload_chunk_ids = UPLOAD_CHUNK_INDEX.setdefault(upload_hash, [])

    for record in chunk_records:

        chunk_id = record['chunk_id']
        CHUNK_REGISTRY[chunk_id] = record
        upload_chunk_ids.append(chunk_id)

    return upload_chunk_ids


# Registrar payloads de embeddings generadoss durante el proceso de subida para su posterior limpieza o rollback

def register_embedding_payloads(upload_hash, embedding_payloads):

    """Mantiene el registro temporal chunk -> embedding asociado."""

    upload_embedding_ids = UPLOAD_EMBEDDING_INDEX.setdefault(upload_hash, [])

    for payload in embedding_payloads:

        chunk_id = payload['chunk_id']
        EMBEDDING_REGISTRY[chunk_id] = payload
        upload_embedding_ids.append(chunk_id)

    return upload_embedding_ids

# Funciones para limpieza de registros temporales de chunks y embeddings asociados a una subida, en caso de error o rollback

def clear_upload_staging(upload_hash):

    """Limpia los registros temporales de chunks y embeddings de una subida."""

    chunk_ids = UPLOAD_CHUNK_INDEX.pop(upload_hash, [])

    for chunk_id in chunk_ids:

        CHUNK_REGISTRY.pop(chunk_id, None)

    embedding_ids = UPLOAD_EMBEDDING_INDEX.pop(upload_hash, [])

    for chunk_id in embedding_ids:

        EMBEDDING_REGISTRY.pop(chunk_id, None)

# Remocion de archivos creados durante una salida fallida

def remove_uploaded_files(filepaths):

    """Elimina archivos creados durante una subida fallida."""

    for filepath in filepaths:

        if not filepath:

            continue

        try:

            if os.path.exists(filepath):

                os.remove(filepath)

        except OSError:

            app.logger.warning('Failed to remove uploaded file after rollback: %s', filepath)


# -------------------------------------------------
# Generar Hash de 8 caracteres
# -------------------------------------------------

def generate_hash(data):

    """Genera un hash SHA256 de 8 caracteres"""

    if isinstance(data, str):

        data = data.encode()

    hash_full = hashlib.sha256(data).hexdigest()

    return hash_full[:8]


# -------------------------------------------------
# Generar Hash para un archivo subido
# -------------------------------------------------

def generate_file_hash(filepath):

    """Genera hash SHA256 del contenido del archivo"""

    hash_sha256 = hashlib.sha256()

    with open(filepath, "rb") as f:

        for chunk in iter(lambda: f.read(4096), b""):

            hash_sha256.update(chunk)

    return hash_sha256.hexdigest()[:8]

def save_retrieval_metrics(course_name, course_code, query_text, strategy, top_n, results, user):
    """Guarda métricas de una consulta en retrieval_metrics de forma no bloqueante."""
    try:
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        doc_ids = json.dumps([r.get('metadata', {}).get('doc_hash', '') for r in results])
        scores = json.dumps([round(r.get('score', 0.0), 6) for r in results])

        con = sqlite3.connect(DATABASE)
        c = con.cursor()
        c.execute('''INSERT INTO retrieval_metrics
                     (timestamp, course_name, course_code, query_text, search_strategy,
                      top_n, returned_doc_ids, scores, user)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                  (timestamp, course_name, course_code, query_text, strategy,
                   top_n, doc_ids, scores, user))
        con.commit()
        con.close()
    except Exception:
        app.logger.warning('No se pudieron guardar las métricas de la consulta.')


# -------------------------------------------------
# Funciones Auxiliares para Diff y Versionado
# -------------------------------------------------

def extract_file_content(filepath):

    """Extrae el contenido de un archivo (txt, md, docx en texto)"""

    try:

        ext = os.path.splitext(filepath)[1].lower()

        if ext in ['.txt', '.md']:

            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:

                return f.read()

        elif ext == '.docx':

            # Para docx, extraer solo el texto bósico
            import subprocess
            try:
                result = subprocess.run(['powershell', '-Command', f'''
                    Add-Type -AssemblyName DocumentFormat.OpenXml
                    $doc = [DocumentFormat.OpenXml.Packaging.WordprocessingDocument]::Open('{filepath}', $false)
                    $text = ""
                    foreach ($p in $doc.MainDocumentPart.Document.Body.Descendants([DocumentFormat.OpenXml.Wordprocessing.Paragraph])) {{
                        foreach ($r in $p.Descendants([DocumentFormat.OpenXml.Wordprocessing.Run])) {{
                            foreach ($t in $r.Descendants([DocumentFormat.OpenXml.Wordprocessing.Text])) {{
                                $text += $t.Text
                            }}
                        }}
                        $text += "`n"
                    }}
                    $doc.Close()
                    Write-Output $text
                '''], capture_output=True, text=True, timeout=10)
                return result.stdout if result.returncode == 0 else ""
            except:
                return ""

        elif ext == '.pdf':

            return "[Archivo PDF - no se puede mostrar diff visual]"

        else:

            return "[Formato no soportado para diff]"

    except Exception as e:

        return f"[Error al leer archivo: {str(e)}]"


def compare_file_versions(filepath1, filepath2):

    """Compara dos versiones de un archivo y retorna el resumen de cambios"""

    content1 = extract_file_content(filepath1)
    content2 = extract_file_content(filepath2)

    lines1 = content1.split('\n') if content1 else []
    lines2 = content2.split('\n') if content2 else []

    # Contar lóneas añadidas, eliminadas e igual

    added = 0
    removed = 0
    same = 0

    # Simplificar: contar lóneas diferentes

    max_lines = max(len(lines1), len(lines2))

    for i in range(max_lines):

        line1 = lines1[i] if i < len(lines1) else ""
        line2 = lines2[i] if i < len(lines2) else ""

        if line1 == line2 and line1.strip():

            same += 1

        elif line1 and not line2:

            removed += 1

        elif line2 and not line1:

            added += 1

        elif line1 != line2:

            # Cambio: contar como eliminada y añadida

            removed += 1
            added += 1

    return {
        'added': added,
        'removed': removed,
        'same': same,
        'total': max(len(lines1), len(lines2))
    }


# -----------------------
# Rutas de Autenticación
# -----------------------

# Signup

@app.route('/signup', methods = ['GET', 'POST'])

def signup():

    if request.method == 'POST':

        role = request.form.get('role')
        email = request.form.get('email')
        username = request.form.get('username')
        password = request.form.get('password')
        password_confirm = request.form.get('password_confirm')

        error = None

        if not role or role not in ['admin', 'profesor', 'estudiante']:

            error = 'Selecciona un rol vólido'

        elif not email or not validate_email(email):

            error = 'El correo no es vólido'

        elif not username or len(username.strip()) == 0:

            error = 'El nombre de usuario es requerido'

        elif not password or not password_confirm:

            error = 'La contraseóa es requerida'

        elif len(password) < 8 or len(password) > 20:

            error = 'La contraseóa debe tener entre 8 y 20 caracteres'

        elif password != password_confirm:

            error = 'Las contraseóas no coinciden'

        if not error:
            
            con = sqlite3.connect(DATABASE)
            c = con.cursor()

            c.execute("SELECT id FROM users WHERE email = ?", (email,))
            
            if c.fetchone():

                error = 'Este correo ya está registrado'

            else:

                c.execute("SELECT id FROM users WHERE username = ?", (username,))
                
                if c.fetchone():

                    error = 'Este nombre de usuario ya existe'

            if error:

                con.close()

                return render_template('signup.html', error = error)

            try:

                hashed_password = hash_password(password)
                c.execute('INSERT INTO users VALUES (NULL, ?, ?, ?, ?)',
                         (username, email, hashed_password, role))
                con.commit()
                con.close()

                return redirect(url_for('login'))
            
            except sqlite3.IntegrityError:

                con.close()

                return render_template('signup.html', error = 'Error al crear la cuenta. Intenta de nuevo')

        return render_template('signup.html', error = error)

    return render_template('signup.html')


# Login

@app.route('/login', methods = ['GET', 'POST'])

def login():

    if request.method == 'POST':

        login_input = request.form.get('login')
        password = request.form.get('password')
        
        con = sqlite3.connect(DATABASE)
        c = con.cursor()
        c.execute("SELECT password, role, username FROM users WHERE username = ? OR email = ?", (login_input, login_input))
        result = c.fetchone()
        con.close()
        
        if result and verify_password(password, result[0]):

            session['user'] = result[2]
            session['role'] = result[1]
            session['session_id'] = generate_hash(str(datetime.now()))

            return redirect(url_for('index'))
        
        else:

            return render_template('login.html', error = 'Usuario/correo o contraseóa incorrectos')
    
    return render_template('login.html')


# Logout

@app.route('/logout')

def logout():

    session.clear()

    return redirect(url_for('login'))


# Avance

@app.route('/')

@login_required

def index():

    return render_template('index.html', role = session.get('role'))


# Subir/crear Curso

@app.route('/upload/<course>')

@login_required

def upload_page(course):

    con = sqlite3.connect(DATABASE)
    c = con.cursor()
    c.execute("SELECT name FROM courses WHERE name = ?", (course,))
    result = c.fetchone()
    con.close()
    
    if not result:

        return redirect(url_for('index'))
    
    session['selected_course'] = course

    return render_template('upload.html', course = course)


# Subida de Archivos y Procesamiento para Indexación

@app.route('/api/upload', methods = ['POST'])

@admin_required

def upload_files():

    if 'files[]' not in request.files:

        return jsonify({'error': 'No files provided'}), 400
    
    course = session.get('selected_course', 'Unknown')
    user = session.get('user', 'Unknown')
    files = request.files.getlist('files[]')
    upload_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    upload_hash = generate_hash(str(datetime.now()) + user)
    
    uploaded_files = []
    created_filepaths = []
    all_embedding_payloads = []
    course_code = None
    persisted_vector_ids = []
    previous_embeddings_by_chunk_id = {}

    con = sqlite3.connect(DATABASE)
    c = con.cursor()

    try:

        c.execute("SELECT course_code FROM courses WHERE name = ?", (course,))
        course_row = c.fetchone()

        if not course_row:

            return jsonify({'error': 'Invalid course selected'}), 400

        course_code = course_row[0]
        UPLOAD_CHUNK_INDEX[upload_hash] = []
        UPLOAD_EMBEDDING_INDEX[upload_hash] = []

        for file in files:

            if file and allowed_file(file.filename):

                filename = secure_filename(file.filename)
                # Guardar con timestamp para evitar sobrescrituras
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_')
                filepath = os.path.join(DOWNLOAD_DIR, timestamp + filename)
                file.save(filepath)
                created_filepaths.append(filepath)
                
                file_hash = generate_file_hash(filepath)
                
                # Verificar si el documento ya existe (mismo nombre)
                c.execute('SELECT id, doc_hash FROM documents WHERE course = ? AND filename = ?',
                          (course, filename))
                existing_doc = c.fetchone()
                
                if existing_doc:
                    # Es una versión nueva de un documento existente
                    document_id = existing_doc[0]
                    doc_hash = existing_doc[1]

                    previous_payloads = get_course_embeddings_by_metadata(
                        course_code,
                        {'doc_hash': doc_hash},
                    )

                    for payload in previous_payloads:
                        previous_embeddings_by_chunk_id[payload['chunk_id']] = payload

                    # Issue 14: borrar chunks obsoletos antes de indexar nueva versión.
                    delete_course_embeddings_by_metadata(
                        course_code,
                        {'doc_hash': doc_hash},
                    )
                    
                    # Obtener el próximo nómero de versión
                    c.execute('SELECT MAX(version_number) FROM document_versions WHERE document_id = ?',
                              (document_id,))
                    max_version = c.fetchone()[0] or 0
                    next_version = max_version + 1
                    
                    # Guardar en document_versions
                    c.execute('INSERT INTO document_versions VALUES (NULL, ?, ?, ?, ?, ?, ?, ?)',
                              (document_id, next_version, filename, file_hash, upload_date, filepath, user))
                    
                    # Actualizar el documento principal con la versión mós reciente
                    c.execute('UPDATE documents SET file_hash = ?, upload_date = ?, filepath = ?, uploaded_by = ? WHERE id = ?',
                              (file_hash, upload_date, filepath, user, document_id))
                
                else:
                    # Documento nuevo
                    doc_hash = generate_hash(str(datetime.now()) + filename + user)
                    c.execute('INSERT INTO documents VALUES (NULL, ?, ?, ?, ?, ?, ?, ?)',
                              (course, doc_hash, filename, file_hash, upload_date, filepath, user))
                    document_id = c.lastrowid
                    
                    # Insertar como versión 1
                    c.execute('INSERT INTO document_versions VALUES (NULL, ?, ?, ?, ?, ?, ?, ?)',
                              (document_id, 1, filename, file_hash, upload_date, filepath, user))

                chunk_records = process_uploaded_file(
                    filepath,
                    document_id = document_id,
                    doc_hash = doc_hash,
                    upload_hash = upload_hash,
                    course = course,
                    upload_date = upload_date,
                    filename = filename,
                    file_hash = file_hash,
                )

                register_chunk_records(upload_hash, chunk_records)

                embedding_payloads = build_embedding_payloads(
                    chunk_records,
                    provider = get_embedding_provider(),
                    embedding_dimension = EMBEDDING_DIMENSION,
                )

                register_embedding_payloads(upload_hash, embedding_payloads)
                all_embedding_payloads.extend(embedding_payloads)

                if not chunk_records:

                    app.logger.warning('No se generaron chunks para el archivo subido: %s', filename)
                
                uploaded_files.append({
                    'filename': filename,
                    'file_hash': file_hash,
                    'upload_date': upload_date,
                    'doc_hash': doc_hash,
                    'document_id': document_id
                })
        
        if not uploaded_files:

            clear_upload_staging(upload_hash)

            return jsonify({'error': 'Archivos no válidos proporcionados'}), 400

        persisted_vector_ids = upsert_course_embeddings(course_code, all_embedding_payloads)
        
        con.commit()

        return jsonify({
            'success': True,
            'upload_hash': upload_hash,
            'files': uploaded_files,
            'upload_date': upload_date
        })

    except (EmbeddingGenerationError, VectorStoreError) as e:

        con.rollback()

        if persisted_vector_ids and course_code:

            try:

                delete_course_embeddings(course_code, persisted_vector_ids)
            
            except VectorStoreError:

                app.logger.exception('Fallo al borrar embeddings persistidos para la carga: %s', upload_hash)

        if previous_embeddings_by_chunk_id and course_code:

            try:
                
                upsert_course_embeddings(course_code, list(previous_embeddings_by_chunk_id.values()))
            
            except VectorStoreError:

                app.logger.exception('Fallo al restaurar embeddings anteriores después de la reversión de carga: %s', upload_hash)

        clear_upload_staging(upload_hash)
        remove_uploaded_files(created_filepaths)
        app.logger.exception('Error durante la generación de embeddings o almacenamiento vectorial para la carga: %s', upload_hash)
        return jsonify({'error': str(e)}), 500

    except Exception as e:

        con.rollback()

        if persisted_vector_ids and course_code:

            try:

                delete_course_embeddings(course_code, persisted_vector_ids)
            
            except VectorStoreError:

                app.logger.exception('Fallo al borrar embeddings persistidos para la carga: %s', upload_hash)

        if previous_embeddings_by_chunk_id and course_code:

            try:

                upsert_course_embeddings(course_code, list(previous_embeddings_by_chunk_id.values()))
            
            except VectorStoreError:

                app.logger.exception('Fallo al restaurar embeddings anteriores después de la reversión de carga: %s', upload_hash)

        clear_upload_staging(upload_hash)
        remove_uploaded_files(created_filepaths)
        return jsonify({'error': str(e)}), 500

    finally:

        con.close()


# Obtención de los ODucmentos Globales de un Curso

@app.route('/api/documents/<course>')

@login_required

def get_documents(course):

    """Obtiene documentos del curso (globales)"""

    con = sqlite3.connect(DATABASE)
    c = con.cursor()
    c.execute('SELECT id, doc_hash, filename, file_hash, upload_date, uploaded_by FROM documents WHERE course = ? ORDER BY upload_date DESC',
              (course,))
    
    docs = c.fetchall()
    con.close()
    
    documents = []

    for doc_id, doc_hash, filename, file_hash, upload_date, uploaded_by in docs:

        documents.append({
            'id': doc_id,
            'doc_hash': doc_hash,
            'filename': filename,
            'file_hash': file_hash,
            'upload_date': upload_date,
            'uploaded_by': uploaded_by
        })
    
    return jsonify(documents)

# Consulta Semántica por Curso con Recuperación Top-N

@app.route('/api/query', methods = ['POST'])

@login_required

def query_course_documents():
    """Consulta semántica, keyword (BM25) o híbrida según la estrategia del curso."""

    data = request.json or {}
    query_text = (data.get('query') or data.get('question') or '').strip()

    if not query_text:
        return jsonify({'error': 'La consulta es requerida'}), 400

    raw_course_id = data.get('course_id')
    course_name = (data.get('course') or data.get('course_name') or '').strip()
    course_code = data.get('course_code', '')
    raw_top_n = data.get('top_n', QUERY_TOP_N_DEFAULT)

    try:
        top_n = int(raw_top_n)
    except (TypeError, ValueError):
        return jsonify({'error': 'top_n debe ser un entero positivo'}), 400

    if top_n < 1:
        return jsonify({'error': 'top_n debe ser mayor o igual a 1'}), 400

    top_n = min(top_n, QUERY_TOP_N_MAX)

    course_id = None
    if raw_course_id is not None and str(raw_course_id).strip() != '':
        try:
            course_id = int(raw_course_id)
        except (TypeError, ValueError):
            return jsonify({'error': 'course_id debe ser numérico'}), 400

    con = sqlite3.connect(DATABASE)
    c = con.cursor()

    course_context = resolve_course_context(
        c,
        course_id=course_id,
        course_name=course_name,
        course_code=course_code,
    )

    if not course_context:
        con.close()
        return jsonify({'error': 'Curso no encontrado'}), 404

    resolved_course_id, resolved_course_name, resolved_course_code = course_context

    # Obtener estrategia de búsqueda del curso
    c.execute("SELECT search_strategy FROM courses WHERE id = ?", (resolved_course_id,))
    strategy_row = c.fetchone()
    search_strategy = strategy_row[0] if strategy_row and strategy_row[0] else 'semantic'
    con.close()

    try:
        query_embedding = get_embedding_provider().embed_texts([query_text])[0]

        # --- Búsqueda Semántica ---
        semantic_results = query_course_embeddings(
            resolved_course_code,
            query_embedding,
            top_n=top_n,
        )

        if search_strategy == 'semantic':
            ranked_results = semantic_results

        elif search_strategy == 'keyword':
            all_chunks = query_course_embeddings(
                resolved_course_code,
                query_embedding,
                top_n=QUERY_TOP_N_MAX,
            )
            ranked_results = bm25_search(query_text, all_chunks, top_n=top_n)

        elif search_strategy == 'hybrid':
            all_chunks = query_course_embeddings(
                resolved_course_code,
                query_embedding,
                top_n=QUERY_TOP_N_MAX,
            )
            keyword_results = bm25_search(query_text, all_chunks, top_n=top_n)
            ranked_results = reciprocal_rank_fusion(semantic_results, keyword_results, top_n=top_n)

        else:
            ranked_results = semantic_results

    except (EmbeddingGenerationError, VectorStoreError) as e:
        app.logger.exception('Fallo en la consulta para el curso %s', resolved_course_code)
        return jsonify({'error': str(e)}), 500

    # Guardar métricas sin bloquear la respuesta
    save_retrieval_metrics(
        course_name=resolved_course_name,
        course_code=resolved_course_code,
        query_text=query_text,
        strategy=search_strategy,
        top_n=top_n,
        results=ranked_results,
        user=session.get('user', 'unknown'),
    )

    response_results = []
    for result in ranked_results:
        metadata = result.get('metadata', {})
        response_results.append({
            'chunk_text': result.get('text', ''),
            'score': result.get('score', 0.0),
            'source': {
                'filename': metadata.get('filename'),
                'upload_date': metadata.get('upload_date'),
            },
            'metadata': {
                'doc_hash': metadata.get('doc_hash'),
                'upload_hash': metadata.get('upload_hash'),
                'chunk_index': metadata.get('chunk_index'),
                'course_code': metadata.get('course_code'),
            },
        })

    return jsonify({
        'course': {
            'id': resolved_course_id,
            'name': resolved_course_name,
            'course_code': resolved_course_code,
            'search_strategy': search_strategy,
        },
        'query': query_text,
        'top_n': top_n,
        'results': response_results,
    }), 200


# Obtener comentarios hechos por los estudiantes

@app.route('/api/comments/<int:document_id>')

@login_required

def get_comments(document_id):

    """Obtiene comentarios de un documento (solo si es admin/profesor)"""

    user = session.get('user')
    
    con = sqlite3.connect(DATABASE)
    c = con.cursor()
    
    # Verificar si el usuario es admin/profesor

    c.execute("SELECT role FROM users WHERE username = ?", (user,))
    role_result = c.fetchone()
    
    if not role_result or role_result[0] not in ['admin', 'profesor']:

        con.close()

        return jsonify({'error': 'Acceso denegado'}), 403
    
    # Obtener comentarios del documento

    c.execute('SELECT id, student_name, comment_text, comment_date FROM comments WHERE document_id = ? ORDER BY comment_date DESC',
              (document_id,))
    
    comments = c.fetchall()
    con.close()
    
    comment_list = []

    for comment_id, student_name, comment_text, comment_date in comments:

        comment_list.append({
            'id': comment_id,
            'student_name': student_name,
            'comment_text': comment_text,
            'comment_date': comment_date
        })
    
    return jsonify(comment_list)


# Añadir comentarios

@app.route('/api/add-comment', methods = ['POST'])

@login_required

def add_comment():

    """Agrega un comentario a un documento (solo para estudiantes)"""

    data = request.json
    document_id = data.get('document_id')
    comment_text = data.get('comment_text', '').strip()
    student_name = session.get('user')
    
    if not comment_text:

        return jsonify({'error': 'El comentario no puede estar vacío'}), 400
    
    if len(comment_text) > 500:

        return jsonify({'error': 'El comentario no puede exceder 500 caracteres'}), 400
    
    con = sqlite3.connect(DATABASE)
    c = con.cursor()
    
    # Verificar que el documento existe

    c.execute("SELECT id FROM documents WHERE id = ?", (document_id,))

    if not c.fetchone():

        con.close()

        return jsonify({'error': 'Documento no encontrado'}), 404
    
    comment_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    try:

        c.execute('INSERT INTO comments VALUES (NULL, ?, ?, ?, ?)',
                  (document_id, student_name, comment_text, comment_date))
        con.commit()
        con.close()

        return jsonify({'success': True, 'message': 'Comentario agregado correctamente'}), 201
    except Exception as e:

        con.close()

        return jsonify({'error': str(e)}), 500


# Eliminar un documento subido

@app.route('/api/delete-document/<int:document_id>', methods = ['DELETE'])

@admin_required

def delete_document(document_id):

    """Elimina documento, versiones y embeddings asociados."""

    con = sqlite3.connect(DATABASE)
    c = con.cursor()
    
    # Obtener datos necesarios para sincronizar SQLite + Vector Store.

    c.execute(
        '''SELECT d.filepath, d.doc_hash, c.course_code
           FROM documents d
           JOIN courses c ON c.name = d.course
           WHERE d.id = ?''',
        (document_id,),
    )

    result = c.fetchone()
    
    if not result:

        con.close()

        return jsonify({'error': 'Documento no encontrado'}), 404
    
    filepath = result[0]
    doc_hash = result[1]
    course_code = result[2]

    c.execute("SELECT filepath FROM document_versions WHERE document_id = ?", (document_id,))
    version_filepaths = [row[0] for row in c.fetchall()]
    filepaths_to_remove = list(dict.fromkeys([filepath] + version_filepaths))
    
    try:

        # Issue 14: primero sincronizar Vector Store; si falla, no se confirma SQLite.

        delete_course_embeddings_by_metadata(course_code, {'doc_hash': doc_hash})
        
        # Eliminar comentarios

        c.execute("DELETE FROM comments WHERE document_id = ?", (document_id,))

        # Eliminar versiones

        c.execute("DELETE FROM document_versions WHERE document_id = ?", (document_id,))
        
        # Eliminar documento

        c.execute("DELETE FROM documents WHERE id = ?", (document_id,))
        
        con.commit()
        con.close()

        # Eliminar archivos fósicos fuera de la transacción SQL.
        
        for existing_filepath in filepaths_to_remove:

            if not existing_filepath:

                continue

            try:

                if os.path.exists(existing_filepath):

                    os.remove(existing_filepath)
            
            except OSError:

                app.logger.warning('Error al eliminar el archivo: %s', existing_filepath)

        return jsonify({'success': True, 'message': 'Documento eliminado correctamente'}), 200
    
    except Exception as e:

        con.rollback()

        con.close()

        return jsonify({'error': str(e)}), 500


# Historico

@app.route('/api/history')

def get_history():

    session_id = session.get('session_id', '')
    course = session.get('selected_course', '')
    
    con = sqlite3.connect(DATABASE)
    c = con.cursor()
    c.execute('SELECT upload_hash, upload_date, files_json FROM uploads WHERE session_id = ? AND course = ? ORDER BY upload_date DESC',
              (session_id, course))
    
    uploads = c.fetchall()
    con.close()
    
    history = []

    for upload_hash, upload_date, files_json in uploads:

        files = json.loads(files_json)
        history.append({
            'upload_hash': upload_hash,
            'upload_date': upload_date,
            'files': files,
            'file_count': len(files)
        })
    
    return jsonify(history)


# Cursos o shelves

@app.route('/api/courses')

@login_required

def get_courses():

    con = sqlite3.connect(DATABASE)
    c = con.cursor()
    c.execute("""SELECT id, name, course_code, responsible_teacher, status
                 FROM courses
                 ORDER BY name""")
    courses = []

    for course_id, name, course_code, responsible_teacher, status in c.fetchall():

        courses.append({
            'id': course_id,
            'name': name,
            'course_code': course_code,
            'responsible_teacher': responsible_teacher,
            'status': status
        })

    con.close()

    return jsonify(courses)


@app.route('/api/teachers')

@admin_required

def get_teachers():

    con = sqlite3.connect(DATABASE)
    c = con.cursor()
    c.execute("SELECT username FROM users WHERE role = 'profesor' ORDER BY username")
    teachers = [row[0] for row in c.fetchall()]
    con.close()

    return jsonify(teachers)


# Creación de curso

@app.route('/api/create-course', methods = ['POST'])

@admin_required

def create_course():

    data = request.json or {}
    name = data.get('name', '').strip()
    course_code = normalize_course_code(data.get('course_code', ''))
    responsible_teacher = data.get('responsible_teacher', '').strip()
    status = data.get('status', '').strip().lower()
    
    # Validaciones

    if not name:

        return jsonify({'error': 'El nombre del curso es requerido'}), 400
    
    if len(name) > 100:

        return jsonify({'error': 'El nombre no puede exceder 100 caracteres'}), 400

    if not course_code:

        return jsonify({'error': 'El código del curso es requerido'}), 400

    if len(course_code) > 30:

        return jsonify({'error': 'El código del curso no puede exceder 30 caracteres'}), 400

    if not responsible_teacher:

        return jsonify({'error': 'El docente responsable es requerido'}), 400

    if status not in VALID_SHELF_STATUS:

        return jsonify({'error': 'El estado debe ser \"borrador\" o \"publicado\"'}), 400
    
    con = sqlite3.connect(DATABASE)
    c = con.cursor()

    # Verificar docente responsable

    c.execute("SELECT role FROM users WHERE username = ?", (responsible_teacher,))
    teacher_role = c.fetchone()

    if not teacher_role or teacher_role[0] != 'profesor':
        con.close()
        return jsonify({'error': 'El docente responsable no existe o no tiene rol profesor'}), 400
    
    # Verificar duplicados

    c.execute("SELECT COUNT(*) FROM courses WHERE name = ?", (name,))
    if c.fetchone()[0] > 0:
        con.close()
        return jsonify({'error': 'Este curso ya existe'}), 400

    c.execute("SELECT COUNT(*) FROM courses WHERE course_code = ? COLLATE NOCASE", (course_code,))
    if c.fetchone()[0] > 0:
        con.close()
        return jsonify({'error': 'Este código de curso ya existe'}), 400
    
    # Crear curso

    try:

        created_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        c.execute('''INSERT INTO courses
                     (name, course_code, responsible_teacher, status, created_by, created_date)
                     VALUES (?, ?, ?, ?, ?, ?)''',
                  (name, course_code, responsible_teacher, status, session.get('user'), created_date))

        new_course_id = c.lastrowid
        con.commit()
        con.close()

        return jsonify({
            'success': True,
            'message': f'Curso "{name}" creado exitosamente',
            'course': {
                'id': new_course_id,
                'name': name,
                'course_code': course_code,
                'responsible_teacher': responsible_teacher,
                'status': status
            }
        }), 201
    
    except sqlite3.IntegrityError as e:

        con.close()

        return jsonify({'error': f'Error de integridad en base de datos: {str(e)}'}), 400
    
    except Exception as e:

        con.close()

        return jsonify({'error': str(e)}), 500


# Eliminar curso

@app.route('/api/delete-course/<course>', methods = ['DELETE'])

@login_required

def delete_course(course):

    user = session.get('user')
    con = sqlite3.connect(DATABASE)
    c = con.cursor()
    
    # Verificar que solo ADMIN puede eliminar

    c.execute("SELECT role FROM users WHERE username = ?", (user,))
    role_result = c.fetchone()
    
    if not role_result or role_result[0] != 'admin':

        con.close()

        return jsonify({'error': 'Solo administradores pueden eliminar cursos'}), 403
    
    # Verificar que existe el curso

    c.execute("SELECT id FROM courses WHERE name = ?", (course,))
    course_result = c.fetchone()
    
    if not course_result:

        con.close()

        return jsonify({'error': 'El curso no existe'}), 404
    
    try:

        # Obtener todos los hashes de subida para este curso

        c.execute("SELECT upload_hash FROM uploads WHERE course = ?", (course,))
        uploads = c.fetchall()
        
        # Eliminar archivos del sistema

        for upload in uploads:

            upload_hash = upload[0]
            c.execute("SELECT filepath FROM files WHERE upload_hash = ?", (upload_hash,))
            files = c.fetchall()
            
            for file in files:

                filepath = file[0]

                try:

                    if os.path.exists(filepath):

                        os.remove(filepath)

                except:

                    pass
        
        # Eliminar registros de archivos

        c.execute("DELETE FROM files WHERE upload_hash IN (SELECT upload_hash FROM uploads WHERE course = ?)", (course,))
        
        # Eliminar registros de subidas

        c.execute("DELETE FROM uploads WHERE course = ?", (course,))
        
        # Eliminar asignaciones de profesores

        c.execute("DELETE FROM course_professors WHERE course_name = ?", (course,))
        
        # Eliminar estudiantes del curso

        c.execute("DELETE FROM course_students WHERE course_name = ?", (course,))
        
        # Eliminar documentos y versiones

        c.execute("DELETE FROM document_versions WHERE document_id IN (SELECT id FROM documents WHERE course = ?)", (course,))
        c.execute("DELETE FROM comments WHERE document_id IN (SELECT id FROM documents WHERE course = ?)", (course,))
        c.execute("DELETE FROM documents WHERE course = ?", (course,))
        
        # Eliminar curso

        c.execute("DELETE FROM courses WHERE name = ?", (course,))
        
        con.commit()
        con.close()

        return jsonify({'success': True, 'message': f'Curso "{course}" eliminado con todos sus datos'}), 200
    
    except Exception as e:

        con.close()

        return jsonify({'error': str(e)}), 500


# ----- NUEVAS RUTAS PARA VERSIONADO Y GESTIÓN -----

# Obtener historial de versiones de un documento

@app.route('/api/document-history/<filename>')

@login_required

# Ontener el historial de versiones de un documento por su nombre (asumiendo que el nombre es único dentro del curso)

def get_document_history(filename):

    """Obtiene el historial de versiones de un documento"""

    course = session.get('selected_course', '')
    
    con = sqlite3.connect(DATABASE)
    c = con.cursor()
    
    # Obtener el documento

    c.execute('SELECT id FROM documents WHERE filename = ? AND course = ?',
              (filename, course))
    doc_result = c.fetchone()
    
    if not doc_result:

        con.close()

        return jsonify({'error': 'Documento no encontrado'}), 404
    
    document_id = doc_result[0]
    
    # Obtener todas las versiones

    c.execute('''SELECT id, version_number, filename, file_hash, upload_date, uploaded_by 
                 FROM document_versions 
                 WHERE document_id = ? 
                 ORDER BY version_number DESC''',
              (document_id,))
    
    versions = []

    for version_id, version_num, fname, fhash, upload_date, uploaded_by in c.fetchall():
        versions.append({
            'version_id': version_id,
            'version_number': version_num,
            'filename': fname,
            'file_hash': fhash,
            'upload_date': upload_date,
            'uploaded_by': uploaded_by
        })
    
    con.close()
    
    return jsonify({
        'document_id': document_id,
        'filename': filename,
        'versions': versions
    })


# Obtener diff entre dos versiones

@app.route('/api/document-diff/<int:document_id>/<int:version1>/<int:version2>')

@login_required

# Obtener el diff entre dos versiones de un documento dado su ID y los números de versión

def get_document_diff(document_id, version1, version2):

    """Compara dos versiones de un documento"""

    con = sqlite3.connect(DATABASE)
    c = con.cursor()
    
    # Obtener filepaths de ambas versiones
    c.execute('SELECT filepath FROM document_versions WHERE document_id = ? AND version_number = ?',
              (document_id, version1))
    file1_result = c.fetchone()
    
    c.execute('SELECT filepath FROM document_versions WHERE document_id = ? AND version_number = ?',
              (document_id, version2))
    file2_result = c.fetchone()
    
    con.close()
    
    if not file1_result or not file2_result:

        return jsonify({'error': 'Una o ambas versiones no existen'}), 404
    
    filepath1 = file1_result[0]
    filepath2 = file2_result[0]
    
    # Comparar archivos
    diff = compare_file_versions(filepath1, filepath2)
    
    return jsonify({
        'version1': version1,
        'version2': version2,
        'diff': diff
    })


# Descargar un archivo especófico

@app.route('/api/download/<int:document_id>')

@login_required

def download_document(document_id):

    """Descarga la versión mós reciente de un documento"""

    con = sqlite3.connect(DATABASE)
    c = con.cursor()
    
    # Obtener documento y verificar acceso
    c.execute('SELECT course, filename FROM documents WHERE id = ?',
              (document_id,))
    doc_result = c.fetchone()
    
    if not doc_result:

        con.close()
        
        return jsonify({'error': 'Documento no encontrado'}), 404
    
    course_name = doc_result[0]
    
    # Verificar que el usuario tenga acceso al 
    
    user = session.get('user')
    c.execute("SELECT role FROM users WHERE username = ?", (user,))
    role_result = c.fetchone()
    
    con.close()
    
    if not role_result:

        return jsonify({'error': 'Usuario no autorizado'}), 403
    
    # Obtener versión mós reciente

    con = sqlite3.connect(DATABASE)
    c = con.cursor()
    c.execute('''SELECT filepath, filename FROM document_versions 
                 WHERE document_id = ? 
                 ORDER BY version_number DESC LIMIT 1''',
              (document_id,))
    version_result = c.fetchone()
    con.close()
    
    if not version_result:

        return jsonify({'error': 'Archivo no disponible'}), 404
    
    filepath = version_result[0]
    filename = version_result[1]
    
    if not os.path.exists(filepath):

        return jsonify({'error': 'Archivo no encontrado en servidor'}), 404
    
    return send_file(filepath, as_attachment=True, download_name=filename)


# Descargar una versión especófica

@app.route('/api/download-version/<int:version_id>')

@login_required

def download_version(version_id):

    """Descarga una versión especófica de un documento"""

    con = sqlite3.connect(DATABASE)
    c = con.cursor()
    
    c.execute('SELECT filepath, filename FROM document_versions WHERE id = ?',
              (version_id,))
    result = c.fetchone()
    con.close()
    
    if not result:
        return jsonify({'error': 'Versión no encontrada'}), 404
    
    filepath = result[0]
    filename = result[1]
    
    if not os.path.exists(filepath):
        return jsonify({'error': 'Archivo no encontrado'}), 404
    
    return send_file(filepath, as_attachment=True, download_name=f"v{version_id}_{filename}")


# ----- RUTAS PARA GESTIóN DE PROFESORES (ADMIN) -----

# Obtener profesores asignados a un curso

@app.route('/api/course-professors/<course>')

@login_required

def get_course_professors(course):

    """Obtiene los profesores asignados a un curso (solo admin)"""

    user = session.get('user')
    con = sqlite3.connect(DATABASE)
    c = con.cursor()
    
    # Verificar que es admin

    c.execute("SELECT role FROM users WHERE username = ?", (user,))
    role_result = c.fetchone()
    
    if not role_result or role_result[0] != 'admin':

        con.close()

        return jsonify({'error': 'Acceso denegado'}), 403
    
    # Obtener profesores

    c.execute('''SELECT professor_username, assigned_date 
                 FROM course_professors 
                 WHERE course_name = ? 
                 ORDER BY assigned_date ASC''',
              (course,))
    
    professors = []
    for prof_username, assigned_date in c.fetchall():
        professors.append({
            'username': prof_username,
            'assigned_date': assigned_date
        })
    
    con.close()
    
    return jsonify(professors)


# Asignar profesor a un curso

@app.route('/api/assign-professor', methods=['POST'])

@login_required

def assign_professor():

    """Asigna un profesor a un curso (solo admin)"""

    user = session.get('user')
    con = sqlite3.connect(DATABASE)
    c = con.cursor()
    
    # Verificar que es admin
    c.execute("SELECT role FROM users WHERE username = ?", (user,))
    role_result = c.fetchone()
    
    if not role_result or role_result[0] != 'admin':
        con.close()
        return jsonify({'error': 'Acceso denegado'}), 403
    
    data = request.json or {}
    course_name = data.get('course_name', '').strip()
    professor_username = data.get('professor_username', '').strip()
    
    if not course_name or not professor_username:
        con.close()
        return jsonify({'error': 'Datos incompletos'}), 400
    
    # Verificar que el curso existe
    c.execute("SELECT id FROM courses WHERE name = ?", (course_name,))
    if not c.fetchone():
        con.close()
        return jsonify({'error': 'Curso no existe'}), 404
    
    # Verificar que el profesor existe y es profesor
    c.execute("SELECT role FROM users WHERE username = ?", (professor_username,))
    prof_role = c.fetchone()
    
    if not prof_role or prof_role[0] != 'profesor':
        con.close()
        return jsonify({'error': 'Usuario no es profesor'}), 400
    
    # Verificar que no estó ya asignado
    c.execute("SELECT id FROM course_professors WHERE course_name = ? AND professor_username = ?",
              (course_name, professor_username))
    if c.fetchone():
        con.close()
        return jsonify({'error': 'Profesor ya asignado a este curso'}), 400
    
    try:
        assigned_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        c.execute('INSERT INTO course_professors VALUES (NULL, ?, ?, ?)',
                  (course_name, professor_username, assigned_date))
        con.commit()
        con.close()
        
        return jsonify({'success': True, 'message': 'Profesor asignado correctamente'}), 201
    except Exception as e:
        con.close()
        return jsonify({'error': str(e)}), 500


# Desasignar profesor de un curso

@app.route('/api/unassign-professor', methods=['POST'])

@login_required

def unassign_professor():

    """Desasigna un profesor de un curso (solo admin)"""

    user = session.get('user')
    con = sqlite3.connect(DATABASE)
    c = con.cursor()
    
    # Verificar que es admin
    c.execute("SELECT role FROM users WHERE username = ?", (user,))
    role_result = c.fetchone()
    
    if not role_result or role_result[0] != 'admin':
        con.close()
        return jsonify({'error': 'Acceso denegado'}), 403
    
    data = request.json or {}
    course_name = data.get('course_name', '').strip()
    professor_username = data.get('professor_username', '').strip()
    
    if not course_name or not professor_username:
        con.close()
        return jsonify({'error': 'Datos incompletos'}), 400
    
    try:
        c.execute('DELETE FROM course_professors WHERE course_name = ? AND professor_username = ?',
                  (course_name, professor_username))
        con.commit()
        con.close()
        
        return jsonify({'success': True, 'message': 'Profesor desasignado correctamente'}), 200
    except Exception as e:
        con.close()
        return jsonify({'error': str(e)}), 500


# ----- RUTAS PARA GESTIóN DE ESTUDIANTES (PROFESOR) -----

# Obtener estudiantes en un curso

@app.route('/api/course-students/<course>')

@login_required

def get_course_students(course):

    """Obtiene los estudiantes inscritos en un curso"""

    user = session.get('user')
    con = sqlite3.connect(DATABASE)
    c = con.cursor()
    
    # Verificar permisos (profesor del curso o admin)
    c.execute("SELECT role FROM users WHERE username = ?", (user,))
    role_result = c.fetchone()
    
    if not role_result:
        con.close()
        return jsonify({'error': 'Usuario no vólido'}), 403
    
    role = role_result[0]
    
    if role == 'profesor':
        if not professor_can_manage_course(c, course, user):
            con.close()
            return jsonify({'error': 'No es profesor de este curso'}), 403
    
    elif role != 'admin':
        con.close()
        return jsonify({'error': 'Acceso denegado'}), 403
    
    # Obtener estudiantes
    c.execute('''SELECT student_username, added_date 
                 FROM course_students 
                 WHERE course_name = ? 
                 ORDER BY added_date ASC''',
              (course,))
    
    students = []
    for student_username, added_date in c.fetchall():
        students.append({
            'username': student_username,
            'added_date': added_date
        })
    
    con.close()
    
    return jsonify(students)


# Obtener estudiantes disponibles (no inscritos)

@app.route('/api/available-students/<course>')

@login_required

def get_available_students(course):

    """Obtiene los estudiantes que puede agregar un profesor"""

    user = session.get('user')
    con = sqlite3.connect(DATABASE)
    c = con.cursor()
    
    # Verificar que es profesor del curso
    c.execute("SELECT role FROM users WHERE username = ?", (user,))
    role_result = c.fetchone()
    
    if not role_result:
        con.close()
        return jsonify({'error': 'Usuario no vólido'}), 403
    
    role = role_result[0]
    
    if role == 'profesor':
        if not professor_can_manage_course(c, course, user):
            con.close()
            return jsonify({'error': 'No es profesor de este curso'}), 403
    
    elif role != 'admin':
        con.close()
        return jsonify({'error': 'Acceso denegado'}), 403
    
    # Obtener estudiantes registrados pero no en este curso
    c.execute('''SELECT username FROM users 
                 WHERE role = 'estudiante' 
                 AND username NOT IN (
                     SELECT student_username FROM course_students WHERE course_name = ?
                 )
                 ORDER BY username ASC''',
              (course,))
    
    students = [row[0] for row in c.fetchall()]
    con.close()
    
    return jsonify(students)


# Agregar estudiante a un curso

@app.route('/api/add-student', methods=['POST'])

@login_required

def add_student():

    """Agrega un estudiante a un curso"""

    user = session.get('user')
    con = sqlite3.connect(DATABASE)
    c = con.cursor()
    
    # Verificar permisos
    c.execute("SELECT role FROM users WHERE username = ?", (user,))
    role_result = c.fetchone()
    
    if not role_result:
        con.close()
        return jsonify({'error': 'Usuario no vólido'}), 403
    
    role = role_result[0]
    
    data = request.json or {}
    course_name = data.get('course_name', '').strip()
    student_username = data.get('student_username', '').strip()
    
    if not course_name or not student_username:
        con.close()
        return jsonify({'error': 'Datos incompletos'}), 400
    
    if role == 'profesor':
        if not professor_can_manage_course(c, course_name, user):
            con.close()
            return jsonify({'error': 'No es profesor de este curso'}), 403
    
    elif role != 'admin':
        con.close()
        return jsonify({'error': 'Acceso denegado'}), 403
    
    # Verificar que el estudiante existe
    c.execute("SELECT role FROM users WHERE username = ?", (student_username,))
    student_role = c.fetchone()
    
    if not student_role or student_role[0] != 'estudiante':
        con.close()
        return jsonify({'error': 'Usuario no es estudiante'}), 400
    
    # Verificar que no estó ya inscrito
    c.execute("SELECT id FROM course_students WHERE course_name = ? AND student_username = ?",
              (course_name, student_username))
    if c.fetchone():
        con.close()
        return jsonify({'error': 'Estudiante ya inscrito en este curso'}), 400
    
    try:
        added_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        c.execute('INSERT INTO course_students VALUES (NULL, ?, ?, ?)',
                  (course_name, student_username, added_date))
        con.commit()
        con.close()
        
        return jsonify({'success': True, 'message': 'Estudiante agregado correctamente'}), 201
    except Exception as e:
        con.close()
        return jsonify({'error': str(e)}), 500


# Eliminar estudiante de un curso

@app.route('/api/remove-student', methods=['POST'])

@login_required

def remove_student():

    """Elimina un estudiante de un curso"""

    user = session.get('user')
    con = sqlite3.connect(DATABASE)
    c = con.cursor()
    
    # Verificar permisos
    c.execute("SELECT role FROM users WHERE username = ?", (user,))
    role_result = c.fetchone()
    
    if not role_result:

        con.close()

        return jsonify({'error': 'Usuario no vólido'}), 403
    
    role = role_result[0]
    
    data = request.json or {}
    course_name = data.get('course_name', '').strip()
    student_username = data.get('student_username', '').strip()
    
    if not course_name or not student_username:

        con.close()

        return jsonify({'error': 'Datos incompletos'}), 400
    
    if role == 'profesor':

        if not professor_can_manage_course(c, course_name, user):
            
            con.close()

            return jsonify({'error': 'No es profesor de este curso'}), 403
    
    elif role != 'admin':

        con.close()

        return jsonify({'error': 'Acceso denegado'}), 403
    
    try:
        
        c.execute('DELETE FROM course_students WHERE course_name = ? AND student_username = ?',
                  (course_name, student_username))
        con.commit()
        con.close()
        
        return jsonify({'success': True, 'message': 'Estudiante removido correctamente'}), 200
    
    except Exception as e:

        con.close()

        return jsonify({'error': str(e)}), 500
    
# Actualizar estrategia de búsqueda de un curso
@app.route('/api/course-search-strategy/<int:course_id>', methods=['PUT'])
@admin_required

def update_search_strategy(course_id):
    """Actualiza la estrategia de búsqueda de un curso (solo admin/profesor)."""

    data = request.json or {}
    strategy = data.get('search_strategy', '').strip().lower()

    if strategy not in ('semantic', 'keyword', 'hybrid'):
        return jsonify({'error': 'Estrategia inválida. Use: semantic, keyword o hybrid'}), 400

    con = sqlite3.connect(DATABASE)
    c = con.cursor()

    c.execute("SELECT id FROM courses WHERE id = ?", (course_id,))
    if not c.fetchone():
        con.close()
        return jsonify({'error': 'Curso no encontrado'}), 404

    try:
        c.execute("UPDATE courses SET search_strategy = ? WHERE id = ?", (strategy, course_id))
        con.commit()
        con.close()
        return jsonify({'success': True, 'search_strategy': strategy}), 200
    except Exception as e:
        con.close()
        return jsonify({'error': str(e)}), 500

# Consultar métricas de recuperación
@app.route('/api/retrieval-metrics', methods=['GET'])
@admin_required
def get_retrieval_metrics():
    """Retorna las métricas de consultas realizadas (solo admin/profesor)."""

    course_code = request.args.get('course_code', '').strip()
    limit = min(int(request.args.get('limit', 50)), 200)

    con = sqlite3.connect(DATABASE)
    c = con.cursor()

    if course_code:
        c.execute('''SELECT timestamp, course_name, course_code, query_text,
                            search_strategy, top_n, returned_doc_ids, scores, user
                     FROM retrieval_metrics
                     WHERE course_code = ?
                     ORDER BY timestamp DESC LIMIT ?''', (course_code, limit))
    else:
        c.execute('''SELECT timestamp, course_name, course_code, query_text,
                            search_strategy, top_n, returned_doc_ids, scores, user
                     FROM retrieval_metrics
                     ORDER BY timestamp DESC LIMIT ?''', (limit,))

    rows = c.fetchall()
    con.close()

    metrics = []
    for row in rows:
        metrics.append({
            'timestamp': row[0],
            'course_name': row[1],
            'course_code': row[2],
            'query_text': row[3],
            'search_strategy': row[4],
            'top_n': row[5],
            'returned_doc_ids': json.loads(row[6]),
            'scores': json.loads(row[7]),
            'user': row[8],
        })

    return jsonify({'total': len(metrics), 'metrics': metrics}), 200

# HITL Dashboard - Página de revisión de sugerencias

@app.route('/review/<course>')
@admin_required
def review_page(course):
    """Renderiza el dashboard HITL de revisión de sugerencias del agente."""

    con = sqlite3.connect(DATABASE)
    c = con.cursor()
    c.execute("SELECT id FROM courses WHERE name = ?", (course,))
    result = c.fetchone()
    con.close()

    if not result:
        return redirect(url_for('index'))

    course_id = result[0]
    return render_template('review.html', course=course, course_id=course_id)


# HITL Dashboard - Sugerencias del Agente

@app.route('/api/agent/suggestions', methods=['GET'])
@admin_required
def get_agent_suggestions():
    """Lista sugerencias pendientes del agente para un curso."""

    course_id = request.args.get('course_id', '').strip()

    if not course_id:
        return jsonify({'error': 'El parámetro course_id es requerido'}), 400

    try:
        course_id_int = int(course_id)
    except ValueError:
        return jsonify({'error': 'course_id debe ser un número entero'}), 400

    suggestions = list_agent_suggestions(course_id_int, estado='pendiente')
    return jsonify(suggestions), 200


@app.route('/api/agent/suggestions/<int:suggestion_id>/resolve', methods=['POST'])
@admin_required
def resolve_agent_suggestion(suggestion_id):
    """Aprueba o rechaza una sugerencia del agente."""

    data = request.get_json(silent=True) or {}
    estado = str(data.get('estado', '')).strip()

    if estado not in ('aprobado', 'rechazado'):
        return jsonify({'error': "El campo 'estado' debe ser 'aprobado' o 'rechazado'"}), 400

    reviewed_by = session['user']
    updated = update_agent_suggestion_status(suggestion_id, estado, reviewed_by)

    if not updated:
        return jsonify({'error': 'Sugerencia no encontrada'}), 404

    return jsonify({'message': f'Sugerencia {suggestion_id} marcada como {estado}'}), 200


# ── Proveedor de embeddings reutilizable para el endpoint de chat ──────────

_AGENT_CHAT_EMBEDDING_PROVIDER = None


def _get_agent_chat_embedding_provider():
    """Inicializa y reutiliza el proveedor de embeddings para el chat del agente."""

    global _AGENT_CHAT_EMBEDDING_PROVIDER

    if _AGENT_CHAT_EMBEDDING_PROVIDER is None:
        _AGENT_CHAT_EMBEDDING_PROVIDER = LocalSentenceTransformerProvider(
            model_name=os.environ.get('EMBEDDING_MODEL_NAME', DEFAULT_EMBEDDING_MODEL_NAME),
            batch_size=int(os.environ.get('EMBEDDING_BATCH_SIZE', DEFAULT_EMBEDDING_BATCH_SIZE)),
            device=os.environ.get('EMBEDDING_DEVICE', DEFAULT_EMBEDDING_DEVICE),
            embedding_dimension=DEFAULT_EMBEDDING_DIMENSION,
        )

    return _AGENT_CHAT_EMBEDDING_PROVIDER


_AGENT_CHAT_SIMILARITY_THRESHOLD = 0.3


@app.route('/api/agent/chat', methods=['POST'])
@admin_required
def agent_chat():
    """Chat con el agente de curaduría académica sobre los documentos del curso."""

    data = request.get_json(silent=True) or {}
    message = str(data.get('message', '') or '').strip()
    course_id_raw = data.get('course_id')
    conversation_id = str(data.get('conversation_id', '') or '').strip()

    if not message:
        return jsonify({'error': "El campo 'message' es requerido"}), 400
    if course_id_raw is None:
        return jsonify({'error': "El campo 'course_id' es requerido"}), 400
    if not conversation_id:
        return jsonify({'error': "El campo 'conversation_id' es requerido"}), 400

    try:
        course_id = int(course_id_raw)
    except (TypeError, ValueError):
        return jsonify({'error': "'course_id' debe ser un número entero"}), 400

    # Resolver course_code desde la BD
    con = sqlite3.connect(DATABASE)
    c = con.cursor()
    c.execute('SELECT course_code FROM courses WHERE id = ?', (course_id,))
    row = c.fetchone()
    con.close()

    if not row:
        return jsonify({'error': 'Curso no encontrado'}), 404

    course_code = row[0]

    # Guardar mensaje del profesor
    save_agent_chat_message(
        course_id,
        conversation_id,
        'profesor',
        message,
        session['user'],
    )

    # ── Umbral de similitud vectorial ──────────────────────────────────────
    try:
        provider = _get_agent_chat_embedding_provider()
        query_embedding = provider.embed_texts([message])[0]
        threshold_results = query_course_embeddings(course_code, query_embedding, top_n=5)
    except (EmbeddingGenerationError, VectorStoreError) as e:
        return jsonify({'error': f'Error al procesar el mensaje: {str(e)}'}), 500

    max_score = max((r['score'] for r in threshold_results), default=0.0)

    if max_score < _AGENT_CHAT_SIMILARITY_THRESHOLD:
        off_topic_msg = (
            'Lo siento, tu pregunta no parece estar relacionada con los documentos del curso. '
            'Por favor, reformúlala o consulta sobre los temas cubiertos en los materiales disponibles.'
        )
        save_agent_chat_message(course_id, conversation_id, 'agente', off_topic_msg)
        return jsonify({
            'response': off_topic_msg,
            'sources': [],
            'conversation_id': conversation_id,
        }), 200

    # ── Construir historial de mensajes para el LLM ────────────────────────
    # El mensaje del profesor ya fue persistido arriba, por lo que list_agent_chat_history
    # lo devuelve como último entry — no hay que añadirlo manualmente al final.
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
    from agent_tools import AGENT_TOOLS, get_llm_with_tools

    system_prompt = (
        'Eres un asistente académico especializado en los documentos del curso. '
        'Responde únicamente preguntas relacionadas con el contenido de los documentos disponibles. '
        'Si una pregunta no tiene relación con los materiales del curso, indícalo con cortesía '
        'y pide al usuario que reformule su consulta. Responde siempre en español.'
    )

    history = list_agent_chat_history(course_id, conversation_id)
    lc_messages = [SystemMessage(content=system_prompt)]

    for entry in history:
        if entry['sender_type'] == 'profesor':
            lc_messages.append(HumanMessage(content=entry['message_text']))
        else:
            lc_messages.append(AIMessage(content=entry['message_text']))

    # ── Llamada al LLM con tools ───────────────────────────────────────────
    try:
        llm = get_llm_with_tools()
        ai_response = llm.invoke(lc_messages)
        sources = []
        tool_calls = getattr(ai_response, 'tool_calls', []) or []

        if tool_calls:
            tools_by_name = {t.name: t for t in AGENT_TOOLS}
            tool_messages = []

            for tool_call in tool_calls:
                tool_name = tool_call['name']
                tool_args = tool_call['args']
                tool_call_id = tool_call['id']

                if tool_name in tools_by_name:
                    tool_result = str(tools_by_name[tool_name].invoke(tool_args))

                    # Extraer nombres de archivo del output formateado de la tool
                    for line in tool_result.splitlines():
                        match = re.search(r'Fuente:\s*(.+?)\s*\|', line)
                        if match:
                            source = match.group(1).strip()
                            if source and source not in sources:
                                sources.append(source)

                    tool_messages.append(
                        ToolMessage(content=tool_result, tool_call_id=tool_call_id)
                    )

            # Segunda llamada con los resultados de las tools para obtener la respuesta final
            ai_response = llm.invoke(lc_messages + [ai_response] + tool_messages)

        final_text = str(getattr(ai_response, 'content', '') or '').strip()

        if not final_text:
            final_text = 'No pude generar una respuesta. Por favor, intenta de nuevo.'

    except RuntimeError as e:
        return jsonify({'error': str(e)}), 500
    except Exception as e:
        return jsonify({'error': f'Error al procesar la respuesta del agente: {str(e)}'}), 500

    # Guardar respuesta del agente
    save_agent_chat_message(course_id, conversation_id, 'agente', final_text)

    return jsonify({
        'response': final_text,
        'sources': sources,
        'conversation_id': conversation_id,
    }), 200


if __name__ == '__main__':

    app.run(debug = True, host = 'localhost', port = 5000)
