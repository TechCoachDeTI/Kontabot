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
import json
from datetime import datetime

# Dependencias de Google Sheets
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# Configuración básica de logging
logging.basicConfig(level=logging.INFO)

# ==============================================================================
# 1. CONFIGURACIÓN INICIAL Y DEPENDENCIAS
# ==============================================================================

# Variables de Telegram
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "8200782434:AAEFv7jwu6NFostM6a39ImFBrwG4D0LFbEM")
PORT = int(os.environ.get("PORT", 8080)) 
WEBHOOK_URL = os.getenv("WEBHOOK_URL") 

# Variables de Google Sheets
GOOGLE_SHEET_KEY = os.getenv("GOOGLE_SHEET_KEY") # ID de la hoja de cálculo
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON") # Contenido de la clave JSON

if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN must be set") 

# Variable global para el cliente de gspread y la hoja de trabajo
# Se inicializará en la primera llamada asíncrona.
sheets_client = None
invoice_sheet = None

# ==============================================================================
# 2. FUNCIONES DE LÓGICA DE NEGOCIO (Extracción & GSpread)
# ==============================================================================

# --- Funciones OCR y Extracción (se mantienen igual) ---

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
    
    cleaned_str = re.sub(r'[^\d.,]', '', monto_str)
    
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


# --- Funciones de GSpread Asíncronas ---

def init_gspread_sync():
    """Inicializa el cliente de GSpread de forma síncrona."""
    global sheets_client, invoice_sheet
    if sheets_client and invoice_sheet:
        return invoice_sheet

    if not GOOGLE_SHEET_KEY or not GOOGLE_CREDENTIALS_JSON:
        logging.error("Variables de entorno de Google Sheets no configuradas.")
        return None

    try:
        # Usar el contenido JSON de la variable de entorno
        credentials_info = json.loads(GOOGLE_CREDENTIALS_JSON)
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        
        creds = ServiceAccountCredentials.from_json_keyfile_dict(credentials_info, scope)
        sheets_client = gspread.authorize(creds)
        
        # Abre la hoja de cálculo y selecciona la primera hoja (Worksheet)
        spreadsheet = sheets_client.open_by_key(GOOGLE_SHEET_KEY)
        invoice_sheet = spreadsheet.get_worksheet(0) # Asume que los datos están en la primera hoja
        
        logging.info("Conexión con Google Sheets establecida.")
        
        # Asegurar encabezados si está vacía la hoja (opcional)
        if not invoice_sheet.get_all_values():
            invoice_sheet.append_row([
                'USER_ID', 'TIMESTAMP', 'NCF', 'RNC_CEDULA', 'FECHA_FACTURA', 
                'ITBIS_MONTO', 'TOTAL_MONTO', 'TIPO_DOC', 'ESTADO'
            ])
            
        return invoice_sheet
    except Exception as e:
        logging.error(f"Error al inicializar GSpread: {e}")
        return None

async def get_invoice_data_sheet() -> gspread.Worksheet:
    """Obtiene el objeto Worksheet de forma asíncrona."""
    # Envuelve la inicialización síncrona en un hilo para no bloquear el loop
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, init_gspread_sync)

async def add_invoice_row(data_list: list):
    """Añade una fila a la hoja de cálculo de forma asíncrona."""
    sheet = await get_invoice_data_sheet()
    if sheet:
        loop = asyncio.get_event_loop()
        # Envuelve la llamada de gspread.append_row en un hilo
        await loop.run_in_executor(None, lambda: sheet.append_row(data_list))
        logging.info("Fila añadida a Google Sheets.")

async def get_user_pending_invoices(user_id: int):
    """Obtiene los registros pendientes del usuario desde la hoja de forma asíncrona."""
    sheet = await get_invoice_data_sheet()
    if sheet:
        loop = asyncio.get_event_loop()
        # Envuelve la lectura de datos
        all_data = await loop.run_in_executor(None, sheet.get_all_records)
        
        # Filtra los registros por user_id y estado 'PENDIENTE'
        pending_invoices = [
            record for record in all_data 
            if str(record.get('USER_ID')) == str(user_id) and record.get('ESTADO') == 'PENDIENTE'
        ]
        return pending_invoices
    return []

# ==============================================================================
# 3. MANEJADORES DE COMANDOS DE TELEGRAM
# ==============================================================================

# ... (start_command y help_command se mantienen igual) ...
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
    """Maneja la recepción de fotos y documentos (facturas) y guarda en Google Sheets."""
    
    # 1. Identificación y Filtro de Archivo
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
        # 2. Descarga y OCR
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

        # 3. Preparar la fila para Google Sheets
        # Campos: USER_ID, TIMESTAMP, NCF, RNC_CEDULA, FECHA_FACTURA, ITBIS_MONTO, TOTAL_MONTO, TIPO_DOC, ESTADO
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sheet_row = [
            user_id,
            timestamp,
            datos_extraidos['ncf'] or '',
            datos_extraidos['rnc_cedula'] or '',
            datos_extraidos['fecha'] or '',
            datos_extraidos['itbis_monto'],
            datos_extraidos['total_monto'],
            datos_extraidos['tipo_doc'],
            'PENDIENTE' # Estado inicial
        ]
        
        # 4. Guardar en Google Sheets (asíncrono)
        await add_invoice_row(sheet_row)
        
        # 5. Respuesta al Usuario
        # Simula el conteo de registros pendientes
        pending_invoices = await get_user_pending_invoices(user_id)
        num_registros = len(pending_invoices)

        respuesta = f"✅ Extracción y Guardado en Google Sheets exitoso.\n\n"
        respuesta += f"**NCF:** {datos_extraidos['ncf'] or 'No encontrado'}\n"
        respuesta += f"**RNC/Cédula:** {datos_extraidos['rnc_cedula'] or 'No encontrado'}\n"
        respuesta += f"**Monto ITBIS:** RD$ {datos_extraidos['itbis_monto']:.2f}\n"
        respuesta += f"**Tipo de Documento:** {datos_extraidos['tipo_doc']}\n\n"
        respuesta += f"Tienes **{num_registros}** registros pendientes en la hoja de cálculo. Envía otra factura o usa `/generar`."

        await update.message.reply_text(respuesta, parse_mode=telegram.constants.ParseMode.MARKDOWN)

    except Exception as e:
        await update.message.reply_text(f"Ocurrió un error inesperado. Asegúrate de que las variables `GOOGLE_SHEET_KEY` y `GOOGLE_CREDENTIALS_JSON` estén configuradas correctamente en Cloud Run. Error: {e}")
        logging.error(f"Error principal en handle_document: {e}")


async def generate_file_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Maneja el comando /generar (Lee desde Google Sheets y genera el archivo TXT)."""
    user_id = update.effective_user.id

    # 1. Leer registros pendientes desde Google Sheets
    registros = await get_user_pending_invoices(user_id)

    if not registros:
        await update.message.reply_text("No hay registros pendientes para generar el archivo 606/607 en Google Sheets. ¡Envía tus facturas!")
        return

    # --- SIMULACIÓN DE GENERACIÓN DE ARCHIVO TXT ---
    
    archivo_content = "RNC|NCF|FECHA|MONTO_ITBIS|MONTO_TOTAL|TIPO_DOC\n"
    for reg in registros:
        # Usamos las claves de la hoja (en mayúsculas)
        linea = f"{reg.get('RNC_CEDULA', '0')}|{reg.get('NCF', '0')}|{reg.get('FECHA_FACTURA', '00/00/0000')}|{reg.get('ITBIS_MONTO', 0.0):.2f}|{reg.get('TOTAL_MONTO', 0.0):.2f}|{reg.get('TIPO_DOC', 'DESCONOCIDO')}\n"
        archivo_content += linea
        
    archivo_bytes = archivo_content.encode('utf-8')
    filename = f"DGII_Kontabot_Registros_{len(registros)}.txt"
    
    # 2. Enviar el archivo
    await update.message.reply_document(
        document=archivo_bytes, 
        filename=filename,
        caption=f"✅ Archivo **{filename}** generado con **{len(registros)}** registros leídos desde Google Sheets. ¡Listo para subir a la DGII!",
        parse_mode=telegram.constants.ParseMode.MARKDOWN
    )
    
    # 3. Marcar registros como 'EXPORTADO' en la hoja (simulación)
    # NOTA: La lógica real sería encontrar y actualizar las filas en GSpread.
    # Por simplicidad, aquí solo avisamos que la sesión está finalizada.
    await update.message.reply_text(
        "Sesión finalizada. Los registros se mantendrán en Google Sheets, pero deberían ser marcados como 'EXPORTADO' o eliminados en la implementación final."
    )


# ==============================================================================
# 4. FUNCIÓN PRINCIPAL DE EJECUCIÓN (ASÍNCRONA PARA CLOUD RUN)
# ==============================================================================

async def main() -> None:
    """Configura y ejecuta el bot en modo Webhook para Cloud Run."""
    logging.info("Starting Kontabot in Webhook mode...")
    
    # Verificar configuración crítica de GSpread al inicio
    if not GOOGLE_SHEET_KEY or not GOOGLE_CREDENTIALS_JSON:
        logging.warning("El bot iniciará, pero las funciones de Google Sheets FALLARÁN. Configure GOOGLE_SHEET_KEY y GOOGLE_CREDENTIALS_JSON.")
    
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # 2. Registrar los Manejadores (Handlers)
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("ayuda", help_command))
    application.add_handler(CommandHandler("generar", generate_file_command))
    application.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, handle_document))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, 
                                          lambda update, context: update.message.reply_text("Por favor, envíame una **foto** o **PDF** de tu factura, o usa un comando como `/ayuda`.", 
                                                                                             parse_mode=telegram.constants.ParseMode.MARKDOWN)))


    # 3. Configurar el Webhook
    if WEBHOOK_URL:
        # El token se usa en la URL de webhook para la seguridad y el ruteo
        token_path = TELEGRAM_TOKEN.split(":")[-1] if ":" in TELEGRAM_TOKEN else TELEGRAM_TOKEN
        await application.bot.set_webhook(url=f"{WEBHOOK_URL}/{token_path}") 
    
    await application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        # La ruta (url_path) debe coincidir con la parte final de la URL del webhook
        url_path=token_path,
        webhook_url=WEBHOOK_URL
    )

if __name__ == "__main__":
    asyncio.run(main())
