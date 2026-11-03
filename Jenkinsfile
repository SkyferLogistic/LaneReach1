// LaneReach CI: lint, type-check, test (testcontainers needs the Docker socket), audit, image.
pipeline {
  agent any
  options {
    timestamps()
    timeout(time: 30, unit: 'MINUTES')
    disableConcurrentBuilds()
  }
  environment {
    UV_LINK_MODE = 'copy'
    IMAGE = "lanereach:${env.BUILD_NUMBER}"
  }
  stages {
    stage('Setup') {
      steps {
        sh 'command -v uv || curl -LsSf https://astral.sh/uv/install.sh | sh'
        sh 'uv sync --frozen'
      }
    }
    stage('Lint') {
      steps {
        sh 'uv run ruff check .'
        sh 'uv run ruff format --check .'
      }
    }
    stage('Type-check') {
      steps { sh 'uv run mypy' }
    }
    stage('Test') {
      steps {
        sh 'uv run pytest --junitxml=reports/junit.xml --cov --cov-report=xml:reports/coverage.xml'
      }
      post {
        always { junit allowEmptyResults: true, testResults: 'reports/junit.xml' }
      }
    }
    stage('Frontend') {
      steps {
        dir('web') {
          sh 'npm ci --no-audit --no-fund'
          sh 'npm run typecheck'
          sh 'npm test'
          sh 'npm run build'
        }
      }
    }
    stage('AI evals') {
      // Only when an LLM key is configured in Jenkins (credential id: lanereach-anthropic-key).
      when { expression { return env.ANTHROPIC_API_KEY?.trim() } }
      environment {
        LLM_PROVIDER = 'anthropic'
        LLM_MODEL = 'claude-opus-5'
      }
      steps { sh 'uv run python evals/run_evals.py' }
      post { always { archiveArtifacts allowEmptyArchive: true, artifacts: 'evals/reports/**' } }
    }
    stage('Dependency audit') {
      steps { sh 'make audit' }
    }
    stage('Build image') {
      steps {
        sh 'docker build -f docker/Dockerfile -t "$IMAGE" .'
        sh 'docker build -t "${IMAGE}-web" web'
      }
    }
  }
  post {
    always { archiveArtifacts allowEmptyArchive: true, artifacts: 'reports/**' }
  }
}
