Usa una imagen base de Python que incluya los paquetes de desarrollo necesarios

FROM python:3.11-slim

1. Instalar Tesseract OCR y sus dependencias de sistema

Tesseract es necesario para que pytesseract pueda funcionar.

RUN apt-get update && 

apt-get install -y tesseract-ocr libtesseract-dev && 

apt-get clean && 

rm -rf /var/lib/apt/lists/*

2. Establecer el directorio de trabajo

WORKDIR /app

3. Copiar las dependencias de Python

COPY requirements.txt .

4. Instalar las dependencias de Python

RUN pip install --no-cache-dir -r requirements.txt

5. Copiar el código de la aplicación

COPY kontabot_main.py .

6. Comando de ejecución

Este comando se ejecuta cuando se inicia el contenedor.

En Cloud Run, este contenedor debe estar expuesto a la web si usas Webhooks.

Para el Polling simple, este comando inicia el script Python.

CMD ["python", "kontabot_main.py"]
