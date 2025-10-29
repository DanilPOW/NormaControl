#!/bin/bash

set -e

PROJECT_DIR="/opt/your-service"
cd $PROJECT_DIR

if [ -d "venv" ]; then
    source venv/bin/activate
fi

echo "🧪 Тестирование функциональности PDF проверок..."
echo "================================================"

# Запускаем тесты
pytest tests/test_explicit_checks.py -v --tb=short

# Показываем результаты
echo ""
echo "📊 РЕЗУЛЬТАТЫ ПРОВЕРОК:"
echo "================================================"

python3 -c "
import json
try:
    with open('test_results/functionality_report.json', 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    for result in data['results']:
        print(result)
    
    print('================================================')
    print(f\"✅ Работает: {len([r for r in data['results'] if '✅' in r])} проверок\")
    print(f\"❌ Не работает: {len([r for r in data['results'] if '❌' in r])} проверок\")
    
except Exception as e:
    print('Ошибка загрузки результатов:', e)
"

echo ""
