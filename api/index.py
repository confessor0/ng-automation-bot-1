import os
import sys

# Ensure Vercel can find the 'src' and 'templates' folder from the root
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.web_server import app

# This 'app' object is what Vercel's @vercel/python builder looks for.
