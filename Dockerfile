# Dockerfile
FROM python:3.10-slim

# 1. Установим системные зависимости для Camelot и других пакетов
RUN apt-get update && apt-get install -y \
    build-essential \
    python3-tk \
    ghostscript \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# 2. Копируем все файлы проекта
WORKDIR /app
COPY . /app

# 3. Установим Python-зависимости
RUN pip install --upgrade pip && pip install -r requirements.txt

# Если есть requirements.txt — лучше вот так (убрать строку выше):
# RUN pip install --upgrade pip && pip install -r requirements.txt

# 4. Указываем, какой файл запускать по умолчанию
CMD ["python", "app.py"]
