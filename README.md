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
* Búsqueda semántica en lenguaje natural (Top-N configurable) dentro del curso seleccionado.
* Descarga directa de archivos de las distintas versiones subidas.
* Comentarios de Estudiantes que son visibles solo para profesor/admin.
* Hash SHA256 para cada documento y cada subida.
* Historial de cambios con fechas.
* Interfaz diferenciada por rol de usuario.
* Confirmación antes de eliminar.
* Interfaz moderna y responsiva.

## Estructura del Proyecto

```
Knowledge Base Curator Agent/
├── app.py                    # Servidor Flask con la lógica principal (orquestador)
├── document_processing.py    # Extracción/chunking de texto por archivo
├── embedding_processing.py   # Proveedores y payloads de embeddings
├── vector_store.py           # Persistencia y consultas en ChromaDB
├── requirements.txt          # Dependencias Python
├── database.db               # Base de datos SQLite
├── README.md                 # Documentación del proyecto
├── templates/
│   ├── login.html            # Página de autenticación
│   ├── signup.html           # Página de registro de usuarios
│   ├── index.html            # Página de gestión de cursos
│   └── upload.html           # Página de documentos, comentarios y búsqueda semántica
├── static/
│   └── style.css            # Estilos CSS
├── tests/
│   ├── run_issue_suite.py   # Suite integrada de validación (Issues 11-15)
│   ├── test_issue_11.py
│   ├── test_issue_12.py
│   ├── test_issue_13.py
│   ├── test_issue_14.py
│   └── test_issue_15.py
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
- Cambiar de curso y cerrar sesión

### Profesor
- Crear cursos y eliminar cursos propios (con confirmación)
- Subir documentos al curso y eliminar documentos
- Consultar historial de documentos y comparar versiones
- Descargar documentos
- Ver comentarios de estudiantes
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


### Sincronización de Vector Store en Eliminación y Versionado

- Al eliminar un documento (`DELETE /api/delete-document/<id>`), se eliminan también sus chunks en ChromaDB usando filtro por `doc_hash`.
- Al subir una nueva versión (`POST /api/upload` con mismo nombre en el curso), se eliminan primero los chunks de la versión anterior antes de indexar los nuevos.
- Si ocurre un error de vector store durante eliminación/versionado, SQLite hace rollback para mantener consistencia transaccional.

###Búsqueda y Recuperación en Lenguaje Natural

- Se implementó el endpoint `POST /api/query` para consultar documentos del curso actual.
- La consulta se vectoriza con el mismo modelo de embeddings local (`all-MiniLM-L6-v2`).
- La búsqueda se ejecuta exclusivamente en la colección Chroma del curso seleccionado.
- Se devuelve JSON con resultados rankeados por similitud y metadatos de fuente.

### Interfaz de Usuario (UI)

- Se agregó una sección separada de "Consulta Semántica del Curso" para Admin, Profesor y Estudiante.
- La UI incluye selector Top-N con valores fijos: `1`, `3`, `5`, `10`.
- Cada resultado se muestra como tarjeta con:
  - Chunk de texto
  - Score de similitud
  - Nombre del archivo
  - Fecha de subida
- Para chunks largos se incorporó interacción "Ver más / Ver menos".

### Pruebas

- Se añadieron pruebas de Issue 14 en `tests/test_issue_14.py` (sincronización y rollback).
- Se añadieron pruebas de Issue 15 en `tests/test_issue_15.py` (respuesta, alcance por curso y validaciones).
- Se consolidó la ejecución en `tests/run_issue_suite.py` para validar Issues 11-15 en conjunto.

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
**Última actualización**: Marzo, 2026 
**Desarrollado con**: Python, Flask, HTML5, CSS3, JavaScript
