# Orquestador de la AplicaciÃ³n


# ------------
# Librerias 
# ------------

from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from werkzeug.utils import secure_filename
import os
import sqlite3
import hashlib
import json
from datetime import datetime
from functools import wraps
import shutil


# ----------
# En Flask
# ----------

app = Flask(__name__)
app.secret_key = 'tu_clave_secreta_segura_cambiar'


# ----------------------------------------------------
# ConfiguraciÃ³n --> extensiones, directorios y rutas
# ----------------------------------------------------

DOWNLOAD_DIR = os.path.expanduser('~/Downloads/UploadedFiles')
ALLOWED_EXTENSIONS = {'pdf', 'md', 'docx', 'txt'}
DATABASE = 'database.db'
VALID_SHELF_STATUS = {'borrador', 'publicado'}


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


def normalize_course_code(course_code):

    """Normaliza el cÃ³digo del curso."""

    return str(course_code or '').strip().upper()


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
                  password TEXT NOT NULL,
                  role TEXT NOT NULL)''')

    # Crear usuarios por defecto si no

    c.execute("SELECT COUNT(*) FROM users")

    if c.fetchone()[0] == 0:

        users = [
            ('admin', hash_password('admin123'), 'admin'),
            ('profesor', hash_password('prof123'), 'profesor'),
            ('estudiante', hash_password('est123'), 'estudiante')
        ]

        c.executemany('INSERT INTO users VALUES (NULL, ?, ?, ?)', users)

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


# -----------------------
# Rutas de AutenticaciÃ³n
# -----------------------

# Login

@app.route('/login', methods = ['GET', 'POST'])

def login():

    if request.method == 'POST':

        username = request.form.get('username')
        password = request.form.get('password')
        
        conn = sqlite3.connect(DATABASE)
        c = conn.cursor()
        c.execute("SELECT password, role FROM users WHERE username = ?", (username,))
        result = c.fetchone()
        conn.close()
        
        if result and verify_password(password, result[0]):

            session['user'] = username
            session['role'] = result[1]
            session['session_id'] = generate_hash(str(datetime.now()))

            return redirect(url_for('index'))
        
        else:

            return render_template('login.html', error = 'Usuario o contraseÃ±a incorrectos')
    
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
    
    uploaded_files = []
    
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    
    for file in files:

        if file and allowed_file(file.filename):

            filename = secure_filename(file.filename)
            filepath = os.path.join(DOWNLOAD_DIR, filename)
            file.save(filepath)
            
            file_hash = generate_file_hash(filepath)
            doc_hash = generate_hash(str(datetime.now()) + filename)
            
            # Guardar documento en la BD (global para el curso)

            c.execute('INSERT INTO documents VALUES (NULL, ?, ?, ?, ?, ?, ?, ?)',
                      (course, doc_hash, filename, file_hash, upload_date, filepath, user))
            
            uploaded_files.append({
                'filename': filename,
                'file_hash': file_hash,
                'upload_date': upload_date,
                'doc_hash': doc_hash
            })
    
    if not uploaded_files:

        conn.close()

        return jsonify({'error': 'No valid files provided'}), 400
    
    conn.commit()
    conn.close()
    
    # Generar hash global para esta carga

    upload_hash = generate_hash(str(datetime.now()) + user)
    
    return jsonify({
        'success': True,
        'upload_hash': upload_hash,
        'files': uploaded_files,
        'upload_date': upload_date
    })


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

@admin_required

def delete_course(course):

    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    
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
        
        # Eliminar curso

        c.execute("DELETE FROM courses WHERE name = ?", (course,))
        
        conn.commit()
        conn.close()

        return jsonify({'success': True, 'message': f'Curso "{course}" eliminado con todos sus datos'}), 200
    
    except Exception as e:

        conn.close()

        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':

    app.run(debug = True, host = 'localhost', port = 5000)

