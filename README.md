# Knowledge-Base-Curator-Agent

Agente de curaduría impulsado por IA para cursos o asignaturas universitarias dinámicas. Utiliza modelos de lenguaje extenso (LLMs) y generación aumentada por recuperación (RAG) para supervisar la documentación del curso, detectar inconsistencias, reducir la redundancia y sugerir mejoras entre distintas cohortes, manteniendo a los instructores al mando mediante un sistema de aprobación con intervención humana (human-in-the-loop).



## Descripción General

El **Asistente de Curaduría - Gestor de Documentos** permite:
- **Profesores/Admin**: Subir documentos al curso, ver comentarios de estudiantes y administrar documentos
- **Estudiantes**: Ver documentos del curso y comentar sobre los documentos disponibles

Los documentos son **globales por curso** (no dependen de la sesión), por lo que persisten incluso después de cerrar sesión.

## Características Principales

* Sistema de autenticación con 3 roles (Admin, Profesor, Estudiante)  
* Crear/eliminar cursos (solo Admin y Profesores)  
* Documentos globales, estos ppersisten en la base de datos  
* Subir documentos (PDF, MD, DOCX, TXT) mediante drag & drop  
* Comentarios de Estudiantes que son visibles solo para profesor/admin  
* Hash SHA256 para cada documento y cada subida  
* Historial de cambios con fechas  
* Interfaz diferenciada por rol de usuario  
* Confirmación antes de eliminar  
* Interfaz moderna y responsiva  

## Estructura del Proyecto

```
Testeo Proyecto/
├── app.py                 # Servidor Flask con toda la lógica (Orquestador)
├── requirements.txt       # Dependencias Python
├── database.db           # Base de datos 
├── templates/
│   ├── login.html        # Página de autenticación
│   ├── index.html        # Página de gestión de cursos
│   └── upload.html       # Página de documentos y comentarios
├── static/
│   └── style.css         # Estilos CSS
├── run.bat               # Script de ejecución automática 
└── run.ps1               # Script PowerShell alternativo 
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
- Crear cursos
- Eliminar cursos (con confirmación)
- Subir documentos al curso
- Eliminar documentos
- Ver comentarios de estuudiantes
- Cambiar de curso
- Cerrar sesión

### Profesor
- Crear cursos
- Eliminar cursos (con confirmación)
- Subir documentos al curso
- Eliminar docuemntos
- Ver comentarios de estudiantes
- Cambiar de curso
- Cerrar sesión

### Estudiante
- Ver cursos disponibles
- Seleccionar e ingresar a un curso
- Ver documentos del curso
- Comentar sobre documentos (máx 500 caracteres)
- Cambiar de curso
- Cerrar sesión
- NO puede: Crear/eliminar cursos, subir/eliminar documentos, ver comentarios


## API Endpoints

### Autenticación
- `POST /login` - Inicia sesión
- `GET /logout` - Cierra sesión

### Cursos
- `GET /api/courses` - Obtiene lista de cursos
- `POST /api/create-course` - Crea un nuevo curso (admin/profesor)
- `DELETE /api/delete-course/<course>` - Elimina un curso (admin/profesor)

### Documentos
- `GET /api/documents/<course>` - Obtiene documentos del curso
- `POST /api/upload` - Sube documentos (admin/profesor)
- `DELETE /api/delete-document/<id>` - Elimina un documento (admin/profesor)

### Comentarios
- `GET /api/comments/<document_id>` - Obtiene comentarios (admin/profesor)
- `POST /api/add-comment` - Agrega un comentario (estudiante)

## Stack

- **Base de datos**: SQLite3 (local, no requiere servidor externo)
- **Backend**: Flask 
- **Frontend**: HTML5, CSS3, JavaScript 
- **Hashing**: SHA256
- **Autenticación**: Sesiones de Flask
- **Validación**: Extensiones de archivo, duplicados, límites de caracteres



**Versión**: 1.0 
**Última actualización**: Marzo, 2026 
**Desarrollado con**: Python, Flask, HTML5, CSS3, JavaScript