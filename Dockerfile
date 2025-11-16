# Usa una imagen base de Python optimizada para ser pequeña y eficiente
FROM python:3.11-slim

# ==============================================================================
# 1. INSTALACIÓN DE DEPENDENCIAS DE SISTEMA (TESSERACT OCR)
# ==============================================================================

# Actualiza la lista de paquetes e instala Tesseract OCR, la librería de desarrollo,
# y el paquete de idioma español (tesseract-ocr-spa) para la DGII.
RUN apt-get update --fix-missing && \
    apt-get install -y \
        tesseract-ocr \
        tesseract-ocr-spa \
        libtesseract-dev \
        && \
    # Limpia los cachés para reducir el tamaño final de la imagen del contenedor
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# ==============================================================================
# 2. CONFIGURACIÓN DEL ENTORNO DE PYTHON
# ==============================================================================

# Establece /app como el directorio de trabajo donde residirá el código del bot
WORKDIR /app

# Copia el archivo de requisitos de Python
COPY requirements.txt .

# Instala las librerías de Python listadas en requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copia el código principal de la aplicación
COPY kontabot_main.py .

# ==============================================================================
# 3. COMANDO DE EJECUCIÓN
# ==============================================================================

# Define el comando que se ejecuta cuando se inicia el contenedor
# Esto inicia el bot en modo Polling (para desarrollo)
# NOTA: Para producción con Webhooks, la configuración en Cloud Run podría variar.
CMD ["python", "kontabot_main.py"]
