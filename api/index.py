import sys
import os

# Add the parent directory to the path so we can import from src
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.main import app

# Vercel expects the ASGI app to be named 'app'
app = app
