pipeline {
    agent any
    environment {
        VENV_PATH = "${WORKSPACE}/venv310"
        PYTHONPATH = "${WORKSPACE}"
    }
    stages {
        stage('Checkout') {
            steps {
                git branch: 'main', url: 'https://github.com/DanilPOW/NormaControl.git'
            }
        }
        stage('Setup Python') {
            steps {
                sh '''
                python3 -m venv venv310
                source venv310/bin/activate
                pip install --upgrade pip
                pip install -r requirements.txt
                pip install pytest requests pymupdf
                '''
            }
        }
        stage('Run Tests') {
            steps {
                sh '''
                source venv310/bin/activate
                pytest tests --junitxml=results.xml
                '''
            }
        }
        stage('Image Validation') {
            steps {
                script {
                    sh '''
                    source venv310/bin/activate
                    python -c "
import fitz
from scripts.image_checker import check_images

# Открываем тестовый PDF файл
pdf_path = 'tests/test_image_iscenter'  # путь к вашему тестовому файлу
doc = fitz.open(pdf_path)

# Запускаем проверку
result = check_images(doc, pdf_path)

# Проверяем результат
print('User Summary:', result['user_summary'])
print('Admin Details:', result['admin_details'])

# Определяем успешность теста по наличию ✅ в summary
if '✅' in result['user_summary']:
    print('✅ Image validation PASSED')
    exit(0)
else:
    print('❌ Image validation FAILED')
    exit(1)
                    "
                    '''
                }
            }
        }
        stage('Deploy') {
            when {
                expression { currentBuild.resultIsBetterOrEqualTo('SUCCESS') }
            }
            steps {
                sh '''
                source venv310/bin/activate
                chmod +x deploy.sh
                ./deploy.sh
                '''
            }
        }
    }
    post {
        always {
            junit 'results.xml'
        }
        failure {
            echo '❌ Тесты упали! Проверяй ошибки в Jenkins Console Output.'
        }
        success {
            echo '✅ Все тесты прошли успешно!'
        }
    }
}
