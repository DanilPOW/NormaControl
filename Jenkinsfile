pipeline {
    agent any
    environment {
        VENV_PATH = "${WORKSPACE}/venv310"
    }
    stages {
        stage('Checkout') {
            steps {
                git branch: 'main', url: 'https://github.com/yourusername/yourrepo.git'
            }
        }
        stage('Setup Python') {
            steps {
                sh '''
                python3 -m venv venv310
                source venv310/bin/activate
                pip install --upgrade pip
                pip install -r requirements.txt
                pip install pytest requests
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
