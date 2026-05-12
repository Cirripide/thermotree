FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    binutils libproj-dev gdal-bin libgdal-dev \
    && rm -rf /var/lib/apt/lists/*

ENV GDAL_DISABLE_READDIR_ON_OPEN=EMPTY_DIR \
    GDAL_HTTP_VERSION=2 \
    GDAL_HTTP_MULTIPLEX=YES \
    CPL_VSIL_CURL_ALLOWED_EXTENSIONS=.tif,.tiff,.TIF,.TIFF

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
