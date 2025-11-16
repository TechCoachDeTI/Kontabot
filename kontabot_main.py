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
import asyncio
import logging

# Configuración básica de logging
logging.basicConfig(level=logging.INFO)

# ==============================================================================
# 1. CONFIGURACIÓN INICIAL Y DEPENDENCIAS
# ==============================================================================

# Obtener el token de Telegram desde una variable de entorno
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "8200782434:AAEFv7jwu6NFostM6a39ImFBrwG4D0LFbEM") # Agregado como fallback
# Cloud Run proporciona el puerto como variable de entorno
PORT = int(os.environ.get("PORT", 8080)) 
# La URL base del Cloud Run service (Debe ser configurada como variable de entorno)
WEBHOOK_URL = os.getenv("WEBHOOK_URL") 

if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN must be set") 
    
# Almacenamiento temporal para registros de facturas (simulación de base de datos)
user_sessions = {}

# ==============================================================================
# 2. FUNCIONES DE LÓGICA DE NEGOCIO (Extracción)
# ==============================================================================

def extract_data_with_ocr(image_file: io.BytesIO) -> str:
    """Convierte la imagen (o la primera página del PDF) a texto usando OCR."""
    try:
        image = Image.open(image_file)
        extracted_text = pytesseract.image_to_string(image, lang='spa')
        return extracted_text
    except Exception as e:
        logging.error(f"Error en OCR: {e}")
        return f"ERROR_OCR: Fallo al procesar la imagen."

def clean_and_convert_monto(monto_str: str) -> float:
    """Limpia una cadena de monto y la convierte a float."""
    if not monto_str:
        return 0.0
    
    # Lógica robusta para limpiar el monto de caracteres no deseados
    cleaned_str = re.sub(r'[^\d.,]', '', monto_str)
    
    # Normalización: convierte el separador decimal a punto si se usa coma
    if ',' in cleaned_str and '.' in cleaned_str:
        if cleaned_str.rfind(',') < cleaned_str.rfind('.'):
             cleaned_str = cleaned_str.replace(',', '') 
    
    if cleaned_str.endswith(','):
        cleaned_str = cleaned_str.replace('.', '').replace(',', '.')
    
    if ',' in cleaned_str:
        cleaned_str = cleaned_str.replace('.', '').replace(',', '.')

    try:
        return float(cleaned_str)
    except ValueError:
        return 0.0

def extract_fiscal_entities(texto_ocr: str) -> dict:
    """Analiza el texto crudo y extrae los datos fiscales clave."""
    datos = {
        "ncf": None,
        "rnc_cedula": None,
        "fecha": None,
        "itbis_monto": 0.0,
        "total_monto": 0.0,
        "tipo_doc": "DESCONOCIDO" 
    }

    texto_limpio = texto_ocr.replace('\n', ' ').replace('\t', ' ').upper()
    ncf_pattern = r'NCF\s*[:\s]*([A-Z]{1,2}[0-9]{2,3}[-]?\s*[0-9]{8,15})'
    rnc_pattern = r'(RNC|C[ÉE]DUL[A]?)\s*[:\s]*([0-9]{9,13})'
    monto_pattern = r'([0-9]{1,3}(?:[.,][0-9]{3})*(?:[.,][0-9]{2}))'

    ncf_match = re.search(ncf_pattern, texto_limpio)
    if ncf_match:
        datos["ncf"] = re.sub(r'[\s-]', '', ncf_match.group(1))

    rnc_match = re.search(rnc_pattern, texto_limpio)
    if rnc_match:
        datos["rnc_cedula"] = rnc_match.group(2)
        
    itbis_search = re.search(r'(ITBIS|IVA)\s*[:\s]*' + monto_pattern, texto_limpio)
    if itbis_search:
        datos["itbis_monto"] = clean_and_convert_monto(itbis_search.group(1))

    total_search = re.search(r'(TOTAL|GRAN TOTAL|NETO)\s*[:\s]*' + monto_pattern, texto_limpio)
    if total_search:
        datos["total_monto"] = clean_and_convert_monto(total_search.group(1))
        
    date_match = re.search(r'(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})', texto_limpio)
    if date_match:
        datos["fecha"] = date_match.group(1)
        
    if datos["ncf"] and re.match(r'B0[25]', datos["ncf"]):
         datos["tipo_doc"] = "607_CONSUMIDOR"
    elif datos["ncf"] and re.match(r'B0[1]', datos["ncf"]):
         datos["tipo_doc"] = "606_CREDITO_FISCAL"
    else:
        datos["tipo_doc"] = "606_PRESUMIBLE"

    return datos

# ==============================================================================
# 3. MANEJADORES DE COMANDOS DE TELEGRAM
# ==============================================================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
        file_id = update.message.photo[-1].file_id
        file_name = f"photo_{file_id}.jpg"
    elif update.message.document:
        document = update.message.document
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
        file_obj = await context.bot.get_file(file_id)
        file_bytes = io.BytesIO()
        await file_obj.download_to_memory(file_bytes)
        file_bytes.seek(0)
        
        texto_ocr = extract_data_with_ocr(file_bytes)
        
        if texto_ocr.startswith("ERROR_OCR"):
            await update.message.reply_text("❌ ERROR: No se pudo leer el documento. Asegúrese de que la imagen sea clara y tenga buen contraste.")
            return
            
        datos_extraidos = extract_fiscal_entities(texto_ocr)
        
        user_id = update.effective_user.id
        if user_id not in user_sessions:
            user_sessions[user_id] = []
        
        user_sessions[user_id].append(datos_extraidos)

        respuesta = f"✅ Extracción exitosa.\n\n"
        respuesta += f"**NCF:** {datos_extraidos['ncf'] or 'No encontrado'}\n"
        respuesta += f"**RNC/Cédula:** {datos_extraidos['rnc_cedula'] or 'No encontrado'}\n"
        respuesta += f"**Fecha:** {datos_extraidos['fecha'] or 'No encontrado'}\n"
        respuesta += f"**Monto ITBIS:** RD$ {datos_extraidos['itbis_monto']:.2f}\n"
        respuesta += f"**Monto Total:** RD$ {datos_extraidos['total_monto']:.2f}\n"
        respuesta += f"**Tipo de Documento (Simulación):** {datos_extraidos['tipo_doc']}\n\n"
        respuesta += f"**Registro N° {len(user_sessions[user_id])}** guardado. Envíe otra factura o use `/generar`."

        await update.message.reply_text(respuesta, parse_mode=telegram.constants.ParseMode.MARKDOWN)

    except Exception as e:
        await update.message.reply_text(f"Ocurrió un error inesperado durante el procesamiento: {e}")
        logging.error(f"Error principal en handle_document: {e}")


async def generate_file_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Maneja el comando /generar (Simulación de la Fase 3)."""
    user_id = update.effective_user.id

    if user_id not in user_sessions or not user_sessions[user_id]:
        await update.message.reply_text("No hay registros pendientes para generar el archivo 606/607. ¡Envía tus facturas!")
        return

    registros = user_sessions[user_id]
    
    archivo_content = "RNC|NCF|FECHA|MONTO_ITBIS|MONTO_TOTAL|TIPO_DOC\n"
    for reg in registros:
        linea = f"{reg['rnc_cedula'] or '0'}|{reg['ncf'] or '0'}|{reg['fecha'] or '00/00/0000'}|{reg['itbis_monto']:.2f}|{reg['total_monto']:.2f}|{reg['tipo_doc']}\n"
        archivo_content += linea
        
    archivo_bytes = archivo_content.encode('utf-8')
    filename = f"DGII_Kontabot_Registros_{len(registros)}.txt"
    
    await update.message.reply_document(
        document=archivo_bytes, 
        filename=filename,
        caption=f"✅ Archivo **{filename}** generado con **{len(registros)}** registros. ¡Listo para subir a la DGII!",
        parse_mode=telegram.constants.ParseMode.MARKDOWN
    )
    
    del user_sessions[user_id]
    await update.message.reply_text("Sesión finalizada. Los registros han sido eliminados de la memoria.")


# ==============================================================================
# 4. FUNCIÓN PRINCIPAL DE EJECUCIÓN (ASÍNCRONA PARA CLOUD RUN)
# ==============================================================================

async def main() -> None:
    """Configura y ejecuta el bot en modo Webhook para Cloud Run."""
    logging.info("Starting Kontabot in Webhook mode...")
    
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # 2. Registrar los Manejadores (Handlers)
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("ayuda", help_command))
    application.add_handler(CommandHandler("generar", generate_file_command))
    application.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, handle_document))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, 
                                          lambda update, context: update.message.reply_text("Por favor, envíame una **foto** o **PDF** de tu factura, o usa un comando como `/ayuda`.", 
                                                                                             parse_mode=telegram.constants.ParseMode.MARKDOWN)))


    # 3. Configurar el Webhook (Solo el servidor interno)
    # CRÍTICO: Debemos llamar a set_webhook para notificar a Telegram la URL base.
    if WEBHOOK_URL:
        # Usamos el token real como parte de la URL secreta para set_webhook
        await application.bot.set_webhook(url=f"{WEBHOOK_URL}/8200782434:AAEFv7jwu6NFostM6a39ImFBrwG4D0LFbEM") 
    
    # CRÍTICO: Inicia el servidor HTTP para que Cloud Run lo detecte
    await application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path="8200782434:AAEFv7jwu6NFostM6a39ImFBrwG4D0LFbEM", # Ruta secreta (token)
        webhook_url=WEBHOOK_URL # URL base
    )

if __name__ == "__main__":
    # CRÍTICO: Se ejecuta la función main() de forma asíncrona para iniciar el servidor
    # y así evitar el timeout de Cloud Run.
    asyncio.run(main())
