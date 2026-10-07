# Aptimizer Stage 1

Aptimizer Stage 1 is a full-stack application that combines a Python/FastAPI backend with a React frontend for architectural, planning, and optimization workflows.

## Project structure

- `backend/` — Python API service, planning engine, AI integration, data models, and automated tests.
- `frontend/` — React application built with Create React App and CRACO.

## Prerequisites

- Python 3.10+ for the backend
- Node.js 18+ and npm for the frontend
- Git

## Backend setup

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Copy the example environment configuration when required:

```bash
cp .env.example .env
```

Start the backend service using the application entry point provided by the project:

```bash
python start.py
```

For the test suite:

```bash
pytest
```

## Frontend setup

```bash
cd frontend
npm install
npm start
```

The development server is available at `http://localhost:3000` by default.

## Production build

```bash
cd frontend
npm run build
```

## Documentation

- Backend-specific documentation is available under `backend/validation/README.md`.
- Frontend setup and generation guidance is available under `frontend/README.md`.

## Notes

The repository excludes local environment files, virtual environments, dependency folders, build outputs, caches, logs, and editor artifacts. Existing source files remain unchanged.
