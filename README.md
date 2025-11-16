🤖 Kontabot | Asistente Fiscal DGII para Telegram

Kontabot es un bot de Telegram diseñado para automatizar la tediosa tarea de registrar facturas de compra y venta y generar los archivos de formato 606 y 607 (Reporte de Compras y Ventas de Bienes y Servicios) requeridos por la Dirección General de Impuestos Internos (DGII) de la República Dominicana.

El bot utiliza OCR (Reconocimiento Óptico de Caracteres) para extraer automáticamente los datos clave (NCF, RNC, Montos de ITBIS, etc.) de imágenes o PDFs, permitiendo al usuario generar el archivo de texto final con solo unos pocos clics.

🚀 Características Principales

Entrada Multimodal: Acepta fotos claras o documentos PDF de facturas.

Extracción de Datos Inteligente: Utiliza pytesseract y expresiones regulares avanzadas para identificar y extraer NCF, RNC, y montos clave.

Generación DGII: Genera el archivo de texto plano (.txt) con la estructura de campos requerida por la DGII.

Gestión de Sesiones: Mantiene registros de las facturas enviadas en una sesión hasta que el usuario finaliza y genera el reporte.

🛠️ Stack Tecnológico

Componente

Tecnología

Propósito

Plataforma del Bot

Python (python-telegram-bot)

Manejo de la API de Telegram.

OCR

Tesseract OCR (pytesseract)

Conversión de imagen/PDF a texto.

Despliegue

Docker, Google Cloud Build

Construcción del contenedor con Tesseract.

Alojamiento

Google Cloud Run

Ejecución escalable y serverless del backend 24/7.

⚙️ Despliegue en Google Cloud Run

Kontabot está diseñado para ser desplegado como un servicio de contenedor serverless, ideal para bots de bajo a medio tráfico con picos ocasionales.

1. Requisitos de la Nube

Asegúrate de tener habilitadas las siguientes APIs en tu proyecto de Google Cloud:

Cloud Run API

Cloud Build API

Artifact Registry API

2. Archivos Clave

El despliegue está definido por:

Dockerfile: Define la imagen, instalando Python y el programa de sistema Tesseract OCR.

requirements.txt: Lista las librerías de Python.

kontabot_main.py: Contiene la lógica del bot.

3. Configuración del Token

El bot requiere que el token de Telegram se configure como una Variable de Entorno en tu servicio de Cloud Run:

Variable

Valor

TELEGRAM_TOKEN

El token de API que BotFather te proporcionó.

Instrucción de Despliegue: Configura un Trigger en Cloud Build para que, al hacer git push a la rama main, se construya automáticamente el Dockerfile y se despliegue la nueva imagen en Cloud Run.

📝 Uso del Bot en Telegram

Una vez desplegado y activo, los usuarios pueden interactuar con Kontabot de la siguiente manera:

Comandos Principales

Comando

Función

Descripción

/start

Inicio

Mensaje de bienvenida y resumen de uso.

/ayuda

Asistencia

Muestra la lista de comandos disponibles.

/cancelar

Limpiar

Cancela la operación actual y elimina los registros pendientes de la sesión.

/generar

Generar Archivo

Recopila todos los registros aprobados y envía el archivo .txt DGII (606/607) al chat.

Flujo de Trabajo

El usuario envía una foto o PDF de una factura.

Kontabot realiza el OCR y la extracción de entidades fiscales.

El bot presenta los datos extraídos y pide aprobación (simulado en el código actual).

El usuario envía más facturas, repitiendo el proceso.

Una vez finalizado, el usuario ejecuta /generar.

El bot envía el archivo .txt listo para la DGII.

💡 Próximos Pasos de Desarrollo

Implementación de la conexión con n8n / Google Sheets para almacenamiento persistente y validación.

Desarrollo del Formato DGII 607: El enfoque inicial es la extracción y generación del Formato 606 (Compras). La implementación completa del Formato 607 (Ventas) se abordará en una fase de desarrollo posterior.

Refinamiento de las Expresiones Regulares (RegEx) para una mayor precisión en la extracción de NCF, RNC y montos.

Lógica de validación fiscal (ej: formato de NCF, cálculos de ITBIS).
