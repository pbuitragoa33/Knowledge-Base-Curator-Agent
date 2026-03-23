# Orquestador de la AplicaciÃ³n


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
    delete_course_embeddings,
    upsert_course_embeddings,
)


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
# ConfiguraciÃ³n --> extensiones, directorios y rutas
# ----------------------------------------------------

DOWNLOAD_DIR = os.environ.get('DOWNLOAD_DIR', os.path.expanduser('~/Downloads/UploadedFiles'))
ALLOWED_EXTENSIONS = {'pdf', 'md', 'docx', 'txt'}
DATABASE = os.environ.get('DATABASE_PATH', 'database.db')
VALID_SHELF_STATUS = {'borrador', 'publicado'}
EMBEDDING_MODEL_NAME = os.environ.get('EMBEDDING_MODEL_NAME', DEFAULT_EMBEDDING_MODEL_NAME)
EMBEDDING_BATCH_SIZE = _read_positive_int_env('EMBEDDING_BATCH_SIZE', DEFAULT_EMBEDDING_BATCH_SIZE)
EMBEDDING_DEVICE = os.environ.get('EMBEDDING_DEVICE', DEFAULT_EMBEDDING_DEVICE)
EMBEDDING_DIMENSION = DEFAULT_EMBEDDING_DIMENSION
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

# ContraseÃ±a como hash  con el algoritmo SHA256

def hash_password(password):

    """Hash simple para contraseÃ±a (en producciÃ³n usar bcrypt)"""

    return hashlib.sha256(password.encode()).hexdigest()

# VerificaciÃ³n de contraseÃ±a

def verify_password(password, hashed):

    """Verifica contraseÃ±a"""

    return hash_password(password) == hashed



def validate_email(email):
    """Valida email con expresión regular"""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None

def normalize_course_code(course_code):

    """Normaliza el cÃ³digo del curso."""

    return str(course_code or '').strip().upper()


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


def _create_courses_table(cursor, table_name = 'courses'):

    cursor.execute(f'''CREATE TABLE IF NOT EXISTS {table_name}
                 (id INTEGER PRIMARY KEY,
                  name TEXT UNIQUE NOT NULL,
                  course_code TEXT NOT NULL,
                  responsible_teacher TEXT NOT NULL,
                  status TEXT NOT NULL CHECK(status IN ('borrador', 'publicado')),
                  created_by TEXT,
                  created_date TEXT)''')


def _create_courses_indexes(cursor):

    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_courses_code_nocase ON courses(course_code COLLATE NOCASE)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_courses_responsible_teacher ON courses(responsible_teacher)")


def _get_first_teacher(cursor):

    cursor.execute("SELECT username FROM users WHERE role = 'profesor' ORDER BY id LIMIT 1")
    result = cursor.fetchone()

    return result[0] if result else None


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


def _ensure_courses_schema(conn, cursor):

    expected_columns = ['id', 'name', 'course_code', 'responsible_teacher', 'status', 'created_by', 'created_date']
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

        migrated_rows.append(
            (course_id, course_name, unique_code, responsible_teacher, status, created_by, created_date)
        )

    conn.execute("PRAGMA foreign_keys = OFF")
    cursor.execute("DROP TABLE IF EXISTS courses_new")
    _create_courses_table(cursor, table_name = 'courses_new')
    cursor.executemany('''INSERT INTO courses_new
                          (id, name, course_code, responsible_teacher, status, created_by, created_date)
                          VALUES (?, ?, ?, ?, ?, ?, ?)''', migrated_rows)
    cursor.execute("DROP TABLE courses")
    cursor.execute("ALTER TABLE courses_new RENAME TO courses")
    _create_courses_indexes(cursor)
    conn.execute("PRAGMA foreign_keys = ON")


# --------------------------------------------------
# Acciones relacinadas a la Base de Datos (SQLite3)
# --------------------------------------------------

# InicializaciÃ³n y ConexiÃ³n
# CreaciÃ³n de tablas (en caso de que exista se hace Ingesta)
# CreaciÃ³n de Usuarios y conexiones entre los roles y acciones


def init_db():

    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()

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

    _ensure_courses_schema(conn, c)

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

    conn.commit()
    conn.close()


init_db()


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
        
        # ConexiÃ³n con la base de datos
        
        conn = sqlite3.connect(DATABASE)
        c = conn.cursor()
        c.execute("SELECT role FROM users WHERE username = ?", (session['user'],))
        result = c.fetchone()
        conn.close()
        
        if not result or result[0] not in ['admin', 'profesor']:

            return jsonify({'error': 'Acceso denegado'}), 403
        
        return f(*args, **kwargs)
    
    return decorated_function


# --------------------------------------------------------
# Permitir las tipos de archivos (.txt, .md, .docx, .pdf)
# --------------------------------------------------------

def allowed_file(filename):

    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def get_embedding_provider():

    """Inicializa el proveedor de embeddings solo cuando es necesario."""

    global EMBEDDING_PROVIDER

    if EMBEDDING_PROVIDER is None:

        if app.config.get('TESTING'):

            EMBEDDING_PROVIDER = DeterministicEmbeddingProvider(
                embedding_dimension=EMBEDDING_DIMENSION,
            )

        else:

            EMBEDDING_PROVIDER = LocalSentenceTransformerProvider(
                model_name=EMBEDDING_MODEL_NAME,
                batch_size=EMBEDDING_BATCH_SIZE,
                device=EMBEDDING_DEVICE,
                embedding_dimension=EMBEDDING_DIMENSION,
            )

    return EMBEDDING_PROVIDER


def register_chunk_records(upload_hash, chunk_records):

    """Mantiene el registro temporal chunk -> snapshot de subida."""

    upload_chunk_ids = UPLOAD_CHUNK_INDEX.setdefault(upload_hash, [])

    for record in chunk_records:

        chunk_id = record['chunk_id']
        CHUNK_REGISTRY[chunk_id] = record
        upload_chunk_ids.append(chunk_id)

    return upload_chunk_ids


def register_embedding_payloads(upload_hash, embedding_payloads):

    """Mantiene el registro temporal chunk -> embedding asociado."""

    upload_embedding_ids = UPLOAD_EMBEDDING_INDEX.setdefault(upload_hash, [])

    for payload in embedding_payloads:

        chunk_id = payload['chunk_id']
        EMBEDDING_REGISTRY[chunk_id] = payload
        upload_embedding_ids.append(chunk_id)

    return upload_embedding_ids


def clear_upload_staging(upload_hash):

    """Limpia los registros temporales de chunks y embeddings de una subida."""

    chunk_ids = UPLOAD_CHUNK_INDEX.pop(upload_hash, [])

    for chunk_id in chunk_ids:
        CHUNK_REGISTRY.pop(chunk_id, None)

    embedding_ids = UPLOAD_EMBEDDING_INDEX.pop(upload_hash, [])

    for chunk_id in embedding_ids:
        EMBEDDING_REGISTRY.pop(chunk_id, None)


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

            # Para docx, extraer solo el texto básico
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

    # Contar líneas añadidas, eliminadas e igual
    added = 0
    removed = 0
    same = 0

    # Simplificar: contar líneas diferentes
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
# Rutas de AutenticaciÃ³n
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
            error = 'Selecciona un rol válido'
        elif not email or not validate_email(email):
            error = 'El correo no es válido'
        elif not username or len(username.strip()) == 0:
            error = 'El nombre de usuario es requerido'
        elif not password or not password_confirm:
            error = 'La contraseña es requerida'
        elif len(password) < 8 or len(password) > 20:
            error = 'La contraseña debe tener entre 8 y 20 caracteres'
        elif password != password_confirm:
            error = 'Las contraseñas no coinciden'

        if not error:
            conn = sqlite3.connect(DATABASE)
            c = conn.cursor()

            c.execute("SELECT id FROM users WHERE email = ?", (email,))
            if c.fetchone():
                error = 'Este correo ya está registrado'
            else:
                c.execute("SELECT id FROM users WHERE username = ?", (username,))
                if c.fetchone():
                    error = 'Este nombre de usuario ya existe'

            if error:
                conn.close()
                return render_template('signup.html', error = error)

            try:
                hashed_password = hash_password(password)
                c.execute('INSERT INTO users VALUES (NULL, ?, ?, ?, ?)',
                         (username, email, hashed_password, role))
                conn.commit()
                conn.close()
                return redirect(url_for('login'))
            except sqlite3.IntegrityError:
                conn.close()
                return render_template('signup.html', error = 'Error al crear la cuenta. Intenta de nuevo')

        return render_template('signup.html', error = error)

    return render_template('signup.html')


# Login

@app.route('/login', methods = ['GET', 'POST'])

def login():

    if request.method == 'POST':

        login_input = request.form.get('login')
        password = request.form.get('password')
        
        conn = sqlite3.connect(DATABASE)
        c = conn.cursor()
        c.execute("SELECT password, role, username FROM users WHERE username = ? OR email = ?", (login_input, login_input))
        result = c.fetchone()
        conn.close()
        
        if result and verify_password(password, result[0]):

            session['user'] = result[2]
            session['role'] = result[1]
            session['session_id'] = generate_hash(str(datetime.now()))

            return redirect(url_for('index'))
        
        else:

            return render_template('login.html', error = 'Usuario/correo o contraseña incorrectos')
    
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

    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute("SELECT name FROM courses WHERE name = ?", (course,))
    result = c.fetchone()
    conn.close()
    
    if not result:

        return redirect(url_for('index'))
    
    session['selected_course'] = course

    return render_template('upload.html', course = course)


# Subida de Archivos

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
    
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()

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
                    
                    # Obtener el próximo número de versión
                    c.execute('SELECT MAX(version_number) FROM document_versions WHERE document_id = ?',
                              (document_id,))
                    max_version = c.fetchone()[0] or 0
                    next_version = max_version + 1
                    
                    # Guardar en document_versions
                    c.execute('INSERT INTO document_versions VALUES (NULL, ?, ?, ?, ?, ?, ?, ?)',
                              (document_id, next_version, filename, file_hash, upload_date, filepath, user))
                    
                    # Actualizar el documento principal con la versión más reciente
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
                    document_id=document_id,
                    doc_hash=doc_hash,
                    upload_hash=upload_hash,
                    course=course,
                    upload_date=upload_date,
                    filename=filename,
                    file_hash=file_hash,
                )

                register_chunk_records(upload_hash, chunk_records)

                embedding_payloads = build_embedding_payloads(
                    chunk_records,
                    provider=get_embedding_provider(),
                    embedding_dimension=EMBEDDING_DIMENSION,
                )
                register_embedding_payloads(upload_hash, embedding_payloads)
                all_embedding_payloads.extend(embedding_payloads)

                if not chunk_records:
                    app.logger.warning('No chunks generated for uploaded file: %s', filename)
                
                uploaded_files.append({
                    'filename': filename,
                    'file_hash': file_hash,
                    'upload_date': upload_date,
                    'doc_hash': doc_hash,
                    'document_id': document_id
                })
        
        if not uploaded_files:

            clear_upload_staging(upload_hash)

            return jsonify({'error': 'No valid files provided'}), 400

        persisted_vector_ids = upsert_course_embeddings(course_code, all_embedding_payloads)
        
        conn.commit()

        return jsonify({
            'success': True,
            'upload_hash': upload_hash,
            'files': uploaded_files,
            'upload_date': upload_date
        })

    except (EmbeddingGenerationError, VectorStoreError) as e:

        conn.rollback()

        if persisted_vector_ids and course_code:
            try:
                delete_course_embeddings(course_code, persisted_vector_ids)
            except VectorStoreError:
                app.logger.exception('Failed to rollback persisted embeddings for upload: %s', upload_hash)

        clear_upload_staging(upload_hash)
        remove_uploaded_files(created_filepaths)
        app.logger.exception('Upload processing failed during embedding/vector persistence.')
        return jsonify({'error': str(e)}), 500

    except Exception as e:

        conn.rollback()

        if persisted_vector_ids and course_code:
            try:
                delete_course_embeddings(course_code, persisted_vector_ids)
            except VectorStoreError:
                app.logger.exception('Failed to rollback persisted embeddings for upload: %s', upload_hash)

        clear_upload_staging(upload_hash)
        remove_uploaded_files(created_filepaths)
        return jsonify({'error': str(e)}), 500

    finally:

        conn.close()


# ObtenciÃ³n de los ODucmentos Globales de un Curso

@app.route('/api/documents/<course>')

@login_required

def get_documents(course):

    """Obtiene documentos del curso (globales)"""

    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute('SELECT id, doc_hash, filename, file_hash, upload_date, uploaded_by FROM documents WHERE course = ? ORDER BY upload_date DESC',
              (course,))
    
    docs = c.fetchall()
    conn.close()
    
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


# Obtener comentarios hechos por los estudiantes

@app.route('/api/comments/<int:document_id>')

@login_required

def get_comments(document_id):

    """Obtiene comentarios de un documento (solo si es admin/profesor)"""

    user = session.get('user')
    
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    
    # Verificar si el usuario es admin/profesor

    c.execute("SELECT role FROM users WHERE username = ?", (user,))
    role_result = c.fetchone()
    
    if not role_result or role_result[0] not in ['admin', 'profesor']:

        conn.close()

        return jsonify({'error': 'Acceso denegado'}), 403
    
    # Obtener comentarios del documento

    c.execute('SELECT id, student_name, comment_text, comment_date FROM comments WHERE document_id = ? ORDER BY comment_date DESC',
              (document_id,))
    
    comments = c.fetchall()
    conn.close()
    
    comment_list = []

    for comment_id, student_name, comment_text, comment_date in comments:

        comment_list.append({
            'id': comment_id,
            'student_name': student_name,
            'comment_text': comment_text,
            'comment_date': comment_date
        })
    
    return jsonify(comment_list)


# AÃ±adir comentarios

@app.route('/api/add-comment', methods = ['POST'])

@login_required

def add_comment():

    """Agrega un comentario a un documento (solo para estudiantes)"""

    data = request.json
    document_id = data.get('document_id')
    comment_text = data.get('comment_text', '').strip()
    student_name = session.get('user')
    
    if not comment_text:

        return jsonify({'error': 'El comentario no puede estar vacÃ­o'}), 400
    
    if len(comment_text) > 500:

        return jsonify({'error': 'El comentario no puede exceder 500 caracteres'}), 400
    
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    
    # Verificar que el documento existe

    c.execute("SELECT id FROM documents WHERE id = ?", (document_id,))

    if not c.fetchone():

        conn.close()

        return jsonify({'error': 'Documento no encontrado'}), 404
    
    comment_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    try:

        c.execute('INSERT INTO comments VALUES (NULL, ?, ?, ?, ?)',
                  (document_id, student_name, comment_text, comment_date))
        conn.commit()
        conn.close()

        return jsonify({'success': True, 'message': 'Comentario agregado correctamente'}), 201
    except Exception as e:

        conn.close()

        return jsonify({'error': str(e)}), 500


# Eliminar un documento subido

@app.route('/api/delete-document/<int:document_id>', methods = ['DELETE'])

@admin_required

def delete_document(document_id):

    """Elimina un documento y sus comentarios"""

    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    
    # Obtener ruta del archivo

    c.execute("SELECT filepath FROM documents WHERE id = ?", (document_id,))
    result = c.fetchone()
    
    if not result:

        conn.close()

        return jsonify({'error': 'Documento no encontrado'}), 404
    
    filepath = result[0]
    
    try:

        # Eliminar archivo del sistema

        if os.path.exists(filepath):

            os.remove(filepath)
        
        # Eliminar comentarios

        c.execute("DELETE FROM comments WHERE document_id = ?", (document_id,))
        
        # Eliminar documento

        c.execute("DELETE FROM documents WHERE id = ?", (document_id,))
        
        conn.commit()
        conn.close()

        return jsonify({'success': True, 'message': 'Documento eliminado correctamente'}), 200
    
    except Exception as e:

        conn.close()

        return jsonify({'error': str(e)}), 500


# Historico

@app.route('/api/history')

def get_history():

    session_id = session.get('session_id', '')
    course = session.get('selected_course', '')
    
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute('SELECT upload_hash, upload_date, files_json FROM uploads WHERE session_id = ? AND course = ? ORDER BY upload_date DESC',
              (session_id, course))
    
    uploads = c.fetchall()
    conn.close()
    
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

    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
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

    conn.close()

    return jsonify(courses)


@app.route('/api/teachers')

@admin_required

def get_teachers():

    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute("SELECT username FROM users WHERE role = 'profesor' ORDER BY username")
    teachers = [row[0] for row in c.fetchall()]
    conn.close()

    return jsonify(teachers)


# CreaciÃ³n de curso

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

        return jsonify({'error': 'El cÃ³digo del curso es requerido'}), 400

    if len(course_code) > 30:

        return jsonify({'error': 'El cÃ³digo del curso no puede exceder 30 caracteres'}), 400

    if not responsible_teacher:

        return jsonify({'error': 'El docente responsable es requerido'}), 400

    if status not in VALID_SHELF_STATUS:

        return jsonify({'error': 'El estado debe ser \"borrador\" o \"publicado\"'}), 400
    
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()

    # Verificar docente responsable

    c.execute("SELECT role FROM users WHERE username = ?", (responsible_teacher,))
    teacher_role = c.fetchone()

    if not teacher_role or teacher_role[0] != 'profesor':
        conn.close()
        return jsonify({'error': 'El docente responsable no existe o no tiene rol profesor'}), 400
    
    # Verificar duplicados

    c.execute("SELECT COUNT(*) FROM courses WHERE name = ?", (name,))
    if c.fetchone()[0] > 0:
        conn.close()
        return jsonify({'error': 'Este curso ya existe'}), 400

    c.execute("SELECT COUNT(*) FROM courses WHERE course_code = ? COLLATE NOCASE", (course_code,))
    if c.fetchone()[0] > 0:
        conn.close()
        return jsonify({'error': 'Este cÃ³digo de curso ya existe'}), 400
    
    # Crear curso

    try:

        created_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        c.execute('''INSERT INTO courses
                     (name, course_code, responsible_teacher, status, created_by, created_date)
                     VALUES (?, ?, ?, ?, ?, ?)''',
                  (name, course_code, responsible_teacher, status, session.get('user'), created_date))

        new_course_id = c.lastrowid
        conn.commit()
        conn.close()

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

        conn.close()

        return jsonify({'error': f'Error de integridad en base de datos: {str(e)}'}), 400
    
    except Exception as e:

        conn.close()

        return jsonify({'error': str(e)}), 500


# Eliminar curso

@app.route('/api/delete-course/<course>', methods = ['DELETE'])

@login_required

def delete_course(course):

    user = session.get('user')
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    
    # Verificar que solo ADMIN puede eliminar
    c.execute("SELECT role FROM users WHERE username = ?", (user,))
    role_result = c.fetchone()
    
    if not role_result or role_result[0] != 'admin':
        conn.close()
        return jsonify({'error': 'Solo administradores pueden eliminar cursos'}), 403
    
    # Verificar que existe el curso

    c.execute("SELECT id FROM courses WHERE name = ?", (course,))
    course_result = c.fetchone()
    
    if not course_result:

        conn.close()

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
        
        conn.commit()
        conn.close()

        return jsonify({'success': True, 'message': f'Curso "{course}" eliminado con todos sus datos'}), 200
    
    except Exception as e:

        conn.close()

        return jsonify({'error': str(e)}), 500


# ----- NUEVAS RUTAS PARA VERSIONADO Y GESTIÓN -----

# Obtener historial de versiones de un documento

@app.route('/api/document-history/<filename>')

@login_required

def get_document_history(filename):

    """Obtiene el historial de versiones de un documento"""

    course = session.get('selected_course', '')
    
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    
    # Obtener el documento
    c.execute('SELECT id FROM documents WHERE filename = ? AND course = ?',
              (filename, course))
    doc_result = c.fetchone()
    
    if not doc_result:
        conn.close()
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
    
    conn.close()
    
    return jsonify({
        'document_id': document_id,
        'filename': filename,
        'versions': versions
    })


# Obtener diff entre dos versiones

@app.route('/api/document-diff/<int:document_id>/<int:version1>/<int:version2>')

@login_required

def get_document_diff(document_id, version1, version2):

    """Compara dos versiones de un documento"""

    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    
    # Obtener filepaths de ambas versiones
    c.execute('SELECT filepath FROM document_versions WHERE document_id = ? AND version_number = ?',
              (document_id, version1))
    file1_result = c.fetchone()
    
    c.execute('SELECT filepath FROM document_versions WHERE document_id = ? AND version_number = ?',
              (document_id, version2))
    file2_result = c.fetchone()
    
    conn.close()
    
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


# Descargar un archivo específico

@app.route('/api/download/<int:document_id>')

@login_required

def download_document(document_id):

    """Descarga la versión más reciente de un documento"""

    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    
    # Obtener documento y verificar acceso
    c.execute('SELECT course, filename FROM documents WHERE id = ?',
              (document_id,))
    doc_result = c.fetchone()
    
    if not doc_result:
        conn.close()
        return jsonify({'error': 'Documento no encontrado'}), 404
    
    course_name = doc_result[0]
    
    # Verificar que el usuario tenga acceso al curso
    user = session.get('user')
    c.execute("SELECT role FROM users WHERE username = ?", (user,))
    role_result = c.fetchone()
    
    conn.close()
    
    if not role_result:
        return jsonify({'error': 'Usuario no autorizado'}), 403
    
    # Obtener versión más reciente
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute('''SELECT filepath, filename FROM document_versions 
                 WHERE document_id = ? 
                 ORDER BY version_number DESC LIMIT 1''',
              (document_id,))
    version_result = c.fetchone()
    conn.close()
    
    if not version_result:
        return jsonify({'error': 'Archivo no disponible'}), 404
    
    filepath = version_result[0]
    filename = version_result[1]
    
    if not os.path.exists(filepath):
        return jsonify({'error': 'Archivo no encontrado en servidor'}), 404
    
    return send_file(filepath, as_attachment=True, download_name=filename)


# Descargar una versión específica

@app.route('/api/download-version/<int:version_id>')

@login_required

def download_version(version_id):

    """Descarga una versión específica de un documento"""

    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    
    c.execute('SELECT filepath, filename FROM document_versions WHERE id = ?',
              (version_id,))
    result = c.fetchone()
    conn.close()
    
    if not result:
        return jsonify({'error': 'Versión no encontrada'}), 404
    
    filepath = result[0]
    filename = result[1]
    
    if not os.path.exists(filepath):
        return jsonify({'error': 'Archivo no encontrado'}), 404
    
    return send_file(filepath, as_attachment=True, download_name=f"v{version_id}_{filename}")


# ----- RUTAS PARA GESTIÓN DE PROFESORES (ADMIN) -----

# Obtener profesores asignados a un curso

@app.route('/api/course-professors/<course>')

@login_required

def get_course_professors(course):

    """Obtiene los profesores asignados a un curso (solo admin)"""

    user = session.get('user')
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    
    # Verificar que es admin
    c.execute("SELECT role FROM users WHERE username = ?", (user,))
    role_result = c.fetchone()
    
    if not role_result or role_result[0] != 'admin':
        conn.close()
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
    
    conn.close()
    
    return jsonify(professors)


# Asignar profesor a un curso

@app.route('/api/assign-professor', methods=['POST'])

@login_required

def assign_professor():

    """Asigna un profesor a un curso (solo admin)"""

    user = session.get('user')
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    
    # Verificar que es admin
    c.execute("SELECT role FROM users WHERE username = ?", (user,))
    role_result = c.fetchone()
    
    if not role_result or role_result[0] != 'admin':
        conn.close()
        return jsonify({'error': 'Acceso denegado'}), 403
    
    data = request.json or {}
    course_name = data.get('course_name', '').strip()
    professor_username = data.get('professor_username', '').strip()
    
    if not course_name or not professor_username:
        conn.close()
        return jsonify({'error': 'Datos incompletos'}), 400
    
    # Verificar que el curso existe
    c.execute("SELECT id FROM courses WHERE name = ?", (course_name,))
    if not c.fetchone():
        conn.close()
        return jsonify({'error': 'Curso no existe'}), 404
    
    # Verificar que el profesor existe y es profesor
    c.execute("SELECT role FROM users WHERE username = ?", (professor_username,))
    prof_role = c.fetchone()
    
    if not prof_role or prof_role[0] != 'profesor':
        conn.close()
        return jsonify({'error': 'Usuario no es profesor'}), 400
    
    # Verificar que no esté ya asignado
    c.execute("SELECT id FROM course_professors WHERE course_name = ? AND professor_username = ?",
              (course_name, professor_username))
    if c.fetchone():
        conn.close()
        return jsonify({'error': 'Profesor ya asignado a este curso'}), 400
    
    try:
        assigned_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        c.execute('INSERT INTO course_professors VALUES (NULL, ?, ?, ?)',
                  (course_name, professor_username, assigned_date))
        conn.commit()
        conn.close()
        
        return jsonify({'success': True, 'message': 'Profesor asignado correctamente'}), 201
    except Exception as e:
        conn.close()
        return jsonify({'error': str(e)}), 500


# Desasignar profesor de un curso

@app.route('/api/unassign-professor', methods=['POST'])

@login_required

def unassign_professor():

    """Desasigna un profesor de un curso (solo admin)"""

    user = session.get('user')
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    
    # Verificar que es admin
    c.execute("SELECT role FROM users WHERE username = ?", (user,))
    role_result = c.fetchone()
    
    if not role_result or role_result[0] != 'admin':
        conn.close()
        return jsonify({'error': 'Acceso denegado'}), 403
    
    data = request.json or {}
    course_name = data.get('course_name', '').strip()
    professor_username = data.get('professor_username', '').strip()
    
    if not course_name or not professor_username:
        conn.close()
        return jsonify({'error': 'Datos incompletos'}), 400
    
    try:
        c.execute('DELETE FROM course_professors WHERE course_name = ? AND professor_username = ?',
                  (course_name, professor_username))
        conn.commit()
        conn.close()
        
        return jsonify({'success': True, 'message': 'Profesor desasignado correctamente'}), 200
    except Exception as e:
        conn.close()
        return jsonify({'error': str(e)}), 500


# ----- RUTAS PARA GESTIÓN DE ESTUDIANTES (PROFESOR) -----

# Obtener estudiantes en un curso

@app.route('/api/course-students/<course>')

@login_required

def get_course_students(course):

    """Obtiene los estudiantes inscritos en un curso"""

    user = session.get('user')
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    
    # Verificar permisos (profesor del curso o admin)
    c.execute("SELECT role FROM users WHERE username = ?", (user,))
    role_result = c.fetchone()
    
    if not role_result:
        conn.close()
        return jsonify({'error': 'Usuario no válido'}), 403
    
    role = role_result[0]
    
    if role == 'profesor':
        if not professor_can_manage_course(c, course, user):
            conn.close()
            return jsonify({'error': 'No es profesor de este curso'}), 403
    
    elif role != 'admin':
        conn.close()
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
    
    conn.close()
    
    return jsonify(students)


# Obtener estudiantes disponibles (no inscritos)

@app.route('/api/available-students/<course>')

@login_required

def get_available_students(course):

    """Obtiene los estudiantes que puede agregar un profesor"""

    user = session.get('user')
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    
    # Verificar que es profesor del curso
    c.execute("SELECT role FROM users WHERE username = ?", (user,))
    role_result = c.fetchone()
    
    if not role_result:
        conn.close()
        return jsonify({'error': 'Usuario no válido'}), 403
    
    role = role_result[0]
    
    if role == 'profesor':
        if not professor_can_manage_course(c, course, user):
            conn.close()
            return jsonify({'error': 'No es profesor de este curso'}), 403
    
    elif role != 'admin':
        conn.close()
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
    conn.close()
    
    return jsonify(students)


# Agregar estudiante a un curso

@app.route('/api/add-student', methods=['POST'])

@login_required

def add_student():

    """Agrega un estudiante a un curso"""

    user = session.get('user')
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    
    # Verificar permisos
    c.execute("SELECT role FROM users WHERE username = ?", (user,))
    role_result = c.fetchone()
    
    if not role_result:
        conn.close()
        return jsonify({'error': 'Usuario no válido'}), 403
    
    role = role_result[0]
    
    data = request.json or {}
    course_name = data.get('course_name', '').strip()
    student_username = data.get('student_username', '').strip()
    
    if not course_name or not student_username:
        conn.close()
        return jsonify({'error': 'Datos incompletos'}), 400
    
    if role == 'profesor':
        if not professor_can_manage_course(c, course_name, user):
            conn.close()
            return jsonify({'error': 'No es profesor de este curso'}), 403
    
    elif role != 'admin':
        conn.close()
        return jsonify({'error': 'Acceso denegado'}), 403
    
    # Verificar que el estudiante existe
    c.execute("SELECT role FROM users WHERE username = ?", (student_username,))
    student_role = c.fetchone()
    
    if not student_role or student_role[0] != 'estudiante':
        conn.close()
        return jsonify({'error': 'Usuario no es estudiante'}), 400
    
    # Verificar que no esté ya inscrito
    c.execute("SELECT id FROM course_students WHERE course_name = ? AND student_username = ?",
              (course_name, student_username))
    if c.fetchone():
        conn.close()
        return jsonify({'error': 'Estudiante ya inscrito en este curso'}), 400
    
    try:
        added_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        c.execute('INSERT INTO course_students VALUES (NULL, ?, ?, ?)',
                  (course_name, student_username, added_date))
        conn.commit()
        conn.close()
        
        return jsonify({'success': True, 'message': 'Estudiante agregado correctamente'}), 201
    except Exception as e:
        conn.close()
        return jsonify({'error': str(e)}), 500


# Eliminar estudiante de un curso

@app.route('/api/remove-student', methods=['POST'])

@login_required

def remove_student():

    """Elimina un estudiante de un curso"""

    user = session.get('user')
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    
    # Verificar permisos
    c.execute("SELECT role FROM users WHERE username = ?", (user,))
    role_result = c.fetchone()
    
    if not role_result:
        conn.close()
        return jsonify({'error': 'Usuario no válido'}), 403
    
    role = role_result[0]
    
    data = request.json or {}
    course_name = data.get('course_name', '').strip()
    student_username = data.get('student_username', '').strip()
    
    if not course_name or not student_username:
        conn.close()
        return jsonify({'error': 'Datos incompletos'}), 400
    
    if role == 'profesor':
        if not professor_can_manage_course(c, course_name, user):
            conn.close()
            return jsonify({'error': 'No es profesor de este curso'}), 403
    
    elif role != 'admin':
        conn.close()
        return jsonify({'error': 'Acceso denegado'}), 403
    
    try:
        c.execute('DELETE FROM course_students WHERE course_name = ? AND student_username = ?',
                  (course_name, student_username))
        conn.commit()
        conn.close()
        
        return jsonify({'success': True, 'message': 'Estudiante removido correctamente'}), 200
    except Exception as e:
        conn.close()
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':

    app.run(debug = True, host = 'localhost', port = 5000)

