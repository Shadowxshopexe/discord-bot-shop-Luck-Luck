from flask import Flask
from waitress import serve

app = Flask(__name__)

@app.get("/")
def home():
    return "Bot is running"

def run_keep_alive():
    serve(app, host="0.0.0.0", port=3000)
