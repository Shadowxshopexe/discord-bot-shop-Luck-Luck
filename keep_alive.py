# keep_alive.py
from flask import Flask
from waitress import serve
import threading

app = Flask('keep_alive')

@app.route('/')
def home():
    return "Bot is running"

def run():
    serve(app, host='0.0.0.0', port=3000)

def start():
    thread = threading.Thread(target=run, daemon=True)
    thread.start()

if __name__ == "__main__":
    run()
