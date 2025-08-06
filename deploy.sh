#!/bin/bash
cd /opt/gradio-app

# Остановка приложения
systemctl stop gradio-app

# Обновление кода
git pull origin main

# Активация виртуального окружения
source venv310/bin/activate

# Обновление зависимостей
pip install -r requirements.txt

# Запуск приложения
systemctl start gradio-app

echo "Deployment completed successfully!"
