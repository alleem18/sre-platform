from flask import Flask
import requests
from prometheus_flask_exporter import PrometheusMetrics

app = Flask(__name__)
metrics = PrometheusMetrics(app)

@app.route("/")
def home():

	return {"service" : "service-a", "status" : "healthy"}


@app.route("/health")
def health():
	return {"status": "ok"}, 200


@app.route("/call-b")
def call_b():
    try:
        resp = requests.get("http://service-b", timeout=2)
        return {"service-a": "ok", "service-b-response": resp.json()}
    except Exception as e:
        return {"service-a": "ok", "service-b-error": str(e)}, 502

@app.route("/healthx")
def healtx():
        return {"status": "ok"}, 200


if __name__=="__main__":
	app.run(host="0.0.0.0", port=8080)
# ci trigger test
