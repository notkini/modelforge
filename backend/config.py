import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / '.env')

def get_settings():
    return {
        'database_url': os.getenv('DATABASE_URL', f"sqlite:///{(ROOT / 'modelforge.db').as_posix()}"),
        'google_client_id': os.getenv('GOOGLE_CLIENT_ID', ''),
        'session_secret': os.getenv('SESSION_SECRET', ''),
        'frontend_origin': os.getenv('FRONTEND_ORIGIN', 'http://localhost:5173').rstrip('/'),
        'gemini_api_key': os.getenv('GEMINI_API_KEY', ''),
        'gemini_model': os.getenv('GEMINI_MODEL', 'gemini-3.8-flash'),
        'secure_cookies': os.getenv('SECURE_COOKIES', 'false').lower() == 'true',
        'artifact_dir': Path(os.getenv('ARTIFACT_DIR', str(ROOT / 'private_artifacts'))).resolve(),
    }
