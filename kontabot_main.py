import os
import re
import io
import telegram
from telegram import Update
from telegram.ext import (
    Application, 
    CommandHandler, 
    MessageHandler, 
    filters, 
    ContextTypes
)
from PIL import Image
import pytesseract

# ==============================================================================
# 1. CONFIGURACIÓN INICIAL Y DEPENDENCIAS
# ==============================================================================

# Se recomienda configurar Tesseract como una variable de entorno si no está en PATH
# Para Google Cloud Run, esto no es necesario si Tesseract se instala correctamente en el Dockerfile.

# Obtener el token de Telegram desde una variable de entorno (ESENCIAL para Cloud Run)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    print("ERROR: La variable de entorno TELEGRAM_TOKEN no está configurada.")
    # En un entorno real, el bot se detendría aquí.
    # raise ValueError("TELEGRAM_TOKEN must be set") 

# Almacenamiento temporal para registros de facturas (simulación de base de datos)
# En una aplicación real, se usaría Firestore o una DB SQL.
user_sessions = {}

# ==============================================================================
# 2. FUNCIONES DE LÓGICA DE NEGOCIO (FASE 2: EXTRACCIÓN Y VALIDACIÓN)
# ==============================================================================

def extract_data_with_ocr(image_file: io.BytesIO) -> str:
    """Convierte la imagen (o la primera página del PDF) a texto usando OCR."""
    try:
        # Pytesseract usa el ejecutable Tesseract instalado en el contenedor Docker
        image = Image.open(image_file)
        # Usamos 'spa' para asegurar el reconocimiento en español
        extracted_text = pytesseract.image_to_string(image, lang='spa')
        return extracted_text
    except Exception as e:
        print(f"Error en OCR: {e}")
        return f"ERROR_OCR: Fallo al procesar la imagen."


def extract_fiscal_entities(texto_ocr: str) -> dict:
    """
    Función crucial: Analiza el texto crudo y extrae los datos fiscales clave.
    NOTA: Esta es una implementación PLACEHOLDER con RegEx básicas.
    La versión final requerirá RegEx más robustas o un modelo de NLP (Spacy).
    """
    datos = {
        "ncf": None,
        "rnc_cedula": None,
        "fecha": None,
        "itbis_monto": 0.0,
        "total_monto": 0.0,
        "tipo_doc": "DESCONOCIDO" # 606 (Compra) o 607 (Venta)
    }

    # Patrón RegEx para NCF (ej. B0100000000)
    # Buscar patrones de 11 a 15 caracteres alfanuméricos que empiecen con letra
    ncf_match = re.search(r'([A-Z]\d{2}[-]?\d{10,15})', texto_ocr, re.IGNORECASE)
    if ncf_match:
        datos["ncf"] = ncf_match.group(1).replace('-', '')

    # Patrón RegEx para RNC o Cédula (9 o 11 dígitos)
    rnc_match = re.search(r'RNC|Cédula:\s*(\d{9}|\d{11})', texto_ocr, re.IGNORECASE)
    if rnc_match:
        datos["rnc_cedula"] = rnc_match.group(1)

    # Patrón RegEx para ITBIS (Buscando la palabra 'ITBIS' y un monto)
    itbis_match = re.search(r'ITBIS\s*[:\s]*(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2}))', texto_ocr, re.IGNORECASE)
    if itbis_match:
        # Limpieza simple del monto (reemplaza coma por punto, remueve separadores de miles)
        monto_str = itbis_match.group(1).replace('.', '').replace(',', '.')
        try:
            datos["itbis_monto"] = float(monto_str)
        except ValueError:
            pass # Si falla, se queda en 0.0

    return datos


# ==============================================================================
# 3. MANEJADORES DE COMANDOS DE TELEGRAM
# ==============================================================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Maneja el comando /start y envía el mensaje de bienvenida."""
    welcome_message = """
¡Hola! Soy **Kontabot**, tu asistente fiscal DGII.

**Mi función:** Extraer datos de facturas (Imágenes/PDFs) para generar los archivos **606** y **607** listos para la DGII.

▶️ **Para empezar:**
1.  Envía una **foto** o **PDF** de tu factura de Compra/Venta.
2.  Aprueba los datos extraídos por la IA.
3.  Usa el comando **`/generar`** para recibir tu archivo .txt final.

🔗 Usa **`/ayuda`** para ver todos los comandos.
"""
    await update.message.reply_text(welcome_message, parse_mode=telegram.constants.ParseMode.MARKDOWN)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Maneja el comando /ayuda."""
    help_message = """
**🤖 Comandos Kontabot**
/start - Mensaje de bienvenida.
/ayuda - Muestra esta lista de comandos.
/cancelar - Limpia la sesión actual (borra registros pendientes).

**📊 Gestión de Documentos:**
Tras enviar fotos/PDFs, usa:
/revisar - Muestra las transacciones extraídas y pendientes.
/generar - Crea y envía los archivos 606 y 607 listos.
"""
    await update.message.reply_text(help_message, parse_mode=telegram.constants.ParseMode.MARKDOWN)

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Maneja la recepción de fotos y documentos (facturas)."""
    
    if update.message.photo:
        # Obtener la versión de mayor resolución
        file_id = update.message.photo[-1].file_id
        file_name = f"photo_{file_id}.jpg"
    elif update.message.document:
        document = update.message.document
        # Filtro de seguridad para archivos no deseados
        if document.mime_type not in ['image/jpeg', 'image/png', 'application/pdf']:
            await update.message.reply_text(f"Formato no soportado: {document.mime_type}. Envíe una imagen (JPG/PNG) o PDF.")
            return

        file_id = document.file_id
        file_name = document.file_name
    else:
         await update.message.reply_text("Error al recibir el archivo.")
         return

    await update.message.reply_text(f"Documento **{file_name}** recibido. Iniciando OCR y extracción...", 
                                    parse_mode=telegram.constants.ParseMode.MARKDOWN)

    try:
        # 1. Descargar el archivo a memoria
        file_obj = await context.bot.get_file(file_id)
        file_bytes = io.BytesIO()
        await file_obj.download_to_memory(file_bytes)
        file_bytes.seek(0)
        
        # 2. OCR (Imagen a Texto)
        texto_ocr = extract_data_with_ocr(file_bytes)
        
        if texto_ocr.startswith("ERROR_OCR"):
            await update.message.reply_text("❌ ERROR: No se pudo leer el documento. Asegúrese de que la imagen sea clara y tenga buen contraste.")
            return
            
        # 3. Extracción de Entidades Fiscales (Texto a JSON)
        datos_extraidos = extract_fiscal_entities(texto_ocr)
        
        # 4. Simulación de Validación y Almacenamiento
        # En esta simulación, asumimos que todo es válido.
        user_id = update.effective_user.id
        if user_id not in user_sessions:
            user_sessions[user_id] = []
        
        user_sessions[user_id].append(datos_extraidos)

        # 5. Respuesta al Usuario para Revisión
        respuesta = f"✅ Extracción exitosa.\n\n"
        respuesta += f"**NCF:** {datos_extraidos['ncf'] or 'No encontrado'}\n"
        respuesta += f"**RNC/Cédula:** {datos_extraidos['rnc_cedula'] or 'No encontrado'}\n"
        respuesta += f"**Monto ITBIS:** RD$ {datos_extraidos['itbis_monto']:.2f}\n"
        respuesta += f"**Tipo de Documento (Simulación):** {datos_extraidos['tipo_doc']}\n\n"
        respuesta += f"**Registro N° {len(user_sessions[user_id])}** guardado. Envíe otra factura o use `/generar`."

        await update.message.reply_text(respuesta, parse_mode=telegram.constants.ParseMode.MARKDOWN)

    except Exception as e:
        await update.message.reply_text(f"Ocurrió un error inesperado durante el procesamiento: {e}")
        print(f"Error principal en handle_document: {e}")


async def generate_file_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Maneja el comando /generar (Simulación de la Fase 3)."""
    user_id = update.effective_user.id

    if user_id not in user_sessions or not user_sessions[user_id]:
        await update.message.reply_text("No hay registros pendientes para generar el archivo 606/607. ¡Envía tus facturas!")
        return

    registros = user_sessions[user_id]
    
    # --- SIMULACIÓN DE GENERACIÓN DE ARCHIVO TXT ---
    # En esta sección, el código real formatearía los datos
    # según el estándar de la DGII (campos fijos y delimitados).
    
    archivo_content = "RNC|NCF|FECHA|MONTO_ITBIS|MONTO_TOTAL\n"
    for idx, reg in enumerate(registros):
        # Simulación de línea de formato 606/607
        linea = f"{reg['rnc_cedula'] or '0'}|{reg['ncf'] or '0'}|{'2024-01-01'}|{reg['itbis_monto']:.2f}|{reg['total_monto']:.2f}\n"
        archivo_content += linea
        
    archivo_bytes = archivo_content.encode('utf-8')
    filename = f"DGII_Kontabot_Registros_{len(registros)}.txt"
    
    # Enviar el archivo
    await update.message.reply_document(
        document=archivo_bytes, 
        filename=filename,
        caption=f"✅ Archivo **{filename}** generado con **{len(registros)}** registros. ¡Listo para subir a la DGII!",
        parse_mode=telegram.constants.ParseMode.MARKDOWN
    )
    
    # Limpiar sesión después de generar (opcional)
    del user_sessions[user_id]
    await update.message.reply_text("Sesión finalizada. Los registros han sido eliminados de la memoria.")


# ==============================================================================
# 4. FUNCIÓN PRINCIPAL DE EJECUCIÓN
# ==============================================================================

def main() -> None:
    """Configura y ejecuta el bot."""
    if not TELEGRAM_TOKEN:
        print("Cerrando aplicación. TELEGRAM_TOKEN no configurado.")
        return

    print("Iniciando Kontabot...")
    
    # 1. Crear la Aplicación
    # En Google Cloud Run, generalmente se usa Webhook, pero para desarrollo local y simplicidad
    # usaremos Polling. Cloud Run requiere una configuración de Webhook para entornos de producción.
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # 2. Registrar los Manejadores (Handlers)
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("ayuda", help_command))
    application.add_handler(CommandHandler("generar", generate_file_command))
    
    # Manejador para fotos y documentos
    application.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, handle_document))
    
    # Mensaje por defecto para texto no reconocido
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, 
                                          lambda update, context: update.message.reply_text("Por favor, envíame una **foto** o **PDF** de tu factura, o usa un comando como `/ayuda`.", 
                                                                                             parse_mode=telegram.constants.ParseMode.MARKDOWN)))


    # 3. Iniciar el bot (Polling)
    # NOTA: Para Cloud Run en producción, DEBES cambiar a Webhook para mejor escalabilidad.
    print("Bot listo. Ejecutando en modo Polling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
