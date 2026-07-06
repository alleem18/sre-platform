from flask import Flask
import random

app = Flask(__name__)

@app.route("/")
def home():
    return {"service": "service-b", "data": random.randint(1, 100)}

@app.route("/health")
def health():
    return {"status": "ok"}, 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
